"""Model training (spec §4/§5/§7 Phase 4: "recalibrate/retrain, version model
artifact, log skill metrics").

This is the piece `model.py` has been calling out as a stub since Phase 2
("Phase 4: actually load models/<version>.pkl here"). It is code-complete and
unit-tested against synthetic fixtures — see `tests/test_train.py` — but it has
never seen real data, because two things it depends on do not exist yet:

  * `stage_ft` and `rate_of_rise_in_min` are always None until the SEN0676 is
    mounted, and they are the only Warning-tier drivers (`tiers.py`). The label
    this module builds is therefore always False in the live system today, and
    `train()` will correctly refuse to produce a model — see `MIN_POSITIVE_LABELS`
    below — until real stage data exists to make that label mean something.
  * Even once it does, `min_events_for_ml` (spec §5) gates *this module being
    called at all* on ~10 captured storms (`storms.count()`), so the honest
    expectation is that this stays dormant for months after Phase 1 completes.

That is by design, not a bug to route around: a model trained on zero positive
examples is not "conservative", it is wrong, and the threshold estimate in
`model.py` is the correct answer until real storms exist to fit against.

--- Label: forward-window Warning exceedance ---------------------------------

Target: "will stage or rate-of-rise reach Warning-tier levels within the next
`HORIZON_MINUTES`". Reuses `tiers.WARNING_STAGE_FT` / `WARNING_RATE_OF_RISE_IN_MIN`
directly rather than duplicating the numbers, so tuning a tier threshold and
retraining stay in sync.

Only one of the three horizons spec §5 asks for (+30 min / +1 h / +3 h) is
built. Three models tripling the untested surface for no validation ability
before real storms exist is the wrong trade; +3 h is kept because it is the
one that gives the most lead time to act, which is the entire point of the
project. The others are future work once real data can actually distinguish
whether a shorter horizon is more useful.

`predicted_crest_ft` (stage regression) is deliberately not built here for the
same reason: with no creek gauge, there has never been a real `stage_ft`
sample to regress against, so a regressor would be exercising sklearn/xgboost
API surface, not modeling anything.

--- Why xgboost -----------------------------------------------------------

Chosen for one concrete, current reason: it handles missing feature values
natively. Most rows in the live dataset have gaps — Google Floods is unbuilt,
WU upstream needs 2 keys configured, and stage/rate-of-rise are always None
pre-hardware — and xgboost trains and predicts through NaN without imputation,
where a hand-rolled model would need a missing-value strategy invented for
data whose actual missingness pattern is not yet known. `requirements.txt`
already carries it for exactly this (Addendum A.2), and the Dockerfile's
`libgomp1` exists only to support it.

--- Serialization -----------------------------------------------------------

xgboost's native JSON format (`Booster.save_model`), not pickle. Spec Addendum
A.7 names `model-<version>.pkl`; this deviates deliberately. A pickle ties the
artifact to the exact xgboost build that wrote it and executes arbitrary code
on load, neither of which is a trade worth making for a format whose only
consumer is this same codebase. A `.meta.json` sidecar carries the feature
column order, since the booster itself does not.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import precision_score, recall_score, roc_auc_score

from .tiers import WARNING_RATE_OF_RISE_IN_MIN, WARNING_STAGE_FT

log = logging.getLogger("app.train")

HORIZON_MINUTES = 180          # +3h — see module docstring for why only one horizon

# A purge/embargo window around the chronological train/test split: rows within this
# many seconds of the split point are dropped from both sides. Without it, a storm
# straddling the split boundary would let the test set's label leak information the
# train set's features were built from (or vice versa) — the same row-level split you
# would use on i.i.d. data silently cheats on time series.
SPLIT_EMBARGO_SECONDS = HORIZON_MINUTES * 60
TEST_FRACTION = 0.2            # most recent 20% of the record, by time not row count

# Refuse to train below these. Both are about the label, not the feature count: a
# classifier fit on zero positives has learned "always predict no", which is not a
# threshold estimate with different math, it is a worse one with false confidence.
MIN_POSITIVE_LABELS = 2
MIN_TRAIN_ROWS = 50

# Numeric feature columns. Deliberately explicit rather than "every column in the
# frame": a stray column in the dataset (schema drift, a debugging field) must not
# silently become a model input just by existing.
FEATURE_COLUMNS = (
    "stage_ft", "rate_of_rise_in_min",
    "soil_moisture_mean_pct", "soil_moisture_near_house_pct",
    "soil_moisture_near_creek_pct", "ponding_flag",
    "rain_rate_in_hr", "rain_1h_in", "rain_3h_in", "rain_6h_in",
    "rain_24h_in", "rain_72h_in", "qpf_6h_in", "qpf_24h_in", "api_index_in",
    "upstream_rain_1h_in", "upstream_rain_3h_in", "upstream_rain_6h_in",
    "upstream_rain_24h_in", "upstream_rain_72h_in", "upstream_precip_today_in",
    "nwm_flow_cfs", "nwm_flow_max_cfs",
    "usgs_leggetts_gage_ft", "usgs_leggetts_flow_cfs", "usgs_leggetts_rise_3h_ft",
    "usgs_tunkhannock_gage_ft", "usgs_tunkhannock_flow_cfs",
    "usgs_tunkhannock_rise_3h_ft",
    "nws_flood_watch", "nws_flood_warning", "nws_flash_flood_warning",
    "nws_alert_count",
    "snow_water_equivalent_in", "temp_f", "rain_on_snow_flag",
    # 2g radar cell tracks. These were published and recorded from 0.13.0 but were
    # missing here, so the model could not see them — the one input that leads on the
    # dominant W/NW storm approach was excluded from the thing meant to predict it.
    "radar_cells_tracked", "radar_threat_cells", "radar_threat_eta_min",
    "radar_threat_max_dbz",
    # 2h WPC Excessive Rainfall Outlook. The only input that grades rain against what
    # the ground can absorb, on a day-scale horizon nothing else here reaches.
    "wpc_ero_day1_risk", "wpc_ero_day2_risk", "wpc_ero_day3_risk",
)
# Cast to 0/1 before handing to xgboost; everything else is already numeric-or-NaN.
BOOL_COLUMNS = ("ponding_flag", "rain_on_snow_flag")


@dataclass
class TrainResult:
    version: str
    metrics: dict
    model_path: Path
    meta_path: Path


def _danger_now(df):
    """Row-wise Warning condition — the un-shifted signal `label_forward` looks ahead
    over. Matches tiers.py's own Warning rule so a tuned threshold and a retrained
    model never quietly disagree about what "dangerous" means.

    Always returns a boolean Series aligned to `df.index`, even if stage_ft and
    rate_of_rise_in_min are both absent (not just all-NaN) — a synthetic fixture or a
    dataset predating one of these columns must get "never dangerous", not a crash.
    """
    danger = pd.Series(False, index=df.index)
    if "stage_ft" in df:
        danger = danger | (df["stage_ft"] >= WARNING_STAGE_FT).fillna(False)
    if "rate_of_rise_in_min" in df:
        danger = danger | (df["rate_of_rise_in_min"] >= WARNING_RATE_OF_RISE_IN_MIN).fillna(False)
    return danger


def label_forward(df, horizon_minutes: int = HORIZON_MINUTES):
    """1 if the Warning condition holds at any point in (t, t+horizon], else 0.

    Implemented as a reversed rolling max over `danger_now`, rather than an
    explicit nested loop over future rows: for evenly-sampled data the two are
    equivalent, and the rolling form is the one that does not get slower as the
    dataset grows. Requires `df` sorted by `ts` ascending — callers own that,
    since it is also required for the chronological split.
    """
    danger = _danger_now(df)
    window = f"{horizon_minutes}min"
    ts = pd.to_datetime(df["ts"], unit="s")
    # A forward-looking window is a backward window on the time-reversed series.
    reversed_danger = pd.Series(danger.to_numpy()[::-1], index=ts.iloc[::-1].to_numpy())
    # Exclude t itself — this is "will it happen next", not "is it happening now".
    forward_max = reversed_danger.rolling(window, closed="left").max()[::-1]
    return forward_max.fillna(0).astype(int).to_numpy()


def build_matrix(df):
    """(X, y, ts) from a feature-row DataFrame. `df` must be sorted by ts ascending."""
    for col in FEATURE_COLUMNS:
        if col not in df.columns:
            df = df.assign(**{col: np.nan})
    x = df[list(FEATURE_COLUMNS)].copy()
    for col in BOOL_COLUMNS:
        x[col] = x[col].astype("boolean").astype("Int8")  # nullable -> xgboost sees NaN
    y = label_forward(df)
    return x, y, df["ts"].to_numpy()


def _chronological_split(x, y, ts):
    """Train/test by time, with an embargo around the split (see module docstring).

    Returns (x_train, y_train, x_test, y_test); either half may be empty if the
    embargo consumes it, which callers must treat as "not evaluable" rather than
    erroring — a small dataset failing to produce a usable test split is expected,
    not exceptional.
    """
    order = np.argsort(ts, kind="stable")
    x, y, ts = x.iloc[order].reset_index(drop=True), y[order], ts[order]
    split_ts = np.quantile(ts, 1.0 - TEST_FRACTION)
    train_mask = ts < split_ts - SPLIT_EMBARGO_SECONDS
    test_mask = ts >= split_ts + SPLIT_EMBARGO_SECONDS
    return x[train_mask], y[train_mask], x[test_mask], y[test_mask]


def _skill_metrics(booster, x_test, y_test, ts_test, danger_test) -> dict:
    """Hit rate, false-alarm rate, and mean lead time on the held-out split.

    Every metric is None rather than a misleading 0.0 when the test split cannot
    support it — most commonly because it landed single-class, which is the norm
    rather than the exception when positives are as rare as flood events are.
    """
    if len(y_test) == 0:
        return {"note": "test split empty after embargo"}

    proba = booster.predict(xgb.DMatrix(x_test))
    pred = (proba >= 0.5).astype(int)

    metrics: dict = {"test_rows": int(len(y_test)), "test_positives": int(y_test.sum())}
    if len(set(y_test)) < 2:
        metrics["note"] = "test split is single-class; precision/recall/AUC undefined"
        return metrics

    metrics["hit_rate"] = round(float(recall_score(y_test, pred, zero_division=0)), 3)
    metrics["false_alarm_rate"] = round(
        float(1 - precision_score(y_test, pred, zero_division=0)) if pred.sum() else 0.0, 3)
    metrics["roc_auc"] = round(float(roc_auc_score(y_test, proba)), 3)

    # Lead time: for each row correctly flagged positive, how far ahead of the actual
    # onset was it? Found by walking forward from the row to the nearest later sample
    # where the raw (un-shifted) condition is true — the same lookup label_forward did,
    # kept separate here because a metric needs the moment danger arrived, not just
    # whether it did.
    danger_ts = ts_test[danger_test.to_numpy(dtype=bool)]
    leads = []
    for t, is_tp in zip(ts_test, (pred == 1) & (y_test == 1)):
        if not is_tp:
            continue
        future = danger_ts[danger_ts > t]
        if len(future):
            leads.append((future.min() - t) / 60.0)
    metrics["lead_time_minutes"] = round(float(np.mean(leads)), 1) if leads else None
    metrics["true_positives"] = len(leads)
    return metrics


def train(frame, data_dir: Path) -> TrainResult | None:
    """Fit a candidate model against the dataset. None (with a logged reason) if the
    data cannot support one yet — see MIN_POSITIVE_LABELS/MIN_TRAIN_ROWS.

    Writes `data_dir/models/model-<version>.json` (booster) and a `.meta.json`
    sidecar (feature order + horizon), and returns their paths alongside skill
    metrics — but does not touch the registry. Promoting a version from candidate
    to active is a human decision (the dashboard's Promote button /
    `ModelRegistry.promote`), not something a nightly job should do to itself.
    """
    frame = frame.sort_values("ts").reset_index(drop=True)
    x, y, ts = build_matrix(frame)
    danger = _danger_now(frame)

    if len(x) < MIN_TRAIN_ROWS:
        log.info("Skipping training: %d rows < MIN_TRAIN_ROWS=%d", len(x), MIN_TRAIN_ROWS)
        return None
    if int(y.sum()) < MIN_POSITIVE_LABELS:
        log.info("Skipping training: %d positive labels < MIN_POSITIVE_LABELS=%d "
                 "(expected until the creek gauge exists — see module docstring)",
                 int(y.sum()), MIN_POSITIVE_LABELS)
        return None

    x_train, y_train, x_test, y_test = _chronological_split(x, y, ts)
    if len(x_train) < MIN_TRAIN_ROWS or int(y_train.sum()) < 1:
        log.info("Skipping training: post-embargo train split too small or single-class")
        return None

    order = np.argsort(ts, kind="stable")
    ts_sorted, danger_sorted = ts[order], danger.iloc[order].reset_index(drop=True)
    test_start = len(x) - len(x_test)
    ts_test = ts_sorted[test_start:] if len(x_test) else ts_sorted[:0]
    danger_test = danger_sorted.iloc[test_start:] if len(x_test) else danger_sorted.iloc[:0]

    # scale_pos_weight rather than resampling: flood events are rare by nature (that is
    # the whole reason storms take months to accumulate), and up-weighting the minority
    # class costs nothing extra to compute or store, unlike synthesizing rows.
    n_pos, n_neg = int(y_train.sum()), int(len(y_train) - y_train.sum())
    booster = xgb.train(
        {"objective": "binary:logistic", "eval_metric": "logloss", "max_depth": 4,
         "eta": 0.1, "scale_pos_weight": (n_neg / n_pos) if n_pos else 1.0},
        xgb.DMatrix(x_train, label=y_train),
        num_boost_round=100,
    )

    metrics = _skill_metrics(booster, x_test, y_test, ts_test, danger_test)
    metrics.update({"horizon_minutes": HORIZON_MINUTES,
                    "train_rows": int(len(x_train)), "train_positives": n_pos})

    version = datetime.now(timezone.utc).strftime("gbm-%Y%m%dT%H%M%SZ")
    models_dir = data_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    model_path = models_dir / f"model-{version}.json"
    meta_path = models_dir / f"model-{version}.meta.json"
    booster.save_model(str(model_path))
    _write_meta(meta_path, version)

    log.info("Trained candidate %s: %s", version, metrics)
    return TrainResult(version=version, metrics=metrics, model_path=model_path,
                       meta_path=meta_path)


def _write_meta(path: Path, version: str) -> None:
    path.write_text(json.dumps({
        "version": version, "horizon_minutes": HORIZON_MINUTES,
        "feature_columns": list(FEATURE_COLUMNS),
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }), encoding="utf-8")


def load_artifact(data_dir: Path, version: str):
    """(booster, meta) for a saved version, or (None, None) if either file is missing
    or unreadable — a corrupt/partial artifact must fail open to the threshold
    estimate, not crash the fast loop that is trying to use it."""
    models_dir = data_dir / "models"
    model_path = models_dir / f"model-{version}.json"
    meta_path = models_dir / f"model-{version}.meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        booster = xgb.Booster()
        booster.load_model(str(model_path))
        return booster, meta
    except (FileNotFoundError, ValueError, OSError, xgb.core.XGBoostError) as exc:
        log.warning("could not load model artifact %s: %s", version, exc)
        return None, None
