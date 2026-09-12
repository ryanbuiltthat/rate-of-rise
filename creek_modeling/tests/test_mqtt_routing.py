"""Validates the MQTT client against paho 2.x WITHOUT a broker.

Constructs the real client (proving the paho-2.x constructor works) and drives its
`on_message` callback with a fake message to prove `creek/cmd/<name>` routes onto
the command queue and non-command topics are ignored.

Run under an interpreter that has paho-mqtt>=2.1 installed:
    <venv>/python creek_modeling/tests/test_mqtt_routing.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.commands import CommandQueue  # noqa: E402
from app.mqtt_client import MqttClient  # noqa: E402


def make_client():
    # host/port never dialed — we only exercise the callbacks.
    return MqttClient("localhost", 1883, "", "", "creek")


def test_constructs_on_paho_2x():
    make_client()  # would raise on paho 2.x if the ctor were still 1.x-style


def test_on_message_routes_command_topic():
    q = CommandQueue()
    client = make_client()
    client.subscribe_commands(q)
    msg = SimpleNamespace(topic="creek/cmd/retrain", payload=b"")
    client._on_message(None, None, msg)
    assert q.drain() == [("retrain", "")]


def test_on_message_carries_the_payload_through():
    """The `annotate` command's whole point is the text typed into the dashboard —
    routing by topic is not enough if the payload gets dropped along the way."""
    q = CommandQueue()
    client = make_client()
    client.subscribe_commands(q)
    msg = SimpleNamespace(topic="creek/cmd/annotate",
                          payload="crest ~40min after upstream peak".encode("utf-8"))
    client._on_message(None, None, msg)
    assert q.drain() == [("annotate", "crest ~40min after upstream peak")]


def test_on_message_ignores_non_command_topic():
    q = CommandQueue()
    client = make_client()
    client.subscribe_commands(q)
    client._on_message(None, None, SimpleNamespace(topic="creek/flood_probability", payload=b""))
    assert q.drain() == []


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
