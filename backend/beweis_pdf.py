# -*- coding: utf-8 -*-
"""Beweisdokument je Inserat (ersetzt die Snapshots, Wunsch 10.09.2026).

Wird ein Inserats-Link zum ersten Mal verwendet (egal von wem), entsteht
EIN Beweisdokument, das alle Firmen gemeinsam nutzen. Es haelt fest, was
das Inserat beim ersten Abruf enthielt:

  * links oben die Kennzeichnung des Portals (mobile.de, AutoScout24,
    Kleinanzeigen) — als Schriftzug in Markenfarbe oder, wenn der Betreiber
    eine Logo-Datei mit Nutzungsrecht ablegt, backend/assets/logos/<quelle>.png
  * Anzeigen-ID, vollstaendige Inserats-Adresse (anklickbar), Abrufzeit
  * alle ausgelesenen Angaben, geordnet (Fahrzeugdaten, Ausstattung,
    Beschreibung, Anbieter, weitere Angaben)
  * die Inseratsfotos, eingebettet
  * im Anhang die Adressen aller Inseratsfotos

Reine Funktion: kein Netz, keine Datenbank, kein Browser. Die Fotos laedt
beweis_service vorher (bild_proxy.laden_fuer_pdf) und uebergibt sie als
JPEG-Bytes. Das Dokument ist klar als AutoSchnell-Dokument gekennzeichnet
(kein Bildschirmfoto, kein Dokument des Portals) — ein Nachbau im fremden
Markenauftritt waere ein falscher Beleg.
"""
from __future__ import annotations

import io
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as _rl_canvas
from reportlab.platypus import (
    Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

SEITE_B, SEITE_H = A4
RAND_LR = 15 * mm
# Der Rahmen von SimpleDocTemplate hat innen je 6 pt Abstand — Tabellen in
# voller Breite muessen das abziehen, sonst ragen sie links heraus.
INHALT_B = SEITE_B - 2 * RAND_LR - 12
KOPF_H = 21 * mm
FUSS_H = 14 * mm

DUNKEL = colors.HexColor("#141416")
GRAU = colors.HexColor("#6b7280")
HELLGRAU = colors.HexColor("#f3f4f6")
LINIE = colors.HexColor("#e5e7eb")
ROT = colors.HexColor("#e11d2e")

# Kennzeichnung des Portals links oben. Farbwerte der Schriftzuege: vom
# Betreiber zu bestaetigen. Echte Logo-Dateien NUR mit Nutzungsrecht unter
# backend/assets/logos/<quelle>.png (oder .jpg) ablegen — dann ersetzt die
# Datei den Schriftzug automatisch.
PORTAL_MARKE: Dict[str, Dict[str, str]] = {
    "mobile": {"name": "mobile.de", "text": "#FFFFFF", "grund": "#F26B21",
               "web": "www.mobile.de"},
    "autoscout24": {"name": "AutoScout24", "text": "#333333", "grund": "#F5F200",
                    "web": "www.autoscout24.de"},
    # Kleinanzeigen: dunkelgruener Schriftzug in Kleinbuchstaben auf Weiss
    # (wie das Logo, Vorlage Ahmad 10.09.2026) — ohne farbigen Kasten.
    "kleinanzeigen": {"name": "Kleinanzeigen", "schriftzug": "kleinanzeigen",
                      "text": "#2A3B0B", "grund": None, "web": "www.kleinanzeigen.de"},
}
LOGO_ORDNER = Path(__file__).resolve().parent / "assets" / "logos"
LOGO_MAX_BYTES = 1_000_000


def quelle_norm(quelle: Any) -> str:
    q = str(quelle or "").strip().lower()
    if q.startswith("mobile"):
        return "mobile"
    if q.startswith("autoscout"):
        return "autoscout24"
    if "kleinanzeigen" in q or q.startswith("ebay"):
        return "kleinanzeigen"
    return q or "unbekannt"


def portal_name(quelle: Any) -> str:
    return (PORTAL_MARKE.get(quelle_norm(quelle)) or {}).get("name") or str(quelle or "Portal")


# ------------------------------------------------------------ Schrift ----
# TrueType-Schrift, damit Umlaute, Euro-Zeichen und fremde Buchstaben in
# Beschreibungen sicher erscheinen. Im Docker-Image liegt Liberation Sans
# (fonts-liberation), lokal unter Windows Arial. Fehlt beides, bleibt es bei
# Helvetica und der Text wird auf deren Zeichensatz (cp1252) begrenzt.
_SCHRIFT_KANDIDATEN = [
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
]
_schrift: Optional[Tuple[str, str, bool]] = None


def _schriften() -> Tuple[str, str, bool]:
    global _schrift
    if _schrift is not None:
        return _schrift
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase.ttfonts import TTFont
    for normal, fett in _SCHRIFT_KANDIDATEN:
        if not (os.path.isfile(normal) and os.path.isfile(fett)):
            continue
        try:
            pdfmetrics.registerFont(TTFont("BeweisSans", normal))
            pdfmetrics.registerFont(TTFont("BeweisSans-Bold", fett))
            addMapping("BeweisSans", 0, 0, "BeweisSans")
            addMapping("BeweisSans", 1, 0, "BeweisSans-Bold")
            addMapping("BeweisSans", 0, 1, "BeweisSans")
            addMapping("BeweisSans", 1, 1, "BeweisSans-Bold")
            _schrift = ("BeweisSans", "BeweisSans-Bold", True)
            return _schrift
        except Exception:  # noqa: BLE001 — naechste Schrift versuchen
            continue
    _schrift = ("Helvetica", "Helvetica-Bold", False)
    return _schrift


_UNSICHTBAR = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufe00-\ufe0f\u00ad]")


def _saeubern(wert: Any) -> str:
    """Text fuer das PDF: Steuer- und Unsichtbarzeichen sowie Emojis raus
    (die Schrift hat keine Glyphen dafuer — sonst leere Kaestchen)."""
    s = _UNSICHTBAR.sub("", str(wert if wert is not None else ""))
    s = "".join(
        ch for ch in s
        if (ch in "\n\t" or ord(ch) >= 32)
        and ord(ch) <= 0xFFFF
        and not (0x2300 <= ord(ch) <= 0x23FF or 0x2600 <= ord(ch) <= 0x27BF
                 or 0x2B00 <= ord(ch) <= 0x2BFF or 0xE000 <= ord(ch) <= 0xF8FF))
    if not _schriften()[2]:
        s = s.encode("cp1252", "replace").decode("cp1252")
    return s


def _xml(wert: Any) -> str:
    # Auch Anfuehrungszeichen maskieren: Werte stehen u.a. in href='...' —
    # ein ' in der Inserats-Adresse haette den Link sonst umlenken koennen.
    return (_saeubern(wert).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


# Kontaktangaben privater/unbekannter Anbieter in Freitexten (Titel,
# Beschreibung, weitere Angaben) unkenntlich machen. Telefonnummern beginnen
# in Deutschland mit 0, +49 oder 0049 — Jahreszahlen, Preise und
# Kilometerstaende (beginnen nicht mit 0 bzw. zu wenige Ziffern) bleiben.
_TEL = re.compile(r"(?<![\w+])(?:\+\d{1,3}|00\d{1,3}|0)[\d \-/().]{5,}\d")
_MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
KONTAKT_ENTFERNT = "[Kontaktangabe entfernt]"
_NICHT_MASKIEREN = {"detail_url", "kleinanzeigen_url", "images", "image_urls",
                    "images_thumbs", "mobile_ad_id", "kleinanzeigen_id"}


def kontakt_maskieren(text: str) -> str:
    def _tel(m):
        ziffern = sum(ch.isdigit() for ch in m.group(0))
        return KONTAKT_ENTFERNT if 7 <= ziffern <= 15 else m.group(0)
    return _TEL.sub(_tel, _MAIL.sub(KONTAKT_ENTFERNT, str(text)))


def kontaktdaten_maskieren(daten: Dict[str, Any]) -> Dict[str, Any]:
    aus: Dict[str, Any] = {}
    for k, v in daten.items():
        if k in _NICHT_MASKIEREN or str(k).startswith("seller_"):
            aus[k] = v
        elif isinstance(v, str):
            aus[k] = kontakt_maskieren(v)
        elif isinstance(v, list):
            aus[k] = [kontakt_maskieren(x) if isinstance(x, str) else x for x in v]
        else:
            aus[k] = v
    return aus


# --------------------------------------------------------------- Zeit ----
def _berlin(wert: Any) -> Optional[datetime]:
    if isinstance(wert, str):
        try:
            wert = datetime.fromisoformat(wert.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(wert, datetime):
        return None
    if wert.tzinfo is None:
        wert = wert.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return wert.astimezone(ZoneInfo("Europe/Berlin"))
    except Exception:  # noqa: BLE001 — Image ohne tzdata: EU-Regel von Hand
        u = wert.astimezone(timezone.utc)

        def _letzter_sonntag(monat: int) -> datetime:
            d = datetime(u.year, monat, 31, 1, tzinfo=timezone.utc)
            while d.weekday() != 6:
                d -= timedelta(days=1)
            return d

        sommer = _letzter_sonntag(3) <= u < _letzter_sonntag(10)
        return u.astimezone(timezone(timedelta(hours=2 if sommer else 1)))


def zeit_text(wert: Any) -> str:
    b = _berlin(wert)
    return b.strftime("%d.%m.%Y, %H:%M:%S Uhr") if b else "unbekannt"


# -------------------------------------------------------------- Werte ----
def _eur(betrag: Any) -> str:
    try:
        return (f"{float(betrag):,.0f} €"
                .replace(",", "X").replace(".", ",").replace("X", "."))
    except (TypeError, ValueError):
        return ""


def _zahl(wert: Any, einheit: str) -> str:
    try:
        return f"{int(float(wert)):,} {einheit}".replace(",", ".")
    except (TypeError, ValueError):
        return _saeubern(wert) if wert not in (None, "") else ""


def verkaeufer_art(daten: Dict[str, Any], quelle: Any = None) -> Optional[str]:
    """'haendler' | 'privat' | None (unbekannt). Neues Feld seller_type aus
    den Parsern; Alt-Daten: Ersatzname der Parser. "Händler" gilt NUR bei
    mobile.de als gewerblich (dort setzen ihn die Parser nur bei commercial/
    DEALER) — bei AutoScout24 ist er der Ersatz fuer "unbekannt"."""
    art = str(daten.get("seller_type") or "").strip().lower()
    if art in ("haendler", "händler", "dealer", "gewerblich"):
        return "haendler"
    if art in ("privat", "private", "privatanbieter"):
        return "privat"
    name = str(daten.get("seller_name") or "").strip().lower()
    if name == "händler" and quelle_norm(quelle) == "mobile":
        return "haendler"
    if name in ("privatverkäufer", "privatanbieter"):
        return "privat"
    return None


# Felder, die in den geordneten Abschnitten stehen (oder nur technische
# Doppelungen sind) — alles andere landet unter "Weitere ausgelesene Angaben".
_BEKANNT = {
    "mobile_ad_id", "detail_url", "kleinanzeigen_url", "make", "make_label",
    "model", "model_label", "model_description", "category", "category_label",
    "first_registration", "mileage", "fuel", "fuel_label", "gearbox",
    "gearbox_label", "power_kw", "power_ps", "displacement", "doors", "seats",
    "color", "hu", "previous_owners", "accident_damaged", "roadworthy",
    "features", "description", "list_price", "currency", "seller_name",
    "seller_address", "seller_zip", "seller_city", "seller_phone",
    "seller_email", "seller_type", "images", "image_urls", "image_count",
    "images_thumbs", "title", "vin", "license_plate", "price_label",
    "location", "kleinanzeigen_id",
}
_WEITERE_LABEL: Dict[str, str] = {
    "price": "Preisangabe (Rohwert)",
    "mileage_label": "Kilometerstand (Rohwert)",
    "power_label": "Leistung (Rohwert)",
}


def fahrzeugdaten(quelle: str, daten: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Geordnete Fahrzeugdaten; fehlende Angaben als 'keine Angabe'."""
    q = quelle_norm(quelle)
    kw, ps = daten.get("power_kw"), daten.get("power_ps")
    if kw and ps:
        leistung = f"{kw} kW ({ps} PS)"
    elif ps:
        leistung = f"{ps} PS"
    elif kw:
        leistung = f"{kw} kW"
    else:
        leistung = ""
    preis = _eur(daten.get("list_price"))
    if daten.get("price_label") and q == "kleinanzeigen":
        preis = _saeubern(daten.get("price_label")) or preis
    paare = [
        ("Marke", daten.get("make_label") or daten.get("make")),
        ("Modell", daten.get("model_label") or daten.get("model")),
        ("Ausführung / Titel", daten.get("model_description") or daten.get("title")),
        ("Kategorie", daten.get("category_label") or daten.get("category")),
        ("Erstzulassung", daten.get("first_registration")),
        ("Kilometerstand", _zahl(daten.get("mileage"), "km") if daten.get("mileage") else ""),
        ("Leistung", leistung),
        ("Kraftstoff", daten.get("fuel_label") or daten.get("fuel")),
        ("Getriebe", daten.get("gearbox_label") or daten.get("gearbox")),
        ("Hubraum", _zahl(daten.get("displacement"), "cm³") if daten.get("displacement") else ""),
        ("Farbe", daten.get("color")),
        ("Türen", daten.get("doors")),
        ("Sitzplätze", daten.get("seats")),
        ("Fahrzeughalter", daten.get("previous_owners")),
        ("HU", daten.get("hu")),
        ("Preis laut Inserat", preis),
    ]
    if daten.get("vin"):
        paare.append(("FIN", daten.get("vin")))
    if daten.get("license_plate"):
        paare.append(("Kennzeichen", daten.get("license_plate")))
    # Kleinanzeigen liefert keine Unfall-/Fahrbereit-Angabe (der Parser setzt
    # Platzhalter) — dort nichts behaupten.
    if q != "kleinanzeigen":
        unfall = daten.get("accident_damaged")
        if unfall is True:
            paare.append(("Unfallschaden", "Unfallschaden laut Inserat"))
        elif unfall is False:
            paare.append(("Unfallschaden", "kein Unfallschaden angegeben"))
        if daten.get("roadworthy") is False:
            paare.append(("Fahrbereit", "nicht fahrbereit laut Inserat"))
    return [(k, _saeubern(v) if v not in (None, "") else "keine Angabe") for k, v in paare]


def weitere_angaben(daten: Dict[str, Any]) -> List[Tuple[str, str]]:
    aus: List[Tuple[str, str]] = []
    for k in sorted(daten.keys()):
        if k in _BEKANNT or str(k).startswith("_"):
            continue
        v = daten.get(k)
        if v in (None, "", [], {}):
            continue
        if isinstance(v, bool):
            text = "ja" if v else "nein"
        elif isinstance(v, (int, float, str)):
            text = str(v)
        elif isinstance(v, (list, tuple)):
            text = ", ".join(str(x) for x in v if isinstance(x, (str, int, float)))
        elif isinstance(v, dict):
            text = ", ".join(f"{a}: {b}" for a, b in v.items()
                             if isinstance(b, (str, int, float)))
        else:
            continue
        if text:
            aus.append((_WEITERE_LABEL.get(k, str(k)), _saeubern(text)[:600]))
    return aus


# ------------------------------------------------------------ Kopf/Fuss --
def _logo_bild(quelle: str) -> Optional[ImageReader]:
    for endung in (".png", ".jpg", ".jpeg"):
        pfad = LOGO_ORDNER / f"{quelle}{endung}"
        try:
            if pfad.is_file() and 0 < pfad.stat().st_size <= LOGO_MAX_BYTES:
                bild = ImageReader(str(pfad))
                bild.getSize()
                return bild
        except Exception:  # noqa: BLE001 — unlesbar: Schriftzug nehmen
            continue
    return None


def _seiten_canvas(*, quelle: str, anzeigen_id: str, erstellt: str, dok_nr: str):
    marke = PORTAL_MARKE.get(quelle) or {"name": portal_name(quelle), "text": "#FFFFFF",
                                         "grund": "#374151", "web": ""}
    logo = _logo_bild(quelle)
    normal, fett, _ = _schriften()

    class _Canvas(_rl_canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._seiten: List[dict] = []

        def showPage(self):  # noqa: N802 — reportlab-Name
            self._seiten.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            gesamt = len(self._seiten)
            for zustand in self._seiten:
                self.__dict__.update(zustand)
                self._rahmen(gesamt)
                super().showPage()
            super().save()

        def _rahmen(self, gesamt: int):
            self.saveState()
            oben = SEITE_H - 7 * mm
            # Links oben: Portal-Logo (Datei) oder Schriftzug in Markenfarbe.
            if logo is not None:
                w, h = logo.getSize()
                box_w, box_h = 44 * mm, 11 * mm
                f = min(box_w / max(1, w), box_h / max(1, h))
                self.drawImage(logo, RAND_LR, oben - h * f, width=w * f, height=h * f,
                               mask="auto")
            elif marke.get("grund"):
                wort = marke.get("schriftzug") or marke["name"]
                self.setFont(fett, 13)
                tw = pdfmetrics.stringWidth(wort, fett, 13)
                self.setFillColor(colors.HexColor(marke["grund"]))
                self.roundRect(RAND_LR, oben - 9 * mm, tw + 8 * mm, 9 * mm, 1.8 * mm,
                               stroke=0, fill=1)
                self.setFillColor(colors.HexColor(marke["text"]))
                self.drawString(RAND_LR + 4 * mm, oben - 6.2 * mm, wort)
            else:
                # Schriftzug ohne Kasten (Kleinanzeigen): gross, in Markenfarbe.
                wort = marke.get("schriftzug") or marke["name"]
                self.setFont(fett, 17)
                self.setFillColor(colors.HexColor(marke["text"]))
                self.drawString(RAND_LR, oben - 7 * mm, wort)
            # Rechts oben: Dokumentart, Anzeigen-ID, Seite.
            self.setFillColor(DUNKEL)
            self.setFont(fett, 10.5)
            self.drawRightString(SEITE_B - RAND_LR, oben - 3.6 * mm, "BEWEISDOKUMENT")
            self.setFont(normal, 7.5)
            self.setFillColor(GRAU)
            self.drawRightString(SEITE_B - RAND_LR, oben - 8 * mm,
                                 f"Anzeigen-ID {anzeigen_id} · Seite {self._pageNumber} von {gesamt}")
            self.setStrokeColor(LINIE)
            self.setLineWidth(0.6)
            self.line(RAND_LR, SEITE_H - KOPF_H + 3 * mm, SEITE_B - RAND_LR,
                      SEITE_H - KOPF_H + 3 * mm)
            # Fusszeile: wer das Dokument erstellt hat — und dass es KEIN
            # Dokument des Portals ist.
            y = 8 * mm
            self.line(RAND_LR, y + 4 * mm, SEITE_B - RAND_LR, y + 4 * mm)
            self.setFont(normal, 6.8)
            self.drawString(RAND_LR, y,
                            f"Erstellt von AutoSchnell am {erstellt}. Kein Dokument von "
                            f"{marke['name']} — automatisch aus dem Inserat ausgelesene Angaben.")
            self.drawRightString(SEITE_B - RAND_LR, y, f"Dok.-Nr. {dok_nr}")
            self.restoreState()

    return _Canvas


# --------------------------------------------------------------- Fotos ----
def _bildgroesse(bts: bytes) -> Optional[Tuple[int, int]]:
    try:
        from PIL import Image as PILImage
        with PILImage.open(io.BytesIO(bts)) as im:
            return im.size
    except Exception:  # noqa: BLE001
        return None


def _bild(bts: Optional[bytes], max_b: float, max_h: float, ersatz: Paragraph):
    if not bts:
        return ersatz
    groesse = _bildgroesse(bts)
    if not groesse:
        return ersatz
    w, h = groesse
    f = min(max_b / max(1, w), max_h / max(1, h))
    try:
        return Image(io.BytesIO(bts), width=w * f, height=h * f)
    except Exception:  # noqa: BLE001
        return ersatz


# ---------------------------------------------------------------- PDF ----
def beweis_pdf(*, quelle: str, daten: Dict[str, Any], url: str, item_id: str,
               beweis_id: str, abgerufen_am: Any, erstellt_am: Any,
               fotos: Sequence[Optional[bytes]], foto_urls: Sequence[str],
               privatdaten: bool = False) -> bytes:
    """Beweisdokument als PDF-Bytes.

    fotos: JPEG-Bytes der ersten Inseratsfotos, in derselben Reihenfolge wie
    foto_urls (None = Foto war nicht ladbar). foto_urls: ALLE Fotoadressen des
    Inserats (Anhang). privatdaten: Name/Anschrift/Telefon auch bei privaten
    oder unbekannten Anbietern drucken (Standard nein — das Dokument teilen
    sich alle Firmen, die das Inserat nutzen).
    """
    q = quelle_norm(quelle)
    normal, fett, _ = _schriften()
    portal = portal_name(q)
    web = (PORTAL_MARKE.get(q) or {}).get("web") or ""
    anzeigen_id = _saeubern(daten.get("mobile_ad_id") or item_id or "")
    dok_nr = str(beweis_id or "")[:8].upper()
    erstellt = zeit_text(erstellt_am)
    abgerufen = zeit_text(abgerufen_am)
    art = verkaeufer_art(daten, q)
    voll = art == "haendler" or privatdaten
    maskiert = False
    if not voll:
        gemaskt = kontaktdaten_maskieren(daten)
        maskiert = gemaskt != daten
        daten = gemaskt

    def stil(name, groesse, farbe=DUNKEL, dick=False, zeile=None, **extra):
        return ParagraphStyle(name, fontName=fett if dick else normal, fontSize=groesse,
                              textColor=farbe, leading=zeile or groesse * 1.35, **extra)

    st_h1 = stil("h1", 16, dick=True)
    st_unter = stil("unter", 9.5, GRAU)
    st_h2 = stil("h2", 11.5, dick=True, spaceBefore=2)
    st_text = stil("text", 9)
    st_klein = stil("klein", 7.8, GRAU)
    st_k = stil("k", 8, GRAU)
    st_v = stil("v", 9, dick=True)
    st_preis = stil("preis", 16, ROT, dick=True, alignment=2)
    st_link = stil("link", 8.5, colors.HexColor("#1d4ed8"))

    puffer = io.BytesIO()
    dokument = SimpleDocTemplate(
        puffer, pagesize=A4, leftMargin=RAND_LR, rightMargin=RAND_LR,
        topMargin=KOPF_H + 2 * mm, bottomMargin=FUSS_H + 2 * mm,
        title=f"Beweisdokument {portal} {anzeigen_id}", author="AutoSchnell",
        subject=_saeubern(url)[:500], creator="AutoSchnell Beweisdokument")
    story: List[Any] = []

    def abstand(h=8):
        story.append(Spacer(1, h))

    def schluessel_tabelle(paare: List[Tuple[str, str]], spalten: int = 2,
                           grund: Optional[colors.Color] = None):
        """Schluessel/Wert-Paare in `spalten` Paaren je Zeile."""
        zeilen = []
        for i in range(0, len(paare), spalten):
            zeile = []
            for k, v in paare[i:i + spalten]:
                zeile += [Paragraph(_xml(k), st_k), Paragraph(_xml(v), st_v)]
            while len(zeile) < 2 * spalten:
                zeile += ["", ""]
            zeilen.append(zeile)
        if not zeilen:
            return
        kb = INHALT_B / spalten
        breiten = [kb * 0.38, kb * 0.62] * spalten
        stile = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINIE),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ]
        if grund is not None:
            stile.append(("BACKGROUND", (0, 0), (-1, -1), grund))
        story.append(Table(zeilen, colWidths=breiten, style=TableStyle(stile),
                           repeatRows=0))

    # 1. Kopf: Worum geht es, woher kommen die Daten.
    story.append(Paragraph("Beweisdokument zum Inserat", st_h1))
    story.append(Paragraph(f"{_xml(portal)} · Anzeigen-ID {_xml(anzeigen_id)}", st_unter))
    abstand(8)
    herkunft: List[Tuple[str, str]] = [
        ("Portal", f"{portal}" + (f" ({web})" if web else "")),
        ("Anzeigen-ID", anzeigen_id or "keine Angabe"),
    ]
    if item_id and str(item_id) != anzeigen_id:
        herkunft.append(("Kennung in der Adresse", _saeubern(item_id)))
    herkunft += [
        ("Daten ausgelesen am", abgerufen),
        ("Dokument erstellt am", erstellt),
    ]
    schluessel_tabelle(herkunft, spalten=1, grund=HELLGRAU)
    story.append(Table(
        [[Paragraph("Inserat-Adresse", st_k),
          Paragraph(f"<link href='{_xml(url)}'><u>{_xml(url)}</u></link>", st_link)]],
        colWidths=[INHALT_B * 0.38, INHALT_B * 0.62],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), HELLGRAU),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ])))
    abstand(5)
    story.append(Paragraph(
        "Dieses Dokument hält fest, welche Angaben das Inserat enthielt, als der Link "
        "zum ersten Mal über AutoSchnell verwendet wurde. Es wurde automatisch "
        f"erstellt und ist weder ein Bildschirmfoto noch ein Dokument von {_xml(portal)}. "
        "Maßgeblich für den aktuellen Stand ist das Inserat selbst.", st_klein))
    abstand(12)

    # 2. Fahrzeug: Titel und Preis.
    titel = " ".join(str(x) for x in [daten.get("make_label") or daten.get("make"),
                                      daten.get("model_label") or daten.get("model")] if x)
    unter = daten.get("model_description") or daten.get("title") or ""
    preis = _eur(daten.get("list_price"))
    if q == "kleinanzeigen" and daten.get("price_label"):
        preis = _saeubern(daten.get("price_label")) or preis
    story.append(Table(
        [[Paragraph(_xml(titel or unter or "Fahrzeug"), stil("t", 14, dick=True)),
          Paragraph(_xml(preis or "Preis: keine Angabe"), st_preis)],
         [Paragraph(_xml(unter if titel else ""), st_unter),
          Paragraph("Preis laut Inserat", stil("pl", 7.8, GRAU, alignment=2))]],
        colWidths=[INHALT_B * 0.66, INHALT_B * 0.34],
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ])))
    abstand(8)

    # 3. Fotos des Inserats (eingebettet).
    urls = [u for u in (foto_urls or []) if isinstance(u, str) and u]
    geladen = [b for b in (fotos or []) if b]
    story.append(Paragraph("Fotos des Inserats", st_h2))
    abstand(3)
    if not urls:
        story.append(Paragraph("Das Inserat enthielt beim Abruf keine Fotos.", st_text))
    elif not geladen:
        story.append(Paragraph(
            f"Das Inserat enthielt {len(urls)} Fotos. Sie konnten beim Erstellen dieses "
            "Dokuments nicht vom Portal geladen werden; ihre Adressen stehen im Anhang.",
            st_text))
    else:
        liste = list(fotos)
        nr_erstes = next(i for i, b in enumerate(liste) if b)
        story.append(_bild(liste[nr_erstes], INHALT_B, 110 * mm,
                           Paragraph("Foto nicht lesbar", st_klein)))
        story.append(Paragraph(f"Foto {nr_erstes + 1}", st_klein))
        abstand(5)
        je_reihe = 3
        b = (INHALT_B - (je_reihe - 1) * 4) / je_reihe
        rest = [(i, bts) for i, bts in enumerate(liste) if i != nr_erstes]
        for s in range(0, len(rest), je_reihe):
            zellen = []
            for i, bts in rest[s:s + je_reihe]:
                ersatz = Paragraph(f"Foto {i + 1} konnte nicht geladen werden", st_klein)
                zellen.append([_bild(bts, b, b * 0.8, ersatz),
                               Paragraph(f"Foto {i + 1}", st_klein)])
            while len(zellen) < je_reihe:
                zellen.append("")
            story.append(Table([zellen], colWidths=[b + 4] * je_reihe, style=TableStyle([
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ])))
        hinweis = f"{len(geladen)} von {len(urls)} Inseratsfotos eingebettet"
        if len(geladen) < len(urls):
            hinweis += "; die Adressen aller Fotos stehen im Anhang"
        story.append(Paragraph(hinweis + ".", st_klein))
    abstand(12)

    # 4. Fahrzeugdaten, geordnet.
    story.append(Paragraph("Fahrzeugdaten laut Inserat", st_h2))
    abstand(3)
    schluessel_tabelle(fahrzeugdaten(q, daten), spalten=2)
    abstand(12)

    # 5. Ausstattung.
    ausstattung = [_saeubern(f) for f in (daten.get("features") or []) if f]
    if ausstattung:
        story.append(Paragraph(f"Ausstattung laut Inserat ({len(ausstattung)})", st_h2))
        abstand(3)
        halb = (len(ausstattung) + 1) // 2
        links, rechts = ausstattung[:halb], ausstattung[halb:]
        zeilen = [[Paragraph("• " + _xml(a), st_text),
                   Paragraph("• " + _xml(r), st_text) if r else ""]
                  for a, r in zip(links, rechts + [""] * (len(links) - len(rechts)))]
        story.append(Table(zeilen, colWidths=[INHALT_B / 2] * 2, style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ])))
        abstand(12)

    # 6. Beschreibung des Anbieters (wortgetreu, Absaetze erhalten).
    beschreibung = _saeubern(daten.get("description") or "").strip()
    story.append(Paragraph("Beschreibung laut Inserat", st_h2))
    abstand(3)
    if beschreibung:
        for absatz in re.split(r"\n\s*\n", beschreibung):
            zeilen = [_xml(z.strip()) for z in absatz.splitlines() if z.strip()]
            if zeilen:
                story.append(Paragraph("<br/>".join(zeilen), st_text))
                abstand(4)
    else:
        story.append(Paragraph("Das Inserat enthielt keine Beschreibung.", st_text))
    abstand(8)

    # 7. Anbieter.
    ort = " ".join(str(x) for x in [daten.get("seller_zip"), daten.get("seller_city")] if x)
    anbieter: List[Tuple[str, str]] = [
        ("Anbieterart", {"haendler": "gewerblicher Anbieter", "privat": "Privatanbieter"}
         .get(art, "nicht angegeben")),
    ]
    voll = art == "haendler" or privatdaten
    name = daten.get("seller_name")
    if voll and name and str(name).strip().lower() not in ("händler", "privatverkäufer"):
        anbieter.append(("Name", name))
    if voll and daten.get("seller_address"):
        anbieter.append(("Straße", daten.get("seller_address")))
    if ort:
        anbieter.append(("PLZ / Ort", ort))
    elif daten.get("location"):
        anbieter.append(("Standort", daten.get("location")))
    if voll and daten.get("seller_phone"):
        anbieter.append(("Telefon", daten.get("seller_phone")))
    if voll and daten.get("seller_email"):
        anbieter.append(("E-Mail", daten.get("seller_email")))
    story.append(KeepTogether([Paragraph("Anbieter laut Inserat", st_h2), Spacer(1, 3)]))
    schluessel_tabelle([(k, _saeubern(v)) for k, v in anbieter], spalten=1)
    if maskiert or not voll and any(daten.get(f) for f in ("seller_address", "seller_phone", "seller_email")) \
            or (not voll and name and str(name).strip().lower() not in ("händler", "privatverkäufer")):
        abstand(3)
        story.append(Paragraph(
            "Name, Anschrift, Telefonnummer und E-Mail privater Anbieter werden nicht in "
            "dieses Dokument übernommen; Telefonnummern und E-Mail-Adressen in Titel und "
            "Beschreibung sind unkenntlich gemacht. Das Dokument steht allen Firmen zur "
            "Verfügung, die das Inserat verwenden.", st_klein))
    abstand(12)

    # 8. Alles Weitere, was ausgelesen wurde.
    weitere = weitere_angaben(daten)
    if weitere:
        story.append(KeepTogether([Paragraph("Weitere ausgelesene Angaben", st_h2),
                                   Spacer(1, 3)]))
        schluessel_tabelle(weitere, spalten=1)
        abstand(12)

    # 9. Anhang: Adressen aller Inseratsfotos.
    if urls:
        story.append(KeepTogether([Paragraph("Anhang: Adressen der Inseratsfotos", st_h2),
                                   Spacer(1, 3)]))
        zeilen = [[Paragraph(str(i + 1), st_klein),
                   Paragraph(_xml(u), st_klein)] for i, u in enumerate(urls[:200])]
        story.append(Table(zeilen, colWidths=[9 * mm, INHALT_B - 9 * mm], style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ])))
        if len(urls) > 200:
            story.append(Paragraph(f"… und {len(urls) - 200} weitere.", st_klein))

    # Fotos binaer statt ASCII85 einbetten (sonst rund ein Viertel groesser).
    # Die Einstellung gilt nur waehrend des Aufbaus; ein parallel erzeugtes
    # anderes PDF bleibt in jedem Fall gueltig.
    from reportlab import rl_config
    alt_a85 = rl_config.useA85
    rl_config.useA85 = 0
    try:
        dokument.build(story, canvasmaker=_seiten_canvas(
            quelle=q, anzeigen_id=anzeigen_id or "—", erstellt=erstellt, dok_nr=dok_nr))
    finally:
        rl_config.useA85 = alt_a85
    return puffer.getvalue()
