# Changelog

All notable changes to the **Rate of Rise** add-on are documented here.
The version matches `version:` in `config.yaml`; bump it to trigger the GUI Update button.

## 0.23.3

- **Fix: `creek_warning.yaml` and `creek_node_health.yaml` logged 1092 "duplicate key
  `device_id`" warnings.** Each phone-push automation's `&anchor` block set `device_id`
  for the "primary phone" itself and was then merged (`<<: *anchor`) into a second entry
  that also set `device_id`; per YAML merge-key rules the explicit key should just
  override the merged one, but HA's `annotatedyaml` loader flags that as a literal
  duplicate instead of silently preferring the override. The three affected anchors
  (`creek_push`, `watch_push`, `watch_clear`) are now pure templates with no `device_id`
  of their own — every phone entry, including the primary one, sets it exactly once via
  `<<: *anchor` + `device_id:`. **This does not arrive with the Update button — re-copy
  both `ha-packages/creek_warning.yaml` and `ha-packages/creek_node_health.yaml` to
  `/config/ha-packages/` and reload automations.**

## 0.23.2

- **Fix: 0.23.0's three new entities did not exist under the IDs everything names.**
  The device had been placed in the "Outside" area on 2026-09-20, and Home Assistant puts
  the area in front of every entity it mints after that, so they registered as
  `sensor.outside_rate_of_rise_creek_ml_shadow_probability`,
  `binary_sensor.outside_rate_of_rise_creek_stage_frozen` and
  `binary_sensor.outside_rate_of_rise_creek_stage_implausible`. The dashboard cards showed
  "Entity not found" — and `creek_node_health.yaml`'s gauge-fault check read both
  watchdogs as off, so a frozen or implausible stage could never raise its alert.
  Every discovery config now pins its ID with `default_entity_id` (`entity_ids()`, the
  rule the other 81 were registered under), so the next new entity cannot repeat this.

### Deploying

The pin applies only to an entity's first registration. The three above are already
registered, so rename each once in HA (Settings → Entities → the entity → ⚙ → Entity ID),
deleting the `outside_` prefix. Nothing in the repo changes for that.

## 0.23.1

- **Fix: `sensor.rate_of_rise_creek_predicted_crest` and `..._creek_lag_estimate` logged
  MQTT `ValueError`s on a null `value`.** Their `value_template`s fell back to the literal
  string `'unknown'` instead of Jinja's `none`; both sensors declare `state_class:
  measurement`, so HA validated `"unknown"` as a numeric state and rejected it. Every other
  numeric sensor in `discovery.py` already used `else none` for this case — these two now
  match.

## 0.23.0

Pre-storm hardening, from an audit of the live record (HA recorder, storm log, registry)
before the first storm with the ML path active. Every item below was found in the data,
not reasoned about. **This release also changes the HA packages, the dashboard and the node
firmware, none of which arrive with the Update button** — see *Deploying*, at the end.

- **Fix: normal sensor jitter could raise a Tier 3 Warning.** Rate of rise was the
  difference between the last value change before each poll, and those two changes could be
  one node report (~63 s) apart. At the node's 1 mm resolution a still creek's 2 mm flicker
  read as 0.075 in/min, over the 0.05 Warning; it happened twice on 2026-09-21 and both
  happened to be falls. The rate is now measured across `rate_of_rise_window_minutes`
  (default 10) against the newest reading at least that old. Same flicker: 0.008 in/min.
- **Fix: a radar fault raised a Tier 4 Emergency.** On 2026-09-17 a cold radar answered 0,
  the gateway clamped it to the 37.6 in range ceiling, and stage alone raised Emergency on a
  dry creek — on the first reading after a 24-minute dropout. A rise faster than
  `max_stage_rise_in_min` (default 2.0 in/min) from the last good reading is now withheld
  from the tiers as a sensor fault, and the new **Creek Stage Implausible** watchdog turns
  on. The baseline survives dropouts; a jump that holds for 30 minutes is believed, so a
  real rise during an outage cannot be locked out. Node side, see *Firmware*.
- **Fix: a frozen node-status sensor vouched for a dead link.** The status sensor only
  writes on a transition, so a gateway that stops publishing leaves it at `on`, and the
  add-on trusted it unconditionally. The new `creek_node_packets_entity` (the gateway's
  packet counter) overrules it when it stops moving.
- **New: Creek Stage Frozen watchdog.** On 2026-09-23 the reading sat on one value for
  3 h 11 min while packets kept arriving, and Stage Stale took an hour to notice. Frozen
  fires when the value has not changed in 30 min while the link is up.
- **ML runs in shadow unless `ml_drives_alerts` is on (default off).** The active model
  (or, with none active, the newest candidate) is evaluated every loop and published as
  **Creek ML Shadow Probability**; the tiers use the threshold estimate. Why: every positive
  label the September models trained on was an artifact — the 9/13 reconnect spike and the
  9/17 clamp — and the first promoted model took a dry evening to 93 % (Emergency).
- **Training ignores those artifacts.** Labels and features skip stage readings the creek
  could not have reached, and rates without a confirmed gap-free sample count (which is
  every row before the dropout guard, including the 9/13 spike).
- **"Validated" now means it caught something.** `gbm-20260924T030456Z` had AUC 0.608 and
  caught 0 of 49 held-out positives, and the dashboard called it validated. A model now also
  needs a hit rate above 0. `false_alarm_rate` is undefined (not 0.0) for a model that
  flagged nothing.
- **Probabilities are no longer inflated by class weighting.** Training up-weights the rare
  positives (~34x on the current record), which multiplies the odds by the same factor —
  harmless for AUC, not for fixed 20/50/80 % tier cut-offs. The weight is now recorded in
  the artifact's meta and undone at inference. Older artifacts are read as unweighted.
- **Model Health refreshes on Promote/Rollback**, and **Active Model reads `threshold`**
  instead of `none` when nothing is active — after a rollback the dashboard kept naming the
  removed model until the nightly run.
- **On-site rain from the station's own counter.** `onsite_rain_total_entity` (the Ecowitt
  rain total) is differenced instead of integrating the rain rate sampled once per loop,
  which read 5-10 % low against the station in steady rain and worse in bursts. Rate stays
  the fallback; the counter baseline survives restarts.
- **Upstream rain from each station's own total, and a watchdog that can fire.** WU
  `precipTotal` deltas per station replace `precipRate` sampled every 10 minutes (12-17 %
  low). And a poll where no station answers now counts as failed: previously every
  per-station error was swallowed and `upstream_data_missing` could never fire.
- **High-resolution stage record outside HA's recorder.** Every stage reading, at ~10 s,
  to `/share/rate_of_rise/stage/YYYY-MM-DD.csv`. HA's recorder stopped writing for 25 h on
  2026-09-23 (while HA kept running) and keeps 10 days by default; it was the only place
  the 60 s record lived.
- **Every dataset row records the alert tier, the probability that drove it and the ML
  shadow probability**, so a storm can be replayed afterwards.
- **Radar-cell ETA counts from now, not from the scan**, which is minutes old by the time
  it is read; the 20-minute "imminent" bar was being reached late.
- **"No risk" no longer shows as unknown.** WPC ERO and Google flash-flood entities
  rendered the string "None", which HA's MQTT sensor treats as a null state.
- New options: `rate_of_rise_window_minutes`, `max_stage_rise_in_min`, `ml_drives_alerts`,
  `creek_node_packets_entity`, `onsite_rain_total_entity`. A numeric option missing from an
  older install's options falls back to its default instead of failing at startup.

**HA packages (re-copy both `ha-packages/*.yaml`):**

- **Creek Alert** re-arms itself: `initial_state: true`, plus a companion automation that
  turns it back on after 2 h off. It had been switched off during node work and stayed off.
- **Escalations across a restart are announced.** It now compares with the last tier
  actually notified (`input_number.creek_last_notified_tier`) instead of ignoring every
  transition from `unknown`, which silently dropped Watch -> (restart) -> Emergency.
- **A removed entity no longer crashes it.** `to_state` is None when an entity is deleted;
  that raised on 2026-09-22 when the old Creek Modeling device was deleted.
- **Drops are held 15 minutes** before being announced, so a one-poll blip is one alarm,
  not an alarm and then an all-clear. Rises are never held.
- **Pause, don't disable.** `script.creek_pause_alerts` silences the phones for 2 h and
  re-announces the current tier when it ends (`script.creek_resume_alerts` ends it early).
- **Data problems reach the phones.** Telemetry stale, radar fault, service stale, stage
  stale/frozen/implausible and the rain-source watchdogs push to both phones (critical only
  for a blind gauge during an open storm), and clear on recovery. Before, all of them ended
  on the dashboard.
- The repo copy now matches the live install: the second phone's device id and the
  service-stale unique_id (a re-copy of the old repo file would have failed the whole
  automation at load).

**Firmware (node — press "Push Node Firmware" once CI has published it):** an unsettled
radar now reports no reading (a radar fault the gateway already counts) unless the creek
was already within ~12 in of the range ceiling, instead of reporting its last answer —
a 0 the gateway clamps to 37.6 in.

**Deploying:** Update the add-on. Copy `ha-packages/creek_warning.yaml`,
`ha-packages/creek_node_health.yaml` and `dashboards/creek_flood_watch.yaml` to `/config`,
then Developer tools -> YAML -> reload all, and run `script.creek_alert_test`. Push the node
firmware while the creek is quiet.

## 0.22.1

- **Fix: with `google_floods_api_key` left blank, the Google Flood source started anyway
  and every entity read `unknown`.** `bashio::config` renders an option the operator never
  filled in as the literal string `"null"`, not as an empty string — a fact this repo
  already knew, and already guarded `nwm_reach_id` against at its use site in
  `sources/__init__.py`. 0.21.0's new option did not get that guard, so an unset key was
  truthy, the source was built with the API key `"null"`, and every poll was refused by
  Google. `SourceCoordinator` catches a failing poll by design and keeps serving the last
  good value — of which there had never been one — so all four features sat at `None`
  forever, and the add-on log said `Google Flood Forecasting enabled`.

  The guard now lives in `config.py` (`_optional`) and covers every optional option
  — `google_floods_api_key`, `wu_api_key` and `nwm_reach_id` — rather than only the ones
  whose author happened to know about the trap. An unset key now correctly logs
  `Google Flood Forecasting disabled (needs google_floods_api_key)`.

- **A refused key now says what to check.** Unlike every other source here, this API needs
  a key *and* a one-time "enable the API" click on the Google Cloud project that issued it;
  a project that never enabled it answers 403 for a key that is otherwise perfectly valid,
  which reads as a bad key and is not. A 401 or 403 now raises a message naming both
  possibilities instead of surfacing as a bare `HTTPError` stack trace.

- **Reading the four gauge-status entities.** `Gauges watched` is the one to look at first:
  it is `0` when Google models no gauge within 25 mi (a real reading — see open question
  #2) and a count otherwise, but it is *never* `unknown` once a poll has succeeded. All
  four showing `unknown` means the source has not completed a single successful poll — it
  is either not configured or being refused, never "no flooding nearby". The same
  diagnosis applies to the flash-flood entities added in 0.22.0: they share this source
  and were exposed to the same "null" key.

## 0.22.0

- **Google Flash Flood polygon containment (spec Addendum C 2j, answers open question
  #2's flash-flood half — see `docs/open-questions.md` #2 for the gauge-thresholds
  residual that remains).** `google_floods.py` now also calls `flashFloods:search` and resolves each
  event's polygon geometry (`serializedPolygons/{id}`, parsed as KML —
  `app/sources/kml_geometry.py`) against the site's own coordinates, alongside the
  existing gauge-severity read. Unlike the gauge search, this reads the site itself, not
  a neighbouring river: `google_flash_flood_likely` / `google_flash_flood_highly_likely`
  (published as `Creek Google Flash Flood Status`) and `google_flash_flood_events`
  (`Creek Google Flash Flood Events`). Capped at Tier 2 Watch, same ceiling and reasoning
  as the existing gauge-severity rule — still a Google model forecast, not the creek's
  own instrument. No new config key; reuses `google_floods_api_key`.

## 0.21.1

- **Fix: every Google Flood card on the dashboard read "Entity not found."** 0.21.0's four
  Google Flood cards and its watchdog row were written with the
  `ackerly_creek_modeling_*` prefix, copied from the cards around them. That prefix is
  correct for those neighbours and wrong for anything new: Home Assistant freezes an
  entity_id at first registration and never recomputes it when a device is renamed, so
  entities that predate the "Rate of Rise" device rename (41e6caf) keep the old prefix
  forever, while entities registered *after* it — which is every entity 0.21.0 added — come
  out as `sensor.rate_of_rise_creek_*`. The five cards named entities that exist on no
  install, old or fresh, and so were dead from the first restart after the update. They now
  point at `sensor.rate_of_rise_creek_google_flood_{status,trend,gauge_distance,gauges}`
  and `binary_sensor.rate_of_rise_creek_google_flood_status_missing`, matching what
  `discovery.py`'s `entity_ids()` has said all along.

  **Re-copy `dashboards/creek_flood_watch.yaml`.** Nothing else changed — the add-on was
  publishing these entities correctly the whole time, so the data was there and only the
  dashboard was looking in the wrong place.

  `tests/test_dashboard_entities.py` could not have caught this: it generated a
  legacy-prefixed ID for *every* entity, so both prefixes validated for all of them. The
  legacy set is now restricted to the entities that actually have a legacy ID (frozen as
  `PRE_RENAME_SLUGS`), and a new test fails if the dashboard names a post-rename entity by
  a pre-rename ID. The dashboard's own header comment, which documented only the legacy
  prefix and is what the 0.21.0 cards were copied from, now spells out both rules.

## 0.21.0

- **Google Flood Forecasting is built (spec §3/§5, Addendum C slice 2i).** The
  `google_floods_api_key` option has existed since 0.1 with nothing behind it; setting it
  now enables a real source. It searches `gauges:searchGaugesByArea` for every gauge
  Google models within 25 mi of the site — virtual HydroBASINS gauges included, since on a
  creek this small those are the only plausible candidates — and reads
  `floodStatus:queryLatestFloodStatusByGaugeIds` for the nearest ten every 30 minutes.

  This is the only input here that forecasts *flooding* rather than weather: Google has
  already graded each reach against that reach's own warning/danger/extreme thresholds,
  which QPF, the ERO and the raw NWM discharge all leave to us. It is also the only one
  that can come back empty — if Google models no gauge near this creek, the new
  `Creek Google Flood Gauges` sensor reads 0 and the rest stay unknown. That number is
  the answer to open question #2, and the add-on log names each gauge it found, with its
  river, distance and quality-verification state, on every daily re-discovery.

  Four new features are recorded and published: `google_flood_severity` (0 no flooding ·
  1 above normal · 2 severe · 3 extreme), `google_flood_trend` (+1 rising / 0 steady /
  -1 falling), `google_flood_gauge_mi` and `google_flood_gauges`. The first three always
  describe the same gauge — the worst severity on offer, nearest first among equals — so
  they read as one forecast rather than three unrelated numbers. New entities:
  `sensor.rate_of_rise_creek_google_flood_status` (Google's own wording, not the ladder
  position), `..._trend`, `..._gauge_distance`, `..._gauges`, and a
  `..._google_flood_status_missing` watchdog. The bundled dashboard gains a card for
  them.

  **The tier ceiling for this source is Watch.** Google gauges neighbouring rivers, never
  this creek, so an above-normal river within 15 mi is an Advisory and a severe or extreme
  one is a Watch; Warning and Emergency stay reserved for the creek's own instrument. A
  status from further out than 15 mi, or one with no distance, fires nothing — the source
  searches wider than that because a 25 mi radius is the right width for a *model
  feature*, not for an alarm. `google_flood_severity`, `_trend` and `_gauge_mi` join the
  model's feature columns; `_gauges` deliberately does not, being a property of Google's
  coverage rather than of the weather.

  The API key travels in an `X-Goog-Api-Key` header rather than the documented `?key=`
  query parameter, so it cannot reach a log line or a traceback alongside a failed URL.

## 0.20.4

- **Fix: four USGS gauge cards on the dashboard pointed at entity IDs that never
  existed.** `dashboards/creek_flood_watch.yaml`'s "Downstream gauges (validation)" section
  referenced `sensor.*_usgs_leggetts_*` / `*_usgs_tunkhannock_*` — the internal site labels
  `discovery.py` uses for value templates and object IDs, not the display names
  ("Adjacent"/"Downstream") Home Assistant actually mints entity IDs from. Those four cards
  showed "Entity not found" on every install. Corrected to `*_usgs_adjacent_*` /
  `*_usgs_downstream_*`.

## 0.20.3

- **Fix: the service-stale watchdog kept the pre-rename name, so its entity ID never
  moved.** 0.19.0's notes say `"Creek Modeling Service Stale"` became
  `"Rate of Rise Service Stale"` (`binary_sensor.rate_of_rise_service_stale`). The
  `unique_id` was changed; the `name` was not, and for a template entity the `name` is what
  Home Assistant slugifies into the entity ID on first registration. A fresh copy of
  `ha-packages/creek_warning.yaml` therefore still created
  `binary_sensor.creek_modeling_service_stale`, while the bundled dashboard — updated in
  0.19.0 as promised — points at `binary_sensor.rate_of_rise_service_stale`. The Ingestion
  health card's service-stale row has been dead on every fresh install since.

  The name now matches the `unique_id`. **Re-copy `ha-packages/creek_warning.yaml`.** On an
  install that already registered this entity the ID is pinned in the entity registry and
  only the friendly name changes; rename it by hand (Settings → Devices & Services →
  Entities) if the dashboard row is still blank.

- **The retired `ewfa` project name is gone from everything that ships.** It survived the
  0.19.0 rename in four places, all of them outward-facing: the MQTT discovery device's
  `manufacturer` (shown on the HA device page, now `ryanbuiltthat`), and the `User-Agent`
  this add-on sends to the NWS forecast, NWS alerts and SNODAS APIs (now `rate-of-rise`).
  The startup log line said "Creek modeling service starting" and now names the add-on.
  None of this changes an entity ID: the discovery device's `identifiers` and `name` are
  unchanged, and they are what Home Assistant keys on.

- **Firmware:** the gateway's local ESPHome wrapper is `gateway.yaml`, not
  `ewfa_gateway.yaml` — it pairs with `gateway.base.yaml` beside it. Device identity lives
  entirely in the base config, so this renames a file and nothing else: same device name,
  same OTA target. Build with `esphome run gateway.yaml`; the first build after the rename
  recompiles from scratch.

  `LEGACY_SHARE_SUBDIR = "creek_modeling"` in `app/storms.py` is deliberately **kept** — it
  is how a storm log written before 0.19.0 is still found and migrated.

## 0.20.2

- **Fix: Rollback could not undo the first promotion** — `RegistryError: no history to
  roll back to`. `promote()` pushed the outgoing model onto `history` only when one
  already existed, so promoting the very first model recorded nothing behind it. The
  promotion with the least evidence behind it was the only one that could never be
  undone, and the sole way back was hand-editing `registry.json` on the HA host while
  the distrusted model kept driving the alarm.

  "No ML model" is now a real registry state rather than the absence of one: a history
  entry with a null version restores the threshold estimate, and `promote()` records it
  like any other. `rollback()` also returns to the threshold when a model is active with
  nothing behind it, which recovers registries already left in that state by the old
  code — no manual edit needed.

- **Promoting a model that was never validated now warns.** The first candidate this
  add-on produced scored `test_positives: 0` on 19 test rows — its held-out split had no
  positive examples in it, so hit rate, false-alarm rate and AUC were all undefined and
  nothing had checked the model at all. Promoting it raised a Tier 3 Warning on the next
  inference, because a promoted model's probability alone clears `WARNING_PROBABILITY`
  (Tier 3, 50%) and `EMERGENCY_PROBABILITY` (Tier 4, 80%). It did so silently.

  Promote still activates such a candidate — the call belongs to the operator — but it
  is no longer quiet about it. The caveat names the model, why nothing could score it,
  and what that costs, and it leads the command result the Last Command sensor shows.
  Because a command result scrolls away while the model keeps driving the alarm, the
  registry also publishes `active_validated`, an attribute of the Active Model sensor
  that stays false for as long as an unscored model is active; the dashboard's Model
  review card raises a banner off it. A candidate a held-out split *could* score
  promotes exactly as before, with no warning.

## 0.20.1

- **Fix: the dashboard's Retrain button failed outright** with
  `ValueError: DataFrame.dtypes for data must be int, float, bool or category.
  Invalid columns:soil_moisture_near_creek_pct: object`. The retrain reached xgboost
  for the first time — 17 storms on record cleared `min_events_for_ml`, and the creek
  node's stage data finally made the label mean something — and fell over on a dtype.

  `train.build_matrix` padded feature columns that were *absent* from the dataset with
  NaN, but not one that is present on every row and has never carried a value. The
  near-creek WH51 is exactly that: recorded each loop, always `None`. pandas reads a
  column of `None` back as dtype `object`, and xgboost's `DMatrix` rejects an object
  column outright rather than treating it as missing — the one thing xgboost was chosen
  for. Every feature column is now coerced with `pd.to_numeric(errors="coerce")` before
  training, so an unreported probe becomes NaN (missing) and an unparseable value
  becomes NaN instead of failing the whole retrain. Inference already did this
  (`model._ml_predict`); training now matches.

  No data was lost — the nightly batch consolidates before training, so the failing
  retrains still folded their part files into the dataset.

## 0.20.0

- **Fix: a dropped radio link to the creek node could manufacture a Tier 3 Warning.**
  Observed in the field — the node went quiet, the gateway was moved to recover the link,
  and the first reading back fired a critical flood alert. The cause: when the node stops
  answering, the gateway does not blank `sensor.creek_gateway_stage`, it just stops updating
  it, so Home Assistant keeps serving the last value the node sent.
  `FeatureBuilder._rate_of_rise` differenced the first fresh reading against that stale one
  and divided by a single loop interval, charging the entire outage's level change to five
  minutes: a creek that rose 3 in over a 40-minute dropout read as 0.6 in/min, twelve times
  `WARNING_RATE_OF_RISE_IN_MIN`. One reading was all it took — there was no gap check, no
  link check, and no confirmation.

  Rate of rise is now computed only from samples that can honestly be differenced:

  - **The link is checked first.** The new `creek_node_status_entity` option reads the
    gateway's packet-driven connectivity sensor (ON while the node's 60 s reports arrive,
    OFF after five are missed). While it is OFF, no rate is produced.
  - **Samples are timestamped by the reading, not the poll.** `last_updated` from Home
    Assistant is used, so the interval is the one the creek actually moved over.
  - **Gaps are never charged to one interval.** Beyond `rate_of_rise_max_gap_minutes`
    (default 10) the rate is withheld and the baseline re-seeded at the creek's current
    level, so the next loop measures real movement.
  - **The first samples back must be confirmed.** After a dropout, the rate must clear the
    threshold on `rate_of_rise_confirm_samples` (default 2) consecutive gap-free samples
    before it alone raises Tier 3. The counter saturates, so on a healthy link this adds no
    delay at all. Stage, model probability and the NWS floors are not gated — a creek that
    is genuinely high still warns on the first reading back.

- **Fix: the `Stage Stale` watchdog sat at OK through the whole dropout**, because it only
  ever asked whether `stage_ft` was `None` — and a stale-but-present number never is. It now
  also trips on a dead link (`creek_node_online`) or a stage reading older than its 30-minute
  threshold.

- **New:** `sensor.rate_of_rise_creek_stage_age` (diagnostic, minutes) — how old the reading
  behind the current rate of rise is. Added to the dashboard's *Ingestion health* card
  alongside the node's link state, so a blank rate of rise can be read as "the link just came
  back" rather than "the creek is not moving".

- **New options:** `creek_node_status_entity`, `stage_max_age_minutes` (6),
  `rate_of_rise_max_gap_minutes` (10), `rate_of_rise_confirm_samples` (2). Defaults suit the
  node's 60 s reporting and the gateway's 5-minute offline timeout; no configuration change
  is needed on update. Consider `fast_loop_minutes: 1` to match the telemetry cadence — it
  makes the post-reconnect confirmation cost about two minutes instead of ten.

## 0.19.1

- **Fix: no entity ever existed for the creek gauge's rate of rise.** Stage is published
  by the RFM69 gateway itself (`sensor.creek_gateway_stage`), but rate-of-rise
  (`FeatureBuilder._rate_of_rise` in `app/features.py`) was computed only for internal use
  by `tiers.py`/`model.py` — it was never added to the `creek/features` MQTT payload, so
  there was nothing for MQTT discovery to expose. Both stage and rate-of-rise are named as
  primary features in spec §1/§4; only stage ever actually reached Home Assistant.

  `rate_of_rise_in_min` now rides along in `creek/features` (added to
  `features.DERIVED_KEYS`, same mechanism `temp_f`/`rain_on_snow_flag` already use) and is
  auto-provisioned as **`sensor.rate_of_rise_creek_rate_of_rise`** (`in/min`) via MQTT
  discovery, alongside the existing gauge stage on the bundled dashboard's Ingestion health
  card. No configuration change needed — it appears automatically on update.

## 0.19.0

- **BREAKING — add-on renamed to Rate of Rise, top to bottom.** The add-on's official name
  is now "Rate of Rise" everywhere: `config.yaml`'s `name:` and `slug:` (`creek_modeling` →
  `rate_of_rise`), the repo folder (`creek_modeling/` → `rate_of_rise/`), the Python module
  path, the Docker image/container name, and the MQTT device (`identifiers: ["rate_of_rise"]`,
  `name: "Rate of Rise"` in `app/discovery.py`) and every discovered entity's `unique_id`
  (`creek_modeling_*` → `rate_of_rise_*`).

  **This is a reinstall, not an update.** Because the slug changed, Home Assistant sees a
  different add-on — there is no Update button to press. Uninstall the old "Creek Modeling"
  add-on and install "Rate of Rise" fresh from the repository (or as a local add-on at
  `/addons/rate_of_rise/`). Re-enter the site-specific options (`nwm_reach_id`,
  `upstream_pws_ids`, `nexrad_radar_id`) — they live in Supervisor's per-add-on storage, keyed
  by slug, and do not carry over.

  Because the MQTT device identifier also changed, the entities land under a new **Rate of
  Rise** device at `sensor.rate_of_rise_creek_*` / `binary_sensor.rate_of_rise_creek_*` (was
  `sensor.creek_modeling_creek_*`); the old **Creek Modeling** device and its entities linger
  as unavailable until deleted by hand (Settings → Devices & Services → MQTT → the old
  device → delete). Recorder history stays on the old entity IDs and does not migrate.

  The storm event log's `/share` subdirectory is renamed to match
  (`/share/creek_modeling/events.sqlite` → `/share/rate_of_rise/events.sqlite`); unlike the
  rest of this change, that file *is* migrated automatically on first start of the new
  install — `_resolve_db_path` (`app/storms.py`) now checks the old subdirectory the same
  way it already checked `/data` for a pre-`/share` log, so the annotated history is not
  stranded by the rename.

  The HA-package watchdog that cannot move into the add-on is renamed to match: "Creek
  Modeling Service Stale" → **"Rate of Rise Service Stale"**
  (`binary_sensor.rate_of_rise_service_stale`, was `binary_sensor.creek_modeling_service_stale`)
  in `ha-packages/creek_warning.yaml` — re-copy that file. The bundled dashboard
  (`dashboards/creek_flood_watch.yaml`) is updated to the new entity IDs throughout; re-copy
  it too.

## 0.18.0

- **Creek stage is connected again.** `stage_entity` defaulted to `sensor.creek_stage`,
  which was published by the ESPHome creek node that the Moteino + RFM69 gateway replaced.
  Nothing re-pointed it, so the entity simply did not exist: `get_float` returned None, the
  pipeline reported `stage_stale` and fell back to the USGS proxy, and **tiers 3-4 could
  never fire** — all while every watchdog stayed green, because "no stage" is
  indistinguishable from "gauge not built yet". The gateway now does the distance→depth
  conversion and publishes `sensor.creek_gateway_stage` (ft) and
  `sensor.creek_gateway_creek_depth` (in), and that is the new default.
  `test_esphome_entities.py` was rewritten against the gateway config so this class of
  break fails the build instead of failing silently in production.

- **Site-identifying defaults are no longer committed.** `nwm_reach_id`,
  `upstream_pws_ids` and `nexrad_radar_id` now ship blank and are set in the add-on's own
  configuration, which Home Assistant stores outside this repo. **A running install is
  unaffected** — HA keeps the options you already configured. A fresh install must fill
  these in; blank `nexrad_radar_id` disables cell tracking with a warning rather than
  querying a wrong radar.

- **BREAKING — USGS entity IDs renamed.** The two reference gauges were named after their
  watercourses; they are now `…_creek_usgs_adjacent_*` and `…_creek_usgs_downstream_*`.
  The bundled dashboard is updated. If you reference these in your own dashboards,
  automations or templates, update them; recorder history for the old IDs stays under the
  old names. **Dataset/feature keys are deliberately unchanged** (`usgs_leggetts_*`,
  `usgs_tunkhannock_*`) — they name columns in the stored Parquet and in every feature
  list in `train.py`, so renaming them would strand the historical dataset.

- **BREAKING — MQTT device renamed and re-identified.** The device is now "Creek Modeling"
  with identifier `creek_modeling` (it was named for the watercourse, which named the
  location). Freshly discovered entities land at `…creek_modeling_creek_*`; the bundled
  dashboard and package are updated to match.

  **What to expect on an existing install:** because the device *identifier* changed, Home
  Assistant treats this as a new device rather than a rename. The new device appears with
  the new entity IDs, and the old device and its entities linger as unavailable until you
  delete them (Settings → Devices & Services → MQTT → the old device → delete). Recorder
  history stays attached to the old entity IDs and does not migrate — long-term statistics
  for the model outputs start fresh. This is a one-time cost, taken deliberately at v1
  rather than later when there would be more history to strand.

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

## 0.16.0

- **WPC Excessive Rainfall Outlook (slice 2h)** — the Weather Prediction Center's day
  1-3 categorical flood-risk areas (Marginal / Slight / Moderate / High), as a point
  query via IEM's `outlook_by_point` service. Free, no key, and — the reason this was
  worth doing at all — **no shapefile parsing and no polygon math**: IEM answers "which
  outlooks cover this point", so there is no geometry of ours to get subtly wrong.

  This is the only input in the system that grades forecast rain against **flash flood
  guidance** — against what the ground can currently absorb — instead of reporting rain
  in inches. Everything else answers "how much water"; the ERO answers "is that much
  water a problem here, today", folding in antecedent conditions and regional soil state
  that two buried probes cannot see. It also completes the horizon ladder: the ERO is
  the **day**-scale signal (it exists before anything is on radar), 2g's cell tracks are
  the **hour**-scale one, the gauges are the **now**-scale one.

  Day 1 feeds the Advisory tier — Moderate or High on its own, Slight only over
  already-wet ground, since Slight over dry ground is a summer commonplace and firing on
  it would make Advisory the permanent state. Days 2-3 are model features only.

  Verified against the live service while writing it: the site was under a **Day-1
  Slight** during the very storm that had raised the Watch tier, from an outlook issued
  that morning.

- **Fixed: the radar cell features were never model inputs.** 0.13.0 added
  `radar_cells_tracked`, `radar_threat_cells`, `radar_threat_eta_min` and
  `radar_threat_max_dbz` — published to Home Assistant and written to the dataset, but
  never added to `train.py`'s `FEATURE_COLUMNS`. That list is deliberately explicit so
  stray columns cannot become inputs by accident, and the cost of that choice is that a
  new feature is invisible to the model until it is named there. So the one input that
  leads on the dominant W/NW storm approach was excluded from the thing meant to predict
  that approach. Both the radar and ERO columns are now inputs; the existing padding
  logic means datasets predating either column still train.

  Nothing needs retraining by hand — the nightly job picks the new columns up. Rows
  recorded before this release carry nulls for them, which xgboost handles natively.

- Along with the source: an `ero_outlook_missing` watchdog, three dashboard rows
  ("Excessive rainfall outlook (WPC)" on Flood Watch), a `wpc_ero` option, and 15 tests.
  Absent risk areas are recorded as `0.0` (WPC looked and drew nothing — a real
  low-risk forecast); only an unreadable product is `None`, so the watchdog can tell a
  quiet day from a broken feed, and an unrecognised future category stays `None` rather
  than being silently downgraded to "no risk".

## 0.15.0

- **The Do Not Disturb instructions were wrong, and that is why nothing was audible.**
  Earlier releases said DND bypass was Android's default and only wanted confirming. The
  companion app documentation says the opposite: notifications "do not override Do Not
  Disturb settings" unless a *notification channel* is granted permission to, and that
  permission can only be given by hand on the phone —
  Settings → Apps → Home Assistant → Notifications → **alarm_stream** → override Do Not
  Disturb. No YAML in this repo can set it or read it back. It is now documented as
  required setup, first, before the test, rather than as a footnote.

  Two consequences are documented with it, because both are silent traps: Android fixes
  a channel's importance and sound the first time the channel appears and ignores every
  later change ("only lowering of the importance will work"), and renaming a channel
  starts a fresh one with no permission — which on the phone is indistinguishable from
  the alarm having broken.

- **`script.creek_alert_test` — the dry run is now one action.** The previous procedure
  asked the operator to set `critical_from_tier: 0`, reload, trigger, and set it back.
  Four steps around the thing being tested, and skipping the first silently turns the
  test into a *non-critical all-clear* — which proves nothing while looking exactly like
  a failure, and is the likeliest reason the last round appeared to do nothing.

  The script sends the real critical payload to every configured phone, reusing the same
  YAML anchor as the alert itself, so what it tests is by construction what a Tier 4
  sends. It carries its own notification tag, so testing never replaces a live alert on
  the phone. Four tests hold that contract: the dry run must be genuinely critical, reach
  exactly the same phones, send a byte-identical payload, and use a different tag.

  Run it from Developer tools → Actions → `script.creek_alert_test`, with DND on. DOCS.md
  has a symptom table for what each outcome means.

## 0.14.4

- **Fixed: the alert did nothing when tested — and could not be tested at all.** Every
  variable in the tier automation read `trigger.to_state` unconditionally. A manual run
  (Developer tools → Actions → `automation.trigger`) provides no `trigger` variable, so
  rendering raised `UndefinedError: 'trigger' is undefined` while the variables were
  still being assembled — before the first action, before even the persistent
  notification. The automation silently did nothing. This was present in 0.14.1 through
  0.14.3, underneath the two separate device-targeting bugs those releases fixed, so
  every "re-copy and test" round hit it and reported the same nothing.

  Variables now fall back to reading the entity live when there is no trigger, so a
  manual run is a real dry run describing current conditions. When a trigger *is*
  present it still wins, preserving the original guarantee that an alert describes the
  state that fired it rather than whatever the entity has since moved on to. The
  trigger-churn condition is guarded the same way, so a manual run is never filtered out.

- **The tests now render the automation instead of only inspecting its shape.** The
  reason three consecutive releases shipped broken is that every existing test checked
  structure — is the channel `alarm_stream`, is `device_id` a literal — and all of them
  passed against all three broken versions. `render_variables()` builds the variables in
  order the way Home Assistant does, with and without a trigger, and
  `test_a_manual_run_builds_its_variables_instead_of_dying` reproduces the exact
  `UndefinedError` against the 0.14.3 file. Two companions check that a real trigger
  still wins over live state, and that tier 0 does not come through as an alarm.
  - `pip install pyyaml jinja2` in CI (both test-only; neither belongs in the add-on
    image).
  - **The dry-run procedure in DOCS.md was also wrong** and is rewritten: a manual run
    reports the *current* tier, normally 0, so it sends a quiet all-clear and proves
    nothing about the alarm path. Temporarily set `critical_from_tier: 0`, reload, test
    with DND on, then set it back.
  - Re-copy `ha-packages/creek_warning.yaml`.

## 0.14.3

- **Fixed: 0.14.2's device-action push also failed to load** — Home Assistant reported
  `Unknown device '{{ repeat.item }}'` and the automation was disabled outright, same as
  0.14.1's failure before it. The cause was structural, not a naming mistake this time:
  Home Assistant resolves a device action's `device_id` when the automation is *set up*,
  before any per-iteration template rendering happens. The literal string
  `"{{ repeat.item }}"` was therefore never evaluated as a template at all — it errored
  as a malformed device id. `repeat`/`for_each` cannot drive a device action's
  `device_id`; only the payload around it (`title`, `message`, `data`) can be templated.

  Each phone is now one explicit, non-templated action — the same static-per-instance
  shape a real, working blueprint actually emits (confirmed by reading
  SgtBatten/HA_blueprints' Frigate notifications, which never templates `device_id`
  either). Content is shared between the two phones with a plain YAML anchor
  (`&creek_push` / `<<: *creek_push`) rather than a runtime loop, so the notification
  text and critical-alert payload are still written once, not duplicated per phone.

  Three tests target this specific failure mode: `device_id` must never contain a Jinja
  template, no `repeat` step may appear anywhere in the actions (a structural guard, not
  just a content check), and every configured phone must have its own independent
  action. All three were verified to fail against a reconstruction of the 0.14.2 shape.
  Templates were also hand-rendered end-to-end for both phones and both a quiet and a
  critical tier before committing, rather than trusted to merely parse.

  Adding a phone is now: copy one whole action block, change its `device_id`. There is
  no longer a separate `notify_targets` list to keep in sync with — the action blocks
  are the only configuration surface, so there is nothing left to drift.
  - Re-copy `ha-packages/creek_warning.yaml` yet again — this is the third time in a
    row, and the last one: the automation is now covered by tests that catch this exact
    class of failure before it ships.

## 0.14.2

- **Fixed: 0.14.1's companion-app push failed outright** — Home Assistant reported
  `automation.creek_alert_tier_changed has an unknown action: notify.ryanphone` and the
  automation never ran. The design assumed each companion-app phone has a fixed,
  guessable `notify.<name>` service; it does not, reliably, and there is no way to
  derive that string from a device without asking Home Assistant to resolve it.

  Targets are now Home Assistant **device ids** (`notify_targets` in the automation's
  variables — find one under Settings → Devices & Services → Devices → click the phone →
  the URL ends in the id), fired through a *device action*
  (`device_id`/`domain: mobile_app`/`type: notify`) instead of a templated service call.
  This is the same mechanism community blueprints use for mobile notifications (e.g.
  SgtBatten/HA_blueprints' Frigate notifications) — Home Assistant resolves the id to
  the right notify call internally, so nothing here has to know or guess a service name.
  The critical/`alarm_stream` payload (`channel`, `importance`, `tag`, `ttl`, `priority`)
  carries over unchanged; only `title`/`message` moved from nested under `data:` to
  top-level, which is where a device action expects them.

  `test_targets_are_device_ids_not_notify_service_names` and
  `test_the_push_is_a_device_action_not_a_templated_service_call` pin the shape; both
  verified to fail against the 0.14.1 form. Two targets are configured out of the box
  (both household phones) rather than one, since this was already a multi-target list.
  - Re-copy `ha-packages/creek_warning.yaml` as with 0.14.1 — this does not arrive with
    an add-on update.

## 0.14.1

- **Alert tier changes now push to the companion app, critically** — until now the tier
  automation only posted a notification inside the Home Assistant UI, which nobody is
  looking at when it matters.

  Targets are a configurable list at the top of the automation (`notify_targets`), so
  more phones are one line each, and the push fans out with `continue_on_error` — one
  offline or misspelled target must never stop the others being told.

  At or above `critical_from_tier` the push goes out on Android's `alarm_stream` channel,
  which is what carries the sound through silent, vibrate and Do Not Disturb, and it
  sticks on screen until acknowledged. **The default is tier 2 (Watch), not 3 (Warning),
  deliberately:** tiers 3-4 need the creek gauge, which is not mounted, so a floor of 3
  would mean no critical alert could physically fire — the feature would look wired up
  and never make a sound. Watch is the most urgent tier reachable today, and in a basin
  where rainfall-to-crest is tens of minutes (spec §1) it is the one worth waking for.

  A constant `tag` means each tier change *replaces* the previous notification instead of
  stacking; the all-clear overwrites the alarm rather than sitting beneath it. The alert
  text is read from the triggering state rather than re-read from the entity, which was a
  latent race: the tier can move on before the actions run, and the push would then name
  a tier different from the one that fired it.

  Seven tests in `test_alert_notifications.py` pin the parts that would silently render
  this useless — no targets, a critical floor above any reachable tier, a channel that is
  not the alarm stream, a fan-out that stops at the first dead phone.
  - This is in the HA package, so it does **not** arrive with an add-on update — re-copy
    `ha-packages/creek_warning.yaml` into your HA config directory and reload automations.
  - **Confirm it out loud once, before relying on it.** Android's Do Not Disturb must be
    allowing alarms through for `alarm_stream` to be audible. That is the default, but it
    is a device-side setting this repo cannot set or verify — see DOCS.md.

## 0.14.0

- **Fixed: a restart mid-storm could close the storm on the next lull, and stamp it as
  having ended the moment it began.** The quiet clock — the "how long since it last
  rained" that decides when an event ends — was an instance attribute, so a restart reset
  it to `None` and the fallback became the storm's own `started_ts`. For any storm open
  longer than the quiet window (an ordinary all-day rain), that fallback read as a full
  window of quiet *already elapsed*, so the first sample below the pause threshold closed
  the event immediately. It then recorded `ended_ts = started_ts`: a storm of zero
  duration, in the record the lag analysis is built from.

  The anchor now lives on the row (`last_rain_ts`, added by the usual additive
  migration), so it survives restarts, and the event still ends when the rain stopped
  rather than when the service noticed. A row with no anchor — one opened by the older
  code, including a storm open at upgrade time — is treated as *unknowable* rather than
  as quiet-since-onset: the clock restarts instead of closing on a guess. Four tests
  cover it, each verified to fail against the previous implementation.

- **Storm detection thresholds are now add-on options**, because they define what counts
  as one storm and that cannot be settled without storms on record: `storm_start_rain_1h_in`
  (default 0.10 in/h), `storm_continue_rain_1h_in` (0.02), `storm_quiet_hours` (6). Too
  long a quiet window merges an afternoon's storms into the morning's; too short splits
  one storm in two. Either way the lag it teaches is wrong, so they belong in the UI
  rather than in the source. The configured values are logged at startup.

  Note what this cannot fix: the close test takes the *stronger* of the on-site and
  upstream 1 h rain, so a single stuck PWS reporting a phantom rain rate holds every
  storm open indefinitely — the quiet clock never starts, and no window length helps.
  The runbook now says how to spot that.

## 0.13.0

- **NEXRAD storm-cell tracking (slice 2g)** — inbound-cell early warning from `<nexrad site>`'s
  Level 3 storm-attribute table (the same SCIT product radar apps draw as cell markers
  with track vectors), via the Iowa Environmental Mesonet's CSV service. Free, no key,
  updates every volume scan (~4–6 min in precip mode).

  Why: storms here typically arrive from the W/NW, but the upstream PWS corridor lies to
  the **SE** — geometrically behind the house for the dominant approach. A convective
  cell on the usual track therefore hits the house *before* any upstream gauge sees rain,
  and gridded QPF cannot resolve convection between forecast cycles. Radar cell tracks
  are the only input that leads regardless of approach bearing.

  Each intense cell (≥ 40 dBZ) is tested for closest approach against the site using its
  SCIT motion vector: within 4 mi (covering the house and the short SE upstream corridor)
  and inside 90 min makes it a threat; a threat inside 45 min raises the **Watch** tier —
  the same meaning as "upstream rain, nothing at the gauge yet", just earlier. New
  sensors: cells tracked, cells on intercept, soonest ETA, strongest inbound dBZ, plus a
  `radar_cells_missing` watchdog, dashboard rows ("Inbound cells (radar)" on Flood Watch),
  and options `nexrad_cells` / `nexrad_radar_id`.

  The Level 3 `DRCT` field is the direction a cell comes **from** (wind convention), not
  the direction it moves toward. That was verified empirically before trusting it —
  regressing 45 live `<nexrad site>` storm tracks' actual centroid displacement against their
  reported `DRCT`: the from-convention matched all 45, the toward-convention none. A test
  pins the convention with a hand-computed geometry case, since getting it backwards
  silently inverts every threat call. The parser was also smoke-tested against a real
  4-hour, 1038-row `<nexrad site>` download from this evening's storm; it flagged 2 cells inbound at
  ~2 min / 42 dBZ for the exact window when rain was falling on the site.

## 0.12.2

- **Fixed: the annotation text box from 0.12.1 never actually existed in Home Assistant.**
  Its discovery config set `max: 500`, but 255 is Home Assistant's hard ceiling for MQTT
  text entities (`homeassistant.components.text`'s `native_max_value` is clamped to
  `(0, 255)`, inherited by every platform including MQTT). A `max` above that is not
  clamped down to fit — MQTT discovery validates the payload against the platform schema
  *before* creating anything, so the whole discovery message for that one entity was
  rejected and silently dropped. Every other entity in this release has no such
  constraint, so nothing else was affected and nothing in the add-on's own logs pointed at
  it — confirmed missing only from the HA-side MQTT device page, not from anything this
  service could see about itself.

  Set to 255. A `test_annotate_text_entity_max_is_within_the_mqtt_text_platform_ceiling`
  guard now checks every entity with a `max` against that ceiling, verified to fail on the
  0.12.1 config before this fix.

## 0.12.1

- **Annotate storms from the Operator tab** — no more SSH/Samba/`sqlite3` for the normal
  case. A new "Creek Storm To Annotate" sensor names the storm a note will apply to
  (`Storm #<n>`, or `none yet`); type into the "Creek Annotate Latest Storm" text box and
  hit enter to write it.

  Targets the most recent **closed** event (`StormLog.latest_closed`), not simply the
  newest row: annotation happens the day after a storm closes (spec §7), by which point a
  second storm may already be open, and the newest row would then be the wrong one — the
  operator's "the storm I watched yesterday" stops being `latest()` the moment that
  happens. `latest_closed` is exactly "the most recent event with `ended_ts` set", and the
  confirmation sensor reads the same field the write path uses, so what you see before
  typing is what actually gets annotated.

  The `sqlite3`/Samba path (storm-runbook.md) stays for what the text box can't do:
  annotating an older un-annotated storm, or correcting a note already saved.

- **MQTT command payloads are no longer discarded.** Every command before this one was a
  zero-argument button, so `CommandQueue`/`CommandProcessor`/`_on_message` never carried a
  payload past the topic's command name — correct for those four, but it meant `annotate`
  had nowhere to put its text. `offer`/`drain`/`handle` now carry `(command, payload)`
  throughout; the four existing handlers pick up an unused `payload` argument and are
  otherwise unchanged.

## 0.12.0

- **Phase 4 training pipeline built** (`app/train.py`, spec Addendum D). Fits an xgboost
  classifier predicting "Warning-tier exceedance within +3 h" from the live feature set,
  on a chronological split with an embargo around the boundary so a storm straddling it
  cannot leak between train and test, class-imbalance handled via `scale_pos_weight`
  rather than resampling, and skill metrics (hit rate, false-alarm rate, ROC AUC, mean
  lead time) computed on the held-out split. Wired into the nightly batch: gated first by
  `min_events_for_ml` (unchanged), then by a minimum of positive examples in the label —
  the label depends on `stage_ft`/`rate_of_rise_in_min`, which are `None` until the
  SEN0676 exists, so in the live system today this second gate never opens. That is
  correct, not a bug: a model fit on zero positive examples has learned "always predict
  no," which is worse than the threshold estimate with more confidence attached.
  **Code-complete and unit-tested against synthetic storms
  (`tests/test_train.py`, `tests/test_model.py`); has never seen real data.**

- **`Model` actually loads and runs the promoted artifact now** (`app/model.py`).
  `_load_promoted` was a stub since Phase 2 ("loading deferred to Phase 4"); `_ml_predict`
  raised `NotImplementedError`. Both are real. Serialization is xgboost's native JSON
  format rather than the `.pkl` this repo's docs originally specified — a pickle ties the
  artifact to the exact xgboost build that wrote it and executes arbitrary code on load,
  a poor trade for a format with one consumer.

- **Fixed: Promote/Rollback would have silently done nothing.** Implementing real loading
  surfaced a gap that a stub couldn't: `Model` loaded its artifact once at construction,
  while the dashboard's Promote/Rollback buttons only ever mutate the shared
  `ModelRegistry` — a promotion would not have taken effect until the add-on restarted,
  and nothing would have said so. `Model` now re-checks the registry's active version on
  every prediction (one string compare) and reloads when it changes.

- **Fixed a real crash caught by the new tests, not shipped:** building the single-row
  frame for live inference from a `FeatureRow` with any `None` field produced an
  `object`-dtype pandas column, which xgboost's `DMatrix` rejects outright rather than
  treating as missing. Since most rows have at least one unconfigured/unanswered source,
  this would have failed on close to the first real inference attempt once a model was
  ever promoted. Fixed by coercing to float/NaN before handing off to xgboost.

- `active_method` (published as `model_health.active_method`) is now read from `Model`
  itself rather than re-derived from `events >= min_events_for_ml` in `__main__.py` — the
  two could previously disagree (gate satisfied, no artifact actually loaded) and now
  cannot, by construction.

## 0.11.8

- `onsite_rain_rate_entity` **confirmed** as `sensor.outside_weather_station_rain_intensity`
  by the owner. This is the one that matters: `rain.py` integrates rate x dt into the
  rolling `rain_{1,3,6,24,72}h_in` accumulations, which drive Tier 2 Watch and feed the
  Antecedent Precipitation Index.
- `onsite_rain_daily_entity` set to `sensor.outside_weather_station_rain_24hr`, and
  **documented as unused**: it is parsed into `Config` and read by nothing — no source
  polls it and no feature derives from it. The rolling accumulations come from the rate
  entity, not from here. Setting it has no effect on anything today; it renders a form
  field that does nothing. Left in place rather than removed so stored options are not
  invalidated — wiring it up (as a midnight-reset `rain_today_in` cross-check against the
  integrated accumulator) or dropping it are both open.

## 0.11.7

- **Soil probe mapping corrected — the two probes were swapped in 0.11.6.** The willow is
  by the house, the field probe is the one near the creek, confirmed by the owner. The
  original `config.yaml` comment ("near the house, by the willow tree") had it right all
  along; the labels in `84ff4f5` transposed them, and 0.11.6 propagated that.

  | | Entity |
  |---|---|
  | `soil_moisture_entities[0]` → `soil_moisture_near_house_pct` | `..._soil_moisture_willow` |
  | `soil_moisture_entities[1]` → `soil_moisture_near_creek_pct` | `..._soil_moisture_field` |

  Nothing downstream distinguishes the two probes today — the ponding flag and the Tier 1
  soil condition both run off the mean, which is unaffected by the order. It matters for
  the per-probe dataset columns and for the WH51 field calibration (open question #7),
  where "saturated" for a probe by the house is not the same number as one near the creek.
  Dashboard labels corrected to match.

- **`onsite_temp_entity` set to `sensor.outside_weather_station_outdoors_temp`**, resolving
  the one Ecowitt ID left guessed in 0.11.6. This is the rain-on-snow flag's temperature
  input (spec §1), which could not have fired with a dead entity.

## 0.11.6

- **Ecowitt entity defaults corrected.** `84ff4f5` fixed the dashboard to the real entity
  IDs; the add-on defaults, docs and the dashboard regression test still held three older
  spellings between them. All now agree:

  | Option | Was | Now |
  |---|---|---|
  | `onsite_rain_rate_entity` | `sensor.weather_station_rain_rate` | `sensor.outside_weather_station_rain_intensity` |
  | `onsite_rain_daily_entity` | `sensor.weather_station_daily_rain` | `sensor.outside_weather_station_rain_daily` |
  | `soil_moisture_entities[0]` | `..._soil_moisture_1` | `..._soil_moisture_field` (near house) |
  | `soil_moisture_entities[1]` | `..._soil_moisture_2` | `..._soil_moisture_willow` (near creek) |

  **These are install-time defaults only — a running add-on keeps the options already
  stored, so check the Configuration tab.** A rain entity pointing at an ID that does not
  exist reads as no data, and every on-site rain feature (`rain_rate_in_hr`,
  `rain_{1,3,6,24,72}h_in`, and the API index that rides on them) stays null. Tier 2 Watch
  is driven by exactly those, so it cannot fire. `test_dashboard_entities.py` was the only
  thing that caught the drift, and only for the dashboard.

- `onsite_temp_entity` is **left at `sensor.weather_station_outdoor_temperature` and is
  probably also wrong** — the correct ID was not among the ones corrected upstream. It
  feeds only the rain-on-snow flag, so nothing is affected until winter.

## 0.11.5

- **QPF no longer decays as a forecast storm approaches.** Proration now runs over each
  interval's *remaining* time, so the interval already in progress contributes its full
  forecast instead of a share scaled by how much of it has elapsed.

  Prorating over the full span quietly assumed the un-elapsed remainder had already
  fallen. BGM issues this grid in **6-hour blocks**, so that assumption governed most of
  a 6 h forecast: a forecaster's 0.15" placed in the 18:00–00:00Z block for an evening
  thunderstorm shrank steadily through the afternoon and bottomed out just as the storm
  arrived — the wrong direction of error for a flashy basin (spec §1). Rain that really
  had fallen was never lost; the on-site gauge measures it and it reaches the tiers as
  `rain_*_in`.

  Measured against the live BGM 76,32 grid: `qpf_6h_in` 0.041 → 0.158, `qpf_24h_in`
  0.059 → 0.176. Trailing-edge clipping is unchanged — an interval running past the
  window is still cut at the window, or a 6 h figure would count rain forecast outside
  the next 6 hours.

- **Standing limitation, unchanged by the above:** gridded QPF cannot resolve convection.
  A pop-up thunderstorm reads near zero regardless of proration, so `qpf_*` is a
  frontal-rain signal. The on-site rain rate (sampled every fast loop) is the only
  nowcast, and NWS alerts (5 min) are the only forecaster-in-the-loop signal.

## 0.11.4

- **Storm annotation actually works now.** Two separate defects made the documented
  Phase 3 step a no-op, and neither announced itself:
  - The `sqlite3` **CLI** was never installed in the image — only the Python module, which
    shares the name and made the omission easy to miss. `sqlite3 …` inside the add-on
    container was "command not found". Now installed.
  - Worse, `/data` is **private to each add-on**. Running the documented
    `sqlite3 /data/events.sqlite` in the SSH/Terminal add-on opens *that* add-on's `/data`,
    creating an empty database and annotating it happily — no error, and storms could go
    un-annotated for months before anyone noticed. The storm log now lives at
    **`/share/creek_modeling/events.sqlite`**, one path that resolves the same way from
    every add-on and over Samba.
- **Existing logs migrate automatically** on first start (moved, not copied — a copy would
  leave the service writing one file while a human annotates another). If `/share` is
  unavailable the add-on warns and keeps using `/data` rather than failing to record
  storms. The resolved path is logged at startup as `Storm event log at …`.
- **5 s busy timeout** on the event log. A person editing by hand is now an expected
  concurrent writer, and SQLite's default is to fail instantly on a locked database rather
  than wait out a write that takes under a millisecond.

## 0.11.3

- **Prime the dashboard at startup.** `model_health` and the lag estimate were published
  only by the nightly batch, so `Creek Model Health`, `Creek Dataset Rows`, `Creek Lag
  Estimate` and `Creek Lag Response Series` sat at *unknown* until 3 AM on a fresh install
  or after any restart — waiting a few minutes never helped. All four are now published once
  at startup. (`creek_lag_estimate` regressed in 0.11.0, which moved the publish out of the
  fast loop; the other three have been this way since the beginning.)
- **The lag estimate persists** to `/data/state/lag.json`, so a restart republishes the last
  computed value instead of a placeholder. Before the first nightly run it publishes a
  "pending the first nightly run" reason rather than nothing.
- **Bound the lag fit to the most recent 90 days.** Cost grew linearly with the dataset —
  measured at 15.5 s for a year of 5-minute samples and unbounded after that. It is now flat
  at ~3.5 s no matter how long the record gets. This is also the better estimate: a lag
  averaged across seasons is the wrong number, since March snowmelt on saturated ground is
  not the same warning window as an August thunderstorm. The window is measured from the
  newest sample rather than wall-clock, so a restored or replayed dataset still fits.

## 0.11.2

- **Fix five dashboard cards showing "Entity not found" on a healthy system.** Home
  Assistant mints entity IDs from the device name plus the entity *name*, not from the
  `object_id` the add-on publishes — `object_id` is a suggestion and is not honoured here.
  Most entities hid this because their name slugifies to exactly their object_id, so both
  rules agree by coincidence. Five did not, and the dashboard referenced the object_id form:
  - `creek_nws_alerts_missing` → actually `..._creek_nws_alert_feed_missing`
  - `creek_last_nightly` → `..._creek_last_nightly_run`
  - `creek_api_index` → `..._creek_antecedent_precipitation_index`
  - `creek_usgs_leggetts_gage` → `..._creek_usgs_leggetts_gage_height`
  - `creek_usgs_tunkhannock_gage` → `..._creek_usgs_tunkhannock_gage_height`
- `DiscoveryPublisher.entity_ids()` now derives the real IDs, and a new test checks every
  entity the dashboard references against the add-on's entities, the HA package's, and a
  short allow-list of Ecowitt/ESPHome ones. Verified to fail when the old reference is
  restored, so it is not passing vacuously.
- The `object_id` values are deliberately left alone: they form each entity's `unique_id`,
  so changing them would orphan the entities already registered in Home Assistant.

## 0.11.1

- **Fix the `Creek Modeling Service Stale` watchdog** (`ha-packages/creek_warning.yaml`).
  It inferred liveness from `last_changed` on the pipeline-state entity, which only toggles
  idle/running for a moment each loop — if Home Assistant ever missed that brief transition,
  last_changed froze and the watchdog stuck at Problem indefinitely. It now compares the
  *value* of `creek_last_inference`, which advances every fast loop by construction. Adds a
  `last_inference` attribute so a Problem can be told apart at a glance: entity missing
  versus loop genuinely stalled.
  - This watchdog is the one thing left in the HA package, so it does **not** arrive with an
    add-on update — re-copy `ha-packages/creek_warning.yaml` into your HA config directory.
  - A copy of that file predating 0.6.0 referenced the unprefixed
    `sensor.creek_pipeline_state`, which never existed, so it reported Problem permanently.

## 0.11.0 — Phase 3 (Collect & correlate)

- **Storm event log** (`app/storms.py`, spec §7): detects storms from the live feature
  stream and records them in `events.sqlite` with onset conditions (API, soil moisture,
  SWE, rain-on-snow), running peaks (rain, upstream rain, QPF, stage, rate-of-rise,
  downstream rise, alert tier) and a `notes` column for annotation. Events are defined by
  *rainfall*, not by creek response, so the record being built now stays valid once the
  SEN0676 is mounted — response columns are simply null until then. A brief lull does not
  split one storm in two, and a restart mid-storm resumes the open event.
  - This also makes `min_events_for_ml` meaningful for the first time: the table existed
    but nothing ever wrote to it, so the threshold→ML gate (§5) could never open. The
    nightly batch now sets the registry's event count from completed storms.
- **Nightly dataset builder** (`app/dataset.py`, §4): the fast loop appends to a per-day
  JSONL part file and the nightly batch folds completed parts into `dataset.parquet`.
  Previously every fast loop read the entire Parquet dataset, concatenated one row and
  rewrote it — ~700 ms per append after a year on this schema, but the real cost was
  rewriting the whole dataset 288 times a day onto an SSD. Consolidation is atomic
  (write-temp-then-replace), de-duplicates by timestamp, and survives a torn final line
  from an unclean shutdown without losing the rest of the day.
- **Lag estimation** (`app/lag.py`, §1/§7): cross-correlates rainfall against the response
  series to estimate the rainfall→crest lag §1 calls "the warning window". Correlates on
  first *differences* of the response, since rainfall drives the rise while the level
  itself is dominated by baseflow. Falls back to a USGS downstream gauge while the creek
  node is missing — a different, larger basin, so the result is labelled with the series
  used and must not be read as the creek's lag. That fallback is the "downstream-gauge
  sanity comparison" §7 asks for.
  - Near-ties resolve to the *shorter* lag. Storms recur, so shifting rain forward by a
    whole inter-storm interval lines it up with the next storm's rise and scores just as
    well — measurably better, even, since the longer shift drops the worst-fitting edge
    samples. Physically the earliest lag that explains the response is the causal one.
  - Refuses to guess: reports `lag_minutes: null` with a reason when there is too little
    data, no rain on record, or the best correlation is below 0.30.

## 0.10.0

- **Watchdogs and soil templates move into the add-on.** The soil-moisture mean, the ponding
  flag and eight of the nine sensor-fault watchdogs are now computed here and auto-created
  via MQTT discovery, so they need no file copied into the HA config directory and no
  `configuration.yaml` edit. `ha-packages/creek_warning.yaml` shrinks to just the tier
  notification automation (it calls `persistent_notification`, so it has to be HA-side) and
  the add-on's own liveness watchdog — a service cannot report its own death.
- **The watchdogs are also more accurate here**, because the add-on can see two things a
  template could not:
  - *Whether a source is still alive.* The coordinator serves a source's last-good value
    indefinitely, so a feature still holding a number proved nothing — `has_value()` stayed
    true long after an API stopped answering. The coordinator now tracks each source's last
    successful poll and the watchdogs key off that age.
  - *Whether an input entity is reporting.* A rain rate legitimately sitting at 0.00 in/h
    never changes state, so the old `last_changed` check false-alarmed through dry spells
    and stayed quiet when the gauge actually died. The raw rate is now a feature
    (`rain_rate_in_hr`, null when the entity is unavailable) and staleness tracks the last
    usable read.
  - A boot grace period means a cold start no longer lights up every watchdog at once, and
    a source that is switched off is reported as fine rather than permanently missing.
- **Fix: `creek_temperature` and `creek_rain_on_snow` never worked.** Both are derived in
  `FeatureBuilder` rather than by a source, so they were absent from `FEATURE_KEYS` — and
  the `creek/features` payload is built from that tuple, so the two entities referenced
  fields that were never published and sat at unknown from 0.8.0. Derived keys are now
  published explicitly, with a test that fails if any discovery template references a field
  its topic does not carry.

**Upgrade note:** entity IDs for the migrated entities gain the device prefix —
`binary_sensor.creek_stage_stale` becomes
`binary_sensor.creek_modeling_creek_stage_stale`, and likewise for the soil mean,
ponding and the other watchdogs. The bundled dashboard is updated. The old entities linger
in the entity registry as unavailable until deleted (Settings → Devices & Services →
Entities, filter Unavailable).

## 0.9.0

- **Antecedent Precipitation Index — ingestion slice 2f** (spec §4/§5): `api_index_in`, an
  exponentially-decaying rainfall memory. It complements the WH51 probes rather than
  duplicating them — those read two buried points, this summarises weeks of rainfall over
  the whole basin, and wet antecedent conditions are what turn an ordinary storm into a
  flood. Needs no creek gauge.
  - Decay is applied as a continuous power of elapsed time, not a discrete daily step, so
    an irregular sampling interval or a restart decays correctly.
  - State is persisted with its timestamp, so downtime decays the index instead of freezing
    it. A gap beyond 14 days restarts from zero rather than carrying a stale value across
    rainfall that was never sampled — being wrong in the direction of *understating*
    wetness is the dangerous one, so it is made explicit rather than silent.
  - Rides on the existing on-site rain samples (the accumulator now exposes its per-update
    increment), so the rain-rate entity is still read once per loop.
- **Tier**: the index gives a second route to Advisory alongside soil moisture — high QPF
  onto a wet basin — so a failed WH51 probe cannot mask saturated ground.

## 0.8.0

- **SNODAS snowpack — ingestion slice 2e** (spec §1/§5): `app/sources/snodas.py` reports
  `snow_water_equivalent_in` for the site's grid cell. NOHRSC publishes no point API (the
  "nearest" page returns HTML whatever `fmt` you ask for), so this reads the gridded masked
  product directly: pull the daily tar, stream the SWE member's gzip to the one cell we
  need, and discard the rest rather than decompressing a 46 MB raster. Grid geometry was
  verified against a real February file — the site cell read 42 mm, ocean and Florida cells
  read no-data, and a Cascades cell saturated. Daily product, so results are cached per date
  under `/data/state/` and the fetch walks back up to 5 days for late or skipped postings.
- **Rain-on-snow flag** — derived in `features.py` because it spans sources: a meaningful
  pack, above-freezing temperature, and rain either falling or forecast. Needs the new
  `onsite_temp_entity` option (readings are normalised to °F either way). Every input must
  be present, so a missing source can never fabricate the condition.
- **Tier rule for rain-on-snow**: Advisory when it is forecast, Watch once the rain is
  actually falling. The pack melts into the same storm, so the same QPF yields more runoff.
- Adds a `Creek Snowpack Data Missing` watchdog. Off-season a bare cell reads 0.00 in rather
  than null, so this flags a genuinely failing fetch, not the absence of snow.

## 0.7.0

- **NWS active alert products — ingestion slice 2d** (spec §3): `app/sources/alerts.py`
  polls `api.weather.gov/alerts/active?point=` (free, no key, 5 min) and reports flags for
  an active Flood Watch, Flood Warning, and Flash Flood Warning covering the site, plus a
  total active-product count. Querying by point rather than county zone means only alerts
  whose polygon actually covers us count.
- **Tier force-promotion** (spec §6): an active product now imposes a *floor* on the alert
  tier regardless of what our own instruments show — Flood Watch → ≥ Advisory, Flood
  Warning → ≥ Watch (the rule §6 mandates), Flash Flood Warning → ≥ Warning. A floor never
  lowers a tier the sensors have already earned. The Watch and Flash Flood floors go beyond
  the letter of §6 and are marked as such in `tiers.py`: a Flood Watch *is* the forecast-risk
  case Advisory describes, and in a basin with a tens-of-minutes response (§1) a flash flood
  warning is materially more urgent than an areal one.
- Adds a `Creek NWS Alerts Missing` watchdog. The feed reports "no alerts" as 0 rather than
  null, so an unavailable count means the call itself is failing — and a Flood Warning could
  be in effect without us escalating.

## 0.6.0

- **Forecast-driven alert tiers** (spec §6, Addendum C.3): `app/tiers.py` now evaluates the
  full feature row instead of probability alone, so **Tier 1 Advisory** (NWS QPF onto wet
  soil) and **Tier 2 Watch** (upstream/on-site rain accumulating) fire *without the creek
  gauge* — the first genuinely useful warnings the system can issue before the SEN0676 is
  mounted. Each tier publishes the reasons that fired it (`why` attribute), surfaced on the
  dashboard and in the notification.
  - **Breaking (entity semantics):** the tier scale is now `0` All-clear · `1` Advisory ·
    `2` Watch · `3` Warning · `4` Emergency, matching spec §6 plus an explicit all-clear.
    Previously `0`–`3` collapsed Advisory into Watch. The dashboard is updated in the same
    commit; any external automation keyed on the old numbers needs re-checking.
- **USGS downstream gauges — ingestion slice 2c**: new `app/sources/usgs.py` polls NWIS
  instantaneous values for `<usgs downstream>` (a receiving river downstream) and
  `<usgs adjacent>` (a receiving creek in an adjacent basin), reporting gage height,
  discharge, and 3 h rise for each. Free, no key. The downstream gauge's basin drains the
  same upstream upland as our upstream half, so it sees roughly our rain. These are a
  *different, larger* basin — not a creek-level proxy — but they are the only observed
  rainfall→response signal available before the creek node exists, which gives the Phase-3
  lag estimate a head start. New `usgs_downstream` option (default on).
- **Ingest watchdogs**: four new HA-side binary sensors flag a source publishing no value
  (QPF, upstream PWS, NWM, USGS). They test for a *value*, not staleness — a feature
  legitimately sitting at 0.00 in never changes state, so a staleness check would false-alarm.
- **Tier notification automation** replaces the old Tier 0 placeholder, which keyed off a
  `sensor.nws_qpf_24h` REST sensor that Addendum C decided never to build. Notifications
  only; escalating/wake-the-house actions stay unwired until thresholds are calibrated.
- **Fixes:**
  - Entity IDs in `ha-packages/creek_warning.yaml` and the dashboard now match reality.
    MQTT-discovery entities carry the device-name prefix (`sensor.creek_modeling_creek_*`);
    HA-package templates and the ESPHome node do not. A previous blanket rename had applied
    the prefix to seven entities that never had it, and the pipeline-state watchdog was
    missing the prefix it did need. `discovery.py`'s docstring claimed the old scheme.
  - `sensor.outside_weather_station_rain_rate` → `sensor.weather_station_rain_rate` in the
    rain-rate watchdog (missed by the earlier entity-name correction).
  - An unset `nwm_reach_id` reaches Python as the literal string `"null"` from
    `bashio::config`; it now disables the source instead of polling a bogus reach.

## 0.5.0

- **Forecast/upstream ingestion — slice 2b** (spec Addendum C): adds two sources, published
  on `creek/features` and auto-created as discovery sensors:
  - **Weather Underground upstream PWS** — mean `precipRate` across the configured upstream
    stations feeds a rolling accumulator (`upstream_rain_{1,3,6,24,72}h`), plus
    `upstream_precip_today`. A single station failing is skipped, not fatal. Key stays in options.
  - **NWM / NWPS reach** — short-range streamflow forecast for `nwm_reach_id`: near-term
    (`nwm_flow`) and short-range peak (`nwm_flow_peak`) discharge in ft³/s.
- Extracts the rolling accumulator into `app/sources/accumulator.py`, shared by on-site rain
  and upstream WU. Dashboard gains an "Upstream & model" card. Tests for WU + NWM. Bump 0.5.0.

## 0.4.0

- **Forecast/upstream ingestion — slice 2a** (spec Addendum C): the add-on now fetches its
  own features and publishes them on `creek/features`, auto-created as discovery sensors:
  - **On-site rain accumulations** `rain_{1,3,6,24,72}h` — a rolling accumulator integrates
    the Ecowitt rain rate (in or mm) into a 72 h ring persisted under `/data/state/`.
  - **NWS QPF** `qpf_6h` / `qpf_24h` — free NOAA gridpoint forecast (no key), pro-rated from
    the mm interval values to inches. Location read from HA config; refresh ~15 min.
- Sources run through a coordinator with per-source refresh + last-good caching, so a flaky
  API never stalls or crashes the fast loop. Feature rows (and the dataset) widen accordingly.
- Adds tests for the accumulator, QPF parsing/proration, and the coordinator. Bump 0.4.0.
- (Slice 2b — Weather Underground upstream + NWM reach — to follow.)

## 0.3.0

- **MQTT Discovery:** the add-on now auto-provisions its HA entities (all `creek_*` sensors
  and the four command buttons) under an *Creek Modeling* device — no HA package or
  `configuration.yaml` edit for them, and they re-publish on every (re)connect so they track
  add-on updates. Removes `ha-packages/creek_modeling.yaml`; the sensor-fault watchdogs move
  into the Layer-1 `creek_warning.yaml`.
- **Availability (LWT):** publishes `creek/status/availability` online/offline so discovered
  entities show *unavailable* when the add-on stops.
- **Alert tier in the add-on:** computes and publishes `creek/alert_tier` (`value` + `label`)
  from probability + ponding (`app/tiers.py`), replacing the HA template. PLACEHOLDER
  thresholds (open question #7).

## 0.2.0

- **On-demand commands:** subscribes to `creek/cmd/{run_inference,retrain,promote,rollback}`
  so the HA dashboard can drive the pipeline. Commands execute on the service's single
  thread (no overlapping runs) and are honored within a few seconds.
- **Status publishing:** timestamps are tz-aware ISO (proper HA `timestamp` sensors);
  new `creek/status/pipeline` (state/task/last-run/last-error),
  `creek/status/registry` (active/candidate/history + metrics), and
  `creek/status/command_result` (echo of each command's outcome).
- **Model registry** (`app/registry.py`): real `models/registry.json` schema with working
  `promote()`/`rollback()` pointer logic; ML training/artifact loading remains a Phase-4 stub
  behind the same interface.
- **Fix:** construct the MQTT client with paho-mqtt 2.x's `CallbackAPIVersion` (the previous
  1.x-style constructor raised on the pinned `paho-mqtt>=2.1`).

## 0.1.0

- Phase 2 skeleton: validates the Supervisor Core-API proxy and MQTT wiring.
- Builds live features (creek stage, rate-of-rise, WH51 soil moisture + ponding flag).
- Returns a transparent conservative **threshold** estimate; gates ML on
  `min_events_for_ml` storms captured.
- Publishes `creek/flood_probability`, `creek/predicted_crest`, `creek/lag_estimate`,
  `creek/model_health` over MQTT.
- Installable as a Git-based add-on repository (GUI) or as a local add-on.
