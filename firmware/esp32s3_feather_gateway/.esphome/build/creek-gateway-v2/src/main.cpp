// Auto generated code by esphome
// ========== AUTO GENERATED INCLUDE BLOCK BEGIN ===========
#include "esphome.h"
using namespace esphome;
alignas(logger::Logger) static unsigned char logger__logger_logger_id__pstorage[sizeof(logger::Logger)];
static logger::Logger *const logger_logger_id = reinterpret_cast<logger::Logger *>(logger__logger_logger_id__pstorage);
#ifndef __PICOLIBC__
using std::isnan;
#endif
using std::min;
using std::max;
#include <new>
using namespace time;
using namespace number;
using namespace sensor;
using namespace text_sensor;
using namespace button;
using namespace binary_sensor;
alignas(web_server_base::WebServerBase) static unsigned char web_server_base__web_server_base_webserverbase_id__pstorage[sizeof(web_server_base::WebServerBase)];
static web_server_base::WebServerBase *const web_server_base_webserverbase_id = reinterpret_cast<web_server_base::WebServerBase *>(web_server_base__web_server_base_webserverbase_id__pstorage);
alignas(captive_portal::CaptivePortal) static unsigned char captive_portal__captive_portal_captiveportal_id__pstorage[sizeof(captive_portal::CaptivePortal)];
static captive_portal::CaptivePortal *const captive_portal_captiveportal_id = reinterpret_cast<captive_portal::CaptivePortal *>(captive_portal__captive_portal_captiveportal_id__pstorage);
alignas(wifi::WiFiComponent) static unsigned char wifi__wifi_wificomponent_id__pstorage[sizeof(wifi::WiFiComponent)];
static wifi::WiFiComponent *const wifi_wificomponent_id = reinterpret_cast<wifi::WiFiComponent *>(wifi__wifi_wificomponent_id__pstorage);
alignas(mdns::MDNSComponent) static unsigned char mdns__mdns_mdnscomponent_id__pstorage[sizeof(mdns::MDNSComponent)];
static mdns::MDNSComponent *const mdns_mdnscomponent_id = reinterpret_cast<mdns::MDNSComponent *>(mdns__mdns_mdnscomponent_id__pstorage);
alignas(network::NetworkComponent) static unsigned char network__network_networkcomponent_id__pstorage[sizeof(network::NetworkComponent)];
static network::NetworkComponent *const network_networkcomponent_id = reinterpret_cast<network::NetworkComponent *>(network__network_networkcomponent_id__pstorage);
alignas(esphome::ESPHomeOTAComponent) static unsigned char esphome__esphome_esphomeotacomponent_id__pstorage[sizeof(esphome::ESPHomeOTAComponent)];
static esphome::ESPHomeOTAComponent *const esphome_esphomeotacomponent_id = reinterpret_cast<esphome::ESPHomeOTAComponent *>(esphome__esphome_esphomeotacomponent_id__pstorage);
alignas(web_server::WebServerOTAComponent) static unsigned char web_server__web_server_webserverotacomponent_id__pstorage[sizeof(web_server::WebServerOTAComponent)];
static web_server::WebServerOTAComponent *const web_server_webserverotacomponent_id = reinterpret_cast<web_server::WebServerOTAComponent *>(web_server__web_server_webserverotacomponent_id__pstorage);
alignas(preferences::IntervalSyncer) static unsigned char preferences__preferences_intervalsyncer_id__pstorage[sizeof(preferences::IntervalSyncer)];
static preferences::IntervalSyncer *const preferences_intervalsyncer_id = reinterpret_cast<preferences::IntervalSyncer *>(preferences__preferences_intervalsyncer_id__pstorage);
alignas(safe_mode::SafeModeComponent) static unsigned char safe_mode__safe_mode_safemodecomponent_id__pstorage[sizeof(safe_mode::SafeModeComponent)];
static safe_mode::SafeModeComponent *const safe_mode_safemodecomponent_id = reinterpret_cast<safe_mode::SafeModeComponent *>(safe_mode__safe_mode_safemodecomponent_id__pstorage);
alignas(api::APIServer) static unsigned char api__api_apiserver_id__pstorage[sizeof(api::APIServer)];
static api::APIServer *const api_apiserver_id = reinterpret_cast<api::APIServer *>(api__api_apiserver_id__pstorage);
using namespace api;
alignas(StartupTrigger) static unsigned char esphome__startuptrigger_id__pstorage[sizeof(StartupTrigger)];
static StartupTrigger *const startuptrigger_id = reinterpret_cast<StartupTrigger *>(esphome__startuptrigger_id__pstorage);
alignas(Automation<>) static unsigned char esphome__automation_id__pstorage[sizeof(Automation<>)];
static Automation<> *const automation_id = reinterpret_cast<Automation<> *>(esphome__automation_id__pstorage);
alignas(StatelessLambdaAction<>) static unsigned char esphome__lambdaaction_id__pstorage[sizeof(StatelessLambdaAction<>)];
static StatelessLambdaAction<> *const lambdaaction_id = reinterpret_cast<StatelessLambdaAction<> *>(esphome__lambdaaction_id__pstorage);
using namespace i2c;
alignas(i2c::IDFI2CBus) static unsigned char i2c__i2c_bus__pstorage[sizeof(i2c::IDFI2CBus)];
static i2c::IDFI2CBus *const i2c_bus = reinterpret_cast<i2c::IDFI2CBus *>(i2c__i2c_bus__pstorage);
using namespace json;
alignas(sntp::SNTPComponent) static unsigned char sntp__sntp_time__pstorage[sizeof(sntp::SNTPComponent)];
static sntp::SNTPComponent *const sntp_time = reinterpret_cast<sntp::SNTPComponent *>(sntp__sntp_time__pstorage);
alignas(rfm69_gateway::Rfm69Gateway) static unsigned char rfm69_gateway__creek_radio__pstorage[sizeof(rfm69_gateway::Rfm69Gateway)];
static rfm69_gateway::Rfm69Gateway *const creek_radio = reinterpret_cast<rfm69_gateway::Rfm69Gateway *>(rfm69_gateway__creek_radio__pstorage);
alignas(sensor::Sensor) static unsigned char sensor__creek_distance_mm__pstorage[sizeof(sensor::Sensor)];
static sensor::Sensor *const creek_distance_mm = reinterpret_cast<sensor::Sensor *>(sensor__creek_distance_mm__pstorage);
alignas(sensor::Sensor) static unsigned char sensor__sensor_sensor_id__pstorage[sizeof(sensor::Sensor)];
static sensor::Sensor *const sensor_sensor_id = reinterpret_cast<sensor::Sensor *>(sensor__sensor_sensor_id__pstorage);
alignas(sensor::Sensor) static unsigned char sensor__sensor_sensor_id_2__pstorage[sizeof(sensor::Sensor)];
static sensor::Sensor *const sensor_sensor_id_2 = reinterpret_cast<sensor::Sensor *>(sensor__sensor_sensor_id_2__pstorage);
alignas(sensor::Sensor) static unsigned char sensor__sensor_sensor_id_3__pstorage[sizeof(sensor::Sensor)];
static sensor::Sensor *const sensor_sensor_id_3 = reinterpret_cast<sensor::Sensor *>(sensor__sensor_sensor_id_3__pstorage);
alignas(binary_sensor::BinarySensor) static unsigned char binary_sensor__binary_sensor_binarysensor_id__pstorage[sizeof(binary_sensor::BinarySensor)];
static binary_sensor::BinarySensor *const binary_sensor_binarysensor_id = reinterpret_cast<binary_sensor::BinarySensor *>(binary_sensor__binary_sensor_binarysensor_id__pstorage);
alignas(binary_sensor::BinarySensor) static unsigned char binary_sensor__binary_sensor_binarysensor_id_2__pstorage[sizeof(binary_sensor::BinarySensor)];
static binary_sensor::BinarySensor *const binary_sensor_binarysensor_id_2 = reinterpret_cast<binary_sensor::BinarySensor *>(binary_sensor__binary_sensor_binarysensor_id_2__pstorage);
alignas(binary_sensor::BinarySensor) static unsigned char binary_sensor__binary_sensor_binarysensor_id_3__pstorage[sizeof(binary_sensor::BinarySensor)];
static binary_sensor::BinarySensor *const binary_sensor_binarysensor_id_3 = reinterpret_cast<binary_sensor::BinarySensor *>(binary_sensor__binary_sensor_binarysensor_id_3__pstorage);
alignas(sensor::Sensor) static unsigned char sensor__sensor_sensor_id_4__pstorage[sizeof(sensor::Sensor)];
static sensor::Sensor *const sensor_sensor_id_4 = reinterpret_cast<sensor::Sensor *>(sensor__sensor_sensor_id_4__pstorage);
alignas(binary_sensor::BinarySensor) static unsigned char binary_sensor__binary_sensor_binarysensor_id_4__pstorage[sizeof(binary_sensor::BinarySensor)];
static binary_sensor::BinarySensor *const binary_sensor_binarysensor_id_4 = reinterpret_cast<binary_sensor::BinarySensor *>(binary_sensor__binary_sensor_binarysensor_id_4__pstorage);
alignas(text_sensor::TextSensor) static unsigned char text_sensor__text_sensor_textsensor_id__pstorage[sizeof(text_sensor::TextSensor)];
static text_sensor::TextSensor *const text_sensor_textsensor_id = reinterpret_cast<text_sensor::TextSensor *>(text_sensor__text_sensor_textsensor_id__pstorage);
alignas(text_sensor::TextSensor) static unsigned char text_sensor__text_sensor_textsensor_id_2__pstorage[sizeof(text_sensor::TextSensor)];
static text_sensor::TextSensor *const text_sensor_textsensor_id_2 = reinterpret_cast<text_sensor::TextSensor *>(text_sensor__text_sensor_textsensor_id_2__pstorage);
alignas(sensor::Sensor) static unsigned char sensor__sensor_sensor_id_5__pstorage[sizeof(sensor::Sensor)];
static sensor::Sensor *const sensor_sensor_id_5 = reinterpret_cast<sensor::Sensor *>(sensor__sensor_sensor_id_5__pstorage);
alignas(sensor::Sensor) static unsigned char sensor__sensor_sensor_id_6__pstorage[sizeof(sensor::Sensor)];
static sensor::Sensor *const sensor_sensor_id_6 = reinterpret_cast<sensor::Sensor *>(sensor__sensor_sensor_id_6__pstorage);
alignas(Automation<float>) static unsigned char esphome__automation_id_2__pstorage[sizeof(Automation<float>)];
static Automation<float> *const automation_id_2 = reinterpret_cast<Automation<float> *>(esphome__automation_id_2__pstorage);
alignas(creek_store::CreekStore) static unsigned char creek_store__store__pstorage[sizeof(creek_store::CreekStore)];
static creek_store::CreekStore *const store = reinterpret_cast<creek_store::CreekStore *>(creek_store__store__pstorage);
alignas(template_::TemplateNumber) static unsigned char template__mount_height_mm__pstorage[sizeof(template_::TemplateNumber)];
static template_::TemplateNumber *const mount_height_mm = reinterpret_cast<template_::TemplateNumber *>(template__mount_height_mm__pstorage);
alignas(template_::TemplateSensor) static unsigned char template__creek_stage__pstorage[sizeof(template_::TemplateSensor)];
static template_::TemplateSensor *const creek_stage = reinterpret_cast<template_::TemplateSensor *>(template__creek_stage__pstorage);
alignas(template_::TemplateSensor) static unsigned char template__creek_depth_in__pstorage[sizeof(template_::TemplateSensor)];
static template_::TemplateSensor *const creek_depth_in = reinterpret_cast<template_::TemplateSensor *>(template__creek_depth_in__pstorage);
alignas(wifi_signal::WiFiSignalSensor) static unsigned char wifi_signal__wifi_signal_wifisignalsensor_id__pstorage[sizeof(wifi_signal::WiFiSignalSensor)];
static wifi_signal::WiFiSignalSensor *const wifi_signal_wifisignalsensor_id = reinterpret_cast<wifi_signal::WiFiSignalSensor *>(wifi_signal__wifi_signal_wifisignalsensor_id__pstorage);
alignas(uptime::UptimeSecondsSensor) static unsigned char uptime__uptime_uptimesecondssensor_id__pstorage[sizeof(uptime::UptimeSecondsSensor)];
static uptime::UptimeSecondsSensor *const uptime_uptimesecondssensor_id = reinterpret_cast<uptime::UptimeSecondsSensor *>(uptime__uptime_uptimesecondssensor_id__pstorage);
alignas(wifi_info::IPAddressWiFiInfo) static unsigned char wifi_info__wifi_info_ipaddresswifiinfo_id__pstorage[sizeof(wifi_info::IPAddressWiFiInfo)];
static wifi_info::IPAddressWiFiInfo *const wifi_info_ipaddresswifiinfo_id = reinterpret_cast<wifi_info::IPAddressWiFiInfo *>(wifi_info__wifi_info_ipaddresswifiinfo_id__pstorage);
alignas(restart::RestartButton) static unsigned char restart__restart_restartbutton_id__pstorage[sizeof(restart::RestartButton)];
static restart::RestartButton *const restart_restartbutton_id = reinterpret_cast<restart::RestartButton *>(restart__restart_restartbutton_id__pstorage);
alignas(StatelessLambdaAction<float>) static unsigned char esphome__lambdaaction_id_2__pstorage[sizeof(StatelessLambdaAction<float>)];
static StatelessLambdaAction<float> *const lambdaaction_id_2 = reinterpret_cast<StatelessLambdaAction<float> *>(esphome__lambdaaction_id_2__pstorage);
alignas(binary_sensor::BinarySensor) static unsigned char binary_sensor__binary_sensor_binarysensor_id_5__pstorage[sizeof(binary_sensor::BinarySensor)];
static binary_sensor::BinarySensor *const binary_sensor_binarysensor_id_5 = reinterpret_cast<binary_sensor::BinarySensor *>(binary_sensor__binary_sensor_binarysensor_id_5__pstorage);
alignas(sensor::Sensor) static unsigned char sensor__sensor_sensor_id_7__pstorage[sizeof(sensor::Sensor)];
static sensor::Sensor *const sensor_sensor_id_7 = reinterpret_cast<sensor::Sensor *>(sensor__sensor_sensor_id_7__pstorage);
alignas(text_sensor::TextSensor) static unsigned char text_sensor__text_sensor_textsensor_id_3__pstorage[sizeof(text_sensor::TextSensor)];
static text_sensor::TextSensor *const text_sensor_textsensor_id_3 = reinterpret_cast<text_sensor::TextSensor *>(text_sensor__text_sensor_textsensor_id_3__pstorage);
alignas(sensor::Sensor) static unsigned char sensor__sensor_sensor_id_8__pstorage[sizeof(sensor::Sensor)];
static sensor::Sensor *const sensor_sensor_id_8 = reinterpret_cast<sensor::Sensor *>(sensor__sensor_sensor_id_8__pstorage);
alignas(sensor::Sensor) static unsigned char sensor__sensor_sensor_id_9__pstorage[sizeof(sensor::Sensor)];
static sensor::Sensor *const sensor_sensor_id_9 = reinterpret_cast<sensor::Sensor *>(sensor__sensor_sensor_id_9__pstorage);
alignas(sensor::Sensor) static unsigned char sensor__sensor_sensor_id_10__pstorage[sizeof(sensor::Sensor)];
static sensor::Sensor *const sensor_sensor_id_10 = reinterpret_cast<sensor::Sensor *>(sensor__sensor_sensor_id_10__pstorage);
#undef yield
#define yield() esphome::yield()
#undef millis
#define millis() esphome::millis()
#undef micros
#define micros() esphome::micros()
#undef delay
#define delay(x) esphome::delay(x)
#undef delayMicroseconds
#define delayMicroseconds(x) esphome::delayMicroseconds(x)
static constexpr size_t ESPHOME_LOOPING_COMPONENT_COUNT = \
  (1 * HasLoopOverride<logger::Logger>::value) + \
  (1 * HasLoopOverride<captive_portal::CaptivePortal>::value) + \
  (1 * HasLoopOverride<wifi::WiFiComponent>::value) + \
  (1 * HasLoopOverride<mdns::MDNSComponent>::value) + \
  (1 * HasLoopOverride<network::NetworkComponent>::value) + \
  (1 * HasLoopOverride<esphome::ESPHomeOTAComponent>::value) + \
  (1 * HasLoopOverride<preferences::IntervalSyncer>::value) + \
  (1 * HasLoopOverride<safe_mode::SafeModeComponent>::value) + \
  (1 * HasLoopOverride<web_server::WebServerOTAComponent>::value) + \
  (1 * HasLoopOverride<api::APIServer>::value) + \
  (1 * HasLoopOverride<StartupTrigger>::value) + \
  (1 * HasLoopOverride<i2c::IDFI2CBus>::value) + \
  (1 * HasLoopOverride<sntp::SNTPComponent>::value) + \
  (1 * HasLoopOverride<rfm69_gateway::Rfm69Gateway>::value) + \
  (1 * HasLoopOverride<creek_store::CreekStore>::value) + \
  (1 * HasLoopOverride<template_::TemplateNumber>::value) + \
  (2 * HasLoopOverride<template_::TemplateSensor>::value) + \
  (1 * HasLoopOverride<wifi_signal::WiFiSignalSensor>::value) + \
  (1 * HasLoopOverride<uptime::UptimeSecondsSensor>::value) + \
  (1 * HasLoopOverride<wifi_info::IPAddressWiFiInfo>::value) + \
  (1 * HasLoopOverride<restart::RestartButton>::value);
namespace esphome {
static const char COMP_SRC_TABLE_STR_0[] PROGMEM = "logger";
static const char COMP_SRC_TABLE_STR_1[] PROGMEM = "captive_portal";
static const char COMP_SRC_TABLE_STR_2[] PROGMEM = "wifi";
static const char COMP_SRC_TABLE_STR_3[] PROGMEM = "mdns";
static const char COMP_SRC_TABLE_STR_4[] PROGMEM = "network";
static const char COMP_SRC_TABLE_STR_5[] PROGMEM = "esphome.ota";
static const char COMP_SRC_TABLE_STR_6[] PROGMEM = "preferences";
static const char COMP_SRC_TABLE_STR_7[] PROGMEM = "safe_mode";
static const char COMP_SRC_TABLE_STR_8[] PROGMEM = "web_server.ota";
static const char COMP_SRC_TABLE_STR_9[] PROGMEM = "api";
static const char COMP_SRC_TABLE_STR_10[] PROGMEM = "esphome.coroutine";
static const char COMP_SRC_TABLE_STR_11[] PROGMEM = "i2c";
static const char COMP_SRC_TABLE_STR_12[] PROGMEM = "sntp.time";
static const char COMP_SRC_TABLE_STR_13[] PROGMEM = "rfm69_gateway";
static const char COMP_SRC_TABLE_STR_14[] PROGMEM = "creek_store";
static const char COMP_SRC_TABLE_STR_15[] PROGMEM = "template.number";
static const char COMP_SRC_TABLE_STR_16[] PROGMEM = "template.sensor";
static const char COMP_SRC_TABLE_STR_17[] PROGMEM = "wifi_signal.sensor";
static const char COMP_SRC_TABLE_STR_18[] PROGMEM = "uptime.sensor";
static const char COMP_SRC_TABLE_STR_19[] PROGMEM = "wifi_info.text_sensor";
static const char COMP_SRC_TABLE_STR_20[] PROGMEM = "restart.button";
static const char *const COMP_SRC_TABLE[] PROGMEM = {COMP_SRC_TABLE_STR_0, COMP_SRC_TABLE_STR_1, COMP_SRC_TABLE_STR_2, COMP_SRC_TABLE_STR_3, COMP_SRC_TABLE_STR_4, COMP_SRC_TABLE_STR_5, COMP_SRC_TABLE_STR_6, COMP_SRC_TABLE_STR_7, COMP_SRC_TABLE_STR_8, COMP_SRC_TABLE_STR_9, COMP_SRC_TABLE_STR_10, COMP_SRC_TABLE_STR_11, COMP_SRC_TABLE_STR_12, COMP_SRC_TABLE_STR_13, COMP_SRC_TABLE_STR_14, COMP_SRC_TABLE_STR_15, COMP_SRC_TABLE_STR_16, COMP_SRC_TABLE_STR_17, COMP_SRC_TABLE_STR_18, COMP_SRC_TABLE_STR_19, COMP_SRC_TABLE_STR_20};
const LogString *component_source_lookup(uint8_t index) {
  if (index == 0 || index > 21) return LOG_STR("<unknown>");
  return reinterpret_cast<const LogString *>(
    progmem_read_ptr(&COMP_SRC_TABLE[index - 1]));
}
}  // namespace esphome
namespace esphome {
static const char ENTITY_DC_TABLE_STR_0[] PROGMEM = "distance";
static const char ENTITY_DC_TABLE_STR_1[] PROGMEM = "voltage";
static const char ENTITY_DC_TABLE_STR_2[] PROGMEM = "signal_strength";
static const char ENTITY_DC_TABLE_STR_3[] PROGMEM = "problem";
static const char ENTITY_DC_TABLE_STR_4[] PROGMEM = "connectivity";
static const char ENTITY_DC_TABLE_STR_5[] PROGMEM = "duration";
static const char ENTITY_DC_TABLE_STR_6[] PROGMEM = "restart";
static const char ENTITY_DC_TABLE_EMPTY[] PROGMEM = "";
static const char *const ENTITY_DC_TABLE[] PROGMEM = {ENTITY_DC_TABLE_STR_0, ENTITY_DC_TABLE_STR_1, ENTITY_DC_TABLE_STR_2, ENTITY_DC_TABLE_STR_3, ENTITY_DC_TABLE_STR_4, ENTITY_DC_TABLE_STR_5, ENTITY_DC_TABLE_STR_6};
const char *entity_device_class_lookup(uint8_t index) {
  if (index == 0 || index > 7) return ENTITY_DC_TABLE_EMPTY;
  return progmem_read_ptr(&ENTITY_DC_TABLE[index - 1]);
}

static const char *const ENTITY_UOM_TABLE[] PROGMEM = {"mm", "mV", "dBm", "ft", "in", "s", "MB"};
const char *entity_uom_lookup(uint8_t index) {
  if (index == 0 || index > 7) return "";
  return progmem_read_ptr(&ENTITY_UOM_TABLE[index - 1]);
}

static const char ENTITY_ICON_TABLE_STR_0[] PROGMEM = "mdi:radio-tower";
static const char ENTITY_ICON_TABLE_STR_1[] PROGMEM = "mdi:speedometer";
static const char ENTITY_ICON_TABLE_STR_2[] PROGMEM = "mdi:flask-outline";
static const char ENTITY_ICON_TABLE_STR_3[] PROGMEM = "mdi:radar";
static const char ENTITY_ICON_TABLE_STR_4[] PROGMEM = "mdi:restart-alert";
static const char ENTITY_ICON_TABLE_STR_5[] PROGMEM = "mdi:counter";
static const char ENTITY_ICON_TABLE_STR_6[] PROGMEM = "mdi:radio-off";
static const char ENTITY_ICON_TABLE_STR_7[] PROGMEM = "mdi:arrow-expand-vertical";
static const char ENTITY_ICON_TABLE_STR_8[] PROGMEM = "mdi:waves-arrow-up";
static const char ENTITY_ICON_TABLE_STR_9[] PROGMEM = "mdi:timer-outline";
static const char ENTITY_ICON_TABLE_STR_10[] PROGMEM = "mdi:restart";
static const char ENTITY_ICON_TABLE_STR_11[] PROGMEM = "mdi:micro-sd";
static const char ENTITY_ICON_TABLE_STR_12[] PROGMEM = "mdi:clock-check-outline";
static const char ENTITY_ICON_TABLE_STR_13[] PROGMEM = "mdi:database";
static const char ENTITY_ICON_TABLE_STR_14[] PROGMEM = "mdi:weather-cloudy-alert";
static const char ENTITY_ICON_TABLE_EMPTY[] PROGMEM = "";
static const char *const ENTITY_ICON_TABLE[] PROGMEM = {ENTITY_ICON_TABLE_STR_0, ENTITY_ICON_TABLE_STR_1, ENTITY_ICON_TABLE_STR_2, ENTITY_ICON_TABLE_STR_3, ENTITY_ICON_TABLE_STR_4, ENTITY_ICON_TABLE_STR_5, ENTITY_ICON_TABLE_STR_6, ENTITY_ICON_TABLE_STR_7, ENTITY_ICON_TABLE_STR_8, ENTITY_ICON_TABLE_STR_9, ENTITY_ICON_TABLE_STR_10, ENTITY_ICON_TABLE_STR_11, ENTITY_ICON_TABLE_STR_12, ENTITY_ICON_TABLE_STR_13, ENTITY_ICON_TABLE_STR_14};
const char *entity_icon_lookup(uint8_t index) {
  if (index == 0 || index > 15) return ENTITY_ICON_TABLE_EMPTY;
  return progmem_read_ptr(&ENTITY_ICON_TABLE[index - 1]);
}

}  // namespace esphome
// ========== AUTO GENERATED INCLUDE BLOCK END ==========="

void setup() {
  // ========== AUTO GENERATED CODE BEGIN ===========
  // logger:
  //   level: INFO
  //   hardware_uart: USB_SERIAL_JTAG
  //   id: logger_logger_id
  //   baud_rate: 115200
  //   tx_buffer_size: 512
  //   deassert_rts_dtr: false
  //   task_log_buffer_size: 768
  //   logs: {}
  //   runtime_tag_levels: false
  new(logger_logger_id) logger::Logger(115200);
  logger_logger_id->create_pthread_key();
  logger_logger_id->set_uart_selection(logger::UART_SELECTION_USB_SERIAL_JTAG);
  logger_logger_id->pre_setup();
  logger_logger_id->set_log_level(ESPHOME_LOG_LEVEL_INFO);
  // network:
  //   id: network_networkcomponent_id
  //   enable_ipv6: false
  //   min_ipv6_addr_count: 0
  // esphome:
  //   name: creek-gateway-v2
  //   friendly_name: Creek Gateway v2
  //   platformio_options:
  //     build_unflags:
  //       - -DARDUINO_USB_CDC_ON_BOOT=1
  //     build_flags:
  //       - -DARDUINO_USB_CDC_ON_BOOT=0
  //   on_boot:
  //     - priority: 1100.0
  //       then:
  //         - lambda: !lambda |-
  //             pinMode(7, OUTPUT);
  //             digitalWrite(7, HIGH);
  //           type_id: lambdaaction_id
  //       automation_id: automation_id
  //       trigger_id: startuptrigger_id
  //   min_version: 2026.7.3
  //   build_path: build\creek-gateway-v2
  //   build_flags: []
  //   environment_variables: {}
  //   includes: []
  //   includes_c: []
  //   libraries: []
  //   name_add_mac_suffix: false
  //   merge_warnings: true
  //   debug_scheduler: false
  //   areas: []
  //   devices: []
  new (&App) Application();
  App.pre_setup("creek-gateway-v2", 16, "Creek Gateway v2", 16);
  App.looping_components_.init(ESPHOME_LOOPING_COMPONENT_COUNT);
  // time:
  // number:
  // sensor:
  // text_sensor:
  // button:
  // binary_sensor:
  App.register_component_(logger_logger_id, 1);
  // web_server_base:
  //   id: web_server_base_webserverbase_id
  new(web_server_base_webserverbase_id) web_server_base::WebServerBase();
  web_server_base::global_web_server_base = web_server_base_webserverbase_id;
  // captive_portal:
  //   id: captive_portal_captiveportal_id
  //   web_server_base_id: web_server_base_webserverbase_id
  //   compression: gzip
  new(captive_portal_captiveportal_id) captive_portal::CaptivePortal(web_server_base_webserverbase_id);
  App.register_component_(captive_portal_captiveportal_id, 2);
  // wifi:
  //   reboot_timeout: 0s
  //   ap:
  //     ssid: \033[8mCreek Gateway v2 Fallback\033[28m
  //     password: \033[8mfallback-password\033[28m
  //     id: wifi_wifiap_id
  //     ap_timeout: 90s
  //   id: wifi_wificomponent_id
  //   domain: .local
  //   power_save_mode: LIGHT
  //   fast_connect:
  //     enabled: false
  //     storage: flash
  //   enable_btm: false
  //   enable_rrm: false
  //   passive_scan: false
  //   enable_on_boot: true
  //   post_connect_roaming: true
  //   min_auth_mode: WPA2
  //   networks:
  //     - ssid: \033[8myour-ssid\033[28m
  //       password: \033[8myour-wifi-password\033[28m
  //       id: wifi_wifiap_id_2
  //       priority: 0
  //   use_address: creek-gateway-v2.local
  new(wifi_wificomponent_id) wifi::WiFiComponent();
  wifi_wificomponent_id->init_sta(1);
  {
  wifi::WiFiAP wifi_wifiap_id_2 = wifi::WiFiAP();
  wifi_wifiap_id_2.set_ssid("your-ssid");
  wifi_wifiap_id_2.set_password("your-wifi-password");
  wifi_wifiap_id_2.set_priority(0);
  wifi_wificomponent_id->add_sta(wifi_wifiap_id_2);
  }
  {
  wifi::WiFiAP wifi_wifiap_id = wifi::WiFiAP();
  wifi_wifiap_id.set_ssid("Creek Gateway v2 Fallback");
  wifi_wifiap_id.set_password("fallback-password");
  wifi_wificomponent_id->set_ap(wifi_wifiap_id);
  }
  wifi_wificomponent_id->set_ap_timeout(90000);
  wifi_wificomponent_id->set_reboot_timeout(0);
  wifi_wificomponent_id->set_power_save_mode(wifi::WIFI_POWER_SAVE_LIGHT);
  wifi_wificomponent_id->set_min_auth_mode(wifi::WIFI_MIN_AUTH_MODE_WPA2);
  App.register_component_(wifi_wificomponent_id, 3);
  // mdns:
  //   id: mdns_mdnscomponent_id
  //   disabled: false
  //   services: []
  new(mdns_mdnscomponent_id) mdns::MDNSComponent();
  App.register_component_(mdns_mdnscomponent_id, 4);
  new(network_networkcomponent_id) network::NetworkComponent();
  App.register_component_(network_networkcomponent_id, 5);
  // ota:
  // ota.esphome:
  //   platform: esphome
  //   id: esphome_esphomeotacomponent_id
  //   version: 2
  //   port: 3232
  //   allow_partition_access: false
  new(esphome_esphomeotacomponent_id) esphome::ESPHomeOTAComponent();
  esphome_esphomeotacomponent_id->set_port(3232);
  App.register_component_(esphome_esphomeotacomponent_id, 6);
  // ota.web_server:
  //   platform: web_server
  //   id: web_server_webserverotacomponent_id
  new(web_server_webserverotacomponent_id) web_server::WebServerOTAComponent();
  // preferences:
  //   id: preferences_intervalsyncer_id
  //   flash_write_interval: 60s
  new(preferences_intervalsyncer_id) preferences::IntervalSyncer();
  preferences_intervalsyncer_id->set_write_interval(60000);
  App.register_component_(preferences_intervalsyncer_id, 7);
  // safe_mode:
  //   id: safe_mode_safemodecomponent_id
  //   boot_is_good_after: 1min
  //   disabled: false
  //   num_attempts: 10
  //   reboot_timeout: 5min
  //   storage: flash
  new(safe_mode_safemodecomponent_id) safe_mode::SafeModeComponent();
  App.register_component_(safe_mode_safemodecomponent_id, 8);
  if (safe_mode_safemodecomponent_id->should_enter_safe_mode(10, 300000, 60000, true)) return;
  App.register_component_(web_server_webserverotacomponent_id, 9);
  // api:
  //   encryption:
  //     key: \033[8mAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\033[28m
  //   reboot_timeout: 0s
  //   id: api_apiserver_id
  //   port: 6053
  //   batch_delay: 100ms
  //   custom_services: false
  //   homeassistant_services: false
  //   homeassistant_states: false
  //   listen_backlog: 4
  //   max_connections: 5
  //   max_send_queue: 8
  new(api_apiserver_id) api::APIServer();
  App.register_component_(api_apiserver_id, 10);
  api_apiserver_id->set_port(6053);
  api_apiserver_id->set_reboot_timeout(0);
  api_apiserver_id->set_batch_delay(100);
  api_apiserver_id->set_listen_backlog(4);
  api_apiserver_id->set_noise_psk({0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0});
  new(startuptrigger_id) StartupTrigger(1100.0f);
  App.register_component_(startuptrigger_id, 11);
  new(automation_id) Automation<>(startuptrigger_id);
  new(lambdaaction_id) StatelessLambdaAction<>([]() -> void {
      #line 65 "gateway.base.yaml"
      pinMode(7, OUTPUT);
      digitalWrite(7, HIGH);
  });
  automation_id->add_actions({lambdaaction_id});
  // i2c:
  //   id: i2c_bus
  //   sda: 3
  //   scl: 4
  //   scan: true
  //   sda_pullup_enabled: true
  //   scl_pullup_enabled: true
  //   frequency: 50000.0
  new(i2c_bus) i2c::IDFI2CBus();
  App.register_component_(i2c_bus, 12);
  i2c_bus->set_sda_pin(3);
  i2c_bus->set_sda_pullup_enabled(true);
  i2c_bus->set_scl_pin(4);
  i2c_bus->set_scl_pullup_enabled(true);
  i2c_bus->set_frequency(50000);
  i2c_bus->set_scan(true);
  // json:
  //   {}
  // substitutions:
  //   device_name: creek-gateway-v2
  //   friendly_name: Creek Gateway v2
  //   rfm69_cs_pin: '6'
  //   rfm69_sck_pin: '36'
  //   rfm69_miso_pin: '37'
  //   rfm69_mosi_pin: '35'
  //   rfm69_irq_pin: '5'
  //   rfm69_reset_pin: '9'
  //   rfm69_frequency: '915'
  //   rfm69_node_id: '2'
  //   rfm69_network_id: '100'
  //   sd_cs_pin: '10'
  //   i2c_power_pin: '7'
  //   ecowitt_host: 192.168.30.7
  //   node_hex_url: https:github.com/ryanbuiltthat/rate-of-rise/releases/download/node-firmware-latest/firmware.hex
  //   node_diag_hex_url: https:github.com/ryanbuiltthat/rate-of-rise/releases/download/node-firmware-latest/firmware-diag.hex
  //   blanking_mm: '150'
  //   overrange_slack_mm: '1000'
  // esp32:
  //   board: adafruit_feather_esp32s3_nopsram
  //   variant: ESP32S3
  //   flash_size: 8MB
  //   framework:
  //     type: arduino
  //     sdkconfig_options:
  //       CONFIG_APP_REPRODUCIBLE_BUILD: n
  //     version: 3.3.10
  //     log_level: ERROR
  //     advanced:
  //       compiler_optimization: SIZE
  //       enable_idf_experimental_features: false
  //       enable_lwip_assert: true
  //       ignore_efuse_custom_mac: false
  //       ignore_efuse_mac_crc: false
  //       sram1_as_iram: false
  //       enable_lwip_mdns_queries: true
  //       enable_lwip_bridge_interface: false
  //       enable_lwip_tcpip_core_locking: true
  //       enable_lwip_check_thread_safety: true
  //       disable_libc_locks_in_iram: true
  //       disable_vfs_support_termios: true
  //       disable_vfs_support_select: true
  //       disable_vfs_support_dir: true
  //       freertos_in_iram: false
  //       ringbuf_in_iram: false
  //       heap_in_iram: false
  //       execute_from_psram: false
  //       loop_task_stack_size: 8192
  //       enable_ota_rollback: true
  //       enable_ota_downgrade_protection: false
  //       use_full_certificate_bundle: false
  //       include_builtin_idf_components: []
  //       enable_full_printf: false
  //       disable_debug_stubs: true
  //       disable_ocd_aware: true
  //       disable_usb_serial_jtag_secondary: true
  //       disable_dev_null_vfs: true
  //       disable_mbedtls_peer_cert: true
  //       disable_mbedtls_pkcs7: true
  //       disable_regi2c_in_iram: true
  //       adc_oneshot_in_iram: false
  //       disable_fatfs: true
  //     components: []
  //     platform_version: https:github.com/pioarduino/platform-espressif32/releases/download/55.03.39/platform-espressif32.zip
  //     source: pioarduino/framework-arduinoespressif32@https:github.com/espressif/arduino-esp32/releases/download/3.3.10/esp32-core-3.3.10.tar.xz
  //   toolchain: platformio
  //   watchdog_timeout: 5s
  //   cpu_frequency: 240MHZ
  // time.sntp:
  //   platform: sntp
  //   id: sntp_time
  //   servers:
  //     - 192.168.30.1
  //     - 0.pool.ntp.org
  //     - 1.pool.ntp.org
  //   update_interval: 15min
  new(sntp_time) sntp::SNTPComponent({"192.168.30.1", "0.pool.ntp.org", "1.pool.ntp.org"});
  sntp_time->set_update_interval(900000);
  App.register_component_(sntp_time, 13);
  {
  time::ParsedTimezone tz{};
  tz.std_offset_seconds = 18000;
  tz.dst_offset_seconds = 14400;
  tz.dst_start.time_seconds = 7200;
  tz.dst_start.day = 0;
  tz.dst_start.type = time::DSTRuleType::MONTH_WEEK_DAY;
  tz.dst_start.month = 3;
  tz.dst_start.week = 2;
  tz.dst_start.day_of_week = 0;
  tz.dst_end.time_seconds = 7200;
  tz.dst_end.day = 0;
  tz.dst_end.type = time::DSTRuleType::MONTH_WEEK_DAY;
  tz.dst_end.month = 11;
  tz.dst_end.week = 1;
  tz.dst_end.day_of_week = 0;
  time::set_global_tz(tz);
  }
  // rfm69_gateway:
  //   id: creek_radio
  //   cs_pin: 6
  //   sck_pin: 36
  //   miso_pin: 37
  //   mosi_pin: 35
  //   irq_pin: 5
  //   reset_pin: 9
  //   frequency: 915
  //   node_id: 2
  //   network_id: 100
  //   is_rfm69hw: true
  //   encryption_key: sampleEncryptKey
  //   ota_hex_url: https:github.com/ryanbuiltthat/rate-of-rise/releases/download/node-firmware-latest/firmware.hex
  //   ota_diag_hex_url: https:github.com/ryanbuiltthat/rate-of-rise/releases/download/node-firmware-latest/firmware-diag.hex
  //   distance:
  //     id: creek_distance_mm
  //     name: Sensor Distance
  //     entity_category: diagnostic
  //     on_value:
  //       - then:
  //           - lambda: !lambda |-
  //               const float mount = id(mount_height_mm).state;
  //               if (isnan(mount)) return;
  //               const auto r = creek_core::stage_from_distance(
  //                   mount, isnan(x) ? std::optional<float>() : std::optional<float>(x),
  //                   150, 1000);
  //               if (r.lost_target) ESP_LOGW("creek", "distance %.0f mm implausible - target lost", x);
  //               if (r.clamped) ESP_LOGW("creek", "distance %.0f mm inside blanking zone - depth "
  //                                       "clamped (at or above range ceiling)", x);
  //               id(creek_stage).publish_state(r.stage_ft ? *r.stage_ft : NAN);
  //               id(creek_depth_in).publish_state(r.depth_in ? *r.depth_in : NAN);
  //             type_id: lambdaaction_id_2
  //         trigger_id: trigger_id
  //         automation_id: automation_id_2
  //     disabled_by_default: false
  //     force_update: false
  //     unit_of_measurement: mm
  //     accuracy_decimals: 0
  //     device_class: distance
  //     state_class: measurement
  //   battery_voltage:
  //     name: Creek Node Battery
  //     disabled_by_default: false
  //     id: sensor_sensor_id
  //     force_update: false
  //     unit_of_measurement: mV
  //     accuracy_decimals: 0
  //     device_class: voltage
  //     state_class: measurement
  //     entity_category: diagnostic
  //   rssi:
  //     name: Creek Node RSSI
  //     disabled_by_default: false
  //     id: sensor_sensor_id_2
  //     force_update: false
  //     unit_of_measurement: dBm
  //     accuracy_decimals: 0
  //     device_class: signal_strength
  //     state_class: measurement
  //     entity_category: diagnostic
  //   packet_count:
  //     name: Creek Node Packets
  //     disabled_by_default: false
  //     id: sensor_sensor_id_3
  //     force_update: false
  //     icon: mdi:radio-tower
  //     accuracy_decimals: 0
  //     state_class: total_increasing
  //     entity_category: diagnostic
  //   fast_mode:
  //     name: Creek Node Fast Sampling
  //     disabled_by_default: false
  //     id: binary_sensor_binarysensor_id
  //     icon: mdi:speedometer
  //     entity_category: diagnostic
  //   diag_active:
  //     name: Creek Node Diagnostic Active
  //     disabled_by_default: false
  //     id: binary_sensor_binarysensor_id_2
  //     icon: mdi:flask-outline
  //     entity_category: diagnostic
  //   radar_fault:
  //     name: Creek Node Radar Fault
  //     disabled_by_default: false
  //     id: binary_sensor_binarysensor_id_3
  //     icon: mdi:radar
  //     entity_category: diagnostic
  //     device_class: problem
  //   radar_failures:
  //     name: Creek Node Radar Failures
  //     disabled_by_default: false
  //     id: sensor_sensor_id_4
  //     force_update: false
  //     icon: mdi:radar
  //     accuracy_decimals: 0
  //     state_class: measurement
  //     entity_category: diagnostic
  //   node_status:
  //     name: Creek Node Status
  //     disabled_by_default: false
  //     id: binary_sensor_binarysensor_id_4
  //     entity_category: diagnostic
  //     device_class: connectivity
  //   ota_status:
  //     name: Node OTA Status
  //     disabled_by_default: false
  //     id: text_sensor_textsensor_id
  //     entity_category: diagnostic
  //   reset_cause:
  //     name: Creek Node Reset Cause
  //     disabled_by_default: false
  //     id: text_sensor_textsensor_id_2
  //     icon: mdi:restart-alert
  //     entity_category: diagnostic
  //   cycle:
  //     name: Creek Node Cycle
  //     disabled_by_default: false
  //     id: sensor_sensor_id_5
  //     force_update: false
  //     icon: mdi:counter
  //     accuracy_decimals: 0
  //     state_class: measurement
  //     entity_category: diagnostic
  //   radio_init_failures:
  //     name: Creek Node Radio Init Failures
  //     disabled_by_default: false
  //     id: sensor_sensor_id_6
  //     force_update: false
  //     icon: mdi:radio-off
  //     accuracy_decimals: 0
  //     state_class: measurement
  //     entity_category: diagnostic
  //   timeout: 5min
  new(creek_radio) rfm69_gateway::Rfm69Gateway(6, 36, 37, 35, 5, 91, 9, 2, 100, true, "sampleEncryptKey");
  App.register_component_(creek_radio, 14);
  creek_radio->set_node_timeout(300000);
  creek_radio->set_ota_hex_url("https://github.com/ryanbuiltthat/rate-of-rise/releases/download/node-firmware-latest/firmware.hex");
  creek_radio->set_ota_diag_hex_url("https://github.com/ryanbuiltthat/rate-of-rise/releases/download/node-firmware-latest/firmware-diag.hex");
  new(creek_distance_mm) sensor::Sensor();
  creek_distance_mm->set_state_class(sensor::STATE_CLASS_MEASUREMENT);
  creek_distance_mm->set_accuracy_decimals(0);
  App.register_sensor(creek_distance_mm, "Sensor Distance", 1751367931, 134217985);  // category:diagnostic, dc:distance, uom:mm
  creek_radio->set_distance_sensor(creek_distance_mm);
  new(sensor_sensor_id) sensor::Sensor();
  sensor_sensor_id->set_state_class(sensor::STATE_CLASS_MEASUREMENT);
  sensor_sensor_id->set_accuracy_decimals(0);
  App.register_sensor(sensor_sensor_id, "Creek Node Battery", 1810126576, 134218242);  // category:diagnostic, dc:voltage, uom:mV
  creek_radio->set_battery_sensor(sensor_sensor_id);
  new(sensor_sensor_id_2) sensor::Sensor();
  sensor_sensor_id_2->set_state_class(sensor::STATE_CLASS_MEASUREMENT);
  sensor_sensor_id_2->set_accuracy_decimals(0);
  App.register_sensor(sensor_sensor_id_2, "Creek Node RSSI", 1522383928, 134218499);  // category:diagnostic, dc:signal_strength, uom:dBm
  creek_radio->set_rssi_sensor(sensor_sensor_id_2);
  new(sensor_sensor_id_3) sensor::Sensor();
  sensor_sensor_id_3->set_state_class(sensor::STATE_CLASS_TOTAL_INCREASING);
  sensor_sensor_id_3->set_accuracy_decimals(0);
  App.register_sensor(sensor_sensor_id_3, "Creek Node Packets", 3738579828UL, 134283264);  // category:diagnostic, icon:mdi:radio-tower
  creek_radio->set_packet_count_sensor(sensor_sensor_id_3);
  new(binary_sensor_binarysensor_id) binary_sensor::BinarySensor();
  binary_sensor_binarysensor_id->set_trigger_on_initial_state(false);
  App.register_binary_sensor(binary_sensor_binarysensor_id, "Creek Node Fast Sampling", 610656741, 134348800);  // category:diagnostic, icon:mdi:speedometer
  creek_radio->set_fast_mode_sensor(binary_sensor_binarysensor_id);
  new(binary_sensor_binarysensor_id_2) binary_sensor::BinarySensor();
  binary_sensor_binarysensor_id_2->set_trigger_on_initial_state(false);
  App.register_binary_sensor(binary_sensor_binarysensor_id_2, "Creek Node Diagnostic Active", 283335461, 134414336);  // category:diagnostic, icon:mdi:flask-outline
  creek_radio->set_diag_active_sensor(binary_sensor_binarysensor_id_2);
  new(binary_sensor_binarysensor_id_3) binary_sensor::BinarySensor();
  binary_sensor_binarysensor_id_3->set_trigger_on_initial_state(false);
  App.register_binary_sensor(binary_sensor_binarysensor_id_3, "Creek Node Radar Fault", 1882102462, 134479876);  // category:diagnostic, dc:problem, icon:mdi:radar
  creek_radio->set_radar_fault_sensor(binary_sensor_binarysensor_id_3);
  new(sensor_sensor_id_4) sensor::Sensor();
  sensor_sensor_id_4->set_state_class(sensor::STATE_CLASS_MEASUREMENT);
  sensor_sensor_id_4->set_accuracy_decimals(0);
  App.register_sensor(sensor_sensor_id_4, "Creek Node Radar Failures", 3612571215UL, 134479872);  // category:diagnostic, icon:mdi:radar
  creek_radio->set_radar_failures_sensor(sensor_sensor_id_4);
  new(binary_sensor_binarysensor_id_4) binary_sensor::BinarySensor();
  binary_sensor_binarysensor_id_4->set_trigger_on_initial_state(false);
  App.register_binary_sensor(binary_sensor_binarysensor_id_4, "Creek Node Status", 3203591631UL, 134217733);  // category:diagnostic, dc:connectivity
  creek_radio->set_node_status_sensor(binary_sensor_binarysensor_id_4);
  new(text_sensor_textsensor_id) text_sensor::TextSensor();
  App.register_text_sensor(text_sensor_textsensor_id, "Node OTA Status", 2648174503UL, 134217728);  // category:diagnostic
  creek_radio->set_ota_status_sensor(text_sensor_textsensor_id);
  new(text_sensor_textsensor_id_2) text_sensor::TextSensor();
  App.register_text_sensor(text_sensor_textsensor_id_2, "Creek Node Reset Cause", 4277959938UL, 134545408);  // category:diagnostic, icon:mdi:restart-alert
  creek_radio->set_reset_cause_sensor(text_sensor_textsensor_id_2);
  new(sensor_sensor_id_5) sensor::Sensor();
  sensor_sensor_id_5->set_state_class(sensor::STATE_CLASS_MEASUREMENT);
  sensor_sensor_id_5->set_accuracy_decimals(0);
  App.register_sensor(sensor_sensor_id_5, "Creek Node Cycle", 2010354801, 134610944);  // category:diagnostic, icon:mdi:counter
  creek_radio->set_cycle_sensor(sensor_sensor_id_5);
  new(sensor_sensor_id_6) sensor::Sensor();
  sensor_sensor_id_6->set_state_class(sensor::STATE_CLASS_MEASUREMENT);
  sensor_sensor_id_6->set_accuracy_decimals(0);
  App.register_sensor(sensor_sensor_id_6, "Creek Node Radio Init Failures", 425087583, 134676480);  // category:diagnostic, icon:mdi:radio-off
  creek_radio->set_radio_init_failures_sensor(sensor_sensor_id_6);
  new(automation_id_2) Automation<float>();
  // creek_store:
  //   id: store
  //   radio_id: creek_radio
  //   time_id: sntp_time
  //   mount_height_id: mount_height_mm
  //   i2c_id: i2c_bus
  //   address: 0x68
  //   sd_cs_pin: 10
  //   token: 0123456789abcdef0123456789abcdef
  //   ecowitt_host: 192.168.30.7
  //   ecowitt_interval: 60s
  //   blanking_mm: 150.0
  //   overrange_slack_mm: 1000.0
  //   sd_fault:
  //     name: Store SD Fault
  //     disabled_by_default: false
  //     id: binary_sensor_binarysensor_id_5
  //     icon: mdi:micro-sd
  //     entity_category: diagnostic
  //     device_class: problem
  //   free_space:
  //     name: Store Free Space
  //     disabled_by_default: false
  //     id: sensor_sensor_id_7
  //     force_update: false
  //     unit_of_measurement: MB
  //     icon: mdi:micro-sd
  //     accuracy_decimals: 0
  //     state_class: measurement
  //     entity_category: diagnostic
  //   clock_source:
  //     name: Store Clock Source
  //     disabled_by_default: false
  //     id: text_sensor_textsensor_id_3
  //     icon: mdi:clock-check-outline
  //     entity_category: diagnostic
  //   node_records:
  //     name: Store Node Records
  //     disabled_by_default: false
  //     id: sensor_sensor_id_8
  //     force_update: false
  //     icon: mdi:database
  //     accuracy_decimals: 0
  //     state_class: total_increasing
  //     entity_category: diagnostic
  //   ecowitt_records:
  //     name: Store Ecowitt Records
  //     disabled_by_default: false
  //     id: sensor_sensor_id_9
  //     force_update: false
  //     icon: mdi:database
  //     accuracy_decimals: 0
  //     state_class: total_increasing
  //     entity_category: diagnostic
  //   ecowitt_failures:
  //     name: Ecowitt Poll Failures
  //     disabled_by_default: false
  //     id: sensor_sensor_id_10
  //     force_update: false
  //     icon: mdi:weather-cloudy-alert
  //     accuracy_decimals: 0
  //     state_class: total_increasing
  //     entity_category: diagnostic
  new(store) creek_store::CreekStore();
  App.register_component_(store, 15);
  store->set_i2c_bus(i2c_bus);
  store->set_i2c_address(0x68);
  store->set_radio(creek_radio);
  store->set_time(sntp_time);
  // number.template:
  //   platform: template
  //   id: mount_height_mm
  //   name: Installation Height
  //   icon: mdi:arrow-expand-vertical
  //   entity_category: config
  //   optimistic: true
  //   restore_value: true
  //   initial_value: 1105.0
  //   min_value: 500.0
  //   max_value: 6000.0
  //   step: 1.0
  //   unit_of_measurement: mm
  //   mode: BOX
  //   disabled_by_default: false
  //   update_interval: 60s
  new(mount_height_mm) template_::TemplateNumber();
  mount_height_mm->set_update_interval(60000);
  App.register_component_(mount_height_mm, 16);
  mount_height_mm->traits.set_min_value(500.0f);
  mount_height_mm->traits.set_max_value(6000.0f);
  mount_height_mm->traits.set_step(1.0f);
  mount_height_mm->traits.set_mode(number::NUMBER_MODE_BOX);
  App.register_number(mount_height_mm, "Installation Height", 1546238833, 67633408);  // category:config, uom:mm, icon:mdi:arrow-expand-vertical
  mount_height_mm->set_optimistic(true);
  mount_height_mm->set_initial_value(1105.0f);
  mount_height_mm->set_restore_value(true);
  // sensor.template:
  //   platform: template
  //   id: creek_stage
  //   name: Stage
  //   unit_of_measurement: ft
  //   device_class: distance
  //   state_class: measurement
  //   accuracy_decimals: 2
  //   disabled_by_default: false
  //   force_update: false
  //   update_interval: 60s
  new(creek_stage) template_::TemplateSensor();
  creek_stage->set_state_class(sensor::STATE_CLASS_MEASUREMENT);
  creek_stage->set_accuracy_decimals(2);
  App.register_sensor(creek_stage, "Stage", 1063701865, 1025);  // dc:distance, uom:ft
  creek_stage->set_update_interval(60000);
  App.register_component_(creek_stage, 17);
  // sensor.template:
  //   platform: template
  //   id: creek_depth_in
  //   name: Creek Depth
  //   unit_of_measurement: in
  //   device_class: distance
  //   state_class: measurement
  //   accuracy_decimals: 1
  //   icon: mdi:waves-arrow-up
  //   disabled_by_default: false
  //   force_update: false
  //   update_interval: 60s
  new(creek_depth_in) template_::TemplateSensor();
  creek_depth_in->set_state_class(sensor::STATE_CLASS_MEASUREMENT);
  creek_depth_in->set_accuracy_decimals(1);
  App.register_sensor(creek_depth_in, "Creek Depth", 2911416033UL, 591105);  // dc:distance, uom:in, icon:mdi:waves-arrow-up
  creek_depth_in->set_update_interval(60000);
  App.register_component_(creek_depth_in, 17);
  // sensor.wifi_signal:
  //   platform: wifi_signal
  //   name: WiFi Signal
  //   entity_category: diagnostic
  //   update_interval: 300s
  //   disabled_by_default: false
  //   force_update: false
  //   id: wifi_signal_wifisignalsensor_id
  //   unit_of_measurement: dBm
  //   accuracy_decimals: 0
  //   device_class: signal_strength
  //   state_class: measurement
  new(wifi_signal_wifisignalsensor_id) wifi_signal::WiFiSignalSensor();
  wifi_signal_wifisignalsensor_id->set_state_class(sensor::STATE_CLASS_MEASUREMENT);
  wifi_signal_wifisignalsensor_id->set_accuracy_decimals(0);
  App.register_sensor(wifi_signal_wifisignalsensor_id, "WiFi Signal", 799351157, 134218499);  // category:diagnostic, dc:signal_strength, uom:dBm
  wifi_signal_wifisignalsensor_id->set_update_interval(300000);
  App.register_component_(wifi_signal_wifisignalsensor_id, 18);
  // sensor.uptime:
  //   platform: uptime
  //   name: Uptime
  //   entity_category: diagnostic
  //   update_interval: 300s
  //   disabled_by_default: false
  //   force_update: false
  //   id: uptime_uptimesecondssensor_id
  //   unit_of_measurement: s
  //   icon: mdi:timer-outline
  //   accuracy_decimals: 0
  //   device_class: duration
  //   state_class: total_increasing
  //   type: seconds
  new(uptime_uptimesecondssensor_id) uptime::UptimeSecondsSensor();
  uptime_uptimesecondssensor_id->set_state_class(sensor::STATE_CLASS_TOTAL_INCREASING);
  uptime_uptimesecondssensor_id->set_accuracy_decimals(0);
  App.register_sensor(uptime_uptimesecondssensor_id, "Uptime", 1324261225, 134874630);  // category:diagnostic, dc:duration, uom:s, icon:mdi:timer-outline
  uptime_uptimesecondssensor_id->set_update_interval(300000);
  App.register_component_(uptime_uptimesecondssensor_id, 19);
  // text_sensor.wifi_info:
  //   platform: wifi_info
  //   ip_address:
  //     name: IP Address
  //     entity_category: diagnostic
  //     disabled_by_default: false
  //     id: wifi_info_ipaddresswifiinfo_id
  new(wifi_info_ipaddresswifiinfo_id) wifi_info::IPAddressWiFiInfo();
  App.register_text_sensor(wifi_info_ipaddresswifiinfo_id, "IP Address", 3849966195UL, 134217728);  // category:diagnostic
  App.register_component_(wifi_info_ipaddresswifiinfo_id, 20);
  // button.restart:
  //   platform: restart
  //   name: Restart Creek Gateway
  //   disabled_by_default: false
  //   id: restart_restartbutton_id
  //   icon: mdi:restart
  //   entity_category: config
  //   device_class: restart
  new(restart_restartbutton_id) restart::RestartButton();
  App.register_component_(restart_restartbutton_id, 21);
  App.register_button(restart_restartbutton_id, "Restart Creek Gateway", 2119681088, 67829767);  // category:config, dc:restart, icon:mdi:restart
  // external_components:
  //   - source:
  //       path: C:\1_ProjectRepos\rate-of-rise\firmware\esp32s3_feather_gateway\..\esp32_rfm69_gateway\components
  //       type: local
  //     components:
  //       - rfm69_gateway
  //     refresh: 1d
  //   - source:
  //       path: C:\1_ProjectRepos\rate-of-rise\firmware\esp32s3_feather_gateway\components
  //       type: local
  //     components:
  //       - creek_store
  //     refresh: 1d
  // md5:
  // sha256:
  //   {}
  // socket:
  //   implementation: bsd_sockets
  // web_server_idf:
  //   {}
  new(lambdaaction_id_2) StatelessLambdaAction<float>([](float x) -> void {
      #line 143 "gateway.base.yaml"
      const float mount = mount_height_mm->state;
      if (isnan(mount)) return;
      const auto r = creek_core::stage_from_distance(
          mount, isnan(x) ? std::optional<float>() : std::optional<float>(x),
          150, 1000);
      if (r.lost_target) ESP_LOGW("creek", "distance %.0f mm implausible - target lost", x);
      if (r.clamped) ESP_LOGW("creek", "distance %.0f mm inside blanking zone - depth "
                              "clamped (at or above range ceiling)", x);
      creek_stage->publish_state(r.stage_ft ? *r.stage_ft : NAN);
      creek_depth_in->publish_state(r.depth_in ? *r.depth_in : NAN);
  });
  automation_id_2->add_actions({lambdaaction_id_2});
  creek_distance_mm->add_on_state_callback(TriggerForwarder<float>{automation_id_2});
  store->set_mount_height(mount_height_mm);
  store->set_sd_cs_pin(10);
  store->set_token("0123456789abcdef0123456789abcdef");
  store->set_ecowitt_host("192.168.30.7");
  store->set_ecowitt_interval_ms(60000);
  store->set_blanking_mm(150.0f);
  store->set_overrange_slack_mm(1000.0f);
  store->set_device_name("creek-gateway-v2");
  new(binary_sensor_binarysensor_id_5) binary_sensor::BinarySensor();
  binary_sensor_binarysensor_id_5->set_trigger_on_initial_state(false);
  App.register_binary_sensor(binary_sensor_binarysensor_id_5, "Store SD Fault", 1125577003, 135004164);  // category:diagnostic, dc:problem, icon:mdi:micro-sd
  store->set_sd_fault_sensor(binary_sensor_binarysensor_id_5);
  new(sensor_sensor_id_7) sensor::Sensor();
  sensor_sensor_id_7->set_state_class(sensor::STATE_CLASS_MEASUREMENT);
  sensor_sensor_id_7->set_accuracy_decimals(0);
  App.register_sensor(sensor_sensor_id_7, "Store Free Space", 1249431942, 135005952);  // category:diagnostic, uom:MB, icon:mdi:micro-sd
  store->set_free_space_sensor(sensor_sensor_id_7);
  new(text_sensor_textsensor_id_3) text_sensor::TextSensor();
  App.register_text_sensor(text_sensor_textsensor_id_3, "Store Clock Source", 2862361897UL, 135069696);  // category:diagnostic, icon:mdi:clock-check-outline
  store->set_clock_source_sensor(text_sensor_textsensor_id_3);
  new(sensor_sensor_id_8) sensor::Sensor();
  sensor_sensor_id_8->set_state_class(sensor::STATE_CLASS_TOTAL_INCREASING);
  sensor_sensor_id_8->set_accuracy_decimals(0);
  App.register_sensor(sensor_sensor_id_8, "Store Node Records", 522412032, 135135232);  // category:diagnostic, icon:mdi:database
  store->set_node_records_sensor(sensor_sensor_id_8);
  new(sensor_sensor_id_9) sensor::Sensor();
  sensor_sensor_id_9->set_state_class(sensor::STATE_CLASS_TOTAL_INCREASING);
  sensor_sensor_id_9->set_accuracy_decimals(0);
  App.register_sensor(sensor_sensor_id_9, "Store Ecowitt Records", 3353335597UL, 135135232);  // category:diagnostic, icon:mdi:database
  store->set_ecowitt_records_sensor(sensor_sensor_id_9);
  new(sensor_sensor_id_10) sensor::Sensor();
  sensor_sensor_id_10->set_state_class(sensor::STATE_CLASS_TOTAL_INCREASING);
  sensor_sensor_id_10->set_accuracy_decimals(0);
  App.register_sensor(sensor_sensor_id_10, "Ecowitt Poll Failures", 2020510814, 135200768);  // category:diagnostic, icon:mdi:weather-cloudy-alert
  store->set_ecowitt_failures_sensor(sensor_sensor_id_10);
  // =========== AUTO GENERATED CODE END ============
  App.setup();
}

void loop() {
  App.loop();
}
