"""Tests for the Antecedent Precipitation Index (spec §4/§5).
Run: python rate_of_rise/tests/test_apindex.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.apindex import DEFAULT_K, MAX_GAP_DAYS, PrecipIndex  # noqa: E402

DAY = 86400.0


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance_days(self, days):
        self.t += days * DAY
        return self


def make(clock=None, path=None):
    clock = clock or Clock()
    path = path or Path(tempfile.mkdtemp()) / "api.json"
    return PrecipIndex(path, now_fn=clock), clock, path


def test_starts_at_zero_and_first_sample_only_accumulates():
    api, _, _ = make()
    assert api.update(0.5) == 0.5      # nothing to decay yet


def test_rain_accumulates_within_a_sample_interval():
    api, clock, _ = make()
    api.update(0.5)
    clock.advance_days(0)
    assert api.update(0.25) == 0.75    # no elapsed time -> no decay


def test_decays_by_the_daily_constant():
    api, clock, _ = make()
    api.update(1.0)
    clock.advance_days(1)
    assert api.update(0.0) == round(DEFAULT_K, 3)


def test_decay_is_continuous_not_a_daily_step():
    # Half a day should decay by k**0.5, not by k or by nothing.
    api, clock, _ = make()
    api.update(1.0)
    clock.advance_days(0.5)
    assert api.update(0.0) == round(DEFAULT_K ** 0.5, 3)


def test_decay_compounds_over_a_dry_spell():
    api, clock, _ = make()
    api.update(2.0)
    clock.advance_days(7)
    assert api.update(0.0) == round(2.0 * DEFAULT_K ** 7, 3)


def test_state_survives_a_restart_and_keeps_decaying():
    clock = Clock()
    api, _, path = make(clock)
    api.update(3.0)
    clock.advance_days(2)
    # New instance reading the same state file — as after an add-on restart.
    reopened = PrecipIndex(path, now_fn=clock)
    assert reopened.update(0.0) == round(3.0 * DEFAULT_K ** 2, 3)


def test_long_downtime_restarts_rather_than_decaying_blindly():
    clock = Clock()
    api, _, _ = make(clock)
    api.update(3.0)
    clock.advance_days(MAX_GAP_DAYS + 1)
    # Rain during the gap was never sampled, so carrying the old value forward would be
    # wrong in the dangerous direction — it would understate how wet the basin got.
    assert api.update(0.0) == 0.0


def test_backwards_clock_does_not_inflate_the_index():
    clock = Clock()
    api, _, _ = make(clock)
    api.update(1.0)
    clock.advance_days(-1)
    assert api.update(0.0) == 1.0      # no negative-exponent blow-up


def test_none_and_negative_increments_are_treated_as_no_rain():
    api, clock, _ = make()
    api.update(1.0)
    clock.advance_days(0)
    assert api.update(None) == 1.0
    assert api.update(-5.0) == 1.0


def test_corrupt_state_file_falls_back_to_a_cold_start():
    path = Path(tempfile.mkdtemp()) / "api.json"
    path.write_text("{ not json", encoding="utf-8")
    api = PrecipIndex(path, now_fn=Clock())
    assert api.value == 0.0


def test_adjust_adds_and_removes_past_rain_with_decay():
    import tempfile
    from pathlib import Path as _P
    clock = [1_000_000.0]
    idx = PrecipIndex(_P(tempfile.mkdtemp()) / "api.json", k=0.5, now_fn=lambda: clock[0])
    idx.update(0.0)                                   # anchor at t
    idx.adjust(added=[(clock[0] - 86400.0, 1.0)], removed=[])
    assert abs(idx.value - 0.5) < 1e-3                # a day old at k=0.5: half counts
    idx.adjust(added=[], removed=[(clock[0] - 86400.0, 1.0)])
    assert abs(idx.value) < 1e-3
    idx.adjust(added=[(clock[0] + 10, 1.0)], removed=[])   # in the future: ignored
    assert abs(idx.value) < 1e-3


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
