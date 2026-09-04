"""Payments: Stripe Checkout (offizielles SDK), Status-Abfrage, Webhook,
Zustandsautomat der Transaktionen und Abgleich haengender Zahlungen.

Stripe-Webhook
--------------
Der Endpunkt lautet  POST /api/webhook/stripe  (server.py mountet
`stripe_webhook` direkt an `app`). Das Backend registriert ihn NICHT selbst
bei Stripe — er wird im Stripe-Dashboard (Entwickler -> Webhooks) auf
    https://<oeffentliche API-Adresse>/api/webhook/stripe
eingetragen (Ereignisse: checkout.session.completed,
checkout.session.async_payment_succeeded, checkout.session.async_payment_failed,
checkout.session.expired). Das dort angezeigte Signing-Secret ("whsec_...")
gehoert in STRIPE_WEBHOOK_SECRET. Ohne dieses Secret nimmt der Webhook KEINE
Ereignisse an (503) — eine unsignierte Zahlungsbestaetigung wird nie
akzeptiert. Aus dem Host-Header / request.base_url wird nichts abgeleitet;
braeuchte jemand eine absolute API-Adresse, kommt sie aus env PUBLIC_API_URL.

Zustandsautomat payment_transactions.status
-------------------------------------------
    initiated -> paid -> activating -> active
                             \\-> activation_failed  (Poll/Abgleich holt nach)
    initiated -> failed   (checkout.session.async_payment_failed)
    initiated -> expired  (checkout.session.expired)
`payment_status` spiegelt den Stripe-Wert (unpaid/paid/...). Alt-Datensaetze
von vor 09/2026 tragen status "pending"/"complete" — beide werden weiter
verstanden (pending wie initiated, complete wie active).
"""
import asyncio
import logging
import os
import uuid

from pymongo import ReturnDocument
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from betrieb import alarm
from deps import current_user, db, now_iso

log = logging.getLogger("autohandel")

router = APIRouter()

# Beschluss 09/2026: Stripe gibt es NUR noch für Marktplatz-Käufer
# (20 €/Monat). Firmen- und Sucher-Abos rechnet der Betreiber per Rechnung
# ab und schaltet manuell frei (Admin → Freischaltungen). Die alten Pläne
# "monthly"/"yearly" bleiben unten in der Aktivierung erhalten, damit noch
# offene Alt-Transaktionen sauber abgeschlossen werden.
PLAN_PRICES = {
    "marktplatz": {"amount": 20.00, "currency": "eur",
                   "label": "Marktplatz-Zugang (30 Tage)", "days": 30},
}
PRODUKT_NAME = "AutoSchnell Marktplatz-Zugang 30 Tage"

NICHT_AKTIV_MELDUNG = ("Online-Zahlung ist nicht aktiv — bitte den "
                       "Marktplatz-Zugang per Rechnung beim Betreiber anfragen.")

# Status-Gruppen des Zustandsautomaten (inkl. Alt-Werte)
_OFFEN = ("initiated", "pending")            # bezahlbar, noch nichts passiert
_BEZAHLT_OHNE_ZUGANG = ("paid", "activating", "activation_failed")
_ERNEUT_AKTIVIERBAR = ("paid", "activation_failed")   # nicht "activating": laeuft gerade
_ABGESCHLOSSEN = ("active", "complete")

# Abgleich (zahlungen_abgleichen): so lange darf eine bezahlte Transaktion
# unfreigeschaltet sein, bevor der Abgleich eingreift; und so lange darf
# "activating" dauern, bevor es als haengengeblieben gilt.
ABGLEICH_WARTEZEIT = timedelta(minutes=2)
ABGLEICH_HAENGT_AB = timedelta(minutes=10)


def _truthy(v: Optional[str]) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes")


def stripe_aktiv() -> bool:
    """Online-Zahlung nur, wenn API-Key UND Webhook-Secret gesetzt sind und
    kein Mock-Betrieb (Lasttest/CI, MOCK_PROVIDER_FETCH) laeuft. Ohne
    Webhook-Secret gaebe es keine verifizierte Zahlungsbestaetigung — dann
    lieber gar keinen Checkout anbieten als Geld ohne Zugang einzunehmen."""
    return bool(os.environ.get("STRIPE_API_KEY", "").strip()
                and os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
                and not _truthy(os.environ.get("MOCK_PROVIDER_FETCH")))


def _als_dict(obj) -> dict:
    """StripeObject (kein dict-Subtyp mehr seit SDK 8) -> einfaches dict."""
    if isinstance(obj, dict):
        return obj
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return dict(obj)


def _cent(betrag: float) -> int:
    return int(round(float(betrag) * 100))


def _parse_ts(s) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def _tx(session_id: str) -> Optional[dict]:
    return await db.payment_transactions.find_one({"session_id": session_id},
                                                  {"_id": 0})


class CheckoutIn(BaseModel):
    plan: str  # "marktplatz" (Alt-Plaene monthly/yearly werden nicht mehr verkauft)
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


@router.get("/payments/config")
async def payments_config():
    """Oeffentlich (keine Geheimnisse): Ist die Online-Zahlung aktiv, und was
    kostet der Marktplatz-Zugang? Das Frontend blendet den Stripe-Button
    aus und verweist auf die Rechnung, wenn stripe_aktiv false ist.

    `marktplatz_kostenlos` sagt der Startseite und der Registrierung, ob der
    Zugang derzeit gratis ist. Ohne diese Angabe wirbt die Startseite weiter
    mit einem Preis, den niemand zahlt (Befund 09/2026) — und beim Umlegen
    des Schalters muesste jemand daran denken, den Text zu aendern."""
    pkg = PLAN_PRICES["marktplatz"]
    from routes.marketplace import MARKTPLATZ_KOSTENLOS
    return {"stripe_aktiv": stripe_aktiv(), "preis": pkg["amount"],
            "waehrung": pkg["currency"], "tage": pkg["days"],
            "marktplatz_kostenlos": bool(MARKTPLATZ_KOSTENLOS)}


@router.post("/payments/checkout")
async def create_checkout(body: CheckoutIn, user=Depends(current_user)):
    # Nur Marktplatz-Käufer zahlen online. Firmen/Sucher: Rechnung + manuelle
    # Freischaltung durch den Betreiber — der alte Stripe-Weg ist für sie zu.
    if user.get("role") != "b2b_buyer":
        raise HTTPException(403, "Sucher-Zugänge werden per Rechnung "
                                 "abgerechnet und vom Betreiber "
                                 "freigeschaltet — hier ist keine "
                                 "Online-Zahlung nötig.")
    if body.plan not in PLAN_PRICES:
        raise HTTPException(400, "Unbekannter Plan")
    if not stripe_aktiv():
        raise HTTPException(503, NICHT_AKTIV_MELDUNG)
    pkg = PLAN_PRICES[body.plan]
    # Erfolgs-/Abbruch-Adresse kommt AUSSCHLIESSLICH aus der vom Validator
    # gegen CORS_ORIGINS geprueften origin_url — nie aus dem Host-Header.
    origin = body.origin_url.rstrip("/")
    success_url = f"{origin}/markt/zahlung-erfolg?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{origin}/markt"
    tx_id = str(uuid.uuid4())
    metadata = {
        "user_id": user["id"], "dealer_id": user.get("dealer_id") or "",
        "plan": body.plan, "tx_id": tx_id,
    }
    try:
        # Sync-SDK -> to_thread, damit der Event-Loop waehrend des
        # HTTP-Roundtrips zu Stripe nicht steht. Idempotency-Key je
        # Transaktion: SDK-interne Wiederholungen erzeugen keine zweite Session.
        session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            api_key=os.environ["STRIPE_API_KEY"].strip(),
            idempotency_key=f"checkout-{tx_id}",
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": pkg["currency"],
                    "unit_amount": _cent(pkg["amount"]),
                    "product_data": {"name": PRODUKT_NAME},
                },
                "quantity": 1,
            }],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata=metadata,
            client_reference_id=tx_id,
        )
    except Exception as exc:
        log.warning("Stripe-Checkout konnte nicht angelegt werden: %s", exc)
        raise HTTPException(502, "Stripe ist gerade nicht erreichbar — bitte "
                                 "in einer Minute erneut versuchen.")
    s = _als_dict(session)
    jetzt = now_iso()
    await db.payment_transactions.insert_one({
        "id": tx_id, "session_id": s["id"],
        "user_id": user["id"], "dealer_id": user.get("dealer_id"),
        "plan": body.plan, "amount": pkg["amount"], "currency": pkg["currency"],
        "status": "initiated",
        "payment_status": s.get("payment_status") or "unpaid",
        "metadata": metadata, "created_at": jetzt, "updated_at": jetzt,
    })
    return {"url": s.get("url"), "session_id": s["id"]}


# ---------- Freischaltung ----------
async def _zugang_freischalten(tx: dict, session_id: str) -> str:
    """Eigentliche Freischaltung — je nach Plan:

    - "marktplatz": Marktplatz-Zugang des Käufers um 30 Tage verlängern
      (ab jetzt bzw. ab bisherigem Ablauf, falls der in der Zukunft liegt).
    - Alt-Pläne "monthly"/"yearly": persönliches Sucher-Abo (Bestand),
      idempotent über den Unique-Index subscriptions.session_id.

    Liefert das neue Ablaufdatum (ISO)."""
    plan = tx.get("plan")
    now = datetime.now(timezone.utc)
    if plan == "marktplatz":
        days = PLAN_PRICES["marktplatz"]["days"]
        u = await db.users.find_one({"id": tx.get("user_id")},
                                    {"_id": 0, "id": 1, "marketplace_access": 1})
        # Achtung: bei einem Konto OHNE marketplace_access liefert die
        # Projektion sonst ein leeres Dokument — deshalb auf None pruefen,
        # nicht auf "leer".
        if u is None:
            raise RuntimeError(
                f"Kein Konto zu Zahlung {session_id} gefunden "
                f"(user_id={tx.get('user_id')})")
        basis = now
        alt = _parse_ts(((u or {}).get("marketplace_access") or {}).get("expires_at"))
        if alt and alt > basis:
            basis = alt
        # Audit 09/2026: Das Ablaufdatum wird EINMALIG je Stripe-Session
        # festgeschrieben. Scheitert danach etwas (z.B. der Beleg) und der
        # Reparaturlauf wiederholt die Freischaltung, wird derselbe Wert
        # erneut gesetzt statt ein zweites Mal um 30 Tage verlaengert.
        grant = await db.zugang_grants.find_one_and_update(
            {"session_id": session_id},
            {"$setOnInsert": {
                "id": str(uuid.uuid4()), "session_id": session_id,
                "user_id": tx.get("user_id"), "plan": "marktplatz",
                "tage": days, "basis": basis.isoformat(),
                "expires_at": (basis + timedelta(days=days)).isoformat(),
                "created_at": now_iso()}},
            upsert=True, return_document=ReturnDocument.AFTER)
        expires_at = grant["expires_at"]
        r = await db.users.update_one(
            {"id": tx.get("user_id")},
            {"$set": {"marketplace_access": {
                "active": True, "plan": "monthly",
                "price": PLAN_PRICES["marktplatz"]["amount"],
                "expires_at": expires_at,
                "activated_by": "stripe",
                "session_id": session_id,
                "updated_at": now_iso()}}})
        if r.matched_count == 0:
            raise RuntimeError(
                f"Zugang zu Zahlung {session_id} konnte keinem Konto "
                f"zugeordnet werden (user_id={tx.get('user_id')})")
        return expires_at
    # Alt-Pläne (Bestands-Transaktionen von vor 09/2026)
    days = 30 if plan == "monthly" else 365
    expires_at = (now + timedelta(days=days)).isoformat()
    # Es gilt genau EIN aktives Abo je Konto (Index
    # ein_aktives_abo_je_konto). Ein bisheriges Abo wird deshalb ZUERST
    # als "ersetzt" markiert — die Historie bleibt erhalten, die eigene
    # Session ist ausgenommen (Wiederholungslauf).
    if tx.get("user_id"):
        await db.subscriptions.update_many(
            {"subject_user_id": tx["user_id"], "status": "active",
             "session_id": {"$ne": session_id}},
            {"$set": {"status": "ersetzt", "ersetzt_durch": session_id,
                      "updated_at": now_iso()}})
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
    return expires_at


async def _zahlung_verbuchen(tx: dict, session_id: str, period_until: str) -> None:
    """Genau EIN Zahlungsbeleg je Stripe-Session in manual_payments (die
    Betreiber-Buchhaltung, Admin -> Zahlungen). Idempotent: existiert schon
    ein Beleg mit dieser zahlung_ref, passiert nichts. Alt-Plaene brauchen
    keinen Beleg — dort ist das Abo-Dokument (subscriptions) der Nachweis."""
    if tx.get("plan") != "marktplatz":
        return
    pkg = PLAN_PRICES["marktplatz"]
    await db.manual_payments.update_one(
        {"zahlung_ref": session_id, "quelle": "stripe"},
        {"$setOnInsert": {
            "id": str(uuid.uuid4()),
            "dealer_id": tx.get("dealer_id"),
            "subject_user_id": tx.get("user_id"),
            "plan": "marktplatz",
            "amount": float(tx.get("amount") or pkg["amount"]),
            "currency": tx.get("currency") or pkg["currency"],
            "paid_at": now_iso()[:10],
            "period_until": period_until,
            "quelle": "stripe",
            "zahlung_ref": session_id,
            "note": "",
            "recorded_by": "stripe",
            "created_at": now_iso(),
        }},
        upsert=True,
    )


async def _activate_paid_transaction(tx: dict, session_id: str) -> bool:
    """Freischaltung nach bestaetigter Zahlung — genau einmal je Session.

    Ablauf: atomarer Uebergang paid/activation_failed -> activating (nur der
    Gewinner dieses update_one macht weiter; Webhook-Wiederholungen,
    parallele Status-Polls und der Abgleich stossen sich hier ab), dann
    Zugang freischalten, Zahlungsbeleg verbuchen, Status "active". Scheitert
    die Freischaltung: "activation_failed" + Betriebsalarm
    (zahlung_ohne_zugang) — Poll und stuendlicher Abgleich holen es nach.

    Liefert True, wenn DIESER Aufruf freigeschaltet hat."""
    r = await db.payment_transactions.update_one(
        {"session_id": session_id, "status": {"$in": list(_ERNEUT_AKTIVIERBAR)}},
        {"$set": {"status": "activating", "updated_at": now_iso()}})
    if r.matched_count == 0:
        vorhanden = await db.payment_transactions.find_one(
            {"session_id": session_id}, {"_id": 0, "status": 1})
        if vorhanden is not None:
            # Schon active, gerade activating (anderer Aufrufer) oder noch
            # gar nicht bezahlt: nichts zu tun.
            return False
        # Keine gespeicherte Transaktion — direkter Aufruf (Funktionstest,
        # Altbestand). Kein Status zu fuehren; die Freischaltung selbst ist
        # trotzdem idempotent (Abo je session_id, Beleg je zahlung_ref).
        log.warning("Aktivierung ohne gespeicherte Transaktion: %s", session_id)
    try:
        bis = await _zugang_freischalten(tx, session_id)
        await _zahlung_verbuchen(tx, session_id, bis)
    except Exception as exc:
        fehler = str(exc)[:300]
        log.exception("Freischaltung nach Zahlung fehlgeschlagen: %s", session_id)
        await db.payment_transactions.update_one(
            {"session_id": session_id, "status": "activating"},
            {"$set": {"status": "activation_failed", "activation_error": fehler,
                      "updated_at": now_iso()}})
        await alarm(db, "zahlung_ohne_zugang", ref=session_id,
                    user_id=tx.get("user_id") or "", plan=tx.get("plan") or "",
                    fehler=fehler)
        return False
    jetzt = now_iso()
    await db.payment_transactions.update_one(
        {"session_id": session_id, "status": "activating"},
        {"$set": {"status": "active", "activated_at": jetzt, "updated_at": jetzt,
                  "period_until": bis, "activation_error": None}})
    return True


async def _zahlung_bestaetigt(session_id: str, obj: dict) -> None:
    """Stripe hat die Zahlung bestaetigt (verifizierter Webhook oder
    Session.retrieve mit unserem API-Key): initiated -> paid, dann
    freischalten. Beide Schritte sind idempotent."""
    tx = await _tx(session_id)
    if not tx:
        # Geld eingenommen, aber kein Vorgang dazu — muss ein Mensch ansehen.
        log.error("Stripe meldet Zahlung fuer unbekannte Session %s", session_id)
        await alarm(db, "zahlung_ohne_zugang", ref=session_id,
                    fehler="Transaktion zu dieser Stripe-Session unbekannt")
        return
    jetzt = now_iso()
    await db.payment_transactions.update_one(
        {"session_id": session_id, "status": {"$in": list(_OFFEN)}},
        {"$set": {"status": "paid", "payment_status": "paid", "paid_at": jetzt,
                  "updated_at": jetzt,
                  "stripe_payment_intent": obj.get("payment_intent")}})
    await _activate_paid_transaction(tx, session_id)


async def _endzustand(session_id: Optional[str], status: str) -> None:
    """failed / expired: nur offene Transaktionen umsetzen (eine bereits
    bezahlte wird durch ein spaetes expired-Ereignis nicht zurueckgesetzt)."""
    if not session_id:
        return
    await db.payment_transactions.update_one(
        {"session_id": session_id, "status": {"$in": list(_OFFEN)}},
        {"$set": {"status": status, "payment_status": status,
                  "updated_at": now_iso()}})


@router.get("/payments/status/{session_id}")
async def payment_status(session_id: str, user=Depends(current_user)):
    tx = await _tx(session_id)
    if not tx:
        raise HTTPException(404, "Zahlung nicht gefunden")

    # Eigentuemer-Pruefung SOFORT nach dem Lookup — vor JEDER Rueckgabe.
    # (Vorher wurden bereits bezahlte Transaktionen vor dieser Pruefung
    # zurueckgegeben: wer eine fremde Session-ID kannte, sah fremde
    # Zahlungs-Metadaten.)
    if tx.get("user_id") != user["id"] and user.get("role") != "admin":
        raise HTTPException(403, "Diese Zahlung gehört dir nicht")

    status = tx.get("status")
    if status in _ABGESCHLOSSEN or status in ("failed", "expired"):
        return tx
    if status in _BEZAHLT_OHNE_ZUGANG:
        # Bezahlt, Zugang fehlt noch (Webhook-Aktivierung gescheitert oder
        # gerade unterwegs): hier nachholen — der Poll des Kaeufers ist der
        # schnellste Weg, ihm seinen Zugang zu geben.
        await _activate_paid_transaction(tx, session_id)
        return await _tx(session_id) or tx

    # initiated: bei Stripe nachsehen. Der Webhook ist der massgebliche Weg;
    # das Nachsehen ueber unseren API-Key ist gleichwertig vertrauenswuerdig
    # (NIE die Redirect-URL allein als Zahlungsbeweis nehmen).
    api_key = os.environ.get("STRIPE_API_KEY", "").strip()
    if not api_key:
        return tx
    try:
        # Sync-SDK in gepollter Route — ohne to_thread stand der Loop
        # je Poll einen HTTP-Roundtrip lang (Review 09/2026).
        cs = _als_dict(await asyncio.to_thread(
            stripe.checkout.Session.retrieve, session_id, api_key=api_key))
    except Exception as e:
        log.info("Stripe lookup unavailable for %s: %s", session_id, e)
        return tx
    if cs.get("payment_status") == "paid":
        await _zahlung_bestaetigt(session_id, cs)
    elif cs.get("status") == "expired":
        await _endzustand(session_id, "expired")
    else:
        log.info("Payment %s not confirmed by Stripe (payment_status=%r) — "
                 "returning pending status.", session_id, cs.get("payment_status"))
    return await _tx(session_id) or tx


# ---------- Webhook ----------
# Lebt unter POST /api/webhook/stripe — in server.py direkt an `app`
# registriert (siehe Modul-Docstring: Eintrag im Stripe-Dashboard, Secret in
# STRIPE_WEBHOOK_SECRET).
async def stripe_webhook(request: Request):
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        # Niemals unverifizierte Ereignisse annehmen. 503 statt 200: Stripe
        # zeigt den Fehler im Dashboard und wiederholt, sobald das Secret da ist.
        log.error("Stripe-Webhook ohne STRIPE_WEBHOOK_SECRET aufgerufen — abgelehnt")
        raise HTTPException(503, "Stripe-Webhook ist nicht konfiguriert "
                                 "(STRIPE_WEBHOOK_SECRET fehlt)")
    body = await request.body()
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = _als_dict(stripe.Webhook.construct_event(body, sig, secret))
    except Exception as e:
        # 400 (kein 200!): Stripe zeigt den Fehler im Dashboard an und
        # versucht es erneut; Faelscher bekommen eine klare Ablehnung.
        log.warning("Stripe webhook signature invalid: %s", e)
        raise HTTPException(400, "Ungültige Stripe-Signatur")

    typ = event.get("type") or ""
    obj = (event.get("data") or {}).get("object") or {}
    session_id = obj.get("id")
    if typ in ("checkout.session.completed",
               "checkout.session.async_payment_succeeded"):
        if obj.get("payment_status") == "paid" and session_id:
            await _zahlung_bestaetigt(session_id, obj)
        else:
            # completed mit payment_status "unpaid": verzoegerte Zahlart
            # (SEPA & Co.) — Freischaltung erst bei async_payment_succeeded.
            log.info("Stripe %s fuer %s ohne Zahlungseingang (payment_status=%r)",
                     typ, session_id, obj.get("payment_status"))
    elif typ == "checkout.session.async_payment_failed":
        await _endzustand(session_id, "failed")
    elif typ == "checkout.session.expired":
        await _endzustand(session_id, "expired")
    else:
        log.debug("Stripe-Ereignis %s ignoriert", typ)
    return {"ok": True, "type": typ}


# ---------- Abgleich (stuendlich aus cleanup_service) ----------
async def zahlungen_abgleichen(db_, *, jetzt: Optional[datetime] = None) -> dict:
    """Bezahlte, aber nicht freigeschaltete Transaktionen nachholen.

    - "paid" / "activation_failed", aelter als ABGLEICH_WARTEZEIT: erneut
      freischalten (der Webhook/Poll hatte seine Chance).
    - "activating", aelter als ABGLEICH_HAENGT_AB: haengengeblieben (Prozess
      mitten in der Freischaltung gestorben) -> per Compare-and-Set auf
      activation_failed zuruecksetzen und erneut versuchen. Juengere
      "activating" laufen gerade und bleiben unangetastet.

    `db_` ist die Datenbank des Aufrufers (cleanup_service reicht dasselbe
    Objekt durch wie deps.db). Liefert Zaehler fuer das Cleanup-Protokoll."""
    now = jetzt or datetime.now(timezone.utc)
    stats = {"geprueft": 0, "aktiviert": 0, "fehlgeschlagen": 0,
             "uebersprungen": 0}
    cursor = db_.payment_transactions.find(
        {"status": {"$in": list(_BEZAHLT_OHNE_ZUGANG)}}, {"_id": 0}
    ).sort("created_at", 1).limit(500)
    async for tx in cursor:
        stats["geprueft"] += 1
        sid = tx.get("session_id")
        stamp = _parse_ts(tx.get("updated_at") or tx.get("created_at"))
        alter = (now - stamp) if stamp else ABGLEICH_HAENGT_AB
        if tx.get("status") == "activating":
            if alter < ABGLEICH_HAENGT_AB:
                stats["uebersprungen"] += 1
                continue
            r = await db_.payment_transactions.update_one(
                {"session_id": sid, "status": "activating",
                 "updated_at": tx.get("updated_at")},
                {"$set": {"status": "activation_failed",
                          "activation_error": "Freischaltung haengengeblieben "
                                              "(>10 min activating) — Abgleich",
                          "updated_at": now_iso()}})
            if not r.matched_count:
                stats["uebersprungen"] += 1
                continue
            log.warning("Zahlung %s hing in 'activating' — erneuter Versuch", sid)
        elif alter < ABGLEICH_WARTEZEIT:
            stats["uebersprungen"] += 1
            continue
        if await _activate_paid_transaction(tx, sid):
            stats["aktiviert"] += 1
        else:
            stats["fehlgeschlagen"] += 1
    if stats["aktiviert"] or stats["fehlgeschlagen"]:
        log.info("Zahlungsabgleich: %s", stats)
    return stats
