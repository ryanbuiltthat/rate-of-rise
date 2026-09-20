"""Plain-assert tests for the Google Flood Forecasting source.
Run: python rate_of_rise/tests/test_google_floods.py"""
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.google_floods import (  # noqa: E402
    GAUGE_REFRESH_SECONDS, MAX_GAUGES, MI_PER_DEG_LAT, SEARCH_RADIUS_MI, PAGE_SIZE,
    FLASH_FLOOD_EVENT_WARN_COUNT, FLASH_FLOOD_POLL_BUDGET_SECONDS, GoogleFloods,
)

SITE_LAT, SITE_LON = 41.0, -75.5
KEY = "test-key-not-a-real-one"


def at(miles_north, gauge_id, severity="NO_FLOODING", trend="NO_CHANGE",
       site="Some Creek", verified=True):
    """A (gauge, status) pair for a gauge `miles_north` due north of the site."""
    lat = SITE_LAT + miles_north / MI_PER_DEG_LAT
    gauge = {"gaugeId": gauge_id, "siteName": site, "river": "Some River",
             "countryCode": "US", "qualityVerified": verified, "hasModel": True,
             "location": {"latitude": lat, "longitude": SITE_LON}}
    status = {"gaugeId": gauge_id, "severity": severity, "forecastTrend": trend,
              "qualityVerified": verified, "source": "USGS",
              "gaugeLocation": {"latitude": lat, "longitude": SITE_LON}}
    return gauge, status


def _rect_kml(min_lat, min_lon, max_lat, max_lon):
    coords = (f"{min_lon},{min_lat},0 {max_lon},{min_lat},0 "
              f"{max_lon},{max_lat},0 {min_lon},{max_lat},0 {min_lon},{min_lat},0")
    return (f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords}"
            f"</coordinates></LinearRing></outerBoundaryIs></Polygon>")


# Covers SITE_LAT, SITE_LON (41.0, -75.5).
SITE_UNION_BOX = (40.9, -75.6, 41.1, -75.4)
# Well away from the site.
FAR_HIGHLY_LIKELY_BOX = (40.5, -75.6, 40.6, -75.4)


def _flash_event(event_id, union_kml=None, highly_likely_kml=None):
    """A flashFloods:search event dict plus the polygons map entry it needs."""
    event = {
        "forecastIssueTime": "2026-09-17T10:00:00Z",
        "forecastPeriodHours": 6,
        "affectedCountryCodes": ["US"],
        "eventPolygonId": f"{event_id}_event",
        "likelyAffectedPolygonId": f"{event_id}_likely",
        "highlyLikelyAffectedPolygonId": f"{event_id}_hl",
    }
    polygons = {}
    if union_kml is not None:
        polygons[f"{event_id}_event"] = union_kml
    if highly_likely_kml is not None:
        polygons[f"{event_id}_hl"] = highly_likely_kml
    return event, polygons


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def build(pairs, fail_search_after=None, flash_events=None, polygons=None,
          fail_flash_search=False):
    """A source wired to a fake transport, plus the record of what it asked for.

    `flash_events`: list of flashFloods:search event dicts (camelCase keys, as the API
    returns them). `polygons`: {polygon_id: kml_string} for serializedPolygons/{id}.
    """
    gauges = [g for g, _ in pairs]
    statuses = {s["gaugeId"]: s for _, s in pairs}
    flash_events = flash_events or []
    polygons = polygons or {}
    calls = {"search": [], "status": [], "flash_search": [], "polygon": [], "headers": []}

    def fetch(url, headers, body=None, timeout=20.0):
        calls["headers"].append(headers)
        if "gauges:searchGaugesByArea" in url:
            calls["search"].append((url, body))
            if fail_search_after is not None and len(calls["search"]) > fail_search_after:
                raise RuntimeError("search is down")
            return {"gauges": gauges}
        if "floodStatus:queryLatestFloodStatusByGaugeIds" in url:
            calls["status"].append(url)
            asked = parse_qs(urlparse(url).query).get("gaugeIds", [])
            return {"floodStatuses": [statuses[g] for g in asked if g in statuses]}
        if "flashFloods:search" in url:
            calls["flash_search"].append((url, body))
            if fail_flash_search:
                raise RuntimeError("flash search is down")
            return {"flashFloodEvents": flash_events}
        if "serializedPolygons/" in url:
            polygon_id = url.rsplit("/", 1)[-1]
            calls["polygon"].append(polygon_id)
            return {"polygonId": polygon_id, "kml": polygons.get(polygon_id, "")}
        raise AssertionError(f"unexpected URL: {url}")

    clock = Clock()
    src = GoogleFloods(SITE_LAT, SITE_LON, KEY, fetch=fetch, now_fn=clock)
    return src, calls, clock


# --- the reading itself ------------------------------------------------------------

def test_reads_the_severity_trend_and_distance_of_the_nearest_gauge():
    src, _, _ = build([at(3.0, "usgs_01534000", "ABOVE_NORMAL", "RISE")])
    out = src.poll()
    assert out["google_flood_severity"] == 1.0
    assert out["google_flood_trend"] == 1.0
    assert out["google_flood_gauge_mi"] == 3.0
    assert out["google_flood_gauges"] == 1.0


def test_the_whole_severity_ladder_is_ordered():
    """tiers.py compares these numerically, so the order has to hold."""
    for severity, expected in (("NO_FLOODING", 0.0), ("ABOVE_NORMAL", 1.0),
                               ("SEVERE", 2.0), ("EXTREME", 3.0)):
        src, _, _ = build([at(2.0, "g1", severity)])
        assert src.poll()["google_flood_severity"] == expected, severity


def test_the_trend_ladder_is_signed_so_rising_outranks_falling():
    for trend, expected in (("RISE", 1.0), ("NO_CHANGE", 0.0), ("FALL", -1.0)):
        src, _, _ = build([at(2.0, "g1", "SEVERE", trend)])
        assert src.poll()["google_flood_trend"] == expected, trend


def test_the_worst_severity_wins_over_the_nearest_gauge():
    """A quiet gauge 2 mi away does not cancel a flooding one 8 mi away; the point of
    reading several is to catch the one that is responding."""
    src, _, _ = build([at(2.0, "near", "NO_FLOODING"), at(8.0, "far", "SEVERE")])
    out = src.poll()
    assert out["google_flood_severity"] == 2.0
    assert out["google_flood_gauge_mi"] == 8.0


def test_the_nearest_gauge_breaks_a_severity_tie():
    src, _, _ = build([at(9.0, "far", "SEVERE"), at(4.0, "near", "SEVERE")])
    assert src.poll()["google_flood_gauge_mi"] == 4.0


def test_severity_trend_and_distance_all_describe_the_same_gauge():
    """Mixing a severity from one gauge with a trend from another would read as one
    coherent forecast and be nothing of the kind."""
    src, _, _ = build([at(2.0, "quiet", "NO_FLOODING", "FALL"),
                       at(7.0, "flooding", "EXTREME", "RISE")])
    out = src.poll()
    assert (out["google_flood_severity"], out["google_flood_trend"],
            out["google_flood_gauge_mi"]) == (3.0, 1.0, 7.0)


def test_unknown_severity_carries_no_reading():
    """Google's own 'not enough data to say'. Calling it NO_FLOODING would invent an
    all-clear out of an admission of ignorance."""
    src, _, _ = build([at(2.0, "g1", "UNKNOWN", "RISE")])
    out = src.poll()
    assert out["google_flood_severity"] is None
    assert out["google_flood_trend"] is None      # no governing gauge to take it from
    assert out["google_flood_gauge_mi"] is None
    assert out["google_flood_gauges"] == 1.0      # the gauge still answered


def test_an_unrecognized_severity_is_unknown_rather_than_no_flooding():
    """If Google adds or renames a level, mapping it to 0.0 silently downgrades whatever
    they meant -- the one failure mode here that could hide a real forecast."""
    src, _, _ = build([at(2.0, "g1", "CATASTROPHIC")])
    assert src.poll()["google_flood_severity"] is None


def test_a_gauge_with_an_unknown_severity_does_not_mask_a_readable_one():
    src, _, _ = build([at(2.0, "g1", "UNKNOWN"), at(6.0, "g2", "ABOVE_NORMAL")])
    out = src.poll()
    assert out["google_flood_severity"] == 1.0
    assert out["google_flood_gauge_mi"] == 6.0


def test_an_unrecognized_trend_is_unknown_but_keeps_the_severity():
    src, _, _ = build([at(2.0, "g1", "SEVERE", "FORECAST_TREND_UNSPECIFIED")])
    out = src.poll()
    assert out["google_flood_severity"] == 2.0
    assert out["google_flood_trend"] is None


# --- gauge discovery ---------------------------------------------------------------

def test_no_gauge_nearby_reports_zero_gauges_and_asks_for_no_statuses():
    """The likely steady state for a creek this small, and the answer to open question
    #2. Zero gauges is a real count, not a fault -- but with nothing to ask about there
    is no reason to spend a status call."""
    src, calls, _ = build([])
    out = src.poll()
    assert out["google_flood_gauges"] == 0.0
    assert out["google_flood_severity"] is None
    assert out["google_flood_gauge_mi"] is None
    assert calls["status"] == []


def test_gauges_outside_the_search_radius_are_dropped():
    """The box's corners reach ~1.41x the radius, so the API can legitimately return
    gauges further away than we asked about."""
    src, calls, _ = build([at(SEARCH_RADIUS_MI + 5.0, "too_far", "EXTREME"),
                           at(3.0, "near", "NO_FLOODING")])
    out = src.poll()
    assert out["google_flood_severity"] == 0.0        # the far EXTREME was not read
    assert out["google_flood_gauges"] == 1.0
    assert "too_far" not in calls["status"][0]


def test_only_the_nearest_gauges_are_queried():
    src, calls, _ = build([at(float(i) / 2 + 1, f"g{i}") for i in range(MAX_GAUGES + 6)])
    src.poll()
    asked = parse_qs(urlparse(calls["status"][0]).query)["gaugeIds"]
    assert len(asked) == MAX_GAUGES
    assert "g0" in asked and f"g{MAX_GAUGES + 5}" not in asked


def test_the_search_box_is_counter_clockwise_and_centred_on_the_site():
    """An S2 loop's interior lies to the left of its traversal, so a clockwise box names
    everything on Earth *outside* it -- legal, and a continent's worth of gauges."""
    src, calls, _ = build([])
    src.poll()
    vertices = calls["search"][0][1]["loop"]["vertices"]
    assert len(vertices) == 4
    # Shoelace area over (lon, lat); positive means counter-clockwise.
    area = sum(
        (vertices[i]["longitude"] * vertices[(i + 1) % 4]["latitude"]
         - vertices[(i + 1) % 4]["longitude"] * vertices[i]["latitude"])
        for i in range(4)
    )
    assert area > 0, vertices
    assert min(v["latitude"] for v in vertices) < SITE_LAT
    assert max(v["latitude"] for v in vertices) > SITE_LAT
    assert min(v["longitude"] for v in vertices) < SITE_LON
    assert max(v["longitude"] for v in vertices) > SITE_LON


def test_non_quality_verified_gauges_are_requested():
    """On a creek this small the only candidate is a virtual HydroBASINS gauge, and those
    are exactly what the quality-verified default filters out."""
    src, calls, _ = build([at(2.0, "hybas_7120", "SEVERE", verified=False)])
    out = src.poll()
    assert calls["search"][0][1]["includeNonQualityVerified"] is True
    assert out["google_flood_severity"] == 2.0


def test_gauges_without_a_hydro_model_are_not_requested():
    """No model means no flood status, so they would only be weight in the response."""
    src, calls, _ = build([])
    src.poll()
    assert calls["search"][0][1]["includeGaugesWithoutHydroModel"] is False


def test_the_gauge_list_is_cached_then_rediscovered():
    """Google asks that the list not be cached for long -- gauges come and go -- but
    re-searching on every 30-minute poll would be a search call for nothing."""
    src, calls, clock = build([at(2.0, "g1")])
    src.poll()
    clock.t += GAUGE_REFRESH_SECONDS / 2
    src.poll()
    assert len(calls["search"]) == 1 and len(calls["status"]) == 2
    clock.t += GAUGE_REFRESH_SECONDS
    src.poll()
    assert len(calls["search"]) == 2


def test_a_failed_rediscovery_keeps_reading_the_gauges_we_know():
    """Yesterday's gauges are still there; a search outage is no reason to go blind."""
    src, calls, clock = build([at(2.0, "g1", "SEVERE")], fail_search_after=1)
    assert src.poll()["google_flood_severity"] == 2.0
    clock.t += GAUGE_REFRESH_SECONDS + 1
    assert src.poll()["google_flood_severity"] == 2.0
    assert len(calls["status"]) == 2


def test_a_first_discovery_failure_fails_the_poll():
    """With no gauge list there is nothing to serve, and the coordinator's watchdog is
    the right place for that to surface -- not a silent row of Nones."""
    src, _, _ = build([at(2.0, "g1")], fail_search_after=0)
    try:
        src.poll()
    except RuntimeError:
        return
    raise AssertionError("a failed first discovery must not look like a successful poll")


# --- transport ---------------------------------------------------------------------

def test_the_api_key_travels_in_a_header_not_the_url():
    """`?key=` is documented, but a URL reaches log lines and tracebacks; the key must
    not ride along when a request fails."""
    src, calls, _ = build([at(2.0, "g1")])
    src.poll()
    assert all(h["X-Goog-Api-Key"] == KEY for h in calls["headers"])
    assert all(KEY not in url for url, _ in calls["search"])
    assert all(KEY not in url for url in calls["status"])


def test_gauge_ids_are_sent_as_repeated_query_parameters():
    src, calls, _ = build([at(2.0, "g1"), at(3.0, "g/2 odd")])
    src.poll()
    url = calls["status"][0]
    assert set(parse_qs(urlparse(url).query)["gaugeIds"]) == {"g1", "g/2 odd"}
    assert " " not in url and "floodStatus:queryLatestFloodStatusByGaugeIds" in url


def test_the_search_is_a_post_and_the_status_query_is_a_get():
    src, calls, _ = build([at(2.0, "g1")])
    src.poll()
    assert calls["search"][0][0].endswith("/gauges:searchGaugesByArea")
    assert len(calls["status"]) == 1     # GET -- fetch was called with body=None


def test_a_refused_key_says_what_to_check_rather_than_raising_a_bare_http_error():
    """This API needs a key *and* the API enabled on the issuing Cloud project. A project
    that never enabled it answers 403 for a valid key, which reads as a bad key and is
    not -- so the message has to name both."""
    import app.sources.google_floods as gf

    class Resp:
        status_code = 403

        def raise_for_status(self):
            raise AssertionError("should have been intercepted before raise_for_status")

    original = gf.requests
    gf.requests = type("R", (), {"get": staticmethod(lambda *a, **k: Resp()),
                                 "post": staticmethod(lambda *a, **k: Resp())})()
    try:
        gf._default_fetch("https://example.invalid", {"X-Goog-Api-Key": "bad"})
    except RuntimeError as exc:
        assert "403" in str(exc)
        assert "google_floods_api_key" in str(exc)
        assert "enabled" in str(exc)
    else:
        raise AssertionError("a refused key must not pass silently")
    finally:
        gf.requests = original


def test_a_gauge_with_no_usable_location_is_skipped_not_fatal():
    gauge, status = at(2.0, "g1", "SEVERE")
    broken = ({"gaugeId": "broken", "hasModel": True, "location": {}}, {"gaugeId": "broken"})
    src, _, _ = build([broken, (gauge, status)])
    assert src.poll()["google_flood_severity"] == 2.0


def test_an_empty_response_body_is_not_fatal():
    src, calls, _ = build([])

    def empty_fetch(url, headers, body=None, timeout=20.0):
        return {}

    src._fetch = empty_fetch
    assert src.poll()["google_flood_gauges"] == 0.0


# --- flash flood polygons ------------------------------------------------------------

def test_site_outside_every_event_polygon_reports_zero_and_skips_the_highly_likely_fetch():
    event, polygons = _flash_event("ev1", union_kml=_rect_kml(*FAR_HIGHLY_LIKELY_BOX))
    src, calls, _ = build([], flash_events=[event], polygons=polygons)
    out = src.poll()
    assert out["google_flash_flood_likely"] == 0.0
    assert out["google_flash_flood_highly_likely"] == 0.0
    assert out["google_flash_flood_events"] == 0.0
    assert calls["polygon"] == ["ev1_event"]     # union checked; highly-likely never fetched


def test_site_inside_the_union_but_outside_highly_likely_is_likely_only():
    event, polygons = _flash_event(
        "ev1", union_kml=_rect_kml(*SITE_UNION_BOX),
        highly_likely_kml=_rect_kml(*FAR_HIGHLY_LIKELY_BOX))
    src, calls, _ = build([], flash_events=[event], polygons=polygons)
    out = src.poll()
    assert out["google_flash_flood_likely"] == 1.0
    assert out["google_flash_flood_highly_likely"] == 0.0
    assert out["google_flash_flood_events"] == 1.0
    assert calls["polygon"] == ["ev1_event", "ev1_hl"]


def test_site_inside_highly_likely_is_highly_likely_only():
    event, polygons = _flash_event(
        "ev1", union_kml=_rect_kml(*SITE_UNION_BOX),
        highly_likely_kml=_rect_kml(*SITE_UNION_BOX))
    src, _, _ = build([], flash_events=[event], polygons=polygons)
    out = src.poll()
    assert out["google_flash_flood_highly_likely"] == 1.0
    assert out["google_flash_flood_likely"] == 0.0    # not both set
    assert out["google_flash_flood_events"] == 1.0


def test_two_overlapping_events_both_containing_the_site_count_both():
    ev1, poly1 = _flash_event("ev1", union_kml=_rect_kml(*SITE_UNION_BOX))
    ev2, poly2 = _flash_event("ev2", union_kml=_rect_kml(*SITE_UNION_BOX))
    src, _, _ = build([], flash_events=[ev1, ev2], polygons={**poly1, **poly2})
    assert src.poll()["google_flash_flood_events"] == 2.0


def test_a_polygon_fetch_failure_skips_just_that_event_not_the_whole_poll():
    ev_broken, _ = _flash_event("broken")   # its polygon id has no entry in `polygons`
    ev_ok, poly_ok = _flash_event("ok", union_kml=_rect_kml(*SITE_UNION_BOX))

    def flaky_fetch_factory(base_calls):
        def fetch(url, headers, body=None, timeout=20.0):
            if "serializedPolygons/broken_event" in url:
                raise RuntimeError("polygon service down")
            return base_calls(url, headers, body, timeout)
        return fetch

    src, calls, _ = build([], flash_events=[ev_broken, ev_ok], polygons=poly_ok)
    src._fetch = flaky_fetch_factory(src._fetch)
    out = src.poll()
    assert out["google_flash_flood_events"] == 1.0        # only "ok" counted
    assert out["google_flash_flood_likely"] == 1.0


def test_flash_flood_search_failure_does_not_break_the_gauge_read():
    """A different Google product on the same key, reachable independently of gauge
    coverage -- an outage here must not freeze a healthy gauge-severity read too (unlike
    a floodStatus query failure, which legitimately does fail the whole poll, since that
    IS the gauge read)."""
    src, _, _ = build([at(2.0, "g1", "SEVERE")], flash_events=[], fail_flash_search=True)
    out = src.poll()
    assert out["google_flood_severity"] == 2.0          # gauge read still works
    assert out["google_flash_flood_likely"] is None     # flash flood read: unknown
    assert out["google_flash_flood_highly_likely"] is None
    assert out["google_flash_flood_events"] is None


def test_an_event_with_no_polygon_id_is_skipped_not_fatal():
    event = {"forecastIssueTime": "2026-09-17T10:00:00Z", "forecastPeriodHours": 6}
    src, calls, _ = build([], flash_events=[event])
    out = src.poll()
    assert out["google_flash_flood_events"] == 0.0
    assert calls["polygon"] == []


def test_flash_flood_search_requests_the_full_page_size():
    src, calls, _ = build([], flash_events=[])
    src.poll()
    assert calls["flash_search"][0][1]["pageSize"] == PAGE_SIZE


def test_more_than_the_warn_count_of_events_are_capped_not_all_resolved():
    events = [_flash_event(f"ev{i}")[0] for i in range(FLASH_FLOOD_EVENT_WARN_COUNT + 5)]
    src, calls, _ = build([], flash_events=events)
    src.poll()
    assert len(calls["polygon"]) == FLASH_FLOOD_EVENT_WARN_COUNT


def test_a_polygons_geometry_is_cached_across_polls():
    event, polygons = _flash_event("ev1", union_kml=_rect_kml(*FAR_HIGHLY_LIKELY_BOX))
    src, calls, clock = build([], flash_events=[event], polygons=polygons)
    src.poll()
    clock.t += 1.0
    src.poll()
    assert calls["polygon"].count("ev1_event") == 1    # fetched once, reused the second time


def test_a_wall_clock_budget_stops_resolving_further_events():
    ev1, poly1 = _flash_event("ev1", union_kml=_rect_kml(*FAR_HIGHLY_LIKELY_BOX))
    ev2, poly2 = _flash_event("ev2", union_kml=_rect_kml(*SITE_UNION_BOX))
    src, calls, clock = build([], flash_events=[ev1, ev2], polygons={**poly1, **poly2})

    real_fetch = src._fetch

    def slow_fetch(url, headers, body=None, timeout=20.0):
        if "serializedPolygons/ev1_event" in url:
            clock.t += FLASH_FLOOD_POLL_BUDGET_SECONDS + 1
        return real_fetch(url, headers, body, timeout)

    src._fetch = slow_fetch
    out = src.poll()
    assert calls["polygon"] == ["ev1_event"]           # ev2 never reached
    assert out["google_flash_flood_likely"] is None    # unresolved, not a confident 0.0


def test_one_highly_likely_event_and_one_merely_likely_event_stay_mutually_exclusive():
    """Reviewer-caught gap: two *simultaneous* events used to be able to set both flags
    to 1.0 at once, contradicting the "mutually describe the worst event" contract
    documented in creek-flood-warning-spec.md and this file's own earlier tests."""
    ev1, poly1 = _flash_event("ev1", union_kml=_rect_kml(*SITE_UNION_BOX),
                               highly_likely_kml=_rect_kml(*SITE_UNION_BOX))
    ev2, poly2 = _flash_event("ev2", union_kml=_rect_kml(*SITE_UNION_BOX),
                               highly_likely_kml=_rect_kml(*FAR_HIGHLY_LIKELY_BOX))
    src, _, _ = build([], flash_events=[ev1, ev2], polygons={**poly1, **poly2})
    out = src.poll()
    assert out["google_flash_flood_highly_likely"] == 1.0
    assert out["google_flash_flood_likely"] == 0.0
    assert out["google_flash_flood_events"] == 2.0


def test_unparseable_polygon_kml_is_treated_as_outside_not_a_crash():
    event, _ = _flash_event("ev1")
    polygons = {"ev1_event": "<Placemark><name>weird</name></Placemark>"}
    src, calls, _ = build([], flash_events=[event], polygons=polygons)
    out = src.poll()
    assert out["google_flash_flood_events"] == 0.0


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
