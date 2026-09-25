# -*- coding: utf-8 -*-
"""KI-Schadennachlass beim Erstellen des Kaufvertrags (Wunsch Ahmad
25.09.2026, Stufe 3; Umbau 26.09.2026).

Der Sucher markiert Schaeden in der Skizze und beantwortet je Schaden die
festen Fragen (Groesse, Lack, Tiefe, Umfang ...; "unbekannt" erlaubt). Erst
wenn alles beantwortet ist, startet "Ja, Schaeden bewerten" EINE Bewertung:
sofort eine deterministische Vorschau aus den Referenzen (`vorschau`), im
Hintergrund Kontext + gezielte Websuche (solange eigene Daten nicht reichen)
+ EIN KI-Aufruf; die Karte holt das Ergebnis per Abfrage (`lesen`).

Die KI bekommt einen fertigen Fall: Fahrzeug, Preise (Inserat und schon
verhandelter Preis), Inseratszustand, je Schaden eine Reparaturreferenz
(eigene Datenbank / Markttabelle / Startwerte), Marktvergleich aus unseren
eigenen Daten, Historie — und liefert vier Geldwerte je Schaden und
insgesamt. Keine Rueckfragen. Kostenbremse ai.budget (je Nutzer und Monat,
je Lauf); jede Websuche fuettert die eigene Preisdatenbank.

Rein beratend: "als Kaufpreis uebernehmen" fuellt nur das Preisfeld.
Dieselbe Eingabe wird nicht zweimal berechnet; begrenzt je Firma und Stunde.
"""
from __future__ import annotations

import asyncio
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

from ai import budget, freischaltung, kalibrierung, kontext, marktdaten, preisbasis, schemas
from ai.pickup_assessment import LEASE_S, _alter_jahre, _kosten_pruefen, _lease_abgelaufen
from ai.provider import json_bewerten, ki_aktiv, ki_modell

log = logging.getLogger("autohandel.ki")

SAMMLUNG = "ki_bewertungen"
LERN_SAMMLUNG = "ki_lernfaelle"
ART = "vertrag"
MAX_JE_STUNDE = zahl_env("KI_VERTRAG_MAX_JE_STUNDE", 40, unten=1, oben=10000)

SYSTEM_PROMPT = """Du bist der Bewertungsdienst von AutoSchnell, einer Software fuer Autohaendler in Deutschland.
Ein Einkaeufer (Sucher) verhandelt mit einem Verkaeufer ueber ein inseriertes Gebrauchtfahrzeug und legt gleich den Kaufvertrag an. Er hat Schaeden festgestellt (Skizze mit Bauteil und festen Angaben). Du bewertest NUR den wirtschaftlichen Einfluss dieser Schaeden in Euro: Welchen Nachlass rechtfertigen sie? Das Backend hat den Fall vollstaendig vorbereitet: Du ermittelst nichts, du bewertest.

Regeln:
1. Jeder Schaden in damages bekommt genau eine Position (source_id = id). possibly_known=true heisst: der Schaden koennte im Inserat genannt und eingepreist sein — Nachlass vorsichtiger, nicht streichen; sage das in reason.
2. Verwende ausschliesslich die gelieferten Daten: repair_reference (Reparaturweg, low/median/high EUR, Quelle), market (Vergleichspreise), history (eigene Faelle), prices, listing_state, die Marktrecherche unten. Allgemeines Fachwissen dient der Einordnung; erfinde keine konkreten aktuellen Markt-, Ersatzteil- oder Werkstattpreise. Fehlt eine Referenz, nutze die Ausgangswerte unten und sage das in reason. reason und title sind fuer Haendler und Fahrer: verstaendliches Deutsch, keine technischen Feldnamen oder Codes (nicht possibly_known, damage_worse, equipment_missing, manual_hint).
3. assumption_made=true bei einer Referenz heisst: eine Angabe war "unbekannt", die Referenz nimmt die vorsichtige Auspraegung — uebernimm das und nenne die Annahme in reason. Stelle keine Rueckfragen.
4. Vier Geldwerte je Position und insgesamt: minimum_justified_eur (darunter ist der Nachteil nicht ausgeglichen), fair_discount_eur (sachlich am besten begruendbarer Zielwert, meist nahe median der Referenz plus Aufwand/Wertminderung), best_realistic_eur (sehr gutes, noch vertretbares Ergebnis), negotiation_start_eur (erste Forderung, ueber best, nicht absurd). Immer min <= fair <= best <= start.
5. prices.agreed_price_eur ist der schon VOR der Schadenverhandlung vereinbarte Preis — ein allgemeiner Nachlass gegenueber dem Inserat ist kein Schadennachlass. Basis fuer den Zielpreis ist agreed_price_eur, sonst listing_price_eur. Beruecksichtige Fahrzeugwert, Alter, Kilometer, Klasse, Marke und die Marktposition (liegt der Preis schon unter dem Median, ist der Spielraum kleiner). Bei einem alten, guenstigen Fahrzeug ist voller Reparaturkostenersatz nicht automatisch der faire Nachlass.
6. assessment_kind je Position: repair_estimate = Schaden sichtbar, Reparaturweg klar (Normalfall). diagnosis_required = nur ein Symptom, keine bestaetigte Ursache: Technik-Mangel (type technik) mit status "nur Symptom" oder "unbekannt", Warnleuchte, Rost unter dem Lack mit unklarem Umfang; dann diagnosis_cost_eur (aus repair_reference.diagnosis) und drei Szenarien scenario_low/mid/high_eur (guenstiger, mittlerer, aufwendiger Reparaturfall aus repair_reference.scenarios und Recherche) — die vier Geldwerte folgen daraus (min = Diagnose, fair = guenstig, best = mittel, start = aufwendig); in reason ausdruecklich als Szenario benennen. Technik-Mangel mit status "Werkstatt hat Diagnose bestaetigt" = repair_estimate mit der Referenz. expert_check_required (manual_review_required=true, alle Betraege 0) bei: Unfallschaden nicht repariert mit Rahmen/unbekanntem Umfang, Durchrostung oder tragende Teile (Schweller, Traeger), Fahrzeug nicht fahrbereit, Hochvolt-Fehler. Kategorie fuer Technik-Maengel ist technical. Stellen die Schaeden den Kauf wirtschaftlich in Frage, setze deal_risk=reconsider_purchase; bei hohem Preisrisiko high. Kein starrer Prozentdeckel.
7. combined: sum_fair_eur, Abzug fuer ueberlappende Arbeiten (zwei Schaeden am selben Bauteil = eine Lackierung), dann die vier Gesamtwerte und deal_risk. arguments: hoechstens 3 kurze sachliche Saetze fuer das Gespraech mit dem Verkaeufer (deutsch, neutral, z. B. "Der Kotfluegel vorne rechts hat eine Delle, die im Inserat nicht genannt ist.").
8. Knapp: title hoechstens 8 Woerter, reason hoechstens 14 Woerter, repair_method hoechstens 6 Woerter. Kategorie fuer Skizzen-Schaeden ist "damage", fuer Technik-Maengel "technical". Antworte ausschliesslich nach dem JSON-Schema, alle Texte auf Deutsch, Preise in Euro inkl. MwSt.

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
_BAUTEILE = ("kotflügel", "kotfluegel", "tür", "tuer", "stoß", "stoss", "heck", "front", "haube", "dach", "schweller",
             "spiegel", "scheibe", "felge", "seite", "hinten", "vorne", "links", "rechts")
# Technischer Mangel (25.09.2026 abends): "im Inserat genannt", wenn der Text
# den Bereich anspricht (z. B. "Getriebe ruckelt", "Klima ohne Funktion").
_TECHNIK_WORTE = {
    "motor": ("motor", "öl", "oel", "kühl", "kuehl", "turbo", "zahnriemen", "steuerkette"),
    "getriebe": ("getriebe", "kupplung", "automatik", "dsg", "schalt"),
    "fahrwerk": ("fahrwerk", "brems", "lenk", "stoßd", "stossd", "feder", "achse", "radlager"),
    "elektrik": ("elektr", "steuerger", "sensor", "display", "infotainment", "navi", "kamera"),
    "klima": ("klima", "heizung", "gebläse", "geblaese"),
    "komfort": ("fensterheber", "zentralverriegelung", "verriegel", "sitz", "schiebedach"),
    "abgas": ("auspuff", "abgas", "agr", "dpf", "partikel", "kat", "adblue"),
    "batterie": ("batterie", "lichtmaschine", "anlasser", "springt nicht"),
    "innenraum": ("innenraum", "polster", "himmel", "geruch", "sitz"),
}


def _moeglich_im_inserat(d: dict, text: str) -> bool:
    """Umbau 26.09.2026: nicht mehr 'Wort kommt irgendwo vor' — die Schadensart
    UND ein Bauteilwort der Zone muessen im Inserat stehen. Dann gilt der
    Schaden als MOEGLICHERWEISE bekannt (possibly_known), nie als sicher."""
    if str(d.get("type_key") or "").lower() == "technik":
        sd = d.get("severity_data") if isinstance(d.get("severity_data"), dict) else {}
        bereich = preisbasis._bereich_schluessel(str(sd.get("bereich") or d.get("zone") or ""))
        return any(w in text for w in _TECHNIK_WORTE.get(bereich, ()))
    muster = _ERWAEHNUNG.get(str(d.get("type_key") or "").lower())
    if not muster or not re.search(muster, text):
        return False
    zone = str(d.get("zone") or "").lower()
    woerter = [w for w in _BAUTEILE if w in zone]
    return any(w in text for w in woerter) if woerter else False


def paket_bauen(vehicle: dict, damages: List[dict], *, kaufpreis: Optional[float],
                marktdoc: Optional[dict] = None) -> Dict[str, Any]:
    """Normalisiertes Eingabepaket — Fahrzeug, Preise, Inseratszustand und die
    Schaeden mit Reparaturreferenz. Keine Verkaeuferdaten."""
    v = vehicle or {}
    text = " ".join([str(v.get("description") or "")] + [str(m) for m in (v.get("known_defects") or [])]).lower()
    inserat = _zahl(v.get("price")) or _zahl(v.get("list_price"))
    ez = PV.monat_jahr_text(v.get("first_registration") or v.get("ezl") or "", "ez")
    kw = _zahl(v.get("power_kw"))
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
    }
    listing_state = {
        "accident_damaged": v.get("accident_damaged"),
        "roadworthy": v.get("roadworthy"),
        "keys": PV.zahl(v.get("keys_count")),
        "known_defects": [str(m)[:200] for m in (v.get("known_defects") or []) if str(m or "").strip()][:20],
        "equipment_count": len(v.get("features") or []),
    }
    schaeden = []
    for d in damages or []:
        if not isinstance(d, dict):
            continue
        s = _schaden(d)
        s["possibly_known"] = _moeglich_im_inserat(d, text)
        s["repair_reference"] = kontext.reparaturreferenz(d, marktdoc)
        schaeden.append(s)
    paket = {
        "vehicle": fahrzeug,
        "prices": {"listing_price_eur": inserat, "agreed_price_eur": kaufpreis},
        "listing_state": listing_state,
        "damages": schaeden,
    }
    paket["precomputed"] = kontext.vorberechnet(paket)
    return paket


def eingabe_hash(paket: Dict[str, Any]) -> str:
    kern = {"vehicle": paket.get("vehicle"), "prices": paket.get("prices"), "listing_state": paket.get("listing_state"),
            "damages": [{k: v for k, v in d.items() if k != "repair_reference"} for d in paket.get("damages") or []]}
    roh = json.dumps(kern, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()


def _oeffentlich(doc: dict) -> dict:
    return {"id": doc.get("id"), "status": doc.get("status"), "grund": doc.get("grund") or "",
            "input_hash": doc.get("input_hash"), "modell": doc.get("modell"),
            "prompt_version": doc.get("prompt_version"), "created_at": doc.get("created_at"),
            "dauer_ms": doc.get("dauer_ms"), "ergebnis": doc.get("ergebnis"),
            "kaufpreis": doc.get("kaufpreis"), "basis": doc.get("basis"), "kosten_ct": doc.get("kosten_ct"),
            "vorschau": doc.get("vorschau"), "budget": doc.get("budget")}


async def _limit_erreicht(dealer_id: str) -> bool:
    seit = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    n = await db[SAMMLUNG].count_documents({"art": ART, "dealer_id": dealer_id, "created_at": {"$gte": seit},
                                            "status": {"$nin": ["cache", "keine"]}})
    return n >= MAX_JE_STUNDE


# ------------------------------------------------ Vorschau (sofort, ohne KI)
def vorschau(vehicle_doc: dict, damages: List[dict], kaufpreis: Optional[float] = None,
             marktdoc: Optional[dict] = None) -> dict:
    """Deterministische Sofort-Schaetzung aus den Referenzen — erscheint, bevor
    die KI antwortet. Kein Aufruf, keine Kosten."""
    vehicle = (vehicle_doc or {}).get("data") or {}
    paket = paket_bauen(vehicle, damages, kaufpreis=kaufpreis, marktdoc=marktdoc)
    basis_preis = kaufpreis if kaufpreis else paket["prices"].get("listing_price_eur")
    v = kontext.vorschau(paket, basis_preis)
    v["positionen"] = [{"source_id": d["id"], "title": f"{d['label']} {d['zone']}".strip(),
                        "repair_reference": d.get("repair_reference")} for d in paket["damages"]]
    v["datenlage"] = kontext.datenlage(paket)
    v["kaufpreis"] = basis_preis
    v["basis"] = "kaufpreis" if kaufpreis else "inseratspreis"
    return v


# ------------------------------------------------ Bewerten
_laufende: set = set()


async def bewerten(*, user: dict, vehicle_doc: dict, damages: List[dict],
                   kaufpreis: Optional[float] = None, warten: bool = True) -> dict:
    """Startet die Bewertung. warten=True: rechnet inline und liefert das
    Endergebnis (Tests, Skripte). warten=False: legt sofort einen "laeuft"-
    Eintrag mit Vorschau an, rechnet im Hintergrund und liefert den Start-
    Zustand — die Karte fragt per `lesen(id)` nach. Wirft nie."""
    dealer_id = user.get("dealer_id") or ""
    vehicle = (vehicle_doc or {}).get("data") or {}
    jetzt = now_iso()
    basis = {"id": str(uuid.uuid4()), "art": ART, "dealer_id": dealer_id, "user_id": user.get("id"),
             "vehicle_id": vehicle_doc.get("id"), "prompt_version": schemas.PROMPT_VERSION_VERTRAG,
             "modell": ki_modell(), "created_at": jetzt, "basis": "kaufpreis" if kaufpreis else "inseratspreis"}
    # Kosten der Recherche (Haiku) werden separat mit dem Recherche-Modell gerechnet
    try:
        ktx = await kontext.sammeln(vehicle, ART, eigene_id=str(vehicle_doc.get("id") or ""))
        paket = paket_bauen(vehicle, damages, kaufpreis=kaufpreis, marktdoc=ktx.get("marktdoc"))
        eigene = await marktdaten.eigene_referenzen(paket, ART)
        kontext.eigene_anwenden(paket, eigene)
        h = eingabe_hash(paket)
        basis_preis = kaufpreis if kaufpreis else paket["prices"].get("listing_price_eur")
        basis.update(input_hash=h, kaufpreis=basis_preis)
        if not paket["damages"]:
            return _oeffentlich({**basis, "status": "keine", "grund": "keine Schäden erfasst", "ergebnis": None})
        vorhanden = await db[SAMMLUNG].find_one({"art": ART, "dealer_id": dealer_id, "input_hash": h,
                                                 "status": {"$in": ["ok", "laeuft"]}}, {"_id": 0},
                                                sort=[("created_at", -1)])
        if vorhanden and (vorhanden.get("status") == "ok" or not _lease_abgelaufen(vorhanden)):
            return _oeffentlich(vorhanden)
        vorl = kontext.vorschau(paket, basis_preis)
        if not ki_aktiv():
            return _oeffentlich({**basis, "status": "aus", "grund": "KI-Bewertung nicht aktiv", "ergebnis": None,
                                 "vorschau": vorl})
        # Wunsch Ahmad 25.09.2026 abends: KI je Sucher-Konto freigeschaltet (wie Abo).
        if not await freischaltung.konto_freigeschaltet(user.get("id")):
            return _oeffentlich({**basis, **freischaltung.gesperrt(), "vorschau": vorl})
        if await _limit_erreicht(dealer_id):
            return _oeffentlich({**basis, "status": "limit",
                                 "grund": f"Höchstens {MAX_JE_STUNDE} Bewertungen je Stunde und Firma — bitte später erneut.",
                                 "ergebnis": None, "vorschau": vorl})
        bud = await budget.pruefen(user_id=user.get("id"), dealer_id=dealer_id, art=ART)
        if not bud["erlaubt"]:
            eintrag = {**basis, "status": "budget", "grund": bud["grund"], "ergebnis": None, "vorschau": vorl,
                       "budget": bud, "dauer_ms": 0, "kosten_ct": 0}
            await db[SAMMLUNG].insert_one(dict(eintrag))
            return _oeffentlich(eintrag)
        lease = (datetime.now(timezone.utc) + timedelta(seconds=LEASE_S)).isoformat()
        start = {**basis, "status": "laeuft", "grund": "", "ergebnis": None, "vorschau": vorl, "lease_until": lease,
                 "kosten_ct": 0}
        # Review 25.09.2026 abends: genau EIN laufender Lauf je Firma und
        # Eingabe-Stand (Unique-Index ki_vertrag_laeuft_je_stand) — zwei
        # gleichzeitige Klicks starten keine zweite KI. Ein abgelaufener
        # Lease wird atomar uebernommen.
        andere = await _lauf_beanspruchen(start, dealer_id, h)
        if andere is not None:
            return _oeffentlich(andere)
        res = await budget.reservieren(user_id=user.get("id"), dealer_id=dealer_id, art=ART)
        if res is None:
            eintrag = {**basis, "status": "budget", "grund": bud["grund"] or "Monatsbudget für KI-Bewertungen aufgebraucht.",
                       "ergebnis": None, "vorschau": vorl, "budget": bud, "dauer_ms": 0, "kosten_ct": 0}
            await db[SAMMLUNG].update_one({"id": basis["id"]}, {"$set": eintrag, "$unset": {"lease_until": ""}})
            return _oeffentlich(eintrag)
        paket["market"] = kontext.marktposition(ktx.get("markt"), listing=paket["prices"].get("listing_price_eur"),
                                                agreed=kaufpreis)
        paket["history"] = ktx.get("historie")
        lauf = _rechnen(basis, paket, vorl, bud, eigene, vehicle_doc, marktdoc=ktx.get("marktdoc"), res=res)
        if warten:
            return await lauf
        aufgabe = asyncio.get_running_loop().create_task(lauf)
        _laufende.add(aufgabe)
        aufgabe.add_done_callback(_laufende.discard)
        return _oeffentlich(start)
    except Exception:  # noqa: BLE001 — die KI ist Beiwerk, nie ein 500
        log.exception("KI-Schadennachlass %s gescheitert", vehicle_doc.get("id"))
        return _oeffentlich({**basis, "status": "fehler", "grund": "interner Fehler", "ergebnis": None})


async def _lauf_beanspruchen(start: dict, dealer_id: str, h: str) -> Optional[dict]:
    """Den 'laeuft'-Platz fuer diesen Stand belegen. None = wir halten ihn;
    sonst der fremde Lauf (laeuft, frisch), den der Aufrufer zurueckgibt."""
    from pymongo import ReturnDocument
    from pymongo.errors import DuplicateKeyError
    try:
        await db[SAMMLUNG].insert_one(dict(start))
        return None
    except DuplicateKeyError:
        pass
    filt = {"art": ART, "dealer_id": dealer_id, "input_hash": h, "status": "laeuft"}
    fremd = await db[SAMMLUNG].find_one(filt, {"_id": 0})
    if fremd and not _lease_abgelaufen(fremd):
        return fremd
    # abgelaufen: atomar uebernehmen (nur wer den alten Lease trifft, gewinnt)
    uebernommen = await db[SAMMLUNG].find_one_and_update(
        {**filt, "lease_until": {"$lt": datetime.now(timezone.utc).isoformat()}},
        {"$set": {k: v for k, v in start.items() if k != "_id"}},
        projection={"_id": 0}, return_document=ReturnDocument.AFTER)
    if uebernommen is not None:
        return None
    return await db[SAMMLUNG].find_one(filt, {"_id": 0}) or fremd


async def _rechnen(basis: dict, paket: dict, vorl: dict, bud: dict, eigene: dict, vehicle_doc: dict,
                   *, marktdoc: Optional[dict], res: Optional[dict] = None) -> dict:
    """Der eigentliche Lauf: Websuche (wenn noetig), Lernen, KI, Ablage."""
    try:
        fall = await marktdaten.fall_recherche(ART, paket, sparmodus=bud["sparmodus"], eigene=eigene)
        gelernt = await marktdaten.lernen_aus_recherche(fall, paket, ART)
        if gelernt:
            eigene = await marktdaten.eigene_referenzen(paket, ART)
            kontext.eigene_anwenden(paket, eigene)
        lage = kontext.datenlage(paket)
        zusatz = "\n\n".join(t for t in (marktdaten.als_text(marktdoc),
                                          await kalibrierung.prompt_zusatz(basis["dealer_id"]),
                                          marktdaten.fall_als_text(fall)) if t)
        antwort = await json_bewerten(system=SYSTEM_PROMPT, nutzer=paket, schema=schemas.ANTWORT_SCHEMA,
                                      zusatz=zusatz or None)
        kosten = await _kosten_pruefen(antwort.get("usage") or {}, antwort.get("modell") or basis["modell"],
                                       str(vehicle_doc.get("id") or ""), ART, fall)
        await budget.abrechnen(res, kosten)
        res = None
        usage = dict(antwort.get("usage") or {})
        if fall:
            for k, v in (fall.get("usage") or {}).items():
                usage[k] = int(usage.get(k) or 0) + int(v or 0)
        eintrag = {**basis, "dauer_ms": int(antwort.get("dauer_ms") or 0) + int((fall or {}).get("dauer_ms") or 0),
                   "usage": usage, "kosten_ct": kosten, "modell": antwort.get("modell") or basis["modell"],
                   "eingabe": paket, "datenlage": lage, "vorschau": vorl,
                   "budget": {k: bud.get(k) for k in ("verbraucht_ct", "grenze_ct", "sparmodus")},
                   "recherche": ({"status": fall.get("status"), "suchen": fall.get("suchen"),
                                  "quellen": fall.get("quellen"), "text": fall.get("text"),
                                  "gelernt": gelernt} if fall else None)}
        if antwort.get("status") == "ok" and isinstance(antwort.get("daten"), dict):
            ergebnis = schemas.bereinigen(antwort["daten"], kaufpreis=basis["kaufpreis"])
            for it in ergebnis.get("items") or []:
                it["priority"] = preisbasis.prioritaet(it.get("category") or "damage",
                                                       betrag=it.get("fair_discount_eur"),
                                                       kaufpreis=basis["kaufpreis"],
                                                       manuell=bool(it.get("manual_review_required")))
            ergebnis["datenlage"] = schemas.datenlage_anpassen(ergebnis, lage)
            ergebnis["market"] = paket.get("market")
            ergebnis["quellen"] = list((fall or {}).get("quellen") or [])
            ergebnis["referenzen"] = {d["id"]: d.get("repair_reference") for d in paket["damages"] if d.get("repair_reference")}
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
        await db[SAMMLUNG].update_one({"id": basis["id"]}, {"$set": eintrag, "$unset": {"lease_until": ""}})
        return _oeffentlich(eintrag)
    except Exception:  # noqa: BLE001
        log.exception("KI-Schadennachlass-Lauf %s gescheitert", basis.get("id"))
        await budget.abrechnen(res, 0)           # Reservierung freigeben
        eintrag = {**basis, "status": "fehler", "grund": "interner Fehler", "ergebnis": None, "vorschau": vorl}
        try:
            await db[SAMMLUNG].update_one({"id": basis["id"]}, {"$set": eintrag, "$unset": {"lease_until": ""}})
        except Exception:  # noqa: BLE001
            pass
        return _oeffentlich(eintrag)


async def lesen(bewertung_id: str, dealer_id: str) -> Optional[dict]:
    """Stand einer Bewertung (die Karte fragt nach). Ein "laeuft" ohne
    Ergebnis nach LEASE_S Sekunden gilt als abgestuerzt -> fehler."""
    doc = await db[SAMMLUNG].find_one({"id": bewertung_id, "art": ART, "dealer_id": dealer_id}, {"_id": 0})
    if not doc:
        return None
    if _lease_abgelaufen(doc):
        doc = {**doc, "status": "fehler", "grund": "Bewertung abgebrochen (Zeitlimit) — bitte erneut versuchen."}
    return _oeffentlich(doc)


# ------------------------------------------------ Lernen
async def lernfall_speichern(contract: dict, ki_bewertung_id: Optional[str]) -> None:
    """Beim Anlegen des Vertrags: was die KI empfahl und was wirklich im
    Vertrag steht. Umbau 26.09.2026: gelernt wird NUR der Nachlass wegen der
    Schaeden — also nur, wenn die Bewertung auf dem schon VOR der
    Schadenverhandlung vereinbarten Preis (basis "kaufpreis") beruhte:
    tatsaechlich = dieser Preis minus Vertragspreis. Inseratspreis minus
    Vertragspreis enthaelt den allgemeinen Nachlass und wird NICHT gelernt.
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
        vorher = (eingabe.get("prices") or {}).get("agreed_price_eur")
        kaufpreis = contract.get("purchase_price")
        erzielt = None
        if bew.get("basis") == "kaufpreis" and vorher is not None and kaufpreis is not None:
            try:
                erzielt = round(float(vorher) - float(kaufpreis), 2)
            except (TypeError, ValueError):
                erzielt = None
            if erzielt is not None and erzielt < 0:
                erzielt = None
        await db[LERN_SAMMLUNG].update_one(
            {"contract_id": contract.get("id"), "ki_bewertung_id": ki_bewertung_id},
            {"$set": {
                "art": ART, "dealer_id": contract.get("dealer_id"), "contract_id": contract.get("id"),
                "ki_bewertung_id": ki_bewertung_id, "input_hash": bew.get("input_hash"),
                "created_at": now_iso(), "modell": bew.get("modell"), "prompt_version": bew.get("prompt_version"),
                "fahrzeug": eingabe.get("vehicle"), "schaeden": eingabe.get("damages"),
                "datenlage": bew.get("datenlage"),
                "inseratspreis": inserat, "preis_vor_maengelverhandlung": vorher, "vertragspreis": kaufpreis,
                "ki_nachlass": comb.get("fair_discount_eur"),
                "ki_bereich": [comb.get("minimum_justified_eur"), comb.get("best_realistic_eur")],
                "tatsaechlicher_nachlass": erzielt,
                "items": [{k: i.get(k) for k in ("source_id", "category", "title", "fair_discount_eur",
                                                  "minimum_justified_eur", "best_realistic_eur")}
                          for i in (bew.get("ergebnis") or {}).get("items") or []],
            }}, upsert=True)
        kalibrierung.zuruecksetzen()
    except Exception:  # noqa: BLE001
        log.exception("Lernfall (Vertrag) fuer %s nicht gespeichert", contract.get("id"))
