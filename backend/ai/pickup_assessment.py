# -*- coding: utf-8 -*-
"""KI-Abholbewertung (Wunsch Ahmad 25.09.2026): Was kostet, was der Fahrer vor
Ort anders vorfindet als im Vertrag/Inserat steht?

Ablauf: Fahrer schickt das Protokoll "zur Freigabe" -> das Backend baut aus
Vertrag, Inserat und Fahrereingaben ein KLEINES, normalisiertes Paket (ohne
Verkaeuferdaten), fragt die KI EINMAL fuer alle Abweichungen, prueft die
Antwort (schemas.bereinigen) und legt sie in ki_bewertungen ab — gebunden an
die Protokollversion (revision) und einen Hash der Eingabe. Aendert sich die
Eingabe, gilt die alte Bewertung als "veraltet" und wird neu gerechnet.

Die KI ist rein beratend: sie aendert keinen Preis, gibt nichts frei, schreibt
keinen Vertrag. Ohne relevante Abweichung gibt es keinen Aufruf (keine Kosten).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

import protokoll_vergleich as PV
from deps import db, now_iso

from ai import kalibrierung, marktdaten, preisbasis, schemas
from ai.provider import json_bewerten, ki_aktiv, ki_modell

log = logging.getLogger("autohandel.ki")

SAMMLUNG = "ki_bewertungen"
LERN_SAMMLUNG = "ki_lernfaelle"
KM_TOLERANZ = PV.KM_TOLERANZ          # unter dieser Differenz zaehlt km nicht

# Abschnitt-4-Werte des Fahrers, die als technische Auffaelligkeit gelten
_ZUSTAND_AUFFAELLIG = {
    "driving": ({"Mängel", "Maengel"}, "technical", "Fahrverhalten mit Mängeln (Probefahrt)"),
    "battery": ({"schwach", "defekt"}, "technical", "Batterie/Starter"),
    "warning_lights": ({"ja"}, "warning_light", "Kontrollleuchten leuchten"),
}
_TEURE_DOKUMENTE = ("Zulassungsbescheinigung Teil II", "Zulassungsbescheinigung Teil I",
                    "Servicebuch", "Scheckheft", "HU", "COC", "Zweitsatz Reifen")

SYSTEM_PROMPT = """Du bist der Bewertungsdienst von AutoSchnell, einer Software fuer Autohaendler in Deutschland.
Ein Fahrer holt ein gekauftes Gebrauchtfahrzeug beim Verkaeufer ab und stellt Abweichungen zum Kaufvertrag/Inserat fest. Du bewertest NUR den wirtschaftlichen Einfluss dieser Abweichungen in Euro, damit der Firmenchef mit dem Verkaeufer nachverhandeln kann.

Regeln:
1. Du bekommst Fahrzeugdaten, Preise, bekannte Schaeden (standen schon im Vertrag) und die vor Ort festgestellten Abweichungen. Bewerte NUR die Abweichungen (new_damages und deviations). Bekannte Schaeden nie erneut als Nachlass ansetzen.
2. Je Abweichung: Reparaturweg, geschaetzte Reparaturkosten, empfohlener Nachlass als EIN Hauptwert, ein enger realistischer Bereich (hoechstens +-15 bis 20 % um den Hauptwert), Sicherheit 0..1, ein Satz Begruendung. Nachlass = das, was der Haendler wegen dieser Abweichung weniger zahlen sollte (Reparatur + Aufwand + Wertminderung), nicht nur die reine Reparatur.
3. Keine grossen Spannen. Fehlt dir eine konkrete Angabe fuer eine seriöse Schaetzung (z. B. Lack beschaedigt?, Groesse?), gib die Position mit deiner besten Schaetzung ab UND stelle in needs_information genau EINE konkrete Frage mit 2-4 Antwortmoeglichkeiten.
4. Unfallfreiheit weicht ab, Warnleuchten (Motor/Getriebe/Airbag/ABS), Fahrverhalten mit Maengeln, Durchrostung: manual_review_required=true, priority "rot", Nachlass 0 (keine scheinpraezise Zahl), Grund nennen.
5. Beruecksichtige Fahrzeugwert, Alter, Kilometer, Klasse und Marke (z. B. Schluessel/Scheinwerfer bei Premiummarken teurer; kleine Delle am 3.000-EUR-Auto anders als am 40.000-EUR-Auto). Ein Nachlass darf nie ueber dem Kaufpreis liegen und selten ueber 30 % davon.
6. combined: Einzelsumme, Abzug fuer ueberlappende Arbeiten (z. B. zwei Schaeden am selben Bauteil = eine Lackierung), empfohlener Gesamtnachlass, enger Bereich, Verhandlungseinstieg (10-25 % ueber dem empfohlenen Nachlass), Sicherheit.
7. arguments: hoechstens 4 kurze, sachliche Saetze fuer das Gespraech mit dem Verkaeufer (deutsch, Sie-Form vermeiden, neutral: "Der Kotfluegel vorne rechts hat eine nicht dokumentierte Delle.").
8. Preise in Euro inkl. MwSt. (Deutschland 2026). Nutze die Ausgangswerte unten als Orientierung und passe sie an das konkrete Fahrzeug an. Antworte ausschliesslich nach dem vorgegebenen JSON-Schema, alle Texte auf Deutsch.
9. Knapp: title hoechstens 8 Woerter, reason hoechstens 15 Woerter, repair_method hoechstens 6 Woerter, hoechstens 3 arguments mit je hoechstens 20 Woertern, hoechstens 2 needs_information. Bekannte Schaeden (already_in_contract=true) NICHT als Position ausgeben.
10. driver_answers sind Antworten des Fahrers auf fruehere Rueckfragen (source_id = Schaden/Abweichung). Nutze sie fuer die Schaetzung und stelle dieselbe Frage nicht erneut.

""" + preisbasis.basis_als_text()


# ------------------------------------------------ Paket bauen
def _alter_jahre(ez_text: str) -> Optional[float]:
    """Fahrzeugalter in Jahren aus 'MM/JJJJ' (oder nur Jahr)."""
    m = re.search(r"(\d{4})", str(ez_text or ""))
    if not m:
        return None
    jahr = int(m.group(1))
    mon = re.match(r"\s*(\d{1,2})\s*/", str(ez_text))
    monat = int(mon.group(1)) if mon else 6
    from datetime import date
    heute = date.today()
    return round(max(0.0, (heute.year - jahr) + (heute.month - monat) / 12.0), 1)


def _zahl(w) -> Optional[float]:
    z = PV.zahl(w)
    return float(z) if z is not None else None


def _schaden_kurz(d: dict) -> dict:
    """Ein Schaden aus der Skizze, ohne Anzeige-Koordinaten."""
    sd = d.get("severity_data") if isinstance(d.get("severity_data"), dict) else {}
    return {"id": str(d.get("id") or ""), "type": d.get("type_key") or d.get("type") or "",
            "label": d.get("type_label") or d.get("label") or "",
            "zone": d.get("zone") or d.get("part_label") or d.get("part") or "",
            "view": d.get("view") or "", "note": (d.get("note") or d.get("text") or "")[:200],
            "severity_data": {str(k)[:40]: str(v)[:60] for k, v in sd.items()}}


def _schaden_schluessel(d: dict) -> str:
    return f"{(d.get('type_key') or d.get('type') or '').lower()}|{(d.get('zone') or d.get('part_label') or '').lower()}"


def paket_bauen(protokoll: dict, appt: dict, vehicle: dict, contract: dict) -> Dict[str, Any]:
    """Normalisiertes Eingabepaket fuer die KI — nur, was fuer die Bewertung
    zaehlt. Keine Verkaeuferdaten (Name, Telefon, Adresse, Ausweis)."""
    from routes.protocols import vertragspreis_vor_abholung  # spaet: Kreisimport
    werte = PV.vertragswerte(vehicle, contract)
    zeilen = PV.vergleich(PV.FELDER, protokoll.get("vehicle_check") or {},
                          protokoll.get("condition") or {}, werte)
    kaufpreis = _zahl(vertragspreis_vor_abholung(contract))
    ez = werte.get("first_registration", {}).get("text") or ""
    kw = werte.get("power", {}).get("wert")
    # Wunsch Ahmad 26.09.2026: EZ, PS, Marke, Modell (und Alter, Hubraum,
    # Farbe, Klasse) ausdruecklich mitgeben — die KI soll so genau wie
    # moeglich auf das konkrete Fahrzeug rechnen.
    fahrzeug = {
        "make": werte.get("make", {}).get("text") or "",
        "model": werte.get("model", {}).get("text") or "",
        "variant": (vehicle.get("model_description") or "")[:120],
        "first_registration": ez,
        "age_years": _alter_jahre(ez),
        "mileage_contract_km": werte.get("mileage_contract", {}).get("wert"),
        "mileage_pickup_km": PV.zahl((protokoll.get("condition") or {}).get("mileage")),
        "power_kw": kw,
        "power_ps": PV.zahl(vehicle.get("power_ps")) or (round(float(kw) * 1.36) if kw else None),
        "displacement_ccm": PV.zahl(vehicle.get("displacement") or vehicle.get("cubic_capacity")),
        "fuel": werte.get("fuel", {}).get("text") or "",
        "gearbox": (vehicle.get("gearbox_label") or vehicle.get("gearbox") or "")[:40],
        "category": (vehicle.get("category_label") or vehicle.get("category") or "")[:40],
        "color": (vehicle.get("exterior_color") or vehicle.get("color") or "")[:40],
        "previous_owners_contract": werte.get("previous_owners", {}).get("wert"),
        "hu_contract": werte.get("hu", {}).get("text") or "",
        "accident_free_contract": werte.get("accident_free", {}).get("wert"),
    }
    preise = {"contract_price_eur": kaufpreis,
              "listing_price_eur": _zahl(vehicle.get("price")),
              "driver_proposal_eur": _zahl(protokoll.get("preis_vorschlag"))}
    bekannt = [_schaden_kurz(d) for d in (contract.get("damages") or vehicle.get("damages") or [])
               if isinstance(d, dict)]
    bekannte_maengel = [str(m)[:200] for m in (vehicle.get("known_defects") or []) if str(m or "").strip()][:20]
    bekannt_keys = {_schaden_schluessel(d) for d in (contract.get("damages") or vehicle.get("damages") or [])
                    if isinstance(d, dict)}
    neu = []
    for d in protokoll.get("new_damages") or []:
        if not isinstance(d, dict):
            continue
        k = _schaden_kurz(d)
        # Derselbe Schaden (Art + Bauteil) stand schon im Vertrag -> nicht neu,
        # hoechstens "schlimmer" — das sagt der Fahrer ueber note/severity.
        k["already_in_contract"] = _schaden_schluessel(d) in bekannt_keys
        neu.append(k)

    abweichungen: List[Dict[str, Any]] = []
    for z in PV.abweichungen(zeilen):
        if not z.get("abweichend"):
            continue
        s = z.get("schluessel")
        soll, ist = z.get("vertrag_text") or "", z.get("vor_ort_text") or ""
        eintrag = {"id": f"dev:{s}", "field": s, "label": z.get("feld"), "expected": soll, "actual": ist}
        if s == "mileage_contract":
            a, b = PV.zahl(soll), PV.zahl(ist)
            if a is not None and b is not None:
                diff = b - a
                if diff < KM_TOLERANZ:
                    continue
                eintrag.update(type="mileage", difference_km=diff)
            else:
                eintrag["type"] = "mileage"
        elif s == "previous_owners":
            a, b = PV.zahl(soll), PV.zahl(ist)
            if a is not None and b is not None and b <= a:
                continue
            eintrag.update(type="previous_owners", difference=(b - a) if (a is not None and b is not None) else None)
        elif s == "accident_free":
            eintrag["type"] = "accident_history"
        elif s == "hu":
            eintrag["type"] = "hu"
        elif s in ("make", "model", "first_registration", "vin", "power", "fuel", "color", "commercial"):
            eintrag["type"] = "technical" if s in ("power", "fuel", "first_registration") else "other"
        else:
            eintrag["type"] = "other"
        abweichungen.append(eintrag)

    # Schluessel: Soll aus dem Protokoll (keys_expected) oder dem Vertrag
    soll_schl = PV.zahl(protokoll.get("keys_expected")) or PV.zahl(contract.get("schluessel_anzahl"))
    ist_schl = PV.zahl(protokoll.get("keys_count"))
    if soll_schl is not None and ist_schl is not None and ist_schl < soll_schl:
        abweichungen.append({"id": "dev:keys", "type": "keys", "field": "keys",
                             "label": "Schlüssel", "expected": soll_schl, "actual": ist_schl,
                             "missing": soll_schl - ist_schl})
    # Ausstattung (Abschnitt 3): false = fehlt / funktioniert nicht
    for name, ok in (protokoll.get("features") or {}).items():
        if ok is False:
            abweichungen.append({"id": f"dev:feature:{name[:60]}", "type": "equipment_missing",
                                 "field": "features", "label": "Ausstattung",
                                 "expected": f"{name} vorhanden (laut Inserat)",
                                 "actual": "fehlt oder funktioniert nicht"})
    # Dokumente (Abschnitt 2): false = fehlt
    for name, ok in (protokoll.get("documents") or {}).items():
        if ok is False:
            abweichungen.append({"id": f"dev:doc:{name[:60]}", "type": "documents",
                                 "field": "documents", "label": "Unterlagen",
                                 "expected": f"{name} vorhanden", "actual": "fehlt",
                                 "economically_relevant_hint": any(t.lower() in name.lower() for t in _TEURE_DOKUMENTE)})
    # Zustand (Abschnitt 4)
    zustand = protokoll.get("condition") or {}
    for feld, (werte_auffaellig, typ, label) in _ZUSTAND_AUFFAELLIG.items():
        w = str(zustand.get(feld) or "").strip()
        if w in werte_auffaellig:
            abweichungen.append({"id": f"dev:{feld}", "type": typ, "field": feld,
                                 "label": label, "expected": "ohne Befund", "actual": w})
    reifen = str(zustand.get("tire_profile") or "").strip()
    if reifen and re.search(r"\b(schlecht|abgefahren|runter|mangel|risse|platt|[0-2](?:[,.]\d)?\s*mm)\b", reifen.lower()):
        abweichungen.append({"id": "dev:tires", "type": "tires", "field": "tire_profile",
                             "label": "Reifen", "expected": "fahrbereit laut Inserat",
                             "actual": reifen[:120]})

    # Stufe 3 (26.09.2026): Antworten des Fahrers auf Rueckfragen des Chefs
    # (Ja/Nein/Unklar-Knopf in der Fahrer-App) — Teil der Eingabe, damit die
    # Bewertung danach neu gerechnet wird.
    antworten = []
    for a in (protokoll.get("rueckfrage_antworten") or [])[:10]:
        if isinstance(a, dict) and str(a.get("answer") or "").strip():
            antworten.append({"source_id": str(a.get("source_id") or "")[:200],
                              "question": str(a.get("question") or "")[:300],
                              "answer": str(a.get("answer") or "")[:100]})
    return {
        "vehicle": fahrzeug,
        "prices": preise,
        "known_damages": bekannt,
        "known_defects_listing": bekannte_maengel,
        "new_damages": neu,
        "deviations": abweichungen,
        "driver_answers": antworten,
        "driver_notes": (protokoll.get("notes") or "")[:500],
    }


def relevant(paket: Dict[str, Any]) -> bool:
    """Gibt es ueberhaupt etwas zu bewerten? Sonst kein KI-Aufruf."""
    neue = [d for d in paket.get("new_damages") or [] if not d.get("already_in_contract")]
    return bool(neue or paket.get("deviations"))


def eingabe_hash(paket: Dict[str, Any]) -> str:
    roh = json.dumps(paket, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()


# ------------------------------------------------ Laden der Grundlagen
async def _grundlagen(protocol_id: str, dealer_id: str) -> Optional[Tuple[dict, dict, dict, dict]]:
    doc = await db.pickup_protocols.find_one({"id": protocol_id, "dealer_id": dealer_id},
                                             {"_id": 0, "pdf_path": 0})
    if not doc:
        return None
    appt = await db.appointments.find_one({"id": doc.get("appointment_id"), "dealer_id": dealer_id},
                                          {"_id": 0}) or {}
    from routes.protocols import _fahrzeug_und_vertrag  # spaet: Kreisimport
    vehicle, contract = await _fahrzeug_und_vertrag(appt or {"dealer_id": dealer_id})
    return doc, appt, vehicle, contract


def _oeffentlich(doc: dict) -> dict:
    """Was die Oberflaeche bekommt (ohne Roh-Antwort und Eingabepaket)."""
    return {
        "status": doc.get("status"), "grund": doc.get("grund") or "",
        "protocol_id": doc.get("protocol_id"), "protocol_revision": doc.get("protocol_revision"),
        "input_hash": doc.get("input_hash"), "modell": doc.get("modell"),
        "prompt_version": doc.get("prompt_version"), "created_at": doc.get("created_at"),
        "dauer_ms": doc.get("dauer_ms"), "ergebnis": doc.get("ergebnis"),
        "kaufpreis": doc.get("kaufpreis"),
    }


# ------------------------------------------------ Bewertung ausfuehren
async def bewertung_ausfuehren(protocol_id: str, dealer_id: str, *, erzwingen: bool = False) -> Optional[dict]:
    """Rechnet die Bewertung fuer den aktuellen Stand des Protokolls und legt
    sie ab. Liefert das gespeicherte Dokument (oeffentliche Form) oder None,
    wenn das Protokoll fehlt. Wirft nie."""
    try:
        grund = await _grundlagen(protocol_id, dealer_id)
        if not grund:
            return None
        doc, appt, vehicle, contract = grund
        paket = paket_bauen(doc, appt, vehicle, contract)
        h = eingabe_hash(paket)
        jetzt = now_iso()
        basis = {"id": str(uuid.uuid4()), "art": "abholung", "dealer_id": dealer_id, "protocol_id": protocol_id,
                 "appointment_id": doc.get("appointment_id"), "protocol_revision": doc.get("revision"),
                 "input_hash": h, "prompt_version": schemas.PROMPT_VERSION, "modell": ki_modell(),
                 "created_at": jetzt, "kaufpreis": paket["prices"].get("contract_price_eur")}
        vorhanden = await db[SAMMLUNG].find_one({"protocol_id": protocol_id, "input_hash": h}, {"_id": 0})
        if vorhanden and not erzwingen and vorhanden.get("status") in ("ok", "keine", "laeuft"):
            return _oeffentlich(vorhanden)
        if not relevant(paket):
            eintrag = {**basis, "status": "keine", "grund": "keine preisrelevanten Abweichungen",
                       "ergebnis": None, "dauer_ms": 0}
            await db[SAMMLUNG].update_one({"protocol_id": protocol_id, "input_hash": h},
                                          {"$set": eintrag}, upsert=True)
            return _oeffentlich(eintrag)
        if not ki_aktiv():
            eintrag = {**basis, "status": "aus", "grund": "KI-Bewertung nicht aktiv", "ergebnis": None, "dauer_ms": 0}
            await db[SAMMLUNG].update_one({"protocol_id": protocol_id, "input_hash": h},
                                          {"$set": eintrag}, upsert=True)
            return _oeffentlich(eintrag)
        # Merker "laeuft" — ein zweiter Aufruf (Chef klickt neu) wartet nicht doppelt.
        await db[SAMMLUNG].update_one({"protocol_id": protocol_id, "input_hash": h},
                                      {"$set": {**basis, "status": "laeuft", "grund": "", "ergebnis": None}},
                                      upsert=True)
        # Zusatz zum System-Prompt (ungecacht): Marktpreise (Stufe 5),
        # Erfahrungswerte (Stufe 4), gezielte Recherche zu diesem Fall.
        fall = await marktdaten.fall_recherche("abholung", paket)
        zusatz = "\n\n".join(t for t in (await marktdaten.prompt_zusatz(),
                                          await kalibrierung.prompt_zusatz(),
                                          marktdaten.fall_als_text(fall)) if t)
        antwort = await json_bewerten(system=SYSTEM_PROMPT, nutzer=paket, schema=schemas.ANTWORT_SCHEMA,
                                      zusatz=zusatz or None)
        usage = dict(antwort.get("usage") or {})
        if fall:
            for k, v in (fall.get("usage") or {}).items():
                usage[k] = int(usage.get(k) or 0) + int(v or 0)
        eintrag = {**basis, "dauer_ms": int(antwort.get("dauer_ms") or 0) + int((fall or {}).get("dauer_ms") or 0),
                   "usage": usage, "modell": antwort.get("modell") or basis["modell"],
                   "recherche": ({"status": fall.get("status"), "suchen": fall.get("suchen"),
                                  "quellen": fall.get("quellen"), "text": fall.get("text")} if fall else None)}
        if antwort.get("status") == "ok" and isinstance(antwort.get("daten"), dict):
            ergebnis = schemas.bereinigen(antwort["daten"], kaufpreis=basis["kaufpreis"])
            _prioritaeten_nachziehen(ergebnis, basis["kaufpreis"])
            ergebnis["quellen"] = list((fall or {}).get("quellen") or [])
            eintrag.update(status="ok", grund="", ergebnis=ergebnis, roh=antwort["daten"],
                           eingabe=paket)
        else:
            eintrag.update(status=antwort.get("status") or "fehler", grund=antwort.get("grund") or "",
                           ergebnis=None, eingabe=paket)
            try:
                import betrieb
                await betrieb.alarm(db, "ki_bewertung_fehlgeschlagen", ref=protocol_id,
                                    status=eintrag["status"], grund=eintrag["grund"][:200])
            except Exception:  # noqa: BLE001
                pass
        await db[SAMMLUNG].update_one({"protocol_id": protocol_id, "input_hash": h},
                                      {"$set": eintrag}, upsert=True)
        return _oeffentlich(eintrag)
    except Exception:  # noqa: BLE001 — die KI ist Beiwerk, nie ein 500
        log.exception("KI-Bewertung %s gescheitert", protocol_id)
        return None


def _prioritaeten_nachziehen(ergebnis: dict, kaufpreis: Optional[float]) -> None:
    """Deterministische Prioritaet gewinnt ueber die der KI und sortiert."""
    for it in ergebnis.get("items") or []:
        it["priority"] = preisbasis.prioritaet(it.get("category") or "other",
                                               betrag=it.get("recommended_discount_eur"),
                                               kaufpreis=kaufpreis,
                                               manuell=bool(it.get("manual_review_required")))
    ergebnis["items"] = sorted(ergebnis.get("items") or [],
                               key=lambda i: (preisbasis.prioritaet_reihenfolge(i["priority"]),
                                              -float(i.get("recommended_discount_eur") or 0)))


def bewertung_anstossen(protocol_id: str, dealer_id: str) -> None:
    """Feuer-und-vergiss aus dem Request heraus (nach dem Abschicken). Ein
    Fehler hier darf den Aufrufer nie erreichen."""
    try:
        if not ki_aktiv():
            return
        loop = asyncio.get_running_loop()
        aufgabe = loop.create_task(bewertung_ausfuehren(protocol_id, dealer_id))
        _laufende.add(aufgabe)
        aufgabe.add_done_callback(_laufende.discard)
    except Exception:  # noqa: BLE001
        log.exception("KI-Bewertung fuer %s nicht gestartet", protocol_id)


_laufende: set = set()


# ------------------------------------------------ Lesen (Chef)
async def bewertung_lesen(protocol_id: str, dealer_id: str, *, nachrechnen: bool = True) -> Optional[dict]:
    """Aktuelle Bewertung zum Protokoll. Passt der Hash nicht mehr zum
    heutigen Stand (Fahrer hat nachgetragen), gilt sie als "veraltet" und
    wird — wenn nachrechnen — im Hintergrund neu gerechnet."""
    grund = await _grundlagen(protocol_id, dealer_id)
    if not grund:
        return None
    doc, appt, vehicle, contract = grund
    paket = paket_bauen(doc, appt, vehicle, contract)
    h = eingabe_hash(paket)
    passend = await db[SAMMLUNG].find_one({"protocol_id": protocol_id, "input_hash": h}, {"_id": 0})
    if passend:
        return _oeffentlich(passend)
    if not relevant(paket):
        return {"status": "keine", "grund": "keine preisrelevanten Abweichungen", "protocol_id": protocol_id,
                "input_hash": h, "ergebnis": None}
    if not ki_aktiv():
        return {"status": "aus", "grund": "KI-Bewertung nicht aktiv", "protocol_id": protocol_id,
                "input_hash": h, "ergebnis": None}
    if nachrechnen:
        bewertung_anstossen(protocol_id, dealer_id)
    letzte = await db[SAMMLUNG].find_one({"protocol_id": protocol_id}, {"_id": 0}, sort=[("created_at", -1)])
    if letzte:
        alt = _oeffentlich(letzte)
        alt["status"] = "veraltet"
        alt["grund"] = "Bewertung veraltet – wird neu berechnet"
        return alt
    return {"status": "laeuft", "grund": "", "protocol_id": protocol_id, "input_hash": h, "ergebnis": None}


async def zusammenfassungen(protocol_ids: List[str], dealer_id: str) -> Dict[str, dict]:
    """Kurzform fuer die Freigabe-Liste (ohne Neurechnung): je Protokoll die
    juengste Bewertung mit Status und Gesamtwerten."""
    raus: Dict[str, dict] = {}
    if not protocol_ids:
        return raus
    async for d in db[SAMMLUNG].find({"protocol_id": {"$in": protocol_ids}, "dealer_id": dealer_id},
                                     {"_id": 0, "protocol_id": 1, "status": 1, "created_at": 1,
                                      "ergebnis.combined": 1, "input_hash": 1}).sort("created_at", -1):
        pid = d["protocol_id"]
        if pid in raus:
            continue
        comb = ((d.get("ergebnis") or {}).get("combined") or {})
        raus[pid] = {"status": d.get("status"), "input_hash": d.get("input_hash"),
                     "empfohlener_nachlass": comb.get("recommended_discount_eur"),
                     "empfohlener_preis": comb.get("recommended_purchase_price_eur"),
                     "sicherheit": comb.get("confidence"),
                     "manuell": comb.get("manual_review_required")}
    return raus


# ------------------------------------------------ Lernen aus echten Faellen
async def lernfall_speichern(protocol_id: str, dealer_id: str, *, chef_preis: Optional[float],
                             quelle: str) -> None:
    """Anonymisiert festhalten, was die KI empfahl und was der Chef daraus
    machte — die Grundlage fuer die eigene Preisdatenbank. Keine
    Verkaeuferdaten. Wirft nie."""
    try:
        bew = await db[SAMMLUNG].find_one({"protocol_id": protocol_id, "dealer_id": dealer_id, "status": "ok"},
                                          {"_id": 0}, sort=[("created_at", -1)])
        if not bew:
            return
        eingabe = bew.get("eingabe") or {}
        comb = (bew.get("ergebnis") or {}).get("combined") or {}
        kp = bew.get("kaufpreis")
        await db[LERN_SAMMLUNG].update_one(
            {"protocol_id": protocol_id, "input_hash": bew.get("input_hash")},
            {"$set": {
                "art": "abholung",
                "dealer_id": dealer_id, "protocol_id": protocol_id, "input_hash": bew.get("input_hash"),
                "created_at": now_iso(), "modell": bew.get("modell"), "prompt_version": bew.get("prompt_version"),
                "fahrzeug": eingabe.get("vehicle"), "abweichungen": eingabe.get("deviations"),
                "neue_schaeden": eingabe.get("new_damages"),
                "vertragspreis": kp,
                "ki_nachlass": comb.get("recommended_discount_eur"),
                "ki_bereich": [comb.get("discount_min_eur"), comb.get("discount_max_eur")],
                "fahrer_vorschlag": (eingabe.get("prices") or {}).get("driver_proposal_eur"),
                "chef_preis": chef_preis, "quelle": quelle,
                "chef_nachlass": (round(float(kp) - float(chef_preis), 2)
                                  if kp is not None and chef_preis is not None else None),
                # Stufe 4: einheitlicher Name fuer die Kalibrierung (Vertrag: Inserat - Kaufpreis)
                "tatsaechlicher_nachlass": (round(float(kp) - float(chef_preis), 2)
                                            if kp is not None and chef_preis is not None else None),
                "items": [{k: i.get(k) for k in ("source_id", "category", "title", "recommended_discount_eur",
                                                  "discount_min_eur", "discount_max_eur", "confidence")}
                          for i in (bew.get("ergebnis") or {}).get("items") or []],
            }}, upsert=True)
        kalibrierung.zuruecksetzen()
    except Exception:  # noqa: BLE001
        log.exception("Lernfall fuer %s nicht gespeichert", protocol_id)
