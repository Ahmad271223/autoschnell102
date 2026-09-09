"""Phase 3: B2B-Marktplatz.

- Händlerprofile (öffentlich / privat) mit eigener Verkaufsseite
- Einmalige Einladungslinks (Gültigkeit + Nutzungslimit, Default 1 Nutzung)
- Eigene Rolle `b2b_buyer` (Zwischenhändler) — bewusst KEINE Händlerrolle
- Interessenten-Verwaltung: Interesse/Angebot → akzeptieren / ablehnen /
  Gegenangebot
- Preisstufen: öffentlich < B2B (registrierte Zwischenhändler) <
  privates Netzwerk (per Einladung)
"""
import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import (APIRouter, Body, Depends, HTTPException, Query, Request,
                     Response)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field, field_validator
from pymongo.errors import DuplicateKeyError

from auth import (hash_password_async, new_session_id, create_token,
                  verify_password_async, _DUMMY_HASH)
from deps import (_ablauf_parsen, current_user, db, email_vergeben,
                  firma_gesperrt, gesperrte_firmen_ids, log_activity, now_iso)
from rate_limiter import client_ip, register_limiter, login_limiter
from routes.auth import _check_password_strength
from routes.bestand import current_haendler
from dateien import signierte_datei_url   # signierte Foto-Links (Audit 09/2026)

log = logging.getLogger("autohandel")

router = APIRouter()


# ---------- Zugang zum Marktplatz ----------
# Beschluss 09/2026: Der Marktplatz ist KOSTENLOS und oeffentlich.
#  * Zwischenhaendler brauchen kein Zugangs-Abo mehr.
#  * Oeffentlich veroeffentlichte Fahrzeuge sieht JEDER, auch ohne
#    Anmeldung. Merken, Anfragen und Netzwerk-Inserate bleiben
#    angemeldeten Zwischenhaendlern vorbehalten.
# Die Abrechnung bleibt im Code erhalten und laesst sich mit
# MARKTPLATZ_KOSTENLOS=false wieder einschalten.
MARKTPLATZ_KOSTENLOS = os.environ.get(
    "MARKTPLATZ_KOSTENLOS", "true").strip().lower() not in ("0", "false", "no")

BUYER_ACCESS_PRICE = 20.00         # € pro Monat, nur wenn nicht kostenlos
BUYER_ACCESS_DAYS = 30


GESPERRT_MELDUNG = ("Dein Marktplatz-Zugang wurde vom Betreiber gesperrt — "
                    "bitte den Betreiber kontaktieren.")


def _access_status(user: dict) -> dict:
    """Zugangsstatus eines Zwischenhändlers. Händler haben immer Zugang.

    Zwei getrennte Zustaende (Runde 13: C6): "kostenlos" sagt nur, dass
    kein Geld noetig ist; "gesperrt" ist eine Betreiber-Entscheidung und
    gewinnt immer."""
    acc = user.get("marketplace_access") or {}
    # Runde 13: C6 — vorher gewann der Kostenlos-Schalter vor der Betreiber-
    # Sperre (Sperren war wirkungslos, Oberflaeche zeigte weiter "aktiv");
    # jetzt gewinnt die Sperre immer, "kostenlos" betrifft nur das Geld.
    # Altbestand: active=False ohne "gesperrt" stammt ausschliesslich von
    # der Admin-Sperre und gilt weiter als Sperre.
    gesperrt = bool(acc.get("gesperrt")) or acc.get("active") is False
    if gesperrt:
        return {"active": False, "plan": None, "expires_at": None,
                "price": 0.0 if MARKTPLATZ_KOSTENLOS else BUYER_ACCESS_PRICE,
                "kostenlos": MARKTPLATZ_KOSTENLOS, "gesperrt": True,
                "gesperrt_am": acc.get("gesperrt_am")}
    if MARKTPLATZ_KOSTENLOS:
        return {"active": True, "plan": "kostenlos", "expires_at": None,
                "price": 0.0, "kostenlos": True, "gesperrt": False}
    # Runde 13: die Rolle admin existiert nicht mehr (Runde 12) — nur noch
    # Haendler haben internen Zugang.
    if user.get("role") == "dealer":
        return {"active": True, "plan": "intern", "expires_at": None,
                "price": BUYER_ACCESS_PRICE, "gesperrt": False}
    active = bool(acc.get("active"))
    exp = acc.get("expires_at")
    # Runde 13: A2 — vorher blieb ein Zugang bei fehlendem oder unlesbarem
    # Ablaufdatum dauerhaft aktiv (except: pass; fehlendes Datum wurde gar
    # nicht geprueft). Jetzt fail-closed wie sub_status_from_doc: der
    # Marktplatz-Zugang ist immer befristet (30 Tage), also kein oder
    # unlesbares Ablaufdatum -> inaktiv, protokolliert; nur ein lesbares
    # Datum in der Zukunft gewaehrt Zugang.
    if active:
        ea = _ablauf_parsen(exp)
        if ea is None:
            log.error("Marktplatz-Zugang %s: fehlendes/unlesbares Ablaufdatum "
                      "%r -> inaktiv", user.get("id"), exp)
            active = False
        elif ea < datetime.now(timezone.utc):
            active = False
    return {"active": active, "plan": acc.get("plan"),
            "expires_at": exp, "price": BUYER_ACCESS_PRICE, "gesperrt": False}


def _zugang_erzwingen(user: dict) -> None:
    """Runde 13: C2/C6 — gemeinsame Zugangspruefung fuer Dependencies und
    Inline-Aufrufe (der 402-Text stand vorher dreimal im Code):
    Betreiber-Sperre -> 403 (gewinnt immer, auch im Kostenlos-Modus),
    fehlendes/abgelaufenes Abo -> 402 (nur im Bezahlmodus erreichbar)."""
    status = _access_status(user)
    if status.get("gesperrt"):
        raise HTTPException(403, GESPERRT_MELDUNG)
    if not status["active"]:
        raise HTTPException(
            402, "Kein aktiver Marktplatz-Zugang – bitte Zugang freischalten "
                 f"({BUYER_ACCESS_PRICE:.2f} € / Monat).")


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


async def buyer_nicht_gesperrt(user=Depends(current_buyer)):
    """Runde 13: C6 — Zwischenhaendler, dessen Marktplatz-Zugang der
    Betreiber NICHT gesperrt hat (Merken, Anfragen, Netzwerk). Ob der
    Zugang kostenlos oder bezahlt ist, spielt hier keine Rolle; vorher war
    die Sperre im Kostenlos-Modus wirkungslos."""
    if _access_status(user).get("gesperrt"):
        raise HTTPException(403, GESPERRT_MELDUNG)
    return user


async def require_marketplace_access(user=Depends(buyer_nicht_gesperrt)):
    """Wie current_buyer, aber Zwischenhändler brauchen ein aktives Zugangs-Abo,
    um Fahrzeuge sehen zu können (Händler ausgenommen). Gesperrt -> 403
    (buyer_nicht_gesperrt), ohne Abo -> 402.

    Ist der Marktplatz kostenlos, faellt die Abo-Pruefung weg."""
    _zugang_erzwingen(user)
    return user


async def marktplatz_besucher(
        creds: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False))):
    """Angemeldeter Zwischenhaendler ODER oeffentlicher Besucher (None).

    Ohne Anmeldung sind nur oeffentlich veroeffentlichte Fahrzeuge
    oeffentlicher Haendler sichtbar. Ein ungueltiges oder abgelaufenes
    Token gilt wie "nicht angemeldet" — der Marktplatz soll deswegen
    nicht unbenutzbar werden.

    Ist der Marktplatz NICHT kostenlos, gilt weiterhin: nur angemeldete
    Zwischenhaendler mit aktivem Zugang."""
    nutzer = None
    if creds and creds.credentials:
        try:
            nutzer = await current_user(creds)
        except HTTPException:
            nutzer = None
    if nutzer is not None and nutzer.get("role") != "b2b_buyer":
        raise HTTPException(403, "Nur für registrierte Zwischenhändler")
    # Runde 13: C6 — die Betreiber-Sperre gilt auch im Kostenlos-Modus
    # (vorher wurde dieser Block dort komplett uebersprungen). Die
    # oeffentliche Liste OHNE Token bleibt oeffentlich — gewollt.
    if nutzer is not None and _access_status(nutzer).get("gesperrt"):
        raise HTTPException(403, GESPERRT_MELDUNG)
    if not MARKTPLATZ_KOSTENLOS:
        if nutzer is None:
            raise HTTPException(401, "Nicht authentifiziert")
        _zugang_erzwingen(nutzer)
    return nutzer


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
        # 10.09.2026: Portal-Fotos ueber den eigenen Bild-Proxy (klein,
        # zuverlaessig, kein Fremdhost beim Kaeufer); 3 Tage gueltig.
        from bild_proxy import thumbs as _thumbs
        urls += _thumbs(photos.get("einkauf_urls", []), ttl=3 * 24 * 3600)
    if mode in ("neu", "beide"):
        urls += [signierte_datei_url(k) for k in photos.get("uploaded_keys", [])]
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
        "dealer_photos": [signierte_datei_url(k)
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
    # Nachpruefung Runde 14 (Nr. 119): vorher wurde das ganze marketplace-
    # Objekt gelesen, lokal geaendert und komplett zurueckgeschrieben. Das
    # Frontend schickt public und description in ZWEI getrennten PUTs —
    # der spaetere Schreiber ueberschrieb den frueheren (Lost Update, in
    # 29 von 30 Laeufen blieb public=False). Jetzt: nur die tatsaechlich
    # gesendeten Felder per Dotted-Path setzen; slug/member_since nur
    # anlegen, wenn sie noch fehlen.
    did = user["dealer_id"]
    dealer = await db.dealers.find_one(
        {"id": did}, {"_id": 0, "company_name": 1, "marketplace.slug": 1})
    slug = (((dealer or {}).get("marketplace") or {}).get("slug")
            or _slugify((dealer or {}).get("company_name", ""), did))
    await db.dealers.update_one(
        {"id": did, "marketplace.slug": {"$in": [None, ""]}},
        {"$set": {"marketplace.slug": slug,
                  "marketplace.member_since": now_iso()}})
    sets: Dict[str, Any] = {}
    if body.public is not None:
        sets["marketplace.public"] = bool(body.public)
    if body.description is not None:
        sets["marketplace.description"] = body.description
    if sets:
        await db.dealers.update_one({"id": did}, {"$set": sets})
    mp = ((await db.dealers.find_one({"id": did}, {"_id": 0, "marketplace": 1}))
          or {}).get("marketplace") or {}
    await log_activity(did, user["id"],
                       "marktplatz.profil.aktualisiert",
                       meta={"public": mp.get("public", False)})
    return {"ok": True, "public": mp.get("public", False),
            "slug": mp.get("slug") or slug}


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
    # Nachpruefung Runde 14 (Nr. 87): vorher pauschal die neuesten 50 —
    # ab der 51. Einladung in 30 Tagen fiel eine aeltere, noch einloesbare
    # Einladung aus der Oberflaeche, blieb aber gueltig und war ohne ihre
    # id nicht mehr loeschbar. Jetzt: ALLE noch gueltigen (Deckel 500 nur
    # als Notbremse), dazu die neuesten 50 abgelaufenen/verbrauchten.
    # Antwort bleibt eine Liste (Einstellungen.jsx erwartet invites.map).
    now = now_iso()
    basis: Dict[str, Any] = {"dealer_id": user["dealer_id"]}
    gueltig = {"expires_at": {"$gt": now},
               "$expr": {"$lt": ["$used_count", "$max_uses"]}}
    ungueltig = {"$or": [{"expires_at": {"$lte": now}},
                         {"$expr": {"$gte": ["$used_count", "$max_uses"]}}]}
    items = await db.dealer_invites.find(
        {**basis, **gueltig}, {"_id": 0}).sort("created_at", -1).to_list(500)
    items += await db.dealer_invites.find(
        {**basis, **ungueltig}, {"_id": 0}).sort("created_at", -1).to_list(50)
    for i in items:
        i["valid"] = i["used_count"] < i["max_uses"] and i["expires_at"] > now
    return items


@router.delete("/dealer/invites/{invite_id}")
async def delete_invite(invite_id: str, user=Depends(current_haendler)):
    r = await db.dealer_invites.delete_one(
        {"id": invite_id, "dealer_id": user["dealer_id"]})
    if not r.deleted_count:
        raise HTTPException(404, "Einladung nicht gefunden")
    # Runde 15 (Nr. 8): Erstellen war geloggt, Loeschen nicht.
    await log_activity(user["dealer_id"], user["id"], "einladung.geloescht", ref=invite_id)
    return {"ok": True}


@router.get("/dealer/network/members")
async def list_network_members(response: Response, user=Depends(current_haendler),
                               before: Optional[str] = None):
    """Mitglieder des privaten Netzwerks (PR-Review 09/2026: vorher gab es
    weder Liste noch Widerruf — ein beigetretener Kaeufer behielt den
    Zugang dauerhaft, das Loeschen der Einladung entfernte nur den Link).

    before: optionaler Cursor (created_at des letzten gelisteten Mitglieds)
    fuer den naechsten Abschnitt, wenn X-Truncated=1 kam."""
    out = []
    # Runde 15 (Nr. 4): begrenzt und Konten in EINER Abfrage — vorher ohne
    # Limit und ein users.find_one je Mitglied (5.000 Mitglieder = 5.001
    # Abfragen je Seitenaufruf).
    # Runde 17 (Nr. 386): ab dem 2.001. Mitglied fielen aeltere still aus
    # der Liste — nicht sichtbar, aber weiter mit Zugang und ohne Widerruf-
    # Knopf. Jetzt eins mehr lesen als gezeigt wird, den Abschnitt per
    # X-Truncated melden (wie /appointments) und per before-Cursor
    # weiterblaettern lassen. Antwort bleibt eine Liste (Frontend).
    filt: Dict[str, Any] = {"dealer_id": user["dealer_id"]}
    if before:
        filt["created_at"] = {"$lt": before}
    mitglieder = await db.network_members.find(
        filt, {"_id": 0}).sort("created_at", -1).to_list(2001)
    response.headers["X-Truncated"] = "1" if len(mitglieder) > 2000 else "0"
    mitglieder = mitglieder[:2000]
    konten: Dict[str, dict] = {}
    ids = [m["buyer_user_id"] for m in mitglieder if m.get("buyer_user_id")]
    if ids:
        async for u in db.users.find(
                {"id": {"$in": ids}},
                {"_id": 0, "id": 1, "company_name": 1, "contact_name": 1,
                 "email": 1, "active": 1}):
            konten[u["id"]] = u
    for m in mitglieder:
        b = konten.get(m.get("buyer_user_id"))
        # Nachpruefung Runde 14 (Nr. 66): fehlt das Kaeuferkonto (Altbestand,
        # manuelle Loeschung), meldete die Liste active=True mit leerem
        # Namen. Ohne users-Dokument ist kein Login moeglich, also ehrlich
        # active=False plus fehlt=True; der Eintrag bleibt widerrufbar.
        fehlt = b is None
        b = b or {}
        out.append({"buyer_user_id": m["buyer_user_id"],
                    "company_name": b.get("company_name", ""),
                    "contact_name": b.get("contact_name", ""),
                    "email": b.get("email", ""),
                    "active": False if fehlt else b.get("active", True),
                    "fehlt": fehlt,
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
    # Runde 13: C4 — Merkliste des entfernten Kaeufers bereinigen: vorher
    # blieben die IDs privater Inserate nach dem Widerruf in
    # GET /marktplatz/favoriten stehen. Oeffentlicher Haendler: nur die
    # privaten Inserate fallen weg; nicht oeffentliches Profil: der Kaeufer
    # sieht ab jetzt gar nichts mehr von dieser Firma.
    dealer = await db.dealers.find_one({"id": user["dealer_id"]},
                                       {"_id": 0, "marketplace.public": 1})
    oeffentlich = bool(((dealer or {}).get("marketplace") or {}).get("public"))
    filt: Dict[str, Any] = {"dealer_id": user["dealer_id"]}
    if oeffentlich:
        filt["visibility"] = "private"
    weg = [l["id"] async for l in db.resale_listings.find(filt, {"_id": 0, "id": 1})]
    fav_filter: Dict[str, Any] = {"buyer_user_id": buyer_user_id,
                                  "listing_id": {"$in": weg}}
    if not oeffentlich:
        # Alt-Favoriten ohne dealer_id-Feld ueber die Inserats-IDs erwischen.
        fav_filter = {"buyer_user_id": buyer_user_id,
                      "$or": [{"dealer_id": user["dealer_id"]},
                              {"listing_id": {"$in": weg}}]}
    if weg or not oeffentlich:
        await db.buyer_favorites.delete_many(fav_filter)
    await log_activity(user["dealer_id"], user["id"], "netzwerk.mitglied.entfernt",
                       ref=buyer_user_id)
    return {"ok": True}


async def _redeem_invite(token: str, buyer_user_id: str) -> Optional[str]:
    """Löst eine Einladung ein. Liefert dealer_id oder None."""
    inv = await db.dealer_invites.find_one({"token": token})
    if not inv:
        return None
    # Runde 13: A9 — Einladungen einer gesperrten Firma waren weiter
    # einloesbar (Sperre setzt nur users.active des Chefs, Links blieben bis
    # zu 30 Tage gueltig). Vorher: Token/Ablauf/Nutzungen; jetzt zusaetzlich
    # fail-closed, solange der Haendler-Hauptaccount gesperrt ist — vor
    # BEIDEN Zweigen, damit auch "bereits eingeloest" keine Mitgliedschaft
    # mehr bestaetigt. Die Nutzung wird dabei NICHT verbraucht: nach dem
    # Entsperren funktioniert der Link wieder.
    if await firma_gesperrt(inv["dealer_id"]):
        return None
    if buyer_user_id in (inv.get("used_by") or []):
        # Bereits eingeloest: nur dann noch Mitglied, wenn der Haendler den
        # Zugang nicht inzwischen widerrufen hat.
        noch = await db.network_members.find_one(
            {"dealer_id": inv["dealer_id"], "buyer_user_id": buyer_user_id},
            {"_id": 1})
        return inv["dealer_id"] if noch else None
    # Offensichtlich ungueltig (abgelaufen/verbraucht): gar nicht erst
    # schreiben. Die verbindliche Pruefung bleibt der atomare Schritt unten.
    if (inv.get("expires_at", "") <= now_iso()
            or (inv.get("used_count") or 0) >= (inv.get("max_uses") or 0)):
        return None
    # Nachpruefung Runde 14 (Nr. 64/115): Reihenfolge gedreht — ZUERST die
    # Mitgliedschaft anlegen (Upsert, No-op wenn sie schon besteht), DANN
    # die Einladung atomar verbrauchen. Vorher wurde erst verbraucht und
    # dann die Mitgliedschaft geschrieben: brach es dazwischen ab, war der
    # Einmal-Link verloren (used_by voll, keine Mitgliedschaft -> Zweig oben
    # deutete das als Widerruf); und ein DELETE /dealer/invites in diesem
    # Fenster antwortete ok:true, obwohl danach noch ein Mitglied entstand.
    # Jetzt: bricht es nach dem Upsert ab, ist used_by beim naechsten
    # Versuch leer, der Upsert ein No-op und der Link wird regulaer
    # verbraucht. Wird die Einladung zwischen Upsert und Verbrauch
    # geloescht (oder ist sie doch ungueltig), findet find_one_and_update
    # nichts und die eben angelegte Mitgliedschaft wird zurueckgenommen —
    # nach ok:true des DELETE bleibt also keine neue Mitgliedschaft.
    r = await db.network_members.update_one(
        {"dealer_id": inv["dealer_id"], "buyer_user_id": buyer_user_id},
        {"$setOnInsert": {"via_invite_id": inv["id"], "created_at": now_iso()}},
        upsert=True)
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
        if r.upserted_id is not None:
            # Nur die EBEN angelegte Mitgliedschaft zuruecknehmen — eine
            # aeltere ueber eine andere Einladung bleibt unberuehrt.
            await db.network_members.delete_one({"_id": r.upserted_id})
        return None
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
    # B2B-Bestaetigung (AGB §1): Pflicht-Checkbox "Ich handle als Unternehmer
    # ... und akzeptiere AGB + Datenschutz". Pflichtfeld; False -> 400.
    gewerblich_bestaetigt: bool
    # USt-IdNr. oder Handelsregister-Nr. — freiwillig. Sieht der Wert wie
    # eine USt-IdNr. aus, wird das Landesformat geprueft (Audit 09/2026,
    # Punkt 40); die Online-Pruefung (VIES) stoesst der Admin an.
    ust_id: str = Field(default="", max_length=40)

    @field_validator("ust_id")
    @classmethod
    def _ustid(cls, v):
        from ustid import format_pruefen
        fehler, wert = format_pruefen(v or "")
        if fehler:
            raise ValueError(fehler)
        return wert

    @field_validator("password")
    @classmethod
    def _pw(cls, v):
        return _check_password_strength(v)


@router.post("/buyer/register")
async def buyer_register(body: BuyerRegisterIn, request: Request):
    if not body.gewerblich_bestaetigt:
        raise HTTPException(400, "Bitte bestätige, dass du als Unternehmer handelst")
    ip = client_ip(request)
    if not await register_limiter.check(ip):
        raise HTTPException(429, "Zu viele Registrierungen von dieser IP – bitte später erneut versuchen.")
    email = body.email.strip().lower()
    # Runde 13: B5 — vorher nur users; jetzt plattformweit (auch
    # driver_accounts), Meldung bewusst ohne Kontotyp.
    if await email_vergeben(email):
        raise HTTPException(409, "E-Mail bereits registriert")
    user_id = str(uuid.uuid4())
    sid = new_session_id()
    try:
        await db.users.insert_one({
            "id": user_id, "email": email,
            "password_hash": await hash_password_async(body.password),
            "role": "b2b_buyer", "active": True,
            "dealer_id": None,
            "company_name": body.company_name,
            "contact_name": body.contact_name,
            "phone": body.phone,
            "ust_id": body.ust_id.strip(),
            "gewerblich_bestaetigt_am": now_iso(),
            "current_session_id": sid,
            "created_at": now_iso(),
        })
    except DuplicateKeyError:
        # Nachpruefung Runde 14 (Nr. 62): Rennen zweier Registrierungen mit
        # derselben E-Mail — der Unique-Index faengt die Dublette, vorher
        # wurde daraus ein 500 samt Fehlerlog-Eintrag. Jetzt 409 wie in
        # routes/auth.py.
        raise HTTPException(409, "E-Mail bereits registriert")
    joined = None
    if body.invite_token:
        # Nachpruefung Runde 14 (Nr. 63): das Konto ist nach dem Insert
        # vollstaendig — ein Fehler beim Einloesen der Einladung darf die
        # Registrierung nicht kippen (vorher 500, Konto existierte trotzdem,
        # erneute Registrierung 409). Kein Rollback des Kontos: der waere
        # bei bereits verbrauchtem Einmal-Link schlimmer (Link verloren).
        # Der Beitritt ist ueber POST /invites/{token}/redeem nachholbar;
        # das Frontend warnt bei network_joined=False.
        try:
            joined = await _redeem_invite(body.invite_token, user_id)
        except Exception:
            log.exception("Einladung nach Registrierung nicht einloesbar "
                          "(Kaeufer %s)", user_id)
            joined = None
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
async def redeem_invite(token: str, user=Depends(buyer_nicht_gesperrt)):
    dealer_id = await _redeem_invite(token, user["id"])
    if not dealer_id:
        raise HTTPException(400, "Einladung ist abgelaufen oder bereits verwendet")
    dealer = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "company_name": 1})
    return {"ok": True, "dealer": (dealer or {}).get("company_name", "")}


# =========================================================
#                    MARKTPLATZ (Browse)
# =========================================================
@router.get("/marktplatz/haendler")
async def browse_dealers(response: Response,
                         # Runde 17 (Nr. 384): Suchtext begrenzt — er wird
                         # zu einem Regex ueber alle Firmennamen.
                         q: Optional[str] = Query(default=None, max_length=100),
                         user=Depends(marktplatz_besucher)):
    """Öffentliche Händlersuche + private Händler, in deren Netzwerk der
    Betrachter eingeladen wurde. Ohne Anmeldung: nur öffentliche Händler."""
    my_networks = [] if user is None else [
        m["dealer_id"] async for m in db.network_members.find(
            {"buyer_user_id": user["id"]}, {"_id": 0, "dealer_id": 1})]
    query: Dict[str, Any] = {"$or": [
        {"marketplace.public": True},
        {"id": {"$in": my_networks}},
    ]}
    if q:
        query["company_name"] = {"$regex": re.escape(q.strip()), "$options": "i"}
    if user is not None and user.get("dealer_id"):
        query["id"] = {"$ne": user["dealer_id"]}
    # Runde 13: A8 — gesperrte Firmen (Hauptaccount active=False) blieben im
    # oeffentlichen Marktplatz sichtbar; jetzt dieselbe Regel wie beim Login.
    gesperrt = await gesperrte_firmen_ids()
    if gesperrt:
        query.setdefault("id", {})["$nin"] = list(gesperrt)
    # Runde 17 (Nr. 401): ab dem 1.001. Haendler fehlten welche still —
    # eins mehr lesen, Abschnitt per X-Truncated melden, Liste kuerzen.
    dealers = await db.dealers.find(
        query, {"_id": 0, "id": 1, "company_name": 1, "city": 1, "phone": 1,
                "logo_url": 1, "marketplace": 1}).to_list(1001)
    response.headers["X-Truncated"] = "1" if len(dealers) > 1000 else "0"
    dealers = dealers[:1000]
    # Inseratszahlen ALLER Haendler in EINER Aggregation statt
    # count_documents je Haendler (kein N+1 mehr).
    # Runde 13: A7 — vorher zaehlte die Uebersicht auch private Inserate
    # (Bestandsgroesse fuer jeden Besucher sichtbar, sogar Sortierkriterium);
    # jetzt wie /marktplatz/listings und die Haendlerseite: private Inserate
    # zaehlen nur fuer Netzwerk-Mitglieder des jeweiligen Haendlers.
    counts = {row["_id"]: row["n"] async for row in db.resale_listings.aggregate([
        {"$match": {"dealer_id": {"$in": [dl["id"] for dl in dealers]},
                    "status": "veroeffentlicht",
                    "$or": [{"visibility": {"$ne": "private"}},
                            {"dealer_id": {"$in": my_networks}}]}},
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
    user=Depends(marktplatz_besucher),
    # Runde 17 (Nr. 384/385): Freitext-Filter begrenzt (sie werden zu
    # Regexen ueber den ganzen Bestand), Seitennummer gedeckelt (vorher
    # rechnete Mongo fuer page=10^9 einen sinnlos grossen $skip).
    q: Optional[str] = Query(default=None, max_length=100),
    make: Optional[str] = Query(default=None, max_length=100),
    model: Optional[str] = Query(default=None, max_length=100),
    fuel: Optional[str] = Query(default=None, max_length=100),
    price_min: Optional[float] = None, price_max: Optional[float] = None,
    km_min: Optional[int] = None, km_max: Optional[int] = None,
    ps_min: Optional[int] = None, ps_max: Optional[int] = None,
    sort: Optional[str] = None, dealer: Optional[str] = None,
    nur_favoriten: Optional[int] = 0,
    page: int = Query(1, ge=1, le=1000), limit: int = 300,
):
    """Alle für den Betrachter sichtbaren veröffentlichten Fahrzeuge.

    Filter, Sortierung und Seitengroesse laufen komplett in MongoDB
    (Aggregation) — keine harte 300er-Grenze und kein Nachfiltern in
    Python mehr. sort: preis_auf | preis_ab | km_auf | km_ab
    (Default: neueste zuerst). nur_favoriten=1: nur gemerkte Fahrzeuge.
    page/limit: Seitennummer (1..1000) und Treffer pro Seite (max. 300)."""
    # Standard 300 = das bisherige Maximum, damit das Frontend OHNE
    # Pagination-Umbau weiterhin denselben Bestand sieht; page/limit stehen
    # fuer kuenftige Pagination bereit.
    limit = max(1, min(int(limit or 300), 300))
    # Runde 17 (Nr. 385): Deckel auch hier, falls die Funktion nicht ueber
    # FastAPI (Query-Validierung) aufgerufen wird.
    page = max(1, min(int(page or 1), 1000))

    # Oeffentlicher Besucher (nicht angemeldet): kein Merkzettel, kein
    # Netzwerk — er sieht ausschliesslich oeffentliche Haendler und dort
    # nur oeffentliche Inserate.
    if user is None:
        fav_ids = set()
    else:
        fav_ids = {f["listing_id"] async for f in db.buyer_favorites.find(
            {"buyer_user_id": user["id"]}, {"_id": 0, "listing_id": 1})}
    my_networks, visible_dealers = await _sichtbare_haendler(user)

    match: Dict[str, Any] = {
        "status": "veroeffentlicht",
        "dealer_id": {"$in": list(visible_dealers)},
        # Sichtbarkeit "private": nur für eingeladene Netzwerk-Mitglieder.
        "$and": [_sichtbarkeits_klausel(my_networks)],
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
        zweige: List[Dict[str, Any]] = [
            {"case": {"$and": [{"$in": ["$dealer_id", my_networks]},
                               {"$gt": ["$prices.network", 0]}]},
             "then": "$prices.network"},
        ]
        # Runde 17 (Nr. 381): den B2B-Zweig nur fuer angemeldete
        # Zwischenhaendler — vorher filterte und sortierte ein anonymer
        # Besucher nach dem B2B-Preis (per price_max ablesbar), obwohl die
        # Anzeige ihm den oeffentlichen Preis zeigte.
        if user is not None:
            zweige.append({"case": {"$gt": ["$prices.b2b", 0]},
                           "then": "$prices.b2b"})
        eff_price = {"$switch": {"branches": zweige, "default": "$prices.public"}}
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

    # Runde 17 (Nr. 381): der Marktplatz ist seit 09/2026 oeffentlich —
    # "jeder hier ist registriert" stimmte nicht mehr, der B2B-Preis lag
    # fuer jeden anonymen Besucher offen. B2B nur fuer angemeldete
    # Zwischenhaendler; Netzwerkpreis-Logik unveraendert.
    is_trade = user is not None
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


async def _sichtbare_haendler(user: Optional[dict]) -> tuple:
    """Runde 13: A8/C4 — (my_networks, visible_dealers) fuer Fahrzeugliste
    und Merkliste aus EINER Regel: oeffentliche Haendler + eigenes Netzwerk,
    minus die eigene Firma, minus gesperrte Firmen (vorher blieben Inserate
    gesperrter Firmen in der Liste)."""
    my_networks = [] if user is None else [
        m["dealer_id"] async for m in db.network_members.find(
            {"buyer_user_id": user["id"]}, {"_id": 0, "dealer_id": 1})]
    public_dealer_ids = [d["id"] async for d in db.dealers.find(
        {"marketplace.public": True}, {"_id": 0, "id": 1})]
    visible_dealers = set(public_dealer_ids) | set(my_networks)
    visible_dealers -= await gesperrte_firmen_ids()
    if user is not None:
        visible_dealers.discard(user.get("dealer_id"))
    return my_networks, visible_dealers


def _sichtbarkeits_klausel(my_networks: list) -> dict:
    """Mongo-Klausel "privates Inserat nur im Netzwerk" (Liste, Merkliste,
    Haendleruebersicht — Runde 13: A7/C4 nutzen dieselbe Regel)."""
    return {"$or": [{"visibility": {"$ne": "private"}},
                    {"dealer_id": {"$in": my_networks}}]}


async def _inserat_sichtbar_fuer(user: dict, listing: dict) -> bool:
    """Darf DIESER Betrachter das Inserat sehen? (oeffentlicher Haendler
    oder eigenes Netzwerk; visibility=private nur im Netzwerk). Vorher
    liessen sich private Inserate bei bekannter ID favorisieren und
    anfragen (PR-Review 09/2026)."""
    dealer_id = listing.get("dealer_id")
    if dealer_id == user.get("dealer_id"):
        return False                      # eigene Inserate: kein Selbst-Interesse
    # Runde 13: A8 — gesperrte Firma: nichts merken, nichts anfragen; die
    # Netzwerkmitgliedschaft uebersteuert die Sperre nicht.
    if await firma_gesperrt(dealer_id):
        return False
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
async def toggle_favorit(listing_id: str, user=Depends(buyer_nicht_gesperrt)):
    """Fahrzeug merken / Merken aufheben (Toggle). Bewusst ohne Zugangs-Abo-
    Pflicht beim ENTFERNEN; zum Setzen muss der Zugang aktiv und das
    Inserat sichtbar sein (Betreiber-Sperre: gar nichts, Runde 13 C6)."""
    existing = await db.buyer_favorites.find_one(
        {"buyer_user_id": user["id"], "listing_id": listing_id})
    if existing:
        await db.buyer_favorites.delete_one({"_id": existing["_id"]})
        return {"favorit": False}
    # Runde 13: C2 — abgelaufener Marktplatz-Zugang konnte weiterhin
    # Favoriten SETZEN (nur current_buyer; _inserat_sichtbar_fuer prueft den
    # Zugang nicht). Vorher: 200 {"favorit": true} per bekannter Inserats-ID;
    # jetzt: 402 wie bei Fahrzeugliste und Anfrage. Entfernen bleibt bewusst
    # ohne Zugangspflicht. Wirkt nur bei MARKTPLATZ_KOSTENLOS=false.
    _zugang_erzwingen(user)
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
async def list_favoriten(user=Depends(buyer_nicht_gesperrt)):
    """IDs der gemerkten Fahrzeuge (fuers Herz-Icon)."""
    ids = [f["listing_id"] async for f in db.buyer_favorites.find(
        {"buyer_user_id": user["id"]}, {"_id": 0, "listing_id": 1})]
    if not ids:
        return {"listing_ids": []}
    # Runde 13: C4 — nur Favoriten zurueckgeben, die der Kaeufer JETZT sehen
    # darf (Netzwerk-Widerruf, Haendler nicht mehr oeffentlich, Inserat
    # privat/zurueckgezogen, Firma gesperrt). Vorher kamen die rohen IDs der
    # Merkliste zurueck. Die DB-Zeilen bleiben (Herz erscheint wieder,
    # sobald das Inserat wieder sichtbar ist); nur der Widerruf loescht.
    my_networks, visible_dealers = await _sichtbare_haendler(user)
    sichtbar = [l["id"] async for l in db.resale_listings.find(
        {"id": {"$in": ids}, "status": "veroeffentlicht",
         "dealer_id": {"$in": list(visible_dealers)},
         **_sichtbarkeits_klausel(my_networks)},
        {"_id": 0, "id": 1})]
    return {"listing_ids": sichtbar}


@router.get("/marktplatz/haendler/{slug}")
async def dealer_page(slug: str, user=Depends(marktplatz_besucher)):
    # Erreichbar per Kurzname ODER Dealer-ID (nicht jeder Haendler hat
    # einen Kurznamen gesetzt — die Karte im Markt verlinkt per ID).
    dl = await db.dealers.find_one(
        {"$or": [{"marketplace.slug": slug}, {"id": slug}]}, {"_id": 0})
    if not dl:
        raise HTTPException(404, "Händler nicht gefunden")
    # Runde 13: A8 — gesperrte Firma: Verkaufsseite wie "nicht vorhanden"
    # (404 statt 403, damit die Sperre nach aussen nicht erkennbar ist);
    # gilt auch fuer Netzwerkmitglieder.
    if await firma_gesperrt(dl["id"]):
        raise HTTPException(404, "Händler nicht gefunden")
    mp = dl.get("marketplace") or {}
    # Oeffentlicher Besucher ist in keinem Netzwerk und besitzt keine Firma.
    member = await _is_network_member(dl["id"], user["id"]) if user else False
    eigene_firma = user.get("dealer_id") if user else None
    if not mp.get("public") and not member and dl["id"] != eigene_firma:
        raise HTTPException(403, "Dieses Händlerprofil ist privat (nur auf Einladung)")
    listings = await db.resale_listings.find(
        {"dealer_id": dl["id"], "status": "veroeffentlicht"}, {"_id": 0},
    ).sort("published_at", -1).to_list(200)
    # Private Inserate nur für Netzwerk-Mitglieder (und den Händler selbst).
    if not member and dl["id"] != eigene_firma:
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
        # Runde 17 (Nr. 381): B2B-Preis nur fuer angemeldete Zwischenhaendler
        # (siehe browse_listings).
        "listings": [_public_listing_view(l, is_member=member,
                                          is_trade=user is not None)
                     for l in listings],
    }


# =========================================================
#              INTERESSENTEN / ANGEBOTE
# =========================================================
class InterestIn(BaseModel):
    # Runde 17 (Nr. 397): ge=0 liess inf/nan durch — "Infinity" landete als
    # Angebot in der Historie und beim Haendler; jetzt 422.
    offer: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    message: str = Field(default="", max_length=2000)


class InterestAnswerIn(BaseModel):
    action: Literal["akzeptieren", "ablehnen", "gegenangebot"]
    counter_offer: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
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
async def buyer_interests(user=Depends(buyer_nicht_gesperrt)):
    return await db.listing_interest.find(
        {"buyer_user_id": user["id"]}, {"_id": 0},
    ).sort("created_at", -1).to_list(200)


class BuyerInterestAnswerIn(BaseModel):
    # 09/2026: der Kaeufer kann jetzt auch selbst ein Gegenangebot machen
    action: Literal["annehmen", "ablehnen", "gegenangebot"]
    # Runde 17 (Nr. 397): kein inf/nan als Gegenangebot.
    counter_offer: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    message: str = Field(default="", max_length=2000)


# Verhandlungszustaende (beide Seiten): offen -> gegenangebot (Haendler)
# -> gegenangebot_kaeufer (Kaeufer) -> ... -> akzeptiert | abgelehnt
INTERESSE_OFFEN = ("offen", "gegenangebot", "gegenangebot_kaeufer")


# Runde 13: C1 — vorher hing nur die ERSTE Anfrage (send_interest) am
# Marktplatz-Zugang; ein gesperrter/abgelaufener Zwischenhaendler konnte bei
# MARKTPLATZ_KOSTENLOS=false eine laufende Verhandlung weiterfuehren und per
# "annehmen" ein Fahrzeug reservieren. Jetzt: 403 gesperrt / 402 ohne
# aktiven Zugang — fuer alle drei Aktionen (auch "ablehnen" schreibt eine
# Nachricht in die Historie beim Haendler).
@router.post("/interessen/{interest_id}/kaeufer-antwort")
async def buyer_answer_interest(interest_id: str, body: BuyerInterestAnswerIn,
                                user=Depends(require_marketplace_access)):
    """Kaeufer reagiert auf ein GEGENANGEBOT des Haendlers (Review 09/2026:
    der Kaeufer sah Gegenangebote, konnte aber nicht antworten).
    annehmen: Inserat wird atomar fuer den Kaeufer reserviert, Status
    'akzeptiert'. ablehnen: Status 'abgelehnt'. Beides nur einmal —
    der Statuswechsel selbst ist atomar gegen parallele Antworten."""
    it = await db.listing_interest.find_one(
        {"id": interest_id, "buyer_user_id": user["id"]}, {"_id": 0})
    if not it:
        raise HTTPException(404, "Anfrage nicht gefunden")
    # Runde 13: A10 — vorher reichte buyer_user_id + Status, jetzt muss der
    # Kaeufer das Inserat AKTUELL sehen duerfen (Netzwerk-Widerruf, privates
    # Inserat, privater/gesperrter Haendler) — sonst keine Fortfuehrung und
    # keine Reservierung. Bewusst 403 statt 404: der Kaeufer kennt die
    # Anfrage, es geht um entzogenen Zugang. Kein Status-Filter beim Laden,
    # damit die Pruefung auch beim Kaeufer-Gegenangebot greift.
    l = await db.resale_listings.find_one(
        {"id": it["listing_id"]}, {"_id": 0, "dealer_id": 1, "visibility": 1})
    if not l or not await _inserat_sichtbar_fuer(user, l):
        raise HTTPException(403, "Kein Zugang mehr zu diesem Inserat — die "
                                 "Anfrage kann nicht weitergeführt werden")
    if body.action == "gegenangebot":
        # Kaeufer macht (erneut) ein Angebot — solange nichts abgeschlossen ist
        # und der Haendler nicht gerade auf DIESES Kaeufer-Angebot antworten muss.
        if it.get("status") not in ("offen", "gegenangebot"):
            raise HTTPException(400, "Ein Gegenangebot ist jetzt nicht moeglich "
                                     f"(Status '{it.get('status')}')")
        if body.counter_offer is None or float(body.counter_offer) <= 0:
            raise HTTPException(400, "Gegenangebot benoetigt einen Betrag")
        betrag = round(float(body.counter_offer), 2)
        upd = await db.listing_interest.update_one(
            {"id": interest_id, "buyer_user_id": user["id"],
             "status": it.get("status")},
            {"$set": {"status": "gegenangebot_kaeufer",
                      "buyer_counter_offer": betrag, "updated_at": now_iso()},
             "$push": {"history": {"von": "kaeufer", "aktion": "gegenangebot",
                                   "angebot": betrag, "nachricht": body.message,
                                   "zeit": now_iso()}}})
        if upd.modified_count == 0:
            raise HTTPException(409, "Die Anfrage wurde gerade anderweitig "
                                     "beantwortet — bitte neu laden.")
        await log_activity(it.get("dealer_id", ""), user["id"],
                           "interesse.kaeufer.gegenangebot", ref=interest_id,
                           meta={"listing_id": it.get("listing_id"), "betrag": betrag})
        return {"ok": True, "status": "gegenangebot_kaeufer", "betrag": betrag}
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
        {"$set": {"status": neuer_status, "updated_at": now_iso(),
                  # Audit 09/2026: vereinbarter Preis verbindlich festhalten —
                  # aus GENAU dem Gegenangebot, das der Kaeufer gelesen hat.
                  **({"agreed_price": round(float(it.get("counter_offer") or 0), 2)}
                     if body.action == "annehmen" else {})},
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


async def _kaeufer_darf_noch(it: dict) -> None:
    """Nachpruefung Runde 14 (Nr. 1/2/3): Darf der Kaeufer dieser Anfrage
    die Verhandlung AKTUELL noch fuehren? Vorher pruefte answer_interest nur
    die Anfrage selbst — der Haendler konnte ein Fahrzeug fuer einen
    gesperrten, deaktivierten oder geloeschten Kaeufer reservieren (Nr. 1)
    und fuer einen aus dem Netzwerk entfernten Kaeufer bzw. ein inzwischen
    privates Inserat (Nr. 2/3); die Kaeuferseite war seit Runde 13 (C1,
    A10) dicht. Kaeufer NEU laden (nicht aus der Anfrage) und dieselbe
    Sichtbarkeitsregel wie beim Kaeufer anwenden. 409, weil die Anfrage
    existiert, aber ihr Zustand die Aktion nicht mehr zulaesst."""
    k = await db.users.find_one(
        {"id": it.get("buyer_user_id")},
        {"_id": 0, "id": 1, "role": 1, "active": 1, "dealer_id": 1,
         "marketplace_access": 1})
    if (not k or k.get("role") != "b2b_buyer" or k.get("active") is False
            or _access_status(k).get("gesperrt")):
        raise HTTPException(409, "Der Käufer ist nicht mehr aktiv — die "
                                 "Anfrage kann nicht weitergeführt werden")
    l = await db.resale_listings.find_one(
        {"id": it.get("listing_id")}, {"_id": 0, "dealer_id": 1, "visibility": 1})
    if not l or not await _inserat_sichtbar_fuer(k, l):
        raise HTTPException(409, "Der Käufer hat keinen Zugang mehr zu diesem "
                                 "Inserat — die Anfrage kann nicht "
                                 "weitergeführt werden")


@router.post("/interessen/{interest_id}/antwort")
async def answer_interest(interest_id: str, body: InterestAnswerIn,
                          user=Depends(current_haendler)):
    it = await db.listing_interest.find_one(
        {"id": interest_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if not it:
        raise HTTPException(404, "Anfrage nicht gefunden")
    if it["status"] not in INTERESSE_OFFEN:
        raise HTTPException(400, "Anfrage ist bereits abgeschlossen")
    # Nachpruefung Runde 14 (Nr. 48): im Status 'gegenangebot' liegt das
    # eigene Gegenangebot beim Kaeufer — "akzeptieren" haette den
    # URSPRUNGSPREIS als agreed_price festgeschrieben, waehrend der Knopf
    # den Abschluss zum eigenen Gegenangebot suggerierte. Der Haendler kann
    # sein eigenes Angebot nicht einseitig annehmen; Ablehnen und ein neues
    # Gegenangebot bleiben moeglich.
    if body.action == "akzeptieren" and it["status"] == "gegenangebot":
        raise HTTPException(400, "Dein Gegenangebot liegt beim Käufer — warte "
                                 "auf seine Antwort oder schreibe ein neues "
                                 "Angebot")
    # Nr. 1/2/3: Ablehnen darf der Haendler immer (schliesst nur ab);
    # Annehmen und Gegenangebot nur, wenn der Kaeufer noch darf.
    if body.action in ("akzeptieren", "gegenangebot"):
        await _kaeufer_darf_noch(it)
    status_map = {"akzeptieren": "akzeptiert", "ablehnen": "abgelehnt",
                  "gegenangebot": "gegenangebot"}
    new_status = status_map[body.action]
    update: Dict[str, Any] = {"status": new_status, "updated_at": now_iso()}
    if body.action == "akzeptieren":
        # Vereinbarter Preis: Kaeufer-Gegenangebot > urspruengliches Angebot
        update["agreed_price"] = (it.get("buyer_counter_offer")
                                  if it["status"] == "gegenangebot_kaeufer"
                                  else it.get("offer"))
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
