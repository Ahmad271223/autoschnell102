"""Admin endpoints: users CRUD, contracts, stats, comparisons, URL-stats,
self-password, cleanup trigger.
"""
import base64
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional


def _safe_filename(name: str, fallback: str = "document.pdf") -> str:
    """Strip characters that could inject extra HTTP header lines."""
    safe = re.sub(r'[\r\n\t"\\]', "", name).strip()
    return safe[:200] or fallback

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field, field_validator

from auth import hash_password, verify_password
from cleanup_service import _cleanup_once
from deps import (
    current_admin, db, get_subscription_status, log, log_activity, now_iso,
    sub_status_from_doc,
)
from mobile_service import DEFAULT_RULES, DEFAULT_EXPORT_RULES

router = APIRouter()


# ---------- Models ----------
class AdminUserIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    company_name: str
    plan_type: str = "monthly"  # monthly | yearly | lifetime | trial
    expires_at: Optional[str] = None
    active: Optional[bool] = True


class AdminActiveIn(BaseModel):
    active: bool


class AdminUserPasswordIn(BaseModel):
    new_password: str


class AdminSelfPasswordIn(BaseModel):
    current_password: str
    new_password: str


# ---------- Cleanup trigger ----------
@router.post("/admin/cleanup/run")
async def admin_trigger_cleanup(user=Depends(current_admin)):
    """Manuell einen Cleanup-Durchlauf anstoßen (Debug/QA).
    Regulär läuft der Loop 1× pro Stunde automatisch."""
    stats = await _cleanup_once(db)
    return stats


# ---------- Users ----------
@router.post("/admin/users")
async def admin_create_user(body: AdminUserIn, admin=Depends(current_admin)):
    existing = await db.users.find_one({"email": body.email})
    if existing:
        raise HTTPException(409, "E-Mail bereits registriert")
    user_id = str(uuid.uuid4())
    dealer_id = str(uuid.uuid4())
    await db.users.insert_one({
        "id": user_id, "email": body.email,
        "password_hash": hash_password(body.password),
        "role": "dealer", "active": body.active if body.active is not None else True,
        "dealer_id": dealer_id, "current_session_id": None,
        "created_at": now_iso(),
    })
    await db.dealers.insert_one({
        "id": dealer_id, "user_id": user_id, "company_name": body.company_name,
        "contact_person": "", "phone": "", "email": body.email,
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
    })
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
        {"id": {"$in": dealer_ids}}, {"_id": 0, "id": 1, "company_name": 1})}
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
    return [{**u,
             "company_name": dealers.get(u.get("dealer_id"), {}).get("company_name"),
             "subscription": sub_status_from_doc(newest_subs.get(u.get("dealer_id")))}
            for u in users]


@router.put("/admin/users/{user_id}")
async def admin_update_user(user_id: str, body: dict = Body(...), admin=Depends(current_admin)):
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
        if len(pw) < 8:
            raise HTTPException(400, "Passwort muss mindestens 8 Zeichen haben")
        fields["password_hash"] = hash_password(pw)
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
    "driver_accounts", "dealer_drivers", "dealer_invites",
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
                            admin=Depends(current_admin)):
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
            await db.password_resets.delete_many({"email": u["email"]})
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
        for kategorie in ("protocol", "pickup", "resale", "logo"):
            try:
                dateien += storage.delete_prefix(f"{kategorie}/{dealer_id}/")
            except Exception as exc:
                datei_fehler.append(f"{kategorie}: {exc}")
        geloescht["dateien"] = dateien
        if datei_fehler:
            geloescht["datei_fehler"] = datei_fehler
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
    user_id: str, body: AdminActiveIn, admin=Depends(current_admin)
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
    if u.get("id") == admin.get("id") and not body.active:
        raise HTTPException(400, "Du kannst dich nicht selbst sperren")
    patch = {"active": bool(body.active), "updated_at": now_iso()}
    if not body.active:
        patch["current_session_id"] = None
    await db.users.update_one({"id": user_id}, {"$set": patch})
    await log_activity(admin.get("dealer_id", ""), admin["id"],
                       "admin.user.entsperrt" if body.active else "admin.user.gesperrt",
                       ref=user_id, meta={"email": u.get("email", "")})
    return {"ok": True, "active": bool(body.active)}


@router.post("/admin/users/{user_id}/password")
async def admin_user_set_password(
    user_id: str, body: AdminUserPasswordIn, admin=Depends(current_admin)
):
    """Setzt das Passwort eines Nutzers zurueck (Admin-Funktion)."""
    if len(body.new_password or "") < 8:
        raise HTTPException(400, "Passwort muss mind. 8 Zeichen haben")
    u = await db.users.find_one({"id": user_id})
    if not u:
        raise HTTPException(404, "Nutzer nicht gefunden")
    await db.users.update_one(
        {"id": user_id},
        {"$set": {
            "password_hash": hash_password(body.new_password),
            "current_session_id": None,
            "updated_at": now_iso(),
        }},
    )
    await log_activity(admin.get("dealer_id", ""), admin["id"],
                       "admin.passwort.zurueckgesetzt",
                       ref=user_id, meta={"email": u.get("email", "")})
    return {"ok": True}


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
                              admin=Depends(current_admin)):
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
@router.post("/admin/sucher/{sucher_id}/abo")
async def admin_set_sucher_abo(sucher_id: str, body: dict = Body(...),
                               admin=Depends(current_admin)):
    """Sucher-Abo aktivieren/verlängern (plan: monthly|yearly) oder mit
    plan=null aufheben. Legt/aktualisiert eine subscriptions-Zeile mit
    subject_user_id an — damit gilt der Sucher als abo-berechtigt."""
    from routes.team import SUCHER_PLANS
    sucher = await db.users.find_one(
        {"id": sucher_id, "role": "sucher"}, {"_id": 0, "id": 1, "dealer_id": 1})
    if not sucher:
        raise HTTPException(404, "Sucher nicht gefunden")
    plan = body.get("plan")
    if plan is None:
        await db.subscriptions.update_many(
            {"subject_user_id": sucher_id},
            {"$set": {"status": "cancelled",
                      "expires_at": now_iso(), "updated_at": now_iso()}})
        await log_activity(admin.get("dealer_id", ""), admin["id"],
                           "admin.sucher.abo.aufgehoben", ref=sucher_id)
        return {"ok": True, "active": False}
    if plan not in SUCHER_PLANS:
        raise HTTPException(400, f"Unbekannter Abo-Zeitraum: {plan}")
    days = SUCHER_PLANS[plan]["days"]
    # Restlaufzeit erhalten (PR-Review 09/2026): erneutes Freischalten
    # verlaengert ab dem bisherigen Ablauf, nicht ab "jetzt".
    basis = _restlaufzeit_basis(
        (await db.subscriptions.find_one(
            {"subject_user_id": sucher_id, "status": "active"},
            {"_id": 0, "expires_at": 1}) or {}).get("expires_at"))
    expires_at = (basis + timedelta(days=days)).isoformat()
    await db.subscriptions.delete_many({"subject_user_id": sucher_id})
    await db.subscriptions.insert_one({
        "id": str(uuid.uuid4()),
        "dealer_id": sucher["dealer_id"],
        "subject_user_id": sucher_id,
        "plan": plan, "status": "active",
        "expires_at": expires_at,
        "price": SUCHER_PLANS[plan]["price"],
        "activated_by": admin.get("email", ""),
        "created_at": now_iso(),
    })
    await log_activity(admin.get("dealer_id", ""), admin["id"],
                       "admin.sucher.abo.freigeschaltet", ref=sucher_id,
                       meta={"plan": plan})
    # Offene Anfrage im selben Vorgang schliessen — vorher waren Freischalten
    # und Schliessen getrennt; scheiterte das Schliessen, blieb der erfuellte
    # Antrag offen und konnte doppelt bearbeitet werden.
    await db.plan_requests.update_many(
        {"type": "sucher_abo", "subject_user_id": sucher_id, "status": "offen"},
        {"$set": {"status": "erledigt", "updated_at": now_iso(),
                  "erledigt_durch": "freischaltung"}})
    return {"ok": True, "active": True, "plan": plan, "expires_at": expires_at}


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
                                 admin=Depends(current_admin)):
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
    voll = await db.users.find_one({"id": buyer_id}, {"_id": 0, "marketplace_access": 1})
    acc = (voll or {}).get("marketplace_access") or {}
    basis = _restlaufzeit_basis(acc.get("expires_at") if acc.get("active") else None)
    expires_at = (basis + timedelta(days=BUYER_ACCESS_DAYS)).isoformat()
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
                                   _=Depends(current_admin)):
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
    if len(body.new_password or "") < 8:
        raise HTTPException(400, "Neues Passwort muss mind. 8 Zeichen haben")
    user = await db.users.find_one({"id": admin["id"]})
    if not user or not verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(401, "Aktuelles Passwort ist nicht korrekt")
    await db.users.update_one(
        {"id": admin["id"]},
        {"$set": {"password_hash": hash_password(body.new_password)}},
    )
    return {"ok": True}
