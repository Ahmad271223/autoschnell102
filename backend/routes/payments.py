"""Payments: Stripe checkout, status polling, webhook."""
import logging
import os
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from deps import current_user, db, now_iso

log = logging.getLogger("autohandel")

router = APIRouter()

# Module-scoped because Stripe webhook (POST /api/webhook/stripe) is mounted
# on the FastAPI app directly (not under /api), so it needs `app` in server.py.

# Beschluss 09/2026: Stripe gibt es NUR noch für Marktplatz-Käufer
# (20 €/Monat). Firmen- und Sucher-Abos rechnet der Betreiber per Rechnung
# ab und schaltet manuell frei (Admin → Freischaltungen). Die alten Pläne
# "monthly"/"yearly" bleiben unten in der Aktivierung erhalten, damit noch
# offene Alt-Transaktionen sauber abgeschlossen werden.
PLAN_PRICES = {
    "marktplatz": {"amount": 20.00, "currency": "eur",
                   "label": "Marktplatz-Zugang (30 Tage)", "days": 30},
}


class CheckoutIn(BaseModel):
    plan: str  # "monthly" | "yearly"
    origin_url: str

    @field_validator("origin_url")
    @classmethod
    def validate_origin_url(cls, v: str) -> str:
        """Block open-redirect / SSRF: origin_url must be a plain http/https URL
        and must match one of the allowed CORS origins (env CORS_ORIGINS).
        Without CORS_ORIGINS set, only localhost is accepted (dev mode)."""
        v = v.strip().rstrip("/")
        try:
            parsed = urlparse(v)
        except Exception:
            raise ValueError("origin_url ist keine gültige URL")
        scheme = (parsed.scheme or "").lower()
        if scheme not in ("http", "https"):
            raise ValueError("origin_url muss mit http:// oder https:// beginnen")
        cors_raw = os.environ.get("CORS_ORIGINS", "http://localhost:3000")
        allowed = {o.strip().rstrip("/") for o in cors_raw.split(",") if o.strip()}
        if v not in allowed:
            raise ValueError(
                "origin_url stimmt mit keiner erlaubten Domain überein "
                "(CORS_ORIGINS prüfen)"
            )
        return v


@router.post("/payments/checkout")
async def create_checkout(body: CheckoutIn, request: Request, user=Depends(current_user)):
    # Nur Marktplatz-Käufer zahlen online. Firmen/Sucher: Rechnung + manuelle
    # Freischaltung durch den Betreiber — der alte Stripe-Weg ist für sie zu.
    if user.get("role") != "b2b_buyer":
        raise HTTPException(403, "Sucher-Zugänge werden per Rechnung "
                                 "abgerechnet und vom Betreiber "
                                 "freigeschaltet — hier ist keine "
                                 "Online-Zahlung nötig.")
    if body.plan not in PLAN_PRICES:
        raise HTTPException(400, "Unbekannter Plan")
    pkg = PLAN_PRICES[body.plan]
    # Stripe-Anbindung ist optional (lokale Installationen haben weder das
    # emergentintegrations-Paket noch einen STRIPE_API_KEY). Sauber mit 503
    # antworten statt mit einem unhandled 500 abzustürzen.
    try:
        from emergentintegrations.payments.stripe.checkout import (
            StripeCheckout, CheckoutSessionRequest,
        )
    except ImportError:
        raise HTTPException(
            503,
            "Online-Zahlung ist auf diesem Server nicht eingerichtet. "
            "Bitte den Administrator kontaktieren — das Abo kann im "
            "Admin-Bereich manuell freigeschaltet werden.",
        )
    api_key = os.environ.get("STRIPE_API_KEY", "")
    if not api_key:
        raise HTTPException(
            503,
            "Online-Zahlung ist auf diesem Server nicht eingerichtet "
            "(STRIPE_API_KEY fehlt). Bitte den Administrator kontaktieren.",
        )
    host_url = str(request.base_url).rstrip("/")
    webhook_url = f"{host_url}/api/webhook/stripe"
    sc = StripeCheckout(api_key=api_key, webhook_url=webhook_url)
    origin = body.origin_url.rstrip("/")
    success_url = f"{origin}/markt/zahlung-erfolg?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{origin}/markt"
    metadata = {
        "user_id": user["id"], "dealer_id": user.get("dealer_id") or "",
        "plan": body.plan,
    }
    req = CheckoutSessionRequest(
        amount=float(pkg["amount"]), currency=pkg["currency"],
        success_url=success_url, cancel_url=cancel_url, metadata=metadata,
    )
    session = await sc.create_checkout_session(req)
    await db.payment_transactions.insert_one({
        "id": str(uuid.uuid4()), "session_id": session.session_id,
        "user_id": user["id"], "dealer_id": user.get("dealer_id"),
        "plan": body.plan, "amount": pkg["amount"], "currency": pkg["currency"],
        "payment_status": "initiated", "status": "pending",
        "metadata": metadata, "created_at": now_iso(),
    })
    return {"url": session.url, "session_id": session.session_id}


async def _activate_paid_transaction(tx: dict, session_id: str) -> None:
    """Schaltet nach bestätigter Zahlung frei — je nach Plan:

    - "marktplatz": Marktplatz-Zugang des Käufers um 30 Tage verlängern
      (ab jetzt bzw. ab bisherigem Ablauf, falls der in der Zukunft liegt).
    - Alt-Pläne "monthly"/"yearly": persönliches Sucher-Abo (Bestand).

    Idempotent: Der Aufrufer stellt über den atomaren payment_status-
    Übergang sicher, dass die Aktivierung genau einmal läuft; das
    subscriptions-Upsert ist zusätzlich per session_id geschützt.
    """
    plan = tx.get("plan")
    now = datetime.now(timezone.utc)
    if plan == "marktplatz":
        days = PLAN_PRICES["marktplatz"]["days"]
        u = await db.users.find_one({"id": tx.get("user_id")},
                                    {"_id": 0, "marketplace_access": 1})
        basis = now
        alt = ((u or {}).get("marketplace_access") or {}).get("expires_at")
        if alt:
            try:
                dt = datetime.fromisoformat(alt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt > basis:
                    basis = dt
            except (TypeError, ValueError):
                pass
        await db.users.update_one(
            {"id": tx.get("user_id")},
            {"$set": {"marketplace_access": {
                "active": True, "plan": "monthly",
                "price": PLAN_PRICES["marktplatz"]["amount"],
                "expires_at": (basis + timedelta(days=days)).isoformat(),
                "activated_by": "stripe",
                "session_id": session_id,
                "updated_at": now_iso()}}})
        return
    # Alt-Pläne (Bestands-Transaktionen von vor 09/2026)
    days = 30 if plan == "monthly" else 365
    expires_at = (now + timedelta(days=days)).isoformat()
    await db.subscriptions.update_one(
        {"session_id": session_id},
        {"$setOnInsert": {
            "id": str(uuid.uuid4()), "dealer_id": tx.get("dealer_id"),
            "subject_user_id": tx.get("user_id"),
            "plan": plan, "status": "active", "expires_at": expires_at,
            "session_id": session_id, "created_at": now_iso(),
        }},
        upsert=True,
    )


@router.get("/payments/status/{session_id}")
async def payment_status(session_id: str, request: Request, user=Depends(current_user)):
    tx = await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})
    if not tx:
        raise HTTPException(404, "Zahlung nicht gefunden")

    # Eigentuemer-Pruefung SOFORT nach dem Lookup — vor JEDER Rueckgabe.
    # (Vorher wurden bereits bezahlte Transaktionen vor dieser Pruefung
    # zurueckgegeben: wer eine fremde Session-ID kannte, sah fremde
    # Zahlungs-Metadaten.)
    if tx.get("user_id") != user["id"] and user.get("role") != "admin":
        raise HTTPException(403, "Diese Zahlung gehört dir nicht")

    if tx["payment_status"] == "paid":
        return tx

    # 1) Webhook may have already activated a subscription
    sub = await db.subscriptions.find_one({"session_id": session_id})
    if sub:
        await db.payment_transactions.update_one(
            {"session_id": session_id},
            {"$set": {"payment_status": "paid", "status": "complete", "updated_at": now_iso()}},
        )
        return await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})

    # 2) Try direct Stripe SDK call. The Emergent Stripe proxy currently only
    # supports POST (create), not GET (retrieve), so this normally 404s. That's OK.
    try:
        import stripe as stripe_sdk
        stripe_sdk.api_key = os.environ["STRIPE_API_KEY"]
        if hasattr(stripe_sdk, "api_base"):
            stripe_sdk.api_base = "https://integrations.emergentagent.com/stripe"
        # Sync-SDK in gepollter Route — ohne to_thread stand der Loop
        # je Poll einen HTTP-Roundtrip lang (Review 09/2026).
        cs = await asyncio.to_thread(stripe_sdk.checkout.Session.retrieve,
                                     session_id)
        payment_status_str = cs.get("payment_status") or "pending"
        status_str = cs.get("status") or "open"
    except Exception as e:
        log.info(f"Stripe lookup unavailable for {session_id}: {e}")
        payment_status_str = None
        status_str = None

    # 3) Only activate subscription when Stripe explicitly confirms payment.
    # Do NOT trust the redirect URL alone — anyone can call this endpoint with
    # a known session_id and the old code would have granted a free subscription.
    # The webhook handler is the authoritative confirmation path.
    if payment_status_str != "paid":
        log.info(
            "Payment %s not confirmed by Stripe (status=%r) — returning pending status.",
            session_id, payment_status_str,
        )
        return await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0}) or tx

    # Stripe confirmed payment — atomarer Übergang auf "paid": nur wer den
    # Übergang gewinnt, aktiviert (verhindert Doppel-Aktivierung durch
    # gleichzeitige Status-Polls und Webhook).
    r = await db.payment_transactions.update_one(
        {"session_id": session_id, "payment_status": {"$ne": "paid"}},
        {"$set": {"payment_status": "paid", "status": "complete",
                  "updated_at": now_iso()}})
    if r.modified_count:
        await _activate_paid_transaction(tx, session_id)
    return await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})


# Stripe Webhook lebt unter /api/webhook/stripe (nicht /api/webhook/stripe over
# the api router prefix). Wird in server.py direkt an `app` registriert.
async def stripe_webhook(request: Request):
    body = await request.body()
    sig = request.headers.get("Stripe-Signature", "")
    session_id = None
    payment_status_str = None

    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if secret:
        # Bevorzugter Weg: Signatur mit dem offiziellen Stripe-SDK und dem
        # STRIPE_WEBHOOK_SECRET pruefen (der Wert aus dem Stripe-Dashboard,
        # "whsec_…"). Nur so ist die Herkunft kryptographisch belegt.
        try:
            import stripe as stripe_sdk
            event = stripe_sdk.Webhook.construct_event(body, sig, secret)
        except Exception as e:
            # 400 (kein 200!): Stripe zeigt den Fehler im Dashboard an und
            # versucht es erneut; Faelscher bekommen eine klare Ablehnung.
            log.warning("Stripe webhook signature invalid: %s", e)
            raise HTTPException(400, "Ungültige Stripe-Signatur")
        obj = (event.get("data") or {}).get("object") or {}
        if event.get("type") in ("checkout.session.completed",
                                 "checkout.session.async_payment_succeeded"):
            session_id = obj.get("id")
            payment_status_str = obj.get("payment_status")
    else:
        # Fallback ohne konfiguriertes Secret (nur Entwicklung): Verifikation
        # der Integrationsbibliothek ueberlassen. NIE auf rohes JSON-Parsen
        # zurueckfallen — das wuerde die Signaturpruefung umgehen und jedem
        # erlauben, ein "paid" zu faelschen.
        try:
            from emergentintegrations.payments.stripe.checkout import StripeCheckout
            api_key = os.environ["STRIPE_API_KEY"]
            host_url = str(request.base_url).rstrip("/")
            sc = StripeCheckout(api_key=api_key, webhook_url=f"{host_url}/api/webhook/stripe")
            evt = await sc.handle_webhook(body, sig)
            session_id = evt.session_id
            payment_status_str = evt.payment_status
        except Exception as e:
            log.warning(f"Stripe webhook verification failed: {e}. Rejecting unverified payload.")
            raise HTTPException(400, "Webhook konnte nicht verifiziert werden")

    if payment_status_str == "paid" and session_id:
        tx = await db.payment_transactions.find_one({"session_id": session_id})
        if tx:
            # Atomarer Übergang — genau EIN Gewinner aktiviert (Webhook kann
            # mehrfach feuern, Status-Poll läuft parallel).
            r = await db.payment_transactions.update_one(
                {"session_id": session_id, "payment_status": {"$ne": "paid"}},
                {"$set": {"payment_status": "paid", "status": "complete",
                          "updated_at": now_iso()}},
            )
            if r.modified_count:
                await _activate_paid_transaction(tx, session_id)
    return {"ok": True}
