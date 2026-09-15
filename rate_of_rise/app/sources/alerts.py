"""NWS active alert products (slice 2d).

Spec §3 wants the active Flood Watch/Warning products for the county, and §6 makes an
NWS Flood Warning force-promote the alert tier regardless of what our own sensors say.
That rule matters most exactly when our instrumentation is weakest: a forecaster who has
issued a warning knows things our rain gauges do not.

Free NWS API, no key, one hop:
  GET api.weather.gov/alerts/active?point={lat},{lon}

Returns a GeoJSON FeatureCollection whose `properties.event` carries the product name
("Flood Warning", "Flash Flood Warning", "Flood Watch", …). Querying by point rather than
by county zone means we only see alerts whose polygon actually covers the site.

Features are 0/1 flags so they travel through the numeric feature row and land as HA
binary-ish sensors; `nws_alert_count` is the total active product count for visibility.
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger("app.sources.alerts")

USER_AGENT = "rate-of-rise (github.com/ryanbuiltthat/rate-of-rise)"

# Matched case-insensitively against `properties.event`. Flash-flood products are kept
# separate: in a basin this flashy they are the more urgent signal, and §6's escalation
# for them is steeper than for a generic areal flood warning.
FLASH_WARNING_EVENTS = ("flash flood warning", "flash flood emergency")
WARNING_EVENTS = ("flood warning", "river flood warning", "areal flood warning")
WATCH_EVENTS = ("flood watch", "flash flood watch", "river flood watch")


def _default_fetch(url: str, timeout: float = 15.0) -> dict:
    r = requests.get(url, headers={"User-Agent": USER_AGENT,
                                   "Accept": "application/geo+json"}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _matches(event: str, needles: tuple[str, ...]) -> bool:
    return any(n in event for n in needles)


class NwsAlerts:
    name = "alerts"
    refresh_seconds = 5 * 60      # alerts are time-critical; poll near the fast-loop rate

    def __init__(self, lat: float, lon: float, fetch=_default_fetch):
        self._lat, self._lon = lat, lon
        self._fetch = fetch

    def poll(self) -> dict:
        url = f"https://api.weather.gov/alerts/active?point={self._lat},{self._lon}"
        features = self._fetch(url).get("features") or []

        events = []
        for f in features:
            event = ((f.get("properties") or {}).get("event") or "").strip().lower()
            if event:
                events.append(event)

        # A Flash Flood Warning is also a flood warning for escalation purposes, so the
        # broader flag is set by either — callers should not have to check both.
        flash = any(_matches(e, FLASH_WARNING_EVENTS) for e in events)
        warning = flash or any(_matches(e, WARNING_EVENTS) for e in events)
        watch = any(_matches(e, WATCH_EVENTS) for e in events)

        if warning or watch:
            log.info("NWS active flood products at site: %s", ", ".join(sorted(set(events))))

        return {
            "nws_flood_watch": 1.0 if watch else 0.0,
            "nws_flood_warning": 1.0 if warning else 0.0,
            "nws_flash_flood_warning": 1.0 if flash else 0.0,
            "nws_alert_count": float(len(events)),
        }
