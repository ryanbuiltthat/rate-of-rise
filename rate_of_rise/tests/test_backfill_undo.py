"""undo(): every backfilled recorder row out again, nothing else touched.

Run: python rate_of_rise/tests/test_backfill_undo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_backfill_recorder import T0, add, make_db, rows  # noqa: E402

from app.backfill.entity_map import Point  # noqa: E402
from app.backfill.recorder import RecorderWriter, SchemaUnsupported  # noqa: E402
from app.backfill.undo import undo  # noqa: E402


def test_removes_only_marked_rows_since_the_given_time():
    db, c = make_db()
    add(c, 1, "0.9", T0, 10)
    live = add(c, 1, "1.5", T0 + 900, 10)
    RecorderWriter(db).write("sensor.stage", "number", "ft",
                             [Point(T0 + 60, 0.95), Point(T0 + 600, 1.2)])
    c.execute("UPDATE states SET old_state_id = (SELECT state_id FROM states WHERE state = '1.2')"
              " WHERE state_id = ?", (live,))
    c.commit()
    assert undo(db, since_ts=T0 + 300) == 1
    assert [r[0] for r in rows(c, 1)] == ["0.9", "0.95", "1.5"]
    assert c.execute("SELECT old_state_id FROM states WHERE state_id = ?",
                     (live,)).fetchone()[0] is None
    assert undo(db) == 1
    assert [r[0] for r in rows(c, 1)] == ["0.9", "1.5"]


def test_unsupported_schema_is_refused():
    db, _ = make_db(54)
    try:
        undo(db)
    except SchemaUnsupported as exc:
        assert exc.version == 54
        return
    raise AssertionError("schema 54 was accepted")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
