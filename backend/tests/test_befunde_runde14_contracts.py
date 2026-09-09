# -*- coding: utf-8 -*-
"""Nachpruefung Runde 14 (07.09.2026), Gruppe "contracts" — routes/contracts.py.

  68  GET /contracts schreibt keine heutigen Fahrzeugbilder mehr an Altvertraege
  69  send_contract nutzt effective_dealer (Sucher-Overrides) fuer die Mail
  73  /contracts liefert bis 2000 Eintraege und signalisiert Abschneiden
  88  DamageIn-Schema: nur Objekte, max. 50 Schaeden, Strings gedeckelt
  118 send_status per $push/$slice auf die juengsten 200 Eintraege begrenzt

Einheitentests laufen ohne Backend. HTTP-Tests brauchen das Backend auf
TEST_BASE_URL (Mock-Anbieter) und RUNDE14_HTTP=1 — sie werden nach dem
Neustart gesammelt ausgefuehrt.
"""
import asyncio
import inspect
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "autoschnell")

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
SUF = uuid.uuid4().hex[:8]
PW = "RundeVierzehn14!"
JETZT = datetime.now(timezone.utc)


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _basis_vertrag(**extra):
    d = {"vehicle_id": "v1", "seller_name": "Test Verkaeufer", "purchase_price": 1000}
    d.update(extra)
    return d


# =============================================================== 88: DamageIn (unit)
def test_88_damages_string_element_wird_abgelehnt():
    """Freitext-Strings sind erlaubt (auto_daten.schaeden_bereinigen liest
    sie, pdf_service rendert sie); kaputte Typen und zu lange Texte nicht."""
    from pydantic import ValidationError
    from routes.contracts import ContractIn
    ok = ContractIn(**_basis_vertrag(damages=["Kratzer Tuer links", {"zone": "vorne"}]))
    assert ok.damages[0] == "Kratzer Tuer links"
    for kaputt in ([None], [["liste"]], ["x" * 5001], [{"zone": "a"}] * 201):
        with pytest.raises(ValidationError):
            ContractIn(**_basis_vertrag(damages=kaputt))


def test_88_mehr_als_50_schaeden_und_lange_strings_abgelehnt():
    from pydantic import ValidationError
    from routes.contracts import ContractIn
    with pytest.raises(ValidationError):
        ContractIn(**_basis_vertrag(damages=[{"zone": "a"}] * 201))
    with pytest.raises(ValidationError):
        ContractIn(**_basis_vertrag(damages=[{"zone": "x" * 200000}]))
    with pytest.raises(ValidationError):
        ContractIn(**_basis_vertrag(damages=[{"type_label": "x" * 501}]))
    # 200 Eintraege mit 500 Zeichen sind die Obergrenze — erlaubt.
    ContractIn(**_basis_vertrag(damages=[{"zone": "x" * 500}] * 200))


def test_88_skizzen_eintrag_behaelt_gelesene_schluessel():
    """Die Skizze (DamageSelector.jsx) schickt id/view/type_key/type_label/
    abbr/color/zone/x/y; PDF, auto_daten und Abholprotokoll lesen
    type_label/type_key/zone. Unbekannte Schluessel werden ignoriert."""
    from routes.contracts import ContractIn
    roh = {"id": "1-ab", "view": "front", "type_key": "kratzer",
           "type_label": "Kratzer", "abbr": "K", "color": "#f00",
           "zone": "Stossstange vorn", "x": 12.5, "y": 40, "fremd": "weg"}
    c = ContractIn(**_basis_vertrag(damages=[roh] * 5))
    d = c.model_dump()["damages"]
    assert len(d) == 5
    for k in ("id", "view", "type_key", "type_label", "abbr", "color", "zone"):
        assert d[0][k] == roh[k], k
    assert d[0]["x"] == 12.5 and d[0]["y"] == 40.0
    assert "fremd" not in d[0]
    # leere/fehlende Liste wie bisher
    assert ContractIn(**_basis_vertrag()).model_dump()["damages"] == []
    assert ContractIn(**_basis_vertrag(damages=None)).model_dump()["damages"] is None


def test_88_pdf_mit_skizzen_schaeden_wird_erzeugt():
    from routes.contracts import ContractIn
    from pdf_service import generate_contract_pdf
    c = ContractIn(**_basis_vertrag(damages=[
        {"type_key": "delle", "type_label": "Delle", "zone": "Tuer links"},
        {"type_label": "Kratzer", "zone": "Heck"},
    ]))
    pdf = generate_contract_pdf(dealer={}, vehicle={}, contract=c.model_dump())
    assert pdf[:4] == b"%PDF"


# =============================================================== 69: Mail-Identitaet (unit)
class _FakeColl:
    def __init__(self, doc):
        self.doc = doc
        self.updates = []

    async def find_one(self, *a, **k):
        return dict(self.doc)

    async def update_one(self, filt, upd, **k):
        self.updates.append((filt, upd))

        class R:
            modified_count = 1
            matched_count = 1
        return R()


class _FakeDb:
    def __init__(self, vertrag):
        self.generated_pdfs = _FakeColl(vertrag)

    def __getattr__(self, name):
        raise AssertionError(f"unerwarteter Zugriff auf db.{name}")


def test_69_send_contract_nutzt_effective_dealer_fuer_die_mail(monkeypatch):
    """Sucher mit settings_override (Firmenname, Telefon, Logo): Betreff/HTML
    und Absendername der Mail nennen die Filiale, nicht den Chef."""
    import deps
    import email_service
    import provider_fetch
    import routes.contracts as cm

    vertrag = {"id": "c1", "dealer_id": "d1", "user_id": "u1",
               "contract_no": "KV-1", "seller_name": "Max Kunde",
               "make": "BMW", "model": "320d", "purchase_price": 5000,
               "pdf_b64": "", "filename": "Kaufvertrag.pdf", "send_status": [],
               # Seit 09.09.2026 haengt der Versand die DIGITALE Fassung an;
               # liegt sie vor, braucht der Weg keine Nacherzeugung (kein
               # Zugriff auf vehicles/dealers — genau das prueft dieser Test).
               "pdf_digital_b64": "JVBERi0xLjQgdGVzdA=="}
    sucher = {"id": "u1", "dealer_id": "d1", "role": "sucher",
              "email": "sucher@e2etest-mail.de", "first_name": "Sina", "last_name": "S",
              "settings_override": {"company_name": "Filiale Sued",
                                    "phone": "0711 555", "logo_url": "https://x/logo.png"}}
    chef = {"id": "d1", "company_name": "Chef GmbH", "phone": "030 1",
            "logo_url": "https://x/chef.png", "email": "chef@e2etest-mail.de"}
    fake = _FakeDb(vertrag)
    monkeypatch.setattr(cm, "db", fake)

    async def _eff(user):
        # wie deps.effective_dealer: Overrides ueber das Chef-Dokument
        m = dict(chef)
        m.update({k: v for k, v in (user.get("settings_override") or {}).items()
                  if k in deps.SUCHER_SETTINGS_FIELDS})
        return m
    monkeypatch.setattr(deps, "effective_dealer", _eff)
    # Der rohe Haendler-Datensatz darf gar nicht mehr gelesen werden:
    # _FakeDb wirft bei db.dealers (siehe __getattr__).
    monkeypatch.setattr(provider_fetch, "MOCK_PROVIDER_FETCH", False)
    monkeypatch.setattr(email_service, "email_configured", lambda: True)
    gesendet = []

    async def _send_mit_beleg(to, subject, text, anhang=None, anhang_name="", **kw):
        gesendet.append({"to": to, "subject": subject, "text": text, **kw})
        return True, "resend:test"

    async def _send(to, subject, text, *a, **kw):
        gesendet.append({"to": to, "subject": subject, "text": text, **kw})
        return True
    monkeypatch.setattr(email_service, "send_email_mit_beleg", _send_mit_beleg)
    monkeypatch.setattr(email_service, "send_email", _send)

    async def _log(*a, **k):
        return None
    monkeypatch.setattr(cm, "log_activity", _log)

    body = cm.SendIn(channel="email", recipient="kunde@e2etest-mail.de",
                     message="Hier der Vertrag.", idempotency_key="k1")
    out = asyncio.run(cm.send_contract("c1", body, user=sucher))
    assert out["zustellung"] == "versendet" and out["kopie"] == "gesendet", out
    haupt, kopie = gesendet[0], gesendet[1]
    assert haupt["absender_name"] == "Filiale Sued"
    assert kopie["absender_name"] == "Filiale Sued"
    assert "Filiale Sued" in haupt["html"] and "Chef GmbH" not in haupt["html"]
    assert "0711 555" in haupt["html"] and "030 1" not in haupt["html"]
    assert "https://x/logo.png" in haupt["html"] and "chef.png" not in haupt["html"]
    assert "Filiale Sued" in kopie["html"] and "Chef GmbH" not in kopie["html"]
    assert haupt["reply_to"] == "sucher@e2etest-mail.de"


def test_69_send_contract_liest_kein_rohes_haendlerdokument_mehr():
    import routes.contracts as cm
    quelle = inspect.getsource(cm.send_contract)
    assert "db.dealers.find_one" not in quelle
    assert "effective_dealer(user)" in quelle


# =============================================================== 118: $slice (unit)
def test_118_beide_push_stellen_begrenzen_send_status():
    import routes.contracts as cm
    quelle = inspect.getsource(cm.send_contract)
    assert quelle.count('"$push": {"send_status"') == 2, "beide Schreibpfade erwartet"
    assert quelle.count('"$slice": -SEND_STATUS_MAX') == 2
    assert cm.SEND_STATUS_MAX == 200


def test_118_slice_behaelt_juengste_und_positionales_set_findet_den_neuen():
    """Mechanik gegen echtes Mongo: 210 Reservierungen mit $each/$slice ->
    200 bleiben (aelteste raus), der zuletzt angehaengte ist der letzte und
    das positionale $set (Ergebnis eintragen) trifft ihn weiterhin."""
    from routes.contracts import SEND_STATUS_MAX
    try:
        db = _db()
        db.command("ping")
    except Exception as e:  # pragma: no cover - ohne Mongo kein Mechaniktest
        pytest.skip(f"kein Mongo: {e}")
    cid = f"r14-slice-{SUF}"
    db.generated_pdfs.insert_one({"id": cid, "dealer_id": f"d-{SUF}", "send_status": []})
    try:
        for i in range(SEND_STATUS_MAX + 10):
            db.generated_pdfs.update_one(
                {"id": cid, "send_status.idempotency_key": {"$ne": f"k{i}"}},
                {"$push": {"send_status": {"$each": [{
                    "idempotency_key": f"k{i}", "channel": "whatsapp",
                    "zustellung": "laeuft"}], "$slice": -SEND_STATUS_MAX}}})
        st = db.generated_pdfs.find_one({"id": cid})["send_status"]
        assert len(st) == SEND_STATUS_MAX
        assert st[0]["idempotency_key"] == "k10" and st[-1]["idempotency_key"] == f"k{SEND_STATUS_MAX + 9}"
        res = db.generated_pdfs.update_one(
            {"id": cid, "send_status": {"$elemMatch": {"idempotency_key": f"k{SEND_STATUS_MAX + 9}"}}},
            {"$set": {"send_status.$": {"idempotency_key": f"k{SEND_STATUS_MAX + 9}",
                                        "zustellung": "chat_geoeffnet"}}})
        assert res.modified_count == 1
        st = db.generated_pdfs.find_one({"id": cid})["send_status"]
        assert st[-1]["zustellung"] == "chat_geoeffnet"
        # Doppelklick mit vorhandenem Schluessel: $ne-Filter greift weiter
        res = db.generated_pdfs.update_one(
            {"id": cid, "send_status.idempotency_key": {"$ne": "k50"}},
            {"$push": {"send_status": {"$each": [{"idempotency_key": "k50"}],
                                       "$slice": -SEND_STATUS_MAX}}})
        assert res.modified_count == 0
    finally:
        db.generated_pdfs.delete_many({"id": cid})


# =============================================================== 68/73: list_contracts (unit)
def test_68_list_contracts_schreibt_keine_bilder_mehr_an_den_vertrag():
    import routes.contracts as cm
    quelle = inspect.getsource(cm.list_contracts)
    assert "update_one" not in quelle, "GET /contracts darf nicht schreiben"
    assert 'i["bilder_nachgetragen"] = True' in quelle


def test_73_list_contracts_limit_2000_mit_kopfzeile():
    import routes.contracts as cm
    assert cm.CONTRACTS_LIST_MAX == 2000
    quelle = inspect.getsource(cm.list_contracts)
    assert "to_list(CONTRACTS_LIST_MAX + 1)" in quelle
    assert 'response.headers["X-Truncated"]' in quelle


# =============================================================== HTTP
@pytest.fixture(scope="module")
def welt():
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    r = requests.post(f"{API}/auth/register", json={
        "email": f"r14c_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": "R14 Contracts GmbH", "contact_person": "R T",
        "phone": "0511 14"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    me = requests.get(f"{API}/auth/me", headers=h, timeout=30).json()["user"]
    _db().subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": me["dealer_id"], "subject_user_id": me["id"],
        "plan": "monthly", "status": "active",
        "expires_at": (JETZT + timedelta(days=1)).isoformat(), "created_at": JETZT.isoformat()})
    ka = f"https://www.kleinanzeigen.de/s-anzeige/r14c/94{uuid.uuid4().int % 10**8:08d}-216-1"
    r = requests.post(f"{API}/mobile/compare", json={"url": ka}, headers=h, timeout=90)
    if r.status_code != 200 or not (r.json().get("vehicle") or {}).get("_mock"):
        pytest.skip("Backend ohne MOCK_PROVIDER_FETCH")
    vid = r.json()["vehicle_id"]
    r = requests.post(f"{API}/contracts", headers=h, json={
        "vehicle_id": vid, "seller_name": "R V", "seller_address": "Weg 1",
        "seller_zip": "30159", "seller_city": "Hannover", "purchase_price": 5000},
        timeout=90)
    assert r.status_code == 200, r.text[:200]
    yield {"h": h, "cid": r.json()["id"], "vid": vid, "me": me}
    dbx = _db()
    for c in ("subscriptions", "vehicles", "appointments", "generated_pdfs",
              "generated_pdf_versions", "activity_logs"):
        dbx[c].delete_many({"dealer_id": me["dealer_id"]})
    dbx.users.delete_many({"id": me["id"]})
    dbx.dealers.delete_many({"id": me["dealer_id"]})


def test_http_88_preview_lehnt_kaputte_schaeden_ab(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    basis = {"vehicle_id": welt["vid"], "seller_name": "R V", "purchase_price": 100}
    for kaputt in ([None], [{"zone": "a"}] * 201, [{"zone": "x" * 200000}], ["x" * 5001]):
        r = requests.post(f"{API}/contracts/preview", headers=welt["h"],
                          json={**basis, "damages": kaputt}, timeout=60)
        assert r.status_code == 422, (r.status_code, r.text[:200])
    # Freitext-Eintrag bleibt erlaubt und landet im PDF (kein 500 mehr)
    r = requests.post(f"{API}/contracts/preview", headers=welt["h"],
                      json={**basis, "damages": ["Kratzer Tuer links"]}, timeout=60)
    assert r.status_code == 200, (r.status_code, r.text[:200])
    r = requests.post(f"{API}/contracts/preview", headers=welt["h"], json={
        **basis, "damages": [{"id": f"{i}", "view": "front", "type_key": "kratzer",
                              "type_label": "Kratzer", "abbr": "K", "color": "#f00",
                              "zone": f"Zone {i}", "x": 1.5, "y": 2} for i in range(5)]},
        timeout=60)
    assert r.status_code == 200 and r.content[:4] == b"%PDF", r.text[:200]


def test_http_68_get_contracts_verewigt_keine_heutigen_bilder(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    dbx = _db()
    alt_id = f"alt-{SUF}"
    urls = ["https://img.e2etest/1.jpg", "https://img.e2etest/2.jpg"]
    dbx.vehicles.update_one({"id": welt["vid"], "dealer_id": welt["me"]["dealer_id"]},
                            {"$set": {"data.images": urls}})
    dbx.generated_pdfs.insert_one({
        "id": alt_id, "contract_no": "KV-ALT", "dealer_id": welt["me"]["dealer_id"],
        "user_id": welt["me"]["id"], "vehicle_id": welt["vid"], "make": "Mock",
        "model": "Alt", "seller_name": "Alt V", "send_status": [], "status": "erstellt",
        "created_at": (JETZT - timedelta(days=30)).isoformat()})
    try:
        for _ in range(2):
            r = requests.get(f"{API}/contracts", headers=welt["h"], timeout=30)
            assert r.status_code == 200, r.text[:200]
            alt = next(i for i in r.json() if i["id"] == alt_id)
            assert alt["vehicle_image_urls"] == urls
            assert alt.get("bilder_nachgetragen") is True
            doc = dbx.generated_pdfs.find_one({"id": alt_id})
            assert "vehicle_image_urls" not in doc, "GET hat den Vertrag beschrieben"
    finally:
        dbx.generated_pdfs.delete_many({"id": alt_id})


def test_http_73_liste_bis_2000_und_kopfzeile(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    dbx = _db()
    marker = f"masse-{SUF}"
    docs = [{"id": f"{marker}-{i}", "contract_no": f"KV-M{i}",
             "dealer_id": welt["me"]["dealer_id"], "user_id": welt["me"]["id"],
             "vehicle_id": welt["vid"], "make": "Masse", "model": marker,
             "seller_name": "M", "send_status": [], "status": "erstellt",
             "created_at": (JETZT - timedelta(seconds=i)).isoformat()}
            for i in range(2001)]
    dbx.generated_pdfs.insert_many(docs)
    try:
        r = requests.get(f"{API}/contracts", headers=welt["h"], timeout=60)
        assert r.status_code == 200, r.text[:200]
        assert isinstance(r.json(), list) and len(r.json()) == 2000
        assert r.headers.get("X-Truncated") == "1"
        r = requests.get(f"{API}/contracts", headers=welt["h"],
                         params={"q": "R V"}, timeout=60)
        assert r.status_code == 200 and r.headers.get("X-Truncated") == "0"
        assert all(i["seller_name"] == "R V" for i in r.json())
    finally:
        dbx.generated_pdfs.delete_many({"model": marker})


def test_http_118_send_status_bleibt_bei_200_eintraegen(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    from routes.contracts import SEND_STATUS_MAX
    n = SEND_STATUS_MAX + 10
    keys = []
    for i in range(n):
        k = f"r14-{i}-{uuid.uuid4().hex[:6]}"
        keys.append(k)
        r = requests.post(f"{API}/contracts/{welt['cid']}/send", headers=welt["h"], json={
            "channel": "whatsapp", "recipient": "+491700000014",
            "message": f"Vertrag {i}", "idempotency_key": k}, timeout=30)
        assert r.status_code == 200, r.text[:200]
        assert not r.json().get("bereits_gesendet"), (i, r.json())
    st = _db().generated_pdfs.find_one({"id": welt["cid"]})["send_status"]
    assert len(st) == SEND_STATUS_MAX, len(st)
    assert st[-1]["idempotency_key"] == keys[-1]
    assert st[-1]["zustellung"] == "chat_geoeffnet"
    assert all(e.get("zustellung") not in ("laeuft", "unklar") for e in st)
    # Doppelklick mit gleichem (noch vorhandenem) Schluessel: bereits_gesendet
    r = requests.post(f"{API}/contracts/{welt['cid']}/send", headers=welt["h"], json={
        "channel": "whatsapp", "recipient": "+491700000014",
        "message": f"Vertrag {n - 1}", "idempotency_key": keys[-1]}, timeout=30)
    assert r.status_code == 200 and r.json().get("bereits_gesendet") is True, r.text[:200]
    assert len(_db().generated_pdfs.find_one({"id": welt["cid"]})["send_status"]) == SEND_STATUS_MAX


def test_http_118_wiederaufnahme_nach_slice_funktioniert(welt):
    """Haengender Eintrag am Ende der (vollen) Liste: der naechste Klick
    nimmt ihn wieder auf, statt einen zweiten anzulegen."""
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    from routes.contracts import SEND_STATUS_MAX
    alt = f"haengt-{uuid.uuid4().hex[:8]}"
    _db().generated_pdfs.update_one({"id": welt["cid"]}, {"$push": {"send_status": {
        "$each": [{"idempotency_key": alt, "channel": "whatsapp",
                   "recipient": "+491700000015",
                   "sent_at": (JETZT - timedelta(minutes=20)).isoformat(),
                   "zustellung": "laeuft"}], "$slice": -SEND_STATUS_MAX}}})
    r = requests.post(f"{API}/contracts/{welt['cid']}/send", headers=welt["h"], json={
        "channel": "whatsapp", "recipient": "+491700000015",
        "message": "Nochmal", "idempotency_key": f"neu-{uuid.uuid4().hex[:8]}"}, timeout=30)
    assert r.status_code == 200 and not r.json().get("bereits_gesendet"), r.text[:200]
    st = _db().generated_pdfs.find_one({"id": welt["cid"]})["send_status"]
    treffer = [e for e in st if e.get("recipient") == "+491700000015"]
    assert len(treffer) == 1 and treffer[0]["idempotency_key"] == alt, treffer
    assert treffer[0].get("wiederaufgenommen") is True
    assert treffer[0]["zustellung"] == "chat_geoeffnet"
    assert len(st) <= SEND_STATUS_MAX
