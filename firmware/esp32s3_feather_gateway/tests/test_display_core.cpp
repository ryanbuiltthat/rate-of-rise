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

int main() {
  test_fixed();
  test_with_unit();
  test_age_text();
  test_fresh();
  test_tier();
  test_node_battery();
  test_gauge();
  test_render_main_all_known();
  test_render_main_nothing_known();
  test_render_main_degraded();
  test_sent_cache();
  if (failures) {
    std::printf("%d failure(s)\n", failures);
    return 1;
  }
  std::printf("all display_core tests passed\n");
  return 0;
}
