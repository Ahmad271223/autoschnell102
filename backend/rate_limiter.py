"""Simple in-process rate limiter for login endpoints.

Uses a sliding-window counter per IP address.  Works per-process (not shared
across multiple Uvicorn workers), but is still effective against the most
common brute-force and credential-stuffing attacks on a single-server setup.

Usage:
    from rate_limiter import login_limiter
    if not login_limiter.check(ip):
        raise HTTPException(429, "Zu viele Anmeldeversuche – bitte 60 Sekunden warten.")
"""
import os
import time
from collections import defaultdict
from threading import Lock

# Globaler Schalter: erlaubt das Deaktivieren des Rate-Limiters fuer
# automatisierte Tests / CI (RATE_LIMIT_ENABLED=false). In Produktion
# IMMER aktiv lassen (Default). Niemals in der Prod-.env auf false setzen.
_RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "true").strip().lower() != "false"


class SlidingWindowRateLimiter:
    """Thread-safe sliding-window rate limiter."""

    def __init__(self, max_attempts: int = 10, window_seconds: int = 60):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()
        # Garbage-Collection: alle N Aufrufe abgelaufene Buckets entfernen.
        self._gc_every = 500
        self._calls_since_gc = 0

    def check(self, key: str) -> bool:
        """Return True if the request is allowed; False if the key is rate-limited.

        Call this BEFORE processing the request.  The attempt is counted even
        when the login fails, so a failed login still increments the counter.
        """
        # Test/CI-Bypass — niemals in Produktion aktivieren.
        if not _RATE_LIMIT_ENABLED:
            return True
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            timestamps = self._buckets[key]
            # Drop timestamps outside the current window.
            fresh = [t for t in timestamps if t > cutoff]
            if len(fresh) >= self.max_attempts:
                self._buckets[key] = fresh
                return False
            fresh.append(now)
            self._buckets[key] = fresh
            # Periodisches Aufraeumen leerer/abgelaufener Buckets, sonst
            # waechst das Dict unbegrenzt (eine Entry pro je gesehener IP).
            # Bei 200-500 Nutzern + Bots ein echtes Speicherleck.
            self._maybe_gc(cutoff)
            return True

    def _maybe_gc(self, cutoff: float) -> None:
        """Entfernt Buckets ohne aktuelle Timestamps. Laeuft amortisiert nur
        gelegentlich (alle GC_EVERY Aufrufe), um den Overhead klein zu halten.
        Muss unter gehaltenem _lock aufgerufen werden."""
        self._calls_since_gc += 1
        if self._calls_since_gc < self._gc_every:
            return
        self._calls_since_gc = 0
        stale = [k for k, ts in self._buckets.items()
                 if not any(t > cutoff for t in ts)]
        for k in stale:
            del self._buckets[k]

    def reset(self, key: str) -> None:
        """Clear the counter for a key (e.g. after a successful login)."""
        with self._lock:
            self._buckets.pop(key, None)


# Shared instances — imported directly by route modules.
# 10 attempts / 60 s per IP for the dealer/admin login.
login_limiter = SlidingWindowRateLimiter(max_attempts=10, window_seconds=60)

# Slightly more lenient for the driver app (mobile clients can have flaky
# connectivity and may retry quickly), but still bounded.
driver_login_limiter = SlidingWindowRateLimiter(max_attempts=15, window_seconds=60)

# Registration: 5 new accounts per IP per hour prevents spam account creation.
register_limiter = SlidingWindowRateLimiter(max_attempts=5, window_seconds=3600)

# Driver registration: same limit.
driver_register_limiter = SlidingWindowRateLimiter(max_attempts=5, window_seconds=3600)
