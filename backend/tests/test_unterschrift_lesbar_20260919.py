# -*- coding: utf-8 -*-
"""Kaputte Unterschrift / kaputtes Bild im PDF (Befund Rollentest 19.09.2026).

Beim Abschluss des Abholprotokolls kam eine abgeschnittene PNG-Datei durch die
Bildpruefung: die ersten Bytes stimmen ja. Aufgefallen ist sie erst beim Bauen
des PDF — mit einem "Internen Serverfehler" (500), und zwar genau in dem
Moment, in dem der Fahrer beim Verkaeufer unterschreiben laesst.

Zwei Sicherungen, jede an ihrer richtigen Stelle:
  * Die Unterschrift wird beim Abschluss wirklich gelesen -> klare Ansage
    (400 "bitte noch einmal unterschreiben") statt Absturz. Sie darf nicht
    still fehlen: ohne Unterschrift ist das Protokoll wertlos.
  * Die PDF-Bauer lassen bei einem unlesbaren Bild einfach Platz, statt
    abzustuerzen. Fotos duerfen beim Hochladen weiterhin nie scheitern
    (Regel in storage_service.bild_verkleinern) — dafuer faellt jetzt
    hoechstens das Bild aus, nie der ganze Vorgang.
"""
import inspect
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Immer ueber das MODUL zugreifen: test_bilder_verkleinern laedt
# storage_service neu (importlib.reload) — feste Namen zeigen danach auf die
# alte Klasse, und pytest.raises(st.StorageError) greift nicht mehr.
import storage_service as st  # noqa: E402

PIL = pytest.importorskip("PIL.Image")


def _png(groesse=(24, 24), farbe=(200, 30, 30)):
    speicher = io.BytesIO()
    PIL.new("RGB", groesse, farbe).save(speicher, format="PNG")
    return speicher.getvalue()


def _jpeg(groesse=(24, 24)):
    speicher = io.BytesIO()
    PIL.new("RGB", groesse, (10, 120, 200)).save(speicher, format="JPEG")
    return speicher.getvalue()


def _abgeschnitten(roh):
    """Wie eine abgebrochene Uebertragung: Kopf da, Rest fehlt."""
    return roh[: len(roh) // 2]


def test_01_abgeschnittenes_bild_kommt_durch_die_formatpruefung():
    """Der Grund fuer den Befund: die alte Pruefung sieht nur die ersten
    Bytes — fuer sie ist die halbe Datei eine gueltige PNG-Datei."""
    st.validate_image_bytes(_abgeschnitten(_png()), wo="Unterschrift")


def test_02_neue_pruefung_erkennt_das_abgeschnittene_bild():
    with pytest.raises(st.StorageError) as e:
        st.bild_lesbar_pruefen(_abgeschnitten(_png()), wo="Unterschrift (fahrer)")
    assert "beschädigt" in str(e.value) or "unvollständig" in str(e.value)


def test_03_heile_bilder_gehen_weiter_durch():
    for roh in (_png(), _jpeg(), _png((1, 1))):
        st.bild_lesbar_pruefen(roh, wo="Unterschrift")


def test_04_abschluss_sagt_was_zu_tun_ist():
    import routes.protocols as P
    q = inspect.getsource(P.finalize_protocol)
    assert "bild_lesbar_pruefen" in q, "Unterschrift wird nicht wirklich gelesen"
    assert "bitte noch einmal unterschreiben" in q, \
        "Der Fahrer braucht eine Ansage, keine Fehlernummer"


def test_05_fahrer_app_leert_die_unterschrift_fuer_den_zweiten_versuch():
    """Mit demselben kaputten Bild waere jeder weitere Versuch gescheitert."""
    seite = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
             / "driver" / "Protokoll.jsx").read_text(encoding="utf-8")
    assert "e?.response?.status === 400" in seite
    assert "/Unterschrift/i.test(errMsg(e, \"\"))" in seite
    assert "setSigRunde((n) => n + 1);" in seite


def test_06_protokoll_pdf_stuerzt_an_einem_kaputten_bild_nicht_ab():
    """Genau dieser Aufruf warf vorher OSError('image file is truncated')
    und wurde zu einem 500 beim Unterschreiben."""
    import pickup_pdf_service as P
    kaputt = _abgeschnitten(_png((200, 200)))
    pdf = P.build_pickup_pdf(appointment={"id": "t"}, vehicle={}, contract={},
                             filled={"vehicle_check": {},
                                     "signature_driver": kaputt,
                                     "signature_seller": kaputt})
    assert pdf[:4] == b"%PDF"


def test_07_beweisdokument_nimmt_kein_kaputtes_foto():
    import beweis_pdf as B
    assert B._bildgroesse(_abgeschnitten(_png((200, 200)))) is None
    assert B._bildgroesse(_png((200, 200))) == (200, 200)


def test_08_fotos_duerfen_weiter_nicht_am_verkleinern_scheitern():
    """Bewusste Regel (unveraendert): ein Foto, das sich nicht verkleinern
    laesst, wird im Original gespeichert statt den Fahrer stehen zu lassen."""
    kaputt = b"\xff\xd8\xff" + b"kein echtes Bild" * 20
    assert st.bild_verkleinern(kaputt, "Foto") == kaputt
