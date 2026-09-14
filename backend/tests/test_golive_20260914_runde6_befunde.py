# -*- coding: utf-8 -*-
"""Go-Live 14.09.2026, Runde 6 — Befunde 6, 7, 8, 9, 10, 13 (dritte Liste Ahmad):

  6  Protokoll-Reparatur findet die finale Version auch unter mehr als 50 Entwuerfen
  7  Publish-Sperre traegt ein Besitzer-Token; eine fremde Sperre bleibt stehen
  8  Kontingent-Zaehler wird VOR der Markierung des Inserats befuellt
  9  mehrstufiger Fahrzeug-Lebenszyklus in EINEM Write (CAS), Audit je Schritt
  10 Netzwerk-Widerruf sperrt alle noch gueltigen Einladungen fuer den Kaeufer
  13 Freigabe-Liste: Betriebsalarm, wenn die Obergrenze erreicht ist

In-process gegen eine Wegwerf-Datenbank; Befund 10 per HTTP (RUNDE14_HTTP=1).
"""
import asyncio
import inspect
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import deps  # noqa: E402
import konten as K  # noqa: E402
import routes.marketplace as M  # noqa: E402
import routes.protocols as P  # noqa: E402
import routes.resale as R  # noqa: E402
from lifecycle import LifecycleError  # noqa: E402

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
http = pytest.mark.skipif(not HTTP, reason="RUNDE14_HTTP=1 nicht gesetzt")
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
API = K.API
SUF = uuid.uuid4().hex[:8]
PW = f"Rz7{SUF}Kq4Lm9Xw2"


def _jetzt(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_r6b_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, P, R, M):
        monkeypatch.setattr(mod, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


# ============================================================ 6
def test_06_reparatur_findet_finale_version_unter_vielen_entwuerfen(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    appt = f"t6_{SUF}"
    alt = _jetzt(-3600)
    docs = [{"id": "p1", "appointment_id": appt, "version": 1, "status": "final",
             "superseded": True, "superseded_at": alt, "dealer_id": "d6"}]
    for v in range(2, 60):                       # 58 abgeloeste Entwuerfe darueber
        docs.append({"id": f"p{v}", "appointment_id": appt, "version": v, "status": "entwurf",
                     "superseded": True, "superseded_at": alt, "dealer_id": "d6"})
    run(db.pickup_protocols.insert_many(docs))
    erg = run(P.ohne_aktuelle_version_reparieren(appt, dbx=db))
    assert erg and erg["id"] == "p1" and erg["status"] == "final", erg
    assert run(db.pickup_protocols.count_documents(
        {"appointment_id": appt, "superseded": False})) == 1
    assert run(db.pickup_protocols.find_one({"id": "p1"}))["superseded"] is False
    # ohne finale Version: die hoechste Version wird reaktiviert
    appt2 = f"t6b_{SUF}"
    run(db.pickup_protocols.insert_many([
        {"id": f"q{v}", "appointment_id": appt2, "version": v, "status": "entwurf",
         "superseded": True, "superseded_at": alt} for v in range(1, 4)]))
    erg = run(P.ohne_aktuelle_version_reparieren(appt2, dbx=db))
    assert erg and erg["id"] == "q3"
    # innerhalb der Karenz (gerade erst abgeloest): keine Reparatur
    appt3 = f"t6c_{SUF}"
    run(db.pickup_protocols.insert_one({"id": "r1", "appointment_id": appt3, "version": 1,
                                        "status": "final", "superseded": True,
                                        "superseded_at": _jetzt()}))
    assert run(P.ohne_aktuelle_version_reparieren(appt3, dbx=db)) is None


# ============================================================ 7 + 8
def test_07_08_publish_sperre_mit_token_und_seeding_vor_markierung(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    src = inspect.getsource(R.publish_listing)
    # 7: Token wird gesetzt und nur die eigene Sperre am Ende geloescht
    assert '"publish_lock_token": _token' in src
    assert src.index('"publish_lock_token": _token}') < src.index('"$unset": {"publish_lock_until": ""')
    assert '"publish_lock_token": _token},\n            {"$unset"' in src
    # 8: Seeding des Zaehlers steht VOR "Schritt 1" (Markierung des Inserats)
    assert src.index('{"$set": {field: cur}}') < src.index('"$addToSet": {"counted_periods": period_key}')
    # 7 (Verhalten): fremde, noch gueltige Sperre -> 409, und sie bleibt stehen
    did = f"d7_{SUF}"
    user = {"id": "u7", "dealer_id": did, "role": "dealer"}
    run(db.resale_listings.insert_one({
        "id": "L7", "dealer_id": did, "status": "verkaufsbereit", "prices": {"public": 1000},
        "publish_lock_until": datetime.now(timezone.utc) + timedelta(seconds=25),
        "publish_lock_token": "fremd", "created_at": _jetzt(), "updated_at": _jetzt()}))
    with pytest.raises(HTTPException) as e:
        run(R.publish_listing("L7", R.PublishIn(visibility="public"), user))
    assert e.value.status_code == 409, e.value.detail
    d = run(db.resale_listings.find_one({"id": "L7"}, {"_id": 0}))
    assert d["publish_lock_token"] == "fremd" and d.get("publish_lock_until") is not None
    assert d["status"] == "verkaufsbereit"


# ============================================================ 9
def test_09_mehrstufiger_lebenszyklus_ein_write(monkeypatch):
    from test_befunde_runde14_resale import _Db
    fake = _Db(vehicles=[{"id": "V9", "dealer_id": "d9", "lifecycle": "abgeholt"}])
    monkeypatch.setattr(R, "db", fake)
    audit = []

    async def _log(dealer_id, user_id, action, ref=None, meta=None):
        audit.append((action, (meta or {}).get("von"), (meta or {}).get("nach")))

    monkeypatch.setattr(R, "log_activity_sicher", _log)
    user = {"id": "u9", "dealer_id": "d9"}
    # zwei Schritte -> Endzustand, zwei Audit-Eintraege
    asyncio.run(R._lifecycle_anwenden("V9", "d9", ["verkaufsentwurf", "verkaufsbereit"], user))
    v = fake.vehicles.one(id="V9")
    assert v["lifecycle"] == "verkaufsbereit" and v.get("lifecycle_changed_at")
    assert audit == [("fahrzeug.status.verkaufsentwurf", "abgeholt", "verkaufsentwurf"),
                     ("fahrzeug.status.verkaufsbereit", "verkaufsentwurf", "verkaufsbereit")]
    # unerlaubter zweiter Schritt -> Fehler, NICHTS geschrieben (kein Zwischenzustand)
    fake2 = _Db(vehicles=[{"id": "V9", "dealer_id": "d9", "lifecycle": "abgeholt"}])
    monkeypatch.setattr(R, "db", fake2)
    audit.clear()
    with pytest.raises(LifecycleError):
        asyncio.run(R._lifecycle_anwenden("V9", "d9", ["bestand", "veroeffentlicht"], user))
    assert fake2.vehicles.one(id="V9")["lifecycle"] == "abgeholt" and audit == []
    # Rennen: Stand aendert sich zwischen Lesen und Schreiben -> Fehler, fremder Stand bleibt
    fake3 = _Db(vehicles=[{"id": "V9", "dealer_id": "d9", "lifecycle": "abgeholt"}])
    monkeypatch.setattr(R, "db", fake3)
    echt = fake3.vehicles.find_one

    async def _liest_und_anderer_schreibt(q, proj=None, **kw):
        d = await echt(q, proj, **kw)
        fake3.vehicles.one(id="V9")["lifecycle"] = "bestand"
        return d

    monkeypatch.setattr(fake3.vehicles, "find_one", _liest_und_anderer_schreibt)
    with pytest.raises(LifecycleError) as e:
        asyncio.run(R._lifecycle_anwenden("V9", "d9", ["verkaufsentwurf", "verkaufsbereit"], user))
    assert "zwischenzeitlich" in str(e.value)
    assert fake3.vehicles.one(id="V9")["lifecycle"] == "bestand" and audit == []
    # ein Schritt geht weiter ueber set_lifecycle (Stub-faehig)
    aufrufe = []

    async def _einzeln(vehicle_id, dealer_id, ziel, *, user=None, force=False):
        aufrufe.append(ziel)

    monkeypatch.setattr(R, "set_lifecycle", _einzeln)
    asyncio.run(R._lifecycle_anwenden("V9", "d9", ["verkaufsentwurf"], user))
    assert aufrufe == ["verkaufsentwurf"]


# ============================================================ 13
def test_13_freigaben_alarm_bei_obergrenze(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    did = f"d13_{SUF}"
    for i in (1, 2):
        run(db.appointments.insert_one({"id": f"a{i}", "dealer_id": did, "status": "geplant",
                                        "pickup_date": "2026-12-01", "vehicle_id": f"v{i}",
                                        "created_at": _jetzt()}))
        run(db.pickup_protocols.insert_one({"id": f"p{i}", "dealer_id": did, "appointment_id": f"a{i}",
                                            "status": P.ZUR_FREIGABE, "superseded": False,
                                            "version": 1, "abgeschickt_am": _jetzt(-i),
                                            "created_at": _jetzt()}))
    alarme = []

    async def _alarm(dbx, code, ref=None, meta=None, **kw):
        alarme.append((code, ref, meta))

    monkeypatch.setattr(P.betrieb, "alarm", _alarm)
    user = {"id": "u13", "dealer_id": did, "role": "dealer"}
    monkeypatch.setattr(P, "_FREIGABE_MAX", 1)
    run(P._wartende_protokolle(user))
    assert alarme and alarme[0][0] == "freigaben_liste_abgeschnitten" and alarme[0][1] == did
    alarme.clear()
    monkeypatch.setattr(P, "_FREIGABE_MAX", 5000)
    paare = run(P._wartende_protokolle(user))
    assert len(paare) == 2 and alarme == []


# ============================================================ 10 (HTTP)
@pytest.fixture(scope="module")
def welt_http():
    if not HTTP:
        pytest.skip("RUNDE14_HTTP=1 nicht gesetzt")
    dbx = K._db()
    z = {"user_ids": [], "dealer_ids": []}
    try:
        r = K.registrieren(json={"company_name": f"R6B Firma {SUF}", "password": PW,
                                 "contact_person": "Chef", "email": ""})
        assert r.status_code == 200, r.text[:300]
        z["C"] = {"Authorization": f"Bearer {r.json()['token']}"}
        me = requests.get(f"{API}/auth/me", headers=z["C"], timeout=60).json()["user"]
        z["chef_id"], z["dealer_id"] = me["id"], me["dealer_id"]
        z["user_ids"].append(me["id"])
        z["dealer_ids"].append(me["dealer_id"])
        r = K.kaeufer_registrieren(json={"company_name": f"R6B Kaeufer {SUF}", "contact_name": "Kai",
                                         "password": PW, "gewerblich_bestaetigt": True, "email": ""})
        assert r.status_code == 200, r.text[:300]
        z["KB"] = {"Authorization": f"Bearer {r.json()['token']}"}
        z["kaeufer_id"] = r.json()["user"]["id"]
        z["user_ids"].append(z["kaeufer_id"])
        yield z
    finally:
        dbx.users.delete_many({"$or": [{"id": {"$in": z["user_ids"]}},
                                       {"company_name": {"$regex": SUF}}]})
        dbx.dealers.delete_many({"$or": [{"id": {"$in": z["dealer_ids"]}},
                                         {"company_name": {"$regex": SUF}}]})
        for coll in ("subscriptions", "dealer_invites", "network_members"):
            dbx[coll].delete_many({"dealer_id": {"$in": z["dealer_ids"]}})
        dbx.buyer_favorites.delete_many({"buyer_user_id": {"$in": z["user_ids"]}})
        dbx.activity_logs.delete_many({"$or": [{"user_id": {"$in": z["user_ids"]}},
                                               {"ref": {"$in": z["user_ids"]}}]})


@http
def test_10_widerruf_sperrt_alte_einladungen_neue_gelten(welt_http):
    z = welt_http
    dbx = K._db()
    C, KB = z["C"], z["KB"]
    r = requests.post(f"{API}/dealer/invites", json={"validity_hours": 24, "max_uses": 5},
                      headers=C, timeout=60)
    assert r.status_code == 200, r.text[:300]
    token = r.json()["token"]
    r = requests.post(f"{API}/invites/{token}/redeem", headers=KB, timeout=60)
    assert r.status_code == 200, r.text[:300]
    assert dbx.network_members.count_documents({"dealer_id": z["dealer_id"],
                                                "buyer_user_id": z["kaeufer_id"]}) == 1
    # Widerruf: Mitgliedschaft weg, der Mehrfach-Link ist fuer diesen Kaeufer gesperrt
    r = requests.delete(f"{API}/dealer/network/members/{z['kaeufer_id']}", headers=C, timeout=60)
    assert r.status_code == 200, r.text[:300]
    assert dbx.network_members.count_documents({"dealer_id": z["dealer_id"],
                                                "buyer_user_id": z["kaeufer_id"]}) == 0
    inv = dbx.dealer_invites.find_one({"token": token})
    assert z["kaeufer_id"] in inv.get("gesperrt_fuer", []), inv
    r = requests.post(f"{API}/invites/{token}/redeem", headers=KB, timeout=60)
    assert r.status_code == 400, (r.status_code, r.text[:200])
    assert dbx.network_members.count_documents({"dealer_id": z["dealer_id"],
                                                "buyer_user_id": z["kaeufer_id"]}) == 0
    # Auch ein Link, den der Kaeufer NIE benutzt hat, ist nach dem Widerruf gesperrt
    r = requests.post(f"{API}/dealer/invites", json={"validity_hours": 24, "max_uses": 5},
                      headers=C, timeout=60)
    unbenutzt = r.json()["token"]
    dbx.dealer_invites.update_one({"token": unbenutzt},
                                  {"$set": {"created_at": _jetzt(-60)}})
    # (Einladung existierte VOR dem Widerruf -> simuliert durch erneuten Widerruf-Lauf)
    dbx.network_members.insert_one({"dealer_id": z["dealer_id"], "buyer_user_id": z["kaeufer_id"],
                                    "via_invite_id": "manuell", "created_at": _jetzt()})
    r = requests.delete(f"{API}/dealer/network/members/{z['kaeufer_id']}", headers=C, timeout=60)
    assert r.status_code == 200, r.text[:300]
    r = requests.post(f"{API}/invites/{unbenutzt}/redeem", headers=KB, timeout=60)
    assert r.status_code == 400, (r.status_code, r.text[:200])
    # Eine NEUE Einladung nach dem Widerruf gilt wieder
    r = requests.post(f"{API}/dealer/invites", json={"validity_hours": 24, "max_uses": 1},
                      headers=C, timeout=60)
    neu = r.json()["token"]
    r = requests.post(f"{API}/invites/{neu}/redeem", headers=KB, timeout=60)
    assert r.status_code == 200, (r.status_code, r.text[:200])
    assert dbx.network_members.count_documents({"dealer_id": z["dealer_id"],
                                                "buyer_user_id": z["kaeufer_id"]}) == 1
