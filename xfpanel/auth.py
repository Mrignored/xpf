"""Password hashing, signed session cookies and login throttling."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from typing import Dict, Optional, Tuple

PBKDF2_ROUNDS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ROUNDS, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt_b64, digest_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session(secret: str, username: str, ttl_seconds: int, now: Optional[float] = None) -> str:
    expires = int((now if now is not None else time.time()) + ttl_seconds)
    user_b64 = base64.urlsafe_b64encode(username.encode()).decode().rstrip("=")
    payload = "{}.{}".format(user_b64, expires)
    return "{}.{}".format(payload, _sign(secret, payload))


def read_session(secret: str, token: str, now: Optional[float] = None) -> Optional[str]:
    """Return the username for a valid, unexpired token; otherwise None."""
    try:
        user_b64, expires, signature = token.split(".")
        payload = "{}.{}".format(user_b64, expires)
        if not hmac.compare_digest(signature, _sign(secret, payload)):
            return None
        if int(expires) < (now if now is not None else time.time()):
            return None
        padded = user_b64 + "=" * (-len(user_b64) % 4)
        return base64.urlsafe_b64decode(padded.encode()).decode()
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


class LoginThrottle:
    """Blocks an address for a while after too many failed logins."""

    def __init__(self, max_failures: int = 5, window: int = 300, lockout: int = 300) -> None:
        self.max_failures = max_failures
        self.window = window
        self.lockout = lockout
        self._failures: Dict[str, Tuple[int, float]] = {}
        self._locked: Dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str, now: Optional[float] = None) -> int:
        now = now if now is not None else time.time()
        with self._lock:
            until = self._locked.get(key, 0.0)
            if until > now:
                return int(until - now) + 1
            self._locked.pop(key, None)
            return 0

    def failure(self, key: str, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        with self._lock:
            count, first = self._failures.get(key, (0, now))
            if now - first > self.window:
                count, first = 0, now
            count += 1
            self._failures[key] = (count, first)
            if count >= self.max_failures:
                self._locked[key] = now + self.lockout
                self._failures.pop(key, None)

    def success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._locked.pop(key, None)
