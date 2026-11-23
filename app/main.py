from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import db, scoring, strava
from app.scoring import KM_PER_MI, M_PER_FT


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    db.init_db(conn)
    conn.close()
    yield


app = FastAPI(title="Strava Route Recommendation API", lifespan=lifespan)


STATIC_DIR = Path(__file__).parent / "static"

# Grouping activities into routes compares GPS shapes and takes ~0.5-1 s for ~1000
# activities, but the result only changes when activities are added. Cache it, keyed
# on what would change it: the athlete, sport, and the set of activities (count + newest id).
_route_cache: dict[tuple, list] = {}


def routes_for(athlete_id: int, sport: str, rows) -> list:
    key = (athlete_id, sport, len(rows), max((r["id"] for r in rows), default=0))
    if key not in _route_cache:
        _route_cache[key] = scoring.group_routes(rows)
    return _route_cache[key]
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def home():
    """The web page: a form, a map, and result cards that call the recommendations endpoint."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/auth/login")
def login():
    """Send the user to Strava to approve access."""
    return RedirectResponse(strava.authorize_url())


@app.get("/auth/callback")
def callback(code: str | None = None, error: str | None = None):
    """Strava redirects here after approval; trade the one-time code for tokens and store them."""
    if error or not code:
        raise HTTPException(400, f"Strava authorization failed: {error or 'no code'}")
    t = strava.exchange_code(code)
    a = t["athlete"]
    conn = db.connect()
    db.save_athlete(conn, a["id"], f"{a.get('firstname', '')} {a.get('lastname', '')}".strip(),
                    t["access_token"], t["refresh_token"], t["expires_at"])
    conn.close()
    return {"athlete_id": a["id"], "next": f"POST /athletes/{a['id']}/sync to import your activities"}


@app.post("/athletes/{athlete_id}/sync")
def sync(athlete_id: int):
    conn = db.connect()
    try:
        stored = strava.sync_activities(conn, athlete_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"Strava API error: {e.response.status_code}")
    except httpx.TimeoutException:
        raise HTTPException(504, "Strava took too long to respond; try again")
    finally:
        conn.close()
    return {"activities_stored": stored}


@app.get("/athletes/{athlete_id}/recommendations")
def recommendations(
    athlete_id: int,
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    distance_mi: float = Query(..., gt=0, le=300, description="Target distance in miles"),
    sport: Literal["ride", "run", "walk"] = "ride",
    max_elev_ft: float | None = Query(None, ge=0, le=50_000, description="Most climbing you want, in feet"),
    max_start_mi: float = Query(6.0, gt=0, le=60, description="Furthest a route may start from you, in miles"),
    limit: int = Query(5, ge=1, le=20),
    include_polyline: bool = False,
    w_distance: float | None = Query(None, ge=0, le=100, description="Importance of matching the target distance"),
    w_proximity: float | None = Query(None, ge=0, le=100, description="Importance of starting close by"),
    w_elevation: float | None = Query(None, ge=0, le=100, description="Importance of staying under max_elev_ft"),
    w_novelty: float | None = Query(None, ge=0, le=100, description="Importance of routes not done lately"),
):
    """The API speaks miles and feet. Strava data and the scoring maths are metric,
    so values are converted here on the way in and on the way out."""
    conn = db.connect()
    if db.get_athlete(conn, athlete_id) is None:
        conn.close()
        raise HTTPException(404, f"athlete {athlete_id} has not connected Strava")
    rows = db.activities_for(conn, athlete_id, scoring.SPORTS[sport])
    conn.close()

    weights = {"distance": w_distance, "proximity": w_proximity,
               "elevation": w_elevation, "novelty": w_novelty}
    try:
        scoring.normalize_weights(weights)
    except ValueError as e:
        raise HTTPException(422, str(e))

    recs = scoring.recommend(
        routes_for(athlete_id, sport, rows), lat, lng,
        target_km=distance_mi * KM_PER_MI,
        max_elev_m=None if max_elev_ft is None else max_elev_ft * M_PER_FT,
        max_start_km=max_start_mi * KM_PER_MI,
        limit=limit, units="imperial", weights=weights,
    )
    return [
        {
            "name": r.candidate.name,
            "strava_activity_url": f"https://www.strava.com/activities/{r.candidate.activity_id}",
            "sport_type": r.candidate.sport_type,
            "distance_mi": round(r.candidate.distance_km / KM_PER_MI, 1),
            "elev_gain_ft": round(r.candidate.elev_gain_m / M_PER_FT),
            "start_mi_away": round(r.start_km_away / KM_PER_MI, 2),  # already rounded to 0.25 mi
            "route_type": r.route_type,  # loop, out_and_back, point_to_point, or null without GPS
            "score": r.score,
            "why": r.why,
            # Only ever the trimmed path: the real start and end never leave the server.
            **({"polyline": r.candidate.public_polyline} if include_polyline else {}),
        }
        for r in recs
    ]
