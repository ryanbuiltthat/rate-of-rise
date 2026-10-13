"""Rainfall→response lag estimation (spec §7 Phase 3).

§1 calls the rainfall-to-crest lag "the warning window", and measuring it empirically a
core project outcome. Two ways of measuring it, in order:

  * **Storm by storm** (`storm_lags`). For each storm in the storm log that the creek
    clearly answered, read two times straight off the record: storm start -> the creek
    starts up, and heaviest hour of rain -> crest. Report the median across storms. This
    is the primary method because it is what a person would do with the chart, and it
    works from one storm.
  * **Cross-correlation** (`_correlation_lag`), the fallback: shift a rainfall series
    forward against a response series and take the shift that lines them up best. On this
    creek it found nothing (best r 0.09 after a 16 in rise on 2026-10-11): a 5-minute stage
    step is mostly the radar's 1-2 mm flicker, and 90 mostly dry days bury the storms.

The response series is chosen by what exists:

  * `stage_ft` — the real target, once the SEN0676 is mounted.
  * `usgs_leggetts_rise_3h_ft` — the fallback while it is not. A different, larger basin
    with its own longer lag, so the number it yields is **not** the creek's lag. It is a
    sanity check that the correlation machinery works and that our rainfall inputs track a
    real hydrograph at all — the "downstream-gauge sanity comparison" §7 asks for. The
    result is labelled with the series used so the two are never confused.

Correlation is computed on first differences of the response, not its level: a rising
limb is what rainfall causes, whereas the level itself is dominated by baseflow and would
correlate with anything that trends.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

log = logging.getLogger("app.lag")

# Candidate lags to test, in minutes. §1 expects "tens of minutes to a few hours" for
# the creek; the downstream gauges respond slower, so the range runs out to 12 h.
MIN_LAG_MIN = 0
MAX_LAG_MIN = 720
LAG_STEP_MIN = 15

# Only fit against the recent record. Two reasons: cost grows linearly with the dataset
# (~15 s at a year of 5-minute samples, unbounded after that), and a lag averaged across
# seasons is the wrong number anyway — the warning window during March snowmelt on
# saturated ground is not the one that applies to an August thunderstorm.
LAG_WINDOW_DAYS = 90

# Guardrails — a lag fitted on too little data, or on a period with no rain, is noise.
MIN_PAIRS = 24                 # overlapping samples after shifting
MIN_RAIN_TOTAL_IN = 0.25       # the window must contain a real storm
MIN_CORRELATION = 0.30         # below this, report no estimate rather than a bad one

# A longer lag must beat the incumbent by this much to displace it. Storms recur, so
# shifting rain forward by a whole inter-storm interval lines it up with the *next*
# storm's rise and scores just as well — and often a hair better, since the longer shift
# drops the edge storms and with them the samples that fit worst. Physically the earliest
# lag that explains the response is the causal one, so ties and near-ties go to it.
TIE_MARGIN = 0.02

RESPONSE_PREFERENCE = (
    ("stage_ft", "creek stage"),
    ("usgs_leggetts_rise_3h_ft", "USGS the adjacent creek (proxy)"),
    ("usgs_tunkhannock_rise_3h_ft", "USGS downstream (proxy)"),
)
RAIN_PREFERENCE = ("upstream_rain_1h_in", "rain_1h_in")
# Longest gap between consecutive rows that still counts as one step of the fast loop.
MAX_STEP_S = 600.0

# --- Storm by storm ------------------------------------------------------------------
# For each storm in the storm log the creek clearly answered, two times read straight off
# the record: storm start -> the creek starts up (`response_lag_minutes`, the warning you
# get before the water moves at all), and the heaviest hour of rain -> the crest
# (`lag_minutes`, the rainfall-to-crest lag spec §1 calls the warning window). The median
# across storms is reported, with each storm's numbers alongside.
STORM_MIN_RISE_IN = 0.5      # smaller than this is the creek sitting still (crest.MIN_RISE_IN)
RESPONSE_RISE_IN = 0.25      # above the pre-storm level: the creek has started up
BASELINE_S = 3600.0          # pre-storm level: median stage over the hour before the start
CREST_SEARCH_S = 12 * 3600.0  # the crest can come well after the rain stops
STORM_DETAIL_MAX = 10        # newest storms listed in the published detail


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def _pick(df, candidates) -> tuple[str, str] | None:
    for name, label in candidates:
        if name in df.columns and df[name].notna().sum() >= MIN_PAIRS:
            return name, label
    return None


def estimate_lag(df, storm_windows=()) -> dict:
    """The rainfall-to-crest lag in minutes, and what it was measured from.

    Measured storm by storm (`storm_lags`) when the storm log has storms the creek clearly
    answered; otherwise by cross-correlation over the whole record (`_correlation_lag`).
    Returns a dict that is always safe to publish: `lag_minutes` is None whenever the data
    cannot support an estimate, with `reason` saying why.
    """
    if storm_windows and df is not None and len(df) and "stage_ft" in df.columns:
        by_storm = storm_lags(df, storm_windows)
        if by_storm["lag_minutes"] is not None:
            return by_storm
        fallback = _correlation_lag(df)
        if fallback["lag_minutes"] is None:
            # Neither worked: the storm reason says more about what the record lacks.
            fallback["reason"] = f"{by_storm['reason']}; correlation: {fallback['reason']}"
        return fallback
    return _correlation_lag(df)


def _empty() -> dict:
    return {"lag_minutes": None, "correlation": None, "response": None,
            "rain_series": None, "samples": 0, "reason": None, "method": None,
            "response_lag_minutes": None, "storms": 0, "storm_detail": []}


def _correlation_lag(df) -> dict:
    """Best-fitting lag by cross-correlation, with the series and correlation behind it.

    Weak on this creek: a 5-minute stage step is mostly the radar's 1-2 mm flicker, so
    against 90 mostly dry days the correlation rarely clears MIN_CORRELATION. It stays as
    the fallback for a record with no storm the creek clearly answered (and for the
    downstream-gauge proxy, which has no storm log of its own).
    """
    result = _empty()
    result["method"] = "correlation"

    if df is None or len(df) < MIN_PAIRS:
        result["reason"] = "not enough data yet"
        return result

    response = _pick(df, RESPONSE_PREFERENCE)
    if response is None:
        result["reason"] = "no response series available"
        return result
    response_col, response_label = response
    result["response"] = response_label

    rain_col = next((c for c in RAIN_PREFERENCE
                     if c in df.columns and df[c].notna().sum() >= MIN_PAIRS), None)
    if rain_col is None:
        result["reason"] = "no rainfall series available"
        return result
    result["rain_series"] = rain_col

    frame = df[["ts", rain_col, response_col]].dropna().sort_values("ts")
    if len(frame):
        cutoff = float(frame["ts"].iloc[-1]) - LAG_WINDOW_DAYS * 86400
        frame = frame[frame["ts"] >= cutoff]
    if len(frame) < MIN_PAIRS:
        result["reason"] = "not enough overlapping samples"
        return result
    if float(frame[rain_col].max() or 0.0) < MIN_RAIN_TOTAL_IN:
        result["reason"] = "no significant rain in the record yet"
        return result

    times = frame["ts"].tolist()
    rain = [float(v) for v in frame[rain_col]]
    level = [float(v) for v in frame[response_col]]
    # First differences: rainfall drives the *rise*, not the absolute level. Only between
    # rows a fast loop apart: across a restart or a node dropout the difference is hours
    # of movement charged to one step, which is exactly the spike that swamps the fit.
    rises = [(i, level[i] - level[i - 1]) for i in range(1, len(level))
             if times[i] - times[i - 1] <= MAX_STEP_S]
    rain_at = dict(zip(times, rain))

    best = None
    for lag in range(MIN_LAG_MIN, MAX_LAG_MIN + 1, LAG_STEP_MIN):
        xs, ys = [], []
        offset = lag * 60.0
        for i, rise in rises:
            source_ts = _nearest(times, times[i] - offset)
            if source_ts is None:
                continue
            xs.append(rain_at[source_ts])
            ys.append(rise)
        if len(xs) < MIN_PAIRS:
            continue
        r = _pearson(xs, ys)
        if r is not None and (best is None or r > best[1] + TIE_MARGIN):
            best = (lag, r, len(xs))

    if best is None:
        result["reason"] = "no usable overlap at any lag"
        return result

    lag, r, samples = best
    result["samples"] = samples
    result["correlation"] = round(r, 3)
    if r < MIN_CORRELATION:
        result["reason"] = f"best correlation {r:.2f} below {MIN_CORRELATION}"
        return result

    result["lag_minutes"] = lag
    log.info("Lag estimate: %d min against %s (r=%.2f, n=%d)",
             lag, response_label, r, samples)
    return result


def storm_lags(df, storm_windows) -> dict:
    """Rain-to-response and rain-to-crest lags measured on each storm. `storm_windows` is
    [(start_ts, end_ts)] from the storm log; `df` needs ts, stage_ft and a rain column."""
    result = _empty()
    result["method"] = "storms"
    result["response"] = "creek stage"
    frame = df.sort_values("ts")
    ts = frame["ts"].to_numpy(dtype=float)
    stage = (pd.to_numeric(frame["stage_ft"], errors="coerce")
             # A 3-reading median: the radar's single-reading flicker is not a rise.
             .rolling(3, center=True, min_periods=1).median().to_numpy(dtype=float))
    rains = {c: pd.to_numeric(frame[c], errors="coerce").to_numpy(dtype=float)
             for c in RAIN_PREFERENCE if c in frame.columns}
    # The same recent window as the correlation, for the same reason: March snowmelt on
    # saturated ground and an August thunderstorm do not share a warning window.
    newest = float(ts[-1]) if len(ts) else 0.0
    windows = sorted((float(s), float(e)) for s, e in storm_windows
                     if float(s) >= newest - LAG_WINDOW_DAYS * 86400)
    rows, skipped = [], []
    for k, (start, end) in enumerate(windows):
        next_start = windows[k + 1][0] if k + 1 < len(windows) else math.inf
        row = _one_storm(ts, stage, rains, start, end, next_start)
        (rows if isinstance(row, dict) else skipped).append(row)
    usable = [r for r in rows if r["crest_lag_minutes"] is not None]
    if not usable:
        result["reason"] = ("no storm the creek answered by "
                            f"{STORM_MIN_RISE_IN:g} in or more"
                            + (f" ({len(windows)} storm(s) checked)" if windows else ""))
        return result
    crest = sorted(r["crest_lag_minutes"] for r in usable)
    response = sorted(r["response_lag_minutes"] for r in usable
                      if r["response_lag_minutes"] is not None)
    result.update(
        lag_minutes=_median_5(crest),
        response_lag_minutes=_median_5(response) if response else None,
        storms=len(usable), samples=len(usable),
        rain_series=",".join(sorted({r["rain_series"] for r in usable})),
        storm_detail=usable[-STORM_DETAIL_MAX:])
    log.info("Lag by storm: rain->crest %s min, start->response %s min (%d storm(s))",
             result["lag_minutes"], result["response_lag_minutes"], len(usable))
    return result


def _one_storm(ts, stage, rains, start, end, next_start):
    """One storm's lags as a dict, or a short string saying why it was not usable."""
    search_end = min(end + CREST_SEARCH_S, next_start)
    pre = (ts >= start - BASELINE_S) & (ts < start) & ~np.isnan(stage)
    during = (ts >= start) & (ts <= search_end) & ~np.isnan(stage)
    if not during.any():
        return "no stage readings"
    baseline = float(np.median(stage[pre])) if pre.any() else float(stage[during][0])
    idx = np.flatnonzero(during)
    crest_i = idx[int(np.argmax(stage[idx]))]
    rise_in = (stage[crest_i] - baseline) * 12.0
    if rise_in < STORM_MIN_RISE_IN:
        return "too small a rise"
    rain_win = (ts >= start) & (ts <= end)
    rain_col = next((c for c in RAIN_PREFERENCE if c in rains
                     and np.nan_to_num(rains[c][rain_win]).max(initial=0.0) > 0), None)
    if rain_col is None:
        return "no rain recorded"
    rain = np.nan_to_num(rains[rain_col])
    rain_idx = np.flatnonzero(rain_win)
    # rain_*_1h_in is a trailing hour's total: the hour it covers is centred half an hour
    # before the row that reports it.
    peak_rain_ts = float(ts[rain_idx[int(np.argmax(rain[rain_idx]))]]) - 1800.0
    crest_lag = (ts[crest_i] - peak_rain_ts) / 60.0
    up = idx[(idx <= crest_i) & (stage[idx] >= baseline + RESPONSE_RISE_IN / 12.0)]
    response_lag = (ts[up[0]] - start) / 60.0 if len(up) else None
    # Plain Python numbers: this is published as JSON and saved to lag.json.
    return {"start": _iso(start), "rise_in": round(float(rise_in), 1),
            "peak_rain": _iso(peak_rain_ts), "crest": _iso(ts[crest_i]),
            # Crest before the heaviest rain: the rain that mattered fell before the storm
            # log opened, so there is no rain-to-crest time to read here.
            "crest_lag_minutes": int(round(float(crest_lag))) if crest_lag >= 0 else None,
            "response_lag_minutes": (None if response_lag is None
                                     else int(round(float(response_lag)))),
            "rain_series": rain_col}


def _median_5(values) -> int:
    return int(5 * round(float(np.median(values)) / 5))


def _iso(t: float) -> str:
    return datetime.fromtimestamp(float(t), timezone.utc).isoformat(timespec="minutes")


def _nearest(times: list[float], target: float, tolerance: float = 600.0) -> float | None:
    """Closest sample timestamp to `target`, or None if the gap is too large.

    The fast loop is nominally every 5 min but restarts and slow polls leave gaps; a
    tolerance keeps those from silently pairing rain with a response hours away.
    """
    if not times or target < times[0] - tolerance:
        return None
    lo, hi = 0, len(times) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if times[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    best = min((times[max(0, lo - 1)], times[lo]), key=lambda t: abs(t - target))
    return best if abs(best - target) <= tolerance else None


# --- persistence -------------------------------------------------------------------
# The estimate is computed in the nightly batch, so without this a restart would leave
# the lag entities at unknown until 3 AM. Storing the last result lets startup republish
# it immediately.
def save_lag(state_dir, result: dict) -> None:
    path = state_dir / "state" / "lag.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result), encoding="utf-8")
    except OSError as exc:
        log.warning("could not persist lag estimate: %s", exc)


def load_lag(state_dir) -> dict:
    """Last stored estimate, or a placeholder that is safe to publish at startup."""
    try:
        return json.loads((state_dir / "state" / "lag.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        placeholder = _empty()
    placeholder["reason"] = "pending the first nightly run"
    return placeholder
