"""On-site rain accumulator + Antecedent Precipitation Index.

Home Assistant exposes the Ecowitt's rain two ways: an instantaneous *rate* and the gauge's
own cumulative *counter* ("rain total"). The counter is the better source by construction —
the difference between two readings is exactly the rain that fell between them — so when
`onsite_rain_total_entity` is configured the rolling `rain_{1,3,6,24,72}h_in` totals (spec §5)
are built from counter deltas. The rate, sampled once per fast loop and integrated, is the
fallback: it misses whatever a burst does between two samples, and read 5-10 % low against
the station's own 24 h counter across the September 2026 storms. Cold start: totals build up
over the first 72 h.

The same increments drive the Antecedent Precipitation Index (`api_index_in`), so the
entities are read once per loop rather than twice.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .accumulator import RollingAccumulator
from .apindex import PrecipIndex

log = logging.getLogger("app.sources.rain")

MM_PER_INCH = 25.4

# A counter delta across a longer gap than this is real rain but cannot be placed in time:
# landing an afternoon's rain in "the last hour" would fire rain-rate tiers for rain that
# stopped hours ago. Re-baseline instead, the same way the rate path refuses to integrate
# across a gap. Long enough to ride out an add-on restart or a short HA outage.
COUNTER_MAX_GAP_S = 3600.0


def _inches_per(unit: str | None) -> float:
    return (1.0 / MM_PER_INCH) if "mm" in (unit or "").lower() else 1.0


class RainAccumulator:
    name = "rain"
    refresh_seconds = 0        # sample every fast loop

    def __init__(self, data_dir: Path, rate_entity: str, ha, now_fn=time.time,
                 total_entity: str | None = None):
        self._entity = rate_entity
        self._total_entity = total_entity
        self._ha = ha
        self._now = now_fn
        self._acc = RollingAccumulator(data_dir / "state" / "rain_accum.json", now_fn)
        # The API rides on the same increments, so it lives here rather than polling the
        # entities a second time (spec §4/§5).
        self._api = PrecipIndex(data_dir / "state" / "api_index.json", now_fn=now_fn)
        self._in_per_unit: float | None = None
        self._total_in_per_unit: float | None = None
        # Last counter reading, persisted so an add-on restart does not drop the rain that
        # fell across it: (ts, inches).
        self._counter_path = data_dir / "state" / "rain_counter.json"
        self._last_total = self._load_counter()

    def _unit_factor(self) -> float:
        """Inches per reported unit (1.0 for in/hr, 1/25.4 for mm/hr). Cached."""
        if self._in_per_unit is None:
            self._in_per_unit = _inches_per(self._ha.get_unit(self._entity))
        return self._in_per_unit

    def _load_counter(self) -> tuple[float, float] | None:
        try:
            data = json.loads(self._counter_path.read_text(encoding="utf-8"))
            return float(data["ts"]), float(data["total_in"])
        except (FileNotFoundError, ValueError, OSError, KeyError, TypeError):
            return None

    def _save_counter(self) -> None:
        try:
            if self._last_total is None:
                self._counter_path.unlink(missing_ok=True)
            else:
                ts, total = self._last_total
                self._counter_path.write_text(json.dumps({"ts": ts, "total_in": total}),
                                              encoding="utf-8")
        except OSError as exc:
            log.warning("could not persist %s: %s", self._counter_path.name, exc)

    def _counter_increment(self, now: float) -> float | None:
        """Inches since the last counter reading, or None when the counter cannot be used
        this loop (not configured, or unavailable — the caller falls back to the rate)."""
        if not self._total_entity:
            return None
        raw = self._ha.get_float(self._total_entity)
        if raw is None:
            # The rate covers this loop instead. Forget the baseline, or the next counter
            # reading would count this loop's rain a second time.
            if self._last_total is not None:
                self._last_total = None
                self._save_counter()
            return None
        if self._total_in_per_unit is None:
            self._total_in_per_unit = _inches_per(self._ha.get_unit(self._total_entity))
        total = raw * self._total_in_per_unit

        prev = self._last_total
        self._last_total = (now, total)
        self._save_counter()
        if prev is None:
            return 0.0                                  # first reading: a baseline only
        prev_ts, prev_total = prev
        if now - prev_ts > COUNTER_MAX_GAP_S:
            log.info("rain counter gap of %.0f min — re-baselining rather than landing "
                     "%.2f in in a single loop", (now - prev_ts) / 60.0,
                     max(0.0, total - prev_total))
            return 0.0
        delta = total - prev_total
        if delta < 0:
            # The counter was reset (station reboot, or reset by hand). A reset is not
            # negative rain; count from the new value.
            log.info("rain counter went from %.3f to %.3f in — treating as a reset",
                     prev_total, total)
            return 0.0
        return delta

    def poll(self) -> dict:
        now = self._now()
        rate = self._ha.get_float(self._entity) if self._entity else None
        rate_in = rate * self._unit_factor() if rate is not None else None

        increment = self._counter_increment(now)
        if increment is not None:
            sums = self._acc.add(increment)
        else:
            sums = self._acc.update(rate_in)
        out = {f"rain_{w}h_in": v for w, v in sums.items()}
        # Raw rate passes through so the watchdogs can see the Ecowitt entity itself go
        # quiet: the accumulator keeps emitting totals either way, so its output alone
        # cannot distinguish "no rain" from "gauge stopped reporting".
        out["rain_rate_in_hr"] = rate_in
        out["api_index_in"] = self._api.update(self._acc.last_increment)
        return out
