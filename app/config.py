import os
import secrets
import sys


class Settings:
    # DATABASE_URL itself is read directly by shared/database.py (also used by the
    # tracker process) - not duplicated here to avoid the two drifting apart.
    session_secret: str = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
    if not os.environ.get("SESSION_SECRET"):
        print(
            "WARNING: SESSION_SECRET is not set - using a random secret for this process. "
            "All pilots will be logged out on every restart. Set SESSION_SECRET in .env for production.",
            file=sys.stderr,
        )
    # Password reset links expire after this long.
    reset_token_ttl_minutes: int = int(os.environ.get("RESET_TOKEN_TTL_MINUTES", "60"))
    base_url: str = os.environ.get("BASE_URL", "http://localhost:8000")
    # Header with the visitor's real IP, set by the proxy in front (see
    # security.client_ip). Empty: use the connection's address.
    client_ip_header: str = os.environ.get("CLIENT_IP_HEADER", "").lower()


settings = Settings()
