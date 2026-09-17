"""MQTT Discovery — auto-provision the add-on's HA entities.

Publishing retained config messages under
`homeassistant/<component>/rate_of_rise/<slug>/config` makes Home Assistant create
(and update) the `creek_*` sensors and buttons automatically — no HA package or
`configuration.yaml` edit for them, and they refresh whenever the add-on updates.

ENTITY IDs: Home Assistant mints them from the device name plus the entity **name** —
NOT from the `object_id` published below, which is only a suggestion and is not honoured.
So "Creek Flood Probability" on the "Rate of Rise" device becomes
`sensor.rate_of_rise_creek_flood_probability`. Most entities here hide the
distinction because their name slugifies to exactly their object_id; where the two differ,
the name wins. Use `entity_ids()` rather than assuming, and the dashboard is checked
against it in `tests/test_dashboard_entities.py`.

Only entities defined here get the device prefix. The HA-side template sensor in
`ha-packages/creek_warning.yaml` and the RFM69 gateway keep their own IDs.

All entities share one `device` and an `availability_topic` driven by the MQTT LWT, so
they show *unavailable* when the add-on is stopped.
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger("app.discovery")


def _slugify(text: str) -> str:
    """Mirror Home Assistant's entity_id slugification."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.lower())).strip("_")

DISCOVERY_PREFIX = "homeassistant"
NODE_ID = "rate_of_rise"


class DiscoveryPublisher:
    def __init__(self, publish_raw, base_topic: str):
        """`publish_raw(topic, payload_str, retain)` sends a raw string; `base_topic`
        is the add-on's MQTT base (default `creek`)."""
        self._publish_raw = publish_raw
        self._base = base_topic.rstrip("/")

    def _device(self) -> dict:
        return {
            "identifiers": ["rate_of_rise"],
            "name": "Rate of Rise",
            "manufacturer": "ryanbuiltthat",
            "model": "Flood modeling add-on",
        }

    def _availability(self) -> dict:
        return {
            "availability_topic": f"{self._base}/status/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
        }

    def _specs(self) -> list[tuple[str, str, dict]]:
        """(component, slug, partial-config). State/command topics are relative to base."""
        b = self._base
        return [
            # --- model outputs ---
            ("sensor", "creek_flood_probability", {
                "name": "Creek Flood Probability",
                "state_topic": f"{b}/flood_probability",
                "value_template": "{{ (value_json.value | float(0) * 100) | round(0) }}",
                "unit_of_measurement": "%", "state_class": "measurement",
                "json_attributes_topic": f"{b}/flood_probability", "icon": "mdi:water-alert"}),
            ("sensor", "creek_model_method", {
                "name": "Creek Model Method",
                "state_topic": f"{b}/flood_probability",
                "value_template": "{{ value_json.method }}", "icon": "mdi:function-variant"}),
            ("sensor", "creek_predicted_crest", {
                "name": "Creek Predicted Crest",
                "state_topic": f"{b}/predicted_crest",
                "value_template": "{{ value_json.value if value_json.value is not none else 'unknown' }}",
                "unit_of_measurement": "ft", "state_class": "measurement", "icon": "mdi:wave"}),
            ("sensor", "creek_lag_estimate", {
                "name": "Creek Lag Estimate",
                "state_topic": f"{b}/lag_estimate",
                "value_template": "{{ value_json.value if value_json.value is not none else 'unknown' }}",
                "json_attributes_topic": f"{b}/lag_estimate",
                "unit_of_measurement": "min", "state_class": "measurement", "icon": "mdi:timer-sand"}),
            ("sensor", "creek_alert_tier", {
                "name": "Creek Alert Tier",
                "state_topic": f"{b}/alert_tier",
                "value_template": "{{ value_json.value }}",
                "json_attributes_topic": f"{b}/alert_tier", "icon": "mdi:alert-decagram"}),
            ("sensor", "creek_tier_reason", {
                "name": "Creek Tier Reason",
                "state_topic": f"{b}/alert_tier",
                "value_template": "{{ value_json.why }}",
                "icon": "mdi:comment-question-outline"}),
            # --- pipeline status ---
            ("sensor", "creek_pipeline_state", {
                "name": "Creek Pipeline State",
                "state_topic": f"{b}/status/pipeline",
                "value_template": "{{ value_json.state }}",
                "json_attributes_topic": f"{b}/status/pipeline", "icon": "mdi:cog-play"}),
            ("sensor", "creek_last_inference", {
                "name": "Creek Last Inference",
                "state_topic": f"{b}/status/pipeline", "device_class": "timestamp",
                "value_template": "{{ value_json.last_inference_at if value_json.last_inference_at is not none else none }}",
                "icon": "mdi:clock-check-outline"}),
            ("sensor", "creek_last_nightly", {
                "name": "Creek Last Nightly Run",
                "state_topic": f"{b}/status/pipeline", "device_class": "timestamp",
                "value_template": "{{ value_json.last_nightly_at if value_json.last_nightly_at is not none else none }}",
                "icon": "mdi:weather-night"}),
            ("sensor", "creek_last_command", {
                "name": "Creek Last Command",
                "state_topic": f"{b}/status/command_result",
                "value_template": "{{ value_json.command }} ({{ 'ok' if value_json.ok else 'failed' }})",
                "json_attributes_topic": f"{b}/status/command_result", "icon": "mdi:console"}),
            # --- model health / registry ---
            ("sensor", "creek_model_health", {
                "name": "Creek Model Health",
                "state_topic": f"{b}/model_health",
                "value_template": "{{ value_json.active_method }}",
                "json_attributes_topic": f"{b}/model_health", "icon": "mdi:heart-pulse"}),
            ("sensor", "creek_dataset_rows", {
                "name": "Creek Dataset Rows",
                "state_topic": f"{b}/model_health",
                "value_template": "{{ value_json.dataset_rows }}",
                "state_class": "measurement", "icon": "mdi:table"}),
            ("sensor", "creek_event_count", {
                "name": "Creek Event Count",
                "state_topic": f"{b}/status/storms",
                "value_template": "{{ value_json.event_count }}",
                "json_attributes_topic": f"{b}/status/storms",
                "state_class": "measurement", "icon": "mdi:weather-lightning-rainy"}),
            ("binary_sensor", "creek_storm_in_progress", {
                "name": "Creek Storm In Progress",
                "state_topic": f"{b}/status/storms",
                "value_template": "{{ 'ON' if value_json.open else 'OFF' }}",
                "payload_on": "ON", "payload_off": "OFF",
                "device_class": "moisture", "icon": "mdi:weather-pouring"}),
            # --- storm annotation from the Operator tab ---
            # Shows *which* storm the text box below will annotate before anyone types
            # anything: not necessarily "the storm I just watched" if a second one has
            # already opened (storms.latest_closed — see its docstring). Full record
            # (peaks, onset conditions, existing notes) rides along as attributes.
            ("sensor", "creek_storm_to_annotate", {
                "name": "Creek Storm To Annotate",
                "state_topic": f"{b}/status/storms",
                "value_template": ("{{ ('Storm #' ~ value_json.latest_closed.id) "
                                  "if value_json.latest_closed is not none else 'none yet' }}"),
                "json_attributes_topic": f"{b}/status/storms", "icon": "mdi:clipboard-text-clock-outline"}),
            ("text", "creek_annotate_latest_storm", {
                "name": "Creek Annotate Latest Storm",
                "command_topic": f"{b}/cmd/annotate",
                # 255 is the platform's own hard ceiling for MQTT text entities, not a
                # choice — HA's `text` component clamps native_max_value to (0, 255)
                # regardless of what discovery asks for, and MQTT discovery validates
                # against that schema *before* creating the entity: a `max` above 255
                # does not get clamped, the whole discovery payload for that entity is
                # rejected and nothing is created — silently, with no error the add-on
                # itself can see. This entity did not exist in Home Assistant at all
                # until this was caught (0.12.1 shipped with 500).
                "mode": "text", "max": 255, "icon": "mdi:note-edit-outline"}),
            ("sensor", "creek_lag_response_series", {
                "name": "Creek Lag Response Series",
                "state_topic": f"{b}/status/lag",
                "value_template": "{{ value_json.response if value_json.response is not none else 'none' }}",
                "json_attributes_topic": f"{b}/status/lag", "icon": "mdi:chart-timeline-variant"}),
            ("sensor", "creek_active_model", {
                "name": "Creek Active Model",
                "state_topic": f"{b}/status/registry",
                "value_template": "{{ value_json.active_version if value_json.active_version is not none else 'none' }}",
                "json_attributes_topic": f"{b}/status/registry", "icon": "mdi:cube-outline"}),
            ("sensor", "creek_candidate_model", {
                "name": "Creek Candidate Model",
                "state_topic": f"{b}/status/registry",
                "value_template": "{{ value_json.candidate_version if value_json.candidate_version is not none else 'none' }}",
                "icon": "mdi:cube-scan"}),
            # --- local creek gauge (SEN0676 via Moteino -> RFM69 gateway) ---
            # Stage itself is already published by the gateway as `sensor.creek_gateway_stage`
            # (firmware/esp32_rfm69_gateway/gateway.base.yaml), so it is not repeated here.
            # rate_of_rise_in_min, though, only ever existed inside the add-on process
            # (FeatureBuilder._rate_of_rise, consumed by tiers.py/model.py) and was never
            # published anywhere HA could see it — spec §1/§4 name it as a primary feature
            # alongside stage, but no entity for it ever existed.
            ("sensor", "creek_rate_of_rise", {
                "name": "Creek Rate Of Rise",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.rate_of_rise_in_min if value_json.rate_of_rise_in_min is not none else none }}",
                "unit_of_measurement": "in/min", "state_class": "measurement",
                "icon": "mdi:trending-up"}),
            # How old the stage reading behind that rate is. Blank rate + a climbing age is
            # the signature of a dropped radio link, and it is the difference between "the
            # creek is not rising" and "nobody is watching the creek" — which the stage
            # entity alone cannot show, because the gateway keeps serving the last value the
            # node sent (see FeatureBuilder._rate_of_rise).
            ("sensor", "creek_stage_age", {
                "name": "Creek Stage Age",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.stage_age_min if value_json.stage_age_min is not none else none }}",
                "unit_of_measurement": "min", "state_class": "measurement",
                "entity_category": "diagnostic", "icon": "mdi:clock-alert-outline"}),
            # --- ingested features (Addendum C 2a): rain accumulations + NWS QPF ---
            *(
                ("sensor", f"creek_rain_{w}h", {
                    "name": f"Creek Rain {w}h",
                    "state_topic": f"{b}/features",
                    "value_template": f"{{{{ value_json.rain_{w}h_in if value_json.rain_{w}h_in is not none else none }}}}",
                    "unit_of_measurement": "in", "device_class": "precipitation",
                    "state_class": "measurement", "icon": "mdi:weather-pouring"})
                for w in (1, 3, 6, 24, 72)
            ),
            ("sensor", "creek_api_index", {
                "name": "Creek Antecedent Precipitation Index",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.api_index_in if value_json.api_index_in is not none else none }}",
                "unit_of_measurement": "in", "state_class": "measurement",
                "icon": "mdi:water-percent"}),
            ("sensor", "creek_qpf_6h", {
                "name": "Creek QPF 6h",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.qpf_6h_in if value_json.qpf_6h_in is not none else none }}",
                "unit_of_measurement": "in", "device_class": "precipitation",
                "state_class": "measurement", "icon": "mdi:weather-rainy"}),
            ("sensor", "creek_qpf_24h", {
                "name": "Creek QPF 24h",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.qpf_24h_in if value_json.qpf_24h_in is not none else none }}",
                "unit_of_measurement": "in", "device_class": "precipitation",
                "state_class": "measurement", "icon": "mdi:weather-rainy"}),
            # --- ingested features (Addendum C 2b): upstream WU + NWM reach ---
            *(
                ("sensor", f"creek_upstream_rain_{w}h", {
                    "name": f"Creek Upstream Rain {w}h",
                    "state_topic": f"{b}/features",
                    "value_template": f"{{{{ value_json.upstream_rain_{w}h_in if value_json.upstream_rain_{w}h_in is not none else none }}}}",
                    "unit_of_measurement": "in", "device_class": "precipitation",
                    "state_class": "measurement", "icon": "mdi:weather-pouring"})
                for w in (1, 3, 6, 24, 72)
            ),
            ("sensor", "creek_upstream_precip_today", {
                "name": "Creek Upstream Precip Today",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.upstream_precip_today_in if value_json.upstream_precip_today_in is not none else none }}",
                "unit_of_measurement": "in", "device_class": "precipitation",
                "state_class": "measurement", "icon": "mdi:weather-pouring"}),
            ("sensor", "creek_nwm_flow", {
                "name": "Creek NWM Flow",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.nwm_flow_cfs if value_json.nwm_flow_cfs is not none else none }}",
                "unit_of_measurement": "ft³/s", "state_class": "measurement",
                "icon": "mdi:waves-arrow-right"}),
            ("sensor", "creek_nwm_flow_peak", {
                "name": "Creek NWM Flow Peak",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.nwm_flow_max_cfs if value_json.nwm_flow_max_cfs is not none else none }}",
                "unit_of_measurement": "ft³/s", "state_class": "measurement",
                "icon": "mdi:waves-arrow-up"}),
            # --- ingested features (Addendum C 2c): USGS downstream gauges ---
            *(
                spec
                # `label` is the dataset/feature key and is frozen — it names columns in the
                # stored Parquet and every feature list in train.py, so renaming it would
                # strand the historical dataset. `pretty` only shapes the HA entity name.
                for site, label, pretty in (
                    ("01534860", "leggetts", "Adjacent"),
                    ("01534000", "tunkhannock", "Downstream"),
                )
                for spec in (
                    ("sensor", f"creek_usgs_{label}_gage", {
                        "name": f"Creek USGS {pretty} Gage Height",
                        "state_topic": f"{b}/features",
                        "value_template": (f"{{{{ value_json.usgs_{label}_gage_ft "
                                           f"if value_json.usgs_{label}_gage_ft is not none else none }}}}"),
                        "unit_of_measurement": "ft", "state_class": "measurement",
                        "icon": "mdi:altimeter"}),
                    ("sensor", f"creek_usgs_{label}_flow", {
                        "name": f"Creek USGS {pretty} Flow",
                        "state_topic": f"{b}/features",
                        "value_template": (f"{{{{ value_json.usgs_{label}_flow_cfs "
                                           f"if value_json.usgs_{label}_flow_cfs is not none else none }}}}"),
                        "unit_of_measurement": "ft³/s", "state_class": "measurement",
                        "icon": "mdi:waves"}),
                    ("sensor", f"creek_usgs_{label}_rise_3h", {
                        "name": f"Creek USGS {pretty} Rise 3h",
                        "state_topic": f"{b}/features",
                        "value_template": (f"{{{{ value_json.usgs_{label}_rise_3h_ft "
                                           f"if value_json.usgs_{label}_rise_3h_ft is not none else none }}}}"),
                        "unit_of_measurement": "ft", "state_class": "measurement",
                        "icon": "mdi:trending-up"}),
                )
            ),
            # --- ingested features (Addendum C 2d): NWS active alert products ---
            *(
                ("binary_sensor", f"creek_{slug}", {
                    "name": name,
                    "state_topic": f"{b}/features",
                    "value_template": f"{{{{ 'ON' if value_json.{key} else 'OFF' }}}}",
                    "payload_on": "ON", "payload_off": "OFF",
                    "device_class": "safety", "icon": icon})
                for slug, key, name, icon in (
                    ("nws_flood_watch", "nws_flood_watch",
                     "Creek NWS Flood Watch", "mdi:weather-cloudy-alert"),
                    ("nws_flood_warning", "nws_flood_warning",
                     "Creek NWS Flood Warning", "mdi:home-flood"),
                    ("nws_flash_flood_warning", "nws_flash_flood_warning",
                     "Creek NWS Flash Flood Warning", "mdi:flash-alert"),
                )
            ),
            ("sensor", "creek_nws_alert_count", {
                "name": "Creek NWS Alert Count",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.nws_alert_count if value_json.nws_alert_count is not none else none }}",
                "state_class": "measurement", "icon": "mdi:bell-alert-outline"}),
            # --- ingested features (Addendum C 2e): snowpack + rain-on-snow ---
            ("sensor", "creek_snow_water_equivalent", {
                "name": "Creek Snow Water Equivalent",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.snow_water_equivalent_in if value_json.snow_water_equivalent_in is not none else none }}",
                "unit_of_measurement": "in", "device_class": "precipitation",
                "state_class": "measurement", "icon": "mdi:snowflake"}),
            ("sensor", "creek_temperature", {
                "name": "Creek Temperature",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.temp_f if value_json.temp_f is not none else none }}",
                "unit_of_measurement": "°F", "device_class": "temperature",
                "state_class": "measurement", "icon": "mdi:thermometer"}),
            ("binary_sensor", "creek_rain_on_snow", {
                "name": "Creek Rain On Snow",
                "state_topic": f"{b}/features",
                "value_template": "{{ 'ON' if value_json.rain_on_snow_flag else 'OFF' }}",
                "payload_on": "ON", "payload_off": "OFF",
                "device_class": "safety", "icon": "mdi:weather-snowy-rainy"}),
            # --- ingested features (Addendum C 2g): NEXRAD storm-cell tracks ---
            ("sensor", "creek_radar_cells_tracked", {
                "name": "Creek Radar Cells Tracked",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.radar_cells_tracked if value_json.radar_cells_tracked is not none else none }}",
                "state_class": "measurement", "icon": "mdi:radar"}),
            ("sensor", "creek_radar_threat_cells", {
                "name": "Creek Radar Threat Cells",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.radar_threat_cells if value_json.radar_threat_cells is not none else none }}",
                "state_class": "measurement", "icon": "mdi:crosshairs-gps"}),
            ("sensor", "creek_radar_threat_eta", {
                "name": "Creek Radar Threat ETA",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.radar_threat_eta_min if value_json.radar_threat_eta_min is not none else none }}",
                "unit_of_measurement": "min", "icon": "mdi:clock-fast"}),
            ("sensor", "creek_radar_threat_max_dbz", {
                "name": "Creek Radar Threat Max dBZ",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.radar_threat_max_dbz if value_json.radar_threat_max_dbz is not none else none }}",
                "unit_of_measurement": "dBZ", "icon": "mdi:signal"}),
            # --- ingested features (Addendum C 2h): WPC Excessive Rainfall Outlook ---
            # Published as the category name rather than the number: "Slight" is what
            # WPC says and what the forecast discussion will call it, and an operator
            # reading a dashboard at 2 a.m. should not have to remember that 2 = Slight.
            # The numeric value rides along as an attribute for graphing.
            *(
                ("sensor", f"creek_wpc_ero_day{d}", {
                    "name": f"Creek WPC Excessive Rain Risk Day {d}",
                    "state_topic": f"{b}/features",
                    "value_template": (
                        "{% set r = value_json.wpc_ero_day" + str(d) + "_risk %}"
                        "{{ 'unknown' if r is none else"
                        " {0: 'None', 1: 'Marginal', 2: 'Slight',"
                        " 3: 'Moderate', 4: 'High'}.get(r | int, r) }}"),
                    "icon": "mdi:weather-pouring"})
                for d in (1, 2, 3)
            ),
            # --- ingested features (Addendum C 2i): Google Flood Forecasting ---
            # Same reasoning as the ERO entities above: the operator sees Google's own
            # wording ("Severe"), not the ladder position it occupies. The distance is a
            # separate entity because the status is unreadable without it — Google gauges
            # neighbouring rivers, never this creek.
            ("sensor", "creek_google_flood_status", {
                "name": "Creek Google Flood Status",
                "state_topic": f"{b}/features",
                "value_template": (
                    "{% set s = value_json.google_flood_severity %}"
                    "{{ 'unknown' if s is none else"
                    " {0: 'No flooding', 1: 'Above normal', 2: 'Severe',"
                    " 3: 'Extreme'}.get(s | int, s) }}"),
                "icon": "mdi:sign-caution"}),
            ("sensor", "creek_google_flood_trend", {
                "name": "Creek Google Flood Trend",
                "state_topic": f"{b}/features",
                "value_template": (
                    "{% set t = value_json.google_flood_trend %}"
                    "{{ 'unknown' if t is none else"
                    " {-1: 'Falling', 0: 'Steady', 1: 'Rising'}.get(t | int, t) }}"),
                "icon": "mdi:chart-line-variant"}),
            ("sensor", "creek_google_flood_gauge_distance", {
                "name": "Creek Google Flood Gauge Distance",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.google_flood_gauge_mi if value_json.google_flood_gauge_mi is not none else none }}",
                "unit_of_measurement": "mi", "state_class": "measurement",
                "icon": "mdi:map-marker-distance"}),
            ("sensor", "creek_google_flood_gauges", {
                "name": "Creek Google Flood Gauges",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.google_flood_gauges if value_json.google_flood_gauges is not none else none }}",
                "state_class": "measurement", "icon": "mdi:map-marker-multiple"}),
            ("sensor", "creek_google_flash_flood_status", {
                "name": "Creek Google Flash Flood Status",
                "state_topic": f"{b}/features",
                "value_template": (
                    "{% set hl = value_json.google_flash_flood_highly_likely %}"
                    "{% set l = value_json.google_flash_flood_likely %}"
                    "{{ 'unknown' if hl is none and l is none else"
                    " 'Highly likely' if hl == 1 else"
                    " 'Likely' if l == 1 else 'None' }}"),
                "icon": "mdi:weather-pouring"}),
            ("sensor", "creek_google_flash_flood_events", {
                "name": "Creek Google Flash Flood Events",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.google_flash_flood_events if value_json.google_flash_flood_events is not none else none }}",
                "state_class": "measurement", "icon": "mdi:map-marker-alert"}),
            # --- soil moisture (migrated out of the HA package) ---
            ("sensor", "creek_soil_moisture_mean", {
                "name": "Creek Soil Moisture Mean",
                "state_topic": f"{b}/soil",
                "value_template": "{{ value_json.mean_pct if value_json.mean_pct is not none else none }}",
                "unit_of_measurement": "%", "device_class": "moisture",
                "state_class": "measurement"}),
            ("binary_sensor", "creek_soil_ponding", {
                "name": "Creek Soil Ponding",
                "state_topic": f"{b}/soil",
                "value_template": "{{ 'ON' if value_json.ponding else 'OFF' }}",
                "payload_on": "ON", "payload_off": "OFF",
                "json_attributes_topic": f"{b}/soil",
                "device_class": "moisture", "icon": "mdi:water-alert-outline"}),
            # --- sensor-fault watchdogs (migrated out of the HA package) ---
            *(
                ("binary_sensor", f"creek_{key}", {
                    "name": f"Creek {name}",
                    "state_topic": f"{b}/status/health",
                    "value_template": f"{{{{ 'ON' if value_json.{key} else 'OFF' }}}}",
                    "payload_on": "ON", "payload_off": "OFF",
                    "device_class": "problem", "icon": icon})
                for key, name, icon in (
                    ("stage_stale", "Stage Stale", "mdi:water-off-outline"),
                    ("soil_moisture_stale", "Soil Moisture Stale", "mdi:water-off-outline"),
                    ("rain_rate_stale", "Rain Rate Stale", "mdi:weather-cloudy-alert"),
                    ("forecast_data_missing", "Forecast Data Missing", "mdi:cloud-off-outline"),
                    ("nws_alerts_missing", "NWS Alert Feed Missing", "mdi:bell-off-outline"),
                    ("upstream_data_missing", "Upstream Data Missing", "mdi:cloud-off-outline"),
                    ("nwm_data_missing", "NWM Data Missing", "mdi:waves-arrow-right"),
                    ("downstream_gauge_missing", "Downstream Gauge Missing", "mdi:gauge-empty"),
                    ("snowpack_data_missing", "Snowpack Data Missing", "mdi:snowflake-off"),
                    ("radar_cells_missing", "Radar Cells Missing", "mdi:radar"),
                    ("ero_outlook_missing", "WPC Outlook Missing", "mdi:cloud-off-outline"),
                    ("google_flood_status_missing", "Google Flood Status Missing",
                     "mdi:cloud-off-outline"),
                )
            ),
            # --- command buttons ---
            ("button", "creek_run_inference_now", {
                "name": "Creek Run Inference Now",
                "command_topic": f"{b}/cmd/run_inference", "payload_press": "run", "icon": "mdi:play"}),
            ("button", "creek_retrain_now", {
                "name": "Creek Retrain Now",
                "command_topic": f"{b}/cmd/retrain", "payload_press": "run", "icon": "mdi:brain"}),
            ("button", "creek_promote_model", {
                "name": "Creek Promote Model",
                "command_topic": f"{b}/cmd/promote", "payload_press": "run", "icon": "mdi:arrow-up-bold-box"}),
            ("button", "creek_rollback_model", {
                "name": "Creek Rollback Model",
                "command_topic": f"{b}/cmd/rollback", "payload_press": "run", "icon": "mdi:undo-variant"}),
        ]

    def entity_ids(self) -> dict[str, str]:
        """{slug: entity_id Home Assistant will actually mint}.

        HA derives the entity_id from the device name plus the entity's **name**, not from
        the `object_id` we publish — `object_id` is documented as a suggestion and is not
        honoured here. So "Creek NWS Alert Feed Missing" on the "Rate of Rise"
        device becomes `binary_sensor.rate_of_rise_creek_nws_alert_feed_missing`,
        regardless of its `creek_nws_alerts_missing` object_id.

        Most entities hide this because their name slugifies to exactly their object_id, so
        the two rules agree by coincidence. The ones where they diverge produced dashboard
        references to entities that never existed. Deriving IDs here — and checking the
        dashboard against them in the tests — keeps that from recurring.
        """
        device_name = self._device()["name"]
        out = {}
        for component, slug, cfg in self._specs():
            out[slug] = f"{component}.{_slugify(device_name + ' ' + cfg['name'])}"
        return out

    def configs(self) -> list[tuple[str, dict]]:
        """Build (discovery_topic, full_config) pairs — exposed for testing."""
        out = []
        device, avail = self._device(), self._availability()
        for component, slug, cfg in self._specs():
            full = {
                "unique_id": f"rate_of_rise_{slug}",
                "object_id": slug,
                "device": device,
                **avail,
                **cfg,
            }
            topic = f"{DISCOVERY_PREFIX}/{component}/{NODE_ID}/{slug}/config"
            out.append((topic, full))
        return out

    def publish_all(self) -> None:
        pairs = self.configs()
        for topic, cfg in pairs:
            self._publish_raw(topic, json.dumps(cfg), True)
        log.info("Published MQTT discovery for %d entities", len(pairs))
