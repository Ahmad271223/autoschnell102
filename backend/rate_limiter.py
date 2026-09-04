"""Simple in-process rate limiter for login endpoints.

Uses a sliding-window counter per IP address.  Works per-process (not shared
across multiple Uvicorn workers), but is still effective against the most
common brute-force and credential-stuffing attacks on a single-server setup.

Usage:
    from rate_limiter import login_limiter
    if not await login_limiter.check(ip):
        raise HTTPException(429, "Zu viele Anmeldeversuche – bitte 60 Sekunden warten.")
"""
import ipaddress
import os
import time
from collections import defaultdict
from pathlib import Path
from threading import Lock

# .env selbst laden — der Schalter darf nicht davon abhängen, in welcher
# Reihenfolge die Module importiert werden (sonst liest er den Default,
# bevor server.py/auth.py die .env geladen haben).
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# Globaler Schalter: erlaubt das Deaktivieren des Rate-Limiters fuer
# automatisierte Tests / CI (RATE_LIMIT_ENABLED=false). In Produktion
# IMMER aktiv lassen (Default). Niemals in der Prod-.env auf false setzen.
_RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "true").strip().lower() != "false"

# Loopback-Ausnahme: Zugriffe vom selben Rechner (127.0.0.1/::1) zaehlen
# nicht — sonst blockieren sich lokale Tests und die eigene Nutzung
# gegenseitig (alle teilen sich EINE IP). Im echten Server-Betrieb kommen
# Nutzer nie von Loopback; ein Angreifer auch nicht. Abschaltbar via
# RATE_LIMIT_EXEMPT_LOOPBACK=false (z.B. hinter lokalem Reverse-Proxy,
# der Client-IPs nicht weiterreicht — dort besser den Proxy fixen).
_EXEMPT_LOOPBACK = os.environ.get(
    "RATE_LIMIT_EXEMPT_LOOPBACK", "true").strip().lower() != "false"
_LOOPBACK_KEYS = {"127.0.0.1", "::1", "localhost", "testclient"}

# Hinter einem Reverse-Proxy (nginx/Ingress) ist request.client.host die
# ADRESSE DES PROXYS (meist 127.0.0.1) — der Rate-Limiter wuerde dann alle
# Nutzer in einen Bucket werfen ODER (mit Loopback-Ausnahme) gar nicht
# greifen. Ist TRUST_PROXY gesetzt, nehmen wir die echte Client-IP aus
# X-Forwarded-For (erster Eintrag = urspruenglicher Client). NUR aktivieren,
# wenn WIRKLICH ein vertrauenswuerdiger Proxy davor sitzt, der den Header
# setzt/ueberschreibt — sonst koennte ihn ein Angreifer selbst faelschen.
_TRUST_PROXY = os.environ.get("TRUST_PROXY", "").strip().lower() in ("1", "true", "yes")

# Sitzen MEHRERE Vermittler davor (z.B. Cloudflare -> Load Balancer ->
# nginx), reicht "letzter Eintrag" nicht: der letzte stammt dann vom
# Load Balancer, und ALLE Besucher landeten unter derselben Adresse —
# eine einzige fehlgeschlagene Anmeldung wuerde alle anderen aussperren.
# TRUSTED_PROXIES nennt die eigenen Vermittler als Netze (Komma-Liste,
# z.B. "10.0.0.0/16,127.0.0.1"). Aus der Kette wird dann der letzte
# Eintrag genommen, der NICHT zu den eigenen Vermittlern gehoert.
_TRUSTED_PROXIES = []
for _netz in os.environ.get("TRUSTED_PROXIES", "").split(","):
    _netz = _netz.strip()
    if not _netz:
        continue
    try:
        _TRUSTED_PROXIES.append(ipaddress.ip_network(_netz, strict=False))
    except ValueError:
        pass


def _ist_eigener_proxy(adresse: str) -> bool:
    try:
        ip = ipaddress.ip_address(adresse)
    except ValueError:
        return False
    return any(ip in netz for netz in _TRUSTED_PROXIES)


def client_ip(request) -> str:
    """Echte Besucher-Adresse fuer die Anfragesperren — proxy-bewusst.

    Ohne TRUSTED_PROXIES gilt wie bisher: der LETZTE Eintrag in
    X-Forwarded-For stammt vom eigenen Proxy und ist damit der einzige,
    dem zu trauen ist (der erste ist vom Besucher faelschbar).

    Mit TRUSTED_PROXIES werden die eigenen Vermittler von hinten
    uebersprungen; genommen wird der letzte fremde Eintrag. Nur dann
    wird auch CF-Connecting-IP akzeptiert, und nur wenn die Anfrage
    wirklich ueber einen eigenen Vermittler hereinkam."""
    if not _TRUST_PROXY:
        return (request.client.host if request.client else None) or "unknown"
    nachbar = (request.client.host if request.client else "") or ""
    if _TRUSTED_PROXIES and _ist_eigener_proxy(nachbar):
        # Cloudflare traegt die echte Adresse hier ein; der Header ist
        # nur glaubwuerdig, weil die Anfrage ueber unseren Vermittler kam.
        cf = request.headers.get("cf-connecting-ip", "").strip()
        if cf:
            return cf
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        kette = [t.strip() for t in fwd.split(",") if t.strip()]
        if _TRUSTED_PROXIES:
            for eintrag in reversed(kette):
                if not _ist_eigener_proxy(eintrag):
                    return eintrag
            return kette[0]          # nur eigene Vermittler in der Kette
        return kette[-1]
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real
    return nachbar or "unknown"


class SlidingWindowRateLimiter:
    """Rate-Limiter mit gemeinsamem Mongo-Zaehler (alle Worker) und
    In-Prozess-Fallback."""

    _index_ok = False

    def __init__(self, max_attempts: int = 10, window_seconds: int = 60,
                 name: str = ""):
        # Stabiler Name = gemeinsamer Schluessel ueber ALLE Worker-Prozesse
        # (id(self) o.ae. waere je Prozess anders und wuerde die Zaehler
        # wieder trennen).
        self.name = name or f"limit{max_attempts}per{window_seconds}"
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()
        # Garbage-Collection: alle N Aufrufe abgelaufene Buckets entfernen.
        self._gc_every = 500
        self._calls_since_gc = 0

    async def check(self, key: str) -> bool:
        """True = erlaubt, False = limitiert. VOR der Verarbeitung rufen —
        auch fehlgeschlagene Versuche zaehlen.

        Der Zaehler liegt in MongoDB und gilt damit GEMEINSAM fuer alle
        Uvicorn-Worker (vorher zaehlte jeder der z.B. 8 Prozesse separat —
        aus 10 Versuchen/Minute wurden praktisch bis zu 80). Faellt die
        Datenbank aus, greift der bisherige In-Prozess-Zaehler als Netz.
        """
        # Test/CI-Bypass — niemals in Produktion aktivieren.
        if not _RATE_LIMIT_ENABLED:
            return True
        # Lokale Zugriffe (gleicher Rechner) nicht limitieren.
        if _EXEMPT_LOOPBACK and key in _LOOPBACK_KEYS:
            return True
        try:
            return await self._check_mongo(key)
        except Exception:
            return self._check_lokal(key)

    async def _check_mongo(self, key: str) -> bool:
        """Festes Zeitfenster, atomar per $inc — ein Dokument je
        (Limiter, Schluessel, Fenster); TTL raeumt alte Fenster weg."""
        import time as _t
        from datetime import datetime, timedelta, timezone
        from pymongo import ReturnDocument
        from deps import db
        if not SlidingWindowRateLimiter._index_ok:
            await db.rate_limits.create_index("ablauf", expireAfterSeconds=0)
            SlidingWindowRateLimiter._index_ok = True
        fenster = int(_t.time() // self.window_seconds)
        doc = await db.rate_limits.find_one_and_update(
            {"_id": f"{self.name}:{key}:{fenster}"},
            {"$inc": {"n": 1},
             "$setOnInsert": {"ablauf": datetime.now(timezone.utc)
                              + timedelta(seconds=self.window_seconds * 2)}},
            upsert=True, return_document=ReturnDocument.AFTER)
        return doc["n"] <= self.max_attempts

    def _check_lokal(self, key: str) -> bool:
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

    async def reset(self, key: str) -> None:
        """Zaehler eines Schluessels leeren (z.B. nach erfolgreichem Login)."""
        with self._lock:
            self._buckets.pop(key, None)
        try:
            from deps import db
            await db.rate_limits.delete_many(
                {"_id": {"$regex": f"^{self.name}:{key}:"}})
        except Exception:
            pass


# Shared instances — imported directly by route modules.
# 10 attempts / 60 s per IP for the dealer/admin login.
login_limiter = SlidingWindowRateLimiter(max_attempts=10, window_seconds=60, name="login")

# Slightly more lenient for the driver app (mobile clients can have flaky
# connectivity and may retry quickly), but still bounded.
driver_login_limiter = SlidingWindowRateLimiter(max_attempts=15, window_seconds=60, name="fahrer-login")

# Registration: 5 new accounts per IP per hour prevents spam account creation.
register_limiter = SlidingWindowRateLimiter(max_attempts=5, window_seconds=3600, name="registrierung")

# Driver registration: same limit.
driver_register_limiter = SlidingWindowRateLimiter(max_attempts=5, window_seconds=3600, name="registrierung")
