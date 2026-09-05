# -*- coding: utf-8 -*-
"""Gestaltete Vertrags-E-Mails (Wunsch 09/2026).

Zwei Vorlagen:
  * `vertrag_mail`  — geht an den Verkäufer/Kunden, mit dem Kaufvertrag im
    Anhang, der persönlichen Nachricht des Suchers und einer kurzen
    Übersicht zu Fahrzeug und Preis.
  * `kopie_mail`    — geht zusätzlich an den Sucher selbst, als Beleg
    darüber, was wann an wen verschickt wurde.

Beide liefern (betreff, text, html). Das HTML nutzt nur Tabellen und
Inline-Stile, damit es in Outlook, Gmail und auf dem Handy gleich aussieht;
die Textfassung bleibt vollwertig lesbar.
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Optional, Tuple

FARBE_TEXT = "#111827"
FARBE_GRAU = "#6b7280"
FARBE_LINIE = "#e5e7eb"
FARBE_AKZENT = "#dc2626"
FARBE_HELL = "#f9fafb"


def _eur(betrag) -> str:
    try:
        wert = float(betrag or 0)
    except (TypeError, ValueError):
        return ""
    return f"{wert:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _km(wert) -> str:
    try:
        return f"{int(wert):,}".replace(",", ".") + " km"
    except (TypeError, ValueError):
        return ""


def _datum(wert: Optional[str]) -> str:
    if not wert:
        return ""
    try:
        return datetime.fromisoformat(str(wert)[:19]).strftime("%d.%m.%Y")
    except ValueError:
        return str(wert)


def _absatz(text: str) -> str:
    """Freitext des Suchers sicher in HTML-Absätze umwandeln."""
    teile = [escape(z).strip() for z in (text or "").split("\n")]
    return "<br>".join(t if t else "&nbsp;" for t in teile)


def _fahrzeug_titel(vertrag: dict) -> str:
    teile = [str(vertrag.get(k) or "").strip() for k in ("make", "model")]
    return " ".join(t for t in teile if t) or "Fahrzeug"


def _zeilen(vertrag: dict) -> list:
    daten = vertrag.get("contract_data") or {}
    kandidaten = [
        ("Fahrzeug", _fahrzeug_titel(vertrag)),
        ("Erstzulassung", daten.get("first_registration") or daten.get("erstzulassung") or ""),
        ("Kilometerstand", _km(daten.get("mileage") or daten.get("kilometerstand"))),
        ("Fahrgestellnummer", daten.get("vin") or ""),
        ("Abholung", " ".join(x for x in [_datum(vertrag.get("pickup_date")),
                                          (vertrag.get("pickup_time") or "").strip()] if x)),
    ]
    return [(k, str(v).strip()) for k, v in kandidaten if str(v).strip()]


def _kopf(firma: str, logo_url: str = "") -> str:
    logo = ""
    if logo_url and logo_url.startswith("https://"):
        logo = (f'<img src="{escape(logo_url)}" alt="" height="34" '
                f'style="display:block;border:0;max-height:34px;margin-bottom:8px">')
    return (
        f'<tr><td style="padding:24px 28px 18px 28px;border-bottom:3px solid {FARBE_AKZENT}">'
        f'{logo}'
        f'<div style="font:600 17px/1.3 Arial,Helvetica,sans-serif;color:{FARBE_TEXT}">'
        f'{escape(firma or "Autohaus")}</div>'
        f'</td></tr>')


def _fuss(zusatz: str) -> str:
    return (
        f'<tr><td style="padding:18px 28px 26px 28px;border-top:1px solid {FARBE_LINIE};'
        f'font:400 12px/1.6 Arial,Helvetica,sans-serif;color:{FARBE_GRAU}">{zusatz}</td></tr>')


def _rahmen(inhalt: str) -> str:
    return (
        '<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '</head>'
        f'<body style="margin:0;padding:24px 12px;background:{FARBE_HELL}">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="max-width:600px;margin:0 auto;width:100%;background:#ffffff;'
        f'border:1px solid {FARBE_LINIE};border-radius:10px;overflow:hidden">'
        f'{inhalt}</table></body></html>')


def _datentabelle(zeilen: list) -> str:
    if not zeilen:
        return ""
    reihen = "".join(
        f'<tr>'
        f'<td style="padding:7px 0;font:400 13px/1.4 Arial,Helvetica,sans-serif;'
        f'color:{FARBE_GRAU};white-space:nowrap">{escape(k)}</td>'
        f'<td style="padding:7px 0 7px 16px;font:600 13px/1.4 Arial,Helvetica,sans-serif;'
        f'color:{FARBE_TEXT};text-align:right">{escape(v)}</td></tr>'
        for k, v in zeilen)
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'width="100%">{reihen}</table>')


def _preisblock(preis) -> str:
    if not preis:
        return ""
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:{FARBE_HELL};border:1px solid {FARBE_LINIE};border-radius:8px;'
        f'margin:18px 0"><tr><td style="padding:14px 16px">'
        f'<div style="font:400 11px/1.4 Arial,Helvetica,sans-serif;color:{FARBE_GRAU};'
        f'text-transform:uppercase;letter-spacing:.05em">Vereinbarter Kaufpreis</div>'
        f'<div style="font:700 24px/1.3 Arial,Helvetica,sans-serif;color:{FARBE_TEXT};'
        f'margin-top:3px">{escape(_eur(preis))}</div>'
        f'</td></tr></table>')


def vertrag_mail(*, vertrag: dict, firma: dict, sucher: dict,
                 nachricht: str, betreff: Optional[str] = None) -> Tuple[str, str, str]:
    """E-Mail an den Verkäufer/Kunden. Liefert (betreff, text, html)."""
    firmenname = (firma.get("company_name") or "").strip() or "Autohaus"
    empfaenger = (vertrag.get("seller_name") or "").strip()
    titel = _fahrzeug_titel(vertrag)
    nummer = (vertrag.get("contract_no") or "").strip()
    preis = vertrag.get("purchase_price")
    zeilen = _zeilen(vertrag)
    sucher_name = (f"{sucher.get('first_name', '')} {sucher.get('last_name', '')}".strip()
                   or sucher.get("name") or firmenname)
    sucher_mail = (sucher.get("email") or "").strip()
    sucher_tel = (sucher.get("phone") or firma.get("phone") or "").strip()

    betreff = (betreff or "").strip() or f"Ihr Kaufvertrag – {titel}"

    anrede = f"Hallo {empfaenger}," if empfaenger else "Hallo,"
    text_zeilen = [
        anrede, "",
        (nachricht or "").strip() or
        "anbei erhalten Sie den Kaufvertrag für Ihr Fahrzeug als PDF.",
        "",
        f"Fahrzeug: {titel}",
    ]
    for k, v in zeilen:
        if k != "Fahrzeug":
            text_zeilen.append(f"{k}: {v}")
    if preis:
        text_zeilen.append(f"Vereinbarter Kaufpreis: {_eur(preis)}")
    if nummer:
        text_zeilen.append(f"Vertragsnummer: {nummer}")
    text_zeilen += [
        "",
        "Der vollständige Kaufvertrag liegt dieser E-Mail als PDF bei.",
        "Bitte prüfen Sie ihn in Ruhe. Bei Fragen antworten Sie einfach auf "
        "diese E-Mail — Ihre Antwort geht direkt an "
        f"{sucher_name}{f' ({sucher_mail})' if sucher_mail else ''}.",
        "",
        "Freundliche Grüße",
        sucher_name,
        firmenname,
    ]
    if sucher_tel:
        text_zeilen.append(f"Telefon: {sucher_tel}")
    text = "\n".join(text_zeilen)

    hinweis_anhang = (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="border:1px dashed {FARBE_LINIE};border-radius:8px;margin:4px 0 2px 0">'
        f'<tr><td style="padding:12px 16px;font:400 13px/1.5 Arial,Helvetica,sans-serif;'
        f'color:{FARBE_TEXT}">📎 Der vollständige Kaufvertrag liegt dieser E-Mail '
        f'als PDF bei.</td></tr></table>')

    inhalt = (
        _kopf(firmenname, firma.get("logo_url") or "")
        + '<tr><td style="padding:24px 28px 6px 28px">'
        + f'<div style="font:700 20px/1.3 Arial,Helvetica,sans-serif;color:{FARBE_TEXT}">'
          f'Kaufvertrag{f" {escape(nummer)}" if nummer else ""}</div>'
        + f'<div style="font:400 14px/1.6 Arial,Helvetica,sans-serif;color:{FARBE_TEXT};'
          f'margin-top:16px">{_absatz(anrede)}</div>'
        + f'<div style="font:400 14px/1.6 Arial,Helvetica,sans-serif;color:{FARBE_TEXT};'
          f'margin-top:12px">{_absatz((nachricht or "").strip() or "anbei erhalten Sie den Kaufvertrag für Ihr Fahrzeug als PDF.")}</div>'
        + '</td></tr>'
        + '<tr><td style="padding:6px 28px 0 28px">'
        + _preisblock(preis)
        + _datentabelle(zeilen)
        + '</td></tr>'
        + f'<tr><td style="padding:16px 28px 22px 28px">{hinweis_anhang}</td></tr>'
        + _fuss(
            f'<strong style="color:{FARBE_TEXT}">{escape(sucher_name)}</strong>'
            f'{f" · {escape(firmenname)}" if firmenname else ""}'
            + (f'<br>Telefon: {escape(sucher_tel)}' if sucher_tel else "")
            + (f'<br>E-Mail: <a href="mailto:{escape(sucher_mail)}" '
               f'style="color:{FARBE_GRAU}">{escape(sucher_mail)}</a>' if sucher_mail else "")
            + '<br><br>Antworten auf diese E-Mail gehen direkt an '
            + escape(sucher_name) + '.')
    )
    return betreff, text, _rahmen(inhalt)


def kopie_mail(*, vertrag: dict, firma: dict, sucher: dict,
               empfaenger_adresse: str, betreff_original: str,
               nachricht: str) -> Tuple[str, str, str]:
    """Beleg-E-Mail an den Sucher: was wurde wann an wen geschickt."""
    firmenname = (firma.get("company_name") or "").strip() or "Autohaus"
    titel = _fahrzeug_titel(vertrag)
    nummer = (vertrag.get("contract_no") or "").strip()
    empfaenger_name = (vertrag.get("seller_name") or "").strip()
    zeitpunkt = datetime.now().strftime("%d.%m.%Y um %H:%M Uhr")
    ziel = f"{empfaenger_name} <{empfaenger_adresse}>" if empfaenger_name else empfaenger_adresse

    betreff = f"Kopie: Kaufvertrag an {empfaenger_name or empfaenger_adresse} gesendet"
    text = "\n".join([
        "Deine Kopie zum Nachweis.", "",
        f"Fahrzeug: {titel}",
        f"Vertragsnummer: {nummer}" if nummer else "",
        f"Kaufpreis: {_eur(vertrag.get('purchase_price'))}" if vertrag.get("purchase_price") else "",
        f"Gesendet an: {ziel}",
        f"Betreff: {betreff_original}",
        f"Zeitpunkt: {zeitpunkt}",
        "", "Deine Nachricht an den Verkäufer:",
        (nachricht or "").strip() or "(keine)",
        "",
        "Der versendete Kaufvertrag liegt dieser E-Mail als PDF bei.",
        "Antwortet der Verkäufer, landet seine Antwort direkt in deinem Postfach.",
    ])
    text = "\n".join(z for z in text.split("\n") if z != "")

    zeilen = [("Fahrzeug", titel)]
    if nummer:
        zeilen.append(("Vertragsnummer", nummer))
    if vertrag.get("purchase_price"):
        zeilen.append(("Kaufpreis", _eur(vertrag.get("purchase_price"))))
    zeilen += [("Gesendet an", ziel), ("Betreff", betreff_original),
               ("Zeitpunkt", zeitpunkt)]

    inhalt = (
        _kopf(firmenname, firma.get("logo_url") or "")
        + '<tr><td style="padding:24px 28px 4px 28px">'
        + f'<div style="font:700 19px/1.3 Arial,Helvetica,sans-serif;color:{FARBE_TEXT}">'
          f'Kopie für dich</div>'
        + f'<div style="font:400 14px/1.6 Arial,Helvetica,sans-serif;color:{FARBE_GRAU};'
          f'margin-top:8px">Der Kaufvertrag wurde erfolgreich versendet. '
          f'Diese Nachricht ist dein Nachweis — das PDF liegt bei.</div>'
        + '</td></tr>'
        + f'<tr><td style="padding:14px 28px 0 28px">{_datentabelle(zeilen)}</td></tr>'
        + '<tr><td style="padding:18px 28px 4px 28px">'
        + f'<div style="font:400 11px/1.4 Arial,Helvetica,sans-serif;color:{FARBE_GRAU};'
          f'text-transform:uppercase;letter-spacing:.05em">Deine Nachricht</div>'
        + f'<div style="font:400 14px/1.6 Arial,Helvetica,sans-serif;color:{FARBE_TEXT};'
          f'margin-top:6px;padding:12px 14px;background:{FARBE_HELL};'
          f'border-radius:8px">{_absatz((nachricht or "").strip() or "(keine)")}</div>'
        + '</td></tr>'
        + f'<tr><td style="padding:18px 28px 22px 28px"></td></tr>'
        + _fuss("Antwortet der Verkäufer auf die Vertrags-E-Mail, landet seine "
                "Antwort direkt in deinem Postfach.")
    )
    return betreff, text, _rahmen(inhalt)
