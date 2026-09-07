# -*- coding: utf-8 -*-
"""Nachpruefung Runde 14 (07.09.2026), Gruppe "admin" — routes/admin.py.

  12  Firmenloeschung entfernt password_resets aller Konten der Firma
  17  Fahrer-Sperre trennt offene Termine vom Fahrer (+ Audit je Firma)
  18  PUT /admin/users/{id} active=false verwirft Sitzung (auch der Sucher);
      active=true laesst keine alte Sitzung wieder gelten
  24  Kaeufer-Loeschung pseudonymisiert zugang_grants (Buchhaltung bleibt)
  32  sale-plan custom_quota: ungueltig -> 400, leer -> null
  49  Firmenanlage: Rollback von Konto und Firmenprofil bei Abo-Insert-Fehler
  50  expires_at wird validiert (400), Tagesdatum -> Tagesende
  58  Kontoloeschung: Grabstein, Nebendaten zuerst, users zuletzt, wiederaufnehmbar

Einheitentests rufen die Routen direkt (motor ueber deps.db, kein Server).
HTTP-Teile laufen nur mit RUNDE14_HTTP=1 gegen TEST_BASE_URL.
"""
import asyncio
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
MAIL = "e2etest-mail.de"
JETZT = datetime.now(timezone.utc)

# Super-Admin als Abhaengigkeit fuer direkte Routenaufrufe (kein DB-Konto noetig)
SA = {"id": f"r14sa_{SUF}", "role": "admin", "is_super_admin": True,
      "active": True, "dealer_id": None, "email": f"r14_sa_{SUF}@{MAIL}"}


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _iso(delta_tage=1):
    return (JETZT + timedelta(days=delta_tage)).isoformat()


@pytest.fixture(scope="module")
def aufraeumen():
    """Alles, was die Tests dieser Datei anlegen, traegt SUF im Wert."""
    yield
    dbx = _db()
    muster = {"$regex": SUF}
    dbx.users.delete_many({"$or": [{"id": muster}, {"email": muster}, {"dealer_id": muster}]})
    dbx.dealers.delete_many({"$or": [{"id": muster}, {"email": muster}]})
    dbx.driver_accounts.delete_many({"$or": [{"id": muster}, {"email": muster}]})
    for coll in ("subscriptions", "appointments", "password_resets", "zugang_grants",
                 "activity_logs", "network_members", "buyer_favorites",
                 "listing_interest", "plan_requests", "dealer_drivers"):
        dbx[coll].delete_many({"$or": [{"dealer_id": muster}, {"user_id": muster},
                                       {"subject_user_id": muster}, {"buyer_user_id": muster},
                                       {"driver_id": muster}, {"driver_account_id": muster},
                                       {"id": muster}, {"ref": muster}]})


# =====================================================================
#                          Einheitentests
# =====================================================================
def test_50_ablaufdatum_helfer():
    from fastapi import HTTPException
    from routes.admin import _ablaufdatum_pruefen_400 as f
    assert f(None) is None and f("") is None and f("   ") is None
    tag = f("2027-12-31")
    assert tag.startswith("2027-12-31T23:59:59"), tag          # Tagesende wie _gueltig_bis_parsen
    assert f("2027-12-31T10:00:00Z") == "2027-12-31T10:00:00+00:00"
    assert f("2027-12-31T10:00:00") == "2027-12-31T10:00:00+00:00"   # naiv = UTC
    assert f("2027-12-31T10:00:00+02:00") == "2027-12-31T10:00:00+02:00"
    for kaputt in ("31.12.2027", "2027-13-45", "irgendwann", "2027-12-31T25:00:00", 12345):
        with pytest.raises(HTTPException) as e:
            f(kaputt)
        assert e.value.status_code == 400, kaputt
    # deps liest das Ergebnis als gueltiges, aktives Abo
    from deps import sub_status_from_doc
    st = sub_status_from_doc({"plan": "yearly", "status": "active", "expires_at": tag})
    assert st["active"] is True and st["status"] == "active"


def test_50_49_firmenanlage_datum_und_rollback(aufraeumen, monkeypatch):
    from fastapi import HTTPException
    import routes.admin as a
    dbx = _db()
    mail = f"r14_anlage_{SUF}@{MAIL}"

    def anlegen(**extra):
        body = a.AdminUserIn(email=mail, password=PW, company_name=f"R14 Anlage {SUF}",
                             plan_type="yearly", **extra)
        return asyncio.run(a.admin_create_user(body=body, admin=SA))

    # 50: ungueltiges Datum -> 400, KEIN Konto und KEIN Firmenprofil zurueckgelassen
    with pytest.raises(HTTPException) as e:
        anlegen(expires_at="31.12.2027")
    assert e.value.status_code == 400
    assert dbx.users.count_documents({"email": mail}) == 0
    assert dbx.dealers.count_documents({"email": mail}) == 0

    # 49: Abo-Insert scheitert -> 500, Konto UND Firmenprofil weg, Wiederholung klappt
    class _KaputtesAbo:
        async def insert_one(self, *args, **kwargs):
            raise RuntimeError("simulierter DB-Ausfall")

    class _DbMitKaputtemAbo:
        def __init__(self, echt):
            self._echt = echt
        @property
        def subscriptions(self):
            return _KaputtesAbo()
        def __getattr__(self, name):
            return getattr(self._echt, name)
        def __getitem__(self, name):
            return self._echt[name]

    echt = a.db
    monkeypatch.setattr(a, "db", _DbMitKaputtemAbo(echt))
    with pytest.raises(HTTPException) as e:
        anlegen(expires_at="2027-12-31")
    assert e.value.status_code == 500
    assert dbx.users.count_documents({"email": mail}) == 0, "Konto blieb ohne Abo stehen"
    assert dbx.dealers.count_documents({"email": mail}) == 0, "Firmenprofil blieb stehen"
    monkeypatch.setattr(a, "db", echt)

    erg = anlegen(expires_at="2027-12-31")
    assert erg["ok"] is True, "zweiter Versuch muss durchgehen (vorher 409)"
    sub = dbx.subscriptions.find_one({"dealer_id": erg["dealer_id"]}, {"_id": 0})
    assert sub and sub["expires_at"].startswith("2027-12-31T23:59:59"), sub
    from deps import sub_status_from_doc
    assert sub_status_from_doc(sub)["active"] is True
    # Aufraeumen ueber SUF: dealer_id/user_id tragen es nicht -> gezielt
    dbx.users.delete_many({"id": erg["user_id"]})
    dbx.dealers.delete_many({"id": erg["dealer_id"]})
    dbx.subscriptions.delete_many({"dealer_id": erg["dealer_id"]})


def _firma_anlegen(dbx, kennung):
    dealer_id = f"r14_{kennung}_dealer_{SUF}"
    chef_id = f"r14_{kennung}_chef_{SUF}"
    sucher_id = f"r14_{kennung}_sucher_{SUF}"
    dbx.dealers.insert_one({"id": dealer_id, "user_id": chef_id, "company_name": f"R14 {kennung} {SUF}",
                            "email": f"r14_{kennung}_chef_{SUF}@{MAIL}", "created_at": _iso(0)})
    dbx.users.insert_many([
        {"id": chef_id, "email": f"r14_{kennung}_chef_{SUF}@{MAIL}", "role": "dealer",
         "active": True, "dealer_id": dealer_id, "current_session_id": "sitz-chef",
         "password_hash": "x", "created_at": _iso(0)},
        {"id": sucher_id, "email": f"r14_{kennung}_sucher_{SUF}@{MAIL}", "role": "sucher",
         "active": True, "dealer_id": dealer_id, "current_session_id": "sitz-sucher",
         "password_hash": "x", "created_at": _iso(0)},
    ])
    return dealer_id, chef_id, sucher_id


def test_18_put_sperre_verwirft_sitzungen(aufraeumen):
    import routes.admin as a
    dbx = _db()
    dealer_id, chef_id, sucher_id = _firma_anlegen(dbx, "put")

    erg = asyncio.run(a.admin_update_user(chef_id, body={"active": False}, admin=SA))
    assert erg["ok"] is True and erg["sucher_abgemeldet"] == 1
    chef = dbx.users.find_one({"id": chef_id})
    sucher = dbx.users.find_one({"id": sucher_id})
    assert chef["active"] is False and chef["current_session_id"] is None
    assert sucher["current_session_id"] is None, "Sucher-Sitzung ueberlebte die Firmensperre"

    # Altbestand: ueber den alten PUT gesperrt, Sitzung noch gespeichert ->
    # Entsperren darf sie nicht wieder gelten lassen
    dbx.users.update_one({"id": chef_id}, {"$set": {"current_session_id": "alt-gestohlen"}})
    asyncio.run(a.admin_update_user(chef_id, body={"active": True}, admin=SA))
    chef = dbx.users.find_one({"id": chef_id})
    assert chef["active"] is True and chef["current_session_id"] is None

    # Aktives Konto erneut auf active=true setzen: laufende Sitzung bleibt
    dbx.users.update_one({"id": chef_id}, {"$set": {"current_session_id": "neu"}})
    asyncio.run(a.admin_update_user(chef_id, body={"active": True}, admin=SA))
    assert dbx.users.find_one({"id": chef_id})["current_session_id"] == "neu"

    # Sucher sperren: nur seine Sitzung, kein Firmen-Effekt
    dbx.users.update_one({"id": sucher_id}, {"$set": {"current_session_id": "s2"}})
    erg = asyncio.run(a.admin_update_user(sucher_id, body={"active": "0"}, admin=SA))
    s = dbx.users.find_one({"id": sucher_id})
    assert s["active"] is True, "String '0' ist wahr — bool() wie bei POST /active"
    erg = asyncio.run(a.admin_update_user(sucher_id, body={"active": False}, admin=SA))
    s = dbx.users.find_one({"id": sucher_id})
    assert s["active"] is False and s["current_session_id"] is None and erg["sucher_abgemeldet"] == 0
    assert dbx.users.find_one({"id": chef_id})["current_session_id"] == "neu"


def test_50_put_abo_lehnt_ungueltiges_datum_ab(aufraeumen):
    from fastapi import HTTPException
    import routes.admin as a
    dbx = _db()
    dealer_id, chef_id, sucher_id = _firma_anlegen(dbx, "putabo")
    with pytest.raises(HTTPException) as e:
        asyncio.run(a.admin_update_user(sucher_id, body={"plan_type": "monthly",
                                                         "expires_at": "irgendwann"}, admin=SA))
    assert e.value.status_code == 400
    assert dbx.subscriptions.count_documents({"subject_user_id": sucher_id}) == 0
    asyncio.run(a.admin_update_user(sucher_id, body={"plan_type": "monthly",
                                                     "expires_at": "2027-06-30"}, admin=SA))
    sub = dbx.subscriptions.find_one({"subject_user_id": sucher_id}, {"_id": 0})
    assert sub["expires_at"].startswith("2027-06-30T23:59:59"), sub


def test_12_firmenloeschung_entfernt_password_resets(aufraeumen):
    import routes.admin as a
    dbx = _db()
    dealer_id, chef_id, sucher_id = _firma_anlegen(dbx, "firma")
    for uid in (chef_id, sucher_id):
        dbx.password_resets.insert_one({"id": f"r14_reset_{uid}", "user_id": uid,
                                        "token_hash": "h", "requested_ip": "1.2.3.4",
                                        "created_at": _iso(0), "loeschen_ab": JETZT + timedelta(days=7)})
    dbx.subscriptions.insert_one({"id": f"r14_sub_{SUF}", "dealer_id": dealer_id,
                                  "subject_user_id": sucher_id, "plan": "monthly",
                                  "status": "active", "expires_at": _iso(3), "created_at": _iso(0)})
    erg = asyncio.run(a.admin_delete_user(chef_id, firma_loeschen=True, admin=SA))
    assert erg["ok"] is True
    assert erg["geloescht"].get("password_resets") == 2, erg
    assert erg["geloescht"].get("users") == 2
    assert dbx.password_resets.count_documents({"user_id": {"$in": [chef_id, sucher_id]}}) == 0
    assert dbx.users.count_documents({"dealer_id": dealer_id}) == 0
    assert dbx.dealers.count_documents({"id": dealer_id}) == 0
    assert dbx.subscriptions.count_documents({"dealer_id": dealer_id}) == 0


def test_58_users_zuletzt_in_company_collections():
    from routes.admin import _COMPANY_COLLECTIONS
    assert _COMPANY_COLLECTIONS[-1] == "users"
    assert "driver_accounts" not in _COMPANY_COLLECTIONS


def test_24_58_kaeufer_loeschung_grabstein_und_wiederaufnahme(aufraeumen, monkeypatch):
    import hashlib
    import routes.admin as a
    import snapshot_service
    dbx = _db()
    buyer_id = f"r14_buyer_{SUF}"
    dbx.users.insert_one({"id": buyer_id, "email": f"r14_buyer_{SUF}@{MAIL}", "role": "b2b_buyer",
                          "active": True, "dealer_id": None, "current_session_id": "sitz-buyer",
                          "password_hash": "x", "created_at": _iso(0)})
    dbx.zugang_grants.insert_one({"id": f"r14_grant_{SUF}", "session_id": f"cs_r14_{SUF}",
                                  "user_id": buyer_id, "plan": "marktplatz", "tage": 30,
                                  "basis": _iso(0), "expires_at": _iso(30), "created_at": _iso(0)})
    dbx.subscriptions.insert_one({"id": f"r14_bsub_{SUF}", "dealer_id": None, "subject_user_id": buyer_id,
                                  "plan": "monthly", "status": "active", "expires_at": _iso(3),
                                  "created_at": _iso(0)})
    dbx.password_resets.insert_one({"id": f"r14_breset_{SUF}", "user_id": buyer_id, "token_hash": "h",
                                    "created_at": _iso(0), "loeschen_ab": JETZT + timedelta(days=7)})
    dbx.buyer_favorites.insert_one({"id": f"r14_fav_{SUF}", "buyer_user_id": buyer_id,
                                    "listing_id": "x", "created_at": _iso(0)})

    # Fehlerinjektion NACH den Nebendaten (Snapshot-Schritt) -> Konto muss noch da sein
    async def _kaputt(*args, **kwargs):
        raise RuntimeError("simulierter Abbruch")
    monkeypatch.setattr(snapshot_service, "snapshots_pseudonymisieren", _kaputt)
    with pytest.raises(RuntimeError):
        asyncio.run(a.admin_delete_user(buyer_id, admin=SA))
    u = dbx.users.find_one({"id": buyer_id})
    assert u is not None, "Konto war vor den Nebendaten weg (altes Muster)"
    assert u["loeschung"]["status"] == "laeuft" and u["loeschung"]["durch"] == SA["id"]
    assert u["active"] is False and u["current_session_id"] is None, "Grabstein sperrt sofort"
    # 24: Grant pseudonymisiert statt geloescht (Buchhaltung: Zahlung bleibt belegbar)
    pseudonym = "geloescht:" + hashlib.sha256(buyer_id.encode()).hexdigest()[:8]
    assert dbx.zugang_grants.count_documents({"user_id": buyer_id}) == 0
    g = dbx.zugang_grants.find_one({"session_id": f"cs_r14_{SUF}"})
    assert g and g["user_id"] == pseudonym and g.get("pseudonymisiert_at")
    assert dbx.subscriptions.count_documents({"subject_user_id": buyer_id}) == 0
    assert dbx.password_resets.count_documents({"user_id": buyer_id}) == 0
    assert dbx.buyer_favorites.count_documents({"buyer_user_id": buyer_id}) == 0

    # Wiederaufnahme: zweiter Aufruf fuehrt zu Ende (kein 404), Pseudonym stabil
    monkeypatch.undo()
    erg = asyncio.run(a.admin_delete_user(buyer_id, admin=SA))
    assert erg == {"ok": True, "geloescht": "nur_nutzer"}
    assert dbx.users.count_documents({"id": buyer_id}) == 0
    assert dbx.zugang_grants.find_one({"session_id": f"cs_r14_{SUF}"})["user_id"] == pseudonym
    log_eintrag = dbx.activity_logs.find_one({"action": "admin.user.geloescht", "ref": buyer_id})
    assert log_eintrag and log_eintrag["meta"]["wiederaufnahme"] is True
    dbx.activity_logs.delete_many({"ref": buyer_id})
    dbx.zugang_grants.delete_many({"session_id": f"cs_r14_{SUF}"})


def test_17_fahrer_sperre_trennt_offene_termine(aufraeumen):
    import routes.admin as a
    dbx = _db()
    dealer_id = f"r14_fahrer_dealer_{SUF}"
    driver_id = f"r14_driver_{SUF}"
    dbx.driver_accounts.insert_one({"id": driver_id, "email": f"r14_fahrer_{SUF}@{MAIL}",
                                    "driver_code": f"F{SUF[:4]}", "active": True,
                                    "current_session_id": "sitz-fahrer", "password_hash": "x",
                                    "created_at": _iso(0)})
    termine = [
        {"id": f"r14_t_offen_{SUF}", "dealer_id": dealer_id, "driver_id": driver_id,
         "status": "offen", "zuteilung": "offen", "created_at": _iso(0)},
        {"id": f"r14_t_best_{SUF}", "dealer_id": dealer_id, "driver_id": driver_id,
         "status": "bestätigt", "zuteilung": "angenommen", "created_at": _iso(0)},
        {"id": f"r14_t_leer_{SUF}", "dealer_id": dealer_id, "driver_id": driver_id,
         "created_at": _iso(0)},                                       # kein Status = offen
        {"id": f"r14_t_fertig_{SUF}", "dealer_id": dealer_id, "driver_id": driver_id,
         "status": "abgeholt", "created_at": _iso(0)},
        {"id": f"r14_t_fremd_{SUF}", "dealer_id": dealer_id, "driver_id": f"r14_anderer_{SUF}",
         "status": "offen", "created_at": _iso(0)},
    ]
    dbx.appointments.insert_many(termine)
    erg = asyncio.run(a.admin_driver_set_active(driver_id, a.AdminActiveIn(active=False), admin=SA))
    assert erg["active"] is False and erg["offene_termine_getrennt"] == 3, erg
    for tid in (f"r14_t_offen_{SUF}", f"r14_t_best_{SUF}", f"r14_t_leer_{SUF}"):
        t = dbx.appointments.find_one({"id": tid})
        assert t.get("driver_id") is None and t.get("zuteilung") is None, t
    assert dbx.appointments.find_one({"id": f"r14_t_fertig_{SUF}"})["driver_id"] == driver_id
    assert dbx.appointments.find_one({"id": f"r14_t_fremd_{SUF}"})["driver_id"] == f"r14_anderer_{SUF}"
    d = dbx.driver_accounts.find_one({"id": driver_id})
    assert d["active"] is False and d["current_session_id"] is None
    # Audit im Log der betroffenen Firma (der Chef sieht, was neu zuzuteilen ist)
    e = dbx.activity_logs.find_one({"dealer_id": dealer_id, "action": "fahrer.gesperrt.termine_freigegeben"})
    assert e and sorted(e["meta"]["termine"]) == sorted(
        [f"r14_t_offen_{SUF}", f"r14_t_best_{SUF}", f"r14_t_leer_{SUF}"])
    # Entsperren: keine Termin-Aenderung, Zaehler 0
    erg = asyncio.run(a.admin_driver_set_active(driver_id, a.AdminActiveIn(active=True), admin=SA))
    assert erg == {"ok": True, "active": True, "offene_termine_getrennt": 0}


def test_32_custom_quota_validierung(aufraeumen):
    from fastapi import HTTPException
    import routes.admin as a
    dbx = _db()
    dealer_id = f"r14_quota_dealer_{SUF}"
    dbx.dealers.insert_one({"id": dealer_id, "company_name": f"R14 Quota {SUF}", "created_at": _iso(0)})

    def setzen(cq):
        return asyncio.run(a.admin_set_sale_plan(dealer_id, body={"tier": "enterprise", "custom_quota": cq},
                                                 admin=SA))

    for kaputt in ("abc", "1o0", "-5", -5, True, 1.5, "10.5"):
        with pytest.raises(HTTPException) as e:
            setzen(kaputt)
        assert e.value.status_code == 400, kaputt
    assert setzen("100")["sale_plan"]["custom_quota"] == 100
    assert dbx.dealers.find_one({"id": dealer_id})["sale_plan"]["custom_quota"] == 100
    assert setzen(250)["sale_plan"]["custom_quota"] == 250
    assert setzen(20.0)["sale_plan"]["custom_quota"] == 20
    assert setzen("")["sale_plan"]["custom_quota"] is None
    assert setzen(None)["sale_plan"]["custom_quota"] is None
    assert setzen(0)["sale_plan"]["custom_quota"] is None      # 0 = "keine feste Zahl", wie bisher


# =====================================================================
#                          HTTP-Tests (nach Neustart)
# =====================================================================
def _kopf(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def http_welt(aufraeumen):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    import bcrypt
    dbx = _db()
    sa_mail = f"r14_httpsa_{SUF}@{MAIL}"
    dbx.users.insert_one({
        "id": f"r14_httpsa_{SUF}", "email": sa_mail, "role": "admin", "active": True,
        "dealer_id": None, "is_super_admin": True,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    r = requests.post(f"{API}/auth/login", json={"email": sa_mail, "password": PW}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    z = {"S": _kopf(r.json()["token"])}
    yield z
    for did in z.get("dealer_ids", []):
        for coll in ("users", "subscriptions", "activity_logs", "appointments", "dealer_drivers"):
            dbx[coll].delete_many({"dealer_id": did})
        dbx.dealers.delete_many({"id": did})


def _login(mail):
    r = requests.post(f"{API}/auth/login", json={"email": mail, "password": PW}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    return _kopf(r.json()["token"])


def _firma_http(http_welt, kennung, **extra):
    r = requests.post(f"{API}/admin/users", headers=http_welt["S"], json={
        "email": f"r14_{kennung}_{SUF}@{MAIL}", "password": PW,
        "company_name": f"R14 HTTP {kennung} {SUF}", "plan_type": "none", **extra}, timeout=30)
    return r


def test_http_18_put_sperre_und_entsperren(http_welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    r = _firma_http(http_welt, "h18")
    assert r.status_code == 200, r.text[:300]
    chef_id, dealer_id = r.json()["user_id"], r.json()["dealer_id"]
    http_welt.setdefault("dealer_ids", []).append(dealer_id)
    chef = _login(f"r14_h18_{SUF}@{MAIL}")
    assert requests.get(f"{API}/auth/me", headers=chef, timeout=30).status_code == 200
    r = requests.put(f"{API}/admin/users/{chef_id}", headers=http_welt["S"],
                     json={"active": False}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    assert requests.get(f"{API}/auth/me", headers=chef, timeout=30).status_code == 401
    assert _db().users.find_one({"id": chef_id})["current_session_id"] is None
    r = requests.put(f"{API}/admin/users/{chef_id}", headers=http_welt["S"],
                     json={"active": True}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    assert requests.get(f"{API}/auth/me", headers=chef, timeout=30).status_code == 401, \
        "altes Token darf nach dem Entsperren nicht wieder gelten"
    neu = _login(f"r14_h18_{SUF}@{MAIL}")
    assert requests.get(f"{API}/auth/me", headers=neu, timeout=30).status_code == 200


def test_http_50_firmenanlage_expires_at(http_welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    r = _firma_http(http_welt, "h50a", plan_type="yearly", expires_at="31.12.2027")
    assert r.status_code == 400, r.text[:300]
    assert _db().users.count_documents({"email": f"r14_h50a_{SUF}@{MAIL}"}) == 0
    r = _firma_http(http_welt, "h50b", plan_type="yearly", expires_at="2027-12-31")
    assert r.status_code == 200, r.text[:300]
    dealer_id = r.json()["dealer_id"]
    http_welt.setdefault("dealer_ids", []).append(dealer_id)
    liste = requests.get(f"{API}/admin/users", headers=http_welt["S"], timeout=30).json()
    eintrag = next(u for u in liste if u["id"] == r.json()["user_id"])
    assert eintrag["subscription"]["active"] is True
    assert str(eintrag["subscription"]["expires_at"]).startswith("2027-12-31T23:59:59")


def test_http_32_sale_plan_custom_quota(http_welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    r = _firma_http(http_welt, "h32")
    assert r.status_code == 200, r.text[:300]
    dealer_id = r.json()["dealer_id"]
    http_welt.setdefault("dealer_ids", []).append(dealer_id)
    url = f"{API}/admin/dealers/{dealer_id}/sale-plan"
    for kaputt in ("abc", "1o0", -1):
        r = requests.put(url, headers=http_welt["S"], json={"tier": "enterprise", "custom_quota": kaputt},
                         timeout=30)
        assert r.status_code == 400, (kaputt, r.text[:200])
    r = requests.put(url, headers=http_welt["S"], json={"tier": "enterprise", "custom_quota": "100"}, timeout=30)
    assert r.status_code == 200 and r.json()["sale_plan"]["custom_quota"] == 100, r.text[:200]
    r = requests.put(url, headers=http_welt["S"], json={"tier": "enterprise", "custom_quota": ""}, timeout=30)
    assert r.status_code == 200 and r.json()["sale_plan"]["custom_quota"] is None, r.text[:200]


def test_http_17_fahrer_sperre(http_welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    dbx = _db()
    dealer_id = f"r14_h17_dealer_{SUF}"
    driver_id = f"r14_h17_driver_{SUF}"
    dbx.driver_accounts.insert_one({"id": driver_id, "email": f"r14_h17fahrer_{SUF}@{MAIL}",
                                    "driver_code": f"H{SUF[:4]}", "active": True,
                                    "password_hash": "x", "created_at": _iso(0)})
    dbx.appointments.insert_one({"id": f"r14_h17_t_{SUF}", "dealer_id": dealer_id, "driver_id": driver_id,
                                 "status": "offen", "zuteilung": "offen", "created_at": _iso(0)})
    r = requests.post(f"{API}/admin/drivers/{driver_id}/active", headers=http_welt["S"],
                      json={"active": False}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    assert r.json()["offene_termine_getrennt"] == 1
    t = dbx.appointments.find_one({"id": f"r14_h17_t_{SUF}"})
    assert t.get("driver_id") is None and t.get("zuteilung") is None


def test_http_24_58_kaeufer_loeschen(http_welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    import hashlib
    dbx = _db()
    buyer_id = f"r14_hbuyer_{SUF}"
    dbx.users.insert_one({"id": buyer_id, "email": f"r14_hbuyer_{SUF}@{MAIL}", "role": "b2b_buyer",
                          "active": True, "dealer_id": None, "password_hash": "x", "created_at": _iso(0)})
    dbx.zugang_grants.insert_one({"id": f"r14_hgrant_{SUF}", "session_id": f"cs_r14h_{SUF}",
                                  "user_id": buyer_id, "plan": "marktplatz", "tage": 30,
                                  "expires_at": _iso(30), "created_at": _iso(0)})
    r = requests.delete(f"{API}/admin/users/{buyer_id}", headers=http_welt["S"], timeout=30)
    assert r.status_code == 200 and r.json()["geloescht"] == "nur_nutzer", r.text[:300]
    assert dbx.users.count_documents({"id": buyer_id}) == 0
    g = dbx.zugang_grants.find_one({"session_id": f"cs_r14h_{SUF}"})
    assert g["user_id"] == "geloescht:" + hashlib.sha256(buyer_id.encode()).hexdigest()[:8]
    dbx.zugang_grants.delete_many({"session_id": f"cs_r14h_{SUF}"})
    # zweiter Aufruf: 404 (Konto ist vollstaendig weg)
    assert requests.delete(f"{API}/admin/users/{buyer_id}", headers=http_welt["S"], timeout=30).status_code == 404


def test_http_12_firmenloeschung_password_resets(http_welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    dbx = _db()
    r = _firma_http(http_welt, "h12")
    assert r.status_code == 200, r.text[:300]
    chef_id, dealer_id = r.json()["user_id"], r.json()["dealer_id"]
    http_welt.setdefault("dealer_ids", []).append(dealer_id)
    dbx.password_resets.insert_one({"id": f"r14_hreset_{SUF}", "user_id": chef_id, "token_hash": "h",
                                    "created_at": _iso(0), "loeschen_ab": JETZT + timedelta(days=7)})
    r = requests.delete(f"{API}/admin/users/{chef_id}?firma_loeschen=true", headers=http_welt["S"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    assert r.json()["geloescht"].get("password_resets") == 1
    assert dbx.password_resets.count_documents({"user_id": chef_id}) == 0
    assert dbx.dealers.count_documents({"id": dealer_id}) == 0
