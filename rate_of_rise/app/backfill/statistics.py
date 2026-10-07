"""Hourly long-term statistics for hours the backfill filled.

HA compiles an hour's mean/min/max from the states it had at the time, so an hour with a gap
has statistics that miss it, and history views longer than about 10 days are drawn from those
statistics. After states are inserted, each affected, completed hour is recomputed the way
HA computes it (time-weighted mean; the state in effect at the start of the hour counts) and
re-imported with `recorder/import_statistics`. That is HA's supported way to write statistics.

Only measurement statistics (mean_type 1). A `has_sum` statistic (a total_increasing counter
such as the rain total) carries a running sum that every later hour builds on; re-importing
one hour would mean rewriting every hour after it, so those are left alone.
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import websocket

from .recorder import connect

log = logging.getLogger("app.backfill.statistics")

HOUR = 3600.0
SETTLE_S = 600.0   # leave the hour just finished to HA's own compile


def _num(state: str) -> float | None:
    try:
        return float(state)
    except (TypeError, ValueError):
        return None


def hourly_stats(rows: list[tuple[float, float | None]], hour_start: float,
                 prior: float | None) -> dict | None:
    end = hour_start + HOUR
    pts = [(hour_start, prior)] + [(t, v) for t, v in rows if hour_start <= t < end]
    total = dur = 0.0
    vals = []
    for i, (t, v) in enumerate(pts):
        nxt = pts[i + 1][0] if i + 1 < len(pts) else end
        if v is None:
            continue
        total += v * (nxt - t)
        dur += nxt - t
        vals.append(v)
    if not vals:
        return None
    return {"mean": total / dur if dur else vals[-1], "min": min(vals), "max": max(vals)}


class HAWebsocket:
    """One short-lived connection per call to HA's websocket API via the Supervisor proxy."""

    def __init__(self, url: str, token: str, connect_fn=websocket.create_connection,
                 timeout: float = 30.0):
        self._url, self._token, self._connect, self._timeout = url, token, connect_fn, timeout

    def call(self, msg: dict):
        ws = self._connect(self._url, timeout=self._timeout)
        try:
            json.loads(ws.recv())                                   # auth_required
            ws.send(json.dumps({"type": "auth", "access_token": self._token}))
            auth = json.loads(ws.recv())
            if auth.get("type") != "auth_ok":
                raise RuntimeError(f"HA websocket auth failed: {auth}")
            ws.send(json.dumps({"id": 1, **msg}))
            while True:
                reply = json.loads(ws.recv())
                if reply.get("id") == 1:
                    break
            if not reply.get("success"):
                raise RuntimeError(f"{msg.get('type')} failed: {reply.get('error')}")
            return reply.get("result")
        finally:
            ws.close()


class StatisticsWriter:
    def __init__(self, db_path: Path, send: Callable[[dict], object], now_fn=time.time):
        self._db, self._send, self._now = Path(db_path), send, now_fn

    def backfill(self, entity_id: str, inserted_ts: list[float]) -> int:
        if not inserted_ts:
            return 0
        cutoff = self._now() - SETTLE_S
        hours = sorted({t - t % HOUR for t in inserted_ts if t - t % HOUR + HOUR <= cutoff})
        if not hours:
            return 0
        with closing(connect(self._db)) as conn:
            meta = conn.execute(
                "SELECT unit_of_measurement, name, has_sum, mean_type, unit_class"
                " FROM statistics_meta WHERE statistic_id = ?", (entity_id,)).fetchone()
            if meta is None or meta[2] or meta[3] != 1:
                return 0
            mid = conn.execute("SELECT metadata_id FROM states_meta WHERE entity_id = ?",
                               (entity_id,)).fetchone()
            if mid is None:
                return 0
            stats = []
            for h in hours:
                prior = conn.execute(
                    "SELECT state FROM states WHERE metadata_id = ? AND last_updated_ts < ?"
                    " ORDER BY last_updated_ts DESC LIMIT 1", (mid[0], h)).fetchone()
                rows = conn.execute(
                    "SELECT last_updated_ts, state FROM states WHERE metadata_id = ?"
                    " AND last_updated_ts >= ? AND last_updated_ts < ?"
                    " ORDER BY last_updated_ts", (mid[0], h, h + HOUR)).fetchall()
                s = hourly_stats([(t, _num(v)) for t, v in rows], h,
                                 _num(prior[0]) if prior else None)
                if s is not None:
                    start = datetime.fromtimestamp(h, timezone.utc).isoformat()
                    stats.append({"start": start, **s})
        if not stats:
            return 0
        metadata = {"source": "recorder", "statistic_id": entity_id,
                    "unit_of_measurement": meta[0], "name": meta[1], "has_sum": False,
                    "mean_type": 1}
        if meta[4] is not None:
            metadata["unit_class"] = meta[4]
        for batch in (stats[i:i + 500] for i in range(0, len(stats), 500)):
            self._send({"type": "recorder/import_statistics", "metadata": metadata,
                        "stats": batch})
        return len(stats)
