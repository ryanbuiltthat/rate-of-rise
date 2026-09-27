"""Plain-assert tests for ModelRegistry (no pytest dependency).

Run: python rate_of_rise/tests/test_registry.py
Exercises the promote/rollback pointer logic against a temp registry.json.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.registry import (  # noqa: E402
    READY_MAX_FALSE_ALARM_RATE, READY_MIN_HIT_RATE, READY_MIN_ROC_AUC,
    READY_MIN_TEST_POSITIVES, THRESHOLD_LABEL, ModelRegistry, RegistryError,
    promotion_readiness)

# Stands in for a candidate a held-out split could actually score — and that caught some
# of it — so the pointer tests below promote without tripping the unvalidated warning.
VALIDATED = {"roc_auc": 0.8, "hit_rate": 0.6}


def fresh_registry():
    tmp = Path(tempfile.mkdtemp())
    return ModelRegistry(tmp), tmp


def test_empty_defaults():
    reg, _ = fresh_registry()
    snap = reg.snapshot()
    assert snap["active_version"] is None
    assert snap["candidate_version"] is None
    assert snap["history"] == []
    assert snap["event_count"] == 0


def test_promote_sets_active_and_clears_candidate():
    reg, _ = fresh_registry()
    reg.set_candidate("v1", {"roc_auc": 0.8})
    assert reg.snapshot()["candidate_version"] == "v1"
    assert reg.promote() == "v1"
    snap = reg.snapshot()
    assert snap["active_version"] == "v1"
    assert snap["candidate_version"] is None
    assert snap["active_metrics"] == {"roc_auc": 0.8}


def test_promote_pushes_previous_active_to_history():
    reg, _ = fresh_registry()
    reg.set_candidate("v1", VALIDATED)
    reg.promote()
    reg.set_candidate("v2", VALIDATED)
    reg.promote()
    snap = reg.snapshot()
    assert snap["active_version"] == "v2"
    # The threshold entry behind v1 is the state the first promotion came from, and is
    # what makes that first promotion undoable.
    assert snap["history"] == ["v1", THRESHOLD_LABEL]


def test_rollback_restores_previous_and_keeps_demoted_as_candidate():
    reg, _ = fresh_registry()
    reg.set_candidate("v1", VALIDATED)
    reg.promote()
    reg.set_candidate("v2", VALIDATED)
    reg.promote()  # active=v2, history=[v1]
    assert reg.rollback() == "v1"
    snap = reg.snapshot()
    assert snap["active_version"] == "v1"
    assert snap["candidate_version"] == "v2"  # demoted, not lost
    assert snap["history"] == [THRESHOLD_LABEL]


def test_promote_without_candidate_raises():
    reg, _ = fresh_registry()
    try:
        reg.promote()
    except RegistryError:
        pass
    else:
        raise AssertionError("expected RegistryError")


def test_rollback_with_nothing_active_raises():
    reg, _ = fresh_registry()
    try:
        reg.rollback()
    except RegistryError:
        pass
    else:
        raise AssertionError("expected RegistryError")


def test_first_promotion_can_be_rolled_back_to_the_threshold_estimate():
    """The reported failure: promoting the first model ever produced was a one-way
    door, because promote() recorded nothing when there was no outgoing active."""
    reg, _ = fresh_registry()
    reg.set_candidate("v1", VALIDATED)
    reg.promote()
    assert reg.rollback() is None          # None = no ML model, threshold estimate
    snap = reg.snapshot()
    assert snap["active_version"] is None
    assert snap["candidate_version"] == "v1"   # demoted, still re-promotable
    assert snap["history"] == []


def test_rollback_recovers_a_registry_written_before_the_history_fix():
    """A registry left by the old promote(): a model is active with empty history.
    Rollback must reach the threshold estimate rather than stranding the operator."""
    reg, tmp = fresh_registry()
    reg.set_candidate("v1", VALIDATED)
    reg.promote()
    reg._data["history"] = []      # what the old code left on disk
    reg._save()

    stuck = ModelRegistry(tmp)
    assert stuck.active_version == "v1" and stuck.snapshot()["history"] == []
    assert stuck.rollback() is None
    assert stuck.active_version is None
    assert ModelRegistry(tmp).active_version is None    # and it persisted


def test_promote_warns_but_still_activates_an_unvalidated_candidate():
    """The metrics from the field: a test split with no positives in it, so nothing
    scored the model — yet promoting it put it in charge of Tier 3. The operator keeps
    the call; what they must not get is silence."""
    reg, _ = fresh_registry()
    reg.set_candidate("gbm-unscored", {
        "test_rows": 19, "test_positives": 0,
        "note": "test split is single-class; precision/recall/AUC undefined",
        "train_rows": 181, "train_positives": 36})
    assert reg.promote() == "gbm-unscored"     # allowed
    assert reg.active_version == "gbm-unscored"

    caveat = reg.warning()
    assert caveat is not None
    assert "never validated" in caveat
    assert "single-class" in caveat            # says *why*, not just "unvalidated"
    assert "Tier 3/4" in caveat                # and what it costs
    assert reg.snapshot()["active_validated"] is False


def test_a_scored_candidate_promotes_without_a_warning():
    reg, _ = fresh_registry()
    reg.set_candidate("gbm-scored", {"roc_auc": 0.82, "hit_rate": 0.7})
    assert reg.promote() == "gbm-scored"
    assert reg.warning() is None
    assert reg.snapshot()["active_validated"] is True


def test_the_warning_clears_when_the_model_is_rolled_back_out():
    reg, _ = fresh_registry()
    reg.set_candidate("gbm-unscored", {"note": "test split empty after embargo"})
    reg.promote()
    assert reg.warning() is not None
    assert reg.rollback() is None              # back to the threshold estimate
    # The threshold estimate makes no claim a held-out split could check, so it is not
    # "unvalidated" — it is a different kind of answer, and flagging it would be noise.
    assert reg.warning() is None
    assert reg.snapshot()["active_validated"] is True


def test_an_unvalidated_promotion_is_still_undoable():
    reg, _ = fresh_registry()
    reg.set_candidate("gbm-unscored", {"note": "test split empty after embargo"})
    reg.promote()
    assert reg.rollback() is None
    assert reg.snapshot()["candidate_version"] == "gbm-unscored"   # not lost


def test_persistence_round_trip():
    reg, tmp = fresh_registry()
    reg.set_candidate("v1", dict(VALIDATED, a=1))
    reg.promote()
    reg.set_event_count(7)
    reloaded = ModelRegistry(tmp)
    assert reloaded.active_version == "v1"
    assert reloaded.event_count == 7


def test_an_auc_that_caught_nothing_is_not_validated():
    """gbm-20260924T030456Z, from the field: AUC 0.608, hit rate 0.0 — it missed all 49
    held-out positives — and `active_validated: true` on the dashboard, because the old
    check only asked whether an AUC existed."""
    reg, _ = fresh_registry()
    reg.set_candidate("gbm-20260924T030456Z", {
        "test_rows": 595, "test_positives": 49, "hit_rate": 0.0,
        "false_alarm_rate": None, "roc_auc": 0.608, "true_positives": 0})
    reg.promote()
    caveat = reg.warning()
    assert caveat is not None and "none of the 49" in caveat, caveat
    assert reg.snapshot()["active_validated"] is False


def test_candidate_version_is_readable():
    reg, _ = fresh_registry()
    assert reg.candidate_version is None
    reg.set_candidate("v1", VALIDATED)
    assert reg.candidate_version == "v1"


# Clears every READY_* bar with a little room, so each test below breaks exactly one.
READY = {"roc_auc": 0.7, "hit_rate": 0.4, "false_alarm_rate": 0.6, "test_positives": 10}


def test_no_candidate_is_not_ready():
    snap = fresh_registry()[0].snapshot()
    assert snap["candidate_ready"] is False
    assert snap["candidate_ready_reason"] == "no candidate"


def test_a_candidate_clearing_every_bar_is_ready():
    reg, _ = fresh_registry()
    reg.set_candidate("gbm-good", READY)
    snap = reg.snapshot()
    assert snap["candidate_ready"] is True, snap
    assert "40%" in snap["candidate_ready_reason"]


def test_the_field_candidate_with_a_single_class_split_is_not_ready():
    """gbm-20260921T215321Z: 469 test rows, 0 positives — the case that prompted this."""
    ready, reason = promotion_readiness({
        "test_rows": 469, "test_positives": 0,
        "note": "test split is single-class; precision/recall/AUC undefined"})
    assert not ready
    assert "single-class" in reason


def test_each_ready_bar_is_enforced():
    for key, bad, needle in (
        ("test_positives", READY_MIN_TEST_POSITIVES - 1, "held-out positive"),
        ("roc_auc", READY_MIN_ROC_AUC - 0.01, "AUC"),
        ("hit_rate", READY_MIN_HIT_RATE - 0.01, "caught"),
        ("false_alarm_rate", READY_MAX_FALSE_ALARM_RATE + 0.01, "false-alarm"),
        ("false_alarm_rate", None, "undefined"),
    ):
        ready, reason = promotion_readiness(dict(READY, **{key: bad}))
        assert not ready, (key, bad)
        assert needle in reason, (key, reason)


def test_the_bars_are_inclusive():
    ready, _ = promotion_readiness({
        "roc_auc": READY_MIN_ROC_AUC, "hit_rate": READY_MIN_HIT_RATE,
        "false_alarm_rate": READY_MAX_FALSE_ALARM_RATE,
        "test_positives": READY_MIN_TEST_POSITIVES})
    assert ready


def test_the_2026_09_24_zero_hit_model_is_not_ready():
    ready, _ = promotion_readiness(
        {"roc_auc": 0.608, "hit_rate": 0.0, "false_alarm_rate": None, "test_positives": 49})
    assert not ready


def test_promoting_clears_readiness():
    reg, _ = fresh_registry()
    reg.set_candidate("gbm-good", READY)
    reg.promote()
    assert reg.snapshot()["candidate_ready"] is False


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
