"""Route candidate grouping and scoring. Pure functions, no I/O, so everything here is unit-testable."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.geo import decode, haversine_km, is_loop

SPORTS = {
    "ride": {"Ride", "GravelRide", "MountainBikeRide", "EBikeRide"},
    "run": {"Run", "TrailRun"},
    "walk": {"Walk", "Hike"},
}

WEIGHTS = {"distance": 0.40, "proximity": 0.25, "elevation": 0.15, "novelty": 0.20}

DISTANCE_TOLERANCE = 0.25   # a route 25% off the target distance scores 0 on distance fit
SAME_ROUTE_START_KM = 0.3   # two activities are "the same route" if they start this close...
SAME_ROUTE_DIST_PCT = 0.05  # ...and their distances differ by less than 5%
NOVELTY_FULL_DAYS = 42      # not done in 6+ weeks = full novelty bonus


@dataclass
class Candidate:
    activity_id: int
    name: str
    sport_type: str
    distance_km: float
    elev_gain_m: float
    start_lat: float
    start_lng: float
    polyline: str | None
    last_done: datetime
    times_done: int = 1


@dataclass
class Recommendation:
    candidate: Candidate
    score: float
    start_km_away: float
    loop: bool
    why: list[str] = field(default_factory=list)


def _parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def group_routes(rows) -> list[Candidate]:
    """Collapse repeated activities on the same route into one candidate (keeps the most recent)."""
    candidates: list[Candidate] = []
    for r in sorted(rows, key=lambda r: r["start_date"], reverse=True):
        dist_km = r["distance_m"] / 1000
        match = next(
            (c for c in candidates
             if c.sport_type == r["sport_type"]
             and haversine_km(c.start_lat, c.start_lng, r["start_lat"], r["start_lng"]) <= SAME_ROUTE_START_KM
             and abs(c.distance_km - dist_km) / max(c.distance_km, 1e-9) < SAME_ROUTE_DIST_PCT),
            None,
        )
        if match:
            match.times_done += 1
            continue
        candidates.append(Candidate(
            activity_id=r["id"], name=r["name"], sport_type=r["sport_type"],
            distance_km=dist_km, elev_gain_m=r["elev_gain_m"],
            start_lat=r["start_lat"], start_lng=r["start_lng"],
            polyline=r["polyline"], last_done=_parse_date(r["start_date"]),
        ))
    return candidates


def distance_fit(distance_km: float, target_km: float) -> float:
    off = abs(distance_km - target_km) / target_km
    return max(0.0, 1 - off / DISTANCE_TOLERANCE)


def proximity_fit(km_away: float, max_start_km: float) -> float:
    return max(0.0, 1 - km_away / max_start_km)


def elevation_fit(elev_m: float, max_elev_m: float | None) -> float:
    if max_elev_m is None or elev_m <= max_elev_m:
        return 1.0
    if max_elev_m <= 0:
        return 0.0
    return max(0.0, 1 - (elev_m - max_elev_m) / max_elev_m)


def novelty(last_done: datetime, now: datetime) -> float:
    days = (now - last_done).total_seconds() / 86400
    return min(1.0, max(0.0, days / NOVELTY_FULL_DAYS))


def recommend(
    candidates: list[Candidate],
    lat: float,
    lng: float,
    target_km: float,
    max_elev_m: float | None = None,
    max_start_km: float = 10.0,
    limit: int = 5,
    now: datetime | None = None,
) -> list[Recommendation]:
    now = now or datetime.now(timezone.utc)
    results = []
    for c in candidates:
        km_away = haversine_km(lat, lng, c.start_lat, c.start_lng)
        d, p = distance_fit(c.distance_km, target_km), proximity_fit(km_away, max_start_km)
        if d == 0 or p == 0:  # too long/short or too far away: not a candidate at all
            continue
        e, n = elevation_fit(c.elev_gain_m, max_elev_m), novelty(c.last_done, now)
        score = (WEIGHTS["distance"] * d + WEIGHTS["proximity"] * p
                 + WEIGHTS["elevation"] * e + WEIGHTS["novelty"] * n)

        why = [f"{c.distance_km:.1f} km vs. {target_km:g} km target",
               f"starts {km_away:.1f} km away"]
        if max_elev_m is not None:
            why.append(f"{c.elev_gain_m:.0f} m climbing" + (" (over your max)" if e < 1 else ""))
        days = int((now - c.last_done).days)
        why.append(f"last done {days} days ago" + (f", {c.times_done} times total" if c.times_done > 1 else ""))

        results.append(Recommendation(c, round(score, 3), round(km_away, 2), is_loop(decode(c.polyline)), why))

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:limit]
