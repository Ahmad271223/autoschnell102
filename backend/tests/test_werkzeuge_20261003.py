# -*- coding: utf-8 -*-
"""Werkzeuge (03.10.2026, Wunsch Ahmad): AutoPointer-Vergleich nur fuer Kunde 10002.

"Alle anderen bekommen das nicht, die sollen das gar nicht sehen":
  * Chef und Sucher der Firma 10002 sehen den Eintrag und laden die Datei.
  * Jede andere Firma: leere Liste, Download 404 (wie nicht vorhanden).
  * Betreiber/Fahrer/Kaeufer: leere Liste, Download 403 (keine Firmenroute).

Teil 1 laeuft ohne Server (Freigabe-Logik, Pruefung, Skript), Teil 2 braucht ein
laufendes Backend (wie die anderen HTTP-Tests). Fuer Teil 2 bekommt eine eigene
Testfirma die Kundennummer 10002; haelt eine andere Firma sie, wird deren Nummer
fuer die Dauer des Tests getauscht und danach zurueckgesetzt.
"""
import os
import secrets
import sys
import uuid
from pathlib import Path

import pytest
import requests

import konten  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import werkzeuge as wz  # noqa: E402

API = konten.API
WID = wz.AUTOPOINTER


# ------------------------------------------------------------ Teil 1: ohne Server
def test_01_standard_nur_10002(monkeypatch):
    monkeypatch.delenv("AUTOPOINTER_VERGLEICH_KUNDEN", raising=False)
    assert wz.freigegebene_kunden(WID) == frozenset({"10002"})
    assert wz.ist_freigegeben(WID, 10002)
    assert wz.ist_freigegeben(WID, "10002")
    assert wz.ist_freigegeben(WID, " 010002 ")
    for andere in (10001, 10003, "10002-1", None, "", True):
        assert not wz.ist_freigegeben(WID, andere), andere
    assert wz.freigegebene_werkzeuge(10002) == [WID]
    assert wz.freigegebene_werkzeuge(10023) == []


def test_02_liste_per_umgebung(monkeypatch):
    monkeypatch.setenv("AUTOPOINTER_VERGLEICH_KUNDEN", "10002, 10017;10023")
    assert wz.freigegebene_kunden(WID) == frozenset({"10002", "10017", "10023"})
    assert wz.ist_freigegeben(WID, 10017)
    monkeypatch.setenv("AUTOPOINTER_VERGLEICH_KUNDEN", "")
    assert wz.freigegebene_kunden(WID) == frozenset()
    assert not wz.ist_freigegeben(WID, 10002)
    assert not wz.ist_freigegeben("gibts-nicht", 10002)


def test_03_nur_echte_exe():
    with pytest.raises(ValueError):
        wz.exe_pruefen(b"")
    with pytest.raises(ValueError):
        wz.exe_pruefen(b"PK" + b"\0" * 5000)        # ZIP, kein Programm
    with pytest.raises(ValueError):
        wz.exe_pruefen(b"MZ")                       # zu klein
    wz.exe_pruefen(b"MZ" + b"\0" * 5000)


class _Speicher:
    def __init__(self):
        self.dateien = {}

    def save(self, key, data, max_mb=None):
        assert max_mb == wz.MAX_MB
        self.dateien[key] = data
        return key


class _Sammlung:
    def __init__(self):
        self.docs = {}

    def replace_one(self, filt, doc, upsert=False):
        assert upsert
        self.docs[filt["id"]] = doc


class _Db:
    def __init__(self):
        self.werkzeuge = _Sammlung()


def test_04_hochladen_schreibt_datei_und_eintrag():
    db, sp = _Db(), _Speicher()
    daten = b"MZ" + secrets.token_bytes(4096)
    meta = wz.hochladen(db, WID, daten, "1.0.0", storage=sp)
    assert sp.dateien[meta["schluessel"]] == daten
    assert meta["schluessel"] == "werkzeuge/autopointer-vergleich/AutoSchnell-Vergleich.exe"
    assert db.werkzeuge.docs[WID]["groesse"] == len(daten)
    assert db.werkzeuge.docs[WID]["version"] == "1.0.0"
    assert len(meta["sha256"]) == 64
    with pytest.raises(ValueError):
        wz.hochladen(db, "gibts-nicht", daten, storage=sp)


def test_05_skript(tmp_path):
    from scripts.werkzeug_hochladen import main
    db, sp = _Db(), _Speicher()
    exe = tmp_path / "AutoSchnell-Vergleich.exe"
    exe.write_bytes(b"MZ" + b"\1" * 3000)
    assert main([str(exe), "--version", "1.2.3"], db=db, storage=sp) == 0
    assert db.werkzeuge.docs[WID]["version"] == "1.2.3"
    keine = tmp_path / "bild.png"
    keine.write_bytes(b"\x89PNG" + b"\0" * 3000)
    assert main([str(keine)], db=db, storage=sp) == 2
    assert main([str(tmp_path / "fehlt.exe")], db=db, storage=sp) == 2


def test_06_speicher_grenze_nur_fuer_betreiberdateien():
    from storage_service import MAX_FILE_MB, LocalDiskStorage, StorageError
    sp = LocalDiskStorage(root=Path(os.environ.get("TMP", "/tmp")) / f"wz_{uuid.uuid4().hex[:8]}")
    gross = b"MZ" + b"\0" * (MAX_FILE_MB * 1024 * 1024 + 10)
    with pytest.raises(StorageError):
        sp.save("werkzeuge/test/a.exe", gross)              # Nutzer-Uploads: weiter 25 MB
    sp.save("werkzeuge/test/a.exe", gross, max_mb=wz.MAX_MB)  # Betreiberdatei: erlaubt
    assert sp.groesse("werkzeuge/test/a.exe") == len(gross)
    sp.delete("werkzeuge/test/a.exe")


# ------------------------------------------------------------ Teil 2: ueber HTTP
def _neue_firma():
    s = uuid.uuid4().hex[:8]
    pw = "Wz-Test-" + secrets.token_hex(6) + "!"
    r = konten.registrieren({"email": f"wz_chef_{s}@wztest-mail.de", "password": pw,
                             "company_name": f"WZ Testfirma {s}", "contact_person": "Test"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    me = requests.get(f"{API}/auth/me", headers=konten._kopf(token), timeout=30).json()["user"]
    return {"token": token, "dealer_id": me["dealer_id"], "user_id": me["id"]}


@pytest.fixture(scope="module")
def welt():
    db = konten._db()
    firma = _neue_firma()
    andere = _neue_firma()
    getauscht = None
    halter = db.dealers.find_one({"kunden_nr": {"$in": [10002, "10002"]}}, {"_id": 0, "id": 1, "kunden_nr": 1})
    if halter and halter["id"] != firma["dealer_id"]:
        ersatz = 9_000_000 + secrets.randbelow(900_000)
        db.dealers.update_one({"id": halter["id"]}, {"$set": {"kunden_nr": ersatz}})
        getauscht = (halter["id"], halter["kunden_nr"])
    db.dealers.update_one({"id": firma["dealer_id"]}, {"$set": {"kunden_nr": 10002}})
    s = konten.sucher_als_chef_anlegen(firma["token"], json={"password": "Wz-Sucher-" + secrets.token_hex(6) + "!"})
    assert s.status_code == 200, s.text
    sucher_token = konten.token_direkt(s.json()["sucher_id"])
    meta_vorher = db.werkzeuge.find_one({"id": WID}, {"_id": 0})
    try:
        yield {"db": db, "chef": konten._kopf(firma["token"]), "sucher": konten._kopf(sucher_token),
               "andere": konten._kopf(andere["token"]), "firma": firma, "sucher_id": s.json()["sucher_id"],
               "andere_firma": andere}
    finally:
        # Testfirma ganz wegraeumen: ihr Sucher traegt kontonummer_basis 10002 und hoebe sonst die
        # Nummernreihe der Test-Datenbank ueber 10000 (test_firmen_verwaltung erwartet 1001-9999).
        sucher_id = s.json()["sucher_id"]
        db.users.delete_many({"id": {"$in": [firma["user_id"], sucher_id]}})
        db.dealers.delete_one({"id": firma["dealer_id"]})
        for sammlung in ("subscriptions", "werkzeug_codes", "werkzeug_verbindungen", "werkzeug_vergleiche"):
            db[sammlung].delete_many({"dealer_id": firma["dealer_id"]})
        if getauscht:
            db.dealers.update_one({"id": getauscht[0]}, {"$set": {"kunden_nr": getauscht[1]}})
        if meta_vorher:
            db.werkzeuge.replace_one({"id": WID}, meta_vorher, upsert=True)
        db.subscriptions.delete_many({"id": {"$regex": "^wz-test-"}})


def _liste(kopf):
    r = requests.get(f"{API}/werkzeuge", headers=kopf, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["werkzeuge"]


def _download(kopf):
    return requests.get(f"{API}/werkzeuge/{WID}/download", headers=kopf, timeout=60)


def test_10_ohne_datei_sichtbar_aber_noch_nicht_ladbar(welt):
    welt["db"].werkzeuge.delete_one({"id": WID})
    liste = _liste(welt["chef"])
    assert [w["id"] for w in liste] == [WID]
    assert liste[0]["vorhanden"] is False
    r = _download(welt["chef"])
    assert r.status_code == 404
    assert "noch nicht hochgeladen" in r.json()["detail"]


def test_11_chef_und_sucher_von_10002_laden(welt):
    from storage_service import storage
    daten = b"MZ" + secrets.token_bytes(64 * 1024)
    meta = wz.hochladen(welt["db"], WID, daten, "test-" + uuid.uuid4().hex[:6], storage=storage)
    for wer in ("chef", "sucher"):
        liste = _liste(welt[wer])
        assert liste and liste[0]["vorhanden"] is True and liste[0]["version"] == meta["version"], wer
        assert liste[0]["groesse"] == len(daten)
        r = _download(welt[wer])
        assert r.status_code == 200, (wer, r.text[:200])
        assert r.content == daten
        assert 'filename="AutoSchnell-Vergleich.exe"' in r.headers["content-disposition"]
        assert r.headers["cache-control"] == "no-store"
    log = welt["db"].activity_logs.find_one({"dealer_id": welt["firma"]["dealer_id"], "action": "werkzeug_download"})
    assert log and log["ref"] == WID


def test_12_andere_firma_sieht_nichts(welt):
    assert _liste(welt["andere"]) == []
    r = _download(welt["andere"])
    assert r.status_code == 404
    assert r.json()["detail"] == "Nicht gefunden"
    r = requests.get(f"{API}/werkzeuge/gibts-nicht/download", headers=welt["chef"], timeout=30)
    assert r.status_code == 404


def test_13_betreiber_und_ohne_anmeldung(welt):
    sa = konten.super_kopf()
    assert _liste(sa) == []
    assert _download(sa).status_code == 403
    assert requests.get(f"{API}/werkzeuge", timeout=30).status_code == 401
    assert requests.get(f"{API}/werkzeuge/{WID}/download", timeout=30).status_code == 401


# ------------------------------------------------------------ Teil 3: Lizenz (Wunsch Ahmad 03.10.2026 nachmittags)
# "einmal anmelden, mit AutoSchnell verbinden, Lizenz pruefen, 6-stelliger Code, Vergleichsregeln
#  aus AutoSchnell, pro Sucher 1 Zugang / ein PC" — und Ahmad + Chef sehen, wer was verglichen hat.
AHMADS_REGELN = {"first_registration": {"mode": "older_exact", "years": 1},
                 "mileage": {"mode": "plus", "value": 20000}, "power": {"mode": "min_ps", "value": 5},
                 "fuel": {"mode": "exact"}, "gearbox": {"mode": "exact"}, "damage": {"mode": "no_accident"},
                 "country": {"mode": "exact", "codes": ["DE"]}, "navi": {"mode": "wenn_vorhanden"},
                 "sort": "price_asc"}
POLO_MOBILE = ("https://suchen.mobile.de/fahrzeuge/search.html?isSearchRequest=true&ref=quickSearch&s=Car&vc=Car"
               "&pageNumber=1&ms=25200%3B27%3B%3B%3B&fr=2004%3A&ml=%3A148000&pw=51%3A&ft=PETROL&tr=MANUAL_GEAR"
               "&dam=0&cn=DE&sb=p&od=up")
POLO = {"marke": "VW", "modell": "Polo", "marke_modell_text": "VW Polo", "titel": "VW Polo 1.4 - Klima - 75 PS",
        "ez_monat": 10, "ez_jahr": 2005, "kilometer": 128000, "kw": 55, "ps": 75, "kraftstoff": "Benzin",
        "getriebe": "Schaltgetriebe", "preis": 2599, "quelle": "Kleinanzeigen", "inserat_id": "3529833344"}


def test_20_code_und_vehicle_helfer():
    for _ in range(50):
        c = wz.code_erzeugen()
        assert len(c) == 6 and c.isdigit()
    assert wz.code_normalisieren(" 123-456 ") == "123456"
    assert wz.streuwert("x") != "x" and len(wz.streuwert("x")) == 64
    v = wz.fahrzeug_zu_vehicle(POLO)
    assert v["first_registration"] == "10/2005" and v["mileage"] == 128000 and v["make_label"] == "VW"
    links, hinweise = wz.vergleichs_links(v, AHMADS_REGELN)
    assert [x["portal"] for x in links] == ["mobile.de", "AutoScout24"]
    assert links[0]["url"] == POLO_MOBILE
    assert "fregfrom=2004" in links[1]["url"] and "kmto=148000" in links[1]["url"]
    assert "powerto" not in links[1]["url"]
    assert hinweise == []


def test_21_unbekanntes_modell_keine_suche_nur_nach_marke():
    v = wz.fahrzeug_zu_vehicle({**POLO, "marke": "Bentley", "modell": "Gibtsnicht"})
    links, hinweise = wz.vergleichs_links(v, AHMADS_REGELN)
    assert links == []
    assert any("kein mobile.de-Vergleich" in h for h in hinweise)
    assert any("kein AutoScout24-Vergleich" in h for h in hinweise)


def _abo(welt, an: bool):
    db = welt["db"]
    db.subscriptions.delete_many({"id": f"wz-test-{welt['sucher_id']}"})
    if an:
        from datetime import datetime, timezone
        db.subscriptions.insert_one({
            "id": f"wz-test-{welt['sucher_id']}", "subject_user_id": welt["sucher_id"],
            "dealer_id": welt["firma"]["dealer_id"], "plan": "monthly", "status": "active",
            "expires_at": "2099-01-01T00:00:00+00:00", "created_at": datetime.now(timezone.utc).isoformat()})


def _verbinden(welt, pc):
    r = requests.post(f"{API}/werkzeuge/{WID}/code", headers=welt["sucher"], timeout=30)
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    assert len(code) == 6 and r.json()["minuten"] == 10
    r = requests.post(f"{API}/werkzeuge/{WID}/verbinden",
                      json={"code": code, "pc_name": pc, "pc_kennung": pc + "-id"}, timeout=30)
    assert r.status_code == 200, r.text
    return code, {wz.TOKEN_KOPF: r.json()["schluessel"]}, r.json()


def _status(kopf):
    return requests.get(f"{API}/werkzeuge/{WID}/status", headers=kopf, timeout=30)


def _vergleich(kopf, fahrzeug=None):
    return requests.post(f"{API}/werkzeuge/{WID}/vergleich", headers=kopf,
                         json={"fahrzeug": fahrzeug or POLO}, timeout=30)


def test_30_code_nur_mit_abo(welt):
    _abo(welt, False)
    r = requests.post(f"{API}/werkzeuge/{WID}/code", headers=welt["sucher"], timeout=30)
    assert r.status_code == 402


def test_31_verbinden_status_vergleich(welt):
    _abo(welt, True)
    code, prog, antwort = _verbinden(welt, "PC-A")
    assert "-" in antwort["konto"]                      # Sucher-Kontonummer 10002-1
    # der Code ist verbraucht
    assert requests.post(f"{API}/werkzeuge/{WID}/verbinden", json={"code": code}, timeout=30).status_code == 404
    s = _status(prog)
    assert s.status_code == 200 and s.json()["pc_name"] == "PC-A", s.text
    assert _liste(welt["sucher"])[0]["verbindung"]["pc_name"] == "PC-A"
    r = _vergleich(prog)
    assert r.status_code == 200, r.text
    assert [x["portal"] for x in r.json()["links"]] == ["mobile.de", "AutoScout24"]
    eintrag = welt["db"].werkzeug_vergleiche.find_one({"user_id": welt["sucher_id"]}, sort=[("erstellt_am", -1)])
    assert eintrag and eintrag["fahrzeug"]["inserat_id"] == "3529833344" and eintrag["pc_name"] == "PC-A"


def test_32_firmenregeln_ergeben_ahmads_link(welt):
    _abo(welt, True)
    db = welt["db"]
    vorher = db.dealers.find_one({"id": welt["firma"]["dealer_id"]}, {"_id": 0, "comparison_rules": 1}) or {}
    db.dealers.update_one({"id": welt["firma"]["dealer_id"]}, {"$set": {"comparison_rules": AHMADS_REGELN}})
    try:
        _, prog, _ = _verbinden(welt, "PC-A")
        r = _vergleich(prog)
        assert r.status_code == 200, r.text
        assert r.json()["links"][0]["url"] == POLO_MOBILE
    finally:
        if vorher.get("comparison_rules"):
            db.dealers.update_one({"id": welt["firma"]["dealer_id"]},
                                  {"$set": {"comparison_rules": vorher["comparison_rules"]}})
        else:
            db.dealers.update_one({"id": welt["firma"]["dealer_id"]}, {"$unset": {"comparison_rules": ""}})


def test_33_zweiter_pc_ersetzt_den_ersten(welt):
    _abo(welt, True)
    _, pc_a, _ = _verbinden(welt, "PC-A")
    _, pc_b, _ = _verbinden(welt, "PC-B")
    assert _status(pc_a).status_code == 401
    assert _status(pc_b).status_code == 200
    assert welt["db"].werkzeug_verbindungen.count_documents({"user_id": welt["sucher_id"], "werkzeug": WID}) == 1


def test_34_ohne_abo_gesperrt(welt):
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-A")
    _abo(welt, False)
    try:
        assert _status(prog).status_code == 402
        r = _vergleich(prog)
        assert r.status_code == 402 and "Abo" in r.json()["detail"]
    finally:
        _abo(welt, True)


def test_35_ohne_schluessel_und_andere_firma(welt):
    assert requests.get(f"{API}/werkzeuge/{WID}/status", timeout=30).status_code == 401
    assert _status({wz.TOKEN_KOPF: "falsch"}).status_code == 401
    assert _vergleich({wz.TOKEN_KOPF: "falsch"}).status_code == 401
    assert requests.post(f"{API}/werkzeuge/{WID}/verbinden", json={"code": "000000"}, timeout=30).status_code == 404
    # andere Firma: kein Code, keine Firmenuebersicht (gar nicht sichtbar)
    assert requests.post(f"{API}/werkzeuge/{WID}/code", headers=welt["andere"], timeout=30).status_code in (402, 404)
    assert requests.get(f"{API}/werkzeuge/{WID}/firma", headers=welt["andere"], timeout=30).status_code == 404


def test_36_eingaben_werden_geprueft(welt):
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-A")
    ohne_ez = {k: v for k, v in POLO.items() if k != "ez_jahr"}
    assert _vergleich(prog, ohne_ez).status_code == 422
    r = _vergleich(prog, {**POLO, "marke": "Bentley", "modell": "Gibtsnicht"})
    assert r.status_code == 200 and r.json()["links"] == []


def test_37_chef_und_admin_sehen_wer_was_verglichen_hat(welt):
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-Chefsicht")
    assert _vergleich(prog).status_code == 200
    r = requests.get(f"{API}/werkzeuge/{WID}/firma", headers=welt["chef"], timeout=30)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["vergleiche"][0]["fahrzeug"]["modell"] == "Polo" and d["vergleiche"][0]["konto"]
    assert any(v["pc_name"] == "PC-Chefsicht" for v in d["verbindungen"])
    assert all("token_hash" not in v and "pc_kennung" not in v for v in d["verbindungen"])
    # Sucher darf die Firmenuebersicht nicht sehen
    assert requests.get(f"{API}/werkzeuge/{WID}/firma", headers=welt["sucher"], timeout=30).status_code == 403
    a = requests.get(f"{API}/admin/werkzeug-vergleiche", params={"dealer_id": welt["firma"]["dealer_id"]},
                     headers=konten.super_kopf(), timeout=30)
    assert a.status_code == 200, a.text
    assert a.json()["vergleiche"][0]["kunden_nr"] == 10002 and a.json()["name"] == "AutoPointer-Vergleich"
    assert requests.get(f"{API}/admin/werkzeug-vergleiche", headers=welt["chef"], timeout=30).status_code == 403


def test_38_trennen_durch_chef_und_selbst(welt):
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-A")
    r = requests.delete(f"{API}/werkzeuge/{WID}/verbindungen/{welt['sucher_id']}", headers=welt["chef"], timeout=30)
    assert r.status_code == 200
    assert _status(prog).status_code == 401
    _, prog, _ = _verbinden(welt, "PC-A")
    r = requests.delete(f"{API}/werkzeuge/{WID}/verbindung", headers=welt["sucher"], timeout=30)
    assert r.json()["getrennt"] is True
    assert _status(prog).status_code == 401
    # eine andere Firma kann fremde Sucher nicht trennen
    r = requests.delete(f"{API}/werkzeuge/{WID}/verbindungen/{welt['sucher_id']}", headers=welt["andere"], timeout=30)
    assert r.status_code == 404


def test_39_programm_meldet_sich_selbst_ab(welt):
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-A")
    _abo(welt, False)                       # auch ohne Abo darf der PC freigegeben werden
    try:
        r = requests.post(f"{API}/werkzeuge/{WID}/abmelden", headers=prog, timeout=30)
        assert r.status_code == 200 and r.json()["getrennt"] is True
        assert _status(prog).status_code == 401
        r = requests.post(f"{API}/werkzeuge/{WID}/abmelden", headers=prog, timeout=30)
        assert r.status_code == 200 and r.json()["getrennt"] is False
    finally:
        _abo(welt, True)


def test_40_doppelt_angekommene_anfrage_gibt_denselben_schluessel(welt):
    """Beim Test am 03.10.2026 kam POST /verbinden zweimal an: die erste Anfrage loeste den
    Code ein, die zweite bekam 404 — und genau die sah das Programm."""
    _abo(welt, True)
    r = requests.post(f"{API}/werkzeuge/{WID}/code", headers=welt["sucher"], timeout=30)
    code = r.json()["code"]
    body = {"code": code, "pc_name": "PC-A", "pc_kennung": "kennung-a"}
    eins = requests.post(f"{API}/werkzeuge/{WID}/verbinden", json=body, timeout=30)
    zwei = requests.post(f"{API}/werkzeuge/{WID}/verbinden", json=body, timeout=30)
    assert eins.status_code == 200 and zwei.status_code == 200, zwei.text
    assert eins.json()["schluessel"] == zwei.json()["schluessel"]
    assert _status({wz.TOKEN_KOPF: eins.json()["schluessel"]}).status_code == 200
    # ein anderer PC kann den verbrauchten Code nicht nachnutzen
    fremd = requests.post(f"{API}/werkzeuge/{WID}/verbinden",
                          json={**body, "pc_kennung": "kennung-b"}, timeout=30)
    assert fremd.status_code == 404
    assert welt["db"].werkzeug_verbindungen.count_documents({"user_id": welt["sucher_id"], "werkzeug": WID}) == 1


def test_41_schluessel_ableiten():
    a = wz.schluessel_ableiten("geheim", "code-1", "pc-a")
    assert a == wz.schluessel_ableiten("geheim", "code-1", "pc-a")
    assert a != wz.schluessel_ableiten("geheim", "code-1", "pc-b")
    assert a != wz.schluessel_ableiten("geheim", "code-2", "pc-a")
    assert a != wz.schluessel_ableiten("anders", "code-1", "pc-a")
    assert len(a) >= 40 and "=" not in a
