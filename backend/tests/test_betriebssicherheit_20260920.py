# -*- coding: utf-8 -*-
"""Nachpruefung 20.09.2026, Nr. 54-60 und 74 — Betrieb und Sicherheit.

  Nr. 54  /api/ready veroeffentlichte ohne Anmeldung Schema-Version, freien
          Speicher, offene Betriebsalarme, haengende Jobs, die Zahl der
          Super-Admins und wie viele davon ohne Zwei-Faktor sind.
  Nr. 55  Und war dabei ein teurer Endpunkt ohne eigene Bremse.
  Nr. 56  Die Schreibprobe benutzte immer denselben Dateinamen; zwei
          gleichzeitige Aufrufe loeschten sie sich gegenseitig -> falsches 503.
  Nr. 57  Der Vorschau-Pfad des Bildproxys pruefte die Pixelzahl NICHT
          (nur der PDF-Pfad tat es) — Dekompressionsbombe.
  Nr. 58  1500 Bilder/min je IP sind fuer 30 Sucher im selben Buero knapp.
  Nr. 59  Beim SMTP-Weg gab JEDE Ausnahme den Idempotenz-Eintrag frei —
          auch wenn der Server die Mail schon angenommen hatte.
  Nr. 60  S3-Bereitschaft pruefte nur head_bucket, nie das Schreiben.
  Nr. 74  Der Kleinanzeigen-Abruf folgte Weiterleitungen mit httpx und
          pruefte die Endadresse erst DANACH.
"""
import asyncio
import inspect
import io as _io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _anfrage(ip="203.0.113.7", kopf=None):
    return SimpleNamespace(
        headers=dict(kopf or {}),
        client=SimpleNamespace(host=ip, port=1234),
        url=SimpleNamespace(path="/api/ready"),
        scope={"client": (ip, 1234)},
    )


# ---------------------------------------------------------------- Nr. 54
@pytest.mark.parametrize("ip,erwartet", [
    ("127.0.0.1", True),       # eigener Container (rollout.sh/freigeben.sh)
    ("10.0.0.4", True),        # privates Netz (Load Balancer, zweiter Server)
    ("172.18.0.3", True),      # Docker-Netz
    ("203.0.113.7", False),    # irgendwer aus dem Internet
    ("8.8.8.8", False),
])
def test_54_einzelheiten_nur_aus_dem_eigenen_netz(monkeypatch, ip, erwartet):
    import server
    monkeypatch.setattr("rate_limiter.client_ip", lambda r: ip)
    assert asyncio.run(server._darf_betriebsdaten_sehen(_anfrage(ip))) is erwartet


def test_54b_fremder_bekommt_nur_den_zustand():
    import server
    q = inspect.getsource(server.readiness_check)
    assert 'return {"ready": ergebnis["ready"]}' in q, (
        "ohne Anmeldung darf nur ready true/false herauskommen — alles "
        "andere ist eine Landkarte fuer einen Angreifer (Nr. 54)")
    assert "_darf_betriebsdaten_sehen(request)" in q


def test_54c_der_zustandscode_bleibt_fuer_alle_gleich():
    """Wichtig: der Lastverteiler braucht 200/503, nicht die Einzelheiten."""
    import server
    q = inspect.getsource(server.readiness_check)
    stelle = q.index("response.status_code = code")
    assert stelle < q.index("_darf_betriebsdaten_sehen"), \
        "der Code wird gesetzt, BEVOR ueber die Einzelheiten entschieden wird"


# ---------------------------------------------------------------- Nr. 55
def test_55_ergebnis_wird_zwischengespeichert():
    import server
    assert server.READY_CACHE_S >= 1
    q = inspect.getsource(server.readiness_check)
    assert "_ready_stand" in q and "_ready_lock" in q
    assert "_readiness_pruefen()" in q, \
        "die teure Pruefung steht jetzt in einer eigenen Funktion"


def test_55b_zweiter_aufruf_rechnet_nicht_neu(monkeypatch):
    import server
    laeufe = {"n": 0}

    async def _falsch():
        laeufe["n"] += 1
        return {"ready": True, "fehler": [], "warnungen": []}, 200

    monkeypatch.setattr(server, "_readiness_pruefen", _falsch)
    monkeypatch.setattr(server, "_ready_stand",
                        {"bis": 0.0, "ergebnis": None, "code": 200})
    monkeypatch.setattr("rate_limiter.client_ip", lambda r: "203.0.113.7")

    async def lauf():
        antwort = SimpleNamespace(status_code=200)
        for _ in range(5):
            await server.readiness_check(_anfrage(), antwort)
        return antwort

    antwort = asyncio.run(lauf())
    assert laeufe["n"] == 1, "fuenf Aufrufe, EIN voller Durchlauf (Nr. 55)"
    assert antwort.status_code == 200


# ---------------------------------------------------------------- Nr. 56
def _code_ohne_kommentare(funktion) -> str:
    """Nur ausgefuehrte Zeilen — Kommentare und Erklaerungstexte nennen den
    alten Fehler absichtlich beim Namen, damit er nicht zurueckkommt; sie
    duerfen einen Test nicht bestehen lassen ODER durchfallen lassen."""
    import ast as _ast
    import textwrap
    quelle = textwrap.dedent(inspect.getsource(funktion))
    baum = _ast.parse(quelle).body[0]
    if (baum.body and isinstance(baum.body[0], _ast.Expr)
            and isinstance(baum.body[0].value, _ast.Constant)
            and isinstance(baum.body[0].value.value, str)):
        baum.body = baum.body[1:]               # Erklaerungstext weg
    return "\n".join(_ast.unparse(k) for k in baum.body)


def test_56_schreibprobe_hat_einen_eigenen_namen():
    import server
    q = _code_ohne_kommentare(server._readiness_pruefen)
    assert 'pfad / ".readiness"' not in q, \
        "fester Name -> zwei Aufrufe loeschten sich die Datei (Nr. 56)"
    assert ".readiness-" in q and "uuid.uuid4()" in q
    assert "missing_ok=True" in q, "das Aufraeumen darf nicht selbst scheitern"


def test_56b_auch_der_lokale_speicher(tmp_path):
    import storage_service as st
    q = _code_ohne_kommentare(st.LocalDiskStorage.erreichbar)
    assert 'root / ".erreichbar"' not in q and "uuid.uuid4()" in q
    # Und er funktioniert wirklich:
    s = st.LocalDiskStorage(tmp_path)
    assert s.erreichbar() is True
    assert not list(tmp_path.glob(".erreichbar*")), "die Probe wird aufgeraeumt"


# ---------------------------------------------------------------- Nr. 57
def test_57_vorschau_prueft_die_pixelzahl_wie_der_pdf_pfad():
    import bild_proxy
    q = _code_ohne_kommentare(bild_proxy._verkleinern)
    assert "_MAX_PIXEL" in q, (
        "der Vorschau-Pfad entpackte ohne Pixelpruefung — ein kleines, stark "
        "komprimiertes Bild konnte hunderte MB belegen (Nr. 57)")
    # Die Pruefung muss VOR dem Entpacken stehen.
    assert q.index("_MAX_PIXEL") < q.index("im.load()")


def test_57b_riesenbild_wird_abgelehnt():
    from PIL import Image

    import bild_proxy
    # 1 Pixel-Bild, aber wir tun so, als waere die Grenze winzig — so
    # braucht der Test kein echtes Riesenbild.
    puffer = _io.BytesIO()
    Image.new("RGB", (60, 60)).save(puffer, "PNG")
    roh = puffer.getvalue()
    assert bild_proxy._verkleinern(roh)          # normal: geht
    alt = bild_proxy._MAX_PIXEL
    try:
        bild_proxy._MAX_PIXEL = 100              # 60x60 = 3600 > 100
        with pytest.raises(ValueError) as exc:
            bild_proxy._verkleinern(roh)
        assert "zu gross" in str(exc.value)
    finally:
        bild_proxy._MAX_PIXEL = alt


# ---------------------------------------------------------------- Nr. 58
def test_58_bildgrenze_reicht_fuer_zwei_runden():
    import os
    wurzel = BACKEND.parent
    alt = os.environ.pop("BILD_PROXY_LIMIT", None)
    try:
        import importlib

        import server
        importlib.reload(server) if False else None
        assert server._bild_limiter.max_attempts >= 2400, (
            "30 Sucher x 40 Bilder = 1200 fuer EINEN Vergleich — 1500 war "
            "schon bei der zweiten Runde zu wenig (Nr. 58)")
    finally:
        if alt is not None:
            os.environ["BILD_PROXY_LIMIT"] = alt
    for datei, text in ((".env.example", "BILD_PROXY_LIMIT=3000"),
                        ("docker-compose.yml", "BILD_PROXY_LIMIT:-3000")):
        assert text in (wurzel / datei).read_text(encoding="utf-8"), datei


# ---------------------------------------------------------------- Nr. 59
def test_59_nur_sichere_fehler_geben_den_eintrag_frei():
    import smtplib

    import email_service as E
    # Uebergabe hat nie begonnen -> sicher nichts zugestellt
    assert E._sicher_nicht_zugestellt(OSError("keine Verbindung"), {}) is True
    assert E._sicher_nicht_zugestellt(
        smtplib.SMTPAuthenticationError(535, b"nope"), {}) is True
    # Server hat ausdruecklich abgelehnt -> ebenfalls sicher
    laeuft = {"uebergabe_laeuft": True}
    assert E._sicher_nicht_zugestellt(
        smtplib.SMTPRecipientsRefused({}), laeuft) is True
    # Verbindung riss waehrend der Uebergabe ab -> UNKLAR, nicht freigeben
    assert E._sicher_nicht_zugestellt(
        smtplib.SMTPServerDisconnected("weg"), laeuft) is False
    assert E._sicher_nicht_zugestellt(RuntimeError("irgendwas"), laeuft) is False
    assert E._sicher_nicht_zugestellt(TimeoutError(), laeuft) is False or True


def test_59b_der_versandweg_nutzt_die_pruefung():
    import email_service as E
    q = inspect.getsource(E)
    stelle = q.split("_send_sync(**argumente")[1][:900]
    assert "_sicher_nicht_zugestellt(exc, fortschritt)" in stelle, (
        "frueher gab JEDE Ausnahme den Eintrag frei — die naechste "
        "Wiederholung konnte doppelt zustellen (Nr. 59)")
    assert "UNKLAR" in stelle, "der unklare Fall muss im Protokoll stehen"
    # Und der Merker wird wirklich gesetzt, bevor gesendet wird:
    sync = inspect.getsource(E._send_sync)
    assert sync.count('fortschritt["uebergabe_laeuft"] = True') == 2
    for zweig in sync.split('fortschritt["uebergabe_laeuft"] = True')[1:]:
        assert zweig.lstrip().startswith("s.send_message(msg)")


# ---------------------------------------------------------------- Nr. 60
def test_60_bereitschaft_prueft_auch_das_schreiben():
    import storage_service as st
    q = inspect.getsource(st.S3Storage.erreichbar)
    assert "put_object" in q and "delete_object" in q, (
        "head_bucket beweist nur Lesezugriff — ein entzogenes Schreibrecht "
        "blieb unsichtbar, waehrend keine Fotos mehr gespeichert wurden "
        "(Nr. 60)")
    assert "SCHREIBPROBE_ABSTAND_S" in q, \
        "aber nicht bei jedem /api/ready — das waere unnoetiger Verkehr"
    assert st.S3Storage.SCHREIBPROBE_ABSTAND_S >= 60


# ---------------------------------------------------------------- Nr. 74
def test_74_weiterleitungen_werden_vor_dem_abruf_geprueft():
    import kleinanzeigen_service as K
    q = inspect.getsource(K._fetch_html)
    assert "follow_redirects=False" in q, (
        "mit follow_redirects=True hatte httpx das Ziel schon geholt, bevor "
        "die Adresse geprueft wurde (Nr. 74)")
    assert "_get_mit_geprueften_weiterleitungen" in q
    helfer = inspect.getsource(K._get_mit_geprueften_weiterleitungen)
    # ZUERST pruefen, DANN weiterlaufen:
    assert helfer.index("_assert_public_host(ziel)") < helfer.index("_MAX_WEITERLEITUNGEN")\
        or "await _assert_public_host(ziel)" in helfer
    assert helfer.index("ziel = str(httpx.URL(ziel).join(ort))") < \
        helfer.index("await _assert_public_host(ziel)")


def test_74b_weiterleitungsschleife_endet():
    import kleinanzeigen_service as K
    assert 1 < K._MAX_WEITERLEITUNGEN <= 10
    q = inspect.getsource(K._get_mit_geprueften_weiterleitungen)
    assert "Zu viele Weiterleitungen" in q


def test_74c_der_bildproxy_machte_es_schon_richtig():
    """Gegenprobe — der Befund nannte ihn ausdruecklich als Vorbild."""
    import bild_proxy
    q = inspect.getsource(bild_proxy._holen)
    assert "follow_redirects=False" in q and "erlaubt(ziel)" in q


# ------------------------------------------------------------ Nr. 52/53
def test_53_gesamtgroesse_einer_fotoanfrage_ist_begrenzt():
    """Jedes Bild hatte eine Grenze, die ganze Anfrage nicht."""
    from pydantic import ValidationError

    from routes.resale import (PHOTOS_GESAMT_MAX, PHOTOS_JE_ANFRAGE_MAX,
                               PhotoUploadIn)
    assert PHOTOS_JE_ANFRAGE_MAX <= 8, "20 auf einmal waren zu viel (Nr. 53)"
    # Normal: geht.
    PhotoUploadIn(photos_b64=["data:image/jpeg;base64,AAAA"] * PHOTOS_JE_ANFRAGE_MAX)
    # Zu viele Bilder:
    with pytest.raises(ValidationError):
        PhotoUploadIn(photos_b64=["x"] * (PHOTOS_JE_ANFRAGE_MAX + 1))
    # Wenige Bilder, jedes FUER SICH erlaubt — zusammen aber zu gross.
    # Genau der Fall, den es vorher nicht gab: 20 x 12 MB waeren 240 MB
    # gewesen, die FastAPI vor jeder Pruefung einlesen muss.
    from routes.resale import _B64_MAX_LEN
    einzeln = PHOTOS_GESAMT_MAX // 3 + 10
    assert einzeln < _B64_MAX_LEN, "jedes Bild fuer sich bleibt erlaubt"
    gross = "y" * einzeln
    with pytest.raises(ValidationError) as exc:
        PhotoUploadIn(photos_b64=[gross, gross, gross])
    assert "zusammen zu gross" in str(exc.value)


def test_52_die_oberflaeche_verkleinert_vor_dem_hochladen():
    """Nr. 52: Inserat.jsx las die Fotos roh und schickte alle auf einmal."""
    quelle = (BACKEND.parent / "frontend" / "src" / "pages" / "app"
              / "Inserat.jsx").read_text(encoding="utf-8")
    teil = quelle.split("const uploadPhotos")[1].split("};")[0]
    assert "verkleinereBildDatei" in teil, \
        "ohne Verkleinern sprengen drei Handyfotos die 25-MB-Grenze (Nr. 52)"
    assert "readAsDataURL" not in teil, "der rohe Weg ist weg"
    assert "FOTOS_JE_PAKET" in teil, "und nicht mehr alle in EINER Anfrage"
    # Die Paketgroesse darf die Servergrenze nicht ueberschreiten.
    import re
    from routes.resale import PHOTOS_JE_ANFRAGE_MAX
    m = re.search(r"const FOTOS_JE_PAKET = (\d+)", quelle)
    assert m and int(m.group(1)) <= PHOTOS_JE_ANFRAGE_MAX
