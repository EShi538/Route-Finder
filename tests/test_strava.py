from app.strava import to_row


def test_to_row_maps_summary_activity():
    a = {"id": 5, "name": "Morning Ride", "sport_type": "Ride", "distance": 41200.0,
         "total_elevation_gain": 310.0, "start_latlng": [47.66, -122.30],
         "map": {"summary_polyline": "abc"}, "start_date": "2026-11-20T15:00:00Z"}
    r = to_row(42, a)
    assert r["distance_m"] == 41200.0 and r["start_lat"] == 47.66 and r["polyline"] == "abc"


def test_to_row_skips_activities_without_gps():
    # indoor trainer rides come back with an empty start_latlng
    assert to_row(42, {"id": 6, "sport_type": "VirtualRide", "start_latlng": [],
                       "start_date": "2026-11-20T15:00:00Z"}) is None
