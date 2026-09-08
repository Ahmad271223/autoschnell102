# -*- coding: utf-8 -*-
"""Runde 11 (06.09.2026) — bestaetigte Befunde aus vier weiteren Pruefberichten.

  H1  Sortierung wurde gespeichert, aber von beiden Portal-Bauern ignoriert
  H2  "radius" war speicherbar, aber wirkungslos
  H3  AutoScout setzte Kategorie/Tueren nicht um; Hubraum/Navi/Klima ohne Hinweis
  H4  Laender: XX ging durch, CH lief bei AutoScout still auf "alle Laender"
  H5  Regelvalidierung stutzte still (40 Laender, 30 Features, result_count)
  H6  Ausstattungsnamen ohne Wirkung waren speicherbar; Modus "exact" unerreichbar
  H7  manuelle Suche nannte das verwendete Profil nicht
  I1  GET /dealer/settings schrieb Defaults ohne Bedingung (Rennen mit PUT)
  I2  Chef-Einstellungen und Profilwechsel fehlten im Protokoll
  I3  Abo-Anzeige mischte ersetztes und aktives Abo
  J1  zweites dealer-Konto je Firma ueber Rollenaenderung moeglich
  J2  Sucher-Loeschung liess offene Abo-Anfragen zurueck; Reset-Loeschung traf nie
  J3  Sucher-Liste lieferte "alles ausser Passwort"
  K1  Selbst-Registrierung fail-open ohne APP_ENV
  K2  JWT_SECRET fehlte -> Zufallswert je Prozess (Load Balancer!)
  K3  MFA-Schritt verbrauchte Passwort-Login-Versuche
  K4  Passwort-Reset: alte Links vor dem Versand entwertet; kein Limit je Konto
  K5  Freischaltungs-Status frei waehlbar
  L1  Frontend: abgemeldeter Tab uebernahm das zuletzt angemeldete Konto
      (Jest: frontend/src/lib/sitzung.test.js)

HTTP-Teile brauchen das Backend auf TEST_BASE_URL mit SELF_SIGNUP=true.
"""
import inspect
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "RundeElf123!"
MAIL = "e2etest-mail.de"
BACKEND = Path(__file__).resolve().parents[1]


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _q(url):
    return parse_qs(urlparse(url).query)


def _abo(dealer_id, user_id, **extra):
    doc = {
        "id": str(uuid.uuid4()), "dealer_id": dealer_id,
        "subject_user_id": user_id, "plan": "monthly", "status": "active",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()}
    doc.update(extra)
    _db().subscriptions.insert_one(doc)
    return doc


# =====================================================================
#                          Einheitentests
# =====================================================================
def test_h1_sortierung_kommt_bei_beiden_portalen_an():
    from mobile_service import DEFAULT_RULES, build_search_url as mobile
    from autoscout_service import build_search_url as autoscout
    v = {"make": "BMW", "make_label": "BMW", "model": "M3", "model_label": "M3",
         "mileage": 50000, "power_kw": 100}
    for sort, mo, asc in [
        ("mileage_asc", {"sb": ["ml"], "od": ["up"]}, {"sort": ["mileage"], "desc": ["0"]}),
        ("first_registration_desc", {"sb": ["fr"], "od": ["down"]}, {"sort": ["year"], "desc": ["1"]}),
        ("price_desc", {"sb": ["p"], "od": ["down"]}, {"sort": ["price"], "desc": ["1"]}),
        ("relevance", {"sb": ["rel"]}, {"sort": ["standard"], "desc": ["0"]}),
    ]:
        rules = {**DEFAULT_RULES, "sort": sort}
        qm, qa = _q(mobile(v, rules)), _q(autoscout(v, rules))
        for k, val in mo.items():
            assert qm.get(k) == val, (sort, k, qm.get(k))
        if "od" not in mo:
            assert "od" not in qm
        for k, val in asc.items():
            assert qa.get(k) == val, (sort, k, qa.get(k))
    # Standard bleibt "Preis aufsteigend" (auch ohne sort-Schluessel)
    qm = _q(mobile(v, {k: v_ for k, v_ in DEFAULT_RULES.items() if k != "sort"}))
    assert qm.get("sb") == ["p"] and qm.get("od") == ["up"]


def test_h2_radius_ist_kein_regelschluessel_mehr():
    from regeln import regeln_validieren
    from mobile_service import DEFAULT_RULES, DEFAULT_EXPORT_RULES
    assert "radius" not in DEFAULT_RULES and "radius" not in DEFAULT_EXPORT_RULES
    assert "radius" not in regeln_validieren({"radius": {"mode": "km", "km": 50}})


def test_h3_autoscout_kategorie_und_tueren():
    from autoscout_service import build_search_url, regeln_nicht_abgebildet
    rules = {"category": {"mode": "exact"}, "doors": {"mode": "exact"},
             "country": {"mode": "exact", "codes": ["DE"]}}
    v = {"make": "BMW", "make_label": "BMW", "model": "X5", "category": "OffRoad",
         "doors": "FOUR_OR_FIVE"}
    q = _q(build_search_url(v, rules))
    assert q.get("body") == ["4"], q
    assert q.get("doorfrom") == ["4"] and q.get("doorto") == ["5"], q
    assert regeln_nicht_abgebildet(v, rules) == []
    # Kategorie ohne AutoScout-Entsprechung -> kein Filter, aber Hinweis
    v2 = {**v, "category": "OtherCar", "category_label": "Sonstiges", "displacement": 1998}
    q2 = _q(build_search_url(v2, rules))
    assert "body" not in q2
    hinweise = regeln_nicht_abgebildet(v2, {**rules, "displacement": {"mode": "tolerance", "value": 100},
                                            "features": {"navigation": {"mode": "always"}},
                                            "climatisation": {"mode": "always"}})
    text = " ".join(hinweise)
    assert "Kategorie" in text and "Hubraum" in text and "Navigation" in text and "Klima" in text


def test_h4_laender_zentral_und_autoscout_luecke_mit_hinweis():
    from regeln import RegelFehler, regeln_validieren, laender_ohne_autoscout
    from autoscout_service import build_search_url, regeln_nicht_abgebildet
    with pytest.raises(RegelFehler, match="XX"):
        regeln_validieren({"country": {"mode": "exact", "codes": ["XX"]}})
    ok = regeln_validieren({"country": {"mode": "exact", "codes": ["ch", "DE", "de"]}})
    assert ok["country"]["codes"] == ["CH", "DE"]
    with pytest.raises(RegelFehler, match="mindestens ein Land"):
        regeln_validieren({"country": {"mode": "exact", "codes": []}})
    v = {"make": "BMW", "make_label": "BMW"}
    nur_ch = {"country": {"mode": "exact", "codes": ["CH"]}}
    assert laender_ohne_autoscout(nur_ch) == ["CH"]
    assert "cy" not in _q(build_search_url(v, nur_ch))
    assert any("ALLEN" in h for h in regeln_nicht_abgebildet(v, nur_ch))
    gemischt = {"country": {"mode": "exact", "codes": ["DE", "CH"]}}
    assert _q(build_search_url(v, gemischt)).get("cy") == ["D"]
    assert any("nur in DE" in h for h in regeln_nicht_abgebildet(v, gemischt))
    assert regeln_nicht_abgebildet(v, {"country": {"mode": "all"}}) == []


def test_h5_nichts_wird_still_gestutzt():
    from regeln import RegelFehler, regeln_validieren, LAENDER_MOBILE
    with pytest.raises(RegelFehler, match="hoechstens 40"):
        regeln_validieren({"country": {"mode": "exact", "codes": list(LAENDER_MOBILE)[:41]}})
    with pytest.raises(RegelFehler, match="result_count"):
        regeln_validieren({"result_count": 50})
    assert regeln_validieren({"result_count": 20})["result_count"] == 20
    assert regeln_validieren({"result_count": "4"})["result_count"] == 4


def test_h6_ausstattung_nur_bekannte_namen_und_exact_erreichbar():
    from regeln import RegelFehler, regeln_validieren
    with pytest.raises(RegelFehler, match="panorama"):
        regeln_validieren({"features": {"panorama": {"mode": "always"}}})
    assert regeln_validieren({"features": {"navigation": {"mode": "exact"}}}) == \
        {"features": {"navigation": {"mode": "exact"}}}
    assert regeln_validieren({"climatisation": {"mode": "exact"}})["climatisation"]["mode"] == "exact"


def test_k1_selbst_registrierung_fail_closed(monkeypatch):
    from routes.auth import _self_signup_enabled
    for k in ("SELF_SIGNUP", "APP_ENV"):
        monkeypatch.delenv(k, raising=False)
    assert _self_signup_enabled() is False, "ohne APP_ENV muss die Registrierung AUS sein"
    monkeypatch.setenv("APP_ENV", "production")
    assert _self_signup_enabled() is False
    monkeypatch.setenv("SELF_SIGNUP", "true")
    assert _self_signup_enabled() is True
    monkeypatch.setenv("SELF_SIGNUP", "false")
    monkeypatch.setenv("APP_ENV", "development")
    assert _self_signup_enabled() is False
    monkeypatch.delenv("SELF_SIGNUP")
    assert _self_signup_enabled() is True
    # Team-Verwaltung durch den Chef folgt derselben Regel (keine zweite Kopie)
    import routes.team as t
    assert "_self_signup_enabled" in inspect.getsource(t._chef_verwaltung_erlaubt)


def test_k2_fehlendes_jwt_secret_verhindert_den_start():
    env = {**os.environ, "JWT_SECRET": "", "APP_ENV": ""}
    lauf = dict(cwd=str(BACKEND), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60)
    r = subprocess.run([sys.executable, "-c", "import auth"], **lauf)
    assert r.returncode != 0, "ohne JWT_SECRET und ohne Entwicklungs-APP_ENV darf auth nicht laden"
    assert "JWT_SECRET" in (r.stderr or "") + (r.stdout or "")
    env["APP_ENV"] = "development"
    r = subprocess.run([sys.executable, "-c", "import auth"], **lauf)
    assert r.returncode == 0, (r.stderr or "")[-400:]


def test_k3_k4_k5_quelle():
    import routes.auth as a
    import routes.admin as adm
    src_mfa = inspect.getsource(a.login_mfa)
    assert "login_mfa_limiter.check" in src_mfa and "login_limiter.check" not in src_mfa
    # K6: Fehlversuche atomar ($inc), kein "lesen, +1, schreiben" mehr
    assert '"$inc": {"mfa.fehlversuche": 1}' in src_mfa
    assert 'int(m.get("fehlversuche", 0)) + 1' not in src_mfa
    assert a.login_mfa_limiter.name != a.login_limiter.name
    src_reset = inspect.getsource(a.password_reset_request)
    assert src_reset.index("send_email(") < src_reset.index("delete_many("), \
        "alte Reset-Links duerfen erst NACH erfolgreichem Versand entwertet werden"
    assert "reset_konto_limiter.check" in src_reset
    assert adm.PLAN_REQUEST_STATUS == {"offen", "erledigt", "abgelehnt"}
    assert "PLAN_REQUEST_STATUS" in inspect.getsource(adm.admin_close_plan_request)


def test_i1_j3_quelle():
    import routes.dealer as d
    import routes.team as t
    src = inspect.getsource(d.get_settings)
    assert "$exists" in src, "Backfill muss an 'Feld fehlt weiterhin' gebunden sein"
    src_list = inspect.getsource(t.list_sucher)
    assert '"password_hash": 0' not in src_list and '"email": 1' in src_list


# =====================================================================
#                          HTTP-Tests
# =====================================================================
@pytest.fixture(scope="module")
def welt():
    """Super-Admin (direkt gesaet), Firma mit Chef + Sucher (Chef hat Abo)."""
    import bcrypt
    dbx = _db()
    admin_mail = f"r11_admin_{SUF}@{MAIL}"
    dbx.users.insert_one({
        "id": f"r11adm_{SUF}", "email": admin_mail, "role": "admin", "active": True,
        "dealer_id": None, "is_super_admin": True,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    r = requests.post(f"{API}/auth/login", json={"email": admin_mail, "password": PW}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    A = {"Authorization": f"Bearer {r.json()['token']}"}

    r = requests.post(f"{API}/auth/register", json={
        "email": f"r11_chef_{SUF}@{MAIL}", "password": PW,
        "company_name": f"Runde11 {SUF}", "contact_person": "R E", "phone": "0511 11"}, timeout=30)
    assert r.status_code == 200, f"Backend braucht SELF_SIGNUP=true: {r.text[:200]}"
    C = {"Authorization": f"Bearer {r.json()['token']}"}
    chef = requests.get(f"{API}/auth/me", headers=C, timeout=30).json()["user"]
    _abo(chef["dealer_id"], chef["id"])

    r = requests.post(f"{API}/dealer/sucher", headers=C, json={
        "email": f"r11_sucher_{SUF}@{MAIL}", "password": PW,
        "first_name": "Su", "last_name": "Cher"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    sucher_id = r.json()["sucher_id"]
    z = {"A": A, "C": C, "chef": chef, "sucher_id": sucher_id, "dealer_id": chef["dealer_id"]}
    yield z
    for coll in ("subscriptions", "activity_logs", "plan_requests", "password_resets",
                 "vehicles", "generated_pdfs", "appointments", "resale_listings"):
        dbx[coll].delete_many({"dealer_id": z["dealer_id"]})
    dbx.dealers.update_one({"id": z["dealer_id"]}, {"$unset": {"interner_vermerk_r12": ""}})
    dbx.password_resets.delete_many({"user_id": {"$in": [chef["id"], sucher_id]}})
    dbx.plan_requests.delete_many({"subject_user_id": {"$in": [chef["id"], sucher_id]}})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.dealers.delete_many({"id": z["dealer_id"]})


def _logs(dealer_id, action):
    return _db().activity_logs.count_documents({"dealer_id": dealer_id, "action": action})


def test_h4_h5_speichern_lehnt_unbekanntes_land_und_zu_viel_ab(welt):
    from mobile_service import DEFAULT_RULES
    r = requests.put(f"{API}/dealer/settings", headers=welt["C"], json={
        "comparison_rules": {**DEFAULT_RULES, "country": {"mode": "exact", "codes": ["XX"]}}}, timeout=30)
    assert r.status_code == 400 and "XX" in r.text, r.text[:200]
    r = requests.put(f"{API}/dealer/settings", headers=welt["C"], json={
        "comparison_rules": {**DEFAULT_RULES, "result_count": 50}}, timeout=30)
    assert r.status_code == 400 and "result_count" in r.text, r.text[:200]


def test_i2_chef_einstellungen_und_profilwechsel_im_protokoll(welt):
    from mobile_service import DEFAULT_RULES
    d = welt["dealer_id"]
    vor_e, vor_p = _logs(d, "einstellungen.firma.geaendert"), _logs(d, "einstellungen.profil.gewechselt")
    r = requests.put(f"{API}/dealer/settings", headers=welt["C"], json={
        "comparison_rules": {**DEFAULT_RULES, "sort": "mileage_asc"}}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert r.json()["comparison_rules"]["sort"] == "mileage_asc"
    r = requests.put(f"{API}/dealer/active-profile", headers=welt["C"],
                     json={"active_profile": "export"}, timeout=30)
    assert r.status_code == 200
    assert _logs(d, "einstellungen.firma.geaendert") == vor_e + 1
    assert _logs(d, "einstellungen.profil.gewechselt") == vor_p + 1
    eintrag = _db().activity_logs.find_one({"dealer_id": d, "action": "einstellungen.firma.geaendert"},
                                           sort=[("created_at", -1)])
    assert eintrag["meta"]["felder"] == ["comparison_rules"]


def test_h7_h4_manuelle_suche_nennt_profil_und_autoscout_luecke(welt):
    from mobile_service import DEFAULT_EXPORT_RULES
    # aktiv ist jetzt "export" (voriger Test) — Export-Regeln mit Land CH
    r = requests.put(f"{API}/dealer/settings", headers=welt["C"], json={
        "export_rules": {**DEFAULT_EXPORT_RULES, "country": {"mode": "exact", "codes": ["CH"]}}},
        timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.post(f"{API}/manual/search", headers=welt["C"], json={"make": "BMW", "model": "M3"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["profil"] == "export"
    assert "cn" in _q(d["mobile_url"]) and _q(d["mobile_url"])["cn"] == ["CH"]
    assert "cy" not in _q(d["autoscout_url"])
    assert any("CH" in h and "AutoScout24" in h for h in d["hinweise"]), d["hinweise"]
    # zurueck auf Inland: Profil in der Antwort folgt dem Wechsel
    requests.put(f"{API}/dealer/active-profile", headers=welt["C"], json={"active_profile": "inland"}, timeout=30)
    r = requests.post(f"{API}/manual/search", headers=welt["C"], json={"make": "BMW"}, timeout=30)
    assert r.status_code == 200 and r.json()["profil"] == "inland"
    assert _q(r.json()["autoscout_url"]).get("cy") == ["D"]


def test_i3_abo_anzeige_ignoriert_ersetztes_abo(welt):
    chef = welt["chef"]
    # Ein ERSETZTES Abo mit spaeterem created_at und fernem Ablauf darf die
    # Anzeige nicht mehr verfaelschen.
    _abo(welt["dealer_id"], chef["id"], status="ersetzt",
         expires_at="2099-01-01T00:00:00+00:00",
         created_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    r = requests.get(f"{API}/dealer/subscription", headers=welt["C"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert d["active"] is True and d["raw_status"] == "active"
    assert not str(d["expires_at"]).startswith("2099"), d
    assert d["days_remaining"] is not None and d["days_remaining"] <= 1


def test_j1_zweiter_chef_je_firma_nur_als_chefwechsel(welt):
    A, sid, chef = welt["A"], welt["sucher_id"], welt["chef"]
    r = requests.put(f"{API}/admin/users/{sid}", headers=A, json={"role": "dealer"}, timeout=30)
    assert r.status_code == 400 and "Hauptaccount" in r.text, r.text[:200]
    assert _db().users.find_one({"id": sid})["role"] == "sucher"
    r = requests.put(f"{API}/admin/users/{sid}", headers=A,
                     json={"role": "dealer", "chef_wechsel": True}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert _db().users.find_one({"id": sid})["role"] == "dealer"
    alt = _db().users.find_one({"id": chef["id"]})
    assert alt["role"] == "sucher" and alt.get("current_session_id") is None
    assert _db().dealers.find_one({"id": welt["dealer_id"]})["user_id"] == sid
    assert _db().users.count_documents({"dealer_id": welt["dealer_id"], "role": "dealer"}) == 1
    # Chefwechsel zurueck, damit die uebrigen Tests den alten Chef behalten
    r = requests.put(f"{API}/admin/users/{chef['id']}", headers=A,
                     json={"role": "dealer", "chef_wechsel": True}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.post(f"{API}/auth/login", json={"email": chef["email"], "password": PW}, timeout=30)
    assert r.status_code == 200
    welt["C"] = {"Authorization": f"Bearer {r.json()['token']}"}


def test_j2_sucher_loeschen_raeumt_anfragen_und_resets_auf(welt):
    sid = welt["sucher_id"]
    dbx = _db()
    dbx.plan_requests.insert_one({
        "id": f"r11req_{SUF}", "type": "sucher_abo", "subject_user_id": sid,
        "dealer_id": welt["dealer_id"], "status": "offen",
        "created_at": datetime.now(timezone.utc).isoformat()})
    dbx.password_resets.insert_one({
        "id": f"r11rst_{SUF}", "user_id": sid, "token_hash": "x", "used": False,
        "expires_at": "2099-01-01T00:00:00+00:00",
        "created_at": datetime.now(timezone.utc).isoformat()})
    r = requests.delete(f"{API}/dealer/sucher/{sid}", headers=welt["C"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert dbx.plan_requests.count_documents({"subject_user_id": sid, "status": "offen"}) == 0
    assert dbx.password_resets.count_documents({"user_id": sid}) == 0


def test_k5_freischaltung_status_fest(welt):
    dbx = _db()
    rid = f"r11st_{SUF}"
    dbx.plan_requests.insert_one({"id": rid, "type": "zugang", "status": "offen",
                                  "dealer_id": welt["dealer_id"],
                                  "created_at": datetime.now(timezone.utc).isoformat()})
    r = requests.put(f"{API}/admin/plan-requests/{rid}", headers=welt["A"],
                     json={"status": "offen "}, timeout=30)
    assert r.status_code == 400, r.text[:200]
    assert dbx.plan_requests.find_one({"id": rid})["status"] == "offen"
    r = requests.put(f"{API}/admin/plan-requests/{rid}", headers=welt["A"],
                     json={"status": "abgelehnt"}, timeout=30)
    assert r.status_code == 200
    assert dbx.plan_requests.find_one({"id": rid})["status"] == "abgelehnt"


# =====================================================================
#   Runde 12 (Admin/Rollen + Sucher-Trennung), gleiche Welt
#   M1 Rollenaenderung beendet die Sitzung     M2 Chef nie direkt herabstufen
#   M3 Freischaltungen Super-Admin-only         M4 keine Sitzungs-ID in Admin-Antworten
#   M5 Termin nur mit Vertrag im eigenen Bereich M6 Akte ohne Verkaufsdaten/fremde Historie
#   M7 Sucher sehen nur ihre Einstellungsfelder M8 Loesch-Audit vor dem ersten Schritt
# =====================================================================
def _login(mail):
    r = requests.post(f"{API}/auth/login", json={"email": mail, "password": PW}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def sucher2(welt):
    r = requests.post(f"{API}/dealer/sucher", headers=welt["C"], json={
        "email": f"r12_sucher_{SUF}@{MAIL}", "password": PW,
        "first_name": "Zwei", "last_name": "Ter"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    sid = r.json()["sucher_id"]
    return {"id": sid, "email": f"r12_sucher_{SUF}@{MAIL}", "S": _login(f"r12_sucher_{SUF}@{MAIL}")}


def test_m1_rollenaenderung_beendet_die_sitzung(welt, sucher2):
    assert requests.get(f"{API}/auth/me", headers=sucher2["S"], timeout=30).status_code == 200
    r = requests.put(f"{API}/admin/users/{sucher2['id']}", headers=welt["A"],
                     json={"role": "b2b_buyer"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert requests.get(f"{API}/auth/me", headers=sucher2["S"], timeout=30).status_code == 401, \
        "altes Token darf nach Rollenaenderung nicht weiterleben"
    assert _db().users.find_one({"id": sucher2["id"]}).get("current_session_id") is None
    r = requests.put(f"{API}/admin/users/{sucher2['id']}", headers=welt["A"],
                     json={"role": "sucher"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    sucher2["S"] = _login(sucher2["email"])


def test_m2_chef_wird_nie_direkt_herabgestuft(welt):
    for ziel in ("sucher", "admin", "b2b_buyer"):
        r = requests.put(f"{API}/admin/users/{welt['chef']['id']}", headers=welt["A"],
                         json={"role": ziel}, timeout=30)
        # "admin" scheitert schon an "es gibt nur den Super-Admin", der Rest am Chef-Schutz
        assert r.status_code == 400 and ("Hauptaccount" in r.text or "Super-Admin" in r.text), (ziel, r.text[:200])
    assert _db().users.find_one({"id": welt["chef"]["id"]})["role"] == "dealer"


def test_m3_m4_normaler_admin(welt, sucher2):
    import bcrypt
    mail = f"r12_admin_{SUF}@{MAIL}"
    _db().users.insert_one({
        "id": f"r12adm_{SUF}", "email": mail, "role": "admin", "active": True,
        "dealer_id": None, "is_super_admin": False,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    N = _login(mail)
    # Beschluss 06.09.2026: Es gibt nur den Super-Admin. Ein Konto mit Rolle
    # admin ohne is_super_admin kommt in KEINE Betreiber-Route mehr.
    for pfad in ("/admin/plan-requests?status=offen", "/admin/users",
                 f"/admin/users/{welt['chef']['id']}/contracts", "/admin/betrieb"):
        assert requests.get(f"{API}{pfad}", headers=N, timeout=30).status_code == 403, pfad
    assert requests.get(f"{API}/admin/plan-requests?status=offen", headers=welt["A"], timeout=30).status_code == 200
    # Keine Sitzungs-ID in Betreiber-Antworten
    r = requests.get(f"{API}/admin/users", headers=welt["A"], timeout=60)
    assert r.status_code == 200
    users = r.json() if isinstance(r.json(), list) else r.json().get("items") or r.json().get("users") or []
    assert users and all("current_session_id" not in u for u in users)
    r = requests.get(f"{API}/admin/users/{welt['chef']['id']}/contracts", headers=welt["A"], timeout=30)
    assert r.status_code == 200 and "current_session_id" not in str(r.json().get("user", r.json()))
    # Rolle admin ist nicht mehr vergebbar; Altkonten erscheinen im Betrieb
    r = requests.put(f"{API}/admin/users/{sucher2['id']}", headers=welt["A"],
                     json={"role": "admin"}, timeout=30)
    assert r.status_code == 400 and "Super-Admin" in r.text, r.text[:200]
    b = requests.get(f"{API}/admin/betrieb", headers=welt["A"], timeout=60).json()
    assert mail in b.get("admin_konten_ohne_super_admin", []), b.get("admin_konten_ohne_super_admin")


def _fahrzeug_mit_chef_vertrag(welt, owner_user_id=None):
    """Fahrzeug der Firma + Vertrag des CHEFS (einmal je Modul). Runde 16:
    das Fahrzeug gehoert dem uebergebenen Konto (owner_user_id), damit der
    Sucher es ueberhaupt sieht — der Vertrag bleibt der des Chefs."""
    if "vid" in welt:
        return welt["vid"], welt["cid"]
    dbx = _db()
    vid, cid = f"v_r12_{SUF}", f"c_r12_{SUF}"
    dbx.vehicles.insert_one({"id": vid, "dealer_id": welt["dealer_id"], "mobile_ad_id": f"r12{SUF}",
                             "owner_user_id": owner_user_id,
                             "data": {"make_label": "BMW", "model_label": "M3"},
                             "lifecycle": "verglichen", "status": "verglichen",
                             "created_at": datetime.now(timezone.utc).isoformat()})
    dbx.generated_pdfs.insert_one({"id": cid, "dealer_id": welt["dealer_id"], "user_id": welt["chef"]["id"],
                                   "vehicle_id": vid, "status": "erstellt",
                                   "created_at": datetime.now(timezone.utc).isoformat()})
    welt["vid"], welt["cid"] = vid, cid
    return vid, cid


def test_m5_termin_nur_mit_vertrag_im_eigenen_bereich(welt, sucher2):
    vid, cid = _fahrzeug_mit_chef_vertrag(welt, owner_user_id=sucher2["id"])
    body = {"title": "Abholung", "vehicle_id": vid, "contract_id": cid,
            "pickup_date": "2026-10-01", "pickup_time": "10:00", "pickup_address": "Hannover"}
    r = requests.post(f"{API}/appointments", headers=sucher2["S"], json=body, timeout=30)
    assert r.status_code == 404, f"Sucher darf keinen Termin an den Chef-Vertrag haengen: {r.text[:200]}"
    r = requests.post(f"{API}/appointments", headers=sucher2["S"],
                      json={k: v for k, v in body.items() if k != "contract_id"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    appt_id = r.json()["id"]
    r = requests.put(f"{API}/appointments/{appt_id}", headers=sucher2["S"],
                     json={"contract_id": cid}, timeout=30)
    assert r.status_code == 404, f"auch nachtraeglich nicht: {r.text[:200]}"
    # Runde 15 (Nr. 6): hoechstens EIN offener Abholtermin je Fahrzeug — der
    # Chef bekommt fuer dasselbe Auto keinen zweiten, haengt seinen Vertrag
    # aber an den bestehenden Termin (Chef darf jeden Termin aendern).
    r = requests.post(f"{API}/appointments", headers=welt["C"], json=body, timeout=30)
    assert r.status_code == 409, r.text[:200]
    r = requests.put(f"{API}/appointments/{appt_id}", headers=welt["C"],
                     json={"contract_id": cid}, timeout=30)
    assert r.status_code == 200, r.text[:200]


def test_m6_akte_ohne_verkaufsdaten_und_fremde_historie(welt, sucher2):
    dbx = _db()
    vid, _cid = _fahrzeug_mit_chef_vertrag(welt, owner_user_id=sucher2["id"])
    # Eigene Aktion des Suchers und eine Chef-Aktion am Fahrzeug
    for uid in (sucher2["id"], welt["chef"]["id"]):
        dbx.activity_logs.insert_one({"id": str(uuid.uuid4()), "dealer_id": welt["dealer_id"],
                                      "user_id": uid, "action": "test.r12", "ref": vid, "meta": {},
                                      "created_at": datetime.now(timezone.utc).isoformat()})
    dbx.resale_listings.insert_one({"id": f"rl_r12_{SUF}", "dealer_id": welt["dealer_id"], "vehicle_id": vid,
                                    "status": "entwurf", "einkaufspreis": 12345,
                                    "created_at": datetime.now(timezone.utc).isoformat()})
    r = requests.get(f"{API}/vehicles/{vid}/akte", headers=sucher2["S"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert d["listings"] == [], "Verkaufsdaten sind Chefsache"
    assert all(h.get("user_id") == sucher2["id"] for h in d["history"]), \
        [h.get("action") for h in d["history"]]
    r = requests.get(f"{API}/vehicles/{vid}/akte", headers=welt["C"], timeout=30)
    assert r.status_code == 200 and len(r.json()["listings"]) == 1
    assert any(h.get("user_id") == welt["chef"]["id"] for h in r.json()["history"])


def test_m7_sucher_sieht_nur_seine_einstellungsfelder(welt, sucher2):
    _db().dealers.update_one({"id": welt["dealer_id"]}, {"$set": {"interner_vermerk_r12": "geheim"}})
    for pfad, key in (("/dealer/settings", None), ("/auth/me", "dealer")):
        r = requests.get(f"{API}{pfad}", headers=sucher2["S"], timeout=30)
        assert r.status_code == 200, r.text[:200]
        d = r.json()[key] if key else r.json()
        assert "interner_vermerk_r12" not in d and "user_id" not in d
        assert d.get("company_name") and "active_profile" in d and "comparison_rules" in d
    r = requests.get(f"{API}/dealer/settings", headers=welt["C"], timeout=30)
    assert r.json().get("interner_vermerk_r12") == "geheim"


def test_m8_loesch_audit_vor_dem_ersten_schritt():
    import routes.admin as adm
    src = inspect.getsource(adm.admin_delete_user)
    assert src.index("admin.firma.loeschung.gestartet") < src.index("for coll in _COMPANY_COLLECTIONS")
