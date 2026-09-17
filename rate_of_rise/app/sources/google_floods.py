"""Google Flood Forecasting status (slice 2i).

Spec §3 lists Google's Flood Forecasting API and §5 wants "Google flood status" as a model
feature; open question #2 asks whether Google actually models a gauge near enough to this
creek to be worth anything. Both are answered by the same two calls, so this source makes
them:

  POST /v1/gauges:searchGaugesByArea            -> which gauges exist near the site
  GET  /v1/floodStatus:queryLatestFloodStatusByGaugeIds
                                                -> Google's current call on each of them

  https://developers.google.com/flood-forecasting/rest/v1/floodStatus

WHAT THIS IS, AND WHAT IT IS NOT. Google runs its own hydrologic model on gauges it
ingests (in the US, largely USGS ones) plus "virtual" HydroBASINS gauges on reaches with
no physical instrument, and publishes a forecast severity against that gauge's own model
thresholds. None of those gauges is *this* creek — the creek has no gauge, which is the
whole reason this project exists (§1). So a status here is a statement about the
neighbouring river network, in the same family as the USGS gauges in 2c: a regional
answer to the same rain, on a bigger and slower system. It is read as forecast context,
never as creek level, and deliberately tops out at Tier 2 Watch in `tiers.py` — Warning
and Emergency stay reserved for the creek's own gauge.

WHY IT IS STILL WORTH INGESTING. It is the only source here that is a *forecast of
flooding* rather than a forecast of weather. QPF (2a) says how much rain; the ERO (2h)
says whether that rain exceeds flash-flood guidance somewhere in a multi-county risk
area; NWM (2b) gives raw discharge for one reach with no notion of what is high for it.
Google's status is the one input that has already done the "is this number bad *here*"
step against per-gauge warning/danger/extreme thresholds, on named reaches a few miles
away.

GAUGE DISCOVERY. The area search takes an S2 `loop` — a spherical polygon whose vertices
run counter-clockwise, the interior lying to the left of the traversal. A clockwise loop
is not an error; it names the complement (everything on Earth outside the box), so the
box below is built counter-clockwise on purpose. Getting that backwards is not a
correctness bug here — every returned gauge is re-filtered against `SEARCH_RADIUS_MI`
with a real distance calculation — but it would fetch a continent to keep a handful of
gauges, so `test_google_floods.py` pins the winding.

Google's docs warn that gauges are added and removed and that the search result should
not be cached for long, so the list is re-discovered every `GAUGE_REFRESH_SECONDS`, not
once at startup. Non-quality-verified gauges are deliberately included: on a creek this
small the only candidate is likely to be a virtual HydroBASINS gauge, and those are
exactly what the quality-verified filter drops. The discovered set is logged with each
gauge's river, distance and verification state — that log line is the answer to open
question #2 for whoever reads it.

NOT INGESTED. `gaugeModels.batchGet` thresholds and `gauges.queryGaugeForecasts` values
would give a continuous "fraction of the way to warning level" rather than this 4-step
ladder, which is the better model feature. They are left for a later slice because the
values are per-gauge units (metres of stage on one, m³/s of discharge on another) that
mean nothing until they are paired with that gauge's own thresholds — real work, and
worth doing only once a gauge near enough to matter is known to exist. `flashFloods`
and `inundationMapSet` are satellite-derived products for ungauged basins outside the
US and have no bearing here.

KEY HANDLING. The key rides in the `X-Goog-Api-Key` header rather than the documented
`?key=` query parameter, so it cannot reach a log line or a traceback with the URL.
"""
from __future__ import annotations

import logging
import math
import time
from urllib.parse import urlencode

import requests

log = logging.getLogger("app.sources.google_floods")

FEATURE_KEYS = (
    "google_flood_severity",
    "google_flood_trend",
    "google_flood_gauge_mi",
    "google_flood_gauges",
)

BASE_URL = "https://floodforecasting.googleapis.com/v1"

# How far out to look for gauges. Wide enough to reach the receiving rivers that answer
# this upland's rain (the USGS analogs in 2c sit 7 and 11 mi out), narrow enough that a
# status is still about this weather. `tiers.py` applies its own, tighter radius before
# a status is allowed to raise a tier.
SEARCH_RADIUS_MI = 25.0
MAX_GAUGES = 10                  # nearest N; a status further out is regional noise
GAUGE_REFRESH_SECONDS = 24 * 3600
PAGE_SIZE = 1000                 # a box this size holds tens of gauges, not thousands

# Severity as an ordered ladder, the same shape as the ERO's (sources/ero.py): a real
# "Google looked and forecasts no flooding" is 0.0, and None means only that nothing
# could be read. UNKNOWN is Google's own "not enough data to say", so it is None too.
SEVERITY = {
    "NO_FLOODING": 0.0,
    "ABOVE_NORMAL": 1.0,
    "SEVERE": 2.0,
    "EXTREME": 3.0,
}
SEVERITY_LABELS = {
    0.0: "No flooding",
    1.0: "Above normal",
    2.0: "Severe",
    3.0: "Extreme",
}
# Reported by Google, carry no reading, and are not a surprise worth logging about.
SEVERITY_NO_READING = ("UNKNOWN", "SEVERITY_UNSPECIFIED", "")

TREND = {"RISE": 1.0, "NO_CHANGE": 0.0, "FALL": -1.0}
TREND_LABELS = {1.0: "Rising", 0.0: "Steady", -1.0: "Falling"}

EARTH_RADIUS_MI = 3958.8
MI_PER_DEG_LAT = 69.05


def _default_fetch(url: str, headers: dict, body: dict | None = None,
                   timeout: float = 20.0) -> dict:
    """GET when `body` is None, POST otherwise — the API uses both."""
    if body is None:
        r = requests.get(url, headers=headers, timeout=timeout)
    else:
        r = requests.post(url, headers=headers, json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _distance_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in statute miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * EARTH_RADIUS_MI * math.asin(min(1.0, math.sqrt(a)))


def search_box(lat: float, lon: float, radius_mi: float) -> list[dict]:
    """Counter-clockwise loop vertices for a box around the site (see module docstring)."""
    dlat = radius_mi / MI_PER_DEG_LAT
    # Longitude degrees shrink with latitude; guard the pole case so this never divides
    # by ~0 and asks for a loop spanning every meridian.
    dlon = radius_mi / (MI_PER_DEG_LAT * max(math.cos(math.radians(lat)), 0.01))
    return [
        {"latitude": lat - dlat, "longitude": lon - dlon},   # SW
        {"latitude": lat - dlat, "longitude": lon + dlon},   # SE
        {"latitude": lat + dlat, "longitude": lon + dlon},   # NE
        {"latitude": lat + dlat, "longitude": lon - dlon},   # NW
    ]


class GoogleFloods:
    name = "google_floods"
    # Google's riverine forecasts are issued on a roughly daily cycle, so this is far
    # more often than the data changes. It stays at half an hour anyway: the call is one
    # cheap request against at most MAX_GAUGES ids, and the point of a short interval is
    # that the watchdog notices the API going dark long before the next storm needs it.
    refresh_seconds = 30 * 60

    def __init__(self, lat: float, lon: float, api_key: str,
                 fetch=_default_fetch, now_fn=time.monotonic):
        self._lat = lat
        self._lon = lon
        self._key = api_key
        self._fetch = fetch
        self._now = now_fn
        self._gauges: dict[str, dict] | None = None   # gauge id -> {name, river, mi, ...}
        self._gauges_at = 0.0

    # --- gauge discovery ------------------------------------------------------------

    def _headers(self) -> dict:
        return {"X-Goog-Api-Key": self._key, "Content-Type": "application/json"}

    def _discover(self) -> dict[str, dict]:
        payload = self._fetch(
            f"{BASE_URL}/gauges:searchGaugesByArea",
            self._headers(),
            {
                "loop": {"vertices": search_box(self._lat, self._lon, SEARCH_RADIUS_MI)},
                # A creek this small will have a virtual HydroBASINS gauge at best, and
                # those are precisely what the quality-verified default filters out.
                "includeNonQualityVerified": True,
                # A gauge with no hydro model publishes no flood status, so it is only
                # weight in the response.
                "includeGaugesWithoutHydroModel": False,
                "pageSize": PAGE_SIZE,
            },
        )
        rows = payload.get("gauges") or []
        if payload.get("nextPageToken"):
            # Only reachable if the box somehow held more than PAGE_SIZE modelled gauges,
            # which at this radius would mean the loop was read as the complement. The
            # distance filter below still yields the right answer, so this is a note that
            # far too much was fetched, not a failure.
            log.warning("gauge search returned a full page of %d — check the loop winding",
                        PAGE_SIZE)

        found: dict[str, dict] = {}
        for row in rows:
            gauge_id = row.get("gaugeId")
            location = row.get("location") or {}
            try:
                mi = _distance_mi(self._lat, self._lon,
                                  float(location["latitude"]), float(location["longitude"]))
            except (KeyError, TypeError, ValueError):
                continue     # a gauge with no usable position cannot be ranked
            if not gauge_id or mi > SEARCH_RADIUS_MI:
                continue
            found[gauge_id] = {
                "mi": mi,
                "site": row.get("siteName") or "",
                "river": row.get("river") or "",
                "quality_verified": bool(row.get("qualityVerified")),
            }

        nearest = dict(sorted(found.items(), key=lambda kv: kv[1]["mi"])[:MAX_GAUGES])
        if nearest:
            log.info("Google Flood gauges within %.0f mi: %s", SEARCH_RADIUS_MI,
                     "; ".join(self._describe(gid, g) for gid, g in nearest.items()))
        else:
            # The answer to open question #2, if this is what it keeps saying: Google
            # models nothing near this creek, and the source is dead weight.
            log.info("Google Flood Forecasting has no modelled gauge within %.0f mi",
                     SEARCH_RADIUS_MI)
        return nearest

    @staticmethod
    def _describe(gauge_id: str, gauge: dict) -> str:
        label = gauge["site"] or gauge["river"] or gauge_id
        verified = "" if gauge["quality_verified"] else ", not quality-verified"
        return f"{gauge_id} ({label}, {gauge['mi']:.1f} mi{verified})"

    def _known_gauges(self) -> dict[str, dict]:
        """The discovered set, refreshed daily. A refresh that fails keeps the old set.

        Google asks that this list not be cached for long because gauges come and go, but
        a failed re-discovery is no reason to stop reading the statuses of gauges that
        were there yesterday — so a stale list is preferred to none, and the failure is
        surfaced only when there is nothing to fall back on.
        """
        now = self._now()
        if self._gauges is not None and now - self._gauges_at < GAUGE_REFRESH_SECONDS:
            return self._gauges
        try:
            self._gauges = self._discover()
        except Exception:
            if self._gauges is None:
                raise
            log.warning("Google Flood gauge re-discovery failed; keeping the known %d "
                        "gauge(s)", len(self._gauges), exc_info=True)
            return self._gauges
        self._gauges_at = now
        return self._gauges

    # --- flood status ---------------------------------------------------------------

    def _statuses(self, gauge_ids: list[str]) -> list[dict]:
        # Repeated proto fields transcode to a repeated bare query parameter; the REST
        # reference spells it `gaugeIds[]`, which is its notation for exactly that.
        query = urlencode([("gaugeIds", gid) for gid in gauge_ids])
        payload = self._fetch(
            f"{BASE_URL}/floodStatus:queryLatestFloodStatusByGaugeIds?{query}",
            self._headers(),
        )
        return payload.get("floodStatuses") or []

    @staticmethod
    def _severity(status: dict) -> float | None:
        raw = (status.get("severity") or "").strip().upper()
        if raw in SEVERITY:
            return SEVERITY[raw]
        if raw not in SEVERITY_NO_READING:
            # A severity this code does not know is not "no flooding" — reporting 0.0
            # would silently downgrade whatever Google meant.
            log.warning("unrecognized flood severity %r for gauge %s",
                        status.get("severity"), status.get("gaugeId"))
        return None

    def poll(self) -> dict:
        blank: dict[str, float | None] = {k: None for k in FEATURE_KEYS}
        gauges = self._known_gauges()
        if not gauges:
            # Nothing modelled nearby is a real reading, not a fault: zero gauges is the
            # honest count, and the rest stay unknown because nobody looked.
            return {**blank, "google_flood_gauges": 0.0}

        statuses = self._statuses(list(gauges))
        out = {**blank, "google_flood_gauges": float(len(statuses))}

        # One gauge governs all three of the remaining features, so they always describe
        # the same place: the worst severity on offer, nearest first among equals. Mixing
        # a severity from one gauge with a trend from another would read as a single
        # coherent forecast and be nothing of the kind.
        governing: tuple[float, float, dict] | None = None
        for status in statuses:
            severity = self._severity(status)
            gauge = gauges.get(status.get("gaugeId"))
            if severity is None or gauge is None:
                continue
            rank = (severity, -gauge["mi"])
            if governing is None or rank > governing[:2]:
                governing = (severity, -gauge["mi"], status)

        if governing is not None:
            severity, neg_mi, status = governing
            out["google_flood_severity"] = severity
            out["google_flood_gauge_mi"] = round(-neg_mi, 1)
            out["google_flood_trend"] = TREND.get(
                (status.get("forecastTrend") or "").strip().upper())
        return out
