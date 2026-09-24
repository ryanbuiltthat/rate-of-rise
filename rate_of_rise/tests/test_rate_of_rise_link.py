"""The creek node's radio link, and what a dropout — or a noisy or faulty sensor — must not
be allowed to look like.

Three field failures these guard against:

  * 2026-09-13: the node went quiet for two hours, and the first reading back was
    differenced against the last one from before the outage. The whole outage's level
    change was charged to one loop interval, read as 0.177 in/min, and fired a Tier 3
    Warning — a critical, alarm-stream push (ha-packages/creek_warning.yaml,
    `critical_from_tier: 2`) for a creek that was not doing anything unusual.
  * 2026-09-21/22: rate of rise was taken between the last value *change* before each poll,
    which could be one node report (~63 s) apart. The node reports whole millimetres and a
    still creek flickers 1-2 mm, so 1 mm read as 0.0375 in/min and 2 mm as 0.075 in/min —
    over the 0.05 in/min Warning. Two such 2 mm steps happened; both happened to be falls.
  * 2026-09-17: a cold radar answered 0, the gateway clamped it to the range ceiling
    (3.13 ft), and stage alone raised Tier 4 Emergency on a dry day. It was the first
    reading after a 24-minute dropout.

Run: python rate_of_rise/tests/test_rate_of_rise_link.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Config          # noqa: E402
from app.features import FeatureBuilder  # noqa: E402
from app.health import HealthTracker   # noqa: E402
from app.tiers import compute_tier     # noqa: E402

PACKETS = "sensor.outside_creek_gateway_creek_node_packets"
CLAMP_FT = 955 / 304.8          # the gateway's blanking-zone ceiling at the 1105 mm mount


class FakeHA:
    """Stands in for HAClient: a stage reading with an age, a link sensor, and the
    gateway's packet counter (fresh while the node is online, stale otherwise)."""

    def __init__(self, stage=1.00, age_s=30.0, online=True):
        self.report(stage, age_s, online)

    def report(self, stage, age_s=30.0, online=True, packets_age_s=None):
        self.stage, self.age_s, self.online = stage, age_s, online
        if packets_age_s is None:
            packets_age_s = 30.0 if online is not False else 900.0
        self.packets_age_s = packets_age_s

    def get_float_with_age(self, entity_id):
        if entity_id == PACKETS:
            return 1234.0, self.packets_age_s
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


def build_at(fb, clock, ha, stage, age_s=30.0, online=True, minutes=None, **kw):
    if minutes is not None:
        clock.advance(minutes)
    ha.report(stage, age_s, online, **kw)
    return fb.build()


def settle(fb, clock, ha, stage=1.00, loops=3):
    """A few ordinary 5-minute loops on a still creek, so a rate window exists."""
    row = None
    for _ in range(loops):
        row = build_at(fb, clock, ha, stage, minutes=5.0)
    return row


# --- the rate window ------------------------------------------------------------------

def test_a_rise_is_measured_across_the_window_not_one_interval():
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock)
    assert build_at(fb, clock, ha, 1.00).rate_of_rise_in_min is None     # seeds
    row = build_at(fb, clock, ha, 1.01, minutes=5.0)
    assert row.rate_of_rise_in_min is None, "5 min of history is not a 10 min window"
    row = build_at(fb, clock, ha, 1.02, minutes=5.0)                       # 0.24 in / 10 min
    assert abs(row.rate_of_rise_in_min - 0.024) < 1e-6, row.rate_of_rise_in_min
    assert row.creek_node_online is True


def test_a_two_millimetre_flicker_never_reaches_the_warning_rate():
    """The 2026-09-21/22 regression. 292 <-> 294 mm is exactly the step that read as
    0.075 in/min when it landed one report apart."""
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock)
    low, high = 292 / 304.8, 294 / 304.8
    rates = []
    for i in range(24):                                   # two hours of 5-minute loops
        # The field timing: one poll finds a change 5 s old, the next finds the following
        # change already 237 s old, so the two readings are 300 - 237 + 5 = 68 s apart —
        # one node report. That is what turned 2 mm into 0.069 in/min.
        row = build_at(fb, clock, ha, high if i % 2 else low,
                       age_s=237.0 if i % 2 else 5.0, minutes=5.0)
        if row.rate_of_rise_in_min is not None:
            rates.append(row.rate_of_rise_in_min)
        assert compute_tier(row, 0.0)[0] < 3, (i, row.rate_of_rise_in_min)
    assert rates and max(abs(r) for r in rates) < 0.01, rates


def test_a_steady_creek_on_a_live_link_reads_zero_not_unknown():
    """HA only moves last_updated when the value changes, so an unchanged reading ages
    while the node keeps reporting. With the link up that is a flat creek — rate 0 — not
    a stale one."""
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock)
    build_at(fb, clock, ha, 1.00, age_s=30.0)
    for n in range(1, 4):
        row = build_at(fb, clock, ha, 1.00, age_s=30.0 + n * 300.0, minutes=5.0)
    assert row.rate_of_rise_in_min == 0.0
    row = build_at(fb, clock, ha, 1.01, age_s=20.0, minutes=5.0)
    assert row.rate_of_rise_in_min is not None and row.rate_of_rise_in_min > 0


# --- dropouts -------------------------------------------------------------------------

def test_the_first_reading_after_a_dropout_never_produces_a_rate():
    """The reported false alarm, reproduced: 40 min offline, 3 in of real rise.

    Naively differenced that is 0.6 in/min — twelve times the Tier 3 threshold — from a
    creek that rose at 0.075 in/min. The rate must be withheld, not merely damped.
    """
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock)
    settle(fb, clock, ha)

    # Offline: the gateway keeps serving that same stale 1.00 ft, ageing as it goes.
    for _ in range(8):
        row = build_at(fb, clock, ha, 1.00, age_s=600.0, online=False, minutes=5.0)
        assert row.rate_of_rise_in_min is None
        assert row.creek_node_online is False

    # Link back, water genuinely 3 in higher than 40 min ago.
    row = build_at(fb, clock, ha, 1.25, age_s=30.0, online=True, minutes=1.0)
    assert row.rate_of_rise_in_min is None, row.rate_of_rise_in_min
    assert row.rate_of_rise_sample_count == 0
    assert row.stage_implausible is False, "3 in over 40 min is a creek, not a fault"
    assert compute_tier(row, 0.0)[0] == 0


def test_a_real_rise_still_alarms_once_the_window_refills():
    """Suppression is bounded: the creek gets its Warning as soon as it earns one."""
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock)
    settle(fb, clock, ha)
    build_at(fb, clock, ha, 1.00, age_s=600.0, online=False, minutes=30.0)
    build_at(fb, clock, ha, 1.25, online=True, minutes=1.0)       # re-seed only
    build_at(fb, clock, ha, 1.30, minutes=5.0)                    # window not yet spanned

    row = build_at(fb, clock, ha, 1.35, minutes=5.0)              # 0.12 in/min, 1st rate
    assert row.rate_of_rise_in_min > 0.05
    assert compute_tier(row, 0.0)[0] == 0, "one post-reconnect rate is not confirmation"

    row = build_at(fb, clock, ha, 1.40, minutes=5.0)              # 2nd consecutive rate
    tier, label, reasons = compute_tier(row, 0.0)
    assert (tier, label) == (3, "Warning"), (tier, reasons)
    assert "rising" in reasons[0]


def test_a_long_gap_with_the_link_up_is_still_a_gap():
    """Covers the add-on itself being down (or a slow loop) rather than the radio."""
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock)
    settle(fb, clock, ha)
    row = build_at(fb, clock, ha, 1.30, minutes=45.0)
    assert row.rate_of_rise_in_min is None


def test_max_gap_is_configurable():
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock, rate_of_rise_max_gap_minutes=30.0)
    build_at(fb, clock, ha, 1.00)
    row = build_at(fb, clock, ha, 1.10, minutes=20.0)
    assert row.rate_of_rise_in_min is not None, "20 min is inside a 30 min budget"


def test_without_a_link_sensor_age_alone_suppresses_the_rate():
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock, creek_node_status_entity=None, creek_node_packets_entity=None)
    build_at(fb, clock, ha, 1.00)
    row = build_at(fb, clock, ha, 1.25, age_s=2400.0, minutes=40.0)
    assert row.rate_of_rise_in_min is None
    assert row.stage_age_min == 40.0


def test_a_frozen_node_status_is_overruled_by_a_stopped_packet_counter():
    """A gateway that stops publishing leaves node_status frozen at `on` — it only writes
    on a transition. The packet counter changes on every report, so when it stops moving
    the link is down whatever the status sensor still says."""
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock)
    settle(fb, clock, ha)
    row = build_at(fb, clock, ha, 1.00, age_s=900.0, online=True, packets_age_s=900.0,
                   minutes=5.0)
    assert row.creek_node_online is False
    assert row.rate_of_rise_in_min is None


def test_the_packet_counter_alone_can_vouch_for_the_link():
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock, creek_node_status_entity=None)
    row = build_at(fb, clock, ha, 1.00, online=None, packets_age_s=40.0)
    assert row.creek_node_online is True


# --- impossible readings ----------------------------------------------------------------

def test_a_cold_radar_clamp_is_withheld_not_an_emergency():
    """The 2026-09-17 Tier 4, reproduced on a live link."""
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock)
    settle(fb, clock, ha, stage=0.93)
    row = build_at(fb, clock, ha, CLAMP_FT, minutes=5.0)
    assert row.stage_ft is None and row.stage_implausible is True
    assert abs(row.stage_raw_ft - CLAMP_FT) < 1e-9, "the raw reading is still on record"
    assert row.rate_of_rise_in_min is None
    assert compute_tier(row, 0.0)[0] == 0

    row = build_at(fb, clock, ha, 0.93, minutes=5.0)            # the next reading is sane
    assert row.stage_ft == 0.93 and row.stage_implausible is False


def test_the_clamp_right_after_a_dropout_is_still_withheld():
    """The actual 2026-09-17 sequence: the node was silent 12:32-12:56 and its first
    packet back carried the clamp. Trusting whatever arrives after a reconnect would have
    let it straight through."""
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock)
    settle(fb, clock, ha, stage=0.93)
    for _ in range(5):
        build_at(fb, clock, ha, 0.93, age_s=900.0, online=False, minutes=5.0)
    row = build_at(fb, clock, ha, CLAMP_FT, online=True, minutes=1.0)
    assert row.stage_implausible is True
    assert compute_tier(row, 0.0)[0] == 0


def test_a_jump_that_persists_is_believed_after_thirty_minutes():
    """A creek can genuinely outrun the budget — a big rise while the radio was down.
    Withholding it forever would blind Warning/Emergency in exactly that flood."""
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock)
    settle(fb, clock, ha, stage=0.93)
    for _ in range(6):                                          # 25 min of rejections
        row = build_at(fb, clock, ha, 3.0, minutes=5.0)
        assert row.stage_implausible is True
    row = build_at(fb, clock, ha, 3.0, minutes=5.0)             # 30 min: believed
    assert row.stage_implausible is False and row.stage_ft == 3.0
    assert compute_tier(row, 0.0)[0] == 4


def test_a_fast_but_possible_rise_is_accepted():
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock)
    settle(fb, clock, ha, stage=0.93)
    row = build_at(fb, clock, ha, 0.93 + 8 / 12, minutes=5.0)   # 8 in in 5 min
    assert row.stage_implausible is False


def test_a_fall_is_never_rejected():
    ha = FakeHA()
    clock = Clock()
    fb = builder(ha, clock)
    settle(fb, clock, ha, stage=2.0)
    row = build_at(fb, clock, ha, 0.9, minutes=5.0)
    assert row.stage_implausible is False and row.stage_ft == 0.9


# --- watchdogs and old rows -------------------------------------------------------------

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
