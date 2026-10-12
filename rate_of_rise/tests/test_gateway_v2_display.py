"""Gateway v2's display package must not collide with the gateway and must read real entities.

display.yaml adds two GPIOs to a board whose other pins live in gateway.base.yaml, and imports
two of the add-on's entities by id. Neither is checked by the compiler: a doubled pin builds
fine and fights on the wire, and a mistyped entity id imports nothing, so the tier on the
screen stays `--` for ever. This test is what catches both.

Run: python rate_of_rise/tests/test_gateway_v2_display.py
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "rate_of_rise"))

from app.discovery import DiscoveryPublisher  # noqa: E402

V2 = ROOT / "firmware" / "esp32s3_feather_gateway"
BASE = V2 / "gateway.base.yaml"
DISPLAY = V2 / "display.yaml"
WRAPPERS = [V2 / "gateway.yaml", V2 / "creek-gateway-v2.yaml", V2 / "creek-gateway-v2.prod.yaml"]

# ESP32-S3 strapping pins. GPIO3 (SDA) is one, by Adafruit's design and harmlessly (see the
# WIRING comment in gateway.base.yaml); nothing new may land on any of them.
S3_STRAPPING_PINS = {"0", "3", "45", "46"}


class _EsphomeLoader(yaml.SafeLoader):
    """ESPHome YAML carries tags SafeLoader rejects (`!secret`, `!lambda`, `!include`)."""


_EsphomeLoader.add_multi_constructor("!", lambda loader, suffix, node: f"<{suffix}>")


def load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_EsphomeLoader)


def pins(doc: dict) -> dict[str, str]:
    return {k: str(v) for k, v in doc.get("substitutions", {}).items() if k.endswith("_pin")}


def test_display_pins_are_unique_and_off_strapping_lines():
    base, display = load(BASE), load(DISPLAY)
    i2c = {"i2c_sda": str(base["i2c"]["sda"]), "i2c_scl": str(base["i2c"]["scl"])}
    every = list(pins(base).values()) + list(i2c.values()) + list(pins(display).values())
    assert len(every) == len(set(every)), f"a GPIO is assigned twice: {sorted(every)}"
    bad = {k: v for k, v in pins(display).items() if v in S3_STRAPPING_PINS}
    assert not bad, f"display pins on ESP32-S3 strapping lines: {bad}"
    assert pins(display) == {"display_tx_pin": "39", "display_rx_pin": "38"}


def test_display_imports_entities_the_addon_publishes():
    published = set(DiscoveryPublisher(lambda *a: None, "creek").entity_ids().values())
    imported = [s["entity_id"] for s in load(DISPLAY)["sensor"] if s.get("platform") == "homeassistant"]
    assert sorted(imported) == ["sensor.rate_of_rise_creek_alert_tier",
                                "sensor.rate_of_rise_creek_rate_of_rise"], imported
    missing = [e for e in imported if e not in published]
    assert not missing, f"display imports entities the add-on does not publish: {missing}"


def test_ha_link_means_a_state_subscriber_not_any_api_client():
    # is_connected() counts every API client, and `esphome logs` from the Device Builder is
    # one. Opening the logs during an HA outage, exactly when someone would, would then show
    # "HA ok" and present the last tier and rise as current. Only HA subscribes to states.
    raw = DISPLAY.read_text(encoding="utf-8")
    assert "is_connected_with_state_subscription()" in raw
    assert "->is_connected()" not in raw, "display.yaml treats any API client as HA"


def test_ecowitt_time_is_read_before_it_is_used():
    # last_ecowitt() fills eco_ms through a reference. Passing eco_ms by value in the same
    # call, fresh(last_ecowitt(eco, eco_ms), eco_ms, ...), leaves the order unspecified: if the
    # copy happens first, every poll looks like it came at boot, and rain reads `--` from five
    # minutes after startup on. The read must be a statement of its own.
    raw = DISPLAY.read_text(encoding="utf-8")
    calls = [line for line in raw.splitlines() if "last_ecowitt(" in line]
    assert calls, "display.yaml no longer reads the Ecowitt reading"
    assert all("fresh(" not in line for line in calls), calls


def test_every_v2_wrapper_loads_the_display():
    for path in WRAPPERS:
        doc = load(path)
        assert "display" in doc["packages"], f"{path.name} does not include display.yaml"
        comps = [c for src in doc["external_components"] for c in src["components"]]
        assert "creek_display" in comps, f"{path.name} does not load creek_display"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
