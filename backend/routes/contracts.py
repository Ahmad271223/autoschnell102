"""Contract endpoints: preview, create, list, get, pdf, send, delete."""
import os
import asyncio
import base64
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Dict, List, Optional, Union

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

log = logging.getLogger("autohandel")


from vertrag_dateiname import content_disposition, sicherer_dateiname  # noqa: E402


def _safe_filename(name: str, fallback: str = "document.pdf") -> str:
    """Strip characters that could inject extra HTTP header lines or break parsers.

    Rollenpruefung 22.09.2026 (RP-200/RP-351): Der Name allein reicht fuer die
    Kopfzeile nicht mehr — 'Š', '–' oder ein Emoji liessen Starlette (latin-1)
    mit 500 scheitern. Die Kopfzeilen bauen jetzt alle ueber
    vertrag_dateiname.content_disposition (ASCII + filename*=UTF-8''…);
    dieser Name bleibt fuer Aufrufer, die nur den gesaeuberten Text brauchen.
    """
    return sicherer_dateiname(name, fallback=fallback)

from pymongo.errors import DuplicateKeyError

from deps import (
    TERMIN_OFFEN_WERTE, current_firma, fahrzeug_bereich, ist_sucher,
    clean_doc, db, log_activity, log_activity_sicher, now_iso,
    require_active_sub, datum_iso_pruefen, uhrzeit_hhmm_pruefen,
)
import auto_daten
from cleanup_service import LoeschungAbgelehnt, vertrag_endgueltig_loeschen
from lifecycle import try_set_lifecycle
from pdf_service import DIGITAL_NACHTRAEGLICH, generate_contract_pdf, digitaler_vertragstext
from rate_limiter import SlidingWindowRateLimiter

router = APIRouter()


# ---------- Models ----------
class DamageIn(BaseModel):
    """Ein Eintrag der Schadens-Skizze (DamageSelector.jsx).

    Nachpruefung Runde 14: `damages` war eine ungepruefte `list` — ein
    String-Element liess pdf_service (`d.get(...)`) mit 500 abstuerzen, und
    Anzahl/Laenge waren unbegrenzt (3000 Eintraege mit 200k-Zeichen-Zone
    wanderten komplett in contract_data). Jetzt: nur Objekte, nur die
    Schluessel, die Skizze/PDF/auto_daten/Abholprotokoll tatsaechlich lesen
    (type_label, type_key, zone; id/view/abbr/color/x/y fuer die Anzeige),
    Strings gedeckelt wie die uebrigen Tabellenfelder. Unbekannte Schluessel
    werden ignoriert, nicht abgelehnt (aeltere Oberflaechen)."""
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    id: Optional[str] = Field(default=None, max_length=500)
    view: Optional[str] = Field(default=None, max_length=500)
    type_key: Optional[str] = Field(default="", max_length=500)
    type_label: Optional[str] = Field(default="", max_length=500)
    # Aeltere Oberflaechen/Tests schicken Freitext-Eintraege mit diesen
    # Schluesseln; auto_daten.schaeden_bereinigen liest sie weiterhin.
    label: Optional[str] = Field(default=None, max_length=500)
    type: Optional[str] = Field(default=None, max_length=500)
    kategorie: Optional[str] = Field(default=None, max_length=500)
    part_label: Optional[str] = Field(default=None, max_length=500)
    part: Optional[str] = Field(default=None, max_length=500)
    note: Optional[str] = Field(default=None, max_length=500)
    text: Optional[str] = Field(default=None, max_length=500)
    abbr: Optional[str] = Field(default=None, max_length=500)
    color: Optional[str] = Field(default=None, max_length=500)
    zone: Optional[str] = Field(default="", max_length=500)
    # Wunsch Ahmad 25.09.2026 (KI-Schadennachlass): wenige Zusatzmerkmale je
    # Schadensart (Groesse, Lack beschaedigt, Laenge, Funktion) — als kleines
    # Woerterbuch Text -> Text, hoechstens 8 Eintraege.
    severity_data: Optional[Dict[str, str]] = None

    @field_validator("severity_data", mode="before")
    @classmethod
    def _severity_deckeln(cls, v):
        if v is None or v == {}:
            return None
        if not isinstance(v, dict) or len(v) > 8:
            raise ValueError("severity_data: hoechstens 8 Merkmale")
        return {str(k)[:40]: str(w)[:60] for k, w in v.items() if str(k or "").strip()}
    # Runde 17 (Nr. 338): Skizzen-Koordinaten sind Prozent-/Pixelwerte —
    # inf/nan und Riesenzahlen (JSON-Serialisierung, ReportLab-Layout)
    # werden abgelehnt statt bis ins PDF durchgereicht.
    x: Optional[float] = Field(default=None, allow_inf_nan=False, ge=-10000, le=10000)
    y: Optional[float] = Field(default=None, allow_inf_nan=False, ge=-10000, le=10000)


def _monat_jahr_normieren(v, art: str) -> str:
    """P-20: '2027-03' / '3.2027' / '06/26' -> 'MM/JJJJ' (Regel wie
    protokoll_vergleich.monat_jahr_text); Unlesbares wird abgelehnt."""
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    from protokoll_vergleich import monat_jahr_text
    norm = monat_jahr_text(s, art=art)
    m = re.fullmatch(r"(0[1-9]|1[0-2])/(\d{4})", norm)
    if not m or not (1900 <= int(m.group(2)) <= 2100):
        raise ValueError("Bitte als Monat/Jahr angeben (MM/JJJJ)")
    return norm


class ContractIn(BaseModel):
    # Pruefung 14.09.2026 (Liste 3, Nr. 1): Idempotenz je Anlage — ein Doppelklick
    # oder eine verlorene Antwort mit Wiederholung legt keinen zweiten Vertrag an.
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=80,
                                           pattern=r"^[A-Za-z0-9_\-]+$")

    @field_validator("seller_name")
    @classmethod
    def _verkaeufer_pflicht(cls, v: str) -> str:
        # Pruefung 14.09.2026 (Liste 3, Nr. 8): kein Vertrag ohne Verkaeufer.
        v = (v or "").strip()
        if not v:
            raise ValueError("Bitte den Namen des Verkäufers angeben")
        return v

    # Numbers from listings (e.g. mileage, power_kw, doors, seats) arrive
    # as JSON numbers from the frontend. Coerce them to strings instead
    # of failing the request with "Input should be a valid string".
    model_config = ConfigDict(coerce_numbers_to_str=True)

    vehicle_id: str
    seller_name: str
    seller_address: Optional[str] = ""
    # Runde 17 (Nr. 347): PLZ/Ort landen in der Abholadresse des Termins —
    # realistische Deckel statt der allgemeinen 500 Zeichen.
    seller_zip: Optional[str] = Field(default="", max_length=20)
    seller_city: Optional[str] = Field(default="", max_length=100)
    seller_phone: Optional[str] = ""
    seller_email: Optional[str] = ""
    id_document: Optional[str] = ""
    # Runde 17 (Nr. 338): inf/nan sind kein Kaufpreis (auto_daten rechnet
    # Cent daraus, das PDF druckt ihn).
    # Pruefung 14.09.2026 (Liste 3, Nr. 2): kein 0-Euro-Kaufvertrag.
    purchase_price: float = Field(gt=0, allow_inf_nan=False,
                                  description="Kaufpreis darf nicht negativ sein")
    # Runde 22 (11.09.2026, Vorlage Ahmad): Das Formular bietet jetzt die
    # Auswahl Bar | Überweisung | Echtzeitüberweisung. Bewusst KEIN hartes
    # Enum — der Standard und Freitext alter Clients/Vertraege bleiben
    # gueltig (gemischter Rollout zweier Server); gedeckelt wie alle engen
    # Felder durch _cap_string_length (500 Zeichen).
    payment_method: Optional[str] = "Bar / Überweisung"
    pickup_date: Optional[str] = ""
    pickup_time: Optional[str] = ""

    # Runde 17 (Nr. 346): Der Vertragsweg legte den Abholtermin bisher
    # UNGEPRUEFT an (der Terminplaner prueft laengst) — Datum als
    # JJJJ-MM-TT, Uhrzeit als HH:MM, leer bleibt leer. Regel zentral in deps.
    @field_validator("pickup_date")
    @classmethod
    def _pickup_date_pruefen(cls, v):
        return datum_iso_pruefen(v)

    @field_validator("service_book")
    @classmethod
    def _service_book_pruefen(cls, v):
        wert = (v or "").strip().lower()
        if wert not in ("", "ja", "nein", "teilweise"):
            raise ValueError("Scheckheftgepflegt: ja, nein oder teilweise")
        return wert

    @field_validator("pickup_time")
    @classmethod
    def _pickup_time_pruefen(cls, v):
        return uhrzeit_hhmm_pruefen(v)

    additional_terms: Optional[str] = ""
    notes: Optional[str] = ""
    # Zusicherungen & Zustand (manuell durch Händler ergänzbar)
    tires: Optional[str] = ""              # "4-fach" | "8-fach" | "keine" | ""
    hu_valid: Optional[str] = ""           # "Ja" | "Nein" | ""
    hu_until: Optional[str] = ""           # MM/JJJJ frei
    # Wunsch Ahmad (15.09.2026): Scheckheftgepflegt als Auswahl; bei
    # "teilweise" der Monat/Jahr, bis zu dem das Scheckheft gefuehrt wurde.
    service_book: Optional[str] = ""       # "" | "ja" | "nein" | "teilweise"
    service_book_until: Optional[str] = ""  # MM/JJJJ, nur bei "teilweise"

    @field_validator("hu_until", "service_book_until")
    @classmethod
    def _monat_jahr_pruefen(cls, v, info):
        # Pruefbericht 20.09.2026 (P-20): Der Dialog normiert auf MM/JJJJ, die
        # API nahm aber jeden Text ("2027-03", "3.2027", "bald") und das PDF
        # druckte ihn roh. Jetzt wie monatJahrAusText im Browser: lesbare
        # Formen werden zu MM/JJJJ, alles andere wird abgelehnt; leer bleibt leer.
        return _monat_jahr_normieren(v, art="hu" if info.field_name == "hu_until" else "ez")

    accident_free: Optional[str] = ""      # "Ja" | "Nein" | ""
    accident_location: Optional[str] = ""  # nur wenn accident_free == "Nein"
    eu_import: Optional[str] = ""          # "Ja" | "Nein" | ""
    drivable: Optional[str] = ""           # "Ja" | "Nein" | ""
    commercial_since_ez: Optional[str] = ""  # "Ja" | "Nein" | ""
    # Rollenpruefung 22.09.2026 (RP-404): None = nicht angegeben (dann gilt
    # der Wert aus dem Inserat), "" = im Dialog bewusst geleert (dann steht
    # KEINE Angabe im Vertrag). Vorher war "" der Standard, und pdf_service
    # holte bei "" still den Inseratswert zurueck.
    # Rollenpruefung 22.09.2026 (RP-430): gemeint ist die ANZAHL DER
    # FAHRZEUGHALTER (so liefern sie mobile.de, AutoScout und Kleinanzeigen,
    # "2. Hand" = 2) — nicht die Zahl der Vorbesitzer.
    previous_owners: Optional[str] = None  # Anzahl Fahrzeughalter (lt. Inserat / Händler)
    # Runde 22 (11.09.2026, Vorlage Ahmad): Zulassungsstatus des Fahrzeugs
    # und die Empfangsbestaetigung im Abschnitt "Unterschriften" —
    # Kaeufer: Zulassungsbescheinigung Teil I & II, KFZ mit n Schluessel(n);
    # Verkaeufer: Kaufpreis; je Seite "Datum und Ort". Alles optional:
    # Altvertraege ohne Felder bekommen leere Kaestchen und eine Linie.
    zulassung: Optional[str] = ""                      # "" | "angemeldet" | "abgemeldet"
    empfang_zulassungsbescheinigung: Optional[bool] = False
    empfang_schluessel: Optional[bool] = False
    schluessel_anzahl: Optional[str] = ""               # leer oder 1-2 Ziffern
    empfang_kaufpreis: Optional[bool] = False
    empfang_datum: Optional[str] = ""                   # leer oder JJJJ-MM-TT
    empfang_ort_kaeufer: Optional[str] = Field(default="", max_length=100)
    empfang_ort_verkaeufer: Optional[str] = Field(default="", max_length=100)

    @field_validator("zulassung")
    @classmethod
    def _zulassung_pruefen(cls, v):
        if v is None:
            return v
        s = str(v).strip().lower()
        if s not in ("", "angemeldet", "abgemeldet"):
            raise ValueError("Zulassung muss 'angemeldet' oder 'abgemeldet' sein")
        return s

    @field_validator("schluessel_anzahl")
    @classmethod
    def _schluessel_anzahl_pruefen(cls, v):
        # coerce_numbers_to_str macht aus der Zahl 2 bereits "2".
        if v is None:
            return v
        s = str(v).strip()
        if s and not re.fullmatch(r"[0-9]{1,2}", s):
            raise ValueError("Schlüsselanzahl bitte als Zahl (1-2 Ziffern) angeben")
        return s

    @field_validator("empfang_datum")
    @classmethod
    def _empfang_datum_pruefen(cls, v):
        return datum_iso_pruefen(v)

    @model_validator(mode="after")
    def _hu_ohne_gueltigkeit(self):
        # Rollenpruefung 22.09.2026 (RP-405): "HU/AU: Nein, gültig bis
        # 05/2027" stand im Vertrag — der Dialog sperrte das Datumsfeld nur,
        # statt es zu leeren. Ohne HU gibt es kein "gültig bis".
        if str(self.hu_valid or "").strip().lower() == "nein":
            self.hu_until = ""
        return self

    # Auto-prefilled from listing description but editable per contract.
    # Rollenpruefung 22.09.2026 (RP-404): None = nicht angegeben (dann die
    # Inseratsbeschreibung), "" = im Dialog bewusst geleert (keine
    # Beschreibung im Vertrag). Vorher kam eine geleerte Beschreibung still
    # aus dem Inserat zurueck.
    vehicle_description: Optional[str] = None
    # Rollenpruefung 22.09.2026 (RP-416): hat DERSELBE Sucher zu diesem
    # Fahrzeug schon einen offenen Kaufvertrag, fragt die Anlage erst nach
    # (409 mit Vertragsnummer). Mit true legt er bewusst einen zweiten an
    # (Nachverhandlung). Steuerfeld — kommt nicht in den Vertrag.
    zweiter_vertrag_bestaetigt: Optional[bool] = False
    # Stufe 3 KI-Schadennachlass (26.09.2026): die Bewertung, die der Sucher
    # vor dem Erstellen gesehen hat — Steuerfeld fuer den Lernfall (KI-Empfehlung
    # gegen den tatsaechlichen Vertragspreis), kommt nicht in den Vertrag.
    ki_bewertung_id: Optional[str] = Field(default=None, max_length=100)
    # Optional override for the dealer's default AGB block. If empty,
    # the dealer's saved AGB are used (current behaviour).
    agb_text: Optional[str] = ""
    # Wunsch Ahmad 18.09.2026: Die Vertragsbedingungen ("Allgemeine
    # Vertragsbedingungen" im PDF) standen bisher nur in den Einstellungen und
    # wurden still uebernommen. Jetzt stehen sie sichtbar im Vertragsdialog
    # und lassen sich fuer DIESEN Vertrag aendern. Leer/fehlend = weiterhin
    # der Text aus den Einstellungen (bzw. der Standardtext).
    digital_vertragstext: Optional[str] = None
    # Schäden / Beschädigungen aus der interaktiven Skizze
    damages_text: Optional[str] = ""
    # Nachpruefung Runde 14: geprueftes Schema statt roher Liste — siehe
    # DamageIn. Erlaubt sind Skizzen-Eintraege (DamageIn) ODER reiner
    # Freitext je Schaden (beides liest auto_daten.schaeden_bereinigen, das
    # selbst auf 300 Zeichen kuerzt). Deckel: 200 Eintraege, Freitext 5000
    # Zeichen (Validator unten) — reine DoS-Bremse, bestehende Ablaeufe
    # schicken bis zu ~80 Eintraege mit bis zu 1000 Zeichen.
    damages: Optional[List[Union[DamageIn, str]]] = Field(default_factory=list, max_length=200)

    @field_validator("damages")
    @classmethod
    def _schaeden_freitext_deckeln(cls, v):
        for eintrag in v or []:
            if isinstance(eintrag, str) and len(eintrag) > 5000:
                # auto_daten kuerzt selbst auf 300 Zeichen; hier nur die DoS-Bremse
                raise ValueError("Schaden-Text zu lang (hoechstens 5000 Zeichen)")
        return v
    # Gewerblicher Verkauf: MwSt (19 %) im Vertrag ausweisen —
    # der Kaufpreis gilt dann als Brutto, das PDF rechnet Netto/MwSt aus.
    show_vat: Optional[bool] = False
    # Fahrzeugdaten — werden im Vertrags-Dialog editierbar vorbefüllt.
    # Wenn ein Feld leer ist, fällt das PDF auf den Wert aus dem
    # Vehicle-Dokument zurück, sodass alte Verträge weiter funktionieren.
    vehicle_make: Optional[str] = None
    vehicle_model: Optional[str] = None
    vehicle_category: Optional[str] = None
    vehicle_first_registration: Optional[str] = None
    vehicle_mileage: Optional[str] = None
    vehicle_fuel: Optional[str] = None
    vehicle_gearbox: Optional[str] = None
    vehicle_power_kw: Optional[str] = None
    vehicle_power_ps: Optional[str] = None
    vehicle_displacement: Optional[str] = None
    vehicle_color: Optional[str] = None
    vehicle_doors: Optional[str] = None
    vehicle_seats: Optional[str] = None
    vehicle_vin: Optional[str] = None
    vehicle_license_plate: Optional[str] = None
    vehicle_damage_note: Optional[str] = None
    # Händler-Override pro Vertrag (z.B. abweichende Telefonnummer)
    dealer_company: Optional[str] = None
    dealer_contact: Optional[str] = None
    dealer_phone: Optional[str] = None
    dealer_whatsapp: Optional[str] = None
    dealer_email: Optional[str] = None
    dealer_address: Optional[str] = None
    dealer_zip: Optional[str] = None
    dealer_city: Optional[str] = None

    # DoS- & Layout-Schutz: ReportLab braucht fuer riesige Strings extrem viel
    # CPU (2 MB seller_name = ~28 s + HTTP 500) und kann lange Strings in engen
    # Tabellenzellen gar nicht setzen (Flowable-too-large -> 500).
    # Gestaffelte Caps, sofort bei der Validierung (422):
    #   - Freitext-Bloecke (volle Breite, fliessen ueber Seiten): 20.000 Zeichen
    #   - alle uebrigen Felder (enge Tabellenzellen): 500 Zeichen
    @field_validator("*")
    @classmethod
    def _cap_string_length(cls, v, info):
        if isinstance(v, str):
            long_fields = {
                "additional_terms", "notes", "agb_text",
                "vehicle_description", "damages_text", "digital_vertragstext",
            }
            limit = 20000 if info.field_name in long_fields else 500
            if len(v) > limit:
                raise ValueError(
                    f"Feld '{info.field_name}' zu lang (max. {limit} Zeichen)"
                )
        return v


class SendIn(BaseModel):
    channel: str  # "whatsapp" | "email"
    recipient: str = Field(max_length=200)
    subject: Optional[str] = Field(default=None, max_length=500)
    message: str = Field(max_length=20000)
    # Doppelversand-Schutz: gleicher Schluessel -> garantiert nur EIN
    # Eintrag, auch bei Doppelklick, Netz-Wiederholung oder verlorener
    # Antwort. Das Frontend erzeugt je Klick eine UUID.
    idempotency_key: Optional[str] = Field(default=None, max_length=100)
    # WhatsApp (09.09.2026): "link" (Standard) = Chat oeffnet sich mit einem
    # zeitlich begrenzten Download-Link zur digitalen Fassung im Text;
    # "teilen" = das Handy hat das PDF ueber das Teilen-Menue an WhatsApp
    # uebergeben (von der eigenen Nummer des Suchers) — nur Vermerk.
    methode: Optional[str] = Field(default=None, max_length=20)
    # Pruefbericht 20.09.2026 (U-73): bei methode="teilen" die Fassung der
    # Datei, die das Handy WIRKLICH an WhatsApp uebergeben hat (aus der
    # Kopfzeile X-Vertrag-Version beim Vorabladen). Weicht sie von der
    # aktuellen Fassung ab, gibt es keinen Vermerk (409) — vorher blieb eine
    # im 45-s-Fenster veraltete geteilte Datei unerkannt.
    version: Optional[int] = Field(default=None, ge=1)

    @field_validator("methode")
    @classmethod
    def _methode_bekannt(cls, v):
        if v is not None and v not in ("link", "teilen"):
            raise ValueError("methode muss 'link' oder 'teilen' sein")
        return v

    @model_validator(mode="after")
    def _kanal_und_methode(self):
        # Runde 16 (15.09.2026): kein widerspruechlicher Versanddatensatz —
        # `methode` gibt es nur fuer WhatsApp.
        if self.channel not in ("whatsapp", "email"):
            raise ValueError("channel muss 'whatsapp' oder 'email' sein")
        if self.methode and self.channel != "whatsapp":
            raise ValueError("methode gilt nur fuer den Kanal whatsapp")
        return self


def _versand_anfrage_hash(c: dict, body) -> str:
    """Runde 16: der Versand-Schluessel gehoert zu GENAU diesem Inhalt —
    Fassung, Kanal, Empfaenger, Betreff, Nachricht, Methode. Derselbe
    Schluessel mit anderem Inhalt ist ein Fehler, nie eine Wiederaufnahme."""
    import hashlib
    roh = "|".join([str(c.get("version") or 1), body.channel, (body.recipient or "").strip(),
                    body.subject or "", body.message or "", body.methode or ""])
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:24]


VERSAND_JE_KONTO_10MIN = int(os.environ.get("VERSAND_JE_KONTO_10MIN", "300") or 300)
WA_ZIFFERN_MIN, WA_ZIFFERN_MAX = 7, 15


# ---------- Helpers ----------
def _pdfs_erzeugen(*, dealer: dict, vehicle: dict, contract: dict) -> tuple[bytes, bytes]:
    """Beide Fassungen eines Vertrags in EINEM Thread-Aufruf: Druckfassung
    (mit Unterschriftslinien) und digitale Ausfertigung (Text statt Linien —
    das ist die Fassung, die per E-Mail/WhatsApp verschickt wird)."""
    druck = generate_contract_pdf(dealer=dealer, vehicle=vehicle, contract=contract)
    digital = generate_contract_pdf(dealer=dealer, vehicle=vehicle, contract=contract,
                                    digital=True)
    return druck, digital


# Runde 18 (Pruefbefund Blocker): Fehlt einem Altvertrag der digitale Text,
# darf NICHT der heute eingestellte Text irgendeines Kontos eingesetzt werden
# — das wuerde den historischen Vertragsinhalt nachtraeglich veraendern.
# Stattdessen ein klarer Hinweis, dass die Fassung nachtraeglich entstanden
# ist und ihr Text nicht Teil des damals geschlossenen Vertrags war.
# DIGITAL_NACHTRAEGLICH liegt seit 10.09.2026 in pdf_service (dort wird er erkannt).


async def _digitales_pdf_bytes(c: dict, user: dict, cache: bool = True) -> Optional[bytes]:
    """Digitale Ausfertigung eines gespeicherten Vertrags (fuer Versand und
    Download). Fehlt sie (Altvertrag), wird sie aus den GESPEICHERTEN
    Vertragsdaten nacherzeugt: der bei der Erstellung festgehaltene digitale
    Text, sonst der Nachtraeglich-Hinweis. Weder der Text noch die Identitaet
    des Abrufenden fliessen ein — zwei Abrufe liefern dasselbe Dokument.
    Schlaegt die Erzeugung fehl, liefert die Funktion None — die Aufrufer
    melden das als Fehler. Frueher kam still die Druckfassung MIT
    Unterschriftslinien, obwohl "digital" zugesagt war (Pruefbefund)."""
    if c.get("pdf_digital_b64"):
        return base64.b64decode(c["pdf_digital_b64"])
    try:
        contract_dict = dict(c.get("contract_data") or {})
        v = await db.vehicles.find_one(
            {"id": c.get("vehicle_id"), "dealer_id": c.get("dealer_id")}, {"_id": 0}) or {}
        vehicle = dict(v.get("data") or {})
        # Die im Vertrag festgehaltenen Firmenangaben (Name, Anschrift,
        # Telefon) gewinnen ueber _apply_contract_overrides. Der ABRUFENDE
        # fliesst nie ein (das machte das Dokument abrufabhaengig).
        # Pruefbericht 20.09.2026 (P-13): Basis ist wie bei der Neuerzeugung
        # (regenerate_contract_for_pickup) der ERSTELLER des Vertrags ueber
        # kaeufer_basis — das rohe Haendler-Dokument gab Altvertraegen ohne
        # eingefrorene Kaeuferdaten die Chef-Identitaet, und die wurde gecacht.
        from auftraggeber import kaeufer_basis
        dealer = await kaeufer_basis(dealer_id=c.get("dealer_id"),
                                     user_ids=(c.get("user_id"),)) or {}
        gespeichert = (contract_dict.get("digital_vertragstext") or "").strip()
        contract_dict["digital_vertragstext"] = gespeichert or DIGITAL_NACHTRAEGLICH
        vehicle, dealer = _apply_contract_overrides(
            contract=contract_dict, vehicle=vehicle, dealer=dealer)
        # Rollenpruefung 22.09.2026 (RP-452): nur das beim Vertrag
        # festgehaltene Logo (Altvertraege: keins).
        dealer = await _logo_einsetzen(dealer, contract_dict)
        pdf_bytes = await asyncio.to_thread(
            generate_contract_pdf, dealer=dealer, vehicle=vehicle,
            contract=contract_dict, digital=True)
        if not cache:
            # Archivfassung (Runde 16): nie in das Hauptdokument schreiben.
            return pdf_bytes
        # Nur das PDF zwischenspeichern — contract_data bleibt unangetastet,
        # der historische Vertragsinhalt aendert sich nicht.
        res = await db.generated_pdfs.update_one(
            {"id": c["id"], "version": c.get("version"),
             "pdf_digital_b64": {"$exists": False}},
            {"$set": {"pdf_digital_b64": base64.b64encode(pdf_bytes).decode(),
                      "pdf_digital_nachtraeglich": not gespeichert}})
        if res.matched_count == 0:
            # Runde 16 (15.09.2026): der Cache-CAS verlor — entweder hat ein
            # paralleler Abruf dieselbe Fassung gerade abgelegt (dann die
            # nehmen), oder der Vertrag wurde WAEHREND der Erzeugung neu
            # erstellt: dann darf die eben erzeugte alte Fassung nicht raus.
            frisch = await db.generated_pdfs.find_one(
                {"id": c["id"]}, {"_id": 0, "version": 1, "pdf_digital_b64": 1})
            if frisch and int(frisch.get("version") or 1) == int(c.get("version") or 1):
                if frisch.get("pdf_digital_b64"):
                    return base64.b64decode(frisch["pdf_digital_b64"])
                return pdf_bytes
            log.warning("Vertrag %s wurde waehrend der PDF-Erzeugung neu erstellt — "
                        "alte Fassung verworfen", c.get("id"))
            return None
        return pdf_bytes
    except Exception:
        log.exception("Digitale Ausfertigung von Vertrag %s konnte nicht erzeugt "
                      "werden", c.get("id"))
    return None


DIGITAL_FEHLER_HINWEIS = ("Die digitale Vertragsfassung konnte nicht erzeugt werden. "
                          "Bitte in ein paar Minuten erneut versuchen — ersatzweise "
                          "die Druckfassung herunterladen und von Hand anhängen.")


def _anhang_zu_gross_hinweis() -> str:
    """Pruefbericht 20.09.2026 (P-18): Das PDF ueberschreitet die Anhang-Grenze
    des Mailversands — klare Meldung mit dem Link-Weg statt 'Versand
    fehlgeschlagen, spaeter erneut versuchen'."""
    import email_service
    mb = email_service.MAIL_ANHANG_MAX_BYTES / (1024 * 1024)
    return (f"Das Vertrags-PDF ist größer als {mb:g} MB und kann nicht als E-Mail-Anhang "
            "verschickt werden — der Vertrag wurde NICHT versendet. Bitte per WhatsApp "
            "mit Download-Link teilen oder das PDF herunterladen und selbst anhängen.")


def wa_nummer(recipient: Optional[str]) -> str:
    """Telefonnummer fuer wa.me: nur Ziffern MIT Laendervorwahl, ohne '+'.
    Befund Ahmad 10.09.2026: Verkaeufer-Nummern stehen meist deutsch
    ("0170 1234567" oder "+49 170 …"); wa.me/01701234567 meldet "ungueltig"
    und oeffnet keinen Chat. Regeln: '00' am Anfang weg, eine fuehrende '0'
    wird zu '49' (deutsche Nummer), '+49…' bleibt 49…

    Rollenpruefung 22.09.2026 (RP-515): "+49 (0)1515 1747777" (so liefert
    mobile.de die Nummer) wurde zu 49015151747777 — wa.me meldet dann
    "ungueltige Nummer". Die "(0)" nach der Laendervorwahl ist nur eine
    Lesehilfe und faellt jetzt weg; ebenso eine direkt nach +49/0049
    geschriebene 0 ("+49 0170 …")."""
    roh = re.sub(r"\(\s*0\s*\)", "", str(recipient or "")).strip()
    digits = "".join(ch for ch in roh if ch.isdigit())
    international = roh.startswith("+") or digits.startswith("00")
    if roh.startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) >= 6:
        return "49" + digits[1:]
    if international and digits.startswith("490"):
        # deutsche Nummer mit Fern-Null nach der Laendervorwahl
        digits = "49" + digits[3:]
    return digits


# ---------- Oeffentlicher Download-Link (WhatsApp am PC) ----------
# Der wa.me-Weg kann kein PDF anhaengen. Damit der Verkaeufer den Vertrag
# trotzdem ohne Umweg bekommt, steht in der WhatsApp-Nachricht ein Link auf
# die digitale Fassung: zufaelliges Token (32 Byte), ohne Anmeldung
# abrufbar, zeitlich begrenzt (VERTRAG_LINK_TAGE, Standard 14 Tage), je IP
# gedrosselt, und mit der Loeschung des Vertrags automatisch tot.
VERTRAG_LINK_TAGE = max(1, min(int(os.environ.get("VERTRAG_LINK_TAGE") or 14), 365))
_link_limiter = SlidingWindowRateLimiter(max_attempts=60, window_seconds=60,
                                         name="vertrag_link")
# Runde 16 (15.09.2026): Versand je Konto gedeckelt (VERSAND_JE_KONTO_10MIN).
_versand_limiter = SlidingWindowRateLimiter(
    max_attempts=int(os.environ.get("VERSAND_JE_KONTO_10MIN", "300") or 300),
    window_seconds=600, name="vertrag_versand")


def _oeffentliche_basis() -> str:
    """Basis-Adresse NUR aus der Server-Konfiguration (wie beim Passwort-
    Reset) — nie aus Origin/Referer des Aufrufers."""
    return (os.environ.get("FRONTEND_URL") or "http://localhost:3000").split("?")[0].rstrip("/")


def _freigabe_gueltig(f: dict, version: int) -> bool:
    """Ein bestehender Link wird nur wiederverwendet, wenn er noch mindestens
    einen Tag laeuft UND auf die AKTUELLE Vertragsfassung zeigt (Runde 18:
    nach einer Terminverschiebung ist die alte Freigabe an die alte Fassung
    gebunden — der neue Versand braucht einen neuen Link)."""
    if not (f or {}).get("token") or int(f.get("version") or 0) != int(version or 1):
        return False
    try:
        return datetime.fromisoformat(f.get("laeuft_ab") or "") \
            - datetime.now(timezone.utc) > timedelta(days=1)
    except ValueError:
        return False


# Befund 49 (16.09.2026): so viele noch laufende Alt-Links bleiben abrufbar.
FREIGABE_ALT_MAX = 50


async def _abruf_zaehlen(contract_id: str, token: str, aktuell: bool) -> None:
    """Befund 50 (16.09.2026): Abrufe auch fuer einen ALTEN Link (freigabe_alt)
    zaehlen — vorher traf der Zaehler nur den aktuellen Token, die Statistik
    im Vertrag blieb fuer historisierte Links auf 0."""
    # Nr. 3 (20.09.2026): Der oeffentliche Link ist ein GET und kaeme damit
    # an der Schreibpause vorbei. Ein Abrufzaehler ist kein Geschaeftsvorgang
    # — waehrend einer Sicherung wird er einfach ausgelassen.
    import wartung as _wartung
    if await _wartung.schreiben_pausiert(db):
        return
    if aktuell:
        await db.generated_pdfs.update_one(
            {"id": contract_id, "freigabe.token": token},
            {"$inc": {"freigabe.abrufe": 1},
             "$set": {"freigabe.zuletzt_abgerufen": now_iso()}})
    else:
        await db.generated_pdfs.update_one(
            {"id": contract_id, "freigabe_alt.token": token},
            {"$inc": {"freigabe_alt.$.abrufe": 1},
             "$set": {"freigabe_alt.$.zuletzt_abgerufen": now_iso()}})


async def _freigabe_link(contract_id: str, bereich: dict, user: dict) -> tuple[str, str]:
    """Liefert (Link, gueltig_bis) fuer die AKTUELLE Vertragsfassung.

    Runde 18: Die Freigabe wird ATOMAR gesetzt — zwei gleichzeitige Versande
    erzeugten vorher zwei Tokens, von denen eines sofort wieder ungueltig war
    (beide Antworten meldeten Erfolg). Jetzt gewinnt genau einer, der andere
    bekommt dessen Link. Ein noch gueltiger Link derselben Fassung wird
    wiederverwendet; eine bestehende Gueltigkeitszusage wird nie verkuerzt."""
    # Runde 16 (15.09.2026): (a) verliert der CAS, wird komplett neu gelesen und
    # die dann gespeicherte Freigabe gegen die AKTUELLE Fassung geprueft — sonst
    # bekam ein neuer Versand nach einem Versions-Race den Link der alten
    # Fassung; (b) ein noch laufender alter Link wandert in freigabe_alt und
    # bleibt bis zu seinem Ablauf abrufbar (die Zusage im Chat wird nicht
    # gebrochen).
    for _ in range(4):
        c = await db.generated_pdfs.find_one({"id": contract_id, **bereich},
                                             {"_id": 0, "freigabe": 1, "version": 1})
        if c is None:
            raise HTTPException(404, "Vertrag nicht gefunden")
        version = int(c.get("version") or 1)
        f = c.get("freigabe") or {}
        if _freigabe_gueltig(f, version):
            return f"{_oeffentliche_basis()}/api/public/vertrag/{f['token']}", f["laeuft_ab"]
        neu = {"token": secrets.token_urlsafe(32), "erstellt_am": now_iso(),
               "laeuft_ab": (datetime.now(timezone.utc)
                             + timedelta(days=VERTRAG_LINK_TAGE)).isoformat(),
               "erstellt_von": user.get("id"), "version": version, "abrufe": 0}
        aenderung: dict = {"$set": {"freigabe": neu}}
        if f.get("token") and (f.get("laeuft_ab") or "") > now_iso():
            # Befund 49 (16.09.2026): abgelaufene Alt-Links vorher entfernen und
            # die Liste auf 50 statt 10 begrenzen — die 14-Tage-Zusage eines
            # noch laufenden Links fiel sonst nach zehn Neuerzeugungen weg.
            await db.generated_pdfs.update_one(
                {"id": contract_id, **bereich},
                {"$pull": {"freigabe_alt": {"laeuft_ab": {"$lt": now_iso()}}}})
            aenderung["$push"] = {"freigabe_alt": {"$each": [f], "$slice": -FREIGABE_ALT_MAX}}
        # Nur schreiben, wenn die Freigabe noch genau so aussieht wie gelesen —
        # sonst hat ein paralleler Aufruf bereits eine gesetzt.
        # Pruefung 14.09.2026 (Liste 5, Nr. 2): nur, wenn die Fassung noch dieselbe
        # ist — sonst zeigte ein frischer Link auf eine alte Version.
        res = await db.generated_pdfs.update_one(
            {"id": contract_id, **bereich, "version": c.get("version"),
             **({"freigabe.token": f["token"]} if f.get("token")
                else {"freigabe": {"$exists": False}})},
            aenderung)
        if res.modified_count:
            return f"{_oeffentliche_basis()}/api/public/vertrag/{neu['token']}", neu["laeuft_ab"]
    raise HTTPException(409, "Der Vertrag wurde gerade neu erstellt — bitte die Seite neu "
                             "laden und erneut senden.")


# Gegenpruefung 12.09.2026: Zuordnung und Einsetzen liegen in vertrag_felder
# (ohne Datenbank), damit das Abholprotokoll-PDF nicht den ganzen Routen-Stack
# braucht. Die Namen hier bleiben gueltig.
from vertrag_felder import KAEUFER_FELDER, _apply_contract_overrides  # noqa: E402,F401
from ai import damage_pricing as _ki_vertrag  # noqa: E402


# Runde 24 (11.09.2026, Befund Ahmad): Der Kaeufer (= Auftraggeber im
# Abholprotokoll) braucht einen Namen. Steht weder im Formular noch in den
# Einstellungen (Firma bzw. Sucher-Override) eine Firma, wird der Vertrag
# nicht angelegt. Nur der Name — Adresse/PLZ/Ort erzwingt das Formular, damit
# alte Clients ohne Adressfelder nicht brechen. Die Vorschau bleibt Entwurf.
KAEUFER_FEHLT = ("Käuferdaten fehlen: bitte Firma/Name im Kaufvertrag oder in "
                 "den Einstellungen eintragen.")


def kaeufer_pflicht_pruefen(dealer: Optional[dict]) -> None:
    """422, wenn das Haendler-Dokument NACH _apply_contract_overrides keinen
    Kaeufernamen (company_name) hat."""
    name = (dealer or {}).get("company_name")
    if not (str(name).strip() if name is not None else ""):
        raise HTTPException(422, KAEUFER_FEHLT)

def kaeufer_einfrieren(contract: dict, dealer: dict) -> dict:
    """Runde 25 (12.09.2026, Pruefbefund): Die TATSAECHLICH verwendeten
    Kaeuferdaten in den Vertrag schreiben — auch die, die nur aus den
    Einstellungen (Firma bzw. Sucher-Override) stammen.

    Ohne das griffen spaetere Fassungen (verschobener Termin) und das
    Abholprotokoll erneut auf die HEUTIGEN Einstellungen zu: Aendert der
    Sucher seine Firmendaten, stand im Protokoll ein anderer Auftraggeber
    als im Kaufvertrag. Idempotent; leere Werte werden nicht gesetzt.
    """
    for feld, ziel in KAEUFER_FELDER.items():
        wert = (dealer or {}).get(ziel)
        wert = str(wert).strip() if wert is not None else ""
        if wert:
            contract[feld] = wert
    return contract


# Gesperrt bleibt NUR, was schon immer gesperrt war (Runde 17, Nr. 270):
# verkauft, geloescht, archiviert — geprueft EINMAL beim Start der Anlage.
# Beschluss Ahmad (12.09.2026): Das System sperrt und loescht nichts von
# sich aus, und das Speichern soll klappen. Ein abgeholtes Fahrzeug bleibt
# vertragsfaehig; eine zweite Pruefung kurz vor dem Speichern gibt es
# bewusst NICHT (sie wuerde den Vertrag im letzten Moment verwerfen).
VERTRAG_GESPERRT = {"verkauft", "geloescht", "archiviert"}


def _vehicle_bild_urls(vehicle: dict) -> list:
    """Foto-URLs eines Fahrzeugs — ausgelesene Inserate speichern sie je
    nach Quelle unter `images` (Kleinanzeigen-Scraper) oder `image_urls`."""
    urls = vehicle.get("image_urls") or vehicle.get("images") or []
    return [u for u in urls if isinstance(u, str) and u.startswith("http")]


#: Rollenpruefung 22.09.2026 (RP-014/RP-113): Meldung, wenn ein Sucher einen
#: Vertrag zu einem Fahrzeug anlegen will, das nicht (mehr) in SEINER Liste
#: steht.
#: Rollenpruefung 22.09.2026 (RP-443): auch der Fall "war im Vergleich, ist
#: aber aus dem Fahrzeugpool aussortiert" (aeltere Vergleiche fallen heraus) —
#: dann hilft nur neu vergleichen, und das kostet nichts.
FAHRZEUG_NICHT_IM_BEREICH = ("Fahrzeug nicht gefunden — bitte das Inserat zuerst "
                             "vergleichen, dann den Kaufvertrag anlegen. War es schon "
                             "verglichen, wurde es inzwischen aus deinem Fahrzeugpool "
                             "aussortiert (ältere Vergleiche fallen heraus) — bitte den "
                             "Link neu vergleichen, das kostet nichts.")


async def _fahrzeug_fuer_vertrag(user: dict, vehicle_id: str) -> Optional[dict]:
    """Das Fahrzeug, zu dem dieses Konto einen Kaufvertrag anlegen darf.

    Rollenpruefung 22.09.2026 (RP-014/RP-113): Vorschau und Anlage suchten
    nur nach der Firma. Fahrzeug-IDs sind aus der Anzeigennummer ableitbar
    (v_<Anzeigennummer>) — ein Sucher konnte so am Vergleich vorbei (Abruf,
    Tageslimit) und an einem selbst aus der Liste entfernten Fahrzeug einen
    Vertrag anlegen und wurde dabei Mitbearbeiter. Jetzt gilt dieselbe Regel
    wie beim Termin (appointments._fahrzeug_fuer_termin_erlaubt): Chef =
    Firma, Sucher = eigene bzw. mitbearbeitete Fahrzeuge. Der normale Weg
    aendert sich nicht — der Vergleich traegt den Sucher bereits ein."""
    return await db.vehicles.find_one(
        {"id": vehicle_id, **fahrzeug_bereich(user)}, {"_id": 0})


# Rollenpruefung 22.09.2026 (RP-452): Das Firmenlogo stand nie im Vertrag,
# obwohl die Einstellungen es zusagen ("Es erscheint auf deinen Verträgen").
# Hochgeladene Logos liegen oeffentlich unter /api/files/logo/<firma>/….
_LOGO_PFAD = "/api/files/"


def logo_schluessel(firma: Optional[dict]) -> str:
    """Speicher-Schluessel des hochgeladenen Firmenlogos ('' ohne Logo).
    Nur eigene Uploads (logo/…) — nie eine fremde Adresse."""
    url = str((firma or {}).get("logo_url") or "").strip()
    if not url.startswith(_LOGO_PFAD):
        return ""
    key = url[len(_LOGO_PFAD):].split("?", 1)[0]
    return key if key.startswith("logo/") and ".." not in key else ""


async def _logo_bytes(key: Optional[str]) -> Optional[bytes]:
    """Logo-Datei laden — fehlt sie oder ist der Speicher weg, gibt es den
    Vertrag eben ohne Logo (nie ein Fehler deswegen)."""
    if not key:
        return None
    try:
        from storage_service import load_async
        daten = await load_async(key)
        return daten if daten and len(daten) <= 3 * 1024 * 1024 else None
    except Exception as exc:  # noqa: BLE001
        log.info("Logo %s fuer den Vertrag nicht ladbar: %s", key, exc)
        return None


async def _logo_einsetzen(dealer: dict, contract_dict: dict) -> dict:
    """Das im Vertrag festgehaltene Logo (contract_data.logo_key) als Bytes
    fuer pdf_service in das dealer-Dict legen. Altvertraege ohne Merker
    bleiben ohne Logo — eine neue Fassung sieht nie anders aus als die alte,
    nur weil heute ein Logo hinterlegt ist (Beschluss 09.09.2026: Einstellungen
    veraendern bestehende Vertraege nie)."""
    daten = await _logo_bytes(contract_dict.get("logo_key"))
    if daten:
        dealer = dict(dealer or {})
        dealer["_logo_bytes"] = daten
    return dealer


# ---------- Endpoints ----------
class KiSchadenIn(BaseModel):
    """Stufe 3 (26.09.2026): die Schaeden aus der Skizze des Vertragsdialogs —
    EIN Aufruf fuer alle, erst nach "Ja, Schaeden bewerten"."""
    vehicle_id: str = Field(max_length=200)
    damages: List[DamageIn] = Field(default_factory=list, max_length=60)
    # schon verhandelter Kaufpreis (sonst gilt der Inseratspreis als Basis)
    purchase_price: Optional[float] = Field(default=None, ge=0, le=10_000_000)

    @field_validator("purchase_price", mode="before")
    @classmethod
    def _preis_endlich(cls, v):
        if isinstance(v, float) and v != v:
            raise ValueError("ungültiger Preis")
        return v


@router.get("/contracts/vorschlaege/{vehicle_id}")
async def vertrag_vorschlaege(vehicle_id: str, user=Depends(current_firma)):
    """Stufe 3 (Wunsch Ahmad 25.09.2026): eindeutige Vertragsangaben aus dem
    Inserat — regelbasiert, ohne KI (Schluessel, HU, Scheckheft nur bei
    "lueckenlos"/"kein", Unfallfreiheit, fahrbereit, EU-Import, Bereifung).
    Der Dialog fuellt damit NUR leere Felder und zeigt die Fundstelle."""
    v = await _fahrzeug_fuer_vertrag(user, vehicle_id)
    if not v:
        raise HTTPException(404, FAHRZEUG_NICHT_IM_BEREICH)
    from ai import inserat_regeln
    return inserat_regeln.vorschlaege(v.get("data") or {})


@router.post("/contracts/ki-schadennachlass")
async def vertrag_ki_schadennachlass(body: KiSchadenIn, user=Depends(require_active_sub)):
    """Stufe 3 (Wunsch Ahmad 25.09.2026): KI-Schadennachlass fuer die im
    Vertragsdialog markierten Schaeden — synchron, ein Aufruf, rein beratend.
    Antwort: status ok | keine | aus | limit | fehler | zeitlimit ... mit
    ergebnis (items/combined/needs_information/arguments) und der id fuer
    den Lernfall (ContractIn.ki_bewertung_id)."""
    v = await _fahrzeug_fuer_vertrag(user, body.vehicle_id)
    if not v:
        raise HTTPException(404, FAHRZEUG_NICHT_IM_BEREICH)
    return await _ki_vertrag.bewerten(user=user, vehicle_doc=v,
                                      damages=[d.model_dump() for d in body.damages],
                                      kaufpreis=body.purchase_price)


@router.post("/contracts/preview")
async def preview_contract(body: ContractIn, user=Depends(require_active_sub),
                           variante: str = "druck"):
    """Generate a draft Kaufvertrag PDF without persisting anything.
    Returns the PDF inline so the dealer can review it before final save.
    ?variante=digital liefert die Ausfertigung ohne Unterschriftslinien."""
    # Umbau Kaufvorgaenge 09.09.2026: das Inserat ist firmenweit gemeinsam —
    # JEDER Sucher der Firma darf dafuer einen eigenen Vertrag anlegen.
    # Rollenpruefung 22.09.2026 (RP-014/RP-113): ... aber nur, wenn er es
    # verglichen hat (Fahrzeug in seiner Liste) — siehe _fahrzeug_fuer_vertrag.
    v = await _fahrzeug_fuer_vertrag(user, body.vehicle_id)
    if not v:
        raise HTTPException(404, FAHRZEUG_NICHT_IM_BEREICH)
    from deps import effective_dealer
    dealer = await effective_dealer(user) or {}
    vehicle = v["data"]
    contract_dict = body.model_dump(exclude={"zweiter_vertrag_bestaetigt", "ki_bewertung_id"})
    if not (contract_dict.get("additional_terms") or "").strip():
        # Wunsch Ahmad 20.09.2026: unser Standardsatz (falls eingeschaltet)
        # UND der eigene Text der Firma — nicht mehr nur das Freitextfeld.
        import vertrag_vorlagen as _vorlagen
        contract_dict["additional_terms"] = _vorlagen.sondervereinbarungen(dealer)
    # AGB: only fall back to dealer default if no override was provided.
    if not (contract_dict.get("agb_text") or "").strip():
        contract_dict["agb_text"] = dealer.get("default_terms", "") or ""
    # Wunsch Ahmad 18.09.2026: Ein im Dialog ueberarbeiteter Text gilt fuer
    # diesen Vertrag; leer = Text aus den Einstellungen (bzw. Standard).
    eigener_text = (contract_dict.get("digital_vertragstext") or "").strip()
    contract_dict["digital_vertragstext"] = eigener_text or digitaler_vertragstext(dealer)
    # Vehicle description: pre-fill from the scraped listing if the user
    # didn't paste/override anything. Lets the description appear in the
    # PDF without an extra step.
    # Rollenpruefung 22.09.2026 (RP-404): nur, wenn das Feld FEHLT — eine im
    # Dialog bewusst geleerte Beschreibung ("") bleibt leer.
    if contract_dict.get("vehicle_description") is None:
        contract_dict["vehicle_description"] = vehicle.get("description", "") or ""
    vehicle, dealer = _apply_contract_overrides(
        contract=contract_dict, vehicle=vehicle, dealer=dealer,
    )
    # Rollenpruefung 22.09.2026 (RP-452): die Vorschau zeigt das Logo, das
    # beim Erstellen festgehalten wuerde.
    contract_dict["logo_key"] = logo_schluessel(dealer)
    dealer = await _logo_einsetzen(dealer, contract_dict)
    # ReportLab ist CPU-gebunden -> in Thread auslagern, damit der
    # Event-Loop unter Last (200-500 Nutzer) nicht blockiert.
    # try/except: ein Layout-Fehler (z.B. pathologische Eingabe) wird zu
    # einem sauberen 400 statt einem unhandled 500.
    try:
        pdf_bytes = await asyncio.to_thread(
            generate_contract_pdf,
            dealer=dealer, vehicle=vehicle, contract=contract_dict,
            digital=(variante == "digital"),
        )
    except Exception:
        raise HTTPException(400, "PDF konnte mit diesen Eingaben nicht erzeugt werden.")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline; filename=\"Kaufvertrag_Vorschau.pdf\"",
            "Cache-Control": "no-store",
        },
    )


def _anfrage_hash(body) -> str:
    """Pruefung 14.09.2026 (F16): Fingerabdruck der Vertragsanfrage ohne den
    Schluessel selbst — bindet einen Idempotenz-Schluessel an seinen Inhalt."""
    import hashlib
    import json
    # Rollenpruefung 22.09.2026 (RP-416): die Rueckfrage "zweiter Vertrag?"
    # ist Steuerung, kein Vertragsinhalt — dieselbe Anfrage mit und ohne
    # Bestaetigung ist derselbe Vertrag.
    daten = body.model_dump(exclude={"idempotency_key", "zweiter_vertrag_bestaetigt", "ki_bewertung_id"})
    roh = json.dumps(daten, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:32]


def _vertrag_dateiname(vehicle: dict, datum: Optional[datetime] = None) -> str:
    """Dateiname eines Kaufvertrags aus Marke und Modell (gesaeubert)."""
    tag = (datum or datetime.now()).strftime("%Y%m%d")
    return sicherer_dateiname(
        f"Kaufvertrag_{(vehicle or {}).get('make_label', '')}_"
        f"{(vehicle or {}).get('model_label', '')}_{tag}.pdf",
        fallback=f"Kaufvertrag_{tag}.pdf")


async def _offener_eigener_vertrag(user: dict, vehicle_id: str) -> Optional[dict]:
    """Rollenpruefung 22.09.2026 (RP-416): der noch offene Kaufvertrag DIESES
    Kontos zu diesem Fahrzeug (Vorgang vertrag_erstellt / gesendet /
    abholung_geplant) — oder None.

    Vorher legte ein zweiter Vertrag (z. B. nachverhandelter Preis) still
    einen zweiten Kaufvorgang samt zweitem Abholtermin an; der erste lief mit
    dem alten Preis weiter. Ob der alte Vertrag dabei automatisch storniert
    werden soll, ist Ahmads Entscheidung — hier wird nur nachgefragt."""
    from kaufvorgang import OFFEN as _kv_offen
    async for kv in db.kaufvorgaenge.find(
            {"dealer_id": user["dealer_id"], "user_id": user["id"],
             "vehicle_id": vehicle_id, "status": {"$in": list(_kv_offen)}},
            {"_id": 0, "contract_id": 1}).sort("created_at", -1).limit(5):
        if not kv.get("contract_id"):
            continue
        vertrag = await db.generated_pdfs.find_one(
            {"id": kv["contract_id"], "dealer_id": user["dealer_id"],
             "loeschung.status": {"$ne": "laeuft"}},
            {"_id": 0, "id": 1, "contract_no": 1})
        if vertrag is not None:
            return vertrag
    return None


def _idempotenz_konflikt(vorhanden: dict) -> dict:
    """Pruefbericht 20.09.2026 (U-83): derselbe Idempotenz-Schluessel, anderer
    Inhalt — vorher nur "bitte neu laden" ohne Vertrags-Id und Preis. Der
    Entwurf im sessionStorage traegt den alten Schluessel weiter, deshalb kam
    derselbe 409 nach dem Neuladen wieder. Jetzt bekommt die Oberflaeche, was
    sie fuer die Rueckfrage braucht (vorhandenen Vertrag oeffnen oder mit
    neuem Schluessel neu anlegen)."""
    preis = vorhanden.get("purchase_price")
    return {
        "code": "idempotenz_konflikt",
        "msg": "Dieser Idempotenz-Schlüssel wurde schon für einen anderen Vertrag "
               "verwendet — bitte neu laden",
        "contract_id": vorhanden.get("id"),
        "purchase_price": float(preis) if isinstance(preis, (int, float)) else None,
    }


@router.post("/contracts")
async def create_contract(body: ContractIn, user=Depends(require_active_sub)):
    # Pruefung 14.09.2026 (Liste 3, Nr. 1): derselbe Schluessel -> derselbe Vertrag.
    anfrage_hash = _anfrage_hash(body)
    if body.idempotency_key:
        vorhanden = await db.generated_pdfs.find_one(
            {"dealer_id": user["dealer_id"], "user_id": user["id"],
             "idempotency_key": body.idempotency_key},
            {"_id": 0, "pdf_b64": 0, "pdf_digital_b64": 0})
        if vorhanden:
            # Pruefung 14.09.2026 (F16): derselbe Schluessel mit ANDEREN
            # Vertragsdaten ist ein Fehler, nicht "der alte Vertrag".
            if vorhanden.get("idempotency_hash") and vorhanden["idempotency_hash"] != anfrage_hash:
                raise HTTPException(409, _idempotenz_konflikt(vorhanden))
            return {**clean_doc(vorhanden), "bereits_vorhanden": True}
    # Umbau Kaufvorgaenge 09.09.2026: Inserat firmenweit gemeinsam — jeder
    # Sucher darf einen eigenen Vertrag (= eigenen Kaufvorgang) anlegen.
    # Rollenpruefung 22.09.2026 (RP-014/RP-113): Sucher nur fuer Fahrzeuge in
    # ihrer Liste (verglichen / Mitbearbeiter) — _fahrzeug_fuer_vertrag.
    v = await _fahrzeug_fuer_vertrag(user, body.vehicle_id)
    if not v:
        raise HTTPException(404, FAHRZEUG_NICHT_IM_BEREICH)
    # Runde 17 (Nr. 270): Kein neuer Kaufvertrag fuer ein Fahrzeug, das
    # bereits verkauft, geloescht oder archiviert ist — vorher entstand ein
    # Vertrag samt Auto-Datensatz, der Lebenszyklus blieb stumm stehen.
    # ("geloescht" faengt fahrzeug_bereich schon als 404 ab; die Pruefung
    # bleibt als zweite Sicherung, falls sich der Bereich einmal aendert.)
    if (v.get("lifecycle") or "") in VERTRAG_GESPERRT:
        raise HTTPException(409, "Fahrzeug ist bereits verkauft/gelöscht/archiviert "
                                 "— kein neuer Kaufvertrag möglich")
    # Rollenpruefung 22.09.2026 (RP-416): zweiter Vertrag DESSELBEN Kontos zum
    # selben Fahrzeug nur nach Rueckfrage (Doppel-Abholung durch ZWEI Sucher
    # bleibt erlaubt, Beschluss 13.09.2026).
    if not body.zweiter_vertrag_bestaetigt:
        vorheriger = await _offener_eigener_vertrag(user, body.vehicle_id)
        if vorheriger:
            nr = vorheriger.get("contract_no") or ""
            raise HTTPException(409, {
                "code": "vertrag_vorhanden",
                "contract_id": vorheriger.get("id"),
                "contract_no": nr,
                "msg": (f"Du hast für dieses Fahrzeug schon einen offenen Kaufvertrag"
                        f"{f' (Nr. {nr})' if nr else ''}. Ein zweiter Vertrag ergibt einen "
                        f"zweiten Kauf mit eigenem Abholtermin — der erste läuft mit "
                        f"seinem Preis weiter, bis du ihn im Vertragsarchiv löschst."),
            })
    from deps import effective_dealer
    dealer = await effective_dealer(user) or {}
    vehicle = v["data"]
    # Wunsch Ahmad 19.09.2026: Der Inserats-Zwischenspeicher lebt nur noch
    # 14 Tage — wer einen Vertrag zu dem Wagen macht, "behaelt es bei sich":
    # der ROHE Inseratsstand aus dem Zwischenspeicher wird am Vertrag
    # eingefroren (nicht die vom Haendler bearbeitbaren Fahrzeugdaten). Das
    # Beweisdokument laesst sich damit auch spaeter noch anfordern, ohne dass
    # die Verkaeuferdaten fuer alle anderen im gemeinsamen Speicher bleiben.
    inserat_stand = None
    try:
        import beweis_service as _bs
        _schluessel = _bs.inserat_schluessel(v)
        if _schluessel:
            _eintrag = await db.listings_cache.find_one(
                {"cache_key": _schluessel},
                {"_id": 0, "data": 1, "fetched_at": 1, "source": 1, "item_id": 1, "url": 1})
            if _eintrag and _eintrag.get("data"):
                inserat_stand = {"cache_key": _schluessel, "data": _eintrag["data"],
                                 "fetched_at": _eintrag.get("fetched_at"),
                                 "source": _eintrag.get("source"),
                                 "item_id": _eintrag.get("item_id"),
                                 "url": _eintrag.get("url")}
    except Exception as exc:  # noqa: BLE001 — der Vertrag darf daran nie scheitern
        log.warning("Vertrag %s: Inseratsstand nicht eingefroren: %s", body.vehicle_id, exc)
    # Apply dealer defaults if the form didn't override them. Both
    # special_agreements and agb_text now support a per-contract override
    # (otherwise we still fall back to the dealer's saved defaults).
    contract_dict = body.model_dump(exclude={"zweiter_vertrag_bestaetigt", "ki_bewertung_id"})
    if not (contract_dict.get("additional_terms") or "").strip():
        # Wunsch Ahmad 20.09.2026: unser Standardsatz (falls eingeschaltet)
        # UND der eigene Text der Firma — nicht mehr nur das Freitextfeld.
        import vertrag_vorlagen as _vorlagen
        contract_dict["additional_terms"] = _vorlagen.sondervereinbarungen(dealer)
    if not (contract_dict.get("agb_text") or "").strip():
        contract_dict["agb_text"] = dealer.get("default_terms", "") or ""
    # Rollenpruefung 22.09.2026 (RP-404): nur ein FEHLENDES Feld wird aus dem
    # Inserat gefuellt — "" heisst "bewusst geleert".
    if contract_dict.get("vehicle_description") is None:
        contract_dict["vehicle_description"] = vehicle.get("description", "") or ""
    # Text der digitalen Ausfertigung zum Zeitpunkt der Erstellung
    # festhalten (Beweis: so wurde der Vertrag verschickt). Ein im Dialog
    # ueberarbeiteter Text gilt fuer diesen Vertrag (Wunsch Ahmad 18.09.2026).
    eigener_text = (contract_dict.get("digital_vertragstext") or "").strip()
    contract_dict["digital_vertragstext"] = eigener_text or digitaler_vertragstext(dealer)
    vehicle, dealer = _apply_contract_overrides(
        contract=contract_dict, vehicle=vehicle, dealer=dealer,
    )
    # Runde 24: ohne Kaeufername kein Vertrag (vor PDF und Speichern).
    kaeufer_pflicht_pruefen(dealer)
    # Runde 25: Kaeuferdaten einfrieren, damit spaetere Fassungen und das
    # Abholprotokoll genau diesen Stand zeigen (Pruefbefund 12.09.2026).
    kaeufer_einfrieren(contract_dict, dealer)
    # Rollenpruefung 22.09.2026 (RP-452): das Firmenlogo gehoert zum Kaeufer —
    # der Schluessel wird wie die Kaeuferdaten festgehalten; spaetere Fassungen
    # nehmen genau dieses Logo (oder keins), nie das heutige.
    contract_dict["logo_key"] = logo_schluessel(dealer)
    dealer = await _logo_einsetzen(dealer, contract_dict)
    # Vertragsnummer VOR der PDF-Erzeugung festlegen, damit sie im Dokument
    # (Kopf + Fußzeile) erscheint und im Archiv wiederauffindbar ist.
    pdf_id = str(uuid.uuid4())
    contract_no = f"KV-{datetime.now().strftime('%Y%m%d')}-{pdf_id[:6].upper()}"
    contract_dict["contract_no"] = contract_no
    # Rollenpruefung 22.09.2026 (RP-494): die erste Fassung — jede neue Fassung
    # (verschobener Termin, Abholung) traegt ihre Nummer im PDF-Kopf.
    contract_dict["fassung"] = 1
    # ReportLab ist CPU-gebunden -> in Thread auslagern, damit der
    # Event-Loop unter Last (200-500 Nutzer) nicht blockiert.
    # try/except: ein Layout-Fehler (z.B. pathologische Eingabe) wird zu
    # einem sauberen 400 statt einem unhandled 500.
    try:
        pdf_bytes, pdf_digital = await asyncio.to_thread(
            _pdfs_erzeugen,
            dealer=dealer, vehicle=vehicle, contract=contract_dict,
        )
    except Exception:
        raise HTTPException(400, "PDF konnte mit diesen Eingaben nicht erzeugt werden.")
    pdf_b64 = base64.b64encode(pdf_bytes).decode()
    pdf_digital_b64 = base64.b64encode(pdf_digital).decode()
    # Snapshot vehicle photo URLs at the moment the contract was created.
    # This way the dealer can still see the listing photos retrospectively
    # next to the contract PDF + Beweis-Archiv even if the original ad
    # is deleted by the seller.
    vehicle_image_urls = _vehicle_bild_urls(vehicle)
    doc = {
        "id": pdf_id, "contract_no": contract_no,
        "dealer_id": user["dealer_id"], "user_id": user["id"],
        "vehicle_id": body.vehicle_id, "mobile_ad_id": v.get("mobile_ad_id"),
        "make": vehicle.get("make_label") or vehicle.get("make"),
        "model": vehicle.get("model_description") or vehicle.get("model_label"),
        "seller_name": body.seller_name,
        "seller_phone": body.seller_phone,
        "seller_email": body.seller_email,
        "pickup_date": body.pickup_date,
        "pickup_time": body.pickup_time,
        "purchase_price": body.purchase_price,
        "contract_data": contract_dict,
        "pdf_b64": pdf_b64,
        # Digitale Ausfertigung (ohne Unterschriftslinien) — wird per
        # E-Mail angehaengt bzw. fuer WhatsApp heruntergeladen.
        "pdf_digital_b64": pdf_digital_b64,
        "vehicle_image_urls": vehicle_image_urls,
        "inserat_stand": inserat_stand,
        # Stufe 3 (26.09.2026): welche KI-Schadenbewertung der Sucher vorher sah
        "ki_bewertung_id": (body.ki_bewertung_id or "").strip() or None,
        # Rollenpruefung 22.09.2026 (RP-200/RP-351): schon beim Anlegen ohne
        # Steuerzeichen/Anfuehrungszeichen; die Kopfzeile baut
        # content_disposition (ASCII + UTF-8), 'Š' & Co. geben kein 500 mehr.
        "filename": _vertrag_dateiname(vehicle),
        "send_status": [],
        "status": "erstellt",
        "appointment_id": None,
        "kaufvorgang_id": str(uuid.uuid4()),   # Umbau 09.09.2026: ein Vorgang je Vertrag
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    if body.idempotency_key:
        doc["idempotency_key"] = body.idempotency_key
        doc["idempotency_hash"] = anfrage_hash
    # Dauerhafte, anonyme Auto-Daten (siehe auto_daten.py): ZUERST der
    # Datensatz, dann der Vertrag mit dessen zufaelliger id. Scheitert der
    # Vertrags-Insert, wird der Datensatz sofort wieder entfernt — es gibt
    # nie einen Vertrag ohne Auto-Daten und keinen Datensatz ohne Vertrag.

    # Pruefung 14.09.2026 (Liste 6, Nr. 1): zwischen Vorpruefung und Speichern
    # kann das Fahrzeug verkauft/archiviert/geloescht worden sein.
    # 15.09.2026: die Projektion liefert {} fuer ein Fahrzeug OHNE lifecycle-Feld
    # — "not v_jetzt" hielt das faelschlich fuer "weg" (409). Nur None ist weg.
    v_jetzt = await db.vehicles.find_one(
        {"id": body.vehicle_id, "dealer_id": user["dealer_id"]}, {"_id": 0, "lifecycle": 1})
    if v_jetzt is None or (v_jetzt.get("lifecycle") or "") in VERTRAG_GESPERRT:
        raise HTTPException(409, "Fahrzeug ist inzwischen verkauft/gelöscht/archiviert "
                                 "— kein neuer Kaufvertrag möglich")
    # Wunsch Ahmad 15.09.2026: ein neuer Vertrag zu demselben Fahrzeug (neuer
    # Preis, Nachverhandlung) fuehrt den vorhandenen Auto-Datensatz nach — in
    # den Auto-Daten steht das Auto einmal, mit dem aktuellen Preis und Kaufdatum.
    # Runde 19 (Nr. 37/38): Fahrzeug kurz vor dem Pool-Trimmen schuetzen und
    # eine kurze Sperre je Firma+Fahrzeug, damit zwei ERSTE Vertraege desselben
    # Autos nicht zwei Datensaetze anlegen.
    from fahrzeugpool import kurz_schuetzen
    await kurz_schuetzen(db, user["dealer_id"], body.vehicle_id)
    # Befund 113 (16.09.2026): ohne Sperre geht es NICHT weiter — der Sucher
    # bekommt ein kurzes "gleich erneut" statt eines moeglichen Doppel-Datensatzes.
    try:
        sperre = await auto_daten.vertrag_sperre(db, user["dealer_id"], body.vehicle_id)
    except auto_daten.SperreBelegt:
        # Pruefbericht 20.09.2026 (P1): Legen zwei Sucher fast gleichzeitig
        # einen Vertrag zum SELBEN Fahrzeug an, wartet der zweite 6 s und
        # bekam dann diesen Fehler zu sehen — er musste von Hand noch einmal
        # speichern. An dieser Stelle steht fest, dass NICHTS geschrieben
        # wurde (die Sperre kam nie zustande), ein zweiter Versuch ist also
        # gefahrlos. `X-Wiederholen` sagt das der Oberflaeche ausdruecklich;
        # sie wiederholt nur bei DIESER Kopfzeile, nie bei einem beliebigen
        # 503 (das koennte einen zweiten Vertrag anlegen).
        raise HTTPException(503, "Für dieses Fahrzeug wird gerade ein Kaufvertrag angelegt — "
                                 "bitte in ein paar Sekunden erneut versuchen.",
                            headers={"Retry-After": "3", "X-Wiederholen": "1"})
    auto_daten_neu = False
    auto_daten_id = None
    # Befund 134 (16.09.2026): die Sperre wird in JEDEM Fall wieder freigegeben
    # (auch wenn bestehenden_datensatz/anlegen selbst scheitern) — vorher hielt
    # ein DB-Fehler davor die Sperre bis zum Ablauf.
    try:
        auto_daten_id = await auto_daten.bestehenden_datensatz(
            db, user["dealer_id"], body.vehicle_id)
        auto_daten_neu = auto_daten_id is None
        if auto_daten_neu:
            # Rollenpruefung 22.09.2026 (RP-401): Kaufdatum ausdruecklich =
            # Erstellung des Vertrags (anlegen nimmt sonst "jetzt" — ueber
            # Mitternacht haette der Datensatz einen anderen Tag als der Vertrag).
            auto_daten_id = await auto_daten.anlegen(db, contract_dict, vehicle,
                                                     gekauft_am=doc["created_at"])
        doc["admin_vehicle_data_id"] = auto_daten_id
        try:
            await db.generated_pdfs.insert_one(doc)
            # Stufe 3/4 (26.09.2026): KI-Empfehlung gegen den tatsaechlichen
            # Vertragspreis festhalten (anonym, wirft nie).
            await _ki_vertrag.lernfall_speichern(doc, doc.get("ki_bewertung_id"))
        except DuplicateKeyError:
            # Pruefung 14.09.2026 (Liste 3, Nr. 1): paralleler Doppelklick — der
            # andere Aufruf hat den Vertrag mit demselben Schluessel angelegt.
            if auto_daten_neu:
                await auto_daten.zurueckrollen(db, auto_daten_id)
            vorhanden = await db.generated_pdfs.find_one(
                {"dealer_id": user["dealer_id"], "user_id": user["id"],
                 "idempotency_key": body.idempotency_key},
                {"_id": 0, "pdf_b64": 0, "pdf_digital_b64": 0})
            if vorhanden:
                # Runde 16 (15.09.2026): dieselbe Hash-Pruefung wie im Vorabpfad —
                # sonst bekam Anfrage B still den Vertrag von Anfrage A.
                if vorhanden.get("idempotency_hash") and vorhanden["idempotency_hash"] != anfrage_hash:
                    raise HTTPException(409, _idempotenz_konflikt(vorhanden))
                return {**clean_doc(vorhanden), "bereits_vorhanden": True}
            raise
        except Exception:
            if auto_daten_neu:
                await auto_daten.zurueckrollen(db, auto_daten_id)
            raise
        # Befund 127 (16.09.2026): Nachkontrolle des Lebenszyklus NACH dem
        # Insert — hat der Chef das Fahrzeug genau dazwischen verkauft,
        # archiviert oder geloescht, wird der eben angelegte Vertrag wieder
        # entfernt (Fahrzeug und Vertrag liegen nicht in einer Transaktion).
        v_nach = await db.vehicles.find_one(
            {"id": body.vehicle_id, "dealer_id": user["dealer_id"]}, {"_id": 0, "lifecycle": 1})
        if v_nach is None or (v_nach.get("lifecycle") or "") in VERTRAG_GESPERRT:
            await db.generated_pdfs.delete_one({"id": pdf_id})
            if auto_daten_neu:
                await auto_daten.zurueckrollen(db, auto_daten_id)
            raise HTTPException(409, "Fahrzeug ist inzwischen verkauft/gelöscht/archiviert "
                                     "— kein neuer Kaufvertrag möglich")
        if not auto_daten_neu:
            # Runde 19 (Nr. 36/40): den bestehenden Datensatz erst NACH dem
            # dauerhaften Vertrag nachfuehren (ein gescheiterter Vertrag aendert
            # nichts); ist er inzwischen vom Betreiber geloescht, bekommt der
            # Vertrag einen frischen — kein Verweis ins Leere.
            try:
                if not await auto_daten.aktualisieren(db, auto_daten_id, contract_dict, vehicle,
                                                      gekauft_am=doc["created_at"]):
                    # RP-401: auch der Ersatz-Datensatz traegt das Vertragsdatum.
                    neu_id = await auto_daten.anlegen(db, contract_dict, vehicle,
                                                      gekauft_am=doc["created_at"])
                    r_neu = await db.generated_pdfs.update_one(
                        {"id": pdf_id, "admin_vehicle_data_id": auto_daten_id},
                        {"$set": {"admin_vehicle_data_id": neu_id}})
                    # Befund 115 (16.09.2026): traf der Wechsel nicht (Verweis
                    # inzwischen anders), bliebe der neue Datensatz verwaist.
                    if r_neu.matched_count:
                        auto_daten_id = neu_id
                    else:
                        await auto_daten.zurueckrollen(db, neu_id)
            except Exception:  # noqa: BLE001
                log.exception("Auto-Daten zu Vertrag %s nicht nachgefuehrt — Merker fuer den Aufraeumjob", pdf_id)
                try:
                    await db.generated_pdfs.update_one({"id": pdf_id},
                                                       {"$set": {"auto_daten_nachfuehrung_offen": True}})
                except Exception:  # noqa: BLE001
                    pass
    finally:
        await auto_daten.sperre_freigeben(db, sperre)
    # Runde 17 (Nr. 265): Ab hier ist der Vertrag dauerhaft. Scheitert das
    # Nachziehen von Fahrzeugstatus/Lebenszyklus (DB-Aussetzer), endete der
    # Request bisher mit 500 — der Client wiederholte und legte einen
    # ZWEITEN Vertrag an. Jetzt wie beim Termin-Block: Fehler ins Log, die
    # Antwort traegt einen Nacharbeit-Hinweis, der Vertrag bleibt gueltig.
    nacharbeit_hinweis = None
    try:
        # Umbau Kaufvorgaenge 09.09.2026: der Kaufpreis gehoert zum VORGANG,
        # nicht zum gemeinsamen Fahrzeug (zwei Sucher ueberschrieben sich
        # sonst gegenseitig). vehicles.purchase_price wird erst beim Abholen
        # aus dem erfolgreichen Vorgang uebernommen (kaufvorgang.py).
        await db.vehicles.update_one(
            {"id": body.vehicle_id, "dealer_id": user["dealer_id"],
             # Pruefbericht 20.09.2026 (R2-10): nicht auf ein inzwischen
             # verkauftes/geloeschtes/archiviertes Fahrzeug schreiben — die
             # Pruefungen oben lassen ein kleines Rennfenster.
             "lifecycle": {"$nin": list(VERTRAG_GESPERRT)}},
            {"$set": {"status": "Vertrag erstellt"}},
        )
        if user.get("role") == "sucher":
            # Wer einen Vertrag anlegt, arbeitet am Fahrzeug mit (Sichtbarkeit).
            await db.vehicles.update_one(
                {"id": body.vehicle_id, "dealer_id": user["dealer_id"],
                 "owner_user_id": {"$ne": user["id"]}},
                {"$addToSet": {"mitbearbeiter_ids": user["id"]}})
        import kaufvorgang as _kv
        await _kv.anlegen(dealer_id=user["dealer_id"], user_id=user["id"],
                          vehicle_id=body.vehicle_id, contract_id=pdf_id,
                          purchase_price=body.purchase_price,
                          kaufvorgang_id=doc["kaufvorgang_id"])
        # Fahrzeugstatus = Zusammenfassung aller Vorgaenge (vertrag_erstellt -> gekauft)
        await _kv.fahrzeug_status_aggregieren(body.vehicle_id, user["dealer_id"], user=user)
    except Exception:
        log.exception("Fahrzeugstatus nach Vertrag %s konnte nicht aktualisiert werden", pdf_id)
        nacharbeit_hinweis = ("Vertrag gespeichert; Fahrzeugstatus/Protokoll konnten "
                              "nicht aktualisiert werden.")
        # Pruefung 14.09.2026 (Liste 6, Nr. 2): Merker am Vertrag — der
        # Aufraeum-Job holt Kaufvorgang/Fahrzeugstatus nach
        # (cleanup_service.vertrags_nacharbeit_nachholen).
        try:
            await db.generated_pdfs.update_one({"id": pdf_id}, {"$set": {"nacharbeit_offen": True}})
        except Exception:  # noqa: BLE001
            log.exception("Merker nacharbeit_offen fuer Vertrag %s nicht gesetzt", pdf_id)
    # Audit wirft nie (log_activity_sicher) — der Vertrag steht bereits.
    if not await log_activity_sicher(user["dealer_id"], user["id"], "pdf.erstellt", ref=pdf_id):
        nacharbeit_hinweis = nacharbeit_hinweis or (
            "Vertrag gespeichert; Fahrzeugstatus/Protokoll konnten nicht aktualisiert werden.")

    # Abholtermin automatisch anlegen, wenn ein Abholdatum angegeben wurde,
    # damit der Vertrag im Terminplaner erscheint.
    # Runde 15 (Nr. 5): Der Vertrag ist gespeichert — scheitert der Termin,
    # bleibt der Vertrag gueltig und die Antwort traegt einen Hinweis; vorher
    # brach der Request mit 500 ab, und ein Wiederholen legte einen ZWEITEN
    # Vertrag an.
    termin_hinweis = None
    if body.pickup_date:
        try:
            appt_id, termin_hinweis = await _abholtermin_fuer_vertrag(
                user, body, vehicle, pdf_id, kaufvorgang_id=doc["kaufvorgang_id"])
        except Exception:
            log.exception("Auto-Termin fuer Vertrag %s fehlgeschlagen", pdf_id)
            appt_id = None
            termin_hinweis = ("Der Vertrag ist gespeichert, der Abholtermin konnte "
                              "aber nicht angelegt werden — bitte im Terminplaner "
                              "von Hand anlegen.")
        if appt_id:
            doc["appointment_id"] = appt_id
            doc["status"] = "Termin erstellt"

    # Pruefung 14.09.2026 (Liste 3, Nr. 3): kein Base64-PDF in der Antwort —
    # es liegt gespeichert und kommt per GET /contracts/{id}/pdf.
    out = clean_doc(doc)
    out.pop("pdf_b64", None)
    out.pop("pdf_digital_b64", None)
    if termin_hinweis:
        out["termin_hinweis"] = termin_hinweis
    if nacharbeit_hinweis:
        out["nacharbeit_hinweis"] = nacharbeit_hinweis
    return out


def _zusage_zuruecksetzen_fallback(existing: dict, neu: dict) -> tuple[dict, dict]:
    """Runde 17 (Nr. 355): Ersatz, falls routes.appointments den Helfer
    `zusage_zuruecksetzen_wenn_geaendert` (noch) nicht anbietet — dieselbe
    Regel wie in update_appointment: hat der Fahrer die Fahrt angenommen und
    aendern sich Datum, Uhrzeit oder Abholadresse, gilt die Zusage nicht
    mehr. Liefert ($set-Felder, $unset-Felder)."""
    if existing.get("zuteilung") != "angenommen":
        return {}, {}
    geaendert = any(
        f in neu and (neu.get(f) or "") != (existing.get(f) or "")
        for f in ("pickup_date", "pickup_time", "pickup_address"))
    if not geaendert:
        return {}, {}
    return ({"zuteilung": "offen", "zuteilung_am": now_iso(),
             "zuteilung_neu_wegen_aenderung": True},
            {"zuteilung_beantwortet_am": ""})


async def _termin_gehoert_mir(user: dict, appt: dict) -> bool:
    """Chef immer; Sucher nur, wenn er den Termin angelegt hat oder der
    verknuepfte Vertrag ihm gehoert (NICHT schon, weil ihm das Fahrzeug
    gehoert — sonst uebernaehme ein Mitbearbeiter den Termin des Kollegen)."""
    if user.get("role") != "sucher":
        return True
    if appt.get("created_by") == user["id"]:
        return True
    cid = appt.get("contract_id")
    return bool(cid) and await db.generated_pdfs.count_documents(
        {"id": cid, "user_id": user["id"]}, limit=1) > 0


async def _termin_einfuegen(doc: dict) -> None:
    """Insert des Auto-Termins (eigene Funktion, damit Tests das Zeitfenster
    'Vertragsloeschung zwischen Vorpruefung und Insert' nachstellen koennen)."""
    await db.appointments.insert_one(doc)


async def _abholtermin_fuer_vertrag(user: dict, body, vehicle: dict, pdf_id: str,
                                    kaufvorgang_id: Optional[str] = None):
    """Umbau Kaufvorgaenge 09.09.2026: EIN offener Abholtermin je VERTRAG
    (Teil-Unique-Index termin_offen_je_vertrag), nicht mehr je Fahrzeug.

    Mehrere Sucher duerfen dasselbe Inserat unabhaengig kaufen — jeder
    Vertrag bekommt seinen eigenen Termin. Ein bestehender Termin eines
    ANDEREN Vertrags wird nie umgehaengt (vorher verlor der alte Vertrag
    seinen Termin, Verkaeuferdaten wurden ueberschrieben). Idempotent: gibt
    es fuer diesen Vertrag schon einen offenen Termin (Wiederholung), wird
    er weiterverwendet. Liefert (appointment_id | None, hinweis | None)."""
    dealer_id = user["dealer_id"]
    # Runde 17 (Nr. 347): Abholadresse wie ein Terminfeld deckeln (500).
    pickup_address = " ".join([
        body.seller_address or "", body.seller_zip or "", body.seller_city or "",
    ]).strip()[:500]
    felder = {"seller_name": body.seller_name, "seller_phone": body.seller_phone,
              "seller_email": body.seller_email, "pickup_address": pickup_address,
              "pickup_date": body.pickup_date, "pickup_time": body.pickup_time or ""}
    if not kaufvorgang_id:
        vertrag = await db.generated_pdfs.find_one({"id": pdf_id}, {"_id": 0, "kaufvorgang_id": 1})
        kaufvorgang_id = (vertrag or {}).get("kaufvorgang_id")
    aktion = "termin.auto-erstellt"
    neu_angelegt = False
    for versuch in (1, 2):
        offen = await db.appointments.find_one(
            {"dealer_id": dealer_id, "contract_id": pdf_id,
             "status": {"$in": TERMIN_OFFEN_WERTE}},
            {"_id": 0, "id": 1})
        if offen:
            appt_id = offen["id"]
            aktion = "termin.auto-wiederverwendet"
            break
        appt_id = str(uuid.uuid4())
        title = (f"{vehicle.get('make_label','')} {vehicle.get('model_label','')} abholen".strip()
                 or "Fahrzeug abholen")
        # Pruefung 14.09.2026 (Liste 6, Nr. 7): kein Termin auf einen Vertrag,
        # der inzwischen geloescht wird.
        if not await db.generated_pdfs.count_documents(
                {"id": pdf_id, "loeschung.status": {"$ne": "laeuft"}}, limit=1):
            return None, ("Der Vertrag wird gerade gelöscht — kein Abholtermin angelegt.")
        try:
            await _termin_einfuegen({
                "id": appt_id, "dealer_id": dealer_id,
                "created_by": user["id"],     # Runde 10: sonst kann der Sucher ihn nie loeschen
                "title": title, "vehicle_id": body.vehicle_id, "contract_id": pdf_id,
                "kaufvorgang_id": kaufvorgang_id,
                **felder, "status": "offen",
                "created_at": now_iso(), "updated_at": now_iso(),
            })
            neu_angelegt = True
            break
        except DuplicateKeyError:
            if versuch == 2:
                raise
            continue                        # paralleler Termin desselben Vertrags gewann
    if neu_angelegt and not await db.generated_pdfs.count_documents(
            {"id": pdf_id, "loeschung.status": {"$ne": "laeuft"}}, limit=1):
        # Nachpruefung 15.09.2026 (Fahrer Nr. 3/4): die Vertragsloeschung lief
        # genau zwischen Vorpruefung und Insert — ihr Termin-Bereinigen hat den
        # frischen Termin nicht mehr gesehen. Der Termin ist unbenutzt (offen,
        # ohne Fahrer): weg damit, statt dauerhaft auf einen geloeschten
        # Vertrag zu zeigen. Rest faengt cleanup_service.termin_vertragsverweise_
        # bereinigen (nach 10 Minuten).
        await db.appointments.delete_one(
            {"id": appt_id, "contract_id": pdf_id, "status": "offen",
             "driver_id": {"$in": [None, ""]}})
        return None, ("Der Vertrag wird gerade gelöscht — kein Abholtermin angelegt.")
    # Pruefung 14.09.2026 (Liste 6, Nr. 3): Der Termin STEHT ab hier. Scheitert
    # das Nachziehen, meldete der Aufrufer "Termin konnte nicht angelegt werden"
    # — obwohl er existierte (zweiter Termin -> 409). Jetzt: Termin melden,
    # Rest per Merker nacharbeit_offen (update_appointment / Aufraeum-Job).
    hinweis = None
    try:
        await db.generated_pdfs.update_one(
            {"id": pdf_id},
            {"$set": {"appointment_id": appt_id, "status": "Termin erstellt"}},
        )
        import kaufvorgang as _kv
        if kaufvorgang_id:
            await _kv.status_setzen(kaufvorgang_id, "abholung_geplant", user=user,
                                    appointment_id=appt_id)
        else:
            await try_set_lifecycle(body.vehicle_id, dealer_id, "abholung_geplant", user=user)
    except Exception:  # noqa: BLE001
        log.exception("Auto-Termin %s: Nacharbeit fehlgeschlagen", appt_id)
        try:
            await db.appointments.update_one({"id": appt_id}, {"$set": {"nacharbeit_offen": True}})
        except Exception:  # noqa: BLE001
            log.exception("Merker nacharbeit_offen fuer Termin %s nicht gesetzt", appt_id)
        hinweis = ("Abholtermin angelegt; Vertrags-/Fahrzeugstatus werden nachgezogen.")
    await log_activity_sicher(dealer_id, user["id"], aktion, ref=appt_id,
                       meta={"contract_id": pdf_id, "vehicle_id": body.vehicle_id})
    return appt_id, hinweis


# Nach so vielen Sekunden gilt eine Zustellung "laeuft" als abgebrochen.
# Ein Versand dauert Sekunden; drei Minuten sind grosszuegig.
ZUSTELLUNG_HAENGT_NACH_SEK = int(os.environ.get("ZUSTELLUNG_HAENGT_NACH_SEK", "180"))


# Nachpruefung Runde 14: send_status waechst je Versand um einen Eintrag,
# ohne Obergrenze — bei mutwilligem Dauerversand (je Klick ein neuer
# Schluessel, kein Limiter) naeherte sich das Vertragsdokument samt pdf_b64
# der 16-MB-Grenze, und jede Vertragsliste lieferte die ganze Historie mit.
# $slice -N behaelt die juengsten N Eintraege (aelteste fallen raus); der
# frisch angehaengte Eintrag ist immer der letzte, das positionale $set und
# die Idempotenz-/Wiederaufnahme-Suche finden ihn weiter. Die vollstaendige
# Historie steht ohnehin in activity_logs (pdf.gesendet.<channel>).
SEND_STATUS_MAX = 200
# Runde 17 (Nr. 321): pickup_history (Terminverschiebungen je Vertrag)
# ebenso gedeckelt — juengste 100 Eintraege; die Vorversionen liegen
# ohnehin in generated_pdf_versions, das Audit in activity_logs.
PICKUP_HISTORY_MAX = 100


def _zustellung_haengt(eintrag: dict, jetzt=None) -> bool:
    """True, wenn eine Reservierung aelter ist als ZUSTELLUNG_HAENGT_NACH_SEK
    (oder schon als "unklar" markiert wurde)."""
    if eintrag.get("zustellung") == "unklar":
        return True
    try:
        # Nachpruefung Runde 10: Ein frischer Wiederaufnahme-Claim zaehlt wie
        # ein frischer Erstversand — sonst nehmen zehn gleichzeitige Klicks
        # denselben Eintrag alle wieder auf.
        start = datetime.fromisoformat(str(eintrag.get("wiederaufnahme_am")
                                           or eintrag.get("sent_at") or ""))
    except ValueError:
        return True
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    jetzt = jetzt or datetime.now(timezone.utc)
    return (jetzt - start).total_seconds() > ZUSTELLUNG_HAENGT_NACH_SEK


def _vertrag_bereich(user) -> Dict[str, Any]:
    """Welche Vertraege darf dieses Konto sehen?

    Betreiber-Entscheidung 09/2026: Der Chef sieht alle Vertraege seiner
    Firma, ein Sucher nur die, die er selbst angelegt hat. Vorher sah
    jeder Sucher die Verkaeuferdaten und PDFs seiner Kollegen — bei
    groesseren Haendlern weder gewollt noch datenschutzrechtlich sauber.

    Bewusst streng: fehlt einem alten Vertrag die Angabe, wer ihn angelegt
    hat, sieht ihn der Sucher NICHT (der Chef weiterhin schon). Lieber
    einmal zu wenig zeigen als fremde Verkaeuferdaten preisgeben.

    Runde 17 (Nr. 348): Vertraege mit Grabstein (`loeschung.status ==
    "laeuft"`, cleanup_service.vertrag_endgueltig_loeschen) sind NICHT mehr
    im Bereich — die Loeschung laeuft oder ist abgebrochen und wird vom
    Aufraeumjob zu Ende gefuehrt. Vorher waren solche Vertraege in Liste,
    Detail, PDF, Versionen und Versand weiter sichtbar/versendbar, und ein
    Termin liess sich noch daran haengen. Gilt fuer JEDEN Aufrufer
    (auch routes/appointments.py und routes/bestand.py importieren diesen
    Filter). delete_contract nutzt bewusst NUR dealer_id: das Loeschen
    bleibt idempotent und nimmt einen abgebrochenen Vorgang wieder auf."""
    bereich: Dict[str, Any] = {"dealer_id": user["dealer_id"],
                               "loeschung.status": {"$ne": "laeuft"}}
    if user.get("role") == "sucher":
        bereich["user_id"] = user["id"]
    return bereich


# Nachpruefung Runde 14: vorher stille 500 — bei mehr Vertraegen in 90 Tagen
# fehlten die aeltesten kommentarlos. Jetzt 2000, und ein Abschneiden wird
# per Kopfzeile X-Truncated signalisiert (Antwort bleibt eine Liste).
CONTRACTS_LIST_MAX = 2000


def _vertrag_maskieren(user: dict, doc: Optional[dict]) -> Optional[dict]:
    """Rollenpruefung 22.09.2026 (RP-017/RP-116): Konto-IDs von Chef und
    Kollegen aus Vertragsdaten entfernen — nur fuer Sucher, wie
    deps.konten_maskieren bei Fahrzeugen (Runde 30).

    Ein Sucher sieht nur eigene Vertraege (user_id ist seine eigene ID);
    Herkunfts- und Bearbeitungsvermerke nennen aber andere Konten:
    uebergeben_von, pickup_history[].geaendert_von (Chef verschiebt den
    Termin), freigabe[_alt].erstellt_von (Chef erzeugte den Link) und bei
    archivierten Fassungen archived_by. Kein Rechte-Leck, aber dieselbe
    Regel wie ueberall. Aendert das Dokument an Ort und Stelle."""
    if not ist_sucher(user) or not isinstance(doc, dict):
        return doc
    doc.pop("uebergeben_von", None)
    doc.pop("archived_by", None)
    if isinstance(doc.get("freigabe"), dict):
        doc["freigabe"] = {k: w for k, w in doc["freigabe"].items() if k != "erstellt_von"}
    if isinstance(doc.get("freigabe_alt"), list):
        doc["freigabe_alt"] = [{k: w for k, w in f.items() if k != "erstellt_von"}
                               if isinstance(f, dict) else f for f in doc["freigabe_alt"]]
    if isinstance(doc.get("pickup_history"), list):
        doc["pickup_history"] = [{k: w for k, w in e.items() if k != "geaendert_von"}
                                 if isinstance(e, dict) else e for e in doc["pickup_history"]]
    return doc


async def _vorgang_und_termin_anreichern(user: dict, items: list) -> list:
    """Rollenpruefung 22.09.2026 (RP-406/RP-417): je Vertrag
    `kaufvorgang_status` und `termin_offen` (gibt es einen OFFENEN
    Abholtermin?) — in ZWEI Sammelabfragen fuer die ganze Seite.

    Vorher zeigte der Versand-Dialog nach "nicht abgeholt" / "storniert"
    "Termin im Terminplaner angelegt", obwohl nichts angelegt wurde: er
    kannte nur appointment_id, und die zeigt weiter auf den geschlossenen
    Termin. Wirft nie — ohne Anreicherung faellt die Oberflaeche auf
    appointment_id zurueck."""
    ids = [i.get("id") for i in items if isinstance(i, dict) and i.get("id")]
    if not ids:
        return items
    try:
        vorgaenge: Dict[str, str] = {}
        async for kv in db.kaufvorgaenge.find(
                {"dealer_id": user["dealer_id"], "contract_id": {"$in": ids}},
                {"_id": 0, "contract_id": 1, "status": 1}):
            vorgaenge[kv.get("contract_id")] = kv.get("status")
        offen = set(await db.appointments.distinct(
            "contract_id", {"dealer_id": user["dealer_id"], "contract_id": {"$in": ids},
                            "status": {"$in": TERMIN_OFFEN_WERTE}}))
    except Exception as exc:  # noqa: BLE001
        log.warning("Vertragsliste: Vorgang/Termin nicht angereichert: %s", exc)
        return items
    for i in items:
        if isinstance(i, dict) and i.get("id"):
            i["kaufvorgang_status"] = vorgaenge.get(i["id"])
            i["termin_offen"] = i["id"] in offen
    return items


@router.get("/contracts")
async def list_contracts(
    response: Response,
    user=Depends(current_firma),
    # Runde 17 (Nr. 349/374): Suchtext und Zeitraum gedeckelt — vorher
    # liefen ein 1-MB-Regex (re.escape haelt ihn zwar harmlos, aber Mongo
    # musste ihn ueber jeden Vertrag ziehen) und days=10**9 (timedelta-
    # Ueberlauf -> 500) ungebremst durch. Annotated-Form, damit die Python-
    # Standardwerte None bleiben (In-Prozess-Aufrufer/Tests).
    q: Annotated[Optional[str], Query(max_length=200)] = None,
    days: Annotated[Optional[int], Query(ge=1, le=3650)] = None,
    channel: Optional[str] = None,
    limit: Annotated[Optional[int], Query(ge=1, le=500)] = None,
    before: Annotated[Optional[str], Query(max_length=64)] = None,
):
    query: Dict[str, Any] = _vertrag_bereich(user)
    # Rollenpruefung 22.09.2026 (RP-007/RP-106/RP-257): Das Archiv lud bis zu
    # 2.000 Vertraege auf einmal (und je Karte zwei Beweis-Abfragen). Jetzt
    # seitenweise: `limit` + Cursor `before` (created_at des letzten Eintrags,
    # wie /snapshots); X-Next-Before nennt den Cursor der naechsten Seite.
    # Ohne `limit` bleibt es beim alten Verhalten (bis 2.000, X-Truncated).
    if days:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query["created_at"] = {"$gte": since}
    if before:
        query.setdefault("created_at", {})["$lt"] = before
    if q:
        # re.escape prevents ReDoS and NoSQL-regex injection via crafted patterns.
        q_safe = re.escape(q)
        query["$or"] = [
            {"make": {"$regex": q_safe, "$options": "i"}},
            {"model": {"$regex": q_safe, "$options": "i"}},
            {"seller_name": {"$regex": q_safe, "$options": "i"}},
            # Pruefbericht 20.09.2026 (M37): die Vertragsnummer vom Ausdruck
            # soll im Archiv wiederauffindbar sein (so sagt es contract_no).
            {"contract_no": {"$regex": q_safe, "$options": "i"}},
        ]
    if channel:
        # Pruefung 14.09.2026 (Liste 3, Nr. 7): in der Abfrage filtern — vorher
        # erst NACH dem 2000er-Schnitt, aeltere Treffer fehlten trotz Filter.
        query["send_status.channel"] = channel
    grenze = limit or CONTRACTS_LIST_MAX
    items = await db.generated_pdfs.find(
        query, {"_id": 0, "pdf_b64": 0, "pdf_digital_b64": 0},
    ).sort("created_at", -1).to_list(grenze + 1)
    abgeschnitten = len(items) > grenze
    items = items[:grenze]
    # 10.09.2026: Inseratsfotos neben dem Vertrag als Vorschaubilder ueber
    # den eigenen Bild-Proxy (klein, zuverlaessig).
    from bild_proxy import thumbs as _thumbs
    for i in items:
        if i.get("vehicle_image_urls"):
            i["vehicle_image_urls_thumbs"] = _thumbs(i["vehicle_image_urls"][:12])
    response.headers["X-Truncated"] = "1" if abgeschnitten else "0"
    if limit and abgeschnitten and items:
        response.headers["X-Next-Before"] = str(items[-1].get("created_at") or "")
    # Rollenpruefung 22.09.2026 (RP-406/RP-417): Stand von Kaufvorgang und
    # Abholtermin je Vertrag (eine Sammelabfrage) — der Versand-Dialog und das
    # Archiv wissen so, ob noch ein OFFENER Termin existiert.
    await _vorgang_und_termin_anreichern(user, items)
    for i in items:
        _vertrag_maskieren(user, i)
    # Alt-Vertraege ohne `vehicle_image_urls` (frueher lagen die Fotos beim
    # Fahrzeug unter `data.images`, nicht `image_urls`): die Fotos werden
    # NUR fuer die Anzeige aus dem Fahrzeug ergaenzt und als nachgetragen
    # gekennzeichnet (`bilder_nachgetragen`).
    # Nachpruefung Runde 14: vorher schrieb dieses GET den HEUTIGEN
    # Bilderstand dauerhaft an den Vertrag — ein Lesezugriff verewigte
    # Fotos, die beim Vertragsschluss womoeglich anders aussahen, als
    # unmarkierten Vertragsbeweis. Lesen bleibt Lesen; gespeichert wird der
    # Bilderstand nur noch beim Anlegen (create_contract).
    ohne_fotos = [i for i in items if not i.get("vehicle_image_urls") and i.get("vehicle_id")]
    if ohne_fotos:
        vids = list({i["vehicle_id"] for i in ohne_fotos})
        bilder = {}
        async for v in db.vehicles.find(
                {"id": {"$in": vids}, "dealer_id": user["dealer_id"]},
                {"_id": 0, "id": 1, "data.images": 1, "data.image_urls": 1}):
            urls = _vehicle_bild_urls(v.get("data") or {})
            if urls:
                bilder[v["id"]] = urls
        for i in ohne_fotos:
            urls = bilder.get(i["vehicle_id"])
            if urls:
                i["vehicle_image_urls"] = urls
                i["bilder_nachgetragen"] = True
    # Ersteller anreichern: der Chef sieht so, WELCHER Sucher den Vertrag
    # (= Einkauf) gemacht hat.
    creator_ids = list({i.get("user_id") for i in items if i.get("user_id")})
    if creator_ids:
        names = {}
        async for u in db.users.find({"id": {"$in": creator_ids}},
                                     {"_id": 0, "id": 1, "email": 1, "kontonummer": 1,
                                      "first_name": 1, "last_name": 1, "role": 1}):
            # Kontonummer (13.09.2026): E-Mail ist nur noch optionale Kontakt-
            # adresse — ohne Namen zeigt die Liste die Kontonummer des Erstellers.
            label = f"{u.get('first_name') or ''} {u.get('last_name') or ''}".strip() \
                    or u.get("kontonummer") or u.get("email", "")
            names[u["id"]] = {"name": label, "role": u.get("role")}
        for i in items:
            c = names.get(i.get("user_id"))
            if c:
                i["created_by_name"] = c["name"]
                i["created_by_role"] = c["role"]
    return items


@router.get("/contracts/{contract_id}")
async def get_contract(contract_id: str, user=Depends(current_firma)):
    """Angaben zum Vertrag OHNE die eingebetteten PDF-Dateien.

    Runde 29 (12.09.2026, Pruefbefund): Die Route lieferte das komplette
    Dokument samt beider PDFs als Base64 — je nach Fotoanzahl mehrere
    hundert Kilobyte pro Aufruf, obwohl niemand sie hier braucht. Die
    Dateien gibt es weiterhin unter /contracts/{id}/pdf."""
    c = await db.generated_pdfs.find_one(
        {"id": contract_id, **_vertrag_bereich(user)},
        {"_id": 0, "pdf_b64": 0, "pdf_digital_b64": 0},
    )
    if not c:
        raise HTTPException(404, "Vertrag nicht gefunden")
    # Rollenpruefung 22.09.2026 (RP-406): wie die Liste mit Vorgangs-/Terminstand.
    await _vorgang_und_termin_anreichern(user, [c])
    # Rollenpruefung 22.09.2026 (RP-017/RP-116): keine fremden Konto-IDs fuer Sucher.
    return _vertrag_maskieren(user, c)


@router.get("/contracts/{contract_id}/pdf")
async def get_contract_pdf(contract_id: str, user=Depends(current_firma),
                           variante: str = "druck"):
    """?variante=druck (Standard): Fassung mit Unterschriftslinien.
    ?variante=digital: Ausfertigung fuer E-Mail/WhatsApp — Text statt
    Unterschriftslinien (Altvertraege werden einmalig nacherzeugt)."""
    c = await db.generated_pdfs.find_one(
        {"id": contract_id, **_vertrag_bereich(user)},
        {"_id": 0, "id": 1, "pdf_b64": 1, "pdf_digital_b64": 1, "filename": 1,
         "contract_data": 1, "vehicle_id": 1, "dealer_id": 1, "user_id": 1,
         "version": 1},
    )
    if not c:
        raise HTTPException(404, "Vertrag nicht gefunden")
    if variante == "digital":
        pdf_bytes = await _digitales_pdf_bytes(c, user)
        if not pdf_bytes:
            # Kein stiller Ersatz durch die Druckfassung (Pruefbefund).
            raise HTTPException(503, DIGITAL_FEHLER_HINWEIS)
    else:
        pdf_bytes = base64.b64decode(c["pdf_b64"])
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        # Pruefung 14.09.2026 (Liste 3, Nr. 4): Personendaten nie im Browser-Cache.
        # Rollenpruefung 22.09.2026 (RP-200): ASCII + UTF-8-Name, kein 500 bei 'Š'.
        # Pruefbericht 20.09.2026 (U-73): Fassung dieser Datei — der Versand-
        # Dialog merkt sie zum Blob und schickt sie beim Teilen mit (SendIn.version).
        headers={"Content-Disposition": content_disposition(
                     c.get("filename") or "", fallback="kaufvertrag.pdf"),
                 "Cache-Control": "no-store",
                 "X-Vertrag-Version": str(int(c.get("version") or 1))},
    )


@router.get("/public/vertrag/{token}")
async def public_vertrag_pdf(token: str, request: Request):
    """Digitale Vertragsfassung ueber den Download-Link aus der WhatsApp-
    Nachricht — OHNE Anmeldung, nur mit gueltigem Token (siehe
    _freigabe_link). Je IP gedrosselt; abgelaufen = 410; geloescht oder
    in Loeschung = 404.

    Runde 18: Der Link ist an die FASSUNG gebunden, die verschickt wurde
    (freigabe.version). Wird der Vertrag danach neu erzeugt (verschobener
    Abholtermin), liefert derselbe Link weiterhin die archivierte Fassung —
    vorher zeigte er stillschweigend einen anderen Vertragsinhalt.

    Gezaehlt wird ein ANONYMER Linkabruf. Wer den Link hat, kann ihn oeffnen;
    das ist kein Nachweis, dass der Verkaeufer das Dokument gesehen hat."""
    from rate_limiter import client_ip
    if not await _link_limiter.check(client_ip(request)):
        raise HTTPException(429, "Zu viele Anfragen — bitte kurz warten.")
    if not token or len(token) > 80 or not re.fullmatch(r"[A-Za-z0-9_\-]+", token):
        raise HTTPException(404, "Link ungültig")
    c = await db.generated_pdfs.find_one(
        {"$or": [{"freigabe.token": token}, {"freigabe_alt.token": token}],
         "loeschung.status": {"$ne": "laeuft"}},
        {"_id": 0, "id": 1, "pdf_b64": 1, "pdf_digital_b64": 1, "filename": 1,
         "contract_data": 1, "vehicle_id": 1, "dealer_id": 1, "user_id": 1,
         "version": 1, "freigabe": 1, "freigabe_alt": 1})
    if not c:
        raise HTTPException(404, "Link ungültig oder Vertrag nicht mehr vorhanden")
    freigabe = c.get("freigabe") or {}
    if freigabe.get("token") != token:
        # Runde 16: ein aelterer, noch laufender Link — er liefert weiterhin
        # die damals verschickte Fassung (Zusage im Chat bleibt gueltig).
        freigabe = next((f for f in (c.get("freigabe_alt") or []) if f.get("token") == token), {})
    try:
        laeuft_ab = datetime.fromisoformat(freigabe.get("laeuft_ab") or "")
    except ValueError:
        laeuft_ab = None
    if not laeuft_ab or laeuft_ab <= datetime.now(timezone.utc):
        raise HTTPException(410, "Dieser Link ist abgelaufen — bitte den Händler um "
                                 "einen neuen Link bitten.")
    geteilte_version = int(freigabe.get("version") or c.get("version") or 1)
    if geteilte_version != int(c.get("version") or 1):
        # Der Vertrag wurde nach dem Versand neu erzeugt: die damals
        # verschickte Fassung liegt im Versionsarchiv.
        alt = await db.generated_pdf_versions.find_one(
            {"contract_id": c["id"], "dealer_id": c.get("dealer_id"),
             "version": geteilte_version},
            {"_id": 0, "pdf_digital_b64": 1, "pdf_b64": 1, "filename": 1,
             "contract_data": 1})
        # Abnahme 12.09.2026: Fehlte die digitale Fassung im Archiv, kam
        # still die DRUCKfassung mit Unterschriftslinien — obwohl der Link
        # eine digitale Ausfertigung zusagt. Sie wird jetzt aus den
        # archivierten Vertragsdaten nacherzeugt (dieselbe Funktion wie
        # fuer Altvertraege); nur wenn auch das nicht geht, gibt es 410.
        if (alt or {}).get("pdf_digital_b64"):
            pdf_bytes = base64.b64decode(alt["pdf_digital_b64"])
        elif alt:
            ersteller_alt = await db.users.find_one(
                {"id": c.get("user_id")}, {"_id": 0}) \
                or {"id": c.get("user_id"), "dealer_id": c.get("dealer_id")}
            pdf_bytes = await _digitales_pdf_bytes({**c, **alt}, ersteller_alt, cache=False)
        else:
            pdf_bytes = None
        if not pdf_bytes:
            raise HTTPException(410, "Der Vertrag wurde inzwischen geändert und die "
                                     "verschickte Fassung ist nicht mehr abrufbar — "
                                     "bitte den Händler um einen neuen Link bitten.")
        fname_quelle = (alt or {}).get("filename") or c.get("filename") or ""
    else:
        ersteller = await db.users.find_one({"id": c.get("user_id")}, {"_id": 0}) \
            or {"id": c.get("user_id"), "dealer_id": c.get("dealer_id")}
        pdf_bytes = await _digitales_pdf_bytes(c, ersteller)
        if not pdf_bytes:
            raise HTTPException(503, "Der Vertrag kann gerade nicht bereitgestellt "
                                     "werden — bitte in ein paar Minuten erneut versuchen.")
        fname_quelle = c.get("filename") or ""
    # Pruefung 14.09.2026 (Liste 5, Nr. 3): begann die Loeschung waehrend der
    # PDF-Erzeugung, wird nichts mehr ausgeliefert.
    if not await db.generated_pdfs.count_documents(
            {"id": c["id"], "loeschung.status": {"$ne": "laeuft"}}, limit=1):
        raise HTTPException(404, "Link ungültig oder Vertrag nicht mehr vorhanden")
    await _abruf_zaehlen(c["id"], token, aktuell=(c.get("freigabe") or {}).get("token") == token)
    # Anonymer Abruf: KEINEM Konto zurechenbar (weder Ersteller noch
    # Empfaenger) — der Audit-Eintrag sagt genau das.
    from deps import log_activity_sicher
    await log_activity_sicher(c.get("dealer_id"), None, "vertrag.link.abgerufen",
                              ref=c["id"], meta={"anonym": True,
                                                 "version": geteilte_version})
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        # Rollenpruefung 22.09.2026 (RP-200): ASCII + UTF-8-Name, kein 500 bei 'Š'.
        headers={"Content-Disposition": content_disposition(
                     fname_quelle, fallback="kaufvertrag.pdf"),
                 "Cache-Control": "private, no-store",
                 "X-Robots-Tag": "noindex"},
    )


@router.get("/contracts/{contract_id}/versions")
async def list_contract_versions(contract_id: str, response: Response,
                                 user=Depends(current_firma)):
    """Archivierte Vertragsfassungen (ohne PDF-Inhalt, nur Metadaten).
    Pruefung 14.09.2026 (Liste 6, Nr. 10): 1000 statt 100, und ein
    Abschneiden wird per X-Truncated gemeldet statt still zu geschehen."""
    c = await db.generated_pdfs.find_one(
        {"id": contract_id, **_vertrag_bereich(user)}, {"_id": 0, "id": 1})
    if not c:
        raise HTTPException(404, "Vertrag nicht gefunden")
    grenze = 1000
    # Rollenpruefung 22.09.2026 (RP-045/RP-144): bei einer Kuerzung fehlten
    # die NEUESTEN Fassungen (aufsteigend gelesen, hinten abgeschnitten). Jetzt
    # die juengsten `grenze` lesen und aufsteigend ausliefern; das Archiv
    # zeigt den Hinweis auf X-Truncated.
    fassungen = await db.generated_pdf_versions.find(
        {"contract_id": contract_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "pdf_b64": 0, "pdf_digital_b64": 0, "contract_data": 0},
    ).sort("version", -1).to_list(grenze + 1)
    response.headers["X-Truncated"] = "1" if len(fassungen) > grenze else "0"
    fassungen = list(reversed(fassungen[:grenze]))
    # Rollenpruefung 22.09.2026 (RP-017/RP-116): archived_by nennt das Konto,
    # das die Fassung abgeloest hat (oft der Chef) — fuer Sucher weg.
    return [_vertrag_maskieren(user, f) for f in fassungen]


@router.get("/contracts/{contract_id}/versions/{version}/pdf")
async def get_contract_version_pdf(contract_id: str, version: int,
                                   user=Depends(current_firma),
                                   variante: str = "druck"):
    """Archivierte PDF-Fassung herunterladen (Beweissicherung).
    ?variante=digital liefert die damals archivierte digitale Ausfertigung;
    fehlt sie (Altfassung), kommt ehrlich die Druckfassung."""
    # Auch die alten Fassungen nur, wenn der Vertrag selbst sichtbar ist.
    haupt = await db.generated_pdfs.find_one(
        {"id": contract_id, **_vertrag_bereich(user)}, {"_id": 0, "id": 1})
    if not haupt:
        raise HTTPException(404, "Vertragsfassung nicht gefunden")
    v = await db.generated_pdf_versions.find_one(
        {"contract_id": contract_id, "dealer_id": user["dealer_id"],
         "version": version},
        {"_id": 0, "pdf_b64": 1, "pdf_digital_b64": 1, "filename": 1, "contract_data": 1})
    if not v or not v.get("pdf_b64"):
        raise HTTPException(404, "Vertragsfassung nicht gefunden")
    if variante == "digital" and v.get("pdf_digital_b64"):
        pdf_bytes = base64.b64decode(v["pdf_digital_b64"])
    elif variante == "digital":
        # Runde 16 (15.09.2026): wie beim oeffentlichen Link — die digitale
        # Fassung aus den archivierten Vertragsdaten nacherzeugen statt still
        # die Druckfassung zu liefern.
        voll = await db.generated_pdfs.find_one(
            {"id": contract_id, **_vertrag_bereich(user)},
            {"_id": 0, "pdf_b64": 0, "pdf_digital_b64": 0})
        pdf_bytes = await _digitales_pdf_bytes(
            {**(voll or {}), **v, "id": contract_id, "version": version}, user, cache=False)
        if not pdf_bytes:
            raise HTTPException(503, DIGITAL_FEHLER_HINWEIS)
    else:
        pdf_bytes = base64.b64decode(v["pdf_b64"])
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        # Rollenpruefung 22.09.2026 (RP-200): ASCII + UTF-8-Name, kein 500 bei 'Š'.
        headers={"Content-Disposition": content_disposition(
                     v.get("filename") or "", fallback=f"kaufvertrag-v{version}.pdf"),
                 "Cache-Control": "no-store"},
    )


def _auto_schluessel(contract_id: str, c: dict, body) -> str:
    """Inhaltsschluessel fuer Aufrufer ohne eigenen Schluessel. Runde 10 nahm
    die Minute heraus (Doppelklick ueber die Minutengrenze); die Nachpruefung
    ergaenzt die Vertrags-VERSION: nach einer Neuerzeugung des PDFs
    (Terminverschiebung) ist derselbe Text ein neuer Versand — vorher hiess
    es fuer immer "bereits gesendet"."""
    import hashlib
    roh = "|".join([contract_id, str(c.get("version") or 1), body.channel,
                    body.recipient or "", body.subject or "", body.message or ""])
    return "auto-" + hashlib.sha256(roh.encode("utf-8")).hexdigest()[:24]


async def _reservierung_nachlesen(contract_id: str, bereich: dict,
                                  idempotency_key: str) -> None:
    """Pruefbericht 20.09.2026 (Nr. 8): Warum hat die Reservierung nichts
    geaendert?

    Vorher galt `modified_count == 0` immer als "ein anderer Versand laeuft
    schon" — die Antwort sagte `zustellung: laeuft, bereits_gesendet: true`.
    Null Aenderungen entstehen aber genauso, wenn der Vertrag GENAU in diesem
    Moment geloescht oder einem anderen Konto uebertragen wurde. Dann laeuft
    gar kein Versand, und der Sucher wartet auf eine Mail, die nie kommt.

    Diese Pruefung wirft in genau diesem Fall. Sagt sie nichts, war es
    wirklich ein zweiter Versand und der Aufrufer antwortet wie bisher.
    """
    doc = await db.generated_pdfs.find_one(
        {"id": contract_id, **bereich},
        {"_id": 0, "send_status": 1})
    if doc is None:
        raise HTTPException(404, "Dieser Vertrag ist nicht mehr verfügbar — er "
                                 "wurde gerade gelöscht oder übertragen. Es "
                                 "wurde NICHTS versendet.")
    # WICHTIG (gefunden am 20.09.2026 durch den Parallel-Test
    # test_verlierer_ueberschreibt_kein_ergebnis): Ein FEHLENDER Eintrag ist
    # KEIN Fehler. Die Reservierung wird auch dann abgelehnt, wenn zu
    # demselben Kanal und Empfaenger gerade ein ANDERER Versand laeuft
    # (die Sperre oben). Dann gibt es unseren Schluessel natuerlich noch
    # nicht — und "es laeuft schon" ist genau die richtige Antwort.
    #
    # Eine erste Fassung dieser Pruefung antwortete hier mit 409 und machte
    # aus dem Normalfall einen sichtbaren Fehler. Die Pruefung erkennt
    # deshalb NUR den Fall, um den es wirklich ging: der Vertrag ist weg
    # oder gehoert jemand anderem — dann wird sicher nichts versendet.
    return


async def _versand_laeuft(contract_id: str) -> bool:
    """Wird dieser Vertrag GERADE nach draussen verschickt?

    Pruefbericht 20.09.2026 (N2): Die Versandreservierung schuetzt bisher nur
    davor, DENSELBEN Vertrag zweimal an DENSELBEN Empfaenger zu schicken. Das
    Aendern und das Loeschen des Vertrags warten aber auf gar nichts — ein
    Vertrag konnte also mitten im externen Mailversand neu erzeugt oder
    geloescht werden. Beim Loeschen kam die Mail danach trotzdem beim
    Verkaeufer an, und die Anwendung vermerkte nur "Status-Vermerk nicht
    gespeichert".

    Bewusst KEIN Schloss ueber den ganzen Versand: der haengt an einem
    externen Dienst (Zeitueberschreitung 30 s, dazu Wiederholungen) — ein so
    lange gehaltenes Schloss wuerde den Abholweg blockieren. Stattdessen
    wird die Aenderung waehrenddessen abgelehnt; der Nutzer wiederholt sie
    ein paar Sekunden spaeter. Zusammen mit der Fassungspruefung beim
    Abschluss (siehe _abschluss) kann der Verkaeufer damit keinen anderen
    Stand bekommen, als die Anwendung anzeigt.

    "Laeuft" heisst: Eintrag auf `zustellung: laeuft`, der noch nicht als
    haengend gilt (ZUSTELLUNG_HAENGT_NACH_SEK) — ein abgestuerzter Versand
    blockiert also nichts dauerhaft.
    """
    frisch_ab = (datetime.now(timezone.utc)
                 - timedelta(seconds=ZUSTELLUNG_HAENGT_NACH_SEK)).isoformat()
    return await db.generated_pdfs.count_documents(
        {"id": contract_id,
         "send_status": {"$elemMatch": {
             "zustellung": "laeuft",
             "$or": [{"sent_at": {"$gt": frisch_ab}},
                     {"wiederaufnahme_am": {"$gt": frisch_ab}}]}}},
        limit=1) > 0


def _kein_laufender_versand() -> Dict[str, Any]:
    """Pruefbericht 20.09.2026 (V-26): dieselbe Bedingung wie _versand_laeuft,
    aber als TEIL eines Schreibfilters. Die Vorpruefung allein reichte nicht:
    eine Neuerzeugung, die vor der Versand-Reservierung geprueft hatte, konnte
    danach noch committen — der Verkaeufer bekam die alte Fassung. Steht die
    Bedingung im Compare-and-Set, sind Pruefung und Schreiben atomar."""
    frisch_ab = (datetime.now(timezone.utc)
                 - timedelta(seconds=ZUSTELLUNG_HAENGT_NACH_SEK)).isoformat()
    return {"send_status": {"$not": {"$elemMatch": {
        "zustellung": "laeuft",
        "$or": [{"sent_at": {"$gt": frisch_ab}},
                {"wiederaufnahme_am": {"$gt": frisch_ab}}]}}}}


VERSAND_LAEUFT_TEXT = ("Dieser Vertrag wird gerade verschickt — bitte in ein paar "
                       "Sekunden erneut versuchen.")

#: Rollenpruefung 22.09.2026 (RP-216/RP-367): Kaufvorgaenge, zu denen kein
#: Vertrag und keine Folge-Mail mehr an den Verkaeufer geht.
KAUF_BEENDET = ("storniert", "nicht_abgeholt")
VERSAND_KAUF_BEENDET = ("Der Kauf ist storniert bzw. das Fahrzeug wurde nicht abgeholt — "
                        "der Kaufvertrag wird nicht mehr verschickt. Soll die Abholung "
                        "doch stattfinden, zuerst einen neuen Abholtermin anlegen.")


async def _kaufvorgang_status(c: dict) -> Optional[str]:
    """Status des Kaufvorgangs zu diesem Vertrag (None = keiner / nicht
    lesbar). Rein lesend; ein Lesefehler sperrt den Versand nicht."""
    try:
        kv = await db.kaufvorgaenge.find_one(
            {"contract_id": c.get("id"), "dealer_id": c.get("dealer_id")},
            {"_id": 0, "status": 1})
    except Exception as exc:  # noqa: BLE001
        log.warning("Kaufvorgang zu Vertrag %s nicht lesbar: %s", c.get("id"), exc)
        return None
    return (kv or {}).get("status")


def _platzhalter_im_versand(text: Optional[str], vertrag: dict, firma: dict,
                            user: dict) -> Optional[str]:
    """Rollenpruefung 22.09.2026 (RP-212/RP-363/RP-424): Platzhalter in
    Betreff und Nachricht des Vertragsversands serverseitig einsetzen —
    dieselbe Tabelle wie PDF und Folge-Mail (vertrag_platzhalter). Leerer
    Text bleibt leer (dann nimmt vertrag_mail den Standard)."""
    if not text:
        return text
    from vertrag_platzhalter import ersetzen as _ersetzen
    return _ersetzen(text, vertrag, firma, user)


async def _haengenden_versand_abloesen(contract_id: str, bereich: dict,
                                       eintrag: dict) -> None:
    """Rollenpruefung 22.09.2026 (RP-221/RP-372): einen haengenden
    Versandeintrag (laeuft/unklar, ohne Ergebnis) mit ANDEREM Inhalt als
    "abgeloest" markieren. Der Eintrag bleibt als Beleg; nur ein Eintrag,
    der seit dem Lesen nicht wieder aufgenommen wurde, wird angefasst."""
    await db.generated_pdfs.update_one(
        {"id": contract_id, **bereich,
         "send_status": {"$elemMatch": {
             "idempotency_key": eintrag.get("idempotency_key"),
             "zustellung": {"$in": ["laeuft", "unklar"]},
             "wiederaufnahme_am": eintrag.get("wiederaufnahme_am")}}},
        {"$set": {"send_status.$.zustellung": "abgeloest",
                  "send_status.$.abgeloest_am": now_iso()}})
    log.warning("Vertrag %s: haengender Versand %s (anderer Inhalt) abgeloest",
                contract_id, eintrag.get("idempotency_key"))


def _abschluss(send_entry: dict, neuer_status: str, fassung_veraltet: bool,
               *, wiederaufnahme: bool) -> dict:
    """Das Update, mit dem ein Versand abgeschlossen wird.

    Pruefbericht 20.09.2026 (N1): Entstand WAEHREND des Versands eine neue
    Fassung, ging beim Verkaeufer Fassung N raus — der Vertrag stand danach
    aber als Fassung N+1 auf "versendet", und der Merker
    `nach_abholung_versand_offen` war geloescht. Der Sucher sah also "erledigt",
    obwohl der Verkaeufer den falschen Stand hatte.

    Deshalb: der send_status-Eintrag wird IMMER geschrieben (er traegt die
    Fassung, die wirklich rausging — das ist der Beleg). Status und Merker
    des Vertrags ruehrt der Abschluss nur an, wenn die versendete Fassung
    auch die aktuelle ist.
    """
    eintrag = ({"$set": {"send_status.$": send_entry}} if wiederaufnahme
               else {"$push": {"send_status": {"$each": [send_entry],
                                               "$slice": -SEND_STATUS_MAX}}})
    if fassung_veraltet:
        eintrag.setdefault("$set", {})["updated_at"] = now_iso()
        return eintrag
    eintrag.setdefault("$set", {}).update(
        {"status": neuer_status, "updated_at": now_iso()})
    # 19.09.2026: Die Rueckfrage "neuen Vertrag senden?" ist damit
    # beantwortet — der Hinweis verschwindet aus der Liste.
    eintrag["$unset"] = {"nach_abholung_versand_offen": ""}
    return eintrag


@router.post("/contracts/{contract_id}/send")
async def send_contract(contract_id: str, body: SendIn, user=Depends(require_active_sub)):
    # Pruefbericht Runde 8 (09/2026), hoher Befund: Lesen und PDF gingen
    # laengst ueber _vertrag_bereich, der VERSAND aber nur ueber die Firma.
    # Ein Sucher mit der Vertrags-ID eines Kollegen konnte dessen Vertrag
    # samt PDF an eine beliebige Adresse schicken und den Versandstatus
    # veraendern. Jetzt gilt beim Versand derselbe Bereich wie beim Lesen —
    # und JEDE Schreibabfrage in dieser Funktion nutzt ihn ebenfalls.
    bereich = _vertrag_bereich(user)
    c = await db.generated_pdfs.find_one({"id": contract_id, **bereich}, {"_id": 0})
    if not c:
        raise HTTPException(404, "Vertrag nicht gefunden")
    # Pruefbericht 20.09.2026 (P-05): fuer E-Mail GENAU eine gueltige Adresse.
    # Vorher ging der freie Text ungeprueft weiter — per SMTP wurde er zum
    # To-Kopf (Komma = mehrere Empfaenger), und eine vom Mail-Dienst
    # abgelehnte Adresse endete als "bitte in ein paar Minuten erneut".
    if body.channel == "email":
        import email_service as _es
        adresse = (body.recipient or "").strip()
        if any(z in adresse for z in ",;") or not _es.gueltige_adresse(adresse):
            raise HTTPException(422, "Bitte genau EINE gültige E-Mail-Adresse eingeben "
                                     "(z. B. name@beispiel.de).")
        body.recipient = adresse
    # Rollenpruefung 22.09.2026 (RP-216/RP-367): Nach einem Storno (auch dem
    # V-12-Rueckweg durch den Chef) oder "nicht abgeholt" liess sich der
    # Kaufvertrag weiter an den Verkaeufer schicken — dieselbe Regel wie bei
    # der Folge-Mail (FOLGE_MAIL_STORNIERT). Erst ein neuer Abholtermin macht
    # den Kauf wieder offen.
    if await _kaufvorgang_status(c) in KAUF_BEENDET:
        raise HTTPException(409, VERSAND_KAUF_BEENDET)
    # Pruefbericht 20.09.2026 (U-73): Das Handy hat eine vorab geladene Datei
    # geteilt. Ist der Vertrag seitdem neu erzeugt worden (Terminverschiebung,
    # Preis nach Abholung, Verkaeufer-Korrektur), ging die ALTE Fassung raus —
    # kein Vermerk; der Dialog laedt die neue Fassung und der Nutzer teilt
    # noch einmal. Vor der Reservierung, damit nichts zurueckzunehmen ist.
    if body.methode == "teilen" and body.version is not None \
            and int(c.get("version") or 1) != int(body.version):
        raise HTTPException(409, {
            "code": "fassung_veraltet",
            "msg": "Geteilt wurde eine veraltete Fassung des Vertrags — es gibt inzwischen "
                   f"Fassung {int(c.get('version') or 1)}. Bitte die neue Fassung noch einmal "
                   "teilen.",
            "version": int(c.get("version") or 1),
            "geteilt": int(body.version),
        })
    # Runde 16 (15.09.2026): Versand-Limit je Konto — kein Spam-/Kostenpfad
    # ueber frei eingetragene Empfaenger (Resend/SMTP).
    if not await _versand_limiter.check(f"konto:{user.get('id')}"):
        raise HTTPException(429, f"Zu viele Versände in kurzer Zeit — höchstens "
                                 f"{VERSAND_JE_KONTO_10MIN} je 10 Minuten. Bitte etwas warten.")
    anfrage_hash = _versand_anfrage_hash(c, body)
    frueherer_versand_abgeloest = False
    # Idempotenz RESERVIEREND (Review 09/2026): Der Schluessel wurde vorher
    # erst NACH dem Senden eingetragen — zwei gleichzeitige Anfragen mit
    # demselben Schluessel konnten beide zustellen. Jetzt wird der Eintrag
    # atomar VOR dem Versand angelegt; der Verlierer bekommt das Ergebnis
    # des Gewinners, bei Sendefehler wird der Eintrag wieder entfernt.
    # Pruefbericht Runde 8, Befund 3: Ohne Schluessel gab es keinerlei
    # Schutz — zwei identische Aufrufe stellten zweimal zu. Fehlt der
    # Schluessel, wird er jetzt aus dem Inhalt abgeleitet: dieselbe Mail an
    # denselben Empfaenger ist EIN Versand.
    # Runde 10: vorher steckte die Minute im Schluessel — ein Doppelklick um
    # 12:00:59 und 12:01:00 stellte zweimal zu. Jetzt zaehlt nur der Inhalt;
    # wer denselben Vertrag bewusst noch einmal schicken will, gibt eine
    # andere Nachricht oder einen eigenen Schluessel mit.
    if not body.idempotency_key:
        body.idempotency_key = _auto_schluessel(contract_id, c, body)
    reserviert = False
    wiederaufnahme = False
    reserviert_am = now_iso()
    claim_am = None
    if body.idempotency_key:
        eintraege = c.get("send_status") or []
        vorhanden = next((e for e in eintraege
                          if e.get("idempotency_key") == body.idempotency_key), None)
        if vorhanden is None:
            # Pruefung 14.09.2026 (A5): der Schluessel kann aus der 200er-
            # Verlaufsliste herausgefallen sein — die eigene Sammlung kennt ihn noch.
            try:
                archiv = await db.versand_schluessel.find_one(
                    {"contract_id": contract_id, "key": body.idempotency_key}, {"_id": 0})
            except Exception:  # noqa: BLE001 — Archiv ist Zusatz, der Versand laeuft
                archiv = None
            if archiv:
                if archiv.get("anfrage_hash") and archiv["anfrage_hash"] != anfrage_hash:
                    raise HTTPException(409, "Dieser Versand-Schlüssel gehört zu einem anderen "
                                             "Versand (Empfänger oder Text geändert) — bitte "
                                             "die Seite neu laden und erneut senden.")
                return {"channel": archiv.get("channel"), "status": "ok",
                        "sent_at": archiv.get("sent_at"), "zustellung": "archiv",
                        "bereits_gesendet": True}
            # Nachpruefung Runde 10: Die Oberflaeche schickt je Klick einen
            # NEUEN Schluessel. Haengt zu demselben Kanal und Empfaenger noch
            # ein Versand ohne Ergebnis (Prozess starb, Timeout), haette der
            # zweite Klick ein zweites Mal zugestellt. Er uebernimmt jetzt
            # den haengenden Eintrag — unter DESSEN Schluessel, damit auch
            # Resend nicht doppelt zustellt.
            haengend = next((e for e in eintraege
                             if e.get("idempotency_key")
                             and e.get("channel") == body.channel
                             and (e.get("recipient") or "") == (body.recipient or "")
                             and e.get("zustellung") in ("laeuft", "unklar")
                             and _zustellung_haengt(e)), None)
            if haengend and haengend.get("anfrage_hash") \
                    and haengend["anfrage_hash"] != anfrage_hash:
                # Rollenpruefung 22.09.2026 (RP-221/RP-372): Der haengende
                # Versand hatte einen ANDEREN Inhalt (neue Fassung, anderer
                # Text). Frueher wurde er trotzdem uebernommen und scheiterte
                # dann an der Hash-Pruefung mit 409 — und weil "unklar" nie
                # verschwindet, war derselbe Empfaenger fuer immer gesperrt,
                # auch nach dem Neuladen. Jetzt: der alte Eintrag bleibt als
                # Beleg stehen (zustellung "abgeloest"), dieser Versand wird
                # normal reserviert. Vor Doppelversand schuetzt weiter die
                # Sperre fuer FRISCH laufende Versande (unten).
                await _haengenden_versand_abloesen(contract_id, bereich, haengend)
                frueherer_versand_abgeloest = True
            elif haengend:
                body.idempotency_key = haengend["idempotency_key"]
                vorhanden = haengend
        if vorhanden and vorhanden.get("anfrage_hash") and vorhanden["anfrage_hash"] != anfrage_hash:
            # Runde 16: gleicher Schluessel, anderer Inhalt (Empfaenger/Text nach
            # einem unklaren Versuch geaendert) -> nie wiederaufnehmen.
            raise HTTPException(409, "Dieser Versand-Schlüssel gehört zu einem anderen Versand "
                                     "(Empfänger oder Text geändert) — bitte die Seite neu laden "
                                     "und erneut senden.")
        if (vorhanden and vorhanden.get("zustellung") in ("laeuft", "unklar")
                and _zustellung_haengt(vorhanden)):
            # Zeitpunkt des ersten Versuchs behalten: die Kopie an den Sucher
            # muss bei der Wiederaufnahme denselben Inhalt haben (Resend).
            reserviert_am = vorhanden.get("sent_at") or reserviert_am
            # Der Prozess ist zwischen Reservierung und Ergebnis gestorben.
            # Frueher hiess es hier dauerhaft "bereits gesendet" — obwohl
            # womoeglich nie etwas rausging. Jetzt wird der Versand unter
            # DEMSELBEN Schluessel wiederholt; Resend erkennt den Schluessel
            # und stellt nicht doppelt zu.
            # Nachpruefung Runde 10: Die Wiederaufnahme wird ATOMAR beansprucht
            # (Compare-and-Swap auf den gelesenen Stand). Von zehn gleich-
            # zeitigen Klicks gewinnt genau einer; die anderen bekommen
            # "laeuft / bereits_gesendet" — vorher stellten alle zu.
            claim_am = now_iso()
            res = await db.generated_pdfs.update_one(
                {"id": contract_id, **bereich,
                 "send_status": {"$elemMatch": {
                     "idempotency_key": body.idempotency_key,
                     "zustellung": {"$in": ["laeuft", "unklar"]},
                     "wiederaufnahme_am": vorhanden.get("wiederaufnahme_am")}}},
                {"$set": {"send_status.$.zustellung": "laeuft",
                          "send_status.$.wiederaufnahme_am": claim_am}})
            if res.modified_count == 0:
                # Nr. 8: erst nachsehen, ob der Vertrag ueberhaupt noch da ist.
                await _reservierung_nachlesen(contract_id, bereich,
                                              body.idempotency_key)
                return {"channel": vorhanden.get("channel"), "status": "ok",
                        "sent_at": vorhanden.get("sent_at"),
                        "zustellung": "laeuft", "bereits_gesendet": True}
            wiederaufnahme = True
            reserviert = True
            vorhanden = None
        if vorhanden:
            return {"channel": vorhanden.get("channel"), "status": "ok",
                    "sent_at": vorhanden.get("sent_at"),
                    "zustellung": vorhanden.get("zustellung", ""),
                    "wa_url": vorhanden.get("wa_url"), "bereits_gesendet": True}
    if body.idempotency_key and not wiederaufnahme:
        # Runde 16 (15.09.2026): zwei Tabs mit VERSCHIEDENEN Schluesseln duerfen
        # denselben Vertrag nicht gleichzeitig an denselben Empfaenger schicken —
        # ein noch frischer, laufender Versand desselben Kanals/Empfaengers
        # sperrt die Reservierung (haengende aeltere Eintraege nimmt der Pfad
        # oben wieder auf).
        # Nur fuer Kanaele, die der Server selbst zustellt (E-Mail): bei
        # WhatsApp entsteht nur der Download-Link, und der ist je Vertrag
        # ohnehin eindeutig (_freigabe_link) — zwei Tabs bekommen denselben.
        frisch_ab = (datetime.now(timezone.utc)
                     - timedelta(seconds=ZUSTELLUNG_HAENGT_NACH_SEK)).isoformat()
        sperre = {"send_status": {"$not": {"$elemMatch": {
            "channel": body.channel, "recipient": body.recipient,
            "zustellung": "laeuft",
            "$or": [{"sent_at": {"$gt": frisch_ab}},
                    {"wiederaufnahme_am": {"$gt": frisch_ab}}]}}}}             if body.channel == "email" else {}
        res = await db.generated_pdfs.update_one(
            {"id": contract_id, **bereich,
             "send_status.idempotency_key": {"$ne": body.idempotency_key},
             **sperre},
            {"$push": {"send_status": {"$each": [{
                "idempotency_key": body.idempotency_key, "channel": body.channel,
                "recipient": body.recipient, "subject": body.subject,
                "sent_at": reserviert_am, "zustellung": "laeuft",
                "anfrage_hash": anfrage_hash, "version": int(c.get("version") or 1)}],
                "$slice": -SEND_STATUS_MAX}}})
        if res.modified_count == 0:
            # Nr. 8: dasselbe hier — "nichts geaendert" heisst nicht
            # automatisch "laeuft schon".
            await _reservierung_nachlesen(contract_id, bereich,
                                          body.idempotency_key)
            return {"channel": body.channel, "status": "ok", "sent_at": now_iso(),
                    "zustellung": "laeuft", "bereits_gesendet": True}
        reserviert = True

    # Pruefbericht 20.09.2026 (N1): wahr, wenn waehrend des Versands eine neue
    # Fassung entstand — dann darf der AKTUELLE Vertrag nicht als versendet
    # gelten (Begruendung bei _abschluss). Bewusst HIER, vor jeder
    # Verzweigung: sonst fehlt der Merker auf den Wegen, die den
    # Versandblock gar nicht betreten.
    fassung_veraltet = False

    async def _reservierung_zurueck():
        # Bei einer Wiederaufnahme bleibt der Eintrag stehen: er traegt
        # "unklar", damit der naechste Versuch wieder hier landet.
        if reserviert and wiederaufnahme:
            # nur den EIGENEN Claim zuruecksetzen — nie das Ergebnis eines
            # anderen Aufrufers ueberschreiben
            await db.generated_pdfs.update_one(
                {"id": contract_id, **bereich,
                 "send_status": {"$elemMatch": {"idempotency_key": body.idempotency_key,
                                                "wiederaufnahme_am": claim_am}}},
                {"$set": {"send_status.$.zustellung": "unklar"}})
            return
        if reserviert:
            await db.generated_pdfs.update_one(
                {"id": contract_id, **bereich},
                {"$pull": {"send_status": {"idempotency_key": body.idempotency_key,
                                           "zustellung": "laeuft"}}})
            # Runde 16: ein gescheiterter Versand hinterlaesst KEINEN
            # Archiveintrag mehr — sonst hiess es beim naechsten Versuch
            # "bereits gesendet", obwohl nie etwas rausging.
            try:
                await db.versand_schluessel.delete_one(
                    {"contract_id": contract_id, "key": body.idempotency_key,
                     "zustellung": {"$exists": False}})
            except Exception:  # noqa: BLE001
                log.exception("Versand-Schluessel %s nach Fehlschlag nicht entfernt", contract_id)
    # Runde 17 (Nr. 370): Zwischen dem Lesen oben und dem Versand kann die
    # Loeschung (Frist oder manuell) begonnen haben — der Grabstein nimmt
    # den Vertrag aus dem Bereich. Unmittelbar vor dem Versand noch einmal
    # nachsehen, sonst ginge ein PDF raus, dessen Vertrag gerade
    # verschwindet (und der Status-Vermerk liefe ins Leere).
    frisch = await db.generated_pdfs.find_one({"id": contract_id, **bereich},
                                              {"_id": 0, "version": 1})
    if frisch is None:            # {} = Altvertrag ohne version-Feld, nicht "weg"
        await _reservierung_zurueck()
        raise HTTPException(409, "Vertrag wird gerade gelöscht")
    # Pruefung 14.09.2026 (Liste 5, Nr. 1): wurde der Vertrag seit dem Lesen neu
    # erzeugt (Terminverschiebung, neuer Preis nach Abholung), geht NICHT die
    # alte Fassung raus — der Nutzer laedt neu und sendet die aktuelle.
    if int(frisch.get("version") or 1) != int(c.get("version") or 1):
        await _reservierung_zurueck()
        raise HTTPException(409, "Der Vertrag wurde gerade neu erstellt — bitte die Seite "
                                 "neu laden und erneut senden.")
    # Ehrlicher Versand-Status (PR-Review 09/2026): "versendet" gibt es
    # NUR nach tatsaechlicher Zustellung an den Anbieter. WhatsApp oeffnet
    # lediglich den Chat (PDF haengt der Nutzer selbst an) -> der Vertrag
    # wird als "versand_vorbereitet" gefuehrt, nicht als versendet.
    out: dict = {"channel": body.channel, "status": "ok", "sent_at": now_iso()}
    if body.channel == "whatsapp":
        from urllib.parse import quote_plus
        if body.methode == "teilen":
            # Handy: das PDF wurde ueber das Teilen-Menue an WhatsApp
            # uebergeben (eigene Nummer des Suchers, keine Empfaengernummer
            # noetig). Ob er im Chat wirklich auf Senden getippt hat, wissen
            # wir nicht -> "vorbereitet".
            out["zustellung"] = "geteilt"
        else:
            digits = wa_nummer(body.recipient)
            if not (WA_ZIFFERN_MIN <= len(digits) <= WA_ZIFFERN_MAX):
                # Runde 16 (15.09.2026): keine wa.me-Links und keine
                # "vorbereitet"-Vermerke fuer unbrauchbare Nummern.
                await _reservierung_zurueck()
                raise HTTPException(422, "WhatsApp-Nummer ungültig — bitte mit Vorwahl "
                                         "eingeben (7 bis 15 Ziffern, z. B. 0170 1234567).")
            try:
                link, gueltig_bis = await _freigabe_link(contract_id, bereich, user)
            except HTTPException:
                await _reservierung_zurueck()
                raise
            except Exception:
                log.exception("Download-Link fuer Vertrag %s konnte nicht erzeugt werden",
                              contract_id)
                await _reservierung_zurueck()
                raise HTTPException(502, "Download-Link konnte nicht erzeugt werden — "
                                         "bitte erneut versuchen.")
            try:
                bis_text = datetime.fromisoformat(gueltig_bis).strftime("%d.%m.%Y")
            except ValueError:
                bis_text = gueltig_bis[:10]
            # Rollenpruefung 22.09.2026 (RP-212/RP-363): auch selbst getippte
            # Platzhalter werden eingesetzt — mit der Firma DES VERTRAGS.
            from deps import effective_dealer as _eff
            nachricht = _platzhalter_im_versand(
                body.message, c, _firma_des_vertrags(c, await _eff(user) or {}), user)
            text = (nachricht or "").rstrip() + \
                f"\n\nKaufvertrag als PDF (Link gültig bis {bis_text}):\n{link}"
            out["wa_url"] = f"https://wa.me/{digits}?text={quote_plus(text)}"
            out["download_link"] = link
            out["link_gueltig_bis"] = gueltig_bis
            # Runde 16: "link_bereit" statt "chat_geoeffnet" — ob der Browser den
            # Chat wirklich oeffnet, entscheidet sich erst NACH dieser Antwort.
            out["zustellung"] = "link_bereit"
        neuer_status = "versand_vorbereitet"
    elif body.channel == "email":
        from provider_fetch import MOCK_PROVIDER_FETCH
        import email_service
        if MOCK_PROVIDER_FETCH:
            # Last-/CI-Tests: kein echter Versand, aber ehrlich markiert.
            out["zustellung"] = "mock"
            neuer_status = "versendet"
        elif not email_service.email_configured():
            await _reservierung_zurueck()
            raise HTTPException(503,
                "E-Mail-Versand ist nicht eingerichtet (RESEND_API_KEY oder "
                "SMTP_* in der .env setzen) — der Vertrag wurde NICHT "
                "versendet. Alternativ per WhatsApp teilen oder das PDF "
                "herunterladen.")
        else:
            # Versand IMMER von unserer eigenen Adresse (nur die ist beim
            # Mail-Anbieter freigeschaltet). Der Anzeigename nennt die Firma,
            # und die Antwortadresse ist der Sucher: antwortet der Verkaeufer,
            # landet die Antwort direkt bei ihm (Wunsch 09/2026).
            from vertrag_mail import kopie_mail, sucher_kontakt, vertrag_mail
            # Verschickt wird die DIGITALE Ausfertigung (ohne Unterschrifts-
            # linien, mit dem digitalen Vertragstext) — Wunsch 09.09.2026.
            pdf_bytes = await _digitales_pdf_bytes(c, user)
            if not pdf_bytes:
                # Kein stiller Versand der Druckfassung (Pruefbefund): der
                # Vertrag wird NICHT als versendet markiert.
                await _reservierung_zurueck()
                raise HTTPException(503, DIGITAL_FEHLER_HINWEIS
                                    + " Der Vertrag wurde NICHT versendet.")
            # Pruefbericht 20.09.2026 (P-11): Altvertraege tragen einen
            # unbereinigten Dateinamen — im Mailanhang genauso saeubern wie
            # bei der Anlage und im Download.
            dateiname = _safe_filename(c.get("filename") or "", fallback="Kaufvertrag.pdf")
            # Nachpruefung Runde 14: dieselbe Firmenidentitaet wie im PDF —
            # effective_dealer legt die gewollten Sucher-Overrides (Firmen-
            # name, Telefon, Logo, E-Mail) ueber die Chef-Vorgaben. Vorher
            # las der Versand das rohe Haendler-Dokument, und Betreff, Kopf
            # und Absendername der Mail nannten den Chef statt der Filiale,
            # die auf dem angehaengten Vertrag steht.
            from deps import effective_dealer
            firma = await effective_dealer(user) or {}
            # Runde 16 (15.09.2026): dieselbe Kaeufer-Identitaet wie im PDF — die
            # im Vertrag eingefrorenen Kaeuferdaten (Firma, Anschrift, Telefon)
            # gewinnen ueber den heutigen Stand von Firma/Filiale.
            _, firma = _apply_contract_overrides(
                contract=dict(c.get("contract_data") or {}), vehicle={}, dealer=dict(firma))
            # Rollenpruefung 22.09.2026 (RP-212/RP-363/RP-424): Platzhalter in
            # Betreff UND Text setzt jetzt auch der Server ein. Vorher ersetzte
            # nur der Browser beim Oeffnen des Dialogs — der Betreff nie, und
            # spaeter getippte Platzhalter gingen woertlich an den Verkaeufer.
            # Idempotent: bereits ersetzter Text hat keine Klammern mehr.
            nachricht = _platzhalter_im_versand(body.message, c, firma, user)
            betreff_roh = _platzhalter_im_versand(body.subject, c, firma, user)
            betreff, text, html = vertrag_mail(
                vertrag=c, firma=firma, sucher=user,
                nachricht=nachricht, betreff=betreff_roh)
            # Kontonummer (13.09.2026): Konten ohne E-Mail — Antworten gehen an
            # die eigene Adresse des Suchers, sonst an die Firmenadresse; die
            # Belegkopie NUR an eine eigene Adresse (sonst kopie=nicht_moeglich);
            # beim Chef zaehlt die Firmenadresse als seine eigene.
            sucher_mail, antwort_adresse = sucher_kontakt(user, firma)
            # Rollenpruefung 22.09.2026 (RP-473): ohne gueltige Antwortadresse
            # landen Antworten des Verkaeufers bei unserer Plattformadresse —
            # die Mail verspricht das dann nicht mehr (vertrag_mail), und die
            # Oberflaeche sagt es dem Sucher.
            if not email_service.gueltige_adresse(antwort_adresse):
                out["antwort_adresse_fehlt"] = True
            ok, beleg = await email_service.send_email_mit_beleg(
                body.recipient, betreff, text, anhang=pdf_bytes,
                anhang_name=dateiname, html=html,
                reply_to=antwort_adresse,
                absender_name=firma.get("company_name") or "",
                idempotency_key=f"vertrag-{contract_id}-{body.idempotency_key}")
            if ok and beleg:
                out["beleg"] = beleg
            if not ok and beleg == "anhang_zu_gross":
                # P-18: nichts an den Anbieter uebergeben — der Sucher bekommt
                # den Ausweg (Link-Weg) genannt, kein "spaeter erneut".
                await _reservierung_zurueck()
                raise HTTPException(413, _anhang_zu_gross_hinweis())
            if not ok:
                await _reservierung_zurueck()
                raise HTTPException(502, "E-Mail-Versand fehlgeschlagen — "
                                         "bitte in ein paar Minuten erneut "
                                         "versuchen. Der Vertrag wurde NICHT "
                                         "als versendet markiert.")
            out["zustellung"] = "versendet"
            neuer_status = "versendet"
            # Runde 12 (15.09.2026, Nr. 26): Pruefung und Zustellung sind nicht
            # atomar — wurde der Vertrag GENAU dazwischen neu erzeugt, ging die
            # alte Fassung raus. Das wird gemeldet (Hinweis + Betriebsalarm),
            # der Sucher schickt die neue Fassung nach.
            try:
                danach = await db.generated_pdfs.find_one({"id": contract_id}, {"_id": 0, "version": 1})
                if danach is not None and int(danach.get("version") or 1) != int(c.get("version") or 1):
                    # Pruefbericht 20.09.2026 (N1): DAS hier war der schwerste
                    # Fehler. Der Hinweis wurde zwar gemeldet — der Vertrag
                    # bekam danach trotzdem status="versendet" und verlor den
                    # Merker nach_abholung_versand_offen. Ergebnis: der
                    # Verkaeufer hatte Fassung N in der Hand, die Liste zeigte
                    # Fassung N+1 als versendet, und die Erinnerung, die neue
                    # Fassung nachzuschicken, war weg. Bei einem KAUFVERTRAG
                    # ist das nicht hinnehmbar.
                    #
                    # Jetzt: der Versand wird als das protokolliert, was er
                    # war — Fassung N ging raus (steht im send_status-Eintrag)
                    # —, aber der AKTUELLE Vertrag bleibt unversendet und
                    # behaelt seinen Merker. Die Liste sagt also weiterhin
                    # "noch zu senden", und genau das stimmt.
                    fassung_veraltet = True
                    out["hinweis"] = ("Achtung: Der Vertrag wurde während des Versands neu erstellt — "
                                      "die E-Mail enthält noch die vorherige Fassung. Bitte die neue "
                                      "Fassung erneut senden.")
                    import betrieb as _betrieb
                    await _betrieb.alarm(db, "versand_alte_fassung", ref=contract_id,
                                         dealer_id=user.get("dealer_id", ""),
                                         gesendet=int(c.get("version") or 1),
                                         aktuell=int(danach.get("version") or 1))
            except Exception:  # noqa: BLE001
                log.exception("Fassungspruefung nach Versand von %s fehlgeschlagen", contract_id)
            # Umbau Kaufvorgaenge: der Vorgang ist jetzt "gesendet" (best effort)
            try:
                import kaufvorgang as _kv
                _vorgang = await _kv.fuer_vertrag(c)
                if _vorgang and _vorgang.get("status") == "vertrag_erstellt":
                    await _kv.status_setzen(_vorgang["id"], "gesendet", user=user,
                                            von="vertrag_erstellt")
            except Exception:
                log.exception("Kaufvorgang nach Versand von %s nicht aktualisiert", contract_id)
                # Phase 2 (2.4, G10): Merker statt nur Log — cleanup_service.
                # vertrags_nacharbeit_nachholen setzt "gesendet" nach.
                try:
                    await db.generated_pdfs.update_one(
                        {"id": contract_id},
                        {"$set": {"nacharbeit_offen": True, "nacharbeit_status": "gesendet"}})
                except Exception:  # noqa: BLE001
                    log.exception("Merker nacharbeit_offen (Versand) fuer Vertrag %s nicht gesetzt",
                                  contract_id)
            # Kopie an den Sucher — als Beleg, mit demselben PDF. Schlaegt
            # sie fehl, bleibt der Hauptversand gueltig; das Ergebnis steht
            # in der Antwort ("kopie").
            out["kopie"] = "nicht_moeglich"
            if email_service.gueltige_adresse(sucher_mail):
                k_betreff, k_text, k_html = kopie_mail(
                    vertrag=c, firma=firma, sucher=user,
                    empfaenger_adresse=body.recipient,
                    betreff_original=betreff, nachricht=nachricht,
                    zeitpunkt=reserviert_am)
                out["kopie"] = "gesendet" if await email_service.send_email(
                    sucher_mail, k_betreff, k_text, anhang=pdf_bytes,
                    anhang_name=dateiname, html=k_html,
                    absender_name=firma.get("company_name") or "",
                    idempotency_key=f"kopie-{contract_id}-{body.idempotency_key}") else "fehlgeschlagen"
                if out["kopie"] != "gesendet":
                    log.warning("Kopie des Vertrags %s an %s fehlgeschlagen",
                                contract_id, sucher_mail)
    else:
        await _reservierung_zurueck()
        raise HTTPException(400, "Unbekannter Kanal")
    send_entry = {
        "channel": body.channel, "recipient": body.recipient,
        "subject": body.subject, "sent_at": out["sent_at"],
        "zustellung": out.get("zustellung", ""),
        # Runde 16: welche FASSUNG rausging (Beleg nach mehreren Fassungen)
        "version": int(c.get("version") or 1), "anfrage_hash": anfrage_hash,
    }
    if body.methode:
        send_entry["methode"] = body.methode
    if out.get("download_link"):
        send_entry["download_link"] = out["download_link"]
        send_entry["link_gueltig_bis"] = out.get("link_gueltig_bis")
    if frueherer_versand_abgeloest:
        # Rollenpruefung 22.09.2026 (RP-221): Hinweis fuer die Oberflaeche —
        # ein frueherer Versuch an diesen Empfaenger blieb ohne Ergebnis.
        out["frueherer_versand_unklar"] = True
    if reserviert:
        # Reservierten Eintrag mit dem Ergebnis fuellen (positional update).
        send_entry["idempotency_key"] = body.idempotency_key
        if out.get("wa_url"):
            send_entry["wa_url"] = out["wa_url"]
        if out.get("beleg"):
            send_entry["beleg"] = out["beleg"]
        if wiederaufnahme:
            send_entry["wiederaufgenommen"] = True
            send_entry["wiederaufnahme_am"] = claim_am
        res = await db.generated_pdfs.update_one(
            {"id": contract_id, **bereich,
             "send_status": {"$elemMatch": {
                 "idempotency_key": body.idempotency_key,
                 **({"wiederaufnahme_am": claim_am} if wiederaufnahme else {})}}},
            _abschluss(send_entry, neuer_status, fassung_veraltet,
                       wiederaufnahme=True),
        )
    else:
        res = await db.generated_pdfs.update_one(
            {"id": contract_id, **bereich},
            _abschluss(send_entry, neuer_status, fassung_veraltet,
                       wiederaufnahme=False),
        )
    if reserviert and res.matched_count:
        # Runde 16: Archiv-Eintrag ERST nach dem Erfolg (vorher bei der
        # Reservierung — ein Fehlschlag blieb dauerhaft "bereits gesendet").
        try:
            await db.versand_schluessel.update_one(
                {"contract_id": contract_id, "key": body.idempotency_key},
                {"$set": {"dealer_id": c.get("dealer_id"), "channel": body.channel,
                          "recipient": body.recipient, "sent_at": out["sent_at"],
                          "zustellung": out.get("zustellung", ""),
                          "version": int(c.get("version") or 1), "anfrage_hash": anfrage_hash}},
                upsert=True)
        except Exception:  # noqa: BLE001 — Archiv ist Zusatz, der Versand ist erfolgt
            log.exception("Versand-Schluessel %s nicht archiviert", contract_id)
    if res.matched_count == 0:
        # Runde 17 (Nr. 372): Der Versand IST erfolgt, aber der Vertrag war
        # beim Vermerk nicht mehr im Bereich (Loeschung begonnen, Eintrag
        # durch $slice verdraengt). Vorher blieb das stumm — die Antwort
        # sagte "versendet", das Archiv wusste nichts davon. Jetzt im Log,
        # in der Antwort und als eigener Audit-Eintrag (wirft nie).
        log.warning("Vertrag %s per %s versendet, Status-Vermerk aber nicht "
                    "gespeichert (Vertrag nicht mehr im Bereich?)",
                    contract_id, body.channel)
        out["status_vermerk"] = "nicht_gespeichert"
        await log_activity_sicher(user["dealer_id"], user["id"],
                                  "pdf.gesendet.ohne_vermerk", ref=contract_id,
                                  meta={"channel": body.channel})
    await log_activity_sicher(user["dealer_id"], user["id"], f"pdf.gesendet.{body.channel}", ref=contract_id)
    return out


@router.get("/contracts/{contract_id}/folge-mail/{art}")
async def folge_mail_vorschau(contract_id: str, art: str,
                              user=Depends(current_firma)):
    """Betreff und Text einer Vorlage, Platzhalter schon eingesetzt.

    Wunsch Ahmad 21.09.2026: Hinweis nach Kaufabschluss (E-Mail/WhatsApp)
    und Bahnverbindung — der Sucher kopiert den fertigen Text mit Namen und
    Daten des Vertrags oder verschickt ihn per E-Mail (POST folge-mail)."""
    import vertrag_vorlagen as _vorlagen
    from vertrag_platzhalter import ersetzen as _ersetzen
    if art not in _vorlagen.FOLGE_MAILS:
        raise HTTPException(404, "Unbekannte Vorlage")
    c = await db.generated_pdfs.find_one(
        {"id": contract_id, **_vertrag_bereich(user)}, {"_id": 0})
    if not c:
        raise HTTPException(404, "Vertrag nicht gefunden")
    from deps import effective_dealer
    firma = _firma_des_vertrags(c, await effective_dealer(user) or {})
    betreff, text = _vorlagen.vorlage(firma, art)
    return {"art": art,
            "empfaenger": (c.get("seller_email") or "").strip(),
            "betreff": _ersetzen(betreff, c, firma, user),
            "text": _ersetzen(text, c, firma, user)}


class FolgeMailIn(BaseModel):
    """Eine Vorlage per E-Mail verschicken (Hinweis nach Kaufabschluss oder
    Bahnverbindung). Die Art wird in der Route geprueft — so gibt es eine
    deutsche Meldung statt der englischen Pydantic-Fehlermeldung."""
    art: str = Field(max_length=40)
    recipient: str = Field(max_length=200)
    subject: Optional[str] = Field(default=None, max_length=500)
    message: Optional[str] = Field(default=None, max_length=20000)
    idempotency_key: Optional[str] = Field(
        default=None, min_length=8, max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$")


#: Per E-Mail verschickbar. Die WhatsApp-Fassung wird kopiert, eine
#: Korrektur geht ueber den normalen Versand MIT Vertrag als Anhang.
FOLGE_MAIL_VERSCHICKBAR = ("nach_kauf", "bahn")
FOLGE_MAIL_ART_HINWEIS = {
    "korrektur": ("Einen korrigierten Vertrag bitte über „Senden“ verschicken — "
                  "dann hängt der Vertrag an."),
    "nach_kauf_whatsapp": ("Den WhatsApp-Text bitte kopieren und in WhatsApp "
                           "einfügen."),
}
FOLGE_MAIL_STORNIERT = ("Der Kauf ist storniert bzw. nicht abgeholt — diese "
                        "Mail wird nicht mehr verschickt.")
#: Rollenpruefung 22.09.2026 (RP-215/RP-366)
FOLGE_MAIL_BAHN_OHNE_VERBINDUNG = (
    "Bitte zuerst die Bahnverbindung (Zug und voraussichtliche Ankunftszeit des "
    "Fahrers) in den Text schreiben — die Vorlage kündigt sie an, und die Mail hat "
    "keinen Anhang.")


def _ohne_leerraum(text: Optional[str]) -> str:
    """Vergleichsform eines Textes: Leerraum und Zeilenumbrueche egal."""
    return " ".join(str(text or "").split())


async def _folge_mail_vorhanden(contract_id: str, bereich: dict,
                                schluessel: str) -> Optional[dict]:
    """Rollenpruefung 22.09.2026 (RP-434): Was ist mit dem Eintrag, der
    diesen Schluessel schon traegt?

    * versendet/mock -> {"zustellung": …} (wirklich schon verschickt)
    * laeuft, noch frisch -> {"zustellung": "laeuft"} (die Oberflaeche sagt
      "laeuft noch" statt "bereits verschickt")
    * laeuft, aber haengend (Prozess starb) -> wird ATOMAR neu beansprucht,
      Rueckgabe None: der Aufrufer versendet erneut (derselbe Anbieter-
      Schluessel verhindert eine doppelte Zustellung)."""
    doc = await db.generated_pdfs.find_one({"id": contract_id, **bereich},
                                           {"_id": 0, "send_status": 1})
    eintrag = next((e for e in (doc or {}).get("send_status") or []
                    if isinstance(e, dict) and e.get("idempotency_key") == schluessel), None)
    if eintrag is None:
        return {"zustellung": "laeuft"}
    zustellung = eintrag.get("zustellung") or ""
    if zustellung != "laeuft":
        return {"zustellung": zustellung}
    if _zustellung_haengt(eintrag):
        res = await db.generated_pdfs.update_one(
            {"id": contract_id, **bereich,
             "send_status": {"$elemMatch": {
                 "idempotency_key": schluessel, "zustellung": "laeuft",
                 "wiederaufnahme_am": eintrag.get("wiederaufnahme_am")}}},
            {"$set": {"send_status.$.wiederaufnahme_am": now_iso()}})
        if res.modified_count:
            return None
    return {"zustellung": "laeuft"}


def _firma_des_vertrags(vertrag: dict, firma: dict) -> dict:
    """Rollenpruefung 21.09.2026: Die Folge-Mail nannte die Firma des
    AUFRUFERS (Chef verschickt fuer einen Sucher -> Chef-Firma). Massgeblich
    sind die beim Vertrag eingefrorenen Kaeuferdaten (kaeufer_einfrieren)."""
    daten = vertrag.get("contract_data") or {}
    out = dict(firma or {})
    for feld, ziel in (("dealer_company", "company_name"), ("dealer_phone", "phone"),
                       ("dealer_email", "email")):
        wert = str(daten.get(feld) or "").strip()
        if wert:
            out[ziel] = wert
    return out


@router.post("/contracts/{contract_id}/folge-mail")
async def folge_mail_senden(contract_id: str, body: FolgeMailIn,
                            user=Depends(require_active_sub)):
    """Hinweis nach Kaufabschluss oder Bahnverbindung per E-Mail verschicken.

    Wunsch Ahmad 21.09.2026: erst "wir selber schicken die nicht raus",
    spaeter am selben Abend "doch zum Verschicken kann bleiben". Also: der
    Sucher kann die Vorlage beim Vertrag KOPIEREN oder hier verschicken —
    nie automatisch. Ohne Anhang, reiner Text; es ist eine Nachricht zum
    Vorgang, kein Vertrag. Der Versand wird am Vertrag vermerkt (send_status
    mit `art`).
    """
    import email_service
    import vertrag_vorlagen as _vorlagen
    from provider_fetch import MOCK_PROVIDER_FETCH
    from vertrag_mail import sucher_kontakt
    from vertrag_platzhalter import ersetzen as _ersetzen
    art = (body.art or "").strip()
    if art in FOLGE_MAIL_ART_HINWEIS:
        raise HTTPException(400, FOLGE_MAIL_ART_HINWEIS[art])
    if art not in FOLGE_MAIL_VERSCHICKBAR:
        raise HTTPException(400, "Unbekannte Vorlage")
    empfaenger = (body.recipient or "").strip()
    if not email_service.gueltige_adresse(empfaenger):
        raise HTTPException(400, "Bitte eine gültige E-Mail-Adresse angeben")
    # Dieselbe Bremse wie beim Vertragsversand — sonst waere das hier ein
    # offener Weg, ueber unsere Adresse beliebig viele Mails zu schicken.
    if not await _versand_limiter.check(f"konto:{user.get('id')}"):
        raise HTTPException(429, "Zu viele Sendungen in kurzer Zeit — bitte "
                                 f"höchstens {VERSAND_JE_KONTO_10MIN} je 10 Minuten.")
    if not MOCK_PROVIDER_FETCH and not email_service.email_configured():
        raise HTTPException(503, "E-Mail-Versand ist nicht eingerichtet — die Mail "
                                 "wurde NICHT versendet.")
    bereich = _vertrag_bereich(user)
    c = await db.generated_pdfs.find_one({"id": contract_id, **bereich}, {"_id": 0})
    if not c:
        raise HTTPException(404, "Vertrag nicht gefunden")
    # Rollenpruefung 21.09.2026: nach Storno / "nicht abgeholt" passt weder
    # "Fahrzeug ist verkauft" noch die Bahnverbindung des Fahrers.
    kv = await db.kaufvorgaenge.find_one(
        {"contract_id": contract_id, "dealer_id": c.get("dealer_id")},
        {"_id": 0, "status": 1})
    if (kv or {}).get("status") in ("storniert", "nicht_abgeholt"):
        raise HTTPException(409, FOLGE_MAIL_STORNIERT)
    from deps import effective_dealer
    firma = _firma_des_vertrags(c, await effective_dealer(user) or {})
    std_betreff, std_text = _vorlagen.vorlage(firma, art)
    # Auch ein selbst geaenderter Text bekommt seine Platzhalter ersetzt —
    # sonst las der Verkaeufer "{kunde_name}" (Rollenpruefung 21.09.2026).
    betreff = _ersetzen((body.subject or "").strip() or std_betreff, c, firma, user)
    text = _ersetzen((body.message or "").strip() or std_text, c, firma, user)
    if art == "bahn" and _ohne_leerraum(text) == _ohne_leerraum(
            _ersetzen(std_text, c, firma, user)):
        # Rollenpruefung 22.09.2026 (RP-215/RP-366): Die Vorlage kuendigt
        # "anbei … die Bahnverbindung mit der voraussichtlichen Ankunftszeit"
        # an — verschickt wird aber nur Text, ohne Anhang. Unveraendert
        # abgeschickt versprach die Mail etwas, das fehlt. (Den Wortlaut der
        # Vorlage aendert nur Ahmad.)
        raise HTTPException(400, FOLGE_MAIL_BAHN_OHNE_VERBINDUNG)

    schluessel = (body.idempotency_key or "").strip()
    if schluessel:
        # Rollenpruefung 22.09.2026 (RP-434): Ein frueher gescheiterter
        # Versuch mit DIESEM Schluessel (Altbestand: zustellung
        # "fehlgeschlagen") blockiert nicht mehr — vorher meldete der naechste
        # Klick "bereits verschickt", obwohl nie etwas rausging.
        await db.generated_pdfs.update_one(
            {"id": contract_id, **bereich,
             "send_status": {"$elemMatch": {"idempotency_key": schluessel,
                                            "zustellung": "fehlgeschlagen"}}},
            {"$pull": {"send_status": {"idempotency_key": schluessel,
                                       "zustellung": "fehlgeschlagen"}}})
        # Doppelklick-Schutz wie beim Vertragsversand: derselbe Schluessel
        # legt garantiert nur EINEN Eintrag an.
        res = await db.generated_pdfs.update_one(
            {"id": contract_id, **bereich,
             "send_status.idempotency_key": {"$ne": schluessel}},
            {"$push": {"send_status": {"$each": [{
                "idempotency_key": schluessel, "channel": "email",
                "art": art, "recipient": empfaenger, "subject": betreff,
                "sent_at": now_iso(), "zustellung": "laeuft"}],
                "$slice": -SEND_STATUS_MAX}}})
        if res.modified_count == 0:
            await _reservierung_nachlesen(contract_id, bereich, schluessel)
            # RP-434: ehrlich sagen, was mit dem vorhandenen Eintrag ist —
            # "laeuft" heisst "laeuft noch", nicht "verschickt".
            antwort = await _folge_mail_vorhanden(contract_id, bereich, schluessel)
            if antwort is not None:
                return {"status": "ok", "bereits_gesendet": True, "art": art, **antwort}
            # None: ein abgebrochener Versuch (Prozess starb) wurde gerade neu
            # beansprucht — derselbe Anbieter-Schluessel unten verhindert eine
            # doppelte Zustellung.
            log.info("Folge-Mail %s zu %s: haengender Versuch wird wiederholt",
                     art, contract_id)

    _, antwort_adresse = sucher_kontakt(user, firma)
    if MOCK_PROVIDER_FETCH:
        # Last-/CI-Tests: kein echter Versand, aber ehrlich markiert.
        ok, beleg = True, "mock"
    else:
        try:
            ok, beleg = await email_service.send_email_mit_beleg(
                empfaenger, betreff, text, anhang=None, anhang_name="",
                html=None, reply_to=antwort_adresse,
                absender_name=firma.get("company_name") or "",
                idempotency_key=f"folge-{contract_id}-{schluessel or art}")
        except Exception:  # noqa: BLE001 — Anbieterfehler = nicht versendet
            log.exception("Folge-Mail %s zu %s fehlgeschlagen", art, contract_id)
            ok, beleg = False, ""
    if not ok:
        if schluessel:
            # Rollenpruefung 22.09.2026 (RP-434): Reservierung wieder
            # entfernen (wie _reservierung_zurueck beim Vertragsversand) —
            # vorher blieb "fehlgeschlagen" stehen, und derselbe Schluessel
            # (der Dialog behaelt ihn fuer die Wiederholung) meldete danach
            # "bereits verschickt".
            await db.generated_pdfs.update_one(
                {"id": contract_id, **bereich},
                {"$pull": {"send_status": {"idempotency_key": schluessel,
                                           "zustellung": "laeuft"}}})
        raise HTTPException(502, "E-Mail-Versand fehlgeschlagen — bitte in ein "
                                 "paar Minuten erneut versuchen.")
    if schluessel:
        await db.generated_pdfs.update_one(
            {"id": contract_id, **bereich,
             "send_status.idempotency_key": schluessel},
            {"$set": {"send_status.$.zustellung": "mock" if beleg == "mock" else "versendet",
                      "send_status.$.beleg": beleg or ""}})
    await log_activity_sicher(user["dealer_id"], user["id"],
                              f"pdf.folgemail.{art}", ref=contract_id)
    return {"status": "ok", "art": art, "empfaenger": empfaenger,
            "betreff": betreff, "zustellung": "mock" if beleg == "mock" else "versendet"}


async def _offene_termine_beim_loeschen_stornieren(user: dict, contract_id: str) -> int:
    """Rollenpruefung 22.09.2026 (RP-415): offene Abholtermine eines Vertrags,
    der gerade manuell geloescht wird, auf "storniert" setzen — samt Fahrer-
    Zusage, die damit hinfaellig ist. Felder wie beim Statuswechsel im
    Terminplaner (status_changed_at, abgeschlossen_seit beim ERSTEN Endstatus).
    Die Fristloeschung bleibt davon unberuehrt (sie laeuft nur bei Vertraegen,
    die nicht mehr in Gebrauch sind). Liefert die Zahl der stornierten Termine."""
    jetzt = now_iso()
    n = 0
    async for a in db.appointments.find(
            {"contract_id": contract_id, "dealer_id": user["dealer_id"],
             "status": {"$in": TERMIN_OFFEN_WERTE}},
            {"_id": 0, "id": 1, "driver_id": 1, "zuteilung": 1, "abgeschlossen_seit": 1}):
        aenderung = {"status": "storniert", "status_changed_at": jetzt,
                     "updated_at": jetzt, "storno_grund": "vertrag_geloescht"}
        if not a.get("abgeschlossen_seit"):
            aenderung["abgeschlossen_seit"] = jetzt
        res = await db.appointments.update_one(
            {"id": a["id"], "dealer_id": user["dealer_id"],
             "status": {"$in": TERMIN_OFFEN_WERTE}},
            {"$set": aenderung})
        if res.modified_count:
            n += 1
            await log_activity_sicher(
                user["dealer_id"], user["id"], "termin.storniert.vertrag_geloescht",
                ref=a["id"], meta={"contract_id": contract_id,
                                   "fahrer_zugesagt": a.get("zuteilung") == "angenommen"})
    return n


class VerkaeuferKorrekturIn(BaseModel):
    """Entscheidung Ahmad 22.09.2026 (Rollenpruefung RP-481): Verkaeuferdaten
    eines schon verschickten Vertrags korrigieren — neue Fassung, die alte
    bleibt im Archiv. Felder wie beim Anlegen (ContractIn)."""
    seller_name: str = Field(max_length=500)
    seller_address: Optional[str] = Field(default=None, max_length=500)
    seller_zip: Optional[str] = Field(default=None, max_length=20)
    seller_city: Optional[str] = Field(default=None, max_length=100)
    seller_phone: Optional[str] = Field(default=None, max_length=500)
    seller_email: Optional[str] = Field(default=None, max_length=500)
    id_document: Optional[str] = Field(default=None, max_length=500)

    @field_validator("seller_name")
    @classmethod
    def _verkaeufer_pflicht(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Bitte den Namen des Verkäufers angeben")
        return v


@router.put("/contracts/{contract_id}/verkaeufer")
async def verkaeufer_korrigieren(contract_id: str, body: VerkaeuferKorrekturIn,
                                 user=Depends(current_firma)):
    """Verkaeuferdaten korrigieren (Entscheidung Ahmad 22.09.2026, RP-481).

    Vorher gab es fuer einen Tippfehler im Namen oder eine falsche Anschrift
    in einem verschickten Vertrag keinen Weg ausser Loeschen (das nahm auch
    das Protokoll mit). Jetzt: neue Fassung ueber denselben Mechanismus wie
    beim verschobenen Termin (Archiv, Compare-and-Set, Fassungskennzeichen);
    der Sucher schickt sie danach ueber "Senden" — der Versand-Dialog nimmt
    von selbst die Korrektur-Vorlage, weil eine fruehere Fassung schon raus
    war. Der offene Termin zum Vertrag bekommt Name, Telefon, E-Mail und
    Abholadresse mit; ein Protokoll-Entwurf, der noch den ALTEN Namen traegt,
    verliert ihn und nimmt beim Abschluss den korrigierten (RP-082).
    Chef: alle Vertraege der Firma; Sucher: nur eigene (_vertrag_bereich).
    """
    bereich = _vertrag_bereich(user)
    doc = await db.generated_pdfs.find_one(
        {"id": contract_id, **bereich},
        {"_id": 0, "id": 1, "version": 1, "contract_data": 1, "seller_name": 1})
    if not doc:
        raise HTTPException(404, "Vertrag nicht gefunden")
    daten = body.model_dump()
    erg: dict = {}
    neu = await regenerate_contract_for_pickup(
        contract_id=contract_id, dealer_id=user["dealer_id"], user=user,
        verkaeufer=daten, grund="verkaeufer_korrigiert", ergebnis=erg)
    if not neu:
        grund = erg.get("grund")
        if grund == "keine_aenderung":
            return {"geaendert": False, "version": int(doc.get("version") or 1)}
        if grund == "cas_verloren":
            raise HTTPException(409, "Der Vertrag wurde gerade anderweitig geändert — "
                                     "bitte neu laden und noch einmal versuchen.")
        if grund == "pdf_fehler":
            raise HTTPException(500, "Die neue Fassung konnte nicht erzeugt werden.")
        raise HTTPException(404, "Vertrag nicht gefunden")
    frisch = await db.generated_pdfs.find_one(
        {"id": contract_id, **bereich}, {"_id": 0, "version": 1, "contract_data": 1})
    cd = (frisch or {}).get("contract_data") or {}
    alter_name = str((doc.get("contract_data") or {}).get("seller_name")
                     or doc.get("seller_name") or "").strip()
    neuer_name = str(cd.get("seller_name") or "").strip()
    # Offene Termine zu diesem Vertrag mitziehen (abgeschlossene bleiben als
    # Beleg, wie sie waren). Die Zusage des Fahrers wird NICHT zurueckgesetzt:
    # der Termin selbst (Zeit, Ort) aendert sich nicht.
    from routes.appointments import ABGESCHLOSSEN
    from routes.protocols import SELLER_NAME_GEAENDERT_AM
    adresse = " ".join(x for x in (
        str(cd.get("seller_address") or "").strip(),
        str(cd.get("seller_zip") or "").strip(),
        str(cd.get("seller_city") or "").strip()) if x)[:500]
    termin_set = {"seller_name": neuer_name,
                  "seller_phone": str(cd.get("seller_phone") or "").strip(),
                  "seller_email": str(cd.get("seller_email") or "").strip(),
                  "updated_at": now_iso()}
    if adresse:
        termin_set["pickup_address"] = adresse
    if neuer_name != alter_name:
        termin_set[SELLER_NAME_GEAENDERT_AM] = termin_set["updated_at"]
    offene: list = []
    try:
        offene = [a["id"] async for a in db.appointments.find(
            {"contract_id": contract_id, "dealer_id": user["dealer_id"],
             "status": {"$nin": list(ABGESCHLOSSEN)}}, {"_id": 0, "id": 1})]
        if offene:
            await db.appointments.update_many({"id": {"$in": offene}}, {"$set": termin_set})
            if neuer_name != alter_name and alter_name:
                await db.pickup_protocols.update_many(
                    {"appointment_id": {"$in": offene}, "dealer_id": user["dealer_id"],
                     "status": "entwurf", "superseded": {"$ne": True},
                     "seller_name": alter_name},
                    {"$unset": {"seller_name": ""}})
    except Exception:  # noqa: BLE001 — der Vertrag steht; Termin ist Beiwerk
        log.exception("Verkaeuferkorrektur %s: Termin nicht nachgezogen", contract_id)
    await log_activity_sicher(user["dealer_id"], user["id"], "vertrag.verkaeufer.korrigiert",
                              ref=contract_id,
                              meta={"version": (frisch or {}).get("version"),
                                    "name_geaendert": neuer_name != alter_name,
                                    "termine": len(offene)})
    return {"geaendert": True, "version": int((frisch or {}).get("version") or 0),
            "termine_aktualisiert": len(offene),
            # fuer den Versand-Dialog direkt danach (ohne Neuladen der Liste)
            "verkaeufer": {k: str(cd.get(k) or "") for k in VERKAEUFER_FELDER}}


@router.delete("/contracts/{contract_id}")
async def delete_contract(contract_id: str, user=Depends(current_firma)):
    # Berechtigungsmatrix (PR-Review 09/2026): Loeschen ist destruktiv —
    # der Chef darf alle Vertraege der Firma loeschen, ein Sucher NUR die
    # von ihm selbst erstellten. Existenz/Eigentum wird VOR der Loeschung
    # geprueft (404/403 bleiben wie bisher).
    vorhanden = await db.generated_pdfs.find_one(
        {"id": contract_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "user_id": 1, "contract_no": 1})
    if not vorhanden:
        raise HTTPException(404, "Vertrag nicht gefunden")
    # Pruefbericht 20.09.2026 (N2): nicht mitten im Versand loeschen — die
    # Mail waere sonst schon unterwegs, wenn der Vertrag verschwindet.
    if await _versand_laeuft(contract_id):
        raise HTTPException(409, VERSAND_LAEUFT_TEXT,
                            headers={"Retry-After": "5"})
    if user.get("role") == "sucher" and vorhanden.get("user_id") != user["id"]:
        raise HTTPException(403, "Sucher dürfen nur ihre eigenen Verträge "
                                 "löschen — fremde Verträge löscht der "
                                 "Händler-Hauptaccount")
    async def _vor_kaskade() -> Optional[str]:
        """Pruefbericht 20.09.2026 (R1-07): Diese Pruefungen laufen ERST NACH
        dem Grabstein (cleanup_service.vertrag_endgueltig_loeschen ruft sie
        auf). Vorher lagen sie davor, und im Fenster dazwischen konnte der
        Fahrer noch einen Entwurf beginnen, den die Kaskade sofort
        bereinigte — protocols._vertrag_nicht_in_loeschung sieht nur den
        Grabstein. Ein Text = Ablehnung (Grabstein kommt weg, 409)."""
        # Pruefung 14.09.2026 (F3/F4): Liegt zu einem Termin dieses Vertrags ein
        # Protokoll beim Chef, ist es freigegeben oder wird gerade unterschrieben,
        # bleibt der Vertrag — sonst verliert der laufende Abschluss seinen
        # Vertrag (und ein Scrub koennte mit dem Abschluss um Personendaten ringen).
        termin_ids = [a["id"] async for a in db.appointments.find(
            {"contract_id": contract_id, "dealer_id": user["dealer_id"]}, {"_id": 0, "id": 1})]
        # Befund 124 (16.09.2026): dieselbe Protokollmenge wie die Loeschkaskade —
        # auch Protokolle, die nur noch ueber ihr eigenes contract_id am Vertrag
        # haengen (Termin inzwischen umgehaengt oder geloest).
        protokolle_zum_vertrag = {"$or": [{"appointment_id": {"$in": termin_ids}},
                                          {"contract_id": contract_id}],
                                  "superseded": {"$ne": True}}
        if await db.pickup_protocols.count_documents(
                {**protokolle_zum_vertrag,
                 # Pruefung 14.09.2026 (B28): auch ein begonnener Entwurf zaehlt
                 "status": {"$in": ["entwurf", "zur_freigabe", "freigegeben",
                                    "wird_abgeschlossen"]}}, limit=1):
            return ("Zu diesem Vertrag läuft gerade ein Abholprotokoll "
                    "(Freigabe oder Unterschrift) — der Vertrag kann jetzt "
                    "nicht gelöscht werden.")
        # Runde 19 (16.09.2026, Vertraege Nr. 1): ein unterschriebenes Abholprotokoll
        # macht den Vertrag zum Beleg — der Sucher loescht ihn nicht mehr, nur der
        # Chef (mit Blick auf die Aufbewahrungspflicht).
        if user.get("role") == "sucher" and await db.pickup_protocols.count_documents(
                {**protokolle_zum_vertrag, "status": "final"}, limit=1):
            return ("Zu diesem Vertrag gibt es ein unterschriebenes Abhol-"
                    "protokoll — der Beleg bleibt. Löschen kann ihn nur der "
                    "Händler-Hauptaccount.")
        # Rollenpruefung 22.09.2026 (RP-415): Die Kaskade leerte am OFFENEN Termin
        # nur Verkaeuferdaten und Adresse — Status und Fahrer-Zusage blieben. Der
        # Fahrer sah eine offene Fahrt ohne Adresse, und der Sucher konnte sie
        # nicht mehr loeschen (409 bei angenommener Fahrt). Offene Termine des
        # Vertrags werden jetzt vorher storniert (mit Verlaufseintrag). Das
        # Loeschrecht des Chefs bleibt wie entschieden (21.09.2026).
        await _offene_termine_beim_loeschen_stornieren(user, contract_id)
        return None

    # Kaskade ueber EINE idempotente, wiederaufnehmbare Funktion (Go-Live-
    # Audit 09/2026): Grabstein am Vertrag, dann Versionen loeschen, Termin-
    # Verweise kappen, zuletzt der Vertrag. Bricht der Vorgang ab, fuehrt
    # der Aufraeumjob ihn zu Ende. Vorher wurde der Vertrag ZUERST geloescht
    # und die Kaskade konnte verwaiste Versionen/Termine hinterlassen.
    # Pruefung 14.09.2026 (Liste 6, Nr. 5): auch die manuelle Loeschung
    # entfernt Verkaeuferdaten aus Termin, Protokoll und Bericht — ein
    # geloeschter Vertrag hinterlaesst keine Personendaten.
    try:
        ok = await vertrag_endgueltig_loeschen(
            db, contract_id, scrub_pii=True, grund="manuell", audit=False,
            vor_kaskade=_vor_kaskade)
    except LoeschungAbgelehnt as exc:
        raise HTTPException(409, str(exc))
    if not ok:
        raise HTTPException(404, "Vertrag nicht gefunden")
    # Audit 13.09.2026 (#45): Der Vertrag ist bereits geloescht — ein 500
    # wuerde nur einen 404-Retry ausloesen, und die Spur, WER geloescht (und
    # damit Kaufvorgaenge storniert) hat, ginge verloren. Deshalb best
    # effort; scheitert der Eintrag, stehen Nutzer und Vertrag im Fehlerlog
    # und als Betriebsalarm (log_activity_sicher allein nennt den Nutzer nicht).
    if not await log_activity_sicher(user["dealer_id"], user["id"],
                                     "vertrag.geloescht.manuell", ref=contract_id,
                                     meta={"contract_no": vorhanden.get("contract_no")}):
        log.error("Vertrag %s (Nr. %s) wurde von %s geloescht — Audit-Eintrag fehlt",
                  contract_id, vorhanden.get("contract_no"), user["id"])
        from betrieb import alarm   # lokal: kein Importzyklus
        await alarm(db, "audit_fehlt", ref=contract_id,
                    aktion="vertrag.geloescht.manuell", user_id=user["id"],
                    dealer_id=user["dealer_id"],
                    contract_no=vorhanden.get("contract_no") or "")
    return {"ok": True}


# Pruefung 21.09.2026 (pdf): Kennzeichnung der vor Ort gefundenen Schaeden im
# Schadenstext der neuen Vertragsfassung.
NACH_ABHOLUNG_SCHADEN_ZUSATZ = "(bei Abholung festgestellt)"


def _schadenstext_nach_abholung(damages_text: Optional[str],
                                schaeden_neu: list) -> Optional[str]:
    """Haengt die bei der Abholung aufgenommenen Schaeden an den Schadenstext an.

    Pruefung 21.09.2026 (pdf): pdf_service druckt NUR damages_text, sobald er
    gesetzt ist (die Liste `damages` ist nur der Rueckfall). Jeder Vertrag mit
    Skizzen-Schaeden hat ihn (DamageSelector schreibt beides) — die vor Ort
    gefundenen Schaeden standen deshalb nur in `damages`, nicht in der neuen
    Fassung, die der Verkaeufer als aktuellen Stand bekommt.

    Zeile je Schaden: "• {Art}: {Bauteil} (bei Abholung festgestellt)", mit
    denselben Rueckfaellen wie pdf_service beim Drucken der Liste. Leerer Text
    bleibt leer — dann druckt pdf_service die Liste, und die enthaelt die
    neuen Schaeden schon. Idempotent: eine Zeile, die schon im Text steht,
    kommt nicht ein zweites Mal dazu (Nachholer, erneute Neuerzeugung)."""
    bisher = damages_text or ""
    if not bisher.strip() or not schaeden_neu:
        return damages_text
    vorhanden = {z.strip() for z in bisher.split("\n") if z.strip()}
    zeilen = []
    for d in schaeden_neu:
        if isinstance(d, dict):
            art = str(d.get("type_label") or d.get("type_key")
                      or d.get("label") or d.get("type") or "Schaden")
            zone = str(d.get("zone") or d.get("part_label") or d.get("part") or "")
            kern = f"{art}: {zone}" if zone.strip() else art
        else:
            kern = str(d or "")
        # Zeilenumbrueche im Eintrag wuerden die Zeile im PDF zerreissen und
        # den Abgleich auf "steht schon da" aushebeln.
        kern = " ".join(kern.split())
        if not kern:
            continue
        zeile = f"• {kern} {NACH_ABHOLUNG_SCHADEN_ZUSATZ}"
        if zeile in vorhanden:
            continue
        vorhanden.add(zeile)
        zeilen.append(zeile)
    if not zeilen:
        return damages_text
    return bisher.rstrip() + "\n" + "\n".join(zeilen)


#: Rollenpruefung 22.09.2026 (RP-494): Kennzeichnung der Fassung im PDF.
FASSUNGS_FELDER = ("fassung", "fassung_erstellt_am", "ersetzt_fassung_am")
#: RP-479: Diese Angaben kommen beim Neuaufbau aus dem Stand vor der Abholung
#: trotzdem aus der AKTUELLEN Fassung — sie aendert keine Abholung: Termin,
#: eingefrorene Kaeuferdaten (Runde 25), Logo und Fassungsangaben.
#: Entscheidung Ahmad 22.09.2026: Verkaeuferdaten eines verschickten Vertrags
#: sind korrigierbar (neue Fassung). Beim Neuaufbau aus dem Stand vor der
#: Abholung kommen sie aus der AKTUELLEN Fassung, sonst ginge eine Korrektur
#: bei der naechsten Protokoll-Neuerzeugung wieder verloren.
VERKAEUFER_FELDER = ("seller_name", "seller_address", "seller_zip", "seller_city",
                     "seller_phone", "seller_email", "id_document")
_AUS_AKTUELLER_FASSUNG = ("pickup_date", "pickup_time", "empfang_datum", "logo_key",
                          *KAEUFER_FELDER, *FASSUNGS_FELDER, *VERKAEUFER_FELDER)
#: Rollenprüfung 22.09.2026 (Review): die Vertragsfelder, die eine Protokoll-
#: Korrektur setzt (protokoll_vergleich.vertrags_korrekturen / hu_korrektur).
#: protocols.protokoll_korrekturen ermittelt sie gegen die AKTUELLE Fassung —
#: beim Neuaufbau aus dem Stand vor der Abholung kommen sie deshalb ebenfalls
#: aus der aktuellen Fassung (siehe regenerate_contract_for_pickup).
from protokoll_vergleich import KORREKTUR_FELDER as _PV_KORREKTUR_FELDER  # noqa: E402
_VOR_ORT_FELDER = tuple(dict.fromkeys((*_PV_KORREKTUR_FELDER.values(), "hu_valid")))


def _inhalt(vertragsdaten: dict) -> dict:
    """Vertragsinhalt ohne die Fassungsangaben (fuer "hat sich etwas geaendert?")."""
    return {k: w for k, w in (vertragsdaten or {}).items() if k not in FASSUNGS_FELDER}


def _fassung_kennzeichnen(contract_dict: dict, doc: dict, neue_version: int) -> None:
    """Rollenpruefung 22.09.2026 (RP-494): Eine neue Fassung trug dieselbe
    Vertragsnummer, "erstellt am <heute>" und z. B. einen anderen Preis —
    ohne jeden Hinweis, dass sie eine fruehere ersetzt. Jetzt stehen Nummer
    der Fassung und Datum der ersetzten Fassung im Vertrag (pdf_service
    druckt sie ab Fassung 2 im Kopf und in der Fusszeile)."""
    vorher = str((doc.get("contract_data") or {}).get("fassung_erstellt_am")
                 or doc.get("created_at") or "")
    contract_dict["fassung"] = int(neue_version)
    contract_dict["ersetzt_fassung_am"] = vorher[:10]
    contract_dict["fassung_erstellt_am"] = now_iso()


async def regenerate_contract_for_pickup(
    *, contract_id: str, dealer_id: str, user: dict,
    pickup_date: Optional[str] = None, pickup_time: Optional[str] = None,
    leeren_erlaubt: bool = False,
    neuer_preis: Optional[float] = None, sondervereinbarung: Optional[str] = None,
    korrekturen: Optional[Dict[str, Any]] = None,
    neue_schaeden: Optional[list] = None,
    verkaeufer: Optional[Dict[str, Any]] = None,
    grund: str = "abholtermin_geaendert", protokoll_id: Optional[str] = None,
    ergebnis: Optional[dict] = None,
) -> bool:
    """Erzeugt das Kaufvertrags-PDF mit GEAENDERTEM Abholtermin neu.

    leeren_erlaubt (Nachpruefung Runde 14, Befund 84): Der Terminkalender
    unterscheidet "nicht gesendet" (None) von "bewusst geleert" (""). Nur
    mit diesem Schalter loescht ein leerer Wert Datum/Uhrzeit auch im
    Vertrag — andere Aufrufer behalten die alte Lesart "leer = unveraendert".

    Wird aufgerufen, wenn im Terminkalender das Abholdatum verschoben wird —
    der Vertrag ist eine gespeicherte Datei und wuerde sonst das alte Datum
    zeigen. Der bisherige Termin wird in `pickup_history` mitgeschrieben,
    damit nachvollziehbar bleibt, was wann geaendert wurde.
    Rueckgabe: True, wenn das PDF neu erzeugt wurde.

    ergebnis (Pruefbericht 20.09.2026, V-25): optionales dict, in das der
    Grund eines False geschrieben wird ("kein_anlass", "nicht_gefunden",
    "keine_aenderung", "pdf_fehler", "cas_verloren"). Der Nachholer nach der
    Abholung muss "schon eingearbeitet" von "gescheitert" unterscheiden —
    vorher loeste jeder Doppeltipp nach einem gelungenen Abschluss einen
    Alarm aus, der nie wieder zuging.
    """
    def _grund(wert: str) -> bool:
        if ergebnis is not None:
            ergebnis["grund"] = wert
        return False

    # Wunsch Ahmad 14.09.2026: nach der Abholung wird der Vertrag mit dem vor
    # Ort vereinbarten Preis und der Sondervereinbarung neu erstellt (neue
    # Fassung, die alte bleibt im Archiv) — der Kunde bekommt den aktuellen Stand.
    preis_aenderung = neuer_preis is not None or bool((sondervereinbarung or "").strip())
    # 19.09.2026 (Wunsch Ahmad): auch die vor Ort korrigierten Fahrzeugdaten
    # und neu aufgenommene Schaeden loesen eine neue Fassung aus.
    roh_korrekturen = dict(korrekturen or {})
    korrekturen = {k: w for k, w in roh_korrekturen.items() if w not in (None, "")}
    # Rollenpruefung 22.09.2026 (RP-488): "keine HU" vor Ort liefert
    # protokoll_vergleich.hu_korrektur als {"hu_until": "", "hu_valid": "Nein"}.
    # Der Filter oben warf das leere Datum weg — das alte "gueltig bis" blieb im
    # Vertrag stehen. Nur in DIESEM Fall (HU ausdruecklich "Nein") leert ein ""
    # das Datum; sonst bleibt "leer = keine Angabe".
    if roh_korrekturen.get("hu_until") == "" \
            and str(roh_korrekturen.get("hu_valid") or "").strip() == "Nein":
        korrekturen["hu_until"] = ""
    neue_schaeden = [d for d in (neue_schaeden or []) if d]
    # Entscheidung Ahmad 22.09.2026: korrigierte Verkaeuferdaten (nur die
    # bekannten Felder, getrimmt; None = Feld nicht angefasst).
    verkaeufer = {k: str(w).strip() for k, w in (verkaeufer or {}).items()
                  if k in VERKAEUFER_FELDER and w is not None}
    # Rollenpruefung 22.09.2026 (RP-479): Nach der Abholung wird die neue
    # Fassung aus dem Stand VOR der Abholung aufgebaut (siehe unten) — eine
    # korrigierte Protokollversion OHNE Preis/Aenderungen darf deshalb nicht
    # schon hier aussteigen: sie nimmt die Aenderungen der vorigen Version
    # zurueck. Ob es etwas zurueckzunehmen gibt, steht erst am Vertrag.
    abholung = grund == "abholung_abgeschlossen"
    ohne_anlass = (pickup_date is None and pickup_time is None
                   and not preis_aenderung and not korrekturen and not neue_schaeden
                   and not verkaeufer)
    if not contract_id or (ohne_anlass and not abholung):
        return _grund("kein_anlass")
    # Pruefbericht 20.09.2026 (N2): keine neue Fassung mitten im Versand.
    # Sonst haelt der Verkaeufer die alte Fassung in der Hand, waehrend die
    # Anwendung schon die neue fuehrt. 409 statt still weiterzumachen — der
    # Aufrufer (Termin verschieben, Protokoll abschliessen) meldet das
    # weiter, und der Nutzer wiederholt es in ein paar Sekunden.
    if await _versand_laeuft(contract_id):
        raise HTTPException(409, VERSAND_LAEUFT_TEXT,
                            headers={"Retry-After": "5"})
    # Runde 10: derselbe Bereich wie beim Lesen — ein Sucher erzeugt kein
    # PDF fuer den Vertrag eines Kollegen, auch nicht ueber den Termin.
    # Runde 17 (Nr. 373): auch ohne user den Grabstein-Filter (Nr. 348).
    bereich = (_vertrag_bereich(user) if user
               else {"dealer_id": dealer_id, "loeschung.status": {"$ne": "laeuft"}})
    doc = await db.generated_pdfs.find_one({"id": contract_id, **bereich}, {"_id": 0})
    if not doc:
        return _grund("nicht_gefunden")

    alt_datum = doc.get("pickup_date")
    alt_zeit = doc.get("pickup_time")
    # Leere Werte bedeuten "nicht angegeben" — sie duerfen einen
    # vorhandenen Termin NICHT loeschen (sonst wuerde z.B. das Speichern
    # ohne Uhrzeit die Uhrzeit im Vertrag entfernen).
    def _neu(wert, alt):
        if wert is None:
            return alt
        if (wert or "").strip():
            return wert.strip()
        return "" if leeren_erlaubt else alt

    neu_datum = _neu(pickup_date, alt_datum)
    neu_zeit = _neu(pickup_time, alt_zeit)
    cd_aktuell = dict(doc.get("contract_data") or {})
    # Rollenpruefung 22.09.2026 (RP-479): Nach der Abholung arbeitete die
    # Neuerzeugung nur ADDITIV — Schaeden und Sondervereinbarung wurden
    # angehaengt, der Preis nur gesetzt, wenn einer kam. Eine korrigierte
    # Protokollversion konnte deshalb nichts zuruecknehmen (ein
    # zurueckgesetzter Preis blieb im Kaufvertrag stehen). Jetzt: bei der
    # ERSTEN Neuerzeugung nach einer Abholung wird der Stand davor
    # festgehalten (vertrag_vor_abholung); jede weitere Neuerzeugung fuer eine
    # Abholung geht von diesem Stand aus und legt nur das AKTUELLE Protokoll
    # darueber. Terminangaben (Datum, Uhrzeit, Empfangsdatum) kommen aus der
    # aktuellen Fassung.
    basis = None
    if abholung:
        basis = doc.get("vertrag_vor_abholung")
        if not isinstance(basis, dict):
            basis = None
        if basis is None and doc.get("nach_abholung_protokoll_id"):
            # Vertraege, die VOR dieser Aenderung schon einmal nach einer
            # Abholung neu erzeugt wurden: der Stand davor liegt im Archiv
            # (die erste Fassung mit grund "abholung_abgeschlossen").
            frueher = await db.generated_pdf_versions.find_one(
                {"contract_id": contract_id, "dealer_id": dealer_id,
                 "grund": "abholung_abgeschlossen"},
                {"_id": 0, "contract_data": 1}, sort=[("version", 1)])
            if isinstance((frueher or {}).get("contract_data"), dict):
                basis = frueher["contract_data"]
        if basis is None and ohne_anlass:
            # Erste Abholung ohne jede Aenderung: nichts neu zu erzeugen.
            return _grund("kein_anlass")
    if basis is not None:
        contract_dict = dict(basis)
        for feld in _AUS_AKTUELLER_FASSUNG:
            if feld in cd_aktuell:
                contract_dict[feld] = cd_aktuell[feld]
        # Rollenprüfung 22.09.2026 (Review): Die Korrekturen einer Protokoll-
        # version (protocols.protokoll_korrekturen) werden gegen die AKTUELLE
        # Fassung ermittelt — die zeigt die Fahrer-App auch als "laut Vertrag".
        # Bestaetigt eine Korrektur-Version einen schon eingearbeiteten Wert
        # (v1 "unfallfrei: Nein", v2 als Kopie unveraendert), fehlt er in ihren
        # Korrekturen; aus dem Stand VOR der Abholung aufgebaut, stand danach
        # wieder "unfallfrei: Ja" im Kaufvertrag, obwohl das unterschriebene
        # Protokoll "Nein" sagt. Die Fahrzeugangaben kommen deshalb aus der
        # aktuellen Fassung, die Korrekturen liegen darueber — eine Ruecknahme
        # kommt als Korrektur auf den alten Wert. Preis, Sondervereinbarung und
        # Schaeden bauen weiter auf dem Stand vor der Abholung auf (RP-479).
        for feld in _VOR_ORT_FELDER:
            if feld in cd_aktuell:
                contract_dict[feld] = cd_aktuell[feld]
            else:
                contract_dict.pop(feld, None)
    else:
        contract_dict = dict(cd_aktuell)
    alt_preis = contract_dict.get("purchase_price")
    sonder = (sondervereinbarung or "").strip()
    preis_neu = neuer_preis is not None and (
        alt_preis is None or abs(float(neuer_preis) - float(alt_preis or 0)) > 0.004)
    sonder_neu = bool(sonder) and sonder not in (contract_dict.get("additional_terms") or "")
    # Was der Fahrer vor Ort anders vorgefunden hat, ersetzt die alten Angaben
    # im Vertrag (Marke, Modell, EZ, FIN, Farbe, Kraftstoff, HU, KM, Halter,
    # gewerblich, unfallfrei). Gleiche Werte aendern nichts.
    korrigiert = {k: w for k, w in korrekturen.items()
                  if str(contract_dict.get(k) or "").strip() != str(w).strip()}
    schaeden_alt = list(contract_dict.get("damages") or [])
    schaeden_neu = [d for d in neue_schaeden if d not in schaeden_alt]
    verk_korrigiert = {k: w for k, w in verkaeufer.items()
                       if str(contract_dict.get(k) or "").strip() != w}
    # gilt, wenn der naechste Block aussteigt (der Vertrag zeigt schon alles)
    _grund("keine_aenderung")
    if basis is None and (neu_datum or "") == (alt_datum or "") \
            and (neu_zeit or "") == (alt_zeit or "") \
            and not preis_neu and not sonder_neu and not korrigiert and not schaeden_neu \
            and not verk_korrigiert:
        return False
    if korrigiert:
        contract_dict.update(korrigiert)
    if verk_korrigiert:
        contract_dict.update(verk_korrigiert)
    # Hinweis im Archiv ("was ist anders?"): wie der Preis (preis_vorher) gegen
    # den Vertrag VOR der Abholung — auch wenn die Werte schon aus der
    # vorigen Protokollversion stammen (Rollenprüfung 22.09.2026, Review).
    felder_geaendert = sorted(korrigiert.keys())
    if basis is not None:
        felder_geaendert = sorted(
            f for f in _VOR_ORT_FELDER
            if str(contract_dict.get(f) or "").strip() != str(basis.get(f) or "").strip())
    if schaeden_neu:
        # Die vor Ort aufgenommenen Schaeden kommen zu den im Vertrag
        # dokumentierten dazu — die alten waren bekannt und bleiben stehen.
        contract_dict["damages"] = schaeden_alt + schaeden_neu
        # Pruefung 21.09.2026 (pdf): steht ein Schadenstext im Vertrag, druckt
        # das PDF nur ihn — die neuen Schaeden muessen also auch dort hinein.
        schadenstext = _schadenstext_nach_abholung(
            contract_dict.get("damages_text"), schaeden_neu)
        if schadenstext != contract_dict.get("damages_text"):
            contract_dict["damages_text"] = schadenstext
    contract_dict["pickup_date"] = neu_datum or ""
    contract_dict["pickup_time"] = neu_zeit or ""
    if preis_neu:
        contract_dict["purchase_price"] = float(neuer_preis)
        # Befund 46 (16.09.2026): der Preis, fuer den man zum Auto gefahren
        # ist, bleibt der ERSTE — eine zweite Nachverhandlung (18.000 -> 17.000)
        # ueberschrieb ihn vorher mit dem Zwischenstand, und die Auto-Daten
        # hielten 18.000 fuer den urspruenglichen Einkaufspreis.
        if contract_dict.get("preis_vor_abholung") is None:
            contract_dict["preis_vor_abholung"] = alt_preis
    if sonder_neu:
        bisher = (contract_dict.get("additional_terms") or "").rstrip()
        contract_dict["additional_terms"] = (bisher + "\n\n" if bisher else "") + \
            "Sondervereinbarung bei der Abholung: " + sonder
    # Runde 22 (11.09.2026): Das Empfangsdatum (Uebergabe, "Datum und Ort")
    # folgt im Formular dem Abholdatum. Stand es noch auf dem alten
    # Abholtag, wandert es mit dem verschobenen Termin mit — ein von Hand
    # anders gesetztes Datum bleibt.
    if alt_datum and (contract_dict.get("empfang_datum") or "") == alt_datum:
        contract_dict["empfang_datum"] = neu_datum or ""
    if basis is not None and _inhalt(contract_dict) == _inhalt(cd_aktuell):
        # RP-479: aus dem Stand vor der Abholung neu aufgebaut, und es kommt
        # genau die aktuelle Fassung heraus — nichts zu tun.
        return False

    v = await db.vehicles.find_one(
        {"id": doc.get("vehicle_id"), "dealer_id": dealer_id}, {"_id": 0}) or {}
    vehicle = dict(v.get("data") or {})
    # Gegenpruefung Runde 25 (12.09.2026): Kaeufer ist der ERSTELLER des
    # Vertrags (sonst der Termin-Ersteller, sonst die Firma) — NICHT, wer
    # gerade den Termin verschiebt. Vorher fror der Chef beim Verschieben
    # seine eigenen Firmendaten im Vertrag eines Suchers ein; ueber
    # _apply_contract_overrides galt das dann auch im Abholprotokoll.
    from auftraggeber import kaeufer_basis
    termin = await db.appointments.find_one(
        {"contract_id": contract_id, "dealer_id": dealer_id},
        {"_id": 0, "created_by": 1}) or {}
    dealer = await kaeufer_basis(
        dealer_id=dealer_id,
        user_ids=(doc.get("user_id"), termin.get("created_by"))) or {}
    vehicle, dealer = _apply_contract_overrides(
        contract=contract_dict, vehicle=vehicle, dealer=dealer)
    # Altvertrag ohne gespeicherte Kaeuferdaten: den jetzt verwendeten Stand
    # festhalten, damit alle weiteren Fassungen identisch bleiben (Runde 25).
    kaeufer_einfrieren(contract_dict, dealer)
    # Rollenpruefung 22.09.2026 (RP-452): nur das beim Vertrag festgehaltene Logo.
    dealer = await _logo_einsetzen(dealer, contract_dict)
    # Rollenpruefung 22.09.2026 (RP-494): "Fassung N — ersetzt Fassung N-1 vom …"
    _fassung_kennzeichnen(contract_dict, doc, int(doc.get("version") or 1) + 1)
    # Rollenpruefung 22.09.2026 (RP-432): Korrigiert der Fahrer Marke/Modell,
    # standen sie nur in contract_data — Mail-Betreff, Archivliste, Suche und
    # Dateiname nannten weiter das alte Fahrzeug (make/model oben am Vertrag).
    kopf: Dict[str, Any] = {}
    # Entscheidung Ahmad 22.09.2026: Name, Telefon und E-Mail des Verkaeufers
    # stehen auch oben am Vertrag (Archivliste, Suche, Versand-Dialog).
    for feld in ("seller_name", "seller_phone", "seller_email"):
        if feld in verk_korrigiert:
            kopf[feld] = verk_korrigiert[feld]
    if any((contract_dict.get(k) or "") != (cd_aktuell.get(k) or "")
           for k in ("vehicle_make", "vehicle_model")):
        marke = str(contract_dict.get("vehicle_make") or doc.get("make") or "").strip()
        modell = str(contract_dict.get("vehicle_model") or doc.get("model") or "").strip()
        kopf = {"make": marke, "model": modell,
                "filename": _vertrag_dateiname({"make_label": marke, "model_label": modell})}
    # RP-479: der Preis folgt dem neu aufgebauten Stand — auch zurueck.
    preis_danach = contract_dict.get("purchase_price")
    if preis_danach is not None and doc.get("purchase_price") is not None:
        try:
            if abs(float(preis_danach) - float(doc.get("purchase_price"))) > 0.004:
                kopf["purchase_price"] = float(preis_danach)
        except (TypeError, ValueError):
            pass
    elif preis_neu:
        kopf["purchase_price"] = float(neuer_preis)
    if abholung and not isinstance(doc.get("vertrag_vor_abholung"), dict):
        # RP-479: Stand vor der (ersten) Abholung festhalten — Grundlage jeder
        # weiteren Neuerzeugung fuer eine Abholung.
        kopf["vertrag_vor_abholung"] = basis if basis is not None else cd_aktuell

    # Beschluss Ahmad 09.09.2026: Texte aus den Einstellungen gelten NUR fuer
    # neue Vertraege. Der bei der Erstellung festgehaltene Text bleibt; ein
    # Altvertrag ohne Text bekommt auch bei der Neuerzeugung KEINE heutigen
    # Bedingungen — nur den Nachtraeglich-Hinweis im PDF, contract_data
    # bleibt ohne Text.
    gespeichert = (contract_dict.get("digital_vertragstext") or "").strip()
    pdf_contract = dict(contract_dict)
    if not gespeichert:
        pdf_contract["digital_vertragstext"] = DIGITAL_NACHTRAEGLICH
    try:
        pdf_bytes, pdf_digital = await asyncio.to_thread(
            _pdfs_erzeugen,
            dealer=dealer, vehicle=vehicle, contract=pdf_contract,
        )
    except Exception:
        log.exception("Kaufvertrag konnte mit neuem Abholtermin nicht neu "
                      "erzeugt werden (contract=%s)", contract_id)
        # Pruefbericht 20.09.2026 (P-16): nicht nur ins Log — der Betrieb
        # sieht den Alarm (alle Aufrufer: Termin, Abholung, Verkaeufer-
        # Korrektur, Nachholer); der Aufrufer meldet "pdf_fehler" weiter.
        import betrieb as _betrieb
        await _betrieb.alarm(db, "vertrag_neuerzeugung_fehlgeschlagen", ref=contract_id,
                             dealer_id=dealer_id or "", grund=grund)
        _grund("pdf_fehler")
        return False

    # BEWEISSICHERUNG: Die bisherige PDF-Fassung wird NICHT ueberschrieben,
    # sondern als eigene Version archiviert. So bleibt belegbar, welcher
    # Vertragstext (mit welchem Abholtermin) zu jedem Zeitpunkt galt.
    alte_version = int(doc.get("version") or 1)
    archiv_id = str(uuid.uuid4())
    # Pruefbericht 20.09.2026 (V-27): Archiv per Upsert statt insert_one. Der
    # Unique-Index (contract_id, version) verhindert zwar doppelte Fassungen —
    # starb aber ein frueherer Lauf zwischen Archiv und Compare-and-Set, warf
    # JEDE spaetere Neuerzeugung DuplicateKeyError, und der Vertrag blieb fuer
    # immer auf der alten Fassung (ebenso der Verlierer zweier paralleler
    # Neuerzeugungen: 500 statt sauber False). Jetzt: vorhandene Archivfassung
    # derselben Version bleibt stehen (sie traegt denselben Stand), und beim
    # verlorenen CAS wird nur eine SELBST angelegte Fassung wieder entfernt.
    archiv_angelegt = False
    try:
        archiv_res = await db.generated_pdf_versions.update_one(
            {"contract_id": contract_id, "version": alte_version},
            {"$setOnInsert": {
                "id": archiv_id,
                "contract_id": contract_id,
                "dealer_id": dealer_id,
                "version": alte_version,
                "pdf_b64": doc.get("pdf_b64"),
                "pdf_digital_b64": doc.get("pdf_digital_b64"),
                "contract_data": doc.get("contract_data"),
                "pickup_date": alt_datum,
                "pickup_time": alt_zeit,
                "filename": doc.get("filename"),
                "archived_at": now_iso(),
                "archived_by": user.get("id"),
                "grund": grund,
            }},
            upsert=True)
        archiv_angelegt = archiv_res.upserted_id is not None
    except DuplicateKeyError:
        # paralleles Upsert derselben Fassung — die andere Anlage gilt
        archiv_angelegt = False

    # Runde 17 (Nr. 373): Compare-and-Swap auf die GELESENE Version — zwei
    # gleichzeitige Verschiebungen (oder eine parallele Loeschung) schrieben
    # vorher beide "Version N+1" ueber dieselbe Fassung, und eine der beiden
    # archivierten Vorversionen wiederholte die andere. Altvertraege ohne
    # Feld: `None` trifft fehlend UND null (so wurde oben auch gelesen: 1).
    # Verliert dieser Aufruf, wird die eben archivierte Fassung wieder
    # entfernt und False geliefert (der Termin bleibt korrekt gespeichert,
    # der Gewinner hat das PDF bereits neu erzeugt).
    res = await db.generated_pdfs.update_one(
        {"id": contract_id, "dealer_id": dealer_id,
         "version": doc.get("version"),
         "loeschung.status": {"$ne": "laeuft"},
         # V-26: kein Versand, der seit der Vorpruefung begonnen hat
         **_kein_laufender_versand()},
        {"$set": {
            "pdf_b64": base64.b64encode(pdf_bytes).decode(),
            "pdf_digital_b64": base64.b64encode(pdf_digital).decode(),
            "pdf_digital_nachtraeglich": not gespeichert,
            "contract_data": contract_dict,
            "pickup_date": neu_datum,
            "pickup_time": neu_zeit,
            "version": alte_version + 1,
            # Runde 19 (Nr. 45): Merker — scheitert die Nachfuehrung der Auto-Daten
            # unten, holt auto_daten_reparieren sie nach (vorher nur ein Log).
            "auto_daten_nachfuehrung_offen": True,
            "updated_at": now_iso(),
            # Runde 16 (15.09.2026): eine neue Fassung ist noch NICHT versendet.
            **({"status": "neu erstellt"} if doc.get("status") in ("versendet", "versand_vorbereitet") else {}),
            # purchase_price (RP-479: auch zurueck), make/model/filename
            # (RP-432), vertrag_vor_abholung (RP-479)
            **kopf,
            # 19.09.2026: Woran erkennt die Oberflaeche, dass sie den neuen
            # Vertrag zum Senden anbieten soll — und was sich geaendert hat?
            # RP-479: auch, wenn eine korrigierte Protokollversion Aenderungen
            # der vorigen zuruecknimmt (basis gesetzt) — der Verkaeufer hat
            # dann eine Fassung, die nicht mehr gilt.
            **({"nach_abholung_aktualisiert_am": now_iso(),
                "nach_abholung_protokoll_id": protokoll_id,
                "nach_abholung_aenderungen": {
                    "preis": float(neuer_preis) if preis_neu else None,
                    "preis_vorher": alt_preis if preis_neu else None,
                    "sondervereinbarung": bool(sonder_neu),
                    "felder": felder_geaendert,
                    "neue_schaeden": len(schaeden_neu),
                    # Entscheidung Ahmad 22.09.2026 (Ausweisnummer): vom
                    # Protokoll beigesteuerte Verkaeuferfelder (id_document).
                    "verkaeufer_felder": sorted(verk_korrigiert),
                    "nach_protokoll_korrektur": basis is not None},
                "nach_abholung_versand_offen": True}
               if abholung
               and (preis_neu or sonder_neu or korrigiert or schaeden_neu
                    or verk_korrigiert or basis is not None) else {}),
        },
         # Runde 17 (Nr. 321): Historie gedeckelt — die juengsten 100
         # Verschiebungen bleiben, das Dokument waechst nicht unbegrenzt.
         "$push": {"pickup_history": {"$each": [{
             "von_datum": alt_datum, "von_zeit": alt_zeit,
             "auf_datum": neu_datum, "auf_zeit": neu_zeit,
             "geaendert_von": user.get("id"), "geaendert_am": now_iso(),
             "version_vorher": alte_version,
         }], "$slice": -PICKUP_HISTORY_MAX}}},
    )
    if res.modified_count == 0:
        if archiv_angelegt:
            await db.generated_pdf_versions.delete_one({"id": archiv_id})
        if await _versand_laeuft(contract_id):
            # V-26: der Versand hat zwischen Vorpruefung und Schreiben begonnen
            raise HTTPException(409, VERSAND_LAEUFT_TEXT,
                                headers={"Retry-After": "5"})
        log.warning("Kaufvertrag %s: Neuerzeugung verworfen — Version %s wurde "
                    "zwischenzeitlich geaendert oder der Vertrag wird geloescht",
                    contract_id, doc.get("version"))
        _grund("cas_verloren")
        return False
    # Vertragskorrektur innerhalb der Frist: den BESTEHENDEN Auto-Datensatz
    # aktualisieren (nie ein zweiter); Altvertraege ohne id bekommen ihn
    # hier nachgetragen.
    # Audit 13.09.2026 (#44): Der Vertrag steht bereits (CAS getroffen,
    # Version N+1, Archivfassung N) — Nachfuehrung und Audit sind Beiwerk.
    # Vorher schlug eine Exception hier (z.B. DB-Aussetzer beim Failover)
    # bis PUT /appointments durch: 500, obwohl Termin und Vertrag korrekt
    # gespeichert waren. Fehlende Auto-Datensaetze traegt auto_daten_reparieren
    # nach; das Abholdatum steht nicht in den Auto-Daten.
    try:
        await auto_daten.nachfuehren(db, {**doc, "contract_data": contract_dict})
    except Exception:  # noqa: BLE001
        log.exception("Auto-Daten nach Neuerzeugung von %s nicht nachgefuehrt — "
                      "der Aufraeumjob holt es ueber den Merker nach", contract_id)
    await log_activity_sicher(dealer_id, user.get("id", ""), "vertrag.abholtermin.geaendert",
                              ref=contract_id,
                              meta={"von": alt_datum, "auf": neu_datum})
    if ergebnis is not None:
        ergebnis["grund"] = "neu"
    return True
