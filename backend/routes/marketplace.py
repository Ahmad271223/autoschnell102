"""Phase 3: B2B-Marktplatz.

- Händlerprofile (öffentlich / privat) mit eigener Verkaufsseite
- Einmalige Einladungslinks (Gültigkeit + Nutzungslimit, Default 1 Nutzung)
- Eigene Rolle `b2b_buyer` (Zwischenhändler) — bewusst KEINE Händlerrolle
- Interessenten-Verwaltung: Interesse/Angebot → akzeptieren / ablehnen /
  Gegenangebot
- Preisstufen: öffentlich < B2B (registrierte Zwischenhändler) <
  privates Netzwerk (per Einladung)
"""
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator

from auth import (hash_password_async, new_session_id, create_token,
                  verify_password_async, _DUMMY_HASH)
from deps import current_user, db, log_activity, now_iso
from rate_limiter import client_ip, register_limiter, login_limiter
from routes.auth import _check_password_strength
from routes.bestand import current_haendler

router = APIRouter()


# ---------- Zugangs-Abo (Zwischenhändler) ----------
# Zwischenhändler zahlen einen monatlichen Zugang, um die zum Verkauf
# angebotenen Fahrzeuge sehen zu können. Freischaltung erfolgt (wie beim
# Sucher-Abo/Verkaufspaket) manuell über den Admin — Stripe ist ein
# austauschbarer Baustein.
BUYER_ACCESS_PRICE = 20.00         # € pro Monat (Stand 09/2026, Stripe)
BUYER_ACCESS_DAYS = 30


def _access_status(user: dict) -> dict:
    """Zugangsstatus eines Zwischenhändlers. Händler/Admin haben immer Zugang."""
    if user.get("role") in ("dealer", "admin"):
        return {"active": True, "plan": "intern", "expires_at": None,
                "price": BUYER_ACCESS_PRICE}
    acc = user.get("marketplace_access") or {}
    active = bool(acc.get("active"))
    exp = acc.get("expires_at")
    if active and exp:
        try:
            dt = datetime.fromisoformat(exp)
            if dt.tzinfo is None:                      # naive Alt-Werte tolerieren
                dt = dt.replace(tzinfo=timezone.utc)
            if dt < datetime.now(timezone.utc):
                active = False
        except (ValueError, TypeError):
            pass
    return {"active": active, "plan": acc.get("plan"),
            "expires_at": exp, "price": BUYER_ACCESS_PRICE}


# ---------- Auth-Hilfen ----------
async def current_buyer(user=Depends(current_user)):
    # Strikte Rollentrennung (PR-Review 09/2026): der Marktplatz ist der
    # Bereich der Zwischenhaendler (b2b_buyer). Haendler waren backendseitig
    # zugelassen, die Oberflaeche akzeptierte aber nur den Kaeufer-Token —
    # ein toter, ungetesteter Zugangspfad. Haendler verkaufen ueber ihre
    # Inserate; Admin-Konten verwalten nur (Freischaltungen unter /admin/*).
    if user.get("role") != "b2b_buyer":
        raise HTTPException(403, "Nur für registrierte Zwischenhändler")
    return user


async def require_marketplace_access(user=Depends(current_buyer)):
    """Wie current_buyer, aber Zwischenhändler brauchen ein aktives Zugangs-Abo,
    um Fahrzeuge sehen zu können (Händler/Admin ausgenommen)."""
    if not _access_status(user)["active"]:
        raise HTTPException(
            402, "Kein aktiver Marktplatz-Zugang – bitte Zugang freischalten "
                 f"({BUYER_ACCESS_PRICE:.2f} € / Monat).")
    return user


# ---------- Marken-Normalisierung (Filter-Matching) ----------
# Löst Schreibweisen-Unterschiede zwischen der Picker-Liste (volle Namen,
# z.B. "Volkswagen") und den Fahrzeugdaten (Kurzformen, z.B. "VW") auf.
# Kanonische Namen = Schreibweise der autoscout-Liste, klein & ohne Sonderzeichen.
_MAKE_ALIASES = {
    "vw": "volkswagen",
    "mercedes": "mercedesbenz",
    "merc": "mercedesbenz",
    "mb": "mercedesbenz",
    "amg": "mercedesbenz",
    "benz": "mercedesbenz",
    "rangerover": "landrover",
    "range": "landrover",
    "alfa": "alfaromeo",
    "vauxhall": "opel",
    "chevy": "chevrolet",
    "ds": "dsautomobiles",
    "byd": "byd",
    "vwn": "volkswagen",  # VW Nutzfahrzeuge
}


def _norm_make(s: Optional[str]) -> str:
    """Klein, ohne Leer-/Sonderzeichen, plus Alias-Auflösung (VW->Volkswagen)."""
    key = re.sub(r"[^a-z0-9]", "", (s or "").lower())
    return _MAKE_ALIASES.get(key, key)


def _make_regex_variants(filter_make: str) -> str:
    """Regex-Alternativen fuer den Mongo-Markenfilter — stellt die
    Alias-Toleranz von _make_matches wieder her (VW <-> Volkswagen,
    Mercedes <-> Mercedes-Benz). Erzeugt aus dem Filter alle Schreibweisen,
    die auf denselben normalisierten Namen zeigen, und matcht sie
    zeichenweise tolerant (Leer-/Sonderzeichen zwischen den Buchstaben)."""
    key = re.sub(r"[^a-z0-9]", "", (filter_make or "").lower())
    canon = _MAKE_ALIASES.get(key, key)
    variants = {key, canon} | {a for a, c in _MAKE_ALIASES.items() if c == canon}
    parts = []
    for v in sorted(variants, key=len, reverse=True):
        if len(v) < 2:
            continue
        # "mercedesbenz" soll auch "Mercedes-Benz" treffen: zwischen den
        # Zeichen beliebige Nicht-Alphanumerik zulassen.
        parts.append(r"[^a-z0-9]*".join(re.escape(ch) for ch in v))
    return "|".join(parts) or re.escape(filter_make.strip())


def _make_matches(filter_make: str, label: Optional[str]) -> bool:
    """True, wenn die gewählte Marke zum Fahrzeug-Label passt (schreibweise-tolerant)."""
    if not filter_make:
        return True
    a, b = _norm_make(filter_make), _norm_make(label)
    return bool(a) and (a == b or (len(a) >= 3 and (a in b or b in a)))


def _slugify(name: str, dealer_id: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or "haendler").lower()).strip("-")[:40]
    return f"{base or 'haendler'}-{dealer_id[:6]}"


async def _is_network_member(dealer_id: str, user_id: str) -> bool:
    return bool(await db.network_members.find_one(
        {"dealer_id": dealer_id, "buyer_user_id": user_id}, {"_id": 0, "dealer_id": 1}))


def _price_for(listing: dict, *, is_member: bool, is_trade: bool) -> Optional[float]:
    """Sichtbarer Preis je Betrachter: Netzwerk > B2B > öffentlich."""
    p = listing.get("prices") or {}
    if is_member and p.get("network"):
        return p["network"]
    if is_trade and p.get("b2b"):
        return p["b2b"]
    return p.get("public")


def _public_listing_view(l: dict, *, is_member: bool, is_trade: bool) -> dict:
    """Reduzierte Sicht für Fremde: keine internen Kosten/Margen/EK-Preise."""
    data = l.get("data") or {}
    photos = l.get("photos") or {}
    urls = []
    mode = photos.get("mode", "einkauf")
    if mode in ("einkauf", "beide"):
        urls += photos.get("einkauf_urls", [])
    if mode in ("neu", "beide"):
        urls += [f"/api/files/{k}" for k in photos.get("uploaded_keys", [])]
    return {
        "id": l["id"], "dealer_id": l["dealer_id"],
        "title": l.get("title"), "description": l.get("description"),
        "known_defects": l.get("known_defects") or [],
        "status": l.get("status"),
        "data": {k: data.get(k) for k in (
            "make_label", "model_label", "model_description",
            "first_registration", "mileage", "fuel_label", "gearbox_label",
            "power_ps", "power_kw", "color", "previous_owners", "features",
            "accident_free", "accident_damaged")},
        "photos": urls[:40],
        # Vom Haendler nachtraeglich hochgeladene Bilder (z.B. Schaeden) —
        # beim Kaeufer als 'Weitere Bilder vom Haendler' zum genauen Hinschauen.
        "dealer_photos": [f"/api/files/{k}"
                          for k in photos.get("uploaded_keys", [])][:40],
        "price": _price_for(l, is_member=is_member, is_trade=is_trade),
        "price_level": ("netzwerk" if is_member and (l.get("prices") or {}).get("network")
                        else "b2b" if is_trade and (l.get("prices") or {}).get("b2b")
                        else "oeffentlich"),
        "published_at": l.get("published_at"),
    }


# =========================================================
#              HÄNDLERPROFIL (Verkaufsseite)
# =========================================================
class ProfileIn(BaseModel):
    public: Optional[bool] = None
    description: Optional[str] = Field(default=None, max_length=5000)


@router.get("/dealer/marketplace-profile")
async def get_marketplace_profile(user=Depends(current_haendler)):
    dealer = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0})
    mp = dealer.get("marketplace") or {}
    if not mp.get("slug"):
        mp["slug"] = _slugify(dealer.get("company_name", ""), user["dealer_id"])
    published = await db.resale_listings.count_documents(
        {"dealer_id": user["dealer_id"], "status": "veroeffentlicht"})
    members = await db.network_members.count_documents({"dealer_id": user["dealer_id"]})
    return {"public": bool(mp.get("public")), "slug": mp["slug"],
            "description": mp.get("description", ""),
            "published_count": published, "network_members": members}


@router.put("/dealer/marketplace-profile")
async def update_marketplace_profile(body: ProfileIn, user=Depends(current_haendler)):
    dealer = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0})
    mp = dealer.get("marketplace") or {}
    mp.setdefault("slug", _slugify(dealer.get("company_name", ""), user["dealer_id"]))
    if body.public is not None:
        mp["public"] = bool(body.public)
    if body.description is not None:
        mp["description"] = body.description
    mp.setdefault("member_since", now_iso())
    await db.dealers.update_one({"id": user["dealer_id"]},
                                {"$set": {"marketplace": mp}})
    await log_activity(user["dealer_id"], user["id"],
                       "marktplatz.profil.aktualisiert",
                       meta={"public": mp.get("public", False)})
    return {"ok": True, "public": mp.get("public", False), "slug": mp["slug"]}


# =========================================================
#              EINLADUNGSLINKS (privates Netzwerk)
# =========================================================
class InviteIn(BaseModel):
    validity_hours: Literal[24, 168, 720] = 168     # 24h / 7 Tage / 30 Tage
    max_uses: Literal[1, 5, 10] = 1                 # Default: 1 Nutzung


@router.post("/dealer/invites")
async def create_invite(body: InviteIn, user=Depends(current_haendler)):
    token = secrets.token_urlsafe(18)
    expires = (datetime.now(timezone.utc)
               + timedelta(hours=body.validity_hours)).isoformat()
    doc = {
        "id": str(uuid.uuid4()), "dealer_id": user["dealer_id"],
        "token": token, "expires_at": expires,
        "max_uses": body.max_uses, "used_count": 0, "used_by": [],
        "created_at": now_iso(),
    }
    await db.dealer_invites.insert_one(doc)
    await log_activity(user["dealer_id"], user["id"], "einladung.erstellt",
                       ref=doc["id"], meta={"gueltig_h": body.validity_hours,
                                            "nutzungen": body.max_uses})
    return {"ok": True, "token": token, "expires_at": expires,
            "max_uses": body.max_uses,
            "link": f"/markt/registrieren?invite={token}"}


@router.get("/dealer/invites")
async def list_invites(user=Depends(current_haendler)):
    items = await db.dealer_invites.find(
        {"dealer_id": user["dealer_id"]}, {"_id": 0},
    ).sort("created_at", -1).to_list(50)
    now = now_iso()
    for i in items:
        i["valid"] = i["used_count"] < i["max_uses"] and i["expires_at"] > now
    return items


@router.delete("/dealer/invites/{invite_id}")
async def delete_invite(invite_id: str, user=Depends(current_haendler)):
    r = await db.dealer_invites.delete_one(
        {"id": invite_id, "dealer_id": user["dealer_id"]})
    if not r.deleted_count:
        raise HTTPException(404, "Einladung nicht gefunden")
    return {"ok": True}


@router.get("/dealer/network/members")
async def list_network_members(user=Depends(current_haendler)):
    """Mitglieder des privaten Netzwerks (PR-Review 09/2026: vorher gab es
    weder Liste noch Widerruf — ein beigetretener Kaeufer behielt den
    Zugang dauerhaft, das Loeschen der Einladung entfernte nur den Link)."""
    out = []
    async for m in db.network_members.find(
            {"dealer_id": user["dealer_id"]}, {"_id": 0}).sort("created_at", -1):
        b = await db.users.find_one(
            {"id": m["buyer_user_id"]},
            {"_id": 0, "company_name": 1, "contact_name": 1, "email": 1,
             "active": 1}) or {}
        out.append({"buyer_user_id": m["buyer_user_id"],
                    "company_name": b.get("company_name", ""),
                    "contact_name": b.get("contact_name", ""),
                    "email": b.get("email", ""),
                    "active": b.get("active", True),
                    "joined_at": m.get("created_at")})
    return out


@router.delete("/dealer/network/members/{buyer_user_id}")
async def remove_network_member(buyer_user_id: str,
                                user=Depends(current_haendler)):
    """Netzwerk-Zugang eines Zwischenhaendlers widerrufen: er sieht private
    Inserate und Netzwerkpreise dieses Haendlers ab sofort nicht mehr."""
    r = await db.network_members.delete_one(
        {"dealer_id": user["dealer_id"], "buyer_user_id": buyer_user_id})
    if not r.deleted_count:
        raise HTTPException(404, "Mitglied nicht gefunden")
    # Einmal-Einladungen des Kaeufers nicht wieder freigeben: der Widerruf
    # soll nicht ueber denselben alten Link umgehbar sein.
    await log_activity(user["dealer_id"], user["id"], "netzwerk.mitglied.entfernt",
                       ref=buyer_user_id)
    return {"ok": True}


async def _redeem_invite(token: str, buyer_user_id: str) -> Optional[str]:
    """Löst eine Einladung ein. Liefert dealer_id oder None."""
    inv = await db.dealer_invites.find_one({"token": token})
    if not inv:
        return None
    if buyer_user_id in (inv.get("used_by") or []):
        # Bereits eingeloest: nur dann noch Mitglied, wenn der Haendler den
        # Zugang nicht inzwischen widerrufen hat.
        noch = await db.network_members.find_one(
            {"dealer_id": inv["dealer_id"], "buyer_user_id": buyer_user_id},
            {"_id": 1})
        return inv["dealer_id"] if noch else None
    # ATOMAR pruefen UND verbrauchen: Gueltigkeit, Restnutzungen und die
    # Erhoehung passieren in EINEM Schritt. Vorher (lesen, dann erhoehen)
    # konnten zwei GLEICHZEITIGE Aufrufe denselben Einmal-Link beide
    # erfolgreich einloesen.
    verbraucht = await db.dealer_invites.find_one_and_update(
        {"id": inv["id"],
         "expires_at": {"$gt": now_iso()},
         "$expr": {"$lt": ["$used_count", "$max_uses"]}},
        {"$inc": {"used_count": 1}, "$push": {"used_by": buyer_user_id}})
    if not verbraucht:
        return None
    await db.network_members.update_one(
        {"dealer_id": inv["dealer_id"], "buyer_user_id": buyer_user_id},
        {"$setOnInsert": {"via_invite_id": inv["id"], "created_at": now_iso()}},
        upsert=True)
    return inv["dealer_id"]


# =========================================================
#              ZWISCHENHÄNDLER (b2b_buyer)
# =========================================================
class BuyerRegisterIn(BaseModel):
    company_name: str = Field(min_length=2, max_length=200)
    contact_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    phone: str = Field(default="", max_length=50)
    invite_token: Optional[str] = Field(default=None, max_length=100)

    @field_validator("password")
    @classmethod
    def _pw(cls, v):
        return _check_password_strength(v)


@router.post("/buyer/register")
async def buyer_register(body: BuyerRegisterIn, request: Request):
    ip = client_ip(request)
    if not await register_limiter.check(ip):
        raise HTTPException(429, "Zu viele Registrierungen von dieser IP – bitte später erneut versuchen.")
    existing = await db.users.find_one(
        {"email": {"$regex": f"^{re.escape(body.email)}$", "$options": "i"}})
    if existing:
        raise HTTPException(409, "E-Mail bereits registriert")
    user_id = str(uuid.uuid4())
    sid = new_session_id()
    await db.users.insert_one({
        "id": user_id, "email": body.email.strip().lower(),
        "password_hash": await hash_password_async(body.password),
        "role": "b2b_buyer", "active": True,
        "dealer_id": None,
        "company_name": body.company_name,
        "contact_name": body.contact_name,
        "phone": body.phone,
        "current_session_id": sid,
        "created_at": now_iso(),
    })
    joined = None
    if body.invite_token:
        joined = await _redeem_invite(body.invite_token, user_id)
    await log_activity(joined or "", user_id, "buyer.registriert",
                       meta={"email": body.email, "ip": ip,
                             "einladung": bool(joined)})
    return {"ok": True, "token": create_token(user_id, sid),
            "user": {"id": user_id, "email": body.email, "role": "b2b_buyer",
                     "company_name": body.company_name},
            "network_joined": bool(joined)}


class BuyerLoginIn(BaseModel):
    email: str
    password: str


@router.post("/buyer/login")
async def buyer_login(body: BuyerLoginIn, request: Request):
    """Login für Zwischenhändler (eigener Account, Rolle b2b_buyer)."""
    ip = client_ip(request)
    if not await login_limiter.check(ip):
        raise HTTPException(429, "Zu viele Anmeldeversuche – bitte 60 Sekunden warten.")
    email = body.email.lower().strip()
    u = await db.users.find_one({"email": {"$regex": f"^{re.escape(email)}$",
                                           "$options": "i"},
                                 "role": "b2b_buyer"})
    # Immer bcrypt rechnen (Dummy-Hash), um User-Enumeration per Timing zu
    # verhindern. Deaktivierte Accounts geben dieselbe 401 wie falsche Daten.
    pw_hash = u["password_hash"] if u else _DUMMY_HASH
    ok = await verify_password_async(body.password, pw_hash)
    if not u or not ok or not u.get("active", True):
        raise HTTPException(401, "E-Mail oder Passwort falsch")
    sid = new_session_id()
    await db.users.update_one({"id": u["id"]},
                              {"$set": {"current_session_id": sid}})
    return {"ok": True, "token": create_token(u["id"], sid),
            "user": _buyer_public(u)}


def _buyer_public(u: dict) -> dict:
    return {"id": u["id"], "email": u.get("email"),
            "role": "b2b_buyer",
            "company_name": u.get("company_name"),
            "contact_name": u.get("contact_name"),
            "phone": u.get("phone"),
            "access": _access_status(u)}


@router.get("/buyer/me")
async def buyer_me(user=Depends(current_buyer)):
    """Profil + Zugangsstatus. Auch für Händler/Admin nutzbar (Vorschau)."""
    networks = [m["dealer_id"] async for m in db.network_members.find(
        {"buyer_user_id": user["id"]}, {"_id": 0, "dealer_id": 1})]
    return {**_buyer_public(user), "network_dealer_ids": networks}


@router.get("/marktplatz/zugang")
async def marketplace_access_status(user=Depends(current_buyer)):
    return _access_status(user)


@router.post("/buyer/zugang-anfrage")
async def request_marketplace_access(user=Depends(current_buyer)):
    """Zwischenhändler fragt die Freischaltung des Zugangs an — landet beim
    Admin (manuelle Bezahlung/Freischaltung, wie bei Sucher-Abo & Paketen)."""
    if user.get("role") != "b2b_buyer":
        raise HTTPException(400, "Nur Zwischenhändler benötigen einen Zugang")
    if _access_status(user)["active"]:
        return {"ok": True, "hinweis": "Zugang ist bereits aktiv."}
    req_id = str(uuid.uuid4())
    await db.plan_requests.insert_one({
        "id": req_id, "type": "buyer_access",
        "buyer_user_id": user["id"],
        "company_name": user.get("company_name", ""),
        "contact_email": user.get("email", ""),
        "contact_phone": user.get("phone", ""),
        "wanted": f"Marktplatz-Zugang ({BUYER_ACCESS_PRICE:.2f} €/Monat)",
        "status": "offen", "created_at": now_iso(),
    })
    await log_activity("", user["id"], "marktplatz.zugang.anfrage", ref=req_id)
    return {"ok": True, "request_id": req_id,
            "hinweis": "Anfrage wurde an den Administrator übermittelt."}


@router.post("/invites/{token}/redeem")
async def redeem_invite(token: str, user=Depends(current_buyer)):
    dealer_id = await _redeem_invite(token, user["id"])
    if not dealer_id:
        raise HTTPException(400, "Einladung ist abgelaufen oder bereits verwendet")
    dealer = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "company_name": 1})
    return {"ok": True, "dealer": (dealer or {}).get("company_name", "")}


# =========================================================
#                    MARKTPLATZ (Browse)
# =========================================================
@router.get("/marktplatz/haendler")
async def browse_dealers(q: Optional[str] = None,
                         user=Depends(require_marketplace_access)):
    """Öffentliche Händlersuche + private Händler, in deren Netzwerk der
    Betrachter eingeladen wurde."""
    my_networks = [m["dealer_id"] async for m in db.network_members.find(
        {"buyer_user_id": user["id"]}, {"_id": 0, "dealer_id": 1})]
    query: Dict[str, Any] = {"$or": [
        {"marketplace.public": True},
        {"id": {"$in": my_networks}},
    ]}
    if q:
        query["company_name"] = {"$regex": re.escape(q.strip()), "$options": "i"}
    if user.get("dealer_id"):
        query["id"] = {"$ne": user["dealer_id"]}
    dealers = await db.dealers.find(
        query, {"_id": 0, "id": 1, "company_name": 1, "city": 1, "phone": 1,
                "logo_url": 1, "marketplace": 1}).to_list(1000)
    # Inseratszahlen ALLER Haendler in EINER Aggregation statt
    # count_documents je Haendler (kein N+1 mehr).
    counts = {row["_id"]: row["n"] async for row in db.resale_listings.aggregate([
        {"$match": {"dealer_id": {"$in": [dl["id"] for dl in dealers]},
                    "status": "veroeffentlicht"}},
        {"$group": {"_id": "$dealer_id", "n": {"$sum": 1}}},
    ])}
    out = []
    for dl in dealers:
        name = dl.get("company_name", "")
        mp = dl.get("marketplace") or {}
        published = counts.get(dl["id"], 0)
        out.append({
            "dealer_id": dl["id"],
            "slug": mp.get("slug") or _slugify(name, dl["id"]),
            "company_name": name,
            "city": dl.get("city", ""),
            "phone": dl.get("phone", ""),
            "logo_url": dl.get("logo_url", ""),
            "description": mp.get("description", ""),
            "public": bool(mp.get("public")),
            "network_member": dl["id"] in my_networks,
            "member_since": mp.get("member_since"),
            "vehicle_count": published,
        })
    out.sort(key=lambda x: -x["vehicle_count"])
    return out


@router.get("/marktplatz/listings")
async def browse_listings(
    user=Depends(require_marketplace_access),
    q: Optional[str] = None, make: Optional[str] = None,
    model: Optional[str] = None, fuel: Optional[str] = None,
    price_min: Optional[float] = None, price_max: Optional[float] = None,
    km_min: Optional[int] = None, km_max: Optional[int] = None,
    ps_min: Optional[int] = None, ps_max: Optional[int] = None,
    sort: Optional[str] = None, dealer: Optional[str] = None,
    nur_favoriten: Optional[int] = 0,
    page: int = 1, limit: int = 300,
):
    """Alle für den Betrachter sichtbaren veröffentlichten Fahrzeuge.

    Filter, Sortierung und Seitengroesse laufen komplett in MongoDB
    (Aggregation) — keine harte 300er-Grenze und kein Nachfiltern in
    Python mehr. sort: preis_auf | preis_ab | km_auf | km_ab
    (Default: neueste zuerst). nur_favoriten=1: nur gemerkte Fahrzeuge.
    page/limit: Seitennummer (ab 1) und Treffer pro Seite (max. 200)."""
    # Standard 300 = das bisherige Maximum, damit das Frontend OHNE
    # Pagination-Umbau weiterhin denselben Bestand sieht; page/limit stehen
    # fuer kuenftige Pagination bereit.
    limit = max(1, min(int(limit or 300), 300))
    page = max(1, int(page or 1))

    fav_ids = {f["listing_id"] async for f in db.buyer_favorites.find(
        {"buyer_user_id": user["id"]}, {"_id": 0, "listing_id": 1})}
    my_networks = [m["dealer_id"] async for m in db.network_members.find(
        {"buyer_user_id": user["id"]}, {"_id": 0, "dealer_id": 1})]
    public_dealer_ids = [d["id"] async for d in db.dealers.find(
        {"marketplace.public": True}, {"_id": 0, "id": 1})]
    visible_dealers = set(public_dealer_ids) | set(my_networks)
    visible_dealers.discard(user.get("dealer_id"))

    match: Dict[str, Any] = {
        "status": "veroeffentlicht",
        "dealer_id": {"$in": list(visible_dealers)},
        # Sichtbarkeit "private": nur für eingeladene Netzwerk-Mitglieder.
        "$and": [{"$or": [{"visibility": {"$ne": "private"}},
                          {"dealer_id": {"$in": my_networks}}]}],
    }
    if dealer:
        match["dealer_id"] = dealer if dealer in visible_dealers else "___none"
    if nur_favoriten:
        match["id"] = {"$in": list(fav_ids)}
    if make and len(_norm_make(make)) >= 2:
        match["data.make_label"] = {"$regex": _make_regex_variants(make),
                                    "$options": "i"}
    if model:
        rx = {"$regex": re.escape(model.strip()), "$options": "i"}
        match["$and"].append({"$or": [{"data.model_label": rx},
                                      {"data.model_description": rx}]})
    if fuel:
        match["data.fuel_label"] = {"$regex": re.escape(fuel.strip()),
                                    "$options": "i"}
    if q:
        rx = {"$regex": re.escape(q.strip()), "$options": "i"}
        match["$and"].append({"$or": [{"title": rx},
                                      {"data.model_description": rx}]})
    if km_min:
        match.setdefault("data.mileage", {})["$gte"] = km_min
    if km_max:
        # Fehlender km-Stand galt schon immer als "unter dem Maximum" —
        # ein $lte allein wuerde Inserate ohne km-Angabe verstecken.
        match["$and"].append({"$or": [{"data.mileage": {"$lte": km_max}},
                                      {"data.mileage": None}]})
    if ps_min:
        match.setdefault("data.power_ps", {})["$gte"] = ps_min
    if ps_max:
        match["$and"].append({"$or": [{"data.power_ps": {"$lte": ps_max}},
                                      {"data.power_ps": None}]})

    pipeline: List[Dict[str, Any]] = [{"$match": match}]

    braucht_preis = bool(price_min or price_max
                         or sort in ("preis_auf", "preis_ab"))
    if braucht_preis:
        # Effektiver Preis je Betrachter (Netzwerk > B2B > öffentlich) direkt
        # in der Datenbank — nur wenn Preisfilter/-sortierung aktiv ist
        # (sonst muesste Mongo ihn fuer JEDES Dokument berechnen).
        # $gt 0 statt $gt None: ein als 0 hinterlegter Platzhalter-Preis
        # zaehlt nicht — exakt wie _price_for es beim Anzeigen haelt.
        eff_price = {"$switch": {"branches": [
            {"case": {"$and": [{"$in": ["$dealer_id", my_networks]},
                               {"$gt": ["$prices.network", 0]}]},
             "then": "$prices.network"},
            {"case": {"$gt": ["$prices.b2b", 0]}, "then": "$prices.b2b"},
        ], "default": "$prices.public"}}
        pipeline.append({"$addFields": {"_eff_price": eff_price}})
        price_match: Dict[str, Any] = {}
        if price_min:
            price_match["$gte"] = price_min
        if price_max:
            price_match["$lte"] = price_max
        if price_match:
            # Ohne Preis ("auf Anfrage") zaehlte schon immer als 0 —
            # unter einem Maximum sichtbar, unter einem Minimum nicht.
            cond = {"_eff_price": price_match}
            if price_max and not price_min:
                cond = {"$or": [cond, {"_eff_price": None}]}
            pipeline.append({"$match": cond})

    # Aufsteigende Sortierungen: Inserate OHNE Wert ans Ende (Mongo wuerde
    # null zuerst einsortieren — genau falsch herum fuer "guenstigste
    # zuerst"). Sentinel statt null im Sortierschluessel.
    OHNE_WERT_ANS_ENDE = 9e15
    if sort == "preis_auf":
        pipeline.append({"$addFields": {"_sortkey": {
            "$ifNull": ["$_eff_price", OHNE_WERT_ANS_ENDE]}}})
        order = [("_sortkey", 1)]
    elif sort == "preis_ab":
        order = [("_eff_price", -1)]
    elif sort == "km_auf":
        pipeline.append({"$addFields": {"_sortkey": {
            "$ifNull": ["$data.mileage", OHNE_WERT_ANS_ENDE]}}})
        order = [("_sortkey", 1)]
    elif sort == "km_ab":
        order = [("data.mileage", -1)]
    else:
        order = [("published_at", -1)]
    pipeline.append({"$sort": dict(order)})
    pipeline.append({"$skip": (page - 1) * limit})
    pipeline.append({"$limit": limit})
    pipeline.append({"$project": {"_id": 0, "_eff_price": 0, "_sortkey": 0}})

    items = await db.resale_listings.aggregate(pipeline).to_list(limit)

    # Haendler-Infos in EINER Abfrage statt je Inserat (kein N+1).
    dealer_ids = list({l["dealer_id"] for l in items})
    dealer_docs = {d["id"]: d async for d in db.dealers.find(
        {"id": {"$in": dealer_ids}},
        {"_id": 0, "id": 1, "company_name": 1, "city": 1, "marketplace": 1,
         "phone": 1, "whatsapp_number": 1, "contact_person": 1,
         "logo_url": 1, "opening_hours": 1})}

    is_trade = True  # jeder hier ist registrierter Händler/Zwischenhändler
    out = []
    for l in items:
        member = l["dealer_id"] in my_networks
        view = _public_listing_view(l, is_member=member, is_trade=is_trade)
        view["is_favorit"] = l.get("id") in fav_ids
        dl = dealer_docs.get(l["dealer_id"], {})
        view["dealer"] = {"id": l["dealer_id"],
                          "company_name": dl.get("company_name", ""),
                          "city": dl.get("city", ""),
                          "slug": (dl.get("marketplace") or {}).get("slug", ""),
                          "phone": dl.get("phone") or dl.get("whatsapp_number") or "",
                          "contact_person": dl.get("contact_person") or "",
                          "logo_url": dl.get("logo_url") or "",
                          "opening_hours": dl.get("opening_hours") or ""}
        out.append(view)
    return out


async def _inserat_sichtbar_fuer(user: dict, listing: dict) -> bool:
    """Darf DIESER Betrachter das Inserat sehen? (oeffentlicher Haendler
    oder eigenes Netzwerk; visibility=private nur im Netzwerk). Vorher
    liessen sich private Inserate bei bekannter ID favorisieren und
    anfragen (PR-Review 09/2026)."""
    dealer_id = listing.get("dealer_id")
    if dealer_id == user.get("dealer_id"):
        return False                      # eigene Inserate: kein Selbst-Interesse
    im_netzwerk = await db.network_members.find_one(
        {"dealer_id": dealer_id, "buyer_user_id": user["id"]}, {"_id": 1})
    if (listing.get("visibility") or "") == "private" and not im_netzwerk:
        return False
    if im_netzwerk:
        return True
    d = await db.dealers.find_one({"id": dealer_id},
                                  {"_id": 0, "marketplace.public": 1})
    return bool(((d or {}).get("marketplace") or {}).get("public"))


# ---------- Favoriten (Merkliste) ----------
@router.post("/marktplatz/favoriten/{listing_id}")
async def toggle_favorit(listing_id: str, user=Depends(current_buyer)):
    """Fahrzeug merken / Merken aufheben (Toggle). Bewusst ohne Zugangs-Abo-
    Pflicht beim ENTFERNEN; zum Setzen muss das Inserat sichtbar sein."""
    existing = await db.buyer_favorites.find_one(
        {"buyer_user_id": user["id"], "listing_id": listing_id})
    if existing:
        await db.buyer_favorites.delete_one({"_id": existing["_id"]})
        return {"favorit": False}
    l = await db.resale_listings.find_one(
        {"id": listing_id, "status": "veroeffentlicht"},
        {"_id": 0, "dealer_id": 1, "visibility": 1})
    if not l or not await _inserat_sichtbar_fuer(user, l):
        raise HTTPException(404, "Inserat nicht gefunden")
    await db.buyer_favorites.insert_one({
        "id": str(uuid.uuid4()),
        "buyer_user_id": user["id"],
        "listing_id": listing_id,
        "dealer_id": l.get("dealer_id"),
        "created_at": now_iso(),
    })
    return {"favorit": True}


@router.get("/marktplatz/favoriten")
async def list_favoriten(user=Depends(current_buyer)):
    """IDs der gemerkten Fahrzeuge (fuers Herz-Icon)."""
    ids = [f["listing_id"] async for f in db.buyer_favorites.find(
        {"buyer_user_id": user["id"]}, {"_id": 0, "listing_id": 1})]
    return {"listing_ids": ids}


@router.get("/marktplatz/haendler/{slug}")
async def dealer_page(slug: str, user=Depends(require_marketplace_access)):
    # Erreichbar per Kurzname ODER Dealer-ID (nicht jeder Haendler hat
    # einen Kurznamen gesetzt — die Karte im Markt verlinkt per ID).
    dl = await db.dealers.find_one(
        {"$or": [{"marketplace.slug": slug}, {"id": slug}]}, {"_id": 0})
    if not dl:
        raise HTTPException(404, "Händler nicht gefunden")
    mp = dl.get("marketplace") or {}
    member = await _is_network_member(dl["id"], user["id"])
    if not mp.get("public") and not member and dl["id"] != user.get("dealer_id"):
        raise HTTPException(403, "Dieses Händlerprofil ist privat (nur auf Einladung)")
    listings = await db.resale_listings.find(
        {"dealer_id": dl["id"], "status": "veroeffentlicht"}, {"_id": 0},
    ).sort("published_at", -1).to_list(200)
    # Private Inserate nur für Netzwerk-Mitglieder (und den Händler selbst).
    if not member and dl["id"] != user.get("dealer_id"):
        listings = [l for l in listings
                    if (l.get("visibility") or "public") != "private"]
    return {
        "profile": {
            "id": dl.get("id", ""),
            "company_name": dl.get("company_name", ""),
            "city": dl.get("city", ""),
            "address": dl.get("address", ""),
            "phone": dl.get("phone", "") or dl.get("whatsapp_number", ""),
            "contact_person": dl.get("contact_person", ""),
            "email": dl.get("email", ""), "logo_url": dl.get("logo_url", ""),
            "opening_hours": dl.get("opening_hours", ""),
            "description": mp.get("description", ""),
            "member_since": mp.get("member_since"),
            "network_member": member,
            "vehicle_count": len(listings),
        },
        "listings": [_public_listing_view(l, is_member=member, is_trade=True)
                     for l in listings],
    }


# =========================================================
#              INTERESSENTEN / ANGEBOTE
# =========================================================
class InterestIn(BaseModel):
    offer: Optional[float] = Field(default=None, ge=0)
    message: str = Field(default="", max_length=2000)


class InterestAnswerIn(BaseModel):
    action: Literal["akzeptieren", "ablehnen", "gegenangebot"]
    counter_offer: Optional[float] = Field(default=None, ge=0)
    message: str = Field(default="", max_length=2000)


@router.post("/marktplatz/listings/{listing_id}/interesse")
async def send_interest(listing_id: str, body: InterestIn,
                        user=Depends(require_marketplace_access)):
    l = await db.resale_listings.find_one(
        {"id": listing_id, "status": "veroeffentlicht"}, {"_id": 0})
    if not l:
        raise HTTPException(404, "Inserat nicht gefunden oder nicht verfügbar")
    if l["dealer_id"] == user.get("dealer_id"):
        raise HTTPException(400, "Eigene Inserate können nicht angefragt werden")
    if not await _inserat_sichtbar_fuer(user, l):
        raise HTTPException(404, "Inserat nicht gefunden oder nicht verfügbar")
    doc = {
        "id": str(uuid.uuid4()),
        "listing_id": listing_id,
        "dealer_id": l["dealer_id"],
        "listing_title": l.get("title", ""),
        "buyer_user_id": user["id"],
        "buyer_name": user.get("company_name") or user.get("contact_name")
                      or user.get("email", ""),
        "buyer_email": user.get("email", ""),
        "offer": body.offer,
        "message": body.message,
        "status": "offen",
        "counter_offer": None,
        "history": [{"von": "kaeufer", "aktion": "interesse",
                     "angebot": body.offer, "nachricht": body.message,
                     "zeit": now_iso()}],
        "created_at": now_iso(), "updated_at": now_iso(),
    }
    await db.listing_interest.insert_one(doc)
    await log_activity(l["dealer_id"], user["id"], "interesse.gesendet",
                       ref=listing_id, meta={"angebot": body.offer})
    return {"ok": True, "interest_id": doc["id"]}


@router.get("/dealer/interessen")  # noqa: E302
async def dealer_list_interests(status: Optional[str] = None,
                                listing_id: Optional[str] = None,
                                user=Depends(current_haendler)):
    """Kaufanfragen der Firma — optional nach Status und/oder Inserat
    gefiltert (listing_id: Review 09/2026, fuer die Anzeige je Inserat)."""
    q: Dict[str, Any] = {"dealer_id": user["dealer_id"]}
    if status:
        q["status"] = status
    if listing_id:
        q["listing_id"] = listing_id
    return await db.listing_interest.find(q, {"_id": 0}) \
        .sort("created_at", -1).to_list(200)


@router.get("/buyer/interessen")
async def buyer_interests(user=Depends(current_buyer)):
    return await db.listing_interest.find(
        {"buyer_user_id": user["id"]}, {"_id": 0},
    ).sort("created_at", -1).to_list(200)


class BuyerInterestAnswerIn(BaseModel):
    action: Literal["annehmen", "ablehnen"]
    message: str = Field(default="", max_length=2000)


@router.post("/interessen/{interest_id}/kaeufer-antwort")
async def buyer_answer_interest(interest_id: str, body: BuyerInterestAnswerIn,
                                user=Depends(current_buyer)):
    """Kaeufer reagiert auf ein GEGENANGEBOT des Haendlers (Review 09/2026:
    der Kaeufer sah Gegenangebote, konnte aber nicht antworten).
    annehmen: Inserat wird atomar fuer den Kaeufer reserviert, Status
    'akzeptiert'. ablehnen: Status 'abgelehnt'. Beides nur einmal —
    der Statuswechsel selbst ist atomar gegen parallele Antworten."""
    it = await db.listing_interest.find_one(
        {"id": interest_id, "buyer_user_id": user["id"]}, {"_id": 0})
    if not it:
        raise HTTPException(404, "Anfrage nicht gefunden")
    if it.get("status") != "gegenangebot":
        raise HTTPException(400, "Nur ein Gegenangebot des Haendlers kann "
                                 "angenommen oder abgelehnt werden")
    reserviert = False
    if body.action == "annehmen":
        res = await db.resale_listings.find_one_and_update(
            {"id": it["listing_id"], "status": "veroeffentlicht"},
            {"$set": {"status": "reserviert", "reserved_for": user["id"],
                      "updated_at": now_iso()}})
        if res is None:
            l = await db.resale_listings.find_one(
                {"id": it["listing_id"]}, {"_id": 0, "status": 1})
            raise HTTPException(409, "Fahrzeug ist nicht mehr verfuegbar "
                                     f"(Status '{(l or {}).get('status', 'unbekannt')}').")
        reserviert = True
    neuer_status = "akzeptiert" if body.action == "annehmen" else "abgelehnt"
    upd = await db.listing_interest.update_one(
        {"id": interest_id, "buyer_user_id": user["id"], "status": "gegenangebot",
         # Preis festnageln: sendet der Haendler PARALLEL ein neues
         # Gegenangebot, darf die Annahme des ALTEN Betrags nicht auf den
         # neuen durchschlagen (Review-Workflow 09/2026).
         "counter_offer": it.get("counter_offer")},
        {"$set": {"status": neuer_status, "updated_at": now_iso()},
         "$push": {"history": {"von": "kaeufer", "aktion": body.action,
                               "angebot": it.get("counter_offer") if body.action == "annehmen" else None,
                               "nachricht": body.message, "zeit": now_iso()}}})
    if upd.modified_count == 0:
        # Paralleler Statuswechsel (z.B. Haendler hat gerade geantwortet):
        # Reservierung zurueckgeben und ehrlich ablehnen.
        if reserviert:
            await db.resale_listings.update_one(
                {"id": it["listing_id"], "status": "reserviert",
                 "reserved_for": user["id"]},
                {"$set": {"status": "veroeffentlicht", "updated_at": now_iso()},
                 "$unset": {"reserved_for": ""}})
        raise HTTPException(409, "Die Anfrage wurde gerade anderweitig "
                                 "beantwortet — bitte neu laden.")
    await log_activity("", user["id"], f"interesse.kaeufer.{body.action}",
                       ref=interest_id,
                       meta={"listing_id": it.get("listing_id"),
                             "betrag": it.get("counter_offer")})
    return {"ok": True, "status": neuer_status}


@router.post("/interessen/{interest_id}/antwort")
async def answer_interest(interest_id: str, body: InterestAnswerIn,
                          user=Depends(current_haendler)):
    it = await db.listing_interest.find_one(
        {"id": interest_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not it:
        raise HTTPException(404, "Anfrage nicht gefunden")
    if it["status"] in ("akzeptiert", "abgelehnt"):
        raise HTTPException(400, "Anfrage ist bereits abgeschlossen")
    status_map = {"akzeptieren": "akzeptiert", "ablehnen": "abgelehnt",
                  "gegenangebot": "gegenangebot"}
    new_status = status_map[body.action]
    update: Dict[str, Any] = {"status": new_status, "updated_at": now_iso()}
    if body.action == "gegenangebot":
        if body.counter_offer is None:
            raise HTTPException(400, "Gegenangebot benötigt einen Betrag")
        update["counter_offer"] = round(float(body.counter_offer), 2)
    if body.action == "akzeptieren":
        # ZUERST atomar reservieren (Review 09/2026): vorher wurde die Anfrage
        # als "akzeptiert" gespeichert, selbst wenn das Fahrzeug laengst fuer
        # einen anderen Interessenten reserviert oder verkauft war —
        # zwei Kaeufer hielten sich fuer den Gewinner.
        res = await db.resale_listings.find_one_and_update(
            {"id": it["listing_id"], "dealer_id": user["dealer_id"],
             "status": "veroeffentlicht"},
            {"$set": {"status": "reserviert",
                      "reserved_for": it["buyer_user_id"],
                      "updated_at": now_iso()}})
        if res is None:
            l = await db.resale_listings.find_one(
                {"id": it["listing_id"]}, {"_id": 0, "status": 1, "reserved_for": 1})
            st = (l or {}).get("status", "unbekannt")
            raise HTTPException(409, f"Fahrzeug ist nicht mehr verfuegbar (Status "
                                     f"'{st}') — bereits reserviert oder verkauft.")
    # Status-Guard (Review-Workflow 09/2026): der Schreibvorgang gilt nur,
    # wenn die Anfrage noch im GELESENEN Zustand ist — sonst hat der Kaeufer
    # parallel geantwortet (z.B. Gegenangebot angenommen) und ein
    # ungefilterter Write wuerde dessen "akzeptiert" ueberschreiben,
    # waehrend das Inserat reserviert bliebe (Lost Update).
    upd = await db.listing_interest.update_one(
        {"id": interest_id, "dealer_id": user["dealer_id"],
         "status": it["status"]},
        {"$set": update,
         "$push": {"history": {"von": "haendler", "aktion": body.action,
                               "angebot": body.counter_offer,
                               "nachricht": body.message, "zeit": now_iso()}}})
    if upd.modified_count == 0:
        if body.action == "akzeptieren":
            # Die eben gezogene Reservierung wieder freigeben.
            await db.resale_listings.update_one(
                {"id": it["listing_id"], "status": "reserviert",
                 "reserved_for": it["buyer_user_id"]},
                {"$set": {"status": "veroeffentlicht", "updated_at": now_iso()},
                 "$unset": {"reserved_for": ""}})
        raise HTTPException(409, "Die Anfrage wurde gerade anderweitig "
                                 "beantwortet — bitte neu laden.")
    await log_activity(user["dealer_id"], user["id"], f"interesse.{new_status}",
                       ref=interest_id)
    return {"ok": True, "status": new_status}
