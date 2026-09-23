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


def test_radar_diagnostic_window_ships_disabled():
    """The #17 diagnostic deliberately blinds the radar, so it must never ship armed.

    Flashed with DIAG_RADAR_WINDOW_ENABLE at 1, the node stops reading the creek for up to
    20 minutes at a stretch, two hours a day, forever — and it does so silently, because a
    held-off rail produces the same null distance as a Modbus timeout. On a flashy basin
    where rainfall-to-crest is tens of minutes, a forgotten 1 here is a blind window during
    exactly the event the system exists to catch. Flip it to 1 to run the test, flip it back
    before the build that stays on the pole.
    """
    node_src = (ROOT / "firmware" / "moteino_creek_node" / "src" / "main.cpp").read_text(
        encoding="utf-8")
    match = re.search(r"#define\s+DIAG_RADAR_WINDOW_ENABLE\s+(\d+)", node_src)
    assert match, "could not find DIAG_RADAR_WINDOW_ENABLE in the node firmware"
    assert match.group(1) == "0", (
        "the radar-rail diagnostic is armed; it blinds the creek sensor and must be "
        "reverted to 0 before this firmware goes on the pole")


def test_diagnostic_peek_gap_stays_under_the_rate_of_rise_window():
    """The blind gap between peeks must stay shorter than the add-on's own rate window.

    DIAG_PEEK_EVERY cycles at REPORT_INTERVAL_S is how long the node can go without looking
    at the creek during the diagnostic. MAX_RATE_GAP_S (300 s) is the longest gap the node
    will still compute a rate of rise across, so a peek interval below it would let the node
    manufacture a rate from two readings straddling a blind stretch. Above it the node
    correctly declines to compute a rate at all, which is the honest behaviour — but the gap
    still has to be bounded, or the safety peek stops being a safety peek.
    """
    node_src = (ROOT / "firmware" / "moteino_creek_node" / "src" / "main.cpp").read_text(
        encoding="utf-8")

    def define(name):
        m = re.search(rf"#define\s+{name}\s+(\d+)", node_src)
        assert m, f"could not find {name} in the node firmware"
        return int(m.group(1))

    gap_s = define("DIAG_PEEK_EVERY") * define("REPORT_INTERVAL_S")
    assert gap_s <= 1800, (
        f"diagnostic blind gap is {gap_s} s; rainfall-to-crest here is tens of minutes, "
        "so anything beyond ~30 min stops being a peek and becomes an outage")


def test_ota_url_points_at_the_tag_the_firmware_workflow_publishes():
    """`node_hex_url` and firmware-hex.yml's FIRMWARE_TAG have to name the same release.

    They are two files with no reference between them, and the failure is silent in the worst
    direction: a mismatched tag makes the fetch 404, the gateway reports `fetch error http
    404`, and the node keeps running whatever it already had. Nothing on the dashboard
    changes, so an OTA that never happened looks exactly like an OTA that did.

    Also pins the URL to a release asset rather than raw.githubusercontent.com. The raw form
    served the committed copy, which the workflow can no longer push to main now that main
    requires pull requests — it would serve a stale image indefinitely, with a 200.
    """
    workflow = (ROOT / ".github" / "workflows" / "firmware-hex.yml").read_text(
        encoding="utf-8")
    tag_match = re.search(r"FIRMWARE_TAG:\s*(\S+)", workflow)
    assert tag_match, "could not find FIRMWARE_TAG in firmware-hex.yml"
    tag = tag_match.group(1)

    subs = yaml.load(GATEWAY.read_text(encoding="utf-8"), Loader=_EsphomeLoader)[
        "substitutions"]
    url = subs["node_hex_url"]

    assert "raw.githubusercontent.com" not in url, (
        "node_hex_url points at the committed firmware.hex; that copy is no longer pushed "
        "to main automatically and will silently go stale")
    assert f"/releases/download/{tag}/" in url, (
        f"node_hex_url {url!r} does not fetch from the {tag!r} release the workflow publishes")


def test_diagnostic_image_is_built_published_and_reachable_by_one_button():
    """Running the #17 diagnostic must stay a single button press, end to end.

    Three things have to line up and none of them reference each other: CI has to build the
    armed image and attach it to the release, the gateway has to point at that asset, and a
    button has to exist to push it. Break any one and the failure is a dead end for the person
    trying to use it — the button is there but 404s, or the asset exists but nothing installs
    it. The whole point of the design is that nobody has to arm, build and convert anything by
    hand, so the wiring that makes that true is worth pinning.
    """
    workflow = (ROOT / ".github" / "workflows" / "firmware-hex.yml").read_text(
        encoding="utf-8")
    assert "firmware-diag.hex" in workflow, (
        "the workflow no longer builds the armed diagnostic image")
    # Both publish paths, not just one. `create` runs when the release is new and `upload`
    # when it already exists, and they name their assets separately -- the create path once
    # shipped with only firmware.hex, which published a release the diagnostic button could
    # not use while the workflow reported success.
    for command in ("gh release create", "gh release upload"):
        line = next((l for l in workflow.splitlines()
                     if command in l and not l.strip().startswith("#")), None)
        assert line is not None, f"{command!r} is gone from the workflow"
        assert "firmware-diag.hex" in line, (
            f"{command!r} does not publish the diagnostic image: {line.strip()!r}")
    assert "DIAG_RADAR_WINDOW_ENABLE 1" in workflow, (
        "the workflow no longer arms the diagnostic build at build time")

    tag = re.search(r"FIRMWARE_TAG:\s*(\S+)", workflow).group(1)
    subs = yaml.load(GATEWAY.read_text(encoding="utf-8"), Loader=_EsphomeLoader)[
        "substitutions"]
    assert f"/releases/download/{tag}/firmware-diag.hex" in subs["node_diag_hex_url"], (
        "node_diag_hex_url does not fetch the diagnostic asset the workflow publishes")

    names = set(gateway_entity_ids())
    assert "Push Node Diagnostic Firmware" in names, (
        "no button installs the diagnostic image; it would have to be flashed by hand")


def test_diagnostic_window_is_one_shot():
    """A forgotten diagnostic image has to expire on its own.

    The safety argument for shipping an armed build at all is that it costs one window, once —
    not a blind window every day until somebody notices. That rests entirely on the latch, and
    on the latch only closing once the window has produced usable data (so a storm-shortened
    night retries instead of burning the single shot and needing a human to intervene). Lose
    either half and the design quietly becomes one that depends on remembering to revert.
    """
    node_src = (ROOT / "firmware" / "moteino_creek_node" / "src" / "main.cpp").read_text(
        encoding="utf-8")
    assert "diagCompleted" in node_src, "the one-shot latch is gone"
    assert re.search(r"diagHeld\s*>=\s*DIAG_MIN_USEFUL_HOLDS", node_src), (
        "the latch no longer requires a usable sample, so a cut-short window burns the shot")


def test_rolling_firmware_release_is_published_as_a_prerelease():
    """The add-on's `v<version>` releases must stay the repository's "Latest".

    GitHub gives "Latest" to whichever release is newest, so the rolling firmware release
    takes it by default -- and did, until this was fixed: the front page of a public
    repository advertised node firmware as the project's latest release, above the add-on it
    exists to ship. Home Assistant itself is unaffected (Supervisor reads `version:` from
    rate_of_rise/config.yaml on the default branch and never looks at releases), but the label
    misrepresents the project and is a trap for any future code reaching for /releases/latest.

    It must be `--prerelease` specifically. `--latest=false` and the API's `make_latest=false`
    are only honoured at creation and do not move the label off a release already holding it;
    that was verified against the live release. Both publish paths need it, for the same
    reason the asset list does: create runs when the tag is new, upload-then-edit when it
    already exists.
    """
    workflow = (ROOT / ".github" / "workflows" / "firmware-hex.yml").read_text(
        encoding="utf-8")
    body = [l for l in workflow.splitlines() if not l.strip().startswith("#")]

    create = next((n for n, l in enumerate(body) if 'gh release create "$TAG"' in l), None)
    assert create is not None, "the create path is gone from the workflow"
    assert any("--prerelease" in l for l in body[create:create + 8]), (
        'the create path does not pass --prerelease; a new rolling release would take the '
        '"Latest" label from the add-on')

    edit = next((l for l in body if 'gh release edit "$TAG"' in l), None)
    assert edit is not None, "the release-edit call is gone from the workflow"
    assert "--prerelease" in edit, "the upload path does not re-assert --prerelease"


def test_ota_url_does_not_use_the_floating_latest_release():
    """`/releases/latest/` resolves repo-wide, and release.yml publishes `v*` add-on releases.

    Those carry no firmware.hex, so pointing the gateway at `latest` means the next add-on
    version bump turns the OTA URL into a 404 — a break with no causal connection to anything
    anyone touched in the firmware.
    """
    subs = yaml.load(GATEWAY.read_text(encoding="utf-8"), Loader=_EsphomeLoader)[
        "substitutions"]
    assert "/releases/latest/" not in subs["node_hex_url"], (
        "node_hex_url uses the floating latest release; an add-on release would break it")


def test_diagnostic_window_is_measured_from_uptime_not_the_rtc():
    """The window must not be derived from the RTC's absolute value.

    `rtc.begin()` takes `resetTime = false`, and RTCZero preserves the clock whenever the
    reset cause is watchdog, system or external — which is exactly how RFM69_OTA reboots the
    node. After a wireless push the RTC therefore carries on from wherever it was, anchored
    to the last time the battery was physically connected, and `secondsOfDay()` is not
    seconds-since-boot. The 2026-09-23 run parked the window at an offset that the 8.4 h the
    diagnostic image ran never swept across: it opened zero times, and the unchanged battery
    slope that produced read exactly like a confirmed diagnosis.

    Accumulated deltas do not care what the clock reads, only how much it advances.
    """
    node_src = (ROOT / "firmware" / "moteino_creek_node" / "src" / "main.cpp").read_text(
        encoding="utf-8")
    match = re.search(r"static bool inDiagWindow\(\)\s*\{(.*?)\n\}", node_src, re.DOTALL)
    assert match, "inDiagWindow() is gone"
    body = match.group(1)
    assert "diagUptimeS" in body, "the window no longer uses accumulated uptime"
    assert "secondsOfDay()" not in body, (
        "inDiagWindow() reads the RTC directly again; that value survives an OTA reboot and "
        "is not time-since-boot")
    assert "tickDiagUptime" in node_src, "nothing accumulates the uptime"


def test_diagnostic_active_is_sent_by_the_node_and_published():
    """A held cycle and a failed Modbus read both publish a null distance.

    Nothing else tells them apart, which made "did the diagnostic window actually run?" a
    question only a recorder query could answer — and it was asked twice, both times after an
    unchanged slope had already been read as a result. This flag is that question answered on
    the dashboard, so the next run reports on itself.
    """
    node_src = (ROOT / "firmware" / "moteino_creek_node" / "src" / "main.cpp").read_text(
        encoding="utf-8")
    assert '\\"diag\\":%d' in node_src, "the node no longer sends the diag flag"
    names = set(gateway_entity_ids())
    assert "Creek Node Diagnostic Active" in names, (
        f"gateway does not publish the diagnostic flag: {sorted(names)}")


def test_node_payload_fits_the_radio_frame():
    """RFM69::sendFrame() silently truncates past RF69_MAX_DATA_LEN (61).

    Truncation is not a crash: the node logs a perfectly good payload while the gateway logs
    a JSON parse failure, so the packet budget has to be checked here rather than discovered
    in the field. `diag` only fits because `node` was dropped to pay for it, and the headroom
    left is four bytes — narrow enough that the next key added without checking overflows it.
    """
    node_src = (ROOT / "firmware" / "moteino_creek_node" / "src" / "main.cpp").read_text(
        encoding="utf-8")
    match = re.search(r'"(\{\\"distance_mm\\":%ld.*?\})"', node_src)
    assert match, "could not find the numeric payload format string"
    # Worst case: the SEN0676's 40000 mm ceiling, the divider's 6600 mV ceiling, flags at 1.
    worst = (match.group(1).replace('\\"', '"')
             .replace("%ld", "40000").replace("%u", "6600").replace("%d", "1"))
    assert len(worst) <= 61, (
        f"worst-case payload is {len(worst)} bytes, over RF69_MAX_DATA_LEN: {worst!r}")


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
