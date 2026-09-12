"""Tests for SourceCoordinator merge/cache/error handling.
Run: python creek_modeling/tests/test_sources.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources import FEATURE_KEYS, SourceCoordinator  # noqa: E402


class _Cfg:
    onsite_rain_rate_entity = None   # -> no rain source built
    wu_api_key = ""                  # -> no WU source
    upstream_pws_ids = []
    nwm_reach_id = ""                # -> no NWM source
    usgs_downstream = False          # -> no USGS source


class _HA:
    def get_lat_lon(self):
        return None                  # -> no NWS source built


class FakeSource:
    name = "fake"
    refresh_seconds = 1000           # long, so re-poll won't happen within a test

    def __init__(self):
        self.calls = 0

    def poll(self):
        self.calls += 1
        return {"rain_1h_in": float(self.calls), "not_a_feature": 99}


class BoomSource:
    name = "boom"
    refresh_seconds = 1000

    def poll(self):
        raise RuntimeError("api down")


def make_empty_coordinator():
    return SourceCoordinator(_Cfg(), _HA(), Path("."))


def test_no_sources_returns_all_none():
    c = make_empty_coordinator()
    out = c.features()
    assert set(out) == set(FEATURE_KEYS)
    assert all(v is None for v in out.values())


def test_every_feature_key_is_a_real_feature_row_field():
    from dataclasses import fields
    from app.features import FeatureRow
    missing = set(FEATURE_KEYS) - {f.name for f in fields(FeatureRow)}
    assert not missing, missing


def test_merges_and_filters_to_feature_keys():
    c = make_empty_coordinator()
    fake = FakeSource()
    c._sources.append(fake)
    out = c.features()
    assert out["rain_1h_in"] == 1.0
    assert "not_a_feature" not in out         # filtered to FEATURE_KEYS


def test_caches_between_refresh_intervals():
    c = make_empty_coordinator()
    fake = FakeSource()
    c._sources.append(fake)
    c.features()
    c.features()                               # within refresh window
    assert fake.calls == 1                     # polled once, second call served from cache


def test_error_source_does_not_break_features():
    c = make_empty_coordinator()
    c._sources.append(BoomSource())
    out = c.features()                         # must not raise
    assert all(v is None for v in out.values())


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
