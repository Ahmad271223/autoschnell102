# -*- coding: utf-8 -*-
"""Vertrags-E-Mail (Wunsch 09/2026).

Geprueft wird ohne echten Versand:
  * Absender ist IMMER unsere eigene Adresse; der Anzeigename nennt die Firma
  * Antworten gehen an den Sucher (Reply-To)
  * die Kopie an den Sucher laeuft als stille Kopie bzw. eigene Mail
  * die Vorlage enthaelt Fahrzeug, Preis, Vertragsnummer und Anhang-Hinweis
  * Text- und HTML-Fassung sind beide vorhanden und sauber
"""
import asyncio
import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import email_service  # noqa: E402
import vertrag_mail  # noqa: E402

VERTRAG = {
    "make": "Volkswagen", "model": "Golf VII 1.6 TDI",
    "contract_no": "KV-20260904-A1B2C3", "purchase_price": 8490.0,
    "seller_name": "Sabine Verkauf", "pickup_date": "2026-10-01",
    "pickup_time": "09:30",
    "contract_data": {"first_registration": "08/2015", "mileage": 145880,
                      "vin": "WVWZZZ1KZAW000001"},
}
FIRMA = {"company_name": "Autohaus Muster", "phone": "0511 123456", "logo_url": ""}
SUCHER = {"first_name": "Max", "last_name": "Sucher",
          "email": "max@autohaus-muster.de", "phone": "0511 123457"}


# ------------------------------------------------------------- Vorlage
def test_vertrag_mail_enthaelt_alles_wichtige():
    betreff, text, html = vertrag_mail.vertrag_mail(
        vertrag=VERTRAG, firma=FIRMA, sucher=SUCHER,
        nachricht="Hallo Frau Verkauf,\nwie besprochen anbei der Vertrag.",
        betreff=None)
    assert "Volkswagen Golf VII 1.6 TDI" in betreff
    for erwartet in ("Sabine Verkauf", "8.490,00 €", "KV-20260904-A1B2C3",
                     "145.880 km", "WVWZZZ1KZAW000001", "01.10.2026",
                     "wie besprochen"):
        assert erwartet in text, f"fehlt im Text: {erwartet}"
        assert erwartet in html, f"fehlt im HTML: {erwartet}"
    # Antwort-Hinweis nennt den Sucher
    assert "Max Sucher" in text and "max@autohaus-muster.de" in text
    # E-Mail-tauglich: keine Skripte, kein externes CSS, Tabellenlayout
    assert "<script" not in html.lower() and "<link" not in html.lower()
    assert "<style" not in html.lower() and html.count("<table") >= 3
    assert "max-width:600px" in html
    # Zeilenumbrueche der Nachricht bleiben erhalten
    assert "<br>" in html


def test_vertrag_mail_ohne_nachricht_und_ohne_daten():
    mager = {"make": "", "model": "", "contract_data": {}}
    betreff, text, html = vertrag_mail.vertrag_mail(
        vertrag=mager, firma={}, sucher={"email": "a@b.de"},
        nachricht="", betreff="Eigener Betreff")
    assert betreff == "Eigener Betreff"
    assert "Kaufvertrag" in text and "<table" in html
    assert "Hallo," in text          # ohne Namen keine kaputte Anrede


def test_freitext_wird_nicht_als_html_ausgefuehrt():
    _, _, html = vertrag_mail.vertrag_mail(
        vertrag=VERTRAG, firma=FIRMA, sucher=SUCHER,
        nachricht="<img src=x onerror=alert(1)>", betreff=None)
    assert "<img src=x" not in html
    assert "&lt;img" in html


def test_kopie_mail_nennt_empfaenger_und_zeitpunkt():
    betreff, text, html = vertrag_mail.kopie_mail(
        vertrag=VERTRAG, firma=FIRMA, sucher=SUCHER,
        empfaenger_adresse="sabine@example.com",
        betreff_original="Ihr Kaufvertrag – Volkswagen Golf",
        nachricht="wie besprochen")
    assert betreff.startswith("Kopie:") and "Sabine Verkauf" in betreff
    assert "sabine@example.com" in text and "sabine@example.com" in html
    assert "KV-20260904-A1B2C3" in text
    assert "wie besprochen" in html


# ------------------------------------------------------------- Versand
class _Antwort:
    status_code = 200

    @staticmethod
    def json():
        return {"id": "mail_1"}
    text = "{}"


@pytest.fixture
def resend(monkeypatch):
    """Resend 'eingerichtet', Aufruf wird abgefangen statt gesendet."""
    gesendet = {}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            gesendet["url"] = url
            gesendet["daten"] = json
            gesendet["headers"] = headers
            return _Antwort()
    import httpx
    monkeypatch.setattr(email_service, "RESEND_API_KEY", "re_test_123", raising=False)
    monkeypatch.setattr(email_service, "MAIL_FROM",
                        "AutoSchnell <vertrag@autoschnell.de>", raising=False)
    # Unabhaengig von der .env dieses Rechners pruefen
    monkeypatch.setattr(email_service, "MAIL_ABSENDER_NAME", "AutoSchnell",
                        raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return gesendet


def test_absender_ist_immer_unsere_adresse(resend):
    ok = asyncio.run(email_service.send_email(
        "kunde@example.com", "Betreff", "Text",
        anhang=b"%PDF-1.4 test", anhang_name="Kaufvertrag.pdf",
        html="<p>Hallo</p>", reply_to="max@autohaus-muster.de",
        kopie="max@autohaus-muster.de", absender_name="Autohaus Muster"))
    assert ok
    d = resend["daten"]
    # Adresse bleibt unsere, Firma steht nur im Anzeigenamen
    assert d["from"] == "Autohaus Muster über AutoSchnell <vertrag@autoschnell.de>"
    assert d["to"] == ["kunde@example.com"]
    # Antwort geht an den Sucher
    assert d["reply_to"] == ["max@autohaus-muster.de"]
    # Kopie an den Sucher
    assert d["bcc"] == ["max@autohaus-muster.de"]
    assert d["subject"] == "Betreff" and d["text"] == "Text"
    assert d["html"] == "<p>Hallo</p>"
    anhang = d["attachments"][0]
    assert anhang["filename"] == "Kaufvertrag.pdf"
    assert base64.b64decode(anhang["content"]) == b"%PDF-1.4 test"
    assert resend["headers"]["Authorization"] == "Bearer re_test_123"


def test_ohne_firmenname_nur_marke(resend):
    asyncio.run(email_service.send_email("kunde@example.com", "B", "T"))
    assert resend["daten"]["from"] == "AutoSchnell <vertrag@autoschnell.de>"
    assert "reply_to" not in resend["daten"] and "bcc" not in resend["daten"]


def test_ungueltige_antwort_und_kopieadressen_werden_verworfen(resend):
    asyncio.run(email_service.send_email(
        "kunde@example.com", "B", "T",
        reply_to="kein-email", kopie=["kunde@example.com", "  ", "ok@example.com"]))
    d = resend["daten"]
    assert "reply_to" not in d                    # ungueltig -> weggelassen
    assert d["bcc"] == ["ok@example.com"]          # Empfaenger nicht doppelt


def test_ohne_konfiguration_kein_versand(monkeypatch):
    monkeypatch.setattr(email_service, "RESEND_API_KEY", "", raising=False)
    monkeypatch.setattr(email_service, "SMTP_HOST", "", raising=False)
    monkeypatch.setattr(email_service, "SMTP_USER", "", raising=False)
    monkeypatch.setattr(email_service, "SMTP_PASS", "", raising=False)
    assert email_service.email_configured() is False
    assert asyncio.run(email_service.send_email("a@b.de", "B", "T")) is False


def test_resend_fehler_wird_gemeldet(monkeypatch):
    class _Fehler(_Antwort):
        status_code = 422
        text = '{"message":"Domain not verified"}'

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return _Fehler()
    import httpx
    monkeypatch.setattr(email_service, "RESEND_API_KEY", "re_test_123", raising=False)
    monkeypatch.setattr(email_service, "MAIL_FROM", "AutoSchnell <v@x.de>", raising=False)
    monkeypatch.setattr(email_service, "MAIL_ABSENDER_NAME", "AutoSchnell", raising=False)
    monkeypatch.setattr(email_service, "SMTP_HOST", "", raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    assert asyncio.run(email_service.send_email("a@b.de", "B", "T")) is False


# ------------------------------------------------- Ladereihenfolge (.env)
def test_einstellungen_kommen_aus_der_env_auch_ohne_vorlauf(tmp_path):
    """email_service wird in einem FRISCHEN Prozess importiert — ohne deps,
    ohne auth, ohne gesetzte Umgebungsvariablen.

    Das Modul liest seine Einstellungen beim Import. Laedt es die .env nicht
    selbst, waeren sie leer und der Versand gaelte faelschlich als nicht
    eingerichtet (Befund 09/2026). Der Test legt dafuer notfalls eine
    Wegwerf-.env an und entfernt sie wieder; eine VORHANDENE .env wird nur
    gelesen, nie veraendert."""
    import json
    import os
    import subprocess
    import sys

    backend = Path(__file__).resolve().parents[1]
    env_datei = backend / ".env"
    eigene = not env_datei.exists()
    if eigene:
        env_datei.write_text(
            chr(10).join([
                "RESEND_API_KEY=re_probe_ladereihenfolge",
                "MAIL_FROM=Probe <probe@example.invalid>",
                "MAIL_ABSENDER_NAME=Probe Marke",
            ]) + chr(10), encoding="utf-8")
    try:
        werte = {}
        for zeile in env_datei.read_text(encoding="utf-8").splitlines():
            if "=" in zeile and not zeile.strip().startswith("#"):
                k, _, v = zeile.partition("=")
                werte.setdefault(k.strip(), v.strip())
        if not (werte.get("RESEND_API_KEY") or werte.get("SMTP_HOST")):
            pytest.skip("keine Mail-Einstellungen in backend/.env hinterlegt")

        umgebung = {k: v for k, v in os.environ.items()
                    if not k.startswith(("RESEND_", "MAIL_", "SMTP_"))}
        ergebnis = subprocess.run(
            [sys.executable, "-c",
             "import json, email_service as e;"
             "print(json.dumps({'konfiguriert': e.email_configured(),"
             " 'resend': e.resend_aktiv(), 'adresse': e.absender_adresse(),"
             " 'name': e.MAIL_ABSENDER_NAME}))"],
            cwd=str(backend), env=umgebung, capture_output=True, text=True, timeout=120)
        assert ergebnis.returncode == 0, ergebnis.stderr[-500:]
        d = json.loads(ergebnis.stdout.strip().splitlines()[-1])
        assert d["konfiguriert"] is True, d
        if werte.get("RESEND_API_KEY"):
            assert d["resend"] is True, d
        assert "@" in d["adresse"], d
        if werte.get("MAIL_ABSENDER_NAME"):
            assert d["name"] == werte["MAIL_ABSENDER_NAME"], d
    finally:
        if eigene:
            env_datei.unlink(missing_ok=True)
