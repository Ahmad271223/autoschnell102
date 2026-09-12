# -*- coding: utf-8 -*-
"""Runde 25 (12.09.2026): Kaeuferdaten einfrieren + Abholzeile im Kaufvertrag.

Pruefbefund (bestaetigt): Vertraege OHNE ausdruecklich gespeicherte
Kaeuferfelder holten Firma/Anschrift spaeter erneut aus den HEUTIGEN
Einstellungen. Aenderte der Sucher danach seine Firmendaten, zeigte das
Abholprotokoll (und eine neu erzeugte Vertragsfassung) einen anderen
Auftraggeber als die urspruengliche Ausfertigung.

  * kaeufer_einfrieren schreibt die TATSAECHLICH verwendeten Werte in den
    Vertrag — auch die, die nur aus den Einstellungen stammen
  * Migration m7 traegt sie bei Altvertraegen nach (idempotent, feldweise)
  * spaetere Aenderungen der Einstellungen aendern den Auftraggeber nicht mehr
  * Wunsch Ahmad: "Wird abgeholt am ..." steht unter den Halter-/Kaeufer-
    angaben (vor "1 · Fahrzeugdaten"), nicht mehr im Kaufpreis-Kasten

In-Prozess gegen eine Wegwerf-DB (autoschnell_r25_<uuid>), kein Server.
"""
import asyncio
import io
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"

FIRMA = {"company_name": "Firma R25 GmbH", "contact_person": "Chef Firma",
         "address": "Firmenstr. 1", "zip_code": "30159", "city": "Hannover",
         "phone": "0511 111", "email": "firma@r25.test"}
SUCHER_ALT = {"company_name": "Sucher Nord", "contact_person": "Sam Sucher",
              "address": "Suchergasse 7", "zip_code": "04109", "city": "Leipzig",
              "phone": "0341 777"}
SUCHER_NEU = {"company_name": "Sucher Nord UMBENANNT", "contact_person": "Neue Person",
              "address": "Neue Strasse 1", "zip_code": "99999", "city": "Neustadt",
              "phone": "0999 000"}


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _module(name):
    import importlib
    return importlib.import_module(name)


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return " ".join("\n".join((p.extract_text() or "")
                              for p in PdfReader(io.BytesIO(pdf)).pages).split())


# ---------------------------------------------------------------- ohne DB
def test_01_einfrieren_schreibt_die_verwendeten_werte():
    from routes.contracts import KAEUFER_FELDER, kaeufer_einfrieren
    vertrag = {"seller_name": "V"}
    dealer = dict(FIRMA, whatsapp_number="0170 1")
    kaeufer_einfrieren(vertrag, dealer)
    assert vertrag["dealer_company"] == "Firma R25 GmbH"
    assert vertrag["dealer_contact"] == "Chef Firma"
    assert vertrag["dealer_address"] == "Firmenstr. 1"
    assert vertrag["dealer_zip"] == "30159" and vertrag["dealer_city"] == "Hannover"
    assert vertrag["dealer_phone"] == "0511 111"
    assert vertrag["dealer_email"] == "firma@r25.test"
    assert vertrag["dealer_whatsapp"] == "0170 1"
    assert set(KAEUFER_FELDER) >= set(k for k in vertrag if k.startswith("dealer_"))


def test_02_einfrieren_ohne_werte_setzt_nichts_und_ueberschreibt_eingetipptes():
    from routes.contracts import kaeufer_einfrieren
    vertrag = {"dealer_company": "Von Hand GmbH"}
    kaeufer_einfrieren(vertrag, {"company_name": "   ", "city": None})
    assert vertrag == {"dealer_company": "Von Hand GmbH"}, "leere Werte aendern nichts"
    # Der Dialogwert steckt nach _apply_contract_overrides bereits im dealer-Dict
    kaeufer_einfrieren(vertrag, {"company_name": "Von Hand GmbH", "city": "Bremen"})
    assert vertrag["dealer_company"] == "Von Hand GmbH" and vertrag["dealer_city"] == "Bremen"


def test_03_eingefrorene_werte_gewinnen_gegen_neue_einstellungen():
    """_apply_contract_overrides setzt die gespeicherten Kaeuferfelder ueber
    den (heutigen) Haendler-Stand — genau das macht den Vertrag stabil."""
    from routes.contracts import _apply_contract_overrides, kaeufer_einfrieren
    vertrag = {}
    kaeufer_einfrieren(vertrag, dict(FIRMA, **SUCHER_ALT))
    _, dealer = _apply_contract_overrides(contract=vertrag, vehicle={},
                                          dealer=dict(FIRMA, **SUCHER_NEU))
    assert dealer["company_name"] == "Sucher Nord"
    assert dealer["address"] == "Suchergasse 7" and dealer["city"] == "Leipzig"


def test_04_abholzeile_steht_unter_den_kaeuferangaben():
    from pdf_service import generate_contract_pdf
    c = {"seller_name": "Max Muster", "seller_address": "ul. Osiecka 1",
         "seller_zip": "00-000", "seller_city": "Warschau", "purchase_price": 45000,
         "contract_no": "KV-R25", "pickup_date": "2026-09-13", "pickup_time": "10:00",
         "payment_method": "Bar"}
    f = _text(generate_contract_pdf(dealer=dict(FIRMA), vehicle={"make_label": "BMW"},
                                    contract=c))
    i_ab = f.find("Wird abgeholt am 13.09.2026 um 10:00 Uhr, ul. Osiecka 1")
    i_eins, i_drei = f.find("1 "), f.find("3 ")
    assert i_ab > 0, f
    assert i_ab < f.index("Fahrzeugdaten"), "Abholung steht VOR den Fahrzeugdaten"
    assert i_ab < f.index("Kaufpreis"), "und nicht mehr im Kaufpreis-Kasten"
    assert "Zahlungsart: Bar" in f
    assert i_eins >= 0 and i_drei >= 0


def test_05_ohne_abholdatum_keine_zeile():
    from pdf_service import _abholzeile
    assert _abholzeile({}) == ""
    assert _abholzeile({"pickup_date": "2026-09-13"}) == "Wird abgeholt am 13.09.2026"
    assert _abholzeile({"pickup_date": "kaputt", "pickup_time": "09:00"}) == \
        "Wird abgeholt am kaputt um 09:00 Uhr"


# ---------------------------------------------------------------- Wegwerf-DB
@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    names = ["deps", "routes.contracts", "migrationen", "kaufvorgang",
             "lifecycle", "auto_daten", "routes.appointments"]
    mods = [_module(n) for n in names]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_r25_{s}"
    w.chef = {"id": f"chef_r25_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.sucher = {"id": f"su_r25_{s}", "dealer_id": w.dealer_id, "role": "sucher",
                "settings_override": dict(SUCHER_ALT)}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_r25_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.run(w.db.users.insert_many([{**u, "active": True, "created_at": _jetzt()}
                                  for u in (w.chef, w.sucher)]))
    w.run(w.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                   **FIRMA, "created_at": _jetzt()}))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _altvertrag(w, cid, user, **daten):
    return {"id": cid, "dealer_id": w.dealer_id, "user_id": user["id"],
            "contract_no": f"KV-{cid}",
            "contract_data": {"seller_name": "V", "purchase_price": 1000, **daten}}


def test_06_migration_traegt_kaeuferdaten_nach(welt):
    import migrationen
    w = welt
    cid = f"alt_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_altvertrag(w, cid, w.sucher)))
    stats = w.run(migrationen.m7_kaeuferdaten_einfrieren(w.db))
    assert stats["eingefroren"] == 1, stats
    daten = w.run(w.db.generated_pdfs.find_one({"id": cid}))["contract_data"]
    assert daten["dealer_company"] == "Sucher Nord"        # Sucher-Override
    assert daten["dealer_city"] == "Leipzig"
    assert daten["dealer_email"] == FIRMA["email"]         # Feld ohne Override: Firma
    assert daten["kaeufer_nachtraeglich_eingefroren"] is True


def test_07_migration_ist_idempotent_und_aendert_bestehendes_nicht(welt):
    import migrationen
    w = welt
    cid = f"alt2_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(
        _altvertrag(w, cid, w.sucher, dealer_company="Von Hand GmbH")))
    w.run(migrationen.m7_kaeuferdaten_einfrieren(w.db))
    zweite = w.run(migrationen.m7_kaeuferdaten_einfrieren(w.db))
    assert zweite["eingefroren"] == 0 and zweite["schon_gesetzt"] == 1, zweite
    daten = w.run(w.db.generated_pdfs.find_one({"id": cid}))["contract_data"]
    assert daten["dealer_company"] == "Von Hand GmbH", "Eingetipptes bleibt"
    assert daten["dealer_city"] == "Leipzig", "fehlende Felder wurden ergaenzt"


def test_08_auftraggeber_bleibt_nach_aenderung_der_einstellungen(welt):
    """Der Kern des Befunds: Sucher benennt seine Firma um -> Abholprotokoll
    zeigt weiterhin den eingefrorenen Stand des Vertrags."""
    from auftraggeber import auftraggeber_fuer_termin
    import migrationen
    w = welt
    cid, aid = f"c_{w.s}", f"t_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_altvertrag(w, cid, w.sucher)))
    w.run(migrationen.m7_kaeuferdaten_einfrieren(w.db))
    w.run(w.db.users.update_one({"id": w.sucher["id"]},
                                {"$set": {"settings_override": dict(SUCHER_NEU)}}))
    appt = {"id": aid, "dealer_id": w.dealer_id, "contract_id": cid,
            "created_by": w.sucher["id"], "status": "offen",
            "pickup_date": "2099-01-01", "pickup_time": "10:00"}
    d = w.run(auftraggeber_fuer_termin(appt))
    assert d["company_name"] == "Sucher Nord", "eingefroren, nicht der neue Name"
    assert d["address"] == "Suchergasse 7" and d["city"] == "Leipzig"


def test_09_ohne_migration_wuerde_der_auftraggeber_wandern(welt):
    """Gegenprobe: Ein Vertrag ohne eingefrorene Felder folgt weiterhin den
    Einstellungen — genau deshalb gibt es m7 und das Einfrieren beim Anlegen."""
    from auftraggeber import auftraggeber_fuer_termin
    w = welt
    cid = f"c9_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_altvertrag(w, cid, w.sucher)))
    w.run(w.db.users.update_one({"id": w.sucher["id"]},
                                {"$set": {"settings_override": dict(SUCHER_NEU)}}))
    d = w.run(auftraggeber_fuer_termin(
        {"id": f"t9_{w.s}", "dealer_id": w.dealer_id, "contract_id": cid,
         "created_by": w.sucher["id"], "pickup_date": "2099-01-01"}))
    assert d["company_name"] == "Sucher Nord UMBENANNT"


def test_10_anlegen_friert_die_einstellungen_ein(welt):
    """Der Anlege-Weg selbst: Der Sucher tippt KEINE Kaeuferdaten ein, sie
    stammen aus seinen Einstellungen — danach stehen sie fest im Vertrag,
    und eine spaetere Umbenennung aendert den Auftraggeber nicht mehr."""
    from auftraggeber import auftraggeber_fuer_termin
    C = _module("routes.contracts")
    w = welt
    vid = f"v_r25_{w.s}"
    w.run(w.db.vehicles.insert_one({"id": vid, "dealer_id": w.dealer_id,
                                    "owner_user_id": w.sucher["id"],
                                    "lifecycle": "verglichen", "status": "verglichen",
                                    "data": {"make_label": "BMW", "model_label": "320d"},
                                    "created_at": _jetzt()}))
    body = C.ContractIn(vehicle_id=vid, seller_name="Verkäufer V", purchase_price=1000)
    antwort = w.run(C.create_contract(body, w.sucher))
    assert antwort is not None
    doc = w.run(w.db.generated_pdfs.find_one({"vehicle_id": vid}))
    daten = doc["contract_data"]
    assert daten["dealer_company"] == "Sucher Nord", daten
    assert daten["dealer_contact"] == "Sam Sucher"
    assert daten["dealer_address"] == "Suchergasse 7"
    assert daten["dealer_zip"] == "04109" and daten["dealer_city"] == "Leipzig"
    assert daten["dealer_email"] == FIRMA["email"], "Feld ohne Override: Firma"
    assert "kaeufer_nachtraeglich_eingefroren" not in daten, "beim Anlegen keine Marke"
    # Sucher benennt sich um -> Auftraggeber im Abholprotokoll bleibt
    w.run(w.db.users.update_one({"id": w.sucher["id"]},
                                {"$set": {"settings_override": dict(SUCHER_NEU)}}))
    d = w.run(auftraggeber_fuer_termin(
        {"id": f"t10_{w.s}", "dealer_id": w.dealer_id, "contract_id": doc["id"],
         "created_by": w.sucher["id"], "pickup_date": "2099-01-01"}))
    assert d["company_name"] == "Sucher Nord" and d["city"] == "Leipzig"


def test_11_verschieben_friert_den_ersteller_ein_nicht_den_verschiebenden(welt):
    """Gegenpruefung Runde 25: Der Chef verschiebt den Termin zum Vertrag
    eines Suchers. Eingefroren wird der ERSTELLER (Sucher), nicht der Chef —
    sonst stuende im Abholprotokoll dauerhaft die Firma statt des Suchers."""
    C = _module("routes.contracts")
    w = welt
    cid, vid = f"c11_{w.s}", f"v11_{w.s}"
    w.run(w.db.vehicles.insert_one({"id": vid, "dealer_id": w.dealer_id,
                                    "owner_user_id": w.sucher["id"],
                                    "lifecycle": "abholung_geplant",
                                    "data": {"make_label": "BMW", "model_label": "320d"},
                                    "created_at": _jetzt()}))
    doc = _altvertrag(w, cid, w.sucher, pickup_date="2026-09-20", pickup_time="10:00")
    doc.update({"vehicle_id": vid, "version": 1, "pickup_date": "2026-09-20",
                "pickup_time": "10:00", "pdf_b64": "", "pdf_digital_b64": "",
                "created_at": _jetzt()})
    w.run(w.db.generated_pdfs.insert_one(doc))
    w.run(w.db.appointments.insert_one({"id": f"t11_{w.s}", "dealer_id": w.dealer_id,
                                        "contract_id": cid, "created_by": w.chef["id"],
                                        "pickup_date": "2026-09-20"}))
    ok = w.run(C.regenerate_contract_for_pickup(
        contract_id=cid, dealer_id=w.dealer_id, user=w.chef, pickup_date="2026-09-25"))
    assert ok is True
    daten = w.run(w.db.generated_pdfs.find_one({"id": cid}))["contract_data"]
    assert daten["dealer_company"] == "Sucher Nord", daten
    assert daten["dealer_city"] == "Leipzig"
    assert daten["pickup_date"] == "2026-09-25"


def test_12_migration_nutzt_den_termin_ersteller_wenn_der_vertrag_keinen_hat(welt):
    """Gegenpruefung Runde 25: Altvertrag ohne Ersteller — das Abholprotokoll
    zeigte bisher den Termin-Ersteller. Genau den friert die Migration ein,
    nicht die Firmendaten."""
    import migrationen
    w = welt
    cid = f"c12_{w.s}"
    doc = _altvertrag(w, cid, w.sucher)
    doc.pop("user_id")
    w.run(w.db.generated_pdfs.insert_one(doc))
    w.run(w.db.appointments.insert_one({"id": f"t12_{w.s}", "dealer_id": w.dealer_id,
                                        "contract_id": cid, "created_by": w.sucher["id"],
                                        "pickup_date": "2099-01-01"}))
    w.run(migrationen.m7_kaeuferdaten_einfrieren(w.db))
    daten = w.run(w.db.generated_pdfs.find_one({"id": cid}))["contract_data"]
    assert daten["dealer_company"] == "Sucher Nord", daten
    assert daten["dealer_city"] == "Leipzig"


def test_13_ohne_ersteller_und_ohne_termin_bleibt_die_firma(welt):
    import migrationen
    w = welt
    cid = f"c13_{w.s}"
    doc = _altvertrag(w, cid, w.sucher)
    doc.pop("user_id")
    w.run(w.db.generated_pdfs.insert_one(doc))
    w.run(migrationen.m7_kaeuferdaten_einfrieren(w.db))
    daten = w.run(w.db.generated_pdfs.find_one({"id": cid}))["contract_data"]
    assert daten["dealer_company"] == FIRMA["company_name"]
    assert daten["dealer_city"] == "Hannover"
