"""Tokens, and the check that decides who is asking."""
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional


def new_token(prefix: str = "gl") -> str:
    """A long random token. Shown to its owner once; only its hash is kept."""
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    # Tokens are long and random, so a plain fast hash is enough (there is nothing to guess).
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def bearer(header: Optional[str]) -> Optional[str]:
    """The token out of 'Authorization: Bearer <token>', or None."""
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


class FailureLimiter:
    """Too many wrong tokens from one address in a minute: slow down (the server answers 429 until they stop)."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._failures: Dict[str, Deque[float]] = defaultdict(deque)

    def _trim(self, who: str, now: float) -> Deque[float]:
        q = self._failures[who]
        while q and now - q[0] > 60:
            q.popleft()
        return q

    def blocked(self, who: str) -> bool:
        return len(self._trim(who, time.monotonic())) >= self.per_minute

    def failed(self, who: str) -> None:
        now = time.monotonic()
        self._trim(who, now).append(now)

    def succeeded(self, who: str) -> None:
        self._failures.pop(who, None)
