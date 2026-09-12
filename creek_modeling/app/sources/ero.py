"""WPC Excessive Rainfall Outlook (slice 2h).

The one national product aimed squarely at this system's purpose: the Weather
Prediction Center's ERO forecasts the probability that rainfall will exceed *flash
flood guidance* — exceed what the ground can currently absorb — as risk areas
(Marginal / Slight / Moderate / High), issued several times daily for days 1-3.

That is categorically different from the QPF already ingested (2a). QPF says how much
rain; the ERO says whether that much rain is a flooding problem for that soil, in that
region, today. It folds in antecedent conditions and flash-flood guidance that nothing
else in the feature set sees, and it exists *before* anything is on radar — the
day-scale "today is a bad day" signal, where radar cells (2g) are the hour-scale one
and the gauges are the now-scale one. On 2026-08-02, the first live query of this
service found the site under a Day-1 SLGT during the very storm that had just raised
the Watch tier — a risk area that had been in place since the morning.

Served as a point query by the Iowa Environmental Mesonet (the same infrastructure 2g
already depends on), which ingests the WPC product and answers "what outlooks cover
this point right now" — no shapefile parsing, no polygon math. Free, no key:

  GET mesonet.agron.iastate.edu/api/1/nws/outlook_by_point.json?lon=...&lat=...

The response carries every current SPC/WPC outlook for the point; ERO rows have
outlook_type "E" and category "CATEGORICAL". SPC convective rows (type "C") are
deliberately not ingested: they grade severe-weather risk (hail/wind/tornado), not
rainfall, and the ERO is already the rain-specific distillation of the same setups.
WPC has extended the ERO beyond day 3 (day 4-5 rows appear); days 1-3 are ingested as
the product's calibrated core.

An absent ERO row for a day is a real reading — WPC looked and drew no risk area over
the site — and is reported as 0.0, not None. None means only "could not read the
product", which the ero watchdog then surfaces.
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger("app.sources.ero")

FEATURE_KEYS = ("wpc_ero_day1_risk", "wpc_ero_day2_risk", "wpc_ero_day3_risk")

DAYS = (1, 2, 3)

# WPC's categorical ladder, as numbers the tiers and the model can order.
RISK = {"MRGL": 1.0, "SLGT": 2.0, "MDT": 3.0, "HIGH": 4.0}

RISK_LABELS = {0.0: "none", 1.0: "Marginal", 2.0: "Slight", 3.0: "Moderate", 4.0: "High"}


def _default_fetch(url: str, timeout: float = 15.0) -> dict:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


class WpcEro:
    name = "ero"
    refresh_seconds = 30 * 60    # issued ~4x daily; a point query this cheap can be eager

    def __init__(self, lat: float, lon: float, fetch=_default_fetch):
        self._lat = lat
        self._lon = lon
        self._fetch = fetch

    def poll(self) -> dict:
        url = ("https://mesonet.agron.iastate.edu/api/1/nws/outlook_by_point.json"
               f"?lon={self._lon}&lat={self._lat}")
        payload = self._fetch(url)
        rows = payload.get("data") if isinstance(payload, dict) else payload

        # Start every tracked day at "WPC drew no risk area here" and upgrade from rows.
        out: dict[str, float | None] = {f"wpc_ero_day{d}_risk": 0.0 for d in DAYS}
        for row in rows or []:
            try:
                day = int(row.get("day"))
                if row.get("outlook_type") != "E" or row.get("category") != "CATEGORICAL":
                    continue
                if day not in DAYS:
                    continue
            except (TypeError, ValueError):
                continue
            threshold = row.get("threshold")
            risk = RISK.get(threshold)
            if risk is None:
                # A category this code does not know is not "no risk" — reporting 0.0
                # would silently downgrade whatever WPC meant. Unknown stays unknown.
                log.warning("unrecognized ERO threshold %r for day %d", threshold, day)
                out[f"wpc_ero_day{day}_risk"] = None
                continue
            key = f"wpc_ero_day{day}_risk"
            if out[key] is None or risk > out[key]:
                out[key] = risk
        return out
