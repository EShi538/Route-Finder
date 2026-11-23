"""Route candidate grouping and scoring. Pure functions, no I/O, so everything here is unit-testable."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.geo import Shape, decode, encode, haversine_km, route_type, same_shape, snap, trim_ends

SPORTS = {
    "ride": {"Ride", "GravelRide", "MountainBikeRide", "EBikeRide"},
    "run": {"Run", "TrailRun"},
    "walk": {"Walk", "Hike"},
}

# Default importance of each factor. Callers can override any of them; weights are
# relative (only their ratio matters) and are normalised to sum to 1 before scoring.
WEIGHTS = {"distance": 40, "proximity": 25, "elevation": 15, "novelty": 20}


def normalize_weights(overrides: dict[str, float | None] | None = None) -> dict[str, float]:
    """Merge overrides into the defaults and scale so the weights sum to 1."""
    w = {**WEIGHTS, **{k: v for k, v in (overrides or {}).items() if v is not None}}
    if set(w) != set(WEIGHTS):
        raise ValueError(f"unknown factor(s): {', '.join(sorted(set(w) - set(WEIGHTS)))}")
    if any(v < 0 for v in w.values()):
        raise ValueError("weights can't be negative")
    total = sum(w.values())
    if total <= 0:
        raise ValueError("at least one factor needs a weight above 0")
    return {k: v / total for k, v in w.items()}

DISTANCE_TOLERANCE = 0.25   # a route 25% off the target distance scores 0 on distance fit
SHAPE_DIST_PCT = 0.25       # only shape-compare activities within 25% of each other's distance
SAME_ROUTE_START_KM = 0.3   # fallback when an activity has no GPS path: same start...
SAME_ROUTE_DIST_PCT = 0.05  # ...and distance within 5%
NOVELTY_FULL_DAYS = 42      # not done in 6+ weeks = full novelty bonus
DISTANCE_ROUND_KM = 0.5     # reported start distances are rounded to this

KM_PER_MI = 1.609344
M_PER_FT = 0.3048


class Units:
    """Formats distances and climbing for the human-readable "why" text.
    Calculations always use km and m; only the wording changes."""

    def __init__(self, system: str = "metric"):
        self.imperial = system == "imperial"
        # Start distances are coarsened in the unit shown, so imperial reads 0.25 / 0.5 / 0.75 mi.
        self.round_km = 0.25 * KM_PER_MI if self.imperial else DISTANCE_ROUND_KM

    def dist(self, km: float) -> str:
        # Rounded the same way as the API's numeric fields, so the text matches the cards.
        return f"{km / KM_PER_MI:.1f} mi" if self.imperial else f"{km:.1f} km"

    def away(self, km: float) -> str:
        value, unit = (km / KM_PER_MI, "mi") if self.imperial else (km, "km")
        return f"{round(value, 2):g} {unit}"

    def target(self, km: float) -> str:
        # The user typed this number, so show it back without padding (40 km, 25 mi).
        value, unit = (km / KM_PER_MI, "mi") if self.imperial else (km, "km")
        return f"{round(value, 1):g} {unit}"

    def elev(self, m: float) -> str:
        return f"{m / M_PER_FT:,.0f} ft" if self.imperial else f"{m:,.0f} m"


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
    shape: Shape | None = field(default=None, repr=False, compare=False)
    route_type: str | None = None  # loop / out_and_back / point_to_point; None without GPS
    # What may leave the server: the path with both ends trimmed off, and the
    # point we measure "km away" from (first visible point, or a snapped start).
    public_polyline: str | None = None
    public_start: tuple[float, float] | None = None

    def __post_init__(self):
        points = decode(self.polyline)
        self.route_type = route_type(points)  # from the full path, before trimming
        visible = trim_ends(points, self.activity_id)
        self.public_polyline = encode(visible)
        self.public_start = visible[0] if visible else snap(self.start_lat, self.start_lng)


@dataclass
class Recommendation:
    candidate: Candidate
    score: float
    start_km_away: float
    route_type: str | None
    why: list[str] = field(default_factory=list)


def _parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _same_route(c: Candidate, row, dist_km: float, shape: Shape | None) -> bool:
    if c.sport_type != row["sport_type"]:
        return False
    dist_diff = abs(c.distance_km - dist_km) / max(c.distance_km, 1e-9)
    if c.shape and shape and c.shape.cells and shape.cells:
        # Cheap distance check first, then the real test: do the GPS paths overlap?
        return dist_diff < SHAPE_DIST_PCT and same_shape(c.shape, shape)
    # No GPS path on one side: fall back to "same start point and same distance".
    return (dist_diff < SAME_ROUTE_DIST_PCT
            and haversine_km(c.start_lat, c.start_lng, row["start_lat"], row["start_lng"]) <= SAME_ROUTE_START_KM)


def group_routes(rows) -> list[Candidate]:
    """Collapse activities that followed the same route into one candidate.

    Activities are processed newest first, so each group is represented by its
    most recent activity (name, link and path) and counts how many times it was done.
    """
    rows = sorted(rows, key=lambda r: r["start_date"], reverse=True)
    if not rows:
        return []
    ref_lat = rows[0]["start_lat"]  # one projection for the whole batch so shapes are comparable

    candidates: list[Candidate] = []
    for r in rows:
        dist_km = r["distance_m"] / 1000
        points = decode(r["polyline"])
        shape = Shape(points, ref_lat) if points else None
        match = next((c for c in candidates if _same_route(c, r, dist_km, shape)), None)
        if match:
            match.times_done += 1
            continue
        candidates.append(Candidate(
            activity_id=r["id"], name=r["name"], sport_type=r["sport_type"],
            distance_km=dist_km, elev_gain_m=r["elev_gain_m"],
            start_lat=r["start_lat"], start_lng=r["start_lng"],
            polyline=r["polyline"], last_done=_parse_date(r["start_date"]),
            shape=shape,
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
    units: str = "metric",
    weights: dict[str, float | None] | None = None,
) -> list[Recommendation]:
    """Rank candidates. Distance and start proximity also act as filters: a route too
    far off the target distance or too far away is dropped even if its weight is 0."""
    now = now or datetime.now(timezone.utc)
    w = normalize_weights(weights)
    u = Units(units)
    results = []
    for c in candidates:
        # Measured from the public start, never the real one, so the score and the
        # reported distance can't be used to triangulate where the athlete lives.
        km_away = haversine_km(lat, lng, *c.public_start)
        d, p = distance_fit(c.distance_km, target_km), proximity_fit(km_away, max_start_km)
        if d == 0 or p == 0:  # too long/short or too far away: not a candidate at all
            continue
        e, n = elevation_fit(c.elev_gain_m, max_elev_m), novelty(c.last_done, now)
        score = (w["distance"] * d + w["proximity"] * p
                 + w["elevation"] * e + w["novelty"] * n)

        shown_km = round(km_away / u.round_km) * u.round_km
        why = [f"{u.dist(c.distance_km)} vs. {u.target(target_km)} target",
               f"starts about {u.away(shown_km)} away"]
        if max_elev_m is not None:
            why.append(f"{u.elev(c.elev_gain_m)} climbing" + (" (over your max)" if e < 1 else ""))
        days = int((now - c.last_done).days)
        why.append(f"last done {days} days ago" + (f", {c.times_done} times total" if c.times_done > 1 else ""))

        results.append(Recommendation(c, round(score, 3), round(shown_km, 3), c.route_type, why))

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:limit]
