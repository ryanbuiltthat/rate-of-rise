"""Alert-tier evaluation (spec §6).

Lives in the add-on so the tier is published over MQTT (and auto-created via discovery)
instead of living in an HA template.

Tiers 0–3 in the spec table are *escalation* levels; §6 also calls for an explicit
all-clear. This module therefore emits 0–4, where 0 is All-clear and 1–4 are the spec's
Advisory / Watch / Warning / Emergency:

    0 All-clear   nothing elevated
    1 Advisory    forecast risk        — QPF + antecedent wetness      (no gauge needed)
    2 Watch       rain materializing   — upstream/on-site accumulation (no gauge needed)
    3 Warning     creek responding     — stage / rate-of-rise          (needs the gauge)
    4 Emergency   flood imminent       — stage near bank / high P      (needs the gauge)

The Advisory and Watch rules are deliberately gauge-independent: they run off forecast
and rainfall features that are already flowing, so the system issues useful warnings
before the SEN0676 is mounted. Warning/Emergency stay dormant (their inputs are None)
until the creek node reports.

Active NWS flood products additionally impose a *floor* on the tier (§6), so a forecaster
issuing a warning escalates us regardless of what our own instruments show.

Every rule that fires contributes a human-readable reason, published as an attribute so
the dashboard can say *why* rather than just showing a number.

ALL THRESHOLDS BELOW ARE PLACEHOLDERS AND UNCALIBRATED. Stage values depend on the
surveyed datum (open question #5), the soil percentages on WH51 field calibration (open
question #7), and the rainfall values on observed storms (Phase 3). Treat a tier as a
prompt to go look, not as a validated alarm.
"""
from __future__ import annotations

import logging

from .features import FeatureRow
from .sources.ero import RISK_LABELS as ERO_LABELS

log = logging.getLogger("app.tiers")

TIER_LABELS = {
    0: "All-clear",
    1: "Advisory",
    2: "Watch",
    3: "Warning",
    4: "Emergency",
}

# --- Tier 1 Advisory: forecast risk (spec §6 "QPF >= X in 24 h AND soil >= Y%") ---
ADVISORY_QPF_24H_IN = 1.0
ADVISORY_SOIL_PCT = 70.0
ADVISORY_QPF_6H_IN = 0.75          # heavy short-fuse rain earns an advisory on its own
# Basin-wide antecedent wetness, as an alternative to the two buried WH51 probes: the
# probes measure two points, the index summarises weeks of rainfall over the whole basin.
# Either route can raise the advisory, so a failed probe cannot mask a saturated basin.
ADVISORY_API_INDEX_IN = 2.0
# WPC Excessive Rainfall Outlook (0 none · 1 MRGL · 2 SLGT · 3 MDT · 4 HIGH). The ERO
# already folds antecedent conditions and flash-flood guidance into its categories, so
# Moderate+ is an advisory on its own — WPC is saying flash flooding is likely somewhere
# in the risk area. Slight is common enough in summer that alone it would make Advisory
# near-permanent; over ground that is already wet it means today's rain runs off, so it
# advises only in combination.
ADVISORY_ERO_ALONE = 3.0        # Moderate or High
ADVISORY_ERO_WITH_WET_GROUND = 2.0

# --- Rain-on-snow: rain actually falling (rather than forecast) onto a pack ---
ROS_WATCH_RAIN_1H_IN = 0.05

# --- Tier 2 Watch: rain materializing upstream, creek not yet responding ---
WATCH_UPSTREAM_3H_IN = 0.75
WATCH_UPSTREAM_6H_IN = 1.00
WATCH_ONSITE_6H_IN = 1.00
WATCH_PROBABILITY = 0.20
# An intense radar cell on an intercept course is the same meaning as "upstream rain,
# nothing at the gauge yet" — just earlier, and it works for the dominant W/NW approach
# where the upstream gauges are geometrically behind the house (sources/radar_cells.py).
# The source already filters to >= 40 dBZ inbound cells; this only gates on how soon.
WATCH_RADAR_ETA_MIN = 45.0
# A cell this intense, or this close, alerts on the first qualifying scan — waiting on
# confirmation costs lead time on exactly the storms that matter most (see
# sources/radar_cells.py's module docstring on the W/NW approach gap).
WATCH_RADAR_SEVERE_DBZ = 50.0
WATCH_RADAR_IMMINENT_ETA_MIN = 20.0
# A marginal cell (below WATCH_RADAR_SEVERE_DBZ, further out than the imminent horizon)
# needs this many consecutive confirming scans before it counts — kills the flapping a
# single noisy SCIT vector estimate causes on a cell that isn't urgent enough to act on
# immediately anyway.
WATCH_RADAR_CONFIRM_SCANS = 2.0

# --- Tier 3 Warning: creek responding (gauge required) ---
WARNING_STAGE_FT = 2.0             # bank top is +3 ft (spec §2)
WARNING_RATE_OF_RISE_IN_MIN = 0.05
WARNING_PROBABILITY = 0.50
# Rate of rise is a *difference* between two stage samples, so unlike stage it can be
# manufactured by the radio link rather than by the creek: the first reading after the node
# reconnects differs from the last one before it went quiet by however much the water moved
# in between. features.py already refuses to compute a rate across such a gap; this is the
# second half of the same guard — after any dropout the rate must survive this many
# consecutive gap-free samples before it alone earns a Warning. The counter saturates
# (features.RATE_OF_RISE_SAMPLE_CAP), so on a link that has been up this costs nothing:
# it only delays the first minutes after a reconnect, which is exactly when the number is
# least trustworthy. Stage is not gated — an absolute depth needs no history to be true.
WARNING_RATE_OF_RISE_CONFIRM_SAMPLES = 2

# --- Tier 4 Emergency: overbank imminent (gauge required) ---
EMERGENCY_STAGE_FT = 2.5           # bank top minus a 6 in margin
EMERGENCY_PROBABILITY = 0.80

# --- NWS product force-promotion floors (spec §6) ---
# §6 states only "NWS Flood Warning for the county force-promotes to >= Tier 1", which in
# that section's original 0-3 numbering is Watch — level 2 here. The Watch and Flash Flood
# floors are extensions beyond the letter of the spec: a Flood Watch is by definition the
# forecast-risk situation Advisory describes, and a Flash Flood Warning in a basin this
# flashy (§1: rainfall-to-crest measured in tens of minutes) is materially more urgent
# than an areal flood warning, so it floors at Warning.
NWS_WATCH_FLOOR = 1                # Advisory
NWS_WARNING_FLOOR = 2              # Watch — the rule §6 mandates
NWS_FLASH_WARNING_FLOOR = 3        # Warning


def _ge(value: float | None, threshold: float) -> bool:
    """True only when we actually have a reading — a missing feature never fires a tier."""
    return value is not None and value >= threshold


def compute_tier(
    row: FeatureRow,
    flood_probability: float | None,
    ror_confirm_samples: int | None = None,
) -> tuple[int, str, list[str]]:
    """Highest tier whose conditions are met, with the reasons that got it there."""
    p = flood_probability or 0.0
    if ror_confirm_samples is None:
        ror_confirm_samples = WARNING_RATE_OF_RISE_CONFIRM_SAMPLES
    reasons: list[tuple[int, str]] = []

    # --- Tier 1 Advisory (forecast only; no gauge needed) ---
    if _ge(row.qpf_24h_in, ADVISORY_QPF_24H_IN) and _ge(row.soil_moisture_mean_pct, ADVISORY_SOIL_PCT):
        reasons.append((1, f"{row.qpf_24h_in:.2f}\" forecast in 24 h onto "
                           f"{row.soil_moisture_mean_pct:.0f}% soil moisture"))
    if _ge(row.qpf_24h_in, ADVISORY_QPF_24H_IN) and _ge(row.api_index_in, ADVISORY_API_INDEX_IN):
        reasons.append((1, f"{row.qpf_24h_in:.2f}\" forecast in 24 h onto a wet basin "
                           f"(API {row.api_index_in:.2f}\")"))
    if _ge(row.qpf_6h_in, ADVISORY_QPF_6H_IN):
        reasons.append((1, f"{row.qpf_6h_in:.2f}\" forecast in the next 6 h"))
    if row.ponding_flag:
        reasons.append((1, "low-lying ground already ponding"))

    # --- WPC Excessive Rainfall Outlook (spec Addendum C 2h) ---
    # The only input that grades rainfall against what the ground can absorb, and the
    # only one that exists before anything is on radar. Day 1 only: days 2-3 are carried
    # as features for the model but are too far out to justify an operator-facing tier.
    ero = row.wpc_ero_day1_risk
    if _ge(ero, ADVISORY_ERO_ALONE):
        reasons.append((1, f"WPC {ERO_LABELS.get(ero, ero)} risk of excessive rainfall today"))
    elif _ge(ero, ADVISORY_ERO_WITH_WET_GROUND) and (
        _ge(row.soil_moisture_mean_pct, ADVISORY_SOIL_PCT)
        or _ge(row.api_index_in, ADVISORY_API_INDEX_IN)
    ):
        reasons.append((1, f"WPC {ERO_LABELS.get(ero, ero)} risk of excessive rainfall "
                           f"onto already-wet ground"))

    # --- Rain-on-snow (spec §1: a major regional flood driver) ---
    # The pack contributes meltwater on top of the rain, so the same QPF produces more
    # runoff. Escalates a step: advisory on its own, watch once the rain is falling.
    if row.rain_on_snow_flag:
        swe = row.snow_water_equivalent_in or 0.0
        if _ge(row.rain_1h_in, ROS_WATCH_RAIN_1H_IN):
            reasons.append((2, f"rain on {swe:.2f}\" snowpack — melt adds to runoff"))
        else:
            reasons.append((1, f"rain forecast onto {swe:.2f}\" snowpack"))

    # --- Tier 2 Watch (rain on the ground upstream; still no gauge needed) ---
    if _ge(row.upstream_rain_3h_in, WATCH_UPSTREAM_3H_IN):
        reasons.append((2, f"{row.upstream_rain_3h_in:.2f}\" upstream rain in 3 h"))
    if _ge(row.upstream_rain_6h_in, WATCH_UPSTREAM_6H_IN):
        reasons.append((2, f"{row.upstream_rain_6h_in:.2f}\" upstream rain in 6 h"))
    if _ge(row.rain_6h_in, WATCH_ONSITE_6H_IN):
        reasons.append((2, f"{row.rain_6h_in:.2f}\" on-site rain in 6 h"))
    # Note the inverted comparison: a *smaller* ETA is the worse condition, so _ge does
    # not apply — and a missing ETA (None = no inbound cell) must still never fire.
    if row.radar_threat_eta_min is not None and row.radar_threat_eta_min <= WATCH_RADAR_ETA_MIN:
        severe = _ge(row.radar_threat_max_dbz, WATCH_RADAR_SEVERE_DBZ)
        imminent = row.radar_threat_eta_min <= WATCH_RADAR_IMMINENT_ETA_MIN
        confirmed = _ge(row.radar_threat_scan_count, WATCH_RADAR_CONFIRM_SCANS)
        primed = (_ge(row.soil_moisture_mean_pct, ADVISORY_SOIL_PCT)
                  or _ge(row.api_index_in, ADVISORY_API_INDEX_IN))
        if severe or imminent or (confirmed and primed):
            count = int(row.radar_threat_cells or 1)
            dbz = f" ({row.radar_threat_max_dbz:.0f} dBZ)" if row.radar_threat_max_dbz else ""
            reasons.append((2, f"{count} radar cell(s) inbound{dbz}, "
                               f"~{row.radar_threat_eta_min:.0f} min out"))
    if p >= WATCH_PROBABILITY:
        reasons.append((2, f"model probability {p * 100:.0f}%"))

    # --- Tier 3 Warning (needs the creek node) ---
    if _ge(row.stage_ft, WARNING_STAGE_FT):
        reasons.append((3, f"stage {row.stage_ft:.2f} ft"))
    if _ge(row.rate_of_rise_in_min, WARNING_RATE_OF_RISE_IN_MIN):
        # A None count means the row predates the guard (an old dataset row, or a caller
        # that builds a row by hand) — no information is not evidence of a dropout, so it
        # is trusted, exactly as it was before.
        count = row.rate_of_rise_sample_count
        if count is None or count >= ror_confirm_samples:
            reasons.append((3, f"rising {row.rate_of_rise_in_min:.3f} in/min"))
        else:
            log.info(
                "rate of rise %.3f in/min held below Warning: only %d gap-free sample(s) "
                "since the creek node last reconnected (need %d)",
                row.rate_of_rise_in_min, int(count), ror_confirm_samples,
            )
    if p >= WARNING_PROBABILITY:
        reasons.append((3, f"model probability {p * 100:.0f}%"))

    # --- Tier 4 Emergency (needs the creek node) ---
    if _ge(row.stage_ft, EMERGENCY_STAGE_FT):
        reasons.append((4, f"stage {row.stage_ft:.2f} ft — near bank top"))
    if p >= EMERGENCY_PROBABILITY:
        reasons.append((4, f"model probability {p * 100:.0f}%"))

    # --- NWS force-promotion (spec §6) ---
    # These are floors, not additions: a forecaster issuing a product knows things our
    # instruments do not, so an active warning sets a minimum tier no matter how quiet
    # our own features look. They never *lower* a tier our sensors have already earned.
    for active, floor, text in (
        (row.nws_flood_watch, NWS_WATCH_FLOOR, "NWS Flood Watch in effect"),
        (row.nws_flood_warning, NWS_WARNING_FLOOR, "NWS Flood Warning in effect"),
        (row.nws_flash_flood_warning, NWS_FLASH_WARNING_FLOOR,
         "NWS Flash Flood Warning in effect"),
    ):
        if active:
            reasons.append((floor, text))

    if not reasons:
        return 0, TIER_LABELS[0], []

    tier = max(t for t, _ in reasons)
    # Only explain the tier that was actually reached; lower-tier noise is not useful.
    return tier, TIER_LABELS[tier], [text for t, text in reasons if t == tier]
