"""Geteilte Abhängigkeiten und Helpers — wird von server.py UND routes/*
importiert. Vermeidet Zirkular-Imports.
"""
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorClient

from auth import decode_token

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# ---------- Logging ----------
log = logging.getLogger("autohandel")

# ---------- Mongo ----------
# Connection-Pool explizit dimensioniert fuer 200-500 gleichzeitige Nutzer.
# maxPoolSize: max. gleichzeitige Sockets pro Prozess; minPoolSize haelt
# warme Verbindungen vor (kein Cold-Start unter Last). Timeouts verhindern,
# dass ein langsamer DB-Call den Request unbegrenzt blockiert.
_MONGO_MAX_POOL = int(os.environ.get("MONGO_MAX_POOL_SIZE", "100"))
_MONGO_MIN_POOL = int(os.environ.get("MONGO_MIN_POOL_SIZE", "10"))
def _neuer_client() -> AsyncIOMotorClient:
    return AsyncIOMotorClient(
        os.environ["MONGO_URL"],
        maxPoolSize=_MONGO_MAX_POOL,
        minPoolSize=_MONGO_MIN_POOL,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=30000,
        retryWrites=True,
    )


class _MotorProxy:
    """Motor-Client je Event-Loop (Audit 09/2026, Testrobustheit): ein Motor-
    Client bindet sich an die erste Schleife, in der er benutzt wird. In-
    Prozess-Tests (asyncio.run je Test) liefen danach in "Event loop is
    closed". Der Proxy erzeugt den Client neu, sobald die gebundene Schleife
    geschlossen ist — im Serverbetrieb (eine Schleife je Worker) passiert
    das nie, es bleibt EIN Client mit EINEM Pool."""

    def __init__(self):
        self._client = None
        self._loop = None

    def _aktuell(self):
        try:
            loop = __import__("asyncio").get_running_loop()
        except RuntimeError:
            loop = None
        if self._client is None or (self._loop is not None and self._loop.is_closed()
                                    and loop is not None and loop is not self._loop):
            self._client = _neuer_client()
            self._loop = loop
        elif self._loop is None and loop is not None:
            self._loop = loop
        return self._client

    # Client-Schnittstelle
    def __getattr__(self, name):
        return getattr(self._aktuell(), name)

    def __getitem__(self, name):
        return self._aktuell()[name]

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None


class _DbProxy:
    def __init__(self, client_proxy, name):
        self._client_proxy = client_proxy
        self._name = name

    def _aktuell(self):
        return self._client_proxy._aktuell()[self._name]

    def __getattr__(self, name):
        return getattr(self._aktuell(), name)

    def __getitem__(self, name):
        return self._aktuell()[name]


client = _MotorProxy()
db = _DbProxy(client, os.environ["DB_NAME"])

# ---------- Auth ----------
bearer = HTTPBearer(auto_error=False)


# ---------- Helpers ----------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_doc(d: dict) -> dict:
    if not d:
        return d
    d.pop("_id", None)
    return d


# ---------- Auth dependencies ----------
async def current_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
    if not creds or not creds.credentials:
        raise HTTPException(401, "Nicht authentifiziert")
    try:
        payload = decode_token(creds.credentials)
    except Exception:
        raise HTTPException(401, "Token ungültig")
    if payload.get("typ") == "mfa":
        # Zwischen-Token der Zwei-Faktor-Anmeldung: nur fuer /auth/login/mfa
        raise HTTPException(401, "Zweiter Faktor fehlt — bitte Anmeldung abschliessen")
    user = await db.users.find_one({"id": payload.get("sub")},
                                   {"_id": 0, "password_hash": 0, "mfa.secret": 0,
                                    "mfa.pending_secret": 0, "mfa.wiederherstellung": 0})
    if user and user.get("role") == "sucher" and user.get("dealer_id"):
        # Review 09/2026: Sperrt der Admin den Haendler-Hauptaccount, blieben
        # dessen Sucher voll arbeitsfaehig. Gesperrter Chef = gesperrte Firma.
        # Runde 13: A8/A9 — die Regel steht jetzt EINMAL in firma_gesperrt
        # (Login, Marktplatz und Einladungen teilen sie sich).
        if await firma_gesperrt(user["dealer_id"]):
            raise HTTPException(403, "Die Firma ist gesperrt — bitte den "
                                     "Administrator kontaktieren.")
    if not user or not user.get("active"):
        raise HTTPException(401, "Account deaktiviert")
    # Single-session enforcement (strikt): Das Token-sid MUSS der aktuell
    # gespeicherten Session entsprechen. Ist keine Session gesetzt (z.B. nach
    # Logout oder bevor der Account je eingeloggt war), ist JEDES Token
    # ungültig — sonst wären nach einem Logout alle alten Tokens wieder
    # brauchbar und mehrere Geräte gleichzeitig möglich.
    if payload.get("sid") != user.get("current_session_id"):
        raise HTTPException(401, "Session beendet (anderes Gerät aktiv oder abgemeldet)")
    return user


async def firma_gesperrt(dealer_id: Optional[str]) -> bool:
    """Runde 13: A8/A9 — gesperrter Chef = gesperrte Firma, EINE Fassung der
    Regel fuer Login (current_user), oeffentlichen Marktplatz und
    Einladungen. Vorher pruefte nur current_user den Hauptaccount; der
    Marktplatz zeigte eine gesperrte Firma weiter, _redeem_invite legte
    weiter Mitgliedschaften an. Hauptaccount = aeltestes dealer-Konto je
    Firma (Runde 11); fehlendes active-Feld gilt wie bisher als aktiv."""
    if not dealer_id:
        return False
    chef = await db.users.find_one(
        {"dealer_id": dealer_id, "role": "dealer"},
        {"_id": 0, "active": 1}, sort=[("created_at", 1)])
    return chef is not None and not chef.get("active", True)


async def gesperrte_firmen_ids() -> set:
    """Runde 13: A8 — alle Firmen, deren Hauptaccount (aeltestes dealer-Konto)
    gesperrt ist, in EINER Abfrage — fuer die Listenfilter des Marktplatzes.
    Sperre = active explizit False (fehlendes Feld = aktiv, wie
    firma_gesperrt)."""
    rows = db.users.aggregate([
        {"$match": {"role": "dealer", "dealer_id": {"$nin": [None, ""]}}},
        {"$sort": {"created_at": 1}},
        {"$group": {"_id": "$dealer_id", "active": {"$first": "$active"}}},
        {"$match": {"active": False}},
    ])
    return {r["_id"] async for r in rows}


async def email_vergeben(email: str) -> Optional[str]:
    """Runde 13: B5 — Login-E-Mail plattformweit eindeutig ueber users UND
    driver_accounts. Vorher prueften alle Anlagepfade nur ihre eigene
    Sammlung: dieselbe Adresse konnte Firmen-/Sucher-/Kaeuferkonto UND
    Fahrerkonto sein, und der gemeinsame Passwort-Reset fand dann immer nur
    das users-Konto. Liefert "user", "driver" oder None (Adresse frei)."""
    muster = {"$regex": f"^{re.escape((email or '').strip())}$", "$options": "i"}
    if await db.users.find_one({"email": muster}, {"_id": 1}):
        return "user"
    if await db.driver_accounts.find_one({"email": muster}, {"_id": 1}):
        return "driver"
    return None


async def current_firma(user=Depends(current_user)):
    """Firmen-Routen (Termine, Vertraege, Fahrzeuge, Fahrer-Verwaltung,
    Haendler-Profil): NUR Chef und Sucher einer echten Firma.

    Wichtig gegen das Mandantenleck (PR-Review 09/2026): Zwischenhaendler
    (b2b_buyer) haben dealer_id=None — ohne diese Sperre teilten sich ALLE
    Kaeufer den "None-Mandanten" und konnten dort gegenseitig Termine
    samt Verkaeuferdaten anlegen und lesen."""
    if user.get("role") not in ("dealer", "sucher") or not user.get("dealer_id"):
        raise HTTPException(403, "Nur für Händler-Accounts (Chef und Sucher)")
    # Nachpruefung Runde 14 (Nr. 85): fail-closed, wenn das dealers-Dokument
    # fehlt (Absturzfenster bei der Anlage, manueller DB-Eingriff). Vorher
    # lieferte effective_dealer {} und Termine/Vertraege/Versand liefen mit
    # leerer Firmenidentitaet und Default-Regeln weiter. Eine indexierte
    # find_one je Firmen-Request.
    if not await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 1}):
        raise HTTPException(403, "Kein Händlerprofil — bitte den "
                                 "Administrator kontaktieren")
    return user


async def current_chef(user=Depends(current_firma)):
    """NUR der Haendler-Hauptaccount. Berechtigungsmatrix (PR-Review
    09/2026): destruktive und firmenweite Aktionen (Fahrerliste, fremde
    Vertraege/Termine loeschen, Netzwerk-Mitglieder) sind Chefsache;
    Sucher arbeiten in ihrem eigenen Bereich."""
    if user.get("role") != "dealer":
        raise HTTPException(403, "Nur der Händler-Hauptaccount darf das")
    return user


async def current_admin(user=Depends(current_user)):
    """Betreiber-Routen. Beschluss 06.09.2026 (Runde 12): Es gibt GENAU EINEN
    Betreiber — den Super-Admin. Die frueehere Zwischenstufe "normaler
    Admin" (lesen, aber nicht verwalten) ist abgeschafft; ein Konto mit
    Rolle admin ohne is_super_admin bekommt ueberall 403."""
    if user.get("role") != "admin" or not user.get("is_super_admin"):
        raise HTTPException(403, "Nur der Super-Admin (Betreiber) darf das")
    return user


async def current_super_admin(user=Depends(current_admin)):
    """NUR der Super-Admin (is_super_admin=true in der Datenbank).

    Ein normaler Admin reicht NICHT (PR-Review 09/2026): sonst koennte
    jeder Admin Rollen vergeben, Super-Admin-Passwoerter zuruecksetzen
    und damit die Plattform uebernehmen."""
    if not user.get("is_super_admin"):
        raise HTTPException(403, "Nur der Super-Admin darf das")
    return user


ABO_PLAENE_ERLAUBT = {"monthly", "yearly", "trial", "lifetime"}
# Zustaende, in denen ein Abo (noch) Zugang gewaehrt: gekuendigt laeuft bis
# zum Ablaufdatum weiter. Alles andere (ersetzt, expired, suspended,
# revoked, unbekannt) ist fail-closed inaktiv.
_ABO_STATUS_ZUGANG = ("active", "cancelled")


def _ablauf_parsen(wert):
    """ISO-String oder datetime -> zeitzonen-bewusstes datetime; naive Werte
    gelten als UTC (Audit 09/2026: vorher warf der Vergleich naiver Werte
    still eine Exception und das Abo blieb dauerhaft aktiv). None bei
    unbrauchbarem Wert."""
    if wert is None or wert == "":
        return None
    try:
        if isinstance(wert, datetime):
            ea = wert
        else:
            ea = datetime.fromisoformat(str(wert).strip().replace("Z", "+00:00"))
        if ea.tzinfo is None:
            ea = ea.replace(tzinfo=timezone.utc)
        return ea
    except Exception:
        return None


def sub_status_from_doc(sub) -> dict:
    """Abo-Status aus einem bereits geladenen Abo-Dokument berechnen.
    EINZIGE Stelle fuer diese Geschaeftsregel (Anzeige, Zugriff, Abrechnung).

    Fail-closed (Audit 09/2026):
    - Sperr-/Endzustand (auch bei lifetime) -> inaktiv
    - unbekannter Plan -> inaktiv
    - kein Ablaufdatum bei einem befristeten Plan -> inaktiv
    - unlesbares Ablaufdatum -> inaktiv (wird protokolliert)
    - naives Datum -> als UTC interpretiert (nie "ewig aktiv")
    """
    if not sub:
        return {"active": False, "plan": None, "expires_at": None, "status": "none"}
    plan = sub.get("plan")
    status_ = sub.get("status", "active") or "active"
    expires_at = sub.get("expires_at")
    out = {"active": False, "plan": plan, "expires_at": expires_at, "status": status_}
    if status_ not in _ABO_STATUS_ZUGANG:
        return out
    if plan not in ABO_PLAENE_ERLAUBT:
        log.error("Abo %s: unbekannter Plan %r -> inaktiv", sub.get("id"), plan)
        out["status"] = "ungueltig"
        return out
    if plan == "lifetime" and not expires_at:
        # Lifetime ohne gesetztes Ende: aktiv, solange der Status es erlaubt.
        # "Abo aufheben" setzt cancelled + expires_at=jetzt -> unten inaktiv.
        return {"active": True, "plan": "lifetime", "expires_at": None,
                "status": status_}
    if not expires_at:
        log.error("Abo %s (%s): kein Ablaufdatum -> inaktiv", sub.get("id"), plan)
        out["status"] = "ungueltig"
        return out
    ea = _ablauf_parsen(expires_at)
    if ea is None:
        log.error("Abo %s: unlesbares Ablaufdatum %r -> inaktiv", sub.get("id"), expires_at)
        out["status"] = "ungueltig"
        return out
    if ea < datetime.now(timezone.utc):
        out["status"] = "expired"
        return out
    out["active"] = True
    return out


async def get_subscription_status(dealer_id: str,
                                  subject_user_id: Optional[str] = None) -> dict:
    """Abo-Status. Ohne subject_user_id: Händler-Abo (Bestandslogik).
    Mit subject_user_id: das persönliche Abo eines Sucher-Unteraccounts."""
    if subject_user_id:
        # Abo-Historie bleibt erhalten (Audit 09/2026): ersetzte Zeilen
        # (status "ersetzt") zaehlen nicht, das juengste andere gilt.
        sub = await db.subscriptions.find_one(
            {"subject_user_id": subject_user_id, "status": {"$ne": "ersetzt"}},
            sort=[("created_at", -1)])
    else:
        sub = await db.subscriptions.find_one(
            {"dealer_id": dealer_id, "subject_user_id": {"$exists": False},
             "status": {"$ne": "ersetzt"}},
            sort=[("created_at", -1)])
        if not sub:
            # Fallback: alte Abos, bei denen das Feld explizit null ist.
            # WICHTIG: NICHT einfach irgendein Abo des Haendlers nehmen —
            # sonst wuerde das persoenliche Abo eines Suchers faelschlich
            # fuer den Chef zaehlen (Chef muss sein EIGENES Abo haben).
            sub = await db.subscriptions.find_one(
                {"dealer_id": dealer_id, "subject_user_id": None},
                sort=[("created_at", -1)])
    return sub_status_from_doc(sub)


async def subscription_for(user: dict) -> dict:
    """Abo-Status passend zur Rolle (Modell 08/2026):
    - Sucher: NUR das persönliche Abo (subject_user_id) zählt.
    - Händler-Hauptaccount: kostenlos fürs Verkaufen/Verwalten. Für die
      Sucher-Funktionen (Vergleich/Suche) zählt SEIN persönliches Abo
      ('Chef als eigener Sucher'); als Fallback bleibt das alte
      händlerweite Abo gültig (Bestandskunden/Lifetime verlieren nichts)."""
    if user.get("role") == "sucher":
        return await get_subscription_status(user.get("dealer_id", ""),
                                             subject_user_id=user["id"])
    personal = await get_subscription_status(user.get("dealer_id", ""),
                                             subject_user_id=user["id"])
    if personal.get("active"):
        return personal
    return await get_subscription_status(user.get("dealer_id", ""))


# ---------- Persönliche Einstellungs-Overrides (Sucher) ----------
# Der Chef füllt die Händler-Einstellungen vor; jeder Sucher darf sie FÜR
# SICH überschreiben (users.settings_override). Wirksam = Händler-Werte,
# überlagert von den eigenen. Chef-Werte bleiben unangetastet.
SUCHER_SETTINGS_FIELDS = {
    # Profil (erscheint auf den Verträgen des Suchers)
    "company_name", "contact_person", "phone", "whatsapp_number", "email",
    "address", "zip_code", "city", "logo_url", "opening_hours",
    # Vergleich
    "comparison_rules", "export_rules", "active_profile",
    # Versand
    "email_subject", "email_template", "whatsapp_template",
    # AGB & Vereinbarungen
    "default_terms", "default_special_agreements",
}


async def effective_dealer(user: dict) -> dict:
    """Händler-Dokument aus Sicht dieses Nutzers: für Sucher werden die
    persönlichen Overrides über die Chef-Vorgaben gelegt."""
    dealer = await db.dealers.find_one({"id": user.get("dealer_id")},
                                       {"_id": 0}) or {}
    if user.get("role") != "sucher":
        return dealer
    override = user.get("settings_override") or {}
    merged = dict(dealer)
    for k, v in override.items():
        if k in SUCHER_SETTINGS_FIELDS and v is not None:
            merged[k] = v
    return merged


async def require_active_sub(user=Depends(current_user)):
    # Strikte Rollentrennung: nur Händler-Hauptaccount und Sucher nutzen die
    # Sucher-Funktionen (Vergleich/Suche). Admin verwaltet nur; b2b_buyer
    # gehört auf den Marktplatz. Explizit per ROLLE sperren — nicht darauf
    # verlassen, dass diese Konten "zufällig" kein Abo haben.
    if user.get("role") not in ("dealer", "sucher"):
        raise HTTPException(403, "Diese Funktion ist Händler-/Sucher-Accounts "
                                 "vorbehalten.")
    sub = await subscription_for(user)
    if not sub["active"]:
        raise HTTPException(402, "Kein aktives Abo")
    return user


async def naechste_kunden_nr() -> int:
    """Fortlaufende Firmen-Kundennummer, automatisch und atomar vergeben.
    Start bei 1001 (4-stellig) — Wunsch 09/2026: der Betreiber muss nichts
    angeben und findet Firmen ueber die kurze Nummer wieder.

    Selbstheilung (Haertung 09/2026): haengt der Zaehler hinter dem Bestand
    (Restore ohne counters, alter Zaehler + neue Firmen), wird er auf die
    hoechste vergebene Nummer gehoben — es entsteht nie eine Dublette."""
    for _ in range(5):
        doc = await db.counters.find_one_and_update(
            {"_id": "kunden_nr"},
            {"$inc": {"seq": 1}, "$setOnInsert": {"start": 1000}},
            upsert=True, return_document=True)
        nr = 1000 + int(doc["seq"])
        if not await db.dealers.find_one({"kunden_nr": nr}, {"_id": 1}):
            return nr
        top = await db.dealers.find_one(
            {"kunden_nr": {"$type": "number"}}, {"kunden_nr": 1},
            sort=[("kunden_nr", -1)])
        if top:
            await db.counters.update_one(
                {"_id": "kunden_nr"},
                {"$max": {"seq": int(top["kunden_nr"]) - 1000}})
    raise RuntimeError("Kundennummer: kein freier Wert gefunden")


async def kunden_nummern_nachziehen() -> int:
    """Bestandsfirmen ohne Kundennummer nummerieren (aelteste zuerst).
    Idempotent je Firma ($exists-Guard): parallele Worker erzeugen
    hoechstens Luecken, nie Dubletten. Liefert die Zahl neuer Nummern."""
    n = 0
    async for d in db.dealers.find({"kunden_nr": {"$exists": False}},
                                   {"_id": 0, "id": 1}).sort("created_at", 1):
        r = await db.dealers.update_one(
            {"id": d["id"], "kunden_nr": {"$exists": False}},
            {"$set": {"kunden_nr": await naechste_kunden_nr()}})
        n += r.modified_count
    return n


# Termin-Zustaende, in denen eine Abholung noch AUSSTEHT (Gegenstueck zu
# ABGESCHLOSSEN in routes/appointments.py). Runde 15 (Nr. 6): Grundlage fuer
# die Regel "hoechstens ein offener Abholtermin je Fahrzeug" (Teil-Unique-
# Index in server.py, Vorabpruefung beim Anlegen, Auto-Termin beim Vertrag).
TERMIN_OFFEN = ("offen", "verschoben", "bestätigt", "in Bearbeitung")
# Fuer Abfragen: fehlender oder leerer Status zaehlt ebenfalls als offen.
TERMIN_OFFEN_WERTE = list(TERMIN_OFFEN) + ["", None]


# ---------------------------------------------------------------------
# Fahrzeug-Besitzer (Runde 16, Beschluss Ahmad 08.09.2026): Sucher sehen
# Fahrzeuge, Termine, Beweis-Snapshots, Abholberichte und Protokolle nur
# noch im EIGENEN Arbeitsbereich; der Chef sieht die ganze Firma.
#   vehicles.owner_user_id = Konto, das das Fahrzeug angelegt hat (erster
#   Vergleich bzw. manuelle Anlage). Der Chef haengt per
#   PUT /vehicles/{id}/besitzer um. Ein Sucher, der ein Inserat vergleicht,
#   das ein Kollege bereits fuehrt, bekommt das Ergebnis mit Hinweis, das
#   Fahrzeug bleibt beim Kollegen.
# Regel wie bei Vertraegen (_vertrag_bereich in routes/contracts.py): fehlt
# der Besitzer am Altdokument, sieht der Sucher es nicht (fail-closed); die
# Migration m4 (migrationen.py) ordnet den Altbestand nach Vertrag,
# Vergleich, Aktivitaet, Termin oder Chef zu.
# ---------------------------------------------------------------------
def ist_sucher(user) -> bool:
    return (user or {}).get("role") == "sucher"


def fahrzeug_bereich(user) -> Dict[str, Any]:
    """Mongo-Filter fuer vehicles: Chef = Firma, Sucher = eigene Fahrzeuge."""
    bereich: Dict[str, Any] = {"dealer_id": user["dealer_id"]}
    if ist_sucher(user):
        bereich["owner_user_id"] = user["id"]
    return bereich


async def eigene_fahrzeug_ids(user) -> Optional[List[str]]:
    """IDs der Fahrzeuge im Bereich des Kontos; None = alle (Chef)."""
    if not ist_sucher(user):
        return None
    return await db.vehicles.distinct(
        "id", {"dealer_id": user["dealer_id"], "owner_user_id": user["id"]})


async def fahrzeug_im_bereich(user, vehicle_id: Optional[str]) -> bool:
    if not vehicle_id:
        return False
    return await db.vehicles.count_documents(
        {"id": vehicle_id, **fahrzeug_bereich(user)}, limit=1) > 0


async def termin_bereich(user) -> Dict[str, Any]:
    """Mongo-Filter fuer appointments: Chef = Firma; Sucher = selbst angelegt
    ODER eigenes Fahrzeug ODER eigener Vertrag."""
    q: Dict[str, Any] = {"dealer_id": user["dealer_id"]}
    if ist_sucher(user):
        vids = await eigene_fahrzeug_ids(user) or []
        cids = await db.generated_pdfs.distinct(
            "id", {"dealer_id": user["dealer_id"], "user_id": user["id"]})
        q["$or"] = [{"created_by": user["id"]},
                    {"vehicle_id": {"$in": vids}},
                    {"contract_id": {"$in": cids}}]
    return q


async def termin_im_bereich(user, appt: dict) -> bool:
    """Einzelner, bereits geladener Termin (created_by, contract_id,
    vehicle_id) — dieselbe Regel wie termin_bereich."""
    if not ist_sucher(user):
        return True
    if appt.get("created_by") == user["id"]:
        return True
    cid = appt.get("contract_id")
    if cid and await db.generated_pdfs.count_documents(
            {"id": cid, "user_id": user["id"]}, limit=1):
        return True
    return await fahrzeug_im_bereich(user, appt.get("vehicle_id"))


async def besitzer_namen(dealer_id: str, ids) -> Dict[str, str]:
    """Konto-ID -> Anzeigename (Chef-Ansicht 'Bearbeiter')."""
    ids = [i for i in set(ids or []) if i]
    if not ids:
        return {}
    out: Dict[str, str] = {}
    konten = await db.users.find(
        {"id": {"$in": ids}, "dealer_id": dealer_id},
        {"_id": 0, "id": 1, "first_name": 1, "last_name": 1, "email": 1, "role": 1}).to_list(1000)
    for u in konten:
        name = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip()
        if not name:
            name = ("Händler-Hauptaccount" if u.get("role") == "dealer"
                    else (u.get("email") or u["id"]))
        out[u["id"]] = name
    return out


async def besitzer_anreichern(user, items: list) -> list:
    """Chef-Ansicht: owner_name je Fahrzeug ergaenzen (Sucher sehen ohnehin
    nur eigene Fahrzeuge — kein Feld noetig)."""
    if ist_sucher(user) or not items:
        return items
    namen = await besitzer_namen(user["dealer_id"], [i.get("owner_user_id") for i in items])
    for i in items:
        oid = i.get("owner_user_id")
        i["owner_name"] = namen.get(oid) if oid else None
    return items


async def log_activity(dealer_id: str, user_id: str, action: str,
                       ref: Optional[str] = None, meta: Optional[dict] = None):
    await db.activity_logs.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": dealer_id, "user_id": user_id,
        "action": action, "ref": ref, "meta": meta or {},
        "created_at": now_iso(),
    })
