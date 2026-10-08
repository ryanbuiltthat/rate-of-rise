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

// A record body is the record without its `{"seq":N,` head. The sequence number is stamped
// when the record is written to the card, not when it is encoded, so seq on the card is
// gap-free and monotonic even if the card mounts late.
inline std::string body_head(double ts, const char *ts_src) {
  char buf[96];
  std::snprintf(buf, sizeof buf, "\"ts\":%.1f,\"ts_src\":\"%s\"", ts, ts_src);
  return buf;
}

inline std::string with_seq(uint32_t seq, const std::string &body) {
  char buf[24];
  std::snprintf(buf, sizeof buf, "{\"seq\":%u,", (unsigned) seq);
  return buf + body;
}

// --- Node records ------------------------------------------------------------------------
struct NodeFields {
  std::optional<float> distance_mm;  // nullopt: null on the wire (failed radar read)
  std::optional<float> battery_mv;
  std::optional<int> fast, diag, reset_cause, cycle, init_failures;
};

inline std::string encode_node_body(double ts, const char *ts_src, int rssi,
                                    const NodeFields &f, float mount_mm, const StageResult &s) {
  std::string out = body_head(ts, ts_src);
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
  append_num(out, "depth_in", s.depth_in, 4);
  out += "}\n";
  return out;
}

inline std::string encode_node_record(uint32_t seq, double ts, const char *ts_src, int rssi,
                                      const NodeFields &f, float mount_mm, const StageResult &s) {
  return with_seq(seq, encode_node_body(ts, ts_src, rssi, f, mount_mm, s));
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

// A 200 from the console that set none of the recorded fields (an empty or reshaped
// livedata document) is a failed poll, not a record of nothing.
inline bool ecowitt_has_data(const EcowittReading &e) {
  return e.rain_event_in || e.rain_rate_in_hr || e.rain_day_in || e.rain_24h_in ||
         e.rain_year_in || e.temp_f || !e.soil.empty();
}

inline std::string encode_ecowitt_body(double ts, const char *ts_src, const EcowittReading &e) {
  std::string out = body_head(ts, ts_src);
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

inline std::string encode_ecowitt_record(uint32_t seq, double ts, const char *ts_src,
                                         const EcowittReading &e) {
  return with_seq(seq, encode_ecowitt_body(ts, ts_src, e));
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

// Calendar fields decoded from the RTC are in range, and the year is one it was set in (the
// PCF8523 powers up at 2000). A garbled I2C read must not become the system clock.
inline bool civil_valid(const Civil &c) {
  return c.year >= 2025 && c.year <= 2099 && c.month >= 1 && c.month <= 12 && c.day >= 1 &&
         c.day <= 31 && c.hour < 24 && c.minute < 60 && c.second < 60;
}

inline uint8_t bcd2bin(uint8_t v) { return (uint8_t) ((v >> 4) * 10 + (v & 0x0F)); }
inline uint8_t bin2bcd(uint8_t v) { return (uint8_t) (((v / 10) << 4) | (v % 10)); }

}  // namespace creek_core
