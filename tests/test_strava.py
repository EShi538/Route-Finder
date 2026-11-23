import httpx
import pytest

from app import config, db, strava
from app.strava import to_row


def test_to_row_maps_summary_activity():
    a = {"id": 5, "name": "Morning Ride", "sport_type": "Ride", "distance": 41200.0,
         "total_elevation_gain": 310.0, "start_latlng": [47.66, -122.30],
         "map": {"summary_polyline": "abc"}, "start_date": "2026-11-20T15:00:00Z"}
    r = to_row(42, a)
    assert r["distance_m"] == 41200.0 and r["start_lat"] == 47.66 and r["polyline"] == "abc"


def test_to_row_skips_activities_without_gps():
    # indoor trainer rides come back with an empty start_latlng
    assert to_row(42, {"id": 6, "sport_type": "VirtualRide", "start_latlng": [],
                       "start_date": "2026-11-20T15:00:00Z"}) is None


# --- webhook helpers: real request logic, fake HTTP responses ------------------



@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    c = db.connect()
    db.init_db(c)
    db.save_athlete(c, 42, "Test", "tok", "refresh", 9_999_999_999)  # token not expired: no refresh
    yield c
    c.close()


def fake_get(status, body=None):
    return lambda url, **kw: httpx.Response(status, json=body or {}, request=httpx.Request("GET", url))


def test_fetch_activity_returns_none_when_deleted(conn, monkeypatch):
    monkeypatch.setattr(httpx, "get", fake_get(404))
    assert strava.fetch_activity(conn, 42, 1) is None
    monkeypatch.setattr(httpx, "get", fake_get(200, {"id": 1}))
    assert strava.fetch_activity(conn, 42, 1) == {"id": 1}
    monkeypatch.setattr(httpx, "get", fake_get(500))
    with pytest.raises(httpx.HTTPStatusError):  # an outage must not look like a deletion
        strava.fetch_activity(conn, 42, 1)


@pytest.mark.parametrize("status, expected", [(200, True), (401, False), (429, True), (500, True)])
def test_still_authorized_only_false_on_401(conn, monkeypatch, status, expected):
    monkeypatch.setattr(httpx, "get", fake_get(status))
    assert strava.still_authorized(conn, 42) is expected


def test_still_authorized_false_when_refresh_is_refused(conn, monkeypatch):
    db.save_athlete(conn, 42, "Test", "tok", "refresh", 0)  # expired: forces a refresh
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(400, json={}, request=httpx.Request("POST", url)))
    assert strava.still_authorized(conn, 42) is False
