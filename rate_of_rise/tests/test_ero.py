"""Plain-assert tests for the WPC Excessive Rainfall Outlook source.
Run: python rate_of_rise/tests/test_ero.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.ero import WpcEro  # noqa: E402

SITE = (41.0, -75.5)


def build(rows, wrap=True):
    seen = []

    def fetch(url, timeout=15.0):
        seen.append(url)
        return {"data": rows} if wrap else rows

    return WpcEro(*SITE, fetch=fetch), seen


def ero(day, threshold, category="CATEGORICAL"):
    return {"day": day, "outlook_type": "E", "category": category,
            "threshold": threshold}


def test_reads_the_day1_category_for_the_site():
    src, _ = build([ero(1, "SLGT"), ero(2, "MRGL"), ero(3, "MRGL")])
    out = src.poll()
    assert out["wpc_ero_day1_risk"] == 2.0
    assert out["wpc_ero_day2_risk"] == 1.0
    assert out["wpc_ero_day3_risk"] == 1.0


def test_the_whole_ladder_is_ordered():
    """Tier rules compare these numerically, so the order has to hold."""
    for threshold, expected in (("MRGL", 1.0), ("SLGT", 2.0), ("MDT", 3.0), ("HIGH", 4.0)):
        src, _ = build([ero(1, threshold)])
        assert src.poll()["wpc_ero_day1_risk"] == expected, threshold


def test_no_risk_area_is_zero_not_unknown():
    """The distinction the whole source rests on: WPC looking at this point and drawing
    no risk area is a real forecast of low risk. Reporting it as None would make it
    indistinguishable from a failed fetch, and the watchdog would cry wolf every quiet
    day -- or worse, a None would be read as 'no data' when it means 'no danger'."""
    src, _ = build([])
    out = src.poll()
    assert out == {"wpc_ero_day1_risk": 0.0, "wpc_ero_day2_risk": 0.0,
                   "wpc_ero_day3_risk": 0.0}


def test_an_unknown_category_is_unknown_rather_than_no_risk():
    """If WPC adds or renames a category, calling it 0.0 silently downgrades whatever
    they meant -- the one failure mode here that could hide a real risk."""
    src, _ = build([ero(1, "CATASTROPHIC")])
    assert src.poll()["wpc_ero_day1_risk"] is None


def test_spc_convective_outlooks_are_ignored():
    """The same endpoint serves SPC convective (type C) outlooks, which grade severe
    weather -- hail, wind, tornado -- not rainfall. Reading a TSTM row as a rainfall
    risk would fire the tier on almost every summer day."""
    src, _ = build([{"day": 1, "outlook_type": "C", "category": "CATEGORICAL",
                     "threshold": "TSTM"},
                    {"day": 1, "outlook_type": "C", "category": "CATEGORICAL",
                     "threshold": "ENH"}])
    assert src.poll()["wpc_ero_day1_risk"] == 0.0


def test_non_categorical_ero_rows_are_ignored():
    """WPC also issues probabilistic ERO layers; only the categorical one maps onto
    this ladder."""
    src, _ = build([ero(1, "0.05", category="PROBABILISTIC"), ero(1, "MRGL")])
    assert src.poll()["wpc_ero_day1_risk"] == 1.0


def test_days_beyond_three_are_not_ingested():
    """WPC has extended the ERO past day 3; days 1-3 are the calibrated core and the
    only ones with feature columns."""
    src, _ = build([ero(4, "HIGH"), ero(5, "HIGH"), ero(1, "MRGL")])
    out = src.poll()
    assert out["wpc_ero_day1_risk"] == 1.0
    assert set(out) == {"wpc_ero_day1_risk", "wpc_ero_day2_risk", "wpc_ero_day3_risk"}


def test_the_highest_risk_for_a_day_wins():
    """Overlapping polygons can put a point in more than one row; the worse one is the
    one that matters."""
    src, _ = build([ero(1, "MRGL"), ero(1, "MDT"), ero(1, "SLGT")])
    assert src.poll()["wpc_ero_day1_risk"] == 3.0


def test_malformed_rows_are_skipped_not_fatal():
    src, _ = build([{"day": "x", "outlook_type": "E", "category": "CATEGORICAL",
                     "threshold": "SLGT"},
                    {"outlook_type": "E", "category": "CATEGORICAL"},
                    ero(1, "SLGT")])
    assert src.poll()["wpc_ero_day1_risk"] == 2.0


def test_a_bare_list_response_is_accepted():
    """The service has returned both a bare list and a {"data": [...]} envelope."""
    src, _ = build([ero(1, "SLGT")], wrap=False)
    assert src.poll()["wpc_ero_day1_risk"] == 2.0


def test_the_url_carries_the_site_coordinates():
    src, seen = build([])
    src.poll()
    assert "lat=41.0" in seen[0] and "lon=-75.5" in seen[0], seen[0]
    assert "outlook_by_point" in seen[0]


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
