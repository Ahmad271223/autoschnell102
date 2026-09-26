# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026 (V-12), Entscheidung Ahmad 21.09.2026:
"Darf der Chef einen Termin mit unterschriebenem Abholprotokoll noch auf
'storniert' oder 'nicht abgeholt' setzen? — ja".

Umsetzung (hier festgehalten):
  * ohne ausdrueckliche Bestaetigung (ausgang_bestaetigt) -> 409, nichts
    geschrieben; die Meldung enthaelt NICHT "neu laden" (Termine.jsx wuerde
    sonst den Dialog verwerfen)
  * mit Bestaetigung: Merker ausgang_geaendert im selben Write wie der Status,
    eigener Verlaufseintrag termin.ausgang.geaendert, Vorgang storniert /
    nicht abgeholt, Fahrzeug streng zurueckgenommen (ohne Verweis und ohne den
    Einkaufspreis dieser Abholung), "Neuen Vertrag senden" und der Alarm
    vertrag_nach_abholung_offen entfallen; Protokoll und Vertragsfassung
    bleiben als Nachweis
  * Doppel-Abholung: das Fahrzeug bleibt abgeholt, Verweis auf den anderen Kauf
  * Fahrzeug schon im Bestand: unveraendert, Hinweis in der Antwort
  * Termin ohne Vorgang: Fahrzeug direkt zurueck (und beim Rueckweg wieder hin)
  * Rueckweg storniert -> abgeholt stellt Vorgang, Fahrzeug und Preis wieder her
  * Sucher: erledigt zaehlt wie abgeholt (403), Protokoll und Chef-Entscheidung
    ebenso
  * Zeiger ins Leere / anderer abgeholter Termin: nichts zuruecknehmen
  * Nachholer erzeugen fuer den stornierten Termin keinen Vertrag mehr

Pruefung 21.09.2026 (V-12, Nachpruefung, Tests test_n*):
  * die Regel haengt am Beleg (finales Protokoll / Vorgang abgeholt), auch
    fuer einen zur Korrektur wieder geoeffneten Termin: Sucher 403, Fahrer
    409 ("nicht abgeholt"), Chef 409 ohne Bestaetigung
  * Rueckweg stellt "Neuen Vertrag senden" (nur wenn das Storno ihn entfernte
    und seitdem nichts versendet wurde) und die gescheiterte Neuerzeugung wieder her
  * "erledigt" ohne Fahrzeug/Vertrag ist keine Abholung
  * Aufraeumjobs lesen den Terminstatus frisch (Storno waehrend der Neuerzeugung)
  * gescheiterter Rueckweg ohne Vorgang -> nacharbeit_offen
  * veralteter Dialog-Stand -> "neu laden" vor der Ausgangs-Rueckfrage
  * Merker ausgang_geaendert: beim Wiederoeffnen entfernt, folgt dem Status

In-Prozess gegen eine Wegwerf-DB (autoschnell_v12_<uuid>), kein Server.
"""
import asyncio
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

import deps  # noqa: E402
import kaufvorgang as KV  # noqa: E402
import lifecycle as LC  # noqa: E402
import cleanup_service as CS  # noqa: E402
import routes.appointments as A  # noqa: E402
import routes.contracts as C  # noqa: E402
import routes.drivers as DRV  # noqa: E402
import routes.protocols as P  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _jetzt(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_v12_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, KV, LC, A, C, DRV, P):
        monkeypatch.setattr(mod, "db", db)
    s = uuid.uuid4().hex[:8]
    w = SimpleNamespace(
        db=db, run=loop.run_until_complete, s=s, dealer_id=f"d_v12_{s}",
        chef={"id": f"chef_v12_{s}", "dealer_id": f"d_v12_{s}", "role": "dealer"},
        sucher={"id": f"su_v12_{s}", "dealer_id": f"d_v12_{s}", "role": "sucher"},
        fahrer=f"f_v12_{s}")
    w.run(db.users.insert_many([{**u, "active": True, "created_at": _jetzt()}
                                for u in (w.chef, w.sucher)]))
    w.run(db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                 "company_name": "V12 GmbH", "created_at": _jetzt()}))
    try:
        yield w
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def _abholung(w, name, *, preis=9000.0, vorgang=True, protokoll=True, fahrer=True,
              status="abgeholt", vid=None, fahrzeug=True, lifecycle="abgeholt", **termin):
    """Abgeschlossene Abholung wie nach dem Fahrer-Abschluss: Termin, finales
    Protokoll, Vorgang (abgeholt), Vertrag mit Fassung nach der Abholung und
    offenem Versand-Hinweis, Fahrzeug abgeholt mit Verweis und Preis."""
    s = w.s
    aid, cid, kid, pid = (f"a_{name}_{s}", f"c_{name}_{s}", f"k_{name}_{s}", f"p_{name}_{s}")
    vid = vid or f"v_{name}_{s}"
    db = w.db

    async def anlegen():
        if fahrzeug:
            doc = {"id": vid, "dealer_id": w.dealer_id, "lifecycle": lifecycle,
                   "owner_user_id": w.sucher["id"], "data": {"make_label": "BMW"},
                   "created_at": _jetzt(), "updated_at": _jetzt()}
            if vorgang:
                doc.update(abgeholt_kaufvorgang_id=kid, purchase_price=preis)
            await db.vehicles.insert_one(doc)
        if vorgang:
            await db.generated_pdfs.insert_one({
                "id": cid, "dealer_id": w.dealer_id, "user_id": w.sucher["id"],
                "vehicle_id": vid, "kaufvorgang_id": kid, "appointment_id": aid,
                "status": "neu erstellt", "version": 2, "purchase_price": preis,
                "nach_abholung_protokoll_id": pid, "nach_abholung_versand_offen": True,
                "created_at": _jetzt()})
            await db.kaufvorgaenge.insert_one({
                "id": kid, "dealer_id": w.dealer_id, "user_id": w.sucher["id"],
                "vehicle_id": vid, "contract_id": cid, "appointment_id": aid,
                "status": "abgeholt", "purchase_price": preis,
                "created_at": _jetzt(-60), "updated_at": _jetzt(-60)})
            await db.betriebsalarme.insert_one({
                "id": f"al_{name}_{s}", "typ": "vertrag_nach_abholung_offen", "ref": cid,
                "offen": True, "details": {"protokoll_id": pid}, "created_at": _jetzt()})
        appt = {"id": aid, "dealer_id": w.dealer_id, "title": f"Abholung {name}",
                "status": status, "vehicle_id": vid, "pickup_date": "2026-09-20",
                "pickup_time": "10:00", "seller_name": "Vera", "created_by": w.sucher["id"],
                "abgeschlossen_seit": _jetzt(-3600), "status_changed_at": _jetzt(-3600),
                "created_at": _jetzt(-7200), "updated_at": _jetzt(-600)}
        if vorgang:
            appt.update(contract_id=cid, kaufvorgang_id=kid)
        if fahrer:
            appt["driver_id"] = w.fahrer
        if protokoll:
            appt["protocol_id"] = pid
            await db.pickup_protocols.insert_one({
                "id": pid, "appointment_id": aid, "dealer_id": w.dealer_id,
                "status": "final", "superseded": False, "version": 1,
                "driver_account_id": w.fahrer if fahrer else None,
                "vehicle_id": vid, "contract_id": cid if vorgang else None,
                "finalized_at": _jetzt(-3600), "created_at": _jetzt(-7200)})
        appt.update(termin)
        await db.appointments.insert_one(appt)
    w.run(anlegen())
    return SimpleNamespace(aid=aid, cid=cid, kid=kid, pid=pid, vid=vid)


def _doc(w, coll, _id):
    return w.run(w.db[coll].find_one({"id": _id}, {"_id": 0}))


def _put(w, aid, user, **felder):
    return w.run(A.update_appointment(aid, A.AppointmentIn(**felder), user))


def _fehler(w, aid, user, **felder):
    with pytest.raises(HTTPException) as e:
        _put(w, aid, user, **felder)
    return e.value


def _verlauf(w, aktion, ref):
    return w.run(w.db.activity_logs.find(
        {"dealer_id": w.dealer_id, "action": aktion, "ref": ref}, {"_id": 0}).to_list(20))


# ------------------------------------------------------------------ 1
def test_01_ohne_bestaetigung_409_und_nichts_geschrieben(welt):
    w = welt
    t = _abholung(w, "ohne")
    vorher = _doc(w, "appointments", t.aid)
    for ziel in ("storniert", "nicht abgeholt"):
        e = _fehler(w, t.aid, w.chef, status=ziel)
        assert e.status_code == 409 and e.detail == A.AUSGANG_BESTAETIGEN_HINWEIS
        assert "neu laden" not in e.detail.lower(), "Termine.jsx wuerde sonst neu laden"
    # ausdrueckliches "false" zaehlt nicht als Bestaetigung
    assert _fehler(w, t.aid, w.chef, status="storniert", ausgang_bestaetigt=False).status_code == 409
    assert _doc(w, "appointments", t.aid) == vorher, "Termin unveraendert"
    assert _doc(w, "kaufvorgaenge", t.kid)["status"] == "abgeholt"
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "abgeholt"
    assert _verlauf(w, "termin.aktualisiert", t.aid) == []
    # erledigt zaehlt wie abgeholt
    t2 = _abholung(w, "erl", status="erledigt")
    assert _fehler(w, t2.aid, w.chef, status="storniert").status_code == 409


# ------------------------------------------------------------------ 2 + 11
def test_02_bestaetigt_storniert_raeumt_auf_und_belegt(welt):
    w = welt
    t = _abholung(w, "sto")
    r = _put(w, t.aid, w.chef, status="storniert", ausgang_bestaetigt=True)
    assert r["ok"] is True and "hinweis" not in r, r

    termin = _doc(w, "appointments", t.aid)
    assert termin["status"] == "storniert" and termin["protocol_id"] == t.pid
    assert "ausgang_bestaetigt" not in termin, "Steuerfeld wird nie gespeichert"
    ag = termin["ausgang_geaendert"]
    assert ag["von"] == w.chef["id"] and ag["von_status"] == "abgeholt"
    assert ag["nach_status"] == "storniert" and ag["protokoll_id"] == t.pid
    assert ag["am"] == termin["status_changed_at"], "im selben Write wie der Status"

    proto = _doc(w, "pickup_protocols", t.pid)
    assert proto["status"] == "final" and proto["superseded"] is False, "Protokoll bleibt Nachweis"
    assert _doc(w, "kaufvorgaenge", t.kid)["status"] == "storniert"

    fzg = _doc(w, "vehicles", t.vid)
    assert fzg["lifecycle"] == "storniert", fzg
    assert "abgeholt_kaufvorgang_id" not in fzg and "purchase_price" not in fzg, fzg

    vertrag = _doc(w, "generated_pdfs", t.cid)
    assert "nach_abholung_versand_offen" not in vertrag, "kein 'Neuen Vertrag senden' mehr"
    assert vertrag["nach_abholung_protokoll_id"] == t.pid, "die Fassung bleibt Nachweis"
    assert vertrag["version"] == 2
    assert w.run(w.db.betriebsalarme.count_documents(
        {"typ": "vertrag_nach_abholung_offen", "ref": t.cid, "offen": True})) == 0

    eintraege = _verlauf(w, "termin.ausgang.geaendert", t.aid)
    assert len(eintraege) == 1
    e = eintraege[0]
    assert e["user_id"] == w.chef["id"] and e["created_at"]
    m = e["meta"]
    assert m["status_von"] == "abgeholt" and m["status_nach"] == "storniert"
    assert m["protokoll_id"] == t.pid and m["protokoll_version"] == 1
    assert m["vehicle_id"] == t.vid and m["contract_id"] == t.cid
    assert m["kaufvorgang_id"] == t.kid and m["fahrzeug_nachher"] == "storniert"
    assert m["bestaetigt"] is True
    # der normale Eintrag bleibt ebenfalls
    assert _verlauf(w, "termin.aktualisiert", t.aid)[0]["meta"]["status_nach"] == "storniert"
    # Fahrzeug-Verlauf nennt den Grund
    fz_log = _verlauf(w, "fahrzeug.status.storniert", t.vid)
    assert fz_log and fz_log[0]["meta"]["grund"] == "abholung_zurueckgenommen"
    assert fz_log[0]["meta"]["preis_entfernt"] is True

    # Sucher sehen den Merker ohne Konto-Kennung
    fuer_sucher = A.termin_fuer_sucher(dict(w.sucher), dict(termin))
    assert "von" not in fuer_sucher["ausgang_geaendert"]
    assert fuer_sucher["ausgang_geaendert"]["nach_status"] == "storniert"
    assert A.termin_fuer_sucher(dict(w.chef), dict(termin))["ausgang_geaendert"]["von"] == w.chef["id"]

    # 11: der Fahrer kommt nach der Stornierung nicht mehr an die Unterlagen
    with pytest.raises(HTTPException) as fz:
        DRV.unterlagen_zugriff_oder_404(termin)
    assert fz.value.status_code == 404


# ------------------------------------------------------------------ 3
def test_03_nicht_abgeholt(welt):
    w = welt
    t = _abholung(w, "nab")
    r = _put(w, t.aid, w.chef, status="nicht abgeholt", ausgang_bestaetigt=True)
    assert r["ok"] is True
    assert _doc(w, "kaufvorgaenge", t.kid)["status"] == "nicht_abgeholt"
    fzg = _doc(w, "vehicles", t.vid)
    assert fzg["lifecycle"] == "nicht_abgeholt" and "purchase_price" not in fzg
    assert _doc(w, "appointments", t.aid)["ausgang_geaendert"]["nach_status"] == "nicht abgeholt"
    assert _verlauf(w, "termin.ausgang.geaendert", t.aid)[0]["meta"]["fahrzeug_nachher"] == "nicht_abgeholt"


# ------------------------------------------------------------------ 4
def test_04_doppel_abholung_fahrzeug_bleibt_beim_anderen_kauf(welt):
    w = welt
    vid = f"v_doppel_{w.s}"
    t1 = _abholung(w, "d1", vid=vid, preis=9000.0)
    t2 = _abholung(w, "d2", vid=vid, preis=9500.0, fahrzeug=False)
    r = _put(w, t1.aid, w.chef, status="storniert", ausgang_bestaetigt=True)
    assert r["ok"] is True and "hinweis" not in r
    fzg = _doc(w, "vehicles", vid)
    assert fzg["lifecycle"] == "abgeholt", "der zweite Kauf ist weiter abgeholt"
    assert fzg["abgeholt_kaufvorgang_id"] == t2.kid and fzg["purchase_price"] == 9500.0, fzg
    assert _doc(w, "kaufvorgaenge", t2.kid)["status"] == "abgeholt"
    assert _doc(w, "generated_pdfs", t2.cid)["nach_abholung_versand_offen"] is True, \
        "der Vertrag des anderen Kaufs bleibt unberuehrt"
    assert "nach_abholung_versand_offen" not in _doc(w, "generated_pdfs", t1.cid)


# ------------------------------------------------------------------ 5
def test_05_fahrzeug_schon_im_bestand_bleibt_mit_hinweis(welt):
    w = welt
    t = _abholung(w, "best", lifecycle="bestand")
    r = _put(w, t.aid, w.chef, status="storniert", ausgang_bestaetigt=True)
    assert r["ok"] is True and r["hinweis"] == A.AUSGANG_FAHRZEUG_WEITER_HINWEIS
    fzg = _doc(w, "vehicles", t.vid)
    assert fzg["lifecycle"] == "bestand" and fzg["abgeholt_kaufvorgang_id"] == t.kid
    assert fzg["purchase_price"] == 9000.0, "im Bestand entscheidet der Chef selbst"
    assert _doc(w, "kaufvorgaenge", t.kid)["status"] == "storniert"
    assert _verlauf(w, "termin.ausgang.geaendert", t.aid)[0]["meta"]["fahrzeug_nachher"] == "bestand"


# ------------------------------------------------------------------ 6
def test_06_termin_ohne_vorgang_hin_und_zurueck(welt):
    w = welt
    t = _abholung(w, "ohnekv", vorgang=False, protokoll=False, fahrer=False)
    assert _doc(w, "vehicles", t.vid).get("abgeholt_kaufvorgang_id") is None
    r = _put(w, t.aid, w.chef, status="nicht abgeholt", ausgang_bestaetigt=True)
    assert r["ok"] is True
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "nicht_abgeholt"
    termin = _doc(w, "appointments", t.aid)
    assert termin["ausgang_geaendert"]["protokoll_id"] is None
    # Rueckweg: wieder abgeholt (ohne Bestaetigung) — auch das Fahrzeug
    r = _put(w, t.aid, w.chef, status="abgeholt")
    assert r["ok"] is True
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "abgeholt"
    assert len(_verlauf(w, "termin.ausgang.geaendert", t.aid)) == 2
    # und storniert
    t2 = _abholung(w, "ohnekv2", vorgang=False, protokoll=False, fahrer=False)
    _put(w, t2.aid, w.chef, status="storniert", ausgang_bestaetigt=True)
    assert _doc(w, "vehicles", t2.vid)["lifecycle"] == "storniert"


# ------------------------------------------------------------------ 7
def test_07_rueckweg_stellt_vorgang_fahrzeug_und_preis_wieder_her(welt):
    w = welt
    t = _abholung(w, "rw")
    _put(w, t.aid, w.chef, status="storniert", ausgang_bestaetigt=True)
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "storniert"
    r = _put(w, t.aid, w.chef, status="abgeholt")
    assert r["ok"] is True
    assert _doc(w, "kaufvorgaenge", t.kid)["status"] == "abgeholt"
    fzg = _doc(w, "vehicles", t.vid)
    assert fzg["lifecycle"] == "abgeholt" and fzg["abgeholt_kaufvorgang_id"] == t.kid
    assert fzg["purchase_price"] == 9000.0
    termin = _doc(w, "appointments", t.aid)
    assert termin["ausgang_geaendert"]["von_status"] == "storniert"
    assert termin["ausgang_geaendert"]["nach_status"] == "abgeholt"
    eintraege = _verlauf(w, "termin.ausgang.geaendert", t.aid)
    assert sorted(e["meta"]["status_nach"] for e in eintraege) == ["abgeholt", "storniert"]


# ------------------------------------------------------------------ 8
def test_08_sucher_aendert_den_ausgang_nicht(welt):
    w = welt
    # erledigt ohne Protokoll: wie abgeholt (Luecke vor V-12)
    t = _abholung(w, "su_erl", status="erledigt", protokoll=False, fahrer=False)
    assert _fehler(w, t.aid, w.sucher, status="storniert").status_code == 403
    assert _doc(w, "appointments", t.aid)["status"] == "erledigt"
    assert _doc(w, "kaufvorgaenge", t.kid)["status"] == "abgeholt"
    # storniert mit finalem Protokoll -> nicht abgeholt: nur der Chef
    t2 = _abholung(w, "su_sto", status="storniert")
    assert _fehler(w, t2.aid, w.sucher, status="nicht abgeholt").status_code == 403
    # vom Chef geaenderter Ausgang (ohne Protokoll): der Sucher dreht ihn nicht zurueck
    t3 = _abholung(w, "su_chef", protokoll=False, fahrer=False)
    _put(w, t3.aid, w.chef, status="storniert", ausgang_bestaetigt=True)
    assert _fehler(w, t3.aid, w.sucher, status="abgeholt").status_code == 403
    assert _doc(w, "appointments", t3.aid)["status"] == "storniert"
    # Gegenprobe: zwischen anderen Endzustaenden ohne Protokoll bleibt es frei
    t4 = _abholung(w, "su_frei", status="nicht abgeholt", protokoll=False, fahrer=False,
                   vorgang=False)
    assert _put(w, t4.aid, w.sucher, status="storniert")["ok"] is True


# ------------------------------------------------------------------ 9
def test_09_zeiger_ins_leere_und_anderer_abgeholter_termin(welt):
    w = welt
    db = w.db
    # a) Verweis auf einen fehlenden Vorgang (Runde 27, Test 03): bleibt abgeholt
    vid = f"v_leer_{w.s}"
    w.run(db.vehicles.insert_one({"id": vid, "dealer_id": w.dealer_id, "lifecycle": "abgeholt",
                                  "abgeholt_kaufvorgang_id": "kv-fehlt", "purchase_price": 1.0}))
    w.run(db.kaufvorgaenge.insert_one({
        "id": f"k_leer_{w.s}", "dealer_id": w.dealer_id, "vehicle_id": vid,
        "contract_id": f"c_leer_{w.s}", "status": "storniert", "updated_at": _jetzt()}))
    w.run(KV.fahrzeug_status_aggregieren(vid, w.dealer_id))
    fzg = _doc(w, "vehicles", vid)
    assert fzg["lifecycle"] == "abgeholt" and fzg["abgeholt_kaufvorgang_id"] == "kv-fehlt"

    # b) Vorgang storniert, aber ein Terminplaner-Termin am Auto ist abgeholt
    t = _abholung(w, "tp")
    w.run(db.kaufvorgaenge.update_one({"id": t.kid}, {"$set": {"status": "storniert"}}))
    w.run(db.appointments.update_one({"id": t.aid}, {"$set": {"status": "storniert"}}))
    w.run(db.appointments.insert_one({"id": f"a_tp2_{w.s}", "dealer_id": w.dealer_id,
                                      "vehicle_id": t.vid, "status": "abgeholt"}))
    assert w.run(KV.fahrzeug_status_aggregieren(t.vid, w.dealer_id)) == "storniert"
    fzg = _doc(w, "vehicles", t.vid)
    assert fzg["lifecycle"] == "abgeholt" and fzg["abgeholt_kaufvorgang_id"] == t.kid

    # c) Wieder geoeffnet ohne Protokoll (Vorgang abholung_geplant): wie bisher
    t3 = _abholung(w, "offen")
    w.run(db.kaufvorgaenge.update_one({"id": t3.kid}, {"$set": {"status": "abholung_geplant"}}))
    w.run(db.appointments.update_one({"id": t3.aid}, {"$set": {"status": "offen"}}))
    w.run(KV.fahrzeug_status_aggregieren(t3.vid, w.dealer_id))
    assert _doc(w, "vehicles", t3.vid)["lifecycle"] == "abgeholt"


def test_09b_uebergangstabelle_bleibt_streng():
    """Kein allgemeiner Weg aus 'abgeholt' — sonst setzte jedes try_set_lifecycle
    eines anderen Termins ein abgeholtes Auto still zurueck."""
    assert LC.ALLOWED_TRANSITIONS["abgeholt"] == {"bestand", "verkaufsentwurf", "geloescht"}
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(LC.LifecycleError):
            loop.run_until_complete(LC.abholung_zuruecknehmen("v", "d", "bestand"))
    finally:
        loop.close()


def test_09c_rueckweg_verliert_gegen_geaenderten_preis(welt):
    """Compare-and-Set: hat sich der Preis zwischen Lesen und Schreiben
    geaendert, schreibt der Rueckweg nichts."""
    w = welt
    t = _abholung(w, "cas")
    ok = w.run(LC.abholung_zuruecknehmen(t.vid, w.dealer_id, "storniert",
                                         kaufvorgang_id=t.kid, preis=1234.0,
                                         preis_entfernen=True))
    assert ok is False
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "abgeholt"
    ok = w.run(LC.abholung_zuruecknehmen(t.vid, w.dealer_id, "storniert",
                                         kaufvorgang_id=t.kid, preis=9000.0))
    fzg = _doc(w, "vehicles", t.vid)
    assert ok is True and fzg["lifecycle"] == "storniert"
    assert fzg["purchase_price"] == 9000.0, "ohne preis_entfernen bleibt der Preis"


# ------------------------------------------------------------------ 10
def test_10_nachholer_erzeugen_fuer_stornierten_termin_keinen_vertrag(welt, monkeypatch):
    w = welt
    db = w.db
    aufrufe = []

    async def _aktualisieren(*a, **k):
        aufrufe.append(("aktualisieren", a, k))
        return True

    async def _sicherstellen(*a, **k):
        aufrufe.append(("sicherstellen", a, k))
        return True
    monkeypatch.setattr(P, "vertrag_nach_abholung_aktualisieren", _aktualisieren)
    monkeypatch.setattr(P, "vertrag_nach_abholung_sicherstellen", _sicherstellen)

    t = _abholung(w, "nh", status="storniert", nacharbeit_offen=True,
                  nacharbeit_protokoll_id=f"p_nh_{w.s}", updated_at=_jetzt(-3600))
    w.run(db.kaufvorgaenge.update_one({"id": t.kid}, {"$set": {"status": "storniert"}}))
    # Alarm-Nachholer: Alarm zu, keine Neuerzeugung
    assert w.run(CS.vertrag_nach_abholung_nachholen(db)) == 0
    assert w.run(db.betriebsalarme.count_documents(
        {"typ": "vertrag_nach_abholung_offen", "ref": t.cid, "offen": True})) == 0
    # Termin-Nachholer: ebenfalls keine Neuerzeugung, Merker weg
    assert w.run(CS.termin_nacharbeit_nachholen(db, datetime.now(timezone.utc))) == 1
    assert aufrufe == [], aufrufe
    assert "nacharbeit_offen" not in _doc(w, "appointments", t.aid)

    # Gegenprobe: abgeholter Termin -> der Alarm-Nachholer erzeugt weiter
    t2 = _abholung(w, "nh2")
    w.run(CS.vertrag_nach_abholung_nachholen(db))
    assert [a[0] for a in aufrufe] == ["aktualisieren"]
    assert aufrufe[0][1][1] == t2.pid


def test_10b_termin_nachholer_zieht_den_ausgang_nach(welt):
    """Scheiterte der Rueckweg beim Speichern (nacharbeit_offen), holt ihn der
    Aufraeumjob nach — Termin ohne Vorgang, Fahrzeug noch abgeholt."""
    w = welt
    t = _abholung(w, "nh3", vorgang=False, protokoll=False, fahrer=False, status="storniert",
                  nacharbeit_offen=True, updated_at=_jetzt(-3600),
                  ausgang_geaendert={"am": _jetzt(-3600), "von": w.chef["id"],
                                     "von_status": "abgeholt", "nach_status": "storniert",
                                     "protokoll_id": None})
    assert w.run(CS.termin_nacharbeit_nachholen(w.db, datetime.now(timezone.utc))) == 1
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "storniert"
    assert "nacharbeit_offen" not in _doc(w, "appointments", t.aid)


def test_10c_verlorener_rueckweg_setzt_den_merker(welt, monkeypatch):
    """Verliert der Fahrzeug-Rueckweg (Termin ohne Vorgang) sein Compare-and-Set,
    ist der Termin trotzdem gespeichert — mit nacharbeit_offen statt 500."""
    w = welt
    t = _abholung(w, "verl", vorgang=False, protokoll=False, fahrer=False)

    async def _verliert(*a, **k):
        return False
    monkeypatch.setattr(LC, "abholung_zuruecknehmen", _verliert)
    r = _put(w, t.aid, w.chef, status="storniert", ausgang_bestaetigt=True)
    assert r["ok"] is True and r.get("nacharbeit_offen") is True
    assert r["hinweis"] == A.NACHARBEIT_HINWEIS
    termin = _doc(w, "appointments", t.aid)
    assert termin["status"] == "storniert" and termin["nacharbeit_offen"] is True
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "abgeholt"


# ======================================================================
# Pruefung 21.09.2026 (V-12, Nachpruefung): acht Befunde der Gegenpruefung
# ======================================================================
def _fahrer(w):
    """Fahrer-Konto mit aktueller Verknuepfung zur Firma (_zugriff_pruefen)."""
    w.run(w.db.dealer_drivers.update_one(
        {"dealer_id": w.dealer_id, "driver_account_id": w.fahrer},
        {"$set": {"dealer_id": w.dealer_id, "driver_account_id": w.fahrer}}, upsert=True))
    return {"id": w.fahrer, "display_name": "Fahrer V12"}


def _fahrer_status(w, aid, status, fahrer):
    return w.run(DRV.driver_set_status(aid, DRV.DriverStatusIn(status=status), fahrer))


def _wieder_geoeffnet(w, name, **kw):
    """Abgeholte Abholung, die der Chef zur Korrektur wieder geoeffnet hat
    (abgeholt -> offen): Vorgang bleibt wegen D15 abgeholt, Protokoll final."""
    t = _abholung(w, name, **kw)
    assert _put(w, t.aid, w.chef, status="offen")["ok"] is True
    assert _doc(w, "appointments", t.aid)["status"] == "offen"
    assert _doc(w, "kaufvorgaenge", t.kid)["status"] == "abgeholt", "D15"
    return t


def _fahrzeug_unveraendert(w, t, preis=9000.0):
    fzg = _doc(w, "vehicles", t.vid)
    assert fzg["lifecycle"] == "abgeholt", fzg
    assert fzg["abgeholt_kaufvorgang_id"] == t.kid and fzg["purchase_price"] == preis, fzg
    assert _doc(w, "kaufvorgaenge", t.kid)["status"] == "abgeholt"


# ------------------------------------------------------------------ N1
def test_n1_wieder_geoeffneter_termin_sucher_fahrer_und_chef_ohne_bestaetigung(welt):
    """Befund 1: abgeholt -> offen (Chef) -> storniert/nicht abgeholt nahm die
    unterschriebene Abholung samt Fahrzeug und Preis zurueck — ohne Rueckfrage."""
    w = welt
    t = _wieder_geoeffnet(w, "wo")
    fahrer = _fahrer(w)
    vorher = _doc(w, "appointments", t.aid)
    for ziel in ("storniert", "nicht abgeholt"):
        e = _fehler(w, t.aid, w.sucher, status=ziel)
        assert e.status_code == 403 and "Hauptaccount" in e.detail, e.detail
    with pytest.raises(HTTPException) as fz:
        _fahrer_status(w, t.aid, "nicht abgeholt", fahrer)
    assert fz.value.status_code == 409 and fz.value.detail == DRV.ABHOLUNG_BELEGT_HINWEIS
    for ziel in ("storniert", "nicht abgeholt"):
        e = _fehler(w, t.aid, w.chef, status=ziel)
        assert e.status_code == 409 and e.detail == A.AUSGANG_BESTAETIGEN_HINWEIS
    assert _doc(w, "appointments", t.aid) == vorher, "nichts geschrieben"
    _fahrzeug_unveraendert(w, t)
    # andere offene Zustaende bleiben fuer den Sucher frei
    assert _put(w, t.aid, w.sucher, status="verschoben")["ok"] is True

    # mit Bestaetigung: wie aus "abgeholt" — Merker, Verlauf, Ruecknahme
    r = _put(w, t.aid, w.chef, status="storniert", ausgang_bestaetigt=True)
    assert r["ok"] is True
    ag = _doc(w, "appointments", t.aid)["ausgang_geaendert"]
    assert ag["von_status"] == "verschoben" and ag["nach_status"] == "storniert"
    assert ag["protokoll_id"] == t.pid
    assert _doc(w, "kaufvorgaenge", t.kid)["status"] == "storniert"
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "storniert"
    m = _verlauf(w, "termin.ausgang.geaendert", t.aid)[0]["meta"]
    assert m["status_von"] == "verschoben" and m["protokoll_id"] == t.pid


def test_n1b_waehrend_einer_korrektur_belegt_der_vorgang(welt):
    """Laeuft die Korrektur-Version, ist die finale Fassung abgeloest — der
    Vorgang (D15: abgeholt) belegt die Abholung weiter."""
    w = welt
    t = _wieder_geoeffnet(w, "korr")
    fahrer = _fahrer(w)
    w.run(w.db.pickup_protocols.update_one({"id": t.pid},
                                           {"$set": {"superseded": True, "superseded_at": _jetzt()}}))
    w.run(w.db.pickup_protocols.insert_one({
        "id": f"p2_korr_{w.s}", "appointment_id": t.aid, "dealer_id": w.dealer_id,
        "status": "entwurf", "superseded": False, "version": 2, "corrects_version": 1,
        "driver_account_id": w.fahrer, "created_at": _jetzt()}))
    assert _fehler(w, t.aid, w.sucher, status="storniert").status_code == 403
    with pytest.raises(HTTPException) as fz:
        _fahrer_status(w, t.aid, "nicht abgeholt", fahrer)
    assert fz.value.status_code == 409
    assert _fehler(w, t.aid, w.chef, status="nicht abgeholt").status_code == 409
    _fahrzeug_unveraendert(w, t)
    # mit Bestaetigung: Korrektur verworfen, die unterschriebene Fassung ist Beleg
    _put(w, t.aid, w.chef, status="nicht abgeholt", ausgang_bestaetigt=True)
    assert _doc(w, "appointments", t.aid)["ausgang_geaendert"]["protokoll_id"] == t.pid
    assert _doc(w, "pickup_protocols", t.pid)["superseded"] is False
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "nicht_abgeholt"


def test_n1d_beleg_haengt_am_kauf_nicht_am_alten_protokoll(welt):
    """a) Storno bestaetigt, danach wieder geoeffnet: der Kauf ist schon
    zurueckgenommen — ein erneutes Storno braucht keine Rueckfrage (das
    finale Protokoll allein belegt nichts mehr; so auch test_rollen_negativ
    test_05). Der Fahrer setzt 'nicht abgeholt' trotzdem nicht (Protokoll).
    b) Termin ohne Vorgang, wieder geoeffnet: Protokoll + abgeholtes Fahrzeug
    belegen die Abholung."""
    w = welt
    t = _abholung(w, "zweimal")
    _put(w, t.aid, w.chef, status="storniert", ausgang_bestaetigt=True)
    _put(w, t.aid, w.chef, status="offen")
    assert _doc(w, "kaufvorgaenge", t.kid)["status"] == "abholung_geplant"
    with pytest.raises(HTTPException) as fz:
        _fahrer_status(w, t.aid, "nicht abgeholt", _fahrer(w))
    assert fz.value.status_code == 409
    assert _put(w, t.aid, w.chef, status="storniert")["ok"] is True
    assert "ausgang_geaendert" not in _doc(w, "appointments", t.aid)
    assert len(_verlauf(w, "termin.ausgang.geaendert", t.aid)) == 1

    t2 = _abholung(w, "ohnekv_wo", vorgang=False)
    assert _put(w, t2.aid, w.chef, status="offen")["ok"] is True
    assert _doc(w, "vehicles", t2.vid)["lifecycle"] == "abgeholt"
    assert _fehler(w, t2.aid, w.sucher, status="storniert").status_code == 403
    assert _fehler(w, t2.aid, w.chef, status="storniert").status_code == 409
    _put(w, t2.aid, w.chef, status="storniert", ausgang_bestaetigt=True)
    assert _doc(w, "vehicles", t2.vid)["lifecycle"] == "storniert"
    assert _doc(w, "appointments", t2.aid)["ausgang_geaendert"]["protokoll_id"] == t2.pid


def test_n1c_fahrer_nicht_abgeholt_ohne_beleg_bleibt_frei(welt):
    """Gegenprobe: ohne finales Protokoll und ohne abgeholten Vorgang setzt der
    Fahrer 'nicht abgeholt' wie bisher."""
    w = welt
    t = _abholung(w, "frei", status="offen", protokoll=False, lifecycle="abholung_geplant")
    w.run(w.db.kaufvorgaenge.update_one({"id": t.kid}, {"$set": {"status": "abholung_geplant"}}))
    r = _fahrer_status(w, t.aid, "nicht abgeholt", _fahrer(w))
    assert r["ok"] is True and _doc(w, "appointments", t.aid)["status"] == "nicht abgeholt"
    assert _doc(w, "kaufvorgaenge", t.kid)["status"] == "nicht_abgeholt"


# ------------------------------------------------------------------ N2
def test_n2a_rueckweg_stellt_neuen_vertrag_senden_wieder_her(welt):
    """Befund 2, Fall a: die Fassung nach der Abholung war noch nicht versendet —
    nach Storno und Rueckweg steht "Neuen Vertrag senden" wieder da."""
    w = welt
    t = _abholung(w, "rwa")
    _put(w, t.aid, w.chef, status="storniert", ausgang_bestaetigt=True)
    v = _doc(w, "generated_pdfs", t.cid)
    assert "nach_abholung_versand_offen" not in v
    assert v["nach_abholung_versand_ausgesetzt"]["termin_id"] == t.aid, "im selben Write festgehalten"
    _put(w, t.aid, w.chef, status="abgeholt")
    v = _doc(w, "generated_pdfs", t.cid)
    assert v["nach_abholung_versand_offen"] is True, v
    assert "nach_abholung_versand_ausgesetzt" not in v
    assert v["nach_abholung_protokoll_id"] == t.pid and v["version"] == 2, "keine neue Fassung"
    # idempotent: ein zweiter Rueckweg-Lauf aendert nichts
    w.run(A.ausgang_nachziehen({"id": t.aid, "status": "abgeholt", "vehicle_id": t.vid,
                                "contract_id": t.cid}, w.dealer_id, hat_vorgang=True))
    assert _doc(w, "generated_pdfs", t.cid)["nach_abholung_versand_offen"] is True


def test_n2a_nach_versand_kommt_der_hinweis_nicht_wieder(welt):
    """Wurde der Vertrag seit dem Storno versendet, ist die Rueckfrage beantwortet."""
    w = welt
    t = _abholung(w, "rwv")
    _put(w, t.aid, w.chef, status="storniert", ausgang_bestaetigt=True)
    w.run(w.db.generated_pdfs.update_one(
        {"id": t.cid}, {"$push": {"send_status": {"channel": "email", "sent_at": _jetzt(5)}}}))
    _put(w, t.aid, w.chef, status="abgeholt")
    v = _doc(w, "generated_pdfs", t.cid)
    assert "nach_abholung_versand_offen" not in v and "nach_abholung_versand_ausgesetzt" not in v


def test_n2a_ohne_entfernten_merker_kommt_nichts_wieder(welt):
    """Stand beim Storno kein Merker (schon versendet), setzt der Rueckweg keinen."""
    w = welt
    t = _abholung(w, "rwo")
    w.run(w.db.generated_pdfs.update_one({"id": t.cid},
                                         {"$unset": {"nach_abholung_versand_offen": ""}}))
    _put(w, t.aid, w.chef, status="storniert", ausgang_bestaetigt=True)
    assert "nach_abholung_versand_ausgesetzt" not in _doc(w, "generated_pdfs", t.cid)
    _put(w, t.aid, w.chef, status="abgeholt")
    assert "nach_abholung_versand_offen" not in _doc(w, "generated_pdfs", t.cid)


def test_n2b_rueckweg_holt_gescheiterte_neuerzeugung_nach(welt, monkeypatch):
    """Befund 2, Fall b: Die Neuerzeugung nach der Abholung war gescheitert
    (Alarm offen). Das Storno schloss den Alarm — der Rueckweg versucht sie
    erneut und legt bei Misserfolg den Alarm wieder an."""
    w = welt
    t = _abholung(w, "rwb")
    w.run(w.db.generated_pdfs.update_one(
        {"id": t.cid}, {"$unset": {"nach_abholung_protokoll_id": "",
                                   "nach_abholung_versand_offen": ""}}))
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"neuer_preis": 8500.0}}))
    _put(w, t.aid, w.chef, status="storniert", ausgang_bestaetigt=True)
    offen = {"typ": "vertrag_nach_abholung_offen", "ref": t.cid, "offen": True}
    assert w.run(w.db.betriebsalarme.count_documents(offen)) == 0

    aufrufe = []

    async def _regen(**k):
        aufrufe.append(k)
        k["ergebnis"]["grund"] = "pdf_fehler"
        return False
    monkeypatch.setattr(C, "regenerate_contract_for_pickup", _regen)
    r = _put(w, t.aid, w.chef, status="abgeholt")
    assert r["ok"] is True
    assert len(aufrufe) == 1 and aufrufe[0]["protokoll_id"] == t.pid
    assert aufrufe[0]["neuer_preis"] == 8500.0 and aufrufe[0]["grund"] == "abholung_abgeschlossen"
    assert w.run(w.db.betriebsalarme.count_documents(offen)) == 1, "Alarm wieder offen"


# ------------------------------------------------------------------ N3
def _allgemein(w, name, status="erledigt", **extra):
    aid = f"a_{name}_{w.s}"
    doc = {"id": aid, "dealer_id": w.dealer_id, "title": "Werkstatt", "status": status,
           "created_by": w.sucher["id"], "pickup_date": "2026-09-20",
           "created_at": _jetzt(-7200), "updated_at": _jetzt(-600)}
    doc.update(extra)
    w.run(w.db.appointments.insert_one(doc))
    return aid


def test_n3_erledigt_ohne_kauf_ist_keine_abholung(welt):
    """Befund 3: allgemeine Termine ohne Fahrzeug/Vertrag: erledigt <->
    storniert wie vor V-12 — Sucher frei, Chef ohne Kauf-Rueckfrage."""
    w = welt
    assert A.ausgang_gilt_als_abgeholt({"status": "abgeholt"}) is True
    assert A.ausgang_gilt_als_abgeholt({"status": "erledigt"}) is False
    assert A.ausgang_gilt_als_abgeholt({"status": "erledigt", "vehicle_id": "v"}) is True
    assert A.ausgang_gilt_als_abgeholt({"status": "erledigt", "contract_id": "c"}) is True
    assert A.ausgang_gilt_als_abgeholt({"status": "erledigt", "kaufvorgang_id": "k"}) is True

    a1 = _allgemein(w, "werk1")
    assert _put(w, a1, w.sucher, status="storniert")["ok"] is True
    assert _put(w, a1, w.sucher, status="erledigt")["ok"] is True
    a2 = _allgemein(w, "werk2")
    r = _put(w, a2, w.chef, status="storniert")
    assert r["ok"] is True
    termin = _doc(w, "appointments", a2)
    assert termin["status"] == "storniert" and "ausgang_geaendert" not in termin
    assert _verlauf(w, "termin.ausgang.geaendert", a2) == []
    # mit finalem Protokoll gilt weiter die alte Regel
    a3 = _allgemein(w, "werk3")
    w.run(w.db.pickup_protocols.insert_one({"id": f"p_werk3_{w.s}", "appointment_id": a3,
                                            "status": "final", "superseded": False,
                                            "version": 1}))
    assert _fehler(w, a3, w.sucher, status="storniert").status_code == 403
    assert _fehler(w, a3, w.chef, status="storniert").status_code == 409
    # "abgeholt" bleibt immer eine Abholung
    a4 = _allgemein(w, "werk4", status="abgeholt")
    assert _fehler(w, a4, w.sucher, status="storniert").status_code == 403
    assert _fehler(w, a4, w.chef, status="storniert").status_code == 409


# ------------------------------------------------------------------ N4
def test_n4a_alarm_nachholer_entfernt_merker_nach_storno_waehrend_der_neuerzeugung(
        welt, monkeypatch):
    """Befund 4, Fall b: Der Chef storniert, waehrend der Nachholer die PDF
    erzeugt — dessen Write setzte "Neuen Vertrag senden" danach dauerhaft."""
    w = welt
    t = _abholung(w, "race1")
    w.run(w.db.generated_pdfs.update_one(
        {"id": t.cid}, {"$unset": {"nach_abholung_protokoll_id": "",
                                   "nach_abholung_versand_offen": ""}}))

    async def _aktualisieren(appt, protokoll_id, *a, **k):
        # parallel: Chef storniert (Aufraeumen findet noch keinen Merker) ...
        await w.db.appointments.update_one({"id": appt["id"]}, {"$set": {"status": "storniert"}})
        # ... dann schreibt die Neuerzeugung ihren Stand samt Merker
        await w.db.generated_pdfs.update_one(
            {"id": appt["contract_id"]},
            {"$set": {"nach_abholung_protokoll_id": protokoll_id,
                      "nach_abholung_versand_offen": True}})
        return True
    monkeypatch.setattr(P, "vertrag_nach_abholung_aktualisieren", _aktualisieren)
    assert w.run(CS.vertrag_nach_abholung_nachholen(w.db)) == 0
    v = _doc(w, "generated_pdfs", t.cid)
    assert "nach_abholung_versand_offen" not in v, v
    assert v["nach_abholung_protokoll_id"] == t.pid, "die Fassung bleibt als Nachweis"
    assert w.run(w.db.betriebsalarme.count_documents(
        {"typ": "vertrag_nach_abholung_offen", "ref": t.cid, "offen": True})) == 0


def test_n4b_termin_nachholer_arbeitet_mit_frischem_status(welt, monkeypatch):
    """Befund 4, Fall a: Der Termin-Nachholer las 'abgeholt'; der Chef
    stornierte danach. Mit dem alten Status setzte er den Vorgang wieder auf
    abgeholt und erzeugte den Vertrag nach der Abholung neu."""
    w = welt
    t = _abholung(w, "race2", nacharbeit_offen=True, updated_at=_jetzt(-3600))
    aufrufe = []
    echt_preis = P.preis_nachholen

    async def _preis(appt, *a, **k):
        # parallel: Chef storniert (Vorgang storniert) nach dem Lesen des Jobs
        await w.db.appointments.update_one({"id": appt["id"]}, {"$set": {"status": "storniert"}})
        await w.db.kaufvorgaenge.update_one({"id": t.kid}, {"$set": {"status": "storniert"}})
        return await echt_preis(appt, *a, **k)

    async def _sicherstellen(*a, **k):
        aufrufe.append(a)
        return True
    monkeypatch.setattr(P, "preis_nachholen", _preis)
    monkeypatch.setattr(P, "vertrag_nach_abholung_sicherstellen", _sicherstellen)
    w.run(CS.termin_nacharbeit_nachholen(w.db, datetime.now(timezone.utc)))
    assert _doc(w, "kaufvorgaenge", t.kid)["status"] == "storniert", "nicht zurueck auf abgeholt"
    assert aufrufe == [], "kein Vertrag nach Abholung fuer den stornierten Termin"


def test_n4c_termin_nachholer_entfernt_merker_nach_storno_waehrend_der_neuerzeugung(
        welt, monkeypatch):
    w = welt
    t = _abholung(w, "race3", nacharbeit_offen=True, nacharbeit_protokoll_id=f"p_race3_{w.s}",
                  updated_at=_jetzt(-3600))
    w.run(w.db.generated_pdfs.update_one({"id": t.cid},
                                         {"$unset": {"nach_abholung_versand_offen": ""}}))

    async def _sicherstellen(appt, protokoll):
        await w.db.appointments.update_one({"id": appt["id"]}, {"$set": {"status": "storniert"}})
        await w.db.generated_pdfs.update_one(
            {"id": appt["contract_id"]},
            {"$set": {"nach_abholung_protokoll_id": protokoll["id"],
                      "nach_abholung_versand_offen": True}})
        return True
    monkeypatch.setattr(P, "vertrag_nach_abholung_sicherstellen", _sicherstellen)
    w.run(CS.termin_nacharbeit_nachholen(w.db, datetime.now(timezone.utc)))
    assert "nach_abholung_versand_offen" not in _doc(w, "generated_pdfs", t.cid)


# ------------------------------------------------------------------ N5
def test_n5a_gescheiterter_rueckweg_ohne_vorgang_setzt_nacharbeit(welt, monkeypatch):
    """Befund 5, Fall b: ein uebersprungener Schritt beim Rueckweg (Termin ohne
    Vorgang) wurde nur geloggt — jetzt nacharbeit_offen, der Aufraeumjob holt nach."""
    w = welt
    t = _abholung(w, "rwf", vorgang=False, protokoll=False, fahrer=False)
    _put(w, t.aid, w.chef, status="nicht abgeholt", ausgang_bestaetigt=True)
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "nicht_abgeholt"

    async def _nie(*a, **k):
        return False
    echt = A.try_set_lifecycle
    monkeypatch.setattr(A, "try_set_lifecycle", _nie)
    r = _put(w, t.aid, w.chef, status="abgeholt")
    assert r["ok"] is True and r.get("nacharbeit_offen") is True, r
    termin = _doc(w, "appointments", t.aid)
    assert termin["status"] == "abgeholt" and termin["nacharbeit_offen"] is True
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "nicht_abgeholt"

    # nicht monkeypatch.undo(): das naehme auch die Wegwerf-DB der Welt zurueck
    monkeypatch.setattr(A, "try_set_lifecycle", echt)
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"updated_at": _jetzt(-3600)}}))
    assert w.run(CS.termin_nacharbeit_nachholen(w.db, datetime.now(timezone.utc))) == 1
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "abgeholt"
    assert "nacharbeit_offen" not in _doc(w, "appointments", t.aid)


def test_n5b_andere_abholung_nach_der_ruecknahme(welt, monkeypatch):
    """Befund 5, Fall a: Pruefen und Zuruecknehmen sind nicht atomar — schliesst
    dazwischen eine andere Abholung dieses Autos ab, steht es wieder auf abgeholt."""
    w = welt
    t = _abholung(w, "rwz", vorgang=False, protokoll=False, fahrer=False)
    echt = LC.abholung_zuruecknehmen

    async def _mit_zweiter_abholung(*a, **k):
        ok = await echt(*a, **k)
        await w.db.appointments.insert_one({"id": f"a_rwz2_{w.s}", "dealer_id": w.dealer_id,
                                            "vehicle_id": t.vid, "status": "abgeholt"})
        return ok
    monkeypatch.setattr(LC, "abholung_zuruecknehmen", _mit_zweiter_abholung)
    r = _put(w, t.aid, w.chef, status="storniert", ausgang_bestaetigt=True)
    assert r["ok"] is True and not r.get("nacharbeit_offen"), r
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "abgeholt"


# ------------------------------------------------------------------ N6
def test_n6_veralteter_dialog_stand_bekommt_neu_laden(welt):
    """Befund 6: Der Dialog zeigte noch 'in Bearbeitung' (keine Rueckfrage), der
    Fahrer hatte inzwischen abgeschlossen. Das Ausgangs-409 kam vor der
    Stand-Pruefung — ohne "neu laden" baute Termine.jsx den Dialog nie neu auf."""
    w = welt
    t = _abholung(w, "stand")
    alt = _jetzt(-86400)
    e = _fehler(w, t.aid, w.chef, status="storniert", stand=alt)
    assert e.status_code == 409 and e.detail == A.TERMIN_VERALTET_HINWEIS
    assert "neu laden" in e.detail
    aktuell = _doc(w, "appointments", t.aid)["updated_at"]
    e = _fehler(w, t.aid, w.chef, status="storniert", stand=aktuell)
    assert e.status_code == 409 and e.detail == A.AUSGANG_BESTAETIGEN_HINWEIS
    assert _doc(w, "appointments", t.aid)["status"] == "abgeholt"


# ------------------------------------------------------------------ N8
def test_n8_merker_gilt_nur_fuer_die_geltende_uebersteuerung(welt):
    """Befund 8: ausgang_geaendert wurde nie entfernt — veralteter Hinweis,
    dauerhafte Sucher-Sperre, spaetere Wechsel galten als "Rueckweg"."""
    w = welt
    t = _abholung(w, "mk", vorgang=False, protokoll=False, fahrer=False)
    _put(w, t.aid, w.chef, status="nicht abgeholt", ausgang_bestaetigt=True)
    # Chef wechselt zwischen Endzustaenden: der Merker folgt dem Status
    _put(w, t.aid, w.chef, status="storniert")
    ag = _doc(w, "appointments", t.aid)["ausgang_geaendert"]
    assert ag["von_status"] == "abgeholt" and ag["nach_status"] == "storniert"
    assert _fehler(w, t.aid, w.sucher, status="nicht abgeholt").status_code == 403
    # Wiederoeffnen: Merker weg, der Beleg bleibt im Verlauf
    _put(w, t.aid, w.chef, status="offen")
    assert "ausgang_geaendert" not in _doc(w, "appointments", t.aid)
    assert len(_verlauf(w, "termin.ausgang.geaendert", t.aid)) == 1
    # regulaerer Abschluss danach: keine Sperre, kein "Rueckweg"
    assert _put(w, t.aid, w.sucher, status="nicht abgeholt")["ok"] is True
    assert _put(w, t.aid, w.sucher, status="storniert")["ok"] is True
    assert _put(w, t.aid, w.chef, status="abgeholt")["ok"] is True
    assert len(_verlauf(w, "termin.ausgang.geaendert", t.aid)) == 1
    assert "ausgang_geaendert" not in _doc(w, "appointments", t.aid)


# ------------------------------------------------------------------ Anlegen
def test_12_steuerfeld_wird_beim_anlegen_nicht_gespeichert(welt):
    w = welt
    r = w.run(A.create_appointment(A.AppointmentIn(title="x", pickup_date="2099-01-01",
                                                   ausgang_bestaetigt=True), w.chef))
    doc = _doc(w, "appointments", r["id"])
    assert "ausgang_bestaetigt" not in doc and "ausgang_geaendert" not in doc
