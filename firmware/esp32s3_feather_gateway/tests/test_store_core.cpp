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
        "\"stage_ft\":0.9613,\"depth_in\":11.5354}\n");

  NodeFields bare;  // failed radar read, long-form packet with no diagnostics
  bare.battery_mv = 4012;
  const auto none = stage_from_distance(1105, bare.distance_mm, 150, 1000);
  CHECK(with_seq(7, encode_node_body(10.0, "rtc", -80, bare, 1105, none)) ==
        encode_node_record(7, 10.0, "rtc", -80, bare, 1105, none));
  CHECK(parse_seq(with_seq(7, encode_node_body(10.0, "rtc", -80, bare, 1105, none)).c_str()) ==
        std::optional<uint32_t>(7));
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
  CHECK(with_seq(5521, encode_ecowitt_body(1791378600.1, "ntp", e)) ==
        encode_ecowitt_record(5521, 1791378600.1, "ntp", e));

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
