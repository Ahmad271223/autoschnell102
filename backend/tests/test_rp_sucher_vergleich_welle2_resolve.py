# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026, Welle 2 — Team Sucher/Vergleich.

RP-276b (Übergabe bestand_ui): /listings/resolve verhaelt sich wie
/listings/check und /mobile/compare:
  - Kleinanzeigen im Erweiterungsbetrieb nur im Rueckfall-Kontingent
    (bereits Welle 1, RP-026 — hier mit Rueckbuchung geprueft),
  - Quellen ohne Zugang -> klare 400 statt 502 aus dem Fetcher,
  - jeder Fehler nach der Buchung gibt den Rueckfall zurueck, auch ein
    unerwarteter,
  - dasselbe Tempolimit je Konto wie der Vergleich (Cache-Treffer schreiben
    je Aufruf einen Audit-Eintrag).
Reine In-Prozess-Tests ohne Datenbank (alle DB-Helfer ersetzt).
"""
import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import routes.listings as L  # noqa: E402
from anbieter_fehler import ListingGone  # noqa: E402

USER = {"id": "u_rp2", "dealer_id": "d_rp2", "role": "sucher", "active": True}
KA_URL = "https://www.kleinanzeigen.de/s-anzeige/vw-golf/3012345678-216-1234"
AS_URL = "https://www.autoscout24.de/angebote/vw-golf-719b9573-d82f-4d29-85e0-54b5708d9aa6"
MOBILE_URL = "https://suchen.mobile.de/fahrzeuge/details.html?id=412345678"


@pytest.fixture
def rueckfall(monkeypatch):
    """Erweiterungsbetrieb an, Rueckfall erlaubt, kein Cache-Treffer; zaehlt
    Buchungen und Rueckbuchungen."""
    stand = {"gebucht": 0, "zurueck": 0, "protokoll": 0}

    async def erlaubt(gewuenscht, user):
        if gewuenscht:
            stand["gebucht"] += 1
        return bool(gewuenscht)

    async def zurueck(user):
        stand["zurueck"] += 1

    async def kein_treffer(*a, **k):
        return None

    async def protokoll(*a, **k):
        stand["protokoll"] += 1

    monkeypatch.setattr(L, "_erweiterung_noetig", lambda: True)
    monkeypatch.setattr(L, "_rueckfall_erlaubt", erlaubt)
    monkeypatch.setattr(L, "_rueckfall_zurueck", zurueck)
    monkeypatch.setattr(L, "peek_cached_listing", kein_treffer)
    monkeypatch.setattr(L, "log_activity_sicher", protokoll)
    monkeypatch.setattr(L, "_vergleich_limiter", _Limiter(True))
    return stand


class _Limiter:
    def __init__(self, erlaubt):
        self.erlaubt, self.schluessel = erlaubt, []

    async def check(self, key):
        self.schluessel.append(key)
        return self.erlaubt


def _resolve(url, ohne_erweiterung=True):
    return asyncio.run(L.listings_resolve(
        L.ListingURLIn(url=url, ohne_erweiterung=ohne_erweiterung), USER))


@pytest.mark.parametrize("fehler,code", [
    (ValueError("kaputt"), 500),
    (ListingGone("Inserat ist offline"), 404),
    (RuntimeError("Apify down"), 502),
    (HTTPException(418, "zwischendurch"), 418),
])
def test_rp276b_jeder_fehler_gibt_den_rueckfall_zurueck(rueckfall, monkeypatch, fehler, code):
    async def wirft(*a, **k):
        raise fehler

    monkeypatch.setattr(L, "get_or_fetch_listing", wirft)
    with pytest.raises(HTTPException) as e:
        _resolve(KA_URL)
    assert e.value.status_code == code
    assert rueckfall == {"gebucht": 1, "zurueck": 1, "protokoll": 0}


def test_rp276b_erfolg_behaelt_die_buchung(rueckfall, monkeypatch):
    async def liefert(*a, **k):
        return {"title": "VW Golf"}, False                      # A-20: 2-Tupel

    monkeypatch.setattr(L, "get_or_fetch_listing", liefert)
    r = _resolve(KA_URL)
    assert r["vehicle"]["title"] == "VW Golf" and r["source"] == "kleinanzeigen"
    assert rueckfall == {"gebucht": 1, "zurueck": 0, "protokoll": 1}


def test_rp276b_ohne_rueckfallwunsch_holt_der_browser(rueckfall, monkeypatch):
    async def nie(*a, **k):
        raise AssertionError("Server-Abruf trotz Erweiterungspflicht")

    monkeypatch.setattr(L, "get_or_fetch_listing", nie)
    r = _resolve(KA_URL, ohne_erweiterung=False)
    assert r["needs_client_fetch"] is True
    assert rueckfall["zurueck"] == 0


@pytest.mark.parametrize("url,schalter,text", [
    (AS_URL, "autoscout_quelle_verfuegbar", "AutoScout24"),
    (MOBILE_URL, "mobile_quelle_verfuegbar", "mobile.de"),
])
def test_rp276b_quelle_ohne_zugang_ist_400(rueckfall, monkeypatch, url, schalter, text):
    async def nie(*a, **k):
        raise AssertionError("Abruf trotz fehlendem Zugang")

    monkeypatch.setattr(L, schalter, lambda: False)
    monkeypatch.setattr(L, "get_or_fetch_listing", nie)
    with pytest.raises(HTTPException) as e:
        _resolve(url)
    assert e.value.status_code == 400 and text in e.value.detail
    assert rueckfall["gebucht"] == 0


def test_rp276b_tempolimit_wie_beim_vergleich(rueckfall, monkeypatch):
    async def nie(*a, **k):
        raise AssertionError("Abruf trotz Tempolimit")

    bremse = _Limiter(False)
    monkeypatch.setattr(L, "_vergleich_limiter", bremse)
    monkeypatch.setattr(L, "get_or_fetch_listing", nie)
    with pytest.raises(HTTPException) as e:
        _resolve(KA_URL)
    assert e.value.status_code == 429
    # gemeinsamer Schluessel mit /mobile/compare, kein Rueckfall verbraucht
    assert bremse.schluessel == ["vergleich:u_rp2"]
    assert rueckfall == {"gebucht": 0, "zurueck": 0, "protokoll": 0}


def test_rp276b_ungueltige_adresse_zaehlt_nicht(rueckfall, monkeypatch):
    bremse = _Limiter(True)
    monkeypatch.setattr(L, "_vergleich_limiter", bremse)
    with pytest.raises(HTTPException) as e:
        _resolve("https://example.com/kein-inserat")
    assert e.value.status_code == 400 and bremse.schluessel == []
