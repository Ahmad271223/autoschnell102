# -*- coding: utf-8 -*-
"""Kundenportal: das unterschriebene Vertrags-PDF (Pruefliste 30.09.2026, Nr. 4/5).

Vorher wurde beim Unterschreiben ein NEUES PDF erzeugt (generate_contract_pdf): es trug das Datum des
Unterschriftstages ("erstellt am") und konnte Fahrzeugdaten vom heutigen Stand enthalten — der Kunde
unterschrieb also nicht sicher genau das Dokument, das er gelesen hatte.

Jetzt ist die Grundlage das GESPEICHERTE Dokument, Byte fuer Byte das, was dem Kunden angezeigt wurde:

  * seine Seiten bleiben unveraendert,
  * die Unterschriftsbilder werden in die beiden Unterschriftsfelder gelegt (ueber die Beschriftung
    "Unterschrift" gefunden; fehlt sie — anderes Layout —, stehen die Bilder nur auf dem Nachweisblatt),
  * ein Blatt "Signaturnachweis" wird angefuegt: wer, wann, welche Fassung, und die Pruefsumme
    (SHA-256) des gelesenen Dokuments.
"""
from __future__ import annotations

import hashlib
import io
from typing import List, Optional, Tuple

BILD_HOEHE_MAX = 30.0          # Punkt — der freie Raum ueber der Unterschriftslinie ist 34 hoch
BILD_BREITE_MAX = 200.0
LINIE_UEBER_TEXT = 13.0        # Abstand Grundlinie der Beschriftung -> Unterkante des Bildes


def pruefsumme(pdf: bytes) -> str:
    return hashlib.sha256(pdf or b"").hexdigest()


def _sicher(text) -> str:
    """Nur Zeichen, die die Standardschrift kennt (sonst bricht das Zeichnen ab)."""
    return str(text or "").encode("cp1252", "replace").decode("cp1252")


def felder_finden(reader) -> Optional[Tuple[int, Tuple[float, float, float], Tuple[float, float, float]]]:
    """(Seite, (x, y, groesse) links, (x, y, groesse) rechts) der beiden Beschriftungen "Unterschrift".
    Gesucht wird das LETZTE Paar exakt gleich lautender Beschriftungen auf gleicher Hoehe, eine in der
    linken und eine in der rechten Seitenhaelfte — ein einzelnes Wort "Unterschrift" im Vertragstext
    oder die Ueberschrift "Unterschriften" zaehlen nicht."""
    gefunden = None
    for nr, seite in enumerate(reader.pages):
        breite = float(seite.mediabox.width)
        treffer: List[Tuple[float, float, float]] = []

        def besucher(text, cm, tm, _schrift, groesse, _t=treffer):
            if (text or "").strip() != "Unterschrift":
                return
            x = cm[0] * tm[4] + cm[2] * tm[5] + cm[4]
            y = cm[1] * tm[4] + cm[3] * tm[5] + cm[5]
            _t.append((float(x), float(y), float(groesse or 8)))
        try:
            seite.extract_text(visitor_text=besucher)
        except Exception:  # noqa: BLE001 — unlesbare Seite: dann eben ohne Felder
            continue
        for i, links in enumerate(treffer):
            for rechts in treffer[i + 1:]:
                a, b = (links, rechts) if links[0] <= rechts[0] else (rechts, links)
                if abs(a[1] - b[1]) <= 4 and a[0] < breite / 2 <= b[0] and abs(a[2] - b[2]) < 0.6:
                    gefunden = (nr, a, b)
    return gefunden


def _bild_masse(png: bytes) -> Tuple[float, float]:
    from reportlab.lib.utils import ImageReader
    b, h = ImageReader(io.BytesIO(png)).getSize()
    if not b or not h:
        raise ValueError("leeres Bild")
    faktor = min(BILD_HOEHE_MAX / h, BILD_BREITE_MAX / b)
    return b * faktor, h * faktor


def _zeile_passend(c, text: str, x: float, y: float, breite_max: float, groesse: float = 7.0) -> None:
    from reportlab.pdfbase.pdfmetrics import stringWidth
    text = _sicher(text)
    while groesse > 5.0 and stringWidth(text, "Helvetica", groesse) > breite_max:
        groesse -= 0.25
    while len(text) > 8 and stringWidth(text, "Helvetica", groesse) > breite_max:
        text = text[:-2].rstrip() + "…".encode("cp1252").decode("cp1252")
    c.setFont("Helvetica", groesse)
    c.drawString(x, y, text)


def _auflage(seitenmasse: Tuple[float, float], links, rechts, verkaeufer_png: bytes, kaeufer_png: Optional[bytes],
             verkaeufer_text: str, kaeufer_text: str) -> bytes:
    """Eine durchsichtige Seite: die Bilder ueber den Linien, daneben Name und Zeitpunkt."""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas
    puffer = io.BytesIO()
    c = canvas.Canvas(puffer, pagesize=seitenmasse)
    spalte = max(80.0, (rechts[0] - links[0]) - 24.0)
    for (x, y, groesse), png, text in ((links, verkaeufer_png, verkaeufer_text), (rechts, kaeufer_png, kaeufer_text)):
        if not png:
            continue
        try:
            b, h = _bild_masse(png)
            c.drawImage(ImageReader(io.BytesIO(png)), x, y + LINIE_UEBER_TEXT, width=b, height=h, mask="auto")
        except Exception:  # noqa: BLE001 — ein kaputtes Bild verhindert das Dokument nicht; es steht im Nachweis
            continue
        if text:
            versatz = stringWidth("Unterschrift", "Helvetica", groesse) + 4
            c.setFillGray(0.25)
            _zeile_passend(c, "— " + text, x + versatz, y, spalte - versatz)
    c.showPage()
    c.save()
    return puffer.getvalue()


def _nachweisblatt(*, firma: str, vertragsnummer: str, fassung: int, name: str, zeit: str, summe: str,
                   seiten: int, verkaeufer_png: bytes, kaeufer_png: Optional[bytes], in_feldern: bool) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    puffer = io.BytesIO()
    c = canvas.Canvas(puffer, pagesize=A4)
    breite, hoehe = A4
    x, y = 57.0, hoehe - 80.0
    c.setTitle("Signaturnachweis")
    c.setFont("Helvetica-Bold", 16)
    c.drawString(x, y, "Signaturnachweis")
    y -= 22
    c.setFont("Helvetica", 10)
    c.drawString(x, y, _sicher(f"Kaufvertrag {vertragsnummer} · Vertragsfassung {fassung}"))
    y -= 26
    c.setFont("Helvetica", 9.5)
    for zeile in (f"Digital unterschrieben über das Kundenportal von {firma} am {zeit} Uhr.",
                  (f"Die Seiten 1 bis {seiten} sind" if seiten > 1 else "Die Seite 1 ist")
                  + " das unveränderte Vertragsdokument, das dem Verkäufer vor der",
                  "Unterschrift angezeigt wurde. "
                  + ("Die Unterschriften wurden in die Unterschriftsfelder eingesetzt;" if in_feldern
                     else "Die Unterschriften stehen auf diesem Blatt;"),
                  "dieses Blatt wurde angefügt."):
        c.drawString(x, y, _sicher(zeile))
        y -= 14
    y -= 18

    def block(titel: str, zeile: str, png: Optional[bytes], y0: float) -> float:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x, y0, _sicher(titel))
        y1 = y0 - 14
        c.setFont("Helvetica", 9.5)
        c.drawString(x, y1, _sicher(zeile))
        y1 -= 8
        if png:
            try:
                leser = ImageReader(io.BytesIO(png))
                b, h = leser.getSize()
                faktor = min(60.0 / h, 260.0 / b)
                c.drawImage(leser, x, y1 - h * faktor, width=b * faktor, height=h * faktor, mask="auto")
                y1 -= h * faktor
            except Exception:  # noqa: BLE001
                pass
        c.setLineWidth(0.5)
        c.setStrokeGray(0.6)
        c.line(x, y1 - 4, x + 260, y1 - 4)
        return y1 - 30
    y = block("Verkäufer / Halter", f"{name} — digital unterschrieben am {zeit} Uhr", verkaeufer_png, y)
    y = block("Käufer / Händler", f"{firma} — " + ("hinterlegte Unterschrift" if kaeufer_png else "ohne hinterlegte Unterschrift"),
              kaeufer_png, y)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(x, y, "Prüfsumme des vorgelegten Vertragsdokuments (SHA-256)".encode("cp1252").decode("cp1252"))
    c.setFont("Courier", 8.5)
    c.drawString(x, y - 13, summe)
    c.setFont("Helvetica", 8)
    c.setFillGray(0.35)
    c.drawString(x, y - 30, _sicher("Mit der Prüfsumme lässt sich belegen, dass genau dieses Dokument vorgelegen hat."))
    c.drawCentredString(breite / 2, 40, _sicher(f"Signaturnachweis zu Kaufvertrag {vertragsnummer} · Fassung {fassung}"))
    c.showPage()
    c.save()
    return puffer.getvalue()


def unterschreiben(original: bytes, *, verkaeufer_png: bytes, kaeufer_png: Optional[bytes], name: str, zeit: str,
                   firma: str, vertragsnummer: str, fassung: int) -> Tuple[bytes, str, bool]:
    """Liefert (unterschriebenes PDF, Pruefsumme des Originals, Bilder in den Feldern?). Wirft bei einem
    unlesbaren Original (dann gibt es nichts zu unterschreiben)."""
    from pypdf import PdfReader, PdfWriter
    summe = pruefsumme(original)
    leser = PdfReader(io.BytesIO(original))
    seiten = len(leser.pages)
    if not seiten:
        raise ValueError("Vertragsdokument ohne Seiten")
    felder = felder_finden(leser)
    schreiber = PdfWriter()
    schreiber.append(leser)
    in_feldern = False
    if felder is not None:
        nr, links, rechts = felder
        seite = schreiber.pages[nr]
        masse = (float(seite.mediabox.width), float(seite.mediabox.height))
        auflage = _auflage(masse, links, rechts, verkaeufer_png, kaeufer_png,
                           f"{name} · digital am {zeit} Uhr", "hinterlegte Unterschrift")
        seite.merge_page(PdfReader(io.BytesIO(auflage)).pages[0])
        in_feldern = True
    blatt = _nachweisblatt(firma=firma, vertragsnummer=vertragsnummer, fassung=fassung, name=name, zeit=zeit,
                           summe=summe, seiten=seiten, verkaeufer_png=verkaeufer_png, kaeufer_png=kaeufer_png,
                           in_feldern=in_feldern)
    schreiber.append(PdfReader(io.BytesIO(blatt)))
    raus = io.BytesIO()
    schreiber.write(raus)
    return raus.getvalue(), summe, in_feldern
