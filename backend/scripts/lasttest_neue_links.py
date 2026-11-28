"""Lasttest "180 verschiedene neue Links" (Wunsch Ahmad 16.09.2026).
Aufruf: python scripts/lasttest_neue_links.py — Umgebung wie lasttest_links_gleichzeitig.py,
dazu LASTTEST_JE_QUELLE (Standard 60). Am Backend MOCK_PROVIDER_DELAY_MS_<QUELLE> setzen,
um echte Anbieterzeiten nachzustellen.

Szenario D: 180 Sucher fuegen gleichzeitig 180 VERSCHIEDENE neue Links ein
(60 Kleinanzeigen, 60 mobile.de, 60 AutoScout). Misst je Quelle die Wartezeit
bis der Abruf fertig ist und bis der Vergleich steht. Mit den Umgebungs-
variablen MOCK_PROVIDER_DELAY_MS_<QUELLE> am Backend laesst sich die echte
Anbieterzeit nachstellen; die Differenz zur Wartezeit ist unser Anteil."""
import os
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

S = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, S)
os.environ.setdefault("LASTTEST_API", "http://127.0.0.1:8002/api")
import lasttest_links_gleichzeitig as LT  # noqa: E402
from listing_identity import get_listing_identity  # noqa: E402

JE_QUELLE = int(os.environ.get("LASTTEST_JE_QUELLE", "60"))


def links_bauen():
    out = []
    for i in range(JE_QUELLE):
        nr = 8_100_000_000 + i
        out.append(f"https://www.kleinanzeigen.de/s-anzeige/test-wagen-{i}/{nr}-216-1")
        out.append(f"https://suchen.mobile.de/fahrzeuge/details.html?id={8_200_000_000 + i}&vc=Car")
        out.append(f"https://www.autoscout24.de/angebote/test-wagen-{i}-cat_ma1gr1-{uuid.uuid4()}?sort=standard")
    return [(u, get_listing_identity(u)) for u in out]


def lauf(konten, links):
    keys = [i["cache_key"] for _, i in links]
    LT.db.listings_cache.delete_many({"cache_key": {"$in": keys}})
    LT.db.link_jobs.delete_many({"cache_key": {"$in": keys}})
    LT.db.inserat_beweise.delete_many({"cache_key": {"$in": keys}})
    LT.db.vehicles.delete_many({"dealer_id": {"$regex": f"^d_lt{LT.SUF}_"}})
    calls_vorher = sum(d.get("calls", 0) for d in LT.db.provider_stats.find({}))
    ergebnisse = [dict(link=i, user=k[2]) for i, k in enumerate(konten)]
    start = time.perf_counter() + 3.0
    # Beobachter: alle 0,25 s Job-Zustaende und belegte Anbieter-Slots
    verlauf, stop = [], {"x": False}

    def beobachten():
        import threading as _t
        while not stop["x"]:
            t = time.perf_counter() - start
            st = {}
            for r in LT.db.link_jobs.aggregate([{"$match": {"cache_key": {"$in": keys}}},
                                                {"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
                st[r["_id"]] = r["n"]
            slots = {d["provider"]: d.get("active", 0) for d in LT.db.provider_limits.find({}, {"provider": 1, "active": 1})}
            verlauf.append((t, st, slots))
            time.sleep(0.25)
    import threading
    th = threading.Thread(target=beobachten, daemon=True)
    th.start()
    with ThreadPoolExecutor(max_workers=len(konten)) as pool:
        futs = [pool.submit(LT.sucher_ablauf, k[3], links[i][0], start, ergebnisse[i])
                for i, k in enumerate(konten)]
        for f in futs:
            f.result()
    gesamt = time.perf_counter() - start
    stop["x"] = True
    th.join(timeout=2)
    print("  Verlauf (s: queued/processing/completed | Slots):")
    letzte = -1.0
    for t, st, slots in verlauf:
        if t < 0 or t - letzte < 1.0:
            continue
        letzte = t
        print(f"    {t:5.1f}s: {st.get('queued', 0):3d}/{st.get('processing', 0):3d}/{st.get('completed', 0):3d} | "
              + " ".join(f"{k}={v}" for k, v in sorted(slots.items())))
    time.sleep(1.5)
    calls_nachher = sum(d.get("calls", 0) for d in LT.db.provider_stats.find({}))
    fehler = [e for e in ergebnisse if e.get("fehler")]
    ok = [e for e in ergebnisse if not e.get("fehler")]
    print(f"\n=== Szenario D: {len(konten)} Konten, {len(links)} verschiedene neue Links gleichzeitig ===")
    print(f"erfolgreich: {len(ok)}  Fehler: {len(fehler)}  Gesamtdauer: {gesamt:.1f} s  "
          f"Anbieter-Abrufe: {calls_nachher - calls_vorher}")
    for e in fehler[:8]:
        print(f"   FEHLER Konto {e['user']}: {e['fehler']}")
    for q in ("kleinanzeigen", "mobile", "autoscout24"):
        zj = sorted(e["t_job"] for e in ok if links[e["link"]][1]["source"] == q)
        zs = sorted(e["t_gesamt"] for e in ok if links[e["link"]][1]["source"] == q)
        if not zs:
            continue
        p = lambda a, x: a[min(len(a) - 1, int(len(a) * x))]  # noqa: E731
        verz = os.environ.get(f"MOCK_PROVIDER_DELAY_MS_{q.upper()}", "?")
        print(f"  {q:13s} ({len(zs):3d}): Abruf fertig median {statistics.median(zj):.2f}s / "
              f"p90 {p(zj, .9):.2f}s / max {zj[-1]:.2f}s · bis Vergleich median "
              f"{statistics.median(zs):.2f}s / max {zs[-1]:.2f}s   (Attrappe {verz} ms)")
    mehrfach = sum(1 for _, i in links
                   if (LT.db.listings_cache.find_one({"cache_key": i["cache_key"]}, {"fetch_count": 1}) or {}).get("fetch_count") != 1)
    print(f"  Links mit fetch_count != 1: {mehrfach}")
    LT.db.listings_cache.delete_many({"cache_key": {"$in": keys}})
    LT.db.link_jobs.delete_many({"cache_key": {"$in": keys}})
    LT.db.inserat_beweise.delete_many({"cache_key": {"$in": keys}})


if __name__ == "__main__":
    links = links_bauen()
    konten = LT.konten_anlegen()
    try:
        lauf(konten, links)
    finally:
        LT.cache_zuruecksetzen()
        LT.konten_entfernen()
        print("Testdaten wieder entfernt.")
