"""Configuration loader.

Scalar options + secrets arrive as environment variables from `run.sh`.
List/complex options are read straight from the Supervisor-written
`/data/options.json` so we don't have to marshal arrays through the shell.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
_OPTIONS_JSON = DATA_DIR / "options.json"

# Add-on-private storage is /data; /share is the cross-add-on volume (also exported by
# Samba). Only the storm event log lives there — see the comment in run.sh. Unset outside
# Supervisor (local dev), in which case the storm log falls back to DATA_DIR.
SHARE_DIR = Path(os.environ["SHARE_DIR"]) if os.environ.get("SHARE_DIR") else None

# HA log levels -> Python logging; keep bashio's vocabulary usable here.
_LEVELS = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "notice": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "fatal": logging.CRITICAL,
}


def _optional(value: str | None) -> str:
    """Normalize an optional add-on option that arrived via `bashio::config`.

    bashio renders an option the operator never filled in (`str?`, `password?`) as the
    literal string "null" rather than as an empty string. Left alone that is a truthy
    value, so an unset option reads as configured, enables its source, and then fails
    every poll against a bogus key or ID — with nothing in the log saying the option is
    the problem.

    `nwm_reach_id` has always been guarded against this at its use site in
    `sources/__init__.py`. Normalizing here instead means a newly added optional cannot
    miss the guard by not knowing about it, which is exactly how 0.21.0 shipped the
    Google Floods source enabled with the API key "null".
    """
    text = (value or "").strip()
    return "" if text == "null" else text


def _num(env, name: str, default: float) -> float:
    """A numeric option from the environment, falling back to `default` when bashio hands
    back nothing usable. An option added in a newer version is absent from an older
    install's options.json until Supervisor merges defaults in, and bashio renders an
    absent option as "null" — float("null") would take the service down at startup over an
    option the operator never touched."""
    raw = (env.get(name) or "").strip()
    if raw in ("", "null"):
        return default
    try:
        return float(raw)
    except ValueError:
        logging.getLogger("app.config").warning(
            "option %s=%r is not a number; using %s", name, raw, default)
        return default


def _options() -> dict:
    try:
        return json.loads(_OPTIONS_JSON.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # Running outside Supervisor (local dev) — fall back to env-only.
        return {}


@dataclass
class Config:
    log_level: str = "info"
    fast_loop_minutes: int = 5
    nightly_retrain_hour: int = 3
    mqtt_base_topic: str = "creek"
    publish_prefix: str = "creek"
    min_events_for_ml: int = 10

    # Storm detection (app/storms.py). Tunable because they define what counts as one
    # storm, and the event log is the input to the lag analysis — a quiet window that is
    # too long merges an afternoon's storms into the morning's, too short splits one in
    # two, and either way the lag it teaches is wrong.
    storm_start_rain_1h_in: float = 0.10
    storm_continue_rain_1h_in: float = 0.02
    storm_quiet_hours: float = 6.0

    # Secrets (env only; never logged)
    google_floods_api_key: str = ""
    wu_api_key: str = ""
    nwm_reach_id: str = ""

    # Creek-gauge link guards (app/features.py). The creek node reports every 60 s and the
    # gateway calls it offline after 5 missed reports; these decide how the add-on treats a
    # stage reading that is older than that, and how long a gap can be before the level
    # change across it is no longer attributable to one interval.
    stage_max_age_minutes: float = 6.0
    rate_of_rise_max_gap_minutes: float = 10.0
    rate_of_rise_confirm_samples: int = 2
    # Rate of rise is measured against a reading at least this old, not the previous one.
    # The node reports at 1 mm resolution and a still creek flickers 1-2 mm between reports;
    # two readings one report (~63 s) apart turn a 2 mm flicker into 0.075 in/min, over the
    # 0.05 in/min Warning. Over 10 min the same flicker is 0.008 in/min, while a real
    # Warning-rate rise is 0.5 in — still unmistakable. See FeatureBuilder._rate_of_rise.
    rate_of_rise_window_minutes: float = 10.0
    # Fastest rise the creek can physically make, for rejecting impossible jumps. A radar
    # that returns 0 is clamped by the gateway to the range ceiling (37.6 in), so a still
    # creek at 11 in "rises" 26 in in one reading — that was a Tier 4 Emergency on a dry
    # 2026-09-17. See FeatureBuilder._plausible_stage.
    max_stage_rise_in_min: float = 2.0

    # HA input entities
    stage_entity: str = "sensor.creek_gateway_stage"
    # Connectivity binary sensor published by the RFM69 gateway. Blank disables the check,
    # leaving only the age fallback in FeatureBuilder._link_usable.
    creek_node_status_entity: str | None = "binary_sensor.creek_gateway_creek_node_status"
    # The gateway's packet counter: the one value that must change on every report. The
    # status sensor above only writes on transitions, so a wedged gateway leaves it frozen
    # at `on` — this is what tells the add-on the link is really alive. Blank disables it.
    creek_node_packets_entity: str | None = "sensor.outside_creek_gateway_creek_node_packets"
    soil_moisture_entities: list[str] = field(default_factory=list)
    onsite_rain_rate_entity: str | None = None
    # Monotonic rain counter (the Ecowitt's "rain total"). When set, on-site rain is the
    # difference between successive readings — exact — instead of the instantaneous rate
    # integrated at the fast-loop cadence, which read 5-10 % low against the station's own
    # counter in steady rain and does worse in bursts. The rate entity stays the fallback.
    onsite_rain_total_entity: str | None = None
    onsite_rain_daily_entity: str | None = None
    # Whether a promoted ML model's probability may raise alert tiers. Off by default: a
    # promoted model then runs in shadow — computed, published and recorded, but the tiers
    # use the threshold estimate — until its storms-on-record justify handing it the alarm.
    ml_drives_alerts: bool = False
    upstream_pws_ids: list[str] = field(default_factory=list)
    usgs_downstream: bool = True
    snodas_swe: bool = True
    nexrad_cells: bool = True
    # Site-specific, so not committed: set it in the add-on's configuration. Blank disables
    # NEXRAD cell tracking rather than guessing a radar, since the wrong one silently reports
    # storms that are nowhere near the site.
    nexrad_radar_id: str = ""
    wpc_ero: bool = True
    onsite_temp_entity: str | None = None

    # MQTT (from service discovery via run.sh)
    mqtt_host: str = "core-mosquitto"
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_pass: str = ""

    # HA Core API via Supervisor proxy
    ha_api_url: str = "http://supervisor/core/api"
    supervisor_token: str = ""

    @classmethod
    def load(cls) -> "Config":
        env = os.environ
        opts = _options()
        return cls(
            log_level=env.get("LOG_LEVEL", "info"),
            fast_loop_minutes=int(env.get("FAST_LOOP_MINUTES", 5)),
            nightly_retrain_hour=int(env.get("NIGHTLY_RETRAIN_HOUR", 3)),
            mqtt_base_topic=env.get("MQTT_BASE_TOPIC", "creek"),
            publish_prefix=env.get("PUBLISH_PREFIX", "creek"),
            min_events_for_ml=int(env.get("MIN_EVENTS_FOR_ML", 10)),
            storm_start_rain_1h_in=float(env.get("STORM_START_RAIN_1H_IN", 0.10)),
            storm_continue_rain_1h_in=float(env.get("STORM_CONTINUE_RAIN_1H_IN", 0.02)),
            storm_quiet_hours=float(env.get("STORM_QUIET_HOURS", 6.0)),
            stage_max_age_minutes=float(env.get("STAGE_MAX_AGE_MINUTES", 6.0)),
            rate_of_rise_max_gap_minutes=float(
                env.get("RATE_OF_RISE_MAX_GAP_MINUTES", 10.0)),
            rate_of_rise_confirm_samples=int(env.get("RATE_OF_RISE_CONFIRM_SAMPLES", 2)),
            rate_of_rise_window_minutes=_num(env, "RATE_OF_RISE_WINDOW_MINUTES", 10.0),
            max_stage_rise_in_min=_num(env, "MAX_STAGE_RISE_IN_MIN", 2.0),
            google_floods_api_key=_optional(env.get("GOOGLE_FLOODS_API_KEY")),
            wu_api_key=_optional(env.get("WU_API_KEY")),
            nwm_reach_id=_optional(env.get("NWM_REACH_ID")),
            stage_entity=opts.get("stage_entity", "sensor.creek_gateway_stage"),
            creek_node_status_entity=opts.get(
                "creek_node_status_entity",
                "binary_sensor.creek_gateway_creek_node_status") or None,
            creek_node_packets_entity=opts.get(
                "creek_node_packets_entity",
                "sensor.outside_creek_gateway_creek_node_packets") or None,
            soil_moisture_entities=list(opts.get("soil_moisture_entities", [])),
            onsite_rain_rate_entity=opts.get("onsite_rain_rate_entity") or None,
            onsite_rain_total_entity=opts.get("onsite_rain_total_entity") or None,
            ml_drives_alerts=bool(opts.get("ml_drives_alerts", False)),
            onsite_rain_daily_entity=opts.get("onsite_rain_daily_entity") or None,
            upstream_pws_ids=list(opts.get("upstream_pws_ids", [])),
            usgs_downstream=bool(opts.get("usgs_downstream", True)),
            snodas_swe=bool(opts.get("snodas_swe", True)),
            nexrad_cells=bool(opts.get("nexrad_cells", True)),
            nexrad_radar_id=(opts.get("nexrad_radar_id") or "").strip(),
            wpc_ero=bool(opts.get("wpc_ero", True)),
            onsite_temp_entity=opts.get("onsite_temp_entity") or None,
            mqtt_host=env.get("MQTT_HOST", "core-mosquitto"),
            mqtt_port=int(env.get("MQTT_PORT", 1883)),
            mqtt_user=env.get("MQTT_USER", ""),
            mqtt_pass=env.get("MQTT_PASS", ""),
            ha_api_url=env.get("HA_API_URL", "http://supervisor/core/api"),
            supervisor_token=env.get("SUPERVISOR_TOKEN", ""),
        )

    @property
    def py_log_level(self) -> int:
        return _LEVELS.get(self.log_level, logging.INFO)
