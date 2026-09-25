# -*- coding: utf-8 -*-
"""KI-Abholbewertung (Wunsch Ahmad 25.09.2026): Was kostet, was der Fahrer vor
Ort anders vorfindet als im Vertrag/Inserat steht?

Ablauf: Fahrer schickt das Protokoll "zur Freigabe" -> das Backend baut aus
Vertrag, Inserat und Fahrereingaben ein KLEINES, normalisiertes Paket (ohne
Verkaeuferdaten) samt Kontext (Reparaturreferenzen aus eigener Datenbank /
Markttabelle / Startwerten, Marktvergleich aus eigenen Daten, Historie),
sucht — solange eigene Daten nicht reichen — gezielt im Netz nach, fragt die
KI EINMAL fuer alle Abweichungen, prueft die Antwort (schemas.bereinigen) und
legt sie in ki_bewertungen ab — gebunden an die Protokollversion (revision)
und einen Hash der Eingabe. Aendert sich die Eingabe, gilt die alte Bewertung
als "veraltet" und wird neu gerechnet.

Umbau 26.09.2026 (Wunsch Ahmad): keine Rueckfragen der KI mehr — alle
Angaben kommen aus dem Formular ("unbekannt" ist ein gueltiger Wert, die
Referenz nimmt dann die vorsichtige Annahme); vier Geldwerte statt Spanne;
bekannte Schaeden werden nach Art + Bauteil + Auspraegung abgeglichen, eine
Verschlechterung wird als damage_worse bewertet; Ausstattung fehlt/defekt
getrennt; Unterlagen nur, wenn vereinbart; Kostenbremse (ai.budget: Monat
und Lauf); jede Websuche fuettert die eigene Preisdatenbank; ein
haengender Lauf verfaellt nach LEASE_S Sekunden.

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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import protokoll_vergleich as PV
from deps import db, now_iso

from ai import budget, freischaltung, kalibrierung, kontext, marktdaten, preisbasis, schemas
from ai.provider import json_bewerten, ki_aktiv, ki_modell

log = logging.getLogger("autohandel.ki")

SAMMLUNG = "ki_bewertungen"
LERN_SAMMLUNG = "ki_lernfaelle"
KM_TOLERANZ = PV.KM_TOLERANZ          # unter dieser Differenz zaehlt km nicht
LEASE_S = 150                         # "laeuft" ohne Ergebnis gilt danach als abgestuerzt

# Abschnitt-4-Werte des Fahrers, die als technische Auffaelligkeit gelten
_ZUSTAND_AUFFAELLIG = {
    "driving": ({"Mängel", "Maengel"}, "technical", "Fahrverhalten mit Mängeln (Probefahrt)"),
    "battery": ({"schwach", "defekt"}, "technical", "Batterie/Starter"),
    "warning_lights": ({"ja"}, "warning_light", "Kontrollleuchten leuchten"),
}

SYSTEM_PROMPT = """Du bist der Bewertungsdienst von AutoSchnell, einer Software fuer Autohaendler in Deutschland.
Ein Fahrer holt ein gekauftes Gebrauchtfahrzeug beim Verkaeufer ab und stellt Abweichungen zum Kaufvertrag/Inserat fest. Du bewertest NUR den wirtschaftlichen Einfluss dieser Abweichungen in Euro, damit der Firmenchef mit dem Verkaeufer nachverhandeln kann. Das Backend hat den Fall bereits vollstaendig vorbereitet: Du ermittelst nichts, du bewertest.

Regeln:
1. Bewerte NUR new_damages (ohne already_known) und deviations. Jede Position bekommt genau einen Eintrag mit source_id aus der Eingabe. Bekannte Schaeden (already_known=true) nie bewerten; possibly_known=true heisst: koennte im Vertrag stehen — Nachlass etwas vorsichtiger, nicht streichen. damage_worse: bewerte NUR die Verschlechterung gegenueber expected.
2. Verwende ausschliesslich die gelieferten Daten: repair_reference (Reparaturweg, low/median/high in EUR, Quelle), market (Vergleichspreise), history (eigene Faelle), prices, die Marktrecherche unten. Allgemeines Fachwissen dient der Einordnung; erfinde keine konkreten aktuellen Markt-, Ersatzteil- oder Werkstattpreise. Fehlt eine Referenz, nutze die Ausgangswerte unten und sage das in reason. reason und title sind fuer Haendler und Fahrer: verstaendliches Deutsch, keine technischen Feldnamen oder Codes (nicht possibly_known, damage_worse, equipment_missing, manual_hint).
3. assumption_made=true bei einer Referenz heisst: eine Angabe war "unbekannt", die Referenz nimmt die vorsichtige Auspraegung — uebernimm das und nenne die Annahme in reason. Stelle keine Rueckfragen.
4. Vier Geldwerte je Position und insgesamt: minimum_justified_eur (darunter ist der Nachteil nicht ausgeglichen), fair_discount_eur (sachlich am besten begruendbarer Zielwert, meist nahe median der Referenz plus Aufwand/Wertminderung), best_realistic_eur (sehr gutes, noch vertretbares Ergebnis), negotiation_start_eur (erste Forderung, ueber best, nicht absurd). Immer min <= fair <= best <= start.
5. Nachlass = das, was der Haendler wegen dieser Abweichung weniger zahlen sollte (Reparatur + Aufwand + Wertminderung). Beruecksichtige Fahrzeugwert, Alter, Kilometer, Klasse, Marke und die Marktposition (liegt der Vertragspreis schon unter dem Median, ist der Spielraum kleiner). Bei einem alten, guenstigen Fahrzeug ist voller Reparaturkostenersatz nicht automatisch der faire Nachlass.
6. assessment_kind je Position: repair_estimate = Schaden sichtbar, Reparaturweg klar (Normalfall). diagnosis_required = nur ein Symptom, keine bestaetigte Ursache: Warnleuchte, Geraeusch, Ruckeln, Oelspur, Klima kuehlt nicht, Technik-Mangel (type technik) mit status "nur Symptom" oder "unbekannt", Rost unter dem Lack mit unklarem Umfang; dann diagnosis_cost_eur (Werkstattdiagnose, aus repair_reference.diagnosis) und drei Szenarien scenario_low/mid/high_eur (guenstiger, mittlerer, aufwendiger Reparaturfall aus repair_reference.scenarios und Recherche) — die vier Geldwerte folgen daraus (min = Diagnose, fair = guenstig, best = mittel, start = aufwendig); in reason ausdruecklich als Szenario benennen, nie als festgestellten Schaden. Technik-Mangel mit status "Werkstatt hat Diagnose bestaetigt" = repair_estimate mit der Referenz. expert_check_required (manual_review_required=true, alle Betraege 0) bei: Unfallfreiheit weicht ab, Durchrostung oder tragende Teile, Fahrzeug nicht fahrbereit, Zulassungsbescheinigung Teil II fehlt, Kilometerstand niedriger als im Vertrag (manual_hint), Hochvolt-Fehler. Kategorie fuer Technik-Maengel ist technical. Stellen die Maengel den Kauf wirtschaftlich in Frage, setze deal_risk=reconsider_purchase; bei hohem Preisrisiko high.
7. equipment_missing = fehlt komplett (Nachruestung oder Wertminderung); equipment_defect = vorhanden, defekt (Reparatur). documents mit agreed=false sind organisatorisch: Nachlass 0, price_relevant=false. mileage/previous_owners: der Betrag steht in difference; keine pauschale Cent-je-km-Regel, sondern Fahrzeugwert und Alter.
8. combined: sum_fair_eur, Abzug fuer ueberlappende Arbeiten (zwei Schaeden am selben Bauteil = eine Lackierung), dann die vier Gesamtwerte und deal_risk. arguments: hoechstens 3 kurze sachliche Saetze fuer das Gespraech mit dem Verkaeufer (deutsch, neutral).
9. Knapp: title hoechstens 8 Woerter, reason hoechstens 14 Woerter, repair_method hoechstens 6 Woerter. Antworte ausschliesslich nach dem JSON-Schema, alle Texte auf Deutsch, Preise in Euro inkl. MwSt.

""" + preisbasis.basis_als_text()


# ------------------------------------------------ Hilfen
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


def _zone_norm(zone: str) -> str:
    return re.sub(r"[^a-zäöüß0-9 ]+", " ", str(zone or "").lower()).strip()


def _schaden_kurz(d: dict) -> dict:
    """Ein Schaden aus der Skizze, ohne Anzeige-Koordinaten."""
    sd = d.get("severity_data") if isinstance(d.get("severity_data"), dict) else {}
    return {"id": str(d.get("id") or ""), "type": d.get("type_key") or d.get("type") or "",
            "label": d.get("type_label") or d.get("label") or "",
            "zone": d.get("zone") or d.get("part_label") or d.get("part") or "",
            "view": d.get("view") or "", "note": (d.get("note") or d.get("text") or "")[:200],
            "severity_data": {str(k)[:40]: str(v)[:60] for k, v in sd.items()}}


# Ordnung der Auspraegungen fuer den Abgleich "schlimmer geworden?"
_STUFEN = {
    "groesse": ["bis 2 cm", "2–5 cm", "bis 5 cm", "5–10 cm", "5–15 cm", "über 10 cm", "über 15 cm"],
    "laenge": ["bis 5 cm", "5–15 cm", "15–30 cm", "über 30 cm"],
    "lack": ["nein", "ja"],
    "tiefe": ["oberflächlich", "bis Grundierung", "tief", "bis Blech"],
    "umfang": ["oberflächlich", "einzeln", "wenige", "Blasen", "mehrere", "viele", "sehr viele", "durchgerostet",
               "ganzes Fahrzeug"],
    "funktion": ["eingeschränkt", "komplett ausgefallen", "Gehäuse beschädigt"],
}


def _stufe(key: str, wert: Any) -> Optional[int]:
    w = str(wert or "").strip().lower()
    if not w or w == "unbekannt":
        return None
    for i, s in enumerate(_STUFEN.get(key, [])):
        if s.lower() in w or w in s.lower():
            return i
    return None


def _abgleich(bekannt: List[dict], neu: dict) -> Tuple[str, Optional[dict]]:
    """('neu' | 'bekannt' | 'schlimmer' | 'moeglich', bekannter Schaden).
    bekannt = gleiche Art + gleiches Bauteil und nicht schlimmer;
    schlimmer = gleiche Art + Bauteil, mindestens eine Auspraegung hoeher;
    moeglich = gleiche Art, Bauteil teilt sich ein Wort (z. B. 'Kotflügel')."""
    typ = str(neu.get("type_key") or neu.get("type") or "").lower()
    zone = _zone_norm(neu.get("zone") or neu.get("part_label") or "")
    woerter = set(zone.split())
    kandidat_moeglich = None
    for b in bekannt:
        if str(b.get("type_key") or b.get("type") or "").lower() != typ:
            continue
        bz = _zone_norm(b.get("zone") or b.get("part_label") or "")
        if bz and bz == zone:
            alt_sd = b.get("severity_data") if isinstance(b.get("severity_data"), dict) else {}
            neu_sd = neu.get("severity_data") if isinstance(neu.get("severity_data"), dict) else {}
            schlimmer = False
            for k in set(alt_sd) | set(neu_sd):
                a, n = _stufe(k, alt_sd.get(k)), _stufe(k, neu_sd.get(k))
                if a is not None and n is not None and n > a:
                    schlimmer = True
            return ("schlimmer" if schlimmer else "bekannt"), b
        if bz and woerter and (set(bz.split()) & woerter):
            kandidat_moeglich = b
    return ("moeglich" if kandidat_moeglich else "neu"), kandidat_moeglich


# ------------------------------------------------ Paket bauen
def _unterlage_vereinbart(name: str, contract: dict, vehicle: dict) -> bool:
    """Unterlagen zaehlen nur als Abweichung, wenn sie vereinbart/zugesichert
    waren (Umbau 26.09.2026): Zulassung I/II immer; HU-Bericht bei HU=Ja;
    Servicebuch bei Scheckheft ja/teilweise; Zweitsatz bei 8-fach; COC nur,
    wenn das Inserat es nennt; Bedienungsanleitung/Zubehoer nie."""
    n = name.lower()
    text = " ".join(str(x) for x in (vehicle.get("description"), *(vehicle.get("features") or [])) if x).lower()
    if "zulassung" in n or "fahrzeugbrief" in n or "fahrzeugschein" in n:
        return True
    if "hu" in n or "au-bericht" in n:
        return str(contract.get("hu_valid") or "").strip().lower() == "ja"
    if "service" in n or "scheckheft" in n:
        return str(contract.get("service_book") or "").strip().lower() in ("ja", "teilweise")
    if "zweitsatz" in n or "reifen" in n:
        return str(contract.get("tires") or "") == "8-fach" or "winterreifen" in text or "zweitsatz" in text
    if "coc" in n:
        return "coc" in text
    if "ladekabel" in n:
        return "elektro" in str(vehicle.get("fuel_label") or vehicle.get("fuel") or "").lower()
    return False


def paket_bauen(protokoll: dict, appt: dict, vehicle: dict, contract: dict,
                marktdoc: Optional[dict] = None) -> Dict[str, Any]:
    """Normalisiertes Eingabepaket fuer die KI — nur, was fuer die Bewertung
    zaehlt. Keine Verkaeuferdaten (Name, Telefon, Adresse, Ausweis)."""
    from routes.protocols import vertragspreis_vor_abholung  # spaet: Kreisimport
    werte = PV.vertragswerte(vehicle, contract)
    zeilen = PV.vergleich(PV.FELDER, protokoll.get("vehicle_check") or {},
                          protokoll.get("condition") or {}, werte)
    kaufpreis = _zahl(vertragspreis_vor_abholung(contract))
    ez = werte.get("first_registration", {}).get("text") or ""
    kw = werte.get("power", {}).get("wert")
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
    bekannt_roh = [d for d in (contract.get("damages") or vehicle.get("damages") or []) if isinstance(d, dict)]
    bekannt = [_schaden_kurz(d) for d in bekannt_roh]
    bekannte_maengel = [str(m)[:200] for m in (vehicle.get("known_defects") or []) if str(m or "").strip()][:20]
    neu: List[dict] = []
    abweichungen: List[Dict[str, Any]] = []
    for d in protokoll.get("new_damages") or []:
        if not isinstance(d, dict):
            continue
        k = _schaden_kurz(d)
        status, alt = _abgleich(bekannt_roh, d)
        k["already_known"] = status == "bekannt"
        k["possibly_known"] = status == "moeglich"
        if status == "schlimmer" and alt is not None:
            abweichungen.append({"id": f"worse:{k['id']}", "type": "damage_worse", "field": "damage",
                                 "label": f"{k['label']} {k['zone']} schlimmer als im Vertrag".strip(),
                                 "expected": (alt.get("severity_data") or {}),
                                 "actual": k["severity_data"], "zone": k["zone"], "damage_type": k["type"],
                                 "severity_data": k["severity_data"],
                                 "repair_reference": kontext.reparaturreferenz(d, marktdoc)})
            k["already_known"] = True
            k["worse"] = True
        elif not k["already_known"]:
            k["repair_reference"] = kontext.reparaturreferenz(d, marktdoc)
        neu.append(k)

    for z in PV.abweichungen(zeilen):
        if not z.get("abweichend"):
            continue
        s = z.get("schluessel")
        soll, ist = z.get("vertrag_text") or "", z.get("vor_ort_text") or ""
        eintrag: Dict[str, Any] = {"id": f"dev:{s}", "field": s, "label": z.get("feld"), "expected": soll, "actual": ist}
        if s == "mileage_contract":
            a, b = PV.zahl(soll), PV.zahl(ist)
            if a is not None and b is not None:
                diff = b - a
                if diff <= -KM_TOLERANZ:
                    # Umbau 26.09.2026: deutlich weniger km als im Vertrag ist kein
                    # Nachlass, aber ein Tacho-/Datenproblem -> manuelle Pruefung.
                    eintrag.update(type="other", difference_km=diff, manual_hint=True,
                                   label="Kilometerstand niedriger als im Vertrag (prüfen)")
                    abweichungen.append(eintrag)
                    continue
                if diff < KM_TOLERANZ:
                    continue
                eintrag.update(type="mileage", difference_km=diff,
                               difference_percent=round(diff / a * 100, 2) if a else None,
                               repair_reference=kontext.abweichungsreferenz("mileage", betrag=diff))
            else:
                eintrag["type"] = "mileage"
        elif s == "previous_owners":
            a, b = PV.zahl(soll), PV.zahl(ist)
            if a is not None and b is not None and b <= a:
                continue
            diff = (b - a) if (a is not None and b is not None) else None
            eintrag.update(type="previous_owners", difference=diff,
                           repair_reference=kontext.abweichungsreferenz("previous_owners", betrag=diff))
        elif s == "accident_free":
            eintrag.update(type="accident_history", repair_reference=kontext.abweichungsreferenz("accident_history"))
        elif s == "hu":
            eintrag.update(type="hu", repair_reference=kontext.abweichungsreferenz("hu"))
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
                             "missing": soll_schl - ist_schl,
                             "repair_reference": kontext.abweichungsreferenz("keys")})
    # Ausstattung (Abschnitt 3): False/"fehlt" = fehlt, "defekt" = vorhanden aber
    # defekt, "anders" = anders als beschrieben (Umbau 26.09.2026 getrennt)
    for name, wert in (protokoll.get("features") or {}).items():
        w = str(wert).strip().lower() if isinstance(wert, str) else wert
        if w is False or w == "fehlt":
            abweichungen.append({"id": f"dev:feature:{name[:60]}", "type": "equipment_missing",
                                 "field": "features", "label": "Ausstattung", "equipment": name[:80],
                                 "expected": "vorhanden (laut Inserat)", "actual": "fehlt komplett",
                                 "repair_reference": kontext.abweichungsreferenz("equipment_missing")})
        elif w == "defekt":
            abweichungen.append({"id": f"dev:feature:{name[:60]}", "type": "equipment_defect",
                                 "field": "features", "label": "Ausstattung", "equipment": name[:80],
                                 "expected": "funktionsfähig", "actual": "vorhanden, funktioniert nicht",
                                 "repair_reference": kontext.abweichungsreferenz("equipment_defect")})
        elif w == "anders":
            abweichungen.append({"id": f"dev:feature:{name[:60]}", "type": "equipment_defect",
                                 "field": "features", "label": "Ausstattung", "equipment": name[:80],
                                 "expected": "wie im Inserat beschrieben", "actual": "anders als beschrieben",
                                 "note": "nur bewerten, wenn wirtschaftlich ein Unterschied besteht"})
    # Dokumente (Abschnitt 2): false = fehlt — mit Merker, ob vereinbart
    for name, ok in (protokoll.get("documents") or {}).items():
        if ok is False:
            vereinbart = _unterlage_vereinbart(name, contract, vehicle)
            n = name.lower()
            key = "doc_brief" if "teil ii" in n or "fahrzeugbrief" in n else \
                  ("doc_service" if vereinbart and ("service" in n or "hu" in n or "coc" in n or "reifen" in n)
                   else "doc_organisatorisch")
            z = preisbasis.zeile(key) or {}
            abweichungen.append({"id": f"dev:doc:{name[:60]}", "type": "documents",
                                 "field": "documents", "label": "Unterlagen", "document": name[:80],
                                 "expected": "vorhanden" if vereinbart else "nicht vereinbart",
                                 "actual": "fehlt", "agreed": vereinbart,
                                 "repair_reference": {"key": key, "method": z.get("verfahren", ""), "low": z.get("min", 0),
                                                      "median": round((z.get("min", 0) + z.get("max", 0)) / 2),
                                                      "high": z.get("max", 0), "manual_review": bool(z.get("manuell")),
                                                      "source": "AutoSchnell-Startwerte", "assumption_made": False}
                                 if z else None})
    # Zustand (Abschnitt 4)
    zustand = protokoll.get("condition") or {}
    for feld, (werte_auffaellig, typ, label) in _ZUSTAND_AUFFAELLIG.items():
        w = str(zustand.get(feld) or "").strip()
        if w in werte_auffaellig:
            abweichungen.append({"id": f"dev:{feld}", "type": typ, "field": feld,
                                 "label": label, "expected": "ohne Befund", "actual": w,
                                 "repair_reference": kontext.abweichungsreferenz(typ)})
    reifen = str(zustand.get("tire_profile") or "").strip()
    if reifen and re.search(r"\b(schlecht|abgefahren|runter|mangel|risse|platt|[0-2](?:[,.]\d)?\s*mm)\b", reifen.lower()):
        abweichungen.append({"id": "dev:tires", "type": "tires", "field": "tire_profile",
                             "label": "Reifen", "expected": "fahrbereit laut Inserat",
                             "actual": reifen[:120], "repair_reference": kontext.abweichungsreferenz("tires")})
    antworten = []
    for a in (protokoll.get("rueckfrage_antworten") or [])[:10]:
        if isinstance(a, dict) and str(a.get("answer") or "").strip():
            antworten.append({"source_id": str(a.get("source_id") or "")[:200],
                              "question": str(a.get("question") or "")[:300],
                              "answer": str(a.get("answer") or "")[:100]})
    paket = {
        "vehicle": fahrzeug,
        "prices": preise,
        "known_damages": bekannt,
        "known_defects_listing": bekannte_maengel,
        "new_damages": neu,
        "deviations": abweichungen,
        "driver_answers": antworten,
        "driver_notes": (protokoll.get("notes") or "")[:500],
    }
    paket["precomputed"] = kontext.vorberechnet(paket)
    return paket


def relevant(paket: Dict[str, Any]) -> bool:
    """Gibt es ueberhaupt etwas zu bewerten? Sonst kein KI-Aufruf."""
    neue = [d for d in paket.get("new_damages") or [] if not d.get("already_known")]
    return bool(neue or paket.get("deviations"))


def eingabe_hash(paket: Dict[str, Any]) -> str:
    """Hash ueber die Eingabe OHNE Kontext (Markt, Historie, Referenzquelle)
    — der wechselt taeglich und soll keine Neuberechnung ausloesen."""
    kern = {k: v for k, v in paket.items() if k not in ("market", "history", "precomputed")}

    def _ohne_ref(d):
        if isinstance(d, dict):
            return {k: _ohne_ref(v) for k, v in d.items() if k != "repair_reference"}
        if isinstance(d, list):
            return [_ohne_ref(x) for x in d]
        return d
    roh = json.dumps(_ohne_ref(kern), ensure_ascii=False, sort_keys=True, default=str)
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
        "kaufpreis": doc.get("kaufpreis"), "kosten_ct": doc.get("kosten_ct"),
        "budget": doc.get("budget"),
    }


def fuer_fahrer(erg: dict, *, preis_vorschlag=None) -> dict:
    """Wunsch Ahmad 25.09.2026 (abends): Der Fahrer sieht nach dem Abschicken
    dieselbe Auswertung wie der Chef — aber ohne Kosten, Budget und Modell
    (Betriebsdaten der Firma). Dazu sein eigener Preisvorschlag."""
    raus = {k: v for k, v in erg.items()
            if k not in ("kosten_ct", "budget", "modell", "prompt_version", "dauer_ms")}
    raus["preis_vorschlag"] = preis_vorschlag
    return raus


def _lease_abgelaufen(doc: Optional[dict]) -> bool:
    if not doc or doc.get("status") != "laeuft":
        return False
    bis = doc.get("lease_until")
    if not bis:
        return True
    try:
        t = datetime.fromisoformat(str(bis).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > t
    except ValueError:
        return True


async def _kosten_pruefen(usage: Dict[str, Any], modell: str, ref: str, art: str,
                          fall: Optional[dict] = None) -> float:
    """Kosten in Cent schaetzen (Bewertung mit KI_MODELL, Recherche mit dem
    Recherche-Modell); ueber KI_KOSTEN_MAX_CT -> Betriebsalarm."""
    ct = round(kalibrierung._kosten_usd(modell, usage or {}) * 100, 2)
    if fall:
        ct = round(ct + kalibrierung._kosten_usd(fall.get("modell") or "", fall.get("usage") or {}) * 100, 2)
    if ct > budget.kosten_max_ct():
        try:
            import betrieb
            await betrieb.alarm(db, "ki_kosten_ueberschritten", ref=ref, art=art, kosten_ct=ct,
                                grenze_ct=budget.kosten_max_ct())
        except Exception:  # noqa: BLE001
            pass
    return ct


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
        res = None                       # Budget-Reservierung (Review 25.09.2026)
        ktx = await kontext.sammeln(vehicle, "abholung", eigene_id=str(appt.get("vehicle_id") or ""))
        paket = paket_bauen(doc, appt, vehicle, contract, ktx.get("marktdoc"))
        eigene = await marktdaten.eigene_referenzen(paket, "abholung")
        kontext.eigene_anwenden(paket, eigene)
        h = eingabe_hash(paket)
        jetzt = now_iso()
        basis = {"id": str(uuid.uuid4()), "art": "abholung", "dealer_id": dealer_id, "protocol_id": protocol_id,
                 "appointment_id": doc.get("appointment_id"), "protocol_revision": doc.get("revision"),
                 "input_hash": h, "prompt_version": schemas.PROMPT_VERSION, "modell": ki_modell(),
                 "created_at": jetzt, "kaufpreis": paket["prices"].get("contract_price_eur")}
        vorhanden = await db[SAMMLUNG].find_one({"protocol_id": protocol_id, "input_hash": h}, {"_id": 0})
        if vorhanden and not erzwingen and (vorhanden.get("status") in ("ok", "keine")
                                            or (vorhanden.get("status") == "laeuft" and not _lease_abgelaufen(vorhanden))):
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
        # Wunsch Ahmad 25.09.2026 abends: KI je Konto freigeschaltet (wie Abo) —
        # bei der Abholung zaehlt das Hauptchef-Konto der Firma. Nicht ablegen:
        # schaltet der Betreiber frei, rechnet der naechste Aufruf sofort.
        if not await freischaltung.firma_freigeschaltet(dealer_id):
            return _oeffentlich({**basis, **freischaltung.gesperrt(), "dauer_ms": 0})
        # Kostenbremse (Wunsch Ahmad 26.09.2026): Monatsbudget je Firma, Sparmodus
        bud = await budget.pruefen(user_id=None, dealer_id=dealer_id, art="abholung")
        if not bud["erlaubt"]:
            eintrag = {**basis, "status": "budget", "grund": bud["grund"], "ergebnis": None, "dauer_ms": 0,
                       "budget": bud}
            await db[SAMMLUNG].update_one({"protocol_id": protocol_id, "input_hash": h},
                                          {"$set": eintrag}, upsert=True)
            return _oeffentlich(eintrag)
        # Merker "laeuft" mit Lease — ein zweiter Aufruf wartet nicht doppelt,
        # ein abgestuerzter Lauf verfaellt nach LEASE_S Sekunden.
        lease = (datetime.now(timezone.utc) + timedelta(seconds=LEASE_S)).isoformat()
        await db[SAMMLUNG].update_one({"protocol_id": protocol_id, "input_hash": h},
                                      {"$set": {**basis, "status": "laeuft", "grund": "", "ergebnis": None,
                                                "lease_until": lease}},
                                      upsert=True)
        # Budget atomar reservieren — erst wenn dieser Aufruf den Lauf wirklich haelt
        res = await budget.reservieren(user_id=None, dealer_id=dealer_id, art="abholung")
        if res is None:
            eintrag = {**basis, "status": "budget", "grund": bud["grund"] or "Monatsbudget für KI-Bewertungen aufgebraucht.",
                       "ergebnis": None, "dauer_ms": 0, "budget": bud}
            await db[SAMMLUNG].update_one({"protocol_id": protocol_id, "input_hash": h},
                                          {"$set": eintrag, "$unset": {"lease_until": ""}})
            return _oeffentlich(eintrag)
        paket["market"] = kontext.marktposition(ktx.get("markt"), listing=paket["prices"].get("listing_price_eur"),
                                                agreed=paket["prices"].get("contract_price_eur"))
        paket["history"] = ktx.get("historie")
        fall = await marktdaten.fall_recherche("abholung", paket, sparmodus=bud["sparmodus"], eigene=eigene)
        gelernt = await marktdaten.lernen_aus_recherche(fall, paket, "abholung")
        if gelernt:
            eigene = await marktdaten.eigene_referenzen(paket, "abholung")
            kontext.eigene_anwenden(paket, eigene)
        lage = kontext.datenlage(paket)
        zusatz = "\n\n".join(t for t in (marktdaten.als_text(ktx.get("marktdoc")),
                                          await kalibrierung.prompt_zusatz(dealer_id),
                                          marktdaten.fall_als_text(fall)) if t)
        antwort = await json_bewerten(system=SYSTEM_PROMPT, nutzer=paket, schema=schemas.ANTWORT_SCHEMA,
                                      zusatz=zusatz or None)
        kosten = await _kosten_pruefen(antwort.get("usage") or {}, antwort.get("modell") or basis["modell"],
                                       protocol_id, "abholung", fall)
        await budget.abrechnen(res, kosten)
        res = None
        usage = dict(antwort.get("usage") or {})
        if fall:
            for k, v in (fall.get("usage") or {}).items():
                usage[k] = int(usage.get(k) or 0) + int(v or 0)
        eintrag = {**basis, "dauer_ms": int(antwort.get("dauer_ms") or 0) + int((fall or {}).get("dauer_ms") or 0),
                   "usage": usage, "kosten_ct": kosten, "modell": antwort.get("modell") or basis["modell"],
                   "datenlage": lage, "budget": {k: bud.get(k) for k in ("verbraucht_ct", "grenze_ct", "sparmodus")},
                   "recherche": ({"status": fall.get("status"), "suchen": fall.get("suchen"),
                                  "quellen": fall.get("quellen"), "text": fall.get("text"),
                                  "gelernt": gelernt} if fall else None)}
        if antwort.get("status") == "ok" and isinstance(antwort.get("daten"), dict):
            ergebnis = schemas.bereinigen(antwort["daten"], kaufpreis=basis["kaufpreis"])
            _prioritaeten_nachziehen(ergebnis, basis["kaufpreis"])
            ergebnis["datenlage"] = schemas.datenlage_anpassen(ergebnis, lage)
            ergebnis["market"] = paket.get("market")
            ergebnis["quellen"] = list((fall or {}).get("quellen") or [])
            ergebnis["referenzen"] = {p["id"]: p.get("repair_reference") for p in
                                      [d for d in paket["new_damages"] if not d.get("already_known")] + paket["deviations"]
                                      if p.get("repair_reference")}
            eintrag.update(status="ok", grund="", ergebnis=ergebnis, roh=antwort["daten"], eingabe=paket)
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
                                      {"$set": eintrag, "$unset": {"lease_until": ""}}, upsert=True)
        return _oeffentlich(eintrag)
    except Exception:  # noqa: BLE001 — die KI ist Beiwerk, nie ein 500
        log.exception("KI-Bewertung %s gescheitert", protocol_id)
        try:
            await budget.abrechnen(res, 0)       # Reservierung freigeben
        except Exception:  # noqa: BLE001
            pass
        return None


def _prioritaeten_nachziehen(ergebnis: dict, kaufpreis: Optional[float]) -> None:
    """Deterministische Prioritaet gewinnt ueber die der KI und sortiert."""
    for it in ergebnis.get("items") or []:
        it["priority"] = preisbasis.prioritaet(it.get("category") or "other",
                                               betrag=it.get("fair_discount_eur"),
                                               kaufpreis=kaufpreis,
                                               manuell=bool(it.get("manual_review_required")))
    ergebnis["items"] = sorted(ergebnis.get("items") or [],
                               key=lambda i: (preisbasis.prioritaet_reihenfolge(i["priority"]),
                                              -float(i.get("fair_discount_eur") or 0)))


def bewertung_anstossen(protocol_id: str, dealer_id: str) -> None:
    """Feuer-und-vergiss aus dem Request heraus (nach dem Abschicken). Ein
    Fehler hier darf den Aufrufer nie erreichen."""
    try:
        if not ki_aktiv():
            return
        loop = asyncio.get_running_loop()
        # (Die Freischaltung prueft bewertung_ausfuehren selbst — hier kein
        # await moeglich, der Aufrufer ist eine laufende Anfrage.)
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
    wird — wenn nachrechnen — im Hintergrund neu gerechnet. Ein "laeuft"
    ohne Ergebnis nach LEASE_S Sekunden gilt als abgestuerzt und startet neu."""
    grund = await _grundlagen(protocol_id, dealer_id)
    if not grund:
        return None
    doc, appt, vehicle, contract = grund
    paket = paket_bauen(doc, appt, vehicle, contract, None)
    h = eingabe_hash(paket)
    passend = await db[SAMMLUNG].find_one({"protocol_id": protocol_id, "input_hash": h}, {"_id": 0})
    if passend and not _lease_abgelaufen(passend):
        return _oeffentlich(passend)
    if not relevant(paket):
        return {"status": "keine", "grund": "keine preisrelevanten Abweichungen", "protocol_id": protocol_id,
                "input_hash": h, "ergebnis": None}
    if not ki_aktiv():
        return {"status": "aus", "grund": "KI-Bewertung nicht aktiv", "protocol_id": protocol_id,
                "input_hash": h, "ergebnis": None}
    if not await freischaltung.firma_freigeschaltet(dealer_id):
        return freischaltung.gesperrt(protocol_id=protocol_id, input_hash=h)
    if nachrechnen:
        bewertung_anstossen(protocol_id, dealer_id)
    if passend:
        return {"status": "laeuft", "grund": "", "protocol_id": protocol_id, "input_hash": h, "ergebnis": None}
    letzte = await db[SAMMLUNG].find_one({"protocol_id": protocol_id, "status": "ok"}, {"_id": 0},
                                         sort=[("created_at", -1)])
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
                                      "ergebnis.combined": 1, "ergebnis.datenlage": 1, "input_hash": 1}).sort("created_at", -1):
        pid = d["protocol_id"]
        if pid in raus:
            continue
        comb = ((d.get("ergebnis") or {}).get("combined") or {})
        raus[pid] = {"status": d.get("status"), "input_hash": d.get("input_hash"),
                     "fairer_nachlass": comb.get("fair_discount_eur"),
                     "empfohlener_preis": comb.get("recommended_purchase_price_eur"),
                     "datenlage": (d.get("ergebnis") or {}).get("datenlage"),
                     "deal_risk": comb.get("deal_risk"),
                     "manuell": comb.get("manual_review_required")}
    return raus


# ------------------------------------------------ Lernen aus echten Faellen
async def lernfall_speichern(protocol_id: str, dealer_id: str, *, chef_preis: Optional[float],
                             quelle: str) -> None:
    """Anonymisiert festhalten, was die KI empfahl und was der Chef daraus
    machte — die Grundlage fuer die eigene Preisdatenbank. Bei der Abholung
    ist Vertragspreis minus neuer Preis genau der Nachlass wegen der
    festgestellten Maengel (sauberer Lernwert). Keine Verkaeuferdaten.
    Wirft nie."""
    try:
        bew = await db[SAMMLUNG].find_one({"protocol_id": protocol_id, "dealer_id": dealer_id, "status": "ok"},
                                          {"_id": 0}, sort=[("created_at", -1)])
        if not bew:
            return
        eingabe = bew.get("eingabe") or {}
        comb = (bew.get("ergebnis") or {}).get("combined") or {}
        kp = bew.get("kaufpreis")
        nachlass = (round(float(kp) - float(chef_preis), 2) if kp is not None and chef_preis is not None else None)
        if nachlass is not None and nachlass < 0:
            nachlass = None
        await db[LERN_SAMMLUNG].update_one(
            {"protocol_id": protocol_id, "input_hash": bew.get("input_hash")},
            {"$set": {
                "art": "abholung",
                "dealer_id": dealer_id, "protocol_id": protocol_id, "input_hash": bew.get("input_hash"),
                "created_at": now_iso(), "modell": bew.get("modell"), "prompt_version": bew.get("prompt_version"),
                "fahrzeug": eingabe.get("vehicle"), "abweichungen": eingabe.get("deviations"),
                "neue_schaeden": eingabe.get("new_damages"), "datenlage": bew.get("datenlage"),
                "vertragspreis": kp,
                "ki_nachlass": comb.get("fair_discount_eur"),
                "ki_bereich": [comb.get("minimum_justified_eur"), comb.get("best_realistic_eur")],
                "fahrer_vorschlag": (eingabe.get("prices") or {}).get("driver_proposal_eur"),
                "chef_preis": chef_preis, "quelle": quelle,
                "chef_nachlass": nachlass,
                "tatsaechlicher_nachlass": nachlass,
                "items": [{k: i.get(k) for k in ("source_id", "category", "title", "fair_discount_eur",
                                                  "minimum_justified_eur", "best_realistic_eur")}
                          for i in (bew.get("ergebnis") or {}).get("items") or []],
            }}, upsert=True)
        kalibrierung.zuruecksetzen()
    except Exception:  # noqa: BLE001
        log.exception("Lernfall fuer %s nicht gespeichert", protocol_id)
