# -*- coding: utf-8 -*-
"""Bildschirm-Lesung absichern (Befund/Wunsch Ahmad 09.10.2026: "das darf alles nicht passieren — verbessere das so,
dass er sowas nicht wiederholt").

Das Windows-Programm liest AutoPointer per Texterkennung (DevExpress gibt keinen Text her) — Lesefehler wie "IBO" statt
"i30", "VWT- Ro c" statt "VW T-Roc" oder "EIektro" lassen sich nie ganz vermeiden. Drei Sicherungen:

1. Echte Daten vor Bildschirm (``inserat_daten`` + ``passt_zum_bildschirm`` + ``vehicle_aus_inserat``): liegt das
   Inserat schon gelesen vor (Lesung der Erweiterung der EIGENEN Firma, 24 h, oder der gemeinsame Speicher aus
   Server-Abrufen, 14 Tage), baut der Server die Vergleiche aus DIESEN Daten — wenn Erstzulassung und Kilometer zum
   Bildschirm passen (sonst koennte die Inserat-Nummer falsch gelesen sein) und die erkannte Marke nicht widerspricht.
   Haertung 09.10.2026: Lesungen FREMDER Firmen zaehlen hier nicht — eine gefaelschte Seite einer anderen Firma
   duerfte sonst eine richtige Bildschirm-Lesung ueberschreiben (fuer Links/Kaufvertrag gilt weiter inserat_lesen).
2. Lernen (``lernen`` / ``gelernt``): zeigt eine Lesung des Inserats, dass der Bildschirmtext etwas anderes war, merkt
   sich der Server "dieser gelesene Text = diese Marke/dieses Modell" — JE FIRMA (Haertung 09.10.2026: eine Firma
   kann mit zwei gefaelschten Lesungen sonst die Erkennung aller anderen vergiften; dieselbe Firma hat ohnehin
   dieselbe AutoPointer-Einstellung und denselben Lesefehler). Erst wenn ZWEI verschiedene Inserate dasselbe sagen
   und keines widerspricht, gilt es — dann wird derselbe Lesefehler beim naechsten Mal richtig erkannt.
3. Zweiter Leseversuch des Programms (ab 1.5.12, ``fahrzeug.alternativen``): routes/werkzeuge._erkennen nimmt eine
   Alternative, wenn die erste Lesung nicht erkannt wurde.

Kein FastAPI, keine Routen — die Tests nutzen das Modul direkt.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from werkzeug_erkennung import norm

log = logging.getLogger("autohandel")

SAMMLUNG = "erkennung_gelernt"
#: so viele verschiedene Inserate muessen dieselbe Zuordnung zeigen, bevor sie gilt
BELEGE_MINDESTENS = 2
#: hoechstens so viele Belege je gelesenem Text (Speicher bleibt klein)
BELEGE_HOECHSTENS = 50
#: hoechstens so viele verschiedene gelesene Texte je Firma (Haertung 09.10.2026, M3) — ``tabelle`` laedt bis 5.000
JE_FIRMA_HOECHSTENS = 3000
GELERNT_TAGE = 180
#: je Firma: {dealer_id: {"bis": monotonic, "daten": {schluessel: (Marke, Modell)}}}
_TABELLEN: Dict[str, dict] = {}
_TABELLE_SEKUNDEN = 300
_TABELLEN_HOECHSTENS = 2000

#: Felder einer Inserats-Lesung, die die Vergleichs-Links bestimmen (mobile_service/autoscout_service)
_ECHTE_FELDER = ("make", "make_label", "model", "model_label", "model_description", "category", "first_registration",
                 "mileage", "fuel", "fuel_label", "gearbox", "gearbox_label", "power_kw", "power_ps", "doors")


# ---------------------------------------------------------------- 1. echte Daten vor Bildschirm
async def inserat_daten(db, cache_key: str, user: dict) -> Optional[dict]:
    """Die gelesenen Daten dieses Inserats (Lesung der Erweiterung — eigene Firma zuerst — oder gemeinsamer Speicher),
    sonst None. Nur Lesen: nichts wird abgerufen."""
    if not cache_key:
        return None
    from browser_helfer import inserat_lesen
    try:
        lesung = await inserat_lesen(db, cache_key, user.get("id") or "", user.get("dealer_id") or "")
        # nur die eigene Firma (Haertung 09.10.2026, s. o.) — inserat_lesen nimmt sonst auch fremde Lesungen
        if (lesung is not None and isinstance(lesung[0], dict)
                and (lesung[2] or {}).get("dealer_id") == (user.get("dealer_id") or "")):
            return lesung[0]
        doc = await db.listings_cache.find_one(
            {"cache_key": cache_key, "data": {"$type": "object"}, "expires_at": {"$gt": datetime.now(timezone.utc)}},
            {"_id": 0, "data": 1})
        return (doc or {}).get("data")
    except Exception:  # noqa: BLE001 — eine fehlende Lesung darf den Vergleich nie aufhalten
        log.exception("Erkennung: Inserat-Daten %s nicht gelesen", cache_key)
        return None


def _jahr(ez) -> Optional[int]:
    m = re.search(r"(19|20)\d\d", str(ez or ""))
    return int(m.group(0)) if m else None


def passt_zum_bildschirm(daten: Optional[dict], f: dict, erkannte_marke: Optional[str]) -> bool:
    """Gehoeren die gelesenen Inserat-Daten zum Auto auf dem Bildschirm? Erstzulassung (+-1 Jahr) und Kilometer
    (+-10 %, mind. 5.000 km) muessen passen; eine erkannte Marke darf nicht widersprechen. Sonst koennte die
    Inserat-Nummer falsch gelesen sein — dann lieber die Bildschirm-Werte."""
    if not isinstance(daten, dict) or not (daten.get("make_label") or daten.get("make")):
        return False
    jahr_echt, jahr_bild = _jahr(daten.get("first_registration")), f.get("ez_jahr")
    if not jahr_echt or not jahr_bild or abs(int(jahr_echt) - int(jahr_bild)) > 1:
        return False
    km_echt, km_bild = daten.get("mileage"), f.get("kilometer")
    if not isinstance(km_echt, (int, float)) or km_bild is None:
        return False
    if abs(float(km_echt) - float(km_bild)) > max(5000.0, 0.1 * max(float(km_echt), float(km_bild))):
        return False
    if erkannte_marke:
        from werkzeug_erkennung import MOBILE_ALIASE
        echt = norm(daten.get("make_label") or daten.get("make"))
        bild = norm(erkannte_marke)
        echt, bild = MOBILE_ALIASE.get(echt, echt), MOBILE_ALIASE.get(bild, bild)      # "VW" = "Volkswagen"
        if echt and bild and echt != bild and not (echt.startswith(bild) or bild.startswith(echt)):
            return False
    return True


def vehicle_aus_inserat(daten: dict, vehicle_bild: dict) -> dict:
    """Fahrzeug fuer die Vergleichs-Links: die Werte aus dem Inserat, wo vorhanden, sonst die vom Bildschirm."""
    v = dict(vehicle_bild)
    for k in _ECHTE_FELDER:
        w = daten.get(k)
        if w not in (None, ""):
            v[k] = w
    return v


# ---------------------------------------------------------------- 2. Lernen
def schluessel(roh: Optional[str]) -> str:
    """Der gelesene Text als Schluessel ("VWT- Ro c" -> "vwtroc")."""
    return norm(roh)[:120]


def doc_id(dealer_id: Optional[str], roh: Optional[str]) -> str:
    """Schluessel in der Sammlung: Firma + gelesener Text."""
    return f"{dealer_id or ''}:{schluessel(roh)}"


async def lernen(db, roh: Optional[str], marke: Optional[str], modell: Optional[str], beleg: str,
                 dealer_id: Optional[str] = None) -> None:
    """Der Bildschirmtext ``roh`` war laut Inserat ``beleg`` (cache_key) das Auto ``marke`` ``modell`` — fuer die
    Firma ``dealer_id``."""
    k = schluessel(roh)
    marke, modell = str(marke or "").strip()[:60], str(modell or "").strip()[:80]
    if len(k) < 2 or not marke or not modell or not beleg or not dealer_id:
        return
    jetzt = datetime.now(timezone.utc)
    try:
        # Haertung 09.10.2026 (M3): hoechstens JE_FIRMA_HOECHSTENS gelesene Texte je Firma — ein neuer Text kommt
        # dann nicht mehr dazu (bekannte Texte bekommen weiter Belege)
        if (await db[SAMMLUNG].count_documents({"dealer_id": dealer_id}, limit=JE_FIRMA_HOECHSTENS)
                >= JE_FIRMA_HOECHSTENS
                and not await db[SAMMLUNG].count_documents({"_id": doc_id(dealer_id, roh)}, limit=1)):
            log.warning("Erkennung: Firma %s hat schon %d gelernte Texte — %r nicht gespeichert",
                        dealer_id, JE_FIRMA_HOECHSTENS, roh)
            return
        await db[SAMMLUNG].update_one(
            {"_id": doc_id(dealer_id, roh), f"eintraege.{BELEGE_HOECHSTENS - 1}": {"$exists": False}},
            {"$addToSet": {"eintraege": {"marke": marke, "modell": modell, "beleg": str(beleg)[:80]}},
             "$set": {"dealer_id": dealer_id, "schluessel": k, "roh": str(roh or "")[:160], "aktualisiert": jetzt,
                      "ablauf": jetzt + timedelta(days=GELERNT_TAGE)}},
            upsert=True)
        _TABELLEN.pop(dealer_id, None)          # beim naechsten Vergleich dieser Firma neu laden
    except Exception as exc:  # noqa: BLE001 — Lernen ist Zusatz (DuplicateKey bei voller Liste: egal)
        if "duplicate key" not in str(exc).lower():
            log.exception("Erkennung: Lernen %r -> %s %s nicht gespeichert", roh, marke, modell)


def auswerten(eintraege) -> Optional[Tuple[str, str]]:
    """(Marke, Modell), wenn mindestens BELEGE_MINDESTENS verschiedene Inserate dasselbe zeigen und KEINES
    etwas anderes; sonst None."""
    je_wert: Dict[Tuple[str, str], set] = {}
    for e in eintraege or []:
        if isinstance(e, dict) and e.get("marke") and e.get("modell") and e.get("beleg"):
            je_wert.setdefault((e["marke"], e["modell"]), set()).add(e["beleg"])
    if len(je_wert) != 1:
        return None
    (wert, belege), = je_wert.items()
    return wert if len(belege) >= BELEGE_MINDESTENS else None


async def tabelle(db, dealer_id: Optional[str]) -> Dict[str, Tuple[str, str]]:
    """Die geltenden Zuordnungen EINER Firma {schluessel: (Marke, Modell)} — je Prozess 5 Minuten zwischengespeichert."""
    if not dealer_id:
        return {}
    eintrag = _TABELLEN.get(dealer_id)
    if eintrag and time.monotonic() < eintrag["bis"]:
        return eintrag["daten"]
    daten: Dict[str, Tuple[str, str]] = {}
    try:
        async for d in db[SAMMLUNG].find({"dealer_id": dealer_id}, {"schluessel": 1, "eintraege": 1}).limit(5000):
            wert = auswerten(d.get("eintraege"))
            if wert and d.get("schluessel"):
                daten[d["schluessel"]] = wert
    except Exception:  # noqa: BLE001
        log.exception("Erkennung: gelernte Zuordnungen nicht geladen")
        return (eintrag or {}).get("daten", {})
    if len(_TABELLEN) >= _TABELLEN_HOECHSTENS:
        _TABELLEN.clear()
    _TABELLEN[dealer_id] = {"bis": time.monotonic() + _TABELLE_SEKUNDEN, "daten": daten}
    return daten


def gelernt(tab: Dict[str, Tuple[str, str]], roh: Optional[str]) -> Optional[Tuple[str, str]]:
    return tab.get(schluessel(roh)) if tab else None


def modell_weicht_ab(daten: dict, marke: Optional[str], modell: Optional[str], gefunden: bool) -> bool:
    """Lohnt es sich zu lernen? Wenn der Bildschirmtext gar kein Katalogmodell ergab oder ein anderes als im Inserat."""
    if not gefunden:
        return True
    echt_marke, echt_modell = norm(daten.get("make_label") or daten.get("make")), norm(daten.get("model_label"))
    return (bool(echt_modell) and norm(modell) != echt_modell) or (bool(echt_marke) and bool(marke)
                                                                 and norm(marke) != echt_marke)
