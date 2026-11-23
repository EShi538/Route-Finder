# Strava Route Recommendation API

A REST service that connects to Strava, imports an athlete's activity history, and recommends routes that match a target distance, start location and climbing budget. Each recommendation includes the reasons it was picked.

```
Strava ──OAuth 2.0──▶ FastAPI service ──▶ SQLite
                          │
     GET /me/recommendations?lat=&lng=&distance_mi=
```

## How it works
1. **Connect:** `/auth/login` sends the athlete to Strava. `/auth/callback` checks the OAuth `state`, exchanges the code for tokens and signs the athlete in with a signed session cookie. Tokens refresh automatically before they expire.
2. **Import:** `POST /me/sync` (the **Sync now** button) backfills activities 200 per page and backs off near Strava's rate limit using the `X-RateLimit-*` headers.
3. **Group:** activities with the same sport, a start within 300 m and a distance within 5% are treated as one route.
4. **Score:** each candidate route gets a weighted score:

| Factor | Weight | Scored as |
|---|---|---|
| Distance fit | 0.40 | 1 at the target distance, 0 at 25% off |
| Start proximity | 0.25 | haversine distance from you, 0 at `max_start_mi` |
| Elevation fit | 0.15 | 1 under `max_elev_ft`, falling off above it |
| Novelty | 0.20 | rises to 1 when you haven't done the route in 6 weeks |

Routes that are too long, too short or too far away are filtered out before ranking.

The weights are defaults. Pass `w_distance`, `w_proximity`, `w_elevation` and `w_novelty` (0–100 each) to change how much each factor counts. Only their ratio matters: they are scaled to sum to 1, so `w_distance=60&w_novelty=60` makes those two equally important. Any you leave out keep their default (40/25/15/20), and setting them all to 0 is rejected. A weight of 0 only stops a factor from affecting the ranking: routes too far off the target distance or start are still filtered out. On the web page these are the sliders under **Priorities**.

Each route is also labelled `route_type`: `point_to_point` if it ends more than 0.5 km from its start; `out_and_back` if its second half covers the same ground as its first half in reverse; otherwise `loop` (including several laps of the same loop). It is `null` for activities with no GPS path.

5. **Privacy:** route start and end points are usually someone's home, so they never leave the server. Every returned `polyline` has 300–600 m cut off each end (the amount varies per activity but stays the same between requests), and routes too short to trim come back with no path. `start_mi_away` and the proximity score are measured from the first point that is still visible, and the distance is rounded to 0.25 mi. Activities with no GPS path are measured from a start snapped to a ~500 m grid.

## Endpoints
| Method | Path | Description |
|---|---|---|
| GET | `/auth/login` | Start the Strava OAuth flow |
| GET | `/auth/callback` | OAuth redirect target; stores tokens and signs you in |
| POST | `/auth/logout` | Sign out |
| GET | `/me` | Signed-in athlete and how many activities are imported |
| POST | `/me/sync` | Import activities from Strava |
| GET | `/me/recommendations` | Ranked routes. Params: `lat`, `lng`, `distance_mi`, `sport` (ride/run/walk), `max_elev_ft` (max 50,000), `max_start_mi` (default 6, max 60), `limit`, `w_distance`/`w_proximity`/`w_elevation`/`w_novelty` (relative weights, 0–100) |
| GET/POST | `/webhooks/strava` | Strava webhook: verification handshake and activity events (see below) |
| GET | `/health` | Health check |

Everything under `/me` needs you to be signed in (otherwise `401`) and only ever returns or changes the signed-in athlete's own data. The session cookie is signed with `SESSION_SECRET`, `HttpOnly`, `SameSite=Lax` (so other sites can't trigger a sync as you), and `Secure` when `REDIRECT_URI` is HTTPS.

Example (from the browser once signed in, or with the session cookie):
```bash
curl -b "route_finder_session=…" "http://localhost:8000/me/recommendations?lat=47.66&lng=-122.30&distance_mi=25&max_elev_ft=1500"
```
```json
[{"name": "Lake loop", "distance_mi": 25.6, "elev_gain_ft": 1017, "start_mi_away": 0.5,
  "route_type": "loop", "score": 0.912,
  "why": ["25.6 mi vs. 25 mi target", "starts about 0.5 mi away", "1,017 ft climbing", "last done 44 days ago, 6 times total"]}]
```

All API inputs and outputs are in miles and feet. Strava's data and the scoring code use km and m internally, and `app/main.py` converts between them.

## Keeping activities up to date (Strava webhook)
`POST /me/sync` imports your whole history. After that, a webhook keeps it current: Strava notifies the app whenever you upload, edit or delete an activity, or revoke access, and the app updates just that activity.

Strava doesn't sign webhook events, so the app never trusts what an event says. It re-fetches the activity from Strava and saves it if it exists, or deletes the local copy only if Strava returns 404. On a "revoked access" event, it deletes the athlete's data only if Strava really rejects their tokens. A forged event can't add or remove data.

Strava needs a public HTTPS URL, so set this up once the app is deployed. To try it locally, a tunnel works (e.g. `cloudflared tunnel --url http://localhost:8000`).

1. Put a long random string in `.env` as `STRAVA_VERIFY_TOKEN` (`python -c "import secrets; print(secrets.token_urlsafe(32))"`) and restart the app.
2. With the app running at that URL, create the subscription. Strava calls `GET /webhooks/strava` to check the token before it agrees:
   ```bash
   python -m app.webhook_admin subscribe https://your-host/webhooks/strava
   ```
3. Put the `id` it prints in `.env` as `STRAVA_SUBSCRIPTION_ID` and restart. Events for any other subscription are then ignored.

`python -m app.webhook_admin view` shows the current subscription, and `unsubscribe <id>` removes it. Strava allows one subscription per API app, so unsubscribe before switching to a new URL (e.g. from a tunnel to your deployed host). Webhook activity shows up in the uvicorn log as `webhook activity create 123: stored`.

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add your Strava client ID and secret, and a SESSION_SECRET
uvicorn app.main:app --reload
```
Open http://localhost:8000, click **Connect with Strava** and approve access. Your activities import automatically on first sign-in; **Sync now** fetches everything again. Interactive docs are at http://localhost:8000/docs.

`SESSION_SECRET` signs the login cookie. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Without it the app picks a random key at startup, so everyone is signed out whenever it restarts.

## Tests
```bash
pytest
```

## Docker
```bash
docker build -t strava-routes-api .
docker run -p 8000:8000 --env-file .env strava-routes-api
```
