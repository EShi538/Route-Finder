"""Manage the app's Strava webhook subscription (Strava allows one per API app).

    python -m app.webhook_admin view
    python -m app.webhook_admin subscribe https://your-host/webhooks/strava
    python -m app.webhook_admin unsubscribe <subscription id>

`subscribe` only succeeds while the app is running at that URL with STRAVA_VERIFY_TOKEN
set, because Strava immediately calls GET /webhooks/strava to check the token.
"""

import sys

import httpx

from app import config


def _creds() -> dict:
    if not (config.STRAVA_CLIENT_ID and config.STRAVA_CLIENT_SECRET):
        sys.exit("Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET in .env first.")
    return {"client_id": config.STRAVA_CLIENT_ID, "client_secret": config.STRAVA_CLIENT_SECRET}


def _show(resp: httpx.Response) -> None:
    print(resp.status_code, resp.text or "(no body)")
    if resp.is_error:
        sys.exit(1)


def view() -> None:
    _show(httpx.get(config.STRAVA_PUSH_URL, params=_creds()))


def subscribe(callback_url: str) -> None:
    if not config.STRAVA_VERIFY_TOKEN:
        sys.exit("Set STRAVA_VERIFY_TOKEN in .env first (any long random string).")
    resp = httpx.post(config.STRAVA_PUSH_URL, timeout=30.0, data={
        **_creds(), "callback_url": callback_url, "verify_token": config.STRAVA_VERIFY_TOKEN,
    })
    _show(resp)
    print("Put this id in .env as STRAVA_SUBSCRIPTION_ID so events for other subscriptions are ignored.")


def unsubscribe(subscription_id: str) -> None:
    _show(httpx.delete(f"{config.STRAVA_PUSH_URL}/{subscription_id}", params=_creds()))


if __name__ == "__main__":
    commands = {"view": (view, 0), "subscribe": (subscribe, 1), "unsubscribe": (unsubscribe, 1)}
    if len(sys.argv) < 2 or sys.argv[1] not in commands or len(sys.argv) - 2 != commands[sys.argv[1]][1]:
        sys.exit(__doc__)
    fn, _ = commands[sys.argv[1]]
    fn(*sys.argv[2:])
