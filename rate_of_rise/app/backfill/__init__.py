"""Backfill from the v2 gateway's SD store into HA's recorder, the stage log and the dataset.

Off unless `gateway_store_url` is set: then nothing is constructed, no thread runs, and no
request is made. See docs/superpowers/specs/2026-10-07-gateway-v2-sd-backfill-design.md.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from ..stagelog import stage_log_dir
from .client import StoreClient
from .entity_map import parse_map
from .gaprows import GapFiller
from .reconciler import BackfillService, Destinations, Reconciler
from .statistics import HAWebsocket, StatisticsWriter

log = logging.getLogger("app.backfill")


def recorder_db_path() -> Path:
    """HA's recorder database as the add-on sees it (config.yaml maps homeassistant_config).
    RECORDER_DB overrides it for tests and local runs."""
    return Path(os.environ.get("RECORDER_DB", "/homeassistant/home-assistant_v2.db"))


def soil_channels(soil_entities: list[str], *maps: dict) -> dict[str, str]:
    """Which Ecowitt channel feeds which soil feature, worked out from the entity maps: the
    channel whose mapped entity is soil_moisture_entities[0] is near_house, [1] near_creek."""
    out: dict[str, str] = {}
    for m in maps:
        for fld, entity in (m.get("ecowitt") or {}).items():
            if not fld.startswith("soil_ch"):
                continue
            ch = fld[len("soil_ch"):]
            if len(soil_entities) >= 1 and entity == soil_entities[0]:
                out["near_house"] = ch
            if len(soil_entities) >= 2 and entity == soil_entities[1]:
                out["near_creek"] = ch
    return out


def build_backfill(cfg, publish, dataset, sources, data_dir: Path, share_dir: Path | None):
    if not cfg.gateway_store_url:
        publish("status/backfill", {"state": "off"})
        return None
    try:
        entity_map = parse_map(cfg.backfill_entity_map)
        shadow = parse_map(cfg.backfill_shadow_map)
    except ValueError as exc:
        log.error("gateway store backfill disabled: %s", exc)
        publish("status/backfill", {"state": f"error: {exc}"})
        return None
    client = StoreClient(cfg.gateway_store_url, cfg.gateway_store_token)
    db = recorder_db_path()
    stats = StatisticsWriter(db, HAWebsocket(cfg.ha_ws_url, cfg.supervisor_token).call)
    rain = sources.rain_accumulator() if sources is not None else None
    gaps = GapFiller(cfg, dataset, sources,
                     soil_channels(cfg.soil_moisture_entities, entity_map, shadow), rain)
    dest = Destinations(recorder_db=db if db.exists() else None, statistics=stats,
                        stage_dir=stage_log_dir(data_dir, share_dir), gaps=gaps)
    if dest.recorder_db is None:
        log.info("gateway store backfill: %s not found, so no recorder writes", db)
    rec = Reconciler(client, dest, entity_map, shadow, data_dir / "state" / "backfill.json")
    svc = BackfillService(client, rec, publish)
    svc.start()
    log.info("Gateway store backfill enabled for %s", cfg.gateway_store_url)
    return svc
