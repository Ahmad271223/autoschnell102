# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026 (590 Befunde), Reparaturwelle A5 (22.09.2026):
Protokolle, Termine, Fahrer, Bestand, Konten, Indizes.

  R1-21  Feldnamen im Protokoll: kein "$"-Praefix, kein NUL
  V-23 / U-164  Freigabeliste: geschlossene Termine schon in der Abfrage weg,
         aufsteigend nach Wartebeginn, Kappung als (paare, abgeschnitten)
         und Kopfzeile X-Truncated
  R1-22  Protokollliste/Akte: appointment_id + Abholdatum je Version
  V-30   PUT /appointments ohne stand: gelesener Stand gilt fuer JEDE Aenderung
  R1-09  _offener_termin_zum_fahrzeug entfernt
  R1-26  aktiv = active is True (Firma, Fahrer-Login, Fahrer-Zuweisung)
  R1-28  current_driver(creds, response)
  R1-25  mfa nutzt auth.JWT_SECRET, kein "dev-secret"
  R1-32  DuplicateKeyError beim Vormerken = vorgemerkt, kein Alarm
  R2-12  apply_deviations haengt Maengel per $addToSet an
  U-122  Leereintraege in known_defects (Inserat) verworfen
  P-37   Abholbericht: abgeholter Termin auch jenseits der Ladegrenze
  R1-37  keine toten Super-Admin-Zweige in admin.py
  R1-16  Firmen-Abo: ohne Feld und null gleichrangig, juengstes gilt
  AL-19  _termin_unique_index(abbruch=False) aus dem Handler, "dubletten"

In-Prozess wie test_befunde_runde17_termine.py (Welt-Fixture, nur Mongo).
"""
import asyncio
import inspect
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
WURZEL = Path(__file__).resolve().parents[2]

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"


def _jetzt(delta_s: int = 0):
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


def _module(name):
    import importlib
    return importlib.import_module(name)


class _Welt:
    def __init__(self):
        s = uuid.uuid4().hex[:10]
        self.s = s
        self.dealer_id = f"d_a5_{s}"
        self.chef = {"id": f"chef_a5_{s}", "dealer_id": self.dealer_id, "role": "dealer"}
        self.sucher = {"id": f"su_a5_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
        self.driver_id = f"f_a5_{s}"

    def konten(self):
        return [
            {"id": self.chef["id"], "dealer_id": self.dealer_id, "role": "dealer", "active": True,
             "email": f"{self.chef['id']}@e2etest-mail.de", "created_at": _jetzt()},
            {"id": self.sucher["id"], "dealer_id": self.dealer_id, "role": "sucher", "active": True,
             "first_name": "Susi", "last_name": "S", "email": f"{self.sucher['id']}@e2etest-mail.de",
             "created_at": _jetzt()},
        ]

    def fahrzeug(self, vid, lifecycle="abholung_geplant", **extra):
        doc = {"id": vid, "dealer_id": self.dealer_id, "lifecycle": lifecycle,
               "mobile_ad_id": vid[2:], "data": {"make_label": "BMW", "model_label": "320d"},
               "status": lifecycle, "created_at": _jetzt(), "updated_at": _jetzt()}
        doc.update(extra)
        return doc

    def appt(self, aid, **extra):
        doc = {"id": aid, "dealer_id": self.dealer_id, "title": f"Fahrt {aid}", "status": "offen",
               "pickup_date": "2099-09-10", "pickup_time": "10:00", "pickup_address": "Teststr. 1",
               "seller_name": "Verkaeufer", "created_by": self.chef["id"], "created_at": _jetzt(),
               "updated_at": _jetzt()}
        doc.update(extra)
        return doc

    def protokoll(self, P, aid, pid, **extra):
        doc = {"id": pid, "appointment_id": aid, "dealer_id": self.dealer_id,
               "driver_account_id": self.driver_id, "driver_name": "Fahrer A5",
               "version": 1, "status": P.ZUR_FREIGABE, "superseded": False,
               "vehicle_check": {}, "condition": {"mileage": "123456"}, "keys_count": "2",
               "damages_confirmed": True, "place": "Hannover",
               "abgeschickt_am": _jetzt(), "erstmals_abgeschickt_am": _jetzt(),
               "created_at": _jetzt()}
        doc.update(extra)
        return doc

    async def aufraeumen(self, db):
        for c in ("appointments", "vehicles", "activity_logs", "generated_pdfs", "users",
                  "pickup_reports", "pickup_protocols", "dealer_drivers", "resale_listings",
                  "subscriptions", "kaufvorgaenge"):
            await db[c].delete_many({"dealer_id": {"$regex": f"_a5_{self.s}"}})
        await db.dealers.delete_many({"id": {"$regex": f"_a5_{self.s}"}})
        await db.driver_accounts.delete_many({"id": {"$regex": f"_a5_{self.s}"}})
        await db.betriebsalarme.delete_many({"ref": {"$regex": self.s}})


async def _indizes(db):
    """Dieselben Unique-Indizes wie server.py — die CI-Datenbank ist frisch."""
    from deps import TERMIN_OFFEN
    versuche = [
        lambda: db.appointments.create_index(
            [("dealer_id", 1), ("contract_id", 1)], unique=True, name="termin_offen_je_vertrag",
            partialFilterExpression={"contract_id": {"$type": "string", "$gt": ""},
                                     "status": {"$in": list(TERMIN_OFFEN)}}),
        lambda: db.pickup_protocols.create_index(
            "appointment_id", unique=True, partialFilterExpression={"superseded": False},
            name="ein_aktuelles_protokoll_je_termin"),
        lambda: db.pickup_protocols.create_index(
            [("appointment_id", 1), ("version", 1)], unique=True, name="protokollversion_eindeutig"),
    ]
    for v in versuche:
        try:
            await v()
        except Exception:  # noqa: BLE001  (Index existiert mit anderer Definition)
            pass


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    w = _Welt()
    names = ["deps", "routes.appointments", "routes.contracts", "routes.protocols",
             "routes.drivers", "routes.bestand", "routes.resale", "routes.admin",
             "lifecycle", "kaufvorgang", "indizes"]
    mods = [_module(n) for n in names]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _Ctx:
        def __init__(self):
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
            self.db = self.client[DB_NAME]
            for m in mods:
                if hasattr(m, "db"):
                    m.db = self.db
            self.w = w
            self.s, self.dealer_id, self.chef, self.sucher = w.s, w.dealer_id, w.chef, w.sucher
            self.driver_id = w.driver_id

        def run(self, coro):
            return self.loop.run_until_complete(coro)

    ctx = _Ctx()
    ctx.run(_indizes(ctx.db))
    ctx.run(ctx.db.users.insert_many(w.konten()))
    ctx.run(ctx.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                       "company_name": "A5 GmbH", "created_at": _jetzt()}))
    yield ctx
    try:
        ctx.run(w.aufraeumen(ctx.db))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        ctx.client.close()
        ctx.loop.close()


class _Coll:
    """Motor-Sammlung mit Haken auf find_one (Rennen zwischen Lesen und Schreiben)."""
    def __init__(self, echt, haken):
        self._echt, self._haken = echt, haken

    def __getattr__(self, name):
        return getattr(self._echt, name)

    async def find_one(self, *a, **k):
        doc = await self._echt.find_one(*a, **k)
        await self._haken(self._echt, doc)
        return doc


class _Db:
    def __init__(self, echt, sammlung, haken):
        self._echt, self._sammlung, self._haken = echt, sammlung, haken

    def __getattr__(self, name):
        if name == self._sammlung:
            return _Coll(getattr(self._echt, name), self._haken)
        return getattr(self._echt, name)


# ================================================================ R1-21
def test_r1_21_feldnamen_ohne_dollar_und_nul():
    P = _module("routes.protocols")
    P.ProtocolIn(vehicle_check={"2.0 TDI": True, "make": {"status": "stimmt"}})  # Punkte bleiben
    for kaputt in ({"$where": "x"}, {"a\x00b": 1}, {"make": {"$status": "x"}},
                   {"make": {"na\x00me": "x"}}):
        with pytest.raises(ValidationError):
            P.ProtocolIn(vehicle_check=kaputt)
    with pytest.raises(ValidationError):
        P.ProtocolIn(features={"$gt": True})


# ================================================================ V-23 / U-164
def test_v23_u164_freigabeliste_filtert_in_der_abfrage_und_meldet_kappung(welt, monkeypatch):
    w, P = welt, _module("routes.protocols")
    db, run = w.db, w.run
    a_alt, a_neu, a_zu, a_fremd = (f"a_{n}_a5_{w.s}" for n in ("alt", "neu", "zu", "fremd"))
    run(db.appointments.insert_many([
        w.w.appt(a_alt), w.w.appt(a_neu), w.w.appt(a_zu, status="storniert"),
        # Termin einer ANDEREN Firma mit Protokoll unserer Firma: faellt weg
        w.w.appt(a_fremd, dealer_id=f"d_fremd_a5_{w.s}")]))
    run(db.pickup_protocols.insert_many([
        w.w.protokoll(P, a_neu, f"p_neu_a5_{w.s}", erstmals_abgeschickt_am="2026-09-12T10:00:00+00:00",
                      abgeschickt_am="2026-09-12T10:00:00+00:00"),
        w.w.protokoll(P, a_alt, f"p_alt_a5_{w.s}", erstmals_abgeschickt_am="2026-09-01T08:00:00+00:00",
                      abgeschickt_am="2026-09-13T08:00:00+00:00"),   # spaeter erneut abgeschickt
        w.w.protokoll(P, a_zu, f"p_zu_a5_{w.s}", erstmals_abgeschickt_am="2026-08-01T08:00:00+00:00"),
        w.w.protokoll(P, a_fremd, f"p_fremd_a5_{w.s}", erstmals_abgeschickt_am="2026-08-02T08:00:00+00:00"),
        w.w.protokoll(P, f"a_weg_a5_{w.s}", f"p_weg_a5_{w.s}",
                      erstmals_abgeschickt_am="2026-08-03T08:00:00+00:00"),   # Termin geloescht
    ]))
    paare, abgeschnitten = run(P._wartende_protokolle(w.chef))
    assert abgeschnitten is False
    # am laengsten wartend zuerst (erstmals_abgeschickt_am), Leichen weg
    assert [d["id"] for d, _a in paare] == [f"p_alt_a5_{w.s}", f"p_neu_a5_{w.s}"]
    assert [a["id"] for _d, a in paare] == [a_alt, a_neu]
    assert all("_termin" not in d and "_wartet_seit" not in d for d, _a in paare)
    # Zaehler-Projektion: nur die schmalen Felder
    paare2, _ = run(P._wartende_protokolle(w.chef, P._ZAEHLER_FELDER))
    assert paare2 and "condition" not in paare2[0][0] and paare2[0][0]["id"] == f"p_alt_a5_{w.s}"
    assert "_id" not in paare2[0][1]

    alarme = []

    async def _alarm(dbx, code, ref=None, meta=None, **kw):
        alarme.append((code, ref))
    monkeypatch.setattr(P.betrieb, "alarm", _alarm)
    monkeypatch.setattr(P, "_FREIGABE_MAX", 1)
    resp = Response()
    zahl = run(P.protokolle_zur_freigabe_anzahl(w.chef, resp))
    assert resp.headers.get("X-Truncated") == "1" and zahl["abgeschnitten"] is True
    # gekappt wird das NEUESTE, das am laengsten Wartende bleibt
    assert zahl["wartet"] == 1 and zahl["ids"] == [f"p_alt_a5_{w.s}"]
    assert alarme and alarme[0] == ("freigaben_liste_abgeschnitten", w.dealer_id)
    resp2 = Response()
    liste = run(P.protokolle_zur_freigabe(w.chef, resp2))
    assert resp2.headers.get("X-Truncated") == "1" and [e["protocol_id"] for e in liste] == [f"p_alt_a5_{w.s}"]
    monkeypatch.setattr(P, "_FREIGABE_MAX", 5000)
    resp3 = Response()
    zahl = run(P.protokolle_zur_freigabe_anzahl(w.chef, resp3))
    assert "X-Truncated" not in resp3.headers and zahl["wartet"] == 2 and zahl["abgeschnitten"] is False
    q = inspect.getsource(P._wartende_protokolle)
    assert "$lookup" in q and "_FREIGABE_MAX" in q


# ================================================================ R1-22
def test_r1_22_protokolle_tragen_termin_und_abholdatum(welt):
    w, P, B = welt, _module("routes.protocols"), _module("routes.bestand")
    db, run = w.db, w.run
    vid = f"v_a5_{w.s}"
    a1, a2 = f"a1_a5_{w.s}", f"a2_a5_{w.s}"
    run(db.vehicles.insert_one(w.w.fahrzeug(vid)))
    run(db.appointments.insert_many([
        w.w.appt(a1, vehicle_id=vid, status="abgeholt", pickup_date="2026-09-01", pickup_time="09:00"),
        w.w.appt(a2, vehicle_id=vid, status="abgeholt", pickup_date="2026-09-15", pickup_time="14:30")]))
    run(db.pickup_protocols.insert_many([
        w.w.protokoll(P, a1, f"p1_a5_{w.s}", status="final", vehicle_id=vid,
                      finalized_at="2026-09-01T10:00:00+00:00"),
        w.w.protokoll(P, a2, f"p2_a5_{w.s}", status="final", vehicle_id=vid,
                      finalized_at="2026-09-15T15:00:00+00:00")]))
    liste = run(P.dealer_list_protocols(vid, w.chef, Response()))
    assert [(p["version"], p["appointment_id"], p["pickup_date"], p["pickup_time"]) for p in liste] == [
        (1, a2, "2026-09-15", "14:30"), (1, a1, "2026-09-01", "09:00")]
    akte = run(B.vehicle_akte(vid, user=w.chef))
    assert [(p["appointment_id"], p["pickup_date"]) for p in akte["protocols"]] == [
        (a2, "2026-09-15"), (a1, "2026-09-01")]
    # Akte-Anzeige (Team A2: FahrzeugAkte.jsx) nennt das Abholdatum
    jsx = (WURZEL / "frontend" / "src" / "pages" / "app" / "FahrzeugAkte.jsx").read_text(encoding="utf-8")
    assert "Abholung vom" in jsx and "p.pickup_date" in jsx


# ================================================================ V-30
def test_v30_put_ohne_stand_laeuft_gegen_den_gelesenen_stand(welt, monkeypatch):
    w, A = welt, _module("routes.appointments")
    db, run = w.db, w.run
    aid = f"a_v30_a5_{w.s}"
    run(db.appointments.insert_one(w.w.appt(aid, notes="alt", updated_at="2026-09-20T10:00:00+00:00")))
    zustand = {"gebumpt": False}

    async def _kollege_dazwischen(echt, doc):
        # GENAU zwischen Lesen und Schreiben aendert ein Kollege Notizen + Stand
        if doc and doc.get("id") == aid and not zustand["gebumpt"]:
            zustand["gebumpt"] = True
            await echt.update_one({"id": aid}, {"$set": {"notes": "Kollege",
                                                          "updated_at": "2026-09-20T10:00:01+00:00"}})
    monkeypatch.setattr(A, "db", _Db(db, "appointments", _kollege_dazwischen))
    with pytest.raises(HTTPException) as e:
        run(A.update_appointment(aid, A.AppointmentIn(notes="Chef"), w.chef))
    assert e.value.status_code == 409 and e.value.detail == A.TERMIN_VERALTET_HINWEIS
    assert run(db.appointments.find_one({"id": aid}))["notes"] == "Kollege"
    # ohne Rennen: Notizen ohne stand weiter erlaubt (Titel/Notizen/Kosten
    # brauchen keinen Client-Stand — nur den gelesenen)
    monkeypatch.setattr(A, "db", db)
    run(A.update_appointment(aid, A.AppointmentIn(notes="Chef"), w.chef))
    assert run(db.appointments.find_one({"id": aid}))["notes"] == "Chef"
    q = inspect.getsource(A.update_appointment)
    assert 'if not stand and existing.get("updated_at"):' in q


# ================================================================ R1-09 / R1-28 / R1-37
def test_r1_09_28_37_toter_code_entfernt():
    A = _module("routes.appointments")
    D = _module("routes.drivers")
    assert not hasattr(A, "_offener_termin_zum_fahrzeug")
    assert list(inspect.signature(D.current_driver).parameters) == ["creds", "response"]
    q = (WURZEL / "backend" / "routes" / "admin.py").read_text(encoding="utf-8")
    assert 'not admin.get("is_super_admin")' not in q
    assert q.count("Super-Admin durch Dependency") >= 5 and q.count("R1-37") >= 5


# ================================================================ R1-26
def test_r1_26_aktiv_heisst_active_is_true(welt):
    w, deps, D, A = welt, _module("deps"), _module("routes.drivers"), _module("routes.appointments")
    db, run = w.db, w.run
    # Firma: Chef ohne active-Feld -> gesperrt (fail-closed), True -> offen
    did2, chef2 = f"d2_a5_{w.s}", f"chef2_a5_{w.s}"
    run(db.dealers.insert_one({"id": did2, "user_id": chef2, "company_name": "Ohne", "created_at": _jetzt()}))
    run(db.users.insert_one({"id": chef2, "dealer_id": did2, "role": "dealer", "created_at": _jetzt()}))
    assert run(deps.firma_gesperrt(did2)) is True and did2 in run(deps.gesperrte_firmen_ids())
    run(db.users.update_one({"id": chef2}, {"$set": {"active": True}}))
    assert run(deps.firma_gesperrt(did2)) is False and did2 not in run(deps.gesperrte_firmen_ids())
    assert run(deps.firma_gesperrt(w.dealer_id)) is False
    # Fahrer-Login: Konto ohne active -> 401
    sid = f"sid-{w.s}"
    run(db.driver_accounts.insert_one({"id": w.driver_id, "current_session_id": sid,
                                       "driver_code": f"A5{w.s[:6].upper()}", "password_hash": "x",
                                       "created_at": _jetzt()}))
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=D.create_driver_token(w.driver_id, sid))
    with pytest.raises(HTTPException) as e:
        run(D.current_driver(creds))
    assert e.value.status_code == 401 and "deaktiviert" in e.value.detail
    run(db.driver_accounts.update_one({"id": w.driver_id}, {"$set": {"active": True}}))
    assert run(D.current_driver(creds))["id"] == w.driver_id
    # Fahrer-Zuweisung: verknuepft, aber Konto ohne active -> 400
    run(db.dealer_drivers.insert_one({"id": str(uuid.uuid4()), "dealer_id": w.dealer_id,
                                      "driver_account_id": w.driver_id, "added_at": _jetzt()}))
    run(db.driver_accounts.update_one({"id": w.driver_id}, {"$unset": {"active": ""}}))
    with pytest.raises(HTTPException) as e:
        run(A._fahrer_pruefen(w.dealer_id, w.driver_id))
    assert e.value.status_code == 400
    run(db.driver_accounts.update_one({"id": w.driver_id}, {"$set": {"active": True}}))
    run(A._fahrer_pruefen(w.dealer_id, w.driver_id))
    for modul, name in ((D, "add_driver_by_code"), (D, "driver_login")):
        assert 'get("active", True)' not in inspect.getsource(getattr(modul, name))


# ================================================================ R1-25
def test_r1_25_mfa_nimmt_das_geheimnis_aus_auth():
    MFA, AUTH = _module("mfa"), _module("auth")
    q = inspect.getsource(MFA._schluessel)
    assert 'or "dev-secret"' not in q and "from auth import JWT_SECRET" in q
    assert MFA._schluessel()[-1] == AUTH.JWT_SECRET
    assert MFA.entschluesseln(MFA.verschluesseln("GEHEIM")) == "GEHEIM"


# ================================================================ R1-32
def test_r1_32_doppelte_vormerkung_ist_kein_alarm(monkeypatch):
    SS, BT = _module("storage_service"), _module("betrieb")

    async def kaputt(key):
        raise RuntimeError("Speicher nicht erreichbar (Test)")
    monkeypatch.setattr(SS, "delete_async", kaputt)
    alarme = []

    async def _alarm(dbx, code, **kw):
        alarme.append(code)
    monkeypatch.setattr(BT, "alarm", _alarm)

    class _Retry:
        def __init__(self, fehler):
            self.fehler = fehler

        async def update_one(self, *a, **k):
            raise self.fehler

    class _Fake:
        def __init__(self, fehler):
            self.storage_delete_retry = _Retry(fehler)

    ok = asyncio.run(SS.loeschen_oder_vormerken(_Fake(DuplicateKeyError("parallel vorgemerkt")),
                                                key=f"k/{uuid.uuid4().hex}.png", grund="test"))
    assert ok is False and alarme == [], "Dublette = schon vorgemerkt, kein Alarm"
    ok = asyncio.run(SS.loeschen_oder_vormerken(_Fake(RuntimeError("Mongo weg")),
                                                key=f"k/{uuid.uuid4().hex}.png", grund="test"))
    assert ok is False and alarme == ["datei_loeschung_nicht_vorgemerkt"]
    q = inspect.getsource(SS.loeschen_oder_vormerken)
    i = q.index("$setOnInsert")
    assert '"art"' not in q[i:i + 200] and '"key"' not in q[i:i + 200], \
        "Schluesselfelder stehen im Filter, nicht im $setOnInsert (Mongo wiederholt selbst)"
    assert q.index("except _DuplicateKeyError") < q.index("except Exception as exc2")


# ================================================================ R2-12
def test_r2_12_maengel_werden_angehaengt_nicht_ueberschrieben(welt, monkeypatch):
    w, B = welt, _module("routes.bestand")
    db, run = w.db, w.run
    vid, aid = f"v_r212_a5_{w.s}", f"a_r212_a5_{w.s}"
    run(db.vehicles.insert_one(w.w.fahrzeug(vid, known_defects=["alt"])))
    run(db.appointments.insert_one(w.w.appt(aid, vehicle_id=vid, status="abgeholt")))
    run(db.pickup_reports.insert_one({
        "id": f"r_r212_a5_{w.s}", "vehicle_id": vid, "dealer_id": w.dealer_id, "appointment_id": aid,
        "version": 1, "status": "eingereicht", "created_at": _jetzt(),
        "deviations": [{"id": "d1", "field": "lack", "label": "Kratzer", "actual": "links"}]}))
    zustand = {"gebumpt": False}

    async def _kollege_dazwischen(echt, doc):
        if doc and doc.get("id") == vid and not zustand["gebumpt"]:
            zustand["gebumpt"] = True
            await echt.update_one({"id": vid}, {"$addToSet": {"known_defects": "fremd"}})
    monkeypatch.setattr(B, "db", _Db(db, "vehicles", _kollege_dazwischen))
    out = run(B.apply_deviations(vid, B.ApplyDeviationsIn(deviation_ids=["d1"]), user=w.chef))
    assert out["known_defects"] == ["alt", "Kratzer: links"]
    v = run(db.vehicles.find_one({"id": vid}))
    assert v["known_defects"] == ["alt", "fremd", "Kratzer: links"], "paralleler Eintrag bleibt"
    assert v.get("deviations_applied_at")
    # zweite Uebernahme: nichts Neues -> Liste unveraendert
    monkeypatch.setattr(B, "db", db)
    run(B.apply_deviations(vid, B.ApplyDeviationsIn(deviation_ids=["d1"]), user=w.chef))
    assert run(db.vehicles.find_one({"id": vid}))["known_defects"] == ["alt", "fremd", "Kratzer: links"]
    # Feld fehlt: einmalig als Liste
    vid2 = f"v_r212b_a5_{w.s}"
    run(db.vehicles.insert_one(w.w.fahrzeug(vid2)))
    run(db.appointments.insert_one(w.w.appt(f"a_r212b_a5_{w.s}", vehicle_id=vid2, status="abgeholt")))
    run(db.pickup_reports.insert_one({
        "id": f"r_r212b_a5_{w.s}", "vehicle_id": vid2, "dealer_id": w.dealer_id,
        "appointment_id": f"a_r212b_a5_{w.s}", "version": 1, "status": "eingereicht",
        "created_at": _jetzt(), "deviations": [{"id": "d1", "field": "lack", "label": "Delle"}]}))
    run(B.apply_deviations(vid2, B.ApplyDeviationsIn(deviation_ids=["d1"]), user=w.chef))
    assert run(db.vehicles.find_one({"id": vid2}))["known_defects"] == ["Delle"]


# ================================================================ U-122
def test_u122_leere_maengel_werden_verworfen(welt, monkeypatch):
    w, R = welt, _module("routes.resale")
    db, run = w.db, w.run
    lid, vid = f"l_a5_{w.s}", f"v_u122_a5_{w.s}"
    run(db.vehicles.insert_one(w.w.fahrzeug(vid, lifecycle="verkaufsentwurf")))
    run(db.resale_listings.insert_one({"id": lid, "dealer_id": w.dealer_id, "vehicle_id": vid,
                                       "status": "entwurf", "prices": {}, "data": {},
                                       "created_at": _jetzt(), "updated_at": _jetzt()}))

    async def _roh(l):
        return l
    monkeypatch.setattr(R, "_editor_antwort", _roh)
    run(R.update_listing(lid, R.ListingUpdateIn(known_defects=["", "   ", "  Kratzer  ", "x" * 300]), w.chef))
    assert run(db.resale_listings.find_one({"id": lid}))["known_defects"] == ["Kratzer", "x" * 300]


# ================================================================ P-37
def test_p37_abgeholter_termin_auch_jenseits_der_ladegrenze(welt, monkeypatch):
    w, AB = welt, _module("abholbericht")
    db, run = w.db, w.run
    vid = f"v_p37_a5_{w.s}"
    a1, a2, a3 = (f"a{i}_p37_a5_{w.s}" for i in (1, 2, 3))
    run(db.appointments.insert_many([
        w.w.appt(a1, vehicle_id=vid), w.w.appt(a2, vehicle_id=vid),
        w.w.appt(a3, vehicle_id=vid, status="abgeholt", pickup_date="2026-09-01")]))
    run(db.pickup_reports.insert_many([
        {"id": f"r1_{w.s}", "vehicle_id": vid, "dealer_id": w.dealer_id, "appointment_id": a1,
         "version": 1, "created_at": "2026-09-10T10:00:00+00:00"},
        {"id": f"r2_{w.s}", "vehicle_id": vid, "dealer_id": w.dealer_id, "appointment_id": a2,
         "version": 1, "created_at": "2026-09-09T10:00:00+00:00"},
        {"id": f"r3_{w.s}", "vehicle_id": vid, "dealer_id": w.dealer_id, "appointment_id": a3,
         "version": 1, "created_at": "2026-09-01T10:00:00+00:00"},
        {"id": f"r3b_{w.s}", "vehicle_id": vid, "dealer_id": w.dealer_id, "appointment_id": a3,
         "version": 2, "created_at": "2026-09-01T11:00:00+00:00"},
    ]))
    monkeypatch.setattr(AB, "_BERICHTE_MAX", 2)      # frueher: nur r1, r2 geladen -> r1
    assert run(AB.massgeblicher_bericht(db, vid, w.dealer_id))["id"] == f"r3b_{w.s}"
    assert run(AB.massgeblicher_bericht(db, vid, w.dealer_id, nur_termine=[a1]))["id"] == f"r1_{w.s}"
    assert run(AB.massgeblicher_bericht(db, vid, w.dealer_id, nur_termine=[])) is None
    run(db.appointments.update_one({"id": a3}, {"$set": {"status": "offen"}}))
    assert run(AB.massgeblicher_bericht(db, vid, w.dealer_id))["id"] == f"r1_{w.s}"


# ================================================================ R1-16
def test_r1_16_firmenabo_juengstes_gilt_ohne_feld_oder_null(welt):
    w, deps = welt, _module("deps")
    db, run = w.db, w.run
    did = f"d_abo_a5_{w.s}"
    bald = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    run(db.subscriptions.insert_many([
        {"id": f"s1_{w.s}", "dealer_id": did, "plan": "monthly", "status": "active",
         "expires_at": bald, "created_at": "2026-01-01T00:00:00+00:00"},                       # ohne Feld
        {"id": f"s2_{w.s}", "dealer_id": did, "subject_user_id": None, "plan": "yearly",
         "status": "active", "expires_at": bald, "created_at": "2026-05-01T00:00:00+00:00"},   # null, juenger
        {"id": f"s3_{w.s}", "dealer_id": did, "subject_user_id": None, "plan": "monthly",
         "status": "ersetzt", "created_at": "2026-06-01T00:00:00+00:00"},                      # ersetzt
        {"id": f"s4_{w.s}", "dealer_id": did, "subject_user_id": f"u_x_{w.s}", "plan": "monthly",
         "status": "active", "expires_at": bald, "created_at": "2026-07-01T00:00:00+00:00"},   # persoenlich
    ]))
    erg = run(deps.get_subscription_status(did))
    assert erg["plan"] == "yearly" and erg["active"] is True, erg
    q = inspect.getsource(deps.get_subscription_status)
    assert q.count("find_one") == 2, "eine Abfrage je Zweig, kein null-Fallback mehr"
    q2 = (WURZEL / "backend" / "routes" / "admin.py").read_text(encoding="utf-8")
    assert "_feld_fehlt" not in q2


# ================================================================ AL-19
def test_al19_nachholen_bricht_nie_ab_und_nennt_dubletten(welt, monkeypatch):
    w, IX, ADMIN, BT = welt, _module("indizes"), _module("routes.admin"), _module("betrieb")
    run = w.run
    monkeypatch.setenv("APP_ENV", "production")

    async def _dubletten():
        return ["c-dublette"]

    def _boom(grund):
        raise SystemExit(78)

    async def _still(*a, **k):
        return None
    monkeypatch.setattr(IX, "termin_dubletten", _dubletten)
    monkeypatch.setattr(IX, "_in_produktion_abbrechen", _boom)
    monkeypatch.setattr(BT, "alarm", _still)
    monkeypatch.setattr(ADMIN, "abo_vorgaenge_nachholen", _still)
    try:
        assert run(IX._termin_unique_index(abbruch=False)) is False
        with pytest.raises(SystemExit):
            run(IX._termin_unique_index())          # Start: Abbruch wie bisher
        erg = run(ADMIN.admin_betrieb_nachholen(admin={"id": "sa", "role": "admin", "is_super_admin": True}))
    finally:
        IX.FEHLENDE_UNIQUE.discard("appointments.termin_offen_je_vertrag")
    assert erg["termin_index"] is False and erg["dubletten"] == {"termine": ["c-dublette"]}
    q = inspect.getsource(ADMIN.admin_betrieb_nachholen)
    assert "_termin_unique_index(abbruch=False)" in q
