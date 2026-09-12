"""Tests for the sensor-fault watchdogs (spec §4), migrated out of the HA package.
Run: python creek_modeling/tests/test_health.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.features import FeatureRow  # noqa: E402
from app.health import WATCHDOG_KEYS, HealthTracker  # noqa: E402

ALL_SOURCES = {"nws", "alerts", "wu", "nwm", "usgs", "snodas"}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds
        return self


def row(**kw):
    base = dict(
        ts=0.0, stage_ft=None, rate_of_rise_in_min=None, soil_moisture_mean_pct=None,
        soil_moisture_near_house_pct=None, soil_moisture_near_creek_pct=None,
        ponding_flag=False,
    )
    base.update(kw)
    return FeatureRow(**base)


def healthy_sources(age=10.0):
    return {n: {"age": age, "stale_after": 1800} for n in ALL_SOURCES}


def test_nothing_fires_during_the_boot_grace_period():
    # Every input is missing at startup — that must not light up nine alarms.
    clock = Clock()
    h = HealthTracker(now_fn=clock)
    flags = h.evaluate(row(), {n: {"age": None, "stale_after": 1800} for n in ALL_SOURCES},
                       ALL_SOURCES)
    assert set(flags) == set(WATCHDOG_KEYS)
    assert not any(flags.values())


def test_missing_input_becomes_stale_once_its_threshold_passes():
    clock = Clock()
    h = HealthTracker(now_fn=clock)
    h.evaluate(row(), healthy_sources(), ALL_SOURCES)
    clock.advance(1801)                       # stage threshold is 1800 s
    flags = h.evaluate(row(), healthy_sources(), ALL_SOURCES)
    assert flags["stage_stale"] is True
    assert flags["soil_moisture_stale"] is False   # its threshold is 3600 s


def test_a_reading_clears_the_watchdog():
    clock = Clock()
    h = HealthTracker(now_fn=clock)
    clock.advance(5000)
    assert h.evaluate(row(), healthy_sources(), ALL_SOURCES)["stage_stale"] is True
    assert h.evaluate(row(stage_ft=1.2), healthy_sources(), ALL_SOURCES)["stage_stale"] is False


def test_rain_rate_of_zero_is_a_reading_not_a_fault():
    # The bug the HA template had: 0.00 in/h never changes state, so a last_changed check
    # false-alarmed all through a dry spell.
    clock = Clock()
    h = HealthTracker(now_fn=clock)
    h.evaluate(row(rain_rate_in_hr=0.0), healthy_sources(), ALL_SOURCES)
    clock.advance(100000)
    assert h.evaluate(row(rain_rate_in_hr=0.0), healthy_sources(),
                      ALL_SOURCES)["rain_rate_stale"] is False


def test_a_dead_source_is_caught_even_though_its_feature_still_has_a_value():
    # The blind spot the templates could not see: the coordinator serves the last-good
    # value forever, so `has_value()` stays true long after the API stopped answering.
    clock = Clock()
    h = HealthTracker(now_fn=clock)
    sources = healthy_sources()
    sources["wu"] = {"age": 7200.0, "stale_after": 3600}
    flags = h.evaluate(row(upstream_rain_3h_in=0.42), sources, ALL_SOURCES)
    assert flags["upstream_data_missing"] is True
    assert flags["nwm_data_missing"] is False


def test_a_source_that_is_not_configured_is_not_a_fault():
    clock = Clock()
    h = HealthTracker(now_fn=clock)
    clock.advance(100000)
    configured = ALL_SOURCES - {"wu", "snodas"}
    flags = h.evaluate(row(), healthy_sources(), configured)
    assert flags["upstream_data_missing"] is False
    assert flags["snowpack_data_missing"] is False


def test_a_source_that_never_succeeds_eventually_reports():
    clock = Clock()
    h = HealthTracker(now_fn=clock)
    never = {n: {"age": None, "stale_after": 1800} for n in ALL_SOURCES}
    clock.advance(1801)
    assert h.evaluate(row(), never, ALL_SOURCES)["forecast_data_missing"] is True


def test_snodas_gets_its_longer_window():
    clock = Clock()
    h = HealthTracker(now_fn=clock)
    sources = healthy_sources()
    sources["snodas"] = {"age": 2 * 86400, "stale_after": 3 * 86400}
    # A daily product that skipped a day is normal, not a fault.
    assert h.evaluate(row(), sources, ALL_SOURCES)["snowpack_data_missing"] is False


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
