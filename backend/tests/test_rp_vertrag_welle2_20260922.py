# -*- coding: utf-8 -*-
"""Rollenpruefung 22.09.2026, Welle 2 — Uebergaben an das Team "vertrag".

Ohne Server: reine Funktionen und In-Prozess-Aufrufe gegen eine Wegwerf-
Datenbank (autoschnell_rpv2_<uuid>).

  RP-488      "keine HU" vor Ort leert das alte HU-Datum im neuen Vertrag
  RP-479/480  Protokoll-Korrektur OHNE Werte (protocols -> contracts) nimmt
              die Aenderungen der vorigen Version zurueck
  RP-401      Auto-Datensatz traegt das Vertragsdatum als Kaufdatum
  RP-443      404 ohne Vergleich nennt den aussortierten Pool-Fall
  RP-144      Fassungsliste: bei Kuerzung die NEUESTEN, X-Truncated
  RP-407      Striche (Pd) im Dateinamen werden '-'
  RP-390      Fahrer-Beweis: erste Fahrt, die die Sichtfrist besteht
  RP-440/444  Pseudonym/Ansprechpartner im Beweisdokument maskiert
"""
import asyncio
import base64
import io
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException, Response

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"

FIRMA = {"company_name": "Autohaus RPV2 GmbH", "address": "Hauptstr. 1",
         "zip_code": "30159", "city": "Hannover", "phone": "0511 1",
         "email": "info@rpv2.test", "kunden_nr": "10078"}


def _jetzt(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


def _modul(name):
    import importlib
    return importlib.import_module(name)


def _pdf_text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return " ".join(" ".join((p.extract_text() or "")
                             for p in PdfReader(io.BytesIO(pdf)).pages).split())


# =================================================================== ohne DB
def test_rp407_striche_im_dateinamen_werden_bindestrich():
    from vertrag_dateiname import ascii_dateiname, content_disposition
    assert ascii_dateiname("Kaufvertrag_Mercedes–Benz_C‑Klasse.pdf") == \
        "Kaufvertrag_Mercedes-Benz_C-Klasse.pdf"
    kopf = content_disposition("Kaufvertrag_Škoda_Octavia—RS.pdf")
    kopf.encode("latin-1")
    assert 'filename="Kaufvertrag_Skoda_Octavia-RS.pdf"' in kopf
    assert "filename*=UTF-8''" in kopf
    # bisherige Regeln bleiben
    assert ascii_dateiname("Müller Straße.pdf") == "Mueller_Strasse.pdf"
    assert ascii_dateiname("") == "document.pdf"
    # Rollenprüfung 22.09.2026 (RP-200/RP-351, Uebergabe Termine): der Weg
    # ueber contracts.py (ohne eigenes Vorab-Ersetzen wie in appointments)
    # behaelt den Katalog-Bindestrich U+2011 — "CX‑6e" wird "CX-6e", nicht
    # "CX6e"; filename*= traegt weiter das Original.
    from routes.contracts import _vertrag_dateiname
    name = _vertrag_dateiname({"make_label": "Mazda", "model_label": "CX‑6e"},
                              datetime(2026, 9, 22))
    assert name == "Kaufvertrag_Mazda_CX‑6e_20260922.pdf"
    kopf = content_disposition(name, fallback="kaufvertrag.pdf")
    kopf.encode("latin-1")
    assert 'filename="Kaufvertrag_Mazda_CX-6e_20260922.pdf"' in kopf, kopf
    assert "filename*=UTF-8''Kaufvertrag_Mazda_CX%E2%80%916e_20260922.pdf" in kopf, kopf


def test_rp440_rp444_beweis_maskiert_pseudonym_und_ansprechpartner():
    import beweis_service as BS
    from beweis_pdf import KONTAKT_ENTFERNT, beweis_pdf, weitere_angaben
    privat = {"title": "VW Golf", "make_label": "VW", "seller_type": "privat",
              "seller_name": None, "seller_alias": "vnightx", "seller_zip": "30159"}
    eingefroren = BS.quelle_einfrieren("kleinanzeigen", privat)
    assert eingefroren["seller_alias"] == KONTAKT_ENTFERNT
    assert eingefroren.get("_kontakt_maskiert") is True
    # nie ungefiltert unter "Weitere ausgelesene Angaben"
    assert all(k not in ("seller_alias", "seller_ansprechpartner")
               for k, _ in weitere_angaben(privat))
    pdf = beweis_pdf(quelle="kleinanzeigen", daten=privat, url="https://example.test/x",
                     item_id="1", beweis_id="abc", abgerufen_am=None,
                     erstellt_am=datetime.now(timezone.utc), fotos=[], foto_urls=[])
    text = _pdf_text(pdf)
    assert "vnightx" not in text
    assert "privater Anbieter werden nicht" in text
    # Haendler: Firma als Name, Ansprechpartner eigene Zeile, nichts maskiert
    haendler = {"title": "BMW 320d", "make_label": "BMW", "seller_type": "haendler",
                "seller_name": "Autohaus Nord GmbH", "seller_ansprechpartner": "Herr Meier"}
    assert BS.quelle_einfrieren("autoscout", haendler)["seller_ansprechpartner"] == "Herr Meier"
    text = _pdf_text(beweis_pdf(quelle="autoscout", daten=haendler,
                                url="https://example.test/y", item_id="2", beweis_id="def",
                                abgerufen_am=None, erstellt_am=datetime.now(timezone.utc),
                                fotos=[], foto_urls=[]))
    assert "Autohaus Nord GmbH" in text and "Ansprechpartner Herr Meier" in text


# ============================================================== Wegwerf-DB
@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    namen = ["deps", "routes.contracts", "kaufvorgang", "lifecycle", "auto_daten",
             "routes.appointments", "routes.beweise", "routes.drivers", "routes.protocols"]
    mods = [_modul(n) for n in namen]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_rpv2_{s}"
    w.chef = {"id": f"chef_rpv2_{s}", "dealer_id": w.dealer_id, "role": "dealer", "active": True}
    w.a = {"id": f"sa_rpv2_{s}", "dealer_id": w.dealer_id, "role": "sucher", "active": True,
           "email": f"a_{s}@rpv2.test"}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_rpv2_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            monkeypatch.setattr(m, "db", w.db)
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.run(w.db.users.insert_many([{**u, "created_at": _jetzt()} for u in (w.chef, w.a)]))
    w.run(w.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"], **FIRMA,
                                   "created_at": _jetzt()}))
    C = _modul("routes.contracts")
    monkeypatch.setattr(C, "generate_contract_pdf",
                        lambda *, dealer, vehicle, contract, digital=False: b"%PDF-1.4 rpv2")
    monkeypatch.setattr(C, "_pdfs_erzeugen",
                        lambda *, dealer, vehicle, contract: (b"%PDF-druck", b"%PDF-digital"))
    AD = _modul("auto_daten")
    w.angelegt = []

    async def _anlegen(db, contract_dict, vehicle, gekauft_am=None):
        w.angelegt.append(gekauft_am)
        return f"ad_rpv2_{uuid.uuid4().hex[:8]}"

    async def _nichts(*a, **k):
        return None

    async def _wahr(*a, **k):
        return True
    monkeypatch.setattr(AD, "anlegen", _anlegen)
    monkeypatch.setattr(AD, "zurueckrollen", _nichts)
    monkeypatch.setattr(AD, "aktualisieren", _wahr)
    monkeypatch.setattr(AD, "nachfuehren", _nichts)
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        w.client.close()
        w.loop.close()


def _fahrzeug(w, vid, besitzer, **extra):
    doc = {"id": vid, "dealer_id": w.dealer_id, "owner_user_id": besitzer["id"],
           "lifecycle": "verglichen", "status": "verglichen",
           "data": {"make_label": "BMW", "model_label": "320d"},
           "created_at": _jetzt(), "updated_at": _jetzt()}
    doc.update(extra)
    return doc


def _vertrag(w, cid, user, **extra):
    doc = {"id": cid, "dealer_id": w.dealer_id, "user_id": user["id"], "vehicle_id": f"v_{cid}",
           "contract_no": f"KV-{cid}", "make": "BMW", "model": "320d", "status": "erstellt",
           "seller_name": "Max Kunde", "purchase_price": 10000, "version": 1,
           "pdf_b64": base64.b64encode(b"%PDF-1.4 d").decode(),
           "pdf_digital_b64": base64.b64encode(b"%PDF-1.4 dig").decode(),
           "filename": "Kaufvertrag.pdf", "send_status": [],
           "contract_data": {"seller_name": "Max Kunde", "purchase_price": 10000,
                             "vehicle_make": "BMW", "vehicle_model": "320d",
                             "hu_valid": "Ja", "hu_until": "03/2027",
                             "additional_terms": "• Standard.", "damages": [],
                             "pickup_date": "2026-10-03", "digital_vertragstext": "AVB"},
           "created_at": _jetzt(), "updated_at": _jetzt()}
    doc.update(extra)
    return doc


async def _erwarte(status, coro):
    with pytest.raises(HTTPException) as e:
        await coro
    assert e.value.status_code == status, (e.value.status_code, e.value.detail)
    return e.value


def test_rp488_keine_hu_leert_das_alte_datum(welt):
    C = _modul("routes.contracts")
    w = welt
    cid, vid = f"chu_{w.s}", f"vhu_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.a)))
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a, vehicle_id=vid)))
    # Nur ein leeres Datum OHNE "Nein" aendert nichts (leer = keine Angabe)
    erg = {}
    assert w.run(C.regenerate_contract_for_pickup(
        contract_id=cid, dealer_id=w.dealer_id, user=w.chef, grund="abholung_abgeschlossen",
        korrekturen={"hu_until": ""}, protokoll_id="p0", ergebnis=erg)) is False
    assert erg["grund"] == "kein_anlass"
    # "keine HU" vor Ort: hu_valid Nein UND das alte Datum ist weg
    erg = {}
    assert w.run(C.regenerate_contract_for_pickup(
        contract_id=cid, dealer_id=w.dealer_id, user=w.chef, grund="abholung_abgeschlossen",
        korrekturen={"hu_until": "", "hu_valid": "Nein"}, protokoll_id="p1",
        ergebnis=erg)) is True
    doc = w.run(w.db.generated_pdfs.find_one({"id": cid}))
    cd = doc["contract_data"]
    assert cd["hu_valid"] == "Nein" and cd["hu_until"] == ""
    assert set(doc["nach_abholung_aenderungen"]["felder"]) == {"hu_until", "hu_valid"}


def test_rp479_rp480_korrektur_ohne_werte_ueber_protocols(welt):
    """Beide Seiten zusammen: protocols.vertrag_nach_abholung_aktualisieren
    ruft die Neuerzeugung auch fuer eine Korrektur-Version ohne Werte, und
    contracts baut vom Stand VOR der Abholung neu auf."""
    P = _modul("routes.protocols")
    w = welt
    cid, vid = f"ckp_{w.s}", f"vkp_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.a)))
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a, vehicle_id=vid)))
    appt = {"id": f"t_{w.s}", "dealer_id": w.dealer_id, "contract_id": cid,
            "created_by": w.chef["id"]}
    # Version 1 des Protokolls: 9.000 und ein Vermerk
    assert w.run(P.vertrag_nach_abholung_aktualisieren(
        appt, "p1", 9000.0, "Winterreifen im Kofferraum")) is True
    doc = w.run(w.db.generated_pdfs.find_one({"id": cid}))
    assert doc["purchase_price"] == 9000.0 and doc["nach_abholung_protokoll_id"] == "p1"
    # Version 2 (Korrektur): Preis auf den Vertragspreis zurueckgesetzt, kein Vermerk
    assert w.run(P.vertrag_nach_abholung_aktualisieren(appt, "p2", None, "")) is True
    doc = w.run(w.db.generated_pdfs.find_one({"id": cid}))
    cd = doc["contract_data"]
    assert doc["purchase_price"] == 10000.0 and cd["purchase_price"] == 10000
    assert "Winterreifen" not in cd["additional_terms"]
    assert doc["nach_abholung_protokoll_id"] == "p2"
    assert doc["nach_abholung_aenderungen"]["nach_protokoll_korrektur"] is True
    # Wiederholung derselben Version: nichts zu tun, kein Alarm
    assert w.run(P.vertrag_nach_abholung_aktualisieren(appt, "p2", None, "")) is True
    assert w.run(w.db.betriebsalarme.count_documents({})) == 0


def test_rp401_auto_datensatz_mit_vertragsdatum(welt):
    C = _modul("routes.contracts")
    w = welt
    vid = f"vad_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.a)))
    out = w.run(C.create_contract(C.ContractIn(vehicle_id=vid, seller_name="Verkäufer V",
                                               purchase_price=10000), w.a))
    doc = w.run(w.db.generated_pdfs.find_one({"id": out["id"]}))
    assert w.angelegt == [doc["created_at"]], "Kaufdatum = Erstellung des Vertrags"


def test_rp443_404_nennt_den_aussortierten_fall(welt):
    C = _modul("routes.contracts")
    w = welt
    e = w.run(_erwarte(404, C.create_contract(C.ContractIn(
        vehicle_id=f"weg_{w.s}", seller_name="V", purchase_price=1000), w.a)))
    assert "vergleichen" in e.detail and "aussortiert" in e.detail
    assert "kostet nichts" in e.detail


def test_rp144_fassungsliste_zeigt_bei_kuerzung_die_neuesten(welt):
    C = _modul("routes.contracts")
    w = welt
    cid = f"cfl_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.chef, version=1003)))
    w.run(w.db.generated_pdf_versions.insert_many([
        {"id": f"a{i}_{w.s}", "contract_id": cid, "dealer_id": w.dealer_id, "version": i,
         "archived_at": _jetzt(), "grund": "abholtermin_geaendert"}
        for i in range(1, 1003)]))
    antwort = Response()
    fassungen = w.run(C.list_contract_versions(cid, antwort, w.chef))
    assert antwort.headers["X-Truncated"] == "1"
    assert len(fassungen) == 1000
    assert fassungen[0]["version"] == 3 and fassungen[-1]["version"] == 1002, \
        "die neuesten, aufsteigend"
    # ohne Kuerzung: alles, aufsteigend, X-Truncated 0
    cid2 = f"cfk_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid2, w.chef, version=3)))
    w.run(w.db.generated_pdf_versions.insert_many([
        {"id": f"b{i}_{w.s}", "contract_id": cid2, "dealer_id": w.dealer_id, "version": i,
         "archived_at": _jetzt()} for i in (2, 1)]))
    antwort = Response()
    assert [f["version"] for f in w.run(C.list_contract_versions(cid2, antwort, w.chef))] == [1, 2]
    assert antwort.headers["X-Truncated"] == "0"


def test_rp390_fahrer_beweis_nimmt_die_fahrt_innerhalb_der_frist(welt, monkeypatch):
    B = _modul("routes.beweise")
    w = welt

    async def _antwort(doc):
        return Response(content=b"%PDF-1.4 ok", media_type="application/pdf")
    monkeypatch.setattr(B, "_pdf_antwort", _antwort)
    ck = f"mobile:{w.s}"
    bid = f"bew_{w.s}"
    w.run(w.db.inserat_beweise.insert_one({"id": bid, "cache_key": ck, "quelle": "mobile",
                                           "status": "fertig", "pdf_key": "x.pdf"}))
    fahrer = {"id": f"f_{w.s}", "display_name": "F"}
    w.run(w.db.dealer_drivers.insert_one({"id": str(uuid.uuid4()), "dealer_id": w.dealer_id,
                                          "driver_account_id": fahrer["id"],
                                          "added_at": _jetzt()}))
    w.run(w.db.vehicles.insert_one({"id": f"v_{w.s}", "dealer_id": w.dealer_id,
                                    "lifecycle": "abholung_geplant", "inserat_schluessel": ck}))
    alt = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    # Alte, laengst abgeholte Fahrt ZUERST gespeichert (find_one traf sie)
    w.run(w.db.appointments.insert_one(
        {"id": f"alt_{w.s}", "dealer_id": w.dealer_id, "vehicle_id": f"v_{w.s}",
         "driver_id": fahrer["id"], "zuteilung": "angenommen", "status": "abgeholt",
         "abgeschlossen_seit": alt, "status_changed_at": alt, "updated_at": alt}))
    assert w.run(_erwarte(404, B.driver_beweis_pdf(bid, fahrer)))
    # Neue, laufende Fahrt zum selben Inserat: jetzt 200, unabhaengig von der Reihenfolge
    w.run(w.db.appointments.insert_one(
        {"id": f"neu_{w.s}", "dealer_id": w.dealer_id, "vehicle_id": f"v_{w.s}",
         "driver_id": fahrer["id"], "zuteilung": "angenommen", "status": "offen",
         "updated_at": _jetzt(-3600)}))
    assert w.run(B.driver_beweis_pdf(bid, fahrer)).status_code == 200
    # Ohne jede Fahrt: 404
    fremd = {"id": f"fx_{w.s}", "display_name": "X"}
    w.run(_erwarte(404, B.driver_beweis_pdf(bid, fremd)))
