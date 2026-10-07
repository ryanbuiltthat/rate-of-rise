"""Swap gateway v2 in for v1 in Home Assistant, keeping v1's entity IDs and history.

Run from a laptop with an admin long-lived access token in a file:

    python tools/gateway_cutover.py --ha-url http://192.168.20.3:8123 --token-file ~/.ha_token
    python tools/gateway_cutover.py ... --apply

Without --apply it prints what it would do and stops. With --apply:
  1. preflight: v1's entities are all unavailable (v1 unplugged), v2's backfill is idle,
     and every v1 entity has a v2 entity with the same domain and name;
  2. removes v1's ESPHome config entry (and with it v1's entities, freeing their IDs);
  3. renames each v2 entity to its v1 entity_id. HA keeps history by entity_id string, so
     each renamed entity continues v1's history (see the plan's Task 20 Step 0);
  4. rewrites the add-on's backfill_entity_map to the production IDs and clears
     backfill_shadow_map.
Flashing the production wrapper (OTA buttons back) is the last, manual, step.
See docs/gateway-v2-trial.md.
"""
from __future__ import annotations

import argparse
import json
import sys
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
    dev = next((d for d in devices if name in (d.get("name_by_user"), d.get("name"))), None)
    if dev is None:
        raise SystemExit(f"no device named {name!r}")
    return dev, [e for e in entities if e.get("device_id") == dev["id"]]


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
    _, v2 = device_entities(devices, entities, args.v2_device)
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
    if slug is None:
        problems.append("could not find the Rate of Rise add-on; pass --addon-slug")
    for p in problems:
        print("PREFLIGHT:", p)
    if problems:
        return 1
    if not args.apply:
        print("Preflight passed. Re-run with --apply to make these changes.")
        return 0

    for entry_id in v1_dev.get("config_entries", []):
        ha.delete_entry(entry_id)
        print("removed v1 config entry", entry_id)
    for v1_id, v2_id in pairs:
        ha.call({"type": "config/entity_registry/update", "entity_id": v2_id,
                 "new_entity_id": v1_id})
        print("renamed", v2_id, "->", v1_id)
    info = ha.call({"type": "supervisor/api", "endpoint": f"/addons/{slug}/info", "method": "get"})
    options = dict(info["options"])
    options["backfill_entity_map"] = json.dumps(
        production_map(json.loads(options.get("backfill_entity_map") or "{}"), pairs)
        | {"ecowitt": json.loads(options.get("backfill_shadow_map") or "{}").get("ecowitt", {})})
    options["backfill_shadow_map"] = ""
    ha.call({"type": "supervisor/api", "endpoint": f"/addons/{slug}/options", "method": "post",
             "data": {"options": options}})
    print("add-on options updated; restart the add-on, then install creek-gateway-v2.prod.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
