"""Tests for the NWM/NWPS reach source (no HTTP).
Run: python creek_modeling/tests/test_nwm.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.nwm import NwmReach  # noqa: E402


def make(data_points):
    def fetch(url):
        return {"shortRange": {"series": {"units": "ft³/s", "data": data_points}}}
    return NwmReach("5781431", fetch=fetch)


def test_near_term_and_peak():
    nwm = make([
        {"validTime": "2026-07-25T17:00:00Z", "flow": 3.17},
        {"validTime": "2026-07-25T18:00:00Z", "flow": 5.20},
        {"validTime": "2026-07-25T19:00:00Z", "flow": 2.83},
    ])
    out = nwm.poll()
    assert out["nwm_flow_cfs"] == 3.17      # first point
    assert out["nwm_flow_max_cfs"] == 5.2   # peak


def test_empty_series_yields_none():
    out = make([]).poll()
    assert out["nwm_flow_cfs"] is None
    assert out["nwm_flow_max_cfs"] is None


def test_skips_null_flow_points():
    nwm = make([
        {"validTime": "t1", "flow": None},
        {"validTime": "t2", "flow": 4.0},
    ])
    out = nwm.poll()
    assert out["nwm_flow_cfs"] == 4.0
    assert out["nwm_flow_max_cfs"] == 4.0


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
