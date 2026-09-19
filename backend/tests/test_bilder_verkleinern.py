# -*- coding: utf-8 -*-
"""Fotos werden beim Hochladen verkleinert (Speicher-Audit 09/2026).

Grund: ungefiltert lagen nach kurzer Zeit ueber 11 GB Handyfotos im
Speicher. Ein Foto mit 4000 Bildpunkten Kantenlaenge bringt fuer ein
Inserat, ein Abhol-Protokoll oder ein Vertrags-PDF keinen Mehrwert; es
kostet nur Speicher, Sicherungszeit und Ladezeit beim Nutzer.

Geprueft wird ausserdem, was dabei NICHT kaputtgehen darf:
  * die Datei-Endung muss zum Inhalt passen (sonst liefert der Server
    einen falschen Dateityp aus),
  * die Drehung vom Handy (EXIF) muss angewandt werden,
  * Logos mit transparentem Hintergrund bleiben PNG,
  * unlesbare Bilder duerfen den Upload nicht abbrechen,
  * eine "Bildbombe" wird abgelehnt.
"""
import io
import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

import storage_service as st  # noqa: E402


def _foto(breite=4032, hoehe=3024, qualitaet=92) -> bytes:
    """Ein realistisches Foto: weiche Verlaeufe mit feiner Koernung."""
    random.seed(3)
    im = Image.new("RGB", (breite, hoehe))
    px = im.load()
    for y in range(hoehe):
        fy = y / hoehe
        for x in range(0, breite, 2):
            fx = x / breite
            c = (max(0, min(255, int(120 + 100 * math.sin(fx * 6) * math.cos(fy * 3)
                                     + random.randint(-6, 6)))),
                 max(0, min(255, int(110 + 90 * math.sin(fx * 4 + 1.2)
                                     + random.randint(-6, 6)))),
                 max(0, min(255, int(130 + 80 * math.cos(fy * 5)
                                     + random.randint(-6, 6)))))
            px[x, y] = c
            if x + 1 < breite:
                px[x + 1, y] = c
    o = io.BytesIO()
    im.save(o, format="JPEG", quality=qualitaet)
    return o.getvalue()


def _masse(daten: bytes):
    with Image.open(io.BytesIO(daten)) as im:
        return im.size, (im.format or "").upper(), im.mode


def test_grosses_foto_wird_deutlich_kleiner():
    roh = _foto()
    klein = st.bild_verkleinern(roh, "Inserats-Foto")
    (b, h), fmt, _ = _masse(klein)
    assert max(b, h) == st.MAX_BILD_KANTE, (b, h)
    assert fmt == "JPEG"
    # Der eigentliche Zweck: der Speicherbedarf muss spuerbar sinken.
    assert len(klein) < len(roh) / 4, f"{len(roh)} -> {len(klein)}"


def test_kleines_foto_bleibt_unveraendert():
    """Wer schon ein passendes Bild schickt, bekommt es Byte fuer Byte
    zurueck — kein Qualitaetsverlust durch sinnloses Neukodieren."""
    o = io.BytesIO()
    Image.new("RGB", (800, 600), "red").save(o, format="JPEG", quality=90)
    roh = o.getvalue()
    assert st.bild_verkleinern(roh, "Foto") == roh


def test_endung_und_inhalt_passen_zusammen():
    """Ein PNG-Foto landet unter foto.jpg — dann MUSS auch JPEG drin sein,
    sonst behauptet /api/files einen Dateityp, den die Datei nicht hat."""
    with Image.open(io.BytesIO(_foto(1200, 900))) as im:
        o = io.BytesIO()
        im.save(o, format="PNG")
    _, fmt, _ = _masse(st.bild_verkleinern(o.getvalue(), "Inserats-Foto", "JPEG"))
    assert fmt == "JPEG"


def test_logo_bleibt_png_mit_transparenz():
    logo = Image.new("RGBA", (3000, 3000), (0, 0, 0, 0))
    logo.paste(Image.new("RGBA", (1500, 1500), (255, 0, 0, 255)), (100, 100))
    o = io.BytesIO()
    logo.save(o, format="PNG")
    klein = st.bild_verkleinern(o.getvalue(), "Logo", "PNG")
    (b, h), fmt, modus = _masse(klein)
    assert fmt == "PNG" and modus in ("RGBA", "LA")
    assert max(b, h) == st.MAX_BILD_KANTE
    with Image.open(io.BytesIO(klein)) as k:
        assert k.convert("RGBA").getpixel((k.width - 2, k.height - 2))[3] == 0, \
            "transparente Ecke wurde undurchsichtig"


def test_drehung_vom_handy_wird_angewandt():
    """Ohne EXIF-Auswertung liegen Hochkant-Fotos nach dem Umwandeln quer."""
    quer = Image.new("RGB", (3000, 1200), "blue")
    exif = Image.Exif()
    exif[274] = 6                      # 274 = Orientation, 6 = 90 Grad drehen
    o = io.BytesIO()
    quer.save(o, format="JPEG", exif=exif, quality=90)
    (b, h), _, _ = _masse(st.bild_verkleinern(o.getvalue(), "Foto"))
    assert h > b, f"Foto liegt quer statt hochkant: {b}x{h}"


def test_bildbombe_wird_abgelehnt():
    """Eine kleine Datei kann entpackt Gigabyte belegen. Die Groesse steht
    im Kopf der Datei und wird geprueft, bevor etwas entpackt wird."""
    o = io.BytesIO()
    Image.new("L", (10000, 10000), 0).save(o, format="PNG")
    with pytest.raises(st.StorageError) as e:
        st.bild_verkleinern(o.getvalue(), "Foto")
    assert "Bildpunkte" in str(e.value)


def test_unlesbares_bild_bricht_den_upload_nicht_ab():
    """Das Bild wurde vorher schon als echtes Bild geprueft. Scheitert das
    Verkleinern trotzdem, wird das Original gespeichert statt den Nutzer
    mit einem Fehler stehen zu lassen."""
    kaputt = b"\xff\xd8\xff" + b"kein echtes Bild" * 20
    assert st.bild_verkleinern(kaputt, "Foto") == kaputt


def test_alle_foto_wege_verkleinern():
    """Regressionsschutz: ein neuer Upload-Weg soll nicht vergessen werden."""
    basis = Path(__file__).resolve().parents[1]
    for datei, stichwort in (("routes/resale.py", "Inserats-Foto"),
                             ("routes/drivers.py", "Abweichungsfoto"),
                             ("routes/dealer.py", "Logo")):
        quelle = (basis / datei).read_text(encoding="utf-8")
        assert "bild_verkleinern" in quelle, f"{datei} verkleinert nicht"
        assert stichwort in quelle


def test_grenzwerte_kommen_aus_der_umgebung(monkeypatch):
    """Der Betreiber muss die Kantenlaenge ohne Codeaenderung stellen
    koennen — z.B. wenn Fotos doch groesser gebraucht werden."""
    import importlib
    monkeypatch.setenv("MAX_IMAGE_EDGE", "900")
    monkeypatch.setenv("IMAGE_QUALITY", "70")
    neu = importlib.reload(st)
    try:
        assert neu.MAX_BILD_KANTE == 900 and neu.BILD_QUALITAET == 70
        (b, h), _, _ = _masse(neu.bild_verkleinern(_foto(2000, 1500), "Foto"))
        assert max(b, h) == 900
    finally:
        monkeypatch.undo()
        importlib.reload(st)
