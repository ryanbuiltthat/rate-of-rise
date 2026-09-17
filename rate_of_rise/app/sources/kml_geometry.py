"""KML polygon parsing and point-in-polygon testing for Google Flash Flood events.

GetSerializedPolygon returns a KML string, not a coordinate array (see
app/sources/google_floods.py's module docstring). A KML <Polygon> carries one
<outerBoundaryIs> ring and zero or more <innerBoundaryIs> holes, and a flash-flood
event's geometry can itself be a <MultiGeometry> of several disjoint <Polygon>s.

No XML or geometry library is added for this — KML's <coordinates> block is flat,
whitespace/comma-delimited "lon,lat[,alt] ..." text, and ray-casting over the resulting
point list is small enough to hand-roll and pin with tests, matching this package's
existing choice (google_floods.py's hand-rolled great-circle distance) to avoid a geo
dependency for one narrow need.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_COORDINATES_RE = re.compile(r"<coordinates>(.*?)</coordinates>", re.DOTALL)
_OUTER_RE = re.compile(r"<outerBoundaryIs>(.*?)</outerBoundaryIs>", re.DOTALL)
_INNER_RE = re.compile(r"<innerBoundaryIs>(.*?)</innerBoundaryIs>", re.DOTALL)
_POLYGON_RE = re.compile(r"<Polygon>(.*?)</Polygon>", re.DOTALL)

# Ray-cast/on-segment tolerance in degrees — far tighter than any real coordinate
# rounding in a Google-served KML, loose enough to absorb float round-trip noise.
_EPS = 1e-9


@dataclass
class Ring:
    outer: list[tuple[float, float]]
    holes: list[list[tuple[float, float]]] = field(default_factory=list)


def _parse_ring(coordinates_block: str) -> list[tuple[float, float]]:
    """A KML <coordinates> block to a list of (lat, lon) — KML orders lon,lat[,alt]."""
    points = []
    for tup in coordinates_block.split():
        lon_str, lat_str, *_ = tup.split(",")
        points.append((float(lat_str), float(lon_str)))
    return points


def parse_kml_rings(kml: str) -> list[Ring]:
    """Every polygon in the document: each <Polygon> (bare, or inside a
    <MultiGeometry>) with its outer ring and holes, or — if there is no <Polygon>
    wrapper at all — a single bare <coordinates> block read as one outer ring."""
    polygon_blocks = _POLYGON_RE.findall(kml)
    if not polygon_blocks:
        coords = _COORDINATES_RE.findall(kml)
        return [Ring(outer=_parse_ring(coords[0]))] if coords else []

    rings = []
    for block in polygon_blocks:
        outer_match = _OUTER_RE.search(block)
        if not outer_match:
            continue
        outer_coords = _COORDINATES_RE.findall(outer_match.group(1))
        if not outer_coords:
            continue
        holes = []
        for inner_block in _INNER_RE.findall(block):
            inner_coords = _COORDINATES_RE.findall(inner_block)
            if inner_coords:
                holes.append(_parse_ring(inner_coords[0]))
        rings.append(Ring(outer=_parse_ring(outer_coords[0]), holes=holes))
    return rings


def _on_segment(lat: float, lon: float, lat1: float, lon1: float,
                 lat2: float, lon2: float) -> bool:
    # Handle degenerate edges (single point)
    if lat1 == lat2 and lon1 == lon2:
        return lat == lat1 and lon == lon1

    cross = (lon2 - lon1) * (lat - lat1) - (lat2 - lat1) * (lon - lon1)
    if abs(cross) > _EPS:
        return False
    dot = (lat - lat1) * (lat2 - lat1) + (lon - lon1) * (lon2 - lon1)
    if dot < 0:
        return False
    length_sq = (lat2 - lat1) ** 2 + (lon2 - lon1) ** 2
    return dot <= length_sq + _EPS


def _point_in_ring(lat: float, lon: float, ring: list[tuple[float, float]]) -> bool:
    """Ray-casting (odd crossing count = inside), with an explicit on-edge check first
    so a boundary point reads as inside regardless of crossing parity — the
    conservative direction for a flood warning (a boundary case must not silently
    read as "not affected")."""
    n = len(ring)
    for i in range(n):
        lat1, lon1 = ring[i]
        lat2, lon2 = ring[(i + 1) % n]
        if _on_segment(lat, lon, lat1, lon1, lat2, lon2):
            return True

    inside = False
    j = n - 1
    for i in range(n):
        lat_i, lon_i = ring[i]
        lat_j, lon_j = ring[j]
        if (lat_i > lat) != (lat_j > lat):
            lon_intersect = lon_i + (lat - lat_i) * (lon_j - lon_i) / (lat_j - lat_i)
            if lon < lon_intersect:
                inside = not inside
        j = i
    return inside


def point_in_rings(lat: float, lon: float, rings: list[Ring]) -> bool:
    """Inside any ring's outer boundary and not inside that ring's own holes."""
    for ring in rings:
        if not _point_in_ring(lat, lon, ring.outer):
            continue
        if any(_point_in_ring(lat, lon, hole) for hole in ring.holes):
            continue
        return True
    return False


def point_in_kml(lat: float, lon: float, kml: str) -> bool:
    return point_in_rings(lat, lon, parse_kml_rings(kml))
