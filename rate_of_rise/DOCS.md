# Rate of Rise

Layer 2 of the Creek Flood Early-Warning System. Runs the flood-probability +
predicted-stage inference (fast loop) and the nightly retrain/recalibrate batch, publishing
results to Home Assistant over MQTT.

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
   stays on screen until dismissed. Below it, an ordinary notification.

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
| `google_floods_api_key` | `""` | Google Flood Forecasting API key (Google Cloud project + the API enabled). Setting it enables the source: the gauges Google models within 25 mi of the site, and its forecast flood status for them. Those are neighbouring rivers, not this creek — regional context, capped at Tier 2 Watch. Blank disables it |
| `wu_api_key` | `""` | Optional (Weather Underground PWS) |
| `nwm_reach_id` | `<nwm reach id>` | NWM reach at the sensor site (open question #3) |
| `upstream_pws_ids` | `<upstream PWS 1>`, `<upstream PWS 2>` | Upstream PWS in the upstream corridor (open question #4) |
| `stage_entity` | `sensor.creek_gateway_stage` | Creek depth above the bed, published by the RFM69 gateway |
| `creek_node_status_entity` | `binary_sensor.creek_gateway_creek_node_status` | Radio-link state for the creek node, published by the same gateway: ON while the node's 60 s reports arrive, OFF after five are missed. Blank to disable the check and fall back to `stage_max_age_minutes` |
| `soil_moisture_entities` | WH51 #1, #2 | `..._soil_moisture_willow` (near house), `..._soil_moisture_field` (near creek); order is significant |
| `onsite_rain_rate_entity` | `sensor.outside_weather_station_rain_intensity` | Ecowitt |
| `onsite_rain_daily_entity` | `sensor.outside_weather_station_rain_24hr` | Ecowitt. **Currently unused** — the rolling rain accumulations are integrated from `onsite_rain_rate_entity`, not read from here |
| `usgs_downstream` | `true` | Poll USGS `<usgs downstream>` / `<usgs adjacent>` (free, no key) for lag validation |
| `snodas_swe` | `true` | Daily SNODAS snow-water-equivalent for the site cell (free, no key) |
| `nexrad_cells` | `true` | NEXRAD storm-cell tracks via IEM (free, no key) — inbound-cell ETA for storms approaching from the W/NW, where the upstream gauges cannot lead |
| `nexrad_radar_id` | `<nexrad site>` | The WSR-88D covering the site  |
| `wpc_ero` | `true` | WPC Excessive Rainfall Outlook via IEM (free, no key) — grades forecast rain against flash-flood guidance, i.e. against what the ground can absorb, a day ahead of radar |
| `onsite_temp_entity` | `sensor.outside_weather_station_outdoors_temp` | Ecowitt; needed for the rain-on-snow flag |

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
| 2 | Watch | upstream / on-site rain accumulation | No |
| 3 | Warning | stage, rate-of-rise | Yes |
| 4 | Emergency | stage near bank top | Yes |

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

When the creek node stops answering, the gateway does not blank
`sensor.creek_gateway_stage`; it simply stops updating it, and Home Assistant keeps serving
the last value the node sent. Difference the first reading after the link returns against
that stale one and the whole outage's worth of level change lands in a single loop interval:
a creek that rose 3 in over a 40-minute dropout reads as **0.6 in/min**, twelve times the
Tier 3 threshold, instead of the 0.075 in/min it actually did. With the package's
`critical_from_tier: 2`, that is a critical alarm-stream push for a creek doing nothing
unusual.

Three guards stop it, all in `app/features.py` and `app/tiers.py`:

1. **The link is checked before the reading is used.** `creek_node_status_entity` is
   packet-driven — it is OFF because reports stopped arriving, not because the number
   stopped changing — so a steady creek is never mistaken for a dead one, and a dead one is
   never mistaken for a steady creek. While it is OFF no rate is computed at all.
2. **A gap is never charged to one interval.** Samples are timestamped by the gateway's
   `last_updated`, not by when the add-on happened to poll. If more than
   `rate_of_rise_max_gap_minutes` separates two samples, the rate is withheld and the
   baseline re-seeded at the creek's current level, so the *next* loop measures real
   movement from a real starting point.
3. **The first samples back are confirmed before they can alarm.** After any dropout the
   rate must clear the threshold on `rate_of_rise_confirm_samples` consecutive gap-free
   samples before it alone raises Tier 3. The counter saturates, so on a link that has been
   up this adds no delay whatsoever; it only defers the minutes right after a reconnect,
   which is exactly when the number deserves the least trust.

The cost is bounded and one-sided: after a dropout, a Warning driven *only* by rate of rise
is delayed by `rate_of_rise_confirm_samples` loop intervals. Nothing else is delayed —
stage, model probability and the NWS floors are untouched, so a creek that is genuinely high
still warns on the first reading back. Because the delay is measured in loop intervals, set
`fast_loop_minutes: 1` to match the node's 60 s telemetry and that worst case becomes about
two minutes.

Three entities make this visible on the dashboard's *Ingestion health* card: the node's link
state, `sensor.rate_of_rise_creek_stage_age` (how old the reading behind the rate is), and
the `Stage Stale` watchdog — which now trips on a dead link holding a stale number, where
before it only tripped when the entity went away entirely.

## Persistent storage

```text
/data/datasets/parts/*.jsonl              today's rows, appended each fast loop
/data/datasets/dataset.parquet            consolidated nightly from completed parts
/data/models/registry.json                versioned artifacts + skill metrics
/data/models/model-<version>.json         a candidate/active artifact (xgboost native format)
/data/models/model-<version>.meta.json    its feature column order + horizon
/data/state/*.json                        rain/API/SNODAS accumulator state
/share/rate_of_rise/events.sqlite       annotated storm event log
```

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

> **Calibration note:** WH51 soil-moisture readings are relative (0–100 %) and site-specific.
> The saturated/dry endpoints need field calibration (open question #7) before the ponding
> threshold and Tier 0 condition are meaningful.
