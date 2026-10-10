"""Lasttest Werkzeuge (Frage Ahmad 07.10.2026, "keiner soll 2 s warten"): N Sucher mit Windows-Programm UND
Browser-Erweiterung, verteilt auf PORTS (mehrere lokale Prozesse = wie Worker auf dem Server). NUR lokal gegen eine
Wegwerf-DB (autoschnell_wz2, launch.json "Werkzeug-Backend 8006"/"8007", Anbieter-Mock) — legt Firma + Sucher an und
raeumt am Ende auf.

Aufruf:  PORTS=8006,8007 SUCHER=40 WELLEN=0,1,2,3 python backend/scripts/lasttest_werkzeuge.py
Misst:
  (0) Einzel-Latenz /vergleich warm (20 x nacheinander)
  (1) N Programm-Vergleiche GLEICHZEITIG (nur Programm)
  (2) N Programm-Vergleiche + N Helfer-Inserate GLEICHZEITIG
  (3) 30 s Dauerlast gestaffelt
Messwerte 07.10.2026 (1 Prozess / 2 Prozesse): (1) p95 0,46 s / 0,29 s, (2) Vergleich 0,76 s / 0,39 s."""
import concurrent.futures as cf
import os
import secrets
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone

PORTS = [int(p) for p in (os.environ.get("PORTS") or "8006").split(",")]
os.environ["TEST_BASE_URL"] = f"http://127.0.0.1:{PORTS[0]}"
os.environ["DB_NAME"] = os.environ.get("DB_NAME") or "autoschnell_wz2"
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_BACKEND, "tests"))
sys.path.insert(0, _BACKEND)
import konten  # noqa: E402
import requests  # noqa: E402
import test_browser_helfer_20261004 as tb  # noqa: E402
import werkzeuge as wz  # noqa: E402

API = konten.API
N = int(os.environ.get("SUCHER", "40"))
WELLEN = set((os.environ.get("WELLEN") or "0,1,2,3").split(","))
db = konten._db()
assert db.name.startswith("autoschnell_") and db.name not in ("autoschnell", "autoschnell_prod"), db.name


def api(i):           # Sucher i haengt fest an einem Prozess (wie hinter dem Load Balancer verteilt)
    return f"http://127.0.0.1:{PORTS[i % len(PORTS)]}/api"


s = uuid.uuid4().hex[:8]
r = konten.registrieren({"email": f"last2_{s}@bhtest-mail.de", "password": "Last-" + secrets.token_hex(6) + "!",
                         "company_name": f"Lasttest2 {s}", "contact_person": "Ahmad Test"})
r.raise_for_status()
chef_token = r.json()["token"]
me = requests.get(f"{API}/auth/me", headers=konten._kopf(chef_token), timeout=30).json()["user"]
dealer_id = me["dealer_id"]
halter = db.dealers.find_one({"kunden_nr": {"$in": [10002, "10002"]}, "id": {"$ne": dealer_id}}, {"_id": 0, "id": 1})
if halter:
    db.dealers.update_one({"id": halter["id"]}, {"$set": {"kunden_nr": 9_000_000 + secrets.randbelow(900_000)}})
db.dealers.update_one({"id": dealer_id}, {"$set": {"kunden_nr": 10002}})
sucher = []
for i in range(N):
    su = konten.sucher_als_chef_anlegen(chef_token, json={"password": "Last-S-" + secrets.token_hex(6) + "!"})
    su.raise_for_status()
    sucher.append(su.json()["sucher_id"])
db.subscriptions.insert_many([{
    "id": f"last2-{uid}", "subject_user_id": uid, "dealer_id": dealer_id, "plan": "monthly", "status": "active",
    "expires_at": "2099-01-01T00:00:00+00:00", "created_at": datetime.now(timezone.utc).isoformat()} for uid in sucher])


def verbinden(uid, wid, name):
    kopf = konten._kopf(konten.token_direkt(uid))
    c = requests.post(f"{API}/werkzeuge/{wid}/code", headers=kopf, timeout=30)
    c.raise_for_status()
    v = requests.post(f"{API}/werkzeuge/{wid}/verbinden", timeout=30,
                      json={"code": c.json()["code"], "pc_name": name, "pc_kennung": f"{name}-{uid}"})
    v.raise_for_status()
    return {wz.TOKEN_KOPF: v.json()["schluessel"], "X-Werkzeug-Version": "9.9.9"}


t0 = time.perf_counter()
prog = [verbinden(uid, wz.AUTOPOINTER, "PC") for uid in sucher]
helfer = [verbinden(uid, wz.BROWSER_HELFER, "Edge") for uid in sucher]
print(f"{N} Sucher angelegt und je zweimal verbunden in {time.perf_counter() - t0:.1f} s; Prozesse: {PORTS}")

AUTOS = [("VW Golf", "VW Golf VII 1.4 TSI", 2016, 98000, 92), ("Audi A4", "Audi A4 Avant 2.0 TDI", 2018, 120000, 140),
         ("BMW 320", "BMW 320d Touring", 2017, 145000, 140), ("Opel Corsa", "Opel Corsa 1.2", 2012, 98000, 51),
         ("Skoda Octavia", "Skoda Octavia Combi 1.6 TDI", 2015, 160000, 77)]
SEITE_M = tb._seite(tb._mobile_inserat_html(tb._mobile_listing()))
PREISE = [20000 + d for d in range(-3000, 17000, 1000)]
SUCHE_M = tb._seite(tb._mobile_suche_html(PREISE))
SESS = [requests.Session() for _ in range(N)]


def vergleich(i, k):
    text, titel, jahr, km, kw = AUTOS[(i + k) % len(AUTOS)]
    f = {"marke": text.split()[0], "modell": text.split(maxsplit=1)[1], "marke_modell_text": text, "titel": titel,
         "ez_monat": 5, "ez_jahr": jahr, "kilometer": km + k * 1000, "kw": kw, "kraftstoff": "Benzin",
         "getriebe": "Schaltgetriebe", "preis": 9000 + k * 100, "quelle": "mobile.de",
         "inserat_id": str(400000000 + i * 1000 + k), "roh": True}
    a = time.perf_counter()
    r = SESS[i].post(f"{api(i)}/werkzeuge/{wz.AUTOPOINTER}/vergleich", headers=prog[i], json={"fahrzeug": f}, timeout=60)
    d = r.json() if r.status_code == 200 else {}
    return ("vergleich", r.status_code, time.perf_counter() - a, d.get("vorab", {}).get("status"))


def inserat(i, k):
    a = time.perf_counter()
    r = SESS[i].post(f"{api(i)}/werkzeuge/{wz.BROWSER_HELFER}/inserat", headers=helfer[i], timeout=60,
                     json={"url": tb.MOBILE_URL, "seite": SEITE_M})
    d = r.json() if r.status_code == 200 else {}
    dauer = time.perf_counter() - a
    if r.status_code != 200 or not d.get("vergleich_id"):
        return ("inserat", r.status_code, dauer, None)
    suche = next((l["url"] for l in d["links"] if l["portal"] == "mobile.de"), None)
    b = time.perf_counter()
    m = SESS[i].post(f"{api(i)}/werkzeuge/{wz.BROWSER_HELFER}/marktlage", headers=helfer[i], timeout=60,
                     json={"vergleich_id": d["vergleich_id"], "url": suche, "seite": SUCHE_M})
    return ("inserat+marktlage", 200 if m.status_code == 200 else m.status_code, dauer + (time.perf_counter() - b), None)


def welle(name, jobs, parallel):
    a = time.perf_counter()
    with cf.ThreadPoolExecutor(parallel) as ex:
        erg = list(ex.map(lambda j: j[0](*j[1:]), jobs))
    gesamt = time.perf_counter() - a
    for art in sorted({e[0] for e in erg}):
        z = sorted(e[2] for e in erg if e[0] == art)
        fehler = [e[1] for e in erg if e[0] == art and e[1] != 200]
        p = lambda q: z[min(len(z) - 1, int(len(z) * q))]  # noqa: E731
        ueber = sum(1 for x in z if x > 0.5)
        print(f"  {name:34} {art:18} n={len(z):4}  p50={statistics.median(z):.2f}s  p95={p(0.95):.2f}s  "
              f"max={z[-1]:.2f}s  >0,5s={ueber}  Fehler={len(fehler)} {sorted(set(fehler)) if fehler else ''}")
    print(f"  {name:34} Wand-Zeit gesamt {gesamt:.1f} s")
    return erg


try:
    if "0" in WELLEN:
        z = [vergleich(0, k)[2] for k in range(20)]
        print(f"== Einzel-Latenz /vergleich (warm, 20 x nacheinander, 1 Sucher): p50={statistics.median(z)*1000:.0f} ms "
              f"max={max(z)*1000:.0f} ms  vorab={vergleich(0, 99)[3]}")
    if "1" in WELLEN:
        print(f"== Welle 1: {N} Programm-Vergleiche GLEICHZEITIG (nur Programm)")
        welle("nur Programm, alle auf einmal", [(vergleich, i, 100) for i in range(N)], N)
    if "2" in WELLEN:
        print(f"== Welle 2: {N} Programm-Vergleiche + {N} Helfer-Inserate GLEICHZEITIG")
        welle("Programm + Helfer gleichzeitig", [(vergleich, i, 200) for i in range(N)] + [(inserat, i, 0) for i in range(N)], 2 * N)
    if "3" in WELLEN:
        print("== Welle 3: 30 s Dauerlast — jeder Sucher alle 2 s ein Auto (Programm), alle 4 s ein Inserat (Helfer)")
        jobs = []
        for k in range(15):
            jobs += [(vergleich, i, 300 + k) for i in range(N)]
            if k % 2 == 0:
                jobs += [(inserat, i, k + 1) for i in range(N)]
        welle("Dauerlast 30 s (gestaffelt)", jobs, N)
    offen = db.link_jobs.count_documents({"dealer_id": dealer_id, "status": {"$in": ["queued", "processing"]}})
    fertig = db.link_jobs.count_documents({"dealer_id": dealer_id, "status": "completed"})
    print(f"Apify-Vorab-Abrufe dieses Laufs: fertig={fertig} offen={offen} (0/0 = Helfer liest, kein Apify)")
finally:
    db.users.delete_many({"id": {"$in": sucher + [me['id']]}})
    db.dealers.delete_one({"id": dealer_id})
    for coll in ("subscriptions", "werkzeug_codes", "werkzeug_verbindungen", "werkzeug_vergleiche",
                 "werkzeug_inserate", "vehicles", "vehicle_comparisons", "link_jobs"):
        db[coll].delete_many({"dealer_id": dealer_id})
    db.subscriptions.delete_many({"id": {"$regex": "^last2-"}})
    if halter:
        db.dealers.update_one({"id": halter["id"]}, {"$set": {"kunden_nr": 10002}})
    print("aufgeraeumt")
