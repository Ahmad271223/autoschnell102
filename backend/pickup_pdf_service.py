"""PDF generation for Abholauftrag / Übergabeprotokoll (driver pickup).

The pickup protocol is a driver-facing sibling of the Kaufvertrag PDF. It
auto-fills every known vehicle + seller fact and hands the driver a set of
on-site checks:

  * Fahrzeugdaten        (○ stimmt   ○ weicht ab)
  * Dokumenten-Check     (○ vorhanden ○ fehlt)
  * Ausstattungs-Check   (○ vorhanden ○ fehlt)  — auto-filled from vehicle features
  * Vorbestehende Schäden auf Skizze (aus Kaufvertrag übernommen)
  * Leere Skizze für Vor-Ort-Aufnahme
  * Tankfüllstand, Kilometerstand bei Abholung, Bemerkungen, Unterschriften
"""
from __future__ import annotations

import io
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape as _xe  # escape user-supplied text in Paragraph HTML

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate, Flowable, Frame, KeepTogether, PageBreak, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)

PRIMARY = colors.HexColor("#0A0A0A")
ACCENT = colors.HexColor("#FF3B30")
GREY = colors.HexColor("#71717A")
DIVIDER = colors.HexColor("#E4E4E7")
LIGHT_BG = colors.HexColor("#F4F4F5")

# Blaues Corporate-Design für das Abholprotokoll (Behörden-/Übergabe-Look).
BLUE = colors.HexColor("#1E5BB8")          # Header-Bars + Akzente
BLUE_DARK = colors.HexColor("#164490")     # Hover/Schatten
BLUE_LINK = colors.HexColor("#1E5BB8")     # Auftrags-Nr + E-Mail
BLUE_SOFT = colors.HexColor("#EAF1FB")     # Optional Hintergrundtöne

# Damage sketches live in the frontend's public folder. The backend just
# reads them as static assets. Alle Skizzen sind 1536 × 1024 px (Mai 2026).
SKETCH_DIR = Path("/app/frontend/public/damage")
SKETCHES = {
    "front": {"src": "front.png", "w": 1536, "h": 1024, "label": "Frontansicht"},
    "rear":  {"src": "rear.png",  "w": 1536, "h": 1024, "label": "Heckansicht"},
    "left":  {"src": "left.png",  "w": 1536, "h": 1024, "label": "Fahrerseite (links)"},
    "right": {"src": "right.png", "w": 1536, "h": 1024, "label": "Beifahrerseite (rechts)"},
    "top":   {"src": "top.png",   "w": 1536, "h": 1024, "label": "Draufsicht"},
}


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def _styles() -> Dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontSize=20, leading=24,
                                textColor=PRIMARY, alignment=TA_LEFT, spaceAfter=4),
        "subtitle": ParagraphStyle("subtitle", parent=base["Normal"], fontSize=10,
                                   leading=13, textColor=GREY, spaceAfter=14),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=11, leading=14,
                             textColor=PRIMARY, spaceBefore=10, spaceAfter=5,
                             textTransform="uppercase"),
        "label": ParagraphStyle("label", parent=base["Normal"], fontSize=7, leading=9,
                                textColor=GREY),
        "value": ParagraphStyle("value", parent=base["Normal"], fontSize=9, leading=11,
                                textColor=PRIMARY),
        "body":  ParagraphStyle("body", parent=base["Normal"], fontSize=9, leading=12,
                                textColor=PRIMARY),
        "small": ParagraphStyle("small", parent=base["Normal"], fontSize=7.5,
                                leading=10, textColor=GREY),
        "foot": ParagraphStyle("foot", parent=base["Normal"], fontSize=7, leading=9,
                               textColor=GREY, alignment=TA_CENTER),
        "check_label": ParagraphStyle("check_label", parent=base["Normal"], fontSize=9,
                                      leading=11, textColor=PRIMARY),
        "check_opt": ParagraphStyle("check_opt", parent=base["Normal"], fontSize=8,
                                    leading=10, textColor=PRIMARY, alignment=TA_CENTER),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(v: Any, empty: str = "—") -> str:
    if v is None:
        return empty
    if isinstance(v, float):
        # Kilometer-/Preisformatierung mit Tausender-Punkt und keiner Nachkommastelle.
        return f"{v:,.0f}".replace(",", ".")
    s = str(v).strip()
    return s if s else empty


def _fmt_km(v: Any) -> str:
    if v in (None, "", 0):
        return "—"
    try:
        return f"{int(float(v)):,} km".replace(",", ".")
    except (TypeError, ValueError):
        return str(v)


def _fmt_date(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return str(iso)


def _check_row(label: str, value: Any, options: List[str], st, *, bold_value: bool = False) -> List:
    """Builds a row: [label | value | ○ option1 | ○ option2 | ...]."""
    label_style = ParagraphStyle(
        "rl", parent=st["body"], fontSize=10, leading=13, textColor=BLUE_DARK)
    value_style = ParagraphStyle(
        "rv", parent=st["body"], fontSize=10, leading=13, textColor=PRIMARY,
        fontName="Helvetica-Bold" if bold_value else "Helvetica",
    )
    opt_style = ParagraphStyle(
        "ro", parent=st["body"], fontSize=10, leading=13,
        textColor=GREY, alignment=TA_LEFT,
    )
    row = [Paragraph(label, label_style), Paragraph(_xe(_fmt(value)), value_style)]
    for opt in options:
        row.append(Paragraph(f"○  {opt}", opt_style))
    return row


def _check_table(rows: List[List], col_widths: List[float]) -> Table:
    t = Table(rows, colWidths=col_widths, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, DIVIDER),
    ]))
    return t


def _checklist(items: List[tuple], st, col_count: int = 2) -> Table:
    """Renders a grid of ○ Label / ○ Label items. `items` is list of
    (label, sub_note?) tuples; sub_note is optional gray line."""
    cells = []
    for item in items:
        label, note = (item if isinstance(item, tuple) else (item, ""))
        txt = f"○ {_xe(str(label))}"
        para_html = f"<font size=9 color='#0A0A0A'>{txt}</font>"
        if note:
            para_html += f"<br/><font size=7 color='#71717A'>{_xe(str(note))}</font>"
        cells.append(Paragraph(para_html, st["body"]))

    # Pad to multiple of col_count so Table stays rectangular
    while len(cells) % col_count:
        cells.append(Paragraph("", st["body"]))

    rows = [cells[i:i + col_count] for i in range(0, len(cells), col_count)]
    col_w = (17.5 * cm) / col_count
    t = Table(rows, colWidths=[col_w] * col_count, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


# ---------------------------------------------------------------------------
# Car sketch with damage overlay
# ---------------------------------------------------------------------------

class CarSketch(Flowable):
    """A single car-view PNG rendered inside the PDF, optionally with red
    damage circles overlaid at the coordinates captured in the Kaufvertrag."""

    def __init__(self, view_key: str, damages: List[dict], *,
                 max_width_cm: float = 8.0, empty: bool = False) -> None:
        Flowable.__init__(self)
        meta = SKETCHES[view_key]
        self.png_path = str(SKETCH_DIR / meta["src"])
        self.orig_w = meta["w"]
        self.orig_h = meta["h"]
        self.label = meta["label"]
        self.damages = [d for d in damages if d.get("view") == view_key] if damages else []
        self.empty = empty

        self.max_width = max_width_cm * cm
        self.width = self.max_width
        self.height = self.max_width * (self.orig_h / self.orig_w) + 0.5 * cm  # + label

    # pylint: disable=unused-argument
    def wrap(self, available_width, available_height):
        return self.width, self.height

    def draw(self):
        c = self.canv
        # Label at top
        c.setFillColor(GREY)
        c.setFont("Helvetica-Bold", 7)
        c.drawString(0, self.height - 0.4 * cm, self.label.upper())
        # Marker counter on label row
        if self.damages and not self.empty:
            c.setFillColor(ACCENT)
            c.drawRightString(self.width, self.height - 0.4 * cm,
                              f"{len(self.damages)} MARKIERUNG(EN)")

        img_h = self.height - 0.5 * cm
        # Image
        try:
            if os.path.exists(self.png_path):
                c.drawImage(self.png_path, 0, 0, width=self.width, height=img_h,
                            mask="auto", preserveAspectRatio=True)
            else:
                # Fallback: gray rectangle with view label
                c.setFillColor(LIGHT_BG)
                c.rect(0, 0, self.width, img_h, stroke=0, fill=1)
                c.setFillColor(GREY)
                c.setFont("Helvetica-Oblique", 8)
                c.drawCentredString(self.width / 2, img_h / 2,
                                    f"[Skizze {self.label}]")
        except Exception:
            # Defensive: never let a missing asset break PDF generation.
            c.setFillColor(LIGHT_BG)
            c.rect(0, 0, self.width, img_h, stroke=0, fill=1)

        # Border
        c.setStrokeColor(DIVIDER)
        c.setLineWidth(0.5)
        c.rect(0, 0, self.width, img_h, stroke=1, fill=0)

        if self.empty or not self.damages:
            return

        # Overlay damage circles
        sx = self.width / self.orig_w
        sy = img_h / self.orig_h
        for i, d in enumerate(self.damages, start=1):
            try:
                x = float(d.get("x", 0)) * sx
                # PDF origin is bottom-left, image y is top-down -> flip
                y = img_h - float(d.get("y", 0)) * sy
            except (TypeError, ValueError):
                continue
            color = d.get("color") or "#FF3B30"
            try:
                fill_c = colors.HexColor(color)
            except Exception:
                fill_c = ACCENT
            c.setFillColor(fill_c)
            c.setStrokeColor(colors.black)
            c.setLineWidth(0.8)
            c.circle(x, y, 8, stroke=1, fill=1)
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 7)
            code = d.get("type_abbr") or d.get("abbr") or str(i)
            c.drawCentredString(x, y - 2.2, str(code)[:2])


def _sketch_grid(damages: List[dict], *, empty: bool = False) -> Table:
    """Kompaktes 2-Spalten-Layout (3 Reihen × 2 Spalten) — alle 5 Skizzen
    passen so zuverlässig auf eine einzige PDF-Seite (A4).

        [ front | rear  ]
        [ left  | right ]
        [ top   |   ·   ]
    """
    col_w = 8.6 * cm           # ≈ 17.2 cm Gesamtbreite (A4-Innenmaß ~17.5 cm)
    sketch_w = 8.0             # cm — pro Skizze, lässt etwas Innenrand
    front = CarSketch("front", damages, max_width_cm=sketch_w, empty=empty)
    rear  = CarSketch("rear",  damages, max_width_cm=sketch_w, empty=empty)
    left  = CarSketch("left",  damages, max_width_cm=sketch_w, empty=empty)
    right = CarSketch("right", damages, max_width_cm=sketch_w, empty=empty)
    top   = CarSketch("top",   damages, max_width_cm=sketch_w, empty=empty)

    data = [
        [front, rear],
        [left, right],
        [top, ""],
    ]
    t = Table(data, colWidths=[col_w, col_w], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _damage_legend(damages: List[dict], st) -> Optional[Flowable]:
    if not damages:
        return Paragraph(
            "Keine Schäden im Kaufvertrag dokumentiert.", st["small"])
    # Count by type
    counts: Dict[str, dict] = {}
    for d in damages:
        key = d.get("type") or d.get("type_label") or "unbekannt"
        counts.setdefault(key, {
            "label": d.get("type_label") or key,
            "abbr": d.get("type_abbr") or d.get("abbr") or "?",
            "color": d.get("color") or "#FF3B30",
            "n": 0,
        })
        counts[key]["n"] += 1
    legend_items = []
    for info in counts.values():
        # Escape all user-supplied fields before embedding in Paragraph HTML.
        # color goes inside an attribute value so we also strip quotes.
        safe_color = _xe(str(info["color"])).replace("'", "").replace('"', "")
        legend_items.append(
            f"<font color='{safe_color}'><b>●</b></font> "
            f"<b>{_xe(str(info['abbr']))}</b> — {_xe(str(info['label']))} ({info['n']}x)"
        )
    html = " &nbsp;&nbsp; ".join(legend_items)
    return Paragraph(html, st["small"])


# ---------------------------------------------------------------------------
# Page template / header / footer
# ---------------------------------------------------------------------------

def _make_doc(buf: io.BytesIO) -> BaseDocTemplate:
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        topMargin=2.6 * cm, bottomMargin=1.6 * cm,  # +1cm wegen blauem Header-Bar
        title="Abholprotokoll", author="Autohändle",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin,
                  doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame],
                                       onPage=_draw_chrome)])
    return doc


def _draw_chrome(canvas, doc):
    canvas.saveState()
    page_w, page_h = A4
    # ---- Blauer Header-Bar (volle Breite) ----
    bar_h = 1.7 * cm
    canvas.setFillColor(BLUE)
    canvas.rect(0, page_h - bar_h, page_w, bar_h, stroke=0, fill=1)
    # Titel links, Datum rechts — beides in WEISS auf blau
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(1.8 * cm, page_h - bar_h + 0.55 * cm,
                      "ABHOLPROTOKOLL  ·  ÜBERGABE")
    canvas.setFont("Helvetica", 9)
    canvas.drawRightString(page_w - 1.8 * cm, page_h - bar_h + 0.6 * cm,
                           datetime.now().strftime("%d.%m.%Y · %H:%M"))

    # ---- Footer ----
    canvas.setFillColor(GREY)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(1.8 * cm, 1.0 * cm,
                      "Dieses Protokoll ist vom Fahrer vor Ort auszufüllen und zu unterschreiben.")
    canvas.drawRightString(page_w - 1.8 * cm, 1.0 * cm,
                           f"Seite {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_pickup_pdf(
    *,
    appointment: Dict[str, Any],
    vehicle: Optional[Dict[str, Any]] = None,
    contract: Optional[Dict[str, Any]] = None,
    dealer: Optional[Dict[str, Any]] = None,
    driver: Optional[Dict[str, Any]] = None,
) -> bytes:
    """Returns PDF bytes for the pickup / handover protocol."""
    appointment = appointment or {}
    vehicle = vehicle or {}
    contract = contract or {}
    dealer = dealer or {}
    driver = driver or {}

    # Damages: prefer contract.damages, fallback to vehicle.damages
    damages = (contract.get("damages") or vehicle.get("damages") or [])

    buf = io.BytesIO()
    doc = _make_doc(buf)
    st = _styles()
    story: List[Any] = []

    # -----------------------------------------------------------------
    # PAGE 1 — Auftrags-Kopf · Auftraggeber/Verkäufer · Fahrzeug-Check
    # -----------------------------------------------------------------
    # ---- Auftragsdaten-Kopf (Auftrags-Nr + Abholdatum) ----
    auftrag_nr = (str(appointment.get("id", ""))[:8] or "—").upper()
    pickup_date = _fmt_date(appointment.get("pickup_date"))
    pickup_time = appointment.get("pickup_time") or ""
    abholung_str = pickup_date
    if pickup_time and pickup_date != "—":
        abholung_str = f"{pickup_date} · {pickup_time} Uhr"

    label_top = ParagraphStyle(
        "label_top", parent=st["body"], fontSize=11, leading=15,
        textColor=GREY, fontName="Helvetica",
    )
    value_top = ParagraphStyle(
        "value_top", parent=st["body"], fontSize=12, leading=15,
        textColor=BLUE, fontName="Helvetica-Bold",
    )
    value_top_dark = ParagraphStyle(
        "value_top_dark", parent=st["body"], fontSize=12, leading=15,
        textColor=PRIMARY, fontName="Helvetica-Bold",
    )

    head_table = Table(
        [
            [Paragraph("Auftragsnummer:", label_top),
             Paragraph(auftrag_nr, value_top)],
            [Paragraph("Abholung am:", label_top),
             Paragraph(abholung_str, value_top_dark)],
        ],
        colWidths=[4.0 * cm, 13.5 * cm],
    )
    head_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(Spacer(1, 0.2 * cm))
    story.append(head_table)
    story.append(Spacer(1, 0.6 * cm))

    # ---- Zwei Karten: AUFTRAGGEBER · ABHOLORT/VERKÄUFER ----
    seller_lines: List[str] = []
    seller_name = (appointment.get("seller_name")
                   or contract.get("seller_name") or "")
    if seller_name:
        seller_lines.append(f"<b>{_xe(seller_name)}</b>")
    addr = (appointment.get("pickup_address")
            or " ".join([contract.get("seller_address", ""),
                         contract.get("seller_zip", ""),
                         contract.get("seller_city", "")]).strip())
    if addr:
        seller_lines.append(_xe(addr))
    seller_phone = (appointment.get("seller_phone")
                    or contract.get("seller_phone") or "")
    if seller_phone:
        seller_lines.append(f"Tel.: {_xe(seller_phone)}")
    seller_email = (appointment.get("seller_email")
                    or contract.get("seller_email") or "")
    if seller_email:
        seller_lines.append(
            f"<font color='#1E5BB8'>{_xe(seller_email)}</font>"
        )
    seller_block = "<br/>".join(seller_lines) or "—"

    dealer_lines: List[str] = []
    dealer_name = (dealer.get("company_name") or dealer.get("name") or "")
    if dealer_name:
        dealer_lines.append(f"<b>{_xe(dealer_name)}</b>")
    deal_addr = " ".join([
        dealer.get("address", ""),
        dealer.get("zip_code") or dealer.get("zip", ""),
        dealer.get("city", ""),
    ]).strip()
    if deal_addr:
        dealer_lines.append(_xe(deal_addr))
    if dealer.get("phone"):
        dealer_lines.append(f"Tel.: {_xe(str(dealer['phone']))}")
    if dealer.get("email"):
        dealer_lines.append(
            f"<font color='#1E5BB8'>{_xe(str(dealer['email']))}</font>"
        )
    dealer_block = "<br/>".join(dealer_lines) or "—"

    card_w = 8.55 * cm
    card_body_style = ParagraphStyle(
        "card_body", parent=st["body"], fontSize=10, leading=14,
        textColor=PRIMARY,
    )

    # Header + Body je Card als 2-Zeilen-Tabelle (Header blau, Body weiß)
    def _info_card(header: str, body_html: str) -> Table:
        hdr = Table(
            [[Paragraph(
                f"<font color='#FFFFFF'><b>{header}</b></font>",
                ParagraphStyle("h", parent=st["body"], fontSize=10, leading=12,
                               fontName="Helvetica-Bold"),
            )]],
            colWidths=[card_w],
        )
        hdr.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BLUE),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        body = Table(
            [[Paragraph(body_html, card_body_style)]],
            colWidths=[card_w],
        )
        body.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
            ("BOX", (0, 0), (-1, -1), 0.5, DIVIDER),
        ]))
        return Table([[hdr], [body]], colWidths=[card_w])

    auftrag_card = _info_card("AUFTRAGGEBER", dealer_block)
    abholort_card = _info_card("ABHOLORT / VERKÄUFER", seller_block)
    cards = Table(
        [[auftrag_card, abholort_card]],
        colWidths=[card_w, card_w],
    )
    cards.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 4),  # kleiner Spalt zwischen den Karten
        ("LEFTPADDING", (1, 0), (1, 0), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(cards)
    story.append(Spacer(1, 0.8 * cm))

    # ---- Sektion: FAHRZEUGDATEN — VOR ORT PRÜFEN ----
    h2_blue = ParagraphStyle(
        "h2_blue", parent=st["h2"], fontSize=13, leading=16,
        textColor=BLUE, fontName="Helvetica-Bold",
        spaceBefore=4, spaceAfter=4,
    )
    story.append(Paragraph("FAHRZEUGDATEN — VOR ORT PRÜFEN", h2_blue))
    story.append(Paragraph(
        "Bitte Angaben mit dem Vertrag abgleichen und den zutreffenden Kreis ankreuzen.",
        st["small"]))
    story.append(Spacer(1, 0.25 * cm))

    # Gather values from vehicle + contract
    make = vehicle.get("make") or vehicle.get("brand") or "—"
    model = vehicle.get("model") or "—"
    ez = (vehicle.get("ezl") or vehicle.get("first_registration")
          or vehicle.get("ez") or "—")
    fin = (vehicle.get("vin") or vehicle.get("fin") or "—")
    power_kw = vehicle.get("power_kw") or vehicle.get("kw")
    power_ps = vehicle.get("power_ps") or vehicle.get("ps")
    if power_ps in (None, "", 0) and power_kw not in (None, "", 0):
        try:
            power_ps = round(float(power_kw) * 1.35962)
        except (TypeError, ValueError):
            power_ps = None
    power = f"{_fmt(power_kw)} kW / {_fmt(power_ps)} PS"
    halter = contract.get("previous_owners") or vehicle.get("previous_owners") or "—"
    color = vehicle.get("exterior_color") or vehicle.get("color") or "—"
    fuel = vehicle.get("fuel") or vehicle.get("fuel_type") or "—"
    hu_val = contract.get("hu_valid") or ""
    hu_until = contract.get("hu_until") or vehicle.get("hu") or ""
    hu_disp = f"{hu_val} · gültig bis {hu_until}".strip(" ·") if (hu_val or hu_until) else "—"
    km = _fmt_km(vehicle.get("km") or vehicle.get("mileage"))
    commercial = contract.get("commercial_since_ez") or ""
    accident = contract.get("accident_free") or ""

    col_w = [4.6 * cm, 6.4 * cm, 2.0 * cm, 2.0 * cm, 2.5 * cm]
    check_rows = [
        _check_row("Marke",           make,   ["stimmt", "weicht ab"], st, bold_value=True),
        _check_row("Modell",          model,  ["stimmt", "weicht ab"], st, bold_value=True),
        _check_row("Erstzulassung",   ez,     ["stimmt", "weicht ab"], st),
        _check_row("FIN (Fahrgestell-Nr.)", fin, ["stimmt", "weicht ab"], st),
        _check_row("Leistung",        power,  ["stimmt", "weicht ab"], st),
        _check_row("Halter laut Schein", halter, ["stimmt", "weicht ab"], st),
        _check_row("Farbe",           color,  ["stimmt", "weicht ab"], st),
        _check_row("Kraftstoff",      fuel,   ["stimmt", "weicht ab"], st),
        _check_row("HU",              hu_disp, ["stimmt", "weicht ab"], st),
        _check_row("KM-Stand laut Vertrag", km, ["stimmt", "weicht ab"], st),
        _check_row("Gewerbliche Nutzung", commercial, ["Ja", "Nein", "unbekannt"], st),
        _check_row("Unfallfrei laut Angabe", accident, ["Ja", "Nein", "unbekannt"], st),
    ]
    # Normalise to 5 columns (add empty col for 3-option rows via compute)
    norm = []
    for r in check_rows:
        while len(r) < 5:
            r.append(Paragraph("", st["check_opt"]))
        norm.append(r)

    # Tabelle ohne Kopfzeile — direkt die Datenreihen, sehr luftig
    t = Table(norm, colWidths=col_w, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        # Trenner OBEN über jeder Zeile (= Linie unter der vorherigen)
        ("LINEABOVE", (0, 0), (-1, -1), 0.4, DIVIDER),
        # Letzte Zeile braucht auch unten eine Linie
        ("LINEBELOW", (0, -1), (-1, -1), 0.4, DIVIDER),
        # Label in dezentem Blau
        ("TEXTCOLOR", (0, 0), (0, -1), BLUE_DARK),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (0, -1), 10),
    ]))
    story.append(t)

    story.append(PageBreak())

    # -----------------------------------------------------------------
    # PAGE 2 — Dokumente & Ausstattung
    # -----------------------------------------------------------------
    story.append(Paragraph("DOKUMENTE & ZUBEHÖR", h2_blue))
    story.append(Paragraph("Vor Ort beim Verkäufer einsammeln und abhaken.", st["small"]))
    story.append(Spacer(1, 0.2 * cm))

    docs_items = [
        ("Fahrzeugschein / Zulassung Teil I", "original"),
        ("Fahrzeugbrief / Zulassung Teil II", "original"),
        ("Letzter HU-/AU-Bericht", ""),
        ("Servicebuch / Scheckheft", "gestempelt"),
        ("COC-Papiere (EG-Übereinstimmung)", "falls vorhanden"),
        ("Bedienungsanleitung", ""),
        ("Schlüssel", "Anzahl: _____ von _____"),
        ("Zweitsatz Reifen", "Winter / Sommer / nein"),
        ("Ladekabel / Adapter", "bei E-/Hybrid-Fahrzeugen"),
        ("Werkzeug / Warndreieck / Verbandskasten", ""),
    ]
    story.append(_checklist(docs_items, st, col_count=2))

    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("AUSSTATTUNG LAUT INSERAT — VOR ORT PRÜFEN", h2_blue))
    features = vehicle.get("features") or []
    if features:
        feat_items = [(str(f), "") for f in features if str(f).strip()]
    else:
        feat_items = [
            ("Klimaanlage / Klimaautomatik", ""),
            ("Navigationssystem", ""),
            ("Sitzheizung", ""),
            ("Lederausstattung", ""),
            ("Einparkhilfe / Rückfahrkamera", ""),
            ("Tempomat", ""),
            ("Xenon- / LED-Scheinwerfer", ""),
            ("Soundsystem / Upgrade-Audio", ""),
            ("Panoramadach", ""),
            ("Alufelgen", ""),
            ("Anhängerkupplung", ""),
            ("Standheizung", ""),
        ]
        story.append(Paragraph("<i>Keine Features im Inserat — Standardliste unten.</i>",
                               st["small"]))
        story.append(Spacer(1, 0.1 * cm))

    story.append(_checklist(feat_items, st, col_count=2))

    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("TECHNISCHER ZUSTAND — FAHRER TRÄGT EIN", h2_blue))
    tech_rows = [
        [Paragraph("Kilometerstand bei Abholung", st["check_label"]),
         Paragraph("<u>_____________ km</u>", st["value"]),
         Paragraph("Tankfüllstand", st["check_label"]),
         Paragraph("○ leer &nbsp; ○ ¼ &nbsp; ○ ½ &nbsp; ○ ¾ &nbsp; ○ voll", st["check_opt"])],
        [Paragraph("Reifenprofil VL / VR / HL / HR", st["check_label"]),
         Paragraph("___ / ___ / ___ / ___ mm", st["value"]),
         Paragraph("Fahrverhalten (Probefahrt)", st["check_label"]),
         Paragraph("○ unauffällig &nbsp; ○ Mängel (Bemerkung)", st["check_opt"])],
        [Paragraph("Batterie / Starter", st["check_label"]),
         Paragraph("○ OK &nbsp; ○ schwach &nbsp; ○ defekt", st["check_opt"]),
         Paragraph("Kontrollleuchten leuchten", st["check_label"]),
         Paragraph("○ keine &nbsp; ○ ja (Bemerkung)", st["check_opt"])],
        [Paragraph("Sauberkeit Innenraum", st["check_label"]),
         Paragraph("○ OK &nbsp; ○ mangelhaft", st["check_opt"]),
         Paragraph("Sauberkeit Außen", st["check_label"]),
         Paragraph("○ OK &nbsp; ○ mangelhaft", st["check_opt"])],
    ]
    tech_t = Table(tech_rows, colWidths=[4.5 * cm, 4.2 * cm, 4.5 * cm, 4.3 * cm])
    tech_t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, DIVIDER),
    ]))
    story.append(tech_t)

    story.append(PageBreak())

    # -----------------------------------------------------------------
    # PAGE 3 — Vorbestehende Schäden (aus Kaufvertrag)
    # -----------------------------------------------------------------
    story.append(Paragraph("VORBESTEHENDE SCHÄDEN LAUT KAUFVERTRAG", h2_blue))
    story.append(Paragraph(
        "Diese Schäden wurden im Kaufvertrag dokumentiert. Der Fahrer prüft "
        "vor Ort, ob diese vorhanden sind (○ bestätigt / ○ nicht vorhanden / "
        "○ weicht ab) und markiert ggf. zusätzliche Schäden auf der "
        "leeren Skizze der nächsten Seite.", st["small"]))
    story.append(Spacer(1, 0.2 * cm))
    legend = _damage_legend(damages, st)
    if legend:
        story.append(legend)
        story.append(Spacer(1, 0.25 * cm))
    story.append(KeepTogether(_sketch_grid(damages, empty=False)))

    story.append(PageBreak())

    # -----------------------------------------------------------------
    # PAGE 4 — Vor-Ort-Aufnahme, Bemerkungen, Unterschriften
    # -----------------------------------------------------------------
    story.append(Paragraph("VOR-ORT-AUFNAHME DURCH DEN FAHRER", h2_blue))
    story.append(Paragraph(
        "Der Fahrer markiert hier neu entdeckte Beschädigungen mit Kreuzen (✗) "
        "oder Kreisen (○) und notiert die Art im Feld Bemerkungen.", st["small"]))
    story.append(Spacer(1, 0.2 * cm))
    story.append(KeepTogether(_sketch_grid([], empty=True)))

    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph("BEMERKUNGEN DES FAHRERS", h2_blue))
    # Draw 6 empty lines
    bem_lines = []
    for _ in range(6):
        bem_lines.append([Paragraph("", st["body"])])
    bem_t = Table(bem_lines, colWidths=[17.5 * cm])
    bem_t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, DIVIDER),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(bem_t)

    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("ÜBERGABE-BESTÄTIGUNG", h2_blue))
    sig_rows = [
        [Paragraph("Ort, Datum", st["label"]),
         Paragraph("Unterschrift Verkäufer", st["label"]),
         Paragraph("Unterschrift Fahrer", st["label"])],
        [Paragraph("_________________________", st["value"]),
         Paragraph("_________________________", st["value"]),
         Paragraph("_________________________", st["value"])],
    ]
    sig_t = Table(sig_rows, colWidths=[5.8 * cm, 5.8 * cm, 5.8 * cm])
    sig_t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 28),
        ("LINEBELOW", (0, 1), (-1, 1), 0.5, PRIMARY),
    ]))
    story.append(sig_t)

    doc.build(story)
    return buf.getvalue()
