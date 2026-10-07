"""RecorderWriter against a schema-53 fixture database.

Facts this pins, all observed in the live recorder on 2026-10-07:
  * a state row has last_changed_ts NULL when it equals last_updated_ts;
  * numbers are stored in the entity's display unit (distance in inches, battery in V);
  * the ESPHome device disconnecting writes `unavailable` rows that the backfill removes.

Run: python rate_of_rise/tests/test_backfill_recorder.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backfill.entity_map import Point  # noqa: E402
from app.backfill.recorder import (  # noqa: E402
    MARKER, RecorderWriter, SchemaUnsupported, schema_version)

SCHEMA = Path(__file__).parent / "fixtures" / "recorder_schema_53.sql"
T0 = 1_791_300_000.0


def make_db(version=53):
    db = Path(tempfile.mkdtemp()) / "home-assistant_v2.db"
    c = sqlite3.connect(db)
    c.executescript(SCHEMA.read_text(encoding="utf-8"))
    c.execute("INSERT INTO schema_changes (schema_version, changed) VALUES (?, '2026-01-01')",
              (version,))
    c.executemany("INSERT INTO states_meta (metadata_id, entity_id) VALUES (?, ?)", [
        (1, "sensor.stage"), (2, "sensor.distance"), (3, "binary_sensor.node"),
        (4, "sensor.weight")])
    c.executemany("INSERT INTO state_attributes (attributes_id, shared_attrs) VALUES (?, ?)", [
        (10, '{"unit_of_measurement":"ft","state_class":"measurement"}'),
        (20, '{"unit_of_measurement":"in"}'), (30, '{}'), (40, '{"unit_of_measurement":"kg"}')])
    c.commit()
    return db, c


def add(c, mid, state, ts, attrs, old=None):
    cur = c.execute(
        "INSERT INTO states (state, last_updated_ts, old_state_id, attributes_id, origin_idx,"
        " metadata_id) VALUES (?, ?, ?, ?, 0, ?)", (state, ts, old, attrs, mid))
    c.commit()
    return cur.lastrowid


def rows(c, mid):
    return c.execute("SELECT state, last_updated_ts, last_changed_ts, attributes_id,"
                     " substr(context_id_bin, 1, 4), old_state_id FROM states"
                     " WHERE metadata_id = ? ORDER BY last_updated_ts", (mid,)).fetchall()


def test_schema_guard():
    db, _ = make_db(54)
    assert schema_version(db) == 54
    try:
        RecorderWriter(db)
    except SchemaUnsupported as exc:
        assert exc.version == 54
        return
    raise AssertionError("schema 54 was accepted")


def test_inserts_missing_readings_with_marker_and_attributes():
    db, c = make_db()
    add(c, 1, "0.9", T0, 10)
    res = RecorderWriter(db).write("sensor.stage", "number", "ft",
                                   [Point(T0 + 60, 0.95), Point(T0 + 120, 0.97)])
    assert res.inserted == [T0 + 60, T0 + 120] and res.skipped is None
    got = rows(c, 1)
    assert [r[0] for r in got] == ["0.9", "0.95", "0.97"]
    assert got[1][2] is None and got[1][3] == 10 and got[1][4] == MARKER and got[1][5] is None


def test_existing_row_within_tolerance_is_not_duplicated():
    db, c = make_db()
    add(c, 1, "0.95", T0 + 61.7, 10)        # HA's live write, 1.7 s after the gateway's stamp
    res = RecorderWriter(db).write("sensor.stage", "number", "ft", [Point(T0 + 60, 0.95)])
    assert res.inserted == []
    assert len(rows(c, 1)) == 1


def test_unchanged_value_is_compressed_like_ha():
    db, c = make_db()
    add(c, 1, "0.95", T0, 10)
    res = RecorderWriter(db).write("sensor.stage", "number", "ft", [
        Point(T0 + 60, 0.95), Point(T0 + 120, 0.95), Point(T0 + 180, 0.96),
        Point(T0 + 240, 0.96)])
    assert res.inserted == [T0 + 180]


def test_converts_to_the_entity_display_unit():
    db, c = make_db()
    add(c, 2, "32.0", T0, 20)                 # distance displayed in inches
    RecorderWriter(db).write("sensor.distance", "number", "mm", [Point(T0 + 60, 812)])
    assert rows(c, 2)[-1][0] == "31.968504"


def test_unknown_unit_skips_entity_without_raising():
    db, c = make_db()
    add(c, 4, "1.0", T0, 40)                  # kg: cannot hold a distance
    res = RecorderWriter(db).write("sensor.weight", "number", "mm", [Point(T0 + 60, 812)])
    assert res.skipped and "kg" in res.skipped and res.inserted == []
    assert len(rows(c, 4)) == 1


def test_null_number_is_unknown_and_binary_is_on_off():
    db, c = make_db()
    add(c, 1, "0.9", T0, 10)
    add(c, 3, "off", T0, 30)
    w = RecorderWriter(db)
    w.write("sensor.stage", "number", "ft", [Point(T0 + 60, None)])
    w.write("binary_sensor.node", "binary", None, [Point(T0 + 60, True)])
    assert rows(c, 1)[-1][0] == "unknown"
    assert rows(c, 3)[-1][0] == "on"


def test_unavailable_rows_in_the_gap_are_removed_and_references_cleared():
    db, c = make_db()
    a = add(c, 1, "0.9", T0, 10)
    u = add(c, 1, "unavailable", T0 + 30, 10, old=a)
    b = add(c, 1, "0.99", T0 + 600, 10, old=u)   # live again; points at the unavailable row
    res = RecorderWriter(db).write("sensor.stage", "number", "ft",
                                   [Point(T0 + 90, 0.92), Point(T0 + 150, 0.94)])
    assert res.deleted_unavailable == 1
    states = [r[0] for r in rows(c, 1)]
    assert "unavailable" not in states and states == ["0.9", "0.92", "0.94", "0.99"]
    assert c.execute("SELECT old_state_id FROM states WHERE state_id = ?", (b,)).fetchone()[0] is None


def test_unavailable_far_from_inserted_rows_is_kept():
    db, c = make_db()
    add(c, 1, "unavailable", T0, 10)
    add(c, 1, "0.9", T0 + 5000, 10)
    RecorderWriter(db).write("sensor.stage", "number", "ft", [Point(T0 + 6000, 0.95)])
    assert "unavailable" in [r[0] for r in rows(c, 1)]


def test_second_write_is_a_no_op():
    db, c = make_db()
    add(c, 1, "0.9", T0, 10)
    pts = [Point(T0 + 60, 0.95), Point(T0 + 120, 0.97)]
    RecorderWriter(db).write("sensor.stage", "number", "ft", pts)
    again = RecorderWriter(db).write("sensor.stage", "number", "ft", pts)
    assert again.inserted == [] and len(rows(c, 1)) == 3


def test_dry_run_counts_but_writes_nothing():
    db, c = make_db()
    add(c, 1, "0.9", T0, 10)
    add(c, 1, "unavailable", T0 + 30, 10)
    res = RecorderWriter(db).write("sensor.stage", "number", "ft",
                                   [Point(T0 + 90, 0.92)], dry_run=True)
    assert res.inserted == [T0 + 90] and res.deleted_unavailable == 1
    assert len(rows(c, 1)) == 2


def test_entity_not_in_recorder_is_skipped():
    db, _ = make_db()
    res = RecorderWriter(db).write("sensor.nope", "number", "ft", [Point(T0, 1.0)])
    assert res.skipped == "entity not in recorder" and res.inserted == []


def test_large_batch_is_chunked():
    db, c = make_db()
    add(c, 1, "0.0", T0, 10)
    pts = [Point(T0 + 60 * (i + 1), round(0.001 * (i + 1), 3)) for i in range(1200)]
    res = RecorderWriter(db).write("sensor.stage", "number", "ft", pts)
    assert len(res.inserted) == 1200 and len(rows(c, 1)) == 1201


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
