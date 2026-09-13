"""Home Assistant Core API client via the Supervisor proxy.

Because the add-on sets `homeassistant_api: true`, requests to
`http://supervisor/core/api` authenticate with the injected SUPERVISOR_TOKEN —
no user-created long-lived access token is required or stored.
"""
from __future__ import annotations

import logging

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
