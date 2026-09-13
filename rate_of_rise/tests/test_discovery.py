"""Plain-assert tests for the discovery payloads. Run: python rate_of_rise/tests/test_discovery.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.discovery import DiscoveryPublisher  # noqa: E402


def build():
    published = []
    pub = DiscoveryPublisher(lambda t, p, r: published.append((t, p, r)), "creek")
    return pub, published


def test_topics_and_counts():
    pub, _ = build()
    pairs = pub.configs()
    sensors = [t for t, _ in pairs if "/sensor/" in t]
    binaries = [t for t, _ in pairs if "/binary_sensor/" in t]
    buttons = [t for t, _ in pairs if "/button/" in t]
    texts = [t for t, _ in pairs if "/text/" in t]
    # 16 status/model (incl. Phase 3 lag series) + 8 (2a incl. API index) + 8 (2b)
    # + 6 (2c) + 1 (2d) + 2 (2e) + 4 (2g radar cells) + 3 (2h WPC ERO) + 1 soil mean
    # (migrated out of the HA package) + 1 storm-to-annotate (dashboard annotation)
    assert len(sensors) == 50, len(sensors)
    # 3 NWS flags + rain-on-snow + ponding + storm-in-progress + 11 watchdogs
    assert len(binaries) == 17, len(binaries)
    assert len(buttons) == 4, len(buttons)
    assert len(texts) == 1, len(texts)   # annotate-latest-storm
    for topic, _ in pairs:
        assert topic.startswith("homeassistant/")
        assert topic.endswith("/config")
        assert "/rate_of_rise/" in topic  # node_id


def test_stable_entity_id_hints_and_unique_ids():
    pub, _ = build()
    cfgs = {c["object_id"]: c for _, c in pub.configs()}
    fp = cfgs["creek_flood_probability"]
    assert fp["name"] == "Creek Flood Probability"        # slugifies to sensor.creek_flood_probability
    assert fp["unique_id"] == "rate_of_rise_creek_flood_probability"
    assert fp["state_topic"] == "creek/flood_probability"


def test_every_entity_has_device_and_availability():
    pub, _ = build()
    for _, c in pub.configs():
        assert c["device"]["identifiers"] == ["rate_of_rise"]
        assert c["availability_topic"] == "creek/status/availability"
        assert c["payload_not_available"] == "offline"


def test_buttons_have_command_topics():
    pub, _ = build()
    cfgs = {c["object_id"]: c for _, c in pub.configs()}
    assert cfgs["creek_retrain_now"]["command_topic"] == "creek/cmd/retrain"
    assert cfgs["creek_run_inference_now"]["command_topic"] == "creek/cmd/run_inference"


def test_annotate_text_entity_has_the_right_command_topic():
    pub, _ = build()
    cfgs = {c["object_id"]: c for _, c in pub.configs()}
    annotate = cfgs["creek_annotate_latest_storm"]
    assert annotate["command_topic"] == "creek/cmd/annotate"


def test_annotate_text_entity_max_is_within_the_mqtt_text_platform_ceiling():
    """255 is not a preference, it is HA's hard limit for MQTT text entities. A `max`
    above it does not get clamped -- the whole discovery payload for that entity is
    rejected and the entity is never created, silently. That shipped once (0.12.1: 500)
    and the entity simply did not exist in Home Assistant; this is the regression guard."""
    pub, _ = build()
    cfgs = {c["object_id"]: c for _, c in pub.configs()}
    for object_id, cfg in cfgs.items():
        if "max" in cfg:
            assert 0 <= cfg["max"] <= 255, f"{object_id}: max={cfg['max']} exceeds the MQTT text ceiling"


def test_storm_to_annotate_sensor_reads_latest_closed_not_latest():
    """Guards picking the wrong field: `latest` can be an open event (see
    storms.latest_closed's docstring), which is not what the text box beside it targets."""
    import re

    pub, _ = build()
    cfgs = {c["object_id"]: c for _, c in pub.configs()}
    refs = set(re.findall(r"value_json\.(\w+)",
                          cfgs["creek_storm_to_annotate"]["value_template"]))
    assert refs == {"latest_closed"}, refs


def test_base_topic_is_honored():
    published = []
    pub = DiscoveryPublisher(lambda t, p, r: published.append((t, p, r)), "flood")
    cfgs = {c["object_id"]: c for _, c in pub.configs()}
    assert cfgs["creek_flood_probability"]["state_topic"] == "flood/flood_probability"


def test_rain_and_qpf_sensors_present():
    pub, _ = build()
    cfgs = {c["object_id"]: c for _, c in pub.configs()}
    for w in (1, 3, 6, 24, 72):
        assert cfgs[f"creek_rain_{w}h"]["state_topic"] == "creek/features"
    assert cfgs["creek_qpf_6h"]["device_class"] == "precipitation"
    assert cfgs["creek_qpf_24h"]["unit_of_measurement"] == "in"


def test_publish_all_emits_retained_json():
    pub, published = build()
    pub.publish_all()
    assert len(published) == 72
    for topic, payload, retain in published:
        assert retain is True
        json.loads(payload)  # valid JSON


def test_every_value_template_resolves_against_a_published_payload():
    """Guards a real bug: `creek_temperature` and `creek_rain_on_snow` referenced
    `temp_f` / `rain_on_snow_flag`, which are derived in FeatureBuilder and so were not in
    FEATURE_KEYS — the payload never carried them and both entities stayed unknown."""
    import re

    from app.features import DERIVED_KEYS
    from app.health import WATCHDOG_KEYS
    from app.sources import FEATURE_KEYS

    payloads = {
        "features": set(FEATURE_KEYS) | set(DERIVED_KEYS),
        "status/health": set(WATCHDOG_KEYS),
        "soil": {"mean_pct", "ponding", "near_house_pct", "near_creek_pct"},
        "status/storms": {"event_count", "open", "latest", "latest_closed"},
        "status/lag": {"lag_minutes", "correlation", "response", "rain_series",
                       "samples", "reason"},
    }
    pub, _ = build()
    missing = []
    for _, cfg in pub.configs():
        topic = cfg.get("state_topic", "").removeprefix("creek/")
        if topic not in payloads:
            continue
        for ref in re.findall(r"value_json\.(\w+)", cfg.get("value_template", "")):
            if ref not in payloads[topic]:
                missing.append((cfg["object_id"], topic, ref))
    assert not missing, missing


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
