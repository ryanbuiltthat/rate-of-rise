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

    # HA input entities
    stage_entity: str = "sensor.creek_gateway_stage"
    # Connectivity binary sensor published by the RFM69 gateway. Blank disables the check,
    # leaving only the age fallback in FeatureBuilder._link_usable.
    creek_node_status_entity: str | None = "binary_sensor.creek_gateway_creek_node_status"
    soil_moisture_entities: list[str] = field(default_factory=list)
    onsite_rain_rate_entity: str | None = None
    onsite_rain_daily_entity: str | None = None
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
            google_floods_api_key=env.get("GOOGLE_FLOODS_API_KEY", ""),
            wu_api_key=env.get("WU_API_KEY", ""),
            nwm_reach_id=env.get("NWM_REACH_ID", ""),
            stage_entity=opts.get("stage_entity", "sensor.creek_gateway_stage"),
            creek_node_status_entity=opts.get(
                "creek_node_status_entity",
                "binary_sensor.creek_gateway_creek_node_status") or None,
            soil_moisture_entities=list(opts.get("soil_moisture_entities", [])),
            onsite_rain_rate_entity=opts.get("onsite_rain_rate_entity") or None,
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
