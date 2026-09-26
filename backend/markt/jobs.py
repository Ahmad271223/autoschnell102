# -*- coding: utf-8 -*-
"""Persistente Crawl-Warteschlange (market_crawl_jobs) — kein
asyncio.create_task fuer Tagesarbeit:

  * Tagesplan: je aktives Segment EIN Job je Tag (Dedupe-Schluessel
    segment_id + tag), ueber das Crawl-Fenster verteilt, Taktung aus dem Budget
  * Worker: Lease (claimed_at/lease_until), Retry mit Wartezeit, abgelaufene
    Leases zurueck in die Warteschlange — ein Neustart verliert nichts
  * Buendel (Probe 26.09.2026): mehrere Segmente in EINEM Actor-Lauf
    (maxItemsPerQuery), jede Zeile traegt inputContext = ihre Start-URL —
    die Zuordnung Zeile -> Segment ist damit eindeutig; unbekannte
    inputContexts werden verworfen, nie geraten
  * Budget: vor dem Lauf atomar reservieren (Maximum aus Standard- und
    Ersatz-Scraper, Review 26.09.2026 Nr. 10/11), danach echte Kosten
  * Zeilenfilter (Nr. 2): jede Zeile wird gegen Segment + Suchauftrag
    geprueft (EZ, km, kW, Kraftstoff, Getriebe); > 50 % verworfen = Alarm
  * Zwei Server (Nr. 12/13): Lease deckt die Buendel-Dauer, Heartbeat vor dem
    Lauf, Ergebnis nur schreiben, wenn dieser Worker den Job noch haelt
  * Nr. 47: verfallene Budgetreservierungen (Worker weg) vor jedem Claim freigeben
  * Nr. 48/51: nach dem Lauf Segment/Modell und cancel_requested erneut pruefen —
    inzwischen pausiert/archiviert: nichts speichern, Job 'cancelled', Kosten buchen
  * Nr. 52: kein Sofort-Job fuer ein inaktives Segment
  * Fehler bleiben hier: Alarm fuer den Betreiber, nie ein Einfluss auf den
    Hauptweg
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from markt import apify, budget, entfernung, konfig, normalisieren, segmente, speicher, url
from markt.konfig import JOBS, MODELLE, SEGMENTE

log = logging.getLogger(__name__)
WORKER = f"markt-{uuid.uuid4().hex[:8]}"
_STATUS = ("queued", "running", "completed", "failed", "cancelled")


async def _alarm(db, typ: str, ref: str = "", **details) -> None:
    try:
        from betrieb import alarm
        await alarm(db, typ, ref=ref, **details)
    except Exception:  # noqa: BLE001
        pass


async def _alarm_zu(db, typ: str, ref: str = "") -> None:
    try:
        from betrieb import alarm_schliessen
        await alarm_schliessen(db, typ, ref=ref)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- Taktung
async def intervall(db) -> Dict[str, Any]:
    """Wie oft kann jedes Segment gecrawlt werden, ohne das Monatsbudget zu
    sprengen? MARKT_CRAWL_INTERVALL_TAGE > 0 setzt es fest, 0 = automatisch:
    ceil(Kosten je Tag x 30,4 / Budget). Kosten je Tag rechnen mit Buendeln."""
    segs = 0
    laeufe_seg = 0          # Segment-Abrufe je Tag (Segmente x Abrufe je Tag)
    rows_alle = 0
    async for s in db[SEGMENTE].find({"enabled": True}, {"_id": 0, "max_items": 1, "crawls_per_day": 1}):
        segs += 1
        k = int(s.get("crawls_per_day") or 1)
        laeufe_seg += k
        rows_alle += int(s.get("max_items") or konfig.rows_je_segment()) * k
    rows = konfig.rows_je_segment()
    b = max(1, konfig.buendel_groesse())
    je_tag_alle = konfig.kosten_buendel_usd(konfig.actor(), math.ceil(laeufe_seg / b) if laeufe_seg else 0, rows_alle)
    budget_usd = konfig.budget_monat_usd()
    fest = konfig.crawl_intervall_tage()
    if fest > 0:
        tage = fest
    elif budget_usd <= 0 or segs == 0:
        tage = 1
    else:
        tage = max(1, math.ceil(je_tag_alle * 30.4 / budget_usd))
    je_tag = math.ceil(segs / tage) if segs else 0
    kosten_je_tag = round(je_tag_alle / tage, 4) if segs else 0.0
    # Review 26.09.2026 Nr. 10/11: nur Anzeige — was ein Tag kostet, wenn ALLES ueber den
    # (teureren) Ersatz-Scraper liefe (der Ersatz laeuft je URL einzeln, also ein Start je Segment-Abruf).
    ersatz = konfig.actor_ersatz()
    ersatz_je_tag = (round(konfig.kosten_buendel_usd(ersatz, laeufe_seg, rows_alle) / tage, 2)
                     if ersatz and segs else 0.0)
    return {"segmente": segs, "intervall_tage": tage, "segmente_je_tag": je_tag, "buendel": b,
            "rows_je_tag": round(rows_alle / tage) if segs else 0,
            "kosten_je_tag_usd": round(kosten_je_tag, 2), "kosten_je_monat_usd": round(kosten_je_tag * 30.4, 2),
            "ersatz_kosten_je_tag_usd": ersatz_je_tag, "ersatz_actor": ersatz or None,
            "budget_usd": budget_usd, "automatisch": fest == 0}


# ---------------------------------------------------------------- Tagesplan
def _job_doc(s: Dict[str, Any], tag: str, geplant_iso: str, job_type: str) -> Dict[str, Any]:
    rows = int(s.get("max_items") or konfig.rows_je_segment())
    return {"id": uuid.uuid4().hex, "segment_id": s["id"], "model_id": s.get("model_id"), "tag": tag,
            "job_type": job_type, "scheduled_at": geplant_iso, "status": "queued", "attempts": 0,
            "max_attempts": konfig.job_versuche(), "max_items": rows, "estimated_rows": rows,
            "estimated_cost": konfig.kosten_je_lauf_usd(konfig.actor(), rows), "actual_rows": None,
            "actual_cost": None, "actor_run_id": None, "error": None, "created_at": konfig.jetzt_iso(),
            "finished_at": None}


async def tagesplan(db, tag: Optional[str] = None, *, sofort: bool = False) -> Dict[str, Any]:
    """Jobs fuer die heute faelligen Segmente anlegen (idempotent): die am
    laengsten nicht geplanten zuerst, hoechstens segmente_je_tag (Taktung),
    verteilt ueber das Crawl-Fenster. sofort=True: alle faelligen ab jetzt."""
    t = tag or konfig.heute_tag()
    takt = await intervall(db)
    alle = await db[SEGMENTE].find({"enabled": True}, {"_id": 0}).to_list(20000)
    alle.sort(key=lambda s: (s.get("last_planned_tag") or "", int(s.get("priority") or 5), s["id"]))
    segs = [s for s in alle if (s.get("last_planned_tag") or "") < t][: (len(alle) if sofort else takt["segmente_je_tag"])]
    n = max(1, len(segs))
    start = konfig.jetzt() if sofort else konfig.fenster_start(t)
    dauer = timedelta(seconds=0) if sofort else konfig.fenster_dauer()
    neu = 0
    for i, s in enumerate(segs):
        geplant = start + dauer * (i / n)
        k = max(1, min(4, int(s.get("crawls_per_day") or 1)))
        # 2x taeglich = ~12 h auseinander. Review 26.09.2026 Nr. 18: nur der ERSTE Abruf liegt
        # im Fenster (fenster_von-fenster_bis); der zweite faellt damit bewusst auf den
        # Nachmittag/Abend — gewollt, die Stichproben eines Tages sollen weit auseinanderliegen.
        abstand = timedelta(hours=24 / k)
        for lauf_nr in range(k):
            schluessel = t if lauf_nr == 0 else f"{t}#{lauf_nr + 1}"
            try:
                await db[JOBS].insert_one(_job_doc(s, schluessel, (geplant + abstand * lauf_nr).isoformat(), "daily"))
                neu += 1
            except DuplicateKeyError:
                continue
        await db[SEGMENTE].update_one({"id": s["id"]}, {"$set": {"last_planned_tag": t}})
    return {"segmente": len(segs), "neu": neu, "tag": t, "intervall_tage": takt["intervall_tage"],
            "segmente_gesamt": takt["segmente"]}


async def job_sofort(db, segment_id: str) -> Dict[str, Any]:
    """Betreiber: dieses Segment jetzt crawlen (job_type manuell, eigener Dedupe-Schluessel)."""
    s = await db[SEGMENTE].find_one({"id": segment_id}, {"_id": 0})
    if not s:
        raise ValueError("Segment nicht gefunden")
    # Review 26.09.2026 Nr. 52: kein Job fuer ein deaktiviertes Segment / pausiertes Modell
    if not s.get("enabled"):
        raise ValueError("Segment inaktiv — der Suchauftrag ist pausiert, archiviert oder das Segment wurde entfernt")
    modell = await db[MODELLE].find_one({"id": s.get("model_id")}, {"_id": 0, "enabled": 1, "status": 1})
    if not modell or not modell.get("enabled"):
        raise ValueError("Segment inaktiv — der Suchauftrag ist nicht aktiv")
    doc = _job_doc(s, konfig.heute_tag() + "#" + uuid.uuid4().hex[:6], konfig.jetzt_iso(), "manual")
    await db[JOBS].insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


async def abbrechen(db, job_id: str) -> bool:
    r = await db[JOBS].update_one({"id": job_id, "status": "queued"},
                                  {"$set": {"status": "cancelled", "finished_at": konfig.jetzt_iso()}})
    return r.modified_count == 1


# ---------------------------------------------------------------- Worker
def lease_sekunden(buendelgroesse: Optional[int] = None) -> int:
    """Review 26.09.2026 Nr. 12: ein Buendel kann laenger dauern als die feste Lease
    (900 s) — Standardlauf plus Ersatzweg je URL einzeln, jeder bis lauf_zeitlimit_s.
    Lease = max(MARKT_JOB_LEASE_SEKUNDEN, (1 + Buendelgroesse) x Zeitlimit + 120 s).
    Rechnung in konfig.lease_sekunden (Nr. 47: auch die Budgetreservierung haengt daran)."""
    return konfig.lease_sekunden(buendelgroesse)


def _lease_bis(buendelgroesse: Optional[int] = None) -> str:
    return (konfig.jetzt() + timedelta(seconds=lease_sekunden(buendelgroesse))).isoformat()


async def beanspruchen(db) -> Optional[Dict[str, Any]]:
    jetzt = konfig.jetzt()
    return await db[JOBS].find_one_and_update(
        {"status": "queued", "scheduled_at": {"$lte": jetzt.isoformat()}},
        {"$set": {"status": "running", "claimed_at": jetzt.isoformat(), "worker": WORKER,
                  "lease_until": _lease_bis()},
         "$inc": {"attempts": 1}},
        sort=[("scheduled_at", 1)], projection={"_id": 0}, return_document=ReturnDocument.AFTER)


async def lease_verlaengern(db, job_ids: List[str], buendelgroesse: Optional[int] = None) -> int:
    """Heartbeat vor dem Actor-Lauf: nur Jobs, die DIESER Worker noch haelt (Nr. 13)."""
    if not job_ids:
        return 0
    r = await db[JOBS].update_many({"id": {"$in": list(job_ids)}, "worker": WORKER, "status": "running"},
                                   {"$set": {"lease_until": _lease_bis(buendelgroesse)}})
    return int(r.modified_count)


async def stale_zurueck(db) -> int:
    """Abgelaufene Leases: zurueck in die Warteschlange oder endgueltig gescheitert."""
    jetzt = konfig.jetzt_iso()
    n = 0
    async for j in db[JOBS].find({"status": "running", "lease_until": {"$lt": jetzt}},
                                 {"_id": 0, "id": 1, "attempts": 1, "lease_until": 1, "max_attempts": 1}):
        if int(j.get("attempts") or 0) >= int(j.get("max_attempts") or konfig.job_versuche()):
            r = await db[JOBS].update_one({"id": j["id"], "status": "running", "lease_until": j["lease_until"]},
                                          {"$set": {"status": "failed", "error": "Lease abgelaufen (Prozess weg?)",
                                                    "finished_at": jetzt}})
        else:
            r = await db[JOBS].update_one({"id": j["id"], "status": "running", "lease_until": j["lease_until"]},
                                          {"$set": {"status": "queued", "scheduled_at": jetzt, "error": "Lease abgelaufen — erneut"},
                                           "$unset": {"claimed_at": "", "lease_until": "", "worker": ""}})
        n += int(r.modified_count)
    return n


def _meiner(job: Dict[str, Any]) -> Dict[str, Any]:
    """Review 26.09.2026 Nr. 13: Ergebnis nur schreiben, wenn DIESER Worker den Job noch
    haelt. Ist die Lease abgelaufen und ein anderer Server hat ihn uebernommen, darf das
    alte Ergebnis dessen Stand nicht ueberschreiben."""
    return {"id": job["id"], "status": "running", "worker": WORKER}


async def _scheitern(db, job: Dict[str, Any], grund: str, *, endgueltig: bool) -> bool:
    jetzt = konfig.jetzt()
    if endgueltig or int(job.get("attempts") or 0) >= int(job.get("max_attempts") or konfig.job_versuche()):
        r = await db[JOBS].update_one(_meiner(job), {"$set": {"status": "failed", "error": grund[:300], "finished_at": jetzt.isoformat()},
                                                    "$unset": {"lease_until": ""}})
        if r.modified_count == 0:
            log.warning("Job %s inzwischen von anderem Worker uebernommen — Fehler nicht geschrieben", job["id"])
            return False
        await _alarm(db, "markt_crawl_fehlgeschlagen", ref=job["segment_id"], grund=grund[:200], job=job["id"])
        return True
    warte = timedelta(minutes=10 * int(job.get("attempts") or 1))
    r = await db[JOBS].update_one(_meiner(job), {"$set": {"status": "queued", "error": grund[:300],
                                                          "scheduled_at": (jetzt + warte).isoformat()},
                                                "$unset": {"lease_until": "", "claimed_at": "", "worker": ""}})
    if r.modified_count == 0:
        log.warning("Job %s inzwischen von anderem Worker uebernommen — Wiederholung nicht geschrieben", job["id"])
        return False
    return True


async def _fertig(db, job: Dict[str, Any], **felder) -> bool:
    r = await db[JOBS].update_one(_meiner(job), {"$set": {"status": "completed", "finished_at": konfig.jetzt_iso(),
                                                          "error": None, **felder},
                                                "$unset": {"lease_until": ""}})
    if r.modified_count == 0:
        log.warning("Job %s inzwischen von anderem Worker uebernommen — Ergebnis nicht ueberschrieben", job["id"])
        return False
    return True


async def _abbrechen(db, job: Dict[str, Any], grund: str, **felder) -> bool:
    """Review 26.09.2026 Nr. 48/51: Job nach dem Lauf als 'cancelled' abschliessen (Segment/
    Modell inzwischen pausiert/archiviert oder cancel_requested) — Kosten werden mitgeschrieben,
    weil Apify sie berechnet hat; Zeilen wurden NICHT gespeichert."""
    r = await db[JOBS].update_one(_meiner(job), {"$set": {"status": "cancelled", "finished_at": konfig.jetzt_iso(),
                                                          "error": grund[:300], **felder},
                                                "$unset": {"lease_until": ""}})
    if r.modified_count == 0:
        log.warning("Job %s inzwischen von anderem Worker uebernommen — Abbruch nicht geschrieben", job["id"])
        return False
    return True


async def _grundlagen(db, job: Dict[str, Any]):
    seg = await db[SEGMENTE].find_one({"id": job["segment_id"]}, {"_id": 0})
    modell = await db[MODELLE].find_one({"id": (seg or {}).get("model_id")}, {"_id": 0}) if seg else None
    if not seg or not seg.get("enabled") or not modell or not modell.get("enabled"):
        return None
    return seg, modell


async def _noch_gewollt(db, job: Dict[str, Any]) -> Optional[str]:
    """Nach dem Actor-Lauf, vor dem Speichern (Nr. 48/51): ist das Segment/Modell inzwischen
    deaktiviert, pausiert oder archiviert, oder hat status_setzen cancel_requested gesetzt?
    None = weiter; sonst der Grund."""
    j = await db[JOBS].find_one({"id": job["id"]}, {"_id": 0, "cancel_requested": 1, "cancel_grund": 1})
    if j and j.get("cancel_requested"):
        return str(j.get("cancel_grund") or "Abbruch angefordert (Suchauftrag pausiert/archiviert)")
    seg = await db[SEGMENTE].find_one({"id": job["segment_id"]}, {"_id": 0, "enabled": 1, "model_id": 1})
    if not seg or not seg.get("enabled"):
        return "Segment während des Laufs deaktiviert"
    modell = await db[MODELLE].find_one({"id": seg.get("model_id")}, {"_id": 0, "enabled": 1, "status": 1})
    if not modell or not modell.get("enabled") or (modell.get("status") and modell.get("status") != "active"):
        return f"Suchauftrag während des Laufs {(modell or {}).get('status') or 'deaktiviert'}"
    return None


FILTER_ALARM_MIN_ZEILEN = 3     # Nr. 2: Alarm erst ab 3 gelieferten Zeilen und > 50 % verworfen


def reservierung_usd(laeufe_plan: int, rows_gesamt: int) -> float:
    """Review 26.09.2026 Nr. 10/11: reserviert wird das Maximum aus Standardkosten (ein
    Buendel-Lauf) und Ersatzkosten — der Ersatz-Scraper laeuft je URL einzeln (ein Start je
    Segment) und ist deutlich teurer; sonst sprengt der Ersatzweg das Monatsbudget.
    Abgerechnet werden nachher die echten Kosten."""
    standard = konfig.kosten_buendel_usd(konfig.actor(), 1, rows_gesamt)
    ersatz = konfig.actor_ersatz()
    if not ersatz:
        return standard
    return max(standard, konfig.kosten_buendel_usd(ersatz, max(1, int(laeufe_plan)), rows_gesamt))


def _kontext(it: Dict[str, Any]) -> Optional[str]:
    ctx = it.get("inputContext")
    if isinstance(ctx, str):
        return ctx
    if isinstance(ctx, dict):
        return ctx.get("url") or ctx.get("startUrl")
    return None


async def verarbeiten_buendel(db, jobs_liste: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Mehrere Jobs (Segmente) in EINEM Actor-Lauf. Jede Zeile wird ueber
    inputContext (= Start-URL) genau ihrem Segment zugeordnet."""
    plan: List[Dict[str, Any]] = []
    for job in jobs_liste:
        g = await _grundlagen(db, job)
        if not g:
            await _fertig(db, job, actual_rows=0, actual_cost=0.0, error="übersprungen (Segment/Modell inaktiv)")
            continue
        seg, modell = g
        plan.append({"job": job, "seg": seg, "modell": modell, "url": url.such_url(seg, modell),
                     "max_items": int(seg.get("max_items") or konfig.rows_je_segment())})
    if not plan:
        return {"status": "uebersprungen", "jobs": len(jobs_liste)}
    rows_gesamt = sum(p["max_items"] for p in plan)
    je_query = max(p["max_items"] for p in plan)
    jetzt = konfig.jetzt_iso()
    for p in plan:
        await db[SEGMENTE].update_one({"id": p["seg"]["id"]}, {"$set": {"last_attempt_at": jetzt}})
    res = await budget.reservieren(db, reservierung_usd(len(plan), rows_gesamt))
    if res is None:
        for p in plan:
            await _scheitern(db, p["job"], "Monatsbudget des Market-Crawlers aufgebraucht", endgueltig=True)
        await _alarm(db, "markt_budget_voll", ref=konfig.monat(), budget=konfig.budget_monat_usd())
        return {"status": "budget", "jobs": len(plan)}
    # Nr. 12: Heartbeat — Lease fuer alle Jobs des Buendels auf die Buendel-Dauer verlaengern
    await lease_verlaengern(db, [p["job"]["id"] for p in plan], len(plan))
    try:
        r = await apify.lauf_mit_ersatz([p["url"] for p in plan], rows_gesamt,
                                        max_items_per_query=je_query if len(plan) > 1 else None)
    except apify.ApifyFehler as e:
        gelaufen = e.art in ("zeit", "ausfall")
        await budget.abrechnen(db, res, None if gelaufen else 0.0, 0, gelaufen=gelaufen)
        for p in plan:
            await _scheitern(db, p["job"], f"Apify {e.art}: {e.detail}", endgueltig=e.art in ("token", "guthaben"))
        if e.art in ("token", "guthaben"):
            await _alarm(db, "markt_apify_" + e.art, ref="crawler", detail=e.detail[:200])
        return {"status": "fehler", "art": e.art, "jobs": len(plan)}
    except Exception as e:  # noqa: BLE001
        log.exception("Market-Buendel gescheitert")
        await budget.abrechnen(db, res, None, 0)
        for p in plan:
            await _scheitern(db, p["job"], f"Fehler: {e}"[:300], endgueltig=False)
        return {"status": "fehler", "jobs": len(plan)}
    # Zuordnung Zeile -> Segment: ein Segment = alles; mehrere = NUR ueber inputContext == Start-URL
    je_url: Dict[str, List[dict]] = {p["url"]: [] for p in plan}
    unbekannt = 0
    if len(plan) == 1:
        je_url[plan[0]["url"]] = list(r["items"])
    else:
        for it in r["items"]:
            key = _kontext(it)
            if key in je_url:
                je_url[key].append(it)
            else:
                unbekannt += 1
    if unbekannt:
        await _alarm(db, "markt_zuordnung_unklar", ref=str(r.get("run_id") or ""), zeilen=unbekannt)
    ergebnisse = []
    kosten = r.get("usd")
    start_usd, row_usd = konfig.preise_je_actor(r.get("actor") or konfig.actor())
    vorbereitet = []
    for p in plan:
        geliefert = normalisieren.listings_aus_items(je_url[p["url"]])
        # Review 26.09.2026 Nr. 2: jede Zeile gegen Segment + Suchauftrag pruefen (EZ, km, kW,
        # Kraftstoff, Getriebe) — unpassende verwerfen, nie als "guenstigstes Angebot" fuehren.
        listings, gruende = [], []
        for l in geliefert:
            ok, grund = normalisieren.passt_zum_segment(l, p["seg"], p["modell"])
            if ok:
                listings.append(l)
            else:
                gruende.append(grund)
        listings = listings[: p["max_items"]]
        vorbereitet.append((p, listings, normalisieren.preise_aufsteigend(listings), len(geliefert), gruende))
    # Kosten rechnen mit den GELIEFERTEN Zeilen (Apify bucht auch verworfene)
    gesamt_rows = sum(n for _, _, _, n, _ in vorbereitet)
    # Befund 26.09.2026: Kosten je Job (Start anteilig + Zeilen) und Monatszaehler liefen
    # auseinander. Beide rechnen jetzt mit derselben Summe: mindestens Start + Zeilen,
    # hoeher nur, wenn Apify mehr gebucht hat — dann anteilig auf die Jobs verteilt.
    rechnerisch = round(start_usd + gesamt_rows * row_usd, 4)
    kosten_gesamt = None if kosten is None else max(float(kosten), rechnerisch)
    faktor = (kosten_gesamt / rechnerisch) if (kosten_gesamt and rechnerisch) else 1.0
    for p, listings, sortiert, geliefert_n, gruende in vorbereitet:
        verworfen = len(gruende)
        if geliefert_n >= FILTER_ALARM_MIN_ZEILEN and verworfen * 2 > geliefert_n:
            # mehr als die Haelfte passt nicht zum Segment: mobile.de hat den Filter ignoriert
            await _alarm(db, "markt_filter_ignoriert", ref=p["seg"]["id"], actor=str(r.get("actor") or ""),
                         verworfen=verworfen, geliefert=geliefert_n, job=p["job"]["id"],
                         gruende="; ".join(gruende[:5]))
        elif verworfen == 0 and geliefert_n:
            await _alarm_zu(db, "markt_filter_ignoriert", ref=p["seg"]["id"])
        anteil = None if kosten_gesamt is None else round((start_usd / len(plan) + geliefert_n * row_usd) * faktor, 4)
        # Nr. 48/51: Segment/Modell inzwischen pausiert/archiviert oder Abbruch angefordert ->
        # Zeilen NICHT speichern, Job 'cancelled' mit Grund; Kosten trotzdem (Apify hat sie berechnet)
        grund = await _noch_gewollt(db, p["job"])
        if grund:
            await _abbrechen(db, p["job"], grund, actual_rows=0, gelieferte_rows=geliefert_n, actual_cost=anteil,
                             actor_run_id=r.get("run_id"), run_id=r.get("run_id"), actor=r.get("actor"),
                             dauer_ms=r.get("dauer_ms"), buendel=len(plan))
            ergebnisse.append({"status": "cancelled", "grund": grund, "sample_size": 0})
            continue
        if not sortiert:
            listings.sort(key=lambda x: x["price_gross"])
            await _alarm(db, "markt_sortierung_unsicher", ref=p["seg"]["id"], job=p["job"]["id"])
        try:
            erg = await speicher.verarbeiten(db, p["seg"], listings, sortiert_bestaetigt=sortiert,
                                             lauf_tag=p["job"].get("tag"))
        except Exception as e:  # noqa: BLE001
            log.exception("Market-Speicher %s gescheitert", p["job"]["id"])
            await _scheitern(db, p["job"], f"Speichern: {e}"[:300], endgueltig=False)
            continue
        # 0 Treffer in EINEM Segment ist eine Marktluecke (z. B. EZ 2019 mit 0-50k km), kein
        # Fehler — Befund 26.09.2026: 32 Betriebsalarme an einem Tag. Wir merken es nur am
        # Segment (leer_in_folge) und alarmieren erst, wenn ein ganzer Lauf leer bleibt.
        if not listings:
            await db[SEGMENTE].update_one({"id": p["seg"]["id"]}, {"$inc": {"leer_in_folge": 1}, "$set": {"last_rows": 0}})
        else:
            await db[SEGMENTE].update_one({"id": p["seg"]["id"]}, {"$set": {"leer_in_folge": 0, "last_rows": len(listings)}})
            await _alarm_zu(db, "markt_keine_treffer", ref=p["seg"]["id"])
        await _fertig(db, p["job"], actual_rows=len(listings), gelieferte_rows=geliefert_n, verworfen_filter=verworfen,
                      actual_cost=anteil,
                      actor_run_id=r.get("run_id"), run_id=r.get("run_id"), actor=r.get("actor"),
                      ersatz_grund=r.get("ersatz_grund"), dauer_ms=r.get("dauer_ms"), sorted_confirmed=sortiert,
                      buendel=len(plan), ergebnis=erg)
        await _alarm_zu(db, "markt_crawl_fehlgeschlagen", ref=p["seg"]["id"])
        ergebnisse.append(erg)
    if len(plan) >= 2 and gesamt_rows == 0 and not unbekannt:
        # Ein ganzer Buendel-Lauf ohne eine einzige Zeile: Scraper/Sperre/URL-Form — das ist ein Fehler.
        await _alarm(db, "markt_lauf_leer", ref=str(r.get("run_id") or ""), segmente=len(plan), actor=str(r.get("actor") or ""))
    await budget.abrechnen(db, res, kosten_gesamt, gesamt_rows)
    return {"status": "ok", "jobs": len(plan), "rows": gesamt_rows, "usd": kosten_gesamt, "ergebnisse": ergebnisse}


async def verarbeiten(db, job: Dict[str, Any]) -> Dict[str, Any]:
    """Ein einzelner Job (ohne Buendel)."""
    erg = await verarbeiten_buendel(db, [job])
    if erg.get("ergebnisse"):
        return {"status": "ok", **erg["ergebnisse"][0]}
    return erg


async def einmal(db) -> Dict[str, Any]:
    """Ein Worker-Takt: Stale zurueck, verfallene Budgetreservierungen freigeben (Nr. 47),
    faellige Jobs in Buendeln verarbeiten (im Vordergrund)."""
    await stale_zurueck(db)
    try:
        await budget.verfallene_freigeben(db)
    except Exception:  # noqa: BLE001
        log.exception("Budget-Reaper gescheitert")
    erledigt = 0
    while True:
        buendel: List[Dict[str, Any]] = []
        while len(buendel) < konfig.buendel_groesse():
            job = await beanspruchen(db)
            if not job:
                break
            buendel.append(job)
        if not buendel:
            break
        await verarbeiten_buendel(db, buendel)
        erledigt += len(buendel)
        if len(buendel) < konfig.buendel_groesse():
            break
    return {"erledigt": erledigt}


async def worker_forever(db, erfolg: Optional[Callable[[], None]] = None, takt_s: int = 20) -> None:
    """Dauerschleife je Prozess (Claims sind atomar, mehrere Prozesse sind ok).
    Tagesplan und Entfernungs-Pruefung einmal je Tag ueber eine Mongo-Sperre."""
    await asyncio.sleep(15)
    geplant_fuer = ""
    while True:
        try:
            if erfolg:
                erfolg()
            if not await konfig.crawler_aktiv(db) or not konfig.token():
                await asyncio.sleep(60)
                continue
            try:
                import wartung
                if await wartung.aktiv_async(db):
                    await asyncio.sleep(60)
                    continue
            except Exception:  # noqa: BLE001
                pass
            tag = konfig.heute_tag()
            if geplant_fuer != tag:
                from job_lock import acquire
                token = await acquire(db, f"markt-plan-{tag}", ttl_seconds=20 * 3600)
                if token:
                    await segmente.synchronisieren(db)
                    await tagesplan(db, tag)
                geplant_fuer = tag
            await einmal(db)
            await entfernung.taeglich(db)
        except Exception:  # noqa: BLE001
            log.exception("Market-Worker-Takt gescheitert")
        await asyncio.sleep(takt_s)


async def uebersicht(db, tag: Optional[str] = None) -> Dict[str, Any]:
    t = tag or konfig.heute_tag()
    raus: Dict[str, Any] = {"tag": t, "aktiv": await konfig.crawler_aktiv(db)}
    for s in _STATUS:
        raus[s] = await db[JOBS].count_documents({"tag": {"$regex": f"^{t}"}, "status": s})
    naechster = await db[JOBS].find_one({"status": "queued"}, {"_id": 0, "scheduled_at": 1, "segment_id": 1},
                                        sort=[("scheduled_at", 1)])
    raus["naechster"] = naechster
    raus["letzte"] = await db[JOBS].find({"status": {"$in": ["completed", "failed"]}}, {"_id": 0, "ergebnis": 0})\
        .sort("finished_at", -1).to_list(10)
    raus["fehler_offen"] = raus["failed"]
    return raus
