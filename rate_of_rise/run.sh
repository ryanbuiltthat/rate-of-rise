#!/usr/bin/with-contenv bashio
set -euo pipefail

# --- Scalar options -> environment ---
export LOG_LEVEL="$(bashio::config 'log_level')"
export FAST_LOOP_MINUTES="$(bashio::config 'fast_loop_minutes')"
export NIGHTLY_RETRAIN_HOUR="$(bashio::config 'nightly_retrain_hour')"
export MQTT_BASE_TOPIC="$(bashio::config 'mqtt_base_topic')"
export PUBLISH_PREFIX="$(bashio::config 'publish_prefix')"
export MIN_EVENTS_FOR_ML="$(bashio::config 'min_events_for_ml')"
export STORM_START_RAIN_1H_IN="$(bashio::config 'storm_start_rain_1h_in')"
export STORM_CONTINUE_RAIN_1H_IN="$(bashio::config 'storm_continue_rain_1h_in')"
export STORM_QUIET_HOURS="$(bashio::config 'storm_quiet_hours')"
export STAGE_MAX_AGE_MINUTES="$(bashio::config 'stage_max_age_minutes')"
export RATE_OF_RISE_MAX_GAP_MINUTES="$(bashio::config 'rate_of_rise_max_gap_minutes')"
export RATE_OF_RISE_CONFIRM_SAMPLES="$(bashio::config 'rate_of_rise_confirm_samples')"
export GOOGLE_FLOODS_API_KEY="$(bashio::config 'google_floods_api_key')"
export WU_API_KEY="$(bashio::config 'wu_api_key')"
export NWM_REACH_ID="$(bashio::config 'nwm_reach_id')"
# List/complex options (soil_moisture_entities, upstream_pws_ids, ...) are read by the
# app directly from /data/options.json — no need to marshal arrays through env.

# --- HA Core API via the Supervisor proxy (no long-lived token needed) ---
export HA_API_URL="http://supervisor/core/api"
export SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN}"   # injected by Supervisor

# --- MQTT from the Mosquitto add-on via service discovery ---
if bashio::services.available "mqtt"; then
  export MQTT_HOST="$(bashio::services 'mqtt' 'host')"
  export MQTT_PORT="$(bashio::services 'mqtt' 'port')"
  export MQTT_USER="$(bashio::services 'mqtt' 'username')"
  export MQTT_PASS="$(bashio::services 'mqtt' 'password')"
else
  bashio::exit.nok "No MQTT service available — install/configure the Mosquitto add-on."
fi

# --- Persistent storage (survives add-on updates/restarts) ---
export DATA_DIR="/data"          # dataset.parquet, model registry, accumulator state
mkdir -p "${DATA_DIR}/models" "${DATA_DIR}/datasets" "${DATA_DIR}/state"

# The storm event log lives in /share instead of /data, because it is the one file a
# human is expected to edit by hand. Each add-on gets its own /data, so `sqlite3
# /data/events.sqlite` typed in the SSH/Terminal add-on silently opens an empty database
# of that add-on's own — a trap that could hide months of un-annotated storms. /share is
# mounted by every add-on and exported over Samba, so one path works from anywhere.
export SHARE_DIR="/share"
mkdir -p "${SHARE_DIR}/rate_of_rise" || bashio::log.warning \
  "Could not create ${SHARE_DIR}/rate_of_rise — storm log stays in ${DATA_DIR}."

bashio::log.info "Starting Rate of Rise (fast loop ${FAST_LOOP_MINUTES}m)…"
exec python3 -m app
