# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026 — Team Betrieb (Aufraeumlauf, Sicherung,
Restore, Speicher, Betriebsmeldungen, Skripte).

In-Prozess gegen Wegwerf-Datenbanken (autoschnell_rpb_<zufall>), kein
Server. Nummern = Befunde der Rollenpruefung (Duplikate in Klammern).
"""
import asyncio
import importlib
import inspect
import io
import json
import os
import sys
import threading
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
WURZEL = BACKEND.parent
SCRIPTS = BACKEND / "scripts"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SCRIPTS))
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _iso(delta_s: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


def _tage(n: float) -> str:
    return _iso(n * 86400)


def _m(name):
    return importlib.import_module(name)


@pytest.fixture
def welt():
    """Asynchrone Wegwerf-Datenbank (Motor) mit eigenem Event-Loop."""
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_rpb_{uuid.uuid4().hex[:10]}"
    w = SimpleNamespace(db=client[name], run=loop.run_until_complete, name=name)
    try:
        yield w
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


@pytest.fixture
def sync_db():
    """Synchrone Wegwerf-Datenbank (pymongo) fuer die Skripte."""
    from pymongo import MongoClient
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_rpb_s_{uuid.uuid4().hex[:10]}"
    try:
        yield client[name]
    finally:
        client.drop_database(name)
        client.close()


def _alarm_offen(w, typ, ref):
    return w.run(w.db.betriebsalarme.count_documents({"typ": typ, "ref": ref, "offen": True}))


# ====================================================================== RP-073 / RP-172
def _abholung_mit_versionen(w, s="v"):
    cid, aid = f"c_{s}", f"t_{s}"
    w.run(w.db.appointments.insert_one({"id": aid, "dealer_id": "d1", "contract_id": cid,
                                        "status": "abgeholt", "created_by": "u1"}))
    w.run(w.db.generated_pdfs.insert_one({"id": cid, "dealer_id": "d1"}))
    w.run(w.db.pickup_protocols.insert_many([
        {"id": f"p1_{s}", "appointment_id": aid, "status": "final", "superseded": True,
         "version": 1, "contract_id": cid, "neuer_preis": 1000},
        {"id": f"p2_{s}", "appointment_id": aid, "status": "final", "superseded": False,
         "version": 2, "contract_id": cid, "neuer_preis": 2000},
    ]))
    # Der Alarm nennt die ABGELOESTE Version v1
    w.run(w.db.betriebsalarme.insert_one({"typ": "vertrag_nach_abholung_offen", "ref": cid,
                                          "offen": True, "details": {"protokoll_id": f"p1_{s}"}}))
    return cid, aid


def test_rp073_nachholer_nimmt_die_aktuelle_protokollversion(welt, monkeypatch):
    CS, P = _m("cleanup_service"), _m("routes.protocols")
    cid, _aid = _abholung_mit_versionen(welt)
    aufrufe = []

    async def _korrekturen(appt, p):
        return {}, []

    async def _aktualisieren(appt, protokoll_id, preis, sonder, **kw):
        aufrufe.append((protokoll_id, preis))
        return True
    monkeypatch.setattr(P, "protokoll_korrekturen", _korrekturen)
    monkeypatch.setattr(P, "vertrag_nach_abholung_aktualisieren", _aktualisieren)
    assert welt.run(CS.vertrag_nach_abholung_nachholen(welt.db)) == 1
    assert aufrufe == [("p2_v", 2000)], "NIE mit Preis/Version von v1 neu erzeugen"
    assert _alarm_offen(welt, "vertrag_nach_abholung_offen", cid) == 0


def test_rp073_ohne_aktuelle_version_wird_nur_der_alarm_geschlossen(welt, monkeypatch):
    CS, P = _m("cleanup_service"), _m("routes.protocols")
    cid, _aid = _abholung_mit_versionen(welt, "w")
    welt.run(welt.db.pickup_protocols.update_one({"id": "p2_w"}, {"$set": {"superseded": True}}))
    aufrufe = []

    async def _aktualisieren(*a, **k):
        aufrufe.append(a)
        return True
    monkeypatch.setattr(P, "vertrag_nach_abholung_aktualisieren", _aktualisieren)
    assert welt.run(CS.vertrag_nach_abholung_nachholen(welt.db)) == 0
    assert aufrufe == []
    assert _alarm_offen(welt, "vertrag_nach_abholung_offen", cid) == 0


# ====================================================================== RP-084 / RP-183 / RP-519
def _inserat(w, lid, vid, status="veroeffentlicht", tage=-25, **extra):
    w.run(w.db.resale_listings.insert_one({
        "id": lid, "dealer_id": "d1", "vehicle_id": vid, "status": status,
        "photos": {"uploaded_keys": []}, "published_at": _tage(tage), **extra}))


def test_rp084_abgelaufenes_inserat_setzt_fahrzeug_und_anfragen_zurueck(welt):
    CS = _m("cleanup_service")
    w = welt
    w.run(w.db.vehicles.insert_one({"id": "v1", "dealer_id": "d1", "lifecycle": "veroeffentlicht",
                                    "bestand": {"expires_at": _tage(-100)}}))
    _inserat(w, "l1", "v1")
    w.run(w.db.listing_interest.insert_many([
        {"id": "i1", "listing_id": "l1", "status": "gegenangebot", "history": []},
        {"id": "i2", "listing_id": "l1", "status": "abgelehnt", "history": []}]))
    assert w.run(CS.abgelaufene_inserate_entfernen(w.db, datetime.now(timezone.utc))) == 1
    assert w.run(w.db.resale_listings.find_one({"id": "l1"})) is None, "Anzeige samt Fotos weg"
    v = w.run(w.db.vehicles.find_one({"id": "v1"}, {"_id": 0}))
    assert v["lifecycle"] == "bestand", "nicht mehr dauerhaft 'veroeffentlicht'"
    assert v["bestand"]["expires_at"] > _tage(45), "frische Bestandsfrist statt der alten"
    i1 = w.run(w.db.listing_interest.find_one({"id": "i1"}))
    assert i1["status"] == "abgelehnt" and i1["beendet_grund"] == "inserat_abgelaufen"
    # Das Fahrzeug laesst sich danach wieder inserieren (create_draft-Zustand)
    assert v["lifecycle"] in ("vertrag_erstellt", "gekauft", "abholung_geplant", "abgeholt",
                              "bestand", "verkaufsentwurf", "verkaufsbereit")


class _Inserate:
    """resale_listings, bei der ein Kaeufer das Inserat GENAU nach dem Lesen
    des Aufraeumers reserviert."""

    def __init__(self, echt):
        self._echt = echt

    def __getattr__(self, name):
        return getattr(self._echt, name)

    def find(self, *a, **k):
        echt = self._echt

        class _C:
            def __init__(self):
                self._cur = echt.find(*a, **k)

            def limit(self, n):
                self._cur = self._cur.limit(n)
                return self

            async def to_list(self, n):
                docs = await self._cur.to_list(n)
                for d in docs:
                    await echt.update_one({"id": d["id"]}, {"$set": {"status": "reserviert",
                                                                   "reserved_for": "k1"}})
                return docs
        return _C()


class _Db:
    def __init__(self, echt, **ersatz):
        self._echt, self._ersatz = echt, ersatz

    def __getattr__(self, name):
        return self._ersatz.get(name) or getattr(self._echt, name)

    def __getitem__(self, name):
        return getattr(self, name)


def test_rp084_reservierung_zwischen_lesen_und_loeschen_bleibt(welt):
    CS = _m("cleanup_service")
    w = welt
    w.run(w.db.vehicles.insert_one({"id": "v2", "dealer_id": "d1", "lifecycle": "veroeffentlicht"}))
    _inserat(w, "l2", "v2")
    w.run(w.db.listing_interest.insert_one({"id": "i3", "listing_id": "l2", "status": "akzeptiert"}))
    db = _Db(w.db, resale_listings=_Inserate(w.db.resale_listings))
    assert w.run(CS.abgelaufene_inserate_entfernen(db, datetime.now(timezone.utc))) == 0
    l2 = w.run(w.db.resale_listings.find_one({"id": "l2"}))
    assert l2 and l2["status"] == "reserviert", "die Reservierung faellt nicht mit weg"
    assert w.run(w.db.listing_interest.find_one({"id": "i3"}))["status"] == "akzeptiert"
    assert w.run(w.db.vehicles.find_one({"id": "v2"}))["lifecycle"] == "veroeffentlicht"


def test_rp183_nachholer_fuer_haengengebliebene_fahrzeuge(welt):
    CS = _m("cleanup_service")
    w = welt
    alt = _tage(-2)
    w.run(w.db.vehicles.insert_many([
        {"id": "va", "dealer_id": "d1", "lifecycle": "veroeffentlicht", "lifecycle_changed_at": alt},
        {"id": "vb", "dealer_id": "d1", "lifecycle": "reserviert", "lifecycle_changed_at": alt},
        {"id": "vc", "dealer_id": "d1", "lifecycle": "veroeffentlicht", "lifecycle_changed_at": alt},
        {"id": "vd", "dealer_id": "d1", "lifecycle": "veroeffentlicht",
         "lifecycle_changed_at": _iso(-60)},
        {"id": "ve", "dealer_id": "d1", "lifecycle": "veroeffentlicht", "lifecycle_changed_at": alt},
    ]))
    _inserat(w, "lb", "vb", status="reserviert")          # laufende Reservierung
    _inserat(w, "lc", "vc", status="verkauft")            # verkauft: nicht anfassen
    _inserat(w, "le", "ve", status="geloescht")           # nur geloeschtes Inserat
    n = w.run(CS.fahrzeuge_ohne_inserat_zuruecksetzen(w.db, datetime.now(timezone.utc)))
    stand = {v["id"]: v["lifecycle"]
             for v in w.run(w.db.vehicles.find({}, {"_id": 0}).to_list(20))}
    assert stand == {"va": "bestand", "vb": "reserviert", "vc": "veroeffentlicht",
                     "vd": "veroeffentlicht", "ve": "bestand"}, stand
    assert n == 2


# ====================================================================== RP-513
def test_rp513_akzeptierte_anfrage_einer_laufenden_reservierung_bleibt(welt):
    CS = _m("cleanup_service")
    w = welt
    alt = _tage(-90)
    _inserat(w, "lr", "vr", status="reserviert", reserved_for="k1")
    _inserat(w, "lv", "vv", status="verkauft")
    w.run(w.db.listing_interest.insert_many([
        {"id": "a_res", "listing_id": "lr", "status": "akzeptiert", "updated_at": alt},
        {"id": "a_alt", "listing_id": "lv", "status": "akzeptiert", "updated_at": alt},
        {"id": "ab_alt", "listing_id": "lr", "status": "abgelehnt", "updated_at": alt},
    ]))
    w.run(CS.marktplatz_rotieren(w.db, datetime.now(timezone.utc)))
    rest = sorted(d["id"] for d in w.run(w.db.listing_interest.find({}, {"_id": 0, "id": 1})
                                         .to_list(10)))
    assert rest == ["a_res"], rest


# ====================================================================== RP-243 / RP-394
def test_rp243_ein_kaputter_schritt_kippt_den_lauf_nicht(welt, monkeypatch):
    CS = _m("cleanup_service")
    w = welt
    gelaufen = []

    async def kaputt(db):
        raise RuntimeError("Datensatz kaputt")

    async def spaeter(db, now):
        gelaufen.append("frisch")
        return 0
    monkeypatch.setattr(CS, "auto_daten_reparieren", kaputt)
    monkeypatch.setattr(CS, "termine_frisch_abgleichen", spaeter)
    stats = w.run(CS._cleanup_once(w.db))
    assert gelaufen == ["frisch"], "Schritte NACH dem kaputten laufen weiter"
    assert stats["auto_daten_repariert"] == "fehler"
    assert stats["schritte_fehlgeschlagen"] == ["auto_daten_repariert"]
    assert _alarm_offen(w, "aufraeumschritt_fehlgeschlagen", "auto_daten_repariert") == 1
    bericht = w.run(w.db.system_reports.find_one({"typ": "aufraeumlauf"}))
    assert bericht["vollstaendig"] is False and "letzter_vollstaendiger_lauf" not in bericht

    async def heil(db):
        return 0
    monkeypatch.setattr(CS, "auto_daten_reparieren", heil)
    w.run(CS._cleanup_once(w.db))
    assert _alarm_offen(w, "aufraeumschritt_fehlgeschlagen", "auto_daten_repariert") == 0
    bericht = w.run(w.db.system_reports.find_one({"typ": "aufraeumlauf"}))
    assert bericht["vollstaendig"] is True and bericht["letzter_vollstaendiger_lauf"]


def test_rp394_ready_warnt_ohne_vollstaendigen_lauf():
    q = inspect.getsource(_m("server")._readiness_pruefen)
    assert "AUFRAEUMLAUF_WARN_H" in q and '"typ": "aufraeumlauf"' in q
    assert "Aufraeumlauf: kein vollstaendiger Lauf seit" in q


# ====================================================================== RP-245 / RP-396
def test_rp245_schreibpause_haelt_den_laufenden_lauf_an(welt, monkeypatch):
    CS = _m("cleanup_service")
    w = welt
    danach = []

    async def pause_setzen(db, now, stats):
        await db.system_flags.insert_one({"_id": "wartungsmodus", "aktiv": True,
                                          "umfang": "schreiben", "besitzer": "sicherung",
                                          "gilt_bis": _iso(600)})
        return 0

    async def darf_nicht(db, now, stats):
        danach.append(1)
        return 0
    monkeypatch.setattr(CS, "berichtsfotos_nach_frist_loeschen", pause_setzen)
    monkeypatch.setattr(CS, "berichte_nach_frist_loeschen", darf_nicht)
    with pytest.raises(CS.SchreibpauseAktiv):
        w.run(CS._cleanup_once(w.db))
    assert danach == [], "nach dem Setzen der Pause loescht der Lauf nichts mehr"
    q = inspect.getsource(CS.run_cleanup_forever)
    assert "SchreibpauseAktiv" in q and "hintergrund_schreibt()" in q and "_lauf_aktiv_setzen" in q


def test_rp396_hintergrundarbeit_zaehlt_als_schreiber():
    W = _m("wartung")
    vorher = W.hintergrund_offen()
    with W.hintergrund_schreibt():
        assert W.hintergrund_offen() == vorher + 1
        with pytest.raises(RuntimeError):
            with W.hintergrund_schreibt():
                raise RuntimeError("x")
        assert W.hintergrund_offen() == vorher + 1
    assert W.hintergrund_offen() == vorher
    q = inspect.getsource(_m("server").run_schreiber_melden_forever)
    assert "wartung.hintergrund_offen()" in q


def test_rp245_restore_wartet_auf_den_aufraeumlauf(sync_db, monkeypatch):
    import restore_mongo as RM
    monkeypatch.setenv("RESTORE_AUSLAUF_MIN_S", "0")
    monkeypatch.setenv("RESTORE_AUSLAUF_MAX_S", "1")
    # ohne Backend (keine Sperre) nichts abwarten
    assert RM.hintergrund_abwarten(sync_db) is True
    jetzt = datetime.now(timezone.utc)
    sync_db.job_locks.insert_one({"name": "cleanup-cycle", "acquired_at": jetzt,
                                  "expires_at": jetzt + timedelta(minutes=50),
                                  "lauf_aktiv": True})
    assert RM.hintergrund_abwarten(sync_db) is False, "laufender Lauf: Warnung nach Frist"
    sync_db.job_locks.update_one({"name": "cleanup-cycle"}, {"$set": {"lauf_aktiv": False}})
    sync_db.wartung_schreiber.insert_one({"_id": "p1", "offen": 0, "stand": datetime.now(timezone.utc)})
    assert RM.hintergrund_abwarten(sync_db) is True
    assert "hintergrund_abwarten(ziel)" in inspect.getsource(RM.wiederherstellen)


# ====================================================================== RP-246 / RP-397
def test_rp246_setzen_ueberschreibt_keinen_fremden_merker(sync_db):
    W = _m("wartung")
    coll = sync_db[W.FLAG_COLLECTION]
    # frei -> gesetzt
    k = W.setzen(coll, "Sicherung")
    assert k and coll.find_one({"_id": W.FLAG_ID})["besitzer"] == k
    # eigener Merker darf erneuert werden
    assert W.setzen(coll, "Sicherung", kennung=k) == k
    # Restore-Merker (ohne Ablaufzeit, umfang alles) bleibt
    coll.replace_one({"_id": W.FLAG_ID}, {"_id": W.FLAG_ID, "aktiv": True, "umfang": "alles",
                                          "besitzer": "restore", "grund": "Restore"})
    assert W.setzen(coll, "Sicherung") is None
    doc = coll.find_one({"_id": W.FLAG_ID})
    assert doc["besitzer"] == "restore" and doc["umfang"] == "alles"
    # abgelaufener fremder Merker darf ersetzt werden
    coll.update_one({"_id": W.FLAG_ID}, {"$set": {"gilt_bis": _iso(-60)}})
    assert W.setzen(coll, "Sicherung")
    # zwang ueberschreibt immer
    coll.replace_one({"_id": W.FLAG_ID}, {"_id": W.FLAG_ID, "aktiv": True, "besitzer": "x",
                                          "gilt_bis": _iso(600)})
    assert W.setzen(coll, "Restore", umfang=W.UMFANG_ALLES, zwang=True)


def test_rp246_sicherung_startet_nicht_waehrend_eines_restores(sync_db, tmp_path, monkeypatch):
    _m("backup_service")          # laedt .env — danach gilt die Umgebung des Tests
    import backup_mongo as bm
    sync_db.users.insert_one({"id": "u1"})
    sync_db.system_flags.insert_one({"_id": "wartungsmodus", "aktiv": True, "umfang": "alles",
                                     "besitzer": "restore"})
    rc = bm.backup_erstellen(tmp_path / "b", db_name=sync_db.name, mongo_url=MONGO_URL)
    log = (tmp_path / "b" / "backup.log").read_text(encoding="utf-8")
    assert rc == 1 and "Wartungsmodus 'alles' aktiv" in log, log[-600:]
    assert not [p for p in (tmp_path / "b").iterdir() if p.name.startswith("autoschnell-")]
    assert sync_db.system_flags.find_one({"_id": "wartungsmodus"})["besitzer"] == "restore"


# ====================================================================== RP-241 / RP-392
def test_rp241_frischabgleich_uebernimmt_keinen_veralteten_status(welt, monkeypatch):
    CS, P, KV = _m("cleanup_service"), _m("routes.protocols"), _m("kaufvorgang")
    w = welt
    w.run(w.db.appointments.insert_one({"id": "tf", "dealer_id": "d1", "status": "abgeholt",
                                        "updated_at": _iso(-60)}))
    uebernommen = []

    async def _preis(appt, *a, **k):
        # parallel: der Chef storniert (V-12) zwischen Lesen und Statusuebernahme
        await w.db.appointments.update_one({"id": appt["id"]}, {"$set": {
            "status": "storniert", "updated_at": _iso()}})

    async def _status(appt, status):
        uebernommen.append(status)
        return True
    monkeypatch.setattr(P, "preis_nachholen", _preis)
    monkeypatch.setattr(KV, "termin_status_uebernehmen", _status)
    w.run(CS.termine_frisch_abgleichen(w.db, datetime.now(timezone.utc)))
    assert uebernommen == [], "der alte Status 'abgeholt' darf nicht in den Vorgang"
    # naechster Lauf: jetzt mit dem neuen Stand
    monkeypatch.setattr(P, "preis_nachholen", lambda appt, *a, **k: asyncio.sleep(0))
    w.run(CS.termine_frisch_abgleichen(w.db, datetime.now(timezone.utc)))
    assert uebernommen == ["storniert"]


# ====================================================================== RP-242 / RP-393
def test_rp242_fahrerabgleich_sieht_auch_termine_hinter_den_ersten_500(welt):
    CS = _m("cleanup_service")
    w = welt
    w.run(w.db.dealer_drivers.insert_one({"dealer_id": "d1", "driver_account_id": "f_ok"}))
    w.run(w.db.appointments.insert_many([
        {"id": f"ok{i}", "dealer_id": "d1", "driver_id": "f_ok", "status": "offen",
         "zuteilung": "angenommen"} for i in range(600)]))
    w.run(w.db.appointments.insert_one({"id": "weg", "dealer_id": "d1", "driver_id": "f_weg",
                                        "status": "offen", "zuteilung": "angenommen"}))
    w.run(CS.fahrer_verknuepfung_abgleichen(w.db))
    weg = w.run(w.db.appointments.find_one({"id": "weg"}))
    assert "driver_id" not in weg, "der entfernte Fahrer bleibt nicht dauerhaft zugeteilt"
    assert w.run(w.db.appointments.count_documents({"driver_id": "f_ok"})) == 600


# ====================================================================== RP-244 / RP-395
def test_rp244_frist_laeuft_ab_dem_abschluss_der_abholung(welt):
    CS = _m("cleanup_service")
    w = welt
    cutoff = _tage(-60)
    w.run(w.db.appointments.insert_many([
        {"id": "ta", "contract_id": "ca", "status": "abgeholt", "abgeschlossen_seit": _iso(-60)},
        {"id": "ts", "contract_id": "cs", "status": "storniert", "abgeschlossen_seit": _iso(-60)},
        {"id": "tt", "contract_id": "ct", "status": "abgeholt", "status_changed_at": _tage(-1)},
        {"id": "tx", "contract_id": "cx", "status": "abgeholt", "abgeschlossen_seit": _tage(-90)},
    ]))
    w.run(w.db.kaufvorgaenge.insert_one({"id": "kk", "contract_id": "ck", "status": "abgeholt",
                                         "updated_at": _tage(-2)}))
    ergebnis = {c: w.run(CS.vertrag_noch_in_gebrauch(w.db, c, cutoff))
                for c in ("ca", "cs", "ct", "cx", "ck")}
    assert ergebnis == {"ca": True, "cs": False, "ct": True, "cx": False, "ck": True}, ergebnis


# ====================================================================== RP-247 / RP-398
def test_rp247_trockenlauf_zaehlt_statt_alles_zu_laden(welt, monkeypatch):
    CS = _m("cleanup_service")
    w = welt
    monkeypatch.setattr(CS, "_LOESCHVORSCHAU_MAX_IDS", 3)
    w.run(w.db.generated_pdfs.insert_many([
        {"id": f"alt{i}", "created_at": _tage(-100 - i), "contract_data": {"x": "y" * 100}}
        for i in range(5)]))
    stats = {}
    assert w.run(CS.vertraege_nach_frist_loeschen(w.db, datetime.now(timezone.utc),
                                                  aktiv=False, stats=stats)) == 0
    r = w.run(w.db.system_reports.find_one({"typ": "vertrag_loeschvorschau"}))
    assert r["anzahl"] == 5 and len(r["ids"]) == 3 and stats["contracts_vorschau"] == 5


def test_rp247_scharf_laedt_die_vertragsfassung_nur_fuer_die_reparatur(welt, monkeypatch):
    CS, AD = _m("cleanup_service"), _m("auto_daten")
    w = welt
    w.run(w.db.generated_pdfs.insert_one({"id": "cr", "dealer_id": "d1", "created_at": _tage(-100),
                                          "contract_data": {"seller_name": "V"}}))
    gesehen = []

    async def _nachfuehren(db, c):
        gesehen.append(c)
        return False
    monkeypatch.setattr(AD, "nachfuehren", _nachfuehren)
    stats = {}
    w.run(CS.vertraege_nach_frist_loeschen(w.db, datetime.now(timezone.utc), aktiv=True, stats=stats))
    assert gesehen and gesehen[0]["contract_data"] == {"seller_name": "V"}
    assert stats["contracts_uebersprungen"] == 1
    assert _alarm_offen(w, "vertrag_ohne_auto_daten", "cr") == 1


# ====================================================================== RP-248 / RP-399
def test_rp248_offene_termine_behalten_ihre_personendaten(welt, monkeypatch):
    CS = _m("cleanup_service")
    w = welt
    monkeypatch.setenv("VERTRAG_LOESCHUNG_AKTIV", "true")
    alt = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
    w.run(w.db.appointments.insert_many([
        {"id": "offen", "status": "offen", "seller_name": "Anna", "pickup_date": alt,
         "created_at": _tage(-95)},
        {"id": "frisch_zu", "status": "abgeholt", "seller_name": "Bert", "pickup_date": alt,
         "created_at": _tage(-95), "abgeschlossen_seit": _tage(-5)},
        {"id": "alt_zu", "status": "nicht abgeholt", "seller_name": "Carl", "pickup_date": alt,
         "created_at": _tage(-95), "abgeschlossen_seit": _tage(-80)},
    ]))
    assert w.run(CS.termine_ohne_vertrag_bereinigen(w.db, datetime.now(timezone.utc))) == 1
    namen = {a["id"]: a["seller_name"] for a in
             w.run(w.db.appointments.find({}, {"_id": 0}).to_list(10))}
    assert namen == {"offen": "Anna", "frisch_zu": "Bert", "alt_zu": ""}, namen


# ====================================================================== RP-250 / RP-401
def test_rp250_kaufdatum_nur_ueber_vorhandene_vertraege(welt):
    CS, AD = _m("cleanup_service"), _m("auto_daten")
    w = welt
    w.run(w.db[AD.COLLECTION].insert_many([
        {"id": "avd_mit"}, {"id": "avd_ohne_vertrag"}, {"id": "avd_hat", "purchase_date": "2026-01-01"}]))
    w.run(w.db.generated_pdfs.insert_many([
        {"id": "c1", "admin_vehicle_data_id": "avd_mit", "created_at": "2026-09-01T10:00:00+00:00"},
        {"id": "c2", "admin_vehicle_data_id": "avd_hat", "created_at": "2026-09-02T10:00:00+00:00"}]))
    assert w.run(CS.auto_daten_reparieren(w.db)) == 1
    d = {x["id"]: x for x in w.run(w.db[AD.COLLECTION].find({}, {"_id": 0}).to_list(10))}
    assert d["avd_mit"]["purchase_date"] == "2026-09-01"
    assert d["avd_hat"]["purchase_date"] == "2026-01-01"
    assert d["avd_ohne_vertrag"] == {"id": "avd_ohne_vertrag"}, "Datensaetze ohne Vertrag unberuehrt"
    q = inspect.getsource(CS.auto_daten_reparieren)
    assert '{"purchase_date": {"$exists": False}}, {"_id": 0, "id": 1})]' not in q


# ====================================================================== RP-233 / RP-384
def _anfrage(token: str):
    from starlette.requests import Request
    return Request({"type": "http", "method": "GET", "path": "/api/ready",
                    "client": ("203.0.113.7", 4321), "query_string": b"",
                    "headers": [(b"authorization", f"Bearer {token}".encode())]})


def test_rp233_ready_nur_mit_gueltiger_sitzung(welt, monkeypatch):
    server, auth = _m("server"), _m("auth")
    monkeypatch.setattr(server, "db", welt.db)
    sa = {"id": "sa_rp", "username": "betreiber", "role": "admin", "is_super_admin": True,
          "active": True, "current_session_id": "sid-neu", "password_hash": "h"}
    welt.run(welt.db.users.insert_one(dict(sa)))
    darf = lambda t: welt.run(server._darf_betriebsdaten_sehen(_anfrage(t)))  # noqa: E731
    assert darf(auth.create_token("sa_rp", "sid-neu")) is True
    assert darf(auth.create_token("sa_rp", "sid-alt")) is False, "abgemeldete Sitzung"
    assert darf(auth.create_mfa_token(sa)) is False, "Zwischen-Token ohne zweiten Faktor"


# ====================================================================== RP-547
class _FehlendesObjekt(Exception):
    def __init__(self):
        super().__init__("NoSuchKey")
        self.response = {"Error": {"Code": "NoSuchKey"},
                         "ResponseMetadata": {"HTTPStatusCode": 404}}


def test_rp547_fehlende_datei_im_objektspeicher_ist_404():
    ST = _m("storage_service")
    speicher = object.__new__(ST.S3Storage)
    speicher.bucket = "b"

    class _Client:
        def get_object(self, **k):
            raise _FehlendesObjekt()
    speicher.client = _Client()
    with pytest.raises(ST.StorageError):
        speicher.load("resale/d1/x.jpg")

    class _Kaputt:
        def get_object(self, **k):
            raise RuntimeError("Zugriff verweigert")
    speicher.client = _Kaputt()
    with pytest.raises(RuntimeError):
        speicher.load("resale/d1/x.jpg")


def test_rp547_backendfehler_werden_zusammengefasst(welt, monkeypatch):
    server = _m("server")
    from starlette.requests import Request
    monkeypatch.setattr(server, "db", welt.db)
    mw = server.ErrorReportingMiddleware(app=lambda *a: None)

    async def kaputt(_request):
        raise RuntimeError("Objekt fehlt")

    def req():
        return Request({"type": "http", "method": "GET", "path": "/api/files/logo/x.png",
                        "client": ("1.2.3.4", 1), "query_string": b"", "headers": []})
    for _ in range(3):
        antwort = welt.run(mw.dispatch(req(), kaputt))
        assert antwort.status_code == 500
    docs = welt.run(welt.db.error_logs.find({}, {"_id": 0}).to_list(10))
    assert len(docs) == 1 and docs[0]["anzahl"] == 3, docs
    # Obergrenze: ab ERROR_LOG_MAX nichts Neues mehr
    monkeypatch.setenv("ERROR_LOG_MAX", "1")

    async def anders(_request):
        raise ValueError("anderer Fehler")
    welt.run(mw.dispatch(req(), anders))
    assert welt.run(welt.db.error_logs.count_documents({})) == 1


# ====================================================================== RP-550
def test_rp550_dateizugriffe_im_eigenen_pool():
    ST = _m("storage_service")
    name = asyncio.run(ST.speicher_aufruf(lambda: threading.current_thread().name))
    assert name.startswith("speicher"), name
    q = inspect.getsource(_m("server")._readiness_pruefen)
    assert "asyncio.wait_for(speicher_aufruf(head), timeout=5)" in q


# ====================================================================== RP-543
def test_rp543_neuer_betreiber_bekommt_gnadenfrist(welt, monkeypatch):
    server = _m("server")
    for mod in ("deps", "server"):
        monkeypatch.setattr(_m(mod), "db", welt.db)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("SUPER_ADMIN_USERNAME", "betreiber-rp")
    monkeypatch.setenv("SUPER_ADMIN_PASSWORD", "Kq4Lm9Xw2-Sicher!")
    welt.run(server.seed_super_admin())
    sa = welt.run(welt.db.users.find_one({"username": "betreiber-rp"}))
    frist = sa["mfa"]["pflicht_ausgesetzt_bis"]
    assert sa["mfa"]["aktiv"] is False
    assert _iso(50 * 60) < frist < _iso(70 * 60), frist
    # ein bestehendes Konto bekommt beim naechsten Start KEINE neue Frist
    welt.run(welt.db.users.update_one({"id": sa["id"]}, {"$unset": {"mfa": ""}}))
    welt.run(server.seed_super_admin())
    assert "mfa" not in welt.run(welt.db.users.find_one({"id": sa["id"]}))


# ====================================================================== RP-249 / RP-400
def test_rp249_zeiten_in_deutscher_zeit(monkeypatch):
    BM = _m("betriebsmeldung")
    assert BM._zeitpunkt("2026-09-20T11:40:37+00:00") == "20.09.2026 13:40"   # MESZ
    assert BM._zeitpunkt("2026-01-15T11:40:00+00:00") == "15.01.2026 12:40"   # MEZ
    # Image ohne tzdata: dieselbe Rechnung von Hand
    import zoneinfo

    def ohne(_name):
        raise zoneinfo.ZoneInfoNotFoundError("kein tzdata")
    monkeypatch.setattr(zoneinfo, "ZoneInfo", ohne)
    assert BM._zeitpunkt("2026-09-20T11:40:37+00:00") == "20.09.2026 13:40"
    assert BM._zeitpunkt("2026-01-15T11:40:00+00:00") == "15.01.2026 12:40"
    assert BM._zeitpunkt("2026-03-29T00:59:00+00:00") == "29.03.2026 01:59"   # vor der Umstellung
    assert BM._zeitpunkt("2026-03-29T01:00:00+00:00") == "29.03.2026 03:00"   # danach
    _b, text = BM.alarm_text([{"typ": "x", "created_at": "2026-09-20T11:40:00+00:00"}], 1)
    assert BM.ZEITZONE_HINWEIS in text
    _b, text = BM.bericht_text({"backup": {}}, "20.09.2026")
    assert BM.ZEITZONE_HINWEIS in text


def test_rp249_sammelsperre_wird_nicht_sofort_freigegeben():
    BM = _m("betriebsmeldung")
    code = "\n".join(z.split("#", 1)[0] for z in
                     inspect.getsource(BM.run_betriebsmeldung_forever).splitlines())
    assert 'acquire(db, "betriebsmeldung", ttl_seconds=takt)' in code
    assert "release(" not in code, "die Sperre laeuft mit der Sammelfrist ab"
    assert "_jetzt_berlin()" in code, "Stunde des Tagesberichts in deutscher Zeit"


# ====================================================================== RP-544 / RP-545
def _meta(ordner: Path, name: str, indexe: list) -> Path:
    p = ordner / f"{name}.metadata.json"
    p.write_text(json.dumps({"indexes": indexe}), encoding="utf-8")
    return p


def test_rp544_ttl_indexe_erst_nach_der_kontrolle(sync_db, tmp_path):
    import restore_mongo as RM
    meta = _meta(tmp_path, "rate_limits", [
        {"v": 2, "key": {"_id": 1}, "name": "_id_"},
        {"v": 2, "key": {"ablauf": 1}, "name": "ablauf_ttl", "expireAfterSeconds": 0}])
    dumps = {"rate_limits": ([{"_id": 1, "ablauf": datetime(2000, 1, 1)}], meta)}
    coll = sync_db.rate_limits
    RM.dokumente_laden(coll, dumps["rate_limits"][0])
    assert RM.indexe_anlegen(coll, meta, ttl_spaeter=True) == []
    info = coll.index_information()["ablauf_ttl"]
    assert "expireAfterSeconds" not in info, "in der Kopie noch KEIN TTL-Waechter"
    assert RM.pruefe_datenbank(sync_db, dumps, None, ttl_ausstehend=True) == []
    abw = RM.pruefe_datenbank(sync_db, dumps, None)
    assert any("expireAfterSeconds" in a for a in abw), abw
    assert RM.ttl_aktivieren(sync_db, dumps) == []
    assert coll.index_information()["ablauf_ttl"]["expireAfterSeconds"] == 0
    assert RM.pruefe_datenbank(sync_db, dumps, None, nur_indexe=True) == []
    q = inspect.getsource(RM.wiederherstellen)
    assert "ttl_spaeter=True" in q and "ttl_aktivieren(ziel, dumps)" in q
    assert q.index("ttl_ausstehend=True)\n            if abweichungen") < q.index("ttl_aktivieren(ziel, dumps)")


def test_rp545_restore_liest_die_sicherung_stueckweise(tmp_path, sync_db, monkeypatch):
    import gzip

    import bson
    import restore_mongo as RM
    db_dir = tmp_path / "sicherung" / "autoschnell"
    db_dir.mkdir(parents=True)
    with gzip.open(db_dir / "users.bson.gz", "wb") as fh:
        for i in range(7):
            fh.write(bson.encode({"_id": i, "id": f"u{i}"}))
    _meta(db_dir, "users", [{"v": 2, "key": {"_id": 1}, "name": "_id_"}])
    dumps, _m_, _d = RM.pruefe_backup(tmp_path / "sicherung", allow_no_manifest=True)
    docs, _pfad = dumps["users"]
    assert isinstance(docs, RM.BsonDatei) and len(docs) == 7
    assert [d["id"] for d in docs][:2] == ["u0", "u1"], "Dokumente kommen aus der Datei"
    pakete = []
    echt = type(sync_db.users).insert_many

    def zaehlen(self, dokumente, *a, **k):
        pakete.append(len(dokumente))
        return echt(self, dokumente, *a, **k)
    monkeypatch.setattr(type(sync_db.users), "insert_many", zaehlen)
    monkeypatch.setattr(RM, "LADE_PAKET", 3)
    assert RM.dokumente_laden(sync_db.users, docs) == 7
    assert pakete == [3, 3, 1]
    assert sync_db.users.count_documents({}) == 7


# ====================================================================== RP-552
def test_rp552_rueckstand_wird_gemessen():
    import backup_mongo as bm

    class _Admin:
        def __init__(self, antwort):
            self.antwort = antwort

        def command(self, name):
            if isinstance(self.antwort, Exception):
                raise self.antwort
            return self.antwort
    jetzt = datetime(2026, 9, 22, 12, 0, 0)
    c = SimpleNamespace(admin=_Admin({"optimes": {
        "lastAppliedWallTime": jetzt, "lastCommittedWallTime": jetzt - timedelta(hours=2)}}))
    assert bm.mehrheit_rueckstand_s(c) == 7200
    assert bm.mehrheit_rueckstand_s(SimpleNamespace(admin=_Admin(RuntimeError("standalone")))) is None


def test_rp552_veralteter_commitpunkt_kein_stiller_snapshot(sync_db, tmp_path, monkeypatch):
    import backup_mongo as bm
    sync_db.users.insert_many([{"id": "a"}, {"id": "b"}])
    monkeypatch.setattr(bm, "ist_replica_set", lambda client: True)
    monkeypatch.setattr(bm, "mehrheit_rueckstand_s", lambda client: 3600.0)

    class _Client:
        def start_session(self, **k):
            raise AssertionError("kein Snapshot bei stehendem Commitpunkt")
    target = tmp_path / "t"
    target.mkdir()
    counts, konsistenz, inkonsistent = bm.dump_datenbank(
        _Client(), sync_db, ["users"], target, tmp_path / "log.txt")
    assert counts == {"users": 2}
    assert konsistenz == bm.KONSISTENZ_RUECKFALL
    assert "Mehrheits-Commitpunkt 60 min" in inkonsistent


# ====================================================================== RP-548 / RP-560
def _env_erzeugen(argv, monkeypatch) -> str:
    import env_erzeugen as E
    monkeypatch.setattr(sys, "argv", ["env_erzeugen.py", *argv])
    puffer = io.StringIO()
    with redirect_stdout(puffer):
        assert E.main() == 0
    return puffer.getvalue()


def _werte(text: str) -> dict:
    return dict(z.split("=", 1) for z in text.splitlines() if "=" in z and not z.startswith("#"))


def test_rp548_neue_env_nennt_alle_pflichtwerte(monkeypatch):
    w = _werte(_env_erzeugen(["--domain", "app.example.de"], monkeypatch))
    assert w["APIFY_TOKEN"] == "BITTE-AUSFUELLEN"
    assert w["BACKUP_S3_BUCKET"] == "BITTE-AUSFUELLEN"
    assert len(w["DATEN_SCHLUESSEL"]) == 64 and w["DATEN_SCHLUESSEL"] != w["JWT_SECRET"]


def test_rp560_vorlage_verliert_nichts(tmp_path, monkeypatch):
    vorlage = tmp_path / ".env"
    vorlage.write_text("\n".join([
        "PUBLIC_HOST=alt.example.de", "JWT_SECRET=j" * 1, "DATEN_SCHLUESSEL=geheim123",
        "MONGO_EXTRA_ARGS=--replSet rs0 --keyFile /etc/mongo-keyfile",
        "VERTRAG_LOESCHUNG_AKTIV=true", "WEB_CONCURRENCY=8", "MONGO_CACHE_GB=6",
        "BACKUP_S3_ACCESS_KEY=bk", "COMPOSE_FILE=docker-compose.yml:deploy/x.yml",
        "APIFY_TOKEN=apify_echt", "BACKUP_S3_BUCKET=sicherung"]), encoding="utf-8")
    text = _env_erzeugen(["--domain", "neu.example.de", "--vorlage", str(vorlage)], monkeypatch)
    w = _werte(text)
    assert w["PUBLIC_HOST"] == "neu.example.de", "die Domain der Kommandozeile gilt"
    assert w["DATEN_SCHLUESSEL"] == "geheim123"
    assert w["MONGO_EXTRA_ARGS"] == "--replSet rs0 --keyFile /etc/mongo-keyfile"
    assert w["VERTRAG_LOESCHUNG_AKTIV"] == "true" and w["WEB_CONCURRENCY"] == "8"
    assert w["MONGO_CACHE_GB"] == "6"
    assert w["BACKUP_S3_ACCESS_KEY"] == "bk" and w["COMPOSE_FILE"].endswith("x.yml")
    assert w["APIFY_TOKEN"] == "apify_echt" and w["BACKUP_S3_BUCKET"] == "sicherung"
    assert "Uebernommen aus der Vorlage" in text
    assert text.count("\nWEB_CONCURRENCY=") + text.startswith("WEB_CONCURRENCY=") == 1


# ====================================================================== RP-551
def test_rp551_betreiber_passwort_auf_der_konsole(sync_db, monkeypatch):
    import betreiber_passwort_setzen as BP
    from auth import hash_password, verify_password
    monkeypatch.delenv("SUPER_ADMIN_PASSWORD", raising=False)
    sync_db.users.insert_one({"id": "sa1", "username": "betreiber-x", "role": "admin",
                              "is_super_admin": True, "active": True,
                              "password_hash": hash_password("Altes-Passwort-2026"),
                              "current_session_id": "sid"})
    raus = io.StringIO()
    with redirect_stdout(raus):
        assert BP.main(["--konto", "betreiber-x"], db=sync_db) == 2
        assert BP.main(["--konto", "gibt-es-nicht", "--ja"], db=sync_db) == 1
        assert BP.main(["--konto", "betreiber-x", "--ja"], db=sync_db,
                       eingabe=lambda: "kurz") == 1
    u = sync_db.users.find_one({"id": "sa1"})
    assert verify_password("Altes-Passwort-2026", u["password_hash"]), "abgelehnt = unveraendert"
    from anmeldesperre_aufheben import fenster_sekunden, schluessel
    sync_db.rate_limits.insert_many([{"_id": k, "n": 40} for k in
                                     schluessel("betreiber-x", fenster_sekunden())])
    with redirect_stdout(raus):
        assert BP.main(["--konto", "betreiber-x", "--ja"], db=sync_db,
                       eingabe=lambda: "Neues-Sicheres-2026!") == 0
    u = sync_db.users.find_one({"id": "sa1"})
    assert verify_password("Neues-Sicheres-2026!", u["password_hash"])
    assert u["current_session_id"] is None, "alte Sitzung beendet"
    assert sync_db.rate_limits.count_documents({"n": 40}) == 0, "Anmeldesperre aufgehoben"
    assert sync_db.activity_logs.find_one({"action": "auth.passwort.gesetzt.betreiber_konsole"})
    doku = (WURZEL / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "scripts/betreiber_passwort_setzen.py --konto" in doku


# ====================================================================== RP-559
@pytest.mark.skipif(not __import__("shutil").which("sh"), reason="kein sh vorhanden")
def test_rp559_rollout_raeumt_nur_nach_erfolg_auf(tmp_path):
    from tests.test_rollout import _skript_lauf
    rc, out, _marker, aufrufe = _skript_lauf(tmp_path / "ok", "rollout.sh")
    assert rc == 0, out
    probe = next(i for i, a in enumerate(aufrufe) if "betriebsprobe" in a)
    prune = [i for i, a in enumerate(aufrufe) if "image prune -f" in a]
    assert prune and prune[0] > probe, aufrufe
    assert any("builder prune -f --filter until=168h" in a for a in aufrufe)
    rc, out, _marker, aufrufe = _skript_lauf(tmp_path / "rot", "rollout.sh",
                                             extra_env={"FAKE_PROBE_RC": "1"})
    assert rc == 3 and not any("prune" in a for a in aufrufe), aufrufe


# ====================================================================== Kompose / Container
def test_neue_schalter_erreichen_den_container():
    from tests.test_haertung_20260919 import _compose_umgebung
    umgebung = _compose_umgebung()
    for name in ("AUFRAEUMLAUF_WARN_H", "BACKUP_MEHRHEIT_RUECKSTAND_MAX_S",
                 "RESTORE_AUSLAUF_MIN_S", "RESTORE_AUSLAUF_MAX_S", "S3_VERBINDUNG_TIMEOUT_S",
                 "S3_LESE_TIMEOUT_S", "S3_VERSUCHE", "SPEICHER_THREADS", "SEED_MFA_FRIST_MIN"):
        assert name in umgebung, name
