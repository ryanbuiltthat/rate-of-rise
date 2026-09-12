"""Tests for Model's threshold/ML gating and its interaction with the registry
(spec §5). The behavior this exists to pin down: a Promote/Rollback command only
ever touches ModelRegistry (see __main__.py's `_promote`/`_rollback`) — Model must
notice that on its own rather than requiring the add-on to restart before a
promotion takes effect.

Run: python creek_modeling/tests/test_model.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Config  # noqa: E402
from app.features import FeatureRow  # noqa: E402
from app.model import Model  # noqa: E402
from app.registry import ModelRegistry  # noqa: E402
from app import train as t  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


def row(**overrides):
    base = dict(
        ts=1_700_000_000.0, stage_ft=None, rate_of_rise_in_min=None,
        soil_moisture_mean_pct=50.0, soil_moisture_near_house_pct=50.0,
        soil_moisture_near_creek_pct=50.0, ponding_flag=False,
    )
    base.update(overrides)
    return FeatureRow(**base)


def _trained_artifact(data_dir: Path):
    """A real booster, trained the same way test_train.py's end-to-end test does —
    Model has no business knowing how an artifact was produced, only how to load one."""
    rng = np.random.default_rng(2)
    n = 600
    ts = 1_700_000_000.0 + np.arange(n) * 300.0
    stage = np.full(n, 0.4)
    for s in np.linspace(20, n - 60, 4).astype(int):
        rise_dur = int(rng.integers(8, 20))
        peak = float(rng.uniform(1.5, 3.0))
        stage[s + 6:s + 6 + rise_dur] += np.linspace(0, peak, rise_dur)
    ror = np.diff(stage, prepend=stage[0]) * 12.0
    df = pd.DataFrame({"ts": ts, "stage_ft": stage, "rate_of_rise_in_min": ror})
    result = t.train(df, data_dir)
    assert result is not None, "fixture storm did not produce a trainable artifact"
    return result


def test_defaults_to_threshold_with_no_active_version():
    d = Path(tempfile.mkdtemp())
    model = Model(Config(), ModelRegistry(d), d)
    assert model.active_method == "threshold"
    assert model.predict(row()).method == "threshold"


def test_fails_open_when_the_registry_names_a_version_with_no_artifact_on_disk():
    """A hand-edited or corrupted registry.json must not take inference down —
    the artifact file simply is not there, and predict() must fall back cleanly."""
    d = Path(tempfile.mkdtemp())
    registry = ModelRegistry(d)
    registry.set_candidate("ghost-version", {})
    registry.promote()
    registry.set_event_count(999)   # clear the event gate so only the artifact matters
    model = Model(Config(), registry, d)
    assert model.active_method == "threshold"
    assert model.predict(row()).method == "threshold"


def test_event_gate_holds_even_with_a_real_artifact_promoted():
    d = Path(tempfile.mkdtemp())
    registry = ModelRegistry(d)
    result = _trained_artifact(d)
    registry.set_candidate(result.version, result.metrics)
    registry.promote()
    registry.set_event_count(Config().min_events_for_ml - 1)   # below the gate
    model = Model(Config(), registry, d)
    assert model.active_method == "threshold"


def test_uses_the_promoted_artifact_once_both_gates_are_clear():
    d = Path(tempfile.mkdtemp())
    registry = ModelRegistry(d)
    result = _trained_artifact(d)
    registry.set_candidate(result.version, result.metrics)
    registry.promote()
    registry.set_event_count(Config().min_events_for_ml)
    model = Model(Config(), registry, d)
    assert model.active_method == f"ml:{result.version}"
    pred = model.predict(row(stage_ft=2.8, rate_of_rise_in_min=0.08))
    assert pred.method == f"ml:{result.version}"
    assert 0.0 <= pred.flood_probability <= 1.0
    assert pred.predicted_crest_ft is None   # not built — see train.py's docstring


def test_promote_takes_effect_without_reconstructing_model():
    """The regression this file exists to guard: Model must pick up a promotion made
    after it was constructed, since __main__.py's promote/rollback commands only ever
    touch the shared registry, never the running Model instance."""
    d = Path(tempfile.mkdtemp())
    registry = ModelRegistry(d)
    registry.set_event_count(Config().min_events_for_ml)
    model = Model(Config(), registry, d)
    assert model.active_method == "threshold"   # nothing active yet

    result = _trained_artifact(d)
    registry.set_candidate(result.version, result.metrics)
    registry.promote()                          # mutates the registry Model already holds

    assert model.active_method == f"ml:{result.version}"
    assert model.predict(row()).method == f"ml:{result.version}"


def test_rollback_also_takes_effect_live():
    d = Path(tempfile.mkdtemp())
    registry = ModelRegistry(d)
    registry.set_event_count(Config().min_events_for_ml)

    # ModelRegistry.rollback() restores the *previous* active version, so there has to
    # be one: promote a placeholder with no real artifact file first (Model will read
    # this as "threshold", same as test_fails_open_... above), then promote a real one.
    registry.set_candidate("placeholder-v0", {})
    registry.promote()
    result = _trained_artifact(d)
    registry.set_candidate(result.version, result.metrics)
    registry.promote()      # pushes placeholder-v0 into history

    model = Model(Config(), registry, d)
    assert model.active_method == f"ml:{result.version}"

    registry.rollback()     # restores placeholder-v0 — no artifact on disk
    assert model.active_method == "threshold"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
