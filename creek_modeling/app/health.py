"""Sensor-fault watchdogs (spec §4), computed in the add-on.

These used to be Home Assistant template binary sensors in `ha-packages/creek_warning.yaml`,
which meant they only worked if that file had been copied into the HA config directory and
wired up in `configuration.yaml`. They are auto-provisioned via MQTT discovery instead, like
the rest of the add-on's entities.

Moving them here also makes them *more* accurate, because the add-on can see two things a
template could not:

  * **Whether a source is still alive.** The coordinator serves a source's last-good value
    indefinitely, so a feature still holding a number says nothing about whether its API is
    still answering. Only the add-on knows when each source last succeeded.
  * **Whether an input entity is reporting.** A rain rate legitimately sitting at 0.00 in/h
    never changes state, so an HA `last_changed` check on it false-alarms during dry spells
    and stays quiet when the gauge actually dies. Here we track the last time the read
    produced a usable value at all.

The one watchdog that cannot move is the add-on's own liveness — a service cannot report
its own death — so `Creek Modeling Service Stale` stays in the HA package, backed by the
MQTT LWT that already flips these entities unavailable when the add-on stops.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("app.health")

# Feature -> (watchdog key, seconds without a usable value before it is a fault).
# Thresholds mirror the HA templates these replace.
ENTITY_INPUTS = {
    "stage_ft": ("stage_stale", 1800),
    "soil_moisture_mean_pct": ("soil_moisture_stale", 3600),
    "rain_rate_in_hr": ("rain_rate_stale", 3600),
}

# Source name -> watchdog key. A source that is not configured is not a fault, so these
# only report on sources the coordinator actually built.
SOURCE_WATCHDOGS = {
    "nws": "forecast_data_missing",
    "alerts": "nws_alerts_missing",
    "wu": "upstream_data_missing",
    "nwm": "nwm_data_missing",
    "usgs": "downstream_gauge_missing",
    "snodas": "snowpack_data_missing",
    "radar": "radar_cells_missing",
    "ero": "ero_outlook_missing",
}

WATCHDOG_KEYS = tuple(key for key, _ in ENTITY_INPUTS.values()) + tuple(
    SOURCE_WATCHDOGS.values()
)


class HealthTracker:
    def __init__(self, now_fn=time.monotonic):
        self._now = now_fn
        self._started = now_fn()
        self._last_ok: dict[str, float] = {}

    def _entity_stale(self, key: str, value, threshold: float, now: float) -> bool:
        if value is not None:
            self._last_ok[key] = now
            return False
        last = self._last_ok.get(key)
        # Never seen a value: only a fault once we have been running long enough that one
        # should have arrived. Otherwise every watchdog fires for a few minutes at boot.
        reference = last if last is not None else self._started
        return (now - reference) > threshold

    def evaluate(self, row, source_health: dict, configured: set[str]) -> dict[str, bool]:
        """Watchdog flags: True = something is wrong. Keys match WATCHDOG_KEYS."""
        now = self._now()
        flags: dict[str, bool] = {}

        for feature, (key, threshold) in ENTITY_INPUTS.items():
            flags[key] = self._entity_stale(key, getattr(row, feature, None), threshold, now)

        for source, key in SOURCE_WATCHDOGS.items():
            if source not in configured:
                flags[key] = False          # disabled on purpose is not a fault
                continue
            info = source_health.get(source) or {}
            age, stale_after = info.get("age"), info.get("stale_after", 1800)
            if age is None:
                # Never succeeded — same boot grace as the entity inputs.
                flags[key] = (now - self._started) > stale_after
            else:
                flags[key] = age > stale_after

        faults = sorted(k for k, v in flags.items() if v)
        if faults:
            log.warning("watchdogs active: %s", ", ".join(faults))
        return flags
