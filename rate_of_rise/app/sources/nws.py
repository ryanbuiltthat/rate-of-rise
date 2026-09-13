"""NWS quantitative precipitation forecast (QPF).

Free NOAA/NWS API (no key), two hops:
  1. GET api.weather.gov/points/{lat},{lon} -> properties.forecastGridData URL
  2. GET that grid URL -> properties.quantitativePrecipitation.values

Each value is `{ "validTime": "<ISO start>/<ISO8601 duration>", "value": <mm> }`. We
pro-rate each interval's precip by how much of it falls inside the next 6 h / 24 h and
return inches. NWS requires a descriptive `User-Agent`.

Proration is over each interval's *remaining* time, not its full span — see
`_qpf_within`. The distinction only matters for the interval already in progress, and it
matters a lot: BGM publishes this grid in 6-hour blocks, so that one interval is most of
a 6 h forecast.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger("app.sources.nws")

USER_AGENT = "ewfa-creek-modeling (github.com/ryanbuiltthat/rate-of-rise)"
MM_PER_INCH = 25.4
_DUR = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")


def _duration_seconds(iso: str) -> float:
    m = _DUR.fullmatch(iso)
    if not m:
        return 0.0
    days, hours, mins, secs = (int(x) if x else 0 for x in m.groups())
    return days * 86400 + hours * 3600 + mins * 60 + secs


class NwsQpf:
    name = "nws"
    refresh_seconds = 15 * 60

    def __init__(self, lat: float, lon: float, timeout: float = 15.0):
        self._lat, self._lon = lat, lon
        self._timeout = timeout
        self._grid_url: str | None = None
        self._headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}

    def _get(self, url: str) -> dict:
        r = requests.get(url, headers=self._headers, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    def _resolve_grid_url(self) -> str:
        if self._grid_url is None:
            pts = self._get(f"https://api.weather.gov/points/{self._lat},{self._lon}")
            self._grid_url = pts["properties"]["forecastGridData"]
            log.info("NWS grid resolved: %s", self._grid_url)
        return self._grid_url

    def poll(self) -> dict:
        grid = self._get(self._resolve_grid_url())
        values = grid["properties"]["quantitativePrecipitation"]["values"]
        now = datetime.now(timezone.utc)
        return {
            "qpf_6h_in": round(self._qpf_within(values, now, 6), 3),
            "qpf_24h_in": round(self._qpf_within(values, now, 24), 3),
        }

    @staticmethod
    def _qpf_within(values: list[dict], now: datetime, hours: int) -> float:
        """Forecast rain inside [now, now+hours], in inches.

        Each interval's precip is spread uniformly over the part of it that is still in
        the future, and the share of *that* falling inside the window is counted. For an
        interval entirely ahead of us this is ordinary proration over its full span. For
        the one already in progress it is not, and that is the point.

        The obvious alternative — prorating the in-progress interval over its full span,
        so a 6 h block four hours old contributes a third of its value — quietly assumes
        the missing two thirds have already fallen. Against BGM's 6-hour blocks that
        assumption fails in the worst direction for a flashy basin (spec §1): a
        forecaster's 0.15" put in the 18:00-00:00Z block for an evening thunderstorm
        decays toward zero over the afternoon, so the number bottoms out just as the
        storm arrives. Rain that has genuinely already fallen is not lost either — it is
        measured, by the on-site gauge, and reaches the tiers as rain_*_in.

        Treating the whole interval as still to come biases toward over-warning, which is
        the correct direction of error for a flood warning, and it decays honestly: the
        value falls only as the interval's own end passes out of the window.
        """
        window_end = now + timedelta(hours=hours)
        total_mm = 0.0
        for v in values:
            if v.get("value") is None:
                continue
            start_s, _, dur_s = v["validTime"].partition("/")
            start = datetime.fromisoformat(start_s)
            dur = _duration_seconds(dur_s)
            if dur <= 0:
                continue
            i_end = start + timedelta(seconds=dur)
            # Remaining span of the interval; equals `dur` for anything not yet started.
            remaining_start = max(start, now)
            remaining = (i_end - remaining_start).total_seconds()
            if remaining <= 0:                      # wholly in the past
                continue
            overlap = (min(i_end, window_end) - remaining_start).total_seconds()
            if overlap > 0:
                total_mm += v["value"] * (overlap / remaining)
        return total_mm / MM_PER_INCH
