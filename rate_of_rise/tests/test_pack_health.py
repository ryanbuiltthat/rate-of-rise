"""The creek node's battery-pack health verdict (ha-packages/creek_node_health.yaml).

The node reports one number about power, its battery voltage, and in daylight even that is
the charger's OUT rail rather than the cell. So the verdict is taken once a day at 04:30
from the night's readings: the pre-dawn voltage, the overnight least-squares slope (HA's
`trend` sensor), whether the charger saw any input in the last 24 h, and whether fast
sampling ran overnight (which makes the slope mean nothing about the pack).

The scenarios below are real nights where they can be (open question #17):
  2026-09-24/25, rail switching:  4.187 V, -0.51 mV/h  -> ~1.7 mA  -> healthy
  2026-09-22/23, radar stuck on:  3.985 V, -10.82 mV/h -> ~37 mA   -> unhealthy (drain)

The pack's capacity is a UI number (input_number.creek_node_pack_capacity_mah) because the
pack may be swapped for a different one; every formula that needs capacity reads it.

Run: python rate_of_rise/tests/test_pack_health.py
"""
import ast
import re
import sys
from pathlib import Path

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[2]
HEALTH = ROOT / "ha-packages" / "creek_node_health.yaml"
POWER_SNIPPET = ROOT / "code-snippets" / "creek_node_power_24h.yaml"

BATTERY = "sensor.creek_gateway_creek_node_battery"
PREDAWN = "sensor.creek_node_battery_predawn"
MAX_24H = "sensor.creek_node_battery_max_24h"
FAST = "sensor.creek_node_fast_sampling_tonight"
TREND = "binary_sensor.creek_node_pack_trend"
CAPACITY = "input_number.creek_node_pack_capacity_mah"
QUIET_MA = "input_number.creek_node_avg_current_ma"
HEALTH_SENSOR = "sensor.creek_node_pack_health"
REPLACE_NOW = "binary_sensor.creek_node_pack_replace_now"


def _doc():
    return yaml.safe_load(HEALTH.read_text(encoding="utf-8"))


def _health_block():
    for block in _doc()["template"]:
        for s in block.get("sensor") or []:
            if s.get("unique_id") == "creek_node_pack_health":
                return block, s
    raise AssertionError("no creek_node_pack_health sensor in creek_node_health.yaml")


def _native(text):
    """What HA does with a rendered variable: literal_eval when it parses, else a string."""
    text = text.strip()
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text


def mv_per_h(x):
    """Trend's gradient attribute is in source units (V) per second."""
    return -x / 1000 / 3600


NIGHT_AFTER_FIX = {
    PREDAWN: "4.187", MAX_24H: "4.406", FAST: "0.0", CAPACITY: "6000", QUIET_MA: "2.0",
    TREND: {"gradient": mv_per_h(0.51), "sample_count": 120},
}


def evaluate(**over):
    """Render the 04:30 verdict the way HA does: variables in order, then state/attributes."""
    world = {**NIGHT_AFTER_FIX, **over}
    block, sensor = _health_block()
    env = jinja2.Environment()
    ctx = {
        "states": lambda e: world[e] if isinstance(world.get(e), str) else "unknown",
        "state_attr": lambda e, a: (world.get(e) or {}).get(a)
        if isinstance(world.get(e), dict) else None,
        "is_state": lambda e, v: world.get(e) == v,
    }
    for name, tmpl in (block.get("variables") or {}).items():
        ctx[name] = _native(env.from_string(str(tmpl)).render(**ctx))
    state = env.from_string(sensor["state"]).render(**ctx).strip()
    attrs = {k: _native(env.from_string(str(v)).render(**ctx))
             for k, v in (sensor.get("attributes") or {}).items()}
    return state, attrs


# --- the verdict -----------------------------------------------------------------------

def test_the_first_night_after_the_radar_fix_is_healthy():
    state, attrs = evaluate()
    assert state == "healthy", attrs
    assert 1.5 <= attrs["implied_ma"] <= 2.0, attrs
    assert attrs["slope_skipped"] is False


def test_the_radar_stuck_on_nights_read_unhealthy_and_say_why():
    state, attrs = evaluate(**{PREDAWN: "3.985",
                               TREND: {"gradient": mv_per_h(10.82), "sample_count": 150}})
    assert state == "unhealthy", attrs
    assert 34 <= attrs["implied_ma"] <= 40, attrs
    assert "SHDN" in attrs["reason"], attrs["reason"]


def test_a_pack_that_had_charge_input_and_still_did_not_refill_is_unhealthy():
    state, attrs = evaluate(**{PREDAWN: "3.80"})
    assert state == "unhealthy", attrs
    assert "refill" in attrs["reason"], attrs["reason"]


def test_a_flat_pack_is_replace_now():
    assert evaluate(**{PREDAWN: "3.45"})[0] == "replace now"


def test_an_overcharged_pack_is_replace_now():
    """Above 4.25 V at night is the cell itself (the panel is dark): a charger fault."""
    assert evaluate(**{PREDAWN: "4.27"})[0] == "replace now"


def test_fast_sampling_overnight_skips_the_slope_rather_than_blaming_the_pack():
    """Fast mode holds the radar rail up (~36 mA), so that night's slope says nothing
    about the pack."""
    state, attrs = evaluate(**{FAST: "0.25",
                               TREND: {"gradient": mv_per_h(10.8), "sample_count": 150}})
    assert state == "healthy", attrs
    assert attrs["slope_skipped"] is True
    assert attrs["implied_ma"] is None


def test_too_few_samples_skip_the_slope():
    """Trend keeps its samples in memory only, so an HA restart in the night leaves a
    short, meaningless window."""
    state, attrs = evaluate(**{TREND: {"gradient": mv_per_h(10.8), "sample_count": 10}})
    assert state == "healthy", attrs
    assert attrs["slope_skipped"] is True


def test_the_same_slope_means_more_current_on_a_bigger_pack():
    slope = {"gradient": mv_per_h(1.2), "sample_count": 120}
    small, small_attrs = evaluate(**{CAPACITY: "3000", TREND: slope})
    big, big_attrs = evaluate(**{CAPACITY: "12000", TREND: slope})
    assert abs(big_attrs["implied_ma"] - 4 * small_attrs["implied_ma"]) <= 0.3, \
        (small_attrs, big_attrs)
    assert small == "healthy", small_attrs
    assert big == "unhealthy", big_attrs


def test_the_cold_cutoff_holding_off_charge_is_not_a_pack_fault():
    """The KSD9700 opens below 5 degC, so a winter pack sits a little lower each morning
    with no charger input at all. That is the cutoff working, not the pack failing."""
    state, attrs = evaluate(**{PREDAWN: "3.85", MAX_24H: "4.18"})
    assert state == "healthy", attrs
    assert "cutoff" in attrs["reason"], attrs["reason"]


def test_no_charge_input_with_little_reserve_left_is_unhealthy():
    state, attrs = evaluate(**{PREDAWN: "3.70", MAX_24H: "3.72", CAPACITY: "1500"})
    assert state == "unhealthy", attrs
    assert attrs["reserve_days"] < 14, attrs
    assert "swap" in attrs["reason"], attrs["reason"]


def test_reserve_days_follow_the_capacity_setting():
    """At the quiet-day draw, twice the pack is twice the days. (With a measured slope the
    same slope on a bigger pack is a bigger current, and the days come out the same.)"""
    no_slope = {FAST: "0.25"}
    _, six = evaluate(**no_slope)
    _, twelve = evaluate(**no_slope, **{CAPACITY: "12000"})
    assert abs(twelve["reserve_days"] - 2 * six["reserve_days"]) <= 1, (six, twelve)


def test_no_night_data_is_unknown_not_a_guess():
    assert evaluate(**{PREDAWN: "unavailable"})[0] == "unknown"


def test_a_fresh_capacity_number_reads_as_the_as_built_pack():
    """A new input_number with no `initial:` starts at its minimum. 0 means "not set", so
    the first reload does not judge the pack as a 500 mAh one."""
    assert evaluate(**{CAPACITY: "0.0"}) == evaluate()


def test_missing_helpers_fall_back_to_the_measured_pack():
    """Before the capacity number is first set, or without the optional power package,
    the formulas use the as-built 6 Ah pack and the ~2 mA quiet-day draw."""
    state, attrs = evaluate(**{CAPACITY: "unknown", QUIET_MA: "unavailable"})
    assert state == "healthy", attrs
    assert 1.5 <= attrs["implied_ma"] <= 2.0, attrs


# --- how the inputs are gathered ----------------------------------------------------------

def test_the_verdict_is_taken_once_a_day_before_dawn():
    """Only the dark hours read the cell; any daytime evaluation would judge the charger's
    OUT rail as if it were the pack."""
    block, _ = _health_block()
    triggers = block["triggers"]
    assert [t["trigger"] for t in triggers] == ["time"], triggers
    assert triggers[0]["at"] == "04:30:00"


def test_the_slope_window_stays_inside_the_dark_hours():
    trends = {key: cfg for p in _platforms("binary_sensor", "trend")
              for key, cfg in p["sensors"].items()}
    t = trends["creek_node_pack_trend"]
    assert t["entity_id"] == BATTERY
    assert t["sample_duration"] <= 4.5 * 3600, "window would reach back before midnight"
    assert t["max_samples"] >= 500, "a 4.5 h night at ~60 changes/h would be truncated"


def test_the_capacity_number_survives_a_restart():
    """`initial:` would reset a UI-set capacity every time HA restarts."""
    cap = _doc()["input_number"]["creek_node_pack_capacity_mah"]
    assert "initial" not in cap
    assert cap["unit_of_measurement"] == "mAh"
    assert cap["min"] == 0, "0 is the 'not set yet' value the formulas map to 6000"
    assert cap["max"] >= 20000


def test_replace_now_is_its_own_binary_sensor_for_the_push():
    sensors = {s["unique_id"]: s for block in _doc()["template"]
               for s in block.get("binary_sensor") or []}
    tmpl = sensors["creek_node_pack_replace_now"]["state"]
    env = jinja2.Environment()
    for health, expected in (("replace now", "True"), ("unhealthy", "False"),
                             ("healthy", "False"), ("unknown", "False")):
        got = env.from_string(tmpl).render(
            is_state=lambda e, v, h=health: e == HEALTH_SENSOR and v == h).strip()
        assert got == expected, (health, got)


def test_replace_now_reaches_a_phone_but_never_as_critical():
    autos = {a["id"]: a for a in _doc()["automation"]}
    push = autos["creek_data_watchdog_push"]
    trig = [t for t in push["triggers"]
            if REPLACE_NOW in (t["entity_id"] if isinstance(t["entity_id"], list)
                               else [t["entity_id"]])]
    assert trig and all(t["to"] == "on" and t["id"] != "gauge" for t in trig), trig
    assert REPLACE_NOW in push["variables"]["advice"]
    assert "creek_node_pack_health" in push["variables"]["detail"], "push should carry the reason"
    assert REPLACE_NOW in autos["creek_data_watchdog_clear"]["triggers"][0]["entity_id"]


def test_the_power_package_reads_capacity_from_the_number_too():
    text = POWER_SNIPPET.read_text(encoding="utf-8")
    assert CAPACITY in text
    assert not re.search(r"/\s*6000\b", text), "pack % still divides by a fixed 6000"


def _platforms(domain, platform):
    return [s for s in _doc().get(domain) or [] if s.get("platform") == platform]


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
