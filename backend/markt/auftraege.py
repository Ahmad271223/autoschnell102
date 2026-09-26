# -*- coding: utf-8 -*-
"""Suchauftraege (Auftrag v3, 26.09.2026): der Super-Admin legt Marktanalysen
selbst an — Marke, Modell, Variante, Filter, EZ-Jahre, km-Bereiche, Zeilen,
Frequenz. Hier: Pruefung/Normalisierung eines Entwurfs, deterministische
Kostenprognose (keine KI), Testlauf mit wenigen Treffern, Anlegen/Aendern/
Duplizieren/Archivieren. Archivieren loescht NIE Historie."""
from __future__ import annotations

import math
import re
import uuid
from typing import Any, Dict, List, Optional

from markt import apify, katalog, konfig, normalisieren, segmente, url
from markt.konfig import JOBS, MODELLE

STATUS = ("active", "paused", "archived")
KRAFTSTOFFE = ("", "PETROL", "DIESEL", "HYBRID", "HYBRID_DIESEL", "ELECTRICITY", "LPG", "CNG")
GETRIEBE = ("", "MANUAL_GEAR", "AUTOMATIC_GEAR", "SEMIAUTOMATIC_GEAR")
VERKAEUFER = ("", "DEALER", "FSBO")
ROWS_MAX = 100


class Ungueltig(ValueError):
    pass


def _int(w: Any, name: str, unten: int, oben: int) -> int:
    try:
        z = int(str(w).replace(".", "").replace(" ", ""))
    except (TypeError, ValueError):
        raise Ungueltig(f"{name}: keine Zahl")
    if z < unten or z > oben:
        raise Ungueltig(f"{name}: {z} liegt außerhalb {unten}–{oben}")
    return z


def slug(*teile: str) -> str:
    s = "-".join(str(t or "") for t in teile).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or uuid.uuid4().hex[:8]


def km_bereiche_pruefen(roh: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """min < max, keine negativen Werte, keine Ueberschneidungen, sortiert."""
    if not roh:
        raise Ungueltig("mindestens ein km-Bereich")
    if len(roh) > 12:
        raise Ungueltig("höchstens 12 km-Bereiche")
    sauber = []
    for b in roh:
        mn = _int(b.get("min_km", b.get("min")), "km von", 0, 2000000)
        mx = _int(b.get("max_km", b.get("max")), "km bis", 1, 2000000)
        if mn >= mx:
            raise Ungueltig(f"km-Bereich {mn}–{mx}: von muss kleiner als bis sein")
        sauber.append({"min_km": mn, "max_km": mx})
    sauber.sort(key=lambda b: b["min_km"])
    for a, b in zip(sauber, sauber[1:]):
        if b["min_km"] <= a["max_km"]:
            raise Ungueltig(f"km-Bereiche überschneiden sich: {a['min_km']}–{a['max_km']} und {b['min_km']}–{b['max_km']}")
    return sauber


def ez_jahre_pruefen(roh: Any, von: Any = None, bis: Any = None) -> List[int]:
    """Liste einzelner Jahre (Multi-Select) oder von–bis; jedes Jahr wird ein eigenes Segment."""
    jahre: List[int] = []
    if von or bis:
        v = _int(von or bis, "EZ von", 1980, 2100)
        b = _int(bis or von, "EZ bis", 1980, 2100)
        if b < v:
            raise Ungueltig("EZ bis liegt vor EZ von")
        jahre = list(range(v, b + 1))
    else:
        for j in roh or []:
            jahre.append(_int(j, "EZ-Jahr", 1980, 2100))
    jahre = sorted(set(jahre))
    if not jahre:
        raise Ungueltig("mindestens ein EZ-Jahr")
    if len(jahre) > 15:
        raise Ungueltig("höchstens 15 EZ-Jahre")
    return jahre


def entwurf_pruefen(e: Dict[str, Any], *, bestehend: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Formular -> gueltiges market_models-Dokument (ohne id/Zeitstempel).
    Marke/Modell werden ueber den mobile.de-Katalog aufgeloest (Name oder ID)."""
    marke = str(e.get("make") or e.get("marke") or "").strip()
    modell = str(e.get("model") or e.get("modell") or "").strip()
    variante = str(e.get("variant") or e.get("variante") or "").strip()[:80]
    if not marke or not modell:
        raise Ungueltig("Marke und Modell sind Pflicht")
    ids = katalog.modell_ids(marke, modell)
    if not ids:
        raise Ungueltig(f"Modell „{modell}“ der Marke „{marke}“ nicht im mobile.de-Katalog — bitte aus der Liste wählen")
    fuel = str(e.get("fuel") or "").upper().strip()
    if fuel not in KRAFTSTOFFE:
        raise Ungueltig(f"Kraftstoff {fuel} unbekannt")
    gearbox = str(e.get("gearbox") or "").upper().strip()
    if gearbox not in GETRIEBE:
        raise Ungueltig(f"Getriebe {gearbox} unbekannt")
    seller = str(e.get("seller_type") or "").upper().strip()
    if seller not in VERKAEUFER:
        raise Ungueltig("Verkäuferart unbekannt")
    kw_von = _int(e["power_kw_min"], "kW von", 1, 2000) if e.get("power_kw_min") not in (None, "") else None
    kw_bis = _int(e["power_kw_max"], "kW bis", 1, 2000) if e.get("power_kw_max") not in (None, "") else None
    if kw_von and kw_bis and kw_bis < kw_von:
        raise Ungueltig("kW bis liegt unter kW von")
    jahre = ez_jahre_pruefen(e.get("ez_years"), e.get("ez_from"), e.get("ez_to"))
    km = km_bereiche_pruefen(e.get("km_buckets") or [])
    rows = _int(e.get("rows") or konfig.rows_je_segment(), "Zeilen je Segment", 1, ROWS_MAX)
    crawls = _int(e.get("crawls_per_day") or 1, "Abrufe je Tag", 1, 4)
    status = str(e.get("status") or (bestehend or {}).get("status") or "paused")
    if status not in STATUS:
        raise Ungueltig("Status unbekannt")
    zip_code = str(e.get("zip") or "").strip()[:10]
    radius = _int(e.get("radius_km"), "Radius", 1, 2000) if e.get("radius_km") not in (None, "") else None
    label = str(e.get("label") or "").strip() or f"{ids['make_name']} {variante or ids['model_name']}".strip()
    return {"make": ids["make_name"], "model": ids["model_name"], "variant": variante, "label": label[:120],
            "make_id": ids["make_id"], "model_id": ids["model_id"], "fuel": fuel or None, "gearbox": gearbox or None,
            "body": (str(e.get("body") or "").strip()[:40] or None), "seller_type": seller or None,
            "country": (str(e.get("country") or "DE").strip().upper()[:2] or "DE"), "zip": zip_code or None,
            "radius_km": radius, "power_kw_min": kw_von, "power_kw_max": kw_bis,
            "ez_years": jahre, "km_buckets": km, "rows": rows, "crawls_per_day": crawls,
            "status": status, "enabled": status == "active", "priority": _int(e.get("priority") or 5, "Priorität", 1, 9),
            "notiz": str(e.get("notiz") or "").strip()[:500]}


# ---------------------------------------------------------------- Prognose
def prognose_modell(m: Dict[str, Any]) -> Dict[str, Any]:
    ez = segmente.ez_buckets_fuer_modell_liste(m)
    km = m.get("km_buckets") or konfig.KM_BUCKETS_STANDARD
    rows = int(m.get("rows") or konfig.rows_je_segment())
    k = int(m.get("crawls_per_day") or 1)
    seg = len(ez) * len(km)
    rows_tag = seg * rows * k
    laeufe_tag = math.ceil(seg * k / max(1, konfig.buendel_groesse())) if seg else 0
    kosten_tag = konfig.kosten_buendel_usd(konfig.actor(), laeufe_tag, rows_tag)
    return {"segmente": seg, "ez_jahre": len(ez), "km_bereiche": len(km), "rows": rows, "crawls_per_day": k,
            "rows_tag": rows_tag, "rows_monat": round(rows_tag * 30.4), "laeufe_tag": laeufe_tag,
            "kosten_tag_usd": round(kosten_tag, 2), "kosten_monat_usd": round(kosten_tag * 30.4, 2)}


async def prognose(db, entwurf: Optional[Dict[str, Any]] = None, *, ohne_id: Optional[str] = None) -> Dict[str, Any]:
    """Alle aktiven Marktanalysen (+ optional ein ungespeicherter Entwurf, ggf.
    statt des Modells `ohne_id`) -> Segmente, Zeilen/Tag, Zeilen/Monat, Kosten,
    Vergleich mit dem Monatsbudget. Deterministisch."""
    from markt import budget
    aktive = await db[MODELLE].find({"status": "active"}, {"_id": 0}).to_list(5000)
    if ohne_id:
        aktive = [m for m in aktive if m.get("id") != ohne_id]
    teile = [prognose_modell(m) for m in aktive]
    e = prognose_modell(entwurf) if entwurf and entwurf.get("status", "active") == "active" else None
    alle = teile + ([e] if e else [])
    summe = {k: sum(t[k] for t in alle) for k in ("segmente", "rows_tag", "rows_monat", "laeufe_tag", "kosten_tag_usd", "kosten_monat_usd")}
    b = await budget.dokument(db)
    budget_usd = float(b.get("budget_usd") or 0)
    return {"aktive_modelle": len(aktive) + (1 if e else 0), **{k: round(v, 2) for k, v in summe.items()},
            "entwurf": e, "budget_usd": budget_usd, "verbraucht_usd": round(float(b.get("used_usd") or 0), 2),
            "verbleibend_usd": round(float(b.get("frei_usd") or 0), 2),
            "ueberschritten": budget_usd > 0 and summe["kosten_monat_usd"] > budget_usd,
            "preise": {"start_usd": konfig.preise_je_actor(konfig.actor())[0], "row_usd": konfig.preise_je_actor(konfig.actor())[1],
                       "buendel": konfig.buendel_groesse(), "actor": konfig.actor()}}


# ---------------------------------------------------------------- Testlauf
TESTLAUF_SEGMENTE_MAX = 20      # Nr. 39: hoechstens 20 Segmente je Testlauf (1 Start + max. 40 Zeilen)
TESTLAUF_JE_SEGMENT = 2


def _kontext(it: Dict[str, Any]) -> Optional[str]:
    ctx = it.get("inputContext")
    if isinstance(ctx, str):
        return ctx
    if isinstance(ctx, dict):
        return ctx.get("url") or ctx.get("startUrl")
    return None


def _zeile(l: Dict[str, Any], seg: Dict[str, Any]) -> Dict[str, Any]:
    jahr = re.search(r"(\d{4})", l.get("first_registration") or "")
    return {"title": l.get("title"), "make": l.get("make"), "model": l.get("model"), "variant": l.get("variant"),
            "first_registration": l.get("first_registration"), "mileage_km": l.get("mileage_km"),
            "price_gross": l.get("price_gross"), "power_kw": l.get("power_kw"), "fuel": l.get("fuel"),
            "gearbox": l.get("gearbox"), "url": l.get("url"),
            "ez_ok": bool(jahr) and int(jahr.group(1)) == seg["year_from"],
            "km_ok": l.get("mileage_km") is not None and seg["min_km"] <= int(l["mileage_km"]) <= seg["max_km"]}


async def testlauf(entwurf: Dict[str, Any], n: int = 5) -> Dict[str, Any]:
    """Review 26.09.2026 Nr. 39: EIN Buendel-Lauf ueber ALLE Segmente des Entwurfs
    (hoechstens 20, sonst die ersten 20) mit je 2 Treffern — zeigt je Segment, ob
    der Filter stimmt und ob es leer ist, bevor einen Monat lang falsche Daten
    laufen. `n` begrenzt nur die angezeigten Zeilen des ersten Segments."""
    m = entwurf_pruefen(entwurf)
    alle: List[Dict[str, Any]] = []
    for jahr in m["ez_years"]:
        for b in m["km_buckets"]:
            alle.append({"id": f"test:{jahr}:{b['min_km']}-{b['max_km']}", "min_km": b["min_km"], "max_km": b["max_km"],
                         "year_from": jahr, "year_to": jahr, "label": f"EZ {jahr} · {segmente.km_text(b)}"})
    segs = alle[:TESTLAUF_SEGMENTE_MAX]
    urls = [url.such_url(s, m) for s in segs]
    n_anzeige = max(1, min(int(n), 10))
    if len(urls) > 1:
        r = await apify.lauf(urls, TESTLAUF_JE_SEGMENT * len(urls), max_items_per_query=TESTLAUF_JE_SEGMENT)
    else:
        r = await apify.lauf(urls, n_anzeige)
    # Zuordnung Zeile -> Segment wie im Worker: ein Segment = alles, mehrere NUR ueber inputContext
    je_url: Dict[str, List[dict]] = {u: [] for u in urls}
    if len(urls) == 1:
        je_url[urls[0]] = list(r["items"])
    else:
        for it in r["items"]:
            key = _kontext(it)
            if key in je_url:
                je_url[key].append(it)
    ergebnis_segmente = []
    erste_zeilen: List[Dict[str, Any]] = []
    erste_ls: List[Dict[str, Any]] = []
    for i, (s, u) in enumerate(zip(segs, urls)):
        ls = normalisieren.listings_aus_items(je_url[u])
        zeilen = [_zeile(l, s) for l in ls]
        ergebnis_segmente.append({"label": s["label"], "anzahl": len(zeilen),
                                  "ez_ok": all(z["ez_ok"] for z in zeilen) if zeilen else None,
                                  "km_ok": all(z["km_ok"] for z in zeilen) if zeilen else None})
        if i == 0:
            erste_ls, erste_zeilen = ls[:n_anzeige], zeilen[:n_anzeige]
    return {"url": urls[0], "segment": segs[0]["label"], "anzahl": len(erste_zeilen),
            "sortiert": normalisieren.preise_aufsteigend(erste_ls),
            "alle_ez_ok": all(z["ez_ok"] for z in erste_zeilen) if erste_zeilen else None,
            "alle_km_ok": all(z["km_ok"] for z in erste_zeilen) if erste_zeilen else None,
            "usd": r.get("usd"), "dauer_ms": r.get("dauer_ms"), "actor": r.get("actor"), "zeilen": erste_zeilen,
            "segmente": ergebnis_segmente, "leer": sum(1 for s in ergebnis_segmente if s["anzahl"] == 0),
            "segmente_geprueft": len(segs), "segmente_gesamt": len(alle)}


# ---------------------------------------------------------------- Anlegen / Aendern / Duplizieren / Archivieren
async def anlegen(db, entwurf: Dict[str, Any]) -> Dict[str, Any]:
    m = entwurf_pruefen(entwurf)
    basis = slug(m["make"], m["variant"] or m["model"])
    mid = basis
    i = 2
    while await db[MODELLE].find_one({"id": mid}, {"_id": 1}):
        mid = f"{basis}-{i}"
        i += 1
    doc = {**m, "id": mid, "created_at": konfig.jetzt_iso(), "updated_at": konfig.jetzt_iso()}
    await db[MODELLE].insert_one(dict(doc))
    await segmente.synchronisieren(db)
    doc.pop("_id", None)
    return doc


async def aendern(db, model_id: str, entwurf: Dict[str, Any]) -> Dict[str, Any]:
    alt = await db[MODELLE].find_one({"id": model_id}, {"_id": 0})
    if not alt:
        raise Ungueltig("Modell nicht gefunden")
    m = entwurf_pruefen({**alt, **entwurf}, bestehend=alt)
    await db[MODELLE].update_one({"id": model_id}, {"$set": {**m, "updated_at": konfig.jetzt_iso()}})
    await segmente.synchronisieren(db)
    return await db[MODELLE].find_one({"id": model_id}, {"_id": 0})


async def status_setzen(db, model_id: str, status: str) -> Dict[str, Any]:
    if status not in STATUS:
        raise Ungueltig("Status unbekannt")
    alt = await db[MODELLE].find_one({"id": model_id}, {"_id": 0})
    if not alt:
        raise Ungueltig("Modell nicht gefunden")
    if status == "active" and not alt.get("model_id"):
        raise Ungueltig("Modell hat keine mobile.de-ID — kann nicht beobachtet werden")
    await db[MODELLE].update_one({"id": model_id}, {"$set": {"status": status, "enabled": status == "active",
                                                            "updated_at": konfig.jetzt_iso(),
                                                            **({"archived_at": konfig.jetzt_iso()} if status == "archived" else {})}})
    await segmente.synchronisieren(db)
    if status != "active":
        # wartende Jobs des Modells abbrechen — nichts loeschen, Historie bleibt
        await db[JOBS].update_many({"model_id": model_id, "status": "queued"},
                                   {"$set": {"status": "cancelled", "finished_at": konfig.jetzt_iso()}})
    return await db[MODELLE].find_one({"id": model_id}, {"_id": 0})


async def duplizieren(db, model_id: str, aenderungen: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    alt = await db[MODELLE].find_one({"id": model_id}, {"_id": 0})
    if not alt:
        raise Ungueltig("Modell nicht gefunden")
    entwurf = {k: v for k, v in alt.items() if k not in ("id", "created_at", "updated_at", "archived_at", "grund")}
    entwurf.update(aenderungen or {})
    entwurf["status"] = "paused"
    if not (aenderungen or {}).get("label"):
        entwurf["label"] = ""
    return await anlegen(db, entwurf)


async def monatsverbrauch_je_modell(db) -> Dict[str, float]:
    m = konfig.monat()
    raus: Dict[str, float] = {}
    async for row in db[JOBS].aggregate([{"$match": {"status": "completed", "tag": {"$regex": f"^{m}"}}},
                                         {"$group": {"_id": "$model_id", "usd": {"$sum": {"$ifNull": ["$actual_cost", 0]}},
                                                     "rows": {"$sum": {"$ifNull": ["$actual_rows", 0]}}}}]):
        raus[row["_id"]] = round(float(row["usd"] or 0), 4)
    return raus
