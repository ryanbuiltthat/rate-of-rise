"""Plain-assert tests for option loading.
Run: python rate_of_rise/tests/test_config.py"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Config, _optional  # noqa: E402

# Optional options that arrive as environment variables through `bashio::config`, paired
# with the Config attribute each lands in. Every one of these enables a source when it is
# non-empty, which is what makes bashio's "null" dangerous rather than merely untidy.
OPTIONAL_ENV = {
    "GOOGLE_FLOODS_API_KEY": "google_floods_api_key",
    "WU_API_KEY": "wu_api_key",
    "NWM_REACH_ID": "nwm_reach_id",
}


def load_with(**env):
    """Config.load() with `env` applied over the real environment, then restored."""
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update({k: v for k, v in env.items() if v is not None})
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
    try:
        return Config.load()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_bashio_renders_an_unset_optional_as_the_string_null():
    """The whole point. bashio does not give back an empty string for an option the
    operator never filled in -- it gives back "null", which is truthy, so an unset
    option reads as configured and enables a source that then fails every single poll
    against a bogus key. 0.21.0 shipped the Google Floods source that way."""
    assert _optional("null") == ""
    assert _optional(" null ") == ""


def test_a_real_value_survives_untouched():
    assert _optional("  AIzaSyExample  ") == "AIzaSyExample"
    assert _optional("01534000") == "01534000"


def test_absent_and_empty_are_empty():
    assert _optional(None) == ""
    assert _optional("") == ""
    assert _optional("   ") == ""


def test_a_value_merely_containing_null_is_not_treated_as_unset():
    """Only the exact sentinel. An API key is opaque and could legitimately contain
    these characters; blanking it would disable a correctly configured source."""
    assert _optional("nullify-me-123") == "nullify-me-123"
    assert _optional("NULL") == "NULL"


def test_every_optional_option_is_normalized_on_the_way_into_config():
    """The guard has to sit on the loader, not on each use site. It used to live in
    sources/__init__.py for nwm_reach_id alone, so the next optional added -- the Google
    Floods key -- simply did not get it."""
    for env_name, attr in OPTIONAL_ENV.items():
        cfg = load_with(**{env_name: "null"})
        assert getattr(cfg, attr) == "", f"{env_name} -> {attr}"


def test_a_configured_optional_still_arrives():
    cfg = load_with(GOOGLE_FLOODS_API_KEY="a-real-key", NWM_REACH_ID="01534000")
    assert cfg.google_floods_api_key == "a-real-key"
    assert cfg.nwm_reach_id == "01534000"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
