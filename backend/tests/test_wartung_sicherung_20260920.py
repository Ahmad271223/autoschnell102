# -*- coding: utf-8 -*-
"""Nachpruefung 20.09.2026, Nr. 64-72 — Wartungsmodus und Datei-Sicherung.

Die Runde davor hatte ich die Schreibpause automatisch eingeschaltet, sobald
kein Replica Set laeuft. Das war falsch, und die Durchsicht hat genau
gesagt, warum. Diese Tests halten die Korrekturen fest:

  Nr. 64  Der Schreibstopp galt nur fuer die HTTP-Middleware; die Worker
          schrieben weiter, die Sicherung nannte sich trotzdem
          stichtagsgenau.
  Nr. 65  Nach dem Setzen wurde stur 6 s gewartet.
  Nr. 66  Kein Besitzer, keine Ablaufzeit, kein Waechter: ein abgestuerztes
          Skript konnte die Plattform dauerhaft sperren.
  Nr. 67  /api/ready blieb gruen, der Lastverteiler schickte weiter Kunden
          auf eine Instanz, die alles mit 503 beantwortete.
  Nr. 68  Die "Schreibpause" war ein kompletter Ausfall — auch fuer Lesen.
  Nr. 69  Datenbank und Dateien stammten aus verschiedenen Zeitpunkten.
  Nr. 70  Die Datei-Sicherung hielt nicht fest, WELCHE Dateien zu einem
          Backup gehoerten.
  Nr. 71  Papierkorb-Fehler wurden als Erfolg verbucht.
  Nr. 72  Compose, Env-Generator und Dokumentation nannten verschiedene
          Standardwerte.
"""
import gzip
import inspect
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import wartung as W  # noqa: E402


def _jetzt():
    return datetime.now(timezone.utc)


def _merker(aktiv=True, umfang=W.UMFANG_SCHREIBEN, minuten=10, besitzer="abc"):
    return {"_id": W.FLAG_ID, "aktiv": aktiv, "umfang": umfang,
            "besitzer": besitzer, "grund": "Test",
            "gilt_bis": (_jetzt() + timedelta(minutes=minuten)).isoformat()}


# ------------------------------------------------------------------ Nr. 68
def test_68_schreibpause_laesst_lesen_durch():
    m = _merker(umfang=W.UMFANG_SCHREIBEN)
    for methode in ("GET", "HEAD", "OPTIONS"):
        assert W.pausiert(m, methode) is False, methode
    for methode in ("POST", "PUT", "PATCH", "DELETE"):
        assert W.pausiert(m, methode) is True, methode


def test_68b_restore_sperrt_weiterhin_alles():
    m = _merker(umfang=W.UMFANG_ALLES)
    for methode in ("GET", "POST", "DELETE"):
        assert W.pausiert(m, methode) is True, methode


def test_68c_schreibpause_ist_nicht_mehr_automatisch():
    """Der Kern meines Fehlers: ohne Replica Set lief das taeglich."""
    import backup_service as BS
    q = inspect.getsource(BS._schreibpause_noetig)
    assert "snapshot_pflicht" not in q, \
        "die Automatik ohne Replica Set ist ausdruecklich zurueckgenommen"
    assert "BACKUP_WARTUNG" in q


@pytest.mark.parametrize("url,schalter,erwartet", [
    ("mongodb://a/?replicaSet=rs0", "", False),
    ("mongodb://a/", "", False),          # <- frueher True: taeglicher Ausfall
    ("mongodb://a/", "false", False),
    ("mongodb://a/", "true", True),       # nur auf ausdrueckliche Anweisung
])
def test_68d_schreibpause_nur_auf_anweisung(monkeypatch, url, schalter, erwartet):
    import backup_service as BS
    monkeypatch.setenv("MONGO_URL", url)
    monkeypatch.setenv("BACKUP_WARTUNG", schalter)
    monkeypatch.delenv("BACKUP_SNAPSHOT_PFLICHT", raising=False)
    assert BS._schreibpause_noetig() is erwartet


# ------------------------------------------------------------------ Nr. 66
def test_66_abgelaufener_merker_sperrt_nicht_mehr():
    alt = _merker(minuten=-1)
    assert W.abgelaufen(alt) is True
    assert W.pausiert(alt, "POST") is False, \
        "ein abgestuerztes Skript darf die Plattform nicht dauerhaft sperren"


def test_66b_merker_ohne_frist_bleibt_wirksam():
    """Restore setzt bewusst keine Frist — der Ausfall soll bleiben,
    bis jemand nachgesehen hat."""
    m = {"aktiv": True, "umfang": W.UMFANG_ALLES, "grund": "Restore"}
    assert W.abgelaufen(m) is False
    assert W.pausiert(m, "GET") is True


def test_66c_setzen_verlaengern_aufheben(monkeypatch):
    class _Coll:
        def __init__(self):
            self.doc = None

        def replace_one(self, f, d, upsert=False):
            self.doc = dict(d)
            return type("R", (), {"matched_count": 1})()

        def update_one(self, f, u):
            passt = self.doc is not None and all(
                k == "_id" or self.doc.get(k) == v for k, v in f.items())
            if passt:
                self.doc.update(u["$set"])
            return type("R", (), {"matched_count": 1 if passt else 0})()

    monkeypatch.setenv("WARTUNG_FRIST_MIN", "5")
    c = _Coll()
    kennung = W.setzen(c, "Datensicherung laeuft")
    assert c.doc["aktiv"] is True and c.doc["besitzer"] == kennung
    assert c.doc["umfang"] == W.UMFANG_SCHREIBEN
    assert W._zeit(c.doc["gilt_bis"]) > _jetzt(), "Ablaufzeit gesetzt (Nr. 66)"

    assert W.verlaengern(c, kennung) is True
    assert W.verlaengern(c, "jemand-anders") is False, "nur der Besitzer"
    assert W.aufheben(c, "jemand-anders") is False, "nur der Besitzer"
    assert W.aufheben(c, kennung) is True
    assert c.doc["aktiv"] is False


def test_66d_waechter_beim_start_raeumt_nur_abgelaufene_auf():
    import asyncio

    class _Coll:
        def __init__(self, doc):
            self.doc = doc
            self.geschrieben = False

        async def find_one(self, _f):
            return self.doc

        async def update_one(self, _f, u):
            self.geschrieben = True
            self.doc.update(u["$set"])

    class _DB:
        def __init__(self, coll):
            self._c = coll

        def __getitem__(self, _n):
            return self._c

    abgelaufen = _Coll(_merker(minuten=-5))
    assert asyncio.run(W.abgelaufenen_merker_aufraeumen(_DB(abgelaufen))) is True
    assert abgelaufen.doc["aktiv"] is False

    laufend = _Coll(_merker(minuten=10))
    assert asyncio.run(W.abgelaufenen_merker_aufraeumen(_DB(laufend))) is False
    assert laufend.geschrieben is False

    ohne_frist = _Coll({"aktiv": True, "umfang": W.UMFANG_ALLES})
    assert asyncio.run(W.abgelaufenen_merker_aufraeumen(_DB(ohne_frist))) is False


def test_66e_server_startet_den_waechter():
    """Quelltext von der Platte statt inspect.getsource(server.on_start):
    andere Tests der Suite ersetzen `server` bzw. seine Funktionen zeitweise,
    und dann liest inspect etwas anderes. Die Zusage bleibt dieselbe."""
    quelle = (BACKEND / "server.py").read_text(encoding="utf-8")
    block = quelle[quelle.index("async def on_start("):]
    ende = block.find("\nasync def on_stop(")
    if ende > 0:
        block = block[:ende]
    assert "abgelaufenen_merker_aufraeumen" in block, \
        "der Waechter muss beim Start laufen (Nr. 66)"


def test_66f_sicherung_verlaengert_und_raeumt_auf():
    import backup_mongo as B
    q = inspect.getsource(B.Schreibpause)
    assert "wartung.verlaengern" in q, "lange Laeufe verlaengern die Frist"
    assert "wartung.aufheben" in q
    assert "threading.Thread" in q
    # Und ein Fehler beim Ausschalten ist kein stiller Erfolg mehr:
    assert "laeuft" in inspect.getsource(B.Schreibpause.ausschalten)


# ------------------------------------------------------------------ Nr. 67
def test_67_bereitschaft_meldet_den_wartungsmodus():
    import server
    q = inspect.getsource(server)
    stelle = q.split("wm = await wartung.lesen_async(db)")[1][:1200]
    assert "fehler.append" in stelle, \
        "ein Restore muss die Instanz aus dem Lastverteiler nehmen (Nr. 67)"
    assert "warnungen.append" in stelle, \
        "eine reine Schreibpause bleibt eine Warnung — Lesen geht ja weiter"
    assert "schreiber_offen" in stelle, "Nr. 65: laufende Schreiber sichtbar"


def test_67b_middleware_zaehlt_offene_schreiber():
    import server
    q = inspect.getsource(server.WartungsmodusMiddleware)
    assert "_offene_schreiber += 1" in q and "_offene_schreiber -= 1" in q
    assert "wartung.pausiert(doc, request.method)" in q, \
        "die Methode entscheidet — nicht mehr pauschal alles (Nr. 68)"


# ------------------------------------------------------------------ Nr. 64
@pytest.mark.parametrize("modul,funktion", [
    ("link_jobs", "run_job_worker_forever"),
    ("beweis_service", "run_beweis_worker_forever"),
    ("cleanup_service", "run_cleanup_forever"),
])
def test_64_worker_halten_bei_schreibpause_an(modul, funktion):
    import importlib
    m = importlib.import_module(modul)
    q = inspect.getsource(getattr(m, funktion))
    assert "wartung.aktiv_async(db)" in q, (
        f"{modul}.{funktion} schrieb waehrend der Schreibpause weiter — "
        f"dann ist die Sicherung nicht stichtagsgenau (Nr. 64)")


def test_64b_stoerung_haelt_die_plattform_nicht_an():
    """Eine kaputte Abfrage darf nicht wie eine Pause wirken."""
    import asyncio

    class _DB:
        def __getitem__(self, _n):
            class _C:
                @staticmethod
                async def find_one(_f):
                    raise RuntimeError("DB weg")
            return _C()

    assert asyncio.run(W.aktiv_async(_DB())) is False


# ------------------------------------------------------------------ Nr. 65
def test_65_wartezeit_ist_laenger_und_einstellbar(monkeypatch):
    import backup_mongo as B
    monkeypatch.delenv("BACKUP_WARTUNG_WARTEN_S", raising=False)
    assert B._wartung_warten_s() == 30.0, "6 s waren zu kurz (Nr. 65)"
    monkeypatch.setenv("BACKUP_WARTUNG_WARTEN_S", "45")
    assert B._wartung_warten_s() == 45.0
    monkeypatch.setenv("BACKUP_WARTUNG_WARTEN_S", "1")
    assert B._wartung_warten_s() == 6.0, "unter die Cache-Zeit geht es nicht"
    monkeypatch.setenv("BACKUP_WARTUNG_WARTEN_S", "quatsch")
    assert B._wartung_warten_s() == 30.0


# ------------------------------------------------------------------ Nr. 69
def test_69_pause_umfasst_auch_die_dateien():
    import backup_mongo as B
    q = inspect.getsource(B.backup_erstellen)
    db_ende = q.index("Konsistenz:")
    datei_teil = q.index("---- Datei-Speicher ----")
    aus = q.index("schreibpause.ausschalten()\n", datei_teil)
    assert db_ende < datei_teil < aus, (
        "die Schreibpause endete direkt nach dem Datenbank-Dump — eine danach "
        "geloeschte Datei war in der DB-Sicherung noch verzeichnet (Nr. 69)")


# ------------------------------------------------------------------ Nr. 70
def test_70_objektliste_je_backup(tmp_path):
    import backup_mongo as B
    q = inspect.getsource(B.dateien_in_bucket_sichern)
    assert 'stand["liste"]' in q and "etag" in q, \
        "ohne Objektliste laesst sich kein Stand beweisen (Nr. 70)"
    assert B.DATEIEN_LISTE.endswith(".json.gz")
    assert B.MANIFEST_VERSION >= 5

    # Die Liste wird gelesen und verglichen.
    import dateien_zurueckkopieren as D
    (tmp_path / B.DATEIEN_LISTE).write_bytes(gzip.compress(json.dumps(
        {"a.jpg": {"bytes": 10, "etag": "x"},
         "b.jpg": {"bytes": 20, "etag": "y"}}).encode("utf-8")))
    liste = D.liste_lesen(str(tmp_path))
    ergebnis = D.gegen_liste_pruefen(
        liste, {"dateien/a.jpg": (10, None),      # unveraendert
                "dateien/b.jpg": (99, None),      # andere Groesse
                "dateien/c.jpg": (5, None)},      # spaeter dazugekommen
        "dateien/")
    assert ergebnis["soll"] == 2
    assert ergebnis["abweichend"] == ["b.jpg"]
    assert ergebnis["fehlend"] == []
    assert ergebnis["zusaetzlich"] == 1

    ergebnis2 = D.gegen_liste_pruefen(liste, {"dateien/a.jpg": (10, None)},
                                      "dateien/")
    assert ergebnis2["fehlend"] == ["b.jpg"]


def test_70b_fehlende_liste_sagt_es_deutlich(tmp_path):
    import dateien_zurueckkopieren as D
    with pytest.raises(SystemExit) as exc:
        D.liste_lesen(str(tmp_path))
    assert "Objektliste" in str(exc.value)


# ------------------------------------------------------------------ Nr. 71
def test_71_papierkorbfehler_zaehlen_als_fehler():
    import backup_mongo as B
    q = inspect.getsource(B.dateien_in_bucket_sichern)
    teil = q.split("# Papierkorb:")[1]
    assert 'stand["fehler"] += 1' in teil, (
        "Fehler an der Loeschfrist wurden nur geloggt, das Backup meldete "
        "trotzdem OK (Nr. 71)")
    assert "papierkorb_fehler" in teil


# ------------------------------------------------------------------ Nr. 72
def test_72_standardwerte_stimmen_ueberein():
    """Compose, Env-Generator, .env.example und Code nennen DENSELBEN Wert."""
    import re
    wurzel = BACKEND.parent
    compose = (wurzel / "docker-compose.yml").read_text(encoding="utf-8")
    beispiel = (wurzel / ".env.example").read_text(encoding="utf-8")
    generator = (BACKEND / "scripts" / "env_erzeugen.py").read_text(encoding="utf-8")

    def compose_standard(name):
        m = re.search(r"\$\{" + name + r":-([^}]*)\}", compose)
        return m.group(1) if m else None

    def beispiel_wert(name):
        m = re.search(r"^" + name + r"=(.*)$", beispiel, re.M)
        return m.group(1).strip() if m else None

    def generator_wert(name):
        m = re.search(r'"' + name + r'=([^"]*)"', generator)
        return m.group(1) if m else None

    for name in ("BACKUP_S3_KEEP", "LINK_JOB_SOFORT_MAX", "MAX_IMAGE_UPLOAD_BYTES"):
        c, b = compose_standard(name), beispiel_wert(name)
        assert c is not None, f"{name} fehlt in docker-compose.yml"
        assert b is not None, f"{name} fehlt in .env.example"
        assert c == b, f"{name}: Compose {c} != .env.example {b} (Nr. 72)"
        g = generator_wert(name)
        if g is not None:
            assert g == c, f"{name}: Env-Generator {g} != Compose {c} (Nr. 72)"

    import link_jobs
    import storage_service as st
    assert str(link_jobs.SOFORT_MAX) == compose_standard("LINK_JOB_SOFORT_MAX")
    assert str(st.MAX_IMAGE_BYTES) == compose_standard("MAX_IMAGE_UPLOAD_BYTES")
    assert compose_standard("BACKUP_S3_KEEP") == "14", \
        "Code, .env.example und DEPLOYMENT.md sagen 14"


def test_72b_nginx_begrenzt_die_anfragegroesse():
    """Gegenprobe zu Befund Nr. 51 — der stimmte NICHT.

    Die Durchsicht sah nur die Server-Bloecke (default.conf.template /
    hinter-loadbalancer.conf.template) und schloss daraus auf nginx'
    Standard von 1 MB. Die Vorgabe steht aber im http-Block von
    deploy/nginx.conf, und GENAU DIESE Datei wird in den Container
    gehaengt — nginx vererbt sie an jeden Server- und Location-Block.
    Dieser Test haelt beides fest, damit die Vorgabe nicht verschwindet."""
    wurzel = BACKEND.parent
    conf = (wurzel / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    assert "client_max_body_size 25m;" in conf
    assert conf.index("http {") < conf.index("client_max_body_size")
    compose = (wurzel / "docker-compose.yml").read_text(encoding="utf-8")
    assert "./deploy/nginx.conf:/etc/nginx/nginx.conf:ro" in compose


def test_66g_betriebsseite_zeigt_abgelaufene_wartung_nicht_mehr_an():
    """Dieselbe Regel ueberall: Middleware, /api/ready UND die Betriebsseite
    des Betreibers. Sonst stuende dort weiter "Wartung laeuft", obwohl die
    Plattform nach einem abgestuerzten Sicherungslauf laengst frei ist."""
    import routes.admin as ADMIN
    q = inspect.getsource(ADMIN)
    stelle = q.split('"wartungsmodus":')[1][:200]
    assert "wartung.pausiert(" in stelle, \
        "die Betriebsseite darf nicht selbst nur .get('aktiv') lesen (Nr. 66)"
