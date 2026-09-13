"""Tests for rainfall->response lag estimation (spec §7 Phase 3).
Run: python rate_of_rise/tests/test_lag.py
"""
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.lag import (  # noqa: E402
    LAG_WINDOW_DAYS, estimate_lag, load_lag, save_lag,
)

T0 = 1769904000.0
STEP = 300.0        # the 5-minute fast loop


def synthetic(lag_minutes, n=300, response_col="stage_ft", noise=0.0):
    """Rain pulses, with the response rising `lag_minutes` later by construction."""
    lag_steps = int(lag_minutes * 60 / STEP)
    rain = [0.0] * n
    for start in (40, 130, 220):                 # three storms
        for k in range(8):
            rain[start + k] = 0.6 - 0.05 * k

    level, current = [], 1.0
    for i in range(n):
        driver = rain[i - lag_steps] if i - lag_steps >= 0 else 0.0
        current += driver * 0.1 - 0.002         # rise on lagged rain, slow recession
        current = max(1.0, current)
        if noise:
            current += math.sin(i * 1.7) * noise
        level.append(current)

    return pd.DataFrame({
        "ts": [T0 + i * STEP for i in range(n)],
        "upstream_rain_1h_in": rain,
        response_col: level,
    })


def test_recovers_a_known_lag():
    out = estimate_lag(synthetic(60))
    assert out["lag_minutes"] == 60, out
    assert out["correlation"] > 0.5
    assert out["response"] == "creek stage"


def test_recovers_a_short_lag():
    assert estimate_lag(synthetic(15))["lag_minutes"] == 15


def test_recovers_a_long_lag():
    assert estimate_lag(synthetic(180))["lag_minutes"] == 180


def test_survives_noise_on_the_response():
    out = estimate_lag(synthetic(45, noise=0.01))
    assert out["lag_minutes"] == 45, out


def test_falls_back_to_the_downstream_gauge_and_says_so():
    # No creek node yet: the estimate must be labelled as the proxy it is.
    df = synthetic(120, response_col="usgs_leggetts_rise_3h_ft")
    out = estimate_lag(df)
    assert out["lag_minutes"] == 120
    assert out["response"] == "USGS the adjacent creek (proxy)"


def test_prefers_real_stage_over_the_proxy_when_both_exist():
    df = synthetic(60)
    df["usgs_leggetts_rise_3h_ft"] = df["stage_ft"]
    assert estimate_lag(df)["response"] == "creek stage"


def test_reports_no_estimate_before_there_is_data():
    out = estimate_lag(pd.DataFrame({"ts": [T0]}))
    assert out["lag_minutes"] is None
    assert "not enough data" in out["reason"]


def test_reports_no_estimate_without_a_response_series():
    df = synthetic(60).drop(columns=["stage_ft"])
    out = estimate_lag(df)
    assert out["lag_minutes"] is None
    assert out["reason"] == "no response series available"


def test_reports_no_estimate_when_it_has_not_rained():
    df = synthetic(60)
    df["upstream_rain_1h_in"] = 0.0
    out = estimate_lag(df)
    assert out["lag_minutes"] is None
    assert "no significant rain" in out["reason"]


def test_weak_correlation_reports_the_number_but_withholds_the_estimate():
    # A response unrelated to the rain must not yield a confident lag.
    df = synthetic(60)
    df["stage_ft"] = [1.0 + math.sin(i * 0.9) for i in range(len(df))]
    out = estimate_lag(df)
    assert out["lag_minutes"] is None
    assert out["correlation"] is not None
    assert "below" in out["reason"]


def test_periodic_storms_do_not_alias_to_a_later_cycle():
    """Regression: storms 450 min apart make lag L and lag L+450 score alike — the
    longer shift can even edge ahead, since it drops the edge storms that fit worst.
    The earliest lag that explains the response is the causal one."""
    df = synthetic(60)                            # storms spaced exactly 450 min
    out = estimate_lag(df)
    assert out["lag_minutes"] == 60, out
    assert out["lag_minutes"] != 510


def test_irregularly_spaced_storms_still_resolve():
    df = synthetic(60, n=400)
    rain = [0.0] * 400
    for start in (30, 145, 300):                  # deliberately uneven spacing
        for k in range(8):
            rain[start + k] = 0.6 - 0.05 * k
    lag_steps = int(60 * 60 / STEP)
    level, current = [], 1.0
    for i in range(400):
        current += (rain[i - lag_steps] if i - lag_steps >= 0 else 0.0) * 0.1 - 0.002
        current = max(1.0, current)
        level.append(current)
    df["upstream_rain_1h_in"], df["stage_ft"] = rain, level
    assert estimate_lag(df)["lag_minutes"] == 60


def test_handles_none_input():
    assert estimate_lag(None)["lag_minutes"] is None


def test_only_the_recent_window_is_fitted():
    """Cost grows with the dataset, and a lag averaged across seasons is the wrong
    number anyway — March snowmelt on saturated ground is not an August thunderstorm."""
    recent = synthetic(60, n=300)
    # Prepend a year of older samples with a deliberately different lag.
    old = synthetic(300, n=300)
    old["ts"] = old["ts"] - 400 * 86400
    df = pd.concat([old, recent], ignore_index=True)
    out = estimate_lag(df)
    assert out["lag_minutes"] == 60, out           # the stale 300 min fit is excluded
    assert out["samples"] < len(df)


def test_window_is_measured_from_the_newest_sample_not_wall_clock():
    # The dataset may be replayed or restored; anchoring to now() would discard it all.
    df = synthetic(60, n=300)
    df["ts"] = df["ts"] - 5 * 365 * 86400          # five years stale
    assert estimate_lag(df)["lag_minutes"] == 60


def test_persisted_lag_survives_a_restart():
    import tempfile
    d = Path(tempfile.mkdtemp())
    assert load_lag(d)["lag_minutes"] is None
    assert "pending" in load_lag(d)["reason"]      # safe to publish before any nightly run
    result = estimate_lag(synthetic(60))
    save_lag(d, result)
    assert load_lag(d)["lag_minutes"] == 60


def test_corrupt_lag_state_falls_back_to_the_placeholder():
    import tempfile
    d = Path(tempfile.mkdtemp())
    (d / "state").mkdir(parents=True)
    (d / "state" / "lag.json").write_text("{ not json", encoding="utf-8")
    assert load_lag(d)["lag_minutes"] is None


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
