import hashlib
import secrets
import time

import bcrypt

from config import settings


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


class Throttle:
    """Counts events per key (an e-mail address, an IP) within `window_s` and
    says when a key has had `limit` of them. In memory - a restart resets it,
    which is fine for a single app process. Never holds more than `max_keys`
    keys, so junk requests with endless new addresses can't fill the memory."""

    def __init__(self, limit: int, window_s: float, max_keys: int = 5000):
        self.limit = limit
        self.window_s = window_s
        self.max_keys = max_keys
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        times = [t for t in self._failures.get(key, []) if now - t < self.window_s]
        if times:
            self._failures[key] = times
        else:
            self._failures.pop(key, None)
        return times

    def blocked(self, *keys: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return any(len(self._recent(key, now)) >= self.limit for key in keys)

    def record(self, *keys: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if len(self._failures) >= self.max_keys:
            for key in list(self._failures):
                self._recent(key, now)  # drops keys with nothing recent
            while len(self._failures) >= self.max_keys:  # still full: forget the oldest
                del self._failures[next(iter(self._failures))]
        for key in keys:
            self._failures.setdefault(key, []).append(now)

    def forget(self, key: str) -> None:
        self._failures.pop(key, None)


class LoginThrottle(Throttle):
    """Slows down password guessing: after `limit` failed logins for the same
    e-mail address or from the same IP within `window_s`, further attempts
    are refused until the window has passed."""

    def __init__(self, limit: int = 10, window_s: float = 15 * 60):
        super().__init__(limit, window_s)

    def blocked(self, email: str, ip: str, now: float | None = None) -> bool:
        return super().blocked(f"mail:{email}", f"ip:{ip}", now=now)

    def failed(self, email: str, ip: str, now: float | None = None) -> None:
        self.record(f"mail:{email}", f"ip:{ip}", now=now)

    def succeeded(self, email: str) -> None:
        self.forget(f"mail:{email}")


login_throttle = LoginThrottle()
# "Passwort vergessen?" sends an e-mail: at most 5 an hour per address and per IP.
reset_throttle = Throttle(limit=5, window_s=3600)
# Signing up: at most 10 new accounts an hour from one IP.
signup_throttle = Throttle(limit=10, window_s=3600)


def reset_all_throttles() -> None:
    """For tests."""
    for throttle in (login_throttle, reset_throttle, signup_throttle):
        throttle._failures.clear()


def client_ip(request) -> str:
    """The visitor's IP, for the throttles. Behind a proxy every request comes
    from the proxy, so the real IP is taken from the header the proxy sets
    (CLIENT_IP_HEADER, e.g. CF-Connecting-IP behind Cloudflare). Only set it
    when the app can't be reached except through that proxy - otherwise
    anyone could send the header and pick their own IP."""
    header = settings.client_ip_header
    forwarded = request.headers.get(header, "").split(",")[0].strip() if header else ""
    return forwarded or (request.client.host if request.client else "unknown")
