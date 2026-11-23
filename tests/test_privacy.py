"""The real start and end of a route (usually someone's home) must never leave the server."""

from datetime import datetime, timedelta, timezone

import polyline
import pytest
from fastapi.testclient import TestClient

from app import config, db, scoring
from app.geo import TRIM_MAX_M, TRIM_MIN_M, haversine_km, path_length_m, trim_ends, trim_lengths_m
from tests.conftest import sign_in
from tests.test_shapes import HOME, box, path

NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)
LOOP = path(box(3000, 2000))          # 10 km loop starting and ending at HOME
OUT_BACK_END = path([(0, 0), (4000, 0)])[-1]


def nearest_m(points, target):
    return min(haversine_km(*p, *target) for p in points) * 1000


def test_trim_lengths_vary_per_activity_but_are_stable():
    assert trim_lengths_m(1) == trim_lengths_m(1)
    assert trim_lengths_m(1) != trim_lengths_m(2)
    for i in range(50):
        assert all(TRIM_MIN_M <= m <= TRIM_MAX_M for m in trim_lengths_m(i))


def test_trim_hides_both_ends():
    pts = path([(0, 0), (4000, 0)])
    visible = trim_ends(pts, activity_id=7)
    head, tail = trim_lengths_m(7)
    assert nearest_m(visible, HOME) == pytest.approx(head, abs=5)
    assert nearest_m(visible, OUT_BACK_END) == pytest.approx(tail, abs=5)
    assert path_length_m(visible) == pytest.approx(4000 - head - tail, abs=5)


def test_trim_of_loop_keeps_away_from_home():
    visible = trim_ends(LOOP, activity_id=3)
    assert nearest_m(visible, HOME) >= TRIM_MIN_M - 5


def test_short_route_shows_nothing():
    assert trim_ends(path([(0, 0), (900, 0)]), activity_id=1) == []


def candidate_row(id, pts, days_ago=10):
    return {"id": id, "name": "loop", "sport_type": "Ride", "distance_m": path_length_m(pts),
            "elev_gain_m": 50, "start_lat": pts[0][0], "start_lng": pts[0][1],
            "polyline": polyline.encode(pts), "start_date": (NOW - timedelta(days=days_ago)).isoformat()}


def test_route_type_still_uses_full_path():
    [rec] = scoring.recommend(scoring.group_routes([candidate_row(1, LOOP)]), *HOME, 10, now=NOW)
    assert rec.route_type == "loop"


def test_distance_is_rounded_and_measured_from_trimmed_start():
    [rec] = scoring.recommend(scoring.group_routes([candidate_row(1, LOOP)]), *HOME, 10, now=NOW)
    assert rec.start_km_away in (0.0, 0.5, 1.0)
    assert (rec.start_km_away * 2) == int(rec.start_km_away * 2)


def test_no_gps_start_is_snapped():
    row = {**candidate_row(1, LOOP), "polyline": None}
    [c] = scoring.group_routes([row])
    assert c.public_start != (row["start_lat"], row["start_lng"])
    assert haversine_km(*c.public_start, row["start_lat"], row["start_lng"]) < 0.5


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(main, "_route_cache", {})  # other tests cache a different activity 1
    with TestClient(main.app) as c:
        conn = db.connect()
        db.save_athlete(conn, 42, "Test Athlete", "tok", "refresh", 9_999_999_999)
        db.upsert_activity(conn, {**candidate_row(1, LOOP, days_ago=30), "athlete_id": 42})
        conn.commit()
        conn.close()
        sign_in(c, monkeypatch)
        yield c


def test_api_polyline_never_comes_near_home(client):
    body = client.get("/me/recommendations", params={
        "lat": HOME[0], "lng": HOME[1], "distance_mi": 6.2, "include_polyline": "true"}).json()
    pts = polyline.decode(body[0]["polyline"])
    assert pts
    assert nearest_m(pts, HOME) >= TRIM_MIN_M - 5
