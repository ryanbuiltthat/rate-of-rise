"""Tests for the Weather Underground upstream source (no HTTP).
Run: python rate_of_rise/tests/test_wu.py
"""
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.wu import WuUpstream  # noqa: E402


class FakeWU:
    """Per-station observations the fake API returns; a station set to None is down."""

    def __init__(self, **stations):
        self.stations = stations

    def set(self, sid, total=None, rate=None, day="2026-09-27"):
        self.stations[sid] = {"precipTotal": total, "precipRate": rate, "day": day}

    def __call__(self, url):
        sid = parse_qs(urlparse(url).query)["stationId"][0]
        obs = self.stations.get(sid)
        if obs is None:
            raise RuntimeError("station offline")
        return {"observations": [{
            "obsTimeLocal": f"{obs['day']} 12:00:00",
            "imperial": {"precipTotal": obs["precipTotal"], "precipRate": obs["precipRate"]},
        }]}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(stations, fake, tmp=None):
    tmp = tmp or Path(tempfile.mkdtemp())
    clock = Clock()
    return WuUpstream("KEY", stations, tmp, now_fn=clock, fetch=fake), clock, tmp


def test_rain_is_the_difference_in_each_stations_own_total():
    fake = FakeWU()
    fake.set("KA", total=1.00)
    fake.set("KB", total=2.00)
    wu, clock, _ = make(["KA", "KB"], fake)
    out = wu.poll()                                   # baseline
    assert out["upstream_rain_1h_in"] == 0.0
    clock.t = 600
    fake.set("KA", total=1.10)                        # +0.10
    fake.set("KB", total=2.30)                        # +0.30
    out = wu.poll()
    assert abs(out["upstream_rain_1h_in"] - 0.20) < 1e-9, out   # mean of the two
    assert out["upstream_precip_today_in"] == 1.7                # mean of 1.10, 2.30
    assert wu.last_poll_ok is True


def test_a_burst_between_polls_is_counted_in_full():
    """What the rate-integration approach could not do: a burst that starts and stops
    between two polls leaves precipRate at 0 both times but moves the total."""
    fake = FakeWU()
    fake.set("KA", total=0.0, rate=0.0)
    wu, clock, _ = make(["KA"], fake)
    wu.poll()
    clock.t = 600
    fake.set("KA", total=0.45, rate=0.0)
    assert abs(wu.poll()["upstream_rain_1h_in"] - 0.45) < 1e-9


def test_a_bad_station_is_skipped_not_fatal():
    fake = FakeWU()
    fake.set("KA", total=1.0)
    wu, clock, _ = make(["KA", "BAD"], fake)
    wu.poll()
    clock.t = 600
    fake.set("KA", total=1.2)
    out = wu.poll()
    assert abs(out["upstream_rain_1h_in"] - 0.2) < 1e-9
    assert out["upstream_precip_today_in"] == 1.2
    assert wu.last_poll_ok is True


def test_no_station_answering_is_a_failed_poll():
    """The watchdog gap: every per-station error was swallowed, poll() always returned,
    and `upstream_data_missing` could never fire. It has to be able to."""
    wu, clock, _ = make(["KA", "KB"], FakeWU())
    out = wu.poll()
    assert wu.last_poll_ok is False
    assert out["upstream_precip_today_in"] is None


def test_a_long_silence_reads_as_unknown_not_dry():
    fake = FakeWU()
    fake.set("KA", total=0.5)
    wu, clock, _ = make(["KA"], fake)
    wu.poll()
    fake.stations["KA"] = None
    clock.t = 600
    assert wu.poll()["upstream_rain_1h_in"] == 0.0, "one missed poll changes nothing"
    clock.t = 2400                                           # 40 min since last answer
    assert wu.poll()["upstream_rain_1h_in"] is None


def test_local_midnight_restarts_the_total():
    fake = FakeWU()
    fake.set("KA", total=0.80, day="2026-09-27")
    wu, clock, _ = make(["KA"], fake)
    wu.poll()
    clock.t = 600
    fake.set("KA", total=0.05, day="2026-09-28")              # new day: 0.05 since midnight
    assert abs(wu.poll()["upstream_rain_1h_in"] - 0.05) < 1e-9


def test_a_dip_in_the_feed_is_not_counted_twice():
    fake = FakeWU()
    fake.set("KA", total=0.50)
    wu, clock, _ = make(["KA"], fake)
    wu.poll()
    for t, total in ((600, 0.0), (1200, 0.50)):               # glitch down, then back
        clock.t = t
        fake.set("KA", total=total)
        out = wu.poll()
    assert out["upstream_rain_1h_in"] == 0.0, out


def test_a_station_without_a_total_falls_back_to_the_rate():
    fake = FakeWU()
    fake.set("KA", total=None, rate=0.6)
    wu, clock, _ = make(["KA"], fake)
    wu.poll()
    clock.t = 600
    assert abs(wu.poll()["upstream_rain_1h_in"] - 0.1) < 0.002    # 0.6 in/h * 10 min


def test_the_old_combined_history_seeds_each_station():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "state").mkdir()
    (tmp / "state" / "upstream_rain.json").write_text(
        json.dumps({"increments": [[-600.0, 0.3]]}), encoding="utf-8")
    fake = FakeWU()
    fake.set("KA", total=1.0)
    fake.set("KB", total=1.0)
    wu, clock, _ = make(["KA", "KB"], fake, tmp)
    out = wu.poll()
    assert abs(out["upstream_rain_1h_in"] - 0.3) < 1e-9, "upgrade restarted the window"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
