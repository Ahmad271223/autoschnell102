# -*- coding: utf-8 -*-
"""Startpruefung 27.09.2026 (Go-Live-Pruefbericht, gegengeprueft) — Protokoll.

  K5  Abholprotokoll-PDF: Ausstattung mit "fehlt"/"defekt"/"anders" (so
      speichert die Fahrer-App seit dem KI-Umbau jedes "Nein") stand als
      "[X]" = vorhanden im unterschriebenen PDF. Jetzt Nein mit Grund.
  H1  Lese-Indizes appointments.id / pickup_protocols.id (termin_id,
      protokoll_id): eindeutig ohne Dubletten, sonst nicht eindeutig + Alarm,
      nie Startabbruch; der $lookup der Freigaben nutzt sie.

PDF-Tests bauen ECHTE PDFs (ohne Datenbank); die Index-Tests laufen gegen
eine eigene Wegwerf-Datenbank (Praefix DB_NAME).
"""
import asyncio
import io
import os
import re
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")

#: genau die Werte aus dem Pruefbericht
FEATURES = {"Sitzheizung": "fehlt", "Navigationssystem": "defekt", "Tempomat": "anders",
            "Klimaanlage": True, "Alufelgen": False}


def _pdf():
    import importlib
    return importlib.import_module("pickup_pdf_service")


# =============================================================================== K5
def test_k5_checklist_texte_sind_nein_mit_grund():
    PDF = _pdf()
    items = [(k, "") for k in FEATURES] + [("Standheizung", "")]
    t = PDF._checklist(items, PDF._styles(), col_count=1, checked=FEATURES)
    zeilen = {k: zeile[0].text for k, zeile in zip([k for k, _ in items], t._cellvalues)}
    for name, grund in (("Sitzheizung", "fehlt"), ("Navigationssystem", "defekt"),
                        ("Tempomat", "anders als beschrieben")):
        assert "[X]" not in zeilen[name], zeilen[name]
        assert zeilen[name].startswith("<font size=9 color='#0A0A0A'>[–]"), zeilen[name]
        assert f"— nein ({grund})" in zeilen[name], zeilen[name]
    assert "[X]" in zeilen["Klimaanlage"] and "nein" not in zeilen["Klimaanlage"]
    assert "[–]" in zeilen["Alufelgen"] and "— nein</font>" in zeilen["Alufelgen"]
    assert "(" not in zeilen["Alufelgen"].split("— nein")[1]      # False: ohne Grund
    # nicht beantwortet: leer, weder ja noch nein
    assert "[&nbsp;&nbsp;]" in zeilen["Standheizung"] and "nein" not in zeilen["Standheizung"]


@pytest.mark.parametrize("wert, erwartet", [
    (True, ("ja", "")), (False, ("nein", "")), (None, ("offen", "")), ("", ("offen", "")),
    ("fehlt", ("nein", "fehlt")), (" Defekt ", ("nein", "defekt")),
    ("ANDERS", ("nein", "anders als beschrieben")),
    # alles andere ist NIE "vorhanden" (vorher bool(wert) -> [X])
    ("ja", ("offen", "")), ("irgendwas", ("offen", "")), (1, ("offen", "")), ({}, ("offen", "")),
])
def test_k5_checkwert(wert, erwartet):
    assert _pdf()._checkwert(wert) == erwartet


def _pdf_text(pdf: bytes) -> str:
    from pypdf import PdfReader
    text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)
    return re.sub(r"[ \xa0]+", " ", text)


def test_k5_echtes_pdf_druckt_fehlende_ausstattung_als_nein():
    """Ende zu Ende wie beim Abschluss: ProtocolIn nimmt die Werte der
    Fahrer-App an, filled['features'] geht unveraendert in build_pickup_pdf."""
    from routes.protocols import ProtocolIn
    gespeichert = ProtocolIn(features=FEATURES).model_dump()["features"]
    assert gespeichert == FEATURES                     # Server laesst die Texte zu
    PDF = _pdf()
    pdf = PDF.build_pickup_pdf(
        appointment={"id": f"t_{uuid.uuid4().hex[:8]}", "pickup_date": "2026-09-27"},
        vehicle={"make_label": "BMW", "model_label": "320d", "features": list(FEATURES)},
        contract={"purchase_price": 18000.0},
        dealer={"company_name": "Autohaus Test"}, driver={"name": "Fahrer"},
        filled={"features": gespeichert, "documents": {}, "condition": {}, "version": 1})
    text = _pdf_text(pdf)
    for name in ("Sitzheizung", "Navigationssystem", "Tempomat"):
        assert f"[X] {name}" not in text, text
    assert "[–] Sitzheizung — nein (fehlt)" in text, text
    assert "[–] Navigationssystem — nein (defekt)" in text, text
    assert "[–] Tempomat — nein (anders als beschrieben)" in text, text
    assert "[X] Klimaanlage" in text, text
    assert "[–] Alufelgen — nein" in text, text


# =============================================================================== H1
@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    import indizes as IX
    w = type("W", (), {})()
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"{os.environ.get('DB_NAME', 'autoschnell_test')}_h1_{uuid.uuid4().hex[:8]}"
    w.db = w.client[w.db_name]
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.IX = IX
    vorher = set(IX.FEHLENDE_UNIQUE)
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        IX.FEHLENDE_UNIQUE.clear()
        IX.FEHLENDE_UNIQUE.update(vorher)
        w.client.close()
        w.loop.close()


def _plan_indizes(plan) -> set:
    namen = set()
    if isinstance(plan, dict):
        if plan.get("indexName"):
            namen.add(plan["indexName"])
        for v in plan.values():
            namen |= _plan_indizes(v)
    elif isinstance(plan, list):
        for v in plan:
            namen |= _plan_indizes(v)
    return namen


def _alarme(w, typ):
    return w.run(w.db.betriebsalarme.find({"typ": typ, "offen": True}, {"_id": 0}).to_list(20))


def test_h1_indizes_eindeutig_idempotent_und_vom_lookup_genutzt(welt):
    w = welt
    w.run(w.db.appointments.insert_many(
        [{"id": f"a{i}", "dealer_id": "d1", "status": "offen"} for i in range(300)]))
    w.run(w.db.pickup_protocols.insert_many(
        [{"id": f"p{i}", "appointment_id": f"a{i}", "dealer_id": "d1", "status": "zur_freigabe"}
         for i in range(5)]))
    for _ in range(2):                                     # idempotent
        assert w.run(w.IX.id_lese_indizes(w.db)) == {"appointments": "eindeutig",
                                                      "pickup_protocols": "eindeutig"}
    for sammlung, name in w.IX.ID_LESE_INDIZES:
        info = w.run(w.db[sammlung].index_information())[name]
        assert info["key"] == [("id", 1)] and info.get("unique") is True, info
        assert not info.get("sparse") and not info.get("partialFilterExpression"), info
    assert not _alarme(w, "id_nicht_eindeutig") and not _alarme(w, "index_fehlt")
    assert not _alarme(w, "unique_index_fehlt")
    assert not {"appointments.termin_id", "pickup_protocols.protokoll_id"} & w.IX.FEHLENDE_UNIQUE
    # eindeutig heisst eindeutig
    from pymongo.errors import DuplicateKeyError
    with pytest.raises(DuplicateKeyError):
        w.run(w.db.appointments.insert_one({"id": "a1", "dealer_id": "d2"}))

    # Der $lookup aus routes/protocols._wartende_protokolle nutzt termin_id
    exp = w.run(w.db.command("explain", {
        "aggregate": "pickup_protocols", "cursor": {},
        "pipeline": [{"$match": {"dealer_id": "d1"}},
                     {"$lookup": {"from": "appointments", "localField": "appointment_id",
                                  "foreignField": "id", "as": "_termin"}}]},
        verbosity="executionStats"))
    assert "termin_id" in str(exp), exp
    # find_one({"id": ...}) ohne dealer_id
    for sammlung, name, wert in (("appointments", "termin_id", "a7"),
                                 ("pickup_protocols", "protokoll_id", "p3")):
        plan = w.run(w.db[sammlung].find({"id": wert}).explain())["queryPlanner"]["winningPlan"]
        assert _plan_indizes(plan) == {name}, plan


def test_h1_dubletten_nicht_eindeutig_mit_alarm_ohne_startabbruch(welt, monkeypatch):
    w = welt
    monkeypatch.setenv("APP_ENV", "production")
    w.run(w.db.appointments.insert_many([
        {"id": "doppelt", "dealer_id": "d1"}, {"id": "doppelt", "dealer_id": "d2"},
        {"id": "einzeln", "dealer_id": "d1"}]))
    # zwei Protokolle OHNE id — auch sie verhindern einen schlichten Unique-Index
    w.run(w.db.pickup_protocols.insert_many([{"dealer_id": "d1"}, {"dealer_id": "d1"}]))
    stand = w.run(w.IX.id_lese_indizes(w.db))          # kein SystemExit
    assert stand == {"appointments": "nicht_eindeutig", "pickup_protocols": "nicht_eindeutig"}
    for sammlung, name in w.IX.ID_LESE_INDIZES:
        info = w.run(w.db[sammlung].index_information())[name]
        assert info["key"] == [("id", 1)] and not info.get("unique"), info
    alarme = {a["ref"]: a for a in _alarme(w, "id_nicht_eindeutig")}
    assert "doppelt" in alarme["appointments.termin_id"]["details"]["beispiele"]
    assert "ohne id" in alarme["pickup_protocols.protokoll_id"]["details"]["beispiele"]
    # nicht kritisch: kein Eintrag in FEHLENDE_UNIQUE (der den Produktionsstart abbricht)
    assert not {"appointments.termin_id", "pickup_protocols.protokoll_id"} & w.IX.FEHLENDE_UNIQUE
    assert not _alarme(w, "unique_index_fehlt") and not _alarme(w, "index_fehlt")
    # die Abfrage ist trotzdem schnell
    plan = w.run(w.db.appointments.find({"id": "einzeln"}).explain())["queryPlanner"]["winningPlan"]
    assert _plan_indizes(plan) == {"termin_id"}

    # bereinigt -> naechster Start legt ihn eindeutig an, Alarm geschlossen
    w.run(w.db.appointments.delete_one({"dealer_id": "d2"}))
    w.run(w.db.pickup_protocols.update_many({}, [{"$set": {"id": {"$toString": "$_id"}}}]))
    stand = w.run(w.IX.id_lese_indizes(w.db))
    assert stand == {"appointments": "eindeutig", "pickup_protocols": "eindeutig"}
    for sammlung, name in w.IX.ID_LESE_INDIZES:
        assert w.run(w.db[sammlung].index_information())[name].get("unique") is True
    assert not _alarme(w, "id_nicht_eindeutig")


def test_h1_dubletten_skript_meldet_doppelte_ids(welt, capsys):
    w = welt
    from pymongo import MongoClient
    import importlib.util
    pfad = Path(__file__).resolve().parents[1] / "scripts" / "dubletten_pruefen.py"
    spec = importlib.util.spec_from_file_location("dubletten_pruefen_h1", pfad)
    skript = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(skript)
    w.run(w.db.appointments.insert_many([{"id": "x1"}, {"id": "x1"}, {"id": "x2"}]))
    sync = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    try:
        assert skript.doppelte_ids(sync[w.db_name]) == 1
    finally:
        sync.close()
    assert "appointments.id = 'x1': 2x" in capsys.readouterr().out


def test_h1_quelltext_start_und_kommentar():
    wurzel = Path(__file__).resolve().parents[1]
    server = (wurzel / "server.py").read_text(encoding="utf-8")
    ensure = server[server.index("async def ensure_indexes():"):server.index("async def on_start(")]
    assert "await id_lese_indizes(db)" in ensure
    prot = (wurzel / "routes" / "protocols.py").read_text(encoding="utf-8")
    i = prot.index("# localField/foreignField nutzt den Index appointments.id")
    assert "termin_id" in prot[i:i + 300] and "indizes.id_lese_indizes" in prot[i:i + 400]
