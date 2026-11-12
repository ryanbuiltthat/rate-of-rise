"""Plain-assert tests for the nightly retrain's candidate handling (0.28.3).

Run: python rate_of_rise/tests/test_retrain_candidate.py

The held-out split is the most recent 20 % of the record by time. After weeks of dry
weather it holds no positives, so every nightly fit comes back unscored -- and each one
used to replace the candidate, including a scored one. model.py only shadows a
validated candidate, so the shadow probability went blank until a storm put a rise back
in the test window.
"""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.__main__ as service  # noqa: E402
from app.registry import ModelRegistry  # noqa: E402

SCORED = {"roc_auc": 0.81, "hit_rate": 0.5, "false_alarm_rate": 0.4, "test_positives": 6}
UNSCORED = {"note": "held-out split is single-class (no positives)", "test_rows": 900}


class _Model:
    def event_count(self):
        return 99


class _Dataset:
    def frame(self):
        return None


def _run(registry, metrics, version):
    tmp = Path(tempfile.mkdtemp())
    real = service.train.train
    service.train.train = lambda frame, data_dir: SimpleNamespace(
        version=version, metrics=metrics, model_path=tmp / "m", meta_path=tmp / "mm")
    try:
        cfg = SimpleNamespace(min_events_for_ml=10)
        return service._retrain(cfg, _Dataset(), registry, _Model(), tmp)
    finally:
        service.train.train = real


def fresh():
    return ModelRegistry(Path(tempfile.mkdtemp()))


def test_an_unscored_fit_does_not_replace_a_scored_candidate():
    reg = fresh()
    reg.set_candidate("gbm-storm", SCORED)
    result = _run(reg, UNSCORED, "gbm-dry")
    assert reg.candidate_version == "gbm-storm"
    assert reg.candidate_validated
    assert "kept validated candidate gbm-storm" in result
    assert "single-class" in result


def test_a_scored_fit_still_replaces_a_scored_candidate():
    reg = fresh()
    reg.set_candidate("gbm-old", SCORED)
    _run(reg, SCORED, "gbm-new")
    assert reg.candidate_version == "gbm-new"


def test_an_unscored_fit_replaces_an_unscored_candidate():
    """Nothing scored to protect: the newest fit has seen the most data."""
    reg = fresh()
    reg.set_candidate("gbm-old", UNSCORED)
    _run(reg, UNSCORED, "gbm-new")
    assert reg.candidate_version == "gbm-new"


def test_a_first_fit_is_recorded_even_unscored():
    reg = fresh()
    _run(reg, UNSCORED, "gbm-first")
    assert reg.candidate_version == "gbm-first"


def test_a_zero_hit_fit_counts_as_unscored():
    """An AUC that caught nothing is not validated (registry.is_validated)."""
    reg = fresh()
    reg.set_candidate("gbm-storm", SCORED)
    _run(reg, {"roc_auc": 0.6, "hit_rate": 0.0, "test_positives": 4}, "gbm-blind")
    assert reg.candidate_version == "gbm-storm"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
