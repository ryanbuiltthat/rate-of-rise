"""Pure parts of the cutover: pairing v1/v2 entities and rewriting the entity map.

Run: python tools/test_gateway_cutover.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gateway_cutover import pair_entities, production_map  # noqa: E402


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


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
