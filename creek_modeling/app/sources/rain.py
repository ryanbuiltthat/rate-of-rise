"""On-site rain accumulator + Antecedent Precipitation Index.

Home Assistant exposes an Ecowitt *rain rate* (in/hr or mm/hr) but no rolling
multi-window totals. This samples the rate each fast loop and feeds it (normalized to
inches/hour) into a shared RollingAccumulator, reporting `rain_{1,3,6,24,72}h_in`
(spec §5). Cold start: totals build up over the first 72 h.

The same rate samples drive the Antecedent Precipitation Index (`api_index_in`), so the
entity is read once per loop rather than twice.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from .accumulator import RollingAccumulator
from .apindex import PrecipIndex

log = logging.getLogger("app.sources.rain")

MM_PER_INCH = 25.4


class RainAccumulator:
    name = "rain"
    refresh_seconds = 0        # sample every fast loop

    def __init__(self, data_dir: Path, rate_entity: str, ha, now_fn=time.time):
        self._entity = rate_entity
        self._ha = ha
        self._acc = RollingAccumulator(data_dir / "state" / "rain_accum.json", now_fn)
        # The API rides on the same rate samples, so it lives here rather than polling
        # the entity a second time (spec §4/§5).
        self._api = PrecipIndex(data_dir / "state" / "api_index.json", now_fn=now_fn)
        self._in_per_unit: float | None = None

    def _unit_factor(self) -> float:
        """Inches per reported unit (1.0 for in/hr, 1/25.4 for mm/hr). Cached."""
        if self._in_per_unit is None:
            unit = (self._ha.get_unit(self._entity) or "").lower()
            self._in_per_unit = (1.0 / MM_PER_INCH) if "mm" in unit else 1.0
        return self._in_per_unit

    def poll(self) -> dict:
        rate = self._ha.get_float(self._entity)   # per hour, in the entity's unit
        rate_in = rate * self._unit_factor() if rate is not None else None
        sums = self._acc.update(rate_in)
        out = {f"rain_{w}h_in": v for w, v in sums.items()}
        # Raw rate passes through so the watchdogs can see the Ecowitt entity itself go
        # quiet: the accumulator keeps emitting totals either way, so its output alone
        # cannot distinguish "no rain" from "gauge stopped reporting".
        out["rain_rate_in_hr"] = rate_in
        out["api_index_in"] = self._api.update(self._acc.last_increment)
        return out
