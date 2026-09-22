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
import logging
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from threading import Lock
from typing import Optional

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
# Pruefbericht Runde 8, Befund 4: Ist TRUST_PROXY an, aber keine Liste
# gesetzt, galten die Kopfzeilen von JEDEM direkten Nachbarn — auch von
# einem Angreifer, der das Backend ohne nginx erreicht. Ohne Liste gelten
# jetzt nur die Netze, in denen ein eigener Vermittler ueberhaupt stehen
# kann: der eigene Rechner und die privaten Bereiche (Docker, Hetzner-
# Privatnetz). Ein oeffentlicher Nachbar ist nie ein Vermittler.
# Runde 9: Die Nachbar-Sperre nimmt die konfigurierte Liste UND die privaten
# Netze. Vorher galt bei gesetzter Liste NUR die Liste — stand dort z.B.
# "127.0.0.1" oder "10.0.0.0/16", war der nginx-Container (172.x im
# Docker-Netz) kein Vermittler mehr, und ALLE Besucher landeten unter der
# Adresse des Containers in EINEM Zaehler: zehn Fehlversuche eines
# Nutzers haetten alle anderen fuer eine Minute ausgesperrt. Ein Nachbar
# aus einem privaten Netz ist nie ein Angreifer von aussen; wer im
# privaten Netz sitzt, koennte ohnehin Schlimmeres.
# Runde 10: Wer im privaten Netz weitere Mieter hat (geteiltes Hetzner-
# Netz, fremde Container), kann mit TRUSTED_PROXIES_NUR_LISTE=true die
# privaten Netze ausschalten — dann gelten Kopfzeilen NUR von den
# ausdruecklich genannten Vermittlern. Achtung: dann muss der eigene
# nginx-Container (Docker-Netz 172.x) in TRUSTED_PROXIES stehen.
_NUR_LISTE = (os.environ.get("TRUSTED_PROXIES_NUR_LISTE") or "").strip().lower() in (
    "1", "true", "ja", "yes")
_PRIVATE_NETZE = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
if _NUR_LISTE and not _TRUSTED_PROXIES:
    logging.getLogger("rate_limiter").warning(
        "TRUSTED_PROXIES_NUR_LISTE=true, aber TRUSTED_PROXIES ist leer oder unlesbar — "
        "es gelten weiterhin die privaten Netze als Vermittler")
_VERMITTLER_NETZE = list(_TRUSTED_PROXIES) + ([] if (_NUR_LISTE and _TRUSTED_PROXIES)
                                              else _PRIVATE_NETZE)

# Pruefbericht 20.09.2026 (B-15): Faellt der gemeinsame Mongo-Zaehler aus,
# zaehlt jeder Worker-Prozess fuer sich — bei 4 Workern je Server galt dann
# das Vierfache. Der lokale Rueckfall teilt das Limit deshalb durch die Zahl
# der Prozesse (WEB_CONCURRENCY, wie im Dockerfile/Compose).
try:
    _WEB_CONCURRENCY = max(1, int((os.environ.get("WEB_CONCURRENCY") or "1").strip() or 1))
except ValueError:
    _WEB_CONCURRENCY = 1
# Hinweis auf den Rueckfall hoechstens einmal je Minute je Limiter.
_RUECKFALL_MELDUNG_ABSTAND_S = 60


def _gueltige_ip(wert: str) -> str:
    """Nur echte Adressen zaehlen — sonst landet "not-an-ip" oder ein
    beliebiger Text als Schluessel im Zaehler und im Fehlerarchiv."""
    w = (wert or "").strip()
    if w.startswith("[") and "]" in w:            # [::1]:1234
        w = w[1:w.index("]")]
    elif w.count(":") == 1:                       # 1.2.3.4:5678
        w = w.split(":")[0]
    try:
        return str(ipaddress.ip_address(w))
    except ValueError:
        return ""


def _ist_vermittler(adresse: str) -> bool:
    """Darf dieser direkte Nachbar ueberhaupt Kopfzeilen setzen?"""
    try:
        ip = ipaddress.ip_address(adresse)
    except ValueError:
        return False
    return any(ip in netz for netz in _VERMITTLER_NETZE)


def _ist_eigener_proxy(adresse: str) -> bool:
    """Phase 3 (3.6, E8): In der Kette zaehlen dieselben Vermittler-Netze wie
    bei der Nachbar-Pruefung — Liste PLUS private Netze (ausser mit
    TRUSTED_PROXIES_NUR_LISTE). Vorher galt in der Kette nur die Liste: die
    private 10.x-Adresse des Hetzner-Load-Balancers wurde zur Besucher-IP,
    und alle Nutzer teilten sich einen Zaehler."""
    try:
        ip = ipaddress.ip_address(adresse)
    except ValueError:
        return False
    return any(ip in netz for netz in _VERMITTLER_NETZE)


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
    # Zuerst der direkte Nachbar: Kommt die Anfrage NICHT von einem eigenen
    # Vermittler, zaehlen die Kopfzeilen gar nicht — der Nachbar ist der
    # Besucher, und was er in X-Forwarded-For schreibt, ist seine Sache.
    if not _ist_vermittler(nachbar):
        return nachbar or "unknown"
    # Cloudflare traegt die echte Adresse hier ein. Nachpruefung Runde 10:
    # Die Kopfzeile zaehlt nur, wenn sie zu der Adresse passt, die die
    # eigene Kette (X-Forwarded-For / X-Real-IP vom eigenen nginx) ergibt —
    # oder wenn es gar keine Kette gibt. Sonst koennte ein Besucher sie
    # selbst setzen (nginx reicht fremde Kopfzeilen durch) und je Anfrage
    # eine andere Adresse vortaeuschen.
    cf = _gueltige_ip(request.headers.get("cf-connecting-ip", ""))
    aus_kette = _aus_kette(request)
    if cf and (not aus_kette or cf == aus_kette):
        return cf
    return aus_kette or nachbar or "unknown"


def _aus_kette(request) -> str:
    """Besucheradresse aus X-Forwarded-For / X-Real-IP; "" wenn nichts da."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        kette = [_gueltige_ip(t) for t in fwd.split(",")]
        kette = [t for t in kette if t]
        if kette and _TRUSTED_PROXIES:
            # Liste gesetzt: eigene Vermittler von hinten ueberspringen.
            for eintrag in reversed(kette):
                if not _ist_eigener_proxy(eintrag):
                    return eintrag
            return kette[0]          # nur eigene Vermittler in der Kette
        if kette:
            return kette[-1]         # ohne Liste: was der Vermittler anhing
    real = _gueltige_ip(request.headers.get("x-real-ip", ""))
    if real:
        return real
    return ""


class SlidingWindowRateLimiter:
    """Rate-Limiter mit gemeinsamem Mongo-Zaehler (alle Worker) und
    In-Prozess-Fallback."""

    _index_ok = False

    def __init__(self, max_attempts: int = 10, window_seconds: int = 60,
                 name: str = "", fail_closed: bool = False):
        # Runde 15 (15.09.2026): Anmelde-Limiter sind fail-closed — faellt
        # der gemeinsame Mongo-Zaehler aus, gilt "gesperrt" statt eines
        # Zaehlers je Prozess (aus 10/min wuerden sonst 10 je Worker).
        self.fail_closed = fail_closed
        # Stabiler Name = gemeinsamer Schluessel ueber ALLE Worker-Prozesse
        # (id(self) o.ae. waere je Prozess anders und wuerde die Zaehler
        # wieder trennen). Pruefbericht 20.09.2026 (SV-16): ein erzeugter
        # Name wie "limit20per60" liesse zwei Limiter mit gleichen Zahlen
        # denselben Zaehler teilen — deshalb Pflicht.
        if not name:
            raise ValueError("Limiter braucht einen Namen (gemeinsamer Schluessel "
                             "ueber alle Worker)")
        self.name = name
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()
        self._rueckfall_gemeldet = 0.0
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
            if self.fail_closed:
                logging.getLogger("rate_limiter").exception(
                    "Limiter %s: gemeinsamer Zaehler nicht erreichbar — fail-closed", self.name)
                return False
            await self._rueckfall_melden()
            return self._check_lokal(key)

    async def _rueckfall_melden(self) -> None:
        """B-15: Der stille Rueckfall auf den Zaehler je Prozess war unsichtbar.
        Jetzt Warnung im Log und Betriebsalarm, gedrosselt je Limiter."""
        jetzt = time.monotonic()
        if jetzt - self._rueckfall_gemeldet < _RUECKFALL_MELDUNG_ABSTAND_S:
            return
        self._rueckfall_gemeldet = jetzt
        logging.getLogger("rate_limiter").warning(
            "Limiter %s: gemeinsamer Zaehler nicht erreichbar — Zaehler je Prozess "
            "(Limit durch WEB_CONCURRENCY=%s geteilt)", self.name, _WEB_CONCURRENCY)
        try:
            from betrieb import alarm
            from deps import db
            await alarm(db, "limiter_lokal", ref=self.name, limiter=self.name,
                        hinweis="Der gemeinsame Zaehler (rate_limits) ist nicht erreichbar; "
                                "das Limit gilt vorerst je Worker-Prozess.")
        except Exception:  # noqa: BLE001 — Mongo ist gerade weg, der Alarm darf nichts brechen
            pass

    async def _check_mongo(self, key: str) -> bool:
        """Gleitendes Fenster ueber zwei feste Zeitfenster (atomar per $inc,
        ein Dokument je Limiter/Schluessel/Fenster; TTL raeumt alte weg).

        Pruefung 14.09.2026 (Liste 4, Nr. 73): Ein rein festes Fenster liess
        kurz vor und kurz nach dem Fensterwechsel fast das Doppelte durch.
        Jetzt zaehlt das vorige Fenster anteilig mit (Naeherung des gleitenden
        Fensters, wie sie Cloudflare/nginx verwenden)."""
        import time as _t
        n_jetzt = await self._zaehle_mongo(key)
        if n_jetzt > self.max_attempts:
            return False
        from deps import db
        fenster = int(_t.time() // self.window_seconds)
        vorher = await db.rate_limits.find_one(
            {"_id": f"{self.name}:{key}:{fenster - 1}"}, {"n": 1})
        n_vorher = int((vorher or {}).get("n") or 0)
        if not n_vorher:
            return True
        anteil = 1.0 - (_t.time() % self.window_seconds) / self.window_seconds
        return n_jetzt + n_vorher * anteil <= self.max_attempts

    async def _zaehle_mongo(self, key: str) -> int:
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
        return int(doc["n"])

    async def zaehlen(self, key: str) -> int:
        """Kontonummer (13.09.2026): wie check(), liefert aber den Zaehlerstand
        NACH dem Zaehlen (atomar — genau ein Aufrufer sieht jeden Wert).
        Schalter und Loopback-Ausnahme pruefen die Aufrufer selbst."""
        try:
            return await self._zaehle_mongo(key)
        except Exception:
            now = time.monotonic()
            cutoff = now - self.window_seconds
            with self._lock:
                fresh = [t for t in self._buckets[key] if t > cutoff]
                fresh.append(now)
                self._buckets[key] = fresh
                self._maybe_gc(cutoff)
                return len(fresh)

    async def stand(self, key: str) -> int:
        """Kontonummer (13.09.2026): Zaehlung LESEN, ohne zu zaehlen.
        Runde 15 (15.09.2026): wie check() ueber das aktuelle UND das vorige
        Fenster (anteilig) — vorher zaehlte nur das aktuelle feste Fenster,
        und kurz vor/nach dem Fensterwechsel gingen fast doppelt so viele
        Fehlversuche durch, wie die Sperre "30 je 15 Minuten" verspricht.
        Mongo; Rueckfall auf den lokalen Zaehler (fail_closed: gesperrt)."""
        if not _RATE_LIMIT_ENABLED:
            return 0
        try:
            from deps import db
            jetzt = time.time()
            fenster = int(jetzt // self.window_seconds)
            doc = await db.rate_limits.find_one(
                {"_id": f"{self.name}:{key}:{fenster}"}, {"n": 1})
            n_jetzt = int((doc or {}).get("n", 0))
            vorher = await db.rate_limits.find_one(
                {"_id": f"{self.name}:{key}:{fenster - 1}"}, {"n": 1})
            n_vorher = int((vorher or {}).get("n", 0))
            if not n_vorher:
                return n_jetzt
            anteil = 1.0 - (jetzt % self.window_seconds) / self.window_seconds
            return int(n_jetzt + n_vorher * anteil + 0.999999)
        except Exception:
            if self.fail_closed:
                logging.getLogger("rate_limiter").exception(
                    "Limiter %s: Stand nicht lesbar — fail-closed", self.name)
                return self.max_attempts
            cutoff = time.monotonic() - self.window_seconds
            with self._lock:
                return len([t for t in self._buckets.get(key, []) if t > cutoff])

    def _lokale_grenze(self) -> int:
        """B-15: je Prozess nur der Anteil am Limit (mindestens 1)."""
        return max(1, self.max_attempts // _WEB_CONCURRENCY)

    def _check_lokal(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            timestamps = self._buckets[key]
            # Drop timestamps outside the current window.
            fresh = [t for t in timestamps if t > cutoff]
            if len(fresh) >= self._lokale_grenze():
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
        """Zaehler eines Schluessels leeren (z.B. nach erfolgreichem Login).

        Audit 13.09.2026 (#47): exakte Schluessel statt Regex-Praefix. Die
        Kennung ist frei waehlbar (eine E-Mail wie "a|.|b@x.de" ist gueltig)
        und stand ungeschuetzt im Muster — "|" hob die Bindung an Limiter und
        Schluessel auf, "." passte auf alles: ein einziger Login leerte
        fremde Zaehler bis hin zur ganzen Sammlung rate_limits, ein "+" in
        der Adresse machte das Muster ungueltig (Reset wirkungslos).
        check() liest nur das aktuelle Fenster; die Nachbarfenster decken
        Uhrabweichungen zwischen den App-Servern ab, aeltere raeumt die TTL
        weg. Schluesselformat unveraendert (Mischbetrieb beim Rollout)."""
        with self._lock:
            self._buckets.pop(key, None)
        try:
            from deps import db
            fenster = int(time.time() // self.window_seconds)
            await db.rate_limits.delete_many(
                {"_id": {"$in": [f"{self.name}:{key}:{f}"
                                 for f in (fenster - 1, fenster, fenster + 1)]}})
        except Exception:
            pass


# Shared instances — imported directly by route modules.
# 10 attempts / 60 s per IP for the dealer/admin login.
login_limiter = SlidingWindowRateLimiter(max_attempts=10, window_seconds=60, name="login", fail_closed=True)

# Runde 26 (12.09.2026, Vorgabe Ahmad: kein Sucher bremst einen anderen aus):
# Der Login-Zaehler haengt am KONTO (IP + Kennung), nicht mehr allein an der
# IP — 30 Sucher im selben Buero teilten sich sonst 10 Versuche je Minute,
# und schon richtige Anmeldungen zaehlten mit. Zusaetzlich ein weit
# gefasstes Limit je IP, damit Rateversuche ueber viele Konten weiter
# gebremst werden (Standard 120/min, per LOGIN_IP_LIMIT einstellbar).
login_ip_limiter = SlidingWindowRateLimiter(
    max_attempts=int(os.environ.get("LOGIN_IP_LIMIT", "120") or 120),
    window_seconds=60, name="login-ip", fail_closed=True)


def login_schluessel(ip: str, kennung: str) -> str:
    """Zaehler-Schluessel je Konto UND IP ("1.2.3.4|name@firma.de").
    Kontonummer (13.09.2026): die Kennung laeuft ueber anmeldekennung —
    '10023 2', '10023/2' und '10023-2' zaehlen im selben Zaehler."""
    k = anmeldekennung(kennung or "")
    return f"{ip or 'unknown'}|{k}" if k else (ip or "unknown")


# =========================================================
#   KONTO-LIMITER (Kontonummer, 13.09.2026)
# =========================================================
# Fortlaufende Nummern sind erratbar — ueber viele IPs liesse sich ein
# Passwort gegen alle Nummern probieren (Spraying), IP+Kennung (10/min) und
# IP (120/min) bremsen das nicht. Deshalb zusaetzlich ein Zaehler je
# KENNUNG ohne IP, nur fuer Fehlversuche: ab LOGIN_KONTO_LIMIT (Standard 30)
# im Fenster LOGIN_KONTO_FENSTER (Standard 900 s) antwortet der Login 429 —
# gleicher Text und gleicher Weg fuer vorhandene und unbekannte Kennungen.
# Ausgenommen sind IPs, von denen sich dieses Konto schon erfolgreich
# angemeldet hat (bis zu 5 HMAC-Werte am Konto): ein Angreifer kann niemanden
# an seinem gewohnten Ort aussperren. LOGIN_KONTO_LIMIT=0 sperrt nie, der
# Betriebsalarm kommt trotzdem. Eine erfolgreiche Anmeldung leert den Zaehler
# NICHT (sonst gaebe jede Anmeldung des echten Nutzers einem Angreifer
# wieder volle Versuche) — er laeuft mit dem Fenster ab; vorher hebt nur der
# Betreiber die Sperre auf. "Warnen statt bremsen" betrifft Kosten- und
# Nutzungsgrenzen der Sucher, nicht Passwortraten.
from kontonummer import anmeldekennung  # noqa: E402


def _int_env(name: str, standard: int) -> int:
    try:
        return max(0, int((os.environ.get(name) or "").strip() or standard))
    except ValueError:
        return standard


_LOGIN_KONTO_LIMIT = _int_env("LOGIN_KONTO_LIMIT", 30)
_LOGIN_KONTO_FENSTER = _int_env("LOGIN_KONTO_FENSTER", 900) or 900
# Schwelle fuer den Alarm — auch mit LOGIN_KONTO_LIMIT=0 (nur Warnen).
_KONTO_ALARM_SCHWELLE = _LOGIN_KONTO_LIMIT or 30

login_konto_limiter = SlidingWindowRateLimiter(
    max_attempts=_KONTO_ALARM_SCHWELLE, window_seconds=_LOGIN_KONTO_FENSTER,
    name="login-konto", fail_closed=True)
# Bekannte IP: Sperre erst beim Dreifachen der Schwelle (Nachpruefung 15.09.2026).
_BEKANNTE_IP_FAKTOR = 3


def konto_gesperrt_text() -> str:
    minuten = max(1, round(login_konto_limiter.window_seconds / 60))
    return (f"Zu viele Fehlversuche für dieses Konto – bitte {minuten} Minuten "
            "warten oder den Betreiber kontaktieren.")


def ip_merkwert(ip: str) -> str:
    """HMAC der IP (kein Klartext am Konto), 16 Hex-Zeichen."""
    import hashlib
    import hmac
    from auth import JWT_SECRET
    # Runde 15: mit DATEN_SCHLUESSEL (falls gesetzt) statt JWT_SECRET — eine
    # JWT-Rotation loescht dann nicht alle bekannten IPs.
    geheim = (os.environ.get("DATEN_SCHLUESSEL") or "").strip() or str(JWT_SECRET)
    return hmac.new(geheim.encode(), (ip or "").encode(),
                    hashlib.sha256).hexdigest()[:16]


# Rollenpruefung 22.09.2026 (RP-557): "bekanntes Geraet". Ein Angreifer mit
# EINER IP konnte jede (fortlaufende) Kontonummer — auch den Betreiber — fuer
# alle NEUEN IPs sperren: 30 Fehlversuche je 15 Minuten halten die Sperre.
# Getroffen hat das vor allem Fahrer und Sucher im Mobilnetz (wechselnde IP).
# Jetzt zaehlt zusaetzlich zur IP ein zufaelliger Geraete-Schluessel, den das
# Geraet nach der ersten erfolgreichen Anmeldung bekommt und bei jeder
# Anmeldung mitschickt: am Konto liegt nur sein HMAC (hoechstens 5 Geraete).
# Ein bekanntes Geraet wird wie eine bekannte IP behandelt (Sperre erst beim
# Dreifachen der Schwelle). Den Schluessel kennt nur das Geraet selbst — ein
# Angreifer kann ihn weder erraten noch sich selbst "bekannt" machen, ohne das
# Passwort zu kennen.
GERAET_ID_MUSTER = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_BEKANNTE_GERAETE_MAX = 5


def geraet_id_gueltig(wert) -> Optional[str]:
    """Geraete-Schluessel aus der Anfrage oder None (Form falsch/fehlt)."""
    if isinstance(wert, str) and GERAET_ID_MUSTER.match(wert.strip()):
        return wert.strip()
    return None


def geraet_id_neu() -> str:
    import secrets
    return secrets.token_urlsafe(24)          # 32 Zeichen, passt ins Muster


def geraet_merkwert(geraet_id: str) -> str:
    """HMAC des Geraete-Schluessels (kein Klartext am Konto), 24 Hex-Zeichen.
    Eigener Praefix, damit der Wert nie mit einem IP-Merkwert kollidiert."""
    return ip_merkwert("geraet:" + (geraet_id or ""))[:16] + \
        ip_merkwert("geraet2:" + (geraet_id or ""))[:8]


async def konto_gesperrt(kennung: str, ip: str, konto=None,
                         geraet_id: Optional[str] = None) -> bool:
    """True = Anmeldung fuer diese Kennung von dieser IP vorerst gesperrt.
    VOR bcrypt rufen. Liest nur (zaehlt nicht)."""
    if not _RATE_LIMIT_ENABLED or _LOGIN_KONTO_LIMIT <= 0:
        return False
    if _EXEMPT_LOOPBACK and ip in _LOOPBACK_KEYS:
        return False
    k = anmeldekennung(kennung or "")
    if not k:
        return False
    stand = await login_konto_limiter.stand(k)
    bekannt = bool(konto and ip and ip_merkwert(ip) in (konto.get("login_ips_bekannt") or []))
    geraet = geraet_id_gueltig(geraet_id)
    if not bekannt and konto and geraet:
        bekannt = geraet_merkwert(geraet) in (konto.get("login_geraete_bekannt") or [])
    if bekannt:
        # Nachpruefung 15.09.2026 (Anmeldung Nr. 6): eine bekannte IP (Firmen-
        # NAT) entschaerft die Kontosperre, hebt sie aber nicht auf — ab dem
        # Dreifachen der Schwelle ist auch das eigene Netz gesperrt.
        # Rollenpruefung 22.09.2026 (RP-557): ebenso ein bekanntes Geraet.
        return stand >= _LOGIN_KONTO_LIMIT * _BEKANNTE_IP_FAKTOR
    return stand >= _LOGIN_KONTO_LIMIT


async def konto_fehlversuch(kennung: str, ip: str) -> None:
    """Fehlversuch fuer die Kennung zaehlen (auch unbekannte Kennungen —
    sonst verraet die Sperre, welche Nummern existieren). Beim ersten
    Erreichen der Schwelle im Fenster: Betriebsalarm. Wirft nie."""
    if not _RATE_LIMIT_ENABLED:
        return
    if _EXEMPT_LOOPBACK and ip in _LOOPBACK_KEYS:
        return
    k = anmeldekennung(kennung or "")
    if not k:
        return
    try:
        n = await login_konto_limiter.zaehlen(k)
        if n == _KONTO_ALARM_SCHWELLE:
            from betrieb import alarm
            from deps import db
            await alarm(db, "login_konto_angegriffen", ref=k[:40],
                        fehlversuche=n, fenster_sekunden=login_konto_limiter.window_seconds,
                        sperre_aktiv=_LOGIN_KONTO_LIMIT > 0)
    except Exception:
        logging.getLogger("rate_limiter").exception("Konto-Limiter: Zaehlen fehlgeschlagen")


async def bekannte_ip_merken(db, sammlung: str, konto_id: str, ip: str) -> None:
    """Nach vollstaendig erfolgreicher Anmeldung: HMAC der IP am Konto merken
    (hoechstens 5, aelteste fallen raus). Wirft nie."""
    if not konto_id or not ip or ip == "unknown":
        return
    try:
        wert = ip_merkwert(ip)
        await db[sammlung].update_one(
            {"id": konto_id, "login_ips_bekannt": {"$ne": wert}},
            {"$push": {"login_ips_bekannt": {"$each": [wert], "$slice": -5}}})
    except Exception:
        logging.getLogger("rate_limiter").exception("Konto-Limiter: IP nicht gemerkt")


async def bekanntes_geraet_merken(db, sammlung: str, konto_id: str,
                                  geraet_id: Optional[str]) -> Optional[str]:
    """Rollenpruefung 22.09.2026 (RP-557): nach vollstaendig erfolgreicher
    Anmeldung den Geraete-Schluessel am Konto merken (HMAC, hoechstens 5,
    aelteste fallen raus). Fehlt ein gueltiger Schluessel, wird einer
    erzeugt. Liefert den Schluessel fuer die Antwort (das Geraet legt ihn ab)
    oder None bei einem Fehler. Wirft nie."""
    if not konto_id:
        return None
    try:
        gid = geraet_id_gueltig(geraet_id) or geraet_id_neu()
        wert = geraet_merkwert(gid)
        await db[sammlung].update_one(
            {"id": konto_id, "login_geraete_bekannt": {"$ne": wert}},
            {"$push": {"login_geraete_bekannt": {
                "$each": [wert], "$slice": -_BEKANNTE_GERAETE_MAX}}})
        return gid
    except Exception:
        logging.getLogger("rate_limiter").exception("Konto-Limiter: Geraet nicht gemerkt")
        return None

# Slightly more lenient for the driver app (mobile clients can have flaky
# connectivity and may retry quickly), but still bounded.
driver_login_limiter = SlidingWindowRateLimiter(max_attempts=15, window_seconds=60,
                                                name="fahrer-login", fail_closed=True)

# Zugangs-Anfragen (/zugang-anfrage): 5 je IP und Stunde.
# Kontonummer (13.09.2026), Schritt 5: die Selbst-Registrierung und damit der
# eigene Zaehler der Fahrer-Registrierung (Runde 29) gibt es nicht mehr.
register_limiter = SlidingWindowRateLimiter(max_attempts=5, window_seconds=3600, name="registrierung", fail_closed=True)
