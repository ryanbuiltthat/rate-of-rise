"""NWM / NWPS reach streamflow forecast (slice 2b).

Fetches the short-range streamflow forecast for the configured reach from the free
National Water Prediction Service API and reports the near-term and peak forecast
discharge (ft³/s):
  GET api.water.noaa.gov/nwps/v1/reaches/{reachId}/streamflow?series=short_range
  -> shortRange.series.data = [{ "validTime": ..., "flow": <cfs> }, ...]
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger("app.sources.nwm")


def _default_fetch(url: str, timeout: float = 15.0) -> dict:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


class NwmReach:
    name = "nwm"
    refresh_seconds = 20 * 60

    def __init__(self, reach_id, fetch=_default_fetch):
        self._reach = str(reach_id)
        self._fetch = fetch

    def poll(self) -> dict:
        url = (f"https://api.water.noaa.gov/nwps/v1/reaches/{self._reach}"
               "/streamflow?series=short_range")
        data = self._fetch(url)
        series = ((data.get("shortRange") or {}).get("series")) or {}
        flows = [p["flow"] for p in (series.get("data") or []) if p.get("flow") is not None]
        return {
            "nwm_flow_cfs": round(flows[0], 2) if flows else None,       # near-term
            "nwm_flow_max_cfs": round(max(flows), 2) if flows else None,  # short-range peak
        }
