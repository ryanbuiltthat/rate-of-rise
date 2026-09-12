"""Tests for the NWS active-alerts source (no HTTP).
Run: python creek_modeling/tests/test_alerts.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.alerts import NwsAlerts  # noqa: E402


def make(*events):
    payload = {"features": [{"properties": {"event": e}} for e in events]}
    return NwsAlerts(41.0, -75.5, fetch=lambda url: payload)


def test_no_alerts_is_all_zero():
    out = make().poll()
    assert out["nws_flood_watch"] == 0.0
    assert out["nws_flood_warning"] == 0.0
    assert out["nws_flash_flood_warning"] == 0.0
    assert out["nws_alert_count"] == 0.0


def test_flood_watch():
    out = make("Flood Watch").poll()
    assert out["nws_flood_watch"] == 1.0
    assert out["nws_flood_warning"] == 0.0


def test_flood_warning():
    out = make("Flood Warning").poll()
    assert out["nws_flood_warning"] == 1.0
    assert out["nws_flash_flood_warning"] == 0.0


def test_flash_flood_warning_also_sets_the_broader_warning_flag():
    out = make("Flash Flood Warning").poll()
    assert out["nws_flash_flood_warning"] == 1.0
    # Callers checking "is there a flood warning" must not have to know about flash.
    assert out["nws_flood_warning"] == 1.0


def test_unrelated_alerts_are_counted_but_do_not_set_flood_flags():
    out = make("Heat Advisory", "Air Quality Alert").poll()
    assert out["nws_alert_count"] == 2.0
    assert out["nws_flood_warning"] == 0.0
    assert out["nws_flood_watch"] == 0.0


def test_case_and_whitespace_insensitive():
    out = make("  FLASH FLOOD WARNING  ").poll()
    assert out["nws_flash_flood_warning"] == 1.0


def test_missing_event_names_are_skipped():
    out = NwsAlerts(0, 0, fetch=lambda url: {"features": [{"properties": {}}, {}]}).poll()
    assert out["nws_alert_count"] == 0.0


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
