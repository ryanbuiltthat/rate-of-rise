"""Model registry — the source of truth for which artifact is active.

Wraps `/data/models/registry.json`. Holds the **active** model, an optional
**candidate** produced by the most recent retrain, and a **history** of retired
actives so a promotion can be rolled back. Pointer/metric bookkeeping lives here;
loading the artifact a version names is `model.py`'s job (xgboost JSON plus a
`.meta.json` sidecar — see `train.py` on why not a pickle). Keeping the two
separate means promote/rollback are testable without a broker, HA, or a trained
model.

"No ML model" is a first-class state here, not the absence of one: `active: null`
means `model.py` answers with its threshold estimate, and a history entry whose
version is null restores exactly that. Without it the *first* promotion — the one
with the least evidence behind it — would be the only one that could never be
undone, which is precisely backwards.

Schema (registry.json):
    {
      "active":    {"version", "metrics", "promoted_at"} | null,
      "candidate": {"version", "metrics", "created_at"}   | null,
      "history":   [ {"version", "metrics", "promoted_at", "retired_at"}, ... ],
      "event_count": int,
      "updated_at": iso8601
    }

A history entry's "version" is null for the threshold estimate; `snapshot()`
renders it as THRESHOLD_LABEL so the dashboard shows a word rather than a blank.
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


# How `snapshot()` renders the "no ML model" state in the history list.
THRESHOLD_LABEL = "threshold"

# The metric that marks a candidate as having actually been scored. `train._skill_metrics`
# emits roc_auc (with hit rate and false-alarm rate) only when the held-out split had both
# classes in it; when it came back empty or single-class — the normal outcome on a short
# record, since flood positives are rare — it emits a `note` explaining that and nothing
# else. So "roc_auc is present" is exactly "a held-out split could tell us whether this
# model is any good".
#
# Checked by key rather than by importing train: registry.py stays free of xgboost and
# sklearn, so promote/rollback remain testable without them.
VALIDATION_METRIC = "roc_auc"


def is_validated(metrics: dict | None) -> bool:
    """Whether a candidate's metrics show it was scored against a held-out split *and*
    caught something there.

    An AUC alone is not enough. gbm-20260924T030456Z had roc_auc 0.608 and hit_rate 0.0 —
    it missed all 49 held-out positives — and "roc_auc is present" called that validated,
    so the dashboard said so while the model was driving Tier 3/4. A model that flags none
    of the rises it is tested on has been scored, and failed.
    """
    if not metrics or metrics.get(VALIDATION_METRIC) is None:
        return False
    return (metrics.get("hit_rate") or 0.0) > 0.0


def _validation_caveat(metrics: dict) -> str:
    """Why a model is not validated, in words the operator can act on."""
    if metrics.get(VALIDATION_METRIC) is None:
        return metrics.get("note") or f"no {VALIDATION_METRIC} in its metrics"
    return (f"it caught none of the {metrics.get('test_positives', '?')} held-out "
            f"positives it was tested on (hit rate 0)")


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

    @property
    def candidate_version(self) -> str | None:
        candidate = self._data.get("candidate")
        return candidate.get("version") if candidate else None

    def snapshot(self) -> dict:
        """Compact, JSON-serializable view for the `creek/status/registry` topic."""
        active = self._data.get("active") or {}
        candidate = self._data.get("candidate") or {}
        return {
            "active_version": active.get("version"),
            "active_metrics": active.get("metrics", {}),
            "candidate_version": candidate.get("version"),
            "candidate_metrics": candidate.get("metrics", {}),
            "history": [h.get("version") or THRESHOLD_LABEL
                        for h in self._data.get("history", [])],
            "event_count": self.event_count,
            # Published as an attribute of the Active Model sensor, so "this model was
            # never scored" stays on the dashboard for as long as it is active. The
            # command result that said so at promote time scrolls away; the model does
            # not. True with nothing active — the threshold estimate makes no claim a
            # held-out split could check, so flagging it would be noise.
            "active_validated": not self.warning(),
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

        The outgoing state is always pushed to the front of `history` — including the
        "no ML model" state, recorded as a null version — so every promotion has
        somewhere to roll back to. Returns the newly-active version.

        Promoting an unvalidated candidate warns rather than refuses. The warning is
        worth making loud: a promoted model's probability alone raises Tier 3 at
        WARNING_PROBABILITY and Tier 4 at EMERGENCY_PROBABILITY (`tiers.py`), so a model
        whose held-out split could not score it is driving the alarm on nobody's say-so.
        That is not hypothetical — the first candidate this add-on produced scored
        `test_positives: 0` on 19 test rows and raised a Warning on the next inference.
        But refusing would put the operator's own judgement behind a gate they cannot
        open, so the call stays theirs; `warning()` carries the caveat, and
        `snapshot()["active_validated"]` keeps it visible for as long as the model is
        active rather than only at the moment of the press.
        """
        candidate = self._data.get("candidate")
        if not candidate:
            raise RegistryError("no candidate to promote")
        metrics = candidate.get("metrics", {})

        # dict(... or {"version": None}) is the whole fix for "no history to roll back
        # to": before, an absent active recorded nothing, so the first promotion could
        # never be undone.
        outgoing = dict(self._data.get("active") or {"version": None})
        outgoing["retired_at"] = _now()
        self._data["history"].insert(0, outgoing)
        self._data["active"] = {
            "version": candidate["version"],
            "metrics": metrics,
            "promoted_at": _now(),
        }
        self._data["candidate"] = None
        self._save()
        version = self._data["active"]["version"]
        caveat = self.warning()
        if caveat:
            log.warning("Promoted %s to active — %s", version, caveat)
        else:
            log.info("Promoted %s to active", version)
        return version

    def warning(self) -> str | None:
        """What is worth telling the operator about the active model, or None.

        Only one caveat today: the active model was never scored. Phrased as a whole
        sentence because it is published verbatim — a command result the operator reads
        once, and a sensor attribute they can read at any time afterwards.
        """
        active = self._data.get("active")
        if not active or is_validated(active.get("metrics")):
            return None
        note = _validation_caveat(active.get("metrics") or {})
        return (f"{active['version']} was never validated ({note}); its probability alone "
                f"can raise Tier 3/4 if ml_drives_alerts is on, so watch the first tiers "
                f"it produces")

    def rollback(self) -> str | None:
        """Undo a promote: restore the previous active, or the threshold estimate.

        The model being demoted is kept as the candidate so it isn't lost and can be
        re-promoted. Returns the restored version, or None when the restored state is
        "no ML model" — `model.py` then answers with its threshold estimate.
        """
        history = self._data.get("history", [])
        active = self._data.get("active")
        if history:
            restored = history.pop(0)
        elif active:
            # A model is active but nothing was recorded behind it — the state every
            # registry written before 0.20.2 is left in by its first promotion, since
            # promote() then recorded an outgoing active only when one already existed.
            # That promotion came from the threshold estimate, so that is where undoing
            # it goes. Without this the operator's only way back is hand-editing
            # registry.json on the HA host, with a model they distrust still driving the
            # alarm in the meantime.
            log.info("No recorded predecessor for %s — rolling back to the threshold "
                     "estimate", active.get("version"))
            restored = {"version": None}
        else:
            raise RegistryError("nothing to roll back: no active model and no history")

        if active:
            demoted = dict(active)
            demoted.pop("promoted_at", None)
            self._data["candidate"] = {
                "version": demoted["version"],
                "metrics": demoted.get("metrics", {}),
                "created_at": _now(),
            }
        version = restored.get("version")
        self._data["active"] = None if version is None else {
            "version": version,
            "metrics": restored.get("metrics", {}),
            "promoted_at": _now(),
        }
        self._save()
        log.info("Rolled back to %s", version or "the threshold estimate")
        return version
