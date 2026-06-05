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

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

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
from routes import contracts as contracts_routes
from routes import dealer as dealer_routes
from routes import drivers as drivers_routes
from routes import listings as listings_routes
from routes import manual_search as manual_search_routes
from routes import payments as payments_routes

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


app.add_middleware(SecurityHeadersMiddleware)


@api.get("/")
async def api_root():
    return {"service": "autohandel", "status": "ok"}


# =========================================================
#                  INDEX & SEED SETUP
# =========================================================
async def ensure_indexes():
    await db.users.create_index("email", unique=True)
    await db.dealers.create_index("user_id", unique=True)
    await db.vehicle_cache.create_index("mobile_ad_id", unique=True)
    # TTL on cache (30 minutes)
    try:
        await db.vehicle_cache.create_index("expires_at_dt", expireAfterSeconds=0)
    except Exception:
        pass
    # Vehicle comparisons – auto-cleanup after 14 days
    try:
        await db.vehicle_comparisons.create_index(
            "expires_at_dt", expireAfterSeconds=0,
        )
    except Exception:
        pass
    await db.subscriptions.create_index("dealer_id")
    # Unique index on session_id prevents duplicate subscriptions from race
    # conditions between concurrent payment-status polls and webhook deliveries.
    # sparse=True because legacy subscriptions created before this field existed
    # will have no session_id — they must not block the index creation.
    try:
        await db.subscriptions.create_index(
            "session_id", unique=True, sparse=True,
        )
    except Exception:
        pass
    await db.generated_pdfs.create_index([("dealer_id", 1), ("created_at", -1)])
    await db.appointments.create_index([("dealer_id", 1), ("pickup_date", 1)])
    await db.appointments.create_index([("driver_id", 1), ("pickup_date", 1)])
    # Neue Fahrer-Accounts + Dealer-Driver-Links
    await db.driver_accounts.create_index("email", unique=True)
    await db.driver_accounts.create_index("driver_code", unique=True)
    await db.dealer_drivers.create_index(
        [("dealer_id", 1), ("driver_account_id", 1)], unique=True,
    )
    await db.dealer_drivers.create_index("driver_account_id")
    # Legacy-Index entfernen, falls noch vorhanden
    try:
        await db.drivers.drop()
    except Exception:
        pass


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
        # ensure role admin & lifetime
        await db.users.update_one(
            {"email": email},
            {"$set": {"role": "admin", "active": True}},
        )
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
    await ensure_indexes()
    await seed_admin()
    await seed_super_admin()
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
    # Cleanup-Loop für Assets nach Abholung (7d) bzw. Nicht-Abholung (14d).
    try:
        import asyncio
        asyncio.create_task(run_cleanup_forever(db))
    except Exception as exc:
        log.warning("cleanup task start failed: %s", exc)
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
