"""PDF generation for car purchase contracts (Kaufvertrag) using ReportLab."""
import io
from datetime import datetime
from xml.sax.saxutils import escape as _xml_escape
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
)
from reportlab.lib.enums import TA_LEFT

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


PRIMARY = colors.HexColor("#0A0A0A")
ACCENT = colors.HexColor("#FF3B30")
GREY = colors.HexColor("#71717A")
DIVIDER = colors.HexColor("#E4E4E7")


def _styles():
    s = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=s["Title"], fontSize=22, leading=26,
                                textColor=PRIMARY, alignment=TA_LEFT, spaceAfter=8),
        "h2": ParagraphStyle("h2", parent=s["Heading2"], fontSize=11, leading=14,
                             textColor=PRIMARY, spaceBefore=10, spaceAfter=4),
        "label": ParagraphStyle("label", parent=s["Normal"], fontSize=7, leading=9,
                                textColor=GREY, alignment=TA_LEFT),
        "value": ParagraphStyle("value", parent=s["Normal"], fontSize=9, leading=11,
                                textColor=PRIMARY, alignment=TA_LEFT),
        "small": ParagraphStyle("small", parent=s["Normal"], fontSize=8, leading=11,
                                textColor=GREY),
        "body": ParagraphStyle("body", parent=s["Normal"], fontSize=9, leading=12,
                               textColor=PRIMARY),
    }


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


def _two_column_block(left_title, left_rows, right_title, right_rows, st):
    """Place two key-value blocks side by side."""
    col_w = 8.0 * cm
    label_w = 2.6 * cm
    val_w = col_w - label_w - 0.2 * cm

    left_box = [
        Paragraph(left_title, st["h2"]),
        _kv_compact(left_rows, st, label_w, val_w),
    ]
    right_box = [
        Paragraph(right_title, st["h2"]),
        _kv_compact(right_rows, st, label_w, val_w),
    ]
    t = Table([[left_box, right_box]], colWidths=[col_w, col_w], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 12),
        ("LEFTPADDING", (1, 0), (1, 0), 12),
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
    col_w = 8.0 * cm
    label_w = 3.2 * cm
    val_w = col_w - label_w - 0.2 * cm
    left_t = _kv_compact(left, st, label_w, val_w)
    right_t = _kv_compact(right, st, label_w, val_w) if any(r[0] for r in right) else Paragraph("", st["value"])
    t = Table([[left_t, right_t]], colWidths=[col_w, col_w], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 12),
        ("LEFTPADDING", (1, 0), (1, 0), 12),
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


def generate_contract_pdf(*, dealer: dict, vehicle: dict, contract: dict) -> bytes:
    """Build a Kaufvertrag PDF and return raw bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm, topMargin=1.8*cm, bottomMargin=1.8*cm,
        title="Kaufvertrag", author=dealer.get("company_name", "Autohändler"),
    )
    st = _styles()
    story = []

    today = datetime.now().strftime("%d.%m.%Y")

    # Header
    header_left = Paragraph(
        "<b>KAUFVERTRAG</b><br/>"
        "<font size=8 color='#71717A'>für ein gebrauchtes Kraftfahrzeug</font>",
        st["title"],
    )
    header_right = Paragraph(f"<font size=9>Datum<br/><b>{today}</b></font>", st["value"])
    head = Table([[header_left, header_right]], colWidths=[12*cm, 4.5*cm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(head)
    story.append(Spacer(1, 6))

    # Accent line
    line = Table([[""]], colWidths=[16.5*cm], rowHeights=[2])
    line.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT)]))
    story.append(line)
    story.append(Spacer(1, 10))

    # Parties — side by side
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
    story.append(_two_column_block(
        "Verkäufer (Halter)", seller_rows,
        "Käufer (Händler)", buyer_rows,
        st,
    ))
    story.append(Spacer(1, 8))

    # Vehicle data — 2 columns
    story.append(Paragraph("Fahrzeugdaten", st["h2"]))

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
    story.append(_two_col_kv(veh_rows, st))
    story.append(Spacer(1, 8))

    # Zusicherungen & Zustand — manual fields entered by dealer
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
    story.append(Paragraph("Zusicherungen & Zustand", st["h2"]))
    story.append(_two_col_kv(zus_rows, st))
    story.append(Spacer(1, 8))

    # Schäden / Beschädigungen — aus interaktiver Skizze
    damages_text = (contract.get("damages_text") or "").strip()
    damages_list = contract.get("damages") or []
    if damages_text or damages_list:
        story.append(Paragraph("Schäden / Beschädigungen", st["h2"]))
        if damages_text:
            for line in damages_text.split("\n"):
                line = _xml_escape(line.strip())
                if line:
                    story.append(Paragraph(line, st["body"]))
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
        story.append(Spacer(1, 8))

    # Features
    feats = vehicle.get("features") or []
    if feats:
        story.append(Paragraph("Ausstattung laut Inserat / Verkäuferangaben", st["h2"]))
        col_count = 3
        rows_data = []
        for i in range(0, len(feats), col_count):
            row = feats[i:i+col_count]
            while len(row) < col_count:
                row.append("")
            rows_data.append([Paragraph(f"• {_xml_escape(str(x))}", st["body"]) if x else "" for x in row])
        t = Table(rows_data, colWidths=[5.5*cm]*col_count)
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        story.append(t)
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "<i>Ausstattung laut Inseratsangaben. Vor Vertragsabschluss vom Händler zu prüfen.</i>",
            st["small"],
        ))
        story.append(Spacer(1, 6))

    # Price & terms
    story.append(Paragraph("Kaufpreis & Konditionen", st["h2"]))
    price_str = (
        f"{contract.get('purchase_price', 0):,.2f} EUR"
        .replace(",", "X").replace(".", ",").replace("X", ".")
    )
    story.append(_two_col_kv([
        ("Kaufpreis (vereinbart)", price_str),
        ("Zahlungsart", contract.get("payment_method", "Bar / Überweisung")),
        ("Abholdatum", contract.get("pickup_date", "")),
        ("Abholuhrzeit", contract.get("pickup_time", "")),
    ], st))
    extra = (contract.get("additional_terms") or "").strip()
    if extra:
        story.append(Spacer(1, 4))
        story.append(Paragraph("<b>Besondere Vereinbarungen</b>", st["body"]))
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

    # Vehicle description (from listing or manually edited in dialog).
    vd = (contract.get("vehicle_description") or "").strip()
    if vd:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Fahrzeugbeschreibung (vom Inserat)", st["h2"]))
        for para in vd.split("\n\n"):
            txt = _xml_escape(para).replace("\n", "<br/>").strip()
            if txt:
                story.append(Paragraph(txt, st["body"]))
                story.append(Spacer(1, 2))

    # Disclaimer
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "<b>Gewährleistung:</b> Das Fahrzeug wird unter Ausschluss jeglicher Gewährleistung verkauft, "
        "soweit gesetzlich zulässig. Eigenschaftszusicherungen siehe oben. "
        "Der Käufer ist Händler im Sinne des § 14 BGB.",
        st["body"],
    ))

    # AGB
    agb = (contract.get("agb_text") or "").strip()
    if agb:
        story.append(Spacer(1, 12))
        story.append(Paragraph("Allgemeine Geschäftsbedingungen", st["h2"]))
        for para in agb.split("\n\n"):
            txt = _xml_escape(para).replace("\n", "<br/>").strip()
            if txt:
                story.append(Paragraph(txt, st["small"]))
                story.append(Spacer(1, 4))

    # Signatures
    story.append(Spacer(1, 18))
    sig = Table([
        [
            Paragraph("__________________________<br/><font size=8 color='#71717A'>Verkäufer / Halter</font>", st["body"]),
            Paragraph("__________________________<br/><font size=8 color='#71717A'>Käufer / Händler</font>", st["body"]),
        ],
        [
            Paragraph(f"<font size=8 color='#71717A'>Ort, Datum: {today}</font>", st["body"]),
            Paragraph(f"<font size=8 color='#71717A'>Ort, Datum: {today}</font>", st["body"]),
        ],
    ], colWidths=[8.25*cm, 8.25*cm])
    sig.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                             ("TOPPADDING", (0, 0), (-1, -1), 4)]))
    story.append(sig)

    doc.build(story)
    return buf.getvalue()
