"""Shape matching: deciding whether two GPS paths are the same route."""

import math
import random
from datetime import datetime, timedelta, timezone

import polyline

from app import scoring
from app.geo import Shape, route_type, same_shape

HOME = (47.6600, -122.3000)
M_PER_DEG_LAT = 111_320


def offset(lat, lng, east_m, north_m):
    """Move a point by metres east/north."""
    return (lat + north_m / M_PER_DEG_LAT,
            lng + east_m / (M_PER_DEG_LAT * math.cos(math.radians(lat))))


def path(waypoints_m, jitter_m=0.0, seed=0, start=HOME):
    """Turn (east, north) waypoints in metres into lat/lng points, with optional GPS noise."""
    rng = random.Random(seed)
    return [offset(*start, e + rng.uniform(-jitter_m, jitter_m), n + rng.uniform(-jitter_m, jitter_m))
            for e, n in waypoints_m]


def box(width_m, height_m, direction=1, steps=8):
    """A rectangular loop starting and ending at home, going east (direction=1) or west (-1)."""
    corners = [(0, 0), (direction * width_m, 0), (direction * width_m, height_m), (0, height_m), (0, 0)]
    pts = []
    for (x1, y1), (x2, y2) in zip(corners, corners[1:]):
        pts += [(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps) for i in range(steps)]
    return pts + [corners[-1]]


def shape(points):
    return Shape(points, HOME[0])


def row(id, points, days_ago, sport="Ride"):
    length_m = sum(math.dist(scoring_xy(a), scoring_xy(b)) for a, b in zip(points, points[1:]))
    return {
        "id": id, "name": f"ride {id}", "sport_type": sport, "distance_m": length_m,
        "elev_gain_m": 100, "start_lat": points[0][0], "start_lng": points[0][1],
        "polyline": polyline.encode(points) if points else None,
        "start_date": (datetime(2026, 11, 23, tzinfo=timezone.utc) - timedelta(days=days_ago)).isoformat(),
    }


def scoring_xy(p):
    lat, lng = p
    return (lng * M_PER_DEG_LAT * math.cos(math.radians(HOME[0])), lat * M_PER_DEG_LAT)


LOOP = box(10_000, 10_000)  # a 40 km square loop east of home


def test_identical_route_with_gps_noise_matches():
    assert same_shape(shape(path(LOOP, jitter_m=25, seed=1)), shape(path(LOOP, jitter_m=25, seed=2)))


def test_different_sampling_density_matches():
    sparse = path(LOOP[::4] + [LOOP[-1]])          # like a heavily simplified polyline
    assert same_shape(shape(path(LOOP)), shape(sparse))


def test_reversed_direction_matches():
    assert same_shape(shape(path(LOOP)), shape(path(list(reversed(LOOP)))))


def test_small_detour_still_matches():
    # Same 40 km loop plus a 1 km out-and-back spur halfway along the first side.
    detour = LOOP[:4] + [(5_000, 0), (5_000, -1_000), (5_000, 0)] + LOOP[4:]
    assert same_shape(shape(path(LOOP)), shape(path(detour)))


def test_mirror_image_loop_does_not_match():
    # Same start, same 40 km distance, but heading west instead of east.
    # The old "same start + same distance" rule wrongly merged these.
    assert not same_shape(shape(path(LOOP)), shape(path(box(10_000, 10_000, direction=-1))))


def test_routes_that_share_only_the_first_half_do_not_match():
    diverging = LOOP[:16] + [(10_000, 10_000 + 5_000 * i / 8) for i in range(1, 9)] + [(0, 15_000), (0, 0)]
    assert not same_shape(shape(path(LOOP)), shape(path(diverging)))


def test_short_route_inside_long_route_does_not_match():
    # The first side of the loop is fully contained in the loop, but the loop is not contained in it.
    first_side = path(LOOP[:9])
    a, b = shape(first_side), shape(path(LOOP))
    assert a.overlap(b) > 0.95      # every cell of the short route is on the loop...
    assert not same_shape(a, b)     # ...but the match is checked both ways


def test_group_routes_merges_by_shape_and_counts():
    rows = [
        row(1, path(LOOP, jitter_m=20, seed=1), days_ago=30),
        row(2, path(list(reversed(LOOP)), jitter_m=20, seed=2), days_ago=5),
        row(3, path(box(10_000, 10_000, direction=-1)), days_ago=2),   # mirror loop: different route
    ]
    cands = scoring.group_routes(rows)
    assert len(cands) == 2
    east = next(c for c in cands if c.activity_id != 3)
    assert east.activity_id == 2 and east.times_done == 2   # most recent kept, both counted


def test_activities_without_gps_fall_back_to_start_and_distance():
    a, b = row(1, path(LOOP), 10), row(2, path(LOOP), 5)
    a["polyline"] = b["polyline"] = None
    assert len(scoring.group_routes([a, b])) == 1


# --- route type -------------------------------------------------------------

OUT = [(0, 0), (2000, 500), (4000, 0), (6000, 1500), (8000, 1000)]   # a wiggly 8 km road east


def test_loop_is_loop():
    assert route_type(path(LOOP)) == "loop"


def test_out_and_back_is_detected():
    assert route_type(path(OUT + OUT[-2::-1])) == "out_and_back"


def test_out_and_back_with_gps_noise_and_other_side_of_road():
    there = path(OUT, jitter_m=15, seed=1)
    back = path([(e, n + 20) for e, n in OUT[::-1]], jitter_m=15, seed=2)  # 20 m over, like the other lane
    assert route_type(there + back) == "out_and_back"


def test_out_and_back_with_small_detour_on_the_way_back():
    back = OUT[::-1][:2] + [(5000, 1600)] + OUT[::-1][2:]   # a short detour off the road
    assert route_type(path(OUT + back)) == "out_and_back"


def test_two_laps_of_a_loop_is_a_loop_not_out_and_back():
    small = box(2000, 1000)
    assert route_type(path(small + small[1:])) == "loop"


def test_lollipop_with_short_stem_is_a_loop():
    stem = [(0, 0), (1000, 0)]
    loop = [(1000, 0), (1000, 4000), (5000, 4000), (5000, 0), (1000, 0)]
    assert route_type(path(stem + loop[1:] + stem[::-1])) == "loop"


def test_point_to_point_and_no_gps():
    assert route_type(path(OUT)) == "point_to_point"
    assert route_type([]) is None
