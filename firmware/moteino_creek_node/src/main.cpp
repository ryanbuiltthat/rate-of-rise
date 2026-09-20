// Creek sensor node — Moteino M0 + RFM69HW + DFRobot SEN0676.
//
// Reads water level via Modbus RTU from the SEN0676 80 GHz radar, packs a
// JSON payload, and transmits over RFM69HW (915 MHz) to the ESP32 gateway.
// Sleeps between cycles to keep average draw ~25 mA on solar/LiPo, reporting
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
//   - LowPower     (LowPowerLab — github.com/LowPowerLab/LowPower)
//   - RTCZero      (Arduino — RTC alarm for timed wakeup from standby)
//
// Confirmed on hardware 2026-09-07 (see firmware/README.md, Bench-Test Procedure
// step 1). BENCH_TEST is disabled below for enclosure/pole install — the node now
// sleeps between reports instead of running the bench cadence.

#include <Arduino.h>
#include <RFM69.h>
#include <RFM69_ATC.h>
#include <RFM69_OTA.h>
#include <SPIFlash.h>
#include <LowPower.h>
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
#define SENSOR_SETTLE_MS    500       // time after powering the radar before polling
#define MODBUS_TIMEOUT_MS   1000
#define OTA_LISTEN_MS       1500      // post-TX window to catch a wireless firmware push

// ─── Adaptive crest sampling (open question #15) ─────────────────────────────
// Spec §1: rainfall-to-crest here is tens of minutes, so a fixed 60 s cadence samples
// the crest of a flashy rise about as coarsely as it can be sampled and still be called
// a measurement. Drop to FAST_REPORT_INTERVAL_S once the creek is actually rising, and
// go back to 60 s when it isn't — the battery cost lands only during a rise, which is
// exactly when spending it is correct.
//
// This works at all because LowPower.standby() is a WFI, not a reset: loop() resumes
// where it left off and the statics below survive every sleep. An ESP32 deep-sleep node
// would need RTC_DATA_ATTR or flash to carry the same state across a wake.
#define FAST_REPORT_INTERVAL_S   5
// The radar reports "empty height" (sensor face → water), so a rise makes the distance
// *shrink*; the rate below is computed as (previous − current) to come out positive on
// a rise. 0.02 in/min = 0.508 mm/min. That number is not arbitrary: it sits deliberately
// below the add-on's 0.05 in/min Warning trigger (tiers.py WARNING_RATE_OF_RISE_IN_MIN)
// so the node is already sampling fast *before* a Warning is plausible. If either value
// ever moves, preserve that ordering — the retired ESPHome node enforced it with a test
// that was lost in the Moteino port.
#define RISE_THRESHOLD_MM_MIN    0.5f
// The SEN0676 is ±5 mm, so a single pair of samples cannot resolve 0.5 mm/min on its
// own — noise alone can manufacture one. Requiring consecutive qualifying samples is
// what makes the trigger mean "rising" rather than "jittering", and mirrors the add-on's
// own WARNING_RATE_OF_RISE_CONFIRM_SAMPLES guard on the same quantity.
#define RISE_CONFIRM_SAMPLES     2
// Hysteresis on the way out: a plateau part-way up a real rise shouldn't drop the
// cadence back to 60 s just before the crest. At the fast interval this is ~50 s of
// quiet before reverting.
#define FAST_MODE_HOLD_SAMPLES   10
// Don't manufacture a rate across a gap this long — a missed cycle or two is a real
// interval and fine to measure over, but anything beyond this is a cold start.
#define MAX_RATE_GAP_S           300
// Each wake already costs ~0.5 s of sensor settle plus the Modbus read before the
// OTA_LISTEN_MS window, so at a 5 s sleep the full 1.5 s window would make the real
// period ~7 s and hold the node awake ~30 % of it. Shortening the window in fast mode
// buys that back. It is shortened rather than skipped so a push is still *possible*
// mid-rise: the gateway answers the telemetry packet inline and typically handshakes
// within a few ms (firmware/README.md, "Why arming waits"), which this still covers.
#define OTA_LISTEN_FAST_MS       300

#define SECONDS_PER_DAY          86400L

// ─── Bench testing ───────────────────────────────────────────────────────────
// On USB/bench power: skip LowPower.standby() entirely so the board stays
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
static int32_t lastSampleSod  = -1;
static uint8_t risingSamples  = 0;
static uint8_t quietSamples   = 0;
static bool    fastMode       = false;

// RTC seconds-of-day. This is the only usable time base across a sleep: the RTC is what
// wakes the MCU, while millis()' clock is gated off during standby and so under-counts
// every cycle by however long the node was asleep.
static int32_t secondsOfDay() {
  return (int32_t)rtc.getHours() * 3600L
       + (int32_t)rtc.getMinutes() * 60L
       + (int32_t)rtc.getSeconds();
}

// Enter SAMD21 standby for the given number of seconds using an RTC alarm.
// LowPower.standby() sets the SLEEPDEEP bit and executes WFI; the RTC match
// interrupt is what wakes it back up.
static void sleepSeconds(uint16_t seconds) {
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
  // un-slept cycle rather than a lost day. The margin being skipped here shrinks as the
  // interval does, which is why this matters at FAST_REPORT_INTERVAL_S and did not at a
  // fixed 60 s.
  int32_t remaining = target - secondsOfDay();
  if (remaining < 0) remaining += SECONDS_PER_DAY;
  if (remaining > 0 && remaining <= (int32_t)seconds) {
    LowPower.standby();
  } else {
    Serial.println(F("RTC alarm already passed; skipping standby this cycle"));
  }

  rtc.disableAlarm();
}

// ─── RFM69 reset & init ──────────────────────────────────────────────────────
// RFM69::setMode() spins with no timeout while the radio is leaving sleep, so a
// radio that stops answering MODEREADY after a standby hangs the MCU until the
// reset button is pressed. Hardware-reset the radio on every wake and rebuild its
// config from scratch instead: initialize() has a 50 ms SPI handshake timeout, so
// a bad wake costs one report rather than the node.
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

// ─── Sampling cadence ────────────────────────────────────────────────────────
// Entering fast mode takes RISE_CONFIRM_SAMPLES consecutive qualifying samples; leaving
// it takes FAST_MODE_HOLD_SAMPLES quiet ones. The two are deliberately unequal — see
// their definitions for why sensor noise argues for the first and the shape of a real
// crest argues for the second.
static void updateSampleMode(float rise_mm_min) {
  if (rise_mm_min >= RISE_THRESHOLD_MM_MIN) {
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

// Fold a fresh reading into the cadence decision. A rise shrinks the radar's reported
// distance, so the delta is (previous - current) to come out positive on the way up.
static void updateRateOfRise(int32_t distance_mm) {
  if (distance_mm < 0) return;        // failed read: no reading, no rate, no state change

  const int32_t nowSod = secondsOfDay();
  if (lastDistanceMm >= 0) {
    int32_t elapsed_s = nowSod - lastSampleSod;
    if (elapsed_s < 0) elapsed_s += SECONDS_PER_DAY;    // wrapped past midnight
    if (elapsed_s > 0 && elapsed_s <= MAX_RATE_GAP_S) {
      updateSampleMode((float)(lastDistanceMm - distance_mm) * 60.0f / (float)elapsed_s);
    }
  }

  lastDistanceMm = distance_mm;
  lastSampleSod  = nowSod;
}

// ─── Battery voltage via Moteino M0 onboard VIN divider ─────────────────────
// The Moteino M0 has a built-in 50% voltage divider on A5 connected to VIN.
// Formula from LowPowerLab: vin = analogRead(A5) * 2 * (3.3 / 1023)
// Returns millivolts for integer JSON payload.
static uint16_t readBatteryMv() {
  float vin = analogRead(A5) * 2.0f * 0.003226f;  // 0.003226 = 3.3 / 1023
  return (uint16_t)(vin * 1000.0f);
}

void setup() {
  Serial.begin(115200);         // USB debug

  // SAMD21 native USB drops off the bus during LowPower.standby() (that
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
  resetRadioPin();
  if (!radioInit()) Serial.println(F("RFM69 init failed; retrying on next wake"));
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
  resetRadioPin();
  bool radioOk = radioInit();
  if (!radioOk) Serial.println(F("RFM69 init failed; skipping TX this cycle"));

  // Power up the radar
  digitalWrite(SENSOR_EN_PIN, HIGH);
  delay(SENSOR_SETTLE_MS);

  int32_t distance_mm = modbusReadHolding(SENSOR_ADDR, REG_DISTANCE);

  // Power down the radar
  digitalWrite(SENSOR_EN_PIN, LOW);

  uint16_t batt_mv = readBatteryMv();

  // Decide this cycle's cadence before transmitting, so the payload and the OTA window
  // below both reflect it. Pure arithmetic, and guarded against a zero elapsed time —
  // nothing here can keep the node from reaching its TX.
  updateRateOfRise(distance_mm);

  // Build JSON payload. `fast` is not read by the gateway today (it looks up
  // distance_mm/battery_mv by key and ignores the rest), but the node is the expensive
  // side to change — putting it on the wire now means surfacing "was the node
  // fast-sampling during that storm?" later is a gateway-only change rather than
  // another OTA push.
  //
  // MIND THE PACKET BUDGET before adding a field here. The real limit is not this
  // buffer, it is RF69_MAX_DATA_LEN (61), and RFM69::sendFrame() *silently truncates*
  // past it — the node would log a perfectly good payload while the gateway logged a
  // JSON parse failure. Worst case today is 57 bytes (distance_mm at the SEN0676's
  // 40000 mm ceiling, battery_mv at the divider's 6600 mV ceiling), so there are 4
  // bytes of headroom. Anything longer needs shorter keys, not a bigger buffer.
  char payload[128];
  if (distance_mm >= 0) {
    snprintf(payload, sizeof(payload),
      "{\"node\":%d,\"distance_mm\":%ld,\"battery_mv\":%u,\"fast\":%d}",
      NODEID, (long)distance_mm, batt_mv, fastMode ? 1 : 0);
  } else {
    snprintf(payload, sizeof(payload),
      "{\"node\":%d,\"distance_mm\":null,\"battery_mv\":%u,\"fast\":%d}",
      NODEID, batt_mv, fastMode ? 1 : 0);
  }

  if (radioOk) {
    Serial.print(F("TX: "));
    Serial.println(payload);

    radio.send(GATEWAYID, payload, strlen(payload));

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
        CheckForWirelessHEX(radio, flash, true);
      }
    }
  }

  radio.sleep();

#ifdef BENCH_TEST
  delay(BENCH_TEST_INTERVAL_MS);
#else
  // Standby until next report. RTCZero alarm wakes the SAMD21 from
  // LowPower.standby() (~6 uA vs. delay()'s ~12 mA).
  sleepSeconds(fastMode ? FAST_REPORT_INTERVAL_S : REPORT_INTERVAL_S);
#endif
}
