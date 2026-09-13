"""Plain-assert tests for the NEXRAD cell-track source.
Run: python rate_of_rise/tests/test_radar_cells.py"""
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.radar_cells import RadarCells  # noqa: E402

SITE_LAT, SITE_LON = 41.0, -75.5
COS_LAT = math.cos(math.radians(SITE_LAT))
NOW = datetime(2026, 7, 29, 21, 30, tzinfo=timezone.utc)


def west_of_site(nm: float) -> tuple[float, float]:
    """Coordinates `nm` nautical miles due west of the site."""
    return SITE_LAT, SITE_LON - nm / (60.0 * COS_LAT)


def csv_text(rows) -> str:
    out = ["VALID,STORM_ID,DRCT,SKNT,MAX_DBZ,LAT,LON"]
    out += [",".join(str(v) for v in r) for r in rows]
    return "\n".join(out) + "\n"


def build(rows, radar="KXXX"):
    urls = []

    def fetch(url, timeout=15.0):
        urls.append(url)
        return csv_text(rows)

    src = RadarCells(SITE_LAT, SITE_LON, radar, fetch=fetch, now_fn=lambda: NOW)
    return src, urls


def test_drct_is_direction_from_a_cell_due_west_coming_from_270_hits_us():
    """Pins the empirically-verified wind convention (see the module docstring): a cell
    10 nm due west with DRCT=270 is coming FROM the west, i.e. moving east, straight at
    the site. 10 nm at 20 kt is exactly 30 minutes out. If someone 'fixes' the heading
    to the toward-convention, this cell points away and the test fails."""
    lat, lon = west_of_site(10.0)
    src, _ = build([("202607292128", "A1", 270, 20, 50, lat, lon)])
    out = src.poll()
    assert out["radar_cells_tracked"] == 1.0
    assert out["radar_threat_cells"] == 1.0
    assert abs(out["radar_threat_eta_min"] - 30.0) < 0.5, out["radar_threat_eta_min"]
    assert out["radar_threat_max_dbz"] == 50.0


def test_the_same_cell_coming_from_the_east_is_outbound_and_no_threat():
    lat, lon = west_of_site(10.0)
    src, _ = build([("202607292128", "A1", 90, 20, 50, lat, lon)])
    out = src.poll()
    assert out["radar_cells_tracked"] == 1.0
    assert out["radar_threat_cells"] == 0.0
    assert out["radar_threat_eta_min"] is None
    assert out["radar_threat_max_dbz"] is None
    assert out["radar_threat_scan_count"] is None


def test_an_intense_cell_passing_wide_is_not_a_threat():
    """Same inbound geometry but the track passes ~5 mi north of the site — outside
    the 4 mi CPA radius, so it is rain for the neighbours, not for us."""
    lat, lon = west_of_site(10.0)
    lat += (5.0 / 1.15078) / 60.0     # displace the cell 5 statute miles north
    src, _ = build([("202607292128", "B2", 270, 20, 55, lat, lon)])
    out = src.poll()
    assert out["radar_threat_cells"] == 0.0
    assert out["radar_threat_eta_min"] is None


def test_a_weak_cell_dead_on_is_tracked_but_not_a_threat():
    lat, lon = west_of_site(10.0)
    src, _ = build([("202607292128", "C3", 270, 20, 35, lat, lon)])
    out = src.poll()
    assert out["radar_cells_tracked"] == 1.0
    assert out["radar_threat_cells"] == 0.0


def test_unresolved_motion_rows_never_threaten_or_divide_by_zero():
    """SCIT emits DRCT=0/SKNT=0 while it cannot resolve a track (seen in live KXXX
    data); such a cell has no usable vector regardless of its intensity."""
    lat, lon = west_of_site(2.0)
    src, _ = build([("202607292128", "D4", 0, 0, 60, lat, lon)])
    out = src.poll()
    assert out["radar_cells_tracked"] == 1.0
    assert out["radar_threat_cells"] == 0.0


def test_a_cell_beyond_the_eta_horizon_is_not_yet_a_threat():
    lat, lon = west_of_site(40.0)     # 40 nm at 20 kt = 120 min > the 90 min horizon
    src, _ = build([("202607292128", "E5", 270, 20, 50, lat, lon)])
    out = src.poll()
    assert out["radar_threat_cells"] == 0.0


def test_eta_is_the_soonest_of_several_threats():
    far = west_of_site(10.0)          # 30 min out
    near = west_of_site(5.0)          # 15 min out
    src, _ = build([
        ("202607292128", "F6", 270, 20, 45, far[0], far[1]),
        ("202607292128", "G7", 270, 20, 52, near[0], near[1]),
    ])
    out = src.poll()
    assert out["radar_threat_cells"] == 2.0
    assert abs(out["radar_threat_eta_min"] - 15.0) < 0.5
    assert out["radar_threat_max_dbz"] == 52.0


def test_a_single_scan_yields_scan_count_one():
    lat, lon = west_of_site(10.0)
    src, _ = build([("202607292128", "Q7", 270, 20, 50, lat, lon)])
    out = src.poll()
    assert out["radar_threat_scan_count"] == 1.0


def test_two_consecutive_confirming_scans_yield_scan_count_two():
    lat, lon = west_of_site(10.0)
    src, _ = build([
        ("202607292122", "N4", 270, 20, 50, lat, lon),   # older scan: inbound
        ("202607292128", "N4", 270, 20, 50, lat, lon),   # newest scan: inbound
    ])
    out = src.poll()
    assert out["radar_threat_cells"] == 1.0
    assert out["radar_threat_scan_count"] == 2.0


def test_a_broken_confirmation_streak_counts_only_the_run_from_the_newest_scan():
    """The cell was inbound three scans ago, lost its track for one scan, then
    re-qualified. Only the unbroken run from the newest scan counts — two scans back
    does not get to "vote" once the streak has broken."""
    lat, lon = west_of_site(10.0)
    src, _ = build([
        ("202607292116", "P6", 270, 20, 50, lat, lon),   # 3 scans back: inbound
        ("202607292122", "P6", 90, 20, 50, lat, lon),    # 2 scans back: outbound (breaks it)
        ("202607292128", "P6", 270, 20, 50, lat, lon),   # newest: inbound again
    ])
    out = src.poll()
    assert out["radar_threat_cells"] == 1.0
    assert out["radar_threat_scan_count"] == 1.0


def test_a_gap_longer_than_the_freshness_window_breaks_the_streak():
    """Two fixes both individually pass the intercept test, but they are 24 min
    apart — longer than FRESH_MINUTES (12) — so SCIT lost and re-acquired the
    track rather than watching it continuously. That is not a confirmed streak."""
    lat, lon = west_of_site(10.0)
    src, _ = build([
        ("202607292104", "T1", 270, 20, 50, lat, lon),   # 24 min before the newest
        ("202607292128", "T1", 270, 20, 50, lat, lon),   # newest
    ])
    out = src.poll()
    assert out["radar_threat_cells"] == 1.0
    assert out["radar_threat_scan_count"] == 1.0


def test_scan_count_is_the_max_across_simultaneous_threats():
    far = west_of_site(10.0)      # one confirming scan
    near = west_of_site(5.0)      # two confirming scans
    src, _ = build([
        ("202607292128", "R8", 270, 20, 45, far[0], far[1]),
        ("202607292122", "S9", 270, 20, 52, near[0], near[1]),
        ("202607292128", "S9", 270, 20, 52, near[0], near[1]),
    ])
    out = src.poll()
    assert out["radar_threat_cells"] == 2.0
    assert out["radar_threat_scan_count"] == 2.0


def test_scan_count_is_none_when_there_is_no_current_threat():
    src, _ = build([])
    out = src.poll()
    assert out["radar_threat_scan_count"] is None


def test_only_the_newest_fix_per_storm_id_counts():
    """Each volume scan re-lists every cell. This storm was inbound at 21:22 but its
    newest fix has it outbound — stale fixes must not keep the threat alive."""
    lat, lon = west_of_site(10.0)
    src, _ = build([
        ("202607292122", "H8", 270, 20, 50, lat, lon),
        ("202607292128", "H8", 90, 20, 50, lat, lon),
    ])
    out = src.poll()
    assert out["radar_cells_tracked"] == 1.0
    assert out["radar_threat_cells"] == 0.0


def test_a_cell_that_dropped_out_of_tracking_is_forgotten():
    """A cell whose last fix is far older than the newest scan dissipated or merged —
    SCIT stopped listing it, so it no longer exists to threaten anything."""
    lat, lon = west_of_site(10.0)
    src, _ = build([
        ("202607292106", "I9", 270, 20, 50, lat, lon),   # last seen 22 min earlier
        ("202607292128", "J0", 90, 20, 41, lat, lon),
    ])
    out = src.poll()
    assert out["radar_cells_tracked"] == 1.0


def test_malformed_rows_are_skipped_not_fatal():
    lat, lon = west_of_site(10.0)
    src, _ = build([
        ("202607292128", "K1", 270, 20, 50, "junk", lon),
        ("not-a-time", "L2", 270, 20, 50, lat, lon),
        ("202607292128", "M3", 270, 20, 50, lat, lon),
    ])
    out = src.poll()
    assert out["radar_cells_tracked"] == 1.0
    assert out["radar_threat_cells"] == 1.0


def test_a_quiet_sky_is_zeros_and_nones_not_an_error():
    src, _ = build([])
    out = src.poll()
    assert out["radar_cells_tracked"] == 0.0
    assert out["radar_threat_cells"] == 0.0
    assert out["radar_threat_eta_min"] is None
    assert out["radar_threat_max_dbz"] is None


def test_url_uses_the_iem_site_code_and_the_fetch_window():
    src, urls = build([], radar="KXXX")
    src.poll()
    assert "radar=XXX" in urls[0], urls[0]           # ICAO K prefix stripped for IEM
    assert "sts=2026-07-29T21:00Z" in urls[0]
    assert "ets=2026-07-29T21:30Z" in urls[0]


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
