"""USGS downstream gauge observations (slice 2c).

Creek has no gauge of its own (spec §1), so this reads the two nearest USGS
gauges that actually publish continuous instantaneous values:

  * 01534860 — receiving river, downstream city (~7 mi SE). Adjacent
    basin, but the adjacent creek drains the same the upstream
    upland that forms the creek's upstream half — so it sees roughly *our* rain, and its
    rise is the closest available analog for how that upland responds.
  * 01534000 — receiving creek, adjacent basin (~11 mi W). The receiving system
    downstream of the confluence; much larger drainage and a correspondingly longer lag.

NOTE: spec §1 names <usgs bad id> (a nearby reach) as an active continuous gauge.
It is not — NWIS returns "no sites found" for that number, so it was never pollable.
01534860 replaces it here and is the better analog regardless, being nearer and fed by
our own upstream corridor.

Neither is a substitute for the creek node: different basins, different drainage areas,
different lag. What they *are* is a live, free, already-flowing record of how this
watershed answers rain. Correlating upstream/on-site rainfall against their observed rise
is the only empirical rainfall→response signal available before the SEN0676 is mounted,
so it gives the Phase-3 lag work a head start (spec §7).

Free NWIS instantaneous-values service, no key:
  GET waterservices.usgs.gov/nwis/iv/?format=json&sites=...&parameterCd=00060,00065&period=PT6H

Parameter codes: 00060 = discharge (ft³/s), 00065 = gage height (ft). Values arrive as
strings, newest last, with -999999 marking "no reading".
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import requests

log = logging.getLogger("app.sources.usgs")

# Watershed-specific (spec §1) — these two gauges are the ones this project validates
# against, so they are fixed here rather than being an add-on option. The label becomes
# the feature-name stem, which keeps FEATURE_KEYS static.
SITES = {
    "01534860": "leggetts",
    "01534000": "tunkhannock",
}

DISCHARGE = "00060"
GAGE_HEIGHT = "00065"
NO_DATA = -999999.0
RISE_WINDOW_H = 3


def feature_keys() -> tuple[str, ...]:
    """Feature names this source produces, in a stable order."""
    return tuple(
        f"usgs_{label}_{suffix}"
        for label in SITES.values()
        for suffix in ("gage_ft", "flow_cfs", "rise_3h_ft")
    )


def _default_fetch(url: str, timeout: float = 15.0) -> dict:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


class UsgsDownstream:
    name = "usgs"
    refresh_seconds = 15 * 60

    def __init__(self, fetch=_default_fetch, sites=None):
        self._fetch = fetch
        self._sites = dict(sites) if sites else dict(SITES)

    def poll(self) -> dict:
        url = (
            "https://waterservices.usgs.gov/nwis/iv/?format=json"
            f"&sites={','.join(self._sites)}"
            f"&parameterCd={DISCHARGE},{GAGE_HEIGHT}"
            f"&period=PT{RISE_WINDOW_H * 2}H"
        )
        series = ((self._fetch(url).get("value") or {}).get("timeSeries")) or []

        out: dict[str, float | None] = {k: None for k in feature_keys()}
        for ts in series:
            site, param, points = self._describe(ts)
            label = self._sites.get(site)
            if label is None or not points:
                continue
            latest = points[-1][1]
            if param == GAGE_HEIGHT:
                out[f"usgs_{label}_gage_ft"] = round(latest, 3)
                rise = self._rise(points)
                out[f"usgs_{label}_rise_3h_ft"] = None if rise is None else round(rise, 3)
            elif param == DISCHARGE:
                out[f"usgs_{label}_flow_cfs"] = round(latest, 2)
        return out

    @staticmethod
    def _describe(ts: dict) -> tuple[str, str, list[tuple[datetime, float]]]:
        """(site_no, parameter_code, [(when, value), ...]) with no-data rows dropped."""
        site = (((ts.get("sourceInfo") or {}).get("siteCode") or [{}])[0]).get("value", "")
        param = (((ts.get("variable") or {}).get("variableCode") or [{}])[0]).get("value", "")
        raw = (((ts.get("values") or [{}])[0]).get("value")) or []

        points: list[tuple[datetime, float]] = []
        for p in raw:
            try:
                value = float(p["value"])
                when = datetime.fromisoformat(p["dateTime"])
            except (KeyError, TypeError, ValueError):
                continue
            if value != NO_DATA:
                points.append((when, value))
        points.sort(key=lambda x: x[0])
        return site, param, points

    @staticmethod
    def _rise(points: list[tuple[datetime, float]]) -> float | None:
        """Change in gage height over the last RISE_WINDOW_H hours.

        The downstream hydrograph's slope is the part that correlates with our upstream
        rain; the absolute stage is site-specific and not comparable to ours. Returns
        None when the series does not reach far enough back to be meaningful.
        """
        if len(points) < 2:
            return None
        latest_ts, latest_val = points[-1]
        target = latest_ts - timedelta(hours=RISE_WINDOW_H)
        earlier = [p for p in points if p[0] <= target]
        if not earlier:
            # Series is shorter than the window — a partial rise would understate it.
            return None
        return latest_val - earlier[-1][1]
