"""One backfill pass, and the thread that keeps running them.

A pass: for each stream, pull the records after the cursor, hold back the live edge (the last
HOLDBACK_S, which HA has probably got live anyway), drop records with no trustworthy time,
and write the batch to every destination: HA's recorder (and statistics), the stage log, and
the dataset. The cursor only moves when every destination succeeded. Every destination is
idempotent, so a failed pass is simply run again next time.

The service probes first and treats every non-store answer as normal, quiet operation:
`v1 gateway (no store)` re-probes hourly, `unreachable` every pass interval. Only a 401 is
worth a WARNING (once an hour), because it is a configuration mistake that will not fix
itself.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from .client import ProbeState
from .entity_map import STREAMS, field_spec, points_for
from .gaprows import GapFillDeferred
from .recorder import RecorderWriter, SchemaUnsupported
from .stagelog_merge import merge_stage_rows

log = logging.getLogger("app.backfill")

HOLDBACK_S = 120.0
MAX_RECORDS_PER_PASS = 20000
PASS_INTERVAL_S = 600.0
NO_STORE_RETRY_S = 3600.0
MAX_PASSES_PER_TICK = 20
BAD_TOKEN_WARN_S = 3600.0
FUTURE_TOLERANCE_S = 3600.0   # a record further ahead than this has a garbled clock

COUNT_KEYS = ("inserted_states", "shadow_states", "deleted_unavailable", "imported_stat_hours",
              "stage_log_rows", "dataset_rows", "skipped_no_time", "skipped_bad_time",
              "stat_errors")


@dataclass
class Destinations:
    recorder_db: Path | None        # None: HA's config is not mapped (local runs)
    statistics: object | None       # .backfill(entity_id, inserted_ts) -> hours
    stage_dir: Path | None
    gaps: object | None             # .fill(node_records, eco_records) -> rows


@dataclass
class PassResult:
    more: bool = False
    blocked: str | None = None
    ok: bool = True
    deferred: bool = False
    skipped_sd: bool = False        # the gateway's card is not mounted: nothing to read
    counts: Counter = field(default_factory=Counter)


class Reconciler:
    def __init__(self, client, dest: Destinations, entity_map: dict, shadow_map: dict,
                 cursor_path: Path, now_fn=time.time, writer_factory=RecorderWriter):
        self._client = client
        self._dest = dest
        self._map = entity_map
        self._shadow = shadow_map
        self._path = cursor_path
        self._now = now_fn
        self._writer_factory = writer_factory
        self._store_id: str | None = None
        self._cursor = self._load()
        self._skips_noted: set[tuple[str, str]] = set()
        self._behind_noted: set[tuple[str, int]] = set()

    @property
    def cursor(self) -> dict[str, int]:
        return dict(self._cursor)

    @property
    def store_id(self) -> str | None:
        """The card the cursor belongs to (None until backfill has seen a v2 store)."""
        return self._store_id

    def _load(self) -> dict[str, int]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            cursor = {s: int(data.get(s, 0)) for s in STREAMS}
        except (FileNotFoundError, ValueError, OSError, TypeError, AttributeError):
            return {s: 0 for s in STREAMS}
        sid = data.get("store_id")
        self._store_id = sid if isinstance(sid, str) and sid else None
        return cursor

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**self._cursor, "store_id": self._store_id}),
                       encoding="utf-8")
        tmp.replace(self._path)

    def _adopt_store_id(self, status: dict) -> None:
        """The card's identity. A different one is a replaced or reformatted card whose seq
        restarted: re-read it from the start (every destination is idempotent). The first id
        seen is adopted as is."""
        sid = status.get("store_id")
        if not isinstance(sid, str) or not sid or sid == self._store_id:
            return
        if self._store_id is not None:
            log.warning("gateway card changed; re-reading it from the start (store id %s -> %s)."
                        " Writes are idempotent, so nothing is duplicated.", self._store_id, sid)
            self._cursor = {s: 0 for s in STREAMS}
            self._behind_noted.clear()
        self._store_id = sid
        self._save()

    def run_pass(self, status: dict) -> PassResult:
        result = PassResult()
        if status.get("sd_ok") is False:
            log.debug("backfill: the gateway's SD card is not mounted; nothing to read")
            result.skipped_sd = True
            return result
        self._adopt_store_id(status)
        batches: dict[str, list[dict]] = {}
        consumed: dict[str, int] = {}
        streams = status.get("streams") or {}
        for s in STREAMS:
            last = int((streams.get(s) or {}).get("last", 0) or 0)
            cur = self._cursor.get(s, 0)
            if last < cur:
                # Same card (or a gateway that reports no id): a low seq is not a new card, so
                # the cursor stays and the stream waits. Resetting here could only re-read.
                if (s, last) not in self._behind_noted:
                    self._behind_noted.add((s, last))
                    log.warning("gateway store reports %s seq %d below cursor %d without a card "
                                "change; leaving the cursor alone", s, last, cur)
                batches[s], consumed[s] = [], cur
                continue
            if last <= cur:
                batches[s], consumed[s] = [], cur
                continue
            recs = self._client.records(s, cur, MAX_RECORDS_PER_PASS)
            if len(recs) >= MAX_RECORDS_PER_PASS:
                result.more = True
            now = self._now()
            edge = now - HOLDBACK_S
            usable, upto = [], cur
            for r in recs:
                ts = r.get("ts")
                if isinstance(ts, bool) or not isinstance(ts, (int, float))                         or ts != ts or ts > now + FUTURE_TOLERANCE_S:
                    # Missing, non-numeric or far-future time (a garbled RTC read): it can
                    # never become live, so consume it rather than stall the stream.
                    upto = r["seq"]
                    result.counts["skipped_bad_time"] += 1
                    continue
                if ts > edge:
                    break
                upto = r["seq"]
                if r.get("ts_src") == "none":
                    result.counts["skipped_no_time"] += 1
                    continue
                usable.append(r)
            batches[s], consumed[s] = usable, upto

        try:
            self._write_recorder(batches, result)
        except Exception:
            log.exception("backfill: recorder write failed; the batch is retried next pass")
            result.ok = False
        if result.ok and self._dest.stage_dir is not None and batches.get("node"):
            try:
                result.counts["stage_log_rows"] += merge_stage_rows(
                    self._dest.stage_dir, [(r["ts"], r.get("stage_ft")) for r in batches["node"]],
                    self._now())
            except Exception:
                log.exception("backfill: stage log merge failed; retried next pass")
                result.ok = False
        if result.ok and self._dest.gaps is not None and (batches.get("node") or batches.get("ecowitt")):
            try:
                result.counts["dataset_rows"] += self._dest.gaps.fill(
                    batches.get("node", []), batches.get("ecowitt", []))
            except GapFillDeferred:
                log.debug("backfill: dataset gap rows deferred until the first live rain poll")
                result.deferred = True
                result.ok = False
            except Exception:
                log.exception("backfill: dataset gap rows failed; retried next pass")
                result.ok = False

        if not result.ok:
            result.more = False
            return result
        if any(consumed[s] != self._cursor.get(s, 0) for s in STREAMS) or not self._path.exists():
            self._cursor.update(consumed)
            self._save()
        return result

    def _write_recorder(self, batches: dict, result: PassResult) -> None:
        if self._dest.recorder_db is None or not (self._map or self._shadow):
            return
        if not any(batches.values()):
            return
        try:
            writer = self._writer_factory(self._dest.recorder_db)
        except SchemaUnsupported as exc:
            result.blocked = f"blocked: recorder schema {exc.version}"
            return
        for entities, dry in ((self._map, False), (self._shadow, True)):
            for stream, fields_ in entities.items():
                recs = batches.get(stream) or []
                if not recs:
                    continue
                for fld, entity in fields_.items():
                    spec = field_spec(stream, fld)
                    res = writer.write(entity, spec.kind, spec.unit,
                                       points_for(stream, fld, recs), dry_run=dry,
                                       resolution=spec.resolution)
                    if res.skipped:
                        self._note_skip(entity, res.skipped)
                        continue
                    if dry:
                        result.counts["shadow_states"] += len(res.inserted)
                        if res.inserted or res.deleted_unavailable:
                            log.info("backfill shadow: would insert %d row(s) into %s and "
                                     "remove %d unavailable row(s)", len(res.inserted), entity,
                                     res.deleted_unavailable)
                        continue
                    result.counts["inserted_states"] += len(res.inserted)
                    result.counts["deleted_unavailable"] += res.deleted_unavailable
                    if self._dest.statistics is not None and res.inserted:
                        try:
                            result.counts["imported_stat_hours"] += \
                                self._dest.statistics.backfill(entity, res.inserted)
                        except Exception as exc:
                            # Statistics are the coarse copy; the states are already in. Losing
                            # an hour's re-import is not worth re-running the whole batch.
                            result.counts["stat_errors"] += 1
                            log.warning("backfill: statistics import for %s failed: %s",
                                        entity, exc)

    def _note_skip(self, entity: str, reason: str) -> None:
        if (entity, reason) not in self._skips_noted:
            self._skips_noted.add((entity, reason))
            log.warning("backfill: skipping %s: %s", entity, reason)


class BackfillService:
    def __init__(self, client, reconciler: Reconciler, publish, now_fn=time.time):
        self._client = client
        self._rec = reconciler
        self._publish = publish
        self._now = now_fn
        self._stop = threading.Event()
        self._last_bad_token_warn: float | None = None

    @property
    def client(self):
        return self._client

    @property
    def reconciler(self) -> Reconciler:
        return self._rec

    def start(self) -> None:
        threading.Thread(target=self._run, name="backfill", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        delay = 0.0
        while not self._stop.wait(delay):
            try:
                delay = self.tick()
            except Exception:
                log.exception("backfill tick failed")
                self._status("error: tick failed (see log)")
                delay = PASS_INTERVAL_S

    def tick(self) -> float:
        probe = self._client.probe()
        if probe.state is ProbeState.NO_STORE:
            self._status("v1 gateway (no store)")
            return NO_STORE_RETRY_S
        if probe.state is ProbeState.UNREACHABLE:
            self._status("unreachable")
            return PASS_INTERVAL_S
        if probe.state is ProbeState.BAD_TOKEN:
            now = self._now()
            if self._last_bad_token_warn is None or now - self._last_bad_token_warn >= BAD_TOKEN_WARN_S:
                log.warning("gateway store rejected gateway_store_token (HTTP 401); backfill is "
                            "paused until it matches the gateway's creek_store_token")
                self._last_bad_token_warn = now
            self._status("error: bad token")
            return PASS_INTERVAL_S

        streams = probe.status.get("streams") or {}
        pending = sum(max(0, int((streams.get(s) or {}).get("last", 0) or 0)
                          - self._rec.cursor.get(s, 0)) for s in STREAMS)
        if pending:
            self._status(f"backfilling {pending}")
        totals: Counter = Counter()
        state = "idle"
        try:
            for _ in range(MAX_PASSES_PER_TICK):
                res = self._rec.run_pass(probe.status)
                totals.update(res.counts)
                if res.skipped_sd:
                    state = "gateway SD not mounted"
                    break
                if res.blocked:
                    state = res.blocked
                if res.deferred:
                    state = "waiting for live poll"
                    break
                if not res.ok:
                    state = "error: a destination failed (see log)"
                    break
                if not res.more:
                    break
        except requests.RequestException as exc:
            log.debug("gateway store dropped mid-pass: %s", exc)
            state = "unreachable"
        self._status(state, totals)
        return PASS_INTERVAL_S

    def _status(self, state: str, counts: Counter | None = None) -> None:
        payload = {"state": state,
                   "last_run": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   **{f"cursor_{s}": self._rec.cursor.get(s, 0) for s in STREAMS},
                   **{k: 0 for k in COUNT_KEYS},
                   **{k: int(v) for k, v in (counts or {}).items()}}
        try:
            self._publish("status/backfill", payload)
        except Exception:
            log.debug("could not publish backfill status", exc_info=True)
