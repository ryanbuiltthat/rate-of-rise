"""Plain-assert tests for ModelRegistry (no pytest dependency).

Run: python rate_of_rise/tests/test_registry.py
Exercises the promote/rollback pointer logic against a temp registry.json.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.registry import THRESHOLD_LABEL, ModelRegistry, RegistryError  # noqa: E402

# Every candidate below carries roc_auc unless a test is specifically about the
# validation gate — promote() refuses an unscored candidate without `force`.
VALIDATED = {"roc_auc": 0.8}


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


def test_promote_refuses_a_candidate_with_no_validation():
    """The metrics from the field: a test split with no positives in it, so nothing
    scored the model — yet promoting it put it in charge of Tier 3."""
    reg, _ = fresh_registry()
    reg.set_candidate("gbm-unscored", {
        "test_rows": 19, "test_positives": 0,
        "note": "test split is single-class; precision/recall/AUC undefined",
        "train_rows": 181, "train_positives": 36})
    try:
        reg.promote()
    except RegistryError as exc:
        assert "not been validated" in str(exc)
        assert "single-class" in str(exc)      # says *why*, not just "no"
    else:
        raise AssertionError("expected RegistryError")
    assert reg.active_version is None          # nothing was activated
    assert reg.snapshot()["candidate_version"] == "gbm-unscored"   # and nothing lost


def test_promote_force_overrides_the_validation_gate():
    reg, _ = fresh_registry()
    reg.set_candidate("gbm-unscored", {"note": "test split empty after embargo"})
    assert reg.promote(force=True) == "gbm-unscored"
    assert reg.active_version == "gbm-unscored"
    assert reg.rollback() is None              # and a forced promote is still undoable


def test_promote_accepts_a_candidate_the_split_could_score():
    reg, _ = fresh_registry()
    reg.set_candidate("gbm-scored", {"roc_auc": 0.82, "hit_rate": 0.7})
    assert reg.promote() == "gbm-scored"       # no force needed


def test_persistence_round_trip():
    reg, tmp = fresh_registry()
    reg.set_candidate("v1", dict(VALIDATED, a=1))
    reg.promote()
    reg.set_event_count(7)
    reloaded = ModelRegistry(tmp)
    assert reloaded.active_version == "v1"
    assert reloaded.event_count == 7


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
