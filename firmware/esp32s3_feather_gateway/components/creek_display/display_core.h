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
