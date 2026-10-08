"""End to end: one backfill pass through every real destination, then the same records again.

A fake store serves 40 minutes of node and Ecowitt records. Home Assistant's recorder (a
schema-53 fixture database) has the live rows either side of a 25-minute outage, with the
`unavailable` rows HA wrote when the gateway dropped. The real Reconciler writes through the
real RecorderWriter, the real stage-log merge and a GapFiller over a fake dataset that has no
rows in the outage. Statistics are off.

The first pass fills the gap everywhere. Running the records through again, first with the
same cursor and then from seq 0 (as after a card change), writes nothing anywhere: no
recorder rows, no stage-log rows, no dataset rows.

Run: python rate_of_rise/tests/test_backfill_e2e.py
"""
import sqlite3
import struct
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backfill.gaprows import GapFiller  # noqa: E402
from app.backfill.reconciler import Destinations, Reconciler  # noqa: E402
from app.backfill.recorder import MARKER  # noqa: E402

SCHEMA = Path(__file__).parent / "fixtures" / "recorder_schema_53.sql"
T = 1_791_300_000.0                      # window start; records every 60 s for 40 min
MINUTES = 40
GAP = (6, 30)                            # HA saw nothing from minute 6 to minute 30
NOW = T + MINUTES * 60 + 600             # every record is past the live-edge holdback
CFG = SimpleNamespace(fast_loop_minutes=5, rate_of_rise_window_minutes=10.0,
                      rate_of_rise_max_gap_minutes=10.0, soil_moisture_entities=[])
MAP = {"node": {"stage_ft": "sensor.creek_stage", "depth_in": "sensor.creek_depth",
                "node_status": "binary_sensor.creek_node_status"},
       "ecowitt": {"rain_total_in": "sensor.rain_total", "temp_f": "sensor.outdoor_temp"}}


def f32(v):
    """The float HA stores: ESPHome publishes float32, HA writes its repr."""
    return repr(struct.unpack("f", struct.pack("f", v))[0])


def distance_mm(i):
    # Flat for the first half of the outage, then rising 2 mm a minute.
    return 812.0 if i <= 18 else 812.0 - 2.0 * (i - 18)


def node_records():
    out = []
    for i in range(1, MINUTES + 1):
        depth_mm = 1105.0 - distance_mm(i)
        out.append({"seq": i, "ts": T + 60 * i, "ts_src": "ntp", "rssi": -72,
                    "d": distance_mm(i), "v": 4012, "mount": 1105,
                    "stage_ft": round(depth_mm / 304.8, 4), "depth_in": round(depth_mm / 25.4, 4)})
    return out


def eco_records():
    out = []
    for i in range(1, MINUTES + 1):
        out.append({"seq": i, "ts": T + 60 * i, "ts_src": "ntp",
                    "rain_year_in": round(10.0 + 0.01 * max(0, i - 10), 3),
                    "rain_rate_in_hr": 0.6 if i > 10 else 0.0, "temp_f": 50.2,
                    "soil": {"2": 70}})
    return out


def in_gap(i):
    return GAP[0] <= i < GAP[1]


def make_db():
    db = Path(tempfile.mkdtemp()) / "home-assistant_v2.db"
    c = sqlite3.connect(db)
    c.executescript(SCHEMA.read_text(encoding="utf-8"))
    c.execute("INSERT INTO schema_changes (schema_version, changed) VALUES (53, '2026-01-01')")
    entities = ["sensor.creek_stage", "sensor.creek_depth", "binary_sensor.creek_node_status",
                "sensor.rain_total", "sensor.outdoor_temp"]
    c.executemany("INSERT INTO states_meta (metadata_id, entity_id) VALUES (?, ?)",
                  list(enumerate(entities, 1)))
    c.executemany("INSERT INTO state_attributes (attributes_id, shared_attrs) VALUES (?, ?)", [
        (1, '{"unit_of_measurement":"ft","state_class":"measurement"}'),
        (2, '{"unit_of_measurement":"in","state_class":"measurement"}'),
        (3, '{"device_class":"connectivity"}'),
        (4, '{"unit_of_measurement":"in","state_class":"total_increasing"}'),
        (5, '{"unit_of_measurement":"\\u00b0F","state_class":"measurement"}')])

    def add(mid, state, ts):
        c.execute("INSERT INTO states (state, last_updated_ts, old_state_id, attributes_id,"
                  " origin_idx, metadata_id) VALUES (?, ?, NULL, ?, 0, ?)", (state, ts, mid, mid))

    # What HA recorded live: a row only when the state changed, 1.7 s after the gateway's
    # stamp; at the disconnect an `unavailable` row, then nothing until the gateway is back,
    # when the first state is written again even if unchanged.
    last = {}
    for node, eco in zip(node_records(), eco_records()):
        i = node["seq"]
        if i == GAP[0]:
            for mid in range(1, 6):
                add(mid, "unavailable", node["ts"] + 30)
                last[mid] = "unavailable"
        if in_gap(i):
            continue
        depth_mm = 1105.0 - node["d"]
        for mid, state in ((1, f32(depth_mm / 304.8)), (2, f32(depth_mm / 25.4)), (3, "on"),
                           (4, repr(eco["rain_year_in"])), (5, repr(eco["temp_f"]))):
            if last.get(mid) != state:
                add(mid, state, node["ts"] + 1.7)
                last[mid] = state
    c.commit()
    return db, c


class FakeStore:
    def __init__(self):
        self.recs = {"node": node_records(), "ecowitt": eco_records()}

    def status(self):
        return {"store_schema": 1, "sd_ok": True, "store_id": "0123456789abcdef",
                "streams": {s: {"first": 1, "last": r[-1]["seq"]} for s, r in self.recs.items()}}

    def records(self, stream, after, max_records):
        return [r for r in self.recs[stream] if r["seq"] > after][:max_records]


class FakeDataset:
    """Live fast-loop rows every 5 min, none in the outage; append keeps the last per ts."""

    def __init__(self):
        self.rows = {}
        for k in range(0, MINUTES // 5 + 1):
            ts = T + 300 * k
            if T + 60 * GAP[0] <= ts < T + 60 * GAP[1]:
                continue
            self.rows[ts] = {"ts": ts, "stage_ft": 0.9613, "creek_node_online": True,
                             "api_index_in": 1.0, "rate_of_rise_in_min": 0.0,
                             "rate_of_rise_sample_count": 10.0, "alert_tier": 0}
        self.appended = 0

    def frame(self):
        return pd.DataFrame(sorted(self.rows.values(), key=lambda r: r["ts"]))

    def append_record(self, record):
        self.rows[record["ts"]] = dict(record)
        self.appended += 1


def marked(c, mid=None):
    q = "SELECT COUNT(*) FROM states WHERE substr(context_id_bin, 1, 4) = ?"
    args = [MARKER]
    if mid is not None:
        q += " AND metadata_id = ?"
        args.append(mid)
    return c.execute(q, args).fetchone()[0]


def snapshot(c, stage_dir, ds):
    rows = c.execute("SELECT metadata_id, state, last_updated_ts FROM states"
                     " ORDER BY metadata_id, last_updated_ts").fetchall()
    stage = sorted((p.name, p.read_text(encoding="utf-8")) for p in stage_dir.glob("*.csv"))
    return rows, stage, {ts: dict(r) for ts, r in ds.rows.items()}


def reconciler(store, db, stage_dir, gaps, cursor_path):
    dest = Destinations(recorder_db=db, statistics=None, stage_dir=stage_dir, gaps=gaps)
    return Reconciler(store, dest, MAP, {}, cursor_path, now_fn=lambda: NOW)


def test_one_pass_fills_the_gap_and_the_records_again_write_nothing():
    db, c = make_db()
    work = Path(tempfile.mkdtemp())
    stage_dir, ds, store = work / "stage", FakeDataset(), FakeStore()
    gaps = GapFiller(CFG, ds, soil_channels={"near_creek": "2"}, now_fn=lambda: NOW)
    rec = reconciler(store, db, stage_dir, gaps, work / "state" / "backfill.json")

    first = rec.run_pass(store.status())
    assert first.ok and not first.more and first.blocked is None, first
    assert rec.cursor == {"node": MINUTES, "ecowitt": MINUTES}

    # The gap is filled: stage rows inside it, its `unavailable` rows gone (a later live row
    # exists for every entity), the stage log has every reading, the dataset every slot.
    gap_lo, gap_hi = T + 60 * GAP[0], T + 60 * GAP[1]
    stage_in_gap = c.execute("SELECT state FROM states WHERE metadata_id = 1 AND"
                             " last_updated_ts >= ? AND last_updated_ts < ?",
                             (gap_lo, gap_hi)).fetchall()
    assert stage_in_gap and all(s[0] != "unavailable" for s in stage_in_gap), stage_in_gap
    assert c.execute("SELECT COUNT(*) FROM states WHERE state = 'unavailable'").fetchone()[0] == 0
    assert first.counts["deleted_unavailable"] == 5
    assert first.counts["inserted_states"] == marked(c) > 0
    # The flat first half of the outage repeats the pre-outage depth: compressed like HA, so
    # one row after the unavailable and then one per change.
    depth_rows = marked(c, 2)
    assert depth_rows == 1 + (GAP[1] - 1 - 18), depth_rows
    assert first.counts["stage_log_rows"] == MINUTES
    gap_slots = [ts for ts in (T + 300 * k for k in range(MINUTES // 5 + 1))
                 if gap_lo <= ts < gap_hi]
    assert first.counts["dataset_rows"] == len(gap_slots) == ds.appended
    assert all(ds.rows[ts].get("backfilled") is True and ds.rows[ts]["stage_ft"] is not None
               for ts in gap_slots)

    before = snapshot(c, stage_dir, ds)

    # Same reconciler, same cursor: nothing new to read.
    again = rec.run_pass(store.status())
    assert again.ok
    assert again.counts["inserted_states"] == 0 and again.counts["stage_log_rows"] == 0
    assert again.counts["dataset_rows"] == 0

    # Every record again from seq 0 (a fresh cursor file, as after a card change): every
    # destination finds it already there.
    reread = reconciler(store, db, stage_dir, gaps, work / "state2" / "backfill.json")
    second = reread.run_pass(store.status())
    assert second.ok and reread.cursor == {"node": MINUTES, "ecowitt": MINUTES}
    assert second.counts["inserted_states"] == 0, second.counts
    assert second.counts["deleted_unavailable"] == 0
    assert second.counts["stage_log_rows"] == 0
    assert second.counts["dataset_rows"] == 0
    after = snapshot(c, stage_dir, ds)
    assert after[0] == before[0] and after[1] == before[1] and after[2] == before[2]


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
