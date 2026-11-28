# -*- coding: utf-8 -*-
"""Wunsch Ahmad 24.11.2026: "pruefe den Server, ist alles gut, gibt es Probleme; schau alle nicht erkannten
Inserate an".

- scripts/lesebilder_export.py schreibt zusaetzlich vergleiche_problem.jsonl (Programm-Vergleiche ohne vollen Erfolg,
  nur die gelesenen Fahrzeugwerte) und leseseiten.jsonl (nur Kennzahlen, NIE die Seite selbst)
- deploy/serverpruefung.sh sammelt auf dem Server alles ein (nur lesen) und gibt NUR das Archiv auf stdout aus
- deploy/serverpruefung.ps1 schickt Skript + Pruefskripte hin und holt das Ergebnis (binaer ueber cmd, LF-Zeilen)
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PROJEKT = BACKEND.parent
SCRIPTS = BACKEND / "scripts"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SCRIPTS))
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"

import lesebilder_export as E  # noqa: E402


def test_01_probleme_und_link_grund():
    gut = {"fahrzeug": {"modell_gefunden": True, "inserat_url": "https://x"}, "links": [{"url": "a"}]}
    assert E.vergleich_probleme(gut) == []
    schlecht = {"fahrzeug": {"modell_gefunden": False, "inserat_url": None, "inserat_nummer_unsicher": True},
                "links": []}
    assert E.vergleich_probleme(schlecht) == ["modell_nicht_gefunden", "ohne_inserat_link", "nummer_unsicher",
                                              "keine_vergleichslinks"]
    # fehlt modell_gefunden ganz (alte Eintraege), zaehlt das nicht als "nicht gefunden"
    assert "modell_nicht_gefunden" not in E.vergleich_probleme({"fahrzeug": {"inserat_url": "x"}, "links": [1]})
    assert E.link_grund({"inserat_url": "x"}) == ""
    assert E.link_grund({"inserat_nummer_unsicher": True}).startswith("Nummer verworfen")
    assert E.link_grund({}) == "Portal nicht gelesen"
    assert E.link_grund({"quelle": "AutoScout24", "inserat_id": "123"}) == "AutoScout ohne vollstaendige Hash-ID"
    assert E.link_grund({"quelle": "mobile.de"}) == "keine Inserat-Nummer gelesen"
    assert E.link_grund({"quelle": "Unbekannt", "inserat_id": "12"}) == "Nummer/Portal passt nicht zusammen"
    # Filter und Pruefung meinen dasselbe
    bedingungen = {list(b)[0] for b in E.VERGLEICH_PROBLEM_FILTER["$or"]}
    assert bedingungen == {"fahrzeug.modell_gefunden", "fahrzeug.inserat_url", "fahrzeug.inserat_nummer_unsicher",
                           "links"}
    assert "beschreibung" not in E.VERGLEICH_FELDER


@pytest.fixture()
def welt(tmp_path):
    try:
        from pymongo import MongoClient
        c = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
        c.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"keine MongoDB: {exc}")
    name = f"sp_export_{uuid.uuid4().hex[:8]}"
    db = c[name]
    jetzt = datetime.now(timezone.utc)
    vor = lambda tage: (jetzt - timedelta(days=tage)).isoformat()  # noqa: E731
    db.users.insert_many([{"id": "u1", "kontonummer": "10002-1", "role": "buyer"}])
    db.dealers.insert_many([{"id": "d1", "kunden_nr": 10002}])
    fz_gut = {"marke": "BMW", "modell": "320", "marke_modell_text": "BMW 320d", "modell_gefunden": True,
              "inserat_url": "https://suchen.mobile.de/fahrzeuge/details.html?id=1", "quelle": "mobile.de"}
    db.werkzeug_vergleiche.insert_many([
        {"id": "v1", "user_id": "u1", "dealer_id": "d1", "erstellt_am": vor(1), "probelauf": False,
         "fahrzeug": fz_gut, "links": [{"url": "a"}], "hinweise": []},
        {"id": "v2", "user_id": "u1", "dealer_id": "d1", "erstellt_am": vor(2), "probelauf": False,
         "fahrzeug": {"marke": "Weitere", "marke_modell_text": "Weitere Marken Xyz", "titel": "Xyz Supercar 2.0",
                      "modell_gefunden": False, "inserat_url": None, "quelle": "mobile.de", "inserat_id": "",
                      "beschreibung": "GEHEIM-BESCHREIBUNG 0171 1234567"},
         "links": [], "hinweise": ["Modell nicht erkannt"]},
        {"id": "v3", "user_id": "u1", "dealer_id": "d1", "erstellt_am": vor(3), "probelauf": False,
         "fahrzeug": {**fz_gut, "inserat_url": None, "quelle": "AutoScout24", "inserat_id": "999"},
         "links": [{"url": "b"}], "hinweise": []},
        # Probelauf und zu alt zaehlen nicht
        {"id": "v4", "user_id": "u1", "dealer_id": "d1", "erstellt_am": vor(1), "probelauf": True,
         "fahrzeug": {"modell_gefunden": False}, "links": []},
        {"id": "v5", "user_id": "u1", "dealer_id": "d1", "erstellt_am": vor(90), "probelauf": False,
         "fahrzeug": {"modell_gefunden": False}, "links": []},
    ])
    db.werkzeug_leseseiten.insert_one({
        "id": "s1", "user_id": "u1", "dealer_id": "d1", "url": "https://suchen.mobile.de/auto-inserat/x/1.html",
        "portal": "mobile", "item_id": "1", "grund": "keine Inseratsdaten", "seite": "GEHEIM-SEITE",
        "groesse": 12, "erstellt_am": vor(1)})
    jpg = base64.b64encode(b"\xff\xd8\xff\xe0FAKEJPEG").decode()
    db.werkzeug_lesebilder.insert_one({
        "id": "b1", "user_id": "u1", "dealer_id": "d1", "erstellt_am": vor(1), "grund": "pflichtfeld_fehlt",
        "fehlt": ["Kilometer"], "rohtext": "Kilometer ???", "fahrzeug": {"quelle": "mobile.de"},
        "vorschau_b64": jpg})
    yield {"db": name, "ziel": tmp_path / "export"}
    c.drop_database(name)


def test_02_export_schreibt_vergleiche_und_leseseiten(welt):
    env = dict(os.environ, MONGO_URL=MONGO_URL, DB_NAME=welt["db"], PYTHONIOENCODING="utf-8", S3_BUCKET="",
               EMERGENT_LLM_KEY="")
    r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / "lesebilder_export.py"),
                        "--ziel", str(welt["ziel"]), "--tage", "30"],
                       capture_output=True, text=True, env=env, timeout=300, cwd=str(BACKEND))
    assert r.returncode == 0, r.stdout + r.stderr
    ziel = welt["ziel"]
    # Lesebild: Vorschau als Ersatz (kein Speicher-Schluessel)
    bild = [json.loads(z) for z in (ziel / "lesebilder.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(bild) == 1 and bild[0]["datei"].endswith("_vorschau.jpg") and (ziel / bild[0]["datei"]).is_file()
    # Vergleiche: nur v2 und v3 (v1 gut, v4 Probelauf, v5 zu alt)
    vg = [json.loads(z) for z in (ziel / "vergleiche_problem.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [v["fahrzeug"].get("marke_modell_text") for v in vg] == ["Weitere Marken Xyz", "BMW 320d"]
    assert vg[0]["probleme"] == ["modell_nicht_gefunden", "ohne_inserat_link", "keine_vergleichslinks"]
    assert vg[0]["link_grund"] == "keine Inserat-Nummer gelesen" and vg[0]["kontonummer"] == "10002-1"
    assert vg[0]["kunden_nr"] == "10002" and vg[0]["links"] == 0 and vg[0]["hinweise"] == ["Modell nicht erkannt"]
    assert vg[1]["probleme"] == ["ohne_inserat_link"]
    assert vg[1]["link_grund"] == "AutoScout ohne vollstaendige Hash-ID"
    # Leseseiten: Kennzahlen ja, Seite nie
    ls = [json.loads(z) for z in (ziel / "leseseiten.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(ls) == 1 and ls[0]["grund"] == "keine Inseratsdaten" and ls[0]["groesse"] == 12
    alles = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in ziel.iterdir() if p.suffix != ".jpg")
    assert "GEHEIM-SEITE" not in alles and "GEHEIM-BESCHREIBUNG" not in alles and "1234567" not in alles
    ueb = (ziel / "uebersicht.txt").read_text(encoding="utf-8")
    assert "Programm-Vergleiche im Zeitraum: 3, davon mit Problem: 2 (67 %)" in ueb
    assert "modell_nicht_gefunden 1 (33 %)" in ueb and "Weitere Marken Xyz | Xyz Supercar 2.0 (1)" in ueb
    assert "Leseseiten (Erweiterung konnte die Seite nicht lesen): 1" in ueb


SH = PROJEKT / "deploy" / "serverpruefung.sh"
PS1 = PROJEKT / "deploy" / "serverpruefung.ps1"


def test_03_server_skript_nur_lesen_und_nur_archiv_auf_stdout():
    text = SH.read_text(encoding="utf-8")
    code = "\n".join(z.split("#", 1)[0] if not z.lstrip().startswith("#!") else "" for z in text.splitlines())
    assert "exec 3>&1 1>&2" in code, "alles ausser dem Archiv muss nach stderr"
    assert code.count(">&3") == 1 and re.search(r"tar czf - .*>&3", code)
    # nichts Veraenderndes: kein Neustart, kein Rollout, keine .env, kein Loeschen ausser eigener Temp-Ordner
    for verboten in ("compose up", "compose down", "compose restart", "compose stop", "rollout.sh", "env_setzen",
                     "freigeben.sh", "git pull", "git reset", "git checkout", "migrationen.py", "drain/aktiv"):
        assert verboten not in code, verboten
    for z in re.findall(r"rm -rf [^\n;|&]+", code):
        assert "/tmp/" in z or '"$ARBEIT"' in z or '"$HIER"' in z, z
    assert 'case "$HIER" in /tmp/autoschnell-pruefung*) rm -rf "$HIER"' in code, "Projektordner nie loeschen"
    if shutil.which("sh"):
        r = subprocess.run(["sh", "-n", str(SH)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_04_pc_skript_binaer_und_lf():
    text = PS1.read_text(encoding="utf-8")
    # Archiv laeuft durch cmd (PowerShell 5.1 kodiert Pipelines als Text um)
    assert "cmd.exe /c $batch" in text and "| `\"$Ssh`\"" in text
    assert '.Replace("`r`n", "`n")' in text, "sh braucht LF-Zeilen"
    for d in re.findall(r'"((?:deploy|backend\\scripts)\\[a-z_]+\.(?:sh|py))"', text):
        assert (PROJEKT / d.replace("\\", "/")).is_file(), d
    assert "serverpruefung.sh $art $Tage < /dev/null" in text
    assert '[string]$Server = "root@2.28.66.8"' in text
