# -*- coding: utf-8 -*-
"""Dauerhafte, anonymisierte Auto-Daten (Collection `admin_vehicle_data`).

Fachlicher Auftrag (09/2026): Bei jeder Vertragserstellung wird ein
separater Fahrzeug-Datensatz gespeichert, der die 90-Tage-Loeschung des
Vertrags UEBERLEBT und dauerhaft KEINE Verbindung mehr zu Vertrag,
Haendler oder Personen hat.

Gespeichert wird ausschliesslich die Whitelist: Marke, Modell,
Erstzulassung, Kilometerstand, Kraftstoff, PS, kW, Kaufpreis
(Integer-Cent), Waehrung und textlich genannte Schaeden. Keine IDs der
Quelle, keine Namen/Adressen, keine FIN, keine Fotos, kein PDF.

Verknuepfung: NUR der (noch existierende) Vertrag traegt voruebergehend
`admin_vehicle_data_id`; der Auto-Datensatz selbst kennt seine Quelle
nicht. Mit der Vertragsloeschung verschwindet die Zuordnung — der
Datensatz bleibt anonym bestehen.

Transaktionen: Die Ziel-MongoDB (docker-compose, Standalone ohne
Replica Set) unterstuetzt KEINE Multi-Dokument-Transaktionen — die
wuerden ein Replica Set erfordern (mongod --replSet + rs.initiate();
siehe DEPLOYMENT.md-Hinweis). Statt einer Schein-Transaktion gilt hier:
1) Auto-Datensatz idempotent per Upsert auf seine zufaellige UUID,
2) danach der Vertrag MIT admin_vehicle_data_id,
3) schlaegt der Vertrags-Insert fehl, wird der (noch nicht anonyme,
   weil nie referenzierte) Auto-Datensatz sofort wieder entfernt,
4) Reparatur: der Aufraeumjob traegt fuer Bestandsvertraege ohne
   admin_vehicle_data_id den Datensatz nach (auto_daten_nachtragen).
"""
import re
import uuid
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 2   # v2: + purchase_date (Kaufdatum, nur der Tag)
COLLECTION = "admin_vehicle_data"

MAX_SCHAEDEN = 50            # feste Hoechstzahl an Schadens-Eintraegen
MAX_SCHADEN_LAENGE = 300     # feste Hoechstlaenge je Eintrag

# Personenbezogene Muster: Eintraege mit solchen Inhalten werden NICHT
# dauerhaft uebernommen (komplett verworfen, nicht teilredigiert).
_PII_MUSTER = (
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),   # E-Mail
    re.compile(r"(?:\+?\d[\s\-/()]*){8,}"),                          # Telefon
    re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b"),                          # FIN/VIN
    # Strasse + Hausnummer ("Musterstr. 12", "Am Kanal 3a", "Hauptstrasse 7")
    re.compile(r"(?i)\b[\w\u00e4\u00f6\u00fc\u00df.-]*(stra\u00dfe|strasse|str\.|weg|allee|platz|gasse|ring|damm|ufer)\s*\d{1,4}\s*[a-z]?\b"),
    re.compile(r"\b\d{5}\s+[A-Z\u00c4\u00d6\u00dc][a-z\u00e4\u00f6\u00fc\u00df]{2,}"),   # PLZ + Ort
    re.compile(r"\b[A-Z\u00c4\u00d6\u00dc]{1,3}-[A-Z\u00c4\u00d6\u00dc]{1,2}\s?\d{1,4}[EH]?\b"),  # Kennzeichen
    re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\b"),         # IBAN
)


def _zahl(wert) -> Optional[int]:
    """'242.000 km' / '110' / 110.0 -> int; sonst None."""
    if wert is None:
        return None
    if isinstance(wert, (int, float)):
        return int(wert)
    ziffern = re.sub(r"[^0-9]", "", str(wert))
    return int(ziffern) if ziffern else None


def ez_normalisieren(wert) -> Optional[str]:
    """Erstzulassung auf 'JJJJ-MM' (bzw. 'JJJJ') bringen — damit sind
    Bereichsfilter ein simpler String-Vergleich, egal ob die Quelle
    '01/2020', '2020-01', '2020' oder '1.2020' geliefert hat."""
    if wert in (None, ""):
        return None
    s = str(wert).strip()
    m = re.search(r"(\d{1,2})\s*[./\-]\s*(\d{4})", s)          # MM/JJJJ
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.search(r"(\d{4})\s*-\s*(\d{1,2})", s)                # JJJJ-MM
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.search(r"(19|20)\d{2}", s)                           # nur Jahr
    return m.group(0) if m else None


def schaeden_bereinigen(rohe: List[Any]) -> List[str]:
    """Schadens-Texte fuer die dauerhafte Speicherung absichern:
    reiner Text, Whitespace normalisiert, Laengen-/Anzahl-Deckel,
    leere raus, Eintraege mit erkennbaren Personendaten verworfen."""
    sauber: List[str] = []
    for eintrag in rohe or []:
        if isinstance(eintrag, dict):
            # Skizzen-Eintraege: nur die textlichen Teile uebernehmen.
            text = " ".join(str(eintrag.get(k, "")).strip() for k in
                            ("label", "part", "type", "note", "text")
                            if eintrag.get(k))
        else:
            text = str(eintrag or "")
        # Reiner Text: HTML-Tags raus, Whitespace normalisieren. Das
        # Dashboard rendert ohnehin nur Text (kein innerHTML) — der
        # Strip ist die zweite Schutzschicht fuer die dauerhafte Ablage.
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1\s*>", " ", text)
        text = re.sub(r"<[^>]*>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if any(m.search(text) for m in _PII_MUSTER):
            continue                       # PII: nicht dauerhaft speichern
        sauber.append(text[:MAX_SCHADEN_LAENGE].strip())
        if len(sauber) >= MAX_SCHAEDEN:
            break
    return sauber


def kaufdatum(wert) -> Optional[str]:
    """Kaufdatum als 'JJJJ-MM-TT' (nur der Tag — kein Zeitstempel, der sich
    mit Log- oder Vertragszeiten abgleichen liesse)."""
    if not wert:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(wert))
    return m.group(1) if m else None


def daten_extrahieren(contract_dict: Dict[str, Any],
                      vehicle: Dict[str, Any],
                      gekauft_am: Optional[str] = None) -> Dict[str, Any]:
    """Whitelist-Extraktion aus Vertrag + Fahrzeugdaten. Alles andere
    (IDs, Personen, FIN, Fotos, Links) wird bewusst NICHT uebernommen.
    `gekauft_am`: Tag der Vertragserstellung (Wunsch 09/2026: Kaufdatum
    in der Auto-Daten-Ansicht); None laesst ein vorhandenes Datum stehen."""
    c, v = contract_dict or {}, vehicle or {}

    def erst(*werte):
        for w in werte:
            if w not in (None, ""):
                return w
        return None

    preis = c.get("purchase_price")
    preis_cents = None
    if isinstance(preis, (int, float)) and preis >= 0:
        preis_cents = int(round(float(preis) * 100))

    schaeden_roh: List[Any] = []
    for feld in ("vehicle_damage_note", "damages_text"):
        if c.get(feld):
            schaeden_roh.append(c[feld])
    if isinstance(c.get("damages"), list):
        schaeden_roh.extend(c["damages"])

    daten = {
        "brand": erst(c.get("vehicle_make"), v.get("make_label"), v.get("make")),
        "model": erst(c.get("vehicle_model"), v.get("model_label"), v.get("model")),
        "first_registration": ez_normalisieren(
            erst(c.get("vehicle_first_registration"), v.get("first_registration"))),
        "mileage_km": _zahl(erst(c.get("vehicle_mileage"), v.get("mileage"))),
        "fuel_type": erst(c.get("vehicle_fuel"), v.get("fuel_label"), v.get("fuel")),
        "power_ps": _zahl(erst(c.get("vehicle_power_ps"), v.get("power_ps"))),
        "power_kw": _zahl(erst(c.get("vehicle_power_kw"), v.get("power_kw"))),
        "purchase_price_cents": preis_cents,
        "currency": "EUR",
        "damages": schaeden_bereinigen(schaeden_roh),
        "schema_version": SCHEMA_VERSION,
    }
    tag = kaufdatum(gekauft_am)
    if tag:
        daten["purchase_date"] = tag
    return daten


async def anlegen(db, contract_dict: Dict[str, Any],
                  vehicle: Dict[str, Any],
                  gekauft_am: Optional[str] = None) -> str:
    """Neuen Auto-Datensatz anlegen; liefert dessen zufaellige UUID.
    Idempotenter Upsert auf die frisch erzeugte id."""
    from datetime import datetime, timezone
    datensatz_id = str(uuid.uuid4())
    daten = daten_extrahieren(
        contract_dict, vehicle,
        gekauft_am or datetime.now(timezone.utc).isoformat())
    await db[COLLECTION].update_one(
        {"id": datensatz_id},
        {"$set": daten, "$setOnInsert": {"id": datensatz_id}},
        upsert=True)
    return datensatz_id


async def zurueckrollen(db, datensatz_id: str) -> None:
    """Rollback fuer den Teilfehler-Fall: der Vertrag wurde NIE
    gespeichert, also darf auch der Auto-Datensatz nicht bleiben."""
    if datensatz_id:
        await db[COLLECTION].delete_one({"id": datensatz_id})


async def aktualisieren(db, datensatz_id: str,
                        contract_dict: Dict[str, Any],
                        vehicle: Dict[str, Any],
                        gekauft_am: Optional[str] = None) -> None:
    """Zulaessige Vertragskorrektur innerhalb der 90 Tage: den
    BESTEHENDEN Datensatz aktualisieren (kein Duplikat). Das Kaufdatum
    wird nur gesetzt, wenn es mitgegeben wird (Korrektur aendert es nicht)."""
    if not datensatz_id:
        return
    daten = daten_extrahieren(contract_dict, vehicle, gekauft_am)
    await db[COLLECTION].update_one({"id": datensatz_id}, {"$set": daten})


async def nachtragen(db, contract_doc: Dict[str, Any]) -> Optional[str]:
    """Reparatur/Backfill: ein Vertrag OHNE admin_vehicle_data_id
    (Bestandsdaten oder unvollstaendiger Schreibvorgang) bekommt seinen
    Datensatz nachgezogen. Atomarer $exists-Guard verhindert, dass zwei
    parallele Nachtraege zwei Datensaetze erzeugen."""
    cd = contract_doc.get("contract_data") or {}
    vehicle = {"make_label": contract_doc.get("make"),
               "model_label": contract_doc.get("model")}
    # Solange der Vertrag existiert, liefert das Fahrzeugdokument km/EZ/
    # Kraftstoff/PS als Fallback (im Vertrag stehen nur die vom Haendler
    # ueberschriebenen Werte). Gespeichert wird trotzdem nur die Whitelist.
    if contract_doc.get("vehicle_id"):
        v = await db.vehicles.find_one(
            {"id": contract_doc["vehicle_id"],
             "dealer_id": contract_doc.get("dealer_id")}, {"_id": 0, "data": 1})
        if v and isinstance(v.get("data"), dict):
            vehicle = {**v["data"], **{k: w for k, w in vehicle.items() if w}}
    datensatz_id = await anlegen(db, cd, vehicle,
                                 gekauft_am=contract_doc.get("created_at"))
    res = await db.generated_pdfs.update_one(
        {"id": contract_doc["id"],
         "admin_vehicle_data_id": {"$exists": False}},
        {"$set": {"admin_vehicle_data_id": datensatz_id}})
    if not res.modified_count:
        await zurueckrollen(db, datensatz_id)   # jemand war schneller
        return None
    return datensatz_id
