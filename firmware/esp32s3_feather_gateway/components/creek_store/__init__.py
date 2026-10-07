"""Creek gateway v2 store: every node packet and Ecowitt reading on SD, replayable over HTTP.

The C++ lives in creek_store.h (device) and store_core.h (pure, host-tested). This file wires
YAML to it. It is also what makes v2 differ from v1 at compile time: it defines
USE_RFM69_PACKET_HOOK, the only switch that compiles rfm69_gateway's packet hook in.
"""
import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import binary_sensor, esp32, i2c, number, sensor, text_sensor
from esphome.components import time as time_
from esphome.const import (
    CONF_ID,
    DEVICE_CLASS_PROBLEM,
    ENTITY_CATEGORY_DIAGNOSTIC,
    STATE_CLASS_MEASUREMENT,
    STATE_CLASS_TOTAL_INCREASING,
)
from esphome.core import CORE

DEPENDENCIES = ["rfm69_gateway", "i2c", "time"]
AUTO_LOAD = ["binary_sensor", "json", "sensor", "text_sensor", "web_server_base"]
CODEOWNERS = ["@ryanbuiltthat"]

creek_store_ns = cg.esphome_ns.namespace("creek_store")
CreekStore = creek_store_ns.class_("CreekStore", cg.Component, i2c.I2CDevice)
Rfm69Gateway = cg.esphome_ns.namespace("rfm69_gateway").class_("Rfm69Gateway", cg.Component)

CONF_RADIO_ID = "radio_id"
CONF_TIME_ID = "time_id"
CONF_MOUNT_HEIGHT_ID = "mount_height_id"
CONF_SD_CS_PIN = "sd_cs_pin"
CONF_TOKEN = "token"
CONF_ECOWITT_HOST = "ecowitt_host"
CONF_ECOWITT_INTERVAL = "ecowitt_interval"
CONF_BLANKING_MM = "blanking_mm"
CONF_OVERRANGE_SLACK_MM = "overrange_slack_mm"
CONF_SD_FAULT = "sd_fault"
CONF_FREE_SPACE = "free_space"
CONF_CLOCK_SOURCE = "clock_source"
CONF_NODE_RECORDS = "node_records"
CONF_ECOWITT_RECORDS = "ecowitt_records"
CONF_ECOWITT_FAILURES = "ecowitt_failures"

_DIAG = {"entity_category": ENTITY_CATEGORY_DIAGNOSTIC}

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(CreekStore),
            cv.Required(CONF_RADIO_ID): cv.use_id(Rfm69Gateway),
            cv.Required(CONF_TIME_ID): cv.use_id(time_.RealTimeClock),
            cv.Required(CONF_MOUNT_HEIGHT_ID): cv.use_id(number.Number),
            cv.Required(CONF_SD_CS_PIN): cv.int_range(min=0, max=48),
            # Same value as the add-on's gateway_store_token. 16+ chars so a placeholder
            # cannot pass validation.
            cv.Required(CONF_TOKEN): cv.All(cv.string_strict, cv.Length(min=16)),
            cv.Optional(CONF_ECOWITT_HOST, default=""): cv.string,
            cv.Optional(CONF_ECOWITT_INTERVAL, default="60s"): cv.positive_time_period_milliseconds,
            cv.Optional(CONF_BLANKING_MM, default=150): cv.float_,
            cv.Optional(CONF_OVERRANGE_SLACK_MM, default=1000): cv.float_,
            cv.Optional(CONF_SD_FAULT): binary_sensor.binary_sensor_schema(
                device_class=DEVICE_CLASS_PROBLEM, icon="mdi:micro-sd", **_DIAG),
            cv.Optional(CONF_FREE_SPACE): sensor.sensor_schema(
                unit_of_measurement="MB", accuracy_decimals=0,
                state_class=STATE_CLASS_MEASUREMENT, icon="mdi:micro-sd", **_DIAG),
            cv.Optional(CONF_CLOCK_SOURCE): text_sensor.text_sensor_schema(
                icon="mdi:clock-check-outline", **_DIAG),
            cv.Optional(CONF_NODE_RECORDS): sensor.sensor_schema(
                accuracy_decimals=0, state_class=STATE_CLASS_TOTAL_INCREASING,
                icon="mdi:database", **_DIAG),
            cv.Optional(CONF_ECOWITT_RECORDS): sensor.sensor_schema(
                accuracy_decimals=0, state_class=STATE_CLASS_TOTAL_INCREASING,
                icon="mdi:database", **_DIAG),
            cv.Optional(CONF_ECOWITT_FAILURES): sensor.sensor_schema(
                accuracy_decimals=0, state_class=STATE_CLASS_TOTAL_INCREASING,
                icon="mdi:weather-cloudy-alert", **_DIAG),
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
    .extend(i2c.i2c_device_schema(0x68))   # PCF8523
)


def _require_vfs_dir(config):
    # ESPHome turns CONFIG_VFS_SUPPORT_DIR off, which drops the newlib rmdir/opendir that the
    # Arduino FS library (under SD) links against. esp32's own to_code reads this flag before
    # any other component's to_code runs, so it has to be raised during validation.
    esp32.require_vfs_dir()
    return config


FINAL_VALIDATE_SCHEMA = _require_vfs_dir


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await i2c.register_i2c_device(var, config)

    cg.add(var.set_radio(await cg.get_variable(config[CONF_RADIO_ID])))
    cg.add(var.set_time(await cg.get_variable(config[CONF_TIME_ID])))
    cg.add(var.set_mount_height(await cg.get_variable(config[CONF_MOUNT_HEIGHT_ID])))
    cg.add(var.set_sd_cs_pin(config[CONF_SD_CS_PIN]))
    cg.add(var.set_token(config[CONF_TOKEN]))
    cg.add(var.set_ecowitt_host(config[CONF_ECOWITT_HOST]))
    cg.add(var.set_ecowitt_interval_ms(config[CONF_ECOWITT_INTERVAL]))
    cg.add(var.set_blanking_mm(config[CONF_BLANKING_MM]))
    cg.add(var.set_overrange_slack_mm(config[CONF_OVERRANGE_SLACK_MM]))
    cg.add(var.set_device_name(CORE.name))

    if conf := config.get(CONF_SD_FAULT):
        cg.add(var.set_sd_fault_sensor(await binary_sensor.new_binary_sensor(conf)))
    if conf := config.get(CONF_FREE_SPACE):
        cg.add(var.set_free_space_sensor(await sensor.new_sensor(conf)))
    if conf := config.get(CONF_CLOCK_SOURCE):
        cg.add(var.set_clock_source_sensor(await text_sensor.new_text_sensor(conf)))
    if conf := config.get(CONF_NODE_RECORDS):
        cg.add(var.set_node_records_sensor(await sensor.new_sensor(conf)))
    if conf := config.get(CONF_ECOWITT_RECORDS):
        cg.add(var.set_ecowitt_records_sensor(await sensor.new_sensor(conf)))
    if conf := config.get(CONF_ECOWITT_FAILURES):
        cg.add(var.set_ecowitt_failures_sensor(await sensor.new_sensor(conf)))

    # The one switch that compiles rfm69_gateway's packet hook in. v1 never sets it.
    cg.add_define("USE_RFM69_PACKET_HOOK")
    cg.add_library("FS", None)
    cg.add_library("SD", None)
    cg.add_library("SPI", None)
    # No esp32.include_builtin_idf_component() calls are needed: fatfs and vfs are not in
    # ESPHome's EXCLUDE_COMPONENTS. The directory support is a Kconfig switch; see below.
