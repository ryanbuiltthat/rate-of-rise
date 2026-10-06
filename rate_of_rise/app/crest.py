"""Predicted crest: how high the creek will get in the next 3 h, and how soon.

Why this exists. The project's question changed under it. It began as "will it flood?"
(train.py's Warning-crossing classifier), and that model cannot be validated until the
creek actually gets near the bank — months, maybe a season or more. What every storm
*does* answer is how much the creek came up, how fast, and when it topped out. rise.py
turned that into two yes/no probabilities; this turns it into the numbers themselves:

  * the crest — the highest the creek is expected to reach within HORIZON_MIN, as a
    stage with an 80 % range around it (10th/50th/90th percentile of the rise);
  * the time to that crest, in minutes.

Comparing a predicted crest against the surveyed geometry (Warning, Emergency and the bank
top, all fixed numbers in tiers.py) is then arithmetic, not something that needs a flood
in the record to learn. That is the route to a warning that does not wait for a disaster
to calibrate it.

What it is not, yet: a forecast of what the creek does out of its channel. It is fitted on
whatever rises the record holds — so far inches, from a creek near 1 ft. Every payload
carries the highest stage the training data ever reached, and `beyond_training` says when
the upper end of the range is past it: from there on the number is an extrapolation, and
the further past, the less it should be leaned on. It never drives the alert tiers.

--- Labels ------------------------------------------------------------------------------

For row t with an accepted stage, over the window (t, t + HORIZON_MIN]:
  rise_in            max(0, highest accepted stage in the window − stage at t), in inches
  time_to_crest_min  minutes from t to that highest reading

Unknown (NaN, left out) on the same terms as rise.label_rise: no accepted stage at t, or
fewer than MIN_WINDOW_COVERAGE of the window's readings. Time to crest is only learned from
rows whose rise is at least MIN_RISE_IN: when the creek is flat, "when is the top" is the
noise floor's answer, not the creek's.

--- Evaluation --------------------------------------------------------------------------

Leave-one-storm-out, with rise.py's grouping, for the reason given there: hundreds of
near-identical rows per storm make any row-level split a test on the training storm.
The numbers to watch are `skill` (median-rise error against always quoting the typical
rise), `rise_rows_skill` (the same on rows where the creek really did come up, against
saying "no further rise" — where the number matters) and `episodes_within_tolerance` (rises
whose crest it called within TOLERANCE_IN just before the creek started up).

--- Model -------------------------------------------------------------------------------

xgboost, kept as small as rise.py's models and on the same inputs (rise.FEATURES), with a
quantile objective so one booster yields the low, middle and high of the range.
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

from . import rise
from .tiers import BANK_TOP_FT, EMERGENCY_STAGE_FT, WARNING_STAGE_FT

log = logging.getLogger("app.crest")

HORIZON_MIN = 180
QUANTILES = (0.1, 0.5, 0.9)
# A rise smaller than this is the creek sitting still; it is not a crest to time, and it
# is what counts as an episode for the training gate and the held-out scoring.
MIN_RISE_IN = 0.5
# A held-out rise counts as "called" when the median crest, at the moment the most of that
# rise still lay ahead (just before the creek started up), was within this of what happened.
TOLERANCE_IN = 1.0

_BASE = {"max_depth": 2, "eta": 0.05, "subsample": 0.8, "min_child_weight": 5, "seed": 0}
# min_child_weight is a hessian sum, and the quantile loss's hessian is not rise.py's
# logistic one: at rise.py's 5 the trees could not separate "rain falling, creek not up
# yet" from the rest, and held-out skill on the fabricated storms fell by two thirds.
RISE_PARAMS = {**_BASE, "objective": "reg:quantileerror",
               "quantile_alpha": np.array(QUANTILES), "min_child_weight": 1}
TIME_PARAMS = {**_BASE, "objective": "reg:absoluteerror"}
ROUNDS = 150
KEEP_ARTIFACTS = 3

# Highest named level first, so the first one a crest reaches is the one reported.
LEVELS = (("bank", BANK_TOP_FT), ("emergency", EMERGENCY_STAGE_FT),
          ("warning", WARNING_STAGE_FT))


@dataclass
class CrestResult:
    version: str | None                 # None: no model could be fitted — see `reason`
    metrics: dict = field(default_factory=dict)
    reason: str | None = None


# --- labels ------------------------------------------------------------------------------

def label_crest(df: pd.DataFrame, horizon_min: int = HORIZON_MIN,
                stage: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(rise_in, time_to_crest_min) per row — see the module docstring. `df` sorted by ts."""
    ts = df["ts"].to_numpy(dtype=float)
    stage = rise.accepted_stage(df) if stage is None else stage
    rise_in = np.full(len(ts), np.nan)
    when = np.full(len(ts), np.nan)
    if len(ts) < 2:
        return rise_in, when
    step = float(np.median(np.diff(ts)))
    expected = max(horizon_min * 60.0 / step, 1.0) if step > 0 else 1.0
    good = ~np.isnan(stage)
    gts, gst = ts[good], stage[good]
    horizon_s = horizon_min * 60.0
    for i in np.flatnonzero(good):
        lo = int(np.searchsorted(gts, ts[i], side="right"))
        hi = int(np.searchsorted(gts, ts[i] + horizon_s, side="right"))
        if hi - lo < rise.MIN_WINDOW_COVERAGE * expected:
            continue
        k = lo + int(np.argmax(gst[lo:hi]))
        rise_in[i] = max(0.0, (gst[k] - stage[i]) * 12.0)
        when[i] = (gts[k] - ts[i]) / 60.0
    return rise_in, when


# --- evaluation --------------------------------------------------------------------------

def _fit_rise(x, y):
    return xgb.train(RISE_PARAMS, xgb.DMatrix(x, label=y), num_boost_round=ROUNDS)


def _fit_time(x, y):
    return xgb.train(TIME_PARAMS, xgb.DMatrix(x, label=y), num_boost_round=ROUNDS)


def _quantiles(booster, x) -> np.ndarray:
    """(n, 3) low/mid/high rise in inches, never negative, never crossing."""
    q = np.asarray(booster.predict(xgb.DMatrix(x)), dtype=float).reshape(len(x), -1)
    return np.sort(np.clip(q, 0.0, None), axis=1)


def _held_out(x, rise_in, when, groups) -> tuple[np.ndarray, np.ndarray]:
    q = np.full((len(rise_in), len(QUANTILES)), np.nan)
    t = np.full(len(rise_in), np.nan)
    rising = rise_in >= MIN_RISE_IN
    for gid in np.unique(groups):
        test = groups == gid
        train = ~test
        if train.sum() < 10 or not (rising & train).any():
            continue
        q[test] = _quantiles(_fit_rise(x[train], rise_in[train]), x[test])
        if (rising & train).sum() >= rise.MIN_POSITIVE_ROWS:
            t[test] = _fit_time(x[rising & train], when[rising & train]).predict(
                xgb.DMatrix(x[test]))
    return q, t


def _metrics(ts, rise_in, when, q, t, episodes) -> dict:
    metrics: dict = {"rows": int(len(rise_in)),
                     "rising_rows": int((rise_in >= MIN_RISE_IN).sum()),
                     "episodes": len(episodes)}
    scored = ~np.isnan(q[:, 1])
    if not scored.any():
        metrics["note"] = "held-out predictions could not be scored"
        return metrics
    y, mid = rise_in[scored], q[scored, 1]
    mae = float(np.mean(np.abs(mid - y)))
    clim = float(np.mean(np.abs(np.median(y) - y)))
    metrics.update({
        "mae_in": round(mae, 3),
        "mae_climatology_in": round(clim, 3),
        # >0: better than always quoting the typical rise. The single number to watch.
        "skill": round(1 - mae / clim, 3) if clim > 0 else None,
        # Share of held-out rows whose real rise fell inside the 80 % range. ~0.8 is honest;
        # well under it means the range is too narrow to trust.
        "range_coverage": round(float(np.mean((y >= q[scored, 0]) & (y <= q[scored, 2]))), 3),
    })
    rising = scored & (rise_in >= MIN_RISE_IN)
    if rising.any():
        r_mae = float(np.mean(np.abs(q[rising, 1] - rise_in[rising])))
        metrics["rise_rows_mae_in"] = round(r_mae, 3)
        # Against saying "no further rise" — what the stage alone would tell you. Rows hours
        # before the rain starts are in here too and nothing can call those, so this stays
        # well short of 1 even for a good model; above 0 is what matters.
        metrics["rise_rows_skill"] = round(1 - r_mae / float(np.mean(rise_in[rising])), 3)
    called = 0
    for s, e in episodes:
        rows = np.flatnonzero((ts >= s) & (ts <= e) & scored & (rise_in >= MIN_RISE_IN))
        if len(rows):
            k = rows[int(np.argmax(rise_in[rows]))]
            called += bool(abs(q[k, 1] - rise_in[k]) <= TOLERANCE_IN)
    metrics["episodes_within_tolerance"] = called
    timed = rising & ~np.isnan(t)
    if timed.any():
        metrics["time_mae_min"] = round(float(np.mean(np.abs(t[timed] - when[timed]))), 1)
        metrics["time_mae_climatology_min"] = round(
            float(np.mean(np.abs(np.median(when[timed]) - when[timed]))), 1)
    return metrics


def is_trustworthy(metrics: dict) -> bool:
    """Tested on at least two rises it never saw, beat both quoting the typical rise and
    saying "no further rise", and got at least one crest within TOLERANCE_IN."""
    return (metrics.get("episodes", 0) >= rise.MIN_EPISODES
            and (metrics.get("skill") or 0) > 0
            and (metrics.get("rise_rows_skill") or 0) > 0
            and (metrics.get("episodes_within_tolerance") or 0) >= 1)


# --- training ------------------------------------------------------------------------

def train_crest(frame: pd.DataFrame, data_dir: Path, storm_windows=(),
                confirm_samples: int = 2, stage_max_age_minutes: float = 6.0,
                horizon_min: int = HORIZON_MIN) -> CrestResult:
    """Fit, score and save the crest models. Never raises for want of data: a CrestResult
    with `version=None` and a `reason` says why there is no model."""
    result = CrestResult(version=None)
    if frame is None or len(frame) < 2 or "ts" not in frame:
        result.reason = "no data yet"
        return result
    df = frame.sort_values("ts").reset_index(drop=True)
    stage = rise.accepted_stage(df, stage_max_age_minutes)
    rise_all, when_all = label_crest(df, horizon_min, stage)
    x_all = rise.feature_frame(df, confirm_samples, stage)
    keep = ~np.isnan(rise_all)
    x = x_all[keep].reset_index(drop=True)
    rise_in, when = rise_all[keep], when_all[keep]
    ts = df["ts"].to_numpy(float)[keep]
    rising = rise_in >= MIN_RISE_IN
    episodes = rise._episodes(ts, rising.astype(float))
    if int(rising.sum()) < rise.MIN_POSITIVE_ROWS or len(episodes) < rise.MIN_EPISODES:
        result.reason = (f"not enough rises of {MIN_RISE_IN:g} in or more yet: "
                         f"{len(episodes)} rise(s), {int(rising.sum())} row(s) (need "
                         f"{rise.MIN_EPISODES} and {rise.MIN_POSITIVE_ROWS})")
        result.metrics = {"rows": int(len(rise_in)), "rising_rows": int(rising.sum()),
                          "episodes": len(episodes)}
        return result

    windows = [(float(s), float(e)) for s, e in storm_windows]
    groups = rise._groups(ts, episodes, windows)
    q, t = _held_out(x, rise_in, when, groups)
    metrics = _metrics(ts, rise_in, when, q, t, episodes)
    metrics["trustworthy"] = is_trustworthy(metrics)

    version = datetime.now(timezone.utc).strftime("crest-%Y%m%dT%H%M%SZ")
    out_dir = data_dir / "models" / "crest"
    out_dir.mkdir(parents=True, exist_ok=True)
    _fit_rise(x, rise_in).save_model(str(out_dir / f"{version}.json"))
    _fit_time(x[rising], when[rising]).save_model(str(out_dir / f"{version}.time.json"))
    meta = {"version": version, "horizon_min": horizon_min, "quantiles": list(QUANTILES),
            "features": list(rise.FEATURES), "metrics": metrics,
            # The highest stage this model has ever been shown the creek reach. Above it
            # every prediction is an extrapolation.
            "max_trained_stage_ft": round(float(np.nanmax(stage)), 3),
            "trained_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    (out_dir / f"{version}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    _prune(out_dir)
    log.info("Trained crest model %s (%d min): %s", version, horizon_min, metrics)
    result.version, result.metrics = version, metrics
    return result


def _prune(out_dir: Path) -> None:
    metas = sorted(out_dir.glob("crest-*.meta.json"))
    for old in metas[:-KEEP_ARTIFACTS]:
        stem = old.name.removesuffix(".meta.json")
        for path in (old, out_dir / f"{stem}.json", out_dir / f"{stem}.time.json"):
            path.unlink(missing_ok=True)


# --- inference -----------------------------------------------------------------------

def level_reached(stage_ft: float | None) -> str | None:
    """The highest named level ("bank", "emergency", "warning") a stage reaches, or None."""
    if stage_ft is None:
        return None
    for name, level in LEVELS:
        if stage_ft >= level:
            return name
    return None


class CrestModel:
    """The newest crest model and what it says for a live row."""

    def __init__(self, data_dir: Path, confirm_samples: int = 2):
        self._dir = data_dir / "models" / "crest"
        self._confirm = confirm_samples
        self._loaded: tuple[xgb.Booster, xgb.Booster, dict] | None = None
        self._reason: str | None = None
        self.reload()

    def reload(self) -> None:
        self._loaded = None
        metas = sorted(self._dir.glob("crest-*.meta.json"))
        if not metas:
            self._reason = self._reason or "no model trained yet"
            return
        try:
            meta = json.loads(metas[-1].read_text(encoding="utf-8"))
            boosters = []
            for suffix in ("json", "time.json"):
                b = xgb.Booster()
                b.load_model(str(self._dir / f"{meta['version']}.{suffix}"))
                boosters.append(b)
            self._loaded = (boosters[0], boosters[1], meta)
            self._reason = None
        except (OSError, ValueError, KeyError, xgb.core.XGBoostError) as exc:
            log.warning("crest model unreadable: %s", exc)
            self._reason = "model file unreadable"

    def needs_training(self) -> bool:
        return self._loaded is None

    def set_reason(self, reason: str | None) -> None:
        if reason:
            self._reason = reason

    def predict(self, row) -> dict:
        """The `predicted_crest` payload. Never raises."""
        values = row.as_dict() if hasattr(row, "as_dict") else dict(row)
        payload = {"value": None, "low_ft": None, "high_ft": None, "rise_in": None,
                   "rise_low_in": None, "rise_high_in": None, "time_to_crest_min": None,
                   "reaches": None, "may_reach": None, "beyond_training": None,
                   "max_trained_stage_ft": None, "horizon_min": HORIZON_MIN,
                   "warning_ft": WARNING_STAGE_FT, "emergency_ft": EMERGENCY_STAGE_FT,
                   "bank_top_ft": round(BANK_TOP_FT, 3), "version": None,
                   "trained_at": None, "trustworthy": False, "metrics": {},
                   "reason": self._reason}
        if self._loaded is None:
            return payload
        rise_b, time_b, meta = self._loaded
        payload.update(version=meta["version"], trained_at=meta.get("trained_at"),
                       metrics=meta.get("metrics", {}),
                       trustworthy=bool(meta.get("metrics", {}).get("trustworthy")),
                       max_trained_stage_ft=meta.get("max_trained_stage_ft"), reason=None)
        stage = values.get("stage_ft")
        if stage is None:
            payload["reason"] = "no current stage"
            return payload
        if values.get("stage_held"):
            # A crest added to a stage from before the outage would look current and is not.
            payload["reason"] = "creek gauge offline — no current stage to build on"
            return payload
        try:
            x = rise.feature_frame(pd.DataFrame([values]), self._confirm)
            x = x[meta.get("features", list(rise.FEATURES))]
            lo, mid, hi = (float(v) for v in _quantiles(rise_b, x)[0])
            minutes = max(0.0, float(time_b.predict(xgb.DMatrix(x))[0]))
        except Exception:   # a model that cannot answer must not stop the loop
            log.exception("crest prediction failed for %s", meta["version"])
            payload["reason"] = "prediction failed (see log)"
            return payload
        crest, low, high = (float(stage) + r / 12.0 for r in (mid, lo, hi))
        top = meta.get("max_trained_stage_ft")
        payload.update(
            value=round(crest, 2), low_ft=round(low, 2), high_ft=round(high, 2),
            rise_in=round(float(mid), 1), rise_low_in=round(float(lo), 1),
            rise_high_in=round(float(hi), 1),
            # Timing is the noise floor's answer when the creek is not expected to move.
            time_to_crest_min=round(minutes) if mid >= MIN_RISE_IN else None,
            reaches=level_reached(crest), may_reach=level_reached(high),
            beyond_training=None if top is None else bool(high > top))
        return payload
