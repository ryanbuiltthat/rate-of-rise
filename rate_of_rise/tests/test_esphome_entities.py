"""The RFM69 gateway must publish the entity IDs the rest of the system reads.

`stage_entity` in the add-on's config.yaml, and the creek entities on the dashboard, are
just strings — nothing connects them to the firmware. If the gateway's device name or a
sensor name changes, the add-on reads an entity that does not exist, which in Home
Assistant is silence rather than an error: `get_float` returns None, stage stays null, and
tiers 3-4 simply never fire. Every watchdog stays green throughout, because "no stage" is
also what "no sensor built yet" looks like.

That is not hypothetical here. The retired ESP32-C6 node published `sensor.creek_stage`;
when the Moteino + RFM69 gateway replaced it the gateway published only raw distance, and
nothing re-pointed `stage_entity` — so the pipeline ran for weeks reporting `stage_stale`
and quietly falling back to a USGS proxy. This test is what catches that class of break.

Home Assistant builds an ESPHome entity_id as slugify("<friendly_name> <entity name>"), so
device `Creek Gateway` + sensor `Stage` -> sensor.creek_gateway_stage.

Run: python rate_of_rise/tests/test_esphome_entities.py
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "firmware" / "esp32_rfm69_gateway" / "gateway.base.yaml"
ADDON_CONFIG = ROOT / "rate_of_rise" / "config.yaml"
DASHBOARD = ROOT / "dashboards" / "creek_flood_watch.yaml"

# Domains an ESPHome config declares that become HA entities.
ENTITY_DOMAINS = ("sensor", "binary_sensor", "text_sensor", "number", "switch", "button")


class _EsphomeLoader(yaml.SafeLoader):
    """ESPHome YAML carries tags SafeLoader rejects (`!secret`, `!lambda`)."""


_EsphomeLoader.add_multi_constructor(
    "!", lambda loader, suffix, node: f"<{suffix}>"
)


def _substitute(raw: str, subs: dict) -> str:
    for key, value in subs.items():
        raw = raw.replace("${%s}" % key, str(value))
    return raw


def load_gateway() -> dict:
    raw = GATEWAY.read_text(encoding="utf-8")
    subs = yaml.load(raw, Loader=_EsphomeLoader).get("substitutions", {})
    return yaml.load(_substitute(raw, subs), Loader=_EsphomeLoader)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def gateway_entity_ids() -> dict[str, str]:
    """{entity name: entity_id} for every named entity the gateway declares.

    Covers both the plain platform lists and the named sub-sensors the rfm69_gateway
    component nests one level down (distance/battery_voltage/rssi/...).
    """
    doc = load_gateway()
    prefix = doc["esphome"]["friendly_name"]
    out = {}

    def add(name, domain):
        out[name] = f"{domain}.{slugify(prefix)}_{slugify(name)}"

    def collect(domain, entry):
        for key, value in entry.items():
            if key == "name" and isinstance(value, str):
                add(value, domain)
            elif isinstance(value, dict) and "name" in value:
                add(value["name"], domain)

    for domain in ENTITY_DOMAINS:
        for entry in doc.get(domain) or []:
            collect(domain, entry)

    # The component block is a mapping, not a list, and its sub-sensors carry the domain
    # implicitly: everything it exposes is a sensor except the two called out here.
    non_sensor = {"node_status": "binary_sensor", "ota_status": "text_sensor"}
    for key, value in (doc.get("rfm69_gateway") or {}).items():
        if isinstance(value, dict) and "name" in value:
            add(value["name"], non_sensor.get(key, "sensor"))
    return out


def test_gateway_publishes_the_stage_entity_the_addon_reads():
    expected = yaml.safe_load(ADDON_CONFIG.read_text(encoding="utf-8"))["options"]["stage_entity"]
    ids = set(gateway_entity_ids().values())
    assert expected in ids, (
        f"add-on reads {expected!r} but the gateway publishes {sorted(ids)}")


def test_gateway_publishes_the_node_status_entity_the_addon_reads():
    """The link check is only a check if it names an entity that exists.

    A typo here fails open — HAClient.get_bool returns None for a missing entity, which
    FeatureBuilder._link_usable reads as "no link sensor configured" and falls back to the
    weaker age check. Silent, and exactly when it matters least to be wrong quietly.
    """
    expected = yaml.safe_load(ADDON_CONFIG.read_text(encoding="utf-8"))["options"][
        "creek_node_status_entity"]
    ids = set(gateway_entity_ids().values())
    assert expected in ids, (
        f"add-on reads {expected!r} but the gateway publishes {sorted(ids)}")


def test_dashboard_creek_entities_are_published_by_the_gateway():
    """The dashboard's non-add-on `sensor.creek_*` references must come from the gateway.

    Scoped to sensor.creek_* deliberately: add-on entities carry the `rate_of_rise_`
    device prefix (test_dashboard_entities.py owns those), so they never match this
    pattern in the first place — no exclusion needed here any more.
    """
    # Comments discuss entities in prose and globs ("sensor.creek_gateway_*"), which are not
    # references; scanning them would fail the test on its own documentation.
    dash = "\n".join(line for line in DASHBOARD.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))
    referenced = set(re.findall(r"\bsensor\.creek_[a-z0-9_]+\b", dash))
    published = set(gateway_entity_ids().values())
    missing = sorted(referenced - published)
    assert not missing, f"dashboard references entities the gateway does not publish: {missing}"


def test_every_entity_lands_under_the_creek_prefix():
    """The repo's convention (DOCS.md): gateway entities are `sensor.creek_*`. A
    friendly_name that does not start with "Creek" would silently renames every entity at
    once and break every reference in the add-on config and the dashboard together."""
    bad = [eid for eid in gateway_entity_ids().values()
           if not re.match(r"^[a-z_]+\.creek_[a-z0-9_]+$", eid)]
    assert not bad, f"entities outside the creek_ prefix convention: {bad}"


def test_stage_and_depth_are_both_published():
    """Stage feeds app/tiers.py (feet); depth is the human-readable one (inches). They are
    the same measurement, so losing either silently halves what the system can show."""
    names = set(gateway_entity_ids())
    for required in ("Stage", "Creek Depth"):
        assert required in names, f"gateway no longer publishes {required!r}: {sorted(names)}"


def test_wake_accounting_entities_are_published():
    """Average current is derived from these two plus battery voltage, with no shunt on the
    pack (docs/node-hardware.md, "Measuring average draw without a shunt").

    Dropping either one breaks the measurement silently rather than loudly: the overnight
    voltage slope still computes and still looks entirely reasonable, it is just no longer
    divisible by a known number of wakes, so the mV/h -> mA step quietly reverts to being an
    assumption. Nothing errors, and the resulting current is wrong by whatever the duty cycle
    drifted. Counting battery rows instead is not a fallback -- HA's recorder dedups identical
    states and the node's 6.45 mV ADC quantum makes those common, which undercounts wakes by
    roughly 60 % and does it in a way that correlates with pack activity.
    """
    names = set(gateway_entity_ids())
    for required in ("Creek Node Packets", "Creek Node Fast Sampling"):
        assert required in names, f"gateway no longer publishes {required!r}: {sorted(names)}"


def test_fast_sampling_flag_is_actually_sent_by_the_node():
    """The gateway can only publish the cadence flag if the node still puts it on the wire.

    These are two separate codebases flashed over two different paths -- the node over RFM69
    OTA, the gateway over WiFi -- so they can and do drift apart. If `fast` were dropped from
    the payload the gateway would publish "not fast" forever, which is indistinguishable from
    a node that simply never sees a rise.
    """
    node_src = (ROOT / "firmware" / "moteino_creek_node" / "src" / "main.cpp").read_text(
        encoding="utf-8")
    assert '\\"fast\\":%d' in node_src, (
        "node firmware no longer sends the `fast` flag the gateway publishes")


def test_blanking_zone_matches_the_sensor_datasheet():
    """SEN0676 minimum range is 0.15 m; anything closer is not a measurement. The stage
    lambda uses this to reject a lost target, so a too-small value publishes noise as depth."""
    subs = yaml.load(GATEWAY.read_text(encoding="utf-8"), Loader=_EsphomeLoader)["substitutions"]
    assert int(subs["blanking_mm"]) >= 150, "blanking zone below the datasheet minimum range"


def test_installation_height_default_is_the_surveyed_value():
    """43.5 in = 1105 mm, creekbed -> sensor face. If this drifts from the survey the whole
    depth scale shifts, and nothing else in the system can tell."""
    doc = load_gateway()
    number = next(n for n in doc["number"] if n.get("id") == "mount_height_mm")
    assert int(number["initial_value"]) == 1105, (
        f"installation height default is {number['initial_value']}, surveyed value is 1105 mm")
    assert number["restore_value"] is True, (
        "restore_value must stay true or a reflash silently reverts the datum to the default")


# ESP32-C3 strapping pins: GPIO2, GPIO8, GPIO9. Wiring the radio to GPIO9 (BOOT) holds it
# low through reset via MISO and the chip comes up in ROM download mode — no WiFi, no logs,
# nothing. Verified on hardware; see the WIRING comment in gateway.base.yaml.
C3_STRAPPING_PINS = {"2", "8", "9"}


def test_no_radio_pin_lands_on_a_strapping_pin():
    subs = yaml.load(GATEWAY.read_text(encoding="utf-8"), Loader=_EsphomeLoader)["substitutions"]
    assigned = {k: str(v) for k, v in subs.items() if k.endswith("_pin")}
    bad = {k: v for k, v in assigned.items() if v in C3_STRAPPING_PINS}
    assert not bad, f"radio pins on ESP32-C3 strapping lines: {bad}"


def test_pin_assignments_are_unique():
    subs = yaml.load(GATEWAY.read_text(encoding="utf-8"), Loader=_EsphomeLoader)["substitutions"]
    pins = [str(v) for k, v in subs.items() if k.endswith("_pin")]
    assert len(pins) == len(set(pins)), f"a GPIO is assigned twice: {pins}"


def test_radio_frequency_matches_the_node_firmware():
    """Band mismatch is silent: both radios initialize fine and simply never hear each
    other. The node's FREQUENCY lives in C, so it can only be checked as text."""
    subs = yaml.load(GATEWAY.read_text(encoding="utf-8"), Loader=_EsphomeLoader)["substitutions"]
    node_src = (ROOT / "firmware" / "moteino_creek_node" / "src" / "main.cpp").read_text(
        encoding="utf-8")
    match = re.search(r"#define\s+FREQUENCY\s+RF69_(\d+)MHZ", node_src)
    assert match, "could not find FREQUENCY in the node firmware"
    assert match.group(1) == str(subs["rfm69_frequency"]), (
        f"gateway is on {subs['rfm69_frequency']} MHz but the node is on {match.group(1)} MHz")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
