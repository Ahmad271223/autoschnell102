# -*- coding: utf-8 -*-
"""Systemtest 19.10.2026 — Befunde beim Komplettlauf (Suite, Routen-Sweep, CI):

1. kontenanlage.py: `import logging` und der Logger standen IM Modul-Docstring —
   `log` war nie definiert, die Rollback-Helfer brachen mit NameError ab.
2. migrationen.py: ZIEL_VERSION 22 bei vorhandener Migration 23 — der Abschluss
   schrieb die Version auf 22 zurueck, Migration 23 lief bei jedem Start erneut
   und /ready meldete einen falschen Stand.
3. Mock-Betrieb gab Geld aus: ein Test-Backend mit MOCK_PROVIDER_FETCH=true und
   dem Produktions-APIFY_TOKEN startete 20 echte Markt-Crawls; der KI-Anbieter
   wurde aus dem Aufraeumlauf aufgerufen.
4. CI seit 10.10. rot: gitleaks-Scheinfund "vorschau_b64" (Dictionary-Feldname)
   in der Historie — Allowlist.
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
WURZEL = Path(__file__).resolve().parents[2]
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


# ------------------------------------------------------------- 1. kontenanlage
def test_01_kontenanlage_logger_ist_definiert():
    import kontenanlage
    assert hasattr(kontenanlage, "log") and kontenanlage.log.name == "autohandel"
    doku = kontenanlage.__doc__ or ""
    assert "import logging" not in doku and "getLogger" not in doku, \
        "Import/Logger duerfen nicht im Docstring stehen"


def test_01b_chef_aufraeumen_meldet_fehler_statt_nameerror():
    """Rollback scheitert -> Alarm statt NameError (vorher: `log` unbekannt)."""
    import kontenanlage as K

    class _Users:
        async def delete_one(self, *a, **k):
            raise RuntimeError("DB weg")

    class _Alarme:
        aufrufe = []

        async def update_one(self, *a, **k):
            self.aufrufe.append(a)
            class R: upserted_id = None
            return R()

    class _Db:
        users = _Users()
        dealers = _Users()
        betriebsalarme = _Alarme()

        def __getitem__(self, name):
            return getattr(self, name)

    db = _Db()
    asyncio.run(K._chef_aufraeumen(db, "u1"))      # darf nicht werfen
    asyncio.run(K._firma_aufraeumen(db, "d1"))
    assert len(_Alarme.aufrufe) == 2


# ------------------------------------------------------------- 2. Migrationen
def test_02_zielversion_ist_hoechste_migration():
    import migrationen as M
    assert M.ZIEL_VERSION == max(nr for nr, _n, _f in M.MIGRATIONEN) == 23


def test_02b_abschluss_schreibt_version_nie_zurueck(monkeypatch):
    """Liste laenger als ZIEL_VERSION (wie am 13.10.): nach dem Lauf muss die
    hoechste GELAUFENE Nummer stehen, nicht der alte Stand."""
    from motor.motor_asyncio import AsyncIOMotorClient
    import migrationen as M
    name = f"autoschnell_systest_{uuid.uuid4().hex[:8]}"

    async def m_a(db):
        return {"a": 1}

    async def m_b(db):
        return {"b": 1}

    monkeypatch.setattr(M, "MIGRATIONEN", [(1, "a", m_a), (2, "b", m_b)])
    monkeypatch.setattr(M, "ZIEL_VERSION", 1)

    async def lauf():
        client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        db = client[name]
        try:
            erledigt = await M.ausfuehren(db)
            stand = await M.aktuelle_version(db)
            # zweiter Start: nichts laeuft erneut
            erledigt2 = await M.ausfuehren(db)
            return erledigt, stand, erledigt2
        finally:
            await client.drop_database(name)
            client.close()

    erledigt, stand, erledigt2 = asyncio.run(lauf())
    assert set(erledigt) == {"a", "b"} and stand == 2 and erledigt2 == {}


# ------------------------------------------------------------- 3. Mock gibt kein Geld aus
def test_03_apify_token_im_mock_ignoriert_echten_schluessel(monkeypatch):
    from markt import konfig
    monkeypatch.setattr(konfig, "_echt_im_mock_gemeldet", False)
    monkeypatch.setenv("MOCK_PROVIDER_FETCH", "true")
    monkeypatch.delenv("MARKT_APIFY_IM_MOCK", raising=False)
    monkeypatch.setenv("APIFY_TOKEN", "apify_api_ABCDEF0123456789")
    assert konfig.token() == "", "echter Schluessel im Mock -> keine Laeufe"
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    assert konfig.token() == "test-token", "Testwerte der Suiten bleiben wirksam"
    monkeypatch.setenv("APIFY_TOKEN", "ci-test-apify-token")
    assert konfig.token() == "ci-test-apify-token"
    monkeypatch.setenv("APIFY_TOKEN", "apify_api_ABCDEF0123456789")
    monkeypatch.setenv("MARKT_APIFY_IM_MOCK", "true")
    assert konfig.token().startswith("apify_api_"), "bewusste Freigabe"
    monkeypatch.delenv("MARKT_APIFY_IM_MOCK", raising=False)
    monkeypatch.setenv("MOCK_PROVIDER_FETCH", "false")
    assert konfig.token().startswith("apify_api_"), "Produktion unveraendert"


def test_03b_markt_worker_und_lauf_haengen_an_konfig_token():
    q = (WURZEL / "backend" / "markt" / "jobs.py").read_text(encoding="utf-8")
    assert "konfig.token()" in q, "Worker prueft den Token ueber konfig.token()"
    a = (WURZEL / "backend" / "markt" / "apify.py").read_text(encoding="utf-8")
    assert "tok = konfig.token()" in a and 'raise ApifyFehler("token"' in a
    assert "APIFY_TOKEN" not in a.replace('"APIFY_TOKEN fehlt"', ""), \
        "apify.py liest den Token nur ueber konfig.token()"


def test_03c_ki_im_mock_nur_mit_ausdruecklichem_schalter(monkeypatch):
    from ai import provider as P
    monkeypatch.setattr(P, "_echt_im_mock_gemeldet", False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-TESTWERT")
    monkeypatch.setenv("MOCK_PROVIDER_FETCH", "true")
    monkeypatch.delenv("KI_BEWERTUNG_AKTIV", raising=False)
    assert P.ki_aktiv() is False, "echter Schluessel im Mock ohne Schalter -> aus"
    monkeypatch.setenv("KI_BEWERTUNG_AKTIV", "true")
    assert P.ki_aktiv() is True, "Lauf-Backend (KI-Komplettlauf) bleibt moeglich"
    monkeypatch.setenv("KI_BEWERTUNG_AKTIV", "false")
    assert P.ki_aktiv() is False
    monkeypatch.delenv("KI_BEWERTUNG_AKTIV", raising=False)
    monkeypatch.setenv("MOCK_PROVIDER_FETCH", "false")
    assert P.ki_aktiv() is True, "Produktion: Standard an"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert P.ki_aktiv() is False


# ------------------------------------------------------------- 4. CI / gitleaks
def test_04_gitleaks_allowlist_kennt_den_scheinfund():
    import tomllib
    d = tomllib.loads((WURZEL / ".gitleaks.toml").read_text(encoding="utf-8"))
    assert any("vorschau_b64" in r for r in d["allowlist"]["regexes"])
