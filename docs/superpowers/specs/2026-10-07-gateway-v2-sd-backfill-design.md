# Gateway v2: SD store-and-forward with history backfill

Date: 2026-10-07
Status: Draft, awaiting review

## Problem

The v1 gateway (Seeed XIAO ESP32-C3 + RFM69HW, [`firmware/esp32_rfm69_gateway/`](../../../firmware/esp32_rfm69_gateway/))
holds nothing. Every packet becomes a native ESPHome entity update the moment it arrives, so if
the Home Assistant host is down or the gateway is off WiFi, that reading is gone. The add-on's
stage log ([`stagelog.py`](../../../rate_of_rise/app/stagelog.py)) was added after the
2026-09-23 recorder stall for the same reason, but it reads HA's state machine, so it is blind
whenever HA is.

There is a second local-only source with the same gap. HA's `ecowitt` integration is
push-based (the GW3000B at 192.168.30.7 posts to HA's webhook), so on-site rain, temperature
and WH51 soil moisture also vanish while HA is down. Those are among the model's strongest
features.

## Goals

- **No lost local data.** Every creek-node packet and every Ecowitt reading is written to SD
  with a real timestamp, whether or not HA or WiFi is up.
- **No visible gap after recovery.** Once HA is back, the missing readings appear in HA's
  normal entity history at their real times, in HA's long-term statistics, in the add-on's
  stage log, and as dataset rows the model can train on.
- **Drop-in replacement.** v2 carries every v1 feature: entities, stage/depth math,
  installation height, radar fault, node diagnostics, both OTA push buttons, restart.
- **v1 untouched.** v1's firmware, YAML and behaviour do not change. v2 runs alongside it for a
  trial before a scripted cutover. The add-on must behave exactly as today when pointed at v1
  or at nothing.
- **The add-on stays the only brain.** The gateway records; it does not compute features.

## Non-goals

- Polling internet APIs from the gateway. It would not help when WiFi is down, and it would
  split feature logic between Python and C++. Internet sources with history APIs are re-fetched
  by the add-on after an outage instead (see section 6).
- Re-fetching forecasts as they were issued (QPF, NWM, ERO, Google Floods). These have no
  historical endpoint; gap rows leave them empty.
- SD retention or pruning. The worst case is about 3.5 MB/day, so a 32 GB card holds years.
- HA short-term (5-min) statistics. They purge after 10 days.
- Putting the gateway in Ecowitt's push path, or two gateways pushing node OTA.

## Hardware

| Part | Adafruit PID | Role |
|---|---|---|
| ESP32-S3 Feather, 8 MB flash, w.FL antenna | 5885 | MCU, WiFi. 512 KB SRAM, **no PSRAM**: stream, don't buffer. |
| Radio FeatherWing RFM69HCW 900 MHz | 3229 | 915 MHz RX/TX, same radio family as v1 |
| Adalogger FeatherWing | 2922 | PCF8523 RTC (I2C 0x68, CR1220 backup) + microSD |

Both wings share the Feather's single hardware SPI bus.

| Signal | GPIO | Notes |
|---|---|---|
| SCK / MOSI / MISO | 36 / 35 / 37 | Feather hardware SPI, shared by SD and RFM69 |
| SD CS | 10 | Adalogger default (D10) |
| RFM69 CS | 6 | D6, via wing jumper pad |
| RFM69 IRQ (DIO0) | 5 | D5, via wing jumper pad |
| RFM69 RST | 9 | D9, via wing jumper pad. Active HIGH, idles low, as v1 |
| I2C SDA / SCL | 3 / 4 | PCF8523 |

The first hardware task in the plan verifies every GPIO against the board silkscreen and
Adafruit's pinout before soldering jumpers. Unlike the XIAO C3, none of these are strapping
pins.

## Design

### 1. Firmware layout

```
firmware/esp32s3_feather_gateway/
  gateway.base.yaml          the whole v2 device
  gateway.yaml               local CLI wrapper (components from ../ and ./components)
  creek-gateway-v2.yaml      Device Builder wrapper, trial (no OTA push buttons)
  creek-gateway.prod.yaml    Device Builder wrapper, post-cutover (OTA buttons included)
  secrets.yaml.example
  components/creek_store/    new: clock, SD log, Ecowitt poll, replay API
```

v2 reuses `rfm69_gateway` from `firmware/esp32_rfm69_gateway/components/` rather than a copy.
The only change to it is an **additive packet hook**: `add_on_packet_callback(...)`, invoked at
the end of `handle_packet_()` with the decoded fields and the RSSI. The hook and its call site
sit inside `#ifdef USE_RFM69_PACKET_HOOK`, a define only `creek_store`'s codegen adds. v1's build
never defines it, so v1's compiled firmware is unchanged even though the Device Builder pulls the
component from `main`. The plan includes a check that v1 still compiles and that its generated
source does not contain the hook.

**Shared SPI bus.** v1's `radio_mutex_` becomes the bus mutex. `creek_store` takes the same
mutex around every SD transaction, so an SD write can never interleave with the OTA transfer
task's radio traffic on the dual-core S3. The main loop's non-blocking try-take rule from the
OTA design ([2026-09-12 spec](2026-09-12-gateway-ota-push-design.md)) applies to SD writes too:
when the bus is busy, the record is queued in RAM (bounded, 64 entries) and flushed on a later
loop.

### 2. `creek_store` component

**Clock.**
- The PCF8523 is the time source for every record.
- It is set from ESPHome `time: sntp` (the router's NTP, not HA's) on first sync and re-synced
  every 6 h.
- Each record carries `ts_src`:
  - `ntp`: synced within the last 24 h
  - `rtc`: RTC valid, no recent sync
  - `none`: RTC lost power and no network since boot. The record is kept and the add-on skips it.
- PCF8523 support is about a dozen I2C register reads and writes, implemented in the
  component. ESPHome core has no PCF8523 platform.

**Records.** One NDJSON line per record, appended to daily files named by **UTC date**:

```
/node/2026-10-07.ndjson
{"seq":18234,"ts":1791378600.4,"ts_src":"ntp","rssi":-72,"d":812,"v":4012,"f":0,"g":0,
 "r":"POR","n":143,"i":0,"mount":1105,"stage_ft":0.958,"depth_in":11.5}

/ecowitt/2026-10-07.ndjson
{"seq":5521,"ts":1791378600.1,"ts_src":"ntp","rain_event_in":0.00,"rain_rate_in_hr":0.00,
 "rain_day_in":0.00,"rain_24h_in":0.00,"rain_year_in":29.84,"temp_f":45.9,
 "soil":{"2":61}}
```

- Node fields are the decoded packet fields with the wire keys (`d v f g r n i`). Missing fields
  are omitted, and a null distance is `"d":null`.
- `stage_ft`/`depth_in` are computed with **the same rules as v1's `on_value` lambda**: NaN
  on null distance or a lost target, clamped to the floor in the blanking zone. In v2 those
  rules live in one header (`creek_store/stage_math.h`) called by both v2's `on_value` lambda
  and the store, so v2's live entities and its records cannot drift apart. v1's lambda is not
  touched; a host test pins the header against a table of v1-lambda outputs. NaN is written as
  `null`.
- Ecowitt: `GET http://<ecowitt_host>/get_livedata_info` every 60 s (configurable), parsed by
  id (`0x0D` event, `0x0E` rate, `0x10` day, `0x7C` 24 h, `0x13` year, `0x02` outdoor temp,
  `ch_soil[].humidity` keyed by channel). A failed poll writes nothing and increments a
  failure counter. It is never retried in a tight loop.

**Sequence numbers.** Each stream's `seq` only ever increases. At boot it is recovered from the
last complete line of that stream's newest file. A torn final line, from a power cut mid-write,
is ignored and truncated. Files are opened, appended and closed per record (or per batch when
the queue drains), so a power cut loses at most the record being written.

**Replay API.** Registered on ESPHome's `web_server_base` (port 80). Every request needs
`Authorization: Bearer <store_token>` (a secret); without it the response is 401.

- `GET /store/status` returns:
  ```json
  {"store_schema":1,"device":"creek-gateway-v2","fw":"<esphome version>","now":1791378660,
   "ts_src":"ntp","sd_ok":true,"sd_free_mb":29812,
   "streams":{"node":{"first":1,"last":18234},"ecowitt":{"first":1,"last":5521}}}
  ```
- `GET /store/records?stream=node&after=<seq>&limit=<n≤500>` returns `application/x-ndjson`,
  records with `seq > after` in ascending order, at most `limit`. The response streams from
  SD a line at a time, with no buffer bigger than one line.
  - Finding the start seq uses a small in-RAM index of each day file's first seq, built at
    boot.
  - Header `X-Store-Last` carries the stream's current last seq, so the client knows whether
    to page again.

**Entities** (new, diagnostic): SD OK, Store Free Space (MB), Clock Source (`ntp|rtc|none`),
Node Records (last seq), Ecowitt Records (last seq), Ecowitt Poll Failures.

### 3. Ported v1 configuration and deliberate differences

`gateway.base.yaml` mirrors v1's file section for section: substitutions, the
`rfm69_gateway:` block with every sensor, the installation-height number, the stage/depth
template sensors, WiFi/uptime/IP diagnostics, both OTA buttons, restart. Differences:

- `esp32: board: adafruit_feather_esp32s3`, `variant: esp32s3`, with the S3 pins above. The
  `CONFIG_APP_REPRODUCIBLE_BUILD: "n"` and `toolchain: platformio` workarounds carry over.
- `api: reboot_timeout: 0s` and `wifi: reboot_timeout: 0s`. ESPHome's default reboots the
  device after 15 min without an API client or WiFi, which would loop during exactly the
  outages v2 exists for.
- Logger on the S3's native USB.
- `device_name: creek-gateway-v2` for the trial. The trial wrapper omits the two OTA push
  buttons, so only v1 can push to the node. The production wrapper includes them.

### 4. Add-on backfill: activation and v1 safety

New add-on options:

| Option | Default | Meaning |
|---|---|---|
| `gateway_store_url` | blank | e.g. `http://192.168.30.21`. Blank turns backfill off entirely. |
| `gateway_store_token` | blank | Bearer token, same value as the gateway secret |
| `backfill_entity_map` | blank | Record field → HA entity the recorder destinations **write** (section 5). Blank: recorder destinations A/B do nothing; C and D still run. |
| `backfill_shadow_map` | blank | Same shape; recorder writes for these entities are **logged only**, never written |

Behaviour by case:

| Case | Behaviour |
|---|---|
| URL blank | No backfill object is created. No requests, no files, no log lines. The status entity reads `off`. |
| Any response to `/store/status` that isn't a valid `store_schema` 1 document (404, HTML, ESPHome's own web page, a JSON without `store_schema`) | Treated as "no store". This is what v1 looks like, and what v2 looks like if its web server is off. One DEBUG log line, status `v1 gateway (no store)`, re-probed hourly. |
| Connection refused, timeout, DNS failure | Treated as "unreachable": the gateway is down or the network is. DEBUG log, status `unreachable`, re-probed every 10 min. Not an error. |
| 401 | Status `error: bad token`, one WARNING per hour |
| Valid status | Reconcile (section 5) |

The backfill runs on its own thread with its own timeouts (10 s per request). It cannot block
or delay the fast loop. Any exception inside it is caught, logged and surfaced on the status
entity, and never propagates to the main loop.

### 5. Reconciliation

The add-on keeps a cursor per stream in `/data/state/backfill.json`: the last seq whose
writes to all destinations succeeded. Each pass (at startup, then every 10 min):

1. Page `/store/records` after the cursor until `X-Store-Last` is reached.
2. Hold back anything newer than 2 min. It is still live, and HA probably has it.
3. Drop `ts_src: none` records and count them.
4. Write the batch to each destination below. If every destination succeeds, advance the
   cursor to the batch's last seq. If any fails, leave the cursor where it was and retry next
   pass. Every destination is idempotent, so a retried batch inserts nothing twice.

**Destination A: HA recorder states.** This is a direct write to `/homeassistant/home-assistant_v2.db`
(the add-on gains `homeassistant_config:rw`).

- **Guard.** `SELECT schema_version FROM schema_changes ORDER BY change_id DESC LIMIT 1` must
  be in the allow-list `{53}`. Anything else skips this destination (the others still run) and
  sets the status to `blocked: recorder schema N`. Adding a version to the list is a code
  change made after re-checking the schema.
- **Entity resolution.** Each mapped `entity_id` must already exist in `states_meta`. The
  writer never creates metadata. It reuses the entity's most recent `attributes_id`; these
  sensors' attributes are static. A missing entity is skipped and logged once.
- **Matching.** For each record and mapped entity, look up existing `states` rows in
  `[ts − 20 s, ts + 20 s]`. Any match means HA already has it, so nothing is inserted.
- **Insert.**
  - `state` is formatted with the entity's precision. `NaN`/null becomes `unknown`, as the
    live entity would publish.
  - `last_updated_ts = last_changed_ts = last_reported_ts = ts`, `old_state_id = NULL`,
    `origin_idx = 0`.
  - `context_id_bin` is a 16-byte ULID-shaped value whose first 4 bytes are the fixed marker
    `CB 0F 11 ED` ("creek backfill"), so backfilled rows are identifiable.
- **Gap cleanup.** A gap is a span the batch fills where the recorder had no valid state for
  longer than 3 report intervals. Inside it, `unavailable`/`unknown` rows for mapped entities
  are deleted. Any row whose `old_state_id` points at a deleted row is first set to NULL.
- **Transactions.** `BEGIN IMMEDIATE` with a 30 s busy timeout, at most 500 rows per
  transaction, so the recorder (committing every second) never waits long.
- **Undo.** `tools/backfill_undo.py --since <iso>` deletes every row carrying the marker
  (after NULL-ing references to it). Deleted `unavailable` rows are not restored; they carried
  no data.

**Destination B: HA long-term statistics.**
- For each affected hour and each mapped entity with `state_class: measurement`, recompute
  mean/min/max from the recorder's states for that hour, now including the backfilled rows.
- Import through the websocket command `recorder/import_statistics`
  (`source: recorder`, `statistic_id` = the entity id) via `ws://supervisor/core/websocket`.
- This adds `websocket-client` to `requirements.txt`.
- If Destination A was skipped by the schema guard, B is skipped too.

**Destination C: add-on stage log.** Merge node records into
`/share/rate_of_rise/stage/YYYY-MM-DD.csv`.
- Columns: `reading_ts = ts`, `logged_ts = now`, `stage_ft`.
- A record is skipped if a row exists within ±2 s.
- Each touched day file is rewritten sorted by `reading_ts`, via a temp file and rename.

**Destination D: training dataset and live accumulators.** This one is described in section 6.

**Entity maps.** Record field → entity_id, per stream. Both options are JSON objects:
`{"node": {"stage_ft": "sensor.creek_gateway_v2_stage", ...}, "ecowitt": {...}}`.
- **During the trial** `backfill_entity_map` targets v2's own entities
  (`sensor.creek_gateway_v2_stage`, …) and nothing else. `backfill_shadow_map` holds v1's
  entities and the Ecowitt entities (`sensor.outside_weather_station_rain_total`, …), so the
  production writes are rehearsed in the log on real gaps without touching production history.
- **After cutover** the production ids (v1's names plus Ecowitt) move into
  `backfill_entity_map` and the shadow map is cleared.
- Node status (`binary_sensor`) is backfilled as `on` for any span with node records.

### 6. Dataset gap rows and source re-fetch

The add-on writes a dataset row every fast loop (5 min). When the HA host was down, the
add-on was down too, so those rows never existed.

**Finding the gap.** After Destinations A–C succeed for a batch, list the 5-min slots
covered by the batch's time range that have no dataset row.

**Building each row** reuses the live code paths, called with an explicit `as_of` time:

- **Stage and stage history** (`stage_ft`, rate of rise, `stage_change_1h_in`,
  `stage_above_6h_low_in`): from node records, through the same `features.py` functions the
  live loop uses.
- **On-site rain** (`rain_{1,3,6,24,72}h_in`): from Ecowitt `rain_year_in` counter deltas,
  through the same counter-delta logic as `rain.py`.
- **Soil moisture and temperature**: from Ecowitt records, nearest reading at or before `as_of`.
- **Re-fetched internet sources**, each source gaining a `poll_at(as_of)` method next to
  `poll()`:

  | Source | History endpoint |
  |---|---|
  | `usgs` | NWIS IV `startDT`/`endDT` |
  | `wu` | PWS history API |
  | `radar_cells` | IEM `sts`/`ets` |
  | `alerts` | `api.weather.gov/alerts` with `start`/`end`/`point` |
  | `snodas` | by date; already date-addressed |

  Any re-fetch failure leaves that feature empty for the row. It never fails the batch.
- **Forecast features** (`nws` QPF, `nwm`, `ero`, `google_floods`): empty.
- **Flags.** `backfilled=True` on the row, and `stage_held=False`.

Gap rows train like normal rows. xgboost handles the empty forecast features natively, and
the flag stays in the dataset for later analysis.

**Live accumulators.** The on-site rain `RollingAccumulator` and the API index skip any gap
longer than 15 min (`MAX_GAP_S`). After a backfill, the gap's rain increments, derived from
Ecowitt counter deltas, are inserted into the accumulator's ring and the API index is
re-decayed across the gap. Live 24 h and 72 h totals immediately after an outage are then
correct rather than short. This is the one place backfill affects live output, and it makes
the live value more correct, not less.

**Never live.** Backfilled data does not reach live tiers, rate-of-rise alerts,
notifications, or MQTT feature publishes. Its only effect on live output is through the
corrected accumulator state above.

### 7. Status entity

`sensor.creek_backfill_status`, published via the add-on's existing MQTT discovery.

- **State:** `off` | `v1 gateway (no store)` | `unreachable` | `idle` | `backfilling N` |
  `blocked: …` | `error: …`
- **Attributes:**
  - `last_run`
  - `cursor_node`, `cursor_ecowitt`
  - `inserted_states`, `shadow_states` (would-insert count from the shadow map),
    `deleted_unavailable`, `imported_stat_hours`, `stage_log_rows`, `dataset_rows` (all for
    the last pass)
  - `skipped_no_time`

### 8. Trial and cutover

**Trial.**
- v2 runs as `creek-gateway-v2` beside v1. Both hear every packet: the node sends with
  `radio.send()` and requests no ACK, so a second receiver cannot collide.
- The add-on's live path keeps reading v1's entities. Backfill writes v2's entities and
  shadows (logs only) v1's and the Ecowitt entities.

**Acceptance checks**, all required before cutover:
1. 24 h side by side: v2 packet count, stage and RSSI track v1's. Every packet is on SD with
   `ts_src` `ntp` or `rtc`.
2. WiFi outage: block v2 at OPNsense for 30 min. After it reconnects, v2's HA history has
   no gap and no `unavailable` rows inside it.
3. HA outage: stop HA Core for 30 min. After restart: v2's history filled, the shadow log
   lists the v1 and Ecowitt rows it would have written, stage log filled,
   dataset gap rows present with `backfilled=True`, rain accumulator totals match the GW3000's
   own 24 h counter.
4. Power-cycle v2 while it is offline: seq continues, and records carry `ts_src: rtc`.
5. Point `gateway_store_url` at v1's IP: status `v1 gateway (no store)`, no writes, nothing
   above DEBUG.
6. Push node firmware from v1 while v2 runs: v2 keeps logging the telemetry around it, and
   neither gateway errors.

**Cutover.** `tools/gateway_cutover.py` drives HA's websocket API:
1. **Preflight.** v1's device is offline (you unplug it first), v2's store is reconciled up to
   2 min ago, and every v1 entity_id has a v2 counterpart. It prints the mapping and stops on
   any mismatch.
2. Remove v1's ESPHome config entry and its entities from the registry.
3. Rename each v2 entity to the matching v1 entity_id. The recorder keys history by the
   entity_id string, so the renamed entities continue v1's history. The plan verifies this on a
   scratch HA instance before it is run for real.
4. Write the production entity map into `backfill_entity_map` and clear `backfill_shadow_map`.
5. You switch the Device Builder to `creek-gateway.prod.yaml` (OTA buttons back) and install
   over OTA.

**Rollback.** Plug v1 back in. Its firmware and YAML were never changed.

### 9. Testing

**Firmware.**
- Pure logic (record encode, torn-line recovery, seq-index lookup, Ecowitt JSON parse, the
  shared stage/depth rules) lives in dependency-free headers. Those are tested with host `g++`
  in a new CI job.
- A new CI job runs `esphome compile` for v2's config. It also compiles v1 and asserts
  `USE_RFM69_PACKET_HOOK` is absent from v1's generated source.

**Add-on** tests follow the repo's existing style: standalone `tests/test_*.py` scripts with a
`main()`, run by `tests.yml`. Coverage:
- fake gateway server (a `http.server` thread) for paging, `X-Store-Last`, and the 401 path
- recorder writer against a fixture SQLite built from the live schema-53 DDL (read once,
  immutable, from `H:\home-assistant_v2.db`): inserts, ±20 s matching, idempotent re-run, gap
  cleanup with `old_state_id` re-linking, marker, undo tool
- schema guard refusing version 54
- statistics recompute (the websocket call is faked)
- stage log merge and sort
- gap-row construction with recorded source fixtures for each `poll_at`
- accumulator gap insertion
- **v1 regression** for each of:
  - blank URL (no requests, no thread)
  - 404
  - ESPHome HTML page
  - connection refused
  - timeout

  Each asserts that nothing is logged above DEBUG and the fast loop's output is unchanged.
- Run under pandas 3 as well as 2.2, per the known CI/local split.

**Bench, before the trial.**
- Compile spike: ESPHome Arduino build on the S3 with `SD.h` + RFM69. If FATFS/SD is in
  `EXCLUDE_COMPONENTS`, the fix is decided here, before any other work.
- SPI sharing: hammer SD writes while a node OTA push runs, and check that the transfer
  completes and no record is lost.

**Versioning.** Add-on minor version bump with a CHANGELOG entry.

## Risks

| Risk | Mitigation |
|---|---|
| ESPHome Arduino build excludes FATFS/SD | Compile spike is the first task; fallback is ESP-IDF `sdmmc`/`esp_vfs_fat` via sdkconfig, decided before other work |
| Direct recorder writes break on an HA upgrade | Schema allow-list, idempotent inserts, marker + undo tool, blank-map default, shadow map to rehearse |
| HA's recorder holding a write lock during a big backfill | `BEGIN IMMEDIATE`, 500-row transactions, 30 s busy timeout |
| Add-on (USER VLAN) blocked from gateway port 80 (IoT VLAN) | Plan verifies the OPNsense rule; HA already reaches the gateway's 6053 |
| Entity rename at cutover not carrying history as expected | Verified on a scratch HA instance first; preflight stops on any mismatch |
| RTC drift | PCF8523 drifts seconds per day; NTP re-sync every 6 h; ±20 s matching tolerance |
