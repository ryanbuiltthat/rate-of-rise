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
PACKAGE = ROOT / "ha-packages" / "creek_warning.yaml"

# The live add-on's MQTT-discovery device is still registered under the name it had before
# the public repo's genericized "Rate of Rise" rename (41e6caf) — HA doesn't rename entities
# in an existing install just because discovery.py's device name changes, and re-pointing the
# live device identity means migrating the entity registry, which hasn't happened. The
# dashboard intentionally targets this pre-rename prefix (see its own header comment) rather
# than the one _device() returns today, so it stays usable against the actual live system.
LEGACY_DEVICE_NAME = "Ackerly Creek Modeling"

# Entities that legitimately come from outside the add-on.
EXTERNAL = {
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


def legacy_addon_entity_ids():
    """entity_ids(), but minted against the live install's pre-rename device name."""
    pub = DiscoveryPublisher(lambda *a: None, "creek")
    return {
        f"{component}.{_slugify(LEGACY_DEVICE_NAME + ' ' + cfg['name'])}"
        for component, _slug, cfg in pub._specs()
    }


def package_entity_ids():
    doc = yaml.safe_load(PACKAGE.read_text(encoding="utf-8"))
    ids = set()
    for block in doc.get("template") or []:
        for domain, entries in block.items():
            for entry in entries:
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
