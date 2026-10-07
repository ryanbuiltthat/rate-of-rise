"""Pure parts of the cutover: pairing v1/v2 entities and rewriting the entity map.

Run: python tools/test_gateway_cutover.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gateway_cutover import (apply_plan, device_entities, new_addon_options,  # noqa: E402
                             pair_entities, production_map)


def ent(entity_id, name):
    return {"entity_id": entity_id, "original_name": name}


def test_pairs_by_domain_and_original_name():
    v1 = [ent("sensor.creek_gateway_stage", "Stage"),
          ent("sensor.outside_creek_gateway_sensor_distance", "Sensor Distance"),
          ent("binary_sensor.creek_gateway_creek_node_status", "Creek Node Status"),
          ent("sensor.creek_gateway_uptime", "Uptime")]
    v2 = [ent("sensor.creek_gateway_v2_stage", "Stage"),
          ent("sensor.creek_gateway_v2_sensor_distance", "Sensor Distance"),
          ent("binary_sensor.creek_gateway_v2_creek_node_status", "Creek Node Status"),
          ent("sensor.creek_gateway_v2_uptime", "Uptime"),
          ent("sensor.creek_gateway_v2_store_free_space", "Store Free Space")]
    pairs, only_v1, only_v2 = pair_entities(v1, v2)
    assert ("sensor.creek_gateway_stage", "sensor.creek_gateway_v2_stage") in pairs
    assert ("sensor.outside_creek_gateway_sensor_distance",
            "sensor.creek_gateway_v2_sensor_distance") in pairs
    assert len(pairs) == 4 and only_v1 == []
    assert only_v2 == ["sensor.creek_gateway_v2_store_free_space"]


def test_production_map_swaps_v2_ids_for_v1_ids():
    m = {"node": {"stage_ft": "sensor.creek_gateway_v2_stage"},
         "ecowitt": {"rain_total_in": "sensor.outside_weather_station_rain_total"}}
    out = production_map(m, [("sensor.creek_gateway_stage", "sensor.creek_gateway_v2_stage")])
    assert out == {"node": {"stage_ft": "sensor.creek_gateway_stage"},
                   "ecowitt": {"rain_total_in": "sensor.outside_weather_station_rain_total"}}


PAIRS = [("sensor.creek_gateway_stage", "sensor.creek_gateway_v2_stage")]


def opts(entity_map, shadow, **extra):
    return {"backfill_entity_map": json.dumps(entity_map),
            "backfill_shadow_map": json.dumps(shadow) if shadow is not None else "", **extra}


def test_options_empty_shadow_keeps_existing_ecowitt():
    out = new_addon_options(opts({"ecowitt": {"a": "sensor.a"}}, None), PAIRS)
    assert json.loads(out["backfill_entity_map"])["ecowitt"] == {"a": "sensor.a"}


def test_options_shadow_only_ecowitt_added():
    out = new_addon_options(opts({"node": {}}, {"ecowitt": {"b": "sensor.b"}}), PAIRS)
    assert json.loads(out["backfill_entity_map"])["ecowitt"] == {"b": "sensor.b"}


def test_options_overlap_takes_shadow_value():
    out = new_addon_options(
        opts({"ecowitt": {"a": "sensor.old", "k": "sensor.k"}}, {"ecowitt": {"a": "sensor.new"}}), PAIRS)
    assert json.loads(out["backfill_entity_map"])["ecowitt"] == {"a": "sensor.new", "k": "sensor.k"}


def test_options_node_ids_swapped_and_shadow_cleared():
    out = new_addon_options(opts({"node": {"stage_ft": "sensor.creek_gateway_v2_stage"}}, None), PAIRS)
    assert json.loads(out["backfill_entity_map"])["node"] == {"stage_ft": "sensor.creek_gateway_stage"}
    assert out["backfill_shadow_map"] == ""


def test_options_other_options_pass_through():
    out = new_addon_options(opts({}, None, log_level="debug", port=1), PAIRS)
    assert out["log_level"] == "debug" and out["port"] == 1


def test_options_malformed_map_raises():
    try:
        new_addon_options({"backfill_entity_map": "{oops"}, PAIRS)
    except ValueError as e:
        assert "backfill_entity_map" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_device_entities_rejects_duplicate_names():
    devs = [{"id": "1", "name": "X"}, {"id": "2", "name_by_user": "X"}]
    try:
        device_entities(devs, [], "X")
    except SystemExit as e:
        assert "2 devices" in str(e)
    else:
        raise AssertionError("expected SystemExit")


class FakeHA:
    def __init__(self, fail_renames=0, always_fail=False, linger_polls=0):
        self.calls, self.deleted = [], []
        self.fail_renames, self.always_fail, self.linger = fail_renames, always_fail, linger_polls

    def delete_entry(self, entry_id):
        self.deleted.append(entry_id)

    def call(self, msg):
        self.calls.append(msg)
        t = msg["type"]
        if t == "get_states":
            if self.linger > 0:
                self.linger -= 1
                return [{"entity_id": "sensor.creek_gateway_stage", "state": "unavailable"}]
            return []
        if t == "config/entity_registry/list":
            return []
        if t == "config/entity_registry/update":
            if self.always_fail or self.fail_renames > 0:
                self.fail_renames -= 1
                raise SystemExit("update failed: boom")
        return None


def make_plan():
    return {"entry_ids": ["e1"],
            "pairs": PAIRS + [("sensor.creek_gateway_uptime", "sensor.creek_gateway_v2_uptime")],
            "v1_ids": ["sensor.creek_gateway_stage"], "slug": "abc_rate",
            "options": {"backfill_entity_map": "{}", "backfill_shadow_map": ""}}


def test_apply_retries_rename_and_waits_for_ids():
    ha, sleeps, lines = FakeHA(fail_renames=1, linger_polls=2), [], []
    rc = apply_plan(ha, make_plan(), sleep=sleeps.append, out=lines.append)
    assert rc == 0 and ha.deleted == ["e1"]
    assert sleeps == [1, 1, 2]  # two polls waited, then one rename retry
    renames = [c for c in ha.calls if c["type"] == "config/entity_registry/update"]
    assert len(renames) == 3  # first (fails), retry, second pair
    assert ha.calls[-1]["type"] == "supervisor/api"


def test_apply_failure_reports_remaining_steps():
    ha, lines = FakeHA(always_fail=True), []
    rc = apply_plan(ha, make_plan(), sleep=lambda s: None, out=lines.append)
    text = "\n".join(lines)
    assert rc == 1
    assert "removed v1 config entry e1" in text
    assert "rename sensor.creek_gateway_v2_stage -> sensor.creek_gateway_stage" in text
    assert "rename sensor.creek_gateway_v2_uptime -> sensor.creek_gateway_uptime" in text
    assert "/addons/abc_rate/options" in text and "backfill_entity_map" in text
    assert not any(c["type"] == "supervisor/api" for c in ha.calls)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
