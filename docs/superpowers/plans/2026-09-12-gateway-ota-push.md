# Gateway-Driven OTA Firmware Push Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the ESP32 gateway push a new Moteino firmware image to the creek node over the RFM69 link, triggered by a Home Assistant button, so node updates no longer require retrieving it from the pole.

**Architecture:** The gateway's main loop keeps all ESPHome/WiFi/API duties and fires the first `FLX?` handshake the instant it hears the node's telemetry TX (the only moment the node is listening). It then hands the slow multi-packet transfer to a background FreeRTOS task. A mutex guards the shared radio; the main loop only ever *tries* to take it, never blocks on it.

**Tech Stack:** ESPHome external component (C++ header-only, arduino-esp32 framework), LowPowerLab RFM69 + RFM69_OTA, FreeRTOS, LittleFS, HTTPClient/WiFiClientSecure, PlatformIO (node side only).

**Spec:** [`docs/superpowers/specs/2026-09-12-gateway-ota-push-design.md`](../specs/2026-09-12-gateway-ota-push-design.md) (commit `d733c16`)

## Global Constraints

- **NEVER build or flash the gateway locally.** Not `esphome compile`, not `esphome run`, not the PlatformIO VS Code extension. ESPHome pins PlatformIO Core 6.1.19 while the Moteino node's extension uses 6.2.0 and both share `~/.platformio/penv`; each re-provisions it and breaks the other. This has already broken this machine four times. To get gateway code onto hardware: **commit, push, and let the user install from the Home Assistant ESPHome Device Builder UI**, which pulls `gateway.base.yaml` and `components/` from GitHub at `ref: main`, `refresh: 0s`.
- `esphome logs --device creek-gateway.local` **is** safe — native API only, never invokes PlatformIO. It is the primary verification tool for every gateway task.
- Local PlatformIO (`pio run`) is fine **for the Moteino node only**.
- Every gateway task's verification is hardware-in-the-loop and requires the user to press Install in the Builder UI. Tasks 3-6 may be batched into fewer flashes if the user prefers; they are kept separate because they are separate review units.
- Node radio settings must keep matching the gateway: `FREQUENCY` 915 MHz, network 100, node 1 / gateway 2, 16-char `ENCRYPT_KEY`.
- `RFM69_OTA.cpp` is already compiled into the gateway today (PlatformIO builds every `.cpp` in a library), so its ESP32 compatibility is already proven. Only two of its functions are used — see Task 6 for why.

---

### Task 1: Fix the node build so a successful OTA can actually reboot into the new image

**This is the highest-value task in the plan and must land first.** Without it every other piece can work perfectly and the update still silently never applies.

`resetUsingWatchdog()` in `RFM69_OTA.cpp` is:

```cpp
void resetUsingWatchdog(uint8_t DEBUG __attribute__((unused)))
{
#ifdef __AVR__
  ...
#elif defined(MOTEINO_M0)
  *((volatile uint32_t *)(HMCRAMC0_ADDR + HMCRAMC0_SIZE - 4)) = 0xF1A507AF;
  NVIC_SystemReset();
#endif
}
```

The SAMD21 is not `__AVR__`, and **PlatformIO does not define `MOTEINO_M0`**. The Arduino IDE's `boards.txt` does (`moteino_m0.build.extra_flags=... -DMOTEINO_M0 ...`) but PlatformIO uses `moteino_zero.json`, whose `extra_flags` is only `-DARDUINO_SAMD_ZERO -D__SAMD21G18A__ -DVERY_LOW_POWER`. So on this build the function compiles to an empty body.

Consequence: the node receives the whole image, writes it to SPI flash, prints `FLASH IMG TRANSMISSION SUCCESS!`, calls a no-op, and keeps running the old firmware. The `0xF1A507AF` magic that tells the Moteino M0 bootloader to flash from SPI flash is never written, so even a later manual reset would not apply it.

**Files:**
- Modify: `firmware/moteino_creek_node/platformio.ini`
- Modify: `firmware/moteino_creek_node/src/main.cpp` (the listen-window fix is already in the working tree, uncommitted)

**Interfaces:**
- Produces: a node binary whose `resetUsingWatchdog()` actually resets, which Task 6's transfer depends on for its success path.

- [ ] **Step 1: Confirm the macro is currently absent**

Run from `firmware/moteino_creek_node/`:

```bash
pio run -t envdump 2>/dev/null | grep -i "MOTEINO_M0" || echo "MOTEINO_M0 NOT DEFINED -- confirms the bug"
```

Expected: prints `MOTEINO_M0 NOT DEFINED -- confirms the bug`.

- [ ] **Step 2: Add the build flag**

In `firmware/moteino_creek_node/platformio.ini`, add a `build_flags` line to the `[env:moteino_creek_node]` section:

```ini
[env:moteino_creek_node]
platform = atmelsam
board = moteino_zero
framework = arduino
; PlatformIO's moteino_zero.json omits -DMOTEINO_M0, which the Arduino IDE's boards.txt
; sets. Without it RFM69_OTA's resetUsingWatchdog() compiles to an empty function: a
; completed wireless update writes the image to SPI flash and then never reboots into
; the bootloader, so the node silently keeps running the old firmware forever.
build_flags = -DMOTEINO_M0
lib_deps =
	lowpowerlab/SPIFlash@^101.1.3
	lowpowerlab/RFM69@^1.6.0
	lowpowerlab/LowPower_LowPowerLab@^2.2
	arduino-libraries/RTCZero @ ^1.6.0
```

- [ ] **Step 3: Verify the macro is now defined and the firmware still builds**

```bash
pio run
pio run -t envdump 2>/dev/null | grep -i "MOTEINO_M0"
```

Expected: build succeeds, and the grep now finds `MOTEINO_M0`.

- [ ] **Step 4: Verify the reset path actually compiles in**

```bash
grep -c "F1A507AF" .pio/build/moteino_creek_node/firmware.elf 2>/dev/null || \
  arm-none-eabi-objdump -s .pio/build/moteino_creek_node/firmware.elf | grep -i "a507af" | head -3
```

Expected: the magic constant appears in the built binary. If it does not, the macro is still not reaching the library — stop and investigate before continuing.

- [ ] **Step 5: Commit**

`BENCH_TEST` is currently enabled in the working tree. **Leave it enabled** — the spec's bench plan needs the node's 5 s cadence, and Task 8 turns it back off before redeployment. Call it out in the commit body so it is not mistaken for an accident.

```bash
git add firmware/moteino_creek_node/platformio.ini firmware/moteino_creek_node/src/main.cpp
git commit -m "$(cat <<'EOF'
Define MOTEINO_M0 so a completed OTA can reboot into the new image

RFM69_OTA's resetUsingWatchdog() has an __AVR__ branch and a MOTEINO_M0
branch and nothing else. PlatformIO's moteino_zero.json sets only
-DARDUINO_SAMD_ZERO -D__SAMD21G18A__ -DVERY_LOW_POWER, so neither matched and
the function compiled to an empty body -- the Arduino IDE's boards.txt is
what carries -DMOTEINO_M0, and PlatformIO never reads it.

The failure mode this would have produced is the confusing one: a wireless
update transfers every packet, validates, writes the image to SPI flash,
prints "FLASH IMG TRANSMISSION SUCCESS!", and then silently keeps running the
old firmware, because the 0xF1A507AF magic that tells the M0 bootloader to
flash from SPI flash is never written to RAM.

Also stops the post-TX OTA listen window from closing on the first packet
received. CheckForWirelessHEX() no-ops on anything that isn't its FLX?
handshake, so a stray packet would end the window early instead of leaving it
open for the real handshake.

BENCH_TEST stays enabled deliberately -- the OTA bench plan needs the 5 s
cadence. It goes back off before the node returns to the pole.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Publish the node image as a tracked build artifact

The gateway fetches a fixed URL, so the image needs a stable tracked path. `.pio` is gitignored, so the build output is copied out of it.

**Files:**
- Create: `firmware/moteino_creek_node/firmware.hex`
- Modify: `firmware/README.md` (Build & Flash section — one line pointing at the artifact step)

**Interfaces:**
- Produces: `https://raw.githubusercontent.com/ryanbuiltthat/rate-of-rise/main/firmware/moteino_creek_node/firmware.hex`, the exact URL Task 5 configures as `ota_hex_url`.

- [ ] **Step 1: Build and copy the artifact**

```bash
cd firmware/moteino_creek_node
pio run
arm-none-eabi-objcopy -O ihex .pio/build/moteino_creek_node/firmware.elf firmware.hex
```

**PlatformIO does not emit a `.hex` for this board.** The `atmelsam` / `moteino_zero` target
produces only `firmware.elf` and `firmware.bin`, so the hex must be *generated* with `objcopy`,
not copied. The toolchain's `objcopy` lives in
`~/.platformio/packages/toolchain-gccarmnoneeabi/bin/` and may need its full path if it is not
on `PATH`. This is the canonical command — Task 8 regenerates the artifact the same way.

- [ ] **Step 2: Verify the image only uses record types the OTA protocol supports**

The protocol ships only the *data* bytes of each record and relies on sequential ordering — `validateHEXData()` has a literal `TODO : CHECK for address continuity`. Record type `00` (data) is transferred, type `01` (EOF) is harmless because its data length is 0 and gets skipped. Any **type `04` extended linear address record would be transferred as if it were data and corrupt the image.**

```bash
cut -c8-9 firmware.hex | sort | uniq -c
```

Expected on this image: `00` (many), `01` (exactly one), and `03` (exactly one — a 4-byte start-address record second from last, carrying the entry point). Task 6's transfer loop filters on record type and skips `01`/`03`/`05`, so those are harmless.

If `02` or `04` appears, **stop**. Those re-base the address for every record after them, and this protocol writes strictly sequentially, so the image cannot be carried unmodified. Record the finding and raise it before continuing.

- [ ] **Step 3: Sanity-check the size**

```bash
wc -c firmware.hex
```

Expected: roughly 100-200 KB. Note the number — Task 5's fetch logs the received byte count and the two must match.

- [ ] **Step 4: Commit**

```bash
git add firmware/moteino_creek_node/firmware.hex firmware/README.md
git commit -m "$(cat <<'EOF'
Track the built node image so the gateway can fetch it by URL

The gateway's OTA push pulls a fixed raw.githubusercontent.com URL at
ref: main, mirroring how the Device Builder already pulls gateway.base.yaml
and components/. That needs a stable tracked path, and .pio is gitignored, so
the build output is copied to firmware/moteino_creek_node/firmware.hex and
committed alongside the source it was built from.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Guard the radio with a mutex (pure refactor, no OTA behaviour yet)

Ships and verifies the concurrency change on its own, before any new feature exists to confuse a regression with.

**Files:**
- Modify: `firmware/esp32_rfm69_gateway/components/rfm69_gateway/rfm69_gateway.h`

**Interfaces:**
- Produces: `SemaphoreHandle_t radio_mutex_`, and a `loop()` that holds it across all radio access. Task 6's background task takes the same mutex.

- [ ] **Step 1: Add the FreeRTOS includes**

Below the existing `#include <SPI.h>` in `rfm69_gateway.h`:

```cpp
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>
```

- [ ] **Step 2: Create the mutex before the radio is initialised**

At the very top of `setup()`, before `SPI.begin(...)`:

```cpp
    this->radio_mutex_ = xSemaphoreCreateMutex();
    if (this->radio_mutex_ == nullptr) {
      ESP_LOGE(TAG, "Could not create radio mutex");
      this->mark_failed();
      return;
    }
```

- [ ] **Step 3: Add the member**

With the other members at the bottom of the class:

```cpp
  SemaphoreHandle_t radio_mutex_{nullptr};
```

- [ ] **Step 4: Take it non-blocking in loop()**

Replace the existing `loop()` with:

```cpp
  void loop() override {
    // Non-blocking on purpose. During an OTA push the transfer task holds this mutex for the
    // whole transfer; a blocking take here would stall the main task for that entire time and
    // defeat the reason the transfer runs on its own task at all. Skipping a few iterations of
    // RSSI sampling costs nothing -- the node is blocked inside CheckForWirelessHEX() then and
    // is not transmitting telemetry anyway.
    if (xSemaphoreTake(this->radio_mutex_, 0) == pdTRUE) {
      this->sample_rssi_peak_();
      if (this->radio_.receiveDone()) {
        this->handle_packet_();
      }
      xSemaphoreGive(this->radio_mutex_);
    }
    if (millis() - this->last_packet_ms_ > this->node_timeout_ms_) {
      this->publish_node_status_(false);
    }
  }
```

- [ ] **Step 5: Push and have the user install**

```bash
git add firmware/esp32_rfm69_gateway/components/rfm69_gateway/rfm69_gateway.h
git commit -m "$(cat <<'EOF'
Guard the RFM69 with a mutex ahead of the OTA push path

The upcoming OTA transfer runs on its own FreeRTOS task so a multi-minute
push cannot block WiFi, the API and the watchdog, which means two tasks will
drive one radio. This adds the mutex and moves all of loop()'s radio access
under it, with no behaviour change yet.

loop() takes it non-blocking. A blocking take would stall the main task for
the whole transfer -- exactly what the separate task exists to avoid -- so a
contended iteration simply skips RSSI sampling and moves on.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
git push
```

Then ask the user to install from the HA ESPHome Device Builder UI.

- [ ] **Step 6: Verify no regression**

```bash
esphome logs --device creek-gateway.local
```

Expected, over at least 3 node reports: `RX [1] RSSI=-NN : {"node":1,...}` lines arrive on the normal cadence, RSSI values look like before, the `RSSI: noise floor ... last packet ...` line still appears, and HA still shows the gateway online with `binary_sensor.creek_node_status` connected. If telemetry stopped, the mutex is not being released on some path — fix before continuing.

---

### Task 4: OTA status plumbing (state struct, text sensor, config)

No radio work. Gets the observable surface in place first so later tasks have something to report through.

**Files:**
- Modify: `firmware/esp32_rfm69_gateway/components/rfm69_gateway/rfm69_gateway.h`
- Modify: `firmware/esp32_rfm69_gateway/components/rfm69_gateway/__init__.py`
- Modify: `firmware/esp32_rfm69_gateway/gateway.base.yaml`

**Interfaces:**
- Produces: `OtaState` enum, `set_ota_status_(OtaState, uint8_t, const char*)` (safe to call from any task), `publish_ota_status_()` (main task only), and the `ota_status` config key. Tasks 5 and 6 call `set_ota_status_()` exclusively; neither ever touches `publish_state()`.

- [ ] **Step 1: Add the state type and status members**

In `rfm69_gateway.h`, after the `RSSI_FLOOR_LOG_INTERVAL_MS` constant:

```cpp
enum class OtaState : uint8_t {
  IDLE,
  FETCHING,
  ARMED,
  HANDSHAKING,
  TRANSFERRING,
  DONE,
  FAILED,
};
```

Add to the includes (`<cstdio>` for `snprintf` — the existing file only has `<cmath>`, `<cstring>` and `<utility>`, and relying on it arriving transitively through an ESPHome header is how a build breaks later for an unrelated reason):

```cpp
#include <atomic>
#include <cstdio>

#include "esphome/components/text_sensor/text_sensor.h"
```

And with the other members:

```cpp
  text_sensor::TextSensor *ota_status_sensor_{nullptr};
  SemaphoreHandle_t status_mutex_{nullptr};
  OtaState ota_state_{OtaState::IDLE};
  uint8_t ota_percent_{0};
  char ota_reason_[48]{};
  OtaState published_state_{OtaState::FAILED};  // != IDLE, so the first publish always fires
  uint8_t published_percent_{255};
```

And the setter with the other setters:

```cpp
  void set_ota_status_sensor(text_sensor::TextSensor *s) { this->ota_status_sensor_ = s; }
```

- [ ] **Step 2: Create the status mutex**

In `setup()`, right after the `radio_mutex_` creation from Task 3:

```cpp
    this->status_mutex_ = xSemaphoreCreateMutex();
    if (this->status_mutex_ == nullptr) {
      ESP_LOGE(TAG, "Could not create status mutex");
      this->mark_failed();
      return;
    }
```

- [ ] **Step 3: Add the writer and the publisher**

As new protected methods:

```cpp
  // Callable from any task. Only ever touches the status fields under status_mutex_ -- never an
  // ESPHome object, because publishing from a non-main task is not safe.
  void set_ota_status_(OtaState state, uint8_t percent, const char *reason) {
    xSemaphoreTake(this->status_mutex_, portMAX_DELAY);
    this->ota_state_ = state;
    this->ota_percent_ = percent;
    if (reason != nullptr) {
      strncpy(this->ota_reason_, reason, sizeof(this->ota_reason_) - 1);
      this->ota_reason_[sizeof(this->ota_reason_) - 1] = '\0';
    }
    xSemaphoreGive(this->status_mutex_);
  }

  // Main task only. Publishes on change, so "transferring NN%" does not republish per record.
  void publish_ota_status_() {
    if (this->ota_status_sensor_ == nullptr || this->status_mutex_ == nullptr) {
      return;
    }
    OtaState state;
    uint8_t percent;
    char reason[sizeof(this->ota_reason_)];
    if (xSemaphoreTake(this->status_mutex_, 0) != pdTRUE) {
      return;
    }
    state = this->ota_state_;
    percent = this->ota_percent_;
    memcpy(reason, this->ota_reason_, sizeof(reason));
    xSemaphoreGive(this->status_mutex_);

    if (state == this->published_state_ && percent == this->published_percent_) {
      return;
    }
    this->published_state_ = state;
    this->published_percent_ = percent;

    char buf[80];
    switch (state) {
      case OtaState::IDLE: strcpy(buf, "idle"); break;
      case OtaState::FETCHING: strcpy(buf, "fetching"); break;
      case OtaState::ARMED: strcpy(buf, "armed, waiting for node"); break;
      case OtaState::HANDSHAKING: strcpy(buf, "handshaking"); break;
      case OtaState::TRANSFERRING: snprintf(buf, sizeof(buf), "transferring %u%%", percent); break;
      case OtaState::DONE: strcpy(buf, "done"); break;
      case OtaState::FAILED: snprintf(buf, sizeof(buf), "failed: %s", reason); break;
    }
    this->ota_status_sensor_->publish_state(buf);
  }
```

- [ ] **Step 4: Call the publisher from loop() and seed the initial state**

First line of `loop()`, before the mutex take:

```cpp
    this->publish_ota_status_();
```

Last line of `setup()`, after the existing `ESP_LOGI(TAG, "RFM69 ready ...")`:

```cpp
    this->set_ota_status_(OtaState::IDLE, 0, "");
```

- [ ] **Step 5: Wire the config**

In `__init__.py`, change the import and `AUTO_LOAD`:

```python
from esphome.components import binary_sensor, sensor, text_sensor

AUTO_LOAD = ["binary_sensor", "json", "sensor", "text_sensor"]
```

Add the key next to the other `CONF_` definitions:

```python
CONF_OTA_STATUS = "ota_status"
```

Add to `CONFIG_SCHEMA`, after `CONF_NODE_STATUS`:

```python
        cv.Optional(CONF_OTA_STATUS): text_sensor.text_sensor_schema(
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
```

Add to `to_code()`, after the `CONF_NODE_STATUS` block:

```python
    if conf := config.get(CONF_OTA_STATUS):
        cg.add(var.set_ota_status_sensor(await text_sensor.new_text_sensor(conf)))
```

- [ ] **Step 6: Add the entity to the YAML**

In `gateway.base.yaml`, inside the existing `rfm69_gateway:` block, alongside `node_status:`:

```yaml
  ota_status:
    name: Node OTA Status
```

- [ ] **Step 7: Check the Python parses**

This is a plain syntax check and does not invoke PlatformIO:

```bash
python -c "import ast,sys; ast.parse(open('firmware/esp32_rfm69_gateway/components/rfm69_gateway/__init__.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 8: Commit, push, install, verify**

```bash
git add firmware/esp32_rfm69_gateway/
git commit -m "$(cat <<'EOF'
Add the OTA status text sensor and its cross-task plumbing

The push itself runs on a background task, which must not call publish_state()
-- ESPHome's objects belong to the main task. So the task writes a small
mutex-protected status triple and the main loop polls it and publishes,
keeping every API interaction on one task.

Publishes only on change so "transferring NN%" does not republish per record.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
git push
```

Ask the user to install from the Builder UI. Expected afterwards: a new diagnostic entity `text_sensor.creek_gateway_node_ota_status` exists in Home Assistant reading `idle`, and telemetry is still flowing in `esphome logs`.

---

### Task 5: Fetch the image and arm the push

**Files:**
- Modify: `firmware/esp32_rfm69_gateway/components/rfm69_gateway/rfm69_gateway.h`
- Modify: `firmware/esp32_rfm69_gateway/components/rfm69_gateway/__init__.py`
- Modify: `firmware/esp32_rfm69_gateway/gateway.base.yaml`

**Interfaces:**
- Consumes: `set_ota_status_()` from Task 4.
- Produces: `void start_ota_push()` (public, called from the button lambda), `bool ota_armed_`, `uint32_t ota_deadline_ms_`, and `/ota.hex` in LittleFS. Task 6 consumes all three.

**Deviation from the spec, deliberate.** The spec says `AUTO_LOAD` gains `http_request`. This task uses arduino-esp32's `HTTPClient` directly instead. ESPHome's `http_request` component is built around YAML actions and its C++ surface has shifted between ESPHome releases, whereas this needs one GET streamed to a file from inside a C++ method. `HTTPClient` does that in five lines and the component already reaches for Arduino libraries directly (`SPI`, `RFM69`). `AUTO_LOAD` therefore gains only `text_sensor`. If the fetch later needs to move to `http_request` for the heap reasons in Step 9, revisit this.

- [ ] **Step 1: Add includes and constants**

```cpp
#include <HTTPClient.h>
#include <LittleFS.h>
#include <WiFiClientSecure.h>
```

With the other constants:

```cpp
// Where the fetched image lands in LittleFS.
static const char *const OTA_HEX_PATH = "/ota.hex";

// How long a press stays armed waiting for the node's next report. The node reports every 60 s,
// so this tolerates several missed cycles rather than making the user press the button again.
static const uint32_t OTA_ARM_TIMEOUT_MS = 600000;
```

- [ ] **Step 2: Add the members**

```cpp
  std::string ota_hex_url_;
  bool ota_armed_{false};
  uint32_t ota_deadline_ms_{0};
```

And the setter:

```cpp
  void set_ota_hex_url(const std::string &url) { this->ota_hex_url_ = url; }
```

- [ ] **Step 3: Write the fetch**

```cpp
  // Runs inline on the main task. It is a ~150 KB GET over WiFi -- a couple of seconds -- and
  // putting it on the transfer task would mean reporting download failures across a thread
  // boundary for no benefit. The slow part that needs its own task is the radio transfer.
  bool fetch_hex_() {
    if (!LittleFS.begin(true)) {
      ESP_LOGE(TAG, "LittleFS mount failed");
      this->set_ota_status_(OtaState::FAILED, 0, "littlefs mount failed");
      return false;
    }

    WiFiClientSecure client;
    client.setInsecure();  // no cert pinning; the payload is validated per-record by the node
    HTTPClient http;
    if (!http.begin(client, this->ota_hex_url_.c_str())) {
      this->set_ota_status_(OtaState::FAILED, 0, "bad url");
      return false;
    }
    http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
    const int code = http.GET();
    if (code != HTTP_CODE_OK) {
      ESP_LOGE(TAG, "OTA fetch failed: HTTP %d", code);
      char reason[48];
      snprintf(reason, sizeof(reason), "fetch error http %d", code);
      this->set_ota_status_(OtaState::FAILED, 0, reason);
      http.end();
      return false;
    }

    File f = LittleFS.open(OTA_HEX_PATH, "w");
    if (!f) {
      this->set_ota_status_(OtaState::FAILED, 0, "cannot open /ota.hex");
      http.end();
      return false;
    }
    // Trust writeToStream()'s return value, not f.size(). LittleFS files opened for writing get
    // a 4 KB full stdio buffer (VFSFileImpl, setvbuf _IOFBF), and VFSFileImpl::size() resolves to
    // a path-based stat() with no fflush() in the path -- so before f.close() it cannot see
    // whatever is still buffered. It under-reports by up to ~4095 bytes on every run, and on a
    // short response it can read 0 while the transfer genuinely succeeded.
    const int written = http.writeToStream(&f);
    f.close();
    http.end();

    if (written <= 0) {
      ESP_LOGE(TAG, "OTA fetch wrote nothing (writeToStream=%d)", written);
      this->set_ota_status_(OtaState::FAILED, 0, "empty image");
      return false;
    }
    ESP_LOGI(TAG, "OTA image fetched: %d bytes", written);
    return true;
  }
```

- [ ] **Step 4: Write the public trigger**

```cpp
  // Public: called from a button.template on_press lambda in gateway.base.yaml.
  void start_ota_push() {
    if (this->ota_armed_) {
      ESP_LOGW(TAG, "OTA push already armed, ignoring");
      return;
    }
    ESP_LOGI(TAG, "OTA push requested, fetching %s", this->ota_hex_url_.c_str());
    this->set_ota_status_(OtaState::FETCHING, 0, "");
    this->publish_ota_status_();  // make "fetching" visible before the blocking GET
    if (!this->fetch_hex_()) {
      return;
    }
    this->ota_armed_ = true;
    this->ota_deadline_ms_ = millis() + OTA_ARM_TIMEOUT_MS;
    this->set_ota_status_(OtaState::ARMED, 0, "");
    ESP_LOGI(TAG, "OTA armed; waiting for the node's next report");
  }
```

- [ ] **Step 5: Expire the arm window in loop()**

At the end of `loop()`:

```cpp
    if (this->ota_armed_ && (int32_t)(millis() - this->ota_deadline_ms_) > 0) {
      this->ota_armed_ = false;
      ESP_LOGW(TAG, "OTA arm window expired without a handshake");
      this->set_ota_status_(OtaState::FAILED, 0, "timeout, node did not respond");
    }
```

- [ ] **Step 6: Add the config key**

In `__init__.py`, next to the other `CONF_` names:

```python
CONF_OTA_HEX_URL = "ota_hex_url"
```

A validator that rejects plain HTTP, since `WiFiClientSecure` is what the fetch uses:

```python
def _https_url(value):
    value = cv.url(value)
    if not value.startswith("https://"):
        raise cv.Invalid("ota_hex_url must be an https:// URL")
    return value
```

In `CONFIG_SCHEMA`:

```python
        cv.Required(CONF_OTA_HEX_URL): _https_url,
```

In `to_code()`, after `set_node_timeout`:

```python
    cg.add(var.set_ota_hex_url(config[CONF_OTA_HEX_URL]))
```

And with the other `add_library` calls at the bottom, mirroring the existing `SPI` comment's reasoning:

```python
    # Same story as SPI above: these are selectively compiled out of the Arduino core, and the
    # OTA push needs them to pull the node image over HTTPS into LittleFS.
    cg.add_library("HTTPClient", None)
    cg.add_library("WiFiClientSecure", None)
    cg.add_library("FS", None)
    cg.add_library("LittleFS", None)
```

- [ ] **Step 7: Wire the URL and the button in YAML**

Add to `substitutions:` in `gateway.base.yaml`, next to the `rfm69_*` entries:

```yaml
  # Fixed image URL, pulled at ref: main exactly like the Device Builder pulls this file.
  # Produced by `pio run` in firmware/moteino_creek_node/ and committed -- see firmware/README.md.
  node_hex_url: "https://raw.githubusercontent.com/ryanbuiltthat/rate-of-rise/main/firmware/moteino_creek_node/firmware.hex"
```

In the `rfm69_gateway:` block:

```yaml
  ota_hex_url: ${node_hex_url}
```

And a new top-level block:

```yaml
button:
  - platform: template
    name: Push Node Firmware
    entity_category: config
    on_press:
      - lambda: id(creek_radio).start_ota_push();
```

This needs the component to have an id. If `rfm69_gateway:` has no `id:` yet, add `id: creek_radio` to it.

- [ ] **Step 8: Commit, push, install**

```bash
git add firmware/esp32_rfm69_gateway/
git commit -m "$(cat <<'EOF'
Fetch the node image to LittleFS and arm the push

Adds the button-triggered half: an https GET of the tracked firmware.hex into
LittleFS, then a 10-minute armed window waiting for the node's next report.
Nothing is transmitted yet -- the handshake and transfer land next.

The fetch runs inline rather than on the transfer task. It is a couple of
seconds over WiFi, and handling a download failure across a thread boundary
would buy nothing; the part that genuinely needs its own task is the radio
transfer, which is minutes not seconds.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
git push
```

- [ ] **Step 9: Verify the fetch**

After the user installs, with `esphome logs --device creek-gateway.local` running, press the **Push Node Firmware** button in HA.

Expected:
- `OTA push requested, fetching https://raw.githubusercontent.com/...`
- `OTA image fetched: NNNNNN bytes` where NNNNNN matches Task 2 Step 3's byte count
- status entity goes `fetching` → `armed, waiting for node`
- telemetry keeps arriving throughout
- after 10 minutes with no transfer implemented yet, status becomes `failed: timeout, node did not respond` — that is the correct outcome at this stage

**If the fetch fails with a memory/TLS error:** `WiFiClientSecure` needs ~40 KB of heap for the handshake, which is tight on an ESP32-C3 already running WiFi and the API. Log `ESP.getFreeHeap()` before the GET to confirm. The contingency is to serve the hex over plain HTTP from Home Assistant's `www/` folder (`http://homeassistant.local:8123/local/creek-node.hex`) and drop `WiFiClientSecure` — but that trades away the commit-and-push workflow, so raise it with the user rather than switching unilaterally.

---

### Task 6: Handshake and transfer

**Files:**
- Modify: `firmware/esp32_rfm69_gateway/components/rfm69_gateway/rfm69_gateway.h`

**Interfaces:**
- Consumes: `ota_armed_`, `radio_mutex_`, `set_ota_status_()`, `/ota.hex`.
- Produces: the completed feature. Nothing depends on it.

**Which library functions are used, and why so few.** Only `validateHEXData()` and `prepareSendBuffer()` from `RFM69_OTA.h` — both pure functions with no Serial and no format strings. Everything else is reimplemented here for two concrete reasons:

1. `sendHEXPacket()` parses its ACK with `sscanf((const char*)radio.DATA, "FLX:%u:OK", &tmp)` where `tmp` is `uint16_t`, guarded by `#if defined(__arm__)` to use `%hu` instead. The ESP32-C3 is RISC-V, so it takes the `%u` branch — and on a 32-bit non-AVR target `%u` writes 4 bytes into a 2-byte variable, corrupting adjacent stack. It is correct on AVR (16-bit `int`) and on ARM (the `%hu` branch), and wrong on exactly this target.
2. `HandleSerialHandshake()` and `sendHEXPacket()` busy-wait on `millis()` with no `yield()`. On ESP32 that starves the idle task the watchdog feeds, regardless of which task it runs in.

Reimplementing gives correct ACK parsing, `vTaskDelay(1)` yields, and progress reporting, for about 60 lines.

- [ ] **Step 1: Add the include and constants**

```cpp
#include <RFM69_OTA.h>
```

```cpp
// RFM69_OTA shifts the carrier by this much for the data phase, keeping the base channel clear
// of the transfer. The node's HandleWirelessHEXDataWrapper() does the same shift; if the gateway
// does not match it, the handshake succeeds on the base channel and then every data packet is
// transmitted where the node is not listening.
static const uint32_t OTA_SHIFT_HZ = 1000000;

// Bounds how long loop() can block on the handshake. Must stay well inside the node's 1500 ms
// post-TX listen window (OTA_LISTEN_MS in moteino_creek_node/src/main.cpp).
static const uint32_t OTA_HANDSHAKE_TIMEOUT_MS = 200;
static const uint32_t OTA_RECORD_TIMEOUT_MS = 3000;
static const uint8_t OTA_ACK_TIMEOUT_MS = 30;
static const uint32_t OTA_EOF_TIMEOUT_MS = 2000;
```

- [ ] **Step 2: Add the member and the task trampoline**

```cpp
  // The creek node's address, captured from SENDERID on the packet that triggered the push.
  // NOT node_id_ -- despite the name, that config key is the GATEWAY's own address (it is what
  // gets passed to RFM69::initialize(), and gateway.base.yaml sets rfm69_node_id: "2" with the
  // comment "gateway ID"). The creek node is address 1. Using node_id_ as the peer would compare
  // SENDERID against the gateway's own address (1 == 2, never true, so the handshake would never
  // fire) and would address every packet to the gateway itself. Capturing the peer from the
  // triggering packet is RFM69_OTA's own idiom -- see HandleSerialHEXData's
  // `uint16_t remoteID = radio.SENDERID;`.
  uint16_t ota_peer_id_{0};

  // Written by the transfer task, read by the main loop. std::atomic rather than volatile --
  // volatile orders nothing, and this flag is what keeps the two tasks off each other's radio.
  std::atomic<bool> ota_active_{false};
```

`ota_armed_` stays a plain `bool`: it is only ever touched from the main task.

As a file-scope function just above the class:

```cpp
class Rfm69Gateway;
void ota_task_trampoline(void *arg);
```

And after the class definition:

```cpp
inline void ota_task_trampoline(void *arg) {
  static_cast<Rfm69Gateway *>(arg)->run_ota_transfer();
  vTaskDelete(nullptr);
}
```

`run_ota_transfer()` must be **public** for the trampoline to reach it.

- [ ] **Step 3: Write the handshake and record senders**

```cpp
  // Sends FLX? (or FLX?EOF) until the node answers with an FLX? reply or the timeout expires.
  // Replaces RFM69_OTA's HandleSerialHandshake() to add the yield.
  bool send_flx_handshake_(bool is_eof, uint32_t timeout_ms) {
    const char *msg = is_eof ? "FLX?EOF" : "FLX?";
    const uint8_t msg_len = is_eof ? 7 : 4;
    const uint32_t start = millis();
    while (millis() - start < timeout_ms) {
      if (this->radio_.sendWithRetry(this->ota_peer_id_, msg, msg_len, 2, OTA_ACK_TIMEOUT_MS)) {
        if (this->radio_.DATALEN >= 6 &&
            memcmp((const void *) this->radio_.DATA, "FLX?", 4) == 0) {
          return true;
        }
      }
      vTaskDelay(1);
    }
    return false;
  }

  // Replaces RFM69_OTA's sendHEXPacket(): same protocol, but parses the ACK sequence by hand
  // instead of sscanf("%u") into a uint16_t, which corrupts the stack on this target.
  bool send_hex_record_(uint8_t *buf, uint8_t len, uint16_t seq) {
    const uint32_t start = millis();
    while (millis() - start < OTA_RECORD_TIMEOUT_MS) {
      if (this->radio_.sendWithRetry(this->ota_peer_id_, buf, len, 2, OTA_ACK_TIMEOUT_MS)) {
        const uint8_t ack_len = this->radio_.DATALEN;
        if (ack_len >= 8 && memcmp((const void *) this->radio_.DATA, "FLX:", 4) == 0 &&
            this->radio_.DATA[ack_len - 3] == ':' && this->radio_.DATA[ack_len - 2] == 'O' &&
            this->radio_.DATA[ack_len - 1] == 'K') {
          uint32_t acked = 0;
          bool valid = (ack_len - 3) > 4;
          for (uint8_t i = 4; i < ack_len - 3; i++) {
            const char c = (char) this->radio_.DATA[i];
            if (c < '0' || c > '9') {
              valid = false;
              break;
            }
            acked = acked * 10 + (uint32_t)(c - '0');
          }
          if (valid && acked == seq) {
            return true;
          }
        }
      }
      vTaskDelay(1);
    }
    ESP_LOGE(TAG, "No ACK for record %u", seq);
    return false;
  }
```

- [ ] **Step 4: Write the transfer**

```cpp
  bool transfer_image_() {
    File f = LittleFS.open(OTA_HEX_PATH, "r");
    if (!f) {
      this->set_ota_status_(OtaState::FAILED, 0, "cannot open image");
      return false;
    }
    const size_t total = f.size();

    this->radio_.setFrequency(this->radio_.getFrequency() + OTA_SHIFT_HZ);

    bool ok = true;
    uint16_t seq = 0;
    uint8_t last_percent = 0;
    char line[128];
    uint8_t send_buf[57];

    while (f.available()) {
      size_t n = f.readBytesUntil('\n', line, sizeof(line) - 1);
      line[n] = '\0';
      while (n > 0 && (line[n - 1] == '\r' || line[n - 1] == ' ')) {
        line[--n] = '\0';
      }
      if (n < 11 || line[0] != ':') {
        continue;  // blank or malformed line
      }

      // Strip the Intel HEX ':' -- the protocol frames records without it.
      char *record = line + 1;
      const uint8_t record_len = (uint8_t)(n - 1);

      // Filter on the record TYPE field (chars 6-7), not on data length alone. A real node
      // image ends with a type 03 start-address record carrying 4 bytes of entry point --
      // validateHEXData() happily returns 4 for it, so a length-only check transmits those
      // 4 bytes as if they were firmware. Types 02 and 04 are worse: they re-base the address
      // for everything after them, and this protocol writes strictly sequentially, so there is
      // no correct way to carry them -- abort rather than silently misplace the rest of the image.
      const uint8_t type = (uint8_t)((BYTEfromHEX(record[6], record[7])));
      if (type == 0x02 || type == 0x04) {
        ESP_LOGE(TAG, "Image uses extended addressing (record type %02X); cannot transfer", type);
        this->set_ota_status_(OtaState::FAILED, 0, "image uses extended addressing");
        ok = false;
        break;
      }
      if (type != 0x00) {
        continue;  // 01 EOF, 03/05 entry-point metadata: not firmware data
      }

      const uint8_t data_len = validateHEXData(record, record_len);
      if (data_len == 0 || data_len >= 253) {
        continue;
      }

      // +8 skips the record's own header (2 length + 4 address + 2 type chars): only the data
      // bytes go over the air, and the node reassembles them in seq order.
      const uint8_t buf_len = prepareSendBuffer(record + 8, send_buf, data_len, seq);
      if (!this->send_hex_record_(send_buf, buf_len, seq)) {
        ok = false;
        break;
      }
      seq++;

      const uint8_t percent = total > 0 ? (uint8_t)((f.position() * 100) / total) : 0;
      if (percent != last_percent) {
        last_percent = percent;
        this->set_ota_status_(OtaState::TRANSFERRING, percent, "");
      }
      vTaskDelay(1);
    }
    f.close();

    if (ok) {
      ok = this->send_flx_handshake_(true, OTA_EOF_TIMEOUT_MS);
      if (!ok) {
        ESP_LOGE(TAG, "Node never acknowledged EOF");
      }
    }

    this->radio_.setFrequency(this->radio_.getFrequency() - OTA_SHIFT_HZ);
    return ok;
  }
```

- [ ] **Step 5: Write the task body**

Public, next to `start_ota_push()`:

```cpp
  // Runs on its own task. Holds the radio for the whole transfer so the main loop keeps
  // servicing WiFi, the API and the watchdog.
  void run_ota_transfer() {
    xSemaphoreTake(this->radio_mutex_, portMAX_DELAY);
    const bool ok = this->transfer_image_();
    this->radio_.receiveDone();  // re-arm RX on the base channel
    xSemaphoreGive(this->radio_mutex_);

    if (ok) {
      ESP_LOGI(TAG, "OTA transfer complete; node should reboot into the new image");
      this->set_ota_status_(OtaState::DONE, 100, "");
    } else {
      this->set_ota_status_(OtaState::FAILED, 0, "transfer error");
    }
    this->ota_active_ = false;
  }
```

- [ ] **Step 6: Trigger the handoff from loop()**

New method:

```cpp
  // Called with radio_mutex_ held, immediately after a telemetry packet from the node -- the one
  // moment its listen window is open.
  void try_start_transfer_() {
    this->set_ota_status_(OtaState::HANDSHAKING, 0, "");
    if (!this->send_flx_handshake_(false, OTA_HANDSHAKE_TIMEOUT_MS)) {
      ESP_LOGW(TAG, "Node did not answer the handshake; staying armed for the next report");
      this->set_ota_status_(OtaState::ARMED, 0, "");
      return;
    }
    if (this->radio_.DATALEN >= 7 && this->radio_.DATA[4] == 'N') {
      // "FLX?NOK:NOFLASH" -- the node has no usable SPI flash chip. Retrying will not help.
      ESP_LOGE(TAG, "Node rejected the push: %.*s", this->radio_.DATALEN,
               (const char *) this->radio_.DATA);
      this->ota_armed_ = false;
      this->set_ota_status_(OtaState::FAILED, 0, "node rejected the image");
      return;
    }
    ESP_LOGI(TAG, "Handshake accepted; handing off to the transfer task");
    this->ota_armed_ = false;
    this->ota_active_ = true;
    this->set_ota_status_(OtaState::TRANSFERRING, 0, "");
    xTaskCreate(ota_task_trampoline, "rfm69_ota", 8192, this, 1, nullptr);
  }
```

Then in `loop()`, replace the radio block from Task 3 with:

```cpp
    // ota_active_ is checked before the mutex, not instead of it. Between loop() releasing the
    // mutex after the handshake and the task taking it, a loop iteration could otherwise win the
    // try-take and call receiveDone(), consuming the node's reply out from under the task.
    if (!this->ota_active_ && xSemaphoreTake(this->radio_mutex_, 0) == pdTRUE) {
      this->sample_rssi_peak_();
      if (this->radio_.receiveDone()) {
        this->handle_packet_();
        if (this->ota_armed_ && this->radio_.SENDERID != this->node_id_) {
          this->ota_peer_id_ = this->radio_.SENDERID;
          this->try_start_transfer_();
        }
      }
      xSemaphoreGive(this->radio_mutex_);
    }
```

- [ ] **Step 7: Commit and push**

```bash
git add firmware/esp32_rfm69_gateway/components/rfm69_gateway/rfm69_gateway.h
git commit -m "$(cat <<'EOF'
Transfer the node image over the radio on a background task

Completes the push: loop() answers the node's telemetry TX with FLX? inside
its 1500 ms listen window, then hands the multi-minute transfer to a task so
WiFi, the API and the watchdog keep running.

Uses only validateHEXData() and prepareSendBuffer() from RFM69_OTA and
reimplements the rest. sendHEXPacket() parses its ACK with sscanf("FLX:%u:OK")
into a uint16_t, switching to %hu only under #if defined(__arm__) -- the
ESP32-C3 is RISC-V, so it takes the %u branch and writes 4 bytes into a 2-byte
variable. Both it and HandleSerialHandshake() also busy-wait without yielding,
which starves the idle task the watchdog feeds.

The data phase shifts the carrier +1 MHz to match the node's
HandleWirelessHEXDataWrapper(). Without that the handshake would succeed and
every data packet would go out on a channel the node is not listening to.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
git push
```

- [ ] **Step 8: First end-to-end attempt**

Have the user install, then run `esphome logs --device creek-gateway.local` and press the button.

Expected log sequence: `OTA push requested` → `OTA image fetched: N bytes` → `Handshake accepted; handing off to the transfer task` → status climbing through `transferring NN%` → `OTA transfer complete; node should reboot into the new image`.

Watch specifically for a **gateway reboot mid-transfer** — that is the watchdog, and the fix is more `vTaskDelay(1)` yields or a lower task priority. Also confirm Home Assistant does **not** show the gateway as unavailable during the transfer; if it does, the main loop is being starved.

---

### Task 7: Document it

**Files:**
- Modify: `firmware/README.md`

- [ ] **Step 1: Add an "OTA Firmware Updates" section**

Place it after "Build & Flash". Following this README's convention, document what the thing is, how to use it, and what was learned the hard way. It must cover:

- The update procedure end to end: `pio run` → copy `firmware.hex` → commit → push → press **Push Node Firmware** → watch `text_sensor.creek_gateway_node_ota_status`.
- The full status list: `idle`, `fetching`, `armed, waiting for node`, `handshaking`, `transferring NN%`, `done`, `failed: <reason>`.
- Why arming waits: the node only listens for 1500 ms after each report, so a push waits for the next report (up to 60 s) and stays armed for 10 minutes.
- **The transient telemetry blackout is expected.** Water level, battery, RSSI and node status stop updating for the duration of a transfer. The node is blocked receiving and is not reporting either.
- **`-DMOTEINO_M0` is load-bearing.** Explain the empty-`resetUsingWatchdog()` failure (Task 1) — a transfer that reports complete success and silently changes nothing is the kind of symptom that costs a whole debugging session.
- **The +1 MHz `SHIFTCHANNEL`** — handshake on the base channel, data 1 MHz up, both sides must agree.
- **The `sscanf` bug in `sendHEXPacket()`** and why the component reimplements the host side rather than calling it.
- The image must contain only type `00` and `01` records; a type `04` means the protocol cannot carry it.

- [ ] **Step 2: Commit and push**

```bash
git add firmware/README.md
git commit -m "$(cat <<'EOF'
Document the OTA firmware push path

Covers the procedure, the status sensor's states, and the three findings that
are invisible from the code: -DMOTEINO_M0 gating whether a completed transfer
reboots at all, the +1 MHz channel shift both sides must agree on, and the
sscanf ACK bug that is correct on AVR and ARM and wrong on this RISC-V target.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
git push
```

---

### Task 8: Bench-test, then return the node to deployment configuration

**Files:**
- Modify: `firmware/moteino_creek_node/src/main.cpp`
- Modify: `firmware/moteino_creek_node/firmware.hex`

- [ ] **Step 1: Scenario 1 — happy path**

With `BENCH_TEST` still enabled, change something observable in `main.cpp` (e.g. the `Creek node ready` string), then rebuild and regenerate the artifact with the Task 2 Step 1 commands (`pio run` then `objcopy -O ihex` — PlatformIO emits no `.hex` itself), commit, push, press the button. Confirm the node reboots and the serial monitor shows the new string. **This is the test that proves the whole chain.**

- [ ] **Step 2: Scenario 2 — 404 URL**

Temporarily point `node_hex_url` at a nonexistent path, install, press the button. Expected: `failed: fetch error http 404`, nothing transmitted, telemetry undisturbed. Restore the URL afterwards.

- [ ] **Step 3: Scenario 3 — truncated image**

Commit a `firmware.hex` truncated to ~50% (`head -c` on a copy), press the button. Expected: the transfer starts and then fails, and critically **the node keeps running its existing firmware and resumes reporting**. Restore the full image afterwards.

- [ ] **Step 4: Scenario 4 — no regression**

Across all of the above: telemetry, RSSI and node status behave as before outside transfers, and Home Assistant stays connected to the gateway throughout a transfer.

- [ ] **Step 5: Disable BENCH_TEST and redeploy**

In `main.cpp`, comment it out again:

```cpp
//#define BENCH_TEST
```

Then rebuild and regenerate `firmware.hex` with the Task 2 Step 1 commands (`pio run` then `objcopy -O ihex`), and commit both. Flash the node over USB this time — the currently running node has bench firmware on it, and pushing a sleep-mode image over the air is best done once the path is proven.

```bash
git add firmware/moteino_creek_node/src/main.cpp firmware/moteino_creek_node/firmware.hex
git commit -m "$(cat <<'EOF'
Disable BENCH_TEST now the OTA push is verified end to end

Returns the node to the 60 s sleep cadence for pole deployment. The 5 s bench
cadence existed to make OTA attempts cheap to retry while the path was being
proven.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Confirm the deployed configuration still takes a push**

With the node back on its 60 s cadence, run one more push. The arm window now genuinely waits up to 60 s before the handshake lands — confirm `armed, waiting for node` persists and then transitions, proving the timing works at the real report interval and not just at the bench cadence.

---

## Amendments made during execution

Five defects in this plan were found by implementation and review. The code is authoritative;
these are recorded so the plan is not misleading if anyone reads it later.

1. **`node_id_` is the gateway's own address, not the node's** (Task 6). It is what gets passed
   to `RFM69::initialize()`, and `gateway.base.yaml` sets `rfm69_node_id: "2"  # gateway ID`
   while the creek node is address 1. The plan's `SENDERID == node_id_` was `1 == 2` — never
   true, so the handshake could never fire — and `sendWithRetry(node_id_, ...)` addressed the
   gateway itself. Fixed by capturing the peer from `SENDERID` on the triggering packet into
   `ota_peer_id_`, which is RFM69_OTA's own idiom. Would have presented as a silent 10-minute
   arm-window expiry.

2. **The re-entry guard did not cover the transfer window** (Task 6). `try_start_transfer_()`
   clears `ota_armed_` before spawning the task, so `start_ota_push()`'s `ota_armed_`-only guard
   was open for the whole transfer. A second button press re-entered `fetch_hex_()`, which opens
   `/ota.hex` `"w"` and truncated the file the transfer task was reading — the partial image
   then passed the EOF handshake and the node would commit and reboot into it. Guard is now
   `ota_armed_ || ota_active_`.

3. **Corrupt records were skipped instead of aborting** (Task 6). `validateHEXData()`'s failure
   codes (255/254/253/0) were lumped in with structural skips, so a damaged download produced a
   silently shifted, short image that still passed EOF. A validation failure on a type-00 record
   now aborts. Related: the record-type filter itself was added mid-execution, because the real
   image ends with a type-03 start-address record for which `validateHEXData()` returns 4 — a
   length-only check transmitted 4 bytes of entry-point address as firmware.

4. **`f.size()` was read before flush** (Task 5). LittleFS files opened for writing carry a 4 KB
   `_IOFBF` stdio buffer and `VFSFileImpl::size()` does a path-based `stat()` with no `fflush()`,
   so it under-reported every run and could spuriously fail a good fetch. Uses
   `writeToStream()`'s return value instead.

5. **PlatformIO emits no `.hex` for this board** (Task 2). The `atmelsam` / `moteino_zero` target
   produces only `.elf` and `.bin`; the artifact must be generated with
   `arm-none-eabi-objcopy -O ihex`, not copied.

Two characterizations in this plan were also too generous. `OTA_HANDSHAKE_TIMEOUT_MS` does not
bound `loop()` to 200 ms: `RF69_CSMA_LIMIT_MS` is 1000 ms and its `delay(1)` yield is compiled in
for ESP8266 only, so one `sendWithRetry` (three attempts) can spin ~3.1 s unyielding. The
watchdog margin is therefore ~1.6x against the 5 s TWDT, not comfortable. Neither is fixable
without forking the library, so both are bench watch items in Task 8 rather than code changes.
