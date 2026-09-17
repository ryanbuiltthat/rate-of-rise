"""Plain-assert tests for KML polygon parsing and point-in-polygon testing.
Run: python rate_of_rise/tests/test_kml_geometry.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.kml_geometry import point_in_kml, parse_kml_rings  # noqa: E402


def _rect_kml(min_lat, min_lon, max_lat, max_lon, hole=None):
    """A single <Polygon> rectangle, optionally with one rectangular hole.

    `hole`, if given, is (min_lat, min_lon, max_lat, max_lon) for an
    <innerBoundaryIs> ring nested in the same <Polygon>.
    """
    def ring(a, b, c, d):
        return f"{b},{a},0 {d},{a},0 {d},{c},0 {b},{c},0 {b},{a},0"

    outer = (f"<outerBoundaryIs><LinearRing><coordinates>"
             f"{ring(min_lat, min_lon, max_lat, max_lon)}"
             f"</coordinates></LinearRing></outerBoundaryIs>")
    inner = ""
    if hole is not None:
        inner = (f"<innerBoundaryIs><LinearRing><coordinates>"
                 f"{ring(*hole)}</coordinates></LinearRing></innerBoundaryIs>")
    return f"<Polygon>{outer}{inner}</Polygon>"


# A 41.0,-75.5-centred box, matching test_google_floods.py's SITE_LAT/SITE_LON.
SITE_BOX = (40.9, -75.6, 41.1, -75.4)


def test_a_point_well_inside_the_rectangle_is_inside():
    assert point_in_kml(41.0, -75.5, _rect_kml(*SITE_BOX)) is True


def test_a_point_well_outside_the_rectangle_is_outside():
    assert point_in_kml(42.0, -75.5, _rect_kml(*SITE_BOX)) is False


def test_a_point_exactly_on_an_edge_counts_as_inside():
    """The conservative direction for a flood warning (design spec, KML section):
    a boundary case must not silently read as "not affected"."""
    assert point_in_kml(40.9, -75.5, _rect_kml(*SITE_BOX)) is True    # bottom edge
    assert point_in_kml(41.0, -75.6, _rect_kml(*SITE_BOX)) is True    # left edge


def test_a_point_inside_a_hole_is_outside_even_though_inside_the_outer_ring():
    hole = (40.95, -75.55, 41.05, -75.45)
    kml = _rect_kml(*SITE_BOX, hole=hole)
    assert point_in_kml(41.0, -75.5, kml) is False       # centre of the hole
    assert point_in_kml(40.92, -75.58, kml) is True       # inside outer, outside hole


def test_a_multigeometry_of_two_disjoint_polygons_checks_both():
    far_box = (41.9, -76.6, 42.1, -76.4)
    kml = (f"<MultiGeometry>{_rect_kml(*SITE_BOX)}{_rect_kml(*far_box)}"
           f"</MultiGeometry>")
    assert point_in_kml(41.0, -75.5, kml) is True     # in the first polygon
    assert point_in_kml(42.0, -76.5, kml) is True     # in the second polygon
    assert point_in_kml(0.0, 0.0, kml) is False        # in neither


def test_a_bare_coordinates_block_with_no_polygon_wrapper_is_still_read():
    """GetSerializedPolygon's documented shape is a <Polygon>, but the parser must not
    assume it: a bare <coordinates> block (no <Polygon>/<outerBoundaryIs>) is the
    fallback case, read as a single outer ring."""
    coords = "-75.6,40.9,0 -75.4,40.9,0 -75.4,41.1,0 -75.6,41.1,0 -75.6,40.9,0"
    kml = f"<coordinates>{coords}</coordinates>"
    assert point_in_kml(41.0, -75.5, kml) is True
    assert point_in_kml(42.0, -75.5, kml) is False


def test_empty_kml_has_no_rings_and_contains_nothing():
    assert parse_kml_rings("") == []
    assert point_in_kml(41.0, -75.5, "") is False


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
