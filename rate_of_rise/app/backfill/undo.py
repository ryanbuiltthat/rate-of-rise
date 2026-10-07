"""Remove backfilled recorder rows: every states row whose context id carries MARKER.

The `unavailable` rows the backfill deleted are not restored. They carried no reading, and
HA's history draws the same gap without them.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path

from .recorder import CHUNK, MARKER, SUPPORTED_SCHEMAS, SchemaUnsupported, connect, schema_version


def undo(db_path: Path, since_ts: float = 0.0) -> int:
    version = schema_version(db_path)
    if version not in SUPPORTED_SCHEMAS:
        raise SchemaUnsupported(version)
    with closing(connect(db_path)) as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT state_id FROM states WHERE substr(context_id_bin, 1, 4) = ?"
            " AND last_updated_ts >= ?", (MARKER, since_ts))]
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i:i + CHUNK]
            marks = ",".join("?" * len(chunk))
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(f"UPDATE states SET old_state_id = NULL"
                             f" WHERE old_state_id IN ({marks})", chunk)
                conn.execute(f"DELETE FROM states WHERE state_id IN ({marks})", chunk)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    return len(ids)
