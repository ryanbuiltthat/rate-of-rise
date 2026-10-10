# Gateway v2 Nextion Display — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Date: 2026-10-10
Status: Tasks 1-6 implemented 2026-10-10; Tasks 7-8 (HMI build, bench) pending

**Goal:** Drive a Nextion NX3224T024 from gateway v2:
- a startup image at power-on;
- a main page with stage, rain, temperature, batteries, link status and the add-on's tier and
  rate of rise;
- a settings page for restart, brightness, sleep timeout and display-firmware (TFT) updates.

**Architecture:**
- **Pure logic** lives in a header-only `creek_display` external component (`display_core.h`),
  host-tested like `store_core.h`: formatting, colours, the HMI's names and ids, settings steps
  and touch dispatch.
- **Device glue** is one ESPHome package, `display.yaml`: UART, `nextion`, HA imports, globals,
  scripts and touch handling. All three v2 wrappers add it.
- **creek_store** gains one accessor for the latest good Ecowitt poll, so rain works with HA
  down.
- **The HMI** is built by hand in the Nextion Editor against the contract in
  `firmware/nextion_display/README.md`.

**Tech Stack:**
- ESPHome 2026.7.3 (`nextion`, `uart`, `homeassistant`, `globals`, `script`) on the Arduino
  framework, ESP32-S3.
- C++17, header-only.
- Host tests with g++ in CI; locally on Windows, `python -m ziglang c++` from a venv.
- Python 3.11 + PyYAML for the contract test.
- Nextion Editor for the HMI.

**Spec:** [docs/superpowers/specs/2026-10-10-gateway-v2-nextion-display-design.md](../specs/2026-10-10-gateway-v2-nextion-display-design.md)

## Global Constraints

- **Display:** NX3224T024, landscape 320×240.
  - UART TX GPIO39 → Nextion RX, RX GPIO38 ← Nextion TX, 115200 baud (`baud=115200`, `bkcmd=0`
    in `program.s`).
- **Pages:** `splash`=0 (pic 0 `startup.png`), `main`=1, `settings`=2, `confirm`=3 (pic 1
  `bg.png`).
- **Touch ids:** settings `bBriDn`=4, `bBriUp`=6, `bSlpDn`=8, `bSlpUp`=10, `bUpdate`=11,
  `bRestart`=12; confirm `bYes`=3.
- **Colours (RGB565):**
  - white 65535, grey 33808 (missing), green 2016, yellow 65504, orange 64512, red 63488,
    magenta 63519.
  - Missing values read `--` in grey.
- **Tier labels:** `All-clear`, `Advisory`, `Watch`, `Warning`, `Emergency` for 0–4 (the add-on's
  `TIER_LABELS`). HA entity `sensor.rate_of_rise_creek_alert_tier` is numeric 0–4.
- **Rate of rise:** `sensor.rate_of_rise_creek_rate_of_rise`, in/min, shown signed with 2
  decimals.
- **Node battery bands:** ≥ 4050 / 3850 / 3700 / 3550 mV → bar 100 / 75 / 50 / 25, else 0. Bar
  green ≥ 75, yellow 50, red ≤ 25.
- **Gateway battery:** MAX17043 %, clamped 0–100. Bar green ≥ 50, yellow ≥ 20, red below.
- **Rain freshness:** rain and temperature blank 5 min (300000 ms) after the last good Ecowitt poll.
- **Settings:**
  - Brightness 10–100 % in steps of 10, default 70.
  - Sleep options 30 s, 1 min, 2 min, 5 min, 10 min, Never (0); default index 2 (2 min).
- **Splash:** shown ≥ 3 s after the Nextion handshake, then `main`.
- **Refresh:** every 5 s, only on `main` while awake, sending only changed attributes.
- **Isolation:** v1 is untouched. No add-on or HA-package changes. New entity ids in
  `gateway.base.yaml` are additive; entity names do not change.

## Review Focus

1. **Restored settings out of range.** A brightness or sleep index restored from flash that is
   not a valid option must not produce a dark screen or "Never". Brightness clamps; a bad
   sleep index falls back to the default (2 min), not an end of the list. Pinned in Task 3.
2. **Values that round to zero print as zero.** A rate of rise of −0.001 in/min must read
   `+0.00 in/min`, not `-0.00`. Pinned in Task 1.
3. **`millis()` wrap.** After ~49.7 days of uptime, rain must still read fresh right after a poll.
   Pinned in Task 1.
4. **HA tier that isn't 0–4** (`unavailable` → NaN, a future tier 5, a fraction) shows `--`, not
   a wrong label or an out-of-bounds read. Pinned in Task 1.
5. **Confirm pressed with nothing pending.** For example, the confirm page is still up after a
   gateway restart, and the global is 0 or garbage: nothing must happen. Pinned in Task 3
   (`to_pending`).

---

## File Structure

| File | Responsibility |
|---|---|
| Create `firmware/esp32s3_feather_gateway/components/creek_display/__init__.py` | Empty-config component so `external_components` fetches the header |
| Create `firmware/esp32s3_feather_gateway/components/creek_display/display_core.h` | Pure display logic (Tasks 1–3) |
| Create `firmware/esp32s3_feather_gateway/tests/test_display_core.cpp` | Host tests for `display_core.h` |
| Modify `firmware/esp32s3_feather_gateway/components/creek_store/creek_store.h` | `last_ecowitt()` accessor |
| Modify `firmware/esp32s3_feather_gateway/gateway.base.yaml` | ids on four existing entities; display pins in the WIRING comment |
| Create `firmware/esp32s3_feather_gateway/display.yaml` | Device glue |
| Modify `firmware/esp32s3_feather_gateway/gateway.yaml`, `creek-gateway-v2.yaml`, `creek-gateway-v2.prod.yaml` | Add the display package and component |
| Create `rate_of_rise/tests/test_gateway_v2_display.py` | Pins: unique, off strapping lines; HA imports name real add-on entities |
| Modify `.github/workflows/tests.yml` | display_core host test, YAML list, TFT-export guard |
| Create `firmware/nextion_display/README.md` | HMI contract as a build checklist, wiring, bring-up |
| Modify `docs/gateway-v2-trial.md` | Pointer to the display |

Local host-test command (this machine has no g++; zig's clang stands in):

```bash
python -m venv "$SCRATCH/zigvenv" && "$SCRATCH/zigvenv/Scripts/python" -m pip install ziglang   # once
"$SCRATCH/zigvenv/Scripts/python" -m ziglang c++ -std=c++17 -Wall -Wextra -Werror \
  -I firmware/esp32s3_feather_gateway/components/creek_display \
  firmware/esp32s3_feather_gateway/tests/test_display_core.cpp -o "$SCRATCH/test_display_core.exe" \
  && "$SCRATCH/test_display_core.exe"
```

CI runs the same with `g++`.

---

### Task 1: display_core values: formatting, freshness, tier, batteries

**Files:**
- Create: `firmware/esp32s3_feather_gateway/components/creek_display/display_core.h`
- Create: `firmware/esp32s3_feather_gateway/tests/test_display_core.cpp`
- Modify: `.github/workflows/tests.yml` (firmware job, after "store_core host tests")

**Interfaces:**
- Produces, all in `namespace creek_display`:
  - constants: `PAGE_SPLASH`, `PAGE_MAIN`, `PAGE_SETTINGS`, `PAGE_CONFIRM`; `ID_BRI_DN`,
    `ID_BRI_UP`, `ID_SLP_DN`, `ID_SLP_UP`, `ID_UPDATE`, `ID_RESTART`, `ID_YES`; `WHITE`,
    `GREY`, `GREEN`, `YELLOW`, `ORANGE`, `RED`, `MAGENTA`; `MISSING`; `RAIN_MAX_AGE_MS`.
  - `std::string fixed(float v, int decimals, bool sign = false)`
  - `struct Shown { std::string text; bool missing; }`
  - `Shown with_unit(float v, int decimals, const char *prefix, const char *unit, bool sign = false)`
  - `std::string age_text(uint32_t ms)`
  - `bool fresh(bool seen, uint32_t at_ms, uint32_t now_ms, uint32_t max_age_ms)`
  - `const char *tier_label(float tier)` (nullptr if not 0–4) and `uint16_t tier_color(float tier)`
  - `int node_battery_level(float mv)` and `uint16_t node_battery_color(int level)`
  - `int gauge_percent(float pct)` and `uint16_t gauge_color(int pct)`

- [ ] **Step 1: Write the failing test**

`firmware/esp32s3_feather_gateway/tests/test_display_core.cpp`:

```cpp
// Host tests for creek_display's pure logic. Build and run from the repo root (one command):
//   g++ -std=c++17 -Wall -Wextra -Werror -I firmware/esp32s3_feather_gateway/components/creek_display
//       firmware/esp32s3_feather_gateway/tests/test_display_core.cpp -o test_display_core
//   ./test_display_core
// No trailing backslashes in this comment: -Werror=comment rejects a line-continued comment.
#include "display_core.h"

#include <cmath>
#include <cstdio>
#include <string>

using namespace creek_display;

static int failures = 0;
#define CHECK(cond)                                                          \
  do {                                                                       \
    if (!(cond)) {                                                           \
      std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);            \
      failures++;                                                            \
    }                                                                        \
  } while (0)

static void test_fixed() {
  CHECK(fixed(2.414f, 2) == "2.41");
  CHECK(fixed(28.94f, 1) == "28.9");
  CHECK(fixed(NAN, 2) == "--");
  CHECK(fixed(0.05f, 2, true) == "+0.05");
  CHECK(fixed(-0.12f, 2, true) == "-0.12");
  // Rounds to zero: never "-0.00" (Review Focus 2).
  CHECK(fixed(-0.001f, 2, true) == "+0.00");
  CHECK(fixed(-0.001f, 2) == "0.00");
  CHECK(fixed(-0.4f, 0) == "0");
  CHECK(fixed(-1.25f, 2) == "-1.25");  // a real negative stage stays negative
}

static void test_with_unit() {
  auto s = with_unit(28.94f, 1, "depth ", " in");
  CHECK(s.text == "depth 28.9 in" && !s.missing);
  s = with_unit(NAN, 1, "depth ", " in");
  CHECK(s.text == "depth --" && s.missing);
  s = with_unit(61.4f, 0, "", "\xB0" "F");
  CHECK(s.text == "61\xB0" "F");
  s = with_unit(0.05f, 2, "", " in/min", true);
  CHECK(s.text == "+0.05 in/min");
}

static void test_age_text() {
  CHECK(age_text(0) == "0 s");
  CHECK(age_text(59999) == "59 s");
  CHECK(age_text(60000) == "1 min");
  CHECK(age_text(3599999) == "59 min");
  CHECK(age_text(3600000) == "1 h");
  CHECK(age_text(7 * 3600000u) == "7 h");
}

static void test_fresh() {
  CHECK(!fresh(false, 0, 0, RAIN_MAX_AGE_MS));
  CHECK(fresh(true, 1000, 1000, RAIN_MAX_AGE_MS));
  CHECK(fresh(true, 1000, 1000 + RAIN_MAX_AGE_MS, RAIN_MAX_AGE_MS));
  CHECK(!fresh(true, 1000, 1001 + RAIN_MAX_AGE_MS, RAIN_MAX_AGE_MS));
  // millis() wrapped between the poll and now (Review Focus 3).
  CHECK(fresh(true, 0xFFFFF000u, 0x00000100u, RAIN_MAX_AGE_MS));
  CHECK(RAIN_MAX_AGE_MS == 300000u);
}

static void test_tier() {
  CHECK(std::string(tier_label(0)) == "All-clear");
  CHECK(std::string(tier_label(2)) == "Watch");
  CHECK(std::string(tier_label(4)) == "Emergency");
  CHECK(tier_color(0) == GREEN && tier_color(1) == YELLOW && tier_color(2) == ORANGE);
  CHECK(tier_color(3) == RED && tier_color(4) == MAGENTA);
  // Not a tier (Review Focus 4).
  CHECK(tier_label(NAN) == nullptr && tier_color(NAN) == GREY);
  CHECK(tier_label(5) == nullptr && tier_label(-1) == nullptr && tier_label(2.5f) == nullptr);
}

static void test_node_battery() {
  CHECK(node_battery_level(NAN) == -1);
  CHECK(node_battery_level(4050) == 100 && node_battery_level(4049) == 75);
  CHECK(node_battery_level(3850) == 75 && node_battery_level(3849) == 50);
  CHECK(node_battery_level(3700) == 50 && node_battery_level(3699) == 25);
  CHECK(node_battery_level(3550) == 25 && node_battery_level(3549) == 0);
  CHECK(node_battery_color(-1) == GREY && node_battery_color(100) == GREEN);
  CHECK(node_battery_color(75) == GREEN && node_battery_color(50) == YELLOW);
  CHECK(node_battery_color(25) == RED && node_battery_color(0) == RED);
}

static void test_gauge() {
  CHECK(gauge_percent(NAN) == -1);
  CHECK(gauge_percent(87.4f) == 87 && gauge_percent(87.5f) == 88);
  CHECK(gauge_percent(103.0f) == 100 && gauge_percent(-2.0f) == 0);
  CHECK(gauge_color(-1) == GREY && gauge_color(50) == GREEN && gauge_color(49) == YELLOW);
  CHECK(gauge_color(20) == YELLOW && gauge_color(19) == RED);
}

int main() {
  test_fixed();
  test_with_unit();
  test_age_text();
  test_fresh();
  test_tier();
  test_node_battery();
  test_gauge();
  if (failures) {
    std::printf("%d failure(s)\n", failures);
    return 1;
  }
  std::printf("all display_core tests passed\n");
  return 0;
}
```

- [ ] **Step 2: Run it and confirm it fails**

Run the local host-test command above.
Expected: compile error `'display_core.h' file not found`.

- [ ] **Step 3: Write the implementation**

`firmware/esp32s3_feather_gateway/components/creek_display/display_core.h`:

```cpp
// Creek gateway v2 display: the pure logic behind display.yaml's Nextion pages. Formatting,
// colours, the HMI's page and component ids, settings steps and touch dispatch live here so
// they run on the host (tests/test_display_core.cpp); display.yaml is only device glue. The
// names and ids are the HMI contract in firmware/nextion_display/README.md (spec:
// docs/superpowers/specs/2026-10-10-gateway-v2-nextion-display-design.md).
#pragma once

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <map>
#include <string>
#include <vector>

namespace creek_display {

// --- HMI contract --------------------------------------------------------------------------
inline constexpr uint8_t PAGE_SPLASH = 0;
inline constexpr uint8_t PAGE_MAIN = 1;
inline constexpr uint8_t PAGE_SETTINGS = 2;
inline constexpr uint8_t PAGE_CONFIRM = 3;

// Ids of the buttons that report a touch ("Send Component ID" on release). The Editor numbers
// components in creation order and the page itself is 0, so these follow the README's tables.
inline constexpr uint8_t ID_BRI_DN = 4;  // settings
inline constexpr uint8_t ID_BRI_UP = 6;
inline constexpr uint8_t ID_SLP_DN = 8;
inline constexpr uint8_t ID_SLP_UP = 10;
inline constexpr uint8_t ID_UPDATE = 11;
inline constexpr uint8_t ID_RESTART = 12;
inline constexpr uint8_t ID_YES = 3;  // confirm

// RGB565, as the Nextion's pco takes them.
inline constexpr uint16_t WHITE = 65535;
inline constexpr uint16_t GREY = 33808;
inline constexpr uint16_t GREEN = 2016;
inline constexpr uint16_t YELLOW = 65504;
inline constexpr uint16_t ORANGE = 64512;
inline constexpr uint16_t RED = 63488;
inline constexpr uint16_t MAGENTA = 63519;

inline constexpr const char *MISSING = "--";

// --- values --------------------------------------------------------------------------------
// v at `decimals` places, MISSING for NaN. A value that rounds to zero prints as zero, never
// "-0.00": a creek falling at 0.001 in/min is not news.
inline std::string fixed(float v, int decimals, bool sign = false) {
  if (std::isnan(v)) return MISSING;
  double d = v;
  if (std::fabs(d) < 0.5 * std::pow(10.0, -decimals)) d = 0.0;
  char buf[32];
  std::snprintf(buf, sizeof buf, sign ? "%+.*f" : "%.*f", decimals, d);
  return buf;
}

// A text field's content, and whether it is the missing placeholder (shown grey).
struct Shown {
  std::string text;
  bool missing;
};

// "<prefix><value><unit>", or "<prefix>--" when v is NaN.
inline Shown with_unit(float v, int decimals, const char *prefix, const char *unit,
                       bool sign = false) {
  if (std::isnan(v)) return {std::string(prefix) + MISSING, true};
  return {std::string(prefix) + fixed(v, decimals, sign) + unit, false};
}

// Time since something, in the coarsest unit that fits: "42 s", "4 min", "2 h".
inline std::string age_text(uint32_t ms) {
  const unsigned s = ms / 1000;
  char buf[24];
  if (s < 60)
    std::snprintf(buf, sizeof buf, "%u s", s);
  else if (s < 3600)
    std::snprintf(buf, sizeof buf, "%u min", s / 60);
  else
    std::snprintf(buf, sizeof buf, "%u h", s / 3600);
  return buf;
}

// Five Ecowitt polls at the 60 s interval.
inline constexpr uint32_t RAIN_MAX_AGE_MS = 5u * 60u * 1000u;

// Unsigned subtraction keeps this right across the millis() wrap (~49.7 days).
inline bool fresh(bool seen, uint32_t at_ms, uint32_t now_ms, uint32_t max_age_ms) {
  return seen && static_cast<uint32_t>(now_ms - at_ms) <= max_age_ms;
}

// The add-on's labels (rate_of_rise/app/tiers.py TIER_LABELS). The HA entity's state is the
// tier number; anything that is not exactly 0-4 is not a tier.
inline const char *tier_label(float tier) {
  static const char *const LABELS[] = {"All-clear", "Advisory", "Watch", "Warning", "Emergency"};
  if (std::isnan(tier) || tier < 0.0f || tier > 4.0f || tier != std::floor(tier)) return nullptr;
  return LABELS[static_cast<int>(tier)];
}

inline uint16_t tier_color(float tier) {
  static const uint16_t COLORS[] = {GREEN, YELLOW, ORANGE, RED, MAGENTA};
  return tier_label(tier) ? COLORS[static_cast<int>(tier)] : GREY;
}

// The bands of HA's "Creek Node Battery Level" (ha-packages/creek_node_health.yaml: 4.05 /
// 3.85 / 3.70 / 3.55 V) as a 0-100 bar. -1 when there is no reading.
inline int node_battery_level(float mv) {
  if (std::isnan(mv)) return -1;
  if (mv >= 4050.0f) return 100;
  if (mv >= 3850.0f) return 75;
  if (mv >= 3700.0f) return 50;
  if (mv >= 3550.0f) return 25;
  return 0;
}

inline uint16_t node_battery_color(int level) {
  if (level < 0) return GREY;
  if (level >= 75) return GREEN;
  if (level >= 50) return YELLOW;
  return RED;
}

// The MAX17043's state of charge, rounded and clamped to the bar's 0-100 (the gauge can read
// a little over 100 % on a full pack). -1 when there is no reading.
inline int gauge_percent(float pct) {
  if (std::isnan(pct)) return -1;
  const long p = std::lround(pct);
  return p < 0 ? 0 : (p > 100 ? 100 : static_cast<int>(p));
}

inline uint16_t gauge_color(int pct) {
  if (pct < 0) return GREY;
  if (pct >= 50) return GREEN;
  if (pct >= 20) return YELLOW;
  return RED;
}

}  // namespace creek_display
```

- [ ] **Step 4: Run it and confirm it passes**

Run the local host-test command.
Expected: `all display_core tests passed`.

- [ ] **Step 5: Run it in CI.** In `.github/workflows/tests.yml`, after the `store_core host tests` step of the `firmware` job, add:

```yaml
      - name: display_core host tests
        # creek_display's pure logic (formatting, colours, settings steps, touch dispatch) runs
        # on the host; display.yaml's glue is covered by the compile below and the bench.
        run: |
          g++ -std=c++17 -Wall -Wextra -Werror -I firmware/esp32s3_feather_gateway/components/creek_display firmware/esp32s3_feather_gateway/tests/test_display_core.cpp -o /tmp/test_display_core
          /tmp/test_display_core
```

- [ ] **Step 6: Commit**

```bash
git add firmware/esp32s3_feather_gateway/components/creek_display/display_core.h \
        firmware/esp32s3_feather_gateway/tests/test_display_core.cpp .github/workflows/tests.yml
git commit -m "display: value formatting, freshness, tier and battery bands (host-tested)"
```

---

### Task 2: display_core main page: render_main and SentCache

**Files:**
- Modify: `firmware/esp32s3_feather_gateway/components/creek_display/display_core.h` (append before the closing namespace)
- Modify: `firmware/esp32s3_feather_gateway/tests/test_display_core.cpp`

**Interfaces:**
- Consumes everything from Task 1.
- Produces:
  - `struct MainInputs` (fields below).
  - `struct Field { std::string name; std::string text; uint16_t color; int value; }`. `value >= 0` means a progress bar.
  - `std::vector<Field> render_main(const MainInputs &in)`, returning the 15 main-page
    components in contract order (`bSet` is static).
  - `struct Delta { bool text, color, value; }`
  - `class SentCache { Delta update(const Field &f); void clear(); }`

- [ ] **Step 1: Write the failing tests.** Add these functions to `test_display_core.cpp` above `main()`, and call each from `main()`:

```cpp
static const Field *field(const std::vector<Field> &fields, const char *name) {
  for (const auto &f : fields)
    if (f.name == name) return &f;
  return nullptr;
}

static MainInputs all_known() {
  MainInputs in;
  in.stage_ft = 2.414f;
  in.depth_in = 28.94f;
  in.ha_connected = true;
  in.tier = 2;
  in.rise_in_min = 0.05f;
  in.rain_fresh = true;
  in.rain_rate_in_hr = 0.42f;
  in.rain_event_in = 1.37f;
  in.rain_24h_in = 1.52f;
  in.temp_f = 61.4f;
  in.node_online = 1;
  in.node_heard = true;
  in.node_age_ms = 42000;
  in.node_batt_mv = 4021;
  in.gw_batt_pct = 87.2f;
  in.sd_fault = 0;
  return in;
}

static void test_render_main_all_known() {
  const auto f = render_main(all_known());
  const char *order[] = {"tStage", "tDepth", "tTier", "tRise", "tRate", "tTemp", "tEvent",
                         "t24h", "tNode", "tHA", "tSD", "tNodeBat", "jNodeBat", "tGwBat",
                         "jGwBat"};
  CHECK(f.size() == sizeof(order) / sizeof(order[0]));
  for (size_t i = 0; i < f.size() && i < sizeof(order) / sizeof(order[0]); i++)
    CHECK(f[i].name == order[i]);
  CHECK(field(f, "tStage")->text == "2.41 ft" && field(f, "tStage")->color == WHITE);
  CHECK(field(f, "tDepth")->text == "depth 28.9 in");
  CHECK(field(f, "tTier")->text == "Watch" && field(f, "tTier")->color == ORANGE);
  CHECK(field(f, "tRise")->text == "+0.05 in/min");
  CHECK(field(f, "tRate")->text == "0.42 in/h");
  CHECK(field(f, "tTemp")->text == "61\xB0" "F");
  CHECK(field(f, "tEvent")->text == "event 1.37 in");
  CHECK(field(f, "t24h")->text == "24 h 1.52 in");
  CHECK(field(f, "tNode")->text == "node 42 s" && field(f, "tNode")->color == WHITE);
  CHECK(field(f, "tHA")->text == "HA ok" && field(f, "tHA")->color == GREEN);
  CHECK(field(f, "tSD")->text == "SD ok" && field(f, "tSD")->color == GREEN);
  CHECK(field(f, "tNodeBat")->text == "node 4.02 V");
  CHECK(field(f, "jNodeBat")->value == 75 && field(f, "jNodeBat")->color == GREEN);
  CHECK(field(f, "tGwBat")->text == "gw 87 %");
  CHECK(field(f, "jGwBat")->value == 87 && field(f, "jGwBat")->color == GREEN);
  for (const auto &x : f) CHECK((x.name[0] == 'j') == (x.value >= 0));  // only bars carry .val
}

static void test_render_main_nothing_known() {
  const auto f = render_main(MainInputs{});
  CHECK(field(f, "tStage")->text == "--" && field(f, "tStage")->color == GREY);
  CHECK(field(f, "tDepth")->text == "depth --" && field(f, "tDepth")->color == GREY);
  CHECK(field(f, "tTier")->text == "--" && field(f, "tTier")->color == GREY);
  CHECK(field(f, "tRise")->text == "--");
  CHECK(field(f, "tRate")->text == "--" && field(f, "tEvent")->text == "event --");
  CHECK(field(f, "tNode")->text == "node --" && field(f, "tNode")->color == GREY);
  CHECK(field(f, "tHA")->text == "HA offline" && field(f, "tHA")->color == RED);
  CHECK(field(f, "tSD")->text == "SD --" && field(f, "tSD")->color == GREY);
  CHECK(field(f, "tNodeBat")->text == "node --");
  CHECK(field(f, "jNodeBat")->value == 0 && field(f, "jNodeBat")->color == GREY);
  CHECK(field(f, "tGwBat")->text == "gw --");
  CHECK(field(f, "jGwBat")->value == 0 && field(f, "jGwBat")->color == GREY);
}

static void test_render_main_degraded() {
  // HA down: the add-on's values blank even though the imported sensors still hold them.
  auto in = all_known();
  in.ha_connected = false;
  auto f = render_main(in);
  CHECK(field(f, "tTier")->text == "--" && field(f, "tTier")->color == GREY);
  CHECK(field(f, "tRise")->text == "--");
  CHECK(field(f, "tHA")->text == "HA offline" && field(f, "tHA")->color == RED);
  CHECK(field(f, "tStage")->text == "2.41 ft");  // local values unaffected

  // Ecowitt stale: rain and temperature blank, the last values notwithstanding.
  in = all_known();
  in.rain_fresh = false;
  f = render_main(in);
  CHECK(field(f, "tRate")->text == "--" && field(f, "tTemp")->text == "--");
  CHECK(field(f, "t24h")->text == "24 h --");

  // Node offline outranks a recent packet; SD fault is red.
  in = all_known();
  in.node_online = 0;
  in.sd_fault = 1;
  f = render_main(in);
  CHECK(field(f, "tNode")->text == "node offline" && field(f, "tNode")->color == RED);
  CHECK(field(f, "tSD")->text == "SD fault" && field(f, "tSD")->color == RED);

  // Status unknown but a packet arrived: show its age.
  in = all_known();
  in.node_online = -1;
  in.node_age_ms = 4 * 60000;
  CHECK(render_main(in)[8].text == "node 4 min");
}

static void test_sent_cache() {
  SentCache c;
  const Field text{"tStage", "2.41 ft", WHITE, -1};
  Delta d = c.update(text);
  CHECK(d.text && d.color && !d.value);  // first time: everything a text field has
  d = c.update(text);
  CHECK(!d.text && !d.color && !d.value);  // unchanged: nothing
  d = c.update({"tStage", "2.42 ft", WHITE, -1});
  CHECK(d.text && !d.color);
  d = c.update({"tStage", "--", GREY, -1});
  CHECK(d.text && d.color);

  const Field bar{"jGwBat", "", GREEN, 87};
  d = c.update(bar);
  CHECK(!d.text && d.color && d.value);  // first time: everything a bar has
  d = c.update({"jGwBat", "", GREEN, 86});
  CHECK(d.value && !d.color && !d.text);

  c.clear();  // page re-entered or display woke: resend all
  d = c.update(text);
  CHECK(d.text && d.color);
}
```

`main()` gains:

```cpp
  test_render_main_all_known();
  test_render_main_nothing_known();
  test_render_main_degraded();
  test_sent_cache();
```

- [ ] **Step 2: Run it and confirm it fails**

Run the local host-test command.
Expected: compile errors `unknown type name 'MainInputs'`, `use of undeclared identifier 'render_main'`.

- [ ] **Step 3: Write the implementation.** Append inside `namespace creek_display` in `display_core.h`:

```cpp
// --- main page -----------------------------------------------------------------------------
// One value per data source; NaN or -1 means unknown. Time-dependent facts (node age, rain
// freshness) are worked out by the caller, so render_main is pure.
struct MainInputs {
  float stage_ft = NAN;
  float depth_in = NAN;
  bool ha_connected = false;
  float tier = NAN;         // add-on alert tier, 0-4 (HA import)
  float rise_in_min = NAN;  // add-on rate of rise (HA import)
  bool rain_fresh = false;  // a good Ecowitt poll within RAIN_MAX_AGE_MS
  float rain_rate_in_hr = NAN;
  float rain_event_in = NAN;
  float rain_24h_in = NAN;
  float temp_f = NAN;
  int node_online = -1;      // node status: -1 unknown, 0 offline, 1 online
  bool node_heard = false;   // a packet since boot
  uint32_t node_age_ms = 0;  // since that packet; meaningful only when node_heard
  float node_batt_mv = NAN;
  float gw_batt_pct = NAN;
  int sd_fault = -1;  // -1 unknown, 0 ok, 1 fault
};

// One main-page component's state. value >= 0 marks a progress bar (.val; text unused),
// -1 a text component (.txt).
struct Field {
  std::string name;
  std::string text;
  uint16_t color;
  int value;
};

// Every main-page component the gateway writes, in the README's creation order.
inline std::vector<Field> render_main(const MainInputs &in) {
  std::vector<Field> out;
  auto text = [&out](const char *name, const Shown &s, uint16_t color) {
    out.push_back({name, s.text, s.missing ? GREY : color, -1});
  };
  auto bar = [&out](const char *name, int level, uint16_t color) {
    out.push_back({name, "", color, level < 0 ? 0 : level});
  };
  auto rain = [&in](float v) { return in.rain_fresh ? v : NAN; };

  text("tStage", with_unit(in.stage_ft, 2, "", " ft"), WHITE);
  text("tDepth", with_unit(in.depth_in, 1, "depth ", " in"), WHITE);
  // The add-on's values are only as good as the link to HA: blank them when it is down,
  // rather than show the last tier as if it were current.
  const char *label = in.ha_connected ? tier_label(in.tier) : nullptr;
  text("tTier", label ? Shown{label, false} : Shown{MISSING, true}, tier_color(in.tier));
  text("tRise", with_unit(in.ha_connected ? in.rise_in_min : NAN, 2, "", " in/min", true), WHITE);
  text("tRate", with_unit(rain(in.rain_rate_in_hr), 2, "", " in/h"), WHITE);
  text("tTemp", with_unit(rain(in.temp_f), 0, "", "\xB0" "F"), WHITE);
  text("tEvent", with_unit(rain(in.rain_event_in), 2, "event ", " in"), WHITE);
  text("t24h", with_unit(rain(in.rain_24h_in), 2, "24 h ", " in"), WHITE);

  if (in.node_online == 0)
    text("tNode", {"node offline", false}, RED);
  else if (in.node_heard)
    text("tNode", {"node " + age_text(in.node_age_ms), false}, WHITE);
  else
    text("tNode", {std::string("node ") + MISSING, true}, WHITE);
  text("tHA", {in.ha_connected ? "HA ok" : "HA offline", false}, in.ha_connected ? GREEN : RED);
  if (in.sd_fault < 0)
    text("tSD", {std::string("SD ") + MISSING, true}, WHITE);
  else
    text("tSD", {in.sd_fault ? "SD fault" : "SD ok", false}, in.sd_fault ? RED : GREEN);

  const int node_level = node_battery_level(in.node_batt_mv);
  text("tNodeBat", with_unit(in.node_batt_mv / 1000.0f, 2, "node ", " V"), WHITE);
  bar("jNodeBat", node_level, node_battery_color(node_level));
  const int gw = gauge_percent(in.gw_batt_pct);
  text("tGwBat", with_unit(gw < 0 ? NAN : static_cast<float>(gw), 0, "gw ", " %"), WHITE);
  bar("jGwBat", gw, gauge_color(gw));
  return out;
}

// Which of a field's attributes need sending.
struct Delta {
  bool text = false;
  bool color = false;
  bool value = false;
};

// What the display was last sent, per component, so the periodic refresh sends only changes.
// clear() whenever the main page is (re)entered or the display wakes: the Nextion resets a
// page's components on entry and drops updates while asleep.
class SentCache {
 public:
  Delta update(const Field &f) {
    Delta d;
    const bool is_bar = f.value >= 0;
    auto it = this->sent_.find(f.name);
    if (it == this->sent_.end()) {
      d.text = !is_bar;
      d.color = true;
      d.value = is_bar;
      this->sent_.emplace(f.name, f);
      return d;
    }
    d.text = !is_bar && it->second.text != f.text;
    d.color = it->second.color != f.color;
    d.value = is_bar && it->second.value != f.value;
    it->second = f;
    return d;
  }
  void clear() { this->sent_.clear(); }

 private:
  std::map<std::string, Field> sent_;
};
```

Note the order: `tTemp` sits between `tRate` and `tEvent`, matching the README's creation order
(component #9 comes before #10 `tEvent`). The test's `order[]` array encodes the same.

- [ ] **Step 4: Run it and confirm it passes**

Run the local host-test command.
Expected: `all display_core tests passed`.

- [ ] **Step 5: Commit**

```bash
git add firmware/esp32s3_feather_gateway/components/creek_display/display_core.h \
        firmware/esp32s3_feather_gateway/tests/test_display_core.cpp
git commit -m "display: main-page rendering and a send-only-changes cache (host-tested)"
```

---

### Task 3: display_core settings, touch dispatch and confirm

**Files:**
- Modify: `firmware/esp32s3_feather_gateway/components/creek_display/display_core.h`
- Modify: `firmware/esp32s3_feather_gateway/tests/test_display_core.cpp`

**Interfaces:**
- Consumes the Task 1 id constants.
- Produces:
  - Brightness: `BRIGHTNESS_MIN/MAX/STEP/DEFAULT`, `int clamp_brightness(int)`,
    `int step_brightness(int pct, int dir)`, `std::string brightness_text(int pct)`.
  - Sleep: `struct SleepOption { uint16_t seconds; const char *label; }`, `SLEEP_OPTIONS[]`,
    `SLEEP_OPTION_COUNT`, `SLEEP_DEFAULT`, `int clamp_sleep_index(int)`,
    `int step_sleep(int idx, int dir)`, `const SleepOption &sleep_option(int idx)`.
  - Touch: `enum class Action { NONE, BRIGHT_DOWN, BRIGHT_UP, SLEEP_DOWN, SLEEP_UP, ASK_UPDATE, ASK_RESTART, CONFIRM }`
    and `Action touch_action(uint8_t page, uint8_t component, bool press)`.
  - Confirm: `enum class Pending { NONE = 0, RESTART = 1, UPDATE = 2 }`,
    `Pending to_pending(int raw)`, `const char *confirm_text(Pending)`,
    `const char *working_text(Pending)`.

- [ ] **Step 1: Write the failing tests.** Add these functions and call them from `main()`:

```cpp
static void test_brightness() {
  CHECK(BRIGHTNESS_DEFAULT == 70);
  CHECK(step_brightness(70, 1) == 80 && step_brightness(70, -1) == 60);
  CHECK(step_brightness(100, 1) == 100 && step_brightness(10, -1) == 10);
  // Restored from flash out of range (Review Focus 1): never dark, never over 100.
  CHECK(clamp_brightness(0) == 10 && clamp_brightness(-5) == 10 && clamp_brightness(250) == 100);
  CHECK(step_brightness(0, 1) == 20);
  CHECK(brightness_text(70) == "70 %" && brightness_text(0) == "10 %");
}

static void test_sleep() {
  CHECK(SLEEP_OPTION_COUNT == 6 && SLEEP_DEFAULT == 2);
  CHECK(sleep_option(SLEEP_DEFAULT).seconds == 120);
  CHECK(std::string(sleep_option(SLEEP_DEFAULT).label) == "2 min");
  CHECK(sleep_option(0).seconds == 30 && sleep_option(5).seconds == 0);
  CHECK(std::string(sleep_option(5).label) == "Never");
  CHECK(step_sleep(2, 1) == 3 && step_sleep(2, -1) == 1);
  CHECK(step_sleep(0, -1) == 0 && step_sleep(5, 1) == 5);
  // A bad restored index is the default, not "Never" (Review Focus 1).
  CHECK(clamp_sleep_index(-1) == SLEEP_DEFAULT && clamp_sleep_index(6) == SLEEP_DEFAULT);
  CHECK(sleep_option(99).seconds == 120);
  CHECK(step_sleep(99, 1) == 3);
}

static void test_touch_action() {
  CHECK(touch_action(PAGE_SETTINGS, ID_BRI_DN, false) == Action::BRIGHT_DOWN);
  CHECK(touch_action(PAGE_SETTINGS, ID_BRI_UP, false) == Action::BRIGHT_UP);
  CHECK(touch_action(PAGE_SETTINGS, ID_SLP_DN, false) == Action::SLEEP_DOWN);
  CHECK(touch_action(PAGE_SETTINGS, ID_SLP_UP, false) == Action::SLEEP_UP);
  CHECK(touch_action(PAGE_SETTINGS, ID_UPDATE, false) == Action::ASK_UPDATE);
  CHECK(touch_action(PAGE_SETTINGS, ID_RESTART, false) == Action::ASK_RESTART);
  CHECK(touch_action(PAGE_CONFIRM, ID_YES, false) == Action::CONFIRM);
  // Press events, other components, other pages: nothing.
  CHECK(touch_action(PAGE_SETTINGS, ID_RESTART, true) == Action::NONE);
  CHECK(touch_action(PAGE_CONFIRM, ID_YES, true) == Action::NONE);
  CHECK(touch_action(PAGE_SETTINGS, 2, false) == Action::NONE);   // bBack navigates in the HMI
  CHECK(touch_action(PAGE_SETTINGS, 99, false) == Action::NONE);
  CHECK(touch_action(PAGE_MAIN, ID_RESTART, false) == Action::NONE);  // same id, wrong page
  CHECK(touch_action(PAGE_CONFIRM, 2, false) == Action::NONE);    // bNo navigates in the HMI
  CHECK(touch_action(PAGE_SPLASH, ID_YES, false) == Action::NONE);
  // The ids are the README's creation order.
  CHECK(ID_BRI_DN == 4 && ID_BRI_UP == 6 && ID_SLP_DN == 8 && ID_SLP_UP == 10);
  CHECK(ID_UPDATE == 11 && ID_RESTART == 12 && ID_YES == 3);
  CHECK(PAGE_SPLASH == 0 && PAGE_MAIN == 1 && PAGE_SETTINGS == 2 && PAGE_CONFIRM == 3);
}

static void test_pending() {
  CHECK(to_pending(0) == Pending::NONE);
  CHECK(to_pending(1) == Pending::RESTART && to_pending(2) == Pending::UPDATE);
  // Garbage is nothing pending, so Confirm does nothing (Review Focus 5).
  CHECK(to_pending(3) == Pending::NONE && to_pending(-1) == Pending::NONE);
  CHECK(static_cast<int>(Pending::RESTART) == 1 && static_cast<int>(Pending::UPDATE) == 2);
  CHECK(std::string(confirm_text(Pending::RESTART)).find("Restart") == 0);
  CHECK(std::string(confirm_text(Pending::UPDATE)).find("radio") != std::string::npos);
  CHECK(std::string(confirm_text(Pending::NONE)).empty());
  CHECK(std::string(working_text(Pending::RESTART)) == "Restarting...");
  CHECK(std::string(working_text(Pending::UPDATE)) == "Updating display...");
  // Every message must fit tMsg's txt_maxl (200) and carry no character that breaks the
  // Nextion's quoted-string syntax.
  for (Pending p : {Pending::RESTART, Pending::UPDATE}) {
    const std::string s = confirm_text(p);
    CHECK(s.size() <= 200 && s.find('"') == std::string::npos && s.find('\\') == std::string::npos);
  }
}
```

`main()` gains:

```cpp
  test_brightness();
  test_sleep();
  test_touch_action();
  test_pending();
```

- [ ] **Step 2: Run it and confirm it fails**

Run the local host-test command.
Expected: compile errors for `BRIGHTNESS_DEFAULT`, `step_brightness`, `Action` and the rest.

- [ ] **Step 3: Write the implementation.** Append inside `namespace creek_display`:

```cpp
// --- settings ------------------------------------------------------------------------------
inline constexpr int BRIGHTNESS_MIN = 10;
inline constexpr int BRIGHTNESS_MAX = 100;
inline constexpr int BRIGHTNESS_STEP = 10;
inline constexpr int BRIGHTNESS_DEFAULT = 70;

// Restored globals come back from flash; anything out of range is pulled into it, so a bad
// value can never leave the backlight off.
inline int clamp_brightness(int pct) {
  return pct < BRIGHTNESS_MIN ? BRIGHTNESS_MIN : (pct > BRIGHTNESS_MAX ? BRIGHTNESS_MAX : pct);
}

inline int step_brightness(int pct, int dir) {
  return clamp_brightness(clamp_brightness(pct) + dir * BRIGHTNESS_STEP);
}

inline std::string brightness_text(int pct) {
  char buf[16];
  std::snprintf(buf, sizeof buf, "%d %%", clamp_brightness(pct));
  return buf;
}

struct SleepOption {
  uint16_t seconds;  // the Nextion's thsp; 0 is never
  const char *label;
};

inline constexpr SleepOption SLEEP_OPTIONS[] = {{30, "30 s"},   {60, "1 min"},   {120, "2 min"},
                                                {300, "5 min"}, {600, "10 min"}, {0, "Never"}};
inline constexpr int SLEEP_OPTION_COUNT = sizeof(SLEEP_OPTIONS) / sizeof(SLEEP_OPTIONS[0]);
inline constexpr int SLEEP_DEFAULT = 2;  // 2 min

// An index restored from flash that is not an option becomes the default, not an end of the
// list: "Never" would keep the backlight on and drain the pack.
inline int clamp_sleep_index(int idx) {
  return idx >= 0 && idx < SLEEP_OPTION_COUNT ? idx : SLEEP_DEFAULT;
}

inline int step_sleep(int idx, int dir) {
  const int i = clamp_sleep_index(idx) + dir;
  return i < 0 ? 0 : (i >= SLEEP_OPTION_COUNT ? SLEEP_OPTION_COUNT - 1 : i);
}

inline const SleepOption &sleep_option(int idx) { return SLEEP_OPTIONS[clamp_sleep_index(idx)]; }

// --- touch ---------------------------------------------------------------------------------
enum class Action : uint8_t {
  NONE,
  BRIGHT_DOWN,
  BRIGHT_UP,
  SLEEP_DOWN,
  SLEEP_UP,
  ASK_UPDATE,
  ASK_RESTART,
  CONFIRM,
};

// The HMI sends component ids on Touch Release only, and only for these buttons; navigation
// buttons (Settings, Back, Cancel) change page inside the HMI. Anything else is NONE.
inline Action touch_action(uint8_t page, uint8_t component, bool press) {
  if (press) return Action::NONE;
  if (page == PAGE_SETTINGS) {
    switch (component) {
      case ID_BRI_DN:
        return Action::BRIGHT_DOWN;
      case ID_BRI_UP:
        return Action::BRIGHT_UP;
      case ID_SLP_DN:
        return Action::SLEEP_DOWN;
      case ID_SLP_UP:
        return Action::SLEEP_UP;
      case ID_UPDATE:
        return Action::ASK_UPDATE;
      case ID_RESTART:
        return Action::ASK_RESTART;
      default:
        return Action::NONE;
    }
  }
  if (page == PAGE_CONFIRM && component == ID_YES) return Action::CONFIRM;
  return Action::NONE;
}

// --- confirm -------------------------------------------------------------------------------
enum class Pending : uint8_t { NONE = 0, RESTART = 1, UPDATE = 2 };

// The pending action is kept in an int global. Anything that is not a known action (say the
// confirm page is still up after a gateway restart, when the global is back to 0) is NONE,
// so Confirm then does nothing.
inline Pending to_pending(int raw) {
  return raw == 1 ? Pending::RESTART : (raw == 2 ? Pending::UPDATE : Pending::NONE);
}

// tMsg's txt_maxl is 200. No double quotes or backslashes: the text goes out inside a
// Nextion string literal.
inline const char *confirm_text(Pending p) {
  switch (p) {
    case Pending::RESTART:
      return "Restart the gateway? It is back in about 30 s. Records already on the SD card "
             "are kept.";
    case Pending::UPDATE:
      return "Update the display from GitHub? The radio is paused for the transfer (minutes), "
             "so node packets in that time are lost. The gateway restarts afterwards.";
    default:
      return "";
  }
}

inline const char *working_text(Pending p) {
  switch (p) {
    case Pending::RESTART:
      return "Restarting...";
    case Pending::UPDATE:
      return "Updating display...";
    default:
      return "";
  }
}
```

- [ ] **Step 4: Run it and confirm it passes**

Run the local host-test command.
Expected: `all display_core tests passed`.

- [ ] **Step 5: Commit**

```bash
git add firmware/esp32s3_feather_gateway/components/creek_display/display_core.h \
        firmware/esp32s3_feather_gateway/tests/test_display_core.cpp
git commit -m "display: settings steps, touch dispatch and confirm actions (host-tested)"
```

---

### Task 4: Gateway plumbing: creek_display component, Ecowitt accessor, entity ids

**Files:**
- Create: `firmware/esp32s3_feather_gateway/components/creek_display/__init__.py`
- Modify: `firmware/esp32s3_feather_gateway/components/creek_store/creek_store.h` (public accessor near `ecowitt_task()`, members near `eco_reading_`, assignment in `collect_ecowitt_()`)
- Modify: `firmware/esp32s3_feather_gateway/gateway.base.yaml`

**Interfaces:**
- Produces:
  - `bool creek_store::CreekStore::last_ecowitt(creek_core::EcowittReading &out, uint32_t &at_ms) const`.
  - YAML ids `node_online` (binary_sensor), `node_battery_mv` (sensor, mV),
    `gw_battery_level` (sensor, %) and `store_sd_fault` (binary_sensor).
  - Component key `creek_display:`.

- [ ] **Step 1: Create the component.** `components/creek_display/__init__.py`:

```python
"""Creek gateway v2 display helpers: display_core.h, the pure logic behind display.yaml.

There is no config and no generated code. `creek_display:` in display.yaml loads the
component so ESPHome copies display_core.h into the build, where esphome.h includes it for
display.yaml's lambdas. The logic is host-tested in tests/test_display_core.cpp.
"""
import esphome.config_validation as cv

CODEOWNERS = ["@ryanbuiltthat"]

CONFIG_SCHEMA = cv.Schema({})


async def to_code(config):
    pass
```

- [ ] **Step 2: Add the accessor to creek_store.** In `collect_ecowitt_()`, after the lock block and before `enqueue_`:

```cpp
    this->eco_last_ = r;
    this->eco_last_ms_ = millis();
    this->eco_last_seen_ = true;
```

In the `public:` block that holds `ecowitt_task()`, before it:

```cpp
  // The latest good Ecowitt reading and the millis() it was collected; false until the first.
  // Written by collect_ecowitt_() on the main task, so read it from the main task (display.yaml).
  bool last_ecowitt(creek_core::EcowittReading &out, uint32_t &at_ms) const {
    if (!this->eco_last_seen_) return false;
    out = this->eco_last_;
    at_ms = this->eco_last_ms_;
    return true;
  }
```

Next to `creek_core::EcowittReading eco_reading_;`:

```cpp
  // Main task only: the last good reading, for last_ecowitt().
  creek_core::EcowittReading eco_last_;
  uint32_t eco_last_ms_{0};
  bool eco_last_seen_{false};
```

- [ ] **Step 3: Add the ids in gateway.base.yaml.**
  - `battery_voltage:` under `rfm69_gateway` gains `id: node_battery_mv`.
  - `node_status:` gains `id: node_online`.
  - `sd_fault:` under `creek_store` gains `id: store_sd_fault`.
  - `battery_level:` under `max17043` gains `id: gw_battery_level`.
  - The WIRING comment gains:
    `#   Display UART TX / RX GPIO39 / GPIO38 (Feather TX/RX) — display.yaml`.

- [ ] **Step 4: Validate.** Run from `firmware/esp32s3_feather_gateway/`:
  `python -m esphome config gateway.yaml > /dev/null && echo OK`.
  Expected: `OK`. (`creek_display` is not referenced yet, so this checks only the base edits.
  The accessor compiles in Task 5.)

- [ ] **Step 5: Commit**

```bash
git add firmware/esp32s3_feather_gateway/components/creek_display/__init__.py \
        firmware/esp32s3_feather_gateway/components/creek_store/creek_store.h \
        firmware/esp32s3_feather_gateway/gateway.base.yaml
git commit -m "gateway v2: creek_display component, last good Ecowitt reading, ids for the display"
```

---

### Task 5: display.yaml, wrappers, contract test, compile

**Files:**
- Create: `firmware/esp32s3_feather_gateway/display.yaml`
- Modify: `firmware/esp32s3_feather_gateway/gateway.yaml`, `creek-gateway-v2.yaml`, `creek-gateway-v2.prod.yaml`
- Create: `rate_of_rise/tests/test_gateway_v2_display.py`
- Modify: `.github/workflows/tests.yml` (YAML well-formedness list)

**Interfaces:**
- Consumes:
  - Tasks 1–3: `creek_display::*`.
  - Task 4: `last_ecowitt`, ids `node_online`, `node_battery_mv`, `gw_battery_level` and
    `store_sd_fault`.
  - Base: ids `creek_radio`, `store`, `creek_stage` and `creek_depth_in`.
- Produces:
  - Entity `button.creek_gateway_v2_update_display_firmware`.
  - Substitutions `display_tx_pin`, `display_rx_pin`, `display_tft_url` and
    `display_upload_baud`.

- [ ] **Step 1: Write the failing contract test.** `rate_of_rise/tests/test_gateway_v2_display.py`:

```python
"""Gateway v2's display package must not collide with the gateway and must read real entities.

display.yaml adds two GPIOs to a board whose other pins live in gateway.base.yaml, and imports
two of the add-on's entities by id. Neither is checked by the compiler: a doubled pin builds
fine and fights on the wire, and a mistyped entity id imports nothing, so the tier on the
screen stays `--` for ever. This test is what catches both.

Run: python rate_of_rise/tests/test_gateway_v2_display.py
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "rate_of_rise"))

from app.discovery import DiscoveryPublisher  # noqa: E402

V2 = ROOT / "firmware" / "esp32s3_feather_gateway"
BASE = V2 / "gateway.base.yaml"
DISPLAY = V2 / "display.yaml"
WRAPPERS = [V2 / "gateway.yaml", V2 / "creek-gateway-v2.yaml", V2 / "creek-gateway-v2.prod.yaml"]

# ESP32-S3 strapping pins. GPIO3 (SDA) is one, by Adafruit's design and harmlessly (see the
# WIRING comment in gateway.base.yaml); nothing new may land on any of them.
S3_STRAPPING_PINS = {"0", "3", "45", "46"}


class _EsphomeLoader(yaml.SafeLoader):
    """ESPHome YAML carries tags SafeLoader rejects (`!secret`, `!lambda`, `!include`)."""


_EsphomeLoader.add_multi_constructor("!", lambda loader, suffix, node: f"<{suffix}>")


def load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_EsphomeLoader)


def pins(doc: dict) -> dict[str, str]:
    return {k: str(v) for k, v in doc.get("substitutions", {}).items() if k.endswith("_pin")}


def test_display_pins_are_unique_and_off_strapping_lines():
    base, display = load(BASE), load(DISPLAY)
    i2c = {"i2c_sda": str(base["i2c"]["sda"]), "i2c_scl": str(base["i2c"]["scl"])}
    every = list(pins(base).values()) + list(i2c.values()) + list(pins(display).values())
    assert len(every) == len(set(every)), f"a GPIO is assigned twice: {sorted(every)}"
    bad = {k: v for k, v in pins(display).items() if v in S3_STRAPPING_PINS}
    assert not bad, f"display pins on ESP32-S3 strapping lines: {bad}"
    assert pins(display) == {"display_tx_pin": "39", "display_rx_pin": "38"}


def test_display_imports_entities_the_addon_publishes():
    published = set(DiscoveryPublisher(lambda *a: None, "creek").entity_ids().values())
    imported = [s["entity_id"] for s in load(DISPLAY)["sensor"] if s.get("platform") == "homeassistant"]
    assert sorted(imported) == ["sensor.rate_of_rise_creek_alert_tier",
                                "sensor.rate_of_rise_creek_rate_of_rise"], imported
    missing = [e for e in imported if e not in published]
    assert not missing, f"display imports entities the add-on does not publish: {missing}"


def test_every_v2_wrapper_loads_the_display():
    for path in WRAPPERS:
        doc = load(path)
        assert "display" in doc["packages"], f"{path.name} does not include display.yaml"
        comps = [c for src in doc["external_components"] for c in src["components"]]
        assert "creek_display" in comps, f"{path.name} does not load creek_display"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python rate_of_rise/tests/test_gateway_v2_display.py`
Expected: `FileNotFoundError` for `display.yaml`.

- [ ] **Step 3: Write display.yaml**

```yaml
# Creek gateway v2: Nextion NX3224T024 touch display. Its own package (like ota-push.yaml) so
# the display is one include in each wrapper. Spec:
# docs/superpowers/specs/2026-10-10-gateway-v2-nextion-display-design.md. The pure logic is
# components/creek_display/display_core.h (host-tested); the HMI it drives, with the names
# and ids this file and that header rely on: firmware/nextion_display/README.md.
#
# WIRING (4-pin Nextion harness; the display runs from the LiPo through a Pololu U1V11F5):
#   Feather BAT -> U1V11F5 VIN, SHDN tied to VIN; U1V11F5 VOUT -> Nextion 5V (red)
#   Feather GND -> U1V11F5 GND and Nextion GND (black)
#   Feather TX GPIO39 -> Nextion RX (yellow); Feather RX GPIO38 <- Nextion TX (blue)
# The logger is on USB-Serial-JTAG, so ESPHome's UART driver hands the display UART0. Arduino's
# `Serial`, where the RFM69 library prints, is UART0 too, but nothing calls Serial.begin(),
# so those prints are dropped inside the core and never reach the display.

substitutions:
  display_tx_pin: "39"
  display_rx_pin: "38"
  display_tft_url: "https://raw.githubusercontent.com/ryanbuiltthat/rate-of-rise/main/firmware/nextion_display/creek_gateway_v2_display.tft"
  # TFT upload baud; 0 = the running 115200. The main task (and so the radio) is not serviced
  # during an upload, so faster is better if the wiring holds it (bring-up tries 921600).
  display_upload_baud: "0"

creek_display:

uart:
  - id: display_uart
    tx_pin: ${display_tx_pin}
    rx_pin: ${display_rx_pin}
    baud_rate: 115200

globals:
  - id: disp_brightness
    type: int
    restore_value: true
    initial_value: "70"  # creek_display::BRIGHTNESS_DEFAULT
  - id: disp_sleep_idx
    type: int
    restore_value: true
    initial_value: "2"  # creek_display::SLEEP_DEFAULT (2 min)
  - id: disp_page
    type: int
    initial_value: "0"
  - id: disp_pending
    type: int
    initial_value: "0"  # creek_display::Pending::NONE
  - id: disp_node_heard
    type: bool
    initial_value: "false"
  - id: disp_node_ms
    type: uint32_t
    initial_value: "0"
  - id: disp_sent
    type: creek_display::SentCache

esphome:
  on_boot:
    # Packages concatenate lists, so the base's priority-1100 I2C power-up step stays.
    - priority: -100
      then:
        - lambda: |-
            id(creek_radio).add_on_packet_callback([](const char *, int16_t) {
              id(disp_node_ms) = millis();
              id(disp_node_heard) = true;
            });

display:
  - platform: nextion
    id: disp
    uart_id: display_uart
    tft_url: ${display_tft_url}
    start_up_page: 0  # splash; the Nextion also shows it on its own at power-on
    wake_up_page: 1
    auto_wake_on_touch: true
    on_setup:
      - lambda: |-
          using namespace creek_display;
          id(disp).set_backlight_brightness(clamp_brightness(id(disp_brightness)) / 100.0f);
          id(disp).set_touch_sleep_timeout(sleep_option(id(disp_sleep_idx)).seconds);
      - delay: 3s  # the splash stays up
      - lambda: id(disp).goto_page("main");
    on_page:
      # The HMI's pages run `sendme` on entry, which is what reports navigation done inside
      # the HMI (Settings, Back, Cancel).
      - lambda: |-
          using namespace creek_display;
          id(disp_page) = x;
          if (x != PAGE_CONFIRM) id(disp_pending) = 0;
          if (x == PAGE_MAIN) {
            id(disp_sent).clear();
            id(disp_refresh_main).execute();
          } else if (x == PAGE_SETTINGS) {
            id(disp_show_settings).execute();
          }
    on_wake:
      - lambda: id(disp_sent).clear();
      - script.execute: disp_refresh_main
    on_touch:
      - lambda: |-
          using namespace creek_display;
          ESP_LOGI("display", "touch: page %u component %u %s", (unsigned) page_id,
                   (unsigned) component_id, touch_event ? "press" : "release");
          const Action a = touch_action(page_id, component_id, touch_event);
          if (a == Action::BRIGHT_DOWN || a == Action::BRIGHT_UP) {
            id(disp_brightness) = step_brightness(id(disp_brightness), a == Action::BRIGHT_UP ? 1 : -1);
            id(disp).set_backlight_brightness(id(disp_brightness) / 100.0f);
            id(disp).set_component_text("tBri", brightness_text(id(disp_brightness)).c_str());
          } else if (a == Action::SLEEP_DOWN || a == Action::SLEEP_UP) {
            id(disp_sleep_idx) = step_sleep(id(disp_sleep_idx), a == Action::SLEEP_UP ? 1 : -1);
            id(disp).set_touch_sleep_timeout(sleep_option(id(disp_sleep_idx)).seconds);
            id(disp).set_component_text("tSlp", sleep_option(id(disp_sleep_idx)).label);
          } else if (a == Action::ASK_UPDATE || a == Action::ASK_RESTART) {
            const Pending p = a == Action::ASK_UPDATE ? Pending::UPDATE : Pending::RESTART;
            id(disp_pending) = static_cast<int>(p);
            id(disp).goto_page("confirm");
            id(disp).set_component_text("tMsg", confirm_text(p));
          } else if (a == Action::CONFIRM) {
            const Pending p = to_pending(id(disp_pending));
            id(disp_pending) = 0;
            if (p == Pending::RESTART) id(disp_restart).execute();
            else if (p == Pending::UPDATE) id(disp_upload).execute();
          }

script:
  - id: disp_refresh_main
    then:
      - lambda: |-
          using namespace creek_display;
          if (!id(disp).is_setup() || id(disp).is_sleeping() || id(disp_page) != PAGE_MAIN) return;
          const uint32_t now = millis();
          MainInputs in;
          in.stage_ft = id(creek_stage).state;
          in.depth_in = id(creek_depth_in).state;
          in.ha_connected = api::global_api_server != nullptr && api::global_api_server->is_connected();
          in.tier = id(ha_alert_tier).state;
          in.rise_in_min = id(ha_rate_of_rise).state;
          creek_core::EcowittReading eco;
          uint32_t eco_ms = 0;
          in.rain_fresh = fresh(id(store).last_ecowitt(eco, eco_ms), eco_ms, now, RAIN_MAX_AGE_MS);
          in.rain_rate_in_hr = eco.rain_rate_in_hr.value_or(NAN);
          in.rain_event_in = eco.rain_event_in.value_or(NAN);
          in.rain_24h_in = eco.rain_24h_in.value_or(NAN);
          in.temp_f = eco.temp_f.value_or(NAN);
          in.node_online = id(node_online).has_state() ? (id(node_online).state ? 1 : 0) : -1;
          in.node_heard = id(disp_node_heard);
          in.node_age_ms = now - id(disp_node_ms);
          in.node_batt_mv = id(node_battery_mv).state;
          in.gw_batt_pct = id(gw_battery_level).state;
          in.sd_fault = id(store_sd_fault).has_state() ? (id(store_sd_fault).state ? 1 : 0) : -1;
          for (const auto &f : render_main(in)) {
            const Delta d = id(disp_sent).update(f);
            if (d.text) id(disp).set_component_text(f.name.c_str(), f.text.c_str());
            if (d.value) id(disp).set_component_value(f.name.c_str(), f.value);
            if (d.color) id(disp).set_component_font_color(f.name.c_str(), f.color);
          }

  - id: disp_show_settings
    then:
      - lambda: |-
          using namespace creek_display;
          id(disp).set_component_text("tBri", brightness_text(id(disp_brightness)).c_str());
          id(disp).set_component_text("tSlp", sleep_option(id(disp_sleep_idx)).label);

  - id: disp_restart
    then:
      - lambda: id(disp).set_component_text("tMsg", creek_display::working_text(creek_display::Pending::RESTART));
      - delay: 500ms  # let the message reach the display first
      - lambda: App.safe_reboot();

  - id: disp_upload
    then:
      - lambda: id(disp).set_component_text("tMsg", creek_display::working_text(creek_display::Pending::UPDATE));
      - delay: 500ms
      - script.execute: disp_upload_now

  # Blocks the main task for the whole transfer, then ESPHome reboots the gateway whether it
  # worked or not (nextion_upload.cpp).
  - id: disp_upload_now
    then:
      - lambda: |-
          ESP_LOGW("display", "TFT upload from ${display_tft_url}; the radio is not serviced "
                              "until it ends, and the gateway restarts after");
          id(disp).upload_tft(${display_upload_baud});

interval:
  - interval: 5s
    then:
      - script.execute: disp_refresh_main

# The add-on's outputs. HA only, so the display blanks them when the API is down.
sensor:
  - platform: homeassistant
    id: ha_alert_tier
    entity_id: sensor.rate_of_rise_creek_alert_tier
    internal: true
  - platform: homeassistant
    id: ha_rate_of_rise
    entity_id: sensor.rate_of_rise_creek_rate_of_rise
    internal: true

button:
  # The same upload as the screen's Update button, so a new TFT can be pushed from HA.
  - platform: template
    name: Update Display Firmware
    icon: mdi:monitor-arrow-down
    entity_category: config
    on_press:
      - script.execute: disp_upload_now
```

- [ ] **Step 4: Add the package to the wrappers.**
  - `gateway.yaml`: `packages:` gains `display: !include display.yaml`, and the local
    `components` source becomes `components: [creek_store, creek_display]`.
  - `creek-gateway-v2.yaml` and `creek-gateway-v2.prod.yaml`: `packages:` gain

```yaml
  display:
    url: https://github.com/ryanbuiltthat/rate-of-rise
    ref: main
    files: [firmware/esp32s3_feather_gateway/display.yaml]
    refresh: 0s
```

    and their `esp32s3_feather_gateway/components` source becomes
    `components: [creek_store, creek_display]`. In both header comments, add "and the Nextion
    display (display.yaml)".

- [ ] **Step 5: Run the contract test and confirm it passes**

Run: `python rate_of_rise/tests/test_gateway_v2_display.py`
Expected: `3 passed`.

- [ ] **Step 6: Add display.yaml to CI's YAML well-formedness list** in `.github/workflows/tests.yml`, after `ota-push.yaml`:

```python
                    "firmware/esp32s3_feather_gateway/ota-push.yaml",
                    "firmware/esp32s3_feather_gateway/display.yaml"]:
```

- [ ] **Step 7: Compile**

Run from the repo root: `python -m esphome compile firmware/esp32s3_feather_gateway/gateway.yaml`.
- Expected: `SUCCESS`.
- If the link fails on scons, follow the memory note: remove
  `~/.platformio/packages/tool-scons` and rerun.
- Then confirm the header reached the build:
  `grep -c creek_display firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/src/esphome.h`.
  Expected: ≥ 1.

- [ ] **Step 8: Commit**

```bash
git add firmware/esp32s3_feather_gateway/display.yaml firmware/esp32s3_feather_gateway/gateway.yaml \
        firmware/esp32s3_feather_gateway/creek-gateway-v2.yaml firmware/esp32s3_feather_gateway/creek-gateway-v2.prod.yaml \
        rate_of_rise/tests/test_gateway_v2_display.py .github/workflows/tests.yml
git commit -m "gateway v2: Nextion display package (splash, main, settings, confirm) in every v2 wrapper"
```

---

### Task 6: TFT-export guard, HMI contract README, docs

**Files:**
- Modify: `.github/workflows/tests.yml` (firmware job: checkout `fetch-depth: 0`, new step)
- Create: `firmware/nextion_display/README.md`
- Modify: `docs/gateway-v2-trial.md` (pointer)
- Modify: the spec (`txt_maxl` row in Global HMI settings)

- [ ] **Step 1: Write the guard as a script, and check it against this repo's state**

The guard goes in `.github/workflows/tests.yml`, firmware job:
- The checkout gains `with: { fetch-depth: 0 }`.
- A new step after "display_core host tests":

```yaml
      - name: Nextion TFT is exported from the latest HMI
        # The display runs the .tft, not the .HMI, and only the Nextion Editor can make one
        # from the other. A commit that changes the HMI without a fresh export would ship a
        # display that silently lacks the change, so the last commit touching the .tft must
        # include (or come after) the last one touching the .HMI.
        run: |
          hmi=firmware/nextion_display/creek_gateway_v2_display.HMI
          tft=firmware/nextion_display/creek_gateway_v2_display.tft
          h=$(git log -1 --format=%H -- "$hmi")
          [ -z "$h" ] && { echo "no HMI committed yet"; exit 0; }
          t=$(git log -1 --format=%H -- "$tft")
          if [ -z "$t" ] || ! git merge-base --is-ancestor "$h" "$t"; then
            echo "::error file=$tft::$hmi changed after the last TFT export; export it in the Nextion Editor and commit both"
            exit 1
          fi
          echo "TFT export is current"
```

Run the body locally from bash at the repo root.
Expected: `no HMI committed yet` (the HMI is still untracked).

- [ ] **Step 2: Write `firmware/nextion_display/README.md`.** It holds:
  - the global HMI settings (as in the spec, plus `txt_maxl`: 20 for every text component
    except `tMsg`, which is 200);
  - the three page tables (creation order, names, geometry, font, events);
  - the colour table;
  - the wiring;
  - "Build and export":
    - File > Export TFT to `creek_gateway_v2_display.tft` next to the HMI;
    - commit both; CI's guard enforces it;
    - merge to `main`;
    - press **Update Display Firmware** in HA (or on the screen).
  - "First load": the factory demo TFT accepts ESPHome's upload, so the HA button works from
    the start. A microSD in the display's slot is the fallback.
  - The bring-up checklist from spec Testing §3–4.

- [ ] **Step 3: Update the spec:** add the `txt_maxl` bullet to "Global HMI settings". Link the
  README from `docs/gateway-v2-trial.md` with a short "Display" section.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/tests.yml firmware/nextion_display/README.md docs/gateway-v2-trial.md \
        docs/superpowers/specs/2026-10-10-gateway-v2-nextion-display-design.md
git commit -m "display: HMI contract README, TFT-export guard in CI, trial doc pointer"
```

---

### Task 7 (Ryan, in the Nextion Editor): build the HMI, simulate, export

**Files:** `firmware/nextion_display/creek_gateway_v2_display.HMI`, `.tft`, `assets/*.png`

- [ ] Work through the README checklist in order:
  - device and orientation, `program.s`;
  - pictures (startup = 0, bg = 1);
  - fonts 0/1/2 (iso-8859-1);
  - pages 0–3 with `sendme` in each Postinitialize Event;
  - components in table order with the listed names, `txt_maxl` and events.
- [ ] Debug/simulate:
  - every page renders over its background;
  - Settings / Back / Cancel navigate;
  - the simulator's return-data pane shows `65 02 04 00 FF FF FF` for `bBriDn` on release,
    and likewise `65 02 06/08/0A/0B/0C` and `65 03 03`.
  - If an id differs, fix the creation order, or change the constant in `display_core.h`
    and its test.
- [ ] File > Export TFT as `creek_gateway_v2_display.tft` next to the HMI.
- [ ] Commit the HMI, the TFT and `assets/` together.

### Task 8 (Ryan + Claude, at the bench): wire, flash, check

- [ ] Wire per the README (the U1V11F5 from BAT, SHDN to VIN, TX39 → RX, RX38 ← TX).
- [ ] Merge the branch to `main`.
- [ ] Copy the updated `creek-gateway-v2.yaml` to `/config/esphome/`: diff against the live copy
  first and back it up. Then install from the Device Builder.
- [ ] Load the **first** TFT by microSD or by Editor upload. A new panel talks at 9600 baud, so
  the gateway's 115200 upload cannot reach it (README "First load"). Use **Update Display
  Firmware** in HA for every later TFT.
- [ ] Run the README bring-up checklist. Record:
  - TFT transfer time at 115200, then at 921600 (change the `display_upload_baud` default if
    921600 is clean);
  - pack current awake and asleep;
  - whether the pack still charges on USB with the display awake.
- [ ] Revisit the 2 min sleep default against the measured current; note the results in the
  README.
