"""Weather Underground upstream PWS rain (slice 2b).

Fetches current observations for each configured upstream station
(`api.weather.com/v2/pws/observations/current`, imperial units) and reports
`upstream_rain_{1,3,6,24,72}h_in` as the mean across stations of each station's own rolling
totals, plus `upstream_precip_today_in` (mean of each station's `precipTotal`). The API key
stays in add-on options.

Rain comes from each station's `precipTotal` — its accumulation since local midnight — as
the difference between successive polls. That is the station's own measurement; the
previous approach integrated `precipRate` sampled once per 10-minute poll, which read 12-17 %
low against these same stations' totals across the September 2026 storms and would do worse
in a convective burst. `precipRate` integration remains the fallback for a station that
reports no total.

A single station failing is skipped, not fatal — but a poll where *no* station answers sets
`last_poll_ok = False`, so the coordinator stops counting the source as alive and the
`upstream_data_missing` watchdog fires. Before that flag existed every per-station error was
swallowed, the poll always "succeeded", and the watchdog could not fire at all.
"""
from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path

import requests

from .accumulator import WINDOWS_H, RollingAccumulator

log = logging.getLogger("app.sources.wu")

# Same reasoning as the on-site counter (rain.py): a delta across a longer gap is real rain
# that cannot be placed in time, so it re-baselines instead of landing in one poll.
COUNTER_MAX_GAP_S = 3600.0
# A station that has not answered for this long drops out of the mean rather than dragging
# it towards zero with an empty history. Three polls, so one failed fetch changes nothing.
STATION_FRESH_S = 30 * 60


def _default_fetch(url: str, timeout: float = 15.0) -> dict:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _safe(station_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", station_id)


class WuUpstream:
    name = "wu"
    refresh_seconds = 10 * 60

    def __init__(self, api_key: str, station_ids, state_dir: Path,
                 now_fn=time.time, fetch=_default_fetch):
        self._key = api_key
        self._stations = list(station_ids)
        self._fetch = fetch
        self._now = now_fn
        state = state_dir / "state"
        state.mkdir(parents=True, exist_ok=True)
        # One history per station, so a station that misses a poll catches up on its own
        # next delta instead of skewing a shared total. The old single combined history is
        # used to seed each station once, so an upgrade does not restart the 72 h window.
        combined = state / "upstream_rain.json"
        self._accs: dict[str, RollingAccumulator] = {}
        for sid in self._stations:
            path = state / f"upstream_rain_{_safe(sid)}.json"
            if not path.exists() and combined.exists():
                try:
                    shutil.copyfile(combined, path)
                except OSError as exc:
                    log.warning("could not seed %s from %s: %s", path.name, combined.name, exc)
            self._accs[sid] = RollingAccumulator(path, now_fn)
        # sid -> (local obs date, ts, precipTotal baseline in inches)
        self._totals: dict[str, tuple[str, float, float]] = {}
        self._last_seen: dict[str, float] = {}
        self.last_poll_ok = True

    def _observation(self, station_id: str) -> dict | None:
        url = ("https://api.weather.com/v2/pws/observations/current"
               f"?stationId={station_id}&format=json&units=e&apiKey={self._key}")
        obs = self._fetch(url).get("observations") or []
        return obs[0] if obs else None

    def _total_increment(self, sid: str, obs: dict, total: float, now: float) -> float:
        """Inches since this station's previous poll, from its since-midnight total."""
        day = str(obs.get("obsTimeLocal") or "")[:10]
        prev = self._totals.get(sid)
        if prev is None:
            self._totals[sid] = (day, now, total)
            return 0.0                                   # first reading: a baseline only
        prev_day, prev_ts, prev_total = prev
        if now - prev_ts > COUNTER_MAX_GAP_S:
            self._totals[sid] = (day, now, total)
            return 0.0
        if day and prev_day and day != prev_day:
            # Past local midnight the total restarts from zero; what it reads now fell
            # since then. (Rain between the last poll and midnight is the only loss.)
            self._totals[sid] = (day, now, total)
            return max(0.0, total)
        # Same day. A total that dips and comes back is a glitch in the feed, not a reset:
        # hold the baseline at its high-water mark so the recovery is not counted twice.
        self._totals[sid] = (day or prev_day, now, max(prev_total, total))
        return max(0.0, total - prev_total)

    def poll(self) -> dict:
        now = self._now()
        answered: list[str] = []
        totals: list[float] = []
        for sid in self._stations:
            try:
                o = self._observation(sid)
            except Exception:  # one bad station shouldn't drop the others
                log.warning("WU fetch failed for station %s", sid)
                continue
            if o is None:
                continue
            imp = o.get("imperial") or {}
            total, rate = imp.get("precipTotal"), imp.get("precipRate")
            if total is not None:
                self._accs[sid].add(self._total_increment(sid, o, float(total), now))
                totals.append(float(total))
            elif rate is not None:
                self._accs[sid].update(float(rate))
            else:
                continue
            answered.append(sid)
            self._last_seen[sid] = now

        self.last_poll_ok = bool(answered)
        if not answered:
            log.warning("no upstream PWS station answered (%s)", ", ".join(self._stations))

        fresh = [sid for sid in self._stations
                 if now - self._last_seen.get(sid, float("-inf")) <= STATION_FRESH_S]
        if fresh:
            per_station = [self._accs[sid].sums(now) for sid in fresh]
            out = {f"upstream_rain_{w}h_in": round(sum(s[w] for s in per_station)
                                                   / len(per_station), 3)
                   for w in per_station[0]}
        else:
            # Nobody has answered for half an hour: the rain upstream is unknown, and saying
            # so is more honest than a mean of empty histories reading as "dry".
            out = {f"upstream_rain_{w}h_in": None for w in WINDOWS_H}
        out["upstream_precip_today_in"] = (
            round(sum(totals) / len(totals), 3) if totals else None
        )
        return out
