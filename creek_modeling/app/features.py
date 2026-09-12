"""Feature builders.

Builds the live feature row each fast loop: the cheap local features computed here
(stage, rate-of-rise, soil moisture, ponding) plus everything the SourceCoordinator
ingests (rain accumulations, the antecedent precipitation index, QPF, NWS alerts, upstream
PWS, NWM reach, USGS gauges, SNODAS snowpack). Cross-source features that belong to no
single source — temperature normalisation and the rain-on-snow flag — are derived here.
Still outstanding from spec §5: Google flood status.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass

from .config import Config
from .ha import HAClient

log = logging.getLogger("app.features")


@dataclass
class FeatureRow:
    ts: float                       # unix seconds
    stage_ft: float | None          # current creek stage
    rate_of_rise_in_min: float | None
    soil_moisture_mean_pct: float | None
    soil_moisture_near_house_pct: float | None   # WH51 #1, by the willow = entities[0]
    soil_moisture_near_creek_pct: float | None   # WH51 #2, near the creek = entities[1]
    ponding_flag: bool              # low-lying sensors saturated -> fast runoff
    # --- forecast/upstream features (Addendum C, filled by SourceCoordinator) ---
    rain_rate_in_hr: float | None = None   # raw Ecowitt rate; None = entity unavailable
    rain_1h_in: float | None = None
    rain_3h_in: float | None = None
    rain_6h_in: float | None = None
    rain_24h_in: float | None = None
    rain_72h_in: float | None = None
    qpf_6h_in: float | None = None
    qpf_24h_in: float | None = None
    api_index_in: float | None = None   # antecedent wetness, basin-wide (spec §4/§5)
    upstream_rain_1h_in: float | None = None
    upstream_rain_3h_in: float | None = None
    upstream_rain_6h_in: float | None = None
    upstream_rain_24h_in: float | None = None
    upstream_rain_72h_in: float | None = None
    upstream_precip_today_in: float | None = None
    nwm_flow_cfs: float | None = None
    nwm_flow_max_cfs: float | None = None
    # USGS downstream gauges — no gauge of our own yet, so these carry the only
    # observed rainfall->response signal available (spec §1, slice 2c).
    usgs_leggetts_gage_ft: float | None = None
    usgs_leggetts_flow_cfs: float | None = None
    usgs_leggetts_rise_3h_ft: float | None = None
    usgs_tunkhannock_gage_ft: float | None = None
    usgs_tunkhannock_flow_cfs: float | None = None
    usgs_tunkhannock_rise_3h_ft: float | None = None
    # NWS active alert products — 0/1 flags (spec §3/§6).
    nws_flood_watch: float | None = None
    nws_flood_warning: float | None = None
    nws_flash_flood_warning: float | None = None
    nws_alert_count: float | None = None
    # SNODAS snowpack + the rain-on-snow condition it enables (spec §1/§5).
    snow_water_equivalent_in: float | None = None
    # NEXRAD storm-cell tracks (slice 2g) — the only leading indicator for cells on the
    # dominant W/NW approach, which reach the house before any upstream gauge sees rain.
    radar_cells_tracked: float | None = None
    radar_threat_cells: float | None = None
    radar_threat_eta_min: float | None = None
    radar_threat_max_dbz: float | None = None
    radar_threat_scan_count: float | None = None
    # WPC Excessive Rainfall Outlook (slice 2h) — day-scale flood-risk categories
    # (0 none · 1 Marginal · 2 Slight · 3 Moderate · 4 High). 0.0 means WPC drew no
    # risk area here; None means the product could not be read.
    wpc_ero_day1_risk: float | None = None
    wpc_ero_day2_risk: float | None = None
    wpc_ero_day3_risk: float | None = None
    temp_f: float | None = None
    rain_on_snow_flag: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


# Features derived here rather than by a source. They are not in sources.FEATURE_KEYS, so
# they must be added explicitly wherever the feature payload is assembled.
DERIVED_KEYS = ("temp_f", "rain_on_snow_flag")


# Above this soil-moisture reading the low-lying areas are effectively saturated
# and "pond", shortening the rainfall->runoff response. Tune with observed storms.
PONDING_SATURATION_PCT = 85.0

# Rain-on-snow (spec §1: a major the region flood driver). Rain falling on an existing snowpack
# at above-freezing temperatures both adds its own water and melts the pack, so runoff far
# exceeds what the rainfall alone suggests. PLACEHOLDER thresholds.
ROS_MIN_SWE_IN = 0.20        # below this the pack holds too little water to matter
ROS_MIN_TEMP_F = 34.0        # melting, and precip is falling as rain rather than snow
ROS_MIN_RAIN_1H_IN = 0.02    # rain actually falling now
ROS_MIN_QPF_6H_IN = 0.10     # ...or forecast within the warning window


def _to_fahrenheit(value: float | None, unit: str | None) -> float | None:
    """Normalize a temperature reading to °F; HA may report either scale."""
    if value is None:
        return None
    return value * 9.0 / 5.0 + 32.0 if unit and "C" in unit.upper() else value


class FeatureBuilder:
    def __init__(self, cfg: Config, ha: HAClient, sources=None):
        self._cfg = cfg
        self._ha = ha
        self._sources = sources   # SourceCoordinator | None (Addendum C)
        self._last_stage: tuple[float, float] | None = None  # (ts, stage_ft)
        self._temp_unit: str | None = None                   # cached on first read

    def _rate_of_rise(self, ts: float, stage_ft: float | None) -> float | None:
        """Inches per minute since the previous sample; None on first/invalid."""
        if stage_ft is None:
            return None
        prev = self._last_stage
        self._last_stage = (ts, stage_ft)
        if prev is None:
            return None
        prev_ts, prev_stage = prev
        dt_min = (ts - prev_ts) / 60.0
        if dt_min <= 0:
            return None
        return (stage_ft - prev_stage) * 12.0 / dt_min

    def build(self) -> FeatureRow:
        ts = time.time()
        stage_ft = self._ha.get_float(self._cfg.stage_entity)

        soils = [self._ha.get_float(e) for e in self._cfg.soil_moisture_entities]
        near_house = soils[0] if len(soils) >= 1 else None
        near_creek = soils[1] if len(soils) >= 2 else None
        present = [s for s in soils if s is not None]
        soil_mean = sum(present) / len(present) if present else None
        ponding = any(s >= PONDING_SATURATION_PCT for s in present)

        row = FeatureRow(
            ts=ts,
            stage_ft=stage_ft,
            rate_of_rise_in_min=self._rate_of_rise(ts, stage_ft),
            soil_moisture_mean_pct=soil_mean,
            soil_moisture_near_house_pct=near_house,
            soil_moisture_near_creek_pct=near_creek,
            ponding_flag=ponding,
        )
        if self._sources is not None:
            for key, value in self._sources.features().items():
                setattr(row, key, value)

        # Cross-source, so it is derived here rather than inside any single source.
        row.temp_f = self._temp_f()
        row.rain_on_snow_flag = self._rain_on_snow(row)

        log.debug("Built feature row: %s", row)
        return row

    def _temp_f(self) -> float | None:
        entity = self._cfg.onsite_temp_entity
        if not entity:
            return None
        if self._temp_unit is None:
            self._temp_unit = self._ha.get_unit(entity) or ""
        return _to_fahrenheit(self._ha.get_float(entity), self._temp_unit)

    @staticmethod
    def _rain_on_snow(row: FeatureRow) -> bool:
        """Rain falling on a melting snowpack — every input must be present to fire."""
        if row.snow_water_equivalent_in is None or row.temp_f is None:
            return False
        if row.snow_water_equivalent_in < ROS_MIN_SWE_IN or row.temp_f < ROS_MIN_TEMP_F:
            return False
        raining = (row.rain_1h_in or 0.0) >= ROS_MIN_RAIN_1H_IN
        forecast = (row.qpf_6h_in or 0.0) >= ROS_MIN_QPF_6H_IN
        return raining or forecast
