# -*- coding: utf-8 -*-
"""USt-IdNr.-Pruefung (Go-Live-Audit 09/2026, Punkt 40).

Unit-Tests fuer Format/Normalisierung/VIES-Auswertung (ohne Netz) und ein
Endpunkt-Test: falsche USt-IdNr. bei der Zwischenhaendler-Registrierung
wird mit 422 und klarem Text abgewiesen (kein Konto entsteht).
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ustid  # noqa: E402

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
API = f"{BASE}/api"


@pytest.mark.parametrize("wert,erwartet", [
    ("DE123456789", "DE123456789"),
    ("de 123 456 789", "DE123456789"),
    ("ATU12345678", "ATU12345678"),
    ("NL123456789B01", "NL123456789B01"),
    ("FRXX123456789", "FRXX123456789"),
    ("PL1234567890", "PL1234567890"),
    ("IT12345678901", "IT12345678901"),
    ("GR123456789", "EL123456789"),      # GR -> EL (VIES-Kuerzel)
    ("CHE-123.456.789 MWST", "CHE123456789MWST"),
    ("HRB 12345", "HRB 12345"),          # Handelsregister bleibt wie eingegeben
    ("FN 123456a", "FN 123456a"),
    ("", ""),
])
def test_gueltige_formate(wert, erwartet):
    fehler, s = ustid.format_pruefen(wert)
    assert fehler is None, fehler
    assert s == erwartet


@pytest.mark.parametrize("wert,stichwort", [
    ("DE12345678", "Format von DE"),      # 8 statt 9 Ziffern
    ("DE1234567890", "Format von DE"),
    ("DE012345678", "beginnt nie mit 0"),
    ("AT12345678", "Format von AT"),      # fehlendes U
    ("NL123456789B1", "Format von NL"),
    ("FR123", "Format von FR"),
    ("XY", "vollständig"),
    ("HRB", "vollständig"),
])
def test_ungueltige_formate(wert, stichwort):
    fehler, _ = ustid.format_pruefen(wert)
    assert fehler and stichwort in fehler, fehler


def test_sieht_aus_wie_ustid():
    assert ustid.sieht_aus_wie_ustid("DE123456789")
    assert ustid.sieht_aus_wie_ustid("atu12345678")
    assert not ustid.sieht_aus_wie_ustid("HRB 12345")
    assert not ustid.sieht_aus_wie_ustid("")


class _Antwort:
    def __init__(self, status, daten):
        self.status_code, self._daten = status, daten

    def json(self):
        return self._daten


def _fake_client(antwort=None, ausnahme=None):
    class _C:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            if ausnahme:
                raise ausnahme
            _C.letzte = kw
            return antwort
    return _C


def test_vies_gueltig(monkeypatch):
    import httpx
    C = _fake_client(_Antwort(200, {"valid": True, "name": "MUSTER GMBH",
                                    "address": "MUSTERSTR. 1\n12345 BERLIN"}))
    monkeypatch.setattr(httpx, "AsyncClient", C)
    e = asyncio.run(ustid.vies_pruefen("de 123456789"))
    assert e["status"] == "gueltig" and e["name"] == "MUSTER GMBH"
    assert e["adresse"] == "MUSTERSTR. 1 12345 BERLIN"
    assert C.letzte["json"] == {"countryCode": "DE", "vatNumber": "123456789"}


def test_vies_ungueltig_und_nicht_pruefbar(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(_Antwort(200, {"valid": False, "name": "---", "address": "---"})))
    e = asyncio.run(ustid.vies_pruefen("DE123456789"))
    assert e["status"] == "ungueltig" and e["name"] is None and "kennt" in e["hinweis"]
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(_Antwort(200, {"valid": False, "userError": "MS_UNAVAILABLE"})))
    assert asyncio.run(ustid.vies_pruefen("DE123456789"))["status"] == "nicht_pruefbar"
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(_Antwort(503, {})))
    assert asyncio.run(ustid.vies_pruefen("DE123456789"))["status"] == "nicht_pruefbar"
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(ausnahme=httpx.ReadTimeout("x")))
    e = asyncio.run(ustid.vies_pruefen("DE123456789"))
    assert e["status"] == "nicht_pruefbar" and "nicht erreichbar" in e["hinweis"]
    # Handelsregister / Schweiz: nur manuell pruefbar, kein Netzaufruf
    assert asyncio.run(ustid.vies_pruefen("HRB 12345"))["status"] == "nicht_pruefbar"
    assert asyncio.run(ustid.vies_pruefen("CHE123456789MWST"))["status"] == "nicht_pruefbar"
    assert asyncio.run(ustid.vies_pruefen("DE12345"))["status"] == "nicht_pruefbar"


def test_registrierung_weist_falsche_ustid_ab():
    try:
        requests.get(f"{API}/health", timeout=5)
    except requests.RequestException:
        pytest.skip("Backend nicht erreichbar")
    r = requests.post(f"{API}/buyer/register", json={
        "company_name": "USt Test GmbH", "contact_name": "Test Person",
        "email": "ustid-test-nicht-anlegen@example.invalid",
        "password": "UstIdTest123!x", "gewerblich_bestaetigt": True,
        "ust_id": "DE12345678",
    }, timeout=30)
    assert r.status_code == 422, r.text[:300]
    assert "Format von DE" in r.text
