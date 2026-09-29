import hashlib
import secrets
import time

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed stored hash - never match.
        return False


def generate_token() -> tuple[str, str]:
    """Return (raw_token, token_hash). Only the hash is stored; the raw token is
    handed to the user once (in a URL) and can't be recovered from the hash."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# bcrypt only looks at the first 72 bytes, and refuses longer input.
MAX_PASSWORD_BYTES = 72


def password_problem(password: str) -> str | None:
    """German message if the password can't be used, else None."""
    if len(password) < 8:
        return "Das Passwort muss mindestens 8 Zeichen lang sein."
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return "Das Passwort darf höchstens 72 Zeichen lang sein."
    return None


class LoginThrottle:
    """Slows down password guessing: after `limit` failed logins for the same
    e-mail address or from the same IP within `window_s`, further attempts
    are refused until the window has passed. In memory - a restart resets it,
    which is fine for a single app process."""

    def __init__(self, limit: int = 10, window_s: float = 15 * 60):
        self.limit = limit
        self.window_s = window_s
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        times = [t for t in self._failures.get(key, []) if now - t < self.window_s]
        if times:
            self._failures[key] = times
        else:
            self._failures.pop(key, None)
        return times

    def blocked(self, email: str, ip: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return any(len(self._recent(key, now)) >= self.limit for key in (f"mail:{email}", f"ip:{ip}"))

    def failed(self, email: str, ip: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for key in (f"mail:{email}", f"ip:{ip}"):
            self._failures.setdefault(key, []).append(now)

    def succeeded(self, email: str) -> None:
        self._failures.pop(f"mail:{email}", None)


login_throttle = LoginThrottle()


def client_ip(request) -> str:
    """The visitor's IP. Behind Cloudflare the real one is in CF-Connecting-IP
    (the app is only reachable through the tunnel, so it can't be spoofed)."""
    return (request.headers.get("cf-connecting-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown"))
