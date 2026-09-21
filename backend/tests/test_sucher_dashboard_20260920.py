# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026 — "Was im Sucher-Dashboard bricht" (Serverseite).

B2   Gesperrte Firma: Login gelang, /auth/me antwortete 403, die Oberflaeche
     verwarf beides — Anmeldeschleife ohne ein Wort. Jetzt: keine Sitzung fuer
     KEIN Konto der gesperrten Firma (auch nicht fuer ein weiteres dealer-Konto)
     und die 403 traegt `X-Sperre: firma`, damit die Oberflaeche mit Begruendung
     abmeldet (H1).
B6   Ein weiteres dealer-Konto ist fuer alle Firmenwege Sucher — /auth/me sagt
     das jetzt auch, sonst zeigte die Oberflaeche Chef-Knoepfe, die alle 403
     lieferten.
B19  Persoenliche Werte eines Suchers sind sichtbar (eigene_einstellungen) und
     lassen sich auf die Chef-Vorgaben zuruecksetzen.
M37  Die Archivsuche findet die Vertragsnummer vom Ausdruck.
Dazu: die Oberflaeche wertet Kopfzeilen aus — sie muessen per CORS lesbar sein.
"""
import ast
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import konten  # noqa: E402

API = konten.API
SUF = uuid.uuid4().hex[:8]
PW = "Rt7Kq2Mx9-Sicher!42"


def _db():
    return konten._db()


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture(scope="module")
def welt():
    firma = konten.registrieren({
        "company_name": f"Dashboard-Test {SUF}", "contact_person": "Chef Test",
        "email": f"chefdb{SUF}@e2etest-mail.de", "phone": "0211 1234567",
        "password": PW,
    })
    assert firma.status_code == 200, firma.text[:300]
    daten = firma.json()
    kopf = {"Authorization": f"Bearer {daten['token']}"}
    dealer_id = daten["user"]["dealer_id"]
    sucher = konten.sucher_als_chef_anlegen(kopf, json={
        "first_name": "Sucher", "last_name": "Test",
        "email": f"suchdb{SUF}@e2etest-mail.de", "password": PW})
    assert sucher.status_code == 200, sucher.text[:300]
    s = sucher.json()
    dbx = _db()
    dbx.subscriptions.insert_many([
        {"id": str(uuid.uuid4()), "dealer_id": dealer_id,
         "subject_user_id": daten["user"]["id"], "plan": "yearly", "status": "active",
         "created_at": _jetzt(),
         "expires_at": (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()},
        {"id": str(uuid.uuid4()), "dealer_id": dealer_id,
         "subject_user_id": s["sucher_id"], "plan": "monthly", "status": "active",
         "created_at": _jetzt(),
         "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()},
    ])
    w = {"dealer_id": dealer_id, "kopf": kopf, "chef_id": daten["user"]["id"],
         "chef_nr": daten["user"]["kontonummer"],
         "sucher_id": s["sucher_id"], "sucher_nr": s["kontonummer"]}
    yield w
    dbx = _db()
    dbx.users.delete_many({"dealer_id": dealer_id})
    dbx.dealers.delete_many({"id": dealer_id})
    for coll in ("subscriptions", "vehicles", "generated_pdfs", "generated_pdf_versions",
                 "appointments", "kaufvorgaenge", "activity_logs"):
        dbx[coll].delete_many({"dealer_id": dealer_id})


def _sucher_kopf(w):
    r = konten.anmelden(w["sucher_nr"], PW)
    assert r.status_code == 200, r.text[:300]
    return {"Authorization": f"Bearer {r.json()['token']}"}


class _ChefGesperrt:
    """Chef sperren (= Firma gesperrt) und sicher wieder entsperren."""

    def __init__(self, w):
        self.w = w

    def __enter__(self):
        _db().users.update_one({"id": self.w["chef_id"]}, {"$set": {"active": False}})

    def __exit__(self, *a):
        _db().users.update_one({"id": self.w["chef_id"]}, {"$set": {"active": True}})


class _AlsZweitesDealerKonto:
    """Den Sucher voruebergehend zu einem weiteren dealer-Konto machen
    (Altbestand / abgebrochener Chefwechsel)."""

    def __init__(self, w):
        self.w = w

    def __enter__(self):
        _db().users.update_one({"id": self.w["sucher_id"]}, {"$set": {"role": "dealer"}})

    def __exit__(self, *a):
        _db().users.update_one({"id": self.w["sucher_id"]}, {"$set": {"role": "sucher"}})


# ------------------------------------------------------------------ B2
def test_b2_sucher_einer_gesperrten_firma_bekommt_keine_sitzung(welt):
    with _ChefGesperrt(welt):
        r = konten.anmelden(welt["sucher_nr"], PW)
    assert r.status_code == 403, r.text[:300]
    assert "gesperrt" in r.json()["detail"]
    assert r.headers.get("X-Sperre") == "firma"


def test_b2_zweites_dealer_konto_bekommt_keine_sitzung(welt):
    """Vorher pruefte der Login nur role == "sucher" — dieses Konto bekam ein
    Token und lief danach in die Anmeldeschleife."""
    with _AlsZweitesDealerKonto(welt), _ChefGesperrt(welt):
        r = konten.anmelden(welt["sucher_nr"], PW)
    assert r.status_code == 403, r.text[:300]
    assert r.headers.get("X-Sperre") == "firma"


def test_b2_laufende_sitzung_bekommt_die_sperr_kopfzeile(welt):
    kopf = _sucher_kopf(welt)
    with _ChefGesperrt(welt):
        r = requests.get(f"{API}/auth/me", headers=kopf, timeout=30)
        r2 = requests.get(f"{API}/appointments", headers=kopf, timeout=30)
    assert r.status_code == 403 and r.headers.get("X-Sperre") == "firma", r.text[:200]
    assert r2.status_code == 403 and r2.headers.get("X-Sperre") == "firma", r2.text[:200]
    # Nach dem Entsperren geht es mit demselben Token weiter.
    assert requests.get(f"{API}/auth/me", headers=kopf, timeout=30).status_code == 200


def test_b2_normale_403_traegt_keine_sperr_kopfzeile(welt):
    """Nur die Firmensperre meldet ab — ein gewoehnliches 'darfst du nicht'
    (hier: Logo nur fuer den Chef) darf das nicht ausloesen."""
    kopf = _sucher_kopf(welt)
    r = requests.post(f"{API}/dealer/logo", headers=kopf, timeout=30,
                      json={"logo_b64": "data:image/png;base64,iVBORw0KGgo="})
    assert r.status_code == 403, r.text[:200]
    assert r.headers.get("X-Sperre") is None


# ------------------------------------------------------------------ B6
def test_b6_auth_me_zeigt_zweites_dealer_konto_als_sucher(welt):
    with _AlsZweitesDealerKonto(welt):
        r = konten.anmelden(welt["sucher_nr"], PW)
        assert r.status_code == 200, r.text[:300]
        kopf = {"Authorization": f"Bearer {r.json()['token']}"}
        me = requests.get(f"{API}/auth/me", headers=kopf, timeout=30)
    assert me.status_code == 200, me.text[:300]
    assert me.json()["user"]["role"] == "sucher"
    assert me.json()["user"].get("kein_haupt_chef") is True


def test_b6_der_echte_chef_bleibt_chef(welt):
    me = requests.get(f"{API}/auth/me", headers=welt["kopf"], timeout=30)
    assert me.status_code == 200, me.text[:300]
    assert me.json()["user"]["role"] == "dealer"
    assert not me.json()["user"].get("kein_haupt_chef")


# ------------------------------------------------------------------ B19
def test_b19_eigene_werte_sichtbar_und_zuruecksetzbar(welt):
    kopf = _sucher_kopf(welt)
    r = requests.put(f"{API}/dealer/settings", headers=kopf, timeout=30,
                     json={"email_template": f"Nur fuer mich {SUF}"})
    assert r.status_code == 200, r.text[:300]
    me = requests.get(f"{API}/auth/me", headers=kopf, timeout=30).json()
    assert "email_template" in me["dealer"]["eigene_einstellungen"]
    # Nur das gesendete Feld ist persoenlich — nichts anderes eingefroren.
    assert me["dealer"]["eigene_einstellungen"] == ["email_template"]

    z = requests.post(f"{API}/dealer/settings/zuruecksetzen", headers=kopf, timeout=30, json={})
    assert z.status_code == 200, z.text[:300]
    assert z.json()["zurueckgesetzt"] == ["email_template"]
    me = requests.get(f"{API}/auth/me", headers=kopf, timeout=30).json()
    assert me["dealer"]["eigene_einstellungen"] == []
    chef = requests.get(f"{API}/auth/me", headers=welt["kopf"], timeout=30).json()
    assert me["dealer"].get("email_template") == chef["dealer"].get("email_template")


def test_b19_einzelne_felder_zuruecksetzen(welt):
    kopf = _sucher_kopf(welt)
    requests.put(f"{API}/dealer/settings", headers=kopf, timeout=30,
                 json={"email_subject": "A", "whatsapp_template": "B"})
    z = requests.post(f"{API}/dealer/settings/zuruecksetzen", headers=kopf, timeout=30,
                      json={"felder": ["email_subject"]})
    assert z.status_code == 200, z.text[:300]
    me = requests.get(f"{API}/auth/me", headers=kopf, timeout=30).json()
    assert me["dealer"]["eigene_einstellungen"] == ["whatsapp_template"]
    requests.post(f"{API}/dealer/settings/zuruecksetzen", headers=kopf, timeout=30, json={})


def test_b19_unbekanntes_feld_und_chef_werden_abgelehnt(welt):
    kopf = _sucher_kopf(welt)
    r = requests.post(f"{API}/dealer/settings/zuruecksetzen", headers=kopf, timeout=30,
                      json={"felder": ["logo_url"]})
    assert r.status_code == 400, r.text[:200]
    r = requests.post(f"{API}/dealer/settings/zuruecksetzen", headers=welt["kopf"],
                      timeout=30, json={})
    assert r.status_code == 400, r.text[:200]


# ------------------------------------------------------------------ M37
def test_m37_archivsuche_findet_die_vertragsnummer(welt):
    v = requests.post(f"{API}/vehicles/manual", headers=welt["kopf"], timeout=60, json={
        "make_label": "Audi", "model_label": "A4", "first_registration": "03/2019",
        "mileage": 85000, "purchase_price": 12000})
    assert v.status_code == 200, v.text[:300]
    r = requests.post(f"{API}/contracts", headers=welt["kopf"], timeout=180, json={
        "vehicle_id": v.json()["id"], "seller_name": "Erika Muster",
        "seller_address": "Weg 1", "seller_zip": "40210", "seller_city": "Düsseldorf",
        "purchase_price": 11900, "payment_method": "bar"})
    assert r.status_code == 200, r.text[:300]
    nr = r.json().get("contract_no")
    assert nr, r.json()
    treffer = requests.get(f"{API}/contracts", headers=welt["kopf"], timeout=30,
                           params={"q": nr}).json()
    assert any(t.get("contract_no") == nr for t in treffer), treffer[:3]
    # Teilstueck vom Ausdruck (ohne Datum) genuegt auch
    teil = nr.split("-")[-1]
    treffer = requests.get(f"{API}/contracts", headers=welt["kopf"], timeout=30,
                           params={"q": teil}).json()
    assert any(t.get("contract_no") == nr for t in treffer)


# ------------------------------------------------------------------ Folge-Mails
def _vertrag(welt, kopf=None):
    v = requests.post(f"{API}/vehicles/manual", headers=welt["kopf"], timeout=60, json={
        "make_label": "Skoda", "model_label": "Octavia", "first_registration": "05/2018",
        "mileage": 120000, "purchase_price": 9000})
    assert v.status_code == 200, v.text[:300]
    r = requests.post(f"{API}/contracts", headers=kopf or welt["kopf"], timeout=180, json={
        "vehicle_id": v.json()["id"], "seller_name": "Hans Beispiel",
        "seller_email": "verkaeufer@e2etest-mail.de",
        "seller_address": "Allee 3", "seller_zip": "50667", "seller_city": "Köln",
        "purchase_price": 8900, "payment_method": "bar", "pickup_date": "2026-10-05"})
    assert r.status_code == 200, r.text[:300]
    return r.json()


def test_folgemail_nur_zum_kopieren_nie_versendet(welt):
    """Wunsch Ahmad 21.09.2026: Hinweis nach Kaufabschluss und Bahnverbindung
    verschickt die App NICHT — der Sucher kopiert sie. Die Vorschau liefert
    den fertigen Text mit Namen und Daten; der alte Versandweg antwortet 410
    und schreibt nichts in den Vertrag."""
    c = _vertrag(welt)
    for art in ("nach_kauf", "nach_kauf_whatsapp", "bahn", "korrektur"):
        vorschau = requests.get(f"{API}/contracts/{c['id']}/folge-mail/{art}",
                                headers=welt["kopf"], timeout=30)
        assert vorschau.status_code == 200, (art, vorschau.text[:300])
        d = vorschau.json()
        assert "{" not in d["text"], (art, "Platzhalter nicht ersetzt")
        assert "Hans Beispiel" in d["text"], art
        assert bool(d["betreff"]) == (art != "nach_kauf_whatsapp"), (art, d["betreff"])

    for art in ("nach_kauf", "bahn", "korrektur"):
        r = requests.post(f"{API}/contracts/{c['id']}/folge-mail", headers=welt["kopf"],
                          timeout=60, json={"art": art, "recipient": "verkaeufer@e2etest-mail.de",
                                            "idempotency_key": f"folgetest{SUF}{art}"})
        assert r.status_code == 410, (art, r.status_code, r.text[:300])
        assert "kopieren" in r.json().get("detail", ""), r.text[:300]
    doc = _db().generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "send_status": 1})
    assert not [e for e in doc.get("send_status") or [] if e.get("art")], (
        "eine Folge-Mail wurde am Vertrag als versendet vermerkt")


def test_folgemail_ohne_anmeldung_abgelehnt(welt):
    c = _vertrag(welt)
    r = requests.post(f"{API}/contracts/{c['id']}/folge-mail", timeout=30,
                      json={"art": "bahn", "recipient": "a@b.de"})
    assert r.status_code == 401, r.text[:200]


# ------------------------------------------------------------------ CORS
def test_kopfzeilen_sind_fuer_die_oberflaeche_lesbar():
    baum = ast.parse((BACKEND / "server.py").read_text(encoding="utf-8"))
    gefunden = set()
    for k in ast.walk(baum):
        if isinstance(k, ast.keyword) and k.arg == "expose_headers":
            gefunden |= {e.value for e in k.value.elts}
    for kopf in ("X-Sperre", "X-Wiederholen", "X-Truncated", "Retry-After"):
        assert kopf in gefunden, f"{kopf} fehlt in expose_headers"
