# Firmware — Moteino M0 Creek Node + ESP32 RFM69 Gateway

Firmware for the creek sensor node (Moteino M0 + RFM69HW, a PlatformIO Arduino
sketch) and the house-side gateway (Seeed XIAO ESP32-C3 + RFM69HW, an ESPHome
project). Replaces the original ESP32-C6 WiFi/ESPHome architecture with a
lower-power radio link — see the [project spec](../creek-flood-warning-spec.md)
§2 for the rationale.

The original ESPHome config is retained in [`../esphome/`](../esphome/) for
reference; it is no longer the active creek-node firmware path.

**Status:** The Moteino M0 + RFM69HW node was successfully deployed to the creek
pole on 2026-09-19 and is operating normally. After initial diagnosis revealing
MCU hangs due to undefended RFM69::setMode() blocking on unresponsive radio state
after sleep, the node firmware was hardened with explicit RFM69 RST pin pulses and
re-initialization on every wake, plus a fix for SPIFlash::command()'s indefinite
busy-wait on sleeping chips. Both issues are resolved; the node reports every 60 s
with stable battery voltage and good RSSI (−72 dBm), and 24+ hours of field data
show sensor distance readings responding appropriately to rainfall-driven water
level changes.

## Architecture

```
DFRobot SEN0676 (Modbus RTU)
        │
   Serial1 (UART)
        │
  Moteino M0 (SAMD21)
  + RFM69HW (915 MHz TX)
        │
   ~~~~ radio ~~~~  (500–1000 m, through trees)
        │
  XIAO ESP32-C3 + RFM69HW (915 MHz RX)
  + WiFi, ESPHome
        │
   native API (port 6053)  ──►  Home Assistant
        │
   sensor.creek_gateway_stage, sensor.creek_gateway_creek_depth,
   sensor.creek_gateway_sensor_distance, sensor.creek_gateway_creek_node_battery,
   sensor.creek_gateway_creek_node_rssi, binary_sensor.creek_gateway_creek_node_status
```

## Hardware

| Component | Role | Notes |
|---|---|---|
| Moteino M0 (LowPowerLab) | Creek node MCU, SAMD21 + RFM69HW socket | ~6 µA sleep, onboard radio |
| RFM69HW 915 MHz × 2 | Node TX + gateway RX | One soldered to Moteino, one wired to ESP32 |
| DFRobot SEN0676 | 80 GHz FMCW radar, ±5 mm, UART Modbus RTU | 3.5–5 V, ~30 mA active |
| Seeed XIAO ESP32-C3 | Gateway: RFM69 RX + WiFi + ESPHome API | Plugged in at house, always-on |
| CN3791 1S MPPT charger | Solar charge controller for creek node | ~0.5 mA idle vs. ~5 mA for linear |
| 18650 Li-ion (4P–6P) | Creek node battery pack | See open question #11 |
| 6–7 W solar panel | Creek node power | Sufficient at ~25 mA avg draw |

## Wiring — Creek Node (Moteino M0)

```
Moteino M0 Pin    Connection
─────────────────────────────────────
Serial1 RX (D0)   SEN0676 TX
Serial1 TX (D1)   SEN0676 RX
D4                 Boost/buck EN pin (switches 5 V to SEN0676)
A5                 VIN voltage (onboard 50% divider — no external wiring)
GND                SEN0676 GND, battery GND
SPI (onboard)      RFM69HW (Moteino socket — no external wiring)
```

## Wiring — Gateway (Seeed XIAO ESP32-C3)

All five signal pins are set in `gateway.base.yaml` (`rfm69_*_pin` substitutions) and bound
in the component via `SPI.begin()` before `RFM69::initialize()` — none of them come from the
board's SPI defaults.

```
XIAO Pad (GPIO)    RFM69HW Pin
───────────────────────────────
D10 (GPIO10)       MOSI
D2  (GPIO4)        MISO
D1  (GPIO3)        SCK
D3  (GPIO5)        NSS  (CS)
D4  (GPIO6)        DIO0 (interrupt)
D5  (GPIO7)        RST  (active HIGH; pulsed at boot, idles low — never tie to 3.3 V)
3.3 V              VCC
GND                GND
```

The RFM69's RST is **active high** — the SX1231H resets while the pin is driven high and runs
when it is low. Tying it to 3.3 V holds the radio in reset permanently. The gateway drives it
from D5 (GPIO7) via the component's `reset_pin` option and pulses it high for 200 us at boot,
then holds it low, so the radio is reset deterministically rather than depending on power-on
reset. Grounding RST instead is fine — just drop `reset_pin` from the config.

**The band must match the node.** `frequency: 915` in `gateway.base.yaml` has to equal
`FREQUENCY` in `moteino_creek_node/src/main.cpp` (915 MHz is the North American ISM band; 868
is European). A mismatch is completely silent: both radios initialize normally and simply never
hear each other. Also check the band a *replacement* module is actually built for — RFM69HW
ships in 433/868/915 variants that look identical.

**Do not move MISO/SCK/DIO0 to the XIAO's default SPI pins** — D9 (GPIO9), D8 (GPIO8) and
D0 (GPIO2) are the ESP32-C3's three boot-strapping pins, and GPIO9 is BOOT. Wired that way,
the radio's MISO line holds GPIO9 low through reset and the chip comes up in ROM download
mode instead of running the firmware:

```
rst:0x15 (USB_UART_CHIP_RESET), boot:0x0 (USB_BOOT)
wait usb download
```

No WiFi, no logs, no fallback AP — the app never starts. This was the original wiring and it
cost a long debugging session; unplugging the radio was what proved it. The current pinout is
the fix.

## Libraries

The creek node's are pinned in `moteino_creek_node/platformio.ini`; the gateway's are
declared by the ESPHome component (`components/rfm69_gateway/__init__.py`) and fetched
automatically. Nothing here needs the Arduino Library Manager.

| Library | Author | Used by |
|---|---|---|
| RFM69 | LowPowerLab | Both (node via `lib_deps`, gateway via `cg.add_library`) |
| LowPower | LowPowerLab (`github.com/LowPowerLab/LowPower`) | Creek node (SAMD21 standby) |
| RTCZero | Arduino | Creek node (RTC alarm for timed wakeup from standby) |
| ArduinoJson | Benoît Blanchon | Gateway, via ESPHome's `json` component (payload decode) |

## Configuration

Node and gateway must share the same radio settings:

| Setting | Value | Notes |
|---|---|---|
| Frequency | 915 MHz | Set via `RF69_915MHZ` (North American ISM band) |
| Network ID | 100 | Arbitrary, must match |
| Node ID | 1 (creek), 2 (gateway) | |
| Encryption key | 16-char string | Node: `ENCRYPT_KEY` in `src/main.cpp`. Gateway: `rfm69_encrypt_key` secret. Changing it requires reflashing both. |

Gateway credentials (WiFi, API encryption key, OTA password, RFM69 key) come from
`!secret` — see "Secrets" below. Note that earlier revisions of the gateway config
carried them inline and were pushed to this public repo, so any value used before
2026-09-07 should be considered compromised and remains readable in git history.

## Build & Flash

### Creek node (Moteino M0)

Built with [PlatformIO](https://platformio.org/) (`board = moteino_zero`,
`platform = atmelsam`, `framework = arduino`) — see
`moteino_creek_node/platformio.ini` for the pinned `lib_deps`.

1. Open `firmware/moteino_creek_node/` as a PlatformIO project (PlatformIO
   extension in VS Code, or `pio run` from that directory).
2. Set `ENCRYPT_KEY` in `src/main.cpp` to your chosen key.
3. Connect via USB and run Upload (`pio run -t upload`).
4. **`firmware.hex` regenerates itself — you don't need to run `objcopy` by hand.**
   `.github/workflows/firmware-hex.yml` builds `src/**` and `platformio.ini` on every
   push to `main` that touches them, runs the same `arm-none-eabi-objcopy -O ihex
   .pio/build/moteino_creek_node/firmware.elf firmware.hex` conversion (the atmelsam
   platform doesn't emit a `.hex` on its own), and **publishes the result as an asset on
   the moving `node-firmware-latest` release** — which is what the gateway downloads. It
   then opens a PR to bring the committed copy into step. A pull request only gets the
   build-and-verify half. Trigger it manually from the Actions tab (`workflow_dispatch`)
   if you need a rebuild without a source change.

   **The release is the delivery path; the committed `firmware.hex` is a copy.** Until
   2026-09-21 it was the other way round — the workflow pushed the hex to `main` and the
   gateway fetched it from `raw.githubusercontent.com`. Requiring pull requests on `main`
   broke that push, and it would have broken it invisibly: a stale hex still serves 200 and
   still flashes, so the node would have gone on taking an old image with nothing anywhere
   reporting a problem. Publishing to a release writes the artifact without touching a
   protected branch, so there is nothing left to go stale.

   **The sync PR will sit without checks.** A PR opened with `GITHUB_TOKEN` does not trigger
   workflows, so `test` never starts and the required status check stays pending. That is
   harmless — the release was refreshed before the PR was opened, so the node is already
   current. Close and reopen the PR to run checks under your own account, or merge it
   alongside other work.

**If the board stops being recognized by USB:** the SAMD21's native USB
drops off the bus while asleep (`LowPower.standby()`), so once the sketch
is running it's normal for the board to disappear from the OS a few
seconds after each reset. The sketch delays 8 s at boot before its first
sleep specifically to leave a window for uploads. If you still miss that
window (or the board was flashed before this delay existed), double-tap
the reset button to force it into the bootloader, which bypasses the
sketch entirely and lets you reflash.

### Gateway — build paths

The gateway is an ESPHome project with two entry points that share one configuration:

```
gateway.base.yaml          the whole device: esp32/wifi/api/ota/rfm69_gateway/diagnostics
  ├── gateway.yaml                         (this repo)  component from ./components/
  └── /config/esphome/creek-gateway.yaml   (HA add-on)  base + component pulled from git
```

Both wrappers are thin on purpose. The only thing that differs is the `external_components`
source, so there is nothing to keep in sync — edit `gateway.base.yaml` and both paths follow.

**Local CLI** (fastest loop; component edits take effect without pushing):

1. `cd firmware/esp32_rfm69_gateway`
2. `cp secrets.yaml.example secrets.yaml` and fill it in (gitignored) — see "Secrets".
3. `esphome run gateway.yaml` (or `esphome compile` to just build). `esphome` drives
   PlatformIO under the hood and generates its own `.esphome/build/`; there is no
   platformio.ini to hand-maintain here the way there is for the Moteino node.
4. First flash needs USB; later updates go out over OTA.

Note `esphome upload` does **not** recompile — it pushes the last built binary. Any config
change needs `esphome compile` first, or just use `esphome run`.

**Home Assistant Device Builder** (web UI installs, logs, and no local toolchain):

`/config/esphome/creek-gateway.yaml` on the HA host pulls both `gateway.base.yaml` and
`components/rfm69_gateway/` straight from `github.com/ryanbuiltthat/rate-of-rise` at `ref: main`, so
the builder compiles the same firmware while the config stays version-controlled here:

```yaml
packages:
  gateway:
    url: https://github.com/ryanbuiltthat/rate-of-rise
    ref: main
    files: [firmware/esp32_rfm69_gateway/gateway.base.yaml]
    refresh: 0s

external_components:
  - source:
      type: git
      url: https://github.com/ryanbuiltthat/rate-of-rise
      ref: main
      path: firmware/esp32_rfm69_gateway/components
    components: [rfm69_gateway]
    refresh: 0s
```

Two things to know:

- **Changes must be pushed before the builder can see them.** Both sources use `refresh: 0s`,
  so every validate/compile re-fetches from GitHub and a push is visible immediately. This is
  deliberate: with a caching `refresh`, a clone made before a push keeps being served and the
  builder reports the confusing `gateway.base.yaml does not exist in repository`. The add-on's
  clone cache lives inside its container, so it cannot be cleared from the config share. The
  cost of `0s` is that builds need network access to GitHub.
- **Don't "adopt" the gateway** if it also appears under the builder's *discovered* devices.
  Adopting writes a second, separate YAML into `/config/esphome/` that would immediately
  diverge from this repo, leaving two configs fighting over one device.

The two are not alternatives in the way the old Arduino-IDE/ESPHome split was — they build
byte-identical firmware. Use whichever is convenient.

### Secrets

`/config/esphome/secrets.yaml` on the Home Assistant host is authoritative. `wifi_ssid`,
`wifi_password` and `ap_password` are shared with the other ESPHome devices there; the
gateway adds three of its own:

| Key | Notes |
|---|---|
| `creek_gateway_api_key` | `openssl rand -base64 32`. Changing it means re-adding the device in HA. |
| `creek_gateway_ota_password` | |
| `rfm69_encrypt_key` | 16 chars, must equal the node's `ENCRYPT_KEY`. Changing it needs both devices reflashed. |

The local CLI build reads its own gitignored `secrets.yaml` in `esp32_rfm69_gateway/` —
a mirror of those six keys, since ESPHome resolves `!secret` next to the config file.

### Why an external component

There is no ESPHome platform for a point-to-point encrypted radio, so the RFM69 receive and
JSON decode live in a local external component at `esp32_rfm69_gateway/components/rfm69_gateway/`
wrapping the same LowPowerLab `RFM69` library the node uses. It publishes native ESPHome
sensors rather than MQTT JSON — `distance_mm: null` (the node's Modbus-failure case) becomes
`NAN`, so HA shows *unknown* instead of a plausible-looking zero. Diagnostics (WiFi signal,
uptime, IP) use ESPHome's stock platforms.

Two build workarounds are load-bearing and documented inline where they live: RFM69's
PlatformIO manifest wrongly declares AVR-only support, which `esp32: toolchain: platformio`
plus `lib_compat_mode: soft` works around; and `CONFIG_APP_REPRODUCIBLE_BUILD: "n"` keeps the
compiler command line under the Windows `CreateProcess` limit.

## OTA Firmware Updates

The gateway can push a new node firmware image over the radio link itself — the Moteino
never has to come off the pole. `components/rfm69_gateway/` drives the gateway side of
LowPowerLab's `RFM69_OTA` protocol; `moteino_creek_node/src/main.cpp` answers it on the node
side through `CheckForWirelessHEX()`. The whole gateway-side path lives in
`rfm69_gateway.h`: `start_ota_push()` (arms it) → `try_start_transfer_()` (handshakes) →
`run_ota_transfer()` / `transfer_image_()` (streams the image, on its own FreeRTOS task).

### Procedure

1. **Build the node image.** Push your `src/main.cpp` (or `platformio.ini`) change to `main`
   and let `.github/workflows/firmware-hex.yml` build it and regenerate `firmware.hex` — see
   "Build & Flash → Creek node (Moteino M0)" step 4. PlatformIO's `atmelsam` platform does not
   emit a `.hex` on its own, so that conversion step is not optional, but it no longer needs to
   be run by hand.
2. **Confirm the release refreshed.** The gateway fetches the asset from the moving
   `node-firmware-latest` release — `node_hex_url` in `gateway.base.yaml` — so what matters is
   that the `firmware-hex` run on `main` finished, not that any branch contains the hex.
   Check the run, or that the release's asset timestamp moved. A push to a branch or a PR
   does nothing: the publish step is gated on the event not being a `pull_request`.

   Do **not** repoint this at `/releases/latest/download/`. That resolves to the newest
   release in the whole repository, and `release.yml` cuts a `v<version>` release on every
   add-on version bump. Those have no `firmware.hex` attached, so the next add-on release
   would turn the OTA URL into a 404 — a break with no connection to anything anyone changed
   in the firmware. The fixed tag only moves when the node firmware moves.
3. **Press "Push Node Firmware" in Home Assistant.** It's a `button.template` under the
   gateway device's Configuration section (`entity_category: config`), not the main entity
   card.

   **"Push Node Diagnostic Firmware"** sits beside it and installs the armed radar-rail
   diagnostic build (open question #17) instead. Both images come from the same CI run on the
   same commit and live on the same release, so this is a choice between two artifacts that
   already exist — there is nothing to build locally to use it.

   There is deliberately no "disarm" button. The node-side window is one-shot: it latches
   closed once it has collected a usable sample, so a diagnostic image left installed costs
   one window, once. It also rejects a window that landed in daylight (the pack rises rather
   than falls) and retries the next day, so the press does not have to be timed. Press "Push
   Node Firmware" whenever convenient to return to the stock image.
4. **Watch the OTA status sensor** — `ota_status:` in `gateway.base.yaml`, named
   "Node OTA Status". Following the same unprefixed naming already in effect for
   `sensor.creek_gateway_stage` and its neighbors above, that's `text_sensor.node_ota_status`.

### Status values

`publish_ota_status_()` only republishes on a state or percent change, so the sensor doesn't
spam — expect one line per real transition:

| Status | Meaning |
|---|---|
| `idle` | Boot default only — set once in `setup()` and never again. A terminal `done` or `failed: <reason>` persists indefinitely after a push, until the next push or a reboot. |
| `fetching` | Main task is downloading `firmware.hex` from GitHub and decoding it into a RAM buffer as it arrives (see "Why the image lives in RAM, not a file" below). |
| `armed, waiting for node` | Image fetched; waiting for the node's next telemetry packet to open its listen window. |
| `handshaking` | A telemetry packet just arrived; the gateway is exchanging `FLX?` with the node inside that packet's reply window. |
| `transferring NN%` | Handshake accepted; the transfer task is streaming Intel HEX data records. |
| `done` | Node ACKed EOF and (assuming `-DMOTEINO_M0` is set — see below) has rebooted into the bootloader to flash the image. |
| `failed: <reason>` | See below. |

`failed:` reasons, verbatim from the code — this is the complete set; `fetch_hex_()`,
`transfer_image_()` and their callers have no other way to fail:

| Reason | Cause |
|---|---|
| `bad url` | `ota_hex_url` didn't parse — a config problem, not a network one. |
| `fetch error http <code>` | The GET to the release asset didn't return 200. A `404` most often means the `node-firmware-latest` release or its `firmware.hex` asset is missing — check the `firmware-hex` workflow actually ran on `main` and published. |
| `no memory for image` | `malloc()` for the decoded-image buffer failed, or the response decoded to more bytes than the buffer was sized for. Check the logged free-heap figure. |
| `empty image` | The GET returned 200 but decoded to 0 bytes, or the connection stalled before the server's declared `Content-Length` was reached. |
| `timeout, node did not respond` | 10 minutes armed and the node's telemetry never triggered a handshake. |
| `node rejected the image` | Node replied `FLX?NOK:NOFLASH` — no SPI flash chip found on the node. |
| `no memory for transfer task` | `xTaskCreate()` for the 8 KB transfer task stack failed. |
| `image uses extended addressing` | Hex record type `02`/`04` seen — see "Intel HEX record types" below. |
| `corrupt or oversized image record` | `validateHEXData()` rejected a data record, or it decoded to more than 47 bytes. |
| `record <seq> not acknowledged` | A hex record's ACK never arrived within `OTA_RECORD_TIMEOUT_MS`. |
| `node never acknowledged EOF` | Every record transferred, but the gateway never got an ACK back for the final EOF request. Ambiguous — see below. |

`image uses extended addressing` and `corrupt or oversized image record` are detected in
`fetch_hex_()` now, not `transfer_image_()` — see "Why the image lives in RAM, not a file"
below — but the strings, and what they mean, are unchanged.

`record <seq> not acknowledged` carries the decimal record number, e.g. `record 1847 not
acknowledged` — which record it died on is itself the diagnosis. A failure a handful of
records in reads as a one-off collision worth just pressing the button again; a failure at a
high `seq`, most of the way through, points at something wearing down over the length of the
transfer (a marginal link, a node running low on power) rather than a fluke.

`node never acknowledged EOF` is the one status that can lie, and it's worth understanding why
before trusting it. On the node side, `HandleWirelessHEXData()`'s EOF branch writes the final
size metadata, calls `HandleHandshakeACK(radio, flash, false)` — which sends `"FLX?OK"` via
`radio.sendACK()` — and returns `true`; `CheckForWirelessHEX()` then calls
`resetUsingWatchdog()` immediately. `RFM69::sendACK()` is one-shot: it transmits that single
packet and does not wait to learn whether it arrived, and nothing on the node retries it or
waits to confirm receipt before the reset happens. So this status covers two outcomes the
gateway cannot tell apart: either the EOF request never reached the node, and it's still
running the old firmware exactly as the status implies — or the EOF request *did* reach the
node, which already committed the metadata and rebooted into the new image, and only that
one ACK packet back was lost, in which case the update actually succeeded and the status is
wrong. This is the `-DMOTEINO_M0` trap in reverse: there, every signal says success and
nothing changed; here, the status says failure and the update may already have landed. The
radio side can't resolve it — check the node itself (its serial output at boot, or any
observable change in behavior) rather than trusting the sensor. Re-pushing is harmless either
way: if the node already has the new image, a second push just repeats the same transfer.

### Why arming waits

The node spends nearly the whole 60 s report cycle asleep (`LowPower.standby()`, `main.cpp`)
and listens for only `OTA_LISTEN_MS` = 1500 ms right after it transmits its own telemetry.
That is the entire window in which a push can start — there is no other time the node's
receiver is even running. So the gateway doesn't wait passively for that window; it answers
the *same* telemetry packet that opens it. In `loop()`, the instant `receiveDone()` sees a
packet while a push is armed, it captures the sender as `ota_peer_id_` and calls
`try_start_transfer_()` immediately, inline in that same loop iteration — the handshake
attempt starts within the same loop pass that received the telemetry, not merely "soon
after." How long it then takes to actually land is not fixed. `OTA_HANDSHAKE_TIMEOUT_MS`
(200 ms) only bounds the handshake to a single `sendWithRetry()` call, and per that
constant's own comment in `rfm69_gateway.h`, the typical case is a few milliseconds — the
channel is idle and the node answers on the first try, comfortably inside the 1500 ms window.
The worst case is not: `RFM69::send()` spins in CSMA for up to `RF69_CSMA_LIMIT_MS` (1000 ms),
but only while the channel is genuinely busy; its `delay(1)` yield during that spin is
compiled in for `ESP8266` only, so it never yields on this target; and `sendWithRetry`'s three
attempts (`retries=2`) can each pay that cost — worst case around 3.1 s, well past the node's
window. That worst case hasn't been exercised on the bench against a busy channel; it's an
open item, not a settled guarantee. Because any single report cycle catching the window isn't
guaranteed either way — a lost packet, a busy channel — `OTA_ARM_TIMEOUT_MS` keeps the
button's arm state alive for 10 minutes (about ten report cycles) rather than requiring a
second press.

### The transient telemetry blackout is expected

Water level, battery, RSSI and node status all stop updating for the duration of a transfer.
That's correct, not merely tolerated. On the node side, once `CheckForWirelessHEX()` accepts
the handshake it blocks inside `HandleWirelessHEXDataWrapper()` / `HandleWirelessHEXData()`
for the whole transfer — the node isn't running its sensor-read/report loop, so there is
nothing to report. On the gateway side, `loop()` deliberately skips its own radio work while
a transfer is active: `if (!this->ota_active_ && xSemaphoreTake(this->radio_mutex_, 0) ==
pdTRUE)` guards every `receiveDone()` call and RSSI poll, and the transfer task holds
`radio_mutex_` for the entire transfer. The check is non-blocking on purpose — a blocking
take would stall the main task, and with it WiFi/API/watchdog servicing, for however long the
transfer runs. `binary_sensor.creek_node_status` won't flip to *disconnected* either unless a
transfer runs past the 5-minute node timeout, which a normal transfer shouldn't.

### `-DMOTEINO_M0` is load-bearing

`platformio.ini` sets `build_flags = -DMOTEINO_M0` specifically because PlatformIO's
`moteino_zero.json` doesn't define it — only the Arduino IDE's `boards.txt` does, and
PlatformIO never reads that file. `RFM69_OTA`'s `resetUsingWatchdog()` has exactly two
branches, `__AVR__` and `MOTEINO_M0`; drop the flag and on this SAMD21 board neither matches,
so the function compiles to an empty body. The failure mode that produces is the worst kind,
because every visible signal says success: `CheckForWirelessHEX()` writes the whole image to
SPI flash, prints `FLASH IMG TRANSMISSION SUCCESS!` over serial, the gateway sees the EOF ACK
and reports `done` — and then the node just keeps running the old firmware forever, because
the one thing `resetUsingWatchdog()` was supposed to do never happened: write the magic value
`0xF1A507AF` to the top of SRAM and call `NVIC_SystemReset()` so LowPowerLab's Samba
MultiBoot bootloader flashes from SPI on the next boot. There is no error, no failed status,
no log line to grep for — the only symptom is "I pushed an update and nothing changed,"
noticed only by observing that the running firmware's behavior didn't change. If that ever
happens, check `platformio.ini`'s `build_flags` before anything else.

### The +1 MHz channel shift

The handshake (`FLX?` / `FLX?OK`) happens on the base channel — `rfm69_frequency: "915"` in
`gateway.base.yaml`, matching `FREQUENCY` in the node's `main.cpp`. The data phase runs
1 MHz higher: `RFM69_OTA`'s `SHIFTCHANNEL` constant, applied by the node's
`HandleWirelessHEXDataWrapper()` and mirrored on the gateway by `transfer_image_()`
(`radio_.setFrequency(radio_.getFrequency() + OTA_SHIFT_HZ)`); both sides shift, transfer, and
shift back. If the two ever disagree, the handshake still succeeds — it runs entirely on the
unshifted base channel — and every data record after it goes out on a frequency nobody is
listening on, so the transfer just hangs until `OTA_RECORD_TIMEOUT_MS` (3 s) times out on the
first record and the whole thing fails silently from the node's point of view. `rfm69_gateway.h`
pins this down at compile time: `static_assert(OTA_SHIFT_HZ == SHIFTCHANNEL, ...)`. If that
assert ever fires, someone changed one side of the shift without the other.

### Why the component reimplements RFM69_OTA's host side

`transfer_image_()` and friends don't call `RFM69_OTA`'s own `HandleSerialHEXData()` /
`sendHEXPacket()` — the component borrows only two of its helpers, `validateHEXData()` and
`BYTEfromHEX()` (both now called from `fetch_hex_()`, while it decodes the image — see "Why the
image lives in RAM, not a file" above), and hand-rolls the handshake (`send_flx_handshake_()`)
and the per-record send-and-ACK (`send_hex_record_()`) itself. `prepareSendBuffer()` is no
longer used at all: it exists only to convert ASCII hex pairs into bytes, and `fetch_hex_()`
already does that once, up front, so `transfer_image_()` just `memcpy()`s straight out of the
decoded buffer. The reason for hand-rolling the send/ACK path is `sendHEXPacket()`'s ACK parser:

```cpp
uint16_t tmp = 0;
#if defined(__arm__)
  sscanf((const char*)radio.DATA, "FLX:%hu:OK", &tmp);   // ARM: uint16_t is short
#else
  sscanf((const char*)radio.DATA, "FLX:%u:OK", &tmp);    // AVR: uint16_t is unsigned int
#endif
```

That's correct on AVR (`uint16_t` really is `unsigned int` there) and correct on ARM (the
`%hu` branch matches). It's wrong on the XIAO ESP32-C3, which is RISC-V: `__arm__` is
undefined, so it takes the `%u` branch, and `sscanf` writes a 4-byte `unsigned int` through a
pointer to a 2-byte `uint16_t` — a stack overwrite on every packet ACK, on exactly this
target and no other. `send_hex_record_()` replaces it with a hand-written digit-by-digit parse
of the `FLX:<seq>:OK` suffix instead. Also worth knowing if anything ever points back at the
library directly here: its wait loops (`sendHEXPacket()`, `HandleSerialHandshake()`)
busy-wait with no yield at all, which is why `send_flx_handshake_()` and `send_hex_record_()`
each add a `vTaskDelay(1)` per retry — without it the transfer task's spin can starve the
watchdog.

### Intel HEX record types

`fetch_hex_()` reads the HTTP response line by line and switches on each record's type field
(characters 6–7) while it decodes — see "Why the image lives in RAM, not a file" below for why
this now happens during the fetch rather than the transfer. Only type `00` (data) is kept; `01`
(EOF) and `03`/`05` (start address / metadata) are skipped with a plain `continue`. That's a
type check rather than a length check on purpose: a real node image's last record is a type
`03` start-address record, and `validateHEXData()` happily returns `4` for it — a length-only
filter would keep the entry-point address as if it were four more bytes of firmware. Types `02`
and `04` (Intel HEX's extended segment/linear address records, used to re-base beyond 64 KB)
abort the fetch outright with `failed: image uses extended addressing`: this protocol writes
flash strictly sequentially, one `seq` at a time, with no field for "here's a new base address,"
so there is no correct way to carry them. In practice that caps a transferable image at 64 KB of
address space.

### Why the image lives in RAM, not a file

The first real build attempt (Home Assistant Device Builder, Sept 2026) failed before this ever
got to run, on `NetworkClient.h: No such file or directory` — and fixing that turned up two
worse problems with the design this replaced. The original plan downloaded `firmware.hex` to
LittleFS and streamed it back off disk during the transfer. Neither half of that is possible on
this hardware:

- **LittleFS is excluded from this build.** The generated `platformio.ini`'s
  `board_build.cmake_extra_args` carries `-DEXCLUDE_COMPONENTS=...;joltwallet__littlefs;...`.
  `LittleFS.h` wraps its entire class in `#ifdef CONFIG_LITTLEFS_PAGE_SIZE`, which is only
  defined when that component is present — so with it excluded, `LittleFS` is never even
  *declared*, and the calls that used it could not compile.
- **It would not fit anyway.** `partitions.csv` gives the data partition (`spiffs, data, spiffs,
  , 0xF000`) just **60 KB**, against a `firmware.hex` around **124 KB** of ASCII. The 4 MB flash
  is already committed to two 1.8 MB OTA app slots plus 384 KB of NVS, so growing the partition
  means shrinking OTA headroom and a USB reflash of the gateway — not a config tweak.

The fix rests on one fact: the `.hex` file's 124 KB is almost entirely ASCII transport encoding.
The payload the node actually receives is the roughly **45 KB of binary** underneath it — it
matches `firmware.bin` exactly. So `fetch_hex_()` decodes each Intel HEX record as it streams
in from the HTTP response, instead of saving the ASCII to a file for `transfer_image_()` to
re-parse later, and holds only the ~45 KB result in a heap buffer (`ota_image_`) sized from the
response's `Content-Length` and capped at `OTA_IMAGE_MAX_BYTES` (96 KB). `transfer_image_()`
then just walks that buffer in fixed 16-byte chunks — no file, no filesystem, no parsing left to
do at transfer time.

This preserves the property the design most cares about: the network phase (`fetch_hex_()`,
inline on the main task) still completes — and is validated — before the radio phase
(`try_start_transfer_()` / `transfer_image_()`, on its own task) ever starts, so a WiFi stall
can't abort a transfer already in flight over the radio.

`fetch_hex_()` logs `OTA image fetched and decoded: N bytes` on success, and N is the decoded
binary count — around 44,984 for the current image — not the ~124 KB of hex text the HTTP GET
actually transferred. Do not "fix" this by comparing it against `firmware.hex`'s size on disk;
they were never the same quantity, and the difference is now the whole point: decoding is what
keeps this feature inside the available heap on a target with no room for the file it was
originally designed around.

### A push blocks a second push

`start_ota_push()` refuses to start a new push while `ota_armed_` or `ota_active_` is true —
the button press is just logged and ignored (`OTA push already in progress, ignoring`). This
isn't only debouncing. `try_start_transfer_()` clears `ota_armed_` *before* spawning the
transfer task, so for the whole multi-minute transfer `ota_armed_` is false while `ota_active_`
is true — checking `ota_armed_` alone would let a second press slip through into `fetch_hex_()`
while the transfer task is mid-flight. `fetch_hex_()` mallocs a fresh buffer and only assigns it
to `ota_image_`/`ota_image_len_` once fully decoded, which would reassign those two fields out
from under the transfer task — a data race, since nothing locks them, on top of leaking whatever
buffer the transfer task was still reading from. `ota_active_` covers exactly the window
`ota_armed_` does not, which is why the guard checks both.

## Bench-Test Procedure

1. **Radio link test:** Flash both devices. Watch the creek node's serial monitor and the
   gateway's ESPHome logs (`esphome logs gateway.yaml`, or the builder's Logs button).
   The creek node should TX every 60 s and the gateway should log the decoded payload + RSSI.
   Confirm packets arrive and RSSI is reasonable (better than −90 dBm at bench distance).
   **Done 2026-09-07 (bench) and 2026-09-19 (pole field test) — node and gateway hold a 
   stable 60 s cadence at the pole with RSSI −72 dBm, including overnight and through 
   rainfall events. 24+ hour continuous operation confirmed.**

   **RSSI needs care on this radio.** The stock library's value is not a link-quality
   measure. `RFM69::interruptHandler()` samples RSSI at `PAYLOADREADY` — after reception has
   finished and the receiver is back to hearing an empty channel — and since the init table
   runs DAGC continuously in RX (`REG_TESTDAGC = RF_DAGC_IMPROVED_LOWBETA0`), the register has
   already tracked back down to the noise floor by then. The symptom is a value that barely
   moves: −95 to −103 dBm with the radios touching on a bench *and* with two floors between
   them, a span over which real path loss differs by 30–50 dB.

   `components/rfm69_gateway/` works around it by polling `readRSSI()` from `loop()` and
   publishing the strongest reading from the last 100 ms. A ~50-byte report is on the air for
   about 8 ms at 55555 bps, so the poll catches the packet itself. Because DAGC is continuous,
   no `RSSI_START` trigger is needed — it is a plain register read that never writes to the
   radio and never touches the FIFO.

   Two approaches that do **not** work, both tried on hardware:

   - Capturing from `interruptHook()`. It runs mid-way through the FIFO burst read with CS
     still asserted, and on the SX1231 only the first byte after CS goes low selects a
     register — every byte after that just pops the FIFO. A register access there silently
     eats payload bytes and corrupts every packet (reverted in `d90d817`).
   - Overriding the virtual `setMode()` to sample on the way back into RX. The radio is still
     in standby at that point, but the value has already decayed, so it just reports a slightly
     different noise floor (−103 instead of −98).

   If the poll ever proves too coarse, the real fix is to remap DIO0 from `PayloadReady` to
   `SyncAddress` so the interrupt lands mid-packet — but `interruptHandler()` is not virtual
   and the whole receive path assumes `PayloadReady`, so that means forking the library.
   Consider whether packet delivery rate answers the question first; it measures link
   reliability directly and cannot break reception.

2. **Modbus test:** With the SEN0676 powered and wired to Serial1, the creek
   node should print `TX: {"node":1,"distance_mm":XXXX,...}` with a plausible
   distance value. A `null` distance means the Modbus exchange failed —
   check wiring and baud rate (the datasheet default is 115200).

3. **Entity test:** With the gateway on WiFi and added to Home Assistant, confirm
   `sensor.creek_gateway_sensor_distance` tracks the node's `distance_mm`, that battery and RSSI update
   with each packet, and that `binary_sensor.creek_node_status` goes *disconnected* about
   5 minutes after the node stops transmitting (five missed reports).

4. **Battery/sleep test:** Disconnect USB from the creek node, run on
   battery. Measure current draw: should be ~25 mA average (30 mA during
   sensor read + TX, ~6 µA during sleep).

5. **Range test (bag test at pole):** Wire up the Moteino + SEN0676 in a
   weatherproof bag, mount temporarily at the pole location, and log RSSI
   from the gateway over 24 hours. Target: RSSI better than −80 dBm
   sustained, with < 1% packet loss.

## Troubleshooting — gateway radio

`rfm69_gateway is marked FAILED: unspecified` means `RFM69::initialize()` returned false.
That function has four `return false` paths and ESPHome collapses them all into that one
message, so the component logs `REG_VERSION`, `OPMODE` and `IRQFLAGS1` alongside it. Read
them before touching wiring:

| Log line | Meaning |
|---|---|
| `REG_VERSION=0x00` or `0xFF` | The radio never answered. Power, SPI wiring, or RST held high. |
| `REG_VERSION=0x24`, `MODEREADY=set` | SPI and the radio are fine — suspect the IRQ pin. |
| `REG_VERSION=0x24`, `OPMODE=0x00`, `MODEREADY=clear` | **Dead radio.** See below. |

The third row is worth recognising, because it looks like a config problem and is not. A
`REG_VERSION` of `0x24` proves the SPI bus works in both directions, and config writes will
appear to succeed — bitrate, PA level and sync words all read back correctly, because those
are plain register RAM clocked by the ESP32's own SCK. But `OPMODE` stuck at `0x00` is Sleep,
and writes to it are silently discarded, so the radio can never start its oscillator. Once
diagnosed on this hardware (Sept 2026) it was confirmed by enabling CLKOUT on DIO5: neither
the crystal (FXOSC/32) nor the independent RC oscillator produced a single edge. Supply
measured 3.2 V at the module and grounding RST changed the register dump not at all. The
module was simply dead and had to be replaced.

The tell that separates a dead radio from a wiring fault: writes from the *same* config loop
land for `BITRATE`/`PALEVEL`/`SYNCVALUE` but not for `OPMODE`/`FRFMSB`. Registers in the
clock domain refuse writes while plain register RAM accepts them. No firmware change can fix
that.

**A disconnected MISO can fake a successful init.** `RFM69::initialize()` proves the radio is
present by writing `0xAA` then `0x55` to `SYNCVALUE1` and reading each back. An open MISO line
floats and capacitively echoes the byte just clocked out on MOSI, so both readbacks "succeed"
and the component reports `RFM69 ready` with no radio attached at all. The gateway therefore
checks `REG_VERSION == 0x24` after init — a constant only the chip can produce — and fails
loudly if it does not match. If you see that error, check MISO continuity before anything else.
Note that MISO is legitimately tri-stated while CS is high, so probing the idle pin level
proves nothing; trust `REG_VERSION`.

Note that RFM69 RESET is **active HIGH** — see the gateway wiring section. Tying RST to 3.3 V
holds the radio in reset, and because the SPI register file stays accessible in that state it
produces a confusingly similar signature.

## Home Assistant entities

The gateway publishes over the ESPHome native API (port 6053); HA discovers it via mDNS —
which is only advertised when `api:` is enabled. No MQTT broker is in the data path, and no
hand-written HA YAML is needed.

| Entity | Source field | Unit | Category |
|---|---|---|---|
| `sensor.creek_gateway_stage` | derived: installation height − distance | ft | primary |
| `sensor.creek_gateway_creek_depth` | same measurement, readable units | in | primary |
| `sensor.creek_gateway_sensor_distance` | `distance_mm`: radar face to water surface (`NAN` on sensor failure). **Distance declining = water rising** | mm | diagnostic |
| `sensor.creek_gateway_creek_node_battery` | `battery_mv` | mV | diagnostic |
| `sensor.creek_gateway_creek_node_rssi` | gateway-measured, per packet | dBm | diagnostic |
| `binary_sensor.creek_gateway_creek_node_status` | packet liveness, 5 min timeout | connectivity | diagnostic |
| `number.creek_gateway_installation_height` | datum, surveyed 1105 mm | mm | config |
| WiFi Signal / Uptime / IP Address | gateway itself | | diagnostic |

An earlier design republished the node's payload byte-for-byte onto fixed MQTT topics
(`creek/node_1/data`, `creek/node_1/rssi`, `creek/gateway/status`). Nothing ever consumed
them: HA had no creek stage entity, and the modeling pipeline reported `"stage_stale": true`
while falling back to a USGS gauge proxy. The MQTT path and its HA sensor block were dropped
rather than maintained alongside the entities above. The missing stage entity itself was
fixed separately — the gateway now derives depth, and `stage_entity` in the add-on points at
`sensor.creek_gateway_stage`.

## Integration with the modeling add-on

The modeling add-on (`rate_of_rise/`) reads creek stage from an HA entity. The gateway
publishes distance in millimetres from the sensor to the water surface, not stage — the
`distance_mm` → `stage_ft` conversion against the mount-height datum still has to happen
somewhere, either as an HA template sensor or inside the add-on. Point the add-on's
`stage_entity` at whichever entity ends up carrying the final stage value.
