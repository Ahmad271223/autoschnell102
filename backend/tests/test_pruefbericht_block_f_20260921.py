# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026, Block F (21.09.2026).

  SV-03  Unique-Indizes ueber unique_anlegen: Altdublette = klare Meldung,
         Alarm und FEHLENDE_UNIQUE (hart) bzw. nur Alarm (weich) — keine rohe
         Ausnahme mehr im Start
  SV-04  migrationen._main legt ALLE Indizes an und bricht in Produktion bei
         fehlenden eindeutigen Indizes ab; AL-13 Module vor der Pruefung laden
  AL-08  Termin-Index: paralleles Anlegen/Ersetzen bricht nicht ab
  SV-07  /api/health nennt Einzelheiten nur Berechtigten
  V-18   verwaiste Protokoll-Entwuerfe ohne Termin werden aufgeraeumt
  B-02   Regeln: min <= max, von <= bis
  B-03   Modellzuordnung: CLS ist die CLS-Klasse, Actros keine A-Klasse
  P-05   Vertragsversand per E-Mail nur an genau EINE gueltige Adresse
  P-06   bewusst geleerte FIN bleibt leer
  R1-30  Freitexte der Protokolle/Termine werden mit der Frist geleert
  R1-35  Befoerderung zum Chef ohne anderen Chef setzt den Firmen-Zeiger
  AD-16  Nutzerliste meldet die Kuerzung (X-Truncated)
  DP-03  auch per zahl_env gelesene Namen erreichen den Container
"""
import asyncio
import inspect
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _module(name):
    import importlib
    return importlib.import_module(name)


def _vor(minuten: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minuten)).isoformat()


_MODULE_MIT_DB = ("deps", "indizes", "routes.admin", "routes.contracts", "routes.protocols",
                  "cleanup_service", "lifecycle", "kaufvorgang")


@pytest.fixture
def frisch(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_f_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for n in _MODULE_MIT_DB:
        m = _module(n)
        if hasattr(m, "db"):
            monkeypatch.setattr(m, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


# ====================================================================== SV-03
def test_sv03_unique_anlegen_meldet_dubletten_statt_abzubrechen(frisch):
    IX = _module("indizes")
    db, run = frisch.db, frisch.run
    ref_hart = "t_hart.token"
    ref_weich = "t_weich.token"

    async def lauf():
        await db.t_hart.insert_many([{"token": "x"}, {"token": "x"}])
        await db.t_weich.insert_many([{"token": "y"}, {"token": "y"}])
        hart = await IX.unique_anlegen(db.t_hart, "token", name="token")
        weich = await IX.unique_anlegen(db.t_weich, "token", name="token", weich=True)
        a_hart = await db.betriebsalarme.find_one({"typ": "unique_index_fehlt", "ref": ref_hart})
        a_weich = await db.betriebsalarme.find_one({"typ": "unique_index_fehlt_weich",
                                                    "ref": ref_weich})
        in_register = (ref_hart in IX.FEHLENDE_UNIQUE, ref_weich in IX.FEHLENDE_UNIQUE)
        # bereinigen -> naechster Start legt an und schliesst den Alarm
        await db.t_hart.delete_one({"token": "x"})
        wieder = await IX.unique_anlegen(db.t_hart, "token", name="token")
        a_zu = await db.betriebsalarme.find_one({"typ": "unique_index_fehlt", "ref": ref_hart})
        info = await db.t_hart.index_information()
        return hart, weich, a_hart, a_weich, in_register, wieder, a_zu, info

    try:
        hart, weich, a_hart, a_weich, in_register, wieder, a_zu, info = run(lauf())
    finally:
        IX.FEHLENDE_UNIQUE.discard(ref_hart)
        IX.FEHLENDE_UNIQUE.discard(ref_weich)
    assert hart is False and weich is False
    assert a_hart and a_hart["offen"] is True
    assert a_weich and a_weich["offen"] is True
    assert in_register == (True, False), "nur harte Indizes stoppen den Produktionsstart"
    assert wieder is True and a_zu["offen"] is False
    assert info["token"]["unique"] is True


def test_sv03_start_legt_keine_nackten_unique_indizes_mehr_an():
    q = (BACKEND / "server.py").read_text(encoding="utf-8")
    ensure = q[q.index("async def ensure_indexes():"):q.index("async def on_start(")]
    alle = q[q.index("async def _alle_indexe():"):q.index("async def run_schreiber_melden_forever(")]
    for name in ("ein_aktuelles_protokoll_je_termin", "vertrag_idempotenz", "berichtsversion_eindeutig",
                 "vertrag_freigabe_token", "mail_schluessel", "versand_schluessel"):
        stelle = ensure[ensure.index(name) - 400:ensure.index(name)]
        assert "unique_anlegen(" in stelle, name
    for name in ("protokollversion_eindeutig", "zahlung_je_vorgang"):
        stelle = alle[alle.index(name) - 200:alle.index(name)]
        assert "unique_anlegen(" in stelle, name
    for stueck in ('unique_anlegen(db.dealer_invites, "token")',
                   'unique_anlegen(db.dealer_drivers, [("dealer_id", 1), ("driver_account_id", 1)])',
                   'unique_anlegen(db.vehicle_cache, "mobile_ad_id", weich=True)'):
        assert stueck in ensure, stueck


# ====================================================================== SV-04 / AL-13
def test_sv04_cli_legt_alle_indizes_an_und_prueft_das_register():
    q = inspect.getsource(_module("migrationen")._main)
    assert "indexe=server._alle_indexe" in q
    assert "indizes.FEHLENDE_UNIQUE and _ist_prod()" in q and "return 78" in q
    # AL-13: erst die Module laden (sie lesen dabei ihre Zahlen), dann pruefen
    assert q.index("import server") < q.rindex("pruefe_produktion(log)")


# ====================================================================== AL-08
def test_al08_termin_index_parallel_ersetzen(frisch, monkeypatch):
    IX = _module("indizes")
    db, run = frisch.db, frisch.run
    monkeypatch.setattr(IX, "db", db)

    async def lauf():
        # alter Stand mit anderem Filter -> beide Prozesse wollen ersetzen
        await db.appointments.create_index(
            [("dealer_id", 1), ("contract_id", 1)], unique=True, name="termin_offen_je_vertrag",
            partialFilterExpression={"contract_id": {"$type": "string"}})
        erg = await asyncio.gather(IX._termin_unique_index(), IX._termin_unique_index(),
                                   IX._termin_unique_index())
        info = await db.appointments.index_information()
        return erg, info

    try:
        erg, info = run(lauf())
    finally:
        IX.FEHLENDE_UNIQUE.discard("appointments.termin_offen_je_vertrag")
    assert erg == [True, True, True], erg
    filt = info["termin_offen_je_vertrag"]["partialFilterExpression"]
    assert "status" in filt and filt["contract_id"] == {"$type": "string", "$gt": ""}


# ====================================================================== SV-07
def test_sv07_health_nennt_einzelheiten_nur_berechtigten():
    q = inspect.getsource(_module("server").health_check)
    assert "await _darf_betriebsdaten_sehen(request)" in q and "request is None or" in q
    assert q.count('"kern": kern') == 1


# ====================================================================== V-18
def test_v18_verwaiste_entwuerfe_werden_aufgeraeumt(frisch):
    CS = _module("cleanup_service")
    db, run = frisch.db, frisch.run

    async def lauf():
        await db.appointments.insert_one({"id": "t_da", "dealer_id": "d1", "status": "offen"})
        await db.pickup_protocols.insert_many([
            {"id": "p_weg", "appointment_id": "t_geloescht", "status": "entwurf", "updated_at": _vor(120)},
            {"id": "p_frei", "appointment_id": "t_geloescht", "status": "freigegeben",
             "updated_at": _vor(120)},
            {"id": "p_frisch", "appointment_id": "t_geloescht2", "status": "entwurf",
             "updated_at": _vor(5)},
            {"id": "p_final", "appointment_id": "t_geloescht3", "status": "final",
             "updated_at": _vor(500)},
            {"id": "p_da", "appointment_id": "t_da", "status": "entwurf", "updated_at": _vor(500)},
        ])
        n = await CS.verwaiste_protokoll_entwuerfe_loeschen(db, datetime.now(timezone.utc))
        rest = sorted([p["id"] async for p in db.pickup_protocols.find({}, {"_id": 0, "id": 1})])
        return n, rest

    n, rest = run(lauf())
    assert n == 2
    assert rest == ["p_da", "p_final", "p_frisch"], "Beleg, Termin-Entwurf und Frische bleiben"


# ====================================================================== B-02
def test_b02_grenzen_in_richtiger_reihenfolge():
    R = _module("regeln")
    with pytest.raises(R.RegelFehler) as e:
        R.regeln_validieren({"mileage": {"mode": "custom", "min": 200000, "max": 1000}})
    assert "größer" in str(e.value)
    with pytest.raises(R.RegelFehler):
        R.regeln_validieren({"first_registration": {"mode": "year_range", "from": 2024, "to": 2001}})
    ok = R.regeln_validieren({"mileage": {"mode": "custom", "min": 1000, "max": 1000},
                              "first_registration": {"mode": "year_range", "from": 2001, "to": None}})
    assert ok["mileage"]["min"] == 1000 and ok["first_registration"]["to"] is None


# ====================================================================== B-03
@pytest.mark.parametrize("marke,modell,erwartet", [
    ("Mercedes-Benz", "CLS", "clsklasse"),
    ("Mercedes-Benz", "GLS", "glsklasse"),
    ("Mercedes-Benz", "SLS", "slsamg"),
    ("Mercedes-Benz", "Actros", None),
    ("Mercedes-Benz", "C", "cklasse"),
    ("Mercedes-Benz", "C 200", "c200"),
    ("Mercedes-Benz", "ML 350", "ml350"),
    ("Mercedes-Benz", "E 220 d", "e220"),
    ("Volkswagen", "Golf Variant", "golf"),
    ("Volkswagen", "Passat Variant", "passatvariant"),
])
def test_b03_modellzuordnung(marke, modell, erwartet):
    M = _module("mobile_service")
    _mid, eintrag = M._resolve_make({"make_label": marke})
    zurueck = {v: k for k, v in (eintrag.get("models") or {}).items()}
    assert zurueck.get(M._resolve_model(eintrag, {"model_label": modell})) == erwartet


# ====================================================================== P-05
@pytest.mark.parametrize("adresse", ["a@b.de, c@d.de", "a@b.de;c@d.de", "kein-at-zeichen", ""])
def test_p05_email_versand_nur_an_eine_gueltige_adresse(frisch, adresse):
    C = _module("routes.contracts")
    db, run = frisch.db, frisch.run
    chef = {"id": "chef1", "dealer_id": "d1", "role": "dealer"}

    async def lauf():
        await db.generated_pdfs.insert_one({"id": "c1", "dealer_id": "d1", "user_id": "chef1",
                                            "version": 1, "send_status": [],
                                            "contract_data": {}, "status": "erstellt"})
        with pytest.raises(HTTPException) as e:
            await C.send_contract("c1", C.SendIn(channel="email", recipient=adresse,
                                                 message="Hallo"), chef)
        c = await db.generated_pdfs.find_one({"id": "c1"}, {"_id": 0})
        return e.value, c

    fehler, c = run(lauf())
    assert fehler.status_code == 422 and "EINE" in fehler.detail
    assert c["send_status"] == [], "nichts reserviert, nichts versendet"


# ====================================================================== P-06
def test_p06_geleerte_fin_bleibt_leer():
    from vertrag_felder import _apply_contract_overrides
    fahrzeug = {"vin": "WVWZZZ1KZAW000001", "exterior_color": "Blau"}
    v, _ = _apply_contract_overrides(contract={"vehicle_vin": "", "vehicle_color": ""},
                                     vehicle=fahrzeug, dealer={})
    assert v["vin"] == "" and v["fin"] == "", "bewusst geleert = keine FIN im Vertrag"
    assert v["exterior_color"] == "Blau", "andere leere Felder: weiter der Inseratswert"
    v2, _ = _apply_contract_overrides(contract={}, vehicle=fahrzeug, dealer={})
    assert v2["vin"] == "WVWZZZ1KZAW000001", "Feld fehlt ganz (Altvertrag) = Inseratswert"


# ====================================================================== R1-30
def test_r1_30_freitexte_werden_mitgeloescht(frisch, monkeypatch):
    CS = _module("cleanup_service")
    db, run = frisch.db, frisch.run

    async def lauf():
        await db.pickup_protocols.insert_one({
            "id": "p1", "appointment_id": "t1", "status": "final", "seller_name": "Vera V.",
            "notes": "Verkaeufer: 0170 1234567, Hof hinten", "sondervereinbarung": "Winterreifen an Frau V.",
            "preis_notiz": "Bar an Herrn V.", "place": "Hannover"})
        await CS._protokolle_pii_entfernen(db, ["t1"], "d1", datetime.now(timezone.utc).isoformat())
        return await db.pickup_protocols.find_one({"id": "p1"}, {"_id": 0})

    p = run(lauf())
    assert p["notes"] == "" and p["sondervereinbarung"] == "" and p["preis_notiz"] == ""
    assert p["seller_name"] == "" and p["place"] == "" and p["pii_geloescht_at"]
    q = inspect.getsource(CS)
    assert q.count('"pickup_address": "", "notes": ""') >= 2, "auch die Termin-Notizen"


# ====================================================================== R1-35
def test_r1_35_befoerderung_ohne_anderen_chef_setzt_den_zeiger(frisch):
    AD = _module("routes.admin")
    db, run = frisch.db, frisch.run
    admin = {"id": "sa1", "role": "admin", "is_super_admin": True}

    async def lauf():
        await db.dealers.insert_one({"id": "d1", "user_id": "chef_weg", "kunden_nr": 10077,
                                     "company_name": "F GmbH"})
        await db.users.insert_one({"id": "s1", "dealer_id": "d1", "role": "sucher", "active": True,
                                   "kontonummer": "10077-2", "kontonummer_basis": 10077})
        await AD.admin_update_user("s1", {"role": "dealer"}, admin)
        return (await db.dealers.find_one({"id": "d1"}, {"_id": 0}),
                await db.users.find_one({"id": "s1"}, {"_id": 0}))

    firma, konto = run(lauf())
    assert konto["role"] == "dealer"
    assert firma["user_id"] == "s1", "der Firmen-Zeiger zeigt auf den neuen Chef"


# ====================================================================== AD-16
def test_ad16_nutzerliste_meldet_kuerzung(frisch):
    AD = _module("routes.admin")
    db, run = frisch.db, frisch.run

    async def lauf():
        await db.users.insert_many([{"id": f"u{i}", "role": "sucher", "created_at": _vor(i)}
                                    for i in range(3)])
        r1, r2 = Response(), Response()
        a = await AD.admin_list_users(r1, None, page=1, limit=2)
        b = await AD.admin_list_users(r2, None, page=1, limit=5)
        return a, r1.headers.get("x-truncated"), b, r2.headers.get("x-truncated")

    a, kopf_a, b, kopf_b = run(lauf())
    assert len(a) == 2 and kopf_a == "1"
    assert len(b) == 3 and kopf_b is None
