# -*- coding: utf-8 -*-
"""Runde 31 (12.09.2026) — Fund aus dem Gesamtlauf der Suite.

Der Lauf meldete: "14 gleichzeitige Provider-Abrufe — erlaubt: 8". Einzeln
lief derselbe Test gruen. Ursache war KEIN Zufall und keine Ueberlast:

  provider_limiter._indexes_ready war ein PROZESSWEITER Schalter. Sobald
  irgendein Aufruf die Indizes fuer IRGENDEINE Datenbank angelegt hatte,
  uebersprang jede spaetere Datenbank das Anlegen. Ohne den eindeutigen
  Index auf provider_limits.provider legen gleichzeitige Erst-Anfragen
  MEHRERE Zaehler-Dokumente derselben Quelle an — und dann gilt das Limit
  je Dokument statt je Quelle. Aus 8 erlaubten Abrufen wurden 14.

In Produktion gibt es nur eine Datenbank, deshalb war dort nichts kaputt.
Die Absicherung selbst war aber nur eine Zufallsgroesse: sie haengt daran,
dass der Prozess seine erste Datenbank nie wechselt. Der Merker haengt
jetzt am Datenbanknamen.

In-Prozess gegen zwei Wegwerf-DBs; kein Server.
"""
import asyncio
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = "mongodb://127.0.0.1:27017"


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    import provider_limiter as PL

    class _W:
        pass

    w = _W()
    w.PL = PL
    w.merker_alt = set(PL._indexes_ready)
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.namen = [f"autoschnell_r31_{uuid.uuid4().hex[:8]}" for _ in range(2)]
    w.run = lambda coro: w.loop.run_until_complete(coro)
    yield w
    try:
        for name in w.namen:
            w.run(w.client.drop_database(name))
    finally:
        PL._indexes_ready.clear()
        PL._indexes_ready.update(w.merker_alt)
        w.client.close()
        w.loop.close()


def test_01_merker_ist_eine_menge_je_datenbank(welt):
    """Ein blosses True/False konnte nicht unterscheiden, WELCHE Datenbank
    schon vorbereitet ist."""
    assert isinstance(welt.PL._indexes_ready, set)


def test_02_zweite_datenbank_bekommt_den_unique_index_auch(welt):
    """Der eigentliche Fund: Datenbank 2 ging leer aus, sobald Datenbank 1
    den Merker gesetzt hatte."""
    w = welt
    a, b = (w.client[n] for n in w.namen)

    async def lauf():
        slot_a = await w.PL.acquire_slot(a, "kleinanzeigen")
        assert slot_a, "erster Slot muss klappen"
        # ... und jetzt eine ANDERE Datenbank im selben Prozess.
        slot_b = await w.PL.acquire_slot(b, "kleinanzeigen")
        assert slot_b, "zweiter Slot muss klappen"
        namen_a = await a.provider_limits.index_information()
        namen_b = await b.provider_limits.index_information()
        return namen_a, namen_b

    namen_a, namen_b = w.run(lauf())
    for namen, welche in ((namen_a, "erste"), (namen_b, "zweite")):
        passend = [i for i in namen.values()
                   if i.get("unique") and i.get("key") == [("provider", 1)]]
        assert passend, f"{welche} Datenbank ohne eindeutigen Index: {namen}"


def test_03_limit_haelt_auch_in_der_zweiten_datenbank(welt):
    """Der sichtbare Schaden: ohne den Index vervielfachte sich das Limit,
    weil mehrere Zaehler-Dokumente derselben Quelle entstanden."""
    w = welt
    a, b = (w.client[n] for n in w.namen)
    grenze = w.PL.PROVIDER_MAX_CONCURRENT["kleinanzeigen"]

    async def lauf():
        # Datenbank 1 zuerst — sie setzt den Merker.
        erste = await w.PL.acquire_slot(a, "kleinanzeigen")
        assert erste

        # Datenbank 2: viele gleichzeitige Erst-Anfragen. Genau hier
        # entstanden ohne den Index mehrere Zaehler-Dokumente.
        slots = await asyncio.gather(
            *[w.PL.acquire_slot(b, "kleinanzeigen") for _ in range(grenze * 5)])
        vergeben = [s for s in slots if s]
        zaehler = await b.provider_limits.count_documents(
            {"provider": "kleinanzeigen"})
        return vergeben, zaehler

    vergeben, zaehler = w.run(lauf())
    assert zaehler == 1, f"{zaehler} Zaehler-Dokumente statt einem"
    assert len(vergeben) <= grenze, (
        f"{len(vergeben)} Slots vergeben, erlaubt sind {grenze}")


def test_04_getrennte_datenbanken_teilen_sich_das_limit_nicht(welt):
    """Gegenprobe: Die Begrenzung gilt je Datenbank — sonst haette der Fix
    zwei Testlaeufe gegeneinander ausgebremst."""
    w = welt
    a, b = (w.client[n] for n in w.namen)
    grenze = w.PL.PROVIDER_MAX_CONCURRENT["kleinanzeigen"]

    async def lauf():
        in_a = [s for s in await asyncio.gather(
            *[w.PL.acquire_slot(a, "kleinanzeigen") for _ in range(grenze)]) if s]
        # a ist jetzt voll — b muss trotzdem noch vergeben koennen.
        voll = await w.PL.acquire_slot(a, "kleinanzeigen")
        in_b = await w.PL.acquire_slot(b, "kleinanzeigen")
        return in_a, voll, in_b

    in_a, voll, in_b = w.run(lauf())
    assert len(in_a) == grenze, in_a
    assert voll is None, "das Limit der ersten Datenbank muss greifen"
    assert in_b, "die zweite Datenbank hat ihr eigenes Kontingent"
