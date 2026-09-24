"""Tests for the rolling rain accumulator. Run: python rate_of_rise/tests/test_rain.py"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.rain import RainAccumulator  # noqa: E402


class FakeHA:
    def __init__(self, rate, unit="in/h"):
        self.rate, self.unit = rate, unit

    def get_float(self, _entity):
        return self.rate

    def get_unit(self, _entity):
        return self.unit


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(rate, unit="in/h"):
    tmp = Path(tempfile.mkdtemp())
    clock = Clock()
    acc = RainAccumulator(tmp, "sensor.rain_rate", FakeHA(rate, unit), now_fn=clock)
    return acc, clock, tmp


def test_first_poll_no_increment():
    acc, clock, _ = make(1.0)
    out = acc.poll()  # no prior sample -> baseline only
    assert out["rain_1h_in"] == 0.0


def test_integrates_rate_over_time():
    acc, clock, _ = make(1.0)          # 1.0 in/hr constant
    acc.poll()                          # baseline at t=0
    clock.t = 600                       # +10 min
    acc.poll()                          # +1.0*(600/3600)=0.1667 in
    clock.t = 1200                      # +10 min
    out = acc.poll()                    # another 0.1667
    assert abs(out["rain_1h_in"] - 0.333) < 0.002
    assert abs(out["rain_72h_in"] - 0.333) < 0.002


def test_gap_longer_than_max_is_not_integrated():
    acc, clock, _ = make(1.0)
    acc.poll()                          # t=0 baseline
    clock.t = 3600                      # +60 min > MAX_GAP_S (15 min)
    out = acc.poll()
    assert out["rain_1h_in"] == 0.0     # gap skipped, no phantom hour of rain


def test_window_expiry():
    acc, clock, _ = make(1.0)
    acc.poll()
    clock.t = 600
    acc.poll()                          # 0.1667 in at t=600
    clock.t = 600 + 2 * 3600            # advance >1h but within gap? no—just move clock
    # A poll here would be a gap (skipped), but the 1h window should have expired the
    # earlier increment; advance the clock only (no new rain) via a fresh baseline.
    out = acc.poll()
    assert out["rain_1h_in"] == 0.0     # older increment aged out of the 1h window
    assert abs(out["rain_24h_in"] - 0.1667) < 0.002  # still within 24h


def test_mm_unit_converts_to_inches():
    acc, clock, _ = make(25.4, unit="mm/h")   # 25.4 mm/hr == 1 in/hr
    acc.poll()
    clock.t = 3600 // 6                        # +10 min
    out = acc.poll()
    assert abs(out["rain_1h_in"] - 0.1667) < 0.002


def test_persistence_keeps_history():
    acc, clock, tmp = make(1.0)
    acc.poll()
    clock.t = 600
    acc.poll()                                 # 0.1667 in persisted
    reloaded = RainAccumulator(tmp, "sensor.rain_rate", FakeHA(0.0), now_fn=lambda: 700)
    out = reloaded.poll()                      # first poll after restart = baseline, no add
    assert abs(out["rain_1h_in"] - 0.1667) < 0.002  # prior increment survived


class CounterHA:
    """A rate entity plus the station's own monotonic counter."""

    def __init__(self, rate=0.0, total=10.0, unit="in"):
        self.rate, self.total, self.unit = rate, total, unit

    def get_float(self, entity):
        return self.total if entity == "sensor.rain_total" else self.rate

    def get_unit(self, entity):
        return self.unit if entity == "sensor.rain_total" else "in/h"


def make_counter(ha):
    tmp = Path(tempfile.mkdtemp())
    clock = Clock()
    acc = RainAccumulator(tmp, "sensor.rain_rate", ha, now_fn=clock,
                          total_entity="sensor.rain_total")
    return acc, clock, tmp


def test_the_counter_gives_exact_rain_even_when_the_rate_misses_it():
    """A burst between two samples leaves the rate at 0 both times; the counter moved."""
    ha = CounterHA(rate=0.0, total=27.783)
    acc, clock, _ = make_counter(ha)
    acc.poll()                                    # baseline
    clock.t = 300
    ha.total = 28.035                             # 0.252 in, rate never saw it
    out = acc.poll()
    assert abs(out["rain_1h_in"] - 0.252) < 1e-9, out
    assert out["api_index_in"] > 0.25


def test_a_counter_reset_is_not_negative_rain():
    ha = CounterHA(total=30.0)
    acc, clock, _ = make_counter(ha)
    acc.poll()
    clock.t = 300
    ha.total = 0.0
    assert acc.poll()["rain_1h_in"] == 0.0
    clock.t = 600
    ha.total = 0.1
    assert abs(acc.poll()["rain_1h_in"] - 0.1) < 1e-9, "counts again from the new value"


def test_a_long_counter_gap_rebaselines_instead_of_landing_it_in_one_loop():
    ha = CounterHA(total=5.0)
    acc, clock, _ = make_counter(ha)
    acc.poll()
    clock.t = 3 * 3600
    ha.total = 6.2                                # 1.2 in over 3 h: real, but not "now"
    assert acc.poll()["rain_1h_in"] == 0.0


def test_an_unavailable_counter_falls_back_to_the_rate_without_double_counting():
    ha = CounterHA(rate=1.2, total=5.0)
    acc, clock, _ = make_counter(ha)
    acc.poll()
    clock.t = 300
    ha.total = None                               # counter down: rate covers 5 min
    out = acc.poll()
    assert abs(out["rain_1h_in"] - 0.1) < 1e-9
    clock.t = 600
    ha.total = 5.3                                # back: a fresh baseline, not +0.3
    assert abs(acc.poll()["rain_1h_in"] - 0.1) < 1e-9


def test_the_counter_baseline_survives_a_restart():
    ha = CounterHA(total=5.0)
    acc, clock, tmp = make_counter(ha)
    acc.poll()
    ha.total = 5.2
    again = RainAccumulator(tmp, "sensor.rain_rate", ha, now_fn=lambda: 120.0,
                            total_entity="sensor.rain_total")
    assert abs(again.poll()["rain_1h_in"] - 0.2) < 1e-9, "rain across the restart was lost"


def test_a_millimetre_counter_is_converted():
    ha = CounterHA(total=100.0, unit="mm")
    acc, clock, _ = make_counter(ha)
    acc.poll()
    clock.t = 300
    ha.total = 125.4
    assert abs(acc.poll()["rain_1h_in"] - 1.0) < 1e-9


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
