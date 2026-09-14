"""Autohändler SaaS – FastAPI bootstrap.

Alle Endpoints sind in modulare Router unter `/app/backend/routes/` ausgelagert:
  - routes/auth.py          →  /api/auth/*
  - routes/admin.py         →  /api/admin/*
  - routes/dealer.py        →  /api/dealer/*
  - routes/listings.py      →  /api/mobile/*, /api/snapshots/*, /api/vehicles/*, /api/listings/*
  - routes/contracts.py     →  /api/contracts/*
  - routes/appointments.py  →  /api/appointments/*
  - routes/drivers.py       →  /api/drivers/*, /api/driver/*

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
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

from rate_limiter import SlidingWindowRateLimiter

from auth import hash_password
from cleanup_service import run_cleanup_forever
from snapshot_service import init_storage

# Shared deps (DB connection, helpers) — required for index/seed setup.
from indizes import _termin_unique_index, _unique_index_sicher  # noqa: F401
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
from routes import protocols as protocols_routes
from routes import resale as resale_routes
from routes import team as team_routes
from routes import marketplace as marketplace_routes
from routes import beweise as beweise_routes

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


def _json_sicher(wert):
    """Nachpruefung Runde 14 (Nr. 80/95): Pydantic haengt an jeden
    Validierungsfehler das Eingabe-Echo. Enthaelt es Unendlich oder NaN
    (JSON-Token "Infinity"), scheitert die 422-Antwort selbst an
    json.dumps (allow_nan=False) und der Client sah 500 statt 422."""
    if isinstance(wert, float) and (wert != wert or wert in (float("inf"), float("-inf"))):
        return str(wert)
    if isinstance(wert, dict):
        return {k: _json_sicher(v) for k, v in wert.items()}
    if isinstance(wert, (list, tuple)):
        return [_json_sicher(v) for v in wert]
    if isinstance(wert, (str, int, bool)) or wert is None:
        return wert
    return str(wert)


@app.exception_handler(RequestValidationError)
async def _validierungsfehler(request: Request, exc: RequestValidationError):
    from fastapi.responses import JSONResponse
    fehler = []
    for e in exc.errors():
        e = dict(e)
        e.pop("url", None)
        if "ctx" in e:
            e["ctx"] = _json_sicher({k: (str(v) if isinstance(v, Exception) else v)
                                     for k, v in e["ctx"].items()})
        e["input"] = _json_sicher(e.get("input"))
        fehler.append(e)
    return JSONResponse(status_code=422, content={"detail": fehler})
# Runde 29 (12.09.2026, Pruefbefund): Zustand der kritischen Startteile.
# True = steht, False = fehlgeschlagen, gar nicht gesetzt = noch nicht
# versucht (z.B. Tests, die ensure_indexes nicht aufrufen). /ready macht
# aus False einen FEHLER — der Load Balancer nimmt die Instanz dann nicht
# in die Rotation, statt kaputte Antworten an Besucher zu liefern.
BETRIEBSBEREIT: dict = {}
# Pruefung 14.09.2026 (Liste 1, Nr. 9-12): Hintergrundjobs (Beweis-Worker,
# Aufraeumen, Zahlungsabgleich, Backup, Link-Jobs) laufen unter Aufsicht —
# ein Absturz startet den Job nach einer Pause neu, und /ready meldet einen
# Job, der nicht laeuft, als Fehler (vorher blieb der Prozess "ready", obwohl
# z.B. nie mehr aufgeraeumt oder gesichert wurde).
WORKER_STATUS: dict = {}


def _worker_starten(name: str, fabrik) -> None:
    import asyncio as _asyncio

    async def _laufen():
        neustarts = 0
        while True:
            WORKER_STATUS[name] = {"laeuft": True, "neustarts": neustarts,
                                   "letzter_fehler": None, "seit": now_iso()}
            try:
                await fabrik()
                WORKER_STATUS[name] = {"laeuft": False, "neustarts": neustarts,
                                       "letzter_fehler": "Schleife beendet", "seit": now_iso()}
                return
            except _asyncio.CancelledError:
                WORKER_STATUS[name] = {"laeuft": False, "neustarts": neustarts,
                                       "letzter_fehler": "abgebrochen", "seit": now_iso()}
                raise
            except Exception as exc:  # noqa: BLE001
                neustarts += 1
                WORKER_STATUS[name] = {"laeuft": False, "neustarts": neustarts,
                                       "letzter_fehler": str(exc)[:300], "seit": now_iso()}
                log.exception("Hintergrundjob %s abgestuerzt (Neustart %d)", name, neustarts)
                try:
                    from betrieb import alarm
                    await alarm(db, "hintergrundjob_abgestuerzt", ref=name,
                                fehler=str(exc)[:300], neustarts=neustarts)
                except Exception:  # noqa: BLE001
                    pass
                await _asyncio.sleep(min(300, 10 * (2 ** min(neustarts, 5))))

    _asyncio.create_task(_laufen())

api = APIRouter(prefix="/api")


# Runde 31 (12.09.2026): Fassungs-Stempel "<Commit-Zeit>-<Kurz-SHA>" aus
# deploy/rollout.sh. Die Oberflaeche liest ihn aus jeder API-Antwort und
# erfaehrt so von einer neuen Fassung — ohne eigene Abfrage und bevor sie
# gegen eine fehlende Datei laeuft (Vorfall Fahrer-App/Super-Admin).
APP_FASSUNG = os.environ.get("APP_FASSUNG", "").strip()


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
        if APP_FASSUNG:
            response.headers.setdefault("X-AH-Fassung", APP_FASSUNG)
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


_PROZESS_START = datetime.now(timezone.utc)


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
        # Pruefbericht 09/2026 (roter Befund): hier wurde eine Methode
        # gesucht, die es nirgends gab. Fehlte sie, wurde der Datei-Speicher
        # KOMMENTARLOS uebersprungen — die Bereitschaftspruefung meldete
        # "bereit", obwohl R2 unerreichbar sein konnte. Jetzt gibt es die
        # Methode, ihr Ergebnis steht in der Antwort, und ein Fehlen faellt
        # als Warnung auf statt still zu verschwinden.
        try:
            from storage_service import storage
            head = getattr(storage, "erreichbar", None)
            if not callable(head):
                info["s3"] = "ungeprueft"
                warnungen.append("s3: keine Erreichbarkeitspruefung vorhanden")
            else:
                ok = await asyncio.to_thread(head)
                info["s3"] = "up" if ok else "nicht erreichbar"
                if not ok:
                    warnungen.append("s3: nicht erreichbar")
                    # Runde 8, Befund 5 — bewusste Entscheidung: Ein Ausfall
                    # des Datei-Speichers nimmt den Server NICHT aus dem
                    # Betrieb. Vertraege, Vergleiche, Termine und PDFs liegen
                    # in der Datenbank und funktionieren weiter; nur Fotos
                    # und Protokoll-Dateien haengen an R2. Ein 503 wuerde
                    # ALLES abschalten, um einen Teil zu schuetzen. Dafuer
                    # darf der Ausfall nicht stumm bleiben: Betriebsalarm
                    # (einmal je Ausfall, hochgezaehlt statt dupliziert).
                    try:
                        from betrieb import alarm
                        await alarm(db, "datei_speicher_nicht_erreichbar",
                                    ref=os.environ.get("S3_BUCKET", ""),
                                    hinweis="R2/S3 antwortet nicht. Foto-Upload und "
                                            "Datei-Auslieferung sind gestoert; alles "
                                            "andere laeuft. Zugangsdaten, Eimer und "
                                            "Netz pruefen.")
                    except Exception:               # noqa: BLE001
                        pass
        except Exception as exc:
            info["s3"] = "fehler"
            warnungen.append(f"s3: {exc}")
    try:
        from backup_service import letztes_backup_info_global
        b = await letztes_backup_info_global(db)
        info["backup"] = b
        alter = b.get("alter_stunden")
        # Runde 10: jung reicht nicht — vollstaendig und (wenn eingerichtet)
        # offsite muss es sein. Ein gerade fehlgeschlagener Lauf darf die
        # Warnung nicht unterdruecken.
        offsite_noetig = bool(os.environ.get("BACKUP_S3_BUCKET", "").strip())
        # Entscheidung Ahmad 14.09.2026: ein fehlendes oder zu altes Backup ist
        # in Produktion ein FEHLER — aber erst, wenn die Instanz laenger als
        # 26 h laeuft (frischer Stack, Rollout).
        _ist_prod = os.environ.get("APP_ENV", "").strip().lower() == "production"
        _laeuft_h = (datetime.now(timezone.utc) - _PROZESS_START).total_seconds() / 3600
        _ziel = fehler if (_ist_prod and _laeuft_h > 26) else warnungen
        if alter is None or alter > 26 or not b.get("vollstaendig"):
            _ziel.append("backup: kein vollstaendiges Backup in den letzten 26 h")
        elif offsite_noetig and not b.get("offsite"):
            _ziel.append("backup: letzte Sicherung ohne Offsite-Kopie")
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
        ohne_mfa = await db.users.count_documents(
            {"role": "admin", "is_super_admin": True, "active": {"$ne": False},
             "mfa.aktiv": {"$ne": True}})
        info["super_admins_ohne_mfa"] = ohne_mfa
        _ist_prod = os.environ.get("APP_ENV", "").strip().lower() == "production"
        # Entscheidung Ahmad 14.09.2026: Zwei-Faktor ist fuer den Super-Admin
        # Pflicht (MFA_PFLICHT=false nur fuer Testumgebungen).
        _mfa_pflicht = os.environ.get("MFA_PFLICHT", "true").strip().lower() not in (
            "0", "false", "nein", "no")
        if ohne_mfa:
            (fehler if (_ist_prod and _mfa_pflicht) else warnungen).append(
                f"{ohne_mfa} Super-Admin-Konto/Konten ohne Zwei-Faktor-Anmeldung")
        # Pruefung 14.09.2026 (F5/F6): gar kein aktives Betreiberkonto
        aktive_sa = await db.users.count_documents(
            {"role": "admin", "is_super_admin": True, "active": {"$ne": False}})
        info["super_admins"] = aktive_sa
        if aktive_sa == 0:
            (fehler if _ist_prod else warnungen).append(
                "kein aktives Super-Admin-Konto (SUPER_ADMIN_USERNAME/PASSWORD pruefen)")
        _ = alt
    except Exception as exc:
        warnungen.append(f"queue: {exc}")
    try:
        # Audit 13.09.2026 (#38): prozessunabhaengige Stau-Warnung — startet
        # flottenweit kein Beweis-Worker, bliebe /ready sonst gruen. Warnung,
        # kein Fehler (kein 503-Tor fuer rollout.sh).
        from beweis_service import beweise_haengend as _beweise_haengend
        beweise_stau = await _beweise_haengend(db, 15)
        info["beweise_haengend"] = beweise_stau
        if beweise_stau:
            warnungen.append(f"{beweise_stau} Beweisdokumente warten > 15 min")
    except Exception as exc:  # noqa: BLE001
        warnungen.append(f"beweise: {exc}")
    # Runde 29 (12.09.2026, Pruefbefund): Fehlt ein kritischer Unique-Index
    # oder laeuft der Link-Worker nicht, darf diese Instanz NICHT in die
    # Rotation. Vorher wurde das nur ins Protokoll geschrieben und der
    # Server bediente trotzdem Besucher.
    #
    # Gegenpruefung 12.09.2026 (schwerer Befund): Die Indizes werden LIVE
    # geprueft, nicht ueber einen Merker vom Start. Sonst meldete /ready nach
    # dem Bereinigen der Dubletten weiter 503 — und weil deploy/rollout.sh und
    # deploy/freigeben.sh genau diese Route abfragen, waere der Server nicht
    # mehr aus dem Drain zu holen gewesen, ohne alle Prozesse neu zu starten.
    # (Der Load Balancer prueft /api/health, nicht /ready — ein laufender
    # Server faellt dadurch also nicht aus der Rotation.)
    kritische_indizes = {
        "vehicles": ("dealer_id", "id"),
        "kaufvorgaenge": ("contract_id",),
    }
    steht = {}
    for sammlung, felder in kritische_indizes.items():
        try:
            vorhanden = await db[sammlung].index_information()
            steht[sammlung] = any(
                i.get("unique") and [f for f, _r in i["key"]] == list(felder)
                for i in vorhanden.values())
        except Exception as exc:  # noqa: BLE001
            warnungen.append(f"Index-Pruefung {sammlung}: {exc}")
            continue
        if not steht[sammlung]:
            fehler.append(
                f"Unique-Index {sammlung} ({', '.join(felder)}) fehlt — "
                "Dubletten sind moeglich. Bereinigen mit "
                "'python scripts/dubletten_pruefen.py', danach greift das "
                "sofort (kein Neustart noetig).")
    # Audit 13.09.2026 (#34): Die Anbieter-Begrenzung kann sich selbst heilen
    # (ein kurzer Primaerwechsel waehrend on_start liess diesen Prozess sonst
    # bis zum Neustart 503 melden, obwohl acquire_slot den Index laengst
    # nachgeholt hatte). Nur rollout.sh/freigeben.sh fragen /ready ab.
    if BETRIEBSBEREIT.get("anbieter_grenze") is False:
        try:
            from provider_limiter import ensure_slot_indexes
            await ensure_slot_indexes(db)
            BETRIEBSBEREIT["anbieter_grenze"] = True
        except Exception as exc:  # noqa: BLE001
            warnungen.append(f"anbieter_grenze: {exc}")
    # Prozesslokale Teile: sie koennen sich nicht selbst heilen, ein Neustart
    # dieses Prozesses ist der Weg.
    _TEILE = {"link_worker": "Link-Abruf-Arbeiter",
              "anbieter_grenze": "Anbieter-Begrenzung (provider_limiter)"}
    for schluessel, klartext in _TEILE.items():
        if BETRIEBSBEREIT.get(schluessel) is False:
            fehler.append(f"{klartext} ist beim Start gescheitert — "
                          "Protokoll pruefen und diesen Prozess neu starten")
    # Pruefung 14.09.2026 (Liste 1, Nr. 9-12): ein Hintergrundjob, der nicht
    # laeuft (Absturz, wartet auf Neustart), macht die Instanz nicht bereit.
    for name, st in WORKER_STATUS.items():
        if not st.get("laeuft"):
            fehler.append(f"Hintergrundjob {name} laeuft nicht "
                          f"({st.get('letzter_fehler') or 'unbekannt'}; Neustarts: "
                          f"{st.get('neustarts', 0)})")
    info["hintergrundjobs"] = dict(WORKER_STATUS)
    info["betriebsbereit"] = {**BETRIEBSBEREIT, **{f"index_{k}": v
                                                   for k, v in steht.items()}}
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
# Runde 13: B4 — in Produktion gilt die Pflicht IMMER; der Schalter kann sie
# nur noch ausserhalb (Entwicklung/Test) aussetzen. production_check bricht
# den Start bei =false in Produktion ohnehin ab — das hier ist der zweite Riegel.
_DATEI_SIGNATUR_PFLICHT = (
    os.environ.get("APP_ENV", "").strip().lower() == "production"
    or os.environ.get("DATEI_SIGNATUR_PFLICHT", "true").strip().lower() not in ("0", "false", "no"))


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


# ---------- Bild-Proxy fuer Inseratsfotos (10.09.2026) ----------
# 300 Bilder / 60 s je IP: eine Vergleichsseite laedt hoechstens 10-40.
# Runde 26 (12.09.2026): 30 Sucher im selben Buero teilen sich EINE oeffentliche
# IP, und ein Vergleich laedt bis zu 40 Vorschaubilder — mit 300/min bekamen
# spaetere Nutzer 429 und sahen Fahrzeuge ohne Bild. Die Adresse ist beim
# signierten Bild-Link die einzige Kennung (kein Token im <img>-Tag), deshalb
# bleibt es ein IP-Limit, aber mit realistischem Wert (BILD_PROXY_LIMIT).
_bild_limiter = SlidingWindowRateLimiter(
    max_attempts=int(os.environ.get("BILD_PROXY_LIMIT", "1500") or 1500),
    window_seconds=60, name="bild_proxy")


@app.get("/api/bild")
async def bild_proxy_route(request: Request, u: str = "", exp: Optional[str] = None,
                           sig: Optional[str] = None):
    """Verkleinertes Vorschaubild eines Portal-Fotos (bild_proxy.py). Nur mit
    gueltiger Signatur und nur fuer die bekannten Portal-Hosts — kein
    offener Proxy."""
    import bild_proxy
    from rate_limiter import client_ip
    if not u or not bild_proxy.erlaubt(u):
        return JSONResponse(status_code=404, content={"detail": "Bild nicht verfügbar"})
    if not bild_proxy.gueltig(u, exp, sig):
        return JSONResponse(status_code=403, content={"detail": "Link abgelaufen oder ungültig"})
    if not await _bild_limiter.check(client_ip(request)):
        return JSONResponse(status_code=429, content={"detail": "Zu viele Bildanfragen"})
    data = await bild_proxy.laden(u)
    if not data:
        return JSONResponse(status_code=404, content={"detail": "Bild nicht verfügbar"})
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=604800"})


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
    # Pruefbericht 09/2026: hier stand request.client.host statt der
    # proxy-bewussten client_ip(). Hinter nginx ist das IMMER die Adresse
    # des Proxys. Das war nicht nur im Archiv unbrauchbar — alle Nutzer
    # teilten sich dadurch EINEN Zaehler, ein einziger kaputter Browser
    # haette die Fehlermeldung fuer alle anderen blockiert.
    from rate_limiter import client_ip
    ip = client_ip(request)
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


async def _bestand_lese_indizes() -> None:
    """Audit 13.09.2026 (#53/#48), Nachbesserung: Lese-Indizes fuer den
    Bestand. Beide sind NICHT unique — Altdaten koennen den Aufbau also nicht
    verhindern. Scheitert einer trotzdem (z.B. gleicher Schluessel unter
    anderem Namen), bricht der Start NICHT ab: Warnung + Betriebsalarm
    `index_fehlt`, die Abfragen laufen dann nur langsamer.

    - activity_logs (dealer_id, ref, created_at): Historie der Fahrzeugakte
      (routes/bestand.vehicle_akte: dealer_id + ref $in/$or, sortiert nach
      created_at). Ohne ihn lief Mongo den created_at-Index ueber alle Firmen
      rueckwaerts — je Aktenaufruf praktisch die ganze Sammlung.
    - vehicles archiv_aufraeumen_offen (partiell): stuendliche Nachhol-Abfrage
      der 50-Tage-Archivierung (cleanup_service), die kein dealer_id kennt.
    Bei sehr grosser activity_logs-Sammlung den Index vor dem Rollout bauen.
    Rumpf liegt in indizes.bestand_lese_indizes (testbar ohne server.py);
    server.db wird beim Aufruf gelesen."""
    from indizes import bestand_lese_indizes
    await bestand_lese_indizes(db)


async def _storage_retry_unique_index() -> None:
    """Nachpruefung Runde 14 (Nr. 61): Unique-Index `retry_je_ziel` auf
    storage_delete_retry samt Normalisieren und Zusammenlegen der Altzeilen.
    Audit 13.09.2026 (#41): Rumpf liegt in indizes.storage_retry_unique_index
    (Betriebsalarm bei Scheitern, deterministisches Zusammenlegen; testbar
    ohne server.py). server.db wird beim Aufruf gelesen."""
    from indizes import storage_retry_unique_index
    await storage_retry_unique_index(db)


async def _plan_requests_unique_indizes() -> None:
    """Nachpruefung Runde 14 (Nr. 56/57): hoechstens EINE offene Anfrage je
    Sucher bzw. je Firma als Teil-Unique-Index. Audit 13.09.2026 (#40): Rumpf
    liegt in indizes.plan_requests_unique_indizes (Betriebsalarm statt nur
    Log, Dublettenpruefung kann den Start nicht mehr abbrechen)."""
    from indizes import plan_requests_unique_indizes
    await plan_requests_unique_indizes(db)


async def ensure_indexes():
    # Kontonummer (13.09.2026), Schritt 5: users.email / driver_accounts.email
    # sind nur noch Kontaktadressen — die Eindeutigkeit (email_alt_eindeutig
    # aus Schritt 2 bzw. altes email_1) wird entfernt, kein neuer E-Mail-Index.
    from indizes import email_eindeutigkeit_entfernen
    await email_eindeutigkeit_entfernen(db)
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
    # Pruefung 14.09.2026 (Liste 3, Nr. 1): ein Vertrag je Idempotenz-Schluessel
    await db.generated_pdfs.create_index(
        [("dealer_id", 1), ("user_id", 1), ("idempotency_key", 1)], unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
        name="vertrag_idempotenz")
    # Pruefung 14.09.2026 (Liste 4, Nr. 79): SMTP-Idempotenz — ein Eintrag je
    # Schluessel (parallele Upserts), nach 30 Tagen automatisch weg.
    await db.mail_idempotenz.create_index("key", unique=True, name="mail_schluessel")
    # Pruefung 14.09.2026 (A5): Versand-Schluessel ueberleben die Verlaufsliste
    await db.versand_schluessel.create_index([("contract_id", 1), ("key", 1)], unique=True,
                                             name="versand_schluessel")
    await db.mail_idempotenz.create_index("begonnen", expireAfterSeconds=30 * 86400,
                                          name="mail_idempotenz_ttl")
    await db.pickup_reports.create_index(
        [("appointment_id", 1), ("version", 1)], unique=True,
        name="berichtsversion_eindeutig")
    # Runde 21: Fahrerfotos laufen FAHRERFOTO_TAGE nach dem Hochladen ab
    # (cleanup_service.berichtsfotos_nach_frist_loeschen sucht nach created_at).
    await db.pickup_reports.create_index([("created_at", 1)], name="bericht_erstellt")
    # Tagesbudget-Zaehler (provider_fetch) raeumen sich selbst weg.
    await db.provider_budget.create_index("ablauf", expireAfterSeconds=0)
    # TTL on cache (30 minutes)
    # Audit 13.09.2026 (#42): Scheitern meldet einen Betriebsalarm
    # ttl_index_fehlt (vorher nur eine Warnung mit falschem Text).
    from indizes import ttl_index_sicher
    await ttl_index_sicher(db, "vehicle_cache", "expires_at_dt")
    # Vehicle comparisons – auto-cleanup after 14 days
    await ttl_index_sicher(db, "vehicle_comparisons", "expires_at_dt")
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
        log.error("ensure_indexes: subscriptions.session_id konnte nicht angelegt werden "
                  "— Eindeutigkeits-Garantie fehlt! %s", exc)
        # Pruefung 14.09.2026 (Liste 1, Nr. 5): in Produktion kein Start ohne
        # den Schutz gegen doppelte Abos aus parallelen Freischaltungen.
        from indizes import _in_produktion_abbrechen
        _in_produktion_abbrechen(f"subscriptions.session_id: {exc}")
    # Nachpruefung Runde 14 (Nr. 60): zugang_grants ist der Idempotenz-
    # Schluessel der Stripe-Freischaltung (find_one_and_update mit upsert je
    # session_id). Ohne Unique-Index erzeugen parallele Upserts nachweislich
    # Dubletten (Abgleich startet eine haengende Aktivierung neu, waehrend
    # der alte Aufruf noch laeuft). Mongo wiederholt einen Upsert bei
    # DuplicateKey auf Gleichheitsfilter selbst — payments.py bleibt gleich.
    # Altbestand mit Dubletten wird NICHT automatisch geloescht (Geld-Belege):
    # dann bleibt es beim Warnhinweis, bereinigen mit scripts/dubletten_pruefen.py.
    try:
        await db.zugang_grants.create_index("session_id", unique=True,
                                            name="grant_je_session")
    except Exception as exc:
        log.error("ensure_indexes: zugang_grants.session_id (Dubletten im "
                  "Altbestand? scripts/dubletten_pruefen.py): %s", exc)
        # Pruefung 14.09.2026 (Liste 1, Nr. 6): in Produktion kein Start.
        from indizes import _in_produktion_abbrechen
        _in_produktion_abbrechen(f"zugang_grants.session_id: {exc}")
    await _storage_retry_unique_index()
    await _plan_requests_unique_indizes()
    await db.generated_pdfs.create_index([("dealer_id", 1), ("created_at", -1)])
    # WhatsApp-Download-Link (09.09.2026): Token -> Vertrag, nur fuer
    # Vertraege mit Freigabe (partial), eindeutig.
    await db.generated_pdfs.create_index(
        "freigabe.token", unique=True, name="vertrag_freigabe_token",
        partialFilterExpression={"freigabe.token": {"$exists": True}})
    # Audit-Log + Fehler-Meldungen (Admin-Bereich)
    await db.activity_logs.create_index([("created_at", -1)])
    await db.activity_logs.create_index([("action", 1), ("created_at", -1)])
    # Audit 13.09.2026 (#53/#48): Akte-Historie + Archiv-Nachholung
    await _bestand_lese_indizes()
    await db.error_logs.create_index([("status", 1), ("created_at", -1)])
    # B2B-Modul
    await db.pickup_reports.create_index([("appointment_id", 1), ("version", -1)])
    await db.vehicles.create_index([("dealer_id", 1), ("lifecycle", 1)])
    # Runde 16: Sucher-Bereich (owner_user_id) je Firma
    await db.vehicles.create_index([("dealer_id", 1), ("owner_user_id", 1)])
    await db.vehicles.create_index([("dealer_id", 1), ("mitbearbeiter_ids", 1)])
    # Runde 17: EIN Fahrzeugdokument je (Firma, Fahrzeug-ID) — zwei
    # gleichzeitige erste Vergleiche upserteten vorher zwei Dokumente.
    # Altdubletten: kein Startabbruch, sondern Betriebsalarm.
    # Runde 29 (12.09.2026, Pruefbefund): Stehen diese beiden Indizes nicht,
    # sind Dubletten moeglich (zwei Fahrzeugdokumente, zwei Vorgaenge je
    # Vertrag). Der Start bricht bewusst NICHT ab — sonst haette eine
    # Altdublette den ganzen Server unbedienbar gemacht. Stattdessen meldet
    # /ready einen FEHLER: der Load Balancer nimmt die Instanz gar nicht erst
    # in die Rotation, und die Ursache laesst sich in Ruhe beheben
    # (python scripts/dubletten_pruefen.py).
    BETRIEBSBEREIT["index_vehicles"] = await _unique_index_sicher(
        db.vehicles, ["dealer_id", "id"], abbruch_in_produktion=False)
    # Umbau Kaufvorgaenge 09.09.2026: ein Vorgang je Vertrag
    BETRIEBSBEREIT["index_kaufvorgaenge"] = await _unique_index_sicher(
        db.kaufvorgaenge, "contract_id", abbruch_in_produktion=False)
    await db.kaufvorgaenge.create_index([("dealer_id", 1), ("vehicle_id", 1)])
    await db.kaufvorgaenge.create_index([("dealer_id", 1), ("user_id", 1)])
    await db.kaufvorgaenge.create_index("appointment_id")
    # Runde 29 (12.09.2026): Fahrzeugstatus und Einkaufspreis fragen jetzt
    # gezielt nach Status + juengster Aenderung (statt 200/500 Vorgaenge zu
    # laden). Ohne diesen Index muesste Mongo dafuer sortieren.
    await db.kaufvorgaenge.create_index([("dealer_id", 1), ("vehicle_id", 1),
                                         ("status", 1), ("updated_at", -1)])
    # Nachschlagen per id ist der haeufigste Zugriff ueberhaupt (jede
    # Anmeldung, jede Berechtigungspruefung) — bisher ohne eigenen Index.
    await db.users.create_index("id", name="by_user_id")
    await db.dealers.create_index("id", name="by_dealer_id")
    await db.appointments.create_index([("dealer_id", 1), ("contract_id", 1)])
    # Runde 17: Vertragszeiger je Termin (idempotente Nachfuehrung beim PUT)
    await db.generated_pdfs.create_index([("dealer_id", 1), ("appointment_id", 1)])
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
    # Runde 27 (12.09.2026): heisse Abfrage des LIVE-Zaehlers — bei vielen
    # Suchern fragt die Vergleichsseite alle 30 s nach. Ohne diesen Index
    # scannt Mongo dafuer die ganze Sammlung.
    await db.vehicle_comparisons.create_index(
        [("cache_key", 1), ("created_at", -1)])
    # Runde 27 (Gegenpruefung): Die Fahrzeugakte zaehlt Vertraege und Termine
    # je Fahrzeug. Ohne diese Indizes muss Mongo dafuer jedes Vertrags-
    # dokument der Firma laden — samt eingebettetem PDF (mehrere hundert KB).
    await db.generated_pdfs.create_index([("dealer_id", 1), ("vehicle_id", 1)])
    await db.appointments.create_index([("dealer_id", 1), ("vehicle_id", 1)])
    # Bestandsliste sortiert nach lifecycle_changed_at (mit Limit).
    await db.vehicles.create_index([("dealer_id", 1), ("lifecycle_changed_at", -1)])
    await db.generated_pdfs.create_index([("user_id", 1), ("created_at", -1)])
    await db.resale_listings.create_index([("vehicle_id", 1)])
    # Phase 3: Marktplatz
    await db.dealer_invites.create_index("token", unique=True)
    await db.network_members.create_index(
        [("dealer_id", 1), ("buyer_user_id", 1)], unique=True)
    await db.listing_interest.create_index([("dealer_id", 1), ("created_at", -1)])
    await db.listing_interest.create_index([("buyer_user_id", 1), ("created_at", -1)])
    # Audit 13.09.2026 (#18/#19/#27): neue Eindeutigkeitsregeln im Marktplatz
    # (ein Merklisten-Eintrag, eine laufende Anfrage, eine offene Zugangs-
    # anfrage). Altdubletten werden vorher automatisch bereinigt; scheitert
    # es, Betriebsalarm statt Startabbruch (nicht in BETRIEBSBEREIT: die
    # Routen pruefen weiter selbst vor).
    from indizes import (_buyer_access_unique_index, _favoriten_unique_index,
                         _interesse_unique_index)
    await _favoriten_unique_index()
    await _interesse_unique_index()
    await _buyer_access_unique_index()
    # Audit 13.09.2026 (#24): offene Einladungen je Firma zaehlen und listen
    await db.dealer_invites.create_index([("dealer_id", 1), ("created_at", -1)])
    await db.appointments.create_index([("dealer_id", 1), ("pickup_date", 1)])
    await db.appointments.create_index([("driver_id", 1), ("pickup_date", 1)])
    await _termin_unique_index()
    # Fahrer-Accounts + Dealer-Driver-Links (E-Mail ohne Index, Kontonummer: konto_indizes)
    await _unique_index_sicher(db.driver_accounts, "driver_code")
    await db.dealer_drivers.create_index(
        [("dealer_id", 1), ("driver_account_id", 1)], unique=True,
    )
    await db.dealer_drivers.create_index("driver_account_id")
    # Single-Flight-Lease braucht Eindeutigkeit pro cache_key
    # Audit 13.09.2026 (#36): Cache-Dubletten werden automatisch
    # zusammengelegt; scheitert es trotzdem, Betriebsalarm statt Warnung.
    # Wirft nie (kein Startabbruch im Migrations-Leader).
    from indizes import listings_cache_unique_index
    await listings_cache_unique_index(db)
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
    # Kontonummer (13.09.2026): Teil-Unique-Index kontonummer_eindeutig und
    # kontonummer_basis in users/driver_accounts (nebenlaeufigkeitsfest).
    # KEIN Nachziehen von Nummern — Konten ohne Nummer werden nur gemeldet.
    from indizes import konto_indizes
    await konto_indizes(db)
    try:
        from kontenanlage import konten_ohne_nummer
        ohne = await konten_ohne_nummer(db)
        if ohne["users"] or ohne["driver_accounts"]:
            log.warning("Konten ohne Kontonummer: %d in users, %d Fahrer — sie "
                        "koennen sich kuenftig nicht mehr anmelden (keine "
                        "automatische Vergabe).", ohne["users"], ohne["driver_accounts"])
    except Exception as exc:
        log.warning("Zaehlung Konten ohne Kontonummer fehlgeschlagen: %s", exc)
    # Auto-Daten (dauerhaft, anonym — auto_daten.py): eindeutige Zufalls-id,
    # Suche nach Marke/Modell, Filter; KEIN Index auf irgendeine Quell-ID,
    # weil es keine gibt. Vertraege: created_at fuer die 90-Tage-Loeschung.
    await db.admin_vehicle_data.create_index("id", unique=True)
    await db.admin_vehicle_data.create_index([("brand", 1), ("model", 1)])
    await db.admin_vehicle_data.create_index("purchase_price_cents")
    await db.generated_pdfs.create_index("created_at")
    # (Kontonummer 13.09.2026, Schritt 5: keine password_resets-Indizes mehr —
    # ein neues Passwort setzt nur der Betreiber. Vorhandene Indizes bleiben
    # unangetastet; das Live-Reset leert die Sammlung.)
    # (N1, Review 09/2026) Frueher stand hier `db.drivers.drop()` bei JEDEM
    # Start — als "Legacy-Index entfernen" beschriftet, tatsaechlich ein
    # Collection-Drop. Die Migration ist laengst durch; ersatzlos gestrichen.




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
    # Kontonummer (13.09.2026): Ein Benutzername im Nummernmuster wuerde vom
    # Nummern-Zweig des Logins verdeckt — dann gar nicht erst anlegen.
    from kontonummer import kennung_normalisieren
    if kennung_normalisieren(username):
        log.error("seed_super_admin: SUPER_ADMIN_USERNAME %r sieht wie eine "
                  "Kontonummer, ein Kaeufer-Code oder eine Fahrer-ID aus — Super-Admin "
                  "wird NICHT angelegt. Bitte einen laengeren Benutzernamen mit "
                  "Kleinbuchstaben und Bindestrich waehlen.", username)
        return
    # Kontonummer (13.09.2026), Schritt 5: keine Platzhalter-E-Mail mehr fuer
    # neue Seeds (die E-Mail ist nur Kontaktadresse); ein vorhandenes Konto
    # bleibt unberuehrt.
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
        # Pruefung 14.09.2026 (Liste 1, Nr. 13): brach der erste Seed nach dem
        # Konto ab, fehlten Firmenprofil und Abo fuer immer — hier nachziehen.
        d_id = existing.get("dealer_id")
        if d_id and not await db.dealers.count_documents({"id": d_id}, limit=1):
            await db.dealers.insert_one({
                "kunden_nr": await naechste_kunden_nr(),
                "id": d_id, "user_id": existing["id"],
                "company_name": existing.get("company_name") or "Betreiber",
                "contact_person": "Super Admin", "phone": "", "email": "", "address": "",
                "zip_code": "", "city": "", "created_at": now_iso(),
            })
            log.warning("seed_super_admin: fehlendes Firmenprofil nachgezogen")
        if d_id and not await db.subscriptions.count_documents(
                {"dealer_id": d_id, "status": "active"}, limit=1):
            await db.subscriptions.insert_one({
                "id": str(uuid.uuid4()), "dealer_id": d_id,
                "plan": "lifetime", "status": "active",
                "expires_at": None, "created_at": now_iso(),
            })
            log.warning("seed_super_admin: fehlendes Abo nachgezogen")
        return
    user_id = str(uuid.uuid4())
    dealer_id = str(uuid.uuid4())
    await db.users.insert_one({
        "id": user_id,
        "username": username,
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
        "phone": "", "email": "", "address": "", "zip_code": "",
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
    # Go-Live 13.09.2026 (B4): eine unerwartete Exception der Pruefung (oder
    # ihres Imports) bricht in Produktion mit SystemExit(78) ab, statt nur zu
    # warnen und weiterzustarten; ausserhalb von Produktion nur ein Log.
    try:
        from production_check import produktionspruefung_beim_start
    except Exception as exc:
        log.error("production check not importable: %s", exc)
        if os.environ.get("APP_ENV", "").strip().lower() == "production":
            raise SystemExit(78)
    else:
        produktionspruefung_beim_start(log)
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
        db, indexe=_alle_indexe, seeds=(seed_super_admin,))
    log.info("Migration/Indizes: %s", ergebnis)
    # Object storage for listing snapshots (PDF + PNG proof archives).
    # Non-fatal if EMERGENT_LLM_KEY missing — snapshot endpoints will 503.
    try:
        init_storage()
    except Exception as exc:
        log.warning("snapshot storage init failed at startup: %s", exc)
    # Job-Sperren-Index SYNCHRON anlegen, BEVOR irgendein Hintergrundjob
    # startet — sonst koennten beim allerersten Start (frische Datenbank)
    # mehrere Worker denselben Job uebernehmen, weil der Unique-Index
    # noch fehlt (Snapshot-Recovery startet schon nach 5 Sekunden).
    from indizes import _in_produktion_abbrechen
    try:
        from job_lock import ensure_lock_index
        await ensure_lock_index(db)
    except Exception as exc:
        # Pruefung 14.09.2026 (Liste 1, Nr. 7/8): ohne Job-Sperre laufen
        # Aufraeumen, Backup und Zahlungsabgleich mehrfach — in Produktion
        # kein Start.
        log.error("job lock index setup failed: %s", exc)
        _in_produktion_abbrechen(f"job_locks.name: {exc}")
    try:
        from provider_limiter import ensure_slot_indexes
        await ensure_slot_indexes(db)
        BETRIEBSBEREIT["anbieter_grenze"] = True
    except Exception as exc:
        log.warning("provider slot index setup failed: %s", exc)
        BETRIEBSBEREIT["anbieter_grenze"] = False
        _in_produktion_abbrechen(f"provider_limits.provider: {exc}")
    # Linkpruefungs-Jobs: Indizes synchron, dann die Job-Schleife dieses
    # Workers starten (Details in link_jobs.py).
    try:
        from link_jobs import ensure_job_indexes, run_job_worker_forever
        await ensure_job_indexes(db)
        _worker_starten("link_jobs", lambda: run_job_worker_forever(db))
        BETRIEBSBEREIT["link_worker"] = True
    except Exception as exc:
        log.warning("link job worker start failed: %s", exc)
        BETRIEBSBEREIT["link_worker"] = False
    # Beweisdokumente je Inserat (ersetzt die Snapshots): Indizes synchron,
    # dann die Erzeugungs-Schleife dieses Workers (beweis_service.py).
    # Audit 13.09.2026 (#38): Indizes und Worker-Start getrennt. Vorher stand
    # beides in einem try — eine dauerhafte Index-Ursache in der gemeinsamen
    # DB (Dubletten, Namenskonflikt) liess nach jedem Rollout in KEINEM
    # Prozess einen Beweis-Worker starten. Der Worker braucht den Unique-Index
    # nicht (_beanspruchen ist ein atomarer Statuswechsel je Zeile);
    # beweis_indizes_sichern wirft nie und meldet einen Betriebsalarm.
    try:
        from beweis_service import beweis_indizes_sichern, run_beweis_worker_forever
        try:
            await beweis_indizes_sichern(db)
        except Exception as exc:
            log.error("Beweis-Indizes: %s — Beweis-Worker startet trotzdem", exc)
        _worker_starten("beweise", lambda: run_beweis_worker_forever(db))
    except Exception as exc:
        log.warning("beweis worker start failed: %s", exc)
        WORKER_STATUS["beweise"] = {"laeuft": False, "neustarts": 0, "letzter_fehler": str(exc)[:300]}
    # Cleanup-Loop für Assets nach Abholung (7d) bzw. Nicht-Abholung (14d).
    try:
        _worker_starten("aufraeumen", lambda: run_cleanup_forever(db))
    except Exception as exc:
        log.warning("cleanup task start failed: %s", exc)
        WORKER_STATUS["aufraeumen"] = {"laeuft": False, "neustarts": 0, "letzter_fehler": str(exc)[:300]}
    # 14.09.2026: kein Stripe mehr (Entscheidung Ahmad: Rechnung, Zahlung, dann
    # Zugangsdaten) — der Abgleich holt nur noch Abo-Vorgaenge nach.
    try:
        _worker_starten("abo_abgleich", lambda: run_abgleich_forever())
    except Exception as exc:
        log.warning("abgleich task start failed: %s", exc)
        WORKER_STATUS["abo_abgleich"] = {"laeuft": False, "neustarts": 0, "letzter_fehler": str(exc)[:300]}
    # Tägliches Backup (03:00, MongoDB + Datei-Speicher, 14 Tage Rotation).
    # Läuft im Backend selbst — kein OS-Scheduler nötig; holt beim Start
    # nach, wenn das letzte Backup älter als 24h ist.
    try:
        from backup_service import run_backup_forever
        _worker_starten("backup", lambda: run_backup_forever(db))
    except Exception as exc:
        log.warning("backup task start failed: %s", exc)
        WORKER_STATUS["backup"] = {"laeuft": False, "neustarts": 0, "letzter_fehler": str(exc)[:300]}


async def _alle_indexe():
    """Bestehende Indizes + Audit-Indizes (Punkt 36: Protokollversion eindeutig;
    Abo-Vorgaenge/Zahlungen idempotent; Alarme; Fehler-Dedup)."""
    await ensure_indexes()
    # Audit 13.09.2026 (#36): sichtbar im Betriebsstatus, nicht nur im Log;
    # der Alarm wird geschlossen, sobald die Indizes stehen. Wirft nie.
    from indizes import listings_cache_indizes
    await listings_cache_indizes(db)
    try:
        # Audit 13.09.2026 (#35): inseratscache_rotieren sucht nach expires_at
        # (stuendlich) — ohne Index ein Scan der ganzen Sammlung.
        await db.listings_cache.create_index("expires_at", name="cache_ablauf")
    except Exception as exc:
        log.error("Index cache_ablauf: %s", exc)
    try:
        # Nachbesserung #35: Altbestand (expires_at = Abruf + 1 Jahr) wird
        # ueber fetched_at geloescht — dieselbe Begruendung wie oben.
        await db.listings_cache.create_index("fetched_at", name="cache_abruf")
    except Exception as exc:
        log.error("Index cache_abruf: %s", exc)
    # Beweisdokumente: Unique-Index auf cache_key VOR allen Workern (ein
    # Dokument je Inserat haengt an ihm). Audit 13.09.2026 (#37): wirft nie,
    # fehlt der Index, gibt es einen Betriebsalarm.
    from beweis_service import beweis_indizes_sichern
    await beweis_indizes_sichern(db)
    try:
        await db.pickup_protocols.create_index(
            [("appointment_id", 1), ("version", 1)], unique=True,
            name="protokollversion_eindeutig")
    except Exception as exc:
        log.error("Index protokollversion_eindeutig: %s", exc)
        if os.environ.get("APP_ENV", "").strip().lower() == "production":
            raise
    try:
        # Gegenpruefung 12.09.2026: Der Zaehler "Freigaben" im Menue fragt alle
        # 20 s je offenem Tab — ohne Index durchsuchte das die ganze Sammlung.
        await db.pickup_protocols.create_index(
            [("dealer_id", 1), ("status", 1), ("abgeschickt_am", -1)],
            name="protokolle_je_firma_status")
    except Exception as exc:
        log.error("Index protokolle_je_firma_status: %s", exc)
    await db.subscriptions.create_index([("subject_user_id", 1), ("status", 1), ("created_at", -1)])
    # Audit 09/2026: "genau ein aktives Abo je Konto" gilt jetzt auch in
    # der Datenbank. Findet sich Altbestand mit mehreren aktiven Abos,
    # scheitert die Indexanlage — dann bleibt es bei der Pruefung im Code
    # und ein Betriebsalarm nennt die betroffenen Konten.
    # Audit 13.09.2026 (#39): Alarm bei JEDEM Scheitern, Schliessen nach der
    # Bereinigung, und die Dublettenpruefung kann den Start nicht abbrechen.
    from indizes import abo_unique_index
    await abo_unique_index(db)
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
                a = await abo_vorgaenge_nachholen()
                if a:
                    log.info("Abgleich: abo_vorgaenge=%s", a)
        except Exception as exc:
            log.warning("Abgleich fehlgeschlagen: %s", exc)
        await asyncio.sleep(600)


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
api.include_router(bestand_routes.router)
api.include_router(resale_routes.router)
api.include_router(team_routes.router)
api.include_router(marketplace_routes.router)
api.include_router(beweise_routes.router)
api.include_router(protocols_routes.router)

app.include_router(api)

# Stripe-Webhook ist direkt an `app` gemountet (vollständiger Pfad inkl.
# /api-Prefix wird von Stripe so aufgerufen).

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
    # Runde 10: kein Freibrief mehr — nur was die Oberflaeche wirklich nutzt.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With",
                   "Idempotency-Key", "X-CSRF-Token"],
)
