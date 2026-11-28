# -*- coding: utf-8 -*-
"""Lasttest "180 Sucher, 30 neue Links" (Wunsch Ahmad 16.09.2026): 180 Sucher in 6 Firmen, 30 verschiedene
neue Links. Je Link EIN Erst-Abrufer und FUENF Mitwartende aus derselben Firma.
Szenario A: alle 180 exakt gleichzeitig. Szenario B: je 0,01 s versetzt.
Gemessen: Wartezeit je Konto (Link pruefen -> Job fertig -> Vergleich),
Fehler, Einmal-Abruf je Link, Besitzer/Mitbearbeiter.
Aufruf: python scripts/lasttest_links_gleichzeitig.py [links.txt]
Umgebung: LASTTEST_API (Standard http://127.0.0.1:8002/api), MONGO_URL, DB_NAME,
LASTTEST_NUR_A=1 (nur Szenario A). Legt 180 Wegwerf-Konten an und entfernt sie wieder.
Gegen das Test-Backend mit MOCK_PROVIDER_FETCH=true ist der Anbieter-Abruf eine
0,4-s-Attrappe (keine echten Anfragen an mobile.de/AutoScout); gegen prod2 (siehe
deploy/lasttest-auf-prod2.sh) zaehlen die Abrufe wirklich und kosten Geld."""
import os
import re
import statistics
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests
from pymongo import MongoClient

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)
from auth import create_token  # noqa: E402
from listing_identity import get_listing_identity  # noqa: E402

API = os.environ.get("LASTTEST_API", "http://127.0.0.1:8002/api")
DB_NAME = os.environ.get("DB_NAME", "autoschnell_r29")
db = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))[DB_NAME]
SUF = uuid.uuid4().hex[:6]
FIRMEN, JE_FIRMA, MITWARTENDE = 6, 30, 5

ROHTEXT = open(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "lasttest_links.txt"),
              encoding="utf-8").read()


def _jetzt(delta=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=delta)).isoformat()


# ---------------------------------------------------------------- Links
def links_parsen(text):
    teile = re.split(r"(?=https://)", text)
    gesehen, out = set(), []
    for t in teile:
        t = t.strip()
        if not t.startswith("https://"):
            continue
        try:
            ident = get_listing_identity(t)
        except Exception as exc:  # noqa: BLE001
            print(f"  uebersprungen (keine Inserats-URL): {t[:70]}… — {exc}")
            continue
        if ident["cache_key"] in gesehen:
            print(f"  doppelt: {ident['cache_key']}")
            continue
        gesehen.add(ident["cache_key"])
        out.append((t, ident))
    return out


LINKS = links_parsen(ROHTEXT)
while len(LINKS) < 30:
    nr = 9_000_000_000 + len(LINKS)
    url = f"https://suchen.mobile.de/fahrzeuge/details.html?id={nr}&vc=Car"
    LINKS.append((url, get_listing_identity(url)))
    print(f"  ergaenzt (synthetisch): {url}")
LINKS = LINKS[:30]
print(f"{len(LINKS)} Links: " + ", ".join(sorted({i['source'] for _, i in LINKS})))


# ---------------------------------------------------------------- Konten
def konten_anlegen():
    konten = []          # (firma_index, sucher_index, user_id, token)
    for k in range(FIRMEN):
        did, chef = f"d_lt{SUF}_{k}", f"u_ltchef{SUF}_{k}"
        db.dealers.insert_one({"id": did, "user_id": chef, "company_name": f"Lasttest {SUF} {k}",
                               "email": f"chef{SUF}_{k}@example.com", "active_profile": "inland",
                               "created_at": _jetzt()})
        db.users.insert_one({"id": chef, "email": f"chef{SUF}_{k}@example.com", "role": "dealer",
                             "dealer_id": did, "active": True, "password_hash": "x",
                             "current_session_id": str(uuid.uuid4()), "created_at": _jetzt()})
        for j in range(JE_FIRMA):
            uid, sid = f"u_lt{SUF}_{k}_{j}", str(uuid.uuid4())
            db.users.insert_one({"id": uid, "email": f"lt{SUF}_{k}_{j}@example.com", "role": "sucher",
                                 "dealer_id": did, "active": True, "password_hash": "x",
                                 "current_session_id": sid, "created_at": _jetzt()})
            db.subscriptions.insert_one({"id": str(uuid.uuid4()), "dealer_id": did,
                                         "subject_user_id": uid, "plan": "monthly", "status": "active",
                                         "expires_at": _jetzt(86400), "created_at": _jetzt()})
            konten.append((k, j, uid, create_token(uid, sid)))
    return konten


def konten_entfernen():
    ids = {"dealer_id": {"$regex": f"^d_lt{SUF}_"}}
    for coll in ("users", "subscriptions", "vehicles", "vehicle_comparisons", "activity_logs",
                 "kaufvorgaenge", "abruf_slots"):
        db[coll].delete_many(ids)
    db.users.delete_many({"id": {"$regex": f"^u_lt(chef)?{SUF}_"}})
    db.dealers.delete_many({"id": {"$regex": f"^d_lt{SUF}_"}})
    db.abruf_slots.delete_many({"_id": {"$regex": f"^u_lt{SUF}_"}})


def cache_zuruecksetzen():
    keys = [i["cache_key"] for _, i in LINKS]
    db.listings_cache.delete_many({"cache_key": {"$in": keys}})
    db.listings_cache_client.delete_many({"cache_key": {"$in": keys}})
    db.link_jobs.delete_many({"cache_key": {"$in": keys}})
    db.inserat_beweise.delete_many({"cache_key": {"$in": keys}})
    db.vehicles.delete_many({"dealer_id": {"$regex": f"^d_lt{SUF}_"}})
    db.vehicle_comparisons.delete_many({"dealer_id": {"$regex": f"^d_lt{SUF}_"}})


# ---------------------------------------------------------------- Ablauf je Konto
def sucher_ablauf(token, url, start_at, ergebnis):
    """Wie die Oberflaeche: /listings/check -> Job pollen -> /mobile/compare."""
    H = {"Authorization": f"Bearer {token}"}
    while time.perf_counter() < start_at:
        time.sleep(0.0005)
    t0 = time.perf_counter()
    polls, wiederholungen = 0, 0
    try:
        for _ in range(8):
            r = requests.post(f"{API}/listings/check", json={"url": url}, headers=H, timeout=60)
            if r.status_code == 503:
                wiederholungen += 1
                time.sleep(0.5)
                continue
            break
        ergebnis["check_code"] = r.status_code
        if r.status_code != 200:
            ergebnis["fehler"] = f"check {r.status_code}: {r.text[:120]}"
            return
        st = r.json()
        ergebnis["check_status"] = st.get("status")
        t_check = time.perf_counter()
        if st.get("status") in ("queued", "processing"):
            job_id = st["job_id"]
            deadline = time.perf_counter() + 120
            while time.perf_counter() < deadline:
                polls += 1
                r = requests.get(f"{API}/listings/check/{job_id}", headers=H, timeout=60)
                if r.status_code == 429:
                    time.sleep(0.5)
                    continue
                if r.status_code != 200:
                    ergebnis["fehler"] = f"status {r.status_code}: {r.text[:120]}"
                    return
                d = r.json()
                if d["status"] == "completed":
                    break
                if d["status"] == "failed":
                    ergebnis["fehler"] = f"job failed: {d.get('error')}"
                    return
                time.sleep(0.2)
        t_job = time.perf_counter()
        for _ in range(6):
            r = requests.post(f"{API}/mobile/compare", json={"url": url}, headers=H, timeout=120)
            if r.status_code in (503, 429):
                wiederholungen += 1
                time.sleep(0.5)
                continue
            break
        ergebnis["compare_code"] = r.status_code
        if r.status_code != 200:
            ergebnis["fehler"] = f"compare {r.status_code}: {r.text[:120]}"
            return
        d = r.json()
        ergebnis["cached"] = d.get("cached")
        ergebnis["kollege"] = bool(d.get("kollege"))
        t1 = time.perf_counter()
        ergebnis.update({"t_check": t_check - t0, "t_job": t_job - t0, "t_gesamt": t1 - t0,
                         "polls": polls, "wiederholungen": wiederholungen})
    except Exception as exc:  # noqa: BLE001
        ergebnis["fehler"] = f"exception: {exc}"[:160]


def szenario(name, konten, versatz):
    cache_zuruecksetzen()
    calls_vorher = sum(d.get("calls", 0) for d in db.provider_stats.find({}))
    # Zuordnung: Link i -> Firma i % 6, Erst-Abrufer + 5 Mitwartende aus dieser Firma
    auftraege = []      # (reihenfolge, rolle, link_idx, konto)
    for i, (url, ident) in enumerate(LINKS):
        k, m = i % FIRMEN, i // FIRMEN
        firma = [x for x in konten if x[0] == k]
        gruppe = firma[m * (MITWARTENDE + 1):(m + 1) * (MITWARTENDE + 1)]
        auftraege.append(("erst", i, gruppe[0]))
        for x in gruppe[1:]:
            auftraege.append(("mit", i, x))
    assert len(auftraege) == FIRMEN * JE_FIRMA == 180
    ergebnisse = [dict(rolle=r, link=i, firma=x[0], user=x[2]) for r, i, x in auftraege]
    start = time.perf_counter() + 3.0
    with ThreadPoolExecutor(max_workers=len(auftraege)) as pool:
        futs = []
        for n, (r, i, x) in enumerate(auftraege):
            futs.append(pool.submit(sucher_ablauf, x[3], LINKS[i][0], start + n * versatz, ergebnisse[n]))
        for f in futs:
            f.result()
    gesamt_ende = time.perf_counter() - start
    time.sleep(1.5)     # Nachlaeufer (Beweis-Vormerkung, Audit) abwarten
    calls_nachher = sum(d.get("calls", 0) for d in db.provider_stats.find({}))

    # ---- Auswertung
    fehler = [e for e in ergebnisse if e.get("fehler")]
    ok = [e for e in ergebnisse if not e.get("fehler")]
    print(f"\n=== Szenario {name} (Versatz {versatz*1000:.0f} ms je Konto) ===")
    print(f"180 Konten, Gesamtdauer bis zum letzten Vergleich: {gesamt_ende:.1f} s")
    print(f"erfolgreich: {len(ok)}  Fehler: {len(fehler)}")
    for e in fehler[:10]:
        print(f"   FEHLER {e['rolle']} Link {e['link']} Konto {e['user']}: {e['fehler']}")
    for rolle in ("erst", "mit"):
        zs = sorted(e["t_gesamt"] for e in ok if e["rolle"] == rolle)
        zj = sorted(e["t_job"] for e in ok if e["rolle"] == rolle)
        if zs:
            p = lambda a, q: a[min(len(a) - 1, int(len(a) * q))]  # noqa: E731
            print(f"  {'Erst-Abrufer' if rolle == 'erst' else 'Mitwartende '} ({len(zs)}): "
                  f"bis Job fertig median {statistics.median(zj):.2f}s / max {zj[-1]:.2f}s · "
                  f"gesamt median {statistics.median(zs):.2f}s, p90 {p(zs, 0.9):.2f}s, max {zs[-1]:.2f}s")
    polls = [e.get("polls", 0) for e in ok]
    wdh = sum(e.get("wiederholungen", 0) for e in ok)
    print(f"  Statusabfragen je Konto: median {statistics.median(polls) if polls else 0:.0f}, "
          f"max {max(polls) if polls else 0}; Wiederholungen nach 503/429: {wdh}")
    stati = {}
    for e in ok:
        stati[e.get("check_status")] = stati.get(e.get("check_status"), 0) + 1
    print(f"  Antworten auf 'Link pruefen': {stati}")
    # Unterschied je Quelle (Kleinanzeigen / mobile.de / AutoScout)
    quellen = {}
    for e in ok:
        quellen.setdefault(LINKS[e["link"]][1]["source"], []).append(e["t_gesamt"])
    for q, zs in sorted(quellen.items()):
        zs.sort()
        n_links = sum(1 for _, i in LINKS if i["source"] == q)
        print(f"  Quelle {q:13s} ({n_links:2d} Links, {len(zs):3d} Konten): gesamt median "
              f"{statistics.median(zs):.2f}s, p90 {zs[min(len(zs) - 1, int(len(zs) * 0.9))]:.2f}s, "
              f"max {zs[-1]:.2f}s")
    # Einmal-Abruf je Link
    mehrfach = []
    for url, ident in LINKS:
        c = db.listings_cache.find_one({"cache_key": ident["cache_key"]}, {"fetch_count": 1, "data": 1})
        fc = (c or {}).get("fetch_count", 0)
        if fc != 1 or not (c or {}).get("data"):
            mehrfach.append((ident["cache_key"], fc))
    print(f"  Anbieter-Abrufe (Statistik): {calls_nachher - calls_vorher} fuer {len(LINKS)} Links; "
          f"Links mit fetch_count != 1: {mehrfach or 'keine'}")
    # Vergleichseintraege und Besitzer/Mitbearbeiter
    n_vgl = db.vehicle_comparisons.count_documents({"dealer_id": {"$regex": f"^d_lt{SUF}_"}})
    falsch = []
    for i, (url, ident) in enumerate(LINKS):
        k = i % FIRMEN
        v = db.vehicles.find_one({"dealer_id": f"d_lt{SUF}_{k}", "mobile_ad_id": ident["item_id"]},
                                 {"owner_user_id": 1, "mitbearbeiter_ids": 1})
        if not v:
            falsch.append((i, "kein Fahrzeug"))
            continue
        mit = set(v.get("mitbearbeiter_ids") or [])
        if not v.get("owner_user_id") or len(mit) != MITWARTENDE or v["owner_user_id"] in mit:
            falsch.append((i, f"besitzer={v.get('owner_user_id')} mitbearbeiter={len(mit)}"))
    print(f"  Vergleichseintraege: {n_vgl} (erwartet 180); Fahrzeuge je Firma mit 1 Besitzer + "
          f"{MITWARTENDE} Mitbearbeitern: {'alle korrekt' if not falsch else falsch[:6]}")
    kollegen = sum(1 for e in ok if e["rolle"] == "mit" and e.get("kollege"))
    print(f"  Mitwartende, die als Mitbearbeiter eingetragen wurden (Antwort 'kollege'): {kollegen} von 150")
    return ergebnisse


def neuladen(name, konten, link_idx, anzahl=150):
    """Viele Sucher laden denselben, bereits bekannten Link gleichzeitig neu
    (Cache-Treffer: Link pruefen -> Vergleich, kein Anbieter-Abruf)."""
    url, ident = LINKS[link_idx]
    calls_vorher = sum(d.get("calls", 0) for d in db.provider_stats.find({}))
    fc_vorher = (db.listings_cache.find_one({"cache_key": ident["cache_key"]},
                                            {"fetch_count": 1}) or {}).get("fetch_count")
    auswahl = []
    for k in range(FIRMEN):
        auswahl.extend([x for x in konten if x[0] == k][-25:])
    auswahl = auswahl[:anzahl]
    ergebnisse = [dict(rolle="neu", link=link_idx, firma=x[0], user=x[2]) for x in auswahl]
    start = time.perf_counter() + 2.0
    with ThreadPoolExecutor(max_workers=len(auswahl)) as pool:
        futs = [pool.submit(sucher_ablauf, x[3], url, start, ergebnisse[n])
                for n, x in enumerate(auswahl)]
        for f in futs:
            f.result()
    gesamt = time.perf_counter() - start
    time.sleep(1.0)
    calls_nachher = sum(d.get("calls", 0) for d in db.provider_stats.find({}))
    fc_nachher = (db.listings_cache.find_one({"cache_key": ident["cache_key"]},
                                             {"fetch_count": 1}) or {}).get("fetch_count")
    fehler = [e for e in ergebnisse if e.get("fehler")]
    ok = [e for e in ergebnisse if not e.get("fehler")]
    zs = sorted(e["t_gesamt"] for e in ok)
    stati = {}
    for e in ok:
        stati[e.get("check_status")] = stati.get(e.get("check_status"), 0) + 1
    print(f"\n=== Szenario {name}: {len(auswahl)} Konten laden denselben bekannten Link "
          f"({ident['source']}) gleichzeitig neu ===")
    print(f"erfolgreich: {len(ok)}  Fehler: {len(fehler)}  Gesamtdauer: {gesamt:.2f} s")
    if zs:
        print(f"  je Konto: median {statistics.median(zs):.2f}s, "
              f"p90 {zs[min(len(zs) - 1, int(len(zs) * 0.9))]:.2f}s, max {zs[-1]:.2f}s")
    print(f"  Antworten auf 'Link pruefen': {stati}; neue Anbieter-Abrufe: "
          f"{calls_nachher - calls_vorher}; fetch_count {fc_vorher} -> {fc_nachher}")
    for e in fehler[:5]:
        print(f"   FEHLER Konto {e['user']}: {e['fehler']}")


if __name__ == "__main__":
    print(f"Backend: {API}  Lauf {SUF}")
    r = requests.get(f"{API}/health", timeout=10)
    assert r.status_code == 200, r.text[:100]
    konten = konten_anlegen()
    print(f"{len(konten)} Sucher-Konten in {FIRMEN} Firmen angelegt")
    try:
        szenario("A — alle gleichzeitig", konten, 0.0)
        if not os.environ.get("LASTTEST_NUR_A"):
            szenario("B — je 0,01 s versetzt", konten, 0.01)
        # Neuladen: je Quelle ein bereits bekannter Link, 150 Konten gleichzeitig
        for quelle in ("mobile", "autoscout24", "kleinanzeigen"):
            idx = next((i for i, (_, ident) in enumerate(LINKS) if ident["source"] == quelle), None)
            if idx is not None:
                neuladen(f"C — Neuladen {quelle}", konten, idx)
        # Vergleichswert: EIN Konto allein, bekannter Link (Cache-Treffer)
        H = {"Authorization": f"Bearer {konten[0][3]}"}
        url = LINKS[0][0]
        zeiten = []
        for _ in range(5):
            t0 = time.perf_counter()
            requests.post(f"{API}/listings/check", json={"url": url}, headers=H, timeout=30)
            requests.post(f"{API}/mobile/compare", json={"url": url}, headers=H, timeout=30)
            zeiten.append(time.perf_counter() - t0)
        print("Vergleichswert allein (bekannter Link, pruefen + vergleichen): "
              f"median {statistics.median(zeiten)*1000:.0f} ms")
    finally:
        cache_zuruecksetzen()
        konten_entfernen()
        print("\nTestdaten wieder entfernt.")
