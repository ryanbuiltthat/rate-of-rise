"""Tests for the predicted-crest model (app/crest.py).

Same fabricated storms as test_rise.py: these prove the labels, the leave-one-storm-out
scoring, the gates, the save/load round trip and the payload, not that the model knows the
creek. What it knows is in the metrics it publishes.

Run: python rate_of_rise/tests/test_crest.py
"""
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import crest  # noqa: E402
from app.tiers import BANK_TOP_FT, EMERGENCY_STAGE_FT, WARNING_STAGE_FT  # noqa: E402
from test_rise import STEP, _frame, _storms  # noqa: E402


# --- labels -----------------------------------------------------------------------------

def test_label_is_the_highest_reading_ahead_and_when():
    stage = [1.0] * 20 + [1.0 + 1.2 / 12, 1.0 + 3.0 / 12, 1.0 + 2.0 / 12] + [1.0] * 40
    rise_in, when = crest.label_crest(_frame(stage), 180)
    # Row 15 sees the 3 in peak at row 21, six steps ahead.
    assert math.isclose(rise_in[15], 3.0, abs_tol=1e-9)
    assert when[15] == 6 * STEP / 60
    # Past the peak the creek only falls: no rise, never a negative one.
    assert rise_in[25] == 0.0


def test_label_is_unknown_when_the_radio_was_down():
    df = _frame([1.0] * 80)
    df.loc[10:60, "creek_node_online"] = False
    rise_in, _ = crest.label_crest(df, 180)
    assert np.isnan(rise_in[20])      # its own reading did not count
    assert np.isnan(rise_in[5])       # most of its window was dark


def test_a_clamped_radar_reading_is_not_a_crest():
    stage = [1.0] * 20 + [3.13] + [1.0] * 40              # the 2026-09-17 artifact
    rise_in, _ = crest.label_crest(_frame(stage), 180)
    assert np.nanmax(rise_in) < 0.01


# --- training -------------------------------------------------------------------------

def test_refuses_with_a_reason_until_two_rises_are_on_record():
    df, windows = _storms(n_storms=1)
    r = crest.train_crest(df, Path(tempfile.mkdtemp()), windows)
    assert r.version is None and "1 rise" in r.reason


def test_trains_scores_and_saves_on_repeated_storms():
    df, windows = _storms(n_storms=5)
    d = Path(tempfile.mkdtemp())
    r = crest.train_crest(df, d, windows)
    assert r.version and r.version.startswith("crest-")
    m = r.metrics
    assert m["episodes"] == 5
    for key in ("mae_in", "mae_climatology_in", "skill", "range_coverage", "rise_rows_mae_in",
                "rise_rows_skill", "episodes_within_tolerance", "time_mae_min",
                "time_mae_climatology_min", "trustworthy"):
        assert key in m, key
    assert m["skill"] > 0 and m["rise_rows_skill"] > 0
    # Timing beats always quoting the typical time to crest.
    assert m["time_mae_min"] < m["time_mae_climatology_min"]
    out = d / "models" / "crest"
    meta = json.loads((out / f"{r.version}.meta.json").read_text())
    assert meta["features"] == list(crest.rise.FEATURES)
    # The storms in the fixture top out ~3 in above a 1 ft creek.
    assert 1.1 < meta["max_trained_stage_ft"] < 1.3
    assert (out / f"{r.version}.time.json").exists()


def test_enough_storms_make_it_trustworthy():
    # Not asserted at 5 storms: there the fixture's crests are called 1 in 5 within an inch
    # on xgboost 3.4 and 0 in 5 on 3.2 (CI), a coin toss rather than a property. At 8 both
    # call 2.
    df, windows = _storms(n_storms=8)
    m = crest.train_crest(df, Path(tempfile.mkdtemp()), windows).metrics
    assert m["episodes_within_tolerance"] >= 1 and m["trustworthy"]


def test_old_artifacts_are_pruned():
    df, windows = _storms(n_storms=3)
    d = Path(tempfile.mkdtemp())
    out = d / "models" / "crest"
    out.mkdir(parents=True)
    for i in range(5):           # older fits, lexically before any real version
        for suffix in ("meta.json", "json", "time.json"):
            (out / f"crest-2000010{i}T000000Z.{suffix}").write_text("{}")
    crest.train_crest(df, d, windows)
    assert len(list(out.glob("crest-*.meta.json"))) == crest.KEEP_ARTIFACTS
    assert len(list(out.glob("crest-*.time.json"))) == crest.KEEP_ARTIFACTS


# --- inference ------------------------------------------------------------------------

def test_no_model_publishes_unknown_with_the_reason():
    m = crest.CrestModel(Path(tempfile.mkdtemp()))
    out = m.predict({"stage_ft": 1.0})
    assert out["value"] is None and out["reason"] == "no model trained yet"
    assert m.needs_training()


def _trained():
    df, windows = _storms(n_storms=5)
    d = Path(tempfile.mkdtemp())
    assert crest.train_crest(df, d, windows).version
    return crest.CrestModel(d)


def test_a_trained_model_predicts_a_crest_above_the_current_stage():
    m = _trained()
    assert not m.needs_training()
    wet = m.predict({"stage_ft": 1.0, "rain_1h_in": 0.45, "rain_3h_in": 0.4,
                     "upstream_rain_1h_in": 0.45, "upstream_rain_3h_in": 0.9,
                     "stage_change_1h_in": 0.0, "stage_above_6h_low_in": 0.0})
    dry = m.predict({"stage_ft": 1.0, "rain_1h_in": 0.0, "upstream_rain_1h_in": 0.0})
    assert wet["version"] and wet["reason"] is None
    assert wet["low_ft"] <= wet["value"] <= wet["high_ft"]
    assert wet["rise_in"] > dry["rise_in"] >= 0.0
    assert wet["value"] > dry["value"] >= 1.0
    assert wet["time_to_crest_min"] is not None and wet["time_to_crest_min"] >= 0
    # Nothing in the fixture reaches Warning.
    assert wet["reaches"] is None


def test_no_crest_without_a_current_stage():
    m = _trained()
    assert m.predict({"rain_1h_in": 0.4})["reason"] == "no current stage"
    held = m.predict({"stage_ft": 1.0, "stage_held": True})
    assert held["value"] is None and "offline" in held["reason"]


def test_a_crest_past_anything_trained_on_says_so():
    m = _trained()
    out = m.predict({"stage_ft": 2.9})
    assert out["beyond_training"] is True
    assert out["reaches"] in ("emergency", "bank")


def test_levels_are_the_surveyed_ones_highest_first():
    assert crest.level_reached(None) is None
    assert crest.level_reached(WARNING_STAGE_FT - 0.01) is None
    assert crest.level_reached(WARNING_STAGE_FT) == "warning"
    assert crest.level_reached(EMERGENCY_STAGE_FT) == "emergency"
    assert crest.level_reached(BANK_TOP_FT) == "bank"
    assert math.isclose(BANK_TOP_FT * 12, 44.25)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
