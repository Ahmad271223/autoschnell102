# -*- coding: utf-8 -*-
"""Modelle und Segmente (Modell x km-Bereich). market_models ist die
Wahrheit fuer "welche Modelle"; market_config/km_buckets fuer die
km-Grenzen — beides zentral, nirgends dupliziert."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from markt import katalog, konfig


def ez_kurz(ez: Optional[Dict[str, Any]]) -> str:
    """'2019' (Einzeljahr), '2019-2021' (Bereich), 'alle'."""
    if not ez or not (ez.get("year_from") or ez.get("year_to")):
        return "alle"
    von, bis = ez.get("year_from"), ez.get("year_to")
    if von and bis and von == bis:
        return str(von)
    return f"{von or ''}-{bis or ''}"


def segment_id(model_id: str, bucket: Dict[str, Any], ez: Optional[Dict[str, Any]] = None) -> str:
    """Stabile ID, Auftrag v2: bmw-320d:2019:50001-85000 (Bereichs-EZ: bmw-320d:2019-2021:…)."""
    return f"{model_id}:{ez_kurz(ez)}:{int(bucket['min_km'])}-{int(bucket['max_km'])}"


def ez_text(ez: Optional[Dict[str, Any]]) -> str:
    if not ez or not (ez.get("year_from") or ez.get("year_to")):
        return "alle Baujahre"
    von, bis = ez.get("year_from"), ez.get("year_to")
    if von and bis and von == bis:
        return f"EZ {von}"
    return f"EZ {von or '…'}–{bis or '…'}"


def ez_buckets_fuer_modell(modell: Dict[str, Any], standard: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Modell-eigene EZ-Jahre (ez_years) gehen vor der zentralen Liste."""
    jahre = modell.get("ez_years")
    if isinstance(jahre, list) and jahre:
        return [{"year_from": int(j), "year_to": int(j)} for j in jahre if str(j).isdigit()]
    return standard


def ez_buckets_fuer_modell_liste(modell: Dict[str, Any]) -> List[Dict[str, Any]]:
    return ez_buckets_fuer_modell(modell, list(konfig.EZ_BUCKETS_STANDARD))


def km_buckets_fuer_modell(modell: Dict[str, Any], standard: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Modell-eigene km-Bereiche gehen vor der zentralen Liste."""
    eigene = modell.get("km_buckets")
    if isinstance(eigene, list) and eigene:
        return [{"min_km": int(b["min_km"]), "max_km": int(b["max_km"])} for b in eigene]
    return standard


def modell_aktiv(m: Dict[str, Any]) -> bool:
    """status 'active' (v3) bzw. altes enabled=true ohne status."""
    st = m.get("status")
    if st:
        return st == "active"
    return bool(m.get("enabled"))


def km_text(b: Dict[str, Any]) -> str:
    """'55–85k km' fuer die Anzeige."""
    mn, mx = int(b.get("min_km") or 0), int(b.get("max_km") or 0)
    return f"{round(mn / 1000)}–{round(mx / 1000)}k km"


async def modelle_einspielen(db, *, nur_fehlende: bool = True) -> Dict[str, int]:
    """Startliste in market_models — bestehende Eintraege (auch enabled=false
    des Betreibers) bleiben unangetastet."""
    neu, vorhanden = 0, 0
    for m in katalog.start_modelle():
        alt = await db[konfig.MODELLE].find_one({"id": m["id"]}, {"_id": 1})
        if alt:
            vorhanden += 1
            if nur_fehlende:
                continue
            await db[konfig.MODELLE].update_one({"id": m["id"]}, {"$set": {k: v for k, v in m.items() if k != "enabled"}})
            continue
        await db[konfig.MODELLE].insert_one({**m, "created_at": konfig.jetzt_iso(), "updated_at": konfig.jetzt_iso()})
        neu += 1
    return {"neu": neu, "vorhanden": vorhanden}


async def km_buckets(db) -> List[Dict[str, Any]]:
    doc = await db[konfig.KONFIG].find_one({"_id": "km_buckets"})
    buckets = (doc or {}).get("buckets")
    if not buckets:
        return [dict(b) for b in konfig.KM_BUCKETS_STANDARD]
    return [{"min_km": int(b["min_km"]), "max_km": int(b["max_km"])} for b in buckets]


async def km_buckets_setzen(db, buckets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sauber = []
    for b in buckets:
        mn, mx = int(b.get("min_km") or 0), int(b.get("max_km") or 0)
        if mn < 0 or mx <= mn or mx > 1000000:
            raise ValueError(f"ungültiger km-Bereich {mn}-{mx}")
        sauber.append({"min_km": mn, "max_km": mx})
    if not sauber or len(sauber) > 12:
        raise ValueError("1 bis 12 km-Bereiche")
    await db[konfig.KONFIG].update_one({"_id": "km_buckets"}, {"$set": {"buckets": sauber, "updated_at": konfig.jetzt_iso()}},
                                       upsert=True)
    return sauber


async def ez_buckets(db) -> List[Dict[str, Any]]:
    doc = await db[konfig.KONFIG].find_one({"_id": "ez_buckets"})
    buckets = (doc or {}).get("buckets")
    if buckets is None:
        return [dict(b) for b in konfig.EZ_BUCKETS_STANDARD]
    return [{"year_from": (int(b["year_from"]) if b.get("year_from") else None),
             "year_to": (int(b["year_to"]) if b.get("year_to") else None)} for b in buckets]


async def ez_buckets_setzen(db, buckets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Leere Liste = keine EZ-Aufteilung (ein Segment je km-Bereich)."""
    sauber = []
    for b in buckets or []:
        von = int(b["year_from"]) if b.get("year_from") else None
        bis = int(b["year_to"]) if b.get("year_to") else None
        if von and (von < 1980 or von > 2100) or bis and (bis < 1980 or bis > 2100) or (von and bis and bis < von):
            raise ValueError(f"ungültiger EZ-Bereich {von}-{bis}")
        if not von and not bis:
            raise ValueError("EZ-Bereich braucht von oder bis")
        sauber.append({"year_from": von, "year_to": bis})
    if len(sauber) > 12:
        raise ValueError("höchstens 12 EZ-Bereiche")
    await db[konfig.KONFIG].update_one({"_id": "ez_buckets"}, {"$set": {"buckets": sauber, "updated_at": konfig.jetzt_iso()}},
                                       upsert=True)
    return sauber


def ez_bucket_fuer_jahr(buckets: List[Dict[str, Any]], jahr: Optional[int]) -> Optional[Dict[str, Any]]:
    if jahr is None:
        return None
    for b in buckets:
        von, bis = b.get("year_from"), b.get("year_to")
        if (von is None or jahr >= von) and (bis is None or jahr <= bis):
            return b
    return None


async def einstellungen(db) -> Dict[str, Any]:
    doc = await db[konfig.KONFIG].find_one({"_id": "einstellungen"}) or {}
    return {"rows_je_segment": int(doc.get("rows_je_segment") or konfig.rows_je_segment()),
            "crawls_je_tag": int(doc.get("crawls_je_tag") or 1)}


async def einstellungen_setzen(db, **werte) -> Dict[str, Any]:
    setzen = {}
    if "rows_je_segment" in werte and werte["rows_je_segment"] is not None:
        setzen["rows_je_segment"] = max(1, min(200, int(werte["rows_je_segment"])))
    if "crawls_je_tag" in werte and werte["crawls_je_tag"] is not None:
        setzen["crawls_je_tag"] = max(1, min(4, int(werte["crawls_je_tag"])))
    if setzen:
        setzen["updated_at"] = konfig.jetzt_iso()
        await db[konfig.KONFIG].update_one({"_id": "einstellungen"}, {"$set": setzen}, upsert=True)
    return await einstellungen(db)


async def synchronisieren(db) -> Dict[str, int]:
    """Segmente aus (aktive Modelle x km-Bereiche) anlegen/aktualisieren;
    alles andere deaktivieren (nichts loeschen — Historie bleibt)."""
    buckets = await km_buckets(db)
    ezs = await ez_buckets(db) or [None]
    einst = await einstellungen(db)
    modelle = await db[konfig.MODELLE].find({}, {"_id": 0}).to_list(2000)
    gueltig = set()
    neu = 0
    for m in modelle:
        if not modell_aktiv(m) or not m.get("model_id"):
            continue
        for b in km_buckets_fuer_modell(m, buckets):
            for ez in (ez_buckets_fuer_modell(m, ezs) or [None]):
                sid = segment_id(m["id"], b, ez)
                gueltig.add(sid)
                r = await db[konfig.SEGMENTE].update_one(
                    {"id": sid},
                    {"$set": {"model_id": m["id"], "label": m.get("label"), "make": m.get("make"), "model": m.get("model"),
                              "variant": m.get("variant"), "min_km": b["min_km"], "max_km": b["max_km"],
                              "km_label": km_text(b), "year_from": (ez or {}).get("year_from"),
                              "year_to": (ez or {}).get("year_to"), "ez_label": ez_text(ez),
                              "max_items": int(m.get("rows") or einst["rows_je_segment"]),
                              "crawls_per_day": int(m.get("crawls_per_day") or 1), "sort": "price_asc",
                              "enabled": True, "priority": int(m.get("priority") or 5), "updated_at": konfig.jetzt_iso()},
                     "$setOnInsert": {"fuel": None, "gearbox": None, "body": None,
                                      "last_success_at": None, "last_planned_tag": None, "created_at": konfig.jetzt_iso()}},
                    upsert=True)
                if r.upserted_id is not None:
                    neu += 1
    r = await db[konfig.SEGMENTE].update_many({"id": {"$nin": list(gueltig)}, "enabled": True},
                                              {"$set": {"enabled": False, "updated_at": konfig.jetzt_iso()}})
    return {"segmente": len(gueltig), "neu": neu, "deaktiviert": r.modified_count}


def bucket_fuer_km(buckets: List[Dict[str, Any]], km: Optional[int]) -> Optional[Dict[str, Any]]:
    if km is None:
        return None
    for b in buckets:
        if int(b["min_km"]) <= int(km) <= int(b["max_km"]):
            return b
    return None
