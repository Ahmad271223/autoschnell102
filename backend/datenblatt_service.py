# -*- coding: utf-8 -*-
"""Beweis-Datenblatt fuer mobile.de-Inserate.

mobile.de blockt automatisierte Browser ("Zugriff verweigert") — ein
Playwright-Snapshot der Seite wuerde nur die Fehlerseite zeigen. Ein
1:1-Nachbau der mobile.de-Seite kommt NICHT infrage: ein selbst
erzeugtes Dokument im fremden Markenauftritt, abgelegt als "Snapshot",
waere ein gefaelschter Beleg und vor Gericht wertlos bis schaedlich.

Stattdessen erzeugt dieses Modul ein EHRLICHES Datenblatt im
AutoSchnell-Design mit dem vertrauten Inserats-Aufbau (Fotogalerie,
Preis, Datenkacheln, Ausstattung, Beschreibung, Anbieter) und klarer
Kennzeichnung: Quelle-URL, Anzeigen-ID, Abrufzeitpunkt, Hinweis
"automatisch ausgelesene Daten, kein Original-Screenshot".
"""
import io
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx
from PIL import Image as PILImage, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

ROT = colors.HexColor("#e11d2e")
DUNKEL = colors.HexColor("#141416")
GRAU = colors.HexColor("#6b7280")
HELLGRAU = colors.HexColor("#f3f4f6")
RAND = colors.HexColor("#e5e7eb")

SEITE_B, SEITE_H = A4
INHALT_B = SEITE_B - 30 * mm


def _xml(s: Any) -> str:
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _eur(betrag) -> str:
    try:
        return (f"{float(betrag):,.0f} €"
                .replace(",", "X").replace(".", ",").replace("X", "."))
    except (TypeError, ValueError):
        return "—"


def _zeitpunkt(wert) -> str:
    """fetched_at (datetime oder ISO-String) -> '31.08.2026, 20:10 Uhr'."""
    if isinstance(wert, str):
        try:
            wert = datetime.fromisoformat(wert)
        except ValueError:
            return wert
    if isinstance(wert, datetime):
        return wert.strftime("%d.%m.%Y, %H:%M Uhr")
    return "unbekannt"


async def fotos_laden(urls: List[str], max_n: int = 9) -> List[bytes]:
    """Original-Fotos vom Bilder-CDN laden. Einzelne Fehlschlaege werden
    uebersprungen — lieber ein Datenblatt mit 7 Fotos als gar keins."""
    fotos: List[bytes] = []
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for u in urls[:max_n]:
            try:
                r = await client.get(u)
                if r.status_code == 200 and len(r.content) > 1000:
                    fotos.append(r.content)
            except Exception:
                continue
    return fotos


# ------------------------------------------------------- Mobile Rebuild ----
def rebuild_html(daten: Dict[str, Any], source_url: str, abgerufen_am,
                 fotos: List[bytes]) -> str:
    """Dunkle Inserats-Ansicht ("Mobile Rebuild") als eigenstaendiges HTML.

    Aufbau wie eine Fahrzeug-Detailseite (Galerie links, Preisbox rechts,
    Datenkacheln, Ausstattung, Beschreibung) — aber im AutoSchnell-Design
    mit klarer Herkunftsangabe. Alle Fotos als data-URIs eingebettet, das
    Rendering (PNG/PDF) uebernimmt der Playwright-Worker per set_content.
    """
    import base64 as _b64

    def _uri(bts: bytes) -> str:
        return "data:image/jpeg;base64," + _b64.b64encode(bts).decode()

    ad_id = _xml(daten.get("mobile_ad_id") or "")
    zeit = _xml(_zeitpunkt(abgerufen_am))
    titel = _xml(" ".join(x for x in [daten.get("make_label"),
                                      daten.get("model_label")] if x))
    km = daten.get("mileage")
    kacheln = [
        ("Kilometerstand", f"{km:,} km".replace(",", ".") if km else "—"),
        ("Leistung", f"{daten.get('power_kw')} kW ({daten.get('power_ps')} PS)"
                     if daten.get("power_kw") else "—"),
        ("Kraftstoffart", daten.get("fuel_label") or "—"),
        ("Getriebe", daten.get("gearbox_label") or "—"),
        ("Erstzulassung", daten.get("first_registration") or "—"),
        ("Kategorie", daten.get("category_label") or "—"),
        ("Farbe", daten.get("color") or "—"),
        ("Fahrzeughalter", daten.get("previous_owners") or "—"),
        ("HU", daten.get("hu") or "—"),
        ("Hubraum", f"{daten['displacement']:,} ccm".replace(",", ".")
                    if daten.get("displacement") else "—"),
        ("Türen", daten.get("doors") or "—"),
        ("Sitzplätze", daten.get("seats") or "—"),
    ]
    zustand = []
    if daten.get("accident_damaged") is not None:
        zustand.append("Unfallschaden lt. Inserat" if daten.get("accident_damaged")
                       else "Unfallfrei lt. Inserat")
    if daten.get("roadworthy") is False:
        zustand.append("Nicht fahrbereit")
    if zustand:
        kacheln.append(("Zustand", ", ".join(zustand)))

    kacheln_html = "".join(
        f'<div class="kachel"><div class="k">{_xml(k)}</div>'
        f'<div class="v">{_xml(v)}</div></div>' for k, v in kacheln)
    gross = f'<img class="gross" src="{_uri(fotos[0])}">' if fotos else ""
    thumbs = "".join(f'<img class="thumb" src="{_uri(f)}">' for f in fotos[1:9])
    thumbreihe = f'<div class="thumbreihe">{thumbs}</div>' if thumbs else ""
    anzahl_gesamt = daten.get("image_count") or len(fotos)
    foto_hinweis = (f'<div class="fotohinweis">{len(fotos)} von {anzahl_gesamt} '
                    "Inserats-Fotos abgebildet.</div>"
                    if anzahl_gesamt > len(fotos) else "")

    features = daten.get("features") or []
    features_html = ""
    if features:
        features_html = (
            f'<div class="karte"><h2>Ausstattung ({len(features)})</h2>'
            f'<div class="txt">{_xml(" · ".join(str(f) for f in features))}</div></div>')

    beschreibung = (daten.get("description") or "").strip()
    beschreibung_html = ""
    if beschreibung:
        absaetze = "".join(
            f"<p>{'<br>'.join(_xml(z) for z in a.splitlines() if z.strip())}</p>"
            for a in re.split(r"\n{2,}", beschreibung) if a.strip())
        beschreibung_html = (f'<div class="karte">'
                             f"<h2>Fahrzeugbeschreibung laut Anbieter</h2>"
                             f'<div class="txt">{absaetze}</div></div>')

    haendler = []
    if daten.get("seller_name"):
        haendler.append(f"<b>{_xml(daten['seller_name'])}</b>")
    adresse = ", ".join(x for x in [
        daten.get("seller_address"),
        " ".join(y for y in [daten.get("seller_zip"),
                             daten.get("seller_city")] if y)] if x)
    if adresse:
        haendler.append(_xml(adresse))
    if daten.get("seller_phone"):
        haendler.append(f"Tel.: {_xml(daten['seller_phone'])}")
    haendler_html = ("<div class='haendler'>" + "<br>".join(haendler) + "</div>"
                     if haendler else "")

    preis = _eur(daten.get("list_price"))
    unter = _xml(daten.get("model_description") or "")

    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><style>
body {{ margin:0; background:#141416; color:#e5e7eb; font-family:'Segoe UI','DejaVu Sans',Arial,sans-serif; }}
.kopf {{ background:#0c0c0e; border-bottom:3px solid #e11d2e; padding:14px 28px; display:flex; justify-content:space-between; align-items:center; }}
.kopf b {{ font-size:19px; color:#fff; }}
.kopf span {{ color:#9ca3af; font-size:13px; }}
.hinweis {{ background:#1f2937; color:#d1d5db; font-size:12.5px; padding:9px 28px; }}
.inhalt {{ display:grid; grid-template-columns:1fr 360px; gap:22px; padding:22px 28px; max-width:1250px; margin:0 auto; }}
.gross {{ width:100%; border-radius:10px; display:block; }}
.thumbreihe {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-top:8px; }}
.thumb {{ width:100%; height:110px; object-fit:cover; border-radius:6px; }}
.fotohinweis {{ color:#9ca3af; font-size:11.5px; margin-top:6px; }}
.karte {{ background:#1c1c1f; border:1px solid #2b2b30; border-radius:12px; padding:18px 20px; margin-top:18px; break-inside:avoid; }}
h2 {{ font-size:16px; margin:0 0 12px; color:#fff; }}
.kachelraster {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }}
.kachel .k {{ color:#9ca3af; font-size:11px; text-transform:uppercase; letter-spacing:.4px; }}
.kachel .v {{ font-weight:600; font-size:15px; margin-top:2px; color:#fff; }}
.titelbox {{ background:#1c1c1f; border:1px solid #2b2b30; border-radius:12px; padding:20px; }}
.titelbox h1 {{ margin:0; font-size:22px; color:#fff; }}
.titelbox .sub {{ color:#9ca3af; font-size:13px; margin-top:4px; }}
.preis {{ color:#e11d2e; font-size:30px; font-weight:800; margin:14px 0 4px; }}
.haendler {{ margin-top:14px; padding-top:14px; border-top:1px solid #2b2b30; font-size:13.5px; line-height:1.6; }}
.haendler b {{ color:#fff; }}
.quelle {{ margin-top:14px; background:#111214; border-radius:8px; padding:10px 12px; font-size:11.5px; color:#9ca3af; line-height:1.5; word-break:break-all; }}
.txt {{ font-size:13px; color:#d1d5db; line-height:1.65; }}
.txt p {{ margin:0 0 8px; }}
</style></head><body>
<div class="kopf"><b>AutoSchnell · Mobile Rebuild</b><span>Quelle: mobile.de · Anzeigen-ID {ad_id}</span></div>
<div class="hinweis">Automatisch ausgelesene Inserats-Daten vom {zeit} — kein Original-Screenshot der Anbieterseite.</div>
<div class="inhalt">
  <div>
    {gross}
    {thumbreihe}
    {foto_hinweis}
    <div class="karte"><h2>Technische Daten</h2><div class="kachelraster">{kacheln_html}</div></div>
    {features_html}
    {beschreibung_html}
  </div>
  <div>
    <div class="titelbox">
      <h1>{titel}</h1>
      <div class="sub">{unter}</div>
      <div class="preis">{preis}</div>
      <div class="sub">Angebotspreis lt. Inserat</div>
      {haendler_html}
      <div class="quelle"><b>Herkunft:</b> Daten des Inserats {_xml(source_url)},
      automatisch ausgelesen am {zeit}. Von AutoSchnell nachgebaute Ansicht
      (Mobile Rebuild), kein Dokument des Anbieters.</div>
    </div>
  </div>
</div></body></html>"""


# ---------------------------------------------------------------- PDF ----
def _stil(name, groesse, farbe=DUNKEL, fett=False, zeilenhoehe=None):
    return ParagraphStyle(
        name, fontName="Helvetica-Bold" if fett else "Helvetica",
        fontSize=groesse, textColor=farbe,
        leading=zeilenhoehe or groesse * 1.35)


def _datenkacheln(daten: Dict[str, Any]) -> List[Tuple[str, str]]:
    km = daten.get("mileage")
    ps = daten.get("power_ps")
    kw = daten.get("power_kw")
    leistung = f"{kw} kW ({ps} PS)" if kw and ps else (f"{ps} PS" if ps else "—")
    paare = [
        ("Kilometerstand", f"{km:,} km".replace(",", ".") if km else "—"),
        ("Leistung", leistung),
        ("Kraftstoffart", daten.get("fuel_label") or "—"),
        ("Getriebe", daten.get("gearbox_label") or "—"),
        ("Erstzulassung", daten.get("first_registration") or "—"),
        ("Kategorie", daten.get("category_label") or "—"),
        ("Farbe", daten.get("color") or "—"),
        ("Fahrzeughalter", daten.get("previous_owners") or "—"),
        ("HU", daten.get("hu") or "—"),
        ("Hubraum", f"{daten['displacement']:,} ccm".replace(",", ".")
                    if daten.get("displacement") else "—"),
        ("Türen", daten.get("doors") or "—"),
        ("Sitzplätze", daten.get("seats") or "—"),
    ]
    zustand = []
    if daten.get("accident_damaged") is not None:
        zustand.append("Unfallschaden" if daten.get("accident_damaged")
                       else "Unfallfrei lt. Inserat")
    if daten.get("roadworthy") is False:
        zustand.append("Nicht fahrbereit")
    if zustand:
        paare.append(("Zustand", ", ".join(zustand)))
    return paare


def datenblatt_pdf(daten: Dict[str, Any], source_url: str, abgerufen_am,
                   fotos: List[bytes]) -> bytes:
    """Mehrseitiges PDF im Inserats-Aufbau — AutoSchnell-Design mit klarer
    Quellen-Kennzeichnung auf jeder Seite."""
    ad_id = daten.get("mobile_ad_id") or ""
    zeit = _zeitpunkt(abgerufen_am)

    def _kopf_fuss(canvas, doc):
        canvas.saveState()
        # Kopfband
        canvas.setFillColor(DUNKEL)
        canvas.rect(0, SEITE_H - 16 * mm, SEITE_B, 16 * mm, stroke=0, fill=1)
        canvas.setFillColor(ROT)
        canvas.rect(0, SEITE_H - 16 * mm, 4 * mm, 16 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawString(15 * mm, SEITE_H - 10.5 * mm, "AutoSchnell · Mobile Rebuild")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#d1d5db"))
        canvas.drawRightString(SEITE_B - 15 * mm, SEITE_H - 10.5 * mm,
                               f"Quelle: mobile.de · Anzeigen-ID {ad_id}")
        # Fusszeile
        canvas.setFillColor(GRAU)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(15 * mm, 8 * mm,
                          f"Automatisch ausgelesene Inserats-Daten vom {zeit} — "
                          "kein Original-Screenshot der Anbieterseite.")
        canvas.drawRightString(SEITE_B - 15 * mm, 8 * mm, f"Seite {doc.page}")
        canvas.restoreState()

    puffer = io.BytesIO()
    dokument = SimpleDocTemplate(
        puffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=22 * mm, bottomMargin=14 * mm,
        title=f"Mobile Rebuild mobile.de {ad_id}")
    st_titel = _stil("titel", 17, fett=True)
    st_unter = _stil("unter", 10.5, GRAU)
    st_preis = _stil("preis", 17, ROT, fett=True)
    st_h2 = _stil("h2", 11.5, fett=True)
    st_text = _stil("text", 9)
    st_klein = _stil("klein", 8, GRAU)
    st_kachel_k = _stil("kachelk", 7.5, GRAU)
    st_kachel_v = _stil("kachelv", 9.5, fett=True)

    titel = " ".join(x for x in [daten.get("make_label"), daten.get("model_label")] if x)
    story: List[Any] = []

    # Quellen-Kasten (deutlich, ganz oben)
    story.append(Table(
        [[Paragraph(
            "<b>Herkunft dieses Dokuments:</b> Daten des Inserats "
            f"<link href='{_xml(source_url)}'><u>{_xml(source_url)}</u></link> "
            f"— automatisch ausgelesen am {_xml(zeit)}. "
            "Dieses Datenblatt wurde von AutoSchnell erzeugt und ist kein "
            "Bildschirmfoto der Anbieterseite.", st_klein)]],
        colWidths=[INHALT_B],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), HELLGRAU),
            ("BOX", (0, 0), (-1, -1), 0.75, RAND),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ])))
    story.append(Spacer(1, 10))

    # Titel + Preis nebeneinander (wie die Kopfzeile eines Inserats)
    story.append(Table(
        [[Paragraph(_xml(titel), st_titel),
          Paragraph(_eur(daten.get("list_price")), st_preis)],
         [Paragraph(_xml(daten.get("model_description") or ""), st_unter),
          Paragraph("Angebotspreis lt. Inserat", st_klein)]],
        colWidths=[INHALT_B * 0.68, INHALT_B * 0.32],
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ])))
    story.append(Spacer(1, 10))

    # Fotogalerie: 1 grosses Bild + Raster (Original-Fotos des Inserats)
    if fotos:
        def _bild(bts, breite):
            try:
                img = PILImage.open(io.BytesIO(bts))
                w, h = img.size
                hoehe = breite * h / max(1, w)
                return Image(io.BytesIO(bts), width=breite, height=hoehe)
            except Exception:
                return Paragraph("Foto nicht lesbar", st_klein)

        story.append(_bild(fotos[0], INHALT_B))
        story.append(Spacer(1, 4))
        rest = fotos[1:]
        if rest:
            je_reihe = 3
            b = (INHALT_B - (je_reihe - 1) * 4) / je_reihe
            reihen = [rest[i:i + je_reihe] for i in range(0, len(rest), je_reihe)]
            for reihe in reihen:
                zellen = [_bild(f, b) for f in reihe]
                while len(zellen) < je_reihe:
                    zellen.append("")
                story.append(Table([zellen], colWidths=[b + 4] * je_reihe,
                                   style=TableStyle([
                                       ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                       ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                                       ("TOPPADDING", (0, 0), (-1, -1), 2),
                                       ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                                       ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ])))
        anzahl_gesamt = daten.get("image_count") or len(fotos)
        if anzahl_gesamt > len(fotos):
            story.append(Paragraph(
                f"{len(fotos)} von {anzahl_gesamt} Inserats-Fotos abgebildet — "
                "alle Fotos sind in der Fahrzeugakte hinterlegt.", st_klein))
        story.append(Spacer(1, 10))

    # Datenkacheln (wie die Kennzahlen-Leiste des Inserats)
    story.append(Paragraph("Technische Daten", st_h2))
    story.append(Spacer(1, 4))
    kacheln = _datenkacheln(daten)
    je_reihe = 3
    reihen = [kacheln[i:i + je_reihe] for i in range(0, len(kacheln), je_reihe)]
    kachel_b = INHALT_B / je_reihe
    for reihe in reihen:
        zellen = []
        for k, v in reihe:
            zellen.append([Paragraph(_xml(k).upper(), st_kachel_k),
                           Paragraph(_xml(v), st_kachel_v)])
        while len(zellen) < je_reihe:
            zellen.append(["", ""])
        story.append(Table(
            [[Table([[z[0]], [z[1]]], colWidths=[kachel_b - 6],
                    style=TableStyle([
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 1),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                    ])) if z[0] != "" else "" for z in zellen]],
            colWidths=[kachel_b] * je_reihe,
            style=TableStyle([
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, RAND),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ])))
    story.append(Spacer(1, 10))

    # Ausstattung
    features = daten.get("features") or []
    if features:
        story.append(Paragraph(f"Ausstattung ({len(features)})", st_h2))
        story.append(Spacer(1, 4))
        story.append(Paragraph(_xml(" · ".join(str(f) for f in features)), st_text))
        story.append(Spacer(1, 10))

    # Beschreibung des Anbieters
    beschreibung = (daten.get("description") or "").strip()
    if beschreibung:
        story.append(Paragraph("Fahrzeugbeschreibung laut Anbieter", st_h2))
        story.append(Spacer(1, 4))
        for absatz in re.split(r"\n{2,}", beschreibung):
            zeilen = [_xml(z) for z in absatz.splitlines() if z.strip()]
            if zeilen:
                story.append(Paragraph("<br/>".join(zeilen), st_text))
                story.append(Spacer(1, 4))
        story.append(Spacer(1, 6))

    # Anbieter
    anbieter_zeilen = []
    if daten.get("seller_name"):
        anbieter_zeilen.append(f"<b>{_xml(daten['seller_name'])}</b>")
    adresse = ", ".join(x for x in [
        daten.get("seller_address"),
        " ".join(y for y in [daten.get("seller_zip"), daten.get("seller_city")] if y),
    ] if x)
    if adresse:
        anbieter_zeilen.append(_xml(adresse))
    if daten.get("seller_phone"):
        anbieter_zeilen.append(f"Tel.: {_xml(daten['seller_phone'])}")
    if anbieter_zeilen:
        story.append(Paragraph("Anbieter laut Inserat", st_h2))
        story.append(Spacer(1, 4))
        story.append(Table(
            [[Paragraph("<br/>".join(anbieter_zeilen), st_text)]],
            colWidths=[INHALT_B],
            style=TableStyle([
                ("BOX", (0, 0), (-1, -1), 0.75, RAND),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ])))

    dokument.build(story, onFirstPage=_kopf_fuss, onLaterPages=_kopf_fuss)
    return puffer.getvalue()


# ---------------------------------------------------------------- Bild ----
_FONT_PFADE = [
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]
_FONT_FETT_PFADE = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]


def _font(groesse: int, fett: bool = False):
    for pfad in (_FONT_FETT_PFADE if fett else _FONT_PFADE):
        try:
            return ImageFont.truetype(pfad, groesse)
        except Exception:
            continue
    return ImageFont.load_default()


def datenblatt_bild(daten: Dict[str, Any], source_url: str, abgerufen_am,
                    fotos: List[bytes]) -> bytes:
    """Uebersichtsbild (JPG) fuer den 'Foto'-Knopf: Kopf, Titel/Preis,
    grosses Foto + Raster, Kennzahlen, Quellen-Fusszeile."""
    B = 1200
    kopf_h, titel_h, fakten_h, fuss_h = 64, 92, 118, 54
    gross_h = 620 if fotos else 0
    raster = fotos[1:7]
    raster_h = 190 if raster else 0
    H = kopf_h + titel_h + gross_h + (raster_h + 8 if raster else 0) + fakten_h + fuss_h

    bild = PILImage.new("RGB", (B, H), "#ffffff")
    z = ImageDraw.Draw(bild)

    # Kopfband
    z.rectangle([0, 0, B, kopf_h], fill="#141416")
    z.rectangle([0, 0, 10, kopf_h], fill="#e11d2e")
    z.text((28, 18), "AutoSchnell · Mobile Rebuild", font=_font(26, True), fill="#ffffff")
    quelle = f"Quelle: mobile.de · ID {daten.get('mobile_ad_id') or ''}"
    z.text((B - 24 - z.textlength(quelle, font=_font(20)), 22), quelle,
           font=_font(20), fill="#d1d5db")

    # Titel + Preis
    y = kopf_h + 14
    titel = " ".join(x for x in [daten.get("make_label"), daten.get("model_label")] if x)
    z.text((28, y), titel, font=_font(34, True), fill="#141416")
    unter = (daten.get("model_description") or "")[:70]
    z.text((28, y + 42), unter, font=_font(20), fill="#6b7280")
    preis = _eur(daten.get("list_price"))
    z.text((B - 28 - z.textlength(preis, font=_font(36, True)), y + 2), preis,
           font=_font(36, True), fill="#e11d2e")

    def _einpassen(bts, breite, hoehe):
        """Foto exakt auf breite x hoehe bringen (skalieren + mittig
        beschneiden) — so sind alle Kacheln im Raster gleich gross."""
        f = PILImage.open(io.BytesIO(bts)).convert("RGB")
        faktor = max(breite / f.width, hoehe / f.height)
        f = f.resize((max(1, round(f.width * faktor)),
                      max(1, round(f.height * faktor))), PILImage.LANCZOS)
        links = (f.width - breite) // 2
        oben = (f.height - hoehe) // 2
        return f.crop((links, oben, links + breite, oben + hoehe))

    # Grosses Foto
    y = kopf_h + titel_h
    if fotos:
        try:
            f = _einpassen(fotos[0], B - 56, gross_h)
            bild.paste(f, (28 + (B - 56 - f.width) // 2, y))
        except Exception:
            pass
        y += gross_h
    # Foto-Raster
    if raster:
        y += 8
        b_zelle = (B - 56 - 5 * 8) // 6
        x = 28
        for bts in raster:
            try:
                f = _einpassen(bts, b_zelle, raster_h)
                bild.paste(f, (x, y))
            except Exception:
                pass
            x += b_zelle + 8
        y += raster_h

    # Kennzahlen-Zeile
    y += 14
    km = daten.get("mileage")
    fakten = [
        ("Kilometerstand", f"{km:,} km".replace(",", ".") if km else "—"),
        ("Leistung", f"{daten.get('power_ps')} PS" if daten.get("power_ps") else "—"),
        ("Kraftstoff", daten.get("fuel_label") or "—"),
        ("Getriebe", daten.get("gearbox_label") or "—"),
        ("Erstzulassung", daten.get("first_registration") or "—"),
    ]
    b_spalte = (B - 56) // len(fakten)
    for i, (k, v) in enumerate(fakten):
        x = 28 + i * b_spalte
        z.text((x, y), k.upper(), font=_font(17), fill="#6b7280")
        z.text((x, y + 28), str(v), font=_font(24, True), fill="#141416")
    y += fakten_h - 14

    # Fusszeile mit Quelle + Zeitpunkt
    z.rectangle([0, H - fuss_h, B, H], fill="#f3f4f6")
    z.text((28, H - fuss_h + 8),
           f"Automatisch ausgelesen am {_zeitpunkt(abgerufen_am)} — kein "
           "Original-Screenshot der Anbieterseite.",
           font=_font(17), fill="#6b7280")
    z.text((28, H - fuss_h + 30), source_url[:150], font=_font(15), fill="#9ca3af")

    aus = io.BytesIO()
    bild.save(aus, "JPEG", quality=82, optimize=True)
    return aus.getvalue()
