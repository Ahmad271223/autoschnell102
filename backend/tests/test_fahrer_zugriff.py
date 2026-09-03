# -*- coding: utf-8 -*-
"""Fahrer-Zugriff nach Entfernen/Loeschung, Anonymisierung, Konfliktsuche,
idempotenter Protokoll-Abschluss und parallele Korrekturen (Pruefbericht
09/2026).

In-Prozess-Tests (kein laufendes Backend noetig, nur Mongo): die Routen-
Funktionen aus routes.drivers / routes.protocols werden direkt mit
Fake-`driver`-/`user`-Dicts aufgerufen; das Modul-`db` wird auf einen
Test-Client umgebogen (auch in deps/lifecycle, weil log_activity und
try_set_lifecycle daran haengen). Alle Testdaten tragen ein uuid-Suffix
und werden am Ende jedes Tests entfernt.
"""
import asyncio
import base64
import hashlib
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"

# 1x1-PNG (nur der Magic-Header wird geprueft) fuer Unterschriften.
_PNG_B64 = base64.b64encode(
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()


class _Daten:
    """IDs eines Testlaufs (uuid-Suffix) + Aufraeumen."""

    def __init__(self):
        s = uuid.uuid4().hex[:10]
        self.s = s
        self.dealer_id = f"d_fz_{s}"
        self.dealer2_id = f"d2_fz_{s}"
        self.driver_id = f"f_fz_{s}"
        self.tag = f"fz_{s}"
        self.user = {"id": f"u_fz_{s}", "dealer_id": self.dealer_id,
                     "role": "dealer"}
        self.driver = {"id": self.driver_id, "display_name": f"Fahrer {s}",
                       "email": f"fahrer-{s}@example.test",
                       "driver_code": "FD-" + s.upper()[:8], "active": True}

    def link(self, dealer_id=None):
        return {"id": str(uuid.uuid4()), "dealer_id": dealer_id or self.dealer_id,
                "driver_account_id": self.driver_id,
                "display_name": self.driver["display_name"],
                "added_at": "2026-09-01T00:00:00+00:00"}

    def appt(self, appt_id, status, **extra):
        doc = {"id": appt_id, "dealer_id": self.dealer_id,
               "driver_id": self.driver_id, "status": status,
               "pickup_date": "2026-09-15", "pickup_time": "10:00",
               "pickup_address": "Teststr. 1", "title": f"Fahrt {appt_id}",
               "seller_name": "Verkaeufer", "created_at": "2026-09-01T00:00:00+00:00"}
        doc.update(extra)
        return doc

    async def aufraeumen(self, db):
        dealers = [self.dealer_id, self.dealer2_id]
        pseudo = _pseudonym(self.driver_id)
        await db.appointments.delete_many({"dealer_id": {"$in": dealers}})
        await db.dealer_drivers.delete_many({"driver_account_id": self.driver_id})
        await db.pickup_reports.delete_many({"dealer_id": {"$in": dealers}})
        await db.pickup_protocols.delete_many({"dealer_id": {"$in": dealers}})
        await db.vehicles.delete_many({"dealer_id": {"$in": dealers}})
        await db.listing_snapshots.delete_many({"id": f"snap_{self.tag}"})
        await db.storage_delete_retry.delete_many({"dealer_id": {"$in": dealers}})
        await db.activity_logs.delete_many(
            {"$or": [{"dealer_id": {"$in": dealers}}, {"ref": self.tag},
                     {"user_id": {"$in": [self.driver_id, pseudo]}}]})
        await db.password_resets.delete_many(
            {"user_id": {"$in": [self.driver_id, pseudo]}})


def _pseudonym(driver_id: str) -> str:
    return "geloescht:" + hashlib.sha256(driver_id.encode()).hexdigest()[:12]


def _run(fn):
    """Test-Coroutine mit frischem Motor-Client ausfuehren; Modul-`db`
    von drivers/protocols/deps/lifecycle zeigt solange auf diesen Client
    (Motor bindet sich an die erste Event-Loop — je Test eine neue)."""
    async def _inner():
        from motor.motor_asyncio import AsyncIOMotorClient
        import deps
        import lifecycle
        import routes.drivers as D
        import routes.protocols as P
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        db = cl[DB_NAME]
        alt = (D.db, P.db, deps.db, lifecycle.db)
        t = _Daten()
        D.db = P.db = deps.db = lifecycle.db = db
        try:
            return await fn(db, D, P, t)
        finally:
            D.db, P.db, deps.db, lifecycle.db = alt
            await t.aufraeumen(db)
            cl.close()
    return asyncio.run(_inner())


async def _erwarte(status: int, coro):
    with pytest.raises(HTTPException) as e:
        await coro
    assert e.value.status_code == status, (e.value.status_code, e.value.detail)


# ---------------------------------------------------------------------
# 1) Entfernter Fahrer: kein Zugriff mehr auf (auch abgeschlossene) Termine
# ---------------------------------------------------------------------
def test_entfernter_fahrer_verliert_zugriff_auf_abgeschlossene_termine():
    async def lauf(db, D, P, t):
        appt_id = f"a_{t.tag}"
        contract_id = f"c_{t.tag}"
        vehicle_id = f"v_{t.tag}"
        foto_key = f"pickup/{t.dealer_id}/{uuid.uuid4().hex}.jpg"
        await db.appointments.insert_one(
            t.appt(appt_id, "abgeholt", contract_id=contract_id,
                   vehicle_id=vehicle_id, status_changed_at="2026-09-01T10:00:00+00:00"))
        await db.pickup_reports.insert_one({
            "id": f"r_{t.tag}", "appointment_id": appt_id, "dealer_id": t.dealer_id,
            "driver_account_id": t.driver_id, "driver_name": "x", "version": 1,
            "superseded": False, "deviations": [{"id": "d1", "photo_key": foto_key}]})
        await db.listing_snapshots.insert_one({
            "id": f"snap_{t.tag}", "vehicle_id": vehicle_id, "dealer_id": t.dealer_id,
            "status": "ready", "pdf_path": "snapshots/x.pdf", "png_path": "snapshots/x.png"})
        # driver_id steht noch am Termin, aber KEINE Verknuepfung dealer_drivers
        assert await D.driver_appointments(t.driver) == []
        fin = P.FinalizeIn(signature_driver_b64=_PNG_B64, signature_seller_b64=_PNG_B64)
        aufrufe = {
            "pickup-order.pdf": lambda: D.driver_pickup_order_pdf(appt_id, 0, t.driver),
            "contract pdf": lambda: D.driver_contract_pdf(contract_id, t.driver),
            "snapshot": lambda: D.driver_snapshot(f"snap_{t.tag}", "pdf", t.driver),
            "zuteilung": lambda: D.driver_zuteilung(
                appt_id, D.DriverZuteilungIn(action="annehmen"), t.driver),
            "status": lambda: D.driver_set_status(
                appt_id, D.DriverStatusIn(status="abgeholt"), t.driver),
            "report POST": lambda: D.driver_submit_report(
                appt_id, D.PickupReportIn(), t.driver),
            "report GET": lambda: D.driver_get_report(appt_id, t.driver),
            "pickup-foto": lambda: D.driver_pickup_foto(foto_key, t.driver),
            "protocol GET": lambda: P.get_protocol(appt_id, t.driver),
            "protocol PUT": lambda: P.save_protocol(appt_id, P.ProtocolIn(), t.driver),
            "protocol correction": lambda: P.start_correction(appt_id, t.driver),
            "protocol finalize": lambda: P.finalize_protocol(appt_id, fin, t.driver),
            "protocol.pdf": lambda: P.driver_protocol_pdf(appt_id, t.driver),
        }
        for name, f in aufrufe.items():
            with pytest.raises(HTTPException) as e:
                await f()
            assert e.value.status_code == 404, (name, e.value.status_code, e.value.detail)

        # Verknuepfung wieder da -> Termin sichtbar, Bericht lesbar
        await db.dealer_drivers.insert_one(t.link())
        liste = await D.driver_appointments(t.driver)
        assert [a["id"] for a in liste] == [appt_id]
        assert liste[0]["dealer"]["id"] is None or liste[0]["status"] == "abgeholt"
        bericht = await D.driver_get_report(appt_id, t.driver)
        assert bericht.get("id") == f"r_{t.tag}"
        # Verknuepfung bei einer ANDEREN Firma reicht nicht
        await db.dealer_drivers.delete_many({"driver_account_id": t.driver_id})
        await db.dealer_drivers.insert_one(t.link(t.dealer2_id))
        assert await D.driver_appointments(t.driver) == []
        await _erwarte(404, D.driver_get_report(appt_id, t.driver))
        await _erwarte(404, P.get_protocol(appt_id, t.driver))

    _run(lauf)


# ---------------------------------------------------------------------
# 2) delete_driver: offene Termine trennen, abgeschlossene archivieren
# ---------------------------------------------------------------------
def test_delete_driver_archiviert_abgeschlossene_zuordnung():
    async def lauf(db, D, P, t):
        await db.dealer_drivers.insert_one(t.link())
        await db.appointments.insert_many([
            t.appt(f"a_offen_{t.tag}", "offen", zuteilung="offen"),
            t.appt(f"a_versch_{t.tag}", "verschoben", zuteilung="angenommen"),
            t.appt(f"a_best_{t.tag}", "bestätigt"),
            t.appt(f"a_ohne_{t.tag}", None),            # fehlender Status = offen
            t.appt(f"a_abgeholt_{t.tag}", "abgeholt"),
            t.appt(f"a_erledigt_{t.tag}", "erledigt"),
            t.appt(f"a_nicht_{t.tag}", "nicht abgeholt"),
            # Fahrt bei einer ANDEREN Firma darf unberuehrt bleiben
            dict(t.appt(f"a_fremd_{t.tag}", "abgeholt"), dealer_id=t.dealer2_id),
        ])
        r = await D.delete_driver(t.driver_id, t.user)
        assert r["ok"] is True
        assert r["offene_termine_getrennt"] == 4
        assert r["abgeschlossene_termine_archiviert"] == 3

        docs = {a["id"]: a async for a in db.appointments.find(
            {"dealer_id": {"$in": [t.dealer_id, t.dealer2_id]}}, {"_id": 0})}
        for k in ("a_offen", "a_versch", "a_best", "a_ohne"):
            a = docs[f"{k}_{t.tag}"]
            assert "driver_id" not in a, a
            assert "driver_id_hist" not in a, a
            assert a.get("zuteilung") is None and "zuteilung" in a, a
        for k in ("a_abgeholt", "a_erledigt", "a_nicht"):
            a = docs[f"{k}_{t.tag}"]
            assert "driver_id" not in a, a
            assert a.get("driver_id_hist") == t.driver_id, a
        fremd = docs[f"a_fremd_{t.tag}"]
        assert fremd.get("driver_id") == t.driver_id and "driver_id_hist" not in fremd
        assert not await db.dealer_drivers.find_one(
            {"dealer_id": t.dealer_id, "driver_account_id": t.driver_id})
        # zweiter Aufruf: nicht (mehr) in der Liste
        await _erwarte(404, D.delete_driver(t.driver_id, t.user))
        # Sucher duerfen nicht entfernen
        await _erwarte(403, D.delete_driver(t.driver_id, dict(t.user, role="sucher")))

    _run(lauf)


# ---------------------------------------------------------------------
# 3) Anonymisierung eines geloeschten Fahrer-Kontos
# ---------------------------------------------------------------------
def test_fahrer_konto_anonymisieren_ersetzt_ids_und_namen():
    async def lauf(db, D, P, t):
        pseudo = _pseudonym(t.driver_id)
        assert pseudo.startswith("geloescht:") and len(pseudo) == len("geloescht:") + 12
        await db.dealer_drivers.insert_one(t.link())
        await db.appointments.insert_many([
            t.appt(f"a_offen_{t.tag}", "offen", zuteilung="offen"),
            t.appt(f"a_abgeholt_{t.tag}", "abgeholt"),
            # bereits durch delete_driver archiviert
            dict(t.appt(f"a_hist_{t.tag}", "erledigt", driver_id_hist=t.driver_id),
                 driver_id=None),
        ])
        await db.appointments.update_one({"id": f"a_hist_{t.tag}"},
                                         {"$unset": {"driver_id": ""}})
        await db.pickup_reports.insert_one({
            "id": f"r_{t.tag}", "appointment_id": f"a_abgeholt_{t.tag}",
            "dealer_id": t.dealer_id, "driver_account_id": t.driver_id,
            "driver_name": t.driver["display_name"], "version": 1, "superseded": False})
        await db.pickup_protocols.insert_one({
            "id": f"p_{t.tag}", "appointment_id": f"a_abgeholt_{t.tag}",
            "dealer_id": t.dealer_id, "driver_account_id": t.driver_id,
            "driver_name": t.driver["display_name"], "version": 1,
            "status": "final", "superseded": False,
            "signature_driver_key": f"protocol/{t.dealer_id}/sig.png"})
        await db.activity_logs.insert_many([
            {"id": str(uuid.uuid4()), "dealer_id": t.dealer_id, "user_id": t.driver_id,
             "action": "termin.fahrer.angenommen", "ref": t.tag,
             "meta": {"email": t.driver["email"], "driver_code": t.driver["driver_code"],
                      "display_name": t.driver["display_name"], "grund": "bleibt"}},
            {"id": str(uuid.uuid4()), "dealer_id": "", "user_id": t.driver_id,
             "action": "fahrer.passwort.geaendert", "ref": t.tag, "meta": {}},
            {"id": str(uuid.uuid4()), "dealer_id": "", "user_id": t.driver_id,
             "action": "x", "ref": t.tag},                       # ohne meta
            {"id": str(uuid.uuid4()), "dealer_id": t.dealer_id, "user_id": t.user["id"],
             "action": "termin.zugewiesen", "ref": t.tag, "meta": {"email": "chef@x"}},
        ])
        await db.password_resets.insert_one({
            "id": str(uuid.uuid4()), "user_id": t.driver_id, "token_hash": "abc"})

        counts = await D.fahrer_konto_anonymisieren(db, t.driver_id)
        assert counts == {
            "pseudonym": pseudo, "appointments": 3, "pickup_reports": 1,
            "pickup_protocols": 1, "activity_logs": 3, "password_resets": 1,
            "dealer_drivers": 1,
        }, counts

        assert await db.appointments.count_documents(
            {"$or": [{"driver_id": t.driver_id}, {"driver_id_hist": t.driver_id}]}) == 0
        docs = {a["id"]: a async for a in db.appointments.find(
            {"dealer_id": t.dealer_id}, {"_id": 0})}
        offen = docs[f"a_offen_{t.tag}"]
        assert "driver_id" not in offen and offen["driver_id_hist"] == pseudo
        assert offen["zuteilung"] is None
        abgeholt = docs[f"a_abgeholt_{t.tag}"]
        assert "driver_id" not in abgeholt and abgeholt["driver_id_hist"] == pseudo
        assert "zuteilung" not in abgeholt
        assert docs[f"a_hist_{t.tag}"]["driver_id_hist"] == pseudo

        for coll in (db.pickup_reports, db.pickup_protocols):
            d = await coll.find_one({"dealer_id": t.dealer_id}, {"_id": 0})
            assert d["driver_account_id"] == pseudo
            assert d["driver_name"] == "Fahrer (gelöscht)"
        # Unterschrift-Datei im finalen Protokoll bleibt (Beweiskette)
        p = await db.pickup_protocols.find_one({"id": f"p_{t.tag}"}, {"_id": 0})
        assert p["signature_driver_key"] == f"protocol/{t.dealer_id}/sig.png"

        assert await db.activity_logs.count_documents({"user_id": t.driver_id}) == 0
        logs = [l async for l in db.activity_logs.find({"user_id": pseudo}, {"_id": 0})]
        assert len(logs) == 3
        for l in logs:
            meta = l.get("meta") or {}
            assert not ({"email", "driver_code", "display_name"} & set(meta)), l
        assert any((l.get("meta") or {}).get("grund") == "bleibt" for l in logs)
        chef = await db.activity_logs.find_one({"user_id": t.user["id"], "ref": t.tag})
        assert chef["meta"]["email"] == "chef@x"      # fremde Eintraege unberuehrt

        assert await db.password_resets.count_documents({"user_id": t.driver_id}) == 0
        assert await db.dealer_drivers.count_documents(
            {"driver_account_id": t.driver_id}) == 0
        # Idempotent: zweiter Lauf aendert nichts mehr
        nochmal = await D.fahrer_konto_anonymisieren(db, t.driver_id)
        assert all(v == 0 for k, v in nochmal.items() if k != "pseudonym"), nochmal

    _run(lauf)


# ---------------------------------------------------------------------
# 4) Konfliktsuche: abgeschlossene/stornierte Fahrten zaehlen nicht
# ---------------------------------------------------------------------
def test_konfliktsuche_ignoriert_abgeschlossene_fahrten():
    async def lauf(db, D, P, t):
        await db.dealer_drivers.insert_one(t.link())
        datum = "2026-09-15"
        await db.appointments.insert_many([
            t.appt(f"a_offen_{t.tag}", "offen"),
            t.appt(f"a_versch_{t.tag}", "verschoben"),
            t.appt(f"a_abgeholt_{t.tag}", "abgeholt"),
            t.appt(f"a_nicht_{t.tag}", "nicht abgeholt"),
            t.appt(f"a_storno_{t.tag}", "storniert"),
            t.appt(f"a_erledigt_{t.tag}", "erledigt"),
            t.appt(f"a_anderer_tag_{t.tag}", "offen", pickup_date="2026-09-16"),
            dict(t.appt(f"a_fremd_{t.tag}", "bestätigt"), dealer_id=t.dealer2_id),
            dict(t.appt(f"a_fremd_zu_{t.tag}", "abgeholt"), dealer_id=t.dealer2_id),
        ])
        r = await D.driver_conflicts(t.driver_id, datum, t.user)
        ids = sorted(c["id"] for c in r["conflicts"])
        assert ids == sorted([f"a_offen_{t.tag}", f"a_versch_{t.tag}", f"a_fremd_{t.tag}"]), ids
        assert r["count"] == 3
        fremd = next(c for c in r["conflicts"] if c["id"] == f"a_fremd_{t.tag}")
        assert fremd["is_own"] is False and fremd["title"] == "Andere Fahrt"
        assert "pickup_address" not in fremd and "dealer_id" not in fremd
        # Fahrer nicht in der Liste -> 404
        await db.dealer_drivers.delete_many({"driver_account_id": t.driver_id})
        await _erwarte(404, D.driver_conflicts(t.driver_id, datum, t.user))

    _run(lauf)


# ---------------------------------------------------------------------
# 5) Protokoll-Abschluss ist idempotent, wenn schon alles "abgeholt" ist
# ---------------------------------------------------------------------
def test_finalize_idempotent_wenn_termin_schon_abgeholt():
    async def lauf(db, D, P, t):
        appt_id, vid, proto_id = f"a_{t.tag}", f"v_{t.tag}", f"p_{t.tag}"
        await db.dealer_drivers.insert_one(t.link())
        await db.vehicles.insert_one({"id": vid, "dealer_id": t.dealer_id,
                                      "lifecycle": "abholung_geplant", "data": {}})
        # Termin schon abgeholt, aber protocol_id fehlt (Abbruch nach
        # Schritt 2) und Lebenszyklus noch nicht nachgezogen
        await db.appointments.insert_one(
            t.appt(appt_id, "abgeholt", vehicle_id=vid,
                   status_changed_at="2026-09-10T10:00:00+00:00"))
        await db.pickup_protocols.insert_one({
            "id": proto_id, "appointment_id": appt_id, "dealer_id": t.dealer_id,
            "vehicle_id": vid, "driver_account_id": t.driver_id,
            "driver_name": t.driver["display_name"], "version": 1,
            "status": "final", "superseded": False,
            "pdf_path": f"protocol/{t.dealer_id}/x.pdf",
            "finalized_at": "2026-09-10T10:00:00+00:00"})
        fin = P.FinalizeIn(signature_driver_b64=_PNG_B64, signature_seller_b64=_PNG_B64)
        r = await P.finalize_protocol(appt_id, fin, t.driver)
        assert r == {"ok": True, "protocol_id": proto_id, "version": 1,
                     "nachgezogen": True,
                     "pdf_url": f"/api/driver/appointments/{appt_id}/protocol.pdf"}, r
        a = await db.appointments.find_one({"id": appt_id}, {"_id": 0})
        assert a["status"] == "abgeholt" and a["protocol_id"] == proto_id
        assert a["status_changed_at"] == "2026-09-10T10:00:00+00:00"   # nicht neu gestempelt
        v = await db.vehicles.find_one({"id": vid, "dealer_id": t.dealer_id}, {"_id": 0})
        assert v["lifecycle"] == "abgeholt"
        # Wiederholung: gleiches Ergebnis, kein 409, keine neue Version
        r2 = await P.finalize_protocol(appt_id, fin, t.driver)
        assert r2 == r
        assert await db.pickup_protocols.count_documents({"appointment_id": appt_id}) == 1
        p = await db.pickup_protocols.find_one({"id": proto_id}, {"_id": 0})
        assert p["status"] == "final" and p["pdf_path"] == f"protocol/{t.dealer_id}/x.pdf"

    _run(lauf)


# ---------------------------------------------------------------------
# 6) Zwei gleichzeitige Korrekturen: genau EINE gewinnt
# ---------------------------------------------------------------------
def _finales_protokoll(t, appt_id, proto_id):
    return {"id": proto_id, "appointment_id": appt_id, "dealer_id": t.dealer_id,
            "driver_account_id": t.driver_id, "driver_name": t.driver["display_name"],
            "version": 1, "status": "final", "superseded": False,
            "pdf_path": f"protocol/{t.dealer_id}/x.pdf",
            "signature_driver_key": f"protocol/{t.dealer_id}/s1.png",
            "signature_seller_key": f"protocol/{t.dealer_id}/s2.png",
            "keys_count": "2", "condition": {"mileage": "100"},
            "finalized_at": "2026-09-10T10:00:00+00:00",
            "created_at": "2026-09-10T09:00:00+00:00"}


def test_parallele_korrekturen_genau_eine_gewinnt():
    async def lauf(db, D, P, t):
        appt_id, proto_id = f"a_{t.tag}", f"p_{t.tag}"
        await db.dealer_drivers.insert_one(t.link())
        # Korrektur nur bei (vom Haendler wieder) offenem Termin
        await db.appointments.insert_one(t.appt(appt_id, "offen"))
        await db.pickup_protocols.insert_one(_finales_protokoll(t, appt_id, proto_id))

        res = await asyncio.gather(P.start_correction(appt_id, t.driver),
                                   P.start_correction(appt_id, t.driver),
                                   P.start_correction(appt_id, t.driver),
                                   return_exceptions=True)
        ok = [r for r in res if isinstance(r, dict)]
        fehler = [r for r in res if isinstance(r, HTTPException)]
        assert len(ok) == 1 and len(fehler) == 2, res
        assert all(f.status_code in (400, 409) for f in fehler), fehler
        assert any(f.status_code == 409 for f in fehler), fehler

        docs = [d async for d in db.pickup_protocols.find(
            {"appointment_id": appt_id}, {"_id": 0})]
        assert len(docs) == 2, docs
        aktuell = [d for d in docs if d.get("superseded") is False]
        assert len(aktuell) == 1
        neu = aktuell[0]
        assert neu["id"] == ok[0]["id"] and neu["version"] == 2
        assert neu["status"] == "entwurf" and neu["corrects_version"] == 1
        assert "pdf_path" not in neu and "signature_driver_key" not in neu
        assert "superseded_at" not in neu
        alt = next(d for d in docs if d["id"] == proto_id)
        assert alt["superseded"] is True and alt.get("superseded_at")
        assert alt["status"] == "final" and alt["pdf_path"]
        # Danach: keine weitere Korrektur moeglich (aktuell ist ein Entwurf)
        await _erwarte(400, P.start_correction(appt_id, t.driver))

    _run(lauf)


class _InsertFehler:
    """Collection-Proxy: der naechste insert_one wirft den vorbereiteten
    Fehler (simuliert den Unique-Index (appointment_id, version))."""

    def __init__(self, coll, fehler):
        self._coll, self._fehler = coll, [fehler]

    def __getattr__(self, name):
        return getattr(self._coll, name)

    async def insert_one(self, *a, **k):
        if self._fehler:
            raise self._fehler.pop()
        return await self._coll.insert_one(*a, **k)


class _DbProxy:
    def __init__(self, db, **ersatz):
        self._db, self._ersatz = db, ersatz

    def __getattr__(self, name):
        return self._ersatz.get(name) or getattr(self._db, name)


def test_korrektur_duplicate_key_nimmt_abloesung_zurueck():
    async def lauf(db, D, P, t):
        from pymongo.errors import DuplicateKeyError
        appt_id, proto_id = f"a_{t.tag}", f"p_{t.tag}"
        await db.dealer_drivers.insert_one(t.link())
        await db.appointments.insert_one(t.appt(appt_id, "offen"))
        await db.pickup_protocols.insert_one(_finales_protokoll(t, appt_id, proto_id))
        P.db = _DbProxy(db, pickup_protocols=_InsertFehler(
            db.pickup_protocols, DuplicateKeyError("E11000 duplicate key")))
        await _erwarte(409, P.start_correction(appt_id, t.driver))
        alt = await db.pickup_protocols.find_one({"id": proto_id}, {"_id": 0})
        assert alt["superseded"] is False and "superseded_at" not in alt
        assert await db.pickup_protocols.count_documents({"appointment_id": appt_id}) == 1
        # Ohne Stoerung klappt die Korrektur danach normal
        P.db = db
        r = await P.start_correction(appt_id, t.driver)
        assert r["version"] == 2

    _run(lauf)


# ---------------------------------------------------------------------
# 7) Abschluss-Abbruch: geschriebene Dateien werden wieder entfernt
# ---------------------------------------------------------------------
def _vollstaendiger_entwurf(P, t, appt_id, proto_id):
    return {"id": proto_id, "appointment_id": appt_id, "dealer_id": t.dealer_id,
            "driver_account_id": t.driver_id, "driver_name": t.driver["display_name"],
            "version": 1, "status": "entwurf", "superseded": False,
            "vehicle_check": {k: {"status": "stimmt"}
                              for k, _l, _o in P.VEHICLE_CHECK_FIELDS},
            "condition": {"mileage": "123456"}, "keys_count": "2",
            "damages_confirmed": True, "place": "Hannover",
            "created_at": "2026-09-10T09:00:00+00:00"}


def test_finalize_abbruch_raeumt_dateien_auf_und_meldet_retry():
    async def lauf(db, D, P, t):
        import pickup_pdf_service
        import storage_service
        appt_id, proto_id = f"a_{t.tag}", f"p_{t.tag}"
        await db.dealer_drivers.insert_one(t.link())
        await db.appointments.insert_one(t.appt(appt_id, "offen"))
        await db.pickup_protocols.insert_one(
            _vollstaendiger_entwurf(P, t, appt_id, proto_id))
        fin = P.FinalizeIn(signature_driver_b64=_PNG_B64, signature_seller_b64=_PNG_B64)

        keys = []
        alt_make_key = storage_service.make_key
        alt_build = pickup_pdf_service.build_pickup_pdf
        alt_delete = storage_service.delete_async

        def make_key_merken(*a, **k):
            key = alt_make_key(*a, **k)
            keys.append(key)
            return key

        def pdf_kaputt(**k):
            raise RuntimeError("PDF-Bibliothek explodiert")

        storage_service.make_key = make_key_merken
        pickup_pdf_service.build_pickup_pdf = pdf_kaputt
        try:
            # a) Loeschen klappt: Unterschriften sind hinterher weg
            with pytest.raises(RuntimeError):
                await P.finalize_protocol(appt_id, fin, t.driver)
            assert len(keys) == 2, keys
            assert all(not storage_service.storage.exists(k) for k in keys), keys
            p = await db.pickup_protocols.find_one({"id": proto_id}, {"_id": 0})
            assert p["status"] == "entwurf" and "claim_bis" not in p
            assert await db.storage_delete_retry.count_documents(
                {"dealer_id": t.dealer_id}) == 0

            # b) Loeschen schlaegt fehl: Retry-Zeile je Key
            async def delete_kaputt(key):
                raise OSError("Storage weg")
            storage_service.delete_async = delete_kaputt
            keys.clear()
            with pytest.raises(RuntimeError):
                await P.finalize_protocol(appt_id, fin, t.driver)
            assert len(keys) == 2
            rows = [r async for r in db.storage_delete_retry.find(
                {"dealer_id": t.dealer_id}, {"_id": 0})]
            assert sorted(r["key"] for r in rows) == sorted(keys), rows
            for r in rows:
                assert r["art"] == "key" and r["grund"] == "protokoll-rollback"
                assert r["versuche"] == 0 and "Storage weg" in r["letzter_fehler"]
                assert r["id"] and r["created_at"] and r["updated_at"]
            for k in keys:                      # Dateien tatsaechlich aufraeumen
                storage_service.storage.delete(k)
        finally:
            storage_service.make_key = alt_make_key
            pickup_pdf_service.build_pickup_pdf = alt_build
            storage_service.delete_async = alt_delete

    _run(lauf)


# ---------------------------------------------------------------------
# 8) Passwortregeln: zentrale Pruefung (10 Zeichen, Blockliste)
# ---------------------------------------------------------------------
def test_fahrer_passwortregeln_zentral():
    import routes.drivers as D
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        D.DriverAccountRegister(email="a@example.com", password="Kurz1234!",
                                display_name="Max")            # 9 Zeichen
    with pytest.raises(ValidationError):
        D.DriverAccountRegister(email="a@example.com", password="Passwort123!",
                                display_name="Max")            # Blockliste
    with pytest.raises(ValidationError):
        D.DriverPasswordIn(current_password="x", new_password="nurbuchstaben")
    ok = D.DriverAccountRegister(email="a@example.com",
                                 password="Sicher-Fahrt-2026", display_name="Max")
    assert ok.password == "Sicher-Fahrt-2026"
    assert D.DriverPasswordIn(current_password="x",
                              new_password="Neues-Passwort-77").new_password
