# -*- coding: utf-8 -*-
"""STORNIERT-Zeichen auf Vertrags-PDFs (Wunsch Ahmad 24.11.2026).

Steht der Kaufvorgang des Vertrags oder das Fahrzeug auf "storniert", traegt
jedes AUSGELIEFERTE Vertrags-PDF schraeg, rot und halbtransparent das Wort
"STORNIERT" auf jeder Seite: Ansicht/Download in der App, Druck- und
digitale Fassung, archivierte Fassungen, oeffentlicher Link, Fahrer-App,
Betreiber-Ansicht, Kundenportal (vor der Unterschrift) und der Mailanhang.

Regeln:
  * Das Zeichen wird NUR beim Ausliefern aufgelegt — gespeicherte PDFs
    (generated_pdfs.pdf_b64 / pdf_digital_b64, Archivfassungen, Bucket)
    bleiben unveraendert. Wird der Vorgang wieder aktiv, ist das Zeichen weg.
  * Beweisdokumente und unterschriebene Fassungen mit Signaturnachweis
    werden NIE gestempelt (sie sind Nachweis des damaligen Stands).
  * Die App zeigt das PDF per pdf.js ohne eigenes Zeichen — kein doppeltes.
"""
import asyncio
import io
import logging
from typing import Optional

import deps

log = logging.getLogger("autohandel")

ZEICHEN_TEXT = "STORNIERT"
ZEICHEN_FARBE = (0.85, 0.05, 0.05)     # Rot
ZEICHEN_DECKKRAFT = 0.28               # halbtransparent, Text darunter bleibt lesbar
ZEICHEN_WINKEL = 45


async def ist_storniert(contract_id: str, contract: Optional[dict] = None) -> bool:
    """True, wenn der Kaufvorgang dieses Vertrags ODER das Fahrzeug auf
    "storniert" steht. Liest nur (keine Selbstheilung wie kaufvorgang.fuer_vertrag)."""
    c = contract or {}
    if not all(k in c for k in ("id", "dealer_id", "vehicle_id")) or "kaufvorgang_id" not in c:
        c = await deps.db.generated_pdfs.find_one(
            {"id": contract_id},
            {"_id": 0, "id": 1, "dealer_id": 1, "vehicle_id": 1, "kaufvorgang_id": 1}) or {}
    if not c:
        return False
    kv = None
    if c.get("kaufvorgang_id"):
        kv = await deps.db.kaufvorgaenge.find_one(
            {"id": c["kaufvorgang_id"], "contract_id": contract_id}, {"_id": 0, "status": 1})
    if kv is None:
        kv = await deps.db.kaufvorgaenge.find_one({"contract_id": contract_id}, {"_id": 0, "status": 1})
    if kv and kv.get("status") == "storniert":
        return True
    if c.get("vehicle_id") and c.get("dealer_id"):
        v = await deps.db.vehicles.find_one({"id": c["vehicle_id"], "dealer_id": c["dealer_id"]},
                                       {"_id": 0, "lifecycle": 1})
        if v and v.get("lifecycle") == "storniert":
            return True
    return False


def _auflage(breite: float, hoehe: float) -> bytes:
    """Eine Seite in genau dieser Groesse mit dem schraegen Zeichen in der Mitte."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas

    puffer = io.BytesIO()
    leinwand = canvas.Canvas(puffer, pagesize=(breite, hoehe))
    schrift = "Helvetica-Bold"
    # so gross, dass das Wort die Diagonale gut fuellt (ca. 75 %)
    diagonale = (breite ** 2 + hoehe ** 2) ** 0.5
    groesse = 0.75 * diagonale / max(stringWidth(ZEICHEN_TEXT, schrift, 100), 1) * 100
    leinwand.saveState()
    leinwand.setFillColorRGB(*ZEICHEN_FARBE)
    leinwand.setStrokeColorRGB(*ZEICHEN_FARBE)
    leinwand.setFillAlpha(ZEICHEN_DECKKRAFT)
    leinwand.setStrokeAlpha(ZEICHEN_DECKKRAFT)
    leinwand.translate(breite / 2, hoehe / 2)
    leinwand.rotate(ZEICHEN_WINKEL)
    leinwand.setFont(schrift, groesse)
    leinwand.drawCentredString(0, -groesse * 0.35, ZEICHEN_TEXT)
    leinwand.restoreState()
    leinwand.showPage()
    leinwand.save()
    return puffer.getvalue()


def zeichen_auflegen(pdf_bytes: bytes) -> bytes:
    """Legt "STORNIERT" auf jede Seite und liefert eine NEUE Datei (Eingabe unveraendert)."""
    from pypdf import PdfReader, PdfWriter

    schreiber = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes)))
    auflagen: dict = {}
    for seite in schreiber.pages:
        kasten = seite.mediabox
        masse = (float(kasten.width), float(kasten.height))
        if masse not in auflagen:
            auflagen[masse] = _auflage(*masse)
        auflage = PdfReader(io.BytesIO(auflagen[masse])).pages[0]
        # Auflage in den Ursprung der Seite schieben (mediabox muss nicht bei 0/0 beginnen)
        from pypdf import Transformation
        seite.merge_transformed_page(
            auflage, Transformation().translate(float(kasten.left), float(kasten.bottom)))
    aus = io.BytesIO()
    schreiber.write(aus)
    return aus.getvalue()


async def falls_storniert(contract_id: str, pdf_bytes: Optional[bytes],
                          contract: Optional[dict] = None) -> Optional[bytes]:
    """Zeichen auflegen, wenn der Vertrag storniert ist — sonst die Bytes unveraendert.
    Scheitert das Auflegen (sollte bei eigenen PDFs nie passieren), wird es
    protokolliert und das Original geliefert (lieber lesbar als 500)."""
    if not pdf_bytes or not contract_id:
        return pdf_bytes
    try:
        if not await ist_storniert(contract_id, contract):
            return pdf_bytes
        return await asyncio.to_thread(zeichen_auflegen, pdf_bytes)
    except Exception:  # noqa: BLE001
        log.exception("STORNIERT-Zeichen fuer Vertrag %s nicht aufgelegt", contract_id)
        return pdf_bytes
