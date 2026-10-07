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
* Unavailable rows break "the state in effect": an unavailable row sets the state to
  "unavailable"; only non-unavailable rows count as near matches. The first reading after
  unavailable is always inserted even if its value equals the pre-outage value.
* Compressed like HA: a reading equal to the state already in effect adds no row, unless
  that state is unreachable (unavailable), because HA itself only writes a row when the
  state changes.
* Unavailable rows within BRACKET_S (300 s) of both an existing/batch state before and a
  batch reading after are removed, clearing old_state_id references. Unavailable rows never
  include the entity's newest row.
* Numeric states are compared with floating-point tolerance to account for precision drift.
* Every inserted row's context id starts with MARKER, so undo.py can remove them all.
* Short transactions (CHUNK rows each, BEGIN IMMEDIATE with a busy timeout), because HA's
  recorder commits every second and must never wait long on us.
"""
from __future__ import annotations

import json
import logging
import math
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
BRACKET_S = 300.0
UNAVAILABLE_MARGIN_S = BRACKET_S  # 5-missed-reports node timeout; kept for Task 13/19 imports
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
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_S)) as conn:
        row = conn.execute("SELECT schema_version FROM schema_changes"
                           " ORDER BY change_id DESC LIMIT 1").fetchone()
    return int(row[0]) if row else None


def entity_unit(conn: sqlite3.Connection, metadata_id: int) -> tuple[int | None, str | None]:
    # Newest row whose state is not unavailable; fall back to newest row if none (M-10)
    row = conn.execute(
        "SELECT s.attributes_id, a.shared_attrs FROM states s"
        " LEFT JOIN state_attributes a ON a.attributes_id = s.attributes_id"
        " WHERE s.metadata_id = ? AND s.state != 'unavailable'"
        " ORDER BY s.last_updated_ts DESC LIMIT 1", (metadata_id,)).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT s.attributes_id, a.shared_attrs FROM states s"
            " LEFT JOIN state_attributes a ON a.attributes_id = s.attributes_id"
            " WHERE s.metadata_id = ? ORDER BY s.last_updated_ts DESC LIMIT 1",
            (metadata_id,)).fetchone()
    if row is None:
        return None, None
    try:
        unit = json.loads(row[1] or "{}").get("unit_of_measurement")
    except ValueError:
        unit = None
    return row[0], unit


def _same(a: str | None, b: str | None) -> bool:
    """Numeric tolerance: if both parse as finite floats, equal iff
    abs(fa - fb) <= 1e-4 * max(1.0, abs(fa))."""
    if a is None or b is None:
        return a == b
    try:
        fa = float(a)
        fb = float(b)
        if not (math.isfinite(fa) and math.isfinite(fb)):
            return False
        return abs(fa - fb) <= 1e-4 * max(1.0, abs(fa))
    except ValueError:
        return a == b


def _format(kind: str, value, native_unit: str | None, unit: str | None) -> str:
    if value is None:
        return "unknown"
    if kind == "binary":
        return "on" if value else "off"
    if kind == "text":
        return str(value)
    converted = convert(float(value), native_unit, unit)
    if not math.isfinite(converted):
        return "unknown"
    return format_state(converted)


def _plan(formatted: list[tuple[float, str]], existing: list[tuple[int, str, float]],
          prior_state: str | None, batch_ts: list[float]) -> list[tuple[float, str]]:
    """The (ts, state) pairs to insert. Walks the readings and the existing rows together, so
    'the state in effect' at each reading accounts for both. Unavailable rows break the state
    in effect (Rule 1)."""
    # All existing rows (including unavailable) for state tracking
    all_rows = [(ts, state) for _, state, ts in existing]
    # Non-unavailable rows for near-match detection
    valid = [(ts, state) for _, state, ts in existing if state != "unavailable"]

    out: list[tuple[float, str]] = []
    state_now = prior_state
    j = 0

    for ts, state in formatted:
        # Advance past rows older than ts - MATCH_TOLERANCE_S (walk ALL rows, including unavailable)
        while j < len(all_rows) and all_rows[j][0] < ts - MATCH_TOLERANCE_S:
            state_now = all_rows[j][1]
            j += 1

        # Check if HA already recorded this reading (only non-unavailable rows count)
        near = [v for v in valid[j:] if v[0] <= ts + MATCH_TOLERANCE_S]
        if near:
            state_now = near[-1][1]
            continue

        # HA writes no row for an unchanged state (use numeric tolerance for floats)
        if _same(state, state_now):
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

            # Fetch existing rows over [lo - BRACKET_S, hi + BRACKET_S] (Rule 2)
            existing = conn.execute(
                "SELECT state_id, state, last_updated_ts FROM states WHERE metadata_id = ?"
                " AND last_updated_ts BETWEEN ? AND ? ORDER BY last_updated_ts",
                (metadata_id, lo - BRACKET_S, hi + BRACKET_S)).fetchall()

            # Prior state: newest row before lo - BRACKET_S, including unavailable (Rule 1)
            prior = conn.execute(
                "SELECT state FROM states WHERE metadata_id = ? AND last_updated_ts < ?"
                " ORDER BY last_updated_ts DESC LIMIT 1",
                (metadata_id, lo - BRACKET_S)).fetchone()

            batch_ts = [ts for ts, _ in formatted]
            to_insert = _plan(formatted, existing, prior[0] if prior else None, batch_ts)

            # Determine which unavailable rows to delete (Rule 2)
            doomed: list[int] = []
            if to_insert or batch_ts:
                for sid, state, ts in existing:
                    if state != "unavailable":
                        continue

                    # Check if there's a valid state at or before tu within BRACKET_S
                    before_ok = False
                    # Check existing non-unavailable rows
                    for _, s, ets in existing:
                        if s != "unavailable" and ts - BRACKET_S <= ets <= ts:
                            before_ok = True
                            break
                    # Check batch readings
                    if not before_ok:
                        for bts in batch_ts:
                            if ts - BRACKET_S <= bts <= ts:
                                before_ok = True
                                break

                    if not before_ok:
                        continue

                    # Check if there's a batch reading after tu within BRACKET_S
                    after_ok = any(ts < bts <= ts + BRACKET_S for bts in batch_ts)

                    if before_ok and after_ok:
                        doomed.append(sid)

            # Apply Rule 3 filtering: never delete the entity's newest row (check BEFORE inserts)
            doomed_filtered: list[int] = []
            for sid in doomed:
                ts_row = conn.execute(
                    "SELECT last_updated_ts FROM states WHERE state_id = ?",
                    (sid,)).fetchone()
                if ts_row:
                    newer = conn.execute(
                        "SELECT 1 FROM states WHERE metadata_id = ? AND last_updated_ts > ?"
                        " LIMIT 1",
                        (metadata_id, ts_row[0])).fetchone()
                    if newer:
                        doomed_filtered.append(sid)

            result.inserted = [ts for ts, _ in to_insert]
            result.deleted_unavailable = len(doomed)  # Report initial count for compatibility

            if dry_run or not (to_insert or doomed_filtered):
                return result

            # Inserts run BEFORE deletes (M-3)
            self._apply(conn, metadata_id, attrs_id, to_insert, doomed_filtered)

        return result

    @staticmethod
    def _apply(conn, metadata_id, attrs_id, to_insert, doomed, mid_for_rule3=None) -> None:
        # Inserts first (M-3)
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
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

        # Then deletes (M-3) — doomed list is already filtered by Rule 3
        if doomed:
            for chunk in _chunks(doomed, CHUNK):
                conn.execute("BEGIN IMMEDIATE")
                try:
                    marks = ",".join("?" * len(chunk))
                    conn.execute(f"UPDATE states SET old_state_id = NULL"
                                 f" WHERE old_state_id IN ({marks})", chunk)
                    conn.execute(f"DELETE FROM states WHERE state_id IN ({marks})", chunk)
                    conn.execute("COMMIT")
                except Exception:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
