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
import math
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
    # "Am Kanal 3a", "An der Weide 7", "Im Winkel 12" (Runde 5)
    re.compile(r"\b(Am|An der|An den|Im|In der|Zum|Zur|Auf dem|Auf der|Hinter dem|Unter den)\s+[A-Z\u00c4\u00d6\u00dc][\w\u00e4\u00f6\u00fc\u00df-]+\s+\d{1,4}\s*[a-z]?\b"),
)

# Freitext-Schaeden (Notiz/Skizzen-Text) dauerhaft speichern? Namen oder
# ungewoehnlich geschriebene Personendaten kann kein Filter sicher erkennen
# (Pruefbericht Runde 5). Mit AUTO_DATEN_SCHAEDEN_FREITEXT=false werden nur
# die vordefinierten Skizzen-Bezeichnungen uebernommen, kein Freitext.
# Standard seit Go-Live-Audit 09/2026: AUS (produktionssicher); Altbestand
# bereinigt scripts/schaeden_freitext_bereinigen.py.
import os as _os
SCHAEDEN_FREITEXT = _os.environ.get("AUTO_DATEN_SCHAEDEN_FREITEXT", "false") \
    .strip().lower() in ("1", "true", "yes", "ja")


_DEZIMAL = re.compile(r"^(\d[\d.,]*?)[.,](\d{1,2})$")


def _zahl(wert) -> Optional[int]:
    """'242.000 km' / '111,016 km' / '1.984 ccm' / '110' / '110.5' / 110.0 -> int;
    sonst None.

    Pruefung 14.09.2026 (A19): vorher wurden ALLE Nicht-Ziffern entfernt —
    aus '110.5' wurde 1105, aus '12,5' 125, und das landete dauerhaft in den
    anonymen Auto-Daten. Jetzt: ein Punkt oder Komma mit genau drei Ziffern
    dahinter ist ein Tausendertrenner, mit ein oder zwei Ziffern ein
    Dezimaltrenner (kaufmaennisch gerundet). Buchstaben mitten in der Zahl
    ('1e400') ergeben None."""
    if wert is None or isinstance(wert, bool):
        return None
    if isinstance(wert, (int, float)):
        if isinstance(wert, float) and not math.isfinite(wert):
            return None
        return int(math.floor(wert + 0.5)) if wert >= 0 else int(wert)
    s = str(wert).strip()
    if not s:
        return None
    m = re.match(r"^\s*-?\s*(\d[\d.,\s]*)", s)
    if not m:
        return None
    rest = s[m.end():].strip()
    if rest and not re.match(r"^[A-Za-z€/²³]{1,6}$", rest):
        return None          # '1e400', '12x' — kein Zahlwert
    t = m.group(1).replace(" ", "")
    dm = _DEZIMAL.match(t)
    if dm:
        ganz = re.sub(r"[.,]", "", dm.group(1))
        wert_f = float(f"{ganz}.{dm.group(2)}")
        return int(math.floor(wert_f + 0.5))
    ziffern = re.sub(r"[^0-9]", "", t)
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
            # Skizzen-Eintraege: vordefinierte Bezeichnungen uebernehmen.
            # Audit 09/2026: die Skizze liefert type_label + zone (vorher nur
            # label/part/type/note/text gelesen -> bei Freitext=aus blieb
            # nichts uebrig). Notiz/Freitext nur, wenn SCHAEDEN_FREITEXT.
            art = str(eintrag.get("type_label") or eintrag.get("type")
                      or eintrag.get("kategorie") or "").strip()
            teil = str(eintrag.get("zone") or eintrag.get("part_label")
                       or eintrag.get("part") or eintrag.get("label") or "").strip()
            teile = [t for t in (art, teil) if t]
            text = ": ".join(teile) if len(teile) == 2 else " ".join(teile)
            if SCHAEDEN_FREITEXT:
                notiz = " ".join(str(eintrag.get(k, "")).strip()
                                 for k in ("note", "text") if eintrag.get(k))
                text = (text + " " + notiz).strip() if notiz else text
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
    # Wunsch Ahmad 15.09.2026: der Einkaufspreis, fuer den man zum Auto
    # gefahren ist, bleibt stehen. Wurde vor Ort nachverhandelt (die
    # Neuerzeugung des Vertrags traegt preis_vor_abholung), steht der neue
    # Preis in der eigenen Spalte preis_vor_ort_cents.
    vor_ort_cents = None
    alt = c.get("preis_vor_abholung")
    if isinstance(alt, (int, float)) and alt >= 0 and preis_cents is not None:
        vor_ort_cents = preis_cents
        preis_cents = int(round(float(alt) * 100))

    schaeden_roh: List[Any] = []
    if SCHAEDEN_FREITEXT:
        for feld in ("vehicle_damage_note", "damages_text"):
            if c.get(feld):
                schaeden_roh.append(c[feld])
    if isinstance(c.get("damages"), list):
        # Skizzen-Eintraege (dict) immer; nackte Freitext-Strings nur mit Schalter
        schaeden_roh.extend(d for d in c["damages"]
                            if isinstance(d, dict) or SCHAEDEN_FREITEXT)

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
    if vor_ort_cents is not None:
        daten["preis_vor_ort_cents"] = vor_ort_cents
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
                        gekauft_am: Optional[str] = None) -> bool:
    """Zulaessige Vertragskorrektur innerhalb der 90 Tage: den
    BESTEHENDEN Datensatz aktualisieren (kein Duplikat). Das Kaufdatum
    wird nur gesetzt, wenn es mitgegeben wird (Korrektur aendert es nicht)."""
    if not datensatz_id:
        return False
    daten = daten_extrahieren(contract_dict, vehicle, gekauft_am)
    res = await db[COLLECTION].update_one({"id": datensatz_id}, {"$set": daten})
    # Runde 19 (Nr. 40): der Aufrufer muss wissen, ob der Datensatz noch da war.
    return bool(res.matched_count)


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


async def bestehenden_datensatz(db, dealer_id: str, vehicle_id: Optional[str],
                                mobile_ad_id: Optional[str] = None) -> Optional[str]:
    """Wunsch Ahmad 15.09.2026: ein neuer Kaufvertrag zu DEMSELBEN Fahrzeug
    (Nachverhandlung, neuer Preis) fuehrt den vorhandenen Auto-Datensatz nach,
    statt ein zweites Auto in den Auto-Daten anzulegen. Liefert die id des
    Datensatzes des juengsten, nicht geloeschten Vertrags dieser Firma zu
    diesem Fahrzeug — oder None (kein Vertrag, Datensatz vom Betreiber
    entfernt, Datensatz fehlt)."""
    # Runde 19 (Nr. 39): NUR ueber die quellenspezifische Fahrzeug-ID —
    # mobile_ad_id allein kann auf zwei Portalen dasselbe Zahlenkuerzel tragen
    # (Kleinanzeige 123456 vs. mobile.de 123456) und haette zwei verschiedene
    # Autos zu einem Datensatz verschmolzen. mobile_ad_id wird ignoriert.
    if not dealer_id or not vehicle_id:
        return None
    c = await db.generated_pdfs.find_one(
        {"dealer_id": dealer_id, "vehicle_id": vehicle_id,
         "admin_vehicle_data_id": {"$type": "string"},
         "auto_daten_entfernt_am": {"$exists": False},
         "loeschung.status": {"$ne": "laeuft"}},
        {"_id": 0, "admin_vehicle_data_id": 1}, sort=[("created_at", -1)])
    if not c:
        return None
    datensatz_id = c["admin_vehicle_data_id"]
    if not await db[COLLECTION].count_documents({"id": datensatz_id}, limit=1):
        return None
    return datensatz_id


async def entfernen(db, datensatz_id: str) -> bool:
    """Wunsch Ahmad 15.09.2026: der Betreiber loescht einen Auto-Datensatz
    endgueltig. Vertraege, die ihn noch tragen, bekommen den Vermerk
    auto_daten_entfernt_am: die Fristloeschung verlangt dann keinen Datensatz
    mehr, die Reparatur legt keinen neuen an, und ein weiterer Vertrag zu dem
    Fahrzeug beginnt mit einem frischen Datensatz."""
    if not datensatz_id:
        return False
    from datetime import datetime, timezone
    if not await db[COLLECTION].count_documents({"id": datensatz_id}, limit=1):
        return False
    # Runde 19 (Nr. 42): ZUERST die Vertraege markieren, DANN loeschen — bricht
    # es dazwischen ab, steht der Datensatz noch (harmlos), statt dass ein
    # Vertrag ohne Vermerk auf einen fehlenden Datensatz zeigt.
    await db.generated_pdfs.update_many(
        {"admin_vehicle_data_id": datensatz_id},
        {"$set": {"auto_daten_entfernt_am": datetime.now(timezone.utc).isoformat()}})
    res = await db[COLLECTION].delete_one({"id": datensatz_id})
    if not res.deleted_count:
        await db.generated_pdfs.update_many({"admin_vehicle_data_id": datensatz_id},
                                            {"$unset": {"auto_daten_entfernt_am": ""}})
        return False
    return True


async def nachfuehren(db, contract_doc: Dict[str, Any]) -> bool:
    """Runde 19 (Nr. 45): den Datensatz eines bestehenden Vertrags aus dessen
    aktueller Fassung nachfuehren (Neuerzeugung, Korrektur). Fehlt der Datensatz
    (ohne Vermerk), wird er neu angelegt. Entfernt den Merker
    auto_daten_nachfuehrung_offen. Liefert True, wenn der Vertrag danach einen
    passenden Datensatz hat."""
    cd = contract_doc.get("contract_data") or {}
    vehicle = {"make_label": contract_doc.get("make"),
               "model_label": contract_doc.get("model")}
    if contract_doc.get("vehicle_id"):
        v = await db.vehicles.find_one(
            {"id": contract_doc["vehicle_id"],
             "dealer_id": contract_doc.get("dealer_id")}, {"_id": 0, "data": 1})
        if v and isinstance(v.get("data"), dict):
            vehicle = {**v["data"], **{k: w for k, w in vehicle.items() if w}}
    ok = False
    if contract_doc.get("auto_daten_entfernt_am"):
        ok = True                                   # bewusst ohne Datensatz
    elif contract_doc.get("admin_vehicle_data_id"):
        ok = await aktualisieren(db, contract_doc["admin_vehicle_data_id"], cd, vehicle)
        if not ok:
            await db.generated_pdfs.update_one(
                {"id": contract_doc["id"],
                 "admin_vehicle_data_id": contract_doc["admin_vehicle_data_id"]},
                {"$unset": {"admin_vehicle_data_id": ""}})
            ok = bool(await nachtragen(db, {**contract_doc, "admin_vehicle_data_id": None}))
    else:
        ok = bool(await nachtragen(db, contract_doc))
    if ok:
        await db.generated_pdfs.update_one({"id": contract_doc["id"]},
                                           {"$unset": {"auto_daten_nachfuehrung_offen": ""}})
    return ok


SPERRE_SEKUNDEN = 15


async def vertrag_sperre(db, dealer_id: str, vehicle_id, sekunden: int = SPERRE_SEKUNDEN) -> Optional[str]:
    """Runde 19 (Nr. 38): kurze Sperre je Firma+Fahrzeug fuer die Vertragsanlage,
    damit zwei gleichzeitige ERSTE Vertraege desselben Autos nicht zwei
    Datensaetze anlegen. Wartet bis ~6 s; danach geht es ohne Sperre weiter
    (kein Blocker fuer die Sucher). Liefert den Schluessel oder None."""
    if not dealer_id or not vehicle_id:
        return None
    import asyncio
    from datetime import datetime, timedelta, timezone
    from pymongo.errors import DuplicateKeyError
    schluessel = f"auto_daten:{dealer_id}:{vehicle_id}"
    for _ in range(12):
        jetzt = datetime.now(timezone.utc)
        try:
            await db.sperren.update_one(
                {"_id": schluessel, "$or": [{"bis": None}, {"bis": {"$lt": jetzt}}]},
                {"$set": {"bis": jetzt + timedelta(seconds=sekunden)}}, upsert=True)
            return schluessel
        except DuplicateKeyError:
            await asyncio.sleep(0.5)
    return None


async def sperre_freigeben(db, schluessel: Optional[str]) -> None:
    if not schluessel:
        return
    try:
        await db.sperren.delete_one({"_id": schluessel})
    except Exception:  # noqa: BLE001 — laeuft sonst nach 15 s ab
        pass


async def vor_ort_nachtragen(db, contract_id: str, dealer_id: str,
                             preis=None, maengel: Optional[List[Any]] = None) -> bool:
    """Wunsch Ahmad 15.09.2026: das Ergebnis der Abholung in den Auto-Datensatz —
    der vor Ort nachverhandelte Preis (eigene Spalte, der urspruengliche
    Einkaufspreis bleibt) und die vom Fahrer vor Ort festgehaltenen Maengel
    (gefiltert wie die Vertragsschaeden). Liefert True, wenn geschrieben."""
    if not contract_id:
        return False
    c = await db.generated_pdfs.find_one(
        {"id": contract_id, "dealer_id": dealer_id,
         "auto_daten_entfernt_am": {"$exists": False}},
        {"_id": 0, "admin_vehicle_data_id": 1})
    if not c or not c.get("admin_vehicle_data_id"):
        return False
    werte: Dict[str, Any] = {}
    if isinstance(preis, (int, float)) and not isinstance(preis, bool) and preis >= 0:
        werte["preis_vor_ort_cents"] = int(round(float(preis) * 100))
    if maengel is not None:
        werte["maengel_vor_ort"] = schaeden_bereinigen(list(maengel))
    if not werte:
        return False
    res = await db[COLLECTION].update_one({"id": c["admin_vehicle_data_id"]}, {"$set": werte})
    return bool(res.matched_count)
