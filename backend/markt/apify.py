# -*- coding: utf-8 -*-
"""Apify-Lauf des mobile.de-Scrapers fuer den Market-Crawler.

Anders als der Hauptweg (run-sync, 85 s) laeuft hier alles im Hintergrund:
Lauf starten, abwarten, Datensatz holen — und die ECHTEN Kosten aus dem
Lauf (usageTotalUsd) fuer die Abrechnung. Token nur im Header, nie in der
URL (Log-Leck). Wirft ApifyFehler; nie eine Ausnahme des Hauptwegs."""
from __future__ import annotations

import asyncio
import logging
import ssl
import time
from typing import Any, Dict, List, Optional

import certifi
import httpx

from markt import konfig

log = logging.getLogger(__name__)
_SSL = ssl.create_default_context(cafile=certifi.where())
BASIS = "https://api.apify.com/v2"
ENDZUSTAENDE = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT", "TIMING-OUT", "ABORTING"}


class ApifyFehler(RuntimeError):
    def __init__(self, art: str, detail: str = ""):
        super().__init__(f"{art}: {detail}"[:300])
        self.art = art          # token | guthaben | limit | zeit | ausfall | leer
        self.detail = detail[:300]


def _fehler_aus_status(status: int, text: str) -> ApifyFehler:
    t = (text or "")[:300]
    if status in (401, 403):
        return ApifyFehler("token", t)
    if status == 402 or "insufficient" in t.lower() or "usage limit" in t.lower():
        return ApifyFehler("guthaben", t)
    if status == 429:
        return ApifyFehler("limit", t)
    if status in (408, 504):
        return ApifyFehler("zeit", t)
    return ApifyFehler("ausfall", f"HTTP {status} {t}")


def eingabe(actor_name: str, start_urls: List[str], max_items: int,
            max_items_per_query: Optional[int] = None) -> Dict[str, Any]:
    """Actor-Eingabe je Scraper: URL-Form und Zusatzfelder. scrapesmith kann
    mehrere Suchen je Lauf mit Obergrenze je Suche (maxItemsPerQuery)."""
    form = konfig.start_urls_form(actor_name)
    urls = [{"url": u} for u in start_urls] if form == "objekt" else list(start_urls)
    e: Dict[str, Any] = {"startUrls": urls, "maxItems": int(max_items)}
    if actor_name.startswith("scrapesmith"):
        e["includeFullDetails"] = konfig.apify_details()
        if max_items_per_query:
            e["maxItemsPerQuery"] = int(max_items_per_query)
    return e


def kosten_aus_lauf(actor_name: str, lauf_doc: Dict[str, Any], items: int) -> Optional[float]:
    """Echte Kosten: Apify bucht Zeilen erst kurz nach dem Lauf — usageTotalUsd
    ist direkt danach oft nur der Start. Deshalb aus den Ereigniszaehlern
    (chargedEventCounts) mit den bekannten Preisen rechnen und das Maximum
    mit usageTotalUsd nehmen."""
    usd = lauf_doc.get("usageTotalUsd")
    start, row = konfig.preise_je_actor(actor_name)
    z = lauf_doc.get("chargedEventCounts") or {}
    starts = int(z.get("apify-actor-start") or 1)
    zeilen = int(z.get("apify-default-dataset-item") or z.get("result") or items or 0)
    geschaetzt = round(starts * start + zeilen * row, 4)
    try:
        return round(max(float(usd or 0), geschaetzt), 4)
    except (TypeError, ValueError):
        return geschaetzt


async def lauf(start_urls: List[str], max_items: int, *, zeitlimit_s: Optional[int] = None,
               actor_name: Optional[str] = None, max_items_per_query: Optional[int] = None) -> Dict[str, Any]:
    """{"items": [...], "usd": float|None, "run_id": str, "status": str, "dauer_ms": int, "actor": str}."""
    tok = konfig.token()
    if not tok:
        raise ApifyFehler("token", "APIFY_TOKEN fehlt")
    actor_name = actor_name or konfig.actor()
    zeitlimit = int(zeitlimit_s or konfig.lauf_zeitlimit_s())
    kopf = {"Authorization": f"Bearer {tok}"}
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0), verify=_SSL) as client:
        r = await client.post(f"{BASIS}/acts/{actor_name}/runs",
                              params={"timeout": zeitlimit}, headers=kopf,
                              json=eingabe(actor_name, start_urls, max_items, max_items_per_query))
        if r.status_code >= 400:
            raise _fehler_aus_status(r.status_code, r.text)
        daten = (r.json() or {}).get("data") or {}
        run_id, dataset_id = daten.get("id"), daten.get("defaultDatasetId")
        if not run_id:
            raise ApifyFehler("ausfall", "keine Lauf-ID")
        frist = time.monotonic() + zeitlimit + 30
        status = daten.get("status") or "RUNNING"
        while status not in ENDZUSTAENDE:
            if time.monotonic() > frist:
                try:
                    await client.post(f"{BASIS}/actor-runs/{run_id}/abort", headers=kopf)
                except Exception:  # noqa: BLE001
                    pass
                raise ApifyFehler("zeit", f"Lauf {run_id} nicht fertig nach {zeitlimit} s")
            await asyncio.sleep(5)
            rr = await client.get(f"{BASIS}/actor-runs/{run_id}", headers=kopf)
            if rr.status_code >= 400:
                raise _fehler_aus_status(rr.status_code, rr.text)
            daten = (rr.json() or {}).get("data") or {}
            status = daten.get("status") or status
            dataset_id = daten.get("defaultDatasetId") or dataset_id
        if status != "SUCCEEDED":
            raise ApifyFehler("ausfall", f"Lauf {run_id} endete mit {status}")
        ri = await client.get(f"{BASIS}/datasets/{dataset_id}/items",
                              params={"clean": "1", "format": "json"}, headers=kopf)
        if ri.status_code >= 400:
            raise _fehler_aus_status(ri.status_code, ri.text)
        items = ri.json()
        if not isinstance(items, list):
            raise ApifyFehler("ausfall", "unerwartete Antwortform")
        items = [i for i in items if isinstance(i, dict)]
        usd = kosten_aus_lauf(actor_name, daten, len(items))
    return {"items": items, "usd": usd, "run_id": run_id, "status": status,
            "dauer_ms": int((time.perf_counter() - t0) * 1000), "actor": actor_name}


async def lauf_mit_ersatz(start_urls: List[str], max_items: int, *, zeitlimit_s: Optional[int] = None,
                          max_items_per_query: Optional[int] = None) -> Dict[str, Any]:
    """Standard-Scraper; scheitert er (Fehler oder 0 Treffer bei Suche), einmal
    der Ersatz-Scraper. Token-/Guthabenfehler betreffen beide — kein Ersatz.
    Buendel (mehrere URLs) laufen NUR beim Standard; der Ersatz kennt keine
    Grenze je Suche und bekommt deshalb nur eine URL je Lauf."""
    ersatz = konfig.actor_ersatz()
    try:
        r = await lauf(start_urls, max_items, zeitlimit_s=zeitlimit_s, max_items_per_query=max_items_per_query)
        if r["items"] or not ersatz:
            return r
        erster = "0 Treffer"
    except ApifyFehler as e:
        if not ersatz or e.art in ("token", "guthaben"):
            raise
        erster = f"{e.art}: {e.detail}"
    log.warning("Market-Scraper %s: %s — Ersatz %s", konfig.actor(), erster, ersatz)
    if len(start_urls) > 1:
        # Ersatz je URL einzeln, Zeilen bekommen inputContext = URL fuer die Zuordnung
        items: List[Dict[str, Any]] = []
        usd = 0.0
        run_ids = []
        dauer = 0
        je = max_items_per_query or max_items
        for u in start_urls:
            rr = await lauf([u], je, zeitlimit_s=zeitlimit_s, actor_name=ersatz)
            for it in rr["items"]:
                it.setdefault("inputContext", u)
            items.extend(rr["items"])
            usd += float(rr.get("usd") or 0)
            run_ids.append(rr.get("run_id"))
            dauer += int(rr.get("dauer_ms") or 0)
        return {"items": items, "usd": round(usd, 4), "run_id": ",".join(x for x in run_ids if x), "status": "SUCCEEDED",
                "dauer_ms": dauer, "actor": ersatz, "ersatz_grund": erster}
    r = await lauf(start_urls, max_items, zeitlimit_s=zeitlimit_s, actor_name=ersatz)
    r["ersatz_grund"] = erster
    return r
