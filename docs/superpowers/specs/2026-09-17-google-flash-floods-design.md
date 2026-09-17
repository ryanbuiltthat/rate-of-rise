# Google Flash Flood polygon ingestion

Date: 2026-09-17
Status: Approved

## Problem

`app/sources/google_floods.py` (0.21.0) answered open question #2's gauge half: it
searches `gauges:searchGaugesByArea` (25 mi, non-quality-verified included) and reads
`floodStatus:queryLatestFloodStatusByGaugeIds` for the nearest ten. That is a statement
about a *neighbouring river network* — Google gauges no reach this small, so the status
is read as regional forecast context, never as this creek's own level (module docstring,
`google_floods.py`).

The site's exact point has no gauge coverage at all, not even a low-confidence virtual
one, while the upstream watershed does. The module's own docstring claims `flashFloods`
and `inundationMapSet` are "satellite-derived products for ungauged basins outside the
US and have no bearing here" — checked against Google's actual API reference
(`flashFloods.search` method page, the RPC package docs) during this design's
brainstorming session, and that geographic restriction does not hold up: Google
describes the product as covering "ungauged basins" / "places where we don't have
quality-verified gauges" generally, filterable by country code, with no stated US
exclusion. That is exactly the gap this site sits in. This design adds it.

## What the API actually gives, and why that's harder than the gauge case

`flashFloods.search` (`POST https://floodforecasting.googleapis.com/v1/flashFloods:search`)
takes only `countryCodes[]`, `pageSize`, `pageToken` — **no lat/lon or bounding-box
filter**. Each returned `FlashFloodEvent` has:

```
forecast_issue_time, forecast_period_hours, affected_country_codes[],
likely_affected_polygon_id, highly_likely_affected_polygon_id, event_polygon_id
```

— polygon *IDs*, no coordinates, no severity/likelihood scalar. `event_polygon_id` is
documented as the union of the likely and highly-likely polygons. Resolving a polygon
to geometry is a second call, `GetSerializedPolygon`, returning a `SerializedPolygon
{polygon_id, kml}` — a KML string, not a coordinate array.

So unlike the gauge side (one search + one status call, distance computed directly from
returned lat/lon), telling whether the site is inside an event requires: search
nationally, then resolve and point-in-polygon-test geometry per candidate event. There is
no way to prune by distance before paying for a geometry fetch.

## Design

### 1. Stays inside the existing `GoogleFloods` class

Same API, same key (`google_floods_api_key`), same site lat/lon already threaded through
`__init__`, same per-source watchdog contract the coordinator expects (`name`,
`refresh_seconds`, `poll() -> dict`). A second source class would duplicate all three
for no isolation benefit — the two API surfaces already share a session/header helper
(`_headers()`) and a failure-handling shape.

`poll()` gains a call to a new `_flash_floods()` alongside the existing gauge-status
read, on the same `refresh_seconds = 30 * 60` cadence as the gauge status (not tightened
to a shorter interval — decided explicitly during brainstorming: 30 min is well inside
a flash-flood forecast's `forecastPeriodHours` window, and it keeps one poll cadence for
the whole class rather than two).

### 2. Search, then resolve only what's needed

```
POST /v1/flashFloods:search   {"countryCodes": ["US"]}
```

Hardcoded `"US"` — this is a fixed single-site add-on, not a multi-region product; there
is no lat/lon to search by, so the country list is the only filter available and this
site has exactly one country.

For each returned event:

1. Fetch `GetSerializedPolygon(event_polygon_id)`, parse its KML `<coordinates>` list,
   and run a ray-casting point-in-polygon test against the site's lat/lon. `event_polygon`
   is the union of likely+highly-likely, so this is a single cheap reject test: **outside
   the union means outside both**, and the event contributes nothing further.
2. Only for events where the site is **inside** the union: fetch
   `GetSerializedPolygon(highly_likely_affected_polygon_id)` and test again, to tell
   "likely" from "highly likely."

No coordinate-array geometry library is added as a dependency — KML's
`<coordinates>lon,lat,alt lon,lat,alt ...</coordinates>` is a flat, regular text format;
parsing it is a `str.split()` over whitespace and commas, and ray-casting over the
resulting point list is ~15 lines with no dependency. This mirrors the module's existing
choice to hand-roll great-circle distance rather than pull in a geo library.

A flash-flood polygon can legally be a `MultiGeometry` of several `<Polygon>` elements
(a disjoint event area), and each `<Polygon>` can carry an `<innerBoundaryIs>` hole inside
its `<outerBoundaryIs>`. The parser extracts every `<outerBoundaryIs><coordinates>` block
as one ring and every `<innerBoundaryIs><coordinates>` block as a hole belonging to the
ring it's nested under. The site is "inside" the polygon if it is inside *any* outer ring
and not inside that ring's own holes — a plain per-ring ray-cast, no winding-order
assumption needed since outer and inner are already labelled by tag, unlike the gauge
search's S2 loop. A point exactly on a boundary edge counts as inside (the conservative
direction for a flood warning: a boundary case should not silently read as "not
affected").

No cap on the number of events resolved per cycle (no `MAX_GAUGES`-equivalent). Real
nationwide active-flash-flood-event counts are typically small; capping needs a sort
key, and events carry no lat/lon to sort by without first parsing geometry — building
that pre-filter now would be speculative complexity for a cost that has not been
observed. Instead: log a warning if `flashFloods.search` returns more than 50 events, so
a real problem is visible rather than silently expensive.

### 3. Failure handling — same shape as the gauge side

- An exception from `flashFloods.search` itself: fatal to that `poll()` call, same as a
  first-ever gauge-discovery failure today — surfaces through the existing
  `google_flood_status_missing` watchdog. No new watchdog entity.
- A `GetSerializedPolygon` failure for one event: log and skip that event only (treat as
  "could not be evaluated," not "not affected") — same spirit as a gauge with no usable
  `location` being skipped rather than failing the whole poll.
- Zero events nationally, or zero containing the site, is a real reading (`0.0`), not a
  fault — same convention as `google_flood_gauges == 0.0`.

### 4. New feature keys

Added to `FEATURE_KEYS` in `google_floods.py` and re-exported through
`sources/__init__.py`'s aggregate `FEATURE_KEYS` tuple, and as new fields on
`features.FeatureRow`:

- `google_flash_flood_likely` — `1.0` if inside the event (union) polygon but not the
  highly-likely one, else `0.0` if evaluated and not inside, else `None` if nothing could
  be read.
- `google_flash_flood_highly_likely` — `1.0` if inside the highly-likely polygon, else
  `0.0`/`None` by the same convention.
- `google_flash_flood_events` — count of active national events the site falls inside
  (almost always 0, occasionally 1; a count rather than a flag because overlap is
  possible), same `0.0`-is-real / `None`-means-unread convention as `google_flood_gauges`.

`forecast_issue_time` / `forecast_period_hours` are **not** carried as features — nothing
in the tier or model logic consumes raw timestamps today, and no rule here needs "how
long is left on the forecast." They surface only as HA sensor attributes (§6) for a human
reading the dashboard.

### 5. `tiers.py`

New block immediately after the existing Google Flood Forecasting section, same shape:

```python
# --- Google Flash Flood polygons (spec Addendum C 2j) ---
# A direct containment test of the site itself, not a nearby proxy — no distance gating
# needed here the way the gauge severity block needs GOOGLE_TIER_RADIUS_MI. Still a
# Google model forecast rather than the creek's own instrument, so it tops out at Watch,
# same ceiling and same reasoning as the gauge severity block above.
ADVISORY_GOOGLE_FLASH_FLOOD_LIKELY = True
WATCH_GOOGLE_FLASH_FLOOD_HIGHLY_LIKELY = True
```

```python
if row.google_flash_flood_highly_likely:
    reasons.append((2, "Google forecasts flash flooding highly likely at this location"))
elif row.google_flash_flood_likely:
    reasons.append((1, "Google forecasts flash flooding likely at this location"))
```

Decided explicitly during brainstorming: capped at Watch, not floored at Warning like an
NWS Flash Flood Warning — this is still a Google model forecast about the point, and
`tiers.py`'s stated design reserves Warning/Emergency for the creek's own gauge.

### 6. HA discovery (`discovery.py`)

Two new specs beside the existing `creek_google_flood_*` block, object_id and `name`
kept in agreement (per `entity_ids()`'s own warning: HA mints the entity_id from the
device name + entity **name**, not the published `object_id`, and this module's
`test_entities_added_after_the_rename_use_the_current_device_prefix` already asserts
that *any* spec slug containing `"google"` mints under the `rate_of_rise_` prefix — so
these two are covered by that guard automatically, no test change needed there):

- object_id `creek_google_flash_flood_status`, name `"Creek Google Flash Flood Status"`
  → mints as `sensor.rate_of_rise_creek_google_flash_flood_status`. Value-templated from
  the two flags into "None / Likely / Highly likely" (same
  `{{ 'unknown' if ... is none else {...} }}` shape as `creek_google_flood_status`),
  icon `mdi:weather-pouring`. (Originally scoped to also carry `forecast_issue_time` /
  `forecast_period_hours` as attributes on this sensor — see the "Correction" at the end
  of this document: dropped during the final review's fix wave rather than implemented.)
- object_id `creek_google_flash_flood_events`, name `"Creek Google Flash Flood Events"`
  → mints as `sensor.rate_of_rise_creek_google_flash_flood_events`. The raw count,
  `state_class: measurement`, icon `mdi:map-marker-alert`.

No new watchdog binary_sensor (§3 — shares `google_flood_status_missing`).

**Dashboard.** `dashboards/creek_flood_watch.yaml` already has a "Forecast status" card
(the entities-card holding `rate_of_rise_creek_google_flood_status/_trend/
_gauge_distance/_gauges`, around line 145). Add the two new entities there by their full
minted IDs above — never by the `object_id` form — so
`test_dashboard_entities.py::test_every_dashboard_entity_exists_somewhere` and
`::test_entities_added_after_the_rename_use_the_current_device_prefix` both pass. This is
exactly the mistake `PRE_RENAME_SLUGS`/`LEGACY_DEVICE_NAME` in that test file documents
happening once already for the original four Google Flood cards.

### 7. Config

No new keys. `config.yaml`'s `google_floods_api_key` comment gains a clause noting it now
also gates the flash-flood check, matching how it already reads for the gauge search.

### 8. `train.py`

`google_flash_flood_likely`, `google_flash_flood_highly_likely`, and
`google_flash_flood_events` join the model's feature-column list, next to
`google_flood_severity`/`trend`. Unlike `google_flood_gauges` (deliberately excluded —
"a property of Google's coverage ... not of the weather"), containment *is* a weather
signal in its own right, same reasoning as the gauge severity/trend inclusion.

## Testing

New cases in `tests/test_google_floods.py` (same plain-assert / `main()` style as the
existing file, same fake-transport pattern as `build()`):

- Site outside every event's union polygon → both flags `0.0`, `events == 0.0`, no
  highly-likely fetch made (confirms the cheap-reject optimisation actually skips it).
- Site inside the union but outside highly-likely → `likely == 1.0`,
  `highly_likely == 0.0`.
- Site inside highly-likely → `highly_likely == 1.0`, `likely == 0.0` (not both set).
- Two overlapping events both containing the site → `events == 2.0`.
- A `GetSerializedPolygon` failure on one event does not fail the whole poll and does not
  count that event; a second, healthy event is still read correctly.
- A KML point-in-polygon test pinned against a real sample KML string (a known
  rectangle) with points inside, outside, and exactly on an edge (must read as inside) —
  same spirit as `radar_cells.py`'s hand-verified DRCT-convention test, so the ray-casting
  math can't silently invert. Separate cases for a polygon with an `<innerBoundaryIs>`
  hole (a point in the hole must read as outside) and a `MultiGeometry` of two disjoint
  polygons (a point in the second one must still count as inside).
- `flashFloods:search` failing outright is fatal to that poll (same contract as gauge
  discovery's first-failure test).
- A response of more than 50 events logs the sanity warning (§2) but still resolves
  normally.

New cases in `tests/test_tiers.py`: `google_flash_flood_likely` alone reaches Advisory;
`google_flash_flood_highly_likely` reaches Watch; neither present contributes nothing;
both present only the Watch reason is returned (same "only the reached tier's reasons"
contract `compute_tier` already has).

## Docs

- `docs/open-questions.md` #2 — close out fully (currently "half answered"): the second
  half of the question (does Google's flash-flood product reach a basin this small) is
  answered by this slice.
- `docs/project-knowledge.md` — extend the Google Flood Forecasting paragraph to mention
  the flash-flood polygon check and what it adds beyond the gauge severity read.
- `creek-flood-warning-spec.md` — new Addendum C sub-item "2j" alongside 2i.
- `dashboards/creek_flood_watch.yaml` — two new rows in the existing Forecast status
  card, by minted entity_id (§6).
- `CHANGELOG.md` — new entry.

## Out of scope

- `inundationMapSet` (raster depth/probability maps) — a different, heavier product
  (multiple maps per set, by severity and time range); nothing here needs pixel-level
  depth, only "is the site inside the forecast area," which the polygon check already
  answers. Left for a later slice if the polygon read alone proves not enough.
- A distance-based or count-based cap on events resolved per cycle — see §2; added only
  if the sanity-warning threshold above is ever actually hit in production logs.
- Feeding `forecast_issue_time` / `forecast_period_hours` into the model — see §4.

## Correction (2026-09-17): §6's sensor-attribute requirement dropped

§6 above specified that `Creek Google Flash Flood Status` would carry
`forecast_issue_time` / `forecast_period_hours` as HA attributes, human-readable context
alongside the model-facing `google_flash_flood_likely`/`_highly_likely` features. This
was never implemented — no task's brief carried it into an actual step, and the final
whole-branch review caught the gap.

Ruling made during that review's fix wave: amend this spec rather than implement it now.
Implementing it needs a new data path — the MQTT `json_attributes_topic` mechanism,
populated from per-event fields that `_flash_floods()` deliberately does not surface as
`FEATURE_KEYS` (§4 excludes them from the trained model on purpose) — which is real
plumbing work, not a small addition, for attribute-only display value. Left for a later
slice if the "how long is this forecast valid for" question turns out to matter in
practice once the sensor has been live for a while.
