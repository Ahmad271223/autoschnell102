# -*- coding: utf-8 -*-
"""Audit 13.09.2026, Bereich "zustaende": Teilzustaende nach bereits
geschriebenen Schritten, Obergrenzen ohne Signal, Limiter-Reset.

  #11  admin_create_sucher prueft die E-Mail plattformweit (auch Fahrerkonten),
       Doppelklick -> 409 statt 500
  #43  Zahlungsabgleich rotiert nach updated_at — Dauerfehler hungern spaetere
       Zahlungen nicht mehr aus
  #44  Neuerzeugung mit neuem Abholtermin: Auto-Daten/Audit nach dem CAS
       liefern keinen 500 mehr
  #45  Vertragsloeschung: Audit-Fehler nach der Kaskade -> kein 500 (manuell),
       kein Abbruch des Aufraeumzyklus (Frist); Spur im Log + Betriebsalarm
  #46  set_lifecycle: Audit nach dem CAS ist best effort
  #47  Limiter-Reset mit exakten Schluesseln statt Regex-Praefix
  #51  Admin-Vertragsansicht je Nutzer nach Rolle, Obergrenze signalisiert
  #52  Admin-Vertragsliste blaettert (page/limit), X-Truncated
  #55  Sucherliste des Chefs signalisiert die Obergrenze
  #59  Fahrer-Loeschung: Grabstein + Sperre zuerst, Verknuepfung zuerst weg,
       kein Entsperren waehrend der Loeschung

In-Prozess gegen eine eigene Wegwerf-DB je Test (wird danach geloescht);
kein Server.
"""
import asyncio
import importlib
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException, Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deps  # noqa: E402  (laedt backend/.env)

MONGO_URL = "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
PDF_STUB = b"%PDF-1.4 test"
SA = {"id": "sa_a0913", "role": "admin", "is_super_admin": True, "active": True,
      "dealer_id": None, "email": "sa_a0913@e2etest-mail.de"}

# Module mit eigenem `db` (from deps import db) — alle auf die Wegwerf-DB.
_MODULE = ["deps", "lifecycle", "auto_daten", "auftraggeber", "kaufvorgang",
           "cleanup_service", "routes.contracts", "routes.appointments",
           "routes.bestand", "routes.admin", "routes.drivers", "routes.payments",
           "routes.team"]


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _m(name):
    return importlib.import_module(name)


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    mods = [_m(n) for n in _MODULE]
    alt = [(m, m.db) for m in mods if hasattr(m, "db")]

    class _W:
        pass

    w = _W()
    w.s = uuid.uuid4().hex[:10]
    w.dealer_id = f"d_a0913_{w.s}"
    w.chef = {"id": f"chef_a0913_{w.s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"{DB_NAME}_a0913_{w.s}"
    w.db = w.client[w.db_name]
    for m, _ in alt:
        m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.run(w.db.users.insert_one({
        "id": w.chef["id"], "dealer_id": w.dealer_id, "role": "dealer", "active": True,
        "email": f"{w.chef['id']}@e2etest-mail.de", "created_at": _jetzt()}))
    w.run(w.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                   "company_name": "A0913 GmbH", "created_at": _jetzt()}))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            m.db = d
        w.client.close()
        w.loop.close()


async def _kaputt(*a, **k):
    raise RuntimeError("DB-Aussetzer (Test)")


def _sammlung_hooken(monkeypatch, methode, sammlung, ersatz):
    """Motor liefert je `db.<name>` ein neues Collection-Objekt — deshalb auf
    Klassenebene haken; `ersatz(orig, self, *a, **k)` nur fuer `sammlung`."""
    from motor.motor_asyncio import AsyncIOMotorCollection as K
    orig = getattr(K, methode)

    async def _hook(self, *a, **k):
        if self.name == sammlung:
            return await ersatz(orig, self, *a, **k)
        return await orig(self, *a, **k)
    monkeypatch.setattr(K, methode, _hook)


def _vertrag(w, cid, **extra):
    doc = {"id": cid, "contract_no": f"KV-{cid[-6:]}", "dealer_id": w.dealer_id,
           "user_id": w.chef["id"], "vehicle_id": f"v_{w.s}", "make": "BMW",
           "model": "320d", "seller_name": "Verkaeufer A", "pickup_date": "2099-09-10",
           "pickup_time": "09:00", "purchase_price": 5000, "version": 1,
           "contract_data": {"seller_name": "Verkaeufer A", "purchase_price": 5000,
                             "pickup_date": "2099-09-10", "pickup_time": "09:00"},
           "pdf_b64": "JVBERi0xLjQgYWx0", "filename": "Kaufvertrag.pdf",
           "send_status": [], "status": "erstellt", "created_at": _jetzt(),
           "updated_at": _jetzt()}
    doc.update(extra)
    return doc


def _fenster_abwarten(sekunden: int = 60, puffer: float = 5.0) -> None:
    """Limiter-Fenster sind feste Minuten — nicht kurz vor dem Wechsel
    starten, sonst zaehlt der letzte check() im neuen Fenster."""
    rest = sekunden - (time.time() % sekunden)
    if rest < puffer:
        time.sleep(rest + 0.2)


# ============================================================ #11
def test_11_sucher_anlage_prueft_fahrerkonten_und_duplicate_key(welt, monkeypatch):
    A = _m("routes.admin")
    mail = f"doppelt-{welt.s}@e2etest-mail.de"
    mail2 = f"klick-{welt.s}@e2etest-mail.de"

    async def lauf():
        await welt.db.driver_accounts.insert_one(
            {"id": f"drv_{welt.s}", "email": mail, "active": True, "password_hash": "x"})
        with pytest.raises(HTTPException) as e1:
            await A.admin_create_sucher(
                welt.dealer_id, A.AdminSucherIn(email=mail, password="Sucher12345!"), admin=SA)
        n_users = await welt.db.users.count_documents({"email": mail})
        # Doppelklick/Rennen: die Vorpruefung sieht das Konto noch nicht, der
        # Unique-Index auf users.email entscheidet -> 409 statt 500.
        await welt.db.users.create_index("email", unique=True)
        await welt.db.users.insert_one({"id": f"u_{welt.s}", "email": mail2,
                                        "role": "sucher", "dealer_id": welt.dealer_id})

        async def _frei(_email):
            return None
        monkeypatch.setattr(deps, "email_vergeben", _frei)
        with pytest.raises(HTTPException) as e2:
            await A.admin_create_sucher(
                welt.dealer_id, A.AdminSucherIn(email=mail2, password="Sucher12345!"), admin=SA)
        return e1.value.status_code, n_users, e2.value.status_code

    c1, n_users, c2 = welt.run(lauf())
    assert c1 == 409 and n_users == 0, "Fahrerkonto mit derselben Adresse -> kein Sucher"
    assert c2 == 409


# ============================================================ #43
def test_43_abgleich_rotiert_dauerfehler_nach_hinten(welt, monkeypatch):
    p = _m("routes.payments")
    jetzt = datetime.now(timezone.utc)
    gesperrt = f"k_gesperrt_{welt.s}"
    sid_k1 = f"cs_test_k1_{welt.s}"
    verbucht = []

    async def _freischalten(tx, sid):
        if tx.get("user_id") == gesperrt:
            raise RuntimeError("Konto gesperrt")
        return (jetzt + timedelta(days=30)).isoformat()

    async def _verbuchen(tx, sid, bis):
        verbucht.append(sid)

    async def _kein_alarm(*a, **k):
        return None
    monkeypatch.setattr(p, "_zugang_freischalten", _freischalten)
    monkeypatch.setattr(p, "_zahlung_verbuchen", _verbuchen)
    monkeypatch.setattr(p, "alarm", _kein_alarm)
    monkeypatch.setattr(p.log, "exception", lambda *a, **k: None)   # 500 Tracebacks

    basis = {"dealer_id": None, "plan": "marktplatz", "amount": 20.0,
             "currency": "eur", "payment_status": "paid"}
    docs = [{**basis, "id": f"tx_{i}_{welt.s}", "session_id": f"cs_test_f{i}_{welt.s}",
             "user_id": gesperrt, "status": "activation_failed",
             "created_at": (jetzt - timedelta(days=2)).isoformat(),
             "updated_at": (jetzt - timedelta(days=1)).isoformat()}
            for i in range(500)]
    docs.append({**basis, "id": f"tx_k1_{welt.s}", "session_id": sid_k1,
                 "user_id": f"k1_{welt.s}", "status": "paid",
                 "created_at": (jetzt - timedelta(hours=1)).isoformat(),
                 "updated_at": (jetzt - timedelta(minutes=5)).isoformat()})

    async def lauf():
        await welt.db.payment_transactions.insert_many(docs)
        st1 = await p.zahlungen_abgleichen(welt.db)
        st2 = await p.zahlungen_abgleichen(welt.db)
        k1 = await welt.db.payment_transactions.find_one({"session_id": sid_k1}, {"_id": 0})
        return st1, st2, k1

    st1, st2, k1 = welt.run(lauf())
    assert k1["status"] == "active", (st1, st2, k1["status"])
    assert verbucht == [sid_k1], "genau ein Beleg fuer K1"
    assert p.ABGLEICH_MAX == 500


# ============================================================ #44
def test_44_neuerzeugung_nach_cas_wirft_nicht(welt, monkeypatch):
    C = _m("routes.contracts")
    AD = _m("auto_daten")
    monkeypatch.setattr(C, "generate_contract_pdf", lambda **k: PDF_STUB)
    monkeypatch.setattr(AD, "aktualisieren", _kaputt)
    monkeypatch.setattr(AD, "nachtragen", _kaputt)
    monkeypatch.setattr(C, "log_activity", _kaputt)
    monkeypatch.setattr(deps, "log_activity", _kaputt)
    mit_ad, ohne_ad = f"c_ad_{welt.s}", f"c_ohne_{welt.s}"

    async def lauf():
        await welt.db.vehicles.insert_one({
            "id": f"v_{welt.s}", "dealer_id": welt.dealer_id, "lifecycle": "vertrag_erstellt",
            "data": {"make_label": "BMW"}, "created_at": _jetzt()})
        await welt.db.generated_pdfs.insert_many([
            _vertrag(welt, mit_ad, admin_vehicle_data_id=f"avd_{welt.s}"),
            _vertrag(welt, ohne_ad)])
        erg = {}
        for cid in (mit_ad, ohne_ad):
            ok = await C.regenerate_contract_for_pickup(
                contract_id=cid, dealer_id=welt.dealer_id, user=welt.chef,
                pickup_date="2099-09-11")
            c = await welt.db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
            n = await welt.db.generated_pdf_versions.count_documents({"contract_id": cid})
            erg[cid] = (ok, c["version"], c["pickup_date"], n)
        return erg

    erg = welt.run(lauf())
    for cid, (ok, version, datum, n_archiv) in erg.items():
        assert ok is True and version == 2 and datum == "2099-09-11" and n_archiv == 1, (cid, erg)


# ============================================================ #45
def test_45_manuelle_loeschung_audit_fehler_kein_500(welt, monkeypatch, caplog):
    C = _m("routes.contracts")
    monkeypatch.setattr(C, "log_activity", _kaputt)
    monkeypatch.setattr(deps, "log_activity", _kaputt)
    cid = f"c_del_{welt.s}"

    async def lauf():
        await welt.db.generated_pdfs.insert_one(_vertrag(welt, cid))
        await welt.db.generated_pdf_versions.insert_one(
            {"id": f"ver_{welt.s}", "contract_id": cid, "dealer_id": welt.dealer_id,
             "version": 1, "pdf_b64": "QUJD"})
        r = await C.delete_contract(cid, welt.chef)
        rest = await welt.db.generated_pdfs.count_documents({"id": cid})
        vers = await welt.db.generated_pdf_versions.count_documents({"contract_id": cid})
        al = await welt.db.betriebsalarme.find_one({"typ": "audit_fehlt", "ref": cid}, {"_id": 0})
        return r, rest, vers, al

    with caplog.at_level(logging.ERROR):
        r, rest, vers, al = welt.run(lauf())
    assert r == {"ok": True} and rest == 0 and vers == 0
    assert any(welt.chef["id"] in rec.getMessage() for rec in caplog.records
               if rec.levelno >= logging.ERROR), "wer geloescht hat, steht im Fehlerlog"
    assert al and al["offen"] is True and al["details"]["user_id"] == welt.chef["id"], al


def test_45b_fristloeschung_audit_fehler_bricht_nicht_ab(welt, monkeypatch):
    CS = _m("cleanup_service")
    cid = f"c_frist_{welt.s}"

    async def _insert_kaputt(orig, self, *a, **k):
        raise RuntimeError("DB-Aussetzer (Test)")
    _sammlung_hooken(monkeypatch, "insert_one", "activity_logs", _insert_kaputt)

    async def lauf():
        await welt.db.generated_pdfs.insert_one(_vertrag(welt, cid))
        ok = await CS.vertrag_endgueltig_loeschen(welt.db, cid, scrub_pii=True, grund="90tage")
        rest = await welt.db.generated_pdfs.count_documents({"id": cid})
        al = await welt.db.betriebsalarme.find_one({"typ": "audit_fehlt", "ref": cid}, {"_id": 0})
        return ok, rest, al

    ok, rest, al = welt.run(lauf())
    assert ok is True and rest == 0
    assert al and al["details"]["aktion"] == "vertrag.geloescht.90tage", al


# ============================================================ #46
def test_46_set_lifecycle_audit_best_effort(welt, monkeypatch):
    L = _m("lifecycle")
    monkeypatch.setattr(L, "log_activity", _kaputt, raising=False)
    monkeypatch.setattr(deps, "log_activity", _kaputt)
    vid = f"v_lc_{welt.s}"

    async def lauf():
        await welt.db.vehicles.insert_one({"id": vid, "dealer_id": welt.dealer_id,
                                           "lifecycle": "abgeholt", "created_at": _jetzt()})
        v = await L.set_lifecycle(vid, welt.dealer_id, "bestand", user=welt.chef)
        doc = await welt.db.vehicles.find_one({"id": vid}, {"_id": 0})
        return v, doc

    v, doc = welt.run(lauf())
    assert v["lifecycle"] == "bestand" and doc["lifecycle"] == "bestand"


# ============================================================ #47
def test_47_limiter_reset_trifft_nur_den_eigenen_schluessel(welt, monkeypatch):
    RL = _m("rate_limiter")
    monkeypatch.setattr(RL, "_RATE_LIMIT_ENABLED", True)
    ip = "203.0.113.5"
    a = RL.login_schluessel(ip, f"a-{welt.s}@buero.test")
    b = RL.login_schluessel(ip, f"b-{welt.s}@buero.test")
    netz = "198.51.100.7"
    plus = RL.login_schluessel("192.0.2.9", f"+info-{welt.s}@buero.test")
    _fenster_abwarten()

    async def lauf():
        # Fall 1: ein Login von Konto A leert nicht den Zaehler des Kollegen B
        for _ in range(10):
            await RL.login_limiter.check(b)
        await RL.login_limiter.reset(a)
        kollege = await RL.login_limiter.check(b)
        # Fall 2: Regex-Zeichen in der Kennung leeren nicht die ganze Sammlung
        for _ in range(RL.login_ip_limiter.max_attempts + 1):
            await RL.login_ip_limiter.check(netz)
        await RL.login_limiter.reset(RL.login_schluessel("192.0.2.10", "a|.|b@buero.test"))
        ip_netz = await RL.login_ip_limiter.check(netz)
        # Fall 3: "+" in der Kennung (ungueltiges Muster) — Reset muss wirken
        for _ in range(10):
            await RL.login_limiter.check(plus)
        await RL.login_limiter.reset(plus)
        eigener = await RL.login_limiter.check(plus)
        return kollege, ip_netz, eigener

    kollege, ip_netz, eigener = welt.run(lauf())
    assert kollege is False, "fremder Zaehler wurde geleert"
    assert ip_netz is False, "IP-Netz-Zaehler wurde geleert"
    assert eigener is True, "eigener Reset wirkt auch mit '+' in der Adresse"


# ============================================================ #51
def test_51_admin_nutzervertraege_nach_rolle_und_obergrenze(welt, monkeypatch):
    A = _m("routes.admin")
    s1, s2, k = f"s1_{welt.s}", f"s2_{welt.s}", f"k_{welt.s}"

    async def lauf():
        await welt.db.users.insert_many([
            {"id": s1, "role": "sucher", "dealer_id": welt.dealer_id, "email": f"{s1}@x.de"},
            {"id": s2, "role": "sucher", "dealer_id": welt.dealer_id, "email": f"{s2}@x.de"},
            {"id": k, "role": "b2b_buyer", "dealer_id": None, "email": f"{k}@x.de"}])
        await welt.db.generated_pdfs.insert_many(
            [_vertrag(welt, f"c_chef{i}_{welt.s}") for i in range(2)]
            + [_vertrag(welt, f"c_s1{i}_{welt.s}", user_id=s1) for i in range(3)]
            # Kuenstliches Alt-Dokument ohne dealer_id — produktiv entsteht so
            # etwas nicht, es macht aber den Filter {dealer_id: None} sichtbar.
            + [{"id": f"c_alt_{welt.s}", "user_id": "fremd", "created_at": _jetzt()}])
        erg = {}
        for uid in (welt.chef["id"], s1, s2, k):
            r = await A.admin_user_contracts(uid, _=SA)
            erg[uid] = (len(r["contracts"]), r.get("umfang"), r.get("abgeschnitten"))
        monkeypatch.setattr(A, "ADMIN_VERTRAEGE_MAX", 3, raising=False)
        gekappt = await A.admin_user_contracts(welt.chef["id"], _=SA)
        return erg, gekappt

    erg, gekappt = welt.run(lauf())
    assert erg[welt.chef["id"]] == (5, "firma", False), erg
    assert erg[s1] == (3, "nutzer", False), erg
    assert erg[s2] == (0, "nutzer", False), erg
    assert erg[k] == (0, "nutzer", False), erg
    assert len(gekappt["contracts"]) == 3 and gekappt["abgeschnitten"] is True


# ============================================================ #52
def test_52_admin_vertragsliste_blaettert_und_meldet_kappung(welt):
    A = _m("routes.admin")
    basis = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ids = [f"c_l{i}_{welt.s}" for i in range(5)]

    async def lauf():
        await welt.db.generated_pdfs.insert_many([
            _vertrag(welt, cid, created_at=(basis + timedelta(minutes=i)).isoformat())
            for i, cid in enumerate(ids)])
        r1, r2, r3 = Response(), Response(), Response()
        s1 = await A.admin_all_contracts(r1, _=SA, page=1, limit=3)
        s2 = await A.admin_all_contracts(r2, _=SA, page=2, limit=3)
        alle = await A.admin_all_contracts(r3, _=SA)
        return s1, s2, alle, r1, r2, r3

    s1, s2, alle, r1, r2, r3 = welt.run(lauf())
    assert [c["id"] for c in s1] == [ids[4], ids[3], ids[2]]
    assert r1.headers["X-Truncated"] == "1"
    assert [c["id"] for c in s2] == [ids[1], ids[0]] and r2.headers["X-Truncated"] == "0"
    assert len(alle) == 5 and r3.headers["X-Truncated"] == "0"
    assert all("pdf_b64" not in c for c in alle)


# ============================================================ #55
def test_55_sucherliste_meldet_obergrenze(welt):
    T = _m("routes.team")

    async def lauf():
        await welt.db.users.insert_many([
            {"id": f"su{i}_{welt.s}", "role": "sucher", "dealer_id": welt.dealer_id,
             "email": f"su{i}_{welt.s}@x.de", "active": True,
             "created_at": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00"}
            for i in range(1001)])
        resp = Response()
        items = await T.list_sucher(resp, user=welt.chef)
        return items, resp

    items, resp = welt.run(lauf())
    assert len(items) == 1000
    assert resp.headers.get("X-Truncated") == "1"


# ============================================================ #59
def test_59_fahrer_loeschung_sperrt_zuerst_und_ist_wiederaufnehmbar(welt, monkeypatch):
    A = _m("routes.admin")
    DR = _m("routes.drivers")
    did = f"drv_{welt.s}"
    orig = DR.fahrer_konto_anonymisieren

    async def _abbruch(db, driver_id):
        # erster Schritt gelingt, dann Primary-Wechsel
        await db.dealer_drivers.delete_many({"driver_account_id": driver_id})
        raise RuntimeError("Primary-Wechsel (Test)")

    async def vorbereiten():
        await welt.db.driver_accounts.insert_one({
            "id": did, "email": f"{did}@e2etest-mail.de", "driver_code": "FA0913",
            "active": True, "current_session_id": "sitz-fahrer", "password_hash": "x",
            "created_at": _jetzt()})
        await welt.db.dealer_drivers.insert_one({"id": f"dd_{welt.s}", "dealer_id": welt.dealer_id,
                                                 "driver_account_id": did})
        await welt.db.appointments.insert_one({"id": f"a_{welt.s}", "dealer_id": welt.dealer_id,
                                               "driver_id": did, "status": "offen"})
    welt.run(vorbereiten())

    monkeypatch.setattr(DR, "fahrer_konto_anonymisieren", _abbruch)
    with pytest.raises(RuntimeError):
        welt.run(A.admin_delete_driver(did, admin=SA))
    d = welt.run(welt.db.driver_accounts.find_one({"id": did}, {"_id": 0}))
    start = welt.run(welt.db.activity_logs.find_one(
        {"action": "admin.fahrer.loeschung.gestartet", "ref": did}, {"_id": 0}))
    assert d["active"] is False and d["current_session_id"] is None, d
    assert (d.get("loeschung") or {}).get("status") == "laeuft", d
    assert start is not None, "Start-Audit vor dem ersten destruktiven Schritt"

    # Entsperren waehrend der Loeschung haebe die Sperre auf -> 409
    with pytest.raises(HTTPException) as e:
        welt.run(A.admin_driver_set_active(did, A.AdminActiveIn(active=True), admin=SA))
    assert e.value.status_code == 409

    # Wiederholung fuehrt zu Ende; ein Fehler im Abschluss-Audit (nach dem
    # Loeschen) liefert keinen 500 mehr.
    monkeypatch.setattr(DR, "fahrer_konto_anonymisieren", orig)
    monkeypatch.setattr(deps, "log_activity", _kaputt)
    r = welt.run(A.admin_delete_driver(did, admin=SA))
    assert r["ok"] is True

    async def rest():
        return (await welt.db.driver_accounts.count_documents({"id": did}),
                await welt.db.appointments.count_documents({"driver_id": did}),
                await welt.db.dealer_drivers.count_documents({"driver_account_id": did}))
    assert welt.run(rest()) == (0, 0, 0)


def test_59b_zuweisung_waehrend_loeschung_wird_zurueckgenommen(welt, monkeypatch):
    DR = _m("routes.drivers")
    AP = _m("routes.appointments")
    did = f"drv_race_{welt.s}"
    aid = f"a_race_{welt.s}"
    zustand = {}

    async def _mitten(orig, self, *a, **k):
        # Die Termin-Pseudonymisierung ist durch; GENAU jetzt landet der Write
        # einer Chef-Zuweisung, deren _fahrer_pruefen die Verknuepfung noch sah.
        if "nachpruefung" not in zustand:
            await welt.db.appointments.insert_one({"id": aid, "dealer_id": welt.dealer_id,
                                                   "driver_id": did, "status": "offen"})
            zustand["nachpruefung"] = await AP._fahrer_nachpruefen(aid, welt.dealer_id, did)
        return await orig(self, *a, **k)
    _sammlung_hooken(monkeypatch, "update_many", "pickup_reports", _mitten)

    async def lauf():
        await welt.db.driver_accounts.insert_one({"id": did, "email": f"{did}@x.de", "active": True})
        await welt.db.dealer_drivers.insert_one({"id": f"dd_r_{welt.s}", "dealer_id": welt.dealer_id,
                                                 "driver_account_id": did})
        await DR.fahrer_konto_anonymisieren(welt.db, did)
        return await welt.db.appointments.find_one({"id": aid}, {"_id": 0})

    t = welt.run(lauf())
    assert zustand["nachpruefung"] is False, "Verknuepfung muss schon weg sein"
    assert "driver_id" not in t, t
