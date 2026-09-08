# -*- coding: utf-8 -*-
"""Nachpruefung Runde 14 (09/2026), Gruppe "bestand" — routes/bestand.py.

  35  Akte: nur nicht-abgeloeste finale Protokolle, Feld superseded dabei
  39  PUT /vehicles/{id}/bestand nach verkauft/archiviert/geloescht -> 409
  40  POST /vehicles/{id}/apply-deviations nach Abschluss -> 409
  41  PUT /vehicles/manual/{id} nach Abschluss -> 409, sonst Audit-Eintrag
  46  apply-deviations nimmt den massgeblichen Bericht (abgeholter Termin)
  47  Akte: pickup_report vom abgeholten Termin, pickup_reports je Termin
  93  Bestandsliste: naives expires_at -> kein 500 (als UTC)
  94  Akte: naives expires_at -> kein 500 (als UTC)
  95  purchase_price Infinity/NaN -> 422, kein Dokument
  110 features: Einzelstring max. 120 Zeichen

Einheitentests laufen ohne Server. HTTP-Tests brauchen das Backend auf
TEST_BASE_URL mit dem NEUEN Code — sie laufen nur mit RUNDE14_HTTP=1.
"""
import asyncio
import os
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "autoschnell")

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
SUF = uuid.uuid4().hex[:8]
PW = "RundeVierzehn14!"
JETZT = datetime.now(timezone.utc)

AUTO = {"make_label": "VW", "model_label": "Golf", "mileage": 100000,
        "purchase_price": 20000}


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


# =============================================================== Einheitentests
def test_95_purchase_price_ohne_inf_nan():
    import pydantic
    from routes.bestand import ManualVehicleIn
    for schlecht in (float("inf"), float("-inf"), float("nan"), 1e400):
        with pytest.raises(pydantic.ValidationError):
            ManualVehicleIn(**{**AUTO, "purchase_price": schlecht})
    assert ManualVehicleIn(**{**AUTO, "purchase_price": 0}).purchase_price == 0
    assert ManualVehicleIn(**{**AUTO, "purchase_price": None}).purchase_price is None


def test_110_features_einzelstring_gedeckelt():
    import pydantic
    from routes.bestand import ManualVehicleIn
    with pytest.raises(pydantic.ValidationError):
        ManualVehicleIn(**{**AUTO, "features": ["x" * 121]})
    with pytest.raises(pydantic.ValidationError):
        ManualVehicleIn(**{**AUTO, "features": ["ok", "x" * 5_000_000]})
    ok = ManualVehicleIn(**{**AUTO, "features": ["x" * 120] * 80})
    assert len(ok.features) == 80
    with pytest.raises(pydantic.ValidationError):
        ManualVehicleIn(**{**AUTO, "features": ["a"] * 81})


def test_93_94_resttage_naiv_und_aware():
    from routes.bestand import _als_aware, _resttage
    now = datetime.now(timezone.utc)
    naiv = (now + timedelta(days=10)).replace(tzinfo=None).isoformat()
    aware = (now + timedelta(days=10)).isoformat()
    assert _resttage(naiv, now) in (9, 10)
    assert _resttage(aware, now) in (9, 10)
    assert _resttage(naiv, now) == _resttage(aware, now)
    # Abgelaufen -> 0, nie negativ
    assert _resttage((now - timedelta(days=3)).isoformat(), now) == 0
    assert _als_aware(datetime(2026, 10, 1, 12, 0)).tzinfo is timezone.utc
    with pytest.raises(ValueError):
        _resttage("kein datum", now)


def test_39_40_41_abgeschlossen_sperre():
    from fastapi import HTTPException
    from routes.bestand import _ABGESCHLOSSEN, _abgeschlossen_sperren
    assert set(_ABGESCHLOSSEN) == {"verkauft", "archiviert", "geloescht"}
    for lc in _ABGESCHLOSSEN:
        with pytest.raises(HTTPException) as exc:
            _abgeschlossen_sperren({"lifecycle": lc}, "zu")
        assert exc.value.status_code == 409
    # storniert kann laut lifecycle.py zurueck in den Zyklus -> offen
    for lc in ("bestand", "verkaufsentwurf", "verkaufsbereit", "storniert", None):
        _abgeschlossen_sperren({"lifecycle": lc}, "zu")
    _abgeschlossen_sperren({}, "zu")
    _abgeschlossen_sperren(None, "zu")


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *a, **k):
        return self

    async def to_list(self, n):
        return list(self._docs)[:n]


class _Coll:
    """Minimaler Ersatz fuer eine Motor-Collection (nur was die Routen brauchen)."""

    def __init__(self, docs=None):
        self.docs = docs or []
        self.updates = []

    async def find_one(self, query, projection=None, **k):
        for d in self.docs:
            if all(d.get(key) == val for key, val in query.items()
                   if not isinstance(val, dict)):
                return dict(d)
        return None

    def find(self, query=None, projection=None):
        return _Cursor([dict(d) for d in self.docs])

    async def update_one(self, query, update, **k):
        self.updates.append((query, update))
        # Runde 17 (Nr. 275): update_bestand prueft matched_count (CAS) —
        # die Attrappe liefert wie Motor ein Ergebnisobjekt.
        return types.SimpleNamespace(matched_count=1, modified_count=1)

    def aggregate(self, *a, **k):
        async def _leer():
            if False:
                yield None
        return _leer()


class _Db:
    def __init__(self, **colls):
        self._c = colls

    def __getattr__(self, name):
        return self._c.setdefault(name, _Coll())


def _mit_bericht_helfer(monkeypatch, bericht, aufrufe):
    """Stub fuer abholbericht.massgeblicher_bericht (Modul entsteht parallel)."""
    mod = types.ModuleType("abholbericht")

    async def massgeblicher_bericht(db, vehicle_id, dealer_id):
        aufrufe.append((vehicle_id, dealer_id))
        return bericht
    mod.massgeblicher_bericht = massgeblicher_bericht
    monkeypatch.setitem(sys.modules, "abholbericht", mod)


def test_46_apply_deviations_nutzt_massgeblichen_bericht(monkeypatch):
    import routes.bestand as rb
    bericht = {"id": "r-neu", "appointment_id": "t-neu", "mileage_at_pickup": 155000,
               "deviations": [{"id": "d1", "field": "mileage", "label": "km"},
                              {"id": "d2", "field": "damage", "label": "Kratzer",
                               "actual": "Tuer links"}]}
    aufrufe = []
    _mit_bericht_helfer(monkeypatch, bericht, aufrufe)
    fake = _Db(vehicles=_Coll([{"id": "v1", "dealer_id": "f1", "lifecycle": "bestand",
                                "data": {"mileage": 100000}}]),
               # Bewusst ein "alter" Bericht direkt in der Collection: er darf
               # NICHT mehr per find_one geladen werden.
               pickup_reports=_Coll([{"id": "r-alt", "vehicle_id": "v1",
                                      "dealer_id": "f1", "mileage_at_pickup": 123,
                                      "deviations": []}]))
    logs = []

    async def _log(*a, **k):
        logs.append((a, k))
    monkeypatch.setattr(rb, "db", fake)
    monkeypatch.setattr(rb, "log_activity", _log)
    user = {"id": "u1", "dealer_id": "f1", "role": "chef"}
    out = asyncio.run(rb.apply_deviations(
        "v1", rb.ApplyDeviationsIn(deviation_ids=["d1", "d2"]), user=user))
    assert aufrufe == [("v1", "f1")]
    assert [a for a in out["applied"] if a.get("feld") == "Kilometerstand"][0]["neu"] == 155000
    assert "Kratzer: Tuer links" in out["known_defects"]
    gesetzt = fake.vehicles.updates[-1][1]["$set"]
    assert gesetzt["data"]["mileage"] == 155000
    assert logs and logs[0][1]["meta"]["bericht"] == "r-neu"


def test_40_apply_deviations_nach_verkauf_409(monkeypatch):
    from fastapi import HTTPException
    import routes.bestand as rb
    aufrufe = []
    _mit_bericht_helfer(monkeypatch, {"id": "r", "deviations": []}, aufrufe)
    fake = _Db(vehicles=_Coll([{"id": "v1", "dealer_id": "f1", "lifecycle": "verkauft",
                                "data": {"mileage": 100000}}]))
    monkeypatch.setattr(rb, "db", fake)
    user = {"id": "u1", "dealer_id": "f1", "role": "chef"}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(rb.apply_deviations(
            "v1", rb.ApplyDeviationsIn(deviation_ids=["d1"]), user=user))
    assert exc.value.status_code == 409
    assert fake.vehicles.updates == []
    assert aufrufe == []  # Sperre greift vor dem Bericht-Laden


def test_39_update_bestand_nach_verkauf_409(monkeypatch):
    from fastapi import HTTPException
    import routes.bestand as rb
    for lc in ("verkauft", "archiviert", "geloescht"):
        fake = _Db(vehicles=_Coll([{"id": "v1", "dealer_id": "f1", "lifecycle": lc,
                                    "bestand": {"location": "ALT"}}]))
        monkeypatch.setattr(rb, "db", fake)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(rb.update_bestand(
                "v1", rb.BestandUpdateIn(location="NEU"), user={"id": "u", "dealer_id": "f1"}))
        assert exc.value.status_code == 409
        assert fake.vehicles.updates == []
    fake = _Db(vehicles=_Coll([{"id": "v1", "dealer_id": "f1", "lifecycle": "bestand",
                                "bestand": {"location": "ALT"}}]))
    monkeypatch.setattr(rb, "db", fake)
    out = asyncio.run(rb.update_bestand(
        "v1", rb.BestandUpdateIn(location="NEU"), user={"id": "u", "dealer_id": "f1"}))
    assert out["bestand"]["location"] == "NEU" and len(fake.vehicles.updates) == 1


def test_41_update_manual_nach_verkauf_409_sonst_audit(monkeypatch):
    from fastapi import HTTPException
    import routes.bestand as rb
    logs = []

    async def _log(*a, **k):
        logs.append((a, k))
    monkeypatch.setattr(rb, "log_activity", _log)
    body = rb.ManualVehicleIn(**{**AUTO, "purchase_price": 15000})
    user = {"id": "u", "dealer_id": "f1"}
    fake = _Db(vehicles=_Coll([{"id": "m1", "dealer_id": "f1", "source": "manuell",
                                "lifecycle": "verkauft", "purchase_price": 20000}]))
    monkeypatch.setattr(rb, "db", fake)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(rb.update_manual_vehicle("m1", body, user=user))
    assert exc.value.status_code == 409 and fake.vehicles.updates == [] and logs == []

    fake = _Db(vehicles=_Coll([{"id": "m1", "dealer_id": "f1", "source": "manuell",
                                "lifecycle": "bestand", "purchase_price": 20000}]))
    monkeypatch.setattr(rb, "db", fake)
    assert asyncio.run(rb.update_manual_vehicle("m1", body, user=user)) == {"ok": True}
    assert fake.vehicles.updates[-1][1]["$set"]["purchase_price"] == 15000
    assert logs[0][0][2] == "fahrzeug.manuell.geaendert"
    assert logs[0][1]["meta"]["einkaufspreis_alt"] == 20000
    assert logs[0][1]["meta"]["einkaufspreis_neu"] == 15000


def test_35_47_94_akte_ohne_server(monkeypatch):
    import routes.bestand as rb
    import routes.contracts  # noqa: F401  (Import in der Funktion)
    massg = {"id": "r-b", "appointment_id": "t-b", "mileage_at_pickup": 222222}
    aufrufe = []
    _mit_bericht_helfer(monkeypatch, massg, aufrufe)
    naiv = (JETZT + timedelta(days=20)).replace(tzinfo=None).isoformat()
    fake = _Db(
        vehicles=_Coll([{"id": "v1", "dealer_id": "f1", "lifecycle": "bestand",
                         "bestand": {"expires_at": naiv}}]),
        pickup_reports=_Coll([
            {"id": "r-a", "appointment_id": "t-a", "status": "bestaetigt",
             "created_at": "2026-01-01T00:00:00+00:00", "superseded": False},
            {"id": "r-b", "appointment_id": "t-b", "status": "bestaetigt",
             "created_at": "2026-02-01T00:00:00+00:00", "superseded": False}]),
        pickup_protocols=_Coll([{"id": "p2", "version": 2, "superseded": False}]),
    )
    monkeypatch.setattr(rb, "db", fake)
    user = {"id": "u", "dealer_id": "f1", "role": "chef"}
    akte = asyncio.run(rb.vehicle_akte("v1", user=user))
    assert aufrufe == [("v1", "f1")]
    assert akte["pickup_report"]["appointment_id"] == "t-b"
    flags = {r["id"]: r["massgeblich"] for r in akte["pickup_reports"]}
    assert flags == {"r-a": False, "r-b": True}
    assert akte["retention_days_left"] in (19, 20)  # naiv, kein TypeError
    assert akte["protocols"][0]["superseded"] is False


def test_35_akte_filtert_abgeloeste_protokolle():
    import inspect
    import routes.bestand as rb
    quelle = inspect.getsource(rb.vehicle_akte)
    kopf = quelle[quelle.index("db.pickup_protocols.find("):]
    kopf = kopf[:kopf.index(".to_list(")]
    assert '"superseded": {"$ne": True}' in kopf
    assert '"superseded": 1' in kopf


# =============================================================== HTTP (nach Neustart)
@pytest.fixture(scope="module")
def firma():
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    r = requests.post(f"{API}/auth/register", json={
        "email": f"r14bestand_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": "R14 Bestand GmbH", "contact_person": "B T",
        "phone": "0511 14"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    me = requests.get(f"{API}/auth/me", headers=h, timeout=30).json()["user"]
    _db().subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": me["dealer_id"],
        "subject_user_id": me["id"], "plan": "monthly", "status": "active",
        "expires_at": (JETZT + timedelta(days=1)).isoformat(),
        "created_at": JETZT.isoformat()})
    yield {"h": h, "me": me, "dealer_id": me["dealer_id"]}
    dbx = _db()
    for c in ("subscriptions", "vehicles", "appointments", "pickup_reports",
              "pickup_protocols", "activity_logs", "resale_listings"):
        dbx[c].delete_many({"dealer_id": me["dealer_id"]})
    dbx.users.delete_many({"id": me["id"]})
    dbx.dealers.delete_many({"id": me["dealer_id"]})


def _neues_auto(firma, **extra):
    r = requests.post(f"{API}/vehicles/manual", headers=firma["h"],
                      json={**AUTO, **extra}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    return r.json()["id"]


def _lifecycle(firma, vid, lc):
    _db().vehicles.update_one({"id": vid, "dealer_id": firma["dealer_id"]},
                              {"$set": {"lifecycle": lc}})


def _auto(firma, vid):
    return _db().vehicles.find_one({"id": vid, "dealer_id": firma["dealer_id"]}, {"_id": 0})


def _termin(firma, vid, status, created_at, status_changed_at=None):
    tid = str(uuid.uuid4())
    doc = {"id": tid, "dealer_id": firma["dealer_id"], "vehicle_id": vid,
           "title": "Abholen", "status": status, "created_by": firma["me"]["id"],
           "created_at": created_at, "updated_at": created_at}
    if status_changed_at:
        doc["status_changed_at"] = status_changed_at
    _db().appointments.insert_one(doc)
    return tid


def _bericht(firma, vid, tid, km, created_at, devs=None):
    rid = str(uuid.uuid4())
    _db().pickup_reports.insert_one({
        "id": rid, "appointment_id": tid, "vehicle_id": vid,
        "dealer_id": firma["dealer_id"], "driver_account_id": "d", "driver_name": "Fahrer",
        "mileage_at_pickup": km, "keys_count": 2, "fuel_level": "1/2",
        "deviations": devs or [], "notes": "", "version": 1, "replaces_id": None,
        "superseded": False, "status": "bestaetigt", "created_at": created_at})
    return rid


def test_http_39_bestand_put_nach_abschluss_409(firma):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    for lc in ("verkauft", "archiviert", "geloescht"):
        vid = _neues_auto(firma)
        r = requests.put(f"{API}/vehicles/{vid}/bestand", headers=firma["h"],
                         json={"location": "ALT"}, timeout=30)
        assert r.status_code == 200, r.text[:200]
        _lifecycle(firma, vid, lc)
        r = requests.put(f"{API}/vehicles/{vid}/bestand", headers=firma["h"],
                         json={"location": "NEU", "costs": [{"label": "x", "amount": 9999}]},
                         timeout=30)
        assert r.status_code == 409, (lc, r.text[:200])
        b = _auto(firma, vid)["bestand"]
        assert b.get("location") == "ALT" and not b.get("costs")


def test_http_40_apply_deviations_nach_verkauf_409(firma):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    vid = _neues_auto(firma)
    tid = _termin(firma, vid, "abgeholt", JETZT.isoformat(), JETZT.isoformat())
    _bericht(firma, vid, tid, 155000, JETZT.isoformat(),
             [{"id": "d1", "field": "mileage", "label": "km"}])
    _lifecycle(firma, vid, "verkauft")
    r = requests.post(f"{API}/vehicles/{vid}/apply-deviations", headers=firma["h"],
                      json={"deviation_ids": ["d1"]}, timeout=30)
    assert r.status_code == 409, r.text[:200]
    assert _auto(firma, vid)["data"]["mileage"] == 100000


def test_http_41_manual_put_nach_verkauf_409_sonst_audit(firma):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    vid = _neues_auto(firma)
    r = requests.put(f"{API}/vehicles/manual/{vid}", headers=firma["h"],
                     json={**AUTO, "purchase_price": 18000}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert _auto(firma, vid)["purchase_price"] == 18000
    log = _db().activity_logs.find_one(
        {"dealer_id": firma["dealer_id"], "ref": vid, "action": "fahrzeug.manuell.geaendert"},
        {"_id": 0})
    assert log and log["meta"]["einkaufspreis_alt"] == 20000
    assert log["meta"]["einkaufspreis_neu"] == 18000
    _lifecycle(firma, vid, "verkauft")
    r = requests.put(f"{API}/vehicles/manual/{vid}", headers=firma["h"],
                     json={**AUTO, "purchase_price": 15000}, timeout=30)
    assert r.status_code == 409, r.text[:200]
    assert _auto(firma, vid)["purchase_price"] == 18000


def _zwei_termine(firma):
    """Alter offener Termin (km 123, zuerst eingefuegt) + neuer abgeholter (km 222222)."""
    vid = _neues_auto(firma)
    alt = (JETZT - timedelta(days=10)).isoformat()
    neu = (JETZT - timedelta(days=1)).isoformat()
    t_alt = _termin(firma, vid, "offen", alt)
    t_neu = _termin(firma, vid, "abgeholt", neu, neu)
    r_alt = _bericht(firma, vid, t_alt, 123, alt,
                     [{"id": "alt-1", "field": "mileage", "label": "km"}])
    r_neu = _bericht(firma, vid, t_neu, 222222, neu,
                     [{"id": "neu-1", "field": "mileage", "label": "km"},
                      {"id": "neu-2", "field": "damage", "label": "Kratzer",
                       "actual": "Tuer links"}])
    return vid, t_alt, t_neu, r_alt, r_neu


def test_http_46_apply_deviations_vom_abgeholten_termin(firma):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    vid, _, _, _, _ = _zwei_termine(firma)
    r = requests.post(f"{API}/vehicles/{vid}/apply-deviations", headers=firma["h"],
                      json={"deviation_ids": ["neu-1", "neu-2"]}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    assert len(r.json()["applied"]) == 2
    assert _auto(firma, vid)["data"]["mileage"] == 222222
    assert "Kratzer: Tuer links" in _auto(firma, vid)["known_defects"]
    # IDs des alten Termins sind im massgeblichen Bericht unbekannt -> nichts
    r = requests.post(f"{API}/vehicles/{vid}/apply-deviations", headers=firma["h"],
                      json={"deviation_ids": ["alt-1"]}, timeout=30)
    assert r.status_code == 200 and r.json()["applied"] == []
    assert _auto(firma, vid)["data"]["mileage"] == 222222


def test_http_47_akte_bericht_des_abgeholten_termins(firma):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    vid, t_alt, t_neu, r_alt, r_neu = _zwei_termine(firma)
    r = requests.get(f"{API}/vehicles/{vid}/akte", headers=firma["h"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    akte = r.json()
    assert akte["pickup_report"]["appointment_id"] == t_neu
    assert akte["pickup_report"]["mileage_at_pickup"] == 222222
    je_termin = {b["appointment_id"]: b for b in akte["pickup_reports"]}
    assert set(je_termin) == {t_alt, t_neu}
    assert je_termin[t_neu]["massgeblich"] is True and je_termin[t_neu]["id"] == r_neu
    assert je_termin[t_alt]["massgeblich"] is False
    for b in akte["pickup_reports"]:
        assert {"status", "created_at", "superseded"} <= set(b)


def test_http_35_akte_protokolle_nur_aktuelle_mit_superseded(firma):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    vid = _neues_auto(firma)
    basis = {"vehicle_id": vid, "dealer_id": firma["dealer_id"], "status": "final",
             "driver_name": "F", "seller_name": "V", "place": "Hannover",
             "finalized_at": JETZT.isoformat()}
    _db().pickup_protocols.insert_many([
        {**basis, "id": f"p1-{SUF}", "version": 1, "superseded": True,
         "superseded_at": JETZT.isoformat()},
        {**basis, "id": f"p2-{SUF}", "version": 2, "corrects_version": 1,
         "superseded": False},
    ])
    r = requests.get(f"{API}/vehicles/{vid}/akte", headers=firma["h"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    prot = r.json()["protocols"]
    assert [p["version"] for p in prot] == [2]
    assert prot[0]["superseded"] is False and prot[0]["corrects_version"] == 1


def test_http_93_94_naives_expires_at_kein_500(firma):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    vid = _neues_auto(firma)
    naiv = (JETZT + timedelta(days=20)).replace(tzinfo=None).isoformat()
    assert "+" not in naiv
    _db().vehicles.update_one({"id": vid}, {"$set": {"bestand.expires_at": naiv}})
    r = requests.get(f"{API}/bestand", headers=firma["h"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    it = [i for i in r.json()["items"] if i["id"] == vid][0]
    assert it.get("retention_days_left") in (19, 20)
    r = requests.get(f"{API}/vehicles/{vid}/akte", headers=firma["h"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    assert r.json()["retention_days_left"] in (19, 20)


def test_http_95_purchase_price_infinity_422(firma):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    vorher = _db().vehicles.count_documents({"dealer_id": firma["dealer_id"]})
    for roh in ('{"make_label":"VW","model_label":"Golf","purchase_price":Infinity}',
                '{"make_label":"VW","model_label":"Golf","purchase_price":NaN}',
                '{"make_label":"VW","model_label":"Golf","purchase_price":1e400}'):
        r = requests.post(f"{API}/vehicles/manual", headers={
            **firma["h"], "Content-Type": "application/json"}, data=roh, timeout=30)
        assert r.status_code == 422, (roh, r.status_code, r.text[:200])
    assert _db().vehicles.count_documents({"dealer_id": firma["dealer_id"]}) == vorher
    vid = _neues_auto(firma)
    r = requests.put(f"{API}/vehicles/manual/{vid}", headers={
        **firma["h"], "Content-Type": "application/json"},
        data='{"make_label":"VW","model_label":"Golf","purchase_price":Infinity}', timeout=30)
    assert r.status_code == 422, r.text[:200]
    assert _auto(firma, vid)["purchase_price"] == 20000
    assert requests.get(f"{API}/bestand", headers=firma["h"], timeout=30).status_code == 200


def test_http_110_features_einzelstring(firma):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    r = requests.post(f"{API}/vehicles/manual", headers=firma["h"],
                      json={**AUTO, "features": ["x" * 121]}, timeout=30)
    assert r.status_code == 422, r.text[:200]
    vid = _neues_auto(firma, features=["x" * 120] * 80)
    assert len(_auto(firma, vid)["data"]["features"]) == 80
