# -*- coding: utf-8 -*-
"""Persistente Crawl-Warteschlange (market_crawl_jobs) — kein
asyncio.create_task fuer Tagesarbeit:

  * Tagesplan: je aktives Segment EIN Job je Tag (Dedupe-Schluessel
    segment_id + tag), in Buendel-Slots ueber das Crawl-Fenster verteilt,
    Taktung aus Restbudget und Restmonat
  * Worker: Lease (claimed_at/lease_until), Retry mit Wartezeit, abgelaufene
    Leases zurueck in die Warteschlange — ein Neustart verliert nichts
  * Buendel (Probe 26.09.2026): mehrere Segmente in EINEM Actor-Lauf
    (maxItemsPerQuery), jede Zeile traegt inputContext = ihre Start-URL —
    die Zuordnung Zeile -> Segment ist damit eindeutig; unbekannte
    inputContexts werden verworfen, nie geraten
  * Budget: vor dem Lauf atomar reservieren (Standard + Ersatz-Scraper,
    Reparaturwelle 5 Nr. 13), danach echte Kosten
  * Zeilenfilter (Nr. 2): jede Zeile wird gegen Segment + Suchauftrag
    geprueft (EZ, km, kW, Kraftstoff, Getriebe); > 50 % verworfen = Alarm
  * Zwei Server (Nr. 12/13, Welle 5 Nr. 35/36): Lease deckt die Buendel-Dauer,
    Heartbeat vor dem Lauf (verlorene Jobs fliegen aus dem Plan), Ergebnis
    nur schreiben, wenn dieser Worker den Job noch haelt
  * Nr. 47: verfallene Budgetreservierungen (Worker weg) vor jedem Claim freigeben
  * Nr. 48/51 + Welle 5 Nr. 9: nach dem Lauf Segment/Modell, cancel_requested
    und die Auftragsfassung (definition_hash/version) erneut pruefen —
    inzwischen geaendert: nichts speichern, Job 'cancelled', Kosten buchen
  * Nr. 52: kein Sofort-Job fuer ein inaktives Segment
  * Welle 5 Nr. 1: Top-N-Nachweis ueber searchPosition (sonst 'nur_monoton')
  * Welle 5 Nr. 17: rows + Puffer abrufen, auf rows kuerzen
  * Welle 5 Nr. 21/22: Buendel-Slots im Tagesplan, Worker wartet bis 60 s auf Nachzuegler
  * Welle 5 Nr. 23/24: Crawler aus storniert wartende Jobs; alte Jobs (tag < heute)
    werden nie nachtraeglich abgearbeitet; Tagesplan-Sperre 5 Minuten + Merker
  * Welle 5 Nr. 26: fehlen kritische Unique-Indizes, crawlt der Worker nicht
  * Welle 5 Nr. 34: bis zu MARKT_JOBS_PARALLEL Buendel gleichzeitig
  * Welle 5 Nr. 37: Entfernungspruefung als Hintergrundaufgabe je Takt
  * Fehler bleiben hier: Alarm fuer den Betreiber, nie ein Einfluss auf den
    Hauptweg

Reparaturwelle 6 (Review 26.09.2026 abends):
  * Nr. 77: kein zweiter manueller Job je Segment, solange einer wartet/laeuft
    oder in den letzten 5 Minuten angelegt wurde (SchonEingereiht -> 409)
  * Nr. 93: worker_erfolg erst NACH einem erfolgreich beendeten Takt
  * Nr. 97/98: der Schreibteil nach dem Actor-Lauf zaehlt als Hintergrund-
    Schreiber (wartung.hintergrund_schreibt); ist die Wartung inzwischen aktiv,
    geht das bezahlte Ergebnis als 'ergebnis_zwischenspeicher' an den Job
    zurueck (queued) und wird beim naechsten Claim OHNE neuen Actor-Lauf
    verarbeitet
  * Nr. 99: zwei faellige Jobs desselben Segments kommen nie ins selbe Buendel
    (der zweite: storniert 'doppelt faellig', wenn sein Termin laenger als eine
    Stunde zurueckliegt, sonst wartet er 30 Minuten)
  * Nr. 100: Auswertungs-Sperre je Segment (job_lock 'markt-seg-<id>', 5 min)
    um speicher.verarbeiten — sonst Zwischenspeicher und spaeter erneut
  * Nr. 104/105: Statistik-Tag UND Budget-Monat aus dem Job-Tag, nicht aus
    der Ausfuehrungszeit
  * Nr. 111/112: defekter Zeilenfilter -> Lauf 'data_invalid' + Alarm
  * Nr. 113/114: Actor 'name@build', Build-Kennung am Job und im Tagesaggregat
  * Nr. 122: cancel_requested + abgelaufene Lease -> direkt 'cancelled'
  * Budget voll: Jobs bleiben queued (budget_wait), EIN Alarm, der Takt endet
  * Nr. 127: Zeilen geliefert, alle verworfen -> 'data_invalid'
  * Nr. 143: sample_incomplete, wenn der Markt mehr hergibt als geliefert wurde
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from markt import apify, budget, entfernung, konfig, normalisieren, segmente, speicher, url
from markt.konfig import JOBS, MODELLE, SEGMENTE, TAGESSTATS

log = logging.getLogger(__name__)
WORKER = f"markt-{uuid.uuid4().hex[:8]}"
# Review 26.09.2026 abends P1: 'data_invalid' = Lauf gelaufen und bezahlt, aber die Zeilen sind
# keine gueltige Statistik (Sortierung unsicher) — nichts gespeichert, getrennt gezaehlt
_STATUS = ("queued", "running", "completed", "failed", "cancelled", "data_invalid")
NACHZUEGLER_WARTEN_S = 60       # Nr. 22: so lange wartet der Worker auf faellige Jobs, bis ein Buendel voll ist
TREFFER_TAGE_FUER_ERSATZ = 7    # Nr. 16: Ersatz bei 0 Zeilen nur, wenn ein Segment des Buendels in 7 Tagen Treffer hatte
PLAN_SPERRE_S = 300             # Nr. 24: Tagesplan-Sperre 5 Minuten (der Plan ist idempotent, Merker in market_config)
SOFORT_SPERRE_S = 300           # Welle 6 Nr. 77: kein zweiter manueller Job je Segment innerhalb von 5 Minuten
SEGMENT_SPERRE_S = 300          # Welle 6 Nr. 100: Auswertungs-Sperre je Segment
DOPPELT_STORNO_S = 3600         # Welle 6 Nr. 99: der zweite Job desselben Segments ist 'doppelt faellig', wenn > 1 h ueberfaellig
DOPPELT_WARTEN_S = 1800         # ... sonst wartet er 30 Minuten
BUDGET_WARTEN_S = 900           # Budget voll: Jobs warten 15 Minuten (bleiben queued mit budget_wait)
ZWISCHEN_WARTEN_S = 60          # Ergebnis zwischengespeichert (Wartung/Segment-Sperre): erneut in 60 s
PREIS_ALARM_AB = 3              # Nr. 85: Alarm ab 3 Zeilen mit unplausiblem Preis je Lauf
STORNO_ALT = "Tagesplan veraltet (nie nachtraeglich abgearbeitet)"
STORNO_AUS = "Crawler ausgeschaltet"
STORNO_DOPPELT = "doppelt faellig"


class SchonEingereiht(ValueError):
    """Nr. 77: fuer dieses Segment wartet/laeuft schon ein Job oder ein manueller Lauf ist keine 5 Minuten alt."""


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


async def _wartung_aktiv(db) -> bool:
    try:
        import wartung
        return bool(await wartung.aktiv_async(db))
    except Exception:  # noqa: BLE001
        return False


def _hintergrund_schreibt():
    """Nr. 97: der Schreibteil zaehlt als Hintergrund-Schreiber (die Sicherung wartet darauf)."""
    try:
        import wartung
        return wartung.hintergrund_schreibt()
    except Exception:  # noqa: BLE001
        import contextlib
        return contextlib.nullcontext()


def job_tag(job: Dict[str, Any]) -> str:
    """Nr. 104: der Kalendertag des Jobs (ohne '#'-Suffix eines zweiten/manuellen Laufs)."""
    return str(job.get("tag") or konfig.heute_tag()).split("#")[0]


# ---------------------------------------------------------------- Taktung
def starts_je_gruppe(laeufe_je_rows: Dict[int, int], b: int) -> int:
    """Welle 6 Nr. 133: Buendel enthalten nur Segmente gleicher Zeilenzahl — Actor-Starts je Tag
    = Summe ueber die Gruppen ceil(Laeufe der Gruppe / Buendelgroesse)."""
    return sum(math.ceil(n / max(1, b)) for n in laeufe_je_rows.values() if n)


async def intervall(db) -> Dict[str, Any]:
    """Wie oft kann jedes Segment gecrawlt werden, ohne das Monatsbudget zu sprengen?
    MARKT_CRAWL_INTERVALL_TAGE > 0 setzt es fest, 0 = automatisch.

    Reparaturwelle 5 (Review 26.09.2026 abends):
      Nr. 29: das Budget kommt aus dem Monatsdokument (Admin, market_crawler_budget) — die
              Umgebung (MARKT_BUDGET_MONAT_USD) ist nur die Vorbelegung fuer einen neuen Monat.
      Nr. 30: Budget <= 0 -> keine Planung ("ohne Budget pausiert"), segmente_je_tag 0.
      Nr. 31: Restbudget / verbleibende Tage des Monats bestimmt die Segmente je Tag
              (mindestens 1, hoechstens alle) — Verbrauch und Reservierungen zaehlen mit.
      Nr. 17: Zeilen werden mit Puffer abgerufen (konfig.zeilen_mit_puffer).
      Nr. 38: die Entfernungspruefung (max. je Tag x (Start + 1 Zeile)) ist Teil der Tageskosten.
    Welle 6 Nr. 133: Actor-Starts je Gruppe gleicher Zeilenzahl (starts_je_gruppe)."""
    segs = 0
    laeufe_seg = 0          # Segment-Abrufe je Tag (Segmente x Abrufe je Tag)
    rows_alle = 0           # gespeicherte Zeilen je Tag (rows)
    rows_abruf = 0          # abgerufene Zeilen je Tag (rows + Puffer, Nr. 17)
    laeufe_je_rows: Dict[int, int] = {}
    async for s in db[SEGMENTE].find({"enabled": True}, {"_id": 0, "max_items": 1, "crawls_per_day": 1}):
        segs += 1
        k = int(s.get("crawls_per_day") or 1)
        rows = int(s.get("max_items") or konfig.rows_je_segment())
        laeufe_seg += k
        rows_alle += rows * k
        rows_abruf += konfig.zeilen_mit_puffer(rows) * k
        laeufe_je_rows[rows] = laeufe_je_rows.get(rows, 0) + k
    b = max(1, konfig.buendel_groesse())
    starts = starts_je_gruppe(laeufe_je_rows, b)
    je_tag_alle = konfig.kosten_buendel_usd(konfig.actor(), starts, rows_abruf)
    entfernung_tag = konfig.entfernung_kosten_je_tag_usd()
    doc = await budget.dokument(db)
    budget_usd = float(doc.get("budget_usd") or 0)
    rest = round(budget_usd - float(doc.get("used_usd") or 0) - float(doc.get("reserved_usd") or 0), 4)
    rest_tage = konfig.rest_tage_im_monat()
    fest = konfig.crawl_intervall_tage()
    ohne_budget = budget_usd <= 0
    if ohne_budget:
        tage, je_tag = 0, 0
    elif fest > 0:
        tage = fest
        je_tag = math.ceil(segs / tage) if segs else 0
    elif segs == 0 or je_tag_alle <= 0:
        tage, je_tag = 1, segs
    else:
        tagesbudget = max(0.0, rest) / rest_tage - entfernung_tag
        je_tag = int(math.floor(segs * tagesbudget / je_tag_alle)) if tagesbudget > 0 else 1
        je_tag = max(1, min(segs, je_tag))
        tage = math.ceil(segs / je_tag)
    kosten_je_tag = round(je_tag_alle / tage, 4) if (segs and tage) else 0.0
    # Review 26.09.2026 Nr. 10/11: nur Anzeige — was ein Tag kostet, wenn ALLES ueber den
    # (teureren) Ersatz-Scraper liefe (der Ersatz laeuft je URL einzeln, also ein Start je Segment-Abruf).
    ersatz = konfig.actor_ersatz()
    ersatz_je_tag = (round(konfig.kosten_buendel_usd(ersatz, laeufe_seg, rows_abruf) / tage, 2)
                     if ersatz and segs and tage else 0.0)
    gesamt_tag = round(kosten_je_tag + entfernung_tag, 4)
    return {"segmente": segs, "intervall_tage": tage, "segmente_je_tag": je_tag, "buendel": b,
            "starts_je_tag": starts, "zeilen_gruppen": len(laeufe_je_rows),
            "rows_je_tag": round(rows_alle / tage) if (segs and tage) else 0,
            "rows_abruf_je_tag": round(rows_abruf / tage) if (segs and tage) else 0,
            "puffer_faktor": konfig.PUFFER_FAKTOR, "puffer_max": konfig.PUFFER_MAX,
            "kosten_je_tag_usd": round(gesamt_tag, 2), "kosten_je_monat_usd": round(gesamt_tag * 30.4, 2),
            "kosten_crawl_je_tag_usd": round(kosten_je_tag, 2), "kosten_je_tag_alle_usd": round(je_tag_alle + entfernung_tag, 2),
            "entfernung_je_tag_usd": round(entfernung_tag, 2), "entfernung_max_je_tag": konfig.entfernung_max_je_tag(),
            "ersatz_kosten_je_tag_usd": ersatz_je_tag, "ersatz_actor": ersatz or None,
            "budget_usd": budget_usd, "budget_monat": doc.get("_id"), "restbudget_usd": round(rest, 2), "rest_tage": rest_tage,
            "verbraucht_usd": round(float(doc.get("used_usd") or 0), 2), "reserviert_usd": round(float(doc.get("reserved_usd") or 0), 2),
            "ohne_budget": ohne_budget, "status": "ohne Budget pausiert" if ohne_budget else "ok",
            # fuer die Kostenformel in der Oberflaeche (nie mehr hart "0,004 $ + Zeilen x 0,003 $")
            "start_usd": konfig.preise_je_actor(konfig.actor())[0], "row_usd": konfig.preise_je_actor(konfig.actor())[1],
            "actor": konfig.actor(), "automatisch": fest == 0}


# ---------------------------------------------------------------- Tagesplan
def _job_doc(s: Dict[str, Any], tag: str, geplant_iso: str, job_type: str) -> Dict[str, Any]:
    rows = int(s.get("max_items") or konfig.rows_je_segment())
    abruf = konfig.zeilen_mit_puffer(rows)
    return {"id": uuid.uuid4().hex, "segment_id": s["id"], "model_id": s.get("model_id"), "tag": tag,
            "job_type": job_type, "scheduled_at": geplant_iso, "status": "queued", "attempts": 0,
            "max_attempts": konfig.job_versuche(), "max_items": rows, "estimated_rows": rows, "abruf_rows": abruf,
            "estimated_cost": konfig.kosten_je_lauf_usd(konfig.actor(), abruf), "actual_rows": None,
            "actual_cost": None, "actor_run_id": None, "error": None, "created_at": konfig.jetzt_iso(),
            "finished_at": None}


def _slots(segs: List[Dict[str, Any]], b: int) -> List[List[Dict[str, Any]]]:
    """Nr. 21: Buendel-Slots — Gruppen gleicher Zeilenzahl (der Scraper ist global), je Slot
    hoechstens b Segmente; alle Jobs eines Slots bekommen dieselbe scheduled_at."""
    gruppen: Dict[int, List[Dict[str, Any]]] = {}
    for s in segs:
        gruppen.setdefault(int(s.get("max_items") or konfig.rows_je_segment()), []).append(s)
    slots: List[List[Dict[str, Any]]] = []
    for _, liste in sorted(gruppen.items()):
        for i in range(0, len(liste), max(1, b)):
            slots.append(liste[i:i + max(1, b)])
    return slots


def _abstand(geplant: datetime, ende: datetime, k: int) -> timedelta:
    """Nr. 25: Abstand zwischen den k Abrufen eines Tages = min(24/k h, verbleibendes Fenster/k)
    — der letzte Lauf liegt damit vor 23:30 Uhr deutscher Zeit."""
    rest = max(timedelta(0), ende - geplant)
    return min(timedelta(hours=24 / k), rest / k)


async def tagesplan(db, tag: Optional[str] = None, *, sofort: bool = False) -> Dict[str, Any]:
    """Jobs fuer die heute faelligen Segmente anlegen (idempotent): die am laengsten nicht
    geplanten zuerst, hoechstens segmente_je_tag (Taktung) — abzueglich der heute schon
    geplanten (Nr. 24/32: zweimal aufrufen plant nicht das doppelte Kontingent; sofort=True
    plant ab jetzt, aber ebenfalls nur das Tageskontingent, der Rest wartet).
    Nr. 21: Jobs in Buendel-Slots (gleiche Zeilenzahl, gleiche scheduled_at), Slots ueber das
    Fenster verteilt. Nr. 62: Segmente per Cursor, ohne Obergrenze.
    Welle 6 Nr. 95: unabhaengig vom Tagesmerker — neue Segmente (Aktivierung/Anlage) werden
    im Kontingent des Tages nachgeplant (segmente.synchronisieren ruft mit sofort=True)."""
    t = tag or konfig.heute_tag()
    takt = await intervall(db)
    if takt.get("ohne_budget"):
        # Nr. 30: ohne Budget keine Jobs (vorher: Intervall 1 Tag -> Jobflut, die am Budget scheitert)
        return {"segmente": 0, "neu": 0, "tag": t, "intervall_tage": 0, "segmente_gesamt": takt["segmente"],
                "status": "ohne Budget pausiert", "hinweis": "Monatsbudget ist 0 — keine Planung, keine Jobs."}
    alle = [s async for s in db[SEGMENTE].find({"enabled": True}, {"_id": 0})]
    alle.sort(key=lambda s: (s.get("last_planned_tag") or "", int(s.get("priority") or 5), s["id"]))
    schon = sum(1 for s in alle if (s.get("last_planned_tag") or "") == t)
    faellig = [s for s in alle if (s.get("last_planned_tag") or "") < t]
    kontingent = max(0, int(takt["segmente_je_tag"]) - schon)
    segs = faellig[:kontingent]
    wartend = len(faellig) - len(segs)
    slots = _slots(segs, konfig.buendel_groesse())
    n = max(1, len(slots))
    start = konfig.jetzt() if sofort else konfig.fenster_start(t)
    dauer = timedelta(seconds=0) if sofort else konfig.fenster_dauer()
    ende = konfig.tag_ende(t)
    neu = 0
    for i, slot in enumerate(slots):
        geplant = start + dauer * (i / n)
        for s in slot:
            k = max(1, min(4, int(s.get("crawls_per_day") or 1)))
            # 2x taeglich = ~12 h auseinander. Review 26.09.2026 Nr. 18: nur der ERSTE Abruf liegt
            # im Fenster; der zweite faellt bewusst auf den Nachmittag/Abend. Welle 5 Nr. 25: alle
            # Abrufe eines Tages bleiben vor 23:30 Uhr (Abstand hoechstens Restfenster/k).
            abstand = _abstand(geplant, ende, k)
            for lauf_nr in range(k):
                schluessel = t if lauf_nr == 0 else f"{t}#{lauf_nr + 1}"
                try:
                    await db[JOBS].insert_one(_job_doc(s, schluessel, (geplant + abstand * lauf_nr).isoformat(), "daily"))
                    neu += 1
                except DuplicateKeyError:
                    continue
            await db[SEGMENTE].update_one({"id": s["id"]}, {"$set": {"last_planned_tag": t}})
    await konfig.merker_setzen(db, konfig.TAGESPLAN_DOK, tag=t, segmente=len(segs) + schon, neu=neu, sofort=bool(sofort))
    erg = {"segmente": len(segs), "neu": neu, "tag": t, "intervall_tage": takt["intervall_tage"],
           "segmente_gesamt": takt["segmente"], "slots": len(slots), "schon_geplant": schon, "wartend": wartend,
           "status": "ok"}
    if wartend:
        erg["hinweis"] = (f"{wartend} Segment(e) warten — das Tageskontingent ({takt['segmente_je_tag']}) ist ausgeschoepft; "
                          f"sie kommen an den naechsten Tagen dran (jedes Segment alle {takt['intervall_tage']} Tag(e)).")
    return erg


async def job_sofort(db, segment_id: str) -> Dict[str, Any]:
    """Betreiber: dieses Segment jetzt crawlen (job_type manuell, eigener Dedupe-Schluessel).
    Welle 6 Nr. 77: kein zweiter manueller Job, solange fuer das Segment einer wartet/laeuft oder
    ein manueller Lauf keine SOFORT_SPERRE_S Sekunden alt ist (Doppelklick = ein Job)."""
    s = await db[SEGMENTE].find_one({"id": segment_id}, {"_id": 0})
    if not s:
        raise ValueError("Segment nicht gefunden")
    # Review 26.09.2026 Nr. 52: kein Job fuer ein deaktiviertes Segment / pausiertes Modell
    if not s.get("enabled"):
        raise ValueError("Segment inaktiv — der Suchauftrag ist pausiert, archiviert oder das Segment wurde entfernt")
    modell = await db[MODELLE].find_one({"id": s.get("model_id")}, {"_id": 0, "enabled": 1, "status": 1})
    if not modell or not modell.get("enabled"):
        raise ValueError("Segment inaktiv — der Suchauftrag ist nicht aktiv")
    offen = await db[JOBS].find_one({"segment_id": segment_id, "status": {"$in": ["queued", "running"]}},
                                    {"_id": 0, "id": 1, "status": 1, "job_type": 1, "scheduled_at": 1})
    if offen:
        art = "läuft gerade" if offen["status"] == "running" else "wartet schon"
        raise SchonEingereiht(f"Für dieses Segment {art} ein Job ({'manuell' if offen.get('job_type') == 'manual' else 'Tagesplan'}, "
                              f"{offen['id'][:8]}) — kein zweiter Lauf, bitte abwarten")
    if SOFORT_SPERRE_S > 0:
        seit = (konfig.jetzt() - timedelta(seconds=SOFORT_SPERRE_S)).isoformat()
        letzter = await db[JOBS].find_one({"segment_id": segment_id, "job_type": "manual", "created_at": {"$gte": seit}},
                                          {"_id": 0, "created_at": 1})
        if letzter:
            raise SchonEingereiht(f"Für dieses Segment wurde vor weniger als {SOFORT_SPERRE_S // 60} Minuten schon ein manueller "
                                  f"Lauf angelegt ({str(letzter.get('created_at') or '')[11:16]} UTC) — bitte kurz warten")
    doc = _job_doc(s, konfig.heute_tag() + "#" + uuid.uuid4().hex[:6], konfig.jetzt_iso(), "manual")
    await db[JOBS].insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


async def abbrechen(db, job_id: str) -> bool:
    r = await db[JOBS].update_one({"id": job_id, "status": "queued"},
                                  {"$set": {"status": "cancelled", "finished_at": konfig.jetzt_iso()}})
    return r.modified_count == 1


async def alte_stornieren(db, heute: Optional[str] = None) -> int:
    """Nr. 23: wartende Jobs vergangener Tage werden nie nachtraeglich abgearbeitet (nach Wartung,
    Ausfall oder ausgeschaltetem Crawler) — vor jedem Claim stornieren."""
    t = heute or konfig.heute_tag()
    r = await db[JOBS].update_many({"status": "queued", "tag": {"$lt": t}},
                                   {"$set": {"status": "cancelled", "error": STORNO_ALT, "finished_at": konfig.jetzt_iso()}})
    return int(r.modified_count)


async def crawler_schalten(db, an: bool, wer: str = "") -> Dict[str, Any]:
    """Nr. 23: der Knopf im Admin. Aus: wartende Jobs stornieren (Grund 'Crawler ausgeschaltet'),
    nichts bleibt liegen. An: frischer Tagesplan fuer heute — die heute stornierten Jobs werden
    wieder eingereiht (ihr Slot bleibt, faellige sofort), fehlende Segmente ueber tagesplan(sofort)."""
    aktiv = await konfig.crawler_schalten(db, an, wer=wer)
    jetzt = konfig.jetzt_iso()
    if not aktiv:
        r = await db[JOBS].update_many({"status": "queued"},
                                       {"$set": {"status": "cancelled", "error": STORNO_AUS, "finished_at": jetzt}})
        return {"aktiv": False, "storniert": int(r.modified_count)}
    t = konfig.heute_tag()
    wieder = 0
    async for j in db[JOBS].find({"status": "cancelled", "error": STORNO_AUS, "tag": {"$regex": f"^{t}"}},
                                 {"_id": 0, "id": 1, "scheduled_at": 1}):
        r = await db[JOBS].update_one({"id": j["id"], "status": "cancelled"},
                                      {"$set": {"status": "queued", "error": None, "finished_at": None,
                                                "scheduled_at": max(str(j.get("scheduled_at") or ""), jetzt)}})
        wieder += int(r.modified_count)
    plan = await tagesplan(db, t, sofort=True)
    return {"aktiv": True, "wieder_eingereiht": wieder, "plan": plan}


# ---------------------------------------------------------------- Worker
def lease_sekunden(buendelgroesse: Optional[int] = None) -> int:
    """Review 26.09.2026 Nr. 12: ein Buendel kann laenger dauern als die feste Lease
    (900 s) — Standardlauf plus Ersatzweg je URL einzeln, jeder bis lauf_zeitlimit_s.
    Lease = max(MARKT_JOB_LEASE_SEKUNDEN, (1 + Buendelgroesse) x Zeitlimit + 120 s).
    Rechnung in konfig.lease_sekunden (Nr. 47: auch die Budgetreservierung haengt daran)."""
    return konfig.lease_sekunden(buendelgroesse)


def _lease_bis(buendelgroesse: Optional[int] = None) -> str:
    return (konfig.jetzt() + timedelta(seconds=lease_sekunden(buendelgroesse))).isoformat()


async def beanspruchen(db, max_items: Optional[int] = None, bis: Optional[str] = None,
                       ohne_segmente: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Naechsten faelligen Job atomar uebernehmen. max_items (P6): nur einen Job mit dieser
    Zeilenzahl — so bleibt ein Buendel bei einer Zeilenzahl. bis: faellig bis zu diesem Zeitpunkt
    (Standard jetzt). ohne_segmente (Nr. 99): Segmente, die im Buendel schon vertreten sind.
    Nr. 122: ein Job mit cancel_requested wird nie (wieder) uebernommen."""
    jetzt = konfig.jetzt()
    filt: Dict[str, Any] = {"status": "queued", "scheduled_at": {"$lte": bis or jetzt.isoformat()},
                            "cancel_requested": {"$ne": True}}
    if max_items is not None:
        filt["max_items"] = int(max_items)
    if ohne_segmente:
        filt["segment_id"] = {"$nin": list(ohne_segmente)}
    return await db[JOBS].find_one_and_update(
        filt,
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
    """Abgelaufene Leases: zurueck in die Warteschlange oder endgueltig gescheitert.
    Welle 6 Nr. 122: mit cancel_requested direkt 'cancelled' (Kosten 0) — nie erneut laufen."""
    jetzt = konfig.jetzt_iso()
    n = 0
    async for j in db[JOBS].find({"status": "running", "lease_until": {"$lt": jetzt}},
                                 {"_id": 0, "id": 1, "attempts": 1, "lease_until": 1, "max_attempts": 1, "cancel_requested": 1,
                                  "cancel_grund": 1}):
        if j.get("cancel_requested"):
            r = await db[JOBS].update_one({"id": j["id"], "status": "running", "lease_until": j["lease_until"]},
                                          {"$set": {"status": "cancelled", "finished_at": jetzt, "actual_cost": 0.0, "actual_rows": 0,
                                                    "error": str(j.get("cancel_grund") or "Abbruch angefordert") + " (Lease abgelaufen)"},
                                           "$unset": {"lease_until": ""}})
        elif int(j.get("attempts") or 0) >= int(j.get("max_attempts") or konfig.job_versuche()):
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


async def _noch_meiner(db, job: Dict[str, Any]) -> bool:
    """Welle 5 Nr. 36: VOR dem Speichern — Job noch running, worker == WORKER, Lease nicht abgelaufen."""
    j = await db[JOBS].find_one({**_meiner(job), "lease_until": {"$gt": konfig.jetzt_iso()}}, {"_id": 1})
    return j is not None


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
                                                "$unset": {"lease_until": "", "ergebnis_zwischenspeicher": "", "budget_wait": ""}})
    if r.modified_count == 0:
        log.warning("Job %s inzwischen von anderem Worker uebernommen — Ergebnis nicht ueberschrieben", job["id"])
        return False
    return True


async def _abbrechen(db, job: Dict[str, Any], grund: str, **felder) -> bool:
    """Review 26.09.2026 Nr. 48/51: Job nach dem Lauf als 'cancelled' abschliessen (Segment/
    Modell inzwischen pausiert/archiviert, Auftrag geaendert oder cancel_requested) — Kosten
    werden mitgeschrieben, weil Apify sie berechnet hat; Zeilen wurden NICHT gespeichert."""
    r = await db[JOBS].update_one(_meiner(job), {"$set": {"status": "cancelled", "finished_at": konfig.jetzt_iso(),
                                                          "error": grund[:300], **felder},
                                                "$unset": {"lease_until": "", "ergebnis_zwischenspeicher": ""}})
    if r.modified_count == 0:
        log.warning("Job %s inzwischen von anderem Worker uebernommen — Abbruch nicht geschrieben", job["id"])
        return False
    return True


async def _ungueltig(db, job: Dict[str, Any], grund: str, **felder) -> bool:
    """Review 26.09.2026 abends P1: Lauf gelaufen, Kosten gebucht, aber die Zeilen taugen nicht
    als Statistik (Sortierung unsicher) -> Job 'data_invalid'. Nichts in Snapshots/Tages-/
    Segmentstatistik, kein Trend, keine Chancen, kein last_success_at (last_attempt_at ja)."""
    r = await db[JOBS].update_one(_meiner(job), {"$set": {"status": "data_invalid", "finished_at": konfig.jetzt_iso(),
                                                          "error": grund[:300], **felder},
                                                "$unset": {"lease_until": "", "ergebnis_zwischenspeicher": ""}})
    if r.modified_count == 0:
        log.warning("Job %s inzwischen von anderem Worker uebernommen — 'data_invalid' nicht geschrieben", job["id"])
        return False
    return True


async def _zurueckstellen(db, job: Dict[str, Any], grund: str, warte_s: int, zwischen: Optional[Dict[str, Any]] = None,
                          **felder) -> bool:
    """Welle 6 Nr. 98/100: Job zurueck in die Warteschlange, OHNE den Versuch zu verbrauchen — mit
    dem bezahlten Actor-Ergebnis als 'ergebnis_zwischenspeicher' (naechster Claim verarbeitet es
    ohne neuen Lauf). Auch fuer 'Budget voll' (budget_wait, ohne Zwischenspeicher)."""
    setzen: Dict[str, Any] = {"status": "queued", "error": grund[:300],
                              "scheduled_at": (konfig.jetzt() + timedelta(seconds=int(warte_s))).isoformat(), **felder}
    if zwischen is not None:
        setzen["ergebnis_zwischenspeicher"] = {**zwischen, "gespeichert_at": konfig.jetzt_iso()}
    r = await db[JOBS].update_one(_meiner(job), {"$set": setzen, "$inc": {"attempts": -1},
                                                "$unset": {"lease_until": "", "claimed_at": "", "worker": ""}})
    if r.modified_count == 0:
        log.warning("Job %s inzwischen von anderem Worker uebernommen — nicht zurueckgestellt", job["id"])
        return False
    return True


def _auftrag_fassung(modell: Dict[str, Any]) -> Dict[str, Any]:
    """Welle 5 Nr. 9: Fingerabdruck der Auftragsfassung (definition_hash + version), den der Job
    beim Claim traegt und der Worker nach dem Actor-Lauf erneut vergleicht."""
    from markt import auftraege
    return {"auftrag_hash": modell.get("definition_hash") or auftraege.definition_hash(modell),
            "auftrag_version": segmente.modell_version(modell)}


async def _grundlagen(db, job: Dict[str, Any]):
    seg = await db[SEGMENTE].find_one({"id": job["segment_id"]}, {"_id": 0})
    modell = await db[MODELLE].find_one({"id": (seg or {}).get("model_id")}, {"_id": 0}) if seg else None
    if not seg or not seg.get("enabled") or not modell or not modell.get("enabled"):
        return None
    return seg, modell


async def _noch_gewollt(db, job: Dict[str, Any], fassung: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Nach dem Actor-Lauf, vor dem Speichern (Nr. 48/51): ist das Segment/Modell inzwischen
    deaktiviert, pausiert oder archiviert, oder hat status_setzen cancel_requested gesetzt?
    Welle 5 Nr. 9: oder wurde der Suchauftrag waehrend des Laufs materiell geaendert
    (definition_hash/version anders als beim Claim)? None = weiter; sonst der Grund."""
    j = await db[JOBS].find_one({"id": job["id"]}, {"_id": 0, "cancel_requested": 1, "cancel_grund": 1})
    if j and j.get("cancel_requested"):
        return str(j.get("cancel_grund") or "Abbruch angefordert (Suchauftrag pausiert/archiviert)")
    seg = await db[SEGMENTE].find_one({"id": job["segment_id"]}, {"_id": 0, "enabled": 1, "model_id": 1})
    modell = await db[MODELLE].find_one({"id": (seg or {}).get("model_id") or job.get("model_id")}, {"_id": 0})
    if fassung and modell:
        # Nr. 9 zuerst: eine materielle Aenderung deaktiviert auch das alte Segment — der Grund soll
        # aber "Auftrag geaendert" heissen, nicht "Segment deaktiviert"
        jetzt = _auftrag_fassung(modell)
        if jetzt["auftrag_hash"] != fassung.get("auftrag_hash") or jetzt["auftrag_version"] != fassung.get("auftrag_version"):
            return f"Auftrag geändert (Fassung v{fassung.get('auftrag_version')} → v{jetzt['auftrag_version']})"
    if not seg or not seg.get("enabled"):
        return "Segment während des Laufs deaktiviert"
    if not modell or not modell.get("enabled") or (modell.get("status") and modell.get("status") != "active"):
        return f"Suchauftrag während des Laufs {(modell or {}).get('status') or 'deaktiviert'}"
    return None


FILTER_ALARM_MIN_ZEILEN = 3     # Nr. 2: Alarm erst ab 3 gelieferten Zeilen und > 50 % verworfen


def reservierung_usd(laeufe_plan: int, rows_gesamt: int) -> float:
    """Review 26.09.2026 Nr. 10/11 + Welle 5 Nr. 13: reserviert wird die SUMME aus Standardkosten
    (ein Buendel-Lauf) und Ersatzkosten (je URL einzeln) — beide koennen anfallen: erst der leere
    oder abgebrochene Primaerlauf, dann der Ersatz. Abgerechnet werden nachher die echten Kosten."""
    standard = konfig.kosten_buendel_usd(konfig.actor(), 1, rows_gesamt)
    ersatz = konfig.actor_ersatz()
    if not ersatz:
        return standard
    return round(standard + konfig.kosten_buendel_usd(ersatz, max(1, int(laeufe_plan)), rows_gesamt), 4)


def _kontext(it: Dict[str, Any]) -> Optional[str]:
    ctx = it.get("inputContext")
    if isinstance(ctx, str):
        return ctx
    if isinstance(ctx, dict):
        return ctx.get("url") or ctx.get("startUrl")
    return None


def buendel_schluessel(max_items: Any) -> tuple:
    """Review 26.09.2026 abends P6: ein Buendel enthaelt nur Segmente mit derselben Zeilenzahl
    (und demselben Scraper) — sonst bekaeme jedes Segment im Lauf das Maximum (maxItemsPerQuery)
    und die Kostenreservierung stimmt nicht je Segment."""
    return (konfig.actor(), int(max_items or konfig.rows_je_segment()))


async def _treffer_kuerzlich(db, segment_ids: List[str]) -> bool:
    """Nr. 16: hatte eines dieser Segmente in den letzten 7 Tagen Treffer? Dann ist ein
    leerer Primaerlauf ein Scraper-Ausfall (Ersatz), sonst eine Marktluecke (kein Ersatz)."""
    seit = (datetime.strptime(konfig.heute_tag(), "%Y-%m-%d") - timedelta(days=TREFFER_TAGE_FUER_ERSATZ)).strftime("%Y-%m-%d")
    try:
        n = await db[TAGESSTATS].count_documents({"segment_id": {"$in": list(segment_ids)}, "date": {"$gte": seit},
                                                  "sample_size": {"$gt": 0}}, limit=1)
        return n > 0
    except Exception:  # noqa: BLE001
        return True


async def verarbeiten_buendel(db, jobs_liste: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Mehrere Jobs (Segmente) in EINEM Actor-Lauf. Jede Zeile wird ueber
    inputContext (= Start-URL) genau ihrem Segment zugeordnet. P6: Segmente mit
    verschiedener Zeilenzahl laufen in getrennten Buendeln (je Buendel exakte Reservierung).
    Welle 6 Nr. 98: Jobs mit 'ergebnis_zwischenspeicher' werden einzeln OHNE Actor-Lauf ausgewertet."""
    plan: List[Dict[str, Any]] = []
    zwischen: List[Dict[str, Any]] = []
    for job in jobs_liste:
        g = await _grundlagen(db, job)
        if not g:
            await _fertig(db, job, actual_rows=0, actual_cost=0.0, error="übersprungen (Segment/Modell inaktiv)")
            continue
        seg, modell = g
        rows = int(seg.get("max_items") or konfig.rows_je_segment())
        p = {"job": job, "seg": seg, "modell": modell, "url": url.such_url(seg, modell),
             "max_items": rows, "abruf": konfig.zeilen_mit_puffer(rows), **_auftrag_fassung(modell)}
        (zwischen if job.get("ergebnis_zwischenspeicher") else plan).append(p)
    if not plan and not zwischen:
        return {"status": "uebersprungen", "jobs": len(jobs_liste)}
    teile: List[Dict[str, Any]] = []
    for p in zwischen:
        teile.append(await _buendel_lauf(db, [p], zwischen=p["job"]["ergebnis_zwischenspeicher"]))
    gruppen: Dict[tuple, List[Dict[str, Any]]] = {}
    for p in plan:
        gruppen.setdefault(buendel_schluessel(p["max_items"]), []).append(p)
    for g in gruppen.values():
        teile.append(await _buendel_lauf(db, g))
    if len(teile) == 1:
        return teile[0]
    return {"status": "ok" if any(t.get("status") == "ok" for t in teile) else teile[0].get("status"),
            "jobs": len(plan) + len(zwischen), "buendel": len(teile),
            "rows": sum(int(t.get("rows") or 0) for t in teile),
            "usd": (round(sum(float(t.get("usd") or 0) for t in teile), 4) if any(t.get("usd") is not None for t in teile) else None),
            "ergebnisse": [e for t in teile for e in (t.get("ergebnisse") or [])], "teile": teile}


def _zwischen_paket(p: Dict[str, Any], roh: List[dict], r: Dict[str, Any], anteil: Optional[float], unbekannt: int) -> Dict[str, Any]:
    """Nr. 98: das bezahlte Actor-Ergebnis EINES Jobs fuer den Zwischenspeicher."""
    return {"items": list(roh), "usd": anteil, "run_id": r.get("run_id"), "actor": r.get("actor"), "laeufe": int(r.get("laeufe") or 1),
            "ersatz_grund": r.get("ersatz_grund"), "dauer_ms": r.get("dauer_ms"), "build_id": r.get("build_id"),
            "build_number": r.get("build_number"), "unbekannt": int(unbekannt), "buendel": int(p.get("buendel_n") or 1)}


async def _buendel_lauf(db, plan: List[Dict[str, Any]], zwischen: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """EIN Actor-Lauf fuer ein Buendel gleicher Zeilenzahl (P6): max_items = Segmente x Abruf-
    zeilen (rows + Puffer, Nr. 17), maxItemsPerQuery = Abrufzeilen — die Reservierung deckt
    genau dieses Buendel (Standard + Ersatz, Nr. 13). Mit `zwischen` (Nr. 98): kein Actor-Lauf,
    keine Reservierung — das gespeicherte Ergebnis wird ausgewertet (Kosten sind schon gebucht)."""
    jetzt = konfig.jetzt_iso()
    for p in plan:
        await db[SEGMENTE].update_one({"id": p["seg"]["id"]}, {"$set": {"last_attempt_at": jetzt}})
        # Nr. 9: Auftragsfassung beim Claim am Job vermerken
        await db[JOBS].update_one(_meiner(p["job"]), {"$set": {"auftrag_hash": p["auftrag_hash"], "auftrag_version": p["auftrag_version"]}})
    # Nr. 12: Heartbeat — Lease fuer alle Jobs des Buendels auf die Buendel-Dauer verlaengern.
    # Welle 5 Nr. 35: Jobs, die NICHT mehr uns gehoeren, fliegen aus dem Plan (werden nicht gestartet)
    n_ok = await lease_verlaengern(db, [p["job"]["id"] for p in plan], len(plan))
    if n_ok < len(plan):
        meine = {j["id"] async for j in db[JOBS].find({"id": {"$in": [p["job"]["id"] for p in plan]}, "worker": WORKER,
                                                        "status": "running"}, {"_id": 0, "id": 1})}
        verloren = [p["job"]["id"] for p in plan if p["job"]["id"] not in meine]
        log.warning("Market-Buendel: %d Job(s) inzwischen bei anderem Worker — nicht gestartet: %s", len(verloren), verloren)
        plan = [p for p in plan if p["job"]["id"] in meine]
        if not plan:
            return {"status": "verloren", "jobs": len(verloren)}
    # Nr. 105: der Budget-Monat und (Nr. 104) der Statistik-Tag kommen aus dem Job-Tag
    monat = konfig.monat_aus_tag(job_tag(plan[0]["job"]))
    rows_gesamt = sum(p["abruf"] for p in plan)
    je_query = max(p["abruf"] for p in plan)
    res = None
    if zwischen is None:
        res = await budget.reservieren(db, reservierung_usd(len(plan), rows_gesamt), monat=monat)
        if res is None:
            # Budget voll: Jobs bleiben queued (budget_wait) — kein Massen-'failed'; EIN Alarm; der Takt endet
            for p in plan:
                await _zurueckstellen(db, p["job"], "Monatsbudget des Market-Crawlers aufgebraucht — wartet", BUDGET_WARTEN_S,
                                      budget_wait=True)
            b = await budget.dokument(db, monat)
            await _alarm(db, "markt_budget_voll", ref=monat, budget=float(b.get("budget_usd") or 0), jobs=len(plan))
            return {"status": "budget", "jobs": len(plan)}
        await _alarm_zu(db, "markt_budget_voll", ref=monat)
        ersatz_bei_leer = await _treffer_kuerzlich(db, [p["seg"]["id"] for p in plan])
        try:
            r = await apify.lauf_mit_ersatz([p["url"] for p in plan], rows_gesamt,
                                            max_items_per_query=je_query if len(plan) > 1 else None,
                                            rows_je_url=[p["abruf"] for p in plan], ersatz_bei_leer=ersatz_bei_leer)
        except apify.ApifyFehler as e:
            gelaufen = e.art in ("zeit", "ausfall", "poll", "zuviel")
            await budget.abrechnen(db, res, (e.usd if (gelaufen and e.usd is not None) else (None if gelaufen else 0.0)), 0,
                                   gelaufen=gelaufen, runs=1 if (gelaufen and e.run_id) else 0)
            if e.art == "zuviel":
                # Nr. 87: der Actor lieferte weit mehr als bestellt — keine gueltige Stichprobe, Lauf bezahlt
                anteil = None if e.usd is None else round(float(e.usd) / len(plan), 4)
                for p in plan:
                    await _alarm(db, "markt_datensatz_zu_gross", ref=p["seg"]["id"], job=p["job"]["id"], detail=e.detail[:200])
                    await _ungueltig(db, p["job"], f"Datensatz groesser als erwartet: {e.detail}"[:300], actual_rows=0,
                                     actual_cost=anteil, actor_run_id=e.run_id, run_id=e.run_id)
                return {"status": "data_invalid", "art": e.art, "jobs": len(plan)}
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
    else:
        r = {"items": list(zwischen.get("items") or []), "usd": zwischen.get("usd"), "run_id": zwischen.get("run_id"),
             "actor": zwischen.get("actor"), "laeufe": int(zwischen.get("laeufe") or 1), "ersatz_grund": zwischen.get("ersatz_grund"),
             "dauer_ms": zwischen.get("dauer_ms"), "build_id": zwischen.get("build_id"), "build_number": zwischen.get("build_number")}
    with _hintergrund_schreibt():
        return await _auswerten(db, plan, r, res, monat, zwischen)


async def _auswerten(db, plan: List[Dict[str, Any]], r: Dict[str, Any], res: Optional[Dict[str, Any]], monat: str,
                     zwischen: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Der Schreibteil eines Buendels: Zuordnung, Zeilenfilter, Nachweise, Speichern, Abrechnung."""
    # Zuordnung Zeile -> Segment: ein Segment = alles; mehrere = NUR ueber inputContext == Start-URL
    je_url: Dict[str, List[dict]] = {p["url"]: [] for p in plan}
    unbekannt = 0
    if len(plan) == 1:
        je_url[plan[0]["url"]] = list(r["items"])
        unbekannt = int((zwischen or {}).get("unbekannt") or 0)
    else:
        for it in r["items"]:
            key = _kontext(it)
            if key in je_url:
                je_url[key].append(it)
            else:
                unbekannt += 1
    if unbekannt and zwischen is None:
        await _alarm(db, "markt_zuordnung_unklar", ref=str(r.get("run_id") or ""), zeilen=unbekannt)
    ergebnisse = []
    kosten = r.get("usd")
    start_usd, row_usd = konfig.preise_je_actor(r.get("actor") or konfig.actor())
    laeufe = int(r.get("laeufe") or 1)
    buendel_n = int((zwischen or {}).get("buendel") or len(plan))
    vorbereitet = []
    for p in plan:
        roh = je_url[p["url"]]
        # Welle 5 Nr. 1: Top-N-Nachweis auf den ROHEN Zeilen dieser Suche (vor jedem Filter)
        nachweis, nachweis_grund = normalisieren.top_n_nachweis(roh)
        geliefert = normalisieren.listings_aus_items(roh)
        # Review 26.09.2026 Nr. 2: jede Zeile gegen Segment + Suchauftrag pruefen (EZ, km, kW,
        # Kraftstoff, Getriebe) — unpassende verwerfen, nie als "guenstigstes Angebot" fuehren.
        listings, gruende = [], []
        filter_defekt = ""
        try:
            for l in geliefert:
                ok, grund = normalisieren.passt_zum_segment(l, p["seg"], p["modell"])
                if ok:
                    listings.append(l)
                else:
                    gruende.append(grund)
        except normalisieren.FilterDefekt as e:
            # Nr. 111/112: der Filter selbst ist kaputt — keine Zeile ist gueltig
            filter_defekt = str(e)
            listings, gruende = [], [f"Zeilenfilter defekt: {e}"]
        # Nr. 17: mit Puffer abgerufen, jetzt auf die bestellten Zeilen kuerzen
        listings = listings[: p["max_items"]]
        # Nr. 143: liefert der Scraper die Marktgroesse, ist eine kleinere Lieferung als bestellt 'unvollstaendig'
        markt_n = normalisieren.markt_gesamt(roh)
        sample_incomplete = None if markt_n is None else bool(markt_n > p["max_items"] and len(roh) < p["max_items"])
        vorbereitet.append({"p": p, "listings": listings, "sortiert": normalisieren.preise_aufsteigend(listings),
                            "geliefert_n": len(geliefert), "gruende": gruende, "roh": roh, "roh_n": len(roh),
                            "nachweis": nachweis, "nachweis_grund": nachweis_grund, "filter_defekt": filter_defekt,
                            "sample_incomplete": sample_incomplete, "markt_n": markt_n})
    # Kosten rechnen mit den GELIEFERTEN Rohzeilen (Apify bucht auch verworfene und unbekannte, Nr. 57)
    gesamt_rows = len(r["items"])
    # Befund 26.09.2026: Kosten je Job (Start anteilig + Zeilen) und Monatszaehler liefen
    # auseinander. Beide rechnen jetzt mit derselben Summe: mindestens Starts + Zeilen,
    # hoeher nur, wenn Apify mehr gebucht hat — anteilig auf die Jobs verteilt, sodass die
    # Summe der Jobkosten genau der Buendelsumme entspricht.
    rechnerisch = round(laeufe * start_usd + gesamt_rows * row_usd, 4)
    if zwischen is not None:
        kosten_gesamt = None if kosten is None else float(kosten)          # schon gebucht, nur der Anteil dieses Jobs
    else:
        kosten_gesamt = None if kosten is None else max(float(kosten), rechnerisch)
    je_job_unbekannt = unbekannt / len(plan)
    basis = [start_usd * laeufe / len(plan) + (v["roh_n"] + je_job_unbekannt) * row_usd for v in vorbereitet]
    basis_summe = sum(basis) or 1.0
    wartung_da: Optional[bool] = None
    for v, b_i in zip(vorbereitet, basis):
        p, listings, sortiert, geliefert_n, gruende, roh_n = v["p"], v["listings"], v["sortiert"], v["geliefert_n"], v["gruende"], v["roh_n"]
        nachweis, nachweis_grund = v["nachweis"], v["nachweis_grund"]
        seg_id = p["seg"]["id"]
        verworfen = len(gruende)
        if v["filter_defekt"]:
            await _alarm(db, "markt_filter_defekt", ref="crawler", detail=v["filter_defekt"][:200], job=p["job"]["id"])
        elif geliefert_n >= FILTER_ALARM_MIN_ZEILEN and verworfen * 2 > geliefert_n:
            # mehr als die Haelfte passt nicht zum Segment: mobile.de hat den Filter ignoriert
            await _alarm(db, "markt_filter_ignoriert", ref=seg_id, actor=str(r.get("actor") or ""),
                         verworfen=verworfen, geliefert=geliefert_n, job=p["job"]["id"],
                         gruende="; ".join(gruende[:5]))
        elif verworfen == 0 and geliefert_n:
            await _alarm_zu(db, "markt_filter_ignoriert", ref=seg_id)
        unplausibel = sum(1 for g in gruende if g == normalisieren.GRUND_PREIS_UNPLAUSIBEL)
        if unplausibel >= PREIS_ALARM_AB:
            # Nr. 85: der Scraper liefert Preise, die kein Auto sind (Parser/Waehrung) — Betreiber muss hinsehen
            await _alarm(db, "markt_preis_unplausibel", ref=seg_id, zeilen=unplausibel, job=p["job"]["id"], actor=str(r.get("actor") or ""))
        anteil = None if kosten_gesamt is None else round(kosten_gesamt * b_i / basis_summe, 4)
        lauf_felder = dict(actor_run_id=r.get("run_id"), run_id=r.get("run_id"), actor=r.get("actor"),
                           ersatz_grund=r.get("ersatz_grund"), dauer_ms=r.get("dauer_ms"), buendel=buendel_n,
                           gelieferte_rows=geliefert_n, rohe_rows=roh_n, abruf_rows=p["abruf"], actual_cost=anteil,
                           sortierung=nachweis, sortierung_grund=nachweis_grund or None,
                           # Nr. 114: Build des Scrapers am Job; Nr. 143: Stichprobe vollstaendig?
                           actor_build_id=r.get("build_id"), actor_build_number=r.get("build_number"),
                           sample_incomplete=v["sample_incomplete"], markt_gesamt=v["markt_n"],
                           verworfen_gruende=gruende[:20])
        # Welle 5 Nr. 36: VOR dem Speichern — gehoert der Job noch uns (running, worker, Lease gueltig)?
        if not await _noch_meiner(db, p["job"]):
            log.warning("Job %s: Lease verloren/abgelaufen — Ergebnis nicht gespeichert, Kosten gebucht", p["job"]["id"])
            ergebnisse.append({"status": "verloren", "sample_size": 0})
            continue
        # Nr. 48/51 + Nr. 9: Segment/Modell inzwischen pausiert/archiviert, Auftrag geaendert oder
        # Abbruch angefordert -> Zeilen NICHT speichern, Job 'cancelled' mit Grund; Kosten trotzdem
        grund = await _noch_gewollt(db, p["job"], {"auftrag_hash": p["auftrag_hash"], "auftrag_version": p["auftrag_version"]})
        if grund:
            await _abbrechen(db, p["job"], grund, actual_rows=0, **lauf_felder)
            ergebnisse.append({"status": "cancelled", "grund": grund, "sample_size": 0})
            continue
        if v["filter_defekt"]:
            grund_f = f"Zeilenfilter defekt: {v['filter_defekt']}"[:300]
            await _ungueltig(db, p["job"], grund_f, actual_rows=0, verworfen_filter=verworfen, sorted_confirmed=sortiert, **lauf_felder)
            ergebnisse.append({"status": "data_invalid", "grund": grund_f, "sample_size": 0})
            continue
        if not sortiert or nachweis == normalisieren.SORTIERUNG_UNGUELTIG:
            # P1: unsortierte Actor-Ergebnisse sind KEINE "guenstigsten Angebote" — nichts speichern,
            # Job 'data_invalid' (Kosten gebucht), Alarm bleibt; kein last_success_at am Segment.
            # Welle 5 Nr. 1: auch Positionsnummern mit Luecken (Top-N nicht vollstaendig).
            grund_u = "Sortierung unsicher" if not sortiert else f"Sortierung unsicher: {nachweis_grund}"
            await _alarm(db, "markt_sortierung_unsicher", ref=seg_id, job=p["job"]["id"], grund=grund_u[:200])
            await _ungueltig(db, p["job"], grund_u, actual_rows=0, verworfen_filter=verworfen, sorted_confirmed=False, **lauf_felder)
            ergebnisse.append({"status": "data_invalid", "grund": grund_u, "sample_size": 0})
            continue
        await _alarm_zu(db, "markt_sortierung_unsicher", ref=seg_id)
        if roh_n > 0 and not listings:
            # Nr. 127: der Actor lieferte Zeilen, aber keine einzige taugt — das ist keine Marktluecke
            grund_a = f"alle Zeilen verworfen ({verworfen} von {roh_n}: {'; '.join(gruende[:3])})"[:300]
            await _ungueltig(db, p["job"], grund_a, actual_rows=0, verworfen_filter=verworfen, sorted_confirmed=True, **lauf_felder)
            ergebnisse.append({"status": "data_invalid", "grund": grund_a, "sample_size": 0})
            continue
        # Nr. 98: ist inzwischen die Wartung (Sicherung/Restore) aktiv, wird nichts geschrieben — das bezahlte
        # Ergebnis geht mit dem Job zurueck in die Warteschlange und wird beim naechsten Claim ausgewertet
        if wartung_da is None:
            wartung_da = await _wartung_aktiv(db)
        if wartung_da:
            zs = _zwischen_paket({**p, "buendel_n": buendel_n}, v["roh"], r, anteil, unbekannt)
            await _zurueckstellen(db, p["job"], "Wartung aktiv — Ergebnis zwischengespeichert", ZWISCHEN_WARTEN_S, zs,
                                  actual_cost=anteil, actor_run_id=r.get("run_id"))
            ergebnisse.append({"status": "zurueckgestellt", "grund": "wartung", "sample_size": 0})
            continue
        # Nr. 100: Auswertungs-Sperre je Segment — zwei Laeufe desselben Segments schreiben nie gleichzeitig
        from job_lock import acquire, release
        sperre = f"markt-seg-{seg_id}"
        token = await acquire(db, sperre, ttl_seconds=SEGMENT_SPERRE_S)
        if not token:
            zs = _zwischen_paket({**p, "buendel_n": buendel_n}, v["roh"], r, anteil, unbekannt)
            await _zurueckstellen(db, p["job"], "Segment wird gerade ausgewertet — Ergebnis zwischengespeichert", ZWISCHEN_WARTEN_S, zs,
                                  actual_cost=anteil, actor_run_id=r.get("run_id"))
            ergebnisse.append({"status": "zurueckgestellt", "grund": "segment_sperre", "sample_size": 0})
            continue
        top_n_bewiesen = nachweis == normalisieren.SORTIERUNG_BEWIESEN
        try:
            try:
                erg = await speicher.verarbeiten(db, p["seg"], listings, lauf_tag=p["job"].get("tag"), top_n_bewiesen=top_n_bewiesen,
                                                 tag=job_tag(p["job"]),
                                                 lauf_info={"actor_build": r.get("build_number") or r.get("build_id"),
                                                            "sample_incomplete": v["sample_incomplete"], "run_id": r.get("run_id")})
            except Exception as e:  # noqa: BLE001
                log.exception("Market-Speicher %s gescheitert", p["job"]["id"])
                await _scheitern(db, p["job"], f"Speichern: {e}"[:300], endgueltig=False)
                continue
            # 0 Treffer in EINEM Segment ist eine Marktluecke (z. B. EZ 2019 mit 0-50k km), kein
            # Fehler — Befund 26.09.2026: 32 Betriebsalarme an einem Tag. Wir merken es nur am
            # Segment (leer_in_folge) und alarmieren erst, wenn ein ganzer Lauf leer bleibt.
            if not listings:
                await db[SEGMENTE].update_one({"id": seg_id}, {"$inc": {"leer_in_folge": 1}, "$set": {"last_rows": 0}})
            else:
                await db[SEGMENTE].update_one({"id": seg_id}, {"$set": {"leer_in_folge": 0, "last_rows": len(listings)}})
                await _alarm_zu(db, "markt_keine_treffer", ref=seg_id)
            await _fertig(db, p["job"], actual_rows=len(listings), verworfen_filter=verworfen, sorted_confirmed=sortiert,
                          top_n_bewiesen=top_n_bewiesen, ergebnis=erg, **lauf_felder)
        finally:
            await release(db, sperre, token)
        await _alarm_zu(db, "markt_crawl_fehlgeschlagen", ref=seg_id)
        ergebnisse.append({**erg, "sortierung": nachweis})
    if len(plan) >= 2 and gesamt_rows == 0 and not unbekannt:
        # Ein ganzer Buendel-Lauf ohne eine einzige Zeile: Scraper/Sperre/URL-Form — das ist ein Fehler.
        await _alarm(db, "markt_lauf_leer", ref=str(r.get("run_id") or ""), segmente=len(plan), actor=str(r.get("actor") or ""))
    if res is not None:
        await budget.abrechnen(db, res, kosten_gesamt, gesamt_rows, runs=laeufe)
    return {"status": "ok", "jobs": len(plan), "rows": gesamt_rows, "usd": kosten_gesamt, "laeufe": laeufe, "ergebnisse": ergebnisse,
            "monat": monat}


async def verarbeiten(db, job: Dict[str, Any]) -> Dict[str, Any]:
    """Ein einzelner Job (ohne Buendel)."""
    erg = await verarbeiten_buendel(db, [job])
    if erg.get("ergebnisse"):
        return {"status": "ok", **erg["ergebnisse"][0]}
    return erg


async def _doppelt(db, job: Dict[str, Any]) -> None:
    """Nr. 99: ein zweiter faelliger Job desselben Segments — storniert ('doppelt faellig'), wenn sein
    Termin laenger als DOPPELT_STORNO_S zurueckliegt, sonst wartet er DOPPELT_WARTEN_S."""
    jetzt = konfig.jetzt()
    try:
        faellig = datetime.fromisoformat(str(job.get("scheduled_at")))
    except (TypeError, ValueError):
        faellig = jetzt
    if (jetzt - faellig).total_seconds() > DOPPELT_STORNO_S:
        await db[JOBS].update_one(_meiner(job), {"$set": {"status": "cancelled", "error": STORNO_DOPPELT, "finished_at": jetzt.isoformat()},
                                                "$unset": {"lease_until": "", "claimed_at": "", "worker": ""}})
    else:
        await _zurueckstellen(db, job, f"{STORNO_DOPPELT} — wartet", DOPPELT_WARTEN_S)


async def _buendel_sammeln(db, nachzuegler_s: int) -> List[Dict[str, Any]]:
    """Nr. 22: faellige Jobs bis Buendelgroesse einsammeln (gleiche Zeilenzahl wie der erste);
    ist das Buendel nicht voll, hoechstens nachzuegler_s Sekunden auf Jobs warten, die in
    diesem Zeitraum faellig werden — sonst startet fuer 1-2 Jobs ein eigener Actor-Lauf.
    Nr. 99: je Segment nur ein Job im Buendel."""
    erster = await beanspruchen(db)
    if not erster:
        return []
    buendel: List[Dict[str, Any]] = [erster]
    rows = int(erster.get("max_items") or konfig.rows_je_segment())
    groesse = konfig.buendel_groesse()
    frist = konfig.jetzt() + timedelta(seconds=max(0, int(nachzuegler_s)))
    segmente_drin = {erster["segment_id"]}
    while len(buendel) < groesse:
        job = await beanspruchen(db, max_items=rows)
        if job:
            if job["segment_id"] in segmente_drin:
                await _doppelt(db, job)
                continue
            segmente_drin.add(job["segment_id"])
            buendel.append(job)
            continue
        jetzt = konfig.jetzt()
        if jetzt >= frist:
            break
        naechster = await db[JOBS].find_one({"status": "queued", "max_items": rows, "scheduled_at": {"$lte": frist.isoformat()},
                                             "cancel_requested": {"$ne": True}},
                                            {"_id": 0, "scheduled_at": 1}, sort=[("scheduled_at", 1)])
        if not naechster:
            break
        try:
            faellig = datetime.fromisoformat(str(naechster["scheduled_at"]))
        except (TypeError, ValueError):
            break
        warte = min((faellig - jetzt).total_seconds(), (frist - jetzt).total_seconds())
        await asyncio.sleep(max(0.05, warte))
    return buendel


async def einmal(db, *, max_buendel: Optional[int] = None, schalter_pruefen: bool = False,
                 nachzuegler_s: int = NACHZUEGLER_WARTEN_S) -> Dict[str, Any]:
    """Ein Worker-Takt: Stale zurueck, alte Jobs stornieren (Nr. 23), verfallene Budget-
    reservierungen freigeben (Nr. 47), faellige Jobs in Buendeln verarbeiten — bis zu
    jobs_parallel() Buendel gleichzeitig (Nr. 34), je Buendel eine atomare Reservierung.
    Nr. 26: fehlen kritische Unique-Indizes, wird nicht gecrawlt.
    Nr. 33: schalter_pruefen=True (Worker-Schleife) prueft vor jedem Buendel den Crawler-Schalter;
    die Admin-Route ruft mit max_buendel=1 (ein Buendel je Aufruf, Antwort sagt, wie viele warten).
    Welle 6: beim ersten 'Budget aufgebraucht' endet der Takt (Jobs bleiben queued, budget_wait)."""
    await stale_zurueck(db)
    try:
        await alte_stornieren(db)
    except Exception:  # noqa: BLE001
        log.exception("Alte Markt-Jobs nicht storniert")
    try:
        await budget.verfallene_freigeben(db)
    except Exception:  # noqa: BLE001
        log.exception("Budget-Reaper gescheitert")
    # erst danach die Claims (beanspruchen in _buendel_sammeln) — Nr. 47: freies Budget vor dem Reservieren
    fehlen = await konfig.indizes_fehlen(db)
    if fehlen:
        log.error("Market-Worker: kritische Unique-Indizes fehlen (%s) — kein Crawl", ", ".join(fehlen))
        await _alarm(db, "markt_indizes_fehlen", ref="crawler", fehlen=", ".join(fehlen))
        return {"erledigt": 0, "buendel": 0, "gesperrt": "indizes_fehlen", "wartend": await _wartend(db)}
    erledigt = 0
    buendel_n = 0
    grund = ""
    while max_buendel is None or buendel_n < max_buendel:
        if schalter_pruefen and not await konfig.crawler_aktiv(db):
            grund = "crawler_aus"
            break
        parallel = max(1, konfig.jobs_parallel())
        if max_buendel is not None:
            parallel = min(parallel, max_buendel - buendel_n)
        buendel: List[List[Dict[str, Any]]] = []
        for _ in range(parallel):
            b = await _buendel_sammeln(db, nachzuegler_s)
            if not b:
                break
            buendel.append(b)
        if not buendel:
            break
        ergebnisse = await asyncio.gather(*[verarbeiten_buendel(db, b) for b in buendel], return_exceptions=True)
        budget_voll = False
        for b, e in zip(buendel, ergebnisse):
            if isinstance(e, BaseException):
                log.error("Market-Buendel (%d Jobs) mit Ausnahme: %s", len(b), e)
            elif isinstance(e, dict) and (e.get("status") == "budget" or any(t.get("status") == "budget" for t in (e.get("teile") or []))):
                budget_voll = True
        erledigt += sum(len(b) for b in buendel)
        buendel_n += len(buendel)
        if budget_voll:
            grund = "budget_voll"
            break
    raus: Dict[str, Any] = {"erledigt": erledigt, "buendel": buendel_n, "wartend": await _wartend(db)}
    if grund:
        raus["abgebrochen"] = grund
    return raus


async def _wartend(db) -> int:
    try:
        return int(await db[JOBS].count_documents({"status": "queued", "scheduled_at": {"$lte": konfig.jetzt_iso()}}))
    except Exception:  # noqa: BLE001
        return 0


# Nr. 37: die Entfernungspruefung laeuft als eigene Aufgabe neben dem Worker — hoechstens eine
_entfernung_task: Optional[asyncio.Task] = None


def _entfernung_starten(db) -> bool:
    global _entfernung_task
    if _entfernung_task is not None and not _entfernung_task.done():
        return False

    async def _lauf():
        try:
            await entfernung.taeglich(db)
        except Exception:  # noqa: BLE001
            log.exception("Entfernungs-Pruefung gescheitert")
    _entfernung_task = asyncio.get_running_loop().create_task(_lauf())
    return True


async def worker_forever(db, erfolg: Optional[Callable[[], None]] = None, takt_s: int = 20) -> None:
    """Dauerschleife je Prozess (Claims sind atomar, mehrere Prozesse sind ok).
    Tagesplan einmal je Tag ueber eine kurze Mongo-Sperre + Merker (Nr. 24); Entfernungs-
    Pruefung als Hintergrundaufgabe (Nr. 37); Crawler-Schalter vor jedem Buendel (Nr. 33).
    Welle 6 Nr. 93: `erfolg` (worker_erfolg in server.py) erst nach einem erfolgreich
    beendeten Takt — nicht mehr am Anfang."""
    await asyncio.sleep(15)
    geplant_fuer = ""
    while True:
        try:
            if not await konfig.crawler_aktiv(db) or not konfig.token():
                await asyncio.sleep(60)
                continue
            if await _wartung_aktiv(db):
                await asyncio.sleep(60)
                continue
            tag = konfig.heute_tag()
            if geplant_fuer != tag:
                if (await konfig.merker_lesen(db, konfig.TAGESPLAN_DOK)).get("tag") == tag:
                    geplant_fuer = tag          # ein anderer Prozess hat heute schon geplant
                else:
                    from job_lock import acquire
                    token = await acquire(db, f"markt-plan-{tag}", ttl_seconds=PLAN_SPERRE_S)
                    if token:
                        await segmente.synchronisieren(db, nachplanen=False)
                        await tagesplan(db, tag)
                        geplant_fuer = tag
            await einmal(db, schalter_pruefen=True)
            _entfernung_starten(db)
            if erfolg:
                erfolg()
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
    raus["letzte"] = await db[JOBS].find({"status": {"$in": ["completed", "failed", "data_invalid"]}}, {"_id": 0, "ergebnis": 0, "ergebnis_zwischenspeicher": 0})\
        .sort("finished_at", -1).to_list(10)
    raus["fehler_offen"] = raus["failed"]
    raus["budget_wartend"] = await db[JOBS].count_documents({"status": "queued", "budget_wait": True})
    raus["indizes_fehlen"] = await konfig.indizes_fehlen(db)
    raus["tagesplan"] = await konfig.merker_lesen(db, konfig.TAGESPLAN_DOK)
    return raus
