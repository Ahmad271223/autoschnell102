# -*- coding: utf-8 -*-
"""Marktanalyse fuer die KI-Bewertung (Wunsch Ahmad 26.09.2026: "so genau wie
es geht, auf ADAC und Smart-Repair achten" — und: "Websuche erst mal fuer ein
Jahr, dabei eine eigene Datenbank aufbauen").

Drei Bausteine:

1. **Markttabelle** (ki_marktdaten, ein Dokument "aktuell"): einmal je
   KI_MARKTDATEN_TAGE (Standard 30) recherchiert die KI per Websuche in drei
   Gruppen aktuelle Reparatur-/Smart-Repair-Preise fuer unsere Positionen und
   legt sie mit Quellen ab. Ausgeloest vom stuendlichen Aufraeumlauf oder per
   Knopf auf /admin/betrieb.

2. **Recherche je Fall**: vor der Bewertung sucht die KI gezielt zu den
   Schaeden/Abweichungen dieses Fahrzeugs (hoechstens MAX_SUCHEN_FALL Suchen)
   — Standard AN bei Abholung und Vertrag (Kostenbremse: ai.budget).

3. **Eigene Preisdatenbank** (ki_reparaturpreise): jede Recherche endet mit
   einem festen Datenblock (###DATEN, eine Zeile je Wert), der ohne weiteren
   KI-Aufruf geparst und je Referenzschluessel + Marke + Altersklasse
   gespeichert wird. Liegen zu einem Schaden genug frische eigene Werte vor
   (EIGENE_MIN), entfaellt die Websuche fuer diese Position — so wird das
   System mit jedem Fall guenstiger und schneller.

Websuche und unser festes JSON-Antwortformat gehen nicht in einem Aufruf
(Zitate sind mit strukturierter Ausgabe unvereinbar) — deshalb immer zwei
Schritte: Recherche als Text, Bewertung/Umwandlung als JSON.
"""
from __future__ import annotations

import logging
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from deps import db as _db, now_iso
from konfig import schalter_env, zahl_env

from ai import preisbasis, quellen as _quellen
from ai.provider import LaufUebernommen, json_bewerten, ki_aktiv, ki_modell, ki_recherche_modell, recherche

log = logging.getLogger("autohandel.ki")

SAMMLUNG = "ki_marktdaten"
PREIS_SAMMLUNG = "ki_reparaturpreise"
DOK_ID = "aktuell"
MAX_SUCHEN_TABELLE = 8          # je Gruppe (vier Gruppen, siehe _gruppen)
UMWANDLUNG_MAX_TOKENS = 6000    # Tabelle je Gruppe umwandeln, nie abschneiden (Betrieb 26.09.2026)
MAX_SUCHEN_FALL = 2             # Probelauf 26.09.2026: 4 direkte Suchen = ~140k Tokens = 46 ct
# Review 26.09.2026 (Nr. 19/20): eigene Werte reichen erst ab EIGENE_MIN
# Werten aus mindestens zwei verschiedenen Quellen, hoechstens EIGENE_TAGE alt.
EIGENE_MIN = 5                  # ab so vielen frischen eigenen Werten keine Suche mehr
EIGENE_QUELLEN_MIN = 2
EIGENE_TAGE = 120
EIGENE_STUFEN = ("marke_modell_alter", "marke_alter", "marke", "alle")
# Review 26.09.2026 (Nr. 21): je Recherche hoechstens 12 Positionen — die
# teuersten (Referenz-Median) zuerst, nicht die ersten acht der Liste.
RECHERCHE_POSITIONEN_MAX = 12
DATEN_MARKER = "###DATEN"
BERICHT_MAX = 12000
# Kostendeckel 27.09.2026: der Fall-Text im Prompt der Bewertung ist
# hoechstens so lang (Recherchetext bis 6.000 Zeichen + Quellenzeile) — mit
# dieser Laenge plant die Kostenkasse die Bewertung VOR der Recherche ein.
FALL_RECHERCHE_TEXT_MAX = 6000
FALL_TEXT_MAX_ZEICHEN = 8000
# Probelauf 26.09.2026: mit EINER Anfrage fuer alle Positionen verbrauchte die
# KI alle Suchen, bevor sie einen Wert notiert hatte, und gab dann auf.
# Deshalb kleine Gruppen und die Anweisung, Werte sofort aufzuschreiben.
SUCH_ANWEISUNG = ("Du hast fuer diese Liste hoechstens {n} Suchen. Plane sie (eine Suche deckt 2-3 Positionen, "
                  "ADAC zuerst) und schreibe jeden gefundenen Wert SOFORT in deine Antwort. Ist das Suchlimit "
                  "erreicht, gib die bis dahin gefundenen Werte aus — niemals abbrechen oder eine leere Antwort geben.")
DATEN_ANWEISUNG = ("Schliesse deine Antwort mit einer Zeile '" + DATEN_MARKER + "' ab und darunter je gefundenem Wert "
                   "GENAU EINE Zeile im Format: id|min_eur|max_eur|typisch_eur|quelle|url|passung — id ist die id der "
                   "Position aus der Liste, Betraege als ganze Zahlen ohne Einheit, quelle der Name der Quelle "
                   "(z. B. ADAC), ggf. mit Einschraenkung, url die VOLLSTAENDIGE Adresse der Seite aus den "
                   "Suchergebnissen, von der der Wert stammt (nicht erfinden, nicht kuerzen). passung ist 'genau', "
                   "wenn der Wert zu Fahrzeugklasse, Schadenbild und Jahr passt, sonst 'ungefaehr' "
                   "(Orientierungswert). Lieber ein ungefaehrer Wert mit Hinweis als keine Zeile. "
                   "Keine weiteren Zeilen nach dem Block.")
_GRUPPEN = (
    ("Karosserie und Lack", ("delle", "kratzer", "steinschlag", "rost", "hagelschaden")),
    ("Licht, Felgen, Unfall", ("beleuchtung", "felge", "unfall_nicht_repariert", "unfall_repariert")),
    ("Schluessel, Reifen, HU, Unterlagen, Abweichungen",
     ("keys", "tires", "documents", "hu", "mileage", "previous_owners", "equipment_missing", "equipment_defect")),
    ("Technik: Diagnose und Reparatur", ("technical", "warning_light")),
)
# Quellen je Schadengruppe (Schadenkatalog 25.09.2026): die Recherche soll
# nicht ueberall dieselben Seiten befragen.
QUELLEN_JE_GRUPPE = (
    "Quellen je Gruppe: Karosserie/Lack (Delle, Kratzer, Rost, Hagel, Steinschlag im Lack): ADAC, ATU Smart-Repair, "
    "FairGarage, Dellen-Doktor. Scheiben: Carglass, Wintec. Licht/Felgen: FairGarage, Autobutler, Felgenfachbetriebe. "
    "Technik (Motor, Getriebe, Fahrwerk, Bremsen, Elektrik, Klima, Abgas, Batterie, Warnleuchte): FairGarage/DAT, "
    "Autobutler, repareo, Bosch Car Service (Diagnoseleistungen und -preise); HELLA Tech World nur fuer Ursachen, "
    "nicht fuer Preise. Arbeitskosten: DEKRA-Stundensaetze sind NETTO und ohne Lackmaterial — dann 'netto' hinter "
    "den Quellennamen schreiben. Schluessel/Teile: Markenangaben.")


def aktiv() -> bool:
    return schalter_env("KI_MARKTANALYSE_AKTIV", True)


def tage() -> int:
    return zahl_env("KI_MARKTDATEN_TAGE", 30, unten=1, oben=365)


def je_fall(art: str) -> bool:
    if art == "abholung":
        return schalter_env("KI_MARKTANALYSE_ABHOLUNG", True)
    return schalter_env("KI_MARKTANALYSE_VERTRAG", True)


RECHERCHE_SYSTEM = """Du recherchierst fuer AutoSchnell (Software fuer Autohaendler in Deutschland) aktuelle Reparatur- und Smart-Repair-Preise fuer Gebrauchtwagen — Deutschland, Euro inkl. MwSt., Stand heute.
Bevorzugte Quellen: ADAC (adac.de), Smart-Repair-Anbieter (z. B. Dellen-Doktor, Dellentechnik, Carglass/Wintec fuer Scheiben, ATU, Pitstop), Werkstattportale (FairGarage, autobutler, repareo, Werkstattvergleich), Bosch Car Service (Diagnose), DEKRA (Stundensaetze, netto), Fachanbieter fuer Fahrzeugschluessel und Reifen. Verbraucherportale wie Auto Bild, auto motor und sport sind in Ordnung; Forenbeitraege und Anzeigen nicht.
""" + QUELLEN_JE_GRUPPE + """
Bei Technik-Maengeln ohne bestaetigte Diagnose: Diagnosekosten (Fehlerspeicher auslesen, Pruefung) und je einen guenstigen, mittleren und aufwendigen Reparaturfall angeben — als Szenarien.
Regeln: je Position eine realistische Spanne (min-max) und einen typischen Wert, dazu die Quelle (Name + Adresse). Orientierungswerte ZAEHLEN als Fund: ADAC-Beispielpreise, Preisspannen von Anbietern oder Portalen gehoeren in den Datenblock, auch wenn sie nicht exakt zu Fahrzeug, Groesse oder Jahr passen oder netto sind — schreibe die Einschraenkung kurz hinter den Quellennamen (z. B. "ADAC, netto 2024"). Verboten sind nur frei erfundene Zahlen ohne Quelle; dann sage das statt zu schaetzen. Antworte auf Deutsch, knapp, als Liste."""

UMWANDLUNG_SYSTEM = """Du wandelst einen Recherchebericht ueber Reparaturpreise in eine feste Tabelle um. typ ist IMMER der technische Schluessel aus der Positionsliste (z. B. keys fuer Schluessel, tires fuer Reifen, documents fuer Unterlagen, hu fuer HU), nie ein deutsches Wort. Uebernimm nur Werte, die im Bericht stehen (Euro inkl. MwSt.). Fehlt eine Position im Bericht, lass sie weg. typisch_eur liegt zwischen min_eur und max_eur. quelle: Name der Quelle (z. B. "ADAC", "Dellen-Doktor"), hinweis: hoechstens 12 Woerter. Antworte ausschliesslich nach dem Schema."""

MARKT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "positionen": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "typ": {"type": "string", "enum": sorted({b["typ"] for b in preisbasis.BASIS})},
                "auspraegung": {"type": "string", "description": "moeglichst der Wortlaut aus der Positionsliste"},
                "min_eur": {"type": "number"},
                "max_eur": {"type": "number"},
                "typisch_eur": {"type": "number"},
                "quelle": {"type": "string"},
                "hinweis": {"type": "string"},
            },
            "required": ["typ", "auspraegung", "min_eur", "max_eur", "typisch_eur", "quelle", "hinweis"],
            "additionalProperties": False,
        }},
        "zusammenfassung": {"type": "string", "description": "2-3 Saetze, deutsch"},
    },
    "required": ["positionen", "zusammenfassung"],
    "additionalProperties": False,
}


def _positionen_liste() -> List[Dict[str, str]]:
    return [{"typ": b["typ"], "auspraegung": b["auspraegung"], "verfahren": b["verfahren"]}
            for b in preisbasis.BASIS if not (b.get("manuell") and b["max"] == 0)]


def _gruppen() -> List[tuple]:
    alle = _positionen_liste()
    raus = []
    for titel, typen in _GRUPPEN:
        pos = [p for p in alle if p["typ"] in typen]
        if pos:
            raus.append((titel, pos))
    return raus


def _frage_tabelle(titel: str, positionen: List[Dict[str, str]], max_suchen: int = MAX_SUCHEN_TABELLE) -> str:
    zeilen = [f"- {p['typ']} / {p['auspraegung']} ({p['verfahren']})" for p in positionen]
    return (f"Recherchiere aktuelle Preise (Deutschland, inkl. MwSt.) fuer diese Positionen ({titel}) — Schaeden am "
            "Gebrauchtwagen bzw. Abweichungen bei der Abholung. Suche gezielt nach ADAC-Angaben und "
            "Smart-Repair-Preislisten; fasse je Position Spanne, typischen Wert und Quelle zusammen:\n"
            + "\n".join(zeilen) + "\n\n" + SUCH_ANWEISUNG.format(n=max_suchen))


def _zahl(w, unten=0.0) -> float:
    try:
        z = float(str(w).replace(".", "").replace(",", ".")) if isinstance(w, str) and "," in str(w) else float(w)
    except (TypeError, ValueError):
        return unten
    if z != z or z in (float("inf"), float("-inf")):
        return unten
    return round(max(unten, z), 2)


def _positionen_bereinigen(roh: List[dict]) -> List[Dict[str, Any]]:
    bekannt = {b["typ"] for b in preisbasis.BASIS}
    raus = []
    for p in roh or []:
        if not isinstance(p, dict):
            continue
        typ = str(p.get("typ") or "").strip().lower()
        lo, hi, ty = _zahl(p.get("min_eur")), _zahl(p.get("max_eur")), _zahl(p.get("typisch_eur"))
        if typ not in bekannt or hi <= 0:
            continue
        if lo > hi:
            lo, hi = hi, lo
        ty = min(max(ty, lo), hi) if ty else round((lo + hi) / 2, 2)
        raus.append({"typ": typ, "auspraegung": str(p.get("auspraegung") or "")[:80],
                     "min_eur": lo, "max_eur": hi, "typisch_eur": ty,
                     "quelle": str(p.get("quelle") or "")[:80], "hinweis": str(p.get("hinweis") or "")[:120]})
    return raus[:60]


def _oeffentlich(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    return {"stand": doc.get("stand"), "status": doc.get("status"), "grund": doc.get("grund") or "",
            "positionen": doc.get("positionen") or [], "quellen": doc.get("quellen") or [],
            "zusammenfassung": doc.get("zusammenfassung") or "", "modell": doc.get("modell"),
            "suchen": doc.get("suchen"), "dauer_ms": doc.get("dauer_ms"), "alter_tage": alter_tage(doc)}


def alter_tage(doc: Optional[dict]) -> Optional[float]:
    if not doc or not doc.get("stand"):
        return None
    try:
        stand = datetime.fromisoformat(str(doc["stand"]).replace("Z", "+00:00"))
        if stand.tzinfo is None:
            stand = stand.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - stand).total_seconds() / 86400, 2)
    except ValueError:
        return None


def frisch(doc: Optional[dict]) -> bool:
    a = alter_tage(doc)
    return a is not None and doc.get("status") == "ok" and a < tage()


# ------------------------------------------------ Markttabelle
LAUF_MAX_MINUTEN = 10           # laenger laeuft kein Tabellen-Lauf; danach gilt der Merker als verwaist


async def lauf_markieren(db=None) -> bool:
    """Merker "Lauf laeuft seit" in der DB (beide Server sehen ihn). False, wenn ein
    frischer Merker steht (dann laeuft schon jemand). Atomar per Filter."""
    db = db if db is not None else _db
    grenze = (datetime.now(timezone.utc) - timedelta(minutes=LAUF_MAX_MINUTEN)).isoformat()
    jetzt = now_iso()
    r = await db[SAMMLUNG].update_one(
        {"_id": DOK_ID, "$or": [{"lauf_seit": {"$exists": False}}, {"lauf_seit": None}, {"lauf_seit": {"$lt": grenze}}]},
        {"$set": {"lauf_seit": jetzt}})
    if r.matched_count:
        return True
    if not await db[SAMMLUNG].find_one({"_id": DOK_ID}, {"_id": 1}):
        await db[SAMMLUNG].update_one({"_id": DOK_ID}, {"$setOnInsert": {"lauf_seit": jetzt}}, upsert=True)
        return True
    return False


# Kostenfrage Ahmad 30.09.2026: der Deckel von 20 ct gilt je BEWERTUNG. Ein Lauf der Markttabelle ist eine
# grosse Webrecherche (vier Gruppen, bis zu 32 Suchen, gut 1 EUR) und war NICHT begrenzt: nach einem
# Fehlversuch wurde alle sechs Stunden erneut recherchiert, jeder Klick auf "Marktdaten jetzt" startete
# einen vollen Lauf, und nach einer Ausnahme sogar jede Stunde. Jetzt: hoechstens LAEUFE_JE_MONAT_MAX
# gestartete Laeufe je Kalendermonat (automatisch UND per Knopf zusammen), danach Status "limit" + Alarm.
def laeufe_max() -> int:
    return zahl_env("KI_MARKTDATEN_LAEUFE_MAX", 4, unten=1, oben=60)


def _monat() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def lauf_zaehlen(db=None) -> bool:
    """Einen Tabellenlauf fuer diesen Monat verbuchen — atomar. False = Monatsgrenze erreicht."""
    db = db if db is not None else _db
    monat, grenze = _monat(), laeufe_max()
    r = await db[SAMMLUNG].update_one({"_id": DOK_ID, "laeufe_monat": monat, "laeufe_n": {"$lt": grenze}},
                                      {"$inc": {"laeufe_n": 1}})
    if r.matched_count:
        return True
    r = await db[SAMMLUNG].update_one({"_id": DOK_ID, "laeufe_monat": {"$ne": monat}},
                                      {"$set": {"laeufe_monat": monat, "laeufe_n": 1}})
    if r.matched_count:
        return True
    if not await db[SAMMLUNG].find_one({"_id": DOK_ID}, {"_id": 1}):
        await db[SAMMLUNG].update_one({"_id": DOK_ID}, {"$setOnInsert": {"laeufe_monat": monat, "laeufe_n": 1}},
                                      upsert=True)
        return True
    return False


async def _limit_melden(db) -> dict:
    grund = (f"Monatsgrenze erreicht: {laeufe_max()} Läufe der Markttabelle in diesem Monat "
             "(KI_MARKTDATEN_LAEUFE_MAX). Die bisherige Tabelle gilt weiter.")
    try:
        import betrieb
        await betrieb.alarm(db, "ki_marktdaten_limit", ref=_monat(), grund=grund,
                            hinweis="Jeder Lauf kostet gut 1 EUR. Ursache der Fehlversuche pruefen (Betrieb → KI); "
                                    "erst danach die Grenze mit env_setzen.sh erhoehen.")
    except Exception:  # noqa: BLE001
        pass
    return {"status": "limit", "gestartet": False, "grund": grund}


async def lauf_beenden(db=None) -> None:
    db = db if db is not None else _db
    try:
        await db[SAMMLUNG].update_one({"_id": DOK_ID}, {"$unset": {"lauf_seit": ""}})
    except Exception:  # noqa: BLE001
        pass


def laeuft(doc: Optional[dict]) -> bool:
    seit = (doc or {}).get("lauf_seit")
    if not seit:
        return False
    try:
        t = datetime.fromisoformat(str(seit).replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - t) < timedelta(minutes=LAUF_MAX_MINUTEN)


async def aktualisieren_im_hintergrund(db=None) -> dict:
    """Befund 26.09.2026 abends: der Knopf "Marktdaten jetzt" lief 3-4 Minuten im
    HTTP-Request und der Load Balancer brach nach ~60 s ab (504). Jetzt: Merker setzen,
    Lauf als Task starten, sofort antworten; der Stand kommt ueber GET /admin/ki."""
    import asyncio
    db = db if db is not None else _db
    if not await lauf_markieren(db):
        return {"status": "laeuft", "gestartet": False}
    if not await lauf_zaehlen(db):
        await lauf_beenden(db)
        return await _limit_melden(db)

    async def _lauf():
        try:
            await aktualisieren(db, erzwingen=True)
        except Exception:  # noqa: BLE001
            log.exception("Markttabelle: Hintergrundlauf gescheitert")
        finally:
            await lauf_beenden(db)
    aufgabe = asyncio.get_running_loop().create_task(_lauf())
    _HINTERGRUND.add(aufgabe)
    aufgabe.add_done_callback(_HINTERGRUND.discard)
    return {"status": "gestartet", "gestartet": True}


_HINTERGRUND: set = set()


async def aktuell(db=None) -> Optional[dict]:
    db = db if db is not None else _db
    try:
        return await db[SAMMLUNG].find_one({"_id": DOK_ID})
    except Exception:  # noqa: BLE001
        return None


async def _umwandeln_je_gruppe(gruppen_texte: List[tuple]) -> Dict[str, Any]:
    """Wandelt jede Recherche-Gruppe getrennt in Tabellenzeilen um und fuegt sie zusammen.
    Rueckgabe wie json_bewerten: daten.positionen (dedupliziert), daten.zusammenfassung,
    usage (summiert), dauer_ms (summiert), grund (letzter Fehler), fehlgeschlagen (Gruppen)."""
    roh: List[dict] = []
    zusammenfassungen: List[str] = []
    usage: Dict[str, int] = {}
    dauer = 0
    fehler: List[str] = []
    grund = ""
    for titel, text, positionen in gruppen_texte:
        j = await json_bewerten(system=UMWANDLUNG_SYSTEM,
                                nutzer={"bericht": (f"## {titel}" + chr(10) + text)[:BERICHT_MAX], "positionen": positionen},
                                schema=MARKT_SCHEMA, max_tokens=UMWANDLUNG_MAX_TOKENS)
        dauer += int(j.get("dauer_ms") or 0)
        for k, v in (j.get("usage") or {}).items():
            usage[k] = int(usage.get(k) or 0) + int(v or 0)
        if j.get("status") != "ok" or not isinstance(j.get("daten"), dict):
            grund = j.get("grund") or "Umwandlung fehlgeschlagen"
            fehler.append(titel)
            log.warning("Markttabelle: Gruppe %r nicht umgewandelt: %s", titel, grund)
            continue
        roh.extend(p for p in (j["daten"].get("positionen") or []) if isinstance(p, dict))
        z = str(j["daten"].get("zusammenfassung") or "").strip()
        if z:
            zusammenfassungen.append(z)
    gesehen = set()
    eindeutig: List[dict] = []
    ok_gruppen = len(gruppen_texte) - len(fehler)
    for p in roh:
        key = (str(p.get("typ") or "").strip().lower(), str(p.get("auspraegung") or "").strip().lower())
        if key in gesehen:
            continue
        gesehen.add(key)
        eindeutig.append(p)
    return {"status": "ok" if ok_gruppen > 0 else "fehler", "grund": grund,
            "daten": {"positionen": eindeutig, "zusammenfassung": " ".join(zusammenfassungen)},
            "usage": usage, "dauer_ms": dauer, "fehlgeschlagen": fehler}


async def aktualisieren(db=None, *, erzwingen: bool = False) -> dict:
    """Recherche (Websuche, drei Gruppen) + Umwandlung in die Tabelle; Ablage.
    Eine leere Tabelle ist ein Fehlversuch. Wirft nie."""
    db = db if db is not None else _db
    if not aktiv() or not ki_aktiv():
        return {"status": "aus", "grund": "Marktanalyse oder KI nicht aktiv"}
    vorhanden = await aktuell(db)
    if vorhanden and frisch(vorhanden) and not erzwingen:
        return {**(_oeffentlich(vorhanden) or {}), "aktualisiert": False}
    jetzt = now_iso()
    try:
        texte: List[str] = []
        quellen: Dict[str, str] = {}
        usage_r: Dict[str, int] = {}
        suchen = 0
        dauer = 0
        letzter_status, letzter_grund = "fehler", "keine Antwort"
        gruppen_texte: List[tuple] = []
        for titel, positionen in _gruppen():
            r = await recherche(system=RECHERCHE_SYSTEM, frage=_frage_tabelle(titel, positionen),
                                max_suchen=MAX_SUCHEN_TABELLE)
            letzter_status, letzter_grund = r.get("status") or "fehler", r.get("grund") or ""
            suchen += int(r.get("suchen") or 0)
            dauer += int(r.get("dauer_ms") or 0)
            for k, v in (r.get("usage") or {}).items():
                usage_r[k] = int(usage_r.get(k) or 0) + int(v or 0)
            if r.get("status") == "ok" and (r.get("text") or "").strip():
                texte.append(f"## {titel}\n" + r["text"].strip())
                gruppen_texte.append((titel, r["text"].strip(), positionen))
                for q in r.get("quellen") or []:
                    if q.get("url"):
                        quellen.setdefault(q["url"], q.get("titel") or "")
        if not texte:
            eintrag = {"_id": DOK_ID, "stand_versuch": jetzt, "status": letzter_status or "fehler",
                       "grund": letzter_grund or "keine Antwort"}
            await db[SAMMLUNG].update_one({"_id": DOK_ID}, {"$set": eintrag}, upsert=True)
            await _alarm(db, eintrag["status"], eintrag["grund"])
            return {"status": eintrag["status"], "grund": eintrag["grund"], "aktualisiert": False}
        bericht = "\n\n".join(texte)
        # Betrieb 26.09.2026: EIN Umwandlungs-Aufruf fuer vier Gruppen wurde bei
        # max_tokens abgeschnitten (Alarm ki_marktdaten_fehlgeschlagen). Jetzt je Gruppe
        # ein Aufruf mit nur deren Positionen und hoeherer Grenze; scheitert eine
        # Gruppe, bleibt die Tabelle der anderen erhalten.
        j = await _umwandeln_je_gruppe(gruppen_texte)
        if j.get("status") != "ok" or not isinstance(j.get("daten"), dict):
            grund = j.get("grund") or "Umwandlung fehlgeschlagen"
            await db[SAMMLUNG].update_one({"_id": DOK_ID}, {"$set": {"stand_versuch": jetzt, "status": "fehler", "grund": grund}},
                                          upsert=True)
            await _alarm(db, "fehler", grund)
            return {"status": "fehler", "grund": grund, "aktualisiert": False}
        positionen = _positionen_bereinigen(j["daten"].get("positionen") or [])
        if not positionen:
            grund = "keine Preisangaben gefunden"
            await db[SAMMLUNG].update_one({"_id": DOK_ID},
                                          {"$set": {"stand_versuch": jetzt, "status": "fehler", "grund": grund,
                                                    "bericht": bericht[:BERICHT_MAX]}}, upsert=True)
            await _alarm(db, "fehler", grund)
            return {"status": "fehler", "grund": grund, "aktualisiert": False, "suchen": suchen}
        usage = dict(usage_r)
        for k, v in (j.get("usage") or {}).items():
            usage[k] = int(usage.get(k) or 0) + int(v or 0)
        doc = {"_id": DOK_ID, "stand": jetzt, "status": "ok", "grund": "", "positionen": positionen,
               "quellen": [{"url": u, "titel": t} for u, t in list(quellen.items())[:20]],
               "bericht": bericht[:BERICHT_MAX],
               "zusammenfassung": str(j["daten"].get("zusammenfassung") or "")[:600],
               "modell": ki_modell(), "suchen": suchen, "gruppen_fehler": list(j.get("fehlgeschlagen") or []),
               "dauer_ms": dauer + int(j.get("dauer_ms") or 0), "usage": usage}
        zaehler = await db[SAMMLUNG].find_one({"_id": DOK_ID}, {"_id": 0, "laeufe_monat": 1, "laeufe_n": 1}) or {}
        doc.update({k: zaehler[k] for k in ("laeufe_monat", "laeufe_n") if k in zaehler})
        await db[SAMMLUNG].replace_one({"_id": DOK_ID}, doc, upsert=True)
        try:
            import betrieb
            await betrieb.alarm_schliessen(db, "ki_marktdaten_fehlgeschlagen", ref=DOK_ID)
        except Exception:  # noqa: BLE001
            pass
        return {**(_oeffentlich(doc) or {}), "aktualisiert": True}
    except Exception as exc:  # noqa: BLE001
        log.exception("Marktdaten nicht aktualisiert")
        grund = f"{type(exc).__name__}: {str(exc)[:200]}"
        # 30.09.2026: den Fehlversuch vermerken — ohne stand_versuch startete der stuendliche Aufraeumlauf
        # die ganze (bezahlte) Recherche jede Stunde neu.
        try:
            await db[SAMMLUNG].update_one({"_id": DOK_ID}, {"$set": {"stand_versuch": jetzt, "status": "fehler",
                                                                     "grund": grund}}, upsert=True)
        except Exception:  # noqa: BLE001
            pass
        return {"status": "fehler", "grund": grund, "aktualisiert": False}


async def _alarm(db, status: str, grund: str) -> None:
    try:
        import betrieb
        await betrieb.alarm(db, "ki_marktdaten_fehlgeschlagen", ref=DOK_ID, status=status, grund=grund[:200])
    except Exception:  # noqa: BLE001
        pass


async def pruefen_und_aktualisieren(db=None) -> dict:
    """Schritt im stuendlichen Aufraeumlauf: nur, wenn keine frische Tabelle da ist."""
    db = db if db is not None else _db
    if not aktiv() or not ki_aktiv():
        return {"status": "aus"}
    doc = await aktuell(db)
    if doc and frisch(doc):
        return {"status": "frisch", "alter_tage": alter_tage(doc)}
    versuch = (doc or {}).get("stand_versuch")
    if versuch and (doc or {}).get("status") != "ok":
        try:
            v = datetime.fromisoformat(str(versuch).replace("Z", "+00:00"))
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - v).total_seconds() < 6 * 3600:
                return {"status": "wartet", "grund": (doc or {}).get("grund") or ""}
        except ValueError:
            pass
    # Pruefliste 30.09.2026 (Nr. 10): derselbe Merker wie beim Knopf "Marktdaten jetzt" — vorher lief der
    # stuendliche Weg am Merker vorbei, und ein Klick waehrend des Laufs (oder umgekehrt) recherchierte die
    # ganze Tabelle doppelt.
    if not await lauf_markieren(db):
        return {"status": "laeuft"}
    try:
        if not await lauf_zaehlen(db):
            return await _limit_melden(db)
        erg = await aktualisieren(db, erzwingen=True)
    finally:
        await lauf_beenden(db)
    return {"status": erg.get("status"), "aktualisiert": erg.get("aktualisiert"), "grund": erg.get("grund") or ""}


def als_text(doc: Optional[dict]) -> str:
    """Zusatz fuer den System-Prompt beider Bewertungen."""
    if not doc or doc.get("status") != "ok" or not doc.get("positionen"):
        return ""
    stand = str(doc.get("stand") or "")[:10]
    zeilen = [f"Aktuelle Marktpreise (recherchiert am {stand}, Deutschland inkl. MwSt.; die repair_reference je "
              "Position ist daraus bzw. aus eigenen Daten abgeleitet und geht vor):"]
    for p in doc["positionen"]:
        q = f" — Quelle {p['quelle']}" if p.get("quelle") else ""
        h = f" ({p['hinweis']})" if p.get("hinweis") else ""
        zeilen.append(f"- {p['typ']} / {p['auspraegung']}: {int(p['min_eur'])}-{int(p['max_eur'])} EUR, "
                      f"typisch {int(p['typisch_eur'])}{h}{q}")
    return "\n".join(zeilen)


async def prompt_zusatz(db=None) -> str:
    try:
        return als_text(await aktuell(db))
    except Exception:  # noqa: BLE001
        return ""


# ------------------------------------------------ eigene Preisdatenbank
def _alter_klasse(v: dict) -> str:
    a = (v or {}).get("age_years")
    try:
        a = float(a)
    except (TypeError, ValueError):
        return "?"
    return "0-3" if a < 3 else "3-7" if a < 7 else "7-12" if a < 12 else "12+"


def _marke(v: dict) -> str:
    return str((v or {}).get("make") or "").strip().lower()[:40]


def _positionen(paket: Dict[str, Any], art: str) -> List[dict]:
    """Alle bewertbaren Positionen mit Referenzschluessel: Schaeden + Abweichungen."""
    raus = []
    schaeden = paket.get("damages") if art == "vertrag" else [d for d in (paket.get("new_damages") or [])
                                                             if not d.get("already_known")]
    for d in schaeden or []:
        raus.append(d)
    for a in paket.get("deviations") or []:
        if a.get("repair_reference"):
            raus.append(a)
    return raus


def _netto(quelle: str) -> bool:
    q = str(quelle or "").lower()
    return ("netto" in q or "ohne mwst" in q or "ohne mehrwertsteuer" in q or "zzgl" in q) and "brutto" not in q


def _passung(wert: str) -> Optional[bool]:
    """Siebte Spalte: True = ungefaehrer Orientierungswert, False = genau, None = nicht angegeben."""
    w = str(wert or "").strip().lower()
    if not w:
        return None
    if w.startswith(("ungef", "orient", "ca", "circa", "naeher", "näher")):
        return True
    return False if w.startswith("genau") else None


def _daten_parsen(text: str) -> List[Dict[str, Any]]:
    """Zeilen nach ###DATEN: id|min|max|typisch|quelle|url[|passung]."""
    if not text or DATEN_MARKER not in text:
        return []
    block = text.split(DATEN_MARKER, 1)[1]
    raus = []
    for zeile in block.splitlines():
        teile = [t.strip() for t in zeile.strip().strip("|").split("|")]
        if len(teile) < 4:
            continue
        pid = teile[0].strip("`* ")
        lo, hi, ty = _zahl(teile[1]), _zahl(teile[2]), _zahl(teile[3])
        if not pid or hi <= 0:
            continue
        if lo > hi:
            lo, hi = hi, lo
        ty = min(max(ty, lo), hi) if ty else round((lo + hi) / 2, 2)
        quelle = (teile[4] if len(teile) > 4 else "")[:80]
        # Schadenkatalog 25.09.2026: DEKRA & Co. nennen Netto-Werte — sonst
        # lernt die eigene Datenbank 19 % zu wenig.
        if _netto(quelle):
            lo, hi, ty = (round(x * 1.19, 2) for x in (lo, hi, ty))
            quelle = (quelle + " (auf brutto umgerechnet)")[:80]
        zeile_neu = {"id": pid[:120], "min_eur": lo, "max_eur": hi, "typisch_eur": ty,
                     "quelle": quelle,
                     "url": (teile[5] if len(teile) > 5 else "")[:300]}
        if len(teile) > 6 and _passung(teile[6]) is not None:
            zeile_neu["ungefaehr"] = bool(_passung(teile[6]))
        raus.append(zeile_neu)
    return raus[:20]


# Review 25.09.2026 abends: gelernt wird nur aus bekannten Quellen und nur,
# wenn der Wert plausibel zur Referenz passt — sonst verfaelscht ein
# schlechter Webwert die eigene Datenbank fuer 180 Tage.
# Pruefliste 30.09.2026: die Quellenpruefung liegt in ai.quellen — exakte Domain statt Teilstring, und die
# Adresse muss in diesem Lauf wirklich unter den Suchtreffern gewesen sein.
VERTRAUTE_DOMAINS = _quellen.VERTRAUTE_DOMAINS
PLAUSIBEL_UNTEN, PLAUSIBEL_OBEN = 0.25, 4.0


def quelle_vertraut(quelle: str, url: str, belegt=None) -> bool:
    """Bekannte Domain (exakt oder Unterdomain). Der Quellenname allein genuegt nicht mehr; mit `belegt`
    (Rechnernamen der echten Suchtreffer) muss die Zeile darauf gestuetzt sein."""
    return _quellen.quelle_vertraut(quelle, url, belegt)


# Review 25.09.2026 abends: gelernt wird nur aus bekannten Quellen und nur,
# wenn der Wert plausibel zur Referenz passt — sonst verfaelscht ein
# schlechter Webwert die eigene Datenbank fuer 180 Tage.

def wert_plausibel(zeile: Dict[str, Any], ref: Dict[str, Any]) -> bool:
    """Innerhalb 0,25x der unteren bis 4x der oberen Referenz; min > 0."""
    try:
        lo, hi = float(zeile.get("min_eur") or 0), float(zeile.get("max_eur") or 0)
        r_lo, r_hi = float(ref.get("low") or 0), float(ref.get("high") or 0)
    except (TypeError, ValueError):
        return False
    if lo <= 0 or hi <= 0 or hi > 50000:
        return False
    if r_lo <= 0 or r_hi <= 0:
        return True
    return lo >= r_lo * PLAUSIBEL_UNTEN and hi <= r_hi * PLAUSIBEL_OBEN


def fall_hosts(fall: Optional[dict]) -> List[str]:
    """Rechnernamen der echten Suchtreffer/Zitate dieses Laufs (ai.provider liefert `hosts`;
    sonst aus den Quellen abgeleitet)."""
    if not fall:
        return []
    hosts = {h for h in (fall.get("hosts") or []) if h}
    hosts |= {_quellen.host_von(q.get("url")) for q in (fall.get("quellen") or []) if isinstance(q, dict)}
    hosts.discard("")
    return sorted(hosts)


def recherche_pruefen(fall: Optional[dict], paket: Dict[str, Any], art: str) -> List[Dict[str, Any]]:
    """Pruefliste 30.09.2026 (Nr. 11-13, 22): JEDE Zeile des ###DATEN-Blocks wird geprueft, bevor sie die
    aktuelle Bewertung beeinflusst oder gelernt wird:

      belegt    — die Adresse (oder der Quellenname als ganzes Wort) gehoert zu einem echten Suchtreffer
                  dieses Laufs; das Modell kann keine Quelle mehr dazuschreiben,
      vertraut  — der belegte Rechnername ist eine bekannte Domain (exakt, kein Teilstring),
      plausibel — der Wert liegt zwischen 0,25x der unteren und 4x der oberen Referenz,
      ungefaehr — das Modell hat den Wert selbst als Orientierungswert markiert.

    Das Ergebnis steht in fall["pruefung"]; "ok" = belegt, vertraut und plausibel. Wirft nie."""
    if not fall or fall.get("status") != "ok":
        return []
    if isinstance(fall.get("pruefung"), list):
        return fall["pruefung"]
    raus: List[Dict[str, Any]] = []
    try:
        je_id = {str(p.get("id")): p for p in _positionen(paket, art)}
        hosts = fall_hosts(fall)
        for z in _daten_parsen(fall.get("text") or ""):
            p = je_id.get(z["id"])
            ref = (p or {}).get("repair_reference") or {}
            if not p or not ref.get("key"):
                continue
            host = _quellen.belegter_host(z.get("quelle"), z.get("url"), hosts)
            belegt, vertraut, plausibel = bool(host), _quellen.host_vertraut(host), wert_plausibel(z, ref)
            raus.append({"id": z["id"], "min_eur": z["min_eur"], "max_eur": z["max_eur"], "typisch_eur": z["typisch_eur"],
                         "quelle": z.get("quelle") or "", "url": z.get("url") or "", "host": host,
                         "ungefaehr": bool(z.get("ungefaehr")), "belegt": belegt, "vertraut": vertraut,
                         "plausibel": plausibel, "ok": belegt and vertraut and plausibel})
    except Exception:  # noqa: BLE001
        log.exception("Recherche-Werte nicht pruefbar")
        raus = []
    fall["pruefung"] = raus
    return raus


def recherche_anwenden(paket: Dict[str, Any], art: str, fall: Optional[dict]) -> int:
    """Die GEPRUEFTEN Werte dieses Laufs als Referenz der Position setzen (Quelle "Websuche …"). Vorher
    gelangte der Webwert nur ueber die eigene Datenbank in die Referenz — auch ein einzelner, ungeprueft
    gelernter Wert. Kostenvoranschlag und Fachpruefung bleiben unberuehrt. Liefert die Anzahl."""
    ok = [z for z in ((fall or {}).get("pruefung") or []) if z.get("ok")]
    if not ok:
        return 0
    je_id: Dict[str, List[dict]] = {}
    for z in ok:
        je_id.setdefault(z["id"], []).append(z)
    n = 0
    for p in _positionen(paket, art):
        zeilen = je_id.get(str(p.get("id")))
        ref = p.get("repair_reference")
        if not zeilen or not ref or ref.get("manual_review") or ref.get("basis") == "kostenvoranschlag":
            continue
        genau = [z for z in zeilen if not z.get("ungefaehr")] or zeilen
        namen = sorted({_quellen.stamm_domain(z["host"]) for z in genau if z.get("host")})[:3]
        ref.update(low=round(min(z["min_eur"] for z in genau)),
                   median=round(statistics.median(z["typisch_eur"] for z in genau)),
                   high=round(max(z["max_eur"] for z in genau)),
                   source=f"Websuche ({', '.join(namen)})", web_geprueft=True,
                   approximate=all(bool(z.get("ungefaehr")) for z in genau))
        ref.pop("own_data_n", None)
        n += 1
    if n:
        from ai import kontext
        paket["precomputed"] = kontext.vorberechnet(paket)
    return n


def _fingerabdruck(key: str, quelle_id: str, z: Dict[str, Any]) -> str:
    """Dieselbe Aussage derselben Quelle (Nr. 14/30): Schluessel + Quelle + Preisband."""
    return f"{key}|{quelle_id}|{round(float(z['min_eur']))}|{round(float(z['max_eur']))}|{round(float(z['typisch_eur']))}"


async def lernen_aus_recherche(fall: Optional[dict], paket: Dict[str, Any], art: str, db=None) -> int:
    """Gepruefte Werte je Position in ki_reparaturpreise ablegen. Wirft nie.

    Pruefliste 30.09.2026: nur Zeilen, die recherche_pruefen bestanden haben (belegt, vertraut, plausibel);
    dieselbe Aussage derselben Quelle fuer dasselbe Fahrzeugsegment wird nur EINMAL gespeichert
    (Fingerabdruck) — vorher zaehlte ein ADAC-Wert nach 20 aehnlichen Autos wie 20 Beobachtungen."""
    if not fall or fall.get("status") != "ok":
        return 0
    db = db if db is not None else _db
    try:
        zeilen = recherche_pruefen(fall, paket, art)
        if not zeilen:
            return 0
        je_id = {str(p.get("id")): p for p in _positionen(paket, art)}
        v = paket.get("vehicle") or {}
        jetzt = now_iso()
        seit = (datetime.now(timezone.utc) - timedelta(days=EIGENE_TAGE)).isoformat()
        marke, modell, alter = _marke(v), str(v.get("model") or "")[:60], _alter_klasse(v)
        docs = []
        gesehen: set = set()
        verworfen: List[Dict[str, Any]] = []
        for z in zeilen:
            p = je_id.get(z["id"]) or {}
            key = (p.get("repair_reference") or {}).get("key")
            if not key:
                continue
            if not z.get("ok"):
                verworfen.append({"id": z["id"], "quelle": z.get("quelle"), "url": z.get("url"),
                                  "min_eur": z["min_eur"], "max_eur": z["max_eur"],
                                  "grund": ("nicht belegt" if not z.get("belegt") else
                                            "Quelle unbekannt" if not z.get("vertraut") else "unplausibel")})
                continue
            quelle_id = _quellen.stamm_domain(z["host"])
            fp = _fingerabdruck(key, quelle_id, z)
            if fp in gesehen:
                continue
            gesehen.add(fp)
            if await db[PREIS_SAMMLUNG].find_one({"fp": fp, "marke": marke, "modell": modell, "alter_klasse": alter,
                                                  "stand": {"$gte": seit}}, {"_id": 1}):
                continue                                # diese Aussage kennt die Datenbank schon
            docs.append({"key": key, "typ": p.get("type") or p.get("damage_type") or key.split("_")[0],
                         "zone": str(p.get("zone") or "")[:80], "marke": marke, "modell": modell,
                         "alter_klasse": alter, "min_eur": z["min_eur"], "max_eur": z["max_eur"],
                         "typisch_eur": z["typisch_eur"], "quelle": z["quelle"], "url": z["url"],
                         "host": quelle_id, "fp": fp, "ungefaehr": bool(z.get("ungefaehr")),
                         "art": art, "stand": jetzt})
        if docs:
            await db[PREIS_SAMMLUNG].insert_many(docs)
        if verworfen:
            fall["verworfen"] = verworfen[:20]
            log.info("Recherche: %d Werte nicht gelernt (nicht belegt, Quelle unbekannt oder unplausibel)",
                     len(verworfen))
        return len(docs)
    except Exception:  # noqa: BLE001
        log.exception("Recherche-Werte nicht gelernt")
        return 0


def _modell_norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _quelle_norm(s) -> str:
    """Quellenname ohne Zusaetze ('ADAC, netto 2024' -> 'adac')."""
    return re.split(r"[,(;/]", str(s or "").strip().lower())[0].strip()


def _quelle_id(d: dict) -> str:
    """Wer hat den Wert geliefert? Die Domain (neu gelernte Werte), sonst der Quellenname (Altbestand)."""
    return str(d.get("host") or "").strip().lower() or _quelle_norm(d.get("quelle"))


def _quellen_anzahl(docs: List[dict]) -> int:
    return len({_quelle_id(d) for d in docs if _quelle_id(d)})


def _belege(docs: List[dict]) -> List[dict]:
    """Je Quelle und Preisband EIN Beleg (Nr. 14/16/30): zwanzigmal derselbe Wert derselben Quelle ist
    eine Beobachtung, nicht zwanzig. Die Reihenfolge (neueste zuerst) bleibt."""
    gesehen, raus = set(), []
    for d in docs:
        try:
            k = (_quelle_id(d), round(float(d["min_eur"])), round(float(d["max_eur"])), round(float(d["typisch_eur"])))
        except (KeyError, TypeError, ValueError):
            continue
        if k in gesehen:
            continue
        gesehen.add(k)
        raus.append(d)
    return raus


def _median_je_quelle(belege: List[dict], feld: str) -> float:
    """Erst der Median je Quelle, dann der Median ueber die Quellen (Nr. 16) — eine Quelle mit vielen
    Werten ueberstimmt die anderen nicht mehr."""
    je: Dict[str, List[float]] = {}
    for d in belege:
        je.setdefault(_quelle_id(d) or "?", []).append(float(d[feld]))
    return statistics.median(statistics.median(w) for w in je.values())


#: Pruefliste 30.09.2026 (Nr. 19): bei diesen Arten haengt der Preis an Marke und Technik — ein Wert von
#: irgendeinem Auto ("alle") ist keine Grundlage (Getriebe, Scheinwerfer, Schluessel, Warnleuchte).
MARKENGEBUNDENE_TYPEN = frozenset({"technical", "warning_light", "beleuchtung", "keys"})
EIGENE_JE_SCHLUESSEL_MAX = 300


def _markengebunden(key: str) -> bool:
    return str((preisbasis.zeile(key) or {}).get("typ") or "") in MARKENGEBUNDENE_TYPEN or str(key).startswith("technik_")


async def eigene_referenzen(paket: Dict[str, Any], art: str, db=None) -> Dict[str, Dict[str, Any]]:
    """Je Referenzschluessel die Statistik der eigenen frischen Werte.

    Review 26.09.2026 (Nr. 19/20): Auswahl in Stufen — (1) gleiche Marke,
    gleiches Modell und gleiche Altersklasse, (2) Marke und Altersklasse,
    (3) Marke, (4) alle. Die erste Stufe mit mindestens EIGENE_MIN Belegen
    aus mindestens EIGENE_QUELLEN_MIN verschiedenen Quellen gewinnt
    ("reicht": True -> keine Websuche mehr). Reicht keine Stufe, liefert
    die engste nicht leere Stufe einen Anhalt ("reicht": False, es wird
    weiter gesucht; kontext.eigene_anwenden setzt ihn NICHT als Referenz).
    {} wenn nichts da. Wirft nie.

    Pruefliste 30.09.2026: gezaehlt werden BELEGE (je Quelle und Preisband
    einer), ungefaehre Orientierungswerte zaehlen fuer "reicht" nicht mit,
    die Werte sind Mediane ueber die Quellen, je Schluessel wird einzeln
    gelesen (kein gemeinsames Limit), und bei markengebundenen Arten gibt
    es keine Stufe "alle"."""
    db = db if db is not None else _db
    raus: Dict[str, Dict[str, Any]] = {}
    try:
        keys = {(p.get("repair_reference") or {}).get("key") for p in _positionen(paket, art)}
        keys.discard(None)
        if not keys:
            return raus
        v = paket.get("vehicle") or {}
        marke, modell, alter = _marke(v), _modell_norm(v.get("model")), _alter_klasse(v)
        seit = (datetime.now(timezone.utc) - timedelta(days=EIGENE_TAGE)).isoformat()
        for key in sorted(keys):
            docs = await db[PREIS_SAMMLUNG].find({"key": key, "stand": {"$gte": seit}}, {"_id": 0}) \
                .sort("stand", -1).limit(EIGENE_JE_SCHLUESSEL_MAX).to_list(EIGENE_JE_SCHLUESSEL_MAX)
            if not docs:
                continue
            mit_marke = [d for d in docs if marke and d.get("marke") == marke]
            mit_alter = [d for d in mit_marke if alter != "?" and d.get("alter_klasse") == alter]
            mit_modell = [d for d in mit_alter if modell and _modell_norm(d.get("modell")) == modell]
            stufen = [(s, _belege(b)) for s, b in zip(EIGENE_STUFEN, (mit_modell, mit_alter, mit_marke, docs))]
            if _markengebunden(key):
                stufen = [(s, b) for s, b in stufen if s != "alle"]

            def _genug(b: List[dict]) -> bool:
                fest = [d for d in b if not d.get("ungefaehr")]
                return len(fest) >= EIGENE_MIN and _quellen_anzahl(fest) >= EIGENE_QUELLEN_MIN
            gewinner = next(((s, b) for s, b in stufen if _genug(b)), None)
            reicht = gewinner is not None
            if gewinner is None:
                gewinner = next(((s, b) for s, b in stufen if b), None)
            if gewinner is None:
                continue
            stufe, basis = gewinner
            basis = basis[:30]
            if reicht:
                basis = [d for d in basis if not d.get("ungefaehr")] or basis
            namen = sorted({d.get("quelle") for d in basis if d.get("quelle")})[:4]
            raus[key] = {"n": len(basis), "low": round(_median_je_quelle(basis, "min_eur")),
                         "median": round(_median_je_quelle(basis, "typisch_eur")),
                         "high": round(_median_je_quelle(basis, "max_eur")),
                         "stufe": stufe, "reicht": reicht, "quellen_n": _quellen_anzahl(basis),
                         "nur_marke": stufe != "alle",
                         "source": f"eigene Datenbank (n={len(basis)}{', ' + marke if stufe != 'alle' else ''}"
                                   f"{'; ' + ', '.join(namen) if namen else ''})"}
    except Exception:  # noqa: BLE001
        log.exception("eigene Referenzen nicht ladbar")
    return raus


def recherche_noetig(paket: Dict[str, Any], art: str, eigene: Dict[str, Dict[str, Any]]) -> List[dict]:
    """Positionen, fuer die eigene Daten NICHT reichen (dafuer wird gesucht)."""
    offen = []
    for p in _positionen(paket, art):
        ref = p.get("repair_reference") or {}
        if ref.get("manual_review"):
            continue
        e = eigene.get(ref.get("key") or "")
        if e and e.get("reicht"):
            continue
        offen.append(p)
    return offen


def _referenz_median(p: dict) -> float:
    try:
        return float((p.get("repair_reference") or {}).get("median") or 0)
    except (TypeError, ValueError):
        return 0.0


def recherche_auswahl(positionen: List[dict]) -> List[dict]:
    """Review 26.09.2026 (Nr. 21): hoechstens RECHERCHE_POSITIONEN_MAX
    Positionen je Recherche — sortiert nach erwartetem Betrag (Median der
    Referenz, absteigend), damit die teuersten zuerst kommen."""
    return sorted(positionen, key=_referenz_median, reverse=True)[:RECHERCHE_POSITIONEN_MAX]


# ------------------------------------------------ Recherche je Fall
# Review 26.09.2026 (Nr. 130-132): Die Recherchefrage entsteht NUR aus
# strukturierten Feldern — Art, Bauteil, die festen Merkmale des Formulars,
# Modell, Baujahr. Kein Freitext des Fahrers (note): er ginge sonst als
# Suchanweisung ins Netz, und die Treffer landeten in der eigenen
# Preisdatenbank. Merkmale nur aus dieser Liste, Werte gekuerzt und einzeilig.
_FRAGE_MERKMALE = ("groesse", "laenge", "tiefe", "lack", "lage", "anzahl", "umfang", "stelle", "dellengroesse",
                   "wo", "welches", "funktion", "technik", "nachweis", "qualitaet", "fahrbereit", "airbag",
                   "bereich", "status", "warnleuchte", "kva")
_SCHADEN_ARTEN = ("delle", "kratzer", "rost", "steinschlag", "hagelschaden", "beleuchtung", "unfall_repariert",
                  "unfall_nicht_repariert", "technik")


def _einzeilig(w: Any, n: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+|\s+", " ", str(w or "")).strip()[:n]


def _fall_frage(art: str, paket: Dict[str, Any], positionen: List[dict], suchen: int = MAX_SUCHEN_FALL) -> str:
    v = paket.get("vehicle") or {}
    auto = " ".join(_einzeilig(x, 60) for x in (v.get("make"), v.get("model"), v.get("variant")) if x).strip()
    ez = _einzeilig(v.get("first_registration"), 10)
    km = v.get("mileage_pickup_km") or v.get("mileage_contract_km") or v.get("mileage_km")
    zeilen = []
    for p in recherche_auswahl(positionen):
        sd = p.get("severity_data") or {}
        merk = ", ".join(f"{k} {_einzeilig(sd.get(k), 40)}" for k in _FRAGE_MERKMALE
                         if str(sd.get(k) or "").strip() and str(sd.get(k)).lower() != "unbekannt")
        typ = p.get("damage_type") or p.get("type")
        if typ in _SCHADEN_ARTEN:
            zeilen.append(f"- id {p.get('id')}: {_einzeilig(p.get('label') or typ, 60)} {_einzeilig(p.get('zone'), 60)}"
                          f"{(' (' + merk + ')') if merk else ''}")
        else:
            zeilen.append(f"- id {p.get('id')}: {_einzeilig(p.get('label'), 60)}: erwartet "
                          f"{_einzeilig(p.get('expected'), 60)}, vor Ort {_einzeilig(p.get('actual'), 60)}")
    if not zeilen:
        return ""
    return (f"Fahrzeug: {auto}, Erstzulassung {ez}, {km or '?'} km. Recherchiere aktuelle Reparatur-/Ersatzkosten "
            "(Deutschland, inkl. MwSt.) fuer genau diese Punkte; nutze je Punkt die passenden Quellen (Karosserie: "
            "ADAC/ATU/FairGarage; Scheiben: Carglass; Technik: FairGarage, Autobutler, Bosch Car Service; "
            "Schluessel/Teile: Markenangaben). Je Punkt: Spanne, typischer Wert, Quelle. Knapp antworten:\n"
            + "\n".join(zeilen) + "\n\n" + SUCH_ANWEISUNG.format(n=int(suchen)) + "\n" + DATEN_ANWEISUNG)


async def fall_recherche(art: str, paket: Dict[str, Any], *, sparmodus: bool = False,
                         eigene: Optional[Dict[str, Dict[str, Any]]] = None, kasse=None) -> Optional[dict]:
    """Gezielte Websuche zu den Positionen, fuer die eigene Daten nicht reichen.
    None, wenn aus, Sparmodus oder nichts zu suchen; sonst {text, quellen,
    suchen, dauer_ms, usage, status, modell}.

    Kostendeckel (27.09.2026): mit `kasse` (ai.kostenkasse, die Bewertung ist
    dort schon eingeplant) laeuft die Websuche in RUNDEN mit je einer Suche
    (Nachbesserung 27.09.2026, siehe _fall_runden) — jede Runde nur, wenn
    ihre erwarteten Kosten in den Rest bis zum ZIEL und ihre Obergrenze in
    den Rest bis HART passen. Reicht der Rest nicht einmal fuer die erste,
    entfaellt die Websuche (status "kostendeckel", Hinweis wie im Sparmodus)."""
    if sparmodus or not aktiv() or not je_fall(art) or not ki_aktiv():
        return None
    offen = recherche_noetig(paket, art, eigene or {})
    if not offen:
        return None
    frage = _fall_frage(art, paket, offen)
    if not frage:
        return None
    modell = ki_recherche_modell()
    if kasse is not None:
        return await _fall_runden(art, paket, offen, kasse, modell)
    try:
        r = await recherche(system=RECHERCHE_SYSTEM, frage=frage, max_suchen=MAX_SUCHEN_FALL)
    except LaufUebernommen:
        raise                            # Lauf gehoert einem anderen Aufruf: nichts mehr bezahlen
    except Exception:  # noqa: BLE001
        log.exception("Fall-Recherche gescheitert")
        return None
    # Pruefung 27.09.2026 (F2): das Recherche-Modell steht IMMER im Ergebnis —
    # sonst rechnete die Kostenschaetzung Haiku-Tokens zum Sonnet-Preis.
    modell = r.get("modell") or modell
    if r.get("status") != "ok" or not (r.get("text") or "").strip():
        return {"text": "", "quellen": [], "suchen": int(r.get("suchen") or 0), "dauer_ms": r.get("dauer_ms"),
                "usage": r.get("usage") or {}, "status": r.get("status"), "modell": modell}
    return {"text": (r.get("text") or "")[:FALL_RECHERCHE_TEXT_MAX], "quellen": list(r.get("quellen") or [])[:10],
            "hosts": fall_hosts({"hosts": r.get("hosts"), "quellen": r.get("quellen")}),
            "suchen": int(r.get("suchen") or 0), "dauer_ms": r.get("dauer_ms"), "usage": r.get("usage") or {},
            "modell": modell, "status": "ok",
            "positionen": [str(p.get("id")) for p in recherche_auswahl(offen)]}


HINWEIS_RUNDE_ENTFALLEN = "Weitere Websuche entfallen — Kostendeckel je Lauf"


async def _fall_runden(art: str, paket: Dict[str, Any], offen: List[dict], kasse, modell: str) -> dict:
    """Websuche unter dem Kostendeckel (Nachbesserung 27.09.2026): bis zu
    MAX_SUCHEN_FALL Runden mit je EINER Suche statt einer Anfrage mit zwei.

    Warum: in einer Anfrage liest die Server-Schleife jedes Suchergebnis in
    jeder weiteren Runde erneut — bei zwei Suchen zaehlt ein Ergebnis bis zu
    dreifach (n(n+1)/2). Ein groesser als erwartetes Ergebnis sprengte so die
    harte Grenze schon in der Recherche (Eigenschaftstest: 4x
    SUCHE_TOKENS_MAX -> 34 ct). In getrennten Runden zaehlt es einfach, und
    die zweite Runde wird erst nach den ECHTEN Kosten der ersten geplant.
    Runde 2 sucht nur noch fuer die Positionen, zu denen Runde 1 keinen Wert
    im ###DATEN-Block geliefert hat."""
    from ai import kostenkasse
    rest = list(recherche_auswahl(offen))
    runden: List[dict] = []
    for nr in range(1, MAX_SUCHEN_FALL + 1):
        if not rest:
            break
        frage = _fall_frage(art, paket, rest, suchen=1)
        basis = kasse.recherche_basis_tokens(RECHERCHE_SYSTEM, frage)
        plan = kasse.recherche_plan(basis, 1, modell)
        if plan is None:
            if not runden:
                kasse.hinweis(kostenkasse.HINWEIS_WEBSUCHE_ENTFALLEN)
                return {"text": "", "quellen": [], "suchen": 0, "dauer_ms": 0, "usage": {}, "modell": modell,
                        "status": "kostendeckel", "grund": kostenkasse.HINWEIS_WEBSUCHE_ENTFALLEN}
            kasse.hinweis(HINWEIS_RUNDE_ENTFALLEN)
            break
        try:
            r = await recherche(system=RECHERCHE_SYSTEM, frage=frage, max_suchen=1, max_tokens=plan["max_tokens"],
                                kasse=kasse, basis_tokens=basis, plan=plan, runde=nr)
        except LaufUebernommen:
            raise                        # Lauf gehoert einem anderen Aufruf: nichts mehr bezahlen
        except Exception:  # noqa: BLE001
            log.exception("Fall-Recherche (Runde %d) gescheitert", nr)
            break
        if not r.get("gebucht"):
            # Recherche ohne eigene Buchung (z. B. Attrappe): hier abrechnen
            kasse.recherche_buchen(r.get("modell") or modell, r.get("usage") or {}, plan, nr)
        runden.append(r)
        if r.get("status") != "ok" or not (r.get("text") or "").strip() or not int(r.get("suchen") or 0):
            break                        # Fehler, leer oder ohne Suche: keine weitere Runde
        gefunden = {z["id"] for z in _daten_parsen(r.get("text") or "")}
        rest = [p for p in rest if str(p.get("id")) not in gefunden]
    usage: Dict[str, int] = {}
    for r in runden:
        for k, v in (r.get("usage") or {}).items():
            usage[k] = int(usage.get(k) or 0) + int(v or 0)
    modell = next((r.get("modell") for r in runden if r.get("modell")), modell)
    dauer = sum(int(r.get("dauer_ms") or 0) for r in runden)
    suchen = sum(int(r.get("suchen") or 0) for r in runden)
    gut = [r for r in runden if r.get("status") == "ok" and (r.get("text") or "").strip()]
    if not gut:
        letzte = runden[-1] if runden else {}
        return {"text": "", "quellen": [], "suchen": suchen, "dauer_ms": dauer, "usage": usage,
                "status": letzte.get("status") or "fehler", "modell": modell}
    vor = [(r.get("text") or "").split(DATEN_MARKER, 1)[0].strip() for r in gut]
    # ein ###DATEN-Block fuer alle Runden; eine spaetere Runde liefert nur
    # Werte zu Positionen, die noch keinen hatten (sonst doppelt gelernt)
    daten: List[str] = []
    schon: set = set()
    for r in gut:
        t = r.get("text") or ""
        if DATEN_MARKER not in t:
            continue
        neu = set()
        for zeile in t.split(DATEN_MARKER, 1)[1].splitlines():
            pid = zeile.strip().strip("|").split("|", 1)[0].strip().strip("`* ")
            if zeile.strip() and pid not in schon:
                daten.append(zeile.strip())
                neu.add(pid)
        schon |= neu
    block = ("\n" + DATEN_MARKER + "\n" + "\n".join(daten))[:3000] if daten else ""
    text = "\n\n".join(v for v in vor if v)[:max(0, FALL_RECHERCHE_TEXT_MAX - len(block))].rstrip() + block
    quellen: Dict[str, dict] = {}
    hosts: set = set()
    for r in gut:
        for q in r.get("quellen") or []:
            quellen.setdefault(q.get("url"), q)
        hosts |= set(fall_hosts({"hosts": r.get("hosts"), "quellen": r.get("quellen")}))
    return {"text": text, "quellen": list(quellen.values())[:10], "hosts": sorted(hosts),
            "suchen": suchen, "dauer_ms": dauer,
            "usage": usage, "modell": modell, "status": "ok", "runden": len(runden),
            "positionen": [str(p.get("id")) for p in recherche_auswahl(offen)]}


def pruefung_als_text(fall: Optional[dict]) -> str:
    """Pruefliste 30.09.2026 (Nr. 11): die Bewertung erfaehrt, welche Webwerte die Pruefung bestanden haben
    — und welche nicht verwendet werden duerfen. Ohne Pruefung (alter Stand) leer."""
    zeilen = (fall or {}).get("pruefung")
    if not isinstance(zeilen, list) or not zeilen:
        return ""
    gut = [z for z in zeilen if z.get("ok")]
    rest = [z for z in zeilen if not z.get("ok")]
    teile = []
    if gut:
        teile.append("Gepruefte Werte (Quelle bekannt und im Suchergebnis belegt, Betrag plausibel): " + "; ".join(
            f"id {z['id']}: {round(z['min_eur'])}-{round(z['max_eur'])} EUR, typisch {round(z['typisch_eur'])} "
            f"({_quellen.stamm_domain(z['host'])}{', nur Orientierungswert' if z.get('ungefaehr') else ''})"
            for z in gut[:12]))
    if rest:
        teile.append("NICHT verwenden (Quelle nicht belegt, unbekannt oder Betrag unplausibel): " + "; ".join(
            f"id {z['id']}: {round(z['min_eur'])}-{round(z['max_eur'])} EUR ({str(z.get('quelle') or 'o. Q.')[:30]})"
            for z in rest[:8]) + " — fuer diese Positionen gelten Tabelle und Ausgangswerte.")
    return "\n".join(teile)[:1500] + "\n"


def fall_als_text(fall: Optional[dict], anteil: float = 1.0) -> str:
    """Recherche fuer den Prompt der Bewertung. `anteil` < 1 kuerzt den Text
    (Kostendeckel: Bewertung passt sonst nicht mehr in die harte Grenze);
    insgesamt nie laenger als FALL_TEXT_MAX_ZEICHEN (damit plant die Kasse)."""
    if not fall or not fall.get("text") or anteil <= 0:
        return ""
    text = fall["text"].split(DATEN_MARKER, 1)[0].strip()
    if anteil < 1:
        text = text[:int(len(text) * anteil)].rstrip() + " …"
    voll = ("Marktrecherche zu diesem Fall (Websuche, Quellen unten; geht vor Tabelle und "
            "Ausgangswerten):\n" + pruefung_als_text(fall) + text
            + ("\nQuellen: " + "; ".join(f"{q.get('titel') or ''} {q.get('url')}".strip() for q in fall.get("quellen") or [])
               if fall.get("quellen") else ""))
    return voll[:FALL_TEXT_MAX_ZEICHEN]


async def statistik_eigene(db=None) -> Dict[str, Any]:
    """Fuer die Betriebsseite: Umfang der eigenen Preisdatenbank."""
    db = db if db is not None else _db
    try:
        gesamt = await db[PREIS_SAMMLUNG].count_documents({})
        seit = (datetime.now(timezone.utc) - timedelta(days=EIGENE_TAGE)).isoformat()
        frisch_n = await db[PREIS_SAMMLUNG].count_documents({"stand": {"$gte": seit}})
        keys = await db[PREIS_SAMMLUNG].distinct("key", {"stand": {"$gte": seit}})
        return {"werte": gesamt, "frisch": frisch_n, "schluessel": len(keys), "min_je_schluessel": EIGENE_MIN,
                "tage": EIGENE_TAGE}
    except Exception:  # noqa: BLE001
        return {"werte": 0, "frisch": 0, "schluessel": 0, "min_je_schluessel": EIGENE_MIN, "tage": EIGENE_TAGE}
