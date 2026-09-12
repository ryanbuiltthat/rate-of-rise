"""Tests for the Weather Underground upstream source (no HTTP).
Run: python creek_modeling/tests/test_wu.py
"""
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.wu import WuUpstream  # noqa: E402

# per-station imperial obs the fake API returns
STATIONS = {
    "KA": {"precipRate": 0.4, "precipTotal": 1.0},
    "KB": {"precipRate": 0.6, "precipTotal": 2.0},
}


def fake_fetch(url):
    sid = parse_qs(urlparse(url).query)["stationId"][0]
    if sid == "BAD":
        raise RuntimeError("station offline")
    return {"observations": [{"imperial": STATIONS[sid]}]}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(stations):
    tmp = Path(tempfile.mkdtemp())
    clock = Clock()
    return WuUpstream("KEY", stations, tmp, now_fn=clock, fetch=fake_fetch), clock


def test_mean_rate_accumulates_and_today():
    wu, clock = make(["KA", "KB"])         # mean precipRate = 0.5 in/hr
    wu.poll()                               # baseline at t=0
    clock.t = 600                           # +10 min
    out = wu.poll()
    assert abs(out["upstream_rain_1h_in"] - 0.5 * (600 / 3600)) < 0.002
    assert out["upstream_precip_today_in"] == 1.5   # mean of 1.0, 2.0


def test_bad_station_is_skipped_not_fatal():
    wu, clock = make(["KA", "BAD"])         # BAD raises; KA still counts
    wu.poll()
    clock.t = 600
    out = wu.poll()
    assert abs(out["upstream_rain_1h_in"] - 0.4 * (600 / 3600)) < 0.002
    assert out["upstream_precip_today_in"] == 1.0


def test_no_observations_yields_none_today():
    def empty_fetch(url):
        return {"observations": []}
    tmp = Path(tempfile.mkdtemp())
    wu = WuUpstream("KEY", ["KA"], tmp, now_fn=Clock(), fetch=empty_fetch)
    out = wu.poll()
    assert out["upstream_precip_today_in"] is None
    assert out["upstream_rain_1h_in"] == 0.0


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
