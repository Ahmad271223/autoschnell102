# -*- coding: utf-8 -*-
"""Synchronisierter SEKUNDENSTOSS-Test (Auftrag 08/2026).

Anders als die Matrix gibt es KEINE Denkpausen: alle virtuellen Nutzer
warten an einer gemeinsamen Barrier und feuern exakt gleichzeitig. Der
Freigabezeitpunkt und der tatsaechliche Sendezeitpunkt JEDER Anfrage
werden protokolliert — der Bericht beweist, dass der Stoss innerhalb
derselben Sekunde lag (spannweite_ms).

Szenarien (je mit 100/300/500 Nutzern):
  S1  alle denselben unbekannten Link
  S2  alle unterschiedliche unbekannte Links
  S3  alle unterschiedliche BEKANNTE Links
  S4  50 % bekannt / 50 % unbekannt
  S5  Nutzer DERSELBEN Firma, derselbe Link
  S6  Nutzer VERSCHIEDENER Firmen, derselbe Link
  S7  Doppelklick: jeder Nutzer sendet denselben Check 2x gleichzeitig
  S8  Linkspitze + 20 PDF-Ersteller + 20 Foto-Uploader gleichzeitig

Nur gegen MOCK_PROVIDER_FETCH=true (das Skript verweigert sonst den
Start) — es duerfen NIE 100-500 echte Anbieterabrufe entstehen.
Berichte: docs/lasttests/stoss/<zeit>-S<x>-n<zahl>.json
"""
import argparse
import asyncio
import base64
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
AUSGABE = Path(__file__).resolve().parent.parent.parent / "docs" / "lasttests" / "stoss"

SUF = uuid.uuid4().hex[:6]
PW = "StossTest123!"      # Passwortregel: mindestens 10 Zeichen
N_FIRMEN = 10
FOTO = "data:image/jpeg;base64," + base64.b64encode(
    b"\xff\xd8\xff\xe0" + os.urandom(512 * 1024) + b"\xff\xd9").decode()


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=8000)[DB_NAME]


def _pct(v, p):
    if not v:
        return None
    v = sorted(v)
    return v[max(0, min(len(v) - 1, int(round(p / 100 * len(v))) - 1))]


class Serie:
    def __init__(self):
        self.n = 0

    def links(self, anzahl):
        s = (89_000_000_000 + (int(SUF, 16) % 5_000) * 10_000_000
             + self.n * 100_000)
        self.n += 1
        return [f"https://www.kleinanzeigen.de/s-anzeige/st/{s + i}-216-1"
                for i in range(anzahl)]


SERIE = Serie()


async def _post(sess, url, js, h, timeout=60):
    async with sess.post(url, json=js, headers=h,
                         timeout=aiohttp.ClientTimeout(total=timeout)) as r:
        return r.status, (await r.json(content_type=None)
                          if "json" in (r.headers.get("content-type") or "")
                          else {})


async def welt(sess):
    dbx = _db()
    now = datetime.now(timezone.utc)
    firmen = []
    for i in range(N_FIRMEN):
        mail = f"st_chef_{i}_{SUF}@e2etest-mail.de"
        st, js = await _post(sess, f"{API}/auth/register", {
            "email": mail, "password": PW, "company_name": f"Stoss {i}",
            "contact_person": f"C {i}", "phone": "0511 5"}, None)
        assert st == 200, f"Register {st}: {str(js)[:300]}"
        h = {"Authorization": f"Bearer {js['token']}"}
        async with sess.get(f"{API}/auth/me", headers=h) as r:
            me = (await r.json())["user"]
        dbx.subscriptions.insert_one({
            "id": str(uuid.uuid4()), "dealer_id": me["dealer_id"],
            "subject_user_id": me["id"], "plan": "monthly",
            "status": "active",
            "expires_at": (now + timedelta(days=1)).isoformat(),
            "created_at": now.isoformat()})
        firma = {"h": h, "me": me, "sucher": []}
        for k in range(2):
            smail = f"st_such_{i}_{k}_{SUF}@e2etest-mail.de"
            st, js = await _post(sess, f"{API}/dealer/sucher", {
                "email": smail, "password": PW,
                "first_name": "St", "last_name": f"S{i}{k}"}, h)
            assert st == 200
            dbx.subscriptions.insert_one({
                "id": str(uuid.uuid4()), "dealer_id": me["dealer_id"],
                "subject_user_id": js["sucher_id"], "plan": "monthly",
                "status": "active",
                "expires_at": (now + timedelta(days=1)).isoformat(),
                "created_at": now.isoformat()})
            st, js = await _post(sess, f"{API}/auth/login",
                                 {"email": smail, "password": PW}, None)
            firma["sucher"].append({"Authorization": f"Bearer {js['token']}"})
        # 1 Fahrzeug + Vertrag + Inserat je Firma (fuer S8: PDF/Foto)
        link = SERIE.links(1)[0]
        st, js = await _post(sess, f"{API}/mobile/compare", {"url": link},
                             h, timeout=120)
        assert st == 200, f"compare {st}"
        assert (js.get("vehicle") or {}).get("_mock"), \
            "ABBRUCH: Backend laeuft NICHT im Mock-Modus!"
        firma["vehicle_id"] = js["vehicle_id"]
        st, js = await _post(sess, f"{API}/contracts", {
            "vehicle_id": firma["vehicle_id"], "seller_name": "St V",
            "seller_address": "W 1", "seller_zip": "30159",
            "seller_city": "Hannover", "purchase_price": 9000,
            "pickup_date": "2099-07-01", "pickup_time": "10:00"},
            h, timeout=120)
        assert st == 200
        st, js = await _post(sess, f"{API}/resale/draft/{firma['vehicle_id']}",
                             {}, h)
        assert st == 200
        firma["listing_id"] = js["id"]
        firmen.append(firma)
    return firmen


def aufraeumen(firmen):
    dbx = _db()
    import shutil as _sh
    root = Path(__file__).resolve().parent.parent / "uploads"
    for f in firmen:
        did = f["me"]["dealer_id"]
        for c in ("subscriptions", "vehicles", "appointments",
                  "generated_pdfs", "resale_listings", "vehicle_comparisons",
                  "listing_snapshots"):
            dbx[c].delete_many({"dealer_id": did})
        dbx.dealers.delete_many({"id": did})
        for unter in ("resale", "protocol", "pickup"):
            d = root / unter / did
            if d.exists():
                _sh.rmtree(d, ignore_errors=True)
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.listings_cache.delete_many({"item_id": {"$regex": "^89"}})
    dbx.link_jobs.delete_many({"item_id": {"$regex": "^89"}})


async def sampler_10s(start_event, werte):
    import psutil
    dbx = _db()
    await start_event.wait()
    psutil.cpu_percent(interval=None)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 12:
        await asyncio.sleep(0.5)
        werte.append({
            "s_nach_freigabe": round(time.monotonic() - t0, 1),
            "cpu": psutil.cpu_percent(interval=None),
            "ram": psutil.virtual_memory().percent,
            "mongo": dbx.client.admin.command("serverStatus")
                     ["connections"]["current"],
            "queue": dbx.link_jobs.count_documents(
                {"status": {"$in": ["queued", "processing"]}}),
        })


async def stoss(sess, name, nutzer, links, header, extra_tasks=(),
                doppelklick=False, poll_fertig=True):
    """Kern: alle Tasks an EINER Barrier freigeben, Sendezeit + Antwort
    protokollieren; optional bis completed pollen."""
    start_event = asyncio.Event()
    ergebnisse = []
    system = []

    async def einer(idx):
        url = links[idx % len(links)]
        h = header[idx % len(header)]
        await start_event.wait()
        gesendet = datetime.now(timezone.utc)
        t0 = time.monotonic()
        try:
            st, js = await _post(sess, f"{API}/listings/check",
                                 {"url": url}, h, timeout=90)
        except Exception as exc:
            ergebnisse.append({"idx": idx, "status": 599,
                               "fehler": type(exc).__name__,
                               "gesendet": gesendet.isoformat()})
            return
        annahme_ms = (time.monotonic() - t0) * 1000
        eintrag = {"idx": idx, "status": st,
                   "gesendet": gesendet.isoformat(),
                   "annahme_ms": round(annahme_ms, 1),
                   "job_status": js.get("status"),
                   "job_id": js.get("job_id"), "url": url}
        if poll_fertig and st == 200 and js.get("status") not in (
                "completed",):
            ende = time.monotonic() + 150
            fertig = None
            while time.monotonic() < ende:
                await asyncio.sleep(1.5)
                try:
                    async with sess.get(
                            f"{API}/listings/check/{js['job_id']}",
                            headers=h) as r:
                        if r.status == 404:
                            fertig = "completed"
                            break
                        zj = await r.json()
                except Exception:
                    continue
                if zj["status"] in ("completed", "failed"):
                    fertig = zj["status"]
                    break
            eintrag["fertig_nach_ms"] = round(
                (time.monotonic() - t0) * 1000)
            eintrag["endstatus"] = fertig or "timeout"
        elif st == 200 and js.get("status") == "completed":
            eintrag["fertig_nach_ms"] = round(annahme_ms, 1)
            eintrag["endstatus"] = "sofort"
        ergebnisse.append(eintrag)

    tasks = []
    n_tasks = nutzer * (2 if doppelklick else 1)
    for i in range(n_tasks):
        # Bei Doppelklick: Task-Paar (2k, 2k+1) nutzt denselben Link+Header
        idx = i // 2 if doppelklick else i
        tasks.append(asyncio.create_task(einer(idx)))
    tasks.append(asyncio.create_task(sampler_10s(start_event, system)))
    for f in extra_tasks:
        tasks.append(asyncio.create_task(f(start_event)))

    await asyncio.sleep(1.5)              # alle Tasks stehen an der Barrier
    freigabe = datetime.now(timezone.utc)
    start_event.set()
    await asyncio.gather(*tasks)
    return freigabe, ergebnisse, system


async def lauf(sess, firmen, szenario, nutzer):
    dbx = _db()
    alle_header = [f["h"] for f in firmen] + \
                  [s for f in firmen for s in f["sucher"]]
    warm = SERIE.links(nutzer)
    extra = []
    doppel = False

    if szenario == "S1":
        links, header = SERIE.links(1), alle_header
    elif szenario == "S2":
        links, header = SERIE.links(nutzer), alle_header
    elif szenario == "S3":
        links, header = warm, alle_header
        for u in warm:                       # vorwaermen
            await _post(sess, f"{API}/mobile/compare", {"url": u},
                        firmen[0]["h"], timeout=120)
    elif szenario == "S4":
        for u in warm[:nutzer // 2]:
            await _post(sess, f"{API}/mobile/compare", {"url": u},
                        firmen[0]["h"], timeout=120)
        links = warm[:nutzer // 2] + SERIE.links(nutzer - nutzer // 2)
        header = alle_header
    elif szenario == "S5":
        links = SERIE.links(1)
        f0 = firmen[0]
        header = [f0["h"]] + f0["sucher"]
    elif szenario == "S6":
        links, header = SERIE.links(1), [f["h"] for f in firmen]
    elif szenario == "S7":
        links, header, doppel = SERIE.links(nutzer), alle_header, True
    elif szenario == "S8":
        links, header = SERIE.links(nutzer), alle_header

        async def pdfs(start_event):
            await start_event.wait()
            async def ein_pdf(i):
                f = firmen[i % len(firmen)]
                try:
                    await _post(sess, f"{API}/contracts", {
                        "vehicle_id": f["vehicle_id"],
                        "seller_name": "Spitze", "seller_address": "W",
                        "seller_zip": "30159", "seller_city": "H",
                        "purchase_price": 7000,
                        "pickup_date": "2099-07-02",
                        "pickup_time": "09:00"}, f["h"], timeout=120)
                except Exception:
                    pass
            await asyncio.gather(*[ein_pdf(i) for i in range(20)])

        async def fotos(start_event):
            await start_event.wait()
            async def ein_foto(i):
                f = firmen[i % len(firmen)]
                try:
                    await _post(sess,
                                f"{API}/resale/{f['listing_id']}/photos",
                                {"photos_b64": [FOTO]}, f["h"], timeout=120)
                except Exception:
                    pass
            await asyncio.gather(*[ein_foto(i) for i in range(20)])
        extra = [pdfs, fotos]

    jobs_vorher = dbx.link_jobs.count_documents({})
    freigabe, erg, system = await stoss(
        sess, szenario, nutzer, links, header, extra_tasks=extra,
        doppelklick=doppel)

    # Erreichbarkeit einer normalen Seite WAEHREND der Spitze pruefen
    t0 = time.monotonic()
    async with sess.get(f"{API}/bestand", headers=firmen[0]["h"]) as r:
        seite_ms = round((time.monotonic() - t0) * 1000)
        seite_ok = r.status == 200

    # Drain
    t0 = time.monotonic()
    while time.monotonic() - t0 < 300:
        if dbx.link_jobs.count_documents(
                {"status": {"$in": ["queued", "processing"]}}) == 0:
            break
        await asyncio.sleep(2)
    drain_s = round(time.monotonic() - t0)

    # Auswertung
    sende = [datetime.fromisoformat(e["gesendet"]) for e in erg]
    spann = round((max(sende) - min(sende)).total_seconds() * 1000)
    annahmen = sorted(e["annahme_ms"] for e in erg if "annahme_ms" in e)
    fertig = sorted(e["fertig_nach_ms"] for e in erg
                    if e.get("fertig_nach_ms") is not None)
    sofort = sum(1 for e in erg if e.get("endstatus") == "sofort")
    nach_queue = sum(1 for e in erg if e.get("endstatus") == "completed")
    verloren = sum(1 for e in erg
                   if e.get("endstatus") in (None, "timeout")
                   and e.get("job_status") != "completed"
                   and e["status"] == 200)
    st_zaehler = {}
    for e in erg:
        st_zaehler[str(e["status"])] = st_zaehler.get(str(e["status"]), 0) + 1

    eigene_ids = {str(x) for u in links
                  for x in [u.rsplit("/", 1)[-1].split("-")[0]]}
    doppelte_abrufe = dbx.listings_cache.count_documents(
        {"item_id": {"$in": list(eigene_ids)}, "fetch_count": {"$gt": 1}})
    jobs_je_key = list(dbx.link_jobs.aggregate([
        {"$match": {"item_id": {"$in": list(eigene_ids)}}},
        {"$group": {"_id": "$cache_key", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}}]))
    snap_doppel = list(dbx.listing_snapshots.aggregate([
        {"$match": {"mobile_ad_id": {"$in": list(eigene_ids)}}},
        {"$group": {"_id": "$mobile_ad_id", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}}]))
    haenger = dbx.link_jobs.count_documents({
        "status": "processing",
        "processing_until": {"$lt": datetime.now(timezone.utc)}})

    report = {
        "szenario": szenario, "nutzer": nutzer,
        "zeitstempel": datetime.now(timezone.utc).isoformat(),
        "freigabe": freigabe.isoformat(),
        "stoss_spannweite_ms": spann,
        "antworten_nach_status": st_zaehler,
        "annahme_ms": {"p50": _pct(annahmen, 50), "p95": _pct(annahmen, 95),
                       "p99": _pct(annahmen, 99),
                       "max": annahmen[-1] if annahmen else None},
        "fertig_ms": {"p50": _pct(fertig, 50), "p95": _pct(fertig, 95),
                      "p99": _pct(fertig, 99),
                      "max": fertig[-1] if fertig else None},
        "sofort_verfuegbar": sofort,
        "nach_warteschlange_fertig": nach_queue,
        "verlorene_requests": verloren,
        "queue_max": max((s["queue"] for s in system), default=0),
        "drain_s": drain_s,
        "doppelte_anbieter_abrufe": doppelte_abrufe,
        "doppelte_jobs_je_inserat": len(jobs_je_key),
        "doppelte_snapshots": len(snap_doppel),
        "haengende_jobs": haenger,
        "neue_jobs_gesamt": dbx.link_jobs.count_documents({}) - jobs_vorher,
        "normale_seite_waehrend_spitze": {"ok": seite_ok, "ms": seite_ms},
        "system_erste_10s": system,
        "einzel_ergebnisse": erg if len(erg) <= 120 else erg[:120],
    }
    AUSGABE.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pfad = AUSGABE / f"{ts}-{szenario}-n{nutzer}.json"
    pfad.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    print(f"[{szenario} n={nutzer}] Spannweite {spann} ms | "
          f"Annahme p95 {report['annahme_ms']['p95']} ms | sofort {sofort} "
          f"| Queue-fertig {nach_queue} | verloren {verloren} | "
          f"Doppel-Abrufe {doppelte_abrufe} | Drain {drain_s}s "
          f"-> {pfad.name}")
    return report


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--szenario", default=None, help="S1..S8 oder Liste")
    ap.add_argument("--nutzer", default="100,300,500")
    a = ap.parse_args()
    szenarien = (a.szenario.split(",") if a.szenario
                 else [f"S{i}" for i in range(1, 9)])
    stufen = [int(x) for x in a.nutzer.split(",")]

    conn = aiohttp.TCPConnector(limit=1200)
    async with aiohttp.ClientSession(
            connector=conn,
            timeout=aiohttp.ClientTimeout(total=180)) as sess:
        firmen = await welt(sess)
        try:
            for sz in szenarien:
                for n in stufen:
                    await lauf(sess, firmen, sz, n)
                    await asyncio.sleep(5)
        finally:
            aufraeumen(firmen)
    print("Stoss-Test fertig.")


if __name__ == "__main__":
    asyncio.run(main())
