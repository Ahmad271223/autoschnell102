"""PDF generation for car purchase contracts (Kaufvertrag) using ReportLab."""
import io
from datetime import datetime
from xml.sax.saxutils import escape as _xml_escape
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.pdfgen import canvas as _rl_canvas
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
)
from reportlab.lib.enums import TA_LEFT, TA_RIGHT


def _safe_para(text) -> str:
    """Escape user-supplied text for use inside a ReportLab Paragraph.

    ReportLab's Paragraph() parses content as simplified XML — unescaped
    angle-brackets / ampersands let an attacker inject ReportLab formatting
    tags such as <font color=...>, <b>, <a href=...>, etc.
    This function ensures all user-controlled values are XML-escaped before
    being passed to Paragraph().
    """
    if text is None or text == "":
        return "—"
    return _xml_escape(str(text).strip()) or "—"


PRIMARY = colors.HexColor("#18181B")
ACCENT = colors.HexColor("#FF3B30")
GREY = colors.HexColor("#71717A")
DIVIDER = colors.HexColor("#E4E4E7")
LIGHT = colors.HexColor("#F4F4F5")
DARK = colors.HexColor("#0A0A0A")

PAGE_W, PAGE_H = A4
MARGIN = 1.8 * cm
CONTENT_W = PAGE_W - 2 * MARGIN
COL_W = (CONTENT_W - 0.5 * cm) / 2  # two columns with a small gutter


def _styles():
    s = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=s["Title"], fontSize=24, leading=27,
                                textColor=PRIMARY, alignment=TA_LEFT, spaceAfter=0),
        "subtitle": ParagraphStyle("subtitle", parent=s["Normal"], fontSize=9,
                                   leading=12, textColor=GREY),
        "brand": ParagraphStyle("brand", parent=s["Normal"], fontSize=10, leading=13,
                                textColor=ACCENT),
        "meta_label": ParagraphStyle("meta_label", parent=s["Normal"], fontSize=7,
                                     leading=9, textColor=GREY, alignment=TA_RIGHT),
        "meta_value": ParagraphStyle("meta_value", parent=s["Normal"], fontSize=10,
                                     leading=13, textColor=PRIMARY, alignment=TA_RIGHT),
        "section": ParagraphStyle("section", parent=s["Normal"], fontSize=10,
                                  leading=13, textColor=PRIMARY),
        "boxtitle": ParagraphStyle("boxtitle", parent=s["Normal"], fontSize=9,
                                   leading=12, textColor=PRIMARY),
        "label": ParagraphStyle("label", parent=s["Normal"], fontSize=7, leading=9,
                                textColor=GREY, alignment=TA_LEFT),
        "value": ParagraphStyle("value", parent=s["Normal"], fontSize=9, leading=11,
                                textColor=PRIMARY, alignment=TA_LEFT),
        "small": ParagraphStyle("small", parent=s["Normal"], fontSize=8, leading=11,
                                textColor=GREY),
        "body": ParagraphStyle("body", parent=s["Normal"], fontSize=9, leading=12,
                               textColor=PRIMARY),
        "price_label": ParagraphStyle("price_label", parent=s["Normal"], fontSize=8,
                                      leading=10, textColor=colors.HexColor("#A1A1AA")),
        "price_value": ParagraphStyle("price_value", parent=s["Normal"], fontSize=17,
                                      leading=20, textColor=colors.white,
                                      alignment=TA_RIGHT),
        "price_sub": ParagraphStyle("price_sub", parent=s["Normal"], fontSize=8,
                                    leading=10, textColor=colors.HexColor("#D4D4D8"),
                                    alignment=TA_RIGHT),
        "sig_label": ParagraphStyle("sig_label", parent=s["Normal"], fontSize=8,
                                    leading=10, textColor=GREY),
    }


def _section(title, st):
    """Section heading: light bar with red accent edge — consistent visual anchor."""
    t = Table(
        [[Paragraph(f"<b>{_xml_escape(title)}</b>", st["section"])]],
        colWidths=[CONTENT_W],
    )
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _kv_compact(rows, st, label_w, value_w):
    """Compact key-value table with thin dividers, used inside a column."""
    data = []
    for label, value in rows:
        data.append([
            Paragraph(label, st["label"]),
            Paragraph(_safe_para(value), st["value"]),
        ])
    t = Table(data, colWidths=[label_w, value_w])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, DIVIDER),
    ]))
    return t


def _boxed_kv(title, rows, st):
    """Key-value block inside a bordered box with a titled header row."""
    label_w = 2.6 * cm
    val_w = COL_W - label_w - 0.6 * cm
    inner = _kv_compact(rows, st, label_w, val_w)
    t = Table(
        [[Paragraph(f"<b>{_xml_escape(title)}</b>", st["boxtitle"])], [inner]],
        colWidths=[COL_W],
    )
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, DIVIDER),
        ("BACKGROUND", (0, 0), (0, 0), LIGHT),
        ("LINEBELOW", (0, 0), (0, 0), 0.5, DIVIDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (0, 0), 5),
        ("BOTTOMPADDING", (0, 0), (0, 0), 5),
        ("TOPPADDING", (0, 1), (0, 1), 4),
        ("BOTTOMPADDING", (0, 1), (0, 1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def _two_boxes(left_title, left_rows, right_title, right_rows, st):
    """Place two boxed key-value blocks side by side."""
    left = _boxed_kv(left_title, left_rows, st)
    right = _boxed_kv(right_title, right_rows, st)
    t = Table([[left, "", right]], colWidths=[COL_W, 0.5 * cm, COL_W], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def _two_col_kv(rows, st):
    """Render a flat list of (label,value) into a 2-column kv layout
    (so Fahrzeugdaten fit compactly side by side)."""
    half = (len(rows) + 1) // 2
    left = rows[:half]
    right = rows[half:]
    while len(right) < len(left):
        right.append(("", ""))
    label_w = 3.2 * cm
    val_w = COL_W - label_w - 0.2 * cm
    left_t = _kv_compact(left, st, label_w, val_w)
    right_t = _kv_compact(right, st, label_w, val_w) if any(r[0] for r in right) else Paragraph("", st["value"])
    t = Table([[left_t, "", right_t]], colWidths=[COL_W, 0.5 * cm, COL_W], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def _yn(value):
    """Normalize Ja/Nein/empty to a display string."""
    if not value:
        return "—"
    s = str(value).strip()
    if s.lower() in ("ja", "yes", "true", "1"):
        return "Ja"
    if s.lower() in ("nein", "no", "false", "0"):
        return "Nein"
    return s


def _numbered_canvas_factory(footer_left: str, footer_center: str):
    """Canvas subclass drawing accent bar + footer with 'Seite X von Y' on
    every page. Two-pass: pages are buffered so the total count is known."""

    class _NumberedCanvas(_rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_states = []

        def showPage(self):
            self._saved_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_states)
            for state in self._saved_states:
                self.__dict__.update(state)
                self._decorate(total)
                super().showPage()
            super().save()

        def _decorate(self, total):
            # Top accent bar (full width) — brand anchor on every page.
            self.saveState()
            self.setFillColor(ACCENT)
            self.rect(0, PAGE_H - 0.14 * cm, PAGE_W, 0.14 * cm, stroke=0, fill=1)
            # Footer divider + text
            y = 1.1 * cm
            self.setStrokeColor(DIVIDER)
            self.setLineWidth(0.5)
            self.line(MARGIN, y + 0.35 * cm, PAGE_W - MARGIN, y + 0.35 * cm)
            self.setFillColor(GREY)
            self.setFont("Helvetica", 7)
            self.drawString(MARGIN, y, footer_left)
            self.drawCentredString(PAGE_W / 2, y, footer_center)
            self.drawRightString(PAGE_W - MARGIN, y,
                                 f"Seite {self._pageNumber} von {total}")
            self.restoreState()

    return _NumberedCanvas


def generate_contract_pdf(*, dealer: dict, vehicle: dict, contract: dict) -> bytes:
    """Build a Kaufvertrag PDF and return raw bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=1.6 * cm, bottomMargin=2.0 * cm,
        title="Kaufvertrag", author=dealer.get("company_name", "Autohändler"),
    )
    st = _styles()
    story = []

    today = datetime.now().strftime("%d.%m.%Y")
    company = (dealer.get("company_name") or "Autohändler").strip()
    contract_no = (contract.get("contract_no") or "").strip() or \
        f"KV-{datetime.now().strftime('%Y%m%d-%H%M')}"

    # ---------- Header / Briefkopf ----------
    header_left = [
        Paragraph(f"<b>{_xml_escape(company)}</b>", st["brand"]),
        Spacer(1, 2),
        Paragraph("<b>KAUFVERTRAG</b>", st["title"]),
        Paragraph("für ein gebrauchtes Kraftfahrzeug — Ankauf durch Händler",
                  st["subtitle"]),
    ]
    header_right = [
        Paragraph("VERTRAGS-NR.", st["meta_label"]),
        Paragraph(f"<b>{_xml_escape(contract_no)}</b>", st["meta_value"]),
        Spacer(1, 5),
        Paragraph("DATUM", st["meta_label"]),
        Paragraph(f"<b>{today}</b>", st["meta_value"]),
    ]
    head = Table([[header_left, header_right]], colWidths=[CONTENT_W - 4.5 * cm, 4.5 * cm])
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(head)
    story.append(Spacer(1, 8))

    # Accent line under the letterhead
    line = Table([[""]], colWidths=[CONTENT_W], rowHeights=[2])
    line.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT)]))
    story.append(line)
    story.append(Spacer(1, 12))

    # ---------- Parties — boxed, side by side ----------
    seller_rows = [
        ("Name / Firma", contract.get("seller_name", "")),
        ("Anschrift", contract.get("seller_address", "")),
        ("PLZ / Ort", f"{contract.get('seller_zip','')} {contract.get('seller_city','')}".strip()),
        ("Telefon", contract.get("seller_phone", "")),
        ("E-Mail", contract.get("seller_email", "")),
        ("Ausweis", contract.get("id_document", "")),
    ]
    buyer_rows = [
        ("Firma", dealer.get("company_name", "")),
        ("Ansprechpartner", dealer.get("contact_person", "")),
        ("Anschrift", f"{dealer.get('address','')}".strip()),
        ("PLZ / Ort", f"{dealer.get('zip_code','')} {dealer.get('city','')}".strip()),
        ("Telefon", dealer.get("phone", "")),
        ("E-Mail", dealer.get("email", "")),
    ]
    story.append(_two_boxes(
        "Verkäufer (Halter)", seller_rows,
        "Käufer (Händler)", buyer_rows,
        st,
    ))
    story.append(Spacer(1, 12))

    # ---------- Vehicle data — 2 columns ----------
    def _as_int(val):
        """Robuste Int-Konvertierung — Werte können als Number ODER String
        ankommen (Formularfelder, Mobile.de-Scrape, …). Liefert None wenn leer."""
        if val is None or val == "":
            return None
        try:
            if isinstance(val, (int, float)):
                return int(val)
            # Strings können "150.000", "150,000", "150000 km" usw. enthalten.
            s = str(val).strip().replace(".", "").replace(",", "").replace(" ", "")
            digits = "".join(ch for ch in s if ch.isdigit())
            return int(digits) if digits else None
        except (ValueError, TypeError):
            return None

    mileage_int = _as_int(vehicle.get("mileage"))
    mileage_str = f"{mileage_int:,} km".replace(",", ".") if mileage_int is not None else ""
    kw_int = _as_int(vehicle.get("power_kw"))
    ps_int = _as_int(vehicle.get("power_ps"))
    power_str = f"{kw_int} kW / {ps_int} PS" if kw_int is not None and ps_int is not None else (
        f"{kw_int} kW" if kw_int is not None else (f"{ps_int} PS" if ps_int is not None else "")
    )
    cc_int = _as_int(vehicle.get("displacement"))
    cc_str = f"{cc_int} ccm" if cc_int is not None else ""

    veh_rows = [
        ("Marke", vehicle.get("make_label") or vehicle.get("make", "")),
        ("Modell", vehicle.get("model_label") or vehicle.get("model", "")),
        ("Modellbezeichnung", vehicle.get("model_description", "")),
        ("Kategorie", vehicle.get("category_label") or vehicle.get("category", "")),
        ("Erstzulassung", vehicle.get("first_registration", "")),
        ("Kilometerstand", mileage_str),
        ("Kraftstoff", vehicle.get("fuel_label") or vehicle.get("fuel", "")),
        ("Getriebe", vehicle.get("gearbox_label") or vehicle.get("gearbox", "")),
        ("Leistung", power_str),
        ("Hubraum", cc_str),
        ("Farbe", vehicle.get("color", "")),
        ("Türen", vehicle.get("doors", "")),
        ("Sitze", vehicle.get("seats", "")),
        ("FIN", vehicle.get("vin", "")),
        ("Kennzeichen", vehicle.get("license_plate", "")),
        ("Vorhalter", contract.get("previous_owners") or vehicle.get("previous_owners", "")),
    ]
    story.append(_section("1 · Fahrzeugdaten", st))
    story.append(Spacer(1, 6))
    story.append(_two_col_kv(veh_rows, st))
    story.append(Spacer(1, 12))

    # ---------- Zusicherungen & Zustand — manual fields entered by dealer ----------
    hu_value = (
        f"{_yn(contract.get('hu_valid'))}"
        + (f", gültig bis {contract['hu_until']}" if contract.get("hu_until") else "")
    )
    accident_value = _yn(contract.get("accident_free"))
    if contract.get("accident_free", "").strip().lower() == "nein" and contract.get("accident_location"):
        accident_value = f"Nein (Schaden: {contract['accident_location']})"

    zus_rows = [
        ("Bereifung", contract.get("tires") or "—"),
        ("HU/AU", hu_value or "—"),
        ("Unfallfrei", accident_value),
        ("EU-Import", _yn(contract.get("eu_import"))),
        ("Fahrtauglich", _yn(contract.get("drivable"))),
        ("Gewerblich genutzt seit EZ", _yn(contract.get("commercial_since_ez"))),
        ("Unfallschaden (Inserat)", "Nein" if not vehicle.get("accident_damaged") else "Ja"),
        ("Fahrbereit (Inserat)", "Ja" if vehicle.get("roadworthy", True) else "Nein"),
    ]
    story.append(_section("2 · Zusicherungen & Zustand", st))
    story.append(Spacer(1, 6))
    story.append(_two_col_kv(zus_rows, st))
    story.append(Spacer(1, 12))

    # ---------- Schäden / Beschädigungen — aus interaktiver Skizze ----------
    damages_text = (contract.get("damages_text") or "").strip()
    damages_list = contract.get("damages") or []
    if damages_text or damages_list:
        story.append(_section("Schäden / Beschädigungen", st))
        story.append(Spacer(1, 6))
        if damages_text:
            for line_txt in damages_text.split("\n"):
                line_txt = _xml_escape(line_txt.strip())
                if line_txt:
                    story.append(Paragraph(line_txt, st["body"]))
                    story.append(Spacer(1, 1))
        elif damages_list:
            # Fallback if only the array was sent.
            for d in damages_list:
                tl = _xml_escape(str(d.get("type_label") or d.get("type_key") or "Schaden"))
                zone = _xml_escape(str(d.get("zone") or ""))
                story.append(Paragraph(f"• {tl}: {zone}", st["body"]))
                story.append(Spacer(1, 1))
        story.append(Paragraph(
            "<i>Erfassung erfolgte vor Übergabe gemeinsam mit dem Verkäufer "
            "anhand der Fahrzeugskizze. Markierungen siehe interne Dokumentation.</i>",
            st["small"],
        ))
        story.append(Spacer(1, 12))

    # ---------- Features ----------
    feats = vehicle.get("features") or []
    if feats:
        story.append(_section("Ausstattung laut Inserat / Verkäuferangaben", st))
        story.append(Spacer(1, 6))
        col_count = 3
        rows_data = []
        for i in range(0, len(feats), col_count):
            row = feats[i:i+col_count]
            while len(row) < col_count:
                row.append("")
            rows_data.append([Paragraph(f"• {_xml_escape(str(x))}", st["body"]) if x else "" for x in row])
        t = Table(rows_data, colWidths=[CONTENT_W / col_count] * col_count)
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        story.append(t)
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "<i>Ausstattung laut Inseratsangaben. Vor Vertragsabschluss vom Händler zu prüfen.</i>",
            st["small"],
        ))
        story.append(Spacer(1, 12))

    # ---------- Price & terms ----------
    price_str = (
        f"{contract.get('purchase_price', 0):,.2f} EUR"
        .replace(",", "X").replace(".", ",").replace("X", ".")
    )
    pay_bits = [("Zahlungsart", contract.get("payment_method", "Bar / Überweisung"))]
    if contract.get("pickup_date"):
        pay_bits.append(("Abholdatum", contract.get("pickup_date", "")))
    if contract.get("pickup_time"):
        pay_bits.append(("Abholuhrzeit", contract.get("pickup_time", "")))
    pay_sub = "   ·   ".join(
        f"{k}: {_xml_escape(str(v))}" for k, v in pay_bits if str(v).strip()
    )
    price_box = Table([
        [
            Paragraph("KAUFPREIS (VEREINBART)", st["price_label"]),
            Paragraph(f"<b>{price_str}</b>", st["price_value"]),
        ],
        [
            Paragraph("inkl. aller Bestandteile lt. Vertrag", st["price_label"]),
            Paragraph(pay_sub or "—", st["price_sub"]),
        ],
    ], colWidths=[CONTENT_W * 0.45, CONTENT_W * 0.55])
    price_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), DARK),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ("TOPPADDING", (0, 1), (-1, 1), 2),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, ACCENT),
    ]))
    story.append(KeepTogether([
        _section("3 · Kaufpreis & Konditionen", st),
        Spacer(1, 6),
        price_box,
    ]))

    extra = (contract.get("additional_terms") or "").strip()
    if extra:
        story.append(Spacer(1, 8))
        story.append(Paragraph("<b>Besondere Vereinbarungen</b>", st["body"]))
        story.append(Spacer(1, 2))
        for para in extra.split("\n\n"):
            # Escape user content first, then restore intentional <br/> line-breaks.
            txt = _xml_escape(para).replace("\n", "<br/>").strip()
            if txt:
                story.append(Paragraph(txt, st["body"]))
                story.append(Spacer(1, 2))
    notes = (contract.get("notes") or "").strip()
    if notes:
        story.append(Spacer(1, 4))
        story.append(Paragraph(f"<b>Notizen (intern):</b> {_xml_escape(notes)}", st["small"]))

    # ---------- Vehicle description (from listing or manually edited in dialog) ----------
    vd = (contract.get("vehicle_description") or "").strip()
    if vd:
        story.append(Spacer(1, 12))
        story.append(_section("Fahrzeugbeschreibung (vom Inserat)", st))
        story.append(Spacer(1, 6))
        for para in vd.split("\n\n"):
            txt = _xml_escape(para).replace("\n", "<br/>").strip()
            if txt:
                story.append(Paragraph(txt, st["body"]))
                story.append(Spacer(1, 2))

    # ---------- Disclaimer ----------
    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "<b>Gewährleistung:</b> Das Fahrzeug wird unter Ausschluss jeglicher Gewährleistung verkauft, "
        "soweit gesetzlich zulässig. Eigenschaftszusicherungen siehe oben. "
        "Der Käufer ist Händler im Sinne des § 14 BGB.",
        st["body"],
    ))

    # ---------- AGB ----------
    agb = (contract.get("agb_text") or "").strip()
    if agb:
        story.append(Spacer(1, 12))
        story.append(_section("Allgemeine Geschäftsbedingungen", st))
        story.append(Spacer(1, 6))
        for para in agb.split("\n\n"):
            txt = _xml_escape(para).replace("\n", "<br/>").strip()
            if txt:
                story.append(Paragraph(txt, st["small"]))
                story.append(Spacer(1, 4))

    # ---------- Signatures — boxed, kept on one page ----------
    def _sig_box(role):
        t = Table([
            [Paragraph(f"<b>{role}</b>", st["sig_label"])],
            [Spacer(1, 34)],
            [Paragraph("Ort, Datum", st["sig_label"])],
            [Spacer(1, 22)],
            [Paragraph("Unterschrift", st["sig_label"])],
        ], colWidths=[COL_W])
        t.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, DIVIDER),
            ("BACKGROUND", (0, 0), (0, 0), LIGHT),
            ("LINEBELOW", (0, 0), (0, 0), 0.5, DIVIDER),
            ("LINEBELOW", (0, 1), (0, 1), 0.5, GREY),   # Ort/Datum line
            ("LINEBELOW", (0, 3), (0, 3), 0.5, GREY),   # Unterschrift line
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (0, 0), 5),
            ("BOTTOMPADDING", (0, 0), (0, 0), 5),
            ("BOTTOMPADDING", (0, -1), (0, -1), 6),
        ]))
        return t

    sig = Table(
        [[_sig_box("Verkäufer / Halter"), "", _sig_box("Käufer / Händler")]],
        colWidths=[COL_W, 0.5 * cm, COL_W],
    )
    sig.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(Spacer(1, 20))
    story.append(KeepTogether([
        _section("Unterschriften", st),
        Spacer(1, 8),
        sig,
        Spacer(1, 4),
        Paragraph(
            "Mit ihrer Unterschrift bestätigen beide Parteien die Richtigkeit "
            "aller Angaben sowie den Erhalt einer Vertragsausfertigung.",
            st["small"],
        ),
    ]))

    footer_left = company
    footer_center = f"Kaufvertrag {contract_no} · erstellt am {today}"
    doc.build(story, canvasmaker=_numbered_canvas_factory(footer_left, footer_center))
    return buf.getvalue()
