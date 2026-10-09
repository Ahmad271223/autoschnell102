# -*- coding: utf-8 -*-
"""Wunsch Ahmad 09.10.2026: erkennt das Programm etwas nicht, schickt es ein Bild der AutoPointer-Anzeige (Lesebild);
der Betreiber sieht es unter Programm-Vergleiche. Datei im Speicher, Vorschau in der Datenbank, 30 Tage."""
import base64
import io
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import konten  # noqa: E402
import werkzeuge as wz  # noqa: E402
from test_browser_helfer_20261004 import _verbinden, welt  # noqa: E402,F401 — dieselbe Testfirma (Kunde 10002)

API = konten.API
pytestmark = pytest.mark.skipif(not os.environ.get("RUNDE14_HTTP"), reason="nur gegen ein laufendes Backend")


def _png(breite=640, hoehe=240, text="Hyundai IBO") -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (breite, hoehe), "white")
    ImageDraw.Draw(im).text((10, 10), text, fill="black")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_01_lesebild_hochladen_sehen_loeschen(welt):
    db = welt["db"]
    pc = {**_verbinden(welt, "sucher", wid=wz.AUTOPOINTER, name="PC-Lesebild"), "User-Agent": "AutoSchnell-Vergleich/1.5.13"}
    helfer = _verbinden(welt, name="Edge · Windows")
    bild = _png()
    body = {"grund": "modell_unbekannt", "rohtext": "Marke, Modell: Hyundai IBO\nErstzulassung: 03/2019",
            "fahrzeug": {"marke_modell_text": "Hyundai IBO", "titel": "Hyundai IBO 1.4", "quelle": "mobile.de",
                         "inserat_id": "476271020", "boese": "x" * 500},
            "bild": base64.b64encode(bild).decode("ascii")}
    try:
        r = requests.post(f"{API}/werkzeuge/{wz.AUTOPOINTER}/lesebild", headers=pc, json=body, timeout=60)
        assert r.status_code == 200, r.text
        bid = r.json()["id"]
        doc = db.werkzeug_lesebilder.find_one({"id": bid})
        assert doc["grund"] == "modell_unbekannt" and doc["dealer_id"] == welt["firma"]["dealer_id"]
        assert doc["fahrzeug"] == {"marke_modell_text": "Hyundai IBO", "titel": "Hyundai IBO 1.4",
                                   "quelle": "mobile.de", "inserat_id": "476271020"}, "nur die vier Felder"
        assert doc["key"].startswith("werkzeug-lesebilder/") and doc["key"].endswith(".png")
        assert base64.b64decode(doc["vorschau_b64"])[:3] == b"\xff\xd8\xff", "JPEG-Vorschau"
        assert doc["groesse"] == len(bild) and doc["vorgang_id"] is None
        # Betreiber: Liste mit Vorschau, Konto, Firma, Grund-Text; die Datei als PNG
        liste = requests.get(f"{API}/admin/werkzeug-lesebilder", headers=konten.super_kopf(), timeout=30)
        assert liste.status_code == 200, liste.text
        eintrag = next(x for x in liste.json()["lesebilder"] if x["id"] == bid)
        assert eintrag["grund_text"] == "Modell nicht erkannt" and eintrag["kunden_nr"] in (10002, "10002")
        assert eintrag["vorschau_b64"] and "key" not in eintrag and eintrag["konto"]
        datei = requests.get(f"{API}/admin/werkzeug-lesebilder/{bid}/bild", headers=konten.super_kopf(), timeout=30)
        assert datei.status_code == 200 and datei.headers["content-type"].startswith("image/png")
        assert datei.content == bild
        # nur der Betreiber
        assert requests.get(f"{API}/admin/werkzeug-lesebilder", headers=welt["chef"], timeout=30).status_code in (401, 403)
        # Fehlerfaelle: Erweiterungs-Schluessel, kein PNG, zu gross, falscher Grund
        assert requests.post(f"{API}/werkzeuge/{wz.BROWSER_HELFER}/lesebild", headers=helfer, json=body,
                             timeout=30).status_code == 404
        assert requests.post(f"{API}/werkzeuge/{wz.AUTOPOINTER}/lesebild", headers=pc, timeout=30,
                             json={**body, "bild": base64.b64encode(b"GIF89a" + b"x" * 100).decode()}).status_code == 422
        zu_gross = base64.b64encode(b"\x89PNG" + b"\x00" * (1536 * 1024 + 10)).decode()
        assert requests.post(f"{API}/werkzeuge/{wz.AUTOPOINTER}/lesebild", headers=pc, timeout=60,
                             json={**body, "bild": zu_gross}).status_code in (413, 422)
        assert requests.post(f"{API}/werkzeuge/{wz.AUTOPOINTER}/lesebild", headers=pc, timeout=30,
                             json={**body, "grund": "egal"}).status_code == 422
        # Ablauf: ein alter Eintrag verschwindet samt Datei beim naechsten Aufruf
        db.werkzeug_lesebilder.update_one({"id": bid}, {"$set": {"ablauf": datetime.now(timezone.utc) - timedelta(days=1)}})
        requests.get(f"{API}/admin/werkzeug-lesebilder", headers=konten.super_kopf(), timeout=30)
        assert db.werkzeug_lesebilder.count_documents({"id": bid}) == 0
        assert requests.get(f"{API}/admin/werkzeug-lesebilder/{bid}/bild", headers=konten.super_kopf(),
                            timeout=30).status_code == 404
        # Loeschen durch den Betreiber
        r = requests.post(f"{API}/werkzeuge/{wz.AUTOPOINTER}/lesebild", headers=pc, json=body, timeout=60)
        bid2 = r.json()["id"]
        assert requests.delete(f"{API}/admin/werkzeug-lesebilder/{bid2}", headers=konten.super_kopf(),
                               timeout=30).status_code == 200
        assert db.werkzeug_lesebilder.count_documents({"id": bid2}) == 0
    finally:
        db.werkzeug_lesebilder.delete_many({"dealer_id": welt["firma"]["dealer_id"]})


def test_02_vergleich_nennt_modell_gefunden(welt):
    """Das Programm (ab 1.5.13) schickt bei modell_gefunden=false ein Lesebild — die Antwort muss es nennen."""
    pc = {**_verbinden(welt, "sucher", wid=wz.AUTOPOINTER, name="PC-Lesebild2"), "User-Agent": "AutoSchnell-Vergleich/1.5.13"}
    f = {"marke": "Hyundai", "modell": "", "marke_modell_text": "Hyundai XQZ9", "titel": "Hyundai XQZ9", "ez_monat": 3,
         "ez_jahr": 2019, "kilometer": 40000, "kw": 88, "preis": 9000, "quelle": "mobile.de", "inserat_id": "476271021",
         "roh": True}
    try:
        r = requests.post(f"{API}/werkzeuge/{wz.AUTOPOINTER}/vergleich", headers=pc, json={"fahrzeug": f}, timeout=60)
        assert r.status_code == 200, r.text
        assert r.json()["fahrzeug"]["modell_gefunden"] is False and r.json()["fahrzeug"]["erkannt"] is True
        f2 = dict(f, marke_modell_text="Hyundai i30", titel="Hyundai i30")
        r = requests.post(f"{API}/werkzeuge/{wz.AUTOPOINTER}/vergleich", headers=pc, json={"fahrzeug": f2}, timeout=60)
        assert r.json()["fahrzeug"]["modell_gefunden"] is True
    finally:
        welt["db"].werkzeug_vergleiche.delete_many({"werkzeug": wz.AUTOPOINTER, "user_id": welt["sucher_id"]})
        welt["db"].link_jobs.delete_many({"url": {"$regex": "47627102"}})
