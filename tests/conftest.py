from urllib.parse import parse_qs, urlparse

from app import strava


def sign_in(client, monkeypatch, athlete_id: int = 42) -> None:
    """Go through the real /auth/login -> /auth/callback flow, faking only Strava's token exchange."""
    monkeypatch.setattr(strava, "exchange_code", lambda code: {
        "athlete": {"id": athlete_id, "firstname": "Test", "lastname": "Athlete"},
        "access_token": "tok", "refresh_token": "refresh", "expires_at": 9_999_999_999,
    })
    to_strava = client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(to_strava.headers["location"]).query)["state"][0]
    back = client.get("/auth/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert back.status_code == 303, back.text
