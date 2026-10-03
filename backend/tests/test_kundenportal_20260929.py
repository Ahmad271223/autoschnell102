# -*- coding: utf-8 -*-
"""Firmenseite + Kundenportal (Wunsch Ahmad 29.09.2026): eigene Seite je Firma (Adresse per DNS),
Code-Freigabe eines Kaufvertrags, Unterschrift des Kunden im Browser, hinterlegte Unterschrift des
Chefs, Meldung in der App. In-process gegen eine Wegwerf-Datenbank (echtes Mongo), Speicher im RAM,
keine HTTP-Aufrufe."""
import asyncio
import base64
import io
import os
import socket
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import auftraggeber  # noqa: E402
import deps  # noqa: E402
import rate_limiter  # noqa: E402
import routes.contracts as C  # noqa: E402
import routes.kundenportal as KP  # noqa: E402
import routes.protocols as P  # noqa: E402
import storage_service  # noqa: E402
from pdf_service import generate_contract_pdf  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _png(mit_strich: bool = True) -> bytes:
    from PIL import Image, ImageDraw
    bild = Image.new("RGBA", (320, 120), (255, 255, 255, 0))
    if mit_strich:
        z = ImageDraw.Draw(bild)
        z.line([(20, 90), (120, 30), (200, 100), (300, 40)], fill=(10, 10, 40, 255), width=6)
    puffer = io.BytesIO()
    bild.save(puffer, format="PNG")
    return puffer.getvalue()


def _b64(raw: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def _request(ip: str = "203.0.113.7") -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "query_string": b"", "scheme": "http",
                    "server": ("test", 80), "client": (ip, 1234),
                    "headers": [(b"user-agent", b"pytest-portal")]})


def _lauf(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_kp_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, C, KP, P):
        monkeypatch.setattr(mod, "db", db)
    # Speicher im RAM (Bilder, Unterschriften)
    ablage = {}

    async def save_async(key, raw):
        ablage[key] = raw

    async def load_async(key):
        return ablage.get(key)

    async def loeschen(_db, key=None, prefix=None, grund="", dealer_id="", ref=None):
        if key:
            ablage.pop(key, None)
    monkeypatch.setattr(storage_service, "save_async", save_async)
    monkeypatch.setattr(storage_service, "load_async", load_async)
    monkeypatch.setattr(storage_service, "loeschen_oder_vormerken", loeschen)
    monkeypatch.setenv("FRONTEND_URL", "https://app.auto-schnellkauf.de")
    monkeypatch.delenv("FIRMEN_DOMAIN", raising=False)
    run = loop.run_until_complete
    # Indizes wie in Produktion (server.ensure_indexes)
    run(db.dealers.create_index("webseite.slug", unique=True, name="firma_webseite_slug",
                                partialFilterExpression={"webseite.slug": {"$type": "string"}}))
    run(db.generated_pdfs.create_index([("dealer_id", 1), ("portal.code", 1)], unique=True, name="vertrag_portal_code",
                                       partialFilterExpression={"portal.status": "offen"}))
    s = uuid.uuid4().hex[:6]
    dealer_id = f"d-{s}"
    chef = {"id": f"chef-{s}", "dealer_id": dealer_id, "role": "dealer", "active": True, "email": "chef@example.org"}
    sucher = {"id": f"s-{s}", "dealer_id": dealer_id, "role": "sucher", "active": True, "email": "s@example.org"}
    run(db.dealers.insert_one({"id": dealer_id, "user_id": chef["id"], "company_name": "KFZ Müller GmbH",
                               "address": "Hauptstraße 1", "zip_code": "12345", "city": "Berlin",
                               "phone": "030 123456", "email": "info@example.org", "logo_url": ""}))
    run(db.users.insert_many([{**chef, "created_at": "2026-01-01T00:00:00+00:00"},
                              {**sucher, "created_at": "2026-01-02T00:00:00+00:00"}]))
    try:
        yield SimpleNamespace(db=db, run=run, chef=chef, sucher=sucher, dealer_id=dealer_id, ablage=ablage, s=s)
    finally:
        try:
            run(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def _vertrag(w, user, **extra) -> dict:
    """Ein gespeicherter Kaufvertrag samt Druck-PDF (wie create_contract ihn ablegt, gekuerzt)."""
    cd = {"seller_name": "Erika Mustermann", "seller_address": "Musterweg 2", "seller_zip": "10115",
          "seller_city": "Berlin", "seller_phone": "0170 1234567", "seller_email": "erika@example.org",
          "purchase_price": 12500.0, "vehicle_make": "VW", "vehicle_model": "Golf VII", "vehicle_mileage": "85000",
          "vehicle_first_registration": "05/2019", "vehicle_fuel": "Diesel", "contract_no": f"KV-{w.s}"}
    dealer = {"company_name": "KFZ Müller GmbH", "address": "Hauptstraße 1", "zip_code": "12345", "city": "Berlin"}
    pdf = generate_contract_pdf(dealer=dealer, vehicle={"make_label": "VW", "model_label": "Golf VII"}, contract=cd)
    doc = {"id": f"c-{uuid.uuid4().hex[:8]}", "dealer_id": w.dealer_id, "user_id": user["id"], "contract_no": cd["contract_no"],
           "version": 1, "make": "VW", "model": "Golf VII", "seller_name": "Erika Mustermann", "purchase_price": 12500.0,
           "pickup_date": "2026-10-03", "contract_data": cd, "pdf_b64": base64.b64encode(pdf).decode(),
           "created_at": deps.now_iso(), "status": "Vertrag erstellt"}
    doc.update(extra)
    w.run(w.db.generated_pdfs.insert_one(dict(doc)))
    return doc


def _fehler(coro) -> HTTPException:
    with pytest.raises(HTTPException) as e:
        _lauf(coro)
    return e.value


# ------------------------------------------------------------------ Adressen
def test_01_adressen_aus_domain(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://app.auto-schnellkauf.de")
    monkeypatch.delenv("FIRMEN_DOMAIN", raising=False)
    assert KP.firmen_domain() == "auto-schnellkauf.de" and KP.unterdomain_moeglich()
    assert KP.firmen_url("kfz-mueller") == "https://kfz-mueller.auto-schnellkauf.de"
    assert KP.slug_aus_host("KFZ-Mueller.auto-schnellkauf.de:443") == "kfz-mueller"
    assert KP.slug_aus_host("app.auto-schnellkauf.de") is None            # reserviert
    assert KP.slug_aus_host("auto-schnellkauf.de") is None                # Hauptdomain selbst
    assert KP.slug_aus_host("a.b.auto-schnellkauf.de") is None            # zwei Ebenen
    assert KP.slug_aus_host("kfz-mueller.de") is None                     # fremde Domain (-> webseite.domains)
    monkeypatch.setenv("FIRMEN_DOMAIN", "Kunden.Example.org.")
    assert KP.firmen_domain() == "kunden.example.org"
    monkeypatch.delenv("FIRMEN_DOMAIN", raising=False)
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    assert not KP.unterdomain_moeglich() and KP.firmen_url("kfz-mueller") == "http://localhost:3000/firma/kfz-mueller"
    assert KP.slug_vorschlag("KFZ Müller GmbH & Co. KG") == "kfz-mueller-gmbh-co-kg"
    assert len(KP.code_erzeugen()) == 6 and set(KP.code_erzeugen()) <= set(KP.CODE_ZEICHEN)
    assert KP.code_normalisieren(" ab-c2 3x ") == "ABC23X"


# ------------------------------------------------------------------ Firmenseite
def test_02_webseite_pflegen_und_oeffentlich_lesen(welt):
    w = welt
    st = _lauf(KP.get_webseite(user=w.chef))
    assert st["webseite"]["slug"] == "" and st["slug_vorschlag"] == "kfz-mueller-gmbh" and st["ist_chef"] is True
    assert _fehler(KP.put_webseite(KP.WebseiteIn(slug="app"), user=w.chef)).status_code == 400
    assert _fehler(KP.put_webseite(KP.WebseiteIn(slug="KFZ Müller"), user=w.chef)).status_code == 400
    assert _fehler(KP.put_webseite(KP.WebseiteIn(slug="kfz-mueller"), user=w.sucher)).status_code == 403
    assert _fehler(KP.put_webseite(KP.WebseiteIn(domains=["app.auto-schnellkauf.de"]), user=w.chef)).status_code == 400
    assert _fehler(KP.put_webseite(KP.WebseiteIn(domains=["kein host"]), user=w.chef)).status_code == 400
    st = _lauf(KP.put_webseite(KP.WebseiteIn(slug="kfz-mueller", aktiv=True, ueber_uns="  Wir kaufen Ihr Auto.  ",
                                             domains=["KFZ-Mueller.de.", "kfz-mueller.de", "www.kfz-mueller.de"]), user=w.chef))
    assert st["webseite"]["slug"] == "kfz-mueller" and st["webseite"]["aktiv"] is True
    assert st["webseite"]["ueber_uns"] == "Wir kaufen Ihr Auto." and st["webseite"]["domains"] == ["kfz-mueller.de", "www.kfz-mueller.de"]
    assert st["url"] == "https://kfz-mueller.auto-schnellkauf.de" and st["url_pfad"].endswith("/firma/kfz-mueller")
    # Vorlage 29.09.: Ueberschrift/Unterzeile (leer = Vorgabe mit Ort), Social-Links nur als https-Adresse
    assert st["titel_vorgabe"] == "Ihr Partner für den Autoankauf in Berlin" and st["untertitel_vorgabe"] == "Schnell, sicher & fair"
    assert _fehler(KP.put_webseite(KP.WebseiteIn(facebook="kfz.mueller"), user=w.chef)).status_code == 400
    assert _fehler(KP.put_webseite(KP.WebseiteIn(instagram="javascript:alert(1)"), user=w.chef)).status_code == 400
    st = _lauf(KP.put_webseite(KP.WebseiteIn(titel="  Ihr   Autohaus  ", untertitel="", facebook=" https://www.facebook.com/kfz ",
                                             instagram=""), user=w.chef))
    assert st["webseite"]["titel"] == "Ihr Autohaus" and st["webseite"]["facebook"] == "https://www.facebook.com/kfz"
    assert st["webseite"]["untertitel"] == "" and st["webseite"]["instagram"] == ""
    oeff = _lauf(KP.public_firma(slug="kfz-mueller"))
    assert oeff["titel"] == "Ihr Autohaus" and oeff["untertitel"] == "Schnell, sicher & fair"
    assert oeff["social"] == {"facebook": "https://www.facebook.com/kfz", "instagram": ""} and oeff["titelbild"] == ""
    assert oeff["plattform_url"] == "https://app.auto-schnellkauf.de"
    _lauf(KP.put_webseite(KP.WebseiteIn(titel=""), user=w.chef))
    assert _lauf(KP.public_firma(slug="kfz-mueller"))["titel"] == "Ihr Partner für den Autoankauf in Berlin"
    # zweite Firma: dieselbe Adresse / Domain ist vergeben
    d2 = f"d2-{w.s}"
    chef2 = {"id": f"chef2-{w.s}", "dealer_id": d2, "role": "dealer", "active": True}
    w.run(w.db.dealers.insert_one({"id": d2, "user_id": chef2["id"], "company_name": "Zweite"}))
    assert _fehler(KP.put_webseite(KP.WebseiteIn(slug="kfz-mueller"), user=chef2)).status_code == 409
    assert _fehler(KP.put_webseite(KP.WebseiteIn(domains=["kfz-mueller.de"]), user=chef2)).status_code == 409
    # oeffentlich: per Slug, per Unterdomain, per Kundendomain — und aus, wenn abgeschaltet
    for such in ({"slug": "kfz-mueller"}, {"host": "kfz-mueller.auto-schnellkauf.de"}, {"host": "WWW.kfz-mueller.de"}):
        f = _lauf(KP.public_firma(**such))
        assert f["firma"] == "KFZ Müller GmbH" and f["ueber_uns"] == "Wir kaufen Ihr Auto." and f["portal_aktiv"]
        assert f["kontakt"]["ort"] == "Berlin" and f["url"] == "https://kfz-mueller.auto-schnellkauf.de"
    assert _fehler(KP.public_firma(host="andere.auto-schnellkauf.de")).status_code == 404
    _lauf(KP.put_webseite(KP.WebseiteIn(aktiv=False), user=w.chef))
    assert _fehler(KP.public_firma(slug="kfz-mueller")).status_code == 404
    _lauf(KP.put_webseite(KP.WebseiteIn(aktiv=True), user=w.chef))
    # Bilder: hoechstens sechs, verkleinert, oeffentlicher Pfad; entfernen raeumt den Speicher
    for _ in range(KP.BILDER_MAX):
        st = _lauf(KP.bild_hochladen(KP.BildIn(bild_b64=_b64(_png())), user=w.chef))
    assert len(st["webseite"]["bilder"]) == KP.BILDER_MAX and all(b["url"].startswith("/api/files/firma/") for b in st["webseite"]["bilder"])
    assert _fehler(KP.bild_hochladen(KP.BildIn(bild_b64=_b64(_png())), user=w.chef)).status_code == 400
    assert _fehler(KP.bild_hochladen(KP.BildIn(bild_b64=_b64(_png())), user=w.sucher)).status_code == 403
    key = st["webseite"]["bilder"][0]["key"]
    assert key in w.ablage
    st = _lauf(KP.bild_entfernen(key, user=w.chef))
    assert len(st["webseite"]["bilder"]) == KP.BILDER_MAX - 1 and key not in w.ablage
    assert len(_lauf(KP.public_firma(slug="kfz-mueller"))["bilder"]) == KP.BILDER_MAX - 1


# ------------------------------------------------------------------ Unterschrift des Chefs
def test_03_unterschrift_des_chefs(welt):
    w = welt
    assert _fehler(KP.unterschrift_hochladen(KP.UnterschriftIn(bild_b64=_b64(_png(False))), user=w.chef)).status_code == 400
    assert _fehler(KP.unterschrift_hochladen(KP.UnterschriftIn(bild_b64=_b64(_png())), user=w.sucher)).status_code == 403
    assert _fehler(KP.unterschrift_anzeigen(user=w.chef)).status_code == 404
    assert _lauf(KP.unterschrift_hochladen(KP.UnterschriftIn(bild_b64=_b64(_png())), user=w.chef))["unterschrift_vorhanden"]
    d = w.run(w.db.dealers.find_one({"id": w.dealer_id}, {"_id": 0, "unterschrift_key": 1}))
    assert d["unterschrift_key"].startswith(f"unterschrift/{w.dealer_id}/") and d["unterschrift_key"] in w.ablage
    antwort = _lauf(KP.unterschrift_anzeigen(user=w.chef))            # Vorschau nur fuer den Chef (01.10.2026)
    assert antwort.media_type == "image/png" and antwort.body[:8] == b"\x89PNG\r\n\x1a\n"
    alt = d["unterschrift_key"]
    _lauf(KP.unterschrift_hochladen(KP.UnterschriftIn(bild_b64=_b64(_png())), user=w.chef))
    assert alt not in w.ablage                                          # alte Datei raeumt sich weg
    assert _lauf(KP.unterschrift_entfernen(user=w.chef))["unterschrift_vorhanden"] is False
    assert not w.ablage and _fehler(KP.unterschrift_anzeigen(user=w.chef)).status_code == 404


# ------------------------------------------------------------------ Kundenportal: Freigabe -> Code -> Unterschrift
def test_04_freigabe_code_und_unterschrift(welt, monkeypatch):
    w = welt
    monkeypatch.setattr(deps, "db", w.db)
    monkeypatch.setattr(auftraggeber, "deps", deps)
    c = _vertrag(w, w.sucher)
    # ohne Firmenseite keine Freigabe
    assert _fehler(KP.portal_freigeben(c["id"], user=w.sucher)).status_code == 409
    _lauf(KP.put_webseite(KP.WebseiteIn(slug="kfz-mueller", aktiv=True), user=w.chef))
    _lauf(KP.unterschrift_hochladen(KP.UnterschriftIn(bild_b64=_b64(_png())), user=w.chef))
    frei = _lauf(KP.portal_freigeben(c["id"], user=w.sucher))
    code = frei["code"]
    assert frei["status"] == "offen" and len(code) == 6 and set(code) <= set(KP.CODE_ZEICHEN)
    assert frei["url"] == "https://kfz-mueller.auto-schnellkauf.de" and frei["version"] == 1
    assert _lauf(KP.portal_freigeben(c["id"], user=w.chef))["code"] == code      # noch gueltig -> derselbe Code
    assert _lauf(KP.portal_stand(c["id"], user=w.sucher))["code"] == code
    # fremder Sucher derselben Firma sieht den Vertrag nicht
    fremd = {"id": f"s2-{w.s}", "dealer_id": w.dealer_id, "role": "sucher", "active": True}
    assert _fehler(KP.portal_stand(c["id"], user=fremd)).status_code == 404
    # Kunde: falscher Code, falsche Adresse, richtiger Code
    assert _fehler(KP.portal_oeffnen(KP.OeffnenIn(code="ABCDEF", slug="kfz-mueller"), _request())).status_code == 404
    assert _fehler(KP.portal_oeffnen(KP.OeffnenIn(code=code, slug="andere"), _request())).status_code == 404
    offen = _lauf(KP.portal_oeffnen(KP.OeffnenIn(code=code.lower(), host="kfz-mueller.auto-schnellkauf.de"), _request()))
    sitzung = offen["sitzung"]
    assert offen["vertrag"]["contract_no"] == c["contract_no"] and offen["vertrag"]["verkaeufer"] == "Erika Mustermann"
    assert offen["vertrag"]["kaufpreis"] == 12500.0 and offen["firma"]["firma"] == "KFZ Müller GmbH"
    assert _lauf(KP.portal_sitzung(sitzung))["vertrag"]["status"] == "offen"
    pdf = _lauf(KP.portal_sitzung_pdf(sitzung))
    assert pdf.media_type == "application/pdf" and pdf.body[:4] == b"%PDF" and "no-store" in pdf.headers["cache-control"]
    # Unterschreiben: Zustimmung Pflicht, leeres Bild abgelehnt, dann ok
    assert _fehler(KP.portal_unterschreiben(sitzung, KP.UnterschreibenIn(signature_b64=_b64(_png()), name="Erika Mustermann",
                                                                          einverstanden=False), _request())).status_code == 400
    assert _fehler(KP.portal_unterschreiben(sitzung, KP.UnterschreibenIn(signature_b64=_b64(_png(False)), name="Erika Mustermann",
                                                                          einverstanden=True), _request())).status_code == 400
    erg = _lauf(KP.portal_unterschreiben(sitzung, KP.UnterschreibenIn(signature_b64=_b64(_png()), name="  Erika   Mustermann ",
                                                                       einverstanden=True), _request("198.51.100.9")))
    assert erg["ok"] and erg["contract_no"] == c["contract_no"]
    doc = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0}))
    p = doc["portal"]
    assert p["status"] == "unterschrieben" and p["name"] == "Erika Mustermann" and p["ip"] == "198.51.100.9"
    assert p["kaeufer_unterschrift"] is True and p["unterschrift_key"] in w.ablage and doc["kunde_unterschrieben_am"]
    signiert = base64.b64decode(doc["pdf_signiert_b64"])
    assert signiert[:4] == b"%PDF" and doc["pdf_signiert_version"] == 1
    from pypdf import PdfReader
    text = "".join(seite.extract_text() or "" for seite in PdfReader(io.BytesIO(signiert)).pages)
    assert "Kundenportal von KFZ Müller GmbH" in text and "Erika Mustermann" in text and "hinterlegte Unterschrift" in text
    # danach: Sitzung liefert die unterschriebene Fassung, kein zweites Mal, keine neue Freigabe
    assert _lauf(KP.portal_sitzung(sitzung))["pdf_signiert"] is True
    assert _lauf(KP.portal_sitzung_pdf(sitzung)).body == signiert
    assert _fehler(KP.portal_unterschreiben(sitzung, KP.UnterschreibenIn(signature_b64=_b64(_png()), name="X Y",
                                                                          einverstanden=True), _request())).status_code == 409
    assert _fehler(KP.portal_freigeben(c["id"], user=w.sucher)).status_code == 409
    assert _fehler(KP.portal_oeffnen(KP.OeffnenIn(code=code, slug="kfz-mueller"), _request())).status_code == 404
    st = _lauf(KP.portal_stand(c["id"], user=w.chef))
    assert st["status"] == "unterschrieben" and st["code"] is None and st["pdf_signiert"] is True
    assert _lauf(KP.portal_pdf(c["id"], user=w.sucher)).body == signiert
    assert _fehler(KP.portal_pdf(c["id"], user=fremd)).status_code == 404
    # Meldung in der App: Chef und der Sucher, der den Vertrag angelegt hat — je einmal, bis gelesen
    for nutzer in (w.chef, w.sucher):
        z = _lauf(KP.meldungen_anzahl(user=nutzer))
        assert z["ungelesen"] == 1
        liste = _lauf(KP.meldungen_liste(user=nutzer))
        assert liste[0]["typ"] == "vertrag_unterschrieben" and "Erika Mustermann" in liste[0]["text"]
        assert liste[0]["ref"] == c["id"] and liste[0]["gelesen"] is False
    assert _lauf(KP.meldungen_anzahl(user=fremd))["ungelesen"] == 0
    _lauf(KP.meldung_gelesen(z["ids"][0], user=w.sucher))
    assert _lauf(KP.meldungen_anzahl(user=w.sucher))["ungelesen"] == 0
    assert _lauf(KP.meldungen_anzahl(user=w.chef))["ungelesen"] == 1     # Lesen ist je Konto
    assert _lauf(KP.meldungen_alle_gelesen(user=w.chef))["gelesen"] == 1
    m = w.run(w.db.meldungen.find_one({"ref": c["id"]}, {"_id": 0}))
    assert isinstance(m["laeuft_ab"], datetime)                          # TTL-Index braucht ein echtes Datum


# ------------------------------------------------------------------ Grenzen: zurueckgezogen, abgelaufen, neue Fassung, Drossel
def test_05_code_grenzen_und_drossel(welt, monkeypatch):
    w = welt
    _lauf(KP.put_webseite(KP.WebseiteIn(slug="kfz-mueller", aktiv=True), user=w.chef))
    c = _vertrag(w, w.chef)
    code = _lauf(KP.portal_freigeben(c["id"], user=w.chef))["code"]
    # zurueckgezogen: tot, Stand sagt es
    st = _lauf(KP.portal_zurueckziehen(c["id"], user=w.chef))
    assert st["status"] == "zurueckgezogen" and st["code"] is None
    assert _fehler(KP.portal_oeffnen(KP.OeffnenIn(code=code, slug="kfz-mueller"), _request())).status_code == 404
    # neuer Code, dann abgelaufen
    code2 = _lauf(KP.portal_freigeben(c["id"], user=w.chef))["code"]
    assert code2 != code or True                                          # Zufall — nur der Status zaehlt
    sitzung = _lauf(KP.portal_oeffnen(KP.OeffnenIn(code=code2, slug="kfz-mueller"), _request()))["sitzung"]
    w.run(w.db.generated_pdfs.update_one({"id": c["id"]}, {"$set": {"portal.laeuft_ab": (
        datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}}))
    assert _lauf(KP.portal_stand(c["id"], user=w.chef))["status"] == "abgelaufen"
    assert _fehler(KP.portal_oeffnen(KP.OeffnenIn(code=code2, slug="kfz-mueller"), _request())).status_code == 404
    assert _fehler(KP.portal_sitzung(sitzung)).status_code == 410       # laufende Sitzung endet mit dem Code
    # Freigabe erneuert den Code; eine neue Vertragsfassung macht ihn ungueltig
    code3 = _lauf(KP.portal_freigeben(c["id"], user=w.chef))["code"]
    sitzung3 = _lauf(KP.portal_oeffnen(KP.OeffnenIn(code=code3, slug="kfz-mueller"), _request()))["sitzung"]
    w.run(w.db.generated_pdfs.update_one({"id": c["id"]}, {"$set": {"version": 2}}))
    assert _lauf(KP.portal_stand(c["id"], user=w.chef))["status"] == "fassung_veraltet"
    assert _fehler(KP.portal_oeffnen(KP.OeffnenIn(code=code3, slug="kfz-mueller"), _request())).status_code == 404
    assert _fehler(KP.portal_sitzung_pdf(sitzung3)).status_code == 410
    code4 = _lauf(KP.portal_freigeben(c["id"], user=w.chef))["code"]
    assert _lauf(KP.portal_stand(c["id"], user=w.chef))["version"] == 2
    # Drossel je Besucheradresse: nach 10 Fehlversuchen 429 — auch der richtige Code kommt dann nicht mehr durch
    monkeypatch.setattr(rate_limiter, "_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(KP, "_code_limiter_ip", rate_limiter.SlidingWindowRateLimiter(
        max_attempts=10, window_seconds=600, name=f"portal_test_{w.s}", fail_closed=True))
    for _ in range(10):
        assert _fehler(KP.portal_oeffnen(KP.OeffnenIn(code="ZZZZZZ", slug="kfz-mueller"), _request("192.0.2.44"))).status_code == 404
    assert _fehler(KP.portal_oeffnen(KP.OeffnenIn(code=code4, slug="kfz-mueller"), _request("192.0.2.44"))).status_code == 429
    assert _lauf(KP.portal_oeffnen(KP.OeffnenIn(code=code4, slug="kfz-mueller"), _request("192.0.2.45")))["sitzung"]
    # gefaelschte Sitzung
    assert _fehler(KP.portal_sitzung("kein.token.hier")).status_code == 401
    # Sitzung kennt nur ihre Fassung: JWT mit alter Version -> 410
    import jwt as _jwt
    import auth as _auth
    alt = _jwt.encode({"typ": "portal", "cid": c["id"], "v": 1, "d": w.dealer_id,
                       "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, _auth.JWT_SECRET, algorithm=_auth.JWT_ALG)
    assert _fehler(KP.portal_sitzung(alt)).status_code == 410


# ------------------------------------------------------------------ PDF mit Unterschriften
def test_06_pdf_mit_unterschriften():
    cd = {"seller_name": "Max Muster", "purchase_price": 9999.0, "vehicle_make": "Opel", "vehicle_model": "Astra",
          "contract_no": "KV-TEST"}
    dealer = {"company_name": "Testhaus", "address": "Weg 1", "zip_code": "10000", "city": "Berlin"}
    ohne = generate_contract_pdf(dealer=dealer, vehicle={}, contract=cd)
    mit = generate_contract_pdf(dealer=dealer, vehicle={}, contract=cd, unterschriften={
        "verkaeufer": _png(), "verkaeufer_text": "Max Muster · digital am 29.09.2026, 10:00 Uhr",
        "kaeufer": None, "kaeufer_text": None, "hinweis": "Digital unterschrieben über das Kundenportal von Testhaus."})
    assert mit[:4] == b"%PDF" and len(mit) > len(ohne)
    from pypdf import PdfReader
    text = "".join(s.extract_text() or "" for s in PdfReader(io.BytesIO(mit)).pages)
    assert "Kundenportal von Testhaus" in text and "digital am 29.09.2026" in text
    # kaputtes Bild: kein Absturz, Linie bleibt leer
    kaputt = generate_contract_pdf(dealer=dealer, vehicle={}, contract=cd,
                                   unterschriften={"verkaeufer": b"kein bild", "verkaeufer_text": "x"})
    assert kaputt[:4] == b"%PDF"


# ------------------------------------------------------------------ Weg A: Betreiber richtet ein, Domain pruefen
SUPER = {"id": "sa", "role": "admin", "is_super_admin": True}


def test_07_betreiber_richtet_firmenseite_ein(welt):
    w = welt
    st = _lauf(KP.admin_get_webseite(w.dealer_id, admin=SUPER))
    assert st["ist_chef"] is True and st["firma"] == "KFZ Müller GmbH" and st["slug_vorschlag"] == "kfz-mueller-gmbh"
    st = _lauf(KP.admin_put_webseite(w.dealer_id, KP.WebseiteIn(slug="kfz-mueller", aktiv=True, ueber_uns="Hallo",
                                                                  domains=["kfz-mueller.de"]), admin=SUPER))
    assert st["webseite"]["slug"] == "kfz-mueller" and st["webseite"]["domains"] == ["kfz-mueller.de"]
    assert _fehler(KP.admin_put_webseite("gibt-es-nicht", KP.WebseiteIn(slug="x-y-z"), admin=SUPER)).status_code == 404
    st = _lauf(KP.admin_bild_hochladen(w.dealer_id, KP.BildIn(bild_b64=_b64(_png())), admin=SUPER))
    assert len(st["webseite"]["bilder"]) == 1
    st = _lauf(KP.admin_bild_entfernen(w.dealer_id, st["webseite"]["bilder"][0]["key"], admin=SUPER))
    assert st["webseite"]["bilder"] == []
    assert _lauf(KP.admin_unterschrift_hochladen(w.dealer_id, KP.UnterschriftIn(bild_b64=_b64(_png())), admin=SUPER))["unterschrift_vorhanden"]
    assert _lauf(KP.admin_unterschrift_anzeigen(w.dealer_id, admin=SUPER)).media_type == "image/png"
    assert _lauf(KP.admin_unterschrift_entfernen(w.dealer_id, admin=SUPER))["unterschrift_vorhanden"] is False
    assert _fehler(KP.admin_unterschrift_hochladen("gibt-es-nicht", KP.UnterschriftIn(bild_b64=_b64(_png())), admin=SUPER)).status_code == 404
    # das Audit nennt den Betreiber als Verursacher
    a = w.run(w.db.activity_logs.find_one({"dealer_id": w.dealer_id, "action": "firma.webseite.geaendert"}, {"_id": 0}))
    assert a and a.get("user_id") == "sa"
    # oeffentlich sichtbar wie vom Chef eingerichtet
    assert _lauf(KP.public_firma(host="kfz-mueller.de"))["ueber_uns"] == "Hallo"


def test_08_proxy_hosts_und_domain_pruefung(welt, monkeypatch):
    w = welt
    monkeypatch.setenv("FIRMEN_HOSTS", "*.auto-schnellkauf.de kfz-mueller.de, www.kfz-mueller.de")
    assert KP.proxy_hosts() == ["*.auto-schnellkauf.de", "kfz-mueller.de", "www.kfz-mueller.de"]
    assert KP.proxy_kennt("kfz-mueller.auto-schnellkauf.de") and KP.proxy_kennt("KFZ-Mueller.de:443")
    assert KP.proxy_kennt("app.auto-schnellkauf.de")                      # Hauptadresse immer
    assert not KP.proxy_kennt("a.b.auto-schnellkauf.de") and not KP.proxy_kennt("andere.de")
    _lauf(KP.put_webseite(KP.WebseiteIn(slug="kfz-mueller", aktiv=True, domains=["kfz-mueller.de", "neu.example"]), user=w.chef))
    # DNS/HTTPS werden ersetzt — keine echten Netzaufrufe im Test
    antworten = {}

    async def dns(host):
        if host in antworten.get("dns", {}):
            return antworten["dns"][host]
        raise socket.gaierror("kein Eintrag")

    async def https(host):
        return antworten["https"][host]
    monkeypatch.setattr(KP, "_dns", dns)
    monkeypatch.setattr(KP, "_https_firma", https)
    # 1) DNS fehlt -> erster Schritt rot, Proxy kennt sie nicht -> naechster Schritt = DNS
    antworten = {"dns": {}, "https": {}}
    e = _lauf(KP.admin_domain_pruefung(w.dealer_id, "neu.example", admin=SUPER))
    assert e["ok"] is False and [s["ok"] for s in e["schritte"]] == [False, False]
    assert e["naechster_schritt"].startswith("DNS:") and "CNAME auf app.auto-schnellkauf.de" in e["naechster_schritt"]
    # 2) DNS ok, Proxy kennt sie nicht, Plattform antwortet 444 -> naechster Schritt = Proxy
    antworten = {"dns": {"neu.example": ["104.21.6.253"]}, "https": {"neu.example": {"status": 444, "cloudflare": True, "slug": None}}}
    e = _lauf(KP.admin_domain_pruefung(w.dealer_id, "neu.example", admin=SUPER))
    assert [s["schritt"] for s in e["schritte"]] == ["dns", "proxy", "https", "firmenseite"]
    assert e["naechster_schritt"].startswith("Proxy:") and "FIRMEN_HOSTS" in e["naechster_schritt"]
    # 3) alles steht, aber die Domain ist bei einer anderen Firma eingetragen
    antworten = {"dns": {"kfz-mueller.de": ["104.21.6.253"]}, "https": {"kfz-mueller.de": {"status": 200, "cloudflare": True, "slug": "andere"}}}
    e = _lauf(KP.admin_domain_pruefung(w.dealer_id, "kfz-mueller.de", admin=SUPER))
    assert e["ok"] is False and "anderen Firma" in e["schritte"][-1]["text"]
    # 4) alles gut
    antworten["https"]["kfz-mueller.de"] = {"status": 200, "cloudflare": True, "slug": "kfz-mueller"}
    e = _lauf(KP.admin_domain_pruefung(w.dealer_id, "kfz-mueller.de", admin=SUPER))
    assert e["ok"] is True and e["naechster_schritt"] == "" and e["url"] == "https://kfz-mueller.de"
    assert all(s["ok"] for s in e["schritte"]) and "über Cloudflare" in e["schritte"][2]["text"]
    # 5) HTTPS scheitert (Zertifikat) -> Hinweis auf Cloudflare-Proxy / SSL Full
    async def https_kaputt(host):
        raise ConnectionError("certificate verify failed")
    monkeypatch.setattr(KP, "_https_firma", https_kaputt)
    e = _lauf(KP.admin_domain_pruefung(w.dealer_id, "kfz-mueller.de", admin=SUPER))
    assert e["schritte"][2]["ok"] is False and e["naechster_schritt"].startswith("HTTPS:")
    # Chef prueft nur eigene Domains; ungueltige Domain -> 400
    monkeypatch.setattr(KP, "_https_firma", https)
    assert _fehler(KP.dealer_domain_pruefung("fremde.example", user=w.chef)).status_code == 400
    assert _lauf(KP.dealer_domain_pruefung("kfz-mueller.de", user=w.chef))["ok"] is True
    assert _lauf(KP.dealer_domain_pruefung("kfz-mueller.auto-schnellkauf.de", user=w.chef))["schritte"][1]["ok"] is True
    assert _fehler(KP.admin_domain_pruefung(w.dealer_id, "127.0.0.1", admin=SUPER)).status_code == 400
    assert _fehler(KP.admin_domain_pruefung(w.dealer_id, "kein host", admin=SUPER)).status_code == 400
