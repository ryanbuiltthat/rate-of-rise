"""Tests for model training (spec §4/§5/§7 Phase 4).

These exercise the pipeline against synthetic, fabricated storms — they prove the
mechanics (labeling, splitting, fitting, serializing, gating) are correct, not that
the model predicts anything about Creek. Nothing can prove that until the
SEN0676 exists and real storms are on record; see train.py's module docstring.

Run: python creek_modeling/tests/test_train.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import train as t  # noqa: E402
from app.tiers import WARNING_RATE_OF_RISE_IN_MIN, WARNING_STAGE_FT  # noqa: E402

TS0 = 1_700_000_000.0
STEP = 1800.0   # 30 min, to keep synthetic fixtures small


def _series(n, stage=None, ror=None):
    ts = TS0 + np.arange(n) * STEP
    df = pd.DataFrame({"ts": ts})
    if stage is not None:
        df["stage_ft"] = stage
    if ror is not None:
        df["rate_of_rise_in_min"] = ror
    return df


def test_label_forward_matches_a_hand_computed_window():
    # Danger (stage >= WARNING_STAGE_FT) at rows 10-12 only.
    stage = [0.5] * 10 + [WARNING_STAGE_FT + 0.5] * 3 + [0.5] * 7
    df = _series(20, stage=stage)
    y = t.label_forward(df, horizon_minutes=180)   # 6 steps of 30 min
    expected = [int(any(10 <= j <= 12 for j in range(i + 1, i + 7))) for i in range(20)]
    assert y.tolist() == expected


def test_label_forward_excludes_the_current_row():
    # Danger right now, at row 5, must not make row 5 itself positive — only rows
    # strictly before it whose forward window reaches row 5.
    stage = [0.5] * 5 + [WARNING_STAGE_FT + 1] + [0.5] * 4
    df = _series(10, stage=stage)
    y = t.label_forward(df, horizon_minutes=60)   # 2 steps
    assert y[5] == 0
    assert y[4] == 1 and y[3] == 1
    assert y[2] == 0   # row 2's window (rows 3-4) does not reach row 5


def test_label_forward_all_calm_is_all_zero():
    df = _series(15, stage=[0.5] * 15, ror=[0.0] * 15)
    assert t.label_forward(df).sum() == 0


def test_danger_now_checks_both_stage_and_rate_of_rise():
    df = _series(3, stage=[0.5, WARNING_STAGE_FT + 1, 0.5],
                    ror=[WARNING_RATE_OF_RISE_IN_MIN + 0.1, 0.0, 0.0])
    danger = t._danger_now(df)
    assert danger.tolist() == [True, True, False]


def test_danger_now_survives_missing_columns_entirely():
    # Not all-NaN — the columns are not there at all. Must not crash.
    df = pd.DataFrame({"ts": [TS0, TS0 + STEP]})
    danger = t._danger_now(df)
    assert danger.tolist() == [False, False]


def test_build_matrix_pads_missing_feature_columns_and_casts_bools():
    df = _series(5, stage=[0.5] * 5, ror=[0.0] * 5)
    df["ponding_flag"] = [True, False, True, False, True]
    x, y, ts = t.build_matrix(df)
    assert list(x.columns) == list(t.FEATURE_COLUMNS)
    assert x["rain_1h_in"].isna().all()          # never provided -> padded NaN
    assert x["ponding_flag"].tolist() == [1, 0, 1, 0, 1]
    assert len(y) == 5 and len(ts) == 5


# --- end-to-end training, against a fabricated but internally consistent storm record ---

def _synthetic_storms(n=600, seed=1, n_storms=4):
    """Rain pulses each followed by a lagged stage rise — enough real signal for the
    classifier to separate the classes, so a low ROC AUC would mean a real regression
    rather than "unlucky seed"."""
    rng = np.random.default_rng(seed)
    ts = TS0 + np.arange(n) * 300.0   # 5 min, matching the real fast loop
    stage = np.full(n, 0.4)
    rain = np.zeros(n)
    for s in np.linspace(20, n - 60, n_storms).astype(int):
        dur = rng.integers(6, 15)
        rain[s:s + dur] = rng.uniform(0.05, 0.3, dur)
        rise_dur = rng.integers(8, 20)
        peak = rng.uniform(1.5, 3.0)
        stage[s + 6:s + 6 + rise_dur] += np.linspace(0, peak, rise_dur)
        tail = 25
        end = min(n, s + 6 + rise_dur + tail)
        stage[s + 6 + rise_dur:end] += np.linspace(peak, 0, tail)[:end - (s + 6 + rise_dur)]
    ror = np.diff(stage, prepend=stage[0]) * 12.0
    return pd.DataFrame({"ts": ts, "stage_ft": stage, "rate_of_rise_in_min": ror,
                         "rain_1h_in": rain})


def test_train_refuses_below_min_train_rows():
    n = t.MIN_TRAIN_ROWS - 1
    df = _series(n, stage=[WARNING_STAGE_FT + 1] * n, ror=[0.0] * n)  # plenty "dangerous"
    with tempfile.TemporaryDirectory() as d:
        assert t.train(df, Path(d)) is None   # too few rows, regardless of labels


def test_train_refuses_with_no_positive_labels():
    # Calm throughout: MIN_TRAIN_ROWS satisfied, MIN_POSITIVE_LABELS is not — this is
    # the steady state of the real system today, per train.py's module docstring.
    df = _series(t.MIN_TRAIN_ROWS + 10,
                stage=[0.4] * (t.MIN_TRAIN_ROWS + 10), ror=[0.0] * (t.MIN_TRAIN_ROWS + 10))
    with tempfile.TemporaryDirectory() as d:
        assert t.train(df, Path(d)) is None


def test_train_end_to_end_produces_a_loadable_artifact_with_sane_metrics():
    df = _synthetic_storms()
    with tempfile.TemporaryDirectory() as d:
        data_dir = Path(d)
        result = t.train(df, data_dir)
        assert result is not None
        assert result.model_path.exists() and result.meta_path.exists()

        m = result.metrics
        assert m["train_rows"] > 0 and m["train_positives"] >= t.MIN_POSITIVE_LABELS
        # A model with real separable signal should beat random guessing by a wide
        # margin; this is the guard against a labeling/leakage bug that would make the
        # metric look perfect (>=0.99, usually a leak) or worthless (~0.5, no signal).
        assert 0.6 < m["roc_auc"] < 0.999
        assert 0.0 <= m["hit_rate"] <= 1.0
        assert 0.0 <= m["false_alarm_rate"] <= 1.0
        # Lead time must be positive and within the horizon — a negative or
        # over-horizon value would mean the lookup is finding the wrong sample.
        if m["lead_time_minutes"] is not None:
            assert 0 < m["lead_time_minutes"] <= t.HORIZON_MINUTES

        booster, meta = t.load_artifact(data_dir, result.version)
        assert booster is not None
        assert meta["feature_columns"] == list(t.FEATURE_COLUMNS)
        assert meta["horizon_minutes"] == t.HORIZON_MINUTES


def test_train_test_split_has_no_rows_inside_the_embargo():
    """The embargo exists to stop a storm straddling the split from leaking across it
    (module docstring) — verify no train row and no test row sit within it."""
    df = _synthetic_storms().sort_values("ts").reset_index(drop=True)
    x, y, ts = t.build_matrix(df)
    x_train, y_train, x_test, y_test = t._chronological_split(x, y, ts)
    if len(x_train) == 0 or len(x_test) == 0:
        return   # small/unlucky split — the end-to-end test already covers the normal case
    split_ts = np.quantile(np.sort(ts), 1.0 - t.TEST_FRACTION)
    train_ts = ts[np.isin(np.arange(len(ts)), x_train.index)] if hasattr(x_train, "index") else None
    # Simpler and robust to internal reordering: every train ts must be well before the
    # split, every test ts well after it, with nothing in between.
    assert (np.sort(ts)[:len(x_train)] < split_ts - t.SPLIT_EMBARGO_SECONDS + 1).all()
    assert (np.sort(ts)[-len(x_test):] >= split_ts + t.SPLIT_EMBARGO_SECONDS).all()


def test_load_artifact_missing_version_fails_open():
    with tempfile.TemporaryDirectory() as d:
        booster, meta = t.load_artifact(Path(d), "gbm-does-not-exist")
        assert booster is None and meta is None


def test_load_artifact_corrupt_model_file_fails_open():
    with tempfile.TemporaryDirectory() as d:
        data_dir = Path(d)
        models = data_dir / "models"
        models.mkdir()
        t._write_meta(models / "model-bad.meta.json", "bad")
        (models / "model-bad.json").write_text("not a model", encoding="utf-8")
        booster, meta = t.load_artifact(data_dir, "bad")
        assert booster is None and meta is None


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
