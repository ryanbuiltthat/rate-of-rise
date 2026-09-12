"""Tests for the rain-on-snow derivation and its tier rule (spec §1/§5/§6).
Run: python creek_modeling/tests/test_rain_on_snow.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.features import FeatureBuilder, FeatureRow, _to_fahrenheit  # noqa: E402
from app.tiers import compute_tier  # noqa: E402


def row(**kw):
    base = dict(
        ts=0.0, stage_ft=None, rate_of_rise_in_min=None, soil_moisture_mean_pct=40.0,
        soil_moisture_near_house_pct=40.0, soil_moisture_near_creek_pct=40.0,
        ponding_flag=False,
    )
    base.update(kw)
    return FeatureRow(**base)


def ros(**kw):
    return FeatureBuilder._rain_on_snow(row(**kw))


def test_celsius_is_converted_fahrenheit_is_left_alone():
    assert _to_fahrenheit(0.0, "°C") == 32.0
    assert _to_fahrenheit(100.0, "°C") == 212.0
    assert _to_fahrenheit(41.0, "°F") == 41.0
    assert _to_fahrenheit(None, "°C") is None


def test_fires_when_warm_rain_falls_on_a_pack():
    assert ros(snow_water_equivalent_in=1.5, temp_f=38.0, rain_1h_in=0.05) is True


def test_forecast_rain_also_fires_it():
    assert ros(snow_water_equivalent_in=1.5, temp_f=38.0, qpf_6h_in=0.3) is True


def test_frozen_conditions_do_not_fire():
    # Below freezing the precip is snow, not rain — it adds to the pack, it doesn't melt it.
    assert ros(snow_water_equivalent_in=1.5, temp_f=25.0, rain_1h_in=0.5) is False


def test_negligible_snowpack_does_not_fire():
    assert ros(snow_water_equivalent_in=0.01, temp_f=40.0, rain_1h_in=0.5) is False


def test_dry_warm_day_over_snow_does_not_fire():
    assert ros(snow_water_equivalent_in=2.0, temp_f=45.0) is False


def test_missing_inputs_never_fire():
    # No SNODAS yet, or no temperature entity configured.
    assert ros(temp_f=40.0, rain_1h_in=0.5) is False
    assert ros(snow_water_equivalent_in=2.0, rain_1h_in=0.5) is False


def test_tier_escalates_advisory_on_forecast_watch_once_raining():
    tier, label, reasons = compute_tier(
        row(rain_on_snow_flag=True, snow_water_equivalent_in=1.5, qpf_6h_in=0.3), 0.0)
    assert (tier, label) == (1, "Advisory")
    assert "snowpack" in reasons[0]

    tier, label, reasons = compute_tier(
        row(rain_on_snow_flag=True, snow_water_equivalent_in=1.5, rain_1h_in=0.10), 0.0)
    assert (tier, label) == (2, "Watch")
    assert "melt adds to runoff" in reasons[0]


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
