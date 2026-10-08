"""Merge backfilled node readings into the add-on's stage log (app/stagelog.py).

Same files, same columns, same local-day naming as StageLogger. A reading within
MATCH_TOLERANCE_S of one already logged is the same reading. Each touched day is rewritten
sorted through a temp file and an atomic replace, under stagelog.FILE_LOCK.
"""
from __future__ import annotations

import bisect
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ..stagelog import FILE_LOCK, HEADER

log = logging.getLogger("app.backfill.stagelog")

MATCH_TOLERANCE_S = 2.0


def _read(path: Path) -> list[tuple[float, str]]:
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            parts = line.split(",")
            if len(parts) == 3:
                try:
                    out.append((float(parts[0]), line))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return out


def merge_stage_rows(out_dir: Path, rows: list[tuple[float, float | None]], now: float) -> int:
    by_day: dict[str, list[tuple[float, float | None]]] = defaultdict(list)
    for ts, stage in rows:
        by_day[datetime.fromtimestamp(ts).strftime("%Y-%m-%d")].append((ts, stage))
    added = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for day, day_rows in by_day.items():
        path = out_dir / f"{day}.csv"
        with FILE_LOCK:
            existing = _read(path)
            stamps = sorted(ts for ts, _ in existing)
            new = []
            for ts, stage in day_rows:
                i = bisect.bisect_left(stamps, ts - MATCH_TOLERANCE_S)
                if i < len(stamps) and stamps[i] <= ts + MATCH_TOLERANCE_S:
                    continue
                text = "" if stage is None else f"{stage:.4f}"
                new.append((round(ts, 1), f"{round(ts, 1)},{round(now, 1)},{text}"))
                bisect.insort(stamps, ts)
            if not new:
                continue
            merged = sorted(existing + new, key=lambda r: r[0])
            tmp = path.with_suffix(".tmp")
            tmp.write_text(HEADER + "".join(line + "\n" for _, line in merged), encoding="utf-8")
            tmp.replace(path)
            added += len(new)
    return added
