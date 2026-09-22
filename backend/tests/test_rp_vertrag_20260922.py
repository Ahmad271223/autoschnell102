# -*- coding: utf-8 -*-
"""Rollenpruefung 22.09.2026 — Team "vertrag" (Kaufvertrag, Versand, Archiv,
Beweisdokument).

Geprueft wird ohne Server: reine Funktionen und In-Prozess-Aufrufe gegen eine
Wegwerf-Datenbank (autoschnell_rpv_<uuid>).

  RP-200/351  Dateiname mit 'Š', '–', Emoji -> kein 500 (ASCII + filename*)
  RP-515      WhatsApp "+49 (0)1515 …" -> gueltige wa.me-Nummer
  RP-433      Firmenname mit Komma im Absender wird gequotet
  RP-217/368  {fahrzeug}/{marke}/{modell} im PDF gefuellt
  RP-484      {abholdatum} im Vertrag ohne Uhrzeit, in Mails mit
  RP-432      Fahrer-Korrektur: Mail/Platzhalter nennen das neue Fahrzeug
  RP-405      "HU: Nein" -> kein "gültig bis"
  RP-404      geleerte Fahrzeugfelder/Beschreibung bleiben leer
  RP-430      "Fahrzeughalter (Anzahl)" statt "Vorhalter"
  RP-429      keine falsche "gemeinsam mit dem Verkäufer"-Behauptung
  RP-494      Fassung 2 im Kopf/Fusszeile
  RP-452      Firmenlogo im Vertrag, Logo-Adresse in der Mail
  RP-486      Uebergabeklausel nicht doppelt
  RP-473      ohne gueltige Antwortadresse keine Antwort-Zusage
  RP-113      Vertrag nur fuer Fahrzeuge im eigenen Bereich
  RP-416      zweiter Vertrag desselben Kontos nur nach Rueckfrage
  RP-007      Vertragsliste seitenweise, Vorgangs-/Terminstand je Vertrag
  RP-017      keine fremden Konto-IDs fuer Sucher
  RP-216      kein Vertragsversand nach Storno
  RP-221      haengender Versand mit anderem Inhalt sperrt nicht mehr
  RP-212/424  Platzhalter in Betreff und Text serverseitig
  RP-434      gescheiterte Folge-Mail blockiert den Schluessel nicht
  RP-215      Bahn-Mail ohne Verbindung wird abgelehnt
  RP-415      Vertrag loeschen storniert offene Termine
  RP-479      Protokoll-Korrektur nimmt Aenderungen zurueck
  RP-446      Browser-Daten -> klare 409 statt "neu vergleichen"-Schleife
  RP-498      Beweis nach Verfall auf Anforderung neu
"""
import asyncio
import base64
import inspect
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

FIRMA = {"company_name": "Autohaus RPV GmbH", "address": "Hauptstr. 1",
         "zip_code": "30159", "city": "Hannover", "phone": "0511 1",
         "email": "info@rpv.test", "kunden_nr": "10077"}


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
def test_rp200_dateiname_mit_sonderzeichen_gibt_kein_500():
    """Starlette kodiert Kopfzeilen als latin-1 — 'Š', '–', U+2011 und
    Emojis liessen die PDF-Antwort mit UnicodeEncodeError scheitern."""
    from vertrag_dateiname import ascii_dateiname, content_disposition
    for name in ("Kaufvertrag_Škoda_Octavia_20260922.pdf",
                 "Kaufvertrag_Mercedes–Benz_C‑Klasse.pdf",
                 "Kaufvertrag_🚗_Golf.pdf",
                 'Kaufvertrag_"böse"\r\nX-Evil: 1.pdf'):
        kopf = content_disposition(name, fallback="kaufvertrag.pdf")
        kopf.encode("latin-1")                      # wuerde vorher scheitern
        antwort = Response(content=b"%PDF", media_type="application/pdf",
                           headers={"Content-Disposition": kopf})
        assert antwort.headers["content-disposition"] == kopf
        assert "\r" not in kopf and "\n" not in kopf
    assert ascii_dateiname("Kaufvertrag_Škoda.pdf") == "Kaufvertrag_Skoda.pdf"
    assert ascii_dateiname("Müller Straße.pdf") == "Mueller_Strasse.pdf"
    kopf = content_disposition("Kaufvertrag_Škoda.pdf")
    assert kopf.startswith('inline; filename="Kaufvertrag_Skoda.pdf"')
    assert "filename*=UTF-8''Kaufvertrag_%C5%A0koda.pdf" in kopf
    # reiner ASCII-Name: kein filename*
    assert content_disposition("Kaufvertrag_VW.pdf") == 'inline; filename="Kaufvertrag_VW.pdf"'
    assert content_disposition("", fallback="kaufvertrag.pdf") == 'inline; filename="kaufvertrag.pdf"'
    assert content_disposition("x.pdf", art="attachment").startswith("attachment;")


def test_rp200_alle_pdf_routen_nutzen_den_helfer():
    C = _modul("routes.contracts")
    for fn in (C.get_contract_pdf, C.public_vertrag_pdf, C.get_contract_version_pdf):
        q = inspect.getsource(fn)
        assert "content_disposition(" in q and "filename=\"{fname}\"" not in q, fn.__name__


def test_rp515_whatsapp_nummer_mit_null_in_klammern():
    from routes.contracts import wa_nummer
    assert wa_nummer("+49 (0)1515 1747777") == "4915151747777"
    assert wa_nummer("+49 (0) 170 1234567") == "491701234567"
    assert wa_nummer("+49 0170 1234567") == "491701234567"
    assert wa_nummer("0049 (0)170 1234567") == "491701234567"
    # bisherige Regeln bleiben
    assert wa_nummer("0170 1234567") == "491701234567"
    assert wa_nummer("+41 79 123 45 67") == "41791234567"
    assert wa_nummer("") == "" and wa_nummer(None) == ""


def test_rp433_absender_mit_komma_wird_gequotet(monkeypatch):
    import email_service as ES
    monkeypatch.setattr(ES, "MAIL_ABSENDER_NAME", "AutoSchnell", raising=False)
    monkeypatch.setattr(ES, "MAIL_FROM", "AutoSchnell <vertrag@autoschnell.de>", raising=False)
    assert ES._absender("Autohaus Muster", kodiert=False) == \
        "Autohaus Muster über AutoSchnell <vertrag@autoschnell.de>"
    mit_komma = ES._absender("Autohaus Müller, Inh. X", kodiert=False)
    assert mit_komma == '"Autohaus Müller, Inh. X über AutoSchnell" <vertrag@autoschnell.de>'
    from email.utils import getaddresses
    assert len(getaddresses([mit_komma])) == 1, "das Komma trennt keine zweite Adresse ab"
    boese = ES._absender('A\\B "C"; D\r\nBcc: x@y.de', kodiert=False)
    assert "\r" not in boese and "\n" not in boese
    assert boese.startswith('"') and boese.endswith("<vertrag@autoschnell.de>")


def test_rp217_rp484_rp432_platzhalter():
    import vertrag_platzhalter as P
    # PDF-Weg: nur die Dialogfelder vehicle_make/vehicle_model
    pdf_vertrag = {"vehicle_make": "BMW", "vehicle_model": "320d",
                   "pickup_date": "2026-10-03", "pickup_time": "14:30"}
    text = P.ersetzen("{fahrzeug} / {marke} / {modell} am {abholdatum}", pdf_vertrag,
                      FIRMA, mit_uhrzeit=False)
    assert text == "BMW 320d / BMW / 320d am 03.10.2026", text
    # Mails behalten die Uhrzeit
    assert "um 14:30 Uhr" in P.ersetzen("{abholdatum}", pdf_vertrag, FIRMA)
    # Nach einer Fahrer-Korrektur steht der neue Wert in contract_data —
    # er gewinnt gegen das alte make/model oben am Vertrag.
    vertrag = {"make": "BMW", "model": "320d",
               "contract_data": {"vehicle_make": "Audi", "vehicle_model": "A4"}}
    assert P.marke_modell(vertrag) == ("Audi", "A4")
    assert P.marke_modell({"make": "VW", "model": "Golf"}) == ("VW", "Golf")
    import vertrag_mail as VM
    assert VM._fahrzeug_titel(vertrag) == "Audi A4"


def _pdf(contract, vehicle=None, dealer=None, digital=False):
    from pdf_service import generate_contract_pdf
    basis = {"seller_name": "Max Muster", "purchase_price": 12500, "contract_no": "KV-RPV",
             "payment_method": "Bar"}
    return _pdf_text(generate_contract_pdf(
        dealer=dict(dealer or FIRMA), vehicle=dict(vehicle or {"make_label": "BMW",
                                                               "model_label": "320d"}),
        contract={**basis, **contract}, digital=digital))


def test_rp217_rp484_pdf_setzt_fahrzeug_ein_ohne_uhrzeit():
    text = _pdf({"additional_terms": "• Übergabe des {fahrzeug} am {abholdatum}.",
                 "pickup_date": "2026-10-03", "pickup_time": "14:30",
                 "vehicle_make": "BMW", "vehicle_model": "320d"}, digital=True)
    assert "Übergabe des BMW 320d am 03.10.2026." in text, text
    assert "14:30" not in text, "die Abholuhrzeit steht nicht im Vertrag"
    assert "Übergabe des ____" not in text
    # Ohne Dialogfelder (anderer Client): Marke/Modell aus den Fahrzeugdaten
    text = _pdf({"additional_terms": "{fahrzeug}"},
                vehicle={"make_label": "Skoda", "model_description": "Octavia RS"})
    assert "Skoda Octavia RS" in text


def test_rp405_hu_nein_ohne_gueltig_bis():
    assert "gültig bis" not in _pdf({"hu_valid": "Nein", "hu_until": "05/2027"})
    assert "Ja, gültig bis 05/2027" in _pdf({"hu_valid": "Ja", "hu_until": "05/2027"})
    C = _modul("routes.contracts")
    body = C.ContractIn(vehicle_id="v", seller_name="V", purchase_price=100,
                        hu_valid="Nein", hu_until="05/2027")
    assert body.hu_until == "", "der Server verwirft das Datum bei HU Nein"
    assert C.ContractIn(vehicle_id="v", seller_name="V", purchase_price=100,
                        hu_valid="Ja", hu_until="05/2027").hu_until == "05/2027"


def test_rp404_rp430_halter_und_geleerte_felder():
    fahrzeug = {"make_label": "BMW", "model_label": "320d", "previous_owners": "3",
                "exterior_color": "Blau", "color": "Blau", "mileage": 90000}
    # Feld fehlt (None) -> Inseratswert, neue Beschriftung
    text = _pdf({"previous_owners": None}, vehicle=fahrzeug)
    assert "Fahrzeughalter (Anzahl) 3" in text and "Vorhalter" not in text, text
    # im Dialog geleert -> keine Angabe
    assert "Fahrzeughalter" not in _pdf({"previous_owners": ""}, vehicle=fahrzeug)
    # Fahrzeugfelder: "" = leer, None = Inserat
    from vertrag_felder import _apply_contract_overrides
    v, _ = _apply_contract_overrides(contract={"vehicle_mileage": "", "vehicle_color": "",
                                               "vehicle_first_registration": ""},
                                     vehicle=dict(fahrzeug, first_registration="03/2019"),
                                     dealer={})
    assert v["mileage"] == "" and v["color"] == "" and v["first_registration"] == ""
    v, _ = _apply_contract_overrides(contract={"vehicle_mileage": None}, vehicle=fahrzeug,
                                     dealer={})
    assert v["mileage"] == 90000
    C = _modul("routes.contracts")
    assert C.ContractIn(vehicle_id="v", seller_name="V", purchase_price=1).vehicle_description is None
    assert C.ContractIn(vehicle_id="v", seller_name="V", purchase_price=1).previous_owners is None


def test_rp429_keine_falschen_saetze_im_vertrag():
    text = _pdf({"damages_text": "Kratzer Tür links"},
                vehicle={"make_label": "BMW", "features": ["Navi", "Klima"]})
    assert "gemeinsam mit dem Verkäufer" not in text
    assert "interne Dokumentation" not in text
    assert "vom Händler zu prüfen" not in text
    assert "Kratzer Tür links" in text and "Navi" in text


def test_rp494_fassung_steht_im_vertrag():
    erste = _pdf({"fassung": 1})
    assert "FASSUNG" not in erste and "ersetzt Fassung" not in erste
    zweite = _pdf({"fassung": 2, "ersetzt_fassung_am": "2026-09-21"})
    assert "2 · ersetzt Fassung 1 vom 21.09.2026" in zweite, zweite[:600]
    assert "Fassung 2" in zweite                           # Fusszeile
    digital = _pdf({"fassung": 3, "ersetzt_fassung_am": "2026-09-22"}, digital=True)
    assert "ersetzt Fassung 2 vom 22.09.2026" in digital and "Fassung 3" in digital


def _png(breite=120, hoehe=40) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (breite, hoehe), (200, 20, 20)).save(buf, "PNG")
    return buf.getvalue()


def _bilder(pdf: bytes) -> int:
    from pypdf import PdfReader
    n = 0
    for seite in PdfReader(io.BytesIO(pdf)).pages:
        xo = (seite.get("/Resources") or {}).get("/XObject") or {}
        n += sum(1 for k in xo if xo[k].get("/Subtype") == "/Image")
    return n


def test_rp452_logo_im_vertrag_und_in_der_mail(monkeypatch):
    from pdf_service import generate_contract_pdf
    basis = {"seller_name": "V", "purchase_price": 1000, "contract_no": "KV-L"}
    ohne = generate_contract_pdf(dealer=dict(FIRMA), vehicle={}, contract=basis)
    mit = generate_contract_pdf(dealer=dict(FIRMA, _logo_bytes=_png()), vehicle={},
                                contract=basis)
    assert _bilder(ohne) == 0 and _bilder(mit) == 1
    # kaputtes Logo verhindert keinen Vertrag
    assert generate_contract_pdf(dealer=dict(FIRMA, _logo_bytes=b"kein bild"),
                                 vehicle={}, contract=basis)[:4] == b"%PDF"
    C = _modul("routes.contracts")
    assert C.logo_schluessel({"logo_url": "/api/files/logo/d1/abc_logo.png"}) == "logo/d1/abc_logo.png"
    assert C.logo_schluessel({"logo_url": "https://fremd.example/logo.png"}) == ""
    assert C.logo_schluessel({"logo_url": "/api/files/resale/x.png"}) == ""
    assert C.logo_schluessel({"logo_url": "/api/files/logo/../geheim"}) == ""
    import vertrag_mail as VM
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.test")
    assert VM._logo_adresse("/api/files/logo/d1/x.png") == \
        "https://app.example.test/api/files/logo/d1/x.png"
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    assert VM._logo_adresse("/api/files/logo/d1/x.png") == "", "nur https in Mails"


def test_rp486_uebergabeklausel_nicht_doppelt():
    import vertrag_vorlagen as V
    alt = ("• Die Fahrzeugübergabe findet bis/am ___ in ___ gegen ___ statt.\n"
           "• Eigene Regel: Schlüssel im Handschuhfach.")
    text = V.sondervereinbarungen({"default_special_agreements": alt})
    assert text.count(V.UEBERGABE_SATZ_ANFANG) == 1, text
    assert "___ in ___" not in text and "Eigene Regel" in text
    # Standard AUS: der eigene Text bleibt vollstaendig
    aus = V.sondervereinbarungen({"default_special_agreements": alt,
                                  "sondervereinbarung_standard_aktiv": False})
    assert aus == alt
    # woertlich doppelte Standardzeile faellt ebenfalls weg
    doppelt = V.BESONDERE_VEREINBARUNGEN.split("\n")[1] + "\n• Noch was."
    text = V.sondervereinbarungen({"default_special_agreements": doppelt})
    assert text.count("Vorlage der Kundennummer") == 1 and "Noch was" in text


def test_rp473_ohne_antwortadresse_keine_zusage():
    import vertrag_mail as VM
    vertrag = {"make": "VW", "model": "Golf", "seller_name": "S", "contract_data": {}}
    _, text, html = VM.vertrag_mail(vertrag=vertrag, firma={"company_name": "F", "email": ""},
                                    sucher={"first_name": "Max", "phone": "0511 9"},
                                    nachricht="", betreff=None)
    assert "Ihre Antwort geht direkt" not in text and "gehen direkt an" not in html
    assert "telefonisch unter 0511 9" in text
    _, text, html = VM.vertrag_mail(vertrag=vertrag, firma={"company_name": "F"},
                                    sucher={"first_name": "Max", "email": "max@f.test"},
                                    nachricht="", betreff=None)
    assert "Ihre Antwort geht direkt an Max (max@f.test)" in text
    assert "gehen direkt an" in html


def test_rp416_steuerfeld_zaehlt_nicht_zum_inhalt():
    C = _modul("routes.contracts")
    a = C.ContractIn(vehicle_id="v", seller_name="V", purchase_price=100, idempotency_key="k1234567")
    b = C.ContractIn(vehicle_id="v", seller_name="V", purchase_price=100, idempotency_key="k1234567",
                     zweiter_vertrag_bestaetigt=True)
    assert C._anfrage_hash(a) == C._anfrage_hash(b)


def test_rp415_loeschen_storniert_vorher_die_termine_quelle():
    C = _modul("routes.contracts")
    q = inspect.getsource(C.delete_contract)
    assert q.index("_offene_termine_beim_loeschen_stornieren(") < q.index(
        "await vertrag_endgueltig_loeschen("), "erst stornieren, dann loeschen"


# ============================================================== Wegwerf-DB
@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    namen = ["deps", "routes.contracts", "kaufvorgang", "lifecycle", "auto_daten",
             "routes.appointments", "routes.beweise"]
    mods = [_modul(n) for n in namen]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_rpv_{s}"
    w.chef = {"id": f"chef_rpv_{s}", "dealer_id": w.dealer_id, "role": "dealer", "active": True}
    w.a = {"id": f"sa_rpv_{s}", "dealer_id": w.dealer_id, "role": "sucher", "active": True,
           "email": f"a_{s}@rpv.test"}
    w.b = {"id": f"sb_rpv_{s}", "dealer_id": w.dealer_id, "role": "sucher", "active": True}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_rpv_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            monkeypatch.setattr(m, "db", w.db)
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.run(w.db.users.insert_many([{**u, "created_at": _jetzt()} for u in (w.chef, w.a, w.b)]))
    w.run(w.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"], **FIRMA,
                                   "created_at": _jetzt()}))
    # ReportLab und Auto-Daten sind hier nicht Gegenstand
    C = _modul("routes.contracts")
    monkeypatch.setattr(C, "generate_contract_pdf",
                        lambda *, dealer, vehicle, contract, digital=False: b"%PDF-1.4 rpv")
    AD = _modul("auto_daten")

    async def _anlegen(db, contract_dict, vehicle, gekauft_am=None):
        return f"ad_rpv_{uuid.uuid4().hex[:8]}"

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
           "data": {"make_label": "BMW", "model_label": "320d",
                    "description": "Aus dem Inserat."},
           "created_at": _jetzt(), "updated_at": _jetzt()}
    doc.update(extra)
    return doc


def _body(C, vid, **kw):
    return C.ContractIn(**{"vehicle_id": vid, "seller_name": "Verkäufer V",
                           "purchase_price": 10000, **kw})


async def _erwarte(status, coro):
    with pytest.raises(HTTPException) as e:
        await coro
    assert e.value.status_code == status, (e.value.status_code, e.value.detail)
    return e.value


def test_rp113_vertrag_nur_im_eigenen_bereich(welt):
    C = _modul("routes.contracts")
    w = welt
    vid = f"v_rp113_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.a)))
    # B hat nie verglichen: weder Vorschau noch Vertrag
    e = w.run(_erwarte(404, C.create_contract(_body(C, vid), w.b)))
    assert "vergleichen" in e.detail
    w.run(_erwarte(404, C.preview_contract(_body(C, vid), w.b)))
    assert w.run(w.db.generated_pdfs.count_documents({"vehicle_id": vid})) == 0
    # nach seinem Vergleich (Mitbearbeiter) geht es, ebenso fuer A und den Chef
    w.run(w.db.vehicles.update_one({"id": vid}, {"$addToSet": {"mitbearbeiter_ids": w.b["id"]}}))
    assert w.run(C.create_contract(_body(C, vid), w.b))["user_id"] == w.b["id"]
    assert w.run(C.create_contract(_body(C, vid), w.a))["user_id"] == w.a["id"]
    assert w.run(C.create_contract(_body(C, vid), w.chef))["user_id"] == w.chef["id"]


def test_rp416_zweiter_vertrag_desselben_kontos_nur_nach_rueckfrage(welt):
    C = _modul("routes.contracts")
    w = welt
    vid = f"v_rp416_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.a, mitbearbeiter_ids=[w.b["id"]])))
    erster = w.run(C.create_contract(_body(C, vid), w.a))
    e = w.run(_erwarte(409, C.create_contract(_body(C, vid, purchase_price=9000), w.a)))
    assert e.detail["code"] == "vertrag_vorhanden"
    assert e.detail["contract_id"] == erster["id"]
    assert e.detail["contract_no"] == erster["contract_no"] and erster["contract_no"] in e.detail["msg"]
    zweiter = w.run(C.create_contract(
        _body(C, vid, purchase_price=9000, zweiter_vertrag_bestaetigt=True), w.a))
    assert zweiter["id"] != erster["id"]
    c = w.run(w.db.generated_pdfs.find_one({"id": zweiter["id"]}))
    assert "zweiter_vertrag_bestaetigt" not in c["contract_data"], "Steuerfeld nicht im Vertrag"
    # Doppel-Abholung durch einen ANDEREN Sucher bleibt ohne Rueckfrage erlaubt
    assert w.run(C.create_contract(_body(C, vid), w.b))["user_id"] == w.b["id"]
    # Ist der erste Kauf storniert, fragt nichts mehr nach
    w.run(w.db.kaufvorgaenge.update_many({"user_id": w.a["id"]}, {"$set": {"status": "storniert"}}))
    w.run(C.create_contract(_body(C, vid), w.a))


def test_rp404_beschreibung_bleibt_geleert(welt):
    C = _modul("routes.contracts")
    w = welt
    vid = f"v_rp404_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.chef)))
    leer = w.run(C.create_contract(_body(C, vid, vehicle_description=""), w.chef))
    ohne = w.run(C.create_contract(_body(C, vid, zweiter_vertrag_bestaetigt=True), w.chef))
    daten = {c["id"]: c["contract_data"] for c in
             w.run(w.db.generated_pdfs.find({"vehicle_id": vid}).to_list(10))}
    assert daten[leer["id"]]["vehicle_description"] == ""
    assert daten[ohne["id"]]["vehicle_description"] == "Aus dem Inserat."


def _vertrag(w, cid, user, **extra):
    doc = {"id": cid, "dealer_id": w.dealer_id, "user_id": user["id"], "vehicle_id": f"v_{cid}",
           "contract_no": f"KV-{cid}", "make": "BMW", "model": "320d", "status": "erstellt",
           "seller_name": "Max Kunde", "purchase_price": 10000, "version": 1,
           "pdf_b64": base64.b64encode(b"%PDF-1.4 d").decode(),
           "pdf_digital_b64": base64.b64encode(b"%PDF-1.4 dig").decode(),
           "filename": "Kaufvertrag.pdf", "send_status": [],
           "contract_data": {"seller_name": "Max Kunde", "purchase_price": 10000,
                             "vehicle_make": "BMW", "vehicle_model": "320d",
                             "pickup_date": "2026-10-03", "pickup_time": "14:30",
                             "dealer_company": "Filiale Nord"},
           "created_at": _jetzt(), "updated_at": _jetzt()}
    doc.update(extra)
    return doc


def test_rp007_rp406_rp017_liste_seitenweise_angereichert_maskiert(welt):
    C = _modul("routes.contracts")
    w = welt
    for i in range(5):
        w.run(w.db.generated_pdfs.insert_one(_vertrag(
            w, f"c{i}_{w.s}", w.a, created_at=_jetzt(-100 + i),
            uebergeben_von=w.chef["id"],
            pickup_history=[{"von_datum": "2026-10-01", "geaendert_von": w.chef["id"]}],
            freigabe={"token": "t", "erstellt_von": w.chef["id"], "version": 1})))
    w.run(w.db.kaufvorgaenge.insert_many([
        {"id": f"kv0_{w.s}", "dealer_id": w.dealer_id, "contract_id": f"c0_{w.s}",
         "status": "nicht_abgeholt"},
        {"id": f"kv1_{w.s}", "dealer_id": w.dealer_id, "contract_id": f"c1_{w.s}",
         "status": "abholung_geplant"}]))
    w.run(w.db.appointments.insert_many([
        {"id": f"t0_{w.s}", "dealer_id": w.dealer_id, "contract_id": f"c0_{w.s}",
         "status": "nicht abgeholt"},
        {"id": f"t1_{w.s}", "dealer_id": w.dealer_id, "contract_id": f"c1_{w.s}",
         "status": "offen"}]))
    r1 = Response()
    seite1 = w.run(C.list_contracts(r1, w.a, limit=2))
    assert [c["id"] for c in seite1] == [f"c4_{w.s}", f"c3_{w.s}"]
    assert r1.headers["X-Truncated"] == "1" and r1.headers["X-Next-Before"]
    r2 = Response()
    seite2 = w.run(C.list_contracts(r2, w.a, limit=2, before=r1.headers["X-Next-Before"]))
    assert [c["id"] for c in seite2] == [f"c2_{w.s}", f"c1_{w.s}"]
    r3 = Response()
    seite3 = w.run(C.list_contracts(r3, w.a, limit=2, before=r2.headers["X-Next-Before"]))
    assert [c["id"] for c in seite3] == [f"c0_{w.s}"] and r3.headers["X-Truncated"] == "0"
    assert "X-Next-Before" not in r3.headers
    c0, c1 = seite3[0], seite2[1]
    assert c0["kaufvorgang_status"] == "nicht_abgeholt" and c0["termin_offen"] is False
    assert c1["kaufvorgang_status"] == "abholung_geplant" and c1["termin_offen"] is True
    # Sucher: keine Konto-IDs von Chef/Kollegen
    for c in seite1 + seite2 + seite3:
        assert "uebergeben_von" not in c
        assert all("geaendert_von" not in e for e in c["pickup_history"])
        assert "erstellt_von" not in c["freigabe"]
    einzeln = w.run(C.get_contract(f"c0_{w.s}", w.a))
    assert "uebergeben_von" not in einzeln and einzeln["termin_offen"] is False
    # Der Chef sieht alles
    chef = w.run(C.get_contract(f"c0_{w.s}", w.chef))
    assert chef["uebergeben_von"] == w.chef["id"]
    # Ohne limit: altes Verhalten (alle, X-Truncated 0)
    r4 = Response()
    assert len(w.run(C.list_contracts(r4, w.a))) == 5 and r4.headers["X-Truncated"] == "0"


def test_rp116_fassungen_ohne_archived_by_fuer_sucher(welt):
    C = _modul("routes.contracts")
    w = welt
    cid = f"cv_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a, version=2)))
    w.run(w.db.generated_pdf_versions.insert_one(
        {"id": f"arch_{w.s}", "contract_id": cid, "dealer_id": w.dealer_id, "version": 1,
         "archived_by": w.chef["id"], "archived_at": _jetzt(), "grund": "abholtermin_geaendert"}))
    such = w.run(C.list_contract_versions(cid, Response(), w.a))
    assert such and "archived_by" not in such[0]
    chef = w.run(C.list_contract_versions(cid, Response(), w.chef))
    assert chef[0]["archived_by"] == w.chef["id"]


def test_rp216_kein_vertragsversand_nach_storno(welt):
    C = _modul("routes.contracts")
    w = welt
    cid = f"cs_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a)))
    w.run(w.db.kaufvorgaenge.insert_one({"id": f"kvs_{w.s}", "dealer_id": w.dealer_id,
                                         "contract_id": cid, "status": "storniert"}))
    body = C.SendIn(channel="whatsapp", recipient="0170 1234567", message="Hallo",
                    idempotency_key=f"k-{w.s}")
    e = w.run(_erwarte(409, C.send_contract(cid, body, w.a)))
    assert "storniert" in e.detail
    assert w.run(w.db.generated_pdfs.find_one({"id": cid}))["send_status"] == []


def test_rp221_rp212_haengender_versand_mit_anderem_inhalt(welt, monkeypatch):
    C = _modul("routes.contracts")
    w = welt
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.test")
    cid = f"ch_{w.s}"
    alt = {"idempotency_key": "alt-schluessel", "channel": "whatsapp",
           "recipient": "0170 1234567", "sent_at": _jetzt(-3600), "zustellung": "unklar",
           "anfrage_hash": "anderer-inhalt", "version": 1}
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a, send_status=[alt])))
    body = C.SendIn(channel="whatsapp", recipient="0170 1234567",
                    message="Hallo {kunde_name}, hier der Vertrag für den {fahrzeug}.",
                    idempotency_key=f"neu-{w.s}")
    out = w.run(C.send_contract(cid, body, w.a))
    assert out["zustellung"] == "link_bereit" and out.get("frueherer_versand_unklar") is True
    # RP-212: getippte Platzhalter sind serverseitig eingesetzt
    from urllib.parse import parse_qs, urlparse
    text = parse_qs(urlparse(out["wa_url"]).query)["text"][0]
    assert "Hallo Max Kunde, hier der Vertrag für den BMW 320d." in text, text
    status = {e["idempotency_key"]: e for e in
              w.run(w.db.generated_pdfs.find_one({"id": cid}))["send_status"]}
    assert status["alt-schluessel"]["zustellung"] == "abgeloest", "Beleg bleibt, gesperrt ist nichts"
    assert status[f"neu-{w.s}"]["zustellung"] == "link_bereit"


def test_rp424_rp473_email_platzhalter_im_betreff_und_antwortadresse(welt, monkeypatch):
    C = _modul("routes.contracts")
    w = welt
    import email_service
    import provider_fetch
    monkeypatch.setattr(provider_fetch, "MOCK_PROVIDER_FETCH", False)
    monkeypatch.setattr(email_service, "email_configured", lambda: True)
    gesendet = []

    async def _mit_beleg(to, subject, text, anhang=None, anhang_name="", **kw):
        gesendet.append({"to": to, "subject": subject, "text": text, **kw})
        return True, "resend:rpv"

    async def _send(*a, **k):
        return True
    monkeypatch.setattr(email_service, "send_email_mit_beleg", _mit_beleg)
    monkeypatch.setattr(email_service, "send_email", _send)
    cid = f"ce_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.b)))       # B hat keine E-Mail
    w.run(w.db.dealers.update_one({"id": w.dealer_id}, {"$set": {"email": ""}}))
    body = C.SendIn(channel="email", recipient="kunde@rpv.test",
                    subject="Ihr Kaufvertrag {fahrzeug} ({vertragsnummer})",
                    message="Sehr geehrte/r {kunde_name}", idempotency_key=f"m-{w.s}")
    out = w.run(C.send_contract(cid, body, w.b))
    assert gesendet[0]["subject"] == f"Ihr Kaufvertrag BMW 320d (KV-{cid})", gesendet[0]["subject"]
    assert gesendet[0]["text"].startswith("Sehr geehrte/r Max Kunde")
    # RP-473: keine gueltige Antwortadresse -> Hinweis, keine Antwort-Zusage
    assert out.get("antwort_adresse_fehlt") is True
    assert "Ihre Antwort geht direkt" not in gesendet[0]["text"]


def test_rp434_rp215_folge_mail(welt, monkeypatch):
    C = _modul("routes.contracts")
    w = welt
    import email_service
    import provider_fetch
    monkeypatch.setattr(provider_fetch, "MOCK_PROVIDER_FETCH", False)
    monkeypatch.setattr(email_service, "email_configured", lambda: True)
    ergebnis = {"ok": False}
    versuche = []

    async def _mit_beleg(to, subject, text, **kw):
        versuche.append(kw.get("idempotency_key"))
        return (True, "resend:ok") if ergebnis["ok"] else (False, "")
    monkeypatch.setattr(email_service, "send_email_mit_beleg", _mit_beleg)
    cid = f"cf_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a)))
    body = C.FolgeMailIn(art="nach_kauf", recipient="kunde@rpv.test",
                         idempotency_key=f"fm-{w.s}")
    # 1. Versuch scheitert (502) -> Reservierung ist wieder weg
    w.run(_erwarte(502, C.folge_mail_senden(cid, body, w.a)))
    assert w.run(w.db.generated_pdfs.find_one({"id": cid}))["send_status"] == []
    # 2. Versuch mit DEMSELBEN Schluessel wird wirklich verschickt
    ergebnis["ok"] = True
    out = w.run(C.folge_mail_senden(cid, body, w.a))
    assert out.get("bereits_gesendet") is not True and out["zustellung"] == "versendet"
    assert len(versuche) == 2
    # Altbestand: ein "fehlgeschlagen"-Eintrag mit dem Schluessel blockiert nicht
    w.run(w.db.generated_pdfs.update_one({"id": cid}, {"$push": {"send_status": {
        "idempotency_key": f"alt-{w.s}", "art": "nach_kauf", "zustellung": "fehlgeschlagen"}}}))
    out = w.run(C.folge_mail_senden(cid, C.FolgeMailIn(
        art="nach_kauf", recipient="kunde@rpv.test", idempotency_key=f"alt-{w.s}"), w.a))
    assert out.get("bereits_gesendet") is not True
    # Ein frisch laufender Versand heisst "laeuft", nicht "verschickt"
    w.run(w.db.generated_pdfs.update_one({"id": cid}, {"$push": {"send_status": {
        "idempotency_key": f"lauf-{w.s}", "art": "nach_kauf", "zustellung": "laeuft",
        "sent_at": _jetzt()}}}))
    out = w.run(C.folge_mail_senden(cid, C.FolgeMailIn(
        art="nach_kauf", recipient="kunde@rpv.test", idempotency_key=f"lauf-{w.s}"), w.a))
    assert out["bereits_gesendet"] is True and out["zustellung"] == "laeuft"
    # RP-215: Bahn-Vorlage unveraendert -> 400; mit Verbindung -> geht raus
    e = w.run(_erwarte(400, C.folge_mail_senden(cid, C.FolgeMailIn(
        art="bahn", recipient="kunde@rpv.test"), w.a)))
    assert "Bahnverbindung" in e.detail
    out = w.run(C.folge_mail_senden(cid, C.FolgeMailIn(
        art="bahn", recipient="kunde@rpv.test",
        message="Sehr geehrte/r {kunde_name}, ICE 571, Ankunft 10:12 Uhr."), w.a))
    assert out["zustellung"] == "versendet"


def test_rp415_offene_termine_werden_storniert(welt):
    C = _modul("routes.contracts")
    w = welt
    cid = f"cd_{w.s}"
    w.run(w.db.appointments.insert_many([
        {"id": f"to_{w.s}", "dealer_id": w.dealer_id, "contract_id": cid, "status": "offen",
         "driver_id": "fahrer1", "zuteilung": "angenommen"},
        {"id": f"tz_{w.s}", "dealer_id": w.dealer_id, "contract_id": cid,
         "status": "nicht abgeholt", "abgeschlossen_seit": "2026-09-01T00:00:00+00:00"},
        {"id": f"tf_{w.s}", "dealer_id": "fremde_firma", "contract_id": cid, "status": "offen"}]))
    n = w.run(C._offene_termine_beim_loeschen_stornieren(w.chef, cid))
    assert n == 1
    t = {a["id"]: a for a in w.run(w.db.appointments.find({}).to_list(10))}
    assert t[f"to_{w.s}"]["status"] == "storniert" and t[f"to_{w.s}"]["abgeschlossen_seit"]
    assert t[f"to_{w.s}"]["storno_grund"] == "vertrag_geloescht"
    assert t[f"tz_{w.s}"]["status"] == "nicht abgeholt", "geschlossene bleiben"
    assert t[f"tf_{w.s}"]["status"] == "offen", "fremde Firma unberuehrt"
    assert w.run(w.db.activity_logs.count_documents(
        {"action": "termin.storniert.vertrag_geloescht", "ref": f"to_{w.s}"})) == 1


def test_rp479_rp432_rp494_neuerzeugung_nach_abholung(welt, monkeypatch):
    C = _modul("routes.contracts")
    w = welt
    erzeugt = []

    def _pdfs(*, dealer, vehicle, contract):
        erzeugt.append(dict(contract))
        return b"%PDF-druck", b"%PDF-digital"
    monkeypatch.setattr(C, "_pdfs_erzeugen", _pdfs)
    cid, vid = f"cr_{w.s}", f"vr_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.a)))
    w.run(w.db.generated_pdfs.insert_one(_vertrag(
        w, cid, w.a, vehicle_id=vid, created_at="2026-09-20T10:00:00+00:00",
        contract_data={"seller_name": "Max Kunde", "purchase_price": 10000,
                       "vehicle_make": "BMW", "vehicle_model": "320d",
                       "additional_terms": "• Standard.", "damages": [],
                       "pickup_date": "2026-10-03", "digital_vertragstext": "AVB"})))

    def neu(**kw):
        erg = {}
        ok = w.run(C.regenerate_contract_for_pickup(
            contract_id=cid, dealer_id=w.dealer_id, user=w.chef,
            grund="abholung_abgeschlossen", ergebnis=erg, **kw))
        return ok, erg, w.run(w.db.generated_pdfs.find_one({"id": cid}))

    # Protokoll 1: Preis 9.000, Sondervereinbarung, Fahrer korrigiert die Marke
    ok, _, doc = neu(neuer_preis=9000, sondervereinbarung="Winterreifen im Kofferraum",
                     korrekturen={"vehicle_make": "Audi", "vehicle_model": "A4"},
                     protokoll_id="p1")
    assert ok and doc["purchase_price"] == 9000.0
    cd = doc["contract_data"]
    assert cd["purchase_price"] == 9000.0 and cd["preis_vor_abholung"] == 10000
    assert "Winterreifen" in cd["additional_terms"]
    assert doc["vertrag_vor_abholung"]["purchase_price"] == 10000
    # RP-432: Kopf des Vertrags folgt der Korrektur
    assert doc["make"] == "Audi" and doc["model"] == "A4" and "Audi" in doc["filename"]
    # RP-494: Fassung 2, ersetzt Fassung 1 vom Tag der Erstellung
    assert cd["fassung"] == 2 and cd["ersetzt_fassung_am"] == "2026-09-20"
    assert erzeugt[-1]["fassung"] == 2

    # Protokoll 2 (Korrektur): kein Preis, keine Vereinbarung, keine Korrektur
    # -> Preis und Vereinbarung wieder wie vor der Abholung (vorher blieb
    # 9.000 stehen). Rollenprüfung 22.09.2026 (Review): Die Korrekturen des
    # Protokolls gelten gegen die AKTUELLE Fassung (protokoll_korrekturen) —
    # "keine Korrektur" heisst also "Audi A4 stimmt", nicht "zurueck auf BMW".
    ok, _, doc = neu(protokoll_id="p2")
    assert ok, "die Korrektur nimmt die Aenderungen zurueck"
    cd = doc["contract_data"]
    assert doc["purchase_price"] == 10000.0 and cd["purchase_price"] == 10000
    assert "preis_vor_abholung" not in cd
    assert "Winterreifen" not in cd["additional_terms"]
    assert cd["vehicle_make"] == "Audi" and doc["make"] == "Audi"
    assert doc["nach_abholung_versand_offen"] is True
    assert doc["nach_abholung_aenderungen"]["nach_protokoll_korrektur"] is True
    # gegenueber dem Vertrag vor der Abholung weiter anders: Marke und Modell
    assert doc["nach_abholung_aenderungen"]["felder"] == ["vehicle_make", "vehicle_model"]
    assert cd["fassung"] == 3

    # Dasselbe noch einmal: nichts zu tun
    ok, erg, doc = neu(protokoll_id="p2")
    assert ok is False and erg["grund"] == "keine_aenderung" and doc["version"] == 3

    # Protokoll 3: anderer Preis — wieder vom Stand VOR der Abholung aus
    ok, _, doc = neu(neuer_preis=9500, protokoll_id="p3")
    cd = doc["contract_data"]
    assert ok and cd["purchase_price"] == 9500.0 and cd["preis_vor_abholung"] == 10000
    assert "Winterreifen" not in cd["additional_terms"]


def test_rp479_ohne_vorherige_abholung_bleibt_es_bei_kein_anlass(welt):
    C = _modul("routes.contracts")
    w = welt
    cid = f"ck_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a)))
    erg = {}
    assert w.run(C.regenerate_contract_for_pickup(
        contract_id=cid, dealer_id=w.dealer_id, user=w.chef,
        grund="abholung_abgeschlossen", protokoll_id="p1", ergebnis=erg)) is False
    assert erg["grund"] == "kein_anlass"
    assert w.run(w.db.generated_pdfs.find_one({"id": cid}))["version"] == 1


def test_rp446_browserdaten_geben_klare_meldung(welt):
    B = _modul("routes.beweise")
    w = welt
    vid, schluessel = f"vb_{w.s}", f"kleinanzeigen:77{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.chef, inserat_schluessel=schluessel)))
    w.run(w.db.listings_cache_client.insert_one(
        {"cache_key": schluessel, "dealer_id": w.dealer_id, "data": {"title": "X"},
         "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)}))
    e = w.run(_erwarte(409, B.beweis_anfordern(B.AnforderungIn(vehicle_id=vid), w.chef)))
    assert "Browser-Erweiterung" in e.detail
    # ohne Client-Daten bleibt es beim bisherigen Hinweis
    w.run(w.db.listings_cache_client.delete_many({}))
    e = w.run(_erwarte(404, B.beweis_anfordern(B.AnforderungIn(vehicle_id=vid), w.chef)))
    assert "noch einmal vergleichen" in e.detail


def test_rp498_beweis_nach_verfall_auf_anforderung_neu(welt):
    import beweis_service as BS
    w = welt
    schluessel = f"kleinanzeigen:98{w.s}"
    alt_fertig = datetime.now(timezone.utc) - timedelta(days=40)
    w.run(w.db.inserat_beweise.insert_one({
        "id": f"bw_{w.s}", "cache_key": schluessel, "quelle": "kleinanzeigen",
        "item_id": f"98{w.s}", "status": "geloescht", "status_vor_loeschung": "fertig",
        "erstellt_am": alt_fertig, "fertig_am": alt_fertig,
        "geloescht_am": datetime.now(timezone.utc) - timedelta(days=5)}))
    daten = {"title": "VW Golf", "list_price": 9000, "make_label": "VW",
             "seller_type": "haendler"}
    # Ein normaler Gebrauch des Links (kein Knopfdruck) belebt nichts
    doc = w.run(BS.beweis_vormerken(w.db, cache_key=schluessel, quelle="kleinanzeigen",
                                    item_id=f"98{w.s}", url="", anlass="verglichen",
                                    daten=daten))
    assert doc["status"] == "geloescht"
    # Ausdruecklich angefordert: neues Dokument, das fruehere wird genannt
    doc = w.run(BS.beweis_vormerken(w.db, cache_key=schluessel, quelle="kleinanzeigen",
                                    item_id=f"98{w.s}", url="", anlass="angefordert",
                                    daten=daten))
    assert doc["status"] == "offen" and doc["id"] == f"bw_{w.s}"
    zeile = w.run(w.db.inserat_beweise.find_one({"cache_key": schluessel}))
    assert zeile["quelle_daten"]["title"] == "VW Golf"
    assert zeile["neu_nach_verfall"]["geloescht_am"] and "geloescht_am" not in zeile
    # ein nie erzeugtes (fehlgeschlagenes) Dokument folgt weiter der alten Regel
    from beweis_pdf import beweis_pdf
    pdf = beweis_pdf(quelle="kleinanzeigen", daten=daten, url="https://example.test/x",
                     item_id="1", beweis_id="abc", abgerufen_am=None,
                     erstellt_am=datetime.now(timezone.utc), fotos=[], foto_urls=[],
                     frueheres_dokument=zeile["neu_nach_verfall"])
    assert "Früheres Dokument" in _pdf_text(pdf)
