# Gateway v2: Nextion touch display

Date: 2026-10-10
Status: Approved 2026-10-10

## Problem

Gateway v2 ([`firmware/esp32s3_feather_gateway/`](../../../firmware/esp32s3_feather_gateway/)) keeps
recording through Home Assistant and WiFi outages, but the only way to see what it knows is
through HA. During exactly the outages v2 was built for, there is nothing on site that shows the
creek level, the rain, or whether the node is still talking.

A Nextion NX3224T024 (2.4", 320×240, resistive touch, 4 MB flash) on the gateway fixes that: a
local screen fed by the gateway's own data, with a settings page for the few operations that
make sense at the box.

## Goals

- **Startup image.** `firmware/nextion_display/assets/startup.png` the moment the display has
  power, before the ESP32 has booted.
- **Main page with the basics**: creek stage and depth, rain rate and amounts, temperature,
  battery levels (creek node and gateway), node / HA / SD status, and the add-on's alert tier
  and rate of rise.
- **Works with HA down.** Everything except the two add-on outputs comes from the gateway itself
  (radio, Ecowitt poll, fuel gauge, SD store). The add-on outputs show `--` when the API is
  disconnected rather than a stale value.
- **Settings page**: restart the gateway, display brightness, display sleep timeout, and update
  the display's firmware (TFT) over WiFi.
- **Display stays up on battery.** The display is powered from the gateway's LiPo through a 5 V
  boost, and sleeps after a touch timeout.
- **TFT updates without touching the box.** The exported TFT lives in the repo; the gateway pulls
  it over WiFi from a button on the settings page or in HA.

## Non-goals (this round)

- Node OTA push or installation height from the display.
- Waking the display on an alert tier, graphs, history.
- Any change to v1, to the add-on, or to the HA packages.
- Computing features on the gateway. The add-on stays the only brain (v2 spec); the display
  only shows what the add-on already publishes.

## Hardware

| Part | Role |
|---|---|
| Nextion NX3224T024 (Basic, 2.4") | 320×240 landscape, resistive touch, 4 MB flash, 5 V supply (4.75–7 V), 3.3 V TTL serial |
| Pololu U1V11F5 5 V step-up (product 2562) | Display supply from the LiPo. Same part as the creek node's radar rail. 0.5–5.5 V in. |

### Wiring

| From | To | Notes |
|---|---|---|
| Feather BAT | U1V11F5 VIN | The display keeps running on battery |
| Feather GND | U1V11F5 GND, Nextion GND (black) | Common ground |
| U1V11F5 SHDN | U1V11F5 VIN | Always enabled |
| U1V11F5 VOUT | Nextion 5V (red) | |
| Feather TX, GPIO39 | Nextion RX (yellow) | 3.3 V both ways; no level shifter |
| Feather RX, GPIO38 | Nextion TX (blue) | |

GPIO38/39 are unused on v2 (see the wiring block in `gateway.base.yaml`).

### Power budget

About 90 mA at 5 V with the backlight on (Nextion datasheet), which is about 145 mA from a 3.7 V
pack through the boost. Asleep, the backlight is off and the draw drops sharply. Both numbers
are measured at bring-up (inline meter on VIN of the boost). The Feather's charger also feeds
this load when USB is present. Bring-up confirms the pack still charges on USB with the display
awake (the MAX17043 voltage rises over 30 min).

### Serial port

The logger is on USB-Serial-JTAG, so ESPHome's IDF UART driver
(`uart_component_esp_idf.cpp`, 2026.7.3) hands the display **UART0**. Arduino's `Serial` is also
UART0, and the RFM69 library prints to it. Nothing in the gateway calls `Serial.begin`, so those
prints are dropped inside the Arduino core and never reach the wire. The bring-up log must show
no Nextion "invalid instruction" warnings. Seeing them would mean something began `Serial`.

## Display design (HMI)

The HMI is built by hand in the Nextion Editor. The files live in
[`firmware/nextion_display/`](../../../firmware/nextion_display/):
`creek_gateway_v2_display.HMI` (source), `creek_gateway_v2_display.tft` (export, what the
display runs) and `assets/`. This section is the contract between the HMI and the firmware. The
firmware addresses components by **name** for output and by **page/component id** for touches.
Both must match exactly. The README in that folder repeats this contract as a build checklist.

### Global HMI settings

- Display: NX3224T024, horizontal (landscape, 90°), 320×240.
- `program.s` (before `page 0`): `baud=115200` and `bkcmd=0`.
- Pictures, in this order: **pic 0** = `assets/startup.png`, **pic 1** = `assets/bg.png`.
- Fonts (Font Generator, encoding **iso-8859-1** so `°` is the single byte `0xB0`), in this
  order:
  - **font 0**: 16 px, for labels and small values.
  - **font 1**: 24 px, for the tier and rain rate.
  - **font 2**: 48 px, for stage.
- Text components: `sta` = crop image, `picc` = 1, so the background shows through. The default
  `pco` is white (65535). `txt_maxl` is 20, except `tMsg`, which is 200; the Editor's
  default of 10 would cut off `depth 28.9 in`.
- Buttons: `sta` = solid color, with a distinct `bco2` (pressed colour) so a resistive press
  gives visible feedback.
- Every page's **Postinitialize Event** contains `sendme`. That is the only way ESPHome learns the
  page changed when navigation happens inside the HMI.

### Pages

| Id | Name | Background | Purpose |
|---|---|---|---|
| 0 | `splash` | `sta`=image, `pic`=0 (startup.png) | Shown from power-on until the gateway takes over |
| 1 | `main` | `sta`=image, `pic`=1 (bg.png) | The basics |
| 2 | `settings` | `sta`=image, `pic`=1 | Operations |
| 3 | `confirm` | `sta`=image, `pic`=1 | Yes/no for restart and display update |

Component ids are assigned by the Editor in creation order (the page itself is 0). Create
components **in the order listed**. The firmware relies on the ids of the touch-reporting
buttons, and on every page's ids if components are added later.

### Page 1 `main` (component name = what the firmware writes)

| # | Name | Type | x, y, w, h | Font | Example text |
|---|---|---|---|---|---|
| 1 | `tNode` | Text, left | 4, 2, 120, 22 | 0 | `node 42 s` |
| 2 | `tHA` | Text, center | 128, 2, 90, 22 | 0 | `HA ok` |
| 3 | `tSD` | Text, right | 222, 2, 94, 22 | 0 | `SD ok` |
| 4 | `tStage` | Text, left | 4, 30, 182, 56 | 2 | `2.41 ft` |
| 5 | `tDepth` | Text, left | 4, 88, 182, 22 | 0 | `depth 28.9 in` |
| 6 | `tTier` | Text, center | 190, 30, 126, 34 | 1 | `Watch` |
| 7 | `tRise` | Text, center | 190, 68, 126, 22 | 0 | `+0.05 in/min` |
| 8 | `tRate` | Text, left | 4, 120, 182, 30 | 1 | `0.42 in/h` |
| 9 | `tTemp` | Text, right | 230, 124, 86, 22 | 0 | `61°F` |
| 10 | `tEvent` | Text, left | 4, 154, 150, 22 | 0 | `event 1.37 in` |
| 11 | `t24h` | Text, left | 160, 154, 156, 22 | 0 | `24 h 1.52 in` |
| 12 | `tNodeBat` | Text, left | 4, 184, 112, 20 | 0 | `node 4.02 V` |
| 13 | `jNodeBat` | Progress bar | 4, 206, 112, 14 | – | val 0–100 |
| 14 | `tGwBat` | Text, left | 122, 184, 112, 20 | 0 | `gw 87 %` |
| 15 | `jGwBat` | Progress bar | 122, 206, 112, 14 | – | val 0–100 |
| 16 | `bSet` | Button | 240, 184, 76, 52 | 0 | `Settings` |

`bSet` Touch Release Event: `page settings`. It needs no component id.

### Page 2 `settings`

| # | Name | Type | x, y, w, h | Font | Touch Release |
|---|---|---|---|---|---|
| 1 | `tTitle` | Text (static) | 4, 6, 200, 30 | 1 | – `Settings` |
| 2 | `bBack` | Button | 240, 4, 76, 40 | 0 | code `page main` |
| 3 | `tBriLbl` | Text (static) | 4, 60, 112, 24 | 0 | – `Brightness` |
| 4 | `bBriDn` | Button | 120, 52, 50, 40 | 1 | **Send Component ID** `-` |
| 5 | `tBri` | Text, center | 172, 58, 76, 28 | 0 | – `70 %` |
| 6 | `bBriUp` | Button | 250, 52, 50, 40 | 1 | **Send Component ID** `+` |
| 7 | `tSlpLbl` | Text (static) | 4, 108, 112, 24 | 0 | – `Sleep after` |
| 8 | `bSlpDn` | Button | 120, 100, 50, 40 | 1 | **Send Component ID** `-` |
| 9 | `tSlp` | Text, center | 172, 106, 76, 28 | 0 | – `2 min` |
| 10 | `bSlpUp` | Button | 250, 100, 50, 40 | 1 | **Send Component ID** `+` |
| 11 | `bUpdate` | Button | 4, 156, 150, 50 | 0 | **Send Component ID** `Update display` |
| 12 | `bRestart` | Button | 166, 156, 150, 50 | 0 | **Send Component ID** `Restart gateway` |
| 13 | `tVer` | Text (static) | 4, 216, 312, 20 | 0 | – `HMI v1  2026-10-10` |

### Page 3 `confirm`

| # | Name | Type | x, y, w, h | Font | Touch Release |
|---|---|---|---|---|---|
| 1 | `tMsg` | Text, multi-line (`isbr`=1) | 10, 16, 300, 140 | 0 | – |
| 2 | `bNo` | Button | 20, 172, 130, 56 | 0 | code `page settings` `Cancel` |
| 3 | `bYes` | Button | 170, 172, 130, 56 | 0 | **Send Component ID** `Confirm` |

### Text and colour rules

Missing values read `--` in grey (33808). Colours are RGB565: white 65535, green 2016, yellow
65504, orange 64512, red 63488, magenta 63519.

| Field | Shows | Blank (`--`) when |
|---|---|---|
| `tStage` | `2.41 ft` | stage is NaN (node offline or target lost; the gateway already blanks it) |
| `tDepth` | `depth 28.9 in` | depth is NaN |
| `tTier` | the add-on's label for tier 0–4: All-clear (green), Advisory (yellow), Watch (orange), Warning (red), Emergency (magenta) | HA API disconnected, or the state is not 0–4 |
| `tRise` | `+0.05 in/min`, the add-on's unit | HA API disconnected or NaN |
| `tRate` / `tEvent` / `t24h` / `tTemp` | latest good Ecowitt poll | no good poll in the last 5 min (interval is 60 s) |
| `tNode` | `node 42 s` (time since the last packet; `s` / `min` / `h`); `node offline` in red when the node status is offline | node status unknown since boot |
| `tHA` | `HA ok` green / `HA offline` red | – |
| `tSD` | `SD ok` green / `SD fault` red | SD state unknown |
| `tNodeBat`, `jNodeBat` | `node 4.02 V`; bar 100/75/50/25/0 at ≥ 4.05 / 3.85 / 3.70 / 3.55 V / below (the bands of HA's "Creek Node Battery Level"); bar green ≥ 75, yellow 50, red ≤ 25 | no battery reading |
| `tGwBat`, `jGwBat` | `gw 87 %` (MAX17043, clamped 0–100); bar green ≥ 50, yellow ≥ 20, red below | no gauge reading |

## Firmware design

### Data sources

| Value | Source | Notes |
|---|---|---|
| Stage, depth | `id(creek_stage)`, `id(creek_depth_in)` | existing |
| Node status | rfm69 `node_status` binary sensor | gains `id: node_online` |
| Node last heard | `id(creek_radio).add_on_packet_callback(...)` storing `millis()` | v2 already compiles the hook in |
| Node battery | rfm69 `battery_voltage` (mV) | gains `id: node_battery_mv` |
| Gateway battery | `max17043` `battery_level` | gains `id: gw_battery_level` |
| SD fault | creek_store `sd_fault` | gains `id: store_sd_fault` |
| Rain rate, event, 24 h, temperature | creek_store's latest good Ecowitt poll, through a new accessor | see below |
| Tier, rate of rise | `homeassistant` sensors importing `sensor.rate_of_rise_creek_alert_tier` (numeric 0–4) and `sensor.rate_of_rise_creek_rate_of_rise` (in/min); both `internal: true` | HA only |
| HA connected | `api::global_api_server->is_connected()` | |

The four new ids are additive. Entity names and unique ids do not change.

**creek_store change.** `collect_ecowitt_()` already holds every good reading on the main task.
It also keeps a copy plus the `millis()` it arrived. A public accessor returns them:

```cpp
// Latest good Ecowitt reading and when (millis) it was collected; false until the first one.
bool last_ecowitt(creek_core::EcowittReading &out, uint32_t &at_ms) const;
```

The accessor needs no new entities, adds nothing to HA (which has Ecowitt directly), and leaves the
store's records untouched.

### Units

**`creek_display` external component**
(`firmware/esp32s3_feather_gateway/components/creek_display/`). This is a header-only
`display_core.h` in namespace `creek_display`. It holds the pure logic and is host-tested in CI
like `store_core.h`. The component has an empty config schema. It exists so the header is
fetched by `external_components` and included in the build.
- Page and component id constants (the contract above).
- `MainInputs` struct (one plain value per data source, NaN or flags for unknown) and
  `render_main(const MainInputs&)` returning the list of fields: name, text, colour, and bar
  value. Time-dependent values (node age, Ecowitt age) are computed by the caller and passed in,
  so the function is pure.
- `SentCache`: remembers what was last sent per component and reports which attributes
  changed, so a 5 s refresh only sends what actually changed. Cleared whenever the main page is
  (re)entered or the display wakes, since the Nextion resets page components on entry and
  ignores updates while asleep.
- Settings: brightness steps (10–100 %, step 10), sleep options (30 s, 1 min, 2 min, 5 min,
  10 min, Never = 0) with labels, step functions clamped at the ends.
- `touch_action(page, component, press)`: maps a release on a settings/confirm button to an
  action enum. Everything else is `NONE`.
- Confirm messages for restart and display update.

**`display.yaml` package** (`firmware/esp32s3_feather_gateway/display.yaml`, the same pattern as
`ota-push.yaml`). It holds all the device glue:
- `uart` (TX 39, RX 38, 115200), `display: platform: nextion` (`id: disp`, `tft_url`,
  `start_up_page: 0`, `wake_up_page: 1`, `auto_wake_on_touch: true`), and `creek_display:`.
- `globals`:
  - restored: brightness (default 70 %) and sleep option (default 2 min).
  - not restored: current page, pending confirm action, node last-heard ms, and the
    `SentCache`.
- `esphome: on_boot` (late priority): registers the packet callback. Packages concatenate lists,
  so the base's priority-1100 I2C power-up step is kept.
- `on_setup`:
  - apply brightness and sleep timeout;
  - wait 3 s (the splash stays up);
  - go to `main`.
- `on_page`:
  - record the page;
  - entering `main`: clear the cache and refresh;
  - entering `settings`: write `tBri` and `tSlp`;
  - any page other than `confirm` clears the pending action.
- `on_wake`: clear the cache and refresh.
- `interval: 5s`: refresh `main` when it is the current page and the display is awake.
- `on_touch` (release only), dispatched through `touch_action`:
  - brightness ±: `set_backlight_brightness` and update `tBri`;
  - sleep ±: `set_touch_sleep_timeout` and update `tSlp`;
  - Update / Restart: set pending, go to `confirm`, write `tMsg`;
  - Confirm: write `tMsg` (`Restarting…` / `Updating display…`), then `App.safe_reboot()` or
    `upload_tft(${display_upload_baud})`.
- `button: Update Display Firmware` (entity_category config): the same upload from HA, so a new
  TFT can be pushed without going to the box.
- `substitutions`: `display_tx_pin`, `display_rx_pin`, `display_tft_url`,
  `display_upload_baud`.

**Wrappers.** `gateway.yaml` (local CLI, and what CI compiles), `creek-gateway-v2.yaml` (trial)
and `creek-gateway-v2.prod.yaml` add the `display.yaml` package and `creek_display` to
`external_components`. The HA copy at `/config/esphome/creek-gateway-v2.yaml` is updated by hand.
Diff it against the tracked copy first and back it up.

### TFT delivery

- `tft_url` is `https://raw.githubusercontent.com/ryanbuiltthat/rate-of-rise/main/firmware/nextion_display/creek_gateway_v2_display.tft`.
  - On ESP32, ESPHome's uploader uses the IDF HTTP client with server-cert verification off, so
    HTTPS to GitHub needs no certificate handling.
- The upload runs on the main task. While it runs, **the radio is not serviced**, so node packets
  in that window are lost from the store.
- **The gateway restarts after the transfer**: ESPHome reboots at upload end, whether it
  succeeded or failed.
- The confirm text says both.
- Upload baud starts at 115200. Bring-up tries 921600; if it is reliable, the substitution
  default changes, which cuts the radio pause by about 8×.
- **Forgotten-export guard (CI):** if the `.HMI` was committed more recently than the `.tft`, the
  build fails ("export the TFT"). The `.HMI` and `.tft` are committed together, at milestones
  rather than on every save, because each `.HMI` revision is several MB.

## Error handling

| Condition | Behaviour |
|---|---|
| Display unplugged or dead | `startup_override_ms` (8 s default) lets ESPHome finish setup; the gateway runs as before. Commands to a missing display are dropped. |
| HA API down | Tier and rise show `--`; `tHA` shows `HA offline`; everything local keeps updating |
| Node offline | Stage and depth show `--` (gateway already blanks them); `tNode` shows `node offline` in red |
| Ecowitt unreachable | Rain and temperature go to `--` 5 min after the last good poll |
| SD fault | `tSD` shows `SD fault` in red |
| Display asleep | No sends; full refresh on wake |
| Touch lands on the wrong id | Every touch is logged at INFO (`page`, `component`, `press`), so a mismatch shows the real id at once; fix the constant in `display_core.h` |
| TFT download fails | Gateway reboots (ESPHome behaviour); the display keeps its old TFT or shows Nextion's own update screen until a retry; failure detail is in the log |

## Testing

1. **Host tests** (`tests/test_display_core.cpp`, run in CI next to `test_store_core.cpp`):
   - formatting, including `--`, signs, decimals and `°`;
   - age text boundaries;
   - node battery bands at each threshold;
   - gauge clamping and colours;
   - tier labels/colours, with unknown → `--`;
   - Ecowitt freshness at 5 min, including `millis()` wrap;
   - `SentCache` deltas and clear;
   - brightness and sleep stepping at both ends;
   - `touch_action` for every contract id plus press-vs-release and unknown ids.
2. **CI compile:** `gateway.yaml` includes the display, so `esphome compile` covers the package,
   the component and the creek_store accessor. `display.yaml` is added to the YAML well-formedness
   list. The TFT guard runs in the same job.
3. **Editor simulator:**
   - every page renders against `bg.png`;
   - navigation works;
   - each Send-Component-ID button shows `65 <page> <id> 00 FF FF FF` on release with the ids in
     the contract.
4. **Bench (gateway on the bench, display wired):**
   - Splash at power-on, main ≥ 3 s after setup. All fields populate within one Ecowitt poll and
     one node packet.
   - Block the gateway from HA at OPNsense (as in the v2 trial): tier and rise show `--` and
     `HA offline`; local fields keep updating.
   - Node off or out of range: after the node timeout, `node offline` and stage `--`.
   - Brightness and sleep changes take effect, survive a gateway restart, and the display sleeps
     and wakes onto `main`.
   - Restart via confirm works; Cancel returns to settings without acting.
   - Update display from the screen and from HA: the TFT downloads, the display reboots onto
     splash, the gateway restarts. Record how long the transfer takes at 115200 and at 921600.
   - Current from the pack with the display awake and asleep. On USB with the display awake,
     the pack voltage still rises.
   - No Nextion "invalid instruction" warnings in the log over 30 min.

## Risks

- **UART0 sharing** with the RFM69 library's `Serial` (see Serial port). The check is in the bench list.
- **Radio pause during TFT upload** (see TFT delivery). It's a rare, manual operation, and the
  confirm text says so.
- **Battery runtime:** the display roughly halves it while awake. The sleep timeout is the
  mitigation, and its default (2 min) can change after the current measurements.
- **Id drift** if HMI components are deleted and re-created. Touch logging makes it visible;
  the constants live in one header.
- **Repo growth** from committing the `.HMI`. Commit at milestones; consider Git LFS if it
  becomes a problem.
