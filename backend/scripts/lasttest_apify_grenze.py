# -*- coding: utf-8 -*-
"""Lasttest: mobile.de und AutoScout24 zusammen gegen die Apify-Grenze.

Frage Ahmads vom 20.09.2026: Kleinanzeigen laeuft ueber einen eigenen
bezahlten Dienst (kleinanzeigen-agent.de), mobile.de und AutoScout24 aber
ueber Apify. Beide duerfen je 20 gleichzeitig — zusammen also 40, waehrend
der Starter-Plan nur 32 gleichzeitige Laeufe erlaubt. Eine gemeinsame
Obergrenze ueber beide Quellen gibt es nicht.

Gemessen wird, was beim SUCHER ankommt, nicht was im Log steht:

  * echter Weg: link_jobs -> listing_identity -> provider_limiter (Zaehler
    in der echten Datenbank, gilt ueber alle Prozesse) -> provider_fetch ->
    mobile_service/autoscout_service
  * nur der letzte Schritt ist nachgestellt: ein Apify, das ueber
    --apify-grenze gleichzeitigen Laeufen mit 429 antwortet
  * die Wiederholung (LINK_JOB_MAX_ATTEMPTS) laeuft unveraendert

Es geht KEINE echte Anfrage nach draussen und es entstehen keine Kosten.

Aufruf (Datenbank erreichbar, Backend muss NICHT laufen):
    python -X utf8 scripts/lasttest_apify_grenze.py
    python -X utf8 scripts/lasttest_apify_grenze.py --je-quelle 20 --apify-grenze 32

Exit 0 = kein Sucher sah einen Fehler, 1 = mindestens einer.
"""
import argparse
import asyncio
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Vor dem Import setzen — die Grenzen werden beim Laden der Module gelesen.
os.environ.setdefault("MOCK_PROVIDER_FETCH", "false")
os.environ.setdefault("APIFY_TOKEN", "apify_lasttest_kein_echter_token")

SUF = uuid.uuid4().hex[:8]


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApifyNachgestellt:
    """Apify mit hartem Deckel fuer gleichzeitige Laeufe.

    Der Starter-Plan erlaubt 32 gleichzeitige Actor-Laeufe. Was darueber
    hinaus startet, wird abgewiesen — hier mit 429, weil das der Fall ist,
    den der Code als Tempolimit erkennt (anbieter_fehler.ART_LIMIT).
    """

    def __init__(self, grenze: int, dauer: float):
        self.grenze = grenze
        self.dauer = dauer
        self.laufend = 0
        self.hoechststand = 0
        self.erlaubt = 0
        self.abgewiesen = 0
        self.sperre = asyncio.Lock()

    async def lauf(self, welche: str):
        async with self.sperre:
            if self.laufend >= self.grenze:
                self.abgewiesen += 1
                return 429, '{"error":{"type":"rate-limit-exceeded"}}'
            self.laufend += 1
            self.erlaubt += 1
            self.hoechststand = max(self.hoechststand, self.laufend)
        try:
            await asyncio.sleep(self.dauer)
            return 200, welche
        finally:
            async with self.sperre:
                self.laufend -= 1


def _antwort_klasse():
    class _Antwort:
        def __init__(self, status, text, daten):
            self.status_code = status
            self.text = text
            self._daten = daten

        def json(self):
            return self._daten
    return _Antwort


def _client_klasse(apify: "ApifyNachgestellt"):
    Antwort = _antwort_klasse()

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, endpoint, **kw):
            welche = "mobile" if "mobile" in str(endpoint).lower() else "autoscout"
            # Der Actor-Name entscheidet; im Zweifel ueber die Nutzlast.
            koerper = str(kw.get("json") or "")
            if "autoscout" in koerper:
                welche = "autoscout"
            elif "mobile.de" in koerper:
                welche = "mobile"
            status, _ = await apify.lauf(welche)
            if status != 200:
                return Antwort(429, '{"error":{"type":"rate-limit-exceeded"}}', {})
            if welche == "mobile":
                daten = [{"id": "412345678", "make": "BMW", "model": "320d",
                          "price": 15900, "mileage": 90000,
                          "firstRegistration": "01/2020", "power": 140,
                          "fuel": "Diesel", "gearbox": "Automatik"}]
            else:
                daten = [{"make": "Audi", "model": "A4", "price": 17900,
                          "mileage": 80000, "firstRegistration": "03/2020",
                          "powerKw": 110, "fuel": "Diesel"}]
            return Antwort(200, "ok", daten)

        async def get(self, *a, **kw):
            return Antwort(200, "ok", [])
    return _Client


async def aufbau(db, n: int) -> dict:
    """Eine Firma mit n Suchern — Job-Wege pruefen Konto, Abo und Firma."""
    import bcrypt
    firma = f"apify-{SUF}"
    chef = f"chef-{SUF}"
    hash_ = bcrypt.hashpw(b"Rt7Kq2Mx9-Sicher!42", bcrypt.gensalt()).decode()
    await db.dealers.insert_one(
        {"id": firma, "company_name": f"Apify-Lasttest {SUF}",
         "kunden_nr": 96000 + (int(SUF[:4], 16) % 900), "user_id": chef,
         "created_at": _jetzt()})
    nutzer, abos = [], []
    sucher = []
    for i in range(n):
        uid = f"such-{SUF}-{i}"
        sucher.append(uid)
        nutzer.append({"id": uid, "username": uid, "role": "sucher",
                       "dealer_id": firma, "active": True,
                       "password_hash": hash_, "current_session_id": None,
                       "created_at": _jetzt()})
        abos.append({"id": f"abo-{SUF}-{i}", "dealer_id": firma,
                     "subject_user_id": uid, "plan": "monthly",
                     "status": "active",
                     "expires_at": (datetime.now(timezone.utc)
                                    + timedelta(days=20)).isoformat()})
    nutzer.append({"id": chef, "username": chef, "role": "dealer",
                   "dealer_id": firma, "active": True, "password_hash": hash_,
                   "current_session_id": None, "created_at": _jetzt()})
    abos.append({"id": f"abo-{SUF}-c", "dealer_id": firma,
                 "subject_user_id": chef, "plan": "yearly", "status": "active",
                 "expires_at": (datetime.now(timezone.utc)
                                + timedelta(days=300)).isoformat()})
    await db.users.insert_many(nutzer)
    await db.subscriptions.insert_many(abos)
    return {"firma": firma, "chef": chef, "sucher": sucher}


async def abbau(db, w: dict) -> None:
    await db.users.delete_many({"dealer_id": w["firma"]})
    await db.dealers.delete_many({"id": w["firma"]})
    for coll in ("subscriptions", "link_jobs", "listings_cache",
                 "provider_slots", "anbieter_abrufe", "activity_logs"):
        try:
            await db[coll].delete_many({"$or": [{"dealer_id": w["firma"]},
                                                {"id": {"$regex": SUF}},
                                                {"cache_key": {"$regex": SUF}}]})
        except Exception:  # noqa: BLE001
            pass
    # Die Testlinks tragen keine Kennung im Schluessel — gezielt aufraeumen.
    await db.link_jobs.delete_many({"requested_by_dealer": w["firma"]})


def _links(n: int) -> list:
    """n mobile.de- und n AutoScout-Links, alle verschieden."""
    basis = int(SUF[:6], 16) % 400000000 + 100000000
    mob = [f"https://suchen.mobile.de/fahrzeuge/details.html?id={basis + i}"
           for i in range(n)]
    aut = [f"https://www.autoscout24.de/angebote/{SUF}{i:04d}-1111-2222-3333-"
           f"{i:012d}" for i in range(n)]
    return mob + aut


async def lauf(je_quelle: int, apify_grenze: int, dauer: float) -> int:
    from deps import db
    import httpx
    import link_jobs as LJ
    import mobile_service as MS
    import autoscout_service as AS
    import provider_limiter as PL

    apify = ApifyNachgestellt(apify_grenze, dauer)
    Client = _client_klasse(apify)
    # mobile_service nutzt `httpx`, autoscout_service `_httpx` — beide Namen
    # zeigen auf dasselbe Modul, aber jedes Modul hat seinen eigenen Verweis.
    httpx.AsyncClient = Client
    MS.httpx.AsyncClient = Client
    AS._httpx.AsyncClient = Client

    print(f"  je Quelle ................. {je_quelle} Links "
          f"(= {je_quelle * 2} gleichzeitig)")
    print(f"  eigene Grenzen ............ mobile {PL.PROVIDER_MAX_CONCURRENT['mobile']}, "
          f"autoscout24 {PL.PROVIDER_MAX_CONCURRENT['autoscout24']}  "
          f"(Summe {PL.PROVIDER_MAX_CONCURRENT['mobile'] + PL.PROVIDER_MAX_CONCURRENT['autoscout24']})")
    print(f"  Apify erlaubt ............. {apify_grenze} gleichzeitig, "
          f"je Lauf {dauer:.1f}s")
    print(f"  Wiederholungen ............ {LJ.MAX_ATTEMPTS} Versuche")

    w = await aufbau(db, je_quelle)
    try:
        # Alle Links gleichzeitig einreihen — je ein Sucher je Link.
        alle = _links(je_quelle)
        t0 = time.monotonic()
        await asyncio.gather(*[
            LJ.enqueue_job(db, url, w["firma"], w["sucher"][i % je_quelle])
            for i, url in enumerate(alle)])
        eingereiht = await db.link_jobs.count_documents(
            {"requested_by_dealer": w["firma"]})
        print(f"\n  eingereiht ................ {eingereiht} Auftraege")

        # Die Worker-Schleife nachspielen (wie run_job_worker_forever).
        laufend: set = set()
        while True:
            laufend = {t for t in laufend if not t.done()}
            frei = LJ.JOB_CONCURRENCY - len(laufend)
            if frei > 0:
                for job in await LJ._claim_many(db, frei):
                    laufend.add(asyncio.create_task(LJ._process(db, job)))
            offen = await db.link_jobs.count_documents(
                {"requested_by_dealer": w["firma"], "active": True})
            if not offen and not laufend:
                break
            if time.monotonic() - t0 > 300:
                print("  ABBRUCH: laeuft laenger als 5 Minuten")
                break
            await asyncio.sleep(0.3)
        gesamt = time.monotonic() - t0

        fertig = await db.link_jobs.count_documents(
            {"requested_by_dealer": w["firma"], "status": "completed"})
        kaputt = await db.link_jobs.count_documents(
            {"requested_by_dealer": w["firma"], "status": "failed"})
        beispiele = await db.link_jobs.find(
            {"requested_by_dealer": w["firma"], "status": "failed"},
            {"_id": 0, "source": 1, "error": 1, "attempts": 1}).to_list(5)

        print(f"\n  Ergebnis bekommen ......... {fertig} von {len(alle)}")
        print(f"  Fehler fuer den Sucher .... {kaputt}")
        print(f"  Apify: durchgelassen ...... {apify.erlaubt}")
        print(f"  Apify: mit 429 abgewiesen . {apify.abgewiesen}")
        print(f"  Apify: Hoechststand ....... {apify.hoechststand} gleichzeitig "
              f"(erlaubt {apify_grenze})")
        print(f"  Dauer ..................... {gesamt:.1f}s")
        for b in beispiele:
            print(f"     FEHLER {b.get('source')} nach {b.get('attempts')} "
                  f"Versuchen: {str(b.get('error'))[:90]}")
        return kaputt
    finally:
        await abbau(db, w)
        print("\nTestdaten entfernt.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Lasttest Apify-Grenze")
    ap.add_argument("--je-quelle", type=int, default=20,
                    help="Links je Quelle (mobile.de UND AutoScout24)")
    ap.add_argument("--apify-grenze", type=int, default=32,
                    help="gleichzeitige Actor-Laeufe, die Apify zulaesst")
    ap.add_argument("--dauer", type=float, default=1.5,
                    help="Sekunden je Apify-Lauf")
    a = ap.parse_args(argv)

    print("Lasttest Apify-Grenze — Apify wird nachgestellt, es geht KEINE "
          "echte Anfrage raus.\n")
    kaputt = asyncio.run(lauf(a.je_quelle, a.apify_grenze, a.dauer))
    print("\nERGEBNIS")
    if kaputt:
        print(f"  {kaputt} Sucher haetten einen Fehler gesehen — die eigenen "
              f"Grenzen lassen mehr durch, als Apify annimmt.")
    else:
        print("  Kein Sucher sah einen Fehler.")
    return 1 if kaputt else 0


if __name__ == "__main__":
    raise SystemExit(main())
