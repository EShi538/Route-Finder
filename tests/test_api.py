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
                      params={"lat": 47.66, "lng": -122.30, "distance_km": 40})
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["name"] == "Lake loop"
    assert body[0]["strava_activity_url"].endswith("/activities/1")


def test_recommendations_unknown_athlete(client):
    resp = client.get("/athletes/7/recommendations",
                      params={"lat": 47.66, "lng": -122.30, "distance_km": 40})
    assert resp.status_code == 404


@pytest.mark.parametrize("params", [
    {"lat": 200, "lng": 0, "distance_km": 40},      # invalid latitude
    {"lat": 47, "lng": -122, "distance_km": -5},    # negative distance
    {"lat": 47, "lng": -122, "distance_km": 40, "sport": "swim"},  # unsupported sport
])
def test_recommendations_validates_input(client, params):
    assert client.get("/athletes/42/recommendations", params=params).status_code == 422


def test_polyline_hidden_by_default(client):
    params = {"lat": 47.66, "lng": -122.30, "distance_km": 40}
    assert "polyline" not in client.get("/athletes/42/recommendations", params=params).json()[0]
    params["include_polyline"] = "true"
    assert "polyline" in client.get("/athletes/42/recommendations", params=params).json()[0]


def test_home_page_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Route Finder" in resp.text


def test_recommendations_imperial_units(client):
    params = {"lat": 47.66, "lng": -122.30, "distance_km": 40, "units": "imperial"}
    body = client.get("/athletes/42/recommendations", params=params).json()
    assert body[0]["distance_km"] == 40.0  # numeric fields stay metric
    assert "mi" in body[0]["why"][0]
    params["units"] = "furlongs"
    assert client.get("/athletes/42/recommendations", params=params).status_code == 422
