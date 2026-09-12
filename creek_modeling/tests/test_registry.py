"""Plain-assert tests for ModelRegistry (no pytest dependency).

Run: python creek_modeling/tests/test_registry.py
Exercises the promote/rollback pointer logic against a temp registry.json.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.registry import ModelRegistry, RegistryError  # noqa: E402


def fresh_registry():
    tmp = Path(tempfile.mkdtemp())
    return ModelRegistry(tmp), tmp


def test_empty_defaults():
    reg, _ = fresh_registry()
    snap = reg.snapshot()
    assert snap["active_version"] is None
    assert snap["candidate_version"] is None
    assert snap["history"] == []
    assert snap["event_count"] == 0


def test_promote_sets_active_and_clears_candidate():
    reg, _ = fresh_registry()
    reg.set_candidate("v1", {"skill": 0.5})
    assert reg.snapshot()["candidate_version"] == "v1"
    assert reg.promote() == "v1"
    snap = reg.snapshot()
    assert snap["active_version"] == "v1"
    assert snap["candidate_version"] is None
    assert snap["active_metrics"] == {"skill": 0.5}


def test_promote_pushes_previous_active_to_history():
    reg, _ = fresh_registry()
    reg.set_candidate("v1")
    reg.promote()
    reg.set_candidate("v2")
    reg.promote()
    snap = reg.snapshot()
    assert snap["active_version"] == "v2"
    assert snap["history"] == ["v1"]


def test_rollback_restores_previous_and_keeps_demoted_as_candidate():
    reg, _ = fresh_registry()
    reg.set_candidate("v1")
    reg.promote()
    reg.set_candidate("v2")
    reg.promote()  # active=v2, history=[v1]
    assert reg.rollback() == "v1"
    snap = reg.snapshot()
    assert snap["active_version"] == "v1"
    assert snap["candidate_version"] == "v2"  # demoted, not lost
    assert snap["history"] == []


def test_promote_without_candidate_raises():
    reg, _ = fresh_registry()
    try:
        reg.promote()
    except RegistryError:
        pass
    else:
        raise AssertionError("expected RegistryError")


def test_rollback_without_history_raises():
    reg, _ = fresh_registry()
    reg.set_candidate("v1")
    reg.promote()  # active=v1, history=[]
    try:
        reg.rollback()
    except RegistryError:
        pass
    else:
        raise AssertionError("expected RegistryError")


def test_persistence_round_trip():
    reg, tmp = fresh_registry()
    reg.set_candidate("v1", {"a": 1})
    reg.promote()
    reg.set_event_count(7)
    reloaded = ModelRegistry(tmp)
    assert reloaded.active_version == "v1"
    assert reloaded.event_count == 7


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
