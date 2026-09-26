# -*- coding: utf-8 -*-
"""Monatsbudget des Market-Crawlers (eigen, getrennt vom KI-Budget):
market_crawler_budget je Monat mit budget_usd, reserved_usd, used_usd,
rows, runs. Vor einem Lauf wird geschaetzt reserviert (atomar, $expr),
nach dem Lauf mit den echten Kosten abgerechnet. Voll = keine weiteren
automatischen Crawls; alles andere in AutoSchnell laeuft weiter.

Review 26.09.2026 Nr. 47: jede Reservierung ist ein Eintrag
{id, usd, expires_at} im Array 'reservierungen' des Monatsdokuments;
reserved_usd ist die Summe der offenen Eintraege ($inc beim Anlegen und
Loesen). Stirbt der Worker zwischen Reservieren und Abrechnen, gibt
verfallene_freigeben() den Eintrag nach Ablauf (Lease + 60 s) wieder frei —
Aufruf vor jedem Claim (jobs.einmal) und im Aufraeumlauf.

Reparaturwelle 6 (Review 26.09.2026 abends):
  * Nr. 94: das im Admin gesetzte Budget steht in market_config/budget
    (monthly_budget_usd) und wird fuer jeden NEUEN Monat uebernommen; die
    Umgebung (MARKT_BUDGET_MONAT_USD) gilt nur, solange nichts gesetzt ist
  * Nr. 104/105: reservieren(monat=...) — verspaetete Jobs buchen im Monat
    ihres Job-Tags, nicht im Monat der Ausfuehrung
  * Nr. 115: kosten_abgleich — Apify bucht Zeilen erst nach dem Lauf; fuer
    Laeufe der letzten 24 h wird usageTotalUsd erneut abgerufen und die
    Differenz auf Budget und Jobs gebucht (kosten_abgeglichen)"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

from pymongo import ReturnDocument

from markt import konfig
from markt.konfig import JOBS

log = logging.getLogger(__name__)


async def budget_vorgabe(db) -> float:
    """Nr. 94: Vorbelegung fuer ein neues Monatsdokument — Admin-Wert vor Umgebung."""
    try:
        doc = await db[konfig.KONFIG].find_one({"_id": konfig.BUDGET_DOK}, {"_id": 0, "monthly_budget_usd": 1})
    except Exception:  # noqa: BLE001
        doc = None
    if doc and doc.get("monthly_budget_usd") is not None:
        try:
            return float(doc["monthly_budget_usd"])
        except (TypeError, ValueError):
            pass
    return konfig.budget_monat_usd()


async def dokument(db, monat: Optional[str] = None) -> Dict[str, Any]:
    m = monat or konfig.monat()
    vorgabe = await budget_vorgabe(db)
    await db[konfig.BUDGET].update_one(
        {"_id": m},
        {"$setOnInsert": {"budget_usd": vorgabe, "reserved_usd": 0.0, "used_usd": 0.0,
                          "rows": 0, "runs": 0, "reservierungen": [], "angelegt": konfig.jetzt_iso()}},
        upsert=True)
    d = await db[konfig.BUDGET].find_one({"_id": m}) or {}
    d["frei_usd"] = round(float(d.get("budget_usd") or 0) - float(d.get("used_usd") or 0) - float(d.get("reserved_usd") or 0), 4)
    d["reservierungen_offen"] = len(d.get("reservierungen") or [])
    return d


async def budget_setzen(db, budget_usd: float, monat: Optional[str] = None) -> None:
    """Budget des Monats setzen UND — ohne ausdruecklichen Monat (Admin: der laufende) — als Vorgabe
    fuer kommende Monate merken (Nr. 94). Ein Wert fuer einen bestimmten anderen Monat aendert die
    Vorgabe nicht."""
    await dokument(db, monat)
    await db[konfig.BUDGET].update_one({"_id": monat or konfig.monat()}, {"$set": {"budget_usd": float(budget_usd)}})
    if monat is None:
        await db[konfig.KONFIG].update_one({"_id": konfig.BUDGET_DOK},
                                           {"$set": {"monthly_budget_usd": float(budget_usd), "updated_at": konfig.jetzt_iso()}},
                                           upsert=True)


async def reservieren(db, est_usd: float, *, ablauf_s: Optional[int] = None,
                      monat: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """{"monat", "est_usd", "id", "expires_at"} oder None (Budget voll). Atomar: nur wenn
    used + reserved + est <= budget. ablauf_s: Verfall der Reservierung (Standard
    konfig.reservierung_ablauf_s = Lease + 60 s). monat (Nr. 105): Monat des Job-Tags."""
    m = monat or konfig.monat()
    await dokument(db, m)
    est = round(float(est_usd), 4)
    rid = uuid.uuid4().hex
    jetzt = konfig.jetzt()
    ablauf = (jetzt + timedelta(seconds=int(ablauf_s if ablauf_s is not None else konfig.reservierung_ablauf_s()))).isoformat()
    eintrag = {"id": rid, "usd": est, "angelegt": jetzt.isoformat(), "expires_at": ablauf}
    doc = await db[konfig.BUDGET].find_one_and_update(
        {"_id": m, "$expr": {"$lte": [{"$add": [{"$ifNull": ["$used_usd", 0]}, {"$ifNull": ["$reserved_usd", 0]}, est]},
                                      {"$ifNull": ["$budget_usd", 0]}]}},
        {"$inc": {"reserved_usd": est}, "$set": {"stand": jetzt.isoformat()}, "$push": {"reservierungen": eintrag}},
        return_document=ReturnDocument.AFTER)
    if doc is None:
        return None
    return {"monat": m, "est_usd": est, "id": rid, "expires_at": ablauf}


async def abrechnen(db, reservierung: Optional[Dict[str, Any]], tatsaechlich_usd: Optional[float],
                    rows: int = 0, gelaufen: bool = True, runs: Optional[int] = None) -> None:
    """Reservierung durch echte Kosten ersetzen (None = Schaetzung behalten).
    Loest genau DIESE Reservierung (Nr. 47). Hat der Reaper sie inzwischen freigegeben,
    wird reserved_usd nicht ein zweites Mal gesenkt — die echten Kosten zaehlen trotzdem.
    Reparaturwelle 5 Nr. 56/57: `runs` = Zahl der ECHTEN Actor-Starts (Standard + Ersatz je URL),
    `rows` = gelieferte Datensatz-Zeilen (roh, vor Dedupe/Filter) — so, wie Apify sie berechnet."""
    if not reservierung:
        return
    est = float(reservierung.get("est_usd") or 0)
    real = round(float(tatsaechlich_usd if tatsaechlich_usd is not None else (est if gelaufen else 0.0)), 4)
    starts = int(runs) if runs is not None else (1 if gelaufen else 0)
    verbrauch = {"used_usd": real, "rows": int(rows), "runs": max(0, starts)}
    rid = reservierung.get("id")
    if rid:
        r = await db[konfig.BUDGET].update_one(
            {"_id": reservierung["monat"], "reservierungen.id": rid},
            {"$inc": {"reserved_usd": -est, **verbrauch}, "$pull": {"reservierungen": {"id": rid}}})
        if r.matched_count == 1:
            return
        # Reservierung schon verfallen/freigegeben: nur den Verbrauch buchen
        await db[konfig.BUDGET].update_one({"_id": reservierung["monat"]}, {"$inc": verbrauch})
        return
    # alte Reservierung ohne id (vor Nr. 47): wie bisher
    await db[konfig.BUDGET].update_one({"_id": reservierung["monat"]}, {"$inc": {"reserved_usd": -est, **verbrauch}})


async def verfallene_freigeben(db) -> Dict[str, Any]:
    """Reaper (Nr. 47): abgelaufene Reservierungen loesen (je Eintrag atomar: $pull + $inc),
    danach reserved_usd = Summe der offenen Eintraege — falls beide Zahlen auseinanderlaufen
    (nur wenn sich das Array zwischenzeitlich nicht geaendert hat, CAS auf das Array)."""
    jetzt = konfig.jetzt_iso()
    freigegeben: List[Dict[str, Any]] = []
    korrigiert = 0
    async for d in db[konfig.BUDGET].find({"reservierungen.0": {"$exists": True}}, {"reservierungen": 1, "reserved_usd": 1}):
        for e in list(d.get("reservierungen") or []):
            if str(e.get("expires_at") or "") >= jetzt:
                continue
            r = await db[konfig.BUDGET].update_one(
                {"_id": d["_id"], "reservierungen.id": e.get("id")},
                {"$pull": {"reservierungen": {"id": e.get("id")}}, "$inc": {"reserved_usd": -float(e.get("usd") or 0)},
                 "$set": {"letzter_verfall": jetzt}})
            if r.matched_count == 1:
                freigegeben.append({"monat": d["_id"], "id": e.get("id"), "usd": float(e.get("usd") or 0)})
    # reserved_usd gegen die offenen Eintraege abgleichen (alle Monatsdokumente)
    async for d in db[konfig.BUDGET].find({}, {"reservierungen": 1, "reserved_usd": 1}):
        offen = list(d.get("reservierungen") or [])
        summe = round(sum(float(e.get("usd") or 0) for e in offen), 4)
        if abs(float(d.get("reserved_usd") or 0) - summe) < 0.00005:
            continue
        r = await db[konfig.BUDGET].update_one({"_id": d["_id"], "reservierungen": offen}, {"$set": {"reserved_usd": summe}})
        korrigiert += int(r.modified_count)
    return {"freigegeben": len(freigegeben), "usd": round(sum(f["usd"] for f in freigegeben), 4), "korrigiert": korrigiert}


# ---------------------------------------------------------------- Nr. 115: Kostenabstimmung mit Apify
ABGLEICH_FENSTER_H = 24         # Laeufe der letzten 24 h
ABGLEICH_FRUEHESTENS_MIN = 10   # Apify bucht Zeilen erst kurz nach dem Lauf — nicht frueher abgleichen
ABGLEICH_MAX_JE_LAUF = 200      # hoechstens so viele Jobs je Aufraeumlauf (API-Aufrufe)


async def kosten_abgleich(db, *, lauf_dokument=None) -> Dict[str, Any]:
    """Aufraeumschritt: fuer abgeschlossene Jobs mit actor_run_id (letzte 24 h, noch nicht
    abgeglichen) usageTotalUsd der Laeufe erneut abrufen; die Differenz zur gebuchten Summe
    wird anteilig auf die Jobs desselben Laufs verteilt und im Budget (Monat des Job-Tags)
    nachgebucht. Wirft nie; ohne Token passiert nichts."""
    from markt import apify
    holen = lauf_dokument or apify.lauf_dokument
    if not konfig.token() and lauf_dokument is None:
        return {"geprueft": 0, "gebucht": 0, "differenz_usd": 0.0, "uebersprungen": "kein Token"}
    jetzt = konfig.jetzt()
    seit = (jetzt - timedelta(hours=ABGLEICH_FENSTER_H)).isoformat()
    bis = (jetzt - timedelta(minutes=ABGLEICH_FRUEHESTENS_MIN)).isoformat()
    jobs = await db[JOBS].find({"actor_run_id": {"$nin": [None, ""]}, "actual_cost": {"$ne": None},
                                "kosten_abgeglichen": {"$ne": True}, "finished_at": {"$gte": seit, "$lte": bis},
                                "status": {"$in": ["completed", "cancelled", "data_invalid", "failed"]}},
                               {"_id": 0, "id": 1, "actor_run_id": 1, "actual_cost": 1, "tag": 1, "actor": 1}).to_list(ABGLEICH_MAX_JE_LAUF)
    gruppen: Dict[str, List[Dict[str, Any]]] = {}
    for j in jobs:
        gruppen.setdefault(str(j["actor_run_id"]), []).append(j)
    geprueft = gebucht = 0
    differenz_gesamt = 0.0
    fehler = 0
    for run_ids, liste in gruppen.items():
        echt = 0.0
        ok = True
        for rid in [x for x in run_ids.split(",") if x]:
            try:
                d = await holen(rid)
            except Exception:  # noqa: BLE001
                ok = False
                break
            if not d or d.get("usageTotalUsd") is None:
                ok = False
                break
            echt += float(d.get("usageTotalUsd") or 0)
        geprueft += len(liste)
        if not ok:
            fehler += 1
            continue
        bisher = round(sum(float(j.get("actual_cost") or 0) for j in liste), 4)
        diff = round(echt - bisher, 4)
        monat = konfig.monat_aus_tag(str(liste[0].get("tag") or konfig.heute_tag()).split("#")[0])
        if abs(diff) >= 0.0005:
            # anteilig nach den bisherigen Jobkosten verteilen (Summe der Anteile = diff)
            basis = bisher or float(len(liste))
            for j in liste:
                anteil = round(diff * (float(j.get("actual_cost") or 0) if bisher else 1.0) / basis, 4)
                await db[JOBS].update_one({"id": j["id"]}, {"$set": {"kosten_abgeglichen": True, "kosten_abgleich_diff": anteil,
                                                                     "kosten_abgeglichen_at": jetzt.isoformat()},
                                                            "$inc": {"actual_cost": anteil}})
            await dokument(db, monat)
            await db[konfig.BUDGET].update_one({"_id": monat}, {"$inc": {"used_usd": diff, "abgleich_usd": diff}})
            differenz_gesamt = round(differenz_gesamt + diff, 4)
            gebucht += len(liste)
        else:
            await db[JOBS].update_many({"id": {"$in": [j["id"] for j in liste]}},
                                       {"$set": {"kosten_abgeglichen": True, "kosten_abgleich_diff": 0.0,
                                                 "kosten_abgeglichen_at": jetzt.isoformat()}})
    return {"geprueft": geprueft, "gebucht": gebucht, "differenz_usd": differenz_gesamt, "laeufe": len(gruppen), "fehler": fehler}
