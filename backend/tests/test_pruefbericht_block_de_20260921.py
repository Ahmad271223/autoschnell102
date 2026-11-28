# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026, Bloecke D/E (21.09.2026).

  V-27  Archivfassung per Upsert: ein abgebrochener frueherer Lauf blockiert
        die Neuerzeugung nicht mehr (vorher DuplicateKeyError), der Verlierer
        eines Rennens loescht keine fremde Archivfassung
  V-26  "kein laufender Versand" steht im Compare-and-Set der Neuerzeugung;
        ein 409 beim Termin-Verschieben laesst den Termin nicht mit neuem und
        den Vertrag mit altem Datum stehen (Merker vertrag_veraltet + Nachholer)
  V-25  Vertrag nach Abholung idempotent: Doppeltipp/Nachholer nach einem
        Erfolg loesen keinen Dauer-Alarm aus; der Termin-Nachholer stellt den
        Vertragsstand sicher, bevor der Merker faellt
  V-29  Frischabgleich zieht Preis, Lebenszyklus (ohne Vorgang) und
        abgeschlossen_seit nach
  R1-29 Aufraeumen leert keine Fotos eines inzwischen uebernommenen Fahrzeugs
  V-05  Kein Entsperren von Konten/Firmen in Loeschung
  AL-01 halbe S3-Konfiguration = Backup unvollstaendig
  V-09/R1-39 Fahrer-Terminliste traegt updated_at und den Aenderungs-Hinweis
  AL-03 Alarme ohne Empfaenger werden sichtbar gemeldet

In-Prozess wie Runde 17 (Welt aus test_befunde_runde17_termine) bzw. gegen
eine Wegwerf-Datenbank fuer die Aufraeum-Laeufe.
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
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))
sys.path.insert(0, str(BACKEND / "scripts"))

from test_befunde_runde17_termine import _jetzt, _module, _put, welt  # noqa: E402,F401
from test_befunde_runde17_vertraege import _pdf_stub, _sammlung_hooken  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _vor(sekunden: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=sekunden)).isoformat()


def _stubs(monkeypatch):
    """PDF-Erzeugung und Auto-Daten-Nachfuehrung ersetzen."""
    aufrufe = _pdf_stub(monkeypatch)
    AD = _module("auto_daten")

    async def _nichts(*a, **k):
        return None
    monkeypatch.setattr(AD, "nachfuehren", _nichts)
    return aufrufe


async def _fassungs_index(db):
    try:
        await db.generated_pdf_versions.create_index(
            [("contract_id", 1), ("version", 1)], unique=True, name="vertragsfassung_eindeutig",
            partialFilterExpression={"contract_id": {"$type": "string"}})
    except Exception:  # noqa: BLE001  (steht schon, ggf. mit anderer Definition)
        pass


# ====================================================================== V-27
def test_v27_archiv_aus_abgebrochenem_lauf_blockiert_nicht(welt, monkeypatch):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    _stubs(monkeypatch)
    cid = f"c27_{w.s}"

    async def lauf():
        await _fassungs_index(db)
        await db.generated_pdfs.insert_one(w.vertrag(cid))
        # ein frueherer Lauf starb zwischen Archiv und Compare-and-Set
        await db.generated_pdf_versions.insert_one(
            {"id": f"alt_{w.s}", "contract_id": cid, "dealer_id": w.dealer_id,
             "version": 1, "archived_at": _jetzt()})
        erg = {}
        ok = await C.regenerate_contract_for_pickup(
            contract_id=cid, dealer_id=w.dealer_id, user=w.chef,
            pickup_date="2099-09-11", ergebnis=erg)
        c = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        v = await db.generated_pdf_versions.find({"contract_id": cid}, {"_id": 0}).to_list(10)
        return ok, erg, c, v

    ok, erg, c, v = welt.run(lauf())
    assert ok is True and erg == {"grund": "neu"}
    assert c["version"] == 2 and c["pickup_date"] == "2099-09-11"
    assert [x["id"] for x in v] == [f"alt_{w.s}"], "keine zweite Archivfassung, kein Fehler"


def test_v27_verlierer_loescht_keine_fremde_archivfassung(welt, monkeypatch):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    _stubs(monkeypatch)
    cid = f"c27b_{w.s}"

    async def _parallel(orig, self, *a, **k):
        filt = a[0] if a else k.get("filter")
        upd = a[1] if len(a) > 1 else k.get("update")
        if filt.get("id") == cid and "pdf_b64" in (upd.get("$set") or {}):
            # ein paralleler Lauf gewinnt genau jetzt
            await orig(self, {"id": cid}, {"$inc": {"version": 1}})
        return await orig(self, *a, **k)
    _sammlung_hooken(monkeypatch, "update_one", "generated_pdfs", _parallel)

    async def lauf():
        await _fassungs_index(db)
        await db.generated_pdfs.insert_one(w.vertrag(cid))
        await db.generated_pdf_versions.insert_one(
            {"id": f"fremd_{w.s}", "contract_id": cid, "dealer_id": w.dealer_id,
             "version": 1, "archived_at": _jetzt()})
        erg = {}
        ok = await C.regenerate_contract_for_pickup(
            contract_id=cid, dealer_id=w.dealer_id, user=w.chef,
            pickup_date="2099-09-12", ergebnis=erg)
        v = await db.generated_pdf_versions.find({"contract_id": cid}, {"_id": 0}).to_list(10)
        return ok, erg, v

    ok, erg, v = welt.run(lauf())
    assert ok is False and erg == {"grund": "cas_verloren"}
    assert [x["id"] for x in v] == [f"fremd_{w.s}"], "die Fassung des anderen Laufs bleibt"


# ====================================================================== V-26
def test_v26_versand_beginnt_zwischen_pruefung_und_schreiben(welt, monkeypatch):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    _stubs(monkeypatch)
    cid = f"c26_{w.s}"

    async def _reservierung_dazwischen(orig, self, *a, **k):
        filt = a[0] if a else k.get("filter")
        if filt.get("contract_id") == cid:
            # Die Versand-Reservierung kommt genau zwischen Vorpruefung und CAS.
            await db.generated_pdfs.update_one(
                {"id": cid}, {"$push": {"send_status": {
                    "idempotency_key": "k1", "channel": "email", "recipient": "a@b.de",
                    "zustellung": "laeuft", "sent_at": _jetzt(), "version": 1}}})
        return await orig(self, *a, **k)
    _sammlung_hooken(monkeypatch, "update_one", "generated_pdf_versions", _reservierung_dazwischen)

    async def lauf():
        await _fassungs_index(db)
        await db.generated_pdfs.insert_one(w.vertrag(cid))
        with pytest.raises(HTTPException) as e:
            await C.regenerate_contract_for_pickup(
                contract_id=cid, dealer_id=w.dealer_id, user=w.chef, pickup_date="2099-09-13")
        c = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        v = await db.generated_pdf_versions.count_documents({"contract_id": cid})
        return e.value, c, v

    fehler, c, v = welt.run(lauf())
    assert fehler.status_code == 409 and fehler.detail == C.VERSAND_LAEUFT_TEXT
    assert c["version"] == 1 and c["pickup_date"] == "2099-09-10", "keine neue Fassung mitten im Versand"
    assert v == 0, "die eigene Archivfassung wurde zurueckgenommen"


def test_v26_terminverschiebung_waehrend_versand_merkt_veralteten_vertrag(welt, monkeypatch):
    A, C = _module("routes.appointments"), _module("routes.contracts")
    CS = _module("cleanup_service")
    w, db = welt.w, welt.db
    aid, ca = f"a26_{w.s}", f"ca26_{w.s}"
    aufrufe = []

    async def _versand_laeuft(**k):
        aufrufe.append(k)
        raise HTTPException(409, C.VERSAND_LAEUFT_TEXT, headers={"Retry-After": "5"})
    monkeypatch.setattr(C, "regenerate_contract_for_pickup", _versand_laeuft)

    async def lauf():
        await db.generated_pdfs.insert_one(w.vertrag(ca, appointment_id=aid, status="Termin erstellt"))
        await db.appointments.insert_one(w.appt(aid, contract_id=ca))
        r = await _put(A, aid, w.chef, pickup_date="2099-09-20")
        a1 = await db.appointments.find_one({"id": aid}, {"_id": 0})

        # Der Aufraeumjob holt nach, sobald der Versand vorbei ist.
        async def _geht_wieder(**k):
            aufrufe.append(k)
            if k.get("contract_id") != ca:
                return False
            await db.generated_pdfs.update_one(
                {"id": ca}, {"$set": {"pickup_date": k["pickup_date"],
                                      "pickup_time": k["pickup_time"]}})
            return True
        monkeypatch.setattr(C, "regenerate_contract_for_pickup", _geht_wieder)
        await CS.vertraege_veraltet_nachholen(db)
        a2 = await db.appointments.find_one({"id": aid}, {"_id": 0})
        c2 = await db.generated_pdfs.find_one({"id": ca}, {"_id": 0})
        return r, a1, a2, c2

    r, a1, a2, c2 = welt.run(lauf())
    assert r["ok"] is True and r["contract_updated"] is False
    assert r["hinweis"] == A.VERTRAG_VERALTET_HINWEIS
    assert a1["pickup_date"] == "2099-09-20" and a1["vertrag_veraltet"] is True
    assert aufrufe[-1]["contract_id"] == ca and aufrufe[-1]["pickup_date"] == "2099-09-20"
    assert "vertrag_veraltet" not in a2, "der Nachholer hat die Fassung erzeugt"
    assert c2["pickup_date"] == "2099-09-20"


# ====================================================================== V-25
class _Regen:
    """Ersetzt regenerate_contract_for_pickup mit festgelegten Ergebnissen."""

    def __init__(self, *ergebnisse):
        self.ergebnisse = list(ergebnisse)
        self.aufrufe = []

    async def __call__(self, **kw):
        self.aufrufe.append(kw)
        ok, grund = self.ergebnisse.pop(0) if self.ergebnisse else (True, "neu")
        if kw.get("ergebnis") is not None:
            kw["ergebnis"]["grund"] = grund
        return ok


def _alarme(welt, cid):
    return welt.run(welt.db.betriebsalarme.count_documents(
        {"typ": "vertrag_nach_abholung_offen", "ref": cid, "offen": True}))


def test_v25_vertrag_nach_abholung_ist_idempotent(welt, monkeypatch):
    C, P = _module("routes.contracts"), _module("routes.protocols")
    w, db = welt.w, welt.db
    cid, pid = f"c25_{w.s}", f"p25_{w.s}"
    appt = {"id": f"a25_{w.s}", "dealer_id": w.dealer_id, "contract_id": cid,
            "created_by": w.chef["id"]}
    welt.run(db.generated_pdfs.insert_one(w.vertrag(cid, nach_abholung_protokoll_id=pid)))

    # 1) schon eingearbeitet (Merker der Neuerzeugung): nichts tun, kein Alarm
    rek = _Regen()
    monkeypatch.setattr(C, "regenerate_contract_for_pickup", rek)
    assert welt.run(P.vertrag_nach_abholung_aktualisieren(appt, pid, 9000.0, None)) is True
    assert rek.aufrufe == [] and _alarme(welt, cid) == 0

    # 2) andere Protokollversion, der Vertrag zeigt aber schon alles
    rek = _Regen((False, "keine_aenderung"))
    monkeypatch.setattr(C, "regenerate_contract_for_pickup", rek)
    assert welt.run(P.vertrag_nach_abholung_aktualisieren(appt, "p_neu", 9000.0, None)) is True
    assert len(rek.aufrufe) == 1 and _alarme(welt, cid) == 0

    # 3) verlorener Compare-and-Set: mit dem neuen Stand erneut versuchen
    rek = _Regen((False, "cas_verloren"), (True, "neu"))
    monkeypatch.setattr(C, "regenerate_contract_for_pickup", rek)
    assert welt.run(P.vertrag_nach_abholung_aktualisieren(appt, "p_neu", 9000.0, None)) is True
    assert len(rek.aufrufe) == 2 and _alarme(welt, cid) == 0

    # 4) echter Fehler: False + Alarm (wie bisher)
    rek = _Regen((False, "pdf_fehler"))
    monkeypatch.setattr(C, "regenerate_contract_for_pickup", rek)
    assert welt.run(P.vertrag_nach_abholung_aktualisieren(appt, "p_neu", 9000.0, None)) is False
    assert _alarme(welt, cid) == 1


def test_v25_termin_nachholer_stellt_den_vertrag_sicher(welt, monkeypatch):
    C, P = _module("routes.contracts"), _module("routes.protocols")
    CS = _module("cleanup_service")
    w, db = welt.w, welt.db
    aid, cid, pid = f"a25b_{w.s}", f"c25b_{w.s}", f"p25b_{w.s}"

    async def anlegen():
        await db.generated_pdfs.insert_one(w.vertrag(cid, appointment_id=aid, status="Termin erstellt"))
        await db.appointments.insert_one(w.appt(
            aid, contract_id=cid, status="abgeholt", status_changed_at=_vor(3600),
            nacharbeit_offen=True, nacharbeit_protokoll_id=pid, updated_at=_vor(3600)))
        await db.pickup_protocols.insert_one(w.entwurf(
            P, aid, pid, status="final", contract_id=cid, neuer_preis=9000.0,
            finalized_at=_vor(3600)))
    welt.run(anlegen())

    # Neuerzeugung scheitert -> Merker bleibt (vorher fiel er trotzdem)
    rek = _Regen((False, "pdf_fehler"))
    monkeypatch.setattr(C, "regenerate_contract_for_pickup", rek)
    welt.run(CS.termin_nacharbeit_nachholen(db, datetime.now(timezone.utc)))
    a1 = welt.run(db.appointments.find_one({"id": aid}, {"_id": 0}))
    assert rek.aufrufe and rek.aufrufe[0]["contract_id"] == cid
    assert rek.aufrufe[0]["neuer_preis"] == 9000.0 and rek.aufrufe[0]["protokoll_id"] == pid
    assert a1.get("nacharbeit_offen") is True, "ohne neue Vertragsfassung bleibt der Merker"

    # naechster Lauf: gelingt -> Merker weg
    rek2 = _Regen((True, "neu"))
    monkeypatch.setattr(C, "regenerate_contract_for_pickup", rek2)
    welt.run(db.appointments.update_one({"id": aid}, {"$set": {"updated_at": _vor(3600)}}))
    welt.run(CS.termin_nacharbeit_nachholen(db, datetime.now(timezone.utc)))
    a2 = welt.run(db.appointments.find_one({"id": aid}, {"_id": 0}))
    assert len(rek2.aufrufe) == 1 and "nacharbeit_offen" not in a2


# ====================================================================== Wegwerf-Datenbank
_MODULE_MIT_DB = ("deps", "routes.appointments", "routes.contracts", "routes.protocols",
                  "routes.drivers", "routes.bestand", "routes.admin", "lifecycle",
                  "kaufvorgang", "auto_daten", "cleanup_service")


@pytest.fixture
def frisch(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_de_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for n in _MODULE_MIT_DB:
        m = _module(n)
        if hasattr(m, "db"):
            monkeypatch.setattr(m, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def test_v29_frischabgleich_zieht_lebenszyklus_und_abschluss_nach(frisch):
    CS = _module("cleanup_service")
    db, run = frisch.db, frisch.run
    geschlossen = _vor(1800)

    async def anlegen():
        await db.vehicles.insert_one({"id": "v1", "dealer_id": "d1", "lifecycle": "abholung_geplant",
                                      "created_at": _jetzt()})
        # Termin OHNE Vertrag/Kaufvorgang, vom Fahrer abgeschlossen — die
        # Nacharbeit (Lebenszyklus, Frist) brach ab, der Merker fehlt.
        await db.appointments.insert_one({"id": "t1", "dealer_id": "d1", "vehicle_id": "v1",
                                          "status": "abgeholt", "status_changed_at": geschlossen,
                                          "updated_at": _jetzt(), "created_at": _jetzt()})
    run(anlegen())
    assert run(CS.termine_frisch_abgleichen(db, datetime.now(timezone.utc))) == 1
    v = run(db.vehicles.find_one({"id": "v1"}, {"_id": 0}))
    t = run(db.appointments.find_one({"id": "t1"}, {"_id": 0}))
    assert v["lifecycle"] == "abgeholt"
    assert t["abgeschlossen_seit"] == geschlossen


class _Fahrzeuge:
    """vehicles, bei der der Chef das Fahrzeug GENAU nach der Lebenszyklus-
    Pruefung des Aufraeumers in den Bestand uebernimmt."""

    def __init__(self, echt):
        self._echt = echt

    def __getattr__(self, name):
        return getattr(self._echt, name)

    async def find_one(self, filt, proj=None, *a, **k):
        doc = await self._echt.find_one(filt, proj, *a, **k)
        if proj == {"_id": 0, "lifecycle": 1}:
            await self._echt.update_one({"id": filt["id"]}, {"$set": {"lifecycle": "bestand"}})
        return doc


class _Db:
    def __init__(self, echt, **ersatz):
        self._echt, self._ersatz = echt, ersatz

    def __getattr__(self, name):
        if name in self._ersatz:
            return self._ersatz[name]
        return getattr(self._echt, name)

    def __getitem__(self, name):
        return getattr(self, name)


def test_r1_29_aufraeumen_leert_keine_fotos_eines_uebernommenen_fahrzeugs(frisch):
    CS = _module("cleanup_service")
    db, run = frisch.db, frisch.run
    alt = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()

    async def anlegen():
        await db.vehicles.insert_one({"id": "v1", "dealer_id": "d1", "lifecycle": "abgeholt",
                                      "data": {"images": ["https://bild/1.jpg"]},
                                      "created_at": _jetzt()})
        await db.appointments.insert_one({"id": "t1", "dealer_id": "d1", "vehicle_id": "v1",
                                          "status": "abgeholt", "status_changed_at": alt,
                                          "abgeschlossen_seit": alt, "created_at": alt})
    run(anlegen())
    run(CS._cleanup_once(_Db(db, vehicles=_Fahrzeuge(db.vehicles))))
    v = run(db.vehicles.find_one({"id": "v1"}, {"_id": 0}))
    t = run(db.appointments.find_one({"id": "t1"}, {"_id": 0}))
    assert v["lifecycle"] == "bestand"
    assert v["data"]["images"] == ["https://bild/1.jpg"], "Fotos des Bestandsfahrzeugs bleiben"
    assert t.get("cleanup_skipped") == "haendler_entscheidung"


def test_v05_kein_entsperren_waehrend_der_loeschung(frisch):
    AD = _module("routes.admin")
    db, run = frisch.db, frisch.run
    admin = {"id": "sa1", "role": "admin", "is_super_admin": True}

    async def anlegen():
        await db.dealers.insert_many([
            {"id": "d_weg", "user_id": "chef_weg", "loeschung": {"status": "laeuft"}},
            {"id": "d_ok", "user_id": "chef_ok"}])
        await db.users.insert_many([
            {"id": "u_weg", "dealer_id": "d_ok", "role": "sucher", "active": False,
             "loeschung": {"status": "laeuft"}},
            {"id": "u_firma_weg", "dealer_id": "d_weg", "role": "sucher", "active": False},
            {"id": "u_ok", "dealer_id": "d_ok", "role": "sucher", "active": False}])
    run(anlegen())
    for uid in ("u_weg", "u_firma_weg"):
        with pytest.raises(HTTPException) as e:
            run(AD.admin_user_set_active(uid, AD.AdminActiveIn(active=True), admin))
        assert e.value.status_code == 409, (uid, e.value.detail)
        assert run(db.users.find_one({"id": uid}))["active"] is False
    # Gegenprobe: normales Entsperren und Sperren in Loeschung gehen weiter
    run(AD.admin_user_set_active("u_ok", AD.AdminActiveIn(active=True), admin))
    assert run(db.users.find_one({"id": "u_ok"}))["active"] is True
    run(AD.admin_user_set_active("u_weg", AD.AdminActiveIn(active=False), admin))


# ====================================================================== AL-01
def test_al01_halbe_s3_konfiguration_ist_unvollstaendig(tmp_path, monkeypatch):
    from pymongo import MongoClient
    if not os.environ.get("MONGO_URL"):
        monkeypatch.setenv("MONGO_URL", MONGO_URL)
    import backup_service  # noqa: F401  (laedt .env — danach die Umgebung setzen)
    import backup_mongo as bm
    up, ls = tmp_path / "uploads", tmp_path / "local_storage"
    up.mkdir()
    ls.mkdir()
    monkeypatch.setattr(bm, "UPLOADS_DIR", up)
    monkeypatch.setattr(bm, "LOCAL_STORAGE_DIR", ls)
    for var in ("EMERGENT_LLM_KEY", "S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY",
                "BACKUP_S3_BUCKET", "BACKUP_S3_ENDPOINT"):
        monkeypatch.setenv(var, "")
    monkeypatch.setenv("S3_BUCKET", "nur-der-bucket")        # EINE von vier gesetzt
    monkeypatch.setenv("BACKUP_SNAPSHOT_PAUSE_S", "0")
    name = f"autoschnell_al01_{uuid.uuid4().hex[:8]}"
    c = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    try:
        c[name].users.insert_one({"id": "u1"})
        base = tmp_path / "b"
        rc = bm.backup_erstellen(base, db_name=name, mongo_url=MONGO_URL)
        log = (base / "backup.log").read_text(encoding="utf-8")
        assert rc == 2, log[-800:]
        assert "S3 nur teilweise konfiguriert" in log and "S3_ENDPOINT" in log
        assert "BACKUP UNVOLLSTAENDIG" in log and "BACKUP OK" not in log
    finally:
        c.drop_database(name)
        c.close()


# ====================================================================== V-09 / R1-39 / AL-03 / AD-06
def test_v09_fahrer_terminliste_traegt_stand_und_aenderungshinweis():
    q = inspect.getsource(_module("routes.drivers"))
    assert '"updated_at": a.get("updated_at"),' in q
    assert '"zuteilung_neu_wegen_aenderung": bool(a.get("zuteilung_neu_wegen_aenderung")),' in q


def test_al03_alarme_ohne_empfaenger_werden_gemeldet(monkeypatch):
    import production_check as PC
    q_server = (BACKEND / "server.py").read_text(encoding="utf-8")
    assert "BETRIEB_MELDUNG_AN ist leer" in q_server
    assert '"alarm_empfaenger":' in (BACKEND / "routes" / "admin.py").read_text(encoding="utf-8")
    assert "BETRIEB_MELDUNG_AN ist leer" in inspect.getsource(PC)


def test_ad06_notweg_ohne_zweiten_super_admin_ist_beschrieben():
    admin = (BACKEND / "routes" / "admin.py").read_text(encoding="utf-8")
    assert "muss ein anderer Super-Admin" not in admin, "es gibt bewusst nur EINEN Super-Admin"
    assert "mfa_pruefen.py --konto <name> --abschalten --ja" in admin
    doku = (BACKEND.parent / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "ein anderer Super-Admin setzt unter" not in doku
    assert (BACKEND / "scripts" / "mfa_pruefen.py").is_file()
