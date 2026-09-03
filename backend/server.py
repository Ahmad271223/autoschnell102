"""Autohändler SaaS – FastAPI bootstrap.

Alle Endpoints sind in modulare Router unter `/app/backend/routes/` ausgelagert:
  - routes/auth.py          →  /api/auth/*
  - routes/admin.py         →  /api/admin/*
  - routes/dealer.py        →  /api/dealer/*
  - routes/listings.py      →  /api/mobile/*, /api/snapshots/*, /api/vehicles/*, /api/listings/*
  - routes/contracts.py     →  /api/contracts/*
  - routes/appointments.py  →  /api/appointments/*
  - routes/drivers.py       →  /api/drivers/*, /api/driver/*
  - routes/payments.py      →  /api/payments/*  + /api/webhook/stripe

Geteilte Dependencies (DB-Client, current_user/admin, require_active_sub,
get_subscription_status, helpers) leben in `deps.py` und werden von allen
Routern importiert.
"""
import asyncio
import logging
from typing import Optional
from datetime import datetime, timedelta, timezone
import os
import sys
import uuid
from pathlib import Path

# Windows: SelectorEventLoop unterstützt keine Subprozesse (Playwright).
# ProactorEventLoop ist auf Windows die korrekte Wahl.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import traceback

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

from rate_limiter import SlidingWindowRateLimiter

from auth import hash_password
from cleanup_service import run_cleanup_forever
from listing_identity import ensure_cache_indexes
from snapshot_service import init_storage

# Shared deps (DB connection, helpers) — required for index/seed setup.
from deps import (client, db, kunden_nummern_nachziehen, log,
                  naechste_kunden_nr, now_iso)

# Modular routers.
from routes import admin as admin_routes
from routes import admin_auto_daten as admin_auto_daten_routes
from routes import appointments as appointments_routes
from routes import auth as auth_routes
from routes import bestand as bestand_routes
from routes import contracts as contracts_routes
from routes import dealer as dealer_routes
from routes import drivers as drivers_routes
from routes import listings as listings_routes
from routes import manual_search as manual_search_routes
from routes import payments as payments_routes
from routes import protocols as protocols_routes
from routes import resale as resale_routes
from routes import team as team_routes
from routes import marketplace as marketplace_routes

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
# Audit 09/2026 (Punkt 44): E-Mails, Token, Schluessel und Passwort-Fragmente
# werden in JEDER Log-Zeile maskiert (zentrale Redaktion).
from redaktion import logging_redaktion_aktivieren, redigieren  # noqa: E402
logging_redaktion_aktivieren()

# OpenAPI-Docs (/docs, /redoc, /openapi.json) legen die komplette API-Struktur
# offen — wertvoll fuer Angreifer-Recon. In Produktion deaktiviert; nur mit
# ENABLE_DOCS=true (z.B. lokal/Staging) eingeschaltet.
_DOCS_ENABLED = os.environ.get("ENABLE_DOCS", "").strip().lower() == "true"
from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def lifespan(_app):
    """FastAPI-Lifespan (Audit 09/2026, Punkt 52): ersetzt die veralteten
    on_event-Hooks; on_start/on_stop stehen weiter unten."""
    await on_start()
    try:
        yield
    finally:
        await on_stop()


app = FastAPI(
    title="Autohändler SaaS",
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
    lifespan=lifespan,
)
api = APIRouter(prefix="/api")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject standard security headers on every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        # Strict-Transport-Security: forces HTTPS for 1 year (production only).
        # Browsers that see this header will refuse plain-HTTP connections,
        # preventing token interception via network downgrade attacks.
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
        # Content-Security-Policy for API responses: nothing to render, so
        # lock down everything. This also prevents the API being embedded as
        # a frame and limits damage if a JSON response is somehow rendered.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'",
        )
        return response


class ErrorReportingMiddleware(BaseHTTPMiddleware):
    """Fängt unbehandelte Exceptions ab, speichert sie in error_logs (für den
    Admin-Bereich sichtbar) und liefert eine saubere JSON-500-Antwort — statt
    eines nackten 500 ohne CORS-Header, der im Browser als 'Network Error'
    erscheint."""

    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:
            err_id = str(uuid.uuid4())
            tb = redigieren(traceback.format_exc())
            log.exception("Unhandled error on %s %s (ref=%s)",
                          request.method, request.url.path, err_id[:8])
            try:
                await db.error_logs.insert_one({
                    "id": err_id,
                    "source": "backend",
                    "method": request.method,
                    "path": str(request.url.path)[:300],
                    "error_type": type(exc).__name__,
                    "message": redigieren(str(exc))[:1000],
                    "traceback": tb[-8000:],
                    "ip": (request.client.host if request.client else "") or "",
                    "status": "open",
                    "created_at": now_iso(),
                })
            except Exception:
                log.exception("error_logs write failed (ref=%s)", err_id[:8])
            return JSONResponse(
                status_code=500,
                content={"detail": "Interner Serverfehler — der Fehler wurde "
                                   "automatisch an den Administrator gemeldet "
                                   f"(Ref: {err_id[:8]})."},
            )


class WartungsmodusMiddleware(BaseHTTPMiddleware):
    """Restore/Wartung (Audit 09/2026, Punkt 5): ist `system_flags`
    {_id: "wartungsmodus", aktiv: true} gesetzt, antwortet die API mit 503
    — ausser Health/Ready. Der Wert wird 5 s gecacht (kein DB-Zugriff je
    Request)."""
    _stand = {"aktiv": False, "bis": 0.0}

    async def dispatch(self, request: Request, call_next) -> Response:
        pfad = request.url.path
        if pfad not in ("/api/health", "/api/ready", "/api/"):
            import time as _t
            if _t.monotonic() > self._stand["bis"]:
                try:
                    doc = await db.system_flags.find_one({"_id": "wartungsmodus"})
                    self._stand["aktiv"] = bool((doc or {}).get("aktiv"))
                except Exception:
                    pass
                self._stand["bis"] = _t.monotonic() + 5
            if self._stand["aktiv"]:
                return JSONResponse(status_code=503, headers={"Retry-After": "30"},
                                    content={"detail": "Wartungsmodus — die Plattform "
                                             "ist in wenigen Minuten wieder da."})
        return await call_next(request)


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(ErrorReportingMiddleware)
app.add_middleware(WartungsmodusMiddleware)


@api.get("/")
async def api_root():
    return {"service": "autohandel", "status": "ok"}


@api.get("/health")
async def health_check(response: Response):
    """Health-Check fuers Monitoring / automatischen Neustart.
    Prueft die DB-Verbindung real (ping) — meldet 503, wenn die
    Datenbank haengt, damit ein Watchdog eingreifen kann."""
    try:
        await db.command("ping")
        return {"status": "healthy", "db": "up"}
    except Exception as exc:
        log.warning("health check DB ping failed: %s", exc)
        response.status_code = 503
        return {"status": "unhealthy", "db": "down"}


@api.get("/ready")
async def readiness_check(response: Response):
    """Readiness (Audit 09/2026, Punkt 42) — getrennt von /health (Liveness).
    Nicht bereit (503): Datenbank, Migrationsstand, Speicherplatz oder
    Datei-Speicher fehlen. Warnungen (200): Backup-Alter, offene
    Betriebsalarme, haengende Jobs, S3 nicht erreichbar."""
    import shutil
    from migrationen import ZIEL_VERSION, aktuelle_version
    fehler, warnungen, info = [], [], {}
    try:
        await db.command("ping")
        info["db"] = "up"
    except Exception as exc:
        fehler.append(f"db: {exc}")
    try:
        v = await aktuelle_version(db)
        info["schema_version"] = v
        if v < ZIEL_VERSION:
            fehler.append(f"migration: Stand {v} < Ziel {ZIEL_VERSION}")
    except Exception as exc:
        fehler.append(f"migration: {exc}")
    for name, pfad in (("uploads", ROOT_DIR / "uploads"),
                       ("snapshots", ROOT_DIR / "local_storage"),
                       ("backups", Path(os.environ.get("BACKUP_DIR") or (ROOT_DIR / "backups")))):
        try:
            pfad.mkdir(parents=True, exist_ok=True)
            frei_mb = shutil.disk_usage(str(pfad)).free // (1024 * 1024)
            info[f"frei_mb_{name}"] = frei_mb
            if frei_mb < int(os.environ.get("MIN_FREI_MB", "500") or 500):
                fehler.append(f"{name}: nur {frei_mb} MB frei")
            probe = pfad / ".readiness"
            probe.write_text("ok")
            probe.unlink()
        except Exception as exc:
            fehler.append(f"{name}: nicht schreibbar ({exc})")
    if os.environ.get("S3_BUCKET"):
        try:
            from storage_service import storage
            head = getattr(storage, "erreichbar", None)
            if callable(head):
                ok = await asyncio.to_thread(head)
                if not ok:
                    warnungen.append("s3: nicht erreichbar")
        except Exception as exc:
            warnungen.append(f"s3: {exc}")
    try:
        from backup_service import letztes_backup_info
        b = letztes_backup_info()
        info["backup"] = b
        alter = b.get("alter_stunden")
        if alter is None or alter > 26:
            warnungen.append("backup: kein vollstaendiges Backup in den letzten 26 h")
    except Exception as exc:
        warnungen.append(f"backup: {exc}")
    try:
        n = await db.betriebsalarme.count_documents({"offen": True})
        info["alarme_offen"] = n
        if n:
            warnungen.append(f"{n} offene Betriebsalarme")
        alt = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        haengend = await db.link_jobs.count_documents(
            {"status": "queued", "created_at": {"$lt": datetime.now(timezone.utc) - timedelta(minutes=15)}})
        info["link_jobs_haengend"] = haengend
        if haengend:
            warnungen.append(f"{haengend} Link-Jobs warten > 15 min")
        wm = await db.system_flags.find_one({"_id": "wartungsmodus"})
        info["wartungsmodus"] = bool((wm or {}).get("aktiv"))
        _ = alt
    except Exception as exc:
        warnungen.append(f"queue: {exc}")
    bereit = not fehler
    if not bereit:
        response.status_code = 503
    return {"ready": bereit, "fehler": fehler, "warnungen": warnungen, **info}


# ---------- Datei-Auslieferung (Storage-Abstraktion) ----------
# Fotos/Videos aus dem Fahrzeug-/Verkaufsmodul. Keys enthalten eine
# zufällige UUID (nicht erratbar) — Auslieferung erfolgt daher ohne Auth,
# damit <img src=...> im Frontend ohne Header-Tricks funktioniert.
#
# SENSIBLE Kategorien sind hier GESPERRT: Abhol-Protokolle und die darin
# eingebetteten HANDSCHRIFTLICHEN UNTERSCHRIFTEN (Prefix "protocol/")
# enthalten personenbezogene Daten und werden ausschliesslich über die
# authentifizierten Endpunkte mit Eigentümer-Prüfung ausgeliefert
# (/api/driver/appointments/{id}/protocol.pdf bzw. /api/protocols/{id}.pdf).
# pickup/: Schadenfotos aus Abholberichten zeigen fremde Fahrzeuge und
# gehoeren nicht oeffentlich ins Netz — Abruf nur noch authentifiziert
# ueber /api/pickup-fotos/{key} (Haendler der Firma oder deren Fahrer).
_PRIVATE_FILE_PREFIXES = ("protocol/", "pickup/")


# Audit 09/2026 (Punkt 45): nicht-oeffentliche Dateien (z.B. Fahrzeugfotos
# unter resale/) nur noch mit kurzlebiger Signatur (?exp=&sig=), Cache
# privat. Firmenlogos (logo/) bleiben oeffentlich. Uebergang: bis alle
# Aufrufer signierte Links erzeugen, kann die Pflicht per
# DATEI_SIGNATUR_PFLICHT=false ausgesetzt werden.
_DATEI_SIGNATUR_PFLICHT = os.environ.get("DATEI_SIGNATUR_PFLICHT", "true").strip().lower() \
    not in ("0", "false", "no")


@app.get("/api/files/{key:path}")
async def serve_file(key: str, exp: Optional[str] = None, sig: Optional[str] = None):
    from storage_service import guess_media_type, load_async, StorageError
    from dateien import signatur_gueltig, signatur_noetig
    if key.startswith(_PRIVATE_FILE_PREFIXES):
        return JSONResponse(status_code=404, content={"detail": "Datei nicht gefunden"})
    geschuetzt = signatur_noetig(key)
    if geschuetzt and _DATEI_SIGNATUR_PFLICHT and not signatur_gueltig(key, exp, sig):
        return JSONResponse(status_code=403, content={"detail": "Link abgelaufen oder ungültig"})
    try:
        # Heissester Pfad der App (jedes Foto/Video) — nie im Loop lesen.
        data = await load_async(key)
    except StorageError:
        return JSONResponse(status_code=404, content={"detail": "Datei nicht gefunden"})
    cache = "private, max-age=300" if geschuetzt else "public, max-age=86400"
    return Response(content=data, media_type=guess_media_type(key),
                    headers={"Cache-Control": cache})


# ---------- Frontend-Fehler-Meldung (landet im Admin-Bereich) ----------
# 20 Meldungen / 60 s pro IP — verhindert, dass ein kaputter Client (oder
# ein Angreifer) die error_logs-Collection flutet.
_client_error_limiter = SlidingWindowRateLimiter(max_attempts=20, window_seconds=60)


class ClientErrorIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    stack: str = Field(default="", max_length=8000)
    url: str = Field(default="", max_length=500)
    user_email: str = Field(default="", max_length=200)


@api.post("/client-errors")
async def report_client_error(body: ClientErrorIn, request: Request):
    ip = (request.client.host if request.client else None) or "unknown"
    if not await _client_error_limiter.check(ip):
        return {"ok": False}
    import hashlib
    pfad = body.url.split("?")[0].split("#")[0][:500]
    nachricht = redigieren(body.message)[:1000]
    # Audit 09/2026 (Punkt 30): Deduplizierung (gleiche Meldung + Pfad in 10
    # Minuten wird hochgezaehlt), globale Obergrenze, Redaktion von
    # E-Mail/Token; die Client-E-Mail ist unbestaetigt -> nur maskiert.
    hash_ = hashlib.sha256(f"{pfad}|{nachricht}".encode("utf-8")).hexdigest()[:24]
    frist = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    dup = await db.error_logs.find_one_and_update(
        {"hash": hash_, "created_at": {"$gte": frist}},
        {"$inc": {"anzahl": 1}, "$set": {"zuletzt": now_iso()}},
        projection={"_id": 0, "id": 1})
    if dup:
        return {"ok": True, "ref": dup["id"][:8], "dedup": True}
    maximum = int(os.environ.get("ERROR_LOG_MAX", "20000") or 20000)
    if await db.error_logs.estimated_document_count() >= maximum:
        return {"ok": False, "hinweis": "Fehlerarchiv voll"}
    err_id = str(uuid.uuid4())
    await db.error_logs.insert_one({
        "id": err_id,
        "source": "frontend",
        "method": "",
        "path": pfad,
        "error_type": "ClientError",
        "message": nachricht,
        "traceback": redigieren(body.stack)[:8000],
        "user_email": redigieren(body.user_email)[:200],
        "hash": hash_, "anzahl": 1,
        "ip": ip,
        "status": "open",
        "created_at": now_iso(),
    })
    return {"ok": True, "ref": err_id[:8]}


# =========================================================
#                  INDEX & SEED SETUP
# =========================================================
async def _unique_index_sicher(coll, feld: str) -> None:
    """Unique-Index nur anlegen, wenn keine Dubletten existieren (Runde 5).
    Vorher scheiterte die Anlage still, und die Eindeutigkeit (z.B. eine
    E-Mail = ein Konto) galt dann einfach nicht. In Produktion bricht der
    Start ab, sonst wird gewarnt — bereinigen mit scripts/dubletten_pruefen.py."""
    dubletten = await coll.aggregate([
        {"$match": {feld: {"$exists": True, "$ne": None}}},
        {"$group": {"_id": f"${feld}", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}}, {"$limit": 5}]).to_list(5)
    if dubletten:
        beispiele = ", ".join(str(d["_id"]) for d in dubletten)
        msg = (f"{coll.name}.{feld}: doppelte Werte vorhanden ({beispiele}) — "
               "Unique-Index NICHT angelegt. Bereinigen: "
               "python scripts/dubletten_pruefen.py")
        if os.environ.get("APP_ENV", "").strip().lower() == "production":
            log.error("Start ABGEBROCHEN: %s", msg)
            raise SystemExit(78)
        log.error("ensure_indexes: %s", msg)
        return
    await coll.create_index(feld, unique=True)


async def _kunden_nr_unique_index() -> None:
    """Eindeutigkeit der Kundennummer auch auf DB-Ebene (Backstop gegen
    Zaehler-Fehler). sparse: Firmen ohne Nummer (Migrationsmoment) stoeren
    nicht. Ein aelterer nicht-eindeutiger Index gleicher Form wird ersetzt."""
    dubletten = await db.dealers.aggregate([
        {"$match": {"kunden_nr": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": "$kunden_nr", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}}, {"$limit": 5}]).to_list(5)
    if dubletten:
        msg = ("dealers.kunden_nr: doppelte Kundennummern (%s) — Unique-Index "
               "NICHT angelegt, bitte bereinigen" %
               ", ".join(str(d["_id"]) for d in dubletten))
        if os.environ.get("APP_ENV", "").strip().lower() == "production":
            log.error("Start ABGEBROCHEN: %s", msg)
            raise SystemExit(78)
        log.error("ensure_indexes: %s", msg)
        return
    vorhandene = {i["name"]: i async for i in db.dealers.list_indexes()}
    alt = vorhandene.get("kunden_nr_1")
    if alt is not None and not alt.get("unique"):
        await db.dealers.drop_index("kunden_nr_1")
    if "kunden_nr_unique" not in vorhandene:
        await db.dealers.create_index("kunden_nr", unique=True, sparse=True,
                                      name="kunden_nr_unique")


async def ensure_indexes():
    await _unique_index_sicher(db.users, "email")
    await _unique_index_sicher(db.dealers, "user_id")
    await db.vehicle_cache.create_index("mobile_ad_id", unique=True)
    # Genau EIN aktuelles Abholprotokoll je Termin (Race-Schutz: zwei
    # parallele Entwurf-Anlagen koennen sonst zwei "aktuelle" Versionen
    # erzeugen). Berichte: je Termin darf jede Versionsnummer nur einmal
    # existieren — der Verlierer eines Rennens bekommt DuplicateKey und
    # wiederholt mit frisch gelesener Version.
    await db.pickup_protocols.create_index(
        "appointment_id", unique=True,
        partialFilterExpression={"superseded": False},
        name="ein_aktuelles_protokoll_je_termin")
    await db.pickup_reports.create_index(
        [("appointment_id", 1), ("version", 1)], unique=True,
        name="berichtsversion_eindeutig")
    # Tagesbudget-Zaehler (provider_fetch) raeumen sich selbst weg.
    await db.provider_budget.create_index("ablauf", expireAfterSeconds=0)
    # TTL on cache (30 minutes)
    try:
        await db.vehicle_cache.create_index("expires_at_dt", expireAfterSeconds=0)
    except Exception as exc:
        log.warning("ensure_indexes: Index konnte nicht angelegt werden "
                       "— Eindeutigkeits-Garantie fehlt! %s", exc)
    # Vehicle comparisons – auto-cleanup after 14 days
    try:
        await db.vehicle_comparisons.create_index(
            "expires_at_dt", expireAfterSeconds=0,
        )
    except Exception as exc:
        log.warning("ensure_indexes: Index konnte nicht angelegt werden "
                       "— Eindeutigkeits-Garantie fehlt! %s", exc)
    await db.subscriptions.create_index("dealer_id")
    # Unique index on session_id prevents duplicate subscriptions from race
    # conditions between concurrent payment-status polls and webhook deliveries.
    # sparse=True because legacy subscriptions created before this field existed
    # will have no session_id — they must not block the index creation.
    try:
        await db.subscriptions.create_index(
            "session_id", unique=True, sparse=True,
        )
    except Exception as exc:
        log.warning("ensure_indexes: Index konnte nicht angelegt werden "
                       "— Eindeutigkeits-Garantie fehlt! %s", exc)
    await db.generated_pdfs.create_index([("dealer_id", 1), ("created_at", -1)])
    # Audit-Log + Fehler-Meldungen (Admin-Bereich)
    await db.activity_logs.create_index([("created_at", -1)])
    await db.activity_logs.create_index([("action", 1), ("created_at", -1)])
    await db.error_logs.create_index([("status", 1), ("created_at", -1)])
    # B2B-Modul
    await db.pickup_reports.create_index([("appointment_id", 1), ("version", -1)])
    await db.vehicles.create_index([("dealer_id", 1), ("lifecycle", 1)])
    # Fahrzeugpool-Begrenzung sortiert je Firma nach updated_at (09/2026)
    await db.vehicles.create_index([("dealer_id", 1), ("lifecycle", 1),
                                    ("updated_at", -1)])
    await db.resale_listings.create_index([("dealer_id", 1), ("status", 1)])
    # Marktplatz-Liste: sortiert nach published_at innerhalb der sichtbaren
    # Haendler — ohne diesen Index muesste Mongo den ganzen Bestand in den
    # Speicher sortieren (08/2026, nach Umstellung auf Aggregation).
    await db.resale_listings.create_index(
        [("status", 1), ("dealer_id", 1), ("published_at", -1)])
    # Haendlersuche filtert auf oeffentliche Profile.
    await db.dealers.create_index("marketplace.public")
    # Abo-Sammelabfragen (Admin-Nutzerliste, Sucherverwaltung): ohne diese
    # Indizes waere die Sammelabfrage langsamer als die alten Einzelabrufe.
    await db.subscriptions.create_index([("dealer_id", 1), ("created_at", -1)])
    await db.subscriptions.create_index(
        [("subject_user_id", 1), ("created_at", -1)])
    # Monatsstatistik je Sucher.
    await db.vehicle_comparisons.create_index(
        [("user_id", 1), ("created_at", -1)])
    await db.generated_pdfs.create_index([("user_id", 1), ("created_at", -1)])
    await db.resale_listings.create_index([("vehicle_id", 1)])
    # Phase 3: Marktplatz
    await db.dealer_invites.create_index("token", unique=True)
    await db.network_members.create_index(
        [("dealer_id", 1), ("buyer_user_id", 1)], unique=True)
    await db.listing_interest.create_index([("dealer_id", 1), ("created_at", -1)])
    await db.listing_interest.create_index([("buyer_user_id", 1), ("created_at", -1)])
    await db.appointments.create_index([("dealer_id", 1), ("pickup_date", 1)])
    await db.appointments.create_index([("driver_id", 1), ("pickup_date", 1)])
    # Neue Fahrer-Accounts + Dealer-Driver-Links
    await _unique_index_sicher(db.driver_accounts, "email")
    await _unique_index_sicher(db.driver_accounts, "driver_code")
    await db.dealer_drivers.create_index(
        [("dealer_id", 1), ("driver_account_id", 1)], unique=True,
    )
    await db.dealer_drivers.create_index("driver_account_id")
    # Single-Flight-Lease braucht Eindeutigkeit pro cache_key
    try:
        await db.listings_cache.create_index("cache_key", unique=True)
    except Exception as exc:
        log.warning("ensure_indexes: Index konnte nicht angelegt werden "
                       "— Eindeutigkeits-Garantie fehlt! %s", exc)
    # Snapshots: das Frontend pollt alle 4 s auf (id, dealer_id) — ohne Index
    # ist das ab ein paar tausend Snapshots ein Collection-Scan pro Poll.
    await db.listing_snapshots.create_index("id", unique=True)
    await db.listing_snapshots.create_index([("dealer_id", 1), ("created_at", -1)])
    await db.listing_snapshots.create_index([("vehicle_id", 1), ("status", 1)])
    await db.listing_snapshots.create_index([("status", 1), ("created_at", 1)])
    # Kundennummern (Wunsch 09/2026): Bestandsfirmen ohne Nummer bekommen
    # eine — idempotent je Firma ($exists-Guard; parallele Worker erzeugen
    # hoechstens Luecken, nie Dubletten), aelteste Firma zuerst.
    neu = await kunden_nummern_nachziehen()
    if neu:
        log.info("Kundennummern nachgezogen: %d Firmen", neu)
    await _kunden_nr_unique_index()
    # Auto-Daten (dauerhaft, anonym — auto_daten.py): eindeutige Zufalls-id,
    # Suche nach Marke/Modell, Filter; KEIN Index auf irgendeine Quell-ID,
    # weil es keine gibt. Vertraege: created_at fuer die 90-Tage-Loeschung.
    await db.admin_vehicle_data.create_index("id", unique=True)
    await db.admin_vehicle_data.create_index([("brand", 1), ("model", 1)])
    await db.admin_vehicle_data.create_index("purchase_price_cents")
    await db.generated_pdfs.create_index("created_at")
    # Passwort-Reset-Tokens: Lookup + automatisches Wegräumen
    await db.password_resets.create_index("token_hash")
    # Runde 5: automatisches Wegraeumen war nur versprochen, nicht angelegt.
    await db.password_resets.create_index("loeschen_ab", expireAfterSeconds=0,
                                          name="ttl_loeschen_ab")
    # (N1, Review 09/2026) Frueher stand hier `db.drivers.drop()` bei JEDEM
    # Start — als "Legacy-Index entfernen" beschriftet, tatsaechlich ein
    # Collection-Drop. Die Migration ist laengst durch; ersatzlos gestrichen.




async def seed_admin():
    email = os.environ.get("ADMIN_EMAIL", "admin@autohandel.app")
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not password:
        log.warning(
            "seed_admin: ADMIN_PASSWORD is not set in the environment — "
            "admin account will NOT be seeded. Set ADMIN_PASSWORD in .env."
        )
        return
    existing = await db.users.find_one({"email": email})
    if existing:
        if existing.get("role") == "admin":
            # Runde 5: KEINE Reaktivierung — ein vom Super-Admin gesperrter
            # Admin wurde sonst bei jedem Neustart wieder entsperrt.
            pass
        else:
            # NIEMALS ein Fremdkonto hochstufen (PR-Review 09/2026): wer
            # die Admin-Mail zuerst registriert hatte, wuerde sonst nach
            # einer Fehlkonfiguration automatisch Admin.
            log.error("seed_admin: unter %s existiert bereits ein NORMALES "
                      "Konto (Rolle %s) — es wird NICHT zum Admin "
                      "hochgestuft. ADMIN_EMAIL in der .env aendern.",
                      email, existing.get("role"))
        return
    user_id = str(uuid.uuid4())
    dealer_id = str(uuid.uuid4())
    await db.users.insert_one({
        "id": user_id, "email": email,
        "password_hash": hash_password(password),
        "role": "admin", "active": True,
        "dealer_id": dealer_id,
        "current_session_id": None,
        "created_at": now_iso(),
    })
    await db.dealers.insert_one({
        "kunden_nr": await naechste_kunden_nr(),
        "id": dealer_id, "user_id": user_id,
        "company_name": "Autohandel Admin", "contact_person": "Admin",
        "phone": "", "email": email, "address": "", "zip_code": "", "city": "",
        "created_at": now_iso(),
    })
    await db.subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": dealer_id,
        "plan": "lifetime", "status": "active",
        "expires_at": None, "created_at": now_iso(),
    })


async def seed_super_admin():
    """Plattform-Super-Admin (Login per Benutzername).
    Konfigurierbar über env: SUPER_ADMIN_USERNAME / SUPER_ADMIN_PASSWORD.
    Beide Variablen MÜSSEN in der .env gesetzt sein — es gibt keine
    Fallback-Zugangsdaten mehr im Quellcode.
    """
    username = os.environ.get("SUPER_ADMIN_USERNAME", "")
    password = os.environ.get("SUPER_ADMIN_PASSWORD", "")
    if not username or not password:
        log.warning(
            "seed_super_admin: SUPER_ADMIN_USERNAME / SUPER_ADMIN_PASSWORD sind "
            "nicht gesetzt — Super-Admin wird nicht angelegt. In .env eintragen."
        )
        return
    placeholder_email = f"{username.lower()}@cashcar.local"
    existing = await db.users.find_one({"username": username})
    if existing:
        # Idempotent: Rolle/aktiv-Status sicherstellen, Passwort NICHT überschreiben.
        await db.users.update_one(
            {"id": existing["id"]},
            {"$set": {
                "role": "admin",
                "is_super_admin": True,
                # Runde 5: active wird NICHT angefasst (keine Reaktivierung
                # eines bewusst gesperrten Kontos beim Neustart).
                "username": username,
            }},
        )
        return
    user_id = str(uuid.uuid4())
    dealer_id = str(uuid.uuid4())
    await db.users.insert_one({
        "id": user_id,
        "username": username,
        "email": placeholder_email,
        "password_hash": hash_password(password),
        "role": "admin",
        "is_super_admin": True,
        "active": True,
        "dealer_id": dealer_id,
        "current_session_id": None,
        "created_at": now_iso(),
        "company_name": "Cash Car Hannover (Super-Admin)",
    })
    await db.dealers.insert_one({
        "kunden_nr": await naechste_kunden_nr(),
        "id": dealer_id, "user_id": user_id,
        "company_name": "Cash Car Hannover", "contact_person": "Super Admin",
        "phone": "", "email": placeholder_email, "address": "", "zip_code": "",
        "city": "Hannover", "created_at": now_iso(),
    })
    await db.subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": dealer_id,
        "plan": "lifetime", "status": "active",
        "expires_at": None, "created_at": now_iso(),
    })
    log.info("seed_super_admin: created %s", username)


async def on_start():
    # Robustheit: Nach einem PC-/Server-Neustart braucht MongoDB manchmal ein
    # paar Sekunden. Wir warten geduldig, statt den Backend-Prozess sterben zu
    # lassen — so läuft die App zuverlässig hoch, "sobald das Backend startet".
    import asyncio as _asyncio
    # Produktions-Check WIRKLICH zuerst (Runde 5): vorher liefen Indexanlage
    # und Admin-Seeding bereits, bevor eine fehlerhafte Produktions-
    # konfiguration den Start abbrach — die Datenbank war dann schon
    # veraendert.
    try:
        from production_check import pruefe_produktion
        pruefe_produktion(log)
    except SystemExit:
        raise
    except Exception as exc:
        log.warning("production check failed to run: %s", exc)
    # Audit 09/2026 (Punkt 18): GENAU EIN Prozess legt Indizes/Seeds an und
    # fuehrt die versionierten Migrationen aus; die anderen warten auf den
    # Zielstand. In Produktion bricht ein Fehler den Start ab.
    from migrationen import ausfuehren_oder_warten
    for attempt in range(1, 31):
        try:
            await db.command("ping")
            break
        except Exception as exc:
            if attempt >= 30:
                log.error("MongoDB nach 60s nicht bereit: %s", exc)
                if os.environ.get("APP_ENV", "").strip().lower() == "production":
                    raise SystemExit(78)
            else:
                log.warning("Warte auf MongoDB (%d/30): %s", attempt, exc)
                await _asyncio.sleep(2)
    ergebnis = await ausfuehren_oder_warten(
        db, indexe=_alle_indexe, seeds=(seed_admin, seed_super_admin))
    log.info("Migration/Indizes: %s", ergebnis)
    # Object storage for listing snapshots (PDF + PNG proof archives).
    # Non-fatal if EMERGENT_LLM_KEY missing — snapshot endpoints will 503.
    try:
        init_storage()
    except Exception as exc:
        log.warning("snapshot storage init failed at startup: %s", exc)
    # Playwright Symlink self-heal — Kubernetes-Restarts verlieren
    # gelegentlich den Versions-Symlink. Wir legen ihn beim Boot neu an.
    try:
        from snapshot_service import _ensure_browser_executable
        _ensure_browser_executable()
    except Exception as exc:
        log.warning("playwright self-heal at startup failed: %s", exc)
    # Job-Sperren-Index SYNCHRON anlegen, BEVOR irgendein Hintergrundjob
    # startet — sonst koennten beim allerersten Start (frische Datenbank)
    # mehrere Worker denselben Job uebernehmen, weil der Unique-Index
    # noch fehlt (Snapshot-Recovery startet schon nach 5 Sekunden).
    try:
        from job_lock import ensure_lock_index
        await ensure_lock_index(db)
    except Exception as exc:
        log.warning("job lock index setup failed: %s", exc)
    try:
        from provider_limiter import ensure_slot_indexes
        await ensure_slot_indexes(db)
    except Exception as exc:
        log.warning("provider slot index setup failed: %s", exc)
    # Linkpruefungs-Jobs: Indizes synchron, dann die Job-Schleife dieses
    # Workers starten (Details in link_jobs.py).
    try:
        from link_jobs import ensure_job_indexes, run_job_worker_forever
        await ensure_job_indexes(db)
        import asyncio
        asyncio.create_task(run_job_worker_forever(db))
    except Exception as exc:
        log.warning("link job worker start failed: %s", exc)
    # Cleanup-Loop für Assets nach Abholung (7d) bzw. Nicht-Abholung (14d).
    try:
        import asyncio
        asyncio.create_task(run_cleanup_forever(db))
    except Exception as exc:
        log.warning("cleanup task start failed: %s", exc)
    try:
        asyncio.create_task(run_abgleich_forever())
    except Exception as exc:
        log.warning("abgleich task start failed: %s", exc)
    # Tägliches Backup (03:00, MongoDB + Datei-Speicher, 14 Tage Rotation).
    # Läuft im Backend selbst — kein OS-Scheduler nötig; holt beim Start
    # nach, wenn das letzte Backup älter als 24h ist.
    try:
        from backup_service import run_backup_forever
        asyncio.create_task(run_backup_forever(db))
    except Exception as exc:
        log.warning("backup task start failed: %s", exc)
    # Snapshot Self-Heal: Beim Boot alle Snapshots, die in pending/running
    # hängen geblieben sind (z.B. weil das Backend während eines Jobs
    # neu gestartet wurde), erneut anstoßen. Sonst würde das Frontend
    # ewig „lade…" anzeigen.
    try:
        import asyncio
        asyncio.create_task(_resume_stuck_snapshots())
    except Exception as exc:
        log.warning("snapshot resume task failed: %s", exc)


async def _alle_indexe():
    """Bestehende Indizes + Audit-Indizes (Punkt 36: Protokollversion eindeutig;
    Abo-Vorgaenge/Zahlungen idempotent; Alarme; Fehler-Dedup)."""
    await ensure_indexes()
    try:
        await ensure_cache_indexes(db)
    except Exception as exc:
        log.warning("listings_cache index setup failed: %s", exc)
    try:
        await db.pickup_protocols.create_index(
            [("appointment_id", 1), ("version", 1)], unique=True,
            name="protokollversion_eindeutig")
    except Exception as exc:
        log.error("Index protokollversion_eindeutig: %s", exc)
        if os.environ.get("APP_ENV", "").strip().lower() == "production":
            raise
    await db.subscriptions.create_index([("subject_user_id", 1), ("status", 1), ("created_at", -1)])
    await db.manual_payments.create_index("vorgang_id", unique=True, sparse=True,
                                          name="zahlung_je_vorgang")
    await db.abo_vorgaenge.create_index([("status", 1), ("updated_at", 1)])
    await db.betriebsalarme.create_index([("offen", 1), ("created_at", -1)])
    await db.betriebsalarme.create_index([("typ", 1), ("ref", 1), ("offen", 1)])
    await db.error_logs.create_index([("hash", 1), ("created_at", -1)])
    await db.zugangs_aenderungen.create_index([("subject_user_id", 1), ("created_at", -1)])
    await db.payment_transactions.create_index([("status", 1), ("updated_at", 1)])
    await db.storage_delete_retry.create_index("aufgegeben")


async def run_abgleich_forever():
    """Alle 10 Minuten (ein Prozess): abgebrochene Freischaltungs-Vorgaenge
    nachholen, bezahlte Transaktionen ohne Zugang erneut aktivieren."""
    import asyncio
    from job_lock import acquire
    await asyncio.sleep(20)
    while True:
        try:
            if await acquire(db, "abgleich", ttl_seconds=540):
                from routes.admin import abo_vorgaenge_nachholen
                from routes.payments import zahlungen_abgleichen
                a = await abo_vorgaenge_nachholen()
                z = await zahlungen_abgleichen(db)
                if a or (isinstance(z, dict) and any(z.values())):
                    log.info("Abgleich: abo_vorgaenge=%s zahlungen=%s", a, z)
        except Exception as exc:
            log.warning("Abgleich fehlgeschlagen: %s", exc)
        await asyncio.sleep(600)


async def _resume_stuck_snapshots():
    """Findet Snapshots in pending/running und startet sie sequentiell neu.

    Wir machen das sequentiell (eins nach dem anderen), damit der frisch
    gestartete Backend nicht direkt unter Last steht. Snapshots, die schon
    älter als 1 Stunde sind, markieren wir als failed (vermutlich wirklich
    kaputt — wir wollen die nicht endlos wiederholen).
    """
    import asyncio
    from datetime import datetime, timedelta, timezone
    from snapshot_service import run_snapshot_job

    # 5 Sekunden warten, bis der Webserver wirklich oben ist
    await asyncio.sleep(5)

    # Bei mehreren Worker-Prozessen stoesst nur EINER die haengenden
    # Snapshots neu an — sonst laeuft jeder Job achtfach.
    try:
        from job_lock import acquire
        if not await acquire(db, "snapshot-resume-boot", ttl_seconds=600):
            return
    except Exception:
        pass

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    too_old = await db.listing_snapshots.update_many(
        {"status": {"$in": ["pending", "running"]},
         "created_at": {"$lt": cutoff}},
        {"$set": {"status": "failed",
                  "error": "Backend-Neustart — Job verloren",
                  "completed_at": now_iso()}},
    )
    if too_old.modified_count:
        log.info("snapshot resume: marked %d stale jobs as failed",
                 too_old.modified_count)

    stuck = await db.listing_snapshots.find(
        {"status": {"$in": ["pending", "running"]}},
        {"_id": 0, "id": 1},
    ).to_list(50)
    if not stuck:
        return
    log.info("snapshot resume: re-running %d stuck job(s)", len(stuck))
    for s in stuck:
        try:
            await run_snapshot_job(db, s["id"])
        except Exception as exc:
            log.warning("snapshot resume %s failed: %s", s["id"], exc)


async def on_stop():
    client.close()


# =========================================================
#                       MOUNT
# =========================================================
api.include_router(auth_routes.router)
api.include_router(admin_routes.router)
api.include_router(admin_auto_daten_routes.router)
api.include_router(dealer_routes.router)
api.include_router(contracts_routes.router)
api.include_router(appointments_routes.router)
api.include_router(drivers_routes.router)
api.include_router(listings_routes.router)
api.include_router(manual_search_routes.router)
api.include_router(payments_routes.router)
api.include_router(bestand_routes.router)
api.include_router(resale_routes.router)
api.include_router(team_routes.router)
api.include_router(marketplace_routes.router)
api.include_router(protocols_routes.router)

app.include_router(api)

# Stripe-Webhook ist direkt an `app` gemountet (vollständiger Pfad inkl.
# /api-Prefix wird von Stripe so aufgerufen).
app.post("/api/webhook/stripe")(payments_routes.stripe_webhook)

_cors_raw = os.environ.get("CORS_ORIGINS", "").strip()
if not _cors_raw:
    log.warning(
        "CORS_ORIGINS is not set — defaulting to http://localhost:3000. "
        "Set CORS_ORIGINS in .env for production (e.g. https://app.yourdomain.com)."
    )
    _cors_raw = "http://localhost:3000"
_cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=_cors_origins,
    allow_methods=["*"], allow_headers=["*"],
)
