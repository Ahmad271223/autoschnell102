# -*- coding: utf-8 -*-
"""Rollenpruefung 22.09.2026, Welle 2 — Team Fahrer-App (routes/drivers.py),
Uebergaben anderer Teams:

  RP-546          gleitende Fahrer-Sitzung (X-Neues-Token, hoechstens 30 Tage)
  RP-557          Fahrer-Anmeldung mit "bekanntem Geraet"
  RP-114/015/265  "nicht abgeholt" an einem Termin ohne eigenen Vorgang bewegt
                  das gemeinsame Fahrzeug eines Kollegen nicht mehr

In-Prozess gegen DB_NAME (welt-Fixture aus test_golive_20260913_abschluss).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi import HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
# Module am Dateianfang laden — nie erstmals in der Test-Schleife (Loop-Bindung).
import auth as AUTHMOD  # noqa: E402
import beweis_service  # noqa: E402,F401
import rate_limiter as RL  # noqa: E402
import routes.drivers as D  # noqa: E402
import routes.protocols as P  # noqa: E402,F401
from test_golive_20260913_abschluss import (  # noqa: E402,F401  (welt = Fixture)
    _abholung, _doc, _jetzt, welt)


def _token(sub, sid, *, exp, seit=None, role="driver_account"):
    payload = {"sub": sub, "sid": sid, "role": role, "exp": exp}
    if seit is not None:
        payload["seit"] = int(seit)
    return jwt.encode(payload, AUTHMOD.JWT_SECRET, algorithm=AUTHMOD.JWT_ALG)


# ============================================================ RP-546
def test_rp546_fahrer_token_wird_kurz_vor_ablauf_erneuert():
    jetzt = datetime.now(timezone.utc).timestamp()
    frisch = AUTHMOD.decode_token(D.create_driver_token("f1", "sid1"))
    assert frisch["role"] == "driver_account" and isinstance(frisch["seit"], int)
    assert D.fahrer_token_erneuern(frisch) is None, "frisches Token bleibt"
    bald = {"sub": "f1", "sid": "sid1", "role": "driver_account",
            "exp": jetzt + 3600, "seit": jetzt - 6 * 86400}
    p = AUTHMOD.decode_token(D.fahrer_token_erneuern(bald))
    assert p["sub"] == "f1" and p["sid"] == "sid1", "dieselbe Einzel-Sitzung"
    assert p["role"] == "driver_account"
    assert p["exp"] > jetzt + 6 * 86400 and p["seit"] == int(bald["seit"])
    zu_alt = dict(bald, seit=jetzt - (AUTHMOD.SITZUNG_MAX_TAGE + 1) * 86400)
    assert D.fahrer_token_erneuern(zu_alt) is None, "hoechstens SITZUNG_MAX_TAGE"
    assert D.fahrer_token_erneuern(dict(bald, role=None)) is None, "nur Fahrer-Token"
    knapp = dict(bald, seit=jetzt - (AUTHMOD.SITZUNG_MAX_TAGE - 3) * 86400)
    p2 = AUTHMOD.decode_token(D.fahrer_token_erneuern(knapp))
    assert p2["exp"] <= knapp["seit"] + AUTHMOD.SITZUNG_MAX_TAGE * 86400 + 1
    # Alt-Token ohne "seit": Beginn = Ausstellung (exp - 7 Tage)
    alt = {k: v for k, v in bald.items() if k != "seit"}
    p3 = AUTHMOD.decode_token(D.fahrer_token_erneuern(alt))
    assert p3["seit"] == int(alt["exp"] - D.FAHRER_TOKEN_TAGE * 86400)


def test_rp546_current_driver_liefert_das_neue_token_mit(welt):
    w = welt
    sid = f"sid-{w.s}"
    w.run(w.db.driver_accounts.update_one({"id": w.driver["id"]},
                                          {"$set": {"current_session_id": sid}}))
    jetzt = datetime.now(timezone.utc)
    tok = _token(w.driver["id"], sid, exp=jetzt + timedelta(hours=5),
                 seit=(jetzt - timedelta(days=6)).timestamp())
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=tok)
    resp = Response()
    fahrer = w.run(D.current_driver(None, None, creds, resp))
    assert fahrer["id"] == w.driver["id"]
    neu = resp.headers.get(AUTHMOD.NEUES_TOKEN_KOPF)
    assert neu, "Kopfzeile fehlt"
    p = AUTHMOD.decode_token(neu)
    assert p["sid"] == sid and p["role"] == "driver_account"
    # Das neue Token ist gueltig (dieselbe Sitzung) ...
    resp2 = Response()
    w.run(D.current_driver(None, None, HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=neu), resp2))
    assert AUTHMOD.NEUES_TOKEN_KOPF not in resp2.headers, "frisches Token: keine weitere Kopfzeile"
    # ... ohne Response (direkter Aufruf) laeuft alles wie bisher
    assert w.run(D.current_driver(None, None, creds))["id"] == w.driver["id"]
    # Abmelden beendet auch das verlaengerte Token sofort.
    w.run(w.db.driver_accounts.update_one({"id": w.driver["id"]},
                                          {"$set": {"current_session_id": None}}))
    with pytest.raises(HTTPException) as e:
        w.run(D.current_driver(None, None, HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=neu), Response()))
    assert e.value.status_code == 401


# ============================================================ RP-557
def _anfrage(ip):
    from starlette.requests import Request
    return Request({"type": "http", "method": "POST", "path": "/api/driver/login",
                    "headers": [], "client": (ip, 40000), "query_string": b""})


def test_rp557_fahrer_anmeldung_mit_bekanntem_geraet(welt, monkeypatch):
    w = welt
    gerufen = []

    async def _frei(*_a, **_k):
        return True

    async def _nichts(*_a, **_k):
        return None

    async def _gesperrt(kennung, ip, konto=None, geraet_id=None):
        gerufen.append(geraet_id)
        return False

    async def _pw(passwort, h):
        return passwort == "richtig"

    monkeypatch.setattr(D.driver_login_limiter, "check", _frei)
    monkeypatch.setattr(D.driver_login_limiter, "reset", _nichts)
    monkeypatch.setattr(D.login_ip_limiter, "check", _frei)
    monkeypatch.setattr(D, "konto_gesperrt", _gesperrt)
    monkeypatch.setattr(D, "verify_password_async", _pw)
    w.run(w.db.driver_accounts.update_one({"id": w.driver["id"]},
                                          {"$set": {"password_hash": "h-welle2"}}))
    gid = "Fahrer-Handy_1234567890"
    erg = w.run(D.driver_login(D.DriverAccountLogin(
        kontonummer=w.driver["kontonummer"], password="richtig", geraet_id=gid),
        _anfrage("198.51.100.61")))
    assert erg["token"] and erg["geraet_id"] == gid
    assert gerufen == [gid], "die Sperrpruefung kennt das Geraet"
    konto = _doc(w, "driver_accounts", w.driver["id"])
    assert RL.geraet_merkwert(gid) in konto["login_geraete_bekannt"]
    assert gid not in str(konto["login_geraete_bekannt"]), "nur der HMAC liegt am Konto"
    assert "login_geraete_bekannt" not in str(erg), "Merkwerte nie in der Antwort"
    # Ohne (oder mit ungueltigem) Schluessel: der Server vergibt einen neuen.
    erg2 = w.run(D.driver_login(D.DriverAccountLogin(
        kontonummer=w.driver["kontonummer"], password="richtig", geraet_id="<kaputt>"),
        _anfrage("198.51.100.62")))
    assert RL.geraet_id_gueltig(erg2["geraet_id"]) and erg2["geraet_id"] != gid
    # Schema: Deckel wie /auth/login
    with pytest.raises(Exception):
        D.DriverAccountLogin(kontonummer="FD-1", password="x", geraet_id="x" * 81)


# ============================================================ RP-114/015/265
def test_rp114_fahrer_nicht_abgeholt_bewegt_fahrzeug_mit_vorgang_nicht(welt):
    w = welt
    t = _abholung(w, mit_vertrag=False, proto_status="entwurf")
    # Kauf eines Kollegen am selben (gemeinsamen) Fahrzeug, Abholung geplant.
    w.run(w.db.kaufvorgaenge.insert_one({
        "id": f"kx_{w.s}", "dealer_id": w.dealer_id, "user_id": w.chef["id"], "vehicle_id": t.vid,
        "contract_id": f"cx_{w.s}", "status": "abholung_geplant", "purchase_price": 5000.0,
        "created_at": _jetzt(), "updated_at": _jetzt()}))
    r = w.run(D.driver_set_status(t.aid, D.DriverStatusIn(
        status="nicht abgeholt", notes="Verkäufer nicht erschienen"), w.driver))
    assert r["ok"] and _doc(w, "appointments", t.aid)["status"] == "nicht abgeholt"
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "abholung_geplant", \
        "der offene Kauf des Kollegen bestimmt das Fahrzeug (vorher: nicht_abgeholt)"

    # Gegenprobe: Fahrzeug OHNE Kaufvorgaenge wie bisher direkt.
    aid2 = f"t2_{w.s}"
    w.run(w.db.appointments.insert_one({
        "id": aid2, "dealer_id": w.dealer_id, "driver_id": w.driver["id"],
        "vehicle_id": t.vid2, "status": "offen", "created_by": w.chef["id"],
        "pickup_date": "2099-09-10", "pickup_time": "11:00", "seller_name": "Vera",
        "pickup_address": "Teststr. 2", "created_at": _jetzt()}))
    w.run(D.driver_set_status(aid2, D.DriverStatusIn(
        status="nicht abgeholt", notes="Verkäufer hat abgesagt"), w.driver))
    assert _doc(w, "vehicles", t.vid2)["lifecycle"] == "nicht_abgeholt"
