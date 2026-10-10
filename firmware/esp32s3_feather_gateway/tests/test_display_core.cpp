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
