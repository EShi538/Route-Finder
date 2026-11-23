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

The weights are defaults. Pass `w_distance`, `w_proximity`, `w_elevation` and `w_novelty` (0–100 each) to change how much each factor counts. Only their ratio matters: they are scaled to sum to 1, so `w_distance=60&w_novelty=60` makes those two equally important. Any you leave out keep their default (40/25/15/20), and setting them all to 0 is rejected. A weight of 0 only stops a factor from affecting the ranking: routes too far off the target distance or start are still filtered out. On the web page these are the sliders under **Priorities**.

5. **Privacy:** route start and end points are usually someone's home, so they never leave the server. Every returned `polyline` has 300–600 m cut off each end (the amount varies per activity but stays the same between requests), and routes too short to trim come back with no path. `start_km_away` and the proximity score are measured from the first point that is still visible, and the distance is rounded to 0.5 km (0.25 mi with `units=imperial`). Activities with no GPS path are measured from a start snapped to a ~500 m grid.

## Endpoints
| Method | Path | Description |
|---|---|---|
| GET | `/auth/login` | Start the Strava OAuth flow |
| GET | `/auth/callback` | OAuth redirect target; stores tokens |
| POST | `/athletes/{id}/sync` | Import activities from Strava |
| GET | `/athletes/{id}/recommendations` | Ranked routes. Params: `lat`, `lng`, `distance_km`, `sport` (ride/run/walk), `max_elev_m`, `max_start_km`, `limit`, `w_distance`/`w_proximity`/`w_elevation`/`w_novelty` (relative weights, 0–100), `units` (metric/imperial: wording of `why` only; numeric fields are always km and m) |
| GET | `/health` | Health check |

Example:
```bash
curl "http://localhost:8000/athletes/12345/recommendations?lat=47.66&lng=-122.30&distance_km=40&max_elev_m=500"
```
```json
[{"name": "Lake loop", "distance_km": 41.2, "elev_gain_m": 310, "start_km_away": 1.0,
  "loop": true, "score": 0.912,
  "why": ["41.2 km vs. 40 km target", "starts about 1.0 km away", "310 m climbing", "last done 44 days ago, 6 times total"]}]
```

The web page at `/` works in miles and feet. It converts to km and m when calling the API.

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
