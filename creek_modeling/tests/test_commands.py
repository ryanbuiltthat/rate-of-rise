"""Plain-assert tests for command handling (no pytest, no broker).

Run: python creek_modeling/tests/test_commands.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.commands import CommandProcessor, CommandQueue  # noqa: E402


def test_command_from_topic():
    assert CommandQueue.command_from_topic("creek/cmd/retrain") == "retrain"
    assert CommandQueue.command_from_topic("creek/cmd/run_inference") == "run_inference"
    assert CommandQueue.command_from_topic("creek/flood_probability") is None
    assert CommandQueue.command_from_topic("cmd/promote") == "promote"


def test_queue_offer_and_drain_fifo():
    q = CommandQueue()
    q.offer("run_inference")
    q.offer("retrain")
    assert q.drain() == [("run_inference", ""), ("retrain", "")]
    assert q.drain() == []  # drained empties it


def test_queue_carries_a_payload():
    """annotate is the one command where the payload is the whole point — the other
    four ignore it, but the queue itself must not drop it for any of them."""
    q = CommandQueue()
    q.offer("annotate", "crest ~40min after upstream peak")
    assert q.drain() == [("annotate", "crest ~40min after upstream peak")]


def test_known_command_runs_handler():
    calls = []
    proc = CommandProcessor({"retrain": lambda payload: calls.append("x") or "did retrain"})
    res = proc.handle("retrain")
    assert res.ok is True
    assert res.message == "did retrain"
    assert calls == ["x"]


def test_handler_receives_the_payload():
    received = []
    proc = CommandProcessor({"annotate": lambda payload: received.append(payload) or "ok"})
    proc.handle("annotate", "basement dry")
    assert received == ["basement dry"]


def test_handler_defaults_to_empty_payload():
    received = []
    proc = CommandProcessor({"retrain": lambda payload: received.append(payload)})
    proc.handle("retrain")
    assert received == [""]


def test_unknown_command_rejected():
    proc = CommandProcessor({})
    res = proc.handle("delete_everything")
    assert res.ok is False
    assert "unknown command" in res.message


def test_missing_handler_for_known_command():
    proc = CommandProcessor({})  # 'promote' is known but has no handler wired
    res = proc.handle("promote")
    assert res.ok is False
    assert "no handler" in res.message


def test_handler_exception_is_caught_not_raised():
    def boom(payload):
        raise ValueError("kaboom")

    proc = CommandProcessor({"retrain": boom})
    res = proc.handle("retrain")
    assert res.ok is False
    assert "ValueError" in res.message
    assert "kaboom" in res.message


def test_handler_returning_none_defaults_to_ok():
    proc = CommandProcessor({"run_inference": lambda payload: None})
    res = proc.handle("run_inference")
    assert res.ok is True
    assert res.message == "ok"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
