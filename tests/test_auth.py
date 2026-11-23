"""Sign-in: only the signed-in athlete can read or sync their data, and logins can't be forged."""

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app import config, db, main, strava
from tests.conftest import sign_in

SEARCH = {"lat": 47.66, "lng": -122.30, "distance_mi": 25}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(main, "_route_cache", {})
    monkeypatch.setattr(strava, "sync_activities", lambda conn, athlete_id: 0)
    with TestClient(main.app) as c:
        yield c


@pytest.mark.parametrize("method, path, params", [
    ("get", "/me", None),
    ("get", "/me/recommendations", SEARCH),
    ("post", "/me/sync", None),
])
def test_athlete_endpoints_need_sign_in(client, method, path, params):
    assert getattr(client, method)(path, params=params).status_code == 401


def test_old_athlete_id_urls_are_gone(client):
    assert client.get("/athletes/42/recommendations", params=SEARCH).status_code == 404
    assert client.post("/athletes/42/sync").status_code == 404


def test_sign_in_flow(client, monkeypatch):
    sign_in(client, monkeypatch, athlete_id=42)
    assert client.get("/me").json() == {"athlete_id": 42, "name": "Test Athlete", "activities": 0}
    assert client.get("/me/recommendations", params=SEARCH).status_code == 200
    assert client.post("/me/sync").json() == {"activities_stored": 0}


def test_login_sends_random_state_to_strava(client):
    states = {parse_qs(urlparse(client.get("/auth/login", follow_redirects=False).headers["location"]).query)["state"][0]
              for _ in range(3)}
    assert len(states) == 3


@pytest.mark.parametrize("state", ["forged", ""])
def test_callback_rejects_wrong_or_missing_state(client, monkeypatch, state):
    called = []
    monkeypatch.setattr(strava, "exchange_code", lambda code: called.append(code))
    client.get("/auth/login", follow_redirects=False)
    resp = client.get("/auth/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert resp.status_code == 400 and called == []   # never even talks to Strava
    assert client.get("/me").status_code == 401


def test_callback_without_starting_here_is_rejected(client, monkeypatch):
    # Another site sending your browser straight to the callback with its own code + state.
    monkeypatch.setattr(strava, "exchange_code", lambda code: pytest.fail("should not exchange"))
    resp = client.get("/auth/callback", params={"code": "attacker", "state": "x"}, follow_redirects=False)
    assert resp.status_code == 400


def test_state_can_only_be_used_once(client, monkeypatch):
    sign_in(client, monkeypatch)
    client.post("/auth/logout")
    # Replaying an old callback URL must not sign you back in.
    resp = client.get("/auth/callback", params={"code": "c", "state": "anything"}, follow_redirects=False)
    assert resp.status_code == 400


def test_logout(client, monkeypatch):
    sign_in(client, monkeypatch)
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/me").status_code == 401


def test_session_ends_when_athlete_is_deleted(client, monkeypatch):
    sign_in(client, monkeypatch, athlete_id=42)
    conn = db.connect()
    db.delete_athlete(conn, 42)   # what the deauthorization webhook does
    conn.close()
    assert client.get("/me").status_code == 401


def test_tampered_cookie_is_rejected(client, monkeypatch):
    sign_in(client, monkeypatch, athlete_id=42)
    cookie = client.cookies.get("route_finder_session")
    client.cookies.set("route_finder_session", cookie[:-4] + ("AAAA" if not cookie.endswith("AAAA") else "BBBB"))
    assert client.get("/me").status_code == 401


def test_cookie_is_httponly_and_samesite_lax(client, monkeypatch):
    client.get("/auth/login", follow_redirects=False)
    header = client.get("/auth/login", follow_redirects=False).headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header
