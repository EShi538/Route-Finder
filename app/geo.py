import math

import polyline as polyline_codec

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points, in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def decode(encoded: str | None) -> list[tuple[float, float]]:
    """Decode a Google-encoded polyline (Strava's map.summary_polyline) into (lat, lng) points."""
    if not encoded:
        return []
    return polyline_codec.decode(encoded)


def is_loop(points: list[tuple[float, float]], tolerance_km: float = 0.5) -> bool:
    """True if the route ends within tolerance_km of where it started."""
    if len(points) < 2:
        return False
    (lat1, lng1), (lat2, lng2) = points[0], points[-1]
    return haversine_km(lat1, lng1, lat2, lng2) <= tolerance_km
