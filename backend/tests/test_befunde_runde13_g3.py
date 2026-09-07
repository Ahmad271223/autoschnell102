# -*- coding: utf-8 -*-
"""Runde 13, Gruppe 3 (06.09.2026) — bestaetigter Befund.

  C5  GET /dealer/settings durch einen SUCHER schrieb fehlende Regelpakete
      (comparison_rules / export_rules / active_profile) in das gemeinsame
      dealers-Dokument. Jetzt: Backfill in der Datenbank nur durch den Chef;
      ein Sucher bekommt die Standardwerte NUR in der Antwort. Der Runde-11-
      Schutz ("$exists": nur schreiben, wenn das Feld weiterhin fehlt) bleibt.

Quelltext- und Funktionstests brauchen nur Mongo (MONGO_URL/DB_NAME); die
HTTP-Teile brauchen das Backend auf TEST_BASE_URL mit SELF_SIGNUP=true.
"""
import asyncio
import inspect
import os
import sys
import uuid
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "RundeDreizehn123!"
MAIL = "e2etest-mail.de"
BACKEND = Path(__file__).resolve().parents[1]

# deps.py liest MONGO_URL/DB_NAME beim Import — fuer den Funktionstest ohne
# laufenden Server dieselben Werte wie oben vorgeben (falls nicht gesetzt).
os.environ.setdefault("MONGO_URL", MONGO_URL)
os.environ.setdefault("DB_NAME", DB_NAME)

FELDER = ("comparison_rules", "export_rules", "active_profile")


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _felder_entfernen(dealer_id):
    _db().dealers.update_one({"id": dealer_id}, {"$unset": {f: "" for f in FELDER}})
    doc = _db().dealers.find_one({"id": dealer_id})
    assert doc and not any(f in doc for f in FELDER), "Vorbereitung: Felder muessen fehlen"


def _standards_pruefen(antwort):
    from mobile_service import DEFAULT_RULES, DEFAULT_EXPORT_RULES
    assert antwort.get("comparison_rules") == DEFAULT_RULES, antwort.get("comparison_rules")
    assert antwort.get("export_rules") == DEFAULT_EXPORT_RULES, antwort.get("export_rules")
    assert antwort.get("active_profile") == "inland", antwort.get("active_profile")


# =====================================================================
#                          Quelltext / Funktion
# =====================================================================
def test_c5_quelle():
    import routes.dealer as d
    from mobile_service import DEFAULT_RULES, DEFAULT_EXPORT_RULES
    src = inspect.getsource(d.get_settings)
    # Runde 11 bleibt: Backfill nur, wenn das Feld WEITERHIN fehlt
    assert "$exists" in src, "Backfill muss an 'Feld fehlt weiterhin' gebunden sein"
    # Runde 13: Schreiben ist an die Chef-Rolle gebunden — und zwar VOR update_one
    assert "ist_chef" in src and 'role") == "dealer"' in src
    assert src.index("if not ist_chef") < src.index("update_one("), \
        "die Rollenweiche muss vor dem Schreibzugriff stehen"
    # Beide Zweige nutzen dieselbe Liste; der Sucher-Zweig mischt die
    # Standardwerte in das frisch gelesene Dokument (effective_dealer)
    assert src.count("_SETTINGS_STANDARDS") == 2
    assert src.index("effective_dealer(user)") < src.index("_sucher_sicht(merged)")
    assert dict(d._SETTINGS_STANDARDS) == {
        "comparison_rules": DEFAULT_RULES, "export_rules": DEFAULT_EXPORT_RULES,
        "active_profile": "inland"}


def test_c5_funktion_sucher_liest_ohne_zu_schreiben_chef_fuellt_auf():
    """get_settings direkt (motor ueber deps.db, kein Server): Sucher-GET
    liefert Standardwerte, laesst die DB unberuehrt; Chef-GET fuellt auf."""
    import routes.dealer as d
    dbx = _db()
    dealer_id = f"r13c5_dealer_{SUF}"
    dbx.dealers.insert_one({"id": dealer_id, "user_id": f"r13c5_chef_{SUF}",
                            "company_name": f"Runde13 C5 {SUF}",
                            "created_at": "2026-01-01T00:00:00+00:00"})
    chef = {"id": f"r13c5_chef_{SUF}", "role": "dealer", "dealer_id": dealer_id}
    sucher = {"id": f"r13c5_sucher_{SUF}", "role": "sucher", "dealer_id": dealer_id}
    try:
        antwort = asyncio.run(d.get_settings(user=sucher))
        _standards_pruefen(antwort)
        assert "user_id" not in antwort, "Sucher-Sicht bleibt gefiltert"
        doc = dbx.dealers.find_one({"id": dealer_id})
        assert not any(f in doc for f in FELDER), \
            f"Sucher-GET hat geschrieben: {[f for f in FELDER if f in doc]}"
        # Persoenlicher Override des Suchers gewinnt weiterhin ueber den Standard
        antwort = asyncio.run(d.get_settings(
            user={**sucher, "settings_override": {"active_profile": "export"}}))
        assert antwort["active_profile"] == "export"
        assert not any(f in dbx.dealers.find_one({"id": dealer_id}) for f in FELDER)
        # Chef: Standardwerte in der Antwort UND in der Datenbank
        antwort = asyncio.run(d.get_settings(user=chef))
        _standards_pruefen(antwort)
        doc = dbx.dealers.find_one({"id": dealer_id})
        assert all(f in doc for f in FELDER), \
            f"Chef-Backfill fehlt: {[f for f in FELDER if f not in doc]}"
        _standards_pruefen(doc)
    finally:
        dbx.dealers.delete_many({"id": dealer_id})


# =====================================================================
#                          HTTP-Tests
# =====================================================================
def _login(mail):
    r = requests.post(f"{API}/auth/login", json={"email": mail, "password": PW}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def welt():
    """Super-Admin (direkt gesaet), Firma mit Chef + Sucher."""
    import bcrypt
    dbx = _db()
    admin_mail = f"r13_admin_{SUF}@{MAIL}"
    dbx.users.insert_one({
        "id": f"r13adm_{SUF}", "email": admin_mail, "role": "admin", "active": True,
        "dealer_id": None, "is_super_admin": True,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    A = _login(admin_mail)

    r = requests.post(f"{API}/auth/register", json={
        "email": f"r13_chef_{SUF}@{MAIL}", "password": PW,
        "company_name": f"Runde13 {SUF}", "contact_person": "R E", "phone": "0511 13"}, timeout=30)
    assert r.status_code == 200, f"Backend braucht SELF_SIGNUP=true: {r.text[:200]}"
    C = {"Authorization": f"Bearer {r.json()['token']}"}
    chef = requests.get(f"{API}/auth/me", headers=C, timeout=30).json()["user"]

    sucher_mail = f"r13_sucher_{SUF}@{MAIL}"
    r = requests.post(f"{API}/dealer/sucher", headers=C, json={
        "email": sucher_mail, "password": PW,
        "first_name": "Su", "last_name": "Cher"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    sucher_id = r.json()["sucher_id"]
    z = {"A": A, "C": C, "S": _login(sucher_mail), "chef": chef,
         "sucher_id": sucher_id, "dealer_id": chef["dealer_id"]}
    yield z
    for coll in ("subscriptions", "activity_logs", "plan_requests", "password_resets"):
        dbx[coll].delete_many({"dealer_id": z["dealer_id"]})
    dbx.password_resets.delete_many({"user_id": {"$in": [chef["id"], sucher_id]}})
    dbx.plan_requests.delete_many({"subject_user_id": {"$in": [chef["id"], sucher_id]}})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.dealers.delete_many({"id": z["dealer_id"]})


def test_c5_http_sucher_get_schreibt_nichts_chef_fuellt_auf(welt):
    dbx = _db()
    d = welt["dealer_id"]
    _felder_entfernen(d)

    # Betreiber ist kein Firmenmitglied: 403, nichts geschrieben (fail-closed)
    r = requests.get(f"{API}/dealer/settings", headers=welt["A"], timeout=30)
    assert r.status_code == 403, r.text[:200]
    assert not any(f in dbx.dealers.find_one({"id": d}) for f in FELDER)

    # Sucher: 200 mit Standardwerten in der Antwort ...
    r = requests.get(f"{API}/dealer/settings", headers=welt["S"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    _standards_pruefen(r.json())
    assert "user_id" not in r.json()
    # ... aber die Datenbank bleibt unberuehrt
    doc = dbx.dealers.find_one({"id": d})
    assert not any(f in doc for f in FELDER), \
        f"Sucher-GET hat in dealers geschrieben: {[f for f in FELDER if f in doc]}"
    # auch ein zweites Lesen aendert nichts
    assert requests.get(f"{API}/dealer/settings", headers=welt["S"], timeout=30).status_code == 200
    assert not any(f in dbx.dealers.find_one({"id": d}) for f in FELDER)

    # Chef: dieselben Standardwerte, jetzt auch in der Datenbank (Backfill bleibt)
    r = requests.get(f"{API}/dealer/settings", headers=welt["C"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    _standards_pruefen(r.json())
    doc = dbx.dealers.find_one({"id": d})
    assert all(f in doc for f in FELDER), \
        f"Chef-Backfill fehlt: {[f for f in FELDER if f not in doc]}"
    _standards_pruefen(doc)


def test_c5_http_sucher_override_gewinnt_ueber_standard(welt):
    """Persoenlicher Override des Suchers bleibt wirksam, auch wenn das
    Firmenfeld fehlt — das Mischen der Standardwerte fuellt nur Luecken."""
    dbx = _db()
    d = welt["dealer_id"]
    r = requests.put(f"{API}/dealer/active-profile", headers=welt["S"],
                     json={"active_profile": "export"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    _felder_entfernen(d)
    r = requests.get(f"{API}/dealer/settings", headers=welt["S"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert r.json()["active_profile"] == "export"
    assert r.json().get("comparison_rules") and r.json().get("export_rules")
    assert not any(f in dbx.dealers.find_one({"id": d}) for f in FELDER)
    # Chef-Sicht ist vom Sucher-Override unberuehrt und fuellt "inland" auf
    r = requests.get(f"{API}/dealer/settings", headers=welt["C"], timeout=30)
    assert r.status_code == 200 and r.json()["active_profile"] == "inland"
    assert dbx.dealers.find_one({"id": d}).get("active_profile") == "inland"
