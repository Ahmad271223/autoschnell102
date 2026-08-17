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
from typing import Any, Dict, Literal, Optional

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
BUYER_ACCESS_PRICE = 9.99          # € pro Monat
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
    # Strikte Rollentrennung: Kaeufer + Haendler duerfen den Marktplatz
    # ansehen; Admin-Konten verwalten nur (Freischaltungen laufen ueber
    # /admin/*-Endpunkte).
    if user.get("role") not in ("b2b_buyer", "dealer"):
        raise HTTPException(403, "Nur für registrierte Händler/Zwischenhändler")
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


async def _redeem_invite(token: str, buyer_user_id: str) -> Optional[str]:
    """Löst eine Einladung ein. Liefert dealer_id oder None."""
    inv = await db.dealer_invites.find_one({"token": token})
    if not inv:
        return None
    if inv["expires_at"] <= now_iso() or inv["used_count"] >= inv["max_uses"]:
        return None
    if buyer_user_id in (inv.get("used_by") or []):
        return inv["dealer_id"]  # bereits Mitglied
    await db.dealer_invites.update_one(
        {"id": inv["id"]},
        {"$inc": {"used_count": 1}, "$push": {"used_by": buyer_user_id}})
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
    if not register_limiter.check(ip):
        raise HTTPException(429, "Zu viele Registrierungen von dieser IP – bitte später erneut versuchen.")
    existing = await db.users.find_one(
        {"email": {"$regex": f"^{re.escape(body.email)}$", "$options": "i"}})
    if existing:
        raise HTTPException(409, "E-Mail bereits registriert")
    user_id = str(uuid.uuid4())
    sid = new_session_id()
    await db.users.insert_one({
        "id": user_id, "email": body.email,
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
    if not login_limiter.check(ip):
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
    out = []
    async for dl in db.dealers.find(query, {"_id": 0}):
        if dl["id"] == user.get("dealer_id"):
            continue  # eigener Betrieb
        name = dl.get("company_name", "")
        if q and q.lower() not in name.lower():
            continue
        mp = dl.get("marketplace") or {}
        published = await db.resale_listings.count_documents(
            {"dealer_id": dl["id"], "status": "veroeffentlicht"})
        if not mp.get("public") and dl["id"] not in my_networks:
            continue
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
):
    """Alle für den Betrachter sichtbaren veröffentlichten Fahrzeuge.
    sort: preis_auf | preis_ab | km_auf | km_ab (Default: neueste zuerst).
    nur_favoriten=1: nur gemerkte Fahrzeuge."""
    fav_ids = {f["listing_id"] async for f in db.buyer_favorites.find(
        {"buyer_user_id": user["id"]}, {"_id": 0, "listing_id": 1})}
    my_networks = [m["dealer_id"] async for m in db.network_members.find(
        {"buyer_user_id": user["id"]}, {"_id": 0, "dealer_id": 1})]
    public_dealer_ids = [d["id"] async for d in db.dealers.find(
        {"marketplace.public": True}, {"_id": 0, "id": 1})]
    visible_dealers = set(public_dealer_ids) | set(my_networks)
    visible_dealers.discard(user.get("dealer_id"))
    query: Dict[str, Any] = {
        "status": "veroeffentlicht",
        "dealer_id": {"$in": list(visible_dealers)},
    }
    if dealer:
        query["dealer_id"] = dealer if dealer in visible_dealers else "___none"
    items = await db.resale_listings.find(query, {"_id": 0}) \
        .sort("published_at", -1).to_list(300)
    is_trade = True  # jeder hier ist registrierter Händler/Zwischenhändler
    out = []
    dealer_names: Dict[str, str] = {}
    for l in items:
        data = l.get("data") or {}
        km = data.get("mileage")
        ps = data.get("power_ps")
        if make and not _make_matches(make, data.get("make_label")):
            continue
        if model and model.lower() not in (
                f"{data.get('model_label','')} {data.get('model_description','')}".lower()):
            continue
        if fuel and fuel.lower() not in str(data.get("fuel_label", "")).lower():
            continue
        if q:
            hay = f"{l.get('title','')} {data.get('model_description','')}".lower()
            if q.lower() not in hay:
                continue
        if km_min and (km or 0) < km_min:
            continue
        if km_max and (km or 0) > km_max:
            continue
        if ps_min and (ps or 0) < ps_min:
            continue
        if ps_max and (ps or 0) > ps_max:
            continue
        member = l["dealer_id"] in my_networks
        # Sichtbarkeit "private": nur für eingeladene Netzwerk-Mitglieder.
        if (l.get("visibility") or "public") == "private" and not member:
            continue
        if nur_favoriten and l.get("id") not in fav_ids:
            continue
        view = _public_listing_view(l, is_member=member, is_trade=is_trade)
        view["is_favorit"] = l.get("id") in fav_ids
        price = view["price"] or 0
        if price_min and price < price_min:
            continue
        if price_max and price > price_max:
            continue
        if l["dealer_id"] not in dealer_names:
            dl = await db.dealers.find_one(
                {"id": l["dealer_id"]},
                {"_id": 0, "company_name": 1, "city": 1, "marketplace": 1,
                 "phone": 1, "whatsapp_number": 1, "contact_person": 1,
                 "logo_url": 1, "opening_hours": 1})
            dealer_names[l["dealer_id"]] = dl or {}
        dl = dealer_names[l["dealer_id"]]
        view["dealer"] = {"id": l["dealer_id"],
                          "company_name": dl.get("company_name", ""),
                          "city": dl.get("city", ""),
                          "slug": (dl.get("marketplace") or {}).get("slug", ""),
                          "phone": dl.get("phone") or dl.get("whatsapp_number") or "",
                          "contact_person": dl.get("contact_person") or "",
                          "logo_url": dl.get("logo_url") or "",
                          "opening_hours": dl.get("opening_hours") or ""}
        out.append(view)

    # Sortierung (None-Werte immer ans Ende)
    BIG = float("inf")
    if sort == "preis_auf":
        out.sort(key=lambda v: v["price"] if v.get("price") is not None else BIG)
    elif sort == "preis_ab":
        out.sort(key=lambda v: v["price"] if v.get("price") is not None else -1, reverse=True)
    elif sort == "km_auf":
        out.sort(key=lambda v: (v["data"].get("mileage") if v["data"].get("mileage") is not None else BIG))
    elif sort == "km_ab":
        out.sort(key=lambda v: (v["data"].get("mileage") or -1), reverse=True)
    return out


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
        {"id": listing_id, "status": "veroeffentlicht"}, {"_id": 0, "dealer_id": 1})
    if not l:
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


@router.get("/dealer/interessen")
async def dealer_interests(user=Depends(current_haendler),
                           status: Optional[str] = None):
    query: Dict[str, Any] = {"dealer_id": user["dealer_id"]}
    if status:
        query["status"] = status
    return await db.listing_interest.find(query, {"_id": 0}) \
        .sort("created_at", -1).to_list(200)


@router.get("/buyer/interessen")
async def buyer_interests(user=Depends(current_buyer)):
    return await db.listing_interest.find(
        {"buyer_user_id": user["id"]}, {"_id": 0},
    ).sort("created_at", -1).to_list(200)


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
    await db.listing_interest.update_one(
        {"id": interest_id},
        {"$set": update,
         "$push": {"history": {"von": "haendler", "aktion": body.action,
                               "angebot": body.counter_offer,
                               "nachricht": body.message, "zeit": now_iso()}}})
    if body.action == "akzeptieren":
        # Fahrzeug für den Interessenten reservieren.
        await db.resale_listings.update_one(
            {"id": it["listing_id"], "status": "veroeffentlicht"},
            {"$set": {"status": "reserviert",
                      "reserved_for": it["buyer_user_id"],
                      "updated_at": now_iso()}})
    await log_activity(user["dealer_id"], user["id"], f"interesse.{new_status}",
                       ref=interest_id)
    return {"ok": True, "status": new_status}
