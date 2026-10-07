# Gateway v2 trial and cutover

Runbook for running gateway v2 beside v1, proving it, and swapping it in. Design:
[spec](superpowers/specs/2026-10-07-gateway-v2-sd-backfill-design.md). Hardware and build:
[firmware/README.md](../firmware/README.md#gateway-v2-feather-esp32-s3--sd-store).

## 1. Set up the trial

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
| `off` | `gateway_store_url` is blank, or the add-on has no recorder access. Nothing runs. |
| `v1 gateway (no store)` | The URL answers but is a v1 gateway. Re-probed hourly. Silent. |
| `unreachable` | Refused, timed out or DNS failure. Retried every pass. Silent. |
| `idle` | Caught up with the store. |
| `backfilling N` | N store records still to process. |
| `waiting for live poll` | Gap rows are held until the first live rain poll after an add-on restart. |
| `blocked: recorder schema N` | The recorder schema is not one the writer was checked against (53). Nothing is written; the dataset and stage-log passes are unaffected. |
| `error: ...` | The last pass failed; the text says why. |

Attributes: `last_run`, `cursor_node`, `cursor_ecowitt`, `inserted_states`, `shadow_states`,
`deleted_unavailable`, `imported_stat_hours`, `stage_log_rows`, `dataset_rows`,
`skipped_no_time`, `skipped_bad_time`, `stat_errors`.

Recorder writer rules, useful when reading the history afterwards:

- A reading equal to the state already in effect adds no row (numbers compare numerically).
- An `unavailable` row is removed only when gateway readings bracket it within 300 s on both
  sides, and never when it is the entity's newest real row.
- Every row written is marked; `creek/cmd/backfill_undo` removes them.
- Hourly statistics are re-imported only for measurement sensors.

Gateway records with untrustworthy time (`ts_src: none`, or a timestamp more than 1 h in the
future) are skipped and counted in `skipped_no_time` / `skipped_bad_time`.

## 2. Acceptance checks (all required before cutover)

| # | Check | Pass when |
|---|---|---|
| 1 | 24 h side by side | v2's Creek Node Packets, Stage and RSSI track v1's; Store Clock Source is `ntp`; Store Node Records keeps rising. |
| 2 | WiFi outage | Block 192.168.30.21 at OPNsense for 30 min, then unblock. Within 10 min the status returns to `idle` with `inserted_states` > 0, and v2's Stage history shows no gap and no `unavailable` inside the outage. |
| 3 | HA outage | Stop HA Core for 30 min (Settings → System → Restart → Stop), then start it. v2's history is filled; the add-on log shows `backfill shadow: would insert ...` lines for v1 and Ecowitt entities; `/share/rate_of_rise/stage/` has rows across the outage; the add-on log shows `backfill: wrote N dataset row(s)`; `rain_24h_in` in the next feature row matches the GW3000B's own 24 h total. |
| 4 | Reboot while offline | With v2 blocked at OPNsense, power-cycle it. After it is unblocked: records continue the same seq, and the outage's records carry `ts_src` `rtc`. |
| 5 | v1 safety | Temporarily set `gateway_store_url` to v1's IP (192.168.30.20). Status shows `v1 gateway (no store)` or `unreachable` (both are silent), nothing in the add-on log above DEBUG, and nothing written. Set it back. |
| 6 | OTA from v1 while v2 runs | Push node firmware from v1. v2 logs the telemetry before and after; neither gateway errors. |

## 3. Cutover

Prerequisites on the machine running the script: `pip install requests websocket-client`, and
a file holding an admin long-lived access token.

1. Unplug v1.
2. Dry run (the default, changes nothing):
   `python tools/gateway_cutover.py --ha-url http://192.168.20.3:8123 --token-file <file>`
   It runs the preflight and prints the config entries it would delete, the entity renames,
   and the resulting `backfill_entity_map`. Fix anything it reports.
3. The same command with `--apply`. It waits for v1's entities to clear, retries each rename
   (3 tries), then writes the add-on options (production `backfill_entity_map`,
   `backfill_shadow_map` cleared). On any failure it prints what completed and the steps that
   remain, including the options JSON, and exits non-zero; there is no resume mode, so finish
   the remaining steps by hand.
4. Restart the add-on. Install `creek-gateway-v2.prod.yaml` in the Device Builder (copy it to
   `/config/esphome/` first) so the node-OTA buttons come back.

**Rollback:** plug v1 back in. Its firmware and YAML were never changed. Undo backfilled
recorder rows with an MQTT publish to `creek/cmd/backfill_undo` (payload: an ISO time to
undo from, or empty for all).
