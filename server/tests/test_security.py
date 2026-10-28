"""Unit tests for the rate limiter and session TTL store (server/clinicjam/security.py)."""

import time

from clinicjam.security import LoginRateLimiter, SessionStore


def test_session_store_issues_and_checks():
    store = SessionStore(ttl_seconds=60)
    store.issue("tok1")
    assert "tok1" in store
    assert "unknown-token" not in store


def test_session_store_expires_after_ttl():
    store = SessionStore(ttl_seconds=0.05)
    store.issue("tok1")
    assert "tok1" in store
    time.sleep(0.08)
    assert "tok1" not in store  # lazy expiry on lookup


def test_session_store_revoke():
    store = SessionStore(ttl_seconds=60)
    store.issue("tok1")
    store.revoke("tok1")
    assert "tok1" not in store


def test_session_store_prune_removes_expired_only():
    store = SessionStore(ttl_seconds=0.05)
    store.issue("old")
    time.sleep(0.08)
    store.issue("fresh")
    removed = store.prune()
    assert removed == 1
    assert "fresh" in store
    assert "old" not in store


def test_rate_limiter_allows_until_threshold():
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=60, lockout_seconds=30)
    assert limiter.retry_after("1.2.3.4") == 0
    limiter.record_failure("1.2.3.4")
    assert limiter.retry_after("1.2.3.4") == 0
    limiter.record_failure("1.2.3.4")
    assert limiter.retry_after("1.2.3.4") == 0
    limiter.record_failure("1.2.3.4")  # 3rd failure -> locked out
    assert limiter.retry_after("1.2.3.4") > 0


def test_rate_limiter_lockout_is_per_key():
    limiter = LoginRateLimiter(max_attempts=2, window_seconds=60, lockout_seconds=30)
    limiter.record_failure("attacker")
    limiter.record_failure("attacker")
    assert limiter.retry_after("attacker") > 0
    assert limiter.retry_after("someone-else") == 0


def test_rate_limiter_success_clears_failures():
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=60, lockout_seconds=30)
    limiter.record_failure("1.2.3.4")
    limiter.record_failure("1.2.3.4")
    limiter.record_success("1.2.3.4")
    limiter.record_failure("1.2.3.4")  # back to 1 failure, not 3
    assert limiter.retry_after("1.2.3.4") == 0


def test_rate_limiter_lockout_expires():
    limiter = LoginRateLimiter(max_attempts=1, window_seconds=60, lockout_seconds=0.05)
    limiter.record_failure("1.2.3.4")
    assert limiter.retry_after("1.2.3.4") > 0
    time.sleep(0.08)
    assert limiter.retry_after("1.2.3.4") == 0


def test_rate_limiter_prune_drops_idle_keys():
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=0.05, lockout_seconds=0.05)
    limiter.record_failure("stale-ip")
    time.sleep(0.1)
    limiter.prune()
    assert "stale-ip" not in limiter._failures
    assert "stale-ip" not in limiter._locked_until
