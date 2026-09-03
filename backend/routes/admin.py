"""Admin endpoints: users CRUD, contracts, stats, comparisons, URL-stats,
self-password, cleanup trigger.
"""
import base64
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, Literal, Optional


def _safe_filename(name: str, fallback: str = "document.pdf") -> str:
    """Strip characters that could inject extra HTTP header lines."""
    safe = re.sub(r'[\r\n\t"\\]', "", name).strip()
    return safe[:200] or fallback

from pymongo.errors import DuplicateKeyError
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field, field_validator

from auth import (hash_password, hash_password_async,
                  verify_password, verify_password_async)
from cleanup_service import _cleanup_once
from deps import (
    current_admin, current_super_admin, db, get_subscription_status, log, log_activity, now_iso, sub_status_from_doc, subscription_for,
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


# ---------- Users ----------
@router.post("/admin/users")
async def admin_create_user(body: AdminUserIn, admin=Depends(current_super_admin)):
    # Haertung 09/2026: E-Mail wie bei Registrierung/Sucher normalisieren und
    # schreibungsunabhaengig pruefen — vorher konnten "Chef@X.de" und
    # "chef@x.de" als zwei Konten existieren (Login trifft dann das falsche).
    email = body.email.strip().lower()
    existing = await db.users.find_one(
        {"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}})
    if existing:
        raise HTTPException(409, "E-Mail bereits registriert")
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
    expires = body.expires_at
    if not expires and body.plan_type in ("monthly", "trial"):
        expires = (datetime.now(timezone.utc) + timedelta(days=30 if body.plan_type == "monthly" else 14)).isoformat()
    if not expires and body.plan_type == "yearly":
        expires = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
    await db.subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": dealer_id,
        "plan": body.plan_type, "status": "active",
        "expires_at": None if body.plan_type == "lifetime" else expires,
        "created_at": now_iso(),
    })
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
    page = max(1, int(page or 1))
    users = await db.users.find({}, {"_id": 0, "password_hash": 0}) \
        .sort("created_at", -1).skip((page - 1) * limit).to_list(limit)
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
        if fields["role"] not in ("dealer", "sucher", "admin", "b2b_buyer"):
            raise HTTPException(400, "Unbekannte Rolle")
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
    if fields:
        await db.users.update_one({"id": user_id}, {"$set": fields})
    if "plan_type" in body:
        u = await db.users.find_one({"id": user_id})
        if not u:
            raise HTTPException(404)
        plan = body["plan_type"]
        expires = body.get("expires_at")
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
        await db.subscriptions.insert_one(sub_doc)
        await log_activity(admin.get("dealer_id", ""), admin["id"], "admin.abo.vergeben",
                           ref=user_id, meta={"plan": plan, "expires_at": expires,
                                              "email": u.get("email", "")})
    if fields:
        await log_activity(admin.get("dealer_id", ""), admin["id"], "admin.user.aktualisiert",
                           ref=user_id, meta={"felder": sorted(fields.keys()),
                                              "email": target.get("email", "")})
    return {"ok": True}


# Alles, was einer Firma gehoert (dealer_id-Verweis) — Grundlage fuer
# Loeschvorschau und vollstaendige Firmenloeschung. Bewusst NICHT dabei:
# activity_logs (Plattform-Nachvollziehbarkeit) und payment_transactions
# (Buchhaltungs-/Aufbewahrungspflicht).
_COMPANY_COLLECTIONS = (
    "users", "subscriptions", "vehicles", "appointments",
    "generated_pdfs", "generated_pdf_versions", "resale_listings",
    "listing_interest", "pickup_protocols", "pickup_reports",
    # driver_accounts NICHT: Fahrer-Konten sind firmenneutral (kein
    # dealer_id-Feld) — der alte Eintrag war ein No-Op und liess die
    # Loeschvorschau faelschlich "0 Fahrer" zaehlen. Fahrer loescht der
    # Admin ueber DELETE /admin/drivers/{id}.
    "dealer_drivers", "dealer_invites",
    "plan_requests", "vehicle_comparisons",
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
        await db.users.delete_one({"id": user_id})
        await db.subscriptions.delete_many({"subject_user_id": user_id})
        await db.network_members.delete_many({"buyer_user_id": user_id})
        await db.buyer_favorites.delete_many({"buyer_user_id": user_id})
        await db.listing_interest.delete_many({"buyer_user_id": user_id})
        await db.plan_requests.delete_many({"buyer_user_id": user_id})
        if u.get("email"):
            # Reset-Dokumente tragen user_id, keine E-Mail (Runde 5).
            await db.password_resets.delete_many({"user_id": user_id})
        await log_activity(admin.get("dealer_id", ""), admin["id"],
                           "admin.user.geloescht", ref=user_id,
                           meta={"email": u.get("email", ""),
                                 "rolle": u.get("role", "")})
        return {"ok": True, "geloescht": "nur_nutzer"}

    dealer_id = u.get("dealer_id")
    if not firma_loeschen:
        raise HTTPException(409,
            "Das ist der Händler-Hauptaccount. Zum Sperren bitte "
            "'Deaktivieren' verwenden. Soll die FIRMA komplett gelöscht "
            "werden (alle Nutzer, Fahrzeuge, Verträge, Termine), zuerst "
            f"die Löschvorschau ansehen (/admin/dealers/{dealer_id}/"
            "loeschvorschau) und dann mit ?firma_loeschen=true bestätigen.")

    geloescht = {}
    if dealer_id:
        # Beweis-Snapshots bleiben BEWUSST stehen: Snapshots sind
        # haendlerneutral geteilt (der erste Snapshot einer Anzeige wird
        # von allen uebernommen) — eine Loeschung ueber die Fahrzeug-ID
        # ('v_<Anzeigen-ID>', bei mehreren Haendlern identisch) wuerde die
        # Beweisarchive ANDERER Firmen zerstoeren. Sie enthalten nur
        # oeffentliche Inseratsdaten und verfallen ueber die
        # SNAPSHOT_RETENTION_DAYS-Aufraeumlogik.
        for coll in _COMPANY_COLLECTIONS:
            res = await db[coll].delete_many({"dealer_id": dealer_id})
            if res.deleted_count:
                geloescht[coll] = res.deleted_count
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
    fields = {"active": body.active, "updated_at": now_iso()}
    if not body.active:
        # Sperren beendet die laufende Sitzung sofort (Single-Session strikt).
        fields["current_session_id"] = None
    await db.driver_accounts.update_one({"id": driver_id}, {"$set": fields})
    await log_activity(admin.get("dealer_id", ""), admin["id"],
                       "admin.fahrer.entsperrt" if body.active else "admin.fahrer.gesperrt",
                       ref=driver_id, meta={"email": d.get("email", "")})
    return {"ok": True, "active": body.active}


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
    # Audit 09/2026: nicht nur trennen, sondern pseudonymisieren (Termine,
    # Berichte, Protokolle, Audit-Log) — Funktion in routes/drivers.py.
    from routes.drivers import fahrer_konto_anonymisieren
    anonym = await fahrer_konto_anonymisieren(db, driver_id)
    links = type("R", (), {"deleted_count": anonym.get("dealer_drivers", 0)})()
    getrennt = type("R", (), {"modified_count": anonym.get("appointments", 0)})()
    await db.driver_accounts.delete_one({"id": driver_id})
    await log_activity(admin.get("dealer_id", ""), admin["id"],
                       "admin.fahrer.geloescht", ref=driver_id,
                       meta={"email": d.get("email", ""),
                             "driver_code": d.get("driver_code", ""),
                             "verknuepfungen": links.deleted_count,
                             "offene_termine_getrennt": getrennt.modified_count})
    return {"ok": True, "verknuepfungen_entfernt": links.deleted_count,
            "offene_termine_getrennt": getrennt.modified_count}


# ---------- Contracts (read-only admin views) ----------
@router.get("/admin/contracts")
async def admin_all_contracts(_=Depends(current_admin)):
    items = await db.generated_pdfs.find(
        {}, {"_id": 0, "pdf_b64": 0},
    ).sort("created_at", -1).to_list(2000)
    return items


@router.get("/admin/users/{user_id}/contracts")
async def admin_user_contracts(user_id: str, _=Depends(current_admin)):
    """Listet alle Verträge eines Nutzers auf (read-only).
    Enthält keine PDF-Bytes — nur Metadaten + extrahierte Vertragsdaten,
    damit die Liste schnell lädt. PDF kann separat über
    /api/admin/contracts/{id}/pdf abgerufen werden (falls benötigt).
    """
    user = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
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
    items = await db.generated_pdfs.find(
        {"$or": [{"user_id": user_id}, {"dealer_id": user.get("dealer_id")}]},
        {"_id": 0, "pdf_b64": 0},
    ).sort("created_at", -1).to_list(2000)
    return {"user": user, "contracts": items}


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
        try:
            plan["custom_quota"] = int(body.get("custom_quota") or 0) or None
        except (TypeError, ValueError):
            plan["custom_quota"] = None
    await db.dealers.update_one({"id": dealer_id}, {"$set": {"sale_plan": plan}})
    await log_activity(admin.get("dealer_id", ""), admin["id"],
                       "admin.verkaufsplan.gesetzt", ref=dealer_id,
                       meta={"tier": tier})
    return {"ok": True, "sale_plan": plan}


@router.get("/admin/plan-requests")
async def admin_plan_requests(status: Optional[str] = None,
                              type: Optional[str] = None,
                              _=Depends(current_admin)):
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
    try:
        await db.sperren.insert_one({"_id": sperre, "owner": besitzer,
                                     "seit": now_iso()})
    except DuplicateKeyError:
        alt = await db.sperren.find_one({"_id": sperre}) or {}
        try:
            alter = (datetime.now(timezone.utc)
                     - datetime.fromisoformat(alt["seit"])).total_seconds()
        except Exception:
            alter = 999
        if alter < 60:
            raise HTTPException(409, "Freischaltung laeuft gerade — bitte "
                                     "einen Moment warten (Doppelklick)")
        r = await db.sperren.update_one(
            {"_id": sperre, "owner": alt.get("owner")},
            {"$set": {"owner": besitzer, "seit": now_iso()}})
        if r.modified_count == 0:
            raise HTTPException(409, "Freischaltung laeuft gerade — bitte "
                                     "einen Moment warten (Doppelklick)")
    try:
        return await _abo_freischalten(sucher, sucher_id, body, gezahlt_am, admin)
    finally:
        await db.sperren.delete_one({"_id": sperre, "owner": besitzer})


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
        "grund": str(body.get("grund", ""))[:300],
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
    existing = await db.users.find_one(
        {"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}})
    if existing:
        raise HTTPException(409, "E-Mail ist bereits registriert")
    sucher_id = str(uuid.uuid4())
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
    await log_activity(dealer_id, admin["id"], "admin.sucher.angelegt",
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
        await db.users.update_one(
            {"id": buyer_id},
            {"$set": {"marketplace_access.active": False,
                      "marketplace_access.updated_at": now_iso()}})
        await log_activity("", admin["id"], "admin.buyer.zugang.gesperrt",
                           ref=buyer_id)
        return {"ok": True, "active": False}
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
    r = await db.plan_requests.update_one(
        {"id": req_id},
        {"$set": {"status": body.get("status", "erledigt"),
                  "updated_at": now_iso()}})
    if not r.matched_count:
        raise HTTPException(404, "Anfrage nicht gefunden")
    return {"ok": True}


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
    vehicles_by_ad = {}
    if ad_ids:
        async for v in db.vehicles.find(
            {"mobile_ad_id": {"$in": ad_ids}},
            {"_id": 0, "mobile_ad_id": 1, "data": 1, "updated_at": 1},
        ):
            vehicles_by_ad[v["mobile_ad_id"]] = v

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
            } if vd else None,
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
        from backup_service import letztes_backup_info
        backup = letztes_backup_info()
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
    return {"abo_vorgaenge": await abo_vorgaenge_nachholen(),
            "zahlungen": await zahlungen_abgleichen(db)}
