# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026, Welle 4 (Review) — Team markt_kaeufer.

  Zugangsanfrage "bereits aktiv (bis …)": das Datum in deutscher Zeit wie in
  der Kopfzeile des Marktplatzes (vorher UTC — bei einem Ablauf zwischen 22
  und 24 Uhr UTC nannte die Antwort den Vortag).

Rein in-Prozess: der Pfad "bereits aktiv" kehrt vor jedem Datenbankzugriff
zurueck, es entstehen keine Testdokumente.
"""
import asyncio
import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _kaeufer(expires_at: str) -> dict:
    return {"id": "k_rpmk4", "role": "b2b_buyer", "dealer_id": None,
            "company_name": "Kaeufer W4 GmbH", "active": True,
            "marketplace_access": {"active": True, "plan": "monthly",
                                   "expires_at": expires_at}}


def _ablauf_spaet_abends():
    """Ablauf in gut 30 Tagen um 23:30 UTC — in deutscher Zeit (MEZ wie MESZ)
    schon der Folgetag."""
    tag = (datetime.now(timezone.utc) + timedelta(days=30)).date()
    ablauf = datetime(tag.year, tag.month, tag.day, 23, 30, tzinfo=timezone.utc)
    return ablauf, tag + timedelta(days=1)


def _anfrage(M, user):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(M.request_marketplace_access(user=user))
    finally:
        loop.close()


def test_bereits_aktiv_nennt_das_deutsche_datum(monkeypatch):
    M = importlib.import_module("routes.marketplace")
    monkeypatch.setattr(M, "MARKTPLATZ_KOSTENLOS", False)
    ablauf, folgetag = _ablauf_spaet_abends()
    erg = _anfrage(M, _kaeufer(ablauf.isoformat()))
    assert "request_id" not in erg
    assert erg["hinweis"] == f"Zugang ist bereits aktiv (bis {folgetag:%d.%m.%Y})."
    # vorher: UTC-Datum (Vortag)
    assert f"{ablauf:%d.%m.%Y}" not in erg["hinweis"]


def test_bereits_aktiv_ohne_zeitzonendaten(monkeypatch):
    """Image ohne tzdata: betriebsmeldung._berlin rechnet nach der EU-Regel."""
    import zoneinfo
    M = importlib.import_module("routes.marketplace")
    monkeypatch.setattr(M, "MARKTPLATZ_KOSTENLOS", False)

    def _ohne(*_a, **_k):
        raise zoneinfo.ZoneInfoNotFoundError("kein tzdata")
    monkeypatch.setattr(zoneinfo, "ZoneInfo", _ohne)
    ablauf, folgetag = _ablauf_spaet_abends()
    erg = _anfrage(M, _kaeufer(ablauf.isoformat()))
    assert erg["hinweis"] == f"Zugang ist bereits aktiv (bis {folgetag:%d.%m.%Y})."


def test_naiver_ablauf_gilt_als_utc(monkeypatch):
    """Altbestand ohne Zeitzone: gilt als UTC (deps._ablauf_parsen) und wird
    ebenso in deutsche Zeit umgerechnet."""
    M = importlib.import_module("routes.marketplace")
    monkeypatch.setattr(M, "MARKTPLATZ_KOSTENLOS", False)
    ablauf, folgetag = _ablauf_spaet_abends()
    erg = _anfrage(M, _kaeufer(ablauf.replace(tzinfo=None).isoformat()))
    assert erg["hinweis"] == f"Zugang ist bereits aktiv (bis {folgetag:%d.%m.%Y})."
