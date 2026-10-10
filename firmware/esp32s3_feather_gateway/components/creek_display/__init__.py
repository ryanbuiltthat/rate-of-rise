"""Creek gateway v2 display helpers: display_core.h, the pure logic behind display.yaml.

There is no config and no generated code. `creek_display:` in display.yaml loads the
component so ESPHome copies display_core.h into the build, where esphome.h includes it for
display.yaml's lambdas. The logic is host-tested in tests/test_display_core.cpp.
"""
import esphome.config_validation as cv

CODEOWNERS = ["@ryanbuiltthat"]

CONFIG_SCHEMA = cv.Schema({})


async def to_code(config):
    pass
