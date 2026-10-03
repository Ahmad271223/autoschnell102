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
import math
import os
import re
import secrets
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import (APIRouter, Body, Depends, HTTPException, Query, Request,
                     Response)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from bson.regex import Regex
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from auth import (new_session_id, create_token,
                  verify_password_async, _DUMMY_HASH)
from deps import (_ablauf_parsen, current_user, db,
                  firma_gesperrt, gesperrte_firmen_ids, log_activity,
                  log_activity_sicher, now_iso)
from rate_limiter import (client_ip, login_limiter,
                          login_ip_limiter, login_schluessel,
                          bekannte_ip_merken, bekanntes_geraet_merken,
                          konto_fehlversuch, konto_gesperrt,
                          konto_gesperrt_text)
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
# Rollenprüfung 22.09.2026 (RP-509): so viele Tage vor dem Ablauf darf der
# Kaeufer die Verlaengerung anfragen (Marktplatz.jsx nutzt dieselbe Zahl).
VERLAENGERN_AB_TAGEN = 7


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
        creds: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
        response: Response = None):
    """Angemeldeter Zwischenhaendler ODER oeffentlicher Besucher (None).

    Rollenprüfung 22.09.2026 (RP-546): die Antwort wird an current_user
    durchgereicht — so bekommt auch die Fahrzeugliste (die Seite, auf der der
    Kaeufer die meiste Zeit verbringt) kurz vor Ablauf ein frisches Token
    (Kopfzeile X-Neues-Token). None bei direktem Aufruf.

    Ohne Anmeldung (KEIN Token) sind nur oeffentlich veroeffentlichte
    Fahrzeuge oeffentlicher Haendler sichtbar.

    Rollenprüfung 22.09.2026 (RP-530): Ein MITGESCHICKTES, aber ungueltiges
    Token (Sitzung auf einem anderen Geraet neu angemeldet, abgemeldet,
    Passwort neu) galt vorher still als "nicht angemeldet". Die Liste lud
    dann ohne Netzwerk-Inserate und mit oeffentlichen Preisen, und der
    Kaeufer merkte nicht, dass er abgemeldet war. Jetzt geht der Fehler von
    current_user (401 mit Grund) unveraendert durch; die Kaeufer-App meldet
    ab und zeigt den Grund auf der Anmeldeseite. Ohne Token bleibt alles
    oeffentlich wie bisher.

    Ist der Marktplatz NICHT kostenlos, gilt weiterhin: nur angemeldete
    Zwischenhaendler mit aktivem Zugang."""
    nutzer = None
    if creds and creds.credentials:
        nutzer = await current_user(creds, response)
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


# Rollenprüfung 22.09.2026 (RP-524): Akzente falten. "Citroën" (mobile.de)
# und "Citroen" (Picker/AutoScout) fanden sich vorher gegenseitig nie: das
# "ë" fiel beim Normalisieren weg ("citron") bzw. galt im Mongo-Muster als
# Trennzeichen. Buchstaben ohne Zerlegung (ø, æ, ß) stehen hier ausdruecklich.
_OHNE_ZERLEGUNG = {"ø": "o", "æ": "ae", "œ": "oe", "ß": "ss", "ł": "l", "đ": "d"}
# Im Mongo-Muster steht fuer jeden Grundbuchstaben eine Klasse mit allen
# Akzentformen (klein UND gross: ob PCRE Nicht-ASCII-Buchstaben mit der
# Option "i" faltet, haengt vom Build ab).
_AKZENTE = {
    "a": "àáâãäåāą", "c": "çćč", "d": "ďđ", "e": "èéêëēėęě", "g": "ğ",
    "i": "ìíîïīı", "l": "łľ", "n": "ñńň", "o": "òóôõöøō", "r": "ř",
    "s": "śšş", "t": "ť", "u": "ùúûüūů", "y": "ýÿ", "z": "źżž",
}
# Trennzeichen zwischen zwei Buchstaben: alles ausser Ziffern und Buchstaben
# (auch akzentuierten — sonst "verschluckte" das Muster ein "ë").
_TRENNER = "[^a-z0-9A-Z\u00c0-\u024f]*"
# Wortgrenzen (RP-507/RP-529): davor darf kein Buchstabe/keine Ziffer stehen.
_VORNE = "(?<![a-z0-9A-Z\u00c0-\u024f])"


def _falten(s: Optional[str]) -> str:
    """Klein, Akzente entfernt ("Citroën" -> "citroen", "Škoda" -> "skoda")."""
    t = str(s or "").lower()
    t = "".join(_OHNE_ZERLEGUNG.get(ch, ch) for ch in t)
    t = unicodedata.normalize("NFKD", t)
    return "".join(ch for ch in t if not unicodedata.combining(ch))


def _schluessel(s: Optional[str]) -> str:
    """Gefaltet und nur a-z/0-9 — Vergleichsschluessel fuer Marke/Modell."""
    return re.sub(r"[^a-z0-9]", "", _falten(s))


def _norm_make(s: Optional[str]) -> str:
    """Klein, ohne Leer-/Sonderzeichen und Akzente, plus Alias-Auflösung
    (VW->Volkswagen)."""
    key = _schluessel(s)
    return _MAKE_ALIASES.get(key, key)


def _zeichen_muster(ch: str) -> str:
    """Ein Zeichen des Suchschluessels als Mongo-Regex (mit Akzentformen)."""
    akz = _AKZENTE.get(ch)
    if not akz:
        return re.escape(ch)
    return "[" + ch + akz + akz.upper() + "]"


def _tolerantes_muster(key: str) -> str:
    """"mercedesbenz" trifft auch "Mercedes-Benz", "ds3" auch "DS 3",
    "citroen" auch "Citroën": zwischen den Zeichen beliebige Trennzeichen."""
    return _TRENNER.join(_zeichen_muster(ch) for ch in key)


def _make_regex_variants(filter_make: str) -> str:
    """Regex-Alternativen fuer den Mongo-Markenfilter — stellt die
    Alias-Toleranz von _make_matches wieder her (VW <-> Volkswagen,
    Mercedes <-> Mercedes-Benz). Erzeugt aus dem Filter alle Schreibweisen,
    die auf denselben normalisierten Namen zeigen, und matcht sie
    zeichenweise tolerant (Leer-/Sonderzeichen zwischen den Buchstaben).

    Rollenprüfung 22.09.2026 (RP-507): vorher ohne Wortgrenzen — der Alias
    "mb" (Mercedes) wurde zu "m[^a-z0-9]*b" und traf "Lamborghini". Jetzt
    muss jede Schreibweise als ganzes Wort stehen. RP-524: mit Akzenten."""
    key = _schluessel(filter_make)
    canon = _MAKE_ALIASES.get(key, key)
    variants = {key, canon} | {a for a, c in _MAKE_ALIASES.items() if c == canon}
    parts = []
    for v in sorted(variants, key=len, reverse=True):
        if len(v) < 2:
            continue
        parts.append(_tolerantes_muster(v))
    if not parts:
        return re.escape((filter_make or "").strip())
    return _VORNE + "(?:" + "|".join(parts) + ")(?![a-z0-9A-Z\u00c0-\u024f])"


def _modell_regex(modell: str) -> Optional[str]:
    """Rollenprüfung 22.09.2026 (RP-528/RP-529): Mongo-Muster fuer den
    Modellfilter. Vorher ein reiner Teilstring: "C 200" traf "GLC 200", "X1"
    traf "iX1", "TT" traf "quattro" — und "DS 3", "Ceed", "RS Q3" fanden
    Importe mit "DS3", "cee'd", "RSQ3" nie. Jetzt:
      * vorne eine Wortgrenze (kein Buchstabe/keine Ziffer davor),
      * zwischen den Zeichen beliebige Trennzeichen, Akzente egal,
      * hinten: endet die Eingabe auf eine Ziffer, darf keine Ziffer folgen
        ("A3" trifft nicht "A35", "C 200" aber "C 200 d"); endet sie auf
        einen Buchstaben, darf kein Buchstabe folgen ("Ka" nicht "Kadjar").
    None, wenn die Eingabe keinen Buchstaben/keine Ziffer enthaelt."""
    key = _schluessel(modell)
    if not key:
        return None
    ende = "(?![0-9])" if key[-1].isdigit() else "(?![a-zA-Z\u00c0-\u024f])"
    return _VORNE + _tolerantes_muster(key) + ende


# Rollenprüfung 22.09.2026 (RP-506): Kraftstoff-Filter auf den Code statt
# Teilstring auf die Beschriftung. AutoScout speichert "Elektro/Benzin" —
# "Hybrid" traf das nie, "Benzin" und "Elektro" lieferten die Hybride mit,
# und "LPG / Gas" traf "Erdgas (CNG)" nicht. Gruppen wie im Picker:
_KRAFTSTOFF_GRUPPEN = {
    "PETROL": ("PETROL",), "DIESEL": ("DIESEL",), "ELECTRICITY": ("ELECTRICITY",),
    "HYBRID": ("HYBRID", "HYBRID_DIESEL"), "HYBRID_DIESEL": ("HYBRID", "HYBRID_DIESEL"),
    "LPG": ("LPG", "CNG"), "CNG": ("LPG", "CNG"),
    "HYDROGENIUM": ("HYDROGENIUM",), "ETHANOL": ("ETHANOL",), "OTHER": ("OTHER",),
}
# Altbestand ohne Code (data.fuel roh, z.B. "ELEKTRO/BENZIN"): Beschriftung
# nach denselben Regeln wie fahrzeug_codes.kraftstoff_code einordnen.
_KS_HYBRID = (r"hybrid|plug.?in|(elektr|electr|strom).*(benzin|petrol|gasoline|diesel)"
              r"|(benzin|petrol|gasoline|diesel).*(elektr|electr|strom)")
_KS_GAS = r"lpg|autogas|fl.{1,2}ssiggas|cng|erdgas|natural.?gas"
_KS_ELEKTRO = r"elektr|electr|strom"
_KS_TEXT = {
    "HYBRID": (_KS_HYBRID, ()),
    "LPG": (_KS_GAS, (_KS_HYBRID,)),
    "ELECTRICITY": (_KS_ELEKTRO, (_KS_HYBRID, _KS_GAS)),
    "DIESEL": (r"diesel", (_KS_HYBRID, _KS_GAS)),
    "PETROL": (r"benzin|petrol|gasoline|^\s*super", (_KS_HYBRID, _KS_GAS, _KS_ELEKTRO)),
    "HYDROGENIUM": (r"wasserstoff|hydrogen", ()),
    "ETHANOL": (r"ethanol|e85", ()),
}


def _kraftstoff_bedingung(fuel: str) -> Dict[str, Any]:
    """Mongo-Bedingung fuer den Kraftstoff-Filter (RP-506). Erkannte Werte
    ("Benzin", "Hybrid", "Gas", "LPG", Codes) filtern auf data.fuel und — fuer
    Inserate ohne gueltigen Code — auf die Beschriftung nach Regeln. Nicht
    erkannte Werte bleiben beim alten Teilstring-Filter."""
    from fahrzeug_codes import KRAFTSTOFF_CODES, kraftstoff_code
    roh = (fuel or "").strip()
    code = "LPG" if _schluessel(roh) in ("gas", "lpggas") else kraftstoff_code(roh)
    if not code or code not in _KRAFTSTOFF_GRUPPEN:
        return {"data.fuel_label": {"$regex": re.escape(roh), "$options": "i"}}
    codes = list(_KRAFTSTOFF_GRUPPEN[code])
    text_code = {"HYBRID_DIESEL": "HYBRID", "CNG": "LPG"}.get(code, code)
    alternativen: List[Dict[str, Any]] = [{"data.fuel": {"$in": codes}}]
    if text_code in _KS_TEXT:
        treffer, ausser = _KS_TEXT[text_code]
        text: List[Dict[str, Any]] = [
            {"data.fuel": {"$nin": list(KRAFTSTOFF_CODES)}},
            {"data.fuel_label": {"$regex": treffer, "$options": "i"}}]
        # bson.Regex statt re.compile: genau die Option "i" (ein kompiliertes
        # Python-Muster braechte zusaetzlich das Unicode-Flag "u" mit).
        text += [{"data.fuel_label": {"$not": Regex(a, "i")}} for a in ausser]
        alternativen.append({"$and": text})
    return {"$or": alternativen}


def _ganzzahl_filter(wert: Optional[str], name: str) -> Optional[int]:
    """Rollenprüfung 22.09.2026 (RP-512): km/PS-Filter deutsch lesen.
    Vorher Optional[int]: "95.5" ergab eine englische FastAPI-422, und die
    Oberflaeche machte aus "150.000" im deutschen Browser 150. Punkte in
    Dreierbloecken sind Tausender ("150.000"), "km"/"PS" darf dabei stehen.
    Leer -> None, Unsinn -> 400 auf Deutsch."""
    if isinstance(wert, int) and not isinstance(wert, bool):
        return wert
    if not isinstance(wert, str):
        return None        # None oder (Direktaufruf) der Query-Standardwert
    t = wert.strip().lower()
    t = re.sub(r"(km|ps)$", "", t)
    t = re.sub(r"[\s\u00a0\u202f']", "", t)
    if not t:
        return None
    if re.fullmatch(r"\d{1,3}(\.\d{3})+|\d{1,9}", t):
        return int(t.replace(".", ""))
    raise HTTPException(400, f"{name}: bitte eine ganze Zahl eingeben (z. B. 150.000)")


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


def _preis_gesetzt(wert: Any) -> bool:
    return (isinstance(wert, (int, float)) and not isinstance(wert, bool)
            and math.isfinite(wert) and wert > 0)


def _preisstufe(listing: dict, *, is_member: bool, is_trade: bool) -> tuple:
    """(Preis, Stufe) fuer diesen Betrachter.

    Rollenprüfung 22.09.2026 (RP-521): vorher galt die ERSTE gesetzte Stufe
    (Netzwerk, dann B2B, dann oeffentlich). Senkte der Haendler nur den
    oeffentlichen Preis unter den B2B-Preis, sahen B2B-Kaeufer und
    Netzwerkpartner weiter den alten, hoeheren Preis — die Preissenkung
    erreichte ausgerechnet die bevorzugten Kaeufer nicht. Jetzt gilt der
    NIEDRIGSTE Preis unter den Stufen, die der Betrachter sehen darf
    (anonym: nur oeffentlich). Bei Gleichstand die bevorzugte Stufe.
    Ohne gesetzten Preis wie bisher der oeffentliche Wert ("auf Anfrage")."""
    p = listing.get("prices") or {}
    kandidaten = []
    if is_member and _preis_gesetzt(p.get("network")):
        kandidaten.append((p["network"], "netzwerk"))
    if is_trade and _preis_gesetzt(p.get("b2b")):
        kandidaten.append((p["b2b"], "b2b"))
    if _preis_gesetzt(p.get("public")):
        kandidaten.append((p["public"], "oeffentlich"))
    if not kandidaten:
        return _json_sicher(p.get("public")), "oeffentlich"
    # min() liefert bei Gleichstand das ERSTE Element (Netzwerk vor B2B).
    return min(kandidaten, key=lambda k: k[0])


def _price_for(listing: dict, *, is_member: bool, is_trade: bool) -> Optional[float]:
    """Sichtbarer Preis je Betrachter (RP-521: niedrigste zulaessige Stufe)."""
    return _preisstufe(listing, is_member=is_member, is_trade=is_trade)[0]


def _json_sicher(wert: Any) -> Any:
    """Audit 13.09.2026 (#17): nicht endliche Zahlen (inf/nan) aus Altbestand
    oder von der alten Fassung im Rollout. Ein einziger solcher Wert liess
    vorher die JSON-Antwort der ganzen oeffentlichen Liste mit 500 abbrechen."""
    if isinstance(wert, float) and not math.isfinite(wert):
        return None
    if isinstance(wert, list):
        return [w for w in wert if not (isinstance(w, float) and not math.isfinite(w))]
    return wert


# Rollenprüfung 22.09.2026 (RP-099/RP-349): Marktplatz-Fotos waren nur eine
# Stunde signiert (dateien.STANDARD_TTL). Wer die Liste morgens oeffnete, sah
# nachmittags beim Blaettern in der Lightbox nur noch Fehlbilder. Jetzt so
# lange wie die Portal-Vorschaubilder (bild_proxy, 3 Tage).
MARKT_FOTO_TTL = 3 * 24 * 3600

# Oeffentlich ausgegebene Fahrzeugdaten. Rollenprüfung 22.09.2026:
#  * RP-505: "model_description" (Anzeigentitel des PRIVATverkaeufers aus dem
#    Portal, im Editor nicht pflegbar, teils mit Telefonnummern) geht nicht
#    mehr raus — der Kaeufer sieht den Titel, den der Haendler pflegt.
#  * RP-504: "accident_damaged" (ungepruefte Portal-Angabe) geht nicht mehr
#    raus — die Oberflaeche machte aus False "Unfallfrei: Ja", ohne dass der
#    Haendler etwas zugesichert hatte. Zaehlt nur accident_free aus dem Editor.
_OEFFENTLICHE_DATEN = (
    "make_label", "model_label",
    "first_registration", "mileage", "fuel_label", "gearbox_label",
    "power_ps", "power_kw", "color", "previous_owners", "features",
    "accident_free")
# RP-507: leer gespeicherte Zahlen ("") kamen als 0 km / 0 PS beim Kaeufer an.
_ZAHLFELDER = ("mileage", "power_ps", "power_kw")


def _oeffentlicher_wert(k: str, wert: Any) -> Any:
    wert = _json_sicher(wert)
    if k in _ZAHLFELDER and isinstance(wert, str) and not wert.strip():
        return None
    return wert


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
    haendler_fotos = [signierte_datei_url(k, ttl=MARKT_FOTO_TTL)
                      for k in photos.get("uploaded_keys", [])]
    if mode in ("neu", "beide"):
        urls += haendler_fotos
    daten = {k: _oeffentlicher_wert(k, data.get(k)) for k in _OEFFENTLICHE_DATEN}
    # RP-504: Hat der Haendler NICHTS zur Unfallfreiheit angegeben, das
    # Einkaufsinserat aber einen Unfallschaden gemeldet, bleibt das als
    # Hinweis sichtbar (nie als Zusicherung "unfallfrei").
    if data.get("accident_damaged") is True and daten.get("accident_free") in (None, ""):
        daten["unfallschaden_laut_einkauf"] = True
    preis, stufe = _preisstufe(l, is_member=is_member, is_trade=is_trade)
    return {
        "id": l["id"], "dealer_id": l["dealer_id"],
        "title": l.get("title"), "description": l.get("description"),
        "known_defects": l.get("known_defects") or [],
        "status": l.get("status"),
        "data": daten,
        "photos": urls[:40],
        # Vom Haendler nachtraeglich hochgeladene Bilder (z.B. Schaeden) —
        # beim Kaeufer als 'Weitere Bilder vom Haendler' zum genauen Hinschauen.
        "dealer_photos": haendler_fotos[:40],
        "price": preis,
        "price_level": stufe,
        "published_at": l.get("published_at"),
        # Rollenprüfung 22.09.2026 (RP-519): Ablaufdatum auch fuer Kaeufer
        # (Marktplatz-Liste, -Detail, Haendlerseite) — dasselbe Feld wie in
        # den Inseraten des Haendlers und in den Anfragen.
        "laeuft_ab_am": _laeuft_ab_am(l),
    }


def _laeuft_ab_am(l: dict) -> Optional[str]:
    """Rollenprüfung 22.09.2026 (RP-519): Ende der Laufzeit eines Inserats
    (ISO) — nur solange es veroeffentlicht ist, sonst None. Die 21-Tage-
    Loeschung (cleanup_service.abgelaufene_inserate_entfernen) beendete
    laufende Verhandlungen, ohne dass Haendler oder Kaeufer die Frist irgendwo
    sahen. Gerechnet wird an EINER Stelle (routes.resale._laufzeit_bis), damit
    Inserat, Kaufanfragen und Marktplatz dasselbe Datum zeigen. Das ganze
    Inserat geht hinein: nach einer Freigabe durch den Betreiber zaehlt
    wieder_veroeffentlicht_am als spaeterer Start (RP-517, _laufzeit_anker —
    wie der Aufraeumlauf)."""
    if not l or l.get("status") != "veroeffentlicht":
        return None
    from routes.resale import _laufzeit_bis
    return _laufzeit_bis(l)


def _erstes_foto(l: dict) -> str:
    """Erstes Bild eines Inserats (fuer die Anfrageliste, RP-503) — ohne alle
    40 Links zu signieren."""
    photos = l.get("photos") or {}
    mode = photos.get("mode", "einkauf")
    if mode in ("neu", "beide"):
        for k in photos.get("uploaded_keys") or []:
            if k:
                return signierte_datei_url(k, ttl=MARKT_FOTO_TTL)
    if mode in ("einkauf", "beide"):
        from bild_proxy import thumbs as _thumbs
        erste = _thumbs((photos.get("einkauf_urls") or [])[:1], ttl=MARKT_FOTO_TTL)
        if erste:
            return erste[0]
    return ""


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
    await log_activity_sicher(did, user["id"],
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


# Audit 13.09.2026 (#24): Verwaltungsgrenze fuer den Chef (keine Nutzungs-
# grenze fuer Sucher). Ohne sie fielen ab der 501. gueltigen Einladung
# aeltere still aus GET /dealer/invites — weiter einloesbar, aber ohne id
# weder kopier- noch widerrufbar.
OFFENE_EINLADUNGEN_MAX = 500


def _einladung_gueltig(jetzt: str) -> Dict[str, Any]:
    return {"expires_at": {"$gt": jetzt},
            "$expr": {"$lt": ["$used_count", "$max_uses"]}}


@router.post("/dealer/invites")
async def create_invite(body: InviteIn, user=Depends(current_haendler)):
    # Audit 13.09.2026 (#24): hoechstens OFFENE_EINLADUNGEN_MAX gueltige Links
    # je Firma — so bleibt jeder gueltige Link in der Liste und widerrufbar.
    offen = await db.dealer_invites.count_documents(
        {"dealer_id": user["dealer_id"], **_einladung_gueltig(now_iso())})
    if offen >= OFFENE_EINLADUNGEN_MAX:
        raise HTTPException(409, f"Zu viele offene Einladungslinks ({offen}) – bitte "
                                 "nicht mehr benötigte löschen")
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
    # Audit 13.09.2026 (#57): der Link existiert schon — ein Audit-Fehler darf
    # die Antwort nicht mehr mit 500 kippen (Chef klickte sonst erneut).
    await log_activity_sicher(user["dealer_id"], user["id"], "einladung.erstellt",
                              ref=doc["id"], meta={"gueltig_h": body.validity_hours,
                                                   "nutzungen": body.max_uses})
    return {"ok": True, "token": token, "expires_at": expires,
            "max_uses": body.max_uses,
            # Kontonummer (13.09.2026): Kaeufer registrieren sich nicht mehr
            # selbst — der Link fuehrt zur Anmeldung, die die Einladung einloest.
            "link": f"/markt/login?invite={token}"}


@router.get("/dealer/invites")
async def list_invites(user=Depends(current_haendler), response: Response = None):
    # Nachpruefung Runde 14 (Nr. 87): vorher pauschal die neuesten 50 —
    # ab der 51. Einladung in 30 Tagen fiel eine aeltere, noch einloesbare
    # Einladung aus der Oberflaeche, blieb aber gueltig und war ohne ihre
    # id nicht mehr loeschbar. Jetzt: ALLE noch gueltigen (Deckel 500 nur
    # als Notbremse), dazu die neuesten 50 abgelaufenen/verbrauchten.
    # Antwort bleibt eine Liste (Einstellungen.jsx erwartet invites.map).
    # Audit 13.09.2026 (#24): der Deckel war still. Jetzt begrenzt create_invite
    # die offenen Links; die Liste liest Luft fuer parallele Erstellungen und
    # meldet einen Abschnitt trotzdem per X-Truncated.
    now = now_iso()
    basis: Dict[str, Any] = {"dealer_id": user["dealer_id"]}
    gueltig = _einladung_gueltig(now)
    ungueltig = {"$or": [{"expires_at": {"$lte": now}},
                         {"$expr": {"$gte": ["$used_count", "$max_uses"]}}]}
    grenze = OFFENE_EINLADUNGEN_MAX + 50
    items = await db.dealer_invites.find(
        {**basis, **gueltig}, {"_id": 0}).sort("created_at", -1).to_list(grenze + 1)
    abgeschnitten = len(items) > grenze
    if abgeschnitten:
        log.warning("Einladungen Firma %s: mehr als %d gueltige — Liste gekuerzt",
                    user["dealer_id"], grenze)
        items = items[:grenze]
    if response is not None:
        response.headers["X-Truncated"] = "1" if abgeschnitten else "0"
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
    # Audit 13.09.2026 (#57): nach dem Loeschen darf das Audit nicht mehr
    # kippen (die Wiederholung lieferte sonst 404).
    await log_activity_sicher(user["dealer_id"], user["id"], "einladung.geloescht",
                              ref=invite_id)
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
        # Kontonummer (13.09.2026): feste Projektion OHNE kontonummer — die
        # Kaeufernummer ist ein halbes Zugangsdatum und geht nie an Firmen.
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
    # Audit 13.09.2026 (#58): vorher wurde die Mitgliedschaft ZUERST geloescht.
    # Scheiterte danach die Merklisten-Bereinigung oder das Audit (500), endete
    # die Wiederholung mit 404 — Bereinigung und Audit liefen nie mehr. Jetzt:
    # Merkliste zuerst (idempotent), Mitgliedschaft zuletzt.
    mitglied_filt = {"dealer_id": user["dealer_id"], "buyer_user_id": buyer_user_id}
    if not await db.network_members.find_one(mitglied_filt, {"_id": 1}):
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
    # Pruefung 14.09.2026 (Nr. 10/11): ALLE noch gueltigen Einladungen dieser
    # Firma fuer diesen Kaeufer sperren — auch Mehrfach-Links, die er noch nie
    # benutzt hat (vorher liess sich der Widerruf mit so einem Link umgehen).
    # VOR dem Loeschen der Mitgliedschaft: ein gleichzeitiges Einloesen prueft
    # die Sperre nach dem Verbrauch noch einmal (siehe _redeem_invite) und
    # nimmt seine eben angelegte Mitgliedschaft zurueck. Neue Einladungen, die
    # der Chef danach ausspricht, gelten wieder.
    await db.dealer_invites.update_many(
        {"dealer_id": user["dealer_id"], "expires_at": {"$gt": now_iso()}},
        {"$addToSet": {"gesperrt_fuer": buyer_user_id}})
    # Phase 2 (2.7, B10): Haelt der Kaeufer bei dieser Firma eine Reservierung,
    # faellt sie mit dem Widerruf — sonst blieben Inserat und Fahrzeug fuer
    # einen gesperrten Kaeufer reserviert.
    freigegeben = []
    async for l in db.resale_listings.find(
            {"dealer_id": user["dealer_id"], "status": "reserviert",
             "reserved_for": buyer_user_id}, {"_id": 0, "id": 1}):
        await reservierung_zurueckgeben(l["id"], buyer_user_id)
        freigegeben.append(l["id"])
    # Rollenprüfung 22.09.2026 (RP-089/RP-188/RP-339): die Anfragen dieses
    # Kaeufers mit beenden — vorher blieb seine Anfrage "akzeptiert", obwohl
    # das Inserat wieder veroeffentlicht war; nahm der Haendler danach einen
    # zweiten Kaeufer an, standen zwei "akzeptiert" fuer ein Auto. Ebenso
    # blieben offene Verhandlungen auf Inseraten, die er nicht mehr sehen darf,
    # beim Haendler mit Knoepfen stehen, die nur noch 409 gaben.
    # VOR dem Loeschen der Mitgliedschaft (Wiederholung nach Teilfehler).
    beendet = await _anfragen_netzwerk_beenden(user["dealer_id"], buyer_user_id,
                                               weg=weg, oeffentlich=oeffentlich)
    r = await db.network_members.delete_one(mitglied_filt)
    # Nur wer tatsaechlich geloescht hat, schreibt das Audit (Doppelklick auf
    # zwei Servern: ein Eintrag); ok auch, wenn ein paralleler Aufruf schneller war.
    if r.deleted_count:
        meta: Dict[str, Any] = {}
        if freigegeben:
            meta["reservierungen_freigegeben"] = freigegeben
        if beendet:
            meta["anfragen_beendet"] = beendet
        await log_activity_sicher(user["dealer_id"], user["id"],
                                  "netzwerk.mitglied.entfernt", ref=buyer_user_id,
                                  meta=meta)
    return {"ok": True, **({"reservierungen_freigegeben": freigegeben} if freigegeben else {})}


async def _anfragen_netzwerk_beenden(dealer_id: str, buyer_user_id: str, *,
                                     weg: List[str], oeffentlich: bool) -> int:
    """RP-089/RP-188/RP-339: Anfragen eines aus dem Netzwerk entfernten
    Kaeufers bei dieser Firma beenden (Status abgelehnt, beendet_grund
    'netzwerk_entfernt', Verlaufseintrag von 'system'). Idempotent.

    * "akzeptiert": alle bei dieser Firma, deren Inserat NICHT an ihn verkauft
      ist — seine Reservierungen hat der Widerruf eben freigegeben (auch bei
      einer Wiederholung nach Teilfehler, wenn nichts mehr freizugeben war).
    * laufende (offen/gegenangebot/gegenangebot_kaeufer): nur auf Inseraten,
      die er ab jetzt nicht mehr sieht — private Inserate eines oeffentlichen
      Haendlers bzw. ALLE eines nicht oeffentlichen. Anfragen auf weiter
      oeffentlichen Inseraten laufen weiter (er sieht sie ja noch)."""
    jetzt = now_iso()
    verkauft_an_ihn = [l["id"] async for l in db.resale_listings.find(
        {"dealer_id": dealer_id, "status": "verkauft", "sold_to_user_id": buyer_user_id},
        {"_id": 0, "id": 1})]
    oder: List[Dict[str, Any]] = [
        {"status": "akzeptiert", "listing_id": {"$nin": verkauft_an_ihn}}]
    if oeffentlich:
        if weg:
            oder.append({"status": {"$in": list(INTERESSE_OFFEN)}, "listing_id": {"$in": weg}})
    else:
        oder.append({"status": {"$in": list(INTERESSE_OFFEN)}})
    res = await db.listing_interest.update_many(
        {"dealer_id": dealer_id, "buyer_user_id": buyer_user_id, "$or": oder},
        {"$set": {"status": "abgelehnt", "beendet_grund": "netzwerk_entfernt",
                  "updated_at": jetzt},
         "$push": {"history": {"von": "system", "aktion": "netzwerk_entfernt",
                               "zeit": jetzt}}})
    return res.modified_count


#: Fahrzeug-Ziele, die GENAU dem gleichnamigen Inseratsstatus entsprechen
#: (RP-093(3)): nur solange das Inserat so steht, wird nachgezogen.
_NACHZIEH_ZIELE = ("reserviert", "veroeffentlicht")


async def inserat_fahrzeug_nachziehen(listing_id: str, ziel: str) -> bool:
    """Phase 2 (15.09.2026, 2.7 / A8 B9 B10): Fahrzeug-Lebenszyklus zum Inserat
    nachziehen (reserviert bzw. veroeffentlicht). Gelingt es nicht, bleibt der
    Merker lifecycle_nacharbeit am Inserat, den cleanup_service.
    inserat_fahrzeug_nacharbeit_nachholen abarbeitet — vorher gab es nur einen
    Alarm, und das Fahrzeug blieb falsch. Liefert True bei Erfolg.

    Rollenprüfung 22.09.2026 (RP-093(3)/RP-192/RP-343(c)): Vorher wurde das
    Ziel blind nachgezogen — auch wenn das Inserat inzwischen verkauft,
    geloescht, zurueckgezogen oder wieder anders reserviert war. Der
    Nachholer (cleanup_service.inserat_fahrzeug_nacharbeit_nachholen) setzte
    so z.B. ein verkauftes Auto Stunden spaeter zurueck auf "veroeffentlicht",
    oder er scheiterte endlos und schlug Alarm. Jetzt wird zuerst der
    AKTUELLE Inseratsstatus gelesen: passt er nicht mehr zum Ziel, hat der
    spaetere Statuswechsel das Fahrzeug selbst mitgenommen — der Merker wird
    entfernt statt nachgezogen (Ergebnis True: nichts mehr zu tun)."""
    try:
        l = await db.resale_listings.find_one(
            {"id": listing_id},
            {"_id": 0, "vehicle_id": 1, "dealer_id": 1, "status": 1})
        if not l or not l.get("vehicle_id"):
            return True
        if ziel in _NACHZIEH_ZIELE and l.get("status") != ziel:
            weg = await db.resale_listings.update_one(
                {"id": listing_id, "lifecycle_nacharbeit": ziel},
                {"$unset": {"lifecycle_nacharbeit": "", "nacharbeit_versuche": ""}})
            if weg.modified_count:
                log.info("Inserat %s steht inzwischen auf %r — Fahrzeug-Nacharbeit "
                         "%r verworfen", listing_id, l.get("status"), ziel)
            return True
        from lifecycle import LifecycleError, set_lifecycle
        try:
            await set_lifecycle(l["vehicle_id"], l.get("dealer_id"), ziel)
        except LifecycleError as exc:
            await db.resale_listings.update_one(
                {"id": listing_id}, {"$set": {"lifecycle_nacharbeit": ziel}})
            import betrieb as _betrieb
            await _betrieb.alarm(db, "inserat_fahrzeug_desync", ref=listing_id,
                                 vehicle_id=l["vehicle_id"], ziel=ziel, fehler=str(exc)[:300])
            return False
        await db.resale_listings.update_one(
            {"id": listing_id, "lifecycle_nacharbeit": {"$exists": True}},
            {"$unset": {"lifecycle_nacharbeit": "", "nacharbeit_versuche": ""}})
        return True
    except Exception:  # noqa: BLE001
        log.exception("Fahrzeug zu Inserat %s nicht auf %s gesetzt", listing_id, ziel)
        try:
            await db.resale_listings.update_one(
                {"id": listing_id}, {"$set": {"lifecycle_nacharbeit": ziel}})
        except Exception:  # noqa: BLE001
            log.exception("Merker lifecycle_nacharbeit fuer Inserat %s nicht gesetzt", listing_id)
        return False


async def reservierung_zurueckgeben(listing_id: str, buyer_user_id: str) -> None:
    """Reservierung eines Inserats fuer diesen Kaeufer aufheben (idempotent).
    Phase 2 (2.7, B9): das Fahrzeug geht mit zurueck auf 'veroeffentlicht'."""
    r = await db.resale_listings.update_one(
        {"id": listing_id, "status": "reserviert", "reserved_for": buyer_user_id},
        {"$set": {"status": "veroeffentlicht", "updated_at": now_iso()},
         "$unset": {"reserved_for": ""}})
    if r.matched_count:
        await inserat_fahrzeug_nachziehen(listing_id, "veroeffentlicht")


async def fahrzeug_reserviert_markieren(listing_id: str) -> None:
    """Pruefung 14.09.2026 (Liste 1, Nr. 21): Der Verhandlungsweg setzte nur das
    Inserat auf 'reserviert', das Fahrzeug blieb 'veroeffentlicht'. Jetzt wird
    der Fahrzeug-Lebenszyklus nachgezogen (Phase 2: mit Merker statt Alarm)."""
    await inserat_fahrzeug_nachziehen(listing_id, "reserviert")


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
    # Pruefung 14.09.2026 (Nr. 10): nach einem Widerruf ist dieser Link fuer
    # diesen Kaeufer gesperrt — egal, ob er ihn schon einmal benutzt hat.
    if buyer_user_id in (inv.get("gesperrt_fuer") or []):
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
    # Rollenprüfung 22.09.2026 (RP-541): Ist der Kaeufer schon Mitglied — ueber
    # eine ANDERE Einladung (oder Altbestand ohne via_invite_id) —, verbraucht
    # dieser Link keine Nutzung. Vorher war der Upsert unten ein No-op, der
    # Verbrauch lief trotzdem: ein 5er-Link in einer Gruppe mit Bestands-
    # mitgliedern war leer, bevor die neuen Partner ihn oeffnen konnten.
    # Eine Mitgliedschaft ueber GENAU diesen Link (paralleler Aufruf) laeuft
    # weiter den normalen Weg (Audit #23).
    schon = await db.network_members.find_one(
        {"dealer_id": inv["dealer_id"], "buyer_user_id": buyer_user_id},
        {"_id": 0, "via_invite_id": 1})
    if schon is not None and schon.get("via_invite_id") != inv["id"]:
        return inv["dealer_id"]
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
    # Audit 13.09.2026 (#23): "hat dieser Kaeufer schon eingeloest" gehoert in
    # die atomare Bedingung. Vorher verbrauchte derselbe Kaeufer mit parallelen
    # Aufrufen mehrere Nutzungen eines 5er/10er-Links.
    verbraucht = await db.dealer_invites.find_one_and_update(
        {"id": inv["id"],
         "expires_at": {"$gt": now_iso()},
         "used_by": {"$ne": buyer_user_id},
         "$expr": {"$lt": ["$used_count", "$max_uses"]}},
        {"$inc": {"used_count": 1}, "$push": {"used_by": buyer_user_id}})
    if not verbraucht:
        # Audit 13.09.2026 (#23): hat ein PARALLELER Aufruf desselben Kaeufers
        # die Nutzung schon verbucht, verlaesst er sich auf genau die eben
        # angelegte Mitgliedschaft (sein Upsert war wirkungslos) — dann nicht
        # zuruecknehmen. Vorher blieb der Beitritt als "erfolgreich" gemeldet,
        # aber ohne Mitgliedschaft, und der Link war verloren. Restfenster: ein
        # Widerruf genau in diesen Millisekunden kann ueberholt werden
        # (vernachlaessigbar, der Chef sieht das Mitglied erst danach).
        schon = await db.dealer_invites.find_one(
            {"id": inv["id"], "used_by": buyer_user_id}, {"_id": 1})
        if schon:
            noch = await db.network_members.find_one(
                {"dealer_id": inv["dealer_id"], "buyer_user_id": buyer_user_id},
                {"_id": 1})
            return inv["dealer_id"] if noch else None
        if r.upserted_id is not None:
            # Nur die EBEN angelegte Mitgliedschaft zuruecknehmen — eine
            # aeltere ueber eine andere Einladung bleibt unberuehrt.
            await db.network_members.delete_one({"_id": r.upserted_id})
        return None
    # Pruefung 14.09.2026 (Nr. 11): Rennen mit dem Widerruf. Hat der Chef
    # zwischen der Pruefung oben und dem Verbrauch den Zugang widerrufen
    # (Einladung fuer diesen Kaeufer gesperrt, Mitgliedschaft geloescht), darf
    # die eben per Upsert angelegte Mitgliedschaft nicht stehen bleiben.
    nach = await db.dealer_invites.find_one({"id": inv["id"]}, {"_id": 0, "gesperrt_fuer": 1})
    if buyer_user_id in ((nach or {}).get("gesperrt_fuer") or []):
        await db.network_members.delete_one(
            {"dealer_id": inv["dealer_id"], "buyer_user_id": buyer_user_id,
             "via_invite_id": inv["id"]})
        return None
    return inv["dealer_id"]


# =========================================================
#              ZWISCHENHÄNDLER (b2b_buyer)
# =========================================================
@router.post("/buyer/register")
async def buyer_register():
    """Kontonummer (13.09.2026), Schritt 5: Kaeufer registrieren sich nicht
    mehr selbst — der Super-Admin legt sie an (POST /admin/buyers), die
    Einladung loest der Kaeufer nach der Anmeldung ein
    (POST /invites/{token}/redeem). 410 fuer gecachte alte Oberflaechen."""
    from routes.auth import NUR_BETREIBER_KONTEN
    raise HTTPException(410, NUR_BETREIBER_KONTEN)


class BuyerLoginIn(BaseModel):
    """Kontonummer (13.09.2026): `kontonummer`, `email` nur als alter
    Feldname (keine Suche per E-Mail-Adresse mehr, Schritt 5)."""
    kontonummer: Optional[str] = Field(default=None, max_length=80)
    email: Optional[str] = Field(default=None, max_length=254)
    password: str
    # Rollenprüfung 22.09.2026 (RP-557): Geraete-Schluessel aus einer frueheren
    # Anmeldung (wie /auth/login) — ein bekanntes Geraet ist wie eine bekannte
    # IP von der Konto-Sperre entlastet. Falsche Form = ignoriert.
    geraet_id: Optional[str] = Field(default=None, max_length=80)


@router.post("/buyer/login")
async def buyer_login(body: BuyerLoginIn, request: Request):
    """Login für Zwischenhändler (eigener Account, Rolle b2b_buyer)."""
    # Audit 13.09.2026 (#25): wie /auth/login seit Runde 26 — der Zaehler haengt
    # am KONTO (IP + E-Mail), nicht an der IP allein; sonst sperrten sich
    # Zwischenhaendler hinter derselben Adresse gegenseitig aus. Das weit
    # gefasste IP-Limit bremst weiter Rateversuche ueber viele Konten.
    # Bewusst derselbe Limiter "login" wie /auth/login (ein Kaeuferkonto kann
    # sich ueber beide Wege anmelden).
    ip = client_ip(request)
    kennung = (body.kontonummer or body.email or "").strip()
    schluessel = login_schluessel(ip, kennung)
    if not await login_limiter.check(schluessel):
        raise HTTPException(429, "Zu viele Anmeldeversuche für dieses Konto – bitte 60 Sekunden warten.")
    if not await login_ip_limiter.check(ip):
        raise HTTPException(429, "Zu viele Anmeldeversuche aus diesem Netz – bitte 60 Sekunden warten.")
    # Kontonummer (13.09.2026): nur per Nummer (Schritt 5: kein E-Mail-Zweig).
    # Kaeufer-Code (14.09.2026): '6FE7K2M' (auch klein geschrieben oder mit
    # Trennern getippt) ODER eine aeltere numerische Kaeufernummer.
    from kontonummer import anmeldekennung, kennung_normalisieren, nummer_bedingung
    from routes.auth import LOGIN_FALSCH
    nr = kennung_normalisieren(kennung)
    u = None
    if nr:
        u = await db.users.find_one({"kontonummer": nummer_bedingung(nr),
                                     "role": "b2b_buyer"})
    # Konto-Limiter VOR bcrypt (gleicher Weg fuer bekannte und unbekannte).
    konto_k = anmeldekennung(kennung)
    # Rollenprüfung 22.09.2026 (RP-557): vorher sperrte ein Angreifer mit 30
    # Fehlversuchen je Viertelstunde jede fortlaufende Kaeufernummer fuer ALLE
    # neuen IPs (Mobilnetz, Hotel-WLAN). Ein bekanntes Geraet zaehlt jetzt wie
    # eine bekannte IP (Sperre erst beim Dreifachen) — wie /auth/login.
    if await konto_gesperrt(konto_k, ip, u, geraet_id=body.geraet_id):
        raise HTTPException(429, konto_gesperrt_text())
    # Immer bcrypt rechnen (Dummy-Hash), um User-Enumeration per Timing zu
    # verhindern. Deaktivierte Accounts geben dieselbe 401 wie falsche Daten.
    pw_hash = u["password_hash"] if u else _DUMMY_HASH
    ok = await verify_password_async(body.password, pw_hash)
    if not u or not ok:
        await konto_fehlversuch(konto_k, ip)
    if not u or not ok or not u.get("active", True):
        raise HTTPException(401, LOGIN_FALSCH)
    # Audit 13.09.2026 (#26): Passwort stimmte — Zaehler dieses Kontos leeren,
    # damit fruehere Fehlversuche eine richtige Anmeldung nicht blockieren.
    await login_limiter.reset(schluessel)
    # Kontonummer (13.09.2026): den Konto-Zaehler (login_konto_limiter) bei
    # Erfolg bewusst NICHT leeren — sonst bekaeme ein Angreifer, der die
    # Nummer ueber viele IPs probiert, mit jeder Anmeldung des echten Nutzers
    # wieder volle Versuche. Der Nutzer selbst ist von seinen bekannten IPs
    # ohnehin frei; der Zaehler laeuft mit dem Fenster ab, vorher hebt nur der
    # Betreiber die Sperre auf (Passwort setzen, anmeldesperre_aufheben.py).
    sid = new_session_id()
    # Nachpruefung 20.09.2026: Hier stand nur {"id": u["id"]} — der
    # Firmen-Login (routes/auth.sitzungs_bedingung) und der Fahrer-Login
    # schreiben die Sitzung laengst NUR, wenn das Konto noch genau so
    # dasteht wie geprueft. Ohne diese Bedingung konnte ein Passwortwechsel
    # zwischen Pruefung und Schreiben eine gueltige Sitzung mit dem ALTEN
    # Passwort entstehen lassen. Jetzt auch beim Kaeufer: gleiches Passwort,
    # noch aktiv, nicht in Loeschung.
    from routes.auth import SITZUNG_UNGUELTIG, geraet_kurz, sitzungs_bedingung
    # Rollenprüfung 22.09.2026 (RP-098 Nr. 12 / RP-348): Zeitpunkt, Geraet und
    # IP der Sitzung wie beim Firmen-Login (routes/auth._sitzung_ausstellen).
    # Vorher stand hier nur die Sitzungs-ID — ein verdraengtes Kaeufer-Geraet
    # erfuhr nicht, WANN und von WELCHEM Geraet neu angemeldet wurde
    # (deps.sitzung_beendet_grund liest genau diese Felder).
    r = await db.users.update_one(
        {"id": u["id"], **sitzungs_bedingung(u)},
        {"$set": {"current_session_id": sid, "current_session_seit": now_iso(),
                  "current_session_geraet": geraet_kurz(request)[:60],
                  "current_session_ip": ip}})
    if r.matched_count == 0:
        raise HTTPException(401, SITZUNG_UNGUELTIG)
    await bekannte_ip_merken(db, "users", u["id"], ip)
    # RP-557: dieses Geraet ab jetzt als bekannt merken (HMAC am Konto); der
    # Schluessel geht in der Antwort zurueck, die Kaeufer-App legt ihn ab.
    gid = await bekanntes_geraet_merken(db, "users", u["id"], body.geraet_id)
    # Pruefung 14.09.2026 (Liste 1, Nr. 18): dieselbe Anmeldespur wie /auth/login.
    await log_activity_sicher("", u["id"], "auth.login",
                              meta={"ip": ip, "weg": "kaeufer",
                                    "geraet": (request.headers.get("user-agent") or "")[:200]})
    antwort = {"ok": True, "token": create_token(u["id"], sid),
               "user": _buyer_public(u)}
    if gid:
        antwort["geraet_id"] = gid
    return antwort


def _buyer_public(u: dict) -> dict:
    return {"id": u["id"], "email": u.get("email"),
            "kontonummer": u.get("kontonummer"),
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
    status = _access_status(user)
    # Rollenprüfung 22.09.2026 (RP-098 Nr. 4 / RP-348): ein vom Betreiber
    # GESPERRTER Kaeufer sah die Bezahlseite und konnte hier eine neue
    # Freischaltung anfragen — die Anfrage landete in den Freischaltungen, als
    # waere nichts gewesen. Jetzt 409 mit dem Sperrtext.
    if status.get("gesperrt"):
        raise HTTPException(409, GESPERRT_MELDUNG)
    # RP-509: Ein aktiver, bezahlter Zugang liess sich nicht vorab verlaengern
    # ("bereits aktiv" bis zum letzten Tag; der Umweg ueber Sperren vernichtete
    # die Restlaufzeit). Jetzt ist die Anfrage ab VERLAENGERN_AB_TAGEN vor dem
    # Ablauf eine Verlaengerungsanfrage; die Freischaltung rechnet ab dem
    # bisherigen Ablauf weiter (admin._restlaufzeit_basis). Im Kostenlos-Modus
    # (kein Ablauf) bleibt es bei "bereits aktiv".
    verlaengerung = False
    if status["active"]:
        ablauf = _ablauf_parsen(status.get("expires_at"))
        if (ablauf is None or ablauf - datetime.now(timezone.utc)
                > timedelta(days=VERLAENGERN_AB_TAGEN)):
            # Rollenprüfung 22.09.2026 (Review): das Datum in deutscher Zeit —
            # vorher UTC, die Kopfzeile des Marktplatzes zeigt es aber in
            # Ortszeit ("Zugang bis 23.10.") und die Antwort nannte bei einem
            # Ablauf zwischen 22 und 24 Uhr UTC den Vortag. betriebsmeldung.
            # _berlin rechnet ohne Zeitzonendaten nach der EU-Regel von Hand.
            from betriebsmeldung import _berlin   # spaet: nur hier gebraucht
            bis = f" (bis {_berlin(ablauf):%d.%m.%Y})" if ablauf else ""
            return {"ok": True, "hinweis": f"Zugang ist bereits aktiv{bis}.",
                    "verlaengerbar_ab_tagen": VERLAENGERN_AB_TAGEN}
        verlaengerung = True
    # Audit 13.09.2026 (#27): vorher legte jeder Klick eine NEUE offene Anfrage
    # an (auch fuer gesperrte Kaeufer) und konnte die Freischaltungsliste des
    # Betreibers fluten. Jetzt atomarer Upsert wie bei Sucher-Abo und
    # Verkaufspaket; Backstop ist der Teil-Unique-Index
    # uniq_offene_buyer_access_anfrage (indizes.py).
    from routes.team import _offene_anfrage_upsert   # spaet: kein Import-Zyklus
    doc, neu = await _offene_anfrage_upsert(
        {"type": "buyer_access", "buyer_user_id": user["id"], "status": "offen"},
        {"id": str(uuid.uuid4()), "created_at": now_iso(),
         "company_name": user.get("company_name", ""),
         # Kontonummer (13.09.2026): fuer den Betreiber (Freischaltungen)
         "kontonummer": user.get("kontonummer"),
         "contact_email": user.get("email", ""),
         "contact_phone": user.get("phone", "")},
        # Beschluss Ahmad 10.09.2026: Marktplatz vorerst 0 € — keine Kosten
        # mehr in Anfragen und Freischaltungen nennen.
        {"wanted": (("Verlängerung " if verlaengerung else "")
                    + ("Marktplatz-Zugang (kostenlos)" if MARKTPLATZ_KOSTENLOS
                       else f"Marktplatz-Zugang ({BUYER_ACCESS_PRICE:.2f} €/Monat)")),
         # RP-509: fuer die Freischaltungen — Verlaengerung eines laufenden Zugangs
         "verlaengerung": verlaengerung,
         "zugang_bis": status.get("expires_at") if verlaengerung else None,
         "updated_at": now_iso()})
    if neu:
        await log_activity_sicher("", user["id"], "marktplatz.zugang.anfrage",
                                  ref=doc["id"], meta={"verlaengerung": verlaengerung})
    return {"ok": True, "request_id": doc["id"], "verlaengerung": verlaengerung,
            "hinweis": ("Verlängerung wurde an den Administrator übermittelt."
                        if verlaengerung else
                        "Anfrage wurde an den Administrator übermittelt.")}


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
    # Rollenprüfung 22.09.2026 (RP-512): km/PS als Text, deutsch gelesen
    # (_ganzzahl_filter) — "150.000" = 150000, Unsinn -> 400 auf Deutsch.
    km_min: Optional[str] = Query(default=None, max_length=20),
    km_max: Optional[str] = Query(default=None, max_length=20),
    ps_min: Optional[str] = Query(default=None, max_length=20),
    ps_max: Optional[str] = Query(default=None, max_length=20),
    sort: Optional[str] = None, dealer: Optional[str] = None,
    nur_favoriten: Optional[int] = 0,
    page: int = Query(1, ge=1, le=1000), limit: int = 300,
    response: Response = None,
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
    # RP-512: vor jeder Datenbankabfrage lesen (400 statt halber Arbeit).
    km_min = _ganzzahl_filter(km_min, "Kilometer von")
    km_max = _ganzzahl_filter(km_max, "Kilometer bis")
    ps_min = _ganzzahl_filter(ps_min, "PS von")
    ps_max = _ganzzahl_filter(ps_max, "PS bis")

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
        # RP-528/RP-529: tolerant (Trennzeichen, Akzente) und mit Wortgrenzen.
        muster = _modell_regex(model) or re.escape(model.strip())
        rx = {"$regex": muster, "$options": "i"}
        match["$and"].append({"$or": [{"data.model_label": rx},
                                      {"data.model_description": rx}]})
    if fuel and fuel.strip():
        # RP-506: Code-Gruppen statt Teilstring der Beschriftung.
        match["$and"].append(_kraftstoff_bedingung(fuel))
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
        # zaehlt nicht — exakt wie _preisstufe es beim Anzeigen haelt.
        # Rollenprüfung 22.09.2026 (RP-521): der NIEDRIGSTE zulaessige Preis
        # ($min ueberspringt null) statt der ersten gesetzten Stufe —
        # dieselbe Regel wie _preisstufe, sonst filterte/sortierte die Liste
        # nach einem anderen Preis als dem angezeigten.
        stufen: List[Dict[str, Any]] = [
            {"$cond": [{"$and": [{"$in": ["$dealer_id", my_networks]},
                                 {"$gt": ["$prices.network", 0]}]},
                       "$prices.network", None]},
        ]
        # Runde 17 (Nr. 381): die B2B-Stufe nur fuer angemeldete
        # Zwischenhaendler — vorher filterte und sortierte ein anonymer
        # Besucher nach dem B2B-Preis (per price_max ablesbar), obwohl die
        # Anzeige ihm den oeffentlichen Preis zeigte.
        if user is not None:
            stufen.append({"$cond": [{"$gt": ["$prices.b2b", 0]}, "$prices.b2b", None]})
        stufen.append({"$cond": [{"$gt": ["$prices.public", 0]}, "$prices.public", None]})
        eff_price = {"$min": stufen}
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
    # Rollenprüfung 22.09.2026 (RP-098 Nr. 9 / RP-348): Die Liste endete
    # still nach 300 Treffern — Fahrzeug 301 sah niemand, und nichts sagte,
    # dass es mehr gibt. Jetzt einen Treffer mehr holen: X-Truncated=1 heisst
    # "es gibt eine weitere Seite" (Marktplatz.jsx laedt sie per page=N+1).
    pipeline.append({"$limit": limit + 1})
    pipeline.append({"$project": {"_id": 0, "_eff_price": 0, "_sortkey": 0}})

    items = await db.resale_listings.aggregate(pipeline).to_list(limit + 1)
    weitere = len(items) > limit
    items = items[:limit]
    if response is not None:
        response.headers["X-Truncated"] = "1" if weitere else "0"

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
async def toggle_favorit(listing_id: str, aktiv: Optional[bool] = None,
                         user=Depends(buyer_nicht_gesperrt)):
    """Fahrzeug merken / Merken aufheben (Toggle). Bewusst ohne Zugangs-Abo-
    Pflicht beim ENTFERNEN; zum Setzen muss der Zugang aktiv und das
    Inserat sichtbar sein (Betreiber-Sperre: gar nichts, Runde 13 C6)."""
    # Audit 13.09.2026 (#18): vorher find_one, dann (nach bis zu drei weiteren
    # Abfragen) insert_one — ein Doppelklick legte zwei Eintraege an, und der
    # naechste Klick loeschte nur einen. Jetzt ZUERST alle Eintraege loeschen
    # (raeumt Altdubletten mit weg); Backstop beim Setzen ist der Unique-Index
    # favorit_je_kaeufer_inserat (indizes.py).
    # Pruefung 14.09.2026 (Liste 1, Nr. 19): mit ?aktiv=true|false wird der
    # ZIELZUSTAND gesetzt — zwei gleichzeitige "aus"-Klicks ergaben beim reinen
    # Toggle sonst wieder "an". Ohne Angabe wie bisher umschalten.
    if aktiv is False:
        await db.buyer_favorites.delete_many(
            {"buyer_user_id": user["id"], "listing_id": listing_id})
        return {"favorit": False}
    if aktiv is None:
        weg = await db.buyer_favorites.delete_many(
            {"buyer_user_id": user["id"], "listing_id": listing_id})
        if weg.deleted_count:
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
    try:
        await db.buyer_favorites.insert_one({
            "id": str(uuid.uuid4()),
            "buyer_user_id": user["id"],
            "listing_id": listing_id,
            "dealer_id": l.get("dealer_id"),
            "created_at": now_iso(),
        })
    except DuplicateKeyError:
        pass          # paralleler Klick hat schon gemerkt — Ergebnis ist dasselbe
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
        # Rollenprüfung 22.09.2026 (RP-098 Nr. 11 / RP-348): vorher 403
        # "privat" — damit liess sich per Dealer-ID oder geratenem Kurznamen
        # pruefen, ob es eine Firma gibt (404 = nein, 403 = ja, privat). Jetzt
        # wie "nicht gefunden" und wie die gesperrte Firma oben.
        raise HTTPException(404, "Händler nicht gefunden")
    # Private Inserate nur für Netzwerk-Mitglieder (und den Händler selbst).
    # Audit 13.09.2026 (#22): der Sichtbarkeitsfilter lief NACH dem 200er-
    # Deckel, und die Fahrzeugzahl kam aus der gedeckelten Liste (250
    # Inserate -> "200"; 180 neuere private -> ein Fremder sah "20" statt 60).
    # Jetzt filtert Mongo vor dem Limit ($ne trifft auch fehlendes Feld, wie
    # bisher "public"), gezaehlt wird ohne Deckel.
    filt: Dict[str, Any] = {"dealer_id": dl["id"], "status": "veroeffentlicht"}
    if not member and dl["id"] != eigene_firma:
        filt["visibility"] = {"$ne": "private"}
    vehicle_count = await db.resale_listings.count_documents(filt)
    listings = await db.resale_listings.find(filt, {"_id": 0}) \
        .sort("published_at", -1).to_list(200)
    abgeschnitten = vehicle_count > len(listings)
    if abgeschnitten:
        log.warning("Haendlerseite %s: %d sichtbare Inserate, Liste auf %d gekuerzt",
                    dl["id"], vehicle_count, len(listings))
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
            "vehicle_count": vehicle_count,
        },
        # Audit 13.09.2026 (#22): Liste gekuerzt (die Fahrzeuge laedt die
        # Oberflaeche ueber /marktplatz/listings?dealer=...).
        "listings_abgeschnitten": abgeschnitten,
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
    offer: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)   # 14.09.2026: kein 0 €
    message: str = Field(default="", max_length=2000)


class InterestAnswerIn(BaseModel):
    action: Literal["akzeptieren", "ablehnen", "gegenangebot"]
    counter_offer: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    message: str = Field(default="", max_length=2000)
    # Rollenprüfung 22.09.2026 (RP-491): Stand, den der Haendler auf dem
    # Bildschirm hatte. Weicht der gespeicherte Stand ab (der Kaeufer hat
    # inzwischen ein neues Angebot geschickt), antwortet der Server 409 statt
    # zu einem nie gesehenen Preis zu reservieren. Optional (aeltere
    # Oberflaechen); "erwarteter_betrag": null heisst "ohne Preisangebot".
    erwarteter_status: Optional[str] = Field(default=None, max_length=40)
    erwarteter_betrag: Optional[float] = Field(default=None, allow_inf_nan=False)


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
    # Audit 13.09.2026 (#19): hoechstens EINE laufende Verhandlung je Kaeufer
    # und Inserat. Vorher entstand bei jedem erneuten Senden (oder per Skript)
    # eine weitere offene Anfrage beim Haendler. Backstop gegen das Rennen ist
    # der Teil-Unique-Index interesse_offen_je_kaeufer (indizes.py).
    laufend_meldung = ("Zu diesem Fahrzeug läuft bereits deine Anfrage – bitte unter "
                       "'Meine Anfragen' antworten")
    if await db.listing_interest.find_one(
            {"listing_id": listing_id, "buyer_user_id": user["id"],
             "status": {"$in": list(INTERESSE_OFFEN)}}, {"_id": 1}):
        raise HTTPException(409, laufend_meldung)
    doc = {
        "id": str(uuid.uuid4()),
        "listing_id": listing_id,
        "dealer_id": l["dealer_id"],
        "listing_title": l.get("title", ""),
        "buyer_user_id": user["id"],
        # Kontonummer (13.09.2026): Ersatzname weder E-Mail noch Kontonummer
        # (die Kaeufernummer ist ein halbes Zugangsdatum und geht nie an Firmen)
        "buyer_name": user.get("company_name") or user.get("contact_name")
                      or "Zwischenhändler",
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
    try:
        await db.listing_interest.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, laufend_meldung)
    # Audit 13.09.2026 (#19): die Anfrage steht schon — eine Wiederholung nach
    # einem Audit-Fehler bekaeme sonst 409 statt der Bestaetigung.
    await log_activity_sicher(l["dealer_id"], user["id"], "interesse.gesendet",
                              ref=listing_id, meta={"angebot": body.offer})
    return {"ok": True, "interest_id": doc["id"]}


# Audit 13.09.2026 (#20/#21): Obergrenzen der Anfragelisten
ANFRAGEN_MAX = 2000
ERLEDIGTE_ANFRAGEN_MAX = 200


async def _interessen_laden(filt: Dict[str, Any], response: Response,
                            gefiltert: bool) -> list:
    """Audit 13.09.2026 (#20/#21): vorher die neuesten 200 Anfragen ueber ALLE
    Stati. Abgeschlossene bleiben 180 Tage stehen — eine aeltere, noch
    laufende Verhandlung (Kaeufer oder Haendler am Zug) fiel still aus der
    Standardansicht. Ohne Filter jetzt: alle laufenden (bis ANFRAGEN_MAX)
    plus die neuesten ERLEDIGTE_ANFRAGEN_MAX abgeschlossenen, zusammen nach
    created_at sortiert. Mit Filter: bis ANFRAGEN_MAX. Fehlt etwas, meldet
    X-Truncated=1 den Abschnitt (die Status-Tabs zeigen den Rest). Die
    Antwort bleibt eine Liste."""
    if gefiltert:
        items = await db.listing_interest.find(filt, {"_id": 0}) \
            .sort("created_at", -1).to_list(ANFRAGEN_MAX + 1)
        abgeschnitten = len(items) > ANFRAGEN_MAX
        if abgeschnitten:
            log.warning("Anfrageliste %s: mehr als %d Treffer — gekuerzt", filt, ANFRAGEN_MAX)
        items = items[:ANFRAGEN_MAX]
    else:
        offen = list(INTERESSE_OFFEN)
        laufend = await db.listing_interest.find(
            {**filt, "status": {"$in": offen}}, {"_id": 0}) \
            .sort("created_at", -1).to_list(ANFRAGEN_MAX + 1)
        erledigt = await db.listing_interest.find(
            {**filt, "status": {"$nin": offen}}, {"_id": 0}) \
            .sort("created_at", -1).to_list(ERLEDIGTE_ANFRAGEN_MAX + 1)
        if len(laufend) > ANFRAGEN_MAX:
            log.warning("Anfrageliste %s: mehr als %d laufende Anfragen — gekuerzt",
                        filt, ANFRAGEN_MAX)
        # Mehr als 200 abgeschlossene sind der Normalfall grosser Firmen:
        # nur per Kopfzeile melden, nicht ins Protokoll.
        abgeschnitten = (len(laufend) > ANFRAGEN_MAX
                         or len(erledigt) > ERLEDIGTE_ANFRAGEN_MAX)
        # Nachbesserung (#20): X-Truncated ist hier fast immer 1 (erledigte);
        # ob LAUFENDE fehlen, meldet eine eigene Kopfzeile.
        response.headers["X-Truncated-Laufend"] = "1" if len(laufend) > ANFRAGEN_MAX else "0"
        items = laufend[:ANFRAGEN_MAX] + erledigt[:ERLEDIGTE_ANFRAGEN_MAX]
        items.sort(key=lambda i: i.get("created_at") or "", reverse=True)
    response.headers["X-Truncated"] = "1" if abgeschnitten else "0"
    await _inseratsstand_anhaengen(items)
    return items


async def _inseratsstand_anhaengen(items: list) -> None:
    """Rollenprüfung 22.09.2026 (RP-478): Ist das Auto fuer einen ANDEREN
    Kaeufer reserviert, liefen die uebrigen Anfragen dazu scheinbar weiter —
    jede Aktion gab 409, und Kaeufer B erfuhr nichts. Statt eines neuen
    Status (Index, Listen, Wiedereroeffnen beim Aufheben) wird der Zustand
    beim Lesen abgeleitet: inserat_status und anderweitig_reserviert. Wird
    die Reservierung aufgehoben, sind die Anfragen automatisch wieder frei;
    beim Verkauf schliesst resale._anfragen_nach_wechsel sie wie bisher.
    Die Oberflaechen zeigen den Hinweis und blenden die Knoepfe aus."""
    ids = list({i.get("listing_id") for i in items if i.get("listing_id")})
    if not ids:
        return
    # RP-519: published_at/wieder_veroeffentlicht_am fuer das Ablaufdatum.
    stand = {l["id"]: l async for l in db.resale_listings.find(
        {"id": {"$in": ids}}, {"_id": 0, "id": 1, "status": 1, "reserved_for": 1,
                               "published_at": 1, "wieder_veroeffentlicht_am": 1})}
    for i in items:
        l = stand.get(i.get("listing_id"))
        if l is None:
            continue
        i["inserat_status"] = l.get("status")
        # Rollenprüfung 22.09.2026 (RP-519): Wann endet das Inserat (und mit
        # ihm die Anfrage)? Haendler (Kaufanfragen) und Kaeufer (Meine
        # Anfragen) sehen dasselbe Datum; None, sobald es nicht mehr
        # veroeffentlicht ist (reserviert, verkauft, zurueckgezogen ...).
        i["laeuft_ab_am"] = _laeuft_ab_am(l)
        # RP-093(1): auch die Reservierung von Hand (ohne reserved_for) ist
        # "anderweitig" — verhandelt wird dort nicht (_inserat_verhandelbar).
        i["anderweitig_reserviert"] = bool(
            i.get("status") in INTERESSE_OFFEN and l.get("status") == "reserviert"
            and l.get("reserved_for") != i.get("buyer_user_id"))


@router.get("/dealer/interessen")  # noqa: E302
async def dealer_list_interests(response: Response,
                                status: Optional[str] = None,
                                listing_id: Optional[str] = None,
                                user=Depends(current_haendler)):
    """Kaufanfragen der Firma — optional nach Status und/oder Inserat
    gefiltert (listing_id: Review 09/2026, fuer die Anzeige je Inserat)."""
    q: Dict[str, Any] = {"dealer_id": user["dealer_id"]}
    if status:
        q["status"] = status
    if listing_id:
        q["listing_id"] = listing_id
    return await _interessen_laden(q, response, gefiltert=bool(status or listing_id))


#: Anfragen, bei denen der HAENDLER am Zug ist (neu bzw. Kaeufer hat nachverhandelt).
INTERESSE_HAENDLER_AM_ZUG = ("offen", "gegenangebot_kaeufer")


@router.get("/dealer/interessen/anzahl")
async def dealer_interessen_anzahl(user=Depends(current_haendler)):
    """Rollenprüfung 22.09.2026 (RP-472): Der Chef erfuhr von neuen Anfragen
    und Kaeufer-Gegenangeboten nur, wenn er "Kaufanfragen" selbst oeffnete.
    Das Menue (lib/anfragenZaehler.js) holte dafuer bisher zwei komplette
    Listen je Minute (samt Inseratsstand) — hier nur zwei Zaehlungen.
    Mail an die Firma: nicht gebaut (Entscheidung Ahmad offen)."""
    zahlen = {}
    for status in INTERESSE_HAENDLER_AM_ZUG:
        zahlen[status] = await db.listing_interest.count_documents(
            {"dealer_id": user["dealer_id"], "status": status})
    return {"anzahl": sum(zahlen.values()), **zahlen}


@router.get("/buyer/interessen")
async def buyer_interests(response: Response, user=Depends(buyer_nicht_gesperrt)):
    # Audit 13.09.2026 (#21): laufende Verhandlungen nicht mehr verdraengt
    items = await _interessen_laden({"buyer_user_id": user["id"]}, response,
                                    gefiltert=False)
    await _haendler_und_inserat_anhaengen(items, user)
    return items


async def _haendler_und_inserat_anhaengen(items: list, user: dict) -> None:
    """Rollenprüfung 22.09.2026 (RP-503): Das Anfrage-Dokument kennt nur
    dealer_id und den Inseratstitel von damals. Ein reservierter Kaeufer
    wusste weder, bei WELCHER Firma er reserviert hatte, noch wie er sie
    erreicht — das reservierte Inserat faellt aus der Marktplatzliste.
    Jetzt je Anfrage:
      * haendler: Firmenname und Kurzname (immer — mit wem man verhandelt)
      * haendler.kontakt (Telefon, E-Mail, Ort, Ansprechpartner): bei einer
        angenommenen Anfrage (Reservierung) — dann muss er anrufen koennen
      * inserat (aktueller Titel, Marke/Modell, erstes Bild): nur solange er
        das Inserat sehen darf oder es fuer ihn reserviert/an ihn verkauft ist
    Gesperrte Firmen liefern nichts (wie ueberall im Marktplatz). Alles in
    wenigen Abfragen, nicht je Anfrage."""
    if not items:
        return
    dealer_ids = list({i.get("dealer_id") for i in items if i.get("dealer_id")})
    listing_ids = list({i.get("listing_id") for i in items if i.get("listing_id")})
    gesperrt = await gesperrte_firmen_ids()
    firmen = {d["id"]: d async for d in db.dealers.find(
        {"id": {"$in": dealer_ids}},
        {"_id": 0, "id": 1, "company_name": 1, "city": 1, "phone": 1,
         "whatsapp_number": 1, "email": 1, "contact_person": 1, "marketplace": 1})}
    inserate = {l["id"]: l async for l in db.resale_listings.find(
        {"id": {"$in": listing_ids}},
        {"_id": 0, "id": 1, "dealer_id": 1, "title": 1, "status": 1, "visibility": 1,
         "reserved_for": 1, "sold_to_user_id": 1, "photos": 1,
         "data.make_label": 1, "data.model_label": 1})}
    my_networks, visible_dealers = await _sichtbare_haendler(user)
    for i in items:
        d = firmen.get(i.get("dealer_id"))
        if d and d["id"] not in gesperrt:
            mp = d.get("marketplace") or {}
            haendler: Dict[str, Any] = {
                "company_name": d.get("company_name", ""),
                "slug": mp.get("slug") or _slugify(d.get("company_name", ""), d["id"]),
            }
            if i.get("status") == "akzeptiert":
                haendler["kontakt"] = {
                    "phone": d.get("phone") or d.get("whatsapp_number") or "",
                    "email": d.get("email") or "",
                    "city": d.get("city") or "",
                    "contact_person": d.get("contact_person") or "",
                }
            i["haendler"] = haendler
        l = inserate.get(i.get("listing_id"))
        if not l or (d and d["id"] in gesperrt):
            continue
        fuer_ihn = user["id"] in (l.get("reserved_for"), l.get("sold_to_user_id"))
        sichtbar = (l.get("status") == "veroeffentlicht"
                    and l.get("dealer_id") in visible_dealers
                    and ((l.get("visibility") or "") != "private"
                         or l.get("dealer_id") in my_networks))
        if fuer_ihn or sichtbar:
            daten = l.get("data") or {}
            i["inserat"] = {"title": l.get("title") or i.get("listing_title") or "",
                            "make_label": daten.get("make_label") or "",
                            "model_label": daten.get("model_label") or "",
                            "foto": _erstes_foto(l)}


@router.get("/buyer/interessen/zaehler")
async def buyer_interessen_zaehler(seit: Optional[str] = Query(default=None, max_length=64),
                                   user=Depends(buyer_nicht_gesperrt)):
    """Rollenprüfung 22.09.2026 (RP-520): Der Kaeufer erfuhr nie von einem
    Gegenangebot, einer Annahme oder Ablehnung — es gab keinen Hinweis im
    Marktplatz. Zaehler fuer den Knopf "Meine Anfragen":
      * am_zug: Gegenangebote des Haendlers, auf die er antworten soll
      * neu: seit `seit` (Zeitpunkt, zu dem er "Meine Anfragen" zuletzt
        geoeffnet hat) vom Haendler oder vom System geaenderte Anfragen —
        OHNE die Gegenangebote (die stecken schon in am_zug; die Summe
        zaehlt nichts doppelt)
    Leichtgewichtig (zwei count-Abfragen), damit die Seite ihn regelmaessig
    abfragen kann."""
    am_zug = await db.listing_interest.count_documents(
        {"buyer_user_id": user["id"], "status": "gegenangebot"})
    neu = 0
    ab = _ablauf_parsen(seit) if seit else None
    if ab is not None:
        neu = await db.listing_interest.count_documents({
            "buyer_user_id": user["id"],
            "status": {"$ne": "gegenangebot"},
            "updated_at": {"$gt": ab.astimezone(timezone.utc).isoformat()},
            # zuletzt hat NICHT der Kaeufer selbst gehandelt
            "$expr": {"$ne": [{"$arrayElemAt": ["$history.von", -1]}, "kaeufer"]},
        })
    return {"am_zug": am_zug, "neu": neu}


class BuyerInterestAnswerIn(BaseModel):
    # 09/2026: der Kaeufer kann jetzt auch selbst ein Gegenangebot machen
    # Rollenprüfung 22.09.2026 (RP-502): und eine laufende Anfrage
    # zurueckziehen (in jedem laufenden Status).
    action: Literal["annehmen", "ablehnen", "gegenangebot", "zurueckziehen"]
    # Runde 17 (Nr. 397): kein inf/nan als Gegenangebot.
    counter_offer: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    message: str = Field(default="", max_length=2000)
    # Rollenprüfung 22.09.2026 (RP-495): das Gegenangebot, das der Kaeufer auf
    # dem Bildschirm hatte. Hat der Haendler es inzwischen geaendert, nimmt
    # "annehmen" nicht still den neuen Betrag an (409). Optional (alte Oberflaeche).
    erwarteter_betrag: Optional[float] = Field(default=None, allow_inf_nan=False)


# Verhandlungszustaende (beide Seiten): offen -> gegenangebot (Haendler)
# -> gegenangebot_kaeufer (Kaeufer) -> ... -> akzeptiert | abgelehnt
INTERESSE_OFFEN = ("offen", "gegenangebot", "gegenangebot_kaeufer")


def _betrag_gleich(a: Any, b: Any) -> bool:
    """Zwei Betraege auf den Cent gleich (None nur gleich None)."""
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(round(float(a), 2) - round(float(b), 2)) < 0.005
    except (TypeError, ValueError):
        return False


def _eur(betrag: Any) -> str:
    """Deutscher Betrag fuer Meldungen: 12.500 € / 12.500,50 €."""
    if betrag is None:
        return "ohne Preisangebot"
    try:
        wert = round(float(betrag), 2)
    except (TypeError, ValueError):
        return str(betrag)
    text = f"{wert:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if text.endswith(",00"):
        text = text[:-3]
    return f"{text} €"


async def _kaeufer_zieht_zurueck(it: dict, body: "BuyerInterestAnswerIn", user: dict) -> dict:
    """Rollenprüfung 22.09.2026 (RP-502): Der Kaeufer beendet eine LAUFENDE
    Anfrage selbst (vorher gab es dafuer keinen Weg: Ablehnen ging nur bei
    einem Haendler-Gegenangebot, ein neues Senden gab 409). Status abgelehnt
    mit beendet_grund 'kaeufer_zurueckgezogen' — wie die anderen Abschluesse,
    damit Listen, Filter und der Teil-Unique-Index unveraendert bleiben.
    Eine akzeptierte Anfrage (Reservierung) zieht er hier nicht zurueck: das
    klaert er mit dem Haendler, der die Reservierung aufhebt."""
    if it.get("status") not in INTERESSE_OFFEN:
        raise HTTPException(400, "Die Anfrage ist bereits abgeschlossen — "
                                 "zurückziehen geht nur bei laufenden Anfragen")
    jetzt = now_iso()
    upd = await db.listing_interest.update_one(
        {"id": it["id"], "buyer_user_id": user["id"], "status": it.get("status")},
        {"$set": {"status": "abgelehnt", "beendet_grund": "kaeufer_zurueckgezogen",
                  "updated_at": jetzt},
         "$push": {"history": {"von": "kaeufer", "aktion": "zurueckgezogen",
                               "angebot": None, "nachricht": body.message,
                               "zeit": jetzt}}})
    if upd.modified_count == 0:
        raise HTTPException(409, "Die Anfrage wurde gerade anderweitig "
                                 "beantwortet — bitte neu laden.")
    await log_activity_sicher(it.get("dealer_id", ""), user["id"],
                              "interesse.kaeufer.zurueckgezogen", ref=it["id"],
                              meta={"listing_id": it.get("listing_id")})
    return {"ok": True, "status": "abgelehnt", "beendet_grund": "kaeufer_zurueckgezogen"}


# Runde 13: C1 — vorher hing nur die ERSTE Anfrage (send_interest) am
# Marktplatz-Zugang; ein gesperrter/abgelaufener Zwischenhaendler konnte bei
# MARKTPLATZ_KOSTENLOS=false eine laufende Verhandlung weiterfuehren und per
# "annehmen" ein Fahrzeug reservieren. Jetzt: 403 gesperrt / 402 ohne
# aktiven Zugang — fuer alle drei Aktionen (auch "ablehnen" schreibt eine
# Nachricht in die Historie beim Haendler).
# Rollenprüfung 22.09.2026 (RP-510): Die Route haengt jetzt an
# buyer_nicht_gesperrt (Betreiber-Sperre -> 403 fuer ALLES wie bisher); der
# Zugang (402) wird nur noch fuer annehmen und gegenangebot verlangt
# (_zugang_erzwingen inline). Ablehnen und Zurueckziehen BEENDEN nur — nach
# Ablauf des Zugangs konnte der Kaeufer seine laufenden Anfragen vorher weder
# sehen noch loswerden, der Haendler wartete auf eine Antwort, die nie kam.
@router.post("/interessen/{interest_id}/kaeufer-antwort")
async def buyer_answer_interest(interest_id: str, body: BuyerInterestAnswerIn,
                                user=Depends(buyer_nicht_gesperrt)):
    """Kaeufer reagiert auf ein GEGENANGEBOT des Haendlers (Review 09/2026:
    der Kaeufer sah Gegenangebote, konnte aber nicht antworten).
    annehmen: Inserat wird atomar fuer den Kaeufer reserviert, Status
    'akzeptiert'. ablehnen: Status 'abgelehnt'. Beides nur einmal —
    der Statuswechsel selbst ist atomar gegen parallele Antworten.
    gegenangebot: eigenes (neues) Angebot — RP-502 auch, um das eigene
    Angebot zu aendern. zurueckziehen (RP-502): laufende Anfrage beenden."""
    beenden = body.action in ("ablehnen", "zurueckziehen")
    if not beenden:
        _zugang_erzwingen(user)
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
    # RP-510: Beenden (ablehnen/zurueckziehen) fuehrt nichts fort und
    # reserviert nichts — das darf der Kaeufer auch ohne Sicht auf das Inserat.
    l = await db.resale_listings.find_one(
        {"id": it["listing_id"]},
        {"_id": 0, "dealer_id": 1, "visibility": 1, "status": 1, "reserved_for": 1,
         "reserviert_manuell": 1})
    if not beenden and (not l or not await _inserat_sichtbar_fuer(user, l)):
        raise HTTPException(403, "Kein Zugang mehr zu diesem Inserat — die "
                                 "Anfrage kann nicht weitergeführt werden")
    if body.action == "zurueckziehen":
        return await _kaeufer_zieht_zurueck(it, body, user)
    if body.action == "gegenangebot" and not _inserat_verhandelbar(l, user["id"]):
        # Pruefung 14.09.2026 (D14): wie beim Haendler — kein Gegenangebot auf
        # ein nicht mehr verfuegbares Inserat.
        raise HTTPException(409, "Das Inserat ist nicht mehr verfügbar — ein "
                                 "Gegenangebot ist nicht mehr möglich")
    if body.action == "gegenangebot":
        # Kaeufer macht (erneut) ein Angebot — solange nichts abgeschlossen ist.
        # RP-502: auch aus 'gegenangebot_kaeufer' — damit aendert der Kaeufer
        # sein eigenes, noch unbeantwortetes Angebot (vorher 400, ein neues
        # Senden gab 409, und es blieb nur, auf den Haendler zu warten).
        if it.get("status") not in INTERESSE_OFFEN:
            raise HTTPException(400, "Ein Gegenangebot ist jetzt nicht moeglich "
                                     f"(Status '{it.get('status')}')")
        if body.counter_offer is None or float(body.counter_offer) <= 0:
            raise HTTPException(400, "Gegenangebot benoetigt einen Betrag")
        betrag = round(float(body.counter_offer), 2)
        upd = await db.listing_interest.update_one(
            # RP-491: Betrag mit festnageln — nimmt der Haendler PARALLEL das
            # bisherige Kaeufer-Angebot an, gewinnt genau einer von beiden.
            {"id": interest_id, "buyer_user_id": user["id"],
             "status": it.get("status"),
             "buyer_counter_offer": it.get("buyer_counter_offer")},
            {"$set": {"status": "gegenangebot_kaeufer",
                      "buyer_counter_offer": betrag, "updated_at": now_iso()},
             "$push": {"history": {"von": "kaeufer", "aktion": "gegenangebot",
                                   "angebot": betrag, "nachricht": body.message,
                                   "zeit": now_iso()}}})
        if upd.modified_count == 0:
            raise HTTPException(409, "Die Anfrage wurde gerade anderweitig "
                                     "beantwortet — bitte neu laden.")
        await log_activity_sicher(it.get("dealer_id", ""), user["id"],
                           "interesse.kaeufer.gegenangebot", ref=interest_id,
                           meta={"listing_id": it.get("listing_id"), "betrag": betrag})
        return {"ok": True, "status": "gegenangebot_kaeufer", "betrag": betrag}
    if it.get("status") != "gegenangebot":
        raise HTTPException(400, "Nur ein Gegenangebot des Haendlers kann "
                                 "angenommen oder abgelehnt werden")
    # RP-495: hat der Haendler sein Gegenangebot geaendert, seit der Kaeufer
    # die Liste geladen hat, wird NICHT still der neue Betrag angenommen.
    if (body.action == "annehmen" and body.erwarteter_betrag is not None
            and not _betrag_gleich(it.get("counter_offer"), body.erwarteter_betrag)):
        raise HTTPException(409, "Der Händler hat sein Angebot inzwischen auf "
                                 f"{_eur(it.get('counter_offer'))} geändert — bitte "
                                 "neu laden und prüfen.")
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
    try:
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
    except Exception:
        # Pruefung 14.09.2026 (Liste 1, Nr. 20): wirft der zweite Write, bleibt
        # das Auto nicht reserviert.
        if reserviert:
            await reservierung_zurueckgeben(it["listing_id"], user["id"])
        raise
    if upd.modified_count == 0:
        # Paralleler Statuswechsel (z.B. Haendler hat gerade geantwortet):
        # Reservierung zurueckgeben und ehrlich ablehnen.
        if reserviert:
            await reservierung_zurueckgeben(it["listing_id"], user["id"])
        raise HTTPException(409, "Die Anfrage wurde gerade anderweitig "
                                 "beantwortet — bitte neu laden.")
    if reserviert:
        await fahrzeug_reserviert_markieren(it["listing_id"])
    # Rollenprüfung 22.09.2026 (RP-098 Nr. 6 / RP-348): mit der Firma des
    # Inserats — vorher dealer_id "", der Eintrag fehlte im Firmenprotokoll.
    await log_activity_sicher(it.get("dealer_id", ""), user["id"], f"interesse.kaeufer.{body.action}",
                       ref=interest_id,
                       meta={"listing_id": it.get("listing_id"),
                             "betrag": it.get("counter_offer")})
    return {"ok": True, "status": neuer_status}


def _inserat_verhandelbar(l: dict, buyer_user_id) -> bool:
    """Pruefung 14.09.2026 (D13/D14): verhandelt wird nur auf einem
    veroeffentlichten Inserat — oder auf einem, das fuer GENAU diesen Kaeufer
    reserviert ist.

    Rollenprüfung 22.09.2026 (RP-093(1)/RP-343(a), zweite Absicherung): Eine
    Reservierung von Hand (Kaeufer am Telefon, reserviert_manuell, ohne
    reserved_for) liess vorher JEDEN Kaeufer weiterverhandeln (reserved_for
    None galt als "fuer ihn"). resale beendet die offenen Anfragen dabei
    inzwischen selbst; hier gilt zusaetzlich: ohne reserved_for oder bei
    Hand-Reservierung wird nicht verhandelt."""
    status = l.get("status")
    if status == "veroeffentlicht":
        return True
    if status == "reserviert":
        if l.get("reserviert_manuell") or l.get("reserved_for") is None:
            return False
        return l.get("reserved_for") == buyer_user_id
    return False


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
    # Rollenprüfung 22.09.2026 (RP-098 Nr. 5 / RP-348): vorher nur "gesperrt" —
    # im Bezahlmodus (MARKTPLATZ_KOSTENLOS=false) konnte der Haendler fuer einen
    # Kaeufer mit ABGELAUFENEM Zugang reservieren, der selbst weder annehmen
    # noch ein Gegenangebot schicken durfte (402). Im Kostenlos-Modus ist jeder
    # nicht gesperrte Kaeufer aktiv — dort aendert sich nichts.
    if not _access_status(k).get("active"):
        raise HTTPException(409, "Der Marktplatz-Zugang des Käufers ist abgelaufen — "
                                 "die Anfrage kann erst nach seiner Verlängerung "
                                 "weitergeführt werden")
    l = await db.resale_listings.find_one(
        {"id": it.get("listing_id")},
        {"_id": 0, "dealer_id": 1, "visibility": 1, "status": 1, "reserved_for": 1,
         "reserviert_manuell": 1})
    if l and not _inserat_verhandelbar(l, it.get("buyer_user_id")):
        # Pruefung 14.09.2026 (D13): kein Gegenangebot auf ein Inserat, das
        # inzwischen verkauft, zurueckgezogen oder fuer jemand anderen reserviert ist.
        raise HTTPException(409, "Das Inserat ist nicht mehr verfügbar — die Anfrage "
                                 "kann nicht weitergeführt werden")
    if not l or not await _inserat_sichtbar_fuer(k, l):
        raise HTTPException(409, "Der Käufer hat keinen Zugang mehr zu diesem "
                                 "Inserat — die Anfrage kann nicht "
                                 "weitergeführt werden")


def _vereinbarter_preis(it: dict) -> Optional[float]:
    """Preis, zu dem "Akzeptieren" abschliesst: Kaeufer-Gegenangebot, sonst
    das urspruengliche Angebot."""
    if it.get("status") == "gegenangebot_kaeufer":
        return it.get("buyer_counter_offer")
    return it.get("offer")


async def _inseratspreis_fuer_kaeufer(it: dict) -> tuple:
    """RP-093(4): (Preis, Stufe) des Inserats aus Sicht des Kaeufers dieser
    Anfrage — angemeldeter Zwischenhaendler (B2B-Stufe), Netzwerkpreis nur
    als Mitglied der Firma. (None, None) ohne gesetzten Preis."""
    l = await db.resale_listings.find_one(
        {"id": it.get("listing_id")}, {"_id": 0, "prices": 1, "dealer_id": 1})
    if not l:
        return None, None
    mitglied = await _is_network_member(l.get("dealer_id"), it.get("buyer_user_id"))
    preis, stufe = _preisstufe(l, is_member=mitglied, is_trade=True)
    if not _preis_gesetzt(preis):
        return None, None
    return round(float(preis), 2), stufe


def _stand_geaendert_text(it: dict) -> str:
    """RP-491: 409-Text, wenn sich die Anfrage seit dem Laden geaendert hat."""
    st = it.get("status")
    if st == "gegenangebot_kaeufer":
        return (f"Der Käufer hat inzwischen {_eur(it.get('buyer_counter_offer'))} "
                "angeboten — bitte neu laden und prüfen.")
    if st == "gegenangebot":
        return (f"Dein Gegenangebot über {_eur(it.get('counter_offer'))} liegt "
                "inzwischen beim Käufer — bitte neu laden.")
    if st == "offen":
        return (f"Die Anfrage steht inzwischen auf „offen“ ({_eur(it.get('offer'))}) "
                "— bitte neu laden und prüfen.")
    return "Die Anfrage hat sich inzwischen geändert — bitte neu laden."


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
    # Rollenprüfung 22.09.2026 (RP-491): veraltete Kaufanfragen-Seite. Der
    # Haendler sah z.B. "offen, 20.000 €", der Kaeufer hatte inzwischen
    # 18.000 € geboten — "Akzeptieren" reservierte zu 18.000 €, einem Preis,
    # den der Haendler nie gesehen hatte. Schickt die Oberflaeche ihren Stand
    # mit, gilt die Antwort nur fuer genau diesen Stand.
    if body.erwarteter_status is not None and body.erwarteter_status != it["status"]:
        raise HTTPException(409, _stand_geaendert_text(it))
    if body.action == "akzeptieren" and "erwarteter_betrag" in body.model_fields_set:
        if not _betrag_gleich(_vereinbarter_preis(it), body.erwarteter_betrag):
            raise HTTPException(409, _stand_geaendert_text(it))
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
        vereinbart = _vereinbarter_preis(it)
        if vereinbart is None:
            # Rollenprüfung 22.09.2026 (RP-093(4)/RP-343(d)): Eine Anfrage OHNE
            # Preisangebot ("Interesse zum angegebenen Preis") wurde mit
            # agreed_price None angenommen — Reservierung, Verkauf melden und
            # Kaufvertrag hatten keinen Betrag. Jetzt gilt der Inseratspreis,
            # den GENAU dieser Kaeufer sieht (Netzwerk/B2B/oeffentlich, der
            # niedrigste zulaessige wie in der Liste). Ohne jeden Preis ("auf
            # Anfrage") wird nicht blind angenommen.
            vereinbart, stufe = await _inseratspreis_fuer_kaeufer(it)
            if vereinbart is None:
                raise HTTPException(400, "Der Käufer hat keinen Betrag genannt und das "
                                         "Inserat hat keinen Preis — bitte ein "
                                         "Gegenangebot mit Preis senden.")
            update["agreed_price_quelle"] = f"inserat_{stufe}"
        update["agreed_price"] = vereinbart
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
    # RP-491: zusaetzlich die Betraege des gelesenen Stands festnageln — seit
    # RP-502 kann der Kaeufer sein Angebot im Status 'gegenangebot_kaeufer'
    # aendern, ohne dass sich der Status aendert.
    # RP-477: beim Akzeptieren steht der VEREINBARTE Preis in der Historie
    # (vorher body.counter_offer, also leer).
    angebot_historie = (update.get("agreed_price") if body.action == "akzeptieren"
                        else update.get("counter_offer"))
    try:
        upd = await db.listing_interest.update_one(
            {"id": interest_id, "dealer_id": user["dealer_id"],
             "status": it["status"], "offer": it.get("offer"),
             "buyer_counter_offer": it.get("buyer_counter_offer")},
            {"$set": update,
             "$push": {"history": {"von": "haendler", "aktion": body.action,
                                   "angebot": angebot_historie,
                                   "nachricht": body.message, "zeit": now_iso()}}})
    except Exception:
        # Pruefung 14.09.2026 (Liste 1, Nr. 20): Ausnahme im zweiten Write —
        # Reservierung zurueck, sonst bliebe das Auto blockiert.
        if body.action == "akzeptieren":
            await reservierung_zurueckgeben(it["listing_id"], it["buyer_user_id"])
        raise
    if upd.modified_count == 0:
        if body.action == "akzeptieren":
            # Die eben gezogene Reservierung wieder freigeben.
            await reservierung_zurueckgeben(it["listing_id"], it["buyer_user_id"])
        raise HTTPException(409, "Die Anfrage wurde gerade anderweitig "
                                 "beantwortet — bitte neu laden.")
    if body.action == "akzeptieren":
        await fahrzeug_reserviert_markieren(it["listing_id"])
    await log_activity_sicher(user["dealer_id"], user["id"], f"interesse.{new_status}",
                       ref=interest_id)
    return {"ok": True, "status": new_status}
