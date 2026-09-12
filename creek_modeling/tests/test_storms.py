"""Tests for the storm event log (spec §7 Phase 3).
Run: python creek_modeling/tests/test_storms.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import storms as st  # noqa: E402
from app.features import FeatureRow  # noqa: E402
from app.storms import StormLog  # noqa: E402

T0 = 1769904000.0
HOUR = 3600.0


def row(ts, rain=0.0, upstream=0.0, **kw):
    base = dict(
        ts=ts, stage_ft=None, rate_of_rise_in_min=None, soil_moisture_mean_pct=55.0,
        soil_moisture_near_house_pct=55.0, soil_moisture_near_creek_pct=55.0,
        ponding_flag=False, rain_1h_in=rain, upstream_rain_1h_in=upstream,
        api_index_in=1.5,
    )
    base.update(kw)
    return FeatureRow(**base)


def make():
    return StormLog(Path(tempfile.mkdtemp()))


def test_rain_opens_an_event_and_quiet_alone_does_not():
    s = make()
    assert s.observe(row(T0, rain=0.01)) is None
    assert s.count(completed_only=False) == 0
    assert s.observe(row(T0 + 300, rain=0.4)) == "opened"
    assert s.count(completed_only=False) == 1


def test_upstream_rain_alone_can_open_an_event():
    # The upstream corridor is half the basin, and it rains there first.
    s = make()
    assert s.observe(row(T0, upstream=0.5)) == "opened"


def test_a_brief_lull_does_not_split_one_storm_in_two():
    s = make()
    s.observe(row(T0, rain=0.5))
    s.observe(row(T0 + HOUR, rain=0.0))          # a pause, not the end
    s.observe(row(T0 + 2 * HOUR, rain=0.4))
    assert s.count(completed_only=False) == 1
    assert s.count() == 0                         # still open


def test_event_closes_after_the_quiet_window():
    s = make()
    s.observe(row(T0, rain=0.5))
    assert s.observe(row(T0 + st.END_QUIET_SECONDS + 60, rain=0.0)) == "closed"
    assert s.count() == 1
    event = s.latest()
    assert event["ended_ts"] is not None


def test_onset_conditions_are_captured_from_the_opening_row():
    s = make()
    s.observe(row(T0, rain=0.5, api_index_in=2.75, soil_moisture_mean_pct=81.0,
                  snow_water_equivalent_in=1.2, rain_on_snow_flag=True))
    e = s.latest()
    assert e["api_at_onset_in"] == 2.75
    assert e["soil_at_onset_pct"] == 81.0
    assert e["swe_at_onset_in"] == 1.2
    assert e["rain_on_snow"] == 1


def test_peaks_track_the_maximum_over_the_event():
    s = make()
    s.observe(row(T0, rain=0.3))
    s.observe(row(T0 + 300, rain=0.9, stage_ft=2.4, rate_of_rise_in_min=0.08), alert_tier=3)
    s.observe(row(T0 + 600, rain=0.5, stage_ft=1.8), alert_tier=1)
    e = s.latest()
    assert e["peak_rain_1h_in"] == 0.9
    assert e["peak_stage_ft"] == 2.4          # not overwritten by the later lower value
    assert e["peak_rate_of_rise_in_min"] == 0.08
    assert e["peak_alert_tier"] == 3


def test_accumulation_takes_the_largest_window_rather_than_summing_samples():
    # rain_24h_in is already a rolling sum; adding each sample would count it many times.
    s = make()
    s.observe(row(T0, rain=0.5, rain_24h_in=0.5))
    s.observe(row(T0 + 300, rain=0.5, rain_24h_in=1.1))
    s.observe(row(T0 + 600, rain=0.5, rain_24h_in=0.9))
    assert s.latest()["max_24h_rain_in"] == 1.1


def test_missing_response_features_leave_nulls_rather_than_zeros():
    # No creek gauge yet — the event must record "unknown", not a fake 0.0 peak.
    s = make()
    s.observe(row(T0, rain=0.5))
    e = s.latest()
    assert e["peak_stage_ft"] is None
    assert e["peak_rate_of_rise_in_min"] is None


def test_count_gates_on_completed_events_only():
    s = make()
    s.observe(row(T0, rain=0.5))
    assert s.count() == 0                     # an open storm is not yet an observation
    s.observe(row(T0 + st.END_QUIET_SECONDS + 60, rain=0.0))
    assert s.count() == 1


def test_a_restart_mid_storm_resumes_the_open_event():
    d = Path(tempfile.mkdtemp())
    first = StormLog(d)
    first.observe(row(T0, rain=0.5))
    reopened = StormLog(d)                    # as after an add-on restart
    reopened.observe(row(T0 + 300, rain=0.6))
    assert reopened.count(completed_only=False) == 1


def test_a_restart_does_not_close_a_storm_that_is_still_raining_itself_out():
    """The regression: the quiet clock used to be an instance attribute, so a restart
    reset it to None and the fallback became the storm's own *start* time. Once a storm
    had been open longer than the quiet window -- an ordinary all-day rain -- that
    fallback read as a window's worth of quiet already elapsed, and the first lull after
    the restart closed the event on the spot.

    The storm here rains steadily for seven hours (past the six-hour window), so the
    restart lands in exactly that trap."""
    d = Path(tempfile.mkdtemp())
    first = StormLog(d)
    for h in range(8):
        first.observe(row(T0 + h * HOUR, rain=0.5))

    reopened = StormLog(d)
    # A five-minute lull. The rain stopped five minutes ago, not six hours ago.
    assert reopened.observe(row(T0 + 7 * HOUR + 300, rain=0.0)) is None
    assert reopened.count() == 0
    assert reopened.latest()["ended_ts"] is None


def test_a_restart_does_not_collapse_a_long_storms_end_back_onto_its_onset():
    """The other half of the same bug: it closed with ended_ts = row.ts - since, and with
    started_ts standing in as the anchor that is started_ts exactly. A storm that rained
    for three hours got stamped as having ended the moment it began -- a poisoned row in
    the very record the lag analysis learns from."""
    d = Path(tempfile.mkdtemp())
    first = StormLog(d)
    for h in (0, 1, 2, 3):                    # it rains steadily for three hours
        first.observe(row(T0 + h * HOUR, rain=0.5))
    reopened = StormLog(d)                    # restart, then it tapers off
    reopened.observe(row(T0 + 3 * HOUR + 300, rain=0.0))
    reopened.observe(row(T0 + 3 * HOUR + st.END_QUIET_SECONDS + 60, rain=0.0))

    event = reopened.latest()
    assert event["ended_ts"] is not None, "should have closed by now"
    assert event["ended_ts"] == T0 + 3 * HOUR, event      # when the rain actually stopped
    assert event["ended_ts"] - event["started_ts"] == 3 * HOUR


def test_the_quiet_clock_survives_a_restart_rather_than_starting_over():
    """A restart must neither extend nor truncate the storm: the clock is anchored on the
    last *rain*, so quiet accrued before the restart still counts, and the recorded end is
    when the rain stopped rather than when the storm began."""
    d = Path(tempfile.mkdtemp())
    first = StormLog(d)
    first.observe(row(T0, rain=0.5))
    first.observe(row(T0 + 2 * HOUR, rain=0.5))       # rain stops here
    first.observe(row(T0 + 3 * HOUR, rain=0.0))       # 1 h of quiet, then we restart
    reopened = StormLog(d)
    # 6 h total since the rain stopped, so this closes -- and it ended at T0 + 2 h, not
    # at T0, which is what anchoring on started_ts would have recorded.
    assert reopened.observe(row(T0 + 8 * HOUR, rain=0.0)) == "closed"
    assert reopened.latest()["ended_ts"] == T0 + 2 * HOUR


def test_a_legacy_row_with_no_anchor_restarts_the_clock_rather_than_closing():
    """Rows opened by the version that kept the clock in memory have last_rain_ts NULL.
    That is unknowable, not quiet-since-onset -- closing on it would fabricate the very
    zero-length record this fixes, on the storm open at upgrade time."""
    d = Path(tempfile.mkdtemp())
    s = StormLog(d)
    s.observe(row(T0, rain=0.5))
    with sqlite3.connect(d / "events.sqlite") as con:
        con.execute("UPDATE storm_events SET last_rain_ts = NULL")

    s = StormLog(d)
    assert s.observe(row(T0 + 12 * HOUR, rain=0.0)) is None   # would have closed before
    assert s.count() == 0
    # The clock is now anchored, so the window runs from here.
    assert s.observe(row(T0 + 12 * HOUR + st.END_QUIET_SECONDS + 60, rain=0.0)) == "closed"


def test_the_detection_thresholds_are_tunable():
    """They are add-on options because they define what counts as one storm, and that
    cannot be settled without storms on record."""
    d = Path(tempfile.mkdtemp())
    s = StormLog(d, None, start_rain_1h_in=0.5, continue_rain_1h_in=0.2,
                 quiet_seconds=2 * HOUR)
    assert s.observe(row(T0, rain=0.3)) is None            # under the custom open bar
    assert s.observe(row(T0 + 300, rain=0.6)) == "opened"
    assert s.observe(row(T0 + 600, rain=0.3)) is None       # still raining by this config
    # 2 h of quiet is enough here, where the default would still be holding the storm open.
    assert s.observe(row(T0 + 600 + 2 * HOUR, rain=0.1)) == "closed"


def test_annotation_is_preserved():
    s = make()
    s.observe(row(T0, rain=0.5))
    s.annotate(s.latest()["id"], "basement stayed dry; culvert ran full")
    assert "culvert" in s.latest()["notes"]


def test_latest_closed_ignores_a_newer_open_event():
    """The regression this exists to prevent: annotation happens the day after a storm
    closes, and a second storm may already be open by then. latest() would point at
    that unfinished one -- the wrong target for "annotate what I watched yesterday"."""
    s = make()
    s.observe(row(T0, rain=0.5))
    s.observe(row(T0 + 300, rain=0.0))
    s.observe(row(T0 + st.END_QUIET_SECONDS + 600, rain=0.0))   # closes event 1
    first_id = s.latest()["id"]

    s.observe(row(T0 + st.END_QUIET_SECONDS + 3600, rain=0.6))  # event 2 opens
    assert s.latest()["id"] != first_id       # latest() now points at the new, open one
    assert s.latest_closed()["id"] == first_id  # latest_closed() still finds the real target


def test_latest_closed_is_none_before_anything_has_closed():
    s = make()
    s.observe(row(T0, rain=0.5))   # opens an event, never closes it
    assert s.latest_closed() is None


def test_legacy_table_is_migrated_not_dropped():
    d = Path(tempfile.mkdtemp())
    db = d / "events.sqlite"
    with sqlite3.connect(db) as con:          # the original 0.1.0 schema
        con.execute("CREATE TABLE storm_events (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " started_ts REAL NOT NULL, ended_ts REAL, peak_stage_ft REAL,"
                    " notes TEXT)")
        con.execute("INSERT INTO storm_events (started_ts, notes) VALUES (?,?)",
                    (T0, "hand-annotated before the upgrade"))
    s = StormLog(d)
    assert s.count(completed_only=False) == 1
    assert "hand-annotated" in s.latest()["notes"]
    assert "api_at_onset_in" in s.latest()     # new columns added alongside


def test_share_dir_is_preferred_and_a_legacy_data_log_is_moved_not_copied():
    data = Path(tempfile.mkdtemp())
    share = Path(tempfile.mkdtemp())
    legacy = data / "events.sqlite"
    StormLog(data).observe(row(T0, rain=0.5))     # a log that predates the /share move
    assert legacy.exists()

    s = StormLog(data, share)
    moved = share / st.SHARE_SUBDIR / "events.sqlite"
    assert s.path == moved and moved.exists()
    # A copy would leave the service and the human editing different files.
    assert not legacy.exists()
    assert s.count(completed_only=False) == 1     # the existing storm came along


def test_unusable_share_falls_back_to_data_rather_than_failing():
    # `share` is a regular file, so mkdir under it raises NotADirectoryError. Permission
    # bits would not work here: the add-on (and this suite) run as root, which ignores
    # them — the realistic failures are share:rw dropped from config.yaml or /share gone.
    data = Path(tempfile.mkdtemp())
    share = Path(tempfile.mkdtemp()) / "not-a-dir"
    share.touch()

    s = StormLog(data, share)
    assert s.path == data / "events.sqlite"
    assert s.observe(row(T0, rain=0.5)) == "opened"   # still records


def test_no_share_dir_keeps_the_data_path():
    data = Path(tempfile.mkdtemp())
    assert StormLog(data, None).path == data / "events.sqlite"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
