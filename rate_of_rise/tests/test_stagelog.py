"""The high-resolution stage record kept outside Home Assistant's recorder.

On 2026-09-23 HA's recorder wrote nothing for 25 hours while HA itself kept running. The
60 s stage record lived only there, so a storm in that window would have kept the add-on's
5-minute rows and lost its crest. StageLogger reads the live state instead and keeps its own
daily CSVs.

Run: python rate_of_rise/tests/test_stagelog.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.stagelog import LOG_EVERY_S, StageLogger, stage_log_dir  # noqa: E402


class FakeHA:
    def __init__(self):
        self.value, self.age = 0.93, 5.0

    def get_float_with_age(self, _entity):
        return self.value, self.age


class Clock:
    def __init__(self):
        self.t = 1_790_000_000.0

    def __call__(self):
        return self.t


def make():
    d = Path(tempfile.mkdtemp())
    ha, clock = FakeHA(), Clock()
    return StageLogger(ha, "sensor.creek_gateway_stage", d, now_fn=clock), ha, clock, d


def rows(d):
    out = []
    for p in sorted(d.glob("*.csv")):
        lines = p.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "reading_ts,logged_ts,stage_ft"
        out += lines[1:]
    return out


def test_each_new_reading_is_written_once():
    log, ha, clock, d = make()
    assert log.tick() is True
    clock.t += LOG_EVERY_S
    ha.age += LOG_EVERY_S                  # same reading, just older
    assert log.tick() is False
    clock.t += LOG_EVERY_S
    ha.value, ha.age = 0.94, 2.0           # a new reading
    assert log.tick() is True
    assert [r.split(",")[2] for r in rows(d)] == ["0.9300", "0.9400"]


def test_it_rate_limits_itself():
    log, ha, clock, d = make()
    log.tick()
    ha.value, ha.age = 0.95, 0.0
    clock.t += 1.0
    assert log.tick() is False, "called every 5 s by the loop, reads every 10 s"


def test_an_unknown_reading_is_recorded_as_blank_not_skipped():
    """A dropout is part of the record — the gap is information."""
    log, ha, clock, d = make()
    ha.value = None
    assert log.tick() is True
    assert rows(d)[0].endswith(",")


def test_a_missing_entity_writes_nothing():
    log, ha, clock, d = make()
    ha.value, ha.age = None, None
    assert log.tick() is False
    assert rows(d) == []


def test_it_lives_under_share_when_there_is_one():
    assert stage_log_dir(Path("/data"), Path("/share")) == Path("/share/rate_of_rise/stage")
    assert stage_log_dir(Path("/data"), None) == Path("/data/stage")


def test_the_dataset_records_what_each_path_said():
    """Without the tier and both probabilities beside the features, a storm cannot be
    replayed afterwards to ask what the threshold estimate and the shadow model each did."""
    import json
    from app.dataset import DatasetWriter
    from app.features import FeatureRow
    d = Path(tempfile.mkdtemp())
    ds = DatasetWriter(d)
    row = FeatureRow(ts=1_790_000_000.0, stage_ft=0.93, rate_of_rise_in_min=0.0,
                     soil_moisture_mean_pct=None, soil_moisture_near_house_pct=None,
                     soil_moisture_near_creek_pct=None, ponding_flag=False)
    ds.append_row(row, {"alert_tier": 2, "flood_probability": 0.0, "ml_probability": 0.41,
                        "ml_version": "gbm-x", "probability_method": "threshold"})
    part = next((d / "datasets" / "parts").glob("*.jsonl"))
    rec = json.loads(part.read_text(encoding="utf-8").splitlines()[0])
    assert rec["alert_tier"] == 2 and rec["ml_probability"] == 0.41
    assert rec["stage_ft"] == 0.93
    frame = ds.frame()
    assert frame["ml_version"].iloc[0] == "gbm-x"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
