"""Forecast/upstream data sources (spec Addendum C).

Each source exposes `name`, `refresh_seconds`, and `poll() -> dict[str, float|None]`.
`SourceCoordinator` polls each on its own cadence, merges the latest values, and keeps
the last-good result when a source errors — so a flaky API never stalls or crashes the
fast loop. All rainfall features are in inches.

The set of feature keys produced is declared in `FEATURE_KEYS` so the FeatureRow,
discovery, and dataset stay in sync.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from ..config import Config
from ..ha import HAClient
from .alerts import NwsAlerts
from .ero import WpcEro
from .google_floods import GoogleFloods
from .nwm import NwmReach
from .nws import NwsQpf
from .radar_cells import RadarCells
from .rain import RainAccumulator
from .snodas import SnodasSwe
from .usgs import SITES as USGS_SITES, UsgsDownstream, feature_keys as usgs_feature_keys
from .wu import WuUpstream

log = logging.getLogger("app.sources")

# Numeric features produced by the ingestion sources, in publish/record order.
FEATURE_KEYS = (
    # 2a — on-site rain + NWS QPF
    "rain_rate_in_hr",
    "rain_1h_in", "rain_3h_in", "rain_6h_in", "rain_24h_in", "rain_72h_in",
    "qpf_6h_in", "qpf_24h_in",
    # 2f — antecedent precipitation index (rides on the on-site rain samples)
    "api_index_in",
    # 2b — upstream WU + NWM reach
    "upstream_rain_1h_in", "upstream_rain_3h_in", "upstream_rain_6h_in",
    "upstream_rain_24h_in", "upstream_rain_72h_in", "upstream_precip_today_in",
    "nwm_flow_cfs", "nwm_flow_max_cfs",
    # 2c — USGS downstream gauges
    *usgs_feature_keys(),
    # 2d — NWS active alert products
    "nws_flood_watch", "nws_flood_warning", "nws_flash_flood_warning", "nws_alert_count",
    # 2e — SNODAS snowpack
    "snow_water_equivalent_in",
    # 2g — NEXRAD storm-cell tracks (inbound-cell early warning)
    "radar_cells_tracked", "radar_threat_cells",
    "radar_threat_eta_min", "radar_threat_max_dbz", "radar_threat_scan_count",
    # 2h — WPC Excessive Rainfall Outlook (day-scale flood-risk forecast)
    "wpc_ero_day1_risk", "wpc_ero_day2_risk", "wpc_ero_day3_risk",
    # 2i — Google Flood Forecasting status at the nearest modelled gauges
    "google_flood_severity", "google_flood_trend",
    "google_flood_gauge_mi", "google_flood_gauges",
    # 2j — Google Flash Flood polygon containment at the site itself
    "google_flash_flood_likely", "google_flash_flood_highly_likely",
    "google_flash_flood_events",
)


class SourceCoordinator:
    def __init__(self, cfg: Config, ha: HAClient, data_dir: Path):
        self._sources = []
        if cfg.onsite_rain_rate_entity:
            self._sources.append(RainAccumulator(data_dir, cfg.onsite_rain_rate_entity, ha))
        else:
            log.warning("onsite_rain_rate_entity unset — rain accumulation disabled")

        latlon = ha.get_lat_lon()
        if latlon:
            self._sources.append(NwsQpf(*latlon))
            self._sources.append(NwsAlerts(*latlon))
            log.info("NWS QPF + alerts enabled for lat/lon %.4f,%.4f", *latlon)
            if cfg.snodas_swe:
                self._sources.append(SnodasSwe(*latlon, data_dir))
                log.info("SNODAS SWE enabled")
            if cfg.nexrad_cells and cfg.nexrad_radar_id:
                self._sources.append(RadarCells(*latlon, cfg.nexrad_radar_id))
                log.info("NEXRAD cell tracking enabled (radar %s)", cfg.nexrad_radar_id)
            elif cfg.nexrad_cells:
                log.warning("nexrad_radar_id unset — NEXRAD cell tracking disabled")
            if cfg.wpc_ero:
                self._sources.append(WpcEro(*latlon))
                log.info("WPC Excessive Rainfall Outlook enabled")
            # The area search needs the site's coordinates, so this belongs with the
            # other lat/lon sources rather than beside the key-only WU block below.
            if cfg.google_floods_api_key:
                self._sources.append(GoogleFloods(*latlon, cfg.google_floods_api_key))
                log.info("Google Flood Forecasting enabled")
            else:
                log.info("Google Flood Forecasting disabled (needs google_floods_api_key)")
        else:
            log.warning("No lat/lon from HA config — NWS QPF, alerts, SNODAS, "
                        "NEXRAD cells, WPC ERO and Google Floods disabled")

        if cfg.wu_api_key and cfg.upstream_pws_ids:
            self._sources.append(WuUpstream(cfg.wu_api_key, cfg.upstream_pws_ids, data_dir))
            log.info("WU upstream enabled for %d station(s)", len(cfg.upstream_pws_ids))
        else:
            log.info("WU upstream disabled (needs wu_api_key + upstream_pws_ids)")

        # `bashio::config` renders an unset optional as the literal string "null", which
        # would otherwise enable the source and fail every poll against a bogus reach.
        reach_id = cfg.nwm_reach_id.strip()
        if reach_id and reach_id != "null":
            self._sources.append(NwmReach(reach_id))
            log.info("NWM reach %s enabled", reach_id)
        else:
            log.info("NWM reach disabled (needs nwm_reach_id)")

        if cfg.usgs_downstream:
            self._sources.append(UsgsDownstream())
            log.info("USGS downstream gauges enabled: %s", ", ".join(USGS_SITES))
        else:
            log.info("USGS downstream gauges disabled")

        self._cache: dict[str, float | None] = {}
        self._next_poll: dict[str, float] = {}
        self._last_ok: dict[str, float] = {}

    def features(self) -> dict:
        """Merged latest feature values; polls each source only when its interval elapses."""
        now = time.monotonic()
        for src in self._sources:
            if now >= self._next_poll.get(src.name, 0.0):
                try:
                    self._cache.update(src.poll())
                    self._last_ok[src.name] = now
                except Exception:  # keep last-good cache; never break the loop
                    log.exception("source %s poll failed", src.name)
                self._next_poll[src.name] = now + src.refresh_seconds
        return {k: self._cache.get(k) for k in FEATURE_KEYS}

    def health(self) -> dict[str, dict]:
        """Per-source liveness for the watchdogs.

        `age` is seconds since the source last polled successfully, or None if it never
        has. This is the thing the old HA-template watchdogs could not see: the
        coordinator keeps serving a source's last-good value indefinitely, so a feature
        still having a number proves nothing about whether its source is still alive.
        """
        now = time.monotonic()
        out = {}
        for src in self._sources:
            last = self._last_ok.get(src.name)
            out[src.name] = {
                "age": None if last is None else now - last,
                "stale_after": getattr(src, "stale_after_seconds", None)
                or max(4 * src.refresh_seconds, 1800),
            }
        return out

    def configured(self) -> set[str]:
        """Names of the sources actually built — an absent source is not a fault."""
        return {src.name for src in self._sources}
