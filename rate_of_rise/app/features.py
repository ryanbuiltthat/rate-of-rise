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
    # --- creek-gauge link state (see FeatureBuilder._rate_of_rise) ---
    # How old the stage reading is, whether the radio link that produced it is up, and how
    # many consecutive gap-free samples the current rate-of-rise was built from. Together
    # they are what separates "the creek rose this fast" from "the radio was down and came
    # back to a different number".
    stage_age_min: float | None = None
    creek_node_online: bool | None = None
    rate_of_rise_sample_count: float | None = None
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
DERIVED_KEYS = ("temp_f", "rain_on_snow_flag", "rate_of_rise_in_min",
                "stage_age_min", "creek_node_online", "rate_of_rise_sample_count")


# Above this soil-moisture reading the low-lying areas are effectively saturated
# and "pond", shortening the rainfall->runoff response. Tune with observed storms.
PONDING_SATURATION_PCT = 85.0

# Rain-on-snow (spec §1: a major regional flood driver). Rain falling on an existing snowpack
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


# Rate of rise is only as trustworthy as the two samples behind it, and the radio link to
# the creek node is the thing that decides whether there *were* two samples. When the node
# goes quiet the gateway does not blank `sensor.creek_gateway_stage` — it simply stops
# updating it — so Home Assistant keeps serving the last number the node managed to send.
# Naively differencing that against the first reading after the link returns charges the
# whole outage's worth of level change to a single loop interval: a creek that rose 3 in
# over a 40-minute dropout reads as 0.6 in/min (12x the Tier 3 threshold) instead of the
# 0.075 in/min it actually did. That is a Tier 3 Warning, and with the package's
# `critical_from_tier: 2` it is a critical, alarm-stream push at 3 AM for nothing.
#
# So the guards live here, where the number is made, rather than in tiers.py, where it is
# only compared: a rate that cannot be computed honestly is reported as None, and None fires
# no tier. tiers.py adds the second half — a confirmation window on the samples right after
# a reconnect (WARNING_RATE_OF_RISE_CONFIRM_SAMPLES).
RATE_OF_RISE_SAMPLE_CAP = 10.0   # the streak counter saturates here; it only gates the
                                 # first samples after a reconnect, so it need not grow


class FeatureBuilder:
    def __init__(self, cfg: Config, ha: HAClient, sources=None, now_fn=time.time):
        self._cfg = cfg
        self._ha = ha
        self._sources = sources   # SourceCoordinator | None (Addendum C)
        self._now = now_fn        # injectable so the link guards are testable (HealthTracker
                                  # takes the same parameter for the same reason)
        self._last_stage: tuple[float, float] | None = None  # (sample_ts, stage_ft)
        self._ror_samples = 0.0   # consecutive gap-free rates since the last re-seed
        self._temp_unit: str | None = None                   # cached on first read

    def _node_online(self) -> bool | None:
        """The gateway's own view of the radio link, or None if it is not configured.

        `binary_sensor.creek_gateway_creek_node_status` is driven by *packet arrival*
        (components/rfm69_gateway: `node_timeout`, 5 min = five missed 60 s reports), not
        by whether the stage number changed — which is the only honest link check on a
        creek that can legitimately sit at the same depth for an hour.
        """
        entity = self._cfg.creek_node_status_entity
        if not entity:
            return None
        return self._ha.get_bool(entity)

    def _rate_of_rise(
        self, now: float, stage_ft: float | None, age_s: float | None, online: bool | None,
    ) -> float | None:
        """Inches per minute between two *contiguous* stage samples.

        None whenever that cannot be said honestly: no reading, the link is down, the
        first reading back after a dropout, or a gap longer than
        `rate_of_rise_max_gap_minutes`. Each of those also re-seeds the baseline, so the
        next loop measures from the creek's real position rather than from wherever it
        was before the radio went quiet.
        """
        if stage_ft is None or not self._link_usable(age_s, online):
            self._reseed(None)
            return None

        # Timestamp the *reading*, not the poll: `last_updated` is when the gateway wrote
        # this value, so dt is the interval the creek actually moved over even when the
        # loop runs on a different cadence than the node's 60 s reports.
        sample_ts = now - age_s if age_s is not None else now
        prev = self._last_stage
        if prev is None:
            self._reseed((sample_ts, stage_ft))
            return None

        prev_ts, prev_stage = prev
        dt_min = (sample_ts - prev_ts) / 60.0

        if dt_min <= 0:
            # Same reading polled twice. The link is up (checked above), so the creek has
            # simply not moved enough to change the published value: the rate is zero, not
            # unknown. Carry the baseline forward to now so the next real change is
            # measured from here rather than from a timestamp that is already hours old.
            self._last_stage = (now, stage_ft)
            self._ror_samples = min(self._ror_samples + 1.0, RATE_OF_RISE_SAMPLE_CAP)
            return 0.0

        if dt_min > self._cfg.rate_of_rise_max_gap_minutes:
            # A gap. Whatever the creek did across it did not happen in one interval, and
            # attributing it to one is exactly the false alarm this guard exists for.
            log.info(
                "stage gap of %.1f min (limit %.1f) — rate of rise suppressed and re-seeded "
                "at %.2f ft", dt_min, self._cfg.rate_of_rise_max_gap_minutes, stage_ft,
            )
            self._reseed((sample_ts, stage_ft))
            return None

        self._last_stage = (sample_ts, stage_ft)
        self._ror_samples = min(self._ror_samples + 1.0, RATE_OF_RISE_SAMPLE_CAP)
        return (stage_ft - prev_stage) * 12.0 / dt_min

    def _link_usable(self, age_s: float | None, online: bool | None) -> bool:
        """Whether this stage reading is current enough to difference against another."""
        if online is False:
            return False
        if online is True:
            return True     # the gateway is hearing the node; a flat creek is not a fault
        # No connectivity entity to ask (or it is unavailable): fall back to how long ago
        # the value was written. Weaker — a genuinely steady creek can look stale — but it
        # is the only signal left, and erring towards "no rate" errs towards no alarm.
        return age_s is None or age_s <= self._cfg.stage_max_age_minutes * 60.0

    def _reseed(self, sample: tuple[float, float] | None) -> None:
        self._last_stage = sample
        self._ror_samples = 0.0

    def build(self) -> FeatureRow:
        ts = self._now()
        stage_ft, stage_age_s = self._ha.get_float_with_age(self._cfg.stage_entity)
        node_online = self._node_online()

        soils = [self._ha.get_float(e) for e in self._cfg.soil_moisture_entities]
        near_house = soils[0] if len(soils) >= 1 else None
        near_creek = soils[1] if len(soils) >= 2 else None
        present = [s for s in soils if s is not None]
        soil_mean = sum(present) / len(present) if present else None
        ponding = any(s >= PONDING_SATURATION_PCT for s in present)

        rate = self._rate_of_rise(ts, stage_ft, stage_age_s, node_online)
        row = FeatureRow(
            ts=ts,
            stage_ft=stage_ft,
            rate_of_rise_in_min=rate,
            soil_moisture_mean_pct=soil_mean,
            soil_moisture_near_house_pct=near_house,
            soil_moisture_near_creek_pct=near_creek,
            ponding_flag=ponding,
            stage_age_min=stage_age_s / 60.0 if stage_age_s is not None else None,
            creek_node_online=node_online,
            # Published even when the rate is None, so the dashboard can show *why* it is
            # blank (0 = the link just came back, nothing to difference against yet).
            rate_of_rise_sample_count=self._ror_samples,
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
