import os
import secrets
import warnings

from dotenv import load_dotenv

load_dotenv()

STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET", "")
# Must match the "Authorization Callback Domain" in your Strava API settings (localhost works for any port).
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8000/auth/callback")
DB_PATH = os.getenv("DB_PATH", "routes.db")

# Signs the login cookie. Set it to a long random string in production; without one, a
# random key is used and everyone is logged out whenever the app restarts.
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(32)
    warnings.warn("SESSION_SECRET is not set; logins won't survive a restart", stacklevel=1)
# Only send the cookie over HTTPS when the app is served over HTTPS.
SECURE_COOKIES = REDIRECT_URI.startswith("https://")

# Webhooks (optional). STRAVA_VERIFY_TOKEN is a secret you make up; Strava echoes it back when
# you create the subscription. STRAVA_SUBSCRIPTION_ID, once known, makes us ignore events for
# any other subscription.
STRAVA_VERIFY_TOKEN = os.getenv("STRAVA_VERIFY_TOKEN", "")
STRAVA_SUBSCRIPTION_ID = os.getenv("STRAVA_SUBSCRIPTION_ID", "")
STRAVA_PUSH_URL = "https://www.strava.com/api/v3/push_subscriptions"

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API = "https://www.strava.com/api/v3"
