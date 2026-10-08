// Creek sensor node — Moteino M0 + RFM69HW + DFRobot SEN0676.
//
// Reads water level via Modbus RTU from the SEN0676 80 GHz radar, packs a
// JSON payload, and transmits over RFM69HW (915 MHz) to the ESP32 gateway.
// Sleeps between cycles and duty-cycles the radar rail, which holds average draw to
// ~1.7 mA (measured 2026-09-25, open question #17). Until 2026-09-23 the SHDN wire was on
// the wrong header pin, so the radar never powered down and the node drew ~36 mA. It is on
// ~4 (PA08) now, which is what exposed the radar warm-up below. Reporting is
// every 60 s normally and every 5 s once it detects the creek rising (see
// "Adaptive crest sampling" below). Listens briefly after each TX for a
// wireless firmware push (see firmware/README.md, OTA section) so future
// updates don't require pulling the node off the pole.
//
// WIRING (Moteino M0 / SAMD21):
//   SEN0676 TX  -> Serial1 RX (pin 0)
//   SEN0676 RX  -> Serial1 TX (pin 1)
//   SEN0676 VCC -> switched 5 V rail (SENSOR_EN_PIN)
//   SEN0676 GND -> GND
//   RFM69HW     -> onboard SPI; CS/IRQ are RFM69.h's MOTEINO_M0 defaults (SS/D9)
//   RFM69HW RST -> D7 (soldered; pulsed on every wake, see resetRadioPin)
//   Battery     -> onboard 50% divider on A5 (no external wiring needed)
//
// LIBRARIES (see platformio.ini lib_deps):
//   - RFM69        (LowPowerLab)
//   - RTCZero      (Arduino — RTC alarm for timed wakeup from standby)
//   Standby itself is the local standby() below rather than LowPowerLab's LowPower
//   library: that library's standby() lacks the SysTick guard (see "Hardware watchdog").
//
// Confirmed on hardware 2026-09-07 (see firmware/README.md, Bench-Test Procedure
// step 1). BENCH_TEST is disabled below for enclosure/pole install — the node now
// sleeps between reports instead of running the bench cadence.

#include <Arduino.h>
#include <RFM69.h>
#include <RFM69_ATC.h>
#include <RFM69_OTA.h>
#include <SPIFlash.h>
#include <RTCZero.h>

// ─── Radio ───────────────────────────────────────────────────────────────────
#define NODEID        1
#define GATEWAYID     2
#define NETWORKID     100
#define FREQUENCY     RF69_915MHZ
//#define FREQUENCY_EXACT 916000000 // you may define an exact frequency/channel in Hz
#define IS_RFM69HCW    true
// Set in firmware/README.md — must match gateway.
#define ENCRYPT_KEY   "KksDNqcNb6mCY4xA"
//This setting enables this gateway to work with remote nodes that have ATC enabled to
//dial their power down to only the required level (ATC_RSSI)
#define ENABLE_ATC    //comment out this line to disable AUTO TRANSMISSION CONTROLg
#define ATC_RSSI      -78

// ─── Pins ────────────────────────────────────────────────────────────────────
#define RFM69_RST     7
#define SENSOR_EN_PIN 4     // drives a MOSFET or boost-converter EN to power the SEN0676

// ─── Timing ──────────────────────────────────────────────────────────────────
#define REPORT_INTERVAL_S   60        // 60 s between reports (spec §2)
#define MODBUS_TIMEOUT_MS   1000
// Standby is taken in naps no longer than this so the hardware watchdog (16 s period, see
// "Hardware watchdog") can stay armed across the whole sleep. The watchdog runs from the
// internal OSCULP32K, which is only loosely calibrated: budget for it running up to a
// quarter fast (a 12 s real period) and 8 s naps still leave room for the wake, the RTC
// re-arm and the feed. A nap's wake-up costs a few milliseconds of MCU time, so eight of
// them a cycle is a rounding error on the power budget.
#define SLEEP_NAP_MAX_S     8

// ─── Radio bring-up ──────────────────────────────────────────────────────────
// RFM69::initialize() gives the radio 50 ms to report ModeReady after the reset pulse and
// returns false otherwise. One attempt per wake turned a slow radio into a silent node: the
// node went on cycling, radar and all, and "skipped TX this cycle" every minute for hours
// (2026-09-27..29, see docs). So give it a few tries with a longer reset hold and settle
// between them, and count every failed attempt into the `i` payload field so the packet
// that finally gets out says how hard the radio was to wake.
#define RADIO_INIT_ATTEMPTS        3
#define RADIO_INIT_RETRY_HOLD_MS   2     // RST high; the datasheet minimum is 100 us
#define RADIO_INIT_RETRY_SETTLE_MS 20    // after RST falls; datasheet minimum is 5 ms

// ─── Radar warm-up ───────────────────────────────────────────────────────────
// The datasheet's "100 ms startup" is when the SEN0676 starts ANSWERING, not when its
// answer means anything. Register 0x0001 is filtered, and straight off a cold rail it
// reads 0 -- a well-formed reply with a good CRC. This fixed 500 ms delay used to be a
// single read, which went unnoticed for as long as the EN wire was on the wrong header pin
// (SHDN's own pull-up kept the radar on 24/7, so every read found a settled filter). The
// wire moved to ~4 on 2026-09-23 and the creek immediately read 0; pulling it again brought
// the reading back. A 0 is not harmless
// either -- the gateway clamps anything inside the blanking zone to the range ceiling,
// so it published a calm creek as 37.6 in, a few inches off the bank top.
//
// So poll until the filter has settled: two consecutive non-zero readings that agree
// within SENSOR_STABLE_MM (the sensor is ±5 mm, so honest consecutive readings of a
// still surface land within 10 mm of each other).
#define SENSOR_FIRST_POLL_MS     300   // first Modbus attempt after power-up
#define SENSOR_POLL_MS           250   // between warm-up polls
#define SENSOR_STABLE_MM         10
// If the filter never settles, what to report depends on where the creek last was.
//
// Near the sensor, report the last answer -- even a 0. The gateway clamps anything inside
// the blanking zone to the range ceiling so that a radar reading "too close" keeps the
// alarm up as the creek nears the bank, and a null would drop it there.
//
// Anywhere else, report null. The old rule ("erring high only costs battery") was wrong:
// a cold radar's 0 on a creek sitting at 11 in was clamped to 37.6 in by the gateway, and
// that raised a Tier 4 Emergency on a dry 2026-09-17. Water cannot reach the blanking zone
// from far below it within one report, so an unsettled answer there is a sensor fault, and
// null is what the gateway's radar-fault flag is built to count.
#define SENSOR_READY_TIMEOUT_MS  10000
// Must match the gateway's `blanking_mm` (gateway.base.yaml) -- a test checks it.
#define SENSOR_BLANKING_MM       150
// An unsettled answer is trusted only if the last good reading was within this distance of
// the blanking zone (~12 in of water below the range ceiling).
#define UNSETTLED_TRUST_WITHIN_MM 300
#define OTA_LISTEN_MS       1500      // post-TX window to catch a wireless firmware push

// ─── Adaptive crest sampling (open question #15) ─────────────────────────────
// Spec §1: rainfall-to-crest here is tens of minutes, so a fixed 60 s cadence samples
// the crest of a flashy rise about as coarsely as it can be sampled and still be called
// a measurement. Drop to FAST_REPORT_INTERVAL_S once the creek is actually rising, and
// go back to 60 s when it isn't — the battery cost lands only during a rise, which is
// exactly when spending it is correct.
//
// This works at all because standby() is a WFI, not a reset: loop() resumes
// where it left off and the statics below survive every sleep. An ESP32 deep-sleep node
// would need RTC_DATA_ATTR or flash to carry the same state across a wake.
#define FAST_REPORT_INTERVAL_S   5
// The radar reports "empty height" (sensor face → water), so a rise makes the distance
// *shrink*; "drop" below is (older reading − current) and comes out positive on a rise.
//
// A rise is a drop of RISE_MIN_DROP_MM against a reading between RISE_LOOKBACK_MIN_S and
// RISE_LOOKBACK_MAX_S old. Both halves matter. The SEN0676 is ±5 mm, so the drop has to be
// one a single sample's jitter cannot fake; and it has to be measured over minutes, not
// over consecutive samples. The first version compared consecutive readings against
// 0.5 mm/min, which at the 5 s cadence meant any 1 mm wobble read as 10 mm/min: from
// 2026-09-27 the node sat in fast mode ~90 % of the time on a creek that was not moving,
// with the radar rail up and the radio keying every 6 s all night.
//
// The implied trigger rate is RISE_MIN_DROP_MM over RISE_LOOKBACK_MAX_S: 5 mm / 7 min is
// ~0.7 mm/min, 0.028 in/min. That sits deliberately below the add-on's 0.05 in/min Warning
// trigger (tiers.py WARNING_RATE_OF_RISE_IN_MIN) so the node is already sampling fast
// *before* a Warning is plausible. A test in rate_of_rise/tests enforces that ordering.
#define RISE_MIN_DROP_MM         5
#define RISE_LOOKBACK_MIN_S      240
#define RISE_LOOKBACK_MAX_S      420
// One reading is kept per RTC minute so the lookback has something to compare against
// whatever the cadence; 8 slots cover RISE_LOOKBACK_MAX_S with a minute to spare.
#define RISE_HISTORY_SLOTS       8
// Requiring consecutive qualifying samples is what makes the trigger mean "rising"
// rather than "jittering", and mirrors the add-on's own WARNING_RATE_OF_RISE_CONFIRM_SAMPLES
// guard on the same quantity.
#define RISE_CONFIRM_SAMPLES     2
// Hysteresis on the way out: a plateau part-way up a real rise shouldn't drop the
// cadence back to 60 s just before the crest. At the fast interval this is ~50 s of
// quiet before reverting, on top of the lookback still seeing the rise for several minutes.
#define FAST_MODE_HOLD_SAMPLES   10
// Each wake already costs the Modbus read (the rail stays up in fast mode, so no warm-up) before the
// OTA_LISTEN_MS window, so at a 5 s sleep the full 1.5 s window would make the real
// period ~7 s and hold the node awake ~30 % of it. Shortening the window in fast mode
// buys that back. It is shortened rather than skipped so a push is still *possible*
// mid-rise: the gateway answers the telemetry packet inline and typically handshakes
// within a few ms (firmware/README.md, "Why arming waits"), which this still covers.
#define OTA_LISTEN_FAST_MS       300

#define SECONDS_PER_DAY          86400L

// ─── Radar-rail diagnostic window (open question #17) ────────────────────────
// TEMPORARY DIAGNOSTIC. Ships disabled; set to 1, flash, collect one night, revert.
//
// WHAT IT ANSWERS. The pack discharges ~5.7x faster than it did before 2026-09-14 (the
// measurement is in docs/node-hardware.md, "Measuring average draw without a shunt").
// The prime suspect is that SHDN on the radar's U1V11F5 5 V boost is not actually
// connected to SENSOR_EN_PIN. That pin is pulled high on the board, so a loose or broken
// wire leaves the regulator permanently ENABLED -- the SEN0676 runs 24/7 at ~35 mA, every
// reading stays perfect, and the only symptom is the battery. Nothing in the telemetry
// distinguishes that from a correctly duty-cycled rail, because in both cases the
// firmware drives the pin exactly the same way.
//
// The only way to tell is to hold the rail off long enough for the pack to answer. If the
// overnight slope drops (~11 mV/h -> ~4-5 mV/h) the wiring is good and the load is
// elsewhere. If it does not move, SHDN is disconnected -- diagnosed without a site visit.
//
// Two hours is enough: ~120 samples gives a slope standard error near 0.7 mV/h against a
// ~6 mV/h effect. It doubles as the calibration anchor, since a known ~35 mA step against
// a measured slope change converts mV/h to mA for every future night.
//
// THE WINDOW IS MEASURED FROM WHEN THIS IMAGE STARTED RUNNING, by accumulating elapsed
// seconds -- NOT by reading the RTC's absolute value. That distinction is the whole reason
// the 2026-09-23 run collected nothing.
//
// This used to test secondsOfDay() directly, on the stated theory that "setup() calls
// rtc.begin() and never rtc.setTime(), so the RTC starts at 00:00:00 on every power-up".
// That is false after an OTA. RTCZero::begin(bool resetTime = false) preserves the clock
// when the reset cause is a watchdog, system or external reset:
//
//     if ((!resetTime) && (PM->RCAUSE.reg & (PM_RCAUSE_SYST|PM_RCAUSE_WDT|PM_RCAUSE_EXT)))
//       ... oldTime.reg = RTC->MODE2.CLOCK.reg;          // captured
//     if ((!resetTime) && (validTime) && (oldTime.reg != 0L))
//       RTC->MODE2.CLOCK.reg = oldTime.reg;              // restored, not zeroed
//
// and RFM69_OTA reboots through resetUsingWatchdog(), which sets PM_RCAUSE_WDT. So after a
// wireless update the RTC carries on from wherever it was, anchored to the last time the
// battery was physically connected. The window was therefore parked at an arbitrary offset
// that the 8.4 h the diagnostic image ran never swept across -- it opened zero times, and
// the resulting unchanged battery slope was indistinguishable from a real diagnosis.
//
// Accumulating deltas is immune to all of that: it does not care what the clock reads, only
// how much it advances. The window now genuinely opens DIAG_WINDOW_START_S after this image
// starts, whatever reset brought it up, and repeats every 24 h of running from there.
#define DIAG_RADAR_WINDOW_ENABLE 0        // 1 to arm. Keep 0 on anything left on the pole.
#define DIAG_WINDOW_START_S      7200L    // opens this long after boot
#define DIAG_WINDOW_LENGTH_S     7200L    // and stays open this long (start+length < 24 h)

// Going blind is the cost of this test, and in a basin where rainfall-to-crest is tens of
// minutes, two unbroken hours of it is not acceptable. So the window is not actually
// unbroken: every DIAG_PEEK_EVERY cycles the rail comes up for one ordinary reading, which
// caps the blind gap at DIAG_PEEK_EVERY * REPORT_INTERVAL_S (20 min at the values here).
// The peeks cost ~2 s of radar per 20 min -- about 0.06 mA averaged, which is nothing
// against the ~35 mA the test is trying to see.
#define DIAG_PEEK_EVERY          20

// A peek that finds the creek high or rising abandons the window until the next day. The
// node only has RAW DISTANCE -- the datum lives in Home Assistant, not here -- so this is a
// distance, and distance SHRINKS as water rises: the window aborts when the reading falls
// BELOW this.
//
// DERIVE IT FROM THE CREEK, NOT FROM A ROUND NUMBER. This was 850 mm on the theory that
// 1105 - 850 = 255 mm is about 10 in of depth and therefore "well under the 24 in Warning".
// Both halves were true and the value was still wrong, because nobody checked what the creek
// actually sits at: baseline depth here is 11-13 in, occasionally 16. So the guard read
// normal conditions as "creek too high" and abandoned the window on its first peek, every
// single night. The 2026-09-21 run never held the rail once, and the resulting unchanged
// battery slope looked exactly like a confirmed diagnosis.
//
// The bound that matters is the Warning threshold, not an arbitrary margin above zero:
//   WARNING_STAGE_FT 2.0 ft = 24 in = 610 mm depth  ->  1105 - 610 =  495 mm distance
//   this guard, 18 in = 457 mm depth                ->  1105 - 457 =  648 mm distance
//   observed baseline, 11-13 in (16 in peak)        ->           ~790-840 mm distance
// So it sits above the Warning distance (aborts well before a Warning is plausible) and
// below the creek's normal distance (does not abort on an ordinary night). fastMode is the
// first line of defence anyway -- any rise at all blocks the window before this is reached.
//
// **Re-derive it if the pole is ever raised** (open question #16): the install height moves
// and this constant does not follow it.
#define DIAG_MIN_SAFE_DISTANCE_MM 650

// ONE-SHOT. The window runs until it has collected this many held cycles, then latches shut
// for the life of the boot. This is what makes the armed image safe to forget about: a
// diagnostic build left on the node costs one window, once -- not a blind window every day
// until somebody notices. It is also why there is no "disarm" button in Home Assistant, and
// why nobody has to remember to reinstall the normal firmware on a schedule.
//
// The latch is deliberately tied to USEFUL data rather than to the window merely having
// opened. A window abandoned early -- creek up, or the node in fast-sampling mode -- does not
// count, so the test quietly retries the next night instead of burning its single shot on a
// storm and needing a human to notice and press the button again. At a 60 s cadence with a
// peek every 20 cycles, a full two-hour window yields ~114 held cycles, so this asks for
// roughly half a window.
#define DIAG_MIN_USEFUL_HOLDS    60

// ─── Bench testing ───────────────────────────────────────────────────────────
// On USB/bench power: skip standby entirely so the board stays
// reachable over serial instead of the port dropping for 30-55 s per wake,
// and cycles fast enough to check the sensor/radio. Comment out before
// deploying on battery.
//#define BENCH_TEST
#define BENCH_TEST_INTERVAL_MS   5000  // delay between cycles while bench testing

// ─── Modbus RTU constants (SEN0676 datasheet) ────────────────────────────────
#define SENSOR_ADDR   0x01
#define FUNC_READ     0x03
#define REG_DISTANCE  0x0001   // "empty height": sensor face → water surface, mm

#ifdef ENABLE_ATC
  RFM69_ATC radio;
#else
  RFM69 radio;
#endif
RTCZero rtc;
SPIFlash flash(SS_FLASHMEM, 0xEF30); //EF30 for 4mbit  Windbond chip (W25X40CL)

volatile bool rtcAlarmFired = false;
void rtcAlarmISR() { rtcAlarmFired = true; }

// ─── Adaptive crest sampling state ───────────────────────────────────────────
// Carried across standby in plain SRAM — see the FAST_REPORT_INTERVAL_S comment above
// for why that is safe here. -1 means "no sample yet"; a failed Modbus read leaves these
// untouched rather than poisoning the next rate with a reading that never happened.
static int32_t lastDistanceMm = -1;
static uint8_t risingSamples  = 0;
static uint8_t quietSamples   = 0;
static bool    fastMode       = false;

// One good reading per RTC minute, oldest overwritten. riseHead is the next slot to write;
// riseCount saturates at RISE_HISTORY_SLOTS. lastBucket is the minute the newest entry
// belongs to, so a 5 s cadence still stores one sample a minute.
struct RiseSample { int32_t sod; int32_t mm; };
static RiseSample riseHistory[RISE_HISTORY_SLOTS];
static uint8_t    riseHead    = 0;
static uint8_t    riseCount   = 0;
static int32_t    lastBucket  = -1;

// ─── Diagnostics on the wire ─────────────────────────────────────────────────
// Three fields that let the first packet after an outage say what the outage was. All
// cost radio bytes, so they are single-letter keys (see the packet budget note in loop()).
//   r  PM->RCAUSE, read once at boot: 1 power-on, 2/4 brown-out, 16 external (the reset
//      button), 32 watchdog, 64 software (RFM69_OTA's reboot into the bootloader).
//   n  cycles since boot, wrapping. Back at 0 means the node reset; carrying on from where
//      it was means it resumed -- a stalled clock, or a radio that would not come up.
//   i  radio init attempts that failed since the last transmit. Non-zero on the packet that
//      ends a silence means the MCU was awake the whole time and the radio was the problem.
static uint8_t  resetCause        = 0;
static uint16_t cycleCount        = 0;
static uint8_t  radioInitFailures = 0;

#if DIAG_RADAR_WINDOW_ENABLE
// Window bookkeeping, all in plain SRAM across standby like the crest sampling state above.
// diagCycles counts every cycle inside the window and diagHeld only the ones that actually
// held the rail off; diagAborted parks a window that turned unsafe, and clears when the
// window closes so one high-water night does not disable the test for good. diagCompleted is
// the one-shot latch and never clears -- only a reboot re-arms it.
// Seconds this image has been running, accumulated from RTC deltas rather than read off the
// clock -- see the note above. diagLastSod is -1 until the first cycle establishes a base.
static int32_t  diagLastSod   = -1;
static uint32_t diagUptimeS   = 0;
static uint16_t diagCycles    = 0;
static uint16_t diagHeld      = 0;
static bool     diagAborted   = false;
static bool     diagCompleted = false;
// Pack voltage at the window's first cycle and at its most recent one. The window only counts
// as usable if the pack FELL across it -- see the daylight check where the latch closes.
static uint16_t diagStartMv   = 0;
static uint16_t diagLastMv    = 0;
#endif

// RTC seconds-of-day. This is the only usable time base across a sleep: the RTC is what
// wakes the MCU, while millis()' clock is gated off during standby and so under-counts
// every cycle by however long the node was asleep.
static int32_t secondsOfDay() {
  return (int32_t)rtc.getHours() * 3600L
       + (int32_t)rtc.getMinutes() * 60L
       + (int32_t)rtc.getSeconds();
}

// ─── Hardware watchdog ───────────────────────────────────────────────────────
// The catch-all for a hang nobody has found yet. The specific spins already known are
// defended one by one (radioBringUp, flash.sleep), but RFM69::sendFrame() still waits for
// ModeReady and PacketSent with no timeout on every TX, and a board browning out under the
// radar rail's inrush can wedge anywhere. Any of those used to leave the node off the air
// until someone waded out and pressed reset; now the SAMD21 resets itself and reports
// again, with `r` = 32 on the wire to say so.
//
// Armed in setup() and left running, sleep included. The first version (#43) was clocked
// from GCLK2 and disarmed for standby, which left the two failures that actually needed
// catching uncovered: a node that never wakes -- the SAMD21's "sporadically never wakes from
// standby" lockup (see standby() below) or a stalled 32 kHz crystal -- was asleep with the
// watchdog off, and a stalled crystal would have stopped the watchdog too, because GCLK2
// is the RTC's crystal generator. So this one runs from OSCULP32K, the internal
// ultra-low-power oscillator that is always on and cannot stall, on a generator nothing
// else uses; and sleepSeconds() takes standby in SLEEP_NAP_MAX_S naps with a feed between
// them, so the 16 s period spans the sleep. The only time it stands down is an OTA transfer,
// which blocks in the library for ~40 s and has its own timeouts.
//
// Budget, assuming OSCULP32K may run a quarter fast (a 12 s real period): the radar warm-up
// is the one awake stretch that can approach that (SENSOR_READY_TIMEOUT_MS plus a final
// Modbus timeout, ~11 s), so readRadarDistance() feeds once per poll -- each poll is
// bounded by MODBUS_TIMEOUT_MS off millis(), and a hang inside one (a stuck Serial1 flush)
// stops the feeding, which is the point. A feed after the read covers the TX and listen
// window, and sleepSeconds() feeds between naps.
//
// GCLK generators here: 0 DFLL48M and 1 XOSC32K belong to the Arduino core, 2 to RTCZero
// (XOSC32K / 32 for the RTC), 3 OSC8M to the core. 4 is free. OSCULP32K / 32 = 1.024 kHz,
// so the WDT cycle counts are milliseconds (near enough).
#define WDT_GCLK_GEN 4

static void wdtEnable() {
  // Stop the WDT first: its own register sync needs whatever clock it currently has.
  WDT->CTRL.reg = 0;
  while (WDT->STATUS.bit.SYNCBUSY);
  // A clock channel must be disabled before its generator is changed (datasheet 15.6.2.6).
  // Writing CLKCTRL with the ID and CLKEN clear does that; the read-back that follows is of
  // the channel the ID selected.
  GCLK->CLKCTRL.reg = GCLK_CLKCTRL_ID_WDT;
  while (GCLK->CLKCTRL.bit.CLKEN);
  GCLK->GENDIV.reg = GCLK_GENDIV_ID(WDT_GCLK_GEN) | GCLK_GENDIV_DIV(4);   // DIVSEL: 2^(4+1) = 32
  while (GCLK->STATUS.bit.SYNCBUSY);
  // No RUNSTDBY: that bit only governs the GCLK_IO pin. Internally a generator keeps running
  // in standby for as long as an enabled peripheral requests it, and the WDT does.
  GCLK->GENCTRL.reg = GCLK_GENCTRL_ID(WDT_GCLK_GEN) | GCLK_GENCTRL_SRC_OSCULP32K |
                      GCLK_GENCTRL_DIVSEL | GCLK_GENCTRL_GENEN;
  while (GCLK->STATUS.bit.SYNCBUSY);
  GCLK->CLKCTRL.reg = GCLK_CLKCTRL_ID_WDT | GCLK_CLKCTRL_GEN(WDT_GCLK_GEN) | GCLK_CLKCTRL_CLKEN;
  while (GCLK->STATUS.bit.SYNCBUSY);
  WDT->CONFIG.reg = WDT_CONFIG_PER_16K;     // 16384 cycles ≈ 16 s
  WDT->INTENCLR.reg = WDT_INTENCLR_EW;
  WDT->CTRL.reg = WDT_CTRL_ENABLE;
  while (WDT->STATUS.bit.SYNCBUSY);
}

static void wdtFeed() {
  if (WDT->STATUS.bit.SYNCBUSY) return;     // a clear already in flight restarts it anyway
  WDT->CLEAR.reg = WDT_CLEAR_CLEAR_KEY;
}

static void wdtDisable() {
  WDT->CTRL.reg = 0;
  while (WDT->STATUS.bit.SYNCBUSY);
}

// SAMD21 standby. This is LowPowerLab's standby (SLEEPDEEP + WFI) plus the guard that
// ArduinoLowPower carries and that library does not: a SysTick interrupt landing on the WFI can leave the chip
// asleep for good, with the RTC alarm unable to bring it back (Microchip forum, "SAMD21
// sporadically locks and does not wake from standby"). Masking SysTick across the WFI is the
// accepted fix. millis() is stopped in standby anyway, so nothing is lost by it.
static void standby() {
  SysTick->CTRL &= ~SysTick_CTRL_TICKINT_Msk;
  SCB->SCR |= SCB_SCR_SLEEPDEEP_Msk;
  __DSB();
  __WFI();
  SysTick->CTRL |= SysTick_CTRL_TICKINT_Msk;
}

// One nap: standby until an RTC alarm `seconds` from now. The RTC match interrupt is what
// wakes the chip; anything else that fires (USB, a pin) ends the nap early, and the caller
// re-reads the clock and naps again.
static void napSeconds(uint16_t seconds) {
  int32_t target = (secondsOfDay() + (int32_t)seconds) % SECONDS_PER_DAY;

  rtcAlarmFired = false;
  rtc.setAlarmTime((uint8_t)(target / 3600L),
                   (uint8_t)((target % 3600L) / 60L),
                   (uint8_t)(target % 60L));
  rtc.enableAlarm(rtc.MATCH_HHMMSS);

  // MATCH_HHMMSS matches on h:m:s, so it fires once per *day*. If the target second
  // slips past between arming above and the WFI below, the next match is not `seconds`
  // away, it is 24 hours away — the node off the air for a day, including the listen
  // window that OTA needs to fix it. Re-read the clock as late as possible and skip the
  // standby entirely unless the target is still ahead of us: the worst case is then one
  // un-slept nap rather than a lost day. (The watchdog now bounds even that day to 16 s,
  // but a reset is still a lost cycle and a reset cause on the wire; better not to.)
  int32_t remaining = target - secondsOfDay();
  if (remaining < 0) remaining += SECONDS_PER_DAY;
  if (remaining > 0 && remaining <= (int32_t)seconds) {
    standby();
  } else {
    Serial.println(F("RTC alarm already passed; skipping this nap"));
  }

  rtc.disableAlarm();
}

// Sleep `seconds` in naps the watchdog can span, feeding it between them. Measured off the
// RTC rather than counted, so an early wake or an un-slept nap costs nothing but a re-arm.
// Every way this can go wrong ends in a watchdog reset rather than a hang: a nap that never
// wakes (alarm lost, clock stalled) runs the 16 s out with nobody feeding; and the RTC sync
// spins inside RTCZero are awake code the watchdog also sees.
static void sleepSeconds(uint16_t seconds) {
  const int32_t start = secondsOfDay();
  for (;;) {
    wdtFeed();
    int32_t elapsed = secondsOfDay() - start;
    if (elapsed < 0) elapsed += SECONDS_PER_DAY;     // wrapped past midnight
    if (elapsed >= (int32_t)seconds) return;
    const int32_t left = (int32_t)seconds - elapsed;
    napSeconds((uint16_t)(left > SLEEP_NAP_MAX_S ? SLEEP_NAP_MAX_S : left));
  }
}

// ─── RFM69 reset & init ──────────────────────────────────────────────────────
// RFM69::setMode() spins with no timeout while the radio is leaving sleep, so a
// radio that stops answering MODEREADY after a standby hangs the MCU until the
// reset button is pressed. Hardware-reset the radio on every wake and rebuild its
// config from scratch instead: initialize() has a 50 ms SPI handshake timeout, so
// a bad wake costs one report rather than the node (and, since the watchdog, a hang
// costs 16 s rather than a site visit).
static void resetRadioPin() {
  digitalWrite(RFM69_RST, HIGH);   // SX1231: >=100 us high, then >=5 ms before use
  delayMicroseconds(100);
  digitalWrite(RFM69_RST, LOW);
  delay(5);
}

static bool radioInit() {
  if (!radio.initialize(FREQUENCY, NODEID, NETWORKID)) return false;
  #ifdef IS_RFM69HCW
    radio.setHighPower(); //must include this only for RFM69HW/HCW!
  #endif
  #ifdef ENCRYPT_KEY
    radio.encrypt(ENCRYPT_KEY);
  #endif
  #ifdef FREQUENCY_EXACT
    radio.setFrequency(FREQUENCY_EXACT); //set frequency to some custom frequency
  #endif
  #ifdef ENABLE_ATC
    radio.enableAutoPower(ATC_RSSI);
  #endif
  return true;
}

// Reset and initialize the radio, retrying with a longer reset hold and settle when the
// first attempt does not come up (see "Radio bring-up"). Every failed attempt is counted
// into radioInitFailures for the payload. ~150 ms per failed attempt, well inside the
// watchdog budget.
static bool radioBringUp() {
  for (uint8_t attempt = 0; attempt < RADIO_INIT_ATTEMPTS; attempt++) {
    if (attempt == 0) {
      resetRadioPin();
    } else {
      digitalWrite(RFM69_RST, HIGH);
      delay(RADIO_INIT_RETRY_HOLD_MS);
      digitalWrite(RFM69_RST, LOW);
      delay(RADIO_INIT_RETRY_SETTLE_MS);
    }
    if (radioInit()) return true;
    if (radioInitFailures < 255) radioInitFailures++;
    Serial.print(F("RFM69 init failed, attempt "));
    Serial.println(attempt + 1);
  }
  return false;
}

// ─── Modbus CRC-16 (standard) ────────────────────────────────────────────────
static uint16_t modbusCRC16(const uint8_t *buf, uint8_t len) {
  uint16_t crc = 0xFFFF;
  for (uint8_t i = 0; i < len; i++) {
    crc ^= buf[i];
    for (uint8_t j = 0; j < 8; j++) {
      if (crc & 1) crc = (crc >> 1) ^ 0xA001;
      else         crc >>= 1;
    }
  }
  return crc;
}

// ─── Read a single holding register via Modbus RTU on Serial1 ────────────────
// Returns the register value, or -1 on timeout/CRC error.
static int32_t modbusReadHolding(uint8_t addr, uint16_t reg) {
  uint8_t req[8];
  req[0] = addr;
  req[1] = FUNC_READ;
  req[2] = (reg >> 8) & 0xFF;
  req[3] = reg & 0xFF;
  req[4] = 0x00;          // register count high
  req[5] = 0x01;          // register count low (1 register)
  uint16_t crc = modbusCRC16(req, 6);
  req[6] = crc & 0xFF;
  req[7] = (crc >> 8) & 0xFF;

  // Flush any stale data
  while (Serial1.available()) Serial1.read();

  Serial1.write(req, 8);
  Serial1.flush();

  // Response: addr(1) + func(1) + byteCount(1) + data(2) + crc(2) = 7 bytes
  uint8_t resp[7];
  uint8_t idx = 0;
  uint32_t start = millis();
  while (idx < 7 && (millis() - start) < MODBUS_TIMEOUT_MS) {
    if (Serial1.available()) {
      resp[idx++] = Serial1.read();
    }
  }
  if (idx < 7) return -1;

  uint16_t respCrc = modbusCRC16(resp, 5);
  uint16_t rxCrc = resp[5] | (resp[6] << 8);
  if (respCrc != rxCrc) return -1;
  if (resp[0] != addr || resp[1] != FUNC_READ) return -1;

  return (resp[3] << 8) | resp[4];
}

// ─── Read a settled distance off the radar rail ──────────────────────────────
// Powers the rail if it is off and polls until the filter settles (see "Radar warm-up").
// A rail that is already on -- fast mode keeps it up between cycles -- has a live filter,
// so one good read is enough. Returns mm, or -1 if the radar never answered at all.
static bool radarPowered = false;

static int32_t readRadarDistance() {
  if (radarPowered) {
    const int32_t d = modbusReadHolding(SENSOR_ADDR, REG_DISTANCE);
    if (d > 0) return d;
    // A warm radar reading 0 or not answering: fall through and treat it as cold.
  } else {
    digitalWrite(SENSOR_EN_PIN, HIGH);
    radarPowered = true;
  }

  const uint32_t poweredAt = millis();
  delay(SENSOR_FIRST_POLL_MS);
  int32_t prev = -1;
  int32_t last = -1;
  for (;;) {
    wdtFeed();                  // per poll; see the budget note under "Hardware watchdog"
    const int32_t d = modbusReadHolding(SENSOR_ADDR, REG_DISTANCE);
    if (d > 0 && prev > 0 && labs(d - prev) <= SENSOR_STABLE_MM) {
      Serial.print(F("radar settled after "));
      Serial.print(millis() - poweredAt);
      Serial.println(F(" ms"));
      return d;
    }
    if (d >= 0) last = d;
    prev = d;
    if (millis() - poweredAt >= SENSOR_READY_TIMEOUT_MS) break;
    delay(SENSOR_POLL_MS);
  }
  // See SENSOR_READY_TIMEOUT_MS for why the answer depends on where the creek last was.
  // lastDistanceMm is the last *good* reading (updateRateOfRise leaves it alone on a -1).
  const bool nearCeiling = lastDistanceMm >= 0 &&
      lastDistanceMm <= (int32_t)(SENSOR_BLANKING_MM + UNSETTLED_TRUST_WITHIN_MM);
  if (nearCeiling && last >= 0) {
    Serial.print(F("radar did not settle near the ceiling; reporting last answer "));
    Serial.println(last);
    return last;
  }
  Serial.print(F("radar did not settle (last answer "));
  Serial.print(last);
  Serial.println(F("); reporting no reading"));
  return -1;
}

static void radarPowerDown() {
  digitalWrite(SENSOR_EN_PIN, LOW);
  radarPowered = false;
}

// ─── Sampling cadence ────────────────────────────────────────────────────────
// Entering fast mode takes RISE_CONFIRM_SAMPLES consecutive qualifying samples; leaving
// it takes FAST_MODE_HOLD_SAMPLES quiet ones. The two are deliberately unequal — see
// their definitions for why sensor noise argues for the first and the shape of a real
// crest argues for the second.
static void updateSampleMode(bool rising) {
  if (rising) {
    quietSamples = 0;
    if (risingSamples < RISE_CONFIRM_SAMPLES) risingSamples++;
    if (!fastMode && risingSamples >= RISE_CONFIRM_SAMPLES) {
      fastMode = true;
      Serial.println(F("rise confirmed -> fast sampling"));
    }
  } else {
    risingSamples = 0;
    if (fastMode && ++quietSamples >= FAST_MODE_HOLD_SAMPLES) {
      fastMode = false;
      quietSamples = 0;
      Serial.println(F("creek quiet -> normal sampling"));
    }
  }
}

// Keep one good reading per RTC minute. At the 5 s cadence that is every twelfth sample;
// at 60 s it is (nearly) every one.
static void rememberReading(int32_t sod, int32_t mm) {
  const int32_t bucket = sod / 60;
  if (bucket == lastBucket) return;
  lastBucket = bucket;
  riseHistory[riseHead].sod = sod;
  riseHistory[riseHead].mm = mm;
  riseHead = (uint8_t)((riseHead + 1) % RISE_HISTORY_SLOTS);
  if (riseCount < RISE_HISTORY_SLOTS) riseCount++;
}

// The drop (older − current, positive on a rise) against the oldest stored reading whose
// age is inside the lookback window. False when no reading qualifies yet -- the first few
// minutes after boot, or after the clock has been changed under us.
static bool dropOverLookback(int32_t sod, int32_t mm, int32_t *drop) {
  for (uint8_t k = 0; k < riseCount; k++) {
    // Oldest first: riseHead is the next slot to write, so the oldest live entry sits
    // riseCount slots behind it.
    const RiseSample &s =
        riseHistory[(riseHead + RISE_HISTORY_SLOTS - riseCount + k) % RISE_HISTORY_SLOTS];
    int32_t age = sod - s.sod;
    if (age < 0) age += SECONDS_PER_DAY;               // wrapped past midnight
    if (age > RISE_LOOKBACK_MAX_S) continue;           // too old; try the next newer one
    if (age < RISE_LOOKBACK_MIN_S) return false;       // everything newer is newer still
    *drop = s.mm - mm;
    return true;
  }
  return false;
}

// Fold a fresh reading into the cadence decision.
static void updateRateOfRise(int32_t distance_mm) {
  if (distance_mm < 0) return;        // failed read: no reading, no rate, no state change

  const int32_t nowSod = secondsOfDay();
  int32_t drop = 0;
  const bool measurable = dropOverLookback(nowSod, distance_mm, &drop);
  rememberReading(nowSod, distance_mm);
  lastDistanceMm = distance_mm;
  if (!measurable) return;            // nothing old enough to compare against: hold cadence
  updateSampleMode(drop >= RISE_MIN_DROP_MM);
}

#if DIAG_RADAR_WINDOW_ENABLE
// True while the diagnostic window is open. No midnight wrap handling, because
// DIAG_WINDOW_START_S + DIAG_WINDOW_LENGTH_S is asserted below to stay inside one day --
// a window that straddled the RTC's rollover would need the same (now - then + 86400)
// dance updateRateOfRise() does, and there is no reason to buy that complexity for a
// diagnostic that is meant to be reverted.
static_assert(DIAG_WINDOW_START_S + DIAG_WINDOW_LENGTH_S < SECONDS_PER_DAY,
              "diagnostic window must not cross the RTC's 24 h rollover");

// Fold this cycle's elapsed time into diagUptimeS. Wrap-safe the same way updateRateOfRise()
// is, and it keeps its own last-sample marker rather than sharing that one, because the rate
// tracker deliberately skips cycles whose Modbus read failed -- and a held cycle has no read
// at all, so sharing it would stall the clock exactly while the window was open.
static void tickDiagUptime() {
  const int32_t now = secondsOfDay();
  if (diagLastSod >= 0) {
    int32_t elapsed = now - diagLastSod;
    if (elapsed < 0) elapsed += SECONDS_PER_DAY;    // wrapped past the RTC's midnight
    // A jump beyond an hour is not elapsed time, it is the clock having been changed under
    // us; count nothing rather than skipping the window forward by a bogus amount.
    if (elapsed <= 3600) diagUptimeS += (uint32_t) elapsed;
  }
  diagLastSod = now;
}

static bool inDiagWindow() {
  const uint32_t since = diagUptimeS % (uint32_t) SECONDS_PER_DAY;
  return since >= (uint32_t) DIAG_WINDOW_START_S &&
         since < (uint32_t) (DIAG_WINDOW_START_S + DIAG_WINDOW_LENGTH_S);
}
#endif

// ─── Battery voltage via Moteino M0 onboard VIN divider ─────────────────────
// The Moteino M0 has a built-in 50% voltage divider on A5 connected to VIN.
// Formula from LowPowerLab: vin = analogRead(A5) * 2 * (3.3 / 1023)
// Returns millivolts for integer JSON payload.
static uint16_t readBatteryMv() {
  float vin = analogRead(A5) * 2.0f * 0.003226f;  // 0.003226 = 3.3 / 1023
  return (uint16_t)(vin * 1000.0f);
}

void setup() {
  // Why the last boot happened, for the `r` payload field. RCAUSE is latched until the next
  // reset, so reading it first costs nothing and nothing below can disturb it.
  resetCause = PM->RCAUSE.reg;
  // Armed before anything that can spin: flash.initialize() below waits on a chip that may
  // never answer, and the 5 s USB grace delay is well inside the 16 s period.
  wdtEnable();

  Serial.begin(115200);         // USB debug

  // SAMD21 native USB drops off the bus during standby (that
  // sleep mode gates the clock feeding the USB peripheral). Without this
  // delay, the first standby happens within ~1.5 s of reset -- too fast for
  // the IDE's 1200bps-touch upload to reach the board, so it goes invisible
  // to the OS and needs a manual double-tap-reset into the bootloader to
  // reflash. RTC wakeup resumes in loop(), not setup(), so this only costs
  // time once per physical power-cycle/reset, not on every 60 s sleep.
  Serial1.begin(115200);        // SEN0676 Modbus (datasheet default baud)
  delay(5000);

  pinMode(SENSOR_EN_PIN, OUTPUT);
  digitalWrite(SENSOR_EN_PIN, LOW);

  pinMode(RFM69_RST, OUTPUT);
  digitalWrite(RFM69_RST, LOW);
  if (!radioBringUp()) Serial.println(F("RFM69 init failed; retrying on next wake"));
  char buff[50];
  sprintf(buff, "\nTransmitting at %d Mhz...", FREQUENCY==RF69_433MHZ ? 433 : FREQUENCY==RF69_868MHZ ? 868 : 915);
  Serial.println(buff);

  if (flash.initialize())
  {
    Serial.print("SPI Flash Init OK ... UniqueID (MAC): ");
    flash.readUniqueId();
    for (byte i=0;i<8;i++)
    {
      Serial.print(flash.UNIQUEID[i], HEX);
      Serial.print(' ');
    }
    Serial.println();
    // Deep power-down. Only ever call this on a chip that is awake and present:
    // SPIFlash::command() spins on busy() first, and a chip in power-down (or no
    // chip at all) never answers, which hangs the MCU. CheckForWirelessHEX() wakes
    // the flash itself when an OTA handshake arrives.
    flash.sleep();
  }
  else
    Serial.println("SPI Flash MEM not found (is chip soldered?)...");

  #ifdef ENABLE_ATC
    Serial.println("RFM69_ATC Enabled (Auto Transmission Control)\n");
  #endif

  radio.sleep();

  // RTC for timed wakeup from standby. The time value doesn't matter —
  // only the alarm offset from "now" is used.
  rtc.begin();
  rtc.attachInterrupt(rtcAlarmISR);
  Serial.println(F("Creek node ready"));
}

void loop() {
  wdtFeed();                    // armed since setup(); see "Hardware watchdog"
  bool radioOk = radioBringUp();
  if (!radioOk) Serial.println(F("RFM69 init failed; skipping TX this cycle"));

  // Decide whether this cycle is one of the diagnostic hold cycles before touching the
  // rail. Compiles away entirely when the diagnostic is disabled, which is how it ships.
  bool diagHold = false;
#if DIAG_RADAR_WINDOW_ENABLE
  tickDiagUptime();
  const bool diagWindow = inDiagWindow() && !diagCompleted;
  if (!diagWindow) {
    // Leaving the window is where the one-shot latch closes -- but only if the window
    // actually produced enough held cycles to fit a slope to. A window cut short by high
    // water or a rising creek resets instead, and tries again tomorrow, so the diagnostic
    // does not spend its single shot on a night it could not measure.
    //
    // The pack has to have FALLEN across the window, or this was daylight and the number is
    // worthless. That check is what makes the button safe to press at any hour: the window is
    // measured from boot, so pressing at noon would otherwise put it at 2 pm, produce a
    // confident-looking slope of a charging battery, latch, and be done. Rising means the
    // panel is up -- and during charging the A5 divider reads the charger's OUT rail rather
    // than the cell, so the rise is large and unmistakable rather than marginal. Retry
    // tomorrow instead; the window lands at the same offset every day, so it will eventually
    // fall in the dark, and nobody has to have timed the press correctly.
    const bool fell = diagLastMv > 0 && diagLastMv <= diagStartMv;
    if (diagHeld >= DIAG_MIN_USEFUL_HOLDS && fell) {
      diagCompleted = true;
      Serial.println(F("diag: window produced a usable sample; not repeating this boot"));
    } else if (diagHeld >= DIAG_MIN_USEFUL_HOLDS) {
      Serial.println(F("diag: pack rose across the window (daylight); retrying tomorrow"));
    } else if (diagCycles > 0) {
      Serial.println(F("diag: window cut short, retrying tomorrow"));
    }
    diagCycles = 0;
    diagHeld = 0;
    diagStartMv = 0;
    diagLastMv = 0;
    diagAborted = false;
  } else if (!diagAborted && !fastMode) {
    // fastMode means the creek is already rising: never start going blind into that.
    // Peek on the window's first cycle and every DIAG_PEEK_EVERY thereafter.
    const bool peek = (diagCycles % DIAG_PEEK_EVERY) == 0;
    diagCycles++;
    diagHold = !peek;
    if (diagHold) diagHeld++;
  }
#endif

  // Battery first, before the radar rail comes up. Since the 2026-10-04 rework one cable down
  // the pole arm feeds the radar boost and the Moteino together, so VIN here is the charger's
  // output minus that cable's drop at whatever is flowing. With the radar powered (~60 mA)
  // that was ~0.2 V (4.3 V leaving the charger, 4.08 V read on 2026-10-05), enough to hide the
  // charger's 4.4 V daytime rail from the pack-health check. Now only the Moteino's own
  // ~10 mA is flowing, and the error is a few hundredths of a volt.
  //
  // Fast mode keeps the rail up between cycles, so readings taken during a rise still carry
  // the drop. That is acceptable: the pack-health slope already skips any night with fast
  // sampling, and rises are short.
  uint16_t batt_mv = readBatteryMv();

  int32_t distance_mm = -1;
  if (diagHold) {
    // Leave the rail low for the whole cycle -- that is the entire experiment. The null
    // distance this produces travels the existing failed-read path, so the gateway
    // publishes NAN and Home Assistant shows unknown, exactly as it does for a Modbus
    // timeout. Nothing downstream needs to know the difference, and the packet budget
    // (see below) has no room to tell it anyway.
    radarPowerDown();
    Serial.println(F("diag: radar rail held off this cycle"));
  } else {
    distance_mm = readRadarDistance();
  }
  wdtFeed();                    // fresh budget for the TX and listen window

#if DIAG_RADAR_WINDOW_ENABLE
  // A peek that finds the water up abandons the rest of tonight's window. Deliberately
  // only acts on a GOOD reading: a failed read (-1) is not evidence the creek is low, but
  // it is not evidence it is high either, and treating every Modbus timeout as an abort
  // would make the test impossible to complete on a node with a flaky sensor.
  if (diagWindow && !diagHold && distance_mm >= 0 &&
      distance_mm < DIAG_MIN_SAFE_DISTANCE_MM) {
    diagAborted = true;
    Serial.println(F("diag: creek too high, abandoning window until tomorrow"));
  }
#endif

#if DIAG_RADAR_WINDOW_ENABLE
  // Track the window's first and latest pack reading, for the fell-across-the-window test
  // above. Taken on every in-window cycle, peeks included: they are the same measurement --
  // and now genuinely so, because the battery is read before a peek powers the rail.
  if (diagWindow) {
    if (diagStartMv == 0) diagStartMv = batt_mv;
    diagLastMv = batt_mv;
  }
#endif

  // Decide this cycle's cadence before transmitting, so the payload and the OTA window
  // below both reflect it. Pure arithmetic, and guarded against a zero elapsed time —
  // nothing here can keep the node from reaching its TX.
  updateRateOfRise(distance_mm);

  // Fast mode keeps the radar rail up across its 5 s sleeps. A cold start costs a warm-up
  // of up to SENSOR_READY_TIMEOUT_MS, which at a 5 s cadence would stretch the period and
  // spend most of the rail-on time anyway -- while the creek is rising, the ~35 mA is the
  // right thing to spend. It drops again on the first cycle back at 60 s.
  if (!fastMode) radarPowerDown();

  // Build JSON payload. Keys, all one letter (the gateway also still decodes the older
  // long-key form, so the two sides can be updated in either order):
  //   d  distance_mm (null on a failed read)     v  battery_mv
  //   f  fast sampling this cycle                 g  radar rail deliberately held off (#17)
  //   r  reset cause   n  cycles since boot   i  failed radio init attempts since last TX
  // See "Diagnostics on the wire" for what the last three are for.
  //
  // MIND THE PACKET BUDGET before adding a field here. The real limit is not this
  // buffer, it is RF69_MAX_DATA_LEN (61), and RFM69::sendFrame() *silently truncates*
  // past it — the node would log a perfectly good payload while the gateway logged a
  // JSON parse failure. Worst case is 58 bytes (d at the SEN0676's 40000 mm ceiling, v at
  // the divider's 6600 mV ceiling, r and i at 255, n at 65535), so there are 3 bytes of
  // headroom. A test in rate_of_rise/tests renders that worst case and checks it.
  //
  // The single-letter keys are what paid for r/n/i: the old names (distance_mm,
  // battery_mv, fast, diag) filled 57 of the 61 bytes on their own. `g` (was `diag`) marks
  // a cycle where the radar rail was deliberately held off, which is the one thing about
  // this firmware that cannot be inferred downstream -- a held cycle and a failed Modbus
  // read both publish a null distance.
  char payload[128];
  if (distance_mm >= 0) {
    snprintf(payload, sizeof(payload),
      "{\"d\":%ld,\"v\":%u,\"f\":%d,\"g\":%d,\"r\":%u,\"n\":%u,\"i\":%u}",
      (long)distance_mm, (unsigned)batt_mv, fastMode ? 1 : 0, diagHold ? 1 : 0,
      (unsigned)resetCause, (unsigned)cycleCount, (unsigned)radioInitFailures);
  } else {
    snprintf(payload, sizeof(payload),
      "{\"d\":null,\"v\":%u,\"f\":%d,\"g\":%d,\"r\":%u,\"n\":%u,\"i\":%u}",
      (unsigned)batt_mv, fastMode ? 1 : 0, diagHold ? 1 : 0,
      (unsigned)resetCause, (unsigned)cycleCount, (unsigned)radioInitFailures);
  }
  cycleCount++;                 // wraps; a drop back to 0 on the gateway side means a reset

  if (radioOk) {
    Serial.print(F("TX: "));
    Serial.println(payload);

    radio.send(GATEWAYID, payload, strlen(payload));
    radioInitFailures = 0;      // the count just went out on the wire; start the next one

    // Brief window to catch a wireless firmware push (see firmware/README.md, OTA section).
    // The node sleeps the rest of the cycle, so this piggybacks on the wake TX already
    // paid for above rather than costing a dedicated one. CheckForWirelessHEX() is a
    // no-op for any packet that isn't its "FLX?" handshake; if it is, it blocks here
    // for the full transfer and reboots into the bootloader on success. Don't break out
    // on the first receiveDone() — a stray non-handshake packet would close the window
    // before the real handshake had a chance to arrive.
    const uint32_t otaListenMs = fastMode ? OTA_LISTEN_FAST_MS : OTA_LISTEN_MS;
    uint32_t otaListenStart = millis();
    while (millis() - otaListenStart < otaListenMs) {
      if (radio.receiveDone()) {
        // A real handshake blocks in here for the whole transfer, far past 16 s. The
        // library times out a stalled transfer itself, so stand the watchdog down for it.
        wdtDisable();
        CheckForWirelessHEX(radio, flash, true);
        wdtEnable();
      }
    }
  }

  radio.sleep();

#ifdef BENCH_TEST
  delay(BENCH_TEST_INTERVAL_MS);
#else
  // Standby until next report. RTCZero alarm wakes the SAMD21 from
  // standby (~6 uA vs. delay()'s ~12 mA), in watchdog-sized naps.
  sleepSeconds(fastMode ? FAST_REPORT_INTERVAL_S : REPORT_INTERVAL_S);
#endif
}
