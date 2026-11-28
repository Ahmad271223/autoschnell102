# -*- coding: utf-8 -*-
"""Entscheidung Ahmad 04.10.2026 (Pruefung Nr. 26): Gibt es eine neue Fassung des Kaufvertrags (Preis,
Verkaeufer, Fahrzeug, Bedingungen, auch ein verschobener Abholtermin), ist der alte WhatsApp-Link SOFORT
ungueltig. Vorher lieferte er bis zu 14 Tage die alte Fassung — der Verkaeufer konnte zwei verschiedene,
funktionierende Vertraege in der Hand haben. Der Verkaeufer sieht im Browser eine lesbare Seite
("bitte beim Haendler die aktuelle Fassung anfordern") statt JSON.

In-Prozess mit der Wegwerf-Welt aus test_rp_vertrag_20260922."""
import inspect
import sys
from pathlib import Path

from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rp_vertrag_20260922 import _erwarte, _jetzt, _modul, _vertrag, welt  # noqa: E402,F401


def _anfrage(accept="*/*"):
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                    "headers": [(b"accept", accept.encode())], "client": ("203.0.113.9", 1234)})


def _freigabe(token, version):
    return {"token": token, "erstellt_am": _jetzt(-600), "laeuft_ab": _jetzt(5 * 86400),
            "version": version, "abrufe": 0}


def test_26_alter_link_ist_nach_neuer_fassung_sofort_ungueltig(welt):
    C = _modul("routes.contracts")
    w = welt
    cid = f"cl_{w.s}"
    t_alt, t_neu = f"alt{w.s}", f"neu{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(
        w, cid, w.a, version=2, freigabe=_freigabe(t_neu, 2), freigabe_alt=[_freigabe(t_alt, 1)])))
    w.run(w.db.generated_pdf_versions.insert_one({"id": f"v1_{w.s}", "contract_id": cid,
                                                  "dealer_id": w.dealer_id, "version": 1,
                                                  "pdf_digital_b64": "JVBERi0xLjQgYWx0"}))
    # aktueller Link: die aktuelle Fassung
    r = w.run(C.public_vertrag_pdf(t_neu, _anfrage()))
    assert r.media_type == "application/pdf" and r.body.startswith(b"%PDF")
    # alter Link: obwohl er laut Datum noch 5 Tage liefe und die alte Fassung im Archiv liegt -> 410
    e = w.run(_erwarte(410, C.public_vertrag_pdf(t_alt, _anfrage())))
    assert e.detail == C.FASSUNG_ERSETZT_TEXT and "nicht mehr gültig" in e.detail
    # im Browser eine lesbare Seite mit demselben Status
    seite = w.run(C.public_vertrag_pdf(t_alt, _anfrage("text/html,application/xhtml+xml")))
    assert seite.status_code == 410 and b"aktuelle Fassung anfordern" in seite.body
    assert b"<!doctype html>" in seite.body and seite.headers["x-robots-tag"] == "noindex"
    # nur der eigene Style-Block ist erlaubt (Pruefsumme), keine Skripte
    csp = seite.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "style-src 'sha256-" in csp and "script" not in csp


def test_26_link_derselben_fassung_bleibt_gueltig_und_fremde_token_404_als_seite(welt):
    C = _modul("routes.contracts")
    w = welt
    cid = f"cg_{w.s}"
    tok = f"gleich{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a, version=1, freigabe=_freigabe(tok, 1))))
    assert w.run(C.public_vertrag_pdf(tok, _anfrage())).body.startswith(b"%PDF")
    seite = w.run(C.public_vertrag_pdf("gibtsnicht" + w.s, _anfrage("text/html")))
    assert seite.status_code == 404 and b"Kaufvertrag nicht verf" in seite.body
    w.run(_erwarte(404, C.public_vertrag_pdf("gibtsnicht" + w.s, _anfrage())))


def test_26_kein_archivabruf_mehr_im_oeffentlichen_link():
    C = _modul("routes.contracts")
    q = inspect.getsource(C.public_vertrag_pdf)
    assert "generated_pdf_versions" not in q, "alte Fassungen werden ueber den Link nicht mehr ausgeliefert"
    assert "raise HTTPException(410, FASSUNG_ERSETZT_TEXT)" in q
