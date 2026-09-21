"""Digitales Abhol-Protokoll (Fahrer-App).

Dasselbe Protokoll wie das Papier-PDF — nur ausfüllbar:
  * Abschnitt 2/3: Checklisten abhaken (Dokumente, Zubehör, Ausstattung)
  * Abschnitt 4: technischer Zustand eintippen/auswählen
  * Abschnitt 7: Bemerkungen
  * Abschnitt 8: zwei Unterschriften per Finger (Fahrer + Verkäufer)

Ablauf: Entwurf laufend speichern (`PUT .../protocol`) → abschließen
(`POST .../protocol/finalize`). Beim Abschließen entsteht ein
unveränderbares, ausgefülltes PDF im Storage, der Termin wird auf
"abgeholt" gesetzt und der Fahrzeug-Lebenszyklus nachgezogen.
Korrekturen erzeugen eine NEUE Version (alte bleibt als Beweis).
"""
import base64
import logging
import math
import hashlib
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from pymongo.errors import DuplicateKeyError

import betrieb
import protokoll_vergleich as PV
from deps import (besitzer_namen, current_chef, db, ist_sucher,
                  log_activity, log_activity_sicher, now_iso, termin_bereich,
                  termin_im_bereich)
from lifecycle import try_set_lifecycle
# Runde 17 (Nr. 10): dieselbe Schadensform wie im Kaufvertrag (contracts.py
# importiert appointments/protocols nur lazy — kein Zyklus).
from routes.contracts import DamageIn
from routes.drivers import current_driver, _zugriff_pruefen

# Haendler-/Sucher-Zugriff auf die fertigen Protokolle (Fahrzeugakte).
# current_firma = Chef UND seine Sucher; Admin/Kaeufer bleiben aussen vor.
from routes.bestand import current_firma as _dealer_dep
# Freigabe (14.09.2026, Wunsch Ahmad): "Nur der Firmenchef darf mit dem Fahrer
# vor Ort kommunizieren, Sucher nicht." Die Freigabe-Liste, der Zaehler und
# die Freigabe/Rueckgabe selbst sind Chefsache; Sucher sehen weiter nur die
# fertigen Protokolle ihrer eigenen Vorgaenge (Akte, PDF).
_chef_dep = current_chef

log = logging.getLogger("autohandel")

router = APIRouter()


# ---------- Vorlage: dieselben Punkte wie im PDF ----------
DOCUMENT_ITEMS = [
    "Fahrzeugschein / Zulassung Teil I",
    "Fahrzeugbrief / Zulassung Teil II",
    "Letzter HU-/AU-Bericht",
    "Servicebuch / Scheckheft",
    "COC-Papiere (EG-Übereinstimmung)",
    "Bedienungsanleitung",
    "Zweitsatz Reifen",
    "Ladekabel / Adapter",
    "Werkzeug / Warndreieck / Verbandskasten",
]

# Abschnitt 1 im PDF: 12 Zeilen mit "stimmt / weicht ab" (bzw. Ja/Nein/
# unbekannt). Der Fahrer kreuzt jede Zeile am Handy an und kann bei
# Abweichung den korrekten Wert eintippen.
VEHICLE_CHECK_FIELDS = PV.FELDER   # Gegenpruefung 12.09.2026: protokoll_vergleich.FELDER

CONDITION_FIELDS = [
    ("mileage", "Kilometerstand bei Abholung", "text"),
    ("fuel_level", "Tankfüllstand", ["leer", "1/4", "1/2", "3/4", "voll"]),
    ("tire_profile", "Reifenprofil VL / VR / HL / HR", "text"),
    ("driving", "Fahrverhalten (Probefahrt)", ["ok", "Mängel", "nicht gefahren"]),
    ("battery", "Batterie / Starter", ["ok", "schwach", "defekt"]),
    ("warning_lights", "Kontrollleuchten leuchten", ["nein", "ja"]),
    ("clean_inside", "Sauberkeit Innenraum", ["gut", "mittel", "schlecht"]),
    ("clean_outside", "Sauberkeit Außen", ["gut", "mittel", "schlecht"]),
]


# Wunsch Ahmad 19.09.2026: ALLE Ausstattungen aus dem Inserat zeigen.
# Der Deckel von 20 schnitt bei gut ausgestatteten Wagen die Haelfte ab
# (echte Inserate haben 30-60 Zeilen). 80 ist nur noch die Grenze
# gegen Ausreisser — die Antwort des Fahrers darf ebenso viele Zeilen
# tragen (FELD_MAX unten).
AUSSTATTUNG_MAX = PV.AUSSTATTUNG_MAX
# Felder je Abschnitt in der Antwort des Fahrers (Ausstattung + Reserve).
FELD_MAX = AUSSTATTUNG_MAX + 20


class ProtocolIn(BaseModel):
    """Alle Felder optional — der Fahrer speichert laufend Zwischenstände."""
    vehicle_check: Optional[Dict[str, Any]] = None      # Abschnitt 1 (Korrekturen)

        # Runde 17 (Nr. 9): auch documents/features deckeln (vorher unbegrenzt).
    @field_validator("vehicle_check", "condition", "documents", "features", mode="before")
    @classmethod
    def _dict_deckeln(cls, v):
        """Review 09/2026: freie Dicts hatten keine Groessengrenze — Werte
        als Text bis 500 Zeichen (Zahlen/Bool/None ok).

        19.09.2026: Die Grenze lag bei 60 Schluesseln. Seit alle
        Ausstattungen des Inserats angezeigt werden (AUSSTATTUNG_MAX),
        kann allein Abschnitt 3 mehr Zeilen haben — deshalb FELD_MAX."""
        if v is None:
            return v
        if not isinstance(v, dict) or len(v) > FELD_MAX:
            raise ValueError(f"zu viele oder ungueltige Felder (max. {FELD_MAX})")
        def _wert(k, w, tiefe=0):
            if isinstance(w, str):
                return w[:500]
            # Gegenpruefung 12.09.2026: Infinity/NaN (json.loads nimmt sie an)
            # liessen spaeter die ganze Freigaben-Liste scheitern.
            if isinstance(w, float) and not math.isfinite(w):
                raise ValueError(f"Feld {k}: ungültige Zahl")
            if isinstance(w, (int, float, bool)) or w is None:
                return w
            # Verschachtelte Eintraege wie {"make": {"status": "stimmt", "value": ...}}
            if isinstance(w, dict) and tiefe == 0 and len(w) <= 20:
                return {str(kk)[:80]: _wert(kk, ww, 1) for kk, ww in w.items()}
            if isinstance(w, list) and tiefe == 0 and len(w) <= 50:
                return [_wert(k, x, 1) for x in w]
            raise ValueError(f"Feld {k}: nur Text, Zahl oder Ja/Nein")
        out = {}
        for k, w in v.items():
            if not isinstance(k, str) or len(k) > 80:
                raise ValueError("ungueltiger Feldname")
            out[k] = _wert(k, w)
        return out
    documents: Optional[Dict[str, bool]] = None         # Abschnitt 2
    keys_count: Optional[str] = Field(default=None, max_length=20)
    keys_expected: Optional[str] = Field(default=None, max_length=20)
    features: Optional[Dict[str, bool]] = None          # Abschnitt 3
    condition: Optional[Dict[str, Any]] = None          # Abschnitt 4
    damages_confirmed: Optional[bool] = None            # Abschnitt 5
    # Abschnitt 6: neu entdeckte Schaeden, per Tipp auf die Fahrzeug-Skizze
    # markiert (gleiches Format wie die Kaufvertrag-Schaeden: view/zone/x/y/...)
    # Runde 17 (Nr. 10): typisiert wie die Vertrags-Schaeden (vorher freie
    # Dicts — ein String-Element liess pickup_pdf_service abstuerzen).
    new_damages: Optional[List[DamageIn]] = Field(default=None, max_length=40)
    notes: Optional[str] = Field(default=None, max_length=5000)   # Abschnitt 7
    place: Optional[str] = Field(default=None, max_length=200)    # Ort (Abschnitt 8)
    # Wunsch Ahmad 14.09.2026: Auch der Fahrer kann den vor Ort verhandelten
    # Preis und eine Sondervereinbarung eintragen. Der Chef sieht beides bei
    # der Freigabe; gibt er ohne eigenen Preis frei, gilt der Vorschlag.
    preis_vorschlag: Optional[float] = Field(default=None, ge=0, le=10_000_000)
    sondervereinbarung: Optional[str] = Field(default=None, max_length=2000)
    # Phase 2 (2.9, B26): Revisionsnummer des Entwurfs, wie die App ihn geladen
    # bzw. zuletzt gespeichert hat — zwei Tabs desselben Fahrers ueberschreiben
    # sich nicht mehr gegenseitig. Ohne Angabe wie bisher (aeltere App).
    revision: Optional[int] = Field(default=None, ge=0)

    @field_validator("preis_vorschlag", mode="before")
    @classmethod
    def _preis_endlich(cls, v):
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError("ungültiger Preis")
        return v


class FinalizeIn(BaseModel):
    # Runde 17 (Nr. 8): Obergrenze fuer die Base64-Unterschriften (die
    # dekodierte PNG ist auf 2 MB begrenzt; 3 Mio. Zeichen decken das ab).
    signature_driver_b64: str = Field(min_length=20, max_length=3_000_000)
    signature_seller_b64: str = Field(min_length=20, max_length=3_000_000)
    seller_name: Optional[str] = Field(default=None, max_length=200)
    place: Optional[str] = Field(default=None, max_length=200)
    # Gegenpruefung 12.09.2026 (schwerer Befund): Der Chef kann den Preis
    # aendern, waehrend vor Ort unterschrieben wird. Die App schickt
    # deshalb den Preis mit, den sie ANGEZEIGT hat — weicht er ab, wird
    # nicht abgeschlossen. Sonst haette der Verkaeufer einen anderen
    # Betrag gesehen als den, der im PDF ueber seiner Unterschrift steht.
    # None = die App kannte noch keinen Preis (Altfassung / keine Freigabe
    # mit Preis).
    neuer_preis_gesehen: Optional[float] = Field(default=None, ge=0,
                                                 le=10_000_000)
    # Gegenpruefung 12.09.2026: Nicht nur der Preis — auch der Vermerk steht
    # ueber den Unterschriften. Die App schickt den Freigabe-Stand, den sie
    # angezeigt hat; jede Aenderung des Chefs setzt einen neuen.
    # None = aeltere App (dann nur die Preispruefung).
    freigabe_stand_gesehen: Optional[str] = Field(default=None, max_length=64)


# --------------------------------------------------------------------------
# Runde 30 (12.09.2026, Wunsch Ahmad): Freigabe durch den Chef VOR den
# Unterschriften.
#
# Ablauf vor Ort:
#   1. Der Fahrer fuellt das Protokoll vollstaendig aus und schickt es ab
#      (Status "zur_freigabe") — noch OHNE Unterschriften.
#   2. Der Chef sieht das ausgefuellte Protokoll: was weicht ab, welche
#      neuen Schaeden hat der Fahrer gefunden. Er kann den Verkaeufer
#      anrufen und nachverhandeln.
#   3. Der Chef gibt frei — mit dem NEUEN Preis, falls verhandelt wurde
#      (Status "freigegeben"). Oder er schickt das Protokoll zurueck, wenn
#      der Fahrer etwas nachtragen soll (zurueck auf "entwurf").
#   4. Erst dann unterschreiben Verkaeufer und Fahrer vor Ort, und das
#      Protokoll wird endgueltig (Status "final") — mit dem neuen Preis.
ZUR_FREIGABE = "zur_freigabe"
FREIGEGEBEN = "freigegeben"
# In diesen Staenden darf der Fahrer nichts mehr aendern (sonst saehe der
# Chef etwas anderes, als am Ende unterschrieben wird).
GESPERRT_FUER_FAHRER = (ZUR_FREIGABE, FREIGEGEBEN)


class FreigabeIn(BaseModel):
    """Freigabe des Chefs. `neuer_preis` nur, wenn nachverhandelt wurde."""
    # Pruefung 14.09.2026 (A16): kein 0-Euro-Preis bei der Freigabe
    neuer_preis: Optional[float] = Field(default=None, gt=0, le=10_000_000)
    notiz: Optional[str] = Field(default=None, max_length=2000)
    # True = zurueck an den Fahrer (er soll etwas nachtragen/korrigieren).
    zurueck: bool = False
    # Runde 33 (Gegenpruefung 12.09.2026): Den Stand mitschicken, den der
    # Bearbeiter gesehen hat (updated_at aus der Liste). Geben Chef und Sucher
    # gleichzeitig mit verschiedenen Preisen frei, gewann vorher stillschweigend
    # der Letzte. Jetzt lehnt der Server ab und sagt, wer schon freigegeben hat.
    # Ohne Stand (aeltere Oberflaeche im Rollout) bleibt es beim bisherigen Weg.
    stand: Optional[str] = Field(default=None, max_length=64)
    # Verhandelten Preis entfernen — es gilt wieder der Vertragspreis. Vorher
    # liess sich ein einmal eingetragener Preis nicht mehr loeschen.
    preis_zuruecksetzen: bool = False


def unterschrift_hat_tinte(raw: bytes, mindest_pixel: int = 50) -> bool:
    """Ist auf dem Unterschriftsbild ueberhaupt etwas gezeichnet?

    Pruefbericht 20.09.2026 (U-156/F21): Ein leeres Feld galt als
    unterschrieben. Die App schickte bei jedem Loslassen ein Bild — auch ohne
    einen einzigen Strich —, und ein weisses PNG ist groesser als jede
    Mindestlaenge. Das Fahrzeug galt dann als abgeholt, das rechtsverbindliche
    Protokoll trug eine leere Unterschrift, unwiderruflich.

    Gezaehlt werden dunkle Pixel (auf Weiss gelegt, Transparenz = weiss).
    Mindestens `mindest_pixel`, bei winzigen Bildern mindestens eines.
    Nicht lesbar -> False (die Lesbarkeit prueft vorher bild_lesbar_pruefen)."""
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as bild:
            bild.load()
            rgba = bild.convert("RGBA")
        grund = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        grund.alpha_composite(rgba)
        grau = grund.convert("L")
        dunkel = sum(grau.histogram()[:160])
        gesamt = grau.size[0] * grau.size[1]
    except Exception:  # noqa: BLE001 — unlesbar = keine Tinte
        return False
    schwelle = max(1, min(mindest_pixel, int(gesamt * 0.0002)))
    return dunkel >= schwelle


def _vehicle_check_values(vehicle: dict, contract: dict) -> Dict[str, str]:
    """Soll-Werte fuer Abschnitt 1 — dieselben wie im Protokoll-PDF und im
    Freigabe-Kasten.

    Runde 33 (Analyse 12.09.2026): Das waren bisher die INSERATSWERTE.
    Korrigierte der Haendler im Vertrags-Dialog z.B. die Erstzulassung, sah
    der Fahrer vor Ort weiter den alten Wert. Jetzt gelten die Angaben aus
    dem Vertrag (protokoll_vergleich.vertragswerte)."""
    return PV.werte_als_text(PV.vertragswerte(vehicle, contract))


async def _fahrzeug_und_vertrag(appt: dict):
    """Fahrzeugdaten (data) und Vertragsdaten (contract_data) des Termins —
    beide ueber dealer_id (IDs sind nur MIT Firma eindeutig)."""
    vehicle: Dict[str, Any] = {}
    if appt.get("vehicle_id"):
        v = await db.vehicles.find_one(
            {"id": appt["vehicle_id"], "dealer_id": appt.get("dealer_id")}, {"_id": 0}) or {}
        vehicle = dict(v.get("data") or {})
    contract: Dict[str, Any] = {}
    if appt.get("contract_id"):
        c = await db.generated_pdfs.find_one(
            {"id": appt["contract_id"], "dealer_id": appt.get("dealer_id")},
            {"_id": 0, "contract_data": 1}) or {}
        contract = dict(c.get("contract_data") or {})
    return vehicle, contract


def vertragswerte_stand(vehicle: dict, contract: dict) -> str:
    """Pruefung 14.09.2026 (P1): Fingerabdruck des Standes, den der Chef
    freigibt — Soll-Werte aus Abschnitt 1, Vertragspreis, Schaeden. Aendert
    sich davon etwas nach der Freigabe, ist die Freigabe hinfaellig."""
    import json
    daten = {"werte": PV.werte_als_text(PV.vertragswerte(vehicle, contract)),
             "preis": contract.get("purchase_price"),
             "schaeden": contract.get("damages") or vehicle.get("damages") or []}
    return hashlib.sha256(json.dumps(daten, sort_keys=True, default=str,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


async def _appt_or_404(appt_id: str, driver: dict) -> dict:
    appt = await db.appointments.find_one(
        {"id": appt_id, "driver_id": driver["id"]}, {"_id": 0})
    if not appt:
        raise HTTPException(404, "Termin nicht gefunden")
    # Zuweisung allein reicht nicht: der Fahrer muss bei dieser Firma noch
    # in der Fahrerliste stehen (Pruefbericht 09/2026, routes.drivers).
    await _zugriff_pruefen(appt, driver)
    # Pruefung 14.09.2026 (C22): ... und die Fahrt angenommen haben. Vorher
    # konnte ein Fahrer, der die Fahrt noch nicht (oder nicht mehr) angenommen
    # hatte, Protokoll, Verkaeuferdaten und PDF laden und sogar abschliessen.
    zuteilung_offen_oder_409(appt)
    # Pruefbericht 20.09.2026 (Nr. 1): Die Sichtfrist (14 Tage nach der
    # Abholung, 30 nach anderem Abschluss) hing bisher NUR an den einzelnen
    # Dokumentrouten in drivers.py. Die Protokollwege hier — Ansicht, PDF,
    # Speichern, Einreichen, Korrektur, Abschluss — kannten sie nicht. Wer
    # sich die Adresse gemerkt hatte, kam Monate spaeter noch an das
    # unterschriebene Abholprotokoll mit Verkaeuferdaten und Unterschrift,
    # obwohl die Fahrt laengst aus seiner App verschwunden war.
    #
    # Zentral hier, damit JEDER Weg ueber _appt_or_404 sie automatisch
    # bekommt — auch jeder kuenftige. Offene Fahrten sind nicht betroffen:
    # die Frist gilt nur fuer abgeschlossene bzw. stornierte.
    from routes.drivers import unterlagen_zugriff_oder_404
    unterlagen_zugriff_oder_404(appt)
    return appt


ZUTEILUNG_OHNE_ZUGRIFF = ("offen", "abgelehnt")


def zuteilung_offen_oder_409(appt: dict) -> None:
    """Pruefung 14.09.2026 (C22): Nur eine ANGENOMMENE Fahrt gibt Zugriff auf
    Protokoll und Dokumente. Fehlende Zuteilung (nur per Datenbankeingriff)
    setzt der Aufraeumlauf auf "offen" (Runde 13); bis dahin wie Altbestand."""
    if (appt.get("zuteilung") or "angenommen") in ZUTEILUNG_OHNE_ZUGRIFF:
        raise HTTPException(409, "Bitte zuerst die Fahrt annehmen — erst dann sind "
                                 "Protokoll und Dokumente zugänglich.")


async def _dateien_verwerfen(keys: List[str], dealer_id: str) -> None:
    """Beim Abbruch eines Abschlusses bereits geschriebene Dateien
    (Unterschrift-PNGs, PDF) wieder entfernen — best effort. Schlaegt das
    Loeschen fehl, landet der Key in storage_delete_retry, damit der
    Aufraeum-Job die Loeschung nachholt statt die Datei verwaist liegen
    zu lassen (Pruefbericht 09/2026)."""
    from storage_service import delete_async
    for key in list(keys):
        try:
            await delete_async(key)
        except Exception as exc:  # noqa: BLE001
            log.warning("Protokoll-Rollback: %s konnte nicht geloescht werden (%s)",
                        key, exc)
            try:
                jetzt = now_iso()
                await db.storage_delete_retry.insert_one({
                    "id": str(uuid.uuid4()), "art": "key", "key": key,
                    "grund": "protokoll-rollback", "dealer_id": dealer_id,
                    "versuche": 0, "letzter_fehler": str(exc)[:300],
                    "created_at": jetzt, "updated_at": jetzt,
                })
            except Exception:  # noqa: BLE001
                log.exception("storage_delete_retry: Eintrag fuer %s "
                              "fehlgeschlagen", key)


async def _current(appt_id: str) -> Optional[dict]:
    doc = await db.pickup_protocols.find_one(
        {"appointment_id": appt_id, "superseded": {"$ne": True}}, {"_id": 0},
        sort=[("version", -1)])
    if doc is None:
        doc = await ohne_aktuelle_version_reparieren(appt_id)
    return doc


# Pruefung 14.09.2026 (C17/C18): Reparatur erst, wenn die Abloesung nicht
# mehr frisch ist — eine gerade laufende Korrektur (Abloesen -> Insert) oder
# ein laufendes Verwerfen (zwei Writes) darf nicht dazwischen "repariert" werden.
_REPARATUR_KARENZ_SEKUNDEN = 120


async def ohne_aktuelle_version_reparieren(appt_id: str, dbx=None) -> Optional[dict]:
    """Pruefung 14.09.2026 (C17/C18): Hat ein Termin NUR abgeloeste Versionen
    (zweiter Write von korrektur_verwerfen gescheitert, Prozess nach dem
    Abloesen in start_correction gestorben), gab es vorher keinen Weg zurueck:
    der Fahrer sah "kein Protokoll", ein neuer Entwurf scheiterte am Unique-
    Index (appointment_id, version) mit 500, das unterschriebene PDF war fuer
    den Haendler unerreichbar. Jetzt wird die massgebliche Version wieder
    aktuell geschaltet: die hoechste FINALE, sonst die hoechste Version.
    Best effort, wirft nie; liefert die reaktivierte Version oder None."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    dbx = dbx if dbx is not None else db
    try:
        # Pruefung 14.09.2026 (Nr. 6): nicht die 50 hoechsten Versionen laden
        # und darin suchen (eine aeltere finale Version fiel bei mehr als 50
        # Entwuerfen darueber heraus), sondern gezielt fragen: hoechste FINALE
        # Version, sonst die hoechste Version ueberhaupt.
        kandidat = await dbx.pickup_protocols.find_one(
            {"appointment_id": appt_id, "superseded": True, "status": "final"},
            {"_id": 0}, sort=[("version", -1)])
        if kandidat is None:
            kandidat = await dbx.pickup_protocols.find_one(
                {"appointment_id": appt_id, "superseded": True}, {"_id": 0},
                sort=[("version", -1)])
        if kandidat is None:
            return None
        grenze = (_dt.now(_tz.utc) - _td(seconds=_REPARATUR_KARENZ_SEKUNDEN)).isoformat()
        stempel = kandidat.get("superseded_at") or kandidat.get("verworfen_am") or ""
        if stempel and stempel > grenze:
            return None
        jetzt = now_iso()
        res = await dbx.pickup_protocols.update_one(
            {"id": kandidat["id"], "superseded": True},
            {"$set": {"superseded": False, "updated_at": jetzt, "repariert_am": jetzt},
             "$unset": {"superseded_at": "", "verworfen_am": ""}})
        if not res.matched_count:
            return None
        log.warning("Protokoll zu Termin %s hatte keine aktuelle Version — Version %s "
                    "(%s) wieder aktiv geschaltet", appt_id, kandidat.get("version"),
                    kandidat.get("status"))
        return await dbx.pickup_protocols.find_one({"id": kandidat["id"]}, {"_id": 0})
    except Exception:  # noqa: BLE001
        log.exception("Protokoll-Reparatur zu Termin %s fehlgeschlagen", appt_id)
        return None


# Runde 17 (Nr. 7): Termin-Zustaende, in denen der Protokoll-Abschluss den
# Termin NICHT mehr auf "abgeholt" setzen darf (Haendler hat ihn inzwischen
# geschlossen). Dieselbe Menge wie die Vorabpruefung im Finalize.
_TERMIN_GESCHLOSSEN = ["storniert", "nicht abgeholt", "erledigt"]
TERMIN_GESCHLOSSEN_HINWEIS = ("Protokoll gespeichert — der Termin wurde inzwischen "
                              "vom Händler geschlossen oder der Fahrer entfernt.")
# Pruefung 14.09.2026 (F5): Termin wurde nach dem Abschluss wieder geoeffnet.
TERMIN_WIEDER_OFFEN_HINWEIS = ("Der Händler hat den Termin nach dem Abschluss wieder "
                               "geöffnet — bitte eine Korrektur-Version starten.")
# Pruefung 14.09.2026 (P1): Vertrag oder Fahrzeugdaten wurden nach der Freigabe geaendert.
VERTRAG_GEAENDERT_HINWEIS = ("Vertrag oder Fahrzeugdaten wurden nach der Freigabe geändert — "
                             "das Protokoll geht zurück in den Entwurf. Bitte erneut zur "
                             "Freigabe schicken.")
# Go-Live 13.09.2026 (P3): Termin haengt inzwischen an einem anderen Vertrag.
TERMIN_UMGEHAENGT_HINWEIS = ("Protokoll gespeichert — der Termin wurde inzwischen einem "
                             "anderen Vertrag zugeordnet. Bitte den Händler kontaktieren.")
WIRD_ABGESCHLOSSEN = ("Das Protokoll wird gerade abgeschlossen — bitte einen Moment "
                      "warten und neu laden.")
ABSCHLUSS_UEBERHOLT = ("Der Abschluss wurde inzwischen geändert oder neu gestartet — "
                       "bitte die Seite neu laden.")
TERMIN_GEAENDERT = ("Der Termin wurde inzwischen geändert (anderer Vertrag oder anderes "
                    "Fahrzeug) — bitte die Seite neu laden.")

# Go-Live 13.09.2026 (P3): Diese Verweise des Termins bestimmen, WELCHER
# Vertrag/Vorgang abgeschlossen wird.
_TERMIN_ZEIGER = ("contract_id", "vehicle_id")


def _termin_umgehaengt(vorher: dict, jetzt: dict, felder=_TERMIN_ZEIGER) -> bool:
    """Go-Live 13.09.2026 (P3): Zeigt der Termin JETZT auf einen anderen
    Vertrag/ein anderes Fahrzeug als beim Start des Abschlusses? Ein
    geleerter Verweis zaehlt nicht (Vertragsloeschung setzt contract_id=None,
    der Vorgang des Abschlusses bleibt dabei richtig)."""
    return any((jetzt.get(f) or None) not in ((vorher.get(f) or None), None) for f in felder)


async def _termin_abgeholt_setzen(appt_id: str, driver_id: str,
                                  setzen: Dict[str, Any],
                                  erwartet: Optional[Dict[str, Any]] = None) -> bool:
    """Runde 17 (Nr. 7): Termin-Write als Compare-and-Set — nur, wenn der
    Termin noch diesem Fahrer gehoert und nicht inzwischen geschlossen
    wurde. Vorher machte der Abschluss auch einen zwischenzeitlich
    stornierten Termin (oder den eines entfernten Fahrers) zu "abgeholt".
    Liefert False (mit Betriebsalarm), wenn der Termin nicht mehr passt.

    Go-Live 13.09.2026 (P3): mit `erwartet` zusaetzlich nur, wenn Vertrag und
    Fahrzeug noch dieselben sind (oder geleert) — sonst wuerde der Termin
    eines anderen Vertrags mit dem PDF dieses Abschlusses "abgeholt"."""
    filt: Dict[str, Any] = {"id": appt_id, "driver_id": driver_id,
                            "status": {"$nin": _TERMIN_GESCHLOSSEN}}
    if erwartet is not None:
        for f in _TERMIN_ZEIGER:
            filt[f] = {"$in": ([erwartet[f]] if erwartet.get(f) else []) + [None, ""]}
    res = await db.appointments.update_one(filt, {"$set": setzen})
    if res.matched_count == 0:
        typ = "protokoll_final_termin_geschlossen"
        if erwartet is not None:
            akt = await db.appointments.find_one(
                {"id": appt_id, "driver_id": driver_id, "status": {"$nin": _TERMIN_GESCHLOSSEN}},
                {"_id": 0, "contract_id": 1, "vehicle_id": 1})
            if akt and _termin_umgehaengt(erwartet, akt):
                typ = "protokoll_final_termin_umgehaengt"
        await betrieb.alarm(db, typ, ref=appt_id,
                            driver_id=driver_id,
                            protocol_id=setzen.get("protocol_id"))
        return False
    # Nachpruefung Runde 14 (Befund 114): Aufraeum-Frist ab dem ERSTEN
    # Abschluss — nur setzen, wenn noch leer.
    await db.appointments.update_one(
        {"id": appt_id, "abgeschlossen_seit": {"$in": [None, ""]}},
        {"$set": {"abgeschlossen_seit": now_iso()}})
    return True


async def korrektur_verwerfen(appt_id: str) -> bool:
    """Runde 17 (Nr. 11): Schliesst der Haendler den Termin (storniert /
    nicht abgeholt / erledigt), waehrend eine Korrektur-Version des
    Protokolls als Entwurf offen ist, bleibt der Termin sonst OHNE
    massgebliches Protokoll: die korrigierte Version ist bereits abgeloest
    (superseded), der Entwurf wird nie fertig. Deshalb: Entwurf verwerfen,
    DANACH die korrigierte Version wieder als aktuell schalten (Reihenfolge
    wegen des Unique-Index 'ein aktuelles Protokoll je Termin').
    Best effort — wirft nie; True, wenn ein Entwurf verworfen wurde.

    Gegenpruefung 12.09.2026 (schwerer Befund): Seit Runde 30 kann eine
    Korrektur auch beim Chef liegen (zur_freigabe) oder freigegeben sein.
    Wurde nur nach 'entwurf' gesucht, blieb der Termin nach dem Schliessen
    OHNE massgebliches Protokoll zurueck — das unterschriebene PDF der
    Vorversion war nicht mehr erreichbar."""
    OFFENE_KORREKTUR = ["entwurf", ZUR_FREIGABE, FREIGEGEBEN]
    try:
        entwurf = await db.pickup_protocols.find_one(
            {"appointment_id": appt_id, "status": {"$in": OFFENE_KORREKTUR},
             "corrects_version": {"$exists": True}, "superseded": {"$ne": True}},
            {"_id": 0, "id": 1, "corrects_version": 1})
        if not entwurf:
            return False
        jetzt = now_iso()

        # Runde 12 (15.09.2026, Nr. 24): beide Schritte in EINER Transaktion,
        # wenn die Datenbank ein Replica-Set ist — kein Fenster ohne aktuelles
        # Protokoll mehr; sonst nacheinander (Reparatur: ohne_aktuelle_version_reparieren).
        async def _beide(session=None) -> bool:
            ses = {"session": session} if session is not None else {}
            res = await db.pickup_protocols.update_one(
                {"id": entwurf["id"], "status": {"$in": OFFENE_KORREKTUR}},
                {"$set": {"superseded": True, "verworfen_am": jetzt, "updated_at": jetzt}}, **ses)
            if res.matched_count == 0:
                return False
            r2 = await db.pickup_protocols.update_one(
                {"appointment_id": appt_id, "version": entwurf["corrects_version"]},
                {"$set": {"superseded": False, "updated_at": jetzt},
                 "$unset": {"superseded_at": ""}}, **ses)
            if r2.matched_count == 0:
                # Befund 55 (16.09.2026): die korrigierte Version gibt es nicht
                # (mehr) — der Termin bliebe OHNE aktuelles Protokoll. In der
                # Transaktion bricht der Fehler sie ab; ohne Transaktion wird
                # das Verwerfen zurueckgenommen. Nie einen kaputten Zwischen-
                # stand festschreiben.
                if session is not None:
                    raise RuntimeError("Protokoll-Korrektur: Vorversion "
                                       f"{entwurf['corrects_version']} zu Termin {appt_id} fehlt")
                await db.pickup_protocols.update_one(
                    {"id": entwurf["id"]},
                    {"$set": {"superseded": False}, "$unset": {"verworfen_am": ""}})
                log.error("Protokoll-Korrektur zu Termin %s nicht verworfen: Vorversion %s fehlt",
                          appt_id, entwurf["corrects_version"])
                return False
            return True

        from deps import transaktion
        return await transaktion(_beide)
    except Exception:  # noqa: BLE001
        log.exception("Protokoll-Korrektur zu Termin %s konnte nicht verworfen "
                      "werden", appt_id)
        return False


# Go-Live 13.09.2026 (P6): Rueckfrage, wenn der Termin geschlossen wurde.
TERMIN_GESCHLOSSEN_RUECKFRAGE = ("Der Termin wurde geschlossen — die Freigabe gilt nicht mehr. "
                                 "Nach dem Wiederöffnen bitte erneut zur Freigabe schicken.")


# Go-Live 13.09.2026 (P6-Nachbesserung): Merker an einem LAUFENDEN Abschluss
# (wird_abgeschlossen), dessen Termin inzwischen geschlossen oder wieder-
# geoeffnet wurde. Der Abschluss selbst darf weiterlaufen; laeuft sein Claim
# aber ab oder scheitert er (Rollback), gilt die alte Freigabe nicht mehr.
TERMIN_GESCHLOSSEN_MERKER = "termin_geschlossen_im_abschluss"


def _zuruecknehmen_aenderung(user_id: Optional[str]) -> Dict[str, Any]:
    """Go-Live 13.09.2026 (P6): zurueck in den Entwurf — mit Rueckfrage und
    NEUEM Freigabe-Stand; Claim und Merker (P6-Nachbesserung) fallen weg."""
    jetzt = now_iso()
    return {"$set": {"status": "entwurf", "rueckfrage": TERMIN_GESCHLOSSEN_RUECKFRAGE,
                     "rueckfrage_am": jetzt, "rueckfrage_von": user_id,
                     "updated_at": jetzt, "freigabe_stand": jetzt},
            "$unset": {"freigegeben_am": "", "freigegeben_von": "", "claim_bis": "",
                       "claim_token": "", TERMIN_GESCHLOSSEN_MERKER: ""}}


async def _freigabe_zuruecknehmen(bedingung: Dict[str, Any], user_id: Optional[str],
                                  session=None) -> bool:
    """Go-Live 13.09.2026 (P6): Protokoll aus zur_freigabe/freigegeben zurueck
    in den Entwurf — mit Rueckfrage und NEUEM Freigabe-Stand, damit weder eine
    alte Freigabe noch ein alter Stand auf dem Handy weiter gilt. Ein laufender
    Abschluss (wird_abgeschlossen) wird nie angefasst."""
    res = await db.pickup_protocols.update_one(
        {"status": {"$in": [ZUR_FREIGABE, FREIGEGEBEN]}, **bedingung},
        _zuruecknehmen_aenderung(user_id),
        **({"session": session} if session is not None else {}))
    return bool(res.matched_count)


async def freigabe_beim_schliessen_zuruecknehmen(appt_id: str,
                                                user_id: Optional[str] = None,
                                                session=None) -> Optional[bool]:
    """Go-Live 13.09.2026 (P6): Schliesst der Haendler den Termin (storniert /
    nicht abgeholt / erledigt), waehrend das aktuelle Protokoll beim Chef liegt
    oder freigegeben ist, lebte diese Freigabe nach dem Wiederoeffnen einfach
    wieder auf. Jetzt: zurueck in den Entwurf (neuer Stand), der Fahrer schickt
    neu ab. Korrektur-Versionen verwirft vorher korrektur_verwerfen.
    Best effort — wirft nie; True, wenn zurueckgenommen wurde.

    P6-Nachbesserung: auch beim WIEDEROEFFNEN aufgerufen (der Fahrer setzt
    "nicht abgeholt" selbst, drivers.py nimmt nichts zurueck). Ein laufender
    Abschluss bekommt den Merker — laeuft sein Claim spaeter ab oder scheitert
    er, geht das Protokoll in den Entwurf statt zurueck auf freigegeben.
    Merker ZUERST: ein Claim, der dazwischen ablaeuft, landet sonst auf
    freigegeben und wird im zweiten Schritt zurueckgenommen.

    Pruefung 14.09.2026 (C19): Rueckgabe None = Fehler (der Aufrufer setzt
    einen Betriebsalarm; cleanup_service holt die Ruecknahme ueber
    freigaben_geschlossener_termine_zuruecknehmen nach). False = nichts zu tun."""
    bedingung = {"appointment_id": appt_id, "superseded": {"$ne": True},
                 "corrects_version": {"$exists": False}}
    # Befund 54 (16.09.2026): mit `session` laeuft die Ruecknahme in derselben
    # Transaktion wie der Termin-Write des Wiederoeffnens — scheitert dessen
    # Stand-Pruefung (409), bleibt die Freigabe erhalten.
    ses = {"session": session} if session is not None else {}
    try:
        await db.pickup_protocols.update_one(
            {**bedingung, "status": "wird_abgeschlossen"},
            {"$set": {TERMIN_GESCHLOSSEN_MERKER: True}}, **ses)
        return await _freigabe_zuruecknehmen(bedingung, user_id, session=session)
    except Exception:  # noqa: BLE001
        log.exception("Freigabe des Protokolls zu Termin %s konnte beim Schliessen "
                      "nicht zurueckgenommen werden", appt_id)
        return None


async def protokoll_korrekturen(appt: dict, protokoll: dict) -> tuple:
    """Was der Fahrer vor Ort anders vorgefunden hat (19.09.2026).

    Liefert (Vertragsfelder, neue Schaeden) fuer die neue Vertragsfassung —
    Wunsch Ahmad: "alle Daten, die jetzt neu sind, anstelle der alten
    falschen Daten". Wirft nie; im Zweifel bleibt der Vertrag wie er ist."""
    try:
        vehicle, contract = await _fahrzeug_und_vertrag(appt)
        zeilen = PV.vergleich(VEHICLE_CHECK_FIELDS, protokoll.get("vehicle_check"),
                              protokoll.get("condition"),
                              PV.vertragswerte(vehicle, contract))
        korrekturen = PV.vertrags_korrekturen(zeilen)
        schaeden = [d for d in (protokoll.get("new_damages") or []) if d]
        return korrekturen, schaeden
    except Exception:  # noqa: BLE001
        log.exception("Protokoll-Korrekturen zu Termin %s nicht ermittelt", appt.get("id"))
        return {}, []


async def vertrag_nach_abholung_aktualisieren(appt: dict, protokoll_id: str,
                                              neuer_preis, sondervereinbarung,
                                              korrekturen=None, neue_schaeden=None) -> bool:
    """Wunsch Ahmad 14.09.2026: nach dem unterschriebenen Protokoll den Vertrag
    mit neuem Preis und Sondervereinbarung neu erzeugen. 19.09.2026: dazu die
    vor Ort korrigierten Fahrzeugdaten und die neu aufgenommenen Schaeden.
    Wirft nie."""
    if not appt.get("contract_id") or (neuer_preis is None
                                       and not (sondervereinbarung or "").strip()
                                       and not korrekturen and not neue_schaeden):
        return False
    try:
        from routes.contracts import regenerate_contract_for_pickup
        # Pruefbericht 20.09.2026 (V-25): idempotent. Traegt der Vertrag diese
        # Abholung schon (Merker der Neuerzeugung), ist nichts mehr zu tun —
        # vorher lieferte die Neuerzeugung dann "keine Aenderung" = False, und
        # jeder Doppeltipp bzw. jeder Nachholer legte einen Alarm an, der nie
        # wieder zuging.
        stand = await db.generated_pdfs.find_one(
            {"id": appt["contract_id"]}, {"_id": 0, "nach_abholung_protokoll_id": 1})
        if stand and stand.get("nach_abholung_protokoll_id") == protokoll_id:
            return True
        ok = False
        erg: Dict[str, Any] = {}
        # Verliert der Compare-and-Set gegen eine parallele Neuerzeugung
        # (z. B. Termin verschoben), fehlten Preis/Schaeden in deren Fassung —
        # also mit dem neuen Stand erneut versuchen (hoechstens dreimal).
        for _versuch in range(3):
            erg = {}
            ok = await regenerate_contract_for_pickup(
                contract_id=appt["contract_id"], dealer_id=appt.get("dealer_id", ""),
                user={"id": appt.get("created_by"), "dealer_id": appt.get("dealer_id", ""),
                      "role": "dealer"},
                neuer_preis=neuer_preis, sondervereinbarung=sondervereinbarung,
                korrekturen=korrekturen, neue_schaeden=neue_schaeden,
                grund="abholung_abgeschlossen", protokoll_id=protokoll_id,
                ergebnis=erg)
            if ok or erg.get("grund") != "cas_verloren":
                break
        if not ok and erg.get("grund") in ("keine_aenderung", "nicht_gefunden"):
            # Der Vertrag zeigt schon alles (bzw. ist geloescht / in Loeschung):
            # kein Fehler, kein Alarm.
            return True
        if ok:
            await log_activity_sicher(appt.get("dealer_id", ""), appt.get("created_by") or "",
                                      "vertrag.nach_abholung_aktualisiert",
                                      ref=appt["contract_id"],
                                      meta={"protokoll_id": protokoll_id, "neuer_preis": neuer_preis})
            return True
        # Nachpruefung 20.09.2026, Nr. 79: Scheitert das Erzeugen der neuen
        # Vertrags-PDF, faengt regenerate_contract_for_pickup() die Ausnahme
        # selbst ab und liefert nur False. Hier wurde der Alarm aber NUR bei
        # einer durchgereichten Ausnahme gelegt — ein False rutschte still
        # durch. Ergebnis: unterschriebenes Protokoll, abgeholtes Fahrzeug,
        # und dauerhaft die ALTE Vertragsfassung, ohne dass es jemand merkt
        # und ohne Nachholversuch. Jetzt zaehlt False genauso.
        log.error("Vertrag %s nach Abholung nicht aktualisiert (Neuerzeugung "
                  "lieferte False)", appt.get("contract_id"))
        await betrieb.alarm(db, "vertrag_nach_abholung_offen", ref=str(appt.get("contract_id")),
                            protokoll_id=protokoll_id,
                            fehler="Neuerzeugung der Vertrags-PDF fehlgeschlagen")
        return False
    except Exception as exc:  # noqa: BLE001
        log.exception("Vertrag %s nach Abholung nicht aktualisiert", appt.get("contract_id"))
        await betrieb.alarm(db, "vertrag_nach_abholung_offen", ref=str(appt.get("contract_id")),
                            protokoll_id=protokoll_id, fehler=str(exc)[:300])
        return False


async def vertrag_nach_abholung_sicherstellen(appt: dict, protokoll: dict) -> bool:
    """Pruefbericht 20.09.2026 (V-25): Steht der Vertrag auf dem Stand dieses
    finalen Protokolls? Fuer den Aufraeumjob: starb der Prozess zwischen dem
    finalen Protokoll und dem Alarm, loeschte termin_nacharbeit_nachholen den
    Merker, OHNE den Vertrag neu zu erzeugen. True = erledigt oder nichts zu
    tun; False = gescheitert (der Alarm steht dann). Wirft nie."""
    try:
        if not appt.get("contract_id") or not protokoll \
                or protokoll.get("status") != "final" or protokoll.get("superseded"):
            return True
        if "contract_id" in protokoll and \
                (protokoll.get("contract_id") or None) != (appt.get("contract_id") or None):
            # der Termin haengt inzwischen an einem anderen Vertrag — nicht anfassen
            return True
        korrekturen, neue_schaeden = await protokoll_korrekturen(appt, protokoll)
        if protokoll.get("neuer_preis") is None \
                and not (protokoll.get("sondervereinbarung") or "").strip() \
                and not korrekturen and not neue_schaeden:
            return True
        return await vertrag_nach_abholung_aktualisieren(
            appt, protokoll["id"], protokoll.get("neuer_preis"),
            protokoll.get("sondervereinbarung"),
            korrekturen=korrekturen, neue_schaeden=neue_schaeden)
    except Exception:  # noqa: BLE001
        log.exception("Vertragsstand nach Abholung zu Termin %s nicht geprueft", appt.get("id"))
        return False


async def auto_daten_vor_ort_nachtragen(appt: dict, protokoll: dict, neuer_preis) -> bool:
    """Wunsch Ahmad 15.09.2026: nach dem Abschluss der Abholung den vor Ort
    nachverhandelten Preis und die vom Fahrer festgehaltenen Maengel in den
    anonymen Auto-Datensatz schreiben (eigene Spalten; der urspruengliche
    Einkaufspreis bleibt stehen). Best effort, wirft nie."""
    if not appt.get("contract_id"):
        return False
    try:
        import auto_daten
        # Befund 53 (16.09.2026): "keine Angabe" (Feld fehlt) ist etwas anderes
        # als "bewusst keine Maengel" (leere Liste) — nur eine vorhandene Liste
        # ueberschreibt die Maengel vor Ort im dauerhaften Datensatz.
        return await auto_daten.vor_ort_nachtragen(
            db, appt["contract_id"], appt.get("dealer_id", ""),
            preis=neuer_preis, maengel=protokoll.get("new_damages"))
    except Exception:  # noqa: BLE001
        log.exception("Auto-Daten vor Ort fuer Vertrag %s nicht nachgetragen",
                      appt.get("contract_id"))
        return False


async def entwurf_bei_terminaenderung_verwerfen(appt_id: str) -> bool:
    """Pruefung 14.09.2026 (Liste 4, Nr. 1/2): Wechselt am Termin das Fahrzeug,
    der Vertrag oder der Fahrer, waehrend das Protokoll noch ein Entwurf ist,
    passen die bisherigen Angaben (Fahrzeugdaten, Schaeden, Zustand) nicht mehr
    — der Entwurf wird verworfen. Ein Korrektur-Entwurf wird verworfen und die
    korrigierte Version wieder aktuell (korrektur_verwerfen). Best effort."""
    try:
        doc = await db.pickup_protocols.find_one(
            {"appointment_id": appt_id, "superseded": {"$ne": True}, "status": "entwurf"},
            {"_id": 0, "id": 1, "corrects_version": 1})
        if not doc:
            return False
        if "corrects_version" in doc:
            return await korrektur_verwerfen(appt_id)
        res = await db.pickup_protocols.delete_one({"id": doc["id"], "status": "entwurf"})
        return bool(res.deleted_count)
    except Exception:  # noqa: BLE001
        log.exception("Protokoll-Entwurf zu Termin %s nach Terminaenderung nicht verworfen",
                      appt_id)
        return False


async def freigaben_geschlossener_termine_zuruecknehmen(dbx=None) -> int:
    """Pruefung 14.09.2026 (C4/C19): Nachholer fuer den Aufraeum-Job. Findet
    Protokolle, die beim Chef liegen oder freigegeben sind, obwohl ihr Termin
    geschlossen ist (Ruecknahme beim Schliessen gescheitert), und nimmt die
    Freigabe zurueck; offene Korrektur-Versionen an geschlossenen Terminen
    werden verworfen. Nutzt das uebergebene db-Handle (Aufraeum-Job) — die
    Hilfsfunktionen dieses Moduls haengen am Modul-db, deshalb hier direkt.
    Liefert die Zahl der bereinigten Protokolle."""
    dbx = dbx if dbx is not None else db
    n = 0
    # Nachpruefung 15.09.2026 (Fahrer Nr. 7/8): die Kandidaten kommen per
    # $lookup direkt MIT geschlossenem Termin — vorher fuellten 500 legitime
    # offene Freigaben die Grenze, und die kaputten dahinter kamen nie dran.
    geschlossen = sorted(s for s in _ABGESCHLOSSEN if s != "abgeholt")
    kandidaten = await dbx.pickup_protocols.aggregate([
        {"$match": {"status": {"$in": [ZUR_FREIGABE, FREIGEGEBEN]}, "superseded": {"$ne": True}}},
        {"$lookup": {"from": "appointments", "localField": "appointment_id",
                     "foreignField": "id", "as": "termin"}},
        {"$match": {"termin.status": {"$in": geschlossen}}},
        {"$project": {"_id": 0, "id": 1, "appointment_id": 1, "corrects_version": 1}},
        {"$limit": 500},
    ]).to_list(500)
    for p in kandidaten:
        if "corrects_version" in p:
            jetzt = now_iso()
            res = await dbx.pickup_protocols.update_one(
                {"id": p["id"], "status": {"$in": [ZUR_FREIGABE, FREIGEGEBEN]}},
                {"$set": {"superseded": True, "verworfen_am": jetzt, "updated_at": jetzt}})
            if res.matched_count:
                r2 = await dbx.pickup_protocols.update_one(
                    {"appointment_id": p["appointment_id"], "version": p["corrects_version"]},
                    {"$set": {"superseded": False, "updated_at": jetzt},
                     "$unset": {"superseded_at": ""}})
                if r2.matched_count == 0:
                    # Befund 56 (16.09.2026): ohne Vorversion darf der Nachholer
                    # keinen Termin ohne aktuelles Protokoll hinterlassen —
                    # Verwerfen zuruecknehmen und melden.
                    await dbx.pickup_protocols.update_one(
                        {"id": p["id"]},
                        {"$set": {"superseded": False}, "$unset": {"verworfen_am": ""}})
                    log.error("Nachholer: Korrektur %s zu Termin %s nicht verworfen — "
                              "Vorversion %s fehlt", p["id"], p["appointment_id"],
                              p["corrects_version"])
                    try:
                        await betrieb.alarm(dbx, "protokoll_korrektur_ohne_vorversion",
                                            ref=p["appointment_id"], protokoll_id=p["id"],
                                            version=p["corrects_version"])
                    except Exception:  # noqa: BLE001
                        pass
                    continue
                n += 1
            continue
        res = await dbx.pickup_protocols.update_one(
            {"id": p["id"], "status": {"$in": [ZUR_FREIGABE, FREIGEGEBEN]}},
            _zuruecknehmen_aenderung(None))
        if res.matched_count:
            n += 1
    return n


async def _preis_uebernehmen(appt: dict, doc: dict, *, nachholen: bool = False) -> None:
    """Go-Live 13.09.2026 (P5, N2, P5-Zusatz-Korrektur): Preis des finalen
    Protokolls in den Kaufvorgang DIESES Termins uebernehmen. Idempotent ueber
    preis_protokoll_id — deshalb im Normalpfad UND in der Selbstheilung. Vorher
    schrieb nur der Normalpfad den Preis; brach der Abschluss davor ab, zog die
    Selbstheilung Termin und Status nach, der Preis blieb der Vertragspreis.

    * neuer_preis gesetzt: Preis, preis_nachverhandelt, preis_vorher (Vertrags-
      preis) und preis_protokoll_id — nur, wenn der Vorgang nicht schon den Preis
      GENAU dieser Protokollversion traegt.
    * kein neuer_preis: stammt der Preis des Vorgangs aus einer ANDEREN Version
      dieses Termins (Korrektur, Chef hat auf den Vertragspreis zurueckgesetzt),
      zurueck auf den Vertragspreis. Ohne fruehere Verhandlung: nichts.
    * Selbstheilung (nachholen=True): einen inzwischen von Hand eingetragenen
      Preis (final_price, preis_quelle 'vor_ort') nicht ueberschreiben."""
    import kaufvorgang as _kv
    kv = await _kv.fuer_termin(appt)
    dealer_id = appt.get("dealer_id")
    if not kv or kv.get("dealer_id") != dealer_id:
        return
    jetzt = now_iso()
    preis = doc.get("neuer_preis")
    if preis is not None:
        vertragspreis = None
        if kv.get("contract_id"):
            c = await db.generated_pdfs.find_one(
                {"id": kv["contract_id"], "dealer_id": dealer_id},
                {"_id": 0, "contract_data.purchase_price": 1})
            vertragspreis = ((c or {}).get("contract_data") or {}).get("purchase_price")
        if vertragspreis is None:
            vertragspreis = (kv.get("preis_vorher") if kv.get("preis_nachverhandelt")
                             else kv.get("purchase_price"))
        filt: Dict[str, Any] = {"id": kv["id"], "dealer_id": dealer_id,
                                "preis_protokoll_id": {"$ne": doc["id"]}}
        if nachholen:
            filt["preis_quelle"] = {"$ne": "vor_ort"}
        await db.kaufvorgaenge.update_one(
            filt, {"$set": {"purchase_price": float(preis), "preis_nachverhandelt": True,
                            "preis_vorher": vertragspreis, "preis_protokoll_id": doc["id"],
                            "preis_quelle": "protokoll", "updated_at": jetzt}})
        return
    alt = kv.get("preis_protokoll_id")
    if not alt or alt == doc["id"] or kv.get("preis_quelle") == "vor_ort" \
            or kv.get("preis_vorher") is None:
        return
    # Nur eine Version DIESES Termins — der Preis eines anderen Termins bleibt.
    if not await db.pickup_protocols.find_one(
            {"id": alt, "appointment_id": appt.get("id")}, {"_id": 1}):
        return
    await db.kaufvorgaenge.update_one(
        {"id": kv["id"], "dealer_id": dealer_id, "preis_protokoll_id": alt,
         "preis_quelle": {"$ne": "vor_ort"}},
        {"$set": {"purchase_price": kv["preis_vorher"], "updated_at": jetzt},
         "$unset": {"preis_nachverhandelt": "", "preis_vorher": "",
                    "preis_protokoll_id": "", "preis_quelle": ""}})


def _nacharbeit_merker(appt: dict, doc: dict) -> Dict[str, Any]:
    """Go-Live 13.09.2026 (P5-Nachbesserung): Merker im Termin-CAS. Scheitern
    Preis- oder Statusuebernahme danach (500) und laedt der Fahrer die Seite
    neu, statt erneut zu tippen, sieht er 'final' ohne Knopf — die
    Selbstheilung liefe nie. Mit dem Merker holt der naechste PUT des Termins
    Preis und Status nach (update_appointment). Die protokoll_id nur, wenn
    nicht schon ein Merker der Terminanlage (Audit #5) steht — dessen
    Nacharbeit raeumt nur update_appointment ab."""
    merker: Dict[str, Any] = {"nacharbeit_offen": True}
    if not appt.get("nacharbeit_offen"):
        merker["nacharbeit_protokoll_id"] = doc["id"]
    return merker


async def _nacharbeit_erledigt(appt_id: str, doc: dict) -> None:
    """Go-Live 13.09.2026 (P5-Nachbesserung): nur den Merker DIESES Abschlusses
    entfernen. Best effort — ein stehengebliebener Merker ist harmlos (der
    naechste PUT holt idempotent nach)."""
    try:
        await db.appointments.update_one(
            {"id": appt_id, "nacharbeit_protokoll_id": doc["id"]},
            {"$unset": {"nacharbeit_offen": "", "nacharbeit_protokoll_id": ""}})
    except Exception:  # noqa: BLE001
        log.exception("Merker nacharbeit_offen an Termin %s nicht entfernt", appt_id)


async def preis_nachholen(appt: dict) -> None:
    """Go-Live 13.09.2026 (P5-Nachbesserung): fuer update_appointment (Merker
    nacharbeit_offen) den Preis des aktuellen finalen Protokolls in den Vorgang
    DIESES Termins nachziehen — idempotent, ohne einen Handpreis zu
    ueberschreiben. Nur, wenn der Termin noch am Vertrag des Abschlusses haengt;
    sonst landete der Preis im Vorgang eines anderen Vertrags."""
    doc = await db.pickup_protocols.find_one(
        {"appointment_id": appt.get("id"), "status": "final", "superseded": {"$ne": True}},
        {"_id": 0, "id": 1, "neuer_preis": 1, "contract_id": 1})
    if not doc or "contract_id" not in doc \
            or (doc.get("contract_id") or None) != (appt.get("contract_id") or None):
        return
    await _preis_uebernehmen(appt, doc, nachholen=True)


@router.get("/driver/appointments/{appt_id}/protocol")
async def get_protocol(appt_id: str, driver=Depends(current_driver)):
    """Aktuellen Entwurf (oder das abgeschlossene Protokoll) + Vorlage laden."""
    appt = await _appt_or_404(appt_id, driver)
    doc = await _current(appt_id)
    vehicle: Dict[str, Any] = {}
    if appt.get("vehicle_id"):
        # WICHTIG: zusaetzlich ueber dealer_id. Fahrzeug-IDs sind
        # vorhersehbar (v_<Inserats-ID>) und nur MIT dealer_id eindeutig —
        # sonst koennte das Fahrzeug eines fremden Haendlers geladen werden.
        v = await db.vehicles.find_one(
            {"id": appt["vehicle_id"], "dealer_id": appt.get("dealer_id")},
            {"_id": 0}) or {}
        vehicle = dict(v.get("data") or {})
    contract: Dict[str, Any] = {}
    if appt.get("contract_id"):
        c = await db.generated_pdfs.find_one(
            {"id": appt["contract_id"], "dealer_id": appt.get("dealer_id")},
            {"_id": 0, "contract_data": 1}) or {}
        contract = dict(c.get("contract_data") or {})
    return {
        # Gegenpruefung 12.09.2026: Infinity/NaN aus Altdaten nicht ans JSON geben.
        "protocol": PV.json_sicher(doc),
        "template": {
            "vehicle_check_fields": [
                {"key": k, "label": lb, "options": opts}
                for k, lb, opts in VEHICLE_CHECK_FIELDS
            ],
            "vehicle_check_values": _vehicle_check_values(vehicle, contract),
            # Runde 33: Eingabeart je Zeile — Erstzulassung und HU als MM/JJJJ
            # mit Zifferntastatur, Kilometer und Halter nur Ziffern.
            "vehicle_check_art": dict(PV.ARTEN),
            "documents": DOCUMENT_ITEMS,
            "features": (vehicle.get("features") or [])[:AUSSTATTUNG_MAX],
            "condition_fields": [
                {"key": k, "label": lb, "options": opts if isinstance(opts, list) else None}
                for k, lb, opts in CONDITION_FIELDS
            ],
        },
        "vehicle": vehicle,
        "damages": contract.get("damages") or vehicle.get("damages") or [],
        # Runde 30: Der Fahrer sieht den Vertragspreis — und nach der
        # Freigabe den nachverhandelten Preis, den er unterschreibt.
        "preis_vertrag": contract.get("purchase_price"),
        "appointment": {k: appt.get(k) for k in
                        ("id", "title", "pickup_date", "pickup_time",
                         "pickup_address", "seller_name", "status")},
    }


# Termine, die abgeschlossen oder storniert sind, nehmen KEINE neuen
# Protokoll-Versionen mehr an (PR-Review 09/2026): sonst konnte ein Fahrer
# nach Abholung/Nichtabholung/Storno die aktuelle Beweisversion ersetzen.
# Will der Haendler eine Korrektur, setzt er den Termin im Buero wieder auf
# "offen" — erst dann darf der Fahrer erneut ans Protokoll.
_ABGESCHLOSSEN = {"abgeholt", "nicht abgeholt", "storniert", "erledigt"}


def _entwurf_filter(proto_id: str) -> dict:
    """Nachpruefung Runde 10: Schreiben darf, wer den ENTWURF trifft — oder
    ein 'wird_abgeschlossen', dessen Claim abgelaufen ist. Stirbt der
    Prozess mitten im Abschluss, blieb der Status sonst fuer immer stehen:
    jedes Zwischenspeichern der Fahrer-App bekam 409 "bereits
    abgeschlossen", und das Finalize (das den Ablauf kennt) wurde nie
    erreicht, weil die App vor dem Abschluss immer erst speichert."""
    from datetime import datetime as _dt, timezone as _tz
    jetzt = _dt.now(_tz.utc).isoformat()
    return {"id": proto_id,
            "$or": [{"status": "entwurf"},
                    {"status": "wird_abgeschlossen", "claim_bis": {"$lt": jetzt}}]}


def _entwurf_update(payload: dict) -> dict:
    return {"$set": {**payload, "status": "entwurf", "updated_at": now_iso()},
            "$unset": {"claim_bis": ""},
            "$inc": {"revision": 1}}          # Phase 2 (2.9): jede Speicherung zaehlt hoch


async def _entwurf_revision_pruefen(proto_id: str, revision: Optional[int]) -> None:
    """Phase 2 (2.9, B26): Schreiben ging nicht durch — lag es an der
    Revision (anderer Tab/anderes Geraet hat inzwischen gespeichert)?"""
    if revision is None:
        return
    akt = await db.pickup_protocols.find_one({"id": proto_id}, {"_id": 0, "status": 1, "revision": 1})
    if akt and akt.get("status") == "entwurf" and int(akt.get("revision") or 0) != int(revision):
        raise HTTPException(409, "Der Entwurf wurde inzwischen in einem anderen Tab oder auf "
                                 "einem anderen Gerät gespeichert — bitte neu laden.")


async def _speichern_abgelehnt(proto_id: str):
    """Warum ging das Schreiben nicht durch? Laufender Abschluss -> warten,
    sonst ist das Protokoll fertig -> Korrektur-Version."""
    akt = await db.pickup_protocols.find_one({"id": proto_id}, {"_id": 0, "status": 1})
    if akt and akt.get("status") == "wird_abgeschlossen":
        raise HTTPException(409, "Das Protokoll wird gerade abgeschlossen "
                                 "— bitte einen Moment warten.")
    # Runde 30: abgeschickt oder freigegeben -> nicht mehr aenderbar, damit
    # der Chef genau das sieht, was am Ende unterschrieben wird.
    if akt and akt.get("status") == ZUR_FREIGABE:
        raise HTTPException(409, "Das Protokoll liegt beim Händler zur "
                                 "Freigabe und kann gerade nicht geändert "
                                 "werden.")
    if akt and akt.get("status") == FREIGEGEBEN:
        raise HTTPException(409, "Der Händler hat das Protokoll freigegeben — "
                                 "bitte jetzt unterschreiben. Für Änderungen "
                                 "muss er es zurückschicken.")
    raise HTTPException(409, "Protokoll ist bereits abgeschlossen. Bitte eine "
                             "Korrektur-Version starten.")


def _termin_offen_oder_409(appt: dict) -> None:
    if (appt.get("status") or "offen") in _ABGESCHLOSSEN:
        raise HTTPException(409, f"Termin ist '{appt.get('status')}' — das "
                                 "Protokoll ist gesperrt. Für eine Korrektur "
                                 "muss der Händler den Termin wieder öffnen.")


async def _vertrag_nicht_in_loeschung(appt: dict) -> None:
    """Befund 48 (16.09.2026): kein NEUER Entwurf und keine Korrektur zu einem
    Vertrag, dessen Loeschung laeuft (Grabstein loeschung.status = laeuft).
    Die Loeschsperre in DELETE /contracts prueft vorher auf laufende
    Protokolle; zwischen dieser Pruefung und dem Grabstein konnte der Fahrer
    sonst einen Entwurf beginnen, den die Kaskade sofort bereinigt."""
    cid = appt.get("contract_id")
    if cid and await db.generated_pdfs.count_documents(
            {"id": cid, "loeschung.status": "laeuft"}, limit=1):
        raise HTTPException(409, "Der Kaufvertrag zu diesem Termin wird gerade gelöscht — "
                                 "es kann kein Protokoll mehr begonnen werden.")


@router.put("/driver/appointments/{appt_id}/protocol")
async def save_protocol(appt_id: str, body: ProtocolIn,
                        driver=Depends(current_driver)):
    """Zwischenstand speichern — beliebig oft, solange nicht abgeschlossen."""
    appt = await _appt_or_404(appt_id, driver)
    _termin_offen_oder_409(appt)
    doc = await _current(appt_id)
    if doc and doc.get("status") == "final":
        raise HTTPException(409, "Protokoll ist bereits abgeschlossen. Bitte eine "
                                 "Korrektur-Version starten.")
    payload = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    revision = payload.pop("revision", None)
    revision_filt = {"revision": int(revision)} if revision is not None else {}
    # Runde 13 (Liste 3 Nr. 9): traegt der Entwurf eine Revision (alle seit
    # Phase 2), ist sie beim Speichern Pflicht — ein alter Tab ohne Stand
    # ueberschreibt nichts mehr.
    if doc and doc.get("revision") is not None and revision is None:
        raise HTTPException(409, "Bitte die App neu laden — der Entwurf braucht den "
                                 "aktuellen Stand (Revision).")
    if doc:
        # Runde 10: Bedingt auf den Entwurf-Status schreiben. Zwischen der
        # Pruefung oben und dem Schreiben kann das Protokoll unterschrieben
        # und abgeschlossen worden sein — ein verspaetetes Autospeichern
        # haette dann Felder des FERTIGEN Protokolls ueberschrieben.
        # Pruefung 14.09.2026 (Liste 4, Nr. 3/6): der Entwurf gehoert dem
        # Fahrer, der ihn JETZT bearbeitet, und dem Fahrzeug, das JETZT am
        # Termin haengt — beides wird bei jedem Speichern nachgezogen.
        res = await db.pickup_protocols.update_one(
            {**_entwurf_filter(doc["id"]), **revision_filt},
            _entwurf_update({**payload, "driver_account_id": driver["id"],
                             "driver_name": driver.get("display_name", ""),
                             "vehicle_id": appt.get("vehicle_id"),
                             "contract_id": appt.get("contract_id")}))
        if res.matched_count == 0:
            await _entwurf_revision_pruefen(doc["id"], revision)
            await _speichern_abgelehnt(doc["id"])
        return await db.pickup_protocols.find_one({"id": doc["id"]}, {"_id": 0})
    await _vertrag_nicht_in_loeschung(appt)                  # Befund 48
    # Versionsnummer: hoechste vorhandene + 1 — nach einem verworfenen Entwurf
    # (Fahrzeug-/Vertragswechsel) waere "1" eine Dublette im Index.
    hoechste = await db.pickup_protocols.find_one(
        {"appointment_id": appt_id}, {"_id": 0, "version": 1}, sort=[("version", -1)])
    new_doc = {
        "id": str(uuid.uuid4()),
        "appointment_id": appt_id,
        "vehicle_id": appt.get("vehicle_id"),
        "contract_id": appt.get("contract_id"),     # Runde 13 (Nr. 18): Vertragsanker
        "dealer_id": appt.get("dealer_id"),
        "driver_account_id": driver["id"],
        "driver_name": driver.get("display_name", ""),
        "version": int((hoechste or {}).get("version") or 0) + 1,
        "status": "entwurf",
        "superseded": False,
        "revision": 1,
        **payload,
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    try:
        await db.pickup_protocols.insert_one(new_doc)
    except DuplicateKeyError:
        # Unique-Index (genau EIN aktuelles Protokoll je Termin): ein
        # GLEICHZEITIGER Request hat den Entwurf gerade angelegt — dann
        # dessen Dokument aktualisieren statt ein zweites zu erzeugen.
        # Runde 13 (Liste 3 Nr. 13): NUR Dubletten — ein DB-/Netzfehler
        # bleibt ein 500, statt als Parallel-Insert behandelt zu werden.
        vorhandenes = await _current(appt_id)
        if not vorhandenes:
            raise
        # Befund 47 (16.09.2026): B las "kein Protokoll", A legte Version 1 an —
        # ohne eigene Revision schrieb B dann mit leerem Revisionsfilter ueber
        # A's Entwurf. Dieselbe Regel wie oben: ein Entwurf mit Revision wird
        # nur mit Revision geschrieben (App neu laden).
        if vorhandenes.get("revision") is not None and revision is None:
            raise HTTPException(409, "Der Entwurf wurde gerade auf einem anderen Gerät "
                                     "angelegt — bitte die App neu laden.")
        # Pruefung 14.09.2026 (B27): derselbe Stand von Fahrer und Fahrzeug
        # wie im Normalweg — sonst lief der Entwurf nach einer Umbuchung mit
        # veralteten Metadaten weiter.
        res = await db.pickup_protocols.update_one(
            {**_entwurf_filter(vorhandenes["id"]), **revision_filt},
            _entwurf_update({**payload, "driver_account_id": driver["id"],
                             "driver_name": driver.get("display_name", ""),
                             "vehicle_id": appt.get("vehicle_id"),
                             "contract_id": appt.get("contract_id")}))
        if res.matched_count == 0:
            await _entwurf_revision_pruefen(vorhandenes["id"], revision)
            await _speichern_abgelehnt(vorhandenes["id"])
        return await db.pickup_protocols.find_one(
            {"id": vorhandenes["id"]}, {"_id": 0})
    return {k: v for k, v in new_doc.items() if k != "_id"}


def _pflichtfelder_pruefen(doc: dict, appt: dict, *,
                           seller_name: Optional[str] = None,
                           place: Optional[str] = None,
                           mit_uebergabe: bool = True,
                           vollstaendig: bool = False,
                           ausstattung: Optional[List[str]] = None) -> None:
    """Alles beisammen? Das Backend verlaesst sich NICHT auf die App.

    Runde 30: dieselbe Pruefung fuer das Abschicken zur Freigabe UND fuer
    den endgueltigen Abschluss — sonst koennte ein halb ausgefuelltes
    Protokoll beim Chef landen.

    Wunsch Ahmad 14.09.2026 ("alles muss angeklickt werden"): mit
    `vollstaendig=True` (beim Abschicken) sind ALLE Abschnitte Pflicht —
    jeder Zustandspunkt, jedes Dokument und jede Ausstattungszeile muss
    beantwortet sein (Ja/Nein), dazu Schluessel erhalten/vereinbart, Ort und
    Verkaeufername (Pruefung 14.09.2026, P2: der Chef sieht sie so vor der
    Freigabe; beim Unterschreiben sind sie nicht mehr aenderbar).
    """
    if vollstaendig:
        _alle_abschnitte_pruefen(doc, ausstattung or [])
        mit_uebergabe = True
    # Abschnitt 1: alle 12 Fahrzeugdaten-Zeilen muessen beantwortet sein.
    # Pruefung 14.09.2026 (C12): und zwar mit einer der ANGEBOTENEN Antworten —
    # vorher genuegte irgendein Text ("x"), und "weicht ab" ohne den
    # tatsaechlichen Wert stand als leere Abweichung im unterschriebenen PDF.
    vc = doc.get("vehicle_check") or {}
    fehlend, ungueltig, ohne_wert = [], [], []
    for key, label, opts in VEHICLE_CHECK_FIELDS:
        eintrag = vc.get(key)
        status = str((eintrag or {}).get("status") if isinstance(eintrag, dict)
                     else eintrag or "").strip()
        if not status:
            fehlend.append(label)
        elif status not in opts:
            ungueltig.append(label)
        elif status == "weicht ab" and not str(
                (eintrag or {}).get("value") if isinstance(eintrag, dict) else "").strip():
            ohne_wert.append(label)
    if fehlend:
        raise HTTPException(422, "Abschnitt 1 unvollständig — bitte noch "
                                 "ankreuzen: " + ", ".join(fehlend))
    if ungueltig:
        raise HTTPException(422, "Abschnitt 1: ungültige Antwort bei "
                                 + ", ".join(ungueltig) + " — bitte eine der "
                                 "angebotenen Antworten wählen.")
    if ohne_wert:
        raise HTTPException(422, "Abschnitt 1: bei \"weicht ab\" bitte den tatsächlichen "
                                 "Wert eintragen: " + ", ".join(ohne_wert))
    cond = doc.get("condition") or {}
    if not str(cond.get("mileage") or "").strip():
        raise HTTPException(422, "Bitte den Kilometerstand bei Abholung "
                                 "eintragen (Abschnitt 4).")
    if not str(doc.get("keys_count") or "").strip():
        raise HTTPException(422, "Bitte die Anzahl der übergebenen "
                                 "Schlüssel eintragen (Abschnitt 2).")
    if doc.get("damages_confirmed") is None:
        raise HTTPException(422, "Bitte Abschnitt 5 (bekannte Schäden "
                                 "bestätigt) beantworten.")
    if not mit_uebergabe:
        return
    if not (seller_name or doc.get("seller_name")
            or appt.get("seller_name") or "").strip():
        raise HTTPException(422, "Bitte den Namen des Verkäufers angeben.")
    if not (place or doc.get("place") or "").strip():
        raise HTTPException(422, "Bitte den Ort der Übergabe angeben.")


def _alle_abschnitte_pruefen(doc: dict, ausstattung: List[str]) -> None:
    """Wunsch Ahmad 14.09.2026: Zustand (alle Zeilen), Dokumente und
    Ausstattung (je Zeile Ja oder Nein), Schluessel erhalten UND vereinbart."""
    cond = doc.get("condition") or {}
    fehlend = []
    for key, label, opts in CONDITION_FIELDS:
        wert = cond.get(key)
        text = str(wert if wert is not None else "").strip()
        if not text:
            fehlend.append(label)
        elif isinstance(opts, list) and text not in opts:
            raise HTTPException(422, f"Abschnitt 4: ungültige Antwort bei {label} — bitte "
                                     "eine der angebotenen Antworten wählen.")
    if fehlend:
        raise HTTPException(422, "Abschnitt 4 unvollständig — bitte noch ausfüllen: "
                                 + ", ".join(fehlend))
    dokumente = doc.get("documents") or {}
    offen = [d for d in DOCUMENT_ITEMS if not isinstance(dokumente.get(d), bool)]
    if offen:
        raise HTTPException(422, "Abschnitt 2: bitte bei jedem Dokument Ja oder Nein "
                                 "angeben: " + ", ".join(offen))
    if not str(doc.get("keys_expected") or "").strip():
        raise HTTPException(422, "Bitte die Anzahl der vereinbarten Schlüssel "
                                 "eintragen (Abschnitt 2).")
    merkmale = doc.get("features") or {}
    offen = [f for f in ausstattung if not isinstance(merkmale.get(f), bool)]
    if offen:
        raise HTTPException(422, "Abschnitt 3: bitte bei jeder Ausstattung Ja oder Nein "
                                 "angeben: " + ", ".join(offen))


@router.post("/driver/appointments/{appt_id}/protocol/submit")
async def submit_protocol(appt_id: str, driver=Depends(current_driver)):
    """Runde 30 (Wunsch Ahmad): Der Fahrer schickt das ausgefuellte
    Protokoll zur FREIGABE an den Chef — noch ohne Unterschriften.

    Danach sieht der Chef, was vor Ort abweicht und welche Schaeden neu
    sind, und kann mit dem Verkaeufer nachverhandeln. Erst nach seiner
    Freigabe wird unterschrieben."""
    appt = await _appt_or_404(appt_id, driver)
    _termin_offen_oder_409(appt)
    doc = await _current(appt_id)
    if not doc:
        raise HTTPException(400, "Bitte zuerst das Protokoll ausfüllen")
    if doc.get("status") == "final":
        raise HTTPException(409, "Protokoll ist bereits abgeschlossen. Bitte "
                                 "eine Korrektur-Version starten.")
    if doc.get("status") in GESPERRT_FUER_FAHRER:
        # Schon abgeschickt — idempotent, damit ein Doppeltipp oder ein
        # Netzabbruch keine Fehlermeldung erzeugt.
        return {"ok": True, "status": doc["status"],
                "protocol_id": doc["id"], "bereits": True}
    # Vollstaendig ausgefuellt? Wunsch Ahmad 14.09.2026: ALLE Abschnitte,
    # dazu Ort und Verkaeufername (P2) — der Chef sieht das ganze Protokoll.
    vehicle, contract = await _fahrzeug_und_vertrag(appt)
    _pflichtfelder_pruefen(doc, appt, vollstaendig=True,
                           ausstattung=list((vehicle.get("features") or [])[:AUSSTATTUNG_MAX]))
    jetzt = now_iso()
    setzen: Dict[str, Any] = {"status": ZUR_FREIGABE, "abgeschickt_am": jetzt,
                              "abgeschickt_von": driver["id"], "updated_at": jetzt,
                              # Stand fuer die Konfliktpruefung der Freigabe
                              "freigabe_stand": jetzt,
                              # Pruefung 14.09.2026 (P1): Vertrags-/Fahrzeugstand,
                              # den der Chef freigibt — der Abschluss prueft
                              # dagegen (vertragsstand_pruefen).
                              "vertragswerte_stand": vertragswerte_stand(vehicle, contract),
                              "seller_name": (doc.get("seller_name") or appt.get("seller_name") or "").strip()}
    if doc.get("place"):
        setzen["place"] = str(doc["place"]).strip()
    # Runde 33: Die Freigabe-Liste sortiert nach dem ERSTEN Abschicken — ein
    # nach einer Rueckfrage erneut abgeschicktes Protokoll springt sonst nach
    # oben bzw. unten, waehrend der Chef gerade daran arbeitet.
    if not doc.get("erstmals_abgeschickt_am"):
        setzen["erstmals_abgeschickt_am"] = jetzt
    # Runde 13 (Liste 3 Nr. 10-12): genau der geprueften Stand (Revision)
    # wechselt zur Freigabe — ein anderer Tab, der dazwischen speicherte,
    # bekommt 409 statt dass ungepruefte Daten beim Chef landen.
    submit_filt: Dict[str, Any] = {"id": doc["id"], "status": "entwurf"}
    if doc.get("revision") is not None:
        submit_filt["revision"] = doc["revision"]
    res = await db.pickup_protocols.update_one(
        submit_filt,
        {"$set": setzen,
         "$unset": {"rueckfrage": "", "rueckfrage_am": ""}})
    if not res.matched_count:
        # Zwischen Lesen und Schreiben hat sich der Stand geaendert.
        akt = await db.pickup_protocols.find_one({"id": doc["id"]},
                                                 {"_id": 0, "status": 1, "revision": 1})
        if akt and akt.get("status") == "entwurf" and doc.get("revision") is not None \
                and akt.get("revision") != doc.get("revision"):
            raise HTTPException(409, "Der Entwurf wurde inzwischen in einem anderen Tab "
                                     "geändert — bitte neu laden, prüfen und erneut abschicken.")
        return {"ok": True, "status": (akt or {}).get("status", "unbekannt"),
                "protocol_id": doc["id"], "bereits": True}
    # Runde 12 (15.09.2026, Nr. 19): den Termin-Stand anfassen — ein Termin-
    # Update mit Beweisdaten-Aenderung, das den alten Stand gelesen hat,
    # scheitert danach an seiner Stand-Pruefung.
    # Befund 72/73 (16.09.2026): erst NACH dem gelungenen Protokoll-CAS (ein
    # verlorener Revisions-Wettlauf aenderte sonst den Termin und verteilte
    # 409 an fremde Termin-Editoren) — und scheitert der Write, gibt es einen
    # Betriebsalarm statt nur einer Logzeile.
    try:
        await db.appointments.update_one({"id": appt_id}, {"$set": {"updated_at": jetzt}})
    except Exception as exc:  # noqa: BLE001
        log.exception("Termin-Stand nach Abschicken von %s nicht angefasst", appt_id)
        try:
            await betrieb.alarm(db, "termin_stand_nicht_angefasst", ref=appt_id,
                                protokoll_id=doc["id"], fehler=str(exc)[:300])
        except Exception:  # noqa: BLE001
            pass
    # Go-Live 13.09.2026 (P6): Schloss der Haendler den Termin genau zwischen
    # Vorabpruefung und diesem Write, fand sein Zuruecknehmen noch den Entwurf —
    # das Protokoll laege sonst beim Chef an einem geschlossenen Termin.
    frisch = await db.appointments.find_one({"id": appt_id}, {"_id": 0, "status": 1})
    if frisch is None:
        # Pruefung 14.09.2026 (F2): Termin inzwischen geloescht — kein
        # verwaister Freigabevorgang beim Chef.
        await _freigabe_zuruecknehmen(
            {"id": doc["id"], "status": ZUR_FREIGABE, "freigabe_stand": jetzt}, None)
        # Pruefbericht 20.09.2026 (V-18): Der zurueckgenommene Entwurf gehoert
        # zu keinem Termin mehr. Lief das Entwurf-Aufraeumen der Terminloeschung
        # schon VORHER, bliebe er fuer immer liegen. Entwuerfe haben weder PDF
        # noch Unterschriften (die entstehen erst beim Abschluss).
        try:
            await db.pickup_protocols.delete_one({"id": doc["id"], "status": "entwurf"})
        except Exception:  # noqa: BLE001  (der Aufraeumjob holt es nach)
            log.exception("Verwaister Protokoll-Entwurf %s nicht geloescht", doc["id"])
        raise HTTPException(404, "Termin nicht gefunden")
    if (frisch.get("status") or "offen") in _ABGESCHLOSSEN:
        await _freigabe_zuruecknehmen(
            {"id": doc["id"], "status": ZUR_FREIGABE, "freigabe_stand": jetzt}, None)
        _termin_offen_oder_409(frisch)
    # Pruefung 14.09.2026 (C8): Das Protokoll liegt ab hier beim Chef — ein
    # scheiternder Audit-Eintrag gab vorher 500, die App zeigte einen Fehler
    # und der Fahrer tippte erneut (dann "bereits").
    await log_activity_sicher(appt.get("dealer_id", ""), driver["id"],
                              "protokoll.zur_freigabe", ref=doc["id"],
                              meta={"appointment_id": appt_id,
                                    "vehicle_id": appt.get("vehicle_id")})
    return {"ok": True, "status": ZUR_FREIGABE, "protocol_id": doc["id"]}


@router.post("/driver/appointments/{appt_id}/protocol/correction")
async def start_correction(appt_id: str, driver=Depends(current_driver)):
    """Neue Version anlegen: die alte bleibt als Beweis erhalten."""
    appt = await _appt_or_404(appt_id, driver)
    _termin_offen_oder_409(appt)
    await _vertrag_nicht_in_loeschung(appt)                  # Befund 48
    doc = await _current(appt_id)
    if not doc or doc.get("status") != "final":
        raise HTTPException(400, "Es gibt kein abgeschlossenes Protokoll zum Korrigieren")
    # ATOMAR abloesen (Pruefbericht 09/2026): zwei gleichzeitige Korrektur-
    # Aufrufe lasen vorher beide dieselbe finale Version und legten ZWEI
    # Folgeversionen an. Nur wer das superseded-Flag selbst setzt, darf die
    # neue Version anlegen — jeder weitere Aufruf findet die Version nicht
    # mehr als aktuell vor und bekommt 409.
    # Pruefbericht 20.09.2026 (R1-20): Abloesen und Anlegen der Folgeversion
    # laufen jetzt in EINER Transaktion (Replica-Set, also Produktion). Starb
    # der Prozess vorher zwischen beiden Schritten, hatte der Termin keine
    # aktuelle Version mehr; ein Speichern in den 120 s danach legte einen
    # Entwurf OHNE corrects_version an, und die unterschriebene Fassung blieb
    # fuer immer abgeloest. Ohne Replica-Set wie bisher mit Ruecknahme.
    from deps import transaktion
    new_doc = {k: v for k, v in doc.items()
               if k not in ("id", "status", "pdf_path", "pdf_sha256", "finalized_at",
                            "signature_driver_key", "signature_seller_key",
                            "superseded_at", "claim_bis", "vertragswerte_stand",
                            "repariert_am", "pdf_path_loeschung_offen",
                            # Go-Live 13.09.2026 (P1): kein Rest-Token und keine
                            # Abschluss-Verweise in die Folgeversion.
                            "claim_token", "contract_id", "kaufvorgang_id",
                            # Gegenpruefung 12.09.2026: Die Korrektur wartet
                            # NEU — sonst stand sie in der Freigabe-Liste als
                            # "wartet seit 50 Std." ganz oben.
                            "abgeschickt_am", "abgeschickt_von", "erstmals_abgeschickt_am",
                            "freigegeben_am", "freigegeben_von", "freigabe_stand",
                            "rueckfrage", "rueckfrage_am", "rueckfrage_von")}
    # Pruefung 14.09.2026 (C3): NICHT "aktuelle + 1" — nach einer verworfenen
    # Korrektur (Version 2 verworfen, Version 1 wieder aktuell) kollidierte die
    # naechste Korrektur mit der verworfenen 2 (Unique-Index) und lief in 409.
    new_doc.update({
        "id": str(uuid.uuid4()),
        "status": "entwurf",
        "superseded": False,
        "corrects_version": doc.get("version", 1),
        # Pruefung 14.09.2026 (C2): die Korrektur macht DER Fahrer, der sie
        # startet — vorher blieb der Name des Vorgaengers in der Folgeversion.
        "driver_account_id": driver["id"],
        "driver_name": driver.get("display_name", ""),
        # Pruefung 14.09.2026 (P4): das Fahrzeug, das JETZT am Termin haengt.
        "vehicle_id": appt.get("vehicle_id"),
        "created_at": now_iso(), "updated_at": now_iso(),
    })

    async def _abloesung_zuruecknehmen():
        # Schlaegt der Insert fehl, darf der Termin nicht OHNE aktuelle
        # Version zurueckbleiben: alte Version wieder aktiv schalten.
        try:
            await db.pickup_protocols.update_one(
                {"id": doc["id"]},
                {"$set": {"superseded": False},
                 "$unset": {"superseded_at": ""}})
        except Exception:  # noqa: BLE001
            log.exception("Protokoll-Korrektur: Abloesung von %s konnte "
                          "nicht zurueckgenommen werden", doc["id"])

    async def _abloesen_und_anlegen(session=None) -> dict:
        ses = {"session": session} if session is not None else {}
        abgeloest = await db.pickup_protocols.find_one_and_update(
            {"id": doc["id"], "status": "final", "superseded": {"$ne": True}},
            {"$set": {"superseded": True, "superseded_at": now_iso()}}, **ses)
        if abgeloest is None:
            raise HTTPException(409, "Korrektur läuft bereits / Version nicht "
                                     "mehr aktuell")
        hoechste = await db.pickup_protocols.find_one(
            {"appointment_id": appt_id}, {"_id": 0, "version": 1},
            sort=[("version", -1)], **ses)
        neu = dict(new_doc)
        neu["version"] = max(int((hoechste or {}).get("version") or 1),
                             int(doc.get("version", 1))) + 1
        try:
            await db.pickup_protocols.insert_one(dict(neu), **ses)
        except DuplicateKeyError:
            # Unique-Index (appointment_id, version) bzw. "ein aktuelles
            # Protokoll je Termin": ein paralleler Aufruf hat die Folgeversion
            # bereits angelegt. (In der Transaktion rollt der Abbruch die
            # Abloesung selbst zurueck.)
            if session is None:
                await _abloesung_zuruecknehmen()
            raise HTTPException(409, "Korrektur läuft bereits / Version nicht "
                                     "mehr aktuell")
        except Exception:
            if session is not None:
                raise                       # Abbruch -> Rueckabwicklung, transaktion() entscheidet
            await _abloesung_zuruecknehmen()
            raise HTTPException(500, "Korrektur konnte nicht angelegt werden — "
                                     "bitte erneut versuchen (alte Version ist "
                                     "weiterhin gültig).")
        return neu

    neu = await transaktion(_abloesen_und_anlegen)
    return {k: v for k, v in neu.items() if k != "_id"}


@router.post("/driver/appointments/{appt_id}/protocol/finalize")
async def finalize_protocol(appt_id: str, body: FinalizeIn,
                            driver=Depends(current_driver)):
    """Unterschriften anhängen, ausgefülltes PDF erzeugen, Termin auf
    'abgeholt' setzen."""
    from storage_service import make_key, storage, StorageError
    appt = await _appt_or_404(appt_id, driver)
    # Review 09/2026: Der Abschluss machte auch STORNIERTE (oder als "nicht
    # abgeholt" beendete) Termine zu "abgeholt". Nur offene/verschobene
    # Termine — oder die Selbstheilung eines bereits finalen Protokolls bei
    # noch offenem Termin — duerfen hier durch.
    if (appt.get("status") or "offen") in ("storniert", "nicht abgeholt", "erledigt"):
        raise HTTPException(409, f"Termin ist '{appt.get('status')}' — ein Abschluss "
                                 "ist nicht mehr moeglich. Bitte den Haendler "
                                 "kontaktieren.")
    doc = await _current(appt_id)
    if not doc:
        raise HTTPException(400, "Bitte zuerst das Protokoll ausfüllen")
    if doc.get("status") == "final" and (doc.get("pii_geloescht_at") or not doc.get("pdf_path")):
        # Pruefung 14.09.2026 (Liste 4, Nr. 12): PDF und Unterschriften sind
        # nach der Frist geloescht — dieses Protokoll ist kein Beleg mehr, der
        # einen Termin wieder auf "abgeholt" ziehen darf.
        raise HTTPException(409, "Das unterschriebene Protokoll wurde nach Ablauf der "
                                 "Aufbewahrungsfrist bereinigt — bitte eine neue Version "
                                 "erstellen, falls der Termin erneut abgeschlossen werden soll.")
    if doc.get("status") == "final":
        # SELBSTHEILUNG (PR-Review 09/2026): Der Abschluss besteht aus
        # mehreren Schritten (Protokoll final -> Termin abgeholt ->
        # Fahrzeug-Lebenszyklus). Brach er dazwischen ab, war das
        # Protokoll final, der Termin aber offen — und jeder neue Versuch
        # scheiterte hier mit 409. Ein Wiederholungsaufruf zieht die
        # fehlenden Status nach, statt dauerhaft zu blockieren.
        # Auch wenn der Termin schon "abgeholt" ist, gibt es KEIN 409 mehr
        # (Pruefbericht 09/2026): der Aufruf ist idempotent — er stellt
        # sicher, dass protocol_id am Termin und der Fahrzeug-Lebenszyklus
        # gesetzt sind (try_set_lifecycle ist idempotent), und liefert das
        # bestehende Ergebnis. Netzabbruch oder Doppeltipp in der App
        # enden damit nicht mehr in einer Fehlermeldung.
        # Go-Live 13.09.2026 (P5-Nachbesserung): Merker nacharbeit_offen.
        setzen: Dict[str, Any] = {"protocol_id": doc["id"], **_nacharbeit_merker(appt, doc)}
        if (appt.get("status") or "") != "abgeholt":
            setzen.update({"status": "abgeholt",
                           "status_changed_at": now_iso()})
        # Runde 17 (Nr. 7): auch die Selbstheilung nur per Compare-and-Set.
        heil_out = {"ok": True, "protocol_id": doc["id"],
                    "version": doc.get("version", 1),
                    "nachgezogen": True,
                    "pdf_url": f"/api/driver/appointments/{appt_id}/protocol.pdf"}
        # Go-Live 13.09.2026 (P3): Haengt der Termin inzwischen an einem
        # ANDEREN Vertrag als beim Abschluss, nicht heilen — sonst bekaeme
        # dessen Vorgang "abgeholt" mit dem PDF des alten Vertrags.
        # Altprotokolle ohne das Feld: wie bisher.
        # Pruefung 14.09.2026 (P5): dasselbe fuer das Fahrzeug.
        zeiger = tuple(f for f in ("contract_id", "vehicle_id") if f in doc)
        if zeiger and _termin_umgehaengt({f: doc.get(f) for f in zeiger}, appt, zeiger):
            await betrieb.alarm(db, "protokoll_final_termin_umgehaengt", ref=appt_id,
                                driver_id=driver["id"], protocol_id=doc["id"])
            heil_out["hinweis"] = TERMIN_UMGEHAENGT_HINWEIS
            return heil_out
        # Pruefung 14.09.2026 (F5): Hat der Chef den Termin NACH dem Abschluss
        # bewusst wieder geoeffnet (Korrektur), darf ein alter Doppeltipp ihn
        # nicht wieder auf "abgeholt" ziehen.
        if (appt.get("status") or "offen") not in _ABGESCHLOSSEN and (
                appt.get("status_changed_at") or "") > (doc.get("finalized_at") or ""):
            heil_out["hinweis"] = TERMIN_WIEDER_OFFEN_HINWEIS
            heil_out["nachgezogen"] = False
            return heil_out
        if not await _termin_abgeholt_setzen(appt_id, driver["id"], setzen,
                                             erwartet=appt):
            heil_out["hinweis"] = TERMIN_GESCHLOSSEN_HINWEIS
            return heil_out
        # Go-Live 13.09.2026 (P5/N2): auch den nachverhandelten Preis nachziehen
        # (idempotent) — VOR der Statusuebernahme, wie im Normalpfad.
        await _preis_uebernehmen(appt, doc, nachholen=True)
        import kaufvorgang as _kv
        if not await _kv.termin_status_uebernehmen(appt, "abgeholt") and appt.get("vehicle_id"):
            await try_set_lifecycle(appt["vehicle_id"],
                                    appt.get("dealer_id", ""), "abgeholt")
        # Nr. 78: Dieser Selbstheilungspfad holte Termin, Preis und
        # Lebenszyklus nach — die Vertragsneuerzeugung fehlte vollstaendig.
        # Genau sie ist aber der Schritt, der beim Absturz uebrigbleibt.
        korrekturen, neue_schaeden = await protokoll_korrekturen(appt, doc)
        await vertrag_nach_abholung_aktualisieren(
            appt, doc["id"], doc.get("neuer_preis"), doc.get("sondervereinbarung"),
            korrekturen=korrekturen, neue_schaeden=neue_schaeden)
        await auto_daten_vor_ort_nachtragen(appt, doc, doc.get("neuer_preis"))
        # Merker zuletzt — wie im Normalpfad.
        await _nacharbeit_erledigt(appt_id, doc)
        return heil_out

    # Gegenpruefung 12.09.2026: Starb ein frueherer Abschluss mittendrin
    # (Deploy, Absturz), stand das Protokoll nach Ablauf des Claims fuer immer
    # auf "wird_abgeschlossen" — der Fahrer las "zuerst zur Freigabe
    # schicken", der Chef sah es nicht mehr. Abgelaufen = wieder freigegeben.
    if (doc.get("status") == "wird_abgeschlossen"
            and await _abgelaufene_claims_freigeben({"id": doc["id"]})):
        doc = await _current(appt_id) or doc
    # Go-Live 13.09.2026 (N1): Laeuft der Abschluss noch (gueltiger Claim),
    # las der Fahrer nach einem Abbruch (524) sonst "zuerst zur Freigabe
    # schicken" — obwohl laengst freigegeben ist.
    if doc.get("status") == "wird_abgeschlossen":
        raise HTTPException(409, WIRD_ABGESCHLOSSEN)

    # ---- Runde 30 (Wunsch Ahmad): Erst die Freigabe des Chefs ----
    # Unterschrieben wird NACH der Nachverhandlung. Ohne Freigabe gibt es
    # keine Unterschriften — sonst stuende am Ende der alte Preis im
    # Protokoll, obwohl vor Ort ein anderer vereinbart wurde.
    if doc.get("status") != FREIGEGEBEN:
        if doc.get("status") == ZUR_FREIGABE:
            raise HTTPException(409, "Das Protokoll liegt beim Händler zur "
                                     "Freigabe. Sobald er freigegeben hat, "
                                     "könnt ihr unterschreiben.")
        raise HTTPException(409, "Bitte das Protokoll zuerst zur Freigabe an "
                                 "den Händler schicken.")
    # Hat der Chef den Preis nach der Anzeige noch geaendert?
    _preis_jetzt = doc.get("neuer_preis")
    if (body.neuer_preis_gesehen is not None or _preis_jetzt is not None) and (
            body.neuer_preis_gesehen is None or _preis_jetzt is None
            or abs(float(body.neuer_preis_gesehen) - float(_preis_jetzt)) > 0.004):
        raise HTTPException(409,
                            "Der Händler hat den Preis inzwischen geändert. Bitte die Seite neu laden und den neuen Preis zeigen, bevor unterschrieben wird.")
    # Gegenpruefung 12.09.2026: auch Vermerk und Freigabe-Stand. Aenderte der
    # Chef nur den Vermerk, stand der neue Text sonst ueber Unterschriften,
    # die ihn nie gesehen hatten.
    # Pruefung 14.09.2026 (C16): Der Stand ist PFLICHT. Ohne ihn (aeltere
    # App, manipulierter Aufruf) liess sich die Pruefung umgehen und ein
    # inzwischen geaenderter Vermerk stand ueber Unterschriften, die ihn nie
    # gesehen hatten. Alle Apps schicken den Stand seit Runde 33.
    _stand_jetzt = doc.get("freigabe_stand")
    if _stand_jetzt and body.freigabe_stand_gesehen is None:
        raise HTTPException(409, STAND_FEHLT)
    if (body.freigabe_stand_gesehen is not None
            and body.freigabe_stand_gesehen != (_stand_jetzt or "")):
        raise HTTPException(409, STAND_GEAENDERT)

    # Pruefung 14.09.2026 (P1): Der Chef hat einen bestimmten Vertrags-/
    # Fahrzeugstand freigegeben. Wurde er seitdem geaendert (Vertrag im
    # Buero bearbeitet, Fahrzeugdaten korrigiert), gilt die Freigabe nicht:
    # zurueck in den Entwurf mit Rueckfrage — der Fahrer schickt neu ab.
    if doc.get("vertragswerte_stand"):
        vehicle_jetzt, contract_jetzt = await _fahrzeug_und_vertrag(appt)
        if vertragswerte_stand(vehicle_jetzt, contract_jetzt) != doc["vertragswerte_stand"]:
            jetzt_ = now_iso()
            await db.pickup_protocols.update_one(
                {"id": doc["id"], "status": FREIGEGEBEN},
                {"$set": {"status": "entwurf", "rueckfrage": VERTRAG_GEAENDERT_HINWEIS,
                          "rueckfrage_am": jetzt_, "rueckfrage_von": None,
                          "updated_at": jetzt_, "freigabe_stand": jetzt_},
                 "$unset": {"freigegeben_am": "", "freigegeben_von": "",
                            "vertragswerte_stand": ""}})
            raise HTTPException(409, VERTRAG_GEAENDERT_HINWEIS)

    # ---- Pflichtfelder: das Backend verlaesst sich NICHT auf die App ----
    _pflichtfelder_pruefen(doc, appt, seller_name=body.seller_name,
                           place=body.place)

    # ---- Abschluss ATOMAR beanspruchen: von beliebig vielen gleichzeitigen
    # Aufrufen gewinnt genau EINER (kein doppeltes PDF, keine doppelten
    # Unterschrift-Dateien). Der Claim traegt ein ABLAUFDATUM: stirbt der
    # Prozess mittendrin (Deploy, Absturz), kann der Fahrer nach 3 Minuten
    # einfach erneut abschliessen — ohne Ablauf waere das Protokoll fuer
    # immer in 'wird_abgeschlossen' gefangen (nur per Datenbank loesbar).
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    _jetzt = _dt.now(_tz.utc)
    # Gegenpruefung 12.09.2026: Der Claim verlangt ausdruecklich FREIGEGEBEN.
    # Vorher genuegte 'nicht final/nicht in Arbeit' — zog der Chef die
    # Freigabe im selben Moment zurueck (zurueck=true -> entwurf), lief der
    # Abschluss trotzdem durch und das Protokoll wurde mit dem alten Preis
    # unterschrieben.
    # Runde 33 (Gegenpruefung 12.09.2026): Der Claim verlangt auch den
    # gerade geprueften Preis. Aenderte der Chef ihn genau zwischen Pruefung
    # und Claim, stand sonst ein Preis im PDF, den der Verkaeufer nie sah.
    # Go-Live 13.09.2026 (P1/P2): Der Claim bekommt einen BESITZER. Vorher war
    # er rein zeitbasiert — nach Ablauf konnte ein zweiter Abschluss uebernehmen,
    # und der erste schrieb danach trotzdem final (PDF von A, Preis von B) bzw.
    # sein Rollback gab den Claim von B frei.
    claim_token = uuid.uuid4().hex
    claim = await db.pickup_protocols.find_one_and_update(
        {"id": doc["id"],
         "$or": [{"status": FREIGEGEBEN, "neuer_preis": _preis_jetzt,
                  # Gegenpruefung 12.09.2026: auch Vermerk und Stand
                  "preis_notiz": doc.get("preis_notiz"),
                  "freigabe_stand": _stand_jetzt},
                 {"status": "wird_abgeschlossen",
                  "claim_bis": {"$lt": _jetzt.isoformat()},
                  # Go-Live 13.09.2026 (P6-Nachbesserung): nie die alte Freigabe
                  # eines Abschlusses uebernehmen, dessen Termin geschlossen wurde.
                  TERMIN_GESCHLOSSEN_MERKER: {"$ne": True}}]},
        {"$set": {"status": "wird_abgeschlossen",
                  "claim_bis": (_jetzt + _td(minutes=3)).isoformat(),
                  "claim_token": claim_token,
                  "updated_at": now_iso()}})
    if not claim:
        akt = await db.pickup_protocols.find_one({"id": doc["id"]},
                                                 {"_id": 0, "status": 1, "neuer_preis": 1,
                                                  "preis_notiz": 1, "freigabe_stand": 1})
        if ((akt or {}).get("status") == FREIGEGEBEN
                and (akt or {}).get("neuer_preis") != _preis_jetzt):
            raise HTTPException(409,
                                "Der Händler hat den Preis inzwischen geändert. Bitte die Seite neu laden und den neuen Preis zeigen, bevor unterschrieben wird.")
        if ((akt or {}).get("status") == FREIGEGEBEN
                and ((akt or {}).get("preis_notiz") != doc.get("preis_notiz")
                     or (akt or {}).get("freigabe_stand") != _stand_jetzt)):
            raise HTTPException(409, STAND_GEAENDERT)
        if (akt or {}).get("status") in ("entwurf", ZUR_FREIGABE):
            raise HTTPException(409, "Der Händler hat die Freigabe gerade zurückgezogen — bitte die Seite neu laden.")
        raise HTTPException(409, "Das Protokoll wird gerade abgeschlossen "
                                 "— bitte einen Moment warten.")

    dealer_id = appt.get("dealer_id", "x")
    # Alle in DIESEM Abschluss geschriebenen Storage-Keys (Unterschrift-
    # PNGs, PDF). Bricht der Abschluss danach ab, werden sie im Rollback
    # wieder entfernt — vorher blieben sie verwaist im Storage liegen
    # (Pruefbericht 09/2026). Ein Key wird VOR dem Schreiben vorgemerkt,
    # damit auch eine halb geschriebene Datei aufgeraeumt wird.
    geschrieben: List[str] = []

    async def _rollback():
        """Claim freigeben, damit der Fahrer neu abschliessen kann — und
        bereits geschriebene Dateien verwerfen.

        Runde 30: zurueck auf FREIGEGEBEN (nicht auf 'entwurf'). Sonst
        haette ein gescheiterter Abschluss die Freigabe des Chefs geloescht
        und der Fahrer haette vor Ort erneut auf ihn warten muessen.

        Go-Live 13.09.2026 (P2): nur den EIGENEN Claim — ein inzwischen neu
        gestarteter Abschluss (Claim abgelaufen, uebernommen) bleibt stehen."""
        try:
            await db.pickup_protocols.update_one(
                {"id": doc["id"], "status": "wird_abgeschlossen",
                 "claim_token": claim_token, TERMIN_GESCHLOSSEN_MERKER: {"$ne": True}},
                {"$set": {"status": FREIGEGEBEN},
                 "$unset": {"claim_bis": "", "claim_token": ""}})
            # Go-Live 13.09.2026 (P6-Nachbesserung): Wurde der Termin waehrend
            # dieses Abschlusses geschlossen, gilt die alte Freigabe nicht mehr.
            await db.pickup_protocols.update_one(
                {"id": doc["id"], "status": "wird_abgeschlossen",
                 "claim_token": claim_token, TERMIN_GESCHLOSSEN_MERKER: True},
                _zuruecknehmen_aenderung(None))
        except Exception:  # noqa: BLE001
            log.exception("Protokoll-Rollback: Claim von %s konnte nicht "
                          "freigegeben werden (laeuft nach 3 Min. ab)", doc["id"])
        await _dateien_verwerfen(geschrieben, dealer_id)
        geschrieben.clear()

    def _save_sig(b64: str, who: str) -> tuple:
        """Runde 17 (Nr. 8): liefert (Storage-Key, Rohbytes) — die Bytes
        werden fuer das PDF weiterverwendet statt ein zweites Mal dekodiert."""
        try:
            raw = base64.b64decode(b64.split(",")[-1], validate=False)
            from storage_service import bild_lesbar_pruefen, validate_image_bytes
            validate_image_bytes(raw, wo=f"Unterschrift ({who})")
            # Rollentest 19.09.2026: Eine abgeschnittene Unterschrift kam bis
            # hierher durch (die ersten Bytes stimmen ja) und liess erst das
            # PDF abstuerzen — der Fahrer stand beim Verkaeufer vor einem
            # "Internen Serverfehler". Jetzt sagt der Server sofort, was zu
            # tun ist: noch einmal unterschreiben.
            bild_lesbar_pruefen(raw, wo=f"Unterschrift ({who})")
        except (ValueError, TypeError):
            raise HTTPException(400, f"Unterschrift ({who}) konnte nicht gelesen "
                                     "werden — bitte noch einmal unterschreiben")
        if not unterschrift_hat_tinte(raw):
            raise HTTPException(400, f"Unterschrift ({who}) ist leer — bitte im Feld "
                                     "unterschreiben")
        if not raw or len(raw) > 2 * 1024 * 1024:
            raise HTTPException(400, f"Unterschrift ({who}) ungültig oder zu groß")
        try:
            key = make_key("protocol", dealer_id, f"unterschrift-{who}.png")
            geschrieben.append(key)
            storage.save(key, raw)
        except StorageError as exc:
            raise HTTPException(400, f"Unterschrift konnte nicht gespeichert werden: {exc}")
        return key, raw

    import asyncio as _asyncio
    try:
        sig_driver, sig_driver_raw = await _asyncio.to_thread(
            _save_sig, body.signature_driver_b64, "fahrer")
        sig_seller, sig_seller_raw = await _asyncio.to_thread(
            _save_sig, body.signature_seller_b64, "verkaeufer")
    except Exception:
        await _rollback()
        raise

    # ---- Daten für das ausgefüllte PDF zusammenstellen ----
    vehicle: Dict[str, Any] = {}
    if appt.get("vehicle_id"):
        # WICHTIG: zusaetzlich ueber dealer_id. Fahrzeug-IDs sind
        # vorhersehbar (v_<Inserats-ID>) und nur MIT dealer_id eindeutig —
        # sonst koennte das Fahrzeug eines fremden Haendlers geladen werden.
        v = await db.vehicles.find_one(
            {"id": appt["vehicle_id"], "dealer_id": appt.get("dealer_id")},
            {"_id": 0}) or {}
        vehicle = dict(v.get("data") or {})
    contract: Dict[str, Any] = {}
    if appt.get("contract_id"):
        c = await db.generated_pdfs.find_one(
            {"id": appt["contract_id"], "dealer_id": appt.get("dealer_id")},
            {"_id": 0, "contract_data": 1}) or {}
        contract = dict(c.get("contract_data") or {})
    # Runde 24 (11.09.2026, Befund Ahmad "AUFTRAGGEBER —"): derselbe
    # Auftraggeber wie im Kaufvertrag (Kaeuferfelder bzw. Sucher-Einstellungen
    # des Erstellers), nicht mehr das nackte Firmen-Dokument.
    from auftraggeber import auftraggeber_fuer_termin
    dealer = await auftraggeber_fuer_termin(appt)

    filled = {k: doc.get(k) for k in
              ("vehicle_check", "documents", "keys_count", "keys_expected",
               "features", "condition", "damages_confirmed", "new_damages",
               "notes")}
    # Pruefung 14.09.2026 (P2): Was der Chef freigegeben hat, gilt — Ort und
    # Verkaeufername aus dem Protokoll; die App-Werte nur, wenn dort nichts steht.
    filled["place"] = doc.get("place") or body.place or ""
    filled["seller_name"] = doc.get("seller_name") or body.seller_name or appt.get("seller_name") or ""
    filled["sondervereinbarung"] = doc.get("sondervereinbarung") or ""
    filled["driver_name"] = driver.get("display_name", "")
    # Runde 30: der nachverhandelte Preis steht im unterschriebenen PDF.
    # Gegenpruefung: aus dem FRISCH beanspruchten Dokument lesen (claim),
    # nicht aus dem vor dem Claim gelesenen Stand — sonst koennten PDF und
    # Kaufvorgang auseinanderlaufen.
    _preis_final = (claim or doc).get("neuer_preis")
    filled["neuer_preis"] = _preis_final
    filled["preis_notiz"] = (claim or doc).get("preis_notiz") or ""
    filled["signature_driver"] = sig_driver_raw
    filled["signature_seller"] = sig_seller_raw
    filled["version"] = doc.get("version", 1)

    from pickup_pdf_service import build_pickup_pdf
    import asyncio as _aio
    try:
        # Im Thread: die PDF-Erzeugung ist CPU-Arbeit und wuerde sonst den
        # ganzen Worker-Prozess blockieren.
        pdf_bytes = await _aio.to_thread(
            build_pickup_pdf,
            appointment=appt, vehicle=vehicle, contract=contract,
            dealer=dealer,
            driver={"id": driver["id"], "name": driver.get("display_name"),
                    "code": driver.get("driver_code")},
            filled=filled,
        )
        pdf_key = make_key("protocol", dealer_id, "abholprotokoll.pdf")
        geschrieben.append(pdf_key)
        from storage_service import save_async
        await save_async(pdf_key, pdf_bytes)
    except StorageError as exc:
        await _rollback()
        raise HTTPException(400, f"PDF konnte nicht gespeichert werden: {exc}")
    except Exception:
        await _rollback()
        raise

    # Runde 17 (Nr. 7): Zwischen Vorabpruefung und diesem Punkt liegen
    # Dateischreiben und PDF-Erzeugung — wurde der Fahrer inzwischen aus
    # der Firma entfernt (oder die Firma gesperrt), darf das Protokoll
    # nicht mehr final werden: Verknuepfung erneut pruefen, sonst Rollback.
    try:
        await _zugriff_pruefen(appt, driver)
    except HTTPException:
        await _rollback()
        raise

    # Go-Live 13.09.2026 (P3): PDF, Kaufvorgang und Termin-Status stammen aus
    # dem Termin-Stand vom START. Wurde der Termin inzwischen an einen anderen
    # Vertrag/ein anderes Fahrzeug gehaengt (PUT ist gesperrt, direkte Writes
    # nicht), wuerden sonst zwei Vorgaenge vermischt: nicht abschliessen.
    frisch = await db.appointments.find_one(
        {"id": appt_id}, {"_id": 0, "contract_id": 1, "vehicle_id": 1})
    if frisch is None:
        # Pruefung 14.09.2026 (F1): Termin inzwischen geloescht — kein finales
        # Protokoll ohne Termin.
        await _rollback()
        raise HTTPException(409, "Der Termin wurde inzwischen gelöscht — das Protokoll "
                                 "kann nicht abgeschlossen werden.")
    if _termin_umgehaengt(appt, frisch):
        await _rollback()
        raise HTTPException(409, TERMIN_GEAENDERT)

    # Ab hier sind die Dateien im Protokoll referenziert — erst wenn DIESER
    # Schritt fehlschlaegt, waeren sie verwaist, deshalb auch hier Rollback.
    # Go-Live 13.09.2026 (P1/P2): nur mit dem EIGENEN Claim-Token und nur,
    # solange der unterschriebene Stand (Preis, Vermerk, Freigabe-Stand) gilt.
    # FREIGEGEBEN ist erlaubt: ein langsamer, aber unbestrittener Abschluss,
    # dessen Claim nur per Ablauf freigegeben wurde, endet nicht unnoetig mit
    # 409 — jede echte Aenderung (neuer Claim, Zurueckschicken, neue Freigabe,
    # Autospeichern) aendert Token, Stand oder Status.
    try:
        res = await db.pickup_protocols.update_one(
            {"id": doc["id"], "claim_token": claim_token,
             "superseded": {"$ne": True},
             "status": {"$in": ["wird_abgeschlossen", FREIGEGEBEN]},
             "neuer_preis": claim.get("neuer_preis"),
             "preis_notiz": claim.get("preis_notiz"),
             "freigabe_stand": claim.get("freigabe_stand")},
            {"$unset": {"claim_bis": "", "claim_token": "", TERMIN_GESCHLOSSEN_MERKER: ""},
             "$set": {"status": "final", "pdf_path": pdf_key,
                      # Pruefung 14.09.2026 (P3): Pruefsumme des unterschriebenen
                      # PDFs — beim Abruf gegengerechnet (wie beim Beweisdokument).
                      "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                      "signature_driver_key": sig_driver,
                      "signature_seller_key": sig_seller,
                      "seller_name": filled["seller_name"],
                      "place": filled["place"],
                      # Go-Live 13.09.2026 (P3): fuer die Selbstheilung
                      "contract_id": appt.get("contract_id"),
                      "kaufvorgang_id": appt.get("kaufvorgang_id"),
                      "finalized_at": now_iso(), "updated_at": now_iso()}})
    except Exception:
        await _rollback()
        raise
    if res.matched_count == 0:
        # Ueberholt: Status NICHT anfassen (gehoert jetzt einem anderen
        # Vorgang), nur die eigenen Dateien verwerfen.
        await _dateien_verwerfen(geschrieben, dealer_id)
        raise HTTPException(409, ABSCHLUSS_UEBERHOLT)

    # Termin + Fahrzeug-Lebenszyklus nachziehen: abgeschlossen = abgeholt.
    # Runde 17 (Nr. 7): Compare-and-Set — ein inzwischen geschlossener
    # Termin (oder ein entfernter Fahrer) bleibt unangetastet; das Protokoll
    # ist trotzdem gespeichert (Beweis), der Fahrer bekommt einen Hinweis.
    # Go-Live 13.09.2026 (P3): auch nur mit demselben Vertrag/Fahrzeug.
    termin_gesetzt = await _termin_abgeholt_setzen(
        appt_id, driver["id"],
        {"status": "abgeholt", "status_changed_at": now_iso(),
         "protocol_id": doc["id"],
         # Go-Live 13.09.2026 (P5-Nachbesserung): Sicherheitsnetz, falls Preis-
         # oder Statusuebernahme scheitern und der Fahrer nicht erneut tippt.
         **_nacharbeit_merker(appt, doc)}, erwartet=appt)
    if termin_gesetzt:
        # Runde 30 (Wunsch Ahmad): Wurde vor Ort nachverhandelt, ist DAS der
        # Preis, den die Firma wirklich zahlt. Er gehoert deshalb in den
        # Kaufvorgang — sonst stuende in Akte und Auswertung weiter der alte
        # Vertragspreis. VOR der Statusuebernahme, damit die Zusammenfassung
        # gleich den richtigen Preis ans Fahrzeug schreibt.
        # Go-Live 13.09.2026 (P5/P5-Zusatz-Korrektur): ueber denselben
        # idempotenten Helfer wie die Selbstheilung; auch Termine ohne
        # kaufvorgang_id und eine zurueckgenommene Verhandlung einer Korrektur.
        await _preis_uebernehmen(appt, {"id": doc["id"], "neuer_preis": _preis_final})
        # Umbau Kaufvorgaenge: abgeholt gilt fuer den VORGANG dieses Termins,
        # das Fahrzeug bekommt die Zusammenfassung (und den realisierten Preis).
        import kaufvorgang as _kv
        if not await _kv.termin_status_uebernehmen(appt, "abgeholt") and appt.get("vehicle_id"):
            await try_set_lifecycle(appt["vehicle_id"], dealer_id, "abgeholt")
        # Wunsch Ahmad 14.09.2026: Der Kaufvertrag wird abschliessend mit dem vor
        # Ort vereinbarten Preis und der Sondervereinbarung neu erstellt (neue
        # Fassung, alte im Archiv). Best effort — das Protokoll ist der Beleg.
        # 19.09.2026: dazu die vor Ort korrigierten Fahrzeugdaten und die neu
        # aufgenommenen Schaeden. Gab es NICHTS davon, bleibt die alte Fassung
        # unveraendert (kein neuer Vertrag ohne Anlass).
        korrekturen, neue_schaeden = await protokoll_korrekturen(appt, filled)
        await vertrag_nach_abholung_aktualisieren(
            appt, doc["id"], _preis_final, filled.get("sondervereinbarung"),
            korrekturen=korrekturen, neue_schaeden=neue_schaeden)
        # Nachpruefung 20.09.2026, Nr. 78: Der Merker wurde bisher SCHON VOR
        # der Vertragsneuerzeugung entfernt. Starb der Prozess dazwischen,
        # sah die Selbstheilung ein finales Protokoll, heilte Termin, Preis
        # und Lebenszyklus — und die neue Vertragsfassung fehlte fuer immer.
        # Jetzt faellt der Merker erst, wenn wirklich alles erledigt ist.
        await _nacharbeit_erledigt(appt_id, doc)
        # Wunsch Ahmad 15.09.2026: Preis vor Ort und Maengel des Fahrers in die
        # Auto-Daten (eigene Spalten, der Einkaufspreis des Vertrags bleibt).
        await auto_daten_vor_ort_nachtragen(appt, filled, _preis_final)
    # Pruefung 14.09.2026 (C8): Protokoll und Termin sind fertig — kein 500 mehr
    # durch einen scheiternden Audit-Eintrag.
    await log_activity_sicher(dealer_id, driver["id"], "abholprotokoll.abgeschlossen",
                              ref=appt.get("vehicle_id"),
                              meta={"version": doc.get("version", 1),
                                    "appointment_id": appt_id,
                                    "termin_gesetzt": termin_gesetzt})
    out: Dict[str, Any] = {
        "ok": True, "protocol_id": doc["id"], "version": doc.get("version", 1),
        "pdf_url": f"/api/driver/appointments/{appt_id}/protocol.pdf"}
    if not termin_gesetzt:
        out["hinweis"] = TERMIN_GESCHLOSSEN_HINWEIS
    return out


@router.get("/driver/appointments/{appt_id}/protocol.pdf")
async def driver_protocol_pdf(appt_id: str, driver=Depends(current_driver)):
    """Das ausgefüllte, abgeschlossene Protokoll als PDF (Fahrer-Ansicht)."""
    await _appt_or_404(appt_id, driver)
    doc = await _current(appt_id)
    if not doc or doc.get("status") != "final":
        # Pruefung 14.09.2026 (C9): Waehrend einer Korrektur (neue Version im
        # Entwurf) ist die letzte UNTERSCHRIEBENE Version weiter das gueltige
        # Dokument — vorher 404, obwohl das PDF existierte.
        doc = await db.pickup_protocols.find_one(
            {"appointment_id": appt_id, "status": "final"}, {"_id": 0},
            sort=[("version", -1)])
    if not doc or doc.get("status") != "final" or not doc.get("pdf_path"):
        raise HTTPException(404, "Noch kein abgeschlossenes Protokoll vorhanden")
    # Pruefung 14.09.2026 (P10): nach einer Neuzuteilung bekommt der neue
    # Fahrer nicht das vom Vorgaenger unterschriebene PDF.
    if doc.get("driver_account_id") and doc["driver_account_id"] != driver["id"]:
        raise HTTPException(404, "Noch kein abgeschlossenes Protokoll vorhanden")
    return await _protokoll_pdf_antwort(doc)


async def _protokoll_pdf_antwort(doc: dict):
    """Pruefung 14.09.2026 (C10/C20): Personendaten — nie im Browser-Cache
    (no-store, wie Abholauftrag und Vertrag); eine vorgemerkte, noch nicht
    nachgeholte Dateiloeschung (pdf_path_loeschung_offen) liefert 410 statt
    das PDF weiter auszugeben."""
    from fastapi import Response
    if doc.get("pdf_path_loeschung_offen"):
        raise HTTPException(410, "Das Protokoll-PDF wurde nach Ablauf der "
                                 "Aufbewahrungsfrist gelöscht.")
    from storage_service import load_async, StorageError
    try:
        data = await load_async(doc["pdf_path"])
    except StorageError:
        raise HTTPException(404, "PDF nicht gefunden")
    # Pruefung 14.09.2026 (P3): veraenderte/vertauschte Datei im Speicher nie
    # als unterschriebenes Protokoll ausliefern.
    soll = str(doc.get("pdf_sha256") or "")
    ist = hashlib.sha256(data).hexdigest()
    if soll and ist != soll:
        log.error("Protokoll %s: PDF-Pruefsumme weicht ab (gespeichert %s, Datei %s)",
                  doc.get("id"), soll[:12], ist[:12])
        await betrieb.alarm(db, "protokoll_pdf_pruefsumme_abweichend", ref=str(doc.get("id")),
                            pdf_path=str(doc.get("pdf_path") or ""), soll=soll, ist=ist)
        raise HTTPException(409, "Die gespeicherte Datei stimmt nicht mit der Prüfsumme des "
                                 "unterschriebenen Protokolls überein — bitte den Betreiber "
                                 "informieren.")
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="Abholprotokoll.pdf"',
                             "Cache-Control": "no-store",
                             "X-Protokoll-SHA256": ist})


async def _protokoll_im_bereich(user: dict, doc: dict) -> bool:
    """Umbau Kaufvorgaenge 09.09.2026: das Protokoll gehoert zum TERMIN und
    damit zum Kaufvorgang eines Suchers — das gemeinsame Fahrzeug gibt
    keinen Zugriff mehr (Mitbearbeiter sehen sonst Verkaeufer/Unterschrift
    des Kollegen)."""
    appt = await db.appointments.find_one(
        {"id": doc.get("appointment_id"), "dealer_id": user["dealer_id"]},
        {"_id": 0, "created_by": 1, "contract_id": 1, "kaufvorgang_id": 1})
    return bool(appt) and await termin_im_bereich(user, appt)


# Audit 13.09.2026 (#2): Obergrenze nur gegen Ausreisser — mehr als 20
# Versionen je Fahrzeug sind selten, aber moeglich (vorher still gekappt).
_PROTOKOLLE_JE_FAHRZEUG = 200


# ---------- Händler-Sicht ----------
@router.get("/vehicles/{vehicle_id}/protocols")
async def dealer_list_protocols(vehicle_id: str, user=Depends(_dealer_dep),
                                response: Response = None):
    """Alle Protokoll-Versionen eines Fahrzeugs (Händler/Chef)."""
    # Runde 16: Sucher sehen darin nur die Protokolle der EIGENEN Termine
    # (termin_bereich). Befund 94 (16.09.2026): kein Vorfilter mehr ueber den
    # Fahrzeug-Bereich — ein eigener Termin/Vertrag zum Fahrzeug genuegt (wie
    # _protokoll_im_bereich); ohne eigenen Termin bleibt die Liste leer.
    filt: Dict[str, Any] = {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
                            "status": "final"}
    if ist_sucher(user):
        # Audit 13.09.2026 (#1): erst auf die EIGENEN Termine eingrenzen, DANN
        # begrenzen (wie die Akte). Vorher wurden firmenweit 20 Versionen
        # geladen und erst danach je Dokument gefiltert — 20 fremde Versionen
        # verdraengten das eigene Protokoll still. Regel wie
        # _protokoll_im_bereich (Termin per id + Firma, termin_bereich).
        termin_ids = [t for t in await db.pickup_protocols.distinct("appointment_id", filt) if t]
        filt["appointment_id"] = {"$in": await db.appointments.distinct(
            "id", {"id": {"$in": termin_ids}, **await termin_bereich(user)})}
    # Audit 13.09.2026 (#2): nach Abschlusszeit statt nach Versionsnummer
    # (jeder Termin zaehlt ab 1 — ein neues v1 fiel hinter alte Korrekturen)
    # und eine Obergrenze nur gegen Ausreisser, mit Kopf und Warnung.
    docs = await db.pickup_protocols.find(
        filt,
        {"_id": 0, "id": 1, "version": 1, "finalized_at": 1, "driver_name": 1,
         "seller_name": 1, "place": 1, "corrects_version": 1, "superseded": 1,
         "appointment_id": 1},
    ).sort([("finalized_at", -1), ("version", -1)]).to_list(_PROTOKOLLE_JE_FAHRZEUG + 1)
    # Nachbesserung: eins mehr holen — genau an der Grenze ist nichts abgeschnitten.
    if len(docs) > _PROTOKOLLE_JE_FAHRZEUG:
        docs = docs[:_PROTOKOLLE_JE_FAHRZEUG]
        log.warning("Protokollliste Fahrzeug %s: Obergrenze %d erreicht, aeltere "
                    "Versionen abgeschnitten", vehicle_id, _PROTOKOLLE_JE_FAHRZEUG)
        if response is not None:
            response.headers["X-Truncated"] = "1"
    for d in docs:
        d.pop("appointment_id", None)
    return docs


def _abweichungen(doc: dict, werte: Optional[Dict[str, Any]] = None) -> List[dict]:
    """Worueber laesst sich verhandeln? (Runde 30)

    Runde 33: ueber protokoll_vergleich — mit dem Wert laut Vertrag und dem
    Wert vor Ort. Ja/Nein-Zeilen zaehlen nur, wenn der Vertrag etwas anderes
    sagt ("Gewerbliche Nutzung: Nein" war vorher faelschlich eine Abweichung);
    ein alter Korrekturwert bei "stimmt" zaehlt nicht."""
    zeilen = PV.vergleich(VEHICLE_CHECK_FIELDS, doc.get("vehicle_check") or {},
                          doc.get("condition") or {}, werte or {})
    return PV.abweichungen(zeilen)


_FREIGABE_STATI = [ZUR_FREIGABE, FREIGEGEBEN]
# Obergrenze gegen Ausreisser; realistisch warten nur wenige gleichzeitig.
# Pruefung 14.09.2026 (Nr. 13): 500 war zu knapp gedacht — ab der Grenze fielen
# die am laengsten wartenden Fahrer still aus der Liste. Jetzt 5000 und ein
# Betriebsalarm, sobald eine Firma die Grenze erreicht.
_FREIGABE_MAX = 5000

STAND_GEAENDERT = ("Der Händler hat Preis oder Vermerk inzwischen geändert. Bitte die "
                   "Seite neu laden und dem Verkäufer den neuen Stand zeigen, bevor "
                   "unterschrieben wird.")
# Pruefung 14.09.2026 (C7/C16): ohne Stand keine Freigabe / kein Abschluss.
STAND_FEHLT = ("Die App hat den Freigabe-Stand nicht mitgeschickt — bitte die Seite neu "
               "laden und die Freigabe erneut anzeigen, bevor unterschrieben wird.")
STAND_FEHLT_FREIGABE = ("Die Freigabe-Ansicht ist veraltet — bitte die Seite neu laden und "
                        "erneut freigeben.")

# Gegenpruefung 12.09.2026: Der Zaehler im Menue fragt alle 20 s je offenem
# Tab — dafuer nur die Felder, die er braucht, nicht ganze Protokolle.
_ZAEHLER_FELDER = {"_id": 0, "id": 1, "status": 1, "appointment_id": 1, "vehicle_id": 1,
                   "abgeschickt_am": 1, "erstmals_abgeschickt_am": 1}


async def _abgelaufene_claims_freigeben(bedingung: Dict[str, Any]) -> int:
    """Gegenpruefung 12.09.2026: Ein Abschluss, der mittendrin starb (Deploy,
    Absturz), hinterliess "wird_abgeschlossen" mit abgelaufenem Claim. Das
    Protokoll verschwand aus Liste und Zaehler, der Chef las dauerhaft "wird
    gerade unterschrieben". Nach Ablauf gilt wieder die Freigabe."""
    from datetime import datetime as _dt, timezone as _tz
    abgelaufen = {**bedingung, "status": "wird_abgeschlossen",
                  "claim_bis": {"$lt": _dt.now(_tz.utc).isoformat()}}
    # Go-Live 13.09.2026 (P6-Nachbesserung): Wurde der Termin waehrend dieses
    # Abschlusses geschlossen (oder wiedergeoeffnet), gilt die alte Freigabe
    # nicht mehr — zurueck in den Entwurf statt auf freigegeben.
    zurueck = await db.pickup_protocols.update_many(
        {**abgelaufen, TERMIN_GESCHLOSSEN_MERKER: True}, _zuruecknehmen_aenderung(None))
    res = await db.pickup_protocols.update_many(
        {**abgelaufen, TERMIN_GESCHLOSSEN_MERKER: {"$ne": True}},
        {"$set": {"status": FREIGEGEBEN}, "$unset": {"claim_bis": ""}})
    return res.modified_count + zurueck.modified_count


async def _wartende_protokolle(user, felder: Optional[Dict[str, int]] = None) -> List[tuple]:
    """Alle Protokolle, die beim Chef liegen — zur Freigabe oder freigegeben,
    noch nicht unterschrieben.

    Runde 33 (Analyse 12.09.2026, Wunsch Ahmad "mehrere Fahrer gleichzeitig"):
      * das am LAENGSTEN wartende zuerst (vorher: neueste zuerst)
      * keine harte Grenze von 50 VOR dem Sucher-Filter mehr — ein Sucher
        konnte seine eigenen verlieren, die am laengsten wartenden fielen raus
      * Protokolle zu abgeschlossenen oder geloeschten Terminen blieben fuer
        immer stehen — jetzt nicht mehr
      * Termine gebuendelt geladen statt einzeln je Protokoll
    Gegenpruefung 12.09.2026: Die Grenze griff ohne Sortierung — liegen
    gebliebene Protokolle geschlossener Termine (die aeltesten) verdraengten
    die Fahrer, die JETZT warten. Geladen wird deshalb das zuletzt Abgeschickte
    zuerst; angezeigt weiter das am laengsten Wartende oben."""
    await _abgelaufene_claims_freigeben({"dealer_id": user["dealer_id"]})
    docs = await db.pickup_protocols.find(
        {"dealer_id": user["dealer_id"], "status": {"$in": _FREIGABE_STATI},
         "superseded": {"$ne": True}},
        felder or {"_id": 0, "pdf_path": 0},
    ).sort("abgeschickt_am", -1).to_list(_FREIGABE_MAX)
    if len(docs) >= _FREIGABE_MAX:
        log.warning("Freigaben: Firma %s hat %s oder mehr offene Protokolle — die aeltesten "
                    "werden nicht gezeigt", user["dealer_id"], _FREIGABE_MAX)
        try:
            await betrieb.alarm(db, "freigaben_liste_abgeschnitten", ref=user["dealer_id"],
                                meta={"max": _FREIGABE_MAX})
        except Exception:
            log.exception("Alarm freigaben_liste_abgeschnitten nicht abgesetzt")
    termin_ids = sorted({d.get("appointment_id") for d in docs if d.get("appointment_id")})
    termine: Dict[str, dict] = {}
    if termin_ids:
        async for t in db.appointments.find(
                {"id": {"$in": termin_ids}, "dealer_id": user["dealer_id"]},
                {"_id": 0, "id": 1, "status": 1, "pickup_date": 1, "pickup_time": 1,
                 "pickup_address": 1, "seller_name": 1, "vehicle_id": 1,
                 "contract_id": 1, "created_by": 1, "kaufvorgang_id": 1}):
            termine[t["id"]] = t
    paare = []
    for d in docs:
        appt = termine.get(d.get("appointment_id"))
        if not appt or (appt.get("status") or "offen") in _ABGESCHLOSSEN:
            continue
        # Runde 16/29: Sucher sehen nur ihre eigenen Vorgaenge.
        if ist_sucher(user) and not await termin_im_bereich(user, appt):
            continue
        paare.append((d, appt))
    paare.sort(key=lambda p: str(p[0].get("erstmals_abgeschickt_am")
                                 or p[0].get("abgeschickt_am") or ""))
    return paare


def _eur(preis) -> str:
    try:
        return f"{float(preis):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"
    except (TypeError, ValueError):
        return ""


@router.get("/protocols/zur-freigabe/anzahl")
async def protokolle_zur_freigabe_anzahl(user=Depends(_chef_dep)):
    """Runde 33: Zaehler fuers Menue — der Chef merkt auf jeder Seite, dass
    ein Fahrer beim Verkaeufer auf seine Freigabe wartet."""
    paare = await _wartende_protokolle(user, _ZAEHLER_FELDER)
    wartend = [d for d, _a in paare if d.get("status") == ZUR_FREIGABE]
    return {"wartet": len(wartend),
            "freigegeben": sum(1 for d, _a in paare if d.get("status") == FREIGEGEBEN),
            # Gegenpruefung 12.09.2026: fuer den Hinweis "neues Protokoll" — die
            # Zahl allein bleibt gleich, wenn eins freigegeben und eins
            # abgeschickt wird.
            "ids": [d.get("id") for d in wartend]}


@router.get("/protocols/zur-freigabe")
async def protokolle_zur_freigabe(user=Depends(_chef_dep)):
    """Runde 30 (Wunsch Ahmad): Was wartet gerade auf die Freigabe des Chefs?

    Liefert das ausgefuellte Protokoll — Vergleich Vertrag/vor Ort, neue
    Schaeden, Kilometerstand — damit der Chef entscheiden (und notfalls
    beim Verkaeufer anrufen) kann, ohne das ganze PDF zu oeffnen.
    Runde 33: fuer die eigene Seite "Freigaben" (siehe _wartende_protokolle)."""
    paare = await _wartende_protokolle(user)
    fz_ids = sorted({d.get("vehicle_id") or a.get("vehicle_id") for d, a in paare} - {None, ""})
    fahrzeuge: Dict[str, dict] = {}
    if fz_ids:
        # Zusaetzlich ueber dealer_id — Fahrzeug-IDs koennen firmenuebergreifend gleich sein.
        async for v in db.vehicles.find({"id": {"$in": fz_ids}, "dealer_id": user["dealer_id"]},
                                        {"_id": 0, "id": 1, "data": 1}):
            fahrzeuge[v["id"]] = v.get("data") or {}
    vertrag_ids = sorted({a.get("contract_id") for _d, a in paare} - {None, ""})
    vertraege: Dict[str, dict] = {}
    if vertrag_ids:
        async for c in db.generated_pdfs.find({"id": {"$in": vertrag_ids}, "dealer_id": user["dealer_id"]},
                                              {"_id": 0, "id": 1, "contract_data": 1}):
            vertraege[c["id"]] = c.get("contract_data") or {}
    namen = await besitzer_namen(user["dealer_id"],
                                 [d.get("freigegeben_von") for d, _a in paare]
                                 + [d.get("rueckfrage_von") for d, _a in paare])
    raus = []
    for d, appt in paare:
        # Gegenpruefung 12.09.2026: Infinity/NaN aus einem einzigen Protokoll
        # liess die ganze Liste scheitern — und damit JEDE Freigabe der Firma.
        d = PV.json_sicher(d)
        try:
            fahrzeug = fahrzeuge.get(d.get("vehicle_id") or appt.get("vehicle_id")) or {}
            vertrag = vertraege.get(appt.get("contract_id")) or {}
            werte = PV.vertragswerte(fahrzeug, vertrag)
            zeilen = PV.vergleich(VEHICLE_CHECK_FIELDS, d.get("vehicle_check") or {},
                                  d.get("condition") or {}, werte)
            km = PV.zahl((d.get("condition") or {}).get("mileage"))
            name = " ".join(str(x) for x in (fahrzeug.get("make_label") or fahrzeug.get("make"),
                                             fahrzeug.get("model_label") or fahrzeug.get("model")) if x)
            raus.append({
                "protocol_id": d["id"],
                "appointment_id": d.get("appointment_id"),
                "vehicle_id": d.get("vehicle_id"),
                "status": d.get("status"),
                # Runde 33: Stand fuer die Konfliktpruefung bei der Freigabe.
                "stand": d.get("freigabe_stand") or d.get("updated_at"),
                "fahrzeug": name,
                "abholung": " ".join(x for x in (appt.get("pickup_date"), appt.get("pickup_time")) if x),
                "abholort": appt.get("pickup_address") or "",
                "verkaeufer": d.get("seller_name") or appt.get("seller_name") or "",
                "fahrer": d.get("driver_name") or "",
                "abgeschickt_am": d.get("abgeschickt_am"),
                "erstmals_abgeschickt_am": d.get("erstmals_abgeschickt_am") or d.get("abgeschickt_am"),
                "kilometerstand": (d.get("condition") or {}).get("mileage") or "",
                "kilometerstand_text": PV.km_text(km) if km is not None else "",
                "abweichungen": PV.abweichungen(zeilen),
                "vergleich": zeilen,
                "neue_schaeden": d.get("new_damages") or [],
                "schaeden_bestaetigt": d.get("damages_confirmed"),
                "bemerkungen": d.get("notes") or "",
                # Abnahme 12.09.2026: Der Chef bekam nur einen Auszug. Er soll
                # das GANZE ausgefuellte Protokoll sehen koennen, bevor er
                # freigibt — Haken bei Dokumenten und Ausstattung inklusive.
                "dokumente": d.get("documents") or {},
                "ausstattung": d.get("features") or {},
                "zustand": d.get("condition") or {},
                "schluessel": d.get("keys_count") or "",
                "schluessel_vereinbart": d.get("keys_expected") or "",
                "fahrzeugdaten": d.get("vehicle_check") or {},
                "ort": d.get("place") or "",
                "preis_vertrag": vertrag.get("purchase_price"),
                "neuer_preis": d.get("neuer_preis"),
                "preis_notiz": d.get("preis_notiz") or "",
                # Wunsch Ahmad 14.09.2026: vor Ort vom Fahrer eingetragen.
                "preis_vorschlag_fahrer": d.get("preis_vorschlag") or None,
                "sondervereinbarung": d.get("sondervereinbarung") or "",
                "freigegeben_von_name": namen.get(d.get("freigegeben_von")) or "",
                "rueckfrage_von_name": namen.get(d.get("rueckfrage_von")) or "",
            })
        except Exception:  # noqa: BLE001
            log.exception("Freigaben: Protokoll %s liess sich nicht aufbereiten", d.get("id"))
            raus.append({
                "protocol_id": d.get("id"), "appointment_id": d.get("appointment_id"),
                "vehicle_id": d.get("vehicle_id"), "status": d.get("status"),
                "stand": d.get("freigabe_stand") or d.get("updated_at"),
                "fahrzeug": "", "abholort": appt.get("pickup_address") or "",
                "abholung": " ".join(x for x in (appt.get("pickup_date"), appt.get("pickup_time")) if x),
                "verkaeufer": d.get("seller_name") or appt.get("seller_name") or "",
                "fahrer": d.get("driver_name") or "",
                "abgeschickt_am": d.get("abgeschickt_am"),
                "erstmals_abgeschickt_am": d.get("erstmals_abgeschickt_am") or d.get("abgeschickt_am"),
                "abweichungen": [], "vergleich": [], "neue_schaeden": [],
                "neuer_preis": d.get("neuer_preis"), "preis_notiz": d.get("preis_notiz") or "",
                "ladefehler": "Dieses Protokoll enthält ungültige Werte — bitte beim Fahrer "
                              "nachfragen oder an ihn zurückschicken.",
            })
    return raus


async def _freigabe_konflikt(protocol_id: str, user: dict) -> str:
    """Warum ging die Freigabe nicht durch? Mit Name und Preis — damit am
    Telefon niemand einen Preis nennt, den ein Kollege schon ueberschrieben hat."""
    akt = await db.pickup_protocols.find_one(
        {"id": protocol_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "status": 1, "neuer_preis": 1, "freigegeben_von": 1, "rueckfrage_von": 1})
    if not akt:
        return "Das Protokoll gibt es nicht mehr."
    status = akt.get("status")
    if status == "wird_abgeschlossen":
        return "Vor Ort wird gerade unterschrieben — der Preis lässt sich jetzt nicht mehr ändern."
    if status == "final":
        return "Das Protokoll ist bereits abgeschlossen."
    wer_id = akt.get("freigegeben_von") if status == FREIGEGEBEN else akt.get("rueckfrage_von")
    # Gegenpruefung 12.09.2026: nicht sich selbst als "jemand anderes" nennen.
    if wer_id and wer_id == user.get("id"):
        if status == FREIGEGEBEN:
            return "Du hast dieses Protokoll bereits freigegeben — die Liste zeigt jetzt den aktuellen Stand."
        if status == "entwurf":
            return "Du hast das Protokoll bereits an den Fahrer zurückgeschickt."
    wer = (await besitzer_namen(user["dealer_id"], [wer_id])).get(wer_id) if wer_id else ""
    wer = wer or "jemand anderes"
    if status == FREIGEGEBEN:
        preis = (f" mit {_eur(akt['neuer_preis'])}" if akt.get("neuer_preis") is not None
                 else " zum Vertragspreis")
        return f"Inzwischen hat {wer}{preis} freigegeben — bitte die Liste prüfen."
    if status == "entwurf":
        return f"Inzwischen hat {wer} das Protokoll an den Fahrer zurückgeschickt."
    return "Der Stand hat sich gerade geändert — bitte neu laden."


@router.post("/protocols/{protocol_id}/freigabe")
async def protokoll_freigeben(protocol_id: str, body: FreigabeIn,
                              user=Depends(_chef_dep)):
    """Runde 30: Der Chef gibt das abgeschickte Protokoll frei — mit dem
    neuen Preis, falls er nachverhandelt hat. Oder er schickt es zurueck,
    wenn der Fahrer etwas nachtragen soll.

    Erst nach der Freigabe kann vor Ort unterschrieben werden.
    Runde 33: Stand-Pruefung, Preis zuruecksetzen, klare Meldungen."""
    # Abgelaufener Claim (Abschluss abgestuerzt): wieder freigegeben, damit der
    # Chef nicht dauerhaft "wird gerade unterschrieben" liest.
    await _abgelaufene_claims_freigeben({"id": protocol_id, "dealer_id": user["dealer_id"]})
    doc = await db.pickup_protocols.find_one(
        {"id": protocol_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Protokoll nicht gefunden")
    if ist_sucher(user) and not await _protokoll_im_bereich(user, doc):
        raise HTTPException(404, "Protokoll nicht gefunden")
    if doc.get("status") == "final":
        raise HTTPException(409, "Das Protokoll ist bereits abgeschlossen.")
    if doc.get("status") == "wird_abgeschlossen":
        raise HTTPException(409, "Vor Ort wird gerade unterschrieben — der Preis "
                                 "lässt sich jetzt nicht mehr ändern.")
    if doc.get("status") not in _FREIGABE_STATI:
        raise HTTPException(409, "Der Fahrer hat das Protokoll noch nicht "
                                 "abgeschickt.")
    appt = await db.appointments.find_one(
        {"id": doc.get("appointment_id"), "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not appt or (appt.get("status") or "offen") in _ABGESCHLOSSEN:
        raise HTTPException(409, "Der Termin ist bereits abgeschlossen oder gelöscht — "
                                 "eine Freigabe ist nicht mehr möglich.")
    if not body.zurueck:
        # Pruefung 14.09.2026 (Liste 4, Nr. 7/8): Freigegeben wird nur ein
        # vollstaendiges, gueltiges Protokoll — dieselbe Pruefung wie beim
        # Abschicken. Ein beschaedigtes Protokoll (Ladefehler in der Liste)
        # laesst sich so nicht freigeben; es geht zurueck an den Fahrer.
        vehicle_fr, _contract_fr = await _fahrzeug_und_vertrag(appt)
        try:
            _pflichtfelder_pruefen(doc, appt, vollstaendig=True,
                                   ausstattung=list((vehicle_fr.get("features") or [])[:AUSSTATTUNG_MAX]))
        except HTTPException as exc:
            raise HTTPException(422, "Das Protokoll ist nicht vollständig oder enthält "
                                     f"ungültige Werte ({exc.detail}) — bitte an den "
                                     "Fahrer zurückschicken.")
    jetzt = now_iso()
    bedingung: Dict[str, Any] = {"id": protocol_id, "status": {"$in": _FREIGABE_STATI}}
    # Pruefung 14.09.2026 (C7): Der Stand ist PFLICHT — ohne ihn gewann bei
    # zwei gleichzeitigen Freigaben (Chef und Sucher, verschiedene Preise)
    # stillschweigend der Letzte. Die Oberflaeche schickt ihn seit Runde 33.
    # Protokolle von vor dieser Fassung (ohne Stand) wie bisher: mit Stand
    # gegen updated_at, ohne Stand ungeprueft.
    if doc.get("freigabe_stand"):
        if not body.stand:
            raise HTTPException(409, STAND_FEHLT_FREIGABE)
        # Gegenpruefung 12.09.2026: gegen den Freigabe-Stand, nicht updated_at —
        # den aendern auch Claim und Rollback eines gescheiterten Abschlusses,
        # und der Chef las dann von einer "fremden" Freigabe.
        bedingung["freigabe_stand"] = body.stand
    elif body.stand:
        bedingung["updated_at"] = body.stand
    else:
        # Befund 58 (16.09.2026): Altbestand ohne freigabe_stand und ohne
        # Client-Stand — dann gilt der eben GELESENE Stand als Bedingung;
        # zwei gleichzeitige Freigaben gewinnen nicht mehr beide.
        bedingung["updated_at"] = doc.get("updated_at")

    if body.zurueck:
        res = await db.pickup_protocols.update_one(
            bedingung,
            {"$set": {"status": "entwurf", "rueckfrage": (body.notiz or "").strip(),
                      "rueckfrage_am": jetzt, "rueckfrage_von": user["id"],
                      "updated_at": jetzt, "freigabe_stand": jetzt},
             "$unset": {"freigegeben_am": "", "freigegeben_von": ""}})
        if not res.matched_count:
            raise HTTPException(409, await _freigabe_konflikt(protocol_id, user))
        # Pruefung 14.09.2026 (C8): Die Freigabe ist geschrieben — ein
        # scheiternder Audit-Eintrag gab 500 und die Oberflaeche liess den
        # Chef wiederholen (dann Konflikt 409, weil der Stand neu ist).
        await log_activity_sicher(user["dealer_id"], user["id"],
                                  "protokoll.zurueck_an_fahrer", ref=protocol_id,
                                  meta={"notiz": (body.notiz or "")[:200]})
        return {"ok": True, "status": "entwurf", "stand": jetzt}

    if body.preis_zuruecksetzen:
        zuruecksetzen: Dict[str, Any] = {"updated_at": jetzt, "freigabe_stand": jetzt}
        if doc.get("status") == FREIGEGEBEN:
            # Gegenpruefung 12.09.2026: Wer den Preis zuruecksetzt, bestimmt jetzt,
            # was unterschrieben wird — Liste und Konfliktmeldung nannten sonst
            # den, der vorher mit dem verhandelten Preis freigegeben hatte.
            zuruecksetzen.update({"freigegeben_von": user["id"], "freigegeben_am": jetzt})
        res = await db.pickup_protocols.update_one(
            bedingung,
            # Befund 51 (16.09.2026): auch die Preisquelle faellt weg — sonst stand
            # "fahrer" ohne Preis.
            {"$set": zuruecksetzen, "$unset": {"neuer_preis": "", "preis_notiz": "",
                                               "preis_quelle": ""}})
        if not res.matched_count:
            raise HTTPException(409, await _freigabe_konflikt(protocol_id, user))
        await log_activity_sicher(user["dealer_id"], user["id"],
                                  "protokoll.preis_zurueckgesetzt",
                                  ref=protocol_id, meta={"vorher": doc.get("neuer_preis")})
        return {"ok": True, "status": doc.get("status"), "neuer_preis": None, "stand": jetzt}

    setzen: Dict[str, Any] = {"status": FREIGEGEBEN, "freigegeben_am": jetzt,
                              "freigegeben_von": user["id"], "updated_at": jetzt,
                              "freigabe_stand": jetzt}
    if body.neuer_preis is not None:
        setzen["neuer_preis"] = float(body.neuer_preis)
        # Befund 51 (16.09.2026): der Chef hat den Preis bestimmt — nicht mehr
        # der Fahrer-Vorschlag, auch wenn der vorher galt.
        setzen["preis_quelle"] = "chef"
    elif doc.get("neuer_preis") is None and doc.get("preis_vorschlag"):
        # Wunsch Ahmad 14.09.2026: Der Fahrer hat vor Ort einen Preis
        # eingetragen und der Chef gibt ohne eigenen Preis frei — dann gilt
        # der Vorschlag des Fahrers.
        setzen["neuer_preis"] = float(doc["preis_vorschlag"])
        setzen["preis_quelle"] = "fahrer"
    if body.notiz is not None:
        setzen["preis_notiz"] = body.notiz.strip()
    res = await db.pickup_protocols.update_one(
        bedingung,
        {"$set": setzen, "$unset": {"rueckfrage": "", "rueckfrage_am": ""}})
    if not res.matched_count:
        raise HTTPException(409, await _freigabe_konflikt(protocol_id, user))
    await log_activity_sicher(user["dealer_id"], user["id"], "protokoll.freigegeben",
                              ref=protocol_id,
                              meta={"neuer_preis": body.neuer_preis,
                                    "notiz": (body.notiz or "")[:200]})
    return {"ok": True, "status": FREIGEGEBEN,
            "neuer_preis": setzen.get("neuer_preis", doc.get("neuer_preis")),
            "stand": jetzt}


@router.get("/protocols/{protocol_id}.pdf")
async def dealer_protocol_pdf(protocol_id: str, user=Depends(_dealer_dep)):
    doc = await db.pickup_protocols.find_one(
        {"id": protocol_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not doc or not doc.get("pdf_path"):
        raise HTTPException(404, "Protokoll nicht gefunden")
    # Runde 16: Sucher nur im eigenen Bereich (Fahrzeug oder Termin).
    if ist_sucher(user) and not await _protokoll_im_bereich(user, doc):
        raise HTTPException(404, "Protokoll nicht gefunden")
    return await _protokoll_pdf_antwort(doc)
