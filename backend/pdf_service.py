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
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, Flowable,
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

# Digitale Ausfertigung (Wunsch Ahmad 09.09.2026): Wird der Vertrag per
# E-Mail oder WhatsApp verschickt, gibt es keine Unterschriftslinien —
# unter "Unterschriften" steht stattdessen dieser Text. Firma (Chef) und
# Sucher koennen ihn in den Einstellungen dauerhaft durch einen eigenen
# ersetzen (dealers.digital_vertragstext bzw. Sucher-Override); leer =
# dieser Standard. Absaetze durch Leerzeile trennen.
DIGITAL_VERTRAGSTEXT_STANDARD = (
    "Folgende Vertragsbedingungen werden beidseitig eingewilligt.\n\n"
    "1. Der/Die Verkäufer*in übernimmt nach der Fahrzeugübergabe keine "
    "Garantie oder Gewährleistung für das Fahrzeug.\n\n"
    "2. Mündliche und schriftliche Absagen sind nach Vertragsbestätigung "
    "aufgrund anfallender Kosten nicht wirksam.\n\n"
    "3. Der/Die Verkäufer*in bestätigt, dass die oben festgehaltenen Daten "
    "überprüft wurden und ihrer Richtigkeit entsprechen.\n\n"
    "4. Dieser Vertrag ist rechtskräftig, verbindlich und auch ohne "
    "Unterschrift gültig."
)


# Hinweis fuer Altvertraege (vor Einfuehrung der Vertragsbedingungen): steht
# in der digitalen Fassung unter "Unterschriften" — nie als Vertragstext.
DIGITAL_NACHTRAEGLICH = (
    "Diese digitale Ausfertigung wurde nachträglich erzeugt.\n\n"
    "Der Vertrag wurde vor Einführung der digitalen Ausfertigung geschlossen. "
    "Für ihn sind keine Vertragsbedingungen gespeichert; es gelten "
    "ausschließlich die oben aufgeführten Vertragsangaben und die unterschriebene "
    "Ausfertigung."
)


def digitaler_vertragstext(dealer: dict) -> str:
    """Wirksamer Text fuer die digitale Ausfertigung: eigener Text der Firma
    bzw. des Suchers (effective_dealer), sonst der Standard."""
    eigen = ((dealer or {}).get("digital_vertragstext") or "").strip()
    return eigen or DIGITAL_VERTRAGSTEXT_STANDARD


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
    # Runde 22: keine Fuellzeile mehr bei ungerader Zeilenzahl — sie erschien
    # als leere Zeile mit "—" (seit der Zeile "Zulassung" in Abschnitt 2).
    # Die Spalten stehen oben buendig (VALIGN TOP), unterschiedliche Hoehe stoert nicht.
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


def _zulassung_anzeige(value):
    """Runde 22 (11.09.2026): Zulassungsstatus fuer die Zusicherungen —
    nur die beiden bekannten Werte, alles andere (auch leer) als '—'."""
    return {"angemeldet": "Angemeldet", "abgemeldet": "Abgemeldet"}.get(
        str(value or "").strip().lower(), "—")


# ---------- Empfangsbestaetigung (Runde 22, 11.09.2026, Vorlage Ahmad) ----------
# Im Abschnitt "Unterschriften" je Partei ein Kasten: Kaeufer bestaetigt den
# Empfang von Zulassungsbescheinigung Teil I & II und KFZ mit n Schluessel(n),
# Verkaeufer den Empfang des Kaufpreises; darunter "Datum und Ort". Es steht
# nur, was im Vertrag erfasst ist — Altvertraege ohne Felder bekommen leere
# Kaestchen und eine Linie zum Ausfuellen von Hand.
class _Kaestchen(Flowable):
    """Ankreuz-Kaestchen, selbst gezeichnet: Quadrat, bei an=True mit Haken.
    Bewusst kein Unicode-Zeichen (☐/☒) — die Standardschrift Helvetica hat
    diese Zeichen nicht, sie erschienen als schwarze Kaesten."""

    def __init__(self, an=False, groesse=8):
        super().__init__()
        self.an = bool(an)
        self.groesse = groesse
        self.width = self.height = groesse

    def wrap(self, avail_w, avail_h):
        return self.groesse, self.groesse

    def draw(self):
        s = self.groesse
        c = self.canv
        c.saveState()
        c.setStrokeColor(PRIMARY)
        c.setLineWidth(0.7)
        c.rect(0, 0, s, s, stroke=1, fill=0)
        if self.an:
            # Haken aus zwei Strichen
            c.setLineWidth(1.2)
            p = c.beginPath()
            p.moveTo(s * 0.18, s * 0.52)
            p.lineTo(s * 0.42, s * 0.2)
            p.lineTo(s * 0.86, s * 0.84)
            c.drawPath(p, stroke=1, fill=0)
        c.restoreState()


def _angekreuzt(value) -> bool:
    """True/False aus contract_data; Texte alter Clients ("true", "ja")
    werden verstanden, alles andere gilt als nicht angekreuzt."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "ja", "yes", "x")
    return bool(value)


def _empfang_datum_ort(datum_iso, ort) -> str:
    """'18.08.2026, Rensenheim' — nur, was im Vertrag steht. Runde 22
    (11.09.2026, Gegenpruefung): fehlt nur ein Teil, steht an seiner Stelle
    eine Linie zum Ausfuellen von Hand ('18.08.2026, ______________' bzw.
    '__________, Rensenheim') — das Formular fuellt das Datum immer vor, der
    Verkaeufer-Ort fehlt aber, wenn das Inserat keinen Ort hat. Fehlt beides:
    eine durchgehende Linie."""
    datum = str(datum_iso or "").strip()
    if datum:
        try:
            from datetime import date as _date
            datum = _date.fromisoformat(datum).strftime("%d.%m.%Y")
        except (ValueError, TypeError):
            pass
    ort = str(ort or "").strip()
    if not datum and not ort:
        return "_" * 26
    return f"{datum or '_' * 10}, {ort or '_' * 14}"


def _empfang_block(seite, contract, st, breite):
    """Inhalt der Empfangsbestaetigung einer Partei (seite: "kaeufer" |
    "verkaeufer") als randlose Tabelle der Breite `breite`."""
    c = contract or {}
    if seite == "kaeufer":
        anzahl = str(c.get("schluessel_anzahl") or "").strip()
        punkte = [
            (c.get("empfang_zulassungsbescheinigung"), "Zulassungsbescheinigung Teil I & II"),
            (c.get("empfang_schluessel"), f"KFZ mit {anzahl or '____'} Schlüssel(n)"),
        ]
        ort = c.get("empfang_ort_kaeufer")
    else:
        punkte = [(c.get("empfang_kaufpreis"), "Kaufpreis")]
        ort = c.get("empfang_ort_verkaeufer")
    rows = [[Paragraph("<b>bestätigt Empfang von:</b>", st["sig_label"]), ""]]
    for an, text in punkte:
        rows.append([_Kaestchen(_angekreuzt(an)),
                     Paragraph(_xml_escape(text), st["value"])])
    # Hoehenausgleich: beide Kaesten stehen nebeneinander gleich hoch.
    while len(rows) < 3:
        rows.append(["", Paragraph("&nbsp;", st["value"])])
    datum_ort = _empfang_datum_ort(c.get("empfang_datum"), ort)
    rows.append([Paragraph("Datum und Ort: " + _xml_escape(datum_ort),
                           st["value"]), ""])
    t = Table(rows, colWidths=[0.5 * cm, breite - 0.5 * cm])
    t.setStyle(TableStyle([
        ("SPAN", (0, 0), (1, 0)),
        ("SPAN", (0, -1), (1, -1)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, -1), (-1, -1), 6),
    ]))
    return t


def _empfang_kasten(rolle, seite, contract, st, unterschrift):
    """Kasten einer Partei im Abschnitt "Unterschriften": Titel,
    "bestätigt Empfang von:" mit Kaestchen, "Datum und Ort". Druckfassung
    (unterschrift=True) zusaetzlich mit der Unterschriftslinie; die digitale
    Fassung ohne. Die fruehere Linie "Ort, Datum" entfaellt (Beschluss
    11.09.2026, wie Ahmads Vorlage): "Datum und Ort" steht schon im Kasten —
    zwei Datums-/Ortsangaben je Partei verwirrten."""
    rows = [
        [Paragraph(f"<b>{_xml_escape(rolle)}</b>", st["sig_label"])],
        [_empfang_block(seite, contract, st, COL_W - 16)],
    ]
    stil = [
        ("BOX", (0, 0), (-1, -1), 0.5, DIVIDER),
        ("BACKGROUND", (0, 0), (0, 0), LIGHT),
        ("LINEBELOW", (0, 0), (0, 0), 0.5, DIVIDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (0, 0), 5),
        ("BOTTOMPADDING", (0, 0), (0, 0), 5),
        ("TOPPADDING", (0, 1), (0, 1), 5),
        ("BOTTOMPADDING", (0, -1), (0, -1), 6),
    ]
    if unterschrift:
        rows += [
            [Spacer(1, 34)],
            [Paragraph("Unterschrift", st["sig_label"])],
        ]
        stil += [
            ("LINEBELOW", (0, 2), (0, 2), 0.5, GREY),   # Unterschrift line
        ]
    t = Table(rows, colWidths=[COL_W])
    t.setStyle(TableStyle(stil))
    return t


def _empfang_paar(contract, st, unterschrift):
    """Beide Kaesten nebeneinander — Verkaeufer links, Kaeufer rechts
    (wie die Parteien oben im Vertrag)."""
    t = Table(
        [[_empfang_kasten("Verkäufer / Halter", "verkaeufer", contract, st, unterschrift),
          "",
          _empfang_kasten("Käufer / Händler", "kaeufer", contract, st, unterschrift)]],
        colWidths=[COL_W, 0.5 * cm, COL_W],
    )
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


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


def _abholzeile(contract: dict) -> str:
    """Abholung als EINE Zeile: "Wird abgeholt am 19.11.2026 um 10:00 Uhr,
    <Anschrift des Verkaeufers>". Leer, wenn kein Abholdatum im Vertrag steht.

    Wunsch Ahmad (12.09.2026): Die Zeile steht jetzt unter den Halter- und
    Kaeuferangaben statt im Kaufpreis-Kasten.
    """
    if not contract.get("pickup_date"):
        return ""
    datum = str(contract.get("pickup_date", ""))
    try:
        from datetime import date as _date
        datum = _date.fromisoformat(datum).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        pass
    abhol = f"Wird abgeholt am {datum}"
    if str(contract.get("pickup_time") or "").strip():
        abhol += f" um {contract['pickup_time']} Uhr"
    adresse = ", ".join(x for x in [
        (contract.get("seller_address") or "").strip(),
        " ".join(y for y in [
            (contract.get("seller_zip") or "").strip(),
            (contract.get("seller_city") or "").strip()] if y),
    ] if x)
    if adresse:
        abhol += f", {adresse}"
    return abhol


def generate_contract_pdf(*, dealer: dict, vehicle: dict, contract: dict,
                          digital: bool = False) -> bytes:
    """Build a Kaufvertrag PDF and return raw bytes.

    digital=True: Ausfertigung fuer den Versand per E-Mail/WhatsApp — ohne
    Unterschriftslinien; unter "Unterschriften" steht der digitale
    Vertragstext (contract["digital_vertragstext"], sonst Standard)."""
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

    # Abholung direkt unter den Halter-/Kaeuferangaben (Wunsch Ahmad 12.09.2026).
    _abhol = _abholzeile(contract)
    if _abhol:
        story.append(Paragraph(f"<b>Abholung:</b> {_xml_escape(_abhol)}", st["body"]))
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
        # Runde 22 (11.09.2026, Vorlage Ahmad): angemeldet oder abgemeldet.
        ("Zulassung", _zulassung_anzeige(contract.get("zulassung"))),
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
    damage_note = (contract.get("vehicle_damage_note") or "").strip()
    if damages_text or damages_list or damage_note:
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
                if not isinstance(d, dict):
                    # Nachpruefung Runde 14: Freitext-Eintraege (Strings) sind
                    # erlaubt — vorher stuerzte `d.get` hier mit 500 ab.
                    story.append(Paragraph(f"• {_xml_escape(str(d or ''))}", st["body"]))
                    story.append(Spacer(1, 1))
                    continue
                tl = _xml_escape(str(d.get("type_label") or d.get("type_key")
                                     or d.get("label") or d.get("type") or "Schaden"))
                zone = _xml_escape(str(d.get("zone") or d.get("part_label") or d.get("part") or ""))
                story.append(Paragraph(f"• {tl}: {zone}" if zone else f"• {tl}", st["body"]))
                story.append(Spacer(1, 1))
        if damage_note:
            # Freitextfeld "Sonstige Schäden / Hinweis" aus dem Formular —
            # stand bisher nur in der Datenbank, nie im Vertrag.
            story.append(Spacer(1, 4))
            story.append(Paragraph(
                f"<b>Sonstige Schäden / Hinweis:</b> {_xml_escape(damage_note)}",
                st["body"],
            ))
            story.append(Spacer(1, 2))
        if damages_text or damages_list:
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
    def _eur(betrag):
        return (f"{betrag:,.2f} EUR"
                .replace(",", "X").replace(".", ",").replace("X", "."))

    brutto = float(contract.get("purchase_price") or 0)
    price_str = _eur(brutto)

    # MwSt-Ausweis (gewerblicher Verkauf, Regelbesteuerung): Kaufpreis ist
    # der Bruttobetrag, Netto und Steuer werden daraus gerechnet.
    if contract.get("show_vat"):
        netto = brutto / 1.19
        mwst = brutto - netto
        preis_label = (f"Netto {_eur(netto)}   ·   "
                       f"zzgl. 19 % MwSt {_eur(mwst)}")
    else:
        preis_label = "inkl. aller Bestandteile lt. Vertrag"

    pay_bits = [("Zahlungsart", contract.get("payment_method", "Bar / Überweisung"))]
    pay_sub = "   ·   ".join(
        (f"{k}: {_xml_escape(str(v))}" if k else _xml_escape(str(v)))
        for k, v in pay_bits if str(v).strip()
    )
    price_box = Table([
        [
            Paragraph("KAUFPREIS (VEREINBART)", st["price_label"]),
            Paragraph(f"<b>{price_str}</b>", st["price_value"]),
        ],
        [
            Paragraph(preis_label, st["price_label"]),
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

    # ---------- Allgemeine Vertragsbedingungen (Beschluss Ahmad 10.09.2026) ----------
    # Der Standardtext (vier Klauseln) bzw. der in den Einstellungen
    # gespeicherte Text steht in JEDER Fassung unter einer eigenen
    # Ueberschrift — nicht mehr unter "Unterschriften". Immer nur der bei
    # der Erstellung festgehaltene Text (contract_data); Altvertraege ohne
    # Text haben den Abschnitt nicht (DIGITAL_NACHTRAEGLICH ist ein Hinweis
    # fuer die Unterschriften-Zeile, kein Vertragstext).
    avb = (contract.get("digital_vertragstext") or "").strip()
    nachtraeglich = bool(avb) and avb == DIGITAL_NACHTRAEGLICH.strip()
    if avb and not nachtraeglich:
        story.append(Spacer(1, 12))
        story.append(_section("Allgemeine Vertragsbedingungen", st))
        story.append(Spacer(1, 6))
        for para in avb.split("\n\n"):
            txt = _xml_escape(para).replace("\n", "<br/>").strip()
            if txt:
                story.append(Paragraph(txt, st["body"]))
                story.append(Spacer(1, 4))

    # ---------- Digitale Ausfertigung: ein Satz statt Unterschriftslinien ----------
    if digital:
        # Runde 22 (11.09.2026): Empfangsbestaetigung beider Parteien (ohne
        # Unterschriftslinie) vor dem Satz zur Gueltigkeit.
        block = [_section("Unterschriften", st), Spacer(1, 8),
                 _empfang_paar(contract, st, unterschrift=False), Spacer(1, 8),
                 Paragraph("Dieser Vertrag ist ohne Unterschrift gültig.", st["body"])]
        if nachtraeglich:
            block.append(Spacer(1, 8))
            for para in avb.split("\n\n"):
                txt = _xml_escape(para).replace("\n", "<br/>").strip()
                if txt:
                    block.append(Paragraph(txt, st["small"]))
                    block.append(Spacer(1, 3))
        story.append(Spacer(1, 20))
        story.append(KeepTogether(block))
        footer_left = company
        footer_center = f"Kaufvertrag {contract_no} · erstellt am {today} · digitale Ausfertigung"
        doc.build(story, canvasmaker=_numbered_canvas_factory(footer_left, footer_center))
        return buf.getvalue()

    # ---------- Signatures — boxed, kept on one page ----------
    # Runde 22 (11.09.2026, Vorlage Ahmad): Die Unterschriftskaesten
    # ("Verkäufer / Halter" links, "Käufer / Händler" rechts) tragen unter
    # dem Titel die Empfangsbestaetigung mit Ankreuz-Kaestchen und "Datum
    # und Ort", darunter wie bisher die Linien — siehe _empfang_kasten.
    sig = _empfang_paar(contract, st, unterschrift=True)
    story.append(Spacer(1, 20))
    story.append(KeepTogether([
        _section("Unterschriften", st),
        Spacer(1, 8),
        sig,
        Spacer(1, 4),
        Paragraph(
            # Wunsch Ahmad (12.09.2026): Der Satz deckt auch den Fall ab, dass der
            # Vertrag elektronisch uebermittelt wurde — ein Kfz-Kaufvertrag ist
            # formfrei, eine eigenhaendige Unterschrift also nicht noetig.
            "Mit ihrer Unterschrift bestätigen beide Parteien die Richtigkeit "
            "aller Angaben sowie den Erhalt einer Vertragsausfertigung. Wird dieser "
            "Vertrag elektronisch übermittelt, gilt die Bestätigung der Vertragsinhalte "
            "in Textform, zum Beispiel per E-Mail, als Zustimmung beider Parteien; eine "
            "eigenhändige Unterschrift ist dann nicht erforderlich.",
            st["small"],
        ),
    ]))

    footer_left = company
    footer_center = f"Kaufvertrag {contract_no} · erstellt am {today}"
    doc.build(story, canvasmaker=_numbered_canvas_factory(footer_left, footer_center))
    return buf.getvalue()
