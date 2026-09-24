"""Home Assistant Core API client via the Supervisor proxy.

Because the add-on sets `homeassistant_api: true`, requests to
`http://supervisor/core/api` authenticate with the injected SUPERVISOR_TOKEN —
no user-created long-lived access token is required or stored.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

log = logging.getLogger("app.ha")


class HAClient:
    def __init__(self, api_url: str, token: str, timeout: float = 10.0):
        self._url = api_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        )

    def get_state(self, entity_id: str) -> dict | None:
        """Return the raw state object for an entity, or None if unavailable."""
        try:
            r = self._session.get(f"{self._url}/states/{entity_id}", timeout=self._timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            log.warning("get_state(%s) failed: %s", entity_id, exc)
            return None

    def get_float(self, entity_id: str) -> float | None:
        """State parsed as float; None for missing/unknown/unavailable/non-numeric."""
        state = self.get_state(entity_id)
        if not state:
            return None
        raw = state.get("state")
        if raw in (None, "", "unknown", "unavailable"):
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            log.debug("Non-numeric state for %s: %r", entity_id, raw)
            return None

    def get_float_with_age(self, entity_id: str) -> tuple[float | None, float | None]:
        """(value, seconds since `last_updated`), or (None, None).

        What that age measures matters. Since HA 2024.3 a write of the *same* value moves
        only `last_reported`; `last_updated` moves when the state or its attributes change.
        So for the stage this is "time since the reading last changed", not "time since the
        node last reported" — a still creek at 1 mm resolution can legitimately hold one
        value for many minutes while the node reports every 60 s. That is why callers
        treat a live link (packet counter / node status) as the authority on freshness and
        use this age only as a fallback, and why an age that keeps climbing *with* the
        link up is the signature of a radar that has stopped re-measuring (health.py,
        `stage_frozen`). For a monotonic counter every report is a change, so there it is
        exactly the time since the last packet.
        """
        state = self.get_state(entity_id)
        if not state:
            return None, None
        raw = state.get("state")
        if raw in (None, "", "unknown", "unavailable"):
            return None, self._age_of(state)
        try:
            return float(raw), self._age_of(state)
        except (TypeError, ValueError):
            log.debug("Non-numeric state for %s: %r", entity_id, raw)
            return None, self._age_of(state)

    @staticmethod
    def _age_of(state: dict) -> float | None:
        """Seconds since `last_updated`, clamped at 0; None if it cannot be parsed."""
        stamp = state.get("last_updated") or state.get("last_changed")
        if not stamp:
            return None
        try:
            when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            log.debug("Unparseable last_updated: %r", stamp)
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - when).total_seconds())

    def get_bool(self, entity_id: str) -> bool | None:
        """True/False for an on/off entity; None when it is missing or unavailable.

        None is *not* False on purpose: an unconfigured or not-yet-created connectivity
        sensor must not read as "the node is down" and suppress the gauge.
        """
        state = self.get_state(entity_id)
        if not state:
            return None
        raw = str(state.get("state", "")).lower()
        if raw in ("on", "true", "connected", "home"):
            return True
        if raw in ("off", "false", "disconnected", "not_home"):
            return False
        return None

    def get_unit(self, entity_id: str) -> str | None:
        """The entity's unit_of_measurement attribute, or None."""
        state = self.get_state(entity_id)
        if not state:
            return None
        return (state.get("attributes") or {}).get("unit_of_measurement")

    def get_config(self) -> dict | None:
        """HA Core config (`/config`) — includes latitude/longitude/unit_system."""
        try:
            r = self._session.get(f"{self._url}/config", timeout=self._timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            log.warning("get_config failed: %s", exc)
            return None

    def get_lat_lon(self) -> tuple[float, float] | None:
        """(latitude, longitude) from HA config, or None if unavailable."""
        cfg = self.get_config()
        if not cfg:
            return None
        lat, lon = cfg.get("latitude"), cfg.get("longitude")
        if lat is None or lon is None:
            return None
        return float(lat), float(lon)

    def ping(self) -> bool:
        """Confirm the Supervisor proxy + token reach a running Core API."""
        try:
            r = self._session.get(f"{self._url}/", timeout=self._timeout)
            r.raise_for_status()
            return True
        except requests.RequestException as exc:
            log.error("HA Core API ping failed: %s", exc)
            return False
