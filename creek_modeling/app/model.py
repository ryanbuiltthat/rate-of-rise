"""Inference + model registry.

Until >= `min_events_for_ml` storms are captured (spec §5), this returns a
transparent, conservative *threshold* estimate rather than an ML prediction —
early months are data-collection + threshold alerting only. The ML path
(`train.py`) slots in once the registry names a promoted artifact; the caller
interface (`predict()` returning a `Prediction`) does not change either way, so
nothing downstream needs to know which path answered.
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


class Model:
    def __init__(self, cfg: Config, registry: ModelRegistry, data_dir: Path):
        self._cfg = cfg
        self._data_dir = data_dir
        # Share the one registry instance the service owns: `_refresh` compares against
        # its live active_version, so a promote/rollback command (which only ever
        # touches the registry, never this object) is picked up on the next prediction
        # rather than requiring the add-on to restart for a promotion to take effect.
        self._registry = registry
        self._loaded_version: str | None = None
        self._booster = None
        self._meta = None
        self._refresh()

    def _refresh(self) -> None:
        """(Re)load the artifact if the registry's active version has changed.

        Cheap on the common path — one string comparison — so calling it from every
        `predict()` costs nothing while a promote/rollback is rare. Falls back to the
        threshold estimate (booster/meta left None) rather than raising when the
        registry names a version whose files are missing or unreadable: a nightly job
        or a hand-edited registry.json must not be able to take inference down.
        """
        version = self._registry.active_version
        if version == self._loaded_version:
            return
        if not version:
            self._booster, self._meta = None, None
        else:
            self._booster, self._meta = trainer.load_artifact(self._data_dir, version)
            if self._booster is None:
                log.warning("registry names active version %s but its artifact is "
                           "missing/unreadable — falling back to the threshold estimate",
                           version)
        self._loaded_version = version

    @property
    def active_method(self) -> str:
        """What `predict()` will actually do right now — "ml:<version>" or
        "threshold". Exists so callers (model_health) report reality rather than
        re-deriving the same gate `predict()` uses and risking the two disagreeing."""
        self._refresh()
        if self._booster is not None and self.event_count() >= self._cfg.min_events_for_ml:
            return f"ml:{self._registry.active_version}"
        return "threshold"

    def event_count(self) -> int:
        return self._registry.event_count

    def predict(self, row: FeatureRow) -> Prediction:
        self._refresh()
        if self._booster is not None and self.event_count() >= self._cfg.min_events_for_ml:
            return self._ml_predict(row)
        return self._threshold_predict(row)

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

    def _ml_predict(self, row: FeatureRow) -> Prediction:
        """Run the promoted booster. `predicted_crest_ft` stays None here too —
        see train.py's module docstring for why a stage regressor is not built."""
        values = row.as_dict()
        columns = self._meta["feature_columns"]
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
        p = float(self._booster.predict(xgb.DMatrix(x))[0])
        return Prediction(
            flood_probability=round(p, 3),
            predicted_crest_ft=None,
            lag_estimate_min=None,
            method=f"ml:{self._registry.active_version}",
        )
