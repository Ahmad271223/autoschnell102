# -*- coding: utf-8 -*-
"""PDF „Abänderungsvertrag vom <Datum>“ (Wunsch Ahmad 24.11.2026).

Kopf wie der Kaufvertrag (Logo, Firma, Nummer, Datum, Kunden-Nr., Vertragsfarbe/-layout, Schrift schwarz), dann
die Parteien Verkäufer/Käufer, der Bezug auf den ursprünglichen Vertrag (Datum, Fahrzeug, FIN), NUR die Änderungen
als Liste „bisher → neu“, der Satz, dass alles andere unverändert gilt, und die Unterschriftsfelder Verkäufer/Käufer.
Keine Fassungsnummer im Dokument (der Kunde sieht keine Fassung, Entscheidung Ahmad 04.10.2026), kein Satz
„ohne Unterschrift gültig“ (01.10.2026).

Die Bausteine kommen aus pdf_service (gleiches Aussehen); pdf_service selbst wird hier nicht verändert. Die beiden
Beschriftungen „Unterschrift“ stehen auf gleicher Höhe links/rechts — so setzt portal_pdf.unterschreiben die
Unterschriftsbilder bei der Online-Unterschrift in die Felder (wie beim Kaufvertrag).
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Dict, List, Optional
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import abaenderung as AB

TITEL = "ABÄNDERUNGSVERTRAG"
UNVERAENDERT_SATZ = ("Alle übrigen Vereinbarungen des Kaufvertrags bleiben unverändert und gelten weiter. "
                     "Dieser Abänderungsvertrag ist Bestandteil des Kaufvertrags.")


def datum_de(iso: Optional[str]) -> str:
    s = str(iso or "").strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%d.%m.%Y")
    except ValueError:
        return s[:10]


def _fahrzeug(vehicle: dict) -> str:
    from pdf_service import hersteller_typ
    return hersteller_typ(vehicle or {}) or "Fahrzeug"


def erzeugen(*, dealer: dict, vehicle: dict, contract: dict, aenderungen: List[Dict[str, str]],
             vertrag_datum: str, datum: Optional[str] = None) -> bytes:
    """dealer/vehicle wie beim Kaufvertrag (Käuferdaten eingefroren, Dialogwerte übernommen — siehe
    routes.contracts._apply_contract_overrides), contract = AKTUELLE Vertragsdaten (Verkäufer, Nummer, Design),
    aenderungen = abaenderung.aenderungen(...), vertrag_datum = Datum des ursprünglichen Kaufvertrags (ISO)."""
    import pdf_service as P
    c = contract or {}
    hexwert = P.vertrag_farbe_hex(c.get("vertrag_farbe"))
    token = P._GESTALTUNG.set({"farbe": colors.HexColor(hexwert), "hex": hexwert,
                               "layout": P.vertrag_layout(c.get("vertrag_layout"))})
    try:
        return _bauen(P, dealer=dealer or {}, vehicle=vehicle or {}, contract=c, aenderungen=aenderungen,
                      vertrag_datum=vertrag_datum, datum=datum)
    finally:
        P._GESTALTUNG.reset(token)


def _bauen(P, *, dealer, vehicle, contract, aenderungen, vertrag_datum, datum) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=P.MARGIN, rightMargin=P.MARGIN,
                            topMargin=1.6 * cm, bottomMargin=2.0 * cm, title="Abänderungsvertrag",
                            author=dealer.get("company_name", "Autohändler"))
    st = P._styles()
    story = []
    heute = datum_de(datum) if datum else datetime.now().strftime("%d.%m.%Y")
    firma = (dealer.get("company_name") or "Autohändler").strip()
    nr = str(contract.get("contract_no") or "").strip()
    vom = datum_de(vertrag_datum)
    untertitel = f"zum KFZ-Kaufvertrag{(' Nr. ' + nr) if nr else ''} vom {vom}"

    rechts = [Paragraph("VERTRAGS-NR.", st["meta_label"]), Paragraph(f"<b>{_xml_escape(nr or '—')}</b>", st["meta_value"]),
              Spacer(1, 5), Paragraph("DATUM", st["meta_label"]), Paragraph(f"<b>{heute}</b>", st["meta_value"])]
    kundennummer = str(contract.get("vertrags_kundennummer") or contract.get("kundennummer")
                       or dealer.get("vertrags_kundennummer") or "").strip()
    if kundennummer:
        rechts += [Spacer(1, 5), Paragraph("KUNDEN-NR.", st["meta_label"]),
                   Paragraph(f"<b>{_xml_escape(kundennummer)}</b>", st["meta_value"])]
    logo = P._logo_flowable(dealer.get("_logo_bytes"))
    if P._formular():
        links = [Paragraph(f"<b>{_xml_escape(firma)}</b>", st["f_firma"])]
        if logo is not None:
            links = [logo, Spacer(1, 3)] + links
        mitte = [Paragraph(f"<b>{TITEL}</b>", st["f_title"]), Paragraph(_xml_escape(untertitel), st["f_subtitle"])]
        kopf = Table([[links, mitte, rechts]], colWidths=[5.2 * cm, P.CONTENT_W - 9.4 * cm, 4.2 * cm], hAlign="LEFT")
        kopf.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("VALIGN", (1, 0), (1, 0), "MIDDLE"),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        story += [kopf, Spacer(1, 10)]
        trenner = Table([[""]], colWidths=[P.CONTENT_W], rowHeights=[0.8], hAlign="LEFT")
        trenner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), P.DIVIDER)]))
        story += [trenner, Spacer(1, 12)]
    else:
        links = [Paragraph(f"<b>{_xml_escape(firma)}</b>", st["brand"]), Spacer(1, 2),
                 Paragraph(f"<b>{TITEL}</b>", st["title"]), Paragraph(_xml_escape(untertitel), st["subtitle"])]
        if logo is not None:
            links = [logo, Spacer(1, 4)] + links
        kopf = Table([[links, rechts]], colWidths=[P.CONTENT_W - 4.5 * cm, 4.5 * cm])
        kopf.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        story += [kopf, Spacer(1, 8)]
        linie = Table([[""]], colWidths=[P.CONTENT_W], rowHeights=[2])
        linie.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), P._akzent())]))
        story += [linie, Spacer(1, 12)]

    verkaeufer = [("Name / Firma", contract.get("seller_name", "")), ("Anschrift", contract.get("seller_address", "")),
                  ("PLZ / Ort", f"{contract.get('seller_zip', '')} {contract.get('seller_city', '')}".strip()),
                  ("Telefon", contract.get("seller_phone", "")), ("E-Mail", contract.get("seller_email", ""))]
    kaeufer = [("Name / Firma", dealer.get("company_name", "")), ("Anschrift", str(dealer.get("address", "")).strip()),
               ("PLZ / Ort", f"{dealer.get('zip_code', '')} {dealer.get('city', '')}".strip()),
               ("Telefon", dealer.get("phone", "")), ("E-Mail", dealer.get("email", ""))]
    story += [P._two_boxes("Verkäufer", P._ohne_leere(verkaeufer), "Käufer", P._ohne_leere(kaeufer), st), Spacer(1, 12)]

    fin = str(contract.get("vehicle_vin") or vehicle.get("vin") or "").strip()
    bezug = (f"Die Parteien ändern den zwischen ihnen geschlossenen KFZ-Kaufvertrag{(' Nr. ' + nr) if nr else ''} "
             f"vom {vom} über das Fahrzeug {_fahrzeug(vehicle)}"
             + (f", FIN {fin}" if fin else "") + ", wie folgt:")
    story += [P._section("Bezug", st), Spacer(1, 6), Paragraph(_xml_escape(bezug), st["body"]), Spacer(1, 12)]

    zeilen = []
    for i, z in enumerate(aenderungen or [], start=1):
        art = z.get("art") or "wert"
        label = _xml_escape(str(z.get("label") or ""))
        if art == "neu":
            inhalt = f"<b>{label}:</b> {_xml_escape(str(z.get('neu') or ''))}"
        elif art == "entfaellt":
            inhalt = f"<b>{label}:</b> {_xml_escape(str(z.get('bisher') or ''))}"
        else:
            bisher, neu = str(z.get("bisher") or ""), str(z.get("neu") or "")
            if len(bisher) + len(neu) > 140:
                inhalt = (f"<b>{label}</b><br/>bisher: {_xml_escape(bisher[:1500])}"
                          f"<br/>neu: {_xml_escape(neu[:3000])}")
            else:
                inhalt = f"<b>{label}:</b> {_xml_escape(bisher)} → <b>{_xml_escape(neu)}</b>"
        zeilen.append([Paragraph(f"{i}.", st["body"]), Paragraph(inhalt.replace("\n", "<br/>"), st["body"])])
    if zeilen:
        t = Table(zeilen, colWidths=[0.8 * cm, P.CONTENT_W - 0.8 * cm], hAlign="LEFT")
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                               ("LINEBELOW", (0, 0), (-1, -1), 0.25, P.DIVIDER)]))
        story += [P._section("Änderungen", st), Spacer(1, 6), t, Spacer(1, 10)]
    story += [Paragraph(_xml_escape(UNVERAENDERT_SATZ), st["body"]), Spacer(1, 16)]

    # Unterschriften: wie im Kaufvertrag (Kundenportal-Kasten ohne Empfangsbestaetigung) — je Seite die Linie mit
    # der Beschriftung "Unterschrift", damit portal_pdf die Bilder bei der Online-Unterschrift dort einsetzt.
    sig = P._empfang_paar(contract, st, unterschrift=True, mit_empfang=False, bilder=None, portal=True)
    story.append(KeepTogether([P._section("Unterschriften", st), Spacer(1, 8), sig]))
    doc.build(story, canvasmaker=P._numbered_canvas_factory(firma, f"Abänderungsvertrag {nr} · vom {heute}".replace("  ", " ")))
    return buf.getvalue()
