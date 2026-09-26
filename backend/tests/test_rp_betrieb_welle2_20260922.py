# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026 — Team Betrieb, zweite Welle: Übergaben der
anderen Teams (Indizes, Migrationen, Aufräumlauf, Validierung, Datei-Links,
Produktions-Check).

In-Prozess gegen Wegwerf-Datenbanken (autoschnell_rpb2_<zufall>), kein
Server, KEIN `import server` (bindet den Motor-Client an die Test-Schleife) —
server.py wird per AST gelesen.
"""
import ast
import asyncio
import importlib
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from urllib.parse import parse_qs, urlparse

import pytest
from pymongo.errors import DuplicateKeyError

BACKEND = Path(__file__).resolve().parents[1]
WURZEL = BACKEND.parent
sys.path.insert(0, str(BACKEND))
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _iso(delta_s: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


def _tage(n: float) -> str:
    return _iso(n * 86400)


def _m(name):
    return importlib.import_module(name)


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_rpb2_{uuid.uuid4().hex[:10]}"
    w = SimpleNamespace(db=client[name], run=loop.run_until_complete, name=name)
    try:
        yield w
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def _alarm(w, typ, ref):
    return w.run(w.db.betriebsalarme.find_one({"typ": typ, "ref": ref, "offen": True}))


def _server_quelle() -> str:
    return (BACKEND / "server.py").read_text(encoding="utf-8")


def _abschnitt(q: str, von: str, bis: str) -> str:
    return q[q.index(von):q.index(bis, q.index(von))]


# ====================================================================== RP-083 / RP-182
def test_rp083_aktive_stati_wie_im_inserat_modul():
    """indizes.INSERAT_AKTIV ist eine Kopie von routes.resale._AKTIV (ohne
    das Routenmodul zu laden) — beide muessen gleich bleiben."""
    baum = ast.parse((BACKEND / "routes" / "resale.py").read_text(encoding="utf-8"))
    werte = None
    for k in baum.body:
        if isinstance(k, ast.Assign) and any(getattr(t, "id", None) == "_AKTIV" for t in k.targets):
            werte = ast.literal_eval(k.value)
    assert werte is not None
    assert tuple(werte) == _m("indizes").INSERAT_AKTIV


def _inserat(lid, vid, status, dealer="d1", **extra):
    return {"id": lid, "dealer_id": dealer, "vehicle_id": vid, "status": status, **extra}


def test_rp083_ein_aktives_inserat_je_fahrzeug(welt):
    I = _m("indizes")
    w = welt
    w.run(w.db.resale_listings.insert_many([
        _inserat("alt_geloescht", "v1", "geloescht"),
        _inserat("alt_verkauft", "v1", "verkauft"),
        _inserat("ohne_fz_1", None, "entwurf"),
        _inserat("ohne_fz_2", None, "entwurf"),
    ]))
    assert w.run(I.inserat_je_fahrzeug_unique_index(w.db)) is True
    w.run(w.db.resale_listings.insert_one(_inserat("l1", "v1", "entwurf")))
    with pytest.raises(DuplicateKeyError):
        w.run(w.db.resale_listings.insert_one(_inserat("l2", "v1", "veroeffentlicht")))
    # andere Firma, anderes Fahrzeug, beendete Inserate: erlaubt
    w.run(w.db.resale_listings.insert_one(_inserat("l3", "v1", "entwurf", dealer="d2")))
    w.run(w.db.resale_listings.insert_one(_inserat("l4", "v2", "reserviert")))
    w.run(w.db.resale_listings.insert_one(_inserat("l5", "v1", "geloescht")))
    # Beenden gibt das Fahrzeug frei
    w.run(w.db.resale_listings.update_one({"id": "l1"}, {"$set": {"status": "verkauft"}}))
    w.run(w.db.resale_listings.insert_one(_inserat("l6", "v1", "entwurf")))
    # zweiter Start: idempotent
    assert w.run(I.inserat_je_fahrzeug_unique_index(w.db)) is True


def test_rp083_altdubletten_nur_gemeldet_nichts_geloescht(welt):
    I = _m("indizes")
    w = welt
    w.run(w.db.resale_listings.insert_many([
        _inserat("a", "v9", "veroeffentlicht"), _inserat("b", "v9", "verkaufsbereit")]))
    assert w.run(I.inserat_je_fahrzeug_unique_index(w.db)) is False
    a = _alarm(w, "inserat_dubletten_je_fahrzeug", "resale_listings")
    assert a and "d1/v9" in a["details"]["beispiele"]
    assert w.run(w.db.resale_listings.count_documents({"vehicle_id": "v9"})) == 2
    assert "ein_aktives_je_fahrzeug" not in w.run(w.db.resale_listings.index_information())
    assert "ein_aktives_je_fahrzeug" not in I.FEHLENDE_UNIQUE, "kein Startabbruch"
    # bereinigt -> naechster Start legt an und schliesst den Alarm
    w.run(w.db.resale_listings.update_one({"id": "b"}, {"$set": {"status": "geloescht"}}))
    assert w.run(I.inserat_je_fahrzeug_unique_index(w.db)) is True
    assert _alarm(w, "inserat_dubletten_je_fahrzeug", "resale_listings") is None


def test_rp083_fruehere_indexfassung_wird_ersetzt(welt):
    """Der Test des Marktplatz-Teams legt den Index ohne vehicle_id-Filter an;
    ensure_indexes ersetzt ihn durch die endgueltige Fassung."""
    I = _m("indizes")
    w = welt
    w.run(w.db.resale_listings.create_index(
        [("dealer_id", 1), ("vehicle_id", 1)], unique=True, name="ein_aktives_je_fahrzeug",
        partialFilterExpression={"status": {"$in": list(I.INSERAT_AKTIV)}}))
    assert w.run(I.inserat_je_fahrzeug_unique_index(w.db)) is True
    info = w.run(w.db.resale_listings.index_information())["ein_aktives_je_fahrzeug"]
    assert info["partialFilterExpression"]["vehicle_id"] == {"$type": "string"}


# ====================================================================== RP-046 / 145 / 152
def test_rp145_ein_aktives_firmen_abo_je_firma(welt):
    I = _m("indizes")
    w = welt
    assert w.run(I.firmen_abo_unique_index(w.db)) is True
    w.run(w.db.subscriptions.insert_one({"id": "s1", "dealer_id": "d1", "status": "active",
                                         "art": "firma"}))
    with pytest.raises(DuplicateKeyError):
        w.run(w.db.subscriptions.insert_one({"id": "s2", "dealer_id": "d1",
                                             "status": "active", "art": "firma"}))
    # persoenliche Abos, ersetzte Firmen-Abos und andere Firmen stoeren nicht
    w.run(w.db.subscriptions.insert_many([
        {"id": "s3", "dealer_id": "d1", "status": "active", "subject_user_id": "u1"},
        {"id": "s4", "dealer_id": "d1", "status": "ersetzt", "art": "firma"},
        {"id": "s5", "dealer_id": "d2", "status": "active", "art": "firma"}]))
    # der plan_type-Weg: erst ersetzen, dann einfuegen — geht
    w.run(w.db.subscriptions.update_one({"id": "s1"}, {"$set": {"status": "ersetzt"}}))
    w.run(w.db.subscriptions.insert_one({"id": "s6", "dealer_id": "d1", "status": "active",
                                         "art": "firma"}))


def test_rp145_migration_markiert_altbestand_ohne_abbruch(welt):
    I, M = _m("indizes"), _m("migrationen")
    w = welt
    # Der Index steht schon VOR der Migration (angelegt, als noch keine
    # Dubletten da waren). Rollenprüfung 22.09.2026 (Review): mit Alt-
    # Dubletten legt der Indexlauf ihn nicht mehr an — die Dublettensuche
    # zaehlt seitdem auch unmarkierte Firmen-Abos.
    assert w.run(I.firmen_abo_unique_index(w.db)) is True
    w.run(w.db.subscriptions.insert_many([
        {"id": "alt1", "dealer_id": "d1", "status": "active"},
        {"id": "alt2", "dealer_id": "d1", "status": "active", "subject_user_id": None},
        {"id": "alt3", "dealer_id": "d1", "status": "ersetzt"},
        {"id": "pers", "dealer_id": "d1", "status": "active", "subject_user_id": "u1"},
        {"id": "d2", "dealer_id": "d2", "status": "active"},
    ]))
    stats = w.run(M.m11_firmen_abo_art(w.db))
    assert stats["konflikte"] == 1 and stats["aktiv_markiert"] == 2 \
        and stats["inaktiv_markiert"] == 1, stats
    art = {s["id"]: s.get("art") for s in w.run(w.db.subscriptions.find({}).to_list(20))}
    assert art["alt3"] == "firma" and art["d2"] == "firma" and art["pers"] is None
    assert [art["alt1"], art["alt2"]].count("firma") == 1, "das zweite bleibt unveraendert"
    assert _alarm(w, "mehrfache_aktive_firmen_abos", "subscriptions")
    # idempotent
    assert w.run(M.m11_firmen_abo_art(w.db))["aktiv_markiert"] == 0


def test_rp145_mehrere_chefkonten_werden_gemeldet(welt):
    CS = _m("cleanup_service")
    w = welt
    w.run(w.db.users.insert_many([
        {"id": "c1", "dealer_id": "d1", "role": "dealer", "kontonummer": "10001"},
        {"id": "c2", "dealer_id": "d1", "role": "dealer", "kontonummer": "10002"},
        {"id": "s1", "dealer_id": "d1", "role": "sucher"},
        {"id": "c3", "dealer_id": "d2", "role": "dealer"},
        {"id": "c4", "dealer_id": "d3", "role": "dealer"},
        {"id": "c5", "dealer_id": "d3", "role": "dealer", "loeschung": {"status": "laeuft"}},
    ]))
    assert w.run(CS.mehrere_chefkonten_melden(w.db)) == 1
    a = _alarm(w, "mehrere_chefkonten", "d1")
    assert a and "10001" in a["details"]["kontonummern"] and a["details"]["konten"] == 2
    assert _alarm(w, "mehrere_chefkonten", "d3") is None, "Konto in Loeschung zaehlt nicht"
    # Betreiber stuft das Zweitkonto herab -> Alarm zu
    w.run(w.db.users.update_one({"id": "c2"}, {"$set": {"role": "sucher"}}))
    assert w.run(CS.mehrere_chefkonten_melden(w.db)) == 0
    assert _alarm(w, "mehrere_chefkonten", "d1") is None
    # laeuft im Aufraeumlauf mit
    assert 's("mehrere_chefkonten"' in (BACKEND / "cleanup_service.py").read_text(encoding="utf-8")


def test_rp145_seed_und_indexaufrufe_im_start():
    q = _server_quelle()
    alle = _abschnitt(q, "async def _alle_indexe():", "async def run_schreiber_melden_forever(")
    assert "await firmen_abo_unique_index(db)" in alle
    assert "await inserat_je_fahrzeug_unique_index(db)" in alle
    seed = _abschnitt(q, "async def seed_super_admin():", "async def on_start(")
    assert seed.count('"art": "firma"') == 2, "beide Seed-Abos sind Firmen-Abos"


# ====================================================================== RP-066 / RP-165
def test_rp066_idempotenz_index_im_start(welt):
    q = _server_quelle()
    ensure = _abschnitt(q, "async def ensure_indexes():", "async def seed_super_admin(")
    assert 'name="bericht_idempotenz", weich=True' in ensure
    assert "[(\"appointment_id\", 1), (\"client_bericht_id\", 1)]" in ensure
    # dieselbe Definition, echt angelegt: gleiche ID je Termin nur einmal,
    # Berichte ohne Schluessel beliebig oft
    I = _m("indizes")
    w = welt
    assert w.run(I.unique_anlegen(
        w.db.pickup_reports, [("appointment_id", 1), ("client_bericht_id", 1)],
        name="bericht_idempotenz", weich=True,
        partialFilterExpression={"client_bericht_id": {"$type": "string"}})) is True
    w.run(w.db.pickup_reports.insert_many([
        {"id": "r1", "appointment_id": "t1", "version": 1},
        {"id": "r2", "appointment_id": "t1", "version": 2},
        {"id": "r3", "appointment_id": "t1", "version": 3, "client_bericht_id": "abc12345"}]))
    with pytest.raises(DuplicateKeyError):
        w.run(w.db.pickup_reports.insert_one(
            {"id": "r4", "appointment_id": "t1", "version": 4, "client_bericht_id": "abc12345"}))
    w.run(w.db.pickup_reports.insert_one(
        {"id": "r5", "appointment_id": "t2", "version": 1, "client_bericht_id": "abc12345"}))


# ====================================================================== RP-223 / RP-374
def test_rp223_migration_traegt_abschlusszeit_nach(welt):
    M = _m("migrationen")
    w = welt
    angelegt, geaendert = _tage(-40), _tage(-2)
    w.run(w.db.appointments.insert_many([
        {"id": "t_erl", "status": "erledigt", "created_at": angelegt},
        {"id": "t_sto", "status": "storniert", "created_at": angelegt, "abgeschlossen_seit": ""},
        {"id": "t_off", "status": "offen", "created_at": angelegt},
        {"id": "t_hat", "status": "abgeholt", "created_at": angelegt,
         "status_changed_at": geaendert},
        {"id": "t_ohne", "status": "abgeholt"},
    ]))
    assert w.run(M.m12_termine_abschluss_zeit(w.db)) == {"termine": 2}
    t = {a["id"]: a for a in w.run(w.db.appointments.find({}, {"_id": 0}).to_list(10))}
    for tid in ("t_erl", "t_sto"):
        assert t[tid]["abgeschlossen_seit"] == angelegt == t[tid]["status_changed_at"]
        assert t[tid]["abschluss_zeit_nachgetragen"] is True
    assert "abgeschlossen_seit" not in t["t_off"], "offene Termine bleiben"
    assert t["t_hat"]["status_changed_at"] == geaendert \
        and "abgeschlossen_seit" not in t["t_hat"], "vorhandene Zeit bleibt"
    assert "abgeschlossen_seit" not in t["t_ohne"]
    assert w.run(M.m12_termine_abschluss_zeit(w.db)) == {"termine": 0}


# ====================================================================== RP-532
def test_rp532_migration_fotomodus(welt):
    M = _m("migrationen")
    w = welt
    w.run(w.db.resale_listings.insert_many([
        {"id": "e1", "photos": {"mode": "einkauf", "uploaded_keys": ["resale/d/1.jpg"]}},
        {"id": "e2", "photos": {"mode": "einkauf", "uploaded_keys": []}},
        {"id": "e3", "photos": {"uploaded_keys": ["resale/d/2.jpg"]}},
        {"id": "e4", "photos": {"mode": "neu", "uploaded_keys": ["resale/d/3.jpg"]}},
        {"id": "e5"},
    ]))
    assert w.run(M.m13_inserat_fotomodus(w.db)) == {"inserate": 2}
    modus = {l["id"]: (l.get("photos") or {}).get("mode")
             for l in w.run(w.db.resale_listings.find({}).to_list(10))}
    assert modus == {"e1": "beide", "e2": "einkauf", "e3": "beide", "e4": "neu", "e5": None}


def test_migrationen_eingetragen():
    M = _m("migrationen")
    nummern = [n for n, _, _ in M.MIGRATIONEN]
    assert nummern == sorted(nummern) and len(set(nummern)) == len(nummern)
    assert M.ZIEL_VERSION == max(nummern) == 17   # 17: Markt-Standard v3 (26.09.2026 abends)
    namen = {n: name for n, name, _ in M.MIGRATIONEN}
    assert namen[11] == "firmen_abo_art" and namen[12] == "termine_abschluss_zeit" \
        and namen[13] == "inserat_fotomodus" and namen[14] == "vertrags_kundennummern"


# ====================================================================== RP-517
def test_rp517_freigegebenes_inserat_bekommt_eine_neue_laufzeit(welt):
    CS = _m("cleanup_service")
    w = welt
    w.run(w.db.vehicles.insert_many([
        {"id": "vf", "dealer_id": "d1", "lifecycle": "veroeffentlicht"},
        {"id": "va", "dealer_id": "d1", "lifecycle": "veroeffentlicht"}]))
    w.run(w.db.resale_listings.insert_many([
        # vor 30 Tagen veroeffentlicht, vor 2 Tagen vom Betreiber freigegeben
        {"id": "frei", "dealer_id": "d1", "vehicle_id": "vf", "status": "veroeffentlicht",
         "published_at": _tage(-30), "wieder_veroeffentlicht_am": _tage(-2),
         "photos": {"uploaded_keys": []}},
        # Freigabe laenger her als die Laufzeit: laeuft ab
        {"id": "alt", "dealer_id": "d1", "vehicle_id": "va", "status": "veroeffentlicht",
         "published_at": _tage(-60), "wieder_veroeffentlicht_am": _tage(-25),
         "photos": {"uploaded_keys": []}}]))
    assert w.run(CS.abgelaufene_inserate_entfernen(w.db, datetime.now(timezone.utc))) == 1
    assert w.run(w.db.resale_listings.find_one({"id": "frei"}))["status"] == "veroeffentlicht"
    assert w.run(w.db.resale_listings.find_one({"id": "alt"})) is None
    assert w.run(w.db.vehicles.find_one({"id": "va"}))["lifecycle"] == "bestand"
    assert w.run(w.db.vehicles.find_one({"id": "vf"}))["lifecycle"] == "veroeffentlicht"


# ====================================================================== RP-265 / RP-080 Nr. 5
@pytest.mark.parametrize("status,hat_vorgaenge,erwartet_gesetzt,erwartet_rueck", [
    ("erledigt", False, "abgeholt", "abgeholt"),          # RP-080 Nr. 5: erledigt = abgeholt
    ("abgeholt", False, "abgeholt", "abgeholt"),
    ("nicht abgeholt", False, "nicht_abgeholt", "nicht_abgeholt"),
    ("storniert", False, None, None),                    # Storno aendert nichts
    ("offen", False, "abholung_geplant", "abholung_geplant"),
    ("nicht abgeholt", True, None, "zusammengefasst"),   # RP-265: Kollege kauft noch
    ("offen", True, None, "zusammengefasst"),
])
def test_rp265_fahrzeug_ohne_vorgang(monkeypatch, status, hat_vorgaenge, erwartet_gesetzt,
                                     erwartet_rueck):
    CS, KV, L = _m("cleanup_service"), _m("kaufvorgang"), _m("lifecycle")
    gesetzt, zusammengefasst = [], []

    async def _hat(vid, did):
        return hat_vorgaenge

    async def _agg(vid, did, **kw):
        zusammengefasst.append((vid, did))

    async def _setzen(vid, did, ziel, **kw):
        gesetzt.append(ziel)
        return True
    monkeypatch.setattr(KV, "fahrzeug_hat_vorgaenge", _hat)
    monkeypatch.setattr(KV, "fahrzeug_status_aggregieren", _agg)
    monkeypatch.setattr(L, "try_set_lifecycle", _setzen)
    loop = asyncio.new_event_loop()
    try:
        rueck = loop.run_until_complete(CS._fahrzeug_ohne_vorgang_nachziehen(
            {"id": "t", "vehicle_id": "v1", "dealer_id": "d1"}, status))
    finally:
        loop.close()
    assert rueck == erwartet_rueck
    assert gesetzt == ([erwartet_gesetzt] if erwartet_gesetzt else [])
    assert bool(zusammengefasst) == (erwartet_rueck == "zusammengefasst")


def test_rp265_beide_aufraeumwege_nutzen_den_helfer():
    import inspect
    CS = _m("cleanup_service")
    for fn in (CS.termin_nacharbeit_nachholen, CS.termine_frisch_abgleichen):
        q = inspect.getsource(fn)
        assert "_fahrzeug_ohne_vorgang_nachziehen(appt, status)" in q, fn.__name__
        assert "try_set_lifecycle(" not in q, f"{fn.__name__}: kein direkter Statuswechsel mehr"


# ====================================================================== RP-080 Nr. 2
def test_rp080_gesperrte_fahrer_verlieren_die_zuweisung(welt):
    CS = _m("cleanup_service")
    w = welt
    w.run(w.db.dealer_drivers.insert_many([
        {"dealer_id": "d1", "driver_account_id": f} for f in ("f_ok", "f_aus", "f_weg", "f_ohne")]))
    w.run(w.db.driver_accounts.insert_many([
        {"id": "f_ok", "active": True},
        {"id": "f_aus", "active": False},
        {"id": "f_weg", "active": True, "loeschung": {"status": "laeuft"}},
        # f_ohne: kein Konto-Dokument (Testwelten) — Verknuepfung zaehlt
    ]))
    w.run(w.db.appointments.insert_many([
        {"id": f"t_{f}", "dealer_id": "d1", "driver_id": f, "status": "offen",
         "zuteilung": "angenommen"} for f in ("f_ok", "f_aus", "f_weg", "f_ohne")]))
    w.run(w.db.appointments.insert_one({"id": "t_zu", "dealer_id": "d1", "driver_id": "f_aus",
                                        "status": "abgeholt", "zuteilung": "angenommen"}))
    w.run(CS.fahrer_verknuepfung_abgleichen(w.db))
    t = {a["id"]: a for a in w.run(w.db.appointments.find({}, {"_id": 0}).to_list(10))}
    assert t["t_f_ok"]["driver_id"] == "f_ok" and t["t_f_ohne"]["driver_id"] == "f_ohne"
    for tid in ("t_f_aus", "t_f_weg"):
        assert "driver_id" not in t[tid] and t[tid]["zuteilung"] is None, tid
    assert t["t_zu"]["driver_id"] == "f_aus", "abgeschlossene Fahrten behalten den Fahrer"
    a = _alarm(w, "fahrer_ohne_verknuepfung_getrennt", "f_aus")
    assert a and a["details"]["grund"] == "konto_gesperrt"


# ====================================================================== RP-135 / RP-036
def _validierung_ns():
    """_FELD_NAMEN, _feldname und _meldung_deutsch aus server.py, ohne es zu
    importieren."""
    baum = ast.parse(_server_quelle())
    teile = []
    for k in baum.body:
        if isinstance(k, ast.Assign) and any(getattr(t, "id", None) == "_FELD_NAMEN"
                                             for t in k.targets):
            teile.append(k)
        if isinstance(k, ast.FunctionDef) and k.name in ("_feldname", "_meldung_deutsch"):
            teile.append(k)
    assert len(teile) == 3
    ns = {"Optional": Optional}
    exec(compile(ast.Module(body=teile, type_ignores=[]), "server.py", "exec"), ns)
    return ns


def test_rp135_laengenfehler_kommen_deutsch():
    ns = _validierung_ns()
    f = ns["_meldung_deutsch"]
    assert f({"type": "string_too_long", "loc": ("body", "description"),
              "ctx": {"max_length": 500}}) == "Beschreibung: höchstens 500 Zeichen"
    assert f({"type": "too_long", "loc": ("body", "costs"),
              "ctx": {"max_length": 50}}) == "Kosten: höchstens 50 Einträge"
    assert f({"type": "string_too_long", "loc": ("body", "unbekannt_feld"),
              "ctx": {"max_length": 9}}) == "unbekannt_feld: höchstens 9 Zeichen"
    assert f({"type": "string_too_long", "loc": ("body",),
              "ctx": {"max_length": 9}}) == "Höchstens 9 Zeichen"
    assert f({"type": "string_too_short", "loc": ("body", "grund"),
              "ctx": {"min_length": 1}}) == "Grund: darf nicht leer sein"
    assert f({"type": "string_too_long", "loc": ("body", "costs", 3, "label"),
              "ctx": {"max_length": 80}}) == "Bezeichnung: höchstens 80 Zeichen"
    # alles andere bleibt unveraendert
    assert f({"type": "missing", "loc": ("body", "title")}) is None
    assert f({"type": "string_too_long", "loc": ("body", "title"), "ctx": {}}) is None
    q = _server_quelle()
    handler = _abschnitt(q, "async def _validierungsfehler(", "_GEHEIME_FELDER")
    assert 'e["msg"] = deutsch' in handler


def test_rp135_gleicher_wortlaut_wie_die_oberflaeche():
    """Server und frontend/src/lib/api.js (validierungsText) nennen dieselben
    Felder gleich (nur gemeinsame Felder — neue duerfen einseitig dazukommen)."""
    import re
    ns = _validierung_ns()
    js = (WURZEL / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    block = js[js.index("const FELDNAMEN = {"):js.index("};", js.index("const FELDNAMEN = {"))]
    frontend = dict(re.findall(r'(\w+): "([^"]+)"', block))
    assert frontend, "FELDNAMEN in api.js nicht gefunden"
    gemeinsam = set(frontend) & set(ns["_FELD_NAMEN"])
    assert len(gemeinsam) >= 10
    for feld in gemeinsam:
        assert ns["_FELD_NAMEN"][feld] == frontend[feld], feld


# ====================================================================== RP-546
def test_rp546_neues_token_ist_fuer_die_oberflaeche_lesbar():
    baum = ast.parse(_server_quelle())
    gefunden = None
    for k in ast.walk(baum):
        if isinstance(k, ast.keyword) and k.arg == "expose_headers":
            gefunden = ast.literal_eval(k.value)
    assert gefunden and "X-Neues-Token" in gefunden
    assert _m("auth").NEUES_TOKEN_KOPF == "X-Neues-Token"


# ====================================================================== RP-098 Nr. 8
def test_rp098_inseratsfotos_laenger_signiert(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "JWT_SECRET", "rpb2-" + uuid.uuid4().hex + uuid.uuid4().hex)
    D = _m("dateien")

    def laufzeit(url):
        return int(parse_qs(urlparse(url).query)["exp"][0]) - int(time.time())
    inserat = f"resale/d1/{uuid.uuid4().hex}.jpg"
    anderes = f"vehicles/d1/{uuid.uuid4().hex}.jpg"
    assert laufzeit(D.signierte_datei_url(inserat)) > 23 * 3600
    assert laufzeit(D.signierte_datei_url(anderes)) <= D.STANDARD_TTL
    assert laufzeit(D.signierte_datei_url(inserat, ttl=120)) <= 120, "ausdruecklicher ttl gewinnt"
    url = D.signierte_datei_url(inserat)
    q = parse_qs(urlparse(url).query)
    assert D.signatur_gueltig(inserat, q["exp"][0], q["sig"][0])
    compose = (WURZEL / "docker-compose.yml").read_text(encoding="utf-8")
    assert "- DATEI_LINK_TTL_INSERAT_SEKUNDEN=${DATEI_LINK_TTL_INSERAT_SEKUNDEN:-86400}" in compose


# ====================================================================== RP-549 / RP-553
class _Log:
    def __init__(self):
        self.zeilen = []

    def warning(self, text, *a):
        self.zeilen.append(str(text) % a if a else str(text))

    info = error = warning


@pytest.mark.parametrize("wert,warnt", [
    ("", False), ("memo23~mobile-de-scraper", False), ("memo23/mobile-de-scraper", False),
    ("mobile-de-scraper", True), ("memo23 mobile-de-scraper", True),
])
def test_rp549_vertippter_actor_name_wird_gemeldet(monkeypatch, wert, warnt):
    PC = _m("production_check")
    monkeypatch.setenv("APP_ENV", "entwicklung")
    monkeypatch.setenv("APIFY_TOKEN", "apify_test_token")
    monkeypatch.setenv("APIFY_MOBILE_ACTOR", wert)
    monkeypatch.setenv("APIFY_AUTOSCOUT_ACTOR", "")
    log = _Log()
    PC.pruefe_produktion(log)
    alles = "\n".join(log.zeilen)
    assert ("APIFY_MOBILE_ACTOR=" in alles) == warnt, alles
    assert "APIFY_AUTOSCOUT_ACTOR=" not in alles


def test_rp553_apify_schalter_dokumentiert():
    beispiel = (WURZEL / ".env.example").read_text(encoding="utf-8")
    compose = (WURZEL / "docker-compose.yml").read_text(encoding="utf-8")
    for name in ("APIFY_MEMORY_MB", "APIFY_MOBILE_BUILD", "APIFY_AUTOSCOUT_BUILD"):
        assert f"\n{name}=" in beispiel, name
        assert f"- {name}=${{{name}:-}}" in compose, name


# ====================================================================== RP-550 / RP-249
def test_rp550_snapshot_zugriffe_im_speicher_pool(monkeypatch):
    SS, ST = _m("snapshot_service"), _m("storage_service")
    aufrufe = []

    async def _pool(fn, *args):
        aufrufe.append(fn.__name__)
        return fn(*args)
    monkeypatch.setattr(ST, "speicher_aufruf", _pool)
    monkeypatch.setattr(SS, "delete_object", lambda p: True)
    monkeypatch.setattr(SS, "get_object", lambda p: (b"x", "image/jpeg"))
    loop = asyncio.new_event_loop()
    try:
        assert loop.run_until_complete(SS.delete_object_async("snapshots/x.jpg")) is True
        assert loop.run_until_complete(SS.get_object_async("snapshots/x.jpg")) == (b"x", "image/jpeg")
    finally:
        loop.close()
    assert aufrufe == ["<lambda>", "<lambda>"]


def test_rp249_tzdata_im_image():
    req = (BACKEND / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert any(z.strip().startswith("tzdata==") for z in req)


def test_rp556_geraetewechsel_mit_gnadenfrist_dokumentiert():
    doku = (WURZEL / "DEPLOYMENT.md").read_text(encoding="utf-8")
    abschnitt = doku[doku.index("**Zwei-Faktor wird nicht aus einer laufenden Sitzung ersetzt.**"):]
    abschnitt = abschnitt[:abschnitt.index("\n- **")]
    assert "30 Minuten" in abschnitt and "in einem Zug" in abschnitt
    assert "mfa_pruefen.py" in abschnitt
