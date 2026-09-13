# -*- coding: utf-8 -*-
"""Kontonummer (13.09.2026), Schritt 1 — Grundlage, in-process.

  - kontonummer.py: Normalisierung, anmeldekennung, Dubletten-Erkennung
  - kontenanlage.py auf Wegwerf-DB: parallele Vergabe, Chef = kunden_nr,
    Sucher-Zusatz ohne Wiedervergabe, Firma ohne kunden_nr, gemeinsame Reihe
    fuer Kaeufer/Fahrer, Selbstheilung ueber drei Sammlungen, konten_ohne_nummer
  - indizes.konto_indizes: Teil-Index, Dublette -> Alarm/Abbruch, zwei
    parallele Aufrufe (auch beim Ersetzen eines alten Index)
  - Konto-Limiter: Sperre gleich fuer bekannte und unbekannte Kennung,
    bekannte IP frei, reset, LOGIN_KONTO_LIMIT=0 nur Alarm, stand() zaehlt nicht
  - Nummernsuchen nutzen den Teil-Index (explain: IXSCAN kontonummer_eindeutig)
  - Route buyer_login: nach 30 Fehlversuchen von fremden IPs 429 (gleicher
    Text fuer vorhandene und unbekannte Nummer, ohne bcrypt), von der
    gemerkten IP weiter 401/200; ein Erfolg leert den Konto-Zaehler nicht
  - Verdrahtung der drei Login-Routen per Quelltext

Kein Import von server.py. routes.marketplace wird NUR hier am Modulanfang
importiert (beim Einsammeln, nie erstmals innerhalb einer Test-Schleife —
Event-Loop-Falle der In-Prozess-Tests).
"""
import ast
import asyncio
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import deps  # noqa: E402  (laedt backend/.env)
import indizes  # noqa: E402
import kontenanlage as KA  # noqa: E402
import rate_limiter as RL  # noqa: E402
import routes.marketplace as MARKT  # noqa: E402  (am Modulanfang, siehe oben)
from kontonummer import (anmeldekennung, ist_kontonummer,  # noqa: E402
                         ist_kontonummer_dublette, normalisieren, nummer_bedingung,
                         sucher_nummer)

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def wegwerf():
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    s = uuid.uuid4().hex[:10]
    name = f"autoschnell_konto_{s}"
    w = SimpleNamespace(db=client[name], run=loop.run_until_complete, s=s)
    try:
        yield w
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def _konto(kid, **extra):
    doc = {"id": kid, "email": f"{kid}@konto.test", "password_hash": "x",
           "active": True, "created_at": _jetzt()}
    doc.update(extra)
    return doc


# ====================================================================== rein
def test_normalisieren_und_anmeldekennung():
    for roh in ("10023 2", "10023/2", " 10023-02 ", "10023.2", "10023_2",
                "10023–2", "10023—2", "10023−2", "１００２３-2"):
        assert normalisieren(roh) == "10023-2", roh
    assert normalisieren("10023") == "10023"
    assert normalisieren(" 010023 ") == "10023"
    for kaputt in ("ci-superadmin", "10023  2", "10023-0", "10023-", "0123", "123",
                   "1234567890", "a@b.de", "", "   ", None, 10023, "10023-12345",
                   "10023 2 3"):
        assert normalisieren(kaputt) is None, kaputt
    assert anmeldekennung("10023 2") == "10023-2"
    assert anmeldekennung(" Name@Firma.DE ") == "name@firma.de"
    assert len(anmeldekennung("x" * 200)) == 80
    assert anmeldekennung(None) == ""
    assert sucher_nummer(10023, 2) == "10023-2"
    assert ist_kontonummer("10023-2") and ist_kontonummer("1001")
    assert not ist_kontonummer("10023 2") and not ist_kontonummer("ci-superadmin")


def test_login_schluessel_varianten_gleich():
    ip = "203.0.113.9"
    varianten = {RL.login_schluessel(ip, v) for v in ("10023 2", "10023/2", " 10023-02 ")}
    assert varianten == {f"{ip}|10023-2"}
    # E-Mail-Schluessel unveraendert (test_audit_20260913_markt test_26)
    assert RL.login_schluessel(ip, " A@B.de ") == f"{ip}|a@b.de"
    assert RL.login_schluessel(ip, "") == ip


def test_ist_kontonummer_dublette():
    assert ist_kontonummer_dublette(DuplicateKeyError(
        "E11000 duplicate key error collection: x.users index: kontonummer_eindeutig "
        'dup key: { kontonummer: "10023" }'))
    assert ist_kontonummer_dublette(DuplicateKeyError(
        "E11000", 11000, {"keyPattern": {"kontonummer": 1}}))
    # andere Dubletten bleiben 409 (u_62: email_1; zustaende test_11: users.email)
    assert not ist_kontonummer_dublette(DuplicateKeyError(
        "E11000 duplicate key error index: email_1"))
    assert not ist_kontonummer_dublette(DuplicateKeyError(
        'E11000 duplicate key error index: email_1 dup key: { email: "kontonummer@x.de" }'))
    assert not ist_kontonummer_dublette(DuplicateKeyError(
        "E11000 kontonummer", 11000, {"keyPattern": {"email": 1}}))
    assert not ist_kontonummer_dublette(ValueError("kontonummer_eindeutig"))


# ============================================================ kontenanlage
def test_kontenanlage_und_nummernreihe(wegwerf, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    w = wegwerf
    db = w.db

    async def firma(i):
        return await KA.firma_mit_chef_anlegen(
            db, {"id": f"d{i}", "user_id": f"u{i}", "company_name": f"F{i}", "email": ""},
            _konto(f"u{i}", role="dealer"))

    async def lauf():
        await db.dealers.create_index("kunden_nr", unique=True, sparse=True,
                                      name="kunden_nr_unique")
        await db.users.create_index("email", unique=True)
        assert await indizes.konto_indizes(db) == {"users": True, "driver_accounts": True}
        z = {}
        # 20 parallele Firmen: verschiedene Nummern, Chef = kunden_nr
        z["firmen"] = await asyncio.gather(*[firma(i) for i in range(20)])
        z["chefs"] = {u["id"]: u async for u in db.users.find({"role": "dealer"})}
        z["dealers"] = {d["id"]: d async for d in db.dealers.find({})}
        d0 = z["firmen"][0]
        # Sucher -1/-2, geloeschter Zusatz wird nie neu vergeben (-3)
        s1 = await KA.sucher_anlegen(db, d0["dealer_id"], _konto("s1"))
        s2 = await KA.sucher_anlegen(db, d0["dealer_id"], _konto("s2"))
        await db.users.delete_one({"id": "s2"})
        s3 = await KA.sucher_anlegen(db, d0["dealer_id"], _konto("s3"))
        # Zaehler zurueckgesetzt (Restore) -> ueber dem hoechsten Zusatz (-4)
        await db.dealers.update_one({"id": d0["dealer_id"]}, {"$set": {"sucher_seq": 0}})
        s4 = await KA.sucher_anlegen(db, d0["dealer_id"], _konto("s4"))
        z["sucher"] = [s1, s2, s3, s4]
        z["s1_doc"] = await db.users.find_one({"id": "s1"})
        # parallele Sucher derselben Firma
        d1 = z["firmen"][1]
        z["parallel"] = await asyncio.gather(*[
            KA.sucher_anlegen(db, d1["dealer_id"], _konto(f"p{i}")) for i in range(6)])
        # Firma ohne kunden_nr bekommt beim Sucher eine
        await db.dealers.insert_one({"id": "alt", "user_id": "altchef", "company_name": "Alt"})
        z["alt_sucher"] = await KA.sucher_anlegen(db, "alt", _konto("salt"))
        z["alt"] = await db.dealers.find_one({"id": "alt"})
        # 404 / 409 (Loeschung laeuft)
        codes = []
        with pytest.raises(HTTPException) as e:
            await KA.sucher_anlegen(db, "gibtsnicht", _konto("sx1"))
        codes.append(e.value.status_code)
        await db.dealers.update_one({"id": z["firmen"][2]["dealer_id"]},
                                    {"$set": {"loeschung": {"status": "laeuft"}}})
        with pytest.raises(HTTPException) as e:
            await KA.sucher_anlegen(db, z["firmen"][2]["dealer_id"], _konto("sx2"))
        codes.append(e.value.status_code)
        z["codes"] = codes
        # Kaeufer und Fahrer in derselben Reihe
        z["kaeufer"] = await KA.kaeufer_anlegen(db, _konto("k1"))
        z["fahrer"] = await KA.fahrer_anlegen(db, _konto("f1", display_name="Fahrer 1"))
        z["k_doc"] = await db.users.find_one({"id": "k1"})
        z["f_doc"] = await db.driver_accounts.find_one({"id": "f1"})
        # andere Dublette (E-Mail): kein neuer Versuch, genau eine Nummer verbraucht
        vor = (await db.counters.find_one({"_id": "kunden_nr"}))["seq"]
        with pytest.raises(DuplicateKeyError):
            await KA.kaeufer_anlegen(db, _konto("k2", email="k1@konto.test"))
        z["seq_diff_email"] = (await db.counters.find_one({"_id": "kunden_nr"}))["seq"] - vor
        # Kontonummer-Dublette -> neuer Versuch mit der naechsten Nummer
        seq = (await db.counters.find_one({"_id": "kunden_nr"}))["seq"]
        await db.users.insert_one(_konto("blocker", role="b2b_buyer",
                                         kontonummer=str(1000 + seq + 1)))
        z["blocker_nr"] = 1000 + seq + 1
        z["nach_blocker"] = await KA.kaeufer_anlegen(db, _konto("k3"))
        # Selbstheilung ueber drei Sammlungen
        heil = []
        for coll, doc in ((db.driver_accounts, _konto("fx", kontonummer="50000",
                                                      kontonummer_basis=50000)),
                          (db.users, _konto("ux", role="b2b_buyer", kontonummer="60000",
                                            kontonummer_basis=60000)),
                          (db.dealers, {"id": "dx", "user_id": "dxu", "kunden_nr": 70000})):
            await coll.insert_one(doc)
            await db.counters.update_one({"_id": "kunden_nr"}, {"$set": {"seq": 1}})
            heil.append(int((await KA.kaeufer_anlegen(db, _konto(f"h{len(heil)}")))["kontonummer"]))
        await db.counters.delete_one({"_id": "kunden_nr"})
        heil.append(int((await KA.fahrer_anlegen(db, _konto("h_fahrer")))["kontonummer"]))
        z["heil"] = heil
        # deps.naechste_kunden_nr delegiert
        monkeypatch.setattr(deps, "db", db)
        z["deps_nr"] = await deps.naechste_kunden_nr()
        # Teil-Index: viele Konten ohne Nummer; konten_ohne_nummer zaehlt nur
        await db.users.insert_many([
            _konto("o1", role="dealer"), _konto("o2", role="sucher"),
            _konto("o3", role="admin", is_super_admin=True),
            _konto("o4", role="dealer", loeschung={"status": "laeuft"})])
        await db.driver_accounts.insert_one(_konto("fo1"))
        z["ohne"] = await KA.konten_ohne_nummer(db)
        z["o1_danach"] = await db.users.find_one({"id": "o1"})
        return z

    z = w.run(lauf())
    nummern = [f["kunden_nr"] for f in z["firmen"]]
    assert len(set(nummern)) == 20 and all(n > 1000 for n in nummern), nummern
    for f in z["firmen"]:
        chef = z["chefs"][f["user_id"]]
        assert f["kontonummer"] == str(f["kunden_nr"]) == chef["kontonummer"]
        assert chef["kontonummer_basis"] == f["kunden_nr"]
        assert z["dealers"][f["dealer_id"]]["kunden_nr"] == f["kunden_nr"]
    nr0 = z["firmen"][0]["kunden_nr"]
    assert [s["kontonummer"] for s in z["sucher"]] == [f"{nr0}-1", f"{nr0}-2", f"{nr0}-3",
                                                      f"{nr0}-4"]
    assert z["s1_doc"]["kontonummer_basis"] == nr0 and z["s1_doc"]["role"] == "sucher"
    assert z["s1_doc"]["current_session_id"] is None
    nr1 = z["firmen"][1]["kunden_nr"]
    assert sorted(s["kontonummer"] for s in z["parallel"]) == sorted(
        f"{nr1}-{i}" for i in range(1, 7))
    assert isinstance(z["alt"]["kunden_nr"], int)
    assert z["alt_sucher"]["kontonummer"] == f"{z['alt']['kunden_nr']}-1"
    assert z["codes"] == [404, 409]
    k_nr, f_nr = int(z["kaeufer"]["kontonummer"]), int(z["fahrer"]["kontonummer"])
    assert k_nr > max(nummern + [z["alt"]["kunden_nr"]]) and f_nr > k_nr
    assert z["k_doc"]["role"] == "b2b_buyer" and z["k_doc"]["dealer_id"] is None
    assert z["k_doc"]["kontonummer_basis"] == k_nr
    assert z["f_doc"]["kontonummer_basis"] == f_nr
    assert z["f_doc"]["driver_code"] == z["fahrer"]["driver_code"]
    assert z["fahrer"]["driver_code"].startswith("FD-")
    assert z["seq_diff_email"] == 1
    assert int(z["nach_blocker"]["kontonummer"]) == z["blocker_nr"] + 1
    assert z["heil"][:3] == [50001, 60001, 70001], z["heil"]
    assert z["heil"][3] > 70001
    assert z["deps_nr"] > z["heil"][3]
    assert z["ohne"] == {"users": 2, "driver_accounts": 1}
    assert "kontonummer" not in z["o1_danach"], "konten_ohne_nummer darf nichts vergeben"


# ================================================================= Indizes
def test_konto_indizes_parallel_und_dubletten(wegwerf, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    w = wegwerf
    db = w.db
    partial = {"kontonummer": {"$type": "string"}}

    async def lauf():
        z = {}
        await db.users.insert_one({"id": "vorher"})   # Sammlungen existieren
        await db.driver_accounts.insert_one({"id": "vorher"})
        z["leer"] = await asyncio.gather(indizes.konto_indizes(db), indizes.konto_indizes(db),
                                         return_exceptions=True)
        # alter Index gleichen Namens mit anderen Optionen -> parallel ersetzen
        await db.driver_accounts.drop_index("kontonummer_eindeutig")
        await db.driver_accounts.create_index("kontonummer", name="kontonummer_eindeutig",
                                              unique=True, sparse=True)
        await db.users.drop_index("kontonummer_eindeutig")
        await db.users.create_index("kontonummer", name="kontonummer_1_alt")
        z["ersetzen"] = await asyncio.gather(indizes.konto_indizes(db),
                                             indizes.konto_indizes(db),
                                             return_exceptions=True)
        z["info_u"] = await db.users.index_information()
        z["info_d"] = await db.driver_accounts.index_information()
        # Teil-Index laesst viele Konten ohne Nummer zu
        await db.users.insert_many([{"id": f"ohne{i}"} for i in range(5)])
        # Dublette verhindert den Index -> Alarm (ausserhalb der Produktion)
        await db.users.drop_index("kontonummer_eindeutig")
        await db.users.insert_many([{"id": "d1", "kontonummer": "77777"},
                                    {"id": "d2", "kontonummer": "77777"}])
        z["mit_dublette"] = await indizes.konto_indizes(db)
        z["alarm"] = await db.betriebsalarme.find_one(
            {"typ": "unique_index_fehlt", "ref": "users.kontonummer_eindeutig", "offen": True})
        monkeypatch.setenv("APP_ENV", "production")
        try:
            await indizes.konto_indizes(db)
            z["abbruch"] = None
        except SystemExit as exc:
            z["abbruch"] = exc.code
        monkeypatch.delenv("APP_ENV", raising=False)
        await db.users.delete_one({"id": "d2"})
        z["bereinigt"] = await indizes.konto_indizes(db)
        z["alarm_danach"] = await db.betriebsalarme.count_documents(
            {"typ": "unique_index_fehlt", "ref": "users.kontonummer_eindeutig", "offen": True})
        return z

    z = w.run(lauf())
    for erg in z["leer"] + z["ersetzen"]:
        assert erg == {"users": True, "driver_accounts": True}, erg
    for info in (z["info_u"], z["info_d"]):
        idx = info["kontonummer_eindeutig"]
        assert idx.get("unique") is True and idx["partialFilterExpression"] == partial, idx
        assert not idx.get("sparse")
        assert info["kontonummer_basis"].get("sparse") is True
    assert "kontonummer_1_alt" not in z["info_u"], "alter Index mit gleichem Schluessel bleibt"
    assert z["mit_dublette"]["users"] is False and z["mit_dublette"]["driver_accounts"] is True
    assert z["alarm"] and "77777" in z["alarm"]["details"]["beispiele"]
    assert z["abbruch"] == 78
    assert z["bereinigt"]["users"] is True and z["alarm_danach"] == 0


# =========================================================== Konto-Limiter
def _fenster_abwarten(sekunden: int, puffer: float = 8.0) -> None:
    rest = sekunden - (time.time() % sekunden)
    if rest < puffer:
        time.sleep(rest + 0.2)


@pytest.fixture
def limiter(wegwerf, monkeypatch):
    monkeypatch.setattr(deps, "db", wegwerf.db)
    monkeypatch.setattr(RL, "_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(RL, "_EXEMPT_LOOPBACK", False)
    monkeypatch.setattr(RL, "_LOGIN_KONTO_LIMIT", 30)
    monkeypatch.setattr(RL, "_KONTO_ALARM_SCHWELLE", 30)
    monkeypatch.setattr(RL.login_konto_limiter, "name", f"login-konto-t{wegwerf.s}")
    _fenster_abwarten(RL.login_konto_limiter.window_seconds)
    return wegwerf


def test_konto_limiter_sperre_bekannte_ip_reset(limiter):
    w = limiter
    fremd, andere, bekannt = "198.51.100.23", "198.51.100.99", "192.0.2.77"
    konto = {"id": "kx", "login_ips_bekannt": [RL.ip_merkwert(bekannt)]}

    async def lauf():
        z = {}
        for _ in range(29):
            await RL.konto_fehlversuch("10023", fremd)
            await RL.konto_fehlversuch("99999", fremd)       # unbekannte Nummer zaehlt auch
        z["vor"] = (await RL.konto_gesperrt("10023", andere, konto),
                    await RL.konto_gesperrt("99999", andere, None))
        z["stand"] = [await RL.login_konto_limiter.stand("10023") for _ in range(3)]
        await RL.konto_fehlversuch("10023 ", fremd)          # Variante: selber Zaehler
        await RL.konto_fehlversuch("99999", fremd)
        z["nach"] = (await RL.konto_gesperrt("10023", andere, konto),
                     await RL.konto_gesperrt("99999", andere, None),
                     await RL.konto_gesperrt(" 10023 ", fremd, None))
        z["bekannt"] = await RL.konto_gesperrt("10023", bekannt, konto)
        z["alarme"] = sorted([a["ref"] async for a in w.db.betriebsalarme.find(
            {"typ": "login_konto_angegriffen", "offen": True})])
        await RL.login_konto_limiter.reset("10023")
        z["reset"] = (await RL.konto_gesperrt("10023", andere, konto),
                      await RL.konto_gesperrt("99999", andere, None))
        return z

    z = w.run(lauf())
    assert z["vor"] == (False, False)
    assert z["stand"] == [29, 29, 29], "stand() darf nicht zaehlen"
    assert z["nach"] == (True, True, True), "Sperre gleich fuer bekannte und unbekannte Kennung"
    assert z["bekannt"] is False, "von einer bekannten IP bleibt das Konto frei"
    assert z["alarme"] == ["10023", "99999"]
    assert z["reset"] == (False, True)
    assert "15 Minuten" in RL.konto_gesperrt_text()


def test_konto_limiter_limit_null_nur_alarm_und_loopback(limiter, monkeypatch):
    w = limiter
    monkeypatch.setattr(RL, "_LOGIN_KONTO_LIMIT", 0)

    async def lauf():
        for _ in range(31):
            await RL.konto_fehlversuch("10024", "198.51.100.5")
        gesperrt = await RL.konto_gesperrt("10024", "198.51.100.6", None)
        alarm = await w.db.betriebsalarme.find_one({"typ": "login_konto_angegriffen",
                                                   "ref": "10024"})
        return gesperrt, alarm

    gesperrt, alarm = w.run(lauf())
    assert gesperrt is False, "LOGIN_KONTO_LIMIT=0 sperrt nie"
    assert alarm and alarm["anzahl"] == 1 and alarm["details"]["sperre_aktiv"] is False

    monkeypatch.setattr(RL, "_LOGIN_KONTO_LIMIT", 30)
    monkeypatch.setattr(RL, "_EXEMPT_LOOPBACK", True)

    async def loopback():
        for _ in range(31):
            await RL.konto_fehlversuch("10025", "127.0.0.1")
        return (await RL.login_konto_limiter.stand("10025"),
                await RL.konto_gesperrt("10025", "127.0.0.1", None))
    assert w.run(loopback()) == (0, False)


def test_bekannte_ip_merken_hoechstens_fuenf(limiter):
    w = limiter

    async def lauf():
        await w.db.driver_accounts.insert_one({"id": "fk"})
        for i in range(7):
            await RL.bekannte_ip_merken(w.db, "driver_accounts", "fk", f"192.0.2.{i}")
        await RL.bekannte_ip_merken(w.db, "driver_accounts", "fk", "192.0.2.6")
        await RL.bekannte_ip_merken(w.db, "driver_accounts", "fk", "unknown")
        return (await w.db.driver_accounts.find_one({"id": "fk"}))["login_ips_bekannt"]

    werte = w.run(lauf())
    assert werte == [RL.ip_merkwert(f"192.0.2.{i}") for i in range(2, 7)]
    assert all(len(x) == 16 and "192" not in x for x in werte), "keine Klartext-IP"


# ============================================================ Verdrahtung
def _funktion(datei: str, name: str) -> str:
    text = (BACKEND / datei).read_text(encoding="utf-8")
    for knoten in ast.walk(ast.parse(text)):
        if isinstance(knoten, (ast.AsyncFunctionDef, ast.FunctionDef)) and knoten.name == name:
            return ast.get_source_segment(text, knoten)
    raise AssertionError(f"{datei}: {name} fehlt")


def test_login_routen_verdrahtet():
    for datei, name, limiter_name in (("routes/auth.py", "login", "login_limiter"),
                                      ("routes/marketplace.py", "buyer_login", "login_limiter"),
                                      ("routes/drivers.py", "driver_login",
                                       "driver_login_limiter")):
        q = _funktion(datei, name)
        assert "body.kontonummer or body.email" in q, name
        assert "konto_gesperrt(" in q and "konto_fehlversuch(" in q, name
        pruef = q.index("verify_password_async(")
        assert q.index("konto_gesperrt(") < pruef < q.index("konto_fehlversuch("), name
        # Nachbesserung: Erfolg leert den Konto-Zaehler NICHT (Spraying-Schutz)
        assert "login_konto_limiter.reset(" not in q, name
        assert "login_konto_limiter.check(" not in q, name
        assert "nummer_bedingung(nr)" in (q if name != "login" else
                                          _funktion(datei, "_konto_fuer_login")), name
        # runde26 test_05 / audit_markt test_25_26 bleiben erfuellt
        assert "login_schluessel(" in q and f"{limiter_name}.check(schluessel)" in q, name
        assert f"{limiter_name}.reset(schluessel)" in q and "login_ip_limiter.check(ip)" in q
    assert "login_limiter.check(ip)" not in _funktion("routes/marketplace.py", "buyer_login")
    assert "bekannte_ip_merken(" in _funktion("routes/auth.py", "_sitzung_ausstellen")
    for datei, name in (("routes/marketplace.py", "buyer_login"),
                        ("routes/drivers.py", "driver_login")):
        assert "bekannte_ip_merken(" in _funktion(datei, name), name
    suche = _funktion("routes/auth.py", "_konto_fuer_login")
    assert '"is_super_admin": True' in suche
    assert '"kontonummer": nummer_bedingung(nr)' in suche
    assert "nummer_bedingung(" in _funktion("kontenanlage.py", "_hoechster_zusatz")
    assert "normalisieren(username)" in _funktion("server.py", "seed_super_admin")
    ensure = _funktion("server.py", "ensure_indexes")
    assert ensure.index("_kunden_nr_unique_index()") < ensure.index("konto_indizes(db)")
    assert "konten_ohne_nummer" in ensure
    assert "nachziehen" not in _funktion("kontenanlage.py", "konten_ohne_nummer")


# ================================================ Nummernsuche nutzt Teil-Index
def _scan_stufen(plan) -> list:
    stufen = []

    def gehe(o):
        if isinstance(o, dict):
            if o.get("stage") in ("COLLSCAN", "IXSCAN"):
                stufen.append((o["stage"], o.get("indexName")))
            for v in o.values():
                gehe(v)
        elif isinstance(o, list):
            for v in o:
                gehe(v)
    gehe(plan)
    return stufen


def test_nummernsuche_nutzt_teil_index(wegwerf):
    """Nachbesserung: {kontonummer: '10023'} allein ist fuer den Planer keine
    Teilmenge von partialFilterExpression {$type: 'string'} (COLLSCAN ueber
    die ganze Sammlung bei jeder Anmeldung). nummer_bedingung fuehrt den
    Typfilter mit -> IXSCAN auf kontonummer_eindeutig."""
    db = wegwerf.db
    rollen = {"$in": ["dealer", "sucher", "b2b_buyer"]}

    async def plan(coll, filt):
        erg = await coll.find(filt).explain()
        return _scan_stufen(erg["queryPlanner"]["winningPlan"])

    async def lauf():
        await indizes.konto_indizes(db)
        await db.users.insert_many(
            [{"id": f"u{i}", "kontonummer": str(10000 + i), "role": "dealer"} for i in range(40)]
            + [{"id": f"s{i}", "kontonummer": f"10023-{i + 1}", "role": "sucher"}
               for i in range(5)]
            + [{"id": f"o{i}", "role": "dealer"} for i in range(40)])
        await db.driver_accounts.insert_many(
            [{"id": f"f{i}", "kontonummer": str(20000 + i)} for i in range(40)]
            + [{"id": f"fo{i}"} for i in range(40)])
        plaene = {
            "auth": await plan(db.users, {"kontonummer": nummer_bedingung("10023"),
                                          "role": rollen}),
            "buyer": await plan(db.users, {"kontonummer": nummer_bedingung("10023"),
                                           "role": "b2b_buyer"}),
            "driver": await plan(db.driver_accounts,
                                 {"kontonummer": nummer_bedingung("20001")}),
            "zusatz": await plan(db.users,
                                 {"kontonummer": nummer_bedingung({"$regex": "^10023-"})}),
        }
        treffer = (await db.users.find_one({"kontonummer": nummer_bedingung("10005"),
                                            "role": rollen}),
                   await KA._hoechster_zusatz(db, 10023))
        return plaene, treffer

    plaene, (treffer, zusatz) = wegwerf.run(lauf())
    for fall, stufen in plaene.items():
        assert stufen == [("IXSCAN", "kontonummer_eindeutig")], (fall, stufen)
    assert treffer["id"] == "u5"
    assert zusatz == 5


# ============================== Route: Konto-Sperre wirklich 429 (buyer_login)
def _anfrage(ip):
    from starlette.requests import Request
    return Request({"type": "http", "method": "POST", "path": "/api/buyer/login",
                    "headers": [], "client": (ip, 40000), "query_string": b""})


def test_buyer_login_konto_sperre_429_bekannte_ip_frei(limiter, monkeypatch):
    """Abnahme Schritt 1 an der echten Route: 30 Fehlversuche von fremden IPs
    (Spraying), danach 429 mit identischem Text fuer vorhandene und unbekannte
    Nummer, ohne Passwortpruefung; von der gemerkten IP weiter 401/200. Der
    Erfolg dort leert den Konto-Zaehler NICHT (Nachbesserung)."""
    w = limiter
    monkeypatch.setattr(MARKT, "db", w.db)
    pw, pw_hash = "KontoLimit13!xY", "hash-kb1-kein-dummy"
    geprueft = []

    async def pruefen(passwort, h):
        # schneller Ersatz fuer bcrypt: haelt den Lauf sicher im Fenster und
        # zaehlt, ob eine gesperrte Anmeldung noch ein Passwort prueft
        geprueft.append(h)
        return passwort == pw and h == pw_hash

    monkeypatch.setattr(MARKT, "verify_password_async", pruefen)
    nr, unbekannt, bekannt, fremd = "10077", "10078", "192.0.2.10", "198.51.100.200"

    async def anmelden(nummer, passwort, ip):
        try:
            erg = await MARKT.buyer_login(
                MARKT.BuyerLoginIn(kontonummer=nummer, password=passwort), _anfrage(ip))
            return 200, erg["user"]["kontonummer"]
        except HTTPException as e:
            return e.status_code, e.detail

    async def lauf():
        z = {}
        await w.db.users.insert_one({"id": "kb1", "role": "b2b_buyer", "kontonummer": nr,
                                     "kontonummer_basis": 10077, "dealer_id": None,
                                     "active": True, "password_hash": pw_hash,
                                     "email": "kb1@konto.test", "created_at": _jetzt()})
        z["erst"] = await anmelden(nr, pw, bekannt)              # merkt die IP
        z["gemerkt"] = (await w.db.users.find_one({"id": "kb1"}))["login_ips_bekannt"]
        z["fehl"] = []
        for i in range(30):                                       # je Versuch neue IP
            ip = f"198.51.100.{i + 1}"
            z["fehl"].append((await anmelden(nr, "falsch-falsch", ip))[0])
            z["fehl"].append((await anmelden(unbekannt, "falsch-falsch", ip))[0])
        vor = len(geprueft)
        z["gesperrt"] = [await anmelden(nr, "falsch-falsch", fremd),
                         await anmelden(unbekannt, "falsch-falsch", fremd),
                         await anmelden("10077 ", pw, "198.51.100.201")]
        z["passwort_geprueft"] = len(geprueft) - vor
        z["bekannt"] = [await anmelden(nr, "falsch-falsch", bekannt),
                        await anmelden(nr, pw, bekannt)]
        z["danach_fremd"] = await anmelden(nr, pw, fremd)
        z["stand"] = await RL.login_konto_limiter.stand(nr)
        return z

    z = w.run(lauf())
    sperre = (429, RL.konto_gesperrt_text())
    assert z["erst"] == (200, nr)
    assert z["gemerkt"] == [RL.ip_merkwert(bekannt)]
    assert z["fehl"] == [401] * 60
    assert z["gesperrt"] == [sperre, sperre, sperre], z["gesperrt"]
    assert z["passwort_geprueft"] == 0, "gesperrte Anmeldung darf kein Passwort pruefen"
    assert z["bekannt"] == [(401, "E-Mail oder Passwort falsch"), (200, nr)]
    assert z["danach_fremd"] == sperre, "Erfolg von bekannter IP hat die Sperre aufgehoben"
    assert z["stand"] == 31
