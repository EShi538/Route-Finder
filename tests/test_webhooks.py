"""Strava webhook: verification handshake and event handling.

Strava is never called: fetch_activity / still_authorized are replaced with fakes that
return what Strava "currently" has, so each test states the truth on Strava's side and
checks that our database ends up matching it.
"""

import pytest
from fastapi.testclient import TestClient

from app import config, db, main, strava
from tests.conftest import sign_in

ATHLETE = 42
STRAVA_NOW: dict[int, dict | None] = {}   # activity id -> what Strava returns (None = 404)


def activity(id, name="Morning Ride", lat=47.66, gps=True):
    return {"id": id, "athlete": {"id": ATHLETE}, "name": name, "sport_type": "Ride",
            "distance": 40_000.0, "total_elevation_gain": 300.0,
            "start_latlng": [lat, -122.30] if gps else [], "map": {"summary_polyline": None},
            "start_date": "2026-11-01T15:00:00Z"}


def event(object_id, aspect="create", kind="activity", owner=ATHLETE, sub=999, **extra):
    return {"object_type": kind, "object_id": object_id, "aspect_type": aspect,
            "owner_id": owner, "subscription_id": sub, "event_time": 1_790_000_000, **extra}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(config, "STRAVA_VERIFY_TOKEN", "s3cret-token")
    monkeypatch.setattr(config, "STRAVA_SUBSCRIPTION_ID", "999")
    monkeypatch.setattr(main, "_route_cache", {})
    STRAVA_NOW.clear()
    monkeypatch.setattr(strava, "fetch_activity", lambda conn, athlete, aid: STRAVA_NOW.get(aid))
    monkeypatch.setattr(strava, "still_authorized", lambda conn, athlete: False)
    with TestClient(main.app) as c:
        conn = db.connect()
        db.save_athlete(conn, ATHLETE, "Test", "tok", "refresh", 9_999_999_999)
        db.upsert_activity(conn, strava.to_row(ATHLETE, activity(1, "Old name")))
        conn.commit()
        conn.close()
        yield c


def stored(activity_id):
    conn = db.connect()
    row = conn.execute("SELECT * FROM activities WHERE id = ?", (activity_id,)).fetchone()
    conn.close()
    return row


# --- verification handshake --------------------------------------------------

def test_verification_echoes_challenge_with_right_token(client):
    resp = client.get("/webhooks/strava", params={
        "hub.mode": "subscribe", "hub.verify_token": "s3cret-token", "hub.challenge": "abc123"})
    assert resp.status_code == 200 and resp.json() == {"hub.challenge": "abc123"}


@pytest.mark.parametrize("params", [
    {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "x"},
    {"hub.mode": "unsubscribe", "hub.verify_token": "s3cret-token", "hub.challenge": "x"},
    {},
])
def test_verification_rejects_bad_requests(client, params):
    assert client.get("/webhooks/strava", params=params).status_code == 403


def test_verification_refused_when_no_token_configured(client, monkeypatch):
    monkeypatch.setattr(config, "STRAVA_VERIFY_TOKEN", "")
    resp = client.get("/webhooks/strava", params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "x"})
    assert resp.status_code == 403


# --- activity events ---------------------------------------------------------

def test_new_activity_is_stored(client):
    STRAVA_NOW[2] = activity(2, "Brand new ride")
    assert client.post("/webhooks/strava", json=event(2)).status_code == 200
    assert stored(2)["name"] == "Brand new ride"


def test_update_overwrites_every_field(client):
    STRAVA_NOW[1] = activity(1, "Renamed", lat=47.70)   # renamed and cropped
    client.post("/webhooks/strava", json=event(1, "update", updates={"title": "Renamed"}))
    row = stored(1)
    assert row["name"] == "Renamed" and row["start_lat"] == 47.70


def test_delete_removes_activity_only_when_strava_confirms(client):
    STRAVA_NOW[1] = activity(1)                       # forged delete: still exists on Strava
    client.post("/webhooks/strava", json=event(1, "delete"))
    assert stored(1) is not None
    STRAVA_NOW[1] = None                              # really deleted: Strava 404s
    client.post("/webhooks/strava", json=event(1, "delete"))
    assert stored(1) is None


def test_activity_edited_to_have_no_gps_is_removed(client):
    STRAVA_NOW[1] = activity(1, gps=False)
    client.post("/webhooks/strava", json=event(1, "update"))
    assert stored(1) is None


def test_events_for_unknown_athletes_or_other_subscriptions_are_ignored(client):
    STRAVA_NOW[3] = activity(3)
    client.post("/webhooks/strava", json=event(3, owner=7))
    client.post("/webhooks/strava", json=event(3, sub=123))
    assert stored(3) is None


def test_activity_owned_by_someone_else_is_ignored(client):
    STRAVA_NOW[4] = {**activity(4), "athlete": {"id": 7}}
    client.post("/webhooks/strava", json=event(4))
    assert stored(4) is None


def test_update_refreshes_cached_routes(client, monkeypatch):
    sign_in(client, monkeypatch, ATHLETE)
    params = {"lat": 47.66, "lng": -122.30, "distance_mi": 25}
    assert client.get("/me/recommendations", params=params).json()[0]["name"] == "Old name"
    STRAVA_NOW[1] = activity(1, "Renamed")
    client.post("/webhooks/strava", json=event(1, "update"))
    assert client.get("/me/recommendations", params=params).json()[0]["name"] == "Renamed"


def test_failing_strava_call_still_acknowledges(client, monkeypatch):
    def boom(*_):
        raise RuntimeError("Strava is down")
    monkeypatch.setattr(strava, "fetch_activity", boom)
    assert client.post("/webhooks/strava", json=event(1, "update")).status_code == 200
    assert stored(1)["name"] == "Old name"


def test_non_json_body_is_rejected(client):
    assert client.post("/webhooks/strava", content=b"not json").status_code == 400
    assert client.post("/webhooks/strava", json=[1, 2]).status_code == 400


# --- deauthorization ---------------------------------------------------------

def deauth():
    return event(ATHLETE, "update", kind="athlete", updates={"authorized": "false"})


def test_deauthorization_deletes_athlete_and_activities(client):
    client.post("/webhooks/strava", json=deauth())
    conn = db.connect()
    assert db.get_athlete(conn, ATHLETE) is None
    assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 0
    conn.close()


def test_forged_deauthorization_is_ignored(client, monkeypatch):
    monkeypatch.setattr(strava, "still_authorized", lambda conn, athlete: True)
    client.post("/webhooks/strava", json=deauth())
    assert stored(1) is not None
