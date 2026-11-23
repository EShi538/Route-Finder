# Strava Route Recommendation API

A REST service that connects to Strava, imports an athlete's activity history, and recommends routes that match a target distance, start location and climbing budget. Each recommendation includes the reasons it was picked.

```
Strava ──OAuth 2.0──▶ FastAPI service ──▶ SQLite
                          │
     GET /athletes/{id}/recommendations?lat=&lng=&distance_km=
```

## How it works
1. **Connect:** `/auth/login` sends the athlete to Strava. `/auth/callback` exchanges the code for tokens, and tokens refresh automatically before they expire.
2. **Import:** `POST /athletes/{id}/sync` backfills activities 200 per page and backs off near Strava's rate limit using the `X-RateLimit-*` headers.
3. **Group:** activities with the same sport, a start within 300 m and a distance within 5% are treated as one route.
4. **Score:** each candidate route gets a weighted score:

| Factor | Weight | Scored as |
|---|---|---|
| Distance fit | 0.40 | 1 at the target distance, 0 at 25% off |
| Start proximity | 0.25 | haversine distance from you, 0 at `max_start_km` |
| Elevation fit | 0.15 | 1 under `max_elev_m`, falling off above it |
| Novelty | 0.20 | rises to 1 when you haven't done the route in 6 weeks |

Routes that are too long, too short or too far away are filtered out before ranking.

## Endpoints
| Method | Path | Description |
|---|---|---|
| GET | `/auth/login` | Start the Strava OAuth flow |
| GET | `/auth/callback` | OAuth redirect target; stores tokens |
| POST | `/athletes/{id}/sync` | Import activities from Strava |
| GET | `/athletes/{id}/recommendations` | Ranked routes. Params: `lat`, `lng`, `distance_km`, `sport` (ride/run/walk), `max_elev_m`, `max_start_km`, `limit` |
| GET | `/health` | Health check |

Example:
```bash
curl "http://localhost:8000/athletes/12345/recommendations?lat=47.66&lng=-122.30&distance_km=40&max_elev_m=500"
```
```json
[{"name": "Lake loop", "distance_km": 41.2, "elev_gain_m": 310, "start_km_away": 0.8,
  "loop": true, "score": 0.912,
  "why": ["41.2 km vs. 40 km target", "starts 0.8 km away", "310 m climbing", "last done 44 days ago, 6 times total"]}]
```

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add your Strava client ID and secret
uvicorn app.main:app --reload
```
Open http://localhost:8000/auth/login, approve access, then run `curl -X POST localhost:8000/athletes/<your id>/sync`. Interactive docs are at http://localhost:8000/docs.

## Tests
```bash
pytest
```

## Docker
```bash
docker build -t strava-routes-api .
docker run -p 8000:8000 --env-file .env strava-routes-api
```
