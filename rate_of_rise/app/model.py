"""Inference + model registry.

Until >= `min_events_for_ml` storms are captured (spec §5), this returns a
transparent, conservative *threshold* estimate rather than an ML prediction —
early months are data-collection + threshold alerting only. The ML path
(`train.py`) slots in once the registry names a promoted artifact; the caller
interface (`predict()` returning a `Prediction`) does not change either way, so
nothing downstream needs to know which path answered.

Two questions are kept apart here, because conflating them is how an unscored model
ended up raising Tier 4 on a dry evening (2026-09-13):

  * `predict()` answers "what probability should the alert tiers use?" — the threshold
    estimate, unless a model is promoted, past the event gate, AND the operator has set
    `ml_drives_alerts`.
  * `shadow()` answers "what does the ML model say?" — the active model, or failing that
    the newest candidate — whether or not it is allowed near the alarm. It is published and
    recorded every loop, which is the only way to watch a model through a real storm before
    trusting it with one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from . import train as trainer
from .config import Config
from .features import FeatureRow
from .registry import ModelRegistry

log = logging.getLogger("app.model")


@dataclass
class Prediction:
    flood_probability: float        # 0..1
    predicted_crest_ft: float | None
    lag_estimate_min: float | None
    method: str                     # "threshold" | "ml:<version>"


class _Artifact:
    """A loaded booster + meta for one version, reloaded only when the version changes."""

    def __init__(self, data_dir: Path):
        self._data_dir = data_dir
        self.version: str | None = None
        self.booster = None
        self.meta = None

    def load(self, version: str | None) -> None:
        if version == self.version:
            return
        if not version:
            self.booster, self.meta = None, None
        else:
            self.booster, self.meta = trainer.load_artifact(self._data_dir, version)
            if self.booster is None:
                log.warning("registry names version %s but its artifact is missing/"
                            "unreadable — it cannot answer", version)
        self.version = version

    def probability(self, row: FeatureRow) -> float:
        values = row.as_dict()
        columns = self.meta["feature_columns"]
        # A column the row does not carry, or one that is None (a source that has not
        # answered yet — the common case, not the exception), becomes NaN rather than
        # Python None: a single-row frame with any None column comes out `object` dtype,
        # which xgboost's DMatrix rejects outright rather than treating as missing. Bool
        # flags become 0/1 first, same as at training time (train.build_matrix).
        row_dict = {}
        for c in columns:
            v = values.get(c)
            row_dict[c] = float(v) if isinstance(v, bool) else (np.nan if v is None else v)
        x = pd.DataFrame([row_dict], columns=columns).astype("float64")
        raw = float(self.booster.predict(xgb.DMatrix(x))[0])
        # Undo the class weighting training applied, or the probability is inflated by up
        # to the weight's odds ratio (~34x on the September 2026 record). Artifacts written
        # before the weight was recorded carry none and are left as they were.
        return trainer.correct_for_class_weight(raw, self.meta.get("scale_pos_weight", 1.0))


class Model:
    def __init__(self, cfg: Config, registry: ModelRegistry, data_dir: Path):
        self._cfg = cfg
        self._data_dir = data_dir
        # Share the one registry instance the service owns: `_refresh` compares against
        # its live active_version, so a promote/rollback command (which only ever
        # touches the registry, never this object) is picked up on the next prediction
        # rather than requiring the add-on to restart for a promotion to take effect.
        self._registry = registry
        self._active = _Artifact(data_dir)
        self._shadow = _Artifact(data_dir)
        self._refresh()

    def _refresh(self) -> None:
        """(Re)load artifacts whose version has changed in the registry.

        Cheap on the common path — a string comparison each — so calling it from every
        prediction costs nothing while a promote/rollback is rare. Falls back to the
        threshold estimate (booster left None) rather than raising when the registry names
        a version whose files are missing or unreadable: a nightly job or a hand-edited
        registry.json must not be able to take inference down.
        """
        active = self._registry.active_version
        self._active.load(active)
        self._shadow.load(active or self._registry.candidate_version)

    def _ml_ready(self) -> bool:
        return (self._active.booster is not None
                and self.event_count() >= self._cfg.min_events_for_ml)

    @property
    def active_method(self) -> str:
        """What drives the alert tiers right now — "ml:<version>" or "threshold". Exists
        so callers (model_health) report reality rather than re-deriving the same gate
        `predict()` uses and risking the two disagreeing."""
        self._refresh()
        if self._cfg.ml_drives_alerts and self._ml_ready():
            return f"ml:{self._active.version}"
        return "threshold"

    @property
    def shadow_version(self) -> str | None:
        """The model `shadow()` evaluates, or None when there is none to evaluate."""
        self._refresh()
        return self._shadow.version if self._shadow.booster is not None else None

    def event_count(self) -> int:
        return self._registry.event_count

    def predict(self, row: FeatureRow) -> Prediction:
        """The probability the alert tiers use."""
        self._refresh()
        if self._cfg.ml_drives_alerts and self._ml_ready():
            try:
                return Prediction(
                    flood_probability=round(self._active.probability(row), 3),
                    predicted_crest_ft=None,   # see train.py's docstring
                    lag_estimate_min=None,
                    method=f"ml:{self._active.version}",
                )
            except Exception:
                # A model that cannot answer must not take the tiers down with it — every
                # rain, radar and stage rule still has to be evaluated this loop.
                log.exception("ML prediction failed — using the threshold estimate")
        return self._threshold_predict(row)

    def shadow(self, row: FeatureRow) -> tuple[float, str] | None:
        """(probability, version) from the ML model regardless of whether it drives the
        alert, or None when no model is available. Never raises."""
        self._refresh()
        if self._shadow.booster is None:
            return None
        try:
            return round(self._shadow.probability(row), 3), self._shadow.version
        except Exception:
            log.exception("shadow ML prediction failed for %s", self._shadow.version)
            return None

    def _threshold_predict(self, row: FeatureRow) -> Prediction:
        """Conservative, explainable proxy. NOT a calibrated probability yet.

        Combines rate-of-rise with an antecedent-wetness bump: when the low-lying
        soil sensors are saturated/ponding, the same rain produces faster runoff,
        so nudge probability up. Real thresholds get tuned against §6 tiers.
        """
        p = 0.0
        ror = row.rate_of_rise_in_min or 0.0
        if ror > 0:
            # 0 in/min -> 0; ~0.5 in/min sustained -> ~0.5, saturating toward 1.
            p = min(1.0, ror / 0.5 * 0.5)
        if row.ponding_flag:
            p = min(1.0, p + 0.15)
        return Prediction(
            flood_probability=round(p, 3),
            predicted_crest_ft=None,          # requires lag/response fit (Phase 3)
            lag_estimate_min=None,            # empirical, measured from storms (Phase 3)
            method="threshold",
        )
