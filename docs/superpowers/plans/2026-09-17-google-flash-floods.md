# Google Flash Flood Polygon Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the `GoogleFloods` source to also read Google's `flashFloods` API (point-in-polygon containment against the site, not gauge severity), and thread the result through features, tiers, the trained model, HA discovery, the dashboard, and docs — closing out open question #2's remaining half.

**Architecture:** One new pure-geometry module (KML parsing + point-in-polygon) consumed by an extension to the existing `GoogleFloods` class (same file, same API key, same 30-min poll cycle). Its output becomes three new feature keys that flow through the same pipeline every other source already uses: `FEATURE_KEYS` → `FeatureRow` → `tiers.compute_tier` → `train.FEATURE_COLUMNS` → `discovery.py` MQTT entities → the dashboard.

**Tech Stack:** Python 3.12, `requests`, stdlib `re`/`dataclasses` (no new dependency — KML is parsed by hand, matching this module's existing choice to hand-roll great-circle distance rather than pull in a geo library). Plain-assert test files with a `main()` runner, matching every existing test in `rate_of_rise/tests/`.

**Spec:** `docs/superpowers/specs/2026-09-17-google-flash-floods-design.md`

## Global Constraints

- No new Python dependency for geometry (spec §2). `requirements.txt` is not touched.
- No new config key (spec §7) — reuses `cfg.google_floods_api_key` and the site lat/lon already threaded into `GoogleFloods.__init__`.
- Same 30-min poll cadence as the existing gauge-status read (decided during brainstorming) — do not give the new code its own `refresh_seconds`.
- Capped at Tier 2 Watch, never floors at Warning (decided during brainstorming) — mirrors the existing `google_flood_severity` ceiling in `tiers.py`.
- HA mints entity_ids from **device name + entity `name`**, never the published `object_id` (see `discovery.py`'s own `entity_ids()` docstring and `tests/test_dashboard_entities.py`). Every new entity's `name` must slugify to the same thing as its `object_id` slug, and every dashboard reference must use the minted `sensor.rate_of_rise_creek_google_flash_flood_*` form — never a bare `creek_google_flash_flood_*` guess.
- `python rate_of_rise/tests/test_X.py` is how every test file in this repo is run (no pytest runner is used for `rate_of_rise/tests/`, unlike `creek_modeling/tests/`). Run each modified/new file directly after every step that touches it.

---

## File Structure

| File | Change |
|---|---|
| `rate_of_rise/app/sources/kml_geometry.py` | **New.** KML polygon parsing + point-in-polygon test. Pure functions, no network, no new dependency. |
| `rate_of_rise/tests/test_kml_geometry.py` | **New.** Pins the ray-casting/hole/multigeometry/on-edge behavior. |
| `rate_of_rise/app/sources/google_floods.py` | **Modify.** Adds `_flash_flood_events()`, `_polygon_kml()`, `_flash_floods()`; extends `FEATURE_KEYS` and `poll()`. |
| `rate_of_rise/tests/test_google_floods.py` | **Modify.** `build()` helper routes by URL instead of by body-presence (needed once there are two POST and two GET endpoints); new test cases for the flash-flood path. |
| `rate_of_rise/app/sources/__init__.py` | **Modify.** Aggregate `FEATURE_KEYS` tuple gains the three new keys. |
| `rate_of_rise/app/features.py` | **Modify.** `FeatureRow` gains the three new fields. |
| `rate_of_rise/tests/test_sources.py` | **No content change** — its existing `test_every_feature_key_is_a_real_feature_row_field` is the regression guard; just re-run it. |
| `rate_of_rise/app/tiers.py` | **Modify.** New Advisory/Watch rule block. |
| `rate_of_rise/tests/test_tiers.py` | **Modify.** New test cases for the new rule. |
| `rate_of_rise/app/train.py` | **Modify.** `FEATURE_COLUMNS` gains the three new names. |
| `rate_of_rise/tests/test_train.py` | **No content change** — its column-list assertions are dynamic (`list(t.FEATURE_COLUMNS)`); just re-run it. |
| `rate_of_rise/app/discovery.py` | **Modify.** Two new entity specs. |
| `rate_of_rise/tests/test_discovery.py` | **Modify.** `test_topics_and_counts` and `test_publish_all_emits_retained_json` hardcode exact entity counts that the two new sensor specs will break; new existence checks for the two new specs, mirroring `test_rain_and_qpf_sensors_present`. |
| `dashboards/creek_flood_watch.yaml` | **Modify.** Two new rows in the existing "Google flood status" card. |
| `rate_of_rise/tests/test_dashboard_entities.py` | **No content change** — its `"google" in slug` check already covers new Google entities generically; just re-run it. |
| `docs/open-questions.md`, `docs/project-knowledge.md`, `creek-flood-warning-spec.md`, `rate_of_rise/CHANGELOG.md`, `rate_of_rise/DOCS.md`, `rate_of_rise/config.yaml` | **Modify.** Docs + version bump, final task. |

---

### Task 1: KML polygon parsing and point-in-polygon test

**Files:**
- Create: `rate_of_rise/app/sources/kml_geometry.py`
- Test: `rate_of_rise/tests/test_kml_geometry.py`

**Interfaces:**
- Produces: `parse_kml_rings(kml: str) -> list[Ring]`, `Ring` (dataclass: `outer: list[tuple[float, float]]`, `holes: list[list[tuple[float, float]]]`), `point_in_rings(lat: float, lon: float, rings: list[Ring]) -> bool`, `point_in_kml(lat: float, lon: float, kml: str) -> bool`. Task 2 consumes only `point_in_kml`.

- [ ] **Step 1: Write the failing tests**

Create `rate_of_rise/tests/test_kml_geometry.py`:

```python
"""Plain-assert tests for KML polygon parsing and point-in-polygon testing.
Run: python rate_of_rise/tests/test_kml_geometry.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.kml_geometry import point_in_kml, parse_kml_rings  # noqa: E402


def _rect_kml(min_lat, min_lon, max_lat, max_lon, hole=None):
    """A single <Polygon> rectangle, optionally with one rectangular hole.

    `hole`, if given, is (min_lat, min_lon, max_lat, max_lon) for an
    <innerBoundaryIs> ring nested in the same <Polygon>.
    """
    def ring(a, b, c, d):
        return f"{b},{a},0 {d},{a},0 {d},{c},0 {b},{c},0 {b},{a},0"

    outer = (f"<outerBoundaryIs><LinearRing><coordinates>"
             f"{ring(min_lat, min_lon, max_lat, max_lon)}"
             f"</coordinates></LinearRing></outerBoundaryIs>")
    inner = ""
    if hole is not None:
        inner = (f"<innerBoundaryIs><LinearRing><coordinates>"
                 f"{ring(*hole)}</coordinates></LinearRing></innerBoundaryIs>")
    return f"<Polygon>{outer}{inner}</Polygon>"


# A 41.0,-75.5-centred box, matching test_google_floods.py's SITE_LAT/SITE_LON.
SITE_BOX = (40.9, -75.6, 41.1, -75.4)


def test_a_point_well_inside_the_rectangle_is_inside():
    assert point_in_kml(41.0, -75.5, _rect_kml(*SITE_BOX)) is True


def test_a_point_well_outside_the_rectangle_is_outside():
    assert point_in_kml(42.0, -75.5, _rect_kml(*SITE_BOX)) is False


def test_a_point_exactly_on_an_edge_counts_as_inside():
    """The conservative direction for a flood warning (design spec, KML section):
    a boundary case must not silently read as "not affected"."""
    assert point_in_kml(40.9, -75.5, _rect_kml(*SITE_BOX)) is True    # bottom edge
    assert point_in_kml(41.0, -75.6, _rect_kml(*SITE_BOX)) is True    # left edge


def test_a_point_inside_a_hole_is_outside_even_though_inside_the_outer_ring():
    hole = (40.95, -75.55, 41.05, -75.45)
    kml = _rect_kml(*SITE_BOX, hole=hole)
    assert point_in_kml(41.0, -75.5, kml) is False       # centre of the hole
    assert point_in_kml(40.92, -75.58, kml) is True       # inside outer, outside hole


def test_a_multigeometry_of_two_disjoint_polygons_checks_both():
    far_box = (41.9, -76.6, 42.1, -76.4)
    kml = (f"<MultiGeometry>{_rect_kml(*SITE_BOX)}{_rect_kml(*far_box)}"
           f"</MultiGeometry>")
    assert point_in_kml(41.0, -75.5, kml) is True     # in the first polygon
    assert point_in_kml(42.0, -76.5, kml) is True     # in the second polygon
    assert point_in_kml(0.0, 0.0, kml) is False        # in neither


def test_a_bare_coordinates_block_with_no_polygon_wrapper_is_still_read():
    """GetSerializedPolygon's documented shape is a <Polygon>, but the parser must not
    assume it: a bare <coordinates> block (no <Polygon>/<outerBoundaryIs>) is the
    fallback case, read as a single outer ring."""
    coords = "-75.6,40.9,0 -75.4,40.9,0 -75.4,41.1,0 -75.6,41.1,0 -75.6,40.9,0"
    kml = f"<coordinates>{coords}</coordinates>"
    assert point_in_kml(41.0, -75.5, kml) is True
    assert point_in_kml(42.0, -75.5, kml) is False


def test_empty_kml_has_no_rings_and_contains_nothing():
    assert parse_kml_rings("") == []
    assert point_in_kml(41.0, -75.5, "") is False


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python rate_of_rise/tests/test_kml_geometry.py`
Expected: `ModuleNotFoundError: No module named 'app.sources.kml_geometry'`

- [ ] **Step 3: Write the implementation**

Create `rate_of_rise/app/sources/kml_geometry.py`:

```python
"""KML polygon parsing and point-in-polygon testing for Google Flash Flood events.

GetSerializedPolygon returns a KML string, not a coordinate array (see
app/sources/google_floods.py's module docstring). A KML <Polygon> carries one
<outerBoundaryIs> ring and zero or more <innerBoundaryIs> holes, and a flash-flood
event's geometry can itself be a <MultiGeometry> of several disjoint <Polygon>s.

No XML or geometry library is added for this — KML's <coordinates> block is flat,
whitespace/comma-delimited "lon,lat[,alt] ..." text, and ray-casting over the resulting
point list is small enough to hand-roll and pin with tests, matching this package's
existing choice (google_floods.py's hand-rolled great-circle distance) to avoid a geo
dependency for one narrow need.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_COORDINATES_RE = re.compile(r"<coordinates>(.*?)</coordinates>", re.DOTALL)
_OUTER_RE = re.compile(r"<outerBoundaryIs>(.*?)</outerBoundaryIs>", re.DOTALL)
_INNER_RE = re.compile(r"<innerBoundaryIs>(.*?)</innerBoundaryIs>", re.DOTALL)
_POLYGON_RE = re.compile(r"<Polygon>(.*?)</Polygon>", re.DOTALL)

# Ray-cast/on-segment tolerance in degrees — far tighter than any real coordinate
# rounding in a Google-served KML, loose enough to absorb float round-trip noise.
_EPS = 1e-9


@dataclass
class Ring:
    outer: list[tuple[float, float]]
    holes: list[list[tuple[float, float]]] = field(default_factory=list)


def _parse_ring(coordinates_block: str) -> list[tuple[float, float]]:
    """A KML <coordinates> block to a list of (lat, lon) — KML orders lon,lat[,alt]."""
    points = []
    for tup in coordinates_block.split():
        lon_str, lat_str, *_ = tup.split(",")
        points.append((float(lat_str), float(lon_str)))
    return points


def parse_kml_rings(kml: str) -> list[Ring]:
    """Every polygon in the document: each <Polygon> (bare, or inside a
    <MultiGeometry>) with its outer ring and holes, or — if there is no <Polygon>
    wrapper at all — a single bare <coordinates> block read as one outer ring."""
    polygon_blocks = _POLYGON_RE.findall(kml)
    if not polygon_blocks:
        coords = _COORDINATES_RE.findall(kml)
        return [Ring(outer=_parse_ring(coords[0]))] if coords else []

    rings = []
    for block in polygon_blocks:
        outer_match = _OUTER_RE.search(block)
        if not outer_match:
            continue
        outer_coords = _COORDINATES_RE.findall(outer_match.group(1))
        if not outer_coords:
            continue
        holes = []
        for inner_block in _INNER_RE.findall(block):
            inner_coords = _COORDINATES_RE.findall(inner_block)
            if inner_coords:
                holes.append(_parse_ring(inner_coords[0]))
        rings.append(Ring(outer=_parse_ring(outer_coords[0]), holes=holes))
    return rings


def _on_segment(lat: float, lon: float, lat1: float, lon1: float,
                 lat2: float, lon2: float) -> bool:
    cross = (lon2 - lon1) * (lat - lat1) - (lat2 - lat1) * (lon - lon1)
    if abs(cross) > _EPS:
        return False
    dot = (lat - lat1) * (lat2 - lat1) + (lon - lon1) * (lon2 - lon1)
    if dot < 0:
        return False
    length_sq = (lat2 - lat1) ** 2 + (lon2 - lon1) ** 2
    return dot <= length_sq + _EPS


def _point_in_ring(lat: float, lon: float, ring: list[tuple[float, float]]) -> bool:
    """Ray-casting (odd crossing count = inside), with an explicit on-edge check first
    so a boundary point reads as inside regardless of crossing parity — the
    conservative direction for a flood warning (a boundary case must not silently
    read as "not affected")."""
    n = len(ring)
    for i in range(n):
        lat1, lon1 = ring[i]
        lat2, lon2 = ring[(i + 1) % n]
        if _on_segment(lat, lon, lat1, lon1, lat2, lon2):
            return True

    inside = False
    j = n - 1
    for i in range(n):
        lat_i, lon_i = ring[i]
        lat_j, lon_j = ring[j]
        if (lat_i > lat) != (lat_j > lat):
            lon_intersect = lon_i + (lat - lat_i) * (lon_j - lon_i) / (lat_j - lat_i)
            if lon < lon_intersect:
                inside = not inside
        j = i
    return inside


def point_in_rings(lat: float, lon: float, rings: list[Ring]) -> bool:
    """Inside any ring's outer boundary and not inside that ring's own holes."""
    for ring in rings:
        if not _point_in_ring(lat, lon, ring.outer):
            continue
        if any(_point_in_ring(lat, lon, hole) for hole in ring.holes):
            continue
        return True
    return False


def point_in_kml(lat: float, lon: float, kml: str) -> bool:
    return point_in_rings(lat, lon, parse_kml_rings(kml))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python rate_of_rise/tests/test_kml_geometry.py`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add rate_of_rise/app/sources/kml_geometry.py rate_of_rise/tests/test_kml_geometry.py
git commit -m "Add KML polygon parsing and point-in-polygon test for flash flood geometry"
```

---

### Task 2: Extend `GoogleFloods` to ingest flash flood events

**Files:**
- Modify: `rate_of_rise/app/sources/google_floods.py`
- Modify: `rate_of_rise/tests/test_google_floods.py`

**Interfaces:**
- Consumes: `point_in_kml(lat, lon, kml) -> bool` from Task 1.
- Produces: `GoogleFloods.FEATURE_KEYS` gains `"google_flash_flood_likely"`, `"google_flash_flood_highly_likely"`, `"google_flash_flood_events"`; `GoogleFloods.poll()`'s returned dict always includes these three keys (never omitted, per the existing `blank` pattern).

- [ ] **Step 1: Update the test file's fake transport to route by URL**

The existing `build()` helper in `test_google_floods.py` distinguishes the two existing endpoints by whether `body is None` (GET) or not (POST). Once there are two POST endpoints (`gauges:searchGaugesByArea`, `flashFloods:search`) and two GET endpoints (`floodStatus:query...`, `serializedPolygons/{id}`), that is no longer enough. Replace `build()` in `rate_of_rise/tests/test_google_floods.py`:

```python
def build(pairs, fail_search_after=None, flash_events=None, polygons=None,
          fail_flash_search=False):
    """A source wired to a fake transport, plus the record of what it asked for.

    `flash_events`: list of flashFloods:search event dicts (camelCase keys, as the API
    returns them). `polygons`: {polygon_id: kml_string} for serializedPolygons/{id}.
    """
    gauges = [g for g, _ in pairs]
    statuses = {s["gaugeId"]: s for _, s in pairs}
    flash_events = flash_events or []
    polygons = polygons or {}
    calls = {"search": [], "status": [], "flash_search": [], "polygon": [], "headers": []}

    def fetch(url, headers, body=None, timeout=20.0):
        calls["headers"].append(headers)
        if "gauges:searchGaugesByArea" in url:
            calls["search"].append((url, body))
            if fail_search_after is not None and len(calls["search"]) > fail_search_after:
                raise RuntimeError("search is down")
            return {"gauges": gauges}
        if "floodStatus:queryLatestFloodStatusByGaugeIds" in url:
            calls["status"].append(url)
            asked = parse_qs(urlparse(url).query).get("gaugeIds", [])
            return {"floodStatuses": [statuses[g] for g in asked if g in statuses]}
        if "flashFloods:search" in url:
            calls["flash_search"].append((url, body))
            if fail_flash_search:
                raise RuntimeError("flash search is down")
            return {"flashFloodEvents": flash_events}
        if "serializedPolygons/" in url:
            polygon_id = url.rsplit("/", 1)[-1]
            calls["polygon"].append(polygon_id)
            return {"polygonId": polygon_id, "kml": polygons.get(polygon_id, "")}
        raise AssertionError(f"unexpected URL: {url}")

    clock = Clock()
    src = GoogleFloods(SITE_LAT, SITE_LON, KEY, fetch=fetch, now_fn=clock)
    return src, calls, clock
```

This is a pure refactor of the fake transport — every existing test in the file keeps
its current behavior (`calls["search"]`/`calls["status"]` mean exactly what they meant
before; `flash_events`/`polygons` default to empty, so existing tests get zero flash
flood events and never call `serializedPolygons`).

- [ ] **Step 2: Run the existing suite to confirm the refactor didn't break anything**

Run: `python rate_of_rise/tests/test_google_floods.py`
Expected: all existing tests still `PASS` (the class itself hasn't changed yet — this
step is checking the test-file refactor alone).

- [ ] **Step 3: Add a KML rectangle helper and the new failing tests**

Add near the top of `test_google_floods.py`, after the `at()` helper:

```python
def _rect_kml(min_lat, min_lon, max_lat, max_lon):
    coords = (f"{min_lon},{min_lat},0 {max_lon},{min_lat},0 "
              f"{max_lon},{max_lat},0 {min_lon},{max_lat},0 {min_lon},{min_lat},0")
    return (f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords}"
            f"</coordinates></LinearRing></outerBoundaryIs></Polygon>")


# Covers SITE_LAT, SITE_LON (41.0, -75.5).
SITE_UNION_BOX = (40.9, -75.6, 41.1, -75.4)
# Well away from the site.
FAR_HIGHLY_LIKELY_BOX = (40.5, -75.6, 40.6, -75.4)


def _flash_event(event_id, union_kml=None, highly_likely_kml=None):
    """A flashFloods:search event dict plus the polygons map entry it needs."""
    event = {
        "forecastIssueTime": "2026-09-17T10:00:00Z",
        "forecastPeriodHours": 6,
        "affectedCountryCodes": ["US"],
        "eventPolygonId": f"{event_id}_event",
        "likelyAffectedPolygonId": f"{event_id}_likely",
        "highlyLikelyAffectedPolygonId": f"{event_id}_hl",
    }
    polygons = {}
    if union_kml is not None:
        polygons[f"{event_id}_event"] = union_kml
    if highly_likely_kml is not None:
        polygons[f"{event_id}_hl"] = highly_likely_kml
    return event, polygons
```

Then append these test functions (before `main()`):

```python
# --- flash flood polygons ------------------------------------------------------------

def test_site_outside_every_event_polygon_reports_zero_and_skips_the_highly_likely_fetch():
    event, polygons = _flash_event("ev1", union_kml=_rect_kml(*FAR_HIGHLY_LIKELY_BOX))
    src, calls, _ = build([], flash_events=[event], polygons=polygons)
    out = src.poll()
    assert out["google_flash_flood_likely"] == 0.0
    assert out["google_flash_flood_highly_likely"] == 0.0
    assert out["google_flash_flood_events"] == 0.0
    assert calls["polygon"] == ["ev1_event"]     # union checked; highly-likely never fetched


def test_site_inside_the_union_but_outside_highly_likely_is_likely_only():
    event, polygons = _flash_event(
        "ev1", union_kml=_rect_kml(*SITE_UNION_BOX),
        highly_likely_kml=_rect_kml(*FAR_HIGHLY_LIKELY_BOX))
    src, calls, _ = build([], flash_events=[event], polygons=polygons)
    out = src.poll()
    assert out["google_flash_flood_likely"] == 1.0
    assert out["google_flash_flood_highly_likely"] == 0.0
    assert out["google_flash_flood_events"] == 1.0
    assert calls["polygon"] == ["ev1_event", "ev1_hl"]


def test_site_inside_highly_likely_is_highly_likely_only():
    event, polygons = _flash_event(
        "ev1", union_kml=_rect_kml(*SITE_UNION_BOX),
        highly_likely_kml=_rect_kml(*SITE_UNION_BOX))
    src, _, _ = build([], flash_events=[event], polygons=polygons)
    out = src.poll()
    assert out["google_flash_flood_highly_likely"] == 1.0
    assert out["google_flash_flood_likely"] == 0.0    # not both set
    assert out["google_flash_flood_events"] == 1.0


def test_two_overlapping_events_both_containing_the_site_count_both():
    ev1, poly1 = _flash_event("ev1", union_kml=_rect_kml(*SITE_UNION_BOX))
    ev2, poly2 = _flash_event("ev2", union_kml=_rect_kml(*SITE_UNION_BOX))
    src, _, _ = build([], flash_events=[ev1, ev2], polygons={**poly1, **poly2})
    assert src.poll()["google_flash_flood_events"] == 2.0


def test_a_polygon_fetch_failure_skips_just_that_event_not_the_whole_poll():
    ev_broken, _ = _flash_event("broken")   # its polygon id has no entry in `polygons`
    ev_ok, poly_ok = _flash_event("ok", union_kml=_rect_kml(*SITE_UNION_BOX))

    def flaky_fetch_factory(base_calls):
        def fetch(url, headers, body=None, timeout=20.0):
            if "serializedPolygons/broken_event" in url:
                raise RuntimeError("polygon service down")
            return base_calls(url, headers, body, timeout)
        return fetch

    src, calls, _ = build([], flash_events=[ev_broken, ev_ok], polygons=poly_ok)
    src._fetch = flaky_fetch_factory(src._fetch)
    out = src.poll()
    assert out["google_flash_flood_events"] == 1.0        # only "ok" counted
    assert out["google_flash_flood_likely"] == 1.0


def test_flash_flood_search_failure_propagates_like_the_status_query_does():
    """No caching layer sits in front of flashFloods:search (unlike gauge discovery's
    daily-refresh cache) — a failure here fails the whole poll every time, exactly like
    a floodStatus query failure already does. The coordinator's own try/except is what
    keeps the source's last-good values alive across a bad poll (app/sources/__init__.py)."""
    src, _, _ = build([], flash_events=[], fail_flash_search=True)
    try:
        src.poll()
    except RuntimeError:
        return
    raise AssertionError("a flashFloods:search failure must propagate out of poll()")


def test_an_event_with_no_polygon_id_is_skipped_not_fatal():
    event = {"forecastIssueTime": "2026-09-17T10:00:00Z", "forecastPeriodHours": 6}
    src, calls, _ = build([], flash_events=[event])
    out = src.poll()
    assert out["google_flash_flood_events"] == 0.0
    assert calls["polygon"] == []
```

- [ ] **Step 4: Run the tests to verify the new ones fail**

Run: `python rate_of_rise/tests/test_google_floods.py`
Expected: the seven new tests `FAIL` with `KeyError: 'google_flash_flood_likely'` (the
key doesn't exist in `poll()`'s output yet); everything else still `PASS`.

- [ ] **Step 5: Implement the flash flood read in `google_floods.py`**

In `rate_of_rise/app/sources/google_floods.py`:

Add the import and the module docstring's "NOT INGESTED" paragraph is now wrong —
replace it (the class now ingests exactly what it said it wouldn't):

```python
from .kml_geometry import point_in_kml
```

Replace the docstring's `NOT INGESTED.` paragraph (currently claims `flashFloods` and
`inundationMapSet` are "outside the US" and "have no bearing here" — checked against
Google's own API reference during this feature's design and found not to hold up; see
`docs/superpowers/specs/2026-09-17-google-flash-floods-design.md`) with:

```
FLASH FLOODS. `flashFloods:search` (filtered by country code only — no lat/lon filter
exists at the API level) plus `serializedPolygons/{id}` (KML geometry per polygon,
resolved via app/sources/kml_geometry.py's point-in-polygon test) answer a question the
gauge search cannot: is the site itself — not a neighbouring gauge — inside a forecast
flash-flood area. `event_polygon_id` is the union of the likely and highly-likely
polygons, so it is checked first as a cheap reject; only a site inside it pays for a
second fetch to tell "likely" from "highly likely". `inundationMapSet` (raster
depth/probability maps) is still not ingested — a heavier product than a scalar
containment read needs.
```

Add the three new keys to `FEATURE_KEYS`:

```python
FEATURE_KEYS = (
    "google_flood_severity",
    "google_flood_trend",
    "google_flood_gauge_mi",
    "google_flood_gauges",
    "google_flash_flood_likely",
    "google_flash_flood_highly_likely",
    "google_flash_flood_events",
)
```

Add a constant near `MAX_GAUGES`:

```python
# Above this many active national events in one flashFloods:search response, log a
# warning rather than silently resolving geometry for all of them — there is no
# geographic prefilter at the API level (see the module docstring), so an unbounded
# severe-weather day is the one scenario where this read could get expensive.
FLASH_FLOOD_EVENT_WARN_COUNT = 50
```

Add three new methods to the `GoogleFloods` class, after `_statuses`/`_severity` and
before `poll`:

```python
    # --- flash flood polygons --------------------------------------------------------

    def _flash_flood_events(self) -> list[dict]:
        payload = self._fetch(
            f"{BASE_URL}/flashFloods:search", self._headers(), {"countryCodes": ["US"]},
        )
        events = payload.get("flashFloodEvents") or []
        if len(events) > FLASH_FLOOD_EVENT_WARN_COUNT:
            log.warning(
                "flashFloods:search returned %d active events nationally — resolving "
                "geometry for all of them; consider a cap if this recurs", len(events),
            )
        return events

    def _polygon_kml(self, polygon_id: str) -> str:
        payload = self._fetch(f"{BASE_URL}/serializedPolygons/{polygon_id}", self._headers())
        return payload.get("kml") or ""

    def _flash_floods(self) -> dict:
        """Site containment across every currently active national event.

        `event_polygon_id` (the union of likely + highly-likely, per Google's docs) is
        checked first: outside it means outside both, so most events cost one polygon
        fetch, not two. A polygon fetch failure skips just that one event — logged, not
        fatal — the same as a gauge with no usable location being skipped rather than
        failing the whole poll.
        """
        likely_any = False
        highly_likely_any = False
        count = 0.0
        for event in self._flash_flood_events():
            event_polygon_id = event.get("eventPolygonId")
            if not event_polygon_id:
                continue
            try:
                event_kml = self._polygon_kml(event_polygon_id)
            except Exception:
                log.warning("could not resolve flash flood event polygon %s",
                            event_polygon_id, exc_info=True)
                continue
            if not point_in_kml(self._lat, self._lon, event_kml):
                continue

            count += 1.0
            is_highly_likely = False
            highly_likely_id = event.get("highlyLikelyAffectedPolygonId")
            if highly_likely_id:
                try:
                    hl_kml = self._polygon_kml(highly_likely_id)
                    is_highly_likely = point_in_kml(self._lat, self._lon, hl_kml)
                except Exception:
                    log.warning("could not resolve highly-likely polygon %s",
                                highly_likely_id, exc_info=True)
            if is_highly_likely:
                highly_likely_any = True
            else:
                likely_any = True

        return {
            "google_flash_flood_likely": 1.0 if likely_any else 0.0,
            "google_flash_flood_highly_likely": 1.0 if highly_likely_any else 0.0,
            "google_flash_flood_events": count,
        }
```

Replace `poll()` so the flash flood read runs unconditionally (it does not depend on
gauge discovery at all — a creek with zero nearby gauges can still sit inside a flash
flood polygon):

```python
    def poll(self) -> dict:
        blank: dict[str, float | None] = {k: None for k in FEATURE_KEYS}
        out = {**blank, **self._flash_floods()}

        gauges = self._known_gauges()
        if not gauges:
            # Nothing modelled nearby is a real reading, not a fault: zero gauges is the
            # honest count, and the rest stay unknown because nobody looked.
            out["google_flood_gauges"] = 0.0
            return out

        statuses = self._statuses(list(gauges))
        out["google_flood_gauges"] = float(len(statuses))

        # One gauge governs all three of the remaining features, so they always describe
        # the same place: the worst severity on offer, nearest first among equals. Mixing
        # a severity from one gauge with a trend from another would read as a single
        # coherent forecast and be nothing of the kind.
        governing: tuple[float, float, dict] | None = None
        for status in statuses:
            severity = self._severity(status)
            gauge = gauges.get(status.get("gaugeId"))
            if severity is None or gauge is None:
                continue
            rank = (severity, -gauge["mi"])
            if governing is None or rank > governing[:2]:
                governing = (severity, -gauge["mi"], status)

        if governing is not None:
            severity, neg_mi, status = governing
            out["google_flood_severity"] = severity
            out["google_flood_gauge_mi"] = round(-neg_mi, 1)
            out["google_flood_trend"] = TREND.get(
                (status.get("forecastTrend") or "").strip().upper())
        return out
```

(This is the same body as before, minus the `out = {**blank, "google_flood_gauges": ...}`
duplication now folded into the single `out` built up front, and with the new
`self._flash_floods()` merge at the top.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python rate_of_rise/tests/test_google_floods.py`
Expected: all tests `PASS` (existing + 7 new = the file's total count is now 7 higher).

- [ ] **Step 7: Commit**

```bash
git add rate_of_rise/app/sources/google_floods.py rate_of_rise/tests/test_google_floods.py
git commit -m "Ingest Google flashFloods polygon containment alongside gauge severity"
```

---

### Task 3: Thread the new features through `FEATURE_KEYS` and `FeatureRow`

**Files:**
- Modify: `rate_of_rise/app/sources/__init__.py`
- Modify: `rate_of_rise/app/features.py`
- Test: `rate_of_rise/tests/test_sources.py` (existing, unmodified — it's the regression guard for this task)

**Interfaces:**
- Consumes: the three keys from Task 2's `GoogleFloods.FEATURE_KEYS`.
- Produces: `app.sources.FEATURE_KEYS` (the aggregate tuple `SourceCoordinator.features()`
  filters to) and `app.features.FeatureRow` both include
  `google_flash_flood_likely`, `google_flash_flood_highly_likely`,
  `google_flash_flood_events`. Task 4 and Task 5 read these field names.

- [ ] **Step 1: Run the existing regression test to see it fail once the keys exist upstream**

This task is pure propagation with an existing test as the safety net, so there's no
new test to write — `test_every_feature_key_is_a_real_feature_row_field` in
`test_sources.py` already asserts `set(FEATURE_KEYS) - {every FeatureRow field name} == set()`.
Confirm it currently passes (the new keys aren't in `sources.FEATURE_KEYS` yet, only in
`GoogleFloods.FEATURE_KEYS`, which nothing aggregates them from until this step):

Run: `python rate_of_rise/tests/test_sources.py`
Expected: `5 passed` (unchanged from before Task 2 — `sources.FEATURE_KEYS` hasn't
changed yet).

- [ ] **Step 2: Add the keys to the aggregate `FEATURE_KEYS` in `sources/__init__.py`**

In `rate_of_rise/app/sources/__init__.py`, extend the tuple:

```python
    # 2i — Google Flood Forecasting status at the nearest modelled gauges
    "google_flood_severity", "google_flood_trend",
    "google_flood_gauge_mi", "google_flood_gauges",
    # 2j — Google Flash Flood polygon containment at the site itself
    "google_flash_flood_likely", "google_flash_flood_highly_likely",
    "google_flash_flood_events",
)
```

- [ ] **Step 3: Run the test to see it fail with the missing `FeatureRow` fields**

Run: `python rate_of_rise/tests/test_sources.py`
Expected: `test_every_feature_key_is_a_real_feature_row_field` `FAIL` — the assertion
message names the three new keys as `missing`.

- [ ] **Step 4: Add the fields to `FeatureRow`**

In `rate_of_rise/app/features.py`, after the existing `google_flood_*` fields:

```python
    google_flood_severity: float | None = None
    google_flood_trend: float | None = None
    google_flood_gauge_mi: float | None = None
    google_flood_gauges: float | None = None
    # Google Flash Flood polygons (slice 2j) — direct containment of the site itself in
    # Google's forecast flash-flood area, not a nearby gauge's severity. `likely`/
    # `highly_likely` are 0/1 flags (1.0 = the site is inside; 0.0 = a real reading that
    # it is not; None only if nothing could be read); `events` is the count of active
    # national events the site falls inside (almost always 0, occasionally 1; a count
    # rather than a flag because overlap across events is possible).
    google_flash_flood_likely: float | None = None
    google_flash_flood_highly_likely: float | None = None
    google_flash_flood_events: float | None = None
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python rate_of_rise/tests/test_sources.py`
Expected: `5 passed`

Also re-run `python rate_of_rise/tests/test_google_floods.py` to confirm nothing in
Task 2 regressed now that the keys exist upstream too (`GoogleFloods.poll()` already
returns them; this task only affects the coordinator/dataclass layer).

- [ ] **Step 6: Commit**

```bash
git add rate_of_rise/app/sources/__init__.py rate_of_rise/app/features.py
git commit -m "Add flash flood feature keys to FEATURE_KEYS and FeatureRow"
```

---

### Task 4: Tier rules for flash flood containment

**Files:**
- Modify: `rate_of_rise/app/tiers.py`
- Modify: `rate_of_rise/tests/test_tiers.py`

**Interfaces:**
- Consumes: `FeatureRow.google_flash_flood_likely`, `FeatureRow.google_flash_flood_highly_likely` (Task 3).
- Produces: `compute_tier` returns tier 1 ("Advisory") when only `google_flash_flood_likely` is set, tier 2 ("Watch") when `google_flash_flood_highly_likely` is set, same as any other rule in the function.

- [ ] **Step 1: Write the failing tests**

Add to `rate_of_rise/tests/test_tiers.py`, near the existing Google Flood tests
(after `test_google_forecasting_no_flooding_nearby_is_not_a_tier`):

```python
def test_flash_flood_likely_is_an_advisory():
    tier, label, reasons = compute_tier(row(google_flash_flood_likely=1.0), 0.0)
    assert (tier, label) == (1, "Advisory")
    assert "flash flooding likely" in reasons[0]


def test_flash_flood_highly_likely_is_a_watch():
    tier, label, reasons = compute_tier(row(google_flash_flood_highly_likely=1.0), 0.0)
    assert (tier, label) == (2, "Watch")
    assert "highly likely" in reasons[0]


def test_flash_flood_highly_likely_never_reaches_warning():
    """Same reasoning as the gauge severity block: still a Google model forecast about
    the site, not the creek's own instrument."""
    tier, _, _ = compute_tier(row(google_flash_flood_highly_likely=1.0), 0.0)
    assert tier == 2


def test_flash_flood_zero_flags_fire_nothing():
    assert compute_tier(
        row(google_flash_flood_likely=0.0, google_flash_flood_highly_likely=0.0),
        0.0)[0] == 0


def test_flash_flood_both_flags_set_only_reports_the_watch_reason():
    """Mirrors compute_tier's existing "only the reached tier's reasons" contract."""
    tier, label, reasons = compute_tier(
        row(google_flash_flood_likely=1.0, google_flash_flood_highly_likely=1.0), 0.0)
    assert (tier, label) == (2, "Watch")
    assert len(reasons) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python rate_of_rise/tests/test_tiers.py`
Expected: the five new tests `FAIL` (tier comes back `0` — `compute_tier` doesn't know
about the new fields yet); all existing tests still `PASS`.

- [ ] **Step 3: Add the rule block to `tiers.py`**

Add a comment after the existing `GOOGLE_TIER_RADIUS_MI` block (no new constants —
unlike the severity block, `google_flash_flood_likely`/`_highly_likely` are already 0/1
flags with no threshold left to name):

```python
# --- Google Flash Flood polygons (spec Addendum C 2j) ---
# A direct containment test of the site itself, not a nearby proxy -- no distance
# gating needed here the way the gauge severity block needs GOOGLE_TIER_RADIUS_MI.
# Still a Google model forecast rather than the creek's own instrument, so it tops out
# at Watch, same ceiling and same reasoning as the gauge severity block above. The two
# features are already 0/1 flags, so the rule below tests them directly -- no threshold
# constant to name, unlike the severity ladder above it.
```

Add the rule inside `compute_tier`, immediately after the existing Google Flood
Forecasting block (after the `elif gsev >= ADVISORY_GOOGLE_SEVERITY:` line):

```python
    # --- Google Flash Flood polygons (spec Addendum C 2j) ---
    if row.google_flash_flood_highly_likely:
        reasons.append((2, "Google forecasts flash flooding highly likely at this location"))
    elif row.google_flash_flood_likely:
        reasons.append((1, "Google forecasts flash flooding likely at this location"))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python rate_of_rise/tests/test_tiers.py`
Expected: all tests `PASS`.

- [ ] **Step 5: Commit**

```bash
git add rate_of_rise/app/tiers.py rate_of_rise/tests/test_tiers.py
git commit -m "Add flash flood containment to the Advisory/Watch tier rules"
```

---

### Task 5: Add the new features to the trained model

**Files:**
- Modify: `rate_of_rise/app/train.py`
- Test: `rate_of_rise/tests/test_train.py` (existing, unmodified — its column-list
  assertions are dynamic against `t.FEATURE_COLUMNS`)

**Interfaces:**
- Consumes: `google_flash_flood_likely`, `google_flash_flood_highly_likely`,
  `google_flash_flood_events` field names (Task 3) — must match exactly.
- Produces: `train.FEATURE_COLUMNS` includes the three names; `build_matrix()`'s output
  columns automatically follow (it iterates `FEATURE_COLUMNS`).

- [ ] **Step 1: Run the existing test to see current behavior**

Run: `python rate_of_rise/tests/test_train.py`
Expected: all tests `PASS` (nothing has touched `train.py` yet — this step is a
baseline before the change).

- [ ] **Step 2: Add the three columns to `FEATURE_COLUMNS`**

In `rate_of_rise/app/train.py`, extend the tuple:

```python
    # 2i Google Flood Forecasting. The only input that has already graded a river
    # against its own warning/danger thresholds rather than leaving that to us.
    # `google_flood_gauges` is deliberately absent: it counts how many gauges answered,
    # which is a property of Google's coverage and this add-on's search radius, not of
    # the weather, and a model given it would learn the day the coverage changed.
    "google_flood_severity", "google_flood_trend", "google_flood_gauge_mi",
    # 2j Google Flash Flood polygons. Unlike google_flood_gauges above, containment is
    # itself a weather signal (the site is or isn't inside a forecast flash-flood area),
    # not a coverage artifact, so all three ride along — same reasoning as severity/
    # trend rather than the gauge count.
    "google_flash_flood_likely", "google_flash_flood_highly_likely",
    "google_flash_flood_events",
)
```

- [ ] **Step 3: Run the test to verify it still passes**

Run: `python rate_of_rise/tests/test_train.py`
Expected: all tests `PASS` — `test_build_matrix_pads_missing_feature_columns_and_casts_bools`
already proves any column named in `FEATURE_COLUMNS` but absent from a given dataframe
comes back as an all-NaN column rather than failing, so no fixture changes are needed.

- [ ] **Step 4: Commit**

```bash
git add rate_of_rise/app/train.py
git commit -m "Add flash flood containment features to the trained model's feature columns"
```

---

### Task 6: HA discovery entities

**Files:**
- Modify: `rate_of_rise/app/discovery.py`
- Modify: `rate_of_rise/tests/test_discovery.py`

**Interfaces:**
- Consumes: `google_flash_flood_likely`, `google_flash_flood_highly_likely`,
  `google_flash_flood_events` (published on the `{b}/features` MQTT topic by the
  existing feature-publishing path — same topic the `creek_google_flood_*` entities
  already read from, no new topic).
- Produces: two new discovery specs whose **minted** entity_ids (device name + entity
  `name`, per `entity_ids()`) are `sensor.rate_of_rise_creek_google_flash_flood_status`
  and `sensor.rate_of_rise_creek_google_flash_flood_events`. Task 7 references these
  exact strings in the dashboard.

- [ ] **Step 1: Add the two specs**

In `rate_of_rise/app/discovery.py`'s `_specs()`, immediately after the existing
`("sensor", "creek_google_flood_gauges", {...})` entry:

```python
            ("sensor", "creek_google_flash_flood_status", {
                "name": "Creek Google Flash Flood Status",
                "state_topic": f"{b}/features",
                "value_template": (
                    "{% set hl = value_json.google_flash_flood_highly_likely %}"
                    "{% set l = value_json.google_flash_flood_likely %}"
                    "{{ 'unknown' if hl is none and l is none else"
                    " 'Highly likely' if hl == 1 else"
                    " 'Likely' if l == 1 else 'None' }}"),
                "icon": "mdi:weather-pouring"}),
            ("sensor", "creek_google_flash_flood_events", {
                "name": "Creek Google Flash Flood Events",
                "state_topic": f"{b}/features",
                "value_template": "{{ value_json.google_flash_flood_events if value_json.google_flash_flood_events is not none else none }}",
                "state_class": "measurement", "icon": "mdi:map-marker-alert"}),
```

The `name` fields are written to slugify to exactly their `object_id` — `"Creek Google
Flash Flood Status"` → `creek_google_flash_flood_status`, matching the existing pattern
every other entity here follows (per `entity_ids()`'s own warning about the two
diverging).

- [ ] **Step 2: Run `test_discovery.py` to see it fail on the hardcoded counts**

`rate_of_rise/tests/test_discovery.py` has two tests that hardcode exact entity counts
across the whole `DiscoveryPublisher` output — adding two new `"sensor"` specs breaks
both:

Run: `python rate_of_rise/tests/test_discovery.py`
Expected: `test_topics_and_counts` `FAIL` (`assert len(sensors) == 56` — actual is now
58) and `test_publish_all_emits_retained_json` `FAIL` (`assert len(published) == 79` —
actual is now 81); every other test still `PASS`.

- [ ] **Step 3: Update the hardcoded counts and add existence checks**

In `rate_of_rise/tests/test_discovery.py`:

```python
    # 16 status/model (incl. Phase 3 lag series) + 1 local gauge rate-of-rise
    # + 8 (2a incl. API index) + 8 (2b) + 6 (2c) + 1 (2d) + 2 (2e) + 4 (2g radar cells)
    # + 3 (2h WPC ERO) + 4 (2i/2j Google flood + flash flood)
    # + 1 soil mean (migrated out of the HA package)
    # + 1 storm-to-annotate (dashboard annotation)
    assert len(sensors) == 58, len(sensors)
```

(This replaces the existing comment block and its `assert len(sensors) == 56` — the
`+ 4 (2i/2j Google flood + flash flood)` line replaces whatever the existing comment
currently says for the Google Flood sensors, folding the two new ones into the same
count note.)

```python
def test_publish_all_emits_retained_json():
    pub, published = build()
    pub.publish_all()
    assert len(published) == 81
    for topic, payload, retain in published:
        assert retain is True
        json.loads(payload)  # valid JSON
```

Add a new test near `test_rain_and_qpf_sensors_present`, matching its style:

```python
def test_google_flash_flood_sensors_present():
    pub, _ = build()
    cfgs = {c["object_id"]: c for _, c in pub.configs()}
    status = cfgs["creek_google_flash_flood_status"]
    assert status["state_topic"] == "creek/features"
    assert "google_flash_flood_highly_likely" in status["value_template"]
    assert "google_flash_flood_likely" in status["value_template"]
    events = cfgs["creek_google_flash_flood_events"]
    assert events["state_topic"] == "creek/features"
    assert "google_flash_flood_events" in events["value_template"]
```

- [ ] **Step 4: Run the full test file to verify it passes**

Run: `python rate_of_rise/tests/test_discovery.py`
Expected: all tests `PASS`.

- [ ] **Step 5: Verify the minted entity_ids and the naming-convention guard**

Run: `python rate_of_rise/tests/test_dashboard_entities.py`
Expected: all 4 tests `PASS`. In particular,
`test_entities_added_after_the_rename_use_the_current_device_prefix` iterates every
spec slug containing `"google"` and asserts its minted id starts with `rate_of_rise_` —
the two new specs are covered by that loop automatically, no test-file change needed
there.

Also confirm the exact minted strings (this is what Task 7 references verbatim):

```bash
python -c "
import sys; sys.path.insert(0, 'rate_of_rise')
from app.discovery import DiscoveryPublisher
ids = DiscoveryPublisher(lambda *a: None, 'creek').entity_ids()
print(ids['creek_google_flash_flood_status'])
print(ids['creek_google_flash_flood_events'])
"
```
Expected output:
```
sensor.rate_of_rise_creek_google_flash_flood_status
sensor.rate_of_rise_creek_google_flash_flood_events
```

- [ ] **Step 6: Commit**

```bash
git add rate_of_rise/app/discovery.py rate_of_rise/tests/test_discovery.py
git commit -m "Publish HA discovery entities for Google flash flood status"
```

---

### Task 7: Dashboard cards

**Files:**
- Modify: `dashboards/creek_flood_watch.yaml`

**Interfaces:**
- Consumes: `sensor.rate_of_rise_creek_google_flash_flood_status`,
  `sensor.rate_of_rise_creek_google_flash_flood_events` (Task 6, exact strings).

- [ ] **Step 1: Add the two rows to the existing Google flood card**

In `dashboards/creek_flood_watch.yaml`, the "Google flood status (nearby rivers)" card
(around line 143) currently ends with the `Gauges watched` row. Add two more rows and
extend the card's own comment, since it now covers two different Google products:

```yaml
      # Google's Flood Forecasting model, read two ways: gauge severity at the nearest
      # gauges it runs (neighbouring rivers, never this creek -- regional context graded
      # against each gauge's own warning/danger thresholds; distance is shown because
      # the status cannot be read without it; gauges = 0 means Google models nothing
      # within 25 mi, open question #2's gauge half) and flash-flood polygon containment
      # of the site itself (open question #2's flash-flood half -- a direct read of
      # whether this exact point is inside a forecast flash-flood area).
      - type: entities
        title: Google flood status (nearby rivers)
        show_header_toggle: false
        entities:
          - entity: sensor.rate_of_rise_creek_google_flood_status
            name: Forecast status
          - entity: sensor.rate_of_rise_creek_google_flood_trend
            name: ↳ trend
          - entity: sensor.rate_of_rise_creek_google_flood_gauge_distance
            name: ↳ distance to that gauge
          - entity: sensor.rate_of_rise_creek_google_flood_gauges
            name: Gauges watched
          - type: divider
          - entity: sensor.rate_of_rise_creek_google_flash_flood_status
            name: Flash flood risk (this location)
          - entity: sensor.rate_of_rise_creek_google_flash_flood_events
            name: Active national events overlapping the site
```

- [ ] **Step 2: Verify the dashboard references resolve**

Run: `python rate_of_rise/tests/test_dashboard_entities.py`
Expected: all 4 tests `PASS` —
`test_every_dashboard_entity_exists_somewhere` now includes the two new references and
confirms they match entities `discovery.py` actually publishes (Task 6).

- [ ] **Step 3: Commit**

```bash
git add dashboards/creek_flood_watch.yaml
git commit -m "Add flash flood risk to the Google flood status dashboard card"
```

---

### Task 8: Docs, changelog, and version bump

**Files:**
- Modify: `docs/open-questions.md`
- Modify: `docs/project-knowledge.md`
- Modify: `creek-flood-warning-spec.md`
- Modify: `rate_of_rise/CHANGELOG.md`
- Modify: `rate_of_rise/DOCS.md`
- Modify: `rate_of_rise/config.yaml`

**Interfaces:** None — this task only updates prose and the version string. Run the
full test suite at the end as this plan's final gate.

- [ ] **Step 1: Close out open question #2 in `docs/open-questions.md`**

Replace the `- **#2.**` entry (currently ends "...**Half answered as of 0.21.0.**" and
the paragraph that follows it) with:

```markdown
- **#2.** Google Floods API: does a virtual gauge (hybas) land on the creek, or only on the
  larger receiving reach? What are its thresholds? **Fully answered as of 0.22.0.**
  `app/sources/google_floods.py` searches `gauges:searchGaugesByArea` for every gauge
  Google models within 25 mi — non-quality-verified and virtual HydroBASINS gauges
  included, which are the only plausible candidates on a creek this small — and reads
  `floodStatus:queryLatestFloodStatusByGaugeIds` for the nearest ten. The `Creek Google
  Flood Gauges` sensor is the count, and the add-on log names each gauge with its river,
  distance and verification state on every daily re-discovery. A count of 0 answers that
  no gauge, verified or virtual, lands close enough to be a useful proxy for this creek.

  The second half — does Google's *flash flood* product (a different, ungauged-basin
  forecast, not a gauge) reach a basin this small — is answered by the same 0.22.0
  release: `flashFloods:search` plus `serializedPolygons/{id}` polygon geometry are now
  checked against the site's own coordinates every 30 min (`google_flash_flood_likely`,
  `google_flash_flood_highly_likely`, `google_flash_flood_events`; Addendum C 2j). Unlike
  the gauge search, this is a direct read of the site itself, not a neighbouring proxy.
```

- [ ] **Step 2: Extend the Google Flood Forecasting paragraph in `docs/project-knowledge.md`**

Find the paragraph ending "...grading a neighbouring gauge against that gauge's own
warning/danger/extreme thresholds. It is regional, never about this creek — Google
gauges no reach this small — so it is capped at Tier 2 Watch." and add immediately
after it:

```markdown
The same release (0.22.0) adds a second, independent read from the same API:
`flashFloods:search` plus polygon geometry, checked directly against the site's own
coordinates rather than a neighbouring gauge. It answers a different question — is
*this exact point* inside a forecast flash-flood area — and is capped at the same Tier
2 Watch ceiling for the same reason (still a Google model forecast, not the creek's own
instrument). See `docs/open-questions.md` #2 and Addendum C 2j.
```

- [ ] **Step 3: Add Addendum C 2j to `creek-flood-warning-spec.md`**

Update the data-sources table row (around the `Google Flood Forecasting API` row) so its
final clause no longer says these are unbuilt:

Change:
```
Gauge-model thresholds and `v1.flashFloods` are not ingested — see 2i
```
to:
```
Gauge-model thresholds are not ingested; `v1.flashFloods` is ingested separately — see 2j
```

Then add a new Addendum C entry immediately after the existing `2i` write-up (after the
paragraph ending "...the tier radius is 15 mi rather than the 25 mi search radius, for
the same 'model feature vs. operator signal' reason as the ERO's Moderate/Slight split
above." or wherever that section currently ends):

```markdown
- **2j — Google Flash Flood polygon containment (done):** `google_floods.py`, same key
  as 2i. `flashFloods:search` (filtered only by country code — no lat/lon filter exists
  at this API's level) returns every currently active or forecast flash-flood event
  nationally as polygon IDs, not coordinates or a severity field. Each event's
  `event_polygon_id` (the union of its likely- and highly-likely-affected areas) is
  resolved via `serializedPolygons/{id}` to KML and checked against the site's own
  lat/lon with a hand-rolled point-in-polygon test (`app/sources/kml_geometry.py`) —
  outside the union means outside both, so only a site inside it pays for a second fetch
  to tell "likely" from "highly likely."

  This is the answer to open question #2's remaining half: unlike 2i, which reads a
  *neighbouring gauge*, this reads the site itself. Features: `google_flash_flood_likely`
  and `google_flash_flood_highly_likely` (0/1, mutually describing the worst event
  overlapping the site) and `google_flash_flood_events` (count of active national events
  the site falls inside — almost always 0). Same Tier 2 Watch ceiling as 2i, for the same
  reason: still a Google model forecast, not the creek's own instrument.
```

- [ ] **Step 4: Add the CHANGELOG entry and bump the version**

In `rate_of_rise/config.yaml`, bump `version: "0.21.1"` to `version: "0.22.0"`.

In `rate_of_rise/CHANGELOG.md`, add a new section above `## 0.21.1`:

```markdown
## 0.22.0

- **Google Flash Flood polygon containment (spec Addendum C 2j, closes open question
  #2).** `google_floods.py` now also calls `flashFloods:search` and resolves each
  event's polygon geometry (`serializedPolygons/{id}`, parsed as KML —
  `app/sources/kml_geometry.py`) against the site's own coordinates, alongside the
  existing gauge-severity read. Unlike the gauge search, this reads the site itself, not
  a neighbouring river: `google_flash_flood_likely` / `google_flash_flood_highly_likely`
  (published as `Creek Google Flash Flood Status`) and `google_flash_flood_events`
  (`Creek Google Flash Flood Events`). Capped at Tier 2 Watch, same ceiling and reasoning
  as the existing gauge-severity rule — still a Google model forecast, not the creek's
  own instrument. No new config key; reuses `google_floods_api_key`.
```

- [ ] **Step 5: Update the `google_floods_api_key` description in `DOCS.md` and `config.yaml`**

In `rate_of_rise/DOCS.md`, replace the `google_floods_api_key` table row's description:

```markdown
| `google_floods_api_key` | `""` | Google Flood Forecasting API key (Google Cloud project + the API enabled). Setting it enables two reads: the gauges Google models within 25 mi of the site with their forecast status (neighbouring rivers, capped at Tier 2 Watch), and flash-flood polygon containment of the site itself (also capped at Tier 2 Watch). Blank disables both |
```

In `rate_of_rise/config.yaml`, update the comment above `google_floods_api_key`:

```yaml
  # Google Flood Forecasting (floodforecasting.googleapis.com) — needs a Google Cloud
  # project with the API enabled and an API key. Setting it enables two reads: the
  # gauges Google models within 25 mi of the site (neighbouring rivers, not this creek —
  # regional context, tops out at Tier 2 Watch), and flash-flood polygon containment of
  # the site itself (also capped at Tier 2 Watch). Blank disables both entirely.
  google_floods_api_key: ""
```

- [ ] **Step 6: Run the full test suite as the final gate**

```bash
for f in rate_of_rise/tests/test_kml_geometry.py rate_of_rise/tests/test_google_floods.py \
         rate_of_rise/tests/test_sources.py rate_of_rise/tests/test_tiers.py \
         rate_of_rise/tests/test_train.py rate_of_rise/tests/test_dashboard_entities.py; do
  echo "=== $f ==="
  python "$f" || exit 1
done
```
Expected: every file prints its `N passed` line with no failures.

- [ ] **Step 7: Commit**

```bash
git add docs/open-questions.md docs/project-knowledge.md creek-flood-warning-spec.md \
        rate_of_rise/CHANGELOG.md rate_of_rise/DOCS.md rate_of_rise/config.yaml
git commit -m "Document Google flash flood ingestion and bump to 0.22.0"
```

---

## Self-Review

**Spec coverage:** §1 (stays in `GoogleFloods`) → Task 2. §2 (search + cheap-reject
polygon resolution, no cap, warning threshold) → Task 2 Step 5. §2's KML/holes/
MultiGeometry addendum → Task 1. §3 (failure handling) → Task 2's
`test_a_polygon_fetch_failure_...` and `test_flash_flood_search_failure_...`. §4 (new
feature keys) → Task 3. §5 (`tiers.py`) → Task 4. §6 (discovery) → Task 6. §7 (config —
no new key) → Task 8 Step 5 (comment only, confirmed no new key added anywhere in this
plan). §8 (`train.py`) → Task 5. Testing section → covered across Tasks 1, 2, 4;
dashboard/docs sections → Tasks 7, 8. Out-of-scope items (`inundationMapSet`, event cap,
timestamp features) are not implemented anywhere in this plan, matching the spec.

**Placeholder scan:** none found — every step has literal code or literal prose to
write, no "TODO"/"similar to Task N"/"add appropriate handling."

**Type consistency:** `google_flash_flood_likely` / `_highly_likely` / `_events` spelled
identically across Tasks 2, 3, 4, 5, 6 (`GoogleFloods.FEATURE_KEYS`,
`sources.FEATURE_KEYS`, `FeatureRow` fields, `tiers.py`, `train.FEATURE_COLUMNS`,
`discovery.py`'s value_templates). `point_in_kml(lat, lon, kml) -> bool` signature
matches between Task 1's production and Task 2's one call site. Entity id strings in
Task 6 and Task 7 match exactly (`sensor.rate_of_rise_creek_google_flash_flood_status`,
`sensor.rate_of_rise_creek_google_flash_flood_events`).
