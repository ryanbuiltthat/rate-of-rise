"""Tests for the USGS downstream-gauge source (no HTTP).
Run: python creek_modeling/tests/test_usgs.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.usgs import UsgsDownstream, feature_keys  # noqa: E402


def series(site, param, points):
    """points: [(iso_datetime, value_str), ...]"""
    return {
        "sourceInfo": {"siteCode": [{"value": site}]},
        "variable": {"variableCode": [{"value": param}]},
        "values": [{"value": [{"dateTime": t, "value": v} for t, v in points]}],
    }


def make(*ts):
    return UsgsDownstream(fetch=lambda url: {"value": {"timeSeries": list(ts)}})


def test_feature_keys_cover_both_sites():
    keys = feature_keys()
    assert len(keys) == 6
    assert "usgs_leggetts_gage_ft" in keys
    assert "usgs_tunkhannock_rise_3h_ft" in keys


def test_latest_gage_and_flow():
    out = make(
        series("01534860", "00065", [
            ("2026-07-26T09:00:00-04:00", "2.10"),
            ("2026-07-26T12:00:00-04:00", "2.85"),
        ]),
        series("01534860", "00060", [
            ("2026-07-26T12:00:00-04:00", "141.5"),
        ]),
    ).poll()
    assert out["usgs_leggetts_gage_ft"] == 2.85     # newest reading
    assert out["usgs_leggetts_flow_cfs"] == 141.5
    assert out["usgs_leggetts_rise_3h_ft"] == 0.75  # 2.85 - 2.10 over exactly 3 h


def test_series_shorter_than_window_reports_no_rise():
    out = make(
        series("01534000", "00065", [
            ("2026-07-26T11:00:00-04:00", "1.00"),
            ("2026-07-26T12:00:00-04:00", "1.40"),
        ]),
    ).poll()
    assert out["usgs_tunkhannock_gage_ft"] == 1.4
    # Only 1 h of record — a partial rise would understate the real 3 h change.
    assert out["usgs_tunkhannock_rise_3h_ft"] is None


def test_no_data_sentinel_is_dropped():
    out = make(
        series("01534860", "00065", [
            ("2026-07-26T09:00:00-04:00", "2.00"),
            ("2026-07-26T12:00:00-04:00", "-999999"),
        ]),
    ).poll()
    assert out["usgs_leggetts_gage_ft"] == 2.0   # sentinel ignored, not reported as level


def test_unsorted_points_are_ordered_before_use():
    out = make(
        series("01534860", "00065", [
            ("2026-07-26T12:00:00-04:00", "3.00"),
            ("2026-07-26T09:00:00-04:00", "2.00"),
        ]),
    ).poll()
    assert out["usgs_leggetts_gage_ft"] == 3.0
    assert out["usgs_leggetts_rise_3h_ft"] == 1.0


def test_unknown_site_ignored_and_missing_sites_are_none():
    out = make(series("09999999", "00065", [("2026-07-26T12:00:00-04:00", "5.0")])).poll()
    assert all(v is None for v in out.values())
    assert set(out) == set(feature_keys())


def test_empty_response_yields_all_none():
    out = UsgsDownstream(fetch=lambda url: {}).poll()
    assert set(out) == set(feature_keys())
    assert all(v is None for v in out.values())


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
