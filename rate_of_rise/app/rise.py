"""Rise probability: the chance the creek climbs X inches within the next 1 h and 3 h.

Why this exists alongside train.py. train.py's model answers "will the creek reach
Warning in the next 3 h?", and in September 2026 the honest answer to "how many times has
that happened?" was never: every positive it had ever trained on was a radio or radar
artifact, and once those were masked it had nothing to learn from. A season can pass that
way. This asks a question every storm answers instead — did the creek come up by X inches
within the hour (or three)? — so each rain event is evidence, whether or not it gets near
the bank.

What it is not: a flood model. It is fitted on rises of an inch or two from a creek
sitting near 1 ft, and what the creek does out of its channel is not in that data. It
never drives the alert tiers; it is published for people to read, next to the stage.

--- Label -------------------------------------------------------------------------------

Row t is positive when the highest accepted stage in (t, t + horizon] is at least
`threshold_in` above the stage at t. Unknown (NaN, and left out of training) when stage at
t was not accepted, or when fewer than MIN_WINDOW_COVERAGE of the window's expected
readings were — a rise that happened while the radio was down is not evidence of no rise.
"Accepted" is the live system's rule: the node was reporting and the reading passed the
plausibility check (train.implausible_stage_mask).

--- Evaluation --------------------------------------------------------------------------

A 5-minute series has hundreds of near-identical rows per storm, so any row-level split
lets the model be tested on the storm it was trained on. Every metric here is scored
leave-one-storm-out instead: each storm (from the storm log, padded by STORM_PAD_S), and
each rise the log missed, is predicted by a model that never saw it; dry stretches are
split into a few contiguous blocks. With a handful of storms these numbers are thin, and
the metrics say how thin (`episodes`).

--- Model -------------------------------------------------------------------------------

xgboost for the same reason as train.py (it trains and predicts through missing values
natively), but deliberately small — depth 2, and a short, physically motivated feature
list — because a few storms cannot support forty features. No class weighting: the
probability is what gets published, so it has to be the probability, not a ranking.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import brier_score_loss, roc_auc_score

from .features import stage_history_features
from .train import implausible_stage_mask

log = logging.getLogger("app.rise")

HORIZONS_MIN = (60, 180)
LABELS = {60: "1h", 180: "3h"}

# Inputs, in order. Rain where it falls and upstream, the forecast, how wet the basin
# already is, an inbound radar cell, and the creek's own recent movement. The stage-history
# pair is what separates "rain and no response yet" from "already answered": without it the
# rows just after a crest — still raining, creek done — read as the start of another rise.
FEATURES = (
    "rain_1h_in", "rain_3h_in", "upstream_rain_1h_in", "upstream_rain_3h_in",
    "qpf_6h_in", "soil_moisture_mean_pct", "api_index_in", "radar_threat_eta_min",
    "rate_of_rise_in_min", "stage_change_1h_in", "stage_above_6h_low_in",
)

# A window with fewer than this share of its expected readings is not labelled.
MIN_WINDOW_COVERAGE = 0.5
# Refuse to fit below these: rows and separate rises, because 30 positive rows from one
# storm are one example, not thirty.
MIN_POSITIVE_ROWS = 6
MIN_EPISODES = 2
# Positive rows further apart than this belong to different rises.
EPISODE_GAP_S = 3 * 3600.0
STORM_PAD_S = 6 * 3600.0
DRY_FOLDS = 4
# Beyond this many held-out groups, storms are dealt into this many folds instead, so a
# year of storms does not mean a year of refits every night.
MAX_FOLDS = 12

XGB_PARAMS = {"objective": "binary:logistic", "eval_metric": "logloss", "max_depth": 2,
              "eta": 0.05, "subsample": 0.8, "min_child_weight": 5, "seed": 0}
ROUNDS = 150

# Old artifacts kept per horizon; the rest are deleted after a successful fit.
KEEP_ARTIFACTS = 3


@dataclass
class RiseResult:
    horizon_min: int
    threshold_in: float
    version: str | None                 # None: no model could be fitted — see `reason`
    metrics: dict = field(default_factory=dict)
    reason: str | None = None


# --- frame preparation -----------------------------------------------------------------

def accepted_stage(df: pd.DataFrame, stage_max_age_minutes: float = 6.0) -> np.ndarray:
    """stage_ft where the live system would have believed it, NaN elsewhere.

    Mirrors FeatureBuilder: a reading counts when the node was online (or, with no link
    sensor, the reading was fresh) and it passed the plausibility check. `df` sorted by ts.
    """
    if "stage_ft" not in df:
        return np.full(len(df), np.nan)
    stage = pd.to_numeric(df["stage_ft"], errors="coerce").to_numpy(dtype=float).copy()
    # A copy: under pandas copy-on-write (the default from 3.0) to_numpy() hands back a
    # read-only view, and the |= below would raise.
    bad = implausible_stage_mask(df).to_numpy(dtype=bool, copy=True)
    if "creek_node_online" in df:
        online = df["creek_node_online"].astype("boolean")
        bad |= online.eq(False).fillna(False).to_numpy(dtype=bool)
        if "stage_age_min" in df:
            age = pd.to_numeric(df["stage_age_min"], errors="coerce")
            stale = online.isna().to_numpy() & (age > stage_max_age_minutes).fillna(False).to_numpy()
            bad |= stale
    stage[bad] = np.nan
    return stage


def label_rise(df: pd.DataFrame, horizon_min: int, threshold_in: float,
               stage: np.ndarray | None = None) -> np.ndarray:
    """1.0 / 0.0 / NaN per row — see the module docstring. `df` sorted by ts."""
    ts = df["ts"].to_numpy(dtype=float)
    stage = accepted_stage(df) if stage is None else stage
    y = np.full(len(ts), np.nan)
    if len(ts) < 2:
        return y
    step = float(np.median(np.diff(ts)))
    expected = max(horizon_min * 60.0 / step, 1.0) if step > 0 else 1.0
    good = ~np.isnan(stage)
    gts, gst = ts[good], stage[good]
    horizon_s = horizon_min * 60.0
    for i in np.flatnonzero(good):
        lo = int(np.searchsorted(gts, ts[i], side="right"))
        hi = int(np.searchsorted(gts, ts[i] + horizon_s, side="right"))
        if hi - lo < MIN_WINDOW_COVERAGE * expected:
            continue
        rise_in = (gst[lo:hi].max() - stage[i]) * 12.0
        y[i] = 1.0 if rise_in >= threshold_in else 0.0
    return y


def feature_frame(df: pd.DataFrame, confirm_samples: int = 2,
                  stage: np.ndarray | None = None) -> pd.DataFrame:
    """The model's inputs, from dataset rows (training) or a one-row live frame.

    Stage history is recomputed from `stage` when it is given (training: the dataset's
    stored columns do not exist before 0.24.0, and recomputing keeps old and new rows on
    one definition); otherwise the row's own values are used (live, where FeatureBuilder
    computed them with the same function). A rate of rise counts only once it has
    `confirm_samples` gap-free samples behind it, as for Warning labels in train.py —
    rows without a count predate the dropout guard, which is where the reconnect spike is.
    """
    out = pd.DataFrame(index=df.index)
    for col in FEATURES:
        out[col] = pd.to_numeric(df[col], errors="coerce") if col in df else np.nan
    if stage is not None:
        change, above = stage_history_features(df["ts"].to_numpy(dtype=float), stage)
        out["stage_change_1h_in"], out["stage_above_6h_low_in"] = change, above
        out.loc[np.isnan(stage), "rate_of_rise_in_min"] = np.nan
    count = (pd.to_numeric(df["rate_of_rise_sample_count"], errors="coerce")
             if "rate_of_rise_sample_count" in df else pd.Series(np.nan, index=df.index))
    out.loc[~(count >= confirm_samples).fillna(False), "rate_of_rise_in_min"] = np.nan
    return out.astype("float64")


# --- evaluation --------------------------------------------------------------------------

def _episodes(ts: np.ndarray, y: np.ndarray) -> list[tuple[float, float]]:
    """(first, last) timestamp of each run of positive rows."""
    pos = ts[y == 1]
    if not len(pos):
        return []
    breaks = np.flatnonzero(np.diff(pos) > EPISODE_GAP_S)
    starts = np.r_[pos[0], pos[breaks + 1]]
    ends = np.r_[pos[breaks], pos[-1]]
    return list(zip(starts, ends))


def _groups(ts: np.ndarray, episodes, storm_windows) -> np.ndarray:
    """A held-out group per row: each storm window, each rise outside every window, and
    DRY_FOLDS contiguous blocks of what is left."""
    g = np.full(len(ts), -1)
    windows = [(s - STORM_PAD_S, e + STORM_PAD_S) for s, e in storm_windows]
    for s, e in episodes:
        if not any(ws <= s <= we or ws <= e <= we for ws, we in windows):
            windows.append((s - STORM_PAD_S, e + STORM_PAD_S))
    windows.sort()
    gid = 0
    for ws, we in windows:
        m = (ts >= ws) & (ts <= we) & (g == -1)
        if m.any():
            g[m] = gid
            gid += 1
    n_event = gid
    if n_event > MAX_FOLDS - DRY_FOLDS:          # deal storms round-robin into folds
        folds = MAX_FOLDS - DRY_FOLDS
        g[g >= 0] = g[g >= 0] % folds
        n_event = folds
    dry = np.flatnonzero(g == -1)
    for k, block in enumerate(np.array_split(dry, DRY_FOLDS)):
        g[block] = n_event + k
    return g


def _fit(x: pd.DataFrame, y: np.ndarray):
    return xgb.train(XGB_PARAMS, xgb.DMatrix(x, label=y), num_boost_round=ROUNDS)


def _held_out(x: pd.DataFrame, y: np.ndarray, groups: np.ndarray) -> np.ndarray:
    p = np.full(len(y), np.nan)
    for gid in np.unique(groups):
        test = groups == gid
        train = ~test
        if y[train].sum() < 1 or train.sum() < 10:
            continue
        p[test] = _fit(x[train], y[train]).predict(xgb.DMatrix(x[test]))
    return p


def _metrics(ts, y, p, episodes, storm_windows) -> dict:
    scored = ~np.isnan(p)
    metrics: dict = {"rows": int(len(y)), "positive_rows": int(y.sum()),
                     "episodes": len(episodes)}
    if scored.sum() == 0 or len(set(y[scored])) < 2:
        metrics["note"] = "held-out predictions could not be scored (single class)"
        return metrics
    ys, ps = y[scored], p[scored]
    brier = brier_score_loss(ys, ps)
    clim = brier_score_loss(ys, np.full(len(ys), ys.mean()))
    metrics.update({
        "roc_auc": round(float(roc_auc_score(ys, ps)), 3),
        "brier": round(float(brier), 4),
        "brier_climatology": round(float(clim), 4),
        # >0: better than always quoting the base rate. The single number to watch.
        "brier_skill": round(float(1 - brier / clim), 3) if clim > 0 else None,
    })
    # Per rise: did the model that never saw it say >= 50 % on a row whose window held it?
    metrics["episodes_flagged"] = sum(
        bool(((ts >= s) & (ts <= e) & scored & (y == 1) & (p >= 0.5)).any())
        for s, e in episodes)
    # False alarms where nothing was going on at all: outside every storm and every rise.
    quiet = scored & (y == 0)
    for s, e in list(storm_windows) + list(episodes):
        quiet &= ~((ts >= s - STORM_PAD_S) & (ts <= e + STORM_PAD_S))
    metrics["quiet_rows"] = int(quiet.sum())
    metrics["quiet_false_alarms"] = int((quiet & (p >= 0.5)).sum())
    return metrics


def is_trustworthy(metrics: dict) -> bool:
    """Enough to read the number as a probability rather than a curiosity: it has been
    tested on at least two rises it never saw, beat the base rate, and caught one."""
    return (metrics.get("episodes", 0) >= MIN_EPISODES
            and (metrics.get("brier_skill") or 0) > 0
            and (metrics.get("episodes_flagged") or 0) >= 1)


# --- training ------------------------------------------------------------------------

def train_rise(frame: pd.DataFrame, data_dir: Path, horizon_min: int, threshold_in: float,
               storm_windows=(), confirm_samples: int = 2,
               stage_max_age_minutes: float = 6.0) -> RiseResult:
    """Fit, score and save one horizon's model. Never raises for want of data: a
    RiseResult with `version=None` and a `reason` says why there is no model."""
    result = RiseResult(horizon_min=horizon_min, threshold_in=threshold_in, version=None)
    if frame is None or len(frame) < 2 or "ts" not in frame:
        result.reason = "no data yet"
        return result
    df = frame.sort_values("ts").reset_index(drop=True)
    stage = accepted_stage(df, stage_max_age_minutes)
    y_all = label_rise(df, horizon_min, threshold_in, stage)
    x_all = feature_frame(df, confirm_samples, stage)
    keep = ~np.isnan(y_all)
    x, y, ts = x_all[keep].reset_index(drop=True), y_all[keep], df["ts"].to_numpy(float)[keep]
    episodes = _episodes(ts, y)
    if int(y.sum()) < MIN_POSITIVE_ROWS or len(episodes) < MIN_EPISODES:
        result.reason = (f"not enough rises of {threshold_in:g} in within "
                         f"{LABELS.get(horizon_min, horizon_min)} yet: {len(episodes)} "
                         f"rise(s), {int(y.sum())} row(s) (need {MIN_EPISODES} and "
                         f"{MIN_POSITIVE_ROWS})")
        result.metrics = {"rows": int(len(y)), "positive_rows": int(y.sum()),
                          "episodes": len(episodes)}
        return result

    windows = [(float(s), float(e)) for s, e in storm_windows]
    groups = _groups(ts, episodes, windows)
    held_out = _held_out(x, y, groups)
    metrics = _metrics(ts, y, held_out, episodes, windows)
    metrics["base_rate"] = round(float(y.mean()), 4)
    metrics["trustworthy"] = is_trustworthy(metrics)

    booster = _fit(x, y)
    version = datetime.now(timezone.utc).strftime(f"rise{LABELS.get(horizon_min, horizon_min)}-%Y%m%dT%H%M%SZ")
    out_dir = data_dir / "models" / "rise"
    out_dir.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out_dir / f"{version}.json"))
    meta = {"version": version, "horizon_min": horizon_min, "threshold_in": threshold_in,
            "features": list(FEATURES), "metrics": metrics,
            "trained_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    (out_dir / f"{version}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    _prune(out_dir, horizon_min)
    log.info("Trained rise model %s (>= %g in within %d min): %s",
             version, threshold_in, horizon_min, metrics)
    result.version, result.metrics = version, metrics
    return result


def _prune(out_dir: Path, horizon_min: int) -> None:
    prefix = f"rise{LABELS.get(horizon_min, horizon_min)}-"
    metas = sorted(out_dir.glob(f"{prefix}*.meta.json"))
    for old in metas[:-KEEP_ARTIFACTS]:
        stem = old.name.removesuffix(".meta.json")
        for path in (old, out_dir / f"{stem}.json"):
            path.unlink(missing_ok=True)


# --- inference -----------------------------------------------------------------------

class RiseModels:
    """The newest model per horizon, and the probability each gives for a live row.

    A model trained for a different threshold than the one configured is not used: its
    number would answer a question nobody is asking. `needs_training()` reports that (or a
    missing model) so the service can refit at startup instead of waiting for the night.
    """

    def __init__(self, data_dir: Path, thresholds: dict[int, float], confirm_samples: int = 2):
        self._dir = data_dir / "models" / "rise"
        self._thresholds = dict(thresholds)
        self._confirm = confirm_samples
        self._loaded: dict[int, tuple[xgb.Booster, dict]] = {}
        self._reasons: dict[int, str] = {}
        self.reload()

    def reload(self) -> None:
        self._loaded.clear()
        for h in HORIZONS_MIN:
            metas = sorted(self._dir.glob(f"rise{LABELS[h]}-*.meta.json"))
            if not metas:
                self._reasons.setdefault(h, "no model trained yet")
                continue
            try:
                meta = json.loads(metas[-1].read_text(encoding="utf-8"))
                if float(meta["threshold_in"]) != float(self._thresholds[h]):
                    self._reasons[h] = (f"model was trained for {meta['threshold_in']:g} in, "
                                        f"not the configured {self._thresholds[h]:g} in — "
                                        f"retraining")
                    continue
                booster = xgb.Booster()
                booster.load_model(str(self._dir / f"{meta['version']}.json"))
                self._loaded[h] = (booster, meta)
                self._reasons.pop(h, None)
            except (OSError, ValueError, KeyError, xgb.core.XGBoostError) as exc:
                log.warning("rise model for %d min unreadable: %s", h, exc)
                self._reasons[h] = "model file unreadable"

    def needs_training(self) -> bool:
        return any(h not in self._loaded for h in HORIZONS_MIN)

    def set_reason(self, horizon_min: int, reason: str | None) -> None:
        if reason:
            self._reasons[horizon_min] = reason

    def predict(self, row) -> dict[int, dict]:
        """{horizon_min: payload} for publishing. Never raises."""
        values = row.as_dict() if hasattr(row, "as_dict") else dict(row)
        out = {}
        for h in HORIZONS_MIN:
            payload = {"value": None, "horizon_min": h, "threshold_in": self._thresholds[h],
                       "version": None, "trained_at": None, "trustworthy": False,
                       "metrics": {}, "reason": self._reasons.get(h)}
            if h in self._loaded:
                booster, meta = self._loaded[h]
                payload.update(version=meta["version"], trained_at=meta.get("trained_at"),
                               metrics=meta.get("metrics", {}),
                               trustworthy=bool(meta.get("metrics", {}).get("trustworthy")),
                               reason=None)
                try:
                    x = feature_frame(pd.DataFrame([values]), self._confirm)
                    x = x[meta.get("features", list(FEATURES))]
                    payload["value"] = round(float(booster.predict(xgb.DMatrix(x))[0]), 3)
                except Exception:   # a model that cannot answer must not stop the loop
                    log.exception("rise prediction failed for %s", meta["version"])
                    payload["reason"] = "prediction failed (see log)"
            out[h] = payload
        return out
