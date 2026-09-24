"""Feature builders.

Builds the live feature row each fast loop: the cheap local features computed here
(stage, rate-of-rise, soil moisture, ponding) plus everything the SourceCoordinator
ingests (rain accumulations, the antecedent precipitation index, QPF, NWS alerts, upstream
PWS, NWM reach, USGS gauges, SNODAS snowpack, Google flood status). Cross-source
features that belong to no single source — temperature normalisation and the
rain-on-snow flag — are derived here.
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
    # What the gauge actually reported, before the plausibility check — kept so a rejected
    # reading is still on record — and whether that check threw it out (stage_ft is then
    # None). See FeatureBuilder._plausible_stage.
    stage_raw_ft: float | None = None
    stage_implausible: bool | None = None
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
    # Google Flood Forecasting (slice 2i) — its model's current call on the nearest
    # gauges it runs, which are neighbouring rivers rather than this creek. Severity is
    # 0 no flooding · 1 above normal · 2 severe · 3 extreme; trend is +1 rising / 0
    # steady / -1 falling; `gauge_mi` is how far away the gauge that set the severity
    # is, without which the severity cannot be read. None means nothing could be read;
    # `gauges` = 0.0 means Google models nothing near here at all.
    google_flood_severity: float | None = None
    google_flood_trend: float | None = None
    google_flood_gauge_mi: float | None = None
    google_flood_gauges: float | None = None
    # Google Flash Flood polygons (slice 2j) — direct containment of the site itself in
    # Google's forecast flash-flood area, not a nearby gauge's severity. `likely`/
    # `highly_likely` are 0/1 flags (1.0 = the site is inside; 0.0 = a real reading that
    # it is not; None only if nothing could be read); `events` is the count of active
    # national events the site falls inside (almost always 0, occasionally 1; a count
    # rather than a flag because overlap across events is possible).
    google_flash_flood_likely: float | None = None
    google_flash_flood_highly_likely: float | None = None
    google_flash_flood_events: float | None = None
    temp_f: float | None = None
    rain_on_snow_flag: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


# Features derived here rather than by a source. They are not in sources.FEATURE_KEYS, so
# they must be added explicitly wherever the feature payload is assembled.
DERIVED_KEYS = ("temp_f", "rain_on_snow_flag", "rate_of_rise_in_min",
                "stage_age_min", "creek_node_online", "rate_of_rise_sample_count",
                "stage_raw_ft", "stage_implausible")


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

# Loop timing is not exact: a baseline taken "10 minutes ago" by a 5-minute loop can be
# 9 min 58 s old. Without this slack that reading would miss the window and the rate would
# silently stretch to 15 min.
RATE_WINDOW_SLACK_S = 60.0

# How long a rejected jump must persist before it is believed anyway. The plausibility
# check (FeatureBuilder._plausible_stage) can only compare against the last good reading,
# and a creek genuinely can outrun the budget — a big rise while the radio was down, or
# one faster than max_stage_rise_in_min. Without an exit that reading would be withheld
# forever. The operator hears about the rejection at once (the stage_implausible watchdog,
# with the raw reading); this only bounds how long the tiers wait for them.
IMPLAUSIBLE_ACCEPT_AFTER_S = 30 * 60.0


class FeatureBuilder:
    def __init__(self, cfg: Config, ha: HAClient, sources=None, now_fn=time.time):
        self._cfg = cfg
        self._ha = ha
        self._sources = sources   # SourceCoordinator | None (Addendum C)
        self._now = now_fn        # injectable so the link guards are testable (HealthTracker
                                  # takes the same parameter for the same reason)
        # (sample_ts, stage_ft), oldest first, trimmed to what the rate window still needs.
        self._stage_hist: list[tuple[float, float]] = []
        self._ror_samples = 0.0   # consecutive gap-free rates since the last re-seed
        # Last reading the plausibility check accepted: (sample_ts, stage_ft). Deliberately
        # kept across link dropouts — see _plausible_stage.
        self._last_good: tuple[float, float] | None = None
        self._implausible_since: float | None = None
        self._temp_unit: str | None = None                   # cached on first read

    def _node_online(self) -> bool | None:
        """Whether the creek node is really reporting right now, or None if unknown.

        `binary_sensor.creek_gateway_creek_node_status` is driven by *packet arrival*
        (components/rfm69_gateway: `node_timeout`, 5 min = five missed 60 s reports), not
        by whether the stage number changed — which is the only honest link check on a
        creek that can legitimately sit at the same depth for an hour.

        But that sensor only writes on a transition, so it cannot report the gateway's own
        death: a gateway that stops publishing leaves it frozen at `on`, and on its own it
        told this code the link was fine through exactly that. The packet counter moves on
        every report, so a counter that has stopped moving overrules it. An unavailable
        counter counts as stopped — the gateway is rebooting, and the stage it last served
        is no fresher than the counter.
        """
        status = None
        if self._cfg.creek_node_status_entity:
            status = self._ha.get_bool(self._cfg.creek_node_status_entity)
        packets = self._cfg.creek_node_packets_entity
        if packets:
            value, age_s = self._ha.get_float_with_age(packets)
            if value is None and age_s is None:
                return status          # entity missing entirely: nothing to overrule with
            if value is None or age_s > self._cfg.stage_max_age_minutes * 60.0:
                return False
            if status is None:
                return True            # counter moving is itself proof the link is up
        return status

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

    def _plausible_stage(self, sample_ts: float, stage_ft: float) -> bool:
        """Whether a reading is a rise the creek could physically have made.

        The failure this exists for, from the field: a radar that has not warmed up answers
        0, the gateway treats anything that close as water inside the blanking zone and
        clamps depth to the range ceiling (37.6 in), and a creek sitting at 11 in "rose"
        26 in between two readings. Stage alone raised a Tier 4 Emergency on a dry
        2026-09-17. A jump like that is not a flood, it is a sensor fault, and the tier
        must not treat it as either a reading or an alarm.

        Rises only: falling fast raises no alarm, and a glitch that got through has to be
        able to come back down. The budget stops growing at `rate_of_rise_max_gap_minutes`,
        so a sensor stuck on the bad value does not become "plausible" merely by staying
        there. The baseline survives link dropouts on purpose: the 2026-09-17 reading was
        the *first* one after a 24-minute dropout — the node came back with a cold radar —
        so trusting whatever arrives after a reconnect would have let exactly it through.

        The price is that a creek which really does outrun the budget (a big rise while the
        radio was down) is withheld too. IMPLAUSIBLE_ACCEPT_AFTER_S bounds that: a jump that
        persists that long is believed. The stage_implausible watchdog pushes the raw
        reading to the phones the moment the first one is rejected, so a person is looking
        long before the tiers give in.
        """
        if self._last_good is None:
            self._last_good = (sample_ts, stage_ft)
            return True
        good_ts, good_stage = self._last_good
        minutes = (sample_ts - good_ts) / 60.0
        minutes = min(max(minutes, 1.0), self._cfg.rate_of_rise_max_gap_minutes)
        rise_in = (stage_ft - good_stage) * 12.0
        if rise_in > self._cfg.max_stage_rise_in_min * minutes:
            if self._implausible_since is None:
                self._implausible_since = sample_ts
            held_s = sample_ts - self._implausible_since
            if held_s < IMPLAUSIBLE_ACCEPT_AFTER_S:
                log.warning(
                    "stage %.2f ft rejected as implausible: %.1f in above the last good "
                    "reading (%.2f ft) — faster than %.1f in/min. Treating it as a sensor "
                    "fault, not a flood (%.0f min so far).", stage_ft, rise_in, good_stage,
                    self._cfg.max_stage_rise_in_min, held_s / 60.0)
                return False
            log.warning("stage %.2f ft has held for %.0f min — accepting it as real despite "
                        "the jump from %.2f ft", stage_ft, held_s / 60.0, good_stage)
        self._implausible_since = None
        self._last_good = (sample_ts, stage_ft)
        return True

    def _rate_of_rise(self, sample_ts: float, stage_ft: float) -> float | None:
        """Inches per minute across the last `rate_of_rise_window_minutes`.

        Measured against the newest reading at least a window old, never the previous one.
        The node reports whole millimetres and a still creek flickers a millimetre or two
        between reports, and the two readings consecutive loops see can be one report
        (~63 s) apart: 2 mm over 63 s is 0.075 in/min, over the 0.05 in/min Warning, from a
        creek that is not moving. Over 10 min the same flicker is 0.008 in/min, while a real
        Warning-rate rise is half an inch — nothing is lost but the noise.

        None until the history spans a window (after a start or a re-seed), and after a
        gap longer than `rate_of_rise_max_gap_minutes`, which re-seeds: whatever the creek
        did across a gap did not happen in one interval.
        """
        hist = self._stage_hist
        if hist:
            gap_s = sample_ts - hist[-1][0]
            if gap_s > self._cfg.rate_of_rise_max_gap_minutes * 60.0:
                log.info(
                    "stage gap of %.1f min (limit %.1f) — rate of rise suppressed and "
                    "re-seeded at %.2f ft", gap_s / 60.0,
                    self._cfg.rate_of_rise_max_gap_minutes, stage_ft)
                self._reseed()
            elif gap_s <= 0:
                # The same reading again, with no link signal to vouch that it is current:
                # nothing new to add. The rate as of the newest sample still stands.
                return self._rate_from_history()
        hist.append((sample_ts, stage_ft))
        return self._rate_from_history()

    def _rate_from_history(self) -> float | None:
        hist = self._stage_hist
        if len(hist) < 2:
            return None
        latest_ts, latest_stage = hist[-1]
        need_s = self._cfg.rate_of_rise_window_minutes * 60.0 - RATE_WINDOW_SLACK_S
        base = None
        for i in range(len(hist) - 2, -1, -1):
            if latest_ts - hist[i][0] >= need_s:
                base = i
                break
        if base is None:
            return None
        del hist[:base]                    # older than the baseline: never needed again
        base_ts, base_stage = hist[0]
        self._ror_samples = min(self._ror_samples + 1.0, RATE_OF_RISE_SAMPLE_CAP)
        return (latest_stage - base_stage) * 12.0 / ((latest_ts - base_ts) / 60.0)

    def _reseed(self) -> None:
        self._stage_hist.clear()
        self._ror_samples = 0.0

    def build(self) -> FeatureRow:
        ts = self._now()
        stage_raw, stage_age_s = self._ha.get_float_with_age(self._cfg.stage_entity)
        node_online = self._node_online()

        soils = [self._ha.get_float(e) for e in self._cfg.soil_moisture_entities]
        near_house = soils[0] if len(soils) >= 1 else None
        near_creek = soils[1] if len(soils) >= 2 else None
        present = [s for s in soils if s is not None]
        soil_mean = sum(present) / len(present) if present else None
        ponding = any(s >= PONDING_SATURATION_PCT for s in present)

        stage_ft, rate, implausible = stage_raw, None, None
        if stage_raw is None or not self._link_usable(stage_age_s, node_online):
            # No reading, or the radio is down and HA is serving the last one it heard.
            # No rate across the gap. The plausibility baseline is kept (see
            # _plausible_stage for why the reading after a dropout is the suspect one).
            self._reseed()
        else:
            # With the link confirmed up, the reading HA holds is the creek as of now —
            # `last_updated` only moves when the value changes, so it dates the last change,
            # not the last report. Without that confirmation, date it by the write.
            sample_ts = ts if node_online is True or stage_age_s is None else ts - stage_age_s
            implausible = not self._plausible_stage(sample_ts, stage_raw)
            if implausible:
                # Withheld from the tiers, the rate history and the storm peaks alike —
                # but not a gap: the rate resumes from the same history once readings are
                # sane again, unless the fault outlasts rate_of_rise_max_gap_minutes.
                stage_ft = None
            else:
                rate = self._rate_of_rise(sample_ts, stage_raw)

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
            stage_raw_ft=stage_raw,
            stage_implausible=implausible,
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
