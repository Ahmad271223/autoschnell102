# -*- coding: utf-8 -*-
"""Kontonummer (13.09.2026), Schritt 3 — Nummernschema der Test-Helfer.

tests/konten.py vergibt direkt eingefuegten Konten ohne Nummer eine Nummer
(kennung_fuer_mail). Das muss dasselbe Schema wie die echte Kontenanlage
ergeben (Chef = kunden_nr, Sucher = '<kunden_nr>-<zusatz>', Kaeufer/Fahrer
eigene Nummer aus der EINEN Reihe) — sonst laufen Tests mit Daten, die in
Produktion nie entstehen. Zweiter Teil: die synchronen Kopien in konten.py
und backend/kontenanlage.py teilen Reihe und Sucher-Zaehler.

In-Prozess ohne Backend: pymongo gegen eine Wegwerf-DB, kontenanlage mit
eigenem Motor-Client in EINEM asyncio.run. Kein server-/deps-/routes-Import.
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import kontenanlage as KA  # noqa: E402
import konten  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


@pytest.fixture
def dbx(monkeypatch):
    name = f"autoschnell_nl_3kh_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(konten, "DB_NAME", name)
    d = konten._db()
    # Wie in Produktion: Teil-Unique-Index je Nummer, eindeutige kunden_nr
    for coll in (d.users, d.driver_accounts):
        coll.create_index("kontonummer", name="kontonummer_eindeutig", unique=True,
                          partialFilterExpression={"kontonummer": {"$type": "string"}})
        coll.create_index("kontonummer_basis", name="kontonummer_basis", sparse=True)
    d.dealers.create_index("kunden_nr", name="kunden_nr_unique", unique=True, sparse=True)
    try:
        yield d
    finally:
        d.client.drop_database(name)


def _konto(d, kid, role, dealer_id=None, sammlung="users", **extra):
    doc = {"id": kid, "email": f"{kid}@konten-helfer.test", "role": role,
           "dealer_id": dealer_id, "active": True, "password_hash": "x"}
    doc.update(extra)
    if sammlung == "driver_accounts":
        doc.pop("role")
        doc.pop("dealer_id")
    d[sammlung].insert_one(doc)
    return doc["email"]


def test_01_schema_wie_kontenanlage(dbx):
    d = dbx
    d.dealers.insert_one({"id": "F1", "company_name": "Eins", "kunden_nr": 5000})
    d.dealers.insert_one({"id": "F2", "company_name": "Zwei"})          # ohne kunden_nr
    chef1 = _konto(d, "c1", "dealer", "F1")
    s1 = _konto(d, "s1", "sucher", "F1")
    s2 = _konto(d, "s2", "sucher", "F1")
    chef1b = _konto(d, "c1b", "dealer", "F1")    # Firmennummer schon vergeben
    chef2 = _konto(d, "c2", "dealer", "F2")
    s3 = _konto(d, "s3", "sucher", "F2")
    kaeufer = _konto(d, "k1", "b2b_buyer")
    rest = _konto(d, "s9", "sucher", "GIBT_ES_NICHT")
    fahrer = _konto(d, "fa1", None, sammlung="driver_accounts")

    assert konten.kennung_fuer_mail(chef1) == "5000"
    assert konten.kennung_fuer_mail(s1) == "5000-1"
    assert konten.kennung_fuer_mail(s2) == "5000-2"
    assert konten.kennung_fuer_mail(chef1b) == "5000-3"
    for kid in ("c1", "s1", "s2", "c1b"):
        assert d.users.find_one({"id": kid})["kontonummer_basis"] == 5000, kid

    # Firma ohne kunden_nr bekommt eine; Chef = Firmennummer, Sucher mit Zusatz
    nr2 = konten.kennung_fuer_mail(chef2)
    f2 = d.dealers.find_one({"id": "F2"})
    assert isinstance(f2["kunden_nr"], int) and f2["kunden_nr"] > 5000
    assert nr2 == str(f2["kunden_nr"])
    assert konten.kennung_fuer_mail(s3) == f"{f2['kunden_nr']}-1"
    assert d.users.find_one({"id": "s3"})["kontonummer_basis"] == f2["kunden_nr"]

    # Kaeufer, Fahrer und Testrest ohne Firma: eigene Nummer aus der Reihe
    k_nr = konten.kennung_fuer_mail(kaeufer)
    fa_nr = konten.kennung_fuer_mail(fahrer, "driver_accounts")
    r_nr = konten.kennung_fuer_mail(rest)
    for nr, coll, kid in ((k_nr, d.users, "k1"), (r_nr, d.users, "s9")):
        assert nr.isdigit() and int(nr) > f2["kunden_nr"], nr
        assert coll.find_one({"id": kid})["kontonummer_basis"] == int(nr)
    # Fahrer (14.09.2026): Kontonummer = Fahrer-ID, wie kontenanlage.fahrer_anlegen
    fa_doc = d.driver_accounts.find_one({"id": "fa1"})
    assert fa_nr.startswith("FD-") and len(fa_nr) == 11 and fa_doc["driver_code"] == fa_nr
    assert "kontonummer_basis" not in fa_doc
    assert len({k_nr, fa_nr, r_nr}) == 3

    # Idempotent: zweiter Aufruf aendert nichts, Zaehler bleibt
    seq = d.dealers.find_one({"id": "F1"})["sucher_seq"]
    assert konten.kennung_fuer_mail(s2) == "5000-2"
    assert d.dealers.find_one({"id": "F1"})["sucher_seq"] == seq


def test_02_gemeinsame_reihe_mit_kontenanlage(dbx):
    d = dbx
    d.dealers.insert_one({"id": "G1", "company_name": "Gemeinsam", "kunden_nr": 7000})
    assert konten.kennung_fuer_mail(_konto(d, "gs1", "sucher", "G1")) == "7000-1"
    k_nr = int(konten.kennung_fuer_mail(_konto(d, "gk1", "b2b_buyer")))

    async def _kontenanlage():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        try:
            adb = client[d.name]
            s = await KA.sucher_anlegen(adb, "G1", {"id": "gs2", "password_hash": "x"})
            k = await KA.kaeufer_anlegen(adb, {"id": "gk2", "password_hash": "x"})
            return s["kontonummer"], k["kontonummer"]
        finally:
            client.close()

    s_ka, k_ka = asyncio.run(_kontenanlage())
    # kontenanlage setzt nach den Nummern der Helfer fort ...
    assert s_ka == "7000-2"
    from kontonummer import KAEUFER_MUSTER
    assert KAEUFER_MUSTER.match(k_ka), k_ka      # Kaeufer-Code seit 14.09.2026
    # ... und die Helfer nach denen der kontenanlage
    assert konten.kennung_fuer_mail(_konto(d, "gs3", "sucher", "G1")) == "7000-3"
    # Kaeufer der kontenanlage verbrauchen keine Nummer mehr (Kaeufer-Code) —
    # die Helfer-Nummer folgt weiter der Reihe (nach dem ersten Helfer-Kaeufer)
    assert int(konten.kennung_fuer_mail(_konto(d, "gk3", "b2b_buyer"))) > k_nr
