# -*- coding: utf-8 -*-
"""API-Tests fuer die Linkpruefung als Hintergrundjob (Priorität 2).

Belegt:
- unbekannter Link -> Job-ID sofort, Status queued/processing -> completed
- IDEMPOTENZ: viele parallele Checks desselben Links -> EIN Job, EIN
  externer Abruf (fetch_count == 1)
- verschiedene Links blockieren sich nicht (alle Jobs fertig im Zeitbudget)
- danach liefert /mobile/compare sofort aus dem Cache

Braucht ein laufendes Backend MIT MOCK_PROVIDER_FETCH=true (wie in CI) —
ohne Mock ueberspringen sich die Tests mit Begruendung, statt echte
Anbieter-Abrufe auszuloesen.
"""
import concurrent.futures
import os
import sys
import time
import uuid
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUFFIX = uuid.uuid4().hex[:8]
PW = "JobTest123!"

# Eigener Nummernkreis je Testlauf, damit parallele/alte Laeufe nicht stoeren.
BASE_ID = 9800000000 + (int(SUFFIX, 16) % 90000000)


def _link(n: int) -> str:
    return f"https://www.kleinanzeigen.de/s-anzeige/jobtest/{BASE_ID + n}-216-1"


def _mongo():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


@pytest.fixture(scope="module")
def chef():
    from datetime import datetime, timedelta, timezone
    mail = f"jobtest_{SUFFIX}@e2etest-mail.de"
    r = requests.post(f"{API}/auth/register", json={
        "email": mail, "password": PW, "company_name": "Jobtest GmbH",
        "contact_person": "J T", "phone": "0511 2"}, timeout=30)
    assert r.status_code == 200, f"Registrierung: {r.status_code} {r.text[:200]}"
    tok = r.json()["token"]
    me = requests.get(f"{API}/auth/me",
                      headers={"Authorization": f"Bearer {tok}"},
                      timeout=30).json()["user"]
    dbx = _mongo()
    dbx.subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": me["dealer_id"],
        "subject_user_id": me["id"], "plan": "monthly", "status": "active",
        "expires_at": (datetime.now(timezone.utc)
                       + timedelta(days=1)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()})

    h = {"Authorization": f"Bearer {tok}"}
    # Mock-Pruefung: ohne Mock KEINE echten Abrufe ausloesen -> skip.
    r = requests.post(f"{API}/mobile/compare", json={"url": _link(0)},
                      headers=h, timeout=90)
    if r.status_code != 200 or not (r.json().get("vehicle") or {}).get("_mock"):
        _cleanup(me)
        pytest.skip("Backend laeuft nicht im Mock-Modus "
                    "(MOCK_PROVIDER_FETCH=true) — Job-Tests wuerden echte "
                    "Anbieter-Abrufe ausloesen.")
    yield {"token": tok, "user": me, "h": h}
    _cleanup(me)


def _cleanup(me):
    dbx = _mongo()
    dbx.subscriptions.delete_many({"subject_user_id": me["id"]})
    dbx.vehicle_comparisons.delete_many({"dealer_id": me["dealer_id"]})
    dbx.vehicles.delete_many({"dealer_id": me["dealer_id"]})
    dbx.dealers.delete_many({"id": me["dealer_id"]})
    dbx.users.delete_many({"id": me["id"]})
    dbx.listings_cache.delete_many({"item_id": {"$regex": f"^{BASE_ID // 100}"}})
    dbx.link_jobs.delete_many({"item_id": {"$regex": f"^{BASE_ID // 100}"}})


def _wait_for_job(h, job_id, budget_s=60):
    ende = time.monotonic() + budget_s
    while time.monotonic() < ende:
        r = requests.get(f"{API}/listings/check/{job_id}", headers=h,
                         timeout=30)
        if r.status_code == 404:
            return "completed"       # schon weggeraeumt -> Ergebnis im Cache
        body = r.json()
        if body["status"] in ("completed", "failed"):
            return body["status"], body.get("error")
        time.sleep(1)
    return "timeout", None


def test_unbekannter_link_wird_zum_job_und_fertig(chef):
    h = chef["h"]
    r = requests.post(f"{API}/listings/check", json={"url": _link(1)},
                      headers=h, timeout=30)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body["status"] in ("queued", "processing"), body
    assert body.get("job_id"), "keine Job-ID"

    status = _wait_for_job(h, body["job_id"])
    assert status[0] == "completed", f"Job endete als {status}"

    # Danach: compare liefert SOFORT aus dem Cache (cached == True).
    r = requests.post(f"{API}/mobile/compare", json={"url": _link(1)},
                      headers=h, timeout=30)
    assert r.status_code == 200 and r.json().get("cached") is True

    # Zweiter Check desselben Links: sofort completed, kein neuer Job.
    r = requests.post(f"{API}/listings/check", json={"url": _link(1)},
                      headers=h, timeout=30)
    assert r.json()["status"] == "completed"


def test_paralleler_selber_link_ein_job_ein_abruf(chef):
    """15 gleichzeitige Checks desselben NEUEN Links: alle bekommen
    dieselbe Job-ID (Idempotenz), und extern wird genau EINMAL geholt."""
    h = chef["h"]
    url = _link(2)

    def check():
        return requests.post(f"{API}/listings/check", json={"url": url},
                             headers=h, timeout=30).json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
        antworten = list(ex.map(lambda _: check(), range(15)))

    job_ids = {a.get("job_id") for a in antworten if a.get("job_id")}
    fertige = [a for a in antworten if a["status"] == "completed"]
    assert len(job_ids) <= 1, f"Mehrere Jobs fuer denselben Link: {job_ids}"
    assert job_ids or fertige, f"Weder Job noch Ergebnis: {antworten[:3]}"

    if job_ids:
        status = _wait_for_job(h, job_ids.pop())
        assert status[0] == "completed", f"Job endete als {status}"

    doc = _mongo().listings_cache.find_one({"item_id": str(BASE_ID + 2)})
    assert doc is not None, "Inserat nicht im Cache"
    assert doc.get("fetch_count", 0) == 1, (
        f"Inserat wurde {doc.get('fetch_count')}x extern geholt (Soll: 1)")


def test_verschiedene_links_blockieren_sich_nicht(chef):
    """8 verschiedene neue Links gleichzeitig: alle Jobs werden im
    Zeitbudget fertig, jeder Link genau 1x extern geholt."""
    h = chef["h"]
    urls = [_link(10 + i) for i in range(8)]
    start = time.monotonic()
    jobs = []
    for u in urls:
        body = requests.post(f"{API}/listings/check", json={"url": u},
                             headers=h, timeout=30).json()
        if body["status"] != "completed":
            jobs.append(body["job_id"])
    for jid in jobs:
        status = _wait_for_job(h, jid, budget_s=90)
        assert status[0] == "completed", f"Job {jid} endete als {status}"
    dauer = time.monotonic() - start
    assert dauer < 90, f"8 Links brauchten {dauer:.0f}s"

    dbx = _mongo()
    for i in range(8):
        doc = dbx.listings_cache.find_one({"item_id": str(BASE_ID + 10 + i)})
        assert doc and doc.get("fetch_count", 0) == 1, (
            f"Link {i}: fetch_count={doc and doc.get('fetch_count')}")
