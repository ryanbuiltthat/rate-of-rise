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
from datetime import datetime, timezone

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
        events = []
        for f in self._fetch(url).get("features") or []:
            event = ((f.get("properties") or {}).get("event") or "").strip().lower()
            if event:
                events.append(event)
        return self._flags(events, log_active=True)

    def history_between(self, start: datetime, end: datetime):
        """Every product whose window overlaps [start, end], fetched once. The evaluator counts
        the ones in force at `as_of`: onset (else effective) <= as_of < ends (else expires).

        The archive holds every message, not just the ones still standing, so: a Cancel is not
        an alert, and what it cancels is dropped; an Update replaces what it references, so the
        referenced message is dropped rather than counted twice."""
        iso = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        url = (f"https://api.weather.gov/alerts?point={self._lat},{self._lon}"
               f"&start={iso(start)}&end={iso(end)}")
        features = [f for f in self._fetch(url).get("features") or [] if isinstance(f, dict)]
        superseded: set[str] = set()
        for f in features:
            p = f.get("properties") or {}
            if p.get("messageType") not in ("Cancel", "Update"):
                continue
            for ref in p.get("references") or []:
                if isinstance(ref, dict):
                    superseded.update(str(ref[k]) for k in ("identifier", "@id") if ref.get(k))
        spans = []
        for f in features:
            p = f.get("properties") or {}
            if p.get("messageType") == "Cancel":
                continue
            ids = {str(v) for v in (f.get("id"), p.get("id"), p.get("@id")) if v}
            if ids & superseded:
                continue
            event = (p.get("event") or "").strip().lower()
            try:
                begin = datetime.fromisoformat(p.get("onset") or p.get("effective"))
                finish = datetime.fromisoformat(p.get("ends") or p.get("expires"))
            except (TypeError, ValueError):
                continue
            if event:
                spans.append((begin, finish, event))
        return lambda as_of: self._flags([e for b, f, e in spans if b <= as_of < f],
                                         log_active=False)

    def _flags(self, events: list[str], log_active: bool) -> dict:
        # A Flash Flood Warning is also a flood warning for escalation purposes, so the
        # broader flag is set by either — callers should not have to check both.
        flash = any(_matches(e, FLASH_WARNING_EVENTS) for e in events)
        warning = flash or any(_matches(e, WARNING_EVENTS) for e in events)
        watch = any(_matches(e, WATCH_EVENTS) for e in events)
        if log_active and (warning or watch):
            log.info("NWS active flood products at site: %s", ", ".join(sorted(set(events))))
        return {
            "nws_flood_watch": 1.0 if watch else 0.0,
            "nws_flood_warning": 1.0 if warning else 0.0,
            "nws_flash_flood_warning": 1.0 if flash else 0.0,
            "nws_alert_count": float(len(events)),
        }
