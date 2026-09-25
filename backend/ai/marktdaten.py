# -*- coding: utf-8 -*-
"""Marktanalyse fuer die KI-Bewertung (Wunsch Ahmad 26.09.2026: "so genau wie
es geht, auf ADAC und Smart-Repair achten").

Zwei Bausteine:

1. **Markttabelle** (ki_marktdaten, ein Dokument "aktuell"): einmal je
   KI_MARKTDATEN_TAGE (Standard 30) recherchiert die KI per Websuche aktuelle
   Reparatur-/Smart-Repair-Preise fuer unsere Positionen (Delle, Kratzer,
   Scheibe, Schluessel, Reifen, HU ...) — bevorzugt ADAC und Smart-Repair-
   Anbieter — und legt sie mit Quellen ab. Die Tabelle geht als Zusatz in
   JEDE Bewertung (hat Vorrang vor den Startwerten in preisbasis.py).
   Ausgeloest vom stuendlichen Aufraeumlauf (nur wenn veraltet) und per
   Knopf auf /admin/betrieb.

2. **Recherche je Fall** (optional): vor der eigentlichen Bewertung sucht die
   KI gezielt zu den konkreten Schaeden dieses Fahrzeugs (hoechstens wenige
   Suchen) und bekommt das Ergebnis samt Quellen mit. Kostet ~1 ct je Suche
   und 20-40 s — deshalb Standard AN bei der Abholung (laeuft im
   Hintergrund) und AUS beim Vertrag (der Sucher wartet).

Websuche und unser festes JSON-Antwortformat gehen nicht in einem Aufruf
(Zitate sind mit strukturierter Ausgabe unvereinbar) — deshalb immer zwei
Schritte: Recherche als Text, Bewertung/Umwandlung als JSON.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from deps import db as _db, now_iso
from konfig import schalter_env, zahl_env

from ai import preisbasis
from ai.provider import json_bewerten, ki_aktiv, ki_modell, recherche

log = logging.getLogger("autohandel.ki")

SAMMLUNG = "ki_marktdaten"
DOK_ID = "aktuell"
MAX_SUCHEN_TABELLE = 8          # je Gruppe (drei Gruppen, siehe _gruppen)
MAX_SUCHEN_FALL = 4
# Probelauf 26.09.2026: mit EINER Anfrage fuer alle Positionen verbrauchte die
# KI alle Suchen, bevor sie einen Wert notiert hatte, und gab dann auf.
# Deshalb drei kleinere Gruppen und die Anweisung, Werte sofort aufzuschreiben.
SUCH_ANWEISUNG = ("Du hast fuer diese Liste hoechstens {n} Suchen. Plane sie (eine Suche deckt 1-2 Positionen, "
                  "ADAC zuerst) und schreibe jeden gefundenen Wert SOFORT in deine Antwort. Ist das Suchlimit "
                  "erreicht, gib die bis dahin gefundenen Werte aus — niemals abbrechen oder eine leere Antwort geben.")
_GRUPPEN = (
    ("Karosserie und Lack", ("delle", "kratzer", "steinschlag", "rost", "hagelschaden")),
    ("Licht, Felgen, Unfall", ("beleuchtung", "felge", "unfall_nicht_repariert", "unfall_repariert")),
    ("Schluessel, Reifen, HU, Unterlagen, Abweichungen",
     ("keys", "tires", "documents", "hu", "mileage", "previous_owners", "equipment_missing", "equipment_defect")),
)
BERICHT_MAX = 12000


def aktiv() -> bool:
    return schalter_env("KI_MARKTANALYSE_AKTIV", True)


def tage() -> int:
    return zahl_env("KI_MARKTDATEN_TAGE", 30, unten=1, oben=365)


def je_fall(art: str) -> bool:
    if art == "abholung":
        return schalter_env("KI_MARKTANALYSE_ABHOLUNG", True)
    return schalter_env("KI_MARKTANALYSE_VERTRAG", False)


RECHERCHE_SYSTEM = """Du recherchierst fuer AutoSchnell (Software fuer Autohaendler in Deutschland) aktuelle Reparatur- und Smart-Repair-Preise fuer Gebrauchtwagen — Deutschland, Euro inkl. MwSt., Stand heute.
Bevorzugte Quellen: ADAC (adac.de), Smart-Repair-Anbieter (z. B. Dellen-Doktor, Dellentechnik, Carglass/Wintec fuer Scheiben, ATU, Pitstop), Werkstattportale (FairGarage, autobutler, repareo, Werkstattvergleich), Fachanbieter fuer Fahrzeugschluessel und Reifen. Verbraucherportale wie Auto Bild, auto motor und sport sind in Ordnung; Forenbeitraege und Anzeigen nicht.
Regeln: je Position eine realistische Spanne (min-max) und einen typischen Wert, dazu die Quelle (Name + Adresse). Wenn du keine belastbare Angabe findest, sage das statt zu schaetzen. Antworte auf Deutsch, knapp, als Liste."""

UMWANDLUNG_SYSTEM = """Du wandelst einen Recherchebericht ueber Reparaturpreise in eine feste Tabelle um. typ ist IMMER der technische Schluessel aus der Positionsliste (z. B. keys fuer Schluessel, tires fuer Reifen, documents fuer Unterlagen, hu fuer HU), nie ein deutsches Wort. Uebernimm nur Werte, die im Bericht stehen (Euro inkl. MwSt.). Fehlt eine Position im Bericht, lass sie weg. typisch_eur liegt zwischen min_eur und max_eur. quelle: Name der Quelle (z. B. "ADAC", "Dellen-Doktor"), hinweis: hoechstens 12 Woerter (z. B. "je nach Groesse und Lage"). Antworte ausschliesslich nach dem Schema."""

MARKT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "positionen": {"type": "array", "items": {
            "type": "object",
            "properties": {
                # Probelauf 26.09.2026: ohne feste Werte kamen "schluessel"/"reifen"
                # statt keys/tires zurueck und fielen weg — jetzt als Aufzaehlung.
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
    """(Titel, Positionen) je Gruppe — nur Positionen, die es in BASIS gibt."""
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
        z = float(w)
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
async def aktuell(db=None) -> Optional[dict]:
    db = db if db is not None else _db
    try:
        return await db[SAMMLUNG].find_one({"_id": DOK_ID})
    except Exception:  # noqa: BLE001
        return None


async def aktualisieren(db=None, *, erzwingen: bool = False) -> dict:
    """Recherche (Websuche) + Umwandlung in die Tabelle; Ablage. Wirft nie."""
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
        letzter_status = "fehler"
        letzter_grund = "keine Antwort"
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
                for q in r.get("quellen") or []:
                    if q.get("url"):
                        quellen.setdefault(q["url"], q.get("titel") or "")
        if not texte:
            eintrag = {"_id": DOK_ID, "stand_versuch": jetzt, "status": letzter_status or "fehler",
                       "grund": letzter_grund or "keine Antwort"}
            await db[SAMMLUNG].update_one({"_id": DOK_ID}, {"$set": eintrag}, upsert=True)
            await _alarm(db, eintrag["status"], eintrag["grund"])
            return {"status": eintrag["status"], "grund": eintrag["grund"], "aktualisiert": False}
        r = {"text": "\n\n".join(texte), "quellen": [{"url": u, "titel": t} for u, t in quellen.items()],
             "usage": usage_r, "suchen": suchen, "dauer_ms": dauer, "modell": ki_modell()}
        j = await json_bewerten(system=UMWANDLUNG_SYSTEM,
                                nutzer={"bericht": r["text"][:BERICHT_MAX], "positionen": _positionen_liste()},
                                schema=MARKT_SCHEMA)
        if j.get("status") != "ok" or not isinstance(j.get("daten"), dict):
            grund = j.get("grund") or "Umwandlung fehlgeschlagen"
            await db[SAMMLUNG].update_one({"_id": DOK_ID}, {"$set": {"stand_versuch": jetzt, "status": "fehler", "grund": grund}},
                                          upsert=True)
            await _alarm(db, "fehler", grund)
            return {"status": "fehler", "grund": grund, "aktualisiert": False}
        positionen = _positionen_bereinigen(j["daten"].get("positionen") or [])
        if not positionen:
            # Probelauf 26.09.2026: die KI gab auf ("Suchlimit erschoepft") — eine
            # leere Tabelle ist ein Fehlversuch, kein Erfolg (Alarm, spaeter neu).
            grund = "keine Preisangaben gefunden"
            await db[SAMMLUNG].update_one({"_id": DOK_ID},
                                          {"$set": {"stand_versuch": jetzt, "status": "fehler", "grund": grund,
                                                    "bericht": (r.get("text") or "")[:BERICHT_MAX]}},
                                          upsert=True)
            await _alarm(db, "fehler", grund)
            return {"status": "fehler", "grund": grund, "aktualisiert": False, "suchen": suchen}
        usage = dict(r.get("usage") or {})
        for k, v in (j.get("usage") or {}).items():
            usage[k] = int(usage.get(k) or 0) + int(v or 0)
        doc = {"_id": DOK_ID, "stand": jetzt, "status": "ok", "grund": "", "positionen": positionen,
               "quellen": list(r.get("quellen") or [])[:20], "bericht": (r.get("text") or "")[:BERICHT_MAX],
               "zusammenfassung": str(j["daten"].get("zusammenfassung") or "")[:600],
               "modell": r.get("modell") or ki_modell(), "suchen": int(r.get("suchen") or 0),
               "dauer_ms": int(r.get("dauer_ms") or 0) + int(j.get("dauer_ms") or 0), "usage": usage}
        await db[SAMMLUNG].replace_one({"_id": DOK_ID}, doc, upsert=True)
        try:
            import betrieb
            await betrieb.alarm_schliessen(db, "ki_marktdaten_fehlgeschlagen", ref=DOK_ID)
        except Exception:  # noqa: BLE001
            pass
        return {**(_oeffentlich(doc) or {}), "aktualisiert": True}
    except Exception as exc:  # noqa: BLE001
        log.exception("Marktdaten nicht aktualisiert")
        return {"status": "fehler", "grund": f"{type(exc).__name__}: {str(exc)[:200]}", "aktualisiert": False}


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
    # Fehlversuch nicht jede Stunde wiederholen: fruehestens nach 6 Stunden
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
    erg = await aktualisieren(db, erzwingen=True)
    return {"status": erg.get("status"), "aktualisiert": erg.get("aktualisiert"), "grund": erg.get("grund") or ""}


def als_text(doc: Optional[dict]) -> str:
    """Zusatz fuer den System-Prompt beider Bewertungen."""
    if not doc or doc.get("status") != "ok" or not doc.get("positionen"):
        return ""
    stand = str(doc.get("stand") or "")[:10]
    zeilen = [f"Aktuelle Marktpreise (recherchiert am {stand}, Deutschland inkl. MwSt.; diese Werte haben "
              "Vorrang vor den Ausgangswerten oben):"]
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


# ------------------------------------------------ Recherche je Fall
def _fall_frage(art: str, paket: Dict[str, Any]) -> str:
    v = paket.get("vehicle") or {}
    auto = " ".join(str(x) for x in (v.get("make"), v.get("model"), v.get("variant")) if x).strip()
    ez = v.get("first_registration") or ""
    km = v.get("mileage_pickup_km") or v.get("mileage_contract_km") or v.get("mileage_km")
    zeilen = []
    schaeden = paket.get("damages") if art == "vertrag" else [d for d in (paket.get("new_damages") or [])
                                                             if not d.get("already_in_contract")]
    for d in schaeden or []:
        sd = d.get("severity_data") or {}
        merk = ", ".join(f"{k} {w}" for k, w in sd.items())
        zeilen.append(f"- {d.get('label') or d.get('type')} {d.get('zone') or ''}{(' (' + merk + ')') if merk else ''}")
    for a in (paket.get("deviations") or []):
        t = a.get("type")
        if t in ("keys", "tires", "hu", "equipment_missing", "equipment_defect", "documents"):
            zeilen.append(f"- {a.get('label')}: erwartet {a.get('expected')}, vor Ort {a.get('actual')}")
    if not zeilen:
        return ""
    return (f"Fahrzeug: {auto}, Erstzulassung {ez}, {km or '?'} km. Recherchiere aktuelle Reparatur-/Ersatzkosten "
            "(Deutschland, inkl. MwSt.) fuer genau diese Punkte; bevorzuge ADAC und Smart-Repair-Anbieter, bei "
            "Schluesseln/Teilen Markenangaben. Je Punkt: Spanne, typischer Wert, Quelle. Knapp antworten:\n"
            + "\n".join(zeilen[:8]) + "\n\n" + SUCH_ANWEISUNG.format(n=MAX_SUCHEN_FALL))


async def fall_recherche(art: str, paket: Dict[str, Any]) -> Optional[dict]:
    """Gezielte Websuche zu den Schaeden/Abweichungen eines Falls. None, wenn
    aus oder nichts zu suchen; sonst {text, quellen, suchen, dauer_ms, usage}."""
    if not aktiv() or not je_fall(art) or not ki_aktiv():
        return None
    frage = _fall_frage(art, paket)
    if not frage:
        return None
    try:
        r = await recherche(system=RECHERCHE_SYSTEM, frage=frage, max_suchen=MAX_SUCHEN_FALL)
    except Exception:  # noqa: BLE001
        log.exception("Fall-Recherche gescheitert")
        return None
    if r.get("status") != "ok" or not (r.get("text") or "").strip():
        return {"text": "", "quellen": [], "suchen": int(r.get("suchen") or 0), "dauer_ms": r.get("dauer_ms"),
                "usage": r.get("usage") or {}, "status": r.get("status")}
    return {"text": (r.get("text") or "")[:6000], "quellen": list(r.get("quellen") or [])[:10],
            "suchen": int(r.get("suchen") or 0), "dauer_ms": r.get("dauer_ms"), "usage": r.get("usage") or {},
            "status": "ok"}


def fall_als_text(fall: Optional[dict]) -> str:
    if not fall or not fall.get("text"):
        return ""
    return ("Marktrecherche zu diesem Fall (Websuche, Quellen unten; hat Vorrang vor Tabelle und "
            "Ausgangswerten):\n" + fall["text"]
            + ("\nQuellen: " + "; ".join(f"{q.get('titel') or ''} {q.get('url')}".strip() for q in fall.get("quellen") or [])
               if fall.get("quellen") else ""))
