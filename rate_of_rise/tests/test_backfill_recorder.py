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
    add(c, 1, "0.95", T0 + 600, 10)  # later live row so unavailable is not newest (Rule 3)
    res = RecorderWriter(db).write("sensor.stage", "number", "ft",
                                   [Point(T0 + 90, 0.92)], dry_run=True)
    assert res.inserted == [T0 + 90] and res.deleted_unavailable == 1
    assert len(rows(c, 1)) == 3  # no actual deletion in dry_run


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


def test_flat_gap_is_filled_and_its_unavailable_removed():
    db, c = make_db()
    add(c, 3, "on", T0, 30)
    add(c, 3, "unavailable", T0 + 30, 30)
    add(c, 3, "on", T0 + 1800, 30)
    pts = [Point(T0 + 60 + 60*i, True) for i in range(29)]  # 29 readings at T0+60, +120, ... +1740
    res = RecorderWriter(db).write("binary_sensor.node", "binary", None, pts)
    assert res.inserted == [T0 + 60]  # only first one inserted (flat, all True = "on")
    assert res.deleted_unavailable == 1
    states = [r[0] for r in rows(c, 3)]
    assert states == ["on", "on", "on"] and "unavailable" not in states


def test_unavailable_between_two_separate_fills_is_kept():
    db, c = make_db()
    add(c, 1, "0.9", T0, 10)
    add(c, 1, "0.95", T0 + 3000, 10)
    add(c, 1, "unavailable", T0 + 5000, 10)
    add(c, 1, "0.99", T0 + 9500, 10)
    res = RecorderWriter(db).write("sensor.stage", "number", "ft",
                                   [Point(T0 + 60, 0.91), Point(T0 + 9000, 0.98)])
    assert "unavailable" in [r[0] for r in rows(c, 1)]


def test_newest_unavailable_row_is_never_deleted():
    db, c = make_db()
    add(c, 1, "0.9", T0, 10)
    add(c, 1, "unavailable", T0 + 30, 10)
    res = RecorderWriter(db).write("sensor.stage", "number", "ft", [Point(T0 + 90, 0.92)])
    states = [r[0] for r in rows(c, 1)]
    assert "unavailable" in states and res.inserted == [T0 + 90]


def test_ha_float_text_counts_as_unchanged():
    db, c = make_db()
    # Store the formatted value that HA would have (high precision)
    add(c, 2, "31.968503937007874", T0, 20)  # distance in inches
    # Write 812 mm twice; should format to ~31.968504 in (same within tolerance)
    res = RecorderWriter(db).write("sensor.distance", "number", "mm",
                                   [Point(T0 + 60, 812), Point(T0 + 120, 812)])
    assert res.inserted == []  # numeric compression: both are unchanged


def test_non_finite_and_none_are_unknown():
    db, c = make_db()
    add(c, 1, "0.9", T0, 10)
    add(c, 3, "off", T0, 30)
    w = RecorderWriter(db)
    w.write("sensor.stage", "number", "ft", [Point(T0 + 60, float("nan"))])
    w.write("binary_sensor.node", "binary", None, [Point(T0 + 60, None)])
    assert rows(c, 1)[-1][0] == "unknown"
    assert rows(c, 3)[-1][0] == "unknown"


def test_rollback_after_failed_insert_preserves_original_error():
    db, c = make_db()
    add(c, 1, "0.9", T0, 10)

    # Monkeypatch the module's connect function to return a proxy that fails on executemany
    import app.backfill.recorder as rec
    original_connect = rec.connect

    class FailingConnection:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def executemany(self, sql, params):
            # Execute ROLLBACK first (simulating SQLite ending transaction)
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            # Then raise the disk error
            raise sqlite3.OperationalError("disk I/O error")

    def failing_connect(db_path):
        return FailingConnection(original_connect(db_path))

    rec.connect = failing_connect
    try:
        w = RecorderWriter(db)
        try:
            w.write("sensor.stage", "number", "ft", [Point(T0 + 60, 0.95)])
            raise AssertionError("should have raised")
        except sqlite3.OperationalError as exc:
            assert "disk I/O error" in str(exc)
    finally:
        rec.connect = original_connect


def test_second_run_never_deletes_the_newest_unavailable_row():
    db, c = make_db()
    add(c, 1, "0.9", T0, 10)
    add(c, 1, "unavailable", T0 + 30, 10)
    # First run: insert 0.92
    RecorderWriter(db).write("sensor.stage", "number", "ft", [Point(T0 + 90, 0.92)])
    states_after_first = [r[0] for r in rows(c, 1)]
    assert "unavailable" in states_after_first  # unavailable still there after first run
    # Second run: same points again (idempotent)
    RecorderWriter(db).write("sensor.stage", "number", "ft", [Point(T0 + 90, 0.92)])
    states_after_second = [r[0] for r in rows(c, 1)]
    assert "unavailable" in states_after_second  # unavailable must still be there (not deleted by MARKER exclusion logic)


def test_ha_row_after_an_unavailable_still_matches():
    db, c = make_db()
    add(c, 1, "0.9", T0, 10)
    add(c, 1, "unavailable", T0 + 30, 10)
    add(c, 1, "0.95", T0 + 1801.7, 10)  # HA's live write, 1.7 s after our gateway stamp
    # Readings: 0.9 every 60s from T0+60 to T0+1740, then 0.95 at T0+1800
    pts = [Point(T0 + 60 * (i + 1), 0.9) for i in range(29)]  # T0+60 to T0+1740
    pts.append(Point(T0 + 1800, 0.95))  # Should match HA's live row at T0+1801.7 (within ±20s)
    res = RecorderWriter(db).write("sensor.stage", "number", "ft", pts)
    # Only first reading (0.9 @ T0+60) should be inserted; 0.95 @ T0+1800 matches HA's live row
    # Unavailable row is deleted (bracketed, with newer HA row)
    assert res.inserted == [T0 + 60]
    assert len(rows(c, 1)) == 3  # original 3 - 1 unavailable + 1 inserted = 3


def test_ha_raw_float_vs_two_dp_record_is_unchanged():
    # HA stores the device's float verbatim; the old 2-dp record rounds it. With the record's
    # quantum (0.01 in) as the tolerance, a flat stretch adds nothing.
    db, c = make_db()
    add(c, 2, "11.496063232421875", T0, 20)          # depth, displayed in inches
    pts = [Point(T0 + 60 * (i + 1), 11.50) for i in range(5)]
    res = RecorderWriter(db).write("sensor.distance", "number", "in", pts, resolution=0.01)
    assert res.inserted == [], res.inserted
    assert len(rows(c, 2)) == 1


def test_ha_raw_float_vs_four_dp_record_is_unchanged():
    db, c = make_db()
    add(c, 2, "11.496063232421875", T0, 20)
    pts = [Point(T0 + 60 * (i + 1), 11.4961) for i in range(5)]
    res = RecorderWriter(db).write("sensor.distance", "number", "in", pts, resolution=0.0001)
    assert res.inserted == [], res.inserted


def test_resolution_is_converted_into_the_display_unit():
    # Record in mm (quantum 1 mm), entity displayed in inches: 1 mm = 0.03937 in, so a
    # 0.5 mm-equivalent difference (0.0197 in) is the same reading.
    db, c = make_db()
    add(c, 2, "31.98", T0, 20)
    res = RecorderWriter(db).write("sensor.distance", "number", "mm",
                                   [Point(T0 + 60, 812)], resolution=1.0)
    assert res.inserted == [], res.inserted


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
