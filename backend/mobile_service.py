"""mobile.de Sandbox API client + cache + URL builder for filtered search.

Strategy:
- If MOBILE_API_USER/PASS provided: call services.sandbox.mobile.de Search API.
- Else: parse the bundled sandbox_data.xml (real Sandbox response) on import
  and serve those ads when the ad-id matches; otherwise return a deterministic
  template so any input still works for demo.
- Cache via MongoDB collection `vehicle_cache` with TTL index (default 30 minutes).
- URL builder produces a https://suchen.mobile.de search URL with filters
  derived from comparison rules.
- Make/model numeric IDs are loaded dynamically from
  `mobile_makes_models.json` (178 makes, 2721 models, sourced from the user's
  verified `allemodellefinal.txt` upload).
"""
import json
import os
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlencode, quote

import ssl
import certifi
import httpx
import xmltodict

_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

from owners_extractor import extract_owners_from_text
from proxy_config import get_proxy_url, random_user_agent


MOBILE_BASE = os.environ.get("MOBILE_API_BASE", "https://services.sandbox.mobile.de")
MOBILE_USER = os.environ.get("MOBILE_API_USER", "")
MOBILE_PASS = os.environ.get("MOBILE_API_PASS", "")
# Sandbox-/Demo-Daten NUR ausliefern, wenn ausdrücklich aktiviert. Sonst würde
# jeder fehlgeschlagene mobile.de-Abruf still ein erfundenes Fahrzeug liefern
# (und es 24 h cachen + in Verträge übernehmen). Default: ehrlicher Fehler.
MOBILE_SANDBOX_MODE = os.environ.get("MOBILE_SANDBOX_MODE", "").strip().lower() in (
    "1", "true", "yes", "on",
)


class MobileUnavailable(RuntimeError):
    """mobile.de-Fahrzeug konnte nicht echt geladen werden (Route -> HTTP 502)."""

FUEL_LABELS = {
    "DIESEL": "Diesel", "PETROL": "Benzin", "ELECTRICITY": "Elektro",
    "HYBRID": "Hybrid", "HYBRID_DIESEL": "Hybrid (Diesel/Elektro)",
    "LPG": "LPG", "CNG": "Erdgas (CNG)", "ETHANOL": "Bioethanol",
    "HYDROGENIUM": "Wasserstoff", "OTHER": "Andere",
}
GEAR_LABELS = {
    "MANUAL_GEAR": "Schaltgetriebe", "AUTOMATIC_GEAR": "Automatik",
    "SEMIAUTOMATIC_GEAR": "Halbautomatik",
}
CATEGORY_LABELS = {
    "Cabrio": "Cabrio / Roadster", "EstateCar": "Kombi", "Limousine": "Limousine",
    "OffRoad": "SUV / Geländewagen", "OtherCar": "Sonstiges", "SmallCar": "Kleinwagen",
    "SportsCar": "Sportwagen / Coupé", "Van": "Van / Kleinbus",
}

AD_ID_RE = re.compile(r"(?:id=|details\.html\?id=|/)(\d{6,12})")


def extract_ad_id(url_or_id: str) -> Optional[str]:
    if not url_or_id:
        return None
    s = url_or_id.strip()
    if s.isdigit() and 6 <= len(s) <= 12:
        return s
    m = AD_ID_RE.search(s)
    return m.group(1) if m else None


def kw_to_ps(kw) -> int:
    if not kw:
        return 0
    return int(round(float(kw) * 1.35962))


def ps_to_kw(ps) -> int:
    if not ps:
        return 0
    return int(round(float(ps) / 1.35962))


# -------------------- XML Parsing helpers --------------------
def _attr(node, k):
    if isinstance(node, dict):
        return node.get(f"@{k}") or node.get(k)
    return None


def _desc(node):
    if not isinstance(node, dict):
        return None
    d = node.get("resource:local-description")
    if isinstance(d, list):
        d = next((x for x in d if isinstance(x, dict) and x.get("@xml-lang") == "en"), d[0] if d else None)
    if isinstance(d, dict):
        return d.get("#text")
    return d


def _features_list(vehicle: dict) -> List[str]:
    feats_root = vehicle.get("ad:features") or {}
    feats_raw = feats_root.get("ad:feature") if isinstance(feats_root, dict) else []
    if isinstance(feats_raw, dict):
        feats_raw = [feats_raw]
    out = []
    for f in feats_raw or []:
        label = _desc(f) or _attr(f, "key")
        if label:
            out.append(str(label).replace("_", " ").title() if str(label).isupper() else str(label))
    return out


def _parse_ad_xml(ad: dict) -> Dict[str, Any]:
    """Map a single mobile.de <ad:ad> dict (already parsed by xmltodict) to a flat
    vehicle dict. Looks into <ad:vehicle> + <ad:vehicle><ad:specifics>."""
    vehicle = ad.get("ad:vehicle") or {}
    specifics = vehicle.get("ad:specifics") or {}

    make_node = vehicle.get("ad:make") or {}
    model_node = vehicle.get("ad:model") or {}
    cat_node = vehicle.get("ad:category") or {}
    fuel_node = specifics.get("ad:fuel") or vehicle.get("ad:fuel") or {}
    gear_node = specifics.get("ad:gearbox") or vehicle.get("ad:gearbox") or {}

    mileage = _attr(specifics.get("ad:mileage") or vehicle.get("ad:mileage") or {}, "value")
    kw = _attr(specifics.get("ad:power") or vehicle.get("ad:power") or {}, "value")
    fr = _attr(specifics.get("ad:first-registration") or vehicle.get("ad:first-registration") or {}, "value")
    if fr and len(fr) >= 7:  # 'YYYY-MM'
        fr = f"{fr[5:7]}/{fr[0:4]}"
    cubic = _attr(specifics.get("ad:cubic-capacity") or {}, "value")
    doors = _attr(specifics.get("ad:door-count") or vehicle.get("ad:door-count") or {}, "key")
    seats = _attr(specifics.get("ad:seats") or vehicle.get("ad:seats") or {}, "value")
    color = _desc(specifics.get("ad:exterior-color") or vehicle.get("ad:exterior-color"))

    price_node = ad.get("ad:price") or {}
    consumer_price = price_node.get("ad:consumer-price-amount") or {}
    list_price = _attr(consumer_price, "value")

    seller_node = ad.get("seller:seller") or {}
    seller_addr = seller_node.get("seller:address") or {}

    description_node = ad.get("ad:description") or {}
    description = _attr(description_node, "value") or (description_node.get("#text") if isinstance(description_node, dict) else "") or ""
    if isinstance(description, dict):
        description = description.get("#text", "")

    # Extract image URLs (largest available representation per image).
    image_urls: List[str] = []
    images_node = ad.get("ad:images") or {}
    image_list = images_node.get("ad:image") or []
    if isinstance(image_list, dict):
        image_list = [image_list]
    SIZE_PRIORITY = ["XXXL", "XXL", "XL", "L", "M", "S", "ICON"]
    for img in image_list:
        reps = img.get("ad:representation") or []
        if isinstance(reps, dict):
            reps = [reps]
        # Index by size attr.
        by_size = {}
        for r in reps:
            s = _attr(r, "size") or ""
            u = _attr(r, "url") or ""
            if s and u:
                by_size[s] = u
        # Pick the highest-quality URL we have.
        chosen = next((by_size[s] for s in SIZE_PRIORITY if s in by_size), None)
        if chosen and chosen not in image_urls:
            image_urls.append(chosen)

    return {
        "mobile_ad_id": _attr(ad, "key"),
        "detail_url": _attr(ad.get("ad:detail-page") or {}, "url"),
        "make": _attr(make_node, "key"),
        "make_label": _desc(make_node) or _attr(make_node, "key"),
        "model": _attr(model_node, "key"),
        "model_label": _desc(model_node) or _attr(model_node, "key"),
        "model_description": _attr(vehicle.get("ad:model-description") or {}, "value"),
        "category": _attr(cat_node, "key"),
        "category_label": _desc(cat_node) or CATEGORY_LABELS.get(_attr(cat_node, "key") or "", ""),
        "first_registration": fr,
        "mileage": int(mileage) if mileage else None,
        "fuel": _attr(fuel_node, "key"),
        "fuel_label": FUEL_LABELS.get(_attr(fuel_node, "key") or "", _desc(fuel_node) or ""),
        "gearbox": _attr(gear_node, "key"),
        "gearbox_label": GEAR_LABELS.get(_attr(gear_node, "key") or "", _desc(gear_node) or ""),
        "power_kw": int(kw) if kw else None,
        "power_ps": kw_to_ps(int(kw)) if kw else None,
        "displacement": int(cubic) if cubic else None,
        "doors": doors,
        "seats": int(seats) if seats else None,
        "color": color,
        "vin": _attr(vehicle.get("ad:vin") or {}, "value"),
        "license_plate": None,
        "hu": _attr(specifics.get("ad:hu") or vehicle.get("ad:hu") or {}, "value"),
        "previous_owners": _attr(specifics.get("ad:previous-owner") or vehicle.get("ad:previous-owner") or {}, "value")
                           or extract_owners_from_text(description),
        "accident_damaged": (_attr(vehicle.get("ad:accident-damaged") or {}, "value") == "true"),
        "roadworthy": (_attr(vehicle.get("ad:roadworthy") or {}, "value") != "false"),
        "features": _features_list(vehicle),
        "description": description,
        "list_price": float(list_price) if list_price else None,
        "currency": _attr(price_node, "currency") or "EUR",
        "seller_name": _attr(seller_node.get("seller:contact-person") or {}, "value")
                       or _attr(seller_node.get("seller:company-name") or {}, "value")
                       or ("Händler" if _attr(seller_node.get("seller:type") or {}, "commercial") == "true" else "Privatverkäufer"),
        "seller_address": _attr(seller_addr.get("seller:street") or {}, "value") if isinstance(seller_addr, dict) else None,
        "seller_zip": _attr(seller_addr.get("seller:zipcode") or {}, "value") if isinstance(seller_addr, dict) else None,
        "seller_city": _attr(seller_addr.get("seller:city") or {}, "value") if isinstance(seller_addr, dict) else None,
        "seller_phone": _attr(seller_node.get("seller:phone") or {}, "value"),
        "seller_email": _attr(seller_node.get("seller:email") or {}, "value"),
        "image_urls": image_urls,
        "image_count": len(image_urls),
    }


# -------------------- Sandbox bundle --------------------
_SANDBOX_BUNDLE: Dict[str, Dict[str, Any]] = {}
_SANDBOX_LIST: List[Dict[str, Any]] = []


def _load_sandbox_bundle():
    """Load /app/backend/sandbox_data.xml once → memory dict by ad_id."""
    global _SANDBOX_BUNDLE, _SANDBOX_LIST
    p = Path(__file__).parent / "sandbox_data.xml"
    if not p.exists():
        return
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = xmltodict.parse(f.read())
        ads = data.get("search:search-result", {}).get("search:ads", {}).get("ad:ad", [])
        if isinstance(ads, dict):
            ads = [ads]
        for raw in ads:
            v = _parse_ad_xml(raw)
            if v.get("mobile_ad_id"):
                _SANDBOX_BUNDLE[v["mobile_ad_id"]] = v
                _SANDBOX_LIST.append(v)
    except Exception as exc:
        print(f"[mobile_service] failed to load sandbox bundle: {exc}")


# Sandbox bundle is loaded LATER, after _load_makes_models() so that
# the generic-model enhancement can resolve real model names from titles.


# -------------------- API fetch --------------------
async def _fetch_from_mobile_api(ad_id: str) -> Optional[dict]:
    if not (MOBILE_USER and MOBILE_PASS):
        return None
    url = f"{MOBILE_BASE}/search-api/ad/{ad_id}"
    try:
        async with httpx.AsyncClient(
            timeout=8.0, verify=_SSL_CONTEXT, proxy=get_proxy_url(),
        ) as client:
            r = await client.get(url, auth=(MOBILE_USER, MOBILE_PASS),
                                 headers={"Accept": "application/xml",
                                          "User-Agent": random_user_agent()})
            if r.status_code != 200:
                return None
            data = xmltodict.parse(r.text)
            ad = data.get("ad:ad") or data.get("ad") or {}
            if not ad:
                return None
            parsed = _parse_ad_xml(ad)
            # Recover real model name when seller picked "Weitere [Brand]".
            try:
                _enhance_generic_model(parsed)
            except Exception:
                pass
            return parsed
    except Exception:
        return None


# -------------------- Mock fallback --------------------
def _mock_vehicle(ad_id: str) -> dict:
    """Resolve to a real sandbox ad if matching id, else rotate."""
    if ad_id in _SANDBOX_BUNDLE:
        v = dict(_SANDBOX_BUNDLE[ad_id])
        v["mobile_ad_id"] = ad_id
        return v
    if _SANDBOX_LIST:
        # Deterministic rotation for any other id
        v = dict(_SANDBOX_LIST[int(ad_id) % len(_SANDBOX_LIST)])
        v["mobile_ad_id"] = ad_id
        v["detail_url"] = f"https://suchen.mobile.de/auto-inserat/{ad_id}.html"
        return v
    # Last fallback if XML missing
    return {
        "mobile_ad_id": ad_id,
        "detail_url": f"https://suchen.mobile.de/auto-inserat/{ad_id}.html",
        "make": "VOLKSWAGEN", "make_label": "Volkswagen", "model": "Golf", "model_label": "Golf",
        "model_description": "Golf VII 1.6 TDI", "category": "Limousine", "category_label": "Limousine",
        "first_registration": "08/2015", "mileage": 145880,
        "fuel": "DIESEL", "fuel_label": "Diesel",
        "gearbox": "MANUAL_GEAR", "gearbox_label": "Schaltgetriebe",
        "power_kw": 81, "power_ps": 110, "displacement": 1598,
        "doors": "FOUR_OR_FIVE", "seats": 5, "color": "Weiß",
        "features": ["Klimaanlage", "Tempomat", "Bluetooth"],
        "description": "Demo-Fahrzeug.", "list_price": 8490.0, "currency": "EUR",
        "seller_name": "Demo Händler", "seller_zip": "10115", "seller_city": "Berlin",
        "seller_phone": "", "seller_email": "",
        "accident_damaged": False, "roadworthy": True,
    }


# -------------------- Cache --------------------
async def cache_get(db, ad_id: str) -> Optional[dict]:
    doc = await db.vehicle_cache.find_one({"mobile_ad_id": ad_id}, {"_id": 0})
    if not doc:
        return None
    expires_at = doc.get("expires_at")
    if expires_at:
        ea = datetime.fromisoformat(expires_at) if isinstance(expires_at, str) else expires_at
        if ea.tzinfo is None:
            ea = ea.replace(tzinfo=timezone.utc)
        if ea < datetime.now(timezone.utc):
            return None
    return doc.get("data")


async def cache_set(db, ad_id: str, data: dict, ttl_minutes: int = 30):
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    await db.vehicle_cache.update_one(
        {"mobile_ad_id": ad_id},
        {"$set": {
            "mobile_ad_id": ad_id, "data": data,
            "expires_at": expires_at.isoformat(), "expires_at_dt": expires_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )


async def get_vehicle(db, ad_id: str) -> dict:
    cached = await cache_get(db, ad_id)
    if cached:
        # Run the generic-model recovery on cached entries too — earlier
        # cached vehicles may pre-date this enhancement.
        try:
            _enhance_generic_model(cached)
        except Exception:
            pass
        return {**cached, "_source": "cache"}
    fresh = await _fetch_from_mobile_api(ad_id)
    if not fresh:
        # Kein echtes Ergebnis. Nur im ausdrücklichen Sandbox-Modus dürfen
        # Demo-Daten zurückgehen — sonst ehrlicher Fehler statt Fake-Daten.
        if MOBILE_SANDBOX_MODE:
            fresh = _mock_vehicle(ad_id)
            fresh["_source"] = "sandbox" if ad_id in _SANDBOX_BUNDLE else "mock"
        elif not (MOBILE_USER and MOBILE_PASS):
            raise MobileUnavailable(
                "mobile.de ist nicht angebunden (Zugangsdaten fehlen). Bitte eine "
                "kleinanzeigen.de-URL verwenden oder MOBILE_API_USER/MOBILE_API_PASS "
                "in der .env setzen. (Zum lokalen Testen: MOBILE_SANDBOX_MODE=true)"
            )
        else:
            raise MobileUnavailable(
                "Fahrzeug konnte bei mobile.de nicht geladen werden — Inserat evtl. "
                "entfernt oder mobile.de-API vorübergehend nicht erreichbar."
            )
    else:
        fresh["_source"] = "api"
    # Defensive — _fetch_from_mobile_api already does this, but applying
    # again on mock/sandbox returns is harmless and keeps behavior uniform.
    try:
        _enhance_generic_model(fresh)
    except Exception:
        pass
    await cache_set(db, ad_id, {k: v for k, v in fresh.items() if not k.startswith("_")})
    return fresh


# -------------------- Filter URL builder --------------------
def _parse_first_registration(fr: str) -> Optional[int]:
    if not fr:
        return None
    m = re.search(r"(\d{4})", str(fr))
    return int(m.group(1)) if m else None


# ---------- Dynamic make/model ID lookup ----------
# Loads /app/backend/mobile_makes_models.json (178 makes, 2721 models)
# at import time and builds a normalized lookup index. Used to translate
# the API's vehicle.make / vehicle.model into mobile.de's internal numeric
# IDs for the `ms=MAKE_ID;MODEL_ID;;;` URL segment.
_MAKES_INDEX: Dict[str, Dict[str, Any]] = {}

# Common API-key → JSON-display-name aliases (where the mobile.de API ad
# returns one spelling but our JSON catalogue uses another). Normalized
# values, no diacritics. e.g. API returns "VW", catalogue has "Volkswagen".
_MAKE_ALIASES = {
    "vw": "volkswagen",
    "mercedesbenz": "mercedesbenz",  # API: MERCEDES_BENZ → norm same
    "mercedes": "mercedesbenz",
    "alfaromeo": "alfaromeo",
    "landrover": "landrover",
    "rangerover": "landrover",
    "rollsroyce": "rollsroyce",
    "astonmartin": "astonmartin",
    "dsautomobiles": "ds",
    "ds": "ds",
}


def _normalize(s: str) -> str:
    """Lowercase, strip diacritics, keep only [a-z0-9]. Used to match
    'Citroën' == 'CITROEN' == 'citroen', or 'C-Klasse' == 'cklasse'."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _load_makes_models():
    """Build _MAKES_INDEX from mobile_makes_models.json. Each entry holds
    the raw make name, its mobile.de numeric id, and a normalized model
    lookup table."""
    global _MAKES_INDEX
    p = Path(__file__).parent / "mobile_makes_models.json"
    if not p.exists():
        print("[mobile_service] mobile_makes_models.json not found")
        return
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"[mobile_service] failed to load makes/models: {exc}")
        return
    for marke in data.get("marken", []):
        name = marke.get("name", "")
        mid = marke.get("id")
        if not name or mid is None:
            continue
        models: Dict[str, str] = {}
        for mod in marke.get("modelle", []):
            mn = mod.get("name", "")
            mod_id = mod.get("id")
            if not mn or mod_id is None:
                continue
            # Skip group (Alle) fallback entries from the lookup table —
            # they would shadow specific model matches because mobile.de's
            # filter expects the most specific id. We still keep them in a
            # secondary index for "first-word" fallback below (e.g. C 200
            # → C-Klasse).
            mod_id_s = str(mod_id)
            # First entry for a key wins (JSON is sorted A-Z, more specific
            # variants appear before "Andere"/groups for typical cases).
            norm = _normalize(mn)
            if norm and norm not in models:
                models[norm] = mod_id_s
            # Some catalogue names carry a generation/variant in parens
            # (e.g. "Aygo (X)", "Octavia (Mk3)") — also index the cleaned
            # form so a plain "Aygo" from kleinanzeigen still resolves.
            clean = re.sub(r"\s*\([^)]*\)\s*", "", mn).strip()
            if clean and clean != mn:
                norm_clean = _normalize(clean)
                if norm_clean and norm_clean not in models:
                    models[norm_clean] = mod_id_s
        _MAKES_INDEX[_normalize(name)] = {
            "id": str(mid),
            "raw_name": name,
            "models": models,
            # Keep raw model display names (cleaned of variant parens) for
            # text-based model recovery from listing titles/descriptions.
            "models_raw": sorted({
                re.sub(r"\s*\([^)]*\)\s*", "", (mod.get("name") or "")).strip()
                for mod in marke.get("modelle", [])
                if mod.get("name")
            } - {"", "Andere", "Sonstige", "Weitere"}),
        }
    print(f"[mobile_service] loaded {len(_MAKES_INDEX)} makes from JSON")


_load_makes_models()


# ---------- Generic-model recovery from title/description ----------
# When a listing has a generic model label like "Weitere Peugeot",
# "Sonstige BMW" or "Andere VW", try to recover the real model name
# from <ad:model-description> (the listing title) or the body text by
# matching against the brand's known model catalogue.

_GENERIC_MODEL_RX = re.compile(
    r"^\s*(weitere|andere|sonstige|other|misc)\b",
    re.IGNORECASE,
)


def _is_generic_model_label(label: Optional[str]) -> bool:
    if not label:
        return False
    return bool(_GENERIC_MODEL_RX.search(str(label).strip()))


def _enhance_generic_model(vehicle: Dict[str, Any]) -> Dict[str, Any]:
    """If `model_label` is generic (e.g. 'Weitere Peugeot'), try to find
    the real model name in the listing title (`model_description`) or
    the body description by matching against the brand's known models.
    Mutates and returns `vehicle`."""
    label = vehicle.get("model_label") or ""
    if not _is_generic_model_label(label):
        return vehicle
    if not _MAKES_INDEX:
        return vehicle

    # Resolve the brand entry.
    make_norm = _normalize(vehicle.get("make_label") or vehicle.get("make") or "")
    make_entry = _MAKES_INDEX.get(make_norm)
    if not make_entry and make_norm in _MAKE_ALIASES:
        make_entry = _MAKES_INDEX.get(_MAKE_ALIASES[make_norm])
    if not make_entry:
        return vehicle

    # Build the search text: prefer the listing title (model_description),
    # then fall back to the first part of the body description.
    md = (vehicle.get("model_description") or "").strip()
    desc = (vehicle.get("description") or "").strip()
    candidates_haystacks = []
    if md:
        candidates_haystacks.append(md)
    if desc:
        candidates_haystacks.append(desc[:600])
    if not candidates_haystacks:
        return vehicle

    raw_models = make_entry.get("models_raw") or []
    if not raw_models:
        return vehicle
    # Sort longest first so "407 sW" wins over "407".
    raw_models = sorted(raw_models, key=lambda x: len(x), reverse=True)

    def _try_match(haystack: str) -> Optional[str]:
        h_low = haystack.lower()
        for name in raw_models:
            n_low = name.lower().strip()
            if not n_low:
                continue
            # Word-boundary match on the full clean name.
            pat = r"(?<![\w])" + re.escape(n_low) + r"(?![\w])"
            if re.search(pat, h_low):
                return name
        return None

    # Search each haystack in priority order.
    for hs in candidates_haystacks:
        matched = _try_match(hs)
        if matched:
            vehicle["model_label"] = matched
            vehicle["model"] = matched.upper()
            return vehicle

    # Fallback: if model_description contains useful info beyond the
    # brand name, surface it as the label even if no catalogue match.
    if md:
        brand = (vehicle.get("make_label") or "").strip()
        cleaned = md
        if brand and md.lower().startswith(brand.lower()):
            cleaned = md[len(brand):].strip(" -·,")
        if cleaned and not _is_generic_model_label(cleaned):
            vehicle["model_label"] = cleaned
    return vehicle


# Now that the make/model index is loaded, populate the sandbox bundle
# (so sandbox listings also get generic-model enhancement applied).
_load_sandbox_bundle()
# Apply generic-model recovery to sandbox entries that have it.
for _ad_id, _v in list(_SANDBOX_BUNDLE.items()):
    _enhance_generic_model(_v)


def _resolve_make(vehicle: dict) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Return (make_id, make_entry) for the given vehicle. Tries make_label
    first (clean human form like 'Volkswagen', 'Citroën'), then make key
    (API form like 'VW', 'CITROEN'), then aliases."""
    candidates = [vehicle.get("make_label"), vehicle.get("make")]
    for cand in candidates:
        if not cand:
            continue
        norm = _normalize(cand)
        if norm in _MAKES_INDEX:
            entry = _MAKES_INDEX[norm]
            return entry["id"], entry
        if norm in _MAKE_ALIASES:
            aliased = _MAKE_ALIASES[norm]
            if aliased in _MAKES_INDEX:
                entry = _MAKES_INDEX[aliased]
                return entry["id"], entry
    return None, None


def _resolve_model(make_entry: Dict[str, Any], vehicle: dict) -> Optional[str]:
    """Return the mobile.de model id for the given vehicle within the
    given make. Tries (1) exact normalized match on model_label / model,
    (2) progressively shorter prefixes ('C 200' → 'C 20' → 'C 2' → 'C').
    Returns None if no match — the URL still gets the make filter."""
    if not make_entry:
        return None
    models = make_entry.get("models") or {}
    for cand in (vehicle.get("model_label"), vehicle.get("model")):
        if not cand:
            continue
        norm = _normalize(cand)
        if not norm:
            continue
        if norm in models:
            return models[norm]
        # Prefix shrink — e.g. mobile.de catalogue has 'C-Klasse' (norm=cklasse)
        # but the ad reports model_label 'C 200' (norm=c200). Try shrinking
        # the candidate one char at a time and look for a model name that
        # *starts with* that prefix.
        for length in range(len(norm) - 1, 0, -1):
            prefix = norm[:length]
            for mname_norm, mid in models.items():
                if mname_norm == prefix or mname_norm.startswith(prefix + "klasse"):
                    return mid
            # Also try matching a model whose first token equals the prefix
            # (e.g. 'passatvariant' → first try 'passat' which exists).
        # First-token fallback (split at first non-digit→letter boundary)
        m_first = re.match(r"[a-z]+", norm) or re.match(r"\d+", norm)
        if m_first:
            tok = m_first.group(0)
            if tok in models:
                return models[tok]
    return None


def build_search_url(vehicle: dict, rules: dict) -> str:
    # mobile.de's modern compact URL format (matches what the UI generates):
    #   ms=MAKE_ID;MODEL_ID;;;   (5 fields, 4 semicolons)
    #   fr=YYYY:YYYY  / fr=YYYY:    first registration range
    #   ml=MIN:MAX                  mileage range (km)
    #   pw=MIN:MAX                  power range (kW)
    #   ft=PETROL                   fuel type
    #   tr=MANUAL_GEAR              transmission
    #   c=OffRoad                   category
    #   dam=0/1                     damaged filter
    # Mixing old long names (maxMileage, fuels, …) with `ms=` confuses
    # mobile.de's parser → some filters get silently dropped. So keep
    # everything in compact form.
    params = [
        ("isSearchRequest", "true"),
        ("ref", "quickSearch"),
        ("s", "Car"),
        ("vc", "Car"),
        ("pageNumber", "1"),
    ]

    # Make + Model resolution from the JSON catalogue (mobile_makes_models.json,
    # 178 makes / 2721 models). Both make and model are matched in their
    # normalized form so 'Citroën'/'CITROEN', 'C-Klasse'/'C 200', and
    # 'VW'/'Volkswagen' all route correctly.
    make_id, make_entry = _resolve_make(vehicle)
    if make_id:
        model_id = _resolve_model(make_entry, vehicle)
        if model_id:
            params.append(("ms", f"{make_id};{model_id};;;"))
        else:
            params.append(("ms", f"{make_id};;;;"))
    # No fallback `ms=` if make is unknown — better to send no make filter
    # than the wrong one.

    # Erstzulassung (compact: fr=YYYY:YYYY or fr=YYYY:)
    fr_year = _parse_first_registration(vehicle.get("first_registration", ""))
    fr_rule = rules.get("first_registration", {"mode": "older_exact", "years": 1})
    if fr_rule.get("mode") == "year_range":
        from_y = fr_rule.get("from")
        to_y = fr_rule.get("to")
        fr_str = f"{from_y if from_y else ''}:{to_y if to_y else ''}"
        if fr_str != ":":
            params.append(("fr", fr_str))
    elif fr_year and fr_rule.get("mode") != "ignore":
        mode = fr_rule.get("mode")
        if mode == "exact":
            params.append(("fr", f"{fr_year}:{fr_year}"))
        elif mode == "older_exact":
            x = int(fr_rule.get("years", 1))
            params.append(("fr", f"{fr_year - x}:"))

    # Kilometer (compact: ml=MIN:MAX)
    km = vehicle.get("mileage")
    km_rule = rules.get("mileage", {"mode": "plus", "value": 30000})
    if km and km_rule.get("mode") != "ignore":
        mode = km_rule.get("mode")
        v = int(km_rule.get("value", 30000))
        if mode == "exact":
            params.append(("ml", f":{km}"))
        elif mode == "plus":
            params.append(("ml", f":{km + v}"))
        elif mode == "range":
            params.append(("ml", f"{max(0, km - v)}:{km + v}"))
        elif mode == "custom":
            mn = int(km_rule["min"]) if km_rule.get("min") is not None else ""
            mx = int(km_rule["max"]) if km_rule.get("max") is not None else ""
            params.append(("ml", f"{mn}:{mx}"))

    # Leistung (compact: pw=MIN:MAX in kW)
    kw = vehicle.get("power_kw")
    pwr_rule = rules.get("power", {"mode": "tolerance_ps", "value": 5})
    if kw and pwr_rule.get("mode") != "ignore":
        mode = pwr_rule.get("mode")
        if mode == "exact":
            params.append(("pw", f"{kw}:{kw}"))
        elif mode == "tolerance_kw":
            v = int(pwr_rule.get("value", 5))
            params.append(("pw", f"{max(1, kw - v)}:{kw + v}"))
        elif mode == "tolerance_ps":
            v_ps = int(pwr_rule.get("value", 5))
            cur_ps = vehicle.get("power_ps") or kw_to_ps(kw)
            mn = ps_to_kw(max(1, cur_ps - v_ps))
            mx = ps_to_kw(cur_ps + v_ps)
            params.append(("pw", f"{mn}:{mx}"))

    # Kraftstoff / Getriebe / Kategorie (compact)
    if rules.get("fuel", {}).get("mode") == "exact" and vehicle.get("fuel"):
        params.append(("ft", vehicle["fuel"]))
    if rules.get("gearbox", {}).get("mode") == "exact" and vehicle.get("gearbox"):
        params.append(("tr", vehicle["gearbox"]))
    if rules.get("category", {}).get("mode") == "exact" and vehicle.get("category"):
        params.append(("c", vehicle["category"]))
    if rules.get("doors", {}).get("mode") == "exact" and vehicle.get("doors"):
        params.append(("doors", str(vehicle["doors"])))

    # Hubraum (kept long form — no documented compact equivalent)
    cc = vehicle.get("displacement")
    cc_rule = rules.get("displacement", {"mode": "ignore"})
    if cc and cc_rule.get("mode") in ("exact", "tolerance"):
        if cc_rule.get("mode") == "exact":
            params.append(("minCubicCapacity", str(cc)))
            params.append(("maxCubicCapacity", str(cc)))
        else:
            v = int(cc_rule.get("value", 100))
            params.append(("minCubicCapacity", str(max(0, cc - v))))
            params.append(("maxCubicCapacity", str(cc + v)))

    # Schaden (compact: dam=0 = nicht anzeigen, dam=1 = anzeigen)
    if rules.get("damage", {}).get("mode") == "no_accident":
        params.append(("dam", "0"))

    # Anbieter (kept long — no documented compact equivalent)
    seller_mode = rules.get("seller", {}).get("mode", "all")
    if seller_mode == "dealer":
        params.append(("sellerType", "DEALER"))
    elif seller_mode == "private":
        params.append(("sellerType", "FOR_SALE_BY_OWNER"))

    # Land / Country (mobile.de URL param: cn=DE; multiple via cn=DE&cn=AT…).
    # Default = nur Deutschland; "all" = kein Filter.
    country_rule = rules.get("country") or {}
    country_mode = country_rule.get("mode", "exact")
    if country_mode == "exact":
        codes = country_rule.get("codes")
        if not codes:
            single = country_rule.get("value") or "DE"
            codes = [single]
        for code in codes:
            if code:
                params.append(("cn", code))

    # Ausstattungs-Filter: Navigation
    feats = rules.get("features") or {}
    vehicle_features = set(
        (f or "").lower() for f in (vehicle.get("features") or [])
    )
    nav_rule = feats.get("navigation") or {}
    nav_mode = nav_rule.get("mode", "ignore")
    if nav_mode == "always":
        params.append(("f", "NAVIGATION_SYSTEM"))
    elif nav_mode == "exact":
        if any(("navi" in vf or "navigation" in vf) for vf in vehicle_features):
            params.append(("f", "NAVIGATION_SYSTEM"))

    # Klimatisierung – mobile.de Single-Select-Enum unter `climatisation=`.
    # Werte: AUTOMATIC_CLIMATISATION, MANUAL_CLIMATISATION,
    # AUTOMATIC_CLIMATISATION_2_ZONES, _3_ZONES, _4_ZONES, NO_CLIMATISATION.
    climate_rule = rules.get("climatisation") or {}
    climate_mode = climate_rule.get("mode", "ignore")
    valid_climate = {
        "AUTOMATIC_CLIMATISATION",
        "MANUAL_CLIMATISATION",
        "AUTOMATIC_CLIMATISATION_2_ZONES",
        "AUTOMATIC_CLIMATISATION_3_ZONES",
        "AUTOMATIC_CLIMATISATION_4_ZONES",
        "NO_CLIMATISATION",
    }
    if climate_mode == "always":
        val = climate_rule.get("value")
        if val in valid_climate:
            params.append(("climatisation", val))
    elif climate_mode == "exact":
        # Mappt anhand der Ausstattungs-Strings, was das Fahrzeug konkret hat.
        if any("klimaautomat" in vf or "automatic climat" in vf for vf in vehicle_features):
            params.append(("climatisation", "AUTOMATIC_CLIMATISATION"))
        elif any("klimaanl" in vf or "klima" in vf for vf in vehicle_features):
            params.append(("climatisation", "MANUAL_CLIMATISATION"))

    # Sortierung – billigste zuerst (mobile.de UI uses sb=p&od=up)
    params.append(("sb", "p"))
    params.append(("od", "up"))

    return f"https://suchen.mobile.de/fahrzeuge/search.html?{urlencode(params, quote_via=quote)}"


DEFAULT_RULES = {
    "first_registration": {"mode": "older_exact", "years": 1},
    "mileage": {"mode": "plus", "value": 30000},
    "power": {"mode": "tolerance_ps", "value": 5},
    "fuel": {"mode": "exact"},
    "gearbox": {"mode": "exact"},
    "category": {"mode": "exact"},
    "doors": {"mode": "ignore"},
    "displacement": {"mode": "ignore"},
    "damage": {"mode": "no_accident"},
    "seller": {"mode": "all"},
    "country": {"mode": "exact", "codes": ["DE"]},
    "radius": {"mode": "country"},
    "sort": "price_asc",
    "result_count": 4,
    "features": {
        "navigation": {"mode": "ignore"},
    },
    "climatisation": {"mode": "ignore", "value": "AUTOMATIC_CLIMATISATION"},
}


# Export-Profil: gängige Default-Einstellung für Export-Geschäft –
# weltweit, kein Kilometer-Limit, jeder Anbieter, Schäden mit drin.
DEFAULT_EXPORT_RULES = {
    "first_registration": {"mode": "any"},
    "mileage": {"mode": "ignore"},
    "power": {"mode": "tolerance_ps", "value": 10},
    "fuel": {"mode": "exact"},
    "gearbox": {"mode": "exact"},
    "category": {"mode": "exact"},
    "doors": {"mode": "ignore"},
    "displacement": {"mode": "ignore"},
    "damage": {"mode": "ignore"},
    "seller": {"mode": "all"},
    "country": {"mode": "all"},
    "radius": {"mode": "country"},
    "sort": "price_asc",
    "result_count": 4,
    "features": {
        "navigation": {"mode": "ignore"},
    },
    "climatisation": {"mode": "ignore", "value": "AUTOMATIC_CLIMATISATION"},
}
