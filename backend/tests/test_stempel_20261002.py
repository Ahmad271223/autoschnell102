# -*- coding: utf-8 -*-
"""Stempel & Unterschrift (Wunsch Ahmad 02.10.2026, nach seiner HTML-Vorlage — nur Stempel und Unterschrift,
keine Rechnung): Der Chef erstellt in den Firmenseiten-Einstellungen aus den Firmendaten einen Stempel (sechs
Designs), unterschreibt im Feld, die Unterschrift liegt ueber dem Stempel; "Uebernehmen" hinterlegt das PNG auf
demselben Weg wie ein hochgeladenes Bild (POST /dealer/unterschrift). Der Server liefert dafuer die Firmendaten
zur Vorbelegung mit (firma_daten); Sucher bekommen das Werkzeug nicht (Nr. 4 der Pruefliste 30.09.)."""
import io
from pathlib import Path

import routes.kundenportal as KP

from test_kundenportal_20260929 import _b64, _lauf, welt  # noqa: F401

FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"


def _stempel_png() -> bytes:
    """Ein Stempel wie aus dem Werkzeug: weisser Grund, schwarzer Rahmen, Text, Unterschriftsstrich."""
    from PIL import Image, ImageDraw
    bild = Image.new("RGB", (1000, 400), (255, 255, 255))
    z = ImageDraw.Draw(bild)
    z.rounded_rectangle([6, 6, 994, 394], radius=22, outline=(0, 0, 0), width=8)
    z.text((80, 120), "KFZ MUELLER GMBH", fill=(0, 0, 0))
    z.line([(300, 300), (400, 240), (500, 310), (650, 250)], fill=(10, 10, 40), width=6)
    p = io.BytesIO()
    bild.save(p, format="PNG")
    return p.getvalue()


def test_01_firmendaten_zur_vorbelegung(welt):  # noqa: F811
    w = welt
    w.run(w.db.dealers.update_one({"id": w.dealer_id}, {"$set": {"phone": "030 123456", "email": "info@example.org"}}))
    st = _lauf(KP.get_webseite(user=w.chef))
    assert st["firma_daten"] == {"name": "KFZ Müller GmbH", "strasse": "Hauptstraße 1", "plz": "12345", "ort": "Berlin",
                                 "tel": "030 123456", "mail": "info@example.org"}
    assert st["ist_chef"] is True
    # auch der Sucher sieht die Firmendaten (Werkzeug selbst nur beim Chef, Oberflaeche)
    assert _lauf(KP.get_webseite(user=w.sucher))["firma_daten"]["name"] == "KFZ Müller GmbH"


def test_02_stempel_png_wird_als_unterschrift_hinterlegt(welt):  # noqa: F811
    w = welt
    erg = _lauf(KP.unterschrift_hochladen(KP.UnterschriftIn(bild_b64=_b64(_stempel_png())), user=w.chef))
    assert erg["unterschrift_vorhanden"] is True
    d = w.run(w.db.dealers.find_one({"id": w.dealer_id}, {"_id": 0, "unterschrift_key": 1}))
    assert d["unterschrift_key"] in w.ablage
    gespeichert = w.ablage[d["unterschrift_key"]]
    assert gespeichert[:8] == b"\x89PNG\r\n\x1a\n"


def test_03_oberflaeche_verdrahtet():
    fe = (FRONTEND / "components" / "FirmenseiteEinstellungen.jsx").read_text(encoding="utf-8")
    assert 'data-testid="firmenseite-stempel-oeffnen"' in fe and "<StempelUnterschrift" in fe
    assert "istChef && stempelOffen" in fe, "Werkzeug nur fuer den Chef"
    assert "api.post(`${basis}/unterschrift`, { bild_b64: dataUrl })" in fe
    komp = (FRONTEND / "components" / "StempelUnterschrift.jsx").read_text(encoding="utf-8")
    assert "Rechnung" not in komp.replace("keine Rechnung", ""), "nur Stempel und Unterschrift, keine Rechnung"
    lib = (FRONTEND / "lib" / "stempel.js").read_text(encoding="utf-8")
    for design in ("kasten", "rund", "ring", "text", "linien", "kapsel"):
        assert f'id: "{design}"' in lib, design
