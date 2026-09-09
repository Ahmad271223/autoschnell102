# -*- coding: utf-8 -*-
"""Runde 19 (09.09.2026): Die Abmelde-Meldung nennt den ECHTEN Grund.

Vorher hiess jede 401 auf der Login-Seite "auf einem anderen Geraet
verwendet" — auch nach Abmeldung, Sperre oder abgelaufenem Token. Jetzt:
  * neue Anmeldung -> "erneut angemeldet am <Zeit> von <Browser auf System>"
  * Abmeldung/Sperre -> "Abmeldung, Sperre oder Passwortwechsel"
  * Login-Protokoll traegt das Geraet

Teil 1 ohne Server (geraet_kurz, sitzung_beendet_grund), Teil 2 HTTP.
"""
import os
import sys
import uuid
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deps import sitzung_beendet_grund  # noqa: E402
from routes.auth import geraet_kurz  # noqa: E402

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
SUF = uuid.uuid4().hex[:8]
PW = "SitzungTest123!x"


class _Req:
    def __init__(self, ua):
        self.headers = {"user-agent": ua}


def test_01_geraet_kurz_erkennt_browser_und_system():
    assert geraet_kurz(_Req("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/128.0 Mobile Safari/537.36")) == "Chrome auf Android"
    assert geraet_kurz(_Req("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
                            "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")) == "Safari auf iPhone"
    assert geraet_kurz(_Req("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 Edg/128.0")) == "Edge auf Windows"
    assert geraet_kurz(_Req("Mozilla/5.0 (Windows NT 10.0; rv:130.0) Gecko/20100101 Firefox/130.0")) \
        == "Firefox auf Windows"
    assert geraet_kurz(_Req("")) == ""
    assert geraet_kurz(None) == ""


def test_02_grund_unterscheidet_abmeldung_und_neue_anmeldung():
    assert "Abmeldung, Sperre oder Passwortwechsel" in sitzung_beendet_grund(
        {"current_session_id": None})
    g = sitzung_beendet_grund({"current_session_id": "x",
                               "current_session_seit": "2026-09-09T21:41:00+00:00",
                               "current_session_geraet": "Chrome auf Android"})
    assert "erneut angemeldet" in g and "von Chrome auf Android" in g
    assert "09.09.2026" in g and "nur eine Anmeldung" in g
    # ohne Zusatzangaben trotzdem verstaendlich
    assert "erneut angemeldet" in sitzung_beendet_grund({"current_session_id": "x"})


@pytest.fixture(scope="module")
def konto():
    try:
        if requests.get(f"{API}/health", timeout=5).status_code != 200:
            pytest.skip("Backend nicht erreichbar")
    except requests.RequestException:
        pytest.skip("Backend nicht erreichbar")
    mail = f"sitzung_{SUF}@sitzungtest-mail.de"
    r = requests.post(f"{API}/auth/register", json={
        "email": mail, "password": PW, "company_name": f"Sitzung {SUF}",
        "contact_person": "S Chef", "phone": "0511 9"}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"Registrierung nicht moeglich ({r.status_code})")
    yield {"mail": mail, "token0": r.json()["token"], "user_id": r.json()["user"]["id"],
           "dealer_id": r.json()["user"].get("dealer_id")}
    from pymongo import MongoClient
    dbx = MongoClient(os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017")[
        os.environ.get("DB_NAME") or "autoschnell"]
    dbx.users.delete_many({"email": mail})
    dbx.dealers.delete_many({"id": r.json()["user"].get("dealer_id")})
    dbx.activity_logs.delete_many({"user_id": r.json()["user"]["id"]})


def _login(mail, ua):
    return requests.post(f"{API}/auth/login", json={"email": mail, "password": PW},
                         headers={"User-Agent": ua}, timeout=30)


def test_10_zweite_anmeldung_nennt_zeit_und_geraet(konto):
    a = _login(konto["mail"], "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0 Safari/537.36")
    assert a.status_code == 200, a.text[:200]
    tok_a = a.json()["token"]
    assert requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {tok_a}"},
                        timeout=30).status_code == 200
    b = _login(konto["mail"], "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                              "AppleWebKit/605.1.15 Version/17.5 Mobile/15E148 Safari/604.1")
    assert b.status_code == 200, b.text[:200]
    r = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {tok_a}"}, timeout=30)
    assert r.status_code == 401
    detail = r.json()["detail"]
    assert "erneut angemeldet" in detail and "von Safari auf iPhone" in detail, detail
    assert "am " in detail and "Uhr" in detail
    konto["token_b"] = b.json()["token"]
    # Login-Protokoll kennt das Geraet
    from pymongo import MongoClient
    dbx = MongoClient(os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017")[
        os.environ.get("DB_NAME") or "autoschnell"]
    letzter = dbx.activity_logs.find_one({"action": "auth.login", "user_id": konto["user_id"]},
                                         sort=[("created_at", -1)])
    assert letzter and letzter["meta"].get("geraet") == "Safari auf iPhone"
    u = dbx.users.find_one({"id": konto["user_id"]})
    assert u.get("current_session_geraet") == "Safari auf iPhone" and u.get("current_session_seit")


def test_11_nach_abmeldung_heisst_es_nicht_anderes_geraet(konto):
    h = {"Authorization": f"Bearer {konto['token_b']}"}
    r = requests.post(f"{API}/auth/logout", headers=h, timeout=30)
    assert r.status_code in (200, 204), r.text[:200]
    r = requests.get(f"{API}/auth/me", headers=h, timeout=30)
    assert r.status_code == 401
    assert "Abmeldung, Sperre oder Passwortwechsel" in r.json()["detail"]
    assert "anderen Ger" not in r.json()["detail"]
