"""Plain-assert tests for the Google Flood Forecasting source.
Run: python rate_of_rise/tests/test_google_floods.py"""
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.google_floods import (  # noqa: E402
    GAUGE_REFRESH_SECONDS, MAX_GAUGES, MI_PER_DEG_LAT, SEARCH_RADIUS_MI, GoogleFloods,
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


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def build(pairs, fail_search_after=None):
    """A source wired to a fake transport, plus the record of what it asked for."""
    gauges = [g for g, _ in pairs]
    statuses = {s["gaugeId"]: s for _, s in pairs}
    calls = {"search": [], "status": [], "headers": []}

    def fetch(url, headers, body=None, timeout=20.0):
        calls["headers"].append(headers)
        if body is not None:
            calls["search"].append((url, body))
            if fail_search_after is not None and len(calls["search"]) > fail_search_after:
                raise RuntimeError("search is down")
            return {"gauges": gauges}
        calls["status"].append(url)
        asked = parse_qs(urlparse(url).query).get("gaugeIds", [])
        return {"floodStatuses": [statuses[g] for g in asked if g in statuses]}

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


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
