"""Client for the v2 gateway's store API (firmware/esp32s3_feather_gateway, creek_store).

Feature detection is the point of this module. The `gateway_store_url` option can point at a
v2 gateway, at a v1 gateway (no store), at a gateway that is down, or at nothing, and only
the first is a path to backfill. Everything else must leave the add-on behaving as it did
before backfill existed: no exception escapes probe(), and nothing here logs above DEBUG.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum

import requests

log = logging.getLogger("app.backfill.client")

STORE_SCHEMA = 1
PAGE_LIMIT = 500
TIMEOUT_S = 10.0


class ProbeState(Enum):
    OK = "ok"
    NO_STORE = "no_store"          # answered, but not a v2 store: v1, or web server off
    UNREACHABLE = "unreachable"    # refused / timed out / DNS: gateway or network down
    BAD_TOKEN = "bad_token"


@dataclass
class Probe:
    state: ProbeState
    status: dict = field(default_factory=dict)


class StoreClient:
    def __init__(self, base_url: str, token: str, session: requests.Session | None = None,
                 timeout: float = TIMEOUT_S):
        self._url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = session or requests.Session()
        self._headers = {"Authorization": f"Bearer {token}"}

    def probe(self) -> Probe:
        try:
            r = self._session.get(f"{self._url}/store/status", headers=self._headers,
                                  timeout=self._timeout)
        except requests.RequestException as exc:
            log.debug("gateway store unreachable: %s", exc)
            return Probe(ProbeState.UNREACHABLE)
        if r.status_code == 401:
            return Probe(ProbeState.BAD_TOKEN)
        if r.status_code != 200:
            log.debug("gateway store probe got HTTP %s: not a v2 store", r.status_code)
            return Probe(ProbeState.NO_STORE)
        try:
            body = r.json()
        except ValueError:
            log.debug("gateway store probe got a non-JSON 200: not a v2 store")
            return Probe(ProbeState.NO_STORE)
        if (not isinstance(body, dict) or body.get("store_schema") != STORE_SCHEMA
                or not isinstance(body.get("streams"), dict)):
            log.debug("gateway store probe: unrecognised status document")
            return Probe(ProbeState.NO_STORE)
        return Probe(ProbeState.OK, body)

    def records(self, stream: str, after: int, max_records: int) -> list[dict]:
        """Records with seq > after, ascending, at most `max_records`.

        Pages until a page reaches X-Store-Last or comes back empty. Lines that do not parse
        (a torn write on the card) are skipped. Network and HTTP errors raise
        requests.RequestException; the caller treats that as "try again next pass".
        """
        out: list[dict] = []
        cursor = after
        while len(out) < max_records:
            r = self._session.get(
                f"{self._url}/store/records", headers=self._headers, timeout=self._timeout,
                params={"stream": stream, "after": cursor,
                        "limit": min(PAGE_LIMIT, max_records - len(out))})
            r.raise_for_status()
            page = []
            for line in r.text.splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and isinstance(rec.get("seq"), int) and rec["seq"] > cursor:
                    page.append(rec)
            if not page:
                break
            room = max_records - len(out)
            out.extend(page[:room])
            cursor = out[-1]["seq"]
            try:
                last = int(r.headers.get("X-Store-Last", cursor))
            except ValueError:
                last = cursor
            if cursor >= last:
                break
        return out
