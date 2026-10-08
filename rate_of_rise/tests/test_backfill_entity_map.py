"""Units and the record-field registry the backfill writes from.

Run: python rate_of_rise/tests/test_backfill_entity_map.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backfill.entity_map import (  # noqa: E402
    MISSING, field_spec, parse_map, points_for, reset_cause_text)
from app.backfill.units import UnitMismatch, convert, format_state  # noqa: E402


def test_convert_lengths_voltage_rate_and_temperature():
    assert abs(convert(812, "mm", "in") - 31.968504) < 1e-6
    assert convert(4180, "mV", "V") == 4.18
    assert abs(convert(1.0, "in/h", "mm/h") - 25.4) < 1e-9
    assert abs(convert(45.9, "°F", "°C") - 7.722222) < 1e-5
    assert convert(-72, "dBm", "dBm") == -72
    assert convert(5, None, None) == 5


def test_convert_refuses_incompatible_units():
    try:
        convert(1.0, "mm", "kg")
    except UnitMismatch:
        return
    raise AssertionError("expected UnitMismatch")


def test_format_state_matches_ha_float_text():
    assert format_state(-59.0) == "-59.0"
    assert format_state(0.9583333333) == "0.958333"
    assert format_state(4.18) == "4.18"


def test_node_fields_extract_absent_null_and_values():
    rec = {"seq": 1, "ts": 10.0, "rssi": -72, "d": None, "v": 4012, "f": 1, "r": 1,
           "stage_ft": None}
    assert field_spec("node", "distance_mm").extract(rec) is None          # null: unknown
    assert field_spec("node", "cycle").extract(rec) is MISSING             # absent: skip
    assert field_spec("node", "fast").extract(rec) is True
    assert field_spec("node", "diag_active").extract(rec) is MISSING
    assert field_spec("node", "reset_cause").extract(rec) == "power-on"
    assert field_spec("node", "node_status").extract(rec) is True
    assert field_spec("node", "battery_mv").unit == "mV"


def test_reset_cause_text_matches_firmware():
    assert reset_cause_text(0x40) == "software"
    assert reset_cause_text(0x20) == "watchdog"
    assert reset_cause_text(0x04) == "brown-out 3.3 V"
    assert reset_cause_text(0) == "unknown"


def test_ecowitt_soil_channels():
    rec = {"seq": 3, "ts": 5.0, "soil": {"2": 61}, "rain_year_in": 29.84}
    assert field_spec("ecowitt", "soil_ch2").extract(rec) == 61
    assert field_spec("ecowitt", "soil_ch1").extract(rec) is MISSING
    assert field_spec("ecowitt", "rain_total_in").extract(rec) == 29.84


def test_ecowitt_null_is_not_reported_not_unknown():
    # The console's null means "no reading this poll" (the live Ecowitt entity keeps its
    # value), so it writes no row. Node nulls stay None, which is `unknown`.
    rec = {"seq": 4, "ts": 6.0, "rain_year_in": None, "temp_f": None, "soil": {"2": None}}
    assert field_spec("ecowitt", "rain_total_in").extract(rec) is MISSING
    assert field_spec("ecowitt", "temp_f").extract(rec) is MISSING
    assert field_spec("ecowitt", "soil_ch2").extract(rec) is MISSING
    assert points_for("ecowitt", "rain_total_in", [rec]) == []
    assert field_spec("node", "stage_ft").extract({"stage_ft": None}) is None


def test_points_for_skips_missing():
    recs = [{"seq": 1, "ts": 1.0, "n": 5}, {"seq": 2, "ts": 2.0}, {"seq": 3, "ts": 3.0, "n": 6}]
    pts = points_for("node", "cycle", recs)
    assert [(p.ts, p.value) for p in pts] == [(1.0, 5), (3.0, 6)]


def test_parse_map_blank_valid_and_invalid():
    assert parse_map("") == {}
    m = parse_map('{"node": {"stage_ft": "sensor.creek_gateway_v2_stage"},'
                  ' "ecowitt": {"soil_ch2": "sensor.field"}}')
    assert m["node"]["stage_ft"] == "sensor.creek_gateway_v2_stage"
    for bad in ('not json', '{"node": {"bogus": "sensor.x"}}', '{"other": {}}',
                '{"node": {"stage_ft": "no_domain"}}', '["node"]'):
        try:
            parse_map(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
