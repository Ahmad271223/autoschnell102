# -*- coding: utf-8 -*-
"""Rollenpruefung 22.09.2026, Welle 2 — Uebergaben an Team admin_abo.

In-Prozess gegen eine Wegwerf-DB (autoschnell_rpaa2_<uuid>), kein Server.
  RP-511         Einladung in der Zugangsanfrage: speichern, anzeigen, beim
                 Anlegen des Kaeuferkontos einloesen
  RP-507 (3)     Kostenlos-Modus bucht keine 20 EUR "bezahlt"
  RP-200/RP-351  Vertrags-PDF des Betreibers: Dateiname mit Sonderzeichen
  RP-543         403-Text beim Betreiber-Login ohne Zwei-Faktor
  RP-394         Stand des Aufraeumlaufs auf /admin/betrieb
  RP-245         Schreibpause im manuellen Aufraeumlauf -> 503 statt 500
  RP-005/104/425 active_profile nur ueber PUT /dealer/active-profile
  RP-138         Logo im PUT /dealer/settings nur entfernen oder unveraendert
"""
import asyncio
import base64
import inspect
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deps  # noqa: E402
import routes.admin as ADMIN  # noqa: E402
import routes.auth as AUTH  # noqa: E402
import routes.dealer as DEALER  # noqa: E402
import routes.marketplace as MARKT  # noqa: E402
import routes.team as TEAM  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
SA = {"id": "rpaa2_sa", "role": "admin", "is_super_admin": True,
      "username": "rpaa2-sa", "dealer_id": ""}
PW = "Sicher-Passwort-2026!"


def _iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_rpaa2_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, ADMIN, TEAM, DEALER, MARKT, AUTH):
        monkeypatch.setattr(mod, "db", db)

    async def _kein_rs():
        return False
    monkeypatch.setattr(deps, "ist_replica_set", _kein_rs)
    run = loop.run_until_complete
    run(db.manual_payments.create_index("vorgang_id", unique=True, sparse=True))
    run(db.job_locks.create_index("name", unique=True))
    s = uuid.uuid4().hex[:8]
    w = SimpleNamespace(db=db, run=run, s=s, dealer_id=f"d_{s}",
                        chef_id=f"chef_{s}", sucher_id=f"su_{s}")
    run(db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef_id,
                               "company_name": "Autohaus Einlader", "kunden_nr": 10998,
                               "active_profile": "export",
                               "logo_url": f"/api/files/logo/{w.dealer_id}/alt.png",
                               "created_at": _iso(days=-100)}))
    run(db.users.insert_many([
        {"id": w.chef_id, "role": "dealer", "dealer_id": w.dealer_id, "active": True,
         "kontonummer": "10998", "created_at": _iso(days=-100)},
        {"id": w.sucher_id, "role": "sucher", "dealer_id": w.dealer_id, "active": True,
         "kontonummer": "10998-1", "created_at": _iso(days=-50),
         "settings_override": {"active_profile": "inland", "phone": "0511 1"}},
    ]))
    try:
        yield w
    finally:
        try:
            run(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def _fehler(w, coro):
    try:
        w.run(coro)
    except HTTPException as e:
        return e.status_code, str(e.detail)
    return None, None


def _einladung(w, **felder):
    doc = {"id": f"inv_{uuid.uuid4().hex[:8]}", "dealer_id": w.dealer_id,
           "token": f"tok_{uuid.uuid4().hex}", "expires_at": _iso(days=5),
           "max_uses": 1, "used_count": 0, "used_by": [], "created_at": _iso()}
    doc.update(felder)
    w.run(w.db.dealer_invites.insert_one(dict(doc)))
    return doc


# ======================================================= RP-511
class _Limiter:
    async def check(self, *_a, **_k):
        return True


async def _still(*_a, **_k):
    return None


def _anfrage_stellen(w, monkeypatch, **felder):
    monkeypatch.setattr(AUTH, "register_limiter", _Limiter())
    monkeypatch.setattr(AUTH, "client_ip", lambda _r: "10.0.0.1")
    monkeypatch.setattr(AUTH, "log_activity_sicher", _still)
    daten = {"art": "kaeufer", "company_name": f"Partner {w.s}", "contact_person": "Pia Partner",
             "email": f"partner_{w.s}@e2etest-mail.de", "gewerblich_bestaetigt": True}
    daten.update(felder)
    w.run(AUTH.zugang_anfrage(AUTH.ZugangsAnfrageIn(**daten), request=None))
    return w.run(w.db.plan_requests.find_one({"contact_email": daten["email"]}, {"_id": 0}))


def test_rp511_anfrage_speichert_die_einladung(welt, monkeypatch):
    inv = _einladung(welt)
    doc = _anfrage_stellen(welt, monkeypatch, invite_token=f"  {inv['token']} ")
    assert doc["invite_token"] == inv["token"]


def test_rp511_nur_fuer_kaeufer_und_kaputtes_token_kippt_nichts(welt, monkeypatch):
    doc = _anfrage_stellen(welt, monkeypatch, art="firma", invite_token="abcdefgh12345")
    assert "invite_token" not in doc
    doc = _anfrage_stellen(welt, monkeypatch, email=f"b_{welt.s}@e2etest-mail.de",
                           invite_token="<script>")
    assert doc and "invite_token" not in doc
    assert AUTH.ZugangsAnfrageIn(art="kaeufer", company_name="AB", contact_person="CD",
                                 email="x@e2etest-mail.de", invite_token=None).invite_token == ""


def test_rp511_liste_nennt_die_einladung_nie_das_token(welt, monkeypatch):
    inv = _einladung(welt)
    _anfrage_stellen(welt, monkeypatch, invite_token=inv["token"])
    items = welt.run(ADMIN.admin_plan_requests(Response(), status="offen", type="zugang",
                                               limit=50, seite=1, _=SA))
    assert len(items) == 1
    assert "invite_token" not in items[0]
    assert items[0]["einladung"] == {"firma": "Autohaus Einlader", "gueltig": True}


def test_rp511_anlegen_loest_die_einladung_ein(welt, monkeypatch):
    inv = _einladung(welt)
    anfrage = _anfrage_stellen(welt, monkeypatch, invite_token=inv["token"])
    body = ADMIN.AdminKaeuferIn(company_name=anfrage["company_name"], contact_name="Pia Partner",
                                email=anfrage["contact_email"], password=PW,
                                b2b_nachweis=True, anfrage_id=anfrage["id"])
    erg = welt.run(ADMIN.admin_create_buyer(body, admin=SA))
    assert erg["einladung"] == {"eingeloest": True, "firma": "Autohaus Einlader"}
    mitglied = welt.run(welt.db.network_members.find_one(
        {"dealer_id": welt.dealer_id, "buyer_user_id": erg["user_id"]}))
    assert mitglied and mitglied["via_invite_id"] == inv["id"]
    assert welt.run(welt.db.dealer_invites.find_one({"id": inv["id"]}))["used_count"] == 1
    assert welt.run(welt.db.plan_requests.find_one({"id": anfrage["id"]}))["status"] == "erledigt"


def test_rp511_verbrauchte_einladung_kippt_die_anlage_nicht(welt, monkeypatch):
    inv = _einladung(welt, used_count=1, used_by=["jemand"])
    anfrage = _anfrage_stellen(welt, monkeypatch, invite_token=inv["token"])
    body = ADMIN.AdminKaeuferIn(company_name=anfrage["company_name"], contact_name="Pia Partner",
                                email=anfrage["contact_email"], password=PW,
                                b2b_nachweis=True, anfrage_id=anfrage["id"])
    erg = welt.run(ADMIN.admin_create_buyer(body, admin=SA))
    assert erg["ok"] and erg["einladung"] == {"eingeloest": False, "firma": "Autohaus Einlader"}
    assert not welt.run(welt.db.network_members.find_one({"buyer_user_id": erg["user_id"]}))


def test_rp511_ohne_einladung_keine_angabe(welt):
    body = ADMIN.AdminKaeuferIn(company_name="Frei GmbH", contact_name="Fred Frei",
                                password=PW, b2b_nachweis=True)
    erg = welt.run(ADMIN.admin_create_buyer(body, admin=SA))
    assert erg["ok"] and erg["einladung"] is None


# ======================================================= RP-507 (3)
def _kaeufer(w, **felder):
    bid = f"k_{uuid.uuid4().hex[:8]}"
    doc = {"id": bid, "role": "b2b_buyer", "active": True,
           "marketplace_access": {"active": False, "gesperrt": True}}
    doc.update(felder)
    w.run(w.db.users.insert_one(doc))
    return bid


def test_rp507_kostenlos_modus_bucht_keine_zwanzig_euro(welt, monkeypatch):
    monkeypatch.setattr(MARKT, "MARKTPLATZ_KOSTENLOS", True)
    bid = _kaeufer(welt)
    erg = welt.run(ADMIN.admin_set_buyer_access(bid, {"plan": "monthly"}, admin=SA))
    assert erg["zahlungsart"] == "kostenlos"
    z = welt.run(welt.db.manual_payments.find_one({"vorgang_id": erg["vorgang_id"]}))
    assert z["amount"] == 0.0 and z["kostenlos"] is True and z["zahlungsart"] == "kostenlos"
    assert z["grund"]                          # Vermerk, ohne dass der Betreiber einen schreibt


def test_rp507_ausdrueckliche_zahlungsart_gilt_weiter(welt, monkeypatch):
    monkeypatch.setattr(MARKT, "MARKTPLATZ_KOSTENLOS", True)
    bid = _kaeufer(welt)
    erg = welt.run(ADMIN.admin_set_buyer_access(
        bid, {"plan": "monthly", "zahlungsart": "rechnung_bezahlt"}, admin=SA))
    z = welt.run(welt.db.manual_payments.find_one({"vorgang_id": erg["vorgang_id"]}))
    assert z["amount"] == 20.0 and z["zahlungsart"] == "rechnung_bezahlt"


def test_rp507_bezahlmodus_unveraendert(welt, monkeypatch):
    monkeypatch.setattr(MARKT, "MARKTPLATZ_KOSTENLOS", False)
    bid = _kaeufer(welt)
    erg = welt.run(ADMIN.admin_set_buyer_access(bid, {"plan": "monthly"}, admin=SA))
    z = welt.run(welt.db.manual_payments.find_one({"vorgang_id": erg["vorgang_id"]}))
    assert erg["zahlungsart"] == "rechnung_bezahlt" and z["amount"] == 20.0
    code, text = _fehler(welt, ADMIN.admin_set_buyer_access(
        _kaeufer(welt), {"plan": "monthly", "zahlungsart": "kostenlos"}, admin=SA))
    assert code == 400 and "kulanz" in text


# ======================================================= RP-200 / RP-351
def test_rp200_vertrags_pdf_mit_sonderzeichen_im_namen(welt):
    cid = f"c_{welt.s}"
    welt.run(welt.db.generated_pdfs.insert_one({
        "id": cid, "filename": "Kaufvertrag_Škoda_Octavia–RS 🚗.pdf",
        "pdf_b64": base64.b64encode(b"%PDF-1.4 test").decode()}))
    antwort = welt.run(ADMIN.admin_contract_pdf(cid, _=SA))
    kopf = antwort.headers["content-disposition"]
    kopf.encode("latin-1")                      # vorher: UnicodeEncodeError -> 500
    assert kopf.startswith('inline; filename="Kaufvertrag_Skoda_Octavia')
    assert "filename*=UTF-8''" in kopf and "%C5%A0koda" in kopf


# ======================================================= RP-543
def test_rp543_hinweis_nennt_den_vollstaendigen_notfallbefehl():
    t = AUTH.MFA_PFLICHT_HINWEIS
    assert "docker compose exec backend python scripts/mfa_pruefen.py" in t
    assert "--konto <Benutzername> --abschalten --ja" in t.replace("' ausführen", "")
    assert "30 Minuten" in t
    q = inspect.getsource(AUTH)
    assert "raise HTTPException(403, MFA_PFLICHT_HINWEIS)" in q
    assert "(python scripts/mfa_pruefen.py)." not in q


# ======================================================= RP-394
def test_rp394_betriebsseite_zeigt_den_aufraeumlauf(welt):
    alt = _iso(hours=-5)
    welt.run(welt.db.system_reports.insert_one({
        "typ": "aufraeumlauf", "letzter_lauf": _iso(minutes=-10),
        "letzter_vollstaendiger_lauf": alt, "fehlgeschlagen": ["auto_daten_reparieren"],
        "vollstaendig": False}))
    stand = welt.run(ADMIN._aufraeumlauf_stand())
    assert stand["letzter_vollstaendiger_lauf"] == alt
    assert stand["fehlgeschlagen"] == ["auto_daten_reparieren"]
    assert stand["ueberfaellig"] is True and stand["grenze_h"] == 3
    betrieb = welt.run(ADMIN.admin_betrieb(admin=SA))
    assert betrieb["aufraeumlauf"]["fehlgeschlagen"] == ["auto_daten_reparieren"]


def test_rp394_frischer_vollstaendiger_lauf_ist_nicht_ueberfaellig(welt):
    welt.run(welt.db.system_reports.insert_one({
        "typ": "aufraeumlauf", "letzter_lauf": _iso(minutes=-5),
        "letzter_vollstaendiger_lauf": _iso(minutes=-5), "fehlgeschlagen": [],
        "vollstaendig": True}))
    assert welt.run(ADMIN._aufraeumlauf_stand())["ueberfaellig"] is False


def test_rp394_ohne_bericht_nichts_ueberfaellig(welt):
    stand = welt.run(ADMIN._aufraeumlauf_stand())
    assert stand["letzter_lauf"] is None and stand["ueberfaellig"] is False


# ======================================================= RP-245
def test_rp245_schreibpause_waehrend_des_laufs_gibt_503(welt, monkeypatch):
    from cleanup_service import SchreibpauseAktiv
    gesehen = {}

    async def lauf(db, wache=None):
        gesehen["wache"] = wache
        gesehen["aktiv"] = (await db.job_locks.find_one({"name": "cleanup-cycle"}))["lauf_aktiv"]
        raise SchreibpauseAktiv("Schreibpause")
    monkeypatch.setattr(ADMIN, "_cleanup_once", lauf)
    code, text = _fehler(welt, ADMIN.admin_trigger_cleanup(user=SA))
    assert code == 503 and "Schreibpause" in text
    assert gesehen["wache"] is not None and gesehen["aktiv"] is True
    sperre = welt.run(welt.db.job_locks.find_one({"name": "cleanup-cycle"}))
    # Sperre freigegeben (oder zumindest nicht mehr als laufend markiert)
    assert sperre is None or not sperre.get("lauf_aktiv")


# ======================================================= RP-005 / RP-104 / RP-425
def test_rp005_put_settings_uebernimmt_kein_active_profile(welt):
    chef = {"id": welt.chef_id, "role": "dealer", "dealer_id": welt.dealer_id}
    assert "active_profile" not in DEALER._collect_settings_update(
        DEALER.DealerSettingsIn(active_profile="inland"))
    welt.run(DEALER.update_settings(
        DEALER.DealerSettingsIn(active_profile="inland", email_subject="Neu"), user=chef))
    d = welt.run(welt.db.dealers.find_one({"id": welt.dealer_id}))
    assert d["active_profile"] == "export" and d["email_subject"] == "Neu"


def test_rp005_zuruecksetzen_ohne_liste_behaelt_das_eigene_profil(welt):
    sucher = welt.run(welt.db.users.find_one({"id": welt.sucher_id}, {"_id": 0}))
    erg = welt.run(DEALER.settings_zuruecksetzen(DEALER.OverrideZuruecksetzenIn(), user=sucher))
    assert erg["zurueckgesetzt"] == ["phone"]
    ov = welt.run(welt.db.users.find_one({"id": welt.sucher_id}))["settings_override"]
    assert ov == {"active_profile": "inland"}
    # ausdruecklich genannt: dann schon
    sucher = welt.run(welt.db.users.find_one({"id": welt.sucher_id}, {"_id": 0}))
    erg = welt.run(DEALER.settings_zuruecksetzen(
        DEALER.OverrideZuruecksetzenIn(felder=["active_profile"]), user=sucher))
    assert erg["zurueckgesetzt"] == ["active_profile"]


# ======================================================= RP-138
def test_rp138_alter_tab_setzt_das_logo_nicht_zurueck(welt, monkeypatch):
    geraeumt = []

    async def _raeumen(url, dealer_id):
        geraeumt.append(url)
    monkeypatch.setattr(DEALER, "_altes_logo_wegraeumen", _raeumen)
    chef = {"id": welt.chef_id, "role": "dealer", "dealer_id": welt.dealer_id}
    neu = f"/api/files/logo/{welt.dealer_id}/neu.png"
    welt.run(welt.db.dealers.update_one({"id": welt.dealer_id}, {"$set": {"logo_url": neu}}))
    # alter Tab schickt die alte URL mit -> ignoriert, Telefon gespeichert
    erg = welt.run(DEALER.update_settings(DEALER.DealerSettingsIn(profile=DEALER.DealerProfile(
        logo_url=f"/api/files/logo/{welt.dealer_id}/alt.png", phone="030 9")), user=chef))
    assert erg["logo_url"] == neu and erg["phone"] == "030 9"
    # unveraendert: nichts passiert
    welt.run(DEALER.update_settings(DEALER.DealerSettingsIn(
        profile=DEALER.DealerProfile(logo_url=neu)), user=chef))
    assert geraeumt == []
    # "Logo entfernen": leer, und die Datei wird weggeraeumt
    erg = welt.run(DEALER.update_settings(DEALER.DealerSettingsIn(
        profile=DEALER.DealerProfile(logo_url="")), user=chef))
    assert erg["logo_url"] == "" and geraeumt == [neu]


def test_rp138_fremder_host_bleibt_400(welt):
    chef = {"id": welt.chef_id, "role": "dealer", "dealer_id": welt.dealer_id}
    code, _ = _fehler(welt, DEALER.update_settings(DEALER.DealerSettingsIn(
        profile=DEALER.DealerProfile(logo_url="https://tracker.example/x.png")), user=chef))
    assert code == 400
