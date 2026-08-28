# -*- coding: utf-8 -*-
"""Staging-Lasttest: simuliert N parallele Sucher gegen ein laufendes Backend.

Misst je Endpunkt p50/p95/p99, Fehlerrate, dazu MongoDB-Verbindungen,
CPU/RAM des Rechners und die TATSAECHLICHE Zahl externer Provider-Abrufe
(aus provider_stats — beweist providerfreundliches Verhalten).

Voraussetzungen:
  - Backend laeuft (TEST_BASE_URL, Standard http://localhost:8001)
  - Fuer den Neue-Links-Anteil MUSS das Backend mit MOCK_PROVIDER_FETCH=true
    laufen — sonst wuerden echte Kleinanzeigen-Abrufe ausgeloest!
    (Das Skript verweigert Neue-Links-Last, wenn der Mock nicht antwortet.)

Aufruf (Beispiel, 300 Nutzer, 3 Minuten):
  python -X utf8 scripts/lasttest.py --users 300 --duration 180
"""
import argparse
import asyncio
import json
import os
import random
import time
import uuid
from datetime import datetime, timezone

import aiohttp

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"

SUFFIX = uuid.uuid4().hex[:8]
PW = "Lasttest123!"


def _pct(sorted_ms, p):
    if not sorted_ms:
        return None
    idx = min(len(sorted_ms) - 1, int(round(p / 100 * len(sorted_ms))) - 1)
    return sorted_ms[max(0, idx)]


class Stats:
    def __init__(self):
        self.samples = {}     # name -> [ms]
        self.errors = {}      # name -> {status: n}
        self.total = 0

    def add(self, name, ms, status):
        self.total += 1
        if 200 <= status < 300:
            self.samples.setdefault(name, []).append(ms)
        else:
            self.errors.setdefault(name, {}).setdefault(status, 0)
            self.errors[name][status] += 1

    def report(self):
        out = {}
        for name, vals in sorted(self.samples.items()):
            vals = sorted(vals)
            errs = self.errors.get(name, {})
            n_err = sum(errs.values())
            out[name] = {
                "ok": len(vals), "fehler": n_err,
                "fehlerrate_prozent": round(
                    100 * n_err / max(1, len(vals) + n_err), 2),
                "p50_ms": round(_pct(vals, 50) or 0),
                "p95_ms": round(_pct(vals, 95) or 0),
                "p99_ms": round(_pct(vals, 99) or 0),
                "max_ms": round(vals[-1]) if vals else None,
                "fehler_nach_status": errs,
            }
        for name, errs in self.errors.items():
            if name not in out:
                out[name] = {"ok": 0, "fehler": sum(errs.values()),
                             "fehlerrate_prozent": 100.0,
                             "fehler_nach_status": errs}
        return out


async def _timed(session, stats, name, method, url, **kw):
    t0 = time.monotonic()
    try:
        async with session.request(method, url, **kw) as r:
            await r.read()
            stats.add(name, (time.monotonic() - t0) * 1000, r.status)
            return r.status
    except Exception:
        stats.add(name, (time.monotonic() - t0) * 1000, 599)
        return 599


async def register_user(session, i):
    mail = f"last_{SUFFIX}_{i}@e2etest-mail.de"
    async with session.post(f"{API}/auth/register", json={
            "email": mail, "password": PW,
            "company_name": f"Lasttest {i}", "contact_person": "L T",
            "phone": "0511 0"}) as r:
        if r.status != 200:
            return None
        tok = (await r.json())["token"]
    return {"mail": mail, "token": tok}


def seed_subscriptions_and_get_metrics_before():
    """Abos fuer alle Lasttest-Nutzer direkt in der DB setzen (compare
    verlangt ein aktives Abo) + Provider-Zaehler VOR dem Test lesen."""
    from pymongo import MongoClient
    from datetime import timedelta
    dbx = MongoClient(MONGO_URL)[DB_NAME]
    users = list(dbx.users.find({"email": {"$regex": f"^last_{SUFFIX}_"}},
                                {"id": 1, "dealer_id": 1}))
    now = datetime.now(timezone.utc)
    for u in users:
        dbx.subscriptions.insert_one({
            "id": str(uuid.uuid4()), "dealer_id": u["dealer_id"],
            "subject_user_id": u["id"], "plan": "monthly", "status": "active",
            "expires_at": (now + timedelta(days=1)).isoformat(),
            "created_at": now.isoformat()})
    today = now.strftime("%Y-%m-%d")
    row = dbx.provider_stats.find_one({"provider": "kleinanzeigen",
                                       "date": today}) or {}
    return len(users), int(row.get("calls", 0))


def metrics_after(calls_before):
    from pymongo import MongoClient
    dbx = MongoClient(MONGO_URL)[DB_NAME]
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    row = dbx.provider_stats.find_one({"provider": "kleinanzeigen",
                                       "date": today}) or {}
    server = dbx.client.admin.command("serverStatus")
    return {
        "externe_provider_abrufe_im_test": int(row.get("calls", 0)) - calls_before,
        "mongo_verbindungen_aktuell": server.get("connections", {}).get("current"),
        "mongo_verbindungen_verfuegbar": server.get("connections", {}).get("available"),
    }


def cleanup():
    from pymongo import MongoClient
    dbx = MongoClient(MONGO_URL)[DB_NAME]
    uids = [u["id"] for u in dbx.users.find(
        {"email": {"$regex": f"^last_{SUFFIX}_"}}, {"id": 1})]
    dids = [d["id"] for d in dbx.dealers.find(
        {"user_id": {"$in": uids}}, {"id": 1})]
    for c in ("subscriptions", "vehicle_comparisons", "vehicles"):
        dbx[c].delete_many({"dealer_id": {"$in": dids}})
    dbx.subscriptions.delete_many({"subject_user_id": {"$in": uids}})
    dbx.listings_cache.delete_many({"item_id": {"$regex": "^99"}})
    dbx.listings_cache_client.delete_many({"item_id": {"$regex": "^99"}})
    dbx.dealers.delete_many({"id": {"$in": dids}})
    dbx.users.delete_many({"id": {"$in": uids}})


async def user_loop(session, user, stats, deadline, n_links):
    """Ein simulierter Sucher: mischt neue Links, bekannte Links und
    Marktplatz-/Bestandsaufrufe — wie echtes Verhalten."""
    h = {"Authorization": f"Bearer {user['token']}"}
    while time.monotonic() < deadline:
        dice = random.random()
        if dice < 0.25:
            # NEUER Link (Mock-Provider): der harte Pfad (Lease+Limiter)
            iid = 9900000000 + random.randrange(n_links)
            await _timed(session, stats, "vergleich_neuer_link", "POST",
                         f"{API}/mobile/compare", headers=h,
                         json={"url": f"https://www.kleinanzeigen.de/s-anzeige/last/{iid}-216-1"})
        elif dice < 0.75:
            # BEKANNTER Link: der haeufigste Fall (Cache-Treffer)
            iid = 9900000000 + random.randrange(max(1, n_links // 4))
            await _timed(session, stats, "vergleich_bekannter_link", "POST",
                         f"{API}/mobile/compare", headers=h,
                         json={"url": f"https://www.kleinanzeigen.de/s-anzeige/last/{iid}-216-1"})
        elif dice < 0.9:
            await _timed(session, stats, "bestand", "GET",
                         f"{API}/bestand", headers=h)
        else:
            await _timed(session, stats, "abo_status", "GET",
                         f"{API}/dealer/subscription", headers=h)
        await asyncio.sleep(random.uniform(0.2, 1.2))


async def sample_system(deadline, cpu_samples):
    try:
        import psutil
    except ImportError:
        return
    while time.monotonic() < deadline:
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
        cpu_samples.append((cpu, ram))
        await asyncio.sleep(2)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=300)
    ap.add_argument("--duration", type=int, default=180)
    ap.add_argument("--links", type=int, default=200,
                    help="Zahl unterschiedlicher Test-Inserate")
    args = ap.parse_args()

    conn = aiohttp.TCPConnector(limit=args.users + 50)
    timeout = aiohttp.ClientTimeout(total=75)
    async with aiohttp.ClientSession(connector=conn, timeout=timeout) as session:
        # SICHERHEITSCHECK: Der Mock-Modus MUSS aktiv sein — sonst wuerde
        # dieser Lasttest hunderte ECHTE Abrufe bei Kleinanzeigen ausloesen.
        # Wir pruefen das mit einem einzigen Vergleich und brechen ab, wenn
        # die Antwort nicht als synthetisch markiert ist.
        probe_user = await register_user(session, "probe")
        assert probe_user, "Registrierung kaputt — Backend erreichbar?"
        seed_subscriptions_and_get_metrics_before()
        async with session.post(
                f"{API}/mobile/compare",
                headers={"Authorization": f"Bearer {probe_user['token']}"},
                json={"url": "https://www.kleinanzeigen.de/s-anzeige/probe/"
                             "9900000000-216-1"}) as r:
            body = await r.json() if r.status == 200 else {}
        if not (body.get("vehicle") or {}).get("_mock"):
            cleanup()
            raise SystemExit(
                "ABBRUCH: Das Backend laeuft NICHT im Mock-Modus "
                "(MOCK_PROVIDER_FETCH=true). Ohne ihn wuerde der Lasttest "
                "echte Abrufe bei Kleinanzeigen/mobile.de ausloesen.")
        print("[0/4] Mock-Modus bestaetigt — keine echten Anbieter-Abrufe.")

        print(f"[1/4] Registriere {args.users} Test-Nutzer …")
        users = []
        for batch in range(0, args.users, 25):
            batch_users = await asyncio.gather(
                *[register_user(session, i)
                  for i in range(batch, min(batch + 25, args.users))])
            users += [u for u in batch_users if u]
        print(f"      {len(users)} Nutzer registriert")

        n_users, calls_before = seed_subscriptions_and_get_metrics_before()
        print(f"[2/4] {n_users} Abos gesetzt. Provider-Abrufe vorher: {calls_before}")

        stats = Stats()
        cpu_samples = []
        deadline = time.monotonic() + args.duration
        print(f"[3/4] Lasttest: {len(users)} parallele Nutzer, "
              f"{args.duration}s, {args.links} verschiedene Inserate …")
        await asyncio.gather(
            sample_system(deadline, cpu_samples),
            *[user_loop(session, u, stats, deadline, args.links)
              for u in users])

        print("[4/4] Auswertung …")
        report = {
            "konfiguration": {"nutzer": len(users), "dauer_s": args.duration,
                              "verschiedene_inserate": args.links,
                              "ziel": BASE,
                              "zeitpunkt": datetime.now(timezone.utc).isoformat()},
            "anfragen_gesamt": stats.total,
            "endpunkte": stats.report(),
            "system": metrics_after(calls_before),
        }
        if cpu_samples:
            cpus = sorted(c for c, _ in cpu_samples)
            rams = sorted(r for _, r in cpu_samples)
            report["system"]["cpu_prozent_p50"] = round(_pct(cpus, 50) or 0)
            report["system"]["cpu_prozent_max"] = round(cpus[-1])
            report["system"]["ram_prozent_max"] = round(rams[-1])
        print(json.dumps(report, indent=2, ensure_ascii=False))
        out = os.path.join(os.path.dirname(__file__), "..",
                           f"lasttest-bericht-{SUFFIX}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\nBericht gespeichert: {os.path.abspath(out)}")
        cleanup()
        print("Testdaten aufgeraeumt.")


if __name__ == "__main__":
    asyncio.run(main())
