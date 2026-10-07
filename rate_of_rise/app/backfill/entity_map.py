"""Which record field becomes which HA entity, and how.

The add-on options carry two maps, field -> entity_id, per stream (`backfill_entity_map` is
written, `backfill_shadow_map` only logged). The fields themselves are fixed here: each one
knows how to pull its value out of a record, what kind of HA state it is, and the unit the
gateway records it in.

Absent and null are different. A key the record does not carry (an older node build, a
console with no such sensor) is MISSING: no row. A key carried as null (a failed radar read)
is None: the row says `unknown`, as the live entity did.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

STREAMS = ("node", "ecowitt")
MISSING = object()


@dataclass(frozen=True)
class Point:
    ts: float
    value: object


@dataclass(frozen=True)
class FieldSpec:
    kind: str                 # "number" | "binary" | "text"
    unit: str | None          # the unit the gateway records it in
    extract: Callable[[dict], object]
    # The record's quantum in `unit` (how finely the gateway writes it). Backfilled numbers are
    # compared with HA's raw floats to within half of it; None = no quantum, exact compare.
    resolution: float | None = None


def reset_cause_text(code: int) -> str:
    """Same table as rfm69_gateway.h's reset-cause text sensor (SAMD21 PM->RCAUSE)."""
    if code & 0x40:
        return "software"
    if code & 0x20:
        return "watchdog"
    if code & 0x10:
        return "external"
    if code & 0x04:
        return "brown-out 3.3 V"
    if code & 0x02:
        return "brown-out 1.2 V"
    if code & 0x01:
        return "power-on"
    return "unknown"


def _key(name: str):
    return lambda r: r[name] if name in r else MISSING


def _flag(name: str):
    def get(r):
        v = r.get(name)
        return MISSING if v is None else bool(v)
    return get


def _reset(r):
    v = r.get("r")
    return MISSING if v is None else reset_cause_text(int(v))


NODE_FIELDS: dict[str, FieldSpec] = {
    "stage_ft": FieldSpec("number", "ft", _key("stage_ft"), 0.0001),
    "depth_in": FieldSpec("number", "in", _key("depth_in"), 0.0001),
    "distance_mm": FieldSpec("number", "mm", _key("d"), 1.0),
    "battery_mv": FieldSpec("number", "mV", _key("v"), 1.0),
    "rssi_dbm": FieldSpec("number", "dBm", _key("rssi"), 1.0),
    "cycle": FieldSpec("number", None, _key("n")),
    "radio_init_failures": FieldSpec("number", None, _key("i")),
    "fast": FieldSpec("binary", None, _flag("f")),
    "diag_active": FieldSpec("binary", None, _flag("g")),
    "reset_cause": FieldSpec("text", None, _reset),
    # Every record is a packet heard, so the node was online at that moment.
    "node_status": FieldSpec("binary", None, lambda r: True),
}

ECOWITT_FIELDS: dict[str, FieldSpec] = {
    "rain_total_in": FieldSpec("number", "in", _key("rain_year_in"), 0.001),
    "rain_rate_in_hr": FieldSpec("number", "in/h", _key("rain_rate_in_hr"), 0.001),
    "rain_24h_in": FieldSpec("number", "in", _key("rain_24h_in"), 0.001),
    "rain_day_in": FieldSpec("number", "in", _key("rain_day_in"), 0.001),
    "rain_event_in": FieldSpec("number", "in", _key("rain_event_in"), 0.001),
    "temp_f": FieldSpec("number", "°F", _key("temp_f"), 0.1),
}

_SOIL = re.compile(r"^soil_ch(\d+)$")


def field_spec(stream: str, field: str) -> FieldSpec:
    if stream == "node":
        return NODE_FIELDS[field]
    if stream == "ecowitt":
        if field in ECOWITT_FIELDS:
            return ECOWITT_FIELDS[field]
        m = _SOIL.match(field)
        if m:
            ch = m.group(1)
            return FieldSpec("number", "%", lambda r: (r.get("soil") or {}).get(ch, MISSING), 1.0)
    raise KeyError(f"{stream}.{field}")


def parse_map(text: str) -> dict[str, dict[str, str]]:
    """Validate an entity-map option. Blank is an empty map; anything malformed raises
    ValueError naming the problem, so the add-on can say exactly what to fix."""
    if not text or not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"entity map is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("entity map must be a JSON object of streams")
    out: dict[str, dict[str, str]] = {}
    for stream, fields in data.items():
        if stream not in STREAMS:
            raise ValueError(f"unknown stream {stream!r} (expected one of {STREAMS})")
        if not isinstance(fields, dict):
            raise ValueError(f"{stream}: expected an object of field -> entity_id")
        for field, entity in fields.items():
            try:
                field_spec(stream, field)
            except KeyError:
                raise ValueError(f"unknown field {stream}.{field}") from None
            if not isinstance(entity, str) or "." not in entity:
                raise ValueError(f"{stream}.{field}: {entity!r} is not an entity_id")
        out[stream] = dict(fields)
    return out


def points_for(stream: str, field: str, records: list[dict]) -> list[Point]:
    spec = field_spec(stream, field)
    out = []
    for rec in records:
        value = spec.extract(rec)
        if value is MISSING:
            continue
        out.append(Point(float(rec["ts"]), value))
    return out
