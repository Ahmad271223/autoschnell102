"""Admin endpoints: users CRUD, contracts, stats, comparisons, URL-stats,
self-password, cleanup trigger.
"""
import base64
import hashlib
import os
import re
import asyncio
import uuid

import wartung
import vertrag_vorlagen as _vorlagen
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import betrieb
from decimal import Decimal
from typing import Dict, Literal, Optional


# Rollenprüfung 22.09.2026 (RP-200/RP-351, Welle 3): der alte Helfer
# _safe_filename liess Nicht-latin-1-Zeichen im Dateinamen durch (500 beim
# PDF-Abruf) und wird nicht mehr gebraucht — Dateinamen fuer Kopfzeilen nur
# noch ueber vertrag_dateiname.content_disposition.

from pymongo.errors import DuplicateKeyError
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field, field_validator

from auth import (create_token, hash_password, hash_password_async, new_session_id,
                  verify_password_async)
from cleanup_service import _cleanup_once
from deps import (
    _ablauf_parsen, current_admin, current_super_admin, db, firma_gesperrt,
    get_subscription_status, haupt_chef_id, ist_haupt_chef, log, log_activity,
    log_activity_sicher, now_iso, sub_status_from_doc, subscription_for,
)
from mobile_service import DEFAULT_RULES, DEFAULT_EXPORT_RULES
from passwoerter import persoenliche_werte, pruefe_passwort

router = APIRouter()


# ---------- Models ----------
def _leer_zu_none(v):
    """Kontonummer (13.09.2026): die E-Mail ist nur noch Kontaktadresse und
    optional — ein leeres Formularfeld ("") gilt als "keine Angabe" statt 422."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return v.strip() if isinstance(v, str) else v


def _handelnder(admin: dict) -> str:
    """Kontonummer (13.09.2026): Kennung des handelnden Betreibers in
    Buchhaltungs- und Verlaufsfeldern (resolved_by, recorded_by, admin_email,
    ...). Der Super-Admin hat einen Benutzernamen; seine E-Mail ist nur der
    Platzhalter '<username>@cashcar.local' und gehoert dort nicht hin."""
    return (admin or {}).get("username") or (admin or {}).get("id") or ""


class AdminUserIn(BaseModel):
    # Kontonummer (13.09.2026): Anmeldung per Kontonummer — die E-Mail ist
    # optionale Kontaktadresse der Firma (dealers.email).
    email: Optional[EmailStr] = None
    password: str = Field(min_length=8, max_length=200)
    company_name: str = Field(min_length=1, max_length=200)
    contact_person: str = Field(default="", max_length=120)
    phone: str = Field(default="", max_length=50)
    # Audit 09/2026: nur feste Werte — "lifetime" und Freitext erzeugten
    # unbegrenzten Zugang. Betreiber-Modell: "none" (Firma ohne Abo).
    plan_type: Literal["none", "monthly", "yearly", "trial"] = "none"
    expires_at: Optional[str] = None
    active: Optional[bool] = True
    # Zugangs-Anfrage (type zugang, art firma), die mit der Anlage erledigt ist
    anfrage_id: Optional[str] = Field(default=None, max_length=100)
    # Nachpruefung 15.09.2026: Eingaben weichen bewusst von der Anfrage ab.
    daten_geaendert: bool = False

    @field_validator("email", mode="before")
    @classmethod
    def _email_leer(cls, v):
        return _leer_zu_none(v)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return pruefe_passwort(v)


class AdminActiveIn(BaseModel):
    active: bool


class AdminUserPasswordIn(BaseModel):
    new_password: str = Field(min_length=1, max_length=200)

    @field_validator("new_password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return pruefe_passwort(v)


class AdminSelfPasswordIn(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


# ---------- Cleanup trigger ----------
@router.post("/admin/cleanup/run")
async def admin_trigger_cleanup(user=Depends(current_super_admin)):
    """Manuell einen Cleanup-Durchlauf anstoßen (Debug/QA).
    Regulär läuft der Loop 1× pro Stunde automatisch."""
    # Pruefung 14.09.2026 (Liste 1, Nr. 30): dieselbe Sperre wie der stuendliche
    # Lauf — kein zweiter Lauf parallel (auch nicht durch zwei Admin-Klicks).
    from job_lock import acquire, heartbeat, release
    token = await acquire(db, "cleanup-cycle", ttl_seconds=3300)
    if not token:
        raise HTTPException(409, "Ein Aufräumlauf läuft gerade — bitte später erneut.")
    # Rollenprüfung 22.09.2026 (RP-245, Welle 2): wie der stuendliche Lauf —
    # die Sperre traegt "lauf_aktiv" (ein Restore wartet darauf), und eine
    # Schreibpause, die WAEHREND des Laufs beginnt, bricht ihn sauber ab
    # (cleanup_service.SchreibpauseAktiv) — vorher wurde daraus eine 500.
    from cleanup_service import SchreibpauseAktiv, _lauf_aktiv_setzen
    await _lauf_aktiv_setzen(db, token, True)
    try:
        # Nr. 34: geht die Sperre waehrend des Laufs verloren, bricht der
        # Lauf ab — sonst raeumen zwei Prozesse gleichzeitig auf.
        from job_lock import SperreVerloren
        async with heartbeat(db, "cleanup-cycle", token, 3300) as wache:
            try:
                # RP-245: die Wache mitgeben — zwischen den Schritten wird
                # Sperre UND Schreibpause geprueft.
                stats = await _cleanup_once(db, wache=wache)
                wache.pruefen()
            except SperreVerloren as exc:
                raise HTTPException(
                    409, "Der Aufraeumlauf wurde abgebrochen, weil die Sperre "
                         "zwischenzeitlich an einen anderen Server ging. Bitte "
                         f"spaeter erneut versuchen. ({exc})")
            except SchreibpauseAktiv:
                raise HTTPException(503, AUFRAEUMLAUF_SCHREIBPAUSE)
    finally:
        await _lauf_aktiv_setzen(db, token, False)
        await release(db, "cleanup-cycle", token=token)
    return stats


AUFRAEUMLAUF_SCHREIBPAUSE = ("Eine Sicherung oder ein Restore läuft gerade (Schreibpause) — "
                             "der Aufräumlauf wurde angehalten.")


def _vertragstext_start_admin() -> str:
    from pdf_service import VERTRAGSTEXT_START
    return VERTRAGSTEXT_START


async def _dealer_anlegen_mit_kunden_nr(doc: dict, naechste_kunden_nr) -> None:
    """Firmenprofil mit frischer Kundennummer einfuegen; bei DuplicateKey
    (Unique-Index kunden_nr, nur im Rennen mit einem korrigierten Zaehler)
    neue Nummer ziehen — max. 3 Versuche.
    Kontonummer (13.09.2026): Weiterleitung auf kontenanlage.firma_einfuegen."""
    from kontenanlage import firma_einfuegen
    await firma_einfuegen(db, doc, naechste_kunden_nr)


def _ablaufdatum_pruefen_400(wert, feld: str = "expires_at"):
    """Nachpruefung Runde 14 (Befund 50): Abo-Ablauf aus der Admin-Eingabe
    pruefen — vorher wurde der Rohwert ("31.12.2027", "irgendwann")
    ungeprueft ins Abo geschrieben; deps.sub_status_from_doc wertet ein
    unlesbares Datum fail-closed als "ungueltig", die Firma stand also
    stillschweigend OHNE wirksames Abo da, der Admin bekam 200.
    Erlaubt: leer (-> None), 'JJJJ-MM-TT' (Ablauf am Tagesende wie bei
    _gueltig_bis_parsen) oder ein ISO-Zeitpunkt (naiv = UTC). Sonst 400
    (nicht 422), damit die Oberflaeche die deutsche Meldung zeigt."""
    if wert is None or str(wert).strip() == "":
        return None
    w = str(wert).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", w):
        try:
            return _gueltig_bis_parsen(w)
        except HTTPException:
            raise HTTPException(400, f"{feld} ist kein gueltiges Datum (JJJJ-MM-TT)")
    try:
        d = datetime.fromisoformat(w.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, f"{feld} muss ein ISO-Datum (JJJJ-MM-TT) "
                                 "oder ein ISO-Zeitpunkt sein")
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.isoformat()


async def kaeufer_reservierungen_freigeben(user_id: str, grund: str) -> int:
    """Pruefung 14.09.2026 (M1): Fuer einen Kaeufer reservierte Inserate
    (Status reserviert, reserved_for) wieder veroeffentlichen und seine
    akzeptierten Anfragen beenden. Liefert die Zahl der freigegebenen Inserate.

    Rollenpruefung 22.09.2026 (RP-098 Nr. 1, RP-348 Nr. 1, RP-517): Vorher
    setzte ein update_many nur die INSERATE zurueck — der Fahrzeug-Lebenszyklus
    blieb 'reserviert'. War das Inserat aelter als 21 Tage, raeumte der
    Aufraeumlauf es danach weg, und das Fahrzeug stand fuer immer auf
    'reserviert' (jeder Uebergang 409). Jetzt einzeln je Inserat (CAS auf den
    Reservierungsstand) und mit derselben Nachfuehrung wie die Rueckgabe im
    Marktplatz (inserat_fahrzeug_nachziehen, bei Fehler Merker + Nacharbeit).
    `wieder_veroeffentlicht_am` markiert die Freigabe fuer die Laufzeit-
    Loeschung (Schonfrist, Uebergabe an cleanup_service)."""
    from routes.marketplace import inserat_fahrzeug_nachziehen
    jetzt = now_iso()
    freigegeben = 0
    async for l in db.resale_listings.find(
            {"reserved_for": user_id, "status": "reserviert"}, {"_id": 0, "id": 1}):
        r = await db.resale_listings.update_one(
            {"id": l["id"], "reserved_for": user_id, "status": "reserviert"},
            {"$set": {"status": "veroeffentlicht", "updated_at": jetzt,
                      "wieder_veroeffentlicht_am": jetzt,
                      "reservierung_aufgehoben_grund": grund},
             "$unset": {"reserved_for": ""}})
        if r.modified_count:
            freigegeben += 1
            await inserat_fahrzeug_nachziehen(l["id"], "veroeffentlicht")
    await db.listing_interest.update_many(
        {"buyer_user_id": user_id, "status": "akzeptiert"},
        {"$set": {"status": "abgelehnt", "beendet_grund": grund, "updated_at": jetzt},
         "$push": {"history": {"von": "system", "aktion": grund, "zeit": jetzt}}})
    return freigegeben


async def kaeufer_verhandlungen_beenden(user_id: str, grund: str) -> int:
    """Nachpruefung Runde 14 (Befund 1) / Rollenpruefung 22.09.2026 (RP-098
    Nr. 2, RP-348 Nr. 2): Ein gesperrter Kaeufer behaelt keine laufende
    Verhandlung — offene Anfragen und Gegenangebote enden, akzeptierte
    (reservierte) Fahrzeuge werden frei. EIN Weg fuer die Kontosperre und die
    Sperre des Marktplatz-Zugangs (vorher tat das nur die Kontosperre; nach
    'Zugang sperren' konnte der Haendler eine alte Anfrage noch annehmen und
    fuer den gesperrten Kaeufer reservieren). Liefert die Zahl der
    freigegebenen Inserate."""
    await db.listing_interest.update_many(
        {"buyer_user_id": user_id,
         "status": {"$in": ["offen", "gegenangebot", "gegenangebot_kaeufer"]}},
        {"$set": {"status": "abgelehnt", "beendet_grund": grund,
                  "updated_at": now_iso()},
         "$push": {"history": {"von": "system", "aktion": grund,
                               "zeit": now_iso()}}})
    return await kaeufer_reservierungen_freigeben(user_id, grund)


async def _ist_hauptchef_konto(konto: Optional[dict]) -> bool:
    """Rollenpruefung 22.09.2026 (RP-031/RP-151): Ist dieses Konto der EINE
    Hauptchef seiner Firma (Zeiger dealers.user_id, sonst aeltestes
    dealer-Konto)? Vorher entschied die rohe Rolle: ein liegengebliebenes
    zweites dealer-Konto galt als Chef — sein Sperren meldete alle Sucher der
    Firma ab, sein Loeschen bot die Loeschung der GANZEN Firma an."""
    if not konto or konto.get("role") != "dealer" or not konto.get("dealer_id"):
        return False
    return await ist_haupt_chef(konto)


async def _firmenkonten_abmelden(dealer_id: str, chef_id: str) -> int:
    """Firmensperre (Audit 09/2026): mit dem Hauptchef verlieren ALLE anderen
    Konten der Firma ihre Sitzung. Rollenpruefung 22.09.2026 (RP-131/RP-282):
    vorher nur role 'sucher' — ein liegengebliebenes zweites dealer-Konto
    behielt seine Sitzung. Liefert die Zahl der abgemeldeten Konten."""
    r = await db.users.update_many(
        {"dealer_id": dealer_id, "id": {"$ne": chef_id},
         "current_session_id": {"$ne": None}},
        {"$set": {"current_session_id": None, "updated_at": now_iso()}})
    return r.modified_count


async def _chef_fuer_uebernahme(dealer_id: str, ausser: str) -> Optional[str]:
    """Wer uebernimmt Fahrzeuge und Vorgaenge eines Kontos, das die Firma
    verlaesst (Loeschen, Wechsel zum Zwischenhaendler)? Der eingetragene
    Hauptchef (Zeiger), falls es das Konto gibt; sonst das aelteste andere
    dealer-Konto. Rollenpruefung 22.09.2026 (RP-031/RP-046): vorher suchte der
    Rollenwechsel das aelteste dealer-Konto statt des Zeigers."""
    firma_doc = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "user_id": 1})
    zeiger = (firma_doc or {}).get("user_id")
    if zeiger and zeiger != ausser and await db.users.find_one(
            {"id": zeiger, "dealer_id": dealer_id}, {"_id": 0, "id": 1}):
        return zeiger
    alt = await db.users.find_one(
        {"dealer_id": dealer_id, "role": "dealer", "id": {"$ne": ausser}},
        {"_id": 0, "id": 1}, sort=[("created_at", 1)])
    return (alt or {}).get("id")


CHEFWECHSEL_SPERRE_S = 30


@asynccontextmanager
async def _chefwechsel_sperre(dealer_id: str):
    """Firmenweite Sperre 'chefwechsel-<Firma>' (Runde 12, auch ueber zwei
    Server). Rollenpruefung 22.09.2026 (RP-029/RP-128/RP-279): dieselbe Sperre
    nehmen jetzt auch die Konto-Loeschung und der Start der Firmenloeschung —
    sonst konnte ein Chefwechsel ein Konto zum Chef machen, das parallel als
    'Sucher' geloescht wurde (Firma ohne Chef)."""
    from job_lock import acquire, release
    name = f"chefwechsel-{dealer_id}"
    token = await acquire(db, name, ttl_seconds=CHEFWECHSEL_SPERRE_S)
    if not token:
        raise HTTPException(409, "Ein Chefwechsel oder eine Löschung dieser Firma läuft "
                                 "gerade — bitte gleich erneut versuchen")
    try:
        yield
    finally:
        await release(db, name, token=token)


_KONTO_GEAENDERT = ("Das Konto wurde zwischenzeitlich geändert oder wird gerade gelöscht "
                    "— bitte die Seite neu laden und noch einmal ansehen.")
_FIRMA_IN_LOESCHUNG = "Diese Firma wird gerade gelöscht — keine Änderungen mehr möglich"


async def _chef_befoerdern(target: dict, alte_rolle: Optional[str], fields: dict,
                           chef_wechsel: bool, admin: dict) -> None:
    """Konto zum Hauptchef machen — unter der Firmen-Sperre, mit Abgleich.

    Pruefung 14.09.2026 (M12) / Runde 12 (15.09.2026, Nr. 2/3) /
    Nachpruefung 20.09.2026: Zeiger, neue Rolle und Herabstufung aller
    anderen dealer-Konten laufen als EINE Transaktion (ohne Replica Set
    einzeln, jeder Schritt wiederholbar).

    Rollenpruefung 22.09.2026 (RP-028/RP-127/RP-278, RP-128/RP-279): Die Rolle
    schreibt jetzt NUR diese Funktion — innerhalb der Sperre. Nach dem Erwerb
    wird das Konto frisch gelesen (Rolle unveraendert, keine laufende
    Loeschung) und die Firma auf eine laufende Firmenloeschung geprueft;
    geschrieben wird mit Abgleich (Rolle, Grabstein), sonst 409."""
    dealer_id = target["dealer_id"]
    async with _chefwechsel_sperre(dealer_id):
        frisch = await db.users.find_one(
            {"id": target["id"]}, {"_id": 0, "role": 1, "loeschung": 1, "is_super_admin": 1})
        if not frisch or frisch.get("role") != alte_rolle \
                or (frisch.get("loeschung") or {}).get("status") == "laeuft":
            raise HTTPException(409, _KONTO_GEAENDERT)
        # Rollenprüfung 22.09.2026 (Review): zweite Linie hinter der Pruefung
        # in admin_update_user — das Betreiber-Konto wird hier nie zum Chef.
        if frisch.get("is_super_admin"):
            raise HTTPException(400, "Super-Admin-Rolle kann nicht geändert werden")
        firma = await db.dealers.find_one({"id": dealer_id},
                                          {"_id": 0, "user_id": 1, "loeschung": 1})
        if firma is None:
            raise HTTPException(409, "Die Firma dieses Kontos gibt es nicht (mehr) — "
                                     "ein Chef lässt sich hier nicht bestimmen.")
        if (firma.get("loeschung") or {}).get("status") == "laeuft":
            raise HTTPException(409, _FIRMA_IN_LOESCHUNG)
        chef = await db.users.find_one(
            {"dealer_id": dealer_id, "role": "dealer", "id": {"$ne": target["id"]}},
            {"_id": 0, "id": 1, "email": 1, "kontonummer": 1})
        if chef and not chef_wechsel:
            # Kontonummer (13.09.2026): Meldung nennt die Kontonummer
            # (die E-Mail ist optional). Die Nummern bleiben beim
            # Chefwechsel am Konto.
            raise HTTPException(
                400, "Diese Firma hat bereits einen Hauptaccount "
                     f"(Kontonummer {chef.get('kontonummer') or '—'}). Eine Firma hat genau "
                     "einen Chef. Soll dieses Konto der neue Chef werden "
                     "und der bisherige zum Sucher, dann chef_wechsel=true "
                     "mitschicken.")
        ziel = {"role": "dealer", "current_session_id": None}
        if fields.get("dealer_id"):
            # Pruefung 14.09.2026 (M17): Rueckkehr eines Zwischenhaendlers
            ziel["dealer_id"] = fields["dealer_id"]
        from deps import transaktion

        async def _wechseln(s):
            jetzt = now_iso()
            # Zuerst das Zielkonto (Abgleich auf Rolle + Grabstein), dann der
            # Zeiger (Abgleich auf die Firmenloeschung): scheitert einer, ist
            # in der Transaktion nichts geschrieben.
            r = await db.users.update_one(
                {"id": target["id"], "role": alte_rolle,
                 "is_super_admin": {"$ne": True},
                 "loeschung.status": {"$ne": "laeuft"}},
                {"$set": {**ziel, "updated_at": jetzt}}, session=s)
            if r.matched_count == 0:
                raise HTTPException(409, _KONTO_GEAENDERT)
            r = await db.dealers.update_one(
                {"id": dealer_id, "loeschung.status": {"$ne": "laeuft"}},
                {"$set": {"user_id": target["id"], "updated_at": jetzt}}, session=s)
            if r.matched_count == 0:
                raise HTTPException(409, _FIRMA_IN_LOESCHUNG)
            if chef:
                await db.users.update_one(
                    {"id": chef["id"], "role": "dealer"},
                    {"$set": {"role": "sucher", "current_session_id": None,
                              "updated_at": jetzt}}, session=s)
                await db.users.update_many(
                    {"dealer_id": dealer_id, "role": "dealer",
                     "id": {"$ne": target["id"]}},
                    {"$set": {"role": "sucher", "current_session_id": None,
                              "updated_at": jetzt}}, session=s)

        await transaktion(_wechseln)
    if chef:
        await log_activity_sicher(
            admin.get("dealer_id", ""), admin["id"], "admin.firma.chefwechsel",
            ref=dealer_id,
            meta={"alter_chef": chef.get("kontonummer") or chef["id"],
                  "neuer_chef": target.get("kontonummer") or target["id"]})


async def _grabstein_setzen(u: dict, admin: dict, rolle: Optional[str] = None) -> None:
    """Loeschung eines einzelnen Kontos beginnen (Grabstein am Konto).

    Nachpruefung Runde 14 (Befund 58): Reihenfolge wie bei
    cleanup_service.vertrag_endgueltig_loeschen — Grabstein zuerst,
    Nebendaten danach, das users-Dokument ZULETZT. Jeder Schritt ist
    wiederholbar: der Grabstein sperrt das Konto sofort (kein Login, keine
    Sitzung), ein zweiter Aufruf fuehrt die Loeschung zu Ende.

    Rollenpruefung 22.09.2026 (RP-029/RP-128/RP-279): mit `rolle` nur, solange
    das Konto noch diese Rolle hat (Abgleich) — sonst 409 statt einer
    Loeschung unter falschen Voraussetzungen."""
    jetzt = now_iso()
    filt = {"id": u["id"]}
    if rolle is not None:
        filt["role"] = rolle
    if ((u.get("loeschung") or {}).get("status")) == "laeuft":
        r = await db.users.update_one(
            filt,
            {"$set": {"loeschung.gestartet": jetzt, "active": False,
                      "current_session_id": None, "updated_at": jetzt},
             "$inc": {"loeschung.wiederaufnahmen": 1}})
    else:
        r = await db.users.update_one(
            filt,
            {"$set": {"loeschung": {"status": "laeuft", "gestartet": jetzt,
                                    "grund": "admin", "durch": admin["id"]},
                      "active": False, "current_session_id": None,
                      "updated_at": jetzt}})
    if r.matched_count == 0:
        raise HTTPException(409, _KONTO_GEAENDERT)


async def _zweitkonto_herabstufen(target: dict) -> None:
    """Rollenpruefung 22.09.2026 (RP-031/RP-130/RP-281): ein liegengebliebenes
    zweites dealer-Konto (NICHT der Zeiger) zum Sucher machen — unter der
    Firmen-Sperre und nur, solange es wirklich nicht der Hauptchef ist."""
    async with _chefwechsel_sperre(target["dealer_id"]):
        if await ist_haupt_chef({**target, "role": "dealer"}):
            raise HTTPException(409, _KONTO_GEAENDERT)
        r = await db.users.update_one(
            {"id": target["id"], "role": "dealer",
             "is_super_admin": {"$ne": True},        # Rollenprüfung 22.09.2026 (Review)
             "loeschung.status": {"$ne": "laeuft"}},
            {"$set": {"role": "sucher", "current_session_id": None,
                      "updated_at": now_iso()}})
        if r.matched_count == 0:
            raise HTTPException(409, _KONTO_GEAENDERT)


# ---------- Zugangs-Anfrage beim Anlegen schliessen (Kontonummer, 13.09.2026) ----------
# Nachpruefung 15.09.2026 (Konten Nr. 4/5): 300 s statt 120 s; die harte
# Grenze "eine Anfrage = ein Konto" sichert der Teil-Unique-Index
# zugangsanfrage_eindeutig (indizes.konto_indizes), nicht die Frist.
ANFRAGE_RESERVIERUNG_S = 300


async def _anfrage_reservieren(anfrage_id: Optional[str], art: str,
                               eingaben: Optional[dict] = None) -> Optional[dict]:
    """Offene Zugangs-Anfrage (type zugang) fuer die Kontenanlage reservieren —
    VOR dem Ziehen einer Nummer, damit ein Doppelklick nicht zwei Konten zu
    derselben Anfrage anlegt. Alt-Anfragen ohne `art` gelten als 'firma'.
    404: unbekannt; 400: andere Kontoart; 409: erledigt oder gerade in Anlage.
    Eine liegengebliebene Reservierung (Absturz) verfaellt nach 120 s."""
    if not anfrage_id:
        return None
    anfrage = await db.plan_requests.find_one({"id": anfrage_id, "type": "zugang"},
                                              {"_id": 0})
    if not anfrage:
        raise HTTPException(404, "Zugangs-Anfrage nicht gefunden")
    anfrage_art = anfrage.get("art") or "firma"
    if anfrage_art != art:
        raise HTTPException(400, f"Die Anfrage gehört zur Kontoart '{anfrage_art}', "
                                 f"nicht '{art}'")
    if anfrage.get("status") != "offen":
        raise HTTPException(409, "Die Zugangs-Anfrage ist bereits erledigt")
    # Pruefung 14.09.2026 (M7): Konnte die Anfrage nach der Anlage nicht auf
    # 'erledigt' gesetzt werden, lief die Reservierung nach 120 s ab und ein
    # zweites Konto liess sich zur selben Anfrage anlegen. Jedes Konto traegt
    # jetzt zugangsanfrage_id — existiert eines, wird die Anfrage hier
    # geschlossen und die Anlage abgelehnt.
    for sammlung in ("users", "driver_accounts"):
        konto = await db[sammlung].find_one({"zugangsanfrage_id": anfrage_id},
                                            {"_id": 0, "id": 1, "kontonummer": 1})
        if konto:
            await db.plan_requests.update_one(
                {"id": anfrage_id, "status": "offen"},
                {"$set": {"status": "erledigt", "erledigt_durch": "kontenanlage_nachgeholt",
                          "angelegt_konto_id": konto["id"],
                          "kontonummer": konto.get("kontonummer") or "", "updated_at": now_iso()},
                 "$unset": {"anlage_marke": "", "anlage_seit": ""}})
            raise HTTPException(409, "Zu dieser Zugangs-Anfrage wurde bereits ein Konto "
                                     f"angelegt (Kontonummer {konto.get('kontonummer') or '—'}).")
    # Nachpruefung 15.09.2026 (Konten, Freigabe-Bindung): die Freigabe gilt
    # GENAU dem Antrag — weichen E-Mail oder Firma der Eingabe von der
    # Anfrage ab, muss der Betreiber das mit daten_geaendert=true bestaetigen
    # (sonst: Anfrage A erledigt, Konto von Firma B angelegt).
    if eingaben and not eingaben.get("daten_geaendert"):
        abweichungen = []
        e_mail = (eingaben.get("email") or "").strip().lower()
        a_mail = (anfrage.get("contact_email") or "").strip().lower()
        if e_mail and a_mail and e_mail != a_mail:
            abweichungen.append(f"E-Mail (Anfrage: {a_mail}, Eingabe: {e_mail})")
        e_firma = " ".join((eingaben.get("company_name") or "").split()).lower()
        a_firma = " ".join((anfrage.get("company_name") or "").split()).lower()
        if e_firma and a_firma and e_firma != a_firma:
            abweichungen.append(f"Firma (Anfrage: {anfrage.get('company_name')}, "
                                f"Eingabe: {eingaben.get('company_name')})")
        if abweichungen:
            raise HTTPException(409, "Die Eingaben weichen von der Zugangs-Anfrage ab: "
                                     + "; ".join(abweichungen)
                                     + ". Bewusst abweichen: daten_geaendert=true mitschicken.")
    marke = str(uuid.uuid4())
    jetzt = datetime.now(timezone.utc)
    frist = (jetzt - timedelta(seconds=ANFRAGE_RESERVIERUNG_S)).isoformat()
    r = await db.plan_requests.update_one(
        {"id": anfrage_id, "type": "zugang", "status": "offen",
         "$or": [{"anlage_marke": {"$exists": False}},
                 {"anlage_seit": {"$lt": frist}}]},
        {"$set": {"anlage_marke": marke, "anlage_seit": jetzt.isoformat()}})
    if r.modified_count != 1:
        raise HTTPException(409, "Zu dieser Anfrage wird gerade ein Konto angelegt")
    anfrage["anlage_marke"] = marke
    return anfrage


async def _anfrage_freigeben(anfrage: Optional[dict]) -> None:
    """Reservierung nach einem Fehler der Anlage zuruecknehmen (nur die eigene)."""
    if not anfrage:
        return
    try:
        await db.plan_requests.update_one(
            {"id": anfrage["id"], "anlage_marke": anfrage["anlage_marke"]},
            {"$unset": {"anlage_marke": "", "anlage_seit": ""}})
    except Exception:
        log.exception("Reservierung der Anfrage %s nicht freigegeben", anfrage.get("id"))


async def _anfrage_abschliessen(anfrage: Optional[dict], konto_id: str,
                                kontonummer: str, admin: dict) -> None:
    """Anfrage 'erledigt' mit angelegt_konto_id und kontonummer. Das Konto
    steht schon — ein Fehler hier bricht die Anlage nicht mehr ab."""
    if not anfrage:
        return
    try:
        # Nachpruefung 15.09.2026 (Konten, Freigabe-Fingerabdruck): geschlossen
        # wird genau der Antrag mit dem Datenstand der Reservierung — Firma,
        # E-Mail und Art im Filter.
        r = await db.plan_requests.update_one(
            {"id": anfrage["id"], "anlage_marke": anfrage["anlage_marke"],
             "company_name": anfrage.get("company_name"),
             "contact_email": anfrage.get("contact_email"),
             "art": anfrage.get("art")},
            {"$set": {"status": "erledigt", "erledigt_durch": "kontenanlage",
                      "angelegt_konto_id": konto_id, "kontonummer": kontonummer,
                      "erledigt_von": _handelnder(admin), "updated_at": now_iso()},
             "$unset": {"anlage_marke": "", "anlage_seit": ""}})
        if r.matched_count == 0:
            log.error("Anfrage %s: Datenstand seit der Reservierung veraendert — "
                      "nicht als erledigt markiert (Konto %s)", anfrage.get("id"), konto_id)
            await betrieb.alarm(db, "zugangsanfrage_nicht_geschlossen", ref=anfrage.get("id") or "",
                                konto_id=konto_id, kontonummer=kontonummer)
    except Exception:
        log.exception("Anfrage %s nach Kontenanlage nicht geschlossen", anfrage.get("id"))


# Kontonummer (13.09.2026), Schritt 5: Die E-Mail ist nur noch Kontaktadresse
# und darf mehrfach vorkommen (keine 409 "E-Mail bereits registriert" mehr).
# Eine DuplicateKey bei der Anlage kann nur noch eine echte Dublette sein
# (Kontonummern-Dubletten wiederholt kontenanlage selbst).
_KONTO_DUBLETTE = ("Konto konnte wegen einer Dublette nicht angelegt werden — "
                   "bitte erneut versuchen")


async def _konto_sperre_aufheben(konto: dict) -> dict:
    """Kontonummer (13.09.2026): Passwort-Setzen durch den Betreiber hebt die
    Sperre des Konto-Limiters auf (einziger Weg bei 'Passwort vergessen').

    14.09.2026 (Ahmad: "Meldung 'Sperre aufgehoben', obwohl nie eine Sperre
    war"): liefert, was wirklich passiert ist — {sperre_aufgehoben, fehlversuche}
    — damit die Oberflaeche nicht pauschal von einer Sperre spricht."""
    nr = (konto or {}).get("kontonummer")
    if not nr:
        return {"sperre_aufgehoben": False, "fehlversuche": 0}
    try:
        from kontonummer import anmeldekennung
        from rate_limiter import login_konto_limiter, _LOGIN_KONTO_LIMIT
        k = anmeldekennung(nr)
        stand = int(await login_konto_limiter.stand(k))
        await login_konto_limiter.reset(k)
        return {"sperre_aufgehoben": bool(_LOGIN_KONTO_LIMIT) and stand >= _LOGIN_KONTO_LIMIT,
                "fehlversuche": stand}
    except Exception:
        log.exception("Konto-Sperre fuer %s nicht aufgehoben", nr)
        # Nachpruefung 15.09.2026 (Konten Nr. 3): nicht still "Passwort gesetzt"
        # melden — der Betreiber sieht den Hinweis, der Alarm bleibt offen.
        try:
            await betrieb.alarm(db, "konto_sperre_nicht_aufgehoben", ref=str(nr))
        except Exception:  # noqa: BLE001
            log.exception("Alarm konto_sperre_nicht_aufgehoben fuer %s nicht gesetzt", nr)
        return {"sperre_aufgehoben": False, "fehlversuche": 0, "unklar": True,
                "hinweis": SPERRE_UNKLAR_HINWEIS}


SPERRE_UNKLAR_HINWEIS = ("Die Anmeldesperre konnte nicht geprüft werden — scheitert die "
                         "Anmeldung weiter: python scripts/anmeldesperre_aufheben.py "
                         "<Kontonummer> auf dem Server ausführen.")


def _pw_persoenlich_400(pw: str, werte) -> None:
    """Nachpruefung 15.09.2026 (Anmeldung Nr. 9): kein Passwort aus eigenen
    Kontodaten (Kontonummer, Name, E-Mail, Firma, Fahrer-ID) — 400 mit Text."""
    try:
        pruefe_passwort(pw or "", persoenlich=[w for w in (werte or []) if w])
    except ValueError as exc:
        raise HTTPException(400, str(exc))


_KONTOART = {"dealer": ("firma", "/login", "Firmen-Hauptaccount (Chef)"),
             "sucher": ("sucher", "/login", "Sucher einer Firma"),
             "b2b_buyer": ("kaeufer", "/markt/login", "Zwischenhändler (B2B-Marktplatz)"),
             "admin": ("admin", "/login", "Admin-Konto")}


@router.get("/admin/konten/pruefen")
async def admin_konto_pruefen(kennung: str = Query(..., min_length=1, max_length=80),
                              admin=Depends(current_super_admin)):
    """Betreiber-Diagnose (14.09.2026, Ahmad: "Fahrer-Login mit 10004 gibt 401,
    B2B-Anmeldung geht nicht"): Zu welcher KONTOART gehoert die Kennung, wo
    meldet sie sich an, ist das Konto aktiv, hat es ein Passwort, und laeuft
    eine Anmeldesperre? Die Anmeldemasken selbst sagen bewusst nur
    "Kontonummer oder Passwort falsch" (keine Konto-Aufzaehlung fuer Fremde);
    hier sieht es nur der Super-Admin."""
    from kontonummer import (anmeldekennung, fahrer_normalisieren, kaeufer_normalisieren,
                             normalisieren, nummer_bedingung)
    from rate_limiter import login_konto_limiter, _LOGIN_KONTO_LIMIT, _LOGIN_KONTO_FENSTER
    roh = kennung.strip()
    kanon = normalisieren(roh) or kaeufer_normalisieren(roh) or fahrer_normalisieren(roh)
    grund = {"limit": _LOGIN_KONTO_LIMIT, "fenster_minuten": max(1, _LOGIN_KONTO_FENSTER // 60)}
    if not kanon:
        return {"gefunden": False, "kennung": roh, **grund,
                "hinweis": "Das ist weder eine Kontonummer (z. B. 10023 oder 10023-2) "
                           "noch ein Käufer-Code (z. B. 6FE7K2M) noch eine Fahrer-ID "
                           "(z. B. FD-7K2M9QX4)."}
    proj = {"_id": 0, "id": 1, "role": 1, "active": 1, "password_hash": 1, "dealer_id": 1,
            "company_name": 1, "first_name": 1, "last_name": 1, "contact_name": 1,
            "display_name": 1, "driver_code": 1, "kontonummer": 1, "created_at": 1,
            "login_ips_bekannt": 1, "login_geraete_bekannt": 1, "loeschung": 1}
    konto = await db.users.find_one({"kontonummer": nummer_bedingung(kanon)}, proj)
    fahrer = None if konto else await db.driver_accounts.find_one(
        {"kontonummer": nummer_bedingung(kanon)}, proj)
    stand = int(await login_konto_limiter.stand(anmeldekennung(kanon)))
    gesperrt = bool(_LOGIN_KONTO_LIMIT) and stand >= _LOGIN_KONTO_LIMIT
    if not konto and not fahrer:
        return {"gefunden": False, "kennung": kanon, "fehlversuche": stand,
                "anmeldesperre": gesperrt, **grund,
                "hinweis": "Kein Konto mit dieser Kennung — weder Firma, Sucher, "
                           "Zwischenhändler noch Fahrer. Bitte die Kontonummer aus der "
                           "Nutzer- bzw. Fahrerliste vergleichen."}
    doc = konto or fahrer
    if fahrer:
        art, seite, art_text = "fahrer", "/fahrer/login", "Fahrer-Konto (Fahrer-App)"
        name = doc.get("display_name") or ""
    else:
        art, seite, art_text = _KONTOART.get(
            doc.get("role"), ("unbekannt", "/login", doc.get("role") or "?"))
        name = (doc.get("company_name") or doc.get("contact_name")
                or f"{doc.get('first_name') or ''} {doc.get('last_name') or ''}".strip())
    firma = None
    if doc.get("dealer_id") and not fahrer:
        f = await db.dealers.find_one({"id": doc["dealer_id"]}, {"_id": 0, "company_name": 1})
        firma = (f or {}).get("company_name")
        if not name:
            name = firma or ""       # Chef: der Firmenname steht am Haendlerprofil
    # Runde 15: dieselbe Bedeutung wie die Anmeldung — bei users heisst ein
    # fehlendes active-Feld "gesperrt" (Login: `if not active`), bei Fahrern
    # "aktiv"; Migration 8 schreibt das Feld ohnehin in jedes Konto.
    aktiv = doc.get("active", fahrer is not None) is not False
    hinweise = [f"{art_text}: Anmeldung unter {seite}."]
    if art == "fahrer":
        hinweise.append("Nicht unter /login (Firmen) und nicht im B2B-Marktplatz — dort gibt "
                        "es für diese Nummer immer „Kontonummer oder Passwort falsch“.")
    elif art in ("firma", "sucher"):
        hinweise.append("Nicht in der Fahrer-App und nicht im B2B-Marktplatz anmelden.")
    elif art == "kaeufer":
        hinweise.append("Nicht in der Fahrer-App anmelden; unter /login geht es ebenfalls.")
    if not aktiv:
        hinweise.append("Das Konto ist GESPERRT (deaktiviert) — Anmeldung wird abgelehnt. "
                        "In der Liste „Entsperren“.")
    if (doc.get("loeschung") or {}).get("status") == "laeuft":
        hinweise.append("Die Löschung dieses Kontos läuft — keine Anmeldung mehr möglich.")
    if not doc.get("password_hash"):
        hinweise.append("Es ist KEIN Passwort gesetzt — bitte „Passwort setzen“.")
    if gesperrt:
        hinweise.append(f"Anmeldesperre aktiv: {stand} Fehlversuche in {grund['fenster_minuten']} "
                        f"Minuten (Grenze {_LOGIN_KONTO_LIMIT}). „Passwort setzen“ hebt sie auf; "
                        "von bekannten Geräten des Nutzers greift sie nicht.")
    elif stand:
        hinweise.append(f"{stand} Fehlversuch(e) im aktuellen Fenster — noch keine Sperre.")
    if aktiv and doc.get("password_hash") and not gesperrt:
        hinweise.append("Konto ist in Ordnung. Bleibt es bei „falsch“: Passwort neu setzen "
                        "(Tippfehler, Leerzeichen am Ende, Groß-/Kleinschreibung).")
    return {"gefunden": True, "kennung": kanon, "art": art, "art_text": art_text,
            "anmeldeseite": seite, "name": name, "firma": firma, "aktiv": aktiv,
            "passwort_gesetzt": bool(doc.get("password_hash")),
            "fehlversuche": stand, "anmeldesperre": gesperrt, **grund,
            "driver_code": doc.get("driver_code"), "konto_id": doc.get("id"),
            "bekannte_geraete": len(doc.get("login_ips_bekannt") or []),
            # Rollenpruefung 22.09.2026 (RP-557): gemerkte Geraete (App/Browser)
            "bekannte_geraete_schluessel": len(doc.get("login_geraete_bekannt") or []),
            "erstellt_am": doc.get("created_at"), "hinweise": hinweise}


# ---------- Users ----------
@router.post("/admin/users")
async def admin_create_user(body: AdminUserIn, admin=Depends(current_super_admin)):
    # Haertung 09/2026: E-Mail wie bei Registrierung/Sucher normalisieren und
    # schreibungsunabhaengig pruefen — vorher konnten "Chef@X.de" und
    # "chef@x.de" als zwei Konten existieren (Login trifft dann das falsche).
    # Kontonummer (13.09.2026): Die E-Mail ist optional (Kontaktadresse).
    email = (body.email or "").strip().lower() or None
    # Nachpruefung Runde 14 (Befund 50): Ablauf VOR dem ersten Insert
    # pruefen — ein 400 hier laesst weder Konto noch Firmenprofil zurueck.
    # Kontonummer (13.09.2026): Reihenfolge Ablauf -> Anfrage -> Nummer.
    ablauf_eingabe = _ablaufdatum_pruefen_400(body.expires_at)
    _pw_persoenlich_400(body.password, [body.company_name, body.contact_person, email])
    anfrage = await _anfrage_reservieren(
        body.anfrage_id, "firma",
        eingaben={"email": email, "company_name": body.company_name,
                  "daten_geaendert": body.daten_geaendert})
    user_id = str(uuid.uuid4())
    dealer_id = str(uuid.uuid4())
    chef_doc = {
        "id": user_id,
        "password_hash": await hash_password_async(body.password),
        "role": "dealer", "active": body.active if body.active is not None else True,
        "dealer_id": dealer_id, "current_session_id": None,
        "created_at": now_iso(),
    }
    if email:                       # ohne Angabe fehlt das Feld (nie "")
        chef_doc["email"] = email
    if anfrage:
        chef_doc["zugangsanfrage_id"] = anfrage["id"]      # Pruefung 14.09.2026 (M7)
    # Kontonummer (13.09.2026): erst die Firma mit Nummer, dann der Chef mit
    # kontonummer = str(kunden_nr) — kontenanlage raeumt die Firma bei jedem
    # Fehler des Chef-Inserts wieder weg (kein Profil ohne Konto).
    from kontenanlage import firma_mit_chef_anlegen
    try:
        erg = await firma_mit_chef_anlegen(db, {
        "id": dealer_id, "user_id": user_id, "company_name": body.company_name,
        "contact_person": body.contact_person.strip(), "phone": body.phone.strip(),
        "email": email or "",
        "address": "", "zip_code": "", "city": "", "logo_url": "",
        "comparison_rules": DEFAULT_RULES,
        "export_rules": DEFAULT_EXPORT_RULES,
        "active_profile": "inland",
        # Vorlage Ahmad 20.09.2026: ausformulierte Standardtexte fuer Mail,
        # WhatsApp, die drei Folge-Mails und die Besonderen Vereinbarungen —
        # alle mit Platzhaltern, die der Server beim Erstellen einsetzt.
        # Jede Firma kann sie in den Einstellungen ueberschreiben.
        **_vorlagen.STARTWERTE,
        "default_terms": "",
        # Runde 26: ein Feld fuer Vertragsbedingungen (vier Klauseln + AGB).
        "digital_vertragstext": _vertragstext_start_admin(),
        "created_at": now_iso(),
        }, chef_doc)
    except DuplicateKeyError:
        await _anfrage_freigeben(anfrage)
        raise HTTPException(409, _KONTO_DUBLETTE)
    except HTTPException:
        await _anfrage_freigeben(anfrage)
        raise
    except Exception:
        await _anfrage_freigeben(anfrage)
        log.exception("admin_create_user: Firma/Konto-Insert fehlgeschlagen")
        raise HTTPException(500, "Firma anlegen fehlgeschlagen — bitte erneut versuchen")
    antwort = {"ok": True, "user_id": user_id, "dealer_id": dealer_id,
               "kunden_nr": erg["kunden_nr"], "kontonummer": erg["kontonummer"]}
    # plan_type "none" (Betreiber-Modell 09/2026): Firmen-Hauptaccount ohne
    # jedes Abo anlegen — Verkaufen/Verwalten ist kostenlos, Sucher-Abos
    # werden einzeln nach Rechnungszahlung freigeschaltet.
    if body.plan_type == "none":
        await _anfrage_abschliessen(anfrage, user_id, erg["kontonummer"], admin)
        await log_activity_sicher(admin.get("dealer_id", ""), admin["id"],
                           "admin.user.erstellt", ref=user_id,
                           meta={"plan": "none", "kontonummer": erg["kontonummer"],
                                 "anfrage": bool(anfrage)})
        return antwort
    expires = ablauf_eingabe
    if not expires and body.plan_type in ("monthly", "trial"):
        expires = (datetime.now(timezone.utc) + timedelta(days=30 if body.plan_type == "monthly" else 14)).isoformat()
    if not expires and body.plan_type == "yearly":
        expires = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
    # Nachpruefung Runde 14 (Befund 49): Abo-Insert mit Rollback wie beim
    # Firmenprofil — vorher blieb bei einem Fehler hier (DB-Ausfall
    # zwischen zwei Inserts) eine Firma OHNE Abo stehen, und der zweite
    # Versuch lief auf 409 "E-Mail bereits registriert". Der tote
    # "lifetime"-Zweig ist entfernt (Literal erlaubt nur none/monthly/
    # yearly/trial).
    try:
        await db.subscriptions.insert_one({
            "id": str(uuid.uuid4()), "dealer_id": dealer_id,
            "plan": body.plan_type, "status": "active",
            "expires_at": expires,
            # Rollenpruefung 22.09.2026 (RP-145 Nr. 3): Firmen-Abos tragen ein
            # Kennzeichen — Grundlage fuer einen Teil-Unique-Index
            # "ein aktives Firmen-Abo je Firma" (indizes.py, Uebergabe Betrieb).
            "art": "firma",
            "created_at": now_iso(),
        })
    except Exception:
        # Pruefung 14.09.2026 (M8): kein atomarer Rollback moeglich — jeder
        # Schritt einzeln; was liegen bleibt, meldet ein Betriebsalarm, und
        # cleanup_service.konten_ohne_firma_sperren sperrt Konten ohne Firma.
        for sammlung, ident in (("users", user_id), ("dealers", dealer_id)):
            try:
                await db[sammlung].delete_one({"id": ident})
            except Exception:  # noqa: BLE001
                log.exception("admin_create_user: Rollback %s/%s fehlgeschlagen", sammlung, ident)
                await betrieb.alarm(db, "firmenanlage_rollback_offen", ref=ident,
                                    sammlung=sammlung, dealer_id=dealer_id, user_id=user_id)
        await _anfrage_freigeben(anfrage)
        log.exception("admin_create_user: Abo-Insert fehlgeschlagen")
        raise HTTPException(500, "Firma anlegen fehlgeschlagen — bitte erneut versuchen")
    await _anfrage_abschliessen(anfrage, user_id, erg["kontonummer"], admin)
    await log_activity_sicher(admin.get("dealer_id", ""), admin["id"], "admin.user.erstellt",
                       ref=user_id, meta={"plan": body.plan_type,
                                          "kontonummer": erg["kontonummer"],
                                          "anfrage": bool(anfrage)})
    return antwort


# Pruefbericht 20.09.2026 (AD-19/AD-33): serverseitige Suche fuer Nutzer- und
# Fahrerliste — vorher suchte die Oberflaeche nur in der ersten Seite (1000
# Konten bzw. 2000 Fahrer), aeltere Konten waren unauffindbar. Der Suchtext
# wird als Literal gesucht (re.escape, keine Regex-Sonderzeichen des Nutzers),
# Gross-/Kleinschreibung egal, hoechstens SUCHE_MAX_ZEICHEN Zeichen.
SUCHE_MAX_ZEICHEN = 80


def _suchmuster(q: Optional[str]) -> Optional[dict]:
    q = (q or "").strip()[:SUCHE_MAX_ZEICHEN]
    if not q:
        return None
    return {"$regex": re.escape(q), "$options": "i"}


async def _firmen_ids_zu(q: Optional[str], muster: dict) -> list:
    """Firmen, deren Name den Suchtext enthaelt — oder deren Kundennummer ihm
    entspricht ('#10999' wie in der Nutzerliste, oder '10999')."""
    bedingung = [{"company_name": muster}]
    ziffern = (q or "").strip().lstrip("#")
    if ziffern.isdigit() and len(ziffern) <= 12:      # int64-sicher
        bedingung.append({"kunden_nr": int(ziffern)})
    return [d["id"] async for d in db.dealers.find(
        {"$or": bedingung}, {"_id": 0, "id": 1}).limit(500)]


@router.get("/admin/users")
async def admin_list_users(response: Response, _=Depends(current_admin),
                           page: int = 1, limit: int = 1000,
                           q: Optional[str] = None):
    """Nutzerliste MIT Firmenname und Abo-Status — in 3 Abfragen gesamt
    statt 2 Abfragen JE NUTZER (vorher: bis zu 2001 Abfragen bei 1000
    Nutzern). Standard-Limit 1000 = bisheriges Verhalten, damit die
    Admin-Oberflaeche ohne Umbau denselben Bestand sieht; page/limit
    stehen fuer kuenftige Pagination bereit.

    AD-19: `q` sucht serverseitig in Kontonummer, E-Mail, Benutzername,
    Vor-/Nachname, Kontaktname und ueber die Firma (Name oder Kundennummer)."""
    limit = max(1, min(int(limit or 1000), 1000))
    # Nachpruefung 13.09.2026 (wie #52): page nach oben begrenzen — sonst
    # sprengt (page - 1) * limit int64 und pymongo wirft OverflowError (500).
    page = max(1, min(int(page or 1), 10 ** 6))
    filter_: dict = {}
    muster = _suchmuster(q)
    if muster:
        oder = [{"kontonummer": muster}, {"email": muster}, {"username": muster},
                {"first_name": muster}, {"last_name": muster}, {"contact_name": muster},
                {"company_name": muster}]
        firmen = await _firmen_ids_zu(q, muster)
        if firmen:
            oder.append({"dealer_id": {"$in": firmen}})
        filter_ = {"$or": oder}
    # Runde 12: Sitzungs-ID gehoert nicht in Admin-Antworten.
    # Rollenpruefung 22.09.2026 (Welle 3): 'id' als zweiter Sortierschluessel —
    # bei gleichem created_at (Altbestand, Import) ist die Reihenfolge sonst
    # nicht festgelegt, und ueber die Seitengrenze hinweg konnte ein Konto
    # auf keiner Seite oder auf zweien auftauchen.
    users = await db.users.find(filter_, {"_id": 0, "password_hash": 0, "mfa.secret": 0,
                                           "mfa.pending_secret": 0, "mfa.wiederherstellung": 0,
                                           "current_session_id": 0}) \
        .sort([("created_at", -1), ("id", 1)]).skip((page - 1) * limit).to_list(limit + 1)
    # Pruefbericht 20.09.2026 (AD-16): eine Zeile mehr lesen — gibt es sie,
    # ist die Liste gekuerzt (X-Truncated), die Oberflaeche sagt es.
    users = _seite_kopf(response, users, limit)
    for u in users:                     # nur der Schalter, nie das Geheimnis
        u["mfa_aktiv"] = bool((u.pop("mfa", None) or {}).get("aktiv"))
    dealer_ids = list({u.get("dealer_id") for u in users if u.get("dealer_id")})
    dealers = {d["id"]: d async for d in db.dealers.find(
        {"id": {"$in": dealer_ids}},
        {"_id": 0, "id": 1, "company_name": 1, "kunden_nr": 1, "user_id": 1})}
    # Rollenpruefung 22.09.2026 (RP-033/RP-132): Hauptchef je Firma = Zeiger
    # dealers.user_id; Firmen ohne Zeiger (Altbestand): aeltestes dealer-Konto
    # (eine Sammelabfrage). Die Oberflaeche nutzt `ist_chef` statt der rohen
    # Rolle (Firmensperre, Loeschung der ganzen Firma).
    haupt_je_firma = {d_id: d.get("user_id") for d_id, d in dealers.items() if d.get("user_id")}
    ohne_zeiger = [d_id for d_id in dealer_ids if d_id not in haupt_je_firma]
    if ohne_zeiger:
        async for row in db.users.aggregate([
                {"$match": {"dealer_id": {"$in": ohne_zeiger}, "role": "dealer"}},
                {"$sort": {"created_at": 1}},
                {"$group": {"_id": "$dealer_id", "id": {"$first": "$id"}}}]):
            haupt_je_firma[row["_id"]] = row["id"]

    def _ist_chef(u) -> bool:
        return (u.get("role") == "dealer" and bool(u.get("dealer_id"))
                and haupt_je_firma.get(u["dealer_id"]) == u.get("id"))
    # Juengstes HAENDLER-Abo je Firma — dieselbe Regel wie
    # deps.get_subscription_status: Dokumente ohne subject_user_id-Feld und
    # Alt-Dokumente mit explizitem null zaehlen gleich, das juengste nicht
    # ersetzte gilt (Pruefbericht 20.09.2026, R1-16: vorher gewann "Feld
    # fehlt" hier wie dort, egal wie alt).
    newest_subs = {}
    async for row in db.subscriptions.aggregate([
        {"$match": {"dealer_id": {"$in": dealer_ids}, "status": {"$ne": "ersetzt"},
                    "$or": [{"subject_user_id": {"$exists": False}},
                            {"subject_user_id": None}]}},
        {"$sort": {"created_at": -1}},
        {"$group": {"_id": "$dealer_id", "sub": {"$first": "$$ROOT"}}},
    ]):
        newest_subs[row["_id"]] = row["sub"]
    # Persoenliche Abos fuer ALLE Konten (Sucher UND Chef) — dieselbe Regel
    # wie deps.subscription_for (Audit 09/2026: Anzeige und Zugriff duerfen
    # nicht auseinanderlaufen): Chef = persoenlich, sonst altes Firmen-Abo.
    konto_ids = [u["id"] for u in users if u.get("role") in ("sucher", "dealer")]
    persoenlich = {}
    if konto_ids:
        async for row in db.subscriptions.aggregate([
            {"$match": {"subject_user_id": {"$in": konto_ids},
                        "status": {"$ne": "ersetzt"}}},
            {"$sort": {"created_at": -1}},
            {"$group": {"_id": "$subject_user_id", "sub": {"$first": "$$ROOT"}}},
        ]):
            persoenlich[row["_id"]] = row["sub"]

    def _abo(u):
        if u.get("role") == "sucher":
            return sub_status_from_doc(persoenlich.get(u["id"]))
        pers = sub_status_from_doc(persoenlich.get(u["id"]))
        # Rollenpruefung 22.09.2026 (RP-033): Firmen-Abo-Rueckfall nur fuer
        # den Hauptchef — wie deps.subscription_for.
        if pers["active"] or not _ist_chef(u):
            return pers
        firma = sub_status_from_doc(newest_subs.get(u.get("dealer_id")))
        return firma if firma["active"] else pers

    return [{**u,
             "company_name": dealers.get(u.get("dealer_id"), {}).get("company_name"),
             "kunden_nr": dealers.get(u.get("dealer_id"), {}).get("kunden_nr"),
             "ist_chef": _ist_chef(u),
             "subscription": _abo(u)}
            for u in users]


@router.put("/admin/users/{user_id}")
async def admin_update_user(user_id: str, body: dict = Body(...), admin=Depends(current_super_admin)):
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(404, "Nutzer nicht gefunden")
    # Runde 12 (15.09.2026, Nr. 5): ein Konto in laufender Loeschung wird nicht
    # mehr veraendert (Rolle, Aktivstatus) — sonst laeuft die Loeschkaskade
    # gegen ein wiederbelebtes Konto.
    if (target.get("loeschung") or {}).get("status") == "laeuft":
        raise HTTPException(409, "Dieses Konto wird gerade gelöscht — keine Änderungen mehr möglich")
    fields = {}
    for k in ("active", "role"):
        if k in body:
            fields[k] = body[k]
    # ROLLENAENDERUNGEN sind Super-Admin-Sache (PR-Review 09/2026): sonst
    # kann jeder normale Admin beliebige Nutzer zu weiteren Admins machen
    # (Eskalation) oder Kollegen degradieren. Erlaubte Zielrollen sind
    # zudem fest verdrahtet. Super-Admin durch Dependency gesichert
    # (current_super_admin; Pruefbericht 20.09.2026, R1-37: toter Zweig weg).
    if "role" in fields:
        # Runde 12: "admin" ist keine vergebbare Rolle mehr — es gibt genau
        # einen Betreiber (Super-Admin), weitere Admin-Konten sind abgeschafft.
        if fields["role"] == "admin":
            raise HTTPException(400, "Es gibt nur den Super-Admin als Betreiber — "
                                     "weitere Admin-Konten sind nicht vorgesehen.")
        if fields["role"] not in ("dealer", "sucher", "b2b_buyer"):
            raise HTTPException(400, "Unbekannte Rolle")
        # Rollenprüfung 22.09.2026 (Review): Der Schutz des Betreiber-Kontos
        # muss VOR jedem Schritt des Rollenwechsels greifen. Seit Befoerderung
        # und Herabstufung die Rolle selbst schreiben (unter der Firmen-Sperre,
        # _chef_befoerdern/_zweitkonto_herabstufen), kam die Pruefung weiter
        # unten erst NACH dem Schreiben: PUT role=dealer auf den Super-Admin
        # (sein Seed-Konto hat eine dealer_id) machte ihn zum Haendler und
        # meldete ihn ab — danach 403 auf allen Betreiber-Routen bis zum
        # Neustart. Auch der Zwischenhaendler-Zweig (Fahrzeuge/Vorgaenge an
        # den Chef, Abos ersetzt) lief vor der Pruefung.
        if target.get("is_super_admin") and fields["role"] != target.get("role"):
            raise HTTPException(400, "Super-Admin-Rolle kann nicht geändert werden")
        # Pruefbericht 09/2026: bisher wurde nur der NAME der Zielrolle
        # geprueft, nicht ob das Konto dazu passt. Aus einem Zwischen-
        # haendler (gehoert zu keiner Firma) liess sich so ein "Sucher ohne
        # Firma" machen: formal Sucher, praktisch ueberall ausgesperrt.
        # Und ein Chef liess sich degradieren, obwohl seine Firma dann
        # ohne Chef dastand.
        neue_rolle = fields["role"]
        alte_rolle = target.get("role")
        if neue_rolle != alte_rolle:
            if neue_rolle in ("dealer", "sucher") and not target.get("dealer_id")                     and target.get("ehemalige_dealer_id") and await db.dealers.count_documents(
                        {"id": target["ehemalige_dealer_id"]}, limit=1):
                # Pruefung 14.09.2026 (M17): ein Zwischenhaendler, der frueher
                # Sucher dieser Firma war, kehrt zu ihr zurueck.
                target["dealer_id"] = fields["dealer_id"] = target["ehemalige_dealer_id"]
            if neue_rolle in ("dealer", "sucher") and not target.get("dealer_id"):
                raise HTTPException(
                    400, "Dieses Konto gehört zu keiner Firma. Ein Wechsel zu "
                         "Händler oder Sucher würde ein Konto erzeugen, das "
                         "sich nirgends anmelden kann. Bitte stattdessen eine "
                         "Firma anlegen und den Zugang dort einrichten.")
            from kontonummer import KAEUFER_MUSTER
            if neue_rolle == "sucher" and alte_rolle == "b2b_buyer" \
                    and KAEUFER_MUSTER.match(str(target.get("kontonummer") or "")):
                # Runde 15 (15.09.2026): ein Zwischenhaendler behielt beim
                # Rueckwechsel seinen Kaeufer-Code — die Anmeldung sucht
                # Kaeufer-Codes aber nur bei role=b2b_buyer: ein korrekt
                # aktiver Sucher mit Firma bekam dauerhaft 401. Jetzt bekommt
                # er eine Sucher-Nummer seiner Firma (<kunden_nr>-<zusatz>).
                from kontenanlage import kunden_nr_sicherstellen, naechster_sucher_zusatz
                from kontonummer import sucher_nummer
                firma_doc = await kunden_nr_sicherstellen(db, target["dealer_id"])
                kunden_nr = int(firma_doc["kunden_nr"])
                zusatz = await naechster_sucher_zusatz(db, target["dealer_id"], kunden_nr)
                fields["kontonummer"] = sucher_nummer(kunden_nr, zusatz)
                fields["kontonummer_basis"] = kunden_nr
                fields["kontonummer_art"] = "sucher"
                fields["kontonummer_vorher"] = target.get("kontonummer")
            rolle_geschrieben = False
            if neue_rolle == "dealer" and target.get("dealer_id"):
                # Runde 11: GENAU EIN Hauptaccount je Firma. current_chef()
                # erkennt den Chef an der Rolle — ein zweites dealer-Konto
                # haette sofort volle Chef-Rechte, und die Firmensperre
                # ("gesperrter Chef = gesperrte Firma") wuerde je nach
                # gefundenem Datensatz zufaellig greifen oder nicht.
                # Rollenpruefung 22.09.2026 (RP-028/RP-127/RP-278, RP-128):
                # Befoerderung und Chefwechsel laufen komplett unter der
                # Firmen-Sperre und schreiben die Rolle SELBST (mit Abgleich).
                # Vorher schrieb der allgemeine Abschluss-Write unten die Rolle
                # NACH der Freigabe noch einmal — ein zweiter Chefwechsel
                # dazwischen hatte dieses Konto schon zum Sucher gemacht, und
                # es wurde wieder "dealer" (zwei dealer-Konten).
                await _chef_befoerdern(target, alte_rolle, fields,
                                       bool(body.get("chef_wechsel")), admin)
                rolle_geschrieben = True
            if alte_rolle == "dealer" and target.get("dealer_id"):
                # Runde 12: Der Hauptaccount wird NIE direkt herabgestuft.
                # Vorher war es erlaubt, sobald kein weiterer Zugang
                # existierte — genau dann blieb die Firma ohne Chef zurueck;
                # und ein Sucher zaehlte faelschlich als "Ersatz". Der
                # einzige Weg ist der Chefwechsel (Nachfolger mit
                # chef_wechsel=true befoerdern), der den alten Chef selbst
                # zum Sucher macht.
                # Rollenpruefung 22.09.2026 (RP-031/RP-130/RP-281): gemeint
                # ist der HAUPTaccount (Zeiger dealers.user_id). Ein
                # liegengebliebenes zweites dealer-Konto arbeitet laengst als
                # Sucher (current_firma) — es darf zum Sucher werden, damit der
                # Betreiber den Altbestand bereinigen kann (unter der Sperre).
                if neue_rolle != "sucher" or await _ist_hauptchef_konto(target):
                    raise HTTPException(
                        400, "Der Händler-Hauptaccount kann nicht herabgestuft "
                             "werden — die Firma braucht immer genau einen Chef. "
                             "Nachfolger bestimmen: dessen Konto mit role=dealer "
                             "und chef_wechsel=true befördern; der bisherige Chef "
                             "wird dabei zum Sucher.")
                await _zweitkonto_herabstufen(target)
                rolle_geschrieben = True
            if neue_rolle == "b2b_buyer" and target.get("dealer_id"):
                # Pruefung 14.09.2026 (M17): Ein Sucher, der Zwischenhaendler
                # wird, haengt sonst weiter an seiner Firma (dealer_id) und
                # besitzt ihre Fahrzeuge. Firmenbindung loesen, Fahrzeuge an
                # den Chef, aus den Mitbearbeitern raus, Abo-Anfragen weg.
                # Rollenpruefung 22.09.2026 (RP-046/RP-145 Nr. 1, RP-031): der
                # Chef ist der ZEIGER (nicht das aelteste dealer-Konto); ohne
                # Chef bricht der Wechsel ab (wie das Loeschen eines Suchers,
                # G14), und Kaufvorgaenge, Vertraege und Termine gehen wie
                # beim Loeschen mit an den Chef (vorgang_uebergeben) — das
                # Konto verlaesst die Firma, ihre Daten bleiben bei ihr.
                firma = {"dealer_id": target["dealer_id"]}
                chef_neu = await _chef_fuer_uebernahme(target["dealer_id"], target["id"])
                if not chef_neu:
                    raise HTTPException(409, "Die Firma hat keinen Hauptaccount — erst einen "
                                             "Chef bestimmen, dann das Konto umstellen")
                await db.vehicles.update_many(
                    {**firma, "owner_user_id": target["id"]},
                    {"$set": {"owner_user_id": chef_neu,
                              "uebernommen_von": target["id"], "updated_at": now_iso()}})
                from routes.bestand import vorgang_uebergeben
                uebergabe = await vorgang_uebergeben(target["dealer_id"], None,
                                                     target["id"], chef_neu)
                if any(uebergabe.values()):
                    log.info("Konto %s wird Zwischenhaendler: Vorgang an Chef %s "
                             "uebergeben: %s", target["id"], chef_neu, uebergabe)
                await db.vehicles.update_many(
                    {**firma, "mitbearbeiter_ids": target["id"]},
                    {"$pull": {"mitbearbeiter_ids": target["id"]}})
                await db.plan_requests.delete_many(
                    {"subject_user_id": target["id"], "status": "offen"})
                await db.subscriptions.update_many(
                    {"subject_user_id": target["id"], "status": "active"},
                    {"$set": {"status": "ersetzt", "ersetzt_durch": "rollenwechsel",
                              "updated_at": now_iso()}})
                fields["dealer_id"] = None
                fields["ehemalige_dealer_id"] = target["dealer_id"]
            if rolle_geschrieben:
                # Rolle, Sitzungsende (und ggf. dealer_id) stehen schon — unter
                # der Sperre geschrieben. Nicht noch einmal ausserhalb.
                fields.pop("role", None)
                fields.pop("dealer_id", None)
            else:
                # Runde 12: Jede Rollenaenderung beendet die laufende Sitzung.
                # current_user() liest die Rolle bei jedem Request frisch — ein
                # bestehendes Haendler-Token bekam so ohne neue Anmeldung (und
                # ohne zweiten Faktor) Admin-Rechte.
                fields["current_session_id"] = None
        else:
            # Rollenpruefung 22.09.2026 (RP-028): unveraenderte Rolle nicht
            # blind zurueckschreiben — ein paralleler Chefwechsel koennte
            # das Konto inzwischen zum Sucher gemacht haben.
            if neue_rolle == "dealer" and target.get("dealer_id") \
                    and body.get("chef_wechsel") and not await _ist_hauptchef_konto(target):
                # Ein abgebrochener Chefwechsel ohne Replica Set (Rolle schon
                # gesetzt, Zeiger noch alt) wird durch Wiederholen zu Ende gefuehrt.
                await _chef_befoerdern(target, alte_rolle, fields, True, admin)
            fields.pop("role", None)
    # Admin-Konten verwalten nur Super-Admins — Super-Admin durch Dependency
    # gesichert (current_super_admin; R1-37: toter Zweig weg).
    if target.get("is_super_admin"):
        if "role" in fields and fields["role"] != "admin":
            raise HTTPException(400, "Super-Admin-Rolle kann nicht geändert werden")
        if "active" in fields and not fields["active"]:
            raise HTTPException(400, "Super-Admin kann nicht gesperrt werden")
    # Selbst-Sperre verhindern
    if target.get("id") == admin.get("id") and "active" in fields and not fields["active"]:
        raise HTTPException(400, "Du kannst dich nicht selbst sperren")
    if "password" in body:
        # Nachpruefung 15.09.2026 (Anmeldung Nr. 1/2/10): Passwoerter werden
        # nur noch ueber die eigenen Endpunkte gesetzt — fremde Konten per
        # POST /admin/users/{id}/password, das eigene per /admin/me/password
        # (mit aktuellem Passwort). Rolle, Sperre und Passwort in EINEM
        # untypisierten Aufruf gibt es nicht mehr.
        raise HTTPException(400, "Passwörter werden hier nicht gesetzt — bitte "
                                 "POST /admin/users/{id}/password (fremdes Konto) bzw. "
                                 "/admin/me/password (eigenes Konto) verwenden.")
    # Nachpruefung Runde 14 (Befund 18): Sperren ueber PUT muss dasselbe
    # tun wie POST /active — vorher blieb current_session_id stehen, das
    # alte Token war nach dem Entsperren wieder gueltig (auch ein
    # gestohlenes), und die Sucher einer per PUT gesperrten Firma
    # behielten ihre Sitzungen. Beim Entsperren eines gesperrten Kontos
    # wird die Sitzung ebenfalls verworfen (Altbestand, der noch ueber den
    # alten PUT gesperrt wurde): eine Sperre darf nie eine Sitzung
    # "konservieren".
    sucher_abgemeldet = 0
    if "active" in fields:
        fields["active"] = bool(fields["active"])
        if not fields["active"]:
            fields["current_session_id"] = None
            # Rollenpruefung 22.09.2026 (RP-151, RP-131/RP-282): nur der
            # HAUPTchef sperrt die Firma — dann enden die Sitzungen ALLER
            # anderen Konten der Firma (auch liegengebliebener dealer-Konten).
            if await _ist_hauptchef_konto(target):
                sucher_abgemeldet = await _firmenkonten_abmelden(
                    target["dealer_id"], target["id"])
        elif not target.get("active", True):
            fields["current_session_id"] = None
    if fields:
        await db.users.update_one({"id": user_id},
                                  {"$set": {**fields, "updated_at": now_iso()}})
    if "plan_type" in body:
      # Pruefung 14.09.2026 (G8): auch der alte Plan-Pfad laeuft unter der
      # Abo-Sperre des Kontos (siehe _sperre / admin_sucher_abo).
      async with _sperre(f"abo:{user_id}", _handelnder(admin)):
        u = await db.users.find_one({"id": user_id})
        if not u:
            raise HTTPException(404)
        plan = body["plan_type"]
        # Pruefung 14.09.2026 (M14/M15): dieselben festen Werte wie bei der
        # Firmenanlage — "lifetime" (unbefristet) und Freitext sind hier
        # nicht mehr anlegbar; Altbestand bleibt lesbar (deps.ABO_PLAENE_ERLAUBT).
        if plan not in ("monthly", "yearly", "trial"):
            raise HTTPException(400, "plan_type muss monthly, yearly oder trial sein")
        # Nachpruefung Runde 14 (Befund 50): derselbe Altpfad wie bei der
        # Firmenanlage — Rohwert nie ungeprueft ins Abo schreiben.
        expires = _ablaufdatum_pruefen_400(body.get("expires_at"))
        if not expires:
            expires = (datetime.now(timezone.utc) + timedelta(
                days={"monthly": 30, "trial": 14, "yearly": 365}[plan])).isoformat()
        sub_doc = {
            "id": str(uuid.uuid4()), "dealer_id": u.get("dealer_id"),
            "plan": plan, "status": "active",
            "expires_at": expires, "created_at": now_iso(),
        }
        # Sucher-Unteraccounts haben ein PERSÖNLICHES Abo (Phase 2).
        # Rollenpruefung 22.09.2026 (RP-053/RP-152, RP-046 Nr. 2): ein
        # liegengebliebenes zweites dealer-Konto ebenso (es arbeitet als
        # Sucher) — vorher legte es ein FIRMEN-Abo an, das dem Hauptchef galt.
        # Beim Hauptchef ersetzt das neue Firmen-Abo die bisherigen (auch
        # gekuendigte): vorher entstand ein zweites aktives Firmen-Abo, und
        # das juengste (evtl. kuerzere) verdraengte still das aeltere.
        if u.get("role") == "sucher" or not await _ist_hauptchef_konto(u):
            sub_doc["subject_user_id"] = u["id"]
            alte = {"subject_user_id": u["id"]}
        else:
            alte = _FIRMEN_ABO(u.get("dealer_id"))
            sub_doc["art"] = "firma"          # RP-145 Nr. 3 (Index-Kennzeichen)
        # Genau EIN aktives Abo je Konto (Index): vorheriges zuerst
        # als "ersetzt" markieren, Historie bleibt (Vorher-Status fuer die
        # Ruecknahme gemerkt).
        await db.subscriptions.update_many(
            {**alte, "status": {"$in": ["active", "cancelled"]}},
            [{"$set": {"status_vorher": "$status", "status": "ersetzt",
                       "ersetzt_durch": sub_doc["id"], "updated_at": now_iso()}}])
        try:
            await db.subscriptions.insert_one(sub_doc)
        except Exception as exc:
            # Pruefung 14.09.2026 (M13): Scheitert der Insert, darf das Konto
            # sein bisheriges Abo nicht verlieren — die eben ersetzten wieder aktiv.
            log.exception("admin_update_user: Abo-Insert fehlgeschlagen")
            try:
                await db.subscriptions.update_many(
                    {"ersetzt_durch": sub_doc["id"], "status": "ersetzt"},
                    [{"$set": {"status": {"$ifNull": ["$status_vorher", "active"]},
                               "updated_at": now_iso()}},
                     {"$unset": ["ersetzt_durch", "status_vorher"]}])
            except Exception:  # noqa: BLE001
                log.exception("admin_update_user: altes Abo nicht wiederhergestellt")
                await betrieb.alarm(db, "abo_wechsel_offen", ref=user_id,
                                    fehler=str(exc)[:300])
            raise HTTPException(500, "Abo konnte nicht gespeichert werden — das bisherige "
                                     "Abo bleibt bestehen, bitte erneut versuchen")
        await log_activity_sicher(admin.get("dealer_id", ""), admin["id"], "admin.abo.vergeben",
                           ref=user_id, meta={"plan": plan, "expires_at": expires,
                                              "kontonummer": u.get("kontonummer", "")})
    if fields:
        await log_activity_sicher(admin.get("dealer_id", ""), admin["id"], "admin.user.aktualisiert",
                           ref=user_id, meta={"felder": sorted(fields.keys()),
                                              "kontonummer": target.get("kontonummer", ""),
                                              "sucher_abgemeldet": sucher_abgemeldet})
    return {"ok": True, "sucher_abgemeldet": sucher_abgemeldet}


# Alles, was einer Firma gehoert (dealer_id-Verweis) — Grundlage fuer
# Loeschvorschau und vollstaendige Firmenloeschung. Bewusst NICHT dabei:
# activity_logs (Plattform-Nachvollziehbarkeit) und payment_transactions
# (Buchhaltungs-/Aufbewahrungspflicht).
async def _audit_pseudonymisieren(user_id: str, pseudonym: str) -> int:
    """Runde 8 (15.09.2026, Liste 3 Nr. 6): Spuren eines geloeschten Kontos im
    Audit-Log wie beim Fahrer (fahrer_konto_anonymisieren): Handelnder und Bezug
    (ref) auf das Pseudonym, personenbezogene Meta-Felder (E-Mail, Kontonummer,
    Anzeigename, IP, Geraet) weg. Der Eintrag selbst bleibt (Nachvollziehbarkeit,
    Frist LOG_AUFBEWAHRUNG_TAGE). Liefert die Zahl geaenderter Eintraege."""
    felder = {"meta.email": "", "meta.kontonummer": "", "meta.display_name": "",
              "meta.ip": "", "meta.geraet": "", "meta.driver_code": ""}
    n = 0
    for feld in ("user_id", "ref"):
        r = await db.activity_logs.update_many(
            {feld: user_id}, {"$set": {feld: pseudonym}, "$unset": felder})
        n += r.modified_count
    return n


def _nutzer_pseudonym(user_id: str) -> str:
    """Deterministisch (SHA-256 der Konto-ID, 8 Hex) wie bei zugang_grants."""
    return "geloescht:" + hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:8]


_COMPANY_COLLECTIONS = (
    "subscriptions", "vehicles", "appointments",
    "generated_pdfs", "generated_pdf_versions", "resale_listings",
    "listing_interest", "pickup_protocols", "pickup_reports",
    # driver_accounts NICHT: Fahrer-Konten sind firmenneutral (kein
    # dealer_id-Feld) — der alte Eintrag war ein No-Op und liess die
    # Loeschvorschau faelschlich "0 Fahrer" zaehlen. Fahrer loescht der
    # Admin ueber DELETE /admin/drivers/{id}.
    "dealer_drivers", "dealer_invites",
    # Runde 8 (15.09.2026, Liste 3 Nr. 4): Netzwerk-Mitgliedschaften
    # (Kaeufer <-> Firma) blieben stehen — Kaeufer bekamen weiter die
    # network_dealer_ids einer Firma, die es nicht mehr gab.
    "network_members",
    "plan_requests", "vehicle_comparisons",
    # Runde 18: Kaufvorgaenge (Umbau 09.09.2026) tragen dealer_id, Sucher,
    # Vertrag, Fahrzeug und Kaufpreis — blieben bei der Firmenloeschung liegen.
    "kaufvorgaenge",
    # Review 26.09.2026 (Nr. 141/142): KI-Bewertungen (Eingabepakete, Ergebnisse)
    # und Lernfaelle der Firma tragen dealer_id — blieben bei der Firmen-
    # loeschung liegen. ki_budget hat KEIN dealer_id-Feld (Kennung im
    # Schluessel) und laeuft ueber ai.budget.zaehler_loeschen; ki_reparatur-
    # preise ist systemweit ohne Firmen-/Nutzerbezug und bleibt.
    "ki_bewertungen", "ki_lernfaelle",
    # Go-Live 14.09.2026 (B6): users steht NICHT mehr im Tupel. Als letzter
    # Eintrag der Schleife lief users.delete_many noch VOR Snapshots, Dateien
    # und dealers.delete_many — brach einer dieser Schritte ab, fand der
    # erneute Aufruf ueber die Chef-ID nichts mehr (404), Grabstein, Dateien
    # und Snapshots blieben liegen. users loescht admin_delete_user jetzt als
    # eigenen letzten Schritt NACH dealers; die Vorschau zaehlt sie getrennt.
)


@router.get("/admin/dealers/{dealer_id}/loeschvorschau")
async def admin_delete_preview(dealer_id: str, admin=Depends(current_admin)):
    """Vorschau: was eine vollstaendige Firmenloeschung entfernen wuerde."""
    dealer = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "id": 1,
                                                          "company_name": 1})
    if not dealer:
        raise HTTPException(404, "Händler nicht gefunden")
    counts = {}
    for coll in _COMPANY_COLLECTIONS:
        counts[coll] = await db[coll].count_documents({"dealer_id": dealer_id})
    # Go-Live 14.09.2026 (B6): users nicht mehr in _COMPANY_COLLECTIONS
    counts["users"] = await db.users.count_documents({"dealer_id": dealer_id})
    # Review 26.09.2026 (Nr. 142/145): KI-Budgetzaehler der Firma und ihrer Konten
    from ai import budget as ki_budget
    konten = await db.users.distinct("id", {"dealer_id": dealer_id})
    counts["ki_budget"] = await ki_budget.zaehler_zaehlen(dealer_id, konten, db=db)
    return {"dealer_id": dealer_id,
            "hinweis": "Beweis-Snapshots werden NICHT geloescht (haendler"
                       "neutral geteilt, verfallen ueber die Aufbewahrungs"
                       "frist).",
            "company_name": dealer.get("company_name", ""),
            "wuerde_loeschen": counts}


@router.delete("/admin/users/{user_id}")
async def admin_delete_user(user_id: str, firma_loeschen: bool = False,
                            admin=Depends(current_super_admin)):
    """Nutzer loeschen — nach Rolle getrennt (Beschluss PR-Review 08/2026):

    - Sucher/Fahrer/Kaeufer: NUR dieser Nutzer (+ sein persoenliches Abo)
      wird geloescht. Firma, Fahrzeuge, Vertraege bleiben unberuehrt.
      (Vorher riss das Loeschen eines Suchers den Firmendatensatz und
      ALLE Abos der Firma mit — andere Nutzer blieben verwaist zurueck.)
    - Haendler-Hauptaccount: ohne ?firma_loeschen=true kommt 409 mit dem
      Hinweis auf Deaktivieren bzw. Loeschvorschau. Mit firma_loeschen=true
      wird die Firma VOLLSTAENDIG entfernt (alle Nutzer, Fahrzeuge,
      Termine, Vertraege, Inserate, Protokolle, Abos) — nachvollziehbar
      im Audit-Log. Buchhaltungsdaten (payment_transactions) bleiben.
    """
    u = await db.users.find_one({"id": user_id})
    if not u:
        raise HTTPException(404)
    if u.get("is_super_admin"):
        raise HTTPException(400, "Super-Admin kann nicht gelöscht werden")
    # Super-Admin durch Dependency gesichert (current_super_admin; R1-37).
    if u.get("id") == admin.get("id"):
        raise HTTPException(400, "Du kannst dich nicht selbst löschen")

    # Rollenpruefung 22.09.2026 (RP-031/RP-130/RP-281): Hauptaccount ist der
    # ZEIGER dealers.user_id (sonst das aelteste dealer-Konto), nicht jedes
    # Konto mit role 'dealer'. Ein liegengebliebenes zweites dealer-Konto
    # arbeitet als Sucher und wird wie ein Sucher geloescht — vorher kam die
    # Rueckfrage "komplette Firma loeschen", und mit firma_loeschen=true waere
    # die GANZE Firma weg gewesen.
    firmenkonto = u.get("role") in ("sucher", "dealer") and bool(u.get("dealer_id"))
    if u.get("role") != "dealer" or not await _ist_hauptchef_konto(u):
        if firmenkonto:
            # Rollenpruefung 22.09.2026 (RP-029/RP-128/RP-279): Entscheidung
            # und Grabstein unter DERSELBEN Firmen-Sperre wie der Chefwechsel,
            # mit frisch gelesenem Stand und Abgleich — sonst konnte ein
            # paralleler Chefwechsel genau dieses Konto zum Chef machen, und
            # die Loeschung lief als "Sucher" weiter (Firma ohne Chef).
            async with _chefwechsel_sperre(u["dealer_id"]):
                frisch = await db.users.find_one(
                    {"id": user_id}, {"_id": 0, "role": 1, "dealer_id": 1, "loeschung": 1})
                if not frisch:
                    raise HTTPException(404)
                if frisch.get("role") != u.get("role") \
                        or frisch.get("dealer_id") != u.get("dealer_id"):
                    raise HTTPException(409, _KONTO_GEAENDERT)
                firma_doc = await db.dealers.find_one(
                    {"id": u["dealer_id"]}, {"_id": 0, "user_id": 1, "loeschung": 1})
                if ((firma_doc or {}).get("loeschung") or {}).get("status") == "laeuft":
                    raise HTTPException(409, "Die Firma dieses Kontos wird gerade gelöscht — "
                                             "das Konto verschwindet mit ihr.")
                if (firma_doc or {}).get("user_id") == user_id:
                    raise HTTPException(409, "Dieses Konto ist inzwischen der Chef der Firma — "
                                             "bitte die Seite neu laden.")
                # Pruefung 14.09.2026 (G14): ohne Hauptaccount blieben die
                # Fahrzeuge sonst beim geloeschten Sucher haengen — vorher
                # abbrechen. Chef = Zeiger (sonst aeltestes anderes dealer-Konto).
                if not await _chef_fuer_uebernahme(u["dealer_id"], user_id):
                    raise HTTPException(409, "Die Firma hat keinen Hauptaccount — erst einen Chef "
                                             "bestimmen, dann den Sucher löschen")
                u["loeschung"] = frisch.get("loeschung")
                await _grabstein_setzen(u, admin, rolle=frisch.get("role"))
        else:
            await _grabstein_setzen(u, admin)
        # Einzelner Mitarbeiter-/Kaeufer-Account: diesen entfernen — samt
        # seiner personenbezogenen Reste (DSGVO): Netzwerk-Mitgliedschaften,
        # Favoriten und Kaufanfragen. Vorher blieb all das nach der
        # "vollstaendigen" Loeschung zurueck.
        #
        # (Grabstein: _grabstein_setzen, oben — unter der Firmen-Sperre.)
        jetzt = now_iso()
        grab = u.get("loeschung") or {}
        uebernommen = 0
        if firmenkonto:
            # Kontonummer (13.09.2026), Schritt 5: aus der entfernten Chef-Route
            # team.delete_sucher uebernommen (Runde 29 / Runde 11 J2) — Sucher
            # loescht nur noch der Betreiber. Die Fahrzeuge des Suchers gehen an
            # den Firmen-Hauptaccount, sein Konto verschwindet aus den
            # Mitbearbeitern (sonst ein Besitzer, den es nicht mehr gibt), offene
            # Abo-Anfragen bleiben nicht verwaist stehen. Wiederholbar: nach dem
            # Grabstein, ein zweiter Lauf findet nichts mehr.
            firma = {"dealer_id": u["dealer_id"]}
            # Runde 12/13: der eingetragene Hauptaccount (dealers.user_id), sonst
            # das aelteste dealer-Konto (_chef_fuer_uebernahme, Rollenpruefung
            # 22.09.2026 — dieselbe Regel wie beim Wechsel zum Zwischenhaendler).
            chef_id = await _chef_fuer_uebernahme(u["dealer_id"], user_id)
            chef = {"id": chef_id} if chef_id else None
            if chef:
                uebernommen = (await db.vehicles.update_many(
                    {**firma, "owner_user_id": user_id},
                    {"$set": {"owner_user_id": chef["id"], "uebernommen_von": user_id,
                              "updated_at": jetzt}})).modified_count
                # Runde 13 (Liste 4 Nr. 1/2): auch Kaufvorgaenge, Vertraege und
                # Termine des Suchers gehen an den Chef — kein Waisenvorgang, den
                # spaeter kein Sucher uebernehmen kann.
                from routes.bestand import vorgang_uebergeben
                uebergabe = await vorgang_uebergeben(u["dealer_id"], None, user_id, chef["id"])
                if any(uebergabe.values()):
                    log.info("Sucher %s geloescht: Vorgang an Chef %s uebergeben: %s",
                             user_id, chef["id"], uebergabe)
                # Pruefbericht 20.09.2026 (Nr. 2): Grabstein fuer die Nachlese.
                # Eine Anfrage, die VOR der Loeschung durch die Anmeldung kam,
                # laeuft weiter und kann danach noch einen Vertrag oder Termin
                # mit dieser user_id anlegen — der bliebe als Waise liegen.
                # cleanup_service.konto_nachlese_abarbeiten wiederholt die
                # Uebergabe deshalb noch eine Weile (idempotent).
                try:
                    from cleanup_service import KONTO_NACHLESE
                    await db[KONTO_NACHLESE].update_one(
                        {"_id": user_id},
                        {"$set": {"dealer_id": u["dealer_id"], "an": chef["id"],
                                  "seit": now_iso()}},
                        upsert=True)
                except Exception:  # noqa: BLE001 — Loeschung darf daran nie scheitern
                    log.exception("Konto-Grabstein fuer %s nicht gesetzt", user_id)
            else:
                log.warning("Sucher %s geloescht: Firma %s ohne Hauptaccount — "
                            "Fahrzeuge bleiben beim alten Besitzer", user_id,
                            u["dealer_id"])
            await db.vehicles.update_many(
                {**firma, "mitbearbeiter_ids": user_id},
                {"$pull": {"mitbearbeiter_ids": user_id}})
            await db.plan_requests.delete_many(
                {"subject_user_id": user_id, "status": "offen"})
        await db.subscriptions.delete_many({"subject_user_id": user_id})
        await db.network_members.delete_many({"buyer_user_id": user_id})
        await db.buyer_favorites.delete_many({"buyer_user_id": user_id})
        # Pruefung 14.09.2026 (M1): fuer diesen Kaeufer reservierte Fahrzeuge
        # wieder freigeben — sonst blieben sie fuer immer blockiert.
        await kaeufer_reservierungen_freigeben(user_id, "kaeufer_geloescht")
        await db.listing_interest.delete_many({"buyer_user_id": user_id})
        await db.plan_requests.delete_many({"buyer_user_id": user_id})
        # Nachpruefung Runde 14 (Befund 24): zugang_grants (Stripe-
        # Freischaltungen, routes/payments.py) blieben mit der user_id
        # stehen — die Collection raeumte sonst niemand auf. Sie werden
        # NICHT geloescht, sondern pseudonymisiert: der Grant gehoert zur
        # Zahlung (session_id) und ist wie payment_transactions Teil der
        # Buchhaltung (Beleg, welche Laufzeit fuer welche Zahlung gutge-
        # schrieben wurde) — nur der Personenbezug faellt weg. Pseudonym
        # deterministisch (SHA-256 der Konto-ID, 8 Hex) wie bei
        # fahrer_konto_anonymisieren: mehrfach laufende Loeschung ergibt
        # denselben Wert, mehrere Grants desselben Kontos bleiben als
        # zusammengehoerig erkennbar, ohne Rueckschluss auf die Person.
        pseudonym = _nutzer_pseudonym(user_id)
        await db.zugang_grants.update_many(
            {"user_id": user_id},
            {"$set": {"user_id": pseudonym, "pseudonymisiert_at": jetzt}})
        # Review 26.09.2026 (Nr. 143): KI-Bewertungen/Lernfaelle des Kontos
        # (Vertragsbewertungen tragen user_id) auf dasselbe Pseudonym; die
        # persoenlichen Budgetzaehler (vertrag:<user_id>:<Monat>) fallen weg.
        try:
            from ai import budget as ki_budget, retention as ki_retention
            await ki_retention.nutzer_pseudonymisieren(db, user_id, pseudonym)
            await ki_budget.zaehler_loeschen(None, [user_id], db=db)
        except Exception:  # noqa: BLE001 — Loeschung darf daran nie scheitern
            log.exception("KI-Spuren von %s nicht pseudonymisiert", user_id)
        # Runde 8 (15.09.2026, Liste 3 Nr. 6): Anmelde-Eintraege (E-Mail bzw.
        # Kontonummer, IP, Geraet) und Admin-Aktionen mit diesem Konto als ref
        # blieben bisher unveraendert im Audit-Log.
        await _audit_pseudonymisieren(user_id, pseudonym)
        # Runde 13: B8 — Nutzerkennung in Beweis-Snapshots pseudonymisieren.
        from snapshot_service import snapshots_pseudonymisieren
        await snapshots_pseudonymisieren(db, user_id=user_id)
        await db.users.delete_one({"id": user_id})
        # Go-Live 14.09.2026 (B6c): Konto ist geloescht — ein Audit-Fehler darf
        # keinen 500 mehr ausloesen, dessen Wiederholung auf 404 laeuft.
        await log_activity_sicher(admin.get("dealer_id", ""), admin["id"],
                                  "admin.user.geloescht", ref=user_id,
                                  # Kontonummer (13.09.2026): keine E-Mail im Audit;
                                  # Runde 8: auch keine Kontonummer mehr
                                  meta={"rolle": u.get("role", ""),
                                        "fahrzeuge_uebernommen": uebernommen,
                                        "wiederaufnahme": grab.get("status") == "laeuft"})
        return {"ok": True, "geloescht": "nur_nutzer"}

    dealer_id = u.get("dealer_id")
    if not firma_loeschen:
        raise HTTPException(409,
            "Das ist der Händler-Hauptaccount. Zum Sperren bitte "
            "'Deaktivieren' verwenden. Soll die FIRMA komplett gelöscht "
            "werden (alle Nutzer, Fahrzeuge, Verträge, Termine), zuerst "
            f"die Löschvorschau ansehen (/admin/dealers/{dealer_id}/"
            "loeschvorschau) und dann mit ?firma_loeschen=true bestätigen.")

    # Runde 12: Audit VOR dem ersten destruktiven Schritt. Bricht die
    # Loeschung mittendrin ab, steht sonst nirgends, wer sie ausgeloest hat.
    await log_activity_sicher(admin.get("dealer_id", ""), admin["id"],
                       "admin.firma.loeschung.gestartet", ref=dealer_id or user_id,
                       meta={"kontonummer": u.get("kontonummer", ""), "dealer_id": dealer_id})
    geloescht = {}
    if dealer_id:
        # Beweis-Snapshots bleiben BEWUSST stehen: Snapshots sind
        # haendlerneutral geteilt (der erste Snapshot einer Anzeige wird
        # von allen uebernommen) — eine Loeschung ueber die Fahrzeug-ID
        # ('v_<Anzeigen-ID>', bei mehreren Haendlern identisch) wuerde die
        # Beweisarchive ANDERER Firmen zerstoeren. Sie enthalten nur
        # oeffentliche Inseratsdaten und verfallen ueber die
        # SNAPSHOT_RETENTION_DAYS-Aufraeumlogik.
        #
        # Nachpruefung Runde 14 (Befund 58): Grabstein am Firmenprofil
        # (Muster cleanup_service.vertrag_endgueltig_loeschen) — die
        # Firma ist ab jetzt als "in Loeschung" erkennbar.
        #
        # Go-Live 14.09.2026 (B6): Reihenfolge Nebendaten -> Snapshots ->
        # Dateien -> dealers -> users. Die Konten bleiben bis zum Schluss
        # stehen, damit ein abgebrochener Lauf ueber die Chef-ID wieder-
        # aufnehmbar ist; jeder Schritt ist idempotent (update_one/delete_many
        # ohne Treffer, delete_prefix auf leerem Praefix).
        jetzt = now_iso()
        wiederaufnahme = False
        firma_doc = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "loeschung": 1})
        if firma_doc is None or (firma_doc.get("loeschung") or {}).get("status") == "laeuft":
            wiederaufnahme = True
        # Rollenpruefung 22.09.2026 (RP-029/RP-128): der Grabstein der Firma
        # entsteht unter der Firmen-Sperre — ein Chefwechsel, der gerade
        # laeuft, ist damit fertig, und jeder spaetere sieht die Loeschung
        # (409). Die Kaskade selbst laeuft danach ohne Sperre weiter.
        async with _chefwechsel_sperre(dealer_id):
            await db.dealers.update_one(
                {"id": dealer_id},
                {"$set": {"loeschung": {"status": "laeuft", "gestartet": jetzt,
                                        "grund": "admin", "durch": admin["id"]},
                          "updated_at": jetzt}})
        # Go-Live 14.09.2026 (B6a): ALLE Konten der Firma sofort sperren und
        # abmelden. Vorher arbeiteten Chef und Sucher waehrend der Kaskade
        # (und nach einem Abbruch unbegrenzt) weiter: der Grabstein am
        # dealers-Dokument wertet niemand aus. Mit gesperrtem Chef greift
        # firma_gesperrt — current_user weist Sucher ab, der Marktplatz blendet
        # die Firma aus.
        await db.users.update_many(
            {"dealer_id": dealer_id},
            {"$set": {"active": False, "current_session_id": None, "updated_at": jetzt}})
        # Befund 148/165 (19.09.2026): Offene Link-Auftraege tragen kein
        # dealer_id-Feld (sondern dealer_ids/requested_by_dealer) und blieben
        # deshalb in der Schlange stehen — der Worker haette sie mitten in der
        # Loeschkaskade noch extern abgerufen, und abbrechen konnte sie
        # niemand mehr (die Firma ist gesperrt).
        try:
            from link_jobs import firma_austragen
            n_jobs = await firma_austragen(db, dealer_id)
            if n_jobs:
                geloescht["link_jobs"] = n_jobs
        except Exception as exc:  # noqa: BLE001 — Loeschung nie daran scheitern
            log.warning("Firmenloeschung %s: Link-Auftraege nicht bereinigt: %s",
                        dealer_id, exc)
        for coll in _COMPANY_COLLECTIONS:
            res = await db[coll].delete_many({"dealer_id": dealer_id})
            if res.deleted_count:
                geloescht[coll] = res.deleted_count
        # Review 26.09.2026 (Nr. 141/145): KI-Budgetzaehler (Kennung im
        # Schluessel, kein dealer_id-Feld) — Firma UND ihre Konten, solange
        # die Konten noch stehen (users faellt erst als letzter Schritt).
        try:
            from ai import budget as ki_budget
            konten = await db.users.distinct("id", {"dealer_id": dealer_id})
            n_budget = await ki_budget.zaehler_loeschen(dealer_id, konten, db=db)
            if n_budget:
                geloescht["ki_budget"] = n_budget
        except Exception as exc:  # noqa: BLE001 — Loeschung nie daran scheitern
            log.warning("Firmenloeschung %s: KI-Budgetzaehler nicht bereinigt: %s",
                        dealer_id, exc)
        # (Kontonummer 13.09.2026, Schritt 5: password_resets gibt es nicht
        # mehr — Runde 14 Befund 12 entfaellt.)
        # Runde 13: B8 — die Snapshot-Zeilen bleiben (Beweiszweck), aber die
        # Zuordnung "welche Firma, welcher Nutzer hat gesichert" wird
        # pseudonymisiert; vorher blieben dealer_id/user_id unbegrenzt stehen.
        from snapshot_service import snapshots_pseudonymisieren
        n_snap = await snapshots_pseudonymisieren(db, dealer_id=dealer_id)
        if n_snap:
            geloescht["listing_snapshots_pseudonymisiert"] = n_snap
        # GESPEICHERTE DATEIEN der Firma mitloeschen (DSGVO): Unterschriften
        # + Protokoll-PDFs (protocol/), Abhol-/Schadenfotos (pickup/),
        # Inserats-Fotos (resale/), Logo (logo/). Beweis-Snapshots bleiben
        # bewusst (haendlerneutral geteilt, s.o.). Fehler beim Dateiloeschen
        # brechen die Kontoloeschung nicht ab, werden aber ausgewiesen.
        from storage_service import storage
        dateien = 0
        datei_fehler = []
        import asyncio as _asyncio
        for kategorie in ("protocol", "pickup", "resale", "logo"):
            try:
                dateien += await _asyncio.to_thread(
                    storage.delete_prefix, f"{kategorie}/{dealer_id}/")
            except Exception as exc:
                datei_fehler.append(f"{kategorie}: {exc}")
        geloescht["dateien"] = dateien
        if datei_fehler:
            geloescht["datei_fehler"] = datei_fehler
            # Runde 5: nicht stillschweigend "ok" — Wiederholung einplanen
            # (cleanup_service.storage_loeschungen_nachholen) und im
            # Fehlerarchiv sichtbar machen.
            for kategorie in ("protocol", "pickup", "resale", "logo"):
                await db.storage_delete_retry.update_one(
                    {"prefix": f"{kategorie}/{dealer_id}/"},
                    {"$setOnInsert": {"id": str(uuid.uuid4()),
                                      "prefix": f"{kategorie}/{dealer_id}/",
                                      "dealer_id": dealer_id,
                                      "created_at": now_iso()},
                     "$set": {"letzter_fehler": "; ".join(datei_fehler)[:300]}},
                    upsert=True)
            await db.error_logs.insert_one({
                "id": str(uuid.uuid4()), "source": "backend", "method": "DELETE",
                "path": f"/api/admin/users/{user_id}", "error_type": "StorageDelete",
                "message": "Dateien der geloeschten Firma konnten nicht (vollstaendig) "
                           "entfernt werden — Wiederholung eingeplant: "
                           + "; ".join(datei_fehler)[:600],
                "traceback": "", "ip": "", "status": "open",
                "created_at": now_iso()})
        await db.dealers.delete_many({"id": dealer_id})
        # Go-Live 14.09.2026 (B6b): users als eigener LETZTER Schritt nach
        # dealers — erst jetzt verschwindet die Chef-ID, ueber die ein
        # erneuter Aufruf den Vorgang findet. (password_resets gibt es seit
        # Kontonummer 13.09.2026, Schritt 5 nicht mehr.)
        # Runde 8 (15.09.2026, Liste 3 Nr. 6): Audit-Spuren aller Konten der
        # Firma pseudonymisieren, bevor die Konten verschwinden.
        async for konto in db.users.find({"dealer_id": dealer_id}, {"_id": 0, "id": 1}):
            await _audit_pseudonymisieren(konto["id"], _nutzer_pseudonym(konto["id"]))
        res = await db.users.delete_many({"dealer_id": dealer_id})
        if res.deleted_count:
            geloescht["users"] = res.deleted_count
        # Phase 3 (3.4, Liste 4 Nr. 15): Grabstein — der Aufraeumjob entfernt
        # Reste, die eine noch laufende Anfrage nach der Loeschung schrieb.
        try:
            await db.firmen_geloescht.update_one(
                {"id": dealer_id}, {"$set": {"id": dealer_id, "geloescht_am": now_iso()}},
                upsert=True)
        except Exception:  # noqa: BLE001
            log.exception("Grabstein fuer Firma %s nicht gesetzt", dealer_id)
    else:
        wiederaufnahme = False
        await db.users.delete_one({"id": user_id})

    # Go-Live 14.09.2026 (B6c): alles ist geloescht — ein Audit-Fehler darf
    # keinen 500 mehr liefern (die Wiederholung liefe auf 404).
    await log_activity_sicher(admin.get("dealer_id", ""), admin["id"],
                              "admin.firma.geloescht", ref=dealer_id or user_id,
                              meta={"geloescht": geloescht,
                                    "wiederaufnahme": wiederaufnahme})
    return {"ok": True, "geloescht": geloescht or "nur_nutzer"}


@router.post("/admin/users/{user_id}/active")
async def admin_user_set_active(
    user_id: str, body: AdminActiveIn, admin=Depends(current_super_admin)
):
    """Soft-Block / Entsperren eines Nutzer-Kontos.

    Sperren = active=False. Der User existiert weiterhin, kann sich aber
    nicht mehr einloggen (siehe /auth/login). Die laufende Session wird
    invalidiert, indem `current_session_id` zurueckgesetzt wird.
    """
    u = await db.users.find_one({"id": user_id})
    if not u:
        raise HTTPException(404, "Nutzer nicht gefunden")
    if u.get("is_super_admin") and not body.active:
        raise HTTPException(400, "Super-Admin kann nicht gesperrt werden")
    # Dieselbe Regel wie bei Passwort/PUT (Prüfbericht Runde 4): Admin-Konten
    # sperrt/entsperrt nur der Super-Admin — Super-Admin durch Dependency
    # gesichert (current_super_admin; R1-37).
    if u.get("id") == admin.get("id") and not body.active:
        raise HTTPException(400, "Du kannst dich nicht selbst sperren")
    # Pruefbericht 20.09.2026 (V-05): Konten in Loeschung (oder einer Firma in
    # Loeschung) liessen sich hier wieder aktivieren — der Weg der Oberflaeche
    # pruefte, anders als PUT /admin/users, keinen Grabstein.
    if body.active:
        if (u.get("loeschung") or {}).get("status") == "laeuft":
            raise HTTPException(409, "Dieses Konto wird gerade gelöscht — Entsperren ist nicht möglich")
        if u.get("dealer_id"):
            firma = await db.dealers.find_one({"id": u["dealer_id"]}, {"_id": 0, "loeschung": 1})
            if ((firma or {}).get("loeschung") or {}).get("status") == "laeuft":
                raise HTTPException(409, "Die Firma dieses Kontos wird gerade gelöscht — "
                                         "Entsperren ist nicht möglich")
    patch = {"active": bool(body.active), "updated_at": now_iso()}
    if not body.active or not u.get("active", True):
        # Sperren beendet die Sitzung; Entsperren verwirft eine WAEHREND der
        # Sperre entstandene Sitzung (Nachpruefung 15.09.2026, Anmeldung
        # Nr. 3/4: ein Login, der die Sperre ueberholte, lebte sonst wieder auf).
        patch["current_session_id"] = None
    filt = {"id": user_id}
    if body.active:
        # auch gegen ein Rennen mit dem Loeschstart: nur ohne laufenden Grabstein
        filt["loeschung.status"] = {"$ne": "laeuft"}
    res = await db.users.update_one(filt, {"$set": patch})
    if res.matched_count == 0:
        raise HTTPException(409, "Das Konto wurde inzwischen geändert (z. B. Löschung gestartet) — bitte neu laden")
    if not body.active and u.get("role") == "b2b_buyer":
        # Nachpruefung Runde 14 (Befund 1): Ein gesperrter Kaeufer darf keine
        # laufende Verhandlung behalten — sonst koennte der Haendler seine
        # alte Anfrage spaeter noch annehmen und fuer ihn reservieren.
        # Pruefung 14.09.2026 (M1): auch bereits akzeptierte (reservierte)
        # Fahrzeuge werden frei — ein gesperrter Kaeufer kauft nicht.
        await kaeufer_verhandlungen_beenden(user_id, "kaeufer_gesperrt")
    sucher_abgemeldet = 0
    if not body.active and await _ist_hauptchef_konto(u):
        sucher_abgemeldet = await _firmenkonten_abmelden(u["dealer_id"], u["id"])
    await log_activity_sicher(admin.get("dealer_id", ""), admin["id"],
                       "admin.user.entsperrt" if body.active else "admin.user.gesperrt",
                       ref=user_id, meta={"kontonummer": u.get("kontonummer", ""),
                                          "sucher_abgemeldet": sucher_abgemeldet})
    return {"ok": True, "active": bool(body.active),
            "sucher_abgemeldet": sucher_abgemeldet}


@router.post("/admin/users/{user_id}/password")
async def admin_user_set_password(
    user_id: str, body: AdminUserPasswordIn, admin=Depends(current_super_admin)
):
    """Setzt das Passwort eines Nutzers zurueck (Admin-Funktion)."""
    # (Passwortregel bereits im Modell AdminUserPasswordIn)
    u = await db.users.find_one({"id": user_id})
    if not u:
        raise HTTPException(404, "Nutzer nicht gefunden")
    # Dieselben Regeln wie bei der PUT-Route (Pruefbericht Runde 4: hier
    # fehlte der Schutz — ein normaler Admin konnte das Super-Admin-Passwort
    # setzen und die Plattform uebernehmen).
    if u.get("is_super_admin"):
        # Nachpruefung 15.09.2026 (Anmeldung Nr. 1/10): GENAU EIN Weg fuer das
        # Betreiber-Passwort — /admin/me/password mit aktuellem Passwort.
        raise HTTPException(400, "Das Super-Admin-Passwort wird nur über /admin/me/password "
                                 "mit dem aktuellen Passwort geändert.")
    # Super-Admin durch Dependency gesichert (current_super_admin; R1-37).
    _pw_persoenlich_400(body.new_password, persoenliche_werte(u))
    # Nachpruefung 15.09.2026 (Anmeldung Nr. 11): kein Passwort fuer ein Konto
    # in laufender Loeschung — Bedingung im Filter (CAS).
    r = await db.users.update_one(
        {"id": user_id, "loeschung.status": {"$ne": "laeuft"}},
        {"$set": {
            "password_hash": await hash_password_async(body.new_password),
            "current_session_id": None,
            "updated_at": now_iso(),
        }},
    )
    if r.matched_count == 0:
        raise HTTPException(409, "Dieses Konto wird gerade gelöscht — kein Passwort mehr setzen")
    sperre = await _konto_sperre_aufheben(u)
    await log_activity_sicher(admin.get("dealer_id", ""), admin["id"],
                       "admin.passwort.zurueckgesetzt",
                       ref=user_id, meta={"kontonummer": u.get("kontonummer", ""),
                                          "sperre_aufgehoben": sperre.get("sperre_aufgehoben")})
    return {"ok": True, **sperre}


# ---------- Fahrer-Verwaltung (Review 09/2026: fehlte komplett) ----------
# Fahrer-Konten sind firmenneutral (kein dealer_id) — die Liste ist
# plattformweit; Firmenzugehoerigkeit ergibt sich aus dealer_drivers.
@router.get("/admin/drivers")
async def admin_list_drivers(response: Response, limit: int = 2000, seite: int = 1,
                             _=Depends(current_admin), q: Optional[str] = None):
    """AD-33: `q` sucht serverseitig in Kontonummer, Name, E-Mail, Fahrer-Code
    und ueber die verknuepften Firmen (Name/Kundennummer) — wie die Suche in
    der Oberflaeche, nur ueber ALLE Fahrer statt nur die erste Seite."""
    limit = max(1, min(int(limit), 5000))
    seite = max(1, int(seite))
    filter_: dict = {}
    muster = _suchmuster(q)
    if muster:
        oder = [{"kontonummer": muster}, {"display_name": muster},
                {"email": muster}, {"driver_code": muster}]
        firmen = await _firmen_ids_zu(q, muster)
        if firmen:
            fahrer_ids = await db.dealer_drivers.distinct(
                "driver_account_id", {"dealer_id": {"$in": firmen}})
            if fahrer_ids:
                oder.append({"id": {"$in": fahrer_ids}})
        filter_ = {"$or": oder}
    fahrer = await db.driver_accounts.find(
        filter_, {"_id": 0, "password_hash": 0, "current_session_id": 0},
    ).sort("created_at", -1).skip((seite - 1) * limit).to_list(limit + 1)
    fahrer = _seite_kopf(response, fahrer, limit)
    links: Dict[str, dict] = {}
    async for row in db.dealer_drivers.aggregate([
        {"$group": {"_id": "$driver_account_id", "n": {"$sum": 1},
                    "dealer_ids": {"$addToSet": "$dealer_id"}}},
    ]):
        links[row["_id"]] = row
    dealer_ids = sorted({d for r in links.values() for d in r.get("dealer_ids") or []})
    namen = {}
    if dealer_ids:
        async for d in db.dealers.find({"id": {"$in": dealer_ids}},
                                       {"_id": 0, "id": 1, "company_name": 1}):
            namen[d["id"]] = d.get("company_name") or d["id"][:8]
    termine: Dict[str, dict] = {}
    async for row in db.appointments.aggregate([
        {"$match": {"driver_id": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$driver_id", "n": {"$sum": 1},
                    "offen": {"$sum": {"$cond": [
                        {"$in": ["$status", ["offen", "verschoben"]]}, 1, 0]}}}},
    ]):
        termine[row["_id"]] = row
    return [{**f,
             "firmen": sorted(namen.get(d, d[:8])
                              for d in (links.get(f["id"], {}).get("dealer_ids") or [])),
             "verknuepfungen": links.get(f["id"], {}).get("n", 0),
             "termine": termine.get(f["id"], {}).get("n", 0),
             "termine_offen": termine.get(f["id"], {}).get("offen", 0)}
            for f in fahrer]


async def _fahrer_or_404(driver_id: str) -> dict:
    d = await db.driver_accounts.find_one({"id": driver_id}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Fahrer nicht gefunden")
    return d


@router.post("/admin/drivers/{driver_id}/active")
async def admin_driver_set_active(driver_id: str, body: AdminActiveIn,
                                  admin=Depends(current_super_admin)):
    d = await _fahrer_or_404(driver_id)
    # Audit 13.09.2026 (#59): Ein halb geloeschtes Konto (Grabstein aus
    # admin_delete_driver) nicht wieder entsperren — sonst hebt ein Klick die
    # Sperre auf, waehrend Termine/Berichte schon pseudonymisiert sind.
    if body.active and (d.get("loeschung") or {}).get("status") == "laeuft":
        raise HTTPException(409, "Löschung läuft — bitte 'Löschen' erneut "
                                 "ausführen, um sie abzuschließen")
    jetzt = now_iso()
    fields = {"active": body.active, "updated_at": jetzt}
    if not body.active or not d.get("active", True):
        # Sperren beendet die laufende Sitzung sofort (Single-Session strikt);
        # Entsperren verwirft eine waehrend der Sperre entstandene Sitzung
        # (Nachpruefung 15.09.2026, Anmeldung Nr. 5).
        fields["current_session_id"] = None
    filt_konto = {"id": driver_id}
    if body.active:
        # Nachbesserung #59: Die Pruefung oben ist Lesen-dann-Schreiben. Setzt
        # ein paralleler DELETE den Grabstein dazwischen, darf dieser Write das
        # Konto nicht wieder aktivieren — Bedingung im Filter (CAS).
        filt_konto["loeschung.status"] = {"$ne": "laeuft"}
    r_konto = await db.driver_accounts.update_one(filt_konto, {"$set": fields})
    if r_konto.matched_count == 0:
        if not await db.driver_accounts.find_one({"id": driver_id}, {"_id": 1}):
            raise HTTPException(404, "Fahrer nicht gefunden")
        raise HTTPException(409, "Löschung läuft — bitte 'Löschen' erneut "
                                 "ausführen, um sie abzuschließen")
    termine_getrennt = 0
    if not body.active:
        # Nachpruefung Runde 14 (Befund 17): offene Fahrten (offen/
        # bestaetigt/verschoben/in Bearbeitung, leerer Status = offen) vom
        # gesperrten Fahrer trennen — er kommt nicht mehr an die Fahrer-
        # App, und der Firmenkalender zeigt keinen Sperrstatus: die Fahrten
        # hingen unsichtbar an einem Fahrer, der sie nie abholt. Dieselbe
        # Regel wie DELETE /drivers/{id} (routes/drivers.py): Zuweisung weg,
        # offene Annahme-Anfrage verworfen (zuteilung=None), der Chef
        # teilt neu zu. Abgeschlossene Fahrten behalten ihre Zuordnung.
        # Jede betroffene Firma bekommt einen Eintrag in ihrem Audit-Log,
        # damit der Chef die neu zuzuteilenden Termine findet.
        from routes.drivers import _OFFEN_WERTE
        filt = {"driver_id": driver_id, "status": {"$in": _OFFEN_WERTE}}
        betroffen = [a async for a in db.appointments.find(
            filt, {"_id": 0, "id": 1, "dealer_id": 1})]
        if betroffen:
            r = await db.appointments.update_many(
                filt, {"$unset": {"driver_id": ""},
                       "$set": {"zuteilung": None, "updated_at": jetzt}})
            termine_getrennt = r.modified_count
            je_firma: Dict[str, list] = {}
            for a in betroffen:
                je_firma.setdefault(a.get("dealer_id") or "", []).append(a["id"])
            for firma_id, termin_ids in je_firma.items():
                await log_activity_sicher(firma_id, admin["id"],
                                   "fahrer.gesperrt.termine_freigegeben",
                                   ref=driver_id,
                                   meta={"driver_code": d.get("driver_code", ""),
                                         "termine": termin_ids})
    await log_activity_sicher(admin.get("dealer_id", ""), admin["id"],
                       "admin.fahrer.entsperrt" if body.active else "admin.fahrer.gesperrt",
                       ref=driver_id, meta={"kontonummer": d.get("kontonummer", ""),
                                            "offene_termine_getrennt": termine_getrennt})
    return {"ok": True, "active": body.active,
            "offene_termine_getrennt": termine_getrennt}


@router.post("/admin/drivers/{driver_id}/password")
async def admin_driver_set_password(driver_id: str, body: AdminUserPasswordIn,
                                    admin=Depends(current_super_admin)):
    d = await _fahrer_or_404(driver_id)
    # Gleiche Staerke-Regel wie bei der Fahrer-Registrierung (wirft
    # ValueError -> sauberer 400 statt 500).
    from routes.drivers import _check_password_strength
    try:
        _check_password_strength(body.new_password or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _pw_persoenlich_400(body.new_password, persoenliche_werte(d))
    # Nachpruefung 15.09.2026 (Anmeldung Nr. 12): kein Passwort fuer ein Konto
    # in laufender Loeschung — Bedingung im Filter (CAS wie beim Entsperren).
    r = await db.driver_accounts.update_one(
        {"id": driver_id, "loeschung.status": {"$ne": "laeuft"}},
        {"$set": {"password_hash": await hash_password_async(body.new_password),
                  "current_session_id": None, "updated_at": now_iso()}})
    if r.matched_count == 0:
        raise HTTPException(409, "Löschung läuft — kein Passwort mehr setzen")
    sperre = await _konto_sperre_aufheben(d)
    await log_activity_sicher(admin.get("dealer_id", ""), admin["id"],
                       "admin.fahrer.passwort.zurueckgesetzt",
                       ref=driver_id, meta={"kontonummer": d.get("kontonummer", ""),
                                            "sperre_aufgehoben": sperre.get("sperre_aufgehoben")})
    return {"ok": True, **sperre}


@router.delete("/admin/drivers/{driver_id}")
async def admin_delete_driver(driver_id: str, admin=Depends(current_super_admin)):
    """Fahrer-Konto loeschen (DSGVO — vorher gab es dafuer KEINEN Weg):
    Haendler-Verknuepfungen entfernen, OFFENE Termine vom Fahrer trennen
    (abgeschlossene behalten die historische Zuordnung — nach der Loeschung
    ist die driver_id keiner Person mehr zuzuordnen), Reset-Tokens weg,
    dann das Konto selbst."""
    d = await _fahrer_or_404(driver_id)
    # Audit 13.09.2026 (#59): Grabstein und Sperre ZUERST, im Muster von
    # admin_delete_user. Vorher lief die Pseudonymisierung (sieben getrennte
    # Schreibschritte, update_many/delete_many sind keine retryable writes)
    # ohne Sperre und ohne Marker: brach sie ab, blieb der Fahrer aktiv und
    # eingeloggt, und nichts meldete den Mischzustand. Jetzt weisen
    # current_driver (401), driver_login (403) und add_driver_by_code (409)
    # sofort ab, und ein erneuter DELETE fuehrt die Loeschung zu Ende.
    jetzt = now_iso()
    grab = d.get("loeschung") or {}
    wiederaufnahme = grab.get("status") == "laeuft"
    if wiederaufnahme:
        await db.driver_accounts.update_one(
            {"id": driver_id},
            {"$set": {"loeschung.gestartet": jetzt, "active": False,
                      "current_session_id": None, "updated_at": jetzt},
             "$inc": {"loeschung.wiederaufnahmen": 1}})
    else:
        await db.driver_accounts.update_one(
            {"id": driver_id},
            {"$set": {"loeschung": {"status": "laeuft", "gestartet": jetzt,
                                    "grund": "admin", "durch": admin["id"]},
                      "active": False, "current_session_id": None,
                      "updated_at": jetzt}})
    # Start-Audit VOR dem ersten destruktiven Schritt mit der WERFENDEN
    # Variante (wie admin.firma.loeschung.gestartet): scheitert es, wird
    # nichts pseudonymisiert. Ohne E-Mail in meta.
    await log_activity_sicher(admin.get("dealer_id", ""), admin["id"],
                       "admin.fahrer.loeschung.gestartet", ref=driver_id,
                       meta={"wiederaufnahme": wiederaufnahme})
    # Audit 09/2026: nicht nur trennen, sondern pseudonymisieren (Termine,
    # Berichte, Protokolle, Audit-Log) — Funktion in routes/drivers.py.
    # Bewusst akzeptiertes Restrisiko: Ein Fahrer-Bericht, der VOR der Sperre
    # begann und erst nach delete_one gespeichert wird, traegt noch Kennung
    # und Klarnamen (selten, braeuchte eine Nachpruefung in submit_report).
    from routes.drivers import fahrer_konto_anonymisieren
    anonym = await fahrer_konto_anonymisieren(db, driver_id)
    links = type("R", (), {"deleted_count": anonym.get("dealer_drivers", 0)})()
    getrennt = type("R", (), {"modified_count": anonym.get("appointments", 0)})()
    await db.driver_accounts.delete_one({"id": driver_id})
    # Konto ist geloescht — ein Audit-Fehler darf keinen 500 mit 404-Retry
    # ("Fahrer nicht gefunden") mehr ausloesen.
    await log_activity_sicher(admin.get("dealer_id", ""), admin["id"],
                              "admin.fahrer.geloescht", ref=driver_id,
                              # Kontonummer (13.09.2026): weder E-Mail noch
                              # Fahrer-Code des geloeschten Kontos im Audit
                              meta={"verknuepfungen": links.deleted_count,
                                    "offene_termine_getrennt": getrennt.modified_count,
                                    "wiederaufnahme": wiederaufnahme})
    return {"ok": True, "verknuepfungen_entfernt": links.deleted_count,
            "offene_termine_getrennt": getrennt.modified_count}


# ---------- Contracts (read-only admin views) ----------
# Audit 13.09.2026 (#51/#52): Obergrenze der Admin-Vertragslisten. Sie wird
# signalisiert (X-Truncated bzw. "abgeschnitten") statt still gezogen.
ADMIN_VERTRAEGE_MAX = 2000


@router.get("/admin/contracts")
async def admin_all_contracts(response: Response, _=Depends(current_admin),
                              page: int = 1, limit: int = ADMIN_VERTRAEGE_MAX):
    """Alle Vertraege plattformweit, neueste zuerst (ohne PDF-Bytes).

    Audit 13.09.2026 (#52): Vorher kappte to_list(2000) ohne jedes Signal.
    Jetzt blaettern per ?page=&limit= (Standard wie bisher: erste 2000) und
    Kopfzeile X-Truncated: "1", wenn es weitere Vertraege gibt. Die Antwort
    bleibt eine Liste."""
    limit = max(1, min(int(limit or ADMIN_VERTRAEGE_MAX), ADMIN_VERTRAEGE_MAX))
    # Nachbesserung #52: page auch nach oben begrenzen — sonst sprengt
    # (page - 1) * limit int64 und pymongo wirft OverflowError (500).
    page = max(1, min(int(page or 1), 10 ** 6))
    items = await db.generated_pdfs.find(
        {}, {"_id": 0, "pdf_b64": 0, "pdf_digital_b64": 0},
    ).sort("created_at", -1).skip((page - 1) * limit).to_list(limit + 1)
    abgeschnitten = len(items) > limit
    response.headers["X-Truncated"] = "1" if abgeschnitten else "0"
    if abgeschnitten:
        log.warning("Admin-Vertragsliste Seite %s auf %s Eintraege gekuerzt "
                    "(weitere per ?page=)", page, limit)
    return items[:limit]


#: Wunsch Ahmad 20.09.2026: Die Firmenseite laedt die Vertraege in
#: 20er-Schritten nach ("weitere 20 anzeigen"), statt bis zu 2000 Stueck auf
#: einmal. Bei einer Firma mit vielen Vertraegen war die Seite sonst
#: sekundenlang am Laden, bevor ueberhaupt etwas zu sehen war.
ADMIN_VERTRAEGE_SEITE = 20


@router.get("/admin/users/{user_id}/contracts")
async def admin_user_contracts(user_id: str, response: Response,
                               _=Depends(current_admin),
                               seite: int = 1,
                               limit: int = ADMIN_VERTRAEGE_SEITE):
    """Listet die Verträge eines Nutzers auf (read-only).
    Chef (role dealer): alle Verträge seiner Firma (umfang "firma"); jedes
    andere Konto: nur die selbst erzeugten (umfang "nutzer").
    Enthält keine PDF-Bytes — nur Metadaten + extrahierte Vertragsdaten,
    damit die Liste schnell lädt. PDF kann separat über
    /api/admin/contracts/{id}/pdf abgerufen werden (falls benötigt).

    Wunsch Ahmad 20.09.2026 — Blaettern: `seite` und `limit` (Standard 20,
    hoechstens ADMIN_VERTRAEGE_MAX). Die Antwort nennt zusaetzlich
    `gesamt` (wie viele es wirklich sind) und `weitere` (ob es noch mehr
    gibt), damit die Oberflaeche "weitere 20 anzeigen" sauber anbieten und
    am Ende ausblenden kann.
    """
    user = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0, "mfa": 0,
                                                     "current_session_id": 0})
    if not user:
        raise HTTPException(404, "Nutzer nicht gefunden")
    # Firmen-Kopf (Wunsch 09/2026): Firmenname + Kundennummer mitliefern.
    if user.get("dealer_id"):
        firma = await db.dealers.find_one(
            {"id": user["dealer_id"]},
            {"_id": 0, "company_name": 1, "kunden_nr": 1})
        if firma:
            user.setdefault("company_name", firma.get("company_name"))
            user["kunden_nr"] = firma.get("kunden_nr")
    # Audit 13.09.2026 (#51): nach Rolle getrennt (Definition wie
    # contracts._vertrag_bereich). Vorher griff beim Sucher der Zweig
    # {dealer_id: <Firma>} und zeigte ALLE Firmenvertraege als "seine";
    # Konten ohne Firma filterten auf {dealer_id: None}.
    # Rollenpruefung 22.09.2026 (RP-151): "firmenweit" nur fuer den HAUPTchef
    # (Zeiger) — ein liegengebliebenes zweites dealer-Konto sieht wie ein
    # Sucher nur die eigenen Vertraege. `ist_chef` steuert auch die
    # Firmen-Verwaltung in der Oberflaeche (UserDetail).
    user["ist_chef"] = await _ist_hauptchef_konto(user)
    if user["ist_chef"]:
        filt, umfang = {"dealer_id": user["dealer_id"]}, "firma"
    else:
        filt, umfang = {"user_id": user_id}, "nutzer"
    limit = max(1, min(int(limit or ADMIN_VERTRAEGE_SEITE), ADMIN_VERTRAEGE_MAX))
    # Wie bei /admin/contracts: die Seitenzahl nach oben begrenzen, sonst
    # sprengt (seite - 1) * limit int64 und pymongo wirft OverflowError (500).
    seite = max(1, min(int(seite or 1), 10 ** 6))
    ueberspringen = (seite - 1) * limit
    gesamt = await db.generated_pdfs.count_documents(filt)
    items = await db.generated_pdfs.find(
        filt, {"_id": 0, "pdf_b64": 0, "pdf_digital_b64": 0},
    ).sort("created_at", -1).skip(ueberspringen).to_list(limit + 1)
    weitere = len(items) > limit
    items = items[:limit]
    # Die Obergrenze bleibt wie bisher: mehr als ADMIN_VERTRAEGE_MAX zeigt
    # die Admin-Ansicht nicht — das wird gesagt, nicht still gezogen.
    erreichbar = min(gesamt, ADMIN_VERTRAEGE_MAX)
    abgeschnitten = gesamt > ADMIN_VERTRAEGE_MAX
    if ueberspringen + len(items) >= ADMIN_VERTRAEGE_MAX:
        weitere = False
    if abgeschnitten:
        log.warning("Admin-Vertragsansicht %s (%s): %s Vertraege, angezeigt "
                    "werden hoechstens %s", user_id, umfang, gesamt,
                    ADMIN_VERTRAEGE_MAX)
    response.headers["X-Truncated"] = "1" if weitere else "0"
    return {"user": user, "contracts": items, "umfang": umfang,
            "abgeschnitten": abgeschnitten, "gesamt": gesamt,
            "erreichbar": erreichbar, "seite": seite, "limit": limit,
            "weitere": weitere}


@router.get("/admin/contracts/{contract_id}/pdf")
async def admin_contract_pdf(contract_id: str, _=Depends(current_admin)):
    """Liefert das PDF eines beliebigen Vertrags an den Admin (read-only)."""
    doc = await db.generated_pdfs.find_one({"id": contract_id})
    if not doc or not doc.get("pdf_b64"):
        raise HTTPException(404, "Vertrag oder PDF nicht gefunden")
    pdf_bytes = base64.b64decode(doc["pdf_b64"])
    raw_name = doc.get("filename") or f"vertrag_{contract_id}.pdf"
    # Rollenprüfung 22.09.2026 (RP-200/RP-351, Welle 2): Starlette kodiert
    # Kopfzeilen als latin-1 — ein 'Š', '–' oder Emoji im Dateinamen gab
    # hier 500. Derselbe Helfer wie in contracts.py (ASCII + filename*).
    from vertrag_dateiname import content_disposition
    return StreamingResponse(
        iter([pdf_bytes]), media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(
            raw_name, fallback=f"vertrag_{contract_id}.pdf")},
    )


# ---------- Stats ----------
@router.get("/admin/stats")
async def admin_stats(_=Depends(current_admin)):
    return {
        "users": await db.users.count_documents({}),
        # "aktiv" heisst: Status aktiv UND nicht abgelaufen (PR-Review 09/2026)
        "active_subs": await db.subscriptions.count_documents(
            {"status": "active",
             "$or": [{"expires_at": None}, {"expires_at": {"$gt": now_iso()}}]}),
        "contracts": await db.generated_pdfs.count_documents({}),
        "appointments": await db.appointments.count_documents({}),
        "comparisons_today": await db.vehicle_comparisons.count_documents({
            "created_at": {"$gte": datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).isoformat()}
        }),
        "open_errors": await db.error_logs.count_documents({"status": "open"}),
    }


# ---------- Audit-Log ----------
@router.get("/admin/audit")
async def admin_audit_log(
    limit: int = 200, action: Optional[str] = None, q: Optional[str] = None,
    _=Depends(current_admin),
):
    """Audit-Trail der Plattform: Logins, Registrierungen, Verträge, Termine,
    Admin-Aktionen. Quelle ist die activity_logs-Collection; Einträge werden
    mit Nutzer-E-Mail/Firma angereichert, damit die Liste lesbar ist."""
    limit = max(1, min(int(limit or 200), 1000))
    query: dict = {}
    if action:
        query["action"] = {"$regex": f"^{re.escape(action)}"}
    items = await db.activity_logs.find(query, {"_id": 0}) \
        .sort("created_at", -1).to_list(limit * 3 if q else limit)

    # E-Mail/Firma nachschlagen (ein Batch-Lookup statt N Einzel-Queries).
    user_ids = {i.get("user_id") for i in items if i.get("user_id")}
    users = await db.users.find(
        {"id": {"$in": list(user_ids)}},
        {"_id": 0, "id": 1, "email": 1, "username": 1, "role": 1, "kontonummer": 1},
    ).to_list(len(user_ids) or 1)
    by_id = {u["id"]: u for u in users}
    out = []
    for i in items:
        u = by_id.get(i.get("user_id")) or {}
        entry = {
            **i,
            "email": u.get("email") or (i.get("meta") or {}).get("email")
                     or (i.get("meta") or {}).get("identifier") or "",
            # Kontonummer (13.09.2026): erste Kennung des Handelnden
            "kontonummer": u.get("kontonummer") or (i.get("meta") or {}).get("kontonummer")
                           or "",
            "username": u.get("username", ""),
            "role": u.get("role", ""),
        }
        if q:
            hay = " ".join(str(v) for v in (
                entry.get("kontonummer"), entry.get("email"), entry.get("username"),
                entry.get("action"), entry.get("ref"),
                str(entry.get("meta") or ""),
            )).lower()
            if q.lower() not in hay:
                continue
        out.append(entry)
        if len(out) >= limit:
            break
    return out


# ---------- Fehler-Meldungen (Error-Reporting an den Admin) ----------
@router.get("/admin/errors")
async def admin_errors(
    status: Optional[str] = None, limit: int = 200, _=Depends(current_admin),
):
    """Alle vom Server erfassten Fehler (unbehandelte Exceptions + gemeldete
    Frontend-Fehler), neueste zuerst. status: open | resolved | (alle)."""
    limit = max(1, min(int(limit or 200), 1000))
    query: dict = {}
    if status in ("open", "resolved"):
        query["status"] = status
    return await db.error_logs.find(query, {"_id": 0}) \
        .sort("created_at", -1).to_list(limit)


@router.put("/admin/errors/{error_id}")
async def admin_resolve_error(
    error_id: str, body: dict = Body(default={}), admin=Depends(current_admin),
):
    """Fehler als erledigt (oder wieder offen) markieren."""
    new_status = body.get("status", "resolved")
    if new_status not in ("open", "resolved"):
        raise HTTPException(400, "status muss 'open' oder 'resolved' sein")
    r = await db.error_logs.update_one(
        {"id": error_id},
        {"$set": {"status": new_status, "resolved_by": _handelnder(admin),
                  "resolved_at": now_iso() if new_status == "resolved" else None}},
    )
    if not r.matched_count:
        raise HTTPException(404, "Fehler-Eintrag nicht gefunden")
    return {"ok": True, "status": new_status}


@router.delete("/admin/errors")
async def admin_clear_resolved_errors(_=Depends(current_admin)):
    """Alle als erledigt markierten Fehler löschen (Aufräumen)."""
    r = await db.error_logs.delete_many({"status": "resolved"})
    return {"ok": True, "deleted": r.deleted_count}


# ---------- Monitoring (Priorität 5 — Betriebsueberwachung) ----------
@router.get("/admin/monitoring")
async def admin_monitoring(admin=Depends(current_admin)):
    """Betriebszustand auf einen Blick: unerwartete Fehler, Job-Rueckstau,
    Anbieter-Slots, Zahlen fuer Alarme. Gedacht fuer den Admin-Bereich UND
    fuer externe Ueberwachung (z.B. Uptime-Robot auf die Kennzahlen)."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    vor_1h = (now - timedelta(hours=1)).isoformat()

    # Unerwartete 500er der letzten Stunde (error_logs schreibt der
    # globale Exception-Handler)
    fehler_1h = await db.error_logs.count_documents(
        {"created_at": {"$gte": vor_1h}})

    # Linkpruefungs-Jobs: Rueckstau + Alter des aeltesten wartenden Jobs
    queued = await db.link_jobs.count_documents({"status": "queued"})
    processing = await db.link_jobs.count_documents({"status": "processing"})
    failed_1h = await db.link_jobs.count_documents(
        {"status": "failed", "updated_at": {"$gte": now - timedelta(hours=1)}})
    aeltester = await db.link_jobs.find_one(
        {"status": "queued"}, {"_id": 0, "created_at": 1},
        sort=[("created_at", 1)])
    wartezeit_s = None
    if aeltester and aeltester.get("created_at"):
        ca = aeltester["created_at"]
        if ca.tzinfo is None:
            ca = ca.replace(tzinfo=timezone.utc)
        wartezeit_s = int((now - ca).total_seconds())

    # Anbieter-Slots (zentrale Begrenzung)
    slots = {row["provider"]: row.get("active", 0)
             async for row in db.provider_limits.find({}, {"_id": 0})}
    heute = now.strftime("%Y-%m-%d")
    abrufe_heute = {row["provider"]: row.get("calls", 0)
                    async for row in db.provider_stats.find(
                        {"date": heute}, {"_id": 0})}

    # Ampel: gruen | gelb | rot — einfache Schwellen fuer Alarme
    ampel = "gruen"
    hinweise = []
    if fehler_1h > 0:
        ampel = "gelb"
        hinweise.append(f"{fehler_1h} unerwartete Fehler in der letzten Stunde")
    if queued > 50 or (wartezeit_s or 0) > 300:
        ampel = "gelb"
        hinweise.append("Job-Rueckstau (Warteschlange/Wartezeit erhoeht)")
    if fehler_1h > 20 or (wartezeit_s or 0) > 900:
        ampel = "rot"
    return {
        "ampel": ampel, "hinweise": hinweise, "zeitpunkt": now.isoformat(),
        "unerwartete_fehler_letzte_stunde": fehler_1h,
        "link_jobs": {"queued": queued, "processing": processing,
                      "failed_letzte_stunde": failed_1h,
                      "aeltester_wartender_sekunden": wartezeit_s},
        "anbieter_slots_aktiv": slots,
        "anbieter_abrufe_heute": abrufe_heute,
    }


# ---------- Verkaufspakete (Phase 2 — Vergabe durch den Admin) ----------
@router.put("/admin/dealers/{dealer_id}/sale-plan")
async def admin_set_sale_plan(dealer_id: str, body: dict = Body(...),
                              admin=Depends(current_super_admin)):
    """Verkaufspaket zuweisen/ändern. tier: s5|s10|s20|s30|s40|enterprise
    oder null zum Entfernen. Bei Neuvergabe/Wechsel startet der rollierende
    Abrechnungszeitraum neu (Buchungsdatum)."""
    from routes.team import SALE_PLANS
    dealer = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "id": 1, "sale_plan": 1})
    if not dealer:
        raise HTTPException(404, "Händler nicht gefunden")
    tier = body.get("tier")
    if tier is None:
        await db.dealers.update_one({"id": dealer_id}, {"$unset": {"sale_plan": ""}})
        await log_activity_sicher(admin.get("dealer_id", ""), admin["id"],
                           "admin.verkaufsplan.entfernt", ref=dealer_id)
        return {"ok": True, "sale_plan": None}
    if tier not in SALE_PLANS:
        raise HTTPException(400, f"Unbekanntes Paket: {tier}")
    old = (dealer.get("sale_plan") or {})
    plan = {
        "tier": tier,
        # Zeitraum bleibt bei reiner Quota-Erhöhung erhalten, startet aber
        # neu, wenn vorher kein Paket existierte.
        "period_start": old.get("period_start") or now_iso(),
    }
    # Laufzeit: months=N begrenzt das Paket auf N x 30 Tage. Verlaengerung
    # rechnet ab dem bisherigen Ablauf weiter (nicht ab heute), damit dem
    # Haendler keine bezahlte Restlaufzeit verloren geht. Ohne months bleibt
    # ein bestehendes Ablaufdatum erhalten; gab es keins, ist das Paket
    # unbefristet (wie bisher).
    months = body.get("months")
    if months:
        try:
            months = max(1, min(24, int(months)))
        except (TypeError, ValueError):
            raise HTTPException(400, "months muss eine Zahl (1–24) sein")
        base = datetime.now(timezone.utc)
        old_vu = old.get("valid_until")
        if old_vu:
            try:
                prev = datetime.fromisoformat(old_vu)
                if prev.tzinfo is None:
                    prev = prev.replace(tzinfo=timezone.utc)
                if prev > base:
                    base = prev
            except (TypeError, ValueError):
                pass
        plan["valid_until"] = (base + timedelta(days=30 * months)).isoformat()
    elif old.get("valid_until"):
        plan["valid_until"] = old["valid_until"]
        # Rollenpruefung 22.09.2026 (RP-508): ein ABGELAUFENES Paket wurde ohne
        # months mit seinem alten Ablauf "neu vergeben" — also sofort wieder
        # abgelaufen. Jetzt gilt es dann 30 Tage ab heute (wie months=1).
        vu = _ablauf_parsen(old["valid_until"])
        if vu is not None and vu <= datetime.now(timezone.utc):
            months = 1
            plan["valid_until"] = (datetime.now(timezone.utc)
                                   + timedelta(days=30)).isoformat()
    if tier == "enterprise":
        # Nachpruefung Runde 14 (Befund 32): ungueltige Eingabe ("abc",
        # "1o0", negativ) wurde still zu custom_quota=None — mit
        # VERKAUF_KOSTENLOS=false hiess das quota=0, und resale.py
        # ueberspringt bei 0 die Kontingentpruefung (= unbegrenzt). Jetzt
        # 400; leer/None bleibt erlaubt (Enterprise ohne feste Zahl).
        cq = body.get("custom_quota")
        if cq is None or (isinstance(cq, str) and cq.strip() == ""):
            plan["custom_quota"] = None
        else:
            if isinstance(cq, bool):
                raise HTTPException(400, "custom_quota muss eine ganze Zahl sein")
            if isinstance(cq, float) and cq.is_integer():
                cq = int(cq)
            try:
                n = int(cq) if isinstance(cq, int) else int(str(cq).strip())
            except (TypeError, ValueError):
                raise HTTPException(400, "custom_quota muss eine ganze Zahl sein")
            if n < 0:
                raise HTTPException(400, "custom_quota darf nicht negativ sein")
            plan["custom_quota"] = n or None
    # Runde 8 (15.09.2026, Liste 4 Nr. 13): Stand-Pruefung — zwei parallele
    # Admin-Aktionen (Verlaengerung, Wechsel, Kontingent) lasen denselben alten
    # Ablauf und ueberschrieben sich gegenseitig; jetzt greift nur die erste,
    # die zweite bekommt "bitte neu laden".
    res = await db.dealers.update_one(
        {"id": dealer_id,
         "sale_plan.tier": old.get("tier"),
         "sale_plan.valid_until": old.get("valid_until"),
         "sale_plan.period_start": old.get("period_start")},
        {"$set": {"sale_plan": plan}})
    if res.matched_count == 0:
        raise HTTPException(409, "Das Verkaufspaket wurde gerade parallel geändert — "
                                 "bitte neu laden und erneut speichern.")
    zahlung = None
    if months and not _verkauf_kostenlos():
        # Rollenpruefung 22.09.2026 (RP-508): wie bei Sucher-Abo und
        # Marktplatz-Zugang entsteht zur bezahlten Laufzeit EIN Zahlungs-
        # eintrag (Upsert ueber vorgang_id). Enterprise ohne Listenpreis: 0 EUR
        # mit Vermerk — der Betrag wird dann per "Zahlung nachtragen" erfasst.
        vid = str(uuid.uuid4())
        preis = SALE_PLANS.get(tier, {}).get("price")
        await db.manual_payments.update_one(
            {"vorgang_id": vid},
            {"$setOnInsert": {
                "id": str(uuid.uuid4()), "dealer_id": dealer_id,
                "subject_user_id": None, "plan": f"verkauf_{tier}",
                "amount": round(float(preis) * int(months), 2) if preis else 0.0,
                "currency": "EUR", "paid_at": now_iso()[:10],
                "period_until": plan.get("valid_until"),
                "zahlungsart": "rechnung_bezahlt",
                "note": "" if preis else "Enterprise ohne Listenpreis — Betrag nachtragen",
                "quelle": "manuell", "vorgang_id": vid,
                "recorded_by": _handelnder(admin), "created_at": now_iso()}},
            upsert=True)
        zahlung = vid
    await log_activity_sicher(admin.get("dealer_id", ""), admin["id"],
                       "admin.verkaufsplan.gesetzt", ref=dealer_id,
                       meta={"tier": tier, "monate": months or None,
                             "zahlung_vorgang": zahlung})
    return {"ok": True, "sale_plan": plan}


def _verkauf_kostenlos() -> bool:
    """Schalter aus routes.team (Verkaufen kostenlos) — spaet gelesen."""
    from routes.team import VERKAUF_KOSTENLOS
    return bool(VERKAUF_KOSTENLOS)


def _seite_kopf(response: Response, items: list, limit: int) -> list:
    """Phase 4 (4.3): eine Zeile mehr lesen als angezeigt — ist sie da, wird
    die Liste gekuerzt und der Kopf X-Truncated gesetzt (wie bei Terminen)."""
    if len(items) > limit:
        response.headers["X-Truncated"] = "1"
        return items[:limit]
    return items


@router.get("/admin/plan-requests")
async def admin_plan_requests(response: Response, status: Optional[str] = None,
                              type: Optional[str] = None,
                              limit: int = 200, seite: int = 1,
                              _=Depends(current_super_admin)):
    # Runde 12: Freischaltungen sind Super-Admin-Sache (Oberflaeche:
    # superOnly). Firmenname, Ansprechpartner, E-Mail, Telefon und
    # Nachricht der Anfragen lasen vorher auch normale Admins.
    query: Dict = {}
    if status:
        query["status"] = status
    if type:
        query["type"] = type
    limit = max(1, min(int(limit), 1000))
    seite = max(1, int(seite))
    items = await db.plan_requests.find(query, {"_id": 0}) \
        .sort("created_at", -1).skip((seite - 1) * limit).to_list(limit + 1)
    items = _seite_kopf(response, items, limit)
    # Rollenprüfung 22.09.2026 (RP-511, Welle 2): "mit Einladung" anzeigen —
    # mit der einladenden Firma und ob die Einladung noch gilt. Das Token
    # selbst verlaesst den Server nicht (es ist ein Zugangsschluessel).
    for item in items:
        token = item.pop("invite_token", None)
        if token:
            item["einladung"] = await _einladung_info(token)
    return items


async def _einladung_info(token: str) -> dict:
    """Firma und Gueltigkeit einer Einladung (fuer die Anzeige). Wirft nie."""
    try:
        inv = await db.dealer_invites.find_one(
            {"token": token}, {"_id": 0, "dealer_id": 1, "expires_at": 1,
                               "used_count": 1, "max_uses": 1})
        if not inv:
            return {"firma": "", "gueltig": False}
        firma = await db.dealers.find_one({"id": inv.get("dealer_id")},
                                          {"_id": 0, "company_name": 1})
        gueltig = (str(inv.get("expires_at") or "") > now_iso()
                   and (inv.get("used_count") or 0) < (inv.get("max_uses") or 0))
        return {"firma": (firma or {}).get("company_name", ""), "gueltig": gueltig}
    except Exception:  # noqa: BLE001 — nur Anzeige
        log.exception("Einladung zur Zugangsanfrage nicht lesbar")
        return {"firma": "", "gueltig": None}


# ---------- Sucher-Abo freischalten (manuell) ----------
class AboFreischaltenIn(BaseModel):
    """Freischalten/Verlaengern (plan) oder Aufheben (plan=null).
    Audit 09/2026: feste Plantypen, echte Betragspruefung (kein stilles
    Ersetzen durch den Listenpreis, kein 0 EUR), Zahlungsart mit
    Pflichtbegruendung bei Kulanz."""
    plan: Optional[Literal["monthly", "yearly", "probe3", "probe5"]] = None
    gueltig_bis: Optional[str] = Field(default=None, max_length=30)
    betrag: Optional[Decimal] = Field(default=None, gt=Decimal("0"),
                                      le=Decimal("100000"))
    gezahlt_am: Optional[str] = Field(default=None, max_length=10)
    notiz: str = Field(default="", max_length=500)
    zahlungsart: Literal["rechnung_bezahlt", "kulanz", "probe"] = "rechnung_bezahlt"
    grund: str = Field(default="", max_length=300)
    waehrung: Literal["EUR"] = "EUR"
    # Rollenpruefung 22.09.2026 (RP-225/RP-376): Bezug zur Anfrage und ein
    # Schluessel je Klick. Dieselbe Anfrage bzw. derselbe Schluessel bucht
    # nie ein zweites Mal — eine Wiederholung (zweiter Tab, erneuter Klick
    # nach Netzfehler, obwohl der erste durchlief) bekommt das Ergebnis des
    # ersten Vorgangs zurueck.
    anfrage_id: Optional[str] = Field(default=None, max_length=100)
    idempotenz_schluessel: Optional[str] = Field(default=None, min_length=8, max_length=80,
                                                 pattern=r"^[A-Za-z0-9_-]+$")


def _datum_pruefen_400(wert, feld: str) -> str:
    """'JJJJ-MM-TT' oder leer; sonst 400 (nicht 422, damit die Oberflaeche
    eine deutsche Meldung zeigt)."""
    if not wert:
        return ""
    w = str(wert).strip()[:10]
    try:
        datetime.strptime(w, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, f"{feld} muss ein Datum JJJJ-MM-TT sein")
    return w


class KiFreischaltenIn(BaseModel):
    """KI-Bewertung je Konto an/aus (Wunsch Ahmad 25.09.2026 abends, wie Abo)."""
    aktiv: bool
    grund: Optional[str] = Field(default=None, max_length=300)


@router.post("/admin/sucher/{sucher_id}/ki")
async def admin_set_sucher_ki(sucher_id: str, body: KiFreischaltenIn,
                              admin=Depends(current_super_admin)):
    """KI-Bewertung (Abholung + Vertrag) fuer ein Konto freischalten oder
    sperren. NUR Super-Admin. Unabhaengig vom Abo: ohne Abo laeuft der
    Vertrag ohnehin nicht; die Abholung haengt am Hauptchef-Konto der Firma.
    Wirkt sofort — die Karten fragen bei jedem Aufruf nach."""
    sucher = await db.users.find_one(
        {"id": sucher_id, "role": {"$in": ["sucher", "dealer"]}},
        {"_id": 0, "id": 1, "dealer_id": 1, "active": 1, "ki_aktiv": 1, "loeschung": 1})
    if not sucher:
        raise HTTPException(404, "Sucher nicht gefunden")
    if body.aktiv and sucher.get("active") is False:
        raise HTTPException(400, "Dieses Konto ist deaktiviert — erst das Konto wieder aktivieren.")
    if body.aktiv and (sucher.get("loeschung") or {}).get("status") == "laeuft":
        raise HTTPException(409, "Dieses Konto wird gerade gelöscht — keine Freischaltung mehr.")
    await db.users.update_one({"id": sucher_id},
                              {"$set": {"ki_aktiv": bool(body.aktiv), "ki_aktiv_seit": now_iso() if body.aktiv else None,
                                        "ki_aktiv_von": _handelnder(admin), "updated_at": now_iso()}})
    await log_activity_sicher(admin.get("dealer_id", ""), admin["id"],
                              "admin.sucher.ki." + ("freigeschaltet" if body.aktiv else "gesperrt"),
                              ref=sucher_id, meta={"grund": body.grund or "", "dealer_id": sucher.get("dealer_id")})
    return {"ok": True, "ki_aktiv": bool(body.aktiv)}


@router.post("/admin/sucher/{sucher_id}/abo")
async def admin_set_sucher_abo(sucher_id: str, body: AboFreischaltenIn,
                               admin=Depends(current_super_admin)):
    """Sucher-Abo aktivieren/verlaengern (plan: monthly|yearly) oder mit
    plan=null aufheben. NUR Super-Admin (Betreiber). Legt einen
    idempotenten Vorgang (abo_vorgaenge) an — jeder Schritt ist
    wiederholbar, ein Reparaturlauf holt Abgebrochenes nach."""
    from routes.team import SUCHER_PLANS
    sucher = await db.users.find_one(
        {"id": sucher_id, "role": {"$in": ["sucher", "dealer"]}},
        {"_id": 0, "id": 1, "dealer_id": 1, "email": 1, "active": 1, "role": 1,
         "loeschung": 1})
    if not sucher:
        raise HTTPException(404, "Sucher nicht gefunden")
    # Nachpruefung 20.09.2026, Nr. 63: gesucht wurde nur nach Id und Rolle.
    # Ein deaktiviertes Konto liess sich kostenpflichtig verlaengern und
    # blieb trotzdem gesperrt — Zahlung und nutzbarer Zugang liefen
    # auseinander. Das Aufheben (plan=None) bleibt erlaubt, es macht
    # nichts kostenpflichtig.
    if body.plan is not None and sucher.get("active") is False:
        raise HTTPException(
            400, "Dieses Konto ist deaktiviert — eine Freischaltung wuerde "
                 "bezahlt, das Konto bliebe aber gesperrt. Erst das Konto "
                 "wieder aktivieren.")
    if body.plan is not None:
        # Rollenpruefung 22.09.2026 (RP-231/RP-382): dieselbe Ueberlegung fuer
        # Konto-Loeschung, Firmenloeschung und Firmensperre (Chef gesperrt,
        # die Sucher bleiben 'active') — bezahlt, aber nicht nutzbar.
        if (sucher.get("loeschung") or {}).get("status") == "laeuft":
            raise HTTPException(409, "Dieses Konto wird gerade gelöscht — keine "
                                     "Freischaltung mehr.")
        if sucher.get("dealer_id"):
            firma_doc = await db.dealers.find_one({"id": sucher["dealer_id"]},
                                                  {"_id": 0, "loeschung": 1})
            if ((firma_doc or {}).get("loeschung") or {}).get("status") == "laeuft":
                raise HTTPException(409, "Die Firma dieses Kontos wird gerade gelöscht — "
                                         "keine Freischaltung mehr.")
            if await firma_gesperrt(sucher["dealer_id"]):
                raise HTTPException(409, "Die Firma dieses Kontos ist gesperrt (Chef "
                                         "deaktiviert) — eine Freischaltung wäre bezahlt, "
                                         "aber nicht nutzbar. Erst die Firma entsperren.")
    # Rollenpruefung 22.09.2026 (RP-229/RP-380, RP-051): Beim Hauptchef zaehlt
    # auch das alte FIRMEN-Abo (deps.subscription_for) — Aufheben und
    # Verlaengern muessen es mit erfassen.
    ist_chef = await _ist_hauptchef_konto(sucher)
    if body.plan is None:
      # Aufheben = NUR die kostenpflichtige Sucher-Funktion sperren
      # (Login/Bestand bleiben). Auch Lifetime wird damit inaktiv.
      # Pruefung 14.09.2026 (G7): dieselbe Sperre wie beim Freischalten —
      # sonst konnten sich Aufheben und Freischalten ueberholen.
      async with _sperre(f"abo:{sucher_id}", _handelnder(admin)) as wache:
        wache.pruefen()
        aufheben = {"$set": {"status": "cancelled", "expires_at": now_iso(),
                             "aufgehoben_von": _handelnder(admin),
                             "updated_at": now_iso()}}
        await db.subscriptions.update_many(
            {"subject_user_id": sucher_id, "status": {"$in": ["active", "cancelled"]}},
            aufheben)
        firmen_abos = 0
        if ist_chef:
            # Vorher meldete die Oberflaeche "Abo aufgehoben", der Chef suchte
            # aber ueber das Firmen-Abo (ohne subject_user_id) weiter.
            firmen_abos = (await db.subscriptions.update_many(
                {**_FIRMEN_ABO(sucher["dealer_id"]),
                 "status": {"$in": ["active", "cancelled"]}},
                aufheben)).modified_count
        await log_activity_sicher(admin.get("dealer_id", ""), admin["id"],
                           "admin.sucher.abo.aufgehoben", ref=sucher_id,
                           meta={"grund": body.grund, "firmen_abos": firmen_abos})
        # Der TATSAECHLICHE Stand danach (dieselbe Regel wie die Zugriffspruefung).
        danach = await subscription_for(sucher)
        return {"ok": True, "active": bool(danach.get("active")),
                "subscription": danach}
    if body.plan not in SUCHER_PLANS:
        raise HTTPException(400, f"Unbekannter Abo-Zeitraum: {body.plan}")
    # Wunsch Ahmad 20.09.2026 — Probe-Abo (kostenlos, wenige Tage):
    from routes.team import ist_probe
    probe = ist_probe(body.plan)
    anfrage = None
    if body.anfrage_id:
        # Rollenpruefung 22.09.2026 (RP-225/RP-376, RP-224/RP-375): die
        # Freischaltung gilt GENAU dieser Anfrage — sie muss zu diesem Konto
        # gehoeren, und ueber eine Anfrage gibt es kein Probe-Abo (Proben
        # vergibt nur der Betreiber direkt; alte Anfragen mit probe3/probe5
        # vergaben es vorher still und kostenlos).
        anfrage = await db.plan_requests.find_one(
            {"id": body.anfrage_id, "type": "sucher_abo"},
            {"_id": 0, "id": 1, "subject_user_id": 1, "status": 1, "wanted_plan": 1})
        if not anfrage:
            raise HTTPException(404, "Abo-Anfrage nicht gefunden")
        if anfrage.get("subject_user_id") != sucher_id:
            raise HTTPException(400, "Die Anfrage gehört zu einem anderen Konto")
        if probe:
            raise HTTPException(400, "Probe-Abos werden nicht über eine Anfrage vergeben — "
                                     "bitte monatlich/jährlich wählen oder die Probe direkt "
                                     "in der Firmenansicht vergeben.")
    if probe:
        # FALLE, die hier sonst zuschlaegt: _abo_vorgang_ausfuehren ersetzt
        # ALLE bisherigen Abos durch das neue. Eine Probe auf ein laufendes
        # Jahres-Abo wuerde also ein bezahltes Jahr durch drei Tage
        # ersetzen. Deshalb nur fuer Konten OHNE laufendes bezahltes Abo.
        # Rollenpruefung 22.09.2026 (RP-228/RP-379): diese Pruefung steht
        # jetzt in _abo_freischalten UNTER der Sperre (vorher davor — eine
        # parallele bezahlte Freischaltung konnte dazwischen fertig werden).
        if body.betrag is not None:
            raise HTTPException(400, "Ein Probe-Abo ist kostenlos — kein Betrag.")
        if body.gueltig_bis:
            raise HTTPException(
                400, "Beim Probe-Abo entscheidet die Laufzeit des Plans "
                     "(3 bzw. 5 Tage) — kein eigenes Datum.")
        body.zahlungsart = "probe"
    elif body.zahlungsart == "probe":
        raise HTTPException(400, "Zahlungsart 'probe' gibt es nur beim Probe-Abo")
    if body.zahlungsart == "kulanz" and not body.grund.strip():
        raise HTTPException(400, "Kulanz-Freischaltung braucht eine Begruendung")
    gezahlt_am = _datum_pruefen_400(body.gezahlt_am, "gezahlt_am")
    # Doppelklick-/Doppelanfrage-Schutz mit Besitzer-Token (Audit 09/2026):
    # geloest wird NUR die eigene Sperre; eine verwaiste Sperre wird per
    # Compare-and-Swap uebernommen, nie blind geloescht.
    sperre = f"abo:{sucher_id}"
    besitzer = str(uuid.uuid4())
    async with _sperre(sperre, besitzer) as wache:
        return await _abo_freischalten(sucher, sucher_id, body, gezahlt_am,
                                       admin, wache, ist_chef=ist_chef)


def _FIRMEN_ABO(dealer_id: Optional[str]) -> dict:
    """Filter fuer das alte firmenweite Abo (ohne subject_user_id; {feld: None}
    trifft in MongoDB fehlend UND null) — die Grundlage des Rueckfalls in
    deps.subscription_for fuer den Hauptchef."""
    return {"dealer_id": dealer_id, "subject_user_id": None}


async def _massgebliches_abo(sucher_id: str, dealer_id: Optional[str],
                             ist_chef: bool, *,
                             auch_abgelaufen: bool = False) -> Optional[dict]:
    """Das Abo-Dokument, das fuer dieses Konto GERADE Zugang gibt — dieselbe
    Auswahl wie deps.subscription_for: das juengste nicht ersetzte eigene Abo,
    beim Hauptchef ersatzweise das juengste Firmen-Abo. Nur wenn es wirklich
    aktiv ist (sub_status_from_doc), sonst None.

    Rollenpruefung 22.09.2026 (RP-050/RP-149, RP-051/RP-150): Restlaufzeit,
    Probe-Sperre und 'Gueltig bis' suchten nur status 'active' — ein
    gekuendigtes, aber noch laufendes Abo ('cancelled') und beim Chef das
    Firmen-Abo, das die Oberflaeche anzeigt, fielen durch.

    Rollenpruefung 22.09.2026 (Welle 3, test_betreiber::test_14):
    `auch_abgelaufen=True` (nur fuer 'Gueltig bis') liefert ersatzweise
    dasselbe juengste Dokument, wenn es noch status 'active' traegt und nur
    per DATUM abgelaufen (oder ohne lesbares Datum) ist. Der Betreiber steuert
    die Laufzeit frei: ein Datum in der Vergangenheit sperrt, ein neues Datum
    gibt den Zugang ohne neue Zahlung zurueck — so war es vor RP-050, und die
    Umstellung auf sub_status_from_doc hatte das mit 404 verbaut. Ein
    aufgehobenes Abo ('cancelled' mit Ablauf in der Vergangenheit) bleibt
    aussen vor: es wird ueber das Datum nicht wiederbelebt."""
    eigen = await db.subscriptions.find_one(
        {"subject_user_id": sucher_id, "status": {"$ne": "ersetzt"},
         "$or": [{"dealer_id": dealer_id}, {"dealer_id": {"$exists": False}},
                 {"dealer_id": None}]},
        {"_id": 0}, sort=[("created_at", -1)])
    if sub_status_from_doc(eigen)["active"]:
        return eigen
    firma = None
    if ist_chef and dealer_id:
        firma = await db.subscriptions.find_one(
            {**_FIRMEN_ABO(dealer_id), "status": {"$ne": "ersetzt"}},
            {"_id": 0}, sort=[("created_at", -1)])
        if sub_status_from_doc(firma)["active"]:
            return firma
    if auch_abgelaufen:
        # Erst das eigene, beim Chef dann das Firmen-Abo — dieselbe Reihenfolge
        # wie deps.subscription_for. Fehlt das Statusfeld, gilt es wie in
        # sub_status_from_doc als 'active'.
        for kandidat in (eigen, firma):
            if kandidat and (kandidat.get("status") or "active") == "active":
                return kandidat
    return None


def _vorgang_antwort(v: dict, **extra) -> dict:
    return {"ok": True, "active": True, "plan": v.get("plan"),
            "expires_at": v.get("expires_at"), "vorgang_id": v.get("id"),
            "betrag": v.get("betrag"), **extra}


SPERRE_FRIST_S = 45           # so lange gilt eine Sperre ohne Herzschlag
SPERRE_HERZSCHLAG_S = 15      # so oft wird sie waehrend der Arbeit verlaengert


class _Wache:
    """Zustand einer laufenden Sperre (Nachpruefung 20.09.2026, Nr. 50).

    `pruefen()` steht vor jedem entscheidenden Schreibvorgang: hat der
    Herzschlag die Sperre verloren, bricht der Vorgang hier ab, statt
    parallel zu einem zweiten weiterzuschreiben."""

    def __init__(self, name: str):
        self.name = name
        self.verloren = False

    def pruefen(self) -> None:
        if self.verloren:
            raise HTTPException(
                409, "Der Vorgang wurde abgebrochen, weil die Sperre "
                     "zwischenzeitlich verlorenging (Datenbank-Stoerung). "
                     "Bitte noch einmal ausloesen — es wurde nichts doppelt "
                     "gebucht.")


@asynccontextmanager
async def _sperre(name: str, besitzer: str):
    """Sperre mit Ablauf und Herzschlag (Audit 09/2026).

    Vorher galt eine feste Frist von 60 s ab Beginn: dauerte eine
    Freischaltung laenger, konnte ein zweiter Vorgang die Sperre
    uebernehmen, waehrend der erste noch schrieb — doppelte
    Verlaengerungen und Zahlungen waren moeglich. Jetzt traegt die Sperre
    ein Ablaufdatum, das ein Hintergrund-Herzschlag alle 15 s
    weiterschiebt; uebernommen wird nur eine wirklich abgelaufene Sperre."""
    def _bis(sekunden: int) -> str:
        return (datetime.now(timezone.utc)
                + timedelta(seconds=sekunden)).isoformat()
    belegt = HTTPException(409, "Freischaltung laeuft gerade — bitte einen "
                                "Moment warten (Doppelklick)")
    try:
        await db.sperren.insert_one({"_id": name, "owner": besitzer,
                                     "seit": now_iso(),
                                     "bis": _bis(SPERRE_FRIST_S)})
    except DuplicateKeyError:
        alt = await db.sperren.find_one({"_id": name}) or {}
        # Alte Sperren ohne "bis": auf die frueheren 60 s ab "seit" abbilden.
        frist = alt.get("bis")
        if not frist and alt.get("seit"):
            try:
                frist = (datetime.fromisoformat(alt["seit"])
                         + timedelta(seconds=60)).isoformat()
            except ValueError:
                frist = None
        if frist and frist > now_iso():
            raise belegt
        r = await db.sperren.update_one(
            {"_id": name, "owner": alt.get("owner")},
            {"$set": {"owner": besitzer, "seit": now_iso(),
                      "bis": _bis(SPERRE_FRIST_S)}})
        if r.modified_count == 0:
            raise belegt

    # Nachpruefung 20.09.2026, Nr. 50: Verlor der Herzschlag die Sperre,
    # beendete er nur sich selbst — der geschuetzte Code lief weiter, und
    # ein neuer Besitzer konnte gleichzeitig denselben Vorgang starten.
    # Jetzt merkt sich die Wache den Verlust; vor jedem entscheidenden
    # Schreibvorgang fragt der geschuetzte Code sie mit pruefen().
    wache = _Wache(name)

    async def _herzschlag():
        while True:
            await asyncio.sleep(SPERRE_HERZSCHLAG_S)
            try:
                r = await db.sperren.update_one(
                    {"_id": name, "owner": besitzer},
                    {"$set": {"bis": _bis(SPERRE_FRIST_S)}})
            except Exception:  # noqa: BLE001
                # Eine Stoerung ist KEIN Beweis fuer den Verlust — beim
                # naechsten Schlag noch einmal versuchen.
                continue
            if r.matched_count == 0:
                wache.verloren = True       # Sperre gehoert uns nicht mehr
                return
    schlag = asyncio.create_task(_herzschlag())
    try:
        yield wache
    finally:
        schlag.cancel()
        try:
            await schlag
        except (asyncio.CancelledError, Exception):   # noqa: B014
            pass
        await db.sperren.delete_one({"_id": name, "owner": besitzer})


async def _abo_freischalten(sucher: dict, sucher_id: str, body: AboFreischaltenIn,
                            gezahlt_am: str, admin: dict, wache=None,
                            ist_chef: bool = False) -> dict:
    from routes.team import SUCHER_PLANS, ist_probe
    plan = body.plan
    days = SUCHER_PLANS[plan]["days"]
    dealer_id = sucher.get("dealer_id")
    # Rollenpruefung 22.09.2026 (RP-225/RP-376): Wiederholung erkennen, BEVOR
    # etwas gebucht wird (unter der Sperre, also ohne Rennen mit dem ersten
    # Aufruf). Ein noch laufender Vorgang wird zu Ende gefuehrt statt verdoppelt.
    frueher = None
    if body.idempotenz_schluessel:
        frueher = await db.abo_vorgaenge.find_one(
            {"subject_user_id": sucher_id,
             "idempotenz_schluessel": body.idempotenz_schluessel}, {"_id": 0})
    if not frueher and body.anfrage_id:
        frueher = await db.abo_vorgaenge.find_one(
            {"subject_user_id": sucher_id, "anfrage_id": body.anfrage_id,
             "status": {"$in": ["laeuft", "fertig"]}}, {"_id": 0})
    if frueher:
        if frueher.get("status") == "laeuft":
            await _abo_vorgang_ausfuehren(frueher)
        return _vorgang_antwort(frueher, bereits_freigeschaltet=True)
    if body.anfrage_id:
        offen = await db.plan_requests.find_one(
            {"id": body.anfrage_id, "status": "offen"}, {"_id": 0, "id": 1})
        if not offen:
            raise HTTPException(409, "Diese Anfrage ist bereits erledigt oder abgelehnt — "
                                     "es wurde nichts gebucht. Bitte die Liste neu laden.")
    # Rollenpruefung 22.09.2026 (RP-050, RP-051): der Stand, der gerade Zugang
    # gibt — auch gekuendigt-aber-laufend und beim Chef das Firmen-Abo.
    laufend = await _massgebliches_abo(sucher_id, dealer_id, ist_chef)
    if ist_probe(plan):
        # Rollenpruefung 22.09.2026 (RP-228/RP-379, RP-230/RP-381): Probe-Sperre
        # HIER unter der Sperre; Ablauf als Datum verglichen (vorher String mit
        # gemischten Zeitzonen, ein datetime-Altwert warf 500). Jedes noch
        # laufende bezahlte Abo zaehlt — auch 'cancelled' und beim Chef das
        # Firmen-Abo. Unlesbares Ablaufdatum gilt als laufend (fail-closed:
        # lieber keine Probe als ein bezahltes Jahr durch drei Tage ersetzen).
        jetzt_dt = datetime.now(timezone.utc)
        kandidaten = [d async for d in db.subscriptions.find(
            {"subject_user_id": sucher_id, "status": {"$in": ["active", "cancelled"]},
             "plan": {"$nin": ["probe3", "probe5"]}},
            {"_id": 0, "plan": 1, "expires_at": 1})]
        if ist_chef and dealer_id:
            kandidaten += [d async for d in db.subscriptions.find(
                {**_FIRMEN_ABO(dealer_id), "status": {"$in": ["active", "cancelled"]},
                 "plan": {"$nin": ["probe3", "probe5"]}},
                {"_id": 0, "plan": 1, "expires_at": 1})]
        for bezahlt in kandidaten:
            ea = _ablauf_parsen(bezahlt.get("expires_at"))
            if ea is None or ea > jetzt_dt:
                raise HTTPException(
                    400, "Dieses Konto hat bereits ein bezahltes Abo "
                         f"({bezahlt.get('plan')}, gueltig bis "
                         f"{str(bezahlt.get('expires_at') or 'unbefristet')[:10]}). Ein Probe-Abo "
                         f"wuerde es ersetzen — erst das bezahlte Abo aufheben.")
    gueltig_bis = _gueltig_bis_parsen(body.gueltig_bis)
    if gueltig_bis:
        # Wunsch 09/2026: der Betreiber schreibt direkt "bis wann gueltig" —
        # ab dann sperrt die Sucher-Funktion automatisch (Abo-Ablaufpruefung).
        expires_at = gueltig_bis
    else:
        # Restlaufzeit erhalten: erneutes Freischalten verlaengert ab dem
        # bisherigen Ablauf, nicht ab "jetzt".
        if ist_probe(plan):
            # Die Probe beginnt JETZT — nicht am Ende einer Restlaufzeit.
            # Sonst ergaebe eine zweite Probe sechs statt drei Tage.
            expires_at = (datetime.now(timezone.utc)
                          + timedelta(days=days)).isoformat()
        else:
            basis = _restlaufzeit_basis((laufend or {}).get("expires_at"))
            expires_at = (basis + timedelta(days=days)).isoformat()
    if body.zahlungsart in ("kulanz", "probe"):
        # Probe-Abo (20.09.2026): kostenlos wie Kulanz, aber ein eigener
        # Grund — in der Zahlungsliste soll "Probe" stehen, nicht "Kulanz".
        betrag = 0.0
    elif body.betrag is not None:
        betrag = round(float(body.betrag), 2)
    else:
        betrag = float(SUCHER_PLANS[plan]["price"])
    vorgang = {
        "id": str(uuid.uuid4()), "typ": "freischaltung",
        "subject_user_id": sucher_id, "dealer_id": sucher.get("dealer_id"),
        "plan": plan, "expires_at": expires_at, "betrag": betrag,
        "waehrung": "EUR", "zahlungsart": body.zahlungsart,
        "grund": body.grund.strip(), "gezahlt_am": gezahlt_am or now_iso()[:10],
        "notiz": body.notiz.strip(),
        "admin_id": admin["id"], "admin_email": _handelnder(admin),
        "status": "laeuft", "schritte": {},
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    # Rollenpruefung 22.09.2026 (RP-225/RP-376, RP-051): Bezug fuer die
    # Wiederholungserkennung; beim Hauptchef wird das alte Firmen-Abo mit
    # ersetzt (seine Resttage stecken jetzt im neuen, persoenlichen Abo).
    if body.anfrage_id:
        vorgang["anfrage_id"] = body.anfrage_id
    if body.idempotenz_schluessel:
        vorgang["idempotenz_schluessel"] = body.idempotenz_schluessel
    if ist_chef:
        vorgang["firmen_abo_ersetzen"] = True
    # Nr. 50: letzte Gegenprobe, bevor Abo und Zahlung entstehen — die
    # Restlaufzeit oben wurde gelesen, als die Sperre noch uns gehoerte.
    if wache is not None:
        wache.pruefen()
    await db.abo_vorgaenge.insert_one(dict(vorgang))
    await _abo_vorgang_ausfuehren(vorgang)
    return _vorgang_antwort(vorgang)


async def _abo_vorgang_ausfuehren(v: dict) -> None:
    """Alle Schritte einer Freischaltung — jeder Schritt idempotent, damit
    ein Wiederholungslauf (nach Absturz/Timeout) nichts doppelt anlegt:
    1) alte Abos als 'ersetzt' markieren (Historie bleibt), 2) neues Abo mit
    id = Vorgangs-ID (Upsert), 3) genau EINE Zahlung je Vorgang (Upsert
    ueber vorgang_id), 4) offene Anfrage schliessen, 5) Audit einmalig,
    6) Vorgang fertig."""
    vid, sid, jetzt = v["id"], v["subject_user_id"], now_iso()
    # Rollenpruefung 22.09.2026 (RP-227/RP-378): ersetzt werden nur Abos, die
    # VOR diesem Vorgang entstanden sind. Holte der Reparaturlauf einen alten,
    # abgebrochenen Vorgang nach, machte Schritt 1 sonst ein inzwischen NEU
    # freigeschaltetes Abo zu 'ersetzt' (zwei Zahlungen, kein aktives Abo).
    vorher = {"$or": [{"created_at": {"$lt": v.get("created_at") or jetzt}},
                      {"created_at": {"$exists": False}}, {"created_at": None}]}
    await db.subscriptions.update_many(
        {"subject_user_id": sid, "id": {"$ne": vid}, "status": {"$ne": "ersetzt"},
         **vorher},
        {"$set": {"status": "ersetzt", "ersetzt_durch": vid, "updated_at": jetzt}})
    if v.get("firmen_abo_ersetzen") and v.get("dealer_id"):
        # Rollenpruefung 22.09.2026 (RP-051/RP-150): beim Hauptchef steckt die
        # Restlaufzeit des alten Firmen-Abos jetzt im neuen persoenlichen Abo
        # — das Firmen-Abo wird Historie, sonst zaehlten seine Tage doppelt
        # und "Aufheben"/"Gueltig bis" trafen das falsche Dokument.
        await db.subscriptions.update_many(
            {**_FIRMEN_ABO(v["dealer_id"]), "status": {"$ne": "ersetzt"}, **vorher},
            {"$set": {"status": "ersetzt", "ersetzt_durch": vid, "updated_at": jetzt}})
    await db.subscriptions.update_one(
        {"id": vid},
        {"$setOnInsert": {
            "id": vid, "dealer_id": v.get("dealer_id"), "subject_user_id": sid,
            "plan": v["plan"], "status": "active", "expires_at": v["expires_at"],
            "price": v["betrag"], "vorgang_id": vid,
            "activated_by": v.get("admin_email", ""), "created_at": jetzt}},
        upsert=True)
    await db.manual_payments.update_one(
        {"vorgang_id": vid},
        {"$setOnInsert": {
            "id": str(uuid.uuid4()), "dealer_id": v.get("dealer_id"),
            "subject_user_id": sid, "plan": v["plan"],
            "amount": float(v["betrag"]), "currency": "EUR",
            "paid_at": v.get("gezahlt_am") or jetzt[:10],
            "period_until": v["expires_at"],       # bezahlt bis = Ablauf bei Freischaltung
            "note": v.get("notiz", ""),
            "zahlungsart": v.get("zahlungsart", "rechnung_bezahlt"),
            "kostenlos": v.get("zahlungsart") in ("kulanz", "probe"),
            "grund": v.get("grund", ""),
            "quelle": "manuell", "vorgang_id": vid,
            "recorded_by": v.get("admin_email", ""), "created_at": jetzt}},
        upsert=True)
    await db.plan_requests.update_many(
        {"type": "sucher_abo", "subject_user_id": sid, "status": "offen"},
        {"$set": {"status": "erledigt", "updated_at": jetzt,
                  "erledigt_durch": "freischaltung", "vorgang_id": vid}})
    if not (v.get("schritte") or {}).get("audit"):
        await log_activity_sicher(v.get("dealer_id", "") or "", v.get("admin_id", ""),
                           "admin.sucher.abo.freigeschaltet", ref=sid,
                           meta={"plan": v["plan"], "betrag": v["betrag"],
                                 "zahlungsart": v.get("zahlungsart"),
                                 "vorgang_id": vid})
        await db.abo_vorgaenge.update_one({"id": vid}, {"$set": {"schritte.audit": True}})
    await db.abo_vorgaenge.update_one(
        {"id": vid}, {"$set": {"status": "fertig", "updated_at": now_iso()}})


async def abo_vorgaenge_nachholen(db_=None) -> int:
    """Reparaturlauf: Vorgaenge, die vor >2 Minuten begonnen und nie
    'fertig' wurden (Absturz mitten im Freischalten), werden komplett
    wiederholt — alle Schritte sind idempotent."""
    frist = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    n = 0
    async for v in db.abo_vorgaenge.find({"status": "laeuft", "updated_at": {"$lt": frist}},
                                         {"_id": 0}).limit(100):
        try:
            # Rollenpruefung 22.09.2026 (RP-227/RP-378): unter DERSELBEN Sperre
            # wie die Freischaltung (vorher ohne) — und nur, wenn es fuer das
            # Konto keinen NEUEREN Vorgang gibt. Hat der Betreiber nach dem
            # Absturz neu freigeschaltet, gilt der neue; der alte wird nicht
            # mehr ausgefuehrt (sonst: zwei Zahlungen, kein aktives Abo).
            async with _sperre(f"abo:{v['subject_user_id']}",
                               f"nachholen-{uuid.uuid4()}") as wache:
                wache.pruefen()
                frisch = await db.abo_vorgaenge.find_one(
                    {"id": v["id"], "status": "laeuft"}, {"_id": 0})
                if not frisch:
                    continue
                neuer = await db.abo_vorgaenge.find_one(
                    {"subject_user_id": frisch["subject_user_id"],
                     "id": {"$ne": frisch["id"]},
                     "created_at": {"$gt": frisch.get("created_at") or ""},
                     "status": {"$in": ["laeuft", "fertig"]}},
                    {"_id": 0, "id": 1}, sort=[("created_at", -1)])
                if neuer:
                    await _abo_vorgang_ueberholt(frisch, neuer["id"])
                else:
                    await _abo_vorgang_ausfuehren(frisch)
                n += 1
        except HTTPException:
            # Sperre belegt (Freischaltung laeuft gerade) — naechster Lauf.
            continue
        except Exception:
            log.exception("Abo-Vorgang %s konnte nicht nachgeholt werden", v.get("id"))
    return n


async def _abo_vorgang_ueberholt(v: dict, neuer_id: str) -> None:
    """Rollenpruefung 22.09.2026 (RP-227/RP-378): einen abgebrochenen Vorgang
    abschliessen, den ein neuerer Vorgang desselben Kontos ueberholt hat —
    ohne neues Abo und ohne neue Zahlung. Ein schon angelegtes Abo dieses
    Vorgangs wird Historie ('ersetzt' durch den neueren). Stand bereits eine
    Zahlung (Absturz nach Schritt 3), bleibt sie stehen: ein Betriebsalarm
    bittet um Pruefung (evtl. doppelt gebucht)."""
    vid, jetzt = v["id"], now_iso()
    await db.subscriptions.update_one(
        {"id": vid, "status": {"$ne": "ersetzt"}},
        {"$set": {"status": "ersetzt", "ersetzt_durch": neuer_id, "updated_at": jetzt}})
    zahlung = await db.manual_payments.find_one({"vorgang_id": vid}, {"_id": 0, "id": 1})
    await db.abo_vorgaenge.update_one(
        {"id": vid, "status": "laeuft"},
        {"$set": {"status": "ueberholt", "ueberholt_durch": neuer_id,
                  "zahlung_vorhanden": bool(zahlung), "updated_at": jetzt}})
    try:
        await betrieb.alarm(db, "abo_vorgang_ueberholt", ref=vid,
                            konto=v.get("subject_user_id") or "", neuer=neuer_id,
                            zahlung_vorhanden=bool(zahlung))
    except Exception:  # noqa: BLE001 — der Abschluss steht, der Alarm ist Zugabe
        log.exception("Alarm abo_vorgang_ueberholt fuer %s nicht gesetzt", vid)


def _gueltig_bis_parsen(wert) -> str:
    """'JJJJ-MM-TT' -> Ablauf am ENDE dieses Tages (UTC); None/leer -> ''.
    Vergangene Daten sind erlaubt (bewusstes Sofort-Sperren)."""
    if not wert:
        return ""
    import re as _re
    w = str(wert).strip()[:10]
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", w):
        raise HTTPException(400, "gueltig_bis muss ein Datum JJJJ-MM-TT sein")
    try:
        tag = datetime.fromisoformat(w)
    except ValueError:
        raise HTTPException(400, "gueltig_bis ist kein gueltiges Datum")
    # Tagesende in deutscher Zeit — sonst zeigt die Oberflaeche den Folgetag
    # (UTC 23:59 = 00:59 Berlin). Ohne tzdata (Windows-Dev) Fallback +01:00.
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Berlin")
    except Exception:
        tz = timezone(timedelta(hours=1))
    return tag.replace(hour=23, minute=59, second=59, tzinfo=tz).isoformat()


@router.patch("/admin/sucher/{sucher_id}/abo-gueltig-bis")
async def admin_set_abo_gueltig_bis(sucher_id: str, body: dict = Body(...),
                                    admin=Depends(current_super_admin)):
    """NUR das Ablaufdatum des massgeblichen Abos aendern (Wunsch 09/2026) —
    ohne Zahlung; auch eines, das nur per Datum abgelaufen ist (nicht aber
    eines, das 'Abo aufheben' beendet hat). Audit 09/2026: die Zahlungshistorie wird NICHT mehr
    umgeschrieben; die Aenderung landet als eigener Datensatz in
    zugangs_aenderungen (alt/neu/Grund/Admin)."""
    gueltig_bis = _gueltig_bis_parsen(body.get("gueltig_bis"))
    if not gueltig_bis:
        raise HTTPException(400, "gueltig_bis (JJJJ-MM-TT) fehlt")
    # Audit 09/2026: eine Laufzeitaenderung ohne Zahlung ist immer eine
    # Ausnahme — sie braucht eine Begruendung im unveraenderlichen Verlauf.
    grund = str(body.get("grund", ""))[:300].strip()
    if not grund:
        raise HTTPException(400, "Bitte einen Grund fuer die Laufzeitaenderung angeben")
    # Nachpruefung 20.09.2026, Nr. 73: hier wurde EIN aktives Abo gelesen,
    # danach aber JEDES dann aktive Abo geaendert — ohne Id, ohne Abgleich
    # und ohne dieselbe Sperre wie die Freischaltung. Eine parallel laufende
    # Freischaltung konnte so ihr frisch bezahltes Abo ueberschrieben
    # bekommen, waehrend der Verlaufseintrag auf das alte Abo zeigte.
    # Jetzt: dieselbe Sperre, Aenderung NUR am gelesenen Abo, und der alte
    # Ablauf muss beim Schreiben noch derselbe sein.
    # Rollenpruefung 22.09.2026 (RP-050/RP-149, RP-051/RP-150): gemeint ist das
    # Abo, das GERADE Zugang gibt — auch ein gekuendigtes, noch laufendes
    # ('cancelled') und beim Hauptchef das Firmen-Abo, das die Oberflaeche
    # anzeigt. Vorher: nur status 'active' und nur persoenlich -> 404.
    # Rollenpruefung 22.09.2026 (Welle 3, test_betreiber::test_14): ein nur per
    # Datum abgelaufenes Abo (status weiter 'active') bleibt ueber ein neues
    # Datum verlaengerbar — auch_abgelaufen=True; aufgehobene nicht.
    konto = await db.users.find_one({"id": sucher_id},
                                    {"_id": 0, "id": 1, "role": 1, "dealer_id": 1})
    ist_chef = await _ist_hauptchef_konto(konto)
    async with _sperre(f"abo:{sucher_id}", _handelnder(admin)) as wache:
        aktiv = await _massgebliches_abo(sucher_id, (konto or {}).get("dealer_id"), ist_chef,
                                         auch_abgelaufen=True)
        if not aktiv:
            raise HTTPException(404, "Kein aktives Abo fuer dieses Konto")
        if not aktiv.get("id"):
            raise HTTPException(409, "Dieses Abo hat keine Kennung (Altbestand) — bitte neu "
                                     "freischalten statt das Datum zu ändern.")
        wache.pruefen()
        r = await db.subscriptions.update_one(
            {"id": aktiv["id"], "status": aktiv.get("status"),
             "expires_at": aktiv.get("expires_at")},
            {"$set": {"expires_at": gueltig_bis, "updated_at": now_iso()}})
        if r.matched_count == 0:
            raise HTTPException(
                409, "Das Abo hat sich zwischenzeitlich geaendert (vermutlich "
                     "eine parallele Freischaltung). Bitte die Seite neu laden "
                     "und noch einmal ansehen — es wurde nichts geaendert.")
    await db.zugangs_aenderungen.insert_one({
        "id": str(uuid.uuid4()), "subject_user_id": sucher_id,
        "dealer_id": aktiv.get("dealer_id"), "abo_id": aktiv.get("id"),
        "plan": aktiv.get("plan"),
        "alt": aktiv.get("expires_at"), "neu": gueltig_bis,
        "art": "laufzeit_geaendert", "grund": grund,
        "admin_id": admin["id"], "admin_email": _handelnder(admin),
        "created_at": now_iso(),
    })
    await log_activity_sicher(admin.get("dealer_id", ""), admin["id"],
                       "admin.sucher.abo.gueltig_bis", ref=sucher_id,
                       meta={"alt": aktiv.get("expires_at"), "gueltig_bis": gueltig_bis[:10]})
    return {"ok": True, "expires_at": gueltig_bis}


def _restlaufzeit_basis(expires_at) -> datetime:
    """Startpunkt einer Verlaengerung: bisheriger Ablauf, falls der noch in
    der Zukunft liegt — sonst jetzt."""
    jetzt = datetime.now(timezone.utc)
    # Rollenpruefung 22.09.2026 (RP-230): derselbe Parser wie die
    # Zugriffspruefung (ISO-String mit Z/Offset oder datetime-Altwert).
    alt = _ablauf_parsen(expires_at)
    return alt if alt is not None and alt > jetzt else jetzt


# ---------- Sucher-Konten anlegen/verwalten (Betreiber, 09/2026) ----------
# Der Betreiber legt Sucher-Konten für eine Firma an (Anmeldung mit
# Kontonummer '<kunden_nr>-<zusatz>' + Passwort, Kontonummer 13.09.2026),
# auch nachträglich, und kann sie sperren/löschen
# (Sperren/Löschen laufen über die bestehenden /admin/users-Routen).
class AdminSucherIn(BaseModel):
    # Kontonummer (13.09.2026): Anmeldung per '<kunden_nr>-<zusatz>', die
    # E-Mail ist optionale Kontaktadresse des Suchers.
    email: Optional[EmailStr] = None
    password: str = Field(min_length=8, max_length=200)
    first_name: str = Field(default="", max_length=80)
    last_name: str = Field(default="", max_length=80)
    phone: str = Field(default="", max_length=50)

    @field_validator("email", mode="before")
    @classmethod
    def _email_leer(cls, v):
        return _leer_zu_none(v)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return pruefe_passwort(v)


@router.post("/admin/dealers/{dealer_id}/sucher")
async def admin_create_sucher(dealer_id: str, body: AdminSucherIn,
                              admin=Depends(current_super_admin)):
    # Kontonummer (13.09.2026): 404 ohne Firma, 409 waehrend ihrer Loeschung —
    # beides VOR jeder Pruefung und vor dem Ziehen einer Nummer.
    dealer = await db.dealers.find_one({"id": dealer_id},
                                       {"_id": 0, "id": 1, "company_name": 1,
                                        "kunden_nr": 1, "loeschung": 1})
    if not dealer:
        raise HTTPException(404, "Firma nicht gefunden")
    if (dealer.get("loeschung") or {}).get("status") == "laeuft":
        raise HTTPException(409, "Die Firma wird gerade gelöscht")
    # Kontonummer (13.09.2026), Schritt 5: E-Mail nur Kontaktadresse — die
    # Plattformregel B5 (Audit #11) entfaellt, gleiche Adresse ist erlaubt.
    email = (body.email or "").strip().lower() or None
    _pw_persoenlich_400(body.password, [body.first_name, body.last_name, email])
    sucher_id = str(uuid.uuid4())
    konto = {
        "id": sucher_id,
        "password_hash": await hash_password_async(body.password),
        "role": "sucher", "active": True,
        "dealer_id": dealer_id,
        "first_name": body.first_name.strip(),
        "last_name": body.last_name.strip(),
        "phone": body.phone.strip(),
        "created_by": admin["id"],
        "current_session_id": None,
        "created_at": now_iso(),
    }
    if email:                       # ohne Angabe fehlt das Feld (nie "")
        konto["email"] = email
    # Kontonummer (13.09.2026): Nummer '<kunden_nr>-<zusatz>'; eine Firma ohne
    # kunden_nr bekommt eine. Neuer Zusatz nur bei einer Kontonummer-Dublette
    # (kontenanlage), jede andere Dublette bleibt 409 (statt 500).
    from kontenanlage import sucher_anlegen
    try:
        erg = await sucher_anlegen(db, dealer_id, konto)
    except DuplicateKeyError:
        raise HTTPException(409, _KONTO_DUBLETTE)
    # Konto ist angelegt — ein Audit-Fehler darf keinen 500 mit Retry ausloesen.
    await log_activity_sicher(dealer_id, admin["id"], "admin.sucher.angelegt",
                              ref=sucher_id, meta={"kontonummer": erg["kontonummer"],
                                                   "firma": dealer.get("company_name", "")})
    return {"ok": True, "sucher_id": sucher_id, "email": email or "",
            "kontonummer": erg["kontonummer"],
            "hinweis": "Konto angelegt — zum Suchen/Vergleichen noch das "
                       "Sucher-Abo freischalten (150 €/Monat bzw. "
                       "1.500 €/Jahr)."}


# ---------- Kaeufer- und Fahrer-Anlage durch den Betreiber (Kontonummer, 13.09.2026) ----------
class AdminKaeuferIn(BaseModel):
    """Zwischenhaendler (b2b_buyer) anlegen — Konten legt nur der Betreiber an."""
    company_name: str = Field(min_length=2, max_length=200)
    contact_name: str = Field(min_length=2, max_length=120)
    email: Optional[EmailStr] = None
    phone: str = Field(default="", max_length=50)
    # USt-IdNr. oder Handelsregister-Nr. (freiwillig, Formatpruefung wie im
    # Anfrage-Formular — gemeinsamer Validator in ustid.py)
    ust_id: str = Field(default="", max_length=40)
    password: str = Field(min_length=8, max_length=200)
    # AGB §1: der Betreiber bestaetigt, dass der B2B-Nachweis vorliegt
    b2b_nachweis: bool = False
    anfrage_id: Optional[str] = Field(default=None, max_length=100)
    # Nachpruefung 15.09.2026: Eingaben weichen bewusst von der Anfrage ab.
    daten_geaendert: bool = False

    @field_validator("email", mode="before")
    @classmethod
    def _email_leer(cls, v):
        return _leer_zu_none(v)

    @field_validator("ust_id")
    @classmethod
    def _ustid(cls, v):
        from ustid import feld_pruefen
        return feld_pruefen(v)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return pruefe_passwort(v)


@router.post("/admin/buyers")
async def admin_create_buyer(body: AdminKaeuferIn, admin=Depends(current_super_admin)):
    """Zwischenhaendler mit eigener Kontonummer aus der gemeinsamen Reihe.
    Kein Token (Single-Session: die erste Anmeldung macht der Kaeufer selbst);
    der Marktplatz-Zugang laeuft weiter ueber POST /admin/buyers/{id}/access."""
    if not body.b2b_nachweis:
        raise HTTPException(400, "Bitte bestätigen, dass der Nachweis der "
                                 "gewerblichen Tätigkeit (B2B) vorliegt")
    email = (body.email or "").strip().lower() or None
    _pw_persoenlich_400(body.password, [body.company_name, body.contact_name, email])
    anfrage = await _anfrage_reservieren(
        body.anfrage_id, "kaeufer",
        eingaben={"email": email, "company_name": body.company_name,
                  "daten_geaendert": body.daten_geaendert})
    try:
        bestaetigt_am = (anfrage or {}).get("gewerblich_bestaetigt_am")
        konto = {
            "id": str(uuid.uuid4()),
            "password_hash": await hash_password_async(body.password),
            "role": "b2b_buyer", "active": True, "dealer_id": None,
            "company_name": body.company_name.strip(),
            "contact_name": body.contact_name.strip(),
            "phone": body.phone.strip(),
            "ust_id": body.ust_id or (anfrage or {}).get("ust_id", "") or "",
            "gewerblich_bestaetigt_am": bestaetigt_am or now_iso(),
            "gewerblich_bestaetigt_durch": "anfrage" if bestaetigt_am else "betreiber",
            "created_by": admin["id"],
            "current_session_id": None,
            "created_at": now_iso(),
        }
        if email:
            konto["email"] = email
        if anfrage:
            konto["zugangsanfrage_id"] = anfrage["id"]     # Pruefung 14.09.2026 (M7)
        from kontenanlage import kaeufer_anlegen
        try:
            erg = await kaeufer_anlegen(db, konto)
        except DuplicateKeyError:
            raise HTTPException(409, _KONTO_DUBLETTE)
    except BaseException:
        await _anfrage_freigeben(anfrage)
        raise
    await _anfrage_abschliessen(anfrage, erg["user_id"], erg["kontonummer"], admin)
    einladung = await _einladung_einloesen(anfrage, erg["user_id"], admin)
    await log_activity_sicher("", admin["id"], "admin.kaeufer.angelegt", ref=erg["user_id"],
                              meta={"kontonummer": erg["kontonummer"],
                                    "anfrage": bool(anfrage),
                                    "einladung": einladung})
    return {"ok": True, "user_id": erg["user_id"], "kontonummer": erg["kontonummer"],
            "einladung": einladung}


async def _einladung_einloesen(anfrage: Optional[dict], kaeufer_id: str,
                               admin: dict) -> Optional[dict]:
    """Rollenprüfung 22.09.2026 (RP-511, Welle 2): Die Zugangsanfrage eines
    eingeladenen Partners traegt das Einladungs-Token (routes.auth). Beim
    Anlegen des Kontos wird es hier eingeloest — vorher ging die Einladung
    still verloren. Best effort: das Konto steht schon; scheitert es, sieht
    der Betreiber das in der Antwort, und der Partner kann den Link nach der
    ersten Anmeldung selbst einloesen (BuyerLogin merkt ihn sich).
    None = die Anfrage hatte keine Einladung."""
    token = ((anfrage or {}).get("invite_token") or "").strip()
    if not token:
        return None
    firma = ""
    dealer_id = None
    try:
        from routes.marketplace import _redeem_invite
        dealer_id = await _redeem_invite(token, kaeufer_id)
        if dealer_id:
            d = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "company_name": 1})
            firma = (d or {}).get("company_name", "")
        else:
            firma = (await _einladung_info(token)).get("firma", "")
    except Exception:  # noqa: BLE001 — das Konto ist angelegt
        log.exception("Einladung der Anfrage %s nicht eingeloest", (anfrage or {}).get("id"))
        dealer_id = None
    await log_activity_sicher(dealer_id or "", admin["id"], "admin.kaeufer.einladung",
                              ref=kaeufer_id,
                              meta={"eingeloest": bool(dealer_id),
                                    "anfrage": (anfrage or {}).get("id")})
    return {"eingeloest": bool(dealer_id), "firma": firma}


class AdminFahrerIn(BaseModel):
    """Fahrer-Konto anlegen. Die Zuordnung zur Firma macht weiter der Chef per
    Fahrer-Code (POST /drivers/add)."""
    display_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=200)
    email: Optional[EmailStr] = None
    phone: str = Field(default="", max_length=50)
    anfrage_id: Optional[str] = Field(default=None, max_length=100)
    # Nachpruefung 15.09.2026: Eingaben weichen bewusst von der Anfrage ab.
    daten_geaendert: bool = False

    @field_validator("email", mode="before")
    @classmethod
    def _email_leer(cls, v):
        return _leer_zu_none(v)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return pruefe_passwort(v)


@router.post("/admin/drivers")
async def admin_create_driver(body: AdminFahrerIn, admin=Depends(current_super_admin)):
    """Fahrer mit Kontonummer (gemeinsame Reihe) und FD-Code, ohne Token."""
    email = (body.email or "").strip().lower() or None
    _pw_persoenlich_400(body.password, [body.display_name, email])
    anfrage = await _anfrage_reservieren(
        body.anfrage_id, "fahrer",
        eingaben={"email": email, "daten_geaendert": body.daten_geaendert})
    try:
        konto = {
            "id": str(uuid.uuid4()),
            "password_hash": await hash_password_async(body.password),
            "display_name": body.display_name.strip(),
            "active": True,
            "created_by": admin["id"],
            "current_session_id": None,
            "created_at": now_iso(),
        }
        if email:
            konto["email"] = email
        if body.phone.strip():
            konto["phone"] = body.phone.strip()
        if anfrage:
            konto["zugangsanfrage_id"] = anfrage["id"]     # Pruefung 14.09.2026 (M7)
        from kontenanlage import fahrer_anlegen
        try:
            erg = await fahrer_anlegen(db, konto)
        except DuplicateKeyError:
            raise HTTPException(409, _KONTO_DUBLETTE)
    except BaseException:
        await _anfrage_freigeben(anfrage)
        raise
    await _anfrage_abschliessen(anfrage, erg["driver_id"], erg["kontonummer"], admin)
    # Ohne Klartext (weder E-Mail noch Name) im Audit
    await log_activity_sicher("", admin["id"], "admin.fahrer.angelegt", ref=erg["driver_id"],
                              meta={"kontonummer": erg["kontonummer"],
                                    "anfrage": bool(anfrage)})
    return {"ok": True, "driver_id": erg["driver_id"], "kontonummer": erg["kontonummer"],
            "driver_code": erg["driver_code"]}


@router.get("/admin/dealers/{dealer_id}/sucher")
async def admin_list_dealer_sucher(dealer_id: str, response: Response, limit: int = 200,
                                   seite: int = 1, _=Depends(current_admin)):
    """Alle Sucher einer Firma inkl. Abo-Status, letzter Zahlung und
    nächster Fälligkeit (= Abo-Ablauf) — für die Freischaltungs-Ansicht."""
    # Chef ZUERST (Wunsch 09/2026: "Firmen-Chef Freischaltung Sucher-
    # Funktion ja/nein" auf derselben Karte), danach die Sucher.
    limit = max(1, min(int(limit), 2000))
    seite = max(1, int(seite))
    items = await db.users.find(
        {"dealer_id": dealer_id, "role": {"$in": ["dealer", "sucher"]}},
        {"_id": 0, "password_hash": 0},
    ).sort("created_at", 1).skip((seite - 1) * limit).to_list(limit + 1)
    items = _seite_kopf(response, items, limit)
    # Rollenpruefung 22.09.2026 (RP-033/RP-132/RP-151/RP-283): Chef ist der
    # ZEIGER dealers.user_id (sonst das aelteste dealer-Konto) — vorher galt
    # jedes Konto mit role 'dealer' als Chef und bekam den Firmen-Abo-Rueckfall.
    haupt = await haupt_chef_id(dealer_id)
    items.sort(key=lambda x: 0 if x.get("id") == haupt else 1)
    # Wann bekam ein Konto zuletzt ein Probe-Abo? (eine Abfrage fuer die Seite)
    # Die Oberflaeche fragt dann nach, bevor sie eine weitere Probe vergibt.
    probe_am = {}
    ids = [s["id"] for s in items]
    if ids:
        async for row in db.manual_payments.aggregate([
                {"$match": {"subject_user_id": {"$in": ids}, "zahlungsart": "probe"}},
                {"$group": {"_id": "$subject_user_id", "am": {"$max": "$created_at"}}}]):
            probe_am[row["_id"]] = row["am"]
    out = []
    for s in items:
        ist_chef = s.get("id") == haupt
        # Chef: zentrale Aufloesung (persoenlich, sonst altes Firmen-Abo)
        sub = (await subscription_for(s) if ist_chef
               else await get_subscription_status(dealer_id, subject_user_id=s["id"]))
        letzte = await db.manual_payments.find_one(
            {"subject_user_id": s["id"]}, {"_id": 0},
            sort=[("created_at", -1)])
        out.append({**s, "ist_chef": ist_chef,
                    # KI-Bewertung je Konto freigeschaltet? (25.09.2026 abends, wie Abo)
                    "ki_aktiv": s.get("ki_aktiv") is True,
                    # liegengebliebenes zweites dealer-Konto: arbeitet als Sucher
                    "weiteres_dealer_konto": s.get("role") == "dealer" and not ist_chef,
                    "subscription": sub,
                    "letzte_zahlung": letzte,
                    "probe_vergeben_am": probe_am.get(s["id"]),
                    "naechste_zahlung_am": sub.get("expires_at")})
    return out


class AdminZahlungIn(BaseModel):
    subject_user_id: Optional[str] = None   # Sucher; leer = Firmen-Ebene
    amount: float = Field(ge=0, le=100000)
    paid_at: Optional[str] = Field(default=None, max_length=10)  # JJJJ-MM-TT
    note: str = Field(default="", max_length=500)


@router.get("/admin/dealers/{dealer_id}/zahlungen")
async def admin_list_zahlungen(dealer_id: str, _=Depends(current_super_admin)):
    """Zahlungshistorie einer Firma (alle manuell erfassten Zahlungen,
    neueste zuerst) — Grundlage für 'was wurde wann gezahlt'."""
    return await db.manual_payments.find(
        {"dealer_id": dealer_id}, {"_id": 0},
    ).sort("created_at", -1).to_list(500)


@router.post("/admin/dealers/{dealer_id}/zahlungen")
async def admin_add_zahlung(dealer_id: str, body: AdminZahlungIn,
                            admin=Depends(current_super_admin)):
    """Zahlung nachtragen/korrigieren, OHNE am Abo etwas zu ändern
    (Freischalten + Verlängern erfasst die Zahlung bereits automatisch)."""
    dealer = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "id": 1})
    if not dealer:
        raise HTTPException(404, "Firma nicht gefunden")
    # Nachpruefung 20.09.2026, Nr. 61: geprueft wurde nur, ob die FIRMA
    # existiert. Der mitgegebene Sucher wurde ungeprueft uebernommen — durch
    # einen Auswahl- oder Anzeigefehler konnte eine Zahlung unter Firma X
    # mit einem Sucher aus Firma Y landen. In der Firmenabrechnung taucht
    # sie dann bei der falschen Firma auf.
    if body.subject_user_id:
        person = await db.users.find_one(
            {"id": body.subject_user_id},
            {"_id": 0, "id": 1, "dealer_id": 1, "email": 1})
        if not person:
            raise HTTPException(404, "Der angegebene Sucher wurde nicht gefunden")
        if (person.get("dealer_id") or "") != dealer_id:
            raise HTTPException(
                400, "Der angegebene Sucher gehoert nicht zu dieser Firma — "
                     "die Zahlung wuerde in der falschen Firmenabrechnung "
                     "landen.")
    doc = {
        "id": str(uuid.uuid4()), "dealer_id": dealer_id,
        "subject_user_id": body.subject_user_id or None,
        "plan": None,
        "amount": round(float(body.amount), 2),
        "paid_at": (body.paid_at or now_iso()[:10]),
        "period_until": None,
        "note": body.note.strip(),
        "recorded_by": _handelnder(admin),
        "created_at": now_iso(),
    }
    # KOPIE einfuegen: insert_one haengt dem uebergebenen dict die Mongo-_id
    # (ObjectId) an — die Antwort waere sonst nicht JSON-serialisierbar (500).
    await db.manual_payments.insert_one(dict(doc))
    await log_activity_sicher(dealer_id, admin["id"], "admin.zahlung.erfasst",
                       meta={"betrag": doc["amount"], "sucher":
                             body.subject_user_id or ""})
    return {"ok": True, "zahlung": doc}


# ---------- Zwischenhändler-Zugang freischalten (manuell) ----------
@router.get("/admin/buyers")
async def admin_list_buyers(response: Response, limit: int = 1000, seite: int = 1,
                            _=Depends(current_admin)):
    """Alle Zwischenhändler mit Zugangsstatus (für die Freischaltung)."""
    from routes.marketplace import _access_status
    limit = max(1, min(int(limit), 5000))
    seite = max(1, int(seite))
    users = await db.users.find(
        {"role": "b2b_buyer"}, {"_id": 0, "password_hash": 0},
    ).sort("created_at", -1).skip((seite - 1) * limit).to_list(limit + 1)
    users = _seite_kopf(response, users, limit)
    return [{**u, "access": _access_status(u)} for u in users]


@router.post("/admin/buyers/{buyer_id}/access")
async def admin_set_buyer_access(buyer_id: str, body: dict = Body(...),
                                 admin=Depends(current_super_admin)):
    """Marktplatz-Zugang eines Zwischenhändlers aktivieren/verlängern
    (plan='monthly') oder mit plan=null sperren."""
    from routes.marketplace import BUYER_ACCESS_DAYS, BUYER_ACCESS_PRICE
    buyer = await db.users.find_one(
        {"id": buyer_id, "role": "b2b_buyer"}, {"_id": 0, "id": 1, "active": 1})
    if not buyer:
        raise HTTPException(404, "Zwischenhändler nicht gefunden")
    plan = body.get("plan")
    # Nachpruefung 20.09.2026, Nr. 62: geladen wurde nur Id und Rolle. Ein
    # deaktiviertes Konto bekam Zahlung und marketplace_access.active=True,
    # konnte sich aber weiterhin nicht anmelden — Zahlung und nutzbarer
    # Zugang liefen auseinander. Sperren (plan=None) bleibt erlaubt.
    if plan is not None and buyer.get("active") is False:
        raise HTTPException(
            400, "Dieses Konto ist deaktiviert — eine Freischaltung wuerde "
                 "bezahlt, der Zwischenhaendler kaeme aber trotzdem nicht "
                 "hinein. Erst das Konto wieder aktivieren.")
    if plan is None:
        # Runde 13: C6 — die Sperre ist ein EIGENER Zustand (gesperrt), der
        # auch im Kostenlos-Modus gilt und den eine Stripe-Zahlung nicht
        # loescht (marketplace._access_status, payments._zugang_freischalten).
        # active=False bleibt fuer den Altbestand als Sperr-Signal erhalten.
        # Nachpruefung 20.09.2026, Nr. 49: Freischalten und Sperren liefen
        # ohne gemeinsame Sperre — der zuletzt eintreffende Write gewann,
        # und es konnte eine Zahlung gespeichert sein, obwohl das Konto
        # danach gesperrt war. Beide Wege nehmen jetzt DIESELBE Sperre.
        async with _sperre(f"buyer-zugang:{buyer_id}", _handelnder(admin)) as wache:
            wache.pruefen()
            await db.users.update_one(
                {"id": buyer_id},
                {"$set": {"marketplace_access.active": False,
                          "marketplace_access.gesperrt": True,
                          "marketplace_access.gesperrt_am": now_iso(),
                          "marketplace_access.gesperrt_von": _handelnder(admin),
                          "marketplace_access.updated_at": now_iso()}})
        # Rollenpruefung 22.09.2026 (RP-098 Nr. 2, RP-348 Nr. 2): wie bei der
        # Kontosperre enden laufende Verhandlungen und Reservierungen — sonst
        # konnte der Haendler eine alte Anfrage des gesperrten Kaeufers noch
        # annehmen und fuer ihn reservieren.
        # Grund wie bei der Kontosperre — der Marktplatz uebersetzt ihn
        # ("Beendet — dein Zugang wurde gesperrt").
        freigegeben = await kaeufer_verhandlungen_beenden(buyer_id, "kaeufer_gesperrt")
        await log_activity_sicher("", admin["id"], "admin.buyer.zugang.gesperrt",
                           ref=buyer_id, meta={"reservierungen_freigegeben": freigegeben})
        return {"ok": True, "active": False, "gesperrt": True,
                "reservierungen_freigegeben": freigegeben}
    if plan != "monthly":
        raise HTTPException(400, "Nur 'monthly' unterstützt")
    # Rollenprüfung 22.09.2026 (RP-507 (3), Welle 2): Im Kostenlos-Modus
    # (MARKTPLATZ_KOSTENLOS) buchte "Freischalten" standardmaessig 20 EUR
    # "rechnung_bezahlt" — obwohl niemand etwas zahlt. Jetzt ist dort
    # "kostenlos" (0 EUR, ohne Pflicht-Grund) der Standard; eine ausdrueckliche
    # Zahlungsart gilt weiter. Im Bezahlmodus gibt es "kostenlos" nicht (dafuer
    # ist "kulanz" mit Begruendung da).
    kostenlos_modus = _marktplatz_kostenlos()
    zahlungsart = body.get("zahlungsart") or ("kostenlos" if kostenlos_modus
                                              else "rechnung_bezahlt")
    erlaubt = ("rechnung_bezahlt", "kulanz") + (("kostenlos",) if kostenlos_modus else ())
    if zahlungsart not in erlaubt:
        raise HTTPException(400, "zahlungsart: " + " oder ".join(erlaubt))
    grund = str(body.get("grund", ""))[:300].strip()
    if zahlungsart == "kulanz" and not grund:
        raise HTTPException(400, "Kulanz-Freischaltung braucht eine Begruendung")
    if zahlungsart == "kostenlos" and not grund:
        grund = "Marktplatz kostenlos (MARKTPLATZ_KOSTENLOS)"
    ohne_geld = zahlungsart in ("kulanz", "kostenlos")
    # Nachpruefung 20.09.2026, Nr. 47/48: Diese Route hatte — anders als die
    # Sucher-Freischaltung — WEDER Sperre NOCH Abgleich. Zwei schnell
    # hintereinander abgeschickte Klicks lasen denselben Ablaufstand,
    # legten ZWEI Zahlungen an und verlaengerten trotzdem nur EINMAL um 30
    # Tage. Und Zahlung und Zugang wurden nacheinander geschrieben: starb
    # der Prozess dazwischen, stand die Zahlung in der Historie, der
    # Zwischenhaendler hatte aber keinen Zugang.
    # Jetzt derselbe Weg wie beim Sucher: eine Sperre je Konto und EIN
    # Vorgang, dessen Schritte alle idempotent sind (Upsert ueber
    # vorgang_id) — eine Wiederholung legt nichts doppelt an.
    vid = str(uuid.uuid4())
    async with _sperre(f"buyer-zugang:{buyer_id}", _handelnder(admin)) as wache:
        voll = await db.users.find_one({"id": buyer_id},
                                       {"_id": 0, "marketplace_access": 1})
        acc = (voll or {}).get("marketplace_access") or {}
        # Rollenpruefung 22.09.2026 (RP-509): auch nach "Sperren" zaehlt die
        # schon bezahlte Restlaufzeit (gespeichertes expires_at) — vorher
        # rechnete das Entsperren ab jetzt, die Resttage waren verloren. Bei
        # aktivem Zugang ist derselbe Aufruf die Verlaengerung (+30 Tage ab
        # bisherigem Ablauf, Knopf "Verlängern" in den Freischaltungen).
        basis = _restlaufzeit_basis(acc.get("expires_at"))
        expires_at = (basis + timedelta(days=BUYER_ACCESS_DAYS)).isoformat()
        wache.pruefen()
        # Zuerst der Zugang (das braucht der Kunde), mit Abgleich auf den
        # gelesenen Stand: hat inzwischen jemand anderes etwas geaendert,
        # wird hier abgebrochen statt blind ueberschrieben.
        r = await db.users.update_one(
            {"id": buyer_id,
             "marketplace_access.expires_at": acc.get("expires_at"),
             "marketplace_access.active": acc.get("active")},
            {"$set": {"marketplace_access": {
                "active": True, "plan": "monthly",
                "price": BUYER_ACCESS_PRICE, "expires_at": expires_at,
                "vorgang_id": vid,
                "activated_by": _handelnder(admin), "updated_at": now_iso()}}})
        if r.matched_count == 0:
            raise HTTPException(
                409, "Der Zugang hat sich zwischenzeitlich geaendert "
                     "(Doppelklick oder zweiter Admin). Bitte die Seite neu "
                     "laden — es wurde nichts doppelt gebucht.")
        # Audit 09/2026: manuelle Marktplatz-Freischaltung erzeugt einen
        # Zahlungsdatensatz (Zugang und Finanzhistorie laufen nicht
        # auseinander). Upsert ueber vorgang_id: genau EINE Zahlung je
        # Vorgang, auch wenn dieser Schritt wiederholt wird.
        await db.manual_payments.update_one(
            {"vorgang_id": vid},
            {"$setOnInsert": {
                "id": str(uuid.uuid4()), "dealer_id": None,
                "subject_user_id": buyer_id, "plan": "marktplatz",
                "amount": 0.0 if ohne_geld else float(BUYER_ACCESS_PRICE),
                "currency": "EUR", "paid_at": now_iso()[:10],
                "period_until": expires_at,
                "zahlungsart": zahlungsart, "kostenlos": ohne_geld,
                "grund": grund, "note": str(body.get("notiz", ""))[:500],
                "quelle": "manuell", "vorgang_id": vid,
                "recorded_by": _handelnder(admin), "created_at": now_iso()}},
            upsert=True)
    await log_activity_sicher("", admin["id"], "admin.buyer.zugang.freigeschaltet",
                       ref=buyer_id, meta={"expires_at": expires_at,
                                           "vorgang_id": vid})
    await db.plan_requests.update_many(
        {"type": "buyer_access", "buyer_user_id": buyer_id, "status": "offen"},
        {"$set": {"status": "erledigt", "updated_at": now_iso(),
                  "erledigt_durch": "freischaltung"}})
    return {"ok": True, "active": True, "expires_at": expires_at,
            "vorgang_id": vid, "zahlungsart": zahlungsart}


def _marktplatz_kostenlos() -> bool:
    """Schalter aus routes.marketplace (Marktplatz kostenlos) — spaet gelesen,
    damit Tests ihn per monkeypatch umstellen koennen."""
    from routes import marketplace as _markt
    return bool(_markt.MARKTPLATZ_KOSTENLOS)


@router.put("/admin/plan-requests/{req_id}")
async def admin_close_plan_request(req_id: str, body: dict = Body(default={}),
                                   _=Depends(current_super_admin)):
    # Runde 11: feste Zustaende. Ein Tippfehler wie "offen " liess eine
    # Anfrage vorher aus der Oberflaeche verschwinden (die laedt status=offen).
    status = body.get("status", "erledigt")
    if status not in PLAN_REQUEST_STATUS:
        raise HTTPException(400, f"status muss einer von {sorted(PLAN_REQUEST_STATUS)} sein")
    r = await db.plan_requests.update_one(
        {"id": req_id},
        {"$set": {"status": status, "updated_at": now_iso()}})
    if not r.matched_count:
        raise HTTPException(404, "Anfrage nicht gefunden")
    return {"ok": True}


PLAN_REQUEST_STATUS = {"offen", "erledigt", "abgelehnt"}


# ---------- Vehicle comparisons ----------
@router.get("/admin/comparisons")
async def admin_comparisons(limit: int = 200, _=Depends(current_admin)):
    """Aggregierte Liste aller verglichenen Fahrzeuge (mobile / kleinanzeigen
    / autoscout). Pro Fahrzeug: Anzahl Vergleiche, Quellen, beteiligte Nutzer
    (mit Firmenname), Zeitraum, sowie Fahrzeugdaten (Marke/Modell/EZ/Preis)
    soweit verfügbar.
    """
    pipeline = [
        {"$group": {
            "_id": "$mobile_ad_id",
            "count": {"$sum": 1},
            "user_ids": {"$addToSet": "$user_id"},
            "dealer_ids": {"$addToSet": "$dealer_id"},
            "sources": {"$addToSet": "$source"},
            "first_at": {"$min": "$created_at"},
            "last_at": {"$max": "$created_at"},
        }},
        {"$sort": {"count": -1, "last_at": -1}},
        {"$limit": max(1, min(limit, 1000))},
    ]
    rows = await db.vehicle_comparisons.aggregate(pipeline).to_list(1000)

    # Bulk-Lookup für Performance
    all_user_ids = {uid for r in rows for uid in (r.get("user_ids") or []) if uid}
    all_dealer_ids = {did for r in rows for did in (r.get("dealer_ids") or []) if did}
    ad_ids = [r["_id"] for r in rows if r.get("_id")]

    users_by_id = {}
    if all_user_ids:
        async for u in db.users.find(
            {"id": {"$in": list(all_user_ids)}},
            {"_id": 0, "id": 1, "email": 1, "username": 1, "company_name": 1, "dealer_id": 1, "active": 1},
        ):
            users_by_id[u["id"]] = u
    dealers_by_id = {}
    if all_dealer_ids:
        async for d in db.dealers.find(
            {"id": {"$in": list(all_dealer_ids)}},
            {"_id": 0, "id": 1, "company_name": 1},
        ):
            dealers_by_id[d["id"]] = d
    # Runde 13: B3 — je (Inserat, Firma) gibt es ein eigenes Fahrzeugdokument
    # mit derselben mobile_ad_id. Vorher gewann "das letzte Dokument", also
    # konnten Haendlerkorrekturen (Kilometer nach Abholung, Preis) von Firma B
    # neben den Nutzern von Firma A stehen. Jetzt: nur REINE Inseratsdaten
    # (data solange "verglichen", danach inserat_aktuell), davon das neueste.
    vehicles_by_ad = {}
    if ad_ids:
        async for v in db.vehicles.find(
            {"mobile_ad_id": {"$in": ad_ids}},
            {"_id": 0, "mobile_ad_id": 1, "dealer_id": 1, "lifecycle": 1, "data": 1,
             "inserat_aktuell": 1, "inserat_aktuell_am": 1, "updated_at": 1},
        ):
            lc = v.get("lifecycle") or "verglichen"
            if lc == "verglichen":
                inserat, stand, korrigiert = v.get("data") or {}, v.get("updated_at") or "", False
            elif v.get("inserat_aktuell"):
                inserat, stand, korrigiert = v["inserat_aktuell"], v.get("inserat_aktuell_am") or "", False
            else:
                # data kann Haendlerkorrekturen tragen — nur als Notnagel,
                # wenn keine andere Firma reine Inseratsdaten hat.
                inserat, stand, korrigiert = v.get("data") or {}, "", True
            bisher = vehicles_by_ad.get(v["mobile_ad_id"])
            if bisher is None or (bisher["korrigiert"] and not korrigiert) \
                    or (bisher["korrigiert"] == korrigiert and str(stand) > str(bisher["stand"])):
                vehicles_by_ad[v["mobile_ad_id"]] = {
                    "data": inserat, "stand": stand, "korrigiert": korrigiert}

    out = []
    for r in rows:
        ad_id = r["_id"]
        v = vehicles_by_ad.get(ad_id) or {}
        vd = v.get("data") or {}
        users = []
        for uid in (r.get("user_ids") or []):
            u = users_by_id.get(uid)
            if not u:
                continue
            company = u.get("company_name") or dealers_by_id.get(u.get("dealer_id"), {}).get("company_name") or ""
            users.append({
                "id": u["id"],
                "email": u.get("email"),
                "username": u.get("username"),
                "company_name": company,
                "active": u.get("active", True),
            })
        out.append({
            "ad_id": ad_id,
            "count": r.get("count", 0),
            "sources": sorted([s for s in (r.get("sources") or []) if s]),
            "first_at": r.get("first_at"),
            "last_at": r.get("last_at"),
            "users": users,
            "vehicle": {
                "make": vd.get("make") or vd.get("brand"),
                "model": vd.get("model"),
                "ez": vd.get("ez") or vd.get("first_registration"),
                "mileage": vd.get("mileage"),
                "price": vd.get("price"),
                "fuel": vd.get("fuel"),
                "vin": vd.get("vin") or vd.get("fin"),
                "url": vd.get("url") or vd.get("listing_url"),
                # Runde 13: B3 — von wann die Inseratsdaten sind und ob sie
                # notgedrungen aus einem korrigierten Datensatz stammen
                "stand": v.get("stand") or None,
                "korrigiert": bool(v.get("korrigiert")),
            } if vd else None,
            # Runde 13: B3 — wie viele Firmen dasselbe Inserat halten
            "firmen": len([d for d in (r.get("dealer_ids") or []) if d]),
        })
    return {"items": out, "total": len(out)}


# ---------- URL-Stats (live) ----------
@router.get("/admin/url-stats")
async def admin_url_stats(_=Depends(current_admin)):
    """Live URL-Counter — wie viele Inserate wurden je Quelle abgefragt.
    Drei Zeitfenster: insgesamt, letzte 24h, heute (lokal serverseitig).
    """
    now = datetime.now(timezone.utc)
    today_iso = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    last24_iso = (now - timedelta(hours=24)).isoformat()
    last7d_iso = (now - timedelta(days=7)).isoformat()

    async def _by_source(query: dict) -> Dict[str, int]:
        pipeline = [
            {"$match": query} if query else {"$match": {}},
            {"$group": {"_id": "$source", "n": {"$sum": 1}}},
        ]
        rows = await db.vehicle_comparisons.aggregate(pipeline).to_list(50)
        d = {"mobile": 0, "kleinanzeigen": 0, "autoscout": 0, "autoscout24": 0, "other": 0}
        total = 0
        for r in rows:
            src = (r.get("_id") or "other").lower()
            n = r.get("n", 0)
            total += n
            if src in d:
                d[src] += n
            else:
                d["other"] += n
        # Normalisieren: autoscout24 unter autoscout zusammenfassen
        d["autoscout"] = d.pop("autoscout") + d.pop("autoscout24")
        d["total"] = total
        return d

    return {
        "all_time": await _by_source({}),
        "last_7d": await _by_source({"created_at": {"$gte": last7d_iso}}),
        "last_24h": await _by_source({"created_at": {"$gte": last24_iso}}),
        "today": await _by_source({"created_at": {"$gte": today_iso}}),
        "now": now.isoformat(),
    }


# ---------- Self-password change ----------
@router.post("/admin/me/password")
async def admin_self_password(body: AdminSelfPasswordIn, admin=Depends(current_admin)):
    try:
        pruefe_passwort(body.new_password or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    user = await db.users.find_one({"id": admin["id"]})
    if not user or not await verify_password_async(body.current_password, user["password_hash"]):
        # Rollenpruefung 22.09.2026 (RP-554): 400 statt 401 — lib/api.js meldet
        # bei JEDER 401 ab; ein Tippfehler im aktuellen Passwort warf den
        # Betreiber aus seiner Sitzung (wie beim MFA-Abschalten, AD-06).
        raise HTTPException(400, "Aktuelles Passwort ist nicht korrekt")
    _pw_persoenlich_400(body.new_password, persoenliche_werte(user))
    # Pruefbericht 20.09.2026 (AD-28): vorher current_session_id=None und
    # {ok: true} — der naechste Aufruf dieses Tabs lief in die 401 und auf die
    # Anmeldeseite, obwohl "Passwort geaendert" stand. Jetzt wie
    # /admin/me/mfa/aktivieren: DIESER Aufruf bekommt eine neue Einzel-Sitzung
    # (Token in der Antwort); jedes andere Geraet muss sich neu anmelden, die
    # alte sid passt nicht mehr (Einzel-Sitzung bleibt).
    sid = new_session_id()
    # Nachpruefung 15.09.2026: CAS auf den soeben geprueften Hash.
    r = await db.users.update_one(
        {"id": admin["id"], "password_hash": user["password_hash"]},
        {"$set": {"password_hash": await hash_password_async(body.new_password),
                  "current_session_id": sid, "current_session_seit": now_iso()}},
    )
    if r.matched_count == 0:
        raise HTTPException(409, "Das Passwort wurde gerade anderweitig geändert — bitte neu anmelden")
    await log_activity_sicher("", admin["id"], "admin.passwort.geaendert")
    return {"ok": True, "token": create_token(admin["id"], sid),
            "hinweis": "Andere Geräte müssen sich neu anmelden."}


# ---------- Betrieb: Alarme, Loesch-Warteschlange, Abgleiche ----------
@router.get("/admin/betrieb")
async def admin_betrieb(admin=Depends(current_super_admin)):
    """Sichtbarkeit fuer alles, was frueher still scheiterte (Audit 09/2026):
    offene Betriebsalarme, nicht loeschbare Dateien, haengende
    Freischaltungs-Vorgaenge, letztes Backup. (Pruefbericht 20.09.2026 DO-22:
    "Zahlungen ohne Zugang" entfaellt — Stripe ist seit 14.09.2026 entfernt,
    der Alarm zahlung_ohne_zugang wird nirgends mehr ausgeloest.)"""
    from betrieb import alarm_uebersicht, offene_alarme
    try:
        # Runde 21 (Nebenbefund): serveruebergreifend wie /ready — sonst
        # zeigte der Server ohne eigene Sicherung "kein Backup". Die
        # Auskunft traegt konsistent/konsistenz/inkonsistent; ein
        # inkonsistentes Backup gilt nicht als vollstaendig.
        from backup_service import letztes_backup_info_global
        backup = await letztes_backup_info_global(db)
    except Exception as exc:                      # pragma: no cover
        backup = {"hinweis": f"nicht ermittelbar: {exc}"}
    frist = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    return {
        "alarme": await offene_alarme(db),
        # AL-02/AD-13: vollstaendige Zahlen je Typ, auch wenn die Liste gekuerzt ist
        "alarm_uebersicht": await alarm_uebersicht(db),
        # AL-03: ohne Empfaenger meldet niemand die Alarme per E-Mail
        "alarm_empfaenger": os.environ.get("BETRIEB_MELDUNG_AN", "").strip() or None,
        "datei_loeschungen_offen": await db.storage_delete_retry.count_documents({}),
        "datei_loeschungen_aufgegeben": await db.storage_delete_retry.find(
            {"aufgegeben": True}, {"_id": 0}).limit(50).to_list(50),
        "abo_vorgaenge_haengend": await db.abo_vorgaenge.count_documents(
            {"status": "laeuft", "updated_at": {"$lt": frist}}),
        "backup": backup,
        # Nachpruefung 20.09.2026, Nr. 66: hier stand nur `.get("aktiv")`.
        # Ein abgelaufener Merker (abgestuerztes Sicherungsskript) haette die
        # Betriebsseite weiter als "Wartung laeuft" gezeigt, obwohl die
        # Plattform laengst wieder frei ist. Dieselbe Regel wie in der
        # Middleware und in /api/ready.
        "wartungsmodus": wartung.pausiert(await wartung.lesen_async(db), "POST"),
        # Abo-Audit: Super-Admins ohne Zwei-Faktor sichtbar machen
        # (Kontonummer 13.09.2026: Benutzername zuerst)
        "super_admins_ohne_mfa": [u.get("username") or u.get("email") async for u in db.users.find(
            {"role": "admin", "is_super_admin": True, "active": {"$ne": False},
             "mfa.aktiv": {"$ne": True}}, {"_id": 0, "email": 1, "username": 1})],
        # Runde 12: Es gibt nur den Super-Admin. Alte Konten mit Rolle admin
        # ohne is_super_admin koennen nichts mehr und gehoeren geloescht.
        "admin_konten_ohne_super_admin": [u.get("email") or u.get("username") async for u in db.users.find(
            {"role": "admin", "is_super_admin": {"$ne": True}},
            {"_id": 0, "email": 1, "username": 1})],
        # Runde 17: die Parallelitaets-Backstops muessen sichtbar sein —
        # bei Altdubletten werden sie beim Start nur uebersprungen.
        "termin_index_aktiv": "termin_offen_je_vertrag" in await db.appointments.index_information(),
        "fahrzeug_index_aktiv": any(
            i.get("unique") and i.get("key") == [("dealer_id", 1), ("id", 1)]
            for i in (await db.vehicles.index_information()).values()),
        # Go-Live 14.09.2026 (B5, Punkt 3): "ein Kaufvorgang je Vertrag" haengt
        # allein am Unique-Index kaufvorgaenge(contract_id) (kaufvorgang.py
        # verlaesst sich auf DuplicateKeyError). Fehlt er (z.B. nach drop()),
        # meldete das bisher nur /api/ready — hier war nichts zu sehen.
        "kaufvorgang_index_aktiv": await _kaufvorgang_index_aktiv(),
        # Audit 13.09.2026 Nachbesserung (#18): Backstops Merkliste,
        # laufende Kaufanfrage, offene Marktplatz-Zugangsanfrage
        "favoriten_index_aktiv": "favorit_je_kaeufer_inserat"
            in await db.buyer_favorites.index_information(),
        "interesse_index_aktiv": "interesse_offen_je_kaeufer"
            in await db.listing_interest.index_information(),
        "zugangsanfrage_index_aktiv": "uniq_offene_buyer_access_anfrage"
            in await db.plan_requests.index_information(),
        # Kontonummer (13.09.2026): Konten, die sich per Nummer anmelden
        # muessten, aber keine haben — nur Zaehlung, keine Vergabe.
        "konten_ohne_nummer": await _konten_ohne_nummer_zaehlen(),
        # Rollenprüfung 22.09.2026 (RP-394, Welle 2): letzter (vollstaendiger)
        # Aufraeumlauf und die gescheiterten Schritte (cleanup_service).
        "aufraeumlauf": await _aufraeumlauf_stand(),
        # Pruefbericht 20.09.2026 (AL-21): Umgebungswerte, die eine Grenze
        # verletzt haben und geklemmt wurden (konfig.zahl_env/kommazahl_env).
        "geklemmte_werte": {n: {"eingestellt": r, "wirksam": w}
                            for n, (r, w) in __import__("konfig").GEKLEMMT.items()},
    }


async def _aufraeumlauf_stand() -> dict:
    """Rollenprüfung 22.09.2026 (RP-394): Stand aus system_reports (typ
    "aufraeumlauf") plus "ueberfaellig" nach derselben Grenze wie /api/ready
    (AUFRAEUMLAUF_WARN_H, Standard 3 h). Wirft nie."""
    from konfig import zahl_env
    grenze_h = zahl_env("AUFRAEUMLAUF_WARN_H", 3, unten=1)
    try:
        bericht = await db.system_reports.find_one({"typ": "aufraeumlauf"}, {"_id": 0}) or {}
    except Exception as exc:  # noqa: BLE001
        return {"hinweis": f"nicht ermittelbar: {exc}", "grenze_h": grenze_h}
    stand = {k: bericht.get(k) for k in ("letzter_lauf", "letzter_vollstaendiger_lauf",
                                         "vollstaendig")}
    stand["fehlgeschlagen"] = list(bericht.get("fehlgeschlagen") or [])
    stand["grenze_h"] = grenze_h
    grenze_iso = (datetime.now(timezone.utc) - timedelta(hours=grenze_h)).isoformat()
    # Nur "ueberfaellig", wenn es ueberhaupt schon Laeufe gab — ein frischer
    # Stack ohne Bericht meldet /api/ready bereits nach der Anlaufzeit.
    stand["ueberfaellig"] = bool(bericht.get("letzter_lauf")) and (
        str(bericht.get("letzter_vollstaendiger_lauf") or "") < grenze_iso)
    return stand


async def _kaufvorgang_index_aktiv() -> bool:
    """Go-Live 14.09.2026 (B5): steht der Unique-Index kaufvorgaenge(contract_id)?"""
    return any(i.get("unique") and [f for f, _r in i.get("key", [])] == ["contract_id"]
               for i in (await db.kaufvorgaenge.index_information()).values())


async def _konten_ohne_nummer_zaehlen() -> dict:
    from kontenanlage import konten_ohne_nummer
    return await konten_ohne_nummer(db)


# Wunsch Ahmad 21.09.2026 (Betrieb): Testmail an BETRIEB_MELDUNG_AN per Knopf.
# Bisher war der einzige Test der Tagesbericht — erst um 8 Uhr, und bei einem
# Fehlschlag erst am naechsten Morgen wieder. Hoechstens eine Testmail je
# Minute und Prozess: ein Dauerklick soll das Resend-Kontingent nicht leeren.
BETRIEB_TESTMAIL_ABSTAND_S = 60
_betrieb_testmail = {"zuletzt": None}      # time.monotonic() des letzten Versands
# Pruefung 21.09.2026 (Betrieb): der Versand wartete synchron — bei einem
# nicht erreichbaren Resend bis zu 6 x 30 s + Pausen (~3 Minuten). axios bricht
# nach 60 s ab, Cloudflare nach ~100 s (524): genau der Fall, den die Testmail
# aufklaeren soll, kam ohne Grund an. Jetzt hoechstens 45 s, dann eine klare
# Meldung. Abbrechen ist sicher: jeder Klick hat einen eigenen Idempotency-Key.
BETRIEB_TESTMAIL_MAX_S = 45
BETRIEB_TESTMAIL_PROTOKOLL = "Server-Protokoll: docker compose logs --since 10m backend | grep email_service"


@router.get("/admin/ki")
async def admin_ki(admin=Depends(current_super_admin)):
    """Stufe 4 (26.09.2026): Betriebszahlen der KI-Bewertung — Aufrufe je
    Status/Art (30 Tage), Dauer, Tokens, Kostenschaetzung, letzte Fehler,
    Lernfaelle und die daraus gebildeten Erfahrungswerte."""
    from ai import kalibrierung
    from ai.provider import ki_aktiv, ki_modell
    return {"aktiv": ki_aktiv(), "modell": ki_modell(), **(await kalibrierung.statistik())}


@router.post("/admin/ki/marktdaten")
async def admin_ki_marktdaten(admin=Depends(current_super_admin)):
    """Stufe 5 (26.09.2026): Markttabelle jetzt neu recherchieren (Websuche,
    ADAC/Smart-Repair) — sonst laeuft das automatisch alle KI_MARKTDATEN_TAGE."""
    from ai import marktdaten
    erg = await marktdaten.aktualisieren(erzwingen=True)
    return {k: erg.get(k) for k in ("status", "grund", "stand", "aktualisiert", "positionen", "quellen",
                                   "zusammenfassung", "suchen", "dauer_ms", "alter_tage") if k in erg}


@router.post("/admin/betrieb/testmail")
async def admin_betrieb_testmail(admin=Depends(current_super_admin)):
    """Kurze Probe-Mail an BETRIEB_MELDUNG_AN ueber denselben Versandweg wie
    Alarme, Anfragen und Tagesbericht (betriebsmeldung -> email_service).

    Pruefung 21.09.2026 (Betrieb): Lehnt der Anbieter ab oder antwortet er
    nicht, kommt 200 mit {ok: false, grund} — KEIN 502/504. Cloudflare ersetzt
    502/504 vom Server durch eine eigene Fehlerseite; im Browser stand dann
    nur "Request failed with status code 502", der Grund (z. B. "Resend lehnt
    ab (HTTP 403): domain not verified") ging verloren."""
    import time

    import betriebsmeldung
    import email_service
    from provider_fetch import MOCK_PROVIDER_FETCH
    ziel = betriebsmeldung.empfaenger()
    if not ziel:
        raise HTTPException(400, "Keine Adresse eingetragen (BETRIEB_MELDUNG_AN)")
    if not email_service.gueltige_adresse(ziel):
        raise HTTPException(400, f"BETRIEB_MELDUNG_AN ist keine gültige E-Mail-Adresse "
                                 f"(„{ziel[:120]}“) — genau eine Adresse, ohne Leerzeichen "
                                 f"und ohne Anführungszeichen.")
    if not MOCK_PROVIDER_FETCH and not email_service.email_configured():
        raise HTTPException(503, "E-Mail-Versand ist nicht eingerichtet (RESEND_API_KEY "
                                 "oder SMTP_* in der .env setzen) — es wurde keine Mail "
                                 "verschickt. Ohne Versandweg kommen auch Alarme und "
                                 "Tagesbericht nicht an.")
    jetzt = time.monotonic()
    zuletzt = _betrieb_testmail["zuletzt"]
    if zuletzt is not None and jetzt - zuletzt < BETRIEB_TESTMAIL_ABSTAND_S:
        rest = int(BETRIEB_TESTMAIL_ABSTAND_S - (jetzt - zuletzt)) + 1
        raise HTTPException(429, f"Höchstens eine Testmail pro Minute — bitte noch "
                                 f"{rest} s warten.")
    # Vor dem Versand merken (kein await dazwischen): ein Doppelklick
    # schickt keine zweite Mail hinterher.
    _betrieb_testmail["zuletzt"] = jetzt
    if MOCK_PROVIDER_FETCH:
        # Last-/CI-Tests: kein echter Versand, aber ehrlich markiert (wie
        # beim Vertragsversand).
        return {"ok": True, "an": ziel, "zustellung": "mock"}
    try:
        ok, beleg, grund = await asyncio.wait_for(
            betriebsmeldung.testmail_senden(ziel, von=_handelnder(admin)),
            timeout=BETRIEB_TESTMAIL_MAX_S)
    except asyncio.TimeoutError:
        log.error("[betriebsmeldung] Testmail an %s: keine Antwort des Mail-Anbieters "
                  "innerhalb von %s s", ziel, BETRIEB_TESTMAIL_MAX_S)
        return {"ok": False, "an": ziel, "zustellung": "unklar",
                "grund": f"Der Mail-Anbieter hat nicht innerhalb von {BETRIEB_TESTMAIL_MAX_S} s "
                         f"geantwortet — Ausgang unklar (die Testmail an {ziel} kann noch "
                         f"ankommen). {BETRIEB_TESTMAIL_PROTOKOLL}"}
    if not ok:
        return {"ok": False, "an": ziel, "zustellung": "abgelehnt",
                "grund": f"Testmail an {ziel} nicht zugestellt: "
                         + (grund or "der Mail-Anbieter hat sie nicht angenommen")
                         + f" — {BETRIEB_TESTMAIL_PROTOKOLL}"}
    return {"ok": True, "an": ziel, "zustellung": beleg.split(":", 1)[0] or "versendet",
            "beleg": beleg}


@router.post("/admin/betrieb/alarme/{alarm_id}/quittieren")
async def admin_alarm_quittieren(alarm_id: str, admin=Depends(current_super_admin)):
    from betrieb import quittieren
    if not await quittieren(db, alarm_id, _handelnder(admin)):
        raise HTTPException(404, "Alarm nicht gefunden oder bereits quittiert")
    return {"ok": True}


@router.post("/admin/betrieb/nachholen")
async def admin_betrieb_nachholen(admin=Depends(current_super_admin)):
    """Reparaturlaeufe sofort anstossen (sonst alle 10 Minuten automatisch)."""
    # Runde 17: fehlende Unique-Indizes (Altdubletten beim Start) ohne
    # Neustart nachholen, sobald die Daten bereinigt sind.
    # Pruefbericht 20.09.2026 (AL-19): KEIN Produktionsabbruch aus dem Handler
    # (abbruch=False — vorher SystemExit(78) mitten in der Anfrage, der Worker
    # endete ohne Antwort); Dubletten stehen als "dubletten" in der Antwort.
    # _favoriten_unique_index/_interesse_/_buyer_access_ bereinigen selbst und
    # brechen nie ab (_unique_index_mit_bereinigung).
    from indizes import (_termin_unique_index, _unique_index_sicher, _favoriten_unique_index,
                         _interesse_unique_index, _buyer_access_unique_index, termin_dubletten)
    termin_index = await _termin_unique_index(abbruch=False)
    return {"abo_vorgaenge": await abo_vorgaenge_nachholen(),
            "termin_index": termin_index,
            "dubletten": {"termine": [] if termin_index else await termin_dubletten()},
            "fahrzeug_index": await _unique_index_sicher(
                db.vehicles, ["dealer_id", "id"], abbruch_in_produktion=False),
            # Go-Live 14.09.2026 (B5, Punkt 3): wie in server.ensure_indexes —
            # nach einem drop()-Reset ohne Neustart nachholbar.
            "kaufvorgang_index": await _unique_index_sicher(
                db.kaufvorgaenge, "contract_id", abbruch_in_produktion=False),
            # Audit 13.09.2026 Nachbesserung (#18): Indizes mit automatischer
            # Dublettenbereinigung — schrieb die alte Fassung beim Rollout
            # dazwischen, fehlten sie sonst bis zum naechsten Neustart.
            "favoriten_index": await _favoriten_unique_index(),
            "interesse_index": await _interesse_unique_index(),
            "zugangsanfrage_index": await _buyer_access_unique_index()}


# ---------- Zwei-Faktor-Anmeldung (Admin / Super-Admin) ----------
class MfaCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=40)


@router.get("/admin/me/mfa")
async def admin_mfa_status(admin=Depends(current_admin)):
    voll = await db.users.find_one({"id": admin["id"]}, {"_id": 0, "mfa": 1})
    m = (voll or {}).get("mfa") or {}
    # Rollenpruefung 22.09.2026 (RP-556): Pflicht und laufende Gnadenfrist
    # mitliefern — die Einstellungen warnen dann auch nach einem Neuladen.
    from routes.auth import mfa_pflicht_aktiv
    frist = m.get("pflicht_ausgesetzt_bis") if not m.get("aktiv") else None
    # Rollenpruefung RP-556 (Welle B3): laeuft ein Geraetewechsel (Pending-
    # Geheimnis NEBEN dem aktiven), meldet der Status bis wann.
    wechsel_bis = _mfa_wechsel_bis(m)
    return {"aktiv": bool(m.get("aktiv")), "aktiviert_am": m.get("aktiviert_am"),
            "wiederherstellungscodes_uebrig": len(m.get("wiederherstellung") or []),
            # Wunsch Ahmad 21.09.2026 (AD-06): wann die Notfall-Codes zuletzt neu erzeugt wurden
            "codes_erneuert_am": m.get("codes_erneuert_am"),
            "einrichtung_offen": bool(m.get("pending_secret")) and not m.get("aktiv"),
            "wechsel_offen": wechsel_bis is not None,
            "wechsel_bis": wechsel_bis,
            "geraet_gewechselt_am": m.get("geraet_gewechselt_am"),
            "pflicht": mfa_pflicht_aktiv() and bool(admin.get("is_super_admin")),
            "neu_einrichten_bis": frist if frist and str(frist) > now_iso() else None}


MFA_EINRICHTUNG_MAX_S = 3600
# Rollenpruefung RP-556 (Welle B3): ein begonnener Geraetewechsel (neues
# Geheimnis neben dem aktiven) verfaellt nach 15 Minuten.
MFA_WECHSEL_MAX_S = 900


def _pending_alter_s(m: dict) -> float:
    """Alter des Pending-Geheimnisses in Sekunden (fehlt der Zeitstempel:
    'uralt', also abgelaufen)."""
    try:
        return (datetime.now(timezone.utc)
                - datetime.fromisoformat(m.get("pending_seit", ""))).total_seconds()
    except (TypeError, ValueError):
        return 10 ** 9


def _mfa_wechsel_laeuft(m: dict) -> bool:
    """Rollenpruefung RP-556 (Welle B3): aktive Zwei-Faktor MIT Pending-
    Geheimnis aus /admin/me/mfa/wechsel (Kennzeichen pending_wechsel) — nur
    dann darf /aktivieren das aktive Geheimnis ersetzen."""
    return bool(m.get("aktiv") and m.get("pending_secret") and m.get("pending_wechsel"))


def _mfa_wechsel_bis(m: dict):
    if not _mfa_wechsel_laeuft(m):
        return None
    rest = MFA_WECHSEL_MAX_S - _pending_alter_s(m)
    if rest <= 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=rest)).isoformat()
# Rollenpruefung 22.09.2026 (RP-556): Frist nach dem Abschalten zum Neu-
# Einrichten (wie das Notfall-Skript scripts/mfa_pruefen.py).
MFA_GNADENFRIST_MIN = 30


@router.post("/admin/me/mfa/einrichten")
async def admin_mfa_einrichten(admin=Depends(current_admin)):
    """Neues Geheimnis erzeugen (noch NICHT aktiv) — Anzeige als otpauth-Link
    bzw. Klartext fuer die manuelle Eingabe in der Authenticator-App."""
    import mfa as _mfa
    # Ein bereits erzeugtes, frisches Geheimnis WIEDERVERWENDEN. Sonst
    # erzeugt jeder erneute Klick auf "Einrichten" ein neues, waehrend in der
    # Authenticator-App noch das erste steht — der Code passt dann nie, und
    # die Meldung "Code ungueltig" fuehrt in die Irre.
    voll = await db.users.find_one({"id": admin["id"]}, {"_id": 0, "mfa": 1})
    vorhanden = (voll or {}).get("mfa") or {}
    if vorhanden.get("aktiv"):
        # Runde 15 (15.09.2026): eine aktive Zwei-Faktor-Anmeldung wird NICHT
        # aus einer laufenden Sitzung heraus ersetzt — sonst stellt ein
        # gestohlenes Token MFA auf das eigene Geraet um. Geraetewechsel:
        # Rollenpruefung RP-556 (Welle B3): /admin/me/mfa/wechsel mit dem
        # aktuellen App-Code (der alte Schluessel gilt bis zur Bestaetigung
        # weiter) — oder wie bisher abschalten, dann neu einrichten.
        raise HTTPException(409, "Zwei-Faktor ist bereits aktiv. Zum Wechsel des Geräts "
                                 "„Gerät wechseln“ nutzen (aktueller Code aus der bisherigen "
                                 "App; der alte Schlüssel gilt weiter, bis der neue bestätigt "
                                 "ist) — oder mit dem aktuellen Code abschalten, dann neu "
                                 "einrichten.")
    secret = None
    if vorhanden.get("pending_secret") and not vorhanden.get("aktiv"):
        try:
            alter = (datetime.now(timezone.utc)
                     - datetime.fromisoformat(vorhanden.get("pending_seit", ""))
                     ).total_seconds()
        except (TypeError, ValueError):
            alter = 10 ** 9
        if alter < 3600:                       # eine Stunde lang dasselbe
            secret = _mfa.entschluesseln(vorhanden["pending_secret"]) or None
    neu_erzeugt = secret is None
    if neu_erzeugt:
        secret = _mfa.secret_erzeugen()
    r = await db.users.update_one(
        {"id": admin["id"], "mfa.aktiv": {"$ne": True}},
        {"$set": {"mfa.pending_secret": _mfa.verschluesseln(secret),
                  "mfa.pending_seit": now_iso() if neu_erzeugt
                  else vorhanden.get("pending_seit", now_iso())}})
    if r.matched_count == 0:
        raise HTTPException(409, "Zwei-Faktor wurde gerade aktiviert — bitte Seite neu laden.")
    # Kontonummer (13.09.2026): Label in der Authenticator-App = Benutzername
    return {"secret": secret, "otpauth_uri": _mfa.provisioning_uri(secret, admin.get("username") or admin.get("email") or admin["id"]),
            "hinweis": "Code aus der App eingeben, um die Zwei-Faktor-Anmeldung zu aktivieren."}


@router.post("/admin/me/mfa/aktivieren")
async def admin_mfa_aktivieren(body: MfaCodeIn, admin=Depends(current_admin)):
    import mfa as _mfa
    voll = await db.users.find_one({"id": admin["id"]}, {"_id": 0, "mfa": 1})
    m = (voll or {}).get("mfa") or {}
    # Rollenpruefung RP-556 (Welle B3): Geraetewechsel — bei AKTIVER Zwei-
    # Faktor gilt der Aufruf dem Pending-Geheimnis aus /admin/me/mfa/wechsel
    # (dort war der aktuelle App-Code Pflicht). Ohne ein solches bleibt es
    # bei "bereits aktiv": eine gestohlene Sitzung allein ersetzt nichts.
    wechsel = _mfa_wechsel_laeuft(m)
    if m.get("aktiv") and not wechsel:
        raise HTTPException(409, "Zwei-Faktor ist bereits aktiv.")
    secret = _mfa.entschluesseln(m.get("pending_secret", "")) if m.get("pending_secret") else None
    if not secret:
        raise HTTPException(400, "Zuerst einrichten (Geheimnis erzeugen)")
    # Runde 15: ein nie abgeschlossenes Geheimnis verfaellt nach einer Stunde
    # (vorher pruefte nur /einrichten das Alter, /aktivieren nie).
    alter = _pending_alter_s(m)
    if wechsel and alter > MFA_WECHSEL_MAX_S:
        # Abgelaufener Wechsel: Pending-Geheimnis weg, das aktive bleibt.
        await db.users.update_one(
            {"id": admin["id"], "mfa.pending_secret": m.get("pending_secret")},
            {"$unset": {"mfa.pending_secret": "", "mfa.pending_seit": "",
                        "mfa.pending_wechsel": ""}})
        raise HTTPException(410, "Der Gerätewechsel ist abgelaufen (15 Minuten) — der bisherige "
                                 "Schlüssel gilt weiter. Bitte erneut „Gerät wechseln“ wählen "
                                 "und den neuen Schlüssel in die App übernehmen.")
    if alter > MFA_EINRICHTUNG_MAX_S:
        raise HTTPException(410, "Die Einrichtung ist abgelaufen — bitte neu einrichten und den "
                                 "neuen Schlüssel in die App übernehmen.")
    if wechsel and not await _admin_mfa_limiter.check(admin["id"]):
        # Beim Wechsel ersetzt ein passender Code das AKTIVE Geheimnis —
        # dieselbe Mengenbremse wie bei Abschalten/Notfall-Codes (10/min).
        raise HTTPException(429, MFA_ZU_VIELE_EINGABEN)
    zaehler = _mfa.code_pruefen(secret, body.code)
    if zaehler is None:
        raise HTTPException(
            400, "Code ungültig. Zwei häufige Gründe: die Uhrzeit des Geräts "
                 "weicht ab (auf automatische Zeit stellen), oder in der App "
                 "steht noch ein älterer Eintrag für dieses Konto — dann den "
                 "Eintrag dort löschen und den hier angezeigten Schlüssel neu "
                 "übernehmen.")
    codes, hashes = _mfa.wiederherstellungscodes()
    if wechsel:
        # Rollenpruefung RP-556 (Welle B3): erst JETZT, nach bestaetigtem Code
        # vom neuen Geraet, ersetzt das neue Geheimnis das aktive — per
        # Compare-and-set auf GENAU das aktive UND das geprueft Pending-
        # Geheimnis (paralleles Abschalten oder ein zweiter Wechsel -> 409).
        # Notfall-Codes werden NEU erzeugt (die alten gelten nicht mehr):
        # /aktivieren liefert sie immer, die Oberflaeche zeigt sie mit dem
        # Ablage-Zwang an, und der bisherige Weg (Abschalten -> Einrichten)
        # erzeugte sie ebenfalls neu — ein Geraetewechsel ist der Moment, an
        # dem ein vollstaendiger, frischer Satz gebraucht wird. Die Sitzung
        # bleibt: sie wurde bereits mit zweitem Faktor ausgestellt.
        jetzt = now_iso()
        r = await db.users.update_one(
            {"id": admin["id"], "mfa.aktiv": True, "mfa.secret": m.get("secret"),
             "mfa.pending_secret": m.get("pending_secret")},
            {"$set": {"mfa": {"aktiv": True, "secret": _mfa.verschluesseln(secret),
                              "letzter_zaehler": zaehler, "fehlversuche": 0,
                              "wiederherstellung": hashes,
                              "aktiviert_am": m.get("aktiviert_am") or jetzt,
                              "geraet_gewechselt_am": jetzt, "codes_erneuert_am": jetzt}}})
        if r.matched_count == 0:
            raise HTTPException(409, "Die Zwei-Faktor-Anmeldung wurde inzwischen geändert — "
                                     "bitte Seite neu laden.")
        uebrig_alt = len(m.get("wiederherstellung") or [])
        try:
            await db.zugangs_aenderungen.insert_one({
                "id": str(uuid.uuid4()), "art": "mfa_geraet_gewechselt",
                "subject_user_id": admin["id"], "subject_email": admin.get("email", ""),
                "subject_super_admin": bool(admin.get("is_super_admin")),
                "alt": f"{uebrig_alt}_wiederherstellungscodes",
                "neu": f"{len(codes)}_neue_wiederherstellungscodes",
                "grund": "Gerät gewechselt (Einstellungen, mit App-Code des alten und des "
                         "neuen Geräts)",
                "admin_id": admin["id"], "admin_email": _handelnder(admin),
                "created_at": jetzt})
        except Exception:
            log.exception("zugangs_aenderungen: Eintrag mfa_geraet_gewechselt fuer %s nicht "
                          "gespeichert", admin["id"])
        await log_activity_sicher("", admin["id"], "admin.mfa.geraet_gewechselt",
                                  meta={"alt_uebrig": uebrig_alt, "neu": len(codes)})
        return {"ok": True, "aktiv": True, "geraet_gewechselt": True,
                "wiederherstellungscodes": codes,
                "hinweis": "Gerät gewechselt: ab jetzt gilt nur der Schlüssel auf dem neuen "
                           "Gerät — den alten Eintrag in der bisherigen App löschen. Die "
                           "bisherigen Notfall-Codes gelten nicht mehr; diese Codes jetzt "
                           "sicher aufbewahren — sie werden nur einmal angezeigt."}
    # Runde 15: (a) Compare-and-set auf GENAU das geprueft Pending-Geheimnis —
    # zwei parallele Aktivierungen erzeugten sonst zwei Code-Saetze, von denen
    # der angezeigte nie galt; (b) die bisherige, OHNE zweiten Faktor
    # ausgestellte Sitzung endet: dieser Aufruf bekommt eine neue Sitzung
    # (Token in der Antwort), jedes andere Geraet muss sich neu anmelden.
    sid = new_session_id()
    r = await db.users.update_one(
        {"id": admin["id"], "mfa.pending_secret": m.get("pending_secret"),
         "mfa.aktiv": {"$ne": True}},
        {"$set": {"mfa": {"aktiv": True, "secret": _mfa.verschluesseln(secret),
                          "letzter_zaehler": zaehler, "fehlversuche": 0,
                          "wiederherstellung": hashes, "aktiviert_am": now_iso()},
                  "current_session_id": sid, "current_session_seit": now_iso()}})
    if r.matched_count == 0:
        raise HTTPException(409, "Die Einrichtung wurde gerade anderweitig abgeschlossen oder "
                                 "geändert — bitte Seite neu laden.")
    await log_activity_sicher("", admin["id"], "admin.mfa.aktiviert")
    return {"ok": True, "aktiv": True, "wiederherstellungscodes": codes,
            "token": create_token(admin["id"], sid),
            "hinweis": "Diese Codes jetzt sicher aufbewahren — sie werden nur einmal angezeigt. "
                       "Andere Geräte müssen sich neu anmelden (jetzt mit zweitem Faktor)."}


MFA_CODE_VERBRAUCHT = ("Dieser Code wurde gerade schon verwendet — bitte den nächsten "
                       "Code aus der App abwarten.")
MFA_FEHLVERSUCHE_MAX = 5                 # wie /auth/login/mfa: danach 15 Minuten Sperre
MFA_ZU_VIELE_EINGABEN = ("Zu viele Code-Eingaben für die Zwei-Faktor-Anmeldung — bitte "
                         "60 Sekunden warten.")

# Pruefung 21.09.2026 (MFA): gemeinsamer Zaehler je Admin-Konto (Mongo, alle
# Worker, beide Server) wie login_mfa_limiter beim Anmelden. Die Sperre nach 5
# falschen Codes allein hielt nur Einzelanfragen nacheinander auf: ein Schwung
# GLEICHZEITIGER Anfragen hatte den Stand vor der Sperre gelesen und wurde voll
# gegen TOTP geprueft. Schluessel ist die Konto-ID, nicht die IP — auch viele
# IPs mit derselben (gestohlenen) Sitzung werden gebremst. fail_closed: faellt
# der gemeinsame Zaehler aus, gilt "gesperrt" statt eines Zaehlers je Prozess.
from rate_limiter import SlidingWindowRateLimiter as _Begrenzer  # noqa: E402

_admin_mfa_limiter = _Begrenzer(max_attempts=10, window_seconds=60, name="admin-mfa",
                                fail_closed=True)


async def _mfa_app_code_bestaetigen(admin_id: str, m: dict, code: str) -> int:
    """Aktuellen 6-stelligen App-Code einer AKTIVEN Zwei-Faktor-Anmeldung
    pruefen (Abschalten, neue Notfall-Codes). Liefert den passenden
    30-s-Zaehler.

    Wunsch Ahmad 21.09.2026 (Pruefbericht AD-06): dieselben Regeln wie beim
    Anmelden (/auth/login/mfa) — Replay-Schutz ueber letzter_zaehler, 5
    Fehlversuche -> 15 Minuten Sperre (vorher liess sich der Code hier aus
    einer laufenden Sitzung beliebig oft raten), dazu die Mengenbremse
    _admin_mfa_limiter (10 je Minute und Konto). Nur ein App-Code, KEIN
    Wiederherstellungscode.
    Fehler sind 400, nie 401: lib/api.js meldet bei JEDER 401 ab — ein
    vertippter Code beim "Abschalten" warf den Betreiber aus der Sitzung."""
    import mfa as _mfa
    # Pruefung 21.09.2026 (MFA): VOR allem anderen zaehlen — auch Formfehler
    # und richtige Codes, genau wie beim Anmelden.
    if not await _admin_mfa_limiter.check(admin_id):
        raise HTTPException(429, MFA_ZU_VIELE_EINGABEN)
    sperre = m.get("gesperrt_bis")
    if sperre and sperre > now_iso():
        raise HTTPException(429, "Zweiter Faktor vorübergehend gesperrt (zu viele falsche Codes) — "
                                 "bitte in 15 Minuten erneut versuchen.")
    # Pruefung 21.09.2026 (MFA): Ziffern anderer Schriften ('١٢٣٤٥٦',
    # '１２３４５６') erst in ASCII umsetzen, dann NUR ASCII-Ziffern zulassen —
    # vorher bestanden sie isdigit(), und hmac.compare_digest warf 500.
    code = _mfa.code_normalisieren(code)
    if not _mfa.code_format_ok(code):
        # Kann nie passen (z.B. ein Notfall-Code) — kein Rateversuch, kein Fehlversuch.
        raise HTTPException(400, "Bitte den aktuellen 6-stelligen Code aus der Authenticator-App "
                                 "eingeben (nur Ziffern; hier gilt kein Notfall-Code).")
    secret = _mfa.entschluesseln(m.get("secret", "")) or ""
    zaehler = _mfa.code_pruefen(secret, code, int(m.get("letzter_zaehler", -1))) if secret else None
    if zaehler is not None:
        return zaehler
    if secret and _mfa.code_pruefen(secret, code, -1) is not None:
        # Richtig, aber schon benutzt (meist der Code vom Anmelden): kein Fehlversuch.
        raise HTTPException(400, MFA_CODE_VERBRAUCHT)
    from pymongo import ReturnDocument
    doc = await db.users.find_one_and_update(
        {"id": admin_id, "mfa.aktiv": True}, {"$inc": {"mfa.fehlversuche": 1}},
        projection={"_id": 0, "mfa.fehlversuche": 1}, return_document=ReturnDocument.AFTER)
    if int(((doc or {}).get("mfa") or {}).get("fehlversuche", 0)) >= MFA_FEHLVERSUCHE_MAX:
        await db.users.update_one(
            {"id": admin_id, "mfa.fehlversuche": {"$gte": MFA_FEHLVERSUCHE_MAX}},
            {"$set": {"mfa.gesperrt_bis": (datetime.now(timezone.utc)
                                           + timedelta(minutes=15)).isoformat(),
                      "mfa.fehlversuche": 0}})
    await log_activity_sicher("", admin_id, "admin.mfa.code_falsch")
    raise HTTPException(400, "Code ungültig")


@router.post("/admin/me/mfa/deaktivieren")
async def admin_mfa_deaktivieren(body: MfaCodeIn, admin=Depends(current_admin)):
    voll = await db.users.find_one({"id": admin["id"]}, {"_id": 0, "mfa": 1})
    m = (voll or {}).get("mfa") or {}
    if not m.get("aktiv"):
        raise HTTPException(400, "Zwei-Faktor ist nicht aktiv")
    # Wunsch Ahmad 21.09.2026 (AD-06): falscher Code -> 400 statt 401 (sonst
    # Abmeldung), Fehlversuche zaehlen wie beim Anmelden.
    await _mfa_app_code_bestaetigen(admin["id"], m, body.code)
    # Runde 15: nur GENAU das geprueft Geheimnis abschalten — ein alter
    # Abschalt-Aufruf loeschte sonst eine inzwischen neu eingerichtete MFA.
    # Rollenpruefung 22.09.2026 (RP-556): In Produktion ist der zweite Faktor
    # fuer den Betreiber PFLICHT (routes/auth.py) — nach dem Abschalten liess
    # ihn die naechste Anmeldung nicht mehr herein (403), dauerhaft. Jetzt
    # bleibt statt "gar nichts" eine Gnadenfrist stehen (dasselbe Feld wie
    # beim Notfall-Skript scripts/mfa_pruefen.py): 30 Minuten zum Neu-
    # Einrichten; die Oberflaeche oeffnet die Einrichtung sofort.
    from routes.auth import mfa_pflicht_aktiv
    frist_bis = (datetime.now(timezone.utc)
                 + timedelta(minutes=MFA_GNADENFRIST_MIN)).isoformat()
    r = await db.users.update_one(
        {"id": admin["id"], "mfa.aktiv": True, "mfa.secret": m.get("secret")},
        {"$set": {"mfa": {"aktiv": False, "pflicht_ausgesetzt_bis": frist_bis,
                          "abgeschaltet_am": now_iso()}}})
    if r.matched_count == 0:
        raise HTTPException(409, "Die Zwei-Faktor-Anmeldung wurde inzwischen geändert — "
                                 "bitte Seite neu laden.")
    await log_activity_sicher("", admin["id"], "admin.mfa.deaktiviert")
    pflicht = mfa_pflicht_aktiv() and bool(admin.get("is_super_admin"))
    return {"ok": True, "aktiv": False, "pflicht": pflicht,
            "neu_einrichten_bis": frist_bis if pflicht else None,
            "hinweis": (f"Zwei-Faktor ist aus. Für den Betreiber ist sie Pflicht: bitte in den "
                        f"nächsten {MFA_GNADENFRIST_MIN} Minuten neu einrichten — sonst ist "
                        "nach dem Abmelden keine Anmeldung mehr möglich (dann nur noch über "
                        "scripts/mfa_pruefen.py auf dem Server).") if pflicht else None}


@router.post("/admin/me/mfa/codes-neu")
async def admin_mfa_codes_neu(body: MfaCodeIn, admin=Depends(current_admin)):
    """NUR die 8 Notfall-Codes (Wiederherstellungscodes) neu erzeugen — der
    Schluessel in der Authenticator-App bleibt, die Sitzung auch.

    Wunsch Ahmad 21.09.2026 (Pruefbericht AD-06): Neue Codes gab es vorher
    nur ueber Abschalten -> Einrichten -> Aktivieren. Dazwischen war der
    Betreiber ohne zweiten Faktor, und ging der Tab verloren, liess ihn die
    Anmeldung in Produktion nicht mehr herein. Verlangt wird der aktuelle
    App-Code (kein Notfall-Code — sonst vermehrte ein gefundener Code sich
    selbst). Alle bisherigen Codes gelten ab sofort nicht mehr; die neuen
    stehen genau einmal in der Antwort."""
    import mfa as _mfa
    voll = await db.users.find_one({"id": admin["id"]}, {"_id": 0, "mfa": 1})
    m = (voll or {}).get("mfa") or {}
    if not m.get("aktiv"):
        raise HTTPException(409, "Die Zwei-Faktor-Anmeldung ist nicht aktiv — bitte zuerst "
                                 "einrichten; dabei entstehen die Notfall-Codes.")
    zaehler = await _mfa_app_code_bestaetigen(admin["id"], m, body.code)
    codes, hashes = _mfa.wiederherstellungscodes()
    # Compare-and-set wie beim Anmelden: GENAU das geprueft Geheimnis, und
    # der Code wird dabei atomar verbraucht. Zwei parallele Klicks erzeugen
    # so nicht zwei Code-Saetze, von denen der zuerst angezeigte nie galt.
    r = await db.users.update_one(
        {"id": admin["id"], "mfa.aktiv": True, "mfa.secret": m.get("secret"),
         "$or": [{"mfa.letzter_zaehler": {"$lt": zaehler}},
                 {"mfa.letzter_zaehler": {"$exists": False}}]},
        {"$set": {"mfa.wiederherstellung": hashes, "mfa.letzter_zaehler": zaehler,
                  "mfa.fehlversuche": 0, "mfa.codes_erneuert_am": now_iso()}})
    if r.matched_count == 0:
        jetzt = ((await db.users.find_one({"id": admin["id"]}, {"_id": 0, "mfa": 1}))
                 or {}).get("mfa") or {}
        if jetzt.get("aktiv") and jetzt.get("secret") == m.get("secret"):
            raise HTTPException(400, MFA_CODE_VERBRAUCHT)
        raise HTTPException(409, "Die Zwei-Faktor-Anmeldung wurde inzwischen geändert — "
                                 "bitte Seite neu laden.")
    uebrig_alt = len(m.get("wiederherstellung") or [])
    # Die Codes sind schon ersetzt: ein Fehler im Verlauf darf die Antwort
    # nicht mehr mit 500 abbrechen — sonst waeren die alten Codes ungueltig
    # und die neuen nie angezeigt.
    try:
        await db.zugangs_aenderungen.insert_one({
            "id": str(uuid.uuid4()), "art": "mfa_codes_neu",
            "subject_user_id": admin["id"], "subject_email": admin.get("email", ""),
            "subject_super_admin": bool(admin.get("is_super_admin")),
            "alt": f"{uebrig_alt}_wiederherstellungscodes",
            "neu": f"{len(codes)}_neue_wiederherstellungscodes",
            "grund": "selbst erneuert (Einstellungen, mit App-Code)",
            "admin_id": admin["id"], "admin_email": _handelnder(admin),
            "created_at": now_iso()})
    except Exception:
        log.exception("zugangs_aenderungen: Eintrag mfa_codes_neu fuer %s nicht gespeichert",
                      admin["id"])
    await log_activity_sicher("", admin["id"], "admin.mfa.codes_neu",
                              meta={"alt_uebrig": uebrig_alt, "neu": len(codes)})
    return {"ok": True, "wiederherstellungscodes": codes,
            "hinweis": "Die bisherigen Notfall-Codes gelten ab sofort nicht mehr. Diese Codes "
                       "jetzt sicher aufbewahren — sie werden nur einmal angezeigt."}


@router.post("/admin/me/mfa/wechsel")
async def admin_mfa_wechsel(body: MfaCodeIn, admin=Depends(current_admin)):
    """Rollenpruefung 22.09.2026 (RP-556, Welle B3): Geraet wechseln OHNE
    Abschalten. Vorher fuehrte der Weg ueber "Abschalten -> neu einrichten"
    — dazwischen war der Betreiber ohne zweiten Faktor, und in Produktion
    liess ihn die Anmeldung nach Ablauf der Gnadenfrist nicht mehr herein.

    Jetzt: mit dem AKTUELLEN App-Code (kein Notfall-Code; dieselben Regeln
    wie Abschalten — Replay-Schutz, 5 Fehlversuche -> Sperre, 10/min) wird
    ein neues Geheimnis NEBEN dem aktiven abgelegt (pending_secret +
    pending_wechsel) und als Schluessel/otpauth-Link fuer das neue Geraet
    zurueckgegeben. Das aktive Geheimnis bleibt bis dahin unveraendert
    gueltig; erst /admin/me/mfa/aktivieren mit dem Code des NEUEN Geraets
    ersetzt es (CAS). Der Wechsel verfaellt nach MFA_WECHSEL_MAX_S (15 min).
    Ein erneuter Aufruf erzeugt ein neues Pending-Geheimnis (jeder Aufruf
    verbraucht ohnehin einen frischen App-Code; der zuletzt angezeigte
    Schluessel ist der einzige, der gilt)."""
    import mfa as _mfa
    voll = await db.users.find_one({"id": admin["id"]}, {"_id": 0, "mfa": 1})
    m = (voll or {}).get("mfa") or {}
    if not m.get("aktiv"):
        raise HTTPException(409, "Die Zwei-Faktor-Anmeldung ist nicht aktiv — bitte über "
                                 "„Einrichten“ anlegen.")
    zaehler = await _mfa_app_code_bestaetigen(admin["id"], m, body.code)
    secret = _mfa.secret_erzeugen()
    jetzt = now_iso()
    # Compare-and-set wie bei den Notfall-Codes: GENAU das geprueft Geheimnis,
    # und der App-Code wird dabei atomar verbraucht.
    r = await db.users.update_one(
        {"id": admin["id"], "mfa.aktiv": True, "mfa.secret": m.get("secret"),
         "$or": [{"mfa.letzter_zaehler": {"$lt": zaehler}},
                 {"mfa.letzter_zaehler": {"$exists": False}}]},
        {"$set": {"mfa.pending_secret": _mfa.verschluesseln(secret), "mfa.pending_seit": jetzt,
                  "mfa.pending_wechsel": True, "mfa.letzter_zaehler": zaehler,
                  "mfa.fehlversuche": 0}})
    if r.matched_count == 0:
        aktuell = ((await db.users.find_one({"id": admin["id"]}, {"_id": 0, "mfa": 1}))
                   or {}).get("mfa") or {}
        if aktuell.get("aktiv") and aktuell.get("secret") == m.get("secret"):
            raise HTTPException(400, MFA_CODE_VERBRAUCHT)
        raise HTTPException(409, "Die Zwei-Faktor-Anmeldung wurde inzwischen geändert — "
                                 "bitte Seite neu laden.")
    await log_activity_sicher("", admin["id"], "admin.mfa.wechsel_begonnen")
    bis = (datetime.now(timezone.utc) + timedelta(seconds=MFA_WECHSEL_MAX_S)).isoformat()
    return {"secret": secret, "wechsel": True, "gueltig_bis": bis,
            "otpauth_uri": _mfa.provisioning_uri(
                secret, admin.get("username") or admin.get("email") or admin["id"]),
            "hinweis": "Diesen Schlüssel in die Authenticator-App auf dem NEUEN Gerät übernehmen "
                       "und den dort angezeigten Code eingeben. Bis dahin gilt der bisherige "
                       f"Schlüssel weiter; der Wechsel verfällt nach {MFA_WECHSEL_MAX_S // 60} "
                       "Minuten."}


@router.post("/admin/users/{user_id}/mfa-zuruecksetzen")
async def admin_mfa_zuruecksetzen(user_id: str, body: dict = Body(default={}),
                                  admin=Depends(current_super_admin)):
    """Nur Super-Admin: Zwei-Faktor eines AUSGESPERRTEN Admins entfernen —
    der Betroffene richtet sie danach neu ein.

    Audit 09/2026: Ein Selbstreset ist verboten — sonst koennte eine
    gestohlene Super-Admin-Sitzung den zweiten Faktor einfach abschalten.
    Wird das Konto eines ANDEREN Super-Admins zurueckgesetzt, muss der
    Handelnde sein eigenes Passwort bestaetigen und einen Grund angeben;
    der Vorgang loest zusaetzlich einen Betriebsalarm aus."""
    if user_id == admin["id"]:
        raise HTTPException(
            400, "Die eigene Zwei-Faktor-Anmeldung kann hier nicht "
                 "zurueckgesetzt werden. Zum Abschalten den Weg "
                 "Einstellungen -> Abschalten mit gueltigem Code nutzen; "
                 "bei Verlust des Geraets mit einem Wiederherstellungscode "
                 "anmelden oder auf dem Server "
                 "'python scripts/mfa_pruefen.py --konto <name> --abschalten --ja' "
                 "ausfuehren (siehe DEPLOYMENT.md).")
    u = await db.users.find_one({"id": user_id, "role": "admin"},
                                {"_id": 0, "id": 1, "email": 1, "username": 1,
                                 "is_super_admin": 1})
    if not u:
        raise HTTPException(404, "Admin-Konto nicht gefunden")
    grund = str(body.get("grund", ""))[:300].strip()
    if u.get("is_super_admin"):
        passwort = str(body.get("passwort", ""))
        eigener = await db.users.find_one({"id": admin["id"]}, {"_id": 0, "password_hash": 1})
        if not passwort or not await verify_password_async(
                passwort, (eigener or {}).get("password_hash", "")):
            # Rollenpruefung 22.09.2026 (RP-554): 400 statt 401 (sonst Abmeldung).
            raise HTTPException(400, "Zum Zuruecksetzen eines Super-Admin-Kontos "
                                     "bitte das eigene Passwort bestaetigen")
        if not grund:
            raise HTTPException(400, "Bitte einen Grund angeben (wird protokolliert)")
    await db.users.update_one({"id": user_id},
                              {"$unset": {"mfa": ""}, "$set": {"current_session_id": None}})
    await db.zugangs_aenderungen.insert_one({
        "id": str(uuid.uuid4()), "art": "mfa_zurueckgesetzt",
        "subject_user_id": user_id, "subject_email": u.get("email", ""),
        "subject_super_admin": bool(u.get("is_super_admin")),
        "alt": "zwei_faktor_aktiv", "neu": "zwei_faktor_entfernt",
        "grund": grund, "admin_id": admin["id"],
        "admin_email": _handelnder(admin), "created_at": now_iso()})
    # Kontonummer (13.09.2026): Admin-Konten werden ueber den Benutzernamen
    # benannt (die E-Mail des Super-Admins ist nur ein Platzhalter).
    kennung = u.get("username") or u.get("email") or user_id
    await log_activity_sicher("", admin["id"], "admin.mfa.zurueckgesetzt", ref=user_id,
                       meta={"konto": kennung, "grund": grund,
                             "super_admin": bool(u.get("is_super_admin"))})
    if u.get("is_super_admin"):
        from betrieb import alarm
        await alarm(db, "mfa_zurueckgesetzt", ref=kennung,
                    von=_handelnder(admin), grund=grund or "ohne Angabe")
    return {"ok": True}


@router.post("/admin/buyers/{buyer_id}/ustid-pruefen")
async def admin_buyer_ustid_pruefen(buyer_id: str, admin=Depends(current_admin)):
    """USt-IdNr. eines Zwischenhaendlers beim EU-Dienst VIES pruefen (Audit
    09/2026, Punkt 40). Ergebnis wird am Kaeufer gespeichert; Nicht-
    erreichbarkeit ist ein Ergebnis ('nicht_pruefbar'), kein Fehler."""
    from ustid import vies_pruefen
    buyer = await db.users.find_one({"id": buyer_id, "role": "b2b_buyer"},
                                    {"_id": 0, "id": 1, "ust_id": 1})
    if not buyer:
        raise HTTPException(404, "Zwischenhändler nicht gefunden")
    ergebnis = await vies_pruefen(buyer.get("ust_id") or "")
    ergebnis["geprueft_von"] = _handelnder(admin)
    await db.users.update_one({"id": buyer_id}, {"$set": {"ust_id_pruefung": ergebnis}})
    await log_activity_sicher("", admin["id"], "admin.buyer.ustid.geprueft", ref=buyer_id,
                       meta={"status": ergebnis["status"], "ust_id": ergebnis.get("ust_id")})
    return ergebnis
