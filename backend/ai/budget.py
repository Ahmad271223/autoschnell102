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


async def reservieren(*, user_id: Optional[str], dealer_id: Optional[str], art: str, db=None) -> Optional[Dict[str, Any]]:
    """Review 25.09.2026 abends: das Budget wird VOR dem Lauf atomar reserviert
    (Zaehler je Konto/Firma und Monat, ein find_one_and_update mit $expr) —
    zwei gleichzeitige Laeufe koennen die Grenze nicht mehr gemeinsam
    ueberschreiten. Reserviert wird die Einzelgrenze (KI_KOSTEN_MAX_CT),
    `abrechnen` ersetzt sie nach dem Lauf durch die echten Kosten.
    None = Budget voll. Ohne Grenze oder bei DB-Fehler: offen (wie pruefen)."""
    grenze = round(budget_monat_eur() * 100, 2)
    est = round(kosten_max_ct(), 2)
    if grenze <= 0:
        return {"schluessel": None, "est_ct": 0.0}
    db = db if db is not None else _db
    key = _schluessel(user_id, dealer_id, art)
    try:
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
        if doc is None:
            return None                                   # Budget voll
        return {"schluessel": key, "est_ct": est}
    except Exception:  # noqa: BLE001 — die Bremse darf die KI nicht abschalten
        return {"schluessel": None, "est_ct": 0.0}


async def abrechnen(reservierung: Optional[Dict[str, Any]], tatsaechlich_ct: float, db=None) -> None:
    """Reservierung durch die echten Kosten ersetzen (0 = freigeben). Wirft nie."""
    if not reservierung or not reservierung.get("schluessel"):
        return
    db = db if db is not None else _db
    try:
        delta = round(float(tatsaechlich_ct or 0) - float(reservierung.get("est_ct") or 0), 2)
        await db[ZAEHLER].update_one({"_id": reservierung["schluessel"]}, {"$inc": {"ct": delta}})
    except Exception:  # noqa: BLE001
        pass


async def zaehler_ct(*, user_id: Optional[str], dealer_id: Optional[str], art: str, db=None) -> float:
    db = db if db is not None else _db
    try:
        d = await db[ZAEHLER].find_one({"_id": _schluessel(user_id, dealer_id, art)}, {"ct": 1})
        return round(float((d or {}).get("ct") or 0), 2)
    except Exception:  # noqa: BLE001
        return 0.0


async def pruefen(*, user_id: Optional[str], dealer_id: Optional[str], art: str, db=None) -> Dict[str, Any]:
    """{"erlaubt", "sparmodus", "verbraucht_ct", "grenze_ct", "grund"} — wirft nie."""
    grenze = round(budget_monat_eur() * 100, 2)
    try:
        v = await verbraucht_ct(user_id=user_id, dealer_id=dealer_id, art=art, db=db)
    except Exception:  # noqa: BLE001
        return {"erlaubt": True, "sparmodus": False, "verbraucht_ct": None, "grenze_ct": grenze, "grund": ""}
    erlaubt = grenze <= 0 or v["verbraucht_ct"] < grenze
    sparmodus = (grenze > 0 and v["verbraucht_ct"] >= grenze * SPARMODUS_AB) or v["letzter_lauf_ct"] > kosten_max_ct()
    grund = ""
    if not erlaubt:
        grund = (f"Monatsbudget für KI-Bewertungen aufgebraucht ({v['verbraucht_ct'] / 100:.2f} € von "
                 f"{grenze / 100:.2f} €) — ab dem 1. des nächsten Monats wieder verfügbar.")
    elif sparmodus:
        grund = "Sparmodus: keine Websuche je Fall (Budget fast erreicht oder letzter Lauf zu teuer)."
    return {"erlaubt": erlaubt, "sparmodus": sparmodus, "verbraucht_ct": v["verbraucht_ct"],
            "letzter_lauf_ct": v["letzter_lauf_ct"], "grenze_ct": grenze, "grund": grund}
