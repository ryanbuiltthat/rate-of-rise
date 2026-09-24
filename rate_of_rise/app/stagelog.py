"""High-resolution stage record, independent of Home Assistant's recorder.

The fast loop samples the creek every 5 minutes, which is what the dataset keeps. The node
reports every 60 s (every ~5 s while the creek is rising), and until now the only place that
finer record lived was Home Assistant's recorder — which keeps 10 days by default, and which
on 2026-09-23 stopped writing anything for 25 hours while HA itself kept running. A storm in
a window like that would have kept its 5-minute rows and lost its crest.

So this reads the stage entity from HA's live state machine (which kept working through that
stall) every few seconds and appends each *new* reading to a daily CSV under /share:

    /share/rate_of_rise/stage/YYYY-MM-DD.csv     reading_ts,logged_ts,stage_ft

One row per change of `last_updated`, so a still creek writes little. Unix seconds rather than
a local wall-clock column, so a DST change cannot fold an hour of a storm onto itself. It is a
record, not an input: nothing reads it back, which is why a failed write only logs.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("app.stagelog")

LOG_EVERY_S = 10.0    # the node's fast mode reports every ~5-7 s; 10 s catches most of it
HEADER = "reading_ts,logged_ts,stage_ft\n"


class StageLogger:
    def __init__(self, ha, entity: str, out_dir: Path, now_fn=time.time):
        self._ha = ha
        self._entity = entity
        self._dir = out_dir
        self._now = now_fn
        self._next = 0.0
        self._last_reading_ts: float | None = None
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("stage log directory %s unusable: %s", self._dir, exc)

    def tick(self) -> bool:
        """Log the current reading if it is new. Cheap to call often; rate-limits itself.
        Returns True when a row was written."""
        now = self._now()
        if now < self._next:
            return False
        self._next = now + LOG_EVERY_S
        value, age_s = self._ha.get_float_with_age(self._entity)
        if age_s is None:
            return False
        reading_ts = round(now - age_s, 1)
        if self._last_reading_ts is not None and reading_ts <= self._last_reading_ts:
            return False                                   # same reading as last time
        self._last_reading_ts = reading_ts
        day = datetime.fromtimestamp(reading_ts).strftime("%Y-%m-%d")
        path = self._dir / f"{day}.csv"
        stage = "" if value is None else f"{value:.4f}"    # blank = unknown/unavailable
        try:
            new = not path.exists()
            with path.open("a", encoding="utf-8") as fh:
                if new:
                    fh.write(HEADER)
                fh.write(f"{reading_ts},{round(now, 1)},{stage}\n")
        except OSError as exc:
            log.warning("could not append to %s: %s", path, exc)
            return False
        return True


def stage_log_dir(data_dir: Path, share_dir: Path | None) -> Path:
    """Under /share when there is one (reachable over Samba), else the add-on's /data."""
    base = (share_dir / "rate_of_rise") if share_dir is not None else data_dir
    return base / "stage"
