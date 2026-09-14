# -*- coding: utf-8 -*-
"""Go-Live 13.09.2026 — Bereich "abschluss": Protokoll-Abschluss darf sich
nicht mit einem anderen Abschluss, Vertrag oder Vorgang vermischen.

  * P1  Uebernahme nach Claim-Ablauf: der erste Abschluss schrieb danach
        trotzdem final (PDF von A, Preis von B, Kaufvorgang von A).
  * P2  Rollback gab den Claim eines neuen Abschlusses frei; finaler Write
        auch ueber einen Entwurf.
  * N1  Zweiter Tipp waehrend des Abschlusses las "zuerst zur Freigabe".
  * P3 / P6-Zusatz-Umhaengen  Vertrags-/Fahrzeugwechsel am Termin waehrend
        Freigabe/Abschluss vermischte PDF von Vertrag A mit Termin/Vorgang B.

Fachregel: dasselbe Auto darf beliebig viele Vertraege, Vorgaenge und Termine
haben — gesperrt wird nur das Umhaengen EINER laufenden Abholung.

In-Prozess (kein Server) gegen DB_NAME; Storage und PDF sind im Speicher
gefaelscht. Rennen deterministisch per Haken im PDF-Speichern.
"""
import asyncio
import base64
import importlib
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()
_ABGELAUFEN = "2000-01-01T00:00:00+00:00"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _m(name):
    return importlib.import_module(name)


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    namen = ["deps", "routes.appointments", "routes.contracts", "routes.protocols",
             "routes.drivers", "routes.bestand", "lifecycle", "kaufvorgang", "auftraggeber"]
    mods = [_m(n) for n in namen]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s = uuid.uuid4().hex[:10]
    w.dealer_id = f"d_glab_{s}"
    w.chef = {"id": f"chef_glab_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    # Kontonummer (13.09.2026): Fahrerkonten wie aus der Kontenanlage des
    # Super-Admins — Kontonummer aus der gemeinsamen Reihe und FD-Code (beide
    # eindeutig), KEINE E-Mail (nur Kontakt, optional). kontonummer und
    # driver_code traegt anlegen() in diese Dicts nach.
    w.driver = {"id": f"f_glab_{s}", "display_name": "Fahrer GL", "active": True}
    w.driver2 = {"id": f"f2_glab_{s}", "display_name": "Fahrer GL 2", "active": True}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db = w.client[DB_NAME]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)

    # Storage im Speicher: Unterschriften (sync), PDF (async, mit Haken), Loeschen.
    w.pdfs, w.geloescht, w.pdf_preise = [], [], []
    w.save_hook = None
    SS = _m("storage_service")

    class _Speicher:
        def save(self, key, data):
            return key

    async def _save(key, data):
        w.pdfs.append(key)
        if w.save_hook is not None:
            await w.save_hook(key)
        return key

    async def _delete(key):
        w.geloescht.append(key)
        return True

    monkeypatch.setattr(SS, "storage", _Speicher())
    monkeypatch.setattr(SS, "make_key", lambda *a, **k: f"test/{uuid.uuid4().hex}.bin")
    monkeypatch.setattr(SS, "save_async", _save)
    monkeypatch.setattr(SS, "delete_async", _delete)
    PDF = _m("pickup_pdf_service")

    def _pdf(**k):
        w.pdf_preise.append((k.get("contract") or {}).get("purchase_price"))
        return b"%PDF-1.4 test"
    monkeypatch.setattr(PDF, "build_pickup_pdf", _pdf)
    C = _m("routes.contracts")

    async def _regen(**k):
        return True
    monkeypatch.setattr(C, "regenerate_contract_for_pickup", _regen)

    async def anlegen():
        # Chef ohne users.email (der Betreiber legt Chefs ohne Adresse an).
        await w.db.users.insert_one({**w.chef, "active": True, "created_at": _jetzt()})
        await w.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                       "company_name": "GL Abschluss", "created_at": _jetzt()})
        K = _m("kontenanlage")
        for d in (w.driver, w.driver2):
            erg = await K.fahrer_anlegen(w.db, {**d, "created_at": _jetzt()})
            d.update(kontonummer=erg["kontonummer"], driver_code=erg["driver_code"])
            konto = await w.db.driver_accounts.find_one({"id": d["id"]}, {"_id": 0})
            assert konto["kontonummer"].isdigit() and "email" not in konto, konto
            await w.db.dealer_drivers.insert_one(
                {"id": str(uuid.uuid4()), "dealer_id": w.dealer_id, "driver_account_id": d["id"],
                 "display_name": d["display_name"], "added_at": _jetzt()})

    async def aufraeumen():
        for c in ("appointments", "vehicles", "activity_logs", "generated_pdfs", "users",
                  "pickup_protocols", "dealer_drivers", "storage_delete_retry", "kaufvorgaenge"):
            await w.db[c].delete_many({"dealer_id": w.dealer_id})
        await w.db.dealers.delete_many({"id": w.dealer_id})
        await w.db.driver_accounts.delete_many({"id": {"$in": [w.driver["id"], w.driver2["id"]]}})
        await w.db.betriebsalarme.delete_many({"ref": {"$regex": s}})

    # Auch ein Fehler beim Anlegen raeumt auf — sonst blieben Konten ohne
    # driver_code in der gemeinsamen Test-DB und stoerten andere Tests.
    try:
        w.run(anlegen())
        yield w
    finally:
        try:
            w.run(aufraeumen())
        finally:
            for m, d in alt:
                if d is not None:
                    m.db = d
            w.client.close()
            w.loop.close()


class _T:
    pass


def _abholung(w, *, mit_vertrag=True, name="a", proto_status="freigegeben", **proto_extra):
    """Ein Auto, Vertrag A (Termin, Vorgang kA) und Vertrag B (eigener Vorgang
    kB ohne Termin) — beide zum SELBEN Fahrzeug, wie es die Fachregel erlaubt."""
    t = _T()
    s = f"{name}_{w.s}"
    t.aid, t.vid, t.vid2, t.pid = f"t_{s}", f"v_{s}", f"v2_{s}", f"p_{s}"
    t.ca, t.ka, t.cb, t.kb = f"cA_{s}", f"kA_{s}", f"cB_{s}", f"kB_{s}"

    async def anlegen():
        for vid in (t.vid, t.vid2):
            await w.db.vehicles.insert_one({"id": vid, "dealer_id": w.dealer_id,
                                            "lifecycle": "abholung_geplant", "created_at": _jetzt(),
                                            "data": {"make_label": "BMW", "model_label": "320d"}})
        termin = {"id": t.aid, "dealer_id": w.dealer_id, "driver_id": w.driver["id"],
                  "vehicle_id": t.vid, "status": "offen", "created_by": w.chef["id"],
                  "pickup_date": "2099-09-10", "pickup_time": "10:00", "seller_name": "Vera",
                  "pickup_address": "Teststr. 1", "created_at": _jetzt()}
        if mit_vertrag:
            for cid, kid, preis, termin_id, st in ((t.ca, t.ka, 10000.0, t.aid, "abholung_geplant"),
                                                   (t.cb, t.kb, 20000.0, None, "vertrag_erstellt")):
                await w.db.generated_pdfs.insert_one(
                    {"id": cid, "dealer_id": w.dealer_id, "user_id": w.chef["id"],
                     "vehicle_id": t.vid, "kaufvorgang_id": kid, "appointment_id": termin_id,
                     "status": "Termin erstellt" if termin_id else "erstellt", "version": 1,
                     "contract_data": {"seller_name": "Vera", "purchase_price": preis},
                     "created_at": _jetzt()})
                await w.db.kaufvorgaenge.insert_one(
                    {"id": kid, "dealer_id": w.dealer_id, "user_id": w.chef["id"],
                     "vehicle_id": t.vid, "contract_id": cid, "purchase_price": preis,
                     "status": st, "appointment_id": termin_id,
                     "created_at": _jetzt(), "updated_at": _jetzt()})
            termin.update({"contract_id": t.ca, "kaufvorgang_id": t.ka})
        await w.db.appointments.insert_one(termin)
        P = _m("routes.protocols")
        doc = {"id": t.pid, "appointment_id": t.aid, "dealer_id": w.dealer_id,
               "vehicle_id": t.vid, "driver_account_id": w.driver["id"],
               "driver_name": w.driver["display_name"], "version": 1, "status": proto_status,
               "superseded": False,
               "vehicle_check": {k: {"status": "stimmt"} for k, _l, _o in P.VEHICLE_CHECK_FIELDS},
               "condition": {"mileage": "123456"}, "keys_count": "2", "damages_confirmed": True,
               "place": "Hannover", "neuer_preis": 9000.0, "freigabe_stand": "s1",
               "created_at": _jetzt()}
        doc.update(proto_extra)
        await w.db.pickup_protocols.insert_one(doc)
    w.run(anlegen())
    return t


def _fin(P, preis=9000.0, stand="s1"):
    return P.FinalizeIn(signature_driver_b64=_PNG_B64, signature_seller_b64=_PNG_B64,
                        seller_name="Vera", place="Hannover",
                        neuer_preis_gesehen=preis, freigabe_stand_gesehen=stand)


async def _claim_ablaufen(w, pid):
    await w.db.pickup_protocols.update_one({"id": pid}, {"$set": {"claim_bis": _ABGELAUFEN}})


def _doc(w, coll, ident):
    return w.run(w.db[coll].find_one({"id": ident}, {"_id": 0}))


# ============================================================ P1
def test_p1_uebernahme_nach_claimablauf_vermischt_nicht(welt):
    """A haengt im PDF-Speichern, der Claim laeuft ab, der Zaehler des Chefs
    gibt frei, der Chef aendert den Preis, B schliesst ab — A darf danach
    NICHT mehr final schreiben (vorher: PDF von A, Preis 8000, Vorgang 9000)."""
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)
    zw = {}

    async def hook(key):
        if len(w.pdfs) == 1:                       # PDF von A
            await _claim_ablaufen(w, t.pid)
            await P._abgelaufene_claims_freigeben({"dealer_id": w.dealer_id})
            r = await P.protokoll_freigeben(t.pid, P.FreigabeIn(neuer_preis=8000.0, stand="s1"), w.chef)
            zw["B"] = await P.finalize_protocol(t.aid, _fin(P, 8000.0, r["stand"]), w.driver)
    w.save_hook = hook

    with pytest.raises(HTTPException) as fehler:
        w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert fehler.value.status_code == 409 and "neu laden" in fehler.value.detail, fehler.value.detail
    assert zw["B"]["ok"] is True
    proto = _doc(w, "pickup_protocols", t.pid)
    assert proto["status"] == "final" and proto["pdf_path"] == w.pdfs[1], "PDF von B bleibt"
    assert proto["neuer_preis"] == 8000.0
    assert "claim_token" not in proto and "claim_bis" not in proto
    assert _doc(w, "kaufvorgaenge", t.ka)["purchase_price"] == 8000.0
    assert w.pdfs[0] in w.geloescht and w.pdfs[1] not in w.geloescht, "nur die Dateien von A verworfen"


def test_p1_langsamer_unbestrittener_abschluss_bleibt_ok(welt):
    """Variante 5: Gibt nur der Zaehler den abgelaufenen Claim frei und aendert
    sich sonst nichts, endet der langsame Abschluss nicht unnoetig mit 409."""
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)

    async def hook(key):
        await _claim_ablaufen(w, t.pid)
        await P._abgelaufene_claims_freigeben({"dealer_id": w.dealer_id})
    w.save_hook = hook

    assert w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))["ok"] is True
    proto = _doc(w, "pickup_protocols", t.pid)
    assert proto["status"] == "final" and proto["pdf_path"] == w.pdfs[0]
    assert "claim_token" not in proto and not w.geloescht
    assert _doc(w, "appointments", t.aid)["status"] == "abgeholt"


def test_p1_gleichzeitig_genau_ein_abschluss(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)

    async def beide():
        # Deterministisch: Der Abschluss, der zuerst im PDF-Speichern ist, haelt
        # dort, bis der andere zurueck ist. Ohne den Haken lief der andere je
        # nach Timing (volle Suite) erst NACH dem finalen Write los und landete —
        # fachlich richtig — in der Selbstheilung (ok, nachgezogen) statt im
        # Konflikt. Erreichen beide das Speichern (der Fehlerfall), wartet der
        # zweite nicht und die Pruefungen unten schlagen an.
        anderer_fertig = asyncio.Event()

        async def hook(key):
            if len(w.pdfs) == 1:
                try:
                    await asyncio.wait_for(anderer_fertig.wait(), 10)
                except asyncio.TimeoutError:
                    pass
        w.save_hook = hook

        async def abschluss():
            try:
                return await P.finalize_protocol(t.aid, _fin(P), w.driver)
            finally:
                anderer_fertig.set()
        return await asyncio.gather(abschluss(), abschluss(), return_exceptions=True)
    ergebnisse = w.run(beide())
    ok = [e for e in ergebnisse if isinstance(e, dict) and e.get("ok")]
    konflikt = [e for e in ergebnisse if isinstance(e, HTTPException) and e.status_code == 409]
    assert len(ok) == 1 and len(konflikt) == 1, ergebnisse
    assert len(w.pdfs) == 1 and _doc(w, "pickup_protocols", t.pid)["status"] == "final"


def test_p1_korrektur_uebernimmt_kein_claim_token(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="final", claim_token="rest-aus-mischbetrieb",
                  contract_id="cX", kaufvorgang_id="kX", pdf_path="test/alt.pdf")
    neu = w.run(P.start_correction(t.aid, w.driver))
    for feld in ("claim_token", "contract_id", "kaufvorgang_id"):
        assert feld not in neu, feld


# ============================================================ P2
def test_p2_rollback_gibt_fremden_claim_nicht_frei(welt):
    """A scheitert (Storage), nachdem B den abgelaufenen Claim uebernommen hat:
    A's Rollback darf B's Claim nicht auf 'freigegeben' setzen — sonst konnte
    der Chef zurueckschicken und B machte trotzdem final."""
    w = welt
    P = _m("routes.protocols")
    from storage_service import StorageError
    t = _abholung(w)
    ctx = {}

    async def lauf():
        b_im_save, b_weiter = asyncio.Event(), asyncio.Event()

        async def hook(key):
            if len(w.pdfs) == 1:                   # A
                await _claim_ablaufen(w, t.pid)
                ctx["B"] = asyncio.ensure_future(P.finalize_protocol(t.aid, _fin(P), w.driver))
                await b_im_save.wait()
                raise StorageError("S3 haengt")
            b_im_save.set()                        # B
            await b_weiter.wait()
        w.save_hook = hook
        try:
            try:
                await P.finalize_protocol(t.aid, _fin(P), w.driver)
            except HTTPException as exc:
                ctx["A"] = exc.status_code
            ctx["nach_rollback_A"] = await w.db.pickup_protocols.find_one({"id": t.pid}, {"_id": 0})
            try:
                await P.protokoll_freigeben(t.pid, P.FreigabeIn(zurueck=True, notiz="Km", stand="s1"),
                                            w.chef)
                ctx["zurueck"] = "angenommen"
            except HTTPException as exc:
                ctx["zurueck"] = exc.status_code
        finally:
            b_weiter.set()
            if "B" in ctx:
                ctx["B_ergebnis"] = await ctx["B"]
    w.run(lauf())

    assert ctx["A"] == 400
    assert ctx["nach_rollback_A"]["status"] == "wird_abgeschlossen", "Claim von B bleibt"
    assert ctx["zurueck"] == 409
    assert ctx["B_ergebnis"]["ok"] is True
    ende = _doc(w, "pickup_protocols", t.pid)
    assert ende["status"] == "final" and not ende.get("rueckfrage") and ende["pdf_path"] == w.pdfs[1]
    assert w.pdfs[0] in w.geloescht and w.pdfs[1] not in w.geloescht


def test_p2_autospeichern_nach_claimablauf_wird_nicht_final(welt):
    """Autospeichern machte nach Claim-Ablauf einen Entwurf — der laufende
    Abschluss schrieb trotzdem final (PDF ohne die neue Notiz)."""
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)
    ctx = {}

    async def hook(key):
        if "save" not in ctx:
            await _claim_ablaufen(w, t.pid)
            ctx["save"] = await P.save_protocol(t.aid, P.ProtocolIn(notes="nachgetragen"), w.driver)
    w.save_hook = hook

    with pytest.raises(HTTPException) as fehler:
        w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert fehler.value.status_code == 409
    assert ctx["save"]["status"] == "entwurf"
    proto = _doc(w, "pickup_protocols", t.pid)
    assert proto["status"] == "entwurf" and not proto.get("pdf_path") and proto["notes"] == "nachgetragen"
    assert w.pdfs[0] in w.geloescht
    assert _doc(w, "appointments", t.aid)["status"] == "offen"


# ============================================================ N1
def test_n1_zweiter_tipp_waehrend_abschluss_meldet_wird_abgeschlossen(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)
    ctx = {}

    async def hook(key):
        if "B" not in ctx:
            ctx["B"] = "laeuft"
            try:
                await P.finalize_protocol(t.aid, _fin(P), w.driver)
                ctx["B"] = "ok"
            except HTTPException as exc:
                ctx["B"] = (exc.status_code, exc.detail)
    w.save_hook = hook

    assert w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))["ok"] is True
    code, text = ctx["B"]
    assert code == 409 and "gerade abgeschlossen" in text and "zur Freigabe" not in text, text


# ============================================================ P3 / P6-Zusatz-Umhaengen
def test_p3_put_sperrt_umhaengen_waehrend_freigabe_und_abschluss(welt):
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w)
    in_zukunft = "2999-01-01T00:00:00+00:00"

    def put(**felder):
        return w.run(A.update_appointment(t.aid, A.AppointmentIn(**felder), w.chef))

    for status in ("zur_freigabe", "freigegeben", "wird_abgeschlossen"):
        w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"status": status,
                                                                         "claim_bis": in_zukunft}}))
        for felder in ({"contract_id": t.cb}, {"contract_loesen": True}):
            with pytest.raises(HTTPException) as fehler:
                put(**felder)
            assert fehler.value.status_code == 409 and "Protokoll" in fehler.value.detail, \
                (status, felder, fehler.value.detail)
        # Termine.jsx schickt das ganze Objekt — unveraenderte contract_id stoert nicht.
        assert put(contract_id=t.ca, vehicle_id=t.vid, notes=f"n-{status}")
        termin = _doc(w, "appointments", t.aid)
        assert termin["contract_id"] == t.ca and termin["notes"] == f"n-{status}"

    # Fahrerwechsel bleibt bei freigegebenem Protokoll erlaubt.
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"status": "freigegeben"}}))
    put(driver_id=w.driver2["id"])
    assert _doc(w, "appointments", t.aid)["driver_id"] == w.driver2["id"]
    assert _doc(w, "kaufvorgaenge", t.kb)["status"] == "vertrag_erstellt"

    # Entwurf: Umhaengen erlaubt.
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"status": "entwurf"}}))
    put(contract_id=t.cb)
    assert _doc(w, "appointments", t.aid)["contract_id"] == t.cb


def test_p3_put_fahrzeugwechsel_gesperrt_ohne_protokoll_frei(welt):
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w, mit_vertrag=False, name="ohnevertrag")

    def put(**felder):
        return w.run(A.update_appointment(t.aid, A.AppointmentIn(**felder), w.chef))

    for felder in ({"vehicle_id": t.vid2}, {"fahrzeug_loesen": True}):
        with pytest.raises(HTTPException) as fehler:
            put(**felder)
        assert fehler.value.status_code == 409 and "Protokoll" in fehler.value.detail, fehler.value.detail
    assert _doc(w, "appointments", t.aid)["vehicle_id"] == t.vid

    w.run(w.db.pickup_protocols.delete_one({"id": t.pid}))
    put(vehicle_id=t.vid2)
    assert _doc(w, "appointments", t.aid)["vehicle_id"] == t.vid2


def test_p3_put_waehrend_abschluss_abgelehnt_abschluss_sauber(welt):
    """Vorher: PUT contract_id=B waehrend des PDF-Speicherns ging durch — PDF von
    Vertrag A, Termin auf B abgeholt, Vorgang A abgeholt, Vorgang B offen."""
    w = welt
    P = _m("routes.protocols")
    A = _m("routes.appointments")
    t = _abholung(w)
    ctx = {}

    async def hook(key):
        if "put" not in ctx:
            try:
                ctx["put"] = await A.update_appointment(t.aid, A.AppointmentIn(contract_id=t.cb), w.chef)
            except HTTPException as exc:
                ctx["put"] = exc.status_code
    w.save_hook = hook

    fertig = w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert ctx["put"] == 409
    assert fertig["ok"] is True and "hinweis" not in fertig
    termin = _doc(w, "appointments", t.aid)
    assert (termin["contract_id"], termin["kaufvorgang_id"], termin["status"]) == (t.ca, t.ka, "abgeholt")
    ka, kb = _doc(w, "kaufvorgaenge", t.ka), _doc(w, "kaufvorgaenge", t.kb)
    assert ka["status"] == "abgeholt" and ka["purchase_price"] == 9000.0
    assert kb["status"] == "vertrag_erstellt" and kb["purchase_price"] == 20000.0
    assert w.pdf_preise == [10000.0]
    proto = _doc(w, "pickup_protocols", t.pid)
    assert (proto["contract_id"], proto["kaufvorgang_id"]) == (t.ca, t.ka)


def test_p3_direktes_umhaengen_im_abschluss_bricht_ab(welt):
    """Umgehung am PUT vorbei (direkter Write): der Abschluss wird nicht final."""
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)

    async def hook(key):
        await w.db.appointments.update_one({"id": t.aid},
                                           {"$set": {"contract_id": t.cb, "kaufvorgang_id": t.kb}})
    w.save_hook = hook

    with pytest.raises(HTTPException) as fehler:
        w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert fehler.value.status_code == 409 and "Termin" in fehler.value.detail, fehler.value.detail
    proto = _doc(w, "pickup_protocols", t.pid)
    assert proto["status"] == "freigegeben" and not proto.get("pdf_path") and "claim_token" not in proto
    assert w.pdfs[0] in w.geloescht
    ka, kb = _doc(w, "kaufvorgaenge", t.ka), _doc(w, "kaufvorgaenge", t.kb)
    assert (ka["status"], ka["purchase_price"]) == ("abholung_geplant", 10000.0)
    assert (kb["status"], kb["purchase_price"]) == ("vertrag_erstellt", 20000.0)
    assert _doc(w, "appointments", t.aid)["status"] == "offen"


def test_p3_umhaengen_nach_protokollwrite_keine_heilung(welt, monkeypatch):
    """Wird der Termin genau zwischen Protokoll-Write und Termin-Write (oder nach
    einem Abbruch dort) umgehaengt, darf weder der Abschluss noch die
    Selbstheilung Termin/Vorgang B mit dem PDF von Vertrag A abholen."""
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)
    echt = P._termin_abgeholt_setzen
    ctx = {"n": 0}

    async def dazwischen(*a, **k):
        ctx["n"] += 1
        if ctx["n"] == 1:
            await w.db.appointments.update_one({"id": t.aid},
                                               {"$set": {"contract_id": t.cb, "kaufvorgang_id": t.kb}})
        return await echt(*a, **k)
    monkeypatch.setattr(P, "_termin_abgeholt_setzen", dazwischen)

    erst = w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert erst["ok"] is True and erst.get("hinweis"), erst
    assert _doc(w, "appointments", t.aid)["status"] == "offen"
    assert _doc(w, "kaufvorgaenge", t.ka)["status"] == "abholung_geplant"
    assert _doc(w, "kaufvorgaenge", t.kb)["status"] == "vertrag_erstellt"

    heil = w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert heil.get("nachgezogen") and "anderen Vertrag" in heil.get("hinweis", ""), heil
    assert _doc(w, "appointments", t.aid)["status"] == "offen"
    assert _doc(w, "kaufvorgaenge", t.kb)["status"] == "vertrag_erstellt"
    alarm = w.run(w.db.betriebsalarme.find_one({"typ": "protokoll_final_termin_umgehaengt", "ref": t.aid}))
    assert alarm and alarm["offen"] is True


def test_p3_geloeschter_vertrag_im_abschluss_blockiert_nicht(welt):
    """Gegenprobe: Die Vertragsloeschung setzt contract_id=None — das ist kein
    Umhaengen, der Abschluss laeuft durch (Vorgang A wird abgeholt)."""
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)

    async def hook(key):
        await w.db.appointments.update_one({"id": t.aid}, {"$set": {"contract_id": None}})
    w.save_hook = hook

    fertig = w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert fertig["ok"] is True and "hinweis" not in fertig, fertig
    assert _doc(w, "appointments", t.aid)["status"] == "abgeholt"
    assert _doc(w, "kaufvorgaenge", t.ka)["status"] == "abgeholt"
