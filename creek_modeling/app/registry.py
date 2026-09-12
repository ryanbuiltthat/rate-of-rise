"""Model registry — the source of truth for which artifact is active.

Wraps `/data/models/registry.json`. Holds the **active** model, an optional
**candidate** produced by the most recent retrain, and a **history** of retired
actives so a promotion can be rolled back. Pointer/metric bookkeeping lives here
now; loading the actual `.pkl` artifact stays a Phase-4 concern (see
`model.py`). Keeping this separate means promote/rollback are testable without a
broker, HA, or a trained model.

Schema (registry.json):
    {
      "active":    {"version", "metrics", "promoted_at"} | null,
      "candidate": {"version", "metrics", "created_at"}   | null,
      "history":   [ {"version", "metrics", "promoted_at", "retired_at"}, ... ],
      "event_count": int,
      "updated_at": iso8601
    }
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("app.registry")


def _now() -> str:
    # tz-aware to match the service's timestamp convention (see __main__._now_iso).
    return datetime.now().astimezone().isoformat(timespec="seconds")


class RegistryError(RuntimeError):
    """Raised when a promote/rollback is requested but the state doesn't allow it."""


class ModelRegistry:
    def __init__(self, data_dir: Path):
        self._path = data_dir / "models" / "registry.json"
        self._data = self._load()

    # --- persistence -----------------------------------------------------
    def _load(self) -> dict:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {}
        except (ValueError, OSError) as exc:  # corrupt/unreadable — start clean, don't crash
            log.warning("registry.json unreadable (%s); starting empty", exc)
            raw = {}
        return {
            "active": raw.get("active"),
            "candidate": raw.get("candidate"),
            "history": list(raw.get("history", [])),
            "event_count": int(raw.get("event_count", 0)),
            "updated_at": raw.get("updated_at"),
        }

    def _save(self) -> None:
        self._data["updated_at"] = _now()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        log.debug("registry saved: %s", self.snapshot())

    # --- reads -----------------------------------------------------------
    @property
    def event_count(self) -> int:
        return int(self._data.get("event_count", 0))

    @property
    def active_version(self) -> str | None:
        active = self._data.get("active")
        return active.get("version") if active else None

    def snapshot(self) -> dict:
        """Compact, JSON-serializable view for the `creek/status/registry` topic."""
        active = self._data.get("active") or {}
        candidate = self._data.get("candidate") or {}
        return {
            "active_version": active.get("version"),
            "active_metrics": active.get("metrics", {}),
            "candidate_version": candidate.get("version"),
            "candidate_metrics": candidate.get("metrics", {}),
            "history": [h.get("version") for h in self._data.get("history", [])],
            "event_count": self.event_count,
        }

    # --- writes ----------------------------------------------------------
    def set_event_count(self, n: int) -> None:
        self._data["event_count"] = int(n)
        self._save()

    def set_candidate(self, version: str, metrics: dict | None = None) -> None:
        """Record the output of a retrain as the promotable candidate."""
        self._data["candidate"] = {
            "version": version,
            "metrics": metrics or {},
            "created_at": _now(),
        }
        self._save()

    def promote(self) -> str:
        """Make the candidate the active model.

        The outgoing active (if any) is pushed to the front of `history` so it can
        be restored. Returns the newly-active version.
        """
        candidate = self._data.get("candidate")
        if not candidate:
            raise RegistryError("no candidate to promote")
        outgoing = self._data.get("active")
        if outgoing:
            outgoing = dict(outgoing)
            outgoing["retired_at"] = _now()
            self._data["history"].insert(0, outgoing)
        self._data["active"] = {
            "version": candidate["version"],
            "metrics": candidate.get("metrics", {}),
            "promoted_at": _now(),
        }
        self._data["candidate"] = None
        self._save()
        log.info("Promoted %s to active", self._data["active"]["version"])
        return self._data["active"]["version"]

    def rollback(self) -> str:
        """Restore the most recently retired model as active (undo a promote).

        The model being demoted is kept as the candidate so it isn't lost and can
        be re-promoted. Returns the restored active version.
        """
        history = self._data.get("history", [])
        if not history:
            raise RegistryError("no history to roll back to")
        restored = history.pop(0)
        demoted = self._data.get("active")
        if demoted:
            demoted = dict(demoted)
            demoted.pop("promoted_at", None)
            self._data["candidate"] = {
                "version": demoted["version"],
                "metrics": demoted.get("metrics", {}),
                "created_at": _now(),
            }
        self._data["active"] = {
            "version": restored["version"],
            "metrics": restored.get("metrics", {}),
            "promoted_at": _now(),
        }
        self._save()
        log.info("Rolled back to %s", self._data["active"]["version"])
        return self._data["active"]["version"]
