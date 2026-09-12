"""Tests for the two-tier dataset writer (spec §4/§7 Phase 3).
Run: python creek_modeling/tests/test_dataset.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dataset import DatasetWriter  # noqa: E402
from app.features import FeatureRow  # noqa: E402

DAY = 86400.0
# 2026-02-01T00:00:00Z and the following day, so part filenames are predictable.
DAY1 = 1769904000.0
DAY2 = DAY1 + DAY


def row(ts, stage=1.0):
    return FeatureRow(
        ts=ts, stage_ft=stage, rate_of_rise_in_min=None, soil_moisture_mean_pct=50.0,
        soil_moisture_near_house_pct=50.0, soil_moisture_near_creek_pct=50.0,
        ponding_flag=False,
    )


def make():
    return DatasetWriter(Path(tempfile.mkdtemp()))


def test_appends_land_in_a_per_day_part_file():
    d = make()
    d.append_row(row(DAY1))
    d.append_row(row(DAY1 + 300))
    d.append_row(row(DAY2))
    names = sorted(p.name for p in d._parts.glob("*.jsonl"))
    assert names == ["2026-02-01.jsonl", "2026-02-02.jsonl"]
    assert d.row_count() == 3


def test_todays_part_is_left_open_for_the_running_loop():
    d = make()
    d.append_row(row(DAY1))
    d.append_row(row(DAY2))
    pending = [p.name for p in d.pending_parts(now=DAY2)]
    assert pending == ["2026-02-01.jsonl"]


def test_consolidate_folds_completed_parts_and_removes_them():
    d = make()
    for i in range(5):
        d.append_row(row(DAY1 + i * 300))
    d.append_row(row(DAY2))
    assert d.consolidate(now=DAY2) == 6          # count includes today's open part
    assert [p.name for p in d._parts.glob("*.jsonl")] == ["2026-02-02.jsonl"]
    assert d._parquet.exists()
    assert d.row_count() == 6


def test_consolidate_is_idempotent_and_deduplicates_by_timestamp():
    d = make()
    d.append_row(row(DAY1))
    d.append_row(row(DAY1))                      # same ts replayed
    assert d.consolidate(now=DAY2) == 1
    assert d.consolidate(now=DAY2) == 1          # nothing left to fold


def test_nothing_to_consolidate_is_not_an_error():
    d = make()
    assert d.consolidate(now=DAY1) == 0


def test_torn_final_line_does_not_lose_the_rest_of_the_day():
    d = make()
    for i in range(3):
        d.append_row(row(DAY1 + i * 300))
    # Simulate an unclean shutdown mid-write.
    part = d._parts / "2026-02-01.jsonl"
    part.write_text(part.read_text() + '{"ts": 123, "stage_ft":', encoding="utf-8")
    assert d.consolidate(now=DAY2) == 3


def test_frame_spans_parquet_and_unconsolidated_parts():
    d = make()
    d.append_row(row(DAY1, stage=1.0))
    d.consolidate(now=DAY2)
    d.append_row(row(DAY2, stage=2.0))
    df = d.frame()
    assert len(df) == 2
    assert list(df["stage_ft"]) == [1.0, 2.0]    # sorted by ts, both tiers present


def test_frame_fills_requested_columns_that_do_not_exist_yet():
    d = make()
    d.append_row(row(DAY1))
    df = d.frame(columns=["ts", "stage_ft", "a_future_feature"])
    assert list(df.columns) == ["ts", "stage_ft", "a_future_feature"]
    assert df["a_future_feature"].isna().all()


def test_empty_dataset_returns_an_empty_frame_not_an_error():
    assert len(make().frame()) == 0


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
