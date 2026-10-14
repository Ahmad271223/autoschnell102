# -*- coding: utf-8 -*-
"""Lasttest "30 Sucher, je 30 neue mobile.de-Links" (Frage Ahmad 01.10.2026).

EINE Firma mit 30 Suchern, 900 verschiedene neue mobile.de-Links.
  Szenario 1 (Alltag): jeder Sucher fuegt seine 30 Links NACHEINANDER ein — den naechsten,
      sobald der vorige Vergleich steht; alle 30 Sucher starten gleichzeitig.
      Die erste Welle (30 Links auf einmal) wird getrennt ausgewiesen.
  Szenario 2 (Sturm): jeder Sucher wirft alle 30 Links AUF EINMAL ein (900 gleichzeitig) —
      zeigt die Warteschlangen-Grenzen je Konto (LINK_JOB_MAX_OFFEN_JE_KONTO) und je Firma
      (LINK_JOB_MAX_OFFEN_JE_FIRMA): abgelehnte Links werden wie in der Oberflaeche
      nach kurzer Pause erneut versucht.
Aufruf: python scripts/lasttest_30x30.py
Umgebung wie lasttest_links_gleichzeitig.py (LASTTEST_API, MONGO_URL, DB_NAME), dazu
LASTTEST_SUCHER (30), LASTTEST_LINKS_JE_SUCHER (30), LASTTEST_SZENARIO=1|2|beide,
LASTTEST_FRIST_S (Wartezeit je Link, 300). Am Backend: MOCK_PROVIDER_FETCH=true und
MOCK_PROVIDER_DELAY_MS_MOBILE=8000 (Apify-Dauer nachgestellt); Slots wie in Produktion
(MAX_CONCURRENT_MOBILE, APIFY_MAX_PARALLEL) und LINK_JOB_CONCURRENCY=256 (ein Prozess
bildet so den Verbund aus 2 x 4 Workern ab). Legt Wegwerf-Konten an und entfernt sie wieder."""
import os
import random
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

S = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, S)
os.environ.setdefault("LASTTEST_API", "http://127.0.0.1:8002/api")
# lasttest_links_gleichzeitig liest beim Import eine Linkliste (lasttest_links.txt, nicht im Repo):
# ohne die Datei eine leere unterschieben — die eigenen Links baut dieses Skript selbst.
if len(sys.argv) < 2 and not os.path.exists(os.path.join(S, "lasttest_links.txt")):
    import tempfile
    _leer = os.path.join(tempfile.gettempdir(), "lasttest_links_leer.txt")
    open(_leer, "w").close()
    sys.argv.append(_leer)
import contextlib
import io
with contextlib.redirect_stdout(io.StringIO()):
    import lasttest_links_gleichzeitig as LT  # noqa: E402
from listing_identity import get_listing_identity  # noqa: E402

SUCHER = int(os.environ.get("LASTTEST_SUCHER", "30"))
JE_SUCHER = int(os.environ.get("LASTTEST_LINKS_JE_SUCHER", "30"))
FRIST_S = float(os.environ.get("LASTTEST_FRIST_S", "300"))
SZENARIO = os.environ.get("LASTTEST_SZENARIO", "beide").strip().lower()
API, db = LT.API, LT.db


def links_bauen():
    out = []
    for s in range(SUCHER):
        for j in range(JE_SUCHER):
            url = f"https://suchen.mobile.de/fahrzeuge/details.html?id={9_300_000_000 + s * 1000 + j}&vc=Car"
            out.append((url, get_listing_identity(url)))
    return out


LINKS = links_bauen()


def aufraeumen():
    keys = [i["cache_key"] for _, i in LINKS]
    db.listings_cache.delete_many({"cache_key": {"$in": keys}})
    db.listings_cache_client.delete_many({"cache_key": {"$in": keys}})
    db.link_jobs.delete_many({"cache_key": {"$in": keys}})
    db.inserat_beweise.delete_many({"cache_key": {"$in": keys}})
    db.vehicles.delete_many({"dealer_id": {"$regex": f"^d_lt{LT.SUF}_"}})
    db.vehicle_comparisons.delete_many({"dealer_id": {"$regex": f"^d_lt{LT.SUF}_"}})


def _retry_after(r, standard):
    try:
        return max(0.2, float(r.headers.get("Retry-After") or standard))
    except ValueError:
        return standard


def ablauf(token, url, erg):
    """Wie die Oberflaeche: Link pruefen (503/429 -> warten, nochmal) -> Job pollen -> Vergleich."""
    H = {"Authorization": f"Bearer {token}"}
    t0 = time.perf_counter()
    frist = t0 + FRIST_S
    erg.update(abgelehnt=0, polls=0)
    try:
        while True:
            r = requests.post(f"{API}/listings/check", json={"url": url}, headers=H, timeout=60)
            if r.status_code == 200:
                break
            if r.status_code in (429, 503) and time.perf_counter() < frist:
                erg["abgelehnt"] += 1
                erg.setdefault("ablehnung", r.text[:90])
                time.sleep(_retry_after(r, 2.0) + random.random())
                continue
            erg["fehler"] = f"check {r.status_code}: {r.text[:120]}"
            return
        st = r.json()
        erg["check_status"] = st.get("status")
        t_check = time.perf_counter()
        if st.get("status") in ("queued", "processing"):
            job_id = st["job_id"]
            while time.perf_counter() < frist:
                erg["polls"] += 1
                r = requests.get(f"{API}/listings/check/{job_id}", headers=H, timeout=60)
                if r.status_code == 429:
                    time.sleep(0.5)
                    continue
                if r.status_code != 200:
                    erg["fehler"] = f"status {r.status_code}: {r.text[:120]}"
                    return
                d = r.json()
                if d["status"] == "completed":
                    break
                if d["status"] == "failed":
                    erg["fehler"] = f"job failed: {d.get('error')}"
                    return
                time.sleep(0.25)
            else:
                erg["fehler"] = f"Frist {FRIST_S:.0f} s abgelaufen (Job nicht fertig)"
                return
        t_job = time.perf_counter()
        for _ in range(8):
            r = requests.post(f"{API}/mobile/compare", json={"url": url}, headers=H, timeout=120)
            if r.status_code in (503, 429):
                time.sleep(_retry_after(r, 1.0))
                continue
            break
        if r.status_code != 200:
            erg["fehler"] = f"compare {r.status_code}: {r.text[:120]}"
            return
        erg.update(t_check=t_check - t0, t_job=t_job - t0, t_gesamt=time.perf_counter() - t0)
    except Exception as exc:  # noqa: BLE001
        erg["fehler"] = f"exception: {exc}"[:160]


class Beobachter:
    """Alle 0,5 s: offene Jobs und belegte Anbieter-Slots — zeigt, ob die Slots ausgelastet waren."""

    def __init__(self, keys):
        self.keys, self.stop, self.max_slots, self.max_offen = keys, False, 0, 0
        self.t = threading.Thread(target=self._lauf, daemon=True)

    def _lauf(self):
        while not self.stop:
            try:
                st = {r["_id"]: r["n"] for r in db.link_jobs.aggregate(
                    [{"$match": {"cache_key": {"$in": self.keys}}}, {"$group": {"_id": "$status", "n": {"$sum": 1}}}])}
                self.max_offen = max(self.max_offen, st.get("queued", 0) + st.get("processing", 0))
                d = db.provider_limits.find_one({"provider": "mobile"}, {"active": 1})
                self.max_slots = max(self.max_slots, int((d or {}).get("active") or 0))
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):
        self.stop = True
        self.t.join(timeout=2)


def _p(a, q):
    return a[min(len(a) - 1, int(len(a) * q))]


def _zeiten(name, erg):
    zs = sorted(e["t_gesamt"] for e in erg if e.get("t_gesamt") is not None)
    if not zs:
        print(f"  {name}: keine erfolgreichen")
        return
    print(f"  {name} ({len(zs)}): median {statistics.median(zs):.1f} s · p90 {_p(zs, 0.9):.1f} s · "
          f"max {zs[-1]:.1f} s")


def _bilanz(titel, erg, dauer, calls_vorher):
    time.sleep(1.5)
    calls_nachher = sum(d.get("calls", 0) for d in db.provider_stats.find({}))
    fehler = [e for e in erg if e.get("fehler")]
    ok = [e for e in erg if not e.get("fehler")]
    print(f"\n=== {titel} ===")
    print(f"Links gesamt {len(erg)}, erfolgreich {len(ok)}, Fehler {len(fehler)}, "
          f"Gesamtdauer bis zum letzten Vergleich {dauer:.0f} s")
    for e in fehler[:8]:
        print(f"   FEHLER Sucher {e['sucher']} Link {e['nr']}: {e['fehler']}")
    abgelehnt = [e for e in erg if e.get("abgelehnt")]
    if abgelehnt:
        print(f"  Links, die erst abgewiesen wurden (Warteschlange voll) und spaeter durchkamen: "
              f"{len(abgelehnt)}; Ablehnungen gesamt {sum(e['abgelehnt'] for e in abgelehnt)}; "
              f"Beispiel: {abgelehnt[0].get('ablehnung')}")
    mehrfach = 0
    keys = [i["cache_key"] for _, i in LINKS]
    for c in db.listings_cache.find({"cache_key": {"$in": keys}}, {"fetch_count": 1}):
        if c.get("fetch_count", 0) != 1:
            mehrfach += 1
    print(f"  Anbieter-Abrufe laut Statistik: {calls_nachher - calls_vorher} fuer {len(erg)} Links; "
          f"Links mit mehr als einem Abruf: {mehrfach}")
    return ok


def szenario_1(konten):
    aufraeumen()
    keys = [i["cache_key"] for _, i in LINKS]
    calls_vorher = sum(d.get("calls", 0) for d in db.provider_stats.find({}))
    erg = [dict(sucher=s, nr=j) for s in range(SUCHER) for j in range(JE_SUCHER)]
    start = time.perf_counter() + 2.0

    def sucher(s):
        token = konten[s][3]
        while time.perf_counter() < start:
            time.sleep(0.001)
        for j in range(JE_SUCHER):
            e = erg[s * JE_SUCHER + j]
            e["start"] = time.perf_counter() - start
            ablauf(token, LINKS[s * JE_SUCHER + j][0], e)
            if e.get("fehler"):
                time.sleep(1.0)

    with Beobachter(keys) as b, ThreadPoolExecutor(max_workers=SUCHER) as pool:
        for f in [pool.submit(sucher, s) for s in range(SUCHER)]:
            f.result()
        dauer = time.perf_counter() - start
    ok = _bilanz(f"Szenario 1 — {SUCHER} Sucher, je {JE_SUCHER} Links nacheinander", erg, dauer, calls_vorher)
    _zeiten("Erste Welle (30 Links gleichzeitig), Wartezeit je Link", [e for e in ok if e["nr"] == 0])
    _zeiten("Alle 900 Links, Wartezeit je Link", ok)
    je_sucher = sorted(max(e["start"] + e["t_gesamt"] for e in ok if e["sucher"] == s)
                       for s in range(SUCHER) if any(e["sucher"] == s for e in ok))
    if je_sucher:
        print(f"  Zeit, bis ein Sucher mit allen {JE_SUCHER} Links durch ist: schnellster {je_sucher[0]:.0f} s · "
              f"median {statistics.median(je_sucher):.0f} s · langsamster {je_sucher[-1]:.0f} s")
    print(f"  Hoechste gleichzeitig belegte mobile.de-Slots: {b.max_slots}; hoechste offene Jobs: {b.max_offen}")
    print(f"  Statusabfragen je Link: median {statistics.median([e['polls'] for e in ok]) if ok else 0:.0f}")


def szenario_2(konten):
    aufraeumen()
    keys = [i["cache_key"] for _, i in LINKS]
    calls_vorher = sum(d.get("calls", 0) for d in db.provider_stats.find({}))
    erg = [dict(sucher=s, nr=j) for s in range(SUCHER) for j in range(JE_SUCHER)]
    start = time.perf_counter() + 3.0

    def einer(n):
        while time.perf_counter() < start:
            time.sleep(0.001)
        ablauf(konten[erg[n]["sucher"]][3], LINKS[n][0], erg[n])

    with Beobachter(keys) as b, ThreadPoolExecutor(max_workers=len(erg)) as pool:
        for f in [pool.submit(einer, n) for n in range(len(erg))]:
            f.result()
        dauer = time.perf_counter() - start
    ok = _bilanz(f"Szenario 2 — {SUCHER} Sucher werfen je {JE_SUCHER} Links AUF EINMAL ein", erg, dauer, calls_vorher)
    sofort = [e for e in ok if not e.get("abgelehnt")]
    print(f"  Sofort angenommen: {len(sofort)} von {len(erg)} (Grenzen je Konto/Firma)")
    _zeiten("Sofort angenommene, Wartezeit je Link", sofort)
    _zeiten("Alle durchgekommenen, Wartezeit je Link (inkl. Warten auf einen Platz)", ok)
    print(f"  Hoechste gleichzeitig belegte mobile.de-Slots: {b.max_slots}; hoechste offene Jobs: {b.max_offen}")


if __name__ == "__main__":
    print(f"Backend: {API}  Lauf {LT.SUF}  {SUCHER} Sucher x {JE_SUCHER} Links = {len(LINKS)} neue mobile.de-Links")
    r = requests.get(f"{API}/health", timeout=10)
    assert r.status_code == 200, r.text[:100]
    LT.FIRMEN, LT.JE_FIRMA = 1, SUCHER
    konten = LT.konten_anlegen()
    print(f"{len(konten)} Sucher-Konten in einer Firma angelegt")
    try:
        if SZENARIO in ("1", "beide"):
            szenario_1(konten)
        if SZENARIO in ("2", "beide"):
            szenario_2(konten)
    finally:
        aufraeumen()
        LT.konten_entfernen()
        print("\naufgeraeumt")
