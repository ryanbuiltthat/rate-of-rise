"""Plain-assert tests for the threshold estimate's stage term (0.28.4).

Run: python rate_of_rise/tests/test_threshold_estimate.py

The estimate read rate of rise alone, so a creek at 2.9 ft on a slow rise showed ~0 %
while the stage rules had it at Emergency. The stage term fixes the number. The tier is
what alerts, and these tests pin that the term never changes it.
"""
import sys
import tempfile
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Config  # noqa: E402
from app.features import FeatureRow  # noqa: E402
from app.model import Model, stage_probability  # noqa: E402
from app.registry import ModelRegistry  # noqa: E402
from app.tiers import (BANK_TOP_FT, EMERGENCY_PROBABILITY, EMERGENCY_STAGE_FT,  # noqa: E402
                       WARNING_PROBABILITY, WARNING_STAGE_FT, WATCH_PROBABILITY,
                       compute_tier)


def row(**kw):
    base = dict(ts=1_700_000_000.0, stage_ft=None, rate_of_rise_in_min=None,
                soil_moisture_mean_pct=50.0, soil_moisture_near_house_pct=50.0,
                soil_moisture_near_creek_pct=50.0, ponding_flag=False)
    base.update(kw)
    return FeatureRow(**base)


def threshold_model():
    tmp = Path(tempfile.mkdtemp())
    return Model(Config(), ModelRegistry(tmp), tmp)


def old_estimate(r):
    """The 0.28.3 estimate, verbatim: rate of rise plus the ponding bump."""
    p = 0.0
    ror = r.rate_of_rise_in_min or 0.0
    if ror > 0:
        p = min(1.0, ror / 0.5 * 0.5)
    if r.ponding_flag:
        p = min(1.0, p + 0.15)
    return round(p, 3)


def test_the_field_case_no_longer_reads_zero():
    """2026-11-12: 2.9 ft on a slow rise, Emergency by stage, ~0 % by the estimate."""
    pred = threshold_model().predict(row(stage_ft=2.9, rate_of_rise_in_min=0.002))
    assert pred.method == "threshold"
    assert pred.flood_probability >= EMERGENCY_PROBABILITY


def test_the_stage_term_follows_the_tier_bands():
    assert stage_probability(None) == 0.0
    assert stage_probability(WARNING_STAGE_FT - 0.01) == 0.0
    assert stage_probability(WARNING_STAGE_FT) == WARNING_PROBABILITY
    assert WARNING_PROBABILITY < stage_probability(2.25) < EMERGENCY_PROBABILITY
    assert stage_probability(EMERGENCY_STAGE_FT - 0.001) < EMERGENCY_PROBABILITY
    assert stage_probability(EMERGENCY_STAGE_FT) == EMERGENCY_PROBABILITY
    assert stage_probability(BANK_TOP_FT) == 1.0
    assert stage_probability(BANK_TOP_FT + 2) == 1.0


def test_the_stage_term_never_falls_as_the_creek_rises():
    stages = [i / 100 for i in range(0, 500)]
    values = [stage_probability(s) for s in stages]
    assert values == sorted(values)


def test_below_warning_stage_nothing_changes():
    model = threshold_model()
    for stage, ror, ponding in product((0.3, 1.0, 1.99), (None, 0.0, 0.01, 0.1),
                                       (False, True)):
        r = row(stage_ft=stage, rate_of_rise_in_min=ror, ponding_flag=ponding)
        assert model.predict(r).flood_probability == old_estimate(r)


def test_a_fast_rise_still_outranks_a_low_stage_term():
    """The stage term is a floor, not a replacement."""
    model = threshold_model()
    r = row(stage_ft=2.0, rate_of_rise_in_min=0.9)
    assert model.predict(r).flood_probability == old_estimate(r) == 0.9


def test_the_tier_is_unchanged_everywhere():
    """The point of the band mapping: same alert level as the 0.28.3 estimate, for every
    stage, rise rate, ponding state, crest and post-reconnect sample count tried."""
    model = threshold_model()
    stages = [None] + [i / 20 for i in range(0, 90)]           # 0.00 .. 4.45 ft
    rors = (None, 0.0, 0.01, 0.049, 0.05, 0.2, 0.5, 0.8, 1.2)
    crests = (None, 1.0, 2.4, 2.5, 3.0)
    checked = 0
    for stage, ror, ponding, crest, samples in product(
            stages, rors, (False, True), crests, (None, 1, 5)):
        r = row(stage_ft=stage, rate_of_rise_in_min=ror, ponding_flag=ponding,
                rate_of_rise_sample_count=samples)
        before = compute_tier(r, old_estimate(r), predicted_crest_ft=crest)[0]
        after = compute_tier(r, model.predict(r).flood_probability,
                             predicted_crest_ft=crest)[0]
        assert before == after, (stage, ror, ponding, crest, samples, before, after)
        checked += 1
    assert checked > 20_000


def test_the_watch_band_is_never_entered_by_stage_alone():
    """A stage term between WATCH and WARNING probability would be a Watch from height
    alone, below the Warning stage -- a new alert. There is no such stage."""
    for i in range(0, 500):
        p = stage_probability(i / 100)
        assert not (WATCH_PROBABILITY <= p < WARNING_PROBABILITY), i / 100


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
