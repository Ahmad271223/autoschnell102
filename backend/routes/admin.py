"""Admin endpoints: users CRUD, contracts, stats, comparisons, URL-stats,
self-password, cleanup trigger.
"""
import base64
import hashlib
import re
import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, Literal, Optional


def _safe_filename(name: str, fallback: str = "document.pdf") -> str:
    """Strip characters that could inject extra HTTP header lines."""
    safe = re.sub(r'[\r\n\t"\\]', "", name).strip()
    return safe[:200] or fallback

from pymongo.errors import DuplicateKeyError
from fastapi import APIRouter, Body, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field, field_validator

from auth import (hash_password, hash_password_async,
                  verify_password, verify_password_async)
from cleanup_service import _cleanup_once
from deps import (
    current_admin, current_super_admin, db, get_subscription_status, log, log_activity,
    log_activity_sicher, now_iso, sub_status_from_doc, subscription_for,
)
from mobile_service import DEFAULT_RULES, DEFAULT_EXPORT_RULES
from passwoerter import pruefe_passwort

router = APIRouter()


# ---------- Models ----------
class AdminUserIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    company_name: str = Field(min_length=1, max_length=200)
    # Audit 09/2026: nur feste Werte — "lifetime" und Freitext erzeugten
    # unbegrenzten Zugang. Betreiber-Modell: "none" (Firma ohne Abo).
    plan_type: Literal["none", "monthly", "yearly", "trial"] = "none"
    expires_at: Optional[str] = None
    active: Optional[bool] = True

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return pruefe_passwort(v)


class AdminActiveIn(BaseModel):
    active: bool


class AdminUserPasswordIn(BaseModel):
    new_password: str = Field(min_length=1, max_length=200)

    @field_validator("new_password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return pruefe_passwort(v)


class AdminSelfPasswordIn(BaseModel):
    current_password: str
    new_password: str


# ---------- Cleanup trigger ----------
@router.post("/admin/cleanup/run")
async def admin_trigger_cleanup(user=Depends(current_super_admin)):
    """Manuell einen Cleanup-Durchlauf anstoßen (Debug/QA).
    Regulär läuft der Loop 1× pro Stunde automatisch."""
    stats = await _cleanup_once(db)
    return stats


def _vertragstext_start_admin() -> str:
    from pdf_service import VERTRAGSTEXT_START
    return VERTRAGSTEXT_START


async def _dealer_anlegen_mit_kunden_nr(doc: dict, naechste_kunden_nr) -> None:
    """Firmenprofil mit frischer Kundennummer einfuegen; bei DuplicateKey
    (Unique-Index kunden_nr, nur im Rennen mit einem korrigierten Zaehler)
    neue Nummer ziehen — max. 3 Versuche."""
    for versuch in range(3):
        doc.pop("_id", None)            # insert_one schreibt _id ins dict
        doc["kunden_nr"] = await naechste_kunden_nr()
        try:
            await db.dealers.insert_one(doc)
            return
        except DuplicateKeyError as e:
            if "kunden_nr" not in str(e) or versuch == 2:
                raise


def _ablaufdatum_pruefen_400(wert, feld: str = "expires_at"):
    """Nachpruefung Runde 14 (Befund 50): Abo-Ablauf aus der Admin-Eingabe
    pruefen — vorher wurde der Rohwert ("31.12.2027", "irgendwann")
    ungeprueft ins Abo geschrieben; deps.sub_status_from_doc wertet ein
    unlesbares Datum fail-closed als "ungueltig", die Firma stand also
    stillschweigend OHNE wirksames Abo da, der Admin bekam 200.
    Erlaubt: leer (-> None), 'JJJJ-MM-TT' (Ablauf am Tagesende wie bei
    _gueltig_bis_parsen) oder ein ISO-Zeitpunkt (naiv = UTC). Sonst 400
    (nicht 422), damit die Oberflaeche die deutsche Meldung zeigt."""
    if wert is None or str(wert).strip() == "":
        return None
    w = str(wert).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", w):
        try:
            return _gueltig_bis_parsen(w)
        except HTTPException:
            raise HTTPException(400, f"{feld} ist kein gueltiges Datum (JJJJ-MM-TT)")
    try:
        d = datetime.fromisoformat(w.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, f"{feld} muss ein ISO-Datum (JJJJ-MM-TT) "
                                 "oder ein ISO-Zeitpunkt sein")
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.isoformat()


# ---------- Users ----------
@router.post("/admin/users")
async def admin_create_user(body: AdminUserIn, admin=Depends(current_super_admin)):
    # Haertung 09/2026: E-Mail wie bei Registrierung/Sucher normalisieren und
    # schreibungsunabhaengig pruefen — vorher konnten "Chef@X.de" und
    # "chef@x.de" als zwei Konten existieren (Login trifft dann das falsche).
    email = body.email.strip().lower()
    # Runde 13: B5 — plattformweit (users UND driver_accounts) statt nur users.
    from deps import email_vergeben
    if await email_vergeben(email):
        raise HTTPException(409, "E-Mail bereits registriert")
    # Nachpruefung Runde 14 (Befund 50): Ablauf VOR dem ersten Insert
    # pruefen — ein 400 hier laesst weder Konto noch Firmenprofil zurueck.
    ablauf_eingabe = _ablaufdatum_pruefen_400(body.expires_at)
    user_id = str(uuid.uuid4())
    dealer_id = str(uuid.uuid4())
    try:
        await db.users.insert_one({
            "id": user_id, "email": email,
            "password_hash": await hash_password_async(body.password),
            "role": "dealer", "active": body.active if body.active is not None else True,
            "dealer_id": dealer_id, "current_session_id": None,
            "created_at": now_iso(),
        })
    except DuplicateKeyError:
        # Rennen zweier gleichzeitiger Anlagen: Unique-Index entscheidet.
        raise HTTPException(409, "E-Mail bereits registriert")
    from deps import naechste_kunden_nr
    try:
        await _dealer_anlegen_mit_kunden_nr({
        "id": dealer_id, "user_id": user_id, "company_name": body.company_name,
        "contact_person": "", "phone": "", "email": email,
        "address": "", "zip_code": "", "city": "", "logo_url": "",
        "comparison_rules": DEFAULT_RULES,
        "export_rules": DEFAULT_EXPORT_RULES,
        "active_profile": "inland",
        "email_subject": "Kaufvertrag für Ihr Fahrzeug",
        "email_template": "Guten Tag,\n\nanbei sende ich Ihnen den Kaufvertrag.\n\nMfG\n{händler_name}",
        "whatsapp_template": "Hallo, hier ist der Kaufvertrag. Bitte prüfen.",
        "default_terms": "",
        # Runde 26: ein Feld fuer Vertragsbedingungen (vier Klauseln + AGB).
        "digital_vertragstext": _vertragstext_start_admin(),
        "default_special_agreements": "",
        "created_at": now_iso(),
        }, naechste_kunden_nr)
    except Exception:
        # Kein Konto ohne Firmenprofil zuruecklassen (Login liefe sonst
        # auf ein Profil-404) — Benutzer wieder entfernen.
        await db.users.delete_one({"id": user_id})
        log.exception("admin_create_user: Firmenprofil-Insert fehlgeschlagen")
        raise HTTPException(500, "Firma anlegen fehlgeschlagen — bitte erneut versuchen")
    # plan_type "none" (Betreiber-Modell 09/2026): Firmen-Hauptaccount ohne
    # jedes Abo anlegen — Verkaufen/Verwalten ist kostenlos, Sucher-Abos
    # werden einzeln nach Rechnungszahlung freigeschaltet.
    if body.plan_type == "none":
        await log_activity(admin.get("dealer_id", ""), admin["id"],
                           "admin.user.erstellt", ref=user_id,
                           meta={"email": body.email, "plan": "none"})
        return {"ok": True, "user_id": user_id, "dealer_id": dealer_id}
    expires = ablauf_eingabe
    if not expires and body.plan_type in ("monthly", "trial"):
        expires = (datetime.now(timezone.utc) + timedelta(days=30 if body.plan_type == "monthly" else 14)).isoformat()
    if not expires and body.plan_type == "yearly":
        expires = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
    # Nachpruefung Runde 14 (Befund 49): Abo-Insert mit Rollback wie beim
    # Firmenprofil — vorher blieb bei einem Fehler hier (DB-Ausfall
    # zwischen zwei Inserts) eine Firma OHNE Abo stehen, und der zweite
    # Versuch lief auf 409 "E-Mail bereits registriert". Der tote
    # "lifetime"-Zweig ist entfernt (Literal erlaubt nur none/monthly/
    # yearly/trial).
    try:
        await db.subscriptions.insert_one({
            "id": str(uuid.uuid4()), "dealer_id": dealer_id,
            "plan": body.plan_type, "status": "active",
            "expires_at": expires,
            "created_at": now_iso(),
        })
    except Exception:
        await db.dealers.delete_one({"id": dealer_id})
        await db.users.delete_one({"id": user_id})
        log.exception("admin_create_user: Abo-Insert fehlgeschlagen")
        raise HTTPException(500, "Firma anlegen fehlgeschlagen — bitte erneut versuchen")
    await log_activity(admin.get("dealer_id", ""), admin["id"], "admin.user.erstellt",
                       ref=user_id, meta={"email": body.email, "plan": body.plan_type})
    return {"ok": True, "user_id": user_id, "dealer_id": dealer_id}


@router.get("/admin/users")
async def admin_list_users(_=Depends(current_admin),
                           page: int = 1, limit: int = 1000):
    """Nutzerliste MIT Firmenname und Abo-Status — in 3 Abfragen gesamt
    statt 2 Abfragen JE NUTZER (vorher: bis zu 2001 Abfragen bei 1000
    Nutzern). Standard-Limit 1000 = bisheriges Verhalten, damit die
    Admin-Oberflaeche ohne Umbau denselben Bestand sieht; page/limit
    stehen fuer kuenftige Pagination bereit."""
    limit = max(1, min(int(limit or 1000), 1000))
    # Nachpruefung 13.09.2026 (wie #52): page nach oben begrenzen — sonst
    # sprengt (page - 1) * limit int64 und pymongo wirft OverflowError (500).
    page = max(1, min(int(page or 1), 10 ** 6))
    # Runde 12: Sitzungs-ID gehoert nicht in Admin-Antworten.
    users = await db.users.find({}, {"_id": 0, "password_hash": 0, "mfa.secret": 0,
                                      "mfa.pending_secret": 0, "mfa.wiederherstellung": 0,
                                      "current_session_id": 0}) \
        .sort("created_at", -1).skip((page - 1) * limit).to_list(limit)
    for u in users:                     # nur der Schalter, nie das Geheimnis
        u["mfa_aktiv"] = bool((u.pop("mfa", None) or {}).get("aktiv"))
    dealer_ids = list({u.get("dealer_id") for u in users if u.get("dealer_id")})
    dealers = {d["id"]: d async for d in db.dealers.find(
        {"id": {"$in": dealer_ids}},
        {"_id": 0, "id": 1, "company_name": 1, "kunden_nr": 1})}
    # Juengstes HAENDLER-Abo je Firma — mit derselben Vorrang-Regel wie
    # deps.get_subscription_status: Dokumente OHNE subject_user_id-Feld
    # gewinnen gegen Alt-Dokumente mit explizitem null, egal wie alt.
    newest_subs = {}
    async for row in db.subscriptions.aggregate([
        {"$match": {"dealer_id": {"$in": dealer_ids},
                    "$or": [{"subject_user_id": {"$exists": False}},
                            {"subject_user_id": None}]}},
        {"$addFields": {"_feld_fehlt": {
            "$cond": [{"$eq": [{"$type": "$subject_user_id"}, "missing"]},
                      1, 0]}}},
        {"$sort": {"_feld_fehlt": -1, "created_at": -1}},
        {"$group": {"_id": "$dealer_id", "sub": {"$first": "$$ROOT"}}},
    ]):
        newest_subs[row["_id"]] = row["sub"]
    # Persoenliche Abos fuer ALLE Konten (Sucher UND Chef) — dieselbe Regel
    # wie deps.subscription_for (Audit 09/2026: Anzeige und Zugriff duerfen
    # nicht auseinanderlaufen): Chef = persoenlich, sonst altes Firmen-Abo.
    konto_ids = [u["id"] for u in users if u.get("role") in ("sucher", "dealer")]
    persoenlich = {}
    if konto_ids:
        async for row in db.subscriptions.aggregate([
            {"$match": {"subject_user_id": {"$in": konto_ids},
                        "status": {"$ne": "ersetzt"}}},
            {"$sort": {"created_at": -1}},
            {"$group": {"_id": "$subject_user_id", "sub": {"$first": "$$ROOT"}}},
        ]):
            persoenlich[row["_id"]] = row["sub"]

    def _abo(u):
        if u.get("role") == "sucher":
            return sub_status_from_doc(persoenlich.get(u["id"]))
        pers = sub_status_from_doc(persoenlich.get(u["id"]))
        if pers["active"] or u.get("role") != "dealer":
            return pers
        firma = sub_status_from_doc(newest_subs.get(u.get("dealer_id")))
        return firma if firma["active"] else pers

    return [{**u,
             "company_name": dealers.get(u.get("dealer_id"), {}).get("company_name"),
             "kunden_nr": dealers.get(u.get("dealer_id"), {}).get("kunden_nr"),
             "subscription": _abo(u)}
            for u in users]


@router.put("/admin/users/{user_id}")
async def admin_update_user(user_id: str, body: dict = Body(...), admin=Depends(current_super_admin)):
    target = await db.users.find_one({"id": user_id})
    if not target:
        raise HTTPException(404, "Nutzer nicht gefunden")
    fields = {}
    for k in ("active", "role"):
        if k in body:
            fields[k] = body[k]
    # ROLLENAENDERUNGEN sind Super-Admin-Sache (PR-Review 09/2026): sonst
    # kann jeder normale Admin beliebige Nutzer zu weiteren Admins machen
    # (Eskalation) oder Kollegen degradieren. Erlaubte Zielrollen sind
    # zudem fest verdrahtet.
    if "role" in fields:
        if not admin.get("is_super_admin"):
            raise HTTPException(403, "Rollen ändern darf nur der Super-Admin")
        # Runde 12: "admin" ist keine vergebbare Rolle mehr — es gibt genau
        # einen Betreiber (Super-Admin), weitere Admin-Konten sind abgeschafft.
        if fields["role"] == "admin":
            raise HTTPException(400, "Es gibt nur den Super-Admin als Betreiber — "
                                     "weitere Admin-Konten sind nicht vorgesehen.")
        if fields["role"] not in ("dealer", "sucher", "b2b_buyer"):
            raise HTTPException(400, "Unbekannte Rolle")
        # Pruefbericht 09/2026: bisher wurde nur der NAME der Zielrolle
        # geprueft, nicht ob das Konto dazu passt. Aus einem Zwischen-
        # haendler (gehoert zu keiner Firma) liess sich so ein "Sucher ohne
        # Firma" machen: formal Sucher, praktisch ueberall ausgesperrt.
        # Und ein Chef liess sich degradieren, obwohl seine Firma dann
        # ohne Chef dastand.
        neue_rolle = fields["role"]
        alte_rolle = target.get("role")
        if neue_rolle != alte_rolle:
            if neue_rolle in ("dealer", "sucher") and not target.get("dealer_id"):
                raise HTTPException(
                    400, "Dieses Konto gehört zu keiner Firma. Ein Wechsel zu "
                         "Händler oder Sucher würde ein Konto erzeugen, das "
                         "sich nirgends anmelden kann. Bitte stattdessen eine "
                         "Firma anlegen und den Zugang dort einrichten.")
            if neue_rolle == "dealer" and target.get("dealer_id"):
                # Runde 11: GENAU EIN Hauptaccount je Firma. current_chef()
                # erkennt den Chef an der Rolle — ein zweites dealer-Konto
                # haette sofort volle Chef-Rechte, und die Firmensperre
                # ("gesperrter Chef = gesperrte Firma") wuerde je nach
                # gefundenem Datensatz zufaellig greifen oder nicht.
                chef = await db.users.find_one(
                    {"dealer_id": target["dealer_id"], "role": "dealer",
                     "id": {"$ne": target["id"]}},
                    {"_id": 0, "id": 1, "email": 1})
                if chef and not body.get("chef_wechsel"):
                    raise HTTPException(
                        400, "Diese Firma hat bereits einen Hauptaccount "
                             f"({chef.get('email', '')}). Eine Firma hat genau "
                             "einen Chef. Soll dieses Konto der neue Chef werden "
                             "und der bisherige zum Sucher, dann chef_wechsel=true "
                             "mitschicken.")
                if chef:
                    # Chefwechsel: bisheriger Chef wird Sucher (seine Sitzung
                    # endet), das Firmenprofil zeigt auf den neuen Chef.
                    await db.users.update_one(
                        {"id": chef["id"], "role": "dealer"},
                        {"$set": {"role": "sucher", "current_session_id": None,
                                  "updated_at": now_iso()}})
                    await db.dealers.update_one(
                        {"id": target["dealer_id"]},
                        {"$set": {"user_id": target["id"], "updated_at": now_iso()}})
                    await log_activity(
                        admin.get("dealer_id", ""), admin["id"], "admin.firma.chefwechsel",
                        ref=target["dealer_id"],
                        meta={"alter_chef": chef.get("email", ""),
                              "neuer_chef": target.get("email", "")})
            if alte_rolle == "dealer" and target.get("dealer_id"):
                # Runde 12: Der Hauptaccount wird NIE direkt herabgestuft.
                # Vorher war es erlaubt, sobald kein weiterer Zugang
                # existierte — genau dann blieb die Firma ohne Chef zurueck;
                # und ein Sucher zaehlte faelschlich als "Ersatz". Der
                # einzige Weg ist der Chefwechsel (Nachfolger mit
                # chef_wechsel=true befoerdern), der den alten Chef selbst
                # zum Sucher macht.
                raise HTTPException(
                    400, "Der Händler-Hauptaccount kann nicht herabgestuft "
                         "werden — die Firma braucht immer genau einen Chef. "
                         "Nachfolger bestimmen: dessen Konto mit role=dealer "
                         "und chef_wechsel=true befördern; der bisherige Chef "
                         "wird dabei zum Sucher.")
            # Runde 12: Jede Rollenaenderung beendet die laufende Sitzung.
            # current_user() liest die Rolle bei jedem Request frisch — ein
            # bestehendes Haendler-Token bekam so ohne neue Anmeldung (und
            # ohne zweiten Faktor) Admin-Rechte.
            fields["current_session_id"] = None
    # Admin-Konten verwalten nur Super-Admins: Passwort-Reset, Sperren
    # oder Loeschen eines Admins durch einen NORMALEN Admin waere eine
    # Kontouebernahme auf gleicher Stufe.
    if target.get("role") == "admin" and target.get("id") != admin.get("id") \
            and not admin.get("is_super_admin"):
        raise HTTPException(403, "Admin-Konten verwaltet nur der Super-Admin")
    if target.get("is_super_admin"):
        if "role" in fields and fields["role"] != "admin":
            raise HTTPException(400, "Super-Admin-Rolle kann nicht geändert werden")
        if "active" in fields and not fields["active"]:
            raise HTTPException(400, "Super-Admin kann nicht gesperrt werden")
        if "password" in body and target.get("id") != admin.get("id"):
            raise HTTPException(403, "Das Super-Admin-Passwort ändert nur der "
                                     "Super-Admin selbst")
    # Selbst-Sperre verhindern
    if target.get("id") == admin.get("id") and "active" in fields and not fields["active"]:
        raise HTTPException(400, "Du kannst dich nicht selbst sperren")
    if "password" in body and body["password"]:
        pw = str(body["password"])
        try:
            pruefe_passwort(pw)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        fields["password_hash"] = await hash_password_async(pw)
        # Passwortwechsel beendet alle Sitzungen des Kontos — ein
        # gestohlener Token ueberlebt die Aenderung nicht (PR-Review).
        fields["current_session_id"] = None
    # Nachpruefung Runde 14 (Befund 18): Sperren ueber PUT muss dasselbe
    # tun wie POST /active — vorher blieb current_session_id stehen, das
    # alte Token war nach dem Entsperren wieder gueltig (auch ein
    # gestohlenes), und die Sucher einer per PUT gesperrten Firma
    # behielten ihre Sitzungen. Beim Entsperren eines gesperrten Kontos
    # wird die Sitzung ebenfalls verworfen (Altbestand, der noch ueber den
    # alten PUT gesperrt wurde): eine Sperre darf nie eine Sitzung
    # "konservieren".
    sucher_abgemeldet = 0
    if "active" in fields:
        fields["active"] = bool(fields["active"])
        if not fields["active"]:
            fields["current_session_id"] = None
            if target.get("role") == "dealer" and target.get("dealer_id"):
                r = await db.users.update_many(
                    {"dealer_id": target["dealer_id"], "role": "sucher"},
                    {"$set": {"current_session_id": None, "updated_at": now_iso()}})
                sucher_abgemeldet = r.modified_count
        elif not target.get("active", True):
            fields["current_session_id"] = None
    if fields:
        await db.users.update_one({"id": user_id},
                                  {"$set": {**fields, "updated_at": now_iso()}})
    if "plan_type" in body:
        u = await db.users.find_one({"id": user_id})
        if not u:
            raise HTTPException(404)
        plan = body["plan_type"]
        # Nachpruefung Runde 14 (Befund 50): derselbe Altpfad wie bei der
        # Firmenanlage — Rohwert nie ungeprueft ins Abo schreiben.
        expires = _ablaufdatum_pruefen_400(body.get("expires_at"))
        if plan == "lifetime":
            expires = None
        sub_doc = {
            "id": str(uuid.uuid4()), "dealer_id": u["dealer_id"],
            "plan": plan, "status": "active",
            "expires_at": expires, "created_at": now_iso(),
        }
        # Sucher-Unteraccounts haben ein PERSÖNLICHES Abo (Phase 2).
        if u.get("role") == "sucher":
            sub_doc["subject_user_id"] = u["id"]
            # Genau EIN aktives Abo je Konto (Index): vorheriges zuerst
            # als "ersetzt" markieren, Historie bleibt.
            await db.subscriptions.update_many(
                {"subject_user_id": u["id"], "status": "active"},
                {"$set": {"status": "ersetzt", "ersetzt_durch": sub_doc["id"],
                          "updated_at": now_iso()}})
        await db.subscriptions.insert_one(sub_doc)
        await log_activity(admin.get("dealer_id", ""), admin["id"], "admin.abo.vergeben",
                           ref=user_id, meta={"plan": plan, "expires_at": expires,
                                              "email": u.get("email", "")})
    if fields:
        await log_activity(admin.get("dealer_id", ""), admin["id"], "admin.user.aktualisiert",
                           ref=user_id, meta={"felder": sorted(fields.keys()),
                                              "email": target.get("email", ""),
                                              "sucher_abgemeldet": sucher_abgemeldet})
    return {"ok": True, "sucher_abgemeldet": sucher_abgemeldet}


# Alles, was einer Firma gehoert (dealer_id-Verweis) — Grundlage fuer
# Loeschvorschau und vollstaendige Firmenloeschung. Bewusst NICHT dabei:
# activity_logs (Plattform-Nachvollziehbarkeit) und payment_transactions
# (Buchhaltungs-/Aufbewahrungspflicht).
_COMPANY_COLLECTIONS = (
    "subscriptions", "vehicles", "appointments",
    "generated_pdfs", "generated_pdf_versions", "resale_listings",
    "listing_interest", "pickup_protocols", "pickup_reports",
    # driver_accounts NICHT: Fahrer-Konten sind firmenneutral (kein
    # dealer_id-Feld) — der alte Eintrag war ein No-Op und liess die
    # Loeschvorschau faelschlich "0 Fahrer" zaehlen. Fahrer loescht der
    # Admin ueber DELETE /admin/drivers/{id}.
    "dealer_drivers", "dealer_invites",
    "plan_requests", "vehicle_comparisons",
    # Runde 18: Kaufvorgaenge (Umbau 09.09.2026) tragen dealer_id, Sucher,
    # Vertrag, Fahrzeug und Kaufpreis — blieben bei der Firmenloeschung liegen.
    "kaufvorgaenge",
    # Nachpruefung Runde 14 (Befund 58): users ZULETZT — bricht die
    # Firmenloeschung mittendrin ab, findet der erneute Aufruf ueber den
    # Chef-Account den Vorgang noch (vorher: 404, Rest blieb verwaist).
    "users",
)


@router.get("/admin/dealers/{dealer_id}/loeschvorschau")
async def admin_delete_preview(dealer_id: str, admin=Depends(current_admin)):
    """Vorschau: was eine vollstaendige Firmenloeschung entfernen wuerde."""
    dealer = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "id": 1,
                                                          "company_name": 1})
    if not dealer:
        raise HTTPException(404, "Händler nicht gefunden")
    counts = {}
    for coll in _COMPANY_COLLECTIONS:
        counts[coll] = await db[coll].count_documents({"dealer_id": dealer_id})
    return {"dealer_id": dealer_id,
            "hinweis": "Beweis-Snapshots werden NICHT geloescht (haendler"
                       "neutral geteilt, verfallen ueber die Aufbewahrungs"
                       "frist).",
            "company_name": dealer.get("company_name", ""),
            "wuerde_loeschen": counts}


@router.delete("/admin/users/{user_id}")
async def admin_delete_user(user_id: str, firma_loeschen: bool = False,
                            admin=Depends(current_super_admin)):
    """Nutzer loeschen — nach Rolle getrennt (Beschluss PR-Review 08/2026):

    - Sucher/Fahrer/Kaeufer: NUR dieser Nutzer (+ sein persoenliches Abo)
      wird geloescht. Firma, Fahrzeuge, Vertraege bleiben unberuehrt.
      (Vorher riss das Loeschen eines Suchers den Firmendatensatz und
      ALLE Abos der Firma mit — andere Nutzer blieben verwaist zurueck.)
    - Haendler-Hauptaccount: ohne ?firma_loeschen=true kommt 409 mit dem
      Hinweis auf Deaktivieren bzw. Loeschvorschau. Mit firma_loeschen=true
      wird die Firma VOLLSTAENDIG entfernt (alle Nutzer, Fahrzeuge,
      Termine, Vertraege, Inserate, Protokolle, Abos) — nachvollziehbar
      im Audit-Log. Buchhaltungsdaten (payment_transactions) bleiben.
    """
    u = await db.users.find_one({"id": user_id})
    if not u:
        raise HTTPException(404)
    if u.get("is_super_admin"):
        raise HTTPException(400, "Super-Admin kann nicht gelöscht werden")
    if u.get("role") == "admin" and not admin.get("is_super_admin"):
        raise HTTPException(403, "Admin-Konten löscht nur der Super-Admin")
    if u.get("id") == admin.get("id"):
        raise HTTPException(400, "Du kannst dich nicht selbst löschen")

    if u.get("role") != "dealer":
        # Einzelner Mitarbeiter-/Kaeufer-Account: diesen entfernen — samt
        # seiner personenbezogenen Reste (DSGVO): Netzwerk-Mitgliedschaften,
        # Favoriten, Kaufanfragen und offene Passwort-Resets. Vorher blieb
        # all das nach der "vollstaendigen" Loeschung zurueck.
        #
        # Nachpruefung Runde 14 (Befund 58): Reihenfolge wie bei
        # cleanup_service.vertrag_endgueltig_loeschen — Grabstein zuerst,
        # Nebendaten danach, das users-Dokument ZULETZT. Vorher wurde das
        # Konto als Erstes geloescht; brach der Prozess danach ab, blieben
        # Abo/Favoriten/Resets ohne Bezugskonto zurueck und ein erneuter
        # Aufruf lief auf 404 statt nachzuholen. Jetzt ist jeder Schritt
        # wiederholbar: der Grabstein sperrt das Konto sofort (kein Login,
        # keine Sitzung), ein zweiter Aufruf fuehrt die Loeschung zu Ende.
        jetzt = now_iso()
        grab = u.get("loeschung") or {}
        if grab.get("status") == "laeuft":
            await db.users.update_one(
                {"id": user_id},
                {"$set": {"loeschung.gestartet": jetzt, "active": False,
                          "current_session_id": None, "updated_at": jetzt},
                 "$inc": {"loeschung.wiederaufnahmen": 1}})
        else:
            await db.users.update_one(
                {"id": user_id},
                {"$set": {"loeschung": {"status": "laeuft", "gestartet": jetzt,
                                        "grund": "admin", "durch": admin["id"]},
                          "active": False, "current_session_id": None,
                          "updated_at": jetzt}})
        await db.subscriptions.delete_many({"subject_user_id": user_id})
        await db.network_members.delete_many({"buyer_user_id": user_id})
        await db.buyer_favorites.delete_many({"buyer_user_id": user_id})
        await db.listing_interest.delete_many({"buyer_user_id": user_id})
        await db.plan_requests.delete_many({"buyer_user_id": user_id})
        # Reset-Dokumente tragen user_id, keine E-Mail (Runde 5).
        await db.password_resets.delete_many({"user_id": user_id})
        # Nachpruefung Runde 14 (Befund 24): zugang_grants (Stripe-
        # Freischaltungen, routes/payments.py) blieben mit der user_id
        # stehen — die Collection raeumte sonst niemand auf. Sie werden
        # NICHT geloescht, sondern pseudonymisiert: der Grant gehoert zur
        # Zahlung (session_id) und ist wie payment_transactions Teil der
        # Buchhaltung (Beleg, welche Laufzeit fuer welche Zahlung gutge-
        # schrieben wurde) — nur der Personenbezug faellt weg. Pseudonym
        # deterministisch (SHA-256 der Konto-ID, 8 Hex) wie bei
        # fahrer_konto_anonymisieren: mehrfach laufende Loeschung ergibt
        # denselben Wert, mehrere Grants desselben Kontos bleiben als
        # zusammengehoerig erkennbar, ohne Rueckschluss auf die Person.
        pseudonym = "geloescht:" + hashlib.sha256(
            user_id.encode("utf-8")).hexdigest()[:8]
        await db.zugang_grants.update_many(
            {"user_id": user_id},
            {"$set": {"user_id": pseudonym, "pseudonymisiert_at": jetzt}})
        # Runde 13: B8 — Nutzerkennung in Beweis-Snapshots pseudonymisieren.
        from snapshot_service import snapshots_pseudonymisieren
        await snapshots_pseudonymisieren(db, user_id=user_id)
        await db.users.delete_one({"id": user_id})
        await log_activity(admin.get("dealer_id", ""), admin["id"],
                           "admin.user.geloescht", ref=user_id,
                           meta={"email": u.get("email", ""),
                                 "rolle": u.get("role", ""),
                                 "wiederaufnahme": grab.get("status") == "laeuft"})
        return {"ok": True, "geloescht": "nur_nutzer"}

    dealer_id = u.get("dealer_id")
    if not firma_loeschen:
        raise HTTPException(409,
            "Das ist der Händler-Hauptaccount. Zum Sperren bitte "
            "'Deaktivieren' verwenden. Soll die FIRMA komplett gelöscht "
            "werden (alle Nutzer, Fahrzeuge, Verträge, Termine), zuerst "
            f"die Löschvorschau ansehen (/admin/dealers/{dealer_id}/"
            "loeschvorschau) und dann mit ?firma_loeschen=true bestätigen.")

    # Runde 12: Audit VOR dem ersten destruktiven Schritt. Bricht die
    # Loeschung mittendrin ab, steht sonst nirgends, wer sie ausgeloest hat.
    await log_activity(admin.get("dealer_id", ""), admin["id"],
                       "admin.firma.loeschung.gestartet", ref=dealer_id or user_id,
                       meta={"email": u.get("email", ""), "dealer_id": dealer_id})
    geloescht = {}
    if dealer_id:
        # Beweis-Snapshots bleiben BEWUSST stehen: Snapshots sind
        # haendlerneutral geteilt (der erste Snapshot einer Anzeige wird
        # von allen uebernommen) — eine Loeschung ueber die Fahrzeug-ID
        # ('v_<Anzeigen-ID>', bei mehreren Haendlern identisch) wuerde die
        # Beweisarchive ANDERER Firmen zerstoeren. Sie enthalten nur
        # oeffentliche Inseratsdaten und verfallen ueber die
        # SNAPSHOT_RETENTION_DAYS-Aufraeumlogik.
        #
        # Nachpruefung Runde 14 (Befund 58): Grabstein am Firmenprofil
        # (Muster cleanup_service.vertrag_endgueltig_loeschen) — die
        # Firma ist ab jetzt als "in Loeschung" erkennbar; dealers wird
        # weiterhin als Letztes entfernt, users (in _COMPANY_COLLECTIONS)
        # erst nach allen Nebendaten.
        jetzt = now_iso()
        await db.dealers.update_one(
            {"id": dealer_id},
            {"$set": {"loeschung": {"status": "laeuft", "gestartet": jetzt,
                                    "grund": "admin", "durch": admin["id"]},
                      "updated_at": jetzt}})
        # Nachpruefung Runde 14 (Befund 12): password_resets tragen user_id,
        # kein dealer_id — die Konto-IDs der Firma VOR dem Loeschen der
        # users einsammeln, sonst bleiben die Reset-Dokumente (token_hash,
        # requested_ip) bis zum TTL-Ablauf (7 Tage) stehen.
        user_ids = [x["id"] async for x in db.users.find(
            {"dealer_id": dealer_id}, {"_id": 0, "id": 1})]
        for coll in _COMPANY_COLLECTIONS:
            res = await db[coll].delete_many({"dealer_id": dealer_id})
            if res.deleted_count:
                geloescht[coll] = res.deleted_count
        if user_ids:
            res = await db.password_resets.delete_many({"user_id": {"$in": user_ids}})
            if res.deleted_count:
                geloescht["password_resets"] = res.deleted_count
        # Runde 13: B8 — die Snapshot-Zeilen bleiben (Beweiszweck), aber die
        # Zuordnung "welche Firma, welcher Nutzer hat gesichert" wird
        # pseudonymisiert; vorher blieben dealer_id/user_id unbegrenzt stehen.
        from snapshot_service import snapshots_pseudonymisieren
        n_snap = await snapshots_pseudonymisieren(db, dealer_id=dealer_id)
        if n_snap:
            geloescht["listing_snapshots_pseudonymisiert"] = n_snap
        # GESPEICHERTE DATEIEN der Firma mitloeschen (DSGVO): Unterschriften
        # + Protokoll-PDFs (protocol/), Abhol-/Schadenfotos (pickup/),
        # Inserats-Fotos (resale/), Logo (logo/). Beweis-Snapshots bleiben
        # bewusst (haendlerneutral geteilt, s.o.). Fehler beim Dateiloeschen
        # brechen die Kontoloeschung nicht ab, werden aber ausgewiesen.
        from storage_service import storage
        dateien = 0
        datei_fehler = []
        import asyncio as _asyncio
        for kategorie in ("protocol", "pickup", "resale", "logo"):
            try:
                dateien += await _asyncio.to_thread(
                    storage.delete_prefix, f"{kategorie}/{dealer_id}/")
            except Exception as exc:
                datei_fehler.append(f"{kategorie}: {exc}")
        geloescht["dateien"] = dateien
        if datei_fehler:
            geloescht["datei_fehler"] = datei_fehler
            # Runde 5: nicht stillschweigend "ok" — Wiederholung einplanen
            # (cleanup_service.storage_loeschungen_nachholen) und im
            # Fehlerarchiv sichtbar machen.
            for kategorie in ("protocol", "pickup", "resale", "logo"):
                await db.storage_delete_retry.update_one(
                    {"prefix": f"{kategorie}/{dealer_id}/"},
                    {"$setOnInsert": {"id": str(uuid.uuid4()),
                                      "prefix": f"{kategorie}/{dealer_id}/",
                                      "dealer_id": dealer_id,
                                      "created_at": now_iso()},
                     "$set": {"letzter_fehler": "; ".join(datei_fehler)[:300]}},
                    upsert=True)
            await db.error_logs.insert_one({
                "id": str(uuid.uuid4()), "source": "backend", "method": "DELETE",
                "path": f"/api/admin/users/{user_id}", "error_type": "StorageDelete",
                "message": "Dateien der geloeschten Firma konnten nicht (vollstaendig) "
                           "entfernt werden — Wiederholung eingeplant: "
                           + "; ".join(datei_fehler)[:600],
                "traceback": "", "ip": "", "status": "open",
                "created_at": now_iso()})
        await db.dealers.delete_many({"id": dealer_id})
    else:
        await db.users.delete_one({"id": user_id})

    await log_activity(admin.get("dealer_id", ""), admin["id"],
                       "admin.firma.geloescht", ref=dealer_id or user_id,
                       meta={"email": u.get("email", ""),
                             "geloescht": geloescht})
    return {"ok": True, "geloescht": geloescht or "nur_nutzer"}


@router.post("/admin/users/{user_id}/active")
async def admin_user_set_active(
    user_id: str, body: AdminActiveIn, admin=Depends(current_super_admin)
):
    """Soft-Block / Entsperren eines Nutzer-Kontos.

    Sperren = active=False. Der User existiert weiterhin, kann sich aber
    nicht mehr einloggen (siehe /auth/login). Die laufende Session wird
    invalidiert, indem `current_session_id` zurueckgesetzt wird.
    """
    u = await db.users.find_one({"id": user_id})
    if not u:
        raise HTTPException(404, "Nutzer nicht gefunden")
    if u.get("is_super_admin") and not body.active:
        raise HTTPException(400, "Super-Admin kann nicht gesperrt werden")
    # Dieselbe Regel wie bei Passwort/PUT (Prüfbericht Runde 4): Admin-Konten
    # sperrt/entsperrt nur der Super-Admin — nicht ein Admin-Kollege.
    if u.get("role") == "admin" and u.get("id") != admin.get("id") \
            and not admin.get("is_super_admin"):
        raise HTTPException(403, "Admin-Konten verwaltet nur der Super-Admin")
    if u.get("id") == admin.get("id") and not body.active:
        raise HTTPException(400, "Du kannst dich nicht selbst sperren")
    patch = {"active": bool(body.active), "updated_at": now_iso()}
    if not body.active:
        patch["current_session_id"] = None
    await db.users.update_one({"id": user_id}, {"$set": patch})
    if not body.active and u.get("role") == "b2b_buyer":
        # Nachpruefung Runde 14 (Befund 1): Ein gesperrter Kaeufer darf keine
        # laufende Verhandlung behalten — sonst koennte der Haendler seine
        # alte Anfrage spaeter noch annehmen und fuer ihn reservieren.
        await db.listing_interest.update_many(
            {"buyer_user_id": user_id,
             "status": {"$in": ["offen", "gegenangebot", "gegenangebot_kaeufer"]}},
            {"$set": {"status": "abgelehnt", "beendet_grund": "kaeufer_gesperrt",
                      "updated_at": now_iso()},
             "$push": {"history": {"von": "system", "aktion": "kaeufer_gesperrt",
                                   "zeit": now_iso()}}})
    sucher_abgemeldet = 0
    if not body.active and u.get("role") == "dealer" and u.get("dealer_id"):
        # Firmensperre (Audit 09/2026): auch die Sitzungen aller Sucher der
        # Firma sofort widerrufen — vorher wurden alte Sucher-Tokens nach
        # dem Entsperren wieder gueltig (auch gestohlene).
        r = await db.users.update_many(
            {"dealer_id": u["dealer_id"], "role": "sucher"},
            {"$set": {"current_session_id": None, "updated_at": now_iso()}})
        sucher_abgemeldet = r.modified_count
    await log_activity(admin.get("dealer_id", ""), admin["id"],
                       "admin.user.entsperrt" if body.active else "admin.user.gesperrt",
                       ref=user_id, meta={"email": u.get("email", ""),
                                          "sucher_abgemeldet": sucher_abgemeldet})
    return {"ok": True, "active": bool(body.active),
            "sucher_abgemeldet": sucher_abgemeldet}


@router.post("/admin/users/{user_id}/password")
async def admin_user_set_password(
    user_id: str, body: AdminUserPasswordIn, admin=Depends(current_super_admin)
):
    """Setzt das Passwort eines Nutzers zurueck (Admin-Funktion)."""
    # (Passwortregel bereits im Modell AdminUserPasswordIn)
    u = await db.users.find_one({"id": user_id})
    if not u:
        raise HTTPException(404, "Nutzer nicht gefunden")
    # Dieselben Regeln wie bei der PUT-Route (Pruefbericht Runde 4: hier
    # fehlte der Schutz — ein normaler Admin konnte das Super-Admin-Passwort
    # setzen und die Plattform uebernehmen).
    if u.get("is_super_admin") and u.get("id") != admin.get("id"):
        raise HTTPException(403, "Das Super-Admin-Passwort ändert nur der "
                                 "Super-Admin selbst")
    if u.get("role") == "admin" and u.get("id") != admin.get("id") \
            and not admin.get("is_super_admin"):
        raise HTTPException(403, "Admin-Konten verwaltet nur der Super-Admin")
    await db.users.update_one(
        {"id": user_id},
        {"$set": {
            "password_hash": await hash_password_async(body.new_password),
            "current_session_id": None,
            "updated_at": now_iso(),
        }},
    )
    await log_activity(admin.get("dealer_id", ""), admin["id"],
                       "admin.passwort.zurueckgesetzt",
                       ref=user_id, meta={"email": u.get("email", "")})
    return {"ok": True}


# ---------- Fahrer-Verwaltung (Review 09/2026: fehlte komplett) ----------
# Fahrer-Konten sind firmenneutral (kein dealer_id) — die Liste ist
# plattformweit; Firmenzugehoerigkeit ergibt sich aus dealer_drivers.
@router.get("/admin/drivers")
async def admin_list_drivers(_=Depends(current_admin)):
    fahrer = await db.driver_accounts.find(
        {}, {"_id": 0, "password_hash": 0, "current_session_id": 0},
    ).sort("created_at", -1).to_list(2000)
    links: Dict[str, dict] = {}
    async for row in db.dealer_drivers.aggregate([
        {"$group": {"_id": "$driver_account_id", "n": {"$sum": 1},
                    "dealer_ids": {"$addToSet": "$dealer_id"}}},
    ]):
        links[row["_id"]] = row
    dealer_ids = sorted({d for r in links.values() for d in r.get("dealer_ids") or []})
    namen = {}
    if dealer_ids:
        async for d in db.dealers.find({"id": {"$in": dealer_ids}},
                                       {"_id": 0, "id": 1, "company_name": 1}):
            namen[d["id"]] = d.get("company_name") or d["id"][:8]
    termine: Dict[str, dict] = {}
    async for row in db.appointments.aggregate([
        {"$match": {"driver_id": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$driver_id", "n": {"$sum": 1},
                    "offen": {"$sum": {"$cond": [
                        {"$in": ["$status", ["offen", "verschoben"]]}, 1, 0]}}}},
    ]):
        termine[row["_id"]] = row
    return [{**f,
             "firmen": sorted(namen.get(d, d[:8])
                              for d in (links.get(f["id"], {}).get("dealer_ids") or [])),
             "verknuepfungen": links.get(f["id"], {}).get("n", 0),
             "termine": termine.get(f["id"], {}).get("n", 0),
             "termine_offen": termine.get(f["id"], {}).get("offen", 0)}
            for f in fahrer]


async def _fahrer_or_404(driver_id: str) -> dict:
    d = await db.driver_accounts.find_one({"id": driver_id}, {"_id": 0})
    if not d:
        raise HTTPException(404, "Fahrer nicht gefunden")
    return d


@router.post("/admin/drivers/{driver_id}/active")
async def admin_driver_set_active(driver_id: str, body: AdminActiveIn,
                                  admin=Depends(current_super_admin)):
    d = await _fahrer_or_404(driver_id)
    # Audit 13.09.2026 (#59): Ein halb geloeschtes Konto (Grabstein aus
    # admin_delete_driver) nicht wieder entsperren — sonst hebt ein Klick die
    # Sperre auf, waehrend Termine/Berichte schon pseudonymisiert sind.
    if body.active and (d.get("loeschung") or {}).get("status") == "laeuft":
        raise HTTPException(409, "Löschung läuft — bitte 'Löschen' erneut "
                                 "ausführen, um sie abzuschließen")
    jetzt = now_iso()
    fields = {"active": body.active, "updated_at": jetzt}
    if not body.active:
        # Sperren beendet die laufende Sitzung sofort (Single-Session strikt).
        fields["current_session_id"] = None
    filt_konto = {"id": driver_id}
    if body.active:
        # Nachbesserung #59: Die Pruefung oben ist Lesen-dann-Schreiben. Setzt
        # ein paralleler DELETE den Grabstein dazwischen, darf dieser Write das
        # Konto nicht wieder aktivieren — Bedingung im Filter (CAS).
        filt_konto["loeschung.status"] = {"$ne": "laeuft"}
    r_konto = await db.driver_accounts.update_one(filt_konto, {"$set": fields})
    if r_konto.matched_count == 0:
        if not await db.driver_accounts.find_one({"id": driver_id}, {"_id": 1}):
            raise HTTPException(404, "Fahrer nicht gefunden")
        raise HTTPException(409, "Löschung läuft — bitte 'Löschen' erneut "
                                 "ausführen, um sie abzuschließen")
    termine_getrennt = 0
    if not body.active:
        # Nachpruefung Runde 14 (Befund 17): offene Fahrten (offen/
        # bestaetigt/verschoben/in Bearbeitung, leerer Status = offen) vom
        # gesperrten Fahrer trennen — er kommt nicht mehr an die Fahrer-
        # App, und der Firmenkalender zeigt keinen Sperrstatus: die Fahrten
        # hingen unsichtbar an einem Fahrer, der sie nie abholt. Dieselbe
        # Regel wie DELETE /drivers/{id} (routes/drivers.py): Zuweisung weg,
        # offene Annahme-Anfrage verworfen (zuteilung=None), der Chef
        # teilt neu zu. Abgeschlossene Fahrten behalten ihre Zuordnung.
        # Jede betroffene Firma bekommt einen Eintrag in ihrem Audit-Log,
        # damit der Chef die neu zuzuteilenden Termine findet.
        from routes.drivers import _OFFEN_WERTE
        filt = {"driver_id": driver_id, "status": {"$in": _OFFEN_WERTE}}
        betroffen = [a async for a in db.appointments.find(
            filt, {"_id": 0, "id": 1, "dealer_id": 1})]
        if betroffen:
            r = await db.appointments.update_many(
                filt, {"$unset": {"driver_id": ""},
                       "$set": {"zuteilung": None, "updated_at": jetzt}})
            termine_getrennt = r.modified_count
            je_firma: Dict[str, list] = {}
            for a in betroffen:
                je_firma.setdefault(a.get("dealer_id") or "", []).append(a["id"])
            for firma_id, termin_ids in je_firma.items():
                await log_activity(firma_id, admin["id"],
                                   "fahrer.gesperrt.termine_freigegeben",
                                   ref=driver_id,
                                   meta={"driver_code": d.get("driver_code", ""),
                                         "termine": termin_ids})
    await log_activity(admin.get("dealer_id", ""), admin["id"],
                       "admin.fahrer.entsperrt" if body.active else "admin.fahrer.gesperrt",
                       ref=driver_id, meta={"email": d.get("email", ""),
                                            "offene_termine_getrennt": termine_getrennt})
    return {"ok": True, "active": body.active,
            "offene_termine_getrennt": termine_getrennt}


@router.post("/admin/drivers/{driver_id}/password")
async def admin_driver_set_password(driver_id: str, body: AdminUserPasswordIn,
                                    admin=Depends(current_super_admin)):
    d = await _fahrer_or_404(driver_id)
    # Gleiche Staerke-Regel wie bei der Fahrer-Registrierung (wirft
    # ValueError -> sauberer 400 statt 500).
    from routes.drivers import _check_password_strength
    try:
        _check_password_strength(body.new_password or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    await db.driver_accounts.update_one(
        {"id": driver_id},
        {"$set": {"password_hash": await hash_password_async(body.new_password),
                  "current_session_id": None, "updated_at": now_iso()}})
    await log_activity(admin.get("dealer_id", ""), admin["id"],
                       "admin.fahrer.passwort.zurueckgesetzt",
                       ref=driver_id, meta={"email": d.get("email", "")})
    return {"ok": True}


@router.delete("/admin/drivers/{driver_id}")
async def admin_delete_driver(driver_id: str, admin=Depends(current_super_admin)):
    """Fahrer-Konto loeschen (DSGVO — vorher gab es dafuer KEINEN Weg):
    Haendler-Verknuepfungen entfernen, OFFENE Termine vom Fahrer trennen
    (abgeschlossene behalten die historische Zuordnung — nach der Loeschung
    ist die driver_id keiner Person mehr zuzuordnen), Reset-Tokens weg,
    dann das Konto selbst."""
    d = await _fahrer_or_404(driver_id)
    # Audit 13.09.2026 (#59): Grabstein und Sperre ZUERST, im Muster von
    # admin_delete_user. Vorher lief die Pseudonymisierung (sieben getrennte
    # Schreibschritte, update_many/delete_many sind keine retryable writes)
    # ohne Sperre und ohne Marker: brach sie ab, blieb der Fahrer aktiv und
    # eingeloggt, und nichts meldete den Mischzustand. Jetzt weisen
    # current_driver (401), driver_login (403) und add_driver_by_code (409)
    # sofort ab, und ein erneuter DELETE fuehrt die Loeschung zu Ende.
    jetzt = now_iso()
    grab = d.get("loeschung") or {}
    wiederaufnahme = grab.get("status") == "laeuft"
    if wiederaufnahme:
        await db.driver_accounts.update_one(
            {"id": driver_id},
            {"$set": {"loeschung.gestartet": jetzt, "active": False,
                      "current_session_id": None, "updated_at": jetzt},
             "$inc": {"loeschung.wiederaufnahmen": 1}})
    else:
        await db.driver_accounts.update_one(
            {"id": driver_id},
            {"$set": {"loeschung": {"status": "laeuft", "gestartet": jetzt,
                                    "grund": "admin", "durch": admin["id"]},
                      "active": False, "current_session_id": None,
                      "updated_at": jetzt}})
    # Start-Audit VOR dem ersten destruktiven Schritt mit der WERFENDEN
    # Variante (wie admin.firma.loeschung.gestartet): scheitert es, wird
    # nichts pseudonymisiert. Ohne E-Mail in meta.
    await log_activity(admin.get("dealer_id", ""), admin["id"],
                       "admin.fahrer.loeschung.gestartet", ref=driver_id,
                       meta={"wiederaufnahme": wiederaufnahme})
    # Audit 09/2026: nicht nur trennen, sondern pseudonymisieren (Termine,
    # Berichte, Protokolle, Audit-Log) — Funktion in routes/drivers.py.
    # Bewusst akzeptiertes Restrisiko: Ein Fahrer-Bericht, der VOR der Sperre
    # begann und erst nach delete_one gespeichert wird, traegt noch Kennung
    # und Klarnamen (selten, braeuchte eine Nachpruefung in submit_report).
    from routes.drivers import fahrer_konto_anonymisieren
    anonym = await fahrer_konto_anonymisieren(db, driver_id)
    links = type("R", (), {"deleted_count": anonym.get("dealer_drivers", 0)})()
    getrennt = type("R", (), {"modified_count": anonym.get("appointments", 0)})()
    await db.driver_accounts.delete_one({"id": driver_id})
    # Konto ist geloescht — ein Audit-Fehler darf keinen 500 mit 404-Retry
    # ("Fahrer nicht gefunden") mehr ausloesen.
    await log_activity_sicher(admin.get("dealer_id", ""), admin["id"],
                              "admin.fahrer.geloescht", ref=driver_id,
                              meta={"email": d.get("email", ""),
                                    "driver_code": d.get("driver_code", ""),
                                    "verknuepfungen": links.deleted_count,
                                    "offene_termine_getrennt": getrennt.modified_count,
                                    "wiederaufnahme": wiederaufnahme})
    return {"ok": True, "verknuepfungen_entfernt": links.deleted_count,
            "offene_termine_getrennt": getrennt.modified_count}


# ---------- Contracts (read-only admin views) ----------
# Audit 13.09.2026 (#51/#52): Obergrenze der Admin-Vertragslisten. Sie wird
# signalisiert (X-Truncated bzw. "abgeschnitten") statt still gezogen.
ADMIN_VERTRAEGE_MAX = 2000


@router.get("/admin/contracts")
async def admin_all_contracts(response: Response, _=Depends(current_admin),
                              page: int = 1, limit: int = ADMIN_VERTRAEGE_MAX):
    """Alle Vertraege plattformweit, neueste zuerst (ohne PDF-Bytes).

    Audit 13.09.2026 (#52): Vorher kappte to_list(2000) ohne jedes Signal.
    Jetzt blaettern per ?page=&limit= (Standard wie bisher: erste 2000) und
    Kopfzeile X-Truncated: "1", wenn es weitere Vertraege gibt. Die Antwort
    bleibt eine Liste."""
    limit = max(1, min(int(limit or ADMIN_VERTRAEGE_MAX), ADMIN_VERTRAEGE_MAX))
    # Nachbesserung #52: page auch nach oben begrenzen — sonst sprengt
    # (page - 1) * limit int64 und pymongo wirft OverflowError (500).
    page = max(1, min(int(page or 1), 10 ** 6))
    items = await db.generated_pdfs.find(
        {}, {"_id": 0, "pdf_b64": 0, "pdf_digital_b64": 0},
    ).sort("created_at", -1).skip((page - 1) * limit).to_list(limit + 1)
    abgeschnitten = len(items) > limit
    response.headers["X-Truncated"] = "1" if abgeschnitten else "0"
    if abgeschnitten:
        log.warning("Admin-Vertragsliste Seite %s auf %s Eintraege gekuerzt "
                    "(weitere per ?page=)", page, limit)
    return items[:limit]


@router.get("/admin/users/{user_id}/contracts")
async def admin_user_contracts(user_id: str, _=Depends(current_admin)):
    """Listet die Verträge eines Nutzers auf (read-only).
    Chef (role dealer): alle Verträge seiner Firma (umfang "firma"); jedes
    andere Konto: nur die selbst erzeugten (umfang "nutzer").
    Enthält keine PDF-Bytes — nur Metadaten + extrahierte Vertragsdaten,
    damit die Liste schnell lädt. PDF kann separat über
    /api/admin/contracts/{id}/pdf abgerufen werden (falls benötigt).
    """
    user = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0, "mfa": 0,
                                                     "current_session_id": 0})
    if not user:
        raise HTTPException(404, "Nutzer nicht gefunden")
    # Firmen-Kopf (Wunsch 09/2026): Firmenname + Kundennummer mitliefern.
    if user.get("dealer_id"):
        firma = await db.dealers.find_one(
            {"id": user["dealer_id"]},
            {"_id": 0, "company_name": 1, "kunden_nr": 1})
        if firma:
            user.setdefault("company_name", firma.get("company_name"))
            user["kunden_nr"] = firma.get("kunden_nr")
    # Audit 13.09.2026 (#51): nach Rolle getrennt (Definition wie
    # contracts._vertrag_bereich). Vorher griff beim Sucher der Zweig
    # {dealer_id: <Firma>} und zeigte ALLE Firmenvertraege als "seine";
    # Konten ohne Firma filterten auf {dealer_id: None}.
    if user.get("role") == "dealer" and user.get("dealer_id"):
        filt, umfang = {"dealer_id": user["dealer_id"]}, "firma"
    else:
        filt, umfang = {"user_id": user_id}, "nutzer"
    items = await db.generated_pdfs.find(
        filt, {"_id": 0, "pdf_b64": 0, "pdf_digital_b64": 0},
    ).sort("created_at", -1).to_list(ADMIN_VERTRAEGE_MAX + 1)
    abgeschnitten = len(items) > ADMIN_VERTRAEGE_MAX
    if abgeschnitten:
        log.warning("Admin-Vertragsansicht %s (%s) auf %s Eintraege gekuerzt",
                    user_id, umfang, ADMIN_VERTRAEGE_MAX)
    return {"user": user, "contracts": items[:ADMIN_VERTRAEGE_MAX],
            "umfang": umfang, "abgeschnitten": abgeschnitten}


@router.get("/admin/contracts/{contract_id}/pdf")
async def admin_contract_pdf(contract_id: str, _=Depends(current_admin)):
    """Liefert das PDF eines beliebigen Vertrags an den Admin (read-only)."""
    doc = await db.generated_pdfs.find_one({"id": contract_id})
    if not doc or not doc.get("pdf_b64"):
        raise HTTPException(404, "Vertrag oder PDF nicht gefunden")
    pdf_bytes = base64.b64decode(doc["pdf_b64"])
    raw_name = doc.get("filename") or f"vertrag_{contract_id}.pdf"
    filename = _safe_filename(raw_name, fallback=f"vertrag_{contract_id}.pdf")
    return StreamingResponse(
        iter([pdf_bytes]), media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ---------- Stats ----------
@router.get("/admin/stats")
async def admin_stats(_=Depends(current_admin)):
    return {
        "users": await db.users.count_documents({}),
        # "aktiv" heisst: Status aktiv UND nicht abgelaufen (PR-Review 09/2026)
        "active_subs": await db.subscriptions.count_documents(
            {"status": "active",
             "$or": [{"expires_at": None}, {"expires_at": {"$gt": now_iso()}}]}),
        "contracts": await db.generated_pdfs.count_documents({}),
        "appointments": await db.appointments.count_documents({}),
        "comparisons_today": await db.vehicle_comparisons.count_documents({
            "created_at": {"$gte": datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).isoformat()}
        }),
        "open_errors": await db.error_logs.count_documents({"status": "open"}),
    }


# ---------- Audit-Log ----------
@router.get("/admin/audit")
async def admin_audit_log(
    limit: int = 200, action: Optional[str] = None, q: Optional[str] = None,
    _=Depends(current_admin),
):
    """Audit-Trail der Plattform: Logins, Registrierungen, Verträge, Termine,
    Admin-Aktionen. Quelle ist die activity_logs-Collection; Einträge werden
    mit Nutzer-E-Mail/Firma angereichert, damit die Liste lesbar ist."""
    limit = max(1, min(int(limit or 200), 1000))
    query: dict = {}
    if action:
        query["action"] = {"$regex": f"^{re.escape(action)}"}
    items = await db.activity_logs.find(query, {"_id": 0}) \
        .sort("created_at", -1).to_list(limit * 3 if q else limit)

    # E-Mail/Firma nachschlagen (ein Batch-Lookup statt N Einzel-Queries).
    user_ids = {i.get("user_id") for i in items if i.get("user_id")}
    users = await db.users.find(
        {"id": {"$in": list(user_ids)}},
        {"_id": 0, "id": 1, "email": 1, "username": 1, "role": 1},
    ).to_list(len(user_ids) or 1)
    by_id = {u["id"]: u for u in users}
    out = []
    for i in items:
        u = by_id.get(i.get("user_id")) or {}
        entry = {
            **i,
            "email": u.get("email") or (i.get("meta") or {}).get("email")
                     or (i.get("meta") or {}).get("identifier") or "",
            "username": u.get("username", ""),
            "role": u.get("role", ""),
        }
        if q:
            hay = " ".join(str(v) for v in (
                entry.get("email"), entry.get("action"), entry.get("ref"),
                str(entry.get("meta") or ""),
            )).lower()
            if q.lower() not in hay:
                continue
        out.append(entry)
        if len(out) >= limit:
            break
    return out


# ---------- Fehler-Meldungen (Error-Reporting an den Admin) ----------
@router.get("/admin/errors")
async def admin_errors(
    status: Optional[str] = None, limit: int = 200, _=Depends(current_admin),
):
    """Alle vom Server erfassten Fehler (unbehandelte Exceptions + gemeldete
    Frontend-Fehler), neueste zuerst. status: open | resolved | (alle)."""
    limit = max(1, min(int(limit or 200), 1000))
    query: dict = {}
    if status in ("open", "resolved"):
        query["status"] = status
    return await db.error_logs.find(query, {"_id": 0}) \
        .sort("created_at", -1).to_list(limit)


@router.put("/admin/errors/{error_id}")
async def admin_resolve_error(
    error_id: str, body: dict = Body(default={}), admin=Depends(current_admin),
):
    """Fehler als erledigt (oder wieder offen) markieren."""
    new_status = body.get("status", "resolved")
    if new_status not in ("open", "resolved"):
        raise HTTPException(400, "status muss 'open' oder 'resolved' sein")
    r = await db.error_logs.update_one(
        {"id": error_id},
        {"$set": {"status": new_status, "resolved_by": admin.get("email", ""),
                  "resolved_at": now_iso() if new_status == "resolved" else None}},
    )
    if not r.matched_count:
        raise HTTPException(404, "Fehler-Eintrag nicht gefunden")
    return {"ok": True, "status": new_status}


@router.delete("/admin/errors")
async def admin_clear_resolved_errors(_=Depends(current_admin)):
    """Alle als erledigt markierten Fehler löschen (Aufräumen)."""
    r = await db.error_logs.delete_many({"status": "resolved"})
    return {"ok": True, "deleted": r.deleted_count}


# ---------- Monitoring (Priorität 5 — Betriebsueberwachung) ----------
@router.get("/admin/monitoring")
async def admin_monitoring(admin=Depends(current_admin)):
    """Betriebszustand auf einen Blick: unerwartete Fehler, Job-Rueckstau,
    Anbieter-Slots, Zahlen fuer Alarme. Gedacht fuer den Admin-Bereich UND
    fuer externe Ueberwachung (z.B. Uptime-Robot auf die Kennzahlen)."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    vor_1h = (now - timedelta(hours=1)).isoformat()

    # Unerwartete 500er der letzten Stunde (error_logs schreibt der
    # globale Exception-Handler)
    fehler_1h = await db.error_logs.count_documents(
        {"created_at": {"$gte": vor_1h}})

    # Linkpruefungs-Jobs: Rueckstau + Alter des aeltesten wartenden Jobs
    queued = await db.link_jobs.count_documents({"status": "queued"})
    processing = await db.link_jobs.count_documents({"status": "processing"})
    failed_1h = await db.link_jobs.count_documents(
        {"status": "failed", "updated_at": {"$gte": now - timedelta(hours=1)}})
    aeltester = await db.link_jobs.find_one(
        {"status": "queued"}, {"_id": 0, "created_at": 1},
        sort=[("created_at", 1)])
    wartezeit_s = None
    if aeltester and aeltester.get("created_at"):
        ca = aeltester["created_at"]
        if ca.tzinfo is None:
            ca = ca.replace(tzinfo=timezone.utc)
        wartezeit_s = int((now - ca).total_seconds())

    # Anbieter-Slots (zentrale Begrenzung)
    slots = {row["provider"]: row.get("active", 0)
             async for row in db.provider_limits.find({}, {"_id": 0})}
    heute = now.strftime("%Y-%m-%d")
    abrufe_heute = {row["provider"]: row.get("calls", 0)
                    async for row in db.provider_stats.find(
                        {"date": heute}, {"_id": 0})}

    # Ampel: gruen | gelb | rot — einfache Schwellen fuer Alarme
    ampel = "gruen"
    hinweise = []
    if fehler_1h > 0:
        ampel = "gelb"
        hinweise.append(f"{fehler_1h} unerwartete Fehler in der letzten Stunde")
    if queued > 50 or (wartezeit_s or 0) > 300:
        ampel = "gelb"
        hinweise.append("Job-Rueckstau (Warteschlange/Wartezeit erhoeht)")
    if fehler_1h > 20 or (wartezeit_s or 0) > 900:
        ampel = "rot"
    return {
        "ampel": ampel, "hinweise": hinweise, "zeitpunkt": now.isoformat(),
        "unerwartete_fehler_letzte_stunde": fehler_1h,
        "link_jobs": {"queued": queued, "processing": processing,
                      "failed_letzte_stunde": failed_1h,
                      "aeltester_wartender_sekunden": wartezeit_s},
        "anbieter_slots_aktiv": slots,
        "anbieter_abrufe_heute": abrufe_heute,
    }


# ---------- Verkaufspakete (Phase 2 — Vergabe durch den Admin) ----------
@router.put("/admin/dealers/{dealer_id}/sale-plan")
async def admin_set_sale_plan(dealer_id: str, body: dict = Body(...),
                              admin=Depends(current_super_admin)):
    """Verkaufspaket zuweisen/ändern. tier: s5|s10|s20|s30|s40|enterprise
    oder null zum Entfernen. Bei Neuvergabe/Wechsel startet der rollierende
    Abrechnungszeitraum neu (Buchungsdatum)."""
    from routes.team import SALE_PLANS
    dealer = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "id": 1, "sale_plan": 1})
    if not dealer:
        raise HTTPException(404, "Händler nicht gefunden")
    tier = body.get("tier")
    if tier is None:
        await db.dealers.update_one({"id": dealer_id}, {"$unset": {"sale_plan": ""}})
        await log_activity(admin.get("dealer_id", ""), admin["id"],
                           "admin.verkaufsplan.entfernt", ref=dealer_id)
        return {"ok": True, "sale_plan": None}
    if tier not in SALE_PLANS:
        raise HTTPException(400, f"Unbekanntes Paket: {tier}")
    old = (dealer.get("sale_plan") or {})
    plan = {
        "tier": tier,
        # Zeitraum bleibt bei reiner Quota-Erhöhung erhalten, startet aber
        # neu, wenn vorher kein Paket existierte.
        "period_start": old.get("period_start") or now_iso(),
    }
    # Laufzeit: months=N begrenzt das Paket auf N x 30 Tage. Verlaengerung
    # rechnet ab dem bisherigen Ablauf weiter (nicht ab heute), damit dem
    # Haendler keine bezahlte Restlaufzeit verloren geht. Ohne months bleibt
    # ein bestehendes Ablaufdatum erhalten; gab es keins, ist das Paket
    # unbefristet (wie bisher).
    months = body.get("months")
    if months:
        try:
            months = max(1, min(24, int(months)))
        except (TypeError, ValueError):
            raise HTTPException(400, "months muss eine Zahl (1–24) sein")
        base = datetime.now(timezone.utc)
        old_vu = old.get("valid_until")
        if old_vu:
            try:
                prev = datetime.fromisoformat(old_vu)
                if prev.tzinfo is None:
                    prev = prev.replace(tzinfo=timezone.utc)
                if prev > base:
                    base = prev
            except (TypeError, ValueError):
                pass
        plan["valid_until"] = (base + timedelta(days=30 * months)).isoformat()
    elif old.get("valid_until"):
        plan["valid_until"] = old["valid_until"]
    if tier == "enterprise":
        # Nachpruefung Runde 14 (Befund 32): ungueltige Eingabe ("abc",
        # "1o0", negativ) wurde still zu custom_quota=None — mit
        # VERKAUF_KOSTENLOS=false hiess das quota=0, und resale.py
        # ueberspringt bei 0 die Kontingentpruefung (= unbegrenzt). Jetzt
        # 400; leer/None bleibt erlaubt (Enterprise ohne feste Zahl).
        cq = body.get("custom_quota")
        if cq is None or (isinstance(cq, str) and cq.strip() == ""):
            plan["custom_quota"] = None
        else:
            if isinstance(cq, bool):
                raise HTTPException(400, "custom_quota muss eine ganze Zahl sein")
            if isinstance(cq, float) and cq.is_integer():
                cq = int(cq)
            try:
                n = int(cq) if isinstance(cq, int) else int(str(cq).strip())
            except (TypeError, ValueError):
                raise HTTPException(400, "custom_quota muss eine ganze Zahl sein")
            if n < 0:
                raise HTTPException(400, "custom_quota darf nicht negativ sein")
            plan["custom_quota"] = n or None
    await db.dealers.update_one({"id": dealer_id}, {"$set": {"sale_plan": plan}})
    await log_activity(admin.get("dealer_id", ""), admin["id"],
                       "admin.verkaufsplan.gesetzt", ref=dealer_id,
                       meta={"tier": tier})
    return {"ok": True, "sale_plan": plan}


@router.get("/admin/plan-requests")
async def admin_plan_requests(status: Optional[str] = None,
                              type: Optional[str] = None,
                              _=Depends(current_super_admin)):
    # Runde 12: Freischaltungen sind Super-Admin-Sache (Oberflaeche:
    # superOnly). Firmenname, Ansprechpartner, E-Mail, Telefon und
    # Nachricht der Anfragen lasen vorher auch normale Admins.
    query: Dict = {}
    if status:
        query["status"] = status
    if type:
        query["type"] = type
    return await db.plan_requests.find(query, {"_id": 0}) \
        .sort("created_at", -1).to_list(200)


# ---------- Sucher-Abo freischalten (manuell) ----------
class AboFreischaltenIn(BaseModel):
    """Freischalten/Verlaengern (plan) oder Aufheben (plan=null).
    Audit 09/2026: feste Plantypen, echte Betragspruefung (kein stilles
    Ersetzen durch den Listenpreis, kein 0 EUR), Zahlungsart mit
    Pflichtbegruendung bei Kulanz."""
    plan: Optional[Literal["monthly", "yearly"]] = None
    gueltig_bis: Optional[str] = Field(default=None, max_length=30)
    betrag: Optional[Decimal] = Field(default=None, gt=Decimal("0"),
                                      le=Decimal("100000"))
    gezahlt_am: Optional[str] = Field(default=None, max_length=10)
    notiz: str = Field(default="", max_length=500)
    zahlungsart: Literal["rechnung_bezahlt", "kulanz"] = "rechnung_bezahlt"
    grund: str = Field(default="", max_length=300)
    waehrung: Literal["EUR"] = "EUR"


def _datum_pruefen_400(wert, feld: str) -> str:
    """'JJJJ-MM-TT' oder leer; sonst 400 (nicht 422, damit die Oberflaeche
    eine deutsche Meldung zeigt)."""
    if not wert:
        return ""
    w = str(wert).strip()[:10]
    try:
        datetime.strptime(w, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, f"{feld} muss ein Datum JJJJ-MM-TT sein")
    return w


@router.post("/admin/sucher/{sucher_id}/abo")
async def admin_set_sucher_abo(sucher_id: str, body: AboFreischaltenIn,
                               admin=Depends(current_super_admin)):
    """Sucher-Abo aktivieren/verlaengern (plan: monthly|yearly) oder mit
    plan=null aufheben. NUR Super-Admin (Betreiber). Legt einen
    idempotenten Vorgang (abo_vorgaenge) an — jeder Schritt ist
    wiederholbar, ein Reparaturlauf holt Abgebrochenes nach."""
    from routes.team import SUCHER_PLANS
    sucher = await db.users.find_one(
        {"id": sucher_id, "role": {"$in": ["sucher", "dealer"]}},
        {"_id": 0, "id": 1, "dealer_id": 1, "email": 1})
    if not sucher:
        raise HTTPException(404, "Sucher nicht gefunden")
    if body.plan is None:
        # Aufheben = NUR die kostenpflichtige Sucher-Funktion sperren
        # (Login/Bestand bleiben). Auch Lifetime wird damit inaktiv.
        await db.subscriptions.update_many(
            {"subject_user_id": sucher_id, "status": {"$in": ["active", "cancelled"]}},
            {"$set": {"status": "cancelled", "expires_at": now_iso(),
                      "aufgehoben_von": admin.get("email", ""),
                      "updated_at": now_iso()}})
        await log_activity(admin.get("dealer_id", ""), admin["id"],
                           "admin.sucher.abo.aufgehoben", ref=sucher_id,
                           meta={"grund": body.grund})
        return {"ok": True, "active": False}
    if body.plan not in SUCHER_PLANS:
        raise HTTPException(400, f"Unbekannter Abo-Zeitraum: {body.plan}")
    if body.zahlungsart == "kulanz" and not body.grund.strip():
        raise HTTPException(400, "Kulanz-Freischaltung braucht eine Begruendung")
    gezahlt_am = _datum_pruefen_400(body.gezahlt_am, "gezahlt_am")
    # Doppelklick-/Doppelanfrage-Schutz mit Besitzer-Token (Audit 09/2026):
    # geloest wird NUR die eigene Sperre; eine verwaiste Sperre wird per
    # Compare-and-Swap uebernommen, nie blind geloescht.
    sperre = f"abo:{sucher_id}"
    besitzer = str(uuid.uuid4())
    async with _sperre(sperre, besitzer):
        return await _abo_freischalten(sucher, sucher_id, body, gezahlt_am, admin)


SPERRE_FRIST_S = 45           # so lange gilt eine Sperre ohne Herzschlag
SPERRE_HERZSCHLAG_S = 15      # so oft wird sie waehrend der Arbeit verlaengert


@asynccontextmanager
async def _sperre(name: str, besitzer: str):
    """Sperre mit Ablauf und Herzschlag (Audit 09/2026).

    Vorher galt eine feste Frist von 60 s ab Beginn: dauerte eine
    Freischaltung laenger, konnte ein zweiter Vorgang die Sperre
    uebernehmen, waehrend der erste noch schrieb — doppelte
    Verlaengerungen und Zahlungen waren moeglich. Jetzt traegt die Sperre
    ein Ablaufdatum, das ein Hintergrund-Herzschlag alle 15 s
    weiterschiebt; uebernommen wird nur eine wirklich abgelaufene Sperre."""
    def _bis(sekunden: int) -> str:
        return (datetime.now(timezone.utc)
                + timedelta(seconds=sekunden)).isoformat()
    belegt = HTTPException(409, "Freischaltung laeuft gerade — bitte einen "
                                "Moment warten (Doppelklick)")
    try:
        await db.sperren.insert_one({"_id": name, "owner": besitzer,
                                     "seit": now_iso(),
                                     "bis": _bis(SPERRE_FRIST_S)})
    except DuplicateKeyError:
        alt = await db.sperren.find_one({"_id": name}) or {}
        # Alte Sperren ohne "bis": auf die frueheren 60 s ab "seit" abbilden.
        frist = alt.get("bis")
        if not frist and alt.get("seit"):
            try:
                frist = (datetime.fromisoformat(alt["seit"])
                         + timedelta(seconds=60)).isoformat()
            except ValueError:
                frist = None
        if frist and frist > now_iso():
            raise belegt
        r = await db.sperren.update_one(
            {"_id": name, "owner": alt.get("owner")},
            {"$set": {"owner": besitzer, "seit": now_iso(),
                      "bis": _bis(SPERRE_FRIST_S)}})
        if r.modified_count == 0:
            raise belegt

    async def _herzschlag():
        while True:
            await asyncio.sleep(SPERRE_HERZSCHLAG_S)
            r = await db.sperren.update_one(
                {"_id": name, "owner": besitzer},
                {"$set": {"bis": _bis(SPERRE_FRIST_S)}})
            if r.matched_count == 0:
                return                      # Sperre gehoert uns nicht mehr
    schlag = asyncio.create_task(_herzschlag())
    try:
        yield
    finally:
        schlag.cancel()
        try:
            await schlag
        except (asyncio.CancelledError, Exception):   # noqa: B014
            pass
        await db.sperren.delete_one({"_id": name, "owner": besitzer})


async def _abo_freischalten(sucher: dict, sucher_id: str, body: AboFreischaltenIn,
                            gezahlt_am: str, admin: dict) -> dict:
    from routes.team import SUCHER_PLANS
    plan = body.plan
    days = SUCHER_PLANS[plan]["days"]
    gueltig_bis = _gueltig_bis_parsen(body.gueltig_bis)
    if gueltig_bis:
        # Wunsch 09/2026: der Betreiber schreibt direkt "bis wann gueltig" —
        # ab dann sperrt die Sucher-Funktion automatisch (Abo-Ablaufpruefung).
        expires_at = gueltig_bis
    else:
        # Restlaufzeit erhalten: erneutes Freischalten verlaengert ab dem
        # bisherigen Ablauf, nicht ab "jetzt".
        basis = _restlaufzeit_basis(
            (await db.subscriptions.find_one(
                {"subject_user_id": sucher_id, "status": "active"},
                {"_id": 0, "expires_at": 1}) or {}).get("expires_at"))
        expires_at = (basis + timedelta(days=days)).isoformat()
    if body.zahlungsart == "kulanz":
        betrag = 0.0
    elif body.betrag is not None:
        betrag = round(float(body.betrag), 2)
    else:
        betrag = float(SUCHER_PLANS[plan]["price"])
    vorgang = {
        "id": str(uuid.uuid4()), "typ": "freischaltung",
        "subject_user_id": sucher_id, "dealer_id": sucher.get("dealer_id"),
        "plan": plan, "expires_at": expires_at, "betrag": betrag,
        "waehrung": "EUR", "zahlungsart": body.zahlungsart,
        "grund": body.grund.strip(), "gezahlt_am": gezahlt_am or now_iso()[:10],
        "notiz": body.notiz.strip(),
        "admin_id": admin["id"], "admin_email": admin.get("email", ""),
        "status": "laeuft", "schritte": {},
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    await db.abo_vorgaenge.insert_one(dict(vorgang))
    await _abo_vorgang_ausfuehren(vorgang)
    return {"ok": True, "active": True, "plan": plan, "expires_at": expires_at,
            "vorgang_id": vorgang["id"], "betrag": betrag}


async def _abo_vorgang_ausfuehren(v: dict) -> None:
    """Alle Schritte einer Freischaltung — jeder Schritt idempotent, damit
    ein Wiederholungslauf (nach Absturz/Timeout) nichts doppelt anlegt:
    1) alte Abos als 'ersetzt' markieren (Historie bleibt), 2) neues Abo mit
    id = Vorgangs-ID (Upsert), 3) genau EINE Zahlung je Vorgang (Upsert
    ueber vorgang_id), 4) offene Anfrage schliessen, 5) Audit einmalig,
    6) Vorgang fertig."""
    vid, sid, jetzt = v["id"], v["subject_user_id"], now_iso()
    await db.subscriptions.update_many(
        {"subject_user_id": sid, "id": {"$ne": vid}, "status": {"$ne": "ersetzt"}},
        {"$set": {"status": "ersetzt", "ersetzt_durch": vid, "updated_at": jetzt}})
    await db.subscriptions.update_one(
        {"id": vid},
        {"$setOnInsert": {
            "id": vid, "dealer_id": v.get("dealer_id"), "subject_user_id": sid,
            "plan": v["plan"], "status": "active", "expires_at": v["expires_at"],
            "price": v["betrag"], "vorgang_id": vid,
            "activated_by": v.get("admin_email", ""), "created_at": jetzt}},
        upsert=True)
    await db.manual_payments.update_one(
        {"vorgang_id": vid},
        {"$setOnInsert": {
            "id": str(uuid.uuid4()), "dealer_id": v.get("dealer_id"),
            "subject_user_id": sid, "plan": v["plan"],
            "amount": float(v["betrag"]), "currency": "EUR",
            "paid_at": v.get("gezahlt_am") or jetzt[:10],
            "period_until": v["expires_at"],       # bezahlt bis = Ablauf bei Freischaltung
            "note": v.get("notiz", ""),
            "zahlungsart": v.get("zahlungsart", "rechnung_bezahlt"),
            "kostenlos": v.get("zahlungsart") == "kulanz",
            "grund": v.get("grund", ""),
            "quelle": "manuell", "vorgang_id": vid,
            "recorded_by": v.get("admin_email", ""), "created_at": jetzt}},
        upsert=True)
    await db.plan_requests.update_many(
        {"type": "sucher_abo", "subject_user_id": sid, "status": "offen"},
        {"$set": {"status": "erledigt", "updated_at": jetzt,
                  "erledigt_durch": "freischaltung", "vorgang_id": vid}})
    if not (v.get("schritte") or {}).get("audit"):
        await log_activity(v.get("dealer_id", "") or "", v.get("admin_id", ""),
                           "admin.sucher.abo.freigeschaltet", ref=sid,
                           meta={"plan": v["plan"], "betrag": v["betrag"],
                                 "zahlungsart": v.get("zahlungsart"),
                                 "vorgang_id": vid})
        await db.abo_vorgaenge.update_one({"id": vid}, {"$set": {"schritte.audit": True}})
    await db.abo_vorgaenge.update_one(
        {"id": vid}, {"$set": {"status": "fertig", "updated_at": now_iso()}})


async def abo_vorgaenge_nachholen(db_=None) -> int:
    """Reparaturlauf: Vorgaenge, die vor >2 Minuten begonnen und nie
    'fertig' wurden (Absturz mitten im Freischalten), werden komplett
    wiederholt — alle Schritte sind idempotent."""
    frist = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    n = 0
    async for v in db.abo_vorgaenge.find({"status": "laeuft", "updated_at": {"$lt": frist}},
                                         {"_id": 0}).limit(100):
        try:
            await _abo_vorgang_ausfuehren(v)
            n += 1
        except Exception:
            log.exception("Abo-Vorgang %s konnte nicht nachgeholt werden", v.get("id"))
    return n


def _gueltig_bis_parsen(wert) -> str:
    """'JJJJ-MM-TT' -> Ablauf am ENDE dieses Tages (UTC); None/leer -> ''.
    Vergangene Daten sind erlaubt (bewusstes Sofort-Sperren)."""
    if not wert:
        return ""
    import re as _re
    w = str(wert).strip()[:10]
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", w):
        raise HTTPException(400, "gueltig_bis muss ein Datum JJJJ-MM-TT sein")
    try:
        tag = datetime.fromisoformat(w)
    except ValueError:
        raise HTTPException(400, "gueltig_bis ist kein gueltiges Datum")
    # Tagesende in deutscher Zeit — sonst zeigt die Oberflaeche den Folgetag
    # (UTC 23:59 = 00:59 Berlin). Ohne tzdata (Windows-Dev) Fallback +01:00.
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Berlin")
    except Exception:
        tz = timezone(timedelta(hours=1))
    return tag.replace(hour=23, minute=59, second=59, tzinfo=tz).isoformat()


@router.patch("/admin/sucher/{sucher_id}/abo-gueltig-bis")
async def admin_set_abo_gueltig_bis(sucher_id: str, body: dict = Body(...),
                                    admin=Depends(current_super_admin)):
    """NUR das Ablaufdatum eines AKTIVEN Abos aendern (Wunsch 09/2026) —
    ohne Zahlung. Audit 09/2026: die Zahlungshistorie wird NICHT mehr
    umgeschrieben; die Aenderung landet als eigener Datensatz in
    zugangs_aenderungen (alt/neu/Grund/Admin)."""
    gueltig_bis = _gueltig_bis_parsen(body.get("gueltig_bis"))
    if not gueltig_bis:
        raise HTTPException(400, "gueltig_bis (JJJJ-MM-TT) fehlt")
    # Audit 09/2026: eine Laufzeitaenderung ohne Zahlung ist immer eine
    # Ausnahme — sie braucht eine Begruendung im unveraenderlichen Verlauf.
    grund = str(body.get("grund", ""))[:300].strip()
    if not grund:
        raise HTTPException(400, "Bitte einen Grund fuer die Laufzeitaenderung angeben")
    aktiv = await db.subscriptions.find_one(
        {"subject_user_id": sucher_id, "status": "active"},
        {"_id": 0, "id": 1, "expires_at": 1, "dealer_id": 1, "plan": 1},
        sort=[("created_at", -1)])
    if not aktiv:
        raise HTTPException(404, "Kein aktives Abo fuer dieses Konto")
    await db.subscriptions.update_many(
        {"subject_user_id": sucher_id, "status": "active"},
        {"$set": {"expires_at": gueltig_bis, "updated_at": now_iso()}})
    await db.zugangs_aenderungen.insert_one({
        "id": str(uuid.uuid4()), "subject_user_id": sucher_id,
        "dealer_id": aktiv.get("dealer_id"), "abo_id": aktiv.get("id"),
        "plan": aktiv.get("plan"),
        "alt": aktiv.get("expires_at"), "neu": gueltig_bis,
        "art": "laufzeit_geaendert", "grund": grund,
        "admin_id": admin["id"], "admin_email": admin.get("email", ""),
        "created_at": now_iso(),
    })
    await log_activity(admin.get("dealer_id", ""), admin["id"],
                       "admin.sucher.abo.gueltig_bis", ref=sucher_id,
                       meta={"alt": aktiv.get("expires_at"), "gueltig_bis": gueltig_bis[:10]})
    return {"ok": True, "expires_at": gueltig_bis}


def _restlaufzeit_basis(expires_at) -> datetime:
    """Startpunkt einer Verlaengerung: bisheriger Ablauf, falls der noch in
    der Zukunft liegt — sonst jetzt."""
    jetzt = datetime.now(timezone.utc)
    if not expires_at:
        return jetzt
    try:
        alt = datetime.fromisoformat(expires_at)
        if alt.tzinfo is None:
            alt = alt.replace(tzinfo=timezone.utc)
        return alt if alt > jetzt else jetzt
    except (ValueError, TypeError):
        return jetzt


# ---------- Sucher-Konten anlegen/verwalten (Betreiber, 09/2026) ----------
# Der Betreiber legt Sucher-Konten für eine Firma an (Anmeldename =
# E-Mail + Passwort), auch nachträglich, und kann sie sperren/löschen
# (Sperren/Löschen laufen über die bestehenden /admin/users-Routen).
class AdminSucherIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    first_name: str = Field(default="", max_length=80)
    last_name: str = Field(default="", max_length=80)
    phone: str = Field(default="", max_length=50)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return pruefe_passwort(v)


@router.post("/admin/dealers/{dealer_id}/sucher")
async def admin_create_sucher(dealer_id: str, body: AdminSucherIn,
                              admin=Depends(current_super_admin)):
    dealer = await db.dealers.find_one({"id": dealer_id},
                                       {"_id": 0, "id": 1, "company_name": 1})
    if not dealer:
        raise HTTPException(404, "Firma nicht gefunden")
    email = body.email.strip().lower()
    # Audit 13.09.2026 (#11): Plattformregel B5 wie in allen anderen
    # Anlagepfaden — users UND driver_accounts. Vorher pruefte dieser Pfad
    # nur users und legte neben einem Fahrerkonto immer ein Doppelkonto an.
    from deps import email_vergeben
    if await email_vergeben(email):
        raise HTTPException(409, "E-Mail ist bereits registriert")
    sucher_id = str(uuid.uuid4())
    try:
        await db.users.insert_one({
            "id": sucher_id, "email": email,
            "password_hash": await hash_password_async(body.password),
            "role": "sucher", "active": True,
            "dealer_id": dealer_id,
            "first_name": body.first_name.strip(),
            "last_name": body.last_name.strip(),
            "phone": body.phone.strip(),
            "created_by": admin["id"],
            "current_session_id": None,
            "created_at": now_iso(),
        })
    except DuplicateKeyError:
        # Doppelklick/Rennen: der Unique-Index auf users.email entscheidet
        # (409 statt 500), wie in admin_create_user.
        raise HTTPException(409, "E-Mail ist bereits registriert")
    # Konto ist angelegt — ein Audit-Fehler darf keinen 500 mit Retry ausloesen.
    await log_activity_sicher(dealer_id, admin["id"], "admin.sucher.angelegt",
                              ref=sucher_id, meta={"email": email,
                                                   "firma": dealer.get("company_name", "")})
    return {"ok": True, "sucher_id": sucher_id, "email": email,
            "hinweis": "Konto angelegt — zum Suchen/Vergleichen noch das "
                       "Sucher-Abo freischalten (150 €/Monat bzw. "
                       "1.500 €/Jahr)."}


@router.get("/admin/dealers/{dealer_id}/sucher")
async def admin_list_dealer_sucher(dealer_id: str, _=Depends(current_admin)):
    """Alle Sucher einer Firma inkl. Abo-Status, letzter Zahlung und
    nächster Fälligkeit (= Abo-Ablauf) — für die Freischaltungs-Ansicht."""
    # Chef ZUERST (Wunsch 09/2026: "Firmen-Chef Freischaltung Sucher-
    # Funktion ja/nein" auf derselben Karte), danach die Sucher.
    items = await db.users.find(
        {"dealer_id": dealer_id, "role": {"$in": ["dealer", "sucher"]}},
        {"_id": 0, "password_hash": 0},
    ).sort("created_at", 1).to_list(200)
    items.sort(key=lambda x: 0 if x.get("role") == "dealer" else 1)
    out = []
    for s in items:
        # Chef: zentrale Aufloesung (persoenlich, sonst altes Firmen-Abo)
        sub = (await subscription_for(s) if s.get("role") == "dealer"
               else await get_subscription_status(dealer_id, subject_user_id=s["id"]))
        letzte = await db.manual_payments.find_one(
            {"subject_user_id": s["id"]}, {"_id": 0},
            sort=[("created_at", -1)])
        out.append({**s, "ist_chef": s.get("role") == "dealer",
                    "subscription": sub,
                    "letzte_zahlung": letzte,
                    "naechste_zahlung_am": sub.get("expires_at")})
    return out


class AdminZahlungIn(BaseModel):
    subject_user_id: Optional[str] = None   # Sucher; leer = Firmen-Ebene
    amount: float = Field(ge=0, le=100000)
    paid_at: Optional[str] = Field(default=None, max_length=10)  # JJJJ-MM-TT
    note: str = Field(default="", max_length=500)


@router.get("/admin/dealers/{dealer_id}/zahlungen")
async def admin_list_zahlungen(dealer_id: str, _=Depends(current_super_admin)):
    """Zahlungshistorie einer Firma (alle manuell erfassten Zahlungen,
    neueste zuerst) — Grundlage für 'was wurde wann gezahlt'."""
    return await db.manual_payments.find(
        {"dealer_id": dealer_id}, {"_id": 0},
    ).sort("created_at", -1).to_list(500)


@router.post("/admin/dealers/{dealer_id}/zahlungen")
async def admin_add_zahlung(dealer_id: str, body: AdminZahlungIn,
                            admin=Depends(current_super_admin)):
    """Zahlung nachtragen/korrigieren, OHNE am Abo etwas zu ändern
    (Freischalten + Verlängern erfasst die Zahlung bereits automatisch)."""
    dealer = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "id": 1})
    if not dealer:
        raise HTTPException(404, "Firma nicht gefunden")
    doc = {
        "id": str(uuid.uuid4()), "dealer_id": dealer_id,
        "subject_user_id": body.subject_user_id or None,
        "plan": None,
        "amount": round(float(body.amount), 2),
        "paid_at": (body.paid_at or now_iso()[:10]),
        "period_until": None,
        "note": body.note.strip(),
        "recorded_by": admin.get("email", ""),
        "created_at": now_iso(),
    }
    # KOPIE einfuegen: insert_one haengt dem uebergebenen dict die Mongo-_id
    # (ObjectId) an — die Antwort waere sonst nicht JSON-serialisierbar (500).
    await db.manual_payments.insert_one(dict(doc))
    await log_activity(dealer_id, admin["id"], "admin.zahlung.erfasst",
                       meta={"betrag": doc["amount"], "sucher":
                             body.subject_user_id or ""})
    return {"ok": True, "zahlung": doc}


# ---------- Zwischenhändler-Zugang freischalten (manuell) ----------
@router.get("/admin/buyers")
async def admin_list_buyers(_=Depends(current_admin)):
    """Alle Zwischenhändler mit Zugangsstatus (für die Freischaltung)."""
    from routes.marketplace import _access_status
    users = await db.users.find(
        {"role": "b2b_buyer"}, {"_id": 0, "password_hash": 0},
    ).sort("created_at", -1).to_list(1000)
    return [{**u, "access": _access_status(u)} for u in users]


@router.post("/admin/buyers/{buyer_id}/access")
async def admin_set_buyer_access(buyer_id: str, body: dict = Body(...),
                                 admin=Depends(current_super_admin)):
    """Marktplatz-Zugang eines Zwischenhändlers aktivieren/verlängern
    (plan='monthly') oder mit plan=null sperren."""
    from routes.marketplace import BUYER_ACCESS_DAYS, BUYER_ACCESS_PRICE
    buyer = await db.users.find_one(
        {"id": buyer_id, "role": "b2b_buyer"}, {"_id": 0, "id": 1})
    if not buyer:
        raise HTTPException(404, "Zwischenhändler nicht gefunden")
    plan = body.get("plan")
    if plan is None:
        # Runde 13: C6 — die Sperre ist ein EIGENER Zustand (gesperrt), der
        # auch im Kostenlos-Modus gilt und den eine Stripe-Zahlung nicht
        # loescht (marketplace._access_status, payments._zugang_freischalten).
        # active=False bleibt fuer den Altbestand als Sperr-Signal erhalten.
        await db.users.update_one(
            {"id": buyer_id},
            {"$set": {"marketplace_access.active": False,
                      "marketplace_access.gesperrt": True,
                      "marketplace_access.gesperrt_am": now_iso(),
                      "marketplace_access.gesperrt_von": admin.get("email", ""),
                      "marketplace_access.updated_at": now_iso()}})
        await log_activity("", admin["id"], "admin.buyer.zugang.gesperrt",
                           ref=buyer_id)
        return {"ok": True, "active": False, "gesperrt": True}
    if plan != "monthly":
        raise HTTPException(400, "Nur 'monthly' unterstützt")
    zahlungsart = body.get("zahlungsart", "rechnung_bezahlt")
    if zahlungsart not in ("rechnung_bezahlt", "kulanz"):
        raise HTTPException(400, "zahlungsart: rechnung_bezahlt oder kulanz")
    grund = str(body.get("grund", ""))[:300].strip()
    if zahlungsart == "kulanz" and not grund:
        raise HTTPException(400, "Kulanz-Freischaltung braucht eine Begruendung")
    voll = await db.users.find_one({"id": buyer_id}, {"_id": 0, "marketplace_access": 1})
    acc = (voll or {}).get("marketplace_access") or {}
    basis = _restlaufzeit_basis(acc.get("expires_at") if acc.get("active") else None)
    expires_at = (basis + timedelta(days=BUYER_ACCESS_DAYS)).isoformat()
    # Audit 09/2026: manuelle Marktplatz-Freischaltung erzeugt jetzt einen
    # Zahlungsdatensatz (Zugang und Finanzhistorie laufen nicht auseinander).
    await db.manual_payments.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": None, "subject_user_id": buyer_id,
        "plan": "marktplatz",
        "amount": 0.0 if zahlungsart == "kulanz" else float(BUYER_ACCESS_PRICE),
        "currency": "EUR", "paid_at": now_iso()[:10], "period_until": expires_at,
        "zahlungsart": zahlungsart, "kostenlos": zahlungsart == "kulanz",
        "grund": grund, "note": str(body.get("notiz", ""))[:500],
        "quelle": "manuell", "recorded_by": admin.get("email", ""),
        "created_at": now_iso()})
    await db.users.update_one(
        {"id": buyer_id},
        {"$set": {"marketplace_access": {
            "active": True, "plan": "monthly",
            "price": BUYER_ACCESS_PRICE, "expires_at": expires_at,
            "activated_by": admin.get("email", ""), "updated_at": now_iso()}}})
    await log_activity("", admin["id"], "admin.buyer.zugang.freigeschaltet",
                       ref=buyer_id, meta={"expires_at": expires_at})
    await db.plan_requests.update_many(
        {"type": "buyer_access", "buyer_user_id": buyer_id, "status": "offen"},
        {"$set": {"status": "erledigt", "updated_at": now_iso(),
                  "erledigt_durch": "freischaltung"}})
    return {"ok": True, "active": True, "expires_at": expires_at}


@router.put("/admin/plan-requests/{req_id}")
async def admin_close_plan_request(req_id: str, body: dict = Body(default={}),
                                   _=Depends(current_super_admin)):
    # Runde 11: feste Zustaende. Ein Tippfehler wie "offen " liess eine
    # Anfrage vorher aus der Oberflaeche verschwinden (die laedt status=offen).
    status = body.get("status", "erledigt")
    if status not in PLAN_REQUEST_STATUS:
        raise HTTPException(400, f"status muss einer von {sorted(PLAN_REQUEST_STATUS)} sein")
    r = await db.plan_requests.update_one(
        {"id": req_id},
        {"$set": {"status": status, "updated_at": now_iso()}})
    if not r.matched_count:
        raise HTTPException(404, "Anfrage nicht gefunden")
    return {"ok": True}


PLAN_REQUEST_STATUS = {"offen", "erledigt", "abgelehnt"}


# ---------- Vehicle comparisons ----------
@router.get("/admin/comparisons")
async def admin_comparisons(limit: int = 200, _=Depends(current_admin)):
    """Aggregierte Liste aller verglichenen Fahrzeuge (mobile / kleinanzeigen
    / autoscout). Pro Fahrzeug: Anzahl Vergleiche, Quellen, beteiligte Nutzer
    (mit Firmenname), Zeitraum, sowie Fahrzeugdaten (Marke/Modell/EZ/Preis)
    soweit verfügbar.
    """
    pipeline = [
        {"$group": {
            "_id": "$mobile_ad_id",
            "count": {"$sum": 1},
            "user_ids": {"$addToSet": "$user_id"},
            "dealer_ids": {"$addToSet": "$dealer_id"},
            "sources": {"$addToSet": "$source"},
            "first_at": {"$min": "$created_at"},
            "last_at": {"$max": "$created_at"},
        }},
        {"$sort": {"count": -1, "last_at": -1}},
        {"$limit": max(1, min(limit, 1000))},
    ]
    rows = await db.vehicle_comparisons.aggregate(pipeline).to_list(1000)

    # Bulk-Lookup für Performance
    all_user_ids = {uid for r in rows for uid in (r.get("user_ids") or []) if uid}
    all_dealer_ids = {did for r in rows for did in (r.get("dealer_ids") or []) if did}
    ad_ids = [r["_id"] for r in rows if r.get("_id")]

    users_by_id = {}
    if all_user_ids:
        async for u in db.users.find(
            {"id": {"$in": list(all_user_ids)}},
            {"_id": 0, "id": 1, "email": 1, "username": 1, "company_name": 1, "dealer_id": 1, "active": 1},
        ):
            users_by_id[u["id"]] = u
    dealers_by_id = {}
    if all_dealer_ids:
        async for d in db.dealers.find(
            {"id": {"$in": list(all_dealer_ids)}},
            {"_id": 0, "id": 1, "company_name": 1},
        ):
            dealers_by_id[d["id"]] = d
    # Runde 13: B3 — je (Inserat, Firma) gibt es ein eigenes Fahrzeugdokument
    # mit derselben mobile_ad_id. Vorher gewann "das letzte Dokument", also
    # konnten Haendlerkorrekturen (Kilometer nach Abholung, Preis) von Firma B
    # neben den Nutzern von Firma A stehen. Jetzt: nur REINE Inseratsdaten
    # (data solange "verglichen", danach inserat_aktuell), davon das neueste.
    vehicles_by_ad = {}
    if ad_ids:
        async for v in db.vehicles.find(
            {"mobile_ad_id": {"$in": ad_ids}},
            {"_id": 0, "mobile_ad_id": 1, "dealer_id": 1, "lifecycle": 1, "data": 1,
             "inserat_aktuell": 1, "inserat_aktuell_am": 1, "updated_at": 1},
        ):
            lc = v.get("lifecycle") or "verglichen"
            if lc == "verglichen":
                inserat, stand, korrigiert = v.get("data") or {}, v.get("updated_at") or "", False
            elif v.get("inserat_aktuell"):
                inserat, stand, korrigiert = v["inserat_aktuell"], v.get("inserat_aktuell_am") or "", False
            else:
                # data kann Haendlerkorrekturen tragen — nur als Notnagel,
                # wenn keine andere Firma reine Inseratsdaten hat.
                inserat, stand, korrigiert = v.get("data") or {}, "", True
            bisher = vehicles_by_ad.get(v["mobile_ad_id"])
            if bisher is None or (bisher["korrigiert"] and not korrigiert) \
                    or (bisher["korrigiert"] == korrigiert and str(stand) > str(bisher["stand"])):
                vehicles_by_ad[v["mobile_ad_id"]] = {
                    "data": inserat, "stand": stand, "korrigiert": korrigiert}

    out = []
    for r in rows:
        ad_id = r["_id"]
        v = vehicles_by_ad.get(ad_id) or {}
        vd = v.get("data") or {}
        users = []
        for uid in (r.get("user_ids") or []):
            u = users_by_id.get(uid)
            if not u:
                continue
            company = u.get("company_name") or dealers_by_id.get(u.get("dealer_id"), {}).get("company_name") or ""
            users.append({
                "id": u["id"],
                "email": u.get("email"),
                "username": u.get("username"),
                "company_name": company,
                "active": u.get("active", True),
            })
        out.append({
            "ad_id": ad_id,
            "count": r.get("count", 0),
            "sources": sorted([s for s in (r.get("sources") or []) if s]),
            "first_at": r.get("first_at"),
            "last_at": r.get("last_at"),
            "users": users,
            "vehicle": {
                "make": vd.get("make") or vd.get("brand"),
                "model": vd.get("model"),
                "ez": vd.get("ez") or vd.get("first_registration"),
                "mileage": vd.get("mileage"),
                "price": vd.get("price"),
                "fuel": vd.get("fuel"),
                "vin": vd.get("vin") or vd.get("fin"),
                "url": vd.get("url") or vd.get("listing_url"),
                # Runde 13: B3 — von wann die Inseratsdaten sind und ob sie
                # notgedrungen aus einem korrigierten Datensatz stammen
                "stand": v.get("stand") or None,
                "korrigiert": bool(v.get("korrigiert")),
            } if vd else None,
            # Runde 13: B3 — wie viele Firmen dasselbe Inserat halten
            "firmen": len([d for d in (r.get("dealer_ids") or []) if d]),
        })
    return {"items": out, "total": len(out)}


# ---------- URL-Stats (live) ----------
@router.get("/admin/url-stats")
async def admin_url_stats(_=Depends(current_admin)):
    """Live URL-Counter — wie viele Inserate wurden je Quelle abgefragt.
    Drei Zeitfenster: insgesamt, letzte 24h, heute (lokal serverseitig).
    """
    now = datetime.now(timezone.utc)
    today_iso = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    last24_iso = (now - timedelta(hours=24)).isoformat()
    last7d_iso = (now - timedelta(days=7)).isoformat()

    async def _by_source(query: dict) -> Dict[str, int]:
        pipeline = [
            {"$match": query} if query else {"$match": {}},
            {"$group": {"_id": "$source", "n": {"$sum": 1}}},
        ]
        rows = await db.vehicle_comparisons.aggregate(pipeline).to_list(50)
        d = {"mobile": 0, "kleinanzeigen": 0, "autoscout": 0, "autoscout24": 0, "other": 0}
        total = 0
        for r in rows:
            src = (r.get("_id") or "other").lower()
            n = r.get("n", 0)
            total += n
            if src in d:
                d[src] += n
            else:
                d["other"] += n
        # Normalisieren: autoscout24 unter autoscout zusammenfassen
        d["autoscout"] = d.pop("autoscout") + d.pop("autoscout24")
        d["total"] = total
        return d

    return {
        "all_time": await _by_source({}),
        "last_7d": await _by_source({"created_at": {"$gte": last7d_iso}}),
        "last_24h": await _by_source({"created_at": {"$gte": last24_iso}}),
        "today": await _by_source({"created_at": {"$gte": today_iso}}),
        "now": now.isoformat(),
    }


# ---------- Self-password change ----------
@router.post("/admin/me/password")
async def admin_self_password(body: AdminSelfPasswordIn, admin=Depends(current_admin)):
    try:
        pruefe_passwort(body.new_password or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    user = await db.users.find_one({"id": admin["id"]})
    if not user or not await verify_password_async(body.current_password, user["password_hash"]):
        raise HTTPException(401, "Aktuelles Passwort ist nicht korrekt")
    await db.users.update_one(
        {"id": admin["id"]},
        {"$set": {"password_hash": await hash_password_async(body.new_password),
          "current_session_id": None}},
    )
    return {"ok": True}


# ---------- Betrieb: Alarme, Loesch-Warteschlange, Abgleiche ----------
@router.get("/admin/betrieb")
async def admin_betrieb(admin=Depends(current_super_admin)):
    """Sichtbarkeit fuer alles, was frueher still scheiterte (Audit 09/2026):
    offene Betriebsalarme, nicht loeschbare Dateien, haengende
    Freischaltungs-Vorgaenge, Zahlungen ohne Zugang, letztes Backup."""
    from betrieb import offene_alarme
    try:
        # Runde 21 (Nebenbefund): serveruebergreifend wie /ready — sonst
        # zeigte der Server ohne eigene Sicherung "kein Backup". Die
        # Auskunft traegt konsistent/konsistenz/inkonsistent; ein
        # inkonsistentes Backup gilt nicht als vollstaendig.
        from backup_service import letztes_backup_info_global
        backup = await letztes_backup_info_global(db)
    except Exception as exc:                      # pragma: no cover
        backup = {"hinweis": f"nicht ermittelbar: {exc}"}
    frist = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    return {
        "alarme": await offene_alarme(db),
        "datei_loeschungen_offen": await db.storage_delete_retry.count_documents({}),
        "datei_loeschungen_aufgegeben": await db.storage_delete_retry.find(
            {"aufgegeben": True}, {"_id": 0}).limit(50).to_list(50),
        "abo_vorgaenge_haengend": await db.abo_vorgaenge.count_documents(
            {"status": "laeuft", "updated_at": {"$lt": frist}}),
        "zahlungen_ohne_zugang": await db.payment_transactions.count_documents(
            {"status": {"$in": ["paid", "activating", "activation_failed"]}}),
        "backup": backup,
        "wartungsmodus": bool(((await db.system_flags.find_one(
            {"_id": "wartungsmodus"})) or {}).get("aktiv")),
        # Abo-Audit: Super-Admins ohne Zwei-Faktor sichtbar machen
        "super_admins_ohne_mfa": [u.get("email") or u.get("username") async for u in db.users.find(
            {"role": "admin", "is_super_admin": True, "active": {"$ne": False},
             "mfa.aktiv": {"$ne": True}}, {"_id": 0, "email": 1, "username": 1})],
        # Runde 12: Es gibt nur den Super-Admin. Alte Konten mit Rolle admin
        # ohne is_super_admin koennen nichts mehr und gehoeren geloescht.
        "admin_konten_ohne_super_admin": [u.get("email") or u.get("username") async for u in db.users.find(
            {"role": "admin", "is_super_admin": {"$ne": True}},
            {"_id": 0, "email": 1, "username": 1})],
        # Runde 17: die Parallelitaets-Backstops muessen sichtbar sein —
        # bei Altdubletten werden sie beim Start nur uebersprungen.
        "termin_index_aktiv": "termin_offen_je_vertrag" in await db.appointments.index_information(),
        "fahrzeug_index_aktiv": any(
            i.get("unique") and i.get("key") == [("dealer_id", 1), ("id", 1)]
            for i in (await db.vehicles.index_information()).values()),
        # Audit 13.09.2026 Nachbesserung (#18): Backstops Merkliste,
        # laufende Kaufanfrage, offene Marktplatz-Zugangsanfrage
        "favoriten_index_aktiv": "favorit_je_kaeufer_inserat"
            in await db.buyer_favorites.index_information(),
        "interesse_index_aktiv": "interesse_offen_je_kaeufer"
            in await db.listing_interest.index_information(),
        "zugangsanfrage_index_aktiv": "uniq_offene_buyer_access_anfrage"
            in await db.plan_requests.index_information(),
    }


@router.post("/admin/betrieb/alarme/{alarm_id}/quittieren")
async def admin_alarm_quittieren(alarm_id: str, admin=Depends(current_super_admin)):
    from betrieb import quittieren
    if not await quittieren(db, alarm_id, admin.get("email", admin["id"])):
        raise HTTPException(404, "Alarm nicht gefunden oder bereits quittiert")
    return {"ok": True}


@router.post("/admin/betrieb/nachholen")
async def admin_betrieb_nachholen(admin=Depends(current_super_admin)):
    """Reparaturlaeufe sofort anstossen (sonst alle 10 Minuten automatisch)."""
    from routes.payments import zahlungen_abgleichen
    # Runde 17: fehlende Unique-Indizes (Altdubletten beim Start) ohne
    # Neustart nachholen, sobald die Daten bereinigt sind.
    from indizes import (_termin_unique_index, _unique_index_sicher, _favoriten_unique_index,
                         _interesse_unique_index, _buyer_access_unique_index)
    return {"abo_vorgaenge": await abo_vorgaenge_nachholen(),
            "zahlungen": await zahlungen_abgleichen(db),
            "termin_index": await _termin_unique_index(),
            "fahrzeug_index": await _unique_index_sicher(
                db.vehicles, ["dealer_id", "id"], abbruch_in_produktion=False),
            # Audit 13.09.2026 Nachbesserung (#18): Indizes mit automatischer
            # Dublettenbereinigung — schrieb die alte Fassung beim Rollout
            # dazwischen, fehlten sie sonst bis zum naechsten Neustart.
            "favoriten_index": await _favoriten_unique_index(),
            "interesse_index": await _interesse_unique_index(),
            "zugangsanfrage_index": await _buyer_access_unique_index()}


# ---------- Zwei-Faktor-Anmeldung (Admin / Super-Admin) ----------
class MfaCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=40)


@router.get("/admin/me/mfa")
async def admin_mfa_status(admin=Depends(current_admin)):
    voll = await db.users.find_one({"id": admin["id"]}, {"_id": 0, "mfa": 1})
    m = (voll or {}).get("mfa") or {}
    return {"aktiv": bool(m.get("aktiv")), "aktiviert_am": m.get("aktiviert_am"),
            "wiederherstellungscodes_uebrig": len(m.get("wiederherstellung") or []),
            "einrichtung_offen": bool(m.get("pending_secret"))}


@router.post("/admin/me/mfa/einrichten")
async def admin_mfa_einrichten(admin=Depends(current_admin)):
    """Neues Geheimnis erzeugen (noch NICHT aktiv) — Anzeige als otpauth-Link
    bzw. Klartext fuer die manuelle Eingabe in der Authenticator-App."""
    import mfa as _mfa
    # Ein bereits erzeugtes, frisches Geheimnis WIEDERVERWENDEN. Sonst
    # erzeugt jeder erneute Klick auf "Einrichten" ein neues, waehrend in der
    # Authenticator-App noch das erste steht — der Code passt dann nie, und
    # die Meldung "Code ungueltig" fuehrt in die Irre.
    voll = await db.users.find_one({"id": admin["id"]}, {"_id": 0, "mfa": 1})
    vorhanden = (voll or {}).get("mfa") or {}
    secret = None
    if vorhanden.get("pending_secret") and not vorhanden.get("aktiv"):
        try:
            alter = (datetime.now(timezone.utc)
                     - datetime.fromisoformat(vorhanden.get("pending_seit", ""))
                     ).total_seconds()
        except (TypeError, ValueError):
            alter = 10 ** 9
        if alter < 3600:                       # eine Stunde lang dasselbe
            secret = _mfa.entschluesseln(vorhanden["pending_secret"]) or None
    neu_erzeugt = secret is None
    if neu_erzeugt:
        secret = _mfa.secret_erzeugen()
    await db.users.update_one(
        {"id": admin["id"]},
        {"$set": {"mfa.pending_secret": _mfa.verschluesseln(secret),
                  "mfa.pending_seit": now_iso() if neu_erzeugt
                  else vorhanden.get("pending_seit", now_iso())}})
    return {"secret": secret, "otpauth_uri": _mfa.provisioning_uri(secret, admin.get("email") or admin.get("username") or admin["id"]),
            "hinweis": "Code aus der App eingeben, um die Zwei-Faktor-Anmeldung zu aktivieren."}


@router.post("/admin/me/mfa/aktivieren")
async def admin_mfa_aktivieren(body: MfaCodeIn, admin=Depends(current_admin)):
    import mfa as _mfa
    voll = await db.users.find_one({"id": admin["id"]}, {"_id": 0, "mfa": 1})
    m = (voll or {}).get("mfa") or {}
    secret = _mfa.entschluesseln(m.get("pending_secret", "")) if m.get("pending_secret") else None
    if not secret:
        raise HTTPException(400, "Zuerst einrichten (Geheimnis erzeugen)")
    zaehler = _mfa.code_pruefen(secret, body.code)
    if zaehler is None:
        raise HTTPException(
            400, "Code ungültig. Zwei häufige Gründe: die Uhrzeit des Geräts "
                 "weicht ab (auf automatische Zeit stellen), oder in der App "
                 "steht noch ein älterer Eintrag für dieses Konto — dann den "
                 "Eintrag dort löschen und den hier angezeigten Schlüssel neu "
                 "übernehmen.")
    codes, hashes = _mfa.wiederherstellungscodes()
    await db.users.update_one(
        {"id": admin["id"]},
        {"$set": {"mfa": {"aktiv": True, "secret": _mfa.verschluesseln(secret),
                          "letzter_zaehler": zaehler, "fehlversuche": 0,
                          "wiederherstellung": hashes, "aktiviert_am": now_iso()}}})
    await log_activity("", admin["id"], "admin.mfa.aktiviert")
    return {"ok": True, "aktiv": True, "wiederherstellungscodes": codes,
            "hinweis": "Diese Codes jetzt sicher aufbewahren — sie werden nur einmal angezeigt."}


@router.post("/admin/me/mfa/deaktivieren")
async def admin_mfa_deaktivieren(body: MfaCodeIn, admin=Depends(current_admin)):
    import mfa as _mfa
    voll = await db.users.find_one({"id": admin["id"]}, {"_id": 0, "mfa": 1})
    m = (voll or {}).get("mfa") or {}
    if not m.get("aktiv"):
        raise HTTPException(400, "Zwei-Faktor ist nicht aktiv")
    secret = _mfa.entschluesseln(m.get("secret", "")) or ""
    if _mfa.code_pruefen(secret, body.code, int(m.get("letzter_zaehler", -1))) is None:
        raise HTTPException(401, "Code ungültig")
    await db.users.update_one({"id": admin["id"]}, {"$unset": {"mfa": ""}})
    await log_activity("", admin["id"], "admin.mfa.deaktiviert")
    return {"ok": True, "aktiv": False}


@router.post("/admin/users/{user_id}/mfa-zuruecksetzen")
async def admin_mfa_zuruecksetzen(user_id: str, body: dict = Body(default={}),
                                  admin=Depends(current_super_admin)):
    """Nur Super-Admin: Zwei-Faktor eines AUSGESPERRTEN Admins entfernen —
    der Betroffene richtet sie danach neu ein.

    Audit 09/2026: Ein Selbstreset ist verboten — sonst koennte eine
    gestohlene Super-Admin-Sitzung den zweiten Faktor einfach abschalten.
    Wird das Konto eines ANDEREN Super-Admins zurueckgesetzt, muss der
    Handelnde sein eigenes Passwort bestaetigen und einen Grund angeben;
    der Vorgang loest zusaetzlich einen Betriebsalarm aus."""
    if user_id == admin["id"]:
        raise HTTPException(
            400, "Die eigene Zwei-Faktor-Anmeldung kann hier nicht "
                 "zurueckgesetzt werden. Zum Abschalten den Weg "
                 "Einstellungen -> Abschalten mit gueltigem Code nutzen; "
                 "bei Verlust des Geraets muss ein anderer Super-Admin "
                 "zuruecksetzen.")
    u = await db.users.find_one({"id": user_id, "role": "admin"},
                                {"_id": 0, "id": 1, "email": 1, "is_super_admin": 1})
    if not u:
        raise HTTPException(404, "Admin-Konto nicht gefunden")
    grund = str(body.get("grund", ""))[:300].strip()
    if u.get("is_super_admin"):
        passwort = str(body.get("passwort", ""))
        eigener = await db.users.find_one({"id": admin["id"]}, {"_id": 0, "password_hash": 1})
        if not passwort or not verify_password(passwort, (eigener or {}).get("password_hash", "")):
            raise HTTPException(401, "Zum Zuruecksetzen eines Super-Admin-Kontos "
                                     "bitte das eigene Passwort bestaetigen")
        if not grund:
            raise HTTPException(400, "Bitte einen Grund angeben (wird protokolliert)")
    await db.users.update_one({"id": user_id},
                              {"$unset": {"mfa": ""}, "$set": {"current_session_id": None}})
    await db.zugangs_aenderungen.insert_one({
        "id": str(uuid.uuid4()), "art": "mfa_zurueckgesetzt",
        "subject_user_id": user_id, "subject_email": u.get("email", ""),
        "subject_super_admin": bool(u.get("is_super_admin")),
        "alt": "zwei_faktor_aktiv", "neu": "zwei_faktor_entfernt",
        "grund": grund, "admin_id": admin["id"],
        "admin_email": admin.get("email", ""), "created_at": now_iso()})
    await log_activity("", admin["id"], "admin.mfa.zurueckgesetzt", ref=user_id,
                       meta={"email": u.get("email", ""), "grund": grund,
                             "super_admin": bool(u.get("is_super_admin"))})
    if u.get("is_super_admin"):
        from betrieb import alarm
        await alarm(db, "mfa_zurueckgesetzt", ref=u.get("email", user_id),
                    von=admin.get("email", ""), grund=grund or "ohne Angabe")
    return {"ok": True}


@router.post("/admin/buyers/{buyer_id}/ustid-pruefen")
async def admin_buyer_ustid_pruefen(buyer_id: str, admin=Depends(current_admin)):
    """USt-IdNr. eines Zwischenhaendlers beim EU-Dienst VIES pruefen (Audit
    09/2026, Punkt 40). Ergebnis wird am Kaeufer gespeichert; Nicht-
    erreichbarkeit ist ein Ergebnis ('nicht_pruefbar'), kein Fehler."""
    from ustid import vies_pruefen
    buyer = await db.users.find_one({"id": buyer_id, "role": "b2b_buyer"},
                                    {"_id": 0, "id": 1, "ust_id": 1})
    if not buyer:
        raise HTTPException(404, "Zwischenhändler nicht gefunden")
    ergebnis = await vies_pruefen(buyer.get("ust_id") or "")
    ergebnis["geprueft_von"] = admin.get("email", "")
    await db.users.update_one({"id": buyer_id}, {"$set": {"ust_id_pruefung": ergebnis}})
    await log_activity("", admin["id"], "admin.buyer.ustid.geprueft", ref=buyer_id,
                       meta={"status": ergebnis["status"], "ust_id": ergebnis.get("ust_id")})
    return ergebnis
