"""On-demand command handling.

Splits into two pieces so the *logic* is testable without a broker:

- `CommandProcessor` maps a command name to a handler callable and returns a
  `CommandResult`. It knows nothing about MQTT — the main loop injects the
  handlers (run inference, retrain, promote, rollback).
- `CommandQueue` is the thread-safe hand-off. The MQTT `on_message` callback
  (which paho runs on its network thread) enqueues the command name and its
  payload; the main loop drains and executes it single-threaded, so no two
  tasks ever overlap.

Command topics live under `<base>/cmd/<name>`; the trailing `<name>` selects the
handler. Most commands (the dashboard's buttons) publish an empty payload and
handlers ignore it; `annotate` is the one that reads it — the note text is the
whole point of that command, not incidental to it.
"""
from __future__ import annotations

import logging
import queue
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger("app.commands")

# The commands the dashboard publishes. Anything else is rejected.
KNOWN_COMMANDS = ("run_inference", "retrain", "promote", "rollback", "annotate")


@dataclass
class CommandResult:
    command: str
    ok: bool
    message: str
    ts: str

    def as_dict(self) -> dict:
        return {"command": self.command, "ok": self.ok, "message": self.message, "ts": self.ts}


class CommandQueue:
    """Thread-safe FIFO between the MQTT callback thread and the main loop."""

    def __init__(self) -> None:
        self._q: queue.Queue[tuple[str, str]] = queue.Queue()

    def offer(self, command: str, payload: str = "") -> None:
        self._q.put((command, payload))

    def drain(self) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        while True:
            try:
                items.append(self._q.get_nowait())
            except queue.Empty:
                break
        return items

    @staticmethod
    def command_from_topic(topic: str) -> str | None:
        """`creek/cmd/retrain` -> `retrain`; returns None if not a cmd topic."""
        parts = topic.rsplit("/", 2)
        if len(parts) >= 2 and parts[-2] == "cmd":
            return parts[-1]
        return None


class CommandProcessor:
    def __init__(self, handlers: dict[str, Callable[[str], str]]):
        """`handlers` maps a known command name to a callable taking the raw MQTT
        payload (empty string for the zero-argument commands) and returning a short
        human-readable message. Missing handlers are treated as unsupported."""
        self._handlers = handlers

    def handle(self, command: str, payload: str = "") -> CommandResult:
        ts = datetime.now().isoformat(timespec="seconds")
        if command not in KNOWN_COMMANDS:
            return CommandResult(command, False, f"unknown command: {command!r}", ts)
        handler = self._handlers.get(command)
        if handler is None:
            return CommandResult(command, False, f"no handler for {command!r}", ts)
        try:
            message = handler(payload) or "ok"
            return CommandResult(command, True, message, ts)
        except Exception as exc:  # a bad command must never kill the service
            log.exception("Command %s failed", command)
            return CommandResult(command, False, f"{type(exc).__name__}: {exc}", ts)
