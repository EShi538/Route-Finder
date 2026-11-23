"""Strava webhook events: keep stored activities in step with Strava without re-syncing.

Strava doesn't sign its events, so anyone who finds the callback URL can post one.
We therefore treat an event only as a hint about *what* changed and ask Strava for
the truth before touching anything:

  * activity create / update / delete -> fetch the activity: store it if it exists,
    delete our copy only if Strava says it's gone (404);
  * athlete "authorized": "false"      -> delete the athlete's data only if Strava
    really rejects their tokens.

A forged event can make us re-check an activity, but never invent or destroy data.
"""

import logging

from app import config, db, strava

log = logging.getLogger(__name__)


def handle_event(event: dict) -> tuple[str, int | None]:
    """Apply one event. Returns (what happened, athlete whose data changed or None)."""
    sub = config.STRAVA_SUBSCRIPTION_ID
    if sub and str(event.get("subscription_id")) != sub:
        return "ignored: other subscription", None

    owner, kind = event.get("owner_id"), event.get("object_type")
    conn = db.connect()
    try:
        if not isinstance(owner, int) or db.get_athlete(conn, owner) is None:
            return "ignored: unknown athlete", None

        if kind == "activity":
            activity_id = event.get("object_id")
            if not isinstance(activity_id, int):
                return "ignored: bad activity id", None
            a = strava.fetch_activity(conn, owner, activity_id)
            if a is not None and (a.get("athlete") or {}).get("id") not in (None, owner):
                return "ignored: activity belongs to someone else", None
            row = strava.to_row(owner, a) if a else None
            if row:
                db.upsert_activity(conn, row)
                conn.commit()
                return "stored", owner
            # Deleted on Strava, or edited into something we don't keep (e.g. GPS removed).
            return ("deleted" if db.delete_activity(conn, activity_id, owner) else "nothing to delete"), owner

        if kind == "athlete" and (event.get("updates") or {}).get("authorized") == "false":
            if strava.still_authorized(conn, owner):
                return "ignored: athlete is still authorized", None
            db.delete_athlete(conn, owner)
            return "athlete deauthorized: data deleted", owner

        return "ignored: unhandled event", None
    finally:
        conn.close()
