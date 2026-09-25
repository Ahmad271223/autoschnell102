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
