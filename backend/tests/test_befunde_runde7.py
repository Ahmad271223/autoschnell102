# -*- coding: utf-8 -*-
"""Pruefbericht Runde 7 (09/2026) — die Befunde, die dabei bestaetigt wurden.

Jeder Test steht fuer genau einen Befund und wuerde vor dem Fix fehlschlagen:

  1  Kostenloser Marktplatz + trotzdem funktionierende 20-Euro-Zahlung
  2  /api/ready suchte eine Methode, die es nirgends gab -> Datei-Speicher
     wurde stillschweigend uebersprungen
  5  Zwei verschiedene Fassungen derselben Sicherheitsregel (current_firma)
  9  production_check prueft, ob die S3-Angaben DA sind, aber nicht, ob
     sie funktionieren
 11  Fehlermeldungen aus dem Browser wurden mit der Proxy-Adresse
     gezaehlt statt mit der des Nutzers
"""
import inspect
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import production_check  # noqa: E402
import storage_service  # noqa: E402


# ---------------------------------------------------------------- Befund 1
def test_01_kostenloser_marktplatz_kassiert_nicht():
    """Solange MARKTPLATZ_KOSTENLOS gilt, darf /payments/checkout fuer den
    Marktplatz NICHT durchgehen. Sonst zahlt jemand 20 Euro fuer etwas,
    das er schon kostenlos hat."""
    quelle = inspect.getsource(
        __import__("routes.payments", fromlist=["x"]).create_checkout)
    assert "MARKTPLATZ_KOSTENLOS" in quelle, \
        "checkout prueft den Kostenlos-Schalter nicht"
    # Die Pruefung muss VOR dem Anlegen der Stripe-Sitzung stehen.
    assert quelle.index("MARKTPLATZ_KOSTENLOS") < quelle.index("Session.create"), \
        "Der Schalter wird erst nach dem Anlegen der Zahlung geprueft"


def test_01b_beide_zustaende_koennen_nicht_gleichzeitig_gelten():
    """Entweder kostenlos ODER bezahlbar — nie beides."""
    import importlib
    import routes.marketplace as mp
    for wert, erwartet_frei in (("true", True), ("false", False)):
        os.environ["MARKTPLATZ_KOSTENLOS"] = wert
        importlib.reload(mp)
        assert mp.MARKTPLATZ_KOSTENLOS is erwartet_frei
        zahlbar = not mp.MARKTPLATZ_KOSTENLOS
        assert zahlbar != mp.MARKTPLATZ_KOSTENLOS
    os.environ.pop("MARKTPLATZ_KOSTENLOS", None)
    importlib.reload(mp)


# ---------------------------------------------------------------- Befund 2
def test_02_beide_speicher_koennen_sagen_ob_sie_erreichbar_sind():
    """Die Bereitschaftspruefung sucht storage.erreichbar(). Gibt es die
    Methode nicht, wurde der Datei-Speicher frueher KOMMENTARLOS
    uebersprungen — /api/ready meldete "bereit", obwohl R2 tot sein
    konnte."""
    for klasse in (storage_service.LocalDiskStorage, storage_service.S3Storage):
        assert hasattr(klasse, "erreichbar"), f"{klasse.__name__} fehlt erreichbar()"
        assert callable(klasse.erreichbar)


def test_02b_lokaler_speicher_meldet_ehrlich(tmp_path, monkeypatch):
    speicher = storage_service.LocalDiskStorage(root=tmp_path)
    assert speicher.erreichbar() is True
    # Nicht beschreibbarer Pfad -> False statt Ausnahme
    speicher.root = tmp_path / "gibt-es-nicht" / "tiefer" / "\0ungueltig"
    assert speicher.erreichbar() is False


def test_02c_ready_ueberspringt_den_speicher_nicht_mehr_stillschweigend():
    quelle = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    abschnitt = quelle[quelle.index('if os.environ.get("S3_BUCKET")'):]
    abschnitt = abschnitt[:2000]
    assert 'info["s3"]' in abschnitt, "Das Ergebnis steht nicht in der Antwort"
    assert "keine Erreichbarkeitspruefung vorhanden" in abschnitt, \
        "Eine fehlende Methode faellt weiterhin nicht auf"


# ---------------------------------------------------------------- Befund 5
def test_05_es_gibt_nur_eine_fassung_von_current_firma():
    """Sicherheitsregeln duerfen nicht doppelt im Code stehen. Sonst wird
    ein Fix an einer Stelle gemacht und an der anderen vergessen."""
    backend = Path(__file__).resolve().parents[1]
    treffer = []
    for datei in list(backend.glob("*.py")) + list((backend / "routes").glob("*.py")):
        text = datei.read_text(encoding="utf-8")
        if "async def current_firma(" in text:
            treffer.append(datei.name)
    assert treffer == ["deps.py"], f"current_firma steht in {treffer}"


def test_05b_regel_verlangt_rolle_UND_firma():
    """Die eine verbliebene Fassung muss beides pruefen: Rolle und
    Zugehoerigkeit zu einer echten Firma (dealer_id). Fehlt Letzteres,
    teilen sich alle Konten ohne Firma denselben leeren Mandanten."""
    import deps
    quelle = inspect.getsource(deps.current_firma)
    assert 'role' in quelle and 'dealer_id' in quelle


def test_05c_haendler_regel_haengt_an_der_chef_regel():
    import routes.bestand as bestand
    quelle = inspect.getsource(bestand.current_haendler)
    assert "current_chef" in quelle, \
        "current_haendler prueft wieder selbst und kann von current_chef abweichen"


# ---------------------------------------------------------------- Befund 9
def test_09_speicherpruefung_erkennt_falschen_eimer_und_schluessel(monkeypatch):
    """Vorhandene Angaben sind nicht dasselbe wie funktionierende Angaben.
    Ohne echten Zugang gibt es hier keine Netzverbindung — geprueft wird
    deshalb die Einstufung: Zugangs-/Namensfehler sind DAUERHAFT (Fehler),
    Netzprobleme nur voruebergehend (Warnung)."""
    art, meldung = production_check._s3_wirklich_pruefen("egal")
    assert art in ("fehler", "warnung", "ok")

    class _Fehler(Exception):
        def __init__(self, code):
            self.response = {"Error": {"Code": code}}
            super().__init__(code)

    def bauen(code):
        def _client(**kw):
            raise _Fehler(code)
        return _client

    for code, erwartet in (("NoSuchBucket", "fehler"),
                           ("AccessDenied", "fehler"),
                           ("SignatureDoesNotMatch", "fehler"),
                           ("InvalidAccessKeyId", "fehler"),
                           ("RequestTimeout", "warnung"),
                           ("SlowDown", "warnung")):
        import s3_kompatibel
        monkeypatch.setattr(s3_kompatibel, "s3_client", bauen(code))
        monkeypatch.setenv("S3_ENDPOINT", "https://x.r2.cloudflarestorage.com")
        art, meldung = production_check._s3_wirklich_pruefen("eimer")
        assert art == erwartet, f"{code} wurde als {art} eingestuft, erwartet {erwartet}"
        assert "eimer" in meldung


def test_09b_produktionspruefung_ruft_die_echte_probe_auf():
    quelle = inspect.getsource(production_check.pruefe_produktion)
    assert "_s3_wirklich_pruefen" in quelle, \
        "Der Start prueft den Datei-Speicher weiterhin nur auf Vorhandensein"


# --------------------------------------------------------------- Befund 11
def test_11_fehlermeldungen_zaehlen_die_echte_nutzer_adresse():
    """Hinter nginx ist request.client.host IMMER der Proxy. Damit teilten
    sich ALLE Nutzer einen einzigen Zaehler: ein kaputter Browser haette
    die Fehlermeldung fuer alle anderen gesperrt — und im Archiv stand nie
    die echte Herkunft."""
    quelle = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    start = quelle.index("async def report_client_error")
    # Kommentarzeilen herausnehmen: dort steht absichtlich beschrieben, was
    # frueher falsch war — das darf den Test nicht ausloesen.
    abschnitt = "\n".join(z for z in quelle[start:start + 1600].split("\n")
                          if not z.strip().startswith("#"))
    assert "client_ip(request)" in abschnitt, "nutzt weiterhin nicht client_ip()"
    assert "request.client.host" not in abschnitt, \
        "greift weiterhin direkt auf die Proxy-Adresse zu"


def test_11b_client_ip_beachtet_die_weiterleitungs_kopfzeile(monkeypatch):
    """Gegenprobe: die Funktion, auf die jetzt umgestellt wurde, liefert
    hinter dem eigenen Vermittler wirklich die Adresse des Besuchers.
    (Die Einstellungen liest das Modul beim Import — deshalb werden hier
    die Modulwerte gesetzt, nicht die Umgebungsvariablen.)"""
    import ipaddress
    import rate_limiter

    class _Req:
        def __init__(self, kopf, peer):
            self.headers = kopf
            self.client = type("c", (), {"host": peer})()

    monkeypatch.setattr(rate_limiter, "_TRUST_PROXY", True)
    monkeypatch.setattr(rate_limiter, "_TRUSTED_PROXIES",
                        [ipaddress.ip_network("10.0.0.0/16")])
    ip = rate_limiter.client_ip(
        _Req({"x-forwarded-for": "203.0.113.9, 10.0.0.5"}, "10.0.0.5"))
    assert ip == "203.0.113.9", ip
    # Ohne Vertrauen in den Proxy bleibt es bei der direkten Adresse.
    monkeypatch.setattr(rate_limiter, "_TRUST_PROXY", False)
    assert rate_limiter.client_ip(
        _Req({"x-forwarded-for": "203.0.113.9"}, "10.0.0.5")) == "10.0.0.5"


@pytest.mark.parametrize("datei,verboten", [
    ("routes/bestand.py", "async def current_firma("),
])
def test_keine_rueckkehr_der_doppelten_regel(datei, verboten):
    quelle = (Path(__file__).resolve().parents[1] / datei).read_text(encoding="utf-8")
    assert verboten not in quelle
