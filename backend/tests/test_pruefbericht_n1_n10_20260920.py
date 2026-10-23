# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026, zweite Runde (N1-N10) — gegengeprueft am Stand dc3c606.

N1  Waehrend des Versands entstand eine neue Fassung: der Verkaeufer bekam
    Fassung N, der Vertrag stand danach als Fassung N+1 auf "versendet" und
    verlor den Merker `nach_abholung_versand_offen`. Bei einem KAUFVERTRAG
    der schwerste Befund des Berichts.
N2  Aendern und Loeschen warteten nicht auf einen laufenden Versand.
N3  Die Einnordung "kein Hauptchef -> Sucher" griff nur bei GESETZTEM
    `dealers.user_id`; Altbestand blieb aussen vor, und es fehlte die
    Migration, die den Zeiger nachtraegt.
N4  Die Firmensperre prueft in current_user nur Konten, die schon als Sucher
    ankommen — ein uebrig gebliebenes dealer-Konto kam daran vorbei.
N5  beweise.py rief current_firma auf, warf den Rueckgabewert aber weg.
N7  run_abgleich_forever und betriebsmeldung schrieben waehrend einer
    Schreibpause weiter (#64 nur teilweise repariert).
N8  Der E2E-Helfer publishListing veroeffentlichte ohne eigenes Foto.
N10 .env.example hatte WEB_CONCURRENCY zweimal mit verschiedenen Werten;
    provider_limiter hatte fuer Kleinanzeigen 3, Compose/.env 2.
"""
import ast
import asyncio
import re
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
PROJEKT = BACKEND.parent


def _quelle(datei: str) -> str:
    return (BACKEND / datei).read_text(encoding="utf-8")


def _nur_code(datei: str) -> str:
    """Quelltext ohne Kommentare UND ohne Docstrings — die nennen den alten
    Fehler absichtlich, sonst schlagen die Suchen unten darauf an."""
    baum = ast.parse(_quelle(datei))
    for knoten in ast.walk(baum):
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef, ast.Module)):
            leib = getattr(knoten, "body", [])
            if (leib and isinstance(leib[0], ast.Expr)
                    and isinstance(leib[0].value, ast.Constant)
                    and isinstance(leib[0].value.value, str)):
                leib.pop(0)
    return ast.unparse(baum)


# ------------------------------------------------------------------ N1
def test_n1_veraltete_fassung_markiert_den_vertrag_nicht_als_versendet():
    import routes.contracts as C

    send_entry = {"idempotency_key": "k1", "version": 3}

    # Fall A: die versendete Fassung ist noch die aktuelle -> alles wie bisher.
    aktuell = C._abschluss(send_entry, "versendet", False, wiederaufnahme=False)
    assert aktuell["$set"]["status"] == "versendet"
    assert "nach_abholung_versand_offen" in aktuell["$unset"]

    # Fall B: waehrend des Versands entstand Fassung N+1.
    veraltet = C._abschluss(send_entry, "versendet", True, wiederaufnahme=False)
    assert "status" not in veraltet.get("$set", {}), (
        "der Vertrag wird trotz neuerer Fassung als versendet markiert (N1)")
    assert "$unset" not in veraltet, (
        "die Erinnerung 'neue Fassung senden' wird geloescht, obwohl der "
        "Verkaeufer die ALTE Fassung hat (N1)")
    # Der Beleg muss trotzdem geschrieben werden — er traegt die Fassung,
    # die wirklich rausging.
    assert veraltet["$push"]["send_status"]["$each"] == [send_entry]
    assert veraltet["$set"]["updated_at"]


def test_n1_gilt_auch_bei_der_wiederaufnahme():
    import routes.contracts as C

    eintrag = {"idempotency_key": "k2", "version": 2}
    veraltet = C._abschluss(eintrag, "versendet", True, wiederaufnahme=True)
    assert veraltet["$set"]["send_status.$"] == eintrag
    assert "status" not in veraltet["$set"], (
        "die Wiederaufnahme markiert den Vertrag trotz neuerer Fassung als "
        "versendet (N1)")
    assert "$unset" not in veraltet

    aktuell = C._abschluss(eintrag, "versendet", False, wiederaufnahme=True)
    assert aktuell["$set"]["status"] == "versendet"
    assert "nach_abholung_versand_offen" in aktuell["$unset"]


def test_n1_der_merker_wird_im_versandweg_wirklich_gesetzt():
    """Gegenprobe an der Quelle: die Fassungspruefung muss den Merker setzen,
    und der Abschluss muss ihn bekommen."""
    code = _nur_code("routes/contracts.py")
    assert "fassung_veraltet = True" in code, (
        "die Fassungspruefung setzt keinen Merker — der Abschluss kann den "
        "Fall gar nicht erkennen")
    assert code.count("_abschluss(send_entry, neuer_status, fassung_veraltet") == 2, (
        "nicht beide Abschluss-Wege gehen ueber _abschluss")


# ------------------------------------------------------------------ N2
def test_n2_aendern_und_loeschen_warten_auf_einen_laufenden_versand():
    code = _nur_code("routes/contracts.py")
    assert "async def _versand_laeuft" in code
    # Beide Wege muessen den Helfer benutzen.
    assert code.count("await _versand_laeuft(contract_id)") >= 2, (
        "Loeschen und/oder Neu-Erzeugen pruefen nicht auf einen laufenden "
        "Versand (N2)")
    baum = ast.parse(_quelle("routes/contracts.py"))
    fuer = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if "_versand_laeuft" in ast.unparse(knoten):
                fuer.add(knoten.name)
    assert {"delete_contract", "regenerate_contract_for_pickup"} <= fuer, (
        f"geprueft wird nur in {sorted(fuer)} — es fehlen Loeschen bzw. "
        "Neu-Erzeugen (N2)")


def test_n2_haengender_versand_blockiert_nicht_dauerhaft():
    """Ein abgestuerzter Versand darf den Vertrag nicht fuer immer sperren."""
    code = _nur_code("routes/contracts.py")
    anfang = code.index("async def _versand_laeuft")
    block = code[anfang:anfang + 1200]
    assert "ZUSTELLUNG_HAENGT_NACH_SEK" in block, (
        "die Pruefung kennt keine Frist — ein haengender Eintrag wuerde den "
        "Vertrag dauerhaft blockieren")


# ------------------------------------------------------------------ N3/N4
def test_n3_einnordung_greift_auch_ohne_chef_zeiger():
    code = _nur_code("deps.py")
    anfang = code.index("async def current_firma")
    block = code[anfang:code.index("async def ist_haupt_chef")]
    assert "sort=[('created_at', 1)]" in block.replace('"', "'"), (
        "ohne gesetzten Zeiger wird der aelteste Chef nicht ermittelt — "
        "Altbestand bleibt uneingenordet (N3)")
    assert "dealers.update_one" in block, (
        "der Zeiger wird nicht nachgetragen (N3)")


def test_n4_firmensperre_greift_nach_der_einnordung():
    code = _nur_code("deps.py")
    anfang = code.index("async def current_firma")
    block = code[anfang:code.index("async def ist_haupt_chef")]
    assert "firma_gesperrt" in block, (
        "ein eingenordetes dealer-Konto umgeht die Firmensperre (N4)")
    # Reihenfolge: erst einnorden, dann sperren pruefen.
    assert block.index("'sucher'") < block.index("firma_gesperrt"), (
        "die Sperre wird vor der Einnordung geprueft — genau der Fall aus N4")


def test_n3_migration_zieht_den_zeiger_nach():
    import migrationen as M
    nummern = [n for n, _, _ in M.MIGRATIONEN]
    assert 9 in nummern, "es fehlt die Migration fuer den Chef-Zeiger (N3)"
    assert M.ZIEL_VERSION >= 9, "ZIEL_VERSION wurde nicht mitgezogen"
    name = [na for n, na, _ in M.MIGRATIONEN if n == 9][0]
    assert "chef" in name


def test_n3_migration_setzt_den_aeltesten_chef():
    """Die Migration am echten Code, mit einer nachgestellten Datenbank."""
    import migrationen as M

    firmen = [{"id": "f-alt"}, {"id": "f-ok", "user_id": "chef-ok"},
              {"id": "f-leer"}]
    nutzer = {
        "f-alt": [{"id": "chef-jung"}, {"id": "chef-alt"}],  # sort liefert alt zuerst
        "f-leer": [],
    }
    geschrieben = {}

    class _Dealers:
        @staticmethod
        def find(filter_, proj=None):
            offen = [f for f in firmen if not f.get("user_id")]

            class _Cursor:
                def __aiter__(self):
                    async def gen():
                        for f in offen:
                            yield {"id": f["id"]}
                    return gen()
            return _Cursor()

        @staticmethod
        async def update_one(filter_, update):
            geschrieben[filter_["id"]] = update["$set"]["user_id"]
            return SimpleNamespace(modified_count=1)

    class _Users:
        @staticmethod
        async def find_one(filter_, proj=None, sort=None):
            liste = nutzer.get(filter_["dealer_id"], [])
            return dict(liste[-1]) if liste else None   # "aeltester"

    ergebnis = asyncio.run(M.m9_chef_zeiger(
        SimpleNamespace(dealers=_Dealers(), users=_Users())))
    assert geschrieben == {"f-alt": "chef-alt"}, geschrieben
    assert ergebnis["chef_zeiger_gesetzt"] == 1
    assert ergebnis["firmen_ohne_chef"] == 1, (
        "eine Firma ganz ohne dealer-Konto muss gezaehlt, aber nicht still "
        "repariert werden")


# ------------------------------------------------------------------ N5
def test_n5_beweise_nutzen_das_eingenordete_konto():
    code = _nur_code("routes/beweise.py")
    assert "await _firmenstatus_pruefen(user)" in code
    ohne = [z for z in code.splitlines()
            if "_firmenstatus_pruefen(user)" in z and "user =" not in z
            and "def " not in z]
    assert not ohne, (
        f"hier wird der Rueckgabewert weggeworfen: {ohne} — _darf_sehen "
        "arbeitet dann weiter mit role=dealer (N5)")


# ------------------------------------------------------------------ N7
@pytest.mark.parametrize("datei,funktion", [
    ("server.py", "run_abgleich_forever"),
    ("betriebsmeldung.py", "run_betriebsmeldung_forever"),
])
def test_n7_hintergrund_schreiber_halten_bei_schreibpause_an(datei, funktion):
    baum = ast.parse(_quelle(datei))
    for knoten in ast.walk(baum):
        if (isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef))
                and knoten.name == funktion):
            code = ast.unparse(knoten)
            assert "aktiv_async" in code, (
                f"{funktion} schreibt waehrend einer Schreibpause weiter (N7)")
            return
    pytest.fail(f"{funktion} nicht gefunden in {datei}")


# ------------------------------------------------------------------ N8
def test_n8_e2e_helfer_laedt_ein_eigenes_foto_hoch():
    h = (PROJEKT / "frontend" / "e2e" / "helpers.js").read_text(encoding="utf-8")
    anfang = h.index("async function publishListing")
    # OHNE Kommentare pruefen: der erklaerende Kommentar nennt "/publish"
    # selbst und stuende sonst vor dem Foto-Upload.
    block = "\n".join(z.split("//", 1)[0]
                      for z in h[anfang:anfang + 1800].splitlines())
    assert "/photos" in block, (
        "publishListing veroeffentlicht ohne eigenes Foto — die API antwortet "
        "seit dem 20.09.2026 mit 400 (N8)")
    assert block.index("/photos") < block.index("/publish"), (
        "das Foto wird erst NACH dem Veroeffentlichen hochgeladen")


# ------------------------------------------------------------------ N10
def test_n10_web_concurrency_steht_nur_einmal():
    t = (PROJEKT / ".env.example").read_text(encoding="utf-8")
    zuweisungen = re.findall(r"^WEB_CONCURRENCY=(\d+)", t, re.M)
    assert len(zuweisungen) == 1, (
        f"WEB_CONCURRENCY steht {len(zuweisungen)}x in .env.example "
        f"({zuweisungen}) — beim Einlesen gewinnt die letzte (N10)")
    compose = (PROJEKT / "docker-compose.yml").read_text(encoding="utf-8")
    aus_compose = re.search(r"WEB_CONCURRENCY=\$\{WEB_CONCURRENCY:-(\d+)\}", compose)
    assert aus_compose and aus_compose.group(1) == zuweisungen[0], (
        f".env.example sagt {zuweisungen[0]}, docker-compose.yml "
        f"{aus_compose.group(1) if aus_compose else '?'}")


def test_n10_kleinanzeigen_grenze_ist_ueberall_gleich():
    import provider_limiter as PL
    t = (PROJEKT / ".env.example").read_text(encoding="utf-8")
    compose = (PROJEKT / "docker-compose.yml").read_text(encoding="utf-8")
    aus_env = int(re.search(r"^MAX_CONCURRENT_KLEINANZEIGEN=(\d+)", t, re.M).group(1))
    aus_compose = int(re.search(
        r"MAX_CONCURRENT_KLEINANZEIGEN=\$\{MAX_CONCURRENT_KLEINANZEIGEN:-(\d+)\}",
        compose).group(1))
    import os
    if "MAX_CONCURRENT_KLEINANZEIGEN" in os.environ:
        pytest.skip("Umgebung setzt den Wert — der Standard ist hier nicht sichtbar")
    assert aus_env == aus_compose == PL.PROVIDER_MAX_CONCURRENT["kleinanzeigen"], (
        f".env.example {aus_env}, Compose {aus_compose}, Code "
        f"{PL.PROVIDER_MAX_CONCURRENT['kleinanzeigen']} (N10)")


# ------------------------------------------------------------------ N6
def test_n6_sicherung_wartet_auf_echtes_auslaufen():
    """Statt blind eine feste Zeit zu schlafen, fragt die Sicherung nach,
    ob wirklich niemand mehr schreibt."""
    quelle = (BACKEND / "scripts" / "backup_mongo.py").read_text(encoding="utf-8")
    code = "\n".join(z.split("#", 1)[0] for z in quelle.splitlines())
    assert "def auslaufen_lassen" in code, (
        "die Schreibpause hat kein echtes Auslaufen (N6)")
    assert "wartung.schreiber_stand" in code, (
        "die Sicherung fragt die Backend-Prozesse nicht nach ihrem Stand")
    assert "time.sleep(_wartung_warten_s())" not in code, (
        "es wird weiterhin blind geschlafen statt zu warten")


def test_n6_stichtagsgenau_nur_bei_bestaetigtem_auslaufen():
    """Der Kern: die Zusage darf nur fallen, wenn sie auch stimmt."""
    quelle = (BACKEND / "scripts" / "backup_mongo.py").read_text(encoding="utf-8")
    code = "\n".join(z.split("#", 1)[0] for z in quelle.splitlines())
    assert "if pause and ruhig:" in code, (
        "die Sicherung nennt sich stichtagsgenau, sobald die Pause AN war — "
        "auch wenn noch geschrieben wurde (N6)")
    assert "KONSISTENZ_SCHREIBPAUSE" in code


def test_n6_melde_sammlung_ist_vom_dump_ausgenommen():
    """Sonst veraendert ausgerechnet der Melder die Datenbank waehrend der
    Sicherung — das Gegenteil des Ziels."""
    quelle = (BACKEND / "scripts" / "backup_mongo.py").read_text(encoding="utf-8")
    code = "\n".join(z.split("#", 1)[0] for z in quelle.splitlines())
    # In backup_once heisst ein PARAMETER `wartung` und verdeckt dort das
    # Modul — deshalb der eigene Name `_wartung_modul`. Beide Schreibweisen
    # sind richtig, die Zusage ist: die Sammlung wird ausgelassen.
    assert ("n != wartung.SCHREIBER_COLLECTION" in code
            or "n != _wartung_modul.SCHREIBER_COLLECTION" in code), (
        "die Melde-Sammlung landet im Dump (N6)")


def test_n6_jeder_prozess_meldet_seinen_stand():
    import wartung
    assert hasattr(wartung, "schreiber_melden")
    assert hasattr(wartung, "schreiber_stand")
    # Ohne Meldung gilt NICHT "ruhig" — sonst wuerde eine alte Fassung ohne
    # Melder faelschlich als stichtagsgenau durchgehen.
    class _Leer:
        @staticmethod
        def find(_f, _p=None):
            return []
    ruhig, offen, prozesse = wartung.schreiber_stand(_Leer())
    assert not ruhig and prozesse == 0, (
        "ohne eine einzige Meldung gilt die Datenbank als ruhig — dann "
        "bringt das Auslaufen nichts")

    class _Zwei:
        @staticmethod
        def find(_f, _p=None):
            return [{"offen": 0}, {"offen": 0}]
    assert wartung.schreiber_stand(_Zwei())[0] is True

    class _Beschaeftigt:
        @staticmethod
        def find(_f, _p=None):
            return [{"offen": 0}, {"offen": 3}]
    ruhig, offen, prozesse = wartung.schreiber_stand(_Beschaeftigt())
    assert not ruhig and offen == 3 and prozesse == 2


def test_n6_der_melder_laeuft_als_eigener_worker():
    quelle = (BACKEND / "server.py").read_text(encoding="utf-8")
    code = "\n".join(z.split("#", 1)[0] for z in quelle.splitlines())
    assert "run_schreiber_melden_forever" in code
    assert '_worker_starten("schreiber_melden"' in code, (
        "der Melder wird nie gestartet (N6)")
    # Er darf im Normalbetrieb NICHTS schreiben.
    anfang = code.index("async def run_schreiber_melden_forever")
    block = code[anfang:anfang + 900]
    assert "wartung.pausiert(doc" in block, (
        "der Melder schreibt auch ohne Schreibpause")
