"""Backfilled node readings merged into the add-on's own stage log.

Run: python rate_of_rise/tests/test_backfill_stagelog.py
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backfill.stagelog_merge import merge_stage_rows  # noqa: E402
from app.stagelog import HEADER  # noqa: E402


def day_of(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")   # StageLogger's naming: local day


def lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def test_adds_missing_rows_sorted_and_skips_near_duplicates():
    d = Path(tempfile.mkdtemp())
    base = datetime(2026, 10, 7, 12, 0).timestamp()
    f = d / f"{day_of(base)}.csv"
    f.write_text(HEADER + f"{base},{base + 3},0.9000\n{base + 300},{base + 301},0.9500\n",
                 encoding="utf-8")
    added = merge_stage_rows(d, [(base + 1.5, 0.9), (base + 120, 0.92), (base + 240, None)],
                             now=base + 1000)
    assert added == 2
    got = lines(f)
    assert got[0] == HEADER.strip()
    assert [r.split(",")[0] for r in got[1:]] == [
        str(base), str(round(base + 120, 1)), str(round(base + 240, 1)), str(base + 300)]
    assert got[2].split(",")[2] == "0.9200" and got[3].split(",")[2] == ""


def test_rows_across_local_midnight_go_to_their_own_days():
    d = Path(tempfile.mkdtemp())
    midnight = datetime(2026, 10, 8, 0, 0).timestamp()
    assert merge_stage_rows(d, [(midnight - 60, 1.0), (midnight + 60, 1.1)], now=midnight) == 2
    assert (d / f"{day_of(midnight - 60)}.csv").exists()
    assert (d / f"{day_of(midnight + 60)}.csv").exists()


def test_nothing_new_leaves_files_untouched():
    d = Path(tempfile.mkdtemp())
    base = datetime(2026, 10, 7, 12, 0).timestamp()
    f = d / f"{day_of(base)}.csv"
    f.write_text(HEADER + f"{base},{base},0.9000\n", encoding="utf-8")
    before = f.stat().st_mtime_ns
    assert merge_stage_rows(d, [(base + 1, 0.9)], now=base) == 0
    assert f.stat().st_mtime_ns == before


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
