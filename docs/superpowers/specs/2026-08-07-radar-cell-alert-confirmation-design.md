# Radar-cell alert confirmation + ground-priming

Date: 2026-08-07
Status: Approved

## Problem

[`sources/radar_cells.py`](../../../creek_modeling/app/sources/radar_cells.py) evaluates
each tracked NEXRAD cell from a single instantaneous scan (DRCT/SKNT). SCIT's per-scan
vector estimate is noisy on marginal cells, so a cell can flip in and out of "threat" from
one poll to the next — observed in practice as most inbound cells changing track after the
very next scan.

Separately, the Tier 2 Watch rule in [`tiers.py`](../../../creek_modeling/app/tiers.py)
(`WATCH_RADAR_ETA_MIN`) fires on *any* cell clearing the source's 40 dBZ floor within 45
minutes, with no regard for whether the basin is actually primed to respond — a marginal
cell over bone-dry ground with no upstream rain gets the same Watch as one arriving after
days of rain.

## Goals

- Reduce single-scan flapping on marginal cells without losing lead time on the storms that
  matter most (the whole reason this source exists — see the radar_cells.py module
  docstring on the W/NW approach gap).
- Only escalate a marginal cell to a Watch when the ground conditions make that rain
  actually consequential, mirroring the existing WPC ERO alone-vs-with-wet-ground pattern
  ([tiers.py:124-131](../../../creek_modeling/app/tiers.py)).

## Design

Severe or imminent cells always alert immediately, no confirmation needed:

- **Severe**: `radar_threat_max_dbz >= 50` (WATCH_RADAR_SEVERE_DBZ)
- **Imminent**: `radar_threat_eta_min <= 20` (WATCH_RADAR_IMMINENT_ETA_MIN)

A marginal cell (40-49 dBZ, more than 20 min out) needs **both**:

- **Confirmed**: at least 2 consecutive scans independently qualify it as an inbound threat
  (WATCH_RADAR_CONFIRM_SCANS)
- **Primed**: the basin is already wet — `soil_moisture_mean_pct >= 70` or
  `api_index_in >= 2.0` (the existing `ADVISORY_SOIL_PCT` / `ADVISORY_API_INDEX_IN`
  thresholds, reused verbatim so "primed" means the same thing everywhere in tiers.py)

The outer gate (`radar_threat_eta_min <= 45`, WATCH_RADAR_ETA_MIN) is unchanged — this
logic only decides whether a cell already inside that horizon is worth alerting on yet.

### 1. `sources/radar_cells.py` — track confirmation

`_current_cells` currently keeps only the newest row per storm ID within the fetch window
and discards the rest. The 30-minute fetch window already contains 4-6 prior scans per
cell (scans arrive every 4-6 min), so no new persistent state is needed across polls — the
history to confirm against is already in the window, it's just being thrown away.

Add a helper that, per storm ID, walks its in-window rows newest-to-oldest and counts how
many *consecutive* rows independently pass the existing `_intercept_eta_min` inbound/CPA/
speed test, stopping at the first row that fails. A brand-new cell scores 1. A cell that's
been consistently inbound for two straight scans scores 2+. A cell that flip-flopped last
scan resets to 1 even if it qualified three scans ago.

New output field `radar_threat_scan_count: float | None` — `None` when there is no current
threat cell, otherwise the max confirming-scan-count across all currently-tracked threat
cells (mirrors how `radar_threat_max_dbz` already aggregates via `max()` across
simultaneous cells). Added to `FEATURE_KEYS`.

`MIN_DBZ` (40, the "is this weather at all" floor) is unchanged.

**Known limitation, accepted for v1:** the existing fields already combine multiple
simultaneous threat cells into one summary (soonest eta from one cell, max dbz possibly
from a different one). `radar_threat_scan_count` follows that same aggregate convention
rather than tracking severity/confirmation per-cell. In the rare case of two simultaneous
cells — one soon-but-marginal, one severe-but-farther — the combined view could bypass
confirmation using the farther cell's dBZ. True per-cell tracking would fix this but means
restructuring the feature/model pipeline around a list instead of scalars. Not worth the
scope for a problem that hasn't been observed in practice; revisit only if it shows up.

### 2. `tiers.py` — the gating rule

Replace the current unconditional radar Watch block:

```python
if row.radar_threat_eta_min is not None and row.radar_threat_eta_min <= WATCH_RADAR_ETA_MIN:
    count = int(row.radar_threat_cells or 1)
    dbz = f" ({row.radar_threat_max_dbz:.0f} dBZ)" if row.radar_threat_max_dbz else ""
    reasons.append((2, f"{count} radar cell(s) inbound{dbz}, "
                       f"~{row.radar_threat_eta_min:.0f} min out"))
```

with a version that adds the severe/imminent/confirmed+primed gate:

```python
if row.radar_threat_eta_min is not None and row.radar_threat_eta_min <= WATCH_RADAR_ETA_MIN:
    severe = _ge(row.radar_threat_max_dbz, WATCH_RADAR_SEVERE_DBZ)
    imminent = row.radar_threat_eta_min <= WATCH_RADAR_IMMINENT_ETA_MIN
    confirmed = _ge(row.radar_threat_scan_count, WATCH_RADAR_CONFIRM_SCANS)
    primed = (_ge(row.soil_moisture_mean_pct, ADVISORY_SOIL_PCT)
              or _ge(row.api_index_in, ADVISORY_API_INDEX_IN))
    if severe or imminent or (confirmed and primed):
        count = int(row.radar_threat_cells or 1)
        dbz = f" ({row.radar_threat_max_dbz:.0f} dBZ)" if row.radar_threat_max_dbz else ""
        reasons.append((2, f"{count} radar cell(s) inbound{dbz}, "
                           f"~{row.radar_threat_eta_min:.0f} min out"))
```

New constants next to the existing radar ones:

```python
WATCH_RADAR_SEVERE_DBZ = 50.0       # intense enough to alert without waiting on confirmation
WATCH_RADAR_IMMINENT_ETA_MIN = 20.0 # too close to wait on confirmation regardless of intensity
WATCH_RADAR_CONFIRM_SCANS = 2.0     # consecutive agreeing scans required for a marginal cell
```

`_ge` already treats `None` as "never fires," so a missing `radar_threat_scan_count` (e.g.
a source version mismatch during rollout) degrades to "can't confirm" rather than raising —
severe/imminent cells are unaffected either way.

### 3. `features.py`

One new `FeatureRow` field in the existing NEXRAD block:

```python
radar_threat_scan_count: float | None = None
```

### Explicitly out of scope (YAGNI)

- No new MQTT discovery sensor for `radar_threat_scan_count` — it exists solely to drive
  the tier gate, not for dashboard display. Easy to add later if it turns out to be useful
  for debugging why an alert did or didn't fire.
- No dataset/model-training changes — the flood-probability model's inputs are unaffected.

## Testing

`tests/test_radar_cells.py` (new cases):
- Two consecutive confirming scans for one storm ID -> `radar_threat_scan_count == 2`.
- Confirming scan, then a non-confirming (e.g. outbound) scan, then confirming again ->
  count reflects only the consecutive run from the newest scan (1, not 3).
- Single scan for a storm ID -> count == 1.
- Two simultaneous threat cells with different scan counts -> output takes the max.

`tests/test_tiers.py` (new cases):
- Severe cell (dbz >= 50) fires even with `radar_threat_scan_count=1` and dry ground.
- Imminent cell (eta <= 20) fires even unconfirmed and dry.
- Marginal cell (dbz 40-49, eta > 20), unconfirmed (`scan_count=1`) -> never fires even on
  wet ground.
- Marginal cell, confirmed (`scan_count>=2`) but dry ground -> never fires.
- Marginal cell, confirmed and primed (wet soil or elevated API index) -> fires.
- Existing `test_an_inbound_radar_cell_is_a_watch_before_any_gauge_sees_rain` (dbz=52,
  eta=25) keeps passing unchanged — 52 >= 50 bypasses via `severe`.
