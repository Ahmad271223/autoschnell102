# -*- coding: utf-8 -*-
"""Wunsch Ahmad 25.11.2026: Kundennummer im Vertrag fortlaufend je Firma — der erste Vertrag 1002, dann immer +1;
Chef und Sucher derselben Firma teilen den Zaehler (nie zweimal dieselbe Nummer), andere Firmen zaehlen getrennt.
Eine selbst eingetragene Nummer gilt weiter; die Vorschau verbraucht keine; eine neue Fassung behaelt ihre."""
import asyncio
import inspect
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import routes.contracts as C  # noqa: E402
import vertrag_kundennummer as K  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


@pytest.fixture
def db():
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    name = f"t_kdnr_{uuid.uuid4().hex[:8]}"
    try:
        loop.run_until_complete(client.admin.command("ping"))
    except Exception:  # noqa: BLE001
        loop.close()
        pytest.skip("MongoDB nicht erreichbar")
    from types import SimpleNamespace
    d = client[name]
    yield SimpleNamespace(counters=d.counters, run=loop.run_until_complete)
    loop.run_until_complete(client.drop_database(name))
    client.close()
    loop.close()


def test_01_fortlaufend_ab_1002_je_firma(db):
    assert db.run(K.naechste(db, "f1")) == "1002"            # Vorschau verbraucht nichts
    assert db.run(K.naechste(db, "f1")) == "1002"
    assert [db.run(K.ziehen(db, "f1")) for _ in range(3)] == ["1002", "1003", "1004"]
    assert db.run(K.naechste(db, "f1")) == "1005"
    assert db.run(K.ziehen(db, "f2")) == "1002"              # andere Firma: eigener Zaehler


def test_02_gleichzeitig_nie_doppelt(db):
    async def viele():
        return await asyncio.gather(*[K.ziehen(db, "f3") for _ in range(25)])
    nummern = db.run(viele())
    assert sorted(int(n) for n in nummern) == list(range(1002, 1027))


def test_03_eingebaut_in_anlegen_vorschau_und_fassung():
    anlegen = inspect.getsource(C.create_contract)
    assert "_kdnr.ziehen(db, user[\"dealer_id\"])" in anlegen      # Zaehler je Firma, nicht je Konto
    assert anlegen.index("_kdnr.ziehen") < anlegen.index("kundennummer_einsetzen(contract_dict)")
    vorschau = inspect.getsource(C.preview_contract)
    assert "_kdnr.naechste(" in vorschau and "_kdnr.ziehen" not in vorschau
    fassung = inspect.getsource(C.vertrag_nachtraeglich_aendern)
    assert "cd_alt.get(\"vertrags_kundennummer\")" in fassung and "_kdnr" not in fassung
