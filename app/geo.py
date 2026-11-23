import math

import polyline as polyline_codec

EARTH_RADIUS_KM = 6371.0
EARTH_RADIUS_M = EARTH_RADIUS_KM * 1000


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points, in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def decode(encoded: str | None) -> list[tuple[float, float]]:
    """Decode a Google-encoded polyline (Strava's map.summary_polyline) into (lat, lng) points."""
    if not encoded:
        return []
    return polyline_codec.decode(encoded)


def is_loop(points: list[tuple[float, float]], tolerance_km: float = 0.5) -> bool:
    """True if the route ends within tolerance_km of where it started."""
    if len(points) < 2:
        return False
    (lat1, lng1), (lat2, lng2) = points[0], points[-1]
    return haversine_km(lat1, lng1, lat2, lng2) <= tolerance_km


# ---------------------------------------------------------------------------
# Shape matching
#
# To decide whether two activities followed the same route, we lay a grid of
# CELL_M x CELL_M squares over the map and record which cells each route passes
# through. Two routes are the same if most of the cells one visits are on (or
# right next to) cells the other visits, checked in both directions.
#
# Why a grid instead of comparing points directly:
#   * Speed: a route becomes a set of ~100-400 cells, and checking overlap is
#     just set lookups, O(n), instead of comparing every point to every
#     segment, O(n*m). That matters when grouping ~1000 activities.
#   * Robustness: GPS noise and different sampling rates move points by tens
#     of metres, which rarely changes the cell, and the one-cell "dilation"
#     absorbs the rest.
#   * Direction doesn't matter: a loop ridden clockwise and counter-clockwise
#     covers the same cells, so it counts as one route.
# ---------------------------------------------------------------------------

CELL_M = 150          # grid cell size, roughly the matching tolerance
SAMPLE_STEP_M = 75    # walk each route at this spacing so no cell is skipped
MIN_OVERLAP = 0.85    # share of each route that must lie on the other


def to_xy(points: list[tuple[float, float]], ref_lat: float) -> list[tuple[float, float]]:
    """Project lat/lng to flat x/y metres (equirectangular). Accurate to well under 1% across a city."""
    k = math.cos(math.radians(ref_lat))
    return [(math.radians(lng) * EARTH_RADIUS_M * k, math.radians(lat) * EARTH_RADIUS_M) for lat, lng in points]


def densify(xy: list[tuple[float, float]], step_m: float = SAMPLE_STEP_M) -> list[tuple[float, float]]:
    """Add points along each segment so consecutive points are at most step_m apart.

    summary_polyline is simplified, so a straight road can be one 2 km segment;
    without this, the cells in the middle of that segment would be missed.
    """
    if not xy:
        return []
    out = [xy[0]]
    for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
        n = max(1, math.ceil(math.hypot(x2 - x1, y2 - y1) / step_m))
        out.extend((x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n) for i in range(1, n + 1))
    return out


def cells(xy: list[tuple[float, float]], size_m: float = CELL_M) -> frozenset[tuple[int, int]]:
    """The set of grid cells a (densified) path passes through."""
    return frozenset((math.floor(x / size_m), math.floor(y / size_m)) for x, y in xy)


def dilate(cell_set: frozenset[tuple[int, int]]) -> frozenset[tuple[int, int]]:
    """Grow a cell set by one cell in every direction (the 3x3 neighbourhood)."""
    return frozenset((cx + dx, cy + dy) for cx, cy in cell_set for dx in (-1, 0, 1) for dy in (-1, 0, 1))


class Shape:
    """Precomputed grid footprint of one route, so each comparison is cheap."""

    __slots__ = ("cells", "near")

    def __init__(self, points: list[tuple[float, float]], ref_lat: float):
        self.cells = cells(densify(to_xy(points, ref_lat)))
        self.near = dilate(self.cells)

    def overlap(self, other: "Shape") -> float:
        """Fraction of this route's cells that lie on or next to the other route."""
        if not self.cells:
            return 0.0
        return len(self.cells & other.near) / len(self.cells)


def same_shape(a: Shape, b: Shape, min_overlap: float = MIN_OVERLAP) -> bool:
    """True if each route lies mostly along the other (symmetric, so a short route
    that is just the first half of a long one does NOT match)."""
    return a.overlap(b) >= min_overlap and b.overlap(a) >= min_overlap
