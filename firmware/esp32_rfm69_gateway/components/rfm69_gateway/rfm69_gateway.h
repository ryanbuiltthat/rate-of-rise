// RFM69 -> Home Assistant gateway component.
//
// Receives packets from the Creek Moteino node over RFM69HW (915 MHz), decodes the
// node's JSON payload, and publishes the readings as native ESPHome entities. Home Assistant
// picks them up over the native API, so no MQTT broker sits in the data path.
//
// WIRING and pin constraints: see esp32_rfm69_gateway/gateway.yaml. In particular the
// SPI pins are remapped away from the XIAO's defaults because those collide with the
// ESP32-C3 boot straps.
#pragma once

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <utility>

#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/components/json/json_util.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/core/component.h"
#include "esphome/core/log.h"

#include <HTTPClient.h>
#include <RFM69.h>
#include <RFM69_OTA.h>
// RFM69_OTA.h defines LED unguarded (to LED_BUILTIN, else 13). esphome.h pulls this header in
// ahead of dozens of others, so retract the macro rather than rely on nothing downstream ever
// using that identifier. SHIFTCHANNEL is deliberately left defined -- it is asserted against
// OTA_SHIFT_HZ below.
#undef LED
#include <RFM69registers.h>  // REG_VERSION, for the init-failure probe below
#include <SPI.h>
#include <WiFiClientSecure.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

namespace esphome {
namespace rfm69_gateway {

static const char *const TAG = "rfm69_gateway";

// How long a peak RSSI sample stays valid. A report is ~8 ms on the air at 55555 bps and
// handle_packet_() runs within a few ms of it, so this only has to outlive a single packet --
// short enough that the value published always belongs to the packet just received.
static const uint32_t RSSI_PEAK_WINDOW_MS = 100;

// Cadence for the noise-floor log line. Matches the node's report interval so each line pairs
// a floor with a packet from roughly the same period.
static const uint32_t RSSI_FLOOR_LOG_INTERVAL_MS = 60000;

// Upper bound on the decoded image. The node's application area is 254 KB, but the gateway has
// to hold this in heap alongside WiFi, the API and an open TLS session, so cap it well below
// that and fail loudly rather than exhausting the heap. Current image: ~45 KB.
static const size_t OTA_IMAGE_MAX_BYTES = 98304;  // 96 KB

// Chunk size for each radio record during the transfer -- matches the record size the node's
// bootloader expects (LowPowerLab's Intel-HEX-over-radio protocol carries up to 16 data bytes
// per record on this hardware).
static const size_t OTA_RECORD_BYTES = 16;

// How long a press stays armed waiting for the node's next report. The node reports every 60 s,
// so this tolerates several missed cycles rather than making the user press the button again.
static const uint32_t OTA_ARM_TIMEOUT_MS = 600000;

// RFM69_OTA shifts the carrier by this much for the data phase, keeping the base channel clear
// of the transfer. The node's HandleWirelessHEXDataWrapper() does the same shift; if the gateway
// does not match it, the handshake succeeds on the base channel and then every data packet is
// transmitted where the node is not listening.
static const uint32_t OTA_SHIFT_HZ = 1000000;
static_assert(OTA_SHIFT_HZ == SHIFTCHANNEL, "gateway channel shift must match RFM69_OTA's");

// Bounds how many sendWithRetry() attempts the handshake makes -- NOT how long loop() blocks.
// The while loop re-tests the deadline only *between* calls, and one sendWithRetry(retries=2)
// is three RFM69::send() attempts, each able to spin in CSMA for up to RF69_CSMA_LIMIT_MS
// (1000 ms, RFM69.h:164) and then busy-wait 30 ms for the ACK. The yield inside that CSMA spin
// is compiled in under #ifdef ESP8266 only, so on this target it never yields. 200 ms therefore
// admits exactly one attempt, whose worst case is ~3.1 s of main-task block -- well past the
// node's 1500 ms listen window (OTA_LISTEN_MS in moteino_creek_node/src/main.cpp). The typical
// case is a few ms: the channel is idle and the node answers on the first try. The worst case
// is real and cannot be bounded from here without forking the library.
static const uint32_t OTA_HANDSHAKE_TIMEOUT_MS = 200;
static const uint32_t OTA_RECORD_TIMEOUT_MS = 3000;
static const uint8_t OTA_ACK_TIMEOUT_MS = 30;
static const uint32_t OTA_EOF_TIMEOUT_MS = 2000;

// How long fetch_hex_() tolerates a completely silent HTTP stream -- nothing buffered, nothing
// newly arriving -- before deciding the response is finished. http.connected() is deliberately
// not what ends that loop: arduino-esp32's NetworkClient::connected() infers liveness from
// errno after a non-blocking recv() peek, and a clean EOF (recv() returning 0) does not set
// errno, so a connection the server has gracefully closed can still read as "connected"
// indefinitely -- and HTTPClient requests keep-alive by default, so the peer closing at all is
// not even guaranteed. A stream silent for this long is trusted instead, regardless of what
// connected() reports. 8 s is generous for a ~124 KB fetch over local WiFi from a CDN that
// normally finishes in a couple of seconds.
static const uint32_t OTA_FETCH_IDLE_TIMEOUT_MS = 8000;

enum class OtaState : uint8_t {
  IDLE,
  FETCHING,
  ARMED,
  HANDSHAKING,
  TRANSFERRING,
  DONE,
  FAILED,
};

// The transfer runs on its own FreeRTOS task; xTaskCreate() takes a plain function pointer, so
// this trampoline bridges to the member. Declared here and defined below the class, where
// Rfm69Gateway is complete.
class Rfm69Gateway;
void ota_task_trampoline(void *arg);
// Same reason, for the fetch phase -- see run_ota_fetch() for why it has its own task too.
void ota_fetch_task_trampoline(void *arg);

class Rfm69Gateway : public Component {
 public:
  Rfm69Gateway(uint8_t cs_pin, uint8_t sck_pin, uint8_t miso_pin, uint8_t mosi_pin,
               uint8_t irq_pin, uint8_t frequency, int8_t reset_pin, uint8_t node_id,
               uint8_t network_id, bool is_rfm69hw, std::string encryption_key)
      : radio_(cs_pin, irq_pin, is_rfm69hw),
        cs_pin_(cs_pin),
        sck_pin_(sck_pin),
        miso_pin_(miso_pin),
        mosi_pin_(mosi_pin),
        irq_pin_(irq_pin),
        frequency_(frequency),
        reset_pin_(reset_pin),
        node_id_(node_id),
        network_id_(network_id),
        is_rfm69hw_(is_rfm69hw),
        encryption_key_(std::move(encryption_key)) {}

  void set_node_timeout(uint32_t timeout_ms) { this->node_timeout_ms_ = timeout_ms; }
  void set_distance_sensor(sensor::Sensor *s) { this->distance_sensor_ = s; }
  void set_battery_sensor(sensor::Sensor *s) { this->battery_sensor_ = s; }
  void set_rssi_sensor(sensor::Sensor *s) { this->rssi_sensor_ = s; }
  void set_node_status_sensor(binary_sensor::BinarySensor *s) { this->node_status_sensor_ = s; }
  void set_ota_status_sensor(text_sensor::TextSensor *s) { this->ota_status_sensor_ = s; }
  void set_ota_hex_url(const std::string &url) { this->ota_hex_url_ = url; }

  void setup() override {
    this->radio_mutex_ = xSemaphoreCreateMutex();
    if (this->radio_mutex_ == nullptr) {
      ESP_LOGE(TAG, "Could not create radio mutex");
      this->mark_failed();
      return;
    }

    this->status_mutex_ = xSemaphoreCreateMutex();
    if (this->status_mutex_ == nullptr) {
      ESP_LOGE(TAG, "Could not create status mutex");
      this->mark_failed();
      return;
    }

    // Claim the SPI bus on our pins before RFM69::initialize() runs. That function calls
    // SPI.begin() with no arguments, which would bind the XIAO's default pins — and on the
    // ESP32-C3 those (GPIO8 SCK / GPIO9 MISO) are boot straps the radio must stay off of.
    // arduino-esp32's SPIClass::begin() returns early once the bus is started, so whoever
    // calls it first wins; going first here makes the remap stick without patching the library.
    // Pass -1 for SS rather than cs_pin: handing CS to the SPI peripheral makes it a hardware
    // chip select, but RFM69 drives CS manually with digitalWrite(). Letting both manage it
    // logs "IO N is not set as GPIO" at boot and leaves CS in the peripheral's hands.
    SPI.begin(this->sck_pin_, this->miso_pin_, this->mosi_pin_, -1);
    pinMode(this->cs_pin_, OUTPUT);
    digitalWrite(this->cs_pin_, HIGH);

    // Optional hardware reset, per SX1231H: hold RESET high >=100 us, release, wait 5 ms before
    // any SPI access. RESET is active HIGH, so the pin idles LOW afterwards.
    if (this->reset_pin_ >= 0) {
      pinMode(this->reset_pin_, OUTPUT);
      digitalWrite(this->reset_pin_, HIGH);
      delayMicroseconds(200);
      digitalWrite(this->reset_pin_, LOW);
      delay(10);
    }

    if (!this->radio_.initialize(this->frequency_, this->node_id_, this->network_id_)) {
      // initialize() has four distinct `return false` paths (unusable IRQ pin, two sync-value
      // readback timeouts, ModeReady timeout) and ESPHome surfaces them all as the same bare
      // "marked FAILED: unspecified". These three registers say which one tripped:
      //   REG_VERSION 0x00/0xFF -> radio never answered. Suspect MISO first (an open MISO
      //                            reads as 0xFF/0x00 crosstalk), then power, wiring, or
      //                            RST held high (RESET is active HIGH -- never tie to 3V3)
      //   REG_VERSION 0x24      -> SPI is fine. If OPMODE reads 0x00 (sleep) and MODEREADY
      //                            stays clear, the radio's oscillator never started and it
      //                            is stuck in sleep -- a dead module, not a config problem.
      const uint8_t version = this->radio_.readReg(REG_VERSION);
      const uint8_t opmode = this->radio_.readReg(REG_OPMODE);
      const uint8_t irqflags1 = this->radio_.readReg(REG_IRQFLAGS1);
      ESP_LOGE(TAG,
               "RFM69 init failed: REG_VERSION=0x%02X (expected 0x24) OPMODE=0x%02X "
               "IRQFLAGS1=0x%02X (MODEREADY=%s)",
               version, opmode, irqflags1,
               (irqflags1 & RF_IRQFLAGS1_MODEREADY) ? "set" : "clear");
      ESP_LOGE(TAG, "  pins cs=%u sck=%u miso=%u mosi=%u irq=%u", this->cs_pin_, this->sck_pin_,
               this->miso_pin_, this->mosi_pin_, this->irq_pin_);
      this->mark_failed();
      return;
    }
    if (this->is_rfm69hw_) {
      this->radio_.setHighPower(true);
    }
    // initialize() can pass on a disconnected MISO: a floating line capacitively echoes the
    // byte just clocked out on MOSI, so its 0xAA/0x55 sync-value readbacks "succeed" without
    // the radio taking part. REG_VERSION is a constant the chip alone can produce, so check it
    // before believing the radio is there.
    const uint8_t version = this->radio_.readReg(REG_VERSION);
    if (version != 0x24) {
      ESP_LOGE(TAG,
               "RFM69 reported ready but REG_VERSION=0x%02X (expected 0x24) -- the radio is "
               "not really answering. Check the MISO connection first.",
               version);
      this->mark_failed();
      return;
    }

    this->radio_.encrypt(this->encryption_key_.c_str());

    // Start the liveness window at boot so a node that never transmits still resolves to
    // offline after one timeout, rather than sitting unknown forever.
    this->last_packet_ms_ = millis();
    ESP_LOGI(TAG, "RFM69 ready on band %u, listening for creek node packets", this->frequency_);
    this->set_ota_status_(OtaState::IDLE, 0, "");
  }

  void loop() override {
    this->publish_ota_status_();
    // The fetch task signals completion by flipping ota_fetch_done_ and exits right after --
    // exchange() both reads and clears it atomically, so this only fires once, and its acquire
    // order is what makes reading ota_image_/ota_image_len_/ota_fetch_ok_ below safe without a
    // mutex: the fetch task's writes to all three happen-before its release store to this flag
    // (see run_ota_fetch()), so this acquire-load happens-after them too.
    if (this->ota_fetch_done_.exchange(false, std::memory_order_acquire)) {
      if (this->ota_fetch_ok_) {
        this->ota_armed_ = true;
        this->ota_deadline_ms_ = millis() + OTA_ARM_TIMEOUT_MS;
        this->set_ota_status_(OtaState::ARMED, 0, "");
        ESP_LOGI(TAG, "OTA armed; waiting for the node's next report");
      }
      // On failure fetch_hex_() has already published FAILED with a specific reason -- nothing
      // more to do here.
    }
    // Non-blocking on purpose. During an OTA push the transfer task holds this mutex for the
    // whole transfer; a blocking take here would stall the main task for that entire time and
    // defeat the reason the transfer runs on its own task at all. Skipping a few iterations of
    // RSSI sampling costs nothing -- the node is blocked inside CheckForWirelessHEX() then and
    // is not transmitting telemetry anyway.
    //
    // ota_active_ is checked before the mutex, not instead of it. Between loop() releasing the
    // mutex after the handshake and the task taking it, a loop iteration could otherwise win the
    // try-take and call receiveDone(), consuming the node's reply out from under the task.
    if (!this->ota_active_ && xSemaphoreTake(this->radio_mutex_, 0) == pdTRUE) {
      this->sample_rssi_peak_();
      if (this->radio_.receiveDone()) {
        this->handle_packet_();
        // The sender of the telemetry IS the node to flash, so capture SENDERID and transmit
        // back to that. node_id_ is NOT the node's address -- it is the gateway's own, handed
        // to radio_.initialize() as _address (gateway.base.yaml: `rfm69_node_id: "2"  # gateway
        // ID`). The creek node is NODEID 1 and sends to GATEWAYID 2, so `SENDERID == node_id_`
        // would be 1 == 2 and never fire, and transmitting to node_id_ would address the
        // gateway itself -- the node drops anything whose TARGETID is not its own _address.
        // Capturing the remote id off the triggering packet is what RFM69_OTA itself does
        // (`uint16_t remoteID = radio.SENDERID;` in CheckForWirelessHEX/HandleSerialHEXData).
        if (this->ota_armed_ && this->radio_.SENDERID != this->node_id_) {
          this->ota_peer_id_ = this->radio_.SENDERID;
          this->try_start_transfer_();
        }
      }
      xSemaphoreGive(this->radio_mutex_);
    }
    if (millis() - this->last_packet_ms_ > this->node_timeout_ms_) {
      this->publish_node_status_(false);
    }
    if (this->ota_armed_ && (int32_t)(millis() - this->ota_deadline_ms_) > 0) {
      this->ota_armed_ = false;
      this->free_ota_image_();
      ESP_LOGW(TAG, "OTA arm window expired without a handshake");
      this->set_ota_status_(OtaState::FAILED, 0, "timeout, node did not respond");
    }
  }

  float get_setup_priority() const override { return setup_priority::LATE; }

  // Public: called from a button.template on_press lambda in gateway.base.yaml.
  void start_ota_push() {
    // All three flags, not just ota_armed_/ota_active_. try_start_transfer_() clears ota_armed_
    // *before* it spawns the transfer task, so for the whole multi-minute transfer ota_armed_ is
    // false while the task is streaming ota_image_ over the radio; ota_fetching_ is false by
    // then too, but true for the whole fetch that precedes it. A second press slipping into any
    // of these three windows would re-enter fetch_hex_() and reassign ota_image_/ota_image_len_
    // out from under whichever task is still reading them, with no lock protecting either field
    // -- a data race on top of leaking whatever buffer that task was still using. The three
    // flags together span every window from the first press to the transfer's end.
    if (this->ota_fetching_ || this->ota_armed_ || this->ota_active_) {
      ESP_LOGW(TAG, "OTA push already in progress, ignoring");
      return;
    }
    ESP_LOGI(TAG, "OTA push requested, fetching %s", this->ota_hex_url_.c_str());
    this->set_ota_status_(OtaState::FETCHING, 0, "");
    this->publish_ota_status_();  // make "fetching" visible before the fetch task starts
    // ota_fetching_ has to be set before the task exists, same reasoning as ota_active_ in
    // try_start_transfer_() below: the task never sets it itself, so a failed xTaskCreate() has
    // to roll it back by hand or every future press would find it already "in progress" forever.
    this->ota_fetching_ = true;
    // 12 KB, larger than the transfer task's 8 KB (try_start_transfer_() below): this task's
    // call chain goes through a full TLS handshake (WiFiClientSecure/mbedTLS), which is more
    // stack-hungry than the transfer task's plain RFM69 SPI/radio calls. Not bench-verified
    // against a worst-case handshake, just sized with margin above the transfer task's figure.
    if (xTaskCreate(ota_fetch_task_trampoline, "rfm69_ota_fetch", 12288, this, 1, nullptr) !=
        pdPASS) {
      ESP_LOGE(TAG, "Could not create the OTA fetch task; aborting the push");
      this->ota_fetching_ = false;
      this->set_ota_status_(OtaState::FAILED, 0, "no memory for fetch task");
    }
  }

  // Runs on its own task -- see fetch_hex_() for why. Mirrors run_ota_transfer()'s shape: do the
  // work, then hand the outcome back to the main task rather than touching any ESPHome object or
  // the main-task-only ota_armed_/ota_deadline_ms_ from here.
  void run_ota_fetch() {
    const bool ok = this->fetch_hex_();
    this->ota_fetch_ok_ = ok;
    this->ota_fetching_ = false;
    // Release order: everything fetch_hex_() wrote (ota_image_/ota_image_len_ on success, or the
    // FAILED status via set_ota_status_() on failure) must be visible to loop() before it can
    // observe ota_fetch_done_ true. See loop()'s exchange() for the acquire side of this edge.
    this->ota_fetch_done_.store(true, std::memory_order_release);
  }

  // Runs on its own task. Holds the radio for the whole transfer so the main loop keeps
  // servicing WiFi, the API and the watchdog.
  void run_ota_transfer() {
    xSemaphoreTake(this->radio_mutex_, portMAX_DELAY);
    const bool ok = this->transfer_image_();
    this->radio_.receiveDone();  // re-arm RX on the base channel
    xSemaphoreGive(this->radio_mutex_);

    // The image is only needed for the duration of the transfer, on both outcomes -- free it
    // here rather than duplicating this call at every return point inside transfer_image_().
    this->free_ota_image_();

    // Success only. The failure case is deliberately silent here: every path on which
    // transfer_image_() returns false has already set FAILED with a reason that says which
    // failure it was, and a generic "transfer error" here would overwrite all of them. The
    // status sensor exists so someone can tell "this image can never be pushed by this
    // protocol" from "the radio timed out, try again" without opening the logs.
    if (ok) {
      ESP_LOGI(TAG, "OTA transfer complete; node should reboot into the new image");
      this->set_ota_status_(OtaState::DONE, 100, "");
    }
    this->ota_active_ = false;
  }

 protected:
  // The RFM69's RSSI register only reflects a packet while that packet is physically on the air.
  // The library samples it at PAYLOADREADY -- after reception has finished and the receiver is
  // already listening to an empty channel -- and because the init table runs DAGC continuously
  // in RX (REG_TESTDAGC = RF_DAGC_IMPROVED_LOWBETA0), the register has tracked back down to the
  // noise floor by then. That produced a flat -95..-103 dBm whether the radios were touching on
  // a bench or two floors apart, a span over which real path loss differs by 30-50 dB.
  //
  // A ~50-byte report occupies the air for roughly 8 ms at 55555 bps, so polling from loop() and
  // keeping the strongest reading from the last RSSI_PEAK_WINDOW_MS catches the packet itself.
  // Continuous DAGC is also why no RSSI_START trigger is needed here (see RFM69::readRSSI) --
  // this is a plain register read that never writes to the radio and never touches the FIFO, so
  // unlike a trigger-and-wait it cannot stall the CPU or disturb a reception in progress.
  void sample_rssi_peak_() {
    // A reading is only meaningful while the receiver is actually running; between a completed
    // receive and the next receiveBegin() the radio sits in standby.
    if (RFM69::_mode != RF69_MODE_RX) {
      return;
    }
    const int16_t rssi = this->radio_.readRSSI();
    const uint32_t now = millis();
    if (now - this->rssi_peak_ms_ > RSSI_PEAK_WINDOW_MS || rssi > this->rssi_peak_) {
      this->rssi_peak_ = rssi;
      this->rssi_peak_ms_ = now;
    }

    // Quietest reading of the interval: the ambient noise floor. Logged next to the last
    // packet's value because the comparison is what makes either number interpretable --
    // a packet only a dB or two above the floor means the poll never landed inside one
    // (or the signal really is that weak), whereas a clear gap means the peak is real.
    if (rssi < this->rssi_floor_) {
      this->rssi_floor_ = rssi;
    }
    if (now - this->rssi_floor_ms_ > RSSI_FLOOR_LOG_INTERVAL_MS) {
      ESP_LOGI(TAG, "RSSI: noise floor %d dBm, last packet %d dBm", this->rssi_floor_,
               this->rssi_last_packet_);
      this->rssi_floor_ = 0;  // 0 dBm is above any real reading, so the min-hold restarts
      this->rssi_floor_ms_ = now;
    }
  }

  void handle_packet_() {
    const int16_t rssi = this->rssi_peak_;
    this->rssi_last_packet_ = rssi;
    const uint8_t sender_id = this->radio_.SENDERID;

    // Copy the payload before ACKing — the RFM69 library reuses its receive buffer.
    uint8_t len = this->radio_.DATALEN;
    if (len > RF69_MAX_DATA_LEN) {
      len = RF69_MAX_DATA_LEN;
    }
    char payload[RF69_MAX_DATA_LEN + 1];
    memcpy(payload, (const void *) this->radio_.DATA, len);
    payload[len] = '\0';

    if (this->radio_.ACKRequested()) {
      this->radio_.sendACK();
    }

    ESP_LOGD(TAG, "RX [%u] RSSI=%d : %s", sender_id, rssi, payload);

    this->last_packet_ms_ = millis();
    this->publish_node_status_(true);
    if (this->rssi_sensor_ != nullptr) {
      this->rssi_sensor_->publish_state(rssi);
    }

    const bool parsed = json::parse_json(std::string(payload), [this](JsonObject root) -> bool {
      // The node sends "distance_mm": null when its Modbus read fails. Publish NAN for that
      // so the reading shows as unknown in HA instead of a plausible-looking zero.
      if (this->distance_sensor_ != nullptr) {
        auto distance = root["distance_mm"];
        this->distance_sensor_->publish_state(distance.isNull() ? NAN : distance.as<float>());
      }
      if (this->battery_sensor_ != nullptr) {
        auto battery = root["battery_mv"];
        this->battery_sensor_->publish_state(battery.isNull() ? NAN : battery.as<float>());
      }
      return true;
    });

    if (!parsed) {
      ESP_LOGW(TAG, "Could not parse packet from node %u: %s", sender_id, payload);
    }
  }

  // BinarySensor::publish_state() forwards every call, so track transitions here rather than
  // republishing "offline" on each loop iteration once the node goes quiet.
  void publish_node_status_(bool online) {
    if (this->node_status_sensor_ == nullptr) {
      return;
    }
    if (this->node_status_known_ && this->node_online_ == online) {
      return;
    }
    this->node_online_ = online;
    this->node_status_known_ = true;
    this->node_status_sensor_->publish_state(online);
  }

  // Callable from any task. Only ever touches the status fields under status_mutex_ -- never an
  // ESPHome object, because publishing from a non-main task is not safe.
  void set_ota_status_(OtaState state, uint8_t percent, const char *reason) {
    xSemaphoreTake(this->status_mutex_, portMAX_DELAY);
    this->ota_state_ = state;
    this->ota_percent_ = percent;
    if (reason != nullptr) {
      strncpy(this->ota_reason_, reason, sizeof(this->ota_reason_) - 1);
      this->ota_reason_[sizeof(this->ota_reason_) - 1] = '\0';
    }
    xSemaphoreGive(this->status_mutex_);
  }

  // Main task only. Publishes on change, so "transferring NN%" does not republish per record.
  void publish_ota_status_() {
    if (this->ota_status_sensor_ == nullptr || this->status_mutex_ == nullptr) {
      return;
    }
    OtaState state;
    uint8_t percent;
    char reason[sizeof(this->ota_reason_)];
    if (xSemaphoreTake(this->status_mutex_, 0) != pdTRUE) {
      return;
    }
    state = this->ota_state_;
    percent = this->ota_percent_;
    memcpy(reason, this->ota_reason_, sizeof(reason));
    xSemaphoreGive(this->status_mutex_);

    if (state == this->published_state_ && percent == this->published_percent_) {
      return;
    }
    this->published_state_ = state;
    this->published_percent_ = percent;

    char buf[80];
    switch (state) {
      case OtaState::IDLE: strcpy(buf, "idle"); break;
      case OtaState::FETCHING: strcpy(buf, "fetching"); break;
      case OtaState::ARMED: strcpy(buf, "armed, waiting for node"); break;
      case OtaState::HANDSHAKING: strcpy(buf, "handshaking"); break;
      case OtaState::TRANSFERRING: snprintf(buf, sizeof(buf), "transferring %u%%", percent); break;
      case OtaState::DONE: strcpy(buf, "done"); break;
      case OtaState::FAILED: snprintf(buf, sizeof(buf), "failed: %s", reason); break;
    }
    this->ota_status_sensor_->publish_state(buf);
  }

  // Called from every path that ends a push, armed or not, successful or not: a fetched image
  // has to be released exactly once between the successful fetch that allocated it and whichever
  // of arm-window expiry, a rejected handshake, a failed task spawn, or run_ota_transfer()'s
  // return brings the push to an end. Safe to call when nothing is held (idle is the common case).
  void free_ota_image_() {
    if (this->ota_image_ != nullptr) {
      free(this->ota_image_);
      this->ota_image_ = nullptr;
    }
    this->ota_image_len_ = 0;
  }

  // Runs on its own task (spawned by start_ota_push(), see run_ota_fetch()) -- it did not
  // always. The original design ran this inline on the main task on the theory that a ~150 KB
  // GET over local WiFi is only a couple of seconds and not worth a thread boundary. On real
  // hardware it is not that fast: http.GET() below does DNS + TCP connect + a full TLS
  // handshake with raw.githubusercontent.com as one blocking call with no yield point
  // HTTPClient exposes, and a live crash trace caught it taking long enough to starve loopTask's
  // ESP-IDF task watchdog (~4 s to the abort, against ESP-IDF's 5 s default) -- Core 0 stuck in
  // xQueueSemaphoreTake, then a hard reset, every single press. Newly created FreeRTOS tasks are
  // not subscribed to that watchdog by default (only loopTask is), which is exactly why
  // run_ota_transfer() already worked this way for the radio phase and why this phase needed
  // the same fix.
  //
  // The .hex file is ASCII text -- Intel HEX records, one per line -- but the node only ever
  // sees the binary payload underneath: ~45 KB against the ~124 KB of hex text. Decoding here,
  // as the response streams in, means only the ~45 KB ever has to be held anywhere, and it lands
  // in a plain heap buffer rather than a filesystem this target doesn't have room for. Everything
  // below through the data-byte append is the same per-record logic transfer_image_() used to
  // apply while walking the file; it moved here because decoding now happens once, during the
  // fetch, rather than being re-parsed record-by-record during the radio transfer.
  bool fetch_hex_() {
    WiFiClientSecure client;
    client.setInsecure();  // no cert pinning; the payload is validated per-record by the node
    HTTPClient http;
    if (!http.begin(client, this->ota_hex_url_.c_str())) {
      this->set_ota_status_(OtaState::FAILED, 0, "bad url");
      return false;
    }
    http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
    const int code = http.GET();
    if (code != HTTP_CODE_OK) {
      ESP_LOGE(TAG, "OTA fetch failed: HTTP %d", code);
      char reason[48];
      snprintf(reason, sizeof(reason), "fetch error http %d", code);
      this->set_ota_status_(OtaState::FAILED, 0, reason);
      http.end();
      return false;
    }

    // The decoded binary is always smaller than half the hex text -- every byte costs at least
    // two ASCII hex digits, plus per-line address/type/checksum/newline overhead -- so
    // getSize()/2 is a safe upper bound whenever the server reports a length. A chunked response
    // reports no length at all (getSize() < 0); OTA_IMAGE_MAX_BYTES is the fallback either way a
    // size could still exceed it.
    const int content_length = http.getSize();
    size_t buf_size = OTA_IMAGE_MAX_BYTES;
    if (content_length > 0) {
      buf_size = std::min((size_t) (content_length / 2), OTA_IMAGE_MAX_BYTES);
    }
    uint8_t *image = (uint8_t *) malloc(buf_size);
    if (image == nullptr) {
      ESP_LOGE(TAG, "OTA fetch: no memory for a %u-byte image buffer (free heap %u)",
               (unsigned) buf_size, (unsigned) ESP.getFreeHeap());
      this->set_ota_status_(OtaState::FAILED, 0, "no memory for image");
      http.end();
      return false;
    }

    NetworkClient &stream = http.getStream();
    size_t image_len = 0;
    // Total bytes consumed off the HTTP stream so far (each line's characters plus the '\n'
    // that ended the read) -- not the decoded byte count. This is what the completeness check
    // after the loop compares against content_length: an idle timeout that fires with this
    // short of what the server declared means the fetch was cut off, not finished.
    size_t raw_consumed = 0;
    bool ok = true;
    char line[128];
    uint32_t last_progress_ms = millis();
    while (millis() - last_progress_ms < OTA_FETCH_IDLE_TIMEOUT_MS) {
      const size_t n = stream.readBytesUntil('\n', line, sizeof(line) - 1);
      if (n == 0) {
        // No full line yet. If the stack has bytes buffered, more of the response is still
        // arriving -- reset the idle clock rather than let a single slow line trip the timeout.
        // If not, say nothing: the idle clock keeps running, and the loop condition above is
        // what ends it once OTA_FETCH_IDLE_TIMEOUT_MS of true silence has passed.
        if (stream.available() > 0) {
          last_progress_ms = millis();
        }
        continue;
      }
      last_progress_ms = millis();
      raw_consumed += n + 1;
      line[n] = '\0';

      // Strip a trailing \r (the file has CRLF line endings) and any trailing spaces.
      size_t trimmed = n;
      while (trimmed > 0 && (line[trimmed - 1] == '\r' || line[trimmed - 1] == ' ')) {
        line[--trimmed] = '\0';
      }
      if (trimmed < 11 || line[0] != ':') {
        continue;  // blank or malformed line
      }

      // Strip the Intel HEX ':' -- the protocol frames records without it.
      char *record = line + 1;
      const uint8_t record_len = (uint8_t) (trimmed - 1);

      // Filter on the record TYPE field (chars 6-7), not on data length alone. A real node
      // image ends with a type 03 start-address record carrying 4 bytes of entry point --
      // validateHEXData() happily returns 4 for it, so a length-only check would treat those
      // 4 bytes as if they were firmware. Types 02 and 04 are worse: they re-base the address
      // for everything after them, and this protocol writes strictly sequentially, so there is
      // no correct way to carry them -- abort rather than silently misplace the rest of the image.
      const uint8_t type = (uint8_t) (BYTEfromHEX(record[6], record[7]));
      if (type == 0x02 || type == 0x04) {
        ESP_LOGE(TAG, "Image uses extended addressing (record type %02X); cannot transfer", type);
        this->set_ota_status_(OtaState::FAILED, 0, "image uses extended addressing");
        ok = false;
        break;
      }
      if (type != 0x00) {
        continue;  // 01 EOF, 03/05 entry-point metadata: not firmware data
      }

      // Abort, never skip. Past the type filter above every remaining record is firmware data,
      // so each of validateHEXData()'s failure codes means the downloaded image is damaged: 255
      // for a non-hex character, 254 for a bad checksum, 253 for a length/header mismatch, and 0
      // for a record under the 12-char minimum -- and a genuine type-00 record always carries a
      // non-zero data length. Skipping a bad record and continuing would punch a silent hole in
      // the image, and nothing downstream would ever notice. This is the only integrity check
      // the image ever gets: the GET runs setInsecure(), so a corrupt or actively hostile
      // response is exactly what this is guarding against.
      //
      // The upper bound is send_buf[57]'s capacity in transfer_image_() (10-char worst-case
      // header + up to 47 data bytes fits comfortably below 57), not 253: validateHEXData() can
      // legitimately return up to 58 from a 127-char line, and this image arrives over that same
      // setInsecure() connection.
      const uint8_t data_len = validateHEXData(record, record_len);
      if (data_len == 0 || data_len > 47) {
        ESP_LOGE(TAG, "Bad data record: validateHEXData returned %u (want 1..47)", data_len);
        this->set_ota_status_(OtaState::FAILED, 0, "corrupt or oversized image record");
        ok = false;
        break;
      }

      // Bounds-check every append against the allocation before writing a single byte of it.
      // buf_size is only ever a safe *estimate* (Content-Length/2, or the hard cap when the
      // server didn't give one) -- a malicious or corrupt response can still claim a small body
      // and then send more, and this is the check that turns that into a clean failure instead
      // of a heap overrun.
      if (image_len + data_len > buf_size) {
        ESP_LOGE(TAG, "OTA image exceeds the %u-byte buffer; aborting", (unsigned) buf_size);
        this->set_ota_status_(OtaState::FAILED, 0, "no memory for image");
        ok = false;
        break;
      }
      // +8 skips the record's own header (2 length + 4 address + 2 type chars): only the data
      // bytes are kept, in seq order, exactly as the node will receive them.
      for (uint8_t i = 0; i < data_len; i++) {
        image[image_len++] = BYTEfromHEX(record[8 + i * 2], record[9 + i * 2]);
      }
    }
    http.end();

    if (!ok) {
      free(image);
      return false;
    }
    // A positive content_length is the number of bytes the server promised for this response;
    // falling short of it means the idle timeout above gave up on a stalled or dropped
    // connection mid-download, not that the file actually ended there. Flashing a node from a
    // silently truncated image is worse than failing loudly here -- the node's own EOF handshake
    // has no way to know the image was supposed to be longer (see firmware/README.md, "A push
    // blocks a second push," for the same failure shape on the radio side) -- so this counts as
    // no image at all rather than a partial one. The few bytes of slack are just for
    // raw_consumed's own accounting being an estimate (it assumes every line it reads ends in a
    // '\n', which is true for every real line but not for a would-be final read past the last
    // one); a handful of bytes is nowhere near enough to hide a connection that actually dropped.
    const bool truncated = content_length > 0 && raw_consumed + 8 < (size_t) content_length;
    if (image_len == 0 || truncated) {
      ESP_LOGE(TAG, "OTA fetch %s (%u bytes decoded, %u of %d bytes of response consumed)",
               truncated ? "stalled before the response finished" : "decoded no data",
               (unsigned) image_len, (unsigned) raw_consumed, content_length);
      this->set_ota_status_(OtaState::FAILED, 0, "empty image");
      free(image);
      return false;
    }

    this->ota_image_ = image;
    this->ota_image_len_ = image_len;
    // The byte count is how the bench test confirms the fetch actually decoded the image (it
    // will read ~44,984, not the ~123,744 of hex text this used to log), and the free-heap figure
    // is the number that says whether OTA_IMAGE_MAX_BYTES is realistic on this device.
    ESP_LOGI(TAG, "OTA image fetched and decoded: %u bytes (free heap %u)", (unsigned) image_len,
             (unsigned) ESP.getFreeHeap());
    return true;
  }

  // Sends FLX? (or FLX?EOF) until the node answers with an FLX? reply or the timeout expires.
  // Replaces RFM69_OTA's HandleSerialHandshake() to add the yield.
  bool send_flx_handshake_(bool is_eof, uint32_t timeout_ms) {
    const char *msg = is_eof ? "FLX?EOF" : "FLX?";
    const uint8_t msg_len = is_eof ? 7 : 4;
    const uint32_t start = millis();
    while (millis() - start < timeout_ms) {
      if (this->radio_.sendWithRetry(this->ota_peer_id_, msg, msg_len, 2, OTA_ACK_TIMEOUT_MS)) {
        if (this->radio_.DATALEN >= 6 &&
            memcmp((const void *) this->radio_.DATA, "FLX?", 4) == 0) {
          return true;
        }
      }
      vTaskDelay(1);
    }
    return false;
  }

  // Replaces RFM69_OTA's sendHEXPacket(): same protocol, but parses the ACK sequence by hand
  // instead of sscanf("%u") into a uint16_t, which corrupts the stack on this target.
  bool send_hex_record_(uint8_t *buf, uint8_t len, uint16_t seq) {
    const uint32_t start = millis();
    while (millis() - start < OTA_RECORD_TIMEOUT_MS) {
      if (this->radio_.sendWithRetry(this->ota_peer_id_, buf, len, 2, OTA_ACK_TIMEOUT_MS)) {
        const uint8_t ack_len = this->radio_.DATALEN;
        if (ack_len >= 8 && memcmp((const void *) this->radio_.DATA, "FLX:", 4) == 0 &&
            this->radio_.DATA[ack_len - 3] == ':' && this->radio_.DATA[ack_len - 2] == 'O' &&
            this->radio_.DATA[ack_len - 1] == 'K') {
          uint32_t acked = 0;
          bool valid = (ack_len - 3) > 4;
          for (uint8_t i = 4; i < ack_len - 3; i++) {
            const char c = (char) this->radio_.DATA[i];
            if (c < '0' || c > '9') {
              valid = false;
              break;
            }
            acked = acked * 10 + (uint32_t)(c - '0');
          }
          if (valid && acked == seq) {
            return true;
          }
        }
      }
      vTaskDelay(1);
    }
    ESP_LOGE(TAG, "No ACK for record %u", seq);
    return false;
  }

  bool transfer_image_() {
    // ota_image_len_ is always > 0 here: fetch_hex_() fails the whole push (never arms) on a
    // zero-length decode, so try_start_transfer_() never reaches this with an empty buffer.
    this->radio_.setFrequency(this->radio_.getFrequency() + OTA_SHIFT_HZ);

    bool ok = true;
    uint16_t seq = 0;
    uint8_t last_percent = 0;
    size_t offset = 0;

    while (offset < this->ota_image_len_) {
      // At the top of the body: with the hex parsing gone (it all happened during the fetch),
      // every iteration here does a radio round trip, but this yield stays for the same reason
      // it always did -- send_hex_record_()'s own vTaskDelay(1) is only reached on retry, so
      // without one here too a run of first-try successes could still starve the idle task the
      // watchdog feeds.
      vTaskDelay(1);

      // send_buf is 57 bytes; sprintf's worst case is a 10-char header ("FLX:65535:") plus a
      // 16-byte chunk (OTA_RECORD_BYTES), 26 bytes total -- well inside the buffer, no bounds
      // check needed here the way fetch_hex_()'s buffer append needed one.
      uint8_t send_buf[57];
      const uint8_t header_len = (uint8_t) sprintf((char *) send_buf, "FLX:%u:", seq);
      const size_t chunk = std::min(OTA_RECORD_BYTES, this->ota_image_len_ - offset);
      memcpy(send_buf + header_len, this->ota_image_ + offset, chunk);
      if (!this->send_hex_record_(send_buf, (uint8_t) (header_len + chunk), seq)) {
        // Name the record. Dying on record 3 and dying on record 2800 are different faults --
        // the first says the node stopped listening or was never in range, the second says the
        // link carried thousands of packets and then lost one, i.e. a collision or a node that
        // went back to sleep. That distinction is worth a snprintf.
        char reason[sizeof(this->ota_reason_)];
        snprintf(reason, sizeof(reason), "record %u not acknowledged", seq);
        this->set_ota_status_(OtaState::FAILED, 0, reason);
        ok = false;
        break;
      }
      seq++;
      offset += chunk;

      const uint8_t percent = (uint8_t) ((offset * 100) / this->ota_image_len_);
      if (percent != last_percent) {
        last_percent = percent;
        this->set_ota_status_(OtaState::TRANSFERRING, percent, "");
      }
    }

    if (ok) {
      ok = this->send_flx_handshake_(true, OTA_EOF_TIMEOUT_MS);
      if (!ok) {
        // Its own reason, because this is not a mid-transfer failure: the whole image reached
        // the node and only the final confirmation was lost. The node holds a complete image in
        // SPI flash but never got the EOF that makes it commit and reboot, so a retry starts the
        // whole transfer again rather than resuming.
        ESP_LOGE(TAG, "Node never acknowledged EOF");
        this->set_ota_status_(OtaState::FAILED, 0, "node never acknowledged EOF");
      }
    }

    this->radio_.setFrequency(this->radio_.getFrequency() - OTA_SHIFT_HZ);
    return ok;
  }

  // Called with radio_mutex_ held, immediately after a telemetry packet from the node -- the one
  // moment its listen window is open.
  void try_start_transfer_() {
    this->set_ota_status_(OtaState::HANDSHAKING, 0, "");
    if (!this->send_flx_handshake_(false, OTA_HANDSHAKE_TIMEOUT_MS)) {
      ESP_LOGW(TAG, "Node did not answer the handshake; staying armed for the next report");
      this->set_ota_status_(OtaState::ARMED, 0, "");
      return;
    }
    if (this->radio_.DATALEN >= 7 && this->radio_.DATA[4] == 'N') {
      // "FLX?NOK:NOFLASH" -- the node has no usable SPI flash chip. Retrying will not help.
      ESP_LOGE(TAG, "Node rejected the push: %.*s", this->radio_.DATALEN,
               (const char *) this->radio_.DATA);
      this->ota_armed_ = false;
      this->free_ota_image_();
      this->set_ota_status_(OtaState::FAILED, 0, "node rejected the image");
      return;
    }
    ESP_LOGI(TAG, "Handshake accepted; handing off to the transfer task");
    this->ota_armed_ = false;
    this->ota_active_ = true;
    this->set_ota_status_(OtaState::TRANSFERRING, 0, "");
    // ota_active_ has to be set before the task exists, because the task never sets it itself --
    // so it also has to be rolled back by hand when no task is created. An 8 KB stack allocation
    // can plausibly fail on an ESP32-C3 right after a TLS session. Left latched, loop()'s
    // !ota_active_ guard would short-circuit every iteration for the rest of the boot: no
    // receiveDone(), no telemetry, no RSSI, status frozen, and only a reboot to recover.
    // ota_armed_ is already false here and stays false -- a failure this deep is not worth
    // re-arming into, and the user can press the button again.
    if (xTaskCreate(ota_task_trampoline, "rfm69_ota", 8192, this, 1, nullptr) != pdPASS) {
      ESP_LOGE(TAG, "Could not create the OTA transfer task; aborting the push");
      this->ota_active_ = false;
      // The task is what normally frees ota_image_ (see run_ota_transfer()); with no task ever
      // created, nothing else will.
      this->free_ota_image_();
      this->set_ota_status_(OtaState::FAILED, 0, "no memory for transfer task");
    }
  }

  RFM69 radio_;
  uint8_t cs_pin_;
  uint8_t sck_pin_;
  uint8_t miso_pin_;
  uint8_t mosi_pin_;
  uint8_t irq_pin_;
  uint8_t frequency_;
  int8_t reset_pin_;
  uint8_t node_id_;
  uint8_t network_id_;
  bool is_rfm69hw_;
  std::string encryption_key_;

  sensor::Sensor *distance_sensor_{nullptr};
  sensor::Sensor *battery_sensor_{nullptr};
  sensor::Sensor *rssi_sensor_{nullptr};
  binary_sensor::BinarySensor *node_status_sensor_{nullptr};
  text_sensor::TextSensor *ota_status_sensor_{nullptr};

  int16_t rssi_peak_{-127};
  uint32_t rssi_peak_ms_{0};
  int16_t rssi_floor_{0};
  uint32_t rssi_floor_ms_{0};
  int16_t rssi_last_packet_{0};
  uint32_t node_timeout_ms_{300000};
  uint32_t last_packet_ms_{0};
  bool node_online_{false};
  bool node_status_known_{false};
  SemaphoreHandle_t radio_mutex_{nullptr};

  SemaphoreHandle_t status_mutex_{nullptr};
  OtaState ota_state_{OtaState::IDLE};
  uint8_t ota_percent_{0};
  char ota_reason_[48]{};
  OtaState published_state_{OtaState::FAILED};  // != IDLE, so the first publish always fires
  uint8_t published_percent_{255};

  std::string ota_hex_url_;
  // ota_armed_ stays a plain bool: it is only ever touched from the main task -- run_ota_fetch()
  // does not set it directly; loop() does, after observing ota_fetch_done_ (see loop()).
  bool ota_armed_{false};
  uint32_t ota_deadline_ms_{0};

  // Written by the main task (start_ota_push(), true) and the fetch task (run_ota_fetch(),
  // false right before it exits) -- std::atomic for the same reason as ota_active_ below: a
  // plain bool gives the compiler no reason not to reorder or cache a write across that
  // cross-task boundary.
  std::atomic<bool> ota_fetching_{false};
  // Sequences the handoff of ota_image_/ota_image_len_/ota_fetch_ok_ from the fetch task back to
  // the main task -- see run_ota_fetch() (release store) and loop() (acquire exchange).
  std::atomic<bool> ota_fetch_done_{false};
  // Valid only once ota_fetch_done_ has been observed true; the same release/acquire pair that
  // guards ota_image_/ota_image_len_ covers this too, so it does not need to be atomic itself.
  bool ota_fetch_ok_{false};

  // The decoded firmware image. The .hex is ~124 KB of ASCII; the payload the node actually
  // receives is the ~45 KB of binary underneath it, so decode during the fetch and hold only
  // that. Lives from a successful fetch until the transfer ends or the arm window expires --
  // free_ota_image_() is the only thing allowed to release it, and every path that ends a push
  // calls it exactly once. Written by the fetch task (fetch_hex_(), inside run_ota_fetch())
  // before its release store to ota_fetch_done_, and by the main task (every free_ota_image_()
  // call) only after that store has been observed or before the fetch task exists -- so nothing
  // ever touches these two fields from both tasks at once. See loop() and run_ota_fetch() for
  // the happens-before edge between the fetch and transfer phases.
  uint8_t *ota_image_{nullptr};
  size_t ota_image_len_{0};

  // The node's radio address, captured from the telemetry packet that opened the listen window
  // (see loop()). Written by the main task before the transfer task exists and read by that task
  // afterwards; the radio_mutex_ give/take pair between the two is the happens-before edge, so
  // no atomic is needed. 0 until the first packet arrives, and nothing transmits before then.
  uint16_t ota_peer_id_{0};

  // Written by the transfer task, read by the main loop. std::atomic rather than volatile --
  // volatile orders nothing, and this flag is what keeps the two tasks off each other's radio.
  std::atomic<bool> ota_active_{false};
};

inline void ota_task_trampoline(void *arg) {
  static_cast<Rfm69Gateway *>(arg)->run_ota_transfer();
  vTaskDelete(nullptr);
}

inline void ota_fetch_task_trampoline(void *arg) {
  static_cast<Rfm69Gateway *>(arg)->run_ota_fetch();
  vTaskDelete(nullptr);
}

}  // namespace rfm69_gateway
}  // namespace esphome
