# -*- coding: utf-8 -*-
"""Anbieter-Ausfaelle (Go-Live-Audit 09/2026, Punkt 48).

Simuliert ohne echte Anbieter-Anfragen, was passiert, wenn Apify/mobile.de/
AutoScout24 nicht wie erwartet antworten:
  - Token ungueltig (401/403)         -> klarer Text + Betriebsalarm
  - Guthaben aufgebraucht (402)       -> klarer Text + Betriebsalarm
  - Anbieter-Limit (429)              -> "spaeter erneut"
  - Zeitueberschreitung / Netzfehler  -> "antwortet nicht"
  - Anbieter-5xx / kaputte Antwort    -> "voruebergehend gestoert"
  - leeres Ergebnis                   -> wie bisher None (Inserat weg)
  - Tagesbudget erreicht              -> Text mit Limit, Zaehler zurueckgenommen
  - Alarm-Drosselung: gleicher Fehler nur einmal je Stunde als Alarm

Reine Unit-Tests: httpx wird im Modul durch einen Fake ersetzt; die
Budget-/Alarm-Tests brauchen nur eine erreichbare MongoDB (MONGO_URL).
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

import anbieter_fehler  # noqa: E402
import mobile_service  # noqa: E402
import autoscout_service  # noqa: E402
import provider_fetch  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"


class _Antwort:
    def __init__(self, status, text="", daten=None):
        self.status_code, self.text, self._daten = status, text, daten

    def json(self):
        if self._daten is None:
            raise ValueError("kein JSON")
        return self._daten


class _FakeClient:
    """Ersatz fuer httpx.AsyncClient: liefert eine feste Antwort oder wirft."""
    antwort = None
    ausnahme = None

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **kw):
        if _FakeClient.ausnahme:
            raise _FakeClient.ausnahme
        return _FakeClient.antwort


@pytest.fixture
def apify(monkeypatch):
    """Apify 'aktiv', offizielle mobile.de-API aus, Sandbox aus."""
    monkeypatch.setattr(mobile_service, "APIFY_TOKEN", "apify_test_token", raising=False)
    monkeypatch.setattr(mobile_service, "MOBILE_USER", "", raising=False)
    monkeypatch.setattr(mobile_service, "MOBILE_PASS", "", raising=False)
    monkeypatch.setattr(mobile_service, "MOBILE_SANDBOX_MODE", False, raising=False)
    monkeypatch.setattr(mobile_service.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(autoscout_service, "APIFY_TOKEN", "apify_test_token", raising=False)
    monkeypatch.setattr(autoscout_service, "autoscout_quelle_verfuegbar", lambda: True, raising=False)
    monkeypatch.setattr(autoscout_service._httpx, "AsyncClient", _FakeClient)
    _FakeClient.antwort, _FakeClient.ausnahme = None, None
    anbieter_fehler._ALARM_ZULETZT.clear()
    yield
    _FakeClient.antwort, _FakeClient.ausnahme = None, None


def _mobile(url="https://suchen.mobile.de/fahrzeuge/details.html?id=123456"):
    return asyncio.run(mobile_service._fetch_from_apify("123456", url))


@pytest.mark.parametrize("status,text,art,stichwort", [
    (401, '{"error":{"type":"token-not-found"}}', anbieter_fehler.ART_TOKEN, "Token"),
    (403, "forbidden", anbieter_fehler.ART_TOKEN, "Token"),
    (402, "Monthly usage hard limit exceeded", anbieter_fehler.ART_GUTHABEN, "Guthaben"),
    (429, "rate limit exceeded", anbieter_fehler.ART_LIMIT, "Limit"),
    (500, "internal", anbieter_fehler.ART_AUSFALL, "gestört"),
    (504, "gateway timeout", anbieter_fehler.ART_ZEIT, "antwortet nicht"),
])
def test_http_fehler_werden_klar_benannt(apify, status, text, art, stichwort):
    _FakeClient.antwort = _Antwort(status, text)
    with pytest.raises(anbieter_fehler.AnbieterFehler) as e:
        _mobile()
    assert e.value.art == art
    assert stichwort in str(e.value), str(e.value)
    assert "mobile.de" in str(e.value)
    # RuntimeError-Unterklasse: die Routen bilden das auf 502 ab
    assert isinstance(e.value, RuntimeError)


def test_zeitueberschreitung_und_netzfehler(apify):
    _FakeClient.ausnahme = httpx.ReadTimeout("zu langsam")
    with pytest.raises(anbieter_fehler.AnbieterFehler) as e:
        _mobile()
    assert e.value.art == anbieter_fehler.ART_ZEIT and "antwortet nicht" in str(e.value)
    _FakeClient.ausnahme = httpx.ConnectError("keine Verbindung")
    with pytest.raises(anbieter_fehler.AnbieterFehler) as e:
        _mobile()
    assert e.value.art == anbieter_fehler.ART_AUSFALL and "gestört" in str(e.value)


def test_kaputte_antwort_ist_ausfall_leere_antwort_bleibt_none(apify):
    _FakeClient.antwort = _Antwort(200, "<html>", daten=None)      # kein JSON
    with pytest.raises(anbieter_fehler.AnbieterFehler) as e:
        _mobile()
    assert e.value.art == anbieter_fehler.ART_AUSFALL
    _FakeClient.antwort = _Antwort(200, "[]", daten=[])            # Inserat weg
    assert _mobile() is None


def test_autoscout_gleiche_behandlung(apify):
    _FakeClient.antwort = _Antwort(401, "token invalid")
    with pytest.raises(anbieter_fehler.AnbieterFehler) as e:
        asyncio.run(autoscout_service.fetch_autoscout_vehicle(
            "https://www.autoscout24.de/angebote/vw-golf-abc123", "abc123"))
    assert e.value.art == anbieter_fehler.ART_TOKEN and "AutoScout24" in str(e.value)
    _FakeClient.ausnahme = httpx.ReadTimeout("x")
    with pytest.raises(anbieter_fehler.AnbieterFehler) as e:
        asyncio.run(autoscout_service.fetch_autoscout_vehicle(
            "https://www.autoscout24.de/angebote/vw-golf-abc123", "abc123"))
    assert e.value.art == anbieter_fehler.ART_ZEIT


def test_get_vehicle_reicht_fehler_durch_statt_fake_daten(apify):
    """get_vehicle darf bei Token-Fehler weder Demo-Daten liefern noch den
    alten Allerweltstext — der klare Fehler muss oben ankommen."""
    _FakeClient.antwort = _Antwort(401, "token invalid")

    class _Db:
        class vehicle_cache:
            @staticmethod
            async def find_one(*a, **kw):
                return None
    with pytest.raises(anbieter_fehler.AnbieterFehler) as e:
        asyncio.run(mobile_service.get_vehicle(_Db(), "123456",
                                               url="https://suchen.mobile.de/fahrzeuge/details.html?id=123456"))
    assert e.value.art == anbieter_fehler.ART_TOKEN


def _mit_db(coro_factory):
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _run():
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        try:
            return await coro_factory(cl[DB_NAME])
        finally:
            cl.close()
    return asyncio.run(_run())


def test_token_fehler_erzeugt_betriebsalarm_einmal_je_stunde(apify):
    anbieter_fehler._ALARM_ZULETZT.clear()
    ref = f"mobile.de-test-{uuid.uuid4().hex[:8]}"

    async def _lauf(db):
        f = anbieter_fehler.AnbieterFehler(anbieter_fehler.ART_TOKEN, ref, "HTTP 401")
        await anbieter_fehler.melden(db, f)
        await anbieter_fehler.melden(db, f)          # gedrosselt -> kein zweiter Alarm
        g = anbieter_fehler.AnbieterFehler(anbieter_fehler.ART_LIMIT, ref, "HTTP 429")
        await anbieter_fehler.melden(db, g)          # nicht betreiber-relevant -> kein Alarm
        n = await db.betriebsalarme.count_documents({"ref": ref})
        docs = await db.betriebsalarme.find({"ref": ref}, {"_id": 0}).to_list(10)
        await db.betriebsalarme.delete_many({"ref": ref})
        return n, docs
    n, docs = _mit_db(_lauf)
    assert n == 1, docs
    assert docs[0]["typ"] == anbieter_fehler.ART_TOKEN
    assert "Apify-Token" in (docs[0].get("details") or {}).get("hinweis", "") or \
        "Apify-Token" in str(docs[0])


def test_tagesbudget_klarer_text_und_zaehler_zurueck(monkeypatch):
    monkeypatch.setattr(provider_fetch, "TAGESLIMIT_JE_FIRMA", 1)
    dealer = f"firma-{uuid.uuid4().hex[:8]}"

    async def _lauf(db):
        await provider_fetch._budget_pruefen(db, "mobile", dealer)      # 1 -> ok
        try:
            with pytest.raises(RuntimeError) as e:
                await provider_fetch._budget_pruefen(db, "mobile", dealer)  # 2 -> Limit
            assert "Tageslimit" in str(e.value) and "1/Tag" in str(e.value)
            from datetime import datetime, timezone
            tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            doc = await db.provider_budget.find_one({"_id": f"{tag}:firma:{dealer}"})
            # der abgelehnte Versuch wurde zurueckgebucht
            assert doc and doc["n"] == 1, doc
            # Kleinanzeigen kostet nichts -> nie ein Limit
            await provider_fetch._budget_pruefen(db, "kleinanzeigen", dealer)
        finally:
            await db.provider_budget.delete_one({"_id": f"{tag}:firma:{dealer}"})
            await db.provider_budget.update_one({"_id": f"{tag}:gesamt"}, {"$inc": {"n": -1}})
    _mit_db(_lauf)


def test_ohne_limit_wird_nie_gebremst(monkeypatch):
    """Betreiber-Entscheidung 09/2026: Sucher duerfen unbegrenzt bei
    mobile.de und AutoScout abrufen. 0 heisst KEIN Limit — nicht "alles
    gesperrt". Gezaehlt wird trotzdem, davon lebt die Warnung."""
    monkeypatch.setattr(provider_fetch, "TAGESLIMIT_JE_FIRMA", 0)
    monkeypatch.setattr(provider_fetch, "TAGESLIMIT_GESAMT", 0)
    monkeypatch.setattr(provider_fetch, "TAGESWARNUNG", 0)
    dealer = f"firma-{uuid.uuid4().hex[:8]}"

    async def _lauf(db):
        from datetime import datetime, timezone
        tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            for _ in range(25):
                await provider_fetch._budget_pruefen(db, "mobile", dealer)
            doc = await db.provider_budget.find_one({"_id": f"{tag}:firma:{dealer}"})
            assert doc and doc["n"] == 25, doc
        finally:
            await db.provider_budget.delete_one({"_id": f"{tag}:firma:{dealer}"})
            await db.provider_budget.update_one({"_id": f"{tag}:gesamt"},
                                                {"$inc": {"n": -25}})
    _mit_db(_lauf)


def test_warnung_meldet_einmal_und_bremst_nicht(monkeypatch):
    """Die Warnschwelle darf niemals einen Abruf verhindern."""
    monkeypatch.setattr(provider_fetch, "TAGESLIMIT_JE_FIRMA", 0)
    monkeypatch.setattr(provider_fetch, "TAGESLIMIT_GESAMT", 0)
    dealer = f"firma-{uuid.uuid4().hex[:8]}"

    async def _lauf(db):
        from datetime import datetime, timezone
        tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        gesamt = await db.provider_budget.find_one({"_id": f"{tag}:gesamt"}) or {}
        stand = int(gesamt.get("n", 0))
        monkeypatch.setattr(provider_fetch, "TAGESWARNUNG", stand + 2)
        try:
            await provider_fetch._budget_pruefen(db, "mobile", dealer)   # stand+1
            await provider_fetch._budget_pruefen(db, "mobile", dealer)   # stand+2 -> Warnung
            await provider_fetch._budget_pruefen(db, "mobile", dealer)   # laeuft weiter
            doc = await db.provider_budget.find_one({"_id": f"{tag}:firma:{dealer}"})
            assert doc["n"] == 3, doc
            n = await db.betriebsalarme.count_documents(
                {"typ": "anbieter_viele_abrufe", "ref": tag})
            assert n == 1, f"erwartet genau eine Warnung, gefunden {n}"
        finally:
            await db.provider_budget.delete_one({"_id": f"{tag}:firma:{dealer}"})
            await db.provider_budget.update_one({"_id": f"{tag}:gesamt"},
                                                {"$inc": {"n": -3}})
            await db.betriebsalarme.delete_many(
                {"typ": "anbieter_viele_abrufe", "ref": tag})
    _mit_db(_lauf)


def test_antwort_ohne_inhalt_heisst_inserat_weg(apify):
    """Echter Lauf auf prod1 (09/2026): fuer eine erfundene Nummer liefert
    Apify EIN Element ohne Daten. Das darf kein leeres Fahrzeug werden,
    sondern muss wie ein verschwundenes Inserat behandelt werden (None)."""
    _FakeClient.antwort = _Antwort(200, "[{}]", daten=[{}])
    assert _mobile() is None
    _FakeClient.antwort = _Antwort(200, "[{}]", daten=[{"url": "https://www.autoscout24.de/x"}])
    assert asyncio.run(autoscout_service.fetch_autoscout_vehicle(
        "https://www.autoscout24.de/angebote/vw-golf-abc123", "abc123")) is None
