// Creek gateway v2 store. See __init__.py for the overview and store_core.h for the formats.
//
// Threads and the bus:
//  * The radio and the SD card share one SPI bus, guarded by rfm69_gateway's mutex.
//  * Node packets arrive through rfm69_gateway's hook, on the main task, with the mutex HELD.
//    They are encoded and queued there, never written there.
//  * loop() writes the queue to SD with a NON-BLOCKING take, as rfm69_gateway's loop does,
//    so an OTA transfer holding the bus for minutes never stalls the main task.
//  * HTTP requests run on the httpd task. Only /store/records takes the bus (3 s wait);
//    /store/status reads atomics and the mutex-guarded store id, so it never waits on it.
#pragma once

#include <atomic>
#include <cctype>
#include <cmath>
#include <deque>
#include <mutex>
#include <string>
#include <sys/time.h>
#include <vector>

#include <esp_random.h>

#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/components/i2c/i2c.h"
#include "esphome/components/json/json_util.h"
#include "esphome/components/number/number.h"
#include "esphome/components/rfm69_gateway/rfm69_gateway.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/components/time/real_time_clock.h"
#include "esphome/components/web_server_base/web_server_base.h"
#include "esphome/core/component.h"
#include "esphome/core/log.h"

#include <FS.h>
#include <SD.h>
#include <HTTPClient.h>
#include <WiFiClient.h>

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
static const size_t PAGE_MAX_BYTES = 32 * 1024;
static const uint32_t PAGE_MAX_LINES = 500;
static const TickType_t HTTP_BUS_WAIT = pdMS_TO_TICKS(3000);
static const size_t SEEK_RESOLUTION = 512;

enum StreamId : uint8_t { NODE = 0, ECOWITT = 1, STREAM_COUNT = 2 };
static const char *const STREAM_NAMES[STREAM_COUNT] = {"node", "ecowitt"};

class CreekStore;
inline void ecowitt_task_trampoline(void *arg);

struct Pending {
  uint8_t stream;
  std::string body;  // record without its seq head; seq is stamped at write time
};

class CreekStore : public Component, public i2c::I2CDevice, public AsyncWebHandler {
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
    if (!creek_core::civil_valid(c)) {
      ESP_LOGW(TAG, "PCF8523 registers out of range (%04d-%02u-%02u %02u:%02u:%02u); time unknown "
                    "until SNTP", c.year, c.month, c.day, c.hour, c.minute, c.second);
      return;
    }
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
    if (this->ever_ntp_ && millis() - this->last_ntp_ms_.load() < NTP_FRESH_MS) return "ntp";
    if (this->clock_set_) return "rtc";
    return "none";
  }

  static double now_ts_() {
    struct timeval tv {};
    gettimeofday(&tv, nullptr);
    return (double) tv.tv_sec + tv.tv_usec / 1e6;
  }

  // --- SD --------------------------------------------------------------------------------
  struct StreamState {
    uint32_t last{0};
    int64_t newest{-1};
    bool torn{false};
  };

  // Bus mutex held. Nothing is adopted unless both streams and the store id read cleanly: a
  // card that half-answers must not reset seq to 0 (the add-on would see the store go
  // backwards), so it counts as not mounted and the 30 s remount retries it.
  void mount_sd_() {
    this->sd_ok_ = false;
    if (!SD.begin(this->sd_cs_pin_, SPI, SD_FREQ_HZ)) {
      ESP_LOGE(TAG, "SD card did not mount (CS GPIO%u); live entities keep working, nothing is "
                    "being stored", this->sd_cs_pin_);
      return;
    }
    StreamState st[STREAM_COUNT];
    for (uint8_t s = 0; s < STREAM_COUNT; s++) {
      if (!this->load_stream_(s, st[s])) {
        ESP_LOGE(TAG, "SD card mounted but the %s stream could not be read; treating it as not "
                      "mounted", STREAM_NAMES[s]);
        return;
      }
    }
    std::string id;
    if (!this->load_store_id_(id)) {
      ESP_LOGE(TAG, "SD card mounted but /store_id.txt could not be read or created; treating "
                    "it as not mounted");
      return;
    }
    for (uint8_t s = 0; s < STREAM_COUNT; s++) {
      // The card is the truth: seq continues from what is on it, never from RAM.
      this->newest_block_[s] = st[s].newest;
      this->needs_newline_[s] = st[s].torn;
      this->last_seq_[s] = st[s].last;
      this->written_seq_[s] = st[s].last;
      ESP_LOGI(TAG, "%s stream: last seq %u (newest block %lld)%s", STREAM_NAMES[s],
               (unsigned) st[s].last, (long long) st[s].newest,
               st[s].torn ? ", torn final line" : "");
    }
    {
      std::lock_guard<std::mutex> lock(this->id_mutex_);
      this->store_id_ = id;
    }
    ESP_LOGI(TAG, "store id %s", id.c_str());
    this->sd_ok_ = true;
  }

  // Bus mutex held. The card's identity, so the add-on can tell a replaced or reformatted card
  // (seq restarts: re-read it from the start) from a store that merely reports a low seq.
  bool load_store_id_(std::string &id) {
    id.clear();
    File f = SD.open("/store_id.txt");
    if (f) {
      char buf[33]{};
      const size_t n = f.read((uint8_t *) buf, sizeof buf - 1);
      f.close();
      for (size_t i = 0; i < n; i++)
        if (std::isxdigit((unsigned char) buf[i])) id += buf[i];
      if (!id.empty()) return true;
    }
    char fresh[17];
    snprintf(fresh, sizeof fresh, "%08x%08x", (unsigned) esp_random(), (unsigned) esp_random());
    File w = SD.open("/store_id.txt", FILE_WRITE);
    if (!w) return false;
    const bool ok = w.print(fresh) == 16;
    w.close();
    if (!ok) return false;
    id = fresh;
    ESP_LOGI(TAG, "New card: created /store_id.txt");
    return true;
  }

  // Bus mutex held. Finds the newest block file and the last complete record in it, so seq
  // continues across reboots. False if the directory or the newest block will not open.
  bool load_stream_(uint8_t s, StreamState &out) {
    const std::string dir = std::string("/") + STREAM_NAMES[s];
    SD.mkdir(dir.c_str());
    File root = SD.open(dir.c_str());
    if (!root) return false;
    out = StreamState{};
    for (File e = root.openNextFile(); e; e = root.openNextFile()) {
      const auto b = creek_core::parse_block_name(e.name());
      if (b && (int64_t) *b > out.newest) out.newest = *b;
      e.close();
    }
    root.close();
    if (out.newest >= 0) {
      File f = SD.open(creek_core::block_path(STREAM_NAMES[s], (uint32_t) out.newest).c_str());
      if (!f) return false;
      const size_t size = f.size();
      const size_t from = size > TAIL_BYTES ? size - TAIL_BYTES : 0;
      std::string tail(size - from, '\0');
      f.seek(from);
      tail.resize(f.read((uint8_t *) &tail[0], tail.size()));
      f.close();
      out.torn = creek_core::tail_is_torn(tail);
      const auto seq = creek_core::last_seq_in_tail(tail);
      out.last = seq ? *seq : (out.newest > 0 ? (uint32_t) out.newest * creek_core::BLOCK - 1 : 0);
    }
    return true;
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
    if (this->free_space_ != nullptr && this->sd_ok_)
      this->free_space_->publish_state(this->free_mb_.load());
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

  // The GW3000B is polled on its own task: an HTTP GET can block for seconds, and the main
  // task is the one servicing the radio. This reads the console's local API, so HA's Ecowitt
  // webhook path is untouched.
  void setup_ecowitt_() {
    if (this->ecowitt_host_.empty()) return;
    if (xTaskCreate(ecowitt_task_trampoline, "ecowitt", 8192, this, 1, nullptr) != pdPASS)
      ESP_LOGE(TAG, "Could not start the Ecowitt poll task");
  }

  // Main task: hand the latest poll result to the store queue.
  void collect_ecowitt_() {
    creek_core::EcowittReading r;
    {
      std::lock_guard<std::mutex> lock(this->eco_mutex_);
      if (!this->eco_ready_) return;
      this->eco_ready_ = false;
      if (!this->eco_ok_) {
        this->ecowitt_failures_++;
        return;
      }
      r = this->eco_reading_;
    }
    this->enqueue_(ECOWITT, creek_core::encode_ecowitt_body(now_ts_(), this->ts_src_(), r));
  }

  bool poll_ecowitt_(const std::string &url, creek_core::EcowittReading &out) {
    WiFiClient client;
    HTTPClient http;
    http.setTimeout(5000);
    if (!http.begin(client, url.c_str())) return false;
    const int code = http.GET();
    if (code != 200) {
      http.end();
      ESP_LOGD(TAG, "Ecowitt poll: HTTP %d", code);
      return false;
    }
    const String body = http.getString();
    http.end();
    const std::string text(body.c_str());
    const bool parsed = json::parse_json(text, [&out](JsonObject root) -> bool {
      for (JsonObject item : root["common_list"].as<JsonArray>())
        creek_core::apply_ecowitt_item(out, item["id"] | "", item["val"] | "", item["unit"] | "");
      for (JsonObject item : root["rain"].as<JsonArray>())
        creek_core::apply_ecowitt_item(out, item["id"] | "", item["val"] | "", item["unit"] | "");
      for (JsonObject ch : root["ch_soil"].as<JsonArray>()) {
        const auto c = creek_core::leading_float(ch["channel"] | "");
        const auto h = creek_core::leading_float(ch["humidity"] | "");
        if (c && h) out.soil.emplace_back((int) *c, *h);
      }
      return true;
    });
    if (parsed && !creek_core::ecowitt_has_data(out)) {
      ESP_LOGD(TAG, "Ecowitt poll: HTTP 200 but no known fields");
      return false;
    }
    return parsed;
  }

 public:
  void ecowitt_task() {
    const std::string url = "http://" + this->ecowitt_host_ + "/get_livedata_info";
    for (;;) {
      creek_core::EcowittReading r;
      const bool ok = this->poll_ecowitt_(url, r);
      {
        std::lock_guard<std::mutex> lock(this->eco_mutex_);
        this->eco_reading_ = r;
        this->eco_ok_ = ok;
        this->eco_ready_ = true;
      }
      vTaskDelay(pdMS_TO_TICKS(this->ecowitt_interval_ms_));
    }
  }

 protected:
  void setup_http_() {
    web_server_base::global_web_server_base->init();
    web_server_base::global_web_server_base->add_handler(this);
  }

  // Any task, no bus: atomics, the constant device name and the mutex-guarded store id only.
  std::string status_json_() {
    std::string id;  // empty while the card is not mounted
    if (this->sd_ok_) {
      std::lock_guard<std::mutex> lock(this->id_mutex_);
      id = this->store_id_;
    }
    char buf[448];
    snprintf(buf, sizeof buf,
             "{\"store_schema\":1,\"device\":\"%s\",\"now\":%.0f,\"ts_src\":\"%s\","
             "\"sd_ok\":%s,\"store_id\":\"%s\",\"sd_free_mb\":%u,\"streams\":{"
             "\"node\":{\"first\":%u,\"last\":%u},\"ecowitt\":{\"first\":%u,\"last\":%u}}}",
             this->device_name_.c_str(), now_ts_(), this->ts_src_(),
             this->sd_ok_ ? "true" : "false", id.c_str(), (unsigned) this->free_mb_.load(),
             this->first_seq_(NODE), (unsigned) this->written_seq_[NODE],
             this->first_seq_(ECOWITT), (unsigned) this->written_seq_[ECOWITT]);
    return buf;
  }

  // The store never deletes, so the first record is seq 1 once anything has been written.
  unsigned first_seq_(uint8_t s) const { return this->written_seq_[s] > 0 ? 1 : 0; }

  // Bus mutex held. Byte offset at or before the first line whose seq > after: binary search
  // on byte offsets, resyncing to the next newline at each probe. A torn line counts as
  // "> after", which only makes the linear scan that follows start a little early.
  size_t offset_after_(File &f, uint32_t after) {
    size_t lo = 0, hi = f.size();
    while (hi - lo > SEEK_RESOLUTION) {
      const size_t mid = lo + (hi - lo) / 2;
      f.seek(mid);
      f.readStringUntil('\n');
      const size_t line_start = f.position();
      const String line = f.readStringUntil('\n');
      const auto seq = creek_core::parse_seq(line.c_str());
      if (seq && *seq <= after) lo = line_start;
      else hi = mid;
    }
    return lo;
  }

  // Bus mutex held.
  std::string read_page_(uint8_t s, uint32_t after, uint32_t limit) {
    std::string body;
    if (this->newest_block_[s] < 0) return body;
    uint32_t n = 0;
    bool first = true;
    for (uint32_t b = creek_core::block_of(after + 1);
         (int64_t) b <= this->newest_block_[s] && n < limit && body.size() < PAGE_MAX_BYTES; b++) {
      File f = SD.open(creek_core::block_path(STREAM_NAMES[s], b).c_str());
      if (!f) continue;
      if (first) {
        f.seek(this->offset_after_(f, after));
        first = false;
      }
      while (f.available() && n < limit && body.size() < PAGE_MAX_BYTES) {
        const String line = f.readStringUntil('\n');
        const auto seq = creek_core::parse_seq(line.c_str());
        if (!seq || *seq <= after) continue;
        body += line.c_str();
        body += '\n';
        n++;
      }
      f.close();
    }
    return body;
  }

 public:
  // Both run on the HTTP server task, not the main task. The ESP32 web_server_base is
  // web_server_idf, whose request API differs from ESPAsyncWebServer (url_to, get_header,
  // std::string params).
  bool canHandle(AsyncWebServerRequest *request) const override {
    char buf[AsyncWebServerRequest::URL_BUF_SIZE];
    return std::string(request->url_to(buf)).compare(0, 7, "/store/") == 0;
  }

  void handleRequest(AsyncWebServerRequest *request) override {
    const optional<std::string> auth = request->get_header("Authorization");
    const std::string expected = "Bearer " + this->token_;
    if (!auth.has_value() || *auth != expected) {
      request->send(401, "text/plain", "unauthorized");
      return;
    }
    char urlbuf[AsyncWebServerRequest::URL_BUF_SIZE];
    const std::string url(request->url_to(urlbuf));
    // Status reads only atomics and the mutex-guarded store id, never the card, so it answers
    // even while a node OTA push holds the bus.
    if (url == "/store/status") {
      const std::string body = this->status_json_();
      request->send(200, "application/json", body.c_str());
      return;
    }
    if (url != "/store/records") {
      request->send(404, "text/plain", "not found");
      return;
    }
    const AsyncWebParameter *sp = request->getParam("stream");
    const std::string name = sp != nullptr ? sp->value() : "";
    const int s = name == "node" ? NODE : name == "ecowitt" ? ECOWITT : -1;
    if (s < 0) {
      request->send(400, "text/plain", "unknown stream");
      return;
    }
    // The card shares the radio's bus. A node OTA push holds it for minutes, so give up after
    // 3 s rather than tie up the HTTP task; the add-on retries next pass.
    if (xSemaphoreTake(this->bus_, HTTP_BUS_WAIT) != pdTRUE) {
      request->send(503, "text/plain", "bus busy");
      return;
    }
    if (!this->sd_ok_) {
      xSemaphoreGive(this->bus_);
      request->send(503, "text/plain", "sd unavailable");
      return;
    }
    const AsyncWebParameter *ap = request->getParam("after");
    const uint32_t after = ap != nullptr ? (uint32_t) strtoul(ap->value().c_str(), nullptr, 10) : 0;
    const AsyncWebParameter *lp = request->getParam("limit");
    uint32_t limit = lp != nullptr ? (uint32_t) strtoul(lp->value().c_str(), nullptr, 10)
                                   : PAGE_MAX_LINES;
    if (limit == 0 || limit > PAGE_MAX_LINES) limit = PAGE_MAX_LINES;
    const std::string body = this->read_page_((uint8_t) s, after, limit);
    const uint32_t last = this->written_seq_[s];
    xSemaphoreGive(this->bus_);
    AsyncWebServerResponse *resp = request->beginResponse(200, "application/x-ndjson", body);
    // httpd_resp_set_hdr keeps the pointer, not a copy: this buffer must outlive send().
    char last_buf[12];
    snprintf(last_buf, sizeof last_buf, "%u", (unsigned) last);
    resp->addHeader("X-Store-Last", last_buf);
    request->send(resp);
  }

 protected:
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

  // Atomic: written on the main task, read by /store/status on the HTTP task without the bus.
  std::atomic<bool> sd_ok_{false};
  std::atomic<bool> clock_set_{false};
  std::atomic<bool> ever_ntp_{false};
  std::atomic<uint32_t> last_ntp_ms_{0};
  std::atomic<uint32_t> free_mb_{0};
  uint32_t last_health_ms_{0};
  uint32_t last_free_ms_{0};
  uint32_t last_remount_ms_{0};
  uint32_t ecowitt_failures_{0};
  std::mutex eco_mutex_;
  std::mutex id_mutex_;  // store_id_: written on the main task, read on the HTTP task
  std::string store_id_;
  bool eco_ready_{false};
  bool eco_ok_{false};
  creek_core::EcowittReading eco_reading_;

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

inline void ecowitt_task_trampoline(void *arg) { static_cast<CreekStore *>(arg)->ecowitt_task(); }

}  // namespace creek_store
}  // namespace esphome
