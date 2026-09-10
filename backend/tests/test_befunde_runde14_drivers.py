# -*- coding: utf-8 -*-
"""Nachpruefung Runde 14 (09/2026), Gruppe "drivers".

  9    gesperrte Firma: Fahrer sieht deren Termine nicht mehr, 403 fuer
       Status/Bericht/PDF; Entsperren stellt alles wieder her; zweite
       (aktive) Firma desselben Fahrers unberuehrt
  36   Abholbericht: Foto 1 gueltig + Foto 2 Muell -> 400, keine Waise im
       Storage; bei Loeschfehler Vormerkung in storage_delete_retry
  37   Versionsnummer aus ALLEN Versionen (nur ersetzte Version 1 -> 2);
       dritter Insert-Fehlschlag: 409, Dateien und Reservierung weg
  38   nach dem Speichern ist nur der neue Bericht aktuell (Selbstheilung),
       Leser liefern die hoechste Version unabhaengig vom Index;
       abholbericht.massgeblicher_bericht bevorzugt den "abgeholt"-Termin
  43   erstbericht_reserviert_at wird bei JEDEM Fehlerpfad geloest
  111  driver_conflicts: count aus der Datenbank, has_more bei > 50

Einheitentests laufen in-Prozess gegen Mongo (Muster tests/test_fahrer_
zugriff.py). HTTP-Tests laufen nur mit RUNDE14_HTTP=1 (Backend auf
TEST_BASE_URL mit SELF_SIGNUP=true, nach dem Neustart mit neuem Code).
"""
import asyncio
import base64
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
# Runde 21 (Pruefbefund C): klarer Skip-Grund statt "HTTP nach Neustart" —
# die CI setzt RUNDE14_HTTP=1 im Schritt "Selbsttest-Suite".
HTTP_GRUND = ("RUNDE14_HTTP=1 nicht gesetzt — HTTP-Test braucht ein laufendes "
              "Backend auf TEST_BASE_URL (CI: Schritt Selbsttest-Suite)")
BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "RundeVierzehn14!"
MAIL = "e2etest-mail.de"

# Nur der Magic-Header wird geprueft; Verkleinern faellt auf das Original zurueck.
JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 64).decode()
MUELL_B64 = base64.b64encode(b"kein bild " * 8).decode()


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _upload_dir(dealer_id):
    return BACKEND / "uploads" / "pickup" / dealer_id


def _dateien(dealer_id):
    d = _upload_dir(dealer_id)
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


# =====================================================================
#                 In-Prozess-Geruest (wie test_fahrer_zugriff.py)
# =====================================================================
class _Daten:
    def __init__(self):
        s = uuid.uuid4().hex[:10]
        self.s = s
        self.tag = f"r14_{s}"
        self.dealer_id = f"d_r14_{s}"
        self.dealer2_id = f"d2_r14_{s}"
        self.driver_id = f"f_r14_{s}"
        self.chef_id = f"u_r14_{s}"
        self.user = {"id": self.chef_id, "dealer_id": self.dealer_id, "role": "dealer"}
        self.driver = {"id": self.driver_id, "display_name": f"Fahrer {s}",
                       "email": f"fahrer-{s}@example.test",
                       "driver_code": "FD-" + s.upper()[:8], "active": True}

    def link(self, dealer_id=None):
        return {"id": str(uuid.uuid4()), "dealer_id": dealer_id or self.dealer_id,
                "driver_account_id": self.driver_id,
                "display_name": self.driver["display_name"], "added_at": _jetzt()}

    def chef(self, dealer_id=None, active=True):
        return {"id": f"u_{dealer_id or self.dealer_id}", "dealer_id": dealer_id or self.dealer_id,
                "role": "dealer", "active": active, "email": f"{dealer_id or self.dealer_id}@example.test",
                "created_at": "2026-01-01T00:00:00+00:00"}

    def appt(self, appt_id, status, **extra):
        doc = {"id": appt_id, "dealer_id": self.dealer_id, "driver_id": self.driver_id,
               "status": status, "pickup_date": "2026-09-15", "pickup_time": "10:00",
               "pickup_address": "Teststr. 1", "title": f"Fahrt {appt_id}",
               "seller_name": "Verkaeufer", "created_at": _jetzt()}
        doc.update(extra)
        return doc

    def bericht(self, appt_id, version, superseded, **extra):
        doc = {"id": f"b_{appt_id}_{version}", "appointment_id": appt_id,
               "dealer_id": self.dealer_id, "driver_account_id": self.driver_id,
               "driver_name": "x", "version": version, "superseded": superseded,
               "deviations": [], "status": "bestaetigt", "notes": f"v{version}",
               "created_at": _jetzt()}
        doc.update(extra)
        return doc

    async def aufraeumen(self, db):
        dealers = [self.dealer_id, self.dealer2_id]
        await db.appointments.delete_many({"dealer_id": {"$in": dealers}})
        await db.dealer_drivers.delete_many({"driver_account_id": self.driver_id})
        await db.pickup_reports.delete_many({"dealer_id": {"$in": dealers}})
        await db.users.delete_many({"dealer_id": {"$in": dealers}})
        await db.storage_delete_retry.delete_many({"dealer_id": {"$in": dealers}})
        await db.activity_logs.delete_many({"dealer_id": {"$in": dealers}})
        for d in dealers:
            verz = _upload_dir(d)
            if verz.exists():
                for p in verz.iterdir():
                    p.unlink()
                verz.rmdir()


def _run(fn):
    async def _inner():
        from motor.motor_asyncio import AsyncIOMotorClient
        import deps
        import lifecycle
        import routes.drivers as D
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        db = cl[DB_NAME]
        alt = (D.db, deps.db, lifecycle.db)
        t = _Daten()
        D.db = deps.db = lifecycle.db = db
        try:
            return await fn(db, D, t)
        finally:
            D.db, deps.db, lifecycle.db = alt
            await t.aufraeumen(db)
            cl.close()
    return asyncio.run(_inner())


async def _erwarte(status, coro):
    with pytest.raises(HTTPException) as e:
        await coro
    assert e.value.status_code == status, (e.value.status_code, e.value.detail)
    return e.value


def _bericht_in(D, *fotos, **felder):
    devs = [D.DeviationIn(field="damage", label=f"Abweichung {i}", photo_b64=f)
            for i, f in enumerate(fotos, 1)]
    return D.PickupReportIn(deviations=devs, **felder)


class _InsertFehler:
    """Collection-Proxy: insert_one wirft n-mal den vorbereiteten Fehler."""

    def __init__(self, coll, fehler, n):
        self._coll, self._fehler, self._n = coll, fehler, n

    def __getattr__(self, name):
        return getattr(self._coll, name)

    async def insert_one(self, *a, **k):
        if self._n > 0:
            self._n -= 1
            raise self._fehler
        return await self._coll.insert_one(*a, **k)


class _DbProxy:
    def __init__(self, db, **ersatz):
        self._db, self._ersatz = db, ersatz

    def __getattr__(self, name):
        return self._ersatz.get(name) or getattr(self._db, name)


# =====================================================================
# 9) gesperrte Firma: kein Fahrer-Zugriff (unit)
# =====================================================================
def test_9_gesperrte_firma_sperrt_fahrerzugriff_und_entsperren_stellt_her():
    async def lauf(db, D, t):
        a1, a2 = f"a1_{t.tag}", f"a2_{t.tag}"
        await db.users.insert_many([t.chef(active=False), t.chef(t.dealer2_id, active=True)])
        await db.dealer_drivers.insert_many([t.link(), t.link(t.dealer2_id)])
        await db.appointments.insert_many([
            t.appt(a1, "offen", contract_id=f"c_{t.tag}"),
            dict(t.appt(a2, "offen"), dealer_id=t.dealer2_id)])

        assert await D._verknuepfte_dealer_ids(t.driver_id) == [t.dealer2_id]
        ids = [a["id"] for a in await D.driver_appointments(t.driver)]
        assert ids == [a2], "Termin der gesperrten Firma darf nicht erscheinen"

        appt1 = await db.appointments.find_one({"id": a1}, {"_id": 0})
        e = await _erwarte(403, D._zugriff_pruefen(appt1, t.driver))
        assert "gesperrt" in e.detail
        await _erwarte(403, D.driver_set_status(a1, D.DriverStatusIn(status="nicht abgeholt"), t.driver))
        await _erwarte(403, D.driver_submit_report(a1, D.PickupReportIn(), t.driver))
        await _erwarte(403, D.driver_get_report(a1, t.driver))
        await _erwarte(403, D.driver_pickup_order_pdf(a1, 0, t.driver))
        await _erwarte(403, D.driver_contract_pdf(f"c_{t.tag}", t.driver))
        await _erwarte(403, D.driver_zuteilung(a1, D.DriverZuteilungIn(action="annehmen"), t.driver))
        assert (await db.appointments.find_one({"id": a1}))["status"] == "offen"
        assert await db.pickup_reports.count_documents({"appointment_id": a1}) == 0
        # aktive zweite Firma unberuehrt
        assert (await D.driver_get_report(a2, t.driver)) == {}

        # Entsperren -> wieder da (Verknuepfung blieb erhalten)
        await db.users.update_one({"id": f"u_{t.dealer_id}"}, {"$set": {"active": True}})
        assert sorted(await D._verknuepfte_dealer_ids(t.driver_id)) == sorted([t.dealer_id, t.dealer2_id])
        assert sorted(a["id"] for a in await D.driver_appointments(t.driver)) == sorted([a1, a2])
        await D._zugriff_pruefen(appt1, t.driver)
        assert (await D.driver_get_report(a1, t.driver)) == {}
        # fehlendes active-Feld = aktiv (wie deps.firma_gesperrt)
        await db.users.update_one({"id": f"u_{t.dealer_id}"}, {"$unset": {"active": ""}})
        await D._zugriff_pruefen(appt1, t.driver)

    _run(lauf)


# =====================================================================
# 43 + 36) Fehlerpfade loesen Reservierung und verwerfen Dateien (unit)
# =====================================================================
def test_43_kaputtes_foto_loest_reservierung_und_wiederholung_klappt():
    async def lauf(db, D, t):
        a = f"a_{t.tag}"
        await db.dealer_drivers.insert_one(t.link())
        await db.appointments.insert_one(t.appt(a, "abgeholt", status_changed_at=_jetzt()))
        e = await _erwarte(400, D.driver_submit_report(a, _bericht_in(D, MUELL_B64), t.driver))
        assert "Foto" in e.detail
        appt = await db.appointments.find_one({"id": a}, {"_id": 0})
        assert "erstbericht_reserviert_at" not in appt, "Reservierung blieb nach 400 stehen"
        assert await db.pickup_reports.count_documents({"appointment_id": a}) == 0
        assert _dateien(t.dealer_id) == []
        # Wiederholung ohne Foto: Erstbericht geht durch
        r = await D.driver_submit_report(a, D.PickupReportIn(notes="ok"), t.driver)
        assert r["ok"] and r["version"] == 1
        assert await db.pickup_reports.count_documents({"appointment_id": a}) == 1
        appt = await db.appointments.find_one({"id": a}, {"_id": 0})
        assert appt.get("erstbericht_reserviert_at"), "Erfolg haelt die Reservierung"
        assert appt.get("has_pickup_report") is True
        # Zweiter Erstbericht bleibt gesperrt (Korrektur nur ueber den Haendler)
        await _erwarte(409, D.driver_submit_report(a, D.PickupReportIn(), t.driver))

    _run(lauf)


def test_36_foto1_gueltig_foto2_muell_hinterlaesst_keine_waise():
    async def lauf(db, D, t):
        a = f"a_{t.tag}"
        await db.dealer_drivers.insert_one(t.link())
        await db.appointments.insert_one(t.appt(a, "offen"))
        await _erwarte(400, D.driver_submit_report(a, _bericht_in(D, JPEG_B64, MUELL_B64), t.driver))
        assert _dateien(t.dealer_id) == [], "Foto 1 blieb als Waise im Storage"
        assert await db.pickup_reports.count_documents({"appointment_id": a}) == 0
        assert await db.storage_delete_retry.count_documents({"dealer_id": t.dealer_id}) == 0
        # ohne Reservierung (Termin offen) darf auch keine geloest werden muessen
        assert "erstbericht_reserviert_at" not in await db.appointments.find_one({"id": a}, {"_id": 0})
        # Danach funktioniert ein sauberer Bericht mit Foto
        r = await D.driver_submit_report(a, _bericht_in(D, JPEG_B64), t.driver)
        assert r["version"] == 1 and r["deviations_count"] == 1
        assert len(_dateien(t.dealer_id)) == 1

    _run(lauf)


def test_36_loeschfehler_beim_rollback_wird_vorgemerkt(monkeypatch):
    async def lauf(db, D, t):
        import storage_service
        a = f"a_{t.tag}"
        await db.dealer_drivers.insert_one(t.link())
        await db.appointments.insert_one(t.appt(a, "offen"))

        async def _kaputt(key):
            raise storage_service.StorageError("Speicher weg")
        monkeypatch.setattr(storage_service, "delete_async", _kaputt)
        await _erwarte(400, D.driver_submit_report(a, _bericht_in(D, JPEG_B64, MUELL_B64), t.driver))
        dateien = _dateien(t.dealer_id)
        assert len(dateien) == 1, "Datei konnte nicht geloescht werden -> muss liegen bleiben"
        eintrag = await db.storage_delete_retry.find_one({"dealer_id": t.dealer_id}, {"_id": 0})
        assert eintrag and eintrag["art"] == "key" and eintrag["key"].endswith(dateien[0])
        assert eintrag["grund"] == "abholbericht-rollback"
        assert eintrag["ref"] == {"collection": "appointments", "id": a}

    _run(lauf)


# =====================================================================
# 37) Versionsnummer aus allen Versionen; Insert-Fehlschlag raeumt auf (unit)
# =====================================================================
def test_37_nur_ersetzte_version_1_ergibt_version_2():
    async def lauf(db, D, t):
        a = f"a_{t.tag}"
        await db.dealer_drivers.insert_one(t.link())
        await db.appointments.insert_one(t.appt(a, "offen"))
        await db.pickup_reports.insert_one(t.bericht(a, 1, True))
        r = await D.driver_submit_report(a, _bericht_in(D, JPEG_B64, notes="neu"), t.driver)
        assert r["version"] == 2, r
        neu = await db.pickup_reports.find_one({"id": r["report_id"]}, {"_id": 0})
        assert neu["superseded"] is False and neu["replaces_id"] is None
        assert (await D.driver_get_report(a, t.driver))["id"] == r["report_id"]
        # Log: Korrektur, weil version > 1
        assert await db.activity_logs.count_documents(
            {"dealer_id": t.dealer_id, "action": "abholung.bericht.korrektur"}) == 1

    _run(lauf)


def test_37_dritter_insert_fehlschlag_verwirft_dateien_und_reservierung():
    async def lauf(db, D, t):
        from pymongo.errors import DuplicateKeyError
        a = f"a_{t.tag}"
        await db.dealer_drivers.insert_one(t.link())
        await db.appointments.insert_one(t.appt(a, "abgeholt", status_changed_at=_jetzt()))
        D.db = _DbProxy(db, pickup_reports=_InsertFehler(
            db.pickup_reports, DuplicateKeyError("E11000 duplicate key"), 3))
        await _erwarte(409, D.driver_submit_report(a, _bericht_in(D, JPEG_B64), t.driver))
        assert _dateien(t.dealer_id) == [], "Foto blieb nach 409 im Storage"
        assert "erstbericht_reserviert_at" not in await db.appointments.find_one({"id": a}, {"_id": 0})
        assert await db.pickup_reports.count_documents({"appointment_id": a}) == 0
        # zwei Fehlschlaege werden ueberstanden (dritter Versuch gewinnt)
        D.db = _DbProxy(db, pickup_reports=_InsertFehler(
            db.pickup_reports, DuplicateKeyError("E11000 duplicate key"), 2))
        r = await D.driver_submit_report(a, _bericht_in(D, JPEG_B64), t.driver)
        assert r["version"] == 1 and len(_dateien(t.dealer_id)) == 1
        D.db = db

    _run(lauf)


# =====================================================================
# 38) genau ein aktueller Bericht; Leser index-unabhaengig (unit)
# =====================================================================
def test_38_doppelzustand_wird_beim_speichern_geheilt_und_leser_sortieren():
    async def lauf(db, D, t):
        a = f"a_{t.tag}"
        await db.dealer_drivers.insert_one(t.link())
        await db.appointments.insert_one(t.appt(a, "offen"))
        await db.pickup_reports.insert_many([t.bericht(a, 1, False), t.bericht(a, 2, False)])
        # Leser: hoechste Version, egal welchen Index der Planer nimmt
        assert (await D.driver_get_report(a, t.driver))["version"] == 2
        for hint in ("berichtsversion_eindeutig", "appointment_id_1_version_-1"):
            doc = await db.pickup_reports.find_one(
                {"appointment_id": a, "superseded": {"$ne": True}}, {"_id": 0},
                sort=[("version", -1)], hint=hint)
            assert doc["version"] == 2, hint
        r = await D.driver_submit_report(a, D.PickupReportIn(notes="v3"), t.driver)
        assert r["version"] == 3
        aktuell = await db.pickup_reports.find({"appointment_id": a, "superseded": {"$ne": True}},
                                               {"_id": 0}).to_list(10)
        assert [d["id"] for d in aktuell] == [r["report_id"]], aktuell
        assert (await db.pickup_reports.find_one({"id": r["report_id"]}))["replaces_id"] == f"b_{a}_2"
        assert await db.pickup_reports.count_documents({"appointment_id": a}) == 3

    _run(lauf)


def test_38_selbstheilung_ersetzt_keinen_juengeren_parallelen_bericht():
    """update_many nur fuer Versionen < eigene: der aeltere von zwei
    parallelen Berichten darf den juengeren nicht ersetzen (sonst null
    aktuelle)."""
    async def lauf(db, D, t):
        a = f"a_{t.tag}"
        await db.dealer_drivers.insert_one(t.link())
        await db.appointments.insert_one(t.appt(a, "offen"))
        await db.pickup_reports.insert_one(t.bericht(a, 1, False))
        # Parallel-Simulation: erster Insert kollidiert, dazwischen legt ein
        # "anderer" Lauf Version 2 an.
        original = db.pickup_reports

        class _Dazwischen:
            def __init__(self):
                self.n = 0

            def __getattr__(self, name):
                return getattr(original, name)

            async def insert_one(self, doc, *a_, **k):
                self.n += 1
                if self.n == 1:
                    await original.insert_one(t.bericht(a, 2, False))
                    await original.update_many({"appointment_id": a, "version": {"$lt": 2}},
                                               {"$set": {"superseded": True}})
                    from pymongo.errors import DuplicateKeyError
                    raise DuplicateKeyError("E11000 duplicate key")
                return await original.insert_one(doc, *a_, **k)
        D.db = _DbProxy(db, pickup_reports=_Dazwischen())
        r = await D.driver_submit_report(a, D.PickupReportIn(notes="spaet"), t.driver)
        D.db = db
        assert r["version"] == 3
        aktuell = await db.pickup_reports.find({"appointment_id": a, "superseded": {"$ne": True}},
                                               {"_id": 0, "version": 1}).to_list(10)
        assert [d["version"] for d in aktuell] == [3], aktuell

    _run(lauf)


def test_38_massgeblicher_bericht_bevorzugt_abgeholten_termin():
    async def lauf(db, D, t):
        from abholbericht import massgeblicher_bericht
        v = f"v_{t.tag}"
        a_nicht, a_abg, a_alt = f"an_{t.tag}", f"ab_{t.tag}", f"aa_{t.tag}"
        await db.appointments.insert_many([
            t.appt(a_nicht, "nicht abgeholt", vehicle_id=v, pickup_date="2026-09-20"),
            t.appt(a_abg, "abgeholt", vehicle_id=v, pickup_date="2026-09-10"),
            t.appt(a_alt, "abgeholt", vehicle_id=v, pickup_date="2026-09-01")])
        alt = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        await db.pickup_reports.insert_many([
            t.bericht(a_nicht, 1, False, vehicle_id=v, created_at=_jetzt()),
            t.bericht(a_abg, 1, True, vehicle_id=v, created_at=alt),
            t.bericht(a_abg, 2, False, vehicle_id=v, created_at=alt),
            t.bericht(a_alt, 1, False, vehicle_id=v, created_at=alt)])
        b = await massgeblicher_bericht(db, v, t.dealer_id)
        assert b and b["appointment_id"] == a_abg and b["version"] == 2 and "_id" not in b
        # fremde Firma: nichts
        assert await massgeblicher_bericht(db, v, t.dealer2_id) is None
        assert await massgeblicher_bericht(db, "", t.dealer_id) is None
        # ohne abgeholten Termin: juengster aktueller Bericht
        await db.appointments.update_many({"vehicle_id": v}, {"$set": {"status": "nicht abgeholt"}})
        b = await massgeblicher_bericht(db, v, t.dealer_id)
        assert b["appointment_id"] == a_nicht
        # nur ersetzte Berichte: None
        await db.pickup_reports.update_many({"vehicle_id": v}, {"$set": {"superseded": True}})
        assert await massgeblicher_bericht(db, v, t.dealer_id) is None

    _run(lauf)


# =====================================================================
# 111) Konfliktzaehler vollstaendig (unit)
# =====================================================================
def test_111_konflikte_zaehlen_ueber_die_liste_hinaus():
    async def lauf(db, D, t):
        await db.dealer_drivers.insert_one(t.link())
        datum = "2026-09-15"
        docs = [t.appt(f"a{i:03d}_{t.tag}", "offen", pickup_time=f"{23 - i % 24:02d}:{i % 60:02d}")
                for i in range(51)]
        docs.append(t.appt(f"a_zu_{t.tag}", "abgeholt"))
        await db.appointments.insert_many(docs)
        r = await D.driver_conflicts(t.driver_id, datum, t.user)
        assert r["count"] == 51 and r["has_more"] is True and len(r["conflicts"]) == 50, (r["count"], len(r["conflicts"]))
        zeiten = [c["pickup_time"] for c in r["conflicts"]]
        assert zeiten == sorted(zeiten), "Liste nach Uhrzeit sortiert"
        await db.appointments.delete_one({"id": f"a000_{t.tag}"})
        r = await D.driver_conflicts(t.driver_id, datum, t.user)
        assert r["count"] == 50 and r["has_more"] is False and len(r["conflicts"]) == 50
        assert (await D.driver_conflicts(t.driver_id, "", t.user)) == {"conflicts": []}

    _run(lauf)


# =====================================================================
#                              HTTP-Tests
# =====================================================================
def _kopf(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def welt():
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    import routes.drivers as D
    dbx = _db()
    z = {}
    for nr in (1, 2):
        mail = f"r14drv_chef{nr}_{SUF}@{MAIL}"
        r = requests.post(f"{API}/auth/register", json={
            "email": mail, "password": PW, "company_name": f"R14 Fahrer-Autohaus {nr} {SUF}",
            "contact_person": f"Chef {nr}", "phone": "0511 1"}, timeout=30)
        assert r.status_code == 200, f"Backend braucht SELF_SIGNUP=true: {r.text[:200]}"
        z[f"h{nr}"] = {"kopf": _kopf(r.json()["token"]), "dealer_id": r.json()["user"]["dealer_id"],
                       "user_id": r.json()["user"]["id"], "mail": mail}
        dbx.subscriptions.insert_one({
            "id": str(uuid.uuid4()), "dealer_id": z[f"h{nr}"]["dealer_id"],
            "subject_user_id": z[f"h{nr}"]["user_id"], "plan": "monthly", "status": "active",
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "created_at": _jetzt()})
    # Fahrerkonto direkt in Mongo (Registrierungs-Limit: 5/h je IP), Token gemuenzt
    did, sid = f"r14drv_{SUF}", str(uuid.uuid4())
    code = "FD-" + SUF.upper()
    dbx.driver_accounts.insert_one({
        "id": did, "email": f"r14drv_fahrer_{SUF}@{MAIL}", "password_hash": "x",
        "display_name": f"Fahrer {SUF}", "driver_code": code, "active": True,
        "current_session_id": sid, "created_at": _jetzt()})
    z["fahrer"] = {"id": did, "kopf": _kopf(D.create_driver_token(did, sid))}
    for nr in (1, 2):
        r = requests.post(f"{API}/drivers/add", headers=z[f"h{nr}"]["kopf"],
                          json={"driver_code": code}, timeout=30)
        assert r.status_code == 200, r.text[:200]
    yield z
    for nr in (1, 2):
        d = z[f"h{nr}"]["dealer_id"]
        for coll in ("appointments", "pickup_reports", "activity_logs", "subscriptions",
                     "storage_delete_retry", "dealer_drivers"):
            dbx[coll].delete_many({"dealer_id": d})
        dbx.users.delete_many({"dealer_id": d})
        dbx.dealers.delete_many({"id": d})
        verz = _upload_dir(d)
        if verz.exists():
            for p in verz.iterdir():
                p.unlink()
            verz.rmdir()
    dbx.driver_accounts.delete_many({"id": did})
    dbx.dealer_drivers.delete_many({"driver_account_id": did})


def _termin(welt, h, status, **extra):
    aid = f"a_{uuid.uuid4().hex[:8]}_{SUF}"
    doc = {"id": aid, "dealer_id": welt[h]["dealer_id"], "driver_id": welt["fahrer"]["id"],
           "status": status, "pickup_date": "2026-09-15", "pickup_time": "10:00",
           "pickup_address": "Teststr. 1", "title": f"Fahrt {aid}", "seller_name": "V",
           "created_at": _jetzt()}
    doc.update(extra)
    _db().appointments.insert_one(doc)
    return aid


def test_9_http_gesperrte_firma_fuer_fahrer_zu(welt):
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    dbx = _db()
    f = welt["fahrer"]["kopf"]
    a1 = _termin(welt, "h1", "offen")
    a2 = _termin(welt, "h2", "offen")
    r = requests.get(f"{API}/driver/appointments", headers=f, timeout=30)
    assert r.status_code == 200 and {a["id"] for a in r.json()} >= {a1, a2}, r.text[:200]
    dbx.users.update_one({"id": welt["h1"]["user_id"]}, {"$set": {"active": False}})
    try:
        r = requests.get(f"{API}/driver/appointments", headers=f, timeout=30)
        ids = {a["id"] for a in r.json()}
        assert a1 not in ids and a2 in ids, ids
        assert requests.put(f"{API}/driver/appointments/{a1}/status", headers=f,
                            json={"status": "nicht abgeholt"}, timeout=30).status_code == 403
        assert requests.post(f"{API}/driver/appointments/{a1}/report", headers=f,
                             json={"notes": "x"}, timeout=30).status_code == 403
        assert requests.get(f"{API}/driver/appointments/{a1}/report", headers=f, timeout=30).status_code == 403
        assert requests.get(f"{API}/driver/appointments/{a1}/pickup-order.pdf", headers=f, timeout=30).status_code == 403
        assert requests.put(f"{API}/driver/appointments/{a1}/zuteilung", headers=f,
                            json={"action": "annehmen"}, timeout=30).status_code == 403
        assert dbx.appointments.find_one({"id": a1})["status"] == "offen"
        # aktive Firma 2 unberuehrt
        assert requests.get(f"{API}/driver/appointments/{a2}/report", headers=f, timeout=30).status_code == 200
    finally:
        dbx.users.update_one({"id": welt["h1"]["user_id"]}, {"$set": {"active": True}})
    r = requests.get(f"{API}/driver/appointments", headers=f, timeout=30)
    assert a1 in {a["id"] for a in r.json()}
    assert requests.get(f"{API}/driver/appointments/{a1}/report", headers=f, timeout=30).status_code == 200


def test_43_36_http_kaputtes_foto_loest_reservierung_und_dateien(welt):
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    dbx = _db()
    f = welt["fahrer"]["kopf"]
    d = welt["h1"]["dealer_id"]
    a = _termin(welt, "h1", "abgeholt", status_changed_at=_jetzt())
    vorher = set(_dateien(d))
    r = requests.post(f"{API}/driver/appointments/{a}/report", headers=f, json={
        "deviations": [{"field": "damage", "label": "Kratzer", "photo_b64": JPEG_B64},
                       {"field": "damage", "label": "Delle", "photo_b64": MUELL_B64}]}, timeout=60)
    assert r.status_code == 400, r.text[:200]
    assert set(_dateien(d)) == vorher, "Foto 1 blieb als Waise liegen"
    assert "erstbericht_reserviert_at" not in dbx.appointments.find_one({"id": a}, {"_id": 0})
    assert dbx.pickup_reports.count_documents({"appointment_id": a}) == 0
    assert dbx.storage_delete_retry.count_documents({"dealer_id": d}) == 0
    r = requests.post(f"{API}/driver/appointments/{a}/report", headers=f,
                      json={"notes": "zweiter Versuch"}, timeout=60)
    assert r.status_code == 200 and r.json()["version"] == 1, r.text[:200]
    assert dbx.pickup_reports.count_documents({"appointment_id": a}) == 1


def test_37_38_http_nur_ersetzte_version_und_ein_aktueller(welt):
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    dbx = _db()
    f = welt["fahrer"]["kopf"]
    d = welt["h1"]["dealer_id"]
    a = _termin(welt, "h1", "offen")
    basis = {"appointment_id": a, "dealer_id": d, "driver_account_id": welt["fahrer"]["id"],
             "driver_name": "x", "deviations": [], "status": "bestaetigt", "created_at": _jetzt()}
    dbx.pickup_reports.insert_many([
        dict(basis, id=f"b1_{a}", version=1, superseded=True),
        dict(basis, id=f"b2_{a}", version=2, superseded=False),
        dict(basis, id=f"b3_{a}", version=3, superseded=False)])
    r = requests.get(f"{API}/driver/appointments/{a}/report", headers=f, timeout=30)
    assert r.status_code == 200 and r.json()["version"] == 3
    r = requests.post(f"{API}/driver/appointments/{a}/report", headers=f, json={
        "deviations": [{"field": "damage", "label": "Kratzer", "photo_b64": JPEG_B64}]}, timeout=60)
    assert r.status_code == 200 and r.json()["version"] == 4, r.text[:200]
    aktuell = list(dbx.pickup_reports.find({"appointment_id": a, "superseded": {"$ne": True}}, {"_id": 0, "id": 1}))
    assert [x["id"] for x in aktuell] == [r.json()["report_id"]], aktuell
    r2 = requests.get(f"{API}/appointments/{a}/pickup-report", headers=welt["h1"]["kopf"], timeout=30)
    if r2.status_code == 200 and (r2.json().get("report") or {}).get("id"):
        assert r2.json()["report"]["version"] == 4


def test_111_http_konfliktzaehler(welt):
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    ids = [_termin(welt, "h1", "offen", pickup_date="2026-10-01", pickup_time=f"{i % 24:02d}:00")
           for i in range(51)]
    try:
        r = requests.get(f"{API}/drivers/{welt['fahrer']['id']}/conflicts",
                         params={"date": "2026-10-01"}, headers=welt["h1"]["kopf"], timeout=30)
        assert r.status_code == 200, r.text[:200]
        j = r.json()
        assert j["count"] == 51 and j["has_more"] is True and len(j["conflicts"]) == 50, (j["count"], len(j["conflicts"]))
    finally:
        _db().appointments.delete_many({"id": {"$in": ids}})
