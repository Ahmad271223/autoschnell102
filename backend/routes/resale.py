"""Weiterverkauf: Inseratsentwürfe, Margen-Rechner, Status-Workflow.

Phase 1: entwurf → verkaufsbereit (kein Marktplatz, kein Kontingent).
Phase 3 ergänzt: veroeffentlicht (erst DANN zählt das Monatskontingent,
pro Inserat nur einmal je Abrechnungszeitraum — `counted_periods`).

`data` ist bewusst eine KOPIE der Fahrzeugdaten: spätere Änderungen an der
Fahrzeugakte dürfen ein bestehendes Inserat nicht unbemerkt verändern.
"""
import base64
import logging
import math
import re
import uuid
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from dateien import signierte_datei_url   # signierte Foto-Links (Audit 09/2026)
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, StringConstraints, field_validator

from deps import (clean_doc, current_user, db, log_activity, log_activity_sicher,
                  now_iso)
from konfig import zahl_env
from lifecycle import (ALLOWED_TRANSITIONS, LifecycleError, set_lifecycle,
                       try_set_lifecycle)
from routes.bestand import current_haendler, _clean_costs

log = logging.getLogger("autohandel")
router = APIRouter()
# Runde 17 (Nr. 289): create_draft nutzt set_lifecycle; die Best-effort-
# Variante bleibt am Modul verfuegbar (Aufrufer/Tests patchen den Namen).
_LIFECYCLE_BEST_EFFORT = try_set_lifecycle

# Nachpruefung Runde 14 (Nr. 28/29/67/89/90): verkaufte und geloeschte
# Inserate sind abgeschlossen — keine Bearbeitung, keine Fotos rein/raus.
# Verkaufte Inserate sind Beweis-Historie (Fotos, Preise, Maengel), geloeschte
# duerfen nicht "durch die Hintertuer" weiterleben.
_ABGESCHLOSSEN = ("verkauft", "geloescht")
# Nachpruefung Runde 14 (Nr. 106): alles, was noch kein Abschluss ist, gilt
# als aktives Inserat — je Fahrzeug darf es davon nur EINES geben.
_AKTIV = ("entwurf", "verkaufsbereit", "veroeffentlicht", "reserviert",
          "zurueckgezogen")
# Ein Foto ist im Storage auf MAX_IMAGE_BYTES (8 MB) begrenzt; Base64 ist
# 4/3 so gross, plus Data-URL-Praefix. Nachpruefung Runde 14 (Nr. 117):
# ohne Deckel je Einzelstring wurden 25-MB-Bloecke erst dekodiert und dann
# verworfen — jetzt scheitert der Riesenblock schon an der Validierung.
_B64_MAX_LEN = 12_000_000

# ---------------------------------------------------------------------
# Weiterverkauf, Regeln vom 20.09.2026 (Ahmad, vor der Wiederaktivierung
# des Marktplatzes):
#
#   * Die Fahrzeugdaten kommen weiter aus dem Einkauf und duerfen
#     angepasst werden — daran aendert sich nichts.
#   * Die Beschreibung ist auf 500 Zeichen begrenzt (vorher 30.000).
#   * Hoechstens 10 Fotos je Inserat (vorher 40).
#   * Fotos werden NICHT mehr aus dem Portal-Inserat uebernommen. Sie
#     muessen neu hochgeladen werden — die Bilder des Verkaeufers gehoeren
#     ihm bzw. dem Portal, sie auf dem eigenen Marktplatz weiterzunutzen
#     waere ein urheberrechtliches Risiko. Bestehende Inserate behalten
#     ihre alten Einkaufsfotos (nichts wird rueckwirkend geloescht), neue
#     bekommen gar keine mehr.
#   * Ein Inserat laeuft hoechstens INSERAT_LAUFZEIT_TAGE (21) und
#     verschwindet danach samt Fotos von selbst. Kaufvertrag, Kaufvorgang
#     und Fahrzeugakte bleiben davon unberuehrt — geloescht wird nur die
#     Verkaufsanzeige.
INSERAT_BESCHREIBUNG_MAX = zahl_env("INSERAT_BESCHREIBUNG_MAX", 500,
                                    unten=100, oben=30000)
INSERAT_FOTOS_MAX = zahl_env("INSERAT_FOTOS_MAX", 10, unten=1, oben=40)
INSERAT_LAUFZEIT_TAGE = zahl_env("INSERAT_LAUFZEIT_TAGE", 21, unten=1, oben=365)


# ---------- Models ----------
# Nachpruefung Runde 14 (Nr. 80): ohne allow_inf_nan=False nahm Pydantic
# Infinity/1e400 an; der Wert landete in Mongo und jede JSON-Antwort mit dem
# Inserat (auch die Liste der Firma) brach danach mit 500 ab.
class ListingUpdateIn(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    # 20.09.2026: 500 statt 30.000 Zeichen (Ahmad). Die Oberflaeche zeigt
    # einen Zaehler; Altbestand mit laengerem Text bleibt lesbar, beim
    # naechsten Speichern gilt aber die neue Grenze.
    description: Optional[str] = Field(default=None,
                                       max_length=INSERAT_BESCHREIBUNG_MAX)
    # Runde 17 (Nr. 336/337): Einzelmangel und Kostenliste schon in der
    # Validierung gedeckelt — vorher lief ein Megabyte-String je Mangel bis
    # zum Kuerzen auf 300 Zeichen durch, die Kosten wurden erst in
    # _clean_costs auf 30 geschnitten.
    known_defects: Optional[List[Annotated[str, StringConstraints(max_length=300)]]] = \
        Field(default=None, max_length=50)
    photo_mode: Optional[Literal["einkauf", "neu", "beide"]] = None
    price_public: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    price_b2b: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    price_network: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    costs: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=30)
    data: Optional[Dict[str, Any]] = None  # korrigierte Fahrzeugdaten
    # Rollenprüfung 22.09.2026 (RP-463): Stand (updated_at), den die
    # Oberflaeche geladen hat. Ein veralteter Tab schickte bisher seinen
    # alten Preis und alte Fahrzeugdaten mit und setzte den Live-Stand still
    # zurueck. Mit Stand: weicht er ab -> 409 "bitte neu laden". Ohne Angabe
    # wie bisher (API-Aufrufer, Tests).
    stand: Optional[str] = Field(default=None, max_length=64)


#: Nachpruefung 20.09.2026, Nr. 53: Jedes einzelne Bild hatte eine Grenze,
#: die GANZE Anfrage aber nicht — 20 x 12 MB waeren 240 MB gewesen, die
#: FastAPI vor jeder Pruefung komplett einlesen und als JSON auseinander-
#: nehmen muss. nginx laesst 25 MB je Anfrage durch (deploy/nginx.conf);
#: mehr kann hier gar nicht ankommen, also ist das die ehrliche Grenze.
#:
#: Die ANZAHL bleibt bei 20 (wie bisher): der Befund zielt auf die
#: Gesamtgroesse, nicht auf die Stueckzahl — 20 kleine Bilder sind harmlos,
#: und eine schaerfere Zahl waere eine Verhaltensaenderung der Schnittstelle
#: ohne Not gewesen. Die Oberflaeche schickt ohnehin Pakete zu vier.
# Nr. 7 (20.09.2026): 24 Mio. Base64-Zeichen liegen mit dem Rest der Anfrage
# dicht an der nginx-Grenze (client_max_body_size 25m) — wird sie gerissen,
# antwortet der Proxy mit 413, bevor das Backend etwas davon merkt. Gleicher
# Abstand wie beim Fahrer-Upload.
PHOTOS_GESAMT_MAX = 20_000_000
PHOTOS_JE_ANFRAGE_MAX = 20


class PhotoUploadIn(BaseModel):
    photos_b64: List[Annotated[str, StringConstraints(max_length=_B64_MAX_LEN)]] = \
        Field(min_length=1, max_length=PHOTOS_JE_ANFRAGE_MAX)

    @field_validator("photos_b64")
    @classmethod
    def _gesamtgroesse(cls, v):
        gesamt = sum(len(x) for x in v)
        if gesamt > PHOTOS_GESAMT_MAX:
            raise ValueError(
                f"Die Fotos sind zusammen zu gross ({gesamt // 1_000_000} MB). "
                f"Bitte in kleineren Gruppen hochladen — die Oberflaeche "
                f"verkleinert sie vorher und schickt sie in Paketen.")
        return v


class ListingStatusIn(BaseModel):
    status: Literal["entwurf", "verkaufsbereit", "reserviert", "verkauft",
                    "zurueckgezogen"]
    sold_price: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    # Rollenprüfung 22.09.2026 (RP-492): der Status, den die Oberflaeche
    # ANGEZEIGT hat. Ein veralteter Tab ("veroeffentlicht") konnte sonst mit
    # "Verkauft" die inzwischen entstandene Reservierung eines B2B-Kaeufers
    # abschliessen oder mit "Reservieren" ein "bereits reserviert" fuer die
    # eigene Reservierung halten. Weicht er ab -> 409. Ohne Angabe wie bisher.
    von_status: Optional[Literal["entwurf", "verkaufsbereit", "veroeffentlicht",
                                 "reserviert", "verkauft", "zurueckgezogen",
                                 "geloescht"]] = None


# Audit 13.09.2026 (#17): ListingUpdateIn.data ist bewusst ein freies Dict
# (Inserat.jsx schickt das komplette l.data, alle Werte als Text aus den
# Eingabefeldern). Die Whitelist pruefte aber nur die SCHLUESSEL: ein
# `{"mileage": 1e400}` landete als inf in Mongo, und danach brach
# /marktplatz/listings fuer jeden Besucher mit 500 ab (JSON kennt kein inf);
# Objekte in Textfeldern legten die Marktplatz-Seite lahm, Megabyte-Listen in
# features blaehten jede Antwort auf. Jetzt je Feld Typ, Endlichkeit und
# Laenge; ungueltige Werte werden verworfen (alter Wert bleibt), zu lange
# Texte und Listen gekuerzt. Bewusst KEIN 422 und keine bool-Pflicht fuer
# accident_free ("Ja"/"Nein"/"" aus der Oberflaeche und den Vertraegen).
_DATEN_TEXT_MAX = {"make_label": 100, "model_label": 150, "model_description": 300,
                   "first_registration": 20, "fuel_label": 50, "gearbox_label": 50,
                   "color": 80, "vin": 30, "previous_owners": 10,
                   # Beschreibung wie ListingUpdateIn.description: importierte
                   # Portaltexte duerfen beim Speichern nicht still schrumpfen
                   "description": 30000, "accident_free": 20}
_DATEN_ZAHL = ("mileage", "power_kw", "power_ps")
_DATEN_ZAHL_TEXT_MAX = 30
_FEATURES_MAX, _FEATURE_LEN = 150, 200
_INT64 = 2 ** 63


def _zahl_ok(val: Any) -> bool:
    """Endliche Zahl, die BSON speichern kann (bool zaehlt hier als Zahl —
    Ja/Nein-Felder wie accident_free brauchen das; die ZAHLENFELDER lehnen
    bool in _fahrzeugwert_bereinigen ab, Pruefung 14.09.2026 F14)."""
    if isinstance(val, bool):
        return True
    if isinstance(val, int):
        return -_INT64 <= val < _INT64
    if isinstance(val, float):
        return math.isfinite(val)
    return False


# Rollenprüfung 22.09.2026 (RP-523): "150 Tkm", "150k", "150 Tsd" oder
# "12,5 tausend" meinen TAUSEND Kilometer. Vorher liess die Pruefung bis zu
# sechs Buchstaben als "Einheit" zu, und auto_daten._zahl nahm die Zahl davor —
# aus "150 Tkm" wurden still 150 km: Kaeufer sahen 150 km, und der km-Filter
# des Marktplatzes zeigte das Auto faelschlich an. Jetzt: Tausender-Einheiten
# mal 1000, sonst ist beim Kilometerstand nur "km" als Einheit erlaubt; alles
# andere wird verworfen (alter Wert bleibt, wie bei jedem unlesbaren Feld).
_KM_TAUSEND = re.compile(
    r"^(\d{1,4}(?:[.,]\d{1,3})?)\s*"
    r"(?:t\s*km|tkm|tsd\.?\s*km|tsd\.?|tausend\s*km|tausend|t|k)\.?$",
    re.IGNORECASE)
_KM_NUR_KM = re.compile(r"^[\d.,\s]+(?:km|kilometer)?\.?$", re.IGNORECASE)
#: Wie frontend/src/lib/preis.js (KM_HOECHSTENS)
KM_HOECHSTENS = 2_000_000


def _km_wert(text: str) -> Optional[int]:
    """Kilometerstand aus Text; None, wenn nicht eindeutig lesbar."""
    s = str(text or "").strip().replace("\u00a0", " ").replace("\u202f", " ")
    if not s:
        return None
    m = _KM_TAUSEND.match(s)
    if m:
        zahl: Optional[int] = int(round(float(m.group(1).replace(",", ".")) * 1000))
    elif _KM_NUR_KM.match(s):
        from auto_daten import _zahl
        zahl = _zahl(s)
    else:
        return None
    if zahl is None or zahl < 0 or zahl > KM_HOECHSTENS:
        return None
    return zahl


def _fahrzeugwert_bereinigen(k: str, val: Any) -> Tuple[bool, Any]:
    """Audit 13.09.2026 (#17): (uebernehmen?, bereinigter Wert) fuer ein Feld
    aus ListingUpdateIn.data."""
    if val is None:
        return True, None
    if k == "features":
        if not isinstance(val, list):
            return False, None
        out = []
        for f in val:
            if len(out) >= _FEATURES_MAX:
                break
            if isinstance(f, str) or (isinstance(f, (int, float))
                                      and not isinstance(f, bool) and _zahl_ok(f)):
                out.append(str(f)[:_FEATURE_LEN])
        return True, out
    if k in _DATEN_ZAHL:
        if isinstance(val, str):
            s = val.strip()
            if len(s) > _DATEN_ZAHL_TEXT_MAX:
                return False, None
            if not s:
                # Rollenprüfung 22.09.2026 (RP-507): ein geleertes Zahlenfeld
                # als None speichern statt "" — der Marktplatz zeigte "" als
                # "0 km", der km-Filter ("ohne Angabe zaehlt als unter dem
                # Maximum", {"data.mileage": None}) liess es aus, und die
                # Sortierung ($ifNull) stellte es nicht ans Ende.
                return True, None
            if k == "mileage":
                # Rollenprüfung 22.09.2026 (RP-523): eigene Regel fuer den
                # Kilometerstand (Tausender-Einheiten, nur "km" als Einheit).
                km = _km_wert(s)
                return (True, km) if km is not None else (False, None)
            # Pruefung 14.09.2026 (F15): Freitext in Zahlenfeldern ablehnen —
            # die Marktplatzfilter rechnen mit $gte/$lte auf diesen Feldern.
            # '150.000 km' und '110,5' werden als Zahl uebernommen, 'viel' nicht.
            from auto_daten import _zahl
            zahl = _zahl(s)
            if zahl is None or not re.match(r"^\s*-?[\d.,\s]+\s*[A-Za-z€/²³]{0,6}\s*$", s):
                return False, None
            return True, zahl
        # Pruefung 14.09.2026 (F14): true/false ist kein Kilometerstand
        return (True, val) if (_zahl_ok(val) and not isinstance(val, bool)) else (False, None)
    grenze = _DATEN_TEXT_MAX.get(k)
    if grenze is None:
        return False, None
    if isinstance(val, str):
        return True, val[:grenze]
    return (True, val) if _zahl_ok(val) else (False, None)


# ---------- Helpers ----------
# Rollenprüfung 22.09.2026 (RP-505): model_description ist die Titelzeile des
# PRIVATVERKAEUFERS aus dem Portal — oft die Variante ("Golf VII 1.4 TSI
# Highline"), manchmal aber mit Telefonnummer, Mailadresse oder Link. Der
# Marktplatz gibt das Feld nicht mehr aus; landete es im vorgeschlagenen
# Inseratstitel, stand die Nummer trotzdem oeffentlich da. Jetzt nur noch,
# wenn die Zeile kurz ist und nichts nach Kontaktangabe aussieht.
_KONTAKT_IM_TEXT = re.compile(
    r"@|https?:|www\.|\.(?:de|com|net|eu|info)\b"
    r"|\b(?:tel|telefon|handy|mobil|whats\s*app|anruf\w*|kontakt|e-?mail|mail)\b"
    r"|(?:\+?\d[\s/\-.()]*){7,}",
    re.IGNORECASE)
_MODELLZUSATZ_MAX = 80


def _modellzusatz(desc: Any) -> str:
    """Modellbezeichnung fuer den Titelvorschlag — "" bei Kontaktangaben
    oder Werbetext (zu lang); der Haendler kann den Titel selbst ergaenzen."""
    text = " ".join(str(desc or "").split())
    if not text or len(text) > _MODELLZUSATZ_MAX or _KONTAKT_IM_TEXT.search(text):
        return ""
    return text


def _build_title(data: dict) -> str:
    model = data.get("model_label") or data.get("model") or ""
    desc = _modellzusatz(data.get("model_description"))
    # Doppelung vermeiden, wenn die Modellbezeichnung den Modellnamen enthält.
    if model and desc and desc.lower().startswith(model.lower()):
        model = ""
    bits = [
        data.get("make_label") or data.get("make") or "",
        model,
        desc,
        data.get("gearbox_label") or "",
    ]
    feats = [f for f in (data.get("features") or [])
             if any(k in str(f).lower() for k in ("led", "navi", "pano", "ahk", "leder"))]
    title = " ".join(b for b in bits if b).strip()
    if feats:
        title += " " + " ".join(str(f).split("/")[0].strip() for f in feats[:3])
    return title[:200] or "Fahrzeug"


def _beschreibung_kuerzen(text: str, grenze: int) -> str:
    """Auf `grenze` Zeichen kuerzen — an einer Zeilen-, Aufzaehlungs- oder
    Wortgrenze, mit "…" am Ende (die Laenge inklusive "…" bleibt <= grenze)."""
    text = (text or "").strip()
    if len(text) <= grenze:
        return text
    schnitt = text[:grenze - 1]
    for trenner in ("\n", ", ", " "):
        pos = schnitt.rfind(trenner)
        if pos >= grenze // 2:
            schnitt = schnitt[:pos]
            break
    return schnitt.rstrip(" ,;:\n") + "…"


def _build_description(data: dict) -> str:
    """Vorschlag fuer die Inserats-Beschreibung.

    Rollenprüfung 22.09.2026 (RP-036/087/135/186/286/337/525/526): Vorher
    entstanden hier bis zu 30.000 Zeichen — Eckdaten, 40 Ausstattungszeilen,
    bis zu 5.000 Zeichen ORIGINALTEXT DES PRIVATVERKAEUFERS aus dem Portal
    und die Maengel. Seit dem 20.09. erlaubt der Server aber nur
    INSERAT_BESCHREIBUNG_MAX (500) Zeichen: fast jeder frische Entwurf war zu
    lang, und jedes Speichern (auch "Verkaufsbereit machen" und
    "Veroeffentlichen") endete mit 422. Ausserdem stand der fremde
    Portaltext woertlich im Haendlerinserat, auch fuer anonyme Besucher
    (Urheberrecht wie bei den Portal-Fotos, dazu Telefonnummern u. ae. des
    Verkaeufers), und die Eckdaten im Text widersprachen den korrigierten
    Fahrzeugdaten (der Kaeufer sieht die Daten strukturiert aus `data`).

    Jetzt nur noch die Ausstattung als eine Zeile, gekuerzt auf die Grenze:
      * KEIN Portaltext des Verkaeufers (bleibt nur intern in data).
      * KEINE Eckdaten (km, EZ, PS ...): die zeigt die Kaeuferansicht aus
        den (korrigierbaren) Fahrzeugdaten — kein zweiter, veraltender Stand.
      * KEINE Maengel: die stehen als eigene Liste im Inserat.
    Der Haendler schreibt den Rest selbst."""
    feats = [str(f).strip() for f in (data.get("features") or []) if str(f).strip()]
    if not feats:
        return ""
    return _beschreibung_kuerzen("Ausstattung: " + ", ".join(feats),
                                 INSERAT_BESCHREIBUNG_MAX)


def _beschreibung_zu_lang(l: dict) -> Optional[str]:
    """Rollenprüfung 22.09.2026 (RP-087/186/337): die 500er-Grenze galt nur
    beim PUT. Altbestand (Entwuerfe von vor dem 20.09.) und der direkte
    API-Weg (PUT nur mit Preis) brachten laengere Texte live. Liefert die
    deutsche Meldung, wenn die gespeicherte Beschreibung zu lang ist."""
    laenge = len(l.get("description") or "")
    if laenge <= INSERAT_BESCHREIBUNG_MAX:
        return None
    return (f"Die Beschreibung ist {laenge} Zeichen lang, erlaubt sind "
            f"{INSERAT_BESCHREIBUNG_MAX}. Bitte kürzen und speichern.")


def _margin(listing: dict) -> dict:
    """Berechnet Kosten + erwartete Marge für die Anzeige."""
    purchase = listing.get("purchase_price") or 0
    costs = sum(c.get("amount", 0) for c in (listing.get("costs") or []))
    price = (listing.get("prices") or {}).get("public") or 0
    total_cost = round(purchase + costs, 2)
    # Rollenprüfung 22.09.2026 (RP-057 b): Einkaufspreis offen (mehrere
    # Kaufvertraege mit verschiedenen Preisen) — keine Summe/Marge mit 0 €
    # Einkauf, das saehe nach einem viel zu hohen Rohertrag aus.
    offen = (listing.get("purchase_price") is None
             and listing.get("purchase_price_quelle") == "mehrdeutig")
    return {
        "purchase_price": None if offen else purchase,
        "purchase_price_quelle": listing.get("purchase_price_quelle"),
        "costs_total": round(costs, 2),
        "total_cost": None if offen else total_cost,
        "expected_margin": round(price - total_cost, 2) if price and not offen else None,
    }


def _with_margin(listing: dict) -> dict:
    listing["margin"] = _margin(listing)
    return listing


# ---------- Fahrzeug-Lebenszyklus (Nachpruefung Runde 14, Nr. 52/53/81) ----------
# Vorher lief jeder Inserats-Statuswechsel ueber try_set_lifecycle, das den
# LifecycleError schluckt: Inserat wechselte, Fahrzeug blieb haengen (z.B.
# dauerhaft "reserviert" ohne Inserat — kein neuer Entwurf mehr moeglich).
# Jetzt wird der Weg VOR dem Schreiben geprueft (409, wenn es keinen gibt)
# und danach mit set_lifecycle gesetzt, damit Fehler sichtbar bleiben.
#
# lifecycle.py kennt aus "reserviert" nur verkauft/veroeffentlicht. Die
# Freigabe einer Reservierung (Inserat reserviert -> verkaufsbereit bzw.
# Loeschen -> bestand) laeuft deshalb ueber den erlaubten Zwischenschritt
# "veroeffentlicht" — zwei Audit-Eintraege, aber kein stiller Desync.
# Fahrzeuge VOR dem Verkaufsblock (Altbestand ohne Entwurfs-Hook) holen den
# Schritt "verkaufsentwurf" nach; von dort aus fuehrt kein Weg direkt zu
# veroeffentlicht/reserviert — ein echter Desync bleibt also ein 409.
_LIFECYCLE_ZWISCHENSCHRITT = {
    "reserviert": "veroeffentlicht",
    "vertrag_erstellt": "verkaufsentwurf",
    "gekauft": "verkaufsentwurf",
    "abholung_geplant": "verkaufsentwurf",
    "abgeholt": "verkaufsentwurf",
    "bestand": "verkaufsentwurf",
}
# Nur in diesen Fahrzeugzustaenden gehoert das Fahrzeug "dem Inserat"; beim
# Loeschen eines Inserats wird sonst nichts zurueckgesetzt (Fahrzeug wurde
# z.B. schon archiviert/geloescht — ein 409 waere dann eine Sackgasse).
_RESALE_LIFECYCLES = ("verkaufsentwurf", "verkaufsbereit", "veroeffentlicht",
                      "reserviert")


def _lifecycle_pfad(current: Optional[str], ziel: str) -> List[str]:
    """Schrittfolge vom Fahrzeugstatus `current` zum Ziel; leer, wenn schon
    erreicht. Wirft LifecycleError, wenn weder direkt noch ueber den
    erlaubten Zwischenschritt ein Weg existiert (reine Funktion, testbar)."""
    current = current or "verglichen"
    if current == ziel:
        return []
    direkt = ALLOWED_TRANSITIONS.get(current, set())
    if ziel in direkt:
        return [ziel]
    zwischen = _LIFECYCLE_ZWISCHENSCHRITT.get(current)
    if zwischen and zwischen in direkt \
            and ziel in ALLOWED_TRANSITIONS.get(zwischen, set()):
        return [zwischen, ziel]
    raise LifecycleError(f"Übergang '{current}' → '{ziel}' ist nicht erlaubt")


async def _zustand_vor_abholung(vehicle_id: str, dealer_id: str) -> Optional[str]:
    """RP-518: Fahrzeugzustand VOR der Abholung, wenn das Fahrzeug noch nicht
    abgeholt ist, aber ein Kauf offen ist (wie kaufvorgang.
    fahrzeug_status_aggregieren: abholung_geplant > gekauft). Sonst None
    (abgeholt, Preis am Fahrzeug, oder gar kein offener Kauf -> "bestand")."""
    v = await db.vehicles.find_one({"id": vehicle_id, "dealer_id": dealer_id},
                                   {"_id": 0, "id": 1, "purchase_price": 1,
                                    "abgeholt_kaufvorgang_id": 1})
    if v is None or v.get("purchase_price") is not None or v.get("abgeholt_kaufvorgang_id"):
        return None
    grund = {"vehicle_id": vehicle_id, "dealer_id": dealer_id}
    if await db.kaufvorgaenge.find_one({**grund, "status": "abgeholt"}, {"_id": 0, "id": 1}):
        return None
    if await db.kaufvorgaenge.find_one({**grund, "status": "abholung_geplant"},
                                       {"_id": 0, "id": 1}):
        return "abholung_geplant"
    if await db.kaufvorgaenge.find_one({**grund, "status": {"$in": ["vertrag_erstellt",
                                                                   "gesendet"]}},
                                       {"_id": 0, "id": 1}):
        return "gekauft"
    return None


def _pfad_zurueck(current: Optional[str], ziel: str) -> Optional[List[str]]:
    """RP-518: kuerzester erlaubter Weg aus dem Verkaufsblock zu `ziel`
    (Breitensuche ueber ALLOWED_TRANSITIONS, nur ueber Verkaufszustaende).
    None, wenn lifecycle.py keinen Weg kennt."""
    start = current or "verglichen"
    if start == ziel:
        return []
    erlaubt_ueber = set(_RESALE_LIFECYCLES)
    offen: List[Tuple[str, List[str]]] = [(start, [])]
    gesehen = {start}
    while offen:
        zustand, weg = offen.pop(0)
        for nach in sorted(ALLOWED_TRANSITIONS.get(zustand, set())):
            if nach == ziel:
                return weg + [nach]
            if nach in erlaubt_ueber and nach not in gesehen and len(weg) < 4:
                gesehen.add(nach)
                offen.append((nach, weg + [nach]))
    return None


async def _fahrzeug_lifecycle(vehicle_id: str, dealer_id: str) -> Optional[str]:
    """Aktueller Lebenszyklus des Fahrzeugs; None, wenn es das Fahrzeug nicht
    (mehr) gibt — dann ist nichts zu synchronisieren."""
    v = await db.vehicles.find_one({"id": vehicle_id, "dealer_id": dealer_id},
                                   {"_id": 0, "lifecycle": 1})
    if not v:
        return None
    return v.get("lifecycle") or "verglichen"


async def _lifecycle_pfad_oder_409(vehicle_id: Optional[str], dealer_id: str,
                                   ziel: str) -> List[str]:
    """Weg zum Ziel ermitteln, bevor am Inserat geschrieben wird."""
    if not vehicle_id:
        return []
    cur = await _fahrzeug_lifecycle(vehicle_id, dealer_id)
    if cur is None:
        return []
    try:
        return _lifecycle_pfad(cur, ziel)
    except LifecycleError as exc:
        raise HTTPException(409, "Fahrzeugstatus passt nicht zum Inserat: "
                                 f"{exc}")


async def _lifecycle_anwenden(vehicle_id: str, dealer_id: str,
                              pfad: List[str], user: dict) -> None:
    """Pruefung 14.09.2026 (Nr. 9): ein mehrstufiger Weg (z.B. abgeholt ->
    verkaufsentwurf -> veroeffentlicht) wurde Schritt fuer Schritt geschrieben;
    scheiterte der zweite Schritt, blieb das Fahrzeug im Zwischenzustand,
    waehrend das Inserat schon zurueckgesetzt war. Jetzt EIN Write mit
    Compare-and-Set auf den gelesenen Stand; das Audit haelt jeden Schritt
    einzeln fest."""
    if not pfad:
        return
    if len(pfad) == 1:
        # Ein Schritt: wie bisher (Compare-and-Set in set_lifecycle).
        await set_lifecycle(vehicle_id, dealer_id, pfad[0], user=user)
        return
    v = await db.vehicles.find_one({"id": vehicle_id, "dealer_id": dealer_id},
                                   {"_id": 0, "lifecycle": 1})
    if not v:
        raise LifecycleError("Fahrzeug nicht gefunden")
    current = v.get("lifecycle") or "verglichen"
    zustand, schritte = current, []
    for schritt in pfad:
        if schritt == zustand:
            continue
        if schritt not in ALLOWED_TRANSITIONS.get(zustand, set()):
            raise LifecycleError(f"Übergang '{zustand}' → '{schritt}' ist nicht erlaubt")
        schritte.append((zustand, schritt))
        zustand = schritt
    if not schritte:
        return
    ziel = pfad[-1]
    cas = {"id": vehicle_id, "dealer_id": dealer_id,
           "lifecycle": v["lifecycle"] if "lifecycle" in v else {"$exists": False}}
    r = await db.vehicles.update_one(cas, {"$set": {
        "lifecycle": ziel, "lifecycle_changed_at": now_iso(), "updated_at": now_iso()}})
    if r.matched_count == 0:
        raise LifecycleError("Fahrzeugstatus wurde zwischenzeitlich geändert — bitte neu laden")
    for von, nach in schritte:
        await log_activity_sicher(dealer_id, (user or {}).get("id", ""),
                                  f"fahrzeug.status.{nach}", ref=vehicle_id,
                                  meta={"von": von, "nach": nach})


async def _anfragen_schliessen(listing_id: str, grund: str, *,
                               auch_akzeptierte: bool = False) -> int:
    """Nachpruefung Runde 14 (Nr. 54/26): Kaufanfragen eines Inserats
    beenden, das verkauft/geloescht/zurueckgezogen wird. Vorher blieben
    Verhandlungen auf geloeschten Inseraten dauerhaft "laufend" (beide
    Seiten konnten weiter kontern), und nach dem Aufheben einer Reservierung
    sah der Kaeufer weiter "fuer dich reserviert" (Status akzeptiert)."""
    from routes.marketplace import INTERESSE_OFFEN
    stati = list(INTERESSE_OFFEN)
    if auch_akzeptierte:
        stati.append("akzeptiert")
    res = await db.listing_interest.update_many(
        {"listing_id": listing_id, "status": {"$in": stati}},
        {"$set": {"status": "abgelehnt", "beendet_grund": grund,
                  "updated_at": now_iso()},
         "$push": {"history": {"von": "system", "aktion": grund,
                               "zeit": now_iso()}}})
    return res.modified_count


# =========================================================
#                 ENTWURF ERZEUGEN
# =========================================================
@router.post("/resale/draft/{vehicle_id}")
async def create_draft(vehicle_id: str, user=Depends(current_haendler)):
    """Erzeugt aus Fahrzeugakte + Abholbericht einen fertigen Inserats-
    entwurf (Titel, Beschreibung, Ausstattung, Fotos, bekannte Mängel).
    Existiert bereits ein aktiver Entwurf, wird dieser zurückgegeben."""
    v = await db.vehicles.find_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not v:
        raise HTTPException(404, "Fahrzeug nicht gefunden")
    # Rollenprüfung 22.09.2026 (RP-083/182): "gibt es schon ein aktives
    # Inserat?" und das Anlegen waren zwei getrennte Schritte ohne Sperre.
    # Zwei gleichzeitige Aufrufe (zweiter Tab, Doppelklick an zwei Geraeten)
    # legten zwei Inserate an — steht das Fahrzeug schon auf
    # "verkaufsentwurf", ist der Lebenszyklus-Schritt ein No-op und haelt
    # nichts auf. Zwei Live-Inserate hiessen zwei Reservierungen bzw. ein
    # Doppelverkauf. Jetzt serialisiert eine kurze Sperre am Fahrzeug
    # (mit Besitzer-Token wie beim Veroeffentlichen); der zweite Aufruf wartet
    # kurz und bekommt dann das eben angelegte Inserat zurueck.
    sperre = await _entwurf_sperre_holen(vehicle_id, user["dealer_id"])
    if sperre is None:
        raise HTTPException(409, "Für dieses Fahrzeug wird gerade ein Inserat "
                                 "angelegt — bitte kurz warten und neu laden.")
    try:
        # Unter der Sperre frisch lesen (der Stand von eben kann veraltet sein).
        v = await db.vehicles.find_one(
            {"id": vehicle_id, "dealer_id": user["dealer_id"]}, {"_id": 0}) or v
        # Nachpruefung Runde 14 (Nr. 106): vorher wurden nur entwurf/verkaufs-
        # bereit gesucht — ueber "zurueckgezogen" (Fahrzeug wieder verkaufsbereit)
        # entstand ein zweites Inserat, und beide liessen sich veroeffentlichen.
        # Jetzt zaehlt jedes aktive Inserat: reaktivierbare werden zurueck-
        # gegeben, live/reservierte blockieren mit 409. Diese Pruefung steht VOR
        # der Lebenszyklus-Pruefung, damit ein bestehendes Inserat nie hinter
        # einer irrefuehrenden "nicht verkaufsfaehig"-Meldung verschwindet.
        existing = await db.resale_listings.find_one(
            {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
             "status": {"$in": list(_AKTIV)}}, {"_id": 0})
        if existing:
            if existing.get("status") in ("veroeffentlicht", "reserviert"):
                raise HTTPException(409, "Fuer dieses Fahrzeug gibt es bereits ein "
                                         f"aktives Inserat (Status "
                                         f"'{existing['status']}')")
            return _with_margin(existing)

        if v.get("lifecycle") not in ("vertrag_erstellt", "gekauft",
                                      "abholung_geplant", "abgeholt", "bestand",
                                      "verkaufsentwurf", "verkaufsbereit"):
            raise HTTPException(400, "Fahrzeug ist nicht im verkaufsfähigen Zustand "
                                     f"(Status: {v.get('lifecycle')})")
        # Runde 17 (Nr. 289): den Weg zum Fahrzeugstatus "verkaufsentwurf" VOR
        # dem Insert pruefen (409 statt Entwurf ohne passendes Fahrzeug) — wie
        # in publish_listing. Vorher schluckte try_set_lifecycle den Fehler.
        pfad = await _lifecycle_pfad_oder_409(vehicle_id, user["dealer_id"],
                                              "verkaufsentwurf")

        data = dict(v.get("data") or {})
        # Rollenprüfung 22.09.2026 (RP-506): AutoScout legt den Kraftstoff roh
        # als Text in data.fuel ab ("Elektro/Benzin"). Der Marktplatzfilter
        # sucht zuerst ueber den Code — deshalb gleich beim Anlegen normieren
        # (Unerkanntes bleibt, wie es ist; der Filter nimmt dann die Beschriftung).
        _kraftstoff_normieren(data)

        # Abweichungen aus dem Abholbericht automatisch einarbeiten.
        # Nachpruefung Runde 14 (Nr. 45): find_one ohne Termin-/Versionsbezug
        # nahm bei mehreren Terminen den aeltesten Bericht (Kilometer/Schaeden
        # des falschen Termins). Der massgebliche Bericht (abgeholter Termin,
        # sonst juengster; hoechste Version) kommt aus abholbericht.py.
        report = None
        try:
            from abholbericht import massgeblicher_bericht
        except ImportError:            # Modul noch nicht ausgeliefert: alter Weg
            report = await db.pickup_reports.find_one(
                {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
                 "superseded": {"$ne": True}}, {"_id": 0})
        else:
            report = await massgeblicher_bericht(db, vehicle_id, user["dealer_id"])
        known_defects = list(v.get("known_defects") or [])
        auto_notes = []
        if report:
            if report.get("mileage_at_pickup"):
                old_km = data.get("mileage")
                data["mileage"] = report["mileage_at_pickup"]
                if old_km and old_km != report["mileage_at_pickup"]:
                    auto_notes.append(
                        f"Kilometerstand wurde von {old_km} auf "
                        f"{report['mileage_at_pickup']} aktualisiert (Abholung).")
            for d in report.get("deviations", []):
                if d.get("field") in ("damage", "tires", "warning_light", "other",
                                      "equipment", "documents"):
                    txt = d.get("label") or "Abweichung"
                    if d.get("actual"):
                        txt += f": {d['actual']}"
                    if txt not in known_defects:
                        known_defects.append(txt)
        # Rollenprüfung 22.09.2026 (RP-474): Kilometerstand und neue Schaeden
        # aus dem UNTERSCHRIEBENEN Abholprotokoll kamen nie ins Inserat —
        # gelesen wurde nur der freiwillige Abhol-Check (pickup_reports). Das
        # Protokoll ist vom Verkaeufer unterschrieben und geht deshalb vor.
        befund = await _protokoll_befund(vehicle_id, user["dealer_id"])
        if befund.get("km") is not None:
            old_km = data.get("mileage")
            data["mileage"] = befund["km"]
            if old_km not in (None, "") and old_km != befund["km"]:
                auto_notes.append(
                    f"Kilometerstand wurde von {old_km} auf {befund['km']} "
                    f"aktualisiert (unterschriebenes Abholprotokoll).")
        for txt in befund.get("schaeden") or []:
            if txt not in known_defects:
                known_defects.append(txt)
        if befund.get("schaeden"):
            auto_notes.append(f"{len(befund['schaeden'])} neue(r) Schaden/Schäden aus dem "
                              f"Abholprotokoll unter „Bekannte Mängel“ übernommen.")

        abgeholt_vorgang = await db.kaufvorgaenge.find_one(
            {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"], "status": "abgeholt"},
            {"_id": 0, "id": 1, "purchase_price": 1}, sort=[("updated_at", -1)])
        # Befund Ahmad 10.09.2026: vor der Abholung gilt der Vertragspreis des
        # Suchers als Einkaufspreis (statt 0 €).
        import kaufvorgang as _kv
        _vorschlag = await _kv.einkaufspreis_vorschlag(vehicle_id, user["dealer_id"], v)
        listing = {
            "id": str(uuid.uuid4()),
            "dealer_id": user["dealer_id"],
            "vehicle_id": vehicle_id,
            "status": "entwurf",
            "title": _build_title(data),
            # RP-036/525/526: kurz, ohne Portaltext des Verkaeufers
            "description": _build_description(data),
            "data": data,                      # Kopie — bewusst entkoppelt
            "known_defects": known_defects,
            "auto_notes": auto_notes,
            # 20.09.2026 (Ahmad): KEINE Fotos mehr aus dem Portal-Inserat. Sie
            # gehoeren dem Verkaeufer bzw. dem Portal; sie auf dem eigenen
            # Marktplatz weiterzunutzen waere ein urheberrechtliches Risiko.
            # Der Haendler laedt eigene Fotos hoch (hoechstens
            # INSERAT_FOTOS_MAX). `einkauf_urls` bleibt als leere Liste
            # erhalten, damit Altbestand und Oberflaeche dasselbe Feld finden.
            "photos": {
                "mode": "neu",
                "einkauf_urls": [],
                "uploaded_keys": [],
            },
            "prices": {"public": None, "b2b": None, "network": None},
            # Umbau Kaufvorgaenge: der realisierte Kaufpreis kommt aus dem
            # abgeholten Vorgang (vehicles.purchase_price wird beim Abholen
            # gesetzt); der Vorgang wird am Inserat vermerkt.
            "purchase_price": _vorschlag.get("preis"),
            "purchase_price_quelle": _vorschlag.get("quelle"),
            "kaufvorgang_id": (abgeholt_vorgang or {}).get("id") or _vorschlag.get("kaufvorgang_id"),
            "costs": (v.get("bestand") or {}).get("costs") or [],
            "visibility": "public",
            "published_at": None,
            "counted_periods": [],
            "created_by": user["id"],
            "created_at": now_iso(), "updated_at": now_iso(),
        }
        try:
            await db.resale_listings.insert_one(listing)
        except DuplicateKeyError:
            # RP-083/182: Rueckhalt, sobald der Teil-Unique-Index "ein aktives
            # Inserat je Fahrzeug" steht (Uebergabe an das Betriebs-Team) —
            # dann gewinnt das vorhandene Inserat.
            vorhanden = await db.resale_listings.find_one(
                {"vehicle_id": vehicle_id, "dealer_id": user["dealer_id"],
                 "status": {"$in": list(_AKTIV)}}, {"_id": 0})
            if vorhanden and vorhanden.get("status") not in ("veroeffentlicht", "reserviert"):
                return _with_margin(vorhanden)
            raise HTTPException(409, "Fuer dieses Fahrzeug gibt es bereits ein "
                                     "aktives Inserat — bitte neu laden")
        # Runde 17 (Nr. 289): set_lifecycle statt try_ — ein Rennen am Fahrzeug
        # (zwischenzeitlich verkauft/geloescht) nimmt den Entwurf zurueck und
        # wird als 409 sichtbar, statt einen Entwurf ohne Fahrzeugbezug zu lassen.
        try:
            await _lifecycle_anwenden(vehicle_id, user["dealer_id"], pfad, user)
        except LifecycleError as exc:
            await db.resale_listings.delete_one({"id": listing["id"], "status": "entwurf"})
            raise HTTPException(409, "Fahrzeugstatus passt nicht zum Inserat: "
                                     f"{exc}")
        await _bestandsfrist_aufheben(vehicle_id, user["dealer_id"])
        await log_activity_sicher(user["dealer_id"], user["id"], "inserat.entwurf",
                           ref=listing["id"], meta={"vehicle_id": vehicle_id})
        return _with_margin(clean_doc(listing))
    finally:
        await _entwurf_sperre_freigeben(vehicle_id, user["dealer_id"], sperre)


def _kraftstoff_normieren(data: dict, *, nur_beschriftung: bool = False) -> None:
    """RP-506: data.fuel auf den Kraftstoffcode (fahrzeug_codes) bringen.

    Beim Anlegen zaehlt der vorhandene Wert, dann die Beschriftung; bleibt
    beides unerkannt, bleibt data.fuel unveraendert. Mit nur_beschriftung
    (der Haendler hat die Beschriftung im Editor geaendert) gilt allein die
    neue Beschriftung — sonst fand der Filter das Auto weiter unter dem alten
    Kraftstoff; ist sie leer oder unerkannt, faellt der Code weg und der
    Filter nimmt die Beschriftung."""
    try:
        from fahrzeug_codes import kraftstoff_code
    except Exception:  # noqa: BLE001 — Anlegen/Speichern darf daran nicht scheitern
        return
    if nur_beschriftung:
        code = kraftstoff_code(data.get("fuel_label"))
        if code:
            data["fuel"] = code
        else:
            data.pop("fuel", None)
        return
    code = kraftstoff_code(data.get("fuel"), data.get("fuel_label"))
    if code:
        data["fuel"] = code


async def _bestandsfrist_aufheben(vehicle_id: str, dealer_id: str) -> None:
    """Rollenprüfung 22.09.2026 (RP-092/191/342): "Weiterverkaufen" ruft seit
    Welle 1 nur noch POST /resale/draft auf (keine Bestands-Entscheidung
    mehr). Die setzte bisher bestand.expires_at auf None; ohne das hing die
    alte 50-Tage-Frist am Fahrzeug. Wird das Inserat spaeter geloescht
    (Fahrzeug zurueck in "bestand"), haette der Aufraeumer das Auto wegen der
    laengst abgelaufenen Frist sofort archiviert (Fotos weg). Mit None traegt
    er die Frist ab lifecycle_changed_at neu nach. Wirft nie."""
    try:
        await db.vehicles.update_one(
            {"id": vehicle_id, "dealer_id": dealer_id, "lifecycle": "verkaufsentwurf",
             "bestand.expires_at": {"$ne": None}},
            {"$set": {"bestand.expires_at": None}})
    except Exception:  # noqa: BLE001
        log.exception("Bestandsfrist an Fahrzeug %s nicht aufgehoben", vehicle_id)


async def _bestandsfrist_neu(vehicle_id: str, dealer_id: str) -> None:
    """RP-092/191/342: Loescht der Chef das Inserat und das Fahrzeug geht
    zurueck in "bestand", beginnt die 50-Tage-Frist neu — wie beim
    automatischen Inseratsende im Aufraeumlauf
    (cleanup_service._fahrzeug_nach_inseratsende_zuruecksetzen). Eine alte,
    abgelaufene Frist aus der Zeit vor dem Inserat haette sonst zur sofortigen
    Archivierung gefuehrt. Wirft nie."""
    from datetime import datetime, timedelta, timezone
    try:
        from routes.bestand import BESTAND_RETENTION_DAYS as _tage
    except Exception:  # noqa: BLE001
        _tage = 50
    jetzt = datetime.now(timezone.utc)
    try:
        await db.vehicles.update_one(
            {"id": vehicle_id, "dealer_id": dealer_id, "lifecycle": "bestand"},
            {"$set": {"bestand.expires_at": (jetzt + timedelta(days=_tage)).isoformat(),
                      "bestand.inserat_beendet_am": jetzt.isoformat()}})
    except Exception:  # noqa: BLE001
        log.exception("Bestandsfrist an Fahrzeug %s nicht neu gesetzt", vehicle_id)


#: RP-083: so lange haelt die Entwurfs-Sperre hoechstens (falls ein Prozess
#: mitten im Anlegen stirbt, verfaellt sie von selbst).
_ENTWURF_SPERRE_SEK = 20
_ENTWURF_SPERRE_VERSUCHE = 15          # x 0,2 s Wartezeit


async def _entwurf_sperre_holen(vehicle_id: str, dealer_id: str) -> Optional[str]:
    """RP-083: kurze Sperre am Fahrzeug fuer create_draft (Token-Besitz wie
    beim Veroeffentlichen). None, wenn sie nach ~3 s noch belegt ist."""
    import asyncio as _asyncio
    from datetime import datetime, timedelta, timezone
    token = uuid.uuid4().hex
    for versuch in range(_ENTWURF_SPERRE_VERSUCHE):
        jetzt = datetime.now(timezone.utc)
        treffer = await db.vehicles.find_one_and_update(
            {"id": vehicle_id, "dealer_id": dealer_id,
             "$or": [{"inserat_entwurf_sperre_bis": {"$exists": False}},
                     {"inserat_entwurf_sperre_bis": None},
                     {"inserat_entwurf_sperre_bis": {"$lt": jetzt}}]},
            {"$set": {"inserat_entwurf_sperre_bis":
                      jetzt + timedelta(seconds=_ENTWURF_SPERRE_SEK),
                      "inserat_entwurf_sperre_token": token}},
            projection={"_id": 0, "id": 1})
        if treffer is not None:
            return token
        if versuch + 1 < _ENTWURF_SPERRE_VERSUCHE:
            await _asyncio.sleep(0.2)
    return None


async def _entwurf_sperre_freigeben(vehicle_id: str, dealer_id: str, token: str) -> None:
    """Nur die EIGENE Sperre freigeben. Fehler hier duerfen das Ergebnis nicht
    kippen — die Sperre verfaellt ohnehin nach _ENTWURF_SPERRE_SEK."""
    try:
        await db.vehicles.update_one(
            {"id": vehicle_id, "dealer_id": dealer_id,
             "inserat_entwurf_sperre_token": token},
            {"$unset": {"inserat_entwurf_sperre_bis": "",
                        "inserat_entwurf_sperre_token": ""}})
    except Exception:  # noqa: BLE001
        log.exception("Entwurfs-Sperre an Fahrzeug %s nicht freigegeben", vehicle_id)


async def _protokoll_befund(vehicle_id: str, dealer_id: str) -> Dict[str, Any]:
    """RP-474: Kilometerstand und neue Schaeden aus dem unterschriebenen
    Abholprotokoll des abgeholten Termins (bei mehreren: das juengste).

    Nur Protokolle, deren Termin wirklich "abgeholt"/"erledigt" steht — nach
    einer Stornierung (V-12) bleibt das Protokoll als Nachweis stehen, die
    Abholung hat dann aber nicht stattgefunden. Wirft nie; im Zweifel bleibt
    der Entwurf wie bisher."""
    try:
        termine = set()
        async for a in db.appointments.find(
                {"vehicle_id": vehicle_id, "dealer_id": dealer_id,
                 "status": {"$in": ["abgeholt", "erledigt"]}},
                {"_id": 0, "id": 1}):
            if a.get("id"):
                termine.add(a["id"])
        if not termine:
            return {}
        protokolle = await db.pickup_protocols.find(
            {"vehicle_id": vehicle_id, "dealer_id": dealer_id, "status": "final",
             "superseded": {"$ne": True}, "appointment_id": {"$in": sorted(termine)}},
            {"_id": 0, "condition": 1, "new_damages": 1, "finalized_at": 1,
             "updated_at": 1, "created_at": 1, "version": 1}).to_list(50)
        if not protokolle:
            return {}
        protokolle.sort(key=lambda p: (p.get("finalized_at") or p.get("updated_at")
                                       or p.get("created_at") or "",
                                       p.get("version") or 0), reverse=True)
        p = protokolle[0]
        from protokoll_vergleich import zahl as _pv_zahl
        km = _pv_zahl((p.get("condition") or {}).get("mileage"))
        if km is not None and not (0 <= km <= KM_HOECHSTENS):
            km = None
        from auto_daten import schaeden_bereinigen
        schaeden = schaeden_bereinigen([d for d in (p.get("new_damages") or [])
                                        if isinstance(d, dict)])
        return {"km": km, "schaeden": [s[:300] for s in schaeden][:30]}
    except Exception:  # noqa: BLE001
        log.exception("Abholprotokoll zu Fahrzeug %s nicht ausgewertet", vehicle_id)
        return {}


# =========================================================
#                 LESEN / BEARBEITEN
# =========================================================
# Audit 13.09.2026 (#16): Abschnittsgroesse von GET /resale
RESALE_LISTE_MAX = 300


@router.get("/resale")
async def list_listings(response: Response, user=Depends(current_haendler),
                        status: Optional[str] = None, before: Optional[str] = None):
    """Inserate der Firma, neueste Aenderung zuerst.

    Audit 13.09.2026 (#16): vorher still die neuesten 300 — verkaufte Inserate
    bleiben als Historie stehen, ab dem 301. fehlten aeltere ohne Hinweis.
    Jetzt eins mehr lesen, Abschnitt per X-Truncated melden und per before
    (updated_at des letzten Eintrags) weiterblaettern (wie
    /dealer/network/members). Den Deckel nicht anheben: _preis_ergaenzen fragt
    je Inserat ohne Einkaufspreis einzeln nach."""
    query: Dict[str, Any] = {"dealer_id": user["dealer_id"],
                             "status": {"$ne": "geloescht"}}
    if status:
        query["status"] = status
    if before:
        query["updated_at"] = {"$lt": before}
    items = await db.resale_listings.find(query, {"_id": 0}) \
        .sort("updated_at", -1).to_list(RESALE_LISTE_MAX + 1)
    abgeschnitten = len(items) > RESALE_LISTE_MAX
    response.headers["X-Truncated"] = "1" if abgeschnitten else "0"
    if abgeschnitten:
        log.warning("GET /resale Firma %s: mehr als %d Inserate — Abschnitt gemeldet",
                    user["dealer_id"], RESALE_LISTE_MAX)
        items = items[:RESALE_LISTE_MAX]
    # Rollenprüfung 22.09.2026 (RP-088): Einkaufspreis/Kosten live, Fahrzeuge
    # in einer Abfrage statt je Inserat.
    await _kalkulation_live(items)
    for i in items:
        # Rollenprüfung 22.09.2026 (RP-519): Ablaufdatum auch in der Liste
        # (published_at + INSERAT_LAUFZEIT_TAGE; None, solange nie veroeffentlicht).
        i["laeuft_ab_am"] = _laufzeit_bis(i)
    return [_mit_foto_urls(_with_margin(i)) for i in items]


@router.get("/resale/{listing_id}")
async def get_listing(listing_id: str, user=Depends(current_haendler)):
    # Nachpruefung Runde 14 (Nr. 28): geloeschte Inserate sind wie in der
    # Liste auch einzeln nicht mehr abrufbar.
    l = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"],
         "status": {"$ne": "geloescht"}}, {"_id": 0})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    return await _editor_antwort(l)


async def _editor_antwort(l: dict) -> dict:
    """Die Form, die der Inserat-Editor bekommt (GET und PUT gleich —
    Runde 21: sonst verschwanden nach "Speichern" Foto-Links und Hinweise)."""
    l = _mit_foto_urls(_with_margin(await _preis_ergaenzen(l)))
    # Runde 21: Fahrerfotos, die noch uebernommen werden koennen.
    l["abholfotos"] = await _abholfotos(l)
    l["fahrerfoto_tage"] = __import__("cleanup_service").FAHRERFOTO_TAGE
    # Rollenprüfung 22.09.2026 (RP-460): "Verkauft" schlug den oeffentlichen
    # Preis vor, obwohl mit dem reservierten Kaeufer ein anderer Betrag
    # vereinbart war (agreed_price an der akzeptierten Anfrage).
    l["vereinbarter_preis"] = await _vereinbarter_preis(l)
    # Rollenprüfung 22.09.2026 (RP-467): oeffentliche Inserate sehen Kaeufer
    # nur bei Firmen mit oeffentlichem Marktplatz-Profil — der Editor warnt
    # vor "Oeffentlich veroeffentlichen", wenn das Profil privat ist.
    l["marktplatz_profil_oeffentlich"] = await _profil_oeffentlich(l.get("dealer_id"))
    # Rollenprüfung 22.09.2026 (RP-468): Restlaufzeit anzeigen (gezaehlt ab
    # der ERSTEN Veroeffentlichung, cleanup_service.abgelaufene_inserate_entfernen).
    # RP-519: dasselbe Datum heisst in allen Antworten (Inserate, Anfragen,
    # Marktplatz) "laeuft_ab_am"; laufzeit_bis bleibt fuer aeltere Oberflaechen.
    l["laufzeit_bis"] = l["laeuft_ab_am"] = _laufzeit_bis(l)
    return l


async def _vereinbarter_preis(l: dict) -> Optional[float]:
    """Vereinbarter Preis der akzeptierten Anfrage des reservierten Kaeufers
    (None bei manueller Reservierung oder ohne Betrag)."""
    if l.get("status") != "reserviert" or not l.get("reserved_for"):
        return None
    it = await db.listing_interest.find_one(
        {"listing_id": l.get("id"), "status": "akzeptiert",
         "buyer_user_id": l["reserved_for"]},
        {"_id": 0, "agreed_price": 1, "buyer_counter_offer": 1, "offer": 1})
    if not it:
        return None
    for feld in ("agreed_price", "buyer_counter_offer", "offer"):
        wert = it.get(feld)
        if isinstance(wert, (int, float)) and not isinstance(wert, bool) \
                and math.isfinite(wert) and wert > 0:
            return float(wert)
    return None


async def _profil_oeffentlich(dealer_id: Optional[str]) -> bool:
    if not dealer_id:
        return False
    d = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "marketplace.public": 1})
    return bool(((d or {}).get("marketplace") or {}).get("public"))


def _laufzeit_grenze_iso() -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc)
            - timedelta(days=INSERAT_LAUFZEIT_TAGE)).isoformat()


def _laufzeit_anker(l: dict) -> Optional[str]:
    """Rollenprüfung 22.09.2026 (RP-517): Beginn der Laufzeit (ISO) — die
    erste Veroeffentlichung, nach einer Freigabe durch den Betreiber
    (admin.kaeufer_reservierungen_freigeben setzt `wieder_veroeffentlicht_am`)
    der SPAETERE der beiden Zeitpunkte. Dieselbe Regel wie der Aufraeumlauf
    (cleanup_service.abgelaufene_inserate_entfernen, Stringvergleich der
    ISO-Zeiten) — sonst zeigte der Editor ein abgelaufenes Datum fuer ein
    Inserat, das noch 21 Tage laeuft, und ein zurueckgezogenes liess sich
    nicht erneut veroeffentlichen. None, solange nie veroeffentlicht."""
    erst = l.get("published_at")
    if not erst or not isinstance(erst, str):
        return None
    wieder = l.get("wieder_veroeffentlicht_am")
    if isinstance(wieder, str) and wieder > erst:
        return wieder
    return erst


def _laufzeit_bis(l: dict) -> Optional[str]:
    """Ende der Laufzeit (ISO) ab _laufzeit_anker, sonst None."""
    from datetime import datetime, timedelta
    anker = _laufzeit_anker(l)
    if not anker:
        return None
    try:
        return (datetime.fromisoformat(anker.replace("Z", "+00:00"))
                + timedelta(days=INSERAT_LAUFZEIT_TAGE)).isoformat()
    except ValueError:
        return None


def _mit_foto_urls(doc: dict) -> dict:
    """Signierte, kurzlebige Links zu den hochgeladenen Fotos (Audit 09/2026,
    Punkt 45) — die Oberflaeche baut keine /api/files-Pfade mehr selbst."""
    keys = ((doc.get("photos") or {}).get("uploaded_keys") or [])
    doc["photo_urls"] = [{"key": k, "url": signierte_datei_url(k)} for k in keys]
    # 10.09.2026: Einkaufsfotos als Vorschaubilder ueber den Bild-Proxy.
    from bild_proxy import thumbs as _thumbs
    doc["einkauf_thumbs"] = _thumbs((doc.get("photos") or {}).get("einkauf_urls") or [])
    return doc


async def _preis_ergaenzen(l: dict) -> dict:
    """Befund Ahmad 10.09.2026: Solange kein realisierter Einkaufspreis am
    Inserat steht, gilt der Vertragspreis des Suchers (kaufvorgang.
    einkaufspreis_vorschlag) — nur in der Antwort, nicht gespeichert.
    Rollenprüfung 22.09.2026 (RP-088): siehe _kalkulation_live."""
    await _kalkulation_live([l])
    return l


async def _kalkulation_live(items: List[dict]) -> None:
    """Rollenprüfung 22.09.2026 (RP-088/187/338): Einkaufspreis und Kosten
    wurden nur EINMAL beim Anlegen des Entwurfs ins Inserat kopiert. Ein bei
    der Abholung nachverhandelter Preis oder spaeter in der Fahrzeugakte
    erfasste Kosten (Transport, Aufbereitung ...) erreichten das Inserat nie
    — die "Erwartete Marge" rechnete mit dem alten Stand, obwohl die
    Oberflaeche sagt "Kosten werden in der Fahrzeugakte gepflegt".

    Jetzt gilt fuer AKTIVE Inserate der Live-Stand des Fahrzeugs:
      * Einkaufspreis aus kaufvorgang.einkaufspreis_vorschlag (Fahrzeug >
        abgeholter Vorgang > offener Vertrag); die Kopie am Inserat ist nur
        noch Rueckfall. Altbestand mit Preis, aber ohne Quelle, galt schon
        immer als "von Hand am Inserat" und bleibt stehen.
      * Kosten aus vehicles.bestand.costs (eine Quelle, die Akte).
    Verkaufte/geloeschte Inserate behalten ihren Stand (beim Verkauf wird er
    festgeschrieben, siehe set_listing_status). Nur in der Antwort — ein
    Lesen schreibt nichts. Fahrzeuge werden in EINER Abfrage je Firma
    geladen (die Liste hat bis zu RESALE_LISTE_MAX Eintraege)."""
    import kaufvorgang as _kv
    aktiv = [l for l in items if l.get("vehicle_id")
             and l.get("status") not in _ABGESCHLOSSEN]
    fahrzeuge: Dict[Tuple[str, str], dict] = {}
    je_firma: Dict[str, set] = {}
    for l in aktiv:
        je_firma.setdefault(l.get("dealer_id") or "", set()).add(l["vehicle_id"])
    for firma, ids in je_firma.items():
        async for v in db.vehicles.find(
                {"dealer_id": firma, "id": {"$in": sorted(ids)}},
                {"_id": 0, "id": 1, "purchase_price": 1,
                 "abgeholt_kaufvorgang_id": 1, "bestand": 1}):
            fahrzeuge[(firma, v["id"])] = v
    for l in items:
        v = fahrzeuge.get((l.get("dealer_id") or "", l.get("vehicle_id")))
        # Rollenprüfung 22.09.2026 (Review): "inserat" ist genau die Quelle,
        # die diese Funktion einem Preis ohne Quelle gibt (unten) und die der
        # Verkauf so festschreibt — gespeichert heisst sie ebenfalls "von Hand
        # am Inserat". Vorher verlor ein solcher Preis nach einem
        # zurueckgenommenen Verkauf (Rennen am Fahrzeug) seinen Schutz.
        von_hand = (l.get("purchase_price") is not None
                    and l.get("purchase_price_quelle") in (None, "", "inserat"))
        if v is not None:
            if not von_hand:
                vorschlag = await _kv.einkaufspreis_vorschlag(
                    l["vehicle_id"], l["dealer_id"], v)
                if vorschlag.get("preis") is not None:
                    l["purchase_price"] = vorschlag["preis"]
                    l["purchase_price_quelle"] = vorschlag["quelle"]
                elif vorschlag.get("quelle") == "mehrdeutig":
                    # Rollenprüfung 22.09.2026 (RP-057 b): mehrere offene
                    # Kaufvertraege verschiedener Konten mit VERSCHIEDENEN
                    # Preisen — die beim Anlegen kopierte Zahl stammte vom
                    # zufaellig zuletzt geaenderten Vertrag. Bis zur Abholung
                    # ehrlich "offen" statt einer geratenen Zahl (die
                    # Oberflaeche zeigt den Hinweis, Marge ohne Wert).
                    l["purchase_price"] = None
                    l["purchase_price_quelle"] = "mehrdeutig"
            kosten = (v.get("bestand") or {}).get("costs") \
                if isinstance(v.get("bestand"), dict) else None
            if isinstance(kosten, list):
                l["costs"] = kosten
        elif l.get("purchase_price") is None and l.get("vehicle_id"):
            # Wie bisher (verkaufte/geloeschte Inserate, Fahrzeug fehlt).
            vorschlag = await _kv.einkaufspreis_vorschlag(l["vehicle_id"],
                                                          l["dealer_id"])
            if vorschlag.get("preis") is not None:
                l["purchase_price"] = vorschlag["preis"]
                l["purchase_price_quelle"] = vorschlag["quelle"]
        if l.get("purchase_price") is not None and not l.get("purchase_price_quelle"):
            l["purchase_price_quelle"] = "inserat"


@router.put("/resale/{listing_id}")
async def update_listing(listing_id: str, body: ListingUpdateIn,
                         user=Depends(current_haendler)):
    l = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    status = l.get("status")
    # Nachpruefung Runde 14 (Nr. 28): geloeschte Inserate waren weiter
    # bearbeitbar (Titel, Preis, VIN) — jetzt wie verkaufte gesperrt.
    if status in _ABGESCHLOSSEN:
        raise HTTPException(400, "Verkaufte oder geloeschte Inserate können "
                                 "nicht bearbeitet werden")
    # Rollenprüfung 22.09.2026 (RP-463): veralteter Tab -> 409 statt still
    # Preis und Fahrzeugdaten auf den alten Stand zurueckzusetzen.
    if body.stand is not None and body.stand != (l.get("updated_at") or ""):
        raise HTTPException(409, "Das Inserat wurde inzwischen geändert (z. B. in "
                                 "einem anderen Fenster) — bitte neu laden. Deine "
                                 "Eingaben wurden nicht gespeichert.")
    # Rollenprüfung 22.09.2026 (RP-458): ein ausdruecklich gesendetes null
    # entfernt den Preis. Vorher wurde None uebersprungen — ein geleerter
    # B2B- oder Netzwerkpreis blieb still stehen (nur "0" entfernte ihn).
    # Der oeffentliche Preis bleibt ab "verkaufsbereit" Pflicht (Regel unten).
    gesendet = body.model_fields_set
    preis_felder = (("price_public", "public"), ("price_b2b", "b2b"),
                    ("price_network", "network"))
    preis_gesendet = any(feld in gesendet for feld, _ in preis_felder)
    # Nachpruefung Runde 14 (Nr. 108): waehrend einer Reservierung ist der
    # Kaeufer an das gesehene Angebot gebunden — Preis, Fahrzeugdaten und
    # Maengel bleiben eingefroren; Titel/Beschreibung/Fotomodus/Kosten sind
    # weiter aenderbar. Ein Zustands-Snapshot waere ein eigenes Feature.
    if status == "reserviert" and (preis_gesendet or body.known_defects is not None
                                   or body.data is not None):
        raise HTTPException(400, "Preis, Fahrzeugdaten und Maengel sind waehrend "
                                 "einer Reservierung nicht aenderbar — zuerst die "
                                 "Reservierung aufheben")
    update: Dict[str, Any] = {"updated_at": now_iso()}
    if body.title is not None:
        update["title"] = body.title.strip()
    if body.description is not None:
        update["description"] = body.description
    if body.known_defects is not None:
        update["known_defects"] = [str(m)[:300] for m in body.known_defects]
    if body.photo_mode is not None:
        update["photos.mode"] = body.photo_mode
    # Nachpruefung Runde 14 (Nr. 91): Preise einzeln per Pfad schreiben statt
    # den ganzen Block aus dem gelesenen Stand zurueckzusetzen — zwei
    # parallele PUTs (public / network) loeschten sich sonst gegenseitig.
    prices = dict(l.get("prices") or {})
    for feld, key in preis_felder:
        if feld not in gesendet:
            continue
        src = getattr(body, feld)
        prices[key] = (round(float(src), 2) or None) if src is not None else None
        update[f"prices.{key}"] = prices[key]
    # Nachpruefung Runde 14 (Nr. 107): die Preispflicht galt nur beim Schritt
    # entwurf -> verkaufsbereit; danach machte price_public=0 aus einem
    # live sichtbaren Inserat eines "ohne Preis". Gilt fuer den gemergten
    # Wert (auch wenn nur ein anderes Preisfeld gesendet wurde).
    if status in ("verkaufsbereit", "veroeffentlicht", "reserviert") \
            and preis_gesendet and not prices.get("public"):
        raise HTTPException(400, "Ein verkaufsbereites oder veroeffentlichtes "
                                 "Inserat braucht einen oeffentlichen Verkaufspreis")
    if body.costs is not None:
        update["costs"] = _clean_costs(body.costs)
    if body.data is not None:
        # Nur bekannte Felder übernehmen, keine beliebigen Keys.
        allowed = {"make_label", "model_label", "model_description",
                   "first_registration", "mileage", "fuel_label",
                   "gearbox_label", "power_kw", "power_ps", "color", "vin",
                   "previous_owners", "features", "description",
                   "accident_free"}
        merged = dict(l.get("data") or {})
        # Audit 13.09.2026 (#17): Werte je Feld bereinigen (siehe
        # _fahrzeugwert_bereinigen); verworfene behalten den alten Wert.
        uebernommen: Dict[str, Any] = {}
        verworfen: List[str] = []
        for k, val in body.data.items():
            if k not in allowed:
                continue
            ok, wert = _fahrzeugwert_bereinigen(k, val)
            if ok:
                merged[k] = uebernommen[k] = wert
            else:
                verworfen.append(k)
        if verworfen:
            log.warning("Inserat %s: ungueltige Fahrzeugdaten verworfen: %s",
                        listing_id, sorted(verworfen))
        # Rollenprüfung 22.09.2026 (RP-506): Korrigiert der Haendler den
        # Kraftstoff, zieht der Code (data.fuel, nicht direkt editierbar) mit —
        # sonst fand der Marktplatzfilter das Auto weiter unter dem alten.
        if "fuel_label" in uebernommen \
                and uebernommen["fuel_label"] != (l.get("data") or {}).get("fuel_label"):
            _kraftstoff_normieren(merged, nur_beschriftung=True)
        update["data"] = merged
    # Nachpruefung Runde 14 (Nr. 89): bedingter Write auf den GELESENEN
    # Status — ein paralleler Verkauf/Loeschung/Reservierung zwischen Lesen
    # und Schreiben wird nicht mehr ueberschrieben (Lost Update).
    bedingung: Dict[str, Any] = {"id": listing_id, "dealer_id": user["dealer_id"],
                                 "status": status}
    if body.stand is not None:
        # RP-463: derselbe Stand auch im atomaren Write (Rennen zwischen
        # Lesen und Schreiben).
        bedingung["updated_at"] = l.get("updated_at")
    res = await db.resale_listings.update_one(bedingung, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(409, "Inserat wurde zwischenzeitlich geaendert "
                                 "(verkauft, geloescht oder reserviert) — "
                                 "bitte neu laden")
    # Runde 15 (Nr. 6): Preis, Kosten, Maengel und Fahrzeugdaten eines
    # (auch live veroeffentlichten) Inserats aenderten sich ohne Spur —
    # Veroeffentlichung und Statuswechsel waren dagegen geloggt.
    felder = sorted(k for k in update if k != "updated_at")
    meta: Dict[str, Any] = {"felder": felder, "status": status}
    if any(k.startswith("prices.") for k in felder):
        meta["preise_alt"] = l.get("prices") or {}
        meta["preise_neu"] = prices
    if "data" in update:
        alt = l.get("data") or {}
        # Audit 13.09.2026 (#17): gegen die bereinigten Werte vergleichen
        meta["fahrzeugdaten_geaendert"] = sorted(
            k for k, val in uebernommen.items() if alt.get(k) != val)
        if verworfen:
            meta["fahrzeugdaten_verworfen"] = sorted(verworfen)
    await log_activity_sicher(user["dealer_id"], user["id"], "inserat.geaendert",
                       ref=listing_id, meta=meta)
    fresh = await db.resale_listings.find_one(
        {"id": listing_id}, {"_id": 0})
    # Runde 21 (Gegenpruefung): dieselbe Form wie GET — sonst verschwanden
    # nach "Speichern" Foto-Links, Einkaufspreis-Quelle und der Hinweis
    # "Fotos vom Fahrer uebernehmen".
    return await _editor_antwort(fresh)


@router.delete("/resale/{listing_id}")
async def delete_listing(listing_id: str, user=Depends(current_haendler)):
    """Inserat loeschen (Soft-Delete). WICHTIG (Beschluss 08/2026): einmal
    veroeffentlichte Inserate zaehlen im Abrechnungszeitraum WEITER auf das
    Kontingent — Loeschen gibt den Slot NICHT frei (counted_periods bleibt).
    Verkaufte Inserate bleiben als Historie erhalten (kein Loeschen)."""
    l = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    current = l.get("status")
    if current == "verkauft":
        raise HTTPException(400, "Verkaufte Inserate koennen nicht geloescht "
                                 "werden (Verkaufs-Historie).")
    if current == "geloescht":
        raise HTTPException(409, "Inserat ist bereits geloescht")
    # Nachpruefung Runde 14 (Nr. 53): Fahrzeug zurueck in den Bestand — den
    # Weg VOR dem Loeschen pruefen (aus "reserviert" ueber den
    # Zwischenschritt), statt den Fehler zu schlucken und das Fahrzeug ohne
    # Inserat in "reserviert" haengen zu lassen. Fahrzeuge ausserhalb des
    # Verkaufsblocks (archiviert, geloescht ...) werden nicht angefasst.
    vehicle_id = l.get("vehicle_id")
    pfad: List[str] = []
    if vehicle_id:
        cur = await _fahrzeug_lifecycle(vehicle_id, user["dealer_id"])
        if cur in _RESALE_LIFECYCLES:
            # Rollenprüfung 22.09.2026 (RP-518): ein VOR der Abholung
            # angelegtes Inserat setzte das Fahrzeug beim Loeschen immer auf
            # "bestand" — danach aenderte "nicht abgeholt" nichts mehr, und
            # nach 50 Tagen wurde archiviert. Ist noch nichts abgeholt, aber
            # ein Kauf offen, soll das Fahrzeug in den Kaufzustand zurueck.
            # Den Rueckweg kennt lifecycle.py seit Welle 2
            # (VERKAUF_RUECKWEG_VOR_ABHOLUNG); findet _pfad_zurueck keinen,
            # bleibt es beim bisherigen "bestand".
            vor_abholung = await _zustand_vor_abholung(vehicle_id, user["dealer_id"])
            weg = _pfad_zurueck(cur, vor_abholung) if vor_abholung else None
            if weg:
                pfad = weg
            else:
                pfad = await _lifecycle_pfad_oder_409(vehicle_id, user["dealer_id"],
                                                      "bestand")
    # Nachpruefung Runde 14 (Nr. 90): bedingt auf den gelesenen Status — ein
    # paralleler Verkauf zwischen Lesen und Schreiben wuerde sonst durch
    # "geloescht" ueberschrieben (und das verkaufte Fahrzeug auf bestand
    # zurueckgesetzt). Lifecycle und Protokoll erst nach erfolgreichem Write.
    res = await db.resale_listings.update_one(
        {"id": listing_id, "dealer_id": user["dealer_id"], "status": current},
        {"$set": {"status": "geloescht", "deleted_at": now_iso(),
                  "updated_at": now_iso()}})
    if res.matched_count == 0:
        raise HTTPException(409, "Inserat wurde zwischenzeitlich verkauft oder "
                                 "geloescht — bitte neu laden")
    if pfad:
        try:
            await _lifecycle_anwenden(vehicle_id, user["dealer_id"], pfad, user)
        except LifecycleError as exc:
            # Rennen am Fahrzeug: Loeschung zuruecknehmen, Fehler sichtbar.
            await db.resale_listings.update_one(
                {"id": listing_id, "dealer_id": user["dealer_id"],
                 "status": "geloescht"},
                {"$set": {"status": current, "updated_at": now_iso()},
                 "$unset": {"deleted_at": ""}})
            raise HTTPException(409, "Fahrzeugstatus passt nicht zum Inserat: "
                                     f"{exc}")
        if pfad[-1] == "bestand":
            # RP-092/191/342: neue 50-Tage-Frist statt einer alten, laengst
            # abgelaufenen (sofortige Archivierung im naechsten Aufraeumlauf).
            await _bestandsfrist_neu(vehicle_id, user["dealer_id"])
    # Nachpruefung Runde 14 (Nr. 54): laufende Verhandlungen und eine
    # akzeptierte Reservierung enden mit dem Inserat.
    await _anfragen_schliessen(listing_id, "inserat_geloescht",
                               auch_akzeptierte=True)
    await log_activity_sicher(user["dealer_id"], user["id"], "inserat.geloescht",
                       ref=listing_id,
                       meta={"war_status": current,
                             "kontingent_bleibt": bool(l.get("counted_periods"))})
    return {"ok": True, "hinweis": "Inserat geloescht. Bereits veroeffentlichte "
                                   "Inserate zaehlen im laufenden Monat weiter "
                                   "auf dein Kontingent."}


@router.post("/resale/{listing_id}/photos")
async def upload_photos(listing_id: str, body: PhotoUploadIn,
                        user=Depends(current_haendler)):
    """Neue Fotos hochladen (Storage-Abstraktion, kein Base64 in Mongo)."""
    l = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "photos": 1, "status": 1})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    # Nachpruefung Runde 14 (Nr. 29): Fotos landeten auch auf geloeschten und
    # verkauften Inseraten im Storage — ohne Aufraeumer, dauerhaft.
    if l.get("status") in _ABGESCHLOSSEN:
        raise HTTPException(400, "Fuer verkaufte oder geloeschte Inserate koennen "
                                 "keine Fotos mehr hochgeladen werden")
    from storage_service import (make_key, storage, StorageError,
                                 validate_image_bytes, bild_verkleinern,
                                 loeschen_oder_vormerken, speicher_aufruf,
                                 MAX_IMAGE_BYTES)
    keys = list((l.get("photos") or {}).get("uploaded_keys", []))
    if len(keys) + len(body.photos_b64) > INSERAT_FOTOS_MAX:
        raise HTTPException(
            400, f"Maximal {INSERAT_FOTOS_MAX} Fotos pro Inserat "
                 f"(aktuell {len(keys)})")

    def _alle_pruefen() -> tuple:
        """Decode + Validierung + Verkleinern fuer alle Fotos — als EIN
        Thread-Hop, damit der Event-Loop nicht sekundenlang steht (Review
        09/2026). Liefert (brauchbare [(index, bytes)], abgelehnte
        [(index, grund)]).

        Rollenprüfung 22.09.2026 (RP-533): Ein einzelnes unbrauchbares Bild
        (HEIC vom iPhone, kaputte Datei, zu gross, Bildbombe) wird
        uebersprungen und je Foto gemeldet. Vorher lehnte der Server das
        GANZE Paket mit 400 ab — die brauchbaren Fotos daneben gingen mit
        verloren, und die Oberflaeche brach die folgenden Pakete ab."""
        brauchbar, abgelehnt = [], []
        for i, b64 in enumerate(body.photos_b64):
            try:
                # Nachpruefung Runde 14 (Nr. 117): Groesse VOR dem Decode
                # pruefen — ein zu grosser Block wird nicht erst dekodiert.
                if len(b64) > MAX_IMAGE_BYTES * 4 // 3 + 1024:
                    raise StorageError("Inserats-Foto zu gross (erlaubt "
                                       f"{MAX_IMAGE_BYTES // (1024 * 1024)} MB)")
                raw = base64.b64decode(b64.split(",")[-1], validate=False)
                # Groesse + Magic Bytes: nur echte Bilder, kein 20-MB-Blob,
                # keine umbenannten ausfuehrbaren Dateien.
                validate_image_bytes(raw, wo="Inserats-Foto")
                # Handyfotos kommen mit 4000 Bildpunkten Kante und
                # mehreren MB. Einmal verkleinern spart rund 90 Prozent
                # Speicher, ohne dass man im Inserat etwas sieht.
                raw = bild_verkleinern(raw, wo="Inserats-Foto")
            except (StorageError, ValueError) as exc:
                abgelehnt.append((i, str(exc)))
                continue
            brauchbar.append((i, raw))
        return brauchbar, abgelehnt

    def _alle_speichern(brauchbar: list) -> tuple:
        """Die geprueften Fotos speichern. Liefert (gespeicherte Keys,
        Fehler|None); bei Fehler raeumt der Aufrufer die halb gespeicherten
        Dateien weg — ein Speicherausfall betrifft das ganze Paket."""
        neu = []
        for _i, raw in brauchbar:
            key = make_key("resale", user["dealer_id"], "foto.jpg")
            try:
                storage.save(key, raw)
            except Exception as exc:  # noqa: BLE001 — Aufrufer raeumt auf und meldet
                return neu, exc
            neu.append(key)
        return neu, None

    import asyncio as _asyncio
    brauchbar, abgelehnt = await _asyncio.to_thread(_alle_pruefen)
    if not brauchbar:
        # Kein einziges brauchbares Foto: wie bisher 400 mit dem Grund.
        if len(abgelehnt) == 1:
            raise HTTPException(400, f"Foto konnte nicht gespeichert werden: {abgelehnt[0][1]}")
        raise HTTPException(400, "Keines der Fotos konnte übernommen werden: " + "; ".join(
            f"Foto {i + 1}: {grund}" for i, grund in abgelehnt))
    # Rollenprüfung 22.09.2026 (RP-550): Speichern im eigenen Speicher-Pool
    # (storage_service.speicher_aufruf) — ein haengender Objektspeicher
    # belegte sonst den Standard-Pool, den auch die Passwortpruefung und die
    # PDF-Erzeugung nutzen. Das Pruefen/Verkleinern oben ist reine
    # Rechenarbeit und bleibt im Standard-Pool.
    added, fehler = await speicher_aufruf(_alle_speichern, brauchbar)
    if fehler is not None:
        # Halb gespeicherte wieder wegraeumen. Fehlschlaege werden NICHT
        # mehr verschluckt, sondern vorgemerkt (storage_delete_retry) und
        # vom Aufraeumjob nachgeholt (Go-Live-Audit 09/2026).
        for k in added:
            await loeschen_oder_vormerken(
                db, key=k, grund="inserat_upload_abbruch",
                dealer_id=user["dealer_id"])
        if isinstance(fehler, StorageError):
            raise HTTPException(400, f"Foto konnte nicht gespeichert werden: {fehler}")
        log.error("Inserat %s: Fotospeicher nicht erreichbar: %r", listing_id, fehler)
        raise HTTPException(503, "Der Fotospeicher ist gerade nicht erreichbar — bitte "
                                 "in ein paar Minuten erneut hochladen.")
    # ATOMAR anhaengen ($push $each) statt die ganze Liste zu ueberschreiben:
    # das alte Lesen-Aendern-Schreiben verlor bei PARALLELEN Uploads aufs
    # selbe Inserat Referenzen (im Lasttest: hunderte Dateien ohne
    # DB-Eintrag). Die Obergrenze prueft dieselbe Bedingung atomar mit —
    # der Verlierer eines Rennens raeumt seine Dateien wieder weg.
    # Nachpruefung Runde 14 (Nr. 29): Statusfilter im atomaren Write — wird
    # das Inserat waehrend des Uploads verkauft/geloescht, raeumt der
    # Verlierer seine Dateien wie beim Limit wieder weg.
    #
    # Pruefbericht 20.09.2026 (Nr. 5): Hier stand eine harte 40, waehrend die
    # Vorpruefung weiter oben laengst INSERAT_FOTOS_MAX (10) nimmt. Damit war
    # das neue Limit unter Gleichzeitigkeit wirkungslos: zwei Uploads mit je
    # 6 Bildern bestanden beide die Vorpruefung (0 + 6 <= 10) und liefen
    # danach BEIDE durch den Riegel — 12 Fotos im Inserat. Jetzt haelt der
    # atomare Riegel dieselbe Zahl wie die Vorpruefung.
    res = await db.resale_listings.update_one(
        {"id": listing_id, "dealer_id": user["dealer_id"],
         "status": {"$nin": list(_ABGESCHLOSSEN)},
         f"photos.uploaded_keys.{INSERAT_FOTOS_MAX - len(added)}": {"$exists": False}},
        {"$push": {"photos.uploaded_keys": {"$each": added}},
         "$set": {"updated_at": now_iso()}})
    if res.modified_count == 0:
        for k in added:
            await loeschen_oder_vormerken(
                db, key=k, grund="inserat_foto_limit", dealer_id=user["dealer_id"])
        raise HTTPException(400, f"Maximal {INSERAT_FOTOS_MAX} Fotos pro Inserat — "
                                 "oder das Inserat ist inzwischen verkauft/geloescht")
    # Rollenprüfung 22.09.2026 (RP-532): Altinserate stehen noch auf
    # photos.mode "einkauf" (nur Portal-Fotos zeigen). Den Umschalter gibt es
    # seit dem 20.09. nicht mehr — neu hochgeladene Fotos waren dort im Editor
    # und beim Kaeufer unsichtbar und liessen sich nicht mehr loeschen. Wie
    # bei der Fahrerfoto-Uebernahme: dann Einkaufs- UND neue Fotos zeigen.
    await db.resale_listings.update_one(
        {"id": listing_id, "dealer_id": user["dealer_id"], "photos.mode": "einkauf"},
        {"$set": {"photos.mode": "beide"}})
    doc = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "photos.uploaded_keys": 1})
    total = len((doc.get("photos") or {}).get("uploaded_keys") or [])
    meta: Dict[str, Any] = {"anzahl": len(added), "gesamt": total, "status": l.get("status")}
    if abgelehnt:
        meta["abgelehnt"] = len(abgelehnt)
    await log_activity_sicher(user["dealer_id"], user["id"], "inserat.foto.hinzugefuegt",
                       ref=listing_id, meta=meta)
    # RP-533: "abgelehnt" nennt je uebersprungenem Foto die Stelle im Paket
    # (index, ab 0) und den Grund — die Oberflaeche ordnet sie den Dateien zu.
    return {"ok": True, "uploaded": [signierte_datei_url(k) for k in added],
            "total": total,
            "abgelehnt": [{"index": i, "grund": grund} for i, grund in abgelehnt]}


# =========================================================
#     Runde 21: Fotos aus dem Abholbericht ins Inserat
# =========================================================
async def _abholfotos(l: dict) -> list:
    """Fahrerfotos (Abweichungsfotos aus aktuellen Abholberichten) dieses
    Fahrzeugs, die noch nicht ins Inserat uebernommen wurden."""
    if not l.get("vehicle_id") or not l.get("dealer_id"):
        return []
    fotos = l.get("photos") or {}
    # Rollenprüfung 22.09.2026 (RP-090/189/340): Fotos, deren Datei beim
    # letzten Versuch fehlte, werden nicht mehr angeboten.
    schon = set(fotos.get("aus_abholbericht") or []) | set(fotos.get("abholfotos_fehlend") or [])
    out: list = []
    gesehen: set = set()
    async for rep in db.pickup_reports.find(
            {"vehicle_id": l["vehicle_id"], "dealer_id": l["dealer_id"],
             "superseded": {"$ne": True}, "deviations.photo_key": {"$type": "string"}},
            {"_id": 0, "deviations": 1}).sort("created_at", -1):
        for d in rep.get("deviations") or []:
            k = d.get("photo_key")
            # RP-090: vorgemerkte Loeschungen (photo_loeschung_offen: Datei weg
            # oder gleich weg, Key bleibt bis zur Nachholung stehen) sind keine
            # Fotos mehr — sonst scheiterte jede Uebernahme an ihnen.
            if d.get("photo_loeschung_offen") or d.get("photo_deleted_at"):
                continue
            if k and k not in schon and k not in gesehen:
                gesehen.add(k)
                out.append({"key": k, "label": d.get("label") or ""})
    return out[:40]


class AbholfotosIn(BaseModel):
    # Ohne Angabe: alle noch nicht uebernommenen Fahrerfotos.
    photo_keys: Optional[List[Annotated[str, StringConstraints(max_length=300)]]] = \
        Field(default=None, max_length=40)


@router.post("/resale/{listing_id}/photos/aus-abholbericht")
async def fotos_aus_abholbericht(listing_id: str, body: Optional[AbholfotosIn] = None,
                                 user=Depends(current_haendler)):
    """Fahrerfotos (z.B. Schaeden) mit einem Klick ins Inserat uebernehmen.
    Es entsteht eine eigene Kopie unter resale/: sie bleibt im Inserat,
    auch wenn der Abholbericht seine Fotos nach FAHRERFOTO_TAGE loescht,
    und verschwindet mit dem Inserat. Nur Fotos aus Berichten DIESES
    Fahrzeugs der eigenen Firma (sonst liessen sich fremde Schluessel
    einschleusen)."""
    l = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "id": 1, "photos": 1, "status": 1, "vehicle_id": 1, "dealer_id": 1})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    if l.get("status") in _ABGESCHLOSSEN:
        raise HTTPException(400, "Fuer verkaufte oder geloeschte Inserate koennen "
                                 "keine Fotos mehr uebernommen werden")
    verfuegbar = [f["key"] for f in await _abholfotos(l)]
    wahl = verfuegbar
    ausgewaehlt = body is not None and body.photo_keys is not None
    if ausgewaehlt:
        erlaubt = set(verfuegbar)
        wahl = [k for k in dict.fromkeys(body.photo_keys) if k in erlaubt]
    if not wahl:
        raise HTTPException(400, "Keine neuen Fotos vom Fahrer vorhanden")
    keys = list((l.get("photos") or {}).get("uploaded_keys") or [])
    frei = INSERAT_FOTOS_MAX - len(keys)
    if ausgewaehlt and len(keys) + len(wahl) > INSERAT_FOTOS_MAX:
        raise HTTPException(400, f"Maximal {INSERAT_FOTOS_MAX} Fotos pro Inserat "
                                 f"(aktuell {len(keys)}) — bitte vorher Fotos "
                                 f"entfernen oder weniger auswaehlen")
    # Rollenprüfung 22.09.2026 (RP-340): Die Oberflaeche schickt immer "alle".
    # Gab es mehr Fahrerfotos als freie Plaetze, kam vorher stets 400, und
    # einzeln auswaehlen kann man dort nicht. Jetzt werden die ersten freien
    # Plaetze gefuellt und der Rest in der Antwort genannt.
    if frei <= 0:
        raise HTTPException(400, f"Das Inserat hat schon {INSERAT_FOTOS_MAX} Fotos — "
                                 "bitte zuerst eines entfernen.")
    zu_viele = max(0, len(wahl) - frei)
    wahl = wahl[:frei]
    import asyncio as _asyncio
    from storage_service import (StorageError, bild_verkleinern, load_async,
                                 loeschen_oder_vormerken, make_key, save_async)
    added: list = []
    # RP-090/189/340: vorher alles oder nichts — EINE fehlende Datei (Frist
    # abgelaufen, Loeschung vorgemerkt) brach die ganze Uebernahme ab, im
    # S3-Speicher sogar dauerhaft mit 503 "bitte erneut versuchen". Jetzt
    # werden fehlende/unlesbare Fotos einzeln uebersprungen und gemeldet;
    # nur echte Speicher-/Netzfehler brechen weiter ab.
    uebernommen: list = []
    fehlend: list = []
    try:
        for k in wahl:
            try:
                raw = await load_async(k)
                # Alte Fahrerfotos koennen noch EXIF (Aufnahmeort) tragen —
                # die Kopie fuer das oeffentliche Inserat ist immer bereinigt.
                raw = await _asyncio.to_thread(bild_verkleinern, raw, "Fahrerfoto")
            except Exception as exc:       # noqa: BLE001
                if _datei_fehlt(exc):
                    fehlend.append(k)
                    continue
                raise
            neu_key = make_key("resale", user["dealer_id"], "foto.jpg")
            # VOR dem Speichern vormerken: auch eine halb geschriebene Datei
            # wird bei einem Abbruch wieder aufgeraeumt.
            added.append(neu_key)
            await save_async(neu_key, raw)
            uebernommen.append(k)
    except Exception as exc:               # auch S3-/Netzfehler, nicht nur StorageError
        for nk in added:
            await loeschen_oder_vormerken(db, key=nk, grund="abholfoto_uebernahme_abbruch",
                                          dealer_id=user["dealer_id"])
        if isinstance(exc, StorageError):
            raise HTTPException(400, f"Foto konnte nicht uebernommen werden: {exc}")
        log.exception("Fahrerfotos konnten nicht ins Inserat %s uebernommen werden", listing_id)
        raise HTTPException(503, "Fotos konnten gerade nicht uebernommen werden — "
                                 "bitte in einem Moment erneut versuchen.")
    if not added:
        # Nichts Lesbares dabei: die fehlenden merken, damit sie nicht bei
        # jedem Laden wieder angeboten werden.
        if fehlend:
            await db.resale_listings.update_one(
                {"id": listing_id, "dealer_id": user["dealer_id"]},
                {"$addToSet": {"photos.abholfotos_fehlend": {"$each": fehlend}}})
        raise HTTPException(400, "Die Fotos vom Fahrer sind nicht mehr vorhanden "
                                 "(Aufbewahrungsfrist abgelaufen oder gelöscht).")
    ergaenzen: Dict[str, Any] = {"photos.aus_abholbericht": {"$each": uebernommen}}
    if fehlend:
        ergaenzen["photos.abholfotos_fehlend"] = {"$each": fehlend}
    # Runde 21 (Gegenpruefung): "$nin" verhindert Doppel-Uebernahmen bei
    # Doppelklick oder parallelen Anfragen — der zweite Aufruf findet die
    # Originale schon vermerkt und raeumt seine Kopien wieder weg.
    res = await db.resale_listings.update_one(
        {"id": listing_id, "dealer_id": user["dealer_id"],
         "status": {"$nin": list(_ABGESCHLOSSEN)},
         "photos.aus_abholbericht": {"$nin": wahl},
         # Nr. 5: dieselbe Zahl wie die Vorpruefung (vorher hart 40).
         f"photos.uploaded_keys.{INSERAT_FOTOS_MAX - len(added)}": {"$exists": False}},
        {"$push": {"photos.uploaded_keys": {"$each": added}},
         "$addToSet": ergaenzen,
         "$set": {"updated_at": now_iso()}})
    if res.modified_count == 0:
        for nk in added:
            await loeschen_oder_vormerken(db, key=nk, grund="abholfoto_uebernahme_limit",
                                          dealer_id=user["dealer_id"])
        raise HTTPException(409, "Die Fotos wurden gerade schon uebernommen, das Inserat "
                                 f"ist voll (max. {INSERAT_FOTOS_MAX} Fotos) oder "
                                 "inzwischen verkauft/geloescht.")
    # Zeigt das Inserat bisher nur Einkaufsfotos, waeren die uebernommenen
    # unsichtbar — dann Einkaufs- UND neue Fotos zeigen.
    await db.resale_listings.update_one(
        {"id": listing_id, "dealer_id": user["dealer_id"], "photos.mode": "einkauf"},
        {"$set": {"photos.mode": "beide"}})
    await log_activity_sicher(user["dealer_id"], user["id"], "inserat.foto.aus_abholbericht",
                       ref=listing_id, meta={"anzahl": len(added),
                                             "fehlend": len(fehlend),
                                             "kein_platz": zu_viele})
    return {"ok": True, "uebernommen": len(added),
            # RP-090/340: was nicht uebernommen werden konnte, und warum
            "fehlend": len(fehlend), "kein_platz": zu_viele,
            "uploaded": [signierte_datei_url(k) for k in added]}


def _datei_fehlt(exc: Exception) -> bool:
    """RP-090/189: Datei nicht (mehr) vorhanden bzw. kein lesbares Bild?
    Lokal wirft der Speicher StorageError ("Datei nicht gefunden"), der
    Bildpruefer ebenfalls StorageError; S3/R2 wirft einen botocore-ClientError
    mit Code NoSuchKey/404 (storage_service bildet ihn noch nicht ab —
    Uebergabe an das Betriebs-Team). Alles andere (Netz, Rechte) ist ein
    echter Fehler und bricht weiter ab."""
    from storage_service import StorageError
    if isinstance(exc, StorageError):
        return True
    antwort = getattr(exc, "response", None)
    if isinstance(antwort, dict):
        code = str((antwort.get("Error") or {}).get("Code") or "")
        return code in ("NoSuchKey", "404", "NotFound")
    return False


# =========================================================
#                 VERÖFFENTLICHEN (Phase 3 — Kontingent)
# =========================================================
class PublishIn(BaseModel):
    visibility: Literal["public", "private"] = "public"


class PhotoRemoveIn(BaseModel):
    # Entweder ein hochgeladener Storage-Key ODER eine Einkaufsfoto-URL.
    key: Optional[str] = Field(default=None, max_length=500)
    url: Optional[str] = Field(default=None, max_length=1000)


#: Rollenprüfung 22.09.2026 (RP-093/192/343): In diesen Zustaenden sieht ein
#: Kaeufer das Inserat — das letzte Foto darf nicht verschwinden.
_FOTO_PFLICHT = ("veroeffentlicht", "reserviert")
_LETZTES_FOTO = ("Das ist das letzte Foto eines veröffentlichten oder reservierten "
                 "Inserats. Bitte zuerst ein anderes Foto hochladen oder das "
                 "Inserat zurückziehen.")


class FotoReihenfolgeIn(BaseModel):
    # Die hochgeladenen Fotos in der neuen Reihenfolge (das erste ist das
    # Titelbild auf dem Marktplatz). Altinserate hatten bis zu 40.
    keys: List[Annotated[str, StringConstraints(max_length=500)]] = \
        Field(min_length=1, max_length=40)


@router.post("/resale/{listing_id}/photos/reihenfolge")
async def fotos_reihenfolge(listing_id: str, body: FotoReihenfolgeIn,
                            user=Depends(current_haendler)):
    """Rollenprüfung 22.09.2026 (RP-469): Reihenfolge und Titelbild liessen
    sich nicht aendern — das zuerst hochgeladene Foto war fuer immer das
    Titelbild. Nur eine Umsortierung der VORHANDENEN Fotos (gleiche Menge),
    atomar gegen den gelesenen Stand (paralleles Hochladen/Loeschen -> 409)."""
    l = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "photos": 1, "status": 1})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    if l.get("status") in _ABGESCHLOSSEN:
        raise HTTPException(400, "Fotos verkaufter oder geloeschter Inserate "
                                 "bleiben als Historie erhalten")
    alt = list((l.get("photos") or {}).get("uploaded_keys") or [])
    neu = list(dict.fromkeys(body.keys))
    if len(neu) != len(alt) or set(neu) != set(alt):
        raise HTTPException(409, "Die Fotos haben sich inzwischen geändert — "
                                 "bitte neu laden.")
    if neu == alt:
        return {"ok": True, "uploaded_keys": alt}
    res = await db.resale_listings.update_one(
        {"id": listing_id, "dealer_id": user["dealer_id"],
         "status": {"$nin": list(_ABGESCHLOSSEN)},
         "photos.uploaded_keys": alt},
        {"$set": {"photos.uploaded_keys": neu, "updated_at": now_iso()}})
    if res.matched_count == 0:
        raise HTTPException(409, "Die Fotos haben sich inzwischen geändert — "
                                 "bitte neu laden.")
    await log_activity_sicher(user["dealer_id"], user["id"], "inserat.foto.reihenfolge",
                              ref=listing_id, meta={"titelbild": neu[0],
                                                    "status": l.get("status")})
    return {"ok": True, "uploaded_keys": neu}


@router.post("/resale/{listing_id}/photos/remove")
async def remove_photo(listing_id: str, body: PhotoRemoveIn,
                       user=Depends(current_haendler)):
    """Einzelnes Bild aus dem Inserat entfernen — auch NACH der
    Veroeffentlichung (Aenderung ist sofort live). Hochgeladene Fotos
    werden zusaetzlich aus dem Storage geloescht; Einkaufsfotos werden
    nur aus dem Inserat genommen (das Original bleibt in der Akte)."""
    l = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "photos": 1, "status": 1})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    # Nachpruefung Runde 14 (Nr. 67, hoch): nach dem Verkauf sind die Fotos
    # Beweismaterial (Zustand bei Uebergabe) — sie duerfen weder aus dem
    # Inserat noch aus dem Storage verschwinden. Gilt auch fuer geloeschte.
    if l.get("status") in _ABGESCHLOSSEN:
        raise HTTPException(400, "Fotos verkaufter oder geloeschter Inserate "
                                 "bleiben als Historie erhalten")
    photos = l.get("photos") or {}
    if body.key:
        keys = list(photos.get("uploaded_keys", []))
        if body.key not in keys:
            raise HTTPException(404, "Foto nicht gefunden")
        keys.remove(body.key)
        from storage_service import loeschen_oder_vormerken
        # Nachpruefung Runde 14 (Nr. 67): ERST bedingt aus dem Inserat nehmen
        # ($pull mit Statusfilter, matched_count pruefen), DANN die Datei
        # loeschen — sonst loescht ein Rennen mit dem Verkauf die Datei
        # trotzdem. ATOMAR ($pull), weil parallele Loeschungen sich sonst
        # gegenseitig verdraengten.
        # Pruefbericht 20.09.2026 (Nr. 6): Ein VEROEFFENTLICHTES Inserat darf
        # sein letztes eigenes Foto nicht verlieren — sonst steht es oeffentlich
        # ohne Bild da, obwohl genau das beim Veroeffentlichen verboten ist.
        # Atomar mitgeprueft: entweder ist es (noch) nicht veroeffentlicht,
        # oder es bleibt danach mindestens ein Bild uebrig (Index 1 vorhanden
        # = zwei Stueck) bzw. es gibt Einkaufsfotos aus dem Altbestand.
        # Rollenprüfung 22.09.2026 (RP-093/192/343): der Schutz galt nur fuer
        # "veroeffentlicht" — ein RESERVIERTES Inserat (der Kaeufer sieht es
        # weiter) konnte alle Fotos verlieren. Jetzt fuer beide Zustaende.
        res = await db.resale_listings.update_one(
            {"id": listing_id, "dealer_id": user["dealer_id"],
             "status": {"$nin": list(_ABGESCHLOSSEN)},
             "photos.uploaded_keys": body.key,
             "$or": [{"status": {"$nin": ["veroeffentlicht", "reserviert"]}},
                     {"photos.uploaded_keys.1": {"$exists": True}},
                     {"photos.einkauf_urls.0": {"$exists": True}}]},
            {"$pull": {"photos.uploaded_keys": body.key},
             "$set": {"updated_at": now_iso()}})
        if res.matched_count == 0:
            nach = await db.resale_listings.find_one(
                {"id": listing_id, "dealer_id": user["dealer_id"]},
                {"_id": 0, "status": 1, "photos": 1}) or {}
            _p = nach.get("photos") or {}
            if (nach.get("status") in _FOTO_PFLICHT
                    and len(_p.get("uploaded_keys") or []) <= 1
                    and not _p.get("einkauf_urls")):
                raise HTTPException(400, _LETZTES_FOTO)
            raise HTTPException(409, "Inserat wurde zwischenzeitlich verkauft "
                                     "oder geloescht — Foto bleibt erhalten")
        # Laesst sich die Datei nicht loeschen, wird sie vorgemerkt; der Key
        # bleibt im Inserat unter photos.loeschung_offen_keys erhalten (nicht
        # mehr sichtbar, aber nicht verloren) — die Nachholung entfernt ihn.
        ok = await loeschen_oder_vormerken(
            db, key=body.key, grund="inserat_foto_entfernt",
            dealer_id=user["dealer_id"],
            ref={"collection": "resale_listings", "id": listing_id,
                 "pull_key_from": "photos.loeschung_offen_keys"})
        if not ok:
            await db.resale_listings.update_one(
                {"id": listing_id, "dealer_id": user["dealer_id"]},
                {"$addToSet": {"photos.loeschung_offen_keys": body.key}})
        doc = await db.resale_listings.find_one(
            {"id": listing_id, "dealer_id": user["dealer_id"]},
            {"_id": 0, "photos.uploaded_keys": 1})
        # Runde 15 (Nr. 6): Foto-Entfernen ist sofort live und war nicht
        # nachvollziehbar (Hochladen ebenfalls).
        await log_activity_sicher(user["dealer_id"], user["id"], "inserat.foto.entfernt",
                           ref=listing_id, meta={"art": "upload", "key": body.key,
                                                 "status": l.get("status")})
        return {"ok": True, "uploaded_keys":
                (doc.get("photos") or {}).get("uploaded_keys") or []}
    if body.url:
        urls = list(photos.get("einkauf_urls", []))
        if body.url not in urls:
            raise HTTPException(404, "Foto nicht gefunden")
        # RP-343 (b): auch das letzte EINKAUFSFOTO eines veroeffentlichten
        # oder reservierten Altinserats liess sich ohne Pruefung entfernen.
        res = await db.resale_listings.update_one(
            {"id": listing_id, "dealer_id": user["dealer_id"],
             "status": {"$nin": list(_ABGESCHLOSSEN)},
             "$or": [{"status": {"$nin": list(_FOTO_PFLICHT)}},
                     {"photos.einkauf_urls.1": {"$exists": True}},
                     {"photos.uploaded_keys.0": {"$exists": True}}]},
            {"$pull": {"photos.einkauf_urls": body.url},
             "$set": {"updated_at": now_iso()}})
        if res.matched_count == 0:
            nach = await db.resale_listings.find_one(
                {"id": listing_id, "dealer_id": user["dealer_id"]},
                {"_id": 0, "status": 1, "photos": 1}) or {}
            _p = nach.get("photos") or {}
            if (nach.get("status") in _FOTO_PFLICHT
                    and len(_p.get("einkauf_urls") or []) <= 1
                    and not _p.get("uploaded_keys")):
                raise HTTPException(400, _LETZTES_FOTO)
            raise HTTPException(409, "Inserat wurde zwischenzeitlich verkauft "
                                     "oder geloescht — Foto bleibt erhalten")
        doc = await db.resale_listings.find_one(
            {"id": listing_id, "dealer_id": user["dealer_id"]},
            {"_id": 0, "photos.einkauf_urls": 1})
        await log_activity_sicher(user["dealer_id"], user["id"], "inserat.foto.entfernt",
                           ref=listing_id, meta={"art": "einkauf", "url": body.url[:300],
                                                 "status": l.get("status")})
        return {"ok": True, "einkauf_urls":
                (doc.get("photos") or {}).get("einkauf_urls") or []}
    raise HTTPException(400, "key oder url angeben")


@router.post("/resale/{listing_id}/publish")
async def publish_listing(listing_id: str, body: PublishIn,
                          user=Depends(current_haendler)):
    """Veröffentlicht ein verkaufsbereites Inserat auf dem Marktplatz.

    Kontingent-Regeln (Beschluss 05.08.2026):
    - Zählt NUR beim tatsächlichen ersten Publish im Abrechnungszeitraum.
    - Entwürfe zählen nie; Zurückziehen + Reaktivieren im selben Zeitraum
      zählt nicht erneut (counted_periods).
    - Kontingent voll → 402 mit Upgrade-Hinweis (kein Einzelkauf).
    """
    # SERIALISIERUNG je Inserat (Runde 5): Zwei gleichzeitige Publishes
    # desselben Inserats konnten auseinanderlaufen — der zweite sah die
    # Markierung des ersten und veroeffentlichte, waehrend der erste wegen
    # ueberschrittener Quote zurueckrollte: Inserat live, aber ungezaehlt.
    # Jetzt bekommt genau EINE Anfrage die Sperre; die andere wartet nicht,
    # sondern bekommt 409 und wiederholt.
    from datetime import datetime, timedelta, timezone
    _jetzt = datetime.now(timezone.utc)
    if not await db.resale_listings.count_documents(
            {"id": listing_id, "dealer_id": user["dealer_id"]}):
        raise HTTPException(404, "Inserat nicht gefunden")
    # Pruefung 14.09.2026 (Nr. 7): Sperre mit Besitzer-Token — das finally
    # unten gibt nur die EIGENE Sperre frei. Vorher loeschte ein Aufruf, der
    # laenger als 30 s brauchte, am Ende die inzwischen von einem zweiten
    # Aufruf gesetzte Sperre.
    _token = uuid.uuid4().hex
    _sperre = await db.resale_listings.find_one_and_update(
        {"id": listing_id, "dealer_id": user["dealer_id"],
         "$or": [{"publish_lock_until": {"$exists": False}},
                 {"publish_lock_until": None},
                 {"publish_lock_until": {"$lt": _jetzt}}]},
        {"$set": {"publish_lock_until": _jetzt + timedelta(seconds=30),
                  "publish_lock_token": _token}})
    if _sperre is None:
        raise HTTPException(409, "Dieses Inserat wird gerade veroeffentlicht — "
                                 "bitte einen Moment warten und neu laden.")
    try:
        l = await db.resale_listings.find_one(
            {"id": listing_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
        if not l:
            raise HTTPException(404, "Inserat nicht gefunden")
        if l.get("status") == "veroeffentlicht":
            # Idempotent (Review 09/2026): Doppelklick oder parallele
            # Anfrage NACH dem erfolgreichen Publish ist kein Fehler.
            return {"ok": True, "status": "veroeffentlicht",
                    "visibility": l.get("visibility") or body.visibility,
                    "bereits_veroeffentlicht": True}
        if l.get("status") not in ("verkaufsbereit", "zurueckgezogen"):
            raise HTTPException(400, "Nur verkaufsbereite (oder zurückgezogene) "
                                     "Inserate können veröffentlicht werden")
        # Nachpruefung Runde 14 (Nr. 107): die Preispflicht greift sonst nur
        # bei entwurf -> verkaufsbereit; ueber zurueckgezogen -> publish kam
        # ein Inserat ohne oeffentlichen Preis live.
        if not (l.get("prices") or {}).get("public"):
            raise HTTPException(400, "Bitte zuerst einen oeffentlichen "
                                     "Verkaufspreis eintragen")
        # Rollenprüfung 22.09.2026 (RP-087/186/337): die 500er-Grenze auch
        # hier — Altbestand und der direkte API-Weg brachten sonst lange
        # Texte (bis 30.000 Zeichen, mit Portaltext) live.
        zu_lang = _beschreibung_zu_lang(l)
        if zu_lang:
            raise HTTPException(400, zu_lang)
        # Rollenprüfung 22.09.2026 (RP-468): gezaehlt wird ab der ERSTEN
        # Veroeffentlichung (published_at bleibt stehen). Nach mehr als
        # INSERAT_LAUFZEIT_TAGE liess sich ein zurueckgezogenes Inserat
        # trotzdem erneut veroeffentlichen, belastete das Kontingent — und
        # der Aufraeumlauf loeschte es binnen einer Stunde samt Fotos, ohne
        # Warnung. Jetzt 409 VOR jedem Kontingentabzug.
        # RP-517: gezaehlt ab _laufzeit_anker (nach einer Freigabe durch den
        # Betreiber ab wieder_veroeffentlicht_am, wie der Aufraeumlauf).
        _anker = _laufzeit_anker(l)
        if _anker and _anker < _laufzeit_grenze_iso():
            raise HTTPException(
                409, f"Die Laufzeit von {INSERAT_LAUFZEIT_TAGE} Tagen seit der "
                     f"ersten Veröffentlichung ist abgelaufen — dieses Inserat kann "
                     f"nicht erneut veröffentlicht werden. Du kannst es löschen und "
                     f"über die Fahrzeugakte ein neues Inserat anlegen.")
        # Rollenprüfung 22.09.2026 (RP-083/182): zweite Absicherung gegen
        # Dubletten aus der Zeit vor der Entwurfs-Sperre — je Fahrzeug nur
        # EIN Inserat live oder reserviert.
        if l.get("vehicle_id") and await db.resale_listings.count_documents(
                {"vehicle_id": l["vehicle_id"], "dealer_id": user["dealer_id"],
                 "id": {"$ne": listing_id},
                 "status": {"$in": ["veroeffentlicht", "reserviert"]}}):
            raise HTTPException(409, "Für dieses Fahrzeug ist bereits ein anderes "
                                     "Inserat veröffentlicht oder reserviert — bitte "
                                     "dieses Inserat löschen.")
        # 20.09.2026 (Ahmad): Fotos muessen NEU hochgeladen werden — aus dem
        # Portal-Inserat wird nichts mehr uebernommen. Ein Inserat ohne
        # eigenes Foto waere auf dem Marktplatz wertlos, deshalb hier die
        # Pflicht. Altbestand mit uebernommenen Einkaufsfotos zaehlt mit,
        # damit vorhandene Inserate weiter veroeffentlicht werden koennen.
        _fotos = l.get("photos") or {}
        if not (_fotos.get("uploaded_keys") or _fotos.get("einkauf_urls")):
            raise HTTPException(
                400, f"Bitte zuerst mindestens ein eigenes Foto hochladen "
                     f"(hoechstens {INSERAT_FOTOS_MAX}). Fotos aus dem "
                     f"urspruenglichen Inserat werden nicht uebernommen.")
        # Nachpruefung Runde 14 (Nr. 81): Fahrzeugweg VOR Kontingent und
        # Statuswechsel pruefen — bei Desync 409 statt Inserat live und
        # Fahrzeug unveraendert (try_set_lifecycle schluckte den Fehler).
        pfad = await _lifecycle_pfad_oder_409(l.get("vehicle_id"),
                                              user["dealer_id"], "veroeffentlicht")

        from routes.team import get_sale_plan_status
        plan = await get_sale_plan_status(user["dealer_id"])
        if not plan.get("active"):
            raise HTTPException(402, "Kein Verkaufspaket aktiv. Bitte im Bereich "
                                     "'Mitarbeiter / Sucher' ein Paket anfragen.")
        period_key = plan["period_key"]
        already = period_key in (l.get("counted_periods") or [])
        # Nachpruefung Runde 14 (Nr. 81): merken, was DIESER Aufruf am
        # Kontingent beansprucht hat, um es bei einem spaeteren Abbruch
        # (Rennen am Inserat oder Fahrzeug) wieder zurueckzugeben.
        markiert = False
        slot_geholt = False

        async def _kontingent_zurueckgeben() -> None:
            if slot_geholt:
                await db.dealers.update_one(
                    {"id": user["dealer_id"]},
                    {"$inc": {f"quota_usage.{period_key}": -1}})
            if markiert:
                await db.resale_listings.update_one(
                    {"id": listing_id, "dealer_id": user["dealer_id"]},
                    {"$pull": {"counted_periods": period_key}})

        if not already:
            quota = plan.get("quota")
            did = user["dealer_id"]
            field = f"quota_usage.{period_key}"
            if quota:
                # Pruefung 14.09.2026 (Nr. 8): den Zaehler VOR der Markierung
                # dieses Inserats aus dem Ist-Stand befuellen. Vorher lag das
                # Seeding NACH Schritt 1: zwei gleichzeitige Publishes
                # verschiedener Inserate zaehlten die frische Markierung des
                # jeweils anderen mit und erhoehten danach beide — ein Slot zu
                # viel, das naechste Inserat wurde zu frueh abgelehnt. Vor der
                # Markierung kann eine "in Arbeit"-Markierung nur existieren,
                # wenn der Zaehler schon gesetzt ist ($exists-Guard greift).
                seeded = await db.dealers.find_one({"id": did}, {field: 1})
                if ((seeded or {}).get("quota_usage") or {}).get(period_key) is None:
                    cur = await db.resale_listings.count_documents(
                        {"dealer_id": did, "counted_periods": period_key,
                         "id": {"$ne": listing_id}})
                    await db.dealers.update_one(
                        {"id": did, field: {"$exists": False}},
                        {"$set": {field: cur}})
            # Schritt 1: Den Abrechnungszeitraum ATOMAR am Inserat markieren.
            # Der $ne-Guard sorgt dafür, dass von BELIEBIG vielen gleichzeitigen
            # Publishes desselben Inserats genau EINER die Markierung setzt —
            # und nur DER beansprucht anschließend einen Kontingent-Slot.
            # (Vorher konnten zwei parallele Publishes desselben Inserats zwei
            # Slots ziehen — dauerhafte Überzählung.)
            marker = await db.resale_listings.update_one(
                {"id": listing_id, "dealer_id": user["dealer_id"],
                 "counted_periods": {"$ne": period_key}},
                {"$addToSet": {"counted_periods": period_key}})
            markiert = bool(marker.modified_count)
            if not marker.modified_count:
                # Ein GLEICHZEITIGER Publish hat die Markierung gesetzt. Zwei
                # Faelle: (a) er hat den Slot bekommen — dann ist alles gezaehlt
                # und wir duerfen mitveroeffentlichen; (b) er lag UEBER der
                # Quota und hat Markierung + Slot gerade zurueckgegeben — dann
                # duerfen wir NICHT einfach durchrutschen (vorher konnte so ein
                # Inserat ueber der Quota live gehen, ohne je gezaehlt zu
                # werden). Nachlesen entscheidet.
                nachgelesen = await db.resale_listings.find_one(
                    {"id": listing_id, "dealer_id": user["dealer_id"]},
                    {"_id": 0, "counted_periods": 1})
                if period_key not in (nachgelesen or {}).get("counted_periods", []):
                    raise HTTPException(402, "Dein monatliches Kontingent ist "
                                             "erreicht. Upgrade auf ein größeres "
                                             "Paket oder Enterprise anfragen.")
            if marker.modified_count and quota:
                # Schritt 2: ATOMARE Kontingent-Beanspruchung (race-fest, auch
                # bei mehreren Worker-Prozessen): ein Zähler pro Händler+Zeitraum
                # wird atomar erhöht — jeder Gewinner bekommt eine EINDEUTIGE
                # Nummer. Wer über der Quota landet, gibt Slot UND Markierung
                # zurück und wird abgelehnt. (Seeding: siehe oben, VOR Schritt 1.)
                claimed = await db.dealers.find_one_and_update(
                    {"id": did},
                    {"$inc": {field: 1}},
                    projection={field: 1},
                    return_document=ReturnDocument.AFTER)
                slot_geholt = True
                used_now = (claimed.get("quota_usage") or {}).get(period_key, 1)
                if used_now > quota:
                    # Über der Quota → Slot und Markierung zurückgeben, ablehnen.
                    await _kontingent_zurueckgeben()
                    raise HTTPException(402, f"Dein monatliches Kontingent von "
                                             f"{quota} Fahrzeugen ist erreicht. "
                                             "Upgrade auf ein größeres Paket oder "
                                             "Enterprise anfragen.")

        # Runde 8 (15.09.2026, Liste 4 Nr. 14): Paket unmittelbar vor dem
        # Live-Schalten erneut pruefen — der Betreiber kann es waehrend des
        # Kontingent-Teils entzogen haben, oder es ist gerade abgelaufen.
        if not (await get_sale_plan_status(user["dealer_id"])).get("active"):
            await _kontingent_zurueckgeben()
            raise HTTPException(402, "Das Verkaufspaket wurde gerade beendet — "
                                     "das Inserat wurde nicht veröffentlicht.")
        # Nachpruefung Runde 14 (Nr. 81/89): bedingt auf den gelesenen Status
        # — ein paralleler Statuswechsel (verkauft/geloescht) darf nicht
        # durch "veroeffentlicht" ueberschrieben werden.
        # Pruefbericht 20.09.2026 (Nr. 6): Der finale Abgleich prueft jetzt
        # AUCH den Fotostand. Vorher stand er nur auf dem Status — wurde
        # zwischen dem Lesen (Foto war da) und hier das letzte Foto entfernt,
        # ging das Inserat trotzdem mit NULL Fotos live, obwohl
        # Veroeffentlichen ohne Foto verboten ist.
        res = await db.resale_listings.update_one(
            {"id": listing_id, "dealer_id": user["dealer_id"],
             "status": l.get("status"),
             "$or": [{"photos.uploaded_keys.0": {"$exists": True}},
                     {"photos.einkauf_urls.0": {"$exists": True}}]},
            {"$set": {"status": "veroeffentlicht",
                      "visibility": body.visibility,
                      "published_at": l.get("published_at") or now_iso(),
                      "updated_at": now_iso()}})
        if res.matched_count == 0:
            await _kontingent_zurueckgeben()
            # Nr. 6: Fotos weg oder Status geaendert — beides getrennt melden,
            # sonst sucht der Sucher an der falschen Stelle.
            jetzt_doc = await db.resale_listings.find_one(
                {"id": listing_id, "dealer_id": user["dealer_id"]},
                {"_id": 0, "photos": 1}) or {}
            _p = jetzt_doc.get("photos") or {}
            if not (_p.get("uploaded_keys") or _p.get("einkauf_urls")):
                raise HTTPException(
                    400, "Das letzte Foto wurde gerade entfernt — bitte "
                         "mindestens ein eigenes Foto hochladen.")
            raise HTTPException(409, "Inserat wurde zwischenzeitlich geaendert "
                                     "— bitte neu laden")
        if pfad:
            try:
                await _lifecycle_anwenden(l["vehicle_id"], user["dealer_id"],
                                          pfad, user)
            except LifecycleError as exc:
                # Rennen am Fahrzeug: Veroeffentlichung zuruecknehmen.
                await db.resale_listings.update_one(
                    {"id": listing_id, "dealer_id": user["dealer_id"],
                     "status": "veroeffentlicht"},
                    {"$set": {"status": l.get("status"),
                              "published_at": l.get("published_at"),
                              "updated_at": now_iso()}})
                await _kontingent_zurueckgeben()
                raise HTTPException(409, "Fahrzeugstatus passt nicht zum "
                                         f"Inserat: {exc}")
        # Runde 17 (Nr. 398): Inserat ist live und gezaehlt — ein Fehler
        # beim Audit darf daraus keinen 500 (und keinen Client-Retry) machen.
        await log_activity_sicher(
            user["dealer_id"], user["id"], "inserat.veroeffentlicht",
            ref=listing_id,
            meta={"sichtbarkeit": body.visibility,
                  "kontingent": f"{plan.get('used', 0) + (0 if already else 1)}/{plan.get('quota')}"})
        antwort: Dict[str, Any] = {"ok": True, "status": "veroeffentlicht",
                                   "visibility": body.visibility}
        # Rollenprüfung 22.09.2026 (RP-467): oeffentliche Inserate sehen
        # Kaeufer nur bei Firmen mit oeffentlichem Marktplatz-Profil
        # (routes/marketplace._sichtbare_haendler). Vorher meldete die Seite
        # "veroeffentlicht", obwohl bei neuen Firmen niemand ausser dem
        # eigenen Netzwerk das Inserat sah. Nicht blockierend (der Editor
        # fragt vorher nach und schaltet das Profil auf Wunsch ein).
        if body.visibility == "public":
            antwort["profil_oeffentlich"] = await _profil_oeffentlich(user["dealer_id"])
            if not antwort["profil_oeffentlich"]:
                antwort["hinweis"] = ("Dein Marktplatz-Profil ist nicht öffentlich — das "
                                      "Inserat sehen nur deine Netzwerk-Partner.")
        return antwort
    finally:
        await db.resale_listings.update_one(
            {"id": listing_id, "dealer_id": user["dealer_id"],
             "publish_lock_token": _token},
            {"$unset": {"publish_lock_until": "", "publish_lock_token": ""}})


# =========================================================
#                 STATUS-WORKFLOW
# =========================================================
#: Wie frontend/src/lib/fahrzeugStatus.js (INSERAT_LABELS) — fuer Meldungen.
_STATUS_TEXT = {
    "entwurf": "Entwurf", "verkaufsbereit": "Verkaufsbereit",
    "veroeffentlicht": "Veröffentlicht", "reserviert": "Reserviert",
    "verkauft": "Verkauft", "zurueckgezogen": "Zurückgezogen",
    "geloescht": "Gelöscht",
}

_LISTING_TO_LIFECYCLE = {
    "entwurf": "verkaufsentwurf",
    "verkaufsbereit": "verkaufsbereit",
    "reserviert": "reserviert",
    "verkauft": "verkauft",
    "zurueckgezogen": "verkaufsbereit",
}


@router.post("/resale/{listing_id}/status")
async def set_listing_status(listing_id: str, body: ListingStatusIn,
                             user=Depends(current_haendler)):
    l = await db.resale_listings.find_one(
        {"id": listing_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden")
    current = l.get("status")
    new = body.status
    # Rollenprüfung 22.09.2026 (RP-492): Der Server las den Status frisch —
    # ein veralteter Tab ("veroeffentlicht") schloss mit "Verkauft" die
    # inzwischen entstandene B2B-Reservierung ab (der reservierte Kaeufer
    # wurde still zum Kaeufer), und "Reservieren" auf ein schon reserviertes
    # Inserat meldete "bereits", als waere es die eigene Reservierung.
    # Rollenprüfung 22.09.2026 (Review): Diese Pruefung stand VOR dem
    # idempotenten Zweig (Nr. 399) — ein Doppelklick auf "Vom Marktplatz
    # nehmen" / "Zurück zu Entwurf" kam mit dem alten angezeigten Status an
    # und bekam 409 "… z. B. von einem Käufer reserviert", obwohl der eigene
    # erste Klick den Zustand schon geschrieben hatte. Steht das Inserat
    # bereits auf dem Ziel und kann es nur von der eigenen Firma dorthin
    # gekommen sein, ist das kein Konflikt: entwurf/verkaufsbereit/
    # zurueckgezogen/verkauft setzt nur diese Route (Kaeufer, Admin und
    # Aufraeumer setzen nur reserviert-mit-Kaeufer, veroeffentlicht oder
    # geloescht). Eine Kaeufer-Reservierung (ohne reserviert_manuell) bleibt
    # 409 — genau davor schuetzt RP-492.
    selbst_erreicht = current == new and (new != "reserviert"
                                          or bool(l.get("reserviert_manuell")))
    if body.von_status is not None and body.von_status != current \
            and not selbst_erreicht:
        raise HTTPException(409, "Das Inserat steht inzwischen auf "
                                 f"„{_STATUS_TEXT.get(current, current)}“ "
                                 "(z. B. von einem Käufer reserviert) — bitte neu "
                                 "laden und dann entscheiden.")
    if current == new:
        # Runde 17 (Nr. 399): Doppelklick / Client-Retry nach erfolgreichem
        # Wechsel ist kein Fehler (vorher 400 "nicht erlaubt", obwohl der
        # Zustand laengst geschrieben war). Anfragen idempotent nachziehen.
        try:
            await _anfragen_nach_wechsel(listing_id, new=new, war_reserviert=False,
                                         manuell_reserviert=bool(l.get("reserviert_manuell")))
        except Exception:  # noqa: BLE001
            log.exception("Kaufanfragen zu Inserat %s nicht geschlossen", listing_id)
        return {"ok": True, "status": new, "bereits": True}
    allowed = {
        "entwurf": {"verkaufsbereit"},
        "verkaufsbereit": {"entwurf", "reserviert", "verkauft", "zurueckgezogen"},
        "veroeffentlicht": {"reserviert", "verkauft", "zurueckgezogen"},
        "reserviert": {"verkauft", "verkaufsbereit"},
        "zurueckgezogen": {"verkaufsbereit", "entwurf"},
    }
    if new not in allowed.get(current, set()):
        raise HTTPException(400, f"Übergang '{current}' → '{new}' nicht erlaubt")

    if new == "verkaufsbereit" and current == "entwurf":
        # Mindestangaben prüfen, bevor das Inserat verkaufsfertig wird.
        if not (l.get("prices") or {}).get("public"):
            raise HTTPException(400, "Bitte zuerst einen Verkaufspreis eintragen")
        # Rollenprüfung 22.09.2026 (RP-087): Beschreibungsgrenze auch hier.
        zu_lang = _beschreibung_zu_lang(l)
        if zu_lang:
            raise HTTPException(400, zu_lang)

    update: Dict[str, Any] = {"status": new, "updated_at": now_iso()}
    unset: Dict[str, Any] = {}
    if new == "verkauft":
        # Nachpruefung Runde 14 (Nr. 79): ohne Verkaufspreis blieb sold_price
        # null — Marge/Auswertung leer, keine dokumentierte Entscheidung dazu.
        if body.sold_price is None:
            raise HTTPException(400, "Bitte den tatsaechlichen Verkaufspreis "
                                     "angeben")
        update["sold_at"] = now_iso()
        update["sold_price"] = round(float(body.sold_price), 2)
        # Nachpruefung Runde 14 (Nr. 27): Semantik explizit — der reservierte
        # Kaeufer wird als Kaeufer festgehalten, reserved_for verschwindet.
        if current == "reserviert" and l.get("reserved_for"):
            update["sold_to_user_id"] = l["reserved_for"]
        # Rollenprüfung 22.09.2026 (RP-088): Einkaufspreis und Kosten zum
        # Verkaufszeitpunkt festschreiben — danach rechnet die Marge des
        # verkauften Inserats mit genau diesem Stand (Historie).
        stand = {k: l.get(k) for k in ("id", "dealer_id", "vehicle_id", "status",
                                       "purchase_price", "purchase_price_quelle",
                                       "costs")}
        await _kalkulation_live([stand])
        # RP-057 b: auch "offen" (mehrdeutig, ohne Preis) festhalten — sonst
        # rechnete das verkaufte Inserat mit der beim Anlegen geratenen Zahl;
        # ohne Preis holt die Anzeige ihn nach der Abholung weiter nach.
        if stand.get("purchase_price") is not None \
                or stand.get("purchase_price_quelle") == "mehrdeutig":
            update["purchase_price"] = stand.get("purchase_price")
            update["purchase_price_quelle"] = stand.get("purchase_price_quelle")
        if isinstance(stand.get("costs"), list):
            update["costs"] = stand["costs"]
    if new == "reserviert":
        # Rollenprüfung 22.09.2026 (RP-093/192/343): eine Reservierung von
        # Hand (Kaeufer am Telefon, im Laden) setzte keinen reserved_for —
        # Marktplatz-Kaeufer konnten weiter verhandeln, annehmen ging aber
        # nicht mehr. Jetzt gekennzeichnet; offene Anfragen enden unten.
        update["reserviert_manuell"] = True
    if current == "reserviert":
        # Nachpruefung Runde 14 (Nr. 26): reserved_for blieb beim Verlassen
        # von "reserviert" stehen und wanderte bis in die Veroeffentlichung.
        unset["reserved_for"] = ""
        unset["reserviert_manuell"] = ""

    # Nachpruefung Runde 14 (Nr. 52/81): Fahrzeugweg VOR dem Schreiben
    # pruefen (409 bei Desync) — vorher schluckte try_set_lifecycle den
    # Fehler und das Fahrzeug blieb z.B. dauerhaft "reserviert".
    vehicle_id = l.get("vehicle_id")
    pfad = await _lifecycle_pfad_oder_409(vehicle_id, user["dealer_id"],
                                          _LISTING_TO_LIFECYCLE[new])

    op: Dict[str, Any] = {"$set": update}
    if unset:
        op["$unset"] = unset
    # Bedingt auf den gelesenen Status (Nr. 89/90): paralleler Wechsel -> 409.
    res = await db.resale_listings.update_one(
        {"id": listing_id, "dealer_id": user["dealer_id"], "status": current}, op)
    if res.matched_count == 0:
        raise HTTPException(409, "Inserat wurde zwischenzeitlich geaendert — "
                                 "bitte neu laden")

    # Fahrzeug-Lebenszyklus synchron halten — Fehler sichtbar (Nr. 81).
    if pfad:
        try:
            await _lifecycle_anwenden(vehicle_id, user["dealer_id"], pfad, user)
        except LifecycleError as exc:
            # Rennen am Fahrzeug: Inserat auf den alten Status zuruecksetzen.
            # Runde 17 (Nr. 290): auch reserved_for aus dem gelesenen Stand
            # wiederherstellen — vorher verlor der Kaeufer seine Reservierung.
            zurueck: Dict[str, Any] = {"status": current, "updated_at": now_iso()}
            if current == "reserviert" and l.get("reserved_for"):
                zurueck["reserved_for"] = l["reserved_for"]
            weg: Dict[str, Any] = {"sold_at": "", "sold_price": "",
                                   "sold_to_user_id": ""}
            # RP-093: Kennzeichen der Hand-Reservierung wie vorher
            if l.get("reserviert_manuell"):
                zurueck["reserviert_manuell"] = True
            else:
                weg["reserviert_manuell"] = ""
            # Rollenprüfung 22.09.2026 (Review): auch den beim Verkauf
            # festgeschriebenen Stand (RP-088) zuruecknehmen. Vorher blieb
            # purchase_price_quelle="inserat" stehen — ein von Hand
            # eingetragener Alt-Einkaufspreis (ohne Quelle) galt danach nicht
            # mehr als "von Hand" und wurde in jeder Anzeige durch den
            # Vertragspreis ersetzt; die Kostenliste war ueberschrieben.
            # Nur die Felder, die das Verkaufs-Update wirklich gesetzt hat.
            for feld in ("purchase_price", "purchase_price_quelle", "costs"):
                if feld not in update:
                    continue
                if feld in l:
                    zurueck[feld] = l[feld]
                else:
                    weg[feld] = ""
            await db.resale_listings.update_one(
                {"id": listing_id, "dealer_id": user["dealer_id"], "status": new},
                {"$set": zurueck, "$unset": weg})
            raise HTTPException(409, "Fahrzeugstatus passt nicht zum Inserat: "
                                     f"{exc}")

    # Nachpruefung Runde 14 (Nr. 54/26): Kaufanfragen mit dem Inserat
    # abschliessen. Runde 17 (Nr. 399): Inserat und Fahrzeug sind bereits
    # geschrieben — Folgeschritte (Anfragen, Audit) duerfen den Vorgang nicht
    # mehr mit 500 abbrechen lassen; Fehler landen im Log.
    try:
        await _anfragen_nach_wechsel(listing_id, new=new,
                                     war_reserviert=(current == "reserviert"),
                                     manuell_reserviert=(new == "reserviert"))
    except Exception:  # noqa: BLE001
        log.exception("Kaufanfragen zu Inserat %s nach '%s' nicht geschlossen",
                      listing_id, new)
    try:
        await log_activity_sicher(user["dealer_id"], user["id"], f"inserat.{new}",
                           ref=listing_id)
    except Exception:  # noqa: BLE001
        log.exception("Audit-Eintrag inserat.%s (%s) nicht gespeichert", new, listing_id)
    return {"ok": True, "status": new}


async def _anfragen_nach_wechsel(listing_id: str, *, new: str,
                                 war_reserviert: bool,
                                 manuell_reserviert: bool = False) -> None:
    """Beim Verkauf bleibt eine akzeptierte Anfrage (der Kaeufer) stehen,
    alle offenen Verhandlungen enden; beim Aufheben einer Reservierung endet
    auch die akzeptierte. Idempotent (update_many auf offene Stati).

    Rollenprüfung 22.09.2026 (RP-093/192/343): Reserviert der Haendler von
    Hand, enden die offenen Verhandlungen ebenfalls — annehmen konnte er sie
    ohnehin nicht mehr (nur auf veroeffentlichten Inseraten), und beim
    Aufheben der Reservierung endeten sie bisher sowieso. So erfahren die
    Kaeufer es sofort statt weiter Gegenangebote ins Leere zu schicken."""
    if new == "verkauft":
        await _anfragen_schliessen(listing_id, "inserat_verkauft")
    elif war_reserviert:
        await _anfragen_schliessen(listing_id, "reservierung_aufgehoben",
                                   auch_akzeptierte=True)
    elif new in ("zurueckgezogen", "entwurf"):
        await _anfragen_schliessen(listing_id, f"inserat_{new}")
    elif new == "reserviert" and manuell_reserviert:
        await _anfragen_schliessen(listing_id, "inserat_reserviert")
