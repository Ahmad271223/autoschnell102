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


def modell_version(modell: Optional[Dict[str, Any]]) -> int:
    """Fassung des Suchauftrags (Review 26.09.2026 abends P4): Start 1; steigt nur, wenn
    materielle Merkmale (Marke/Modell, Kraftstoff, Getriebe, Karosserie, kW, Land, PLZ/Radius,
    Verkaeuferart) geaendert werden — siehe auftraege.definition_hash."""
    try:
        return max(1, int((modell or {}).get("version") or 1))
    except (TypeError, ValueError):
        return 1


def segment_id(model_id: str, bucket: Dict[str, Any], ez: Optional[Dict[str, Any]] = None, version: int = 1) -> str:
    """Stabile ID, Auftrag v2: bmw-320d:2019:50001-85000 (Bereichs-EZ: bmw-320d:2019-2021:…).
    P4: ab Fassung 2 traegt die ID die Fassung (bmw-320d:v2:2019:50001-85000) — alte IDs und
    Daten der Fassung 1 bleiben unveraendert, neue Fassungen mischen sich nicht mit ihnen."""
    fassung = f"v{int(version)}:" if int(version or 1) >= 2 else ""
    return f"{model_id}:{fassung}{ez_kurz(ez)}:{int(bucket['min_km'])}-{int(bucket['max_km'])}"


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
    # Review 26.09.2026 Nr. 40: dieselbe Pruefung wie auftraege.km_bereiche_pruefen —
    # sortiert, keine Ueberschneidung (sonst landet ein Auto in zwei Segmenten)
    sauber.sort(key=lambda b: b["min_km"])
    for a, b in zip(sauber, sauber[1:]):
        if b["min_km"] <= a["max_km"]:
            raise ValueError(f"km-Bereiche überschneiden sich: {a['min_km']}–{a['max_km']} und {b['min_km']}–{b['max_km']}")
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


async def _verkaeufer_normalisieren(db, modelle: List[Dict[str, Any]]) -> int:
    """Reparaturwelle 6 Nr. 84: Auftraege, die noch 'FSBO' tragen, werden auf 'PRIVATE' gehoben — der
    definition_hash aendert sich dadurch NICHT (er normalisiert die Verkaeuferart), also keine neue Fassung."""
    from markt import auftraege
    n = 0
    for m in modelle:
        s = str(m.get("seller_type") or "").upper()
        if s and s != "DEALER" and s != "PRIVATE":
            neu = "PRIVATE" if s in ("FSBO", "PRIVAT") else ("DEALER" if s == "HAENDLER" else None)
            if not neu:
                continue
            alt_hash = m.get("definition_hash")
            m["seller_type"] = neu
            setzen = {"seller_type": neu, "definition_hash": auftraege.definition_hash(m)}
            if alt_hash and m.get("testlauf_ok_hash") == alt_hash:
                setzen["testlauf_ok_hash"] = setzen["definition_hash"]      # der bestandene Testlauf gilt weiter
            await db[konfig.MODELLE].update_one({"id": m["id"]}, {"$set": setzen})
            m.update(setzen)
            n += 1
    return n


async def _nachplanen(db) -> Dict[str, Any]:
    """Reparaturwelle 6 Nr. 95: neue Segmente (Aktivierung/Anlage/Aenderung) bekommen heute noch Jobs im
    Tageskontingent — sonst erst am Folgetag. Nur bei laufendem Crawler und vorhandenem Token; wirft nie."""
    try:
        if not await konfig.crawler_aktiv(db) or not konfig.token():
            return {"uebersprungen": "crawler aus"}
        from markt import jobs
        return await jobs.tagesplan(db, sofort=True)
    except Exception as e:  # noqa: BLE001
        return {"fehler": str(e)[:200]}


async def synchronisieren(db, *, nachplanen: bool = True) -> Dict[str, Any]:
    """Segmente aus (aktive Modelle x km-Bereiche) anlegen/aktualisieren;
    alles andere deaktivieren (nichts loeschen — Historie bleibt).
    nachplanen (Welle 6 Nr. 95): entstehen neue Segmente, werden fuer heute Jobs nachgeplant
    (der Worker ruft mit nachplanen=False, weil er den Tagesplan selbst anlegt)."""
    buckets = await km_buckets(db)
    ezs = await ez_buckets(db) or [None]
    einst = await einstellungen(db)
    modelle = await db[konfig.MODELLE].find({}, {"_id": 0}).to_list(2000)
    await _verkaeufer_normalisieren(db, modelle)
    gueltig = set()
    neu = 0
    for m in modelle:
        if not modell_aktiv(m) or not m.get("model_id"):
            continue
        fassung = modell_version(m)
        for b in km_buckets_fuer_modell(m, buckets):
            for ez in (ez_buckets_fuer_modell(m, ezs) or [None]):
                # P4: Segment-IDs tragen die Fassung des Auftrags — aeltere Fassungen fallen
                # unten unter "deaktivieren" (nichts loeschen, Historie bleibt)
                sid = segment_id(m["id"], b, ez, fassung)
                gueltig.add(sid)
                r = await db[konfig.SEGMENTE].update_one(
                    {"id": sid},
                    {"$set": {"model_id": m["id"], "version": fassung, "label": m.get("label"), "make": m.get("make"), "model": m.get("model"),
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
    weg = [s["id"] async for s in db[konfig.SEGMENTE].find({"id": {"$nin": list(gueltig)}, "enabled": True}, {"_id": 0, "id": 1})]
    r = await db[konfig.SEGMENTE].update_many({"id": {"$nin": list(gueltig)}, "enabled": True},
                                              {"$set": {"enabled": False, "updated_at": konfig.jetzt_iso()}})
    # Review 26.09.2026 Nr. 56: wartende Jobs der deaktivierten Segmente stornieren — sonst
    # crawlt der Worker Segmente, die es im Suchauftrag nicht mehr gibt (Kosten ohne Nutzen)
    storniert = 0
    if weg:
        j = await db[konfig.JOBS].update_many({"segment_id": {"$in": weg}, "status": "queued"},
                                              {"$set": {"status": "cancelled", "error": "Segment deaktiviert",
                                                        "finished_at": konfig.jetzt_iso()}})
        storniert = j.modified_count
    erg: Dict[str, Any] = {"segmente": len(gueltig), "neu": neu, "deaktiviert": r.modified_count, "jobs_storniert": storniert}
    if nachplanen and neu:
        erg["nachgeplant"] = await _nachplanen(db)
    return erg


def bucket_fuer_km(buckets: List[Dict[str, Any]], km: Optional[int]) -> Optional[Dict[str, Any]]:
    if km is None:
        return None
    for b in buckets:
        if int(b["min_km"]) <= int(km) <= int(b["max_km"]):
            return b
    return None
