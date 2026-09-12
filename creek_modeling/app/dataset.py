"""Persistent dataset (spec §4 nightly batch, §7 Phase 3).

Two tiers, because the fast loop and the analysis have opposite needs:

  * **Fast loop** appends one row to a day part file (`datasets/parts/YYYY-MM-DD.jsonl`).
    A JSONL append is a single small write — no read, no rewrite.
  * **Nightly batch** consolidates every completed part into `datasets/dataset.parquet`
    and deletes it, so the columnar file the modelling reads stays a single file.

The previous version appended straight to Parquet, which has no cheap append: each fast
loop read the whole dataset, concatenated one row, and rewrote it. Measured on this schema
that is ~700 ms per append after a year of data, but the real cost is write amplification —
the entire dataset rewritten 288 times a day, forever, on a mini-PC SSD.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .features import FeatureRow

log = logging.getLogger("app.dataset")


class DatasetWriter:
    def __init__(self, data_dir: Path):
        self._parquet = data_dir / "datasets" / "dataset.parquet"
        self._parts = data_dir / "datasets" / "parts"
        self._parts.mkdir(parents=True, exist_ok=True)

    # --- fast loop ------------------------------------------------------------------
    def _part_path(self, ts: float) -> Path:
        day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        return self._parts / f"{day}.jsonl"

    def append_row(self, row: FeatureRow) -> None:
        """Append one row to today's part file."""
        try:
            with self._part_path(row.ts).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row.as_dict()) + "\n")
        except OSError as exc:
            log.error("could not append feature row: %s", exc)

    # --- nightly batch --------------------------------------------------------------
    def pending_parts(self, now: float | None = None) -> list[Path]:
        """Completed part files — today's stays open so the loop keeps appending to it."""
        stamp = now if now is not None else datetime.now(timezone.utc).timestamp()
        today = self._part_path(stamp).name
        return sorted(p for p in self._parts.glob("*.jsonl") if p.name != today)

    def consolidate(self, now: float | None = None) -> int:
        """Fold completed parts into the Parquet dataset. Returns the new row count."""
        parts = self.pending_parts(now)
        if not parts:
            return self.row_count()

        frames = []
        for path in parts:
            rows = self._read_part(path)
            if rows:
                frames.append(pd.DataFrame(rows))
        if not frames:
            for path in parts:      # nothing salvageable; don't re-read them forever
                path.unlink(missing_ok=True)
            return self.row_count()

        merged = pd.concat(frames, ignore_index=True)
        if self._parquet.exists():
            merged = pd.concat([pd.read_parquet(self._parquet), merged], ignore_index=True)
        # Sort and de-duplicate so a replayed part or a clock step cannot double-count.
        merged = merged.sort_values("ts").drop_duplicates(subset=["ts"], keep="last")

        tmp = self._parquet.with_suffix(".tmp")
        merged.to_parquet(tmp, index=False)
        tmp.replace(self._parquet)      # atomic: a crash mid-write cannot truncate it
        for path in parts:
            path.unlink(missing_ok=True)

        log.info("Consolidated %d part file(s); dataset now %d rows", len(parts), len(merged))
        return self.row_count()

    @staticmethod
    def _read_part(path: Path) -> list[dict]:
        rows = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    # A torn final line from an unclean shutdown — skip it, keep the rest.
                    log.warning("skipping malformed row in %s", path.name)
        except OSError as exc:
            log.error("could not read %s: %s", path.name, exc)
        return rows

    # --- reads ----------------------------------------------------------------------
    def row_count(self) -> int:
        """Rows in the consolidated dataset plus everything not yet folded in."""
        total = 0
        if self._parquet.exists():
            total += len(pd.read_parquet(self._parquet, columns=["ts"]))
        for path in self._parts.glob("*.jsonl"):
            total += len(self._read_part(path))
        return total

    def frame(self, columns: list[str] | None = None) -> pd.DataFrame:
        """The whole dataset — consolidated Parquet plus unconsolidated parts."""
        frames = []
        if self._parquet.exists():
            frames.append(pd.read_parquet(self._parquet))
        part_rows = [r for p in sorted(self._parts.glob("*.jsonl"))
                     for r in self._read_part(p)]
        if part_rows:
            frames.append(pd.DataFrame(part_rows))
        if not frames:
            return pd.DataFrame(columns=columns or ["ts"])

        df = pd.concat(frames, ignore_index=True).sort_values("ts")
        df = df.drop_duplicates(subset=["ts"], keep="last").reset_index(drop=True)
        if columns:
            for c in columns:
                if c not in df.columns:
                    df[c] = None
            df = df[columns]
        return df
