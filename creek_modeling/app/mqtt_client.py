"""MQTT client for the modeling service.

Publishes retained JSON model outputs / status to `<base_topic>/<name>` (HA MQTT
sensors subscribe), and optionally subscribes to `<base_topic>/cmd/#` so the
dashboard's buttons can drive the pipeline on demand. Broker host/credentials
come from Mosquitto service discovery (via run.sh).

Targets paho-mqtt 2.x (see requirements.txt): the client is constructed with an
explicit `CallbackAPIVersion` and uses the v2 callback signatures.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable

import paho.mqtt.client as mqtt

from .commands import CommandQueue

log = logging.getLogger("app.mqtt")


class MqttClient:
    def __init__(self, host: str, port: int, user: str, password: str, base_topic: str):
        self._base = base_topic.rstrip("/")
        self._availability_topic = f"{self._base}/status/availability"
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="creek_modeling",
            protocol=mqtt.MQTTv311,
        )
        if user:
            self._client.username_pw_set(user, password)
        # LWT: the broker publishes "offline" if the add-on drops, so discovery entities
        # go unavailable. We publish "online" on connect.
        self._client.will_set(self._availability_topic, "offline", qos=1, retain=True)
        self._host, self._port = host, port
        self._cmd_queue: CommandQueue | None = None
        self._on_ready_cbs: list[Callable[[], None]] = []
        self._client.on_connect = self._on_connect

    # --- lifecycle -------------------------------------------------------
    def connect(self) -> None:
        log.info("Connecting to MQTT %s:%s", self._host, self._port)
        self._client.connect(self._host, self._port, keepalive=60)
        self._client.loop_start()

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    # --- publish ---------------------------------------------------------
    def publish(self, name: str, payload: dict, retain: bool = True) -> None:
        topic = f"{self._base}/{name}"
        self._client.publish(topic, json.dumps(payload), qos=1, retain=retain)
        log.debug("Published %s -> %s", topic, payload)

    def publish_raw(self, topic: str, payload: str, retain: bool = True) -> None:
        """Publish to an absolute topic (not prefixed by base) — used for the
        `homeassistant/.../config` discovery messages and the availability topic."""
        self._client.publish(topic, payload, qos=1, retain=retain)

    # --- readiness -------------------------------------------------------
    def add_on_ready(self, callback: Callable[[], None]) -> None:
        """Register a callback to run each time the broker connection is (re)established —
        e.g. re-publish MQTT discovery. Register before `connect()`."""
        self._on_ready_cbs.append(callback)

    # --- subscribe (commands) -------------------------------------------
    def subscribe_commands(self, cmd_queue: CommandQueue) -> None:
        """Route `<base>/cmd/<name>` messages onto `cmd_queue`. The actual broker
        subscription happens in `_on_connect` so it survives reconnects; call this
        before `connect()`."""
        self._cmd_queue = cmd_queue
        self._client.on_message = self._on_message

    @property
    def _cmd_topic(self) -> str:
        return f"{self._base}/cmd/#"

    def _on_connect(self, _client, _userdata, _flags, reason_code, _properties) -> None:
        log.info("MQTT connected (rc=%s)", reason_code)
        # Mark the service available (pairs with the retained LWT "offline").
        self._client.publish(self._availability_topic, "online", qos=1, retain=True)
        if self._cmd_queue is not None:
            self._client.subscribe(self._cmd_topic, qos=1)
            log.info("Subscribed to %s", self._cmd_topic)
        for cb in self._on_ready_cbs:
            try:
                cb()
            except Exception:  # a discovery/publish hiccup must not drop the connection
                log.exception("on_ready callback failed")

    def _on_message(self, _client, _userdata, msg: mqtt.MQTTMessage) -> None:
        command = CommandQueue.command_from_topic(msg.topic)
        if command is None:
            return
        # Most commands are zero-argument buttons with an empty payload; `annotate`
        # is the one where this text is the entire command, not incidental to it.
        payload = msg.payload.decode("utf-8", errors="replace") if msg.payload else ""
        log.info("Command received on %s -> %s", msg.topic, command)
        if self._cmd_queue is not None:
            self._cmd_queue.offer(command, payload)
