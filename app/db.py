import sqlite3

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS athletes (
    id            INTEGER PRIMARY KEY,
    name          TEXT,
    access_token  TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS activities (
    id          INTEGER PRIMARY KEY,
    athlete_id  INTEGER NOT NULL REFERENCES athletes(id),
    name        TEXT,
    sport_type  TEXT NOT NULL,
    distance_m  REAL NOT NULL,
    elev_gain_m REAL NOT NULL,
    start_lat   REAL NOT NULL,
    start_lng   REAL NOT NULL,
    polyline    TEXT,
    start_date  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activities_athlete ON activities(athlete_id, sport_type);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def save_athlete(conn, athlete_id, name, access_token, refresh_token, expires_at) -> None:
    conn.execute(
        """INSERT INTO athletes (id, name, access_token, refresh_token, expires_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name, access_token=excluded.access_token,
               refresh_token=excluded.refresh_token, expires_at=excluded.expires_at""",
        (athlete_id, name, access_token, refresh_token, expires_at),
    )
    conn.commit()


def get_athlete(conn, athlete_id: int):
    return conn.execute("SELECT * FROM athletes WHERE id = ?", (athlete_id,)).fetchone()


def upsert_activity(conn, a: dict) -> None:
    conn.execute(
        """INSERT INTO activities (id, athlete_id, name, sport_type, distance_m, elev_gain_m,
                                   start_lat, start_lng, polyline, start_date)
           VALUES (:id, :athlete_id, :name, :sport_type, :distance_m, :elev_gain_m,
                   :start_lat, :start_lng, :polyline, :start_date)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name, sport_type=excluded.sport_type,
               distance_m=excluded.distance_m, elev_gain_m=excluded.elev_gain_m,
               polyline=excluded.polyline""",
        a,
    )


def activities_for(conn, athlete_id: int, sport_types: set[str]):
    marks = ",".join("?" * len(sport_types))
    return conn.execute(
        f"SELECT * FROM activities WHERE athlete_id = ? AND sport_type IN ({marks})",
        (athlete_id, *sport_types),
    ).fetchall()
