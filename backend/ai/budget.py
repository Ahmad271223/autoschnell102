# -*- coding: utf-8 -*-
"""Kostenbremse fuer die KI (Wunsch Ahmad 26.09.2026): jeder Nutzer darf
hoechstens KI_BUDGET_MONAT_EUR (15) im Monat verursachen, ein einzelner Lauf
hoechstens KI_KOSTEN_MAX_CT (15 ct).

Beim Vertrag zaehlt das Konto des Suchers (user_id), bei der Abholung die
Firma (dealer_id — der Chef loest die Bewertung aus). Ab 80 % des Monats-
budgets oder nach einem Lauf ueber der Einzelgrenze schaltet die naechste
Bewertung in den Sparmodus (keine Websuche je Fall, nur eigene Daten und
Markttabelle); ist das Budget aufgebraucht, gibt es bis Monatsanfang keine
KI-Bewertung mehr (Status "budget"), der Vertrag/die Freigabe laufen normal.

Wunsch Ahmad 26.09.2026 abends: bei der Abholung gilt ZUSAETZLICH ein
Deckel je Fahrer (KI_BUDGET_FAHRER_EUR, 10) — Schluessel
"fahrer:<driver_id>:<JJJJ-MM>". Reserviert wird nur, wenn Firma UND Fahrer
noch frei sind; abgerechnet wird auf beide Zaehler; der Sparmodus greift,
sobald einer der beiden Deckel zu 80 % erreicht ist (der engere zuerst).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from deps import db as _db
from konfig import kommazahl_env

SAMMLUNG = "ki_bewertungen"
ZAEHLER = "ki_budget"          # je Konto/Firma und Monat: reservierte + abgerechnete Cent
SPARMODUS_AB = 0.8


def budget_monat_eur() -> float:
    return kommazahl_env("KI_BUDGET_MONAT_EUR", 15.0, unten=0.0, oben=100000.0)


def kosten_max_ct() -> float:
    return kommazahl_env("KI_KOSTEN_MAX_CT", 15.0, unten=0.5, oben=10000.0)


def budget_fahrer_eur() -> float:
    """Deckel je Fahrer und Monat (Abholung), 0 = kein Fahrer-Deckel."""
    return kommazahl_env("KI_BUDGET_FAHRER_EUR", 10.0, unten=0.0, oben=100000.0)


def _monatsanfang() -> str:
    jetzt = datetime.now(timezone.utc)
    return jetzt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


async def verbraucht_ct(*, user_id: Optional[str], dealer_id: Optional[str], art: str, db=None) -> Dict[str, Any]:
    """Summe der Kosten dieses Monats und die Kosten des letzten Laufs."""
    db = db if db is not None else _db
    filt: Dict[str, Any] = {"created_at": {"$gte": _monatsanfang()}, "kosten_ct": {"$gt": 0}}
    if art == "vertrag" and user_id:
        filt["user_id"] = user_id
    else:
        filt["dealer_id"] = dealer_id or ""
    summe, n, letzte = 0.0, 0, 0.0
    async for d in db[SAMMLUNG].find(filt, {"_id": 0, "kosten_ct": 1, "created_at": 1}).sort("created_at", -1).limit(5000):
        try:
            k = float(d.get("kosten_ct") or 0)
        except (TypeError, ValueError):
            k = 0.0
        if n == 0:
            letzte = k
        summe += k
        n += 1
    return {"verbraucht_ct": round(summe, 2), "laeufe": n, "letzter_lauf_ct": round(letzte, 2)}


def _schluessel(user_id: Optional[str], dealer_id: Optional[str], art: str) -> str:
    scope = user_id if (art == "vertrag" and user_id) else (dealer_id or "")
    return f"{art}:{scope}:{_monatsanfang()[:7]}"


def _fahrer_schluessel(driver_id: str) -> str:
    """Zaehler je Fahrer und Monat (Abholung, Wunsch Ahmad 26.09.2026 abends)."""
    return f"fahrer:{driver_id}:{_monatsanfang()[:7]}"


def _fahrer_grenze_ct(art: str, driver_id: Optional[str]) -> float:
    """Fahrer-Deckel in Cent — nur bei der Abholung mit bekanntem Fahrer, 0 = keiner."""
    if art != "abholung" or not driver_id:
        return 0.0
    return round(budget_fahrer_eur() * 100, 2)


async def _zaehler_erhoehen(db, key: str, est: float, grenze: float) -> bool:
    """Einen Zaehler atomar um `est` erhoehen, wenn die Grenze dabei eingehalten
    wird (find_one_and_update mit $expr). True = reserviert."""
    from pymongo import ReturnDocument
    # Zaehler anlegen (idempotent), dann atomar erhoehen — nur wenn die
    # Grenze mit dieser Reservierung noch eingehalten wird ($expr ist in
    # einem Upsert nicht erlaubt, deshalb zwei Schritte).
    await db[ZAEHLER].update_one({"_id": key}, {"$setOnInsert": {"ct": 0.0, "angelegt": datetime.now(timezone.utc).isoformat()}},
                                 upsert=True)
    doc = await db[ZAEHLER].find_one_and_update(
        {"_id": key, "$expr": {"$lte": [{"$add": [{"$ifNull": ["$ct", 0]}, est]}, grenze]}},
        {"$inc": {"ct": est}, "$set": {"stand": datetime.now(timezone.utc).isoformat()}},
        return_document=ReturnDocument.AFTER)
    return doc is not None


async def reservieren(*, user_id: Optional[str], dealer_id: Optional[str], art: str, driver_id: Optional[str] = None,
                      db=None) -> Optional[Dict[str, Any]]:
    """Review 25.09.2026 abends: das Budget wird VOR dem Lauf atomar reserviert
    (Zaehler je Konto/Firma und Monat, ein find_one_and_update mit $expr) —
    zwei gleichzeitige Laeufe koennen die Grenze nicht mehr gemeinsam
    ueberschreiten. Reserviert wird die Einzelgrenze (KI_KOSTEN_MAX_CT),
    `abrechnen` ersetzt sie nach dem Lauf durch die echten Kosten.
    None = Budget voll. Ohne Grenze oder bei DB-Fehler: offen (wie pruefen).

    Abholung mit Fahrer (26.09.2026 abends): erst der Firmen-, dann der
    Fahrer-Zaehler. Ist der Fahrer-Deckel voll, wird die Firmen-Reservierung
    sofort zurueckgenommen — reserviert ist nur, wenn BEIDE frei sind."""
    grenze = round(budget_monat_eur() * 100, 2)
    grenze_fahrer = _fahrer_grenze_ct(art, driver_id)
    est = round(kosten_max_ct(), 2)
    if grenze <= 0 and grenze_fahrer <= 0:
        return {"schluessel": None, "est_ct": 0.0}
    db = db if db is not None else _db
    key = _schluessel(user_id, dealer_id, art)
    key_fahrer = _fahrer_schluessel(driver_id) if grenze_fahrer > 0 else None
    try:
        if grenze > 0 and not await _zaehler_erhoehen(db, key, est, grenze):
            return None                                   # Firmen-/Kontobudget voll
        if key_fahrer:
            if not await _zaehler_erhoehen(db, key_fahrer, est, grenze_fahrer):
                if grenze > 0:                            # Firmen-Reservierung zurueck
                    await db[ZAEHLER].update_one({"_id": key}, {"$inc": {"ct": -est}})
                return None                               # Fahrer-Deckel voll
        return {"schluessel": key if grenze > 0 else None, "fahrer_schluessel": key_fahrer, "est_ct": est}
    except Exception:  # noqa: BLE001 — die Bremse darf die KI nicht abschalten
        return {"schluessel": None, "est_ct": 0.0}


async def abrechnen(reservierung: Optional[Dict[str, Any]], tatsaechlich_ct: float, db=None) -> None:
    """Reservierung durch die echten Kosten ersetzen (0 = freigeben) — auf
    dem Firmen-/Konto-Zaehler UND, falls reserviert, dem Fahrer-Zaehler. Wirft nie."""
    if not reservierung:
        return
    schluessel = [k for k in (reservierung.get("schluessel"), reservierung.get("fahrer_schluessel")) if k]
    if not schluessel:
        return
    db = db if db is not None else _db
    try:
        delta = round(float(tatsaechlich_ct or 0) - float(reservierung.get("est_ct") or 0), 2)
        for key in schluessel:
            await db[ZAEHLER].update_one({"_id": key}, {"$inc": {"ct": delta}})
    except Exception:  # noqa: BLE001
        pass


async def _ct_lesen(db, key: str) -> float:
    d = await db[ZAEHLER].find_one({"_id": key}, {"ct": 1})
    return round(float((d or {}).get("ct") or 0), 2)


async def zaehler_ct(*, user_id: Optional[str], dealer_id: Optional[str], art: str, db=None) -> float:
    db = db if db is not None else _db
    try:
        return await _ct_lesen(db, _schluessel(user_id, dealer_id, art))
    except Exception:  # noqa: BLE001
        return 0.0


async def fahrer_zaehler_ct(driver_id: Optional[str], db=None) -> float:
    """Verbrauch des Fahrers in diesem Monat (Cent). Wirft nie."""
    if not driver_id:
        return 0.0
    db = db if db is not None else _db
    try:
        return await _ct_lesen(db, _fahrer_schluessel(driver_id))
    except Exception:  # noqa: BLE001
        return 0.0


def grund_fahrer_voll(verbraucht_ct: float, grenze_ct: float) -> str:
    return (f"Monatsbudget des Fahrers ({grenze_ct / 100:.0f} €) aufgebraucht ({verbraucht_ct / 100:.2f} €) "
            f"— ab dem 1. des nächsten Monats wieder verfügbar.")


def grund_firma_voll(verbraucht_ct: float, grenze_ct: float) -> str:
    return (f"Monatsbudget für KI-Bewertungen aufgebraucht ({verbraucht_ct / 100:.2f} € von "
            f"{grenze_ct / 100:.2f} €) — ab dem 1. des nächsten Monats wieder verfügbar.")


async def pruefen(*, user_id: Optional[str], dealer_id: Optional[str], art: str, driver_id: Optional[str] = None,
                  db=None) -> Dict[str, Any]:
    """{"erlaubt", "sparmodus", "verbraucht_ct", "grenze_ct", "grund", ...} — wirft nie.

    Review 26.09.2026 (Nr. 22): EINE Wahrheit — der Verbrauch kommt aus dem
    Zaehler (ki_budget), den `reservieren`/`abrechnen` fuehren und den
    `abgleichen` stuendlich mit den Bewertungen abgleicht. Vorher summierte
    pruefen() die Bewertungen, reservieren() den Zaehler — beide konnten
    auseinanderlaufen. Nur der letzte Lauf (Sparmodus) kommt weiter aus
    den Bewertungen.

    Abholung mit Fahrer: zusaetzlich der Fahrer-Deckel (fahrer_verbraucht_ct,
    fahrer_grenze_ct) — erlaubt nur, wenn beide frei sind; der Grund nennt
    den engeren Deckel; Sparmodus ab 80 % eines der beiden."""
    grenze = round(budget_monat_eur() * 100, 2)
    grenze_fahrer = _fahrer_grenze_ct(art, driver_id)
    try:
        verbraucht = await zaehler_ct(user_id=user_id, dealer_id=dealer_id, art=art, db=db)
        v = await verbraucht_ct(user_id=user_id, dealer_id=dealer_id, art=art, db=db)
        fahrer_verbraucht = await fahrer_zaehler_ct(driver_id, db=db) if grenze_fahrer > 0 else 0.0
    except Exception:  # noqa: BLE001
        return {"erlaubt": True, "sparmodus": False, "verbraucht_ct": None, "grenze_ct": grenze, "grund": "",
                "fahrer_verbraucht_ct": None, "fahrer_grenze_ct": grenze_fahrer}
    firma_ok = grenze <= 0 or verbraucht < grenze
    fahrer_ok = grenze_fahrer <= 0 or fahrer_verbraucht < grenze_fahrer
    erlaubt = firma_ok and fahrer_ok
    sparmodus = (grenze > 0 and verbraucht >= grenze * SPARMODUS_AB) \
        or (grenze_fahrer > 0 and fahrer_verbraucht >= grenze_fahrer * SPARMODUS_AB) \
        or v["letzter_lauf_ct"] > kosten_max_ct()
    grund = ""
    if not fahrer_ok:
        grund = grund_fahrer_voll(fahrer_verbraucht, grenze_fahrer)
    elif not firma_ok:
        grund = grund_firma_voll(verbraucht, grenze)
    elif sparmodus:
        grund = "Sparmodus: keine Websuche je Fall (Budget fast erreicht oder letzter Lauf zu teuer)."
    return {"erlaubt": erlaubt, "sparmodus": sparmodus, "verbraucht_ct": verbraucht,
            "letzter_lauf_ct": v["letzter_lauf_ct"], "grenze_ct": grenze, "grund": grund,
            "fahrer_verbraucht_ct": fahrer_verbraucht if grenze_fahrer > 0 else None,
            "fahrer_grenze_ct": grenze_fahrer}


async def grund_voll(*, user_id: Optional[str], dealer_id: Optional[str], art: str, driver_id: Optional[str] = None,
                     db=None) -> str:
    """Nach einer gescheiterten Reservierung: welcher Deckel war es? (pruefen
    laesst einen Lauf knapp unter der Grenze zu, reservieren rechnet die
    Einzelgrenze dazu — deshalb hier mit derselben Rechnung.) Wirft nie."""
    grenze = round(budget_monat_eur() * 100, 2)
    grenze_fahrer = _fahrer_grenze_ct(art, driver_id)
    est = round(kosten_max_ct(), 2)
    try:
        if grenze_fahrer > 0:
            f = await fahrer_zaehler_ct(driver_id, db=db)
            if f + est > grenze_fahrer:
                return grund_fahrer_voll(f, grenze_fahrer)
        if grenze > 0:
            return grund_firma_voll(await zaehler_ct(user_id=user_id, dealer_id=dealer_id, art=art, db=db), grenze)
    except Exception:  # noqa: BLE001
        pass
    return "Monatsbudget für KI-Bewertungen aufgebraucht."


async def est_ct_vermerken(bewertung_id: Optional[str], reservierung: Optional[Dict[str, Any]], db=None) -> None:
    """Die Reservierung am Bewertungsdokument festhalten (est_ct), damit
    `abgleichen` einen laufenden Lauf mitzaehlen kann. Wirft nie."""
    if not bewertung_id:
        return
    db = db if db is not None else _db
    try:
        await db[SAMMLUNG].update_one({"id": bewertung_id},
                                      {"$set": {"est_ct": float((reservierung or {}).get("est_ct") or 0)}})
    except Exception:  # noqa: BLE001
        pass


def _lease_gueltig(doc: Dict[str, Any]) -> bool:
    bis = doc.get("lease_until")
    if not bis:
        return False
    try:
        t = datetime.fromisoformat(str(bis).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t > datetime.now(timezone.utc)
    except ValueError:
        return False


async def abgleichen(db=None) -> Dict[str, Any]:
    """Review 26.09.2026 (Nr. 22): Zaehler je Schluessel des laufenden Monats
    aus den Bewertungen neu setzen — Summe kosten_ct der abgeschlossenen
    Laeufe (ok, fehler, zeitlimit ...) plus est_ct der Laeufe, die noch
    "laeuft" sind und einen gueltigen Lease haben. Eine Reservierung, deren
    Lauf abgestuerzt ist (nie abgerechnet), verfaellt so beim naechsten
    Aufraeumlauf statt bis Monatsende zu blockieren. Schritt im stuendlichen
    Aufraeumlauf ("ki_budget"). Wirft nie."""
    db = db if db is not None else _db
    monat = _monatsanfang()
    soll: Dict[str, float] = {}
    try:
        cursor = db[SAMMLUNG].find({"created_at": {"$gte": monat}},
                                   {"_id": 0, "art": 1, "user_id": 1, "dealer_id": 1, "driver_id": 1, "status": 1,
                                    "kosten_ct": 1, "est_ct": 1, "lease_until": 1}).limit(20000)
        async for d in cursor:
            art = d.get("art") or "abholung"
            key = _schluessel(d.get("user_id"), d.get("dealer_id"), art)
            if d.get("status") == "laeuft":
                betrag = float(d.get("est_ct") or 0) if _lease_gueltig(d) else 0.0
            else:
                try:
                    betrag = float(d.get("kosten_ct") or 0)
                except (TypeError, ValueError):
                    betrag = 0.0
            soll[key] = round(soll.get(key, 0.0) + max(0.0, betrag), 2)
            # Fahrer-Deckel (26.09.2026 abends): Abhol-Bewertungen tragen driver_id
            if art == "abholung" and d.get("driver_id"):
                kf = _fahrer_schluessel(str(d["driver_id"]))
                soll[kf] = round(soll.get(kf, 0.0) + max(0.0, betrag), 2)
        # Zaehler dieses Monats ohne Bewertungen -> 0 (verwaiste Reservierung)
        async for z in db[ZAEHLER].find({"_id": {"$regex": ":" + monat[:7] + "$"}}, {"_id": 1}):
            soll.setdefault(z["_id"], 0.0)
        geaendert = 0
        jetzt = datetime.now(timezone.utc).isoformat()
        for key, ct in soll.items():
            r = await db[ZAEHLER].update_one({"_id": key, "ct": {"$ne": ct}},
                                             {"$set": {"ct": ct, "abgeglichen": jetzt}})
            if r.matched_count:
                geaendert += 1
            elif ct > 0:
                # noch kein Zaehler (Lauf ohne Reservierung, z. B. ohne Grenze): anlegen
                await db[ZAEHLER].update_one({"_id": key}, {"$setOnInsert": {"ct": ct, "angelegt": jetzt,
                                                                            "abgeglichen": jetzt}}, upsert=True)
        return {"schluessel": len(soll), "geaendert": geaendert}
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("autohandel.ki").exception("KI-Budget nicht abgeglichen")
        return {"schluessel": len(soll), "geaendert": 0, "fehler": True}


# ------------------------------------------------ Loeschung / Rotation
# Review 26.09.2026 (Nr. 141-145): die Zaehler tragen KEIN dealer_id-Feld —
# die Kennung steht im Schluessel "<art>:<dealer_id|user_id>:<JJJJ-MM>".
def schluessel_filter(dealer_id: Optional[str] = None, user_ids=()) -> Optional[Dict[str, Any]]:
    """Mongo-Filter auf alle Zaehler einer Firma (Abholung: dealer_id) und
    ihrer Konten (Vertrag: user_id). `user_ids` nimmt auch Fahrer-Kennungen
    (fahrer:<driver_id>:<Monat>, Fahrer-Loeschung). None, wenn es nichts zu
    filtern gibt."""
    import re
    kennungen = [k for k in (dealer_id, *user_ids) if isinstance(k, str) and k]
    if not kennungen:
        return None
    return {"_id": {"$regex": "^[a-z_]+:(" + "|".join(re.escape(k) for k in kennungen) + "):"}}


async def zaehler_loeschen(dealer_id: Optional[str] = None, user_ids=(), db=None) -> int:
    """Zaehler einer Firma/ihrer Konten loeschen (Firmenloeschung, Sucher-
    Loeschung). Liefert die Zahl geloeschter Dokumente; wirft nie."""
    db = db if db is not None else _db
    filt = schluessel_filter(dealer_id, user_ids)
    if not filt:
        return 0
    try:
        r = await db[ZAEHLER].delete_many(filt)
        return int(r.deleted_count)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("autohandel.ki").exception("KI-Budgetzaehler nicht geloescht")
        return 0


async def zaehler_zaehlen(dealer_id: Optional[str] = None, user_ids=(), db=None) -> int:
    """Fuer die Loeschvorschau: wie viele Zaehler die Firmenloeschung entfernt."""
    db = db if db is not None else _db
    filt = schluessel_filter(dealer_id, user_ids)
    return await db[ZAEHLER].count_documents(filt) if filt else 0


ZAEHLER_MONATE = 3


async def alte_zaehler_loeschen(db=None, now: Optional[datetime] = None, monate: int = ZAEHLER_MONATE) -> int:
    """Review 26.09.2026 (Nr. 144): Zaehler haben keinen TTL — Monate, die
    aelter als `monate` sind, werden im Aufraeumlauf geloescht (der Monat
    steht am Ende des Schluessels). Wirft nie."""
    import re
    db = db if db is not None else _db
    now = now or datetime.now(timezone.utc)
    jahr, monat = now.year, now.month - monate
    while monat <= 0:
        jahr, monat = jahr - 1, monat + 12
    grenze = f"{jahr:04d}-{monat:02d}"          # Monate < grenze fliegen raus
    n = 0
    try:
        async for z in db[ZAEHLER].find({"_id": {"$regex": r":\d{4}-\d{2}$"}}, {"_id": 1}):
            m = re.search(r":(\d{4}-\d{2})$", str(z["_id"]))
            if m and m.group(1) < grenze:
                r = await db[ZAEHLER].delete_one({"_id": z["_id"]})
                n += int(r.deleted_count)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("autohandel.ki").exception("alte KI-Budgetzaehler nicht geloescht")
    return n
