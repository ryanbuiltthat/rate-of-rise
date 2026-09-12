# Gateway-driven OTA firmware push to the creek node

Date: 2026-09-12
Status: Approved (fetch strategy revised same-day — see "Correction" at the end)

## Problem

The Moteino creek node is mounted on a pole at the creek. Any firmware change currently
means physically retrieving it, flashing over USB, and remounting it.

[`moteino_creek_node/src/main.cpp`](../../../firmware/moteino_creek_node/src/main.cpp)
already has the receiving half of a fix: after each report TX it opens a brief
`OTA_LISTEN_MS` (1500 ms) window and calls LowPowerLab's `CheckForWirelessHEX()`, which
no-ops on any packet that isn't its `FLX?` handshake and, when it is, receives a whole
firmware image over the radio and reboots into the bootloader.

Nothing can send that handshake. The gateway component
([`rfm69_gateway.h`](../../../firmware/esp32_rfm69_gateway/components/rfm69_gateway/rfm69_gateway.h))
only ever receives and ACKs — `radio_.send()` is never called with a payload. The node is
listening for a push that no device in this system can make.

## Goals

- Push a new node image from Home Assistant without visiting the pole.
- Don't disturb the existing telemetry path. Normal reporting, RSSI and node-status
  behaviour must be unchanged outside of an actual push.
- Don't take the gateway off the network while pushing. A transfer runs for tens of
  seconds to minutes; Home Assistant must stay connected and the watchdog must stay fed
  throughout.
- Fail safe. A failed push must leave the node running its existing firmware.

## Design

### Timing — why this works at all

The node sleeps for essentially the whole 60 s report interval, so the gateway cannot
initiate a push whenever it likes. But the node's own TX marks the instant its listen
window opens, and the gateway receives that packet in real time. Answering it immediately
lands well inside the 1500 ms window.

Once the node accepts the handshake inside `CheckForWirelessHEX()`, the rest of the
transfer runs on the library's own internal per-packet timeouts — it blocks in that
function until the image is complete or a packet times out. The 1500 ms window therefore
only has to be wide enough for the *first* handshake packet, not the transfer.

That single constraint shapes the whole design: the first `FLX?` must go out with minimal
latency, so it is sent from the main loop the moment the telemetry packet arrives. The
long tail is handed to a background task.

### 1. Radio ownership: one mutex, two duties

The main loop and the OTA transfer both drive one `RFM69` object. A FreeRTOS mutex guards
it, with asymmetric locking rules:

- The **OTA task** takes the mutex blocking and holds it for the whole transfer.
- The **main loop** uses a *non-blocking* try-take every iteration. If the OTA task holds
  it, the loop skips `sample_rssi_peak_()` and `receiveDone()` for that iteration and
  continues immediately to ESPHome housekeeping.

The non-blocking take is load-bearing. A blocking take in `loop()` would stall the main
task for the full transfer and defeat the entire reason for the background task.

**The mutex alone is not sufficient at handoff.** Between the main loop releasing the
mutex (having just sent `FLX?`) and the OTA task taking it, a main-loop iteration can
win the try-take and call `receiveDone()` — consuming the node's `FLX?OK` handshake reply
and stealing it from the task that is about to ask for it. A separate `ota_active_` flag,
set by `loop()` at the moment it hands off and cleared by the task when the transfer ends,
makes the main loop skip its radio work entirely for the whole handoff-plus-transfer
span. The mutex protects concurrent register access; this flag protects ownership of the
received packet.

Consequence: telemetry, RSSI and node-status stop updating while a transfer is in flight.
That is correct rather than merely tolerable — the node is blocked inside
`CheckForWirelessHEX()` for that time and is not sending telemetry either.

### 2. `__init__.py` — configuration

New config on the `rfm69_gateway` component:

- `ota_hex_url` (required, string): validated as an `https://` URL. Fixed, pointing at
  `raw.githubusercontent.com/.../main/firmware/moteino_creek_node/firmware.hex`. Set via a
  substitution in `gateway.base.yaml` alongside the existing `rfm69_*` substitutions.
- `ota_status` (optional, `text_sensor` schema): diagnostic entity category, following the
  existing optional-entity pattern used by `node_status`.

`AUTO_LOAD` gains `text_sensor` and `http_request`. No new `cg.add_library()` — `LittleFS`
ships with arduino-esp32, and `RFM69_OTA` is part of the already-declared
`lowpowerlab/RFM69` package.

**[Corrected same-day — see "Correction" at the end.]** Both of those turned out to be wrong
once this was actually built: `lib_ldf_mode` is off in ESPHome's generated config, so nothing
`#include`d gets pulled in automatically regardless of what "ships with arduino-esp32" means,
and LittleFS specifically is excluded from this build's `CMakeLists` altogether.

### 3. The image in the repo

`.pio` is gitignored, so the build artifact is copied to a tracked path rather than the URL
pointing into a build directory:

```bash
pio run
cp .pio/build/moteino_creek_node/firmware.hex firmware/moteino_creek_node/firmware.hex
git commit && git push
```

This mirrors the pattern already used for the gateway itself — commit, push, and the live
target pulls `main` (`refresh: 0s`, see firmware/README.md "Gateway — build paths"). A
fixed URL means no entity to keep in sync and no version state to manage; whatever is on
`main` is what gets pushed.

### 4. `rfm69_gateway.h` — the push

New public `start_ota_push()`, wired from a `button.template`'s `on_press:` lambda in
`gateway.base.yaml`. Calling it:

1. **Fetch, inline.** HTTP GET `ota_hex_url` into LittleFS at `/ota.hex`. This blocks the
   main loop, deliberately — it is a ~150 KB file over WiFi, a couple of seconds, and
   putting it on the task would mean handling a download failure across a thread boundary
   for no benefit. Status → `fetching`.
   **[Corrected same-day — see "Correction" at the end: decodes to a RAM buffer instead of
   LittleFS, but stays inline on the main task for the same reason.]**
2. **Arm.** Set an armed flag with a 10-minute deadline. Status → `armed, waiting for
   node`. Staying armed across report cycles means one dropped packet costs 60 s, not
   another button press.

`loop()` gains two responsibilities:

- On a telemetry packet from the node while armed: send `FLX?` immediately (still holding
  the mutex it already took for the receive), then spawn the OTA task and hand off. Status
  → `handshaking`. Normal telemetry handling for that packet still runs — the packet is
  real data and there is no reason to drop it.
- On the arm deadline elapsing without a completed transfer: clear armed state, status →
  `failed: timeout, node did not respond`.

### 5. The OTA task

Spawned per-push and exits when the transfer ends. It reuses RFM69_OTA's host-side
primitives (`sendHEXPacket`, `waitForAck`, `prepareSendBuffer`) fed hex records read line
by line from `/ota.hex`. The library's `CheckForSerialHEX()` / `readSerialLine()` wrappers
are *not* used — those exist only to pull records off a host PC's serial port, and the
primitives underneath them take a buffer and a radio, not a stream.
**[Corrected same-day — see "Correction" at the end: the task instead walks a RAM buffer that
`fetch_hex_()` already decoded, and builds each radio packet directly rather than through
`prepareSendBuffer()`.]**

**Thread safety is kept trivial by keeping ESPHome objects on one task.** The OTA task
touches only the radio (under the mutex) and a small mutex-protected status struct
(`state` enum, `progress`, free text). It never calls `publish_state()`. The main loop
polls that struct each iteration and publishes changes to `ota_status`, so every
ESPHome/API interaction stays on the main task where the framework expects it.

The full set of `ota_status` states, in order:

```text
idle
fetching
armed, waiting for node
handshaking
transferring NN%
done
failed: <reason>
```

`transferring NN%` republishes per record, which at ~5 records/s would be chattier than
Home Assistant needs; publish only when the integer percentage changes.

**Watchdog mitigation.** RFM69 and RFM69_OTA wait with plain `millis()` busy-loops and never
`yield()`. On ESP32 a busy-wait starves the idle task regardless of which task it runs in,
and the idle task is what feeds the watchdog. Task priority is not the mitigation — the
transfer task runs at priority 1, the same priority as Arduino-ESP32's own `loopTask`, not
below it, so relative priority buys nothing here. The actual protection is explicit
`vTaskDelay(1)` calls at every retry, record and line boundary in `send_flx_handshake_()`,
`send_hex_record_()` and the hex-parsing loop, which force a real scheduler yield regardless
of priority. Because those yields are real but not continuous, a single record's failure can
still take up to ~6 s of *cumulative* wall time (a run of fast failed attempts followed by
one `sendWithRetry()` at its own worst case, ~3.1 s) without tripping the 5 s task watchdog —
the watchdog cares about the longest unbroken stretch without a yield, and that stretch is
bounded by the single `sendWithRetry()`, well under 5 s.

### 6. Failure modes

| Failure | Behaviour |
|---|---|
| HTTP error, timeout, empty body | `failed: fetch error`. Never arms; nothing is sent. |
| No handshake within 10 min | `failed: timeout, node did not respond`. Armed state cleared. |
| Transfer stalls or a record fails CRC | Node times out inside `CheckForWirelessHEX()` and resumes its existing firmware on the next cycle. Gateway reports `failed: transfer error`. |
| Gateway reboots mid-push | Same as above from the node's side — it times out and carries on. Armed state does not survive the reboot, so nothing retries unattended. |

The node's fallback is inherently safe: its bootloader only flashes from external SPI
flash once a complete image has been received and flagged, so a partial transfer leaves
the running firmware untouched.

## Testing

Bench-test with `BENCH_TEST` enabled on the node so its 5 s cadence makes a failed attempt
cost seconds rather than a minute:

1. **Happy path** — push a trivially-changed node image (e.g. a changed log string),
   confirm the node reboots into it and resumes reporting.
2. **404 URL** — confirm `failed: fetch error` and that nothing is transmitted.
3. **Truncated hex** — confirm the node falls back to its existing firmware and keeps
   reporting.
4. **No regression** — telemetry, RSSI and node-status behave identically before and after
   a push, and Home Assistant stays connected to the gateway for the whole transfer
   (this is the check that the background task and non-blocking try-take actually work).

Only after all four pass on the bench should a push be attempted against the pole-mounted
node.

## Out of scope

- Automatic version checking or unattended pushes. The trigger is a deliberate button
  press; there is no version state anywhere in this design.
- Pushing to more than one node. `node_id` is already a single value in the component
  config and the creek node is the only one.
- Rolling back to a previous image. Roll back by committing the older hex and pushing
  again.

## Correction (2026-09-12): fetch strategy changed from LittleFS to a RAM buffer

The design above was implemented as written and reviewed complete, then failed its first real
build in the Home Assistant Device Builder: `NetworkClient.h: No such file or directory`.
Chasing that down turned up two further problems, and together they make the LittleFS approach
in §2 ("`__init__.py` — configuration") and §4/§5 above **not implementable on this hardware**,
not merely a bug to fix:

1. **The immediate failure.** ESPHome's generated `platformio.ini` sets `lib_ldf_mode = off`,
   so PlatformIO does no `#include`-chasing at all — only libraries named in `lib_deps` are
   compiled. `HTTPClient` was declared, but its own `#include <NetworkClient.h>` was never
   resolved, because `Networking` (the library that provides it — note the directory is
   `Network/` but `library.properties` declares `name=Networking`) and `NetworkClientSecure`
   were never declared either. (`WiFiClientSecure`, which this design never explicitly
   declared as a library, was never a library to begin with — it's a shim header inside
   `NetworkClientSecure`.)
2. **LittleFS is excluded from this build.** `board_build.cmake_extra_args` carries
   `-DEXCLUDE_COMPONENTS=...;joltwallet__littlefs;...`. `LittleFS.h` wraps its whole class in
   `#ifdef CONFIG_LITTLEFS_PAGE_SIZE`, defined only when that component is present — so with it
   excluded, `LittleFS` is never even *declared*. §2's assumption that "`LittleFS` ships with
   arduino-esp32" is true of the framework in general and false of this build's configuration.
3. **It would not fit anyway.** `partitions.csv` gives the data partition just 60 KB, against a
   `firmware.hex` around 124 KB of ASCII. The 4 MB flash is already committed to two 1.8 MB OTA
   app slots plus 384 KB of NVS, so growing the partition means shrinking OTA headroom and a
   USB reflash — not a config change reachable from within this design.

**Replacement:** the `.hex` file's 124 KB is almost entirely ASCII transport encoding; the
payload the node actually receives is the ~45 KB of binary underneath it (it matches
`firmware.bin` exactly). `fetch_hex_()` now decodes each Intel HEX record as the HTTP response
streams in and holds only that ~45 KB in a heap buffer (`ota_image_`, capped at
`OTA_IMAGE_MAX_BYTES` = 96 KB), instead of writing the ASCII to a file for the transfer task to
re-parse later. `transfer_image_()` walks that buffer directly in fixed 16-byte chunks and
builds each radio packet with a `memcpy()` rather than `prepareSendBuffer()`, which existed only
to do the ASCII-to-binary conversion `fetch_hex_()` now does once, up front.

This does not change the property this design cares about most: the fetch (`fetch_hex_()`,
inline on the main task) still runs to completion, and is fully validated, before the radio
phase (`try_start_transfer_()` / `transfer_image_()`, on its own task) ever begins — a WiFi
stall still cannot abort a transfer already in flight over the radio. Nothing about §1 (radio
ownership), §3 (the image lives in the repo), or §6 (failure modes, apart from the specific
reason strings — see `firmware/README.md`'s `failed:` table for the current set) changed.
Everything else in this document describes the LittleFS approach as it was designed and
approved; it is left as written above rather than edited into agreement with what shipped. See
`firmware/README.md` ("Why the image lives in RAM, not a file") and
`firmware/esp32_rfm69_gateway/components/rfm69_gateway/rfm69_gateway.h` for the implementation.
