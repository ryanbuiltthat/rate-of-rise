"""Tests for NWS QPF parsing/proration (no HTTP). Run: python creek_modeling/tests/test_nws.py"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.nws import NwsQpf, _duration_seconds  # noqa: E402


def test_duration_parsing():
    assert _duration_seconds("PT6H") == 21600
    assert _duration_seconds("PT1H") == 3600
    assert _duration_seconds("PT30M") == 1800
    assert _duration_seconds("P1DT6H") == 108000
    assert _duration_seconds("PT0H") == 0


NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
VALUES = [
    {"validTime": "2026-01-01T00:00:00+00:00/PT6H", "value": 25.4},   # fully in next 6h
    {"validTime": "2026-01-01T06:00:00+00:00/PT6H", "value": 25.4},   # in 24h, not 6h
    {"validTime": "2025-12-31T21:00:00+00:00/PT6H", "value": 20.0},   # 21:00-03:00, half overlaps
    {"validTime": "2026-01-01T12:00:00+00:00/PT6H", "value": None},   # ignored
]


def test_qpf_6h_prorates_partial_intervals():
    # A fully in (25.4mm) + C in progress, counted in full (20mm) = 45.4mm.
    # C runs 21:00-03:00 and its remaining half sits inside the 6 h window, so all of its
    # forecast rain is still ahead of us — see _qpf_within.
    got = NwsQpf._qpf_within(VALUES, NOW, 6)
    assert abs(got - (45.4 / 25.4)) < 1e-3, got


def test_qpf_24h_sums_intervals():
    # A(25.4) + B(25.4) + C(20, in progress) = 70.8mm
    got = NwsQpf._qpf_within(VALUES, NOW, 24)
    assert abs(got - (70.8 / 25.4)) < 1e-3, got


def test_qpf_zero_when_no_overlap():
    future = [{"validTime": "2026-01-05T00:00:00+00:00/PT6H", "value": 50.0}]
    assert NwsQpf._qpf_within(future, NOW, 24) == 0.0


def test_wholly_past_interval_is_ignored():
    past = [{"validTime": "2025-12-31T00:00:00+00:00/PT6H", "value": 50.0}]
    assert NwsQpf._qpf_within(past, NOW, 24) == 0.0


def test_in_progress_interval_does_not_decay_as_it_elapses():
    """The regression this change exists to prevent: a 6 h block holding the evening's
    forecast used to shrink toward zero over the afternoon, bottoming out exactly when
    the storm arrived."""
    block = [{"validTime": "2026-01-01T00:00:00+00:00/PT6H", "value": 25.4}]
    fresh = NwsQpf._qpf_within(block, NOW, 6)                              # 0h elapsed
    stale = NwsQpf._qpf_within(block, NOW + timedelta(hours=5), 6)         # 5h elapsed
    assert abs(fresh - 1.0) < 1e-3, fresh
    assert abs(stale - 1.0) < 1e-3, stale     # was 1/6 of that under full-span proration


def test_future_intervals_still_prorate_on_the_trailing_edge():
    """Only the leading edge changed. A block reaching past the window must still be
    clipped, or a 6 h figure would count rain forecast well outside the next 6 hours."""
    block = [{"validTime": "2026-01-01T04:00:00+00:00/PT6H", "value": 25.4}]
    got = NwsQpf._qpf_within(block, NOW, 6)   # only 04:00-06:00 of 04:00-10:00 is inside
    assert abs(got - (25.4 / 3) / 25.4) < 1e-3, got


def test_in_progress_interval_reaching_past_the_window_is_still_clipped():
    # Starts 2h ago, runs 12h, window is 6h -> 6 of its 10 remaining hours are inside.
    block = [{"validTime": "2025-12-31T22:00:00+00:00/PT12H", "value": 25.4}]
    got = NwsQpf._qpf_within(block, NOW, 6)
    assert abs(got - 0.6) < 1e-3, got


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
