# -*- coding: utf-8 -*-
"""KI-Schadennachlass beim Erstellen des Kaufvertrags (Wunsch Ahmad
25.09.2026, Stufe 3).

Der Sucher markiert Schaeden in der Skizze (mit den Zusatzangaben Groesse /
Lack / Laenge ...), bestaetigt "Das sind alle Schaeden" und bekommt mit EINEM
KI-Aufruf je Schaden Reparaturweg, Reparaturkosten, empfohlenen Nachlass mit
engem Bereich, Verhandlungseinstieg und Sicherheit — plus Gesamtempfehlung.
Grundlage ist der Inseratspreis (oder der schon verhandelte Kaufpreis).

Rein beratend: "als Kaufpreis uebernehmen" fuellt nur das Preisfeld. Der
Aufruf ist synchron (der Sucher wartet auf die Karte), begrenzt je Firma und
Stunde, und dieselbe Eingabe wird nicht zweimal berechnet.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import protokoll_vergleich as PV
from deps import db, now_iso
from konfig import zahl_env

from ai import kalibrierung, marktdaten, preisbasis, schemas
from ai.provider import json_bewerten, ki_aktiv, ki_modell

log = logging.getLogger("autohandel.ki")

SAMMLUNG = "ki_bewertungen"
LERN_SAMMLUNG = "ki_lernfaelle"
ART = "vertrag"
MAX_JE_STUNDE = zahl_env("KI_VERTRAG_MAX_JE_STUNDE", 40, unten=1, oben=10000)

SYSTEM_PROMPT = """Du bist der Bewertungsdienst von AutoSchnell, einer Software fuer Autohaendler in Deutschland.
Ein Einkaeufer (Sucher) verhandelt mit einem privaten oder gewerblichen Verkaeufer ueber ein inseriertes Gebrauchtfahrzeug und legt gleich den Kaufvertrag an. Er hat Schaeden am Fahrzeug festgestellt (Skizze mit Bauteil und Zusatzangaben). Du bewertest NUR den wirtschaftlichen Einfluss dieser Schaeden in Euro: Welchen Nachlass gegenueber dem Inseratspreis rechtfertigen sie?

Regeln:
1. Du bekommst Fahrzeugdaten, Inseratspreis (ggf. schon verhandelter Kaufpreis), die im Inserat genannten bekannten Maengel und die Liste der Schaeden (damages). Bewerte jeden Schaden als eine Position (source_id = id des Schadens).
2. Je Schaden: Reparaturweg, geschaetzte Reparaturkosten, empfohlener Nachlass als EIN Hauptwert, ein enger realistischer Bereich (hoechstens +-15 bis 20 % um den Hauptwert), Sicherheit 0..1, ein Satz Begruendung. Nachlass = das, was der Haendler wegen dieses Schadens weniger zahlen sollte (Reparatur + Aufwand + Wertminderung), nicht nur die reine Reparatur.
3. mentioned_in_listing=true heisst: dieser Schaden steht schon im Inserat und ist im Inseratspreis vermutlich eingepreist — dann deutlich geringerer Nachlass (nur der Teil, der ueber die Inseratsbeschreibung hinausgeht) und das in reason sagen.
4. Keine grossen Spannen. Fehlt dir eine konkrete Angabe (z. B. Lack beschaedigt?, Groesse?), gib die Position mit deiner besten Schaetzung ab UND stelle in needs_information genau EINE konkrete Frage mit 2-4 Antwortmoeglichkeiten (source_id = id des Schadens).
5. Unfallschaden nicht repariert (Blech + Rahmen oder Umfang unbekannt), Durchrostung tragender Teile: manual_review_required=true, priority "rot", Nachlass 0 (keine scheinpraezise Zahl), Grund nennen. Ein dokumentiert reparierter Unfallschaden ist Wertminderung, keine Reparatur.
6. Beruecksichtige Fahrzeugwert, Alter, Kilometer, Klasse und Marke (Premiummarken teurer; eine kleine Delle am 3.000-EUR-Auto anders als am 40.000-EUR-Auto). Ein Nachlass darf nie ueber dem Preis liegen und selten ueber 30 % davon.
7. combined: Einzelsumme, Abzug fuer ueberlappende Arbeiten (zwei Schaeden am selben Bauteil = eine Lackierung), empfohlener Gesamtnachlass, enger Bereich, Verhandlungseinstieg (10-25 % ueber dem empfohlenen Nachlass), Sicherheit.
8. arguments: hoechstens 3 kurze, sachliche Saetze fuer das Gespraech mit dem Verkaeufer (deutsch, neutral, z. B. "Der Kotfluegel vorne rechts hat eine Delle, die im Inserat nicht genannt ist.").
9. Preise in Euro inkl. MwSt. (Deutschland 2026). Nutze die Ausgangswerte unten als Orientierung und passe sie an das konkrete Fahrzeug an. Antworte ausschliesslich nach dem vorgegebenen JSON-Schema, alle Texte auf Deutsch.
10. Knapp: title hoechstens 8 Woerter, reason hoechstens 15 Woerter, repair_method hoechstens 6 Woerter, hoechstens 2 needs_information. Kategorie fuer Skizzen-Schaeden ist "damage".

""" + preisbasis.basis_als_text()


# ------------------------------------------------ Paket
def _zahl(w) -> Optional[float]:
    z = PV.zahl(w)
    return float(z) if z is not None else None


def _schaden(d: dict) -> dict:
    sd = d.get("severity_data") if isinstance(d.get("severity_data"), dict) else {}
    return {"id": str(d.get("id") or ""), "type": d.get("type_key") or d.get("type") or "",
            "label": d.get("type_label") or d.get("label") or "",
            "zone": d.get("zone") or d.get("part_label") or d.get("part") or "",
            "view": d.get("view") or "", "note": (d.get("note") or d.get("text") or "")[:200],
            "severity_data": {str(k)[:40]: str(v)[:60] for k, v in sd.items()}}


_ERWAEHNUNG = {
    "delle": r"delle|beule|eingedr", "kratzer": r"kratzer|schramme|lackschaden|lackkratzer",
    "steinschlag": r"steinschlag|steinschl", "rost": r"rost|korrosion", "hagelschaden": r"hagel",
    "beleuchtung": r"scheinwerfer|leuchte|licht\s+defekt|beleuchtung",
    "unfall_repariert": r"unfall", "unfall_nicht_repariert": r"unfall",
}


def _im_inserat(d: dict, text: str) -> bool:
    """Steht die Schadensart (grob) schon in Beschreibung/bekannten Maengeln?"""
    muster = _ERWAEHNUNG.get(str(d.get("type_key") or "").lower())
    return bool(muster and re.search(muster, text))


def paket_bauen(vehicle: dict, damages: List[dict], *, kaufpreis: Optional[float]) -> Dict[str, Any]:
    """Normalisiertes Eingabepaket — nur Fahrzeug, Preise, bekannte Maengel
    und die Schaeden. Keine Verkaeuferdaten."""
    v = vehicle or {}
    text = " ".join([str(v.get("description") or "")] + [str(m) for m in (v.get("known_defects") or [])]).lower()
    inserat = _zahl(v.get("price")) or _zahl(v.get("list_price"))
    ez = PV.monat_jahr_text(v.get("first_registration") or v.get("ezl") or "", "ez")
    kw = _zahl(v.get("power_kw"))
    # Wunsch Ahmad 26.09.2026: EZ, PS, Marke, Modell, Alter, Hubraum, Farbe
    from ai.pickup_assessment import _alter_jahre
    fahrzeug = {
        "make": (v.get("make_label") or v.get("make") or "")[:60],
        "model": (v.get("model_label") or v.get("model") or "")[:80],
        "variant": (v.get("model_description") or "")[:120],
        "first_registration": ez,
        "age_years": _alter_jahre(ez),
        "mileage_km": _zahl(v.get("mileage") or v.get("km")),
        "power_kw": kw,
        "power_ps": _zahl(v.get("power_ps")) or (round(kw * 1.36) if kw else None),
        "displacement_ccm": _zahl(v.get("displacement") or v.get("cubic_capacity")),
        "color": (v.get("exterior_color") or v.get("color") or "")[:40],
        "fuel": (v.get("fuel_label") or v.get("fuel") or "")[:40],
        "gearbox": (v.get("gearbox_label") or v.get("gearbox") or "")[:40],
        "category": (v.get("category_label") or v.get("category") or "")[:40],
        "previous_owners": _zahl(v.get("previous_owners")),
        "hu": PV.monat_jahr_text(v.get("hu") or "", "hu") or (str(v.get("hu") or "")[:20]),
        "accident_damaged_listing": v.get("accident_damaged"),
    }
    schaeden = []
    for d in damages or []:
        if not isinstance(d, dict):
            continue
        s = _schaden(d)
        s["mentioned_in_listing"] = _im_inserat(d, text)
        schaeden.append(s)
    return {
        "vehicle": fahrzeug,
        "prices": {"listing_price_eur": inserat, "agreed_price_eur": kaufpreis},
        "known_defects_listing": [str(m)[:200] for m in (v.get("known_defects") or []) if str(m or "").strip()][:20],
        "damages": schaeden,
    }


def eingabe_hash(paket: Dict[str, Any]) -> str:
    roh = json.dumps(paket, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()


def _oeffentlich(doc: dict) -> dict:
    return {"id": doc.get("id"), "status": doc.get("status"), "grund": doc.get("grund") or "",
            "input_hash": doc.get("input_hash"), "modell": doc.get("modell"),
            "prompt_version": doc.get("prompt_version"), "created_at": doc.get("created_at"),
            "dauer_ms": doc.get("dauer_ms"), "ergebnis": doc.get("ergebnis"),
            "kaufpreis": doc.get("kaufpreis"), "basis": doc.get("basis")}


async def _limit_erreicht(dealer_id: str) -> bool:
    seit = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    n = await db[SAMMLUNG].count_documents({"art": ART, "dealer_id": dealer_id, "created_at": {"$gte": seit},
                                            "status": {"$nin": ["cache", "keine"]}})
    return n >= MAX_JE_STUNDE


# ------------------------------------------------ Bewerten
async def bewerten(*, user: dict, vehicle_doc: dict, damages: List[dict],
                   kaufpreis: Optional[float] = None) -> dict:
    """Synchron: baut das Paket, nimmt ein vorhandenes Ergebnis fuer denselben
    Stand, sonst EIN KI-Aufruf. Liefert immer ein Ergebnis mit status (ok |
    keine | aus | limit | fehler | zeitlimit | ...). Wirft nie."""
    dealer_id = user.get("dealer_id") or ""
    vehicle = (vehicle_doc or {}).get("data") or {}
    paket = paket_bauen(vehicle, damages, kaufpreis=kaufpreis)
    h = eingabe_hash(paket)
    basis_preis = kaufpreis if kaufpreis else paket["prices"].get("listing_price_eur")
    jetzt = now_iso()
    basis = {"id": str(uuid.uuid4()), "art": ART, "dealer_id": dealer_id, "user_id": user.get("id"),
             "vehicle_id": vehicle_doc.get("id"), "input_hash": h,
             "prompt_version": schemas.PROMPT_VERSION_VERTRAG, "modell": ki_modell(),
             "created_at": jetzt, "kaufpreis": basis_preis,
             "basis": "kaufpreis" if kaufpreis else "inseratspreis"}
    try:
        if not paket["damages"]:
            return _oeffentlich({**basis, "status": "keine", "grund": "keine Schäden erfasst", "ergebnis": None})
        vorhanden = await db[SAMMLUNG].find_one({"art": ART, "dealer_id": dealer_id, "input_hash": h,
                                                 "status": "ok"}, {"_id": 0})
        if vorhanden:
            return _oeffentlich(vorhanden)
        if not ki_aktiv():
            return _oeffentlich({**basis, "status": "aus", "grund": "KI-Bewertung nicht aktiv", "ergebnis": None})
        if await _limit_erreicht(dealer_id):
            return _oeffentlich({**basis, "status": "limit",
                                 "grund": f"Höchstens {MAX_JE_STUNDE} Bewertungen je Stunde und Firma — bitte später erneut.",
                                 "ergebnis": None})
        # Zusatz (ungecacht): Marktpreise, Erfahrungswerte, Recherche je Fall
        # (beim Vertrag standardmaessig aus — der Sucher wartet auf die Karte).
        fall = await marktdaten.fall_recherche("vertrag", paket)
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
                   "usage": usage, "modell": antwort.get("modell") or basis["modell"], "eingabe": paket,
                   "recherche": ({"status": fall.get("status"), "suchen": fall.get("suchen"),
                                  "quellen": fall.get("quellen"), "text": fall.get("text")} if fall else None)}
        if antwort.get("status") == "ok" and isinstance(antwort.get("daten"), dict):
            ergebnis = schemas.bereinigen(antwort["daten"], kaufpreis=basis_preis)
            ergebnis["quellen"] = list((fall or {}).get("quellen") or [])
            for it in ergebnis.get("items") or []:
                it["priority"] = preisbasis.prioritaet(it.get("category") or "damage",
                                                       betrag=it.get("recommended_discount_eur"),
                                                       kaufpreis=basis_preis,
                                                       manuell=bool(it.get("manual_review_required")))
            eintrag.update(status="ok", grund="", ergebnis=ergebnis, roh=antwort["daten"])
        else:
            eintrag.update(status=antwort.get("status") or "fehler", grund=antwort.get("grund") or "",
                           ergebnis=None)
            try:
                import betrieb
                await betrieb.alarm(db, "ki_bewertung_fehlgeschlagen", ref=vehicle_doc.get("id") or "",
                                    status=eintrag["status"], grund=eintrag["grund"][:200], art=ART)
            except Exception:  # noqa: BLE001
                pass
        await db[SAMMLUNG].insert_one(dict(eintrag))
        return _oeffentlich(eintrag)
    except Exception:  # noqa: BLE001 — die KI ist Beiwerk, nie ein 500
        log.exception("KI-Schadennachlass %s gescheitert", vehicle_doc.get("id"))
        return _oeffentlich({**basis, "status": "fehler", "grund": "interner Fehler", "ergebnis": None})


# ------------------------------------------------ Lernen
async def lernfall_speichern(contract: dict, ki_bewertung_id: Optional[str]) -> None:
    """Beim Anlegen des Vertrags: was die KI empfahl und was wirklich im
    Vertrag steht (Inseratspreis minus Kaufpreis = erzielter Nachlass).
    Anonym, ohne Verkaeuferdaten. Wirft nie."""
    if not ki_bewertung_id:
        return
    try:
        bew = await db[SAMMLUNG].find_one({"id": ki_bewertung_id, "art": ART, "status": "ok",
                                           "dealer_id": contract.get("dealer_id")}, {"_id": 0})
        if not bew:
            return
        eingabe = bew.get("eingabe") or {}
        comb = (bew.get("ergebnis") or {}).get("combined") or {}
        inserat = (eingabe.get("prices") or {}).get("listing_price_eur")
        kaufpreis = contract.get("purchase_price")
        try:
            erzielt = round(float(inserat) - float(kaufpreis), 2) if inserat is not None and kaufpreis is not None else None
        except (TypeError, ValueError):
            erzielt = None
        await db[LERN_SAMMLUNG].update_one(
            {"contract_id": contract.get("id"), "ki_bewertung_id": ki_bewertung_id},
            {"$set": {
                "art": ART, "dealer_id": contract.get("dealer_id"), "contract_id": contract.get("id"),
                "ki_bewertung_id": ki_bewertung_id, "input_hash": bew.get("input_hash"),
                "created_at": now_iso(), "modell": bew.get("modell"), "prompt_version": bew.get("prompt_version"),
                "fahrzeug": eingabe.get("vehicle"), "schaeden": eingabe.get("damages"),
                "inseratspreis": inserat, "vertragspreis": kaufpreis,
                "ki_nachlass": comb.get("recommended_discount_eur"),
                "ki_bereich": [comb.get("discount_min_eur"), comb.get("discount_max_eur")],
                "tatsaechlicher_nachlass": erzielt if (erzielt is None or erzielt >= 0) else None,
                "items": [{k: i.get(k) for k in ("source_id", "category", "title", "recommended_discount_eur",
                                                  "discount_min_eur", "discount_max_eur", "confidence")}
                          for i in (bew.get("ergebnis") or {}).get("items") or []],
            }}, upsert=True)
        kalibrierung.zuruecksetzen()
    except Exception:  # noqa: BLE001
        log.exception("Lernfall (Vertrag) fuer %s nicht gespeichert", contract.get("id"))
