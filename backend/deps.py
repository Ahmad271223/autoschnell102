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
from fastapi import Depends, HTTPException, Response
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
from konfig import zahl_env  # Pruefung 14.09.2026: keine Abstuerze durch .env-Tippfehler
_MONGO_MAX_POOL = zahl_env("MONGO_MAX_POOL_SIZE", 100, unten=5)
_MONGO_MIN_POOL = zahl_env("MONGO_MIN_POOL_SIZE", 10, unten=0)
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
        # Runde 17: nicht nur bei GESCHLOSSENER alter Schleife neu binden,
        # sondern immer, wenn eine ANDERE Schleife laeuft. Vorher blieb der
        # Client an einer noch offenen Test-Schleife haengen, und der naechste
        # Test (eigene Schleife) bekam "attached to a different loop" — die
        # Job-Sperre (job_lock.acquire faengt alles) meldete dann 100x False.
        # Im Serverbetrieb (eine Schleife je Worker) passiert das nie.
        if self._client is None or (loop is not None and self._loop is not None
                                    and loop is not self._loop):
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
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


_REPLICA_SET_STAND: dict = {"bis": 0.0, "ist": False}


async def ist_replica_set() -> bool:
    """Transaktionen gibt es nur im Replica-Set (Produktion); Ergebnis 10 min
    zwischengespeichert (Einzelserver in Tests)."""
    import time as _time
    if _time.monotonic() < _REPLICA_SET_STAND["bis"]:
        return _REPLICA_SET_STAND["ist"]
    ist = False
    try:
        h = await db.command("hello")
        ist = bool(h.get("setName"))
    except Exception:  # noqa: BLE001
        ist = False
    _REPLICA_SET_STAND.update(bis=_time.monotonic() + 600, ist=ist)
    return ist


#: Antwort, wenn der Ausgang einer Transaktion nicht feststeht (Nr. 30-33).
COMMIT_UNKLAR = ("Der Vorgang konnte nicht sicher abgeschlossen werden. "
                 "Bitte die Seite neu laden und nachsehen, ob die Aenderung "
                 "schon gespeichert ist — sie wird NICHT automatisch "
                 "wiederholt, damit nichts doppelt passiert.")


async def transaktion(fn):
    """fn(session) in einer Transaktion ausfuehren, wenn moeglich; sonst ohne.

    Nachpruefung 20.09.2026, Nr. 30-33: Vorher wurde JEDER PyMongoError
    gleich behandelt — es lief einfach `fn(None)` noch einmal, ohne
    Transaktion. Genau das war in zwei Faellen falsch:

      * Steht der Ausgang der Uebergabe nicht fest
        (UnknownTransactionCommitResult), kann sie sehr wohl geklappt
        haben. Der zweite Durchlauf sah dann eine bereits geaenderte
        Datenbank: bei der Terminaenderung passte der Abgleich auf
        `updated_at` nicht mehr -> "409 Stand veraltet", obwohl gespeichert
        wurde; beim Loeschen war der Termin schon weg -> "404", obwohl
        genau dieses Loeschen erfolgreich war.
      * Und ausgerechnet bei einer Datenbank-Stoerung — also dann, wenn
        Alles-oder-nichts am wichtigsten ist — lief der mehrschrittige
        Ablauf bewusst OHNE Transaktion.

    Jetzt entscheiden die Kennzeichen, die MongoDB selbst mitschickt:
      TransientTransactionError      nichts wurde uebernommen -> einmal
                                     komplett wiederholen (mit Transaktion)
      UnknownTransactionCommitResult Ausgang unbekannt -> NICHT wiederholen,
                                     503 mit klarer Ansage an den Nutzer
      alles andere                   wie bisher: einmal ohne Transaktion
                                     (die Schritte sind idempotent)

    HTTPException geht unveraendert durch."""
    if await ist_replica_set():
        from pymongo.errors import PyMongoError
        for versuch in (1, 2):
            try:
                async with await client.start_session() as s:
                    async with s.start_transaction():
                        return await fn(s)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001
                if not isinstance(exc, PyMongoError):
                    raise
                if exc.has_error_label("UnknownTransactionCommitResult"):
                    log.error("Transaktion mit unklarem Ausgang (%s) — NICHT "
                              "wiederholt", exc)
                    raise HTTPException(503, COMMIT_UNKLAR)
                if exc.has_error_label("TransientTransactionError") and versuch == 1:
                    log.warning("Transaktion vorzeitig beendet (%s) — sie wurde "
                                "nachweislich nicht uebernommen, zweiter Versuch", exc)
                    continue
                log.warning("Transaktion nicht moeglich (%s) — Schritte laufen einzeln",
                            exc)
                break
    return await fn(None)


# ---------- Helpers ----------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_doc(d: dict) -> dict:
    if not d:
        return d
    d.pop("_id", None)
    return d


# ---------- Auth dependencies ----------
async def current_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
                       response: Response = None):
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
            raise firma_gesperrt_fehler()
    if not user or not user.get("active"):
        raise HTTPException(401, "Account deaktiviert")
    # Single-session enforcement (strikt): Das Token-sid MUSS der aktuell
    # gespeicherten Session entsprechen. Ist keine Session gesetzt (z.B. nach
    # Logout oder bevor der Account je eingeloggt war), ist JEDES Token
    # ungültig — sonst wären nach einem Logout alle alten Tokens wieder
    # brauchbar und mehrere Geräte gleichzeitig möglich.
    if payload.get("sid") != user.get("current_session_id"):
        # Runde 19 (09.09.2026): SAGEN, was passiert ist — vorher hiess jede
        # beendete Sitzung "anderes Geraet", auch nach Abmeldung, Sperre oder
        # Passwortwechsel. Der Aufrufer besass bis eben ein gueltiges Token
        # dieses Kontos; Zeitpunkt und Geraet der neueren Anmeldung darf er
        # erfahren (so macht es jeder Mail-Anbieter).
        raise HTTPException(401, sitzung_beendet_grund(user))
    # Rollenpruefung 22.09.2026 (RP-546): gleitende Sitzung — laeuft das Token
    # bald ab, liefert die Antwort ein frisches derselben Sitzung mit
    # (auth.token_erneuern: hoechstens SITZUNG_MAX_TAGE, nie fuer Betreiber).
    # response ist None bei direktem Aufruf ausserhalb von FastAPI.
    if response is not None:
        try:
            from auth import NEUES_TOKEN_KOPF, token_erneuern
            neu = token_erneuern(payload, user.get("role"))
            if neu:
                response.headers[NEUES_TOKEN_KOPF] = neu
        except Exception:  # noqa: BLE001 — die Verlaengerung ist Kuer, nie ein 500
            log.exception("Token-Verlaengerung fehlgeschlagen")
    return user


def sitzung_beendet_grund(user: dict) -> str:
    """Meldung fuer ein Token, dessen Sitzung nicht mehr die aktuelle ist."""
    if not user.get("current_session_id"):
        return ("Sitzung beendet: Abmeldung, Sperre oder Passwortwechsel — "
                "bitte neu anmelden.")
    seit = user.get("current_session_seit") or ""
    geraet = user.get("current_session_geraet") or ""
    wann = ""
    if seit:
        try:
            from datetime import datetime as _dt
            wann = _dt.fromisoformat(seit).astimezone().strftime("%d.%m.%Y %H:%M")
        except ValueError:
            wann = seit[:16].replace("T", " ")
    teile = ["Sitzung beendet: dein Konto wurde erneut angemeldet"]
    if wann:
        teile.append(f"am {wann} Uhr")
    if geraet:
        teile.append(f"von {geraet}")
    return " ".join(teile) + ". Es ist immer nur eine Anmeldung je Konto aktiv."


FIRMA_GESPERRT_TEXT = "Die Firma ist gesperrt — bitte den Administrator kontaktieren."


def firma_gesperrt_fehler() -> HTTPException:
    """403 fuer Konten einer gesperrten Firma — MIT Kopfzeile `X-Sperre: firma`.

    Pruefbericht 20.09.2026 (B2/H1): Ein nacktes 403 sah fuer die Oberflaeche
    aus wie jedes andere "darfst du nicht". Die Anmeldung lief deshalb in eine
    Schleife (Login ok, /auth/me 403, Token still weg), und wurde die Firma
    waehrend der Arbeit gesperrt, blieben die Seiten einfach leer. Mit der
    Kopfzeile meldet api.js ab und zeigt diesen Text auf der Anmeldeseite.
    Nur fuer Konten DER Firma — Fahrer arbeiten fuer mehrere Firmen und
    bekommen weiter das nackte 403 (routes/drivers.py)."""
    return HTTPException(403, FIRMA_GESPERRT_TEXT, headers={"X-Sperre": "firma"})


async def firma_gesperrt(dealer_id: Optional[str]) -> bool:
    """Runde 13: A8/A9 — gesperrter Chef = gesperrte Firma, EINE Fassung der
    Regel fuer Login (current_user), oeffentlichen Marktplatz und
    Einladungen. Vorher pruefte nur current_user den Hauptaccount; der
    Marktplatz zeigte eine gesperrte Firma weiter, _redeem_invite legte
    weiter Mitgliedschaften an. Hauptaccount = aeltestes dealer-Konto je
    Firma (Runde 11).
    Pruefbericht 20.09.2026 (R1-26): aktiv heisst active is True — wie bei
    current_user ("not user.get('active')"); Migration m8 setzt das Feld
    ueberall explizit, ein fehlendes Feld gilt nicht mehr still als aktiv."""
    if not dealer_id:
        return False
    # Runde 12 (15.09.2026, Nr. 4): der eingetragene Hauptaccount (dealers.user_id)
    # ist massgeblich; nur ohne Zeiger (Altbestand) das aelteste dealer-Konto.
    firma = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "user_id": 1})
    if firma and firma.get("user_id"):
        chef = await db.users.find_one({"id": firma["user_id"]}, {"_id": 0, "active": 1})
        if chef is not None:
            return chef.get("active") is not True
    chef = await db.users.find_one(
        {"dealer_id": dealer_id, "role": "dealer"},
        {"_id": 0, "active": 1}, sort=[("created_at", 1)])
    return chef is not None and chef.get("active") is not True


async def gesperrte_firmen_ids() -> set:
    """Runde 13: A8 — alle Firmen, deren Hauptaccount (aeltestes dealer-Konto)
    gesperrt ist, in EINER Abfrage — fuer die Listenfilter des Marktplatzes.
    Sperre = active nicht True (R1-26: dieselbe Regel wie firma_gesperrt)."""
    # Runde 16 (15.09.2026): dieselbe Regel wie firma_gesperrt — der eingetragene
    # Hauptaccount (dealers.user_id) entscheidet; nur Firmen ohne Eintrag fallen
    # auf das aelteste dealer-Konto zurueck. Vorher bewerteten Sucher (firma_
    # gesperrt) und Fahrer (diese Liste) dieselbe Firma nach einem Chefwechsel
    # unterschiedlich.
    gesperrt: set = set()
    mit_hauptkonto: set = set()
    async for d in db.dealers.aggregate([
            {"$match": {"user_id": {"$nin": [None, ""]}}},
            {"$lookup": {"from": "users", "localField": "user_id", "foreignField": "id",
                         "as": "chef"}},
            {"$project": {"id": 1, "chef.active": 1}}]):
        chefs = d.get("chef") or []
        # Rollenpruefung 22.09.2026 (RP-131/RP-282): zeigt der Zeiger auf ein
        # Konto, das es nicht (mehr) gibt, faellt firma_gesperrt auf das
        # aelteste dealer-Konto zurueck — diese Liste tat das nicht, beide
        # bewerteten dieselbe Firma verschieden. Jetzt: ohne gefundenes
        # Zeiger-Konto zaehlt die Firma wie eine ohne Zeiger (Rueckfall unten).
        if not chefs:
            continue
        mit_hauptkonto.add(d["id"])
        if chefs[0].get("active") is not True:
            gesperrt.add(d["id"])
    rows = db.users.aggregate([
        {"$match": {"role": "dealer", "dealer_id": {"$nin": [None, ""]}}},
        {"$sort": {"created_at": 1}},
        {"$group": {"_id": "$dealer_id", "active": {"$first": "$active"}}},
        {"$match": {"active": {"$ne": True}}},
    ])
    async for r in rows:
        if r["_id"] not in mit_hauptkonto:
            gesperrt.add(r["_id"])
    return gesperrt


# Kontonummer (13.09.2026), Schritt 5: die plattformweite E-Mail-Pruefung
# (Runde 13, B5) entfaellt —
# angemeldet wird per Kontonummer, die E-Mail ist nur noch Kontaktadresse und
# darf mehrfach vorkommen (kein Unique-Index mehr, kein Passwort-Reset per Mail).


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
    firma = await db.dealers.find_one(
        {"id": user["dealer_id"]},
        {"_id": 0, "id": 1, "loeschung": 1, "user_id": 1})
    if firma is None:
        raise HTTPException(403, "Kein Händlerprofil — bitte den "
                                 "Administrator kontaktieren")
    # Runde 12 (15.09.2026, Nr. 6): waehrend der Firmenloeschung (Grabstein am
    # Firmen-Dokument) keine Chef-/Sucher-Schreibvorgaenge mehr — sonst
    # konkurrieren sie mit der Loeschkaskade und legen Daten neu an.
    if (firma.get("loeschung") or {}).get("status") == "laeuft":
        raise HTTPException(409, "Diese Firma wird gerade gelöscht — keine Änderungen mehr möglich")
    # Nachpruefung 20.09.2026: `ist_haupt_chef` und `current_chef` erkennen ein
    # uebrig gebliebenes zweites dealer-Konto korrekt — die BEREICHS-Logik aber
    # nicht. `ist_sucher()` fragt nur `role == "sucher"`, also gilt dort
    # ueberall "wer kein Sucher ist, ist Chef": fahrzeug_bereich,
    # eigene_fahrzeug_ids, termin_bereich, termin_im_bereich, _vertrag_bereich
    # und jede `role == "dealer"`-Abfrage haetten so einem Konto die
    # Firmensicht gegeben. (Kein Mandantenleck — alle diese Wege filtern
    # zuerst nach dealer_id, es bleibt also in der eigenen Firma.)
    #
    # Statt zwoelf Stellen einzeln umzubauen, wird das Konto HIER einmal
    # eingenordet: wer nicht der eingetragene Chef ist, arbeitet fuer die
    # Dauer dieser Anfrage als Sucher. Alle Helfer ziehen damit automatisch
    # nach. Kostet nichts — `user_id` kommt aus der Abfrage, die ohnehin laeuft.
    #
    # Nur bei GESETZTEM Zeiger: fehlt er (Altbestand), greift weiterhin die
    # Ersatzregel aus `ist_haupt_chef`/`current_chef` (aeltestes dealer-Konto),
    # und `current_chef` traegt den Zeiger beim naechsten Mal nach.
    # Nachpruefung 20.09.2026 (N3): Die Einnordung griff nur bei GESETZTEM
    # Zeiger. Bei Altbestand ohne `dealers.user_id` blieben dagegen ALLE
    # dealer-Konten Chef — genau der Fall, den der Fix abdecken sollte.
    # Jetzt wird der Hauptchef immer eindeutig bestimmt: fehlt der Zeiger,
    # gilt das aelteste dealer-Konto, und der Zeiger wird per CAS nachgetragen
    # (dieselbe Regel wie in current_chef/ist_haupt_chef, damit nicht zwei
    # Stellen unterschiedlich entscheiden). Die Zusatzabfrage trifft nur
    # Firmen ohne Zeiger, und auch die nur einmal — danach steht er.
    haupt = firma.get("user_id")
    if user.get("role") == "dealer" and not haupt:
        aeltester = await db.users.find_one(
            {"dealer_id": user["dealer_id"], "role": "dealer"},
            {"_id": 0, "id": 1}, sort=[("created_at", 1)])
        haupt = (aeltester or {}).get("id")
        if haupt:
            try:
                await db.dealers.update_one(
                    {"id": user["dealer_id"],
                     "$or": [{"user_id": {"$exists": False}}, {"user_id": None},
                             {"user_id": ""}]},
                    {"$set": {"user_id": haupt}})
            except Exception:  # noqa: BLE001 — Nachtragen ist Kuer, nicht Pflicht
                pass
    if user.get("role") == "dealer" and haupt and haupt != user["id"]:
        user = dict(user)          # nie das Dokument des Aufrufers veraendern
        user["role"] = "sucher"
        user["kein_haupt_chef"] = True
        # Nachpruefung 20.09.2026 (N4): current_user prueft die Firmensperre
        # nur fuer Konten, die SCHON als Sucher ankommen. Ein uebrig
        # gebliebenes dealer-Konto kam daran vorbei und lief danach als
        # Sucher weiter — also die Sperre umgangen. Deshalb hier, direkt
        # nach der Einnordung, dieselbe Pruefung.
        if await firma_gesperrt(user["dealer_id"]):
            raise firma_gesperrt_fehler()
    return user


async def ist_haupt_chef(user) -> bool:
    """Ist dieses Konto der EINE Hauptchef seiner Firma?

    Pruefbericht 20.09.2026 (P0): `current_chef` hat das schon immer richtig
    gemacht (Zeiger `dealers.user_id`), aber SECHS andere Stellen fragten nur
    `role == "dealer"` — und behandelten damit jedes dealer-Konto der Firma
    als Chef. Betroffen waren die firmenweiten Einstellungen, das Logo, die
    Abo-Kuendigung, das Firmenprofil sowie Fahrer-ID/E-Mail in der Termin-
    und Fahrerliste.

    Ein zweites dealer-Konto entsteht im Normalbetrieb nicht (die Anlage legt
    genau eines an, der Chefwechsel stuft alle anderen unter einer Sperre zu
    Suchern herab). Es bleiben zwei echte Wege: ein Chefwechsel, der zwischen
    den Schritten abbricht, und Altbestand von vor dem 15.09.2026. Genau
    dafuer ist diese Pruefung da — sie kostet eine indexierte Abfrage.

    Wirft nie; im Zweifel False (fail-closed)."""
    if user.get("role") != "dealer" or not user.get("dealer_id"):
        return False
    firma = await db.dealers.find_one({"id": user["dealer_id"]},
                                      {"_id": 0, "user_id": 1})
    haupt = (firma or {}).get("user_id")
    if haupt:
        return haupt == user["id"]
    # Altbestand ohne Zeiger: das aelteste dealer-Konto gilt als Chef.
    aeltester = await db.users.find_one(
        {"dealer_id": user["dealer_id"], "role": "dealer"},
        {"_id": 0, "id": 1}, sort=[("created_at", 1)])
    return not aeltester or aeltester["id"] == user["id"]


async def haupt_chef_id(dealer_id: Optional[str]) -> Optional[str]:
    """Konto-ID des EINEN Hauptchefs einer Firma (oder None).

    Rollenpruefung 22.09.2026 (RP-031/RP-033/RP-151): Betreiber-Ansichten,
    Sperren und Loeschen entschieden nach der rohen Rolle ("jedes dealer-Konto
    ist Chef"). Dieselbe Regel wie ist_haupt_chef/current_chef an EINER Stelle:
    der Zeiger dealers.user_id, ohne Zeiger (Altbestand) das aelteste
    dealer-Konto."""
    if not dealer_id:
        return None
    firma = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "user_id": 1})
    haupt = (firma or {}).get("user_id")
    if haupt:
        return haupt
    aeltester = await db.users.find_one(
        {"dealer_id": dealer_id, "role": "dealer"},
        {"_id": 0, "id": 1}, sort=[("created_at", 1)])
    return (aeltester or {}).get("id")


async def current_chef(user=Depends(current_firma)):
    """NUR der Haendler-Hauptaccount. Berechtigungsmatrix (PR-Review
    09/2026): destruktive und firmenweite Aktionen (Fahrerliste, fremde
    Vertraege/Termine loeschen, Netzwerk-Mitglieder) sind Chefsache;
    Sucher arbeiten in ihrem eigenen Bereich."""
    if user.get("role") != "dealer":
        raise HTTPException(403, "Nur der Händler-Hauptaccount darf das")
    # Runde 12 (15.09.2026, Nr. 1): Chef ist NUR der in dealers.user_id
    # eingetragene Hauptaccount — ein zweites oder liegengebliebenes
    # dealer-Konto derselben Firma bekommt keine Chef-Rechte. Ohne Zeiger
    # (Altbestand vor dem 15.09.) gilt das aelteste dealer-Konto, und der
    # Zeiger wird dabei nachgezogen.
    firma = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0, "user_id": 1})
    haupt = (firma or {}).get("user_id")
    if haupt and haupt != user["id"]:
        raise HTTPException(403, "Nur der Händler-Hauptaccount darf das — dieses Konto ist "
                                 "nicht der eingetragene Chef der Firma")
    if not haupt:
        aeltester = await db.users.find_one(
            {"dealer_id": user["dealer_id"], "role": "dealer"},
            {"_id": 0, "id": 1}, sort=[("created_at", 1)])
        if aeltester and aeltester["id"] != user["id"]:
            raise HTTPException(403, "Nur der Händler-Hauptaccount darf das — dieses Konto ist "
                                     "nicht der eingetragene Chef der Firma")
        try:
            await db.dealers.update_one(
                {"id": user["dealer_id"],
                 "$or": [{"user_id": {"$exists": False}}, {"user_id": None}, {"user_id": ""}]},
                {"$set": {"user_id": user["id"]}})
        except Exception:  # noqa: BLE001
            pass
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


# Wunsch Ahmad 20.09.2026: probe3/probe5 sind vollwertige Abos — ohne
# sie hier waere das Probe-Abo zwar angelegt, wuerde aber KEINEN Zugang
# geben (der Plan gilt dann als "ungueltig", siehe unten).
ABO_PLAENE_ERLAUBT = {"monthly", "yearly", "trial", "lifetime",
                      "probe3", "probe5"}
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
            # Runde 13 (Liste 4 Nr. 3): das persoenliche Abo gehoert zur Firma —
            # Altbestand ohne dealer_id bleibt gueltig.
            {"subject_user_id": subject_user_id, "status": {"$ne": "ersetzt"},
             "$or": [{"dealer_id": dealer_id}, {"dealer_id": {"$exists": False}},
                     {"dealer_id": None}]},
            sort=[("created_at", -1)])
    else:
        # Firmen-Abo: Dokumente ohne subject_user_id-Feld UND Alt-Dokumente mit
        # explizitem null (vor m1_abos_normalisieren) — in EINER Abfrage, das
        # juengste nicht ersetzte gilt. WICHTIG: NICHT einfach irgendein Abo
        # des Haendlers nehmen — sonst wuerde das persoenliche Abo eines
        # Suchers faelschlich fuer den Chef zaehlen (Chef muss sein EIGENES
        # Abo haben).
        # Pruefbericht 20.09.2026 (R1-16): vorher zwei Abfragen (erst ohne Feld,
        # dann null ohne status-Filter) — ein aelteres Dokument ohne Feld gewann
        # gegen ein juengeres mit null, und die Admin-Uebersicht bildete diese
        # Vorrangregel nach. Jetzt zaehlt allein created_at, hier wie dort.
        sub = await db.subscriptions.find_one(
            {"dealer_id": dealer_id, "status": {"$ne": "ersetzt"},
             "$or": [{"subject_user_id": {"$exists": False}}, {"subject_user_id": None}]},
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
    # Rollenpruefung 22.09.2026 (RP-057): Der Rueckfall aufs Firmen-Abo gilt
    # NUR fuer den Hauptchef. Die HTTP-Wege ordnen ein weiteres dealer-Konto
    # schon in current_firma als Sucher ein; der Link-Worker, die Admin-Liste
    # und andere Aufrufer mit der ROHEN Rolle aus db.users bekamen dagegen das
    # Firmen-Abo mit. Jetzt entscheidet diese Funktion selbst (eine indexierte
    # Abfrage, nur fuer dealer-Konten ohne aktives eigenes Abo).
    if not await ist_haupt_chef(user):
        return personal
    return await get_subscription_status(user.get("dealer_id", ""))


# ---------- Persönliche Einstellungs-Overrides (Sucher) ----------
# Der Chef füllt die Händler-Einstellungen vor; jeder Sucher darf sie FÜR
# SICH überschreiben (users.settings_override). Wirksam = Händler-Werte,
# überlagert von den eigenen. Chef-Werte bleiben unangetastet.
SUCHER_SETTINGS_FIELDS = {
    # Profil (erscheint auf den Verträgen des Suchers). Entscheidung Ahmad
    # 16.09.2026: das Firmenlogo aendert nur der Chef — "logo_url" ist kein
    # Sucher-Feld mehr; alte persoenliche Logo-Overrides bleiben wirkungslos.
    "company_name", "contact_person", "phone", "whatsapp_number", "email",
    "address", "zip_code", "city", "opening_hours",
    # Vergleich
    "comparison_rules", "export_rules", "active_profile",
    # Versand
    "email_subject", "email_template", "whatsapp_template",
    # Vorlage Ahmad 20.09.2026: die drei Folge-Mails
    "email_subject_korrektur", "email_template_korrektur",
    "email_subject_nach_kauf", "email_template_nach_kauf",
    "whatsapp_template_nach_kauf",
    "email_subject_bahn", "email_template_bahn",
    # AGB & Vereinbarungen (+ Text der digitalen Ausfertigung, 09.09.2026)
    "default_terms", "default_special_agreements", "digital_vertragstext",
    # Schalter fuer unseren Standardsatz (20.09.2026)
    "sondervereinbarung_standard_aktiv",
}


async def effective_dealer(user: dict) -> dict:
    """Händler-Dokument aus Sicht dieses Nutzers: für Sucher werden die
    persönlichen Overrides über die Chef-Vorgaben gelegt."""
    dealer = await db.dealers.find_one({"id": user.get("dealer_id")},
                                       {"_id": 0}) or {}
    if user.get("role") != "sucher":
        return regelpakete_vervollstaendigen(dealer)
    override = user.get("settings_override") or {}
    merged = dict(dealer)
    eigene = []
    for k, v in override.items():
        if k in SUCHER_SETTINGS_FIELDS and v is not None:
            merged[k] = v
            eigene.append(k)
    # Pruefbericht 20.09.2026 (B19): Welche Felder sind PERSOENLICH gesetzt?
    # Die Einstellungen zeigen das an und bieten "auf Chef-Vorgaben
    # zuruecksetzen" — vorher gab es keinen Weg zurueck.
    merged["eigene_einstellungen"] = sorted(eigene)
    return regelpakete_vervollstaendigen(merged)


def regelpakete_vervollstaendigen(dealer: dict) -> dict:
    """16.09.2026 (Pruefung 'Speichern' der Vergleichsregeln): die Oberflaeche
    bekommt IMMER vollstaendige Regelpakete — fehlende Regeln und fehlende
    Teile (z.B. ein Export-Paket, das nie gespeichert wurde) kommen aus dem
    Standard des jeweiligen Profils. Vorher zeigte das Formular fuer fehlende
    Teile Inland-Platzhalter (+30.000 km), waehrend der Vergleich den Export-
    Standard (kein Kilometer-Limit) anwandte, und ein geaenderter Wert wurde
    ohne Modus gespeichert. Aendert nichts in der Datenbank."""
    if not isinstance(dealer, dict) or not dealer:
        # Kein Haendlerdokument (verwaistes Konto): leer zurueckgeben, damit
        # die Aufrufer-Pruefung "if not dealer -> 403" weiter greift.
        return dealer
    from mobile_service import DEFAULT_EXPORT_RULES, DEFAULT_RULES
    from regeln import regeln_lesen
    dealer["comparison_rules"] = regeln_lesen(dealer.get("comparison_rules"), DEFAULT_RULES)
    dealer["export_rules"] = regeln_lesen(dealer.get("export_rules"), DEFAULT_EXPORT_RULES)
    return dealer


async def require_active_sub(user=Depends(current_firma)):
    """Sucher-Funktionen (Vergleich, Suche, Linkpruefung, Vertraege).

    Befund 144/145/164 (19.09.2026): Diese Sperre hing frueher direkt an
    `current_user` und prueft damit NUR Rolle und Abo. Firmenexistenz und
    Loeschsperre steckten allein in `current_firma` — also konnte ein Sucher
    waehrend der laufenden Firmenloeschung (oder mit einer Firma, die es gar
    nicht mehr gibt) weiter vergleichen, Links einreihen und damit neue
    Fahrzeug-/Cache-Daten erzeugen, die mitten in die Loeschkaskade fielen.
    Jetzt baut sie auf `current_firma` auf: Rolle, Firmendokument und
    Loeschsperre gelten automatisch ueberall, wo das Abo verlangt wird.
    """
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
    hoechste vergebene Nummer gehoben — es entsteht nie eine Dublette.

    Runde 22 (11.09.2026): Gehoben wurde nur bei einer Kollision. Stand der
    Zaehler zurueck und gab es Luecken (geloeschte Firmen), bekam eine neue
    Firma still die Nummer einer GELOESCHTEN Firma. Jetzt gilt: nie unter
    oder auf die hoechste vergebene Nummer (Index kunden_nr_unique, also
    ein billiger Indexzugriff; Firmen werden selten angelegt).

    Kontonummer (13.09.2026): EINE Reihe fuer Firmen, Kaeufer und Fahrer —
    der Rumpf liegt in kontenanlage.naechste_nummer (Selbstheilung ueber
    dealers, users und driver_accounts). Der Name bleibt fuer seed_super_admin,
    kunden_nummern_nachziehen und die Tests."""
    from kontenanlage import naechste_nummer
    return await naechste_nummer(db)


async def kunden_nummern_nachziehen() -> int:
    """Bestandsfirmen ohne Kundennummer nummerieren (aelteste zuerst).
    Idempotent je Firma ($exists-Guard): parallele Worker erzeugen
    hoechstens Luecken, nie Dubletten. Liefert die Zahl neuer Nummern."""
    from kontenanlage import KUNDEN_NR_FEHLT
    n = 0
    # Runde 15: auch null / falscher Typ (Restore, Altbestand), nicht nur fehlend.
    async for d in db.dealers.find({**KUNDEN_NR_FEHLT, "kunden_nr": {"$not": {"$type": "double"}}},
                                   {"_id": 0, "id": 1}).sort("created_at", 1):
        r = await db.dealers.update_one(
            {"id": d["id"], **KUNDEN_NR_FEHLT},
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
#   Vergleich bzw. manuelle Anlage). Umhaengen durch den Chef gibt es seit
#   21.09.2026 nicht mehr (Wunsch Ahmad, R1-01: PUT /vehicles/{id}/besitzer
#   antwortet 410). Ein Sucher, der ein Inserat vergleicht,
#   das ein Kollege bereits fuehrt, bekommt das Ergebnis mit Hinweis, das
#   Fahrzeug bleibt beim Kollegen.
# Regel wie bei Vertraegen (_vertrag_bereich in routes/contracts.py): fehlt
# der Besitzer am Altdokument, sieht der Sucher es nicht (fail-closed); die
# Migration m4 (migrationen.py) ordnet den Altbestand nach Vertrag,
# Vergleich, Aktivitaet, Termin oder Chef zu.
# ---------------------------------------------------------------------
def ist_sucher(user) -> bool:
    return (user or {}).get("role") == "sucher"


def fahrzeug_bereich(user, mit_geloeschten: bool = False) -> Dict[str, Any]:
    """Mongo-Filter fuer vehicles: Chef = Firma, Sucher = eigene Fahrzeuge.
    Runde 17: geloeschte Fahrzeuge (lifecycle "geloescht") sind fuer die
    normalen Lese-/Schreibpfade unsichtbar — vorher lieferten /vehicles und
    /vehicles/{id} "geloeschte" Fahrzeuge weiter aus. Die Akte des Chefs
    darf sie mit mit_geloeschten=True bewusst noch zeigen (Historie)."""
    bereich: Dict[str, Any] = {"dealer_id": user["dealer_id"]}
    if ist_sucher(user):
        # Wunsch Ahmad 09.09.2026: Hauptbearbeiter (owner_user_id) ODER
        # Mitbearbeiter (mitbearbeiter_ids) — wer dasselbe Inserat
        # vergleicht, arbeitet mit und darf einen eigenen Vertrag anlegen.
        bereich["$or"] = [{"owner_user_id": user["id"]},
                          {"mitbearbeiter_ids": user["id"]}]
    if not mit_geloeschten:
        bereich["lifecycle"] = {"$ne": "geloescht"}
    return bereich


async def eigene_fahrzeug_ids(user) -> Optional[List[str]]:
    """IDs der Fahrzeuge im Bereich des Kontos (auch geloeschte — Termine
    und Beweise dazu bleiben im Bereich); None = alle (Chef)."""
    if not ist_sucher(user):
        return None
    return await db.vehicles.distinct(
        "id", {"dealer_id": user["dealer_id"],
               "$or": [{"owner_user_id": user["id"]},
                       {"mitbearbeiter_ids": user["id"]}]})


async def fahrzeug_im_bereich(user, vehicle_id: Optional[str],
                              mit_geloeschten: bool = True) -> bool:
    if not vehicle_id:
        return False
    return await db.vehicles.count_documents(
        {"id": vehicle_id, **fahrzeug_bereich(user, mit_geloeschten=mit_geloeschten)},
        limit=1) > 0


# ---------------------------------------------------------------------
# Runde 17: Datum/Uhrzeit-Pruefung an EINER Stelle (Terminplaner UND
# Vertragsformular — der Vertragsweg legte den Termin vorher ungeprueft an).
# ---------------------------------------------------------------------
_ISO_DATUM_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UHRZEIT_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def datum_iso_pruefen(v):
    """Leer bleibt leer; sonst JJJJ-MM-TT und ein echtes Kalenderdatum."""
    if v is None:
        return v
    s = str(v).strip()
    if not s:
        return ""
    if not _ISO_DATUM_RE.match(s):
        raise ValueError("Datum bitte als JJJJ-MM-TT angeben")
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise ValueError("Kein gueltiges Kalenderdatum")
    return s


def uhrzeit_hhmm_pruefen(v):
    """Leer bleibt leer; sonst HH:MM (00:00 bis 23:59)."""
    if v is None:
        return v
    s = str(v).strip()
    if not s:
        return ""
    if not _UHRZEIT_RE.match(s):
        raise ValueError("Uhrzeit bitte als HH:MM angeben")
    return s


# Uebergabe-Regel (Runde 13, 15.09.2026): Der Bereich eines Suchers sind
# seine Kaufvorgaenge, Vertraege und Termine (siehe termin_bereich). Eine
# Uebergabe ("Aus meiner Liste entfernen" durch den Sucher selbst) oder die
# Loeschung eines Suchers uebertraegt genau diese Objekte zum Fahrzeug mit
# (routes.bestand.vorgang_uebergeben) — der bisherige Bearbeiter verliert
# damit den Zugriff auf Termine, Berichte und Protokolle, der neue bekommt
# den ganzen Vorgang. Das Fahrzeug allein gibt keinen Terminzugriff (mehrere
# Sucher duerfen dasselbe Inserat unabhaengig kaufen).
_OHNE_FAHRZEUG = {"$in": [None, ""]}


async def termin_bereich(user) -> Dict[str, Any]:
    """Mongo-Filter fuer appointments (Umbau Kaufvorgaenge 09.09.2026):
    Chef = Firma; Sucher = selbst angelegt ODER eigener Vertrag ODER
    eigener Kaufvorgang. Das FAHRZEUG gibt keinen Zugriff mehr — mehrere
    Sucher duerfen dasselbe Inserat unabhaengig kaufen, ohne die Termine
    (Verkaeuferdaten) der Kollegen zu sehen."""
    q: Dict[str, Any] = {"dealer_id": user["dealer_id"]}
    if ist_sucher(user):
        cids = await db.generated_pdfs.distinct(
            "id", {"dealer_id": user["dealer_id"], "user_id": user["id"]})
        kids = await db.kaufvorgaenge.distinct(
            "id", {"dealer_id": user["dealer_id"], "user_id": user["id"]})
        q["$or"] = [{"created_by": user["id"]},
                    {"contract_id": {"$in": cids}},
                    {"kaufvorgang_id": {"$in": kids}}]
    return q


async def termin_im_bereich(user, appt: dict) -> bool:
    """Einzelner, bereits geladener Termin (created_by, contract_id,
    kaufvorgang_id) — dieselbe Regel wie termin_bereich."""
    if not ist_sucher(user):
        return True
    if appt.get("created_by") == user["id"]:
        return True
    cid = appt.get("contract_id")
    if cid and await db.generated_pdfs.count_documents(
            {"id": cid, "user_id": user["id"]}, limit=1):
        return True
    kid = appt.get("kaufvorgang_id")
    return bool(kid) and await db.kaufvorgaenge.count_documents(
        {"id": kid, "user_id": user["id"]}, limit=1) > 0


async def besitzer_namen(dealer_id: str, ids) -> Dict[str, str]:
    """Konto-ID -> Anzeigename (Chef-Ansicht 'Bearbeiter')."""
    ids = [i for i in set(ids or []) if i]
    if not ids:
        return {}
    out: Dict[str, str] = {}
    konten = await db.users.find(
        {"id": {"$in": ids}, "dealer_id": dealer_id},
        {"_id": 0, "id": 1, "first_name": 1, "last_name": 1, "email": 1, "role": 1,
         "kontonummer": 1}).to_list(1000)
    for u in konten:
        name = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip()
        if not name:
            # Kontonummer (13.09.2026): Konten ohne E-Mail -> Kontonummer
            name = ("Händler-Hauptaccount" if u.get("role") == "dealer"
                    else (u.get("email") or u.get("kontonummer") or u["id"]))
        out[u["id"]] = name
    return out


# Runde 30 (12.09.2026, Abnahme der Befunde): Felder am gemeinsam genutzten
# Fahrzeug, die KONTEN der Firma benennen. Ein Sucher darf sie nicht sehen —
# auch nicht in der rohen Antwort. Bis hierher waren nur die NAMEN
# ausgeblendet; die Konto-Kennungen der Kollegen standen weiterhin in
# /bestand, /vehicles, /vehicles/{id} und in der Fahrzeugakte.
KONTO_FELDER = ("owner_user_id", "mitbearbeiter_ids", "uebernommen_von",
                "besitzer_migriert_von", "besitzer_vorher",
                # Pruefbericht 20.09.2026 (R1-03): verliess ein Sucher das
                # Fahrzeug, stand seine Konto-ID hier und ging an den naechsten.
                "entfernt_von_sucher")


def konten_maskieren(user, fahrzeuge):
    """Kollegen-Kennungen aus Fahrzeugdaten entfernen (nur fuer Sucher).

    Nimmt ein einzelnes Fahrzeug ODER eine Liste und aendert es an Ort und
    Stelle. Fuer den Chef bleibt alles stehen — er verwaltet die Zuordnung."""
    if not ist_sucher(user) or not fahrzeuge:
        return fahrzeuge
    liste = fahrzeuge if isinstance(fahrzeuge, list) else [fahrzeuge]
    for f in liste:
        if isinstance(f, dict):
            for feld in KONTO_FELDER:
                f.pop(feld, None)
    return fahrzeuge


async def besitzer_anreichern(user, items: list) -> list:
    """Chef-Ansicht: owner_name je Fahrzeug ergaenzen (Sucher sehen ohnehin
    nur eigene Fahrzeuge — kein Feld noetig)."""
    if not items:
        return items
    ids = [i.get("owner_user_id") for i in items]
    for i in items:
        ids.extend(i.get("mitbearbeiter_ids") or [])
    namen = await besitzer_namen(user["dealer_id"], ids)
    for i in items:
        mit = [namen[m] for m in (i.get("mitbearbeiter_ids") or []) if m in namen]
        if ist_sucher(user):
            # Runde 29 (12.09.2026, Regel Ahmad): Ein Sucher sieht NICHT, wer
            # sonst an seinem Auto arbeitet — vorher standen die Namen der
            # Kollegen in Bestandsliste und Fahrzeugpool.
            i["mitbearbeiter_namen"] = []
            continue
        oid = i.get("owner_user_id")
        i["owner_name"] = namen.get(oid) if oid else None
        i["mitbearbeiter_namen"] = mit
    # Runde 30: ... und auch die Kennungen selbst raus.
    konten_maskieren(user, items)
    return items


async def log_activity(dealer_id: str, user_id: str, action: str,
                       ref: Optional[str] = None, meta: Optional[dict] = None):
    await db.activity_logs.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": dealer_id, "user_id": user_id,
        "action": action, "ref": ref, "meta": meta or {},
        "created_at": now_iso(),
    })


async def log_activity_sicher(dealer_id: str, user_id: str, action: str,
                              ref: Optional[str] = None,
                              meta: Optional[dict] = None) -> bool:
    """Runde 17: Audit NACH einem bereits dauerhaften Schritt (Inserat
    veroeffentlicht, Fahrzeug verkauft, Vertrag gespeichert) darf den
    Vorgang nicht mehr mit 500 abbrechen lassen — der Client wuerde
    wiederholen, der Zustand ist aber schon geschrieben. Wirft nie; liefert
    False, wenn der Eintrag nicht gespeichert werden konnte (dann steht es
    im Fehlerlog)."""
    try:
        await log_activity(dealer_id, user_id, action, ref=ref, meta=meta)
        return True
    except Exception:
        logging.getLogger("autohandel").exception(
            "Audit-Eintrag %s (%s) konnte nicht gespeichert werden", action, ref)
        return False


# ---------------------------------------------------------------------
# Go-Live-Schalter (15.09.2026): Marktplatz und Inserieren abgeschaltet.
# ---------------------------------------------------------------------
MARKTPLATZ_GESPERRT = ("Demnächst verfügbar — der Marktplatz und das Inserieren sind "
                       "noch nicht freigeschaltet.")


async def marktplatz_freigeschaltet() -> None:
    """Abhaengigkeit fuer alle Marktplatz-/Inserats-Routen: 503 mit klarem
    Text, solange MARKTPLATZ_AKTIV nicht gesetzt ist (konfig.marktplatz_aktiv).
    Direkte Funktionsaufrufe (Unit-Tests) bleiben unberuehrt."""
    from konfig import marktplatz_aktiv
    if not marktplatz_aktiv():
        raise HTTPException(503, MARKTPLATZ_GESPERRT)

