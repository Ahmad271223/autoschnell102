"""Dealer endpoints: settings GET/PUT, active-profile, subscription info/cancel."""
import math
import os
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from deps import current_user, db, get_subscription_status, now_iso, current_firma
from mobile_service import DEFAULT_RULES, DEFAULT_EXPORT_RULES

router = APIRouter()


# ---------- Models ----------
class DealerProfile(BaseModel):
    company_name: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    whatsapp_number: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    zip_code: Optional[str] = None
    city: Optional[str] = None
    logo_url: Optional[str] = None
    opening_hours: Optional[str] = None

    # Profilfelder landen im Vertrags-PDF (enge Tabellenzellen). Cap 500.
    @field_validator("*")
    @classmethod
    def _cap(cls, v, info):
        if isinstance(v, str) and len(v) > 500:
            # Pruefbericht 20.09.2026 (U-135): mit Feldnamen — vorher wusste
            # niemand, welches der zehn Felder zu lang war.
            raise ValueError(f"Profilfeld '{info.field_name}' zu lang (max. 500 Zeichen)")
        return v


class DealerSettingsIn(BaseModel):
    profile: Optional[DealerProfile] = None
    comparison_rules: Optional[dict] = None
    export_rules: Optional[dict] = None
    active_profile: Optional[str] = None  # "inland" | "export"
    email_subject: Optional[str] = None
    email_template: Optional[str] = None
    whatsapp_template: Optional[str] = None
    default_terms: Optional[str] = None             # AGB (always appended to PDF)
    default_special_agreements: Optional[str] = None  # Standard-Besondere-Vereinbarungen
    # Vorlage Ahmad 20.09.2026: drei Mails, die der Sucher NACHTRAEGLICH von
    # Hand verschickt — erneuter Versand nach einer Korrektur, der Hinweis
    # nach dem Kaufabschluss (per Mail und per WhatsApp) und die
    # Bahnverbindung fuer die Abholung. Alle mit denselben Platzhaltern.
    email_subject_korrektur: Optional[str] = None
    email_template_korrektur: Optional[str] = None
    email_subject_nach_kauf: Optional[str] = None
    email_template_nach_kauf: Optional[str] = None
    whatsapp_template_nach_kauf: Optional[str] = None
    email_subject_bahn: Optional[str] = None
    email_template_bahn: Optional[str] = None
    # Wunsch Ahmad 20.09.2026: Unser Standardsatz fuer die Besonderen
    # Vereinbarungen (Übergabe/Kundennummer, mit Platzhaltern) laesst sich
    # ein- und ausschalten. Der eigene Text der Firma steht unabhaengig
    # davon in default_special_agreements und kommt im Vertrag darunter.
    sondervereinbarung_standard_aktiv: Optional[bool] = None
    # Wunsch Ahmad 24.09.2026: Übergabe & Empfangsbestätigung im gedruckten
    # Vertrag an/aus (Standard an); im Erstellen-Dialog nicht mehr abgefragt.
    empfang_drucken: Optional[bool] = None
    # Text unter "Unterschriften" in der DIGITALEN Ausfertigung (Versand per
    # E-Mail/WhatsApp, ohne Unterschriftslinien). Leer = Standardtext.
    digital_vertragstext: Optional[str] = None

    # DoS-Schutz: default_terms/default_special_agreements werden in JEDES
    # Vertrags-PDF injiziert — ohne Cap koennte ein 2-MB-Wert jeden Vertrag
    # lahmlegen. Freitext-Bloecke 20.000, Kurzfelder 500 Zeichen.
    @field_validator("email_subject", "email_template", "whatsapp_template",
                     "default_terms", "default_special_agreements",
                     "digital_vertragstext", "active_profile",
                     "email_subject_korrektur", "email_template_korrektur",
                     "email_subject_nach_kauf", "email_template_nach_kauf",
                     "whatsapp_template_nach_kauf",
                     "email_subject_bahn", "email_template_bahn")
    @classmethod
    def _cap_text(cls, v, info):
        if isinstance(v, str):
            short = {"active_profile"}
            limit = 500 if info.field_name in short else 20000
            if len(v) > limit:
                raise ValueError(f"'{info.field_name}' zu lang (max. {limit} Zeichen)")
        return v


class ActiveProfileIn(BaseModel):
    active_profile: str  # "inland" | "export"


# ---------- Endpoints ----------
# Standardwerte fuer Bestandshaendler ohne Regelpakete — eine Liste fuer
# Chef- und Sucher-Zweig von get_settings (Runde 13, C5).
_SETTINGS_STANDARDS = (("comparison_rules", DEFAULT_RULES),
                       ("export_rules", DEFAULT_EXPORT_RULES),
                       ("active_profile", "inland"))


@router.get("/dealer/settings")
async def get_settings(user=Depends(current_firma)):
    """Wirksame Einstellungen aus Sicht des Nutzers: Chef-Vorgaben,
    bei Suchern überlagert von den eigenen persönlichen Anpassungen."""
    from deps import effective_dealer
    dealer = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0})
    # Back-fill neuer Felder für Bestandshändler, damit das Frontend sich
    # keine Sorgen um Legacy-Dokumente machen muss. Runde 11: jedes Feld
    # wird nur geschrieben, wenn es in der Datenbank WEITERHIN fehlt —
    # vorher konnte ein Lesezugriff auf einen alten Stand ein gerade vom
    # Chef gespeichertes Regelpaket wieder mit dem Standard ueberschreiben.
    # Runde 13: C5 — Ein GET eines Suchers schrieb fehlende Regelpakete in das
    # gemeinsame dealers-Dokument. Jetzt: Backfill in der Datenbank nur durch
    # den Chef; ein Sucher bekommt die Standardwerte nur in der Antwort.
    # Pruefbericht 20.09.2026 (P0): nur der EINE Hauptchef schreibt in das
    # gemeinsame Firmen-Dokument zurueck.
    from deps import ist_haupt_chef
    ist_chef = await ist_haupt_chef(user)
    if dealer:
        for feld, standard in _SETTINGS_STANDARDS:
            if dealer.get(feld):
                continue
            dealer[feld] = standard
            if not ist_chef:
                continue
            await db.dealers.update_one(
                {"id": user["dealer_id"],
                 "$or": [{feld: {"$exists": False}}, {feld: None},
                         {feld: {}}, {feld: ""}]},
                {"$set": {feld: standard}})
    if user.get("role") == "sucher":
        # effective_dealer liest das Haendler-Dokument NEU aus der Datenbank —
        # die oben nur im Speicher gesetzten Standardwerte muessen deshalb
        # hier in die Antwort gemischt werden, ohne etwas zu schreiben.
        merged = await effective_dealer(user)
        for feld, standard in _SETTINGS_STANDARDS:
            if not merged.get(feld):
                merged[feld] = standard
        return _mit_digital_standard(_sucher_sicht(merged))
    return _mit_digital_standard(dealer)


def _mit_digital_standard(dealer):
    """Der Standardtext der digitalen Ausfertigung wird NICHT gespeichert
    (leer = Standard), aber der Oberflaeche mitgeliefert, damit Chef und
    Sucher ihn sehen und als Vorlage uebernehmen koennen."""
    if isinstance(dealer, dict):
        from pdf_service import DIGITAL_VERTRAGSTEXT_STANDARD
        dealer["digital_vertragstext_standard"] = DIGITAL_VERTRAGSTEXT_STANDARD
        # Wunsch Ahmad 20.09.2026: Der Vertragsdialog fuellt die Besonderen
        # Vereinbarungen vor. Er darf dafuer NICHT das Freitextfeld nehmen —
        # darin steht seit dem Schalter nur noch der eigene Text der Firma,
        # unser Standardsatz waere beim Anlegen verloren gegangen. Hier kommt
        # die wirksame Fassung mit: Standardsatz (falls eingeschaltet) plus
        # eigener Text. Die Platzhalter bleiben stehen — sie werden erst
        # beim Erzeugen des PDF gefuellt, mit dem DANN gueltigen Abholdatum.
        import vertrag_vorlagen as _vorlagen
        dealer["sondervereinbarungen_effektiv"] = _vorlagen.sondervereinbarungen(dealer)
        dealer["sondervereinbarung_standard_text"] = _vorlagen.BESONDERE_VEREINBARUNGEN
        # 21.09.2026: leere Vorlagen (Firmen von vor dem 20.09.) zeigen den
        # Standardtext — in den Einstellungen und im Versand-Dialog.
        _vorlagen.mit_standardtexten(dealer)
    return dealer


# Runde 12: Ein Sucher bekommt aus dem Haendler-Dokument NUR die Felder,
# die er selbst einstellen darf, plus Kennung. Vorher kam das ganze
# Dokument (samt allem, was kuenftig dazukommt: Kontingente, Marktplatz,
# interne Vermerke) mit den Overrides obendrauf zurueck.
_SUCHER_SICHT_ZUSATZ = {"id", "kunden_nr", "vertrags_kundennummer", "created_at", "updated_at",
                        "digital_vertragstext_standard",
                        # 20.09.2026: wirksame Besondere Vereinbarungen
                        "sondervereinbarungen_effektiv",
                        "sondervereinbarung_standard_text",
                        # 20.09.2026 (B19): persoenlich ueberschriebene Felder
                        "eigene_einstellungen"}


def _sucher_sicht(dealer: dict) -> dict:
    from deps import SUCHER_SETTINGS_FIELDS
    erlaubt = SUCHER_SETTINGS_FIELDS | _SUCHER_SICHT_ZUSATZ
    return {k: v for k, v in (dealer or {}).items() if k in erlaubt}


@router.put("/dealer/active-profile")
async def set_active_profile(body: ActiveProfileIn, user=Depends(current_firma)):
    """Schneller Profil-Wechsel vom Homebildschirm aus (Inland ↔ Export).
    Sucher wechseln nur IHR eigenes Profil (Override), nicht das des Chefs."""
    if body.active_profile not in ("inland", "export"):
        raise HTTPException(400, "active_profile muss 'inland' oder 'export' sein")
    from deps import ist_haupt_chef, log_activity_sicher
    # Pruefbericht 20.09.2026 (P0): wie bei den Einstellungen — nur der
    # Hauptchef schaltet das Profil der FIRMA um, alle anderen ihr eigenes.
    if not await ist_haupt_chef(user):
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {"settings_override.active_profile": body.active_profile}},
        )
    else:
        await db.dealers.update_one(
            {"id": user["dealer_id"]},
            {"$set": {"active_profile": body.active_profile, "updated_at": now_iso()}},
        )
    # Runde 11: Dieses eine Feld entscheidet, welches komplette Regelpaket
    # Vergleich und manuelle Suche verwenden — der Wechsel gehoert ins Protokoll.
    await log_activity_sicher(user["dealer_id"], user["id"], "einstellungen.profil.gewechselt",
                       meta={"active_profile": body.active_profile,
                             "bereich": "persoenlich" if user.get("role") == "sucher" else "firma"})
    return {"active_profile": body.active_profile}


def _eigene_logo_hosts() -> set:
    """Hosts, von denen ein Logo per absoluter URL stammen darf: die eigene
    Oberflaeche (FRONTEND_URL, Standard wie beim Passwort-Reset) und ein
    eigener oeffentlicher Storage-Host (S3_PUBLIC_URL, falls gesetzt)."""
    hosts = set()
    for env, standard in (("FRONTEND_URL", "http://localhost:3000"),
                          ("S3_PUBLIC_URL", "")):
        wert = (os.environ.get(env) or standard).strip()
        if not wert:
            continue
        try:
            host = (urlparse(wert).hostname or "").lower()
        except Exception:
            host = ""
        if host:
            hosts.add(host)
    return hosts


def _validate_logo_url(url: Optional[str]) -> Optional[str]:
    """Only allow http/https URLs (or empty) for logo_url.

    Blocks javascript:, data:, file: and other schemes that could be used
    for XSS or SSRF once the URL is rendered in the frontend or fetched
    server-side (e.g. for PDF generation).

    Nachpruefung Runde 14 (Nr. 103): absolute URLs nur noch vom eigenen
    Host. Ein fremder https-Host wurde vorher als <img> in Vertrags- und
    Kopie-Mails eingebettet und bekam so Abruf, IP und Zeitpunkt jedes
    Empfaengers (Tracking-Pixel). Eigene Logos kommen per Upload
    (/api/files/...) — das bleibt der Normalweg.
    """
    if not url:
        return url
    # Selbst hochgeladene Logos liegen im eigenen Storage und werden über
    # /api/files/<key> ausgeliefert — dieser relative Pfad ist erlaubt.
    if url.startswith("/api/files/"):
        return url
    try:
        teile = urlparse(url)
        scheme = teile.scheme.lower()
        host = (teile.hostname or "").lower()
    except Exception:
        raise HTTPException(400, "logo_url ist keine gültige URL")
    if scheme not in ("http", "https"):
        raise HTTPException(400, "logo_url muss mit http:// oder https:// beginnen")
    if not host or host not in _eigene_logo_hosts():
        raise HTTPException(400, "logo_url darf nur auf ein hochgeladenes Logo "
                                 "(/api/files/...) oder den eigenen Server zeigen "
                                 "— bitte das Logo hochladen")
    return url


def _regeln_pruefen(rohe: dict, profil: str) -> dict:
    """Regelpaket gegen das feste Schema pruefen (regeln.py). Ungueltige
    Werte werden mit 400 abgelehnt statt spaeter den Vergleich mit 500 zu
    zerlegen (PR-Review 09/2026)."""
    from regeln import RegelFehler, regeln_validieren
    try:
        return regeln_validieren(rohe)
    except RegelFehler as exc:
        raise HTTPException(400, f"{profil}-Regeln ungültig: {exc}")


def _collect_settings_update(body: DealerSettingsIn) -> dict:
    update = {}
    if body.profile:
        for k, v in body.profile.model_dump(exclude_none=True).items():
            if k == "logo_url":
                v = _validate_logo_url(v)
            update[k] = v
    if body.comparison_rules is not None:
        update["comparison_rules"] = _regeln_pruefen(body.comparison_rules, "Inland")
    if body.export_rules is not None:
        update["export_rules"] = _regeln_pruefen(body.export_rules, "Export")
    # Rollenprüfung 22.09.2026 (RP-005/RP-104/RP-425, Welle 2): active_profile
    # wird hier NICHT mehr uebernommen. Die Einstellungen schickten es aus dem
    # (veralteten) Anmelde-Kontext mit — ein Speichern setzte das Profil der
    # Firma still zurueck (z. B. Export -> Inland, auch fuer alle Sucher ohne
    # eigenen Wert). Das Profil schreibt nur PUT /dealer/active-profile; ein
    # alter Client, der es noch mitschickt, bekommt keinen Fehler, der Wert
    # wird nur ignoriert.
    if body.email_subject is not None:
        update["email_subject"] = body.email_subject
    if body.email_template is not None:
        update["email_template"] = body.email_template
    if body.whatsapp_template is not None:
        update["whatsapp_template"] = body.whatsapp_template
    if body.default_terms is not None:
        update["default_terms"] = body.default_terms
    if body.default_special_agreements is not None:
        update["default_special_agreements"] = body.default_special_agreements
    if body.digital_vertragstext is not None:
        update["digital_vertragstext"] = body.digital_vertragstext
    # Vorlage Ahmad 20.09.2026: die drei nachtraeglichen Mails und der
    # Schalter fuer unseren Standardsatz. WICHTIG: `is not None` — beim
    # Schalter ist `False` ein GUELTIGER Wert; eine Pruefung auf Wahrheit
    # haette das Abschalten stillschweigend verschluckt.
    for feld in ("email_subject_korrektur", "email_template_korrektur",
                 "email_subject_nach_kauf", "email_template_nach_kauf",
                 "whatsapp_template_nach_kauf",
                 "email_subject_bahn", "email_template_bahn",
                 "sondervereinbarung_standard_aktiv", "empfang_drucken"):
        wert = getattr(body, feld, None)
        if wert is not None:
            update[feld] = wert
    return update


@router.put("/dealer/settings")
async def update_settings(body: DealerSettingsIn, user=Depends(current_firma)):
    """Chef schreibt die Händler-Vorgaben. Sucher speichern dieselben Felder
    als PERSÖNLICHEN Override (users.settings_override) — die Chef-Werte
    bleiben unverändert und dienen weiter als Vorbefüllung."""
    from deps import SUCHER_SETTINGS_FIELDS, effective_dealer, ist_haupt_chef
    update = _collect_settings_update(body)
    # Pruefbericht 20.09.2026 (P0): frueher entschied `role == "sucher"`.
    # Ein zweites dealer-Konto derselben Firma schrieb damit die
    # firmenweiten Vorgaben um. Jetzt bekommt JEDER ausser dem Hauptchef
    # einen persoenlichen Override — das ist die sichere Seite.
    if not await ist_haupt_chef(user):
        from deps import log_activity_sicher
        # Nur ECHTE Abweichungen von der Chef-Vorgabe werden Override. Die
        # Oberflaeche schickt beim Speichern alle effektiven Werte zurueck —
        # vorher wurden dadurch geerbte Chef-Werte als persoenliche Overrides
        # "eingefroren" und spaetere Chef-Aenderungen kamen beim Sucher nie an
        # (PR-Review 09/2026). Gleiche Werte loeschen den Override wieder.
        dealer = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0}) or {}
        aktuell = user.get("settings_override") or {}
        setzen, loeschen = {}, {}
        for k, v in update.items():
            if k not in SUCHER_SETTINGS_FIELDS:
                continue
            chef_wert = dealer.get(k)
            # Runde 24 (11.09.2026): Regelpakete normalisiert vergleichen. Alte
            # Chef-Pakete enthalten noch Kategorie/Navigation/Klimatisierung,
            # die regeln_validieren jetzt still verwirft — ohne Normalisierung
            # waere jedes unveraenderte Speichern eines Suchers eine
            # "Abweichung" und fror das Chef-Paket als Override ein.
            if k in ("comparison_rules", "export_rules"):
                # 16.09.2026: beide Seiten VOLLSTAENDIG vergleichen (Lesepfad
                # mit Standard) — die Oberflaeche schickt seit heute komplette
                # Pakete; ein unvollstaendiges Chef-Paket (z.B. nie gespeichertes
                # Export-Profil) galt sonst als Abweichung und fror den
                # Chef-Stand als persoenlichen Override ein.
                from regeln import regeln_lesen
                standard = DEFAULT_EXPORT_RULES if k == "export_rules" else DEFAULT_RULES
                chef_wert = regeln_lesen(chef_wert, standard)
                v = regeln_lesen(v, standard)
            if v == chef_wert:
                if k in aktuell:
                    loeschen[f"settings_override.{k}"] = ""
            elif aktuell.get(k) != v:
                setzen[f"settings_override.{k}"] = v
        ops = {}
        if setzen:
            ops["$set"] = setzen
        if loeschen:
            ops["$unset"] = loeschen
        if ops:
            await db.users.update_one({"id": user["id"]}, ops)
            # Audit-Log: welcher Sucher welche Felder fuer sich abweichend
            # gesetzt bzw. wieder auf die Chef-Vorgabe zurueckgesetzt hat.
            await log_activity_sicher(
                user["dealer_id"], user["id"], "sucher.einstellungen.override",
                meta={"gesetzt": sorted(k.split(".", 1)[1] for k in setzen),
                      "zurueckgesetzt": sorted(k.split(".", 1)[1] for k in loeschen)})
        fresh_user = await db.users.find_one({"id": user["id"]}, {"_id": 0})
        return _sucher_sicht(await effective_dealer(fresh_user))
    # Rollenprüfung 22.09.2026 (RP-138, Welle 2): Das Logo aendert nur
    # POST /dealer/logo. Hier gilt nur "" (Logo entfernen) oder der
    # unveraenderte Wert. Ein aelterer Tab schickte sonst die ALTE Logo-URL
    # mit — deren Datei war beim Hochladen schon geloescht, das Logo danach
    # kaputt. Ein abweichender Wert wird ignoriert (die uebrigen Felder
    # werden gespeichert), die Antwort zeigt das tatsaechliche Logo.
    logo_vorher = None
    if "logo_url" in update:
        stand = await db.dealers.find_one({"id": user["dealer_id"]},
                                          {"_id": 0, "logo_url": 1}) or {}
        logo_vorher = stand.get("logo_url") or ""
        neu_logo = update["logo_url"] or ""
        if neu_logo and neu_logo != logo_vorher:
            del update["logo_url"]
            logo_vorher = None
            from deps import log_activity_sicher as _log
            await _log(user["dealer_id"], user["id"], "einstellungen.logo.ignoriert",
                       meta={"grund": "logo_url nur ueber /dealer/logo"})
        elif neu_logo == logo_vorher:
            logo_vorher = None                   # unveraendert: nichts wegraeumen
    update["updated_at"] = now_iso()
    await db.dealers.update_one({"id": user["dealer_id"]}, {"$set": update})
    if logo_vorher:
        # "Logo entfernen": die hochgeladene Datei nicht als Waise liegen lassen
        await _altes_logo_wegraeumen(logo_vorher, user["dealer_id"])
    # Runde 11: Firmenweite Aenderungen des Chefs ins Protokoll — vorher
    # war nur der persoenliche Sucher-Override nachvollziehbar, nicht wann
    # der Chef die Vergleichsregeln der ganzen Firma geaendert hat.
    from deps import log_activity_sicher
    await log_activity_sicher(user["dealer_id"], user["id"], "einstellungen.firma.geaendert",
                       meta={"felder": sorted(k for k in update if k != "updated_at")})
    dealer = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0})
    return dealer


class OverrideZuruecksetzenIn(BaseModel):
    # Leer/fehlend = alle persoenlichen Werte zuruecksetzen.
    felder: Optional[list[str]] = Field(default=None, max_length=60)


@router.post("/dealer/settings/zuruecksetzen")
async def settings_zuruecksetzen(body: OverrideZuruecksetzenIn, user=Depends(current_firma)):
    """Persoenliche Werte eines Suchers loeschen — danach gelten wieder die
    Vorgaben des Chefs (und jede spaetere Aenderung des Chefs kommt an).

    Pruefbericht 20.09.2026 (B19): Einmal gespeichert, blieben abweichende
    Werte fuer immer als persoenlicher Override stehen; die Oberflaeche bot
    keinen Weg zurueck."""
    from deps import SUCHER_SETTINGS_FIELDS, effective_dealer, ist_haupt_chef, log_activity_sicher
    if await ist_haupt_chef(user):
        raise HTTPException(400, "Der Hauptaccount hat keine persönlichen Abweichungen — "
                                 "seine Werte SIND die Vorgaben der Firma.")
    # Rollenprüfung 22.09.2026 (RP-005, Welle 2): ohne Feldliste (alte
    # Oberflaeche) NICHT das eigene aktive Profil mitloeschen — die Anzeige
    # blendet es aus, der Sucher haette sein Profil unbemerkt verloren. Wer
    # es wirklich zuruecksetzen will, nennt es ausdruecklich.
    felder = set(body.felder or (SUCHER_SETTINGS_FIELDS - {"active_profile"}))
    unbekannt = felder - SUCHER_SETTINGS_FIELDS
    if unbekannt:
        raise HTTPException(400, f"Unbekannte Einstellung: {', '.join(sorted(unbekannt))}")
    aktuell = user.get("settings_override") or {}
    weg = sorted(k for k in felder if k in aktuell)
    if weg:
        await db.users.update_one(
            {"id": user["id"]},
            {"$unset": {f"settings_override.{k}": "" for k in weg}})
        await log_activity_sicher(
            user["dealer_id"], user["id"], "sucher.einstellungen.override",
            meta={"gesetzt": [], "zurueckgesetzt": weg})
    fresh_user = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    return {"zurueckgesetzt": weg,
            "dealer": _mit_digital_standard(_sucher_sicht(await effective_dealer(fresh_user)))}


class VertragsKundennummerIn(BaseModel):
    vertrags_kundennummer: str = Field(max_length=100)

    @field_validator("vertrags_kundennummer")
    @classmethod
    def _pruefen(cls, v):
        from kontenanlage import vertrags_kundennummer_pruefen
        return vertrags_kundennummer_pruefen(v)


VERTRAGS_KUNDENNUMMER_VERGEBEN = (
    "Kundennummer bereits vergeben — eine andere Firma nutzt sie schon "
    "(oder sie ist eine Anmeldenummer). Bitte eine andere wählen.")


@router.put("/dealer/vertrags-kundennummer")
async def vertrags_kundennummer_setzen(body: VertragsKundennummerIn,
                                       user=Depends(current_firma)):
    """Wunsch Ahmad 26.09.2026 abends: Die Kundennummer fuer Vertraege setzt die
    Firma selbst — Chef UND Sucher (wie die uebrigen Angaben der Firmen-
    identitaet), aber immer FIRMENWEIT im dealers-Dokument: ein persoenlicher
    Sucher-Override (users.settings_override) waere vom Unique-Index
    dealers.vertrags_kundennummer_unique nicht gedeckt. Wer je Vertrag eine
    andere Nummer will, traegt sie im Vertragsdialog ein (ContractIn.kundennummer).

    Eindeutig ueber alle Firmen und nie gleich einer Anmeldenummer (kunden_nr)
    — Vorpruefung plus DuplicateKeyError des Index als Rennschutz -> 409.
    Audit-Eintrag mit altem und neuem Wert."""
    from kontenanlage import vertrags_kundennummer_belegt
    from deps import log_activity_sicher
    from pymongo.errors import DuplicateKeyError
    nr = body.vertrags_kundennummer
    firma = await db.dealers.find_one({"id": user["dealer_id"]},
                                      {"_id": 0, "vertrags_kundennummer": 1}) or {}
    vorher = str(firma.get("vertrags_kundennummer") or "")
    if nr == vorher:
        return {"vertrags_kundennummer": nr, "geaendert": False}
    if await vertrags_kundennummer_belegt(db, nr, ausser_dealer_id=user["dealer_id"]):
        raise HTTPException(409, VERTRAGS_KUNDENNUMMER_VERGEBEN)
    try:
        await db.dealers.update_one(
            {"id": user["dealer_id"]},
            {"$set": {"vertrags_kundennummer": nr, "updated_at": now_iso()}})
    except DuplicateKeyError:
        raise HTTPException(409, VERTRAGS_KUNDENNUMMER_VERGEBEN)
    await log_activity_sicher(
        user["dealer_id"], user["id"], "einstellungen.vertrags_kundennummer.geaendert",
        meta={"vorher": vorher, "nachher": nr,
              "bereich": "firma", "rolle": user.get("role") or ""})
    return {"vertrags_kundennummer": nr, "geaendert": True}


# Nachpruefung Runde 14 (Nr. 116): 2 MB Bild sind als Base64 ~2,8 MB plus
# data-URL-Praefix. Alles darueber lehnt Pydantic (422) ab, BEVOR der
# komplette String dekodiert wird — vorher wurden bis zu 25 MB (nginx-Limit)
# erst dekodiert und dann an der 2-MB-Grenze verworfen.
LOGO_B64_MAX = 2_900_000


class LogoUploadIn(BaseModel):
    logo_b64: str = Field(max_length=LOGO_B64_MAX)  # data-URL oder reines Base64


@router.post("/dealer/logo")
async def upload_logo(body: LogoUploadIn, user=Depends(current_firma)):
    """Firmenlogo hochladen (max. 2 MB). Speichert im Storage und setzt
    logo_url auf den ausgelieferten /api/files/<key>-Pfad. Entscheidung Ahmad
    16.09.2026: nur der Chef — Sucher bekommen 403 (vorher setzten sie ein
    persoenliches Logo als Override)."""
    # Pruefbericht 20.09.2026 (P0): hier stand nur `role == "sucher"`. Ein
    # zweites oder liegengebliebenes dealer-Konto derselben Firma kam damit
    # durch und konnte das Firmenlogo aller austauschen. Jetzt entscheidet
    # dieselbe Regel wie in current_chef: nur der eingetragene Hauptchef.
    from deps import ist_haupt_chef
    if not await ist_haupt_chef(user):
        raise HTTPException(403, "Das Firmenlogo ändert nur der Chef.")
    import base64
    from storage_service import make_key, storage, StorageError, loeschen_oder_vormerken
    try:
        raw = base64.b64decode(body.logo_b64.split(",")[-1], validate=False)
    except (ValueError, TypeError):
        raise HTTPException(400, "Logo konnte nicht gelesen werden")
    if not raw:
        raise HTTPException(400, "Leeres Logo")
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(400, "Logo zu groß (max. 2 MB)")
    # Magic-Bytes-Pruefung: nur echte Bildformate (JPEG/PNG/WebP/GIF) —
    # beliebige Dateien liessen sich sonst als "logo.png" ablegen.
    from storage_service import validate_image_bytes
    try:
        validate_image_bytes(raw, wo="Logo")
    except StorageError as exc:
        raise HTTPException(400, str(exc))
    try:
        # Der Key endet auf .png, also muss auch PNG herauskommen —
        # sonst liefert der Server spaeter den falschen Dateityp aus.
        from storage_service import bild_verkleinern, save_async
        import asyncio as _aio_bild
        raw = await _aio_bild.to_thread(bild_verkleinern, raw, "Logo", "PNG")
        key = make_key("logo", user["dealer_id"], "logo.png")
        await save_async(key, raw)
    except StorageError as exc:
        raise HTTPException(400, f"Logo konnte nicht gespeichert werden: {exc}")
    logo_url = f"/api/files/{key}"
    # Nachpruefung Runde 14 (Nr. 96): Datei lag bereits im Storage, wenn die
    # Datenbank hier scheiterte — die Referenz fehlte, die Datei blieb als
    # Waise liegen. Jetzt derselbe Weg wie beim Inserat-Upload: loeschen oder
    # fuer den Aufraeumjob vormerken, dann sauberer Fehler. Ausserdem wird
    # das vorherige hochgeladene Logo nach dem Wechsel weggeraeumt (vorher
    # bei JEDEM Logowechsel eine Waise), sofern es nirgends sonst haengt.
    try:
        # Runde 16 (15.09.2026): das ERSETZTE Logo per Vergleichen-und-Setzen
        # aus demselben Write kennen — zwei parallele Uploads lasen vorher
        # beide dasselbe alte Logo, und das dazwischen gespeicherte blieb als
        # Waise liegen. Filter auf den gelesenen Stand; geht der Write
        # verloren (anderer Upload dazwischen), wird neu gelesen.
        coll, filt_id, feld = db.dealers, {"id": user["dealer_id"]}, "logo_url"
        update = {"$set": {"logo_url": logo_url, "updated_at": now_iso()}}
        vorher = None
        gesetzt = False
        for _ in range(4):
            doc = await coll.find_one(filt_id, {"_id": 0, feld.split(".")[0]: 1})
            if doc is None:
                raise RuntimeError("Konto nicht gefunden")
            vorher = doc
            for teil in feld.split("."):
                vorher = vorher.get(teil) if isinstance(vorher, dict) else None
            res = await coll.update_one({**filt_id, feld: vorher}, update)
            if res is None or getattr(res, "matched_count", 1):
                gesetzt = True
                break
        if not gesetzt:
            raise RuntimeError("Logo-Wechsel kollidierte mehrfach mit einem parallelen Upload")
    except Exception as exc:  # noqa: BLE001
        await loeschen_oder_vormerken(db, key=key, grund="logo_upload_abbruch",
                                      dealer_id=user["dealer_id"])
        raise HTTPException(500, f"Logo konnte nicht gespeichert werden: {exc}")
    # Runde 17 (Nr. 377): der Logowechsel war die einzige Einstellungs-
    # aenderung ohne Audit-Spur (Profil/Firma loggen) — das Logo landet auf
    # Vertraegen und der Verkaufsseite.
    from deps import log_activity_sicher
    await log_activity_sicher(user["dealer_id"], user["id"], "einstellungen.logo.geaendert",
                              meta={"vorher": vorher, "nachher": logo_url,
                                    "persoenlich": False})
    await _altes_logo_wegraeumen(vorher, user["dealer_id"])
    return {"ok": True, "logo_url": logo_url}


async def _altes_logo_wegraeumen(alte_url: Optional[str], dealer_id: str) -> None:
    """Vorheriges hochgeladenes Logo (logo/<firma>/...) loeschen, wenn kein
    anderes Dokument es mehr referenziert — auch alte Sucher-Overrides (vor
    dem 16.09.2026 konnten Sucher ein eigenes Logo setzen) halten die Datei."""
    if not alte_url or not alte_url.startswith(f"/api/files/logo/{dealer_id}/"):
        return
    alte_url = alte_url.split("?")[0].split("#")[0]
    key = alte_url[len("/api/files/"):]
    # Rollenprüfung 22.09.2026 (Review): Seit RP-452 haelt jeder Vertrag
    # SEIN Logo fest (contract_data.logo_key, routes/contracts.logo_schluessel)
    # und jede spaetere Fassung laedt genau diese Datei. Wurde sie hier beim
    # Logowechsel geloescht, kam Fassung 2 (Terminverschiebung, Abschluss nach
    # der Abholung) ohne Logo heraus, Fassung 1 hatte es — die Einstellung
    # hat den bestehenden Vertrag doch veraendert. Solange ein Vertrag der
    # Firma die Datei nennt, bleibt sie liegen (Index dealer_id).
    noch_genutzt = (await db.dealers.count_documents({"logo_url": alte_url})
                    or await db.users.count_documents(
                        {"settings_override.logo_url": alte_url})
                    or await db.generated_pdfs.find_one(
                        {"dealer_id": dealer_id, "contract_data.logo_key": key},
                        {"_id": 1}))
    if noch_genutzt:
        return
    from storage_service import loeschen_oder_vormerken
    await loeschen_oder_vormerken(db, key=key,
                                  grund="logo_ersetzt", dealer_id=dealer_id)


# =========================================================
#                  ABO / SUBSCRIPTION
# =========================================================
# Nachpruefung Runde 14 (Nr. 70, 71): Anzeige und Kuendigung waehlten ihr
# Abo-Dokument unterschiedlich (Anzeige: juengstes persoenliches, auch
# abgelaufen; Kuendigung: nur Firmenabo) und die Anzeige mischte Felder aus
# zwei Dokumenten. Jetzt EINE Auswahl fuer beide, deckungsgleich mit der
# Zugriffspruefung (deps.subscription_for): das JUENGSTE nicht ersetzte
# persoenliche Abo, wenn es aktiv ist, sonst das Firmenabo (ein aelteres,
# noch aktives persoenliches Abo zaehlt bewusst nicht — es gibt je Konto
# hoechstens ein nicht ersetztes). Sucher: immer nur das persoenliche.
async def massgebliches_abo(user: dict) -> Optional[dict]:
    from deps import sub_status_from_doc
    # Befund 61 (16.09.2026): dieselbe Firmenbindung wie die Zugriffspruefung
    # (deps.get_subscription_status) — ein persoenliches Abo aus einer
    # frueheren Firma wurde sonst als "aktiv bis ..." angezeigt, waehrend der
    # Vergleich 402 lieferte. Altbestand ohne dealer_id bleibt gueltig.
    persoenlich = await db.subscriptions.find_one(
        {"subject_user_id": user["id"], "status": {"$ne": "ersetzt"},
         "$or": [{"dealer_id": user.get("dealer_id")}, {"dealer_id": {"$exists": False}},
                 {"dealer_id": None}]},
        {"_id": 0}, sort=[("created_at", -1)])
    if user.get("role") == "sucher" or sub_status_from_doc(persoenlich)["active"]:
        return persoenlich
    firma = await db.subscriptions.find_one(
        {"dealer_id": user["dealer_id"], "status": {"$ne": "ersetzt"},
         "$or": [{"subject_user_id": {"$exists": False}},
                 {"subject_user_id": None}]},
        {"_id": 0}, sort=[("created_at", -1)])
    # Ohne Firmenabo bleibt das (inaktive) persoenliche Abo die Anzeige-
    # grundlage — beide Wege ergeben "inaktiv", die Felder stammen aber aus
    # EINEM Dokument (Ablaufdatum, roher Status).
    return firma or persoenlich


@router.get("/dealer/subscription")
async def dealer_subscription(user=Depends(current_firma)):
    """Liefert dem Händler den aktuellen Abo-Stand: Plan, Status, Ablaufdatum,
    verbleibende Tage und ob Kündigen/Verlängern möglich ist.

    Status-Werte:
      - "active"    : Abo läuft
      - "cancelled" : Wurde gekündigt, läuft aber noch bis expires_at
      - "expired"   : Bereits abgelaufen
      - "none"      : Kein Abo vorhanden
    """
    # Review 09/2026: Vorher wurde das NEUESTE Abo der Firma angezeigt —
    # also z.B. das Sucher-Abo eines Mitarbeiters mit fremder Laufzeit.
    # Jetzt: erst das persoenliche Abo des Aufrufers, sonst das
    # haendlerweite (ohne subject_user_id).
    # Runde 11: DIESELBE Auswahl wie die Zugriffspruefung (deps): ersetzte
    # Abos zaehlen nicht. Vorher konnte die Anzeige Plan/Status aus dem
    # neuen Abo, Ablaufdatum aber aus dem bereits ersetzten alten mischen.
    # Nachpruefung Runde 14 (Nr. 71): Runde 11 deckte nur "ersetzt" ab — ein
    # ABGELAUFENES persoenliches Abo lieferte weiter Ablaufdatum/Rohstatus,
    # waehrend Plan/aktiv vom Firmenabo kamen. Jetzt stammen ALLE Felder aus
    # dem einen Dokument von massgebliches_abo.
    from deps import sub_status_from_doc
    sub_doc = await massgebliches_abo(user)
    status = sub_status_from_doc(sub_doc)

    days_remaining = None
    expires_at = sub_doc.get("expires_at") if sub_doc else None
    if expires_at and status.get("plan") != "lifetime":
        # Befund 93 (16.09.2026): derselbe Datumsparser wie die Zugriffspruefung
        # (naive Werte gelten als UTC) — vorher blieb days_remaining bei einem
        # naiven Altwert still None, obwohl dasselbe Abo als aktiv galt.
        from deps import _ablauf_parsen
        ea = _ablauf_parsen(expires_at)
        if ea is not None:
            # Rollenpruefung 22.09.2026 (RP-232/RP-383): angefangene Tage
            # zaehlen (aufrunden) — timedelta.days rundete ab, eine frische
            # 3-Tage-Probe zeigte sofort "noch 2 Tage".
            rest_s = (ea - datetime.now(timezone.utc)).total_seconds()
            days_remaining = max(0, math.ceil(rest_s / 86400))

    is_lifetime = status.get("plan") == "lifetime"
    raw_status = (sub_doc or {}).get("status", "active") if sub_doc else "none"
    # Offene Verlaengerungs-Anfrage beim Betreiber (09/2026) — die
    # Oberflaeche zeigt dann "wartet auf Freigabe" statt neuer Buttons.
    # Runde 15 (Nr. 2): nur Anfragen der EIGENEN Firma — ein Konto kann
    # heute zwar nicht die Firma wechseln, aber eine Altanfrage einer
    # anderen dealer_id darf hier nie als "offen" erscheinen.
    anfrage = await db.plan_requests.find_one(
        {"type": "sucher_abo", "subject_user_id": user["id"],
         "dealer_id": user["dealer_id"], "status": "offen"},
        {"_id": 0, "id": 1, "wanted_plan": 1, "created_at": 1})
    ist_sucher = user.get("role") == "sucher"

    return {
        "anfrage_offen": bool(anfrage),
        "anfrage": anfrage,
        "plan": status.get("plan"),
        "status": status["status"],          # zusammengefasster Live-Status
        "raw_status": raw_status,             # roher DB-Status (active/cancelled/...)
        "active": status["active"],
        "expires_at": expires_at,
        "days_remaining": days_remaining,
        "is_lifetime": is_lifetime,
        "can_cancel": bool(
            status["active"] and not is_lifetime and raw_status == "active"
            and not ist_sucher          # Sucher-Abos verwaltet der Betreiber
        ),
        "can_renew": True,  # Verlängern ist immer erlaubt (neuer Checkout)
        "cancelled_at": (sub_doc or {}).get("cancelled_at"),
    }


@router.post("/dealer/subscription/cancel")
async def dealer_cancel_subscription(user=Depends(current_firma)):
    """Kündigt das aktuelle Abo. Wir setzen `status='cancelled'` UND merken
    uns das Datum, lassen das Abo aber bis `expires_at` weiter aktiv. Damit
    bekommt der Händler die bezahlte Zeit zu Ende und keine sofortige
    Sperre. Verlängern bleibt jederzeit möglich (neuer Checkout)."""
    # Pruefbericht 20.09.2026 (P0): ein zweites dealer-Konto der Firma kam
    # hier durch und konnte das Abo der Firma kuendigen.
    from deps import ist_haupt_chef
    if not await ist_haupt_chef(user):
        raise HTTPException(403, "Nur der Händler-Hauptaccount darf Abos verwalten")
    # Nachpruefung Runde 14 (Nr. 70): GENAU das Abo kuendigen, das die
    # Anzeige zeigt (massgebliches_abo). Vorher suchte die Kuendigung nur das
    # Firmenabo: ein Chef mit aktivem persoenlichem Abo bekam 404 bzw. es
    # wurde ein altes Firmenabo gekuendigt, waehrend sein angezeigtes
    # persoenliches Abo weiterlief. Ein Sucher-Abo eines Mitarbeiters wird
    # hier weiterhin nie getroffen (nur eigene subject_user_id oder Firma).
    sub = await massgebliches_abo(user)
    if not sub:
        raise HTTPException(404, "Kein Abo vorhanden")
    if sub.get("plan") == "lifetime":
        raise HTTPException(400, "Lifetime-Abos können nicht gekündigt werden")
    if sub.get("status") == "cancelled":
        raise HTTPException(400, "Abo ist bereits gekündigt")

    # Pruefung 14.09.2026 (Liste 1, Nr. 17): nur, wenn das Abo noch im GELESENEN
    # Zustand ist — eine parallele Sperre/Ersetzung durch den Betreiber wird
    # nicht mit "cancelled" (= laeuft bis Ablauf weiter) ueberschrieben.
    r = await db.subscriptions.update_one(
        {"id": sub["id"], "status": sub.get("status")},
        {"$set": {
            "status": "cancelled",
            "cancelled_at": now_iso(),
        }},
    )
    if r.matched_count == 0:
        raise HTTPException(409, "Das Abo wurde gerade geändert — bitte die Seite neu laden.")
    # Runde 15 (Nr. 8): finanz- und zugriffsrelevanter Zustandswechsel —
    # bisher ohne Audit-Eintrag.
    from deps import log_activity_sicher
    await log_activity_sicher(user["dealer_id"], user["id"], "abo.gekuendigt", ref=sub["id"],
                       meta={"plan": sub.get("plan"), "expires_at": sub.get("expires_at"),
                             "subject_user_id": sub.get("subject_user_id")})
    return {
        "ok": True,
        "expires_at": sub.get("expires_at"),
        "message": "Abo gekündigt — läuft noch bis zum Ende der bezahlten Periode.",
    }
