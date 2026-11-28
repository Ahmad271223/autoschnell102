# -*- coding: utf-8 -*-
"""Inserats-Zwischenspeicher 14 Tage + Vertragsinhaber behaelt seinen Stand
(Wunsch Ahmad 19.09.2026).

Vorher lagen die Inseratsdaten — inklusive Name, Telefon und Anschrift des
(oft privaten) Verkaeufers — 90 Tage im GEMEINSAMEN Zwischenspeicher, damit
ein spaeteres Beweisdokument noch etwas zu dokumentieren hatte. Jetzt:

  * Der gemeinsame Speicher lebt 14 Tage (336 h), geloescht wird spaetestens
    21 Tage nach dem Abruf (14 + 7 Tage Karenz).
  * Wer einen KAUFVERTRAG zu dem Wagen macht, friert den rohen Inseratsstand
    an seinem Vertrag ein ("behaelt es bei sich") — nur er kommt daran, und
    sein Beweisdokument geht auch nach den 14 Tagen noch.
"""
import inspect
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

WURZEL = BACKEND.parent


def test_01_vierzehn_tage_stehen_ueberall_gleich():
    """Vier Stellen, EINE Zahl — vorher standen im Code 2160 h, im Cleanup
    1440 h und in der Compose-Datei noch einmal etwas anderes."""
    import cleanup_service as CS
    import routes.listings as L
    assert L.LISTING_CACHE_TTL_HOURS == 336, "Router: 14 Tage"
    assert CS.LISTING_CACHE_TTL_HOURS == 336, "Cleanup: dieselbe Zahl"
    assert CS.INSERATSCACHE_MAX_TAGE == 21, "14 Tage + 7 Tage Karenz"
    for datei in ("docker-compose.yml", ".env.example"):
        text = (WURZEL / datei).read_text(encoding="utf-8")
        treffer = re.findall(r"LISTING_CACHE_TTL_HOURS[:=]?-?(\d+)", text)
        assert treffer and all(t == "336" for t in treffer), (datei, treffer)


def test_02_datenschutzerklaerung_nennt_die_neue_frist():
    text = (WURZEL / "frontend" / "src" / "pages" / "legal" / "Datenschutz.jsx").read_text(
        encoding="utf-8")
    assert "max. 21 Tage" in text
    assert "60 Tage</li>" not in text.split("Inserats-Cache")[1][:80]
    assert "Kaufvertrag" in text.split("Inserats-Cache")[1][:220], \
        "Der Ausnahmefall (Vertrag) muss dastehen"


def test_03_vertrag_friert_den_rohen_inseratsstand_ein():
    import routes.contracts as C
    q = inspect.getsource(C.create_contract)
    assert "inserat_stand" in q and "listings_cache" in q
    # Aus dem SPEICHER, nicht aus den (bearbeitbaren) Fahrzeugdaten
    stelle = q.split("inserat_stand = None")[1].split("contract_dict")[0]
    assert 'db.listings_cache.find_one' in stelle
    assert 'v["data"]' not in stelle, "haendlerlokal bearbeitete Daten sind kein Beweis"
    assert '"fetched_at"' in stelle, "der Abrufzeitpunkt gehoert dazu"
    # Ein Fehler beim Einfrieren darf den Vertrag nie kippen
    assert "except Exception" in stelle
    assert '"inserat_stand": inserat_stand,' in q


def test_04_beweis_faellt_auf_den_eigenen_vertrag_zurueck():
    import routes.beweise as RB
    q = inspect.getsource(RB.beweis_anfordern)
    assert "_stand_aus_eigenem_vertrag" in q
    helfer = inspect.getsource(RB._stand_aus_eigenem_vertrag)
    # Nur der eigene Bereich (Chef: Firma, Sucher: eigene Vertraege)
    assert "_vertrag_bereich" in helfer
    assert '"inserat_stand.cache_key": cache_key' in helfer
    assert 'sort=[("created_at", -1)]' in helfer, "juengster Vertrag zaehlt"


def test_05_fremder_vertrag_gibt_keinen_stand_her(monkeypatch):
    """Der Bereichsfilter kommt aus routes.contracts — hier geprueft, dass er
    wirklich in die Abfrage wandert (sonst saehe jeder jeden Stand)."""
    import routes.beweise as RB

    gesehen = {}

    class _Coll:
        async def find_one(self, filt, projektion=None, sort=None):
            gesehen["filt"] = filt
            return None

    class _DB:
        generated_pdfs = _Coll()

    monkeypatch.setattr(RB, "db", _DB())
    import asyncio
    nutzer = {"id": "u1", "dealer_id": "d1", "role": "sucher"}
    asyncio.run(RB._stand_aus_eigenem_vertrag(nutzer, "mobile:123"))
    filt = gesehen["filt"]
    assert filt["inserat_stand.cache_key"] == "mobile:123"
    assert filt.get("dealer_id") == "d1", "ohne Firma waere es ein Mandantenleck"
    assert len(filt) > 2, "Sucher-Einschraenkung fehlt"
