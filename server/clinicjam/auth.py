"""Stateless signed sessions and login brute-force protection.

Why not a set of random tokens in memory?

* Every login added a token to a set that was never pruned — a guest
  could grow it without bound, and a restart logged everyone out.
* An HMAC-signed token needs no server-side storage at all, survives
  restarts, and is invalidated automatically when the PIN it was issued
  for changes (the PIN is mixed into the signing key).

Token format:  ``tier.expires.device.signature``  (all URL-safe ASCII)
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

ADMIN_TTL = 12 * 3600          # a host re-enters the PIN twice a day at most
KIOSK_ADMIN_TTL = 15 * 60      # host controls on the shared touchscreen
GUEST_TTL = 30 * 24 * 3600     # guests stay joined for a month

_DEVICE_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


class SessionSigner:
    def __init__(self, secret: bytes, pins: dict[str, str]):
        """`pins` maps tier name -> current PIN for that tier."""
        self._secret = secret
        self._pins = pins

    def _key(self, tier: str) -> bytes:
        pin = self._pins.get(tier, "")
        return hashlib.sha256(self._secret + b"|" + tier.encode() + b"|" + pin.encode()).digest()

    def issue(self, tier: str, ttl: int, device_id: str = "") -> str:
        device = "".join(ch for ch in device_id if ch in _DEVICE_SAFE)[:40]
        expires = str(int(time.time()) + ttl)
        payload = f"{tier}.{expires}.{device}"
        sig = hmac.new(self._key(tier), payload.encode(), hashlib.sha256).hexdigest()[:32]
        return f"{payload}.{sig}"

    def verify(self, token: str, tier: str) -> str | None:
        """Return the device id bound to the token (may be "") or None."""
        if not token or len(token) > 160:
            return None
        parts = token.split(".")
        if len(parts) != 4 or parts[0] != tier:
            return None
        payload = ".".join(parts[:3])
        expected = hmac.new(self._key(tier), payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(expected, parts[3]):
            return None
        try:
            if int(parts[1]) < time.time():
                return None
        except ValueError:
            return None
        return parts[2]


class LoginLimiter:
    """Per-client exponential lockout plus a global brake.

    * After `threshold` failures from one address, that address is locked
      for 30 s, doubling per further failure up to 15 minutes.
    * If the whole network produces more than `global_limit` failures in
      ten minutes (someone rotating addresses), every attempt is slowed
      down by a few seconds. That turns a 4-digit PIN from "seconds" into
      "hours" and a 6-digit one into "weeks" — and it is loud in the logs.
    """

    def __init__(self, threshold: int = 5, global_limit: int = 40):
        self.threshold = threshold
        self.global_limit = global_limit
        self._by_ip: dict[str, dict[str, float]] = {}
        self._global: list[float] = []
        self._max_tracked = 2000

    def _prune(self, now: float) -> None:
        self._global = [t for t in self._global if now - t < 600]
        if len(self._by_ip) > self._max_tracked or secrets.randbelow(50) == 0:
            stale = [ip for ip, e in self._by_ip.items()
                     if now - e["last"] > 3600 and e["lock_until"] < now]
            for ip in stale:
                del self._by_ip[ip]
            if len(self._by_ip) > self._max_tracked:
                for ip in sorted(self._by_ip, key=lambda k: self._by_ip[k]["last"])[
                    : len(self._by_ip) - self._max_tracked
                ]:
                    del self._by_ip[ip]

    def locked_for(self, ip: str) -> float:
        """Seconds this address must wait before another attempt (0 = go)."""
        now = time.time()
        entry = self._by_ip.get(ip)
        if entry and entry["lock_until"] > now:
            return entry["lock_until"] - now
        return 0.0

    def global_delay(self) -> float:
        now = time.time()
        self._prune(now)
        if len(self._global) >= self.global_limit:
            return 3.0
        return 0.0

    def record_failure(self, ip: str) -> float:
        """Register a failed attempt; returns the lockout now applied."""
        now = time.time()
        self._prune(now)
        self._global.append(now)
        entry = self._by_ip.setdefault(
            ip, {"failures": 0.0, "last": now, "lock_until": 0.0}
        )
        entry["failures"] += 1
        entry["last"] = now
        over = entry["failures"] - self.threshold
        if over >= 0:
            lock = min(900.0, 30.0 * (2 ** over))
            entry["lock_until"] = now + lock
            return lock
        return 0.0

    def record_success(self, ip: str) -> None:
        self._by_ip.pop(ip, None)


class RateLimiter:
    """Simple per-key sliding-window limiter for cheap-to-abuse endpoints."""

    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        hits = self._hits.get(key)
        if hits is None:
            hits = self._hits[key] = []
        cutoff = now - self.window
        while hits and hits[0] < cutoff:
            hits.pop(0)
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        if len(self._hits) > 2000:
            for k in [k for k, v in self._hits.items() if not v or v[-1] < cutoff]:
                del self._hits[k]
        return True
