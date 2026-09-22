# -*- coding: utf-8 -*-
"""Pruefung 14.09.2026, Listen 5-8 (Betrieb, Marktplatz, Versand, Abholbericht).

  L1-2   stornierte Termine haben eine Aufraeumfrist (CLEANUP_RULES).
  L1-17  Abo-Kuendigung nur im gelesenen Zustand (CAS).
  L1-19  Favorit gezielt setzen/entfernen (?aktiv=).
  L1-20/21 Annahme: Ausnahme im zweiten Write gibt die Reservierung frei;
         Fahrzeug-Lebenszyklus folgt dem Inserat.
  L1-30  manueller Cleanup laeuft unter der Job-Sperre.
  L2-1/2 Abholbericht nur nach finalem Protokoll; Termin wird vor dem
         Speichern erneut geprueft.
  L2-3/4/5 Berichte: mit dem Termin geloescht, Frist, PII mit dem Vertrag.
  L2-7   vorgemerkt geloeschte Fotos werden nicht ausgeliefert.
  L2-9   "nicht abgeholt" durch den Fahrer nimmt eine laufende Freigabe zurueck.
  L4-73  Limiter zaehlt das vorige Fenster anteilig.
  L4-79  SMTP-Idempotenz: kein zweiter Versand nach einem Absturz.
  L5-4/5 Storage: Praefix-Loeschung meldet Reste; fehlende Vormerkung alarmiert.
  L5-6/7 Retry-Eintraege werden beansprucht; aufgegebene nach 24 h erneut.
  L5-8   Termine ohne Vertrag: Protokolle mit Personendaten werden gefunden.
  L6-1/2/3 Vertrag: Lebenszyklus vor dem Speichern; Nacharbeit-Merker;
         Auto-Termin meldet sich auch bei gescheiterter Nacharbeit.
  L6-10  Versionsliste mit X-Truncated.
  server: Worker-Aufsicht meldet tote Hintergrundjobs in /ready.
"""
import asyncio
import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException, Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_golive_20260913_abschluss import (  # noqa: E402,F401  (welt = Fixture)
    _PNG_B64, _abholung, _doc, _fin, _jetzt, _m, welt)


def _fehler(w, coro):
    with pytest.raises(HTTPException) as e:
        w.run(coro)
    return e.value.status_code, e.value.detail


def _vor(sekunden):
    return (datetime.now(timezone.utc) - timedelta(seconds=sekunden)).isoformat()


# ============================================================ L1-2 / L1-30
def test_l1_stornierte_termine_haben_frist_und_cleanup_sperre(welt):
    CS = _m("cleanup_service")
    assert dict(CS.CLEANUP_RULES).get("storniert") == 14
    w = welt
    A = _m("routes.admin")
    JL = _m("job_lock")
    # Sperre wird von einem anderen Lauf gehalten -> 409
    async def _besetzt(db_, name, ttl_seconds=0):
        return False
    echt = JL.acquire
    JL.acquire = _besetzt
    SA = {"id": "sa_r4", "role": "admin", "is_super_admin": True, "dealer_id": None}
    try:
        code, _ = _fehler(w, A.admin_trigger_cleanup(user=SA))
        assert code == 409
    finally:
        JL.acquire = echt


# ============================================================ L2-1/2/9
def test_l2_bericht_nur_nach_protokoll_und_recheck_vor_dem_speichern(welt, monkeypatch):
    w = welt
    D = _m("routes.drivers")
    t = _abholung(w, proto_status="entwurf")
    code, text = _fehler(w, D.driver_submit_report(t.aid, D.PickupReportIn(notes="x"), w.driver))
    assert code == 409 and "Abholprotokoll" in text
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"status": "final",
                                                                     "pdf_path": "x"}}))
    # Termin wird waehrend des Uploads storniert -> Bericht wird nicht gespeichert
    echt = D._zugriff_pruefen

    async def _storno(appt, driver):
        await w.db.appointments.update_one({"id": appt["id"]}, {"$set": {"status": "storniert"}})
        return await echt(appt, driver)
    monkeypatch.setattr(D, "_zugriff_pruefen", _storno)
    code, text = _fehler(w, D.driver_submit_report(t.aid, D.PickupReportIn(notes="x"), w.driver))
    assert code == 409 and "nicht gespeichert" in text
    assert w.run(w.db.pickup_reports.count_documents({"appointment_id": t.aid})) == 0
    monkeypatch.setattr(D, "_zugriff_pruefen", echt)
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"status": "offen"}}))
    assert w.run(D.driver_submit_report(t.aid, D.PickupReportIn(notes="x"), w.driver))["ok"]


def test_l2_nicht_abgeholt_durch_fahrer_nimmt_freigabe_zurueck(welt):
    w = welt
    D = _m("routes.drivers")
    t = _abholung(w, proto_status="zur_freigabe")
    w.run(D.driver_set_status(t.aid, D.DriverStatusIn(status="nicht abgeholt"), w.driver))
    p = _doc(w, "pickup_protocols", t.pid)
    assert p["status"] == "entwurf" and p.get("rueckfrage")
    assert _doc(w, "appointments", t.aid)["status"] == "nicht abgeholt"


# ============================================================ L2-3/4/5/7
def test_l2_berichte_frist_pii_und_vorgemerkte_fotos(welt, monkeypatch):
    w = welt
    CS, D = _m("cleanup_service"), _m("routes.drivers")
    t = _abholung(w, proto_status="final", pdf_path="x", finalized_at=_jetzt())
    alt = (datetime.now(timezone.utc) - timedelta(days=CS.BERICHT_AUFBEWAHRUNG_TAGE + 1)).isoformat()
    key = f"pickup/{w.dealer_id}/r4.jpg"
    w.run(w.db.pickup_reports.insert_one({
        "id": f"rep_{w.s}", "appointment_id": t.aid, "dealer_id": w.dealer_id,
        "driver_account_id": w.driver["id"], "driver_name": "Fahrer GL", "version": 1,
        "superseded": False, "status": "bestaetigt", "notes": "Verkaeufer Max 0170",
        "deviations": [{"id": "d1", "note": "Kratzer bei Max", "photo_key": key}],
        "created_at": _jetzt()}))
    # L2-7: vorgemerkt geloeschtes Foto wird nicht mehr ausgeliefert
    w.run(w.db.pickup_reports.update_one({"id": f"rep_{w.s}"},
                                         {"$set": {"deviations.0.photo_loeschung_offen": True}}))
    assert _fehler(w, D.pickup_foto(key, w.chef))[0] == 404
    assert _fehler(w, D.driver_pickup_foto(key, w.driver))[0] == 404
    w.run(w.db.pickup_reports.update_one({"id": f"rep_{w.s}"},
                                         {"$unset": {"deviations.0.photo_loeschung_offen": ""}}))
    # L2-5: PII-Scrub mit dem Vertrag nimmt Berichtstexte und Fotos mit
    w.run(CS.berichte_pii_entfernen(w.db, [t.aid], _jetzt()))
    r = _doc(w, "pickup_reports", f"rep_{w.s}")
    assert r["notes"] == "" and r["deviations"][0]["note"] == "" and r["pii_geloescht_at"]
    assert not r["deviations"][0].get("photo_key")
    # L2-4: Frist -> Bericht weg. Befund 110 (16.09.2026): erst, wenn der Termin
    # geschlossen ist und der Abschluss selbst hinter der Frist liegt.
    w.run(w.db.pickup_reports.update_one({"id": f"rep_{w.s}"}, {"$set": {"created_at": alt}}))
    w.run(CS.berichte_nach_frist_loeschen(w.db, datetime.now(timezone.utc), {}))
    assert _doc(w, "pickup_reports", f"rep_{w.s}") is not None, "offener Termin: Bericht bleibt"
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"status": "abgeholt", "updated_at": alt}}))
    assert w.run(CS.berichte_nach_frist_loeschen(w.db, datetime.now(timezone.utc), {})) >= 1
    assert _doc(w, "pickup_reports", f"rep_{w.s}") is None
    # L2-3: verwaister Bericht (Termin weg) -> weg
    w.run(w.db.pickup_reports.insert_one({
        "id": f"rep2_{w.s}", "appointment_id": f"weg_{w.s}", "dealer_id": w.dealer_id,
        "deviations": [], "created_at": _jetzt()}))
    assert w.run(CS.berichte_nach_frist_loeschen(w.db, datetime.now(timezone.utc), {})) >= 1
    assert _doc(w, "pickup_reports", f"rep2_{w.s}") is None


# ============================================================ L5-6/7/8
def test_l5_retry_claim_wiederbelebung_und_protokoll_pii(welt, monkeypatch):
    w = welt
    CS = _m("cleanup_service")
    # aufgegebener Eintrag von gestern wird wiederbelebt und beansprucht
    w.run(w.db.storage_delete_retry.insert_one({
        "id": f"sr_{w.s}", "art": "key", "key": f"test/{w.s}.bin", "prefix": None,
        "versuche": 20, "aufgegeben": True, "aufgegeben_am": _vor(25 * 3600),
        "dealer_id": w.dealer_id, "grund": "test", "created_at": _vor(30 * 3600)}))
    SS = _m("storage_service")
    monkeypatch.setattr(SS.storage, "delete", lambda key: True, raising=False)
    n = w.run(CS.storage_loeschungen_nachholen(w.db))
    assert n >= 1 and _doc(w, "storage_delete_retry", f"sr_{w.s}") is None
    # beanspruchter Eintrag (claim_bis in der Zukunft) wird nicht angefasst
    w.run(w.db.storage_delete_retry.insert_one({
        "id": f"sr2_{w.s}", "art": "key", "key": f"test/{w.s}b.bin", "prefix": None,
        "versuche": 0, "dealer_id": w.dealer_id, "grund": "test",
        "claim_bis": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        "created_at": _vor(60)}))
    w.run(CS.storage_loeschungen_nachholen(w.db))
    assert _doc(w, "storage_delete_retry", f"sr2_{w.s}") is not None
    w.run(w.db.storage_delete_retry.delete_one({"id": f"sr2_{w.s}"}))
    # L5-8: Termin ohne Vertrag mit leeren Termin-Feldern, aber PII im Protokoll
    monkeypatch.setenv("VERTRAG_LOESCHUNG_AKTIV", "true")
    t = _abholung(w, proto_status="final", pdf_path="x", finalized_at=_jetzt(), mit_vertrag=False)
    w.run(w.db.appointments.update_one(
        {"id": t.aid}, {"$set": {"seller_name": "", "pickup_address": "", "status": "erledigt",
                                 "pickup_date": "2000-01-01"}}))
    n = w.run(CS.termine_ohne_vertrag_bereinigen(w.db, datetime.now(timezone.utc)))
    assert n >= 1
    p = _doc(w, "pickup_protocols", t.pid)
    assert p["seller_name"] == "" and p.get("pii_geloescht_at") and "pdf_path" not in p


# ============================================================ L6-1/2/3/10
def test_l6_vertrag_lebenszyklus_nacharbeit_und_versionen(welt, monkeypatch):
    w = welt
    C, CS = _m("routes.contracts"), _m("cleanup_service")
    t = _abholung(w, proto_status="entwurf")
    chef = {**w.chef, "active": True}

    def _vertrag(vid, key):
        # Rollenpruefung 22.09.2026 (RP-416): zu diesem Fahrzeug gibt es schon
        # einen offenen Vertrag des Chefs — bewusst ein weiterer.
        return C.ContractIn(vehicle_id=vid, seller_name="Vera", purchase_price=12000,
                            idempotency_key=key, zweiter_vertrag_bestaetigt=True)
    # L6-1: Fahrzeug wird waehrend der PDF-Erzeugung verkauft
    from pymongo import MongoClient
    import os as _os
    client = MongoClient(_os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017")
    sync = client[w.db.name]
    echt = C._pdfs_erzeugen

    def _verkauft(**k):
        sync.vehicles.update_one({"id": t.vid}, {"$set": {"lifecycle": "verkauft"}})
        return echt(**k)
    monkeypatch.setattr(C, "_pdfs_erzeugen", _verkauft)
    code, _ = _fehler(w, C.create_contract(_vertrag(t.vid, "l6-1-" + w.s), chef))
    assert code == 409
    monkeypatch.setattr(C, "_pdfs_erzeugen", echt)
    client.close()
    w.run(w.db.vehicles.update_one({"id": t.vid}, {"$set": {"lifecycle": "verglichen"}}))
    # L6-2: Nacharbeit scheitert -> Merker am Vertrag, Aufraeum-Job holt nach
    KV = _m("kaufvorgang")
    echt_anlegen = KV.anlegen

    async def _kaputt(**k):
        raise RuntimeError("Mongo weg")
    monkeypatch.setattr(KV, "anlegen", _kaputt)
    out = w.run(C.create_contract(_vertrag(t.vid, "l6-2-" + w.s), chef))
    assert out.get("nacharbeit_hinweis")
    monkeypatch.setattr(KV, "anlegen", echt_anlegen)
    c = _doc(w, "generated_pdfs", out["id"])
    assert c["nacharbeit_offen"] is True
    assert w.run(w.db.kaufvorgaenge.count_documents({"contract_id": out["id"]})) == 0
    assert w.run(CS.vertrags_nacharbeit_nachholen(w.db)) >= 1
    assert w.run(w.db.kaufvorgaenge.count_documents({"contract_id": out["id"]})) == 1
    assert "nacharbeit_offen" not in _doc(w, "generated_pdfs", out["id"])
    # L6-10: Versionsliste meldet Abschneiden per Kopfzeile
    antwort = Response()
    fassungen = w.run(C.list_contract_versions(out["id"], antwort, chef))
    assert fassungen == [] and antwort.headers.get("X-Truncated") == "0"


# ============================================================ L1-17 / L1-19 / L1-20/21
def test_l1_abo_kuendigung_cas_favorit_zielzustand_annahme_rollback(welt, monkeypatch):
    w = welt
    DL, M = _m("routes.dealer"), _m("routes.marketplace")
    for mod in (DL, M):
        monkeypatch.setattr(mod, "db", w.db)
    sub = {"id": f"sub_{w.s}", "dealer_id": w.dealer_id, "plan": "monthly", "status": "active",
           "expires_at": "2099-01-01T00:00:00+00:00", "created_at": _jetzt()}
    w.run(w.db.subscriptions.insert_one(sub))
    # Betreiber ersetzt das Abo GENAU zwischen Lesen und Schreiben
    echt = DL.massgebliches_abo

    async def _ersetzt(user):
        s = await echt(user)
        await w.db.subscriptions.update_one({"id": sub["id"]}, {"$set": {"status": "ersetzt"}})
        return s
    monkeypatch.setattr(DL, "massgebliches_abo", _ersetzt)
    code, _ = _fehler(w, DL.dealer_cancel_subscription(w.chef))
    assert code == 409 and _doc(w, "subscriptions", sub["id"])["status"] == "ersetzt"
    # Favorit: Zielzustand
    kaeufer = {"id": f"k_{w.s}", "role": "b2b_buyer", "active": True}
    w.run(w.db.users.insert_one({**kaeufer, "dealer_id": w.dealer_id, "created_at": _jetzt()}))
    w.run(w.db.resale_listings.insert_one({"id": f"l_{w.s}", "dealer_id": w.dealer_id,
                                           "vehicle_id": t_vid(w), "status": "veroeffentlicht",
                                           "visibility": "public"}))
    monkeypatch.setattr(M, "_zugang_erzwingen", lambda u: None)

    async def _sichtbar(u, l):
        return True
    monkeypatch.setattr(M, "_inserat_sichtbar_fuer", _sichtbar)
    assert w.run(M.toggle_favorit(f"l_{w.s}", True, kaeufer))["favorit"] is True
    assert w.run(M.toggle_favorit(f"l_{w.s}", True, kaeufer))["favorit"] is True
    assert w.run(M.toggle_favorit(f"l_{w.s}", False, kaeufer))["favorit"] is False
    assert w.run(M.toggle_favorit(f"l_{w.s}", False, kaeufer))["favorit"] is False
    # Annahme durch den Haendler: zweiter Write wirft -> Reservierung wieder frei
    w.run(w.db.listing_interest.insert_one({
        "id": f"i_{w.s}", "listing_id": f"l_{w.s}", "dealer_id": w.dealer_id,
        "buyer_user_id": kaeufer["id"], "status": "offen", "offer": 9000, "history": []}))
    echt_db = M.db

    class _Interessen:
        def __getattr__(self, n):
            return getattr(echt_db.listing_interest, n)

        async def update_one(self, *a, **k):
            raise RuntimeError("Mongo weg")

    class _DB:
        def __getattr__(self, n):
            return _Interessen() if n == "listing_interest" else getattr(echt_db, n)

        def __getitem__(self, n):
            return self.__getattr__(n)
    monkeypatch.setattr(M, "db", _DB())
    with pytest.raises(RuntimeError):
        w.run(M.answer_interest(f"i_{w.s}", M.InterestAnswerIn(action="akzeptieren"), w.chef))
    monkeypatch.setattr(M, "db", echt_db)
    l = _doc(w, "resale_listings", f"l_{w.s}")
    assert l["status"] == "veroeffentlicht" and "reserved_for" not in l
    w.run(w.db.subscriptions.delete_one({"id": sub["id"]}))
    w.run(w.db.listing_interest.delete_many({"id": f"i_{w.s}"}))
    w.run(w.db.resale_listings.delete_many({"id": f"l_{w.s}"}))
    w.run(w.db.buyer_favorites.delete_many({"buyer_user_id": kaeufer["id"]}))
    w.run(w.db.users.delete_one({"id": kaeufer["id"]}))


def t_vid(w):
    return f"v_a_{w.s}"


# ============================================================ L4-73 / L4-79 / L5-4/5 / Worker
def test_l4_limiter_gleitendes_fenster(welt, monkeypatch):
    RL = _m("rate_limiter")
    monkeypatch.setattr(RL, "_RATE_LIMIT_ENABLED", True)
    lim = RL.SlidingWindowRateLimiter(max_attempts=5, window_seconds=3600, name=f"r4_{welt.s}")
    key = f"k{welt.s}"
    w = welt
    import time as _t
    fenster = int(_t.time() // 3600)
    # voriges Fenster: 5 Versuche -> zaehlen anteilig mit
    w.run(w.db.rate_limits.insert_one({"_id": f"{lim.name}:{key}:{fenster - 1}", "n": 5,
                                       "ablauf": datetime.now(timezone.utc) + timedelta(hours=2)}))
    anteil = 1.0 - (_t.time() % 3600) / 3600
    erlaubt = sum(1 for _ in range(5) if w.run(lim.check(key)))
    erwartet = max(0, int(5 - 5 * anteil))
    assert abs(erlaubt - erwartet) <= 1, (erlaubt, erwartet, anteil)
    w.run(w.db.rate_limits.delete_many({"_id": {"$regex": f"^{lim.name}:"}}))


def test_l4_smtp_kein_zweiter_versand_nach_absturz(welt, monkeypatch):
    w = welt
    ES = _m("email_service")
    gesendet = []
    monkeypatch.setattr(ES, "email_configured", lambda: True)
    monkeypatch.setattr(ES, "resend_aktiv", lambda: False)
    monkeypatch.setattr(ES, "smtp_aktiv", lambda: True)
    monkeypatch.setattr(ES, "_send_sync", lambda **k: gesendet.append(k["to"]))
    key = f"vertrag-r4-{w.s}"
    ok, beleg = w.run(ES.send_email_mit_beleg("v@example.test", "Betreff", "Text",
                                              idempotency_key=key))
    assert ok and beleg == "smtp" and gesendet == ["v@example.test"]
    # Wiederholung unter demselben Schluessel: keine zweite Zustellung
    ok, beleg = w.run(ES.send_email_mit_beleg("v@example.test", "Betreff", "Text",
                                              idempotency_key=key))
    assert ok and beleg == "smtp:bereits" and gesendet == ["v@example.test"]
    # Absturz mitten im Versand (Eintrag 'laeuft' ohne Ergebnis): unklar -> nicht senden
    w.run(w.db.mail_idempotenz.update_one({"key": key}, {"$set": {"status": "laeuft"}}))
    ok, _ = w.run(ES.send_email_mit_beleg("v@example.test", "Betreff", "Text",
                                          idempotency_key=key))
    assert ok is False and gesendet == ["v@example.test"]
    # SMTP lehnt ab -> Schluessel wieder frei
    def _ablehnung(**k):
        raise RuntimeError("SMTP 550")
    monkeypatch.setattr(ES, "_send_sync", _ablehnung)
    w.run(w.db.mail_idempotenz.delete_one({"key": key}))
    ok, _ = w.run(ES.send_email_mit_beleg("v@example.test", "Betreff", "Text",
                                          idempotency_key=key))
    assert ok is False and w.run(w.db.mail_idempotenz.find_one({"key": key})) is None


def test_l5_praefix_loeschung_meldet_reste(tmp_path, monkeypatch):
    SS = _m("storage_service")
    speicher = SS.LocalDiskStorage(tmp_path)
    ordner = tmp_path / "protocol" / "d1"
    ordner.mkdir(parents=True)
    (ordner / "a.pdf").write_bytes(b"x")
    import shutil
    echt = shutil.rmtree

    def _halb(pfad, **k):
        # Datei bleibt liegen, Fehler geht an onexc
        k.get("onexc", lambda *a: None)(None, str(pfad), OSError("gesperrt"))
    monkeypatch.setattr(shutil, "rmtree", _halb)
    with pytest.raises(SS.StorageError):
        speicher.delete_prefix("protocol/d1/")
    monkeypatch.setattr(shutil, "rmtree", echt)
    assert speicher.delete_prefix("protocol/d1/") == 1 and not ordner.exists()


def test_server_worker_aufsicht_startet_neu_und_meldet(welt):
    S = _m("server")
    laeufe = {"n": 0}

    async def _stirbt():
        laeufe["n"] += 1
        if laeufe["n"] == 1:
            raise RuntimeError("Absturz")
        await asyncio.sleep(3600)
    w = welt
    S.WORKER_STATUS.pop(f"test_{w.s}", None)
    alt_db = S.db
    S.db = w.db

    async def lauf():
        S._worker_starten(f"test_{w.s}", _stirbt)
        await asyncio.sleep(0.05)
        st1 = dict(S.WORKER_STATUS[f"test_{w.s}"])
        return st1
    try:
        st1 = w.run(lauf())
        assert st1["laeuft"] is False and st1["neustarts"] == 1 and "Absturz" in st1["letzter_fehler"]
        assert w.run(w.db.betriebsalarme.find_one({"typ": "hintergrundjob_abgestuerzt",
                                                   "ref": f"test_{w.s}", "offen": True}))
    finally:
        S.db = alt_db
        S.WORKER_STATUS.pop(f"test_{w.s}", None)
        w.run(w.db.betriebsalarme.delete_many({"ref": f"test_{w.s}"}))
