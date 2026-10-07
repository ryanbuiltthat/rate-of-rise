"""Hourly long-term statistics for hours the backfill filled.

Run: python rate_of_rise/tests/test_backfill_statistics.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_backfill_recorder import add, make_db  # noqa: E402

from app.backfill.statistics import StatisticsWriter, hourly_stats  # noqa: E402

H = 1_791_298_800.0     # 2026-10-06 15:00 UTC, an hour boundary


def test_time_weighted_mean_min_max():
    # 1.0 for the first 15 min (carried in from before the hour), 2.0 for 45 min.
    s = hourly_stats([(H + 900, 2.0)], H, prior=1.0)
    assert abs(s["mean"] - 1.75) < 1e-9 and s["min"] == 1.0 and s["max"] == 2.0
    assert hourly_stats([], H, prior=None) is None
    assert hourly_stats([], H, prior=3.0) == {"mean": 3.0, "min": 3.0, "max": 3.0}


def setup_meta(c, mean_type=1, has_sum=0):
    c.execute("INSERT INTO statistics_meta (statistic_id, source, unit_of_measurement, has_mean,"
              " has_sum, name, mean_type, unit_class) VALUES"
              " ('sensor.stage', 'recorder', 'ft', NULL, ?, NULL, ?, 'distance')",
              (has_sum, mean_type))
    c.commit()


def test_imports_completed_hours_with_metadata_from_the_db():
    db, c = make_db()
    setup_meta(c)
    add(c, 1, "1.0", H - 60, 10)
    add(c, 1, "2.0", H + 900, 10)
    add(c, 1, "unknown", H + 1800, 10)       # non-numeric: ends the 2.0 span, adds nothing
    sent = []
    w = StatisticsWriter(db, sent.append, now_fn=lambda: H + 3 * 3600)
    assert w.backfill("sensor.stage", [H + 900]) == 1
    msg = sent[0]
    assert msg["type"] == "recorder/import_statistics"
    meta = msg["metadata"]
    assert meta == {"source": "recorder", "statistic_id": "sensor.stage",
                    "unit_of_measurement": "ft", "name": None, "has_sum": False,
                    "mean_type": 1, "unit_class": "distance"}
    stat = msg["stats"][0]
    assert stat["start"] == "2026-10-06T15:00:00+00:00"
    assert stat["min"] == 1.0 and stat["max"] == 2.0
    assert abs(stat["mean"] - (1.0 * 900 + 2.0 * 900) / 1800) < 1e-9


def test_current_and_recent_hours_are_left_to_ha():
    db, c = make_db()
    setup_meta(c)
    add(c, 1, "1.0", H + 60, 10)
    sent = []
    assert StatisticsWriter(db, sent.append, now_fn=lambda: H + 3600 + 300).backfill(
        "sensor.stage", [H + 60]) == 0
    assert sent == []


def test_sum_and_meanless_statistics_are_skipped():
    for mean_type, has_sum in ((0, 1), (0, 0)):
        db, c = make_db()
        setup_meta(c, mean_type=mean_type, has_sum=has_sum)
        add(c, 1, "1.0", H + 60, 10)
        sent = []
        assert StatisticsWriter(db, sent.append, now_fn=lambda: H + 5 * 3600).backfill(
            "sensor.stage", [H + 60]) == 0
        assert sent == []


def test_no_statistics_metadata_is_skipped():
    db, c = make_db()
    add(c, 1, "1.0", H + 60, 10)
    sent = []
    assert StatisticsWriter(db, sent.append, now_fn=lambda: H + 5 * 3600).backfill(
        "sensor.stage", [H + 60]) == 0


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
