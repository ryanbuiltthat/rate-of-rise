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

}  // namespace creek_display
