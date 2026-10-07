"""Dataset gap rows built from gateway records.

Two kinds of gap: slots with no row at all (the HA host, and the add-on with it, was down),
and "blind" rows the add-on wrote while HA Core alone was down (every HA read failed, so no
stage and no on-site rain). Both are filled from the gateway's node and Ecowitt records.

Run: python rate_of_rise/tests/test_backfill_gaprows.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backfill.gaprows import GapFiller, GapFillDeferred, eco_increments, missing_slots  # noqa: E402

T = 1_791_300_000.0
CFG = SimpleNamespace(fast_loop_minutes=5, rate_of_rise_window_minutes=10.0,
                      rate_of_rise_max_gap_minutes=10.0,
                      soil_moisture_entities=["sensor.willow", "sensor.field"])


class FakeDataset:
    def __init__(self, rows):
        self.rows, self.appended = rows, []

    def frame(self):
        return pd.DataFrame(self.rows) if self.rows else pd.DataFrame(columns=["ts"])

    def append_record(self, record):
        self.appended.append(record)


class FakeRain:
    def __init__(self, ring=(), anchor_ts_value=2e9):
        self.ring, self.calls = list(ring), []
        self._anchor_ts_value = anchor_ts_value

    def snapshot(self):
        return list(self.ring)

    def replace_window(self, start, end, increments):
        self.calls.append((start, end, list(increments)))

    def anchor_ts(self):
        return self._anchor_ts_value


def node_records(t0=T + 60, t1=T + 2700):
    out, seq, t = [], 1, t0
    while t <= t1:
        out.append({"seq": seq, "ts": t, "ts_src": "ntp", "stage_ft": round(1.0 + 0.0001 * seq, 4)})
        seq, t = seq + 1, t + 60
    return out


def eco_records(t0=T, t1=T + 2700, rain_from=T + 900):
    out, seq, t = [], 1, t0
    while t <= t1:
        steps = max(0, int((t - rain_from) // 60))
        out.append({"seq": seq, "ts": t, "ts_src": "ntp", "rain_year_in": round(10.0 + 0.01 * steps, 2),
                    "rain_rate_in_hr": 0.6 if steps else 0.0, "temp_f": 50.0, "soil": {"2": 70}})
        seq, t = seq + 1, t + 60
    return out


def live_row(ts, stage=1.0, online=True):
    row = {"ts": ts, "stage_ft": stage, "creek_node_online": online, "api_index_in": 1.0,
           "alert_tier": 0}
    # Live rows from the loop have rates and sample counts at cap
    if stage is not None:
        row["rate_of_rise_in_min"] = 0.01  # small rate to indicate it was computed
        row["rate_of_rise_sample_count"] = 10.0
    return row


def test_missing_slots():
    assert missing_slots([T, T + 300, T + 2400, T + 2700], T, T + 2700, 300) == [
        T + 600, T + 900, T + 1200, T + 1500, T + 1800, T + 2100]
    assert missing_slots([T + i * 300 for i in range(10)], T, T + 2700, 300) == []
    assert missing_slots([], T, T + 900, 300) == [T, T + 300, T + 600, T + 900]


def test_eco_increments_handle_resets_and_long_gaps():
    eco = [{"ts": 0.0, "rain_year_in": 1.0}, {"ts": 60.0, "rain_year_in": 1.05},
           {"ts": 120.0, "rain_year_in": 0.0},                  # yearly reset
           {"ts": 180.0, "rain_year_in": 0.02},
           {"ts": 5000.0, "rain_year_in": 0.5},                 # over an hour later: not placeable
           {"ts": 5060.0, "rain_year_in": None}]
    assert eco_increments(eco) == [(60.0, 0.05), (180.0, 0.02)]


def test_fills_missing_slots_from_node_and_ecowitt_records():
    ds = FakeDataset([live_row(T), live_row(T + 300), live_row(T + 2400), live_row(T + 2700)])
    # Set live predecessor at T+300 to non-capped count to test increment logic
    ds.rows[1]["rate_of_rise_sample_count"] = 3.0
    rain = FakeRain()
    n = GapFiller(CFG, ds, soil_channels={"near_creek": "2"}, rain=rain, now_fn=lambda: T + 3600).fill(
        node_records(), eco_records())
    assert n == 6
    rows = {r["ts"]: r for r in ds.appended}
    assert sorted(rows) == [T + 600, T + 900, T + 1200, T + 1500, T + 1800, T + 2100]
    r = rows[T + 1500]
    assert r["backfilled"] is True and r["creek_node_online"] is True
    assert r["stage_ft"] == round(1.0 + 0.0001 * 25, 4)        # node record at T+1500 is seq 25
    assert r["rate_of_rise_in_min"] is not None
    assert r["rate_of_rise_sample_count"] >= 1.0
    assert r["rain_1h_in"] == 0.1 and r["rain_rate_in_hr"] == 0.6
    assert r["soil_moisture_near_creek_pct"] == 70 and r["soil_moisture_near_house_pct"] is None
    assert r["api_index_in"] is not None and r["api_index_in"] > 1.0
    assert r["qpf_6h_in"] is None and r["nwm_flow_cfs"] is None        # forecasts stay empty
    assert rain.calls and rain.calls[0][0] == T and rain.calls[0][1] == T + 2700
    # Sample counts: all gap rows have rates and increment from live row at T+300 (count 3.0)
    # Expected sequence: 4.0, 5.0, 6.0, 7.0, 8.0, 9.0 (capped at 10)
    expected_counts = [4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    sorted_ts = sorted(rows.keys())
    for ts, expected_count in zip(sorted_ts, expected_counts):
        assert rows[ts]["rate_of_rise_in_min"] is not None
        assert rows[ts]["rate_of_rise_sample_count"] == expected_count


def test_blind_rows_are_reissued_with_the_same_ts():
    rows = [live_row(T + i * 300) for i in range(10)]
    # Blind rows (HA Core down): no rate, no count. Update realistic zeros, not None.
    for r in rows[2:8]:
        r.update(stage_ft=None, creek_node_online=None, rate_of_rise_in_min=None, rate_of_rise_sample_count=0.0)
    # Live predecessor at T+300 has a non-capped count (3.0) to test increment logic
    rows[1]["rate_of_rise_sample_count"] = 3.0
    ds = FakeDataset(rows)
    n = GapFiller(CFG, ds, rain=FakeRain(), now_fn=lambda: T + 3600).fill(node_records(), eco_records())
    assert n == 6
    assert sorted(r["ts"] for r in ds.appended) == [T + i * 300 for i in range(2, 8)]
    assert all(r["stage_ft"] is not None and r["alert_tier"] == 0 and r["backfilled"]
               for r in ds.appended)
    # Re-issued blind rows should have rates and sample counts incremented from live predecessor
    # Each blind row (T+600, T+900, ..., T+2100) has a node record at least 540s earlier, so has rate.
    # Counts increment from rows[1] = 3.0: they should be 4.0, 5.0, 6.0, 7.0, 8.0, 9.0
    expected_counts = [4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    for r, expected_count in zip(sorted(ds.appended, key=lambda x: x["ts"]), expected_counts):
        assert r.get("rate_of_rise_in_min") is not None, f"ts {r['ts']}: expected rate"
        assert r.get("rate_of_rise_sample_count") == expected_count, \
            f"ts {r['ts']}: count {r.get('rate_of_rise_sample_count')} != {expected_count}"


def test_no_gap_appends_nothing_and_leaves_rain_alone():
    ds = FakeDataset([live_row(T + i * 300) for i in range(10)])
    rain = FakeRain()
    assert GapFiller(CFG, ds, rain=rain, now_fn=lambda: T + 3600).fill(node_records(), eco_records()) == 0
    assert ds.appended == [] and rain.calls == []


def test_rain_left_alone_when_ecowitt_records_have_an_hour_gap():
    ds = FakeDataset([live_row(T), live_row(T + 6000)])
    rain = FakeRain()
    eco = [e for e in eco_records(T, T + 6000) if not T + 600 < e["ts"] < T + 4800]
    GapFiller(CFG, ds, rain=rain, now_fn=lambda: T + 3600).fill(node_records(T + 60, T + 6000), eco)
    assert rain.calls == []


def test_refetched_sources_are_merged_and_failures_tolerated():
    class Ok:
        name = "usgs"

        def history_between(self, start, end):
            assert start.tzinfo is timezone.utc
            return lambda as_of: {"usgs_leggetts_gage_ft": 2.0}

    class Broken:
        name = "wu"

        def history_between(self, start, end):
            raise RuntimeError("history API down")

    sources = SimpleNamespace(history_sources=lambda: [Ok(), Broken()])
    ds = FakeDataset([live_row(T), live_row(T + 300), live_row(T + 2400), live_row(T + 2700)])
    GapFiller(CFG, ds, sources=sources, rain=FakeRain(), now_fn=lambda: T + 3600).fill(node_records(), eco_records())
    assert ds.appended and all(r["usgs_leggetts_gage_ft"] == 2.0 for r in ds.appended)
    assert all(r["upstream_rain_1h_in"] is None for r in ds.appended)


def test_rain_window_older_than_the_ring_is_unknown():
    ds = FakeDataset([live_row(T), live_row(T + 300), live_row(T + 2400), live_row(T + 2700)])
    rain = FakeRain()
    # horizon = now - RETAIN_S = (T + 72*3600 - 3600) - 72*3600 = T - 3600
    n = GapFiller(CFG, ds, soil_channels={"near_creek": "2"}, rain=rain,
                  now_fn=lambda: T + 72*3600 - 3600).fill(node_records(), eco_records())
    assert n == 6
    rows = {r["ts"]: r for r in ds.appended}
    r = rows[T + 1500]
    # 1h window: T+1500 - 3600 = T-2100, which is >= horizon (T-3600), so not None
    assert r["rain_1h_in"] == 0.1
    # 3h window: T+1500 - 10800 = T-9300, which is < horizon (T-3600), so None
    assert r["rain_3h_in"] is None
    # 72h window: definitely None
    assert r["rain_72h_in"] is None


def test_node_context_carries_across_passes():
    # First pass: no gap (rows at every 300 s through T+600)
    rows = [live_row(T + i * 300) for i in range(3)]  # T, T+300, T+600
    ds = FakeDataset(rows)
    gf = GapFiller(CFG, ds, now_fn=lambda: T + 3600)
    n = gf.fill(node_records(T + 60, T + 600), [])
    assert n == 0  # no gap

    # Second pass: has a gap (rows at T+600, T+2400), but node starts later
    ds.rows.append(live_row(T + 2400))
    # Without context, node_records(T+660, T+2400) would have its first record at T+660.
    # The T+900 slot needs a record from ~T+300 to calculate rate, which is only available from context.
    # Reuse the same GapFiller instance to carry node context across passes.
    n = gf.fill(node_records(T + 660, T + 2400),
                eco_records(T + 600, T + 2400))
    assert n >= 1  # at least one gap row filled
    rows = {r["ts"]: r for r in ds.appended}
    assert T + 900 in rows, f"T+900 slot should be filled; appended rows: {sorted(rows.keys())}"
    r = rows[T + 900]
    # With context, T+900 should have a rate calculated from context data
    assert r["creek_node_online"] is True
    assert r["rate_of_rise_in_min"] is not None


def test_fill_defers_until_the_first_live_poll():
    ds = FakeDataset([live_row(T), live_row(T + 300), live_row(T + 2400), live_row(T + 2700)])
    rain = FakeRain(anchor_ts_value=None)
    gf = GapFiller(CFG, ds, rain=rain, now_fn=lambda: T + 3600)

    # Defer when anchor_ts is None
    try:
        gf.fill(node_records(), eco_records())
        assert False, "Should have raised GapFillDeferred"
    except GapFillDeferred as e:
        assert "waiting for the first live rain poll" in str(e)

    # Nothing appended
    assert ds.appended == []
    # State should be unchanged after deferral
    assert gf._last_eco is None
    assert gf._node_context == []

    # Now anchor_ts is set to a value after started time; retry on same instance
    rain._anchor_ts_value = T + 3600 + 1
    n = gf.fill(node_records(), eco_records())
    assert n == 6  # Should proceed and fill gaps


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
