from datetime import datetime, timedelta, timezone

import pytest

from app import scoring
from app.geo import haversine_km

NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)
HOME = (47.6600, -122.3000)


def row(id, dist_km, lat=HOME[0], lng=HOME[1], elev=100, days_ago=10, sport="Ride", name=None):
    return {
        "id": id, "name": name or f"route {id}", "sport_type": sport,
        "distance_m": dist_km * 1000, "elev_gain_m": elev,
        "start_lat": lat, "start_lng": lng, "polyline": None,
        "start_date": (NOW - timedelta(days=days_ago)).isoformat(),
    }


def test_haversine_known_distance():
    # Seattle to Portland is roughly 233 km in a straight line
    assert haversine_km(47.6062, -122.3321, 45.5152, -122.6784) == pytest.approx(233, abs=3)


def test_group_routes_merges_repeats_and_keeps_latest():
    rows = [row(1, 40.0, days_ago=30), row(2, 40.5, days_ago=5), row(3, 60.0, days_ago=2)]
    cands = scoring.group_routes(rows)
    assert len(cands) == 2
    forty = next(c for c in cands if c.distance_km < 50)
    assert forty.times_done == 2
    assert forty.activity_id == 2  # most recent kept


def test_group_routes_keeps_different_sports_separate():
    cands = scoring.group_routes([row(1, 10, sport="Run"), row(2, 10, sport="Ride")])
    assert len(cands) == 2


@pytest.mark.parametrize("dist, expected", [(40, 1.0), (45, 0.5), (50, 0.0), (30, 0.0)])
def test_distance_fit(dist, expected):
    assert scoring.distance_fit(dist, 40) == pytest.approx(expected)


def test_elevation_fit_no_limit_and_over_limit():
    assert scoring.elevation_fit(900, None) == 1.0
    assert scoring.elevation_fit(400, 500) == 1.0
    assert scoring.elevation_fit(750, 500) == pytest.approx(0.5)
    assert scoring.elevation_fit(100, 0) == 0.0


def test_novelty_caps_at_one():
    assert scoring.novelty(NOW, NOW) == 0.0
    assert scoring.novelty(NOW - timedelta(days=21), NOW) == pytest.approx(0.5)
    assert scoring.novelty(NOW - timedelta(days=400), NOW) == 1.0


def test_recommend_ranks_closest_fit_first_and_filters_out_of_range():
    cands = scoring.group_routes([
        row(1, 40, days_ago=60),                   # perfect distance, at home, not done lately
        row(2, 44, days_ago=1),                    # a bit long, done yesterday
        row(3, 80),                                # far too long -> filtered
        row(4, 40, lat=48.5, lng=-122.3),          # ~93 km away -> filtered
    ])
    recs = scoring.recommend(cands, *HOME, target_km=40, now=NOW)
    assert [r.candidate.activity_id for r in recs] == [1, 2]
    assert recs[0].score > recs[1].score
    assert any("target" in w for w in recs[0].why)


def test_recommend_empty_history():
    assert scoring.recommend([], *HOME, target_km=40, now=NOW) == []


def test_recommend_respects_limit():
    cands = scoring.group_routes([row(i, 40, lat=HOME[0] + i * 0.01) for i in range(10)])
    assert len(scoring.recommend(cands, *HOME, target_km=40, limit=3, now=NOW)) == 3


def test_recommend_imperial_wording():
    cands = scoring.group_routes([row(1, 40.2335, elev=300)])  # 25 mi, ~984 ft
    [metric] = scoring.recommend(cands, *HOME, target_km=40.2335, max_elev_m=400, now=NOW)
    [imperial] = scoring.recommend(cands, *HOME, target_km=40.2335, max_elev_m=400, now=NOW, units="imperial")
    assert imperial.why[0] == "25.0 mi vs. 25 mi target"
    assert imperial.why[2] == "984 ft climbing"
    away_mi = float(imperial.why[1].removeprefix("starts about ").removesuffix(" mi away"))
    assert (away_mi * 4) == int(away_mi * 4)  # whole quarter miles
    assert metric.why[0] == "40.2 km vs. 40.2 km target"
    # Ranking is unchanged; only the start distance is rounded in the shown unit.
    assert imperial.score == metric.score


def test_normalize_weights_defaults_and_overrides():
    w = scoring.normalize_weights()
    assert sum(w.values()) == pytest.approx(1)
    assert w["distance"] == pytest.approx(0.40)
    w = scoring.normalize_weights({"distance": 0, "proximity": 0, "elevation": 0, "novelty": 5})
    assert w == {"distance": 0, "proximity": 0, "elevation": 0, "novelty": 1}
    assert scoring.normalize_weights({"novelty": None}) == scoring.normalize_weights()


@pytest.mark.parametrize("bad", [
    {"distance": 0, "proximity": 0, "elevation": 0, "novelty": 0},
    {"distance": -1},
    {"vibes": 10},
])
def test_normalize_weights_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        scoring.normalize_weights(bad)


def test_weights_change_ranking():
    cands = scoring.group_routes([
        row(1, 40, days_ago=1),     # exact distance, done yesterday
        row(2, 44, days_ago=90),    # a bit long, not done in months
    ])
    distance_first = scoring.recommend(cands, *HOME, target_km=40, now=NOW,
                                       weights={"distance": 90, "novelty": 10})
    novelty_first = scoring.recommend(cands, *HOME, target_km=40, now=NOW,
                                      weights={"distance": 10, "novelty": 90})
    assert [r.candidate.activity_id for r in distance_first] == [1, 2]
    assert [r.candidate.activity_id for r in novelty_first] == [2, 1]


def test_zero_weight_still_filters():
    cands = scoring.group_routes([row(1, 80)])  # far too long
    assert scoring.recommend(cands, *HOME, target_km=40, now=NOW, weights={"distance": 0}) == []
