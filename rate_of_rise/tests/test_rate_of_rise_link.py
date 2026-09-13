"""The creek node's radio link, and what a dropout must not be allowed to look like.

The bug these guard against, observed in the field: the node went quiet, the gateway was
moved to recover the link, and the first reading back was differenced against the last one
from before the outage. The whole outage's worth of level change got charged to a single
loop interval, which read as a huge rate of rise and fired a Tier 3 Warning — a critical,
alarm-stream push (ha-packages/creek_warning.yaml, `critical_from_tier: 2`) for a creek
that was not doing anything unusual.

Run: python rate_of_rise/tests/test_rate_of_rise_link.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Config          # noqa: E402
from app.features import FeatureBuilder  # noqa: E402
from app.health import HealthTracker   # noqa: E402
from app.tiers import compute_tier     # noqa: E402


class FakeHA:
    """Stands in for HAClient: a stage reading with an age, plus a link sensor."""

    def __init__(self, stage=1.00, age_s=30.0, online=True):
        self.stage, self.age_s, self.online = stage, age_s, online

    def report(self, stage, age_s=30.0, online=True):
        self.stage, self.age_s, self.online = stage, age_s, online

    def get_float_with_age(self, entity_id):
        return self.stage, self.age_s

    def get_float(self, entity_id):
        return None

    def get_bool(self, entity_id):
        return self.online

    def get_unit(self, entity_id):
        return None


def builder(ha, clock, **overrides):
    cfg = Config(soil_moisture_entities=[], onsite_temp_entity=None, **overrides)
    return FeatureBuilder(cfg, ha, now_fn=clock)


class Clock:
    """Monotonic stand-in for time.time() inside FeatureBuilder.build()."""

    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, minutes):
        self.t += minutes * 60.0
        return self


def build_at(fb, clock, ha, stage, age_s=30.0, online=True, minutes=None):
    if minutes is not None:
        clock.advance(minutes)
    ha.report(stage, age_s, online)
    return fb.build()


def test_a_normal_interval_still_yields_a_rate():
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock)
    build_at(fb, clock, ha, 1.00)                        # seeds the baseline
    row = build_at(fb, clock, ha, 1.02, minutes=1.0)     # +0.24 in over 1 min
    assert abs(row.rate_of_rise_in_min - 0.24) < 1e-6, row.rate_of_rise_in_min
    assert row.creek_node_online is True


def test_the_first_reading_after_a_dropout_never_produces_a_rate():
    """The reported false alarm, reproduced: 40 min offline, 3 in of real rise.

    Naively differenced that is 0.6 in/min — twelve times the Tier 3 threshold — from a
    creek that rose at 0.075 in/min. The rate must be withheld, not merely damped.
    """
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock)
    build_at(fb, clock, ha, 1.00)                        # last good reading before the drop

    # Offline: the gateway keeps serving that same stale 1.00 ft, ageing as it goes.
    for _ in range(8):
        row = build_at(fb, clock, ha, 1.00, age_s=600.0, online=False, minutes=5.0)
        assert row.rate_of_rise_in_min is None
        assert row.creek_node_online is False

    # Link back, water genuinely 3 in higher than 40 min ago.
    row = build_at(fb, clock, ha, 1.25, age_s=30.0, online=True, minutes=1.0)
    assert row.rate_of_rise_in_min is None, row.rate_of_rise_in_min
    assert row.rate_of_rise_sample_count == 0
    assert compute_tier(row, 0.0)[0] == 0


def test_a_real_rise_still_alarms_two_samples_after_the_link_returns():
    """Suppression is bounded: the creek gets its Warning as soon as it earns one."""
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock)
    build_at(fb, clock, ha, 1.00)
    build_at(fb, clock, ha, 1.00, age_s=600.0, online=False, minutes=30.0)
    build_at(fb, clock, ha, 1.25, online=True, minutes=1.0)      # re-seed only

    row = build_at(fb, clock, ha, 1.26, minutes=1.0)             # 0.12 in/min, 1st sample
    assert row.rate_of_rise_in_min > 0.05
    assert compute_tier(row, 0.0)[0] == 0, "one post-reconnect sample is not confirmation"

    row = build_at(fb, clock, ha, 1.27, minutes=1.0)             # 2nd consecutive sample
    tier, label, reasons = compute_tier(row, 0.0)
    assert (tier, label) == (3, "Warning"), (tier, reasons)
    assert "rising" in reasons[0]


def test_a_long_gap_with_the_link_up_is_still_a_gap():
    """Covers the add-on itself being down (or a slow loop) rather than the radio."""
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock)
    build_at(fb, clock, ha, 1.00)
    row = build_at(fb, clock, ha, 1.30, minutes=45.0)
    assert row.rate_of_rise_in_min is None


def test_max_gap_is_configurable():
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock, rate_of_rise_max_gap_minutes=30.0)
    build_at(fb, clock, ha, 1.00)
    row = build_at(fb, clock, ha, 1.10, minutes=20.0)
    assert row.rate_of_rise_in_min is not None, "20 min is inside a 30 min budget"


def test_a_steady_creek_on_a_live_link_reads_zero_not_unknown():
    """The value only changes when the water moves, so an unchanged reading with the node
    still reporting is a flat creek — not a dead sensor."""
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock)
    build_at(fb, clock, ha, 1.00, age_s=30.0)
    # Same reading, now 20 min old: HA never wrote a new state because nothing changed.
    row = build_at(fb, clock, ha, 1.00, age_s=1230.0, online=True, minutes=20.0)
    assert row.rate_of_rise_in_min == 0.0
    # ...and the baseline moved forward, so the next real change is not read as a gap.
    row = build_at(fb, clock, ha, 1.01, age_s=30.0, minutes=1.0)
    assert row.rate_of_rise_in_min is not None


def test_without_a_link_sensor_age_alone_suppresses_the_rate():
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock, creek_node_status_entity=None)
    build_at(fb, clock, ha, 1.00)
    row = build_at(fb, clock, ha, 1.25, age_s=2400.0, minutes=40.0)
    assert row.rate_of_rise_in_min is None
    assert row.stage_age_min == 40.0


def test_stage_stale_watchdog_sees_a_dead_link_holding_a_stale_number():
    """It used to sit at OK through the whole dropout: stage was never None."""
    clock = Clock()
    h = HealthTracker(now_fn=lambda: clock.t)

    class Row:
        stage_ft = 1.00
        soil_moisture_mean_pct = 50.0
        rain_rate_in_hr = 0.0
        creek_node_online = True
        stage_age_min = 0.5

    row = Row()
    h.evaluate(row, {}, set())
    row.creek_node_online = False
    clock.advance(31)                       # threshold is 1800 s
    assert h.evaluate(row, {}, set())["stage_stale"] is True


def test_rows_without_the_new_fields_behave_as_before():
    """Old dataset rows and hand-built rows carry no sample count; absence of evidence of
    a dropout is not evidence of one, so the rate is trusted exactly as it used to be."""
    from app.features import FeatureRow
    row = FeatureRow(ts=0.0, stage_ft=None, rate_of_rise_in_min=0.06,
                     soil_moisture_mean_pct=None, soil_moisture_near_house_pct=None,
                     soil_moisture_near_creek_pct=None, ponding_flag=False)
    assert row.rate_of_rise_sample_count is None
    assert compute_tier(row, 0.0)[0] == 3


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
