"""Dealer endpoints: settings GET/PUT, active-profile, subscription info/cancel."""
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
    def _cap(cls, v):
        if isinstance(v, str) and len(v) > 500:
            raise ValueError("Profilfeld zu lang (max. 500 Zeichen)")
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

    # DoS-Schutz: default_terms/default_special_agreements werden in JEDES
    # Vertrags-PDF injiziert — ohne Cap koennte ein 2-MB-Wert jeden Vertrag
    # lahmlegen. Freitext-Bloecke 20.000, Kurzfelder 500 Zeichen.
    @field_validator("email_subject", "email_template", "whatsapp_template",
                     "default_terms", "default_special_agreements",
                     "active_profile")
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
    ist_chef = user.get("role") == "dealer"
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
        return _sucher_sicht(merged)
    return dealer


# Runde 12: Ein Sucher bekommt aus dem Haendler-Dokument NUR die Felder,
# die er selbst einstellen darf, plus Kennung. Vorher kam das ganze
# Dokument (samt allem, was kuenftig dazukommt: Kontingente, Marktplatz,
# interne Vermerke) mit den Overrides obendrauf zurueck.
_SUCHER_SICHT_ZUSATZ = {"id", "kunden_nr", "created_at", "updated_at"}


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
    from deps import log_activity
    if user.get("role") == "sucher":
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
    await log_activity(user["dealer_id"], user["id"], "einstellungen.profil.gewechselt",
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
    if body.active_profile is not None:
        if body.active_profile not in ("inland", "export"):
            raise HTTPException(400, "active_profile muss 'inland' oder 'export' sein")
        update["active_profile"] = body.active_profile
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
    return update


@router.put("/dealer/settings")
async def update_settings(body: DealerSettingsIn, user=Depends(current_firma)):
    """Chef schreibt die Händler-Vorgaben. Sucher speichern dieselben Felder
    als PERSÖNLICHEN Override (users.settings_override) — die Chef-Werte
    bleiben unverändert und dienen weiter als Vorbefüllung."""
    from deps import SUCHER_SETTINGS_FIELDS, effective_dealer
    update = _collect_settings_update(body)
    if user.get("role") == "sucher":
        from deps import log_activity
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
            if v == dealer.get(k):
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
            await log_activity(
                user["dealer_id"], user["id"], "sucher.einstellungen.override",
                meta={"gesetzt": sorted(k.split(".", 1)[1] for k in setzen),
                      "zurueckgesetzt": sorted(k.split(".", 1)[1] for k in loeschen)})
        fresh_user = await db.users.find_one({"id": user["id"]}, {"_id": 0})
        return _sucher_sicht(await effective_dealer(fresh_user))
    update["updated_at"] = now_iso()
    await db.dealers.update_one({"id": user["dealer_id"]}, {"$set": update})
    # Runde 11: Firmenweite Aenderungen des Chefs ins Protokoll — vorher
    # war nur der persoenliche Sucher-Override nachvollziehbar, nicht wann
    # der Chef die Vergleichsregeln der ganzen Firma geaendert hat.
    from deps import log_activity
    await log_activity(user["dealer_id"], user["id"], "einstellungen.firma.geaendert",
                       meta={"felder": sorted(k for k in update if k != "updated_at")})
    dealer = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0})
    return dealer


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
    logo_url auf den ausgelieferten /api/files/<key>-Pfad. Sucher setzen
    damit nur IHR persönliches Logo (Override), nicht das des Chefs."""
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
    ist_sucher = user.get("role") == "sucher"
    # Nachpruefung Runde 14 (Nr. 96): Datei lag bereits im Storage, wenn die
    # Datenbank hier scheiterte — die Referenz fehlte, die Datei blieb als
    # Waise liegen. Jetzt derselbe Weg wie beim Inserat-Upload: loeschen oder
    # fuer den Aufraeumjob vormerken, dann sauberer Fehler. Ausserdem wird
    # das vorherige hochgeladene Logo nach dem Wechsel weggeraeumt (vorher
    # bei JEDEM Logowechsel eine Waise), sofern es nirgends sonst haengt.
    try:
        if ist_sucher:
            vorher = (user.get("settings_override") or {}).get("logo_url")
            await db.users.update_one(
                {"id": user["id"]},
                {"$set": {"settings_override.logo_url": logo_url}})
        else:
            alt = await db.dealers.find_one({"id": user["dealer_id"]},
                                            {"_id": 0, "logo_url": 1})
            vorher = (alt or {}).get("logo_url")
            await db.dealers.update_one(
                {"id": user["dealer_id"]},
                {"$set": {"logo_url": logo_url, "updated_at": now_iso()}})
    except Exception as exc:  # noqa: BLE001
        await loeschen_oder_vormerken(db, key=key, grund="logo_upload_abbruch",
                                      dealer_id=user["dealer_id"])
        raise HTTPException(500, f"Logo konnte nicht gespeichert werden: {exc}")
    await _altes_logo_wegraeumen(vorher, user["dealer_id"])
    return {"ok": True, "logo_url": logo_url}


async def _altes_logo_wegraeumen(alte_url: Optional[str], dealer_id: str) -> None:
    """Vorheriges hochgeladenes Logo (logo/<firma>/...) loeschen, wenn kein
    anderes Dokument (Firma oder Sucher-Override) es mehr referenziert —
    ein Sucher kann per Einstellungen die Chef-URL uebernommen haben."""
    if not alte_url or not alte_url.startswith(f"/api/files/logo/{dealer_id}/"):
        return
    alte_url = alte_url.split("?")[0].split("#")[0]
    noch_genutzt = (await db.dealers.count_documents({"logo_url": alte_url})
                    or await db.users.count_documents(
                        {"settings_override.logo_url": alte_url}))
    if noch_genutzt:
        return
    from storage_service import loeschen_oder_vormerken
    await loeschen_oder_vormerken(db, key=alte_url[len("/api/files/"):],
                                  grund="logo_ersetzt", dealer_id=dealer_id)


# =========================================================
#                  ABO / SUBSCRIPTION
# =========================================================
# Nachpruefung Runde 14 (Nr. 70, 71): Anzeige und Kuendigung waehlten ihr
# Abo-Dokument unterschiedlich (Anzeige: juengstes persoenliches, auch
# abgelaufen; Kuendigung: nur Firmenabo) und die Anzeige mischte Felder aus
# zwei Dokumenten. Jetzt EINE Auswahl fuer beide, deckungsgleich mit der
# Zugriffspruefung (deps.subscription_for): zuerst ein AKTIVES persoenliches
# Abo, sonst das Firmenabo. Sucher: immer nur das persoenliche.
async def massgebliches_abo(user: dict) -> Optional[dict]:
    from deps import sub_status_from_doc
    persoenlich = await db.subscriptions.find_one(
        {"subject_user_id": user["id"], "status": {"$ne": "ersetzt"}},
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
        try:
            ea = datetime.fromisoformat(expires_at)
            delta = ea - datetime.now(timezone.utc)
            days_remaining = max(0, delta.days)
        except Exception:
            days_remaining = None

    is_lifetime = status.get("plan") == "lifetime"
    raw_status = (sub_doc or {}).get("status", "active") if sub_doc else "none"
    # Offene Verlaengerungs-Anfrage beim Betreiber (09/2026) — die
    # Oberflaeche zeigt dann "wartet auf Freigabe" statt neuer Buttons.
    anfrage = await db.plan_requests.find_one(
        {"type": "sucher_abo", "subject_user_id": user["id"], "status": "offen"},
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
    if user.get("role") == "sucher":
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

    await db.subscriptions.update_one(
        {"id": sub["id"]},
        {"$set": {
            "status": "cancelled",
            "cancelled_at": now_iso(),
        }},
    )
    return {
        "ok": True,
        "expires_at": sub.get("expires_at"),
        "message": "Abo gekündigt — läuft noch bis zum Ende der bezahlten Periode.",
    }
