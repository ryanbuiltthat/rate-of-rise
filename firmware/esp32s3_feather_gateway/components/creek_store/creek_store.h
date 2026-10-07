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
  std::string body;  // record without its seq head; seq is stamped at write time
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
    // The card is the truth: seq continues from what is on it, never from RAM.
    this->last_seq_[s] = last;
    this->written_seq_[s] = last;
    ESP_LOGI(TAG, "%s stream: last seq %u (newest block %lld)%s", STREAM_NAMES[s],
             (unsigned) this->last_seq_[s], (long long) newest,
             this->needs_newline_[s] ? ", torn final line" : "");
  }

  // Bus mutex held.
  bool append_(const Pending &p) {
    const uint32_t seq = this->last_seq_[p.stream] + 1;
    const uint32_t block = creek_core::block_of(seq);
    File f = SD.open(creek_core::block_path(STREAM_NAMES[p.stream], block).c_str(), FILE_APPEND);
    if (!f) return false;
    bool ok = true;
    // A torn line from a power cut must not swallow the next record: end it first. Readers
    // skip it because it does not parse.
    if (this->needs_newline_[p.stream]) ok = f.print("\n") == 1;
    const std::string line = creek_core::with_seq(seq, p.body);
    ok = ok && f.print(line.c_str()) == line.size();
    f.close();
    if (!ok) {
      this->needs_newline_[p.stream] = true;
      return false;
    }
    this->needs_newline_[p.stream] = false;
    if ((int64_t) block > this->newest_block_[p.stream]) this->newest_block_[p.stream] = block;
    this->last_seq_[p.stream] = seq;
    this->written_seq_[p.stream] = seq;
    return true;
  }

  void enqueue_(uint8_t stream, std::string &&body) {
    if (this->queue_.size() >= QUEUE_MAX) {
      ESP_LOGW(TAG, "store queue full; dropping oldest %s record",
               STREAM_NAMES[this->queue_.front().stream]);
      this->queue_.pop_front();
    }
    this->queue_.push_back(Pending{stream, std::move(body)});
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
    this->enqueue_(NODE, creek_core::encode_node_body(now_ts_(), this->ts_src_(), rssi, f, mount,
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

  // last_seq_: last seq on the card, set when a record is written (main task only).
  // written_seq_: the same value, also read by the HTTP task.
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
