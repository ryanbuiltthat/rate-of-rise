"""Weather Underground upstream PWS rain (slice 2b).

Fetches current observations for each configured upstream station
(`api.weather.com/v2/pws/observations/current`, imperial units) and feeds the mean
`precipRate` (in/hr) into a shared RollingAccumulator -> `upstream_rain_{1,3,6,24,72}h_in`.
Also reports `upstream_precip_today_in` (mean of each station's `precipTotal`). A single
station failing is skipped, not fatal. The API key stays in add-on options.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

from .accumulator import RollingAccumulator

log = logging.getLogger("app.sources.wu")


def _default_fetch(url: str, timeout: float = 15.0) -> dict:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


class WuUpstream:
    name = "wu"
    refresh_seconds = 10 * 60

    def __init__(self, api_key: str, station_ids, state_dir: Path,
                 now_fn=time.time, fetch=_default_fetch):
        self._key = api_key
        self._stations = list(station_ids)
        self._fetch = fetch
        self._acc = RollingAccumulator(state_dir / "state" / "upstream_rain.json", now_fn)

    def _observation(self, station_id: str) -> dict | None:
        url = ("https://api.weather.com/v2/pws/observations/current"
               f"?stationId={station_id}&format=json&units=e&apiKey={self._key}")
        obs = self._fetch(url).get("observations") or []
        return obs[0] if obs else None

    def poll(self) -> dict:
        rates: list[float] = []
        totals: list[float] = []
        for sid in self._stations:
            try:
                o = self._observation(sid)
            except Exception:  # one bad station shouldn't drop the others
                log.warning("WU fetch failed for station %s", sid)
                continue
            imp = (o or {}).get("imperial") or {}
            if imp.get("precipRate") is not None:
                rates.append(float(imp["precipRate"]))
            if imp.get("precipTotal") is not None:
                totals.append(float(imp["precipTotal"]))

        mean_rate = sum(rates) / len(rates) if rates else None
        out = {f"upstream_rain_{w}h_in": v for w, v in self._acc.update(mean_rate).items()}
        out["upstream_precip_today_in"] = (
            round(sum(totals) / len(totals), 3) if totals else None
        )
        return out
