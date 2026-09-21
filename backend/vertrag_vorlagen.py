# -*- coding: utf-8 -*-
"""Standardtexte fuer Kaufvertrag, Mails und WhatsApp (Vorlage Ahmad 20.09.2026).

Diese Texte bekommt JEDE neu angelegte Firma. Jede Firma kann sie in den
Einstellungen ueberschreiben — hier steht nur der Startpunkt.

Alle Texte duerfen Platzhalter enthalten (siehe vertrag_platzhalter.py).
Fehlt eine Angabe, setzt der Server "____" ein — also genau die Luecke,
die frueher von Hand ausgefuellt wurde. Ein Kunde liest nie "{abholdatum}".

Die Korrektur-Vorlage nimmt der Versand-Dialog beim erneuten Versand
eines geaenderten Vertrags. Hinweis nach Kaufabschluss (Mail und WhatsApp)
und Bahnverbindung verschickt die App NIE (Wunsch Ahmad 21.09.2026) — sie
sind nur zum Kopieren da.
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

#: Woran der Vertrag erkennt, dass die Uebergabe (Datum UND Ort) schon in
#: den Besonderen Vereinbarungen steht — der feste Anfang unseres Satzes.
UEBERGABE_SATZ_ANFANG = "Die Fahrzeugübergabe findet bis/am"


def uebergabe_in_vereinbarungen(text) -> bool:
    """Steht die Uebergabe mit Datum und Ort schon in diesem Text?

    Wunsch Ahmad 21.09.2026: Hat der Vertrag unsere Besondere Vereinbarung,
    steht die Zeile "Abholung: Wird abgeholt am …" NICHT noch einmal ueber
    dem Kaufpreis — nur Vertraege ohne unseren Satz brauchen sie.

    Erkannt wird unser Satz am festen Anfang (auch wenn die Luecken schon
    von Hand ausgefuellt wurden) oder ein eigener Satz, der Abholdatum UND
    Uebergabeort als Platzhalter nennt — dann steht beides ebenfalls da.
    """
    roh = str(text or "")
    if UEBERGABE_SATZ_ANFANG in roh:
        return True
    return "{abholdatum}" in roh and "{ort}" in roh


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
# Wunsch Ahmad 21.09.2026: die Texte WOERTLICH nach seiner Vorlage. Zwei
# Angleichungen: "{Platzhalter}" heisst hier {kunde_name} bzw.
# {haendler_name}, und die Anrede lautet einheitlich "Sehr geehrte/r
# Frau/Herr" (in der Vorlage stand teils "Sehr geehrter Frau/Herr").
EMAIL_BETREFF = "KFZ-E-Mail-Bestätigung"

EMAIL_TEXT = (
    "Sehr geehrte/r Frau/Herr {kunde_name},\n\n"
    "vielen Dank für das nette Gespräch. Wie besprochen erhalten Sie im "
    "Anhang dieser E-Mail den Kaufvertrag. Bitte überprüfen Sie sorgfältig "
    "die im Kaufvertrag eingetragenen Daten und bestätigen Sie anschließend "
    "diese E-Mail.\n\n"
    "Vielen Dank\n"
    "Ihr Autohaus\n"
    "{haendler_name}"
)

#: Erneuter Versand, nachdem am Vertrag etwas geaendert wurde. Seit
#: 21.09.2026 nimmt der Versand-Dialog diese Vorlage von selbst, wenn schon
#: eine FRUEHERE Fassung desselben Vertrags verschickt wurde — und der
#: korrigierte Vertrag haengt wirklich an (vorher ging die Korrektur als
#: reine Textmail ohne Anhang raus, obwohl der Text einen Anhang ankuendigte).
EMAIL_BETREFF_KORREKTUR = "KFZ-E-Mail-Bestätigung – korrigierte Fassung"

EMAIL_TEXT_KORREKTUR = (
    "Sehr geehrte/r Frau/Herr {kunde_name},\n\n"
    "wie besprochen erhalten Sie im Anhang dieser E-Mail den Kaufvertrag in "
    "der korrigierten Fassung. Die vorherige Fassung ist damit hinfällig. "
    "Bitte überprüfen Sie sorgfältig die im Kaufvertrag eingetragenen Daten "
    "und bestätigen Sie anschließend diese E-Mail.\n\n"
    "Vielen Dank\n"
    "Ihr Autohaus\n"
    "{haendler_name}"
)

# ---------------------------------------------------------------- WhatsApp
WHATSAPP_TEXT = (
    "Sehr geehrte/r Frau/Herr {kunde_name},\n\n"
    "danke für das nette Gespräch bezüglich Ihres Fahrzeugs. Wie bereits "
    "besprochen, erhalten Sie nachfolgend den Kaufvertrag. Bitte überprüfen "
    "Sie diesen sorgfältig auf die darin gemachten Angaben, und bestätigen "
    "Sie ihn anschließend.\n\n"
    "Vielen Dank\n"
    "Ihr Autohaus\n"
    "{haendler_name}"
)

# ============================================== Vorlagen NUR zum Kopieren
# Wunsch Ahmad 21.09.2026: "wir selber schicken die nicht raus — diese
# Vorlagen sollen da nur sein, damit der Kunde sie immer kopieren kann und
# bei Mail selber einfügen kann". Die App verschickt den Hinweis nach
# Kaufabschluss und die Bahnverbindung NICHT. Sie stehen in den
# Einstellungen (mit Kopier-Knopf) und beim Vertrag im PDF-Archiv (dort mit
# eingesetztem Namen und Daten), der Sucher schickt sie selbst.

# ------------------------------------------------- Hinweis nach Abschluss
EMAIL_BETREFF_NACH_KAUF = "Bestätigung des Kaufvertrags"

_NACH_KAUF_KERN = (
    "Daher bitte ich Sie darum, weiteren Interessenten mitzuteilen, dass "
    "das Fahrzeug bereits verkauft ist.\n"
    "Sollte das Fahrzeug nach der Übergabe noch angemeldet sein, "
    "verpflichten wir uns, das Fahrzeug innerhalb von fünf Werktagen "
    "abzumelden. Bitte geben Sie keine Auskünfte über Kaufpreis, Abholzeit "
    "und Käufer aus. Ich melde mich stets eingangs des Gesprächs mit der "
    "Kundennummer. Nach telefonischer Vereinbarung wird ein Fahrer bei Ihnen "
    "erscheinen, der das Fahrzeug entgegennimmt und Ihnen die Kaufsumme wie "
    "vertraglich vereinbart mittels der im Vertrag festgelegten "
    "Zahlungsmethode überreicht.\n\n"
    "Ich bitte Sie ferner darum, das Inserat nun aus dem Netz zu nehmen.\n"
    "Bei weiteren Fragen können Sie uns gerne anrufen oder eine Email "
    "schreiben.\n\n"
    "Liebe Grüße\n"
    "Ihr Autohaus\n"
    "{haendler_name}"
)

#: Die Vorlage nennt die Bestaetigung "per WhatsApp" — die E-Mail-Fassung
#: sagt an dieser einen Stelle "per E-Mail", sonst ist sie gleich.
EMAIL_TEXT_NACH_KAUF = (
    "Sehr geehrte/r Frau/Herr {kunde_name},\n\n"
    "ich bedanke mich für die Bestätigung des Kaufvertrags per E-Mail. Auf "
    "Grundlage von Angebot und Annahme ist somit zwischen uns beiden ein "
    "rechtskräftiger und im Rechtsverkehr mustergültiger Vertrag zustande "
    "gekommen, der seine Gültigkeit hat.\n\n"
) + _NACH_KAUF_KERN

WHATSAPP_TEXT_NACH_KAUF = (
    "Sehr geehrte/r Frau/Herr {kunde_name},\n\n"
    "ich bedanke mich für die Bestätigung des Kaufvertrags per WhatsApp. Auf "
    "Grundlage von Angebot und Annahme ist somit zwischen uns beiden ein "
    "rechtskräftiger und im Rechtsverkehr mustergültiger Vertrag zustande "
    "gekommen, der seine Gültigkeit hat.\n\n"
) + _NACH_KAUF_KERN

# ------------------------------------------------------- Bahnverbindung
EMAIL_BETREFF_BAHN = "Bahnverbindung / Abholinformation"

EMAIL_TEXT_BAHN = (
    "Sehr geehrte/r Frau/Herr {kunde_name},\n\n"
    "anbei erhalten Sie die Bahnverbindung mit der voraussichtlichen "
    "Ankunftszeit unseres Fahrers.\n"
    "Sollte es zu einer Verspätung kommen, wird sich unser Fahrer bei Ihnen "
    "telefonisch melden.\n\n"
    "Vielen Dank\n"
    "Ihr Autohaus\n"
    "{haendler_name}"
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

#: Die Vorlagen, die die App NICHT verschickt: der Sucher kopiert sie (im
#: PDF-Archiv mit eingesetztem Namen und Daten) und schickt sie selbst.
#: Schluessel -> (Betreff-Feld, Text-Feld, Standardbetreff, Standardtext);
#: WhatsApp hat keinen Betreff (None).
KOPIER_VORLAGEN = {
    "nach_kauf": ("email_subject_nach_kauf", "email_template_nach_kauf",
                  EMAIL_BETREFF_NACH_KAUF, EMAIL_TEXT_NACH_KAUF),
    "nach_kauf_whatsapp": (None, "whatsapp_template_nach_kauf",
                           "", WHATSAPP_TEXT_NACH_KAUF),
    "bahn": ("email_subject_bahn", "email_template_bahn",
             EMAIL_BETREFF_BAHN, EMAIL_TEXT_BAHN),
}

#: Alle Vorlagen mit Vorschau beim Vertrag: die zum Kopieren und die
#: Korrektur (die der Versand-Dialog beim erneuten Versand nimmt).
FOLGE_MAILS = {
    "korrektur": ("email_subject_korrektur", "email_template_korrektur",
                  EMAIL_BETREFF_KORREKTUR, EMAIL_TEXT_KORREKTUR),
    **KOPIER_VORLAGEN,
}


def vorlage(firma: dict, art: str) -> tuple:
    """(Betreff, Text) einer Vorlage — eigener Text der Firma, sonst der
    Standard. Unbekannte Art -> ("", ""); WhatsApp -> Betreff ""."""
    eintrag = FOLGE_MAILS.get(art)
    if not eintrag:
        return "", ""
    betreff_feld, text_feld, betreff_std, text_std = eintrag
    firma = firma or {}
    betreff = ((firma.get(betreff_feld) or "").strip() or betreff_std) if betreff_feld else ""
    text = (firma.get(text_feld) or "").strip() or text_std
    return betreff, text


def mit_standardtexten(firma: dict) -> dict:
    """Leere Vorlagenfelder mit dem Standard fuellen — NUR fuer die Antwort.

    Firmen, die vor dem 20.09.2026 angelegt wurden, haben die Felder fuer
    Korrektur, Hinweis nach Kaufabschluss und Bahnverbindung nicht (und wer
    ein Feld geleert hat, hat es leer gespeichert). Die Oberflaeche zeigte
    dann leere Felder (Einstellungen) bzw. schickte keinen Text. Jetzt sieht
    jeder die wirksame Vorlage; gespeichert wird erst, was jemand selbst
    speichert. Aeltere, NICHT leere Standardtexte stellt die Migration
    m10_vorlagen_texte um.
    """
    if not isinstance(firma, dict):
        return firma
    for feld, standard in STARTWERTE.items():
        if not isinstance(standard, str) or not standard:
            continue                      # Schalter und leeres Freitextfeld
        if not str(firma.get(feld) or "").strip():
            firma[feld] = standard
    return firma
