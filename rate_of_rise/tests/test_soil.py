"""Plain-assert tests for the soil-moisture features: the ponding flag and probe slots.

2026-09-28: the willow-tree WH51 failed and was deleted from soil_moisture_entities. The
field probe read 81 % with water standing on the low ground, and *Creek Soil Ponding* said
dry -- the threshold was a fixed 85 %. Deleting the willow entry also slid the field probe
into slot [0], so it was published and recorded as near_house_pct.

Run: python rate_of_rise/tests/test_soil.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Config          # noqa: E402
from app.features import FeatureBuilder  # noqa: E402

WILLOW = "sensor.outside_weather_station_soil_moisture_willow"
FIELD = "sensor.outside_weather_station_soil_moisture_field"


class FakeHA:
    def __init__(self, soils):
        self.soils = soils

    def get_float_with_age(self, entity_id):
        return 1.0, 30.0

    def get_float(self, entity_id):
        if entity_id == "":
            raise AssertionError("a blank slot must not be looked up")
        return self.soils.get(entity_id)

    def get_bool(self, entity_id):
        return True

    def get_unit(self, entity_id):
        return None


def build(soils, entities, **overrides):
    cfg = Config(soil_moisture_entities=entities, onsite_temp_entity=None, **overrides)
    return FeatureBuilder(cfg, FakeHA(soils), now_fn=lambda: 1_790_000_000.0).build()


def test_the_reading_seen_with_water_standing_is_ponding():
    row = build({FIELD: 81.0}, ["", FIELD])
    assert row.ponding_flag is True


def test_ordinary_wet_ground_is_not_ponding():
    row = build({FIELD: 70.0}, ["", FIELD])
    assert row.ponding_flag is False


def test_the_threshold_is_an_option():
    assert build({FIELD: 81.0}, ["", FIELD], ponding_saturation_pct=85.0).ponding_flag is False
    assert build({FIELD: 72.0}, ["", FIELD], ponding_saturation_pct=70.0).ponding_flag is True


def test_a_blank_slot_keeps_the_other_probe_on_its_own_label():
    row = build({FIELD: 81.0}, ["", FIELD])
    assert row.soil_moisture_near_house_pct is None
    assert row.soil_moisture_near_creek_pct == 81.0
    assert row.soil_moisture_mean_pct == 81.0      # the blank does not drag the mean


def test_both_probes_still_work():
    row = build({WILLOW: 60.0, FIELD: 80.0}, [WILLOW, FIELD])
    assert row.soil_moisture_near_house_pct == 60.0
    assert row.soil_moisture_near_creek_pct == 80.0
    assert row.soil_moisture_mean_pct == 70.0
    assert row.ponding_flag is True                # any one probe is enough


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
