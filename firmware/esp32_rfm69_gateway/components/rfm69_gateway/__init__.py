"""RFM69 -> Home Assistant gateway component.

Receives JSON packets from the Creek Moteino node over RFM69HW and publishes the
decoded readings as native ESPHome entities, which reach Home Assistant over the native API.
See rfm69_gateway.h for the radio and decode logic; this file only wires YAML to the C++ class.
"""

import os

import esphome.codegen as cg
from esphome.components import binary_sensor, sensor, text_sensor
import esphome.config_validation as cv
from esphome.const import (
    CONF_BATTERY_VOLTAGE,
    CONF_DISTANCE,
    CONF_FREQUENCY,
    CONF_ID,
    CONF_TIMEOUT,
    DEVICE_CLASS_CONNECTIVITY,
    DEVICE_CLASS_DISTANCE,
    DEVICE_CLASS_SIGNAL_STRENGTH,
    DEVICE_CLASS_VOLTAGE,
    ENTITY_CATEGORY_DIAGNOSTIC,
    STATE_CLASS_MEASUREMENT,
    STATE_CLASS_TOTAL_INCREASING,
    UNIT_DECIBEL_MILLIWATT,
    UNIT_MILLIMETER,
    UNIT_MILLIVOLT,
)

AUTO_LOAD = ["binary_sensor", "json", "sensor", "text_sensor"]
CODEOWNERS = ["@ryanbuiltthat"]

rfm69_gateway_ns = cg.esphome_ns.namespace("rfm69_gateway")
Rfm69Gateway = rfm69_gateway_ns.class_("Rfm69Gateway", cg.Component)

CONF_CS_PIN = "cs_pin"
CONF_SCK_PIN = "sck_pin"
CONF_MISO_PIN = "miso_pin"
CONF_MOSI_PIN = "mosi_pin"
CONF_IRQ_PIN = "irq_pin"
CONF_RESET_PIN = "reset_pin"

# The RFM69 library takes a band selector, not a frequency: deliberately non-sequential
# values so a miswritten config fails loudly instead of landing on the wrong band. Which
# band is legal depends on region -- 915 MHz is the North American ISM band, 868 MHz the
# European one -- and it MUST match FREQUENCY in moteino_creek_node/src/main.cpp.
RF69_BANDS = {315: 31, 433: 43, 868: 86, 915: 91}
CONF_NODE_ID = "node_id"
CONF_NETWORK_ID = "network_id"
CONF_IS_RFM69HW = "is_rfm69hw"
CONF_ENCRYPTION_KEY = "encryption_key"
CONF_RSSI = "rssi"
CONF_PACKET_COUNT = "packet_count"
CONF_FAST_MODE = "fast_mode"
CONF_DIAG_ACTIVE = "diag_active"
CONF_NODE_STATUS = "node_status"
CONF_OTA_STATUS = "ota_status"
CONF_OTA_HEX_URL = "ota_hex_url"
CONF_OTA_DIAG_HEX_URL = "ota_diag_hex_url"


def _https_url(value):
    value = cv.url(value)
    if not value.startswith("https://"):
        raise cv.Invalid("ota_hex_url must be an https:// URL")
    return value


CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(Rfm69Gateway),
        cv.Required(CONF_CS_PIN): cv.int_,
        cv.Required(CONF_SCK_PIN): cv.int_,
        cv.Required(CONF_MISO_PIN): cv.int_,
        cv.Required(CONF_MOSI_PIN): cv.int_,
        cv.Required(CONF_IRQ_PIN): cv.int_,
        cv.Required(CONF_FREQUENCY): cv.one_of(*RF69_BANDS, int=True),
        # Optional. RESET is active HIGH on the SX1231H. Wiring it to a GPIO lets us
        # force a clean reset at every boot instead of trusting power-on reset, which
        # does not reliably fire on a slow-ramping rail -- the chip then comes up with a
        # working register file but a wedged state machine (OPMODE stuck at 0x00,
        # ModeReady never set). Omit to leave RST tied low and rely on POR.
        cv.Optional(CONF_RESET_PIN, default=-1): cv.int_range(min=-1, max=48),
        cv.Required(CONF_NODE_ID): cv.uint8_t,
        cv.Required(CONF_NETWORK_ID): cv.uint8_t,
        cv.Optional(CONF_IS_RFM69HW, default=True): cv.boolean,
        # 16 ASCII chars, matching what RFM69::encrypt() (AES-128) expects — same
        # constraint the two .ino sketches relied on without validating.
        cv.Required(CONF_ENCRYPTION_KEY): cv.All(
            cv.string_strict, cv.Length(min=16, max=16)
        ),
        # The node transmits every 60 s (REPORT_INTERVAL_S in moteino_creek_node/src/main.cpp),
        # so this default tolerates five missed reports before declaring it offline.
        cv.Optional(CONF_TIMEOUT, default="5min"): cv.positive_time_period_milliseconds,
        cv.Optional(CONF_DISTANCE): sensor.sensor_schema(
            unit_of_measurement=UNIT_MILLIMETER,
            accuracy_decimals=0,
            device_class=DEVICE_CLASS_DISTANCE,
            state_class=STATE_CLASS_MEASUREMENT,
        ),
        cv.Optional(CONF_BATTERY_VOLTAGE): sensor.sensor_schema(
            unit_of_measurement=UNIT_MILLIVOLT,
            accuracy_decimals=0,
            device_class=DEVICE_CLASS_VOLTAGE,
            state_class=STATE_CLASS_MEASUREMENT,
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
        cv.Optional(CONF_RSSI): sensor.sensor_schema(
            unit_of_measurement=UNIT_DECIBEL_MILLIWATT,
            accuracy_decimals=0,
            device_class=DEVICE_CLASS_SIGNAL_STRENGTH,
            state_class=STATE_CLASS_MEASUREMENT,
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
        # Wakes received, as a monotonic counter. No device_class: HA has none for "things
        # that happened", and picking a wrong one (energy, water) would put this in the
        # energy dashboard. state_class matters though -- TOTAL_INCREASING is what lets a
        # statistics or derivative helper turn it into wakes-per-hour across gateway reboots.
        cv.Optional(CONF_PACKET_COUNT): sensor.sensor_schema(
            accuracy_decimals=0,
            state_class=STATE_CLASS_TOTAL_INCREASING,
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            icon="mdi:radio-tower",
        ),
        # No device_class again: this is the node's sampling cadence, and CONNECTIVITY (the
        # nearest fit) would render it as Connected/Disconnected, which reads as a link
        # problem rather than as the node working harder.
        cv.Optional(CONF_FAST_MODE): binary_sensor.binary_sensor_schema(
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            icon="mdi:speedometer",
        ),
        # On while the node is holding the radar rail off for the #17 diagnostic. No
        # device_class: this is not a problem state, it is the test running as intended.
        cv.Optional(CONF_DIAG_ACTIVE): binary_sensor.binary_sensor_schema(
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            icon="mdi:flask-outline",
        ),
        cv.Optional(CONF_NODE_STATUS): binary_sensor.binary_sensor_schema(
            device_class=DEVICE_CLASS_CONNECTIVITY,
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
        cv.Optional(CONF_OTA_STATUS): text_sensor.text_sensor_schema(
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
        cv.Required(CONF_OTA_HEX_URL): _https_url,
        # Optional: a gateway with no diagnostic image configured just pushes the normal
        # one when the diagnostic button is pressed, rather than failing the build.
        cv.Optional(CONF_OTA_DIAG_HEX_URL): _https_url,
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(
        config[CONF_ID],
        config[CONF_CS_PIN],
        config[CONF_SCK_PIN],
        config[CONF_MISO_PIN],
        config[CONF_MOSI_PIN],
        config[CONF_IRQ_PIN],
        RF69_BANDS[config[CONF_FREQUENCY]],
        config[CONF_RESET_PIN],
        config[CONF_NODE_ID],
        config[CONF_NETWORK_ID],
        config[CONF_IS_RFM69HW],
        config[CONF_ENCRYPTION_KEY],
    )
    await cg.register_component(var, config)
    cg.add(var.set_node_timeout(config[CONF_TIMEOUT]))
    cg.add(var.set_ota_hex_url(config[CONF_OTA_HEX_URL]))
    if url := config.get(CONF_OTA_DIAG_HEX_URL):
        cg.add(var.set_ota_diag_hex_url(url))

    if conf := config.get(CONF_DISTANCE):
        cg.add(var.set_distance_sensor(await sensor.new_sensor(conf)))
    if conf := config.get(CONF_BATTERY_VOLTAGE):
        cg.add(var.set_battery_sensor(await sensor.new_sensor(conf)))
    if conf := config.get(CONF_RSSI):
        cg.add(var.set_rssi_sensor(await sensor.new_sensor(conf)))
    if conf := config.get(CONF_PACKET_COUNT):
        cg.add(var.set_packet_count_sensor(await sensor.new_sensor(conf)))
    if conf := config.get(CONF_FAST_MODE):
        cg.add(var.set_fast_mode_sensor(await binary_sensor.new_binary_sensor(conf)))
    if conf := config.get(CONF_DIAG_ACTIVE):
        cg.add(var.set_diag_active_sensor(await binary_sensor.new_binary_sensor(conf)))
    if conf := config.get(CONF_NODE_STATUS):
        cg.add(var.set_node_status_sensor(await binary_sensor.new_binary_sensor(conf)))
    if conf := config.get(CONF_OTA_STATUS):
        cg.add(var.set_ota_status_sensor(await text_sensor.new_text_sensor(conf)))

    # SPI is compiled out of the Arduino-as-ESP-IDF-component core by default
    # (CONFIG_ARDUINO_SELECTIVE_SPI); RFM69.h needs it, so opt back in explicitly.
    cg.add_library("SPI", None)
    cg.add_library("lowpowerlab/RFM69", "1.6.0")
    # lib_ldf_mode is off in ESPHome's generated config, so PlatformIO follows no #include
    # chains, and it compiles every cg.add_library() entry as an ISOLATED library -- one
    # cannot see another's headers even when both are declared correctly. "Network" (the
    # canonical name in ESPHome's own ARDUINO_DISABLED_LIBRARIES table -- the directory is
    # Network/, its library.properties says name=Networking, but ESPHome's selective-
    # compilation code keys off "Network") and "NetworkClientSecure" are declared for
    # completeness/parity with HTTPClient's own selective-compilation entry, but that alone
    # does not make HTTPClient.cpp's #include <NetworkClient.h> resolve -- SPI.h has zero
    # cross-library includes (confirmed: only ESP-IDF/core headers), so it never needed
    # this, but HTTPClient genuinely needs Network's and NetworkClientSecure's headers.
    # Force the include paths directly rather than relying on library resolution for this.
    cg.add_library("HTTPClient", None)
    cg.add_library("Network", None)
    cg.add_library("NetworkClientSecure", None)
    _pio_core_dir = os.environ.get("PLATFORMIO_CORE_DIR", os.path.expanduser("~/.platformio"))
    _arduino_libs_dir = os.path.join(
        _pio_core_dir, "packages", "framework-arduinoespressif32", "libraries"
    )
    for _lib_dir in ("Network", "NetworkClientSecure"):
        cg.add_build_flag(f"-I{os.path.join(_arduino_libs_dir, _lib_dir, 'src')}")
    # esp32's to_code() (toolchain: platformio branch) forces lib_compat_mode=strict, under
    # which PlatformIO's LDF silently drops any lib_deps entry whose library.json doesn't
    # list the target platform. RFM69's manifest says "atmelavr" only (stale — it builds fine
    # on ESP32) but does declare "arduino" as a compatible framework, so downgrading to "soft"
    # (which only skips the platform check) is enough; scalar platformio options are
    # last-write-wins, and this component's to_code() runs after esp32's.
    cg.add_platformio_option("lib_compat_mode", "soft")
