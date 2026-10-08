# Rate of Rise

Layer 2 of the Creek Flood Early-Warning System. Runs the rise-probability, predicted-crest
and flood-probability inference (fast loop) and the nightly retrain/recalibrate batch,
publishing results to Home Assistant over MQTT.

## Prerequisites

- **Mosquitto broker** add-on installed and configured. This add-on declares `mqtt:need` and
  will exit on start if no MQTT service is available.
- Host architecture **amd64** (the add-on is amd64-only).

## Companion Home Assistant config

**The add-on's own entities are created automatically via MQTT Discovery** — the `creek_*`
sensors, buttons, and the storm-annotation text box (flood probability, predicted crest,
lag, alert tier, pipeline/model status, the Run inference / Retrain / Promote / Rollback
buttons, and "Annotate Latest Storm") appear under an
**Rate of Rise** device with no package or `configuration.yaml` edit, and they
re-publish (so they stay current) whenever the add-on updates. An MQTT LWT flips them to
*unavailable* when the add-on is stopped.

Two things still need a **one-time** manual setup (they can't come from the add-on):

1. **Layer-1 package** — the service-stale watchdog and the alert-tier automation. Copy
   `ha-packages/creek_warning.yaml` from the
   [Rate of Rise repo](https://github.com/ryanbuiltthat/rate-of-rise) → `/config/ha-packages/`, and enable
   packages in `/config/configuration.yaml`:

   ```yaml
   homeassistant:
     packages: !include_dir_named ha-packages
   ```

   **Set your phones** under "Companion app" near the bottom of the `creek_tier_change`
   automation. Each phone is its own action block, identified by Home Assistant
   **device id** — not a `notify.` service name (companion-app phones don't have a
   fixed, guessable one) and not a list to append to (a templated device id doesn't
   work — Home Assistant resolves it before the template engine ever runs; both of those
   are what shipped broken in 0.14.1 and 0.14.2). Find a phone's id at Settings →
   Devices & Services → Devices → click the phone → the URL ends
   `.../config/devices/device/<id>`:

   ```yaml
   - &creek_push
     domain: mobile_app
     type: notify
     # ...title/message/data...
     device_id: efa8fde8c961dd7d5c23feda71661457   # primary phone
   - <<: *creek_push
     device_id: 3b510b04ed78e8ef686a358daa0cc84d   # second phone
   ```

   **To add a phone:** copy one whole block (the `- <<: *creek_push` line and its
   `device_id:`), paste it as a new list item, and set its device id. The
   `&creek_push`/`<<: *creek_push` pair is a plain YAML anchor — it shares the
   notification content so it's written once, not once per phone.

   `critical_from_tier` (in the automation's variables, near the top) sets which tier
   makes the push CRITICAL: at or above it, the push goes out on Android's
   `alarm_stream` channel, so it sounds at alarm volume through silent and vibrate and
   stays on screen until dismissed. Below it, an ordinary notification. The default is
   **3 (Warning)** — the creek itself answering; Watch, which radar cells and upstream
   rain raise, is an ordinary push. It was 2 before 0.24.0.

   > **Required, once, per phone: let the channel override Do Not Disturb.** This is not
   > a formality and it is not Android's default — the companion app documentation is
   > explicit that notifications "do not override Do Not Disturb settings" unless a
   > notification channel is given permission to. **No YAML in this repo can grant that**;
   > only the phone's owner can, by hand:
   >
   > Settings → Apps → Home Assistant → Notifications → **alarm_stream** → allow it to
   > override / ignore Do Not Disturb.
   >
   > The channel only appears in that list *after* the first notification using it has
   > arrived, so run the dry run below once first, then grant the permission, then run it
   > again to confirm.
   >
   > Two consequences worth knowing. Android fixes a channel's importance and sound the
   > first time it appears and **ignores every later change** ("only lowering of the
   > importance will work"), so this is a one-time setup that later edits cannot undo —
   > or repair. And renaming the channel starts a *fresh* one with no permission, which
   > on the phone looks exactly like the alarm having stopped working. Don't rename it.

   **Dry-run test it** (spec §8 — an untested alarm is not an alarm). One action, no
   threshold editing:

   1. Turn Do Not Disturb **on**. Phones face-down, screens off, in another room.
   2. Developer tools → Actions → `script.creek_alert_test` → Run. (It sends the real
      critical payload — the same one a Tier 4 sends, sharing the same YAML — to every
      configured phone, under its own tag so it never replaces a live alert.)
   3. Both phones should sound **at alarm volume** and stay on screen until dismissed.

   | What happened | What it means |
   |---|---|
   | Nothing at all on any phone | The script failed before sending — check its trace under Settings → Automations & Scenes → Scripts |
   | Notification arrives, silent | The channel has no DND override yet — grant it (box above), then re-run |
   | Sounds, but quiet / not on alarm volume | Check Android Settings → Sound → Do Not Disturb → Alarms |
   | One phone only | That phone's `device_id` is wrong, or the app is not logged in on it |

2. **Home screen widget (optional)** — a phone widget showing the live alert tier, styled to
   match the add-on's own branding. On-device companion-app configuration only, nothing to
   copy into `/config`. See
   [docs/companion-app-widget.md](../docs/companion-app-widget.md).

3. **Dashboard** — copy `dashboards/creek_flood_watch.yaml` → `/config/dashboards/` and
   register it (core Lovelace, keeps your UI dashboards untouched):

   ```yaml
   lovelace:
     mode: storage
     dashboards:
       creek-flood-watch:     # slug must contain a hyphen
         mode: yaml
         title: Creek Flood Watch
         icon: mdi:water-alert
         show_in_sidebar: true
         filename: dashboards/creek_flood_watch.yaml
   ```

Then **Developer Tools → YAML → Check Configuration** and **Restart**. `!include_dir_named`
and `filename:` are relative to the config directory; the package `template:` block merges
with anything you already have.

> These two files are the source of truth in the repo; the `/config` copies are a deploy
> target — re-copy after pulling repo changes. (The discovered entities need no re-copy.)

## Configuration

Set these on the **Configuration** tab.

| Option | Default | Notes |
|---|---|---|
| `log_level` | `info` | `trace`…`fatal` |
| `fast_loop_minutes` | `5` | Inference cadence (1–60) |
| `nightly_retrain_hour` | `3` | Local hour (0–23) for the nightly batch |
| `mqtt_base_topic` | `creek` | Base MQTT topic |
| `publish_prefix` | `creek` | MQTT topic prefix (entity IDs come from discovery, see below) |
| `min_events_for_ml` | `10` | Stay on the threshold model until ≥ N storms captured |
| `storm_start_rain_1h_in` | `0.10` | 1 h rain (on-site **or** upstream) that opens a storm event |
| `storm_continue_rain_1h_in` | `0.02` | Below this a storm counts as paused; the quiet clock runs |
| `storm_quiet_hours` | `6` | Paused this long and the storm has ended. Too long merges separate storms, too short splits one in two — tune against observed storms |
| `stage_max_age_minutes` | `6` | How old a stage reading may be and still be differenced into a rate of rise. Only consulted when `creek_node_status_entity` is blank or unavailable |
| `rate_of_rise_max_gap_minutes` | `10` | Longest gap between two stage samples that still yields a rate. Past it the rate is withheld and the baseline re-seeded — see *Radio dropouts and false rate-of-rise alarms* |
| `rate_of_rise_confirm_samples` | `2` | Consecutive gap-free samples a rate must survive after a dropout before it alone can raise Tier 3. Costs nothing while the link is up |
| `rate_of_rise_window_minutes` | `10` | Rate of rise is measured against a reading at least this old, never the previous one. A still creek flickers 1–2 mm between reports; over one ~63 s report that read as 0.075 in/min, over the Warning. See *Radio dropouts and false rate-of-rise alarms* |
| `max_stage_rise_in_min` | `2.0` | Fastest rise the creek can physically make. A jump past it is withheld from the tiers as a sensor fault and raises *Creek Stage Implausible* (the 2026-09-17 false Emergency). Believed if it holds 30 min |
| `ml_drives_alerts` | `false` | Off: a promoted model runs in **shadow** — published as *Creek ML Shadow Probability* and recorded every loop, while the tiers use the threshold estimate. On: a promoted model past `min_events_for_ml` drives the tiers (20 % Watch, 50 % Warning, 80 % Emergency) |
| `rise_1h_threshold_in` | `0.5` | *Creek Rise Probability 1h* is the chance the creek comes up at least this many inches within the next hour. Changing it retrains that model at the next add-on start |
| `rise_3h_threshold_in` | `1.0` | The same for *Creek Rise Probability 3h*, over the next three hours |
| `google_floods_api_key` | `""` | Google Flood Forecasting API key (Google Cloud project + the API enabled). Setting it enables two reads: the gauges Google models within 25 mi of the site with their forecast status (neighbouring rivers, capped at Tier 2 Watch), and flash-flood polygon containment of the site itself (also capped at Tier 2 Watch). Blank disables both |
| `wu_api_key` | `""` | Optional (Weather Underground PWS) |
| `nwm_reach_id` | `<nwm reach id>` | NWM reach at the sensor site (open question #3) |
| `upstream_pws_ids` | `<upstream PWS 1>`, `<upstream PWS 2>` | Upstream PWS in the upstream corridor (open question #4) |
| `stage_entity` | `sensor.creek_gateway_stage` | Creek depth above the bed, published by the RFM69 gateway |
| `creek_node_status_entity` | `binary_sensor.creek_gateway_creek_node_status` | Radio-link state for the creek node, published by the same gateway: ON while the node's 60 s reports arrive, OFF after five are missed. Blank to disable the check and fall back to `stage_max_age_minutes` |
| `creek_node_packets_entity` | `sensor.outside_creek_gateway_creek_node_packets` | The gateway's packet counter. It changes on every report, so when it stops the link is treated as down even if the status sensor above is frozen at ON (it only writes on a transition). Blank to disable |
| `soil_moisture_entities` | WH51 #1, #2 | `..._soil_moisture_willow` (near house), `..._soil_moisture_field` (near creek); order is significant. Blank an entry (don't delete it) while its probe is out, or the other slides into its slot and is recorded under the wrong location. The willow probe is blanked until it is replaced |
| `ponding_saturation_pct` | `78` | A probe at or above this turns on *Creek Soil Ponding* (a Tier 1 reason, and faster runoff in the lag estimate). Was a fixed 85 %; the field probe read 81 % with water standing on 2026-09-28 |
| `onsite_rain_rate_entity` | `sensor.outside_weather_station_rain_intensity` | Ecowitt. Feeds the rain-rate watchdog, and the rain totals when the counter below is unavailable |
| `onsite_rain_total_entity` | `sensor.outside_weather_station_rain_total` | Ecowitt's monotonic rain counter. Rolling rain totals are the exact difference between readings; integrating the sampled rate instead read 5–10 % low. Blank to use the rate |
| `onsite_rain_daily_entity` | `sensor.outside_weather_station_rain_24hr` | Ecowitt. **Currently unused** |
| `usgs_downstream` | `true` | Poll USGS `<usgs downstream>` / `<usgs adjacent>` (free, no key) for lag validation |
| `snodas_swe` | `true` | Daily SNODAS snow-water-equivalent for the site cell (free, no key) |
| `nexrad_cells` | `true` | NEXRAD storm-cell tracks via IEM (free, no key) — inbound-cell ETA for storms approaching from the W/NW, where the upstream gauges cannot lead |
| `nexrad_radar_id` | `<nexrad site>` | The WSR-88D covering the site  |
| `wpc_ero` | `true` | WPC Excessive Rainfall Outlook via IEM (free, no key) — grades forecast rain against flash-flood guidance, i.e. against what the ground can absorb, a day ahead of radar |
| `onsite_temp_entity` | `sensor.outside_weather_station_outdoors_temp` | Ecowitt; needed for the rain-on-snow flag |

## Gateway store backfill

Optional. With the Feather ESP32-S3 gateway (v2) the add-on reads back every node packet and
Ecowitt reading it logged to its SD card while Home Assistant or WiFi was down, and fills the
gap in HA's entity history, hourly statistics, the stage log and the dataset. Blank
`gateway_store_url` and nothing runs. Runbook: [docs/gateway-v2-trial.md](../docs/gateway-v2-trial.md).

| Option | Notes |
|---|---|
| `gateway_store_url` | Base URL of the v2 gateway, e.g. `http://192.168.30.21`. Blank disables backfill. Pointed at a v1 gateway it only reports that and stays silent |
| `gateway_store_token` | The gateway's `creek_store_token` |
| `backfill_entity_map` | JSON: the entities that receive written history, per stream: `node` and, after cutover, `ecowitt` (`{"node": {"stage_ft": "sensor...", ...}, "ecowitt": {"rain_total_in": "sensor...", ...}}`) |
| `backfill_shadow_map` | JSON, same shape plus an `ecowitt` stream: entities whose rows are only logged as `backfill shadow: would insert ...`, never written. For rehearsal |

`sensor.rate_of_rise_creek_backfill_status` reads `off`, `v1 gateway (no store)`, `unreachable`,
`gateway SD not mounted`, `idle`, `backfilling N`, `waiting for live poll`,
`blocked: recorder schema N` or `error: ...`. Its attributes carry the cursors and the last
pass's counts. The writer only runs on recorder schema 53; every row it inserts is marked.

The gateway reports a `store_id`, its SD card's identity, which the add-on keeps with the
cursors in `/data/state/backfill.json`. When it changes (card replaced or reformatted) the
add-on logs one WARNING and re-reads the new card from the start; writes are idempotent, so
nothing is duplicated. While the gateway's card is not mounted the status reads
`gateway SD not mounted` and nothing is read.

To take the backfilled recorder rows back out, publish to `creek/cmd/backfill_undo` (payload:
an ISO-8601 time to undo from, or empty for all). Give the time an explicit offset or `Z`
(`2026-10-07T18:00:00Z`): a time without one is read as the add-on's local time, which is UTC
unless the add-on's timezone was changed.

## How it talks to Home Assistant

- **Reads** entity states through the Supervisor proxy at `http://supervisor/core/api`,
  authenticated by the injected `SUPERVISOR_TOKEN` — no long-lived token needed
  (`homeassistant_api: true`).
- **Writes** its outputs, ingested features and status over MQTT (broker discovered via
  services) and auto-creates the matching HA entities via MQTT discovery — no package edit.
- **Watchdogs** for every input and source are computed in the add-on and published on
  `creek/status/health`, so they appear automatically with everything else.
- **Entity IDs:** the discovered entities belong to a *Rate of Rise* device, so
  Home Assistant prefixes the device name:
  `sensor.rate_of_rise_creek_flood_probability`,
  `sensor.rate_of_rise_creek_qpf_24h`, and so on. Entities defined in
  `ha-packages/creek_warning.yaml` and the RFM69 gateway are *not* discovery entities and
  stay unprefixed (`sensor.creek_*`). That package is now down to two things that cannot live
  here: the tier notification automation, and the add-on's own liveness watchdog.

## Alert tiers

`app/tiers.py` evaluates spec §6 and publishes `creek/alert_tier` with a numeric level, a
label, and the reasons that fired:

| Level | Label | Driven by | Needs the creek gauge? |
|---|---|---|---|
| 0 | All-clear | nothing elevated | — |
| 1 | Advisory | NWS QPF + antecedent soil moisture | No |
| 2 | Watch | upstream / on-site rain accumulation, inbound radar cells | No |
| 3 | Warning | stage, rate-of-rise | Yes |
| 4 | Emergency | stage near bank top | Yes |

**A radar Watch is held for 30 min after the last scan that raised it**
(`WATCH_RADAR_HOLD_MIN`, `tiers.RadarWatchHold`). A cell drops out of the threat list as it
arrives overhead, dips below 40 dBZ for a scan, or wobbles off its track, and each time the
tier used to fall straight back to All-clear — nine Watch episodes in 30 h on 2026-09-26/27,
four of them 5–10 min long, while the creek's rises came 1–3 h after the cells. The reason
says when the cell was last seen and how long the hold has left. The hold is in the add-on's
memory, so a restart forgets it; the notification automation's 15-minute hold on drops
covers that.

An active NWS product additionally sets a **floor** on the tier, whatever our own sensors
say (spec §6): Flood Watch → ≥ Advisory, Flood Warning → ≥ Watch, Flash Flood Warning →
≥ Warning. A floor never lowers a tier the sensors have already earned.

Levels 1–2 run entirely off forecast and rainfall data, which is what made the system
useful before the SEN0676 was mounted — and is what keeps it useful whenever the radio
link to the creek node drops. Levels 3–4 need stage from the node, which has been
reporting since 2026-09-12. **All thresholds are placeholders** pending WH51 calibration
(open question #7) and observed storms; the surveyed datum (#5) is resolved.

### Radio dropouts and false rate-of-rise alarms

Stage is an absolute measurement: if it reads 2.1 ft, the creek is 2.1 ft deep, and one
reading is enough to mean it. **Rate of rise is a difference between two readings**, so it
can be manufactured by the radio link rather than by the creek — and that is a real failure
mode, not a theoretical one.

When the creek node stops answering, older gateway firmware does not blank
`sensor.creek_gateway_stage`; it simply stops updating it, and Home Assistant keeps serving
the last value the node sent. Current gateway firmware clears stage, depth, distance,
battery and RSSI to unknown once it declares the node offline, but a gateway that has
itself stopped publishing still leaves the last value frozen, so the guards below stay
necessary. While stage is blank and the link is down, the tiers use the last accepted
reading, so a node lost at the crest keeps Warning or Emergency up rather than letting the
all-clear through; the reason then says "(last reading, node offline)". Difference the first reading after the link returns against
that stale one and the whole outage's worth of level change lands in a single loop interval:
a creek that rose 3 in over a 40-minute dropout reads as **0.6 in/min**, twelve times the
Tier 3 threshold, instead of the 0.075 in/min it actually did. With the package's
`critical_from_tier: 3`, that is a critical alarm-stream push for a creek doing nothing
unusual.

Four guards stop it, all in `app/features.py` and `app/tiers.py`:

1. **The link is checked before the reading is used.** `creek_node_status_entity` is
   packet-driven — it is OFF because reports stopped arriving, not because the number
   stopped changing — but it only writes on a transition, so a gateway that stops
   publishing leaves it frozen at ON. `creek_node_packets_entity` overrules it: a packet
   counter that has not moved in `stage_max_age_minutes` means the link is down, whatever
   the status sensor still says. While the link is down no rate is computed at all.
2. **A rate spans a window, not an interval.** Each rate is taken against the newest reading
   at least `rate_of_rise_window_minutes` old. The node reports whole millimetres and a still
   creek flickers a millimetre or two; the old two-reading difference could land on readings
   one report (~63 s) apart and turn 2 mm into 0.075 in/min — a Warning from a creek that was
   not moving (this happened twice on 2026-09-21; both were falls). If more than
   `rate_of_rise_max_gap_minutes` separates two samples, the history is re-seeded, so a
   window never spans a gap.
3. **The first rates back are confirmed before they can alarm.** After any dropout the rate
   must clear the threshold on `rate_of_rise_confirm_samples` consecutive rates before it
   alone raises Tier 3.
4. **Impossible readings are withheld.** A rise faster than `max_stage_rise_in_min` from the
   last good reading is treated as a sensor fault, not a flood: `stage_ft` is withheld from the
   tiers and *Creek Stage Implausible* turns on (and pushes to the phones). This is the
   2026-09-17 case — a cold radar answered 0, the gateway clamped it to 37.6 in, and stage
   alone raised Tier 4 on a dry day, on the first reading after a dropout. The baseline
   therefore survives dropouts. A jump that holds for 30 minutes is believed, so a real rise
   during an outage cannot be locked out.

The cost is bounded and one-sided: a Warning driven *only* by rate of rise needs a full window
of history first (10 min after a start or reconnect) plus the confirmation. Stage, model
probability and the NWS floors are untouched, so a creek that is genuinely high still warns
on the first reading back — unless that reading is an impossible jump, which is exactly the
case that must not.

The dashboard's *Ingestion health* and *Watchdogs* cards make this visible: the node's link
state, *Telemetry stale* (the packet counter), `sensor.rate_of_rise_creek_stage_age`, and the
*Stage Stale*, *Stage Frozen* (same reading for 30 min while packets arrive — a radar that has
stopped re-measuring) and *Stage Implausible* watchdogs. All of them push to the phones
(`ha-packages/creek_node_health.yaml`), critically if a storm is open.

## Persistent storage

```text
/data/datasets/parts/*.jsonl              today's rows, appended each fast loop
/data/datasets/dataset.parquet            consolidated nightly from completed parts
/data/models/registry.json                versioned artifacts + skill metrics
/data/models/model-<version>.json         a candidate/active artifact (xgboost native format)
/data/models/model-<version>.meta.json    its feature column order + horizon
/data/state/*.json                        rain/API/SNODAS accumulator + rain-counter state
/share/rate_of_rise/events.sqlite       annotated storm event log
/share/rate_of_rise/stage/YYYY-MM-DD.csv  every stage reading, ~10 s resolution
```

The stage CSVs are the storm's high-resolution record, kept by the add-on itself from HA's
live state. The dataset keeps one row per 5-minute loop; the node reports every 60 s (every
~5 s while the creek rises), and until 0.23.0 that finer record lived only in Home
Assistant's recorder — which keeps 10 days by default, and which on 2026-09-23 stopped
writing for 25 hours while HA kept running. Each row is `reading_ts,logged_ts,stage_ft`
(unix seconds; a blank stage is an unknown/unavailable reading, kept because the gap is
information). Each dataset row now also records the alert tier, the probability that drove
it, and the ML shadow probability, so a storm can be replayed afterwards.

`/data` is private to this add-on. The storm log is the exception and lives in `/share`,
because it is the one file a human is expected to edit: `/data` inside the SSH/Terminal
add-on is *that* add-on's own `/data`, so a `sqlite3 /data/events.sqlite` typed there
would silently create and edit an empty database. `/share` is one path that means the same
thing from every add-on, and it is exported over Samba. An events.sqlite left in `/data`
by an earlier version is moved across automatically on first start; if `/share` is
unavailable the add-on logs a warning and keeps using `/data`. The resolved path is logged
at startup — `Storm event log at …`.

Annotating a storm (the "annotated" half of the Phase 3 event log) is a SQLite update.
The `sqlite3` CLI is installed in this add-on's image, but you do not need it — run this
from the **SSH & Web Terminal** add-on, or open the file over Samba with any SQLite
browser:

```sh
sqlite3 /share/rate_of_rise/events.sqlite \
  "SELECT id, datetime(started_ts,'unixepoch','localtime'), ended_ts
     FROM storm_events ORDER BY id DESC LIMIT 5;"

sqlite3 /share/rate_of_rise/events.sqlite \
  "UPDATE storm_events SET notes='basement dry; culvert ran full' WHERE id=3;"
```

Editing while the service is running is fine — both sides use a 5 s busy timeout, and the
fast loop's writes are sub-millisecond.

## Status

**Ingest (Phase 2)** — complete. Live: on-site rain accumulations, the Antecedent
Precipitation Index, NWS QPF, NWS active alert products, Weather Underground upstream PWS,
NWM reach forecast, USGS gauges, SNODAS snowpack with a rain-on-snow flag, NEXRAD cell
tracking, the WPC Excessive Rainfall Outlook, Google Flood Forecasting status at the
nearest gauges Google models, on-site stage and rate-of-rise from the creek node, and
watchdogs on every ingest source.

Google Floods is the one source that can be enabled and still report nothing: it needs an
API key, and it only has an answer if Google models a gauge within 25 mi. `Creek Google
Flood Gauges` reading 0 means it does not — that is a real reading, not a fault, and it is
the answer to open question #2.

Read that entity first when the Google cards look wrong, because it separates the two
cases. A count (including `0`) means the source polled successfully. All four entities
reading `unknown` means it has never polled successfully at all — check the add-on log for
`Google Flood Forecasting enabled` vs `disabled (needs google_floods_api_key)`, and note
that this API needs the key *and* the Flood Forecasting API enabled on the Google Cloud
project that issued it; a project that never enabled it answers 403 for a valid key. The
`Google Flood Status Missing` watchdog has a two-hour grace from start-up, so it stays OFF
through the first failures and is not a health check for the first two hours.

**Correlate (Phase 3)** — the rainfall→response lag estimate runs nightly (`app/lag.py`).

**Predict (Phase 4)** — built and now running against real data: gradient-boosting
inference (`app/model.py`), the nightly retrain (`app/train.py`) and the model registry
(`app/registry.py`). The storm log has cleared `min_events_for_ml`, so Retrain produces
promotable candidates.

What remains is **calibration, not code**. A record this short still yields held-out
splits with no Warning-tier crossings in them, so a candidate usually cannot be scored at
all; Promote says so at the press and keeps saying so while such a model is active. Until
storms accumulate, the threshold estimate is the honest answer and the tier thresholds
stay placeholders.

**Candidate ready (0.24.1).** `binary_sensor.rate_of_rise_creek_candidate_ready` turns on
when a candidate clears low starting bars on its held-out split: at least 3 positives,
AUC ≥ 0.60, hit rate ≥ 20 %, false-alarm rate ≤ 80 % (`READY_*` in `app/registry.py`).
Its `candidate_ready_reason` attribute names the bar a candidate missed, or summarizes
the one that passed. The `creek_candidate_ready` automation in `creek_warning.yaml` pushes
once per ready candidate. Promotion stays a button press.

**Shadow mode (0.23.0).** With `ml_drives_alerts` off (the default) the alert tiers use the
threshold estimate whatever is promoted, and the ML model — the active one, or with none
active the newest candidate — runs alongside as *Creek ML Shadow Probability*, recorded in
every dataset row. That is how to see what a model would have done through a real storm
before letting it raise one. Three things changed with it, all found in the September 2026
record: every positive label the first models trained on was an artifact (a rate charged
across a 2-hour dropout, and the 3.13 ft radar-fault clamp), so training now ignores
implausible stages and rates without a confirmed sample count; "validated" now also requires
catching at least one held-out positive (the model active on 2026-09-24 had AUC 0.608 and
caught 0 of 49); and inference undoes the class weighting, which inflated probabilities by
up to ~34x in odds against the fixed 20/50/80 % tier cut-offs.

**Rise probability (0.24.0).** The Warning-tier model above has nothing to learn from until
the creek reaches Warning, and a season can pass without that. *Creek Rise Probability 1h*
and *3h* ask a question every storm answers instead: will the creek come up at least
`rise_1h_threshold_in` (default 0.5 in) within the hour, or `rise_3h_threshold_in` (1.0 in)
within three? They are refitted every nightly batch and at startup when missing, are used
as soon as they are fitted, and **never drive a tier** — they are published for people to
read. Each sensor's attributes carry the threshold and the model's scores:

- `brier_skill` — above 0 means better than always quoting the base rate. The number to watch.
- `episodes` / `episodes_flagged` — separate rises on record, and how many of them a model
  that never saw that storm put at 50 % or more.
- `quiet_false_alarms` — rows at 50 %+ with no storm and no rise anywhere near them.
- `trustworthy` — at least two rises, positive skill, and one flagged.

Every score is leave-one-storm-out: a 5-minute series has hundreds of near-identical rows per
storm, and a row-level split would test the model on the storm it learned from. The creek's
own recent movement is an input (*Creek Stage Change 1h*, *Creek Stage Above 6h Low*, both
also published) — without it, the rows just after a crest, still raining, looked like the
start of the next rise. Fitted on rises of an inch or two from a creek near 1 ft, these say
nothing about what the creek does out of its banks.

**Predicted crest (0.25.0).** *Creek Predicted Crest* is how high the creek is expected to
get within the next 3 h, in feet. *Creek Time to Crest* is how many minutes away that is.
*Creek Crest Outlook* puts the crest in words against the surveyed levels: "Reaches
Warning" when the median crest does, "May reach Emergency" when only the top of its range
does, and "Below Warning" otherwise. The model (`app/crest.py`) learns from every rise, as
the rise probabilities do, so it can be scored now rather than after a flood. Like them, it
is refitted nightly and at startup when missing, is used at once, and **never drives a
tier**. The sensor's attributes:

- `low_ft` / `high_ft` — the 80 % range around the crest; `rise_in`, `rise_low_in`,
  `rise_high_in` — the same, as inches above the current stage.
- `reaches` / `may_reach` — `warning`, `emergency`, `bank` or null, for the median and for
  the top of the range.
- `beyond_training` — the top of the range is above `max_trained_stage_ft`, the highest the
  model has ever seen the creek. From there up the crest is an extrapolation from smaller
  storms. Every rise on record so far is inches, so near the bank this will be true.
- `skill` — above 0 means better than always quoting the typical rise. `rise_rows_skill` —
  the same on rows where the creek really came up, against "no further rise".
- `range_coverage` — the share of held-out rows whose real rise fell inside the range. Near
  0.8 is honest; well under it means the range is too narrow.
- `episodes_within_tolerance` — rises whose crest it called within 1 in, just before the
  creek started up. `time_mae_min` vs `time_mae_climatology_min` — timing error against
  always quoting the typical time to crest.
- `trustworthy` — two rises, positive `skill` and `rise_rows_skill`, and one crest called.

All three read unknown with no model yet, or with no current stage (including while the
creek node is offline). Time to crest also reads unknown when the predicted rise is under
half an inch, because the creek isn't expected to move. Spec Addendum E explains why the
project now predicts the rise rather than only the flood.

Since 0.24.0 an unvalidated candidate is no longer shown as *Creek ML Shadow Probability*:
the September 2026 candidate, never tested on a real positive, read 82–99 % on ordinary
1 ft rises on the household card.

> **Calibration note:** WH51 soil-moisture readings are relative (0–100 %) and site-specific.
> The saturated/dry endpoints need field calibration (open question #7) before the ponding
> threshold and Tier 0 condition are meaningful.
