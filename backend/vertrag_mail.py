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
    # Rollenpruefung 22.09.2026 (RP-432): zuerst die im Vertrag bearbeiteten
    # Werte (nach einer Korrektur des Fahrers stehen sie nur dort), dann
    # make/model — dieselbe Regel wie die Platzhalter {fahrzeug}/{marke}.
    from vertrag_platzhalter import marke_modell
    teile = marke_modell(vertrag or {})
    return " ".join(t for t in teile if t) or "Fahrzeug"


def _antwort_moeglich(adresse: str) -> bool:
    """Rollenpruefung 22.09.2026 (RP-473): Erreicht eine Antwort des
    Verkaeufers wirklich jemanden? Ohne gueltige Adresse filtert der Versand
    reply_to heraus, und die Antwort landet bei unserer Plattformadresse."""
    try:
        from email_service import gueltige_adresse
        return bool(adresse) and gueltige_adresse(adresse)
    except Exception:  # noqa: BLE001
        return bool(adresse) and "@" in adresse


def _logo_adresse(logo_url: str) -> str:
    """Rollenpruefung 22.09.2026 (RP-452): hochgeladene Logos liegen unter
    /api/files/logo/… (oeffentlich) — fuer die Mail als volle https-Adresse
    (FRONTEND_URL, dieselbe Quelle wie der Download-Link). Nur https; alles
    andere wird nicht eingebunden."""
    url = str(logo_url or "").strip()
    if url.startswith("/api/files/logo/"):
        import os
        basis = (os.environ.get("FRONTEND_URL") or "").split("?")[0].rstrip("/")
        url = f"{basis}{url}" if basis else ""
    return url if url.startswith("https://") else ""


def _zeilen(vertrag: dict) -> list:
    daten = vertrag.get("contract_data") or {}
    # Runde 16 (15.09.2026): die im Vertrag bearbeiteten Werte heissen
    # vehicle_* — die alten Namen bleiben als Rueckfall fuer Altvertraege.
    kandidaten = [
        ("Fahrzeug", _fahrzeug_titel(vertrag)),
        ("Erstzulassung", daten.get("vehicle_first_registration") or daten.get("first_registration")
         or daten.get("erstzulassung") or ""),
        ("Kilometerstand", _km(daten.get("vehicle_mileage") or daten.get("mileage")
                               or daten.get("kilometerstand"))),
        ("Fahrgestellnummer", daten.get("vehicle_vin") or daten.get("vin") or ""),
        # Wunsch Ahmad (15.09.2026): nur das Datum — keine Uhrzeit im Vertragsversand.
        ("Abholung", _datum(vertrag.get("pickup_date")) or ""),
    ]
    return [(k, str(v).strip()) for k, v in kandidaten if str(v).strip()]


def _kopf(firma: str, logo_url: str = "") -> str:
    logo = ""
    logo_url = _logo_adresse(logo_url)
    if logo_url:
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


def sucher_kontakt(user: dict, firma: dict) -> Tuple[str, str]:
    """Kontonummer (13.09.2026): Konten brauchen keine E-Mail mehr.

    Liefert (eigene_adresse, antwort_adresse):
      * eigene_adresse = users.email, sonst die eigene Kontaktadresse aus den
        Sucher-Einstellungen (settings_override.email), sonst "" —
        NUR dorthin geht die Belegkopie (keine Kopie eines Suchers still
        beim Chef);
      * beim Chef selbst (role dealer) ist die Firmenadresse (dealers.email)
        seine eigene: er pflegt nur sie in den Einstellungen, und der
        Betreiber legt Chefs ohne users.email an (Nachbesserung Schritt 2);
      * antwort_adresse = eigene_adresse, sonst die Firmenadresse (bei
        Suchern die effective_dealer-Sicht, also ggf. ueberschrieben)."""
    user = user or {}
    firmen_adresse = str((firma or {}).get("email") or "").strip()
    eigene = ((user.get("email") or "").strip()
              or str((user.get("settings_override") or {}).get("email") or "").strip())
    if not eigene and user.get("role") == "dealer":
        eigene = firmen_adresse
    antwort = eigene or firmen_adresse
    return eigene, antwort


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
    # Kontonummer (13.09.2026): die Adresse, an die Antworten wirklich gehen
    # (eigene Adresse des Suchers, sonst die Firmenadresse)
    _, sucher_mail = sucher_kontakt(sucher, firma)
    # Rollenpruefung 22.09.2026 (RP-473): ohne gueltige Antwortadresse
    # verspricht die Mail NICHT mehr "Ihre Antwort geht direkt an …" — die
    # Antwort landete sonst bei unserer Plattformadresse.
    antwort_ok = _antwort_moeglich(sucher_mail)
    if not antwort_ok:
        sucher_mail = ""
    sucher_tel = (sucher.get("phone") or firma.get("phone") or "").strip()

    betreff = (betreff or "").strip() or f"Ihr Kaufvertrag – {titel}"

    # Wunsch Ahmad 21.09.2026: Die Vorlage aus den Einstellungen bringt ihre
    # eigene Anrede ("Sehr geehrte/r Frau/Herr …") und Grussformel ("Vielen
    # Dank / Ihr Autohaus / …") mit und soll WOERTLICH ankommen. Vorher stand
    # davor noch "Hallo <Name>," und dahinter "Freundliche Grüße" — der
    # Verkaeufer bekam zwei Anreden und zwei Grussformeln. Eigene Anrede und
    # Gruss nur noch, wenn keine Nachricht mitkommt.
    eigener_text = (nachricht or "").strip()
    anrede = "" if eigener_text else (f"Hallo {empfaenger}," if empfaenger else "Hallo,")
    text_zeilen = ([anrede, ""] if anrede else []) + [
        eigener_text or "anbei erhalten Sie den Kaufvertrag für Ihr Fahrzeug als PDF.",
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
    antwort_an = f"{sucher_name}{f' ({sucher_mail})' if sucher_mail else ''}"
    if antwort_ok:
        fragen = ("Bei Fragen antworten Sie einfach auf diese E-Mail — Ihre Antwort "
                  f"geht direkt an {antwort_an}.")
    elif sucher_tel:
        fragen = f"Bei Fragen erreichen Sie uns telefonisch unter {sucher_tel}."
    else:
        fragen = ""
    if eigener_text:
        # Gruss steht schon in der Vorlage — hier nur noch der Kontakt.
        text_zeilen += [
            "",
            "Der vollständige Kaufvertrag liegt dieser E-Mail als PDF bei.",
            *([fragen] if fragen else []),
            "",
            f"Kontakt: {sucher_name} · {firmenname}",
        ]
    else:
        text_zeilen += [
            "",
            "Der vollständige Kaufvertrag liegt dieser E-Mail als PDF bei.",
            " ".join(t for t in ("Bitte prüfen Sie ihn in Ruhe.", fragen) if t),
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
        + (f'<div style="font:400 14px/1.6 Arial,Helvetica,sans-serif;color:{FARBE_TEXT};'
           f'margin-top:16px">{_absatz(anrede)}</div>' if anrede else "")
        + f'<div style="font:400 14px/1.6 Arial,Helvetica,sans-serif;color:{FARBE_TEXT};'
          f'margin-top:{12 if anrede else 16}px">{_absatz(eigener_text or "anbei erhalten Sie den Kaufvertrag für Ihr Fahrzeug als PDF.")}</div>'
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
            # RP-473: nur, wenn eine Antwort wirklich beim Sucher/der Firma ankommt
            + ('<br><br>Antworten auf diese E-Mail gehen direkt an '
               + escape(sucher_name) + '.' if antwort_ok else ""))
    )
    return betreff, text, _rahmen(inhalt)


def kopie_mail(*, vertrag: dict, firma: dict, sucher: dict,
               empfaenger_adresse: str, betreff_original: str,
               nachricht: str, zeitpunkt: str = None) -> Tuple[str, str, str]:
    """Beleg-E-Mail an den Sucher: was wurde wann an wen geschickt.

    zeitpunkt (ISO): Zeit des Versandeintrags. Nachpruefung Runde 10: Bei
    einer Wiederaufnahme muss die Kopie denselben Inhalt haben wie beim
    ersten Versuch — Resend lehnt einen wiederverwendeten Idempotenz-
    Schluessel mit anderem Inhalt ab. Ohne Angabe gilt die Uhr."""
    firmenname = (firma.get("company_name") or "").strip() or "Autohaus"
    titel = _fahrzeug_titel(vertrag)
    nummer = (vertrag.get("contract_no") or "").strip()
    empfaenger_name = (vertrag.get("seller_name") or "").strip()
    _dt = None
    if zeitpunkt:
        try:
            _dt = datetime.fromisoformat(str(zeitpunkt).replace("Z", "+00:00"))
            if _dt.tzinfo is not None:
                _dt = _dt.astimezone()
        except ValueError:
            _dt = None
    zeitpunkt = (_dt or datetime.now()).strftime("%d.%m.%Y um %H:%M Uhr")
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
