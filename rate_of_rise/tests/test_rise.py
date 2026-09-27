"""Tests for the rise-probability models (app/rise.py) and the stage-history features they
lean on (features.stage_history_features).

Like test_train.py these run on fabricated storms: they prove the labelling, the
leave-one-storm-out scoring, the gates and the save/load round trip, not that the model
knows anything about the creek. What it knows is in the metrics it publishes.

Run: python rate_of_rise/tests/test_rise.py
"""
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import rise  # noqa: E402
from app.config import Config  # noqa: E402
from app.features import FeatureBuilder, stage_history_features  # noqa: E402

TS0 = 1_700_000_000.0
STEP = 300.0     # the fast loop's 5 minutes


def _frame(stage, **cols):
    n = len(stage)
    df = pd.DataFrame({"ts": TS0 + np.arange(n) * STEP, "stage_ft": stage,
                       "creek_node_online": True, "rate_of_rise_sample_count": 10.0})
    for k, v in cols.items():
        df[k] = v
    return df


def _storms(n_storms=5, n=2400, seed=0):
    """Dry baseline at 1.0 ft; each storm rains for two hours, and the creek comes up ~2.5 in
    over the next 90 min and recedes. Returns (frame, storm windows)."""
    rng = np.random.default_rng(seed)
    stage = np.full(n, 1.0) + rng.normal(0, 0.002, n)
    rain1 = np.zeros(n)
    windows = []
    for s in np.linspace(100, n - 150, n_storms).astype(int):
        rain1[s:s + 24] = rng.uniform(0.3, 0.5)          # 2 h of heavy rain
        rise_ft = rng.uniform(0.18, 0.25)
        up = np.linspace(0, rise_ft, 18)                   # 90 min rise, from 30 min in
        stage[s + 6:s + 24] += up
        stage[s + 24:s + 84] += np.linspace(rise_ft, 0, 60)
        windows.append((TS0 + s * STEP, TS0 + (s + 30) * STEP))
    df = _frame(stage, rain_1h_in=rain1, rain_3h_in=pd.Series(rain1).rolling(36, 1).sum() / 12,
                upstream_rain_1h_in=rain1, upstream_rain_3h_in=rain1 * 2,
                soil_moisture_mean_pct=70.0, api_index_in=1.0, rate_of_rise_in_min=0.0)
    return df, windows


# --- stage history --------------------------------------------------------------------

def test_stage_history_hand_computed():
    ts = TS0 + np.arange(0, 7 * 3600 + 1, STEP)
    stage = 1.0 + np.arange(len(ts)) * 0.001            # +0.012 ft per hour
    change, above = stage_history_features(ts, stage)
    assert math.isclose(change[-1], 0.012 * 12, rel_tol=1e-6)
    # Six hours of a steady climb: above the 6 h low by 6 h of it.
    assert math.isclose(above[-1], 6 * 0.012 * 12, rel_tol=1e-6)
    assert np.isnan(change[0]) and np.isnan(above[0])   # no history yet


def test_stage_history_needs_a_reading_near_an_hour_ago():
    # A 2 h dropout: no reading within 10 min of an hour ago, so no change — not a change
    # measured against something 2 h old.
    ts = np.r_[TS0 + np.arange(0, 3600, STEP), TS0 + 3 * 3600 + np.arange(0, 1200, STEP)]
    stage = np.full(len(ts), 1.0)
    change, above = stage_history_features(ts, stage)
    assert np.isnan(change[-1])
    assert not np.isnan(above[-1])      # the 6 h low still spans > 1 h


def test_stage_history_skips_readings_that_did_not_count():
    ts = TS0 + np.arange(0, 2 * 3600 + 1, STEP)
    stage = np.full(len(ts), 1.0)
    stage[5] = np.nan                   # an offline / implausible row
    change, above = stage_history_features(ts, stage)
    assert np.isnan(change[5]) and np.isnan(above[5])
    assert change[-1] == 0.0


class _HA:
    def __init__(self):
        self.stage, self.online = 1.0, True

    def get_float_with_age(self, entity_id):
        if "packets" in entity_id:
            return 1.0, 30.0 if self.online else 900.0
        return self.stage, 30.0

    def get_bool(self, entity_id):
        return self.online

    def get_float(self, entity_id):
        return None

    def get_unit(self, entity_id):
        return None


def test_live_rows_match_what_training_recomputes():
    """FeatureBuilder and rise.feature_frame must agree, or the model is scored on one
    definition and fed another. Includes a dropout, which neither may difference across."""
    clock = [TS0]
    ha = _HA()
    fb = FeatureBuilder(Config(soil_moisture_entities=[], onsite_temp_entity=None), ha,
                        now_fn=lambda: clock[0])
    rows = []
    for i in range(120):
        ha.stage = 1.0 + 0.002 * i
        ha.online = not (60 <= i < 70)
        rows.append(fb.build().as_dict())
        clock[0] += STEP
    df = pd.DataFrame(rows)
    stage = rise.accepted_stage(df)
    x = rise.feature_frame(df, stage=stage)
    for col in ("stage_change_1h_in", "stage_above_6h_low_in"):
        live = pd.to_numeric(df[col], errors="coerce").to_numpy(float)
        assert np.allclose(live, x[col].to_numpy(), equal_nan=True, atol=1e-3), col
    assert rows[65]["stage_change_1h_in"] is None      # offline row: nothing, not a guess


# --- labels -----------------------------------------------------------------------------

def test_label_marks_a_rise_within_the_horizon():
    stage = [1.0] * 20 + [1.0 + 0.6 / 12] + [1.0] * 20     # a 0.6 in blip at row 20
    y = rise.label_rise(_frame(stage), 60, 0.5)
    # Rows 8-19 see row 20 within the next 12 steps; row 20 itself does not.
    assert y[8:20].tolist() == [1.0] * 12
    assert y[7] == 0.0 and y[20] == 0.0


def test_label_is_unknown_when_the_radio_was_down():
    stage = [1.0] * 40
    df = _frame(stage)
    df.loc[10:30, "creek_node_online"] = False
    y = rise.label_rise(df, 60, 0.5)
    assert np.isnan(y[15])               # its own reading did not count
    assert np.isnan(y[5])                # most of its window was dark
    assert y[35] == 0.0 or np.isnan(y[35])


def test_a_clamped_radar_reading_is_not_a_rise():
    stage = [1.0] * 20 + [3.13] + [1.0] * 20                # the 2026-09-17 artifact
    y = rise.label_rise(_frame(stage), 60, 0.5)
    assert np.nansum(y) == 0


def test_an_unconfirmed_rate_is_not_an_input():
    df = _frame([1.0] * 3, rate_of_rise_in_min=[0.2, 0.2, 0.2])
    df["rate_of_rise_sample_count"] = [0.0, 1.0, 5.0]
    x = rise.feature_frame(df)
    assert np.isnan(x["rate_of_rise_in_min"][0]) and np.isnan(x["rate_of_rise_in_min"][1])
    assert x["rate_of_rise_in_min"][2] == 0.2


# --- training -------------------------------------------------------------------------

def test_refuses_with_a_reason_until_two_rises_are_on_record():
    df, windows = _storms(n_storms=1)
    r = rise.train_rise(df, Path(tempfile.mkdtemp()), 60, 0.5, windows)
    assert r.version is None
    assert "1 rise" in r.reason


def test_trains_scores_and_saves_on_repeated_storms():
    df, windows = _storms(n_storms=5)
    d = Path(tempfile.mkdtemp())
    r = rise.train_rise(df, d, 180, 1.0, windows)
    assert r.version and r.version.startswith("rise3h-")
    m = r.metrics
    assert m["episodes"] == 5
    for key in ("roc_auc", "brier", "brier_climatology", "brier_skill", "episodes_flagged",
                "quiet_false_alarms", "base_rate", "trustworthy"):
        assert key in m, key
    assert m["brier_skill"] > 0 and m["episodes_flagged"] >= 1
    meta = json.loads((d / "models" / "rise" / f"{r.version}.meta.json").read_text())
    assert meta["threshold_in"] == 1.0 and meta["features"] == list(rise.FEATURES)


def test_old_artifacts_are_pruned():
    df, windows = _storms(n_storms=3)
    d = Path(tempfile.mkdtemp())
    out = d / "models" / "rise"
    out.mkdir(parents=True)
    for i in range(5):           # older fits, lexically before any real version
        (out / f"rise1h-2000010{i}T000000Z.meta.json").write_text("{}")
        (out / f"rise1h-2000010{i}T000000Z.json").write_text("{}")
    rise.train_rise(df, d, 60, 0.5, windows)
    assert len(list(out.glob("rise1h-*.meta.json"))) == rise.KEEP_ARTIFACTS


# --- inference ------------------------------------------------------------------------

def test_no_model_publishes_unknown_with_the_reason():
    m = rise.RiseModels(Path(tempfile.mkdtemp()), {60: 0.5, 180: 1.0})
    out = m.predict({})
    assert out[60]["value"] is None and out[60]["reason"] == "no model trained yet"
    assert m.needs_training()


def test_a_trained_model_answers_even_with_features_missing():
    df, windows = _storms(n_storms=4)
    d = Path(tempfile.mkdtemp())
    for h, x in ((60, 0.5), (180, 1.0)):
        assert rise.train_rise(df, d, h, x, windows).version
    m = rise.RiseModels(d, {60: 0.5, 180: 1.0})
    assert not m.needs_training()
    wet = m.predict({"rain_1h_in": 0.45, "upstream_rain_3h_in": 0.9, "stage_change_1h_in": 0.0})
    dry = m.predict({"rain_1h_in": 0.0})
    for h in (60, 180):
        assert 0.0 <= dry[h]["value"] <= 1.0 and wet[h]["value"] > dry[h]["value"]
        assert wet[h]["version"] and wet[h]["reason"] is None


def test_a_model_for_another_threshold_is_not_used():
    df, windows = _storms(n_storms=4)
    d = Path(tempfile.mkdtemp())
    rise.train_rise(df, d, 60, 0.5, windows)
    m = rise.RiseModels(d, {60: 0.75, 180: 1.0})
    out = m.predict({})
    assert out[60]["value"] is None and "0.5 in" in out[60]["reason"]
    assert m.needs_training()


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
