from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        conn = db.connect()
        db.save_athlete(conn, 42, "Test Athlete", "tok", "refresh", 9_999_999_999)
        start = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        db.upsert_activity(conn, {
            "id": 1, "athlete_id": 42, "name": "Lake loop", "sport_type": "Ride",
            "distance_m": 40_000, "elev_gain_m": 300, "start_lat": 47.66, "start_lng": -122.30,
            "polyline": None, "start_date": start,
        })
        conn.commit()
        conn.close()
        yield c


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_recommendations_returns_route(client):
    resp = client.get("/athletes/42/recommendations",
                      params={"lat": 47.66, "lng": -122.30, "distance_mi": 25})
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["name"] == "Lake loop"
    assert body[0]["strava_activity_url"].endswith("/activities/1")


def test_recommendations_unknown_athlete(client):
    resp = client.get("/athletes/7/recommendations",
                      params={"lat": 47.66, "lng": -122.30, "distance_mi": 25})
    assert resp.status_code == 404


@pytest.mark.parametrize("params", [
    {"lat": 200, "lng": 0, "distance_mi": 25},      # invalid latitude
    {"lat": 47, "lng": -122, "distance_mi": -5},    # negative distance
    {"lat": 47, "lng": -122, "distance_mi": 25, "sport": "swim"},  # unsupported sport
])
def test_recommendations_validates_input(client, params):
    assert client.get("/athletes/42/recommendations", params=params).status_code == 422


def test_polyline_hidden_by_default(client):
    params = {"lat": 47.66, "lng": -122.30, "distance_mi": 25}
    assert "polyline" not in client.get("/athletes/42/recommendations", params=params).json()[0]
    params["include_polyline"] = "true"
    assert "polyline" in client.get("/athletes/42/recommendations", params=params).json()[0]


def test_home_page_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Route Finder" in resp.text


def test_recommendations_use_miles_and_feet(client):
    params = {"lat": 47.66, "lng": -122.30, "distance_mi": 25, "max_elev_ft": 1500}
    r = client.get("/athletes/42/recommendations", params=params).json()[0]
    # The fixture activity is 40 km with 300 m of climbing.
    assert r["distance_mi"] == 24.9
    assert r["elev_gain_ft"] == 984
    assert r["start_mi_away"] * 4 == int(r["start_mi_away"] * 4)  # whole quarter miles
    assert r["why"][0] == "24.9 mi vs. 25 mi target"
    assert r["why"][2] == "984 ft climbing"
    assert not {"distance_km", "elev_gain_m", "start_km_away"} & r.keys()
    # max_elev_ft really is feet: 900 ft (~274 m) is below the route's 300 m.
    params["max_elev_ft"] = 900
    assert "over your max" in client.get("/athletes/42/recommendations", params=params).json()[0]["why"][2]


def test_recommendations_max_start_is_miles(client):
    # The route's (snapped) start is about 0.5 mi from here: excluded at 0.2 mi, included at 1 mi.
    params = {"lat": 47.6555, "lng": -122.30, "distance_mi": 25}
    assert client.get("/athletes/42/recommendations", params={**params, "max_start_mi": 0.2}).json() == []
    assert client.get("/athletes/42/recommendations", params={**params, "max_start_mi": 1}).json()


def test_recommendations_custom_weights(client):
    base = {"lat": 47.66, "lng": -122.30, "distance_mi": 25}
    default = client.get("/athletes/42/recommendations", params=base).json()[0]["score"]
    novelty_only = client.get("/athletes/42/recommendations", params={
        **base, "w_distance": 0, "w_proximity": 0, "w_elevation": 0, "w_novelty": 100}).json()[0]["score"]
    assert novelty_only != default
    all_zero = {**base, "w_distance": 0, "w_proximity": 0, "w_elevation": 0, "w_novelty": 0}
    resp = client.get("/athletes/42/recommendations", params=all_zero)
    assert resp.status_code == 422 and "at least one" in resp.json()["detail"]
    assert client.get("/athletes/42/recommendations", params={**base, "w_novelty": -1}).status_code == 422
