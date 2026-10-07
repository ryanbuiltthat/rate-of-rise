"""Service entrypoint: fast inference loop + nightly batch + on-demand commands.

Fast loop (every `fast_loop_minutes`): build features -> predict -> publish over
MQTT -> append to dataset. Once per day at `nightly_retrain_hour`: run the batch
(dataset consolidation + recalibrate/retrain in Phase 4). Between and during the
loop's sleep it drains the command queue, so dashboard buttons (run inference,
retrain, promote, rollback) are honored within a few seconds. Command execution
happens on this single thread, so no two tasks ever overlap.

Pipeline/model state is published to `creek/status/*` so the HA dashboard can show
what the service is doing and surface the result of each command.
"""
from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import datetime

from .commands import CommandProcessor, CommandQueue
from .config import DATA_DIR, SHARE_DIR, Config
from .crest import TOLERANCE_IN as CREST_TOLERANCE_IN, CrestModel, train_crest
from .dataset import DatasetWriter
from .discovery import DiscoveryPublisher
from .features import DERIVED_KEYS, FeatureBuilder
from .health import HealthTracker
from .lag import estimate_lag, load_lag, save_lag
from .ha import HAClient
from .model import Model
from .mqtt_client import MqttClient
from .registry import ModelRegistry, promotion_readiness
from .rise import HORIZONS_MIN, LABELS as RISE_LABELS, RiseModels, train_rise
from .stagelog import StageLogger, stage_log_dir
from .storms import StormLog
from .backfill import recorder_db_path
from .backfill.undo import undo as backfill_undo
from .sources import FEATURE_KEYS, SourceCoordinator
from . import train
from .tiers import RadarWatchHold, compute_tier

log = logging.getLogger("app")

_running = True


def _handle_sigterm(_signum, _frame):
    global _running
    log.info("SIGTERM received — shutting down cleanly.")
    _running = False


def _now_iso() -> str:
    # tz-aware local time so HA can consume last_inference_at / last_nightly_at /
    # ran_at as proper `device_class: timestamp` sensors.
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _publish_pipeline(mqtt: MqttClient, status: dict, state: str, task: str) -> None:
    status["state"] = state
    status["task"] = task
    mqtt.publish("status/pipeline", dict(status))


def _publish_registry(mqtt: MqttClient, registry: ModelRegistry) -> None:
    mqtt.publish("status/registry", registry.snapshot())


def _run_inference_once(
    features: FeatureBuilder, model: Model, dataset: DatasetWriter,
    mqtt: MqttClient, status: dict, health: HealthTracker = None, sources=None,
    storms: StormLog = None, ror_confirm_samples: int | None = None,
    rise_models: RiseModels = None, radar_hold: RadarWatchHold = None,
    crest_model: CrestModel = None,
) -> str:
    row = features.build()
    pred = model.predict(row)
    shadow = model.shadow(row)
    ml_value, ml_version = shadow if shadow is not None else (None, None)
    mqtt.publish("flood_probability", {"value": pred.flood_probability, "method": pred.method,
                                       "ml_value": ml_value, "ml_version": ml_version})
    rise = rise_models.predict(row) if rise_models is not None else {}
    for horizon, payload in rise.items():
        mqtt.publish(f"rise/{RISE_LABELS[horizon]}", payload)
    crest = crest_model.predict(row) if crest_model is not None else {"value": None}
    mqtt.publish("predicted_crest", crest)
    tier, label, reasons = compute_tier(row, pred.flood_probability, ror_confirm_samples,
                                        radar_hold)
    mqtt.publish("alert_tier", {"value": tier, "label": label, "reasons": reasons,
                                "why": "; ".join(reasons) or "nothing elevated"})
    mqtt.publish("features",
                 {k: getattr(row, k) for k in FEATURE_KEYS + DERIVED_KEYS})
    # Soil mean + ponding come from the feature row too, so the HA package no longer has
    # to recompute them from the raw Ecowitt probes.
    mqtt.publish("soil", {"mean_pct": row.soil_moisture_mean_pct,
                          "ponding": row.ponding_flag,
                          "near_house_pct": row.soil_moisture_near_house_pct,
                          "near_creek_pct": row.soil_moisture_near_creek_pct})
    if health is not None and sources is not None:
        mqtt.publish("status/health",
                     health.evaluate(row, sources.health(), sources.configured()))
    dataset.append_row(row, {"alert_tier": tier, "flood_probability": pred.flood_probability,
                             "probability_method": pred.method, "ml_probability": ml_value,
                             "ml_version": ml_version,
                             **{f"rise_p_{RISE_LABELS[h]}": v["value"]
                                for h, v in rise.items()},
                             "predicted_crest_ft": crest.get("value"),
                             "predicted_crest_high_ft": crest.get("high_ft"),
                             "predicted_time_to_crest_min": crest.get("time_to_crest_min"),
                             "crest_version": crest.get("version")})
    if storms is not None:
        if storms.observe(row, tier) is not None:
            _publish_storms(mqtt, storms)
    status["last_inference_at"] = _now_iso()
    return f"p={pred.flood_probability} tier={tier} method={pred.method}"


def _publish_storms(mqtt: MqttClient, storms: StormLog) -> None:
    latest = storms.latest()
    mqtt.publish("status/storms", {
        "event_count": storms.count(),
        "open": bool(latest and latest.get("ended_ts") is None),
        "latest": latest,
        # What the "annotate" text entity will actually target — see storms.latest_closed
        # for why that can differ from `latest` once a second storm has started.
        "latest_closed": storms.latest_closed(),
    })


def _publish_lag(mqtt: MqttClient, lag: dict) -> None:
    mqtt.publish("status/lag", lag)
    mqtt.publish("lag_estimate", {"value": lag["lag_minutes"],
                                  "response": lag["response"],
                                  "correlation": lag["correlation"],
                                  "reason": lag["reason"]})


def _publish_model_health(cfg: Config, mqtt: MqttClient, rows: int, model: Model,
                          ran_at: str | None) -> str:
    # Reports model.active_method rather than re-deriving "events >= gate" here, so a
    # promoted version whose artifact failed to load (Model._load_promoted) shows up as
    # "threshold" — what predict() will actually do — not as a method with nothing behind it.
    method = model.active_method
    mqtt.publish("model_health", {
        "dataset_rows": rows,
        "event_count": model.event_count(),
        "min_events_for_ml": cfg.min_events_for_ml,
        "active_method": method,
        # Whether a promoted model is allowed to drive the tiers, and which model the
        # shadow probability comes from — so "threshold" above is never ambiguous about
        # whether an ML model exists at all.
        "ml_drives_alerts": cfg.ml_drives_alerts,
        "shadow_model": model.shadow_version,
        "ran_at": ran_at,
    })
    return method


def _retrain(cfg: Config, dataset: DatasetWriter, registry: ModelRegistry,
            model: Model, data_dir) -> str:
    """Fit a new candidate if the storm log has cleared the ML gate (spec §5).

    Never touches `registry.active_*` — only `set_candidate`. Promoting a candidate to
    active is the dashboard's Promote button (`ModelRegistry.promote`), a human decision
    the spec calls "post-storm manual review", not something a nightly job does to itself.
    """
    if model.event_count() < cfg.min_events_for_ml:
        return f"skipped (events={model.event_count()} < min_events_for_ml={cfg.min_events_for_ml})"
    result = train.train(dataset.frame(), data_dir)
    if result is None:
        # Expected, not an error, until real storms give the label positive examples —
        # see train.py's module docstring. The event count alone does not guarantee that;
        # min_events_for_ml counts storms, not Warning-tier crossings within them.
        return "no candidate produced (see log — commonly too few positive examples yet)"
    registry.set_candidate(result.version, result.metrics)
    ready, reason = promotion_readiness(result.metrics)
    verdict = "ready to promote" if ready else "not ready"
    return f"candidate {result.version} {verdict} ({reason}): {result.metrics}"


def _storm_windows(storms: StormLog | None) -> list[tuple[float, float]]:
    """(start, end) of every storm on record; an open one runs to its last rain (or now)."""
    if storms is None:
        return []
    out = []
    for e in storms.events(limit=100_000):
        end = e.get("ended_ts") or e.get("last_rain_ts") or time.time()
        out.append((float(e["started_ts"]), float(end)))
    return out


def _retrain_rise(cfg: Config, dataset: DatasetWriter, rise_models: RiseModels | None,
                  storms: StormLog | None, data_dir) -> str:
    """Refit both rise-probability models (rise.py). Not gated on min_events_for_ml: the
    rises themselves are the evidence, and train_rise refuses when there are too few.
    The newest fit is used at once — these never drive a tier, so there is no Promote."""
    if rise_models is None:
        return "off"
    frame = dataset.frame()
    windows = _storm_windows(storms)
    parts = []
    for horizon in HORIZONS_MIN:
        threshold = cfg.rise_thresholds[horizon]
        try:
            result = train_rise(frame, data_dir, horizon, threshold, windows,
                                cfg.rate_of_rise_confirm_samples, cfg.stage_max_age_minutes)
        except Exception:     # a failed fit leaves the previous model answering
            log.exception("rise model training failed for %d min", horizon)
            parts.append(f"{RISE_LABELS[horizon]}: failed (see log)")
            continue
        if result.version:
            m = result.metrics
            parts.append(f"{RISE_LABELS[horizon]}: {result.version} skill={m.get('brier_skill')} "
                         f"rises={m.get('episodes')} flagged={m.get('episodes_flagged')}")
        else:
            rise_models.set_reason(horizon, result.reason)
            parts.append(f"{RISE_LABELS[horizon]}: {result.reason}")
    rise_models.reload()
    return "; ".join(parts)


def _retrain_crest(cfg: Config, dataset: DatasetWriter, crest_model: CrestModel | None,
                   storms: StormLog | None, data_dir) -> str:
    """Refit the crest model (crest.py). Like the rise models: not gated on
    min_events_for_ml, used at once, never drives a tier."""
    if crest_model is None:
        return "off"
    try:
        result = train_crest(dataset.frame(), data_dir, _storm_windows(storms),
                             cfg.rate_of_rise_confirm_samples, cfg.stage_max_age_minutes)
    except Exception:     # a failed fit leaves the previous model answering
        log.exception("crest model training failed")
        return "failed (see log)"
    if result.version:
        m = result.metrics
        out = (f"{result.version} skill={m.get('skill')} rises={m.get('episodes')} "
               f"within_{CREST_TOLERANCE_IN:g}in={m.get('episodes_within_tolerance')}")
    else:
        crest_model.set_reason(result.reason)
        out = result.reason
    crest_model.reload()
    return out


def _nightly_batch(
    cfg: Config, dataset: DatasetWriter, mqtt: MqttClient, model: Model,
    registry: ModelRegistry, status: dict, storms: StormLog = None,
    rise_models: RiseModels = None, crest_model: CrestModel = None,
) -> str:
    # Fold yesterday's part files into the Parquet dataset (§4 "append day's data").
    rows = dataset.consolidate()
    if storms is not None:
        # The storm log is the source of truth for the ML gate (§5).
        registry.set_event_count(storms.count())
        _publish_storms(mqtt, storms)

    # First lag/response estimate (§7 Phase 3). Falls back to a downstream gauge while the
    # creek node is missing — that number is the sanity check, not the creek's lag.
    lag = estimate_lag(dataset.frame())
    save_lag(DATA_DIR, lag)      # so a restart republishes it instead of showing unknown
    _publish_lag(mqtt, lag)

    retrain_result = _retrain(cfg, dataset, registry, model, DATA_DIR)
    rise_result = _retrain_rise(cfg, dataset, rise_models, storms, DATA_DIR)
    crest_result = _retrain_crest(cfg, dataset, crest_model, storms, DATA_DIR)

    log.info("Nightly batch: dataset=%d rows, events=%d, lag=%s, retrain=%s, rise=%s, "
             "crest=%s", rows, model.event_count(), lag["lag_minutes"], retrain_result,
             rise_result, crest_result)
    ran_at = _now_iso()
    status["last_nightly_at"] = ran_at
    method = _publish_model_health(cfg, mqtt, rows, model, ran_at)
    _publish_registry(mqtt, registry)
    return (f"rows={rows} events={model.event_count()} lag={lag['lag_minutes']} "
            f"method={method} retrain={retrain_result} rise=[{rise_result}] "
            f"crest=[{crest_result}]")


def _process_commands(
    processor: CommandProcessor, cmd_queue: CommandQueue, mqtt: MqttClient, status: dict,
) -> None:
    for command, payload in cmd_queue.drain():
        _publish_pipeline(mqtt, status, "running", command)
        result = processor.handle(command, payload)
        mqtt.publish("status/command_result", result.as_dict(), retain=False)
        if not result.ok:
            status["last_error"] = f"{command}: {result.message}"
        log.info("Command %s -> ok=%s %s", command, result.ok, result.message)
        _publish_pipeline(mqtt, status, "idle", "none")


def main() -> int:
    cfg = Config.load()
    logging.basicConfig(
        level=cfg.py_log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    signal.signal(signal.SIGTERM, _handle_sigterm)

    data_dir = DATA_DIR
    log.info("Rate of Rise service starting (loop=%dm)", cfg.fast_loop_minutes)

    ha = HAClient(cfg.ha_api_url, cfg.supervisor_token)
    if not ha.ping():
        log.error("Cannot reach HA Core via Supervisor proxy — check homeassistant_api + token.")
        return 1

    registry = ModelRegistry(data_dir)
    cmd_queue = CommandQueue()
    mqtt = MqttClient(cfg.mqtt_host, cfg.mqtt_port, cfg.mqtt_user, cfg.mqtt_pass, cfg.mqtt_base_topic)
    mqtt.subscribe_commands(cmd_queue)   # subscription happens on connect
    # Auto-provision the creek_* entities via MQTT discovery (re-published on every
    # (re)connect, so HA picks them up without any package/config edit).
    discovery = DiscoveryPublisher(mqtt.publish_raw, cfg.mqtt_base_topic)
    mqtt.add_on_ready(discovery.publish_all)
    mqtt.connect()

    sources = SourceCoordinator(cfg, ha, data_dir)
    features = FeatureBuilder(cfg, ha, sources, state_path=data_dir / "last_stage.json")
    health = HealthTracker()
    model = Model(cfg, registry, data_dir)
    rise_models = RiseModels(data_dir, cfg.rise_thresholds, cfg.rate_of_rise_confirm_samples)
    crest_model = CrestModel(data_dir, cfg.rate_of_rise_confirm_samples)
    radar_hold = RadarWatchHold()     # one per service: the memory compute_tier lacks
    dataset = DatasetWriter(data_dir)
    storms = StormLog(
        data_dir, SHARE_DIR,
        start_rain_1h_in=cfg.storm_start_rain_1h_in,
        continue_rain_1h_in=cfg.storm_continue_rain_1h_in,
        quiet_seconds=cfg.storm_quiet_hours * 3600,
    )
    stage_log = StageLogger(ha, cfg.stage_entity, stage_log_dir(data_dir, SHARE_DIR))
    log.info("High-resolution stage record at %s", stage_log_dir(data_dir, SHARE_DIR))

    status = {
        "state": "idle",
        "task": "none",
        "last_inference_at": None,
        "last_nightly_at": None,
        "last_error": None,
    }

    def refresh_health():
        # Model Health was published only at startup and by the nightly batch, so after a
        # Rollback it went on naming the model that had just been removed until 3 AM — the
        # dashboard could not answer "what is live right now?" when it mattered most.
        _publish_model_health(cfg, mqtt, dataset.row_count(), model,
                              status.get("last_nightly_at"))

    processor = CommandProcessor(
        {
            "run_inference": lambda payload: _run_inference_once(
                features, model, dataset, mqtt, status, health, sources, storms,
                cfg.rate_of_rise_confirm_samples, rise_models, radar_hold, crest_model),
            "retrain": lambda payload: _nightly_batch(
                cfg, dataset, mqtt, model, registry, status, storms, rise_models,
                crest_model),
            "promote": lambda payload: _promote(mqtt, registry, refresh_health,
                                                cfg.ml_drives_alerts),
            "rollback": lambda payload: _rollback(mqtt, registry, refresh_health),
            "annotate": lambda payload: _annotate(mqtt, storms, payload),
            "backfill_undo": lambda payload: _backfill_undo(payload),
        }
    )

    # Prime the dashboard sensors immediately.
    # Prime the dashboard. model_health and the lag estimate are otherwise only published
    # by the nightly batch, so without this their entities sit at unknown until 3 AM.
    _publish_pipeline(mqtt, status, "idle", "none")
    _publish_registry(mqtt, registry)
    _publish_storms(mqtt, storms)
    _publish_model_health(cfg, mqtt, dataset.row_count(), model, None)
    _publish_lag(mqtt, load_lag(data_dir))

    # A rise model that is missing (first start, a wiped /data) or was trained for another
    # threshold (the option changed) is refitted now, not at the next nightly run — it
    # takes seconds, and otherwise the Rise Probability sensors sit at unknown all day.
    if rise_models.needs_training():
        _publish_pipeline(mqtt, status, "running", "retrain")
        log.info("Rise models missing or stale — training at startup: %s",
                 _retrain_rise(cfg, dataset, rise_models, storms, data_dir))
        _publish_pipeline(mqtt, status, "idle", "none")
    if crest_model.needs_training():
        _publish_pipeline(mqtt, status, "running", "retrain")
        log.info("Crest model missing — training at startup: %s",
                 _retrain_crest(cfg, dataset, crest_model, storms, data_dir))
        _publish_pipeline(mqtt, status, "idle", "none")

    last_nightly_day: int | None = None
    interval = max(1, cfg.fast_loop_minutes) * 60

    try:
        while _running:
            _publish_pipeline(mqtt, status, "running", "inference")
            try:
                _run_inference_once(
                    features, model, dataset, mqtt, status, health, sources, storms,
                    cfg.rate_of_rise_confirm_samples, rise_models, radar_hold, crest_model)
            except Exception:  # a transient feature/predict error must not kill the loop
                log.exception("Inference failed")
                status["last_error"] = "inference failed (see log)"
            _publish_pipeline(mqtt, status, "idle", "none")

            _process_commands(processor, cmd_queue, mqtt, status)

            now = datetime.now()
            if now.hour == cfg.nightly_retrain_hour and now.day != last_nightly_day:
                _publish_pipeline(mqtt, status, "running", "retrain")
                _nightly_batch(cfg, dataset, mqtt, model, registry, status, storms,
                               rise_models, crest_model)
                _publish_pipeline(mqtt, status, "idle", "none")
                last_nightly_day = now.day

            # Sleep in short slices so SIGTERM and commands are honored promptly.
            waited = 0
            while _running and waited < interval:
                time.sleep(min(5, interval - waited))
                waited += 5
                _process_commands(processor, cmd_queue, mqtt, status)
                try:
                    stage_log.tick()
                except Exception:   # a record, not an input: never let it stop the loop
                    log.exception("stage log tick failed")
    finally:
        mqtt.disconnect()
        log.info("Stopped.")
    return 0


def _promote(mqtt: MqttClient, registry: ModelRegistry, refresh_health=None,
             drives_alerts: bool = True) -> str:
    """Promoting an unvalidated model is allowed but never silent — the caveat leads the
    command result, which is what the dashboard's Last Command sensor shows."""
    version = registry.promote()
    _publish_registry(mqtt, registry)
    if refresh_health is not None:
        refresh_health()
    caveat = registry.warning()
    where = ("" if drives_alerts else
             " — shadow only: ml_drives_alerts is off, so alerts still use the threshold "
             "estimate")
    return (f"WARNING: {caveat}{where}" if caveat else f"promoted {version}{where}")


def _rollback(mqtt: MqttClient, registry: ModelRegistry, refresh_health=None) -> str:
    version = registry.rollback()
    _publish_registry(mqtt, registry)
    if refresh_health is not None:
        refresh_health()
    # A None version is the threshold estimate, not a missing answer — say so, since
    # this is what the operator sees on the dashboard after backing out a bad model.
    return f"rolled back to {version or 'the threshold estimate (no ML model active)'}"


def _backfill_undo(payload: str) -> str:
    """Take every backfilled row back out of HA's recorder (app/backfill/undo.py). Payload:
    an ISO-8601 time to undo from, or empty for all of them. Raises on a bad time or an
    unsupported recorder schema; CommandProcessor reports either."""
    text = payload.strip()
    since = datetime.fromisoformat(text).timestamp() if text else 0.0
    n = backfill_undo(recorder_db_path(), since)
    return f"removed {n} backfilled recorder row(s) since {text or 'the beginning'}"


def _annotate(mqtt: MqttClient, storms: StormLog, payload: str) -> str:
    """Write dashboard-submitted notes onto the most recent *closed* storm.

    Raises rather than returning a failure string on purpose: CommandProcessor.handle
    already catches and reports exceptions uniformly for every command, so this stays
    consistent with the others instead of inventing a second error-reporting path.
    """
    text = payload.strip()
    if not text:
        raise ValueError("no annotation text provided")
    event = storms.latest_closed()
    if event is None:
        raise ValueError("no closed storm event to annotate yet")
    storms.annotate(event["id"], text)
    _publish_storms(mqtt, storms)   # so the "ready to annotate" sensor reflects it live
    return f"storm #{event['id']} annotated"


if __name__ == "__main__":
    sys.exit(main())
