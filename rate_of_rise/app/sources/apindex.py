"""Antecedent Precipitation Index (slice 2f).

Spec §4 and §5 both call for an API: a single number standing in for how wet the basin
already is. It is the classic complement to the WH51 probes — those measure two buried
points, while the API summarises weeks of basin-wide rainfall history. Wet antecedent
conditions are what turn an ordinary storm into a flood, so this is one of the strongest
predictors available and, usefully, needs no creek gauge.

    API(t) = API(t-1) * k^(elapsed_days) + rainfall since t-1

`k` is a daily recession constant: the fraction of today's index still "remembered"
tomorrow, representing drainage and evapotranspiration. Applying it as a continuous power
of elapsed time (rather than a discrete daily step) means the index decays correctly
across an irregular sampling interval or an add-on restart.

State is persisted with its timestamp, so downtime decays the index rather than freezing
it — coming back after a dry week should not report the basin as still saturated.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("app.sources.apindex")

# Daily recession constant. Literature puts it around 0.85-0.95 depending on basin and
# season; 0.92 gives roughly a two-week memory, which suits a small flashy basin carrying
# an appreciable antecedent signal. PLACEHOLDER — tune against observed storms (Phase 3).
DEFAULT_K = 0.92

# Beyond this, treat the gap as a cold start rather than decaying across an unknown
# stretch: the rainfall during the gap was never sampled, so the index would be wrong in
# a way that silently understates wetness.
MAX_GAP_DAYS = 14.0


class PrecipIndex:
    """Exponentially-decaying rainfall memory, in inches."""

    def __init__(self, state_path: Path, k: float = DEFAULT_K, now_fn=time.time):
        self._path = state_path
        self._k = k
        self._now = now_fn
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._value, self._ts = self._load()

    def _load(self) -> tuple[float, float | None]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return float(data["value"]), float(data["ts"])
        except (FileNotFoundError, ValueError, OSError, KeyError, TypeError):
            return 0.0, None

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps({"value": self._value, "ts": self._ts}),
                                  encoding="utf-8")
        except OSError as exc:
            log.warning("could not persist %s: %s", self._path.name, exc)

    def update(self, rain_increment_in: float | None) -> float:
        """Decay by the elapsed time, add the rain seen since the last call, persist."""
        now = self._now()
        if self._ts is not None:
            elapsed_days = (now - self._ts) / 86400.0
            if elapsed_days < 0:
                elapsed_days = 0.0        # clock stepped backwards; don't inflate
            if elapsed_days > MAX_GAP_DAYS:
                log.info("API gap of %.1f days — restarting the index from zero",
                         elapsed_days)
                self._value = 0.0
            else:
                self._value *= self._k ** elapsed_days

        self._value += max(0.0, rain_increment_in or 0.0)
        self._ts = now
        self._save()
        return round(self._value, 3)

    @property
    def value(self) -> float:
        return round(self._value, 3)
