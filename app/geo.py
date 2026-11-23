import math
import random

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


def encode(points: list[tuple[float, float]]) -> str | None:
    """Inverse of decode()."""
    return polyline_codec.encode(points) if points else None


# ---------------------------------------------------------------------------
# Privacy
#
# A route's first and last points are usually where the athlete lives or works.
# Before a path leaves the server we cut a stretch off each end, like Strava's
# own privacy zones. The cut length varies per activity (so nobody can add a
# fixed amount back) but is stable for the same activity (so repeated requests
# can't be averaged to find the real end). Everything shown to the user, and
# every distance we report, is measured from the trimmed path.
# ---------------------------------------------------------------------------

TRIM_MIN_M = 300
TRIM_MAX_M = 600
MIN_VISIBLE_M = 200     # if less than this would remain, show no path at all
SNAP_DEG = 0.005        # ~500 m grid for activities with no GPS path to trim


def trim_lengths_m(activity_id: int) -> tuple[float, float]:
    """How many metres to hide at the start and end of this activity's path."""
    rng = random.Random(f"trim:{activity_id}")
    return rng.uniform(TRIM_MIN_M, TRIM_MAX_M), rng.uniform(TRIM_MIN_M, TRIM_MAX_M)


def _cut_start(points: list[tuple[float, float]], cut_m: float) -> list[tuple[float, float]]:
    """Drop the first cut_m metres of a path, interpolating the new first point."""
    walked = 0.0
    for i, (a, b) in enumerate(zip(points, points[1:])):
        seg = haversine_km(*a, *b) * 1000
        if walked + seg >= cut_m:
            t = (cut_m - walked) / seg if seg else 0.0
            new_start = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            return [new_start] + points[i + 1:]
        walked += seg
    return []


def path_length_m(points: list[tuple[float, float]]) -> float:
    return sum(haversine_km(*a, *b) for a, b in zip(points, points[1:])) * 1000


def trim_ends(points: list[tuple[float, float]], activity_id: int) -> list[tuple[float, float]]:
    """The part of a path that is safe to show: both ends cut off. Empty if the route is too short."""
    head, tail = trim_lengths_m(activity_id)
    if path_length_m(points) < head + tail + MIN_VISIBLE_M:
        return []
    trimmed = _cut_start(points, head)
    return _cut_start(trimmed[::-1], tail)[::-1]


def snap(lat: float, lng: float, step_deg: float = SNAP_DEG) -> tuple[float, float]:
    """Round a point to the centre of a coarse grid cell (for starts we can't trim)."""
    return ((math.floor(lat / step_deg) + 0.5) * step_deg,
            (math.floor(lng / step_deg) + 0.5) * step_deg)


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
