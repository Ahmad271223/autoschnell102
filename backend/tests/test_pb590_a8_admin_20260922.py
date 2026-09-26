# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026, Reparaturwelle A8 — Super-Admin-Oberflaeche und
ihre Backend-Routen.

In-Prozess gegen eine Wegwerf-DB (autoschnell_pb590a8_<uuid>), kein Server.
  AD-19  GET /admin/users?q=  sucht Kontonummer/E-Mail/Name/Firma/#Kundennummer (escaped)
  AD-33  GET /admin/drivers?q= sucht Kontonummer/Name/E-Mail/Code/Firma
  AD-28  POST /admin/me/password stellt eine neue Einzel-Sitzung aus (Token)
  DO-22  /admin/betrieb ohne zahlungen_ohne_zugang; Doku als historisch markiert
  RE-09/RE-10  Datenschutz: Loeschfristen der Inseratsfotos, Backup-Aufbewahrung
"""
import asyncio
import inspect
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Response

BACKEND = Path(__file__).resolve().parents[1]
WURZEL = BACKEND.parent
sys.path.insert(0, str(BACKEND))

import auth as AUTHMOD  # noqa: E402
import deps  # noqa: E402
import routes.admin as ADMIN  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
SA = {"id": "pb590a8_sa", "role": "admin", "is_super_admin": True,
      "username": "pb590a8-sa", "dealer_id": ""}


def _iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_pb590a8_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, ADMIN):
        monkeypatch.setattr(mod, "db", db)
    run = loop.run_until_complete
    w = SimpleNamespace(db=db, run=run)
    # Zwei Firmen, je Chef + Sucher; ein Zwischenhaendler ohne Firma.
    run(db.dealers.insert_many([
        {"id": "d_mueller", "user_id": "chef_m", "company_name": "Müller Autohandel",
         "kunden_nr": 10999, "created_at": _iso(days=-100)},
        {"id": "d_schulz", "user_id": "chef_s", "company_name": "Schulz Kfz",
         "kunden_nr": 11000, "created_at": _iso(days=-90)},
    ]))
    run(db.users.insert_many([
        {"id": "chef_m", "role": "dealer", "dealer_id": "d_mueller", "active": True,
         "kontonummer": "10999", "email": "chef@mueller.de", "created_at": _iso(days=-100)},
        {"id": "su_m", "role": "sucher", "dealer_id": "d_mueller", "active": True,
         "kontonummer": "10999-1", "email": "erika@mueller.de", "first_name": "Erika",
         "last_name": "Sucherin", "created_at": _iso(days=-50)},
        {"id": "chef_s", "role": "dealer", "dealer_id": "d_schulz", "active": True,
         "kontonummer": "11000", "created_at": _iso(days=-90)},
        {"id": "su_s", "role": "sucher", "dealer_id": "d_schulz", "active": True,
         "kontonummer": "11000-1", "first_name": "Karl", "created_at": _iso(days=-40)},
        {"id": "k_1", "role": "b2b_buyer", "active": True, "kontonummer": "K7HX2P",
         "contact_name": "Kai Käufer", "email": "kai@handel.de", "created_at": _iso(days=-30)},
    ]))
    try:
        yield w
    finally:
        try:
            run(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def _ids(liste):
    return sorted(u["id"] for u in liste)


def _users(w, **params):
    r = Response()
    erg = w.run(ADMIN.admin_list_users(r, None, **params))
    return erg, r.headers.get("x-truncated")


# ======================================================= AD-19
def test_ad19_suche_kontonummer_email_name(welt):
    w = welt
    assert _ids(_users(w, q="10999-1")[0]) == ["su_m"]
    assert _ids(_users(w, q="erika@mueller")[0]) == ["su_m"]
    assert _ids(_users(w, q="sucherin")[0]) == ["su_m"], "Nachname, Gross-/Kleinschreibung egal"
    assert _ids(_users(w, q="Kai Käufer")[0]) == ["k_1"], "Kontaktname des Zwischenhaendlers"
    assert _ids(_users(w, q="K7HX")[0]) == ["k_1"]


def test_ad19_suche_ueber_firma_und_kundennummer(welt):
    w = welt
    assert _ids(_users(w, q="müller")[0]) == ["chef_m", "su_m"], "Firmenname trifft alle Konten der Firma"
    assert _ids(_users(w, q="#10999")[0]) == ["chef_m", "su_m"], "#Kundennummer wie in der Nutzerliste"
    assert _ids(_users(w, q="11000")[0]) == ["chef_s", "su_s"], "Kundennummer/Kontonummer-Praefix"
    assert _ids(_users(w, q="Kfz")[0]) == ["chef_s", "su_s"]


def test_ad19_suchtext_ist_literal_und_begrenzt(welt):
    w = welt
    # Regex-Sonderzeichen sind Literale (re.escape) — ".*" oder "(" treffen nichts, werfen nichts.
    assert _users(w, q=".*")[0] == []
    assert _users(w, q="(")[0] == []
    assert _users(w, q="10999-1|11000")[0] == []
    # Leer/Blank: ganze Liste wie ohne q
    erg, kopf = _users(w, q="   ")
    assert len(erg) == 5 and kopf is None
    # Ueberlange Eingabe wird gekappt, nicht abgelehnt
    assert _users(w, q="x" * 5000)[0] == []
    # Riesige Ziffernfolge sprengt kein int64 (kunden_nr-Abgleich)
    assert _users(w, q="#" + "9" * 40)[0] == []


def test_ad19_suche_mit_seite_und_kuerzung(welt):
    w = welt
    erg, kopf = _users(w, q="mueller", page=1, limit=1)
    assert len(erg) == 1 and kopf == "1", "Treffer ueber dem Limit: X-Truncated"
    erg2, kopf2 = _users(w, q="mueller", page=2, limit=1)
    assert len(erg2) == 1 and kopf2 is None
    assert {erg[0]["id"], erg2[0]["id"]} == {"chef_m", "su_m"}


def test_ad19_ohne_q_wie_bisher(welt):
    w = welt
    erg, kopf = _users(w)
    assert len(erg) == 5 and kopf is None
    sig = inspect.signature(ADMIN.admin_list_users)
    assert list(sig.parameters)[:4] == ["response", "_", "page", "limit"], \
        "bestehende Aufrufer (positional) bleiben gueltig"
    assert sig.parameters["q"].default is None


# ======================================================= AD-33
def _drivers(w, **params):
    r = Response()
    return w.run(ADMIN.admin_list_drivers(r, **params)), r.headers.get("x-truncated")


def test_ad33_fahrersuche(welt):
    w = welt
    w.run(w.db.driver_accounts.insert_many([
        {"id": "f1", "display_name": "Anna Fahrerin", "kontonummer": "FD-AAAA2222",
         "driver_code": "FD-AAAA2222", "email": "anna@f.de", "active": True, "created_at": _iso(days=-10)},
        {"id": "f2", "display_name": "Bernd Bote", "kontonummer": "FD-BBBB3333",
         "driver_code": "FD-BBBB3333", "active": True, "created_at": _iso(days=-5)},
    ]))
    w.run(w.db.dealer_drivers.insert_one({"dealer_id": "d_mueller", "driver_account_id": "f2"}))
    assert _ids(_drivers(w, q="AAAA")[0]) == ["f1"]
    assert _ids(_drivers(w, q="bote")[0]) == ["f2"]
    assert _ids(_drivers(w, q="anna@f")[0]) == ["f1"]
    assert _ids(_drivers(w, q="Müller")[0]) == ["f2"], "verknuepfte Firma"
    assert _ids(_drivers(w, q="#10999")[0]) == ["f2"]
    assert _drivers(w, q=".*")[0] == []
    alle, kopf = _drivers(w)
    assert _ids(alle) == ["f1", "f2"] and kopf is None
    assert alle[0]["firmen"] == ["Müller Autohandel"], "Anreicherung unveraendert"


# ======================================================= AD-28
def test_ad28_passwortwechsel_stellt_neue_einzel_sitzung_aus(welt):
    w = welt
    w.run(w.db.users.insert_one({**SA, "active": True,
                                 "password_hash": AUTHMOD.hash_password("Richtig-12345"),
                                 "current_session_id": "sid-alt",
                                 "current_session_seit": _iso(days=-3)}))
    erg = w.run(ADMIN.admin_self_password(
        ADMIN.AdminSelfPasswordIn(current_password="Richtig-12345", new_password="Neues-Passwort-9"),
        admin=SA))
    assert erg["ok"] is True and erg.get("token"), "Token der neuen Sitzung in der Antwort"
    payload = AUTHMOD.decode_token(erg["token"])
    u = w.run(w.db.users.find_one({"id": SA["id"]}))
    assert payload["sub"] == SA["id"]
    assert payload["sid"] == u["current_session_id"], "genau DIESE Sitzung gilt jetzt"
    assert u["current_session_id"] != "sid-alt", "die alte Sitzung (andere Geraete) ist ungueltig"
    assert u["current_session_seit"] > _iso(minutes=-1)
    assert w.run(AUTHMOD.verify_password_async("Neues-Passwort-9", u["password_hash"]))
    # Einzel-Sitzung: ein Geraet mit der alten sid bekommt die Meldung "erneut
    # angemeldet" (deps.current_user vergleicht sid mit current_session_id).
    assert "nur eine Anmeldung je Konto" in deps.sitzung_beendet_grund(u)


def test_ad28_falsches_passwort_bleibt_400_ohne_neue_sitzung(welt):
    from fastapi import HTTPException
    w = welt
    w.run(w.db.users.insert_one({**SA, "active": True,
                                 "password_hash": AUTHMOD.hash_password("Richtig-12345"),
                                 "current_session_id": "sid-alt"}))
    with pytest.raises(HTTPException) as e:
        w.run(ADMIN.admin_self_password(
            ADMIN.AdminSelfPasswordIn(current_password="falsch", new_password="Neues-Passwort-9"),
            admin=SA))
    assert e.value.status_code == 400
    assert w.run(w.db.users.find_one({"id": SA["id"]}))["current_session_id"] == "sid-alt"


# ======================================================= DO-22
def test_do22_zahlungen_ohne_zugang_entfernt():
    q = inspect.getsource(ADMIN.admin_betrieb)
    assert "zahlungen_ohne_zugang" not in q
    assert "payment_transactions" not in q
    betrieb = (WURZEL / "frontend" / "src" / "pages" / "admin_v2" / "Betrieb.jsx").read_text(encoding="utf-8")
    # Der Kopfkommentar darf den Namen der entfernten Kachel nennen — die
    # Kachel selbst (label=...) und der Zaehler duerfen nicht mehr vorkommen.
    assert 'label="Zahlungen ohne Zugang"' not in betrieb
    assert "zahlungen_ohne_zugang" not in betrieb
    assert "Bezahlt-ohne-Zugang" not in betrieb
    # Doku: als historisch gekennzeichnet, nicht geloescht (Nachvollziehbarkeit)
    golive = (WURZEL / "GO-LIVE-CHECKLISTE.md").read_text(encoding="utf-8")
    zeile = next(z for z in golive.splitlines() if "| 8 |" in z and "Bezahlt ohne Zugang" in z)
    assert "historisch" in zeile and "14.09.2026" in zeile
    deploy = (WURZEL / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "historisch:* „bezahlt" in deploy or "historisch:* „bezahlt" in deploy
    env = (WURZEL / ".env.example").read_text(encoding="utf-8")
    assert '"bezahlt ohne Zugang" ist historisch' in env


# ======================================================= RE-09 / RE-10
def test_re09_re10_datenschutz_nennt_die_echten_fristen():
    import cleanup_service as CS
    text = (WURZEL / "frontend" / "src" / "pages" / "legal" / "Datenschutz.jsx").read_text(encoding="utf-8")
    regeln = dict(CS.CLEANUP_RULES)
    assert regeln == {"abgeholt": 7, "nicht abgeholt": 14, "erledigt": 7, "storniert": 14}, \
        "aendert sich das, muss der Datenschutztext nachgezogen werden"
    satz = " ".join(text.split())
    assert "bei abgeholten oder erledigten Terminen 7 Tage, bei nicht abgeholten oder stornierten Terminen 14 Tage" in satz
    assert "7 Tage nach der Abholung (bei nicht abgeholten" not in satz
    # RE-10: Zahlen aus backup_mongo.py (KEEP, OFFSITE_KEEP_DEFAULT) und dem Papierkorb
    backup = (BACKEND / "scripts" / "backup_mongo.py").read_text(encoding="utf-8")
    assert "\nKEEP = 14\n" in backup and "OFFSITE_KEEP_DEFAULT = 14" in backup
    compose = (WURZEL / "docker-compose.yml").read_text(encoding="utf-8")
    assert "BACKUP_DATEIEN_AUFBEWAHRUNG_TAGE:-30" in compose
    assert "auf unseren Servern die letzten 14 Sicherungen" in satz
    assert "die jüngste vollständige bleibt stets erhalten" in satz
    assert "außer Haus die letzten 14 Sicherungen" in satz
    assert "gelöschte Dateien bleiben bis zu 30 Tage in der Dateisicherung" in satz
    assert "auf unseren Servern 14 Tage" not in satz
    # keine Anbieter-/Kontaktdaten eingebaut (Entscheidung Ahmad)
    for angabe in ("Fakih", "Baldurstraße", "30657", "info@auto-schnellkauf.de"):
        assert angabe not in text
