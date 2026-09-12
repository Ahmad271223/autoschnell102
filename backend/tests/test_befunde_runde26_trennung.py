# -*- coding: utf-8 -*-
"""Runde 26 (12.09.2026, Vorgabe Ahmad): Jeder Sucher ist ein EIGENES Konto.

  "alle sucher innerhalb einer firma sind eigene accounts ... da darf keine
   limits vom einen auf den anderen übertragen werden"

Bisher galten mehrere Grenzen gemeinsam und bremsten Kollegen aus:
  * Login: 10 Versuche je Minute und IP — 30 Sucher im selben Buero teilen
    sich eine oeffentliche Adresse; schon richtige Anmeldungen zaehlten mit,
    und ein Ruecksetzen nach erfolgreichem Login gab es nicht.
  * Kleinanzeigen-Rueckfall: 25 Abrufe je Tag und FIRMA.
  * Bild-Proxy: 300 Bilder je Minute und IP (ein Vergleich laedt bis zu 40).
  * nginx reichte X-Forwarded-For des Besuchers ungeprueft durch, die
    IP-Sperren waren damit beeinflussbar.

In-Prozess; die Limiter werden direkt mit fremden Adressen geprueft
(localhost ist per RATE_LIMIT_EXEMPT_LOOPBACK ausgenommen), Muster wie
tests/test_whatsapp_teilen.py::test_09.
"""
import asyncio
import importlib
import inspect
import io
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WURZEL = Path(__file__).resolve().parents[2]
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _ip() -> str:
    return f"203.0.113.{uuid.uuid4().int % 250 + 1}"


def _mail() -> str:
    return f"konto-{uuid.uuid4().hex[:8]}@buero.test"


# ------------------------------------------------------------------ Login
def test_01_schluessel_trennt_konten_am_selben_anschluss():
    from rate_limiter import login_schluessel
    ip = "203.0.113.7"
    assert login_schluessel(ip, "a@x.de") != login_schluessel(ip, "b@x.de")
    assert login_schluessel(ip, "  A@X.DE ") == login_schluessel(ip, "a@x.de")
    assert login_schluessel(ip, "") == ip
    assert login_schluessel("", "a@x.de").startswith("unknown")


def test_02_fehlversuche_eines_kontos_sperren_den_kollegen_nicht():
    from rate_limiter import login_limiter, login_schluessel
    ip = _ip()
    a = login_schluessel(ip, _mail())
    b = login_schluessel(ip, _mail())

    async def lauf():
        eigene = [await login_limiter.check(a) for _ in range(11)]
        return eigene, await login_limiter.check(b)

    eigene, kollege = asyncio.run(lauf())
    assert all(eigene[:10]), eigene
    assert eigene[10] is False, "11. Versuch desselben Kontos wird gebremst"
    assert kollege is True, "Kollege am selben Anschluss darf sich anmelden"


def test_03_erfolgreiche_anmeldung_leert_den_zaehler():
    from rate_limiter import login_limiter, login_schluessel
    schluessel = login_schluessel(_ip(), _mail())

    async def lauf():
        vorher = [await login_limiter.check(schluessel) for _ in range(9)]
        await login_limiter.reset(schluessel)
        nachher = [await login_limiter.check(schluessel) for _ in range(10)]
        return vorher, nachher

    vorher, nachher = asyncio.run(lauf())
    assert all(vorher)
    assert all(nachher), "nach dem Ruecksetzen stehen wieder volle Versuche bereit"


def test_04_ip_limit_bleibt_als_netz_gegen_rateversuche():
    from rate_limiter import login_ip_limiter
    assert login_ip_limiter.max_attempts >= 60, "muss fuer ein Buero mit 30 Suchern reichen"
    assert login_ip_limiter.window_seconds == 60
    ip = _ip()

    async def lauf():
        return [await login_ip_limiter.check(ip)
                for _ in range(login_ip_limiter.max_attempts + 1)]

    erg = asyncio.run(lauf())
    assert all(erg[:-1]) and erg[-1] is False, "ueber dem IP-Limit wird gebremst"


def test_05_beide_anmeldewege_nutzen_konto_schluessel_und_ruecksetzen():
    for modul, limiter in (("routes.auth", "login_limiter"),
                           ("routes.drivers", "driver_login_limiter")):
        quelle = inspect.getsource(importlib.import_module(modul))
        assert "login_schluessel(" in quelle, modul
        assert f"{limiter}.check(schluessel)" in quelle, modul
        assert f"{limiter}.reset(schluessel)" in quelle, f"{modul}: kein Reset nach Erfolg"
        assert "login_ip_limiter.check(ip)" in quelle, modul


# ------------------------------------------------- Kleinanzeigen-Rueckfall
@pytest.fixture
def db_welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    L = importlib.import_module("routes.listings")
    alt = getattr(L, "db", None)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_r26_{uuid.uuid4().hex[:10]}"
    L.db = client[name]
    try:
        yield L, (lambda coro: loop.run_until_complete(coro))
    finally:
        loop.run_until_complete(client.drop_database(name))
        if alt is not None:
            L.db = alt
        client.close()
        loop.close()


def test_06_rueckfall_budget_gilt_je_sucher_nicht_je_firma(db_welt, monkeypatch):
    L, run = db_welt
    monkeypatch.setattr(L, "RUECKFALL_TAGESLIMIT", 2)
    firma = f"d_r26_{uuid.uuid4().hex[:8]}"
    a = {"id": f"su_a_{uuid.uuid4().hex[:6]}", "dealer_id": firma, "role": "sucher"}
    b = {"id": f"su_b_{uuid.uuid4().hex[:6]}", "dealer_id": firma, "role": "sucher"}

    async def lauf():
        erg_a = [await L._rueckfall_erlaubt(True, a) for _ in range(3)]
        erg_b = [await L._rueckfall_erlaubt(True, b) for _ in range(2)]
        return erg_a, erg_b

    erg_a, erg_b = run(lauf())
    assert erg_a == [True, True, False], erg_a
    assert erg_b == [True, True], "der Kollege hat sein eigenes Tagesbudget"


def test_07_rueckfall_schluessel_enthaelt_das_konto(db_welt, monkeypatch):
    L, run = db_welt
    monkeypatch.setattr(L, "RUECKFALL_TAGESLIMIT", 5)
    user = {"id": "su_schluessel", "dealer_id": "d_schluessel", "role": "sucher"}
    run(L._rueckfall_erlaubt(True, user))
    tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    doc = run(L.db.provider_budget.find_one({"_id": f"{tag}:rueckfall:su_schluessel"}))
    assert doc and doc["n"] == 1, "Budget haengt an der Konto-ID"
    assert run(L.db.provider_budget.find_one({"_id": f"{tag}:rueckfall:d_schluessel"})) is None


# -------------------------------------------------------- Betrieb / Proxy
def test_08_nginx_gibt_die_besucher_adresse_weiter():
    for name in ("default.conf.template", "hinter-loadbalancer.conf.template"):
        text = (WURZEL / "deploy" / name).read_text(encoding="utf-8")
        api = text.split("location /api/")[1].split("location ")[0]
        assert "proxy_set_header X-Real-IP $remote_addr;" in api, name
        assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in api, name


def test_09_bild_limit_reicht_fuer_ein_buero_mit_30_suchern():
    # server.py wird bewusst NICHT importiert (bindet den Motor-Client an den
    # Test-Loop) — der Quelltext reicht fuer diese Zusage.
    quelle = (WURZEL / "backend" / "server.py").read_text(encoding="utf-8")
    assert 'os.environ.get("BILD_PROXY_LIMIT", "1500")' in quelle
    assert "max_attempts=300" not in quelle.split("_bild_limiter")[1][:400]


# ------------------------------------------------ Fotos: kleiner speichern
def test_10_beweisdokument_holt_kleinere_fotos():
    """Wunsch Ahmad (12.09.2026): Das Beweisdokument soll unter 500 KB
    bleiben. Das erste Foto darf groesser sein (Uebersicht), die weiteren
    werden staerker verkleinert."""
    import asyncio
    import beweis_service as BS
    import bild_proxy

    gerufen = []

    async def _falsches_laden(url, kante=800, qualitaet=0):
        gerufen.append((kante, qualitaet))
        return b"jpeg"

    alt = bild_proxy.laden_fuer_pdf
    bild_proxy.laden_fuer_pdf = _falsches_laden
    try:
        asyncio.run(BS._fotos_laden([f"https://img.example/{i}" for i in range(3)]))
    finally:
        bild_proxy.laden_fuer_pdf = alt
    assert gerufen[0] == (BS._FOTO_KANTE_ERSTE, BS._FOTO_QUALITAET_ERSTE)
    assert gerufen[1] == (BS._FOTO_KANTE, BS._FOTO_QUALITAET)
    assert BS._FOTO_KANTE_ERSTE <= 1000 and BS._FOTO_KANTE <= 640
    assert BS._FOTO_QUALITAET_ERSTE <= 68 and BS._FOTO_QUALITAET <= 62


def test_11_kleinere_fotos_ergeben_ein_deutlich_kleineres_pdf():
    """Messung mit rauschigen Fotos (unguenstigster Fall): mindestens ein
    Drittel kleiner als mit den alten Werten."""
    import random
    from datetime import datetime, timezone
    from PIL import Image
    import beweis_pdf as B
    from bild_proxy import _fuer_pdf

    random.seed(11)
    rohe = []
    for i in range(4):
        im = Image.new("RGB", (1200, 900))
        px = im.load()
        for y in range(0, 900, 2):
            for x in range(0, 1200, 2):
                v = (x * 7 + y * 3 + i * 40) % 200
                r = (v + random.randint(0, 55)) % 256
                px[x, y] = (r, (v + 60) % 256, (v + 120) % 256)
                px[x + 1, y] = (r, (v + 62) % 256, (v + 118) % 256)
        b = io.BytesIO()
        im.save(b, "JPEG", quality=88)
        rohe.append(b.getvalue())

    zeit = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)

    def groesse(ke, k, qe, q):
        fotos = [_fuer_pdf(r, ke if i == 0 else k, qe if i == 0 else q)
                 for i, r in enumerate(rohe)]
        return len(B.beweis_pdf(
            quelle="mobile", daten={"make_label": "VW", "model_label": "Golf"},
            url="https://x.de/a", item_id="1", beweis_id="b1", abgerufen_am=zeit,
            erstellt_am=zeit, fotos=fotos,
            foto_urls=[f"https://img/{i}" for i in range(len(rohe))]))

    import beweis_service as BS
    frueher = groesse(1280, 800, 75, 70)
    jetzt = groesse(BS._FOTO_KANTE_ERSTE, BS._FOTO_KANTE,
                    BS._FOTO_QUALITAET_ERSTE, BS._FOTO_QUALITAET)
    assert jetzt < frueher * 0.7, (frueher, jetzt)


def test_12_gespeicherte_fotos_werden_immer_verkleinert():
    """Inserats-, Abhol- und Logofotos laufen alle durch bild_verkleinern."""
    from PIL import Image
    import storage_service as st

    assert st.MAX_BILD_KANTE <= 1600 and st.BILD_QUALITAET <= 80
    gross = io.BytesIO()
    Image.new("RGB", (2400, 1800), (120, 40, 40)).save(gross, "JPEG", quality=95)
    klein = st.bild_verkleinern(gross.getvalue(), "Inserats-Foto")
    with Image.open(io.BytesIO(klein)) as im:
        assert max(im.size) == st.MAX_BILD_KANTE
    assert len(klein) < len(gross.getvalue())


# --------------------------------------------- Vertragstexte: ein Feld
def test_13_startertext_enthaelt_klauseln_und_agb():
    """Runde 26 (Wunsch Ahmad): Aus zwei aehnlich klingenden Feldern wird
    eins. Neue Firmen starten mit EINEM Text, der beides enthaelt.
    """
    from pdf_service import (AGB_PUNKTE_START, DIGITAL_VERTRAGSTEXT_STANDARD,
                             VERTRAGSTEXT_START)
    assert DIGITAL_VERTRAGSTEXT_STANDARD in VERTRAGSTEXT_START
    assert AGB_PUNKTE_START in VERTRAGSTEXT_START
    for stueck in ("Sachmängelhaftung", "§ 14 BGB", "Eigentumsübergang",
                   "Schriftform", "Gerichtsstand"):
        assert stueck in VERTRAGSTEXT_START, stueck
    # Absaetze sind durch Leerzeilen getrennt (so rendert das PDF sie)
    absaetze = [a for a in VERTRAGSTEXT_START.split(chr(10) + chr(10)) if a.strip()]
    assert len(absaetze) >= 9, absaetze


def test_14_neue_firmen_bekommen_nur_noch_ein_textfeld():
    """Anlage ueber die Selbstregistrierung UND ueber den Betreiber setzen
    den Startertext in digital_vertragstext; default_terms bleibt leer."""
    for modul in ("routes.auth", "routes.admin"):
        quelle = inspect.getsource(importlib.import_module(modul))
        assert '"default_terms": ""' in quelle, modul
        assert "VERTRAGSTEXT_START" in quelle, modul
        assert '"digital_vertragstext": _vertragstext_start' in quelle, modul


def test_15_einstellungen_fuehren_alten_agb_text_zusammen():
    """Die Oberflaeche haengt einen vorhandenen AGB-Text unten an und leert
    beim Speichern das alte Feld — geprueft am Quelltext der Seite."""
    seite = (WURZEL / "frontend" / "src" / "pages" / "app" / "Einstellungen.jsx")
    quelle = seite.read_text(encoding="utf-8")
    assert "zusammenfuehren(dealer.default_terms, dealer.digital_vertragstext)" in quelle
    assert 'default_terms: "",' in quelle, "altes Feld wird beim Speichern geleert"
    assert "Allgemeine Geschäftsbedingungen (AGB)" not in quelle, "zweites Feld ist weg"
    assert "agb-zusammengefuehrt" in quelle, "Hinweis fuer den Nutzer fehlt"
