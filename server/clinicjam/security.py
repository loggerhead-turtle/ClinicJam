"""In-memory helpers for session/auth hygiene on a long-running, single
process device: nothing here needs to survive a restart (sessions reset
to "logged out" on restart, which is an acceptable, safe default), but
everything here DOES need to stop growing forever across a months-long
uptime — a Pi left running at an office for a year will see thousands of
distinct guest phones and admin logins.
"""

from __future__ import annotations

import time
from collections import defaultdict


class SessionStore:
    """A set of bearer tokens with a TTL, so logged-in sessions expire and
    get pruned instead of accumulating in memory forever. Mirrors the
    cookie's own max_age so a cookie that's still valid client-side is
    still valid here too."""

    def __init__(self, ttl_seconds: float):
        self.ttl_seconds = ttl_seconds
        self._tokens: dict[str, float] = {}  # token -> issued_at

    def issue(self, token: str) -> None:
        self._tokens[token] = time.time()

    def __contains__(self, token: str) -> bool:
        issued = self._tokens.get(token)
        if issued is None:
            return False
        if time.time() - issued > self.ttl_seconds:
            del self._tokens[token]
            return False
        return True

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)

    def prune(self) -> int:
        """Drop expired tokens proactively (not just lazily on lookup), so
        a session nobody ever rechecks doesn't sit in memory until the
        process restarts. Returns the number removed."""
        cutoff = time.time() - self.ttl_seconds
        stale = [t for t, issued in self._tokens.items() if issued < cutoff]
        for t in stale:
            del self._tokens[t]
        return len(stale)


class LoginRateLimiter:
    """Blocks brute-forcing a short numeric PIN. Tracks failed attempts
    per client IP in a sliding window; once the cap is hit, that IP is
    locked out for a cooldown period regardless of further attempts.

    This is intentionally simple (in-memory, per-process, resets on
    restart) — proportionate for a LAN-only admin PIN, not a substitute
    for a real auth system."""

    def __init__(self, max_attempts: int = 5, window_seconds: float = 60.0,
                 lockout_seconds: float = 60.0):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._failures: dict[str, list[float]] = defaultdict(list)
        self._locked_until: dict[str, float] = {}

    def _prune_window(self, key: str, now: float) -> None:
        cutoff = now - self.window_seconds
        self._failures[key] = [t for t in self._failures[key] if t >= cutoff]

    def retry_after(self, key: str) -> float:
        """Seconds the caller must wait before trying again, or 0 if
        they're clear to attempt a login right now."""
        now = time.time()
        until = self._locked_until.get(key)
        if until is not None:
            if now < until:
                return until - now
            del self._locked_until[key]
            self._failures.pop(key, None)
        return 0.0

    def record_failure(self, key: str) -> None:
        now = time.time()
        self._prune_window(key, now)
        self._failures[key].append(now)
        if len(self._failures[key]) >= self.max_attempts:
            self._locked_until[key] = now + self.lockout_seconds

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)

    def prune(self) -> None:
        """Forget IPs with no recent activity so the dicts don't grow
        forever as different guest phones come and go over months."""
        now = time.time()
        cutoff = now - max(self.window_seconds, self.lockout_seconds)
        for key in list(self._failures):
            self._prune_window(key, now)
            locked = self._locked_until.get(key, 0) > now
            if not self._failures[key] and not locked:
                del self._failures[key]
        for key, until in list(self._locked_until.items()):
            if until < cutoff:
                del self._locked_until[key]
