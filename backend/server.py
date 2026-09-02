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
from deps import client, db, log, now_iso

# Modular routers.
from routes import admin as admin_routes
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

# OpenAPI-Docs (/docs, /redoc, /openapi.json) legen die komplette API-Struktur
# offen — wertvoll fuer Angreifer-Recon. In Produktion deaktiviert; nur mit
# ENABLE_DOCS=true (z.B. lokal/Staging) eingeschaltet.
_DOCS_ENABLED = os.environ.get("ENABLE_DOCS", "").strip().lower() == "true"
app = FastAPI(
    title="Autohändler SaaS",
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
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
            tb = traceback.format_exc()
            log.exception("Unhandled error on %s %s (ref=%s)",
                          request.method, request.url.path, err_id[:8])
            try:
                await db.error_logs.insert_one({
                    "id": err_id,
                    "source": "backend",
                    "method": request.method,
                    "path": str(request.url.path)[:300],
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:1000],
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


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(ErrorReportingMiddleware)


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


@app.get("/api/files/{key:path}")
async def serve_file(key: str):
    from storage_service import guess_media_type, storage, StorageError
    if key.startswith(_PRIVATE_FILE_PREFIXES):
        return JSONResponse(status_code=404, content={"detail": "Datei nicht gefunden"})
    try:
        data = storage.load(key)
    except StorageError:
        return JSONResponse(status_code=404, content={"detail": "Datei nicht gefunden"})
    return Response(content=data, media_type=guess_media_type(key),
                    headers={"Cache-Control": "public, max-age=86400"})


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
    if not _client_error_limiter.check(ip):
        return {"ok": False}
    err_id = str(uuid.uuid4())
    await db.error_logs.insert_one({
        "id": err_id,
        "source": "frontend",
        "method": "",
        "path": body.url,
        "error_type": "ClientError",
        "message": body.message,
        "traceback": body.stack,
        "user_email": body.user_email,
        "ip": ip,
        "status": "open",
        "created_at": now_iso(),
    })
    return {"ok": True, "ref": err_id[:8]}


# =========================================================
#                  INDEX & SEED SETUP
# =========================================================
async def ensure_indexes():
    await db.users.create_index("email", unique=True)
    await db.dealers.create_index("user_id", unique=True)
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
    await db.driver_accounts.create_index("email", unique=True)
    await db.driver_accounts.create_index("driver_code", unique=True)
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
    # Passwort-Reset-Tokens: Lookup + automatisches Wegräumen
    await db.password_resets.create_index("token_hash")
    # Legacy-Index entfernen, falls noch vorhanden
    try:
        await db.drivers.drop()
    except Exception as exc:
        log.warning("ensure_indexes: Index konnte nicht angelegt werden "
                       "— Eindeutigkeits-Garantie fehlt! %s", exc)




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
            await db.users.update_one({"email": email},
                                      {"$set": {"active": True}})
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
                "active": True,
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


@app.on_event("startup")
async def on_start():
    # Robustheit: Nach einem PC-/Server-Neustart braucht MongoDB manchmal ein
    # paar Sekunden. Wir warten geduldig, statt den Backend-Prozess sterben zu
    # lassen — so läuft die App zuverlässig hoch, "sobald das Backend startet".
    import asyncio as _asyncio
    for attempt in range(1, 31):
        try:
            await ensure_indexes()
            break
        except Exception as exc:
            if attempt >= 30:
                log.error("MongoDB nach 60s nicht bereit — Indexe übersprungen: %s", exc)
            else:
                log.warning("Warte auf MongoDB (%d/30): %s", attempt, exc)
                await _asyncio.sleep(2)
    try:
        await seed_admin()
        await seed_super_admin()
    except Exception as exc:
        log.warning("Admin-Seed übersprungen (DB nicht bereit?): %s", exc)
    # B2B-Modul: Lebenszyklus-Status für Bestandsfahrzeuge nachziehen.
    try:
        from lifecycle import migrate_missing_lifecycles
        n = await migrate_missing_lifecycles()
        if n:
            log.info("lifecycle migration: %d Fahrzeuge migriert", n)
    except Exception as exc:
        log.warning("lifecycle migration failed: %s", exc)
    try:
        await ensure_cache_indexes(db)
    except Exception as exc:
        log.warning("listings_cache index setup failed: %s", exc)
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
    # Produktions-Check ZUERST: mit APP_ENV=production bricht der Start
    # bei Entwicklungswerten (Dev-Secret, Demo-Passwort, localhost-CORS,
    # Mongo ohne Auth) sofort und mit klarer Meldung ab.
    try:
        from production_check import pruefe_produktion
        pruefe_produktion(log)
    except SystemExit:
        raise
    except Exception as exc:
        log.warning("production check failed to run: %s", exc)
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


@app.on_event("shutdown")
async def on_stop():
    client.close()


# =========================================================
#                       MOUNT
# =========================================================
api.include_router(auth_routes.router)
api.include_router(admin_routes.router)
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
