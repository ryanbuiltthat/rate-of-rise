"""Backfilled states, written straight into Home Assistant's recorder database.

Nothing in Home Assistant accepts a state with a past timestamp: ESPHome, MQTT and the REST
API all stamp "now". So the readings the gateway held through an outage can only reach an
entity's history as inserted rows. This does that, carefully:

* Only on a recorder schema checked against this code (SUPPORTED_SCHEMAS). Any other
  version is refused before anything is read or written.
* Never creates an entity: it must already be in `states_meta`. Attributes are reused from
  the entity's newest row; these sensors' attributes do not change.
* Numbers go in the entity's display unit, read from those attributes (see units.py).
* Idempotent: a reading within MATCH_TOLERANCE_S of an existing row is already there.
* Compressed like HA: a reading equal to the state already in effect adds no row, because
  HA itself only writes a row when the state changes.
* `unavailable` rows HA wrote while the gateway was disconnected, within
  UNAVAILABLE_MARGIN_S of what was filled, are removed after clearing every old_state_id
  that points at them.
* Every inserted row's context id starts with MARKER, so undo.py can remove them all.
* Short transactions (CHUNK rows each, BEGIN IMMEDIATE with a busy timeout), because HA's
  recorder commits every second and must never wait long on us.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from .entity_map import Point
from .units import UnitMismatch, convert, format_state

log = logging.getLogger("app.backfill.recorder")

SUPPORTED_SCHEMAS = frozenset({53})
MARKER = bytes.fromhex("CB0F11ED")
MATCH_TOLERANCE_S = 20.0
UNAVAILABLE_MARGIN_S = 120.0
CHUNK = 500
BUSY_TIMEOUT_S = 30.0


class SchemaUnsupported(RuntimeError):
    def __init__(self, version: int | None):
        super().__init__(f"recorder schema {version} is not supported "
                         f"(supported: {sorted(SUPPORTED_SCHEMAS)})")
        self.version = version


@dataclass
class WriteResult:
    entity_id: str
    inserted: list[float] = field(default_factory=list)
    deleted_unavailable: int = 0
    skipped: str | None = None


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_S, isolation_level=None)
    conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_S * 1000)}")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def schema_version(db_path: Path) -> int | None:
    with closing(sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True,
                                 timeout=BUSY_TIMEOUT_S)) as conn:
        row = conn.execute("SELECT schema_version FROM schema_changes"
                           " ORDER BY change_id DESC LIMIT 1").fetchone()
    return int(row[0]) if row else None


def entity_unit(conn: sqlite3.Connection, metadata_id: int) -> tuple[int | None, str | None]:
    row = conn.execute(
        "SELECT s.attributes_id, a.shared_attrs FROM states s"
        " LEFT JOIN state_attributes a ON a.attributes_id = s.attributes_id"
        " WHERE s.metadata_id = ? AND s.attributes_id IS NOT NULL"
        " ORDER BY s.last_updated_ts DESC LIMIT 1", (metadata_id,)).fetchone()
    if row is None:
        return None, None
    try:
        unit = json.loads(row[1] or "{}").get("unit_of_measurement")
    except ValueError:
        unit = None
    return row[0], unit


def _format(kind: str, value, native_unit: str | None, unit: str | None) -> str:
    if kind == "binary":
        return "on" if value else "off"
    if kind == "text":
        return str(value)
    if value is None:
        return "unknown"
    return format_state(convert(float(value), native_unit, unit))


def _plan(formatted: list[tuple[float, str]], existing: list[tuple[int, str, float]],
          prior_state: str | None) -> list[tuple[float, str]]:
    """The (ts, state) pairs to insert. Walks the readings and the existing rows together, so
    'the state in effect' at each reading accounts for both."""
    valid = [(ts, state) for _, state, ts in existing if state != "unavailable"]
    out: list[tuple[float, str]] = []
    state_now = prior_state
    j = 0
    for ts, state in formatted:
        while j < len(valid) and valid[j][0] < ts - MATCH_TOLERANCE_S:
            state_now = valid[j][1]
            j += 1
        near = [v for v in valid[j:] if v[0] <= ts + MATCH_TOLERANCE_S]
        if near:                      # HA already recorded this reading
            state_now = near[-1][1]
            continue
        if state == state_now:        # HA writes no row for an unchanged state
            continue
        out.append((ts, state))
        state_now = state
    return out


def _chunks(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


class RecorderWriter:
    def __init__(self, db_path: Path):
        self._db = Path(db_path)
        self.version = schema_version(self._db)
        if self.version not in SUPPORTED_SCHEMAS:
            raise SchemaUnsupported(self.version)

    def write(self, entity_id: str, kind: str, native_unit: str | None, points: list[Point],
              dry_run: bool = False) -> WriteResult:
        result = WriteResult(entity_id)
        if not points:
            return result
        with closing(connect(self._db)) as conn:
            meta = conn.execute("SELECT metadata_id FROM states_meta WHERE entity_id = ?",
                                (entity_id,)).fetchone()
            if meta is None:
                result.skipped = "entity not in recorder"
                return result
            metadata_id = meta[0]
            attrs_id, unit = entity_unit(conn, metadata_id)
            try:
                formatted = [(p.ts, _format(kind, p.value, native_unit, unit))
                             for p in sorted(points, key=lambda p: p.ts)]
            except UnitMismatch as exc:
                result.skipped = str(exc)
                return result
            lo, hi = formatted[0][0], formatted[-1][0]
            existing = conn.execute(
                "SELECT state_id, state, last_updated_ts FROM states WHERE metadata_id = ?"
                " AND last_updated_ts BETWEEN ? AND ? ORDER BY last_updated_ts",
                (metadata_id, lo - UNAVAILABLE_MARGIN_S, hi + UNAVAILABLE_MARGIN_S)).fetchall()
            prior = conn.execute(
                "SELECT state FROM states WHERE metadata_id = ? AND last_updated_ts < ?"
                " AND state != 'unavailable' ORDER BY last_updated_ts DESC LIMIT 1",
                (metadata_id, lo - UNAVAILABLE_MARGIN_S)).fetchone()
            to_insert = _plan(formatted, existing, prior[0] if prior else None)
            doomed: list[int] = []
            if to_insert:
                first = to_insert[0][0] - UNAVAILABLE_MARGIN_S
                last = to_insert[-1][0] + UNAVAILABLE_MARGIN_S
                doomed = [sid for sid, state, ts in existing
                          if state == "unavailable" and first <= ts <= last]
            result.inserted = [ts for ts, _ in to_insert]
            result.deleted_unavailable = len(doomed)
            if dry_run or not (to_insert or doomed):
                return result
            self._apply(conn, metadata_id, attrs_id, to_insert, doomed)
        return result

    @staticmethod
    def _apply(conn, metadata_id, attrs_id, to_insert, doomed) -> None:
        if doomed:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for chunk in _chunks(doomed, CHUNK):
                    marks = ",".join("?" * len(chunk))
                    conn.execute(f"UPDATE states SET old_state_id = NULL"
                                 f" WHERE old_state_id IN ({marks})", chunk)
                    conn.execute(f"DELETE FROM states WHERE state_id IN ({marks})", chunk)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        for chunk in _chunks(to_insert, CHUNK):
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.executemany(
                    "INSERT INTO states (state, last_updated_ts, old_state_id, attributes_id,"
                    " origin_idx, context_id_bin, metadata_id) VALUES (?, ?, NULL, ?, 0, ?, ?)",
                    [(state, ts, attrs_id, MARKER + os.urandom(12), metadata_id)
                     for ts, state in chunk])
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
