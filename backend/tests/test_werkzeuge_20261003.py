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
def test_01_standard_nur_10001_und_10002(monkeypatch):
    # 03.10.2026 abends (Wunsch Ahmad): "schalte das Programm auch frei jetzt fuer 10001"
    monkeypatch.delenv("AUTOPOINTER_VERGLEICH_KUNDEN", raising=False)
    monkeypatch.setenv("BROWSER_HELFER_KUNDEN", "")      # 04.10.2026: zweites Werkzeug, hier nur das Programm
    assert wz.freigegebene_kunden(WID) == frozenset({"10001", "10002", "10007"})   # 10007 seit 06.10.2026
    assert wz.ist_freigegeben(WID, 10002)
    assert wz.ist_freigegeben(WID, 10007)
    assert wz.ist_freigegeben(WID, "10002")
    assert wz.ist_freigegeben(WID, " 010002 ")
    assert wz.ist_freigegeben(WID, 10001)
    for andere in (10003, 1001, "10002-1", "10001-1", None, "", True):
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
        for sammlung in ("subscriptions", "werkzeug_codes", "werkzeug_verbindungen", "werkzeug_vergleiche",
                        "werkzeug_app_starts"):
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
    # 04.10.2026: der Browser-Helfer steht (freigegeben fuer 10001/10002) hinter dem Programm
    assert [w["id"] for w in liste][0] == WID and set(w["id"] for w in liste) <= {WID, wz.BROWSER_HELFER}
    assert liste[0]["vorhanden"] is False and liste[0]["art"] == "windows" and liste[0]["geraet"] == "PC"
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


def test_22_programm_navi_wie_eingestellt():
    # 03.10.2026 war im Programm der Navi-Filter immer aus. Wunsch Ahmad 04.10.2026 (abends): "immer an die
    # AutoSchnell-Regeln halten" — "Navi wenn vorhanden" filtert auch im Programm, "egal" nicht.
    v = wz.fahrzeug_zu_vehicle({**POLO, "titel": "VW Polo 1.4 Navi Klima"})
    import mobile_service as ms
    links, _ = wz.vergleichs_links(v, AHMADS_REGELN)
    assert links[0]["portal"] == "mobile.de" and links[0]["url"] == ms.build_search_url(v, AHMADS_REGELN)
    assert "NAVIGATION_SYSTEM" in links[0]["url"]
    links, _ = wz.vergleichs_links(v, {**AHMADS_REGELN, "navi": {"mode": "ignore"}})
    assert links and all("NAVIGATION" not in x["url"] and "navi" not in x["url"].lower() for x in links)
    assert AHMADS_REGELN["navi"] == {"mode": "wenn_vorhanden"}              # Firmenregeln nicht veraendert


def test_23_weitere_vw_modell_aus_dem_titel():
    # Befund 03.10.2026: Kleinanzeigen-Inserat "VW Weitere VW", Titel "VW Beetle Cabrio 1.2 TSI" -> kein Vergleich
    beetle = {**POLO, "modell": "Weitere VW", "marke_modell_text": "VW Weitere VW", "titel": "VW Beetle Cabrio 1.2 TSI",
              "ez_monat": 6, "ez_jahr": 2017, "kilometer": 41000, "kw": 77, "ps": 105}
    v = wz.fahrzeug_zu_vehicle(beetle)
    assert v["model_label"] == "Beetle"
    links, hinweise = wz.vergleichs_links(v, AHMADS_REGELN)
    assert [x["portal"] for x in links] == ["mobile.de", "AutoScout24"], hinweise
    # Nichts Brauchbares im Titel: Platzhalter bleibt, Hinweis statt Suche nur nach "VW"
    v = wz.fahrzeug_zu_vehicle({**beetle, "titel": "Schoenes Cabrio zu verkaufen"})
    assert v["model_label"] == "Weitere VW"
    links, hinweise = wz.vergleichs_links(v, AHMADS_REGELN)
    assert links == [] and any("Weitere VW" in h for h in hinweise)


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
    assert a.json()["vergleiche"][0]["kunden_nr"] == 10002 and a.json()["name"] == "AutoSchnell Vergleich"
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


# ------------------------------------------------------------ Teil 4: Inserat-Link + Vorab-Abruf fuer den Kaufvertrag
# Wunsch Ahmad 03.10.2026: "wenn Kunde dieses Auto anklickt soll Vergleich kommen und wir scrapen im Hintergrund
# schonmal das Auto fuer den Vertrag ... wenn Hash-ID nicht sichtbar war: URL selber kopieren und einfuegen".
UUID = "ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d"


def test_42_inserat_url_je_portal():
    assert wz.inserat_url("mobile.de", "453270494") == "https://suchen.mobile.de/fahrzeuge/details.html?id=453270494"
    # live 03.10.2026: AutoPointer zeigt bei mobile.de auch 14-stellige IDs (Ford Grand Tourneo) — mobile.de oeffnet sie
    assert wz.inserat_url("mobile.de", "48761918484992") ==         "https://suchen.mobile.de/fahrzeuge/details.html?id=48761918484992"
    assert wz.inserat_url("Kleinanzeigen", "3529833344") == "https://www.kleinanzeigen.de/s-anzeige/3529833344"
    # AutoScout: NUR mit vollstaendiger Hash-ID (die Inserat-ID aus AutoPointer kennt AutoScout nicht)
    assert wz.inserat_url("AutoScout24", "474879577", UUID) == f"https://www.autoscout24.de/angebote/{UUID}"
    assert wz.inserat_url("AutoScout24", "474879577", UUID.upper()) == f"https://www.autoscout24.de/angebote/{UUID}"
    assert wz.inserat_url("AutoScout24", "474879577", "9bcc72cb-be50-4cc4-804d-0d...") is None
    assert wz.inserat_url("AutoScout24", "474879577", "") is None
    assert wz.inserat_url("mobile.de", "abc") is None
    assert wz.inserat_url("", "453270494") is None


def _kleinanzeigen_polo():
    nummer = "35" + str(secrets.randbelow(10 ** 8)).zfill(8)
    return {**POLO, "quelle": "Kleinanzeigen", "inserat_id": nummer}, f"https://www.kleinanzeigen.de/s-anzeige/{nummer}"


def test_43_klick_liest_das_inserat_vorab_aus(welt):
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-A")
    fz, url = _kleinanzeigen_polo()
    r = _vergleich(prog, fz)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["inserat_url"] == url
    assert d["vorab"]["status"] in ("laeuft", "fertig"), d["vorab"]
    db = welt["db"]
    assert db.link_jobs.find_one({"url": url, "user_ids": welt["sucher_id"]}) or d["vorab"]["status"] == "fertig"
    eintrag = db.werkzeug_vergleiche.find_one({"fahrzeug.inserat_id": fz["inserat_id"]})
    assert eintrag["fahrzeug"]["inserat_url"] == url and eintrag["vorab"] == d["vorab"]["status"]


def test_44_autoscout_ohne_vollstaendige_hash_id_sagt_es(welt):
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-A")
    ohne = {**POLO, "quelle": "AutoScout24", "inserat_id": "474879577", "hash_id": ""}
    d = _vergleich(prog, ohne).json()
    assert d["inserat_url"] is None and d["vorab"]["status"] == "kein_link"
    assert "kopieren" in d["vorab"]["hinweis"]
    assert d["links"], "die Vergleiche kommen trotzdem"
    mit = {**ohne, "hash_id": UUID}
    d = _vergleich(prog, mit).json()
    assert d["inserat_url"] == f"https://www.autoscout24.de/angebote/{UUID}"
    assert d["vorab"]["status"] != "kein_link"


def test_45_probelauf_liest_nichts_aus(welt):
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-A")
    fz, url = _kleinanzeigen_polo()
    r = requests.post(f"{API}/werkzeuge/{WID}/vergleich", headers=prog, json={"fahrzeug": fz, "probelauf": True},
                      timeout=30)
    assert r.json()["vorab"]["status"] == "probelauf"
    assert welt["db"].link_jobs.find_one({"url": url}) is None


def test_46_meine_autos_fuer_den_kaufvertrag(welt):
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-A")
    fz, url = _kleinanzeigen_polo()
    assert _vergleich(prog, fz).status_code == 200
    r = requests.get(f"{API}/werkzeuge/{WID}/meine", headers=welt["sucher"], timeout=30)
    assert r.status_code == 200, r.text
    liste = r.json()["vergleiche"]
    assert liste[0]["fahrzeug"]["inserat_url"] == url
    assert all(not x.get("probelauf") for x in liste)
    assert requests.get(f"{API}/werkzeuge/{WID}/meine", headers=welt["andere"], timeout=30).status_code == 404


def test_47_danach_steht_das_auto_in_der_app_sofort_bereit(welt):
    """Ganzer Weg: Klick im Programm -> Abruf im Hintergrund -> /app/vergleich?url=... ruft
    /mobile/compare auf und bekommt das Fahrzeug aus dem Speicher (kein neuer Abruf)."""
    import time
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-A")
    fz, url = _kleinanzeigen_polo()
    assert _vergleich(prog, fz).json()["vorab"]["status"] in ("laeuft", "fertig")
    db = welt["db"]
    # Der Sucher bleibt beim Auto: die Vorab-Wartezeit (15 s) ist um — hier vorgespult
    db.link_jobs.update_many({"url": url}, {"$set": {"fruehestens": None}})
    for _ in range(120):
        job = db.link_jobs.find_one({"url": url}, {"_id": 0, "status": 1})
        if job and job["status"] in ("completed", "failed"):
            break
        time.sleep(0.25)
    assert job and job["status"] == "completed", job
    r = requests.post(f"{API}/mobile/compare", json={"url": url}, headers=welt["sucher"], timeout=60)
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d.get("vehicle_id") and d.get("vehicle")
    assert d.get("cached") is True, "Fahrzeug kam aus dem Speicher, kein zweiter Abruf"


# Wunsch Ahmad 03.10.2026 abends: "irgendwie haengt meine App ... wenn neues Inserat geoeffnet wird, soll im
# Hintergrund die alte URL automatisch entfernt und durch die neue ersetzt werden".
def _vorab_job(db, url):
    return db.link_jobs.find_one({"url": url}, {"_id": 0})


def test_48_neues_auto_ersetzt_den_wartenden_vorab_abruf(welt):
    from datetime import datetime, timezone
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-A")
    db = welt["db"]
    fz_a, url_a = _kleinanzeigen_polo()
    assert _vergleich(prog, fz_a).json()["vorab"]["status"] == "laeuft"
    a = _vorab_job(db, url_a)
    assert a and a["status"] == "queued" and a.get("vorab") is True
    frueh = a["fruehestens"].replace(tzinfo=timezone.utc)
    assert frueh > datetime.now(timezone.utc), "wartet erst kurz, bevor Apify laeuft"
    # naechstes Auto angeklickt: der alte Abruf ist weg, der neue wartet
    fz_b, url_b = _kleinanzeigen_polo()
    assert _vergleich(prog, fz_b).json()["vorab"]["status"] == "laeuft"
    assert _vorab_job(db, url_a) is None
    b = _vorab_job(db, url_b)
    assert b and b["status"] == "queued" and b.get("vorab") is True
    verbindung = db.werkzeug_verbindungen.find_one({"user_id": welt["sucher_id"], "werkzeug": WID})
    assert verbindung["vorab_job_id"] == b["id"]
    # Auto ohne Inserat-Link (AutoScout ohne Hash-ID): auch dann faellt der alte Abruf weg
    ohne = {**POLO, "quelle": "AutoScout24", "inserat_id": "474879577", "hash_id": ""}
    assert _vergleich(prog, ohne).json()["vorab"]["status"] == "kein_link"
    assert _vorab_job(db, url_b) is None
    assert db.werkzeug_verbindungen.find_one({"id": verbindung["id"]})["vorab_job_id"] is None


def test_49_in_der_app_geoeffnet_startet_sofort_und_bleibt(welt):
    import time
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-A")
    db = welt["db"]
    fz_a, url_a = _kleinanzeigen_polo()
    assert _vergleich(prog, fz_a).json()["vorab"]["status"] == "laeuft"
    # Sucher oeffnet das Auto fuer den Kaufvertrag in der App: kein Warten mehr
    r = requests.post(f"{API}/listings/check", json={"url": url_a}, headers=welt["sucher"], timeout=30)
    assert r.status_code == 200, r.text
    a = _vorab_job(db, url_a)
    assert a and welt["sucher_id"] in (a.get("app_konten") or [])
    assert a.get("fruehestens") is None or a["status"] != "queued"
    # ein neues Auto im Programm zieht diesen Abruf NICHT zurueck — die App wartet darauf
    fz_b, _url_b = _kleinanzeigen_polo()
    assert _vergleich(prog, fz_b).status_code == 200
    for _ in range(120):
        a = _vorab_job(db, url_a)
        if a and a["status"] in ("completed", "failed"):
            break
        time.sleep(0.25)
    assert a and a["status"] == "completed", a


def test_50_kollege_wartet_auf_dasselbe_inserat(welt):
    """Zwei Konten, dasselbe Inserat: zieht das Programm des einen zurueck, bleibt der Abruf fuer den anderen."""
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-A")
    db = welt["db"]
    fz_a, url_a = _kleinanzeigen_polo()
    _vergleich(prog, fz_a)
    db.link_jobs.update_one({"url": url_a}, {"$addToSet": {"user_ids": "kollege-x"}})
    fz_b, _ = _kleinanzeigen_polo()
    _vergleich(prog, fz_b)
    a = _vorab_job(db, url_a)
    assert a and a["user_ids"] == ["kollege-x"], a
    db.link_jobs.delete_one({"url": url_a})


# Wunsch Ahmad 03.10.2026: Erkennung auf dem Server — das Programm (ab 1.4.0) schickt nur Rohtext (roh=True)
def _roh(text, titel, **zusatz):
    erstes, _, rest = text.partition(" ")
    return {**POLO, "marke": erstes or "?", "modell": rest, "marke_modell_text": text, "titel": titel, "roh": True,
            **zusatz}


_PROG: dict = {}


def _prog(welt):
    """Eine Verbindung fuer 51-53 — je Konto gibt es hoechstens 20 Codes in 10 Minuten."""
    if "kopf" not in _PROG:
        _abo(welt, True)
        _PROG["kopf"] = _verbinden(welt, "PC-A")[1]
    return _PROG["kopf"]


def test_51_server_erkennt_marke_und_modell(welt):
    prog = _prog(welt)
    r = _vergleich(prog, _roh("Hyundai ilO", "Hyundai ilO Classic", ez_monat=12, ez_jahr=2010, kilometer=193530,
                              kw=57, ps=78))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["fahrzeug"] == {"marke": "Hyundai", "modell": "i10", "erkannt": True}
    assert [x["portal"] for x in d["links"]] == ["mobile.de", "AutoScout24"]
    gespeichert = welt["db"].werkzeug_vergleiche.find_one({"user_id": welt["sucher_id"]}, sort=[("erstellt_am", -1)])
    assert (gespeichert["fahrzeug"]["marke"], gespeichert["fahrzeug"]["modell"]) == ("Hyundai", "i10")


def test_52_server_erkennt_aus_der_ueberschrift_und_meldet_unbekanntes(welt):
    prog = _prog(welt)
    d = _vergleich(prog, _roh("Andere", "Ford Mondeo Turnier 2.0 TDCi Diesel, ...")).json()
    assert d["fahrzeug"]["marke"] == "Ford" and d["fahrzeug"]["modell"] == "Mondeo" and d["links"]
    d = _vergleich(prog, _roh("Quatschmarke X1", "Quatschmarke X1 Sport")).json()
    assert d["fahrzeug"]["erkannt"] is False and d["links"] == []
    assert any("Marke" in h for h in d["hinweise"])


def test_53_aeltere_programme_wie_bisher(welt):
    """Programme bis 1.3.5 schicken schon erkannte Werte (ohne roh) — der Server nimmt sie wie bisher."""
    prog = _prog(welt)
    d = _vergleich(prog, POLO).json()
    assert d["fahrzeug"] == {"marke": "VW", "modell": "Polo", "erkannt": True}
    assert [x["portal"] for x in d["links"]] == ["mobile.de", "AutoScout24"]



# ------------------------------------------------------------ Pruefbericht 03.10.2026 (Nr. 4, 12, 16)
def _code_bremse_frei(welt):
    """Die Tests dieser Datei holen zusammen mehr als 20 Codes in 10 Minuten (Bremse je Konto) — vor den
    folgenden Tests den Zaehler des Test-Suchers leeren."""
    welt["db"].rate_limits.delete_many({"_id": {"$regex": f"^werkzeug_code_konto:konto:{welt['sucher_id']}:"}})


def test_54_modell_aus_der_beschreibung_mit_hinweis_und_ohne_speichern(welt):
    """Befund 04.10.2026: Mercedes, Feld "Andere", Modell nur in der Beschreibung ("meinen Mercedes C 300 e")."""
    prog = _prog(welt)
    text = "Verkaufe auf diesem Weg meinen Mercedes C 300 e, da ich ein Firmenfahrzeug erhalte"
    r = _vergleich(prog, _roh("Andere", "Mercedes-Benz Weitere Mercedes Be...", beschreibung=text,
                              ez_monat=6, ez_jahr=2022, kilometer=82200, kw=229, ps=311))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["fahrzeug"]["modell"] == "C 300" and d["links"]
    assert d["hinweise"][0].startswith("Modell aus der Beschreibung übernommen: C 300")
    gespeichert = welt["db"].werkzeug_vergleiche.find_one({"user_id": welt["sucher_id"]}, sort=[("erstellt_am", -1)])
    assert gespeichert["fahrzeug"]["modell"] == "C 300" and "beschreibung" not in gespeichert["fahrzeug"]


def test_55_plausibilitaet_ez_und_km():
    """Befund 04.10.2026 (echte Liste): unplausible EZ/km sagen — die Filter bleiben wie eingestellt."""
    from datetime import date
    heute = date(2026, 10, 4)

    def pruefen(**werte):
        f = {**POLO, **werte}
        v = wz.fahrzeug_zu_vehicle(f)
        h = wz.plausibel(f, heute)
        assert "first_registration" in v and "mileage" in v      # nie still weglassen (Wunsch Ahmad 04.10.)
        return h

    h = pruefen(ez_monat=4, ez_jahr=2026, kilometer=165000)             # Kia Rio
    assert len(h) == 1 and "passt nicht zu 165.000 km" in h[0]
    assert pruefen(ez_monat=10, ez_jahr=2026, kilometer=75000)          # Opel Crossland
    h = pruefen(ez_monat=2, ez_jahr=1998, kilometer=1960817)            # Audi 80
    assert len(h) == 1 and "1.960.817" in h[0]
    assert "Zukunft" in pruefen(ez_monat=11, ez_jahr=2026, kilometer=10)[0]
    for ok in (dict(ez_monat=2, ez_jahr=2026, kilometer=15720),           # Seat Ibiza: passt
               dict(ez_monat=None, ez_jahr=2026, kilometer=0),            # Neuwagen
               dict(ez_monat=10, ez_jahr=2005, kilometer=128000)):        # Polo
        assert pruefen(**ok) == [], ok


def test_56_unplausibles_wird_gemeldet_filter_wie_eingestellt(welt):
    """Wunsch Ahmad 04.10.: "EZ immer -1 und km immer +20.000 oder je nachdem, was im Konto eingestellt ist" —
    auch bei unplausiblen Daten nur melden, die Filter kommen unveraendert aus den Einstellungen."""
    prog = _prog(welt)
    db, dealer_id = welt["db"], welt["firma"]["dealer_id"]
    vorher = db.dealers.find_one({"id": dealer_id}, {"_id": 0, "comparison_rules": 1}) or {}
    kia = _roh("Kia Rio", "Kia Rio 1.2", ez_monat=4, ez_jahr=2026, kilometer=165000, kw=62, ps=84)
    try:
        for regeln, fr, ml in ((AHMADS_REGELN, "fr=2025%3A", "ml=%3A185000"),
                               ({**AHMADS_REGELN, "first_registration": {"mode": "older_exact", "years": 3},
                                 "mileage": {"mode": "plus", "value": 40000}}, "fr=2023%3A", "ml=%3A205000")):
            db.dealers.update_one({"id": dealer_id}, {"$set": {"comparison_rules": regeln}})
            r = _vergleich(prog, kia)
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["melden"] and "passt nicht zu 165.000 km" in d["melden"][0]
            mobile = next(x["url"] for x in d["links"] if x["portal"] == "mobile.de")
            assert fr in mobile and ml in mobile, mobile
    finally:
        if vorher.get("comparison_rules"):
            db.dealers.update_one({"id": dealer_id}, {"$set": {"comparison_rules": vorher["comparison_rules"]}})
        else:
            db.dealers.update_one({"id": dealer_id}, {"$unset": {"comparison_rules": ""}})


def test_57_tageslimit_des_programms(welt):
    """Entscheidung Ahmad 06.10.2026: 600 Vergleiche je Konto und Tag ueber das Programm (PROGRAMM_TAGESLIMIT_JE_KONTO,
    Server-Vorgabe); kurz vorher ein Hinweis, der Probelauf zaehlt nicht, danach 429 mit klarem Text."""
    import provider_fetch as pf
    prog, db = _prog(welt), welt["db"]
    schluessel = f"{pf.tagesschluessel()}:programm:{welt['sucher_id']}"
    limit = 600          # Vorgabe docker-compose.yml und ci.yml (PROGRAMM_TAGESLIMIT_JE_KONTO); lokal: launch.json
    try:
        db.provider_budget.update_one({"_id": schluessel}, {"$set": {"n": limit - 2}}, upsert=True)
        r = _vergleich(prog, _roh("VW Polo", "VW Polo 1.4"))
        assert r.status_code == 200, r.text
        assert any("Noch 1 Vergleich heute" in m for m in r.json()["melden"]), r.json()["melden"]
        # Probelauf zaehlt nicht
        r = requests.post(f"{API}/werkzeuge/{WID}/vergleich", headers=prog, timeout=30,
                          json={"fahrzeug": _roh("VW Polo", "VW Polo 1.4"), "probelauf": True})
        assert r.status_code == 200, r.text
        assert db.provider_budget.find_one({"_id": schluessel})["n"] == limit - 1
        r = _vergleich(prog, _roh("VW Polo", "VW Polo 1.4"))
        assert r.status_code == 200, r.text                                  # der letzte
        r = _vergleich(prog, _roh("VW Golf", "VW Golf 2.0"))
        assert r.status_code == 429 and "Tageslimit des Programms" in r.json()["detail"],             f"{r.status_code} {r.text[:200]} — laeuft der Test-Server mit PROGRAMM_TAGESLIMIT_JE_KONTO=600?"
        assert db.provider_budget.find_one({"_id": schluessel})["n"] == limit  # abgelehnt = nicht gezaehlt
        assert db.werkzeug_vergleiche.count_documents({"user_id": welt["sucher_id"], "fahrzeug.modell": "Golf"}) == 0
    finally:
        db.provider_budget.delete_one({"_id": schluessel})


def test_58_konto_deaktiviert_ist_403_der_schluessel_bleibt(welt):
    """Paket 2: 401 liess das Programm seinen Schluessel wegwerfen — nach dem Reaktivieren war ein neuer Code noetig."""
    prog, db = _prog(welt), welt["db"]
    db.users.update_one({"id": welt["sucher_id"]}, {"$set": {"active": False}})
    try:
        r = requests.get(f"{API}/werkzeuge/{WID}/status", headers=prog, timeout=30)
        assert r.status_code == 403 and "deaktiviert" in r.json()["detail"], r.text
    finally:
        db.users.update_one({"id": welt["sucher_id"]}, {"$set": {"active": True}})
    assert requests.get(f"{API}/werkzeuge/{WID}/status", headers=prog, timeout=30).status_code == 200


def test_59_inserat_im_browser_statt_apify_wenn_der_helfer_verbunden_ist(welt):
    """Wunsch Ahmad 07.10.2026: hat das Konto den Browser-Helfer, holt das Programm keinen Vorab-Abruf (Apify) mehr —
    es oeffnet das Inserat als Tab, der Helfer liest es. Ohne Helfer wie bisher."""
    prog, db = _prog(welt), welt["db"]
    _code_bremse_frei(welt)                    # 20 Codes je Konto und 10 min — die Tests davor haben viele geholt
    auto = _roh("VW Polo", "VW Polo 1.4", inserat_id="3529833377")
    url = "https://www.kleinanzeigen.de/s-anzeige/3529833377"
    db.link_jobs.delete_many({"url": url})
    r = requests.post(f"{API}/werkzeuge/browser-helfer/code", headers=welt["sucher"], timeout=30)
    assert r.status_code == 200, r.text
    r = requests.post(f"{API}/werkzeuge/browser-helfer/verbinden", timeout=30,
                      json={"code": r.json()["code"], "pc_name": "Edge", "pc_kennung": "edge-59"})
    assert r.status_code == 200, r.text
    try:
        r = _vergleich(prog, auto)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["inserat_im_browser"] is True and d["vorab"]["status"] == "browser" and d["inserat_url"] == url
        assert db.link_jobs.count_documents({"url": url}) == 0, "kein Apify-Vorab-Abruf"
        assert db.werkzeug_vergleiche.find_one({"user_id": welt["sucher_id"], "fahrzeug.inserat_id": "3529833377"})["vorab"] == "browser"
        # Probelauf: nie
        p = requests.post(f"{API}/werkzeuge/{WID}/vergleich", headers=prog, timeout=30,
                          json={"fahrzeug": auto, "probelauf": True})
        assert p.status_code == 200 and p.json()["inserat_im_browser"] is False
    finally:
        assert requests.delete(f"{API}/werkzeuge/browser-helfer/verbindung", headers=welt["sucher"], timeout=30).status_code == 200
    # ohne Helfer: Vorab-Abruf wie bisher
    r = _vergleich(prog, auto)
    assert r.status_code == 200, r.text
    assert r.json()["inserat_im_browser"] is False and r.json()["vorab"]["status"] in ("laeuft", "fertig", "in_app")
    db.link_jobs.delete_many({"url": url})


def test_60_anderer_pc_genau_benannt(welt):
    """Nr. 16: der alte PC erfaehrt, dass und wo das Konto neu verbunden wurde."""
    _code_bremse_frei(welt)
    _abo(welt, True)
    _, pc_a, _ = _verbinden(welt, "PC-Buero")
    _, pc_b, _ = _verbinden(welt, "PC-Halle")
    r = _status(pc_a)
    assert r.status_code == 401
    assert "anderen PC („PC-Halle“)" in r.json()["detail"] and "neuen Code" in r.json()["detail"]
    assert _status(pc_b).status_code == 200


def test_61_getrennt_durch_chef_oder_app_genau_benannt(welt):
    _code_bremse_frei(welt)
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-A")
    requests.delete(f"{API}/werkzeuge/{WID}/verbindungen/{welt['sucher_id']}", headers=welt["chef"], timeout=30)
    assert "Dein Chef hat diesen PC" in _status(prog).json()["detail"]
    _, prog, _ = _verbinden(welt, "PC-A")
    requests.delete(f"{API}/werkzeuge/{WID}/verbindung", headers=welt["sucher"], timeout=30)
    assert "in der AutoSchnell-App getrennt" in _status(prog).json()["detail"]
    # unbekannter Schluessel: der allgemeine Text wie bisher
    assert "nicht (mehr) verbunden" in _status({wz.TOKEN_KOPF: "gibt-es-nicht"}).json()["detail"]


def test_62_status_nennt_die_angebotene_version_und_merkt_die_eigene(welt):
    """Nr. 4: das Programm erfaehrt die angebotene Version; der Server merkt sich, welche Version laeuft."""
    _code_bremse_frei(welt)
    _abo(welt, True)
    db = welt["db"]
    db.werkzeuge.update_one({"id": WID}, {"$set": {"version": "9.9.9"}}, upsert=True)
    _, prog, _ = _verbinden(welt, "PC-A")
    r = requests.get(f"{API}/werkzeuge/{WID}/status", headers={**prog, "User-Agent": "AutoSchnell-Vergleich/1.5.0"},
                     timeout=30)
    assert r.status_code == 200, r.text
    assert r.json()["aktuelle_version"] == "9.9.9" and r.json()["programm_name"] == wz.WERKZEUGE[WID]["name"]
    v = db.werkzeug_verbindungen.find_one({"user_id": welt["sucher_id"], "werkzeug": WID}, {"_id": 0})
    assert v["programm_version"] == "1.5.0"
    # Chef sieht die Version seines Suchers
    r = requests.get(f"{API}/werkzeuge/{WID}/firma", headers=welt["chef"], timeout=30)
    assert any(x.get("programm_version") == "1.5.0" for x in r.json()["verbindungen"])


def test_63_app_start_rueckmeldung(welt):
    """Nr. 12: die App meldet den Start, das Programm fragt danach — nur fuer die eigene Firma."""
    _code_bremse_frei(welt)
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-A")
    start = uuid.uuid4().hex
    frage = lambda s: requests.get(f"{API}/werkzeuge/{WID}/app-start/{s}", headers=prog, timeout=30)  # noqa: E731
    assert frage(start).json() == {"bestaetigt": False}
    r = requests.post(f"{API}/werkzeuge/app-start/{start}", headers=welt["sucher"], timeout=30)
    assert r.status_code == 200, r.text
    assert requests.post(f"{API}/werkzeuge/app-start/{start}", headers=welt["sucher"], timeout=30).status_code == 200
    assert frage(start).json() == {"bestaetigt": True}
    # eine fremde Firma meldet denselben Start nicht fuer uns
    fremd = uuid.uuid4().hex
    requests.post(f"{API}/werkzeuge/app-start/{fremd}", headers=welt["andere"], timeout=30)
    assert frage(fremd).json() == {"bestaetigt": False}
    assert requests.post(f"{API}/werkzeuge/app-start/kaputt", headers=welt["sucher"], timeout=30).status_code == 400
    assert requests.post(f"{API}/werkzeuge/app-start/{start}", timeout=30).status_code in (401, 403)
    assert frage("kaputt").json() == {"bestaetigt": False}
    welt["db"].werkzeug_app_starts.delete_many({"start": {"$in": [start, fremd]}})


# ------------------------------------------------------------ Paket 2 (Pruefung 05./06.10.2026)
def test_70_vergleiche_laufen_nach_60_tagen_ab(welt):
    """Entscheidung Ahmad 06.10.2026: Vergleiche 60 Tage aufbewahren — jeder neue Eintrag traegt `ablauf`
    (TTL-Index werkzeug_vergleiche_ablauf), der Bestand bekommt ihn per Migration 22."""
    import asyncio
    from datetime import datetime, timedelta, timezone
    from indizes import WERKZEUG_INDIZES
    assert any(s == "werkzeug_vergleiche" and k == [("ablauf", 1)] and o.get("expireAfterSeconds") == 0
               for s, k, o in WERKZEUG_INDIZES)
    assert any(s == "werkzeug_vergleiche" and k == [("user_id", 1)] for s, k, o in WERKZEUG_INDIZES)
    assert any(s == "werkzeug_vergleiche" and k == [("dealer_id", 1)] for s, k, o in WERKZEUG_INDIZES)
    assert wz.VERGLEICHE_TAGE == 60
    db = welt["db"]
    doc = db.werkzeug_vergleiche.find_one({"user_id": welt["sucher_id"], "probelauf": {"$ne": True}},
                                          sort=[("erstellt_am", -1)])
    assert doc and isinstance(doc.get("ablauf"), datetime), doc
    frist = doc["ablauf"].replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)
    assert timedelta(days=59) < frist <= timedelta(days=60), frist
    # Migration 22: Bestand ohne ablauf
    alt_id = f"alt-{uuid.uuid4().hex[:8]}"
    db.werkzeug_vergleiche.insert_one({"id": alt_id, "werkzeug": WID, "dealer_id": welt["firma"]["dealer_id"],
                                       "user_id": welt["sucher_id"], "erstellt_am": "2026-09-01T10:00:00+00:00",
                                       "fahrzeug": {}, "links": [], "probelauf": False})
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        import migrationen
        async def lauf():
            client = AsyncIOMotorClient(konten.MONGO_URL, serverSelectionTimeoutMS=5000)
            try:
                return await migrationen.m22_werkzeug_vergleiche_ablauf(client[db.name])
            finally:
                client.close()
        stats = asyncio.run(lauf())
        assert stats["gesetzt"] >= 1, stats
        nach = db.werkzeug_vergleiche.find_one({"id": alt_id})
        assert nach["ablauf"].replace(tzinfo=timezone.utc) == datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)
        assert asyncio.run(lauf())["gesetzt"] == 0                      # idempotent
    finally:
        db.werkzeug_vergleiche.delete_one({"id": alt_id})


def test_72_quelltext_paket2():
    """Bremsen je Konto (Neu-Verbinden setzte den Zaehler zurueck), Vorab-Tausch in einem Zug, Auswerten mit
    Zeitlimit, Datenbank-Ausfall als 503."""
    import inspect
    import routes.werkzeuge as rw
    import server
    q = inspect.getsource(rw)
    assert "verbindung:{v['id']}" not in q, "Bremse noch je Verbindung"
    assert q.count("konto:{user['id']}") >= 4
    assert "ReturnDocument.BEFORE" in inspect.getsource(rw._vorab_ersetzen)
    assert "wait_for" in inspect.getsource(rw._auswerten) and "503" in inspect.getsource(rw._auswerten)
    s = inspect.getsource(server.ErrorReportingMiddleware)
    assert "ConnectionFailure" in s and "Retry-After" in s and "503" in s


def test_73_datenbank_kurz_weg_ist_503():
    import asyncio
    from pymongo.errors import ServerSelectionTimeoutError
    from starlette.requests import Request
    import server
    mw = server.ErrorReportingMiddleware(app=None)
    anfrage = Request({"type": "http", "method": "POST", "path": "/api/werkzeuge/x/vergleich", "headers": [],
                       "query_string": b"", "scheme": "http", "server": ("test", 80), "client": ("127.0.0.1", 1)})

    async def weg(_):
        raise ServerSelectionTimeoutError("No replica set members match selector Primary()")

    antwort = asyncio.run(mw.dispatch(anfrage, weg))
    assert antwort.status_code == 503 and antwort.headers.get("retry-after") == "5"
    assert "Datenbank" in antwort.body.decode("utf-8")


def test_74_neues_passwort_trennt_programm_und_helfer(welt):
    """Entscheidung Ahmad 06.10.2026: ein neues Passwort (Admin setzt es) trennt alle Werkzeuge des Kontos — der
    naechste Aufruf bekommt 401 mit dem Grund, ein neuer Code verbindet wieder."""
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient
    import routes.werkzeuge as rw
    _abo(welt, True)
    _, prog, _ = _verbinden(welt, "PC-Passwort")
    r = requests.post(f"{API}/werkzeuge/browser-helfer/code", headers=welt["sucher"], timeout=30)
    assert r.status_code == 200, r.text
    r = requests.post(f"{API}/werkzeuge/browser-helfer/verbinden", timeout=30,
                      json={"code": r.json()["code"], "pc_name": "Edge", "pc_kennung": "edge-passwort"})
    assert r.status_code == 200, r.text
    helfer = {wz.TOKEN_KOPF: r.json()["schluessel"], "X-Werkzeug-Version": "2.7.0"}
    assert requests.get(f"{API}/werkzeuge/{WID}/status", headers=prog, timeout=30).status_code == 200

    async def lauf():
        client = AsyncIOMotorClient(konten.MONGO_URL, serverSelectionTimeoutMS=5000)
        alt = rw.db
        rw.db = client[welt["db"].name]
        try:
            return await rw.alle_trennen(welt["sucher_id"], "passwort")
        finally:
            rw.db = alt
            client.close()
    assert asyncio.run(lauf()) == 2
    for kopf, wid in ((prog, WID), (helfer, "browser-helfer")):
        r = requests.get(f"{API}/werkzeuge/{wid}/status", headers=kopf, timeout=30)
        assert r.status_code == 401 and "Passwort" in r.json()["detail"] and "neuen Code" in r.json()["detail"], r.text
    assert welt["db"].werkzeug_verbindungen.count_documents({"user_id": welt["sucher_id"]}) == 0
    assert asyncio.run(lauf()) == 0                                       # nichts mehr zu trennen
    # die Admin-Route ruft es auf (Quelltext)
    import inspect
    import routes.admin as ra
    assert "alle_trennen(user_id, \"passwort\")" in inspect.getsource(ra.admin_user_set_password)
