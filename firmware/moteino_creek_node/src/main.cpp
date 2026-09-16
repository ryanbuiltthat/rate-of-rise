// Creek sensor node — Moteino M0 + RFM69HW + DFRobot SEN0676.
//
// Reads water level via Modbus RTU from the SEN0676 80 GHz radar, packs a
// JSON payload, and transmits over RFM69HW (915 MHz) to the ESP32 gateway.
// Sleeps between cycles to keep average draw ~25 mA on solar/LiPo. Listens
// briefly after each TX for a wireless firmware push (see firmware/README.md,
// OTA section) so future updates don't require pulling the node off the pole.
//
// WIRING (Moteino M0 / SAMD21):
//   SEN0676 TX  -> Serial1 RX (pin 0)
//   SEN0676 RX  -> Serial1 TX (pin 1)
//   SEN0676 VCC -> switched 5 V rail (SENSOR_EN_PIN)
//   SEN0676 GND -> GND
//   RFM69HW     -> onboard SPI (Moteino socket: SCK/MOSI/MISO/CS=D8/INT=D3)
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
//#define ENABLE_ATC    //comment out this line to disable AUTO TRANSMISSION CONTROLg
#define ATC_RSSI      -85

// ─── Pins ────────────────────────────────────────────────────────────────────
#define RFM69_CS      8
#define RFM69_INT     3
#define SENSOR_EN_PIN 4     // drives a MOSFET or boost-converter EN to power the SEN0676

// ─── Timing ──────────────────────────────────────────────────────────────────
#define REPORT_INTERVAL_S   60        // 60 s between reports (spec §2)
#define SENSOR_SETTLE_MS    500       // time after powering the radar before polling
#define MODBUS_TIMEOUT_MS   1000
#define OTA_LISTEN_MS       1500      // post-TX window to catch a wireless firmware push

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

//RFM69 radio(RFM69_CS, RFM69_INT, IS_RFM69HW);
#ifdef ENABLE_ATC
  RFM69_ATC radio;
#else
  RFM69 radio;
#endif
RTCZero rtc;
SPIFlash flash(SS_FLASHMEM, 0xEF30); //EF30 for 4mbit  Windbond chip (W25X40CL)

volatile bool rtcAlarmFired = false;
void rtcAlarmISR() { rtcAlarmFired = true; }

// Enter SAMD21 standby for the given number of seconds using an RTC alarm.
// LowPower.standby() sets the SLEEPDEEP bit and executes WFI; the RTC match
// interrupt is what wakes it back up.
static void sleepSeconds(uint16_t seconds) {
  uint8_t now_s  = rtc.getSeconds();
  uint8_t now_m  = rtc.getMinutes();
  uint8_t now_h  = rtc.getHours();

  // Add the offset, rolling over seconds → minutes → hours.
  uint16_t total_s = now_s + seconds;
  uint16_t total_m = now_m + total_s / 60;
  uint8_t  alarm_s = total_s % 60;
  uint8_t  alarm_m = (total_m) % 60;
  uint8_t  alarm_h = (now_h + total_m / 60) % 24;

  rtcAlarmFired = false;
  rtc.setAlarmTime(alarm_h, alarm_m, alarm_s);
  rtc.enableAlarm(rtc.MATCH_HHMMSS);

  LowPower.standby();

  rtc.disableAlarm();
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

  if (!radio.initialize(FREQUENCY, NODEID, NETWORKID)) {
    Serial.println(F("RFM69 init failed"));
    while (1);
  }
  #ifdef IS_RFM69HCW
    radio.setHighPower(); //must include this only for RFM69HW/HCW!
  #endif
  #ifdef ENCRYPT_KEY
    radio.encrypt(ENCRYPT_KEY);
  #endif

  #ifdef FREQUENCY_EXACT
    radio.setFrequency(FREQUENCY_EXACT); //set frequency to some custom frequency
  #endif

  //Auto Transmission Control - dials down transmit power to save battery (-100 is the noise floor, -90 is still pretty good)
  //For indoor nodes that are pretty static and at pretty stable temperatures (like a MotionMote) -90dBm is quite safe
  //For more variable nodes that can expect to move or experience larger temp drifts a lower margin like -70 to -80 would probably be better
  //Always test your ATC mote in the edge cases in your own environment to ensure ATC will perform as you expect
  #ifdef ENABLE_ATC
    radio.enableAutoPower(ATC_RSSI);
  #endif
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
  // Power up the radar
  digitalWrite(SENSOR_EN_PIN, HIGH);
  delay(SENSOR_SETTLE_MS);

  int32_t distance_mm = modbusReadHolding(SENSOR_ADDR, REG_DISTANCE);

  // Power down the radar
  digitalWrite(SENSOR_EN_PIN, LOW);

  uint16_t batt_mv = readBatteryMv();

  // Build JSON payload
  char payload[128];
  if (distance_mm >= 0) {
    snprintf(payload, sizeof(payload),
      "{\"node\":%d,\"distance_mm\":%ld,\"battery_mv\":%u}",
      NODEID, (long)distance_mm, batt_mv);
  } else {
    snprintf(payload, sizeof(payload),
      "{\"node\":%d,\"distance_mm\":null,\"battery_mv\":%u}",
      NODEID, batt_mv);
  }

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
  uint32_t otaListenStart = millis();
  while (millis() - otaListenStart < OTA_LISTEN_MS) {
    if (radio.receiveDone()) {
      CheckForWirelessHEX(radio, flash, true);
    }
  }

  radio.sleep();

#ifdef BENCH_TEST
  delay(BENCH_TEST_INTERVAL_MS);
#else
  // Standby until next report. RTCZero alarm wakes the SAMD21 from
  // LowPower.standby() (~6 uA vs. delay()'s ~12 mA).
  sleepSeconds(REPORT_INTERVAL_S);
#endif
}
