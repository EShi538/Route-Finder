"""Thin Strava API client: OAuth token exchange/refresh and activity sync."""

import time
from urllib.parse import urlencode

import httpx

from app import config, db

SCOPES = "read,activity:read_all"


def authorize_url() -> str:
    return config.STRAVA_AUTH_URL + "?" + urlencode({
        "client_id": config.STRAVA_CLIENT_ID,
        "redirect_uri": config.REDIRECT_URI,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": SCOPES,
    })


def exchange_code(code: str) -> dict:
    resp = httpx.post(config.STRAVA_TOKEN_URL, data={
        "client_id": config.STRAVA_CLIENT_ID,
        "client_secret": config.STRAVA_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    })
    resp.raise_for_status()
    return resp.json()


def valid_access_token(conn, athlete_id: int) -> str:
    """Return a usable access token, refreshing it first if it expires within a minute."""
    athlete = db.get_athlete(conn, athlete_id)
    if athlete is None:
        raise LookupError(f"athlete {athlete_id} has not connected Strava")
    if athlete["expires_at"] > time.time() + 60:
        return athlete["access_token"]

    resp = httpx.post(config.STRAVA_TOKEN_URL, data={
        "client_id": config.STRAVA_CLIENT_ID,
        "client_secret": config.STRAVA_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": athlete["refresh_token"],
    })
    resp.raise_for_status()
    t = resp.json()
    db.save_athlete(conn, athlete_id, athlete["name"], t["access_token"], t["refresh_token"], t["expires_at"])
    return t["access_token"]


def to_row(athlete_id: int, a: dict) -> dict | None:
    """Map a Strava SummaryActivity to our activities row. Skips activities with no GPS start (e.g. trainer rides)."""
    start = a.get("start_latlng") or []
    if len(start) != 2:
        return None
    return {
        "id": a["id"],
        "athlete_id": athlete_id,
        "name": a.get("name", ""),
        "sport_type": a.get("sport_type") or a.get("type", ""),
        "distance_m": a.get("distance", 0.0),
        "elev_gain_m": a.get("total_elevation_gain", 0.0),
        "start_lat": start[0],
        "start_lng": start[1],
        "polyline": (a.get("map") or {}).get("summary_polyline"),
        "start_date": a["start_date"],
    }


def sync_activities(conn, athlete_id: int, max_pages: int = 10) -> int:
    """Backfill the athlete's activities, 200 per page. Returns how many were stored."""
    token = valid_access_token(conn, athlete_id)
    stored = 0
    with httpx.Client(base_url=config.STRAVA_API, headers={"Authorization": f"Bearer {token}"},
                      timeout=30.0) as client:
        for page in range(1, max_pages + 1):
            resp = client.get("/athlete/activities", params={"per_page": 200, "page": page})
            resp.raise_for_status()
            batch = resp.json()
            for a in batch:
                row = to_row(athlete_id, a)
                if row:
                    db.upsert_activity(conn, row)
                    stored += 1
            conn.commit()
            if len(batch) < 200:
                break
            # Strava reports usage as "15min,daily" in this header; back off near the short-term limit.
            usage, limit = resp.headers.get("X-RateLimit-Usage"), resp.headers.get("X-RateLimit-Limit")
            if usage and limit and int(usage.split(",")[0]) >= int(limit.split(",")[0]) - 5:
                break
    return stored


def fetch_activity(conn, athlete_id: int, activity_id: int) -> dict | None:
    """One activity as Strava has it now, or None if it no longer exists (deleted)."""
    token = valid_access_token(conn, athlete_id)
    resp = httpx.get(f"{config.STRAVA_API}/activities/{activity_id}",
                     headers={"Authorization": f"Bearer {token}"}, timeout=10.0)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def still_authorized(conn, athlete_id: int) -> bool:
    """False if Strava rejects this athlete's tokens, i.e. they revoked access."""
    try:
        token = valid_access_token(conn, athlete_id)
        resp = httpx.get(f"{config.STRAVA_API}/athlete",
                         headers={"Authorization": f"Bearer {token}"}, timeout=10.0)
    except httpx.HTTPStatusError as e:  # the token refresh itself was refused
        return e.response.status_code not in (400, 401)
    return resp.status_code != 401
