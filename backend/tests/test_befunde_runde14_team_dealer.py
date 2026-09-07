# -*- coding: utf-8 -*-
"""Nachpruefung Runde 14 (09/2026) — Gruppe team_dealer.

  31   _current_period: kaputtes period_start -> Paket ungueltig statt 500
  33   Chef-Anfrage fuer Sucher-Abo war nicht idempotent
  56   Eigene Abo-Anfrage: find_one+insert nicht rennfest (16 parallel -> 16 Zeilen)
  57   Verkaufspaket-Anfrage ohne Dublettenpruefung (latent, VERKAUF_KOSTENLOS)
  104  Abo-Anfrage fuer deaktivierten Sucher -> 400
  72   /dealer/sucher: to_list(100) -> 1000
  70   Kuendigung traf ein anderes Abo als die Anzeige
  71   Anzeige mischte Felder aus persoenlichem und Firmenabo
  96   Logo-Upload: DB-Fehler liess Datei als Waise liegen; altes Logo nie geloescht
  103  logo_url: fremde http(s)-Hosts (Tracking-Pixel in Vertragsmails) abgelehnt
  116  LogoUploadIn.logo_b64 mit Obergrenze (422 vor dem Decode)
  92   Sucher-Statistik: $match zusaetzlich auf dealer_id

Einheitentests laufen ohne Server (teils gegen ein eigenes Mongo-Schema).
HTTP-Teile laufen nur mit RUNDE14_HTTP=1 gegen das Backend auf TEST_BASE_URL
(SELF_SIGNUP=true, Mock-Anbieter wie in CI).
"""
import asyncio
import inspect
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "RundeVierzehn14!"
MAIL = "e2etest-mail.de"
JETZT = datetime.now(timezone.utc)


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _kopf(token):
    return {"Authorization": f"Bearer {token}"}


def _abo_doc(dealer_id, user_id=None, **extra):
    doc = {
        "id": str(uuid.uuid4()), "dealer_id": dealer_id,
        "plan": "monthly", "status": "active",
        "expires_at": (JETZT + timedelta(days=1)).isoformat(),
        "created_at": JETZT.isoformat()}
    if user_id is not None:
        doc["subject_user_id"] = user_id
    doc.update(extra)
    return doc


# =====================================================================
#            Mini-Mongo im Speicher fuer Einheitentests ohne Server
# =====================================================================
def _wert(doc, pfad):
    """Punktpfad wie in Mongo ("settings_override.logo_url")."""
    aktuell = doc
    for teil in pfad.split("."):
        if not isinstance(aktuell, dict):
            return None
        aktuell = aktuell.get(teil)
    return aktuell


def _passt(doc, filt):
    for k, v in filt.items():
        if k == "$or":
            if not any(_passt(doc, teil) for teil in v):
                return False
            continue
        wert = _wert(doc, k)
        if isinstance(v, dict) and any(str(op).startswith("$") for op in v):
            for op, arg in v.items():
                if op == "$ne" and wert == arg:
                    return False
                if op == "$exists" and (k in doc) != bool(arg):
                    return False
                if op == "$in" and wert not in arg:
                    return False
                if op == "$gte" and not (wert is not None and wert >= arg):
                    return False
        elif wert != v:
            return False
    return True


class _Coll:
    def __init__(self, docs=None, fehler=None):
        self.docs = [dict(d) for d in (docs or [])]
        self.fehler = fehler
        self.updates = []

    def _treffer(self, filt, sort=None):
        rows = [d for d in self.docs if _passt(d, filt)]
        if sort:
            feld, richtung = sort[0]
            rows.sort(key=lambda d: d.get(feld) or "", reverse=(richtung == -1))
        return rows

    async def find_one(self, filt, projection=None, sort=None):
        rows = self._treffer(filt, sort)
        if not rows:
            return None
        return {k: v for k, v in rows[0].items() if k != "_id"}

    async def update_one(self, filt, update, upsert=False):
        if self.fehler:
            raise self.fehler
        self.updates.append((filt, update))
        for d in self.docs:
            if _passt(d, filt):
                for k, v in (update.get("$set") or {}).items():
                    ziel = d
                    *pfad, letzter = k.split(".")
                    for teil in pfad:
                        ziel = ziel.setdefault(teil, {})
                    ziel[letzter] = v
                break

    async def count_documents(self, filt):
        return len(self._treffer(filt))


class _Db:
    def __init__(self, **colls):
        for name, docs in colls.items():
            setattr(self, name, docs if isinstance(docs, _Coll) else _Coll(docs))

    def __getattr__(self, name):
        coll = _Coll()
        setattr(self, name, coll)
        return coll


def _lauf(coro):
    return asyncio.run(coro)


# =====================================================================
#                              Nr. 31
# =====================================================================
def test_31_current_period_meldet_unlesbaren_start_als_valueerror():
    from routes.team import _current_period
    with pytest.raises(ValueError):
        _current_period("invalid")
    with pytest.raises(ValueError):
        _current_period(12345)
    # Fehlender Wert = heute, Z-Suffix wird verstanden.
    key, start, ende = _current_period(None)
    assert len(key) == 8 and start < ende
    key2, _, _ = _current_period("2026-01-01T00:00:00Z")
    assert len(key2) == 8


def test_31_sale_plan_status_kaputter_zeitraum_kein_500(monkeypatch):
    import routes.team as team
    monkeypatch.setattr(team, "VERKAUF_KOSTENLOS", False)
    monkeypatch.setattr(team, "db", _Db(dealers=[
        {"id": "d1", "sale_plan": {"tier": "s5", "period_start": "invalid"}}]))
    st = _lauf(team.get_sale_plan_status("d1"))
    assert st["active"] is False and st["tier"] == "s5"
    assert "ungueltig" in st.get("fehler", "")
    assert st["remaining"] == 0 and st["quota"] == 0
    # Gesunder Datensatz bleibt aktiv (Regression).
    monkeypatch.setattr(team, "db", _Db(dealers=[
        {"id": "d2", "sale_plan": {"tier": "s5", "period_start": JETZT.isoformat()},
         "quota_usage": {}}], resale_listings=_Coll()))
    st = _lauf(team.get_sale_plan_status("d2"))
    assert st["active"] is True and st["quota"] == 5


# =====================================================================
#                          Nr. 33 / 56 / 57
# =====================================================================
def _motor_db(name):
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=3000)[name]


@pytest.fixture
def eigenes_schema(monkeypatch):
    """Eigene Datenbank mit den erwarteten Teil-Unique-Indizes (die
    produktiv server.ensure_indexes anlegt), damit der Upsert-Helfer
    rennfest geprueft werden kann, ohne die Entwicklungs-DB anzufassen."""
    import routes.team as team
    name = f"r14_unit_{SUF}"
    try:
        _db().client.admin.command("ping")
    except Exception:
        pytest.skip("Mongo nicht erreichbar")
    sync = _db().client[name]
    sync.plan_requests.create_index(
        [("type", 1), ("subject_user_id", 1)], unique=True,
        name="uniq_offene_sucher_abo_anfrage",
        partialFilterExpression={"type": "sucher_abo", "status": "offen"})
    sync.plan_requests.create_index(
        [("type", 1), ("dealer_id", 1)], unique=True,
        name="uniq_offene_verkaufspaket_anfrage",
        partialFilterExpression={"type": "verkaufspaket", "status": "offen"})
    yield name
    _db().client.drop_database(name)


def test_56_upsert_16_parallel_genau_eine_offene_anfrage(eigenes_schema, monkeypatch):
    import routes.team as team
    sid = f"s-{SUF}"

    async def lauf():
        monkeypatch.setattr(team, "db", _motor_db(eigenes_schema))
        aufrufe = [team._offene_anfrage_upsert(
            {"type": "sucher_abo", "subject_user_id": sid, "status": "offen"},
            {"id": str(uuid.uuid4()), "dealer_id": "dA", "created_at": team.now_iso()},
            {"wanted_plan": "monthly", "updated_at": team.now_iso()})
            for _ in range(16)]
        ergebnisse = await asyncio.gather(*aufrufe)
        anzahl = await team.db.plan_requests.count_documents(
            {"type": "sucher_abo", "subject_user_id": sid, "status": "offen"})
        return ergebnisse, anzahl

    ergebnisse, anzahl = _lauf(lauf())
    assert anzahl == 1, anzahl
    assert sum(1 for _, neu in ergebnisse if neu) == 1, ergebnisse
    ids = {d["id"] for d, _ in ergebnisse}
    assert len(ids) == 1, ids


def test_57_upsert_verkaufspaket_aktualisiert_wunsch_statt_doppel(eigenes_schema, monkeypatch):
    import routes.team as team

    async def lauf():
        monkeypatch.setattr(team, "db", _motor_db(eigenes_schema))
        schl = {"type": "verkaufspaket", "dealer_id": "dB", "status": "offen"}
        d1, neu1 = await team._offene_anfrage_upsert(
            schl, {"id": "a1", "created_at": "x"}, {"wanted_tier": "s10", "message": "eins"})
        d2, neu2 = await team._offene_anfrage_upsert(
            schl, {"id": "a2", "created_at": "y"}, {"wanted_tier": "s20", "message": "zwei"})
        # Erledigte Anfrage blockiert keine neue.
        await team.db.plan_requests.update_one({"id": "a1"}, {"$set": {"status": "erledigt"}})
        d3, neu3 = await team._offene_anfrage_upsert(
            schl, {"id": "a3", "created_at": "z"}, {"wanted_tier": "s5", "message": "drei"})
        alle = await team.db.plan_requests.find({"dealer_id": "dB"}, {"_id": 0}).to_list(None)
        return (d1, neu1), (d2, neu2), (d3, neu3), alle

    (d1, neu1), (d2, neu2), (d3, neu3), alle = _lauf(lauf())
    assert neu1 is True and d1["id"] == "a1"
    assert neu2 is False and d2["id"] == "a1" and d2["wanted_tier"] == "s20"
    assert d2["created_at"] == "x", "setOnInsert darf Bestand nicht ueberschreiben"
    assert neu3 is True and d3["id"] == "a3"
    assert len(alle) == 2


def test_33_57_104_72_92_quelle():
    """Quellpruefung der Stellen, die nur per HTTP nachstellbar sind."""
    import routes.team as team
    q_sucher = inspect.getsource(team.sucher_abo_request)
    assert "_offene_anfrage_upsert" in q_sucher and "bereits_offen" in q_sucher
    assert "insert_one" not in q_sucher
    assert '"active": 1' in q_sucher and "deaktiviert" in q_sucher                   # 104
    q_paket = inspect.getsource(team.sale_plan_upgrade_request)
    assert '"type": "verkaufspaket"' in q_paket and "insert_one" not in q_paket      # 57
    q_eigen = inspect.getsource(team.eigenes_abo_anfrage)
    assert "_offene_anfrage_upsert" in q_eigen and "insert_one" not in q_eigen       # 56
    q_liste = inspect.getsource(team.list_sucher)
    assert ".to_list(1000)" in q_liste                                                # 72
    assert ".to_list(100)" not in q_liste.replace(".to_list(1000)", "")
    assert q_liste.count('"dealer_id": user["dealer_id"]') >= 4, "3x $match + Filter"  # 92


# =====================================================================
#                             Nr. 70 / 71
# =====================================================================
def _chef():
    return {"id": "u-chef", "dealer_id": "dX", "role": "dealer", "email": "c@x"}


def test_70_71_massgebliches_abo_waehlt_ein_dokument(monkeypatch):
    import routes.dealer as dealer
    from deps import sub_status_from_doc
    pers_aktiv = _abo_doc("dX", "u-chef", id="pers-aktiv", plan="monthly",
                          created_at=(JETZT - timedelta(days=1)).isoformat())
    pers_abgelaufen = _abo_doc("dX", "u-chef", id="pers-alt", plan="monthly",
                               expires_at=(JETZT - timedelta(days=5)).isoformat(),
                               created_at=(JETZT - timedelta(days=1)).isoformat())
    firma = _abo_doc("dX", None, id="firma", plan="yearly",
                     expires_at=(JETZT + timedelta(days=300)).isoformat(),
                     created_at=(JETZT - timedelta(days=60)).isoformat())
    fremd = _abo_doc("dX", "u-sucher", id="fremd", plan="yearly",
                     expires_at=(JETZT + timedelta(days=500)).isoformat(),
                     created_at=JETZT.isoformat())

    def wahl(docs, user=None):
        monkeypatch.setattr(dealer, "db", _Db(subscriptions=docs))
        return _lauf(dealer.massgebliches_abo(user or _chef()))

    # Chef: aktives persoenliches Abo schlaegt Firmenabo (Nr. 70).
    assert wahl([pers_aktiv, firma, fremd])["id"] == "pers-aktiv"
    # Chef: abgelaufenes persoenliches -> Firmenabo, KEINE gemischten Felder (Nr. 71).
    d = wahl([pers_abgelaufen, firma, fremd])
    assert d["id"] == "firma" and d["expires_at"] == firma["expires_at"]
    assert sub_status_from_doc(d)["active"] is True
    # Chef: nur abgelaufenes persoenliches -> dieses (inaktiv), nie das Sucher-Abo.
    d = wahl([pers_abgelaufen, fremd])
    assert d["id"] == "pers-alt" and sub_status_from_doc(d)["active"] is False
    # Ersetzte Abos zaehlen nicht (Runde 11 bleibt).
    ersetzt = dict(pers_aktiv, id="ersetzt", status="ersetzt",
                   created_at=(JETZT + timedelta(hours=1)).isoformat())
    assert wahl([pers_aktiv, ersetzt, firma])["id"] == "pers-aktiv"
    # Sucher: immer nur das persoenliche, auch wenn abgelaufen und Firma aktiv.
    sucher = {"id": "u-chef", "dealer_id": "dX", "role": "sucher"}
    assert wahl([pers_abgelaufen, firma], sucher)["id"] == "pers-alt"
    # Gar nichts -> None.
    assert wahl([fremd]) is None


def test_70_71_anzeige_und_kuendigung_nutzen_dieselbe_auswahl():
    import routes.dealer as dealer
    q_anzeige = inspect.getsource(dealer.dealer_subscription)
    q_kuend = inspect.getsource(dealer.dealer_cancel_subscription)
    assert "massgebliches_abo(user)" in q_anzeige and "massgebliches_abo(user)" in q_kuend
    assert "subscription_for" not in q_anzeige, "Status muss aus demselben Dokument kommen"
    assert "sub_status_from_doc(sub_doc)" in q_anzeige
    assert "db.subscriptions.find_one" not in q_kuend


# =====================================================================
#                              Nr. 103
# =====================================================================
def test_103_logo_url_nur_eigene_hosts(monkeypatch):
    from fastapi import HTTPException
    from routes.dealer import _validate_logo_url
    monkeypatch.setenv("FRONTEND_URL", "https://app.autoschnell.example/")
    monkeypatch.setenv("S3_PUBLIC_URL", "https://files.autoschnell.example/bucket")
    assert _validate_logo_url("") == ""
    assert _validate_logo_url(None) is None
    assert _validate_logo_url("/api/files/logo/d1/abc.png") == "/api/files/logo/d1/abc.png"
    assert _validate_logo_url("https://app.autoschnell.example/logo.png")
    assert _validate_logo_url("https://APP.autoschnell.example/logo.png")
    assert _validate_logo_url("https://files.autoschnell.example/bucket/logo/x.png")
    for fremd in ("https://tracker.example/x.png", "http://evil.example/p.gif",
                  "https://app.autoschnell.example.evil.example/x.png",
                  "https://user@tracker.example/x.png"):
        with pytest.raises(HTTPException) as e:
            _validate_logo_url(fremd)
        assert e.value.status_code == 400, fremd
    for schema in ("javascript:alert(1)", "data:image/png;base64,AAAA", "file:///etc/passwd"):
        with pytest.raises(HTTPException) as e:
            _validate_logo_url(schema)
        assert e.value.status_code == 400


def test_103_logo_url_standardhost_ohne_konfiguration(monkeypatch):
    from fastapi import HTTPException
    from routes.dealer import _validate_logo_url
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    monkeypatch.delenv("S3_PUBLIC_URL", raising=False)
    assert _validate_logo_url("http://localhost:3000/logo.png")
    with pytest.raises(HTTPException):
        _validate_logo_url("https://tracker.example/x.png")


# =====================================================================
#                              Nr. 116
# =====================================================================
def test_116_logo_b64_obergrenze_im_schema():
    from pydantic import ValidationError
    from routes.dealer import LogoUploadIn, LOGO_B64_MAX
    assert 2_800_000 <= LOGO_B64_MAX <= 3 * 1024 * 1024
    LogoUploadIn.model_validate({"logo_b64": "A" * LOGO_B64_MAX})
    with pytest.raises(ValidationError):
        LogoUploadIn.model_validate({"logo_b64": "A" * (LOGO_B64_MAX + 1)})


# =====================================================================
#                              Nr. 96
# =====================================================================
@pytest.fixture
def storage_attrappe(monkeypatch):
    import storage_service as ss
    protokoll = {"gespeichert": [], "geloescht": []}

    async def save_async(key, data):
        protokoll["gespeichert"].append(key)
        return key

    async def loeschen(db, *, key=None, prefix=None, grund, dealer_id="", ref=None, art="storage"):
        protokoll["geloescht"].append((key, grund))
        return True

    monkeypatch.setattr(ss, "save_async", save_async)
    monkeypatch.setattr(ss, "loeschen_oder_vormerken", loeschen)
    monkeypatch.setattr(ss, "validate_image_bytes", lambda raw, wo="": None)
    monkeypatch.setattr(ss, "bild_verkleinern", lambda raw, wo, fmt: raw)
    return protokoll


def _logo_body():
    import base64
    from routes.dealer import LogoUploadIn
    return LogoUploadIn(logo_b64="data:image/png;base64," + base64.b64encode(b"\x89PNG-fake").decode())


def test_96_db_fehler_raeumt_gespeicherte_datei_weg(monkeypatch, storage_attrappe):
    from fastapi import HTTPException
    import routes.dealer as dealer
    monkeypatch.setattr(dealer, "db", _Db(
        dealers=_Coll([{"id": "dX", "logo_url": None}], fehler=RuntimeError("mongo weg"))))
    with pytest.raises(HTTPException) as e:
        _lauf(dealer.upload_logo(_logo_body(), _chef()))
    assert e.value.status_code == 500
    assert len(storage_attrappe["gespeichert"]) == 1
    key = storage_attrappe["gespeichert"][0]
    assert storage_attrappe["geloescht"] == [(key, "logo_upload_abbruch")]


def test_96_zweiter_upload_raeumt_altes_logo_weg(monkeypatch, storage_attrappe):
    import routes.dealer as dealer
    alt = "/api/files/logo/dX/altes.png"
    monkeypatch.setattr(dealer, "db", _Db(dealers=[{"id": "dX", "logo_url": alt}]))
    erg = _lauf(dealer.upload_logo(_logo_body(), _chef()))
    assert erg["ok"] and erg["logo_url"].startswith("/api/files/logo/dX/")
    assert storage_attrappe["geloescht"] == [("logo/dX/altes.png", "logo_ersetzt")]
    assert dealer.db.dealers.docs[0]["logo_url"] == erg["logo_url"]


def test_96_altes_logo_bleibt_wenn_ein_sucher_es_noch_nutzt(monkeypatch, storage_attrappe):
    import routes.dealer as dealer
    alt = "/api/files/logo/dX/altes.png"
    monkeypatch.setattr(dealer, "db", _Db(
        dealers=[{"id": "dX", "logo_url": alt}],
        users=[{"id": "u-s", "settings_override": {"logo_url": alt}}]))
    _lauf(dealer.upload_logo(_logo_body(), _chef()))
    assert storage_attrappe["geloescht"] == []
    # Fremde Firma / fremder Pfad wird nie angefasst.
    _lauf(dealer._altes_logo_wegraeumen("/api/files/logo/dANDERE/x.png", "dX"))
    _lauf(dealer._altes_logo_wegraeumen("https://app.example/logo.png", "dX"))
    assert storage_attrappe["geloescht"] == []


def test_96_sucher_upload_setzt_nur_override(monkeypatch, storage_attrappe):
    import routes.dealer as dealer
    alt = "/api/files/logo/dX/sucher-alt.png"
    sucher = {"id": "u-s", "dealer_id": "dX", "role": "sucher",
              "settings_override": {"logo_url": alt}}
    monkeypatch.setattr(dealer, "db", _Db(
        dealers=[{"id": "dX", "logo_url": "/api/files/logo/dX/chef.png"}],
        users=[{"id": "u-s", "settings_override": {"logo_url": alt}}]))
    erg = _lauf(dealer.upload_logo(_logo_body(), sucher))
    assert dealer.db.users.updates and "settings_override.logo_url" in dealer.db.users.updates[0][1]["$set"]
    assert dealer.db.dealers.updates == []
    assert storage_attrappe["geloescht"] == [("logo/dX/sucher-alt.png", "logo_ersetzt")]
    assert erg["logo_url"] != alt


# =====================================================================
#                          HTTP (nach Neustart)
# =====================================================================
@pytest.fixture(scope="module")
def welt():
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    r = requests.post(f"{API}/auth/register", json={
        "email": f"r14td_{SUF}@{MAIL}", "password": PW,
        "company_name": "R14 Team GmbH", "contact_person": "R T", "phone": "0511 14"},
        timeout=30)
    assert r.status_code == 200, r.text[:200]
    C = _kopf(r.json()["token"])
    chef = requests.get(f"{API}/auth/me", headers=C, timeout=30).json()["user"]
    dealer_id = chef["dealer_id"]
    r = requests.post(f"{API}/dealer/sucher", headers=C, json={
        "first_name": "Sina", "last_name": "Sucht", "email": f"r14td_s_{SUF}@{MAIL}",
        "password": PW}, timeout=30)
    if r.status_code == 403:
        pytest.skip("SELF_SIGNUP=false — Chef darf keine Sucher anlegen")
    assert r.status_code == 200, r.text[:200]
    sucher_id = r.json()["sucher_id"]
    from auth import create_token
    sid = str(uuid.uuid4())
    _db().users.update_one({"id": sucher_id}, {"$set": {"current_session_id": sid}})
    S = _kopf(create_token(sucher_id, sid))
    yield {"C": C, "S": S, "chef": chef, "dealer_id": dealer_id, "sucher_id": sucher_id}
    dbx = _db()
    for c in ("subscriptions", "plan_requests", "generated_pdfs", "vehicle_comparisons",
              "activity_logs", "storage_delete_retry"):
        dbx[c].delete_many({"dealer_id": dealer_id})
    dbx.plan_requests.delete_many({"subject_user_id": {"$in": [chef["id"], sucher_id]}})
    dbx.users.delete_many({"dealer_id": dealer_id})
    dbx.dealers.delete_many({"id": dealer_id})


def _offen(**filt):
    return _db().plan_requests.count_documents({"status": "offen", **filt})


def test_http_33_doppelte_sucher_anfrage_bereits_offen(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    url = f"{API}/dealer/sucher/{welt['sucher_id']}/abo-anfrage"
    r1 = requests.post(url, headers=welt["C"], json={"plan": "monthly"}, timeout=30)
    r2 = requests.post(url, headers=welt["C"], json={"plan": "yearly"}, timeout=30)
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    assert r1.json()["request_id"] == r2.json()["request_id"]
    assert r2.json().get("bereits_offen") is True and not r1.json().get("bereits_offen")
    assert _offen(type="sucher_abo", subject_user_id=welt["sucher_id"]) == 1
    row = _db().plan_requests.find_one({"id": r1.json()["request_id"]}, {"_id": 0})
    assert row["wanted_plan"] == "yearly" and row["dealer_id"] == welt["dealer_id"]
    assert row["sucher_email"].startswith("r14td_s_")
    _db().plan_requests.delete_many({"subject_user_id": welt["sucher_id"]})


def test_http_56_sechzehn_parallele_eigene_anfragen(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    _db().plan_requests.delete_many({"subject_user_id": welt["chef"]["id"]})
    n = 16
    schranke = Barrier(n)

    def schuss(_):
        schranke.wait(timeout=30)
        r = requests.post(f"{API}/dealer/abo-anfrage-selbst", headers=welt["C"],
                          json={"plan": "monthly"}, timeout=60)
        return r.status_code, r.json()

    with ThreadPoolExecutor(max_workers=n) as ex:
        erg = list(ex.map(schuss, range(n)))
    assert all(s == 200 for s, _ in erg), erg
    assert _offen(type="sucher_abo", subject_user_id=welt["chef"]["id"]) == 1
    assert len({d["request_id"] for _, d in erg}) == 1, erg
    assert sum(1 for _, d in erg if not d.get("bereits_offen")) == 1, erg
    # GET /dealer/subscription meldet die offene Anfrage.
    r = requests.get(f"{API}/dealer/subscription", headers=welt["C"], timeout=30)
    assert r.json()["anfrage_offen"] is True
    _db().plan_requests.delete_many({"subject_user_id": welt["chef"]["id"]})


def test_http_56_index_vorhanden():
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    info = _db().plan_requests.index_information()
    partial = [v for v in info.values() if v.get("partialFilterExpression")]
    schluessel = {tuple(sorted(k for k, _ in v["key"])) for v in partial if v.get("unique")}
    assert ("subject_user_id", "type") in schluessel, info
    assert ("dealer_id", "type") in schluessel, info


def test_http_57_verkaufspaket_anfrage_idempotent(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    url = f"{API}/dealer/sale-plan/upgrade-request"
    r1 = requests.post(url, headers=welt["C"], json={"wanted_tier": "s10"}, timeout=30)
    if r1.status_code == 400 and "kostenlos" in r1.text:
        pytest.skip("VERKAUF_KOSTENLOS aktiv — Pfad nicht erreichbar")
    r2 = requests.post(url, headers=welt["C"], json={"wanted_tier": "s20"}, timeout=30)
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    assert r1.json()["request_id"] == r2.json()["request_id"]
    assert r2.json().get("bereits_offen") is True
    assert _offen(type="verkaufspaket", dealer_id=welt["dealer_id"]) == 1
    row = _db().plan_requests.find_one({"id": r1.json()["request_id"]}, {"_id": 0})
    assert row["wanted_tier"] == "s20"


def test_http_104_deaktivierter_sucher_400(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    sid = welt["sucher_id"]
    _db().plan_requests.delete_many({"subject_user_id": sid})
    _db().users.update_one({"id": sid}, {"$set": {"active": False}})
    try:
        r = requests.post(f"{API}/dealer/sucher/{sid}/abo-anfrage", headers=welt["C"],
                          json={"plan": "monthly"}, timeout=30)
        assert r.status_code == 400 and "deaktiviert" in r.text, r.text[:200]
        assert _offen(type="sucher_abo", subject_user_id=sid) == 0
    finally:
        _db().users.update_one({"id": sid}, {"$set": {"active": True}})
    r = requests.post(f"{API}/dealer/sucher/{sid}/abo-anfrage", headers=welt["C"],
                      json={"plan": "monthly"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    _db().plan_requests.delete_many({"subject_user_id": sid})


def test_http_72_92_liste_ueber_100_und_fremde_dealer_id(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    dbx = _db()
    extra = [{"id": f"r14x-{SUF}-{i}", "email": f"r14x_{SUF}_{i}@{MAIL}",
              "password_hash": "x", "role": "sucher", "active": True,
              "dealer_id": welt["dealer_id"], "first_name": "X", "last_name": str(i),
              "current_session_id": None, "created_at": JETZT.isoformat()}
             for i in range(101)]
    dbx.users.insert_many(extra)
    sid = welt["sucher_id"]
    # Vertrag/Vergleich/Abo des eigenen Suchers unter FREMDER Firma (Nr. 92).
    dbx.generated_pdfs.insert_one({"id": f"r14pdf-{SUF}", "dealer_id": f"fremd-{SUF}",
                                   "user_id": sid, "created_at": JETZT.isoformat()})
    dbx.vehicle_comparisons.insert_one({"id": f"r14cmp-{SUF}", "dealer_id": f"fremd-{SUF}",
                                        "user_id": sid, "created_at": JETZT.isoformat()})
    dbx.subscriptions.insert_one(_abo_doc(f"fremd-{SUF}", sid, id=f"r14abo-{SUF}",
                                          expires_at=(JETZT + timedelta(days=99)).isoformat()))
    try:
        r = requests.get(f"{API}/dealer/sucher", headers=welt["C"], timeout=60)
        assert r.status_code == 200, r.text[:200]
        rows = r.json()
        assert len(rows) >= 102, len(rows)
        assert all("stats_month" in x and "subscription" in x for x in rows)
        mein = next(x for x in rows if x["id"] == sid)
        assert mein["stats_month"] == {"kaeufe": 0, "vergleiche": 0}, mein["stats_month"]
        assert mein["subscription"]["active"] is False, mein["subscription"]
    finally:
        dbx.users.delete_many({"id": {"$in": [e["id"] for e in extra]}})
        dbx.generated_pdfs.delete_many({"dealer_id": f"fremd-{SUF}"})
        dbx.vehicle_comparisons.delete_many({"dealer_id": f"fremd-{SUF}"})
        dbx.subscriptions.delete_many({"dealer_id": f"fremd-{SUF}"})


def test_http_70_kuendigung_trifft_das_angezeigte_abo(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    dbx = _db()
    chef = welt["chef"]
    dbx.subscriptions.delete_many({"dealer_id": welt["dealer_id"]})
    pers = _abo_doc(welt["dealer_id"], chef["id"], plan="monthly",
                    expires_at=(JETZT + timedelta(days=20)).isoformat(),
                    created_at=(JETZT - timedelta(days=10)).isoformat())
    firma = _abo_doc(welt["dealer_id"], None, plan="yearly",
                     expires_at=(JETZT + timedelta(days=300)).isoformat(),
                     created_at=(JETZT - timedelta(days=100)).isoformat())
    dbx.subscriptions.insert_many([pers, firma])
    r = requests.get(f"{API}/dealer/subscription", headers=welt["C"], timeout=30)
    d = r.json()
    assert d["plan"] == "monthly" and d["can_cancel"] is True and d["days_remaining"] in (19, 20), d
    r = requests.post(f"{API}/dealer/subscription/cancel", headers=welt["C"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert r.json()["expires_at"] == pers["expires_at"]
    assert dbx.subscriptions.find_one({"id": pers["id"]})["status"] == "cancelled"
    assert dbx.subscriptions.find_one({"id": firma["id"]})["status"] == "active"
    d = requests.get(f"{API}/dealer/subscription", headers=welt["C"], timeout=30).json()
    assert d["raw_status"] == "cancelled" and d["active"] is True and d["can_cancel"] is False
    # Nur persoenliches Abo: Kuendigung darf nicht 404 liefern.
    dbx.subscriptions.delete_many({"id": firma["id"]})
    dbx.subscriptions.update_one({"id": pers["id"]}, {"$set": {"status": "active"},
                                                       "$unset": {"cancelled_at": ""}})
    r = requests.post(f"{API}/dealer/subscription/cancel", headers=welt["C"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    # Sucher darf weiterhin nicht kuendigen.
    r = requests.post(f"{API}/dealer/subscription/cancel", headers=welt["S"], timeout=30)
    assert r.status_code == 403
    dbx.subscriptions.delete_many({"dealer_id": welt["dealer_id"]})


def test_http_71_anzeige_ohne_gemischte_felder(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    dbx = _db()
    chef = welt["chef"]
    dbx.subscriptions.delete_many({"dealer_id": welt["dealer_id"]})
    pers = _abo_doc(welt["dealer_id"], chef["id"], plan="monthly",
                    expires_at=(JETZT - timedelta(days=5)).isoformat(),
                    created_at=(JETZT - timedelta(days=35)).isoformat())
    firma = _abo_doc(welt["dealer_id"], None, plan="yearly",
                     expires_at=(JETZT + timedelta(days=300)).isoformat(),
                     created_at=(JETZT - timedelta(days=100)).isoformat())
    dbx.subscriptions.insert_many([pers, firma])
    d = requests.get(f"{API}/dealer/subscription", headers=welt["C"], timeout=30).json()
    assert d["plan"] == "yearly" and d["active"] is True
    assert d["expires_at"] == firma["expires_at"] and d["days_remaining"] >= 299, d
    # Kein Abo -> none.
    dbx.subscriptions.delete_many({"dealer_id": welt["dealer_id"]})
    d = requests.get(f"{API}/dealer/subscription", headers=welt["C"], timeout=30).json()
    assert d["status"] == "none" and d["active"] is False and d["can_cancel"] is False
    assert d["expires_at"] is None and d["raw_status"] == "none"
    r = requests.post(f"{API}/dealer/subscription/cancel", headers=welt["C"], timeout=30)
    assert r.status_code == 404


def test_http_103_fremder_logo_host_abgelehnt(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    for H in (welt["C"], welt["S"]):
        r = requests.put(f"{API}/dealer/settings", headers=H,
                         json={"profile": {"logo_url": "https://tracker.example/x.png"}},
                         timeout=30)
        assert r.status_code == 400, r.text[:200]
    r = requests.put(f"{API}/dealer/settings", headers=welt["C"],
                     json={"profile": {"logo_url": "/api/files/logo/x/y.png"}}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert r.json()["logo_url"] == "/api/files/logo/x/y.png"


def test_http_116_zu_grosser_base64_string_422(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    r = requests.post(f"{API}/dealer/logo", headers=welt["C"],
                      json={"logo_b64": "A" * 3_000_000}, timeout=60)
    assert r.status_code == 422, r.text[:200]


def test_http_96_logo_wechsel_raeumt_altes_auf(welt):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    import base64
    import io
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("PIL fehlt")
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), (200, 30, 30)).save(buf, format="PNG")
    b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    r1 = requests.post(f"{API}/dealer/logo", headers=welt["C"], json={"logo_b64": b64}, timeout=60)
    assert r1.status_code == 200, r1.text[:200]
    url1 = r1.json()["logo_url"]
    assert requests.get(f"{BASE}{url1}", timeout=30).status_code == 200
    r2 = requests.post(f"{API}/dealer/logo", headers=welt["C"], json={"logo_b64": b64}, timeout=60)
    assert r2.status_code == 200, r2.text[:200]
    url2 = r2.json()["logo_url"]
    assert url2 != url1
    assert requests.get(f"{BASE}{url2}", timeout=30).status_code == 200
    # Altes Logo weg oder vorgemerkt — nie stillschweigend als Waise.
    weg = requests.get(f"{BASE}{url1}", timeout=30).status_code == 404
    vorgemerkt = _db().storage_delete_retry.count_documents(
        {"key": url1[len("/api/files/"):]}) == 1
    assert weg or vorgemerkt
    assert _db().dealers.find_one({"id": welt["dealer_id"]})["logo_url"] == url2
