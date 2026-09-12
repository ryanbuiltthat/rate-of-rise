"""Rolling multi-window precip accumulator (shared by on-site rain + upstream WU).

Given a precip *rate* in inches/hour sampled over time, integrates rate x elapsed
into a ring of (timestamp, inches) increments persisted to a JSON state file, and
reports 1/3/6/24/72 h totals. Elapsed intervals longer than `max_gap_s` are treated
as a break (add-on downtime) and not integrated, so a gap can't fabricate hours of rain.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("app.sources.accumulator")

WINDOWS_H = (1, 3, 6, 24, 72)
RETAIN_S = 72 * 3600
MAX_GAP_S = 15 * 60


class RollingAccumulator:
    def __init__(self, state_path: Path, now_fn=time.time,
                 windows_h=WINDOWS_H, retain_s=RETAIN_S, max_gap_s=MAX_GAP_S):
        self._path = state_path
        self._now = now_fn
        self._windows = windows_h
        self._retain = retain_s
        self._max_gap = max_gap_s
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._increments: list[list[float]] = self._load()
        self._last_ts: float | None = None   # not persisted -> no gap-integration on restart
        # Rain integrated by the most recent update() call; consumed by the API index.
        self.last_increment: float = 0.0

    def _load(self) -> list[list[float]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return [list(x) for x in data.get("increments", [])]
        except (FileNotFoundError, ValueError, OSError):
            return []

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps({"increments": self._increments}), encoding="utf-8")
        except OSError as exc:
            log.warning("could not persist %s: %s", self._path.name, exc)

    def update(self, rate_in_per_hr: float | None) -> dict[int, float]:
        """Integrate the latest rate sample and return {window_hours: inches}."""
        now = self._now()
        self.last_increment = 0.0
        if rate_in_per_hr is not None and self._last_ts is not None:
            dt_s = now - self._last_ts
            if 0 < dt_s <= self._max_gap:
                inches = max(0.0, rate_in_per_hr) * (dt_s / 3600.0)
                if inches > 0:
                    self._increments.append([now, inches])
                    self.last_increment = inches
        self._last_ts = now

        cutoff = now - self._retain
        self._increments = [x for x in self._increments if x[0] >= cutoff]
        self._save()

        return {
            w: round(sum(inc for ts, inc in self._increments if ts >= now - w * 3600), 3)
            for w in self._windows
        }
