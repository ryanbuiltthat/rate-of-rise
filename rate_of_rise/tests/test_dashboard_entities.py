"""The dashboard must reference entity IDs the add-on actually creates.

Regression guard. Home Assistant mints entity_ids from the device name plus the entity
*name*; the `object_id` we publish is only a suggestion and is not honoured. Most entities
hide that because their name slugifies to exactly their object_id — but five did not, and
the dashboard referenced the object_id form for all five, so those cards showed "Entity not
found" on a perfectly healthy system.

Run: python rate_of_rise/tests/test_dashboard_entities.py
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "rate_of_rise"))

from app.discovery import DiscoveryPublisher, _slugify  # noqa: E402

DASHBOARD = ROOT / "dashboards" / "creek_flood_watch.yaml"
PACKAGE_DIR = ROOT / "ha-packages"

# The live add-on's MQTT-discovery device is still registered under the name it had before
# the public repo's genericized "Rate of Rise" rename (41e6caf) — HA doesn't rename entities
# in an existing install just because discovery.py's device name changes, and re-pointing the
# live device identity means migrating the entity registry, which hasn't happened. The
# dashboard intentionally targets this pre-rename prefix (see its own header comment) rather
# than the one _device() returns today, so it stays usable against the actual live system.
LEGACY_DEVICE_NAME = "Ackerly Creek Modeling"

# ...but that grandfathering applies ONLY to entities that already existed when the rename
# landed. Home Assistant freezes an entity_id at first registration; it does not recompute
# it when the device name changes. So a pre-rename entity keeps the old prefix forever,
# while an entity added *after* the rename registers against the device's current name and
# comes out as `rate_of_rise_*` — which is exactly what `entity_ids()` returns.
#
# Referencing a new entity with the legacy prefix therefore names an entity that will never
# exist on any install, old or fresh. That shipped once: 0.21.0's four Google Flood cards
# and its watchdog row were copied from the legacy-prefixed cards around them and were
# "Entity not found" on the live system from the moment the add-on restarted.
#
# This set is frozen history and must never gain a member — nothing added today can
# retroactively have been registered before the rename. Regenerate it only to verify:
#   git show 41e6caf:rate_of_rise/app/discovery.py
# and collect the slugs its DiscoveryPublisher._specs() yields.
PRE_RENAME_SLUGS = frozenset({
    "creek_active_model", "creek_alert_tier", "creek_annotate_latest_storm", "creek_api_index",
    "creek_candidate_model", "creek_dataset_rows", "creek_downstream_gauge_missing",
    "creek_ero_outlook_missing", "creek_event_count", "creek_flood_probability",
    "creek_forecast_data_missing", "creek_lag_estimate", "creek_lag_response_series",
    "creek_last_command", "creek_last_inference", "creek_last_nightly", "creek_model_health",
    "creek_model_method", "creek_nwm_data_missing", "creek_nwm_flow", "creek_nwm_flow_peak",
    "creek_nws_alert_count", "creek_nws_alerts_missing", "creek_nws_flash_flood_warning",
    "creek_nws_flood_warning", "creek_nws_flood_watch", "creek_pipeline_state",
    "creek_predicted_crest", "creek_promote_model", "creek_qpf_24h", "creek_qpf_6h",
    "creek_radar_cells_missing", "creek_radar_cells_tracked", "creek_radar_threat_cells",
    "creek_radar_threat_eta", "creek_radar_threat_max_dbz", "creek_rain_1h", "creek_rain_24h",
    "creek_rain_3h", "creek_rain_6h", "creek_rain_72h", "creek_rain_on_snow",
    "creek_rain_rate_stale", "creek_retrain_now", "creek_rollback_model",
    "creek_run_inference_now", "creek_snow_water_equivalent", "creek_snowpack_data_missing",
    "creek_soil_moisture_mean", "creek_soil_moisture_stale", "creek_soil_ponding",
    "creek_stage_stale", "creek_storm_in_progress", "creek_storm_to_annotate",
    "creek_temperature", "creek_tier_reason", "creek_upstream_data_missing",
    "creek_upstream_precip_today", "creek_upstream_rain_1h", "creek_upstream_rain_24h",
    "creek_upstream_rain_3h", "creek_upstream_rain_6h", "creek_upstream_rain_72h",
    "creek_usgs_leggetts_flow", "creek_usgs_leggetts_gage", "creek_usgs_leggetts_rise_3h",
    "creek_usgs_tunkhannock_flow", "creek_usgs_tunkhannock_gage",
    "creek_usgs_tunkhannock_rise_3h", "creek_wpc_ero_day1", "creek_wpc_ero_day2",
    "creek_wpc_ero_day3",
})

# Entities that legitimately come from outside the add-on.
EXTERNAL = {
    # Gateway entities registered AFTER the gateway device was renamed in HA. Exactly the
    # LEGACY_DEVICE_NAME mechanism above, one device over: HA freezes an entity_id at first
    # registration and never recomputes it, so entities that existed before the rename kept
    # `creek_gateway_*` while everything added since registers as `outside_creek_gateway_*`.
    # Both prefixes are live and correct. Do not "tidy" either to match the other -- the
    # dashboard has to name each entity by the id HA actually assigned it.
    "binary_sensor.outside_creek_gateway_creek_node_radar_fault",
    "binary_sensor.outside_creek_gateway_creek_node_diagnostic_active",
    # RFM69 gateway (firmware/esp32_rfm69_gateway/gateway.base.yaml). test_esphome_entities.py
    # is what proves the gateway actually publishes these; here they are just "not the add-on's".
    "sensor.creek_gateway_stage",
    "sensor.creek_gateway_creek_depth",
    # Packet-driven link state for the creek node. test_esphome_entities.py proves the
    # gateway publishes it and that the add-on's creek_node_status_entity option names it.
    "binary_sensor.creek_gateway_creek_node_status",
    "sensor.outside_weather_station_soil_moisture_willow",  # Ecowitt WH51 #1 — near house, by the willow
    "sensor.outside_weather_station_soil_moisture_field",   # Ecowitt WH51 #2 — near creek
    "sensor.outside_weather_station_rain_intensity",
    "sensor.outside_weather_station_rain_daily",
    # ha-packages/creek_warning.yaml's own unique_id predates the same "Rate of Rise" rename
    # (41e6caf) and is a plain template-sensor id, not device-prefixed — the live install's
    # copy of that package hasn't been updated to rate_of_rise_service_stale either.
    "binary_sensor.creek_modeling_service_stale",
}

ENTITY_PATTERN = re.compile(r"\b(?:binary_sensor|sensor|button)\.[a-z0-9_]+\b")


def addon_entity_ids():
    return set(DiscoveryPublisher(lambda *a: None, "creek").entity_ids().values())


def legacy_entity_id(component, cfg):
    """The entity_id HA minted for this spec back when the device carried its old name."""
    return f"{component}.{_slugify(LEGACY_DEVICE_NAME + ' ' + cfg['name'])}"


def legacy_addon_entity_ids():
    """Legacy-prefixed IDs for the entities that actually have one on the live install.

    Deliberately not every spec: generating a legacy ID for *all* of them let the union
    check below accept either prefix for any entity, so it could not tell a correct
    reference from one naming an entity that has never existed.
    """
    pub = DiscoveryPublisher(lambda *a: None, "creek")
    return {
        legacy_entity_id(component, cfg)
        for component, slug, cfg in pub._specs()
        if slug in PRE_RENAME_SLUGS
    }


def package_entity_ids():
    """Template entities from every ha-packages file, not just creek_warning.yaml.

    These belong to no device, so nothing else in the system proves they exist — if the
    dashboard names one that no package defines, it renders as an "Entity not available"
    row and the operator reads a blank where a fault indicator should be.
    """
    ids = set()
    for path in sorted(PACKAGE_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for block in doc.get("template") or []:
            for domain, entries in block.items():
                for entry in entries:
                    if isinstance(entry, dict) and "unique_id" in entry:
                        ids.add(f"{domain}.{entry['unique_id']}")
    return ids


def dashboard_references():
    """Every entity id the dashboard names, from `entity:` keys and Jinja templates."""
    doc = yaml.safe_load(DASHBOARD.read_text(encoding="utf-8"))
    found = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("entity", "entity_id") and isinstance(value, str):
                    found.add(value)
                elif isinstance(value, str):
                    found.update(ENTITY_PATTERN.findall(value))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(doc)
    return found


def test_every_dashboard_entity_exists_somewhere():
    known = addon_entity_ids() | legacy_addon_entity_ids() | package_entity_ids() | EXTERNAL
    missing = sorted(dashboard_references() - known)
    assert not missing, f"dashboard references entities nothing provides: {missing}"


def test_addon_entity_ids_come_from_the_name_not_the_object_id():
    ids = DiscoveryPublisher(lambda *a: None, "creek").entity_ids()
    # The case that broke: object_id creek_nws_alerts_missing, name "...Alert Feed...".
    assert ids["creek_nws_alerts_missing"] == (
        "binary_sensor.rate_of_rise_creek_nws_alert_feed_missing")
    # And one where the two happen to agree, so the rule is not accidentally inverted.
    assert ids["creek_stage_stale"] == (
        "binary_sensor.rate_of_rise_creek_stage_stale")


def test_entities_added_after_the_rename_use_the_current_device_prefix():
    """The 0.21.0 regression, pinned. An entity minted today gets the device's current
    name, so the legacy prefix names nothing -- and because the check above accepts both
    prefixes for grandfathered entities, only an explicit test catches this."""
    pub = DiscoveryPublisher(lambda *a: None, "creek")
    refs = dashboard_references()
    current = pub.entity_ids()

    wrong = sorted(
        legacy_entity_id(component, cfg)
        for component, slug, cfg in pub._specs()
        if slug not in PRE_RENAME_SLUGS and legacy_entity_id(component, cfg) in refs
    )
    assert not wrong, (
        f"dashboard names post-rename entities by their pre-rename id: {wrong}")

    # And the Google entities specifically, since they are what regressed.
    for slug in (s for s in current if "google" in s):
        assert current[slug].split(".", 1)[1].startswith("rate_of_rise_"), current[slug]


def test_package_entities_are_unprefixed():
    # Template entities get no device prefix — mixing the two conventions is what caused
    # the earlier round of "Entity not found".
    for entity_id in package_entity_ids():
        assert "ackerly" not in entity_id, entity_id


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
