# -*- coding: utf-8 -*-
"""Apify-Lauf des mobile.de-Scrapers fuer den Market-Crawler.

Anders als der Hauptweg (run-sync, 85 s) laeuft hier alles im Hintergrund:
Lauf starten, abwarten, Datensatz holen — und die ECHTEN Kosten aus dem
Lauf (usageTotalUsd) fuer die Abrechnung. Token nur im Header, nie in der
URL (Log-Leck). Wirft ApifyFehler; nie eine Ausnahme des Hauptwegs.

Reparaturwelle 5 (Review 26.09.2026 abends, Nr. 67/68): Startfehler und
Poll-Fehler sind getrennt. Ein voruebergehender Fehler beim Abfragen des
Laufzustands (429/5xx/Netz) wird bis zu dreimal mit Backoff wiederholt;
erst danach wird der Primaerlauf per API abgebrochen (sonst liefe er weiter
und kostete Geld, waehrend der Ersatz startet). 429 (Rate-Limit) loest nie
den Ersatz aus — der Job kommt spaeter erneut."""
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
POLL_VERSUCHE = 3           # Nr. 67: so oft wird ein fehlgeschlagenes Polling wiederholt
POLL_BACKOFF_S = (5, 10, 20)
# Nr. 68: bei diesen Fehlerarten laeuft NIE der Ersatz-Scraper. Reparaturwelle 6 Nr. 87: 'zuviel'
# (Datensatz groesser als bestellt) ist ein Datenfehler des Laufs — kein Ersatz, Job 'data_invalid'
OHNE_ERSATZ = ("token", "guthaben", "limit", "zuviel")
DATENSATZ_PUFFER = 20       # Nr. 87: abgerufen wird hoechstens bestellte Zeilen x 2 + 20


def datensatz_limit(max_items: int) -> int:
    return int(max(1, int(max_items or 1)) * 2 + DATENSATZ_PUFFER)


class ApifyFehler(RuntimeError):
    def __init__(self, art: str, detail: str = "", *, usd: Optional[float] = None, run_id: Optional[str] = None):
        super().__init__(f"{art}: {detail}"[:300])
        self.art = art          # token | guthaben | limit | zeit | ausfall | poll | leer | zuviel
        self.detail = detail[:300]
        # Nr. 14/67: Kosten, die der (abgebrochene) Lauf schon verursacht hat, und seine ID
        self.usd = usd
        self.run_id = run_id


def _fehler_aus_status(status: int, text: str, **kw) -> ApifyFehler:
    t = (text or "")[:300]
    if status in (401, 403):
        return ApifyFehler("token", t, **kw)
    if status == 402 or "insufficient" in t.lower() or "usage limit" in t.lower():
        return ApifyFehler("guthaben", t, **kw)
    if status == 429:
        return ApifyFehler("limit", t, **kw)
    if status in (408, 504):
        return ApifyFehler("zeit", t, **kw)
    return ApifyFehler("ausfall", f"HTTP {status} {t}", **kw)


def eingabe(actor_name: str, start_urls: List[str], max_items: int,
            max_items_per_query: Optional[int] = None) -> Dict[str, Any]:
    """Actor-Eingabe je Scraper: URL-Form und Zusatzfelder. scrapesmith kann
    mehrere Suchen je Lauf mit Obergrenze je Suche (maxItemsPerQuery)."""
    actor_name = konfig.actor_und_build(actor_name)[0]
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
    # Befund 26.09.2026: direkt nach dem Lauf sind oft erst ein Teil der Zeilen gebucht —
    # dann fehlte Geld im Monatszaehler (Kosten heute 4,17 $ > Monat 3,41 $). Nie unter den
    # tatsaechlich gelieferten Zeilen rechnen.
    zeilen = max(int(z.get("apify-default-dataset-item") or z.get("result") or 0), int(items or 0))
    geschaetzt = round(starts * start + zeilen * row, 4)
    try:
        return round(max(float(usd or 0), geschaetzt), 4)
    except (TypeError, ValueError):
        return geschaetzt


async def _abbrechen(client: httpx.AsyncClient, run_id: str, kopf: Dict[str, str]) -> None:
    """Nr. 67: Primaerlauf abbrechen, BEVOR ein Ersatz startet — sonst laufen beide und zahlen beide."""
    try:
        await client.post(f"{BASIS}/actor-runs/{run_id}/abort", headers=kopf)
    except Exception:  # noqa: BLE001
        log.warning("Market-Scraper: Lauf %s konnte nicht abgebrochen werden", run_id)


async def lauf_dokument(run_id: str) -> Optional[Dict[str, Any]]:
    """Nr. 115: das Lauf-Dokument (usageTotalUsd, buildNumber, ...) eines Actor-Laufs — fuer die
    Kostenabstimmung nach dem Lauf. None bei Fehler."""
    tok = konfig.token()
    if not tok or not run_id:
        return None
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=20.0), verify=_SSL) as client:
        rr = await client.get(f"{BASIS}/actor-runs/{run_id}", headers={"Authorization": f"Bearer {tok}"})
        if rr.status_code >= 400:
            raise _fehler_aus_status(rr.status_code, rr.text, run_id=run_id)
        return (rr.json() or {}).get("data") or None


async def lauf(start_urls: List[str], max_items: int, *, zeitlimit_s: Optional[int] = None,
               actor_name: Optional[str] = None, max_items_per_query: Optional[int] = None) -> Dict[str, Any]:
    """{"items": [...], "usd": float|None, "run_id": str, "status": str, "dauer_ms": int, "actor": str, "laeufe": 1,
    "build_id": str|None, "build_number": str|None}.
    Reparaturwelle 6 Nr. 87: der Datensatz wird mit `limit` (bestellte Zeilen x 2 + 20) abgerufen —
    kommen so viele Zeilen, hat der Actor die Bestellung ignoriert: ApifyFehler 'zuviel' (der Job
    endet 'data_invalid', kein Ersatz). Nr. 113/114: 'name@build' pinnt den Actor (?build=), die
    Build-Kennung des Laufs wird mitgeliefert."""
    tok = konfig.token()
    if not tok:
        raise ApifyFehler("token", "APIFY_TOKEN fehlt")
    actor_voll = actor_name or konfig.actor()
    actor_name, build = konfig.actor_und_build(actor_voll)
    zeitlimit = int(zeitlimit_s or konfig.lauf_zeitlimit_s())
    kopf = {"Authorization": f"Bearer {tok}"}
    start_usd = konfig.preise_je_actor(actor_name)[0]
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0), verify=_SSL) as client:
        # ---- Start: ein Fehler hier hat noch nichts gekostet (kein Lauf) ----
        r = await client.post(f"{BASIS}/acts/{actor_name}/runs",
                              params={"timeout": zeitlimit, **({"build": build} if build else {})}, headers=kopf,
                              json=eingabe(actor_name, start_urls, max_items, max_items_per_query))
        if r.status_code >= 400:
            raise _fehler_aus_status(r.status_code, r.text)
        daten = (r.json() or {}).get("data") or {}
        run_id, dataset_id = daten.get("id"), daten.get("defaultDatasetId")
        if not run_id:
            raise ApifyFehler("ausfall", "keine Lauf-ID")
        # ---- Warten: Poll-Fehler werden wiederholt, dann wird der Lauf abgebrochen (Nr. 67/68) ----
        frist = time.monotonic() + zeitlimit + 30
        status = daten.get("status") or "RUNNING"
        poll_fehler = 0
        letzter: Optional[ApifyFehler] = None
        while status not in ENDZUSTAENDE:
            if time.monotonic() > frist:
                await _abbrechen(client, run_id, kopf)
                raise ApifyFehler("zeit", f"Lauf {run_id} nicht fertig nach {zeitlimit} s", usd=start_usd, run_id=run_id)
            await asyncio.sleep(5)
            try:
                rr = await client.get(f"{BASIS}/actor-runs/{run_id}", headers=kopf)
                if rr.status_code >= 400:
                    raise _fehler_aus_status(rr.status_code, rr.text, usd=start_usd, run_id=run_id)
            except ApifyFehler as e:
                if e.art in ("token", "guthaben"):
                    raise
                letzter = e
            except (httpx.HTTPError, ValueError) as e:
                letzter = ApifyFehler("poll", str(e)[:200], usd=start_usd, run_id=run_id)
            else:
                poll_fehler = 0
                daten = (rr.json() or {}).get("data") or {}
                status = daten.get("status") or status
                dataset_id = daten.get("defaultDatasetId") or dataset_id
                continue
            poll_fehler += 1
            if poll_fehler >= POLL_VERSUCHE:
                # dreimal gescheitert: Primaerlauf beenden, BEVOR jemand einen Ersatz startet
                await _abbrechen(client, run_id, kopf)
                art = "limit" if letzter is not None and letzter.art == "limit" else "poll"
                raise ApifyFehler(art, f"Lauf {run_id}: Statusabfrage {POLL_VERSUCHE}x gescheitert ({letzter.detail if letzter else ''})",
                                  usd=start_usd, run_id=run_id)
            await asyncio.sleep(POLL_BACKOFF_S[min(poll_fehler - 1, len(POLL_BACKOFF_S) - 1)])
        if status != "SUCCEEDED":
            raise ApifyFehler("ausfall", f"Lauf {run_id} endete mit {status}", usd=start_usd, run_id=run_id)
        grenze = datensatz_limit(max_items)
        ri = await client.get(f"{BASIS}/datasets/{dataset_id}/items",
                              params={"clean": "1", "format": "json", "limit": grenze}, headers=kopf)
        if ri.status_code >= 400:
            raise _fehler_aus_status(ri.status_code, ri.text, usd=start_usd, run_id=run_id)
        items = ri.json()
        if not isinstance(items, list):
            raise ApifyFehler("ausfall", "unerwartete Antwortform", usd=start_usd, run_id=run_id)
        items = [i for i in items if isinstance(i, dict)]
        usd = kosten_aus_lauf(actor_name, daten, len(items))
        if len(items) >= grenze:
            # Nr. 87: der Actor hat weit mehr geliefert als bestellt (maxItems ignoriert) — der Rest bleibt
            # bei Apify, nichts davon ist eine gueltige Stichprobe; Kosten sind angefallen
            raise ApifyFehler("zuviel", f"Lauf {run_id}: Datensatz groesser als erwartet (>= {grenze} Zeilen bei {max_items} bestellt)",
                              usd=usd, run_id=run_id)
    return {"items": items, "usd": usd, "run_id": run_id, "status": status,
            "dauer_ms": int((time.perf_counter() - t0) * 1000), "actor": actor_voll, "laeufe": 1,
            "build_id": daten.get("buildId") or None, "build_number": daten.get("buildNumber") or None}


async def lauf_mit_ersatz(start_urls: List[str], max_items: int, *, zeitlimit_s: Optional[int] = None,
                          max_items_per_query: Optional[int] = None, rows_je_url: Optional[List[int]] = None,
                          ersatz_bei_leer: bool = True) -> Dict[str, Any]:
    """Standard-Scraper; scheitert er, einmal der Ersatz-Scraper. Token-/Guthaben-/
    Rate-Limit-Fehler (Nr. 68) betreffen beide — kein Ersatz. Buendel (mehrere URLs) laufen
    NUR beim Standard; der Ersatz kennt keine Grenze je Suche und bekommt deshalb nur eine
    URL je Lauf mit der Zeilenzahl DIESES Segments (Nr. 15: rows_je_url).

    Nr. 16: 0 Treffer sind erst dann ein Scraper-Ausfall, wenn der Aufrufer sagt, dass das
    Buendel Segmente mit Treffern in den letzten 7 Tagen enthaelt (ersatz_bei_leer); ein
    legitim leeres Buendel (Marktluecke) loest keinen Ersatz aus.
    Nr. 14: die Kosten des leeren/abgebrochenen Primaerlaufs werden dem Ersatzergebnis
    zugeschlagen (usd, run_ids beider Laeufe, laeufe = Zahl der Actor-Starts)."""
    ersatz = konfig.actor_ersatz()
    primaer_usd = 0.0
    primaer_run = ""
    primaer_laeufe = 0
    try:
        r = await lauf(start_urls, max_items, zeitlimit_s=zeitlimit_s, max_items_per_query=max_items_per_query)
        if r["items"] or not ersatz or not ersatz_bei_leer:
            return r
        erster = "0 Treffer"
        primaer_usd = float(r.get("usd") or 0)
        primaer_run = str(r.get("run_id") or "")
        primaer_laeufe = 1
    except ApifyFehler as e:
        if not ersatz or e.art in OHNE_ERSATZ:
            raise
        erster = f"{e.art}: {e.detail}"
        primaer_usd = float(e.usd or 0)
        primaer_run = str(e.run_id or "")
        primaer_laeufe = 1 if e.run_id else 0
    log.warning("Market-Scraper %s: %s — Ersatz %s", konfig.actor(), erster, ersatz)
    je_url = list(rows_je_url or [])
    if len(je_url) != len(start_urls):
        je_url = [max_items_per_query or max_items] * len(start_urls)
    items: List[Dict[str, Any]] = []
    usd = primaer_usd
    run_ids = [primaer_run] if primaer_run else []
    dauer = 0
    build: Dict[str, Any] = {}
    for u, je in zip(start_urls, je_url):
        rr = await lauf([u], int(je), zeitlimit_s=zeitlimit_s, actor_name=ersatz)
        if len(start_urls) > 1:
            for it in rr["items"]:
                it.setdefault("inputContext", u)           # Zuordnung wie beim Standard-Buendel
        items.extend(rr["items"])
        usd += float(rr.get("usd") or 0)
        run_ids.append(str(rr.get("run_id") or ""))
        dauer += int(rr.get("dauer_ms") or 0)
        build = {"build_id": rr.get("build_id"), "build_number": rr.get("build_number")}
    return {"items": items, "usd": round(usd, 4), "run_id": ",".join(x for x in run_ids if x), "status": "SUCCEEDED",
            "dauer_ms": dauer, "actor": ersatz, "ersatz_grund": erster, "laeufe": primaer_laeufe + len(start_urls),
            "primaer_usd": round(primaer_usd, 4), "primaer_run_id": primaer_run or None, **build}
