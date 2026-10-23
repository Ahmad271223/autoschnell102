# -*- coding: utf-8 -*-
"""Nachpruefung 20.09.2026, Nr. 24-29 und 45/46 — Inbetriebnahme.

Alles Fehler, die erst NACH dem Start auffallen — im schlechtesten Fall
beim Kunden:

  Nr. 24/25  Der .env-Generator schrieb Platzhalter ("BITTE-AUSFUELLEN-..."),
             und die Produktionspruefung fragte nur "ist ein Wert da?".
             E-Mail und Fahrzeugsuche galten damit als eingerichtet.
  Nr. 26     `.lstrip("https://")` entfernte EINZELZEICHEN, nicht das
             Schema — aus "shop.example.de" wurde "op.example.de".
  Nr. 27     Der Generator setzte die Load-Balancer-Vorlage als Standard.
             Auf einem Einzelserver antwortet nginx damit mit 444.
  Nr. 28     MONGO_EXTRA_ARGS stand nur als Kommentar in der Compose-Datei,
             obwohl das Handbuch rs.initiate() verlangt.
  Nr. 29     Der Rollout prueft /api/ready — das beweist nur, dass die
             Datenbank erreichbar ist, nicht dass repliziert wird.
  Nr. 45/46  Halb gesetzte Sicherungs-Zugangsdaten mischten zwei Zugaenge.
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
WURZEL = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))


# ------------------------------------------------------------ Nr. 24/25
@pytest.mark.parametrize("wert,erwartet", [
    ("BITTE-AUSFUELLEN-re_...", True),
    ("BITTE-AUSFUELLEN-oder-leer-lassen", True),
    ("bitte-ausfuellen-klein", True),          # Gross/Klein egal
    ("CHANGEME", True),
    ("re_abc123echterSchluessel", False),
    # Bewusst NICHT erkannt: zu unscharfe Marken wuerden ein zufaellig
    # erzeugtes Geheimnis treffen und den Produktionsstart blockieren.
    ("aXXXXbase64zufall", False),
    ("DEIN-eigener-wert", False),
    ("", False),                               # leer ist ein eigener Fall
    ("   ", False),
])
def test_24_platzhalter_werden_erkannt(wert, erwartet):
    import production_check as PC
    assert PC.ist_platzhalter(wert) is erwartet


def test_24b_platzhalter_bricht_den_produktionsstart_ab(monkeypatch):
    import production_check as PC
    meldungen = []

    class _Log:
        def error(self, s, *a):
            meldungen.append(("error", s % a if a else s))

        def warning(self, s, *a):
            meldungen.append(("warn", s % a if a else s))

        def info(self, s, *a):
            meldungen.append(("info", s % a if a else s))

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("RESEND_API_KEY", "BITTE-AUSFUELLEN-re_...")
    monkeypatch.setenv("APIFY_TOKEN", "BITTE-AUSFUELLEN-oder-leer-lassen")
    with pytest.raises(SystemExit):
        PC.pruefe_produktion(_Log())
    text = " ".join(t for _, t in meldungen)
    assert "RESEND_API_KEY" in text and "APIFY_TOKEN" in text
    assert "Vorlage" in text


def test_25_echter_wert_loest_nichts_aus(monkeypatch):
    import production_check as PC
    monkeypatch.setenv("RESEND_API_KEY", "re_echt_123")
    offen = [n for n, w in __import__("os").environ.items()
             if PC.ist_platzhalter(w)]
    assert "RESEND_API_KEY" not in offen


# ------------------------------------------------------------ Nr. 26
@pytest.mark.parametrize("roh,erwartet", [
    ("https://app.auto-schnellkauf.de", "app.auto-schnellkauf.de"),
    ("http://app.auto-schnellkauf.de/", "app.auto-schnellkauf.de"),
    ("app.auto-schnellkauf.de", "app.auto-schnellkauf.de"),
    # Genau die Faelle, die lstrip() zerstoert hat:
    ("shop.example.de", "shop.example.de"),
    ("test.example.de", "test.example.de"),
    ("https.example.de", "https.example.de"),
    ("http://shop.example.de/pfad", "shop.example.de"),
    ("  HTTPS://Shop.Example.DE  ", "shop.example.de"),
])
def test_26_domain_wird_richtig_gesaeubert(roh, erwartet):
    import env_erzeugen as E
    assert E._domain_saeubern(roh) == erwartet


def test_26b_lstrip_ist_wirklich_weg():
    """Nur ausgefuehrter Code zaehlt — im Kommentar steht der alte Ausdruck
    absichtlich noch, damit der Fehler nicht wiederkommt."""
    import ast
    import inspect

    import env_erzeugen as E
    baum = ast.parse(inspect.getsource(E))
    aufrufe = [k.func.attr for k in ast.walk(baum)
               if isinstance(k, ast.Call) and isinstance(k.func, ast.Attribute)]
    assert "lstrip" not in aufrufe, \
        "lstrip entfernt Einzelzeichen, kein Praefix (Nr. 26)"


# ------------------------------------------------------------ Nr. 27
def test_27_einzelserver_ist_der_standard():
    import env_erzeugen as E
    assert E._proxy_vorlage(False) == "default.conf.template"
    assert E._proxy_vorlage(True) == "hinter-loadbalancer.conf.template"


def test_27b_die_wahl_ist_ein_bewusster_schalter():
    import inspect

    import env_erzeugen as E
    q = inspect.getsource(E.main)
    assert "--hinter-loadbalancer" in q, \
        "die Betriebsart muss angegeben werden, nicht stillschweigend gesetzt"


def test_27c_die_lb_vorlage_verwirft_wirklich_direktzugriffe():
    """Gegenprobe: der Befund stimmt nur, wenn die Vorlage das auch tut."""
    text = (WURZEL / "deploy" / "hinter-loadbalancer.conf.template").read_text(
        encoding="utf-8")
    assert "444" in text and "direktzugriff" in text.lower()


# ------------------------------------------------------------ Nr. 28
def test_28_replica_schalter_steht_in_der_vorlage_und_im_handbuch():
    beispiel = (WURZEL / ".env.example").read_text(encoding="utf-8")
    assert "MONGO_EXTRA_ARGS=--replSet rs0" in beispiel, \
        "ohne diesen Schalter scheitert rs.initiate() (Nr. 28)"
    doku = (WURZEL / "DEPLOYMENT.md").read_text(encoding="utf-8")
    stelle = doku.split("Ein-Knoten-Replica-Set")[1][:1500]
    assert "MONGO_EXTRA_ARGS" in stelle, \
        "das Runbook verlangte rs.initiate(), nannte den Schalter aber nicht"
    assert "replikat_pruefen.py" in stelle, "Gegenprobe gehoert dazu"


# ------------------------------------------------------------ Nr. 29
def test_29_rollout_prueft_das_replikat():
    text = (WURZEL / "deploy" / "rollout.sh").read_text(encoding="utf-8")
    assert "replikat_pruefen.py" in text, \
        "/api/ready beweist nur Erreichbarkeit, nicht Replikation (Nr. 29)"
    stelle = text.split("replikat_pruefen.py")[0]
    assert "replicaSet=" in stelle.split("Nr. 29")[-1], \
        "nur pruefen, wenn ueberhaupt ein Replica Set konfiguriert ist"
    nach = text.split("replikat_pruefen.py")[1][:600]
    assert "exit 1" in nach and "Drain" in nach, \
        "ein kaputtes Replikat muss den Rollout stoppen, nicht nur warnen"


# ------------------------------------------------------------ Nr. 45/46
def test_46_gemischtes_zugangspaar_wird_abgelehnt():
    from s3_kompatibel import s3_client
    with pytest.raises(ValueError) as exc:
        s3_client(access_key="nur-schluessel", secret_key=None)
    assert "Geheimnis" in str(exc.value)
    with pytest.raises(ValueError):
        s3_client(access_key=None, secret_key="nur-geheimnis")


def test_45_startcheck_meldet_halbe_sicherungs_zugangsdaten(monkeypatch):
    import production_check as PC
    meldungen = []

    class _Log:
        def error(self, s, *a):
            meldungen.append(s % a if a else s)

        def warning(self, s, *a):
            meldungen.append(s % a if a else s)

        def info(self, s, *a):
            meldungen.append(s % a if a else s)

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("BACKUP_S3_ACCESS_KEY", "abc")
    monkeypatch.delenv("BACKUP_S3_SECRET_KEY", raising=False)
    with pytest.raises(SystemExit):
        PC.pruefe_produktion(_Log())
    text = " ".join(meldungen)
    assert "BACKUP_S3_SECRET_KEY" in text and "zusammen" in text
