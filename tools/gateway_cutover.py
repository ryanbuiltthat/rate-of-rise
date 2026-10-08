"""Swap gateway v2 in for v1 in Home Assistant, keeping v1's entity IDs and history.

Order: unplug v1; install creek-gateway-v2.prod.yaml (the node-OTA buttons) on v2; dry run
this script; run it with --apply; restart the add-on. The production build goes on v2 first
because v1's two OTA buttons need v2 counterparts to pair with: a v2 button renamed to v1's id
keeps v1's id and history. Against the trial build (no buttons) preflight stops on them.

Run from a laptop with an admin long-lived access token in a file:

    python tools/gateway_cutover.py --ha-url http://192.168.20.3:8123 --token-file ~/.ha_token
    python tools/gateway_cutover.py ... --apply

A real run needs: pip install requests websocket-client

Without --apply it prints what it would do and stops. All reading and computing happens in
preflight (a malformed add-on map stops the run there). The dry run prints the config entries
it would delete, the renames, and the resulting backfill_entity_map. With --apply:
  1. preflight: v1's entities are all unavailable (v1 unplugged), v2's backfill is idle,
     and every v1 entity has a v2 entity with the same domain and name;
  2. removes v1's ESPHome config entry (and with it v1's entities, freeing their IDs),
     then waits up to ~10 s for those IDs to leave get_states and the entity registry;
  3. renames each v2 entity to its v1 entity_id (3 tries each). HA keeps history by
     entity_id string, so each renamed entity continues v1's history (see the plan's
     Task 20 Step 0);
  4. writes the add-on options: backfill_entity_map with the production IDs, and
     backfill_shadow_map cleared.
If any step fails, the script prints what completed and the exact remaining steps (including
the options JSON) and exits non-zero. There is no resume mode.
See docs/gateway-v2-trial.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def pair_entities(v1: list[dict], v2: list[dict]):
    def key(e):
        return e["entity_id"].split(".", 1)[0], (e.get("original_name") or "").strip().lower()
    v2_by_key = {key(e): e["entity_id"] for e in v2}
    pairs, only_v1 = [], []
    for e in v1:
        match = v2_by_key.pop(key(e), None)
        if match:
            pairs.append((e["entity_id"], match))
        else:
            only_v1.append(e["entity_id"])
    return pairs, only_v1, sorted(v2_by_key.values())


def production_map(entity_map: dict, pairs) -> dict:
    v1_for = {v2: v1 for v1, v2 in pairs}
    return {stream: {f: v1_for.get(e, e) for f, e in fields.items()}
            for stream, fields in entity_map.items()}


def _json_map(options: dict, key: str) -> dict:
    try:
        value = json.loads(options.get(key) or "{}")
    except ValueError as e:
        raise ValueError(f"add-on option {key} is not valid JSON: {e}") from e
    if not isinstance(value, dict):
        raise ValueError(f"add-on option {key} must be a JSON object")
    return value


def new_addon_options(options: dict, pairs) -> dict:
    """Full options dict for the add-on after cutover. Never drops an ecowitt entry."""
    entity_map = _json_map(options, "backfill_entity_map")
    shadow = _json_map(options, "backfill_shadow_map")
    new_map = production_map(entity_map, pairs)
    ecowitt = {**entity_map.get("ecowitt", {}), **shadow.get("ecowitt", {})}
    if ecowitt:
        new_map["ecowitt"] = ecowitt
    return {**options, "backfill_entity_map": json.dumps(new_map), "backfill_shadow_map": ""}


class HA:
    def __init__(self, url: str, token: str):
        import websocket  # imported here so the pure helpers test without the deps
        self.url, self.token, self._id = url.rstrip("/"), token, 0
        ws_url = self.url.replace("http", "ws", 1) + "/api/websocket"
        self.ws = websocket.create_connection(ws_url, timeout=30)
        json.loads(self.ws.recv())
        self.ws.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(self.ws.recv()).get("type") != "auth_ok":
            raise SystemExit("HA rejected the token")

    def call(self, msg: dict):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, **msg}))
        while True:
            reply = json.loads(self.ws.recv())
            if reply.get("id") == self._id:
                break
        if not reply.get("success"):
            raise SystemExit(f"{msg['type']} failed: {reply.get('error')}")
        return reply.get("result")

    def delete_entry(self, entry_id: str) -> None:
        import requests
        r = requests.delete(f"{self.url}/api/config/config_entries/entry/{entry_id}",
                            headers={"Authorization": f"Bearer {self.token}"}, timeout=30)
        r.raise_for_status()


def device_entities(devices, entities, name):
    found = [d for d in devices if name in (d.get("name_by_user"), d.get("name"))]
    if not found:
        raise SystemExit(f"no device named {name!r}")
    if len(found) > 1:
        raise SystemExit(f"{len(found)} devices are named {name!r}; rename one or pass a unique name")
    dev = found[0]
    return dev, [e for e in entities if e.get("device_id") == dev["id"]]


def wait_ids_gone(ha, ids, sleep=time.sleep, tries=10):
    """Poll once a second until none of ids is in get_states or the entity registry."""
    ids = set(ids)
    for attempt in range(tries):
        states = {s["entity_id"] for s in ha.call({"type": "get_states"})}
        reg = {e["entity_id"] for e in ha.call({"type": "config/entity_registry/list"})}
        still = ids & (states | reg)
        if not still:
            return
        if attempt < tries - 1:
            sleep(1)
    raise RuntimeError("v1 entity IDs still present after waiting: " + ", ".join(sorted(still)))


def apply_plan(ha, plan, sleep=time.sleep, out=print) -> int:
    """Perform the irreversible calls from a precomputed plan. On any failure, say exactly
    what is done and what remains, and return 1."""
    entries_left = list(plan["entry_ids"])
    renames_left = list(plan["pairs"])  # (v1_id, v2_id)
    options_pending = True
    done = []
    try:
        while entries_left:
            ha.delete_entry(entries_left[0])
            done.append("removed v1 config entry " + entries_left.pop(0))
            out(done[-1])
        wait_ids_gone(ha, plan["v1_ids"], sleep)
        while renames_left:
            v1_id, v2_id = renames_left[0]
            for attempt in range(3):
                try:
                    ha.call({"type": "config/entity_registry/update", "entity_id": v2_id,
                             "new_entity_id": v1_id})
                    break
                except (Exception, SystemExit):
                    if attempt == 2:
                        raise
                    sleep(2)
            done.append(f"renamed {v2_id} -> {v1_id}")
            renames_left.pop(0)
            out(done[-1])
        ha.call({"type": "supervisor/api", "endpoint": f"/addons/{plan['slug']}/options",
                 "method": "post", "data": {"options": plan["options"]}})
        options_pending = False
        out("add-on options updated; restart the add-on")
        return 0
    except (Exception, SystemExit) as e:
        out(f"\nAPPLY FAILED: {e!r}")
        out("Completed: " + ("; ".join(done) if done else "nothing"))
        out("Remaining steps (do these by hand):")
        for entry_id in entries_left:
            out(f"  delete v1 config entry {entry_id}")
        for v1_id, v2_id in renames_left:
            out(f"  rename {v2_id} -> {v1_id}")
        if options_pending:
            out(f"  POST /addons/{plan['slug']}/options with options:")
            out(json.dumps(plan["options"], indent=2))
        return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ha-url", required=True)
    ap.add_argument("--token-file", required=True, type=Path)
    ap.add_argument("--v1-device", default="Creek Gateway")
    ap.add_argument("--v2-device", default="Creek Gateway v2")
    ap.add_argument("--addon-slug", default=None, help="default: the add-on named Rate of Rise")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    ha = HA(args.ha_url, args.token_file.expanduser().read_text().strip())
    devices = ha.call({"type": "config/device_registry/list"})
    entities = ha.call({"type": "config/entity_registry/list"})
    v1_dev, v1 = device_entities(devices, entities, args.v1_device)
    v2_dev, v2 = device_entities(devices, entities, args.v2_device)
    if v1_dev["id"] == v2_dev["id"]:
        raise SystemExit("--v1-device and --v2-device resolve to the same device")
    pairs, only_v1, only_v2 = pair_entities(v1, v2)

    print(f"{len(pairs)} entity pair(s):")
    for a, b in pairs:
        print(f"  {b}  ->  {a}")
    if only_v2:
        print("v2-only (kept as they are):", ", ".join(only_v2))
    problems = []
    if only_v1:
        problems.append("v1 entities with no v2 counterpart: " + ", ".join(only_v1))
    states = {s["entity_id"]: s["state"] for s in ha.call({"type": "get_states"})}
    live_v1 = [e["entity_id"] for e in v1 if states.get(e["entity_id"]) not in (None, "unavailable")]
    if live_v1:
        problems.append("v1 is still online (unplug it first): " + ", ".join(live_v1[:5]))
    backfill = states.get("sensor.rate_of_rise_creek_backfill_status")
    if backfill not in ("idle",):
        problems.append(f"backfill status is {backfill!r}, not 'idle': let it catch up first")

    addons = ha.call({"type": "supervisor/api", "endpoint": "/addons", "method": "get"})["addons"]
    slug = args.addon_slug or next((a["slug"] for a in addons if a["name"] == "Rate of Rise"), None)
    new_options = None
    if slug is None:
        problems.append("could not find the Rate of Rise add-on; pass --addon-slug")
    else:
        info = ha.call({"type": "supervisor/api", "endpoint": f"/addons/{slug}/info", "method": "get"})
        try:
            new_options = new_addon_options(dict(info["options"]), pairs)
        except ValueError as e:
            problems.append(str(e))
    for p in problems:
        print("PREFLIGHT:", p)
    if problems:
        return 1

    entry_ids = list(v1_dev.get("config_entries", []))
    print("would delete v1 config entries:", ", ".join(entry_ids) or "(none)")
    print("would rename:")
    for v1_id, v2_id in pairs:
        print(f"  {v2_id} -> {v1_id}")
    print("resulting backfill_entity_map:")
    print(json.dumps(json.loads(new_options["backfill_entity_map"]), indent=2))
    if not args.apply:
        print("Preflight passed. Re-run with --apply to make these changes.")
        return 0

    plan = {"entry_ids": entry_ids, "pairs": pairs, "v1_ids": [e["entity_id"] for e in v1],
            "slug": slug, "options": new_options}
    return apply_plan(ha, plan)


if __name__ == "__main__":
    sys.exit(main())
