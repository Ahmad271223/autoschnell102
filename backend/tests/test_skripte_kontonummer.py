# -*- coding: utf-8 -*-
"""Kontonummer (13.09.2026), Schritt 6 — Betriebsskripte und Lasttests.

  - dubletten_pruefen: doppelte kontonummer je Sammlung UND kreuzweise ueber
    users/driver_accounts; doppelte E-Mail ist kein Befund mehr
  - verbindung_pruefen: kennt den Teil-Index kontonummer_eindeutig
  - anmeldesperre_aufheben: loescht genau die Zaehler, die reset() loescht
    (Fenster f-1..f+1), nichts bei Sonderzeichen-Nachbarn (wie zustaende
    test_47), Probelauf aendert nichts
  - Lasttests: keine alten Wege mehr (grep ueber backend/scripts und deploy),
    Aufraeumen genau ueber die gesammelten IDs, konten.json mit Kontonummer

In-process, synchrones pymongo gegen die Wegwerf-DB autoschnell_skripte_<hex>.
Kein Import von server.py, deps oder routes (keine Event-Loop-Falle).
"""
import importlib.util
import inspect
import re
import sys
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = BACKEND / "scripts"
WURZEL = BACKEND.parent
for _p in (str(BACKEND), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import os  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _skript(name):
    spec = importlib.util.spec_from_file_location(f"{name}_s6", SCRIPTS / f"{name}.py")
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture
def db():
    from pymongo import MongoClient
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_skripte_{uuid.uuid4().hex[:10]}"
    try:
        yield client[name]
    finally:
        client.drop_database(name)
        client.close()


# ------------------------------------------------------------ dubletten_pruefen
def test_01_dubletten_kontonummer_je_sammlung_und_kreuzweise(db, capsys):
    DP = _skript("dubletten_pruefen")
    assert ("users", "email") not in DP.PRUEFUNGEN
    assert ("users", "kontonummer") in DP.PRUEFUNGEN
    assert ("driver_accounts", "kontonummer") in DP.PRUEFUNGEN

    # sauber: gleiche Kontakt-E-Mail mehrfach ist erlaubt, Konten ohne Nummer auch
    db.users.insert_many([
        {"id": "u1", "kontonummer": "10023", "email": "info@firma.test", "role": "dealer"},
        {"id": "u2", "kontonummer": "10023-2", "email": "info@firma.test", "role": "sucher"},
        {"id": "sa", "username": "chef-admin", "role": "admin"},
        {"id": "u3", "role": "dealer"}])
    db.driver_accounts.insert_many([
        {"id": "d1", "kontonummer": "10031", "email": "info@firma.test", "driver_code": "FD-A"},
        {"id": "d2", "email": "info@firma.test", "driver_code": "FD-B"}])
    assert DP.main(db) == 0
    assert "Keine Dubletten." in capsys.readouterr().out

    # doppelt in users, doppelt in driver_accounts, kreuzweise
    db.users.insert_many([
        {"id": "u4", "kontonummer": "10023", "role": "dealer"},
        {"id": "u5", "kontonummer": "10031", "role": "b2b_buyer"}])
    db.driver_accounts.insert_one({"id": "d3", "kontonummer": "10050", "driver_code": "FD-C"})
    db.driver_accounts.insert_one({"id": "d4", "kontonummer": "10050", "driver_code": "FD-D"})
    assert DP.kreuz_dubletten(db) == 1
    kreuz = capsys.readouterr().out
    assert "'10031'" in kreuz and "u5" in kreuz and "d1" in kreuz

    assert DP.main(db) == 1
    out = capsys.readouterr().out
    assert "users.kontonummer = '10023': 2x" in out
    assert "driver_accounts.kontonummer = '10050': 2x" in out
    assert "users+driver_accounts.kontonummer = '10031'" in out
    assert "info@firma.test" not in out, "E-Mail ist kein Dublettenkriterium mehr"
    assert "3 doppelte Werte" in out
    assert "doppelte_fahrzeuge(db)" in inspect.getsource(DP.main)


# ----------------------------------------------------------- verbindung_pruefen
def test_02_verbindung_pruefen_kennt_kontonummer_index(db):
    VP = _skript("verbindung_pruefen")
    assert VP.KONTO_INDEX == "kontonummer_eindeutig"
    indizes_quelle = (BACKEND / "indizes.py").read_text(encoding="utf-8")
    assert f'"{VP.KONTO_INDEX}"' in indizes_quelle, "Name muss zu indizes.konto_indizes passen"
    quelle = (SCRIPTS / "verbindung_pruefen.py").read_text(encoding="utf-8")
    assert "users.email" not in quelle

    assert VP.konto_index_status(db) == {"users": False, "driver_accounts": False}
    db.users.create_index("kontonummer", name="kontonummer_eindeutig", unique=True,
                          partialFilterExpression={"kontonummer": {"$type": "string"}})
    db.driver_accounts.create_index("kontonummer", name="kontonummer_eindeutig")  # nicht unique
    assert VP.konto_index_status(db) == {"users": True, "driver_accounts": False}


# ------------------------------------------------------- anmeldesperre_aufheben
def _fenster(jetzt, sekunden):
    return int(jetzt // sekunden)


def test_03_anmeldesperre_loescht_nur_exakte_schluessel(db, capsys):
    AS = _skript("anmeldesperre_aufheben")
    import rate_limiter as RL
    jetzt, sek = 1_800_000_123.0, 900
    f = _fenster(jetzt, sek)

    # gleiche Schluesselbildung wie der Limiter und sein reset()
    assert AS.LIMITER_NAME == RL.login_konto_limiter.name
    assert "(fenster - 1, fenster, fenster + 1)" in inspect.getsource(RL.SlidingWindowRateLimiter.reset)
    assert AS.schluessel("10023 2", sek, jetzt) == [
        f"login-konto:10023-2:{f - 1}", f"login-konto:10023-2:{f}", f"login-konto:10023-2:{f + 1}"]
    assert AS.schluessel("  ", sek, jetzt) == []

    sonder = "a|.|b@buero.test"
    loeschen = [f"login-konto:{sonder}:{x}" for x in (f - 1, f, f + 1)]
    bleiben = [
        f"login-konto:{sonder}:{f - 2}",            # aelteres Fenster (raeumt die TTL)
        f"login-konto:axxb@buero.test:{f}",          # '.' und '|' sind kein Muster
        f"login-konto:{sonder}x:{f}",                # kein Praefix
        f"login-konto:+info@buero.test:{f}",
        f"login:{sonder}:{f}",                       # anderer Limiter
        f"login-ip:198.51.100.7:{f}",
        f"login-konto:10023-2:{f}",                  # andere Kennung
    ]
    db.rate_limits.insert_many([{"_id": i, "n": 31} for i in loeschen + bleiben])

    # Probelauf: zeigt, aendert nichts
    assert AS.main([sonder, "--fenster", str(sek)], db=db, jetzt=jetzt) == 0
    out = capsys.readouterr().out
    assert "PROBELAUF" in out and "GESPERRT" in out
    assert db.rate_limits.count_documents({}) == len(loeschen) + len(bleiben)
    assert db.activity_logs.count_documents({}) == 0

    # Ausfuehren (Gross-/Kleinschreibung wie beim Login egal)
    assert AS.main(["A|.|B@Buero.test", "--fenster", str(sek), "--ausfuehren"],
                   db=db, jetzt=jetzt) == 0
    assert "AUFGEHOBEN: 3 Zaehler" in capsys.readouterr().out
    assert sorted(d["_id"] for d in db.rate_limits.find({})) == sorted(bleiben)
    log = db.activity_logs.find_one({"action": "auth.anmeldesperre.aufgehoben"})
    assert log and log["meta"]["zaehler"] == 3 and log["meta"]["username"] == sonder

    # Kontonummer in anderer Schreibweise trifft nur '10023-2', nicht '10023-23'
    db.rate_limits.insert_one({"_id": f"login-konto:10023-23:{f}", "n": 5})
    assert AS.main(["10023/02", "--fenster", str(sek), "--ausfuehren"], db=db, jetzt=jetzt) == 0
    assert db.rate_limits.find_one({"_id": f"login-konto:10023-2:{f}"}) is None
    assert db.rate_limits.find_one({"_id": f"login-konto:10023-23:{f}"}) is not None
    log = db.activity_logs.find_one({"meta.kontonummer": "10023-2"})
    assert log and log["meta"]["zaehler"] == 1

    # nichts gesperrt / leere Kennung
    assert AS.main(["99999", "--fenster", str(sek), "--ausfuehren"], db=db, jetzt=jetzt) == 0
    assert "keine Sperre" in capsys.readouterr().out
    assert AS.main([" ", "--fenster", str(sek)], db=db, jetzt=jetzt) == 2


# ------------------------------------------------------------------ Lasttests
_ALTE_WEGE = [
    (re.compile(r"/(auth|buyer|driver)/register"), "Selbstregistrierung"),
    (re.compile(r"(?<!SUPER_)ADMIN_(EMAIL|PASSWORD)"), "ADMIN_EMAIL/ADMIN_PASSWORD"),
    (re.compile(r"SELF_SIGNUP"), "SELF_SIGNUP"),
    (re.compile(r"(?:post[_a-z]*\(\s*\w+,\s*f?\"[^\"]*|\"POST\",\s*\")/dealer/sucher", re.I),
     "POST /dealer/sucher"),
    (re.compile(r"\{\s*\"email\":\s*\w*mail\w*,\s*\"password\""), "Login per E-Mail"),
]


def test_04_skripte_ohne_alte_anmeldewege():
    dateien = [p for p in SCRIPTS.glob("*.py")] + [p for p in (WURZEL / "deploy").glob("*.sh")]
    assert any(p.name == "lasttest_matrix.py" for p in dateien)
    funde = []
    for pfad in dateien:
        text = pfad.read_text(encoding="utf-8")
        for muster, was in _ALTE_WEGE:
            for m in muster.finditer(text):
                zeile = text.count("\n", 0, m.start()) + 1
                funde.append(f"{pfad.name}:{zeile} {was}")
    assert not funde, funde
    # Reset-Links gibt es nicht mehr — Sitzungen widerrufen fasst sie nicht an
    widerrufen = (SCRIPTS / "sitzungen_widerrufen.py").read_text(encoding="utf-8")
    assert "db.password_resets" not in widerrufen and "Reset-Links geloescht" not in widerrufen


def test_05_lasttest_konten_loescht_genau_die_ids(db):
    LK = _skript("lasttest_konten")
    from kontonummer import normalisieren
    ids = LK.neue_ids()
    sa = LK.wegwerf_super_admin(db, "abc123", ids)
    assert normalisieren(sa["username"]) is None, "Benutzername darf nie wie eine Nummer aussehen"
    konto = db.users.find_one({"id": sa["id"]})
    assert konto["is_super_admin"] is True and konto["role"] == "admin" and "email" not in konto

    ids["users"] += ["u_last"]
    ids["dealers"] += ["d_last"]
    ids["driver_accounts"] += ["f_last"]
    db.users.insert_many([{"id": "u_last", "email": "x_s1@e2etest-mail.de"},
                          {"id": "u_fremd", "email": "x_s1@e2etest-mail.de"}])
    db.dealers.insert_many([{"id": "d_last"}, {"id": "d_fremd"}])
    db.driver_accounts.insert_many([{"id": "f_last"}, {"id": "f_fremd"}])
    db.dealer_drivers.insert_many([{"dealer_id": "d_last", "driver_account_id": "f_last"},
                                   {"dealer_id": "d_fremd", "driver_account_id": "f_fremd"}])
    db.subscriptions.insert_many([{"id": "s1", "dealer_id": "d_last"},
                                  {"id": "s2", "subject_user_id": "u_last", "dealer_id": "d_x"},
                                  {"id": "s3", "dealer_id": "d_fremd", "subject_user_id": "u_fremd"}])

    n = LK.konten_loeschen(db, ids)
    assert n["users"] == 2 and n["dealers"] == 1 and n["driver_accounts"] == 1
    assert [u["id"] for u in db.users.find({})] == ["u_fremd"], "gleiche E-Mail, fremdes Konto bleibt"
    assert [d["id"] for d in db.dealers.find({})] == ["d_fremd"]
    assert [d["id"] for d in db.driver_accounts.find({})] == ["f_fremd"]
    assert [d["driver_account_id"] for d in db.dealer_drivers.find({})] == ["f_fremd"]
    assert [s["id"] for s in db.subscriptions.find({})] == ["s3"]
    # leere Sammelstelle loescht nichts
    assert sum(LK.konten_loeschen(db, LK.neue_ids()).values()) == 0
    assert db.users.count_documents({}) == 1

    for name in ("lasttest.py", "lasttest_matrix.py", "lasttest_stoss.py", "lasttest_sucher.py"):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "import lasttest_konten as LK" in text, name
        assert '{"email": {"$regex"' not in text, f"{name}: Aufraeumen per E-Mail-Regex"


def test_06_lasttest_sucher_liest_kontonummer_und_alte_dateien():
    LS = _skript("lasttest_sucher")
    assert LS.kennung({"kontonummer": "10023-2", "email": "a@b.de"}) == "10023-2"
    assert LS.kennung({"email": "alt@firma.de"}) == "alt@firma.de"
    assert LS.kennung({}) == ""
    quelle = inspect.getsource(LS.anmelden)
    assert '"kontonummer": kontonummer' in quelle and '"email"' not in quelle
