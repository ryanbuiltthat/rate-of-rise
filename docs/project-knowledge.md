# Rate of Rise — Project Knowledge

Orientation for anyone (human or AI) picking up work on this repo without the history.
The [spec](../creek-flood-warning-spec.md) is the source of truth for *what is being
built*; this file covers *how the system fits together, what the conventions are, and
which mistakes have already been made and paid for*.

Current state: add-on **v0.23.0**. ML runs in shadow (`ml_drives_alerts` off) — alerts use
the threshold estimate.

---

## 1. What this is

A DIY flood early-warning system for a small creek in Lackawanna County, northeastern
Pennsylvania (`<site lat>`, `<site lon>`). Published as **Rate of Rise** (the repo and the
modeling add-on share the name). Small, flashy basin: rainfall-to-crest is measured in tens
of minutes, so lead time is the entire point.

The creek has **no USGS gauge of its own**. The on-site radar stream gauge (DFRobot
SEN0676) **is mounted and reporting** as of 2026-09-12, so stage and rate-of-rise now
feed the tier ladder and the dataset. Tiers 1–2 remain gauge-independent by design —
that is what kept the system useful during the wait for hardware, and it is still what
keeps it useful when the radio link to the node drops.

What the gauge does *not* yet give us is calibration: every threshold in `tiers.py` is
still a placeholder, and only observed storms can move it (open questions #8–#10; #7's
WH51 field calibration is done, see `docs/open-questions.md`).

**Storms here typically arrive from the W/NW, but the upstream PWS corridor lies to the
SE.** That geometry is the reason radar cell tracking exists (§2g below): for the
dominant storm track, the "upstream" gauges are physically *behind* the house and
cannot lead. This is the single most important fact about why the data sources look the
way they do.

---

## 2. Architecture — three layers, three deployment paths

This trips people up constantly. **The three layers ship differently**, and only one of
them arrives via the add-on update button:

| Layer | What | How it reaches production |
|---|---|---|
| **Moteino node** (`firmware/moteino_creek_node/`) | Radar stage sensor on the creek | OTA pushed via the gateway |
| **HA package** (`ha-packages/creek_warning.yaml`) | Service-stale watchdog, alert-tier automation, dry-run test script | **Manually copied** to `/config/ha-packages/`, then reload automations |
| **Dashboard** (`dashboards/creek_flood_watch.yaml`) | Lovelace UI | **Manually copied** to `/config/dashboards/` |
| **Add-on** (`rate_of_rise/`) | All ingestion, features, tiers, storm log, ML | HA add-on store → Update |

> **Never say "this will appear after you update the add-on" about package or dashboard
> changes.** They will not. Both must be re-copied by hand. This has caused real
> confusion — a user went several releases without a dashboard card because of exactly
> this wording.

Safe-to-do-mid-storm rule of thumb: **dashboard = display-only, safe anytime.
HA package = automations, reloading can drop a tier transition — wait for quiet.**
Add-on update restarts the modeling service only.

### Add-on internals (`rate_of_rise/app/`)

```
__main__.py     fast loop (5 min) + nightly batch; MQTT publish; command handling
config.py       options: scalars via env from run.sh, lists straight from options.json
sources/        one module per ingestion source (see §3)
features.py     FeatureRow dataclass + derived features (temp_f, rain_on_snow_flag)
tiers.py        §6 alert-tier evaluation (0–4) with human-readable reasons
storms.py       SQLite storm event log — detection, peaks, annotation
dataset.py      JSONL day-parts → nightly consolidation into dataset.parquet
train.py        xgboost pipeline; FEATURE_COLUMNS is the model's input allowlist
model.py        threshold vs ML inference, gated on min_events_for_ml
registry.py     model versions: active / candidate / rollback history
lag.py          rainfall→response lag estimation (Phase 3)
health.py       watchdogs — per-source liveness + per-entity staleness
discovery.py    MQTT discovery — auto-creates every HA entity
```

Data flow: `sources → FeatureBuilder → FeatureRow → {MQTT publish, dataset, storm log,
tiers, model}`.

---

## 3. Data sources (spec Addendum C)

All free, no key except WU. Each exposes `name`, `refresh_seconds`, `poll() -> dict`.
`SourceCoordinator` polls on each source's own cadence, **keeps last-good on error**, so
a flaky API never stalls the loop.

| Slice | Module | Source | Gives |
|---|---|---|---|
| 2a | `rain.py` | on-site Ecowitt rate, integrated | `rain_{1,3,6,24,72}h_in` |
| 2a | `nws.py` | api.weather.gov gridded forecast | `qpf_{6,24}h_in` |
| 2b | `wu.py` | Weather Underground PWS | `upstream_rain_*` |
| 2b | `nwm.py` | NOAA NWPS reach `<nwm reach id>` | `nwm_flow_cfs`, peak |
| 2c | `usgs.py` | NWIS gauges `<usgs downstream>`, `<usgs adjacent>` | gage, flow, 3 h rise |
| 2d | `alerts.py` | NWS active alerts by point | flood watch/warning flags |
| 2e | `snodas.py` | NOHRSC gridded SWE | `snow_water_equivalent_in` |
| 2f | `apindex.py` | derived from on-site rain | `api_index_in` |
| 2g | `radar_cells.py` | NEXRAD **`<nexrad site>`** Level 3 storm attributes via IEM | inbound cell ETA/count/dBZ/scan count |
| 2h | `ero.py` | **WPC Excessive Rainfall Outlook** via IEM point query | day 1–3 risk category |

**The horizon ladder** — worth understanding as a whole:

- **Day scale:** WPC ERO. Exists *before anything is on radar*. The only input that
  grades rain against **flash flood guidance** (what the ground can absorb) rather than
  reporting inches.
- **Hour scale:** radar cell tracks. Closest-point-of-approach against the site from
  SCIT motion vectors. The only input that leads for W/NW storms.
- **Now scale:** on-site rain gauge, upstream PWS, USGS response.

Across all three scales the same gap holds: every one of them forecasts or measures
*weather*, and leaves "is that number bad for this reach" to us. **Google Flood
Forecasting** (`app/sources/google_floods.py`, added 0.21.0) is the one input that has
already taken that step, grading a neighbouring gauge against that gauge's own
warning/danger/extreme thresholds. It is regional, never about this creek — Google gauges
no reach this small — so it is capped at Tier 2 Watch. Whether it says anything at all
depends on Google having a modelled gauge within 25 mi, which is open question #2 and is
now answered by a sensor rather than by speculation.

The same release (0.22.0) adds a second, independent read from the same API:
`flashFloods:search` plus polygon geometry, checked directly against the site's own
coordinates rather than a neighbouring gauge. It answers a different question — is
*this exact point* inside a forecast flash-flood area — and is capped at the same Tier
2 Watch ceiling for the same reason (still a Google model forecast, not the creek's own
instrument). See `docs/open-questions.md` #2 and Addendum C 2j.

---

## 4. Alert tiers (`app/tiers.py`)

`0` All-clear · `1` Advisory · `2` Watch · `3` Warning · `4` Emergency

Tiers 1–2 are **gauge-independent** (forecast + rainfall). Tiers 3–4 need the creek
node and are currently dormant — so **Watch is the highest tier reachable today**, which
is why the critical-notification floor defaults to 2 rather than 3.

Active NWS products impose a *floor* on the tier and never lower an earned one.

**The alone-vs-with-wet-ground pattern recurs** — learn it once and it explains several
rules. A signal strong enough on its own fires alone; a marginal one fires only over
already-primed ground (elevated soil moisture *or* API, so a dead probe can't mask it).
It governs the WPC ERO rule (Moderate+ alone, Slight only when wet) and radar cells
(≥50 dBZ or ≤20 min out fires immediately; a marginal cell needs **two confirming scans**
*and* primed ground). The radar confirmation exists because SCIT revises its per-scan
track vector constantly — most inbound cells change track on the very next scan, so
single-scan marginal alerts flapped.

**Every threshold in this file is an uncalibrated placeholder.** Treat a tier as a prompt
to go look, not a validated alarm.

---

## 5. Conventions

- **Version + changelog together.** Any behaviour or option change bumps `version:` in
  `rate_of_rise/config.yaml` *and* adds a matching `## x.y.z` section to
  `CHANGELOG.md`. CI enforces the pairing. The bump is what surfaces the Update button.
  Package-only changes still get a bump (precedent: 0.11.1) so the "re-copy the package"
  note reaches the add-on store.
- **Tests are plain-assert scripts**, one file per module, each with a `main()` that
  raises. Run any of them with `python rate_of_rise/tests/test_x.py`. CI runs every
  file even after one fails.
- **Never declare work done on local tests alone.** Poll the real GitHub Actions run and
  confirm success. (Six broken commits once landed on main because of this.)
- **Test-only deps** (`pyyaml`, `jinja2`) are installed separately in CI, never added to
  `requirements.txt` — that file builds the add-on image.
- **Comments explain *why*, not *what*.** The codebase is dense with rationale for
  non-obvious choices; match that register. Where a decision looks wrong at first glance,
  say why it isn't.

---

## 6. Hard-won gotchas

Each of these cost at least one full round-trip to discover. **Read before touching the
relevant area.**

### Home Assistant

**Entity IDs are pinned with `default_entity_id` = device name + entity *name*.**
`object_id` is ignored. "Creek NWS Alert Feed Missing" on device "Rate of Rise" →
`binary_sensor.rate_of_rise_creek_nws_alert_feed_missing`, regardless of its
`creek_nws_alerts_missing` object_id. The pin exists because HA otherwise puts the
device's **area** in front of every entity minted after the device is placed in one:
0.23.0's new entities came out `outside_rate_of_rise_*` (the gateway's
`outside_creek_gateway_*` entities are the same effect, ESPHome-side). The pin only
applies at first registration — an existing entity has to be renamed in HA. Use
`DiscoveryPublisher.entity_ids()`; `test_dashboard_entities.py` checks the dashboard
against it.

**MQTT `text` entities cap `max` at 255.** Higher is *not* clamped — MQTT discovery
validates against the platform schema and **silently drops the entire entity**, with no
error the add-on can see. Shipped once as `max: 500`; the entity simply did not exist in
HA. (0.12.2)

**Companion-app phones have no reliable `notify.<name>` service.** Target them by
**device id** through a device action instead:

```yaml
- domain: mobile_app
  type: notify
  device_id: efa8fde8c961dd7d5c23feda71661457
  title: "..."
  message: "..."
  data: { channel: alarm_stream, importance: high, ttl: 0, priority: high }
```

`notify.ryanphone` → *"unknown action"*. (0.14.2)

**`device_id` cannot be templated, and `repeat`/`for_each` cannot drive a device
action.** HA resolves `device_id` at automation *setup* time, before any per-iteration
rendering, so `"{{ repeat.item }}"` is never evaluated — it errors as a literal invalid
device id and the whole automation fails to load. One static action per phone; share
content with a YAML anchor (`&creek_push` / `<<: *creek_push`). (0.14.3)

**A manual run has no `trigger` variable.** `automation.trigger` provides none, so any
template reaching for `trigger.to_state` raises `UndefinedError` while *variables are
still being assembled* — before the first action, before even a persistent notification.
The automation silently does nothing. Always guard:
`trigger is defined and trigger.to_state is defined`, falling back to live entity state.
This is also what makes the automation dry-run testable at all (spec §8). (0.14.4)

**Android DND bypass is a device-side permission, not a payload flag.** Official docs:
notifications *"do not override Do Not Disturb settings"* unless a notification channel
is granted permission to — and only the phone's owner can grant it
(Settings → Apps → Home Assistant → Notifications → *channel* → override DND).
`ttl`/`priority` alone do **not** do it. Additionally, **channel settings are immutable
after first creation** ("only lowering of the importance will work"), and the channel only
appears in Android settings *after* a notification has used it. Renaming a channel starts a
fresh one with no permission — indistinguishable on the phone from the alarm having broken.
(0.15.0)

### Add-on

**New features must be added to `train.py`'s `FEATURE_COLUMNS`** or the model cannot see
them. That list is deliberately an explicit allowlist so stray columns can't become
inputs by accident — the cost is that a new feature stays invisible until named there.
The 2g radar features were published and recorded for three releases while being
excluded from the model that was meant to use them. (0.16.0)

**A column that is always `None` is not the same as a column that is absent.** The first
retrain to actually reach xgboost died on `DMatrix` rejecting
`soil_moisture_near_creek_pct: object` — a probe recorded every loop that has never
returned a value, which pandas reads back as dtype `object` rather than float NaN.
`build_matrix` padded *missing* columns with NaN and never considered present-but-empty
ones. Coerce with `pd.to_numeric(errors="coerce")` at every frame→model boundary;
`model._ml_predict` already did, and training did not. (0.20.1)

**An operator action that changes alerting must be reversible, and the first one is the
one that won't be.** `promote()` recorded the outgoing model in `history` only when there
*was* one, so promoting the first model ever — from the threshold estimate, with the least
evidence behind it — was the single promotion that could never be rolled back. "No model
active" has to be a recordable state, not the absence of one. The same press also handed
Tier 3/4 to a model whose held-out split had zero positives and could not score it, with
nothing saying so; that now warns at the press and stays flagged on the Active Model
sensor via `active_validated`. (0.20.2)

**Anything stateful across restarts must live on disk, not in an instance attribute.**
The storm quiet-clock was an in-memory attribute; a restart mid-storm reset it, the
fallback became the storm's own `started_ts`, and the next lull closed the event with
`ended_ts == started_ts` — a zero-duration storm in the record the lag analysis learns
from. Now persisted as `last_rain_ts` on the row. (0.14.0)

**Distinguish "measured zero" from "no data".** A `0.0` that means *WPC looked and drew
no risk area* must not be `None`, or the watchdog cries wolf every quiet day and a real
low-risk forecast reads as a broken feed. Conversely an unrecognised category stays
`None` rather than being silently downgraded to "no risk".

**`last_updated` moves only when the value changes (HA 2024.3+).** A write of the same value
moves `last_reported`. So a stage "age" is time since the reading last *changed*, not since
the node last reported — a still creek at 1 mm resolution can hold one value for many
minutes. Differencing "the last change before each poll" put two readings one report apart
and turned a 2 mm flicker into a Tier 3 rate (0.23.0). Treat the link (packet counter) as the
authority on freshness; for a monotonic counter, `last_updated` *is* the last packet.

**A single sample can be a sensor fault, and the gateway clamp makes it look like a flood.**
A cold radar's 0 is inside the blanking zone, which the gateway (deliberately, #14) clamps to
the 37.6 in range ceiling — a Tier 4 Emergency from a dry creek (2026-09-17). Check
physical plausibility before a reading can alarm, and keep the baseline across dropouts:
that one arrived as the first reading after a reconnect. (0.23.0)

**Audit the training labels before trusting any metric.** Every positive the first models
trained on was an artifact (that clamp, and a rate charged across a 2 h dropout). AUC was
0.608 and "validated" was true on a model that caught 0 of 49 held-out positives. Also: the
0.19.0 slug rename was a reinstall and wiped `/data`, so the dataset starts ~2026-09-12 while
the storm log (migrated) counts every storm since July — the ML gate counts storms the
training data does not contain.

**An HA automation watching a deleted entity fails silently forever.** The alert automation
still watched the old device's tier entity; deleting that device fired it once with
`to_state: None` (a crash in the trace) and it then watched nothing for two days. After
any device/entity cleanup, re-check what the automations trigger on.

**A flat history graph can be HA's recorder, not the device.** On 2026-09-23 the recorder
wrote no rows for *any* entity for 25 h while automations kept running; on the graphs every
sensor looked frozen. Count recorder rows per hour across all entities before blaming one.
The add-on now keeps its own high-resolution stage record in `/share/rate_of_rise/stage/`.

**A storm may never close if any rain signal never reaches zero.** The close test takes
the *stronger* of on-site and upstream 1 h rain, so one stuck PWS reporting a phantom
rate holds every event open indefinitely — the quiet clock never starts and no
`storm_quiet_hours` value helps. Check both series across a dry stretch before blaming
the window.

### ESPHome

These came off the **retired ESP32-C6 node**, which the Moteino M0 + RFM69HW link
replaced. ESPHome is still how the *gateway* is built (Seeed XIAO ESP32-C3), so the last
two still apply directly; the first is C6-specific, and the C3's own strapping pins
(GPIO2/8/9 — GPIO9 is BOOT) are documented in
`firmware/esp32_rfm69_gateway/gateway.base.yaml`, which is why the gateway's SPI is
deliberately remapped off the XIAO's defaults.

- **GPIO4/5 are strapping pins on the C6** — do not use for peripherals. UART moved to GPIO10/11.
- **`uint32` is not a C++ type**; use `uint32_t`. The wrong one produces confusing
  "member 'value' in non-class type 'int'" errors pointing at unrelated lambda lines.
- **`send_first_at` must be ≤ `send_every`**, or config validation fails outright.

All three are now guarded by tests in `test_esphome_entities.py`.

---

## 7. Testing discipline

The notification saga (0.14.1 → 0.15.0) is the cautionary tale worth internalising:
**four consecutive releases shipped broken while the test suite stayed green**, because
every test checked *structure* — is the channel `alarm_stream`, is `device_id` a literal
— and all of them passed against all four broken versions.

What fixed it: `render_variables()` in `test_alert_notifications.py`, which **renders**
the automation's variables the way HA does, with and without a trigger, and reproduces
the exact `UndefinedError`.

Rules that follow:

1. **Prefer rendering/executing over inspecting.** A test that only asserts a key exists
   proves nothing about whether the thing works.
2. **Verify a regression guard actually fails against the broken version.** Reconstruct
   the old shape and run it. Several guards written during this project passed both ways
   and were worthless until sharpened.
3. **Run tests in isolation when verifying** — the plain runner stops at the first
   failure, which hides whether the *other* guards caught anything.
4. **Validate external data assumptions against the live service** before trusting them.
   The NEXRAD `DRCT` field is direction-*from* (wind convention), confirmed by regressing
   45 live storm tracks' actual displacement against reported bearing — 45–0 for the
   from-convention. Getting it backwards would silently invert every threat call.

---

## 8. Where to look

| Question | File |
|---|---|
| What is being built and why | `creek-flood-warning-spec.md` |
| What changed in a release | `rate_of_rise/CHANGELOG.md` |
| How to install / configure | `rate_of_rise/DOCS.md` |
| What to do during a storm | `docs/storm-runbook.md` |
| Unresolved decisions | `docs/open-questions.md` |
| Node radar registers, power budget | `docs/node-hardware.md` |
| Wiring, build, flash, OTA, bench test | `firmware/README.md` |

---

## 9. Open items

**Blocked on hardware/field work:** rain-on-snow validation (#10, needs a winter event).
The surveyed datum (#5) and WH51 dry/saturated calibration (#7) are both **resolved** —
creekbed to sensor face is 1105 mm, both probes are calibrated at their burial spots, and
stage-based/soil-moisture tier thresholds (#8) are no longer waiting on either; #8 still
wants a hand-edit against real storm data once there's enough to review.

**Blocked on data:** API recession constant `k` (#9) and all forecast/rainfall tier
thresholds want fitting against real storms — which is what the storm event log and
annotations exist to accumulate. `min_events_for_ml` (10) has now been **cleared** — the
storm log passed it — so retrain produces real candidates. Clearing that gate is not the
same as having a trustworthy model: with a record this short the held-out split still
lands single-class, which is why promote warns rather than reassures.

**Available to build:** a tier hold across a stage dropout (#14's residual); more upstream
PWS stations (#4 — two configured, spec wants 3–5, and with two, one dropout halves the
sample). #2 is now closed — see `docs/open-questions.md` for why its per-gauge-threshold
residual isn't tracked as pending work.

**Merged, not yet on the node:** adaptive crest sampling (#15) is on `main` and CI has
regenerated `firmware.hex`, but the node on the pole still runs the fixed 60 s firmware
until someone presses "Push Node Firmware" on the gateway. This is the standing shape of
every node change — code on `main` is not code on the creek — and it is worth checking
before concluding the node is misbehaving. Two follow-ups are deliberately not in it:
surfacing the payload's new `fast` flag as a gateway entity, and restoring the regression
test that enforced node threshold below the add-on's `WARNING_RATE_OF_RISE_IN_MIN`.

**Standing caveat for anything user-facing:** NWS/NOAA remains the real alerting path.
This system is a data-collection and early-warning aid whose thresholds are not yet
field-calibrated, and every operator-facing surface says so.
