# -*- coding: utf-8 -*-
"""Runde 30 (12.09.2026, Wunsch Ahmad): Freigabe des Chefs vor den
Unterschriften.

Ablauf vor Ort:
  1. Der Fahrer fuellt das Abholprotokoll aus und SCHICKT ES AB — noch ohne
     Unterschriften.
  2. Der Chef sieht das ausgefuellte Protokoll: was weicht vom Vertrag ab,
     welche Schaeden sind neu. Er ruft den Verkaeufer an und verhandelt nach.
  3. Er gibt frei — mit dem neuen Preis.
  4. Erst DANN unterschreiben Verkaeufer und Fahrer, und das Protokoll wird
     endgueltig. Der neue Preis steht im PDF und im Kaufvorgang.

In-Prozess gegen eine Wegwerf-DB; kein Server.
"""
import asyncio
import base64
import importlib
import io
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = "mongodb://127.0.0.1:27017"
_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _modul(name):
    return importlib.import_module(name)


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    namen = ["deps", "routes.protocols", "routes.bestand", "routes.drivers",
             "routes.appointments", "kaufvorgang", "lifecycle", "auftraggeber"]
    mods = [_modul(n) for n in namen]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_r30_{s}"
    w.fremd_id = f"dx_r30_{s}"
    w.chef = {"id": f"chef_r30_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.fremd_chef = {"id": f"cx_r30_{s}", "dealer_id": w.fremd_id, "role": "dealer"}
    w.driver = {"id": f"f_r30_{s}", "display_name": "Fahrer R30"}
    w.aid = f"t_r30_{s}"
    w.cid = f"c_r30_{s}"
    w.vid = f"v_r30_{s}"
    w.kid = f"k_r30_{s}"
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_r30_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)

    # Das Speichern der Unterschriften/PDFs geht nicht in den echten Storage.
    SS = _modul("storage_service")
    monkeypatch.setattr(SS, "make_key", lambda *a, **k: f"test/{uuid.uuid4().hex}.bin")

    async def _save(key, daten):
        return key
    monkeypatch.setattr(SS, "save_async", _save, raising=False)
    P = _modul("routes.protocols")
    monkeypatch.setattr(P, "build_pickup_pdf", lambda **k: b"%PDF-1.4 test",
                        raising=False)

    w.run(w.db.users.insert_many([
        {**w.chef, "active": True, "email": f"chef{s}@t.invalid", "created_at": _jetzt()},
        {**w.fremd_chef, "active": True, "email": f"cx{s}@t.invalid", "created_at": _jetzt()}]))
    w.run(w.db.dealers.insert_many([
        {"id": w.dealer_id, "user_id": w.chef["id"], "company_name": "Firma R30",
         "created_at": _jetzt()},
        {"id": w.fremd_id, "user_id": w.fremd_chef["id"], "company_name": "Fremd R30",
         "created_at": _jetzt()}]))
    w.run(w.db.driver_accounts.insert_one(
        {**w.driver, "active": True, "created_at": _jetzt()}))
    w.run(w.db.dealer_drivers.insert_one(
        {"id": str(uuid.uuid4()), "dealer_id": w.dealer_id,
         "driver_account_id": w.driver["id"], "display_name": w.driver["display_name"],
         "added_at": _jetzt()}))
    w.run(w.db.vehicles.insert_one(
        {"id": w.vid, "dealer_id": w.dealer_id, "lifecycle": "abholung_geplant",
         "owner_user_id": w.chef["id"], "created_at": _jetzt()}))
    w.run(w.db.generated_pdfs.insert_one(
        {"id": w.cid, "dealer_id": w.dealer_id, "user_id": w.chef["id"],
         "vehicle_id": w.vid, "contract_no": "KV-R30",
         "contract_data": {"seller_name": "Verkäufer V", "purchase_price": 18500},
         "created_at": _jetzt()}))
    w.run(w.db.kaufvorgaenge.insert_one(
        {"id": w.kid, "dealer_id": w.dealer_id, "user_id": w.chef["id"],
         "vehicle_id": w.vid, "contract_id": w.cid, "status": "abholung_geplant",
         "purchase_price": 18500, "appointment_id": w.aid,
         "created_at": _jetzt(), "updated_at": _jetzt()}))
    w.run(w.db.appointments.insert_one(
        {"id": w.aid, "dealer_id": w.dealer_id, "driver_id": w.driver["id"],
         "vehicle_id": w.vid, "contract_id": w.cid, "kaufvorgang_id": w.kid,
         "status": "offen", "pickup_date": "2099-01-01", "pickup_time": "10:00",
         "seller_name": "Verkäufer V", "pickup_address": "Abholweg 9",
         "created_at": _jetzt()}))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _vollstaendig(w, P, **extra):
    """Ein vollstaendig ausgefuelltes Protokoll (wie nach der Fahrer-App)."""
    doc = {
        "id": f"p_r30_{w.s}", "appointment_id": w.aid, "dealer_id": w.dealer_id,
        "vehicle_id": w.vid, "driver_account_id": w.driver["id"],
        "driver_name": w.driver["display_name"], "version": 1,
        "status": "entwurf", "superseded": False,
        "vehicle_check": {k: {"status": "stimmt"} for k, _l, _o in P.VEHICLE_CHECK_FIELDS},
        "condition": {"mileage": "75200"}, "keys_count": "2",
        "damages_confirmed": True, "place": "Warschau",
        "created_at": _jetzt(), "updated_at": _jetzt(),
    }
    doc.update(extra)
    return doc


def _fin(P, preis=None):
    """Unterschreiben. `preis` = der Preis, den die App angezeigt hat —
    der Server vergleicht ihn mit dem freigegebenen Stand (Gegenpruefung
    12.09.2026)."""
    return P.FinalizeIn(signature_driver_b64=_PNG_B64,
                        signature_seller_b64=_PNG_B64,
                        seller_name="Verkäufer V", place="Warschau",
                        neuer_preis_gesehen=preis)


# ------------------------------------------------------- Der ganze Ablauf
def test_01_fahrer_schickt_ab_chef_gibt_mit_neuem_preis_frei(welt):
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        await w.db.pickup_protocols.insert_one(_vollstaendig(w, P, **{
            "vehicle_check": {
                **{k: {"status": "stimmt"} for k, _l, _o in P.VEHICLE_CHECK_FIELDS},
                "mileage_contract": {"status": "weicht ab", "value": "75.200 km"}},
            "new_damages": [{"view": "front", "zone": "hood", "x": 0.5, "y": 0.5,
                             "type": "SS", "zone_label": "Motorhaube"}],
            "notes": "Steinschlag Frontscheibe"}))

        # 1. Ohne Abschicken gibt es keine Unterschriften
        with pytest.raises(HTTPException) as ohne:
            await P.finalize_protocol(w.aid, _fin(P), w.driver)

        # 2. Fahrer schickt ab
        ab = await P.submit_protocol(w.aid, w.driver)

        # 3. Waehrend es beim Chef liegt: keine Unterschriften
        with pytest.raises(HTTPException) as wartet:
            await P.finalize_protocol(w.aid, _fin(P), w.driver)

        # 4. Der Chef sieht es mit Abweichungen und neuen Schaeden
        liste = await P.protokolle_zur_freigabe(w.chef)

        # 5. Er gibt mit neuem Preis frei
        frei = await P.protokoll_freigeben(
            liste[0]["protocol_id"],
            P.FreigabeIn(neuer_preis=17250, notiz="Steinschlag, 1.250 € Abzug"),
            w.chef)

        # 6. Jetzt wird unterschrieben
        fertig = await P.finalize_protocol(w.aid, _fin(P, 17250), w.driver)
        proto = await w.db.pickup_protocols.find_one({"id": liste[0]["protocol_id"]},
                                                     {"_id": 0})
        kv = await w.db.kaufvorgaenge.find_one({"id": w.kid}, {"_id": 0})
        appt = await w.db.appointments.find_one({"id": w.aid}, {"_id": 0})
        return ohne.value, ab, wartet.value, liste, frei, fertig, proto, kv, appt

    ohne, ab, wartet, liste, frei, fertig, proto, kv, appt = w.run(lauf())
    assert ohne.status_code == 409 and "Freigabe" in ohne.detail
    assert ab["status"] == "zur_freigabe"
    assert wartet.status_code == 409 and "Freigabe" in wartet.detail

    assert len(liste) == 1, liste
    e = liste[0]
    assert e["preis_vertrag"] == 18500
    assert e["kilometerstand"] == "75200"
    assert [a["feld"] for a in e["abweichungen"]] == ["KM-Stand laut Vertrag"]
    assert e["abweichungen"][0]["wert"] == "75.200 km"
    assert len(e["neue_schaeden"]) == 1
    assert e["bemerkungen"] == "Steinschlag Frontscheibe"

    assert frei["status"] == "freigegeben" and frei["neuer_preis"] == 17250
    assert fertig["ok"] is True
    assert proto["status"] == "final" and proto["neuer_preis"] == 17250
    assert proto["preis_notiz"].startswith("Steinschlag")
    # Der nachverhandelte Preis ist der, den die Firma wirklich zahlt.
    assert kv["purchase_price"] == 17250, kv
    assert kv["preis_nachverhandelt"] is True and kv["preis_vorher"] == 18500
    assert appt["status"] == "abgeholt"


def test_02_ohne_verhandlung_bleibt_der_vertragspreis(welt):
    """Der Chef kann auch einfach freigeben — dann aendert sich nichts."""
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        await w.db.pickup_protocols.insert_one(_vollstaendig(w, P))
        await P.submit_protocol(w.aid, w.driver)
        liste = await P.protokolle_zur_freigabe(w.chef)
        await P.protokoll_freigeben(liste[0]["protocol_id"], P.FreigabeIn(), w.chef)
        await P.finalize_protocol(w.aid, _fin(P), w.driver)
        proto = await w.db.pickup_protocols.find_one({"id": liste[0]["protocol_id"]},
                                                     {"_id": 0})
        kv = await w.db.kaufvorgaenge.find_one({"id": w.kid}, {"_id": 0})
        return proto, kv

    proto, kv = w.run(lauf())
    assert proto["status"] == "final" and proto.get("neuer_preis") is None
    assert kv["purchase_price"] == 18500, "ohne Verhandlung bleibt der Vertragspreis"
    assert "preis_nachverhandelt" not in kv


def test_03_chef_schickt_zurueck_fahrer_kann_wieder_aendern(welt):
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        await w.db.pickup_protocols.insert_one(_vollstaendig(w, P))
        await P.submit_protocol(w.aid, w.driver)
        liste = await P.protokolle_zur_freigabe(w.chef)
        zurueck = await P.protokoll_freigeben(
            liste[0]["protocol_id"],
            P.FreigabeIn(zurueck=True, notiz="Bitte Reifenprofil nachtragen"),
            w.chef)
        # Der Fahrer darf jetzt wieder speichern
        gespeichert = await P.save_protocol(
            w.aid, P.ProtocolIn(condition={"mileage": "75200",
                                           "tire_profile": "5/5/4/4"}),
            w.driver)
        doc = await w.db.pickup_protocols.find_one({"id": liste[0]["protocol_id"]},
                                                   {"_id": 0})
        return zurueck, gespeichert, doc

    zurueck, gespeichert, doc = w.run(lauf())
    assert zurueck["status"] == "entwurf"
    assert gespeichert["status"] == "entwurf"
    assert doc["condition"]["tire_profile"] == "5/5/4/4"
    assert doc["rueckfrage"] == "Bitte Reifenprofil nachtragen"


def test_04_abgeschicktes_protokoll_ist_fuer_den_fahrer_gesperrt(welt):
    """Der Chef muss genau das sehen, was am Ende unterschrieben wird."""
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        await w.db.pickup_protocols.insert_one(_vollstaendig(w, P))
        await P.submit_protocol(w.aid, w.driver)
        with pytest.raises(HTTPException) as e:
            await P.save_protocol(w.aid, P.ProtocolIn(notes="heimlich geaendert"),
                                  w.driver)
        doc = await w.db.pickup_protocols.find_one({"appointment_id": w.aid},
                                                   {"_id": 0})
        return e.value, doc

    fehler, doc = w.run(lauf())
    assert fehler.status_code == 409 and "Freigabe" in fehler.detail
    assert doc.get("notes") != "heimlich geaendert"


def test_05_unvollstaendiges_protokoll_geht_nicht_zum_chef(welt):
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        doc = _vollstaendig(w, P)
        doc["condition"] = {}          # Kilometerstand fehlt
        await w.db.pickup_protocols.insert_one(doc)
        with pytest.raises(HTTPException) as e:
            await P.submit_protocol(w.aid, w.driver)
        return e.value

    fehler = w.run(lauf())
    assert fehler.status_code == 422 and "Kilometerstand" in fehler.detail


def test_06_abschicken_ist_wiederholbar(welt):
    """Doppeltipp oder Netzabbruch darf keine Fehlermeldung erzeugen."""
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        await w.db.pickup_protocols.insert_one(_vollstaendig(w, P))
        a = await P.submit_protocol(w.aid, w.driver)
        b = await P.submit_protocol(w.aid, w.driver)
        return a, b

    a, b = w.run(lauf())
    assert a["status"] == "zur_freigabe" and b["status"] == "zur_freigabe"
    assert b.get("bereits") is True


def test_07_fremde_firma_kann_nicht_freigeben(welt):
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        await w.db.pickup_protocols.insert_one(_vollstaendig(w, P))
        await P.submit_protocol(w.aid, w.driver)
        liste_fremd = await P.protokolle_zur_freigabe(w.fremd_chef)
        with pytest.raises(HTTPException) as e:
            await P.protokoll_freigeben(f"p_r30_{w.s}",
                                        P.FreigabeIn(neuer_preis=1),
                                        w.fremd_chef)
        return liste_fremd, e.value

    liste_fremd, fehler = w.run(lauf())
    assert liste_fremd == [], "fremde Firma sieht nichts"
    assert fehler.status_code == 404


def test_08_preis_steht_in_beiden_pdf_fassungen():
    """Quellpruefung: Der Preisblock gehoert in das LEERE Papierformular und
    in das ausgefuellte PDF — beides baut dieselbe Funktion."""
    quelle = (Path(__file__).resolve().parents[1] / "pickup_pdf_service.py"
              ).read_text(encoding="utf-8")
    block = quelle[quelle.index("_preis_vertrag = contract.get"):]
    block = block[:block.index("doc.build(")]
    assert "Kaufpreis laut Vertrag" in block
    assert "Neuer Preis (nach Verhandlung vor Ort)" in block
    # Ohne Wert eine Linie zum Eintragen (Papierformular).
    assert "_______________ €" in block
    assert 'filled.get("neuer_preis")' in block
    # Der Unterschriftssatz nennt den Preis mit.
    assert "sowie den oben genannten Kaufpreis" in block


# =============================================== Gegenpruefung 12.09.2026
def test_09_korrektur_zur_freigabe_wird_beim_schliessen_verworfen(welt):
    """Befund: korrektur_verwerfen suchte nur nach 'entwurf'. Lag die
    Korrektur beim Chef, blieb der Termin nach dem Schliessen OHNE
    massgebliches Protokoll — das unterschriebene PDF der Vorversion war
    nicht mehr erreichbar."""
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        # v1 final (abgeloest), v2 als Korrektur beim Chef
        await w.db.pickup_protocols.insert_many([
            _vollstaendig(w, P, id=f"p1_{w.s}", status="final", superseded=True,
                          pdf_path="protocol/x/v1.pdf"),
            _vollstaendig(w, P, id=f"p2_{w.s}", version=2, corrects_version=1,
                          status=P.ZUR_FREIGABE)])
        verworfen = await P.korrektur_verwerfen(w.aid)
        v1 = await w.db.pickup_protocols.find_one({"id": f"p1_{w.s}"}, {"_id": 0})
        v2 = await w.db.pickup_protocols.find_one({"id": f"p2_{w.s}"}, {"_id": 0})
        return verworfen, v1, v2

    verworfen, v1, v2 = w.run(lauf())
    assert verworfen is True, "die Korrektur beim Chef muss verworfen werden"
    assert v2["superseded"] is True and v2["verworfen_am"]
    assert v1["superseded"] is False, "die unterschriebene Version gilt wieder"


def test_10_zurueckgezogene_freigabe_verhindert_den_abschluss(welt):
    """Befund: Zog der Chef die Freigabe im selben Moment zurueck, lief der
    Abschluss trotzdem durch — mit dem alten Preis."""
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        await w.db.pickup_protocols.insert_one(_vollstaendig(w, P))
        await P.submit_protocol(w.aid, w.driver)
        liste = await P.protokolle_zur_freigabe(w.chef)
        pid = liste[0]["protocol_id"]
        await P.protokoll_freigeben(pid, P.FreigabeIn(neuer_preis=17250), w.chef)
        # Der Chef zieht zurueck, waehrend der Fahrer unterschreibt
        await P.protokoll_freigeben(
            pid, P.FreigabeIn(zurueck=True, notiz="STOPP, neu verhandeln"), w.chef)
        with pytest.raises(HTTPException) as e:
            await P.finalize_protocol(
                w.aid, P.FinalizeIn(signature_driver_b64=_PNG_B64,
                                    signature_seller_b64=_PNG_B64,
                                    seller_name="V", place="Warschau",
                                    neuer_preis_gesehen=17250), w.driver)
        doc = await w.db.pickup_protocols.find_one({"id": pid}, {"_id": 0})
        kv = await w.db.kaufvorgaenge.find_one({"id": w.kid}, {"_id": 0})
        return e.value, doc, kv

    fehler, doc, kv = w.run(lauf())
    assert fehler.status_code == 409
    assert doc["status"] == "entwurf", doc["status"]
    assert kv["purchase_price"] == 18500, "kein Preis darf gesetzt worden sein"


def test_11_geaenderter_preis_stoppt_den_abschluss(welt):
    """Befund (schwer): Der Chef konnte den Preis aendern, waehrend vor Ort
    unterschrieben wurde — der Verkaeufer sah 17.250 und unterschrieb
    16.500."""
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        await w.db.pickup_protocols.insert_one(_vollstaendig(w, P))
        await P.submit_protocol(w.aid, w.driver)
        liste = await P.protokolle_zur_freigabe(w.chef)
        pid = liste[0]["protocol_id"]
        await P.protokoll_freigeben(pid, P.FreigabeIn(neuer_preis=17250), w.chef)
        # Der Fahrer hat 17250 gesehen; der Chef aendert auf 16500.
        await P.protokoll_freigeben(pid, P.FreigabeIn(neuer_preis=16500), w.chef)
        with pytest.raises(HTTPException) as e:
            await P.finalize_protocol(
                w.aid, P.FinalizeIn(signature_driver_b64=_PNG_B64,
                                    signature_seller_b64=_PNG_B64,
                                    seller_name="V", place="Warschau",
                                    neuer_preis_gesehen=17250), w.driver)
        # Mit dem AKTUELLEN Preis geht es durch.
        ok = await P.finalize_protocol(
            w.aid, P.FinalizeIn(signature_driver_b64=_PNG_B64,
                                signature_seller_b64=_PNG_B64,
                                seller_name="V", place="Warschau",
                                neuer_preis_gesehen=16500), w.driver)
        kv = await w.db.kaufvorgaenge.find_one({"id": w.kid}, {"_id": 0})
        return e.value, ok, kv

    fehler, ok, kv = w.run(lauf())
    assert fehler.status_code == 409 and "Preis" in fehler.detail
    assert ok["ok"] is True
    assert kv["purchase_price"] == 16500


def test_12_freigabe_liste_nennt_das_fahrzeug(welt):
    """Befund: Marke/Modell stehen im Unterdokument 'data' — die flache
    Projektion lieferte immer einen leeren Namen."""
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        await w.db.vehicles.update_one(
            {"id": w.vid},
            {"$set": {"data": {"make_label": "Chevrolet", "model_label": "Camaro"}}})
        await w.db.pickup_protocols.insert_one(_vollstaendig(w, P))
        await P.submit_protocol(w.aid, w.driver)
        return await P.protokolle_zur_freigabe(w.chef)

    liste = w.run(lauf())
    assert liste[0]["fahrzeug"] == "Chevrolet Camaro", liste[0]["fahrzeug"]


def test_13_vermerk_mit_spitzer_klammer_bricht_das_pdf_nicht(welt):
    """Befund: Der Vermerk ging ungeschuetzt in den PDF-Absatz — 'Bremsen
    <b> vorn' liess den Abschluss vor Ort dauerhaft scheitern."""
    w = welt
    P = _modul("routes.protocols")

    async def lauf():
        await w.db.pickup_protocols.insert_one(_vollstaendig(w, P))
        await P.submit_protocol(w.aid, w.driver)
        liste = await P.protokolle_zur_freigabe(w.chef)
        await P.protokoll_freigeben(
            liste[0]["protocol_id"],
            P.FreigabeIn(neuer_preis=16000, notiz="Bremsen <b> vorn & Rost <u 500"),
            w.chef)
        return await P.finalize_protocol(
            w.aid, P.FinalizeIn(signature_driver_b64=_PNG_B64,
                                signature_seller_b64=_PNG_B64,
                                seller_name="V", place="Warschau",
                                neuer_preis_gesehen=16000), w.driver)

    r = w.run(lauf())
    assert r["ok"] is True, "der Abschluss darf an einem Vermerk nicht scheitern"
