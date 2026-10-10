# Gateway v2 trial and cutover

Runbook for running gateway v2 beside v1, proving it, and swapping it in. Design:
[spec](superpowers/specs/2026-10-07-gateway-v2-sd-backfill-design.md). Hardware and build:
[firmware/README.md](../firmware/README.md#gateway-v2-feather-esp32-s3--sd-store).

## 1. Set up the trial

The Device Builder wrappers (`creek-gateway-v2.yaml`, `creek-gateway-v2.prod.yaml`) pull the
packages and components from GitHub at `ref: main`, so merge the branch before building the
trial in the Device Builder. A local CLI build (`esphome run gateway.yaml` in
`firmware/esp32s3_feather_gateway/`) uses the working tree and works from the branch.

1. Flash v2 with the **trial** wrapper (`creek-gateway-v2.yaml`, no node-OTA buttons): first
   flash over USB (COM17 on the dev PC), later flashes over the air. Adopt it in HA's ESPHome
   integration as `Creek Gateway v2`.
2. List its entity IDs (Developer Tools → States, filter `creek_gateway_v2`). If HA put it
   in an area, the IDs may carry a prefix such as `outside_`; use whatever HA shows.
3. In the GW3000B's web UI, note which channel each WH51 is on (willow / field).
4. Add-on options (Settings → Add-ons → Rate of Rise → Configuration):
   - `gateway_store_url`: `http://192.168.30.21`
   - `gateway_store_token`: the gateway's `creek_store_token`
   - `backfill_entity_map` (written: v2's own entities):
     ```json
     {"node": {"stage_ft": "sensor.creek_gateway_v2_stage",
               "depth_in": "sensor.creek_gateway_v2_creek_depth",
               "distance_mm": "sensor.creek_gateway_v2_sensor_distance",
               "battery_mv": "sensor.creek_gateway_v2_creek_node_battery",
               "rssi_dbm": "sensor.creek_gateway_v2_creek_node_rssi",
               "node_status": "binary_sensor.creek_gateway_v2_creek_node_status",
               "fast": "binary_sensor.creek_gateway_v2_creek_node_fast_sampling",
               "diag_active": "binary_sensor.creek_gateway_v2_creek_node_diagnostic_active",
               "reset_cause": "sensor.creek_gateway_v2_creek_node_reset_cause",
               "cycle": "sensor.creek_gateway_v2_creek_node_cycle",
               "radio_init_failures": "sensor.creek_gateway_v2_creek_node_radio_init_failures"}}
     ```
   - `backfill_shadow_map` (logged only: v1's entities and the Ecowitt ones):
     ```json
     {"node": {"stage_ft": "sensor.creek_gateway_stage",
               "depth_in": "sensor.creek_gateway_creek_depth",
               "distance_mm": "sensor.outside_creek_gateway_sensor_distance",
               "battery_mv": "sensor.creek_gateway_creek_node_battery",
               "rssi_dbm": "sensor.creek_gateway_creek_node_rssi",
               "node_status": "binary_sensor.creek_gateway_creek_node_status",
               "fast": "binary_sensor.outside_creek_gateway_creek_node_fast_sampling",
               "diag_active": "binary_sensor.outside_creek_gateway_creek_node_diagnostic_active",
               "reset_cause": "sensor.outside_creek_gateway_creek_node_reset_cause",
               "cycle": "sensor.outside_creek_gateway_creek_node_cycle",
               "radio_init_failures": "sensor.outside_creek_gateway_creek_node_radio_init_failures"},
      "ecowitt": {"rain_total_in": "sensor.outside_weather_station_rain_total",
                  "rain_rate_in_hr": "sensor.outside_weather_station_rain_intensity",
                  "rain_24h_in": "sensor.outside_weather_station_rain_24hr",
                  "temp_f": "sensor.outside_weather_station_outdoors_temp",
                  "soil_ch2": "sensor.outside_weather_station_soil_moisture_field"}}
     ```
     Add `"soil_chN": "sensor.outside_weather_station_soil_moisture_willow"` with the
     willow probe's channel once it is reporting again (only channel 2 was, on 2026-10-07).
5. Restart the add-on. `sensor.rate_of_rise_creek_backfill_status` should read `idle`
   within a minute.

### Reading the backfill status

`sensor.rate_of_rise_creek_backfill_status` can read:

| State | Meaning |
|---|---|
| `off` | `gateway_store_url` is blank; nothing runs. |
| `v1 gateway (no store)` | The URL answers but is a v1 gateway. Re-probed hourly. Silent. |
| `unreachable` | Refused, timed out, DNS failure, or an HTTP 5xx from the gateway. Retried every pass. Silent. |
| `gateway SD not mounted` | The gateway answers but its card is not mounted (`sd_ok` false). Nothing is read and the cursors stay put; the gateway retries the mount every 30 s. Check v2's `Store SD Fault` entity and the card. |
| `idle` | Caught up with the store. |
| `backfilling N` | N store records still to process. |
| `waiting for live poll` | Gap rows are held until the first live rain poll after an add-on restart. |
| `blocked: recorder schema N` | The recorder schema is not one the writer was checked against (53). Nothing is written to the recorder. Stage log and dataset rows are still written, and the cursor moves on, so history rows for batches seen while blocked are not retried later. |
| `error: ...` | The last pass failed; the text says why. |

The gateway's status also carries `store_id`, the card's identity (16 hex characters in
`/store_id.txt`, created the first time a blank card mounts). The add-on saves it next to the
cursors in `/data/state/backfill.json`. A different id means the card was replaced or
reformatted: one WARNING (`gateway card changed; re-reading it from the start`), both cursors
back to 0, and the new card is read from the start (writes are idempotent, so nothing is
duplicated). A stream that reports a seq below the cursor on the same card is left alone and
skipped, with one WARNING (`... below cursor ... without a card change; leaving the cursor
alone`); that is not expected and worth a look.

If the add-on can't see Home Assistant's database, backfill still runs and reads `idle`, but no
history rows are written. The add-on log says so at startup, so check it on the first trial start.

Attributes: `last_run`, `cursor_node`, `cursor_ecowitt`, `inserted_states`, `shadow_states`,
`deleted_unavailable`, `imported_stat_hours`, `stage_log_rows`, `dataset_rows`,
`skipped_no_time`, `skipped_bad_time`, `stat_errors`.

Recorder writer rules, useful when reading the history afterwards:

- A reading equal to the state already in effect adds no row (numbers compare numerically). Exception: the first reading after an `unavailable` row is always written, even if unchanged.
- An `unavailable` row is removed only when gateway readings bracket it within 300 s on both
  sides, and never when it is the entity's newest real row.
- Every row written is marked; `creek/cmd/backfill_undo` removes them.
- Hourly statistics are re-imported only for measurement sensors.

Gateway records with untrustworthy time (`ts_src: none`, a missing or non-numeric timestamp, or a timestamp more than 1 h in the
future) are skipped and counted in `skipped_no_time` / `skipped_bad_time`.

### Pruning the card

*Creek Prune Gateway Store* (`button.rate_of_rise_creek_prune_gateway_store`, under the Rate of
Rise device's configuration entities) deletes old records from v2's SD card. It works during
the trial: it only talks to `gateway_store_url`, and never touches HA entities or v1.

What a press deletes, per stream: the oldest block files (10,000 records, about a week each)
while every record in the block is **both** already written to HA by backfill (at or below the
cursor) **and** older than `gateway_store_prune_days` (default 90). It stops at the first
block that fails either, and the newest block always stays. Pruned records can no longer be
re-backfilled (after `backfill_undo`, or after a card change resets the cursors), and the
card was their only raw copy, so keep the setting generous. A 32 GB card holds years.

The result appears on the add-on's last-command sensor:

| Message | Meaning |
|---|---|
| `pruned N node / M ecowitt block(s) older than D d; store now starts at ...` | Done. `0 / 0` just means nothing qualified yet. |
| `not a v2 gateway store; nothing pruned` | The URL is a v1 gateway (or not a store). Nothing was sent. |
| `not pruned: backfill has not read this card yet` | The card is not the one backfill's cursors belong to. Let backfill reach `idle` first. |
| `nothing backfilled yet; nothing to prune` | Both cursors are 0. |
| `not pruned: gateway SD not mounted` / `gateway unreachable` / `gateway card changed` | Nothing deleted; press again once fixed. A node OTA push holding the bus also reads as unreachable. |
| `gateway firmware has no prune endpoint; update v2` | v2 runs firmware from before pruning. |
| `pruning not configured (gateway_store_prune_days)` | The option is blank. |
| `gateway store backfill is off ...` | `gateway_store_url` is blank. |

## 2. Acceptance checks (all required before cutover)

| # | Check | Pass when |
|---|---|---|
| 1 | 24 h side by side | v2's Creek Node Packets, Stage and RSSI track v1's; Store Clock Source is `ntp`; Store Node Records keeps rising. |
| 2 | WiFi outage | Block 192.168.30.21 at OPNsense for 30 min, then unblock. Within 15 min (the next pass can be up to 10 min away, and the newest 2 min are held back) the status returns to `idle`. The attribute counts cover only the latest pass, so look for the first `idle` after the unblock that shows `inserted_states` > 0, or confirm in v2's Stage history that the gap is filled with no `unavailable` inside the outage. |
| 3 | HA outage | Stop HA Core for 30 min (Settings → System → Restart → Stop), then start it. v2's history is filled; the add-on log shows `backfill shadow: would insert ...` lines for v1 and Ecowitt entities; `/share/rate_of_rise/stage/` has rows across the outage; the add-on log shows `backfill: wrote N dataset row(s)`; `rain_24h_in` in the next feature row matches the GW3000B's own 24 h total. |
| 4 | Reboot while offline | With v2 blocked at OPNsense, power-cycle it. After it is unblocked: records continue the same seq, and the outage's records carry `ts_src` `rtc`. |
| 5 | v1 safety | Temporarily set `gateway_store_url` to v1's IP (192.168.30.20). Status shows `v1 gateway (no store)` or `unreachable` (both are silent), no warnings or errors in the add-on log and nothing logged per poll, and nothing written. Set it back. |
| 6 | OTA from v1 while v2 runs | Push node firmware from v1. v2 logs the telemetry before and after; neither gateway errors. |

## 3. Cutover

Prerequisites on the machine running the script: `pip install requests websocket-client`, and
a file holding an admin long-lived access token.

Order matters: the production firmware goes on v2 **before** the script runs. v1 has two
node-OTA buttons (`button.creek_gateway_push_node_firmware`,
`button.outside_creek_gateway_push_node_diagnostic_firmware`). The script pairs every v1
entity with a v2 entity of the same domain and name, and renames the v2 one to inherit v1's
id. The trial build has no buttons, so run against it the preflight stops with "v1 entities
with no v2 counterpart". With the production build installed first, the buttons pair like
everything else and keep their ids. v1 is unplugged first so that only one gateway can ever
push to the node.

1. Unplug v1.
2. Install `creek-gateway-v2.prod.yaml` on v2 in the Device Builder (copy it to
   `/config/esphome/` first; OTA). v2 now has the node-OTA buttons.
3. Dry run (the default, changes nothing):
   `python tools/gateway_cutover.py --ha-url http://192.168.20.3:8123 --token-file <file>`
   It runs the preflight and prints the config entries it would delete, the entity renames,
   and the resulting `backfill_entity_map`. Fix anything it reports. The preflight requires the
   backfill status to read exactly `idle` (it refuses during `waiting for live poll`, for example).
4. The same command with `--apply`. It waits for v1's entities to clear, retries each rename
   (3 tries), then writes the add-on options (production `backfill_entity_map`,
   `backfill_shadow_map` cleared). On any failure it prints what completed and the steps that
   remain, including the options JSON, and exits non-zero; there is no resume mode, so finish
   the remaining steps by hand.
5. Restart the add-on.

**Rollback:** plug v1 back in. Its firmware and YAML were never changed. If v2 already has the
production build (step 2), reinstall the trial wrapper on it, so only v1 can push to the node. Undo backfilled
recorder rows with an MQTT publish to `creek/cmd/backfill_undo` (payload: an ISO time to
undo from, or empty for all). Give the time an explicit offset or `Z`
(`2026-10-07T14:00:00-04:00`, `2026-10-07T18:00:00Z`): a time without one is read as the
add-on's local time, which is UTC unless the add-on's timezone was changed.
