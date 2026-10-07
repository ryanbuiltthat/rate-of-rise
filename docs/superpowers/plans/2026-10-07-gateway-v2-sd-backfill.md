# Gateway v2 SD Store-and-Forward with History Backfill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A second creek gateway (Feather ESP32-S3 + RFM69 + Adalogger) that logs every node packet and Ecowitt reading to SD, and an add-on backfill that writes anything HA missed into HA's recorder history, long-term statistics, the stage log and the training dataset.

**Architecture:** The v2 firmware reuses v1's `rfm69_gateway` component through a compile-time-gated packet hook, and adds a `creek_store` component (RTC clock, SD log, Ecowitt poller, replay HTTP API). The add-on gains an `app/backfill/` package that feature-detects the store, pulls records by sequence number, and reconciles them against what HA actually recorded. It is off unless `gateway_store_url` is set.

**Tech Stack:** ESPHome 2026.7.3 (Arduino framework, `toolchain: platformio`), LowPowerLab RFM69 1.6.0, Arduino `SD`/`FS`, PCF8523 over I2C; Python 3.11 add-on with `requests`, `pandas`, `sqlite3`, `websocket-client`.

**Spec:** [docs/superpowers/specs/2026-10-07-gateway-v2-sd-backfill-design.md](../specs/2026-10-07-gateway-v2-sd-backfill-design.md)

## Global Constraints

- v1 (`firmware/esp32_rfm69_gateway/`) YAML and compiled firmware must not change. All component changes are inside `#ifdef USE_RFM69_PACKET_HOOK`, which only `creek_store`'s codegen defines.
- Node and gateway radio settings are unchanged: 915 MHz, network 100, gateway node id 2, same `rfm69_encrypt_key`.
- v2 device name `creek-gateway-v2`, friendly name `Creek Gateway v2`, permanently (cutover renames HA entity IDs, not the device).
- `api: reboot_timeout: 0s` and `wifi: reboot_timeout: 0s` on v2.
- Store API: bearer token on every request; `store_schema` is `1`.
- Records are NDJSON lines starting with `{"seq":`. **Refinement of the spec:** files are named by sequence block (`/node/000001.ndjson` holds seq 10000–19999), not by UTC day. A record with no trustworthy time would otherwise land in a `1970-01-01` file and break seq ordering across files.
- **Refinement of the spec:** a records page is built in a bounded buffer (≤ 32 KB) rather than streamed line by line, and `/store/status` omits `fw`.
- **Refinements of the spec, found while planning** (each is deliberate; do not "fix" back):
  - The status entity is `sensor.rate_of_rise_creek_backfill_status`. The add-on's discovery names entities `<device> <name>` (see `DiscoveryPublisher.entity_ids`), not the spec's `sensor.creek_backfill_status`.
  - The SD health entity is `Store SD Fault` (device_class problem, on = fault), not "SD OK".
  - The PCF8523 is rewritten on **every** SNTP sync (ESPHome fires the sync callback each `update_interval`), not every 6 h.
  - `unavailable` rows are removed within ±120 s of the first and last inserted row for that entity, a concrete form of the spec's "inside a gap it fills".
  - Backfilled states follow HA's own compression: a reading equal to the state already in effect adds no row.
  - Gap rows include "blind" rows: rows the add-on wrote while HA Core alone was down, re-issued with the same `ts` so the dataset keeps the corrected one.
  - Statistics import is limited to measurement statistics; HA's live recorder showed the rain total is a `has_sum` statistic, whose running sum can't be re-imported for one hour.
  - Node packets backfill these fields: `stage_ft`, `depth_in`, `distance_mm`, `battery_mv`, `rssi_dbm`, `node_status`, `fast`, `diag_active`, `reset_cause`, `cycle`, `radio_init_failures`. The packet counter and radar-fault entities are gateway-derived and not backfilled.
- Add-on backfill is off when `gateway_store_url` is blank: no thread, no requests, no files.
- Against a v1 gateway, an unreachable gateway, or no gateway, nothing is logged above DEBUG. The one exception is a 401 (bad token), at most one WARNING per hour.
- Recorder writes only on schema versions in `SUPPORTED_SCHEMAS = {53}`.
- Recorder states are written in the entity's **current display unit** (from its stored `unit_of_measurement`), not the record's native unit. Live DB evidence: distance is stored in `in`, battery in `V`.
- Every inserted recorder row has `context_id_bin` starting `CB 0F 11 ED`.
- Statistics are imported only for `mean_type == 1` (measurement) entities; `has_sum` entities are skipped.
- Backfilled data never reaches tiers, notifications or MQTT feature publishes. The only live effect is the corrected rain accumulator / API index.
- Add-on tests are standalone scripts in `rate_of_rise/tests/test_*.py` with a `main()` that runs every `test_*` function, run as `python rate_of_rise/tests/test_x.py`.
- Add-on version becomes `0.26.0` with a `## 0.26.0` CHANGELOG section.
- Local builds: `pio run` (node) and `esphome compile` fight over `~/.platformio/packages/tool-scons`. If an esphome compile fails at link after a node build, delete that directory and rerun.

## Review Focus

1. **Gateway booted with a dead RTC battery and no network** — records carry `ts_src: none`. Expected: never written anywhere, counted in `skipped_no_time`, and the cursor still moves past them. Test: Task 19 `test_records_without_time_are_skipped_but_consumed`.
2. **The user changed an entity's display unit in HA** (distance in inches, battery in volts, or a unit the code doesn't know). Expected: converted correctly, or that entity skipped with a reason and the rest of the batch written. Tests: Task 12 `test_converts_to_the_entity_display_unit`, `test_unknown_unit_skips_entity_without_raising`.
3. **HA already has the reading, stamped slightly differently** (API latency, RTC drift of a few seconds). Expected: no duplicate row. Test: Task 12 `test_existing_row_within_tolerance_is_not_duplicated`.
4. **The SD card is replaced or reformatted, so the gateway's seq restarts below the add-on's cursor.** Expected: one WARNING, cursor reset to 0, everything re-read and written idempotently, no infinite wait. Test: Task 19 `test_store_restart_resets_cursor`.
5. **HA's database is locked or the insert fails mid-batch.** Expected: that pass fails, the cursor doesn't move, other destinations' work is redone harmlessly next pass. Test: Task 19 `test_failed_destination_keeps_cursor`.

---

## File map

**Firmware (new unless noted)**
- `firmware/esp32_rfm69_gateway/components/rfm69_gateway/rfm69_gateway.h` (modify) — gated packet hook + bus-mutex getter.
- `firmware/esp32s3_feather_gateway/components/creek_store/store_core.h` — pure C++17: stage math, record encoding, seq/tail parsing, block naming, Ecowitt value parsing, civil time. Host-testable.
- `firmware/esp32s3_feather_gateway/components/creek_store/creek_store.h` — the ESPHome component.
- `firmware/esp32s3_feather_gateway/components/creek_store/__init__.py` — schema + codegen.
- `firmware/esp32s3_feather_gateway/tests/test_store_core.cpp` — host tests.
- `firmware/esp32s3_feather_gateway/gateway.base.yaml`, `ota-push.yaml`, `gateway.yaml`, `creek-gateway-v2.yaml`, `creek-gateway-v2.prod.yaml`, `secrets.yaml.example`, `.gitignore`.
- `.github/workflows/tests.yml` (modify) — `firmware` job.
- `firmware/README.md` (modify) — v2 section.

**Add-on (`rate_of_rise/`)**
- `app/backfill/__init__.py` — `build_backfill()`, `recorder_db_path()`.
- `app/backfill/client.py` — `StoreClient`, `Probe`, `ProbeState`.
- `app/backfill/units.py` — `convert()`, `format_state()`, `UnitMismatch`.
- `app/backfill/entity_map.py` — field registry, `parse_map()`, `points_for()`, `Point`.
- `app/backfill/recorder.py` — `RecorderWriter`, `schema_version()`, `SchemaUnsupported`, `MARKER`.
- `app/backfill/undo.py` — `undo()`.
- `app/backfill/statistics.py` — `hourly_stats()`, `StatisticsWriter`, `HAWebsocket`.
- `app/backfill/stagelog_merge.py` — `merge_stage_rows()`.
- `app/backfill/gaprows.py` — `missing_slots()`, `eco_increments()`, `GapFiller`.
- `app/backfill/reconciler.py` — `Reconciler`, `BackfillService`.
- Modify: `app/config.py`, `config.yaml`, `requirements.txt`, `app/stagelog.py`, `app/dataset.py`, `app/sources/accumulator.py`, `app/sources/apindex.py`, `app/sources/rain.py`, `app/sources/__init__.py`, `app/sources/usgs.py`, `app/sources/alerts.py`, `app/sources/radar_cells.py`, `app/sources/wu.py`, `app/sources/snodas.py`, `app/commands.py`, `app/discovery.py`, `app/__main__.py`, `CHANGELOG.md`, `DOCS.md`.
- Tests: `tests/fixtures/recorder_schema_53.sql`, and `tests/test_backfill_*.py` per module, plus updates to `tests/test_discovery.py`, `tests/test_commands.py`, and the source tests.

**Repo tools**
- `tools/gateway_cutover.py`, `tools/test_gateway_cutover.py`.
- `docs/gateway-v2-trial.md` — trial and cutover runbook (the acceptance checks).

---

## Task 1: `store_core.h` and its host tests

**Files:**
- Create: `firmware/esp32s3_feather_gateway/components/creek_store/store_core.h`
- Create: `firmware/esp32s3_feather_gateway/tests/test_store_core.cpp`

**Interfaces:**
- Produces (namespace `creek_core`, header-only):
  - `struct StageResult { std::optional<float> stage_ft, depth_in; bool clamped; bool lost_target; }`
  - `StageResult stage_from_distance(float mount_mm, std::optional<float> distance_mm, float blanking_mm, float overrange_slack_mm)`
  - `struct NodeFields { std::optional<float> distance_mm, battery_mv; std::optional<int> fast, diag, reset_cause, cycle, init_failures; }`
  - `std::string encode_node_record(uint32_t seq, double ts, const char *ts_src, int rssi, const NodeFields &, float mount_mm, const StageResult &)`
  - `struct EcowittReading { std::optional<float> rain_event_in, rain_rate_in_hr, rain_day_in, rain_24h_in, rain_year_in, temp_f; std::vector<std::pair<int,float>> soil; }`
  - `void apply_ecowitt_item(EcowittReading &, const char *id, const char *val, const char *unit)`
  - `std::string encode_ecowitt_record(uint32_t seq, double ts, const char *ts_src, const EcowittReading &)`
  - `std::optional<float> leading_float(const char *)`
  - `std::optional<uint32_t> parse_seq(const char *line)`
  - `std::optional<uint32_t> last_seq_in_tail(const std::string &tail)`, `bool tail_is_torn(const std::string &tail)`
  - `constexpr uint32_t BLOCK = 10000;` `uint32_t block_of(uint32_t seq)`, `std::string block_path(const char *stream, uint32_t block)`, `std::optional<uint32_t> parse_block_name(const char *name)`
  - `struct Civil { int year; unsigned month, day, hour, minute, second, weekday; }`, `int64_t epoch_from_civil(const Civil &)`, `Civil civil_from_epoch(int64_t)`, `uint8_t bcd2bin(uint8_t)`, `uint8_t bin2bcd(uint8_t)`

- [ ] **Step 1: Write the failing test**

`firmware/esp32s3_feather_gateway/tests/test_store_core.cpp`:

```cpp
// Host tests for creek_store's pure logic. Build and run from the repo root (one command):
//   g++ -std=c++17 -Wall -Wextra -Werror -I firmware/esp32s3_feather_gateway/components/creek_store
//       firmware/esp32s3_feather_gateway/tests/test_store_core.cpp -o test_store_core
//   ./test_store_core
// No trailing backslashes in this comment: -Werror=comment rejects a line-continued comment.
#include "store_core.h"

#include <cmath>
#include <cstdio>
#include <string>

using namespace creek_core;

static int failures = 0;
#define CHECK(cond)                                                          \
  do {                                                                       \
    if (!(cond)) {                                                           \
      std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);            \
      failures++;                                                            \
    }                                                                        \
  } while (0)

static bool near(float a, float b, float eps = 1e-3f) { return std::fabs(a - b) < eps; }

// Pinned against v1's `distance` on_value lambda (esp32_rfm69_gateway/gateway.base.yaml) at
// mount 1105 mm, blanking 150 mm, over-range slack 1000 mm.
static void test_stage_matches_v1_lambda() {
  auto r = stage_from_distance(1105, 812.0f, 150, 1000);
  CHECK(r.stage_ft && near(*r.stage_ft, 0.961286f));
  CHECK(r.depth_in && near(*r.depth_in, 11.5354f));
  CHECK(!r.clamped && !r.lost_target);

  r = stage_from_distance(1105, 100.0f, 150, 1000);  // inside blanking: clamp to the ceiling
  CHECK(r.clamped && r.stage_ft && near(*r.stage_ft, 3.133202f) && near(*r.depth_in, 37.5984f));

  r = stage_from_distance(1105, 2200.0f, 150, 1000);  // past mount + slack: lost target
  CHECK(r.lost_target && !r.stage_ft && !r.depth_in);

  r = stage_from_distance(1105, 2105.0f, 150, 1000);  // exactly at the slack edge: below datum
  CHECK(r.stage_ft && near(*r.stage_ft, -3.28084f));

  r = stage_from_distance(1105, std::nullopt, 150, 1000);
  CHECK(!r.stage_ft && !r.depth_in);
  r = stage_from_distance(NAN, 812.0f, 150, 1000);
  CHECK(!r.stage_ft && !r.depth_in);
}

static void test_node_record_encoding() {
  NodeFields f;
  f.distance_mm = 812;
  f.battery_mv = 4012;
  f.fast = 0;
  f.diag = 0;
  f.reset_cause = 1;
  f.cycle = 143;
  f.init_failures = 0;
  const auto s = stage_from_distance(1105, f.distance_mm, 150, 1000);
  CHECK(encode_node_record(18234, 1791378600.4, "ntp", -72, f, 1105, s) ==
        "{\"seq\":18234,\"ts\":1791378600.4,\"ts_src\":\"ntp\",\"rssi\":-72,\"d\":812,"
        "\"v\":4012,\"f\":0,\"g\":0,\"r\":1,\"n\":143,\"i\":0,\"mount\":1105,"
        "\"stage_ft\":0.9613,\"depth_in\":11.54}\n");

  NodeFields bare;  // failed radar read, long-form packet with no diagnostics
  bare.battery_mv = 4012;
  const auto none = stage_from_distance(1105, bare.distance_mm, 150, 1000);
  CHECK(encode_node_record(7, 10.0, "rtc", -80, bare, 1105, none) ==
        "{\"seq\":7,\"ts\":10.0,\"ts_src\":\"rtc\",\"rssi\":-80,\"d\":null,\"v\":4012,"
        "\"mount\":1105,\"stage_ft\":null,\"depth_in\":null}\n");
}

static void test_ecowitt_items_and_encoding() {
  EcowittReading e;
  // Verbatim from the GW3000B's /get_livedata_info on 2026-10-07.
  apply_ecowitt_item(e, "0x02", "45.9", "F");
  apply_ecowitt_item(e, "0x07", "96%", "");          // humidity: not recorded
  apply_ecowitt_item(e, "0x0D", "0.00 in", "");
  apply_ecowitt_item(e, "0x0E", "0.00 in/Hr", "");
  apply_ecowitt_item(e, "0x10", "0.00 in", "");
  apply_ecowitt_item(e, "0x7C", "0.00 in", "");
  apply_ecowitt_item(e, "0x13", "29.84 in", "");
  e.soil.emplace_back(2, 61.0f);
  CHECK(encode_ecowitt_record(5521, 1791378600.1, "ntp", e) ==
        "{\"seq\":5521,\"ts\":1791378600.1,\"ts_src\":\"ntp\",\"rain_event_in\":0.000,"
        "\"rain_rate_in_hr\":0.000,\"rain_day_in\":0.000,\"rain_24h_in\":0.000,"
        "\"rain_year_in\":29.840,\"temp_f\":45.9,\"soil\":{\"2\":61}}\n");

  EcowittReading metric;  // a console set to metric units
  apply_ecowitt_item(metric, "0x13", "25.4 mm", "");
  apply_ecowitt_item(metric, "0x0E", "2.54 mm/Hr", "");
  apply_ecowitt_item(metric, "0x02", "7.0", "C");
  CHECK(metric.rain_year_in && near(*metric.rain_year_in, 1.0f));
  CHECK(metric.rain_rate_in_hr && near(*metric.rain_rate_in_hr, 0.1f));
  CHECK(metric.temp_f && near(*metric.temp_f, 44.6f));

  EcowittReading empty;
  CHECK(encode_ecowitt_record(1, 2.0, "none", empty) ==
        "{\"seq\":1,\"ts\":2.0,\"ts_src\":\"none\",\"rain_event_in\":null,"
        "\"rain_rate_in_hr\":null,\"rain_day_in\":null,\"rain_24h_in\":null,"
        "\"rain_year_in\":null,\"temp_f\":null,\"soil\":{}}\n");
}

static void test_leading_float() {
  CHECK(leading_float("0.00 in") && *leading_float("0.00 in") == 0.0f);
  CHECK(leading_float("61%") && *leading_float("61%") == 61.0f);
  CHECK(!leading_float("") && !leading_float("--") && !leading_float(nullptr));
}

static void test_parse_seq_and_tail() {
  CHECK(parse_seq("{\"seq\":42,\"ts\":1.0}") == std::optional<uint32_t>(42));
  CHECK(parse_seq("{\"seq\":42,\"ts\":1.0}\r") == std::optional<uint32_t>(42));
  CHECK(!parse_seq("{\"seq\":42,\"ts\":1.0"));  // torn
  CHECK(!parse_seq("\"seq\":42}"));
  CHECK(!parse_seq(""));

  const std::string tail = "s\":1.0}\n{\"seq\":7,\"ts\":2.0}\n{\"seq\":8,\"ts\":3";
  CHECK(last_seq_in_tail(tail) == std::optional<uint32_t>(7));
  CHECK(tail_is_torn(tail));
  const std::string clean = "{\"seq\":9,\"ts\":2.0}\n";
  CHECK(last_seq_in_tail(clean) == std::optional<uint32_t>(9));
  CHECK(!tail_is_torn(clean));
  CHECK(!last_seq_in_tail(""));
  CHECK(!tail_is_torn(""));
}

static void test_blocks() {
  CHECK(block_of(1) == 0 && block_of(9999) == 0 && block_of(10000) == 1);
  CHECK(block_path("node", 12) == "/node/000012.ndjson");
  CHECK(parse_block_name("000012.ndjson") == std::optional<uint32_t>(12));
  CHECK(parse_block_name("/node/000012.ndjson") == std::optional<uint32_t>(12));
  CHECK(!parse_block_name("notes.txt") && !parse_block_name("12.ndjson.bak"));
}

static void test_civil_time() {
  const struct {
    int64_t epoch;
    Civil c;
  } cases[] = {
      {0, {1970, 1, 1, 0, 0, 0, 4}},
      {951782400, {2000, 2, 29, 0, 0, 0, 2}},
      {1709208000, {2024, 2, 29, 12, 0, 0, 4}},
      {1791378600, {2026, 10, 7, 13, 10, 0, 3}},
  };
  for (const auto &k : cases) {
    const Civil c = civil_from_epoch(k.epoch);
    CHECK(c.year == k.c.year && c.month == k.c.month && c.day == k.c.day);
    CHECK(c.hour == k.c.hour && c.minute == k.c.minute && c.second == k.c.second);
    CHECK(c.weekday == k.c.weekday);
    CHECK(epoch_from_civil(k.c) == k.epoch);
  }
  CHECK(bcd2bin(0x59) == 59 && bin2bcd(59) == 0x59 && bin2bcd(7) == 0x07);
}

int main() {
  test_stage_matches_v1_lambda();
  test_node_record_encoding();
  test_ecowitt_items_and_encoding();
  test_leading_float();
  test_parse_seq_and_tail();
  test_blocks();
  test_civil_time();
  if (failures) {
    std::printf("%d failure(s)\n", failures);
    return 1;
  }
  std::printf("all store_core tests passed\n");
  return 0;
}
```

- [ ] **Step 2: Run it to verify it fails**

Run (Git Bash, repo root):
```bash
g++ -std=c++17 -Wall -Wextra -Werror -I firmware/esp32s3_feather_gateway/components/creek_store firmware/esp32s3_feather_gateway/tests/test_store_core.cpp -o /tmp/test_store_core && /tmp/test_store_core
```
Expected: compile error `store_core.h: No such file or directory`.

- [ ] **Step 3: Write `store_core.h`**

```cpp
// Pure logic for the creek_store component: no Arduino, no ESPHome, so it builds and is
// tested on the host (firmware/esp32s3_feather_gateway/tests/test_store_core.cpp).
//
// Namespace `creek_core`, not `creek_store`: ESPHome lambdas are compiled with
// `using namespace esphome;`, where `creek_store` would be ambiguous with
// esphome::creek_store.
#pragma once

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace creek_core {

// --- Stage -------------------------------------------------------------------------------
// The same rules as v1's `distance` on_value lambda (esp32_rfm69_gateway/gateway.base.yaml),
// which explains why each branch exists. v2's lambda calls this, and so does the store, so
// the stage on HA and the stage on SD cannot drift apart.
struct StageResult {
  std::optional<float> stage_ft;
  std::optional<float> depth_in;
  bool clamped{false};      // inside the blanking zone: depth clamped to the ceiling
  bool lost_target{false};  // farther than mount + slack: nothing reflected
};

inline StageResult stage_from_distance(float mount_mm, std::optional<float> distance_mm,
                                       float blanking_mm, float overrange_slack_mm) {
  StageResult r;
  if (std::isnan(mount_mm) || !distance_mm || std::isnan(*distance_mm)) return r;
  const float d = *distance_mm;
  if (d > mount_mm + overrange_slack_mm) {
    r.lost_target = true;
    return r;
  }
  float depth_mm = mount_mm - d;
  if (d < blanking_mm) {
    depth_mm = mount_mm - blanking_mm;
    r.clamped = true;
  }
  r.stage_ft = depth_mm / 304.8f;
  r.depth_in = depth_mm / 25.4f;
  return r;
}

// --- JSON fragments ----------------------------------------------------------------------
inline void append_num(std::string &out, const char *key, std::optional<float> v,
                       int decimals) {
  out += ",\"";
  out += key;
  out += "\":";
  if (!v || std::isnan(*v)) {
    out += "null";
    return;
  }
  char buf[32];
  std::snprintf(buf, sizeof buf, "%.*f", decimals, (double) *v);
  out += buf;
}

// Absent on the wire stays absent on the card: an older node build that never sent the key
// is not the same as one that sent zero.
inline void append_int(std::string &out, const char *key, std::optional<int> v) {
  if (!v) return;
  char buf[40];
  std::snprintf(buf, sizeof buf, ",\"%s\":%d", key, *v);
  out += buf;
}

inline std::string record_head(uint32_t seq, double ts, const char *ts_src) {
  char buf[96];
  std::snprintf(buf, sizeof buf, "{\"seq\":%u,\"ts\":%.1f,\"ts_src\":\"%s\"", (unsigned) seq, ts,
                ts_src);
  return buf;
}

// --- Node records ------------------------------------------------------------------------
struct NodeFields {
  std::optional<float> distance_mm;  // nullopt: null on the wire (failed radar read)
  std::optional<float> battery_mv;
  std::optional<int> fast, diag, reset_cause, cycle, init_failures;
};

inline std::string encode_node_record(uint32_t seq, double ts, const char *ts_src, int rssi,
                                      const NodeFields &f, float mount_mm, const StageResult &s) {
  std::string out = record_head(seq, ts, ts_src);
  char buf[24];
  std::snprintf(buf, sizeof buf, ",\"rssi\":%d", rssi);
  out += buf;
  append_num(out, "d", f.distance_mm, 0);
  if (f.battery_mv) append_num(out, "v", f.battery_mv, 0);
  append_int(out, "f", f.fast);
  append_int(out, "g", f.diag);
  append_int(out, "r", f.reset_cause);
  append_int(out, "n", f.cycle);
  append_int(out, "i", f.init_failures);
  append_num(out, "mount", mount_mm, 0);
  append_num(out, "stage_ft", s.stage_ft, 4);
  append_num(out, "depth_in", s.depth_in, 2);
  out += "}\n";
  return out;
}

// --- Ecowitt -----------------------------------------------------------------------------
struct EcowittReading {
  std::optional<float> rain_event_in, rain_rate_in_hr, rain_day_in, rain_24h_in, rain_year_in;
  std::optional<float> temp_f;
  std::vector<std::pair<int, float>> soil;  // (channel, humidity %)
};

inline std::optional<float> leading_float(const char *s) {
  if (s == nullptr) return std::nullopt;
  char *end = nullptr;
  const float v = std::strtof(s, &end);
  if (end == s) return std::nullopt;
  return v;
}

// The unit is either the item's own "unit" field ("F") or the text after the number
// ("0.00 in", "0.00 in/Hr").
inline std::string unit_of(const char *val, const char *unit) {
  if (unit != nullptr && unit[0] != '\0') return unit;
  if (val == nullptr) return "";
  char *end = nullptr;
  std::strtof(val, &end);
  while (*end == ' ') end++;
  return end;
}

inline std::optional<float> to_inches(std::optional<float> v, const std::string &unit) {
  if (!v) return v;
  if (unit == "in" || unit == "in/Hr" || unit.empty()) return v;
  if (unit == "mm" || unit == "mm/Hr") return *v / 25.4f;
  return std::nullopt;  // a unit we do not know is not a number we can trust
}

inline std::optional<float> to_fahrenheit(std::optional<float> v, const std::string &unit) {
  if (!v) return v;
  if (unit == "F" || unit == "\xC2\xB0" "F" || unit.empty()) return v;
  if (unit == "C" || unit == "\xC2\xB0" "C") return *v * 9.0f / 5.0f + 32.0f;
  return std::nullopt;
}

// Ids from the GW3000B's local API (common_list / rain arrays). 0x13 is the yearly total,
// which is what HA's Ecowitt "rain total" entity tracks (29.84 in on both, 2026-10-07).
inline void apply_ecowitt_item(EcowittReading &e, const char *id, const char *val,
                               const char *unit) {
  if (id == nullptr) return;
  const std::string u = unit_of(val, unit);
  const auto v = leading_float(val);
  if (std::strcmp(id, "0x02") == 0) e.temp_f = to_fahrenheit(v, u);
  else if (std::strcmp(id, "0x0D") == 0) e.rain_event_in = to_inches(v, u);
  else if (std::strcmp(id, "0x0E") == 0) e.rain_rate_in_hr = to_inches(v, u);
  else if (std::strcmp(id, "0x10") == 0) e.rain_day_in = to_inches(v, u);
  else if (std::strcmp(id, "0x7C") == 0) e.rain_24h_in = to_inches(v, u);
  else if (std::strcmp(id, "0x13") == 0) e.rain_year_in = to_inches(v, u);
}

inline std::string encode_ecowitt_record(uint32_t seq, double ts, const char *ts_src,
                                         const EcowittReading &e) {
  std::string out = record_head(seq, ts, ts_src);
  append_num(out, "rain_event_in", e.rain_event_in, 3);
  append_num(out, "rain_rate_in_hr", e.rain_rate_in_hr, 3);
  append_num(out, "rain_day_in", e.rain_day_in, 3);
  append_num(out, "rain_24h_in", e.rain_24h_in, 3);
  append_num(out, "rain_year_in", e.rain_year_in, 3);
  append_num(out, "temp_f", e.temp_f, 1);
  out += ",\"soil\":{";
  for (size_t i = 0; i < e.soil.size(); i++) {
    char buf[32];
    std::snprintf(buf, sizeof buf, "%s\"%d\":%.0f", i ? "," : "", e.soil[i].first,
                  (double) e.soil[i].second);
    out += buf;
  }
  out += "}}\n";
  return out;
}

// --- Reading the card back ---------------------------------------------------------------
// Every record line starts with {"seq": and ends with }, so a line that does not is torn
// (power lost mid-write) and is skipped by every reader.
inline std::optional<uint32_t> parse_seq(const char *line) {
  if (line == nullptr) return std::nullopt;
  size_t n = std::strlen(line);
  while (n && (line[n - 1] == '\n' || line[n - 1] == '\r')) n--;
  static const char PREFIX[] = "{\"seq\":";
  const size_t p = sizeof(PREFIX) - 1;
  if (n <= p || line[n - 1] != '}' || std::strncmp(line, PREFIX, p) != 0) return std::nullopt;
  char *end = nullptr;
  const unsigned long v = std::strtoul(line + p, &end, 10);
  if (end == line + p) return std::nullopt;
  return (uint32_t) v;
}

inline bool tail_is_torn(const std::string &tail) { return !tail.empty() && tail.back() != '\n'; }

inline std::optional<uint32_t> last_seq_in_tail(const std::string &tail) {
  std::optional<uint32_t> last;
  size_t start = 0;
  while (start < tail.size()) {
    const size_t nl = tail.find('\n', start);
    if (nl == std::string::npos) break;  // trailing text with no newline: torn
    const auto seq = parse_seq(tail.substr(start, nl - start).c_str());
    if (seq) last = seq;
    start = nl + 1;
  }
  return last;
}

// --- Files -------------------------------------------------------------------------------
// Files hold fixed seq blocks rather than days: a record's place on the card must not depend
// on the clock, which is exactly the thing that can be wrong (ts_src "none").
constexpr uint32_t BLOCK = 10000;

inline uint32_t block_of(uint32_t seq) { return seq / BLOCK; }

inline std::string block_path(const char *stream, uint32_t block) {
  char buf[48];
  std::snprintf(buf, sizeof buf, "/%s/%06u.ndjson", stream, (unsigned) block);
  return buf;
}

inline std::optional<uint32_t> parse_block_name(const char *name) {
  if (name == nullptr) return std::nullopt;
  const char *base = std::strrchr(name, '/');
  base = base ? base + 1 : name;
  if (std::strlen(base) != 13 || std::strcmp(base + 6, ".ndjson") != 0) return std::nullopt;
  uint32_t v = 0;
  for (int i = 0; i < 6; i++) {
    if (base[i] < '0' || base[i] > '9') return std::nullopt;
    v = v * 10 + (uint32_t) (base[i] - '0');
  }
  return v;
}

// --- Civil time (PCF8523 registers are calendar fields, the system clock is epoch) --------
struct Civil {
  int year;
  unsigned month, day, hour, minute, second, weekday;  // weekday 0 = Sunday
};

// Howard Hinnant's days_from_civil / civil_from_days.
inline int64_t days_from_civil(int y, unsigned m, unsigned d) {
  y -= m <= 2;
  const int64_t era = (y >= 0 ? y : y - 399) / 400;
  const unsigned yoe = (unsigned) (y - era * 400);
  const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
  const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return era * 146097 + (int64_t) doe - 719468;
}

inline int64_t epoch_from_civil(const Civil &c) {
  return days_from_civil(c.year, c.month, c.day) * 86400 + c.hour * 3600 + c.minute * 60 +
         c.second;
}

inline Civil civil_from_epoch(int64_t epoch) {
  int64_t days = epoch / 86400;
  int64_t secs = epoch % 86400;
  if (secs < 0) {
    secs += 86400;
    days -= 1;
  }
  Civil c{};
  c.weekday = (unsigned) ((days % 7 + 11) % 7);  // 1970-01-01 was a Thursday (4)
  const int64_t z = days + 719468;
  const int64_t era = (z >= 0 ? z : z - 146096) / 146097;
  const unsigned doe = (unsigned) (z - era * 146097);
  const unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  const unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  const unsigned mp = (5 * doy + 2) / 153;
  c.day = doy - (153 * mp + 2) / 5 + 1;
  c.month = mp < 10 ? mp + 3 : mp - 9;
  c.year = (int) (yoe + era * 400 + (c.month <= 2));
  c.hour = (unsigned) (secs / 3600);
  c.minute = (unsigned) (secs % 3600 / 60);
  c.second = (unsigned) (secs % 60);
  return c;
}

inline uint8_t bcd2bin(uint8_t v) { return (uint8_t) ((v >> 4) * 10 + (v & 0x0F)); }
inline uint8_t bin2bcd(uint8_t v) { return (uint8_t) (((v / 10) << 4) | (v % 10)); }

}  // namespace creek_core
```

- [ ] **Step 4: Run the tests to verify they pass**

Run the Step 2 command. Expected: `all store_core tests passed`, exit 0. If `test_civil_time` weekday fails, check that `(days % 7 + 11) % 7` gives 4 for day 0 (it does: 11 % 7 = 4).

- [ ] **Step 5: Commit**

```bash
git add firmware/esp32s3_feather_gateway/components/creek_store/store_core.h firmware/esp32s3_feather_gateway/tests/test_store_core.cpp
git commit -m "creek_store: pure record/stage/clock core with host tests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 2: Gated packet hook in `rfm69_gateway`

**Files:**
- Modify: `firmware/esp32_rfm69_gateway/components/rfm69_gateway/rfm69_gateway.h` (public section after `set_ota_diag_hex_url`; end of `handle_packet_()`; member list after `encryption_key_`)

**Interfaces:**
- Produces (only when `USE_RFM69_PACKET_HOOK` is defined):
  - `void Rfm69Gateway::add_on_packet_callback(std::function<void(const char *payload, int16_t rssi)> &&cb)`. Called from `handle_packet_()` **with the bus mutex held, on the main task**. Callbacks must not take the mutex or touch SPI.
  - `SemaphoreHandle_t Rfm69Gateway::bus_mutex() const` — the existing `radio_mutex_`, which v2 also uses for SD.

- [ ] **Step 1: Record v1's baseline build**

Run (Git Bash):
```bash
cd firmware/esp32_rfm69_gateway && cp -n secrets.yaml.example secrets.yaml; esphome compile gateway.yaml && sha256sum .esphome/build/creek-gateway/.pioenvs/creek-gateway/firmware.bin | tee /tmp/v1_before.sha
```
Expected: `INFO Successfully compiled program.` and a hash line. (If `cp -n` keeps an existing real `secrets.yaml`, that's intended.)

- [ ] **Step 2: Add the hook**

In the public section, after `set_ota_diag_hex_url(...)`:

```cpp
#ifdef USE_RFM69_PACKET_HOOK
  // v2 gateway only (creek_store, firmware/esp32s3_feather_gateway). Defined by creek_store's
  // codegen and by nothing else, so v1's build does not contain any of this.
  //
  // The callback runs inside handle_packet_(), on the main task, WITH radio_mutex_ HELD. It
  // must not take the mutex (FreeRTOS mutexes are not recursive) or touch the SPI bus: the
  // store queues the record and writes it from its own loop().
  void add_on_packet_callback(std::function<void(const char *, int16_t)> &&cb) {
    this->packet_callback_.add(std::move(cb));
  }
  // The SD card on the Adalogger shares this SPI bus, so the store serialises on the same
  // mutex the OTA transfer task already holds for the radio.
  SemaphoreHandle_t bus_mutex() const { return this->radio_mutex_; }
#endif
```

At the very end of `handle_packet_()`, after the `if (!parsed) { ... }` block:

```cpp
#ifdef USE_RFM69_PACKET_HOOK
    this->packet_callback_.call(payload, rssi);
#endif
```

After the `std::string encryption_key_;` member:

```cpp
#ifdef USE_RFM69_PACKET_HOOK
  CallbackManager<void(const char *, int16_t)> packet_callback_;
#endif
```

Add `#include "esphome/core/helpers.h"` next to the other `esphome/core/` includes (for `CallbackManager`) and `#include <functional>` next to `<utility>`.

- [ ] **Step 3: Verify v1 is byte-identical**

```bash
cd firmware/esp32_rfm69_gateway && esphome compile gateway.yaml && sha256sum .esphome/build/creek-gateway/.pioenvs/creek-gateway/firmware.bin | diff - /tmp/v1_before.sha && echo IDENTICAL
grep -c USE_RFM69_PACKET_HOOK .esphome/build/creek-gateway/src/esphome/core/defines.h || true
```
Expected: `IDENTICAL`, and the grep count `0`. If the hash differs, find out whether ESPHome embeds a build timestamp: `git stash`, compile twice, and compare those two hashes. If the two unchanged builds also differ, the binary hash can't be the check; use "defines.h has no `USE_RFM69_PACKET_HOOK`" as the check instead and say so in the commit message.

- [ ] **Step 4: Commit**

```bash
git add firmware/esp32_rfm69_gateway/components/rfm69_gateway/rfm69_gateway.h
git commit -m "rfm69_gateway: packet hook and bus mutex getter, compiled only for v2

v1 never defines USE_RFM69_PACKET_HOOK; its firmware.bin is unchanged.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 3: v2 YAML port (radio, entities, no store yet)

**Files:**
- Create: `firmware/esp32s3_feather_gateway/gateway.base.yaml`
- Create: `firmware/esp32s3_feather_gateway/ota-push.yaml`
- Create: `firmware/esp32s3_feather_gateway/gateway.yaml`
- Create: `firmware/esp32s3_feather_gateway/creek-gateway-v2.yaml`
- Create: `firmware/esp32s3_feather_gateway/creek-gateway-v2.prod.yaml`
- Create: `firmware/esp32s3_feather_gateway/secrets.yaml.example`
- Create: `firmware/esp32s3_feather_gateway/.gitignore`

**Interfaces:**
- Consumes: `creek_core::stage_from_distance` (Task 1) inside the distance lambda, via `esphome: includes:`. Task 4 replaces that include with the component header.
- Produces: ids used later: `creek_radio`, `mount_height_mm`, `creek_stage`, `creek_depth_in`, `sntp_time`, `i2c_bus`. Substitutions `sd_cs_pin`, `blanking_mm`, `overrange_slack_mm`, `ecowitt_host`.

- [ ] **Step 1: Write `gateway.base.yaml`**

```yaml
# Creek RFM69 gateway v2 — shared configuration (Feather ESP32-S3 + RFM69HCW + Adalogger).
#
# v1 (../esp32_rfm69_gateway/gateway.base.yaml) is the reference for every behaviour carried
# over here, and its comments explain why each piece exists; they are not repeated. What is
# new in v2 is the store (components/creek_store/): every node packet and Ecowitt reading is
# written to SD with a real timestamp, and the add-on backfills anything HA missed. See
# docs/superpowers/specs/2026-10-07-gateway-v2-sd-backfill-design.md.
#
# WIRING (Adafruit 5885 Feather ESP32-S3, 8 MB, no PSRAM). Both FeatherWings sit on the
# Feather's hardware SPI bus; the store and the radio share it under one mutex.
#   SPI SCK/MOSI/MISO  GPIO36 / GPIO35 / GPIO37   (Feather SCK/MO/MI)
#   SD CS              GPIO10  (Adalogger default, D10)
#   RFM69 CS           GPIO6   (D6, wing jumper pad)
#   RFM69 DIO0 (IRQ)   GPIO5   (D5, wing jumper pad)
#   RFM69 RST          GPIO9   (D9, wing jumper pad) — active HIGH, idles low, as v1
#   I2C SDA / SCL      GPIO3 / GPIO4 (PCF8523 RTC on the Adalogger)
#   I2C_POWER          GPIO7   — powers the Feather's I2C pull-ups; driven high at boot
# The S3's strapping pins are GPIO0, 3, 45 and 46. GPIO3 (SDA) only selects the JTAG source,
# and only when the STRAP_JTAG_SEL eFuse is burned, which it is not on the Feather; Adafruit
# routes SDA there by design. Nothing here can hold the chip in download mode the way the
# XIAO C3's default SPI pins did (see v1).

substitutions:
  device_name: creek-gateway-v2
  friendly_name: Creek Gateway v2

  rfm69_cs_pin: "6"
  rfm69_sck_pin: "36"
  rfm69_miso_pin: "37"
  rfm69_mosi_pin: "35"
  rfm69_irq_pin: "5"
  rfm69_reset_pin: "9"
  # MUST match FREQUENCY in moteino_creek_node/src/main.cpp (see v1).
  rfm69_frequency: "915"
  rfm69_node_id: "2"
  rfm69_network_id: "100"

  sd_cs_pin: "10"
  i2c_power_pin: "7"
  ecowitt_host: "192.168.30.7"

  node_hex_url: "https://github.com/ryanbuiltthat/rate-of-rise/releases/download/node-firmware-latest/firmware.hex"
  node_diag_hex_url: "https://github.com/ryanbuiltthat/rate-of-rise/releases/download/node-firmware-latest/firmware-diag.hex"

  blanking_mm: "150"
  overrange_slack_mm: "1000"

esphome:
  name: ${device_name}
  friendly_name: ${friendly_name}
  # Task 4 replaces this with the creek_store component, whose header includes it.
  includes:
    - components/creek_store/store_core.h
  on_boot:
    # Before the I2C bus (setup priority 1000) comes up: the Feather's I2C pull-ups are
    # powered from GPIO7, and with them off the PCF8523 never answers.
    - priority: 1100
      then:
        - lambda: |-
            pinMode(${i2c_power_pin}, OUTPUT);
            digitalWrite(${i2c_power_pin}, HIGH);

esp32:
  board: adafruit_feather_esp32s3_nopsram
  variant: esp32s3
  flash_size: 8MB
  framework:
    type: arduino
    sdkconfig_options:
      CONFIG_APP_REPRODUCIBLE_BUILD: "n"   # Windows CreateProcess limit; see v1
  toolchain: platformio                    # RFM69's stale library.json; see v1

logger:
  level: INFO

api:
  encryption:
    key: !secret creek_gateway_v2_api_key
  # ESPHome's default reboots the device after 15 min with no API client. v2 exists to keep
  # recording while Home Assistant is down, so that reboot would loop through every outage.
  reboot_timeout: 0s

ota:
  - platform: esphome

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password
  reboot_timeout: 0s        # same reason as api: above
  ap:
    ssid: ${friendly_name} Fallback
    password: !secret ap_password

captive_portal:

# The store's clock. SNTP sets the system clock; creek_store copies it into the PCF8523 on
# every sync and restores the system clock from the PCF8523 at boot. The router first, so a
# LAN with no internet still syncs.
time:
  - platform: sntp
    id: sntp_time
    servers:
      - 192.168.30.1
      - 0.pool.ntp.org
      - 1.pool.ntp.org

i2c:
  id: i2c_bus
  sda: 3
  scl: 4
  scan: true

rfm69_gateway:
  id: creek_radio
  cs_pin: ${rfm69_cs_pin}
  sck_pin: ${rfm69_sck_pin}
  miso_pin: ${rfm69_miso_pin}
  mosi_pin: ${rfm69_mosi_pin}
  irq_pin: ${rfm69_irq_pin}
  reset_pin: ${rfm69_reset_pin}
  frequency: ${rfm69_frequency}
  node_id: ${rfm69_node_id}
  network_id: ${rfm69_network_id}
  is_rfm69hw: true
  encryption_key: !secret rfm69_encrypt_key
  ota_hex_url: ${node_hex_url}
  ota_diag_hex_url: ${node_diag_hex_url}
  distance:
    id: creek_distance_mm
    name: Sensor Distance
    entity_category: diagnostic
    on_value:
      then:
        - lambda: |-
            const float mount = id(mount_height_mm).state;
            if (isnan(mount)) return;
            const auto r = creek_core::stage_from_distance(
                mount, isnan(x) ? std::optional<float>() : std::optional<float>(x),
                ${blanking_mm}, ${overrange_slack_mm});
            if (r.lost_target) ESP_LOGW("creek", "distance %.0f mm implausible - target lost", x);
            if (r.clamped) ESP_LOGW("creek", "distance %.0f mm inside blanking zone - depth "
                                    "clamped (at or above range ceiling)", x);
            id(creek_stage).publish_state(r.stage_ft ? *r.stage_ft : NAN);
            id(creek_depth_in).publish_state(r.depth_in ? *r.depth_in : NAN);
  battery_voltage:
    name: Creek Node Battery
  rssi:
    name: Creek Node RSSI
  packet_count:
    name: Creek Node Packets
  fast_mode:
    name: Creek Node Fast Sampling
  diag_active:
    name: Creek Node Diagnostic Active
  radar_fault:
    name: Creek Node Radar Fault
  radar_failures:
    name: Creek Node Radar Failures
  node_status:
    name: Creek Node Status
  ota_status:
    name: Node OTA Status
  reset_cause:
    name: Creek Node Reset Cause
  cycle:
    name: Creek Node Cycle
  radio_init_failures:
    name: Creek Node Radio Init Failures

number:
  - platform: template
    id: mount_height_mm
    name: Installation Height
    icon: mdi:arrow-expand-vertical
    entity_category: config
    optimistic: true
    restore_value: true
    initial_value: "1105"
    min_value: 500
    max_value: 6000
    step: 1
    unit_of_measurement: mm
    mode: box

sensor:
  - platform: template
    id: creek_stage
    name: Stage
    unit_of_measurement: ft
    device_class: distance
    state_class: measurement
    accuracy_decimals: 2

  - platform: template
    id: creek_depth_in
    name: Creek Depth
    unit_of_measurement: in
    device_class: distance
    state_class: measurement
    accuracy_decimals: 1
    icon: mdi:waves-arrow-up

  - platform: wifi_signal
    name: WiFi Signal
    entity_category: diagnostic
    update_interval: 300s

  - platform: uptime
    name: Uptime
    entity_category: diagnostic
    update_interval: 300s

text_sensor:
  - platform: wifi_info
    ip_address:
      name: IP Address
      entity_category: diagnostic

button:
  - platform: restart
    name: "Restart Creek Gateway"
```

- [ ] **Step 2: Write `ota-push.yaml`**

```yaml
# The two node-OTA buttons, kept out of gateway.base.yaml so the trial build can omit them:
# while v1 and v2 both run, only v1 may push to the node. creek-gateway-v2.prod.yaml adds
# this package back at cutover. Button semantics: see v1's gateway.base.yaml.
button:
  - platform: template
    name: Push Node Firmware
    entity_category: config
    on_press:
      - lambda: id(creek_radio).start_ota_push();

  - platform: template
    name: Push Node Diagnostic Firmware
    entity_category: config
    on_press:
      - lambda: id(creek_radio).start_ota_push(true);
```

- [ ] **Step 3: Write the three wrappers, secrets example and .gitignore**

`gateway.yaml` (local CLI, trial — no OTA buttons):
```yaml
# Creek gateway v2 — local CLI wrapper (trial: no node-OTA buttons).
#   cp secrets.yaml.example secrets.yaml   # then fill in (gitignored)
#   esphome run gateway.yaml
# rfm69_gateway comes from v1's directory (shared, unchanged for v1); creek_store from here.
packages:
  base: !include gateway.base.yaml

external_components:
  - source:
      type: local
      path: ../esp32_rfm69_gateway/components
    components: [rfm69_gateway]
  - source:
      type: local
      path: components
    components: [creek_store]
```

`creek-gateway-v2.yaml` (Device Builder, trial):
```yaml
# Creek gateway v2 — Home Assistant Device Builder wrapper, TRIAL (no node-OTA buttons).
# Tracked mirror of /config/esphome/creek-gateway-v2.yaml; copy it there by hand.
packages:
  gateway:
    url: https://github.com/ryanbuiltthat/rate-of-rise
    ref: main
    files: [firmware/esp32s3_feather_gateway/gateway.base.yaml]
    refresh: 0s

external_components:
  - source:
      type: git
      url: https://github.com/ryanbuiltthat/rate-of-rise
      ref: main
      path: firmware/esp32_rfm69_gateway/components
    components: [rfm69_gateway]
    refresh: 0s
  - source:
      type: git
      url: https://github.com/ryanbuiltthat/rate-of-rise
      ref: main
      path: firmware/esp32s3_feather_gateway/components
    components: [creek_store]
    refresh: 0s
```

`creek-gateway-v2.prod.yaml` (Device Builder, after cutover): identical to `creek-gateway-v2.yaml` except the header comment says `PRODUCTION (node-OTA buttons included)` and `packages:` has a second entry:
```yaml
  ota_push:
    url: https://github.com/ryanbuiltthat/rate-of-rise
    ref: main
    files: [firmware/esp32s3_feather_gateway/ota-push.yaml]
    refresh: 0s
```

`secrets.yaml.example`:
```yaml
# cp secrets.yaml.example secrets.yaml (gitignored). /config/esphome/secrets.yaml on HA is
# authoritative; the first three keys are shared with the other ESPHome devices there.
wifi_ssid: "your-ssid"
wifi_password: "your-wifi-password"
ap_password: "fallback-password"

# openssl rand -base64 32
creek_gateway_v2_api_key: "base64-32-bytes-here"
# Must equal the node's ENCRYPT_KEY (16 chars) — same value as v1's.
rfm69_encrypt_key: "sampleEncryptKey"
# Bearer token for the store API; the add-on's gateway_store_token is the same value.
# openssl rand -hex 16
creek_store_token: "0123456789abcdef0123456789abcdef"
```

`.gitignore`:
```
/.esphome/
/secrets.yaml
```

- [ ] **Step 4: Compile (store-less) to prove the S3 + RFM69 build**

Task 3 compiles **without** `creek_store` in the config, so temporarily comment out the second `external_components` entry in `gateway.yaml` for this one compile (Task 4 restores it), then:
```bash
cd firmware/esp32s3_feather_gateway && cp -n secrets.yaml.example secrets.yaml && esphome compile gateway.yaml
```
Expected: `Successfully compiled program.` If `includes:` fails because `components/creek_store/` has only `store_core.h` (no `__init__.py`), that's fine for `includes:` — it's a plain file path. Restore the `external_components` entry afterwards.

- [ ] **Step 5: Commit**

```bash
git add firmware/esp32s3_feather_gateway/gateway.base.yaml firmware/esp32s3_feather_gateway/ota-push.yaml firmware/esp32s3_feather_gateway/gateway.yaml firmware/esp32s3_feather_gateway/creek-gateway-v2.yaml firmware/esp32s3_feather_gateway/creek-gateway-v2.prod.yaml firmware/esp32s3_feather_gateway/secrets.yaml.example firmware/esp32s3_feather_gateway/.gitignore
git commit -m "gateway v2: Feather ESP32-S3 port of v1's config, trial and prod wrappers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 4: `creek_store` component — clock, SD log, node records, health entities

**Files:**
- Create: `firmware/esp32s3_feather_gateway/components/creek_store/__init__.py`
- Create: `firmware/esp32s3_feather_gateway/components/creek_store/creek_store.h`
- Modify: `firmware/esp32s3_feather_gateway/gateway.base.yaml` (drop `esphome: includes:`, add `creek_store:` block)

**Interfaces:**
- Consumes: `Rfm69Gateway::add_on_packet_callback`, `Rfm69Gateway::bus_mutex()` (Task 2); `creek_core::*` (Task 1).
- Produces: `esphome::creek_store::CreekStore` with members `last_seq_[]`, `written_seq_[]`, `newest_block_[]`, `bus_`, `sd_ok_`, `queue_`, `enqueue_()`, `now_ts_()`, `ts_src_()`, and the stubs `setup_ecowitt_()`, `collect_ecowitt_()`, `setup_http_()` that Tasks 5–6 replace. The YAML block `creek_store:` takes `radio_id, time_id, mount_height_id, i2c_id, address, sd_cs_pin, token, ecowitt_host, ecowitt_interval, blanking_mm, overrange_slack_mm, sd_fault, free_space, clock_source, node_records, ecowitt_records, ecowitt_failures`.

- [ ] **Step 1: Spike — prove `SD.h` builds under ESPHome's Arduino toolchain**

Create a throwaway `creek_store.h`:
```cpp
#pragma once
#include "esphome/core/component.h"
#include <FS.h>
#include <SD.h>
namespace esphome { namespace creek_store {
class CreekStore : public Component {
 public:
  void setup() override { SD.begin(10); }
};
}}  // namespace esphome::creek_store
```
and a throwaway `__init__.py`:
```python
import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.const import CONF_ID
ns = cg.esphome_ns.namespace("creek_store")
CreekStore = ns.class_("CreekStore", cg.Component)
CONFIG_SCHEMA = cv.Schema({cv.GenerateID(): cv.declare_id(CreekStore)}).extend(cv.COMPONENT_SCHEMA)
async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    cg.add_library("FS", None)
    cg.add_library("SD", None)
    cg.add_library("SPI", None)
```
Add `creek_store:` (empty) to `gateway.base.yaml` and run `cd firmware/esp32s3_feather_gateway && esphome compile gateway.yaml`.

Expected: `Successfully compiled program.`

If it fails with `SD.h: No such file` or undefined `ff_*`/`sdmmc_*`/`esp_vfs_fat_*` symbols:
1. Open `.esphome/build/creek-gateway-v2/platformio.ini` and look for `fatfs`, `sdmmc`, `vfs` or `wear_levelling` in the `-DEXCLUDE_COMPONENTS=` list inside `board_build.cmake_extra_args`.
2. Confirm the opt-back-in helper exists: `grep -n "def include_builtin_idf_component" "$(python -c "import esphome,os;print(os.path.dirname(esphome.__file__))")/components/esp32/__init__.py"`.
3. Add `from esphome.components import esp32` and, in `to_code`, `esp32.include_builtin_idf_component("fatfs")` (plus one call per other excluded name from item 1). Recompile.

**Stop and report to the user** if it still fails. Tasks 5–8 depend on it. Note in the commit message which (if any) `include_builtin_idf_component` calls were needed; Step 2 keeps them.

- [ ] **Step 2: Write the real `__init__.py`**

```python
"""Creek gateway v2 store: every node packet and Ecowitt reading on SD, replayable over HTTP.

The C++ lives in creek_store.h (device) and store_core.h (pure, host-tested). This file wires
YAML to it. It is also what makes v2 differ from v1 at compile time: it defines
USE_RFM69_PACKET_HOOK, the only switch that compiles rfm69_gateway's packet hook in.
"""
import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import binary_sensor, i2c, number, sensor, text_sensor
from esphome.components import time as time_
from esphome.const import (
    CONF_ID,
    DEVICE_CLASS_PROBLEM,
    ENTITY_CATEGORY_DIAGNOSTIC,
    STATE_CLASS_MEASUREMENT,
    STATE_CLASS_TOTAL_INCREASING,
)
from esphome.core import CORE

DEPENDENCIES = ["rfm69_gateway", "i2c", "time"]
AUTO_LOAD = ["binary_sensor", "json", "sensor", "text_sensor", "web_server_base"]
CODEOWNERS = ["@ryanbuiltthat"]

creek_store_ns = cg.esphome_ns.namespace("creek_store")
CreekStore = creek_store_ns.class_("CreekStore", cg.Component, i2c.I2CDevice)
Rfm69Gateway = cg.esphome_ns.namespace("rfm69_gateway").class_("Rfm69Gateway", cg.Component)

CONF_RADIO_ID = "radio_id"
CONF_TIME_ID = "time_id"
CONF_MOUNT_HEIGHT_ID = "mount_height_id"
CONF_SD_CS_PIN = "sd_cs_pin"
CONF_TOKEN = "token"
CONF_ECOWITT_HOST = "ecowitt_host"
CONF_ECOWITT_INTERVAL = "ecowitt_interval"
CONF_BLANKING_MM = "blanking_mm"
CONF_OVERRANGE_SLACK_MM = "overrange_slack_mm"
CONF_SD_FAULT = "sd_fault"
CONF_FREE_SPACE = "free_space"
CONF_CLOCK_SOURCE = "clock_source"
CONF_NODE_RECORDS = "node_records"
CONF_ECOWITT_RECORDS = "ecowitt_records"
CONF_ECOWITT_FAILURES = "ecowitt_failures"

_DIAG = {"entity_category": ENTITY_CATEGORY_DIAGNOSTIC}

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(CreekStore),
            cv.Required(CONF_RADIO_ID): cv.use_id(Rfm69Gateway),
            cv.Required(CONF_TIME_ID): cv.use_id(time_.RealTimeClock),
            cv.Required(CONF_MOUNT_HEIGHT_ID): cv.use_id(number.Number),
            cv.Required(CONF_SD_CS_PIN): cv.int_range(min=0, max=48),
            # Same value as the add-on's gateway_store_token. 16+ chars so a placeholder
            # cannot pass validation.
            cv.Required(CONF_TOKEN): cv.All(cv.string_strict, cv.Length(min=16)),
            cv.Optional(CONF_ECOWITT_HOST, default=""): cv.string,
            cv.Optional(CONF_ECOWITT_INTERVAL, default="60s"): cv.positive_time_period_milliseconds,
            cv.Optional(CONF_BLANKING_MM, default=150): cv.float_,
            cv.Optional(CONF_OVERRANGE_SLACK_MM, default=1000): cv.float_,
            cv.Optional(CONF_SD_FAULT): binary_sensor.binary_sensor_schema(
                device_class=DEVICE_CLASS_PROBLEM, icon="mdi:micro-sd", **_DIAG),
            cv.Optional(CONF_FREE_SPACE): sensor.sensor_schema(
                unit_of_measurement="MB", accuracy_decimals=0,
                state_class=STATE_CLASS_MEASUREMENT, icon="mdi:micro-sd", **_DIAG),
            cv.Optional(CONF_CLOCK_SOURCE): text_sensor.text_sensor_schema(
                icon="mdi:clock-check-outline", **_DIAG),
            cv.Optional(CONF_NODE_RECORDS): sensor.sensor_schema(
                accuracy_decimals=0, state_class=STATE_CLASS_TOTAL_INCREASING,
                icon="mdi:database", **_DIAG),
            cv.Optional(CONF_ECOWITT_RECORDS): sensor.sensor_schema(
                accuracy_decimals=0, state_class=STATE_CLASS_TOTAL_INCREASING,
                icon="mdi:database", **_DIAG),
            cv.Optional(CONF_ECOWITT_FAILURES): sensor.sensor_schema(
                accuracy_decimals=0, state_class=STATE_CLASS_TOTAL_INCREASING,
                icon="mdi:weather-cloudy-alert", **_DIAG),
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
    .extend(i2c.i2c_device_schema(0x68))   # PCF8523
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await i2c.register_i2c_device(var, config)

    cg.add(var.set_radio(await cg.get_variable(config[CONF_RADIO_ID])))
    cg.add(var.set_time(await cg.get_variable(config[CONF_TIME_ID])))
    cg.add(var.set_mount_height(await cg.get_variable(config[CONF_MOUNT_HEIGHT_ID])))
    cg.add(var.set_sd_cs_pin(config[CONF_SD_CS_PIN]))
    cg.add(var.set_token(config[CONF_TOKEN]))
    cg.add(var.set_ecowitt_host(config[CONF_ECOWITT_HOST]))
    cg.add(var.set_ecowitt_interval_ms(config[CONF_ECOWITT_INTERVAL]))
    cg.add(var.set_blanking_mm(config[CONF_BLANKING_MM]))
    cg.add(var.set_overrange_slack_mm(config[CONF_OVERRANGE_SLACK_MM]))
    cg.add(var.set_device_name(CORE.name))

    if conf := config.get(CONF_SD_FAULT):
        cg.add(var.set_sd_fault_sensor(await binary_sensor.new_binary_sensor(conf)))
    if conf := config.get(CONF_FREE_SPACE):
        cg.add(var.set_free_space_sensor(await sensor.new_sensor(conf)))
    if conf := config.get(CONF_CLOCK_SOURCE):
        cg.add(var.set_clock_source_sensor(await text_sensor.new_text_sensor(conf)))
    if conf := config.get(CONF_NODE_RECORDS):
        cg.add(var.set_node_records_sensor(await sensor.new_sensor(conf)))
    if conf := config.get(CONF_ECOWITT_RECORDS):
        cg.add(var.set_ecowitt_records_sensor(await sensor.new_sensor(conf)))
    if conf := config.get(CONF_ECOWITT_FAILURES):
        cg.add(var.set_ecowitt_failures_sensor(await sensor.new_sensor(conf)))

    # The one switch that compiles rfm69_gateway's packet hook in. v1 never sets it.
    cg.add_define("USE_RFM69_PACKET_HOOK")
    cg.add_library("FS", None)
    cg.add_library("SD", None)
    cg.add_library("SPI", None)
    # + any esp32.include_builtin_idf_component(...) calls Step 1 proved necessary.
```

- [ ] **Step 3: Write `creek_store.h` (storage, clock, node records, health)**

```cpp
// Creek gateway v2 store. See __init__.py for the overview and store_core.h for the formats.
//
// Threads and the bus:
//  * The radio and the SD card share one SPI bus, guarded by rfm69_gateway's mutex.
//  * Node packets arrive through rfm69_gateway's hook, on the main task, with the mutex HELD.
//    They are encoded and queued there, never written there.
//  * loop() writes the queue to SD with a NON-BLOCKING take, as rfm69_gateway's loop does,
//    so an OTA transfer holding the bus for minutes never stalls the main task.
#pragma once

#include <atomic>
#include <cmath>
#include <deque>
#include <mutex>
#include <string>
#include <sys/time.h>
#include <vector>

#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/components/i2c/i2c.h"
#include "esphome/components/json/json_util.h"
#include "esphome/components/number/number.h"
#include "esphome/components/rfm69_gateway/rfm69_gateway.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/components/time/real_time_clock.h"
#include "esphome/core/component.h"
#include "esphome/core/log.h"

#include <FS.h>
#include <SD.h>

#include "store_core.h"

namespace esphome {
namespace creek_store {

static const char *const TAG = "creek_store";
static const size_t QUEUE_MAX = 64;
static const uint32_t NTP_FRESH_MS = 24UL * 3600UL * 1000UL;
static const uint32_t HEALTH_INTERVAL_MS = 60000;
static const uint32_t FREE_SPACE_INTERVAL_MS = 600000;  // f_getfree can scan the whole FAT
static const uint32_t REMOUNT_INTERVAL_MS = 30000;
static const uint32_t SD_FREQ_HZ = 10000000;
static const size_t TAIL_BYTES = 2048;
static const uint8_t PCF8523_CONTROL_3 = 0x02;
static const uint8_t PCF8523_SECONDS = 0x03;

enum StreamId : uint8_t { NODE = 0, ECOWITT = 1, STREAM_COUNT = 2 };
static const char *const STREAM_NAMES[STREAM_COUNT] = {"node", "ecowitt"};

struct Pending {
  uint8_t stream;
  uint32_t seq;
  std::string line;
};

class CreekStore : public Component, public i2c::I2CDevice {
 public:
  void set_radio(rfm69_gateway::Rfm69Gateway *r) { this->radio_ = r; }
  void set_time(time::RealTimeClock *t) { this->time_ = t; }
  void set_mount_height(number::Number *n) { this->mount_ = n; }
  void set_sd_cs_pin(uint8_t p) { this->sd_cs_pin_ = p; }
  void set_token(const std::string &t) { this->token_ = t; }
  void set_ecowitt_host(const std::string &h) { this->ecowitt_host_ = h; }
  void set_ecowitt_interval_ms(uint32_t ms) { this->ecowitt_interval_ms_ = ms; }
  void set_blanking_mm(float v) { this->blanking_mm_ = v; }
  void set_overrange_slack_mm(float v) { this->overrange_slack_mm_ = v; }
  void set_device_name(const std::string &n) { this->device_name_ = n; }
  void set_sd_fault_sensor(binary_sensor::BinarySensor *s) { this->sd_fault_ = s; }
  void set_free_space_sensor(sensor::Sensor *s) { this->free_space_ = s; }
  void set_clock_source_sensor(text_sensor::TextSensor *s) { this->clock_source_ = s; }
  void set_node_records_sensor(sensor::Sensor *s) { this->node_records_ = s; }
  void set_ecowitt_records_sensor(sensor::Sensor *s) { this->ecowitt_records_ = s; }
  void set_ecowitt_failures_sensor(sensor::Sensor *s) { this->ecowitt_failures_sensor_ = s; }

  // After rfm69_gateway (LATE), which creates the bus mutex and starts SPI on our pins.
  float get_setup_priority() const override { return setup_priority::LATE - 10.0f; }

  void setup() override {
    this->bus_ = this->radio_->bus_mutex();
    if (this->bus_ == nullptr) {
      ESP_LOGE(TAG, "rfm69_gateway has no bus mutex; the store cannot share the SPI bus");
      this->mark_failed();
      return;
    }
    this->restore_clock_from_rtc_();
    this->time_->add_on_time_sync_callback([this]() { this->on_ntp_sync_(); });
    this->radio_->add_on_packet_callback(
        [this](const char *payload, int16_t rssi) { this->on_packet_(payload, rssi); });
    if (xSemaphoreTake(this->bus_, pdMS_TO_TICKS(2000)) == pdTRUE) {
      this->mount_sd_();
      xSemaphoreGive(this->bus_);
    }
    this->setup_ecowitt_();  // Task 5
    this->setup_http_();     // Task 6
    this->publish_health_(true);
  }

  void loop() override {
    this->collect_ecowitt_();  // Task 5
    if (!this->queue_.empty() && this->sd_ok_ && xSemaphoreTake(this->bus_, 0) == pdTRUE) {
      for (uint8_t n = 0; n < 8 && !this->queue_.empty(); n++) {
        if (!this->append_(this->queue_.front())) {
          this->sd_ok_ = false;
          ESP_LOGE(TAG, "SD write failed; %u record(s) stay queued", (unsigned) this->queue_.size());
          break;
        }
        this->queue_.pop_front();
      }
      xSemaphoreGive(this->bus_);
    }
    const uint32_t now = millis();
    if (!this->sd_ok_ && now - this->last_remount_ms_ >= REMOUNT_INTERVAL_MS &&
        xSemaphoreTake(this->bus_, 0) == pdTRUE) {
      this->last_remount_ms_ = now;
      SD.end();
      this->mount_sd_();
      xSemaphoreGive(this->bus_);
    }
    if (now - this->last_health_ms_ >= HEALTH_INTERVAL_MS) this->publish_health_(false);
  }

 protected:
  // --- clock -----------------------------------------------------------------------------
  // The system clock is the single source of record timestamps. SNTP sets it when the network
  // is up; at boot the PCF8523 sets it, so a reboot during an outage still has real time.
  void restore_clock_from_rtc_() {
    // Control_3 powers up as 0b111: battery switchover DISABLED. Left that way the RTC stops
    // the moment USB power drops and the CR1220 is never used. 0x00 = standard switchover.
    if (!this->write_byte(PCF8523_CONTROL_3, 0x00)) {
      ESP_LOGW(TAG, "PCF8523 not answering on I2C; time unknown until SNTP");
      return;
    }
    uint8_t r[7];
    if (this->read_register(PCF8523_SECONDS, r, 7) != i2c::ERROR_OK) return;
    if (r[0] & 0x80) {  // OS: the oscillator stopped (battery flat or removed)
      ESP_LOGW(TAG, "PCF8523 oscillator-stop flag set; time unknown until SNTP");
      return;
    }
    creek_core::Civil c{};
    c.second = creek_core::bcd2bin(r[0] & 0x7F);
    c.minute = creek_core::bcd2bin(r[1] & 0x7F);
    c.hour = creek_core::bcd2bin(r[2] & 0x3F);
    c.day = creek_core::bcd2bin(r[3] & 0x3F);
    c.month = creek_core::bcd2bin(r[5] & 0x1F);
    c.year = 2000 + creek_core::bcd2bin(r[6]);
    if (c.year < 2025) return;  // never set
    struct timeval tv {};
    tv.tv_sec = (time_t) creek_core::epoch_from_civil(c);
    settimeofday(&tv, nullptr);
    this->clock_set_ = true;
    ESP_LOGI(TAG, "System clock restored from PCF8523: %04d-%02u-%02u %02u:%02u:%02u UTC", c.year,
             c.month, c.day, c.hour, c.minute, c.second);
  }

  // ESPHome's sntp fires this on every sync (each update_interval), so the RTC is rewritten
  // every time and never drifts far.
  void on_ntp_sync_() {
    this->last_ntp_ms_ = millis();
    this->ever_ntp_ = true;
    this->clock_set_ = true;
    const creek_core::Civil c = creek_core::civil_from_epoch((int64_t) ::time(nullptr));
    const uint8_t r[7] = {creek_core::bin2bcd((uint8_t) c.second),  // also clears OS
                          creek_core::bin2bcd((uint8_t) c.minute),
                          creek_core::bin2bcd((uint8_t) c.hour),
                          creek_core::bin2bcd((uint8_t) c.day),
                          (uint8_t) c.weekday,
                          creek_core::bin2bcd((uint8_t) c.month),
                          creek_core::bin2bcd((uint8_t) (c.year - 2000))};
    if (this->write_register(PCF8523_SECONDS, r, 7) != i2c::ERROR_OK)
      ESP_LOGW(TAG, "Could not write the PCF8523 after SNTP sync");
  }

  const char *ts_src_() const {
    if (this->ever_ntp_ && millis() - this->last_ntp_ms_ < NTP_FRESH_MS) return "ntp";
    if (this->clock_set_) return "rtc";
    return "none";
  }

  static double now_ts_() {
    struct timeval tv {};
    gettimeofday(&tv, nullptr);
    return (double) tv.tv_sec + tv.tv_usec / 1e6;
  }

  // --- SD --------------------------------------------------------------------------------
  // Bus mutex held.
  void mount_sd_() {
    this->sd_ok_ = SD.begin(this->sd_cs_pin_, SPI, SD_FREQ_HZ);
    if (!this->sd_ok_) {
      ESP_LOGE(TAG, "SD card did not mount (CS GPIO%u); live entities keep working, nothing is "
                    "being stored", this->sd_cs_pin_);
      return;
    }
    for (uint8_t s = 0; s < STREAM_COUNT; s++) this->load_stream_(s);
  }

  // Bus mutex held. Finds the newest block file and the last complete record in it, so seq
  // continues across reboots.
  void load_stream_(uint8_t s) {
    const std::string dir = std::string("/") + STREAM_NAMES[s];
    SD.mkdir(dir.c_str());
    int64_t newest = -1;
    File root = SD.open(dir.c_str());
    if (root) {
      for (File e = root.openNextFile(); e; e = root.openNextFile()) {
        const auto b = creek_core::parse_block_name(e.name());
        if (b && (int64_t) *b > newest) newest = *b;
        e.close();
      }
      root.close();
    }
    this->newest_block_[s] = newest;
    uint32_t last = 0;
    if (newest >= 0) {
      File f = SD.open(creek_core::block_path(STREAM_NAMES[s], (uint32_t) newest).c_str());
      if (f) {
        const size_t size = f.size();
        const size_t from = size > TAIL_BYTES ? size - TAIL_BYTES : 0;
        std::string tail(size - from, '\0');
        f.seek(from);
        tail.resize(f.read((uint8_t *) &tail[0], tail.size()));
        f.close();
        this->needs_newline_[s] = creek_core::tail_is_torn(tail);
        const auto seq = creek_core::last_seq_in_tail(tail);
        last = seq ? *seq : (newest > 0 ? (uint32_t) newest * creek_core::BLOCK - 1 : 0);
      }
    }
    if (last > this->last_seq_[s]) this->last_seq_[s] = last;
    this->written_seq_[s] = this->last_seq_[s];
    ESP_LOGI(TAG, "%s stream: last seq %u (newest block %lld)%s", STREAM_NAMES[s],
             (unsigned) this->last_seq_[s], (long long) newest,
             this->needs_newline_[s] ? ", torn final line" : "");
  }

  // Bus mutex held.
  bool append_(const Pending &p) {
    const uint32_t block = creek_core::block_of(p.seq);
    File f = SD.open(creek_core::block_path(STREAM_NAMES[p.stream], block).c_str(), FILE_APPEND);
    if (!f) return false;
    bool ok = true;
    // A torn line from a power cut must not swallow the next record: end it first. Readers
    // skip it because it does not parse.
    if (this->needs_newline_[p.stream]) ok = f.print("\n") == 1;
    ok = ok && f.print(p.line.c_str()) == p.line.size();
    f.close();
    if (!ok) {
      this->needs_newline_[p.stream] = true;
      return false;
    }
    this->needs_newline_[p.stream] = false;
    if ((int64_t) block > this->newest_block_[p.stream]) this->newest_block_[p.stream] = block;
    this->written_seq_[p.stream] = p.seq;
    return true;
  }

  void enqueue_(uint8_t stream, uint32_t seq, std::string &&line) {
    if (this->queue_.size() >= QUEUE_MAX) {
      ESP_LOGW(TAG, "store queue full; dropping %s seq %u",
               STREAM_NAMES[this->queue_.front().stream], (unsigned) this->queue_.front().seq);
      this->queue_.pop_front();
    }
    this->queue_.push_back(Pending{stream, seq, std::move(line)});
  }

  // --- node packets (main task, bus mutex HELD: queue only) -------------------------------
  void on_packet_(const char *payload, int16_t rssi) {
    creek_core::NodeFields f;
    const bool ok = json::parse_json(std::string(payload), [&f](JsonObject root) -> bool {
      // Both payload dialects, keyed off `v` exactly as rfm69_gateway decodes them.
      const bool compact = !root["v"].isNull();
      auto d = compact ? root["d"] : root["distance_mm"];
      auto v = compact ? root["v"] : root["battery_mv"];
      auto fast = compact ? root["f"] : root["fast"];
      auto diag = compact ? root["g"] : root["diag"];
      if (!d.isNull()) f.distance_mm = d.as<float>();
      if (!v.isNull()) f.battery_mv = v.as<float>();
      if (!fast.isNull()) f.fast = fast.as<int>();
      if (!diag.isNull()) f.diag = diag.as<int>();
      if (!root["r"].isNull()) f.reset_cause = root["r"].as<int>();
      if (!root["n"].isNull()) f.cycle = root["n"].as<int>();
      if (!root["i"].isNull()) f.init_failures = root["i"].as<int>();
      return true;
    });
    if (!ok) return;  // rfm69_gateway already logged the unparseable packet
    const float mount = this->mount_ != nullptr ? this->mount_->state : NAN;
    const auto stage = creek_core::stage_from_distance(mount, f.distance_mm, this->blanking_mm_,
                                                       this->overrange_slack_mm_);
    const uint32_t seq = ++this->last_seq_[NODE];
    this->enqueue_(NODE, seq,
                   creek_core::encode_node_record(seq, now_ts_(), this->ts_src_(), rssi, f, mount,
                                                  stage));
  }

  // --- health ----------------------------------------------------------------------------
  void publish_health_(bool force) {
    const uint32_t now = millis();
    this->last_health_ms_ = now;
    if (this->sd_ok_ && (force || now - this->last_free_ms_ >= FREE_SPACE_INTERVAL_MS) &&
        xSemaphoreTake(this->bus_, 0) == pdTRUE) {
      this->last_free_ms_ = now;
      this->free_mb_ = (uint32_t) ((SD.totalBytes() - SD.usedBytes()) / (1024ULL * 1024ULL));
      xSemaphoreGive(this->bus_);
    }
    if (this->sd_fault_ != nullptr) this->sd_fault_->publish_state(!this->sd_ok_);
    if (this->free_space_ != nullptr && this->sd_ok_) this->free_space_->publish_state(this->free_mb_);
    if (this->clock_source_ != nullptr) {
      const char *src = this->ts_src_();
      if (this->clock_source_->state != src) this->clock_source_->publish_state(src);
    }
    if (this->node_records_ != nullptr) this->node_records_->publish_state(this->written_seq_[NODE]);
    if (this->ecowitt_records_ != nullptr)
      this->ecowitt_records_->publish_state(this->written_seq_[ECOWITT]);
    if (this->ecowitt_failures_sensor_ != nullptr)
      this->ecowitt_failures_sensor_->publish_state(this->ecowitt_failures_);
  }

  // Tasks 5 and 6 replace these three stubs.
  void setup_ecowitt_() {}
  void collect_ecowitt_() {}
  void setup_http_() {}

  rfm69_gateway::Rfm69Gateway *radio_{nullptr};
  time::RealTimeClock *time_{nullptr};
  number::Number *mount_{nullptr};
  SemaphoreHandle_t bus_{nullptr};
  uint8_t sd_cs_pin_{10};
  std::string token_;
  std::string ecowitt_host_;
  uint32_t ecowitt_interval_ms_{60000};
  float blanking_mm_{150};
  float overrange_slack_mm_{1000};
  std::string device_name_;

  bool sd_ok_{false};
  bool clock_set_{false};
  bool ever_ntp_{false};
  uint32_t last_ntp_ms_{0};
  uint32_t last_health_ms_{0};
  uint32_t last_free_ms_{0};
  uint32_t last_remount_ms_{0};
  uint32_t free_mb_{0};
  uint32_t ecowitt_failures_{0};

  // last_seq_: assigned, main task only. written_seq_: on the card; also read by the HTTP task.
  uint32_t last_seq_[STREAM_COUNT]{0, 0};
  std::atomic<uint32_t> written_seq_[STREAM_COUNT]{};
  int64_t newest_block_[STREAM_COUNT]{-1, -1};
  bool needs_newline_[STREAM_COUNT]{false, false};
  std::deque<Pending> queue_;

  binary_sensor::BinarySensor *sd_fault_{nullptr};
  sensor::Sensor *free_space_{nullptr};
  text_sensor::TextSensor *clock_source_{nullptr};
  sensor::Sensor *node_records_{nullptr};
  sensor::Sensor *ecowitt_records_{nullptr};
  sensor::Sensor *ecowitt_failures_sensor_{nullptr};
};

}  // namespace creek_store
}  // namespace esphome
```

- [ ] **Step 4: Wire it into `gateway.base.yaml`**

Delete the `includes:` list (and its comment) under `esphome:`; `creek_store.h` now includes `store_core.h`, which the lambda needs. Add after the `rfm69_gateway:` block:

```yaml
# The store (components/creek_store/): every node packet and Ecowitt reading on SD, replayed
# to the add-on over GET /store/... so it can backfill whatever Home Assistant missed.
creek_store:
  id: store
  radio_id: creek_radio
  time_id: sntp_time
  mount_height_id: mount_height_mm
  i2c_id: i2c_bus
  address: 0x68
  sd_cs_pin: ${sd_cs_pin}
  token: !secret creek_store_token
  ecowitt_host: ${ecowitt_host}
  ecowitt_interval: 60s
  blanking_mm: ${blanking_mm}
  overrange_slack_mm: ${overrange_slack_mm}
  sd_fault:
    name: Store SD Fault
  free_space:
    name: Store Free Space
  clock_source:
    name: Store Clock Source
  node_records:
    name: Store Node Records
  ecowitt_records:
    name: Store Ecowitt Records
  ecowitt_failures:
    name: Ecowitt Poll Failures
```

- [ ] **Step 5: Compile v2, then re-check v1**

```bash
cd firmware/esp32s3_feather_gateway && esphome compile gateway.yaml
grep -c USE_RFM69_PACKET_HOOK .esphome/build/creek-gateway-v2/src/esphome/core/defines.h
cd ../esp32_rfm69_gateway && esphome compile gateway.yaml && (grep -c USE_RFM69_PACKET_HOOK .esphome/build/creek-gateway/src/esphome/core/defines.h || true)
```
Expected: v2 compiles with count `1`; v1 compiles with count `0`.

- [ ] **Step 6: Commit**

```bash
git add firmware/esp32s3_feather_gateway/components/creek_store/__init__.py firmware/esp32s3_feather_gateway/components/creek_store/creek_store.h firmware/esp32s3_feather_gateway/gateway.base.yaml
git commit -m "creek_store: RTC-backed clock, seq-numbered SD log of node packets

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 5: Ecowitt poller

**Files:**
- Modify: `firmware/esp32s3_feather_gateway/components/creek_store/creek_store.h`

**Interfaces:**
- Consumes: `creek_core::apply_ecowitt_item`, `leading_float`, `encode_ecowitt_record`; `enqueue_`, `ts_src_`, `now_ts_` (Task 4).
- Produces: Ecowitt records in `/ecowitt/NNNNNN.ndjson` and the `Ecowitt Poll Failures` count.

- [ ] **Step 1: Replace the two stubs and add the poll task**

Add includes after `<SD.h>`:
```cpp
#include <HTTPClient.h>
#include <WiFiClient.h>
```
Above `class CreekStore`, add:
```cpp
class CreekStore;
inline void ecowitt_task_trampoline(void *arg);
```
Replace `void setup_ecowitt_() {}` and `void collect_ecowitt_() {}` with:

```cpp
  // The GW3000B is polled on its own task: an HTTP GET can block for seconds, and the main
  // task is the one servicing the radio. This reads the console's local API, so HA's Ecowitt
  // webhook path is untouched.
  void setup_ecowitt_() {
    if (this->ecowitt_host_.empty()) return;
    if (xTaskCreate(ecowitt_task_trampoline, "ecowitt", 8192, this, 1, nullptr) != pdPASS)
      ESP_LOGE(TAG, "Could not start the Ecowitt poll task");
  }

  // Main task: hand the latest poll result to the store queue.
  void collect_ecowitt_() {
    creek_core::EcowittReading r;
    {
      std::lock_guard<std::mutex> lock(this->eco_mutex_);
      if (!this->eco_ready_) return;
      this->eco_ready_ = false;
      if (!this->eco_ok_) {
        this->ecowitt_failures_++;
        return;
      }
      r = this->eco_reading_;
    }
    const uint32_t seq = ++this->last_seq_[ECOWITT];
    this->enqueue_(ECOWITT, seq,
                   creek_core::encode_ecowitt_record(seq, now_ts_(), this->ts_src_(), r));
  }

  bool poll_ecowitt_(const std::string &url, creek_core::EcowittReading &out) {
    WiFiClient client;
    HTTPClient http;
    http.setTimeout(5000);
    if (!http.begin(client, url.c_str())) return false;
    const int code = http.GET();
    if (code != 200) {
      http.end();
      ESP_LOGD(TAG, "Ecowitt poll: HTTP %d", code);
      return false;
    }
    const String body = http.getString();
    http.end();
    return json::parse_json(std::string(body.c_str()), [&out](JsonObject root) -> bool {
      for (JsonObject item : root["common_list"].as<JsonArray>())
        creek_core::apply_ecowitt_item(out, item["id"] | "", item["val"] | "", item["unit"] | "");
      for (JsonObject item : root["rain"].as<JsonArray>())
        creek_core::apply_ecowitt_item(out, item["id"] | "", item["val"] | "", item["unit"] | "");
      for (JsonObject ch : root["ch_soil"].as<JsonArray>()) {
        const auto c = creek_core::leading_float(ch["channel"] | "");
        const auto h = creek_core::leading_float(ch["humidity"] | "");
        if (c && h) out.soil.emplace_back((int) *c, *h);
      }
      return true;
    });
  }

 public:
  void ecowitt_task() {
    const std::string url = "http://" + this->ecowitt_host_ + "/get_livedata_info";
    for (;;) {
      creek_core::EcowittReading r;
      const bool ok = this->poll_ecowitt_(url, r);
      {
        std::lock_guard<std::mutex> lock(this->eco_mutex_);
        this->eco_reading_ = r;
        this->eco_ok_ = ok;
        this->eco_ready_ = true;
      }
      vTaskDelay(pdMS_TO_TICKS(this->ecowitt_interval_ms_));
    }
  }

 protected:
```

Add members next to the other state:
```cpp
  std::mutex eco_mutex_;
  bool eco_ready_{false};
  bool eco_ok_{false};
  creek_core::EcowittReading eco_reading_;
```

After the class, before `}  // namespace creek_store`:
```cpp
inline void ecowitt_task_trampoline(void *arg) { static_cast<CreekStore *>(arg)->ecowitt_task(); }
```

- [ ] **Step 2: Compile**

```bash
cd firmware/esp32s3_feather_gateway && esphome compile gateway.yaml
```
Expected: success. If `WiFiClient.h` is not found, add `cg.add_library("WiFi", None)` and `cg.add_library("Network", None)` to `__init__.py`'s `to_code` and recompile.

- [ ] **Step 3: Commit**

```bash
git add firmware/esp32s3_feather_gateway/components/creek_store/
git commit -m "creek_store: poll the GW3000B local API and log Ecowitt readings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 6: Replay HTTP API

**Files:**
- Modify: `firmware/esp32s3_feather_gateway/components/creek_store/creek_store.h`

**Interfaces:**
- Produces the HTTP contract that Task 10's `StoreClient` consumes:
  - `GET /store/status` → 200 `application/json` `{"store_schema":1,"device":"creek-gateway-v2","now":<epoch>,"ts_src":"ntp|rtc|none","sd_ok":true,"sd_free_mb":N,"streams":{"node":{"first":F,"last":L},"ecowitt":{"first":F,"last":L}}}`, where `last` is the last seq **on the card**.
  - `GET /store/records?stream=node|ecowitt&after=<seq>&limit=<1..500>` → 200 `application/x-ndjson`: records with `seq > after`, ascending, at most `limit` lines and about 32 KB. Header `X-Store-Last: <last seq on card>`.
  - Missing or wrong `Authorization: Bearer <token>` → 401. Bus busy for more than 3 s → 503. Unknown stream → 400. SD not mounted → 503 on `/store/records`. Any other `/store/` path → 404.

- [ ] **Step 1: Make the class a web handler and replace `setup_http_`**

Add `#include "esphome/components/web_server_base/web_server_base.h"`. Change the class declaration to:
```cpp
class CreekStore : public Component, public i2c::I2CDevice, public AsyncWebHandler {
```
Add constants beside the others:
```cpp
static const size_t PAGE_MAX_BYTES = 32 * 1024;
static const uint32_t PAGE_MAX_LINES = 500;
static const TickType_t HTTP_BUS_WAIT = pdMS_TO_TICKS(3000);
static const size_t SEEK_RESOLUTION = 512;
```
Replace `void setup_http_() {}` with:

```cpp
  void setup_http_() {
    web_server_base::global_web_server_base->init();
    web_server_base::global_web_server_base->add_handler(this);
  }

  // Bus mutex held.
  std::string status_json_() {
    char buf[384];
    snprintf(buf, sizeof buf,
             "{\"store_schema\":1,\"device\":\"%s\",\"now\":%.0f,\"ts_src\":\"%s\","
             "\"sd_ok\":%s,\"sd_free_mb\":%u,\"streams\":{"
             "\"node\":{\"first\":%u,\"last\":%u},\"ecowitt\":{\"first\":%u,\"last\":%u}}}",
             this->device_name_.c_str(), now_ts_(), this->ts_src_(),
             this->sd_ok_ ? "true" : "false", (unsigned) this->free_mb_,
             this->first_seq_(NODE), (unsigned) this->written_seq_[NODE],
             this->first_seq_(ECOWITT), (unsigned) this->written_seq_[ECOWITT]);
    return buf;
  }

  // The store never deletes, so the first record is seq 1 once anything has been written.
  unsigned first_seq_(uint8_t s) const { return this->written_seq_[s] > 0 ? 1 : 0; }

  // Bus mutex held. Byte offset at or before the first line whose seq > after: binary search
  // on byte offsets, resyncing to the next newline at each probe. A torn line counts as
  // "> after", which only makes the linear scan that follows start a little early.
  size_t offset_after_(File &f, uint32_t after) {
    size_t lo = 0, hi = f.size();
    while (hi - lo > SEEK_RESOLUTION) {
      const size_t mid = lo + (hi - lo) / 2;
      f.seek(mid);
      f.readStringUntil('\n');
      const size_t line_start = f.position();
      const String line = f.readStringUntil('\n');
      const auto seq = creek_core::parse_seq(line.c_str());
      if (seq && *seq <= after) lo = line_start;
      else hi = mid;
    }
    return lo;
  }

  // Bus mutex held.
  std::string read_page_(uint8_t s, uint32_t after, uint32_t limit) {
    std::string body;
    if (this->newest_block_[s] < 0) return body;
    uint32_t n = 0;
    bool first = true;
    for (uint32_t b = creek_core::block_of(after + 1);
         (int64_t) b <= this->newest_block_[s] && n < limit && body.size() < PAGE_MAX_BYTES; b++) {
      File f = SD.open(creek_core::block_path(STREAM_NAMES[s], b).c_str());
      if (!f) continue;
      if (first) {
        f.seek(this->offset_after_(f, after));
        first = false;
      }
      while (f.available() && n < limit && body.size() < PAGE_MAX_BYTES) {
        const String line = f.readStringUntil('\n');
        const auto seq = creek_core::parse_seq(line.c_str());
        if (!seq || *seq <= after) continue;
        body += line.c_str();
        body += '\n';
        n++;
      }
      f.close();
    }
    return body;
  }

 public:
  // Both run on the async TCP task, not the main task.
  bool canHandle(AsyncWebServerRequest *request) const override {
    return request->url().startsWith("/store/");
  }

  void handleRequest(AsyncWebServerRequest *request) override {
    const AsyncWebHeader *auth = request->getHeader("Authorization");
    const std::string expected = "Bearer " + this->token_;
    if (auth == nullptr || std::string(auth->value().c_str()) != expected) {
      request->send(401, "text/plain", "unauthorized");
      return;
    }
    // The card shares the radio's bus. A node OTA push holds it for minutes, so give up after
    // 3 s rather than tie up the TCP task; the add-on retries next pass.
    if (xSemaphoreTake(this->bus_, HTTP_BUS_WAIT) != pdTRUE) {
      request->send(503, "text/plain", "bus busy");
      return;
    }
    if (request->url() == "/store/status") {
      const std::string body = this->status_json_();
      xSemaphoreGive(this->bus_);
      request->send(200, "application/json", body.c_str());
      return;
    }
    if (request->url() == "/store/records") {
      const std::string name =
          request->hasParam("stream") ? request->getParam("stream")->value().c_str() : "";
      const int s = name == "node" ? NODE : name == "ecowitt" ? ECOWITT : -1;
      if (s < 0 || !this->sd_ok_) {
        xSemaphoreGive(this->bus_);
        if (s < 0) request->send(400, "text/plain", "unknown stream");
        else request->send(503, "text/plain", "sd unavailable");
        return;
      }
      const uint32_t after = request->hasParam("after")
          ? (uint32_t) strtoul(request->getParam("after")->value().c_str(), nullptr, 10) : 0;
      uint32_t limit = request->hasParam("limit")
          ? (uint32_t) strtoul(request->getParam("limit")->value().c_str(), nullptr, 10)
          : PAGE_MAX_LINES;
      if (limit == 0 || limit > PAGE_MAX_LINES) limit = PAGE_MAX_LINES;
      const std::string body = this->read_page_((uint8_t) s, after, limit);
      const uint32_t last = this->written_seq_[s];
      xSemaphoreGive(this->bus_);
      AsyncWebServerResponse *resp =
          request->beginResponse(200, "application/x-ndjson", body.c_str());
      resp->addHeader("X-Store-Last", String(last));
      request->send(resp);
      return;
    }
    xSemaphoreGive(this->bus_);
    request->send(404, "text/plain", "not found");
  }

 protected:
```

- [ ] **Step 2: Compile**

```bash
cd firmware/esp32s3_feather_gateway && esphome compile gateway.yaml
```
Expected: success. If `canHandle` is reported as not overriding anything, copy the exact `canHandle`/`handleRequest` signatures from the installed `esphome/components/web_server/web_server.h` (2026.7.3: `bool canHandle(AsyncWebServerRequest *request) const override;`).

- [ ] **Step 3: Commit**

```bash
git add firmware/esp32s3_feather_gateway/components/creek_store/creek_store.h
git commit -m "creek_store: token-protected /store/status and /store/records replay API

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 7: CI for the firmware, and the firmware README

**Files:**
- Modify: `.github/workflows/tests.yml`
- Modify: `firmware/README.md` (new section `## Gateway v2 (Feather ESP32-S3 + SD store)` before `## OTA Firmware Updates`)

- [ ] **Step 1: Add the `firmware` job and the YAML-validation entries**

In `.github/workflows/tests.yml`, add these two paths to the list in "Validate YAML and shell syntax":
```python
                    "firmware/esp32s3_feather_gateway/gateway.base.yaml",
                    "firmware/esp32s3_feather_gateway/ota-push.yaml",
```
Append a second job under `jobs:`:

```yaml
  firmware:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: store_core host tests
        # creek_store's pure logic (record format, stage math, seq recovery, clock) runs on
        # the host; the device-only parts are covered by the compile below and the bench.
        run: |
          g++ -std=c++17 -Wall -Wextra -Werror \
            -I firmware/esp32s3_feather_gateway/components/creek_store \
            firmware/esp32s3_feather_gateway/tests/test_store_core.cpp -o /tmp/test_store_core
          /tmp/test_store_core

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install ESPHome
        # Pinned to the version the gateways are built with locally and in the Device Builder.
        run: pip install esphome==2026.7.3

      - name: Compile v1 and v2
        run: |
          for d in esp32_rfm69_gateway esp32s3_feather_gateway; do
            cp firmware/$d/secrets.yaml.example firmware/$d/secrets.yaml
          done
          # v1's example has no creek_gateway_api_key of the right shape for v2 and vice versa;
          # each example is complete for its own config.
          esphome compile firmware/esp32_rfm69_gateway/gateway.yaml
          esphome compile firmware/esp32s3_feather_gateway/gateway.yaml

      - name: v1 build does not contain the v2 packet hook
        # The shared rfm69_gateway component gained a hook for v2. It must compile out of v1,
        # whose Device Builder config pulls the component from main.
        run: |
          if grep -q USE_RFM69_PACKET_HOOK firmware/esp32_rfm69_gateway/.esphome/build/creek-gateway/src/esphome/core/defines.h; then
            echo "::error::v1 build defines USE_RFM69_PACKET_HOOK"; exit 1
          fi
          grep -q USE_RFM69_PACKET_HOOK firmware/esp32s3_feather_gateway/.esphome/build/creek-gateway-v2/src/esphome/core/defines.h
```

Check that v1's `secrets.yaml.example` has every key v1 reads (`wifi_ssid`, `wifi_password`, `ap_password`, `creek_gateway_api_key`, `rfm69_encrypt_key`). It does as of this plan; `creek_gateway_api_key: "base64-32-bytes-here"` is not valid base64-32. Run `esphome config firmware/esp32_rfm69_gateway/gateway.yaml` locally with the example copied in. If validation rejects the key, change **only the example value** in both examples to a real-shaped dummy, e.g. `"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="`. Changing an example file does not change v1's firmware.

- [ ] **Step 2: Run the host test and both compiles locally**

```bash
g++ -std=c++17 -Wall -Wextra -Werror -I firmware/esp32s3_feather_gateway/components/creek_store firmware/esp32s3_feather_gateway/tests/test_store_core.cpp -o /tmp/test_store_core && /tmp/test_store_core
esphome compile firmware/esp32_rfm69_gateway/gateway.yaml && esphome compile firmware/esp32s3_feather_gateway/gateway.yaml
```
Expected: `all store_core tests passed`, and two successful compiles.

- [ ] **Step 3: Write the README section**

Add to `firmware/README.md`, before `## OTA Firmware Updates`:

````markdown
## Gateway v2 (Feather ESP32-S3 + SD store)

`esp32s3_feather_gateway/` is a second gateway built to replace the XIAO after a side-by-side
trial ([trial runbook](../docs/gateway-v2-trial.md),
[design](../docs/superpowers/specs/2026-10-07-gateway-v2-sd-backfill-design.md)). It does
everything v1 does, and it also keeps a record: every node packet and every Ecowitt reading
goes to the Adalogger's microSD with a real timestamp from its PCF8523 RTC. When Home
Assistant or WiFi comes back after an outage, the add-on reads the gap back over HTTP and
writes it into HA's history at the right times.

| Part | Adafruit PID |
|---|---|
| ESP32-S3 Feather, 8 MB flash, w.FL antenna (no PSRAM) | 5885 |
| Radio FeatherWing RFM69HCW 900 MHz | 3229 |
| Adalogger FeatherWing (PCF8523 + microSD) | 2922 |

Wiring is in `esp32s3_feather_gateway/gateway.base.yaml`. The RFM69 wing's CS/IRQ/RST are
solder-jumper pads: CS → D6 (GPIO6), IRQ → D5 (GPIO5), RST → D9 (GPIO9). The Adalogger's SD
CS is D10 (GPIO10) by default. Fit a CR1220 in the Adalogger, or the clock is lost on every
power cut.

**Build:** `esphome run esp32s3_feather_gateway/gateway.yaml` (trial: no node-OTA buttons).
`creek-gateway-v2.yaml` is the Device Builder trial wrapper, and `creek-gateway-v2.prod.yaml`
adds the OTA buttons back after cutover. `rfm69_gateway` is shared with v1 from
`esp32_rfm69_gateway/components/`. Its v2-only packet hook compiles in only when
`creek_store` defines `USE_RFM69_PACKET_HOOK`, and CI checks v1's build never does.

**On the card:** `/node/NNNNNN.ndjson` and `/ecowitt/NNNNNN.ndjson`, one JSON record per
line, 10 000 records per file (`000001.ndjson` holds seq 10000–19999). Files are named by
sequence, not date, so a record written before the clock is known can't land out of order.
Each record has `ts_src`: `ntp` (synced within 24 h), `rtc` (RTC only), or `none` (no
trustworthy time; the add-on skips these). Nothing is ever deleted. At the worst case of 5 s
fast mode all day that's about 3.5 MB/day.

**Replay API** (port 80, `Authorization: Bearer <creek_store_token>`):
`GET /store/status`, `GET /store/records?stream=node|ecowitt&after=<seq>&limit=<≤500>`.
A quick look from a laptop:

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://<v2-ip>/store/status
curl -s -H "Authorization: Bearer $TOKEN" "http://<v2-ip>/store/records?stream=node&after=0&limit=3"
```
````

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/tests.yml firmware/README.md firmware/esp32_rfm69_gateway/secrets.yaml.example firmware/esp32s3_feather_gateway/secrets.yaml.example
git commit -m "CI: store_core host tests, compile v1 and v2, check v1 has no packet hook

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 8: Bench bring-up (hardware — needs the user)

This task runs on real hardware. Every check has an expected observation. Record what you observe in the commit message, or in a short `docs/gateway-v2-trial.md` "Bench log" section if Task 21 already created it.

**Files:** none changed unless a check fails (then fix in the relevant file and note it).

- [ ] **Step 1: Verify pins against the board before soldering**

Open Adafruit's ESP32-S3 Feather pinout (https://learn.adafruit.com/adafruit-esp32-s3-feather/pinouts, "Pinouts" PDF). Confirm: SCK=GPIO36, MOSI=GPIO35, MISO=GPIO37, D5=GPIO5, D6=GPIO6, D9=GPIO9, D10=GPIO10, SDA=GPIO3, SCL=GPIO4, I2C_POWER=GPIO7. If any differs, update the substitutions in `gateway.base.yaml` and the README table. Then solder the RFM69 wing's CS/IRQ/RST jumpers to D6/D5/D9 and fit a CR1220 in the Adalogger.

- [ ] **Step 2: Reserve an IP**

Add a static DHCP mapping on the IoT VLAN for the Feather's MAC (shown in the first boot log) at **192.168.30.21**, through OPNsense (it can be done through the opnsense MCP with the user's go-ahead). Expected: the boot log shows `IP Address: 192.168.30.21` after a reboot.

- [ ] **Step 3: First flash over USB and read the boot log**

```bash
cd firmware/esp32s3_feather_gateway && esphome run gateway.yaml --device COMx
```
Expected log lines, in order:
- `i2c` scan: `Found i2c device at address 0x68` (the PCF8523; also 0x36 or 0x0B for the Feather's battery monitor).
- `RFM69 ready on band 91, listening for creek node packets`.
- `creek_store`: `node stream: last seq 0 (newest block -1)` on a blank card.
- Within about 60 s, an `RX [1] RSSI=...` line from the node, then `Store Node Records` = 1.

If `0x68` is missing, I2C_POWER isn't high: check the `on_boot` priority, then measure GPIO7.

- [ ] **Step 4: Clock**

Wait for `sntp` to sync (log: `Synchronized time`). Then power-cycle with WiFi off: temporarily change the `wifi_ssid` secret to a wrong value, flash, and boot. Expected: `System clock restored from PCF8523: <correct UTC>` and `Store Clock Source` = `rtc`. Restore the secret afterwards.

- [ ] **Step 5: Replay API**

```bash
TOKEN=<creek_store_token>
curl -s -H "Authorization: Bearer $TOKEN" http://192.168.30.21/store/status
curl -s -H "Authorization: Bearer $TOKEN" "http://192.168.30.21/store/records?stream=node&after=0&limit=3"
curl -s -o /dev/null -w "%{http_code}\n" http://192.168.30.21/store/status
```
Expected: a JSON status with `"store_schema":1` and non-zero `last`; three NDJSON lines starting `{"seq":1,`; `401`.

Also from the HA host (the add-on's network position), using the SSH/Terminal add-on: `curl -s -H "Authorization: Bearer $TOKEN" http://192.168.30.21/store/status`. **If this times out but works from the laptop**, OPNsense blocks USER→IoT port 80 for the HA host. Add a pass rule `192.168.20.3 → 192.168.30.21 TCP 80` on the USER interface, after confirming with the user.

- [ ] **Step 6: Ecowitt**

Within about 2 min of boot, `Store Ecowitt Records` ≥ 1, and `curl ... "...records?stream=ecowitt&after=0&limit=1"` shows `rain_year_in` equal to HA's `sensor.outside_weather_station_rain_total` (29.84 on 2026-10-07) and the soil channel(s). Note **which Ecowitt channel number each WH51 is**: the GW3000B web UI lists them. Task 21's entity maps need it.

- [ ] **Step 7: Torn-line recovery**

Pull USB power mid-operation 3 times, at random moments. Expected after each boot: `last seq N` continues from before (no reset to 0). `curl ...records?after=<N-5>` returns monotonically increasing seqs with no malformed lines.

- [ ] **Step 8: SPI sharing under a node OTA push**

v1 must be unplugged for this one step: two gateways answering the handshake would collide. Flash a build that includes `ota-push.yaml` (temporarily add it to `gateway.yaml`'s `packages:`). Press "Push Node Firmware" while running `while true; do curl -s -H "Authorization: Bearer $TOKEN" http://192.168.30.21/store/status; sleep 1; done`. Expected: the OTA status reaches `done`, curl gets `503 bus busy` during the transfer and 200 otherwise, and after the transfer the node records resume with no seq gap. Revert `gateway.yaml` and plug v1 back in.

- [ ] **Step 9: Commit the bench notes (and any pin fixes)**

```bash
git add -A firmware/esp32s3_feather_gateway firmware/README.md
git commit -m "gateway v2: bench bring-up verified (pins, RTC, SD, API, Ecowitt, OTA sharing)

<one line per step with what was observed>

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 9: Add-on options, recorder mount and dependency

> As in every add-on task: tests added to an existing `tests/test_x.py` go **above `def main():`**, or `main()` never sees them.

**Files:**
- Modify: `rate_of_rise/app/config.py` (dataclass fields + `load()`)
- Modify: `rate_of_rise/config.yaml` (`map:` and `schema:`)
- Modify: `rate_of_rise/requirements.txt`
- Test: `rate_of_rise/tests/test_config.py` (add tests)

**Interfaces:**
- Produces `Config` fields: `gateway_store_url: str = ""`, `gateway_store_token: str = ""`, `backfill_entity_map: str = ""`, `backfill_shadow_map: str = ""`, `ha_ws_url: str = "ws://supervisor/core/websocket"`.

- [ ] **Step 1: Write the failing tests**

Append to `rate_of_rise/tests/test_config.py` (follow the file's existing pattern for writing a temporary `options.json` and calling `Config.load()`; reuse its helper if it has one, e.g. `_load_with(opts)`). If no helper exists, add this one:

```python
def _load_with(opts: dict):
    import json, os, tempfile
    from pathlib import Path
    from app import config as config_mod
    d = Path(tempfile.mkdtemp())
    (d / "options.json").write_text(json.dumps(opts), encoding="utf-8")
    old = config_mod._OPTIONS_JSON
    config_mod._OPTIONS_JSON = d / "options.json"
    try:
        return config_mod.Config.load()
    finally:
        config_mod._OPTIONS_JSON = old


def test_backfill_options_default_off():
    cfg = _load_with({})
    assert cfg.gateway_store_url == ""
    assert cfg.gateway_store_token == ""
    assert cfg.backfill_entity_map == ""
    assert cfg.backfill_shadow_map == ""
    assert cfg.ha_ws_url == "ws://supervisor/core/websocket"


def test_backfill_options_are_read_and_trimmed():
    cfg = _load_with({"gateway_store_url": " http://192.168.30.21/ ",
                      "gateway_store_token": "abc", "backfill_entity_map": '{"node": {}}',
                      "backfill_shadow_map": None})
    assert cfg.gateway_store_url == "http://192.168.30.21/"
    assert cfg.gateway_store_token == "abc"
    assert cfg.backfill_entity_map == '{"node": {}}'
    assert cfg.backfill_shadow_map == ""
```

Check how `_options()` reads the file in `config.py` before relying on `_OPTIONS_JSON`. If `_options()` reads a path captured at import time some other way, patch that instead.

- [ ] **Step 2: Run to verify failure**

Run: `python rate_of_rise/tests/test_config.py`
Expected: `AttributeError: 'Config' object has no attribute 'gateway_store_url'`.

- [ ] **Step 3: Implement**

In the `Config` dataclass, after `onsite_temp_entity`:

```python
    # Gateway v2 store backfill (app/backfill/). Blank URL = off: no thread, no requests.
    # The token is the gateway's creek_store_token. The maps are JSON text (the add-on
    # options form has no dict type): {"node": {"stage_ft": "sensor...."}, "ecowitt": {...}}.
    # entity_map entities are WRITTEN into HA's recorder; shadow_map entities are only
    # logged, which is how the trial rehearses production writes without making them.
    gateway_store_url: str = ""
    gateway_store_token: str = ""
    backfill_entity_map: str = ""
    backfill_shadow_map: str = ""
```

After `supervisor_token`:
```python
    ha_ws_url: str = "ws://supervisor/core/websocket"
```

In `load()`, after `onsite_temp_entity=...`:
```python
            gateway_store_url=(opts.get("gateway_store_url") or "").strip(),
            gateway_store_token=(opts.get("gateway_store_token") or "").strip(),
            backfill_entity_map=(opts.get("backfill_entity_map") or "").strip(),
            backfill_shadow_map=(opts.get("backfill_shadow_map") or "").strip(),
```

In `config.yaml`, add under `map:`:
```yaml
  - homeassistant_config:rw  # backfill writes past readings into home-assistant_v2.db
```
and under `schema:` (after `onsite_temp_entity: str?`):
```yaml
  gateway_store_url: str?
  gateway_store_token: password?
  backfill_entity_map: str?
  backfill_shadow_map: str?
```

In `requirements.txt`, append:
```
# Backfill imports hourly statistics through HA's websocket API (app/backfill/statistics.py).
websocket-client>=1.8
```

- [ ] **Step 4: Run tests**

Run: `pip install "websocket-client>=1.8" && python rate_of_rise/tests/test_config.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add rate_of_rise/app/config.py rate_of_rise/config.yaml rate_of_rise/requirements.txt rate_of_rise/tests/test_config.py
git commit -m "add-on: gateway store backfill options (off by default), recorder mount

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 10: `StoreClient` — feature detection and paging

**Files:**
- Create: `rate_of_rise/app/backfill/__init__.py` (empty docstring module for now; Task 19 fills it)
- Create: `rate_of_rise/app/backfill/client.py`
- Test: `rate_of_rise/tests/test_backfill_client.py`

**Interfaces:**
- Produces: `ProbeState` enum (`OK`, `NO_STORE`, `UNREACHABLE`, `BAD_TOKEN`); `Probe(state: ProbeState, status: dict)`; `StoreClient(base_url: str, token: str, session=None, timeout: float = 10.0)` with `probe() -> Probe` and `records(stream: str, after: int, max_records: int) -> list[dict]` (raises `requests.RequestException` on network/HTTP failure). Constants `STORE_SCHEMA = 1`, `PAGE_LIMIT = 500`.

- [ ] **Step 1: Write the failing tests**

`rate_of_rise/tests/test_backfill_client.py`:

```python
"""StoreClient: talks to a v2 gateway's store, and stays silent about everything else.

The same option can point at a v2 gateway, a v1 gateway (no store), a gateway that is down,
or nothing. Only the first is a path to backfill; all the others must look exactly like
backfill not existing: no exception, nothing logged above DEBUG.

Run: python rate_of_rise/tests/test_backfill_client.py
"""
import json
import logging
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from app.backfill.client import ProbeState, StoreClient  # noqa: E402

TOKEN = "t" * 32


class Gateway(BaseHTTPRequestHandler):
    mode = "v2"          # v2 | 404 | html | sleep | wrongschema
    records = {"node": [], "ecowitt": []}
    page_size = 500

    def log_message(self, *_):
        pass

    def _send(self, code, body, ctype="application/json", headers=None):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        mode = type(self).mode
        if mode == "sleep":
            time.sleep(1.0)
        if mode == "404":
            return self._send(404, "not found", "text/plain")
        if mode == "html":
            return self._send(200, "<html><body>ESPHome</body></html>", "text/html")
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, "unauthorized", "text/plain")
        recs = type(self).records
        if self.path.startswith("/store/status"):
            schema = 2 if mode == "wrongschema" else 1
            return self._send(200, json.dumps({
                "store_schema": schema, "device": "creek-gateway-v2", "now": 0, "ts_src": "ntp",
                "sd_ok": True, "sd_free_mb": 1,
                "streams": {s: {"first": 1 if r else 0, "last": r[-1]["seq"] if r else 0}
                            for s, r in recs.items()}}))
        if self.path.startswith("/store/records"):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            stream, after, limit = q["stream"][0], int(q["after"][0]), int(q["limit"][0])
            limit = min(limit, type(self).page_size)
            page = [r for r in recs[stream] if r["seq"] > after][:limit]
            body = "".join(json.dumps(r) + "\n" for r in page)
            body += '{"seq":99999,"ts":1'   # a torn tail must be ignored, not crash
            last = recs[stream][-1]["seq"] if recs[stream] else 0
            return self._send(200, body, "application/x-ndjson", {"X-Store-Last": str(last)})
        return self._send(404, "not found", "text/plain")


def serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class Loud(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def quietly(fn):
    """Run fn and assert nothing under app.* logged at INFO or above."""
    h = Loud()
    lg = logging.getLogger("app")
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)
    try:
        out = fn()
    finally:
        lg.removeHandler(h)
    assert not h.records, [r.getMessage() for r in h.records]
    return out


def free_port_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def test_v2_store_probes_ok():
    Gateway.mode = "v2"
    Gateway.records = {"node": [{"seq": 1, "ts": 1.0}], "ecowitt": []}
    srv, url = serve()
    try:
        probe = StoreClient(url, TOKEN).probe()
        assert probe.state is ProbeState.OK
        assert probe.status["streams"]["node"]["last"] == 1
    finally:
        srv.shutdown()


def test_v1_gateway_404_is_no_store_and_silent():
    Gateway.mode = "404"
    srv, url = serve()
    try:
        assert quietly(lambda: StoreClient(url, TOKEN).probe()).state is ProbeState.NO_STORE
    finally:
        srv.shutdown()


def test_esphome_html_page_is_no_store_and_silent():
    Gateway.mode = "html"
    srv, url = serve()
    try:
        assert quietly(lambda: StoreClient(url, TOKEN).probe()).state is ProbeState.NO_STORE
    finally:
        srv.shutdown()


def test_unknown_store_schema_is_no_store():
    Gateway.mode = "wrongschema"
    srv, url = serve()
    try:
        assert quietly(lambda: StoreClient(url, TOKEN).probe()).state is ProbeState.NO_STORE
    finally:
        srv.shutdown()


def test_connection_refused_is_unreachable_and_silent():
    probe = quietly(lambda: StoreClient(free_port_url(), TOKEN).probe())
    assert probe.state is ProbeState.UNREACHABLE


def test_timeout_is_unreachable_and_silent():
    Gateway.mode = "sleep"
    srv, url = serve()
    try:
        probe = quietly(lambda: StoreClient(url, TOKEN, timeout=0.2).probe())
        assert probe.state is ProbeState.UNREACHABLE
    finally:
        srv.shutdown()


def test_bad_token():
    Gateway.mode = "v2"
    srv, url = serve()
    try:
        assert StoreClient(url, "wrong" * 4).probe().state is ProbeState.BAD_TOKEN
    finally:
        srv.shutdown()


def test_records_pages_until_last_and_skips_torn_lines():
    Gateway.mode = "v2"
    Gateway.page_size = 3
    Gateway.records = {"node": [{"seq": i, "ts": float(i)} for i in range(1, 9)], "ecowitt": []}
    srv, url = serve()
    try:
        recs = StoreClient(url, TOKEN).records("node", after=2, max_records=100)
        assert [r["seq"] for r in recs] == [3, 4, 5, 6, 7, 8]
        capped = StoreClient(url, TOKEN).records("node", after=0, max_records=4)
        assert [r["seq"] for r in capped] == [1, 2, 3, 4]
    finally:
        Gateway.page_size = 500
        srv.shutdown()


def test_records_network_failure_raises():
    try:
        StoreClient(free_port_url(), TOKEN).records("node", 0, 10)
    except requests.RequestException:
        return
    raise AssertionError("expected RequestException")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python rate_of_rise/tests/test_backfill_client.py`
Expected: `ModuleNotFoundError: No module named 'app.backfill'`.

- [ ] **Step 3: Implement**

`rate_of_rise/app/backfill/__init__.py`:
```python
"""Backfill from the v2 gateway's SD store into HA's recorder, the stage log and the dataset.

See docs/superpowers/specs/2026-10-07-gateway-v2-sd-backfill-design.md.
"""
```

`rate_of_rise/app/backfill/client.py`:
```python
"""Client for the v2 gateway's store API (firmware/esp32s3_feather_gateway, creek_store).

Feature detection is the point of this module. The `gateway_store_url` option can point at a
v2 gateway, at a v1 gateway (no store), at a gateway that is down, or at nothing, and only
the first is a path to backfill. Everything else must leave the add-on behaving as it did
before backfill existed: no exception escapes probe(), and nothing here logs above DEBUG.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum

import requests

log = logging.getLogger("app.backfill.client")

STORE_SCHEMA = 1
PAGE_LIMIT = 500
TIMEOUT_S = 10.0


class ProbeState(Enum):
    OK = "ok"
    NO_STORE = "no_store"          # answered, but not a v2 store: v1, or web server off
    UNREACHABLE = "unreachable"    # refused / timed out / DNS: gateway or network down
    BAD_TOKEN = "bad_token"


@dataclass
class Probe:
    state: ProbeState
    status: dict = field(default_factory=dict)


class StoreClient:
    def __init__(self, base_url: str, token: str, session: requests.Session | None = None,
                 timeout: float = TIMEOUT_S):
        self._url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = session or requests.Session()
        self._headers = {"Authorization": f"Bearer {token}"}

    def probe(self) -> Probe:
        try:
            r = self._session.get(f"{self._url}/store/status", headers=self._headers,
                                  timeout=self._timeout)
        except requests.RequestException as exc:
            log.debug("gateway store unreachable: %s", exc)
            return Probe(ProbeState.UNREACHABLE)
        if r.status_code == 401:
            return Probe(ProbeState.BAD_TOKEN)
        if r.status_code != 200:
            log.debug("gateway store probe got HTTP %s: not a v2 store", r.status_code)
            return Probe(ProbeState.NO_STORE)
        try:
            body = r.json()
        except ValueError:
            log.debug("gateway store probe got a non-JSON 200: not a v2 store")
            return Probe(ProbeState.NO_STORE)
        if (not isinstance(body, dict) or body.get("store_schema") != STORE_SCHEMA
                or not isinstance(body.get("streams"), dict)):
            log.debug("gateway store probe: unrecognised status document")
            return Probe(ProbeState.NO_STORE)
        return Probe(ProbeState.OK, body)

    def records(self, stream: str, after: int, max_records: int) -> list[dict]:
        """Records with seq > after, ascending, at most `max_records`.

        Pages until a page reaches X-Store-Last or comes back empty. Lines that do not parse
        (a torn write on the card) are skipped. Network and HTTP errors raise
        requests.RequestException; the caller treats that as "try again next pass".
        """
        out: list[dict] = []
        cursor = after
        while len(out) < max_records:
            r = self._session.get(
                f"{self._url}/store/records", headers=self._headers, timeout=self._timeout,
                params={"stream": stream, "after": cursor,
                        "limit": min(PAGE_LIMIT, max_records - len(out))})
            r.raise_for_status()
            page = []
            for line in r.text.splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and isinstance(rec.get("seq"), int) and rec["seq"] > cursor:
                    page.append(rec)
            if not page:
                break
            room = max_records - len(out)
            out.extend(page[:room])
            cursor = out[-1]["seq"]
            try:
                last = int(r.headers.get("X-Store-Last", cursor))
            except ValueError:
                last = cursor
            if cursor >= last:
                break
        return out
```

- [ ] **Step 4: Run tests**

Run: `python rate_of_rise/tests/test_backfill_client.py`
Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
git add rate_of_rise/app/backfill/__init__.py rate_of_rise/app/backfill/client.py rate_of_rise/tests/test_backfill_client.py
git commit -m "backfill: store client with silent v1/unreachable feature detection

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 11: Units and the entity-map field registry

**Files:**
- Create: `rate_of_rise/app/backfill/units.py`
- Create: `rate_of_rise/app/backfill/entity_map.py`
- Test: `rate_of_rise/tests/test_backfill_entity_map.py`

**Interfaces:**
- Produces:
  - `units.convert(value: float, from_unit: str | None, to_unit: str | None) -> float` (raises `UnitMismatch`); `units.format_state(value: float) -> str`; `class UnitMismatch(ValueError)`.
  - `entity_map.MISSING` sentinel; `Point(ts: float, value: object)`; `FieldSpec(kind: str, unit: str | None, extract)` with kind ∈ `{"number","binary","text"}`; `field_spec(stream: str, field: str) -> FieldSpec` (raises `KeyError`); `parse_map(text: str) -> dict[str, dict[str, str]]` (raises `ValueError`); `points_for(stream: str, field: str, records: list[dict]) -> list[Point]`; `reset_cause_text(code: int) -> str`; `STREAMS = ("node", "ecowitt")`.

- [ ] **Step 1: Write the failing tests**

`rate_of_rise/tests/test_backfill_entity_map.py`:
```python
"""Units and the record-field registry the backfill writes from.

Run: python rate_of_rise/tests/test_backfill_entity_map.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backfill.entity_map import (  # noqa: E402
    MISSING, field_spec, parse_map, points_for, reset_cause_text)
from app.backfill.units import UnitMismatch, convert, format_state  # noqa: E402


def test_convert_lengths_voltage_rate_and_temperature():
    assert abs(convert(812, "mm", "in") - 31.968504) < 1e-6
    assert convert(4180, "mV", "V") == 4.18
    assert abs(convert(1.0, "in/h", "mm/h") - 25.4) < 1e-9
    assert abs(convert(45.9, "°F", "°C") - 7.722222) < 1e-5
    assert convert(-72, "dBm", "dBm") == -72
    assert convert(5, None, None) == 5


def test_convert_refuses_incompatible_units():
    try:
        convert(1.0, "mm", "kg")
    except UnitMismatch:
        return
    raise AssertionError("expected UnitMismatch")


def test_format_state_matches_ha_float_text():
    assert format_state(-59.0) == "-59.0"
    assert format_state(0.9583333333) == "0.958333"
    assert format_state(4.18) == "4.18"


def test_node_fields_extract_absent_null_and_values():
    rec = {"seq": 1, "ts": 10.0, "rssi": -72, "d": None, "v": 4012, "f": 1, "r": 1,
           "stage_ft": None}
    assert field_spec("node", "distance_mm").extract(rec) is None          # null: unknown
    assert field_spec("node", "cycle").extract(rec) is MISSING             # absent: skip
    assert field_spec("node", "fast").extract(rec) is True
    assert field_spec("node", "diag_active").extract(rec) is MISSING
    assert field_spec("node", "reset_cause").extract(rec) == "power-on"
    assert field_spec("node", "node_status").extract(rec) is True
    assert field_spec("node", "battery_mv").unit == "mV"


def test_reset_cause_text_matches_firmware():
    assert reset_cause_text(0x40) == "software"
    assert reset_cause_text(0x20) == "watchdog"
    assert reset_cause_text(0x04) == "brown-out 3.3 V"
    assert reset_cause_text(0) == "unknown"


def test_ecowitt_soil_channels():
    rec = {"seq": 3, "ts": 5.0, "soil": {"2": 61}, "rain_year_in": 29.84}
    assert field_spec("ecowitt", "soil_ch2").extract(rec) == 61
    assert field_spec("ecowitt", "soil_ch1").extract(rec) is MISSING
    assert field_spec("ecowitt", "rain_total_in").extract(rec) == 29.84


def test_points_for_skips_missing():
    recs = [{"seq": 1, "ts": 1.0, "n": 5}, {"seq": 2, "ts": 2.0}, {"seq": 3, "ts": 3.0, "n": 6}]
    pts = points_for("node", "cycle", recs)
    assert [(p.ts, p.value) for p in pts] == [(1.0, 5), (3.0, 6)]


def test_parse_map_blank_valid_and_invalid():
    assert parse_map("") == {}
    m = parse_map('{"node": {"stage_ft": "sensor.creek_gateway_v2_stage"},'
                  ' "ecowitt": {"soil_ch2": "sensor.field"}}')
    assert m["node"]["stage_ft"] == "sensor.creek_gateway_v2_stage"
    for bad in ('not json', '{"node": {"bogus": "sensor.x"}}', '{"other": {}}',
                '{"node": {"stage_ft": "no_domain"}}', '["node"]'):
        try:
            parse_map(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python rate_of_rise/tests/test_backfill_entity_map.py`
Expected: `ModuleNotFoundError: No module named 'app.backfill.entity_map'`.

- [ ] **Step 3: Implement `units.py`**

```python
"""Unit conversion for backfilled recorder states.

Home Assistant records a sensor in the unit it is *displayed* in, which can be changed per
entity, not in the unit the device publishes. On this install the gateway's distance is
recorded in inches and its battery in volts, though ESPHome publishes millimetres and
millivolts. A backfilled row has to match what HA itself would have written, so every
number is converted into the entity's current unit_of_measurement first.
"""
from __future__ import annotations

_LENGTH_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8}
_VOLTAGE_MV = {"mV": 1.0, "V": 1000.0}
_RATE_MM_H = {"mm/h": 1.0, "in/h": 25.4}
_TABLES = (_LENGTH_MM, _VOLTAGE_MV, _RATE_MM_H)


class UnitMismatch(ValueError):
    """The record's unit cannot be converted to the entity's."""


def convert(value: float, from_unit: str | None, to_unit: str | None) -> float:
    if (from_unit or "") == (to_unit or ""):
        return value
    for table in _TABLES:
        if from_unit in table and to_unit in table:
            return value * table[from_unit] / table[to_unit]
    if from_unit == "°F" and to_unit == "°C":
        return (value - 32.0) * 5.0 / 9.0
    if from_unit == "°C" and to_unit == "°F":
        return value * 9.0 / 5.0 + 32.0
    raise UnitMismatch(f"cannot convert {from_unit!r} to {to_unit!r}")


def format_state(value: float) -> str:
    """HA stores str(float). Six decimals keeps float noise out without losing anything a
    ±5 mm radar or a 0.01 in rain gauge can resolve."""
    return repr(round(float(value), 6))
```

- [ ] **Step 4: Implement `entity_map.py`**

```python
"""Which record field becomes which HA entity, and how.

The add-on options carry two maps, field -> entity_id, per stream (`backfill_entity_map` is
written, `backfill_shadow_map` only logged). The fields themselves are fixed here: each one
knows how to pull its value out of a record, what kind of HA state it is, and the unit the
gateway records it in.

Absent and null are different. A key the record does not carry (an older node build, a
console with no such sensor) is MISSING: no row. A key carried as null (a failed radar read)
is None: the row says `unknown`, as the live entity did.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

STREAMS = ("node", "ecowitt")
MISSING = object()


@dataclass(frozen=True)
class Point:
    ts: float
    value: object


@dataclass(frozen=True)
class FieldSpec:
    kind: str                 # "number" | "binary" | "text"
    unit: str | None          # the unit the gateway records it in
    extract: Callable[[dict], object]


def reset_cause_text(code: int) -> str:
    """Same table as rfm69_gateway.h's reset-cause text sensor (SAMD21 PM->RCAUSE)."""
    if code & 0x40:
        return "software"
    if code & 0x20:
        return "watchdog"
    if code & 0x10:
        return "external"
    if code & 0x04:
        return "brown-out 3.3 V"
    if code & 0x02:
        return "brown-out 1.2 V"
    if code & 0x01:
        return "power-on"
    return "unknown"


def _key(name: str):
    return lambda r: r[name] if name in r else MISSING


def _flag(name: str):
    def get(r):
        v = r.get(name)
        return MISSING if v is None else bool(v)
    return get


def _reset(r):
    v = r.get("r")
    return MISSING if v is None else reset_cause_text(int(v))


NODE_FIELDS: dict[str, FieldSpec] = {
    "stage_ft": FieldSpec("number", "ft", _key("stage_ft")),
    "depth_in": FieldSpec("number", "in", _key("depth_in")),
    "distance_mm": FieldSpec("number", "mm", _key("d")),
    "battery_mv": FieldSpec("number", "mV", _key("v")),
    "rssi_dbm": FieldSpec("number", "dBm", _key("rssi")),
    "cycle": FieldSpec("number", None, _key("n")),
    "radio_init_failures": FieldSpec("number", None, _key("i")),
    "fast": FieldSpec("binary", None, _flag("f")),
    "diag_active": FieldSpec("binary", None, _flag("g")),
    "reset_cause": FieldSpec("text", None, _reset),
    # Every record is a packet heard, so the node was online at that moment.
    "node_status": FieldSpec("binary", None, lambda r: True),
}

ECOWITT_FIELDS: dict[str, FieldSpec] = {
    "rain_total_in": FieldSpec("number", "in", _key("rain_year_in")),
    "rain_rate_in_hr": FieldSpec("number", "in/h", _key("rain_rate_in_hr")),
    "rain_24h_in": FieldSpec("number", "in", _key("rain_24h_in")),
    "rain_day_in": FieldSpec("number", "in", _key("rain_day_in")),
    "rain_event_in": FieldSpec("number", "in", _key("rain_event_in")),
    "temp_f": FieldSpec("number", "°F", _key("temp_f")),
}

_SOIL = re.compile(r"^soil_ch(\d+)$")


def field_spec(stream: str, field: str) -> FieldSpec:
    if stream == "node":
        return NODE_FIELDS[field]
    if stream == "ecowitt":
        if field in ECOWITT_FIELDS:
            return ECOWITT_FIELDS[field]
        m = _SOIL.match(field)
        if m:
            ch = m.group(1)
            return FieldSpec("number", "%", lambda r: (r.get("soil") or {}).get(ch, MISSING))
    raise KeyError(f"{stream}.{field}")


def parse_map(text: str) -> dict[str, dict[str, str]]:
    """Validate an entity-map option. Blank is an empty map; anything malformed raises
    ValueError naming the problem, so the add-on can say exactly what to fix."""
    if not text or not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"entity map is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("entity map must be a JSON object of streams")
    out: dict[str, dict[str, str]] = {}
    for stream, fields in data.items():
        if stream not in STREAMS:
            raise ValueError(f"unknown stream {stream!r} (expected one of {STREAMS})")
        if not isinstance(fields, dict):
            raise ValueError(f"{stream}: expected an object of field -> entity_id")
        for field, entity in fields.items():
            try:
                field_spec(stream, field)
            except KeyError:
                raise ValueError(f"unknown field {stream}.{field}") from None
            if not isinstance(entity, str) or "." not in entity:
                raise ValueError(f"{stream}.{field}: {entity!r} is not an entity_id")
        out[stream] = dict(fields)
    return out


def points_for(stream: str, field: str, records: list[dict]) -> list[Point]:
    spec = field_spec(stream, field)
    out = []
    for rec in records:
        value = spec.extract(rec)
        if value is MISSING:
            continue
        out.append(Point(float(rec["ts"]), value))
    return out
```

- [ ] **Step 5: Run tests**

Run: `python rate_of_rise/tests/test_backfill_entity_map.py`
Expected: `8 passed`.

- [ ] **Step 6: Commit**

```bash
git add rate_of_rise/app/backfill/units.py rate_of_rise/app/backfill/entity_map.py rate_of_rise/tests/test_backfill_entity_map.py
git commit -m "backfill: unit conversion and the record-field -> entity registry

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 12: `RecorderWriter` — backfilled states into `home-assistant_v2.db`

**Files:**
- Create: `rate_of_rise/tests/fixtures/recorder_schema_53.sql`
- Create: `rate_of_rise/app/backfill/recorder.py`
- Test: `rate_of_rise/tests/test_backfill_recorder.py`

**Interfaces:**
- Consumes: `units.convert`, `units.format_state`, `units.UnitMismatch`; `entity_map.Point`.
- Produces: `SUPPORTED_SCHEMAS = frozenset({53})`; `MARKER = bytes.fromhex("CB0F11ED")`; `MATCH_TOLERANCE_S = 20.0`; `UNAVAILABLE_MARGIN_S = 120.0`; `schema_version(db_path) -> int | None`; `connect(db_path) -> sqlite3.Connection`; `class SchemaUnsupported(RuntimeError)` with `.version`; `WriteResult(entity_id, inserted: list[float], deleted_unavailable: int, skipped: str | None)`; `RecorderWriter(db_path)` (raises `SchemaUnsupported`) with `.version` and `write(entity_id: str, kind: str, native_unit: str | None, points: list[Point], dry_run: bool = False) -> WriteResult`; `entity_unit(conn, metadata_id) -> (attributes_id, unit)`.

- [ ] **Step 1: Create the schema fixture**

`rate_of_rise/tests/fixtures/recorder_schema_53.sql`. This is the DDL of the five tables the writer touches, read on 2026-10-07 from the live database (`H:\home-assistant_v2.db`, opened `immutable=1`):

```sql
-- Home Assistant recorder schema 53: the tables app/backfill touches, copied verbatim from
-- the live database on 2026-10-07 (read-only, immutable=1). Regenerate when adding a
-- version to recorder.SUPPORTED_SCHEMAS.
CREATE TABLE schema_changes (
	change_id INTEGER NOT NULL,
	schema_version INTEGER,
	changed DATETIME NOT NULL,
	PRIMARY KEY (change_id)
);
CREATE TABLE state_attributes (
	attributes_id INTEGER NOT NULL,
	hash BIGINT,
	shared_attrs TEXT,
	PRIMARY KEY (attributes_id)
);
CREATE TABLE states_meta (
	metadata_id INTEGER NOT NULL,
	entity_id VARCHAR(255),
	PRIMARY KEY (metadata_id)
);
CREATE TABLE states (
	state_id INTEGER NOT NULL,
	entity_id CHAR(0),
	state VARCHAR(255),
	attributes CHAR(0),
	event_id SMALLINT,
	last_changed CHAR(0),
	last_changed_ts FLOAT,
	last_reported_ts FLOAT,
	last_updated CHAR(0),
	last_updated_ts FLOAT,
	old_state_id INTEGER,
	attributes_id INTEGER,
	context_id CHAR(0),
	context_user_id CHAR(0),
	context_parent_id CHAR(0),
	origin_idx SMALLINT,
	context_id_bin BLOB,
	context_user_id_bin BLOB,
	context_parent_id_bin BLOB,
	metadata_id INTEGER,
	PRIMARY KEY (state_id),
	FOREIGN KEY(old_state_id) REFERENCES states (state_id),
	FOREIGN KEY(attributes_id) REFERENCES state_attributes (attributes_id),
	FOREIGN KEY(metadata_id) REFERENCES states_meta (metadata_id)
);
CREATE TABLE statistics_meta (
	id INTEGER NOT NULL,
	statistic_id VARCHAR(255),
	source VARCHAR(32),
	unit_of_measurement VARCHAR(255),
	has_mean BOOLEAN,
	has_sum BOOLEAN,
	name VARCHAR(255), mean_type INTEGER NOT NULL DEFAULT 0, unit_class VARCHAR(255),
	PRIMARY KEY (id)
);
CREATE INDEX ix_state_attributes_hash ON state_attributes (hash);
CREATE INDEX ix_states_attributes_id ON states (attributes_id);
CREATE INDEX ix_states_context_id_bin ON states (context_id_bin);
CREATE INDEX ix_states_last_updated_ts ON states (last_updated_ts);
CREATE UNIQUE INDEX ix_states_meta_entity_id ON states_meta (entity_id);
CREATE INDEX ix_states_metadata_id_last_updated_ts ON states (metadata_id, last_updated_ts);
CREATE INDEX ix_states_old_state_id ON states (old_state_id);
CREATE UNIQUE INDEX ix_statistics_meta_statistic_id ON statistics_meta (statistic_id);
```

- [ ] **Step 2: Write the failing tests**

`rate_of_rise/tests/test_backfill_recorder.py`:
```python
"""RecorderWriter against a schema-53 fixture database.

Facts this pins, all observed in the live recorder on 2026-10-07:
  * a state row has last_changed_ts NULL when it equals last_updated_ts;
  * numbers are stored in the entity's display unit (distance in inches, battery in V);
  * the ESPHome device disconnecting writes `unavailable` rows that the backfill removes.

Run: python rate_of_rise/tests/test_backfill_recorder.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backfill.entity_map import Point  # noqa: E402
from app.backfill.recorder import (  # noqa: E402
    MARKER, RecorderWriter, SchemaUnsupported, schema_version)

SCHEMA = Path(__file__).parent / "fixtures" / "recorder_schema_53.sql"
T0 = 1_791_300_000.0


def make_db(version=53):
    db = Path(tempfile.mkdtemp()) / "home-assistant_v2.db"
    c = sqlite3.connect(db)
    c.executescript(SCHEMA.read_text(encoding="utf-8"))
    c.execute("INSERT INTO schema_changes (schema_version, changed) VALUES (?, '2026-01-01')",
              (version,))
    c.executemany("INSERT INTO states_meta (metadata_id, entity_id) VALUES (?, ?)", [
        (1, "sensor.stage"), (2, "sensor.distance"), (3, "binary_sensor.node"),
        (4, "sensor.weight")])
    c.executemany("INSERT INTO state_attributes (attributes_id, shared_attrs) VALUES (?, ?)", [
        (10, '{"unit_of_measurement":"ft","state_class":"measurement"}'),
        (20, '{"unit_of_measurement":"in"}'), (30, '{}'), (40, '{"unit_of_measurement":"kg"}')])
    c.commit()
    return db, c


def add(c, mid, state, ts, attrs, old=None):
    cur = c.execute(
        "INSERT INTO states (state, last_updated_ts, old_state_id, attributes_id, origin_idx,"
        " metadata_id) VALUES (?, ?, ?, ?, 0, ?)", (state, ts, old, attrs, mid))
    c.commit()
    return cur.lastrowid


def rows(c, mid):
    return c.execute("SELECT state, last_updated_ts, last_changed_ts, attributes_id,"
                     " substr(context_id_bin, 1, 4), old_state_id FROM states"
                     " WHERE metadata_id = ? ORDER BY last_updated_ts", (mid,)).fetchall()


def test_schema_guard():
    db, _ = make_db(54)
    assert schema_version(db) == 54
    try:
        RecorderWriter(db)
    except SchemaUnsupported as exc:
        assert exc.version == 54
        return
    raise AssertionError("schema 54 was accepted")


def test_inserts_missing_readings_with_marker_and_attributes():
    db, c = make_db()
    add(c, 1, "0.9", T0, 10)
    res = RecorderWriter(db).write("sensor.stage", "number", "ft",
                                   [Point(T0 + 60, 0.95), Point(T0 + 120, 0.97)])
    assert res.inserted == [T0 + 60, T0 + 120] and res.skipped is None
    got = rows(c, 1)
    assert [r[0] for r in got] == ["0.9", "0.95", "0.97"]
    assert got[1][2] is None and got[1][3] == 10 and got[1][4] == MARKER and got[1][5] is None


def test_existing_row_within_tolerance_is_not_duplicated():
    db, c = make_db()
    add(c, 1, "0.95", T0 + 61.7, 10)        # HA's live write, 1.7 s after the gateway's stamp
    res = RecorderWriter(db).write("sensor.stage", "number", "ft", [Point(T0 + 60, 0.95)])
    assert res.inserted == []
    assert len(rows(c, 1)) == 1


def test_unchanged_value_is_compressed_like_ha():
    db, c = make_db()
    add(c, 1, "0.95", T0, 10)
    res = RecorderWriter(db).write("sensor.stage", "number", "ft", [
        Point(T0 + 60, 0.95), Point(T0 + 120, 0.95), Point(T0 + 180, 0.96),
        Point(T0 + 240, 0.96)])
    assert res.inserted == [T0 + 180]


def test_converts_to_the_entity_display_unit():
    db, c = make_db()
    add(c, 2, "32.0", T0, 20)                 # distance displayed in inches
    RecorderWriter(db).write("sensor.distance", "number", "mm", [Point(T0 + 60, 812)])
    assert rows(c, 2)[-1][0] == "31.968504"


def test_unknown_unit_skips_entity_without_raising():
    db, c = make_db()
    add(c, 4, "1.0", T0, 40)                  # kg: cannot hold a distance
    res = RecorderWriter(db).write("sensor.weight", "number", "mm", [Point(T0 + 60, 812)])
    assert res.skipped and "kg" in res.skipped and res.inserted == []
    assert len(rows(c, 4)) == 1


def test_null_number_is_unknown_and_binary_is_on_off():
    db, c = make_db()
    add(c, 1, "0.9", T0, 10)
    add(c, 3, "off", T0, 30)
    w = RecorderWriter(db)
    w.write("sensor.stage", "number", "ft", [Point(T0 + 60, None)])
    w.write("binary_sensor.node", "binary", None, [Point(T0 + 60, True)])
    assert rows(c, 1)[-1][0] == "unknown"
    assert rows(c, 3)[-1][0] == "on"


def test_unavailable_rows_in_the_gap_are_removed_and_references_cleared():
    db, c = make_db()
    a = add(c, 1, "0.9", T0, 10)
    u = add(c, 1, "unavailable", T0 + 30, 10, old=a)
    b = add(c, 1, "0.99", T0 + 600, 10, old=u)   # live again; points at the unavailable row
    res = RecorderWriter(db).write("sensor.stage", "number", "ft",
                                   [Point(T0 + 90, 0.92), Point(T0 + 150, 0.94)])
    assert res.deleted_unavailable == 1
    states = [r[0] for r in rows(c, 1)]
    assert "unavailable" not in states and states == ["0.9", "0.92", "0.94", "0.99"]
    assert c.execute("SELECT old_state_id FROM states WHERE state_id = ?", (b,)).fetchone()[0] is None


def test_unavailable_far_from_inserted_rows_is_kept():
    db, c = make_db()
    add(c, 1, "unavailable", T0, 10)
    add(c, 1, "0.9", T0 + 5000, 10)
    RecorderWriter(db).write("sensor.stage", "number", "ft", [Point(T0 + 6000, 0.95)])
    assert "unavailable" in [r[0] for r in rows(c, 1)]


def test_second_write_is_a_no_op():
    db, c = make_db()
    add(c, 1, "0.9", T0, 10)
    pts = [Point(T0 + 60, 0.95), Point(T0 + 120, 0.97)]
    RecorderWriter(db).write("sensor.stage", "number", "ft", pts)
    again = RecorderWriter(db).write("sensor.stage", "number", "ft", pts)
    assert again.inserted == [] and len(rows(c, 1)) == 3


def test_dry_run_counts_but_writes_nothing():
    db, c = make_db()
    add(c, 1, "0.9", T0, 10)
    add(c, 1, "unavailable", T0 + 30, 10)
    res = RecorderWriter(db).write("sensor.stage", "number", "ft",
                                   [Point(T0 + 90, 0.92)], dry_run=True)
    assert res.inserted == [T0 + 90] and res.deleted_unavailable == 1
    assert len(rows(c, 1)) == 2


def test_entity_not_in_recorder_is_skipped():
    db, _ = make_db()
    res = RecorderWriter(db).write("sensor.nope", "number", "ft", [Point(T0, 1.0)])
    assert res.skipped == "entity not in recorder" and res.inserted == []


def test_large_batch_is_chunked():
    db, c = make_db()
    add(c, 1, "0.0", T0, 10)
    pts = [Point(T0 + 60 * (i + 1), round(0.001 * (i + 1), 3)) for i in range(1200)]
    res = RecorderWriter(db).write("sensor.stage", "number", "ft", pts)
    assert len(res.inserted) == 1200 and len(rows(c, 1)) == 1201


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run to verify failure**

Run: `python rate_of_rise/tests/test_backfill_recorder.py`
Expected: `ModuleNotFoundError: No module named 'app.backfill.recorder'`.

- [ ] **Step 4: Implement `recorder.py`**

```python
"""Backfilled states, written straight into Home Assistant's recorder database.

Nothing in Home Assistant accepts a state with a past timestamp: ESPHome, MQTT and the REST
API all stamp "now". So the readings the gateway held through an outage can only reach an
entity's history as inserted rows. This does that, carefully:

* Only on a recorder schema checked against this code (SUPPORTED_SCHEMAS). Any other
  version is refused before anything is read or written.
* Never creates an entity: it must already be in `states_meta`. Attributes are reused from
  the entity's newest row; these sensors' attributes do not change.
* Numbers go in the entity's display unit, read from those attributes (see units.py).
* Idempotent: a reading within MATCH_TOLERANCE_S of an existing row is already there.
* Compressed like HA: a reading equal to the state already in effect adds no row, because
  HA itself only writes a row when the state changes.
* `unavailable` rows HA wrote while the gateway was disconnected, within
  UNAVAILABLE_MARGIN_S of what was filled, are removed after clearing every old_state_id
  that points at them.
* Every inserted row's context id starts with MARKER, so undo.py can remove them all.
* Short transactions (CHUNK rows each, BEGIN IMMEDIATE with a busy timeout), because HA's
  recorder commits every second and must never wait long on us.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from .entity_map import Point
from .units import UnitMismatch, convert, format_state

log = logging.getLogger("app.backfill.recorder")

SUPPORTED_SCHEMAS = frozenset({53})
MARKER = bytes.fromhex("CB0F11ED")
MATCH_TOLERANCE_S = 20.0
UNAVAILABLE_MARGIN_S = 120.0
CHUNK = 500
BUSY_TIMEOUT_S = 30.0


class SchemaUnsupported(RuntimeError):
    def __init__(self, version: int | None):
        super().__init__(f"recorder schema {version} is not supported "
                         f"(supported: {sorted(SUPPORTED_SCHEMAS)})")
        self.version = version


@dataclass
class WriteResult:
    entity_id: str
    inserted: list[float] = field(default_factory=list)
    deleted_unavailable: int = 0
    skipped: str | None = None


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_S, isolation_level=None)
    conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_S * 1000)}")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def schema_version(db_path: Path) -> int | None:
    with closing(sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True,
                                 timeout=BUSY_TIMEOUT_S)) as conn:
        row = conn.execute("SELECT schema_version FROM schema_changes"
                           " ORDER BY change_id DESC LIMIT 1").fetchone()
    return int(row[0]) if row else None


def entity_unit(conn: sqlite3.Connection, metadata_id: int) -> tuple[int | None, str | None]:
    row = conn.execute(
        "SELECT s.attributes_id, a.shared_attrs FROM states s"
        " LEFT JOIN state_attributes a ON a.attributes_id = s.attributes_id"
        " WHERE s.metadata_id = ? AND s.attributes_id IS NOT NULL"
        " ORDER BY s.last_updated_ts DESC LIMIT 1", (metadata_id,)).fetchone()
    if row is None:
        return None, None
    try:
        unit = json.loads(row[1] or "{}").get("unit_of_measurement")
    except ValueError:
        unit = None
    return row[0], unit


def _format(kind: str, value, native_unit: str | None, unit: str | None) -> str:
    if kind == "binary":
        return "on" if value else "off"
    if kind == "text":
        return str(value)
    if value is None:
        return "unknown"
    return format_state(convert(float(value), native_unit, unit))


def _plan(formatted: list[tuple[float, str]], existing: list[tuple[int, str, float]],
          prior_state: str | None) -> list[tuple[float, str]]:
    """The (ts, state) pairs to insert. Walks the readings and the existing rows together, so
    'the state in effect' at each reading accounts for both."""
    valid = [(ts, state) for _, state, ts in existing if state != "unavailable"]
    out: list[tuple[float, str]] = []
    state_now = prior_state
    j = 0
    for ts, state in formatted:
        while j < len(valid) and valid[j][0] < ts - MATCH_TOLERANCE_S:
            state_now = valid[j][1]
            j += 1
        near = [v for v in valid[j:] if v[0] <= ts + MATCH_TOLERANCE_S]
        if near:                      # HA already recorded this reading
            state_now = near[-1][1]
            continue
        if state == state_now:        # HA writes no row for an unchanged state
            continue
        out.append((ts, state))
        state_now = state
    return out


def _chunks(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


class RecorderWriter:
    def __init__(self, db_path: Path):
        self._db = Path(db_path)
        self.version = schema_version(self._db)
        if self.version not in SUPPORTED_SCHEMAS:
            raise SchemaUnsupported(self.version)

    def write(self, entity_id: str, kind: str, native_unit: str | None, points: list[Point],
              dry_run: bool = False) -> WriteResult:
        result = WriteResult(entity_id)
        if not points:
            return result
        with closing(connect(self._db)) as conn:
            meta = conn.execute("SELECT metadata_id FROM states_meta WHERE entity_id = ?",
                                (entity_id,)).fetchone()
            if meta is None:
                result.skipped = "entity not in recorder"
                return result
            metadata_id = meta[0]
            attrs_id, unit = entity_unit(conn, metadata_id)
            try:
                formatted = [(p.ts, _format(kind, p.value, native_unit, unit))
                             for p in sorted(points, key=lambda p: p.ts)]
            except UnitMismatch as exc:
                result.skipped = str(exc)
                return result
            lo, hi = formatted[0][0], formatted[-1][0]
            existing = conn.execute(
                "SELECT state_id, state, last_updated_ts FROM states WHERE metadata_id = ?"
                " AND last_updated_ts BETWEEN ? AND ? ORDER BY last_updated_ts",
                (metadata_id, lo - UNAVAILABLE_MARGIN_S, hi + UNAVAILABLE_MARGIN_S)).fetchall()
            prior = conn.execute(
                "SELECT state FROM states WHERE metadata_id = ? AND last_updated_ts < ?"
                " AND state != 'unavailable' ORDER BY last_updated_ts DESC LIMIT 1",
                (metadata_id, lo - UNAVAILABLE_MARGIN_S)).fetchone()
            to_insert = _plan(formatted, existing, prior[0] if prior else None)
            doomed: list[int] = []
            if to_insert:
                first = to_insert[0][0] - UNAVAILABLE_MARGIN_S
                last = to_insert[-1][0] + UNAVAILABLE_MARGIN_S
                doomed = [sid for sid, state, ts in existing
                          if state == "unavailable" and first <= ts <= last]
            result.inserted = [ts for ts, _ in to_insert]
            result.deleted_unavailable = len(doomed)
            if dry_run or not (to_insert or doomed):
                return result
            self._apply(conn, metadata_id, attrs_id, to_insert, doomed)
        return result

    @staticmethod
    def _apply(conn, metadata_id, attrs_id, to_insert, doomed) -> None:
        if doomed:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for chunk in _chunks(doomed, CHUNK):
                    marks = ",".join("?" * len(chunk))
                    conn.execute(f"UPDATE states SET old_state_id = NULL"
                                 f" WHERE old_state_id IN ({marks})", chunk)
                    conn.execute(f"DELETE FROM states WHERE state_id IN ({marks})", chunk)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        for chunk in _chunks(to_insert, CHUNK):
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.executemany(
                    "INSERT INTO states (state, last_updated_ts, old_state_id, attributes_id,"
                    " origin_idx, context_id_bin, metadata_id) VALUES (?, ?, NULL, ?, 0, ?, ?)",
                    [(state, ts, attrs_id, MARKER + os.urandom(12), metadata_id)
                     for ts, state in chunk])
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
```

- [ ] **Step 5: Run tests**

Run: `python rate_of_rise/tests/test_backfill_recorder.py`
Expected: `13 passed`.

- [ ] **Step 6: Commit**

```bash
git add rate_of_rise/tests/fixtures/recorder_schema_53.sql rate_of_rise/app/backfill/recorder.py rate_of_rise/tests/test_backfill_recorder.py
git commit -m "backfill: recorder writer (schema-guarded, unit-aware, idempotent, marked)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 13: Undo, as an add-on command

**Files:**
- Create: `rate_of_rise/app/backfill/undo.py`
- Modify: `rate_of_rise/app/commands.py` (`KNOWN_COMMANDS`)
- Modify: `rate_of_rise/app/__main__.py` (register the handler)
- Test: `rate_of_rise/tests/test_backfill_undo.py`; update `rate_of_rise/tests/test_commands.py` if it asserts the exact `KNOWN_COMMANDS` tuple

**Interfaces:**
- Consumes: `recorder.connect`, `recorder.schema_version`, `recorder.MARKER`, `recorder.SUPPORTED_SCHEMAS`, `recorder.SchemaUnsupported`.
- Produces: `undo(db_path: Path, since_ts: float = 0.0) -> int`; command `backfill_undo` on MQTT topic `creek/cmd/backfill_undo`, payload an ISO-8601 time or empty.

- [ ] **Step 1: Write the failing test**

`rate_of_rise/tests/test_backfill_undo.py`:
```python
"""undo(): every backfilled recorder row out again, nothing else touched.

Run: python rate_of_rise/tests/test_backfill_undo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_backfill_recorder import T0, add, make_db, rows  # noqa: E402

from app.backfill.entity_map import Point  # noqa: E402
from app.backfill.recorder import RecorderWriter  # noqa: E402
from app.backfill.undo import undo  # noqa: E402


def test_removes_only_marked_rows_since_the_given_time():
    db, c = make_db()
    add(c, 1, "0.9", T0, 10)
    live = add(c, 1, "1.5", T0 + 900, 10)
    RecorderWriter(db).write("sensor.stage", "number", "ft",
                             [Point(T0 + 60, 0.95), Point(T0 + 600, 1.2)])
    c.execute("UPDATE states SET old_state_id = (SELECT state_id FROM states WHERE state = '1.2')"
              " WHERE state_id = ?", (live,))
    c.commit()
    assert undo(db, since_ts=T0 + 300) == 1
    assert [r[0] for r in rows(c, 1)] == ["0.9", "0.95", "1.5"]
    assert c.execute("SELECT old_state_id FROM states WHERE state_id = ?",
                     (live,)).fetchone()[0] is None
    assert undo(db) == 1
    assert [r[0] for r in rows(c, 1)] == ["0.9", "1.5"]


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python rate_of_rise/tests/test_backfill_undo.py`
Expected: `ModuleNotFoundError: No module named 'app.backfill.undo'`.

- [ ] **Step 3: Implement `undo.py`**

```python
"""Remove backfilled recorder rows: every states row whose context id carries MARKER.

The `unavailable` rows the backfill deleted are not restored. They carried no reading, and
HA's history draws the same gap without them.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path

from .recorder import CHUNK, MARKER, SUPPORTED_SCHEMAS, SchemaUnsupported, connect, schema_version


def undo(db_path: Path, since_ts: float = 0.0) -> int:
    version = schema_version(db_path)
    if version not in SUPPORTED_SCHEMAS:
        raise SchemaUnsupported(version)
    with closing(connect(db_path)) as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT state_id FROM states WHERE substr(context_id_bin, 1, 4) = ?"
            " AND last_updated_ts >= ?", (MARKER, since_ts))]
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i:i + CHUNK]
            marks = ",".join("?" * len(chunk))
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(f"UPDATE states SET old_state_id = NULL"
                             f" WHERE old_state_id IN ({marks})", chunk)
                conn.execute(f"DELETE FROM states WHERE state_id IN ({marks})", chunk)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    return len(ids)
```

- [ ] **Step 4: Register the command**

In `app/commands.py`:
```python
KNOWN_COMMANDS = ("run_inference", "retrain", "promote", "rollback", "annotate",
                  "backfill_undo")
```
Run `python rate_of_rise/tests/test_commands.py`. If a test pins the old tuple, add `"backfill_undo"` to its expected value.

In `app/__main__.py`, add the import `from .backfill import recorder_db_path` and `from .backfill.undo import undo as backfill_undo` (Task 19 creates `recorder_db_path`; until then add it to `app/backfill/__init__.py` now):
```python
import os
from pathlib import Path


def recorder_db_path() -> Path:
    """HA's recorder database as the add-on sees it (config.yaml maps homeassistant_config).
    RECORDER_DB overrides it for tests and local runs."""
    return Path(os.environ.get("RECORDER_DB", "/homeassistant/home-assistant_v2.db"))
```

Add a module-level function in `__main__.py` beside `_annotate`:
```python
def _backfill_undo(payload: str) -> str:
    """Take every backfilled row back out of HA's recorder (app/backfill/undo.py). Payload:
    an ISO-8601 time to undo from, or empty for all of them. Raises on a bad time or an
    unsupported recorder schema; CommandProcessor reports either."""
    text = payload.strip()
    since = datetime.fromisoformat(text).timestamp() if text else 0.0
    n = backfill_undo(recorder_db_path(), since)
    return f"removed {n} backfilled recorder row(s) since {text or 'the beginning'}"
```
and register it in the `CommandProcessor({...})` dict:
```python
            "backfill_undo": lambda payload: _backfill_undo(payload),
```

- [ ] **Step 5: Run tests**

Run: `python rate_of_rise/tests/test_backfill_undo.py && python rate_of_rise/tests/test_commands.py`
Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add rate_of_rise/app/backfill/undo.py rate_of_rise/app/backfill/__init__.py rate_of_rise/app/commands.py rate_of_rise/app/__main__.py rate_of_rise/tests/test_backfill_undo.py rate_of_rise/tests/test_commands.py
git commit -m "backfill: undo command removes every marked recorder row

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 14: Hourly statistics import

**Files:**
- Create: `rate_of_rise/app/backfill/statistics.py`
- Test: `rate_of_rise/tests/test_backfill_statistics.py`

**Interfaces:**
- Consumes: `recorder.connect`.
- Produces: `hourly_stats(rows: list[tuple[float, float]], hour_start: float, prior: float | None) -> dict | None`; `HAWebsocket(url: str, token: str, connect=websocket.create_connection)` with `call(msg: dict) -> object` (raises `RuntimeError` on `success: false`); `StatisticsWriter(db_path, send: Callable[[dict], object], now_fn=time.time)` with `backfill(entity_id: str, inserted_ts: list[float]) -> int` (hours imported).

- [ ] **Step 1: Write the failing tests**

`rate_of_rise/tests/test_backfill_statistics.py`:
```python
"""Hourly long-term statistics for hours the backfill filled.

Run: python rate_of_rise/tests/test_backfill_statistics.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_backfill_recorder import add, make_db  # noqa: E402

from app.backfill.statistics import StatisticsWriter, hourly_stats  # noqa: E402

H = 1_791_298_800.0     # 2026-10-06 15:00 UTC, an hour boundary


def test_time_weighted_mean_min_max():
    # 1.0 for the first 15 min (carried in from before the hour), 2.0 for 45 min.
    s = hourly_stats([(H + 900, 2.0)], H, prior=1.0)
    assert abs(s["mean"] - 1.75) < 1e-9 and s["min"] == 1.0 and s["max"] == 2.0
    assert hourly_stats([], H, prior=None) is None
    assert hourly_stats([], H, prior=3.0) == {"mean": 3.0, "min": 3.0, "max": 3.0}


def setup_meta(c, mean_type=1, has_sum=0):
    c.execute("INSERT INTO statistics_meta (statistic_id, source, unit_of_measurement, has_mean,"
              " has_sum, name, mean_type, unit_class) VALUES"
              " ('sensor.stage', 'recorder', 'ft', NULL, ?, NULL, ?, 'distance')",
              (has_sum, mean_type))
    c.commit()


def test_imports_completed_hours_with_metadata_from_the_db():
    db, c = make_db()
    setup_meta(c)
    add(c, 1, "1.0", H - 60, 10)
    add(c, 1, "2.0", H + 900, 10)
    add(c, 1, "unknown", H + 1800, 10)       # non-numeric: ends the 2.0 span, adds nothing
    sent = []
    w = StatisticsWriter(db, sent.append, now_fn=lambda: H + 3 * 3600)
    assert w.backfill("sensor.stage", [H + 900]) == 1
    msg = sent[0]
    assert msg["type"] == "recorder/import_statistics"
    meta = msg["metadata"]
    assert meta == {"source": "recorder", "statistic_id": "sensor.stage",
                    "unit_of_measurement": "ft", "name": None, "has_sum": False,
                    "mean_type": 1, "unit_class": "distance"}
    stat = msg["stats"][0]
    assert stat["start"] == "2026-10-06T15:00:00+00:00"
    assert stat["min"] == 1.0 and stat["max"] == 2.0
    assert abs(stat["mean"] - (1.0 * 900 + 2.0 * 900) / 1800) < 1e-9


def test_current_and_recent_hours_are_left_to_ha():
    db, c = make_db()
    setup_meta(c)
    add(c, 1, "1.0", H + 60, 10)
    sent = []
    assert StatisticsWriter(db, sent.append, now_fn=lambda: H + 3600 + 300).backfill(
        "sensor.stage", [H + 60]) == 0
    assert sent == []


def test_sum_and_meanless_statistics_are_skipped():
    for mean_type, has_sum in ((0, 1), (0, 0)):
        db, c = make_db()
        setup_meta(c, mean_type=mean_type, has_sum=has_sum)
        add(c, 1, "1.0", H + 60, 10)
        sent = []
        assert StatisticsWriter(db, sent.append, now_fn=lambda: H + 5 * 3600).backfill(
            "sensor.stage", [H + 60]) == 0
        assert sent == []


def test_no_statistics_metadata_is_skipped():
    db, c = make_db()
    add(c, 1, "1.0", H + 60, 10)
    sent = []
    assert StatisticsWriter(db, sent.append, now_fn=lambda: H + 5 * 3600).backfill(
        "sensor.stage", [H + 60]) == 0


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python rate_of_rise/tests/test_backfill_statistics.py`
Expected: `ModuleNotFoundError: No module named 'app.backfill.statistics'`.

- [ ] **Step 3: Implement `statistics.py`**

```python
"""Hourly long-term statistics for hours the backfill filled.

HA compiles an hour's mean/min/max from the states it had at the time, so an hour with a gap
has statistics that miss it, and history views longer than about 10 days are drawn from those
statistics. After states are inserted, each affected, completed hour is recomputed the way
HA computes it (time-weighted mean; the state in effect at the start of the hour counts) and
re-imported with `recorder/import_statistics`. That is HA's supported way to write statistics.

Only measurement statistics (mean_type 1). A `has_sum` statistic (a total_increasing counter
such as the rain total) carries a running sum that every later hour builds on; re-importing
one hour would mean rewriting every hour after it, so those are left alone.
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import websocket

from .recorder import connect

log = logging.getLogger("app.backfill.statistics")

HOUR = 3600.0
SETTLE_S = 600.0   # leave the hour just finished to HA's own compile


def _num(state: str) -> float | None:
    try:
        return float(state)
    except (TypeError, ValueError):
        return None


def hourly_stats(rows: list[tuple[float, float | None]], hour_start: float,
                 prior: float | None) -> dict | None:
    end = hour_start + HOUR
    pts = [(hour_start, prior)] + [(t, v) for t, v in rows if hour_start <= t < end]
    total = dur = 0.0
    vals = []
    for i, (t, v) in enumerate(pts):
        nxt = pts[i + 1][0] if i + 1 < len(pts) else end
        if v is None:
            continue
        total += v * (nxt - t)
        dur += nxt - t
        vals.append(v)
    if not vals:
        return None
    return {"mean": total / dur if dur else vals[-1], "min": min(vals), "max": max(vals)}


class HAWebsocket:
    """One short-lived connection per call to HA's websocket API via the Supervisor proxy."""

    def __init__(self, url: str, token: str, connect_fn=websocket.create_connection,
                 timeout: float = 30.0):
        self._url, self._token, self._connect, self._timeout = url, token, connect_fn, timeout

    def call(self, msg: dict):
        ws = self._connect(self._url, timeout=self._timeout)
        try:
            json.loads(ws.recv())                                   # auth_required
            ws.send(json.dumps({"type": "auth", "access_token": self._token}))
            auth = json.loads(ws.recv())
            if auth.get("type") != "auth_ok":
                raise RuntimeError(f"HA websocket auth failed: {auth}")
            ws.send(json.dumps({"id": 1, **msg}))
            while True:
                reply = json.loads(ws.recv())
                if reply.get("id") == 1:
                    break
            if not reply.get("success"):
                raise RuntimeError(f"{msg.get('type')} failed: {reply.get('error')}")
            return reply.get("result")
        finally:
            ws.close()


class StatisticsWriter:
    def __init__(self, db_path: Path, send: Callable[[dict], object], now_fn=time.time):
        self._db, self._send, self._now = Path(db_path), send, now_fn

    def backfill(self, entity_id: str, inserted_ts: list[float]) -> int:
        if not inserted_ts:
            return 0
        cutoff = self._now() - SETTLE_S
        hours = sorted({t - t % HOUR for t in inserted_ts if t - t % HOUR + HOUR <= cutoff})
        if not hours:
            return 0
        with closing(connect(self._db)) as conn:
            meta = conn.execute(
                "SELECT unit_of_measurement, name, has_sum, mean_type, unit_class"
                " FROM statistics_meta WHERE statistic_id = ?", (entity_id,)).fetchone()
            if meta is None or meta[2] or meta[3] != 1:
                return 0
            mid = conn.execute("SELECT metadata_id FROM states_meta WHERE entity_id = ?",
                               (entity_id,)).fetchone()
            if mid is None:
                return 0
            stats = []
            for h in hours:
                prior = conn.execute(
                    "SELECT state FROM states WHERE metadata_id = ? AND last_updated_ts < ?"
                    " ORDER BY last_updated_ts DESC LIMIT 1", (mid[0], h)).fetchone()
                rows = conn.execute(
                    "SELECT last_updated_ts, state FROM states WHERE metadata_id = ?"
                    " AND last_updated_ts >= ? AND last_updated_ts < ?"
                    " ORDER BY last_updated_ts", (mid[0], h, h + HOUR)).fetchall()
                s = hourly_stats([(t, _num(v)) for t, v in rows], h,
                                 _num(prior[0]) if prior else None)
                if s is not None:
                    start = datetime.fromtimestamp(h, timezone.utc).isoformat()
                    stats.append({"start": start, **s})
        if not stats:
            return 0
        metadata = {"source": "recorder", "statistic_id": entity_id,
                    "unit_of_measurement": meta[0], "name": meta[1], "has_sum": False,
                    "mean_type": 1}
        if meta[4] is not None:
            metadata["unit_class"] = meta[4]
        for batch in (stats[i:i + 500] for i in range(0, len(stats), 500)):
            self._send({"type": "recorder/import_statistics", "metadata": metadata,
                        "stats": batch})
        return len(stats)
```


- [ ] **Step 4: Run tests**

Run: `python rate_of_rise/tests/test_backfill_statistics.py`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add rate_of_rise/app/backfill/statistics.py rate_of_rise/tests/test_backfill_statistics.py
git commit -m "backfill: recompute and import hourly statistics for filled hours

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 15: Stage-log merge

**Files:**
- Modify: `rate_of_rise/app/stagelog.py` (add `FILE_LOCK`, take it in `tick()`'s write)
- Create: `rate_of_rise/app/backfill/stagelog_merge.py`
- Test: `rate_of_rise/tests/test_backfill_stagelog.py`

**Interfaces:**
- Consumes: `stagelog.HEADER`, `stagelog.FILE_LOCK`.
- Produces: `merge_stage_rows(out_dir: Path, rows: list[tuple[float, float | None]], now: float) -> int` (rows added).

- [ ] **Step 1: Write the failing test**

`rate_of_rise/tests/test_backfill_stagelog.py`:
```python
"""Backfilled node readings merged into the add-on's own stage log.

Run: python rate_of_rise/tests/test_backfill_stagelog.py
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backfill.stagelog_merge import merge_stage_rows  # noqa: E402
from app.stagelog import HEADER  # noqa: E402


def day_of(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")   # StageLogger's naming: local day


def lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def test_adds_missing_rows_sorted_and_skips_near_duplicates():
    d = Path(tempfile.mkdtemp())
    base = datetime(2026, 10, 7, 12, 0).timestamp()
    f = d / f"{day_of(base)}.csv"
    f.write_text(HEADER + f"{base},{base + 3},0.9000\n{base + 300},{base + 301},0.9500\n",
                 encoding="utf-8")
    added = merge_stage_rows(d, [(base + 1.5, 0.9), (base + 120, 0.92), (base + 240, None)],
                             now=base + 1000)
    assert added == 2
    got = lines(f)
    assert got[0] == HEADER.strip()
    assert [r.split(",")[0] for r in got[1:]] == [
        str(base), str(round(base + 120, 1)), str(round(base + 240, 1)), str(base + 300)]
    assert got[2].split(",")[2] == "0.9200" and got[3].split(",")[2] == ""


def test_rows_across_local_midnight_go_to_their_own_days():
    d = Path(tempfile.mkdtemp())
    midnight = datetime(2026, 10, 8, 0, 0).timestamp()
    assert merge_stage_rows(d, [(midnight - 60, 1.0), (midnight + 60, 1.1)], now=midnight) == 2
    assert (d / f"{day_of(midnight - 60)}.csv").exists()
    assert (d / f"{day_of(midnight + 60)}.csv").exists()


def test_nothing_new_leaves_files_untouched():
    d = Path(tempfile.mkdtemp())
    base = datetime(2026, 10, 7, 12, 0).timestamp()
    f = d / f"{day_of(base)}.csv"
    f.write_text(HEADER + f"{base},{base},0.9000\n", encoding="utf-8")
    before = f.stat().st_mtime_ns
    assert merge_stage_rows(d, [(base + 1, 0.9)], now=base) == 0
    assert f.stat().st_mtime_ns == before


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python rate_of_rise/tests/test_backfill_stagelog.py`
Expected: `ModuleNotFoundError: No module named 'app.backfill.stagelog_merge'`.

- [ ] **Step 3: Add the shared lock to `stagelog.py`**

At module level, after `HEADER`:
```python
# StageLogger appends from the main loop; the backfill rewrites day files from its own
# thread (app/backfill/stagelog_merge.py). Both take this around file access, or a rewrite
# could drop a row appended between its read and its replace.
FILE_LOCK = threading.Lock()
```
Add `import threading`. In `tick()`, wrap the `try: new = not path.exists() ... except OSError` block in `with FILE_LOCK:`.

- [ ] **Step 4: Implement `stagelog_merge.py`**

```python
"""Merge backfilled node readings into the add-on's stage log (app/stagelog.py).

Same files, same columns, same local-day naming as StageLogger. A reading within
MATCH_TOLERANCE_S of one already logged is the same reading. Each touched day is rewritten
sorted through a temp file and an atomic replace, under stagelog.FILE_LOCK.
"""
from __future__ import annotations

import bisect
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ..stagelog import FILE_LOCK, HEADER

log = logging.getLogger("app.backfill.stagelog")

MATCH_TOLERANCE_S = 2.0


def _read(path: Path) -> list[tuple[float, str]]:
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            parts = line.split(",")
            if len(parts) == 3:
                try:
                    out.append((float(parts[0]), line))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return out


def merge_stage_rows(out_dir: Path, rows: list[tuple[float, float | None]], now: float) -> int:
    by_day: dict[str, list[tuple[float, float | None]]] = defaultdict(list)
    for ts, stage in rows:
        by_day[datetime.fromtimestamp(ts).strftime("%Y-%m-%d")].append((ts, stage))
    added = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for day, day_rows in by_day.items():
        path = out_dir / f"{day}.csv"
        with FILE_LOCK:
            existing = _read(path)
            stamps = sorted(ts for ts, _ in existing)
            new = []
            for ts, stage in day_rows:
                i = bisect.bisect_left(stamps, ts - MATCH_TOLERANCE_S)
                if i < len(stamps) and stamps[i] <= ts + MATCH_TOLERANCE_S:
                    continue
                text = "" if stage is None else f"{stage:.4f}"
                new.append((round(ts, 1), f"{round(ts, 1)},{round(now, 1)},{text}"))
                bisect.insort(stamps, ts)
            if not new:
                continue
            merged = sorted(existing + new, key=lambda r: r[0])
            tmp = path.with_suffix(".tmp")
            tmp.write_text(HEADER + "".join(line + "\n" for _, line in merged), encoding="utf-8")
            tmp.replace(path)
            added += len(new)
    return added
```

- [ ] **Step 5: Run tests (new and existing)**

Run: `python rate_of_rise/tests/test_backfill_stagelog.py && python rate_of_rise/tests/test_stagelog.py`
Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add rate_of_rise/app/stagelog.py rate_of_rise/app/backfill/stagelog_merge.py rate_of_rise/tests/test_backfill_stagelog.py
git commit -m "backfill: merge node readings into the stage log, sorted, under a shared lock

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 16: History fetches for the five re-fetchable sources

> Wherever this plan says "append to `tests/test_x.py`", put the new test **above `def main():`**. Each test file calls `main()` at import time from its `if __name__ == "__main__":` block, and `main()` collects `test_*` from module globals, so a function defined below that block is never run.

**Files:**
- Modify: `rate_of_rise/app/sources/usgs.py`, `alerts.py`, `radar_cells.py`, `wu.py`, `snodas.py`, `__init__.py`
- Test: add tests to `rate_of_rise/tests/test_usgs.py`, `test_alerts.py`, `test_radar_cells.py`, `test_wu.py`, `test_snodas.py`, `test_sources.py`

**Interfaces:**
- Produces, on each of `UsgsDownstream`, `NwsAlerts`, `RadarCells`, `WuUpstream`, `SnodasSwe`: `history_between(start: datetime, end: datetime) -> Callable[[datetime], dict]`. One network fetch per call (WU: one per station per day), then the returned evaluator gives the same feature dict `poll()` would have produced at `as_of` (an aware UTC datetime). Evaluators never raise for "no data"; they return `None` values.
- `SourceCoordinator.history_sources() -> list` (sources that have `history_between`), `SourceCoordinator.rain_accumulator() -> RainAccumulator | None`.
- `poll()` behaviour is unchanged for every source. The existing tests prove it.

Each source below follows the same pattern: refactor `poll()` into "fetch" + "evaluate", then add `history_between` reusing "evaluate". Make one commit per source so a reviewer can reject one without the others.

### 16a — USGS

- [ ] **Step 1: Failing test** (append to `tests/test_usgs.py`)

```python
def test_history_between_evaluates_at_past_times():
    from datetime import datetime, timezone
    calls = []
    series = {"value": {"timeSeries": [{
        "sourceInfo": {"siteCode": [{"value": "01534860"}]},
        "variable": {"variableCode": [{"value": "00065"}]},
        "values": [{"value": [
            {"value": "3.0", "dateTime": "2026-10-07T08:00:00.000-04:00"},
            {"value": "3.5", "dateTime": "2026-10-07T11:00:00.000-04:00"},
            {"value": "4.0", "dateTime": "2026-10-07T12:00:00.000-04:00"}]}]}]}}

    def fetch(url, timeout=15.0):
        calls.append(url)
        return series

    src = UsgsDownstream(fetch=fetch)
    at = src.history_between(datetime(2026, 10, 7, 14, tzinfo=timezone.utc),
                             datetime(2026, 10, 7, 17, tzinfo=timezone.utc))
    assert len(calls) == 1 and "startDT=2026-10-07T08:00Z" in calls[0]
    assert "endDT=2026-10-07T17:00Z" in calls[0]
    out = at(datetime(2026, 10, 7, 15, 30, tzinfo=timezone.utc))    # 11:30 EDT
    assert out["usgs_leggetts_gage_ft"] == 3.5
    assert out["usgs_leggetts_rise_3h_ft"] == 0.5
    assert out["usgs_tunkhannock_gage_ft"] is None
    assert len(calls) == 1
```
(If `UsgsDownstream` isn't already imported at the top of `test_usgs.py`, import it. Make sure `main()` picks up the new test; it does if it collects `test_*` from globals.)

- [ ] **Step 2: Run** `python rate_of_rise/tests/test_usgs.py`. Expected: `AttributeError: ... no attribute 'history_between'`.

- [ ] **Step 3: Implement** in `usgs.py` (add `timezone` to the `datetime` import):

```python
def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
```
Replace `poll` and add the two methods:
```python
    def _base_url(self) -> str:
        return ("https://waterservices.usgs.gov/nwis/iv/?format=json"
                f"&sites={','.join(self._sites)}"
                f"&parameterCd={DISCHARGE},{GAGE_HEIGHT}")

    def _series(self, url: str) -> list:
        series = ((self._fetch(url).get("value") or {}).get("timeSeries")) or []
        return [self._describe(ts) for ts in series]

    def poll(self) -> dict:
        return self._features(self._series(self._base_url() + f"&period=PT{RISE_WINDOW_H * 2}H"),
                              None)

    def history_between(self, start: datetime, end: datetime):
        """One fetch covering [start - 6 h, end]; the evaluator answers as poll() would have."""
        described = self._series(self._base_url()
                                 + f"&startDT={_iso(start - timedelta(hours=RISE_WINDOW_H * 2))}"
                                 + f"&endDT={_iso(end)}")
        return lambda as_of: self._features(described, as_of)

    def _features(self, described, as_of: datetime | None) -> dict:
        out: dict[str, float | None] = {k: None for k in feature_keys()}
        for site, param, points in described:
            label = self._sites.get(site)
            if as_of is not None:
                lo = as_of - timedelta(hours=RISE_WINDOW_H * 2)
                points = [p for p in points if lo <= p[0] <= as_of]
            if label is None or not points:
                continue
            latest = points[-1][1]
            if param == GAGE_HEIGHT:
                out[f"usgs_{label}_gage_ft"] = round(latest, 3)
                rise = self._rise(points)
                out[f"usgs_{label}_rise_3h_ft"] = None if rise is None else round(rise, 3)
            elif param == DISCHARGE:
                out[f"usgs_{label}_flow_cfs"] = round(latest, 2)
        return out
```

- [ ] **Step 4: Run** `python rate_of_rise/tests/test_usgs.py`. Expected: all pass, old and new.

- [ ] **Step 5: Live check of the date format** (needs internet):
```bash
curl -s "https://waterservices.usgs.gov/nwis/iv/?format=json&sites=01534860&parameterCd=00065&startDT=2026-10-06T08:00Z&endDT=2026-10-06T10:00Z" | python -c "import json,sys;d=json.load(sys.stdin);print(len(d['value']['timeSeries'][0]['values'][0]['value']))"
```
Expected: a number > 0. If NWIS rejects the `Z` form (HTTP 400), change `_iso` to `strftime("%Y-%m-%dT%H:%M") + "-00:00"`, update the test's expected strings, and re-run.

- [ ] **Step 6: Commit** `git commit -am "usgs: history_between for backfill gap rows" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`

### 16b — NWS alerts

- [ ] **Step 1: Failing test** (append to `tests/test_alerts.py`)

```python
def test_history_between_uses_effective_and_expiry_windows():
    from datetime import datetime, timezone
    calls = []
    doc = {"features": [
        {"properties": {"event": "Flood Watch", "onset": "2026-10-07T10:00:00-04:00",
                        "ends": "2026-10-07T20:00:00-04:00"}},
        {"properties": {"event": "Flash Flood Warning", "effective": "2026-10-07T13:00:00-04:00",
                        "expires": "2026-10-07T14:00:00-04:00"}}]}

    def fetch(url, timeout=15.0):
        calls.append(url)
        return doc

    at = NwsAlerts(41.5, -75.9, fetch=fetch).history_between(
        datetime(2026, 10, 7, 12, tzinfo=timezone.utc), datetime(2026, 10, 8, tzinfo=timezone.utc))
    assert "alerts?point=41.5,-75.9&start=2026-10-07T12:00:00Z&end=2026-10-08T00:00:00Z" in calls[0]
    before = at(datetime(2026, 10, 7, 13, tzinfo=timezone.utc))         # 09:00 EDT
    assert before["nws_flood_watch"] == 0.0 and before["nws_alert_count"] == 0.0
    during = at(datetime(2026, 10, 7, 17, 30, tzinfo=timezone.utc))     # 13:30 EDT
    assert during["nws_flood_watch"] == 1.0 and during["nws_flash_flood_warning"] == 1.0
    assert during["nws_flood_warning"] == 1.0 and during["nws_alert_count"] == 2.0
    after = at(datetime(2026, 10, 7, 19, tzinfo=timezone.utc))          # 15:00 EDT
    assert after["nws_flash_flood_warning"] == 0.0 and after["nws_flood_watch"] == 1.0
    assert len(calls) == 1
```

- [ ] **Step 2: Run** `python rate_of_rise/tests/test_alerts.py`. Expected: fails on `history_between`.

- [ ] **Step 3: Implement** in `alerts.py`. Imports: `from datetime import datetime, timezone`. Replace `poll` with:

```python
    def poll(self) -> dict:
        url = f"https://api.weather.gov/alerts/active?point={self._lat},{self._lon}"
        events = []
        for f in self._fetch(url).get("features") or []:
            event = ((f.get("properties") or {}).get("event") or "").strip().lower()
            if event:
                events.append(event)
        return self._flags(events, log_active=True)

    def history_between(self, start: datetime, end: datetime):
        """Every product whose window overlaps [start, end], fetched once. The evaluator counts
        the ones in force at `as_of`: onset (else effective) <= as_of < ends (else expires)."""
        iso = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        url = (f"https://api.weather.gov/alerts?point={self._lat},{self._lon}"
               f"&start={iso(start)}&end={iso(end)}")
        spans = []
        for f in self._fetch(url).get("features") or []:
            p = f.get("properties") or {}
            event = (p.get("event") or "").strip().lower()
            try:
                begin = datetime.fromisoformat(p.get("onset") or p.get("effective"))
                finish = datetime.fromisoformat(p.get("ends") or p.get("expires"))
            except (TypeError, ValueError):
                continue
            if event:
                spans.append((begin, finish, event))
        return lambda as_of: self._flags([e for b, f, e in spans if b <= as_of < f],
                                         log_active=False)

    def _flags(self, events: list[str], log_active: bool) -> dict:
        # A Flash Flood Warning is also a flood warning for escalation purposes, so the
        # broader flag is set by either — callers should not have to check both.
        flash = any(_matches(e, FLASH_WARNING_EVENTS) for e in events)
        warning = flash or any(_matches(e, WARNING_EVENTS) for e in events)
        watch = any(_matches(e, WATCH_EVENTS) for e in events)
        if log_active and (warning or watch):
            log.info("NWS active flood products at site: %s", ", ".join(sorted(set(events))))
        return {
            "nws_flood_watch": 1.0 if watch else 0.0,
            "nws_flood_warning": 1.0 if warning else 0.0,
            "nws_flash_flood_warning": 1.0 if flash else 0.0,
            "nws_alert_count": float(len(events)),
        }
```

- [ ] **Step 4: Run** `python rate_of_rise/tests/test_alerts.py && python rate_of_rise/tests/test_alert_notifications.py`. Expected: all pass.

- [ ] **Step 5: Commit** `git commit -am "alerts: history_between for backfill gap rows" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`

### 16c — Radar cells

- [ ] **Step 1: Failing test** (append to `tests/test_radar_cells.py`)

```python
def test_history_between_sees_only_scans_up_to_as_of():
    from datetime import datetime, timezone
    calls = []
    csv_text = ("VALID,STORM_ID,DRCT,SKNT,MAX_DBZ,LAT,LON\n"
                "202610071200,A1,270,20,30,41.6,-76.4\n"
                "202610071230,A1,270,20,30,41.6,-76.3\n")

    def fetch(url, timeout=15.0):
        calls.append(url)
        return csv_text

    at = RadarCells(41.5, -75.9, "BGM", fetch=fetch).history_between(
        datetime(2026, 10, 7, 12, tzinfo=timezone.utc),
        datetime(2026, 10, 7, 13, tzinfo=timezone.utc))
    assert "sts=2026-10-07T11:30Z" in calls[0] and "ets=2026-10-07T13:00Z" in calls[0]
    assert at(datetime(2026, 10, 7, 11, 50, tzinfo=timezone.utc))["radar_cells_tracked"] == 0.0
    assert at(datetime(2026, 10, 7, 12, 5, tzinfo=timezone.utc))["radar_cells_tracked"] == 1.0
    assert at(datetime(2026, 10, 7, 13, 30, tzinfo=timezone.utc))["radar_cells_tracked"] == 0.0
    assert len(calls) == 1
```
(At 13:30 the live poll's window is 13:00–13:30, and the newest scan, 12:30, is outside it, so nothing is tracked.)

- [ ] **Step 2: Run** `python rate_of_rise/tests/test_radar_cells.py`. Expected: fails on `history_between`.

- [ ] **Step 3: Implement** in `radar_cells.py`. Split `poll` into URL building, parsing and evaluation:

```python
    def _url(self, sts: datetime, ets: datetime) -> str:
        fmt = "%Y-%m-%dT%H:%MZ"
        return ("https://mesonet.agron.iastate.edu/cgi-bin/request/gis/nexrad_storm_attrs.py"
                f"?fmt=csv&radar={self._radar}&sts={sts.strftime(fmt)}&ets={ets.strftime(fmt)}")

    def poll(self) -> dict:
        now = self._now()
        by_id = self._parse(self._fetch(self._url(now - timedelta(minutes=FETCH_WINDOW_MIN), now)))
        return self._evaluate(by_id, now)

    def history_between(self, start: datetime, end: datetime):
        """One fetch for [start - FETCH_WINDOW_MIN, end]; the evaluator sees only the scans a
        live poll at `as_of` would have seen."""
        all_rows = self._parse(self._fetch(self._url(start - timedelta(minutes=FETCH_WINDOW_MIN),
                                                     end)))

        def at(as_of: datetime) -> dict:
            naive = as_of.astimezone(timezone.utc).replace(tzinfo=None)
            lo = naive - timedelta(minutes=FETCH_WINDOW_MIN)
            by_id = {}
            for sid, rows in all_rows.items():
                kept = [r for r in rows if lo <= r["valid"] <= naive]
                if kept:
                    by_id[sid] = kept
            return self._evaluate(by_id, as_of)
        return at

    def _evaluate(self, by_id: dict[str, list[dict]], now: datetime) -> dict:
        cells = self._current_cells(by_id)
        # ... the remainder of the old poll() body, unchanged, from `out: dict[...] = {` to
        # `return out` (it already uses `now`, `cells` and `by_id`).
```
Move the old body verbatim into `_evaluate`. `start`/`end` arrive as aware UTC datetimes and `strftime` formats them as UTC wall time, so the URL format is unchanged from the live path.

- [ ] **Step 4: Run** `python rate_of_rise/tests/test_radar_cells.py`. Expected: all pass.

- [ ] **Step 5: Commit** `git commit -am "radar_cells: history_between for backfill gap rows" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`

### 16d — WU upstream

- [ ] **Step 1: Failing test** (append to `tests/test_wu.py`)

```python
def test_history_between_sums_station_totals_up_to_as_of():
    import tempfile
    from datetime import datetime, timezone
    from pathlib import Path as _P
    obs = {"20261007": [
        {"obsTimeUtc": "2026-10-07T12:00:00Z", "obsTimeLocal": "2026-10-07 08:00:00",
         "imperial": {"precipTotal": 0.10}},
        {"obsTimeUtc": "2026-10-07T12:30:00Z", "obsTimeLocal": "2026-10-07 08:30:00",
         "imperial": {"precipTotal": 0.30}},
        {"obsTimeUtc": "2026-10-07T13:00:00Z", "obsTimeLocal": "2026-10-07 09:00:00",
         "imperial": {"precipTotal": 0.35}}]}
    calls = []

    def fetch(url, timeout=15.0):
        calls.append(url)
        day = url.split("date=")[1][:8]
        return {"observations": obs.get(day, [])}

    src = WuUpstream("key", ["KPAX1"], _P(tempfile.mkdtemp()), fetch=fetch)
    at = src.history_between(datetime(2026, 10, 7, 12, 30, tzinfo=timezone.utc),
                             datetime(2026, 10, 7, 13, 0, tzinfo=timezone.utc))
    assert all("/v2/pws/history/all?stationId=KPAX1" in u for u in calls)
    out = at(datetime(2026, 10, 7, 12, 45, tzinfo=timezone.utc))
    assert out["upstream_rain_1h_in"] == 0.2
    assert out["upstream_precip_today_in"] == 0.3
    late = at(datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc))      # nobody fresh
    assert late["upstream_rain_1h_in"] is None
```

- [ ] **Step 2: Run** `python rate_of_rise/tests/test_wu.py`. Expected: fails on `history_between`.

- [ ] **Step 3: Implement** in `wu.py`. Add `from datetime import datetime, timedelta` and:

```python
def _history_series(observations: list[dict]) -> tuple[list[tuple[float, float]],
                                                       list[tuple[float, float]]]:
    """(increments, totals) from one station's history observations, sorted by time.
    precipTotal restarts at local midnight, so a new local day's first total is all new rain;
    a dip within a day is a feed glitch and counts as nothing."""
    pts = []
    for o in observations:
        try:
            ts = datetime.fromisoformat(str(o["obsTimeUtc"]).replace("Z", "+00:00")).timestamp()
            total = float((o.get("imperial") or {})["precipTotal"])
        except (KeyError, TypeError, ValueError):
            continue
        pts.append((ts, str(o.get("obsTimeLocal") or "")[:10], total))
    pts.sort()
    incs, totals = [], []
    prev = None
    for ts, day, total in pts:
        if prev is not None and ts == prev[0]:
            continue
        if prev is None:
            inc = 0.0
        elif day != prev[1]:
            inc = max(0.0, total)
        else:
            inc = max(0.0, total - prev[2])
        if inc > 0:
            incs.append((ts, inc))
        totals.append((ts, total))
        prev = (ts, day, total)
    return incs, totals
```

Add to `WuUpstream`:
```python
    def history_between(self, start: datetime, end: datetime):
        """Each station's PWS history for every UTC day touching [start - 72 h, end], with a
        day's margin on both sides for the station's local dates. The evaluator answers as
        poll() would have at `as_of`, from those totals."""
        per_station = {}
        day = (start - timedelta(hours=72)).date() - timedelta(days=1)
        last = end.date() + timedelta(days=1)
        days = []
        while day <= last:
            days.append(day)
            day += timedelta(days=1)
        for sid in self._stations:
            obs = []
            for d in days:
                url = ("https://api.weather.com/v2/pws/history/all"
                       f"?stationId={sid}&format=json&units=e&date={d:%Y%m%d}&apiKey={self._key}")
                try:
                    obs += self._fetch(url).get("observations") or []
                except Exception:
                    log.debug("WU history %s %s unavailable", sid, d)
            per_station[sid] = _history_series(obs)

        def at(as_of: datetime) -> dict:
            t = as_of.timestamp()
            fresh = [sid for sid, (_, totals) in per_station.items()
                     if any(t - STATION_FRESH_S < ts <= t for ts, _ in totals)]
            if not fresh:
                return {**{f"upstream_rain_{w}h_in": None for w in WINDOWS_H},
                        "upstream_precip_today_in": None}
            out = {}
            for w in WINDOWS_H:
                sums = [sum(i for ts, i in per_station[sid][0] if t - w * 3600 < ts <= t)
                        for sid in fresh]
                out[f"upstream_rain_{w}h_in"] = round(sum(sums) / len(sums), 3)
            latest = [max((ts, tot) for ts, tot in per_station[sid][1] if ts <= t)[1]
                      for sid in fresh]
            out["upstream_precip_today_in"] = round(sum(latest) / len(latest), 3)
            return out
        return at
```
(`WINDOWS_H` is already imported in `wu.py` from `.accumulator`; check, and import it if not.)

- [ ] **Step 4: Run** `python rate_of_rise/tests/test_wu.py`. Expected: all pass.

- [ ] **Step 5: Commit** `git commit -am "wu: history_between from PWS history for backfill gap rows" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`

### 16e — SNODAS, and the coordinator accessors

- [ ] **Step 1: Failing tests** (append to `tests/test_snodas.py` and `tests/test_sources.py`)

`test_snodas.py`:
```python
def test_history_between_fetches_each_day_once_without_evicting_the_live_cache():
    import tempfile
    from datetime import date, datetime, timezone
    from pathlib import Path as _P
    src = SnodasSwe(41.5, -75.9, _P(tempfile.mkdtemp()), today_fn=lambda: date(2026, 10, 7))
    fetched = []

    def fake_fetch_day(day):
        fetched.append(day)
        return 1.5 if day == date(2026, 10, 1) else 0.0
    src._fetch_day = fake_fetch_day
    at = src.history_between(datetime(2026, 10, 1, tzinfo=timezone.utc),
                             datetime(2026, 10, 1, 6, tzinfo=timezone.utc))
    for hour in (0, 2, 4):
        assert at(datetime(2026, 10, 1, hour, tzinfo=timezone.utc)) == {
            "snow_water_equivalent_in": 1.5}
    assert fetched == [date(2026, 10, 1)]
    assert "2026-10-01" not in src._cache
```

`test_sources.py` (uses the file's `make_empty_coordinator()` and `FakeSource`):
```python
def test_history_sources_and_rain_accumulator_accessors():
    c = make_empty_coordinator()
    assert c.history_sources() == [] and c.rain_accumulator() is None

    class Hist(FakeSource):
        name = "usgs"

        def history_between(self, start, end):
            return lambda as_of: {}

    class Rain(FakeSource):
        name = "rain"

    hist, rain = Hist(), Rain()
    c._sources += [FakeSource(), hist, rain]
    assert c.history_sources() == [hist]
    assert c.rain_accumulator() is rain
```

- [ ] **Step 2: Run** both files. Expected: failures on the missing methods.

- [ ] **Step 3: Implement**

`snodas.py`: add `import threading`; in `__init__`, `self._lock = threading.Lock()`. Replace the `poll` lookup loop with a shared helper:
```python
    def poll(self) -> dict:
        if not (0 <= self._row < NROWS and 0 <= self._col < NCOLS):
            log.warning("site falls outside the SNODAS grid — SWE unavailable")
            return {"snow_water_equivalent_in": None}
        swe = self._swe_for(self._today(), persist=True)
        if swe is _NONE_FOUND:
            log.warning("no SNODAS file in the last %d days", MAX_LOOKBACK_DAYS)
            return {"snow_water_equivalent_in": None}
        return {"snow_water_equivalent_in": swe}

    def history_between(self, start, end):
        """SWE as of each past day. The live cache keeps only the newest days, so past days
        are memoised here, per backfill, instead of in it."""
        memo: dict = {}

        def at(as_of):
            day = as_of.date()
            if day not in memo:
                swe = self._swe_for(day, persist=False)
                memo[day] = None if swe is _NONE_FOUND else swe
            return {"snow_water_equivalent_in": memo[day]}
        return at

    def _swe_for(self, today: date, persist: bool):
        for back in range(MAX_LOOKBACK_DAYS):
            day = today - timedelta(days=back)
            key = day.isoformat()
            with self._lock:
                if key in self._cache:
                    return self._cache[key]
            try:
                swe_in = self._fetch_day(day)
            except Exception as exc:  # not yet posted, or a transient network error
                log.debug("SNODAS %s unavailable: %s", key, exc)
                continue
            if persist:
                with self._lock:
                    self._cache[key] = swe_in
                    for stale in sorted(self._cache)[:-MAX_LOOKBACK_DAYS]:
                        del self._cache[stale]
                    self._save_cache()
                log.info("SNODAS %s: SWE %s in", key, swe_in)
            return swe_in
        return _NONE_FOUND
```
with a module-level sentinel `_NONE_FOUND = object()`.

`sources/__init__.py`, in `SourceCoordinator`:
```python
    def history_sources(self) -> list:
        """Sources that can answer for a past time (app/backfill/gaprows.py)."""
        return [s for s in self._sources if hasattr(s, "history_between")]

    def rain_accumulator(self):
        """The live on-site rain accumulator, which backfill corrects after a gap."""
        return next((s for s in self._sources if s.name == "rain"), None)
```

- [ ] **Step 4: Run all source tests**

```bash
for t in usgs alerts radar_cells wu snodas sources rain apindex; do python rate_of_rise/tests/test_$t.py || break; done
```
Expected: every file passes.

- [ ] **Step 5: Commit** `git commit -am "snodas: history_between; coordinator: history_sources, rain_accumulator" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`

---

## Task 17: Thread-safe dataset appends and a correctable rain accumulator

**Files:**
- Modify: `rate_of_rise/app/dataset.py`, `app/sources/accumulator.py`, `app/sources/apindex.py`, `app/sources/rain.py`
- Test: add tests to `rate_of_rise/tests/test_dataset.py`, `test_rain.py`, `test_apindex.py`

**Interfaces:**
- Produces:
  - `DatasetWriter.append_record(record: dict) -> None`, thread-safe; `append_row` and `consolidate` take the same lock.
  - `RollingAccumulator.increments() -> list[tuple[float, float]]`; `RollingAccumulator.replace_window(start: float, end: float, increments: list[tuple[float, float]]) -> list[tuple[float, float]]` (returns the removed ones).
  - `PrecipIndex.adjust(added: list[tuple[float, float]], removed: list[tuple[float, float]]) -> None`.
  - `RainAccumulator.snapshot() -> list[tuple[float, float]]`; `RainAccumulator.replace_window(start, end, increments) -> None`; `poll()` takes the same lock.

- [ ] **Step 1: Failing tests**

Append to `tests/test_apindex.py`:
```python
def test_adjust_adds_and_removes_past_rain_with_decay():
    import tempfile
    from pathlib import Path as _P
    clock = [1_000_000.0]
    idx = PrecipIndex(_P(tempfile.mkdtemp()) / "api.json", k=0.5, now_fn=lambda: clock[0])
    idx.update(0.0)                                   # anchor at t
    idx.adjust(added=[(clock[0] - 86400.0, 1.0)], removed=[])
    assert abs(idx.value - 0.5) < 1e-3                # a day old at k=0.5: half counts
    idx.adjust(added=[], removed=[(clock[0] - 86400.0, 1.0)])
    assert abs(idx.value) < 1e-3
    idx.adjust(added=[(clock[0] + 10, 1.0)], removed=[])   # in the future: ignored
    assert abs(idx.value) < 1e-3
```

Append to `tests/test_rain.py` (use the file's existing fake HA and clock helpers; the example below assumes a `RainAccumulator(data_dir, "sensor.rate", ha, now_fn=clock)` constructor, as in `rain.py`):
```python
def test_replace_window_swaps_increments_and_fixes_the_api():
    import tempfile
    from pathlib import Path as _P

    class HA:
        def get_float(self, _):
            return None

        def get_unit(self, _):
            return "in/h"

    clock = [2_000_000.0]
    rain = RainAccumulator(_P(tempfile.mkdtemp()), "sensor.rate", HA(), now_fn=lambda: clock[0])
    rain._acc.add(0.5)                                  # a "lump" counted at restart time
    rain._api.update(0.5)
    rain.replace_window(clock[0] - 3600, clock[0], [(clock[0] - 3000, 0.2),
                                                    (clock[0] - 1200, 0.3)])
    assert sorted(rain.snapshot()) == [(clock[0] - 3000, 0.2), (clock[0] - 1200, 0.3)]
    assert rain._acc.sums()[1] == 0.5
    assert rain._api.value < 0.5                        # same rain, but some of it is older
```

Append to `tests/test_dataset.py`:
```python
def test_append_record_from_another_thread():
    import tempfile
    import threading
    from pathlib import Path as _P
    ds = DatasetWriter(_P(tempfile.mkdtemp()))
    base = 1_791_000_000.0
    threads = [threading.Thread(target=lambda i=i: ds.append_record(
        {"ts": base + i, "stage_ft": 1.0, "backfilled": True})) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    frame = ds.frame()
    assert len(frame) == 50 and bool(frame["backfilled"].all())
```

- [ ] **Step 2: Run** the three files. Expected: failures on the missing methods.

- [ ] **Step 3: Implement**

`apindex.py`, add to `PrecipIndex`:
```python
    def adjust(self, added: list[tuple[float, float]], removed: list[tuple[float, float]]) -> None:
        """Correct the index for rain that fell in the past (backfill): each amount counts as
        it would have, decayed from when it fell to the index's last update."""
        if self._ts is None:
            return
        decay = lambda t: self._k ** ((self._ts - t) / 86400.0)  # noqa: E731
        delta = (sum(i * decay(t) for t, i in added if t <= self._ts)
                 - sum(i * decay(t) for t, i in removed if t <= self._ts))
        self._value = max(0.0, self._value + delta)
        self._save()
```

`accumulator.py`, add to `RollingAccumulator`:
```python
    def increments(self) -> list[tuple[float, float]]:
        return [(float(t), float(i)) for t, i in self._increments]

    def replace_window(self, start: float, end: float,
                       increments: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """Replace what was recorded in [start, end] with `increments` (backfill: the
        Ecowitt's own counter deltas for a time this accumulator could not see). Returns
        what was removed, so the API index can be corrected by the same amounts."""
        removed = [(t, i) for t, i in self._increments if start <= t <= end]
        kept = [[t, i] for t, i in self._increments if not start <= t <= end]
        added = [[t, i] for t, i in increments if start <= t <= end and i > 0]
        cutoff = self._now() - self._retain
        self._increments = sorted(x for x in kept + added if x[0] >= cutoff)
        self._save()
        return removed
```

`rain.py`: `import threading`; in `__init__`, `self._lock = threading.RLock()`. Wrap the whole body of `poll()` in `with self._lock:`. Add:
```python
    def snapshot(self) -> list[tuple[float, float]]:
        with self._lock:
            return self._acc.increments()

    def replace_window(self, start: float, end: float,
                       increments: list[tuple[float, float]]) -> None:
        """Backfill's correction (app/backfill/gaprows.py): the gap's rain, from the station's
        own counter, in place of what this accumulator recorded (nothing, or one lump at
        restart). Runs on the backfill thread, hence the lock shared with poll()."""
        with self._lock:
            removed = self._acc.replace_window(start, end, increments)
            added = [(t, i) for t, i in increments if start <= t <= end and i > 0]
            self._api.adjust(added=added, removed=removed)
```

`dataset.py`: `import threading`; in `__init__`, `self._lock = threading.Lock()`. Replace `append_row`'s write with a call to a new method, and lock `consolidate`:
```python
    def append_row(self, row: FeatureRow, outputs: dict | None = None) -> None:
        """...(docstring unchanged)..."""
        record = row.as_dict()
        if outputs:
            record.update(outputs)
        self.append_record(record)

    def append_record(self, record: dict) -> None:
        """Append one already-built record to its day's part file. Thread-safe: the backfill
        appends gap rows from its own thread while the fast loop appends today's."""
        with self._lock:
            try:
                with self._part_path(record["ts"]).open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record) + "\n")
            except OSError as exc:
                log.error("could not append feature row: %s", exc)
```
In `consolidate`, put the whole body after the docstring under `with self._lock:`. A backfill append to a past day's part file must not land between consolidate's read of that file and its delete.

- [ ] **Step 4: Run**

```bash
for t in apindex rain dataset sources; do python rate_of_rise/tests/test_$t.py || break; done
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add rate_of_rise/app/dataset.py rate_of_rise/app/sources/accumulator.py rate_of_rise/app/sources/apindex.py rate_of_rise/app/sources/rain.py rate_of_rise/tests/test_dataset.py rate_of_rise/tests/test_rain.py rate_of_rise/tests/test_apindex.py
git commit -m "dataset/rain: thread-safe appends, replaceable accumulator window, API adjust

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 18: `GapFiller` — dataset rows for the time the add-on couldn't see

**Files:**
- Create: `rate_of_rise/app/backfill/gaprows.py`
- Test: `rate_of_rise/tests/test_backfill_gaprows.py`

**Interfaces:**
- Consumes: `features.FeatureRow`, `features.FeatureBuilder._rain_on_snow`, `features.stage_history_features`, `features.PONDING_SATURATION_PCT`, `features.RATE_WINDOW_SLACK_S`; `sources.accumulator.WINDOWS_H`; `sources.apindex.DEFAULT_K`; `DatasetWriter.frame()` and `.append_record()` (Task 17); `RainAccumulator.snapshot()` and `.replace_window()` (Task 17); `SourceCoordinator.history_sources()` (Task 16).
- Produces: `missing_slots(existing_ts, start, end, interval) -> list[float]`; `eco_increments(eco: list[dict]) -> list[tuple[float, float]]`; `GapFiller(cfg, dataset, sources=None, soil_channels: dict[str, str] | None = None, rain=None)` with `fill(node: list[dict], eco: list[dict]) -> int` (rows appended). `soil_channels` maps `"near_house"`/`"near_creek"` to an Ecowitt channel string such as `"2"`.

- [ ] **Step 1: Write the failing tests**

`rate_of_rise/tests/test_backfill_gaprows.py`:
```python
"""Dataset gap rows built from gateway records.

Two kinds of gap: slots with no row at all (the HA host, and the add-on with it, was down),
and "blind" rows the add-on wrote while HA Core alone was down (every HA read failed, so no
stage and no on-site rain). Both are filled from the gateway's node and Ecowitt records.

Run: python rate_of_rise/tests/test_backfill_gaprows.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backfill.gaprows import GapFiller, eco_increments, missing_slots  # noqa: E402

T = 1_791_300_000.0
CFG = SimpleNamespace(fast_loop_minutes=5, rate_of_rise_window_minutes=10.0,
                      rate_of_rise_max_gap_minutes=10.0,
                      soil_moisture_entities=["sensor.willow", "sensor.field"])


class FakeDataset:
    def __init__(self, rows):
        self.rows, self.appended = rows, []

    def frame(self):
        return pd.DataFrame(self.rows) if self.rows else pd.DataFrame(columns=["ts"])

    def append_record(self, record):
        self.appended.append(record)


class FakeRain:
    def __init__(self, ring=()):
        self.ring, self.calls = list(ring), []

    def snapshot(self):
        return list(self.ring)

    def replace_window(self, start, end, increments):
        self.calls.append((start, end, list(increments)))


def node_records(t0=T + 60, t1=T + 2700):
    out, seq, t = [], 1, t0
    while t <= t1:
        out.append({"seq": seq, "ts": t, "ts_src": "ntp", "stage_ft": round(1.0 + 0.0001 * seq, 4)})
        seq, t = seq + 1, t + 60
    return out


def eco_records(t0=T, t1=T + 2700, rain_from=T + 900):
    out, seq, t = [], 1, t0
    while t <= t1:
        steps = max(0, int((t - rain_from) // 60))
        out.append({"seq": seq, "ts": t, "ts_src": "ntp", "rain_year_in": round(10.0 + 0.01 * steps, 2),
                    "rain_rate_in_hr": 0.6 if steps else 0.0, "temp_f": 50.0, "soil": {"2": 70}})
        seq, t = seq + 1, t + 60
    return out


def live_row(ts, stage=1.0, online=True):
    return {"ts": ts, "stage_ft": stage, "creek_node_online": online, "api_index_in": 1.0,
            "alert_tier": 0}


def test_missing_slots():
    assert missing_slots([T, T + 300, T + 2400, T + 2700], T, T + 2700, 300) == [
        T + 600, T + 900, T + 1200, T + 1500, T + 1800, T + 2100]
    assert missing_slots([T + i * 300 for i in range(10)], T, T + 2700, 300) == []
    assert missing_slots([], T, T + 900, 300) == [T, T + 300, T + 600, T + 900]


def test_eco_increments_handle_resets_and_long_gaps():
    eco = [{"ts": 0.0, "rain_year_in": 1.0}, {"ts": 60.0, "rain_year_in": 1.05},
           {"ts": 120.0, "rain_year_in": 0.0},                  # yearly reset
           {"ts": 180.0, "rain_year_in": 0.02},
           {"ts": 5000.0, "rain_year_in": 0.5},                 # over an hour later: not placeable
           {"ts": 5060.0, "rain_year_in": None}]
    assert eco_increments(eco) == [(60.0, 0.05), (180.0, 0.02)]


def test_fills_missing_slots_from_node_and_ecowitt_records():
    ds = FakeDataset([live_row(T), live_row(T + 300), live_row(T + 2400), live_row(T + 2700)])
    rain = FakeRain()
    n = GapFiller(CFG, ds, soil_channels={"near_creek": "2"}, rain=rain).fill(
        node_records(), eco_records())
    assert n == 6
    rows = {r["ts"]: r for r in ds.appended}
    assert sorted(rows) == [T + 600, T + 900, T + 1200, T + 1500, T + 1800, T + 2100]
    r = rows[T + 1500]
    assert r["backfilled"] is True and r["creek_node_online"] is True
    assert r["stage_ft"] == round(1.0 + 0.0001 * 25, 4)        # node record at T+1500 is seq 25
    assert r["rate_of_rise_in_min"] is not None
    assert r["rain_1h_in"] == 0.1 and r["rain_rate_in_hr"] == 0.6
    assert r["soil_moisture_near_creek_pct"] == 70 and r["soil_moisture_near_house_pct"] is None
    assert r["api_index_in"] is not None and r["api_index_in"] > 1.0
    assert r["qpf_6h_in"] is None and r["nwm_flow_cfs"] is None        # forecasts stay empty
    assert rain.calls and rain.calls[0][0] == T and rain.calls[0][1] == T + 2700


def test_blind_rows_are_reissued_with_the_same_ts():
    rows = [live_row(T + i * 300) for i in range(10)]
    for r in rows[2:8]:
        r.update(stage_ft=None, creek_node_online=None)
    ds = FakeDataset(rows)
    n = GapFiller(CFG, ds, rain=FakeRain()).fill(node_records(), eco_records())
    assert n == 6
    assert sorted(r["ts"] for r in ds.appended) == [T + i * 300 for i in range(2, 8)]
    assert all(r["stage_ft"] is not None and r["alert_tier"] == 0 and r["backfilled"]
               for r in ds.appended)


def test_no_gap_appends_nothing_and_leaves_rain_alone():
    ds = FakeDataset([live_row(T + i * 300) for i in range(10)])
    rain = FakeRain()
    assert GapFiller(CFG, ds, rain=rain).fill(node_records(), eco_records()) == 0
    assert ds.appended == [] and rain.calls == []


def test_rain_left_alone_when_ecowitt_records_have_an_hour_gap():
    ds = FakeDataset([live_row(T), live_row(T + 6000)])
    rain = FakeRain()
    eco = [e for e in eco_records(T, T + 6000) if not T + 600 < e["ts"] < T + 4800]
    GapFiller(CFG, ds, rain=rain).fill(node_records(T + 60, T + 6000), eco)
    assert rain.calls == []


def test_refetched_sources_are_merged_and_failures_tolerated():
    class Ok:
        name = "usgs"

        def history_between(self, start, end):
            assert start.tzinfo is timezone.utc
            return lambda as_of: {"usgs_leggetts_gage_ft": 2.0}

    class Broken:
        name = "wu"

        def history_between(self, start, end):
            raise RuntimeError("history API down")

    sources = SimpleNamespace(history_sources=lambda: [Ok(), Broken()])
    ds = FakeDataset([live_row(T), live_row(T + 300), live_row(T + 2400), live_row(T + 2700)])
    GapFiller(CFG, ds, sources=sources, rain=FakeRain()).fill(node_records(), eco_records())
    assert ds.appended and all(r["usgs_leggetts_gage_ft"] == 2.0 for r in ds.appended)
    assert all(r["upstream_rain_1h_in"] is None for r in ds.appended)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python rate_of_rise/tests/test_backfill_gaprows.py`
Expected: `ModuleNotFoundError: No module named 'app.backfill.gaprows'`.

- [ ] **Step 3: Implement `gaprows.py`**

```python
"""Dataset rows for the time the add-on could not see.

When the HA host is down the add-on is down with it, so the fast loop writes no rows. When
HA Core alone is down the add-on keeps running but every HA read fails, so it writes "blind"
rows with no stage and no on-site rain. Either way the gateway's records cover the gap.

This builds the missing rows and re-issues the blind ones (same ts; the dataset keeps the
last row per ts), using the same feature code as the live loop where it can:
stage_history_features for the stage history, the same rate-of-rise window rule, the same
soil/ponding rule, FeatureBuilder._rain_on_snow. On-site rain comes from the GW3000B's
yearly counter, the way rain.py uses HA's copy of it. Sources that keep history are
re-fetched (history_between); forecasts stay empty, because a forecast as it was issued
cannot be fetched afterwards. Every row it writes carries backfilled=True.

It also corrects the live on-site rain accumulator for the gap. Without that, the 24 h and
72 h totals and the API index read short for days after an outage, and in the add-on-
restarted-within-the-hour case one lump of rain lands at restart time.
"""
from __future__ import annotations

import bisect
import logging
import time
from dataclasses import fields
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ..features import (PONDING_SATURATION_PCT, RATE_WINDOW_SLACK_S, FeatureBuilder,
                        FeatureRow, stage_history_features)
from ..sources.accumulator import WINDOWS_H
from ..sources.apindex import DEFAULT_K

log = logging.getLogger("app.backfill.gaprows")

NODE_FRESH_S = 6 * 60.0       # a node record this recent counts as the link being up
ECO_FRESH_S = 5 * 60.0
STAGE_CONTEXT_S = 6 * 3600.0  # stage_history_features looks back 6 h
COUNTER_MAX_GAP_S = 3600.0    # as rain.py: a counter delta across a longer gap cannot be placed
FIELD_NAMES = [f.name for f in fields(FeatureRow)]


def missing_slots(existing_ts, start: float, end: float, interval: float) -> list[float]:
    """Fast-loop slots in [start, end] with no dataset row near them."""
    ts = sorted(t for t in existing_ts if start - interval <= t <= end + interval)
    anchors = [start - interval] + ts + [end + interval]
    out = []
    for a, b in zip(anchors, anchors[1:]):
        t = a + interval
        while t < b - interval / 2:
            if start <= t <= end:
                out.append(round(t, 1))
            t += interval
    return out


def eco_increments(eco: list[dict]) -> list[tuple[float, float]]:
    """Rain between consecutive Ecowitt records, from the yearly counter."""
    out, prev = [], None
    for r in eco:
        total = r.get("rain_year_in")
        if total is None:
            continue
        if prev is not None and 0 < r["ts"] - prev[0] <= COUNTER_MAX_GAP_S and total > prev[1]:
            out.append((r["ts"], round(total - prev[1], 4)))
        prev = (r["ts"], total)
    return out


def _window_sum(incs, t: float, hours: int) -> float:
    return round(sum(i for ts, i in incs if t - hours * 3600 < ts <= t), 3)


def _api_at(base, incs, t: float, k: float = DEFAULT_K):
    if base is None or t < base[0]:
        return None
    b_ts, b_val = base
    v = b_val * k ** ((t - b_ts) / 86400.0)
    v += sum(i * k ** ((t - ts) / 86400.0) for ts, i in incs if b_ts < ts <= t)
    return round(v, 3)


def _plain(v):
    """pandas/numpy scalars and NaN -> plain JSON-safe values."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        return v
    return v.item() if hasattr(v, "item") else v


def _opt(v):
    return None if v is None or np.isnan(v) else round(float(v), 3)


class GapFiller:
    def __init__(self, cfg, dataset, sources=None, soil_channels: dict | None = None, rain=None):
        self._cfg = cfg
        self._dataset = dataset
        self._sources = sources
        self._soil = soil_channels or {}
        self._rain = rain
        self._interval = max(1, cfg.fast_loop_minutes) * 60.0
        self._last_eco: dict | None = None   # so a counter delta can span two passes

    def fill(self, node: list[dict], eco: list[dict]) -> int:
        node = sorted(node, key=lambda r: r["ts"])
        eco = sorted(eco, key=lambda r: r["ts"])
        prev_eco, self._last_eco = self._last_eco, (eco[-1] if eco else self._last_eco)
        stamps = [r["ts"] for r in node] + [r["ts"] for r in eco]
        if not stamps:
            return 0
        start, end = min(stamps), max(stamps)

        frame = self._dataset.frame()
        ctx_rows: list[dict] = []
        if len(frame) and "ts" in frame:
            ctx = frame[(frame["ts"] >= start - STAGE_CONTEXT_S) & (frame["ts"] <= end + self._interval)]
            ctx_rows = [{k: _plain(v) for k, v in row.items()} for row in ctx.to_dict("records")]
        node_ts = [r["ts"] for r in node]
        eco_ts = [r["ts"] for r in eco]

        slots = missing_slots([r["ts"] for r in ctx_rows], start, end, self._interval)
        blind = [r for r in ctx_rows
                 if start <= r["ts"] <= end and r.get("stage_ft") is None
                 and r.get("creek_node_online") is not True and self._fresh(node_ts, r["ts"])]
        if not slots and not blind:
            return 0

        eco_incs = eco_increments(([prev_eco] if prev_eco else []) + eco)
        ring = self._rain.snapshot() if self._rain is not None else []
        if eco_ts:
            ring = [x for x in ring if not eco_ts[0] <= x[0] <= eco_ts[-1]]
        incs = sorted(ring + eco_incs)
        # The API index decays from the last live row before the gap. Blind rows are not live:
        # their index was computed without the gap's rain.
        gap_start = min(slots + [b["ts"] for b in blind])
        blind_ts = {b["ts"] for b in blind}
        api_base = next(((r["ts"], r["api_index_in"]) for r in reversed(ctx_rows)
                         if r["ts"] < gap_start and r["ts"] not in blind_ts
                         and r.get("api_index_in") is not None), None)
        evaluators = self._evaluators(start, end)

        built: dict[float, dict] = {}
        for ts in slots:
            row = {k: None for k in FIELD_NAMES}
            row.update(ts=ts, ponding_flag=False, rain_on_snow_flag=False)
            row.update(self._node_features(ts, node, node_ts))
            row.update(self._eco_features(ts, eco, eco_ts, incs, api_base))
            row.update(self._refetched(ts, evaluators))
            built[ts] = row
        for orig in blind:
            row = dict(orig)
            row.update(self._node_features(orig["ts"], node, node_ts))
            row.update(self._eco_features(orig["ts"], eco, eco_ts, incs, api_base))
            built[orig["ts"]] = row

        series = {r["ts"]: (r.get("stage_ft") if r.get("creek_node_online") is True else None)
                  for r in ctx_rows}
        for ts, row in built.items():
            series[ts] = row.get("stage_ft") if row.get("creek_node_online") is True else None
        order = sorted(series)
        change, above = stage_history_features(
            order, [np.nan if series[t] is None else series[t] for t in order])
        index = {t: i for i, t in enumerate(order)}

        for ts in sorted(built):
            row = built[ts]
            i = index[ts]
            row["stage_change_1h_in"], row["stage_above_6h_low_in"] = _opt(change[i]), _opt(above[i])
            row["rain_on_snow_flag"] = FeatureBuilder._rain_on_snow(
                FeatureRow(**{k: row.get(k) for k in FIELD_NAMES}))
            row["backfilled"] = True
            self._dataset.append_record(row)

        self._correct_rain(eco_ts, eco_incs)
        log.info("backfill: wrote %d dataset row(s) (%d new, %d re-issued) for %s – %s",
                 len(built), len(slots), len(blind),
                 datetime.fromtimestamp(start, timezone.utc).isoformat(timespec="minutes"),
                 datetime.fromtimestamp(end, timezone.utc).isoformat(timespec="minutes"))
        return len(built)

    @staticmethod
    def _fresh(stamps: list[float], ts: float, within: float = NODE_FRESH_S) -> bool:
        i = bisect.bisect_right(stamps, ts) - 1
        return i >= 0 and ts - stamps[i] <= within

    def _node_features(self, ts: float, node: list[dict], node_ts: list[float]) -> dict:
        i = bisect.bisect_right(node_ts, ts) - 1
        if i < 0 or ts - node_ts[i] > NODE_FRESH_S:
            return {"creek_node_online": False, "stage_held": False}
        rec = node[i]
        stage = rec.get("stage_ft")
        return {"stage_ft": stage, "stage_raw_ft": stage, "creek_node_online": True,
                "stage_age_min": round((ts - rec["ts"]) / 60.0, 2), "stage_held": False,
                "rate_of_rise_in_min": self._rate(node, node_ts, i)}

    def _rate(self, node: list[dict], node_ts: list[float], i: int):
        """FeatureBuilder._rate_of_rise's rule: against the newest reading at least a window old,
        and not across a gap longer than rate_of_rise_max_gap_minutes."""
        need = self._cfg.rate_of_rise_window_minutes * 60.0 - RATE_WINDOW_SLACK_S
        t1, s1 = node[i]["ts"], node[i].get("stage_ft")
        j = bisect.bisect_right(node_ts, t1 - need) - 1
        if s1 is None or j < 0:
            return None
        max_gap = self._cfg.rate_of_rise_max_gap_minutes * 60.0
        if any(b - a > max_gap for a, b in zip(node_ts[j:i], node_ts[j + 1:i + 1])):
            return None
        t0, s0 = node[j]["ts"], node[j].get("stage_ft")
        if s0 is None:
            return None
        return round((s1 - s0) * 12.0 / ((t1 - t0) / 60.0), 4)

    def _eco_features(self, ts, eco, eco_ts, incs, api_base) -> dict:
        k = bisect.bisect_right(eco_ts, ts) - 1
        if k < 0 or ts - eco_ts[k] > ECO_FRESH_S:
            return {}
        rec = eco[k]
        out = {f"rain_{w}h_in": _window_sum(incs, ts, w) for w in WINDOWS_H}
        out["rain_rate_in_hr"] = rec.get("rain_rate_in_hr")
        out["temp_f"] = rec.get("temp_f")
        out["api_index_in"] = _api_at(api_base, incs, ts)
        soil = rec.get("soil") or {}
        house = soil.get(self._soil.get("near_house")) if self._soil.get("near_house") else None
        creek = soil.get(self._soil.get("near_creek")) if self._soil.get("near_creek") else None
        present = [v for v in (house, creek) if v is not None]
        out.update(soil_moisture_near_house_pct=house, soil_moisture_near_creek_pct=creek,
                   soil_moisture_mean_pct=sum(present) / len(present) if present else None,
                   ponding_flag=any(v >= PONDING_SATURATION_PCT for v in present))
        return out

    def _evaluators(self, start: float, end: float) -> list:
        if self._sources is None:
            return []
        s = datetime.fromtimestamp(start, timezone.utc)
        e = datetime.fromtimestamp(end, timezone.utc)
        out = []
        for src in self._sources.history_sources():
            try:
                out.append(src.history_between(s, e))
            except Exception as exc:
                log.warning("backfill: %s history unavailable, its features stay empty: %s",
                            getattr(src, "name", src), exc)
        return out

    @staticmethod
    def _refetched(ts: float, evaluators) -> dict:
        out: dict = {}
        when = datetime.fromtimestamp(ts, timezone.utc)
        for ev in evaluators:
            try:
                out.update(ev(when))
            except Exception:
                log.debug("backfill: history evaluator failed at %s", when, exc_info=True)
        return out

    def _correct_rain(self, eco_ts: list[float], eco_incs: list[tuple[float, float]]) -> None:
        if self._rain is None or len(eco_ts) < 2:
            return
        if any(b - a > COUNTER_MAX_GAP_S for a, b in zip(eco_ts, eco_ts[1:])):
            log.info("backfill: Ecowitt records have a gap over an hour; live rain totals left "
                     "as they are")
            return
        self._rain.replace_window(eco_ts[0], eco_ts[-1], eco_incs)
```

- [ ] **Step 4: Run tests**

Run: `python rate_of_rise/tests/test_backfill_gaprows.py`
Expected: `7 passed`. If `test_fills_missing_slots...` fails on `rain_1h_in`, print `eco_increments(eco_records())[:3]` to check each increment is `0.01`; float rounding in the fixture can make a total like `10.010000000000002`, which `round(..., 2)` in the fixture prevents.

- [ ] **Step 5: Commit**

```bash
git add rate_of_rise/app/backfill/gaprows.py rate_of_rise/tests/test_backfill_gaprows.py
git commit -m "backfill: dataset gap rows from gateway records, re-fetched history, rain fix

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 19: Reconciler, the backfill thread, and wiring it into the add-on

**Files:**
- Create: `rate_of_rise/app/backfill/reconciler.py`
- Modify: `rate_of_rise/app/backfill/__init__.py` (add `build_backfill`, `soil_channels`)
- Modify: `rate_of_rise/app/__main__.py` (build it at startup, stop it on exit)
- Modify: `rate_of_rise/app/discovery.py` (status sensor)
- Test: `rate_of_rise/tests/test_backfill_reconciler.py`; update counts in `rate_of_rise/tests/test_discovery.py`

**Interfaces:**
- Consumes: `StoreClient`/`ProbeState` (Task 10), `field_spec`/`points_for`/`parse_map`/`STREAMS` (Task 11), `RecorderWriter`/`SchemaUnsupported` (Task 12), `recorder_db_path` (Task 13), `StatisticsWriter`/`HAWebsocket` (Task 14), `merge_stage_rows` (Task 15), `SourceCoordinator.rain_accumulator()` (Task 16), `GapFiller` (Task 18), `stagelog.stage_log_dir`.
- Produces: `Destinations(recorder_db, statistics, stage_dir, gaps)`; `PassResult(more, blocked, ok, counts)`; `Reconciler(client, dest, entity_map, shadow_map, cursor_path, now_fn=time.time, writer_factory=RecorderWriter)` with `cursor` and `run_pass(status: dict) -> PassResult`; `BackfillService(client, reconciler, publish, now_fn=time.time)` with `start()`, `stop()`, `tick() -> float`; `build_backfill(cfg, publish, dataset, sources, data_dir, share_dir) -> BackfillService | None`; MQTT topic `<base>/status/backfill`; entity `sensor.rate_of_rise_creek_backfill_status`.
- Constants: `HOLDBACK_S = 120.0`, `MAX_RECORDS_PER_PASS = 20000`, `PASS_INTERVAL_S = 600.0`, `NO_STORE_RETRY_S = 3600.0`, `MAX_PASSES_PER_TICK = 20`, `BAD_TOKEN_WARN_S = 3600.0`.

- [ ] **Step 1: Write the failing tests**

`rate_of_rise/tests/test_backfill_reconciler.py`:
```python
"""Reconciler passes and the backfill service: cursor rules, the v1/unreachable/off paths.

Run: python rate_of_rise/tests/test_backfill_reconciler.py
"""
import json
import logging
import sqlite3
import sys
import tempfile
import threading
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from app.backfill import build_backfill  # noqa: E402
from app.backfill import reconciler as rmod  # noqa: E402
from app.backfill.client import Probe, ProbeState  # noqa: E402
from app.backfill.reconciler import (  # noqa: E402
    NO_STORE_RETRY_S, PASS_INTERVAL_S, BackfillService, Destinations, Reconciler)
from app.backfill.recorder import SchemaUnsupported, WriteResult  # noqa: E402

NOW = 1_791_400_000.0


class FakeClient:
    def __init__(self, node=(), eco=(), state=ProbeState.OK):
        self.recs = {"node": list(node), "ecowitt": list(eco)}
        self.state = state
        self.fail = False

    def status(self):
        return {"store_schema": 1, "streams": {
            s: {"first": 1 if r else 0, "last": r[-1]["seq"] if r else 0}
            for s, r in self.recs.items()}}

    def probe(self):
        return Probe(self.state, self.status() if self.state is ProbeState.OK else {})

    def records(self, stream, after, max_records):
        if self.fail:
            raise requests.ConnectionError("gone")
        return [r for r in self.recs[stream] if r["seq"] > after][:max_records]


class FakeWriter:
    calls = []
    raise_on_write = None

    def __init__(self, db):
        pass

    def write(self, entity_id, kind, unit, points, dry_run=False):
        if FakeWriter.raise_on_write:
            raise FakeWriter.raise_on_write
        FakeWriter.calls.append((entity_id, kind, unit, [p.ts for p in points], dry_run))
        return WriteResult(entity_id, inserted=[p.ts for p in points])


class BlockedWriter:
    def __init__(self, db):
        raise SchemaUnsupported(54)


def node(seq, ts, ts_src="ntp", stage=1.0):
    return {"seq": seq, "ts": ts, "ts_src": ts_src, "stage_ft": stage, "v": 4100}


def make(client, writer=FakeWriter, gaps=None, entity_map=None, shadow=None, stats=None):
    d = Path(tempfile.mkdtemp())
    FakeWriter.calls, FakeWriter.raise_on_write = [], None
    dest = Destinations(recorder_db=d / "ha.db", statistics=stats, stage_dir=d / "stage",
                        gaps=gaps)
    rec = Reconciler(client, dest,
                     entity_map if entity_map is not None else
                     {"node": {"stage_ft": "sensor.v2_stage"}},
                     shadow if shadow is not None else {"node": {"battery_mv": "sensor.v1_batt"}},
                     d / "state" / "backfill.json", now_fn=lambda: NOW, writer_factory=writer)
    return rec, d


def test_writes_map_and_shadow_then_advances_cursor():
    client = FakeClient(node=[node(1, NOW - 600), node(2, NOW - 540)])
    rec, d = make(client)
    res = rec.run_pass(client.status())
    assert res.ok and rec.cursor["node"] == 2
    written = [c for c in FakeWriter.calls if not c[4]]
    shadowed = [c for c in FakeWriter.calls if c[4]]
    assert written == [("sensor.v2_stage", "number", "ft", [NOW - 600, NOW - 540], False)]
    assert shadowed[0][0] == "sensor.v1_batt" and shadowed[0][2] == "mV"
    assert res.counts["inserted_states"] == 2 and res.counts["shadow_states"] == 2
    assert res.counts["stage_log_rows"] == 2
    assert json.loads((d / "state" / "backfill.json").read_text())["node"] == 2


def test_holds_back_live_edge():
    client = FakeClient(node=[node(1, NOW - 600), node(2, NOW - 60), node(3, NOW - 30)])
    rec, _ = make(client)
    rec.run_pass(client.status())
    assert rec.cursor["node"] == 1


def test_records_without_time_are_skipped_but_consumed():
    client = FakeClient(node=[node(1, 12.0, ts_src="none"), node(2, NOW - 600)])
    rec, _ = make(client)
    res = rec.run_pass(client.status())
    assert res.counts["skipped_no_time"] == 1 and rec.cursor["node"] == 2
    assert FakeWriter.calls[0][3] == [NOW - 600]


def test_store_restart_resets_cursor():
    client = FakeClient(node=[node(1, NOW - 600)])
    rec, d = make(client)
    rec._cursor["node"] = 5000                       # from the old card
    h = []
    lg = logging.getLogger("app.backfill")
    handler = logging.Handler()
    handler.emit = h.append
    lg.addHandler(handler)
    try:
        rec.run_pass(client.status())
    finally:
        lg.removeHandler(handler)
    assert rec.cursor["node"] == 1
    assert any(r.levelno == logging.WARNING for r in h)


def test_failed_destination_keeps_cursor():
    client = FakeClient(node=[node(1, NOW - 600)])
    rec, _ = make(client)
    FakeWriter.raise_on_write = sqlite3.OperationalError("database is locked")
    res = rec.run_pass(client.status())
    assert not res.ok and rec.cursor.get("node", 0) == 0
    FakeWriter.raise_on_write = None
    assert rec.run_pass(client.status()).ok and rec.cursor["node"] == 1


def test_schema_block_still_runs_other_destinations():
    client = FakeClient(node=[node(1, NOW - 600)])
    rec, d = make(client, writer=BlockedWriter)
    res = rec.run_pass(client.status())
    assert res.ok and res.blocked == "blocked: recorder schema 54"
    assert res.counts["stage_log_rows"] == 1 and rec.cursor["node"] == 1


def test_gap_filler_gets_both_streams():
    seen = []
    gaps = SimpleNamespace(fill=lambda n, e: seen.append((len(n), len(e))) or 3)
    client = FakeClient(node=[node(1, NOW - 600)],
                        eco=[{"seq": 1, "ts": NOW - 590, "ts_src": "ntp", "rain_year_in": 1.0}])
    rec, _ = make(client, gaps=gaps)
    res = rec.run_pass(client.status())
    assert seen == [(1, 1)] and res.counts["dataset_rows"] == 3 and rec.cursor["ecowitt"] == 1


def test_more_when_a_pass_hits_the_cap():
    old = rmod.MAX_RECORDS_PER_PASS
    rmod.MAX_RECORDS_PER_PASS = 2
    try:
        client = FakeClient(node=[node(i, NOW - 1000 + i) for i in range(1, 6)])
        rec, _ = make(client)
        assert rec.run_pass(client.status()).more and rec.cursor["node"] == 2
    finally:
        rmod.MAX_RECORDS_PER_PASS = old


def test_statistics_failure_does_not_block_the_pass():
    class Boom:
        def backfill(self, entity, ts):
            raise RuntimeError("websocket down")
    client = FakeClient(node=[node(1, NOW - 600)])
    rec, _ = make(client, stats=Boom())
    res = rec.run_pass(client.status())
    assert res.ok and rec.cursor["node"] == 1 and res.counts["stat_errors"] == 1


# --- the service ------------------------------------------------------------------------
class Quiet(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.seen = []

    def emit(self, record):
        self.seen.append(record)


def service(state):
    client = FakeClient(node=[node(1, NOW - 600)], state=state)
    rec, _ = make(client)
    published = []
    svc = BackfillService(client, rec, lambda name, payload: published.append((name, payload)),
                          now_fn=lambda: NOW)
    return svc, client, published


def run_quietly(fn):
    h = Quiet()
    lg = logging.getLogger("app")
    old = lg.level
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)      # or INFO records would be filtered before the handler
    try:
        return fn(), h.seen
    finally:
        lg.removeHandler(h)
        lg.setLevel(old)


def test_service_v1_gateway_is_silent_and_waits_an_hour():
    svc, _, published = service(ProbeState.NO_STORE)
    delay, seen = run_quietly(svc.tick)
    assert delay == NO_STORE_RETRY_S and not seen
    assert published[-1] == ("status/backfill", published[-1][1])
    assert published[-1][1]["state"] == "v1 gateway (no store)"
    assert FakeWriter.calls == []


def test_service_unreachable_is_silent():
    svc, _, published = service(ProbeState.UNREACHABLE)
    delay, seen = run_quietly(svc.tick)
    assert delay == PASS_INTERVAL_S and not seen and published[-1][1]["state"] == "unreachable"


def test_service_bad_token_warns_once_per_hour():
    svc, _, published = service(ProbeState.BAD_TOKEN)
    _, seen1 = run_quietly(svc.tick)
    _, seen2 = run_quietly(svc.tick)
    assert len([r for r in seen1 if r.levelno == logging.WARNING]) == 1 and not seen2
    assert published[-1][1]["state"] == "error: bad token"


def test_service_ok_runs_a_pass_and_reports_idle():
    svc, _, published = service(ProbeState.OK)
    svc.tick()
    final = published[-1][1]
    assert final["state"] == "idle" and final["cursor_node"] == 1
    assert final["inserted_states"] == 1


def test_service_network_drop_mid_pass_is_unreachable():
    svc, client, published = service(ProbeState.OK)
    client.fail = True
    _, seen = run_quietly(svc.tick)
    assert published[-1][1]["state"] == "unreachable"
    assert not [r for r in seen if r.levelno >= logging.WARNING]


# --- activation ---------------------------------------------------------------------------
def cfg(**kw):
    base = dict(gateway_store_url="", gateway_store_token="", backfill_entity_map="",
                backfill_shadow_map="", ha_ws_url="ws://x", supervisor_token="",
                soil_moisture_entities=[], fast_loop_minutes=5,
                rate_of_rise_window_minutes=10.0, rate_of_rise_max_gap_minutes=10.0)
    base.update(kw)
    return SimpleNamespace(**base)


def test_blank_url_is_off_no_thread_no_requests():
    published = []
    before = threading.active_count()
    created = []
    import app.backfill as pkg
    real = pkg.StoreClient
    pkg.StoreClient = lambda *a, **k: created.append(1)
    try:
        svc = build_backfill(cfg(), lambda n, p: published.append((n, p)), None, None,
                             Path(tempfile.mkdtemp()), None)
    finally:
        pkg.StoreClient = real
    assert svc is None and created == [] and threading.active_count() == before
    assert published == [("status/backfill", {"state": "off"})]


def test_bad_entity_map_disables_with_a_reason():
    published = []
    svc = build_backfill(cfg(gateway_store_url="http://x", backfill_entity_map="{nope"),
                         lambda n, p: published.append((n, p)), None, None,
                         Path(tempfile.mkdtemp()), None)
    assert svc is None and published[-1][1]["state"].startswith("error: entity map")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python rate_of_rise/tests/test_backfill_reconciler.py`
Expected: `ImportError: cannot import name 'build_backfill'`.

- [ ] **Step 3: Implement `reconciler.py`**

```python
"""One backfill pass, and the thread that keeps running them.

A pass: for each stream, pull the records after the cursor, hold back the live edge (the last
HOLDBACK_S, which HA has probably got live anyway), drop records with no trustworthy time,
and write the batch to every destination: HA's recorder (and statistics), the stage log, and
the dataset. The cursor only moves when every destination succeeded. Every destination is
idempotent, so a failed pass is simply run again next time.

The service probes first and treats every non-store answer as normal, quiet operation:
`v1 gateway (no store)` re-probes hourly, `unreachable` every pass interval. Only a 401 is
worth a WARNING (once an hour), because it is a configuration mistake that will not fix
itself.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from .client import ProbeState
from .entity_map import STREAMS, field_spec, points_for
from .recorder import RecorderWriter, SchemaUnsupported
from .stagelog_merge import merge_stage_rows

log = logging.getLogger("app.backfill")

HOLDBACK_S = 120.0
MAX_RECORDS_PER_PASS = 20000
PASS_INTERVAL_S = 600.0
NO_STORE_RETRY_S = 3600.0
MAX_PASSES_PER_TICK = 20
BAD_TOKEN_WARN_S = 3600.0


@dataclass
class Destinations:
    recorder_db: Path | None        # None: HA's config is not mapped (local runs)
    statistics: object | None       # .backfill(entity_id, inserted_ts) -> hours
    stage_dir: Path | None
    gaps: object | None             # .fill(node_records, eco_records) -> rows


@dataclass
class PassResult:
    more: bool = False
    blocked: str | None = None
    ok: bool = True
    counts: Counter = field(default_factory=Counter)


class Reconciler:
    def __init__(self, client, dest: Destinations, entity_map: dict, shadow_map: dict,
                 cursor_path: Path, now_fn=time.time, writer_factory=RecorderWriter):
        self._client = client
        self._dest = dest
        self._map = entity_map
        self._shadow = shadow_map
        self._path = cursor_path
        self._now = now_fn
        self._writer_factory = writer_factory
        self._cursor = self._load()
        self._skips_noted: set[tuple[str, str]] = set()

    @property
    def cursor(self) -> dict[str, int]:
        return dict(self._cursor)

    def _load(self) -> dict[str, int]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return {s: int(data.get(s, 0)) for s in STREAMS}
        except (FileNotFoundError, ValueError, OSError, TypeError):
            return {s: 0 for s in STREAMS}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._cursor), encoding="utf-8")
        tmp.replace(self._path)

    def run_pass(self, status: dict) -> PassResult:
        result = PassResult()
        batches: dict[str, list[dict]] = {}
        consumed: dict[str, int] = {}
        streams = status.get("streams") or {}
        for s in STREAMS:
            last = int((streams.get(s) or {}).get("last", 0) or 0)
            cur = self._cursor.get(s, 0)
            if last < cur:
                log.warning("gateway store %s stream is at seq %d, below the cursor %d (card "
                            "replaced or reformatted?); re-reading it from the start. Writes "
                            "are idempotent, so nothing is duplicated.", s, last, cur)
                cur = self._cursor[s] = 0
            if last <= cur:
                batches[s], consumed[s] = [], cur
                continue
            recs = self._client.records(s, cur, MAX_RECORDS_PER_PASS)
            if len(recs) >= MAX_RECORDS_PER_PASS:
                result.more = True
            edge = self._now() - HOLDBACK_S
            usable, upto = [], cur
            for r in recs:
                if float(r.get("ts", 0)) > edge:
                    break
                upto = r["seq"]
                if r.get("ts_src") == "none":
                    result.counts["skipped_no_time"] += 1
                    continue
                usable.append(r)
            batches[s], consumed[s] = usable, upto

        try:
            self._write_recorder(batches, result)
        except Exception:
            log.exception("backfill: recorder write failed; the batch is retried next pass")
            result.ok = False
        if result.ok and self._dest.stage_dir is not None and batches.get("node"):
            try:
                result.counts["stage_log_rows"] += merge_stage_rows(
                    self._dest.stage_dir, [(r["ts"], r.get("stage_ft")) for r in batches["node"]],
                    self._now())
            except Exception:
                log.exception("backfill: stage log merge failed; retried next pass")
                result.ok = False
        if result.ok and self._dest.gaps is not None and (batches.get("node") or batches.get("ecowitt")):
            try:
                result.counts["dataset_rows"] += self._dest.gaps.fill(
                    batches.get("node", []), batches.get("ecowitt", []))
            except Exception:
                log.exception("backfill: dataset gap rows failed; retried next pass")
                result.ok = False

        if not result.ok:
            result.more = False
            return result
        if any(consumed[s] != self._cursor.get(s, 0) for s in STREAMS) or not self._path.exists():
            self._cursor.update(consumed)
            self._save()
        return result

    def _write_recorder(self, batches: dict, result: PassResult) -> None:
        if self._dest.recorder_db is None or not (self._map or self._shadow):
            return
        if not any(batches.values()):
            return
        try:
            writer = self._writer_factory(self._dest.recorder_db)
        except SchemaUnsupported as exc:
            result.blocked = f"blocked: recorder schema {exc.version}"
            return
        for entities, dry in ((self._map, False), (self._shadow, True)):
            for stream, fields_ in entities.items():
                recs = batches.get(stream) or []
                if not recs:
                    continue
                for fld, entity in fields_.items():
                    spec = field_spec(stream, fld)
                    res = writer.write(entity, spec.kind, spec.unit,
                                       points_for(stream, fld, recs), dry_run=dry)
                    if res.skipped:
                        self._note_skip(entity, res.skipped)
                        continue
                    if dry:
                        result.counts["shadow_states"] += len(res.inserted)
                        if res.inserted or res.deleted_unavailable:
                            log.info("backfill shadow: would insert %d row(s) into %s and "
                                     "remove %d unavailable row(s)", len(res.inserted), entity,
                                     res.deleted_unavailable)
                        continue
                    result.counts["inserted_states"] += len(res.inserted)
                    result.counts["deleted_unavailable"] += res.deleted_unavailable
                    if self._dest.statistics is not None and res.inserted:
                        try:
                            result.counts["imported_stat_hours"] += \
                                self._dest.statistics.backfill(entity, res.inserted)
                        except Exception as exc:
                            # Statistics are the coarse copy; the states are already in. Losing
                            # an hour's re-import is not worth re-running the whole batch.
                            result.counts["stat_errors"] += 1
                            log.warning("backfill: statistics import for %s failed: %s",
                                        entity, exc)

    def _note_skip(self, entity: str, reason: str) -> None:
        if (entity, reason) not in self._skips_noted:
            self._skips_noted.add((entity, reason))
            log.warning("backfill: skipping %s: %s", entity, reason)


class BackfillService:
    def __init__(self, client, reconciler: Reconciler, publish, now_fn=time.time):
        self._client = client
        self._rec = reconciler
        self._publish = publish
        self._now = now_fn
        self._stop = threading.Event()
        self._last_bad_token_warn: float | None = None

    def start(self) -> None:
        threading.Thread(target=self._run, name="backfill", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        delay = 0.0
        while not self._stop.wait(delay):
            try:
                delay = self.tick()
            except Exception:
                log.exception("backfill tick failed")
                self._status("error: tick failed (see log)")
                delay = PASS_INTERVAL_S

    def tick(self) -> float:
        probe = self._client.probe()
        if probe.state is ProbeState.NO_STORE:
            self._status("v1 gateway (no store)")
            return NO_STORE_RETRY_S
        if probe.state is ProbeState.UNREACHABLE:
            self._status("unreachable")
            return PASS_INTERVAL_S
        if probe.state is ProbeState.BAD_TOKEN:
            now = self._now()
            if self._last_bad_token_warn is None or now - self._last_bad_token_warn >= BAD_TOKEN_WARN_S:
                log.warning("gateway store rejected gateway_store_token (HTTP 401); backfill is "
                            "paused until it matches the gateway's creek_store_token")
                self._last_bad_token_warn = now
            self._status("error: bad token")
            return PASS_INTERVAL_S

        streams = probe.status.get("streams") or {}
        pending = sum(max(0, int((streams.get(s) or {}).get("last", 0) or 0)
                          - self._rec.cursor.get(s, 0)) for s in STREAMS)
        if pending:
            self._status(f"backfilling {pending}")
        totals: Counter = Counter()
        state = "idle"
        try:
            for _ in range(MAX_PASSES_PER_TICK):
                res = self._rec.run_pass(probe.status)
                totals.update(res.counts)
                if res.blocked:
                    state = res.blocked
                if not res.ok:
                    state = "error: a destination failed (see log)"
                    break
                if not res.more:
                    break
        except requests.RequestException as exc:
            log.debug("gateway store dropped mid-pass: %s", exc)
            state = "unreachable"
        self._status(state, totals)
        return PASS_INTERVAL_S

    def _status(self, state: str, counts: Counter | None = None) -> None:
        payload = {"state": state,
                   "last_run": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   **{f"cursor_{s}": self._rec.cursor.get(s, 0) for s in STREAMS},
                   **{k: int(v) for k, v in (counts or {}).items()}}
        try:
            self._publish("status/backfill", payload)
        except Exception:
            log.debug("could not publish backfill status", exc_info=True)
```

- [ ] **Step 4: Implement `build_backfill` in `app/backfill/__init__.py`**

```python
"""Backfill from the v2 gateway's SD store into HA's recorder, the stage log and the dataset.

Off unless `gateway_store_url` is set: then nothing is constructed, no thread runs, and no
request is made. See docs/superpowers/specs/2026-10-07-gateway-v2-sd-backfill-design.md.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from ..stagelog import stage_log_dir
from .client import StoreClient
from .entity_map import parse_map
from .gaprows import GapFiller
from .reconciler import BackfillService, Destinations, Reconciler
from .statistics import HAWebsocket, StatisticsWriter

log = logging.getLogger("app.backfill")


def recorder_db_path() -> Path:
    """HA's recorder database as the add-on sees it (config.yaml maps homeassistant_config).
    RECORDER_DB overrides it for tests and local runs."""
    return Path(os.environ.get("RECORDER_DB", "/homeassistant/home-assistant_v2.db"))


def soil_channels(soil_entities: list[str], *maps: dict) -> dict[str, str]:
    """Which Ecowitt channel feeds which soil feature, worked out from the entity maps: the
    channel whose mapped entity is soil_moisture_entities[0] is near_house, [1] near_creek."""
    out: dict[str, str] = {}
    for m in maps:
        for fld, entity in (m.get("ecowitt") or {}).items():
            if not fld.startswith("soil_ch"):
                continue
            ch = fld[len("soil_ch"):]
            if len(soil_entities) >= 1 and entity == soil_entities[0]:
                out["near_house"] = ch
            if len(soil_entities) >= 2 and entity == soil_entities[1]:
                out["near_creek"] = ch
    return out


def build_backfill(cfg, publish, dataset, sources, data_dir: Path, share_dir: Path | None):
    if not cfg.gateway_store_url:
        publish("status/backfill", {"state": "off"})
        return None
    try:
        entity_map = parse_map(cfg.backfill_entity_map)
        shadow = parse_map(cfg.backfill_shadow_map)
    except ValueError as exc:
        log.error("gateway store backfill disabled: %s", exc)
        publish("status/backfill", {"state": f"error: {exc}"})
        return None
    client = StoreClient(cfg.gateway_store_url, cfg.gateway_store_token)
    db = recorder_db_path()
    stats = StatisticsWriter(db, HAWebsocket(cfg.ha_ws_url, cfg.supervisor_token).call)
    rain = sources.rain_accumulator() if sources is not None else None
    gaps = GapFiller(cfg, dataset, sources,
                     soil_channels(cfg.soil_moisture_entities, entity_map, shadow), rain)
    dest = Destinations(recorder_db=db if db.exists() else None, statistics=stats,
                        stage_dir=stage_log_dir(data_dir, share_dir), gaps=gaps)
    if dest.recorder_db is None:
        log.info("gateway store backfill: %s not found, so no recorder writes", db)
    rec = Reconciler(client, dest, entity_map, shadow, data_dir / "state" / "backfill.json")
    svc = BackfillService(client, rec, publish)
    svc.start()
    log.info("Gateway store backfill enabled for %s", cfg.gateway_store_url)
    return svc
```

The test `test_bad_entity_map_disables_with_a_reason` expects the state to start with `error: entity map`. `parse_map` raises `"entity map is not valid JSON: ..."`, so the published state is `"error: entity map is not valid JSON: ..."` ✓.

- [ ] **Step 5: Run the reconciler tests**

Run: `python rate_of_rise/tests/test_backfill_reconciler.py`
Expected: `16 passed`.

- [ ] **Step 6: Wire into `__main__.py` and discovery**

`__main__.py`: import `from .backfill import build_backfill, recorder_db_path` (replacing Task 13's narrower import). After the `stage_log = StageLogger(...)` and its `log.info(...)`:
```python
    # Gateway v2 store backfill (app/backfill/). Returns None, and starts nothing, when
    # gateway_store_url is blank, which is the default and the v1 configuration.
    backfill = build_backfill(cfg, mqtt.publish, dataset, sources, data_dir, SHARE_DIR)
```
In the `finally:` block before `mqtt.disconnect()`:
```python
        if backfill is not None:
            backfill.stop()
```

`discovery.py`: in `_specs()`, immediately after the `creek_pipeline_state` entry, add:
```python
            # Gateway v2 store backfill (app/backfill/): off / v1 gateway (no store) /
            # unreachable / idle / backfilling N / blocked: ... / error: .... Attributes carry
            # the cursors and the last pass's counts.
            ("sensor", "creek_backfill_status", {
                "name": "Creek Backfill Status",
                "state_topic": f"{b}/status/backfill",
                "value_template": "{{ value_json.state }}",
                "json_attributes_topic": f"{b}/status/backfill",
                "entity_category": "diagnostic",
                "icon": "mdi:database-sync"}),
```

`tests/test_discovery.py`: change `assert len(sensors) == 65` to `66` and `assert len(published) == 91` to `92`.

- [ ] **Step 7: Run the whole add-on suite**

```bash
fail=0; for t in rate_of_rise/tests/test_*.py; do python "$t" > /dev/null || { echo "FAIL $t"; fail=1; }; done; echo "fail=$fail"
```
Expected: `fail=0`. A `terminate called without an active exception` abort right after `test_dataset.py` reports all-pass is the known flaky teardown (memory: CI flaky dataset abort). Rerun that file alone to confirm.

- [ ] **Step 8: Commit**

```bash
git add rate_of_rise/app/backfill/reconciler.py rate_of_rise/app/backfill/__init__.py rate_of_rise/app/__main__.py rate_of_rise/app/discovery.py rate_of_rise/tests/test_backfill_reconciler.py rate_of_rise/tests/test_discovery.py
git commit -m "backfill: reconciler passes, background service, status sensor, add-on wiring

Off unless gateway_store_url is set; a v1 gateway, an unreachable one, or none at all
leave the add-on exactly as before.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 20: Cutover script

**Files:**
- Create: `tools/gateway_cutover.py`
- Test: `tools/test_gateway_cutover.py`

**Interfaces:**
- Consumes: HA websocket API (`config/device_registry/list`, `config/entity_registry/list`, `get_states`, `config/entity_registry/update`, `supervisor/api`) and REST `DELETE /api/config/config_entries/entry/<id>`.
- Produces: `pair_entities(v1: list[dict], v2: list[dict]) -> tuple[list[tuple[str, str]], list[str], list[str]]` (pairs, unpaired v1, unpaired v2); `production_map(entity_map: dict, pairs) -> dict`; CLI `python tools/gateway_cutover.py --ha-url URL --token-file PATH [--v1-device NAME] [--v2-device NAME] [--addon-slug SLUG] [--apply]`. Without `--apply` it only prints the plan.

- [ ] **Step 0: Confirm how HA treats an entity renamed onto an ID with existing history**

Read HA's recorder code for the rename path. In `homeassistant/components/recorder/table_managers/states_meta.py`, look for `update_metadata`; in `homeassistant/components/recorder/core.py` or `entity_registry.py`, look for the `async_update_entity_id` listener (`https://github.com/home-assistant/core/tree/dev/homeassistant/components/recorder`, via WebFetch). Expected: when the new entity_id already exists in `states_meta`, HA logs that it cannot migrate the old history and leaves both rows alone. The renamed entity then shows the **existing** history under that ID (v1's) and continues it. If HA instead merges or deletes, stop and report to the user before writing the tool: the cutover design depends on it.

- [ ] **Step 1: Write the failing test**

`tools/test_gateway_cutover.py`:
```python
"""Pure parts of the cutover: pairing v1/v2 entities and rewriting the entity map.

Run: python tools/test_gateway_cutover.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gateway_cutover import pair_entities, production_map  # noqa: E402


def ent(entity_id, name):
    return {"entity_id": entity_id, "original_name": name}


def test_pairs_by_domain_and_original_name():
    v1 = [ent("sensor.creek_gateway_stage", "Stage"),
          ent("sensor.outside_creek_gateway_sensor_distance", "Sensor Distance"),
          ent("binary_sensor.creek_gateway_creek_node_status", "Creek Node Status"),
          ent("sensor.creek_gateway_uptime", "Uptime")]
    v2 = [ent("sensor.creek_gateway_v2_stage", "Stage"),
          ent("sensor.creek_gateway_v2_sensor_distance", "Sensor Distance"),
          ent("binary_sensor.creek_gateway_v2_creek_node_status", "Creek Node Status"),
          ent("sensor.creek_gateway_v2_uptime", "Uptime"),
          ent("sensor.creek_gateway_v2_store_free_space", "Store Free Space")]
    pairs, only_v1, only_v2 = pair_entities(v1, v2)
    assert ("sensor.creek_gateway_stage", "sensor.creek_gateway_v2_stage") in pairs
    assert ("sensor.outside_creek_gateway_sensor_distance",
            "sensor.creek_gateway_v2_sensor_distance") in pairs
    assert len(pairs) == 4 and only_v1 == []
    assert only_v2 == ["sensor.creek_gateway_v2_store_free_space"]


def test_production_map_swaps_v2_ids_for_v1_ids():
    m = {"node": {"stage_ft": "sensor.creek_gateway_v2_stage"},
         "ecowitt": {"rain_total_in": "sensor.outside_weather_station_rain_total"}}
    out = production_map(m, [("sensor.creek_gateway_stage", "sensor.creek_gateway_v2_stage")])
    assert out == {"node": {"stage_ft": "sensor.creek_gateway_stage"},
                   "ecowitt": {"rain_total_in": "sensor.outside_weather_station_rain_total"}}


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python tools/test_gateway_cutover.py`
Expected: `ModuleNotFoundError: No module named 'gateway_cutover'`.

- [ ] **Step 3: Implement `tools/gateway_cutover.py`**

```python
"""Swap gateway v2 in for v1 in Home Assistant, keeping v1's entity IDs and history.

Run from a laptop with an admin long-lived access token in a file:

    python tools/gateway_cutover.py --ha-url http://192.168.20.3:8123 --token-file ~/.ha_token
    python tools/gateway_cutover.py ... --apply

Without --apply it prints what it would do and stops. With --apply:
  1. preflight: v1's entities are all unavailable (v1 unplugged), v2's backfill is idle,
     and every v1 entity has a v2 entity with the same domain and name;
  2. removes v1's ESPHome config entry (and with it v1's entities, freeing their IDs);
  3. renames each v2 entity to its v1 entity_id. HA keeps history by entity_id string, so
     each renamed entity continues v1's history (see the plan's Task 20 Step 0);
  4. rewrites the add-on's backfill_entity_map to the production IDs and clears
     backfill_shadow_map.
Flashing the production wrapper (OTA buttons back) is the last, manual, step.
See docs/gateway-v2-trial.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests
import websocket


def pair_entities(v1: list[dict], v2: list[dict]):
    def key(e):
        return e["entity_id"].split(".", 1)[0], (e.get("original_name") or "").strip().lower()
    v2_by_key = {key(e): e["entity_id"] for e in v2}
    pairs, only_v1 = [], []
    for e in v1:
        match = v2_by_key.pop(key(e), None)
        if match:
            pairs.append((e["entity_id"], match))
        else:
            only_v1.append(e["entity_id"])
    return pairs, only_v1, sorted(v2_by_key.values())


def production_map(entity_map: dict, pairs) -> dict:
    v1_for = {v2: v1 for v1, v2 in pairs}
    return {stream: {f: v1_for.get(e, e) for f, e in fields.items()}
            for stream, fields in entity_map.items()}


class HA:
    def __init__(self, url: str, token: str):
        self.url, self.token, self._id = url.rstrip("/"), token, 0
        ws_url = self.url.replace("http", "ws", 1) + "/api/websocket"
        self.ws = websocket.create_connection(ws_url, timeout=30)
        json.loads(self.ws.recv())
        self.ws.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(self.ws.recv()).get("type") != "auth_ok":
            raise SystemExit("HA rejected the token")

    def call(self, msg: dict):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, **msg}))
        while True:
            reply = json.loads(self.ws.recv())
            if reply.get("id") == self._id:
                break
        if not reply.get("success"):
            raise SystemExit(f"{msg['type']} failed: {reply.get('error')}")
        return reply.get("result")

    def delete_entry(self, entry_id: str) -> None:
        r = requests.delete(f"{self.url}/api/config/config_entries/entry/{entry_id}",
                            headers={"Authorization": f"Bearer {self.token}"}, timeout=30)
        r.raise_for_status()


def device_entities(devices, entities, name):
    dev = next((d for d in devices if name in (d.get("name_by_user"), d.get("name"))), None)
    if dev is None:
        raise SystemExit(f"no device named {name!r}")
    return dev, [e for e in entities if e.get("device_id") == dev["id"]]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ha-url", required=True)
    ap.add_argument("--token-file", required=True, type=Path)
    ap.add_argument("--v1-device", default="Creek Gateway")
    ap.add_argument("--v2-device", default="Creek Gateway v2")
    ap.add_argument("--addon-slug", default=None, help="default: the add-on named Rate of Rise")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    ha = HA(args.ha_url, args.token_file.expanduser().read_text().strip())
    devices = ha.call({"type": "config/device_registry/list"})
    entities = ha.call({"type": "config/entity_registry/list"})
    v1_dev, v1 = device_entities(devices, entities, args.v1_device)
    _, v2 = device_entities(devices, entities, args.v2_device)
    pairs, only_v1, only_v2 = pair_entities(v1, v2)

    print(f"{len(pairs)} entity pair(s):")
    for a, b in pairs:
        print(f"  {b}  ->  {a}")
    if only_v2:
        print("v2-only (kept as they are):", ", ".join(only_v2))
    problems = []
    if only_v1:
        problems.append("v1 entities with no v2 counterpart: " + ", ".join(only_v1))
    states = {s["entity_id"]: s["state"] for s in ha.call({"type": "get_states"})}
    live_v1 = [e["entity_id"] for e in v1 if states.get(e["entity_id"]) not in (None, "unavailable")]
    if live_v1:
        problems.append("v1 is still online (unplug it first): " + ", ".join(live_v1[:5]))
    backfill = states.get("sensor.rate_of_rise_creek_backfill_status")
    if backfill not in ("idle",):
        problems.append(f"backfill status is {backfill!r}, not 'idle': let it catch up first")

    addons = ha.call({"type": "supervisor/api", "endpoint": "/addons", "method": "get"})["addons"]
    slug = args.addon_slug or next((a["slug"] for a in addons if a["name"] == "Rate of Rise"), None)
    if slug is None:
        problems.append("could not find the Rate of Rise add-on; pass --addon-slug")
    for p in problems:
        print("PREFLIGHT:", p)
    if problems:
        return 1
    if not args.apply:
        print("Preflight passed. Re-run with --apply to make these changes.")
        return 0

    for entry_id in v1_dev.get("config_entries", []):
        ha.delete_entry(entry_id)
        print("removed v1 config entry", entry_id)
    for v1_id, v2_id in pairs:
        ha.call({"type": "config/entity_registry/update", "entity_id": v2_id,
                 "new_entity_id": v1_id})
        print("renamed", v2_id, "->", v1_id)
    info = ha.call({"type": "supervisor/api", "endpoint": f"/addons/{slug}/info", "method": "get"})
    options = dict(info["options"])
    options["backfill_entity_map"] = json.dumps(
        production_map(json.loads(options.get("backfill_entity_map") or "{}"), pairs)
        | {"ecowitt": json.loads(options.get("backfill_shadow_map") or "{}").get("ecowitt", {})})
    options["backfill_shadow_map"] = ""
    ha.call({"type": "supervisor/api", "endpoint": f"/addons/{slug}/options", "method": "post",
             "data": {"options": options}})
    print("add-on options updated; restart the add-on, then install creek-gateway-v2.prod.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

The production map is the trial map with v2 IDs swapped for v1 IDs, plus the Ecowitt entities that were only in the shadow map during the trial.

- [ ] **Step 4: Run tests**

Run: `python tools/test_gateway_cutover.py`
Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add tools/gateway_cutover.py tools/test_gateway_cutover.py
git commit -m "tools: scripted v1 -> v2 cutover with preflight and dry-run default

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Task 21: Docs, version, trial runbook, final verification

**Files:**
- Create: `docs/gateway-v2-trial.md`
- Modify: `rate_of_rise/config.yaml` (`version: "0.26.0"`), `rate_of_rise/CHANGELOG.md`, `rate_of_rise/DOCS.md`
- Modify: `.github/workflows/tests.yml` (run `tools/test_gateway_cutover.py`)

- [ ] **Step 1: Write `docs/gateway-v2-trial.md`**

````markdown
# Gateway v2 trial and cutover

Runbook for running gateway v2 beside v1, proving it, and swapping it in. Design:
[spec](superpowers/specs/2026-10-07-gateway-v2-sd-backfill-design.md). Hardware and build:
[firmware/README.md](../firmware/README.md#gateway-v2-feather-esp32-s3--sd-store).

## 1. Set up the trial

1. Flash v2 with the **trial** wrapper (`creek-gateway-v2.yaml`, no node-OTA buttons). Adopt
   it in HA's ESPHome integration as `Creek Gateway v2`.
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

## 2. Acceptance checks (all required before cutover)

| # | Check | Pass when |
|---|---|---|
| 1 | 24 h side by side | v2's Creek Node Packets, Stage and RSSI track v1's; Store Clock Source is `ntp`; Store Node Records keeps rising. |
| 2 | WiFi outage | Block 192.168.30.21 at OPNsense for 30 min, then unblock. Within 10 min the status returns to `idle` with `inserted_states` > 0, and v2's Stage history shows no gap and no `unavailable` inside the outage. |
| 3 | HA outage | Stop HA Core for 30 min (Settings → System → Restart → Stop), then start it. v2's history is filled; the add-on log shows `backfill shadow: would insert ...` lines for v1 and Ecowitt entities; `/share/rate_of_rise/stage/` has rows across the outage; the add-on log shows `backfill: wrote N dataset row(s)`; `rain_24h_in` in the next feature row matches the GW3000B's own 24 h total. |
| 4 | Reboot while offline | With v2 blocked at OPNsense, power-cycle it. After it is unblocked: records continue the same seq, and the outage's records carry `ts_src` `rtc`. |
| 5 | v1 safety | Temporarily set `gateway_store_url` to v1's IP (192.168.30.20). Status shows `v1 gateway (no store)` or `unreachable`, nothing in the add-on log above DEBUG, and nothing written. Set it back. |
| 6 | OTA from v1 while v2 runs | Push node firmware from v1. v2 logs the telemetry before and after; neither gateway errors. |

## 3. Cutover

1. Unplug v1.
2. `python tools/gateway_cutover.py --ha-url http://192.168.20.3:8123 --token-file <file>`
   prints the plan and runs the preflight. Fix anything it reports.
3. The same command with `--apply`.
4. Restart the add-on. Install `creek-gateway-v2.prod.yaml` in the Device Builder (copy it to
   `/config/esphome/` first) so the node-OTA buttons come back.

**Rollback:** plug v1 back in. Its firmware and YAML were never changed. Undo backfilled
recorder rows with an MQTT publish to `creek/cmd/backfill_undo` (payload: an ISO time to
undo from, or empty for all).
````

- [ ] **Step 2: CHANGELOG, version and DOCS**

`config.yaml`: `version: "0.26.0"`.

`CHANGELOG.md`, new top section:
```markdown
## 0.26.0

- **New: backfill from gateway v2's SD store.** When `gateway_store_url` points at the new
  Feather ESP32-S3 gateway, the add-on reads back every node packet and Ecowitt reading the
  gateway logged while Home Assistant or WiFi was down, and fills the gap: in HA's own
  entity history at the right times (and its hourly statistics), in the stage log, and as
  dataset rows flagged `backfilled`. On-site rain totals and the API index are corrected for
  the gap too. USGS, WU, radar cells, NWS alerts and SNODAS are re-fetched for the gap;
  forecast features stay empty.
- **Off by default, and harmless against the current gateway.** With `gateway_store_url`
  blank nothing runs. Pointed at the v1 gateway, or at a gateway that is down, it reports
  that on `sensor.rate_of_rise_creek_backfill_status` and logs nothing above DEBUG.
- Recorder writes only happen on a recorder schema the add-on has been checked against
  (53). Every inserted row is marked, and `creek/cmd/backfill_undo` removes them.
  `backfill_shadow_map` rehearses writes in the log without making them.
- The add-on now maps `homeassistant_config` (read-write) for those writes, and depends on
  `websocket-client` for the statistics import.
```

`DOCS.md`: add a `## Gateway store backfill` section with the four options, the status values (`off`, `v1 gateway (no store)`, `unreachable`, `idle`, `backfilling N`, `blocked: recorder schema N`, `error: …`), the undo command, and a link to `docs/gateway-v2-trial.md`. Keep it to what an operator needs; the design lives in the spec.

`.github/workflows/tests.yml`, in the `test` job's "Run test suite" step, after the loop:
```bash
          python tools/test_gateway_cutover.py || { echo "::error file=tools/test_gateway_cutover.py::failed"; fail=1; }
```
(`websocket-client`, which the tool imports, is already installed from `requirements.txt`.)

- [ ] **Step 3: Full verification**

```bash
fail=0; for t in rate_of_rise/tests/test_*.py tools/test_gateway_cutover.py; do python "$t" > /dev/null || { echo "FAIL $t"; fail=1; }; done; echo "fail=$fail"
g++ -std=c++17 -Wall -Wextra -Werror -I firmware/esp32s3_feather_gateway/components/creek_store firmware/esp32s3_feather_gateway/tests/test_store_core.cpp -o /tmp/t && /tmp/t
esphome compile firmware/esp32_rfm69_gateway/gateway.yaml && esphome compile firmware/esp32s3_feather_gateway/gateway.yaml
```
Expected: `fail=0`, `all store_core tests passed`, two successful compiles.

Then run the add-on suite under pandas 3 (CI has pandas 3, local has 2.2 — copy-on-write makes `to_numpy()` read-only):
```bash
python -m venv /tmp/pd3 && /tmp/pd3/Scripts/pip install -q -r rate_of_rise/requirements.txt "pandas>=3" pyyaml jinja2
fail=0; for t in rate_of_rise/tests/test_*.py; do /tmp/pd3/Scripts/python "$t" > /dev/null || { echo "FAIL $t"; fail=1; }; done; echo "fail=$fail"
```
Expected: `fail=0`. (`/tmp/pd3/bin/` instead of `Scripts/` on Linux.)

- [ ] **Step 4: Commit**

```bash
git add docs/gateway-v2-trial.md rate_of_rise/config.yaml rate_of_rise/CHANGELOG.md rate_of_rise/DOCS.md .github/workflows/tests.yml
git commit -m "add-on 0.26.0: gateway store backfill; trial and cutover runbook

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Hand off**

Use superpowers:finishing-a-development-branch. The trial (Section 2 of the runbook) runs on real hardware over days and is the user's to schedule. The branch can merge before the trial, because with `gateway_store_url` blank the add-on is unchanged and v1's firmware is byte-identical.
