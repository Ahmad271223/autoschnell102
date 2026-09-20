# -*- coding: utf-8 -*-
"""Standardtexte fuer Kaufvertrag, Mails und WhatsApp (Vorlage Ahmad 20.09.2026).

Diese Texte bekommt JEDE neu angelegte Firma. Jede Firma kann sie in den
Einstellungen ueberschreiben — hier steht nur der Startpunkt.

Alle Texte duerfen Platzhalter enthalten (siehe vertrag_platzhalter.py).
Fehlt eine Angabe, setzt der Server "____" ein — also genau die Luecke,
die frueher von Hand ausgefuellt wurde. Ein Kunde liest nie "{abholdatum}".

Die vier zusaetzlichen Vorlagen (Korrektur, Hinweis nach Kaufabschluss per
Mail und per WhatsApp, Bahnverbindung) gehoeren zu Mails, die der Sucher
NACHTRAEGLICH von Hand verschickt — sie gehen nie automatisch raus.
"""

# ---------------------------------------------------------------- Vertrag
#: Unser Standardsatz fuer die Besonderen Vereinbarungen. Die drei Luecken
#: des Papiervertrags sind Platzhalter: Abholdatum, Übergabeort (Anschrift
#: des Verkäufers) und Zahlungsart setzt der Server beim Erstellen ein.
#:
#: Wunsch Ahmad 20.09.2026: Dieser Block laesst sich EINSCHALTEN oder
#: ABSCHALTEN (dealers.sondervereinbarung_standard_aktiv), und darunter
#: kann jede Firma ihre EIGENEN Vereinbarungen schreiben und speichern
#: (dealers.default_special_agreements). Im Vertrag stehen dann beide
#: untereinander — erst unserer, dann der eigene.
BESONDERE_VEREINBARUNGEN = (
    "• Die Fahrzeugübergabe findet bis/am {abholdatum} in {ort} "
    "gegen {zahlungsart} statt.\n"
    "• Das Fahrzeug wird nur unter Vorlage der Kundennummer "
    "({kundennummer}) nach einem kurzen Gebrauchtwagencheck und "
    "Datenabgleich mit Zulassungsbescheinigung Teil I & II ausgehändigt."
)

#: Standardstellung des Schalters: AN. Eine Firma, die den Satz nicht will,
#: schaltet ihn in den Einstellungen ab.
STANDARD_SONDERVEREINBARUNG_AN = True


def standard_an(firma: dict) -> bool:
    """Ist unser Standardsatz fuer diese Firma eingeschaltet?

    Fehlt das Feld (Altbestand, gerade angelegte Firma), gilt AN — der Satz
    ist der Normalfall, das Abschalten die Ausnahme.
    """
    wert = (firma or {}).get("sondervereinbarung_standard_aktiv")
    if wert is None:
        return STANDARD_SONDERVEREINBARUNG_AN
    return bool(wert)


def sondervereinbarungen(firma: dict) -> str:
    """Der vollstaendige Text fuer den Vertrag: unser Standardsatz (falls
    eingeschaltet) und darunter der eigene Text der Firma.

    Die Platzhalter bleiben hier noch stehen — eingesetzt werden sie erst
    beim Erzeugen des PDF (vertrag_platzhalter.ersetzen), damit sie die
    Daten des KONKRETEN Vertrags bekommen.
    """
    firma = firma or {}
    teile = []
    if standard_an(firma):
        teile.append(BESONDERE_VEREINBARUNGEN)
    eigen = (firma.get("default_special_agreements") or "").strip()
    if eigen:
        teile.append(eigen)
    return "\n\n".join(teile)

# ---------------------------------------------------------------- E-Mail
EMAIL_BETREFF = "KFZ-E-Mail-Bestätigung"

EMAIL_TEXT = (
    "Sehr geehrte/r Frau/Herr {kunde_name},\n\n"
    "vielen Dank für das nette Gespräch. Wie besprochen erhalten Sie im "
    "Anhang dieser E-Mail den Kaufvertrag. Bitte überprüfen Sie sorgfältig "
    "die im Kaufvertrag eingetragenen Daten und bestätigen Sie anschließend "
    "diese E-Mail.\n\n"
    "Vielen Dank\n"
    "Ihr {haendler_name}"
)

#: Erneuter Versand, nachdem am Vertrag etwas geaendert wurde.
EMAIL_BETREFF_KORREKTUR = "KFZ-Kaufvertrag — korrigierte Fassung"

EMAIL_TEXT_KORREKTUR = (
    "Sehr geehrte/r Frau/Herr {kunde_name},\n\n"
    "im Anhang erhalten Sie den Kaufvertrag in der korrigierten Fassung. "
    "Die vorherige Fassung ist damit hinfällig. Bitte prüfen Sie die Angaben "
    "noch einmal und bestätigen Sie diese E-Mail.\n\n"
    "Vielen Dank\n"
    "Ihr {haendler_name}"
)

# ------------------------------------------------- Hinweis nach Abschluss
EMAIL_BETREFF_NACH_KAUF = "Bestätigung des Kaufvertrags"

_NACH_KAUF_KERN = (
    "Daher bitte ich Sie darum, weiteren Interessenten mitzuteilen, dass "
    "das Fahrzeug bereits verkauft ist.\n"
    "Sollte das Fahrzeug nach der Übergabe noch angemeldet sein, "
    "verpflichten wir uns, es innerhalb von fünf Werktagen abzumelden.\n"
    "Bitte geben Sie keine Auskünfte über Kaufpreis, Abholzeit und Käufer "
    "heraus. Ich melde mich stets zu Beginn des Gesprächs mit der "
    "Kundennummer. Nach telefonischer Vereinbarung erscheint ein Fahrer bei "
    "Ihnen, der das Fahrzeug entgegennimmt und Ihnen die Kaufsumme wie "
    "vertraglich vereinbart mittels der im Vertrag festgelegten "
    "Zahlungsmethode überreicht.\n\n"
    "Ich bitte Sie ferner darum, das Inserat nun aus dem Netz zu nehmen.\n"
    "Bei weiteren Fragen können Sie uns gerne anrufen oder eine E-Mail "
    "schreiben.\n\n"
    "Liebe Grüße\n"
    "Ihr {haendler_name}"
)

EMAIL_TEXT_NACH_KAUF = (
    "Sehr geehrte/r Frau/Herr {kunde_name},\n\n"
    "ich bedanke mich für das Rücksenden der Mail. Auf Grundlage von Angebot "
    "und Annahme ist somit zwischen uns beiden ein rechtskräftiger Vertrag "
    "zustande gekommen, der seine Gültigkeit hat.\n\n"
) + _NACH_KAUF_KERN

# ---------------------------------------------------------------- WhatsApp
WHATSAPP_TEXT = (
    "Sehr geehrte/r Frau/Herr {kunde_name},\n\n"
    "danke für das nette Gespräch bezüglich Ihres Fahrzeugs. Wie besprochen "
    "erhalten Sie nachfolgend den Kaufvertrag. Bitte überprüfen Sie ihn "
    "sorgfältig auf die darin gemachten Angaben und bestätigen Sie ihn "
    "anschließend.\n\n"
    "Vielen Dank\n"
    "Ihr {haendler_name}"
)

WHATSAPP_TEXT_NACH_KAUF = (
    "Sehr geehrte/r Frau/Herr {kunde_name},\n\n"
    "ich bedanke mich für die Bestätigung des Kaufvertrags per WhatsApp. Auf "
    "Grundlage von Angebot und Annahme ist somit zwischen uns beiden ein "
    "rechtskräftiger Vertrag zustande gekommen, der seine Gültigkeit hat.\n\n"
) + _NACH_KAUF_KERN

# ------------------------------------------------------- Bahnverbindung
EMAIL_BETREFF_BAHN = "Bahnverbindung / Abholinformation"

EMAIL_TEXT_BAHN = (
    "Sehr geehrte/r Frau/Herr {kunde_name},\n\n"
    "anbei erhalten Sie die Bahnverbindung mit der voraussichtlichen "
    "Ankunftszeit unseres Fahrers für den {abholdatum} in {ort}.\n"
    "Sollte es zu einer Verspätung kommen, meldet sich unser Fahrer "
    "telefonisch bei Ihnen.\n\n"
    "Vielen Dank\n"
    "Ihr {haendler_name}"
)


#: Die Vorlagen, die eine Firma in den Einstellungen pflegen kann —
#: Feldname -> Startwert. Wird beim Anlegen einer Firma gesetzt.
STARTWERTE = {
    "email_subject": EMAIL_BETREFF,
    "email_template": EMAIL_TEXT,
    "email_subject_korrektur": EMAIL_BETREFF_KORREKTUR,
    "email_template_korrektur": EMAIL_TEXT_KORREKTUR,
    "email_subject_nach_kauf": EMAIL_BETREFF_NACH_KAUF,
    "email_template_nach_kauf": EMAIL_TEXT_NACH_KAUF,
    "email_subject_bahn": EMAIL_BETREFF_BAHN,
    "email_template_bahn": EMAIL_TEXT_BAHN,
    "whatsapp_template": WHATSAPP_TEXT,
    "whatsapp_template_nach_kauf": WHATSAPP_TEXT_NACH_KAUF,
    # Das Freitextfeld bleibt LEER: unser Standardsatz kommt ueber den
    # Schalter dazu, hier stehen nur die eigenen Vereinbarungen der Firma.
    "default_special_agreements": "",
    "sondervereinbarung_standard_aktiv": STANDARD_SONDERVEREINBARUNG_AN,
}

#: Die drei Folge-Mails, die ein Sucher nachtraeglich von Hand schickt.
#: Schluessel -> (Betreff-Feld, Text-Feld, Standardbetreff, Standardtext).
FOLGE_MAILS = {
    "korrektur": ("email_subject_korrektur", "email_template_korrektur",
                  EMAIL_BETREFF_KORREKTUR, EMAIL_TEXT_KORREKTUR),
    "nach_kauf": ("email_subject_nach_kauf", "email_template_nach_kauf",
                  EMAIL_BETREFF_NACH_KAUF, EMAIL_TEXT_NACH_KAUF),
    "bahn": ("email_subject_bahn", "email_template_bahn",
             EMAIL_BETREFF_BAHN, EMAIL_TEXT_BAHN),
}


def vorlage(firma: dict, art: str) -> tuple:
    """(Betreff, Text) einer Folge-Mail — eigener Text der Firma, sonst der
    Standard. Unbekannte Art -> ("", "")."""
    eintrag = FOLGE_MAILS.get(art)
    if not eintrag:
        return "", ""
    betreff_feld, text_feld, betreff_std, text_std = eintrag
    firma = firma or {}
    betreff = (firma.get(betreff_feld) or "").strip() or betreff_std
    text = (firma.get(text_feld) or "").strip() or text_std
    return betreff, text
