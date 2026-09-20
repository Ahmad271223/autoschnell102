# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026, dritte Runde — neue, unabhaengige Befunde.

Nr. 1  Die Fahrer-Sichtfrist (14/30 Tage) galt nur fuer einzelne Dokumente.
       Protokoll, Protokoll-PDF und die Abweichungsfotos kannten sie nicht:
       wer sich die Adresse gemerkt hatte, kam Monate spaeter noch an das
       unterschriebene Abholprotokoll mit Verkaeuferdaten.
Nr. 2  Beim Loeschen eines Suchers laeuft eine bereits angemeldete Anfrage
       weiter und kann danach noch mit der geloeschten user_id schreiben —
       ohne dass diese Waisendaten je jemandem zugeordnet wuerden.
Nr. 3  Die Schreibpause entscheidet nach HTTP-Methode. Mehrere GET-Wege
       schreiben aber trotzdem (Altbestands-Reparatur, Abrufzaehler).
Nr. 5  Die neue 10-Foto-Grenze war unter Gleichzeitigkeit wirkungslos: der
       atomare Riegel stand weiter auf 40.
Nr. 6  Ein veroeffentlichtes Inserat konnte sein letztes Foto verlieren —
       und ein Rennen zwischen Veroeffentlichen und Entfernen liess es sogar
       mit null Fotos live gehen.
Nr. 7  Der Fahrer-Upload erlaubte ~40 MB, der Produktions-nginx nur 25.
Nr. 8  "Nichts geaendert" beim Reservieren galt immer als "laeuft schon" —
       auch wenn der Vertrag gerade geloescht oder uebertragen wurde.
Nr. 9  Wartende Anbieter-Jobs pruefen die Firmensperre nicht.
"""
import ast
import asyncio
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
PROJEKT = BACKEND.parent


def _nur_code(datei: str) -> str:
    """Quelltext ohne Kommentare und Docstrings — die nennen den alten
    Fehler absichtlich beim Namen."""
    baum = ast.parse((BACKEND / datei).read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef, ast.Module)):
            leib = getattr(knoten, "body", [])
            if (leib and isinstance(leib[0], ast.Expr)
                    and isinstance(leib[0].value, ast.Constant)
                    and isinstance(leib[0].value.value, str)):
                leib.pop(0)
    return ast.unparse(baum)


# ------------------------------------------------------------------ Nr. 1
def test_01_fahrer_sichtfrist_gilt_zentral_fuer_alle_protokollwege():
    code = _nur_code("routes/protocols.py")
    anfang = code.index("async def _appt_or_404")
    block = code[anfang:anfang + 900]
    assert "unterlagen_zugriff_oder_404" in block, (
        "_appt_or_404 erzwingt die Sichtfrist nicht — Protokoll, PDF, "
        "Speichern, Einreichen, Korrektur und Abschluss haengen alle daran")


def test_01_abweichungsfotos_kennen_die_sichtfrist():
    code = _nur_code("routes/drivers.py")
    anfang = code.index("async def driver_pickup_foto")
    block = code[anfang:anfang + 2000]
    assert "unterlagen_zugriff_oder_404" in block, (
        "die Abweichungsfotos bleiben nach Ablauf der Frist erreichbar")


def test_01_die_frist_wirkt_wirklich():
    """Am echten Helfer, nicht nur am Quelltext."""
    from fastapi import HTTPException
    from routes.drivers import (unterlagen_zugriff_oder_404,
                                FAHRER_SICHT_ABGEHOLT_TAGE)
    frisch = {"status": "abgeholt",
              "abgeschlossen_seit": datetime.now(timezone.utc).isoformat()}
    unterlagen_zugriff_oder_404(frisch)          # darf nicht werfen

    alt = {"status": "abgeholt",
           "abgeschlossen_seit": (datetime.now(timezone.utc)
                                  - timedelta(days=FAHRER_SICHT_ABGEHOLT_TAGE + 1)
                                  ).isoformat()}
    with pytest.raises(HTTPException) as e:
        unterlagen_zugriff_oder_404(alt)
    assert e.value.status_code == 404

    # Eine noch offene Fahrt ist nie betroffen — sonst braeche der Abschluss.
    unterlagen_zugriff_oder_404({"status": "offen"})


# ------------------------------------------------------------------ Nr. 2
def test_02_geloeschter_sucher_hinterlaesst_einen_grabstein():
    code = _nur_code("routes/admin.py")
    assert "KONTO_NACHLESE" in code, (
        "beim Loeschen eines Suchers entsteht kein Grabstein — Waisendaten "
        "aus laufenden Anfragen blieben liegen (Nr. 2)")


def test_02_die_nachlese_wiederholt_die_uebergabe():
    import cleanup_service as CS
    assert hasattr(CS, "konto_nachlese_abarbeiten")
    code = _nur_code("cleanup_service.py")
    # ast.unparse normalisiert Anfuehrungszeichen — beide Schreibweisen gelten.
    assert ('stats["konto_nachlese"]' in code
            or "stats['konto_nachlese']" in code), (
        "die Nachlese laeuft in keinem Aufraeumdurchgang")


def test_02_nachlese_raeumt_alte_grabsteine_ab():
    """Ein Grabstein darf nicht ewig liegen bleiben."""
    import cleanup_service as CS

    jetzt = datetime.now(timezone.utc)
    steine = [
        {"_id": "frisch", "dealer_id": "f1", "an": "chef",
         "seit": jetzt.isoformat()},
        {"_id": "uralt", "dealer_id": "f1", "an": "chef",
         "seit": (jetzt - timedelta(days=3)).isoformat()},
        {"_id": "kaputt", "dealer_id": "f1", "an": "chef", "seit": "quatsch"},
    ]
    geloescht = []
    uebergeben = []

    class _Coll:  # noqa: D401
        @staticmethod
        def find(_f, _p=None):
            class _C:
                def __aiter__(self):
                    async def gen():
                        for s in list(steine):
                            yield s
                    return gen()
            return _C()

        @staticmethod
        async def delete_one(f):
            geloescht.append(f["_id"])

    import routes.bestand as B
    echt_ueber = B.vorgang_uebergeben

    async def _ueber(dealer_id, vehicle_id, von, an):
        uebergeben.append(von)
        return {"vertraege": 0}

    # konto_nachlese_abarbeiten bekommt die Datenbank als Argument — hier
    # eine winzige Attrappe, die nur db[SAMMLUNG] beherrscht.
    class _Db:
        def __getitem__(self, _name):
            return _Coll()

    B.vorgang_uebergeben = _ueber
    try:
        asyncio.run(CS.konto_nachlese_abarbeiten(_Db(), jetzt))
    finally:
        B.vorgang_uebergeben = echt_ueber

    assert "uralt" in geloescht and "kaputt" in geloescht, (
        f"alte/kaputte Grabsteine bleiben liegen: {geloescht}")
    assert "frisch" not in geloescht, "ein frischer Grabstein wurde weggeworfen"
    assert uebergeben == ["frisch"], (
        f"die Uebergabe lief fuer {uebergeben} statt nur fuer den frischen")


# ------------------------------------------------------------------ Nr. 3
@pytest.mark.parametrize("datei,funktion", [
    ("routes/beweise.py", "beweis_zum_fahrzeug"),
    ("routes/contracts.py", "_abruf_zaehlen"),
])
def test_03_schreibende_get_wege_ruhen_bei_schreibpause(datei, funktion):
    baum = ast.parse((BACKEND / datei).read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if (isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef))
                and knoten.name == funktion):
            code = ast.unparse(knoten)
            assert "schreiben_pausiert" in code, (
                f"{funktion} schreibt waehrend einer Schreibpause weiter, "
                "obwohl die Middleware GET durchlaesst (Nr. 3)")
            return
    pytest.fail(f"{funktion} nicht gefunden in {datei}")


def test_03_der_helfer_ist_fail_open():
    """Kann die Frage nicht beantwortet werden, wird geschrieben wie bisher —
    eine kaputte Abfrage darf keine Funktion lahmlegen."""
    code = _nur_code("wartung.py")
    anfang = code.index("async def schreiben_pausiert")
    block = code[anfang:anfang + 400]
    assert "return False" in block


# ------------------------------------------------------------------ Nr. 5
def test_05_atomarer_fotoriegel_haelt_dieselbe_zahl_wie_die_vorpruefung():
    code = _nur_code("routes/resale.py")
    assert "40 - len(added)" not in code, (
        "der atomare Riegel steht noch auf 40, waehrend die Vorpruefung "
        "INSERAT_FOTOS_MAX nimmt — zwei gleichzeitige Uploads kommen so "
        "gemeinsam ueber die Grenze (Nr. 5)")
    assert code.count("INSERAT_FOTOS_MAX - len(added)") == 2, (
        "nicht beide Upload-Wege (normal und aus dem Abholbericht) nutzen "
        "die eingestellte Grenze")


def test_05_die_meldungen_nennen_die_richtige_zahl():
    quelle = (BACKEND / "routes" / "resale.py").read_text(encoding="utf-8")
    assert "max. 40 Fotos" not in quelle and "Maximal 40 Fotos" not in quelle, (
        "die Fehlermeldung spricht noch von 40 Fotos")


# ------------------------------------------------------------------ Nr. 6
def test_06_veroeffentlichtes_inserat_verliert_nicht_sein_letztes_foto():
    code = _nur_code("routes/resale.py")
    anfang = code.index("async def remove_photo")
    block = code[anfang:anfang + 3000]
    assert "photos.uploaded_keys.1" in block, (
        "remove_photo prueft nicht, ob danach noch ein Bild uebrig bleibt — "
        "ein veroeffentlichtes Inserat kann auf null Fotos fallen (Nr. 6)")
    assert "veroeffentlicht" in block


def test_06_publish_prueft_den_fotostand_im_finalen_abgleich():
    """Am ROHEN Quelltext: ast.unparse normalisiert Anfuehrungszeichen, und
    hier geht es um genau diesen Filter."""
    quelle = (BACKEND / "routes" / "resale.py").read_text(encoding="utf-8")
    anfang = quelle.index("async def publish_listing")
    block = quelle[anfang:]
    marke = '{"$set": {"status": "veroeffentlicht",'
    assert marke in block, "der Statuswechsel sieht anders aus als erwartet"
    cas = block[max(0, block.index(marke) - 800):block.index(marke)]
    assert ("photos.uploaded_keys.0" in cas
            and "photos.einkauf_urls.0" in cas), (
        "der finale Abgleich prueft nur den Status — wird zwischendurch das "
        "letzte Foto entfernt, geht das Inserat trotzdem live (Nr. 6)")


# ------------------------------------------------------------------ Nr. 7
def test_07_upload_grenzen_passen_zu_nginx():
    nginx = (PROJEKT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    treffer = re.search(r"client_max_body_size\s+(\d+)m", nginx)
    assert treffer, "client_max_body_size steht nicht in deploy/nginx.conf"
    grenze_bytes = int(treffer.group(1)) * 1024 * 1024

    import routes.drivers as D
    import routes.resale as R
    for name, wert in (("Fahrer-Upload", D.FOTOS_GESAMT_MAX),
                       ("Inserats-Upload", R.PHOTOS_GESAMT_MAX)):
        assert wert < grenze_bytes, (
            f"{name} erlaubt {wert} Zeichen, nginx laesst nur "
            f"{grenze_bytes} Bytes durch — die Anfrage scheitert mit 413, "
            f"bevor das Backend sie sieht (Nr. 7)")
        # Und nicht zu knapp: der Rest der Anfrage braucht auch Platz.
        assert wert <= grenze_bytes * 0.85, (
            f"{name} liegt zu dicht an der nginx-Grenze")


# ------------------------------------------------------------------ Nr. 8
def test_08_nichts_geaendert_heisst_nicht_automatisch_laeuft_schon():
    code = _nur_code("routes/contracts.py")
    assert "async def _reservierung_nachlesen" in code, (
        "es wird weiter geraten, warum die Reservierung nichts geaendert hat")
    assert code.count("await _reservierung_nachlesen(") == 2, (
        "nur einer der beiden Reservierungswege liest nach (Nr. 8)")
    anfang = code.index("async def _reservierung_nachlesen")
    block = code[anfang:anfang + 1400]
    assert "404" in block and "nicht mehr verf" in block, (
        "ein geloeschter/uebertragener Vertrag wird nicht als solcher gemeldet")


# ------------------------------------------------------------------ Nr. 9
def test_09_wartende_jobs_pruefen_die_firmensperre():
    code = _nur_code("link_jobs.py")
    anfang = code.index("async def wartender_darf_abrufen")
    block = code[anfang:anfang + 2500]
    assert "firma_gesperrt" in block, (
        "ein eingereihter Job ruft auch nach einer Firmensperre noch beim "
        "Anbieter ab und verbraucht Kontingent (Nr. 9)")
    # Fail-closed: kann die Sperre nicht geprueft werden, wird NICHT abgerufen.
    assert "return None" in block[block.index("firma_gesperrt"):
                                  block.index("firma_gesperrt") + 500]
