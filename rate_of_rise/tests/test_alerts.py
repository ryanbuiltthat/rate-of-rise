"""Tests for the NWS active-alerts source (no HTTP).
Run: python rate_of_rise/tests/test_alerts.py
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


def test_history_between_uses_effective_and_expiry_windows():
    from datetime import datetime, timezone
    calls = []
    doc = {"features": [
        {"properties": {"event": "Flood Watch", "onset": "2026-10-07T10:00:00-04:00",
                        "ends": "2026-10-07T20:00:00-04:00"}},
        {"properties": {"event": "Flash Flood Warning", "effective": "2026-10-07T13:00:00-04:00",
                        "expires": "2026-10-07T14:00:00-04:00"}}]}

    def fetch(url, timeout=15.0):
        calls.append(url)
        return doc

    at = NwsAlerts(41.5, -75.9, fetch=fetch).history_between(
        datetime(2026, 10, 7, 12, tzinfo=timezone.utc), datetime(2026, 10, 8, tzinfo=timezone.utc))
    assert "alerts?point=41.5,-75.9&start=2026-10-07T12:00:00Z&end=2026-10-08T00:00:00Z" in calls[0]
    before = at(datetime(2026, 10, 7, 13, tzinfo=timezone.utc))         # 09:00 EDT
    assert before["nws_flood_watch"] == 0.0 and before["nws_alert_count"] == 0.0
    during = at(datetime(2026, 10, 7, 17, 30, tzinfo=timezone.utc))     # 13:30 EDT
    assert during["nws_flood_watch"] == 1.0 and during["nws_flash_flood_warning"] == 1.0
    assert during["nws_flood_warning"] == 1.0 and during["nws_alert_count"] == 2.0
    after = at(datetime(2026, 10, 7, 19, tzinfo=timezone.utc))          # 15:00 EDT
    assert after["nws_flash_flood_warning"] == 0.0 and after["nws_flood_watch"] == 1.0
    assert len(calls) == 1


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
