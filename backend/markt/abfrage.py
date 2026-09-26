# -*- coding: utf-8 -*-
"""Lesewege (nur lesen, nie schreiben, nie auf einen Crawl warten):
  * Karte im Vergleich / Hinweis im Vertrag: Segment zum Fahrzeug finden,
    fertige Segmentstatistik + Historie dieses Inserats
  * Chancen (Deal Radar) mit Filtern
  * Admin-Marktanalyse: Modelle, Modell-Detail, Segment-Zusammenfassung,
    Verlauf aus Tagesaggregaten (nie aus Snapshots), Listings, Listing-Verlauf
Alle Zahlen heissen bewusst "Top-20" / "guenstiges Segment" — kein Marktmedian."""
from __future__ import annotations

import re
import statistics
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from markt import budget, jobs, konfig, segmente
from markt.konfig import CHANCEN, JOBS, LISTINGS, MODELLE, SEGMENTE, SEGMENTSTATS, SNAPSHOTS, TAGESSTATS

HINWEIS = ("Beobachtet werden je Segment nur die 20 günstigsten passenden Angebote — "
           "das ist die untere Marktpreisspanne, kein Marktmedian für ganz Deutschland.")


# ---------------------------------------------------------------- Fahrzeug -> Segment
def _kraftstoff_code(v: Dict[str, Any]) -> Optional[str]:
    s = " ".join(str(v.get(k) or "") for k in ("fuel", "fuel_label", "fuel_type")).lower()
    if "diesel" in s:
        return "DIESEL"
    if "elektr" in s or "electric" in s:
        return "ELECTRICITY"
    if "benzin" in s or "petrol" in s or "super" in s:
        return "PETROL"
    return None


def _jahr(v: Dict[str, Any]) -> Optional[int]:
    m = re.search(r"(\d{4})", str(v.get("first_registration") or ""))
    return int(m.group(1)) if m else None


def _kw(v: Dict[str, Any]) -> Optional[int]:
    try:
        kw = v.get("power_kw")
        return int(kw) if kw else None
    except (TypeError, ValueError):
        return None


async def modelle_fuer_fahrzeug(db, v: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Passende market_models-Eintraege zu einem Fahrzeug (Marke/Modell ueber
    den mobile.de-Katalog, dann Kraftstoff und Leistungsbereich), nach Prioritaet."""
    try:
        from mobile_service import _resolve_make, _resolve_model
        make_id, eintrag = _resolve_make(v)
        model_id = _resolve_model(eintrag, v) if make_id else None
    except Exception:  # noqa: BLE001
        return []
    if not make_id or not model_id:
        return []
    kandidaten = await db[MODELLE].find({"make_id": str(make_id), "model_id": str(model_id), "enabled": True},
                                        {"_id": 0}).sort("priority", 1).to_list(50)
    fuel, kw = _kraftstoff_code(v), _kw(v)
    raus = []
    for m in kandidaten:
        if m.get("fuel") and fuel and m["fuel"] != fuel:
            continue
        if kw and m.get("power_kw_min") and kw < int(m["power_kw_min"]) - 3:
            continue
        if kw and m.get("power_kw_max") and kw > int(m["power_kw_max"]) + 3:
            continue
        raus.append(m)
    return raus


async def modell_fuer_fahrzeug(db, v: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ms = await modelle_fuer_fahrzeug(db, v)
    return ms[0] if ms else None


async def segment_fuer_fahrzeug(db, v: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Das erste passende, aktive Segment (Modell x km x EZ) mit Statistik."""
    ms = await modelle_fuer_fahrzeug(db, v)
    if not ms:
        return None
    km = v.get("mileage")
    try:
        km = int(km) if km not in (None, "") else None
    except (TypeError, ValueError):
        km = None
    b = segmente.bucket_fuer_km(await segmente.km_buckets(db), km)
    if not b:
        return None
    ezs = await segmente.ez_buckets(db)
    ez = segmente.ez_bucket_fuer_jahr(ezs, _jahr(v)) if ezs else None
    if ezs and not ez:
        return None
    erstes = None
    for m in ms:
        seg = await db[SEGMENTE].find_one({"id": segmente.segment_id(m["id"], b, ez), "enabled": True}, {"_id": 0})
        if not seg:
            continue
        erstes = erstes or seg
        if await db[SEGMENTSTATS].find_one({"_id": seg["id"], "sample_size": {"$gt": 0}}, {"_id": 1}):
            return seg
    return erstes


def _listing_kurz(l: Dict[str, Any], heute_snap: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    first = float(l.get("first_price") or 0) or None
    cur = float(l.get("current_price") or 0) or None
    return {"listing_id": l.get("listing_id"), "first_seen_at": l.get("first_seen_at"), "first_price": first,
            "current_price": cur, "change_since_first_eur": round(cur - first, 2) if first and cur else None,
            "price_reductions": l.get("price_reductions") or 0, "price_changes": l.get("price_changes") or 0,
            "mobile_created_at": l.get("mobile_created_at"), "active_state": l.get("active_state"),
            "last_seen_at": l.get("last_seen_at"), "rank_today": (heute_snap or {}).get("rank_in_sample"),
            "price_history": (l.get("price_history") or [])[-30:]}


async def karte(db, v: Dict[str, Any], listing_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Payload fuer die Karte im Vergleich. None = keine Daten (Karte bleibt weg)."""
    seg = await segment_fuer_fahrzeug(db, v)
    if not seg:
        return None
    stat = await db[SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0})
    if not stat or not stat.get("sample_size"):
        return None
    raus: Dict[str, Any] = {
        "segment_id": seg["id"], "label": seg.get("label"), "km_label": seg.get("km_label"), "ez_label": seg.get("ez_label"),
        "sample_size": stat.get("sample_size"), "min_price": stat.get("min_price"),
        "median_top20_price": stat.get("median_price"), "avg_top20_price": stat.get("avg_price"),
        "max_top20_price": stat.get("max_price"), "p25_price": stat.get("p25_price"), "p75_price": stat.get("p75_price"),
        "trend_7d_eur": stat.get("trend_7d_eur"), "trend_7d_pct": stat.get("trend_7d_pct"),
        "trend_30d_eur": stat.get("trend_30d_eur"), "trend_30d_pct": stat.get("trend_30d_pct"),
        "datenstand": stat.get("updated_at"), "datum": stat.get("date"), "datenlage": stat.get("datenlage"),
        "beobachtete_tage": stat.get("beobachtete_tage"), "hinweis": HINWEIS, "listing": None,
    }
    if listing_id:
        l = await db[LISTINGS].find_one({"source": konfig.QUELLE, "listing_id": str(listing_id)}, {"_id": 0})
        if l:
            snap = await db[SNAPSHOTS].find_one({"listing_id": str(listing_id), "segment_id": seg["id"], "date": stat.get("date")},
                                                {"_id": 0, "rank_in_sample": 1})
            raus["listing"] = _listing_kurz(l, snap)
    preis = v.get("list_price")
    try:
        preis = float(preis) if preis not in (None, "") else None
    except (TypeError, ValueError):
        preis = None
    if preis and raus["median_top20_price"]:
        raus["preis_vs_median_eur"] = round(preis - float(raus["median_top20_price"]), 2)
        raus["preis_vs_median_pct"] = round((preis - float(raus["median_top20_price"])) / float(raus["median_top20_price"]) * 100, 1)
        raus["unter_top20_min"] = preis < float(raus["min_price"] or 0)
    return raus


# ---------------------------------------------------------------- Chancen
async def chancen(db, *, typ: Optional[str] = None, model_id: Optional[str] = None, segment_id: Optional[str] = None,
                  km_min: Optional[int] = None, km_max: Optional[int] = None, tage: int = 7, limit: int = 100) -> List[Dict[str, Any]]:
    seit = (konfig.jetzt() - timedelta(days=max(1, min(int(tage), 90)))).isoformat()
    filt: Dict[str, Any] = {"created_at": {"$gte": seit}}
    if typ:
        filt["typ"] = typ if not typ.startswith("neu_top") else {"$regex": "^neu_top"}
    if model_id:
        filt["model_id"] = model_id
    if segment_id:
        filt["segment_id"] = segment_id
    if km_min is not None or km_max is not None:
        segs = await db[SEGMENTE].find({"min_km": {"$gte": int(km_min or 0)}, "max_km": {"$lte": int(km_max or 10**7)}},
                                       {"_id": 0, "id": 1}).to_list(5000)
        filt["segment_id"] = {"$in": [s["id"] for s in segs]}
    raus = await db[CHANCEN].find(filt, {"_id": 0}).sort("created_at", -1).to_list(max(1, min(int(limit), 500)))
    for c in raus:
        l = await db[LISTINGS].find_one({"source": c.get("source") or konfig.QUELLE, "listing_id": c.get("listing_id")},
                                        {"_id": 0, "active_state": 1, "current_price": 1})
        c["active_state"] = (l or {}).get("active_state")
        c["current_price"] = (l or {}).get("current_price")
    return raus


# ---------------------------------------------------------------- Admin-Marktanalyse
async def modelle_uebersicht(db, *, mit_archiv: bool = False) -> List[Dict[str, Any]]:
    from markt import auftraege
    filt = {} if mit_archiv else {"status": {"$ne": "archived"}}
    modelle = await db[MODELLE].find(filt, {"_id": 0}).sort([("priority", 1), ("label", 1)]).to_list(2000)
    verbrauch = await auftraege.monatsverbrauch_je_modell(db)
    segs = await db[SEGMENTE].find({}, {"_id": 0}).to_list(20000)
    stats = {s["segment_id"]: s async for s in db[SEGMENTSTATS].find({}, {"_id": 0})}
    heute = konfig.heute_tag()
    fehler_heute = {j["segment_id"] async for j in db[JOBS].find({"tag": heute, "status": "failed"}, {"_id": 0, "segment_id": 1})}
    raus = []
    for m in modelle:
        eigene = [s for s in segs if s.get("model_id") == m["id"]]
        aktive = [s for s in eigene if s.get("enabled")]
        st = [stats[s["id"]] for s in aktive if s["id"] in stats and stats[s["id"]].get("sample_size")]
        mins = [float(s["min_price"]) for s in st if s.get("min_price") is not None]
        meds = [float(s["median_price"]) for s in st if s.get("median_price") is not None]
        t7 = [float(s["trend_7d_pct"]) for s in st if s.get("trend_7d_pct") is not None]
        t30 = [float(s["trend_30d_pct"]) for s in st if s.get("trend_30d_pct") is not None]
        letzte = max((s.get("last_success_at") or "" for s in eigene), default="") or None
        anzahl = await db[LISTINGS].count_documents({"model_id": m["id"]})
        raus.append({**m, "status": m.get("status") or ("active" if m.get("enabled") else "paused"),
                     "prognose": auftraege.prognose_modell(m), "monatsverbrauch_usd": verbrauch.get(m["id"], 0.0),
                     "segmente_aktiv": len(aktive), "segmente_mit_daten": len(st),
                     "last_success_at": letzte, "listings": anzahl,
                     "min_price": min(mins) if mins else None,
                     "median_top20_mittel": round(statistics.median(meds), 2) if meds else None,
                     "trend_7d_pct": round(sum(t7) / len(t7), 2) if t7 else None,
                     "trend_30d_pct": round(sum(t30) / len(t30), 2) if t30 else None,
                     "crawl_status": ("fehler" if any(s["id"] in fehler_heute for s in aktive)
                                      else "ok" if letzte else "wartet")})
    return raus


async def modell_detail(db, model_id: str) -> Optional[Dict[str, Any]]:
    m = await db[MODELLE].find_one({"id": model_id}, {"_id": 0})
    if not m:
        return None
    segs = await db[SEGMENTE].find({"model_id": model_id}, {"_id": 0}).sort([("year_from", 1), ("min_km", 1)]).to_list(500)
    for s in segs:
        st = await db[SEGMENTSTATS].find_one({"_id": s["id"]}, {"_id": 0})
        s["stats"] = st
        s["datenlage"] = (st or {}).get("datenlage") or "keine"
        naechster = await db[JOBS].find_one({"segment_id": s["id"], "status": "queued"}, {"_id": 0, "scheduled_at": 1}, sort=[("scheduled_at", 1)])
        letzter = await db[JOBS].find_one({"segment_id": s["id"], "status": {"$in": ["completed", "failed"]}},
                                          {"_id": 0, "status": 1, "error": 1, "finished_at": 1, "actual_rows": 1}, sort=[("finished_at", -1)])
        s["naechster_crawl"] = (naechster or {}).get("scheduled_at")
        s["letzter_job"] = letzter
    from markt import auftraege
    return {**m, "status": m.get("status") or ("active" if m.get("enabled") else "paused"), "segmente": segs,
            "prognose": auftraege.prognose_modell(m),
            "km_buckets": segmente.km_buckets_fuer_modell(m, await segmente.km_buckets(db)),
            "ez_buckets": segmente.ez_buckets_fuer_modell(m, await segmente.ez_buckets(db))}


async def segment_zusammenfassung(db, segment_id: str) -> Optional[Dict[str, Any]]:
    s = await db[SEGMENTE].find_one({"id": segment_id}, {"_id": 0})
    if not s:
        return None
    st = await db[SEGMENTSTATS].find_one({"_id": segment_id}, {"_id": 0})
    m = await db[MODELLE].find_one({"id": s.get("model_id")}, {"_id": 0})
    letzter_job = await db[JOBS].find_one({"segment_id": segment_id}, {"_id": 0, "ergebnis": 0}, sort=[("created_at", -1)])
    # Datenqualitaet (Auftrag v3): seit wann, Tage, erfolgreiche vs. erwartete Crawls
    erster = await db[TAGESSTATS].find_one({"segment_id": segment_id, "sample_size": {"$gt": 0}}, {"_id": 0, "date": 1}, sort=[("date", 1)])
    erfolgreich = await db[JOBS].count_documents({"segment_id": segment_id, "status": "completed"})
    k = int(s.get("crawls_per_day") or 1)
    tage_seit = 0
    if erster:
        tage_seit = max(1, (datetime.strptime(konfig.heute_tag(), "%Y-%m-%d") - datetime.strptime(erster["date"], "%Y-%m-%d")).days + 1)
    qualitaet = {"daten_seit": (erster or {}).get("date"), "tage_beobachtet": (st or {}).get("beobachtete_tage") or 0,
                 "erfolgreiche_crawls": erfolgreich, "erwartete_crawls": tage_seit * k if erster else 0,
                 "sample_size": (st or {}).get("sample_size") or 0, "datenlage": (st or {}).get("datenlage") or "keine",
                 "crawls_per_day": k}
    return {"segment": s, "modell": m, "stats": st, "letzter_job": letzter_job, "qualitaet": qualitaet, "hinweis": HINWEIS}


_BEREICHE = {"7d": 7, "30d": 30, "90d": 90, "6m": 183, "1y": 366, "alle": 36500}


def _wochenschluessel(tag: str) -> str:
    d = datetime.strptime(tag, "%Y-%m-%d")
    j, w, _ = d.isocalendar()
    return f"{j}-W{w:02d}"


async def segment_verlauf(db, segment_id: str, bereich: str = "30d") -> Dict[str, Any]:
    """Zeitreihe aus market_segment_daily_stats (30 Tage = 30 Dokumente) plus
    deterministische Auswertung: Tages-/Wochenveraenderung, groesster
    Anstieg/Rueckgang, Tage fallend/steigend/unveraendert, Extreme."""
    tage = _BEREICHE.get(bereich, 30)
    seit = (konfig.jetzt() - timedelta(days=tage)).astimezone(konfig.ZEITZONE).strftime("%Y-%m-%d")
    docs = await db[TAGESSTATS].find({"segment_id": segment_id, "date": {"$gte": seit}, "sample_size": {"$gt": 0}},
                                     {"_id": 0, "listing_ids": 0}).sort("date", 1).to_list(5000)
    reihe = []
    vor = None
    for d in docs:
        med = d.get("median_price")
        aend_pct = round((float(med) - float(vor)) / float(vor) * 100, 2) if vor and med is not None else None
        aend_eur = round(float(med) - float(vor), 2) if vor and med is not None else None
        reihe.append({"date": d["date"], "sample_size": d.get("sample_size"), "min": d.get("min_price"),
                      "median": med, "avg": d.get("avg_price"), "max": d.get("max_price"),
                      "p25": d.get("p25_price"), "p75": d.get("p75_price"),
                      "new_in_sample": d.get("new_in_sample_today"), "price_reductions": d.get("price_reductions_today"),
                      "change_eur": aend_eur, "change_pct": aend_pct})
        vor = med if med is not None else vor
    wochen: Dict[str, List[float]] = {}
    for r in reihe:
        if r["median"] is not None:
            wochen.setdefault(_wochenschluessel(r["date"]), []).append(float(r["median"]))
    wochen_reihe = [{"woche": w, "median": round(statistics.median(v), 2), "tage": len(v)} for w, v in sorted(wochen.items())]
    aend = [r for r in reihe if r["change_eur"] is not None]
    auswertung = {
        "start_median": reihe[0]["median"] if reihe else None, "end_median": reihe[-1]["median"] if reihe else None,
        "veraenderung_eur": round(float(reihe[-1]["median"]) - float(reihe[0]["median"]), 2) if len(reihe) > 1 and reihe[0]["median"] and reihe[-1]["median"] is not None else None,
        "veraenderung_pct": round((float(reihe[-1]["median"]) - float(reihe[0]["median"])) / float(reihe[0]["median"]) * 100, 2) if len(reihe) > 1 and reihe[0]["median"] and reihe[-1]["median"] is not None else None,
        "groesster_rueckgang": min(aend, key=lambda r: r["change_eur"]) if aend else None,
        "groesster_anstieg": max(aend, key=lambda r: r["change_eur"]) if aend else None,
        "tage_fallend": sum(1 for r in aend if r["change_eur"] < 0),
        "tage_steigend": sum(1 for r in aend if r["change_eur"] > 0),
        "tage_unveraendert": sum(1 for r in aend if r["change_eur"] == 0),
        "hoechster_median": max((r["median"] for r in reihe if r["median"] is not None), default=None),
        "niedrigster_median": min((r["median"] for r in reihe if r["median"] is not None), default=None),
        "tage": len(reihe),
    }
    return {"segment_id": segment_id, "bereich": bereich, "reihe": reihe, "wochen": wochen_reihe, "auswertung": auswertung}


async def segment_listings(db, segment_id: str) -> Dict[str, Any]:
    """Das aktuelle Sample (juengster Tag) mit Rang heute/gestern und Preisaenderung seit Erstbeobachtung."""
    letzte = await db[TAGESSTATS].find_one({"segment_id": segment_id}, {"_id": 0, "date": 1}, sort=[("date", -1)])
    if not letzte:
        return {"date": None, "listings": []}
    snaps = await db[SNAPSHOTS].find({"segment_id": segment_id, "date": letzte["date"]}, {"_id": 0}).sort("rank_in_sample", 1).to_list(200)
    raus = []
    for s in snaps:
        l = await db[LISTINGS].find_one({"source": s.get("source") or konfig.QUELLE, "listing_id": s["listing_id"]},
                                        {"_id": 0, "price_history": 0})
        if not l:
            continue
        raus.append({**l, **_listing_kurz(l, s), "price_today": s.get("price"), "rank_today": s.get("rank_in_sample"),
                     "rank_yesterday": s.get("rank_yesterday"), "price_change_eur": s.get("price_change_eur"),
                     "price_change_pct": s.get("price_change_pct"), "price_rating_today": s.get("price_rating")})
    return {"date": letzte["date"], "listings": raus}


async def listing_verlauf(db, listing_id: str) -> Optional[Dict[str, Any]]:
    l = await db[LISTINGS].find_one({"source": konfig.QUELLE, "listing_id": str(listing_id)}, {"_id": 0})
    if not l:
        return None
    snaps = await db[SNAPSHOTS].find({"listing_id": str(listing_id)}, {"_id": 0}).sort("date", 1).to_list(2000)
    return {"listing": l, "snapshots": snaps, "hinweis_zustand": ZUSTAND_TEXT.get(l.get("active_state"), "")}


ZUSTAND_TEXT = {
    "seen": "zuletzt im Sample gesehen",
    "not_seen_in_sample": "zuletzt nicht mehr unter den 20 günstigsten — das heißt NICHT verkauft (kann teurer geworden oder verdrängt worden sein)",
    "verification_pending": "wird gerade einzeln nachgeprüft",
    "confirmed_removed": "Inserat nicht mehr online (kein Beleg für einen Verkauf)",
}


async def monitoring(db) -> Dict[str, Any]:
    """Technische Sicht (Auftrag v2 Nr. 48/49): Jobs heute, Zeilen/Kosten heute und
    Monat, Budget, mittlere Laufzeit, letzter Erfolg — und Alarme."""
    t = konfig.heute_tag()
    m = konfig.monat()
    jobs_heute = await db[JOBS].find({"tag": {"$regex": f"^{t}"}}, {"_id": 0, "status": 1, "actual_rows": 1, "actual_cost": 1,
                                                                  "dauer_ms": 1, "sorted_confirmed": 1, "finished_at": 1}).to_list(50000)
    def _z(st):
        return sum(1 for j in jobs_heute if j.get("status") == st)
    fertig = [j for j in jobs_heute if j.get("status") == "completed"]
    rows_heute = sum(int(j.get("actual_rows") or 0) for j in fertig)
    kosten_heute = round(sum(float(j.get("actual_cost") or 0) for j in fertig), 4)
    dauer = [int(j.get("dauer_ms") or 0) for j in fertig if j.get("dauer_ms")]
    b = await budget.dokument(db, m)
    letzter = await db[JOBS].find_one({"status": "completed"}, {"_id": 0, "finished_at": 1, "segment_id": 1}, sort=[("finished_at", -1)])
    stale_grenze = (konfig.jetzt() - timedelta(hours=48)).isoformat()
    stale = await db[SEGMENTE].count_documents({"enabled": True, "last_success_at": {"$ne": None, "$lt": stale_grenze}})
    nie = await db[SEGMENTE].count_documents({"enabled": True, "last_success_at": None,
                                              "created_at": {"$lt": stale_grenze}})
    null = sum(1 for j in fertig if int(j.get("actual_rows") or 0) == 0)
    unsortiert = sum(1 for j in fertig if j.get("sorted_confirmed") is False)
    fehlgeschlagen, gesamt = _z("failed"), len(jobs_heute)
    quote = round(fehlgeschlagen / gesamt * 100, 1) if gesamt else 0.0
    anteil = (float(b.get("used_usd") or 0) + float(b.get("reserved_usd") or 0)) / float(b.get("budget_usd") or 1) * 100 if b.get("budget_usd") else 0.0
    alarme = []
    if stale + nie:
        alarme.append({"typ": "segment_veraltet", "text": f"{stale + nie} Segment(e) seit über 48 h nicht erfolgreich aktualisiert", "stufe": "warn"})
    if gesamt >= 5 and quote >= 20:
        alarme.append({"typ": "fehlerquote", "text": f"Fehlerquote heute {quote} % ({fehlgeschlagen} von {gesamt})", "stufe": "rot"})
    if anteil >= 95:
        alarme.append({"typ": "budget_95", "text": f"Budget zu {anteil:.0f} % verbraucht", "stufe": "rot"})
    elif anteil >= 80:
        alarme.append({"typ": "budget_80", "text": f"Budget zu {anteil:.0f} % verbraucht", "stufe": "warn"})
    if null:
        alarme.append({"typ": "keine_treffer", "text": f"{null} Lauf/Läufe heute mit 0 Treffern", "stufe": "warn"})
    if unsortiert:
        alarme.append({"typ": "sortierung", "text": f"{unsortiert} Lauf/Läufe heute mit unsicherer Sortierung", "stufe": "rot"})
    if not konfig.token():
        alarme.append({"typ": "token", "text": "APIFY_TOKEN fehlt", "stufe": "rot"})
    return {"tag": t, "geplant": gesamt, "erfolgreich": len(fertig), "fehlgeschlagen": fehlgeschlagen, "wartend": _z("queued"),
            "laufend": _z("running"), "rows_heute": rows_heute, "rows_monat": int(b.get("rows") or 0),
            "kosten_heute_usd": kosten_heute, "kosten_monat_usd": round(float(b.get("used_usd") or 0), 4),
            "budget_uebrig_usd": round(float(b.get("frei_usd") or 0), 2), "budget_anteil_pct": round(anteil, 1),
            "mittlere_laufzeit_s": round(sum(dauer) / len(dauer) / 1000, 1) if dauer else None,
            "letzter_erfolg": letzter, "segmente_veraltet": stale + nie, "alarme": alarme}


async def status(db) -> Dict[str, Any]:
    return {"budget": await budget.dokument(db), "jobs": await jobs.uebersicht(db), "takt": await jobs.intervall(db),
            "monitoring": await monitoring(db),
            "modelle": await db[MODELLE].count_documents({"enabled": True}),
            "segmente": await db[SEGMENTE].count_documents({"enabled": True}),
            "listings": await db[LISTINGS].count_documents({}),
            "snapshots": await db[SNAPSHOTS].estimated_document_count(),
            "aktiv": konfig.aktiv(), "chancen_aktiv": konfig.chancen_aktiv(), "actor": konfig.actor(),
            "token_vorhanden": bool(konfig.token()), "km_buckets": await segmente.km_buckets(db),
            "ez_buckets": await segmente.ez_buckets(db), "einstellungen": await segmente.einstellungen(db)}
