"""Dataset rows for the time the add-on could not see.

When the HA host is down the add-on is down with it, so the fast loop writes no rows. When
HA Core alone is down the add-on keeps running but every HA read fails, so it writes "blind"
rows with no stage and no on-site rain. Either way the gateway's records cover the gap.

This builds the missing rows and re-issues the blind ones (same ts; the dataset keeps the
last row per ts), using the same feature code as the live loop where it can:
stage_history_features for the stage history, the same rate-of-rise window rule, the same
soil/ponding rule, FeatureBuilder._rain_on_snow. On-site rain comes from the GW3000B's
yearly counter, the way rain.py uses HA's copy of it. Sources that keep history are
re-fetched (history_between); forecasts stay empty, because a forecast as it was issued
cannot be fetched afterwards. Every row it writes carries backfilled=True.

It also corrects the live on-site rain accumulator for the gap. Without that, the 24 h and
72 h totals and the API index read short for days after an outage, and in the add-on-
restarted-within-the-hour case one lump of rain lands at restart time.

Key rules for gap rows:
- rate_of_rise_sample_count increments across consecutive rows with valid rates, capped at
  RATE_OF_RISE_SAMPLE_CAP. Re-issued blind rows carry over the count from their immediate
  predecessor.
- Rain windows older than the live ring's horizon (time - RETAIN_S) are None, even if Ecowitt
  records cover them. This expresses uncertainty past the point where live data can anchor them.
- Node records carry forward across passes within NODE_CONTEXT_S of the max ts, so that rate
  calculations have the full history available. The de-duplicated context + current batch is
  used for lookups; only the current batch defines the gap bounds.
- Backfill defers until the live rain accumulator has polled once, so that its correction can
  anchor to a known baseline (anchor_ts). The `GapFillDeferred` exception signals a retry.
"""
from __future__ import annotations

import bisect
import logging
import time
from dataclasses import fields
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ..features import (PONDING_SATURATION_PCT, RATE_OF_RISE_SAMPLE_CAP, RATE_WINDOW_SLACK_S,
                        FeatureBuilder, FeatureRow, stage_history_features)
from ..sources.accumulator import RETAIN_S, WINDOWS_H
from ..sources.apindex import DEFAULT_K

log = logging.getLogger("app.backfill.gaprows")

NODE_FRESH_S = 6 * 60.0       # a node record this recent counts as the link being up
ECO_FRESH_S = 5 * 60.0
STAGE_CONTEXT_S = 6 * 3600.0  # stage_history_features looks back 6 h
NODE_CONTEXT_S = 15 * 60.0    # node records carry forward within this window of the max ts
COUNTER_MAX_GAP_S = 3600.0    # as rain.py: a counter delta across a longer gap cannot be placed
FIELD_NAMES = [f.name for f in fields(FeatureRow)]


class GapFillDeferred(Exception):
    """Backfill cannot proceed because the live rain accumulator has not polled yet.

    The correction anchors to the accumulator's baseline (anchor_ts). Until the live path
    has seen at least one update, that baseline does not exist.
    """
    pass


def missing_slots(existing_ts, start: float, end: float, interval: float) -> list[float]:
    """Fast-loop slots in [start, end] with no dataset row near them."""
    ts = sorted(t for t in existing_ts if start - interval <= t <= end + interval)
    anchors = [start - interval] + ts + [end + interval]
    out = []
    for a, b in zip(anchors, anchors[1:]):
        t = a + interval
        while t < b - interval / 2:
            if start <= t <= end:
                out.append(round(t, 1))
            t += interval
    return out


def eco_increments(eco: list[dict]) -> list[tuple[float, float]]:
    """Rain between consecutive Ecowitt records, from the yearly counter."""
    out, prev = [], None
    for r in eco:
        total = r.get("rain_year_in")
        if total is None:
            continue
        if prev is not None and 0 < r["ts"] - prev[0] <= COUNTER_MAX_GAP_S and total > prev[1]:
            out.append((r["ts"], round(total - prev[1], 4)))
        prev = (r["ts"], total)
    return out


def _window_sum(incs, t: float, hours: int) -> float:
    return round(sum(i for ts, i in incs if t - hours * 3600 < ts <= t), 3)


def _api_at(base, incs, t: float, k: float = DEFAULT_K):
    if base is None or t < base[0]:
        return None
    b_ts, b_val = base
    v = b_val * k ** ((t - b_ts) / 86400.0)
    v += sum(i * k ** ((t - ts) / 86400.0) for ts, i in incs if b_ts < ts <= t)
    return round(v, 3)


def _plain(v):
    """pandas/numpy scalars and NaN -> plain JSON-safe values."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        return v
    return v.item() if hasattr(v, "item") else v


def _opt(v):
    return None if v is None or np.isnan(v) else round(float(v), 3)


class GapFiller:
    def __init__(self, cfg, dataset, sources=None, soil_channels: dict | None = None, rain=None, now_fn=None):
        self._cfg = cfg
        self._dataset = dataset
        self._sources = sources
        self._soil = soil_channels or {}
        self._rain = rain
        self._now_fn = now_fn or time.time
        self._interval = max(1, cfg.fast_loop_minutes) * 60.0
        self._last_eco: dict | None = None   # so a counter delta can span two passes
        self._started = self._now_fn()
        self._node_context: list[dict] = []  # node records carry forward within NODE_CONTEXT_S

    def fill(self, node: list[dict], eco: list[dict]) -> int:
        node = sorted(node, key=lambda r: r["ts"])
        eco = sorted(eco, key=lambda r: r["ts"])
        prev_eco, self._last_eco = self._last_eco, (eco[-1] if eco else self._last_eco)
        stamps = [r["ts"] for r in node] + [r["ts"] for r in eco]
        if not stamps:
            return 0

        # Check if rain accumulator is ready before updating context or last_eco
        if self._rain is not None:
            anchor = self._rain.anchor_ts()
            if anchor is None or anchor < self._started:
                raise GapFillDeferred("waiting for the first live rain poll")

        start, end = min(stamps), max(stamps)

        # Merge previous node context with current batch; de-duplicate by ts
        # Prefer new batch records over context records at the same ts
        by_ts = {}
        for r in self._node_context + node:
            by_ts[r["ts"]] = r
        node = sorted(by_ts.values(), key=lambda r: r["ts"])

        frame = self._dataset.frame()
        ctx_rows: list[dict] = []
        if len(frame) and "ts" in frame:
            ctx = frame[(frame["ts"] >= start - STAGE_CONTEXT_S) & (frame["ts"] <= end + self._interval)]
            ctx_rows = [{k: _plain(v) for k, v in row.items()} for row in ctx.to_dict("records")]
        node_ts = [r["ts"] for r in node]
        eco_ts = [r["ts"] for r in eco]

        slots = missing_slots([r["ts"] for r in ctx_rows], start, end, self._interval)
        blind = [r for r in ctx_rows
                 if start <= r["ts"] <= end and r.get("stage_ft") is None
                 and r.get("creek_node_online") is not True and self._fresh(node_ts, r["ts"])]

        # Update node context even if there's no gap (for the next call)
        if node:
            max_node_ts = max(r["ts"] for r in node)
            self._node_context = [r for r in node if r["ts"] >= max_node_ts - NODE_CONTEXT_S]

        if not slots and not blind:
            return 0

        eco_incs = eco_increments(([prev_eco] if prev_eco else []) + eco)
        ring = self._rain.snapshot() if self._rain is not None else []
        if eco_ts:
            ring = [x for x in ring if not eco_ts[0] <= x[0] <= eco_ts[-1]]
        incs = sorted(ring + eco_incs)
        # The API index decays from the last live row before the gap. Blind rows are not live:
        # their index was computed without the gap's rain.
        gap_start = min(slots + [b["ts"] for b in blind])
        blind_ts = {b["ts"] for b in blind}
        api_base = next(((r["ts"], r["api_index_in"]) for r in reversed(ctx_rows)
                         if r["ts"] < gap_start and r["ts"] not in blind_ts
                         and r.get("api_index_in") is not None), None)
        evaluators = self._evaluators(start, end)

        built: dict[float, dict] = {}
        for ts in slots:
            row = {k: None for k in FIELD_NAMES}
            row.update(ts=ts, ponding_flag=False, rain_on_snow_flag=False)
            row.update(self._node_features(ts, node, node_ts))
            row.update(self._eco_features(ts, eco, eco_ts, incs, api_base))
            row.update(self._refetched(ts, evaluators))
            built[ts] = row
        for orig in blind:
            row = dict(orig)
            row.update(self._node_features(orig["ts"], node, node_ts))
            row.update(self._eco_features(orig["ts"], eco, eco_ts, incs, api_base))
            built[orig["ts"]] = row

        series = {r["ts"]: (r.get("stage_ft") if r.get("creek_node_online") is True else None)
                  for r in ctx_rows}
        for ts, row in built.items():
            series[ts] = row.get("stage_ft") if row.get("creek_node_online") is True else None
        order = sorted(series)
        change, above = stage_history_features(
            order, [np.nan if series[t] is None else series[t] for t in order])
        index = {t: i for i, t in enumerate(order)}

        # Calculate rate_of_rise_sample_count for all rows (context + built)
        all_rows = sorted(ctx_rows + list(built.values()), key=lambda r: r["ts"])
        row_by_ts = {r["ts"]: r for r in all_rows}
        for i, row in enumerate(all_rows):
            if row["ts"] in built:  # only update built rows
                rate = row.get("rate_of_rise_in_min")
                prev = all_rows[i - 1] if i > 0 else None
                if rate is not None and prev and row["ts"] - prev["ts"] <= 1.5 * self._interval:
                    prev_rate = prev.get("rate_of_rise_in_min")
                    if prev_rate is not None:
                        prev_count = prev.get("rate_of_rise_sample_count") or 0.0
                        row["rate_of_rise_sample_count"] = min(prev_count + 1.0, RATE_OF_RISE_SAMPLE_CAP)
                    else:
                        row["rate_of_rise_sample_count"] = 1.0
                elif rate is not None:
                    row["rate_of_rise_sample_count"] = 1.0
                else:
                    row["rate_of_rise_sample_count"] = 0.0

        for ts in sorted(built):
            row = built[ts]
            i = index[ts]
            row["stage_change_1h_in"], row["stage_above_6h_low_in"] = _opt(change[i]), _opt(above[i])
            row["rain_on_snow_flag"] = FeatureBuilder._rain_on_snow(
                FeatureRow(**{k: row.get(k) for k in FIELD_NAMES}))
            row["backfilled"] = True
            self._dataset.append_record(row)

        self._correct_rain(eco_ts, eco_incs)
        log.info("backfill: wrote %d dataset row(s) (%d new, %d re-issued) for %s – %s",
                 len(built), len(slots), len(blind),
                 datetime.fromtimestamp(start, timezone.utc).isoformat(timespec="minutes"),
                 datetime.fromtimestamp(end, timezone.utc).isoformat(timespec="minutes"))
        return len(built)

    @staticmethod
    def _fresh(stamps: list[float], ts: float, within: float = NODE_FRESH_S) -> bool:
        i = bisect.bisect_right(stamps, ts) - 1
        return i >= 0 and ts - stamps[i] <= within

    def _node_features(self, ts: float, node: list[dict], node_ts: list[float]) -> dict:
        i = bisect.bisect_right(node_ts, ts) - 1
        if i < 0 or ts - node_ts[i] > NODE_FRESH_S:
            return {"creek_node_online": False, "stage_held": False}
        rec = node[i]
        stage = rec.get("stage_ft")
        return {"stage_ft": stage, "stage_raw_ft": stage, "creek_node_online": True,
                "stage_age_min": round((ts - rec["ts"]) / 60.0, 2), "stage_held": False,
                "rate_of_rise_in_min": self._rate(node, node_ts, i)}

    def _rate(self, node: list[dict], node_ts: list[float], i: int):
        """FeatureBuilder._rate_of_rise's rule: against the newest reading at least a window old,
        and not across a gap longer than rate_of_rise_max_gap_minutes."""
        need = self._cfg.rate_of_rise_window_minutes * 60.0 - RATE_WINDOW_SLACK_S
        t1, s1 = node[i]["ts"], node[i].get("stage_ft")
        j = bisect.bisect_right(node_ts, t1 - need) - 1
        if s1 is None or j < 0:
            return None
        max_gap = self._cfg.rate_of_rise_max_gap_minutes * 60.0
        if any(b - a > max_gap for a, b in zip(node_ts[j:i], node_ts[j + 1:i + 1])):
            return None
        t0, s0 = node[j]["ts"], node[j].get("stage_ft")
        if s0 is None:
            return None
        return round((s1 - s0) * 12.0 / ((t1 - t0) / 60.0), 4)

    def _eco_features(self, ts, eco, eco_ts, incs, api_base) -> dict:
        k = bisect.bisect_right(eco_ts, ts) - 1
        if k < 0 or ts - eco_ts[k] > ECO_FRESH_S:
            return {}
        rec = eco[k]
        # Compute horizon: rain windows older than this are None
        horizon = self._now_fn() - RETAIN_S if self._rain is not None else float('-inf')
        out = {}
        for w in WINDOWS_H:
            if ts - w * 3600 < horizon:
                out[f"rain_{w}h_in"] = None
            else:
                out[f"rain_{w}h_in"] = _window_sum(incs, ts, w)
        out["rain_rate_in_hr"] = rec.get("rain_rate_in_hr")
        out["temp_f"] = rec.get("temp_f")
        out["api_index_in"] = _api_at(api_base, incs, ts)
        soil = rec.get("soil") or {}
        house = soil.get(self._soil.get("near_house")) if self._soil.get("near_house") else None
        creek = soil.get(self._soil.get("near_creek")) if self._soil.get("near_creek") else None
        present = [v for v in (house, creek) if v is not None]
        out.update(soil_moisture_near_house_pct=house, soil_moisture_near_creek_pct=creek,
                   soil_moisture_mean_pct=sum(present) / len(present) if present else None,
                   ponding_flag=any(v >= PONDING_SATURATION_PCT for v in present))
        return out

    def _evaluators(self, start: float, end: float) -> list:
        if self._sources is None:
            return []
        s = datetime.fromtimestamp(start, timezone.utc)
        e = datetime.fromtimestamp(end, timezone.utc)
        out = []
        for src in self._sources.history_sources():
            try:
                out.append(src.history_between(s, e))
            except Exception as exc:
                log.warning("backfill: %s history unavailable, its features stay empty: %s",
                            getattr(src, "name", src), exc)
        return out

    @staticmethod
    def _refetched(ts: float, evaluators) -> dict:
        out: dict = {}
        when = datetime.fromtimestamp(ts, timezone.utc)
        for ev in evaluators:
            try:
                out.update(ev(when))
            except Exception:
                log.debug("backfill: history evaluator failed at %s", when, exc_info=True)
        return out

    def _correct_rain(self, eco_ts: list[float], eco_incs: list[tuple[float, float]]) -> None:
        if self._rain is None or len(eco_ts) < 2:
            return
        if any(b - a > COUNTER_MAX_GAP_S for a, b in zip(eco_ts, eco_ts[1:])):
            log.info("backfill: Ecowitt records have a gap over an hour; live rain totals left "
                     "as they are")
            return
        self._rain.replace_window(eco_ts[0], eco_ts[-1], eco_incs)
