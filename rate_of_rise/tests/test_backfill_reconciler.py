"""Reconciler passes and the backfill service: cursor rules, the v1/unreachable/off paths.

Run: python rate_of_rise/tests/test_backfill_reconciler.py
"""
import json
import logging
import sqlite3
import sys
import tempfile
import threading
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from app.backfill import build_backfill  # noqa: E402
from app.backfill import reconciler as rmod  # noqa: E402
from app.backfill.client import Probe, ProbeState  # noqa: E402
from app.backfill.reconciler import (  # noqa: E402
    NO_STORE_RETRY_S, PASS_INTERVAL_S, BackfillService, Destinations, Reconciler)
from app.backfill.gaprows import GapFillDeferred  # noqa: E402
from app.backfill.recorder import SchemaUnsupported, WriteResult  # noqa: E402

NOW = 1_791_400_000.0


class FakeClient:
    def __init__(self, node=(), eco=(), state=ProbeState.OK):
        self.recs = {"node": list(node), "ecowitt": list(eco)}
        self.state = state
        self.fail = False
        self.store_id = None
        self.sd_ok = None
        self.fetched = []

    def status(self):
        st = {"store_schema": 1, "streams": {
            s: {"first": 1 if r else 0, "last": r[-1]["seq"] if r else 0}
            for s, r in self.recs.items()}}
        if self.store_id is not None:
            st["store_id"] = self.store_id
        if self.sd_ok is not None:
            st["sd_ok"] = self.sd_ok
        return st

    def probe(self):
        return Probe(self.state, self.status() if self.state is ProbeState.OK else {})

    def records(self, stream, after, max_records):
        self.fetched.append((stream, after))
        if self.fail:
            raise requests.ConnectionError("gone")
        return [r for r in self.recs[stream] if r["seq"] > after][:max_records]


class FakeWriter:
    calls = []
    resolutions = {}
    raise_on_write = None

    def __init__(self, db):
        pass

    def write(self, entity_id, kind, unit, points, dry_run=False, resolution=None):
        if FakeWriter.raise_on_write:
            raise FakeWriter.raise_on_write
        FakeWriter.calls.append((entity_id, kind, unit, [p.ts for p in points], dry_run))
        FakeWriter.resolutions[entity_id] = resolution
        return WriteResult(entity_id, inserted=[p.ts for p in points])


class BlockedWriter:
    def __init__(self, db):
        raise SchemaUnsupported(54)


def node(seq, ts, ts_src="ntp", stage=1.0):
    return {"seq": seq, "ts": ts, "ts_src": ts_src, "stage_ft": stage, "v": 4100}


def make(client, writer=FakeWriter, gaps=None, entity_map=None, shadow=None, stats=None):
    d = Path(tempfile.mkdtemp())
    FakeWriter.calls, FakeWriter.raise_on_write, FakeWriter.resolutions = [], None, {}
    dest = Destinations(recorder_db=d / "ha.db", statistics=stats, stage_dir=d / "stage",
                        gaps=gaps)
    rec = Reconciler(client, dest,
                     entity_map if entity_map is not None else
                     {"node": {"stage_ft": "sensor.v2_stage"}},
                     shadow if shadow is not None else {"node": {"battery_mv": "sensor.v1_batt"}},
                     d / "state" / "backfill.json", now_fn=lambda: NOW, writer_factory=writer)
    return rec, d


def test_writes_map_and_shadow_then_advances_cursor():
    client = FakeClient(node=[node(1, NOW - 600), node(2, NOW - 540)])
    rec, d = make(client)
    res = rec.run_pass(client.status())
    assert res.ok and rec.cursor["node"] == 2
    written = [c for c in FakeWriter.calls if not c[4]]
    shadowed = [c for c in FakeWriter.calls if c[4]]
    assert written == [("sensor.v2_stage", "number", "ft", [NOW - 600, NOW - 540], False)]
    assert shadowed[0][0] == "sensor.v1_batt" and shadowed[0][2] == "mV"
    assert res.counts["inserted_states"] == 2 and res.counts["shadow_states"] == 2
    assert res.counts["stage_log_rows"] == 2
    assert json.loads((d / "state" / "backfill.json").read_text())["node"] == 2


def test_holds_back_live_edge():
    client = FakeClient(node=[node(1, NOW - 600), node(2, NOW - 60), node(3, NOW - 30)])
    rec, _ = make(client)
    rec.run_pass(client.status())
    assert rec.cursor["node"] == 1


def test_records_without_time_are_skipped_but_consumed():
    client = FakeClient(node=[node(1, 12.0, ts_src="none"), node(2, NOW - 600)])
    rec, _ = make(client)
    res = rec.run_pass(client.status())
    assert res.counts["skipped_no_time"] == 1 and rec.cursor["node"] == 2
    assert FakeWriter.calls[0][3] == [NOW - 600]


def capture(fn):
    h = []
    lg = logging.getLogger("app.backfill")
    handler = logging.Handler()
    handler.emit = h.append
    lg.addHandler(handler)
    try:
        return fn(), [r for r in h if r.levelno == logging.WARNING]
    finally:
        lg.removeHandler(handler)


def reload(rec, client):
    return Reconciler(client, rec._dest, rec._map, rec._shadow, rec._path, now_fn=lambda: NOW,
                      writer_factory=FakeWriter)


def test_card_change_resets_cursor_and_warns():
    # Replaces the old "store restart resets cursor": a lower seq alone no longer resets; a
    # different store_id (a replaced or reformatted card) does.
    client = FakeClient(node=[node(1, NOW - 600)])
    client.store_id = "bbbbbbbbbbbbbbbb"
    rec, d = make(client)
    rec._cursor["node"], rec._cursor["ecowitt"] = 5000, 70   # from the old card
    rec._store_id = "aaaaaaaaaaaaaaaa"
    _, warns = capture(lambda: rec.run_pass(client.status()))
    assert rec.cursor == {"node": 1, "ecowitt": 0}, rec.cursor
    assert ("node", 0) in client.fetched
    assert len(warns) == 1 and "card changed" in warns[0].getMessage()
    saved = json.loads((d / "state" / "backfill.json").read_text())
    assert saved["store_id"] == "bbbbbbbbbbbbbbbb" and saved["node"] == 1
    assert reload(rec, client)._store_id == "bbbbbbbbbbbbbbbb"


def test_same_card_with_seq_below_cursor_leaves_the_cursor_alone():
    client = FakeClient(node=[node(1, NOW - 600)])
    client.store_id = "aaaaaaaaaaaaaaaa"
    rec, _ = make(client)
    rec._cursor["node"] = 5000
    rec._store_id = "aaaaaaaaaaaaaaaa"
    res1, warns1 = capture(lambda: rec.run_pass(client.status()))
    res2, warns2 = capture(lambda: rec.run_pass(client.status()))
    assert res1.ok and res2.ok
    assert rec.cursor["node"] == 5000 and not [f for f in client.fetched if f[0] == "node"]
    assert len(warns1) == 1 and "below cursor" in warns1[0].getMessage() and not warns2


def test_no_store_id_with_seq_below_cursor_leaves_the_cursor_alone():
    client = FakeClient(node=[node(1, NOW - 600)])        # an older v2 build: no store_id
    rec, _ = make(client)
    rec._cursor["node"] = 5000
    rec.run_pass(client.status())
    assert rec.cursor["node"] == 5000 and not client.fetched


def test_first_run_adopts_the_store_id_without_resetting():
    client = FakeClient(node=[node(1, NOW - 900), node(2, NOW - 600)])
    client.store_id = "cccccccccccccccc"
    rec, d = make(client)
    rec._cursor["node"] = 1                               # saved by a build without store_id
    _, warns = capture(lambda: rec.run_pass(client.status()))
    assert not warns and rec.cursor["node"] == 2 and client.fetched == [("node", 1)]
    assert json.loads((d / "state" / "backfill.json").read_text())["store_id"] == "cccccccccccccccc"


def test_old_cursor_file_without_store_id_loads():
    client = FakeClient()
    rec, d = make(client)
    (d / "state").mkdir(parents=True, exist_ok=True)
    (d / "state" / "backfill.json").write_text('{"node": 12, "ecowitt": 3}')
    again = reload(rec, client)
    assert again.cursor == {"node": 12, "ecowitt": 3} and again._store_id is None


def test_sd_not_mounted_does_nothing():
    client = FakeClient(node=[node(1, NOW - 600)])
    client.sd_ok = False
    rec, _ = make(client)
    res, warns = capture(lambda: rec.run_pass(client.status()))
    assert res.skipped_sd and not client.fetched and rec.cursor["node"] == 0 and not warns
    assert FakeWriter.calls == []


def test_failed_destination_keeps_cursor():
    client = FakeClient(node=[node(1, NOW - 600)])
    rec, _ = make(client)
    FakeWriter.raise_on_write = sqlite3.OperationalError("database is locked")
    res = rec.run_pass(client.status())
    assert not res.ok and rec.cursor.get("node", 0) == 0
    FakeWriter.raise_on_write = None
    assert rec.run_pass(client.status()).ok and rec.cursor["node"] == 1


def test_schema_block_still_runs_other_destinations():
    client = FakeClient(node=[node(1, NOW - 600)])
    rec, d = make(client, writer=BlockedWriter)
    res = rec.run_pass(client.status())
    assert res.ok and res.blocked == "blocked: recorder schema 54"
    assert res.counts["stage_log_rows"] == 1 and rec.cursor["node"] == 1


def test_gap_filler_gets_both_streams():
    seen = []
    gaps = SimpleNamespace(fill=lambda n, e: seen.append((len(n), len(e))) or 3)
    client = FakeClient(node=[node(1, NOW - 600)],
                        eco=[{"seq": 1, "ts": NOW - 590, "ts_src": "ntp", "rain_year_in": 1.0}])
    rec, _ = make(client, gaps=gaps)
    res = rec.run_pass(client.status())
    assert seen == [(1, 1)] and res.counts["dataset_rows"] == 3 and rec.cursor["ecowitt"] == 1


def test_more_when_a_pass_hits_the_cap():
    old = rmod.MAX_RECORDS_PER_PASS
    rmod.MAX_RECORDS_PER_PASS = 2
    try:
        client = FakeClient(node=[node(i, NOW - 1000 + i) for i in range(1, 6)])
        rec, _ = make(client)
        assert rec.run_pass(client.status()).more and rec.cursor["node"] == 2
    finally:
        rmod.MAX_RECORDS_PER_PASS = old


def test_statistics_failure_does_not_block_the_pass():
    class Boom:
        def backfill(self, entity, ts):
            raise RuntimeError("websocket down")
    client = FakeClient(node=[node(1, NOW - 600)])
    rec, _ = make(client, stats=Boom())
    res = rec.run_pass(client.status())
    assert res.ok and rec.cursor["node"] == 1 and res.counts["stat_errors"] == 1


# --- the service ------------------------------------------------------------------------
class Quiet(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.seen = []

    def emit(self, record):
        self.seen.append(record)


def service(state):
    client = FakeClient(node=[node(1, NOW - 600)], state=state)
    rec, _ = make(client)
    published = []
    svc = BackfillService(client, rec, lambda name, payload: published.append((name, payload)),
                          now_fn=lambda: NOW)
    return svc, client, published


def run_quietly(fn):
    h = Quiet()
    lg = logging.getLogger("app")
    old = lg.level
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)      # or INFO records would be filtered before the handler
    try:
        return fn(), h.seen
    finally:
        lg.removeHandler(h)
        lg.setLevel(old)


def test_service_v1_gateway_is_silent_and_waits_an_hour():
    svc, _, published = service(ProbeState.NO_STORE)
    delay, seen = run_quietly(svc.tick)
    assert delay == NO_STORE_RETRY_S and not seen
    assert published[-1] == ("status/backfill", published[-1][1])
    assert published[-1][1]["state"] == "v1 gateway (no store)"
    assert published[-1][1]["inserted_states"] == 0
    assert FakeWriter.calls == []


def test_service_unreachable_is_silent():
    svc, _, published = service(ProbeState.UNREACHABLE)
    delay, seen = run_quietly(svc.tick)
    assert delay == PASS_INTERVAL_S and not seen and published[-1][1]["state"] == "unreachable"


def test_service_bad_token_warns_once_per_hour():
    svc, _, published = service(ProbeState.BAD_TOKEN)
    _, seen1 = run_quietly(svc.tick)
    _, seen2 = run_quietly(svc.tick)
    assert len([r for r in seen1 if r.levelno == logging.WARNING]) == 1 and not seen2
    assert published[-1][1]["state"] == "error: bad token"


def test_service_ok_runs_a_pass_and_reports_idle():
    svc, _, published = service(ProbeState.OK)
    svc.tick()
    final = published[-1][1]
    assert final["state"] == "idle" and final["cursor_node"] == 1
    assert final["inserted_states"] == 1


def test_service_reports_gateway_sd_not_mounted():
    svc, client, published = service(ProbeState.OK)
    client.sd_ok = False
    _, seen = run_quietly(svc.tick)
    assert published[-1][1]["state"] == "gateway SD not mounted"
    assert published[-1][1]["cursor_node"] == 0 and not client.fetched
    assert not [r for r in seen if r.levelno >= logging.WARNING]


def test_service_network_drop_mid_pass_is_unreachable():
    svc, client, published = service(ProbeState.OK)
    client.fail = True
    _, seen = run_quietly(svc.tick)
    assert published[-1][1]["state"] == "unreachable"
    assert not [r for r in seen if r.levelno >= logging.WARNING]


# --- activation ---------------------------------------------------------------------------
def cfg(**kw):
    base = dict(gateway_store_url="", gateway_store_token="", backfill_entity_map="",
                backfill_shadow_map="", ha_ws_url="ws://x", supervisor_token="",
                soil_moisture_entities=[], fast_loop_minutes=5,
                rate_of_rise_window_minutes=10.0, rate_of_rise_max_gap_minutes=10.0)
    base.update(kw)
    return SimpleNamespace(**base)


def test_blank_url_is_off_no_thread_no_requests():
    published = []
    before = threading.active_count()
    created = []
    import app.backfill as pkg
    real = pkg.StoreClient
    pkg.StoreClient = lambda *a, **k: created.append(1)
    try:
        svc = build_backfill(cfg(), lambda n, p: published.append((n, p)), None, None,
                             Path(tempfile.mkdtemp()), None)
    finally:
        pkg.StoreClient = real
    assert svc is None and created == [] and threading.active_count() == before
    assert published == [("status/backfill", {"state": "off"})]


def test_bad_entity_map_disables_with_a_reason():
    published = []
    svc = build_backfill(cfg(gateway_store_url="http://x", backfill_entity_map="{nope"),
                         lambda n, p: published.append((n, p)), None, None,
                         Path(tempfile.mkdtemp()), None)
    assert svc is None and published[-1][1]["state"].startswith("error: entity map")


def test_records_with_bad_time_are_skipped_but_consumed():
    client = FakeClient(node=[node(1, NOW - 600), node(2, NOW + 86400 * 365 * 70),
                              node(3, None), node(4, NOW - 500)])
    rec, _ = make(client)
    res = rec.run_pass(client.status())
    assert res.ok and rec.cursor["node"] == 4 and res.counts["skipped_bad_time"] == 2
    assert FakeWriter.calls[0][3] == [NOW - 600, NOW - 500]


def test_deferred_gap_fill_keeps_cursor_and_is_quiet():
    def deferred(n, e):
        raise GapFillDeferred("rain not polled yet")
    gaps = SimpleNamespace(fill=deferred)
    client = FakeClient(node=[node(1, NOW - 600)])
    rec, _ = make(client, gaps=gaps)
    before = rec.cursor
    res, seen = run_quietly(lambda: rec.run_pass(client.status()))
    assert res.deferred and not res.ok and rec.cursor == before
    assert not [r for r in seen if r.levelno >= logging.WARNING]


def test_service_reports_waiting_for_live_poll():
    def deferred(n, e):
        raise GapFillDeferred("rain not polled yet")
    client = FakeClient(node=[node(1, NOW - 600)])
    rec, _ = make(client, gaps=SimpleNamespace(fill=deferred))
    published = []
    svc = BackfillService(client, rec, lambda name, payload: published.append((name, payload)),
                          now_fn=lambda: NOW)
    svc.tick()
    assert published[-1][1]["state"] == "waiting for live poll"


def test_writer_gets_the_field_resolution():
    client = FakeClient(node=[node(1, NOW - 600), node(2, NOW - 540)])
    rec, _ = make(client)
    rec.run_pass(client.status())
    assert FakeWriter.resolutions == {"sensor.v2_stage": 0.0001, "sensor.v1_batt": 1.0},         FakeWriter.resolutions


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
