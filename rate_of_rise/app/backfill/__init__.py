"""Backfill from the v2 gateway's SD store into HA's recorder, the stage log and the dataset.

See docs/superpowers/specs/2026-10-07-gateway-v2-sd-backfill-design.md.
"""
import os
from pathlib import Path


def recorder_db_path() -> Path:
    """HA's recorder database as the add-on sees it (config.yaml maps homeassistant_config).
    RECORDER_DB overrides it for tests and local runs."""
    return Path(os.environ.get("RECORDER_DB", "/homeassistant/home-assistant_v2.db"))
