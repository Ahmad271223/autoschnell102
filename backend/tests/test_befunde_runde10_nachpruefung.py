# -*- coding: utf-8 -*-
"""Nachpruefung Runde 10 (09/2026): Befunde aus dem Gegen-Review der
Runde-10-Aenderungen, die einer Nachstellung standhielten.

HTTP-Teile brauchen das Backend auf TEST_BASE_URL (Mock-Anbieter wie in CI).
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND / ".env")
except Exception:                                   # noqa: BLE001
    pass

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "NachTest123!"
JETZT = datetime.now(timezone.utc)


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _mit_db(fn):
    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        try:
            return await fn(cl[DB_NAME])
        finally:
            cl.close()
    return asyncio.run(_run())


# =============================================================== Protokoll haengt
@pytest.fixture(scope="module")
def fahrer():
    """Fahrerkonto + Termin direkt in der Datenbank (wie die Fahrer-Tests)."""
    import jwt
    dbx = _db()
    dealer_id = f"nach_dealer_{SUF}"
    drv_id = f"nach_drv_{SUF}"
    appt_id = f"nach_appt_{SUF}"
    sid = f"sid_{SUF}"
    dbx.driver_accounts.insert_one({
        "id": drv_id, "email": f"nachfahrer_{SUF}@e2etest-mail.de", "display_name": "Nach Fahrer",
        "active": True, "current_session_id": sid, "driver_code": f"N{SUF}",
        "password_hash": "x", "created_at": JETZT.isoformat()})
    dbx.dealer_drivers.insert_one({"id": str(uuid.uuid4()), "dealer_id": dealer_id,
                                   "driver_account_id": drv_id, "created_at": JETZT.isoformat()})
    dbx.appointments.insert_one({
        "id": appt_id, "dealer_id": dealer_id, "driver_id": drv_id, "title": "Nach",
        "status": "offen", "pickup_date": "2099-01-01", "pickup_time": "09:00",
        "seller_name": "V", "created_at": JETZT.isoformat()})
    tok = jwt.encode({"sub": drv_id, "sid": sid, "role": "driver_account",
                      "exp": JETZT + timedelta(hours=1)},
                     os.environ["JWT_SECRET"], algorithm=os.environ.get("JWT_ALG", "HS256"))
    yield {"h": {"Authorization": f"Bearer {tok}"}, "appt": appt_id}
    dbx.pickup_protocols.delete_many({"appointment_id": appt_id})
    dbx.appointments.delete_one({"id": appt_id})
    dbx.dealer_drivers.delete_many({"driver_account_id": drv_id})
    dbx.driver_accounts.delete_one({"id": drv_id})


def test_protokoll_nach_prozessabbruch_wieder_speicherbar(fahrer):
    """Stirbt der Prozess mitten im Abschluss, bleibt 'wird_abgeschlossen'
    mit abgelaufenem Claim stehen. Die Fahrer-App speichert vor JEDEM
    Abschluss — das darf nicht ewig 409 liefern."""
    url = f"{API}/driver/appointments/{fahrer['appt']}/protocol"
    r = requests.put(url, headers=fahrer["h"], json={"notes": "a"}, timeout=30)
    if r.status_code == 404:
        pytest.skip("Fahrer-Route nicht erreichbar (Backend-Stand?)")
    assert r.status_code == 200 and r.json()["status"] == "entwurf", r.text[:200]
    pid = r.json()["id"]
    dbx = _db()
    # abgelaufener Claim -> Speichern geht durch und setzt Entwurf zurueck
    dbx.pickup_protocols.update_one({"id": pid}, {"$set": {
        "status": "wird_abgeschlossen",
        "claim_bis": (JETZT - timedelta(minutes=10)).isoformat()}})
    r = requests.put(url, headers=fahrer["h"], json={"notes": "b"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    doc = dbx.pickup_protocols.find_one({"id": pid}, {"_id": 0})
    assert doc["status"] == "entwurf" and doc["notes"] == "b" and "claim_bis" not in doc
    # laufender Claim -> 409 "gerade abgeschlossen", Dokument unveraendert
    # Zeit JETZT bestimmen, nicht beim Import — in einem langen Gesamtlauf
    # waere ein Claim "in 3 Minuten" sonst laengst abgelaufen.
    dbx.pickup_protocols.update_one({"id": pid}, {"$set": {
        "status": "wird_abgeschlossen",
        "claim_bis": (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()}})
    r = requests.put(url, headers=fahrer["h"], json={"notes": "c"}, timeout=30)
    assert r.status_code == 409 and "gerade abgeschlossen" in r.text, r.text[:200]
    doc = dbx.pickup_protocols.find_one({"id": pid}, {"_id": 0})
    assert doc["status"] == "wird_abgeschlossen" and doc["notes"] == "b"
    # final -> 409 "bereits abgeschlossen"
    dbx.pickup_protocols.update_one({"id": pid}, {"$set": {"status": "final"},
                                                 "$unset": {"claim_bis": ""}})
    r = requests.put(url, headers=fahrer["h"], json={"notes": "d"}, timeout=30)
    assert r.status_code == 409 and "bereits abgeschlossen" in r.text, r.text[:200]


# =============================================================== Vertragsversand
@pytest.fixture(scope="module")
def vertrag():
    r = requests.post(f"{API}/auth/register", json={
        "email": f"nachfirma_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": "Nach GmbH", "contact_person": "N T", "phone": "0511 9"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    me = requests.get(f"{API}/auth/me", headers=h, timeout=30).json()["user"]
    _db().subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": me["dealer_id"], "subject_user_id": me["id"],
        "plan": "monthly", "status": "active",
        "expires_at": (JETZT + timedelta(days=1)).isoformat(), "created_at": JETZT.isoformat()})
    ka = f"https://www.kleinanzeigen.de/s-anzeige/nach/94{uuid.uuid4().int % 10**8:08d}-216-1"
    r = requests.post(f"{API}/mobile/compare", json={"url": ka}, headers=h, timeout=90)
    if r.status_code != 200 or not (r.json().get("vehicle") or {}).get("_mock"):
        pytest.skip("Backend ohne MOCK_PROVIDER_FETCH")
    vid = r.json()["vehicle_id"]
    r = requests.post(f"{API}/contracts", headers=h, json={
        "vehicle_id": vid, "seller_name": "N V", "seller_address": "Weg 1",
        "seller_zip": "30159", "seller_city": "Hannover", "purchase_price": 5000,
        "pickup_date": "2099-06-01", "pickup_time": "10:00"}, timeout=90)
    assert r.status_code == 200, r.text[:200]
    yield {"h": h, "cid": r.json()["id"], "me": me, "ka": ka, "vid": vid}
    dbx = _db()
    for c in ("subscriptions", "vehicles", "appointments", "generated_pdfs", "activity_logs"):
        dbx[c].delete_many({"dealer_id": me["dealer_id"]})
    dbx.users.delete_many({"id": me["id"]})
    dbx.dealers.delete_many({"id": me["dealer_id"]})


def _eintraege(cid):
    c = _db().generated_pdfs.find_one({"id": cid}, {"_id": 0, "send_status": 1})
    return (c or {}).get("send_status") or []


def test_neuer_schluessel_nimmt_haengenden_versand_wieder_auf(vertrag):
    """Die Oberflaeche schickt je Klick einen neuen Schluessel. Haengt ein
    Versand ohne Ergebnis, muss der naechste Klick ihn WIEDERAUFNEHMEN
    statt ein zweites Mal zuzustellen."""
    alt = f"alt-{uuid.uuid4().hex[:12]}"
    _db().generated_pdfs.update_one({"id": vertrag["cid"]}, {"$push": {"send_status": {
        "idempotency_key": alt, "channel": "whatsapp", "recipient": "+491700000001",
        "sent_at": (JETZT - timedelta(minutes=20)).isoformat(), "zustellung": "unklar"}}})
    vorher = len(_eintraege(vertrag["cid"]))
    r = requests.post(f"{API}/contracts/{vertrag['cid']}/send", headers=vertrag["h"], json={
        "channel": "whatsapp", "recipient": "+491700000001", "message": "Hier der Vertrag.",
        "idempotency_key": f"neu-{uuid.uuid4().hex[:12]}"}, timeout=60)
    assert r.status_code == 200, r.text[:300]
    nachher = _eintraege(vertrag["cid"])
    assert len(nachher) == vorher, "haengender Versand wurde ein zweites Mal angelegt"
    eintrag = next(e for e in nachher if e.get("idempotency_key") == alt)
    assert eintrag.get("zustellung") not in ("unklar", "laeuft"), eintrag
    # ein NEUER Empfaenger ist kein haengender Versand -> eigener Eintrag
    r = requests.post(f"{API}/contracts/{vertrag['cid']}/send", headers=vertrag["h"], json={
        "channel": "whatsapp", "recipient": "+491700000002", "message": "Hier der Vertrag.",
        "idempotency_key": f"neu-{uuid.uuid4().hex[:12]}"}, timeout=60)
    assert r.status_code == 200 and len(_eintraege(vertrag["cid"])) == vorher + 1


def test_auto_schluessel_kennt_die_vertragsversion():
    from routes.contracts import _auto_schluessel
    body = SimpleNamespace(channel="email", recipient="a@b.de", subject="S", message="T")
    k1 = _auto_schluessel("c1", {"version": 1}, body)
    assert k1 == _auto_schluessel("c1", {}, body), "fehlende Version zaehlt als 1"
    assert k1 != _auto_schluessel("c1", {"version": 2}, body), "neues PDF muss neu senden duerfen"
    assert k1 != _auto_schluessel("c2", {"version": 1}, body)


def test_kopie_ist_bei_wiederaufnahme_inhaltsgleich():
    from vertrag_mail import kopie_mail
    args = dict(vertrag={"seller_name": "V", "contract_no": "K-1"}, firma={"company_name": "F"},
                sucher={"name": "S"}, empfaenger_adresse="v@e2etest-mail.de",
                betreff_original="Kaufvertrag", nachricht="Hallo")
    a = kopie_mail(**args, zeitpunkt="2026-09-06T20:15:00+00:00")
    b = kopie_mail(**args, zeitpunkt="2026-09-06T20:15:00+00:00")
    assert a == b
    assert "06.09.2026" in a[1]
    c = kopie_mail(**args)                       # ohne Angabe: die Uhr
    assert datetime.now().strftime("%d.%m.%Y") in c[1]


# =============================================================== Aufraeumer
def test_termin_mit_baumelndem_vertrag_wird_bereinigt(monkeypatch):
    monkeypatch.setenv("VERTRAG_LOESCHUNG_AKTIV", "ja")            # gleiche Lesart wie die Vertragsloeschung
    from cleanup_service import termine_ohne_vertrag_bereinigen
    alt = f"nach-alt-{SUF}"
    mit = f"nach-mit-{SUF}"
    cid = f"nach-vertrag-{SUF}"

    async def _lauf(db):
        await db.generated_pdfs.insert_one({"id": cid, "dealer_id": f"f-{SUF}"})
        await db.appointments.insert_many([
            {"id": alt, "dealer_id": f"f-{SUF}", "contract_id": "gibt-es-nicht",
             "seller_name": "Anna", "pickup_date": (JETZT - timedelta(days=120)).strftime("%Y-%m-%d"),
             "created_at": (JETZT - timedelta(days=130)).isoformat()},
            {"id": mit, "dealer_id": f"f-{SUF}", "contract_id": cid,
             "seller_name": "Bert", "pickup_date": (JETZT - timedelta(days=120)).strftime("%Y-%m-%d"),
             "created_at": (JETZT - timedelta(days=130)).isoformat()},
        ])
        try:
            await termine_ohne_vertrag_bereinigen(db, JETZT)
            return (await db.appointments.find_one({"id": alt}, {"_id": 0}),
                    await db.appointments.find_one({"id": mit}, {"_id": 0}))
        finally:
            await db.appointments.delete_many({"id": {"$in": [alt, mit]}})
            await db.generated_pdfs.delete_many({"id": cid})
    a, m = _mit_db(_lauf)
    assert a["seller_name"] == "" and a.get("pii_geloescht_at"), "baumelnder Verweis nicht erfasst"
    assert m["seller_name"] == "Bert", "Termin mit echtem Vertrag gehoert der Vertragsfrist"


def test_fehlgeschlagene_protokollbereinigung_wird_nicht_als_erledigt_markiert(monkeypatch):
    monkeypatch.setenv("VERTRAG_LOESCHUNG_AKTIV", "true")
    import cleanup_service as cs

    async def _kaputt(*a, **k):
        raise RuntimeError("Speicher weg")
    monkeypatch.setattr(cs, "_protokolle_pii_entfernen", _kaputt)
    tid = f"nach-fehl-{SUF}"

    async def _lauf(db):
        await db.appointments.insert_one(
            {"id": tid, "dealer_id": f"f-{SUF}", "contract_id": None, "seller_name": "Carl",
             "pickup_date": (JETZT - timedelta(days=120)).strftime("%Y-%m-%d"),
             "created_at": (JETZT - timedelta(days=130)).isoformat()})
        try:
            n = await cs.termine_ohne_vertrag_bereinigen(db, JETZT)
            return n, await db.appointments.find_one({"id": tid}, {"_id": 0})
        finally:
            await db.appointments.delete_many({"id": tid})
    n, t = _mit_db(_lauf)
    assert n == 0 and t["seller_name"] == "Carl" and not t.get("pii_geloescht_at"), \
        "Termin als bereinigt markiert, obwohl die Protokoll-Dateien blieben"


# =============================================================== Betrieb
def test_betriebsdateien_tragen_die_korrekturen():
    wurzel = BACKEND.parent
    nginx = (wurzel / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    assert 'proxy_set_header CF-Connecting-IP  "";' in nginx
    compose = (wurzel / "docker-compose.yml").read_text(encoding="utf-8")
    assert "TRUSTED_PROXIES_NUR_LISTE=${TRUSTED_PROXIES_NUR_LISTE:-false}" in compose
    sh = (wurzel / "deploy" / "lasttest-auf-prod2.sh").read_text(encoding="utf-8")
    assert "-v last-uploads:/app/uploads" in sh and "docker volume rm last-uploads" in sh
    doku = (wurzel / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "stuendliche Sicherung" not in doku
    assert 'TTL "2 min"' in doku and "nur vom LB und vom eigenen nginx-Container" not in doku
    probe = (BACKEND / "scripts" / "anbieter_probe.py").read_text(encoding="utf-8")
    assert "zweiter Abruf brach ab" in probe


def test_kein_helfer_zwischen_dekorator_und_handler():
    """Sicherheitsnetz: Steht eine Hilfsfunktion zwischen `@router...` und dem
    Handler, haengt der Dekorator an der falschen Funktion — der Endpunkt
    verlangt dann deren Parameter als Query (422 fuer jeden Aufruf). Ist in
    dieser Codebasis zweimal passiert (appointments, contracts)."""
    import ast as _ast
    fehler = []
    for datei in sorted((BACKEND / "routes").glob("*.py")):
        baum = _ast.parse(datei.read_text(encoding="utf-8"))
        for knoten in _ast.walk(baum):
            if isinstance(knoten, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and knoten.name.startswith("_"):
                for dek in knoten.decorator_list:
                    quelle = _ast.unparse(dek)
                    if quelle.startswith("router."):
                        fehler.append(f"{datei.name}: @{quelle} haengt an {knoten.name}")
    assert not fehler, fehler


def test_nur_liste_ohne_liste_warnt(capsys):
    """NUR_LISTE ohne Liste bleibt beim sicheren Standard — aber laut."""
    import subprocess
    code = ("import logging; logging.basicConfig(level=logging.WARNING); "
            "import rate_limiter as r; print(r._ist_vermittler('172.18.0.2'))")
    env = {**os.environ, "TRUSTED_PROXIES": "", "TRUSTED_PROXIES_NUR_LISTE": "true"}
    out = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=str(BACKEND),
                         env=env, capture_output=True, text=True, timeout=60)
    assert out.stdout.strip().endswith("True"), out.stdout + out.stderr
    assert "TRUSTED_PROXIES_NUR_LISTE=true, aber TRUSTED_PROXIES ist leer" in (out.stderr + out.stdout)
