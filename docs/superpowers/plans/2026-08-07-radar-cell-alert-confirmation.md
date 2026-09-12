# Radar-cell alert confirmation + ground-priming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop Watch-tier flapping on marginal inbound storm cells by requiring multi-scan
confirmation and antecedent-wetness compounding, while letting severe or imminent cells
still alert immediately with no added delay.

**Architecture:** `sources/radar_cells.py` gains one new derived feature,
`radar_threat_scan_count`, computed from rows already present in its existing 30-minute
fetch window (no new persistent state). `tiers.py`'s Watch rule for radar cells becomes
conditional: severe/imminent cells fire as today; marginal cells additionally need that new
scan-count field plus existing soil-moisture/API-index fields to be elevated.

**Tech Stack:** Python 3.11, stdlib only for this change (`csv`, `datetime`, `math` — no new
dependencies). Tests are plain-assert scripts run via `python tests/test_x.py`, no pytest.

## Global Constraints

- Design spec: `docs/superpowers/specs/2026-08-07-radar-cell-alert-confirmation-design.md`
  — read it first if anything below is ambiguous.
- Run every modified test file directly with `python creek_modeling/tests/test_x.py`
  (working directory: repo root, or `creek_modeling/` — both work since each test file
  inserts its own parent onto `sys.path`). CI runs every `creek_modeling/tests/test_*.py`
  this same way.
- No new dependencies, no new MQTT/dashboard sensor, no dataset/model-training changes —
  these are explicit out-of-scope decisions in the spec.
- Any behavior change to a shipped add-on requires a `config.yaml` version bump with a
  matching `## <version>` section in `CHANGELOG.md`, or CI's changelog check fails the
  build (see `.github/workflows/tests.yml`, "Check the add-on version has a changelog
  entry").
- All new numeric constants are placeholders like their neighbors — comment them as such,
  matching the existing "PLACEHOLDER THRESHOLDS — tune with observed storms" convention.

---

### Task 1: Confirming-scan count in `RadarCells`

**Files:**
- Modify: `creek_modeling/app/sources/radar_cells.py`
- Test: `creek_modeling/tests/test_radar_cells.py`

**Interfaces:**
- Produces: `RadarCells.poll()` return dict gains key `"radar_threat_scan_count": float | None`
  — `None` when there is no current threat cell, otherwise the max count (across
  simultaneously-tracked threat cells) of how many consecutive scans, newest first,
  independently qualified that cell as an inbound threat via the existing
  `_intercept_eta_min` test.
- Consumes: nothing new — reuses the existing `_intercept_eta_min` instance method
  unchanged.

- [ ] **Step 1: Write the failing tests**

Add these five test functions to `creek_modeling/tests/test_radar_cells.py` (append after
`test_eta_is_the_soonest_of_several_threats`, before
`test_only_the_newest_fix_per_storm_id_counts`):

```python
def test_a_single_scan_yields_scan_count_one():
    lat, lon = west_of_site(10.0)
    src, _ = build([("202607292128", "Q7", 270, 20, 50, lat, lon)])
    out = src.poll()
    assert out["radar_threat_scan_count"] == 1.0


def test_two_consecutive_confirming_scans_yield_scan_count_two():
    lat, lon = west_of_site(10.0)
    src, _ = build([
        ("202607292122", "N4", 270, 20, 50, lat, lon),   # older scan: inbound
        ("202607292128", "N4", 270, 20, 50, lat, lon),   # newest scan: inbound
    ])
    out = src.poll()
    assert out["radar_threat_cells"] == 1.0
    assert out["radar_threat_scan_count"] == 2.0


def test_a_broken_confirmation_streak_counts_only_the_run_from_the_newest_scan():
    """The cell was inbound three scans ago, lost its track for one scan, then
    re-qualified. Only the unbroken run from the newest scan counts — two scans back
    does not get to "vote" once the streak has broken."""
    lat, lon = west_of_site(10.0)
    src, _ = build([
        ("202607292116", "P6", 270, 20, 50, lat, lon),   # 3 scans back: inbound
        ("202607292122", "P6", 90, 20, 50, lat, lon),    # 2 scans back: outbound (breaks it)
        ("202607292128", "P6", 270, 20, 50, lat, lon),   # newest: inbound again
    ])
    out = src.poll()
    assert out["radar_threat_cells"] == 1.0
    assert out["radar_threat_scan_count"] == 1.0


def test_scan_count_is_the_max_across_simultaneous_threats():
    far = west_of_site(10.0)      # one confirming scan
    near = west_of_site(5.0)      # two confirming scans
    src, _ = build([
        ("202607292128", "R8", 270, 20, 45, far[0], far[1]),
        ("202607292122", "S9", 270, 20, 52, near[0], near[1]),
        ("202607292128", "S9", 270, 20, 52, near[0], near[1]),
    ])
    out = src.poll()
    assert out["radar_threat_cells"] == 2.0
    assert out["radar_threat_scan_count"] == 2.0


def test_scan_count_is_none_when_there_is_no_current_threat():
    src, _ = build([])
    out = src.poll()
    assert out["radar_threat_scan_count"] is None
```

Also add one assertion line to the existing
`test_the_same_cell_coming_from_the_east_is_outbound_and_no_threat` test, right after its
existing `assert out["radar_threat_max_dbz"] is None` line:

```python
    assert out["radar_threat_scan_count"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python creek_modeling/tests/test_radar_cells.py`
Expected: `KeyError: 'radar_threat_scan_count'` (the key does not exist in `poll()`'s
output yet) on the first new test, and the whole script stops there (it is a plain
`for t in tests: t()` loop with no per-test isolation, so a `KeyError` raised inside one
test function propagates and halts the run — that itself is confirmation the new
assertions are exercised and currently unmet).

- [ ] **Step 3: Implement**

In `creek_modeling/app/sources/radar_cells.py`, replace the `_current_cells` static method
(the one with the "Newest row per storm ID..." docstring) with a `_parse` method that
groups all in-window rows by storm ID (newest first), plus a slimmer `_current_cells` that
derives the existing newest/freshness-filtered list from that grouping:

```python
    @staticmethod
    def _parse(text: str) -> dict[str, list[dict]]:
        """All in-window rows, grouped by storm ID, each list newest-first.

        Every volume scan re-lists every tracked cell, so a storm ID's list holds one
        entry per scan it was seen in. Keeping the full history (rather than only the
        newest fix) is what lets `_scan_count` tell a cell whose track has settled from
        one whose vector is still flopping scan to scan.
        """
        by_id: dict[str, list[dict]] = {}
        for row in csv.DictReader(io.StringIO(text)):
            try:
                cell = {
                    "valid": datetime.strptime(row["VALID"], "%Y%m%d%H%M"),
                    "id": row["STORM_ID"],
                    "lat": float(row["LAT"]),
                    "lon": float(row["LON"]),
                    "drct": float(row["DRCT"]),
                    "sknt": float(row["SKNT"]),
                    "max_dbz": float(row["MAX_DBZ"]),
                }
            except (KeyError, TypeError, ValueError):
                continue    # torn/malformed row — skip it, keep the rest
            by_id.setdefault(cell["id"], []).append(cell)
        for rows in by_id.values():
            rows.sort(key=lambda c: c["valid"], reverse=True)
        return by_id

    @staticmethod
    def _current_cells(by_id: dict[str, list[dict]]) -> list[dict]:
        """Newest row per storm ID, dropping cells that fell out of tracking.

        A cell whose latest fix is much older than the newest scan was dropped by SCIT
        (dissipated or merged) and no longer exists to threaten anything.
        """
        if not by_id:
            return []
        latest_scan = max(rows[0]["valid"] for rows in by_id.values())
        cutoff = latest_scan - timedelta(minutes=FRESH_MINUTES)
        return [rows[0] for rows in by_id.values() if rows[0]["valid"] >= cutoff]

    def _scan_count(self, rows: list[dict]) -> int:
        """How many consecutive scans, newest-first, independently qualify this storm
        as an inbound threat. A track that lost and re-found its intercept resets to
        however far back the current unbroken run reaches — an older confirmation does
        not "count" once the streak has broken."""
        count = 0
        for row in rows:
            if self._intercept_eta_min(row) is None:
                break
            count += 1
        return count
```

Then update `poll()` to use `_parse`/`_current_cells` and populate the new field:

```python
    def poll(self) -> dict:
        now = self._now()
        sts = (now - timedelta(minutes=FETCH_WINDOW_MIN)).strftime("%Y-%m-%dT%H:%MZ")
        ets = now.strftime("%Y-%m-%dT%H:%MZ")
        url = (
            "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/nexrad_storm_attrs.py"
            f"?fmt=csv&radar={self._radar}&sts={sts}&ets={ets}"
        )
        by_id = self._parse(self._fetch(url))
        cells = self._current_cells(by_id)

        out: dict[str, float | None] = {
            "radar_cells_tracked": float(len(cells)),
            "radar_threat_cells": 0.0,
            "radar_threat_eta_min": None,
            "radar_threat_max_dbz": None,
            "radar_threat_scan_count": None,
        }
        etas, dbzs, scan_counts = [], [], []
        for cell in cells:
            eta = self._intercept_eta_min(cell)
            if eta is not None:
                etas.append(eta)
                dbzs.append(cell["max_dbz"])
                scan_counts.append(self._scan_count(by_id[cell["id"]]))
        if etas:
            out["radar_threat_cells"] = float(len(etas))
            out["radar_threat_eta_min"] = round(min(etas), 1)
            out["radar_threat_max_dbz"] = max(dbzs)
            out["radar_threat_scan_count"] = float(max(scan_counts))
        return out
```

Finally, add the new key to the module-level `FEATURE_KEYS` tuple near the top of the
file:

```python
FEATURE_KEYS = (
    "radar_cells_tracked",
    "radar_threat_cells",
    "radar_threat_eta_min",
    "radar_threat_max_dbz",
    "radar_threat_scan_count",
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python creek_modeling/tests/test_radar_cells.py`
Expected: `19 passed` (14 existing + 5 new), no failures.

- [ ] **Step 5: Commit**

```bash
git add creek_modeling/app/sources/radar_cells.py creek_modeling/tests/test_radar_cells.py
git commit -m "$(cat <<'EOF'
Track confirming-scan count for inbound radar cells

Retains each storm ID's full in-window scan history instead of discarding
it after computing the newest fix, and counts how many consecutive scans
independently confirm a cell as an inbound threat. This is the raw signal
the next change uses to require multi-scan confirmation on marginal cells.
EOF
)"
```

---

### Task 2: Expose `radar_threat_scan_count` through the coordinator and FeatureRow

**Files:**
- Modify: `creek_modeling/app/sources/__init__.py`
- Modify: `creek_modeling/app/features.py`
- Test: `creek_modeling/tests/test_sources.py` (existing test, no changes needed — verifies
  this task)

**Interfaces:**
- Consumes: `RadarCells.poll()`'s `"radar_threat_scan_count"` key from Task 1.
- Produces: `FeatureRow.radar_threat_scan_count: float | None`, populated end to end from
  `SourceCoordinator.features()` through `FeatureBuilder.build()`.

- [ ] **Step 1: Run the existing coordinator test to confirm it currently fails to include the new key**

Run: `python creek_modeling/tests/test_sources.py`
Expected: PASSES as-is right now — `test_no_sources_returns_all_none` builds a
coordinator with zero sources configured, so it never touches `RadarCells` and won't
catch a missing key here. This step is a checkpoint, not a red test; the real check is
Step 3.

- [ ] **Step 2: Add the field**

In `creek_modeling/app/sources/__init__.py`, add `"radar_threat_scan_count"` to the
`FEATURE_KEYS` tuple, in the NEXRAD storm-cell group:

```python
    # 2g — NEXRAD storm-cell tracks (inbound-cell early warning)
    "radar_cells_tracked", "radar_threat_cells",
    "radar_threat_eta_min", "radar_threat_max_dbz", "radar_threat_scan_count",
```

In `creek_modeling/app/features.py`, add the field to `FeatureRow` in the NEXRAD block:

```python
    radar_cells_tracked: float | None = None
    radar_threat_cells: float | None = None
    radar_threat_eta_min: float | None = None
    radar_threat_max_dbz: float | None = None
    radar_threat_scan_count: float | None = None
```

- [ ] **Step 3: Run the full test suite to confirm nothing broke**

Run:
```bash
python creek_modeling/tests/test_sources.py
python creek_modeling/tests/test_discovery.py
python creek_modeling/tests/test_dataset.py
python creek_modeling/tests/test_train.py
```
Expected: all PASS. `test_sources.py::test_no_sources_returns_all_none` now asserts
`set(out) == set(FEATURE_KEYS)` against a tuple that includes the new key, and
`SourceCoordinator.features()` returns `None` for it (no sources configured) — this is the
real regression check for a typo'd key name. `test_discovery.py` confirms no dashboard
sensor references the new key incorrectly (it shouldn't reference it at all — no sensor is
being added). `test_dataset.py`/`test_train.py` confirm the dataset writer (which persists
`FeatureRow.as_dict()` verbatim) and the model's explicit `FEATURE_COLUMNS` allow-list
(which must NOT gain this column — see Global Constraints) both still pass unmodified.

- [ ] **Step 4: Commit**

```bash
git add creek_modeling/app/sources/__init__.py creek_modeling/app/features.py
git commit -m "$(cat <<'EOF'
Wire radar_threat_scan_count through to FeatureRow

Plumbing only: the coordinator now passes the field through like every
other source key, and FeatureRow carries it so tiers.py can read it next.
EOF
)"
```

---

### Task 3: Gate the radar Watch rule on severity, imminence, and ground-priming

**Files:**
- Modify: `creek_modeling/app/tiers.py`
- Test: `creek_modeling/tests/test_tiers.py`

**Interfaces:**
- Consumes: `FeatureRow.radar_threat_scan_count` (Task 2),
  `FeatureRow.radar_threat_max_dbz`, `FeatureRow.radar_threat_eta_min` (existing),
  `FeatureRow.soil_moisture_mean_pct`, `FeatureRow.api_index_in` (existing), and the
  existing `ADVISORY_SOIL_PCT` / `ADVISORY_API_INDEX_IN` module constants.
- Produces: `compute_tier()`'s existing return shape is unchanged
  (`tuple[int, str, list[str]]`) — only which cases produce a Tier 2 radar reason changes.

- [ ] **Step 1: Write the failing tests**

Add these five test functions to `creek_modeling/tests/test_tiers.py`, immediately after
`test_a_distant_or_absent_radar_cell_never_fires`:

```python
def test_a_severe_cell_fires_even_unconfirmed_and_dry():
    tier, _, reasons = compute_tier(
        row(radar_threat_eta_min=30.0, radar_threat_cells=1.0,
            radar_threat_max_dbz=55.0, radar_threat_scan_count=1.0,
            soil_moisture_mean_pct=20.0, api_index_in=0.0), 0.0)
    assert tier == 2
    assert "radar" in reasons[0]


def test_an_imminent_cell_fires_even_unconfirmed_and_dry():
    tier, _, reasons = compute_tier(
        row(radar_threat_eta_min=15.0, radar_threat_cells=1.0,
            radar_threat_max_dbz=42.0, radar_threat_scan_count=1.0,
            soil_moisture_mean_pct=20.0, api_index_in=0.0), 0.0)
    assert tier == 2
    assert "radar" in reasons[0]


def test_a_marginal_unconfirmed_cell_never_fires_even_on_wet_ground():
    tier, _, _ = compute_tier(
        row(radar_threat_eta_min=30.0, radar_threat_cells=1.0,
            radar_threat_max_dbz=42.0, radar_threat_scan_count=1.0,
            soil_moisture_mean_pct=90.0), 0.0)
    assert tier == 0


def test_a_marginal_confirmed_cell_never_fires_on_dry_ground():
    tier, _, _ = compute_tier(
        row(radar_threat_eta_min=30.0, radar_threat_cells=1.0,
            radar_threat_max_dbz=42.0, radar_threat_scan_count=2.0,
            soil_moisture_mean_pct=20.0, api_index_in=0.0), 0.0)
    assert tier == 0


def test_a_marginal_confirmed_cell_fires_on_primed_ground():
    tier, _, reasons = compute_tier(
        row(radar_threat_eta_min=30.0, radar_threat_cells=1.0,
            radar_threat_max_dbz=42.0, radar_threat_scan_count=2.0,
            soil_moisture_mean_pct=90.0), 0.0)
    assert tier == 2
    assert "radar" in reasons[0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python creek_modeling/tests/test_tiers.py`
Expected: `test_a_marginal_unconfirmed_cell_never_fires_even_on_wet_ground` and
`test_a_marginal_confirmed_cell_never_fires_on_dry_ground` FAIL (`assert tier == 0` gets
`2`, since the current unconditional rule fires on any dBZ ≥ 40 within the ETA horizon
regardless of confirmation or ground state). The other three tests already pass against
the current code — they will keep passing after Step 3 and confirm the change doesn't
regress the always-alert cases.

- [ ] **Step 3: Implement**

In `creek_modeling/app/tiers.py`, add three new constants directly below the existing
`WATCH_RADAR_ETA_MIN = 45.0` line:

```python
WATCH_RADAR_ETA_MIN = 45.0
# A cell this intense, or this close, alerts on the first qualifying scan — waiting on
# confirmation costs lead time on exactly the storms that matter most (see
# sources/radar_cells.py's module docstring on the W/NW approach gap).
WATCH_RADAR_SEVERE_DBZ = 50.0
WATCH_RADAR_IMMINENT_ETA_MIN = 20.0
# A marginal cell (below WATCH_RADAR_SEVERE_DBZ, further out than the imminent horizon)
# needs this many consecutive confirming scans before it counts — kills the flapping a
# single noisy SCIT vector estimate causes on a cell that isn't urgent enough to act on
# immediately anyway.
WATCH_RADAR_CONFIRM_SCANS = 2.0
```

Then replace the radar-cell block inside `compute_tier` (currently reading):

```python
    # Note the inverted comparison: a *smaller* ETA is the worse condition, so _ge does
    # not apply — and a missing ETA (None = no inbound cell) must still never fire.
    if row.radar_threat_eta_min is not None and row.radar_threat_eta_min <= WATCH_RADAR_ETA_MIN:
        count = int(row.radar_threat_cells or 1)
        dbz = f" ({row.radar_threat_max_dbz:.0f} dBZ)" if row.radar_threat_max_dbz else ""
        reasons.append((2, f"{count} radar cell(s) inbound{dbz}, "
                           f"~{row.radar_threat_eta_min:.0f} min out"))
```

with:

```python
    # Note the inverted comparison: a *smaller* ETA is the worse condition, so _ge does
    # not apply — and a missing ETA (None = no inbound cell) must still never fire.
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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python creek_modeling/tests/test_tiers.py`
Expected: all PASS, including the pre-existing
`test_an_inbound_radar_cell_is_a_watch_before_any_gauge_sees_rain`
(dbz=52, eta=25 — bypasses via `severe`, unaffected by the new gate) and
`test_a_distant_or_absent_radar_cell_never_fires`.

- [ ] **Step 5: Commit**

```bash
git add creek_modeling/app/tiers.py creek_modeling/tests/test_tiers.py
git commit -m "$(cat <<'EOF'
Gate marginal radar-cell Watch alerts on confirmation + ground priming

Severe (>=50 dBZ) or imminent (<=20 min) cells still alert on the first
qualifying scan. A marginal cell now also needs 2+ consecutive confirming
scans AND already-elevated soil moisture or API index before it raises a
Watch, matching the alone-vs-with-wet-ground pattern the WPC ERO rule
already uses. Addresses observed flapping where marginal cells changed
track on the very next scan.
EOF
)"
```

---

### Task 4: Version bump and changelog entry

**Files:**
- Modify: `creek_modeling/config.yaml`
- Modify: `creek_modeling/CHANGELOG.md`

**Interfaces:** None — packaging metadata only.

- [ ] **Step 1: Bump the version**

In `creek_modeling/config.yaml`, change:

```yaml
version: "0.16.0"
```

to:

```yaml
version: "0.17.0"
```

- [ ] **Step 2: Add the changelog entry**

In `creek_modeling/CHANGELOG.md`, insert a new section directly above the existing
`## 0.16.0` heading:

```markdown
## 0.17.0

- **Radar-cell Watch alerts now require confirmation on marginal cells.** Storm cells
  below 50 dBZ and more than 20 minutes out previously raised a Watch on a single scan,
  which flapped on and off as SCIT's per-scan track vector revised itself — most inbound
  cells changed track on the very next scan. Marginal cells now need two consecutive
  confirming scans (`radar_threat_scan_count`, new — a byproduct of retaining each storm
  ID's in-window scan history instead of discarding it after the newest fix) **and**
  already-elevated soil moisture or antecedent precipitation index before they raise a
  Watch, the same alone-vs-with-wet-ground pattern the WPC ERO rule already uses. A cell
  that clears 50 dBZ, or is within 20 minutes, still alerts immediately on the first
  qualifying scan — advance warning on the storms that matter most is unchanged.
```

- [ ] **Step 3: Verify the changelog check passes locally**

Run:
```bash
python - <<'PY'
import pathlib, yaml
version = yaml.safe_load(pathlib.Path("creek_modeling/config.yaml").read_text(encoding="utf-8"))["version"]
changelog = pathlib.Path("creek_modeling/CHANGELOG.md").read_text(encoding="utf-8")
assert f"\n## {version}\n" in changelog, f"missing changelog section for {version}"
print("ok", version)
PY
```
Expected: `ok 0.17.0`. (Requires `pyyaml` installed — `pip install pyyaml` if this errors
with `ModuleNotFoundError`.)

- [ ] **Step 4: Commit**

```bash
git add creek_modeling/config.yaml creek_modeling/CHANGELOG.md
git commit -m "$(cat <<'EOF'
Bump to 0.17.0 for radar-cell alert confirmation

EOF
)"
```

---

### Task 5: Full suite verification

**Files:** None modified — verification only.

- [ ] **Step 1: Run every test file the way CI does**

Run:
```bash
cd "/c/1 Project Repos/ewfa"
fail=0
for t in creek_modeling/tests/test_*.py; do
  echo "=== $t ==="
  python "$t" || fail=1
done
echo "fail=$fail"
```
Expected: every file prints its `N passed` line with no `Traceback`, and the final
`fail=0`.

- [ ] **Step 2: Validate config.yaml still parses as well-formed YAML**

Run:
```bash
python - <<'PY'
import yaml, pathlib
yaml.safe_load(pathlib.Path("creek_modeling/config.yaml").read_text(encoding="utf-8"))
print("ok")
PY
```
Expected: `ok`.

No commit for this task — it's a checkpoint confirming Tasks 1-4 are consistent together
before considering the plan done.
