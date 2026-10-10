"""Prune the v2 gateway's SD store: the Prune Gateway Store button (`<base>/cmd/prune_store`).

Deletes records older than `gateway_store_prune_days` from the gateway's card, but only ones
backfill has already written to HA: the request carries the backfill cursor, and the gateway
deletes a block only when every record in it is at or below the cursor and older than the
cutoff. The newest block always stays. See
docs/superpowers/plans/2026-10-10-gateway-v2-store-prune.md.

It goes through the same feature detection as backfill, so pointed at a v1 gateway (or
anything that isn't a v2 store) it refuses without sending the POST. Every outcome is a short
message for the command-result topic; nothing here raises.
"""
from __future__ import annotations

import logging
import time

import requests

from .client import ProbeState, PruneUnsupported, StoreChanged
from .entity_map import STREAMS

log = logging.getLogger("app.backfill.prune")

MAX_ROUNDS = 50     # each round deletes at most a few blocks; this is far more than enough

_PROBE_REFUSALS = {
    ProbeState.NO_STORE: "not a v2 gateway store; nothing pruned",
    ProbeState.UNREACHABLE: "not pruned: gateway unreachable",
    ProbeState.BAD_TOKEN: "not pruned: gateway rejected gateway_store_token",
}


def prune_store(client, reconciler, days: int | None, now_fn=time.time) -> str:
    if not days:
        return "pruning not configured (gateway_store_prune_days)"
    probe = client.probe()
    if probe.state is not ProbeState.OK:
        return _PROBE_REFUSALS[probe.state]
    status = probe.status
    if not status.get("sd_ok", True):
        return "not pruned: gateway SD not mounted"
    store_id = status.get("store_id")
    if not store_id or store_id != reconciler.store_id:
        # The cursor says what has been written to HA from *this* card. Without a match it
        # says nothing about the card in the gateway now.
        return "not pruned: backfill has not read this card yet"
    through = {s: int(reconciler.cursor.get(s, 0)) for s in STREAMS}
    if not any(through.values()):
        return "nothing backfilled yet; nothing to prune"

    before = now_fn() - days * 86400
    deleted = {s: 0 for s in STREAMS}
    first: dict = {}
    try:
        for _ in range(MAX_ROUNDS):
            reply = client.prune(before, through, store_id)
            for s in STREAMS:
                deleted[s] += int((reply.get("deleted") or {}).get(s, 0) or 0)
            first = reply.get("first") or first
            if not reply.get("more"):
                break
    except PruneUnsupported:
        return "gateway firmware has no prune endpoint; update v2"
    except StoreChanged:
        return "not pruned: gateway card changed"
    except (requests.RequestException, ValueError) as exc:
        log.debug("gateway store prune failed: %s", exc)
        if any(deleted.values()):
            return (f"pruned {_counts(deleted)} block(s), then lost the gateway; "
                    "press again to finish")
        return "not pruned: gateway unreachable"

    msg = f"pruned {_counts(deleted)} block(s) older than {days} d"
    if first:
        msg += "; store now starts at " + ", ".join(
            f"{s} seq {first.get(s, 0)}" for s in STREAMS)
    log.info("gateway store: %s", msg)
    return msg


def _counts(deleted: dict[str, int]) -> str:
    return " / ".join(f"{deleted[s]} {s}" for s in STREAMS)
