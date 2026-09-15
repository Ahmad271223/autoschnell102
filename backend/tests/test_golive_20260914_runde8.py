# -*- coding: utf-8 -*-
"""Go-Live 14.09.2026, Runde 8 (15.09.2026) — neue Befunde (Listen 3 und 4):

  L3-1  Abholbericht nach Konto-Loeschung: Nachpruefung nach dem Insert (Quelle)
  L3-2  Fahrer wieder hinzufuegen trennt liegengebliebene Alt-Termine (HTTP)
  L3-3  Fahrer-Loeschung pseudonymisiert auch Audit-Eintraege mit ref=Fahrer
  L3-4  network_members in der Firmen-Loeschkaskade + Altbestand-Nachlauf
  L3-6  Nutzer-Loeschung pseudonymisiert das Audit-Log
  L3-10 Termine ohne Vertrag: dieselbe Frist wie Vertraege (60 Tage)
  L3-12/13 Migrations-Sperre mit Heartbeat, Wartende bleiben dran
  L4-1  Protokoll-Reparatur ohne 500er-Deckel auf immer denselben Terminen
  L4-13 Verkaufspaket mit Stand-Pruefung (409 bei paralleler Aenderung)
  L4-14 Paket unmittelbar vor dem Live-Schalten erneut geprueft (Quelle)
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
import routes.admin as ADMIN  # noqa: E402
import routes.drivers as DRV  # noqa: E402
import routes.resale as R  # noqa: E402
import cleanup_service as CS  # noqa: E402
import migrationen as MIG  # noqa: E402
import job_lock as JL  # noqa: E402

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
http = pytest.mark.skipif(not HTTP, reason="RUNDE14_HTTP=1 nicht gesetzt")
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
API = K.API
SUF = uuid.uuid4().hex[:8]
PW = f"Rz8{SUF}Kq4Lm9Xw2"


def _jetzt(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_r8_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, ADMIN, DRV, R):
        monkeypatch.setattr(mod, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


# ============================================================ L3-3
def test_l3_3_fahrer_loeschung_pseudonymisiert_ref_eintraege(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.activity_logs.insert_many([
        {"id": "a", "user_id": "adm", "action": "admin.fahrer.gesperrt", "ref": "f1",
         "meta": {"kontonummer": "FD-AAAA2222", "grund": "x"}},
        {"id": "b", "user_id": "f1", "action": "auth.login", "meta": {"email": "f@x.de"}},
        {"id": "c", "user_id": "adm", "action": "admin.fahrer.gesperrt", "ref": "f2",
         "meta": {"kontonummer": "FD-BBBB3333"}},
    ]))
    erg = run(DRV.fahrer_konto_anonymisieren(db, "f1"))
    pseud = DRV.fahrer_pseudonym("f1")
    assert erg["pseudonym"] == pseud and erg["activity_logs"] == 2
    a = run(db.activity_logs.find_one({"id": "a"}, {"_id": 0}))
    assert a["ref"] == pseud and "kontonummer" not in a["meta"] and a["meta"]["grund"] == "x"
    b = run(db.activity_logs.find_one({"id": "b"}, {"_id": 0}))
    assert b["user_id"] == pseud and "email" not in b["meta"]
    c = run(db.activity_logs.find_one({"id": "c"}, {"_id": 0}))
    assert c["ref"] == "f2" and c["meta"]["kontonummer"] == "FD-BBBB3333"


# ============================================================ L3-1
def test_l3_1_bericht_nachpruefung_nach_insert():
    src = inspect.getsource(DRV.driver_submit_report)
    assert "Liste 3 Nr. 1" in src
    assert src.index("pickup_reports.insert_one(doc)") < src.index('fahrer_pseudonym(driver["id"])')
    assert '"loeschung"' in src


# ============================================================ L3-2
@http
def test_l3_2_wieder_hinzufuegen_trennt_alte_termine():
    firma = K.registrieren(json={"email": f"r8chef{SUF}@example.com", "password": PW,
                                 "company_name": f"R8 Chef {SUF}", "contact_person": "Chef"})
    assert firma.status_code == 200, firma.text[:300]
    C = K._kopf(firma.json()["token"])
    dealer_id = firma.json()["user"]["dealer_id"]
    fahrer = K.fahrer_registrieren(json={"display_name": f"Fahrer {SUF}", "password": PW,
                                         "email": f"r8fahrer{SUF}@example.com"})
    assert fahrer.status_code == 200, fahrer.text[:300]
    fid = fahrer.json()["driver"]["id"]
    code = fahrer.json()["driver"]["driver_code"]
    r = requests.post(f"{API}/drivers/add", json={"driver_code": code}, headers=C, timeout=60)
    assert r.status_code == 200, r.text[:300]
    dbx = K._db()
    a1, a2 = f"r8a1_{SUF}", f"r8a2_{SUF}"
    dbx.appointments.insert_many([
        {"id": a1, "dealer_id": dealer_id, "driver_id": fid, "status": "abgeholt",
         "created_at": _jetzt(-3600), "updated_at": _jetzt(-3600)},
        {"id": a2, "dealer_id": dealer_id, "driver_id": fid, "status": "offen",
         "zuteilung": {"status": "angefragt"}, "created_at": _jetzt(-3600),
         "updated_at": _jetzt(-3600)},
    ])
    # Bereits verknuepft: 409, Termine bleiben unangetastet
    r = requests.post(f"{API}/drivers/add", json={"driver_code": code}, headers=C, timeout=60)
    assert r.status_code == 409, r.text[:300]
    assert dbx.appointments.find_one({"id": a1})["driver_id"] == fid
    # Liegengebliebener Rest: Verknuepfung weg, Termine tragen noch driver_id
    dbx.dealer_drivers.delete_one({"dealer_id": dealer_id, "driver_account_id": fid})
    r = requests.post(f"{API}/drivers/add", json={"driver_code": code}, headers=C, timeout=60)
    assert r.status_code == 200, r.text[:300]
    t1 = dbx.appointments.find_one({"id": a1}, {"_id": 0})
    t2 = dbx.appointments.find_one({"id": a2}, {"_id": 0})
    assert "driver_id" not in t1 and t1["driver_id_hist"] == fid
    assert "driver_id" not in t2 and t2["zuteilung"] is None
    assert dbx.dealer_drivers.count_documents({"dealer_id": dealer_id, "driver_account_id": fid}) == 1
    dbx.appointments.delete_many({"id": {"$in": [a1, a2]}})


# ============================================================ L3-4
def test_l3_4_network_members_in_kaskade_und_nachlauf(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    assert "network_members" in ADMIN._COMPANY_COLLECTIONS
    run(db.dealers.insert_one({"id": "d1", "company_name": "Bleibt"}))
    run(db.network_members.insert_many([
        {"dealer_id": "d1", "buyer_user_id": "k1"},
        {"dealer_id": "d-weg", "buyer_user_id": "k1"},
        {"dealer_id": "d-weg", "buyer_user_id": "k2"},
    ]))
    n = run(CS.firmenreste_bereinigen(db))
    assert n >= 2
    assert run(db.network_members.count_documents({"dealer_id": "d-weg"})) == 0
    assert run(db.network_members.count_documents({"dealer_id": "d1"})) == 1


# ============================================================ L3-6
def test_l3_6_nutzer_audit_pseudonymisiert(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.activity_logs.insert_many([
        {"id": "l1", "user_id": "u1", "action": "auth.login",
         "meta": {"email": "u@x.de", "ip": "1.2.3.4", "geraet": "Firefox", "kontonummer": "10023-2"}},
        {"id": "l2", "user_id": "adm", "action": "admin.user.gesperrt", "ref": "u1",
         "meta": {"kontonummer": "10023-2", "grund": "test"}},
        {"id": "l3", "user_id": "u2", "action": "auth.login", "meta": {"ip": "5.6.7.8"}},
    ]))
    pseud = ADMIN._nutzer_pseudonym("u1")
    assert pseud.startswith("geloescht:") and len(pseud) == len("geloescht:") + 8
    assert run(ADMIN._audit_pseudonymisieren("u1", pseud)) == 2
    l1 = run(db.activity_logs.find_one({"id": "l1"}, {"_id": 0}))
    assert l1["user_id"] == pseud and l1["meta"] == {}
    l2 = run(db.activity_logs.find_one({"id": "l2"}, {"_id": 0}))
    assert l2["ref"] == pseud and l2["meta"] == {"grund": "test"}
    l3 = run(db.activity_logs.find_one({"id": "l3"}, {"_id": 0}))
    assert l3["user_id"] == "u2" and l3["meta"]["ip"] == "5.6.7.8"
    src = inspect.getsource(ADMIN.admin_delete_user)
    assert src.count("_audit_pseudonymisieren(") == 2, "Einzelkonto UND Firmenkaskade"
    # Die Abschluss-Audits (nach der Pseudonymisierung) tragen keine Kontonummer
    # mehr; das Start-Audit davor wird von der Pseudonymisierung mit erfasst.
    for marke in ("admin.user.geloescht", "admin.firma.geloescht"):
        k = src.index(marke)
        m = src.index("meta={", k)
        assert "kontonummer" not in src[m:src.index("})", m)], marke


# ============================================================ L3-10
def test_l3_10_termine_ohne_vertrag_60_tage(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    monkeypatch.setenv("VERTRAG_LOESCHUNG_AKTIV", "true")
    assert inspect.signature(CS.termine_ohne_vertrag_bereinigen).parameters["frist_tage"].default == 0
    assert CS.VERTRAG_AUFBEWAHRUNG_TAGE == 60
    jetzt = datetime.now(timezone.utc)
    run(db.appointments.insert_many([
        {"id": "alt", "dealer_id": "d1", "seller_name": "Anna", "seller_phone": "1",
         "seller_email": "a@x.de", "pickup_address": "Weg 1",
         "pickup_date": (jetzt - timedelta(days=70)).strftime("%Y-%m-%d"),
         "created_at": (jetzt - timedelta(days=75)).isoformat()},
        {"id": "jung", "dealer_id": "d1", "seller_name": "Bert", "pickup_address": "Weg 2",
         "pickup_date": (jetzt - timedelta(days=50)).strftime("%Y-%m-%d"),
         "created_at": (jetzt - timedelta(days=55)).isoformat()},
    ]))
    n = run(CS.termine_ohne_vertrag_bereinigen(db, jetzt))
    assert n == 1
    alt = run(db.appointments.find_one({"id": "alt"}, {"_id": 0}))
    jung = run(db.appointments.find_one({"id": "jung"}, {"_id": 0}))
    assert alt["seller_name"] == "" and alt.get("pii_geloescht_at")
    assert jung["seller_name"] == "Bert" and not jung.get("pii_geloescht_at")


# ============================================================ L4-1
def test_l4_1_protokoll_reparatur_nur_termine_ohne_aktuelle_version(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    alt = _jetzt(-86400)

    def p(appt, version, status, superseded):
        d = {"id": f"p_{appt}_{version}", "appointment_id": appt, "version": version,
             "status": status, "superseded": superseded, "created_at": alt}
        if superseded:
            d["superseded_at"] = alt
        return d

    run(db.pickup_protocols.insert_many([
        p("A", 1, "final", True), p("A", 2, "entwurf", True),      # nur abgeloeste
        p("B", 1, "final", True), p("B", 2, "entwurf", False),     # hat aktuelle
        p("C", 1, "entwurf", True),                                # nur abgeloeste
    ]))
    assert run(CS.protokolle_ohne_aktuelle_version_reparieren(db)) == 2
    a = run(db.pickup_protocols.find_one({"appointment_id": "A", "superseded": False}, {"_id": 0}))
    assert a and a["version"] == 1 and a["status"] == "final", "hoechste FINALE Version"
    c = run(db.pickup_protocols.find_one({"appointment_id": "C", "superseded": False}, {"_id": 0}))
    assert c and c["version"] == 1
    assert run(db.pickup_protocols.count_documents({"appointment_id": "B", "superseded": False})) == 1
    assert run(db.pickup_protocols.find_one({"id": "p_B_1"}))["superseded"] is True
    # zweiter Lauf: nichts mehr zu tun (vorher standen reparierte Termine
    # bei jedem Lauf wieder in den ersten 500)
    assert run(CS.protokolle_ohne_aktuelle_version_reparieren(db)) == 0


# ============================================================ L3-12/13
def test_l3_12_13_migrations_sperre_heartbeat_und_warten(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(JL.ensure_lock_index(db))
    assert run(MIG._sperre_holen(db)) is True
    assert run(MIG._sperre_gehalten(db)) is True
    vorher = run(db.job_locks.find_one({"name": MIG._SPERRE}))["expires_at"]
    assert run(MIG._sperre_verlaengern(db)) is True
    nachher = run(db.job_locks.find_one({"name": MIG._SPERRE}))["expires_at"]
    assert nachher >= vorher
    run(db.system_flags.update_one({"_id": "schema"}, {"$set": {"version": 0}}, upsert=True))

    async def leader_lebt_noch():
        # Sperre wird gehalten (Leader arbeitet): warte_sekunden=1 darf NICHT
        # zum Abbruch fuehren — erst wenn die Version nach 2,5 s da ist.
        async def spaeter():
            await asyncio.sleep(2.5)
            await db.system_flags.update_one(
                {"_id": "schema"}, {"$set": {"version": MIG.ZIEL_VERSION}}, upsert=True)
        t = asyncio.ensure_future(spaeter())
        erg = await MIG.ausfuehren_oder_warten(db, warte_sekunden=1)
        await t
        return erg

    assert run(leader_lebt_noch()) == "gewartet"
    # Sperre frei -> dieser Prozess wird Leader, Heartbeat laeuft mit und
    # die Sperre ist danach freigegeben.
    run(MIG._sperre_loesen(db))
    run(db.system_flags.update_one({"_id": "schema"}, {"$set": {"version": 0}}))
    assert run(MIG.ausfuehren_oder_warten(db, warte_sekunden=1)) == "leader"
    assert run(MIG.aktuelle_version(db)) == MIG.ZIEL_VERSION
    assert run(MIG._sperre_gehalten(db)) is False
    src = inspect.getsource(MIG.ausfuehren_oder_warten)
    assert "_heartbeat(" in src and "_sperre_gehalten(" in src


# ============================================================ L4-13
def test_l4_13_verkaufspaket_stand_pruefung(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    echt = {"tier": "s5", "valid_until": "2027-01-01T00:00:00+00:00",
            "period_start": "2026-09-01T00:00:00+00:00"}
    run(db.dealers.insert_one({"id": "d1", "company_name": "X", "sale_plan": dict(echt)}))
    admin = {"id": "sa", "dealer_id": "", "role": "admin", "is_super_admin": True}

    class _Dealers:
        def __init__(self, orig):
            self._orig = orig

        async def find_one(self, *a, **k):     # veralteter Stand (anderer Admin war schneller)
            return {"id": "d1", "sale_plan": {**echt, "valid_until": "2026-12-01T00:00:00+00:00"}}

        def __getattr__(self, n):
            return getattr(self._orig, n)

    class _Db:
        def __init__(self, orig):
            self._orig = orig
            self.dealers = _Dealers(orig.dealers)

        def __getattr__(self, n):
            return getattr(self._orig, n)

    monkeypatch.setattr(ADMIN, "db", _Db(db))
    with pytest.raises(HTTPException) as e:
        run(ADMIN.admin_set_sale_plan("d1", {"tier": "s5", "months": 1}, admin))
    assert e.value.status_code == 409
    assert run(db.dealers.find_one({"id": "d1"}))["sale_plan"] == echt, "nichts ueberschrieben"
    monkeypatch.setattr(ADMIN, "db", db)
    erg = run(ADMIN.admin_set_sale_plan("d1", {"tier": "s5", "months": 1}, admin))
    assert erg["ok"] and erg["sale_plan"]["valid_until"].startswith("2027-01-31")
    # Neuvergabe ohne Paket: Stand "kein Paket" passt ebenfalls
    run(db.dealers.insert_one({"id": "d2", "company_name": "Y"}))
    erg = run(ADMIN.admin_set_sale_plan("d2", {"tier": "s10"}, admin))
    assert erg["ok"] and erg["sale_plan"]["tier"] == "s10"


# ============================================================ L4-14
def test_l4_14_publish_prueft_paket_vor_dem_live_schalten():
    src = inspect.getsource(R.publish_listing)
    i = src.index("Liste 4 Nr. 14")
    j = src.index('"$set": {"status": "veroeffentlicht"')
    assert i < j
    assert "_kontingent_zurueckgeben()" in src[i:j] and "402" in src[i:j]
