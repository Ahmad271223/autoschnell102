# -*- coding: utf-8 -*-
"""Super-Admin-Einsicht in die dauerhaften, anonymen Auto-Daten.

NUR lesend, NUR fuer den Super-Admin (deps.current_super_admin — ein
normaler Admin bekommt 403). Ausgeliefert werden ausschliesslich die
Whitelist-Felder aus auto_daten.py; jede Filter-/Sucheingabe wird typisiert
und als Literal behandelt (kein Regex, kein Mongo-Operator aus dem Client).
"""
import re
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel

from auto_daten import COLLECTION
from deps import current_super_admin, db

router = APIRouter()

FELDER = ("brand", "model", "first_registration", "mileage_km", "fuel_type",
          "power_ps", "power_kw", "purchase_price_cents", "currency",
          "damages", "schema_version", "purchase_date")


class AutoDatenEintrag(BaseModel):
    brand: Optional[str] = None
    model: Optional[str] = None
    first_registration: Optional[str] = None
    mileage_km: Optional[int] = None
    fuel_type: Optional[str] = None
    power_ps: Optional[int] = None
    power_kw: Optional[int] = None
    purchase_price_cents: Optional[int] = None
    currency: str = "EUR"
    damages: List[str] = []
    schema_version: int = 1
    purchase_date: Optional[str] = None      # JJJJ-MM-TT (nur der Tag)


class AutoDatenSeite(BaseModel):
    items: List[AutoDatenEintrag]
    next_cursor: Optional[str] = None
    total: int
    limit: int


_EZ = r"^\d{4}(-\d{2})?$"


def _filter_bauen(search, fuel_type, ez_von, ez_bis, preis_min, preis_max,
                  km_min, km_max) -> dict:
    """Mongo-Filter ausschliesslich aus typisierten, escapeten Eingaben."""
    filt: dict = {}
    if search and search.strip():
        rx = re.escape(search.strip())
        filt["$or"] = [{"brand": {"$regex": rx, "$options": "i"}},
                       {"model": {"$regex": rx, "$options": "i"}}]
    if fuel_type and fuel_type.strip():
        filt["fuel_type"] = {"$regex": f"^{re.escape(fuel_type.strip())}$",
                             "$options": "i"}
    ez: dict = {}
    if ez_von:
        ez["$gte"] = ez_von
    if ez_bis:
        # "2020" soll den ganzen Jahrgang einschliessen ("2020-12" liegt
        # lexikografisch hinter "2020").
        ez["$lte"] = ez_bis if len(ez_bis) == 7 else f"{ez_bis}-12"
    if ez:
        filt["first_registration"] = ez
    preis: dict = {}
    if preis_min is not None:
        preis["$gte"] = preis_min
    if preis_max is not None:
        preis["$lte"] = preis_max
    if preis:
        filt["purchase_price_cents"] = preis
    km: dict = {}
    if km_min is not None:
        km["$gte"] = km_min
    if km_max is not None:
        km["$lte"] = km_max
    if km:
        filt["mileage_km"] = km
    return filt


def _eintrag(d: dict) -> AutoDatenEintrag:
    return AutoDatenEintrag(**{k: d.get(k) for k in FELDER if d.get(k) is not None})


@router.get("/admin/vehicle-data", response_model=AutoDatenSeite)
async def auto_daten_liste(
    response: Response,
    limit: int = Query(50, ge=1, le=100),
    cursor: Optional[str] = Query(None, pattern=r"^[0-9a-f]{24}$"),
    search: Optional[str] = Query(None, max_length=80),
    fuel_type: Optional[str] = Query(None, max_length=40),
    ez_von: Optional[str] = Query(None, pattern=_EZ),
    ez_bis: Optional[str] = Query(None, pattern=_EZ),
    preis_min: Optional[int] = Query(None, ge=0, le=10**12),
    preis_max: Optional[int] = Query(None, ge=0, le=10**12),
    km_min: Optional[int] = Query(None, ge=0, le=10**8),
    km_max: Optional[int] = Query(None, ge=0, le=10**8),
    _admin=Depends(current_super_admin),
):
    response.headers["Cache-Control"] = "no-store"
    filt = _filter_bauen(search, fuel_type, ez_von, ez_bis, preis_min, preis_max,
                         km_min, km_max)

    total = await db[COLLECTION].count_documents(filt)
    seite = dict(filt)
    if cursor:
        seite["_id"] = {"$lt": ObjectId(cursor)}
    docs = await db[COLLECTION].find(seite).sort("_id", -1) \
        .limit(limit + 1).to_list(limit + 1)
    next_cursor = None
    if len(docs) > limit:
        docs = docs[:limit]
        next_cursor = str(docs[-1]["_id"])
    items = [_eintrag(d) for d in docs]
    return AutoDatenSeite(items=items, next_cursor=next_cursor,
                          total=total, limit=limit)


# ---------- Gruppierte Ansicht (Wunsch 09/2026) ----------
# Marke -> Modell -> Erstzulassungsjahr -> Kraftstoff -> Autos (nach PS,
# wahlweise nach Preis auf-/absteigend). Harte Obergrenze, damit die Antwort
# nicht unbegrenzt waechst; `truncated` sagt ehrlich, wenn gekappt wurde.
GRUPPEN_MAX = 5000


class KraftstoffGruppe(BaseModel):
    name: str
    anzahl: int
    autos: List[AutoDatenEintrag]


class JahrGruppe(BaseModel):
    jahr: str
    anzahl: int
    kraftstoffe: List[KraftstoffGruppe]


class ModellGruppe(BaseModel):
    name: str
    anzahl: int
    jahre: List[JahrGruppe]


class MarkenGruppe(BaseModel):
    name: str
    anzahl: int
    modelle: List[ModellGruppe]


class AutoDatenBaum(BaseModel):
    marken: List[MarkenGruppe]
    total: int
    truncated: bool
    sort: str


@router.get("/admin/vehicle-data/gruppiert", response_model=AutoDatenBaum)
async def auto_daten_gruppiert(
    response: Response,
    sort: str = Query("ps", pattern=r"^(ps|price_asc|price_desc)$"),
    search: Optional[str] = Query(None, max_length=80),
    fuel_type: Optional[str] = Query(None, max_length=40),
    ez_von: Optional[str] = Query(None, pattern=_EZ),
    ez_bis: Optional[str] = Query(None, pattern=_EZ),
    preis_min: Optional[int] = Query(None, ge=0, le=10**12),
    preis_max: Optional[int] = Query(None, ge=0, le=10**12),
    km_min: Optional[int] = Query(None, ge=0, le=10**8),
    km_max: Optional[int] = Query(None, ge=0, le=10**8),
    _admin=Depends(current_super_admin),
):
    response.headers["Cache-Control"] = "no-store"
    filt = _filter_bauen(search, fuel_type, ez_von, ez_bis, preis_min, preis_max,
                         km_min, km_max)
    total = await db[COLLECTION].count_documents(filt)
    docs = await db[COLLECTION].find(filt).sort("_id", -1) \
        .limit(GRUPPEN_MAX).to_list(GRUPPEN_MAX)

    def s(x):
        return (x or "").strip()

    def jahr(d):
        ez = s(d.get("first_registration"))
        return ez[:4] if len(ez) >= 4 else "unbekannt"

    def preis(d):
        p = d.get("purchase_price_cents")
        return p if isinstance(p, int) else (10**15 if sort == "price_asc" else -1)

    def auto_sort(d):
        if sort == "price_asc":
            return (preis(d), d.get("power_ps") or 0)
        if sort == "price_desc":
            return (-preis(d), d.get("power_ps") or 0)
        return (d.get("power_ps") if isinstance(d.get("power_ps"), int) else 10**9,
                preis(d))

    baum: dict = {}
    for d in docs:
        m = baum.setdefault(s(d.get("brand")) or "unbekannt", {})
        mo = m.setdefault(s(d.get("model")) or "unbekannt", {})
        j = mo.setdefault(jahr(d), {})
        j.setdefault(s(d.get("fuel_type")) or "unbekannt", []).append(d)

    marken = []
    for marke in sorted(baum, key=str.lower):
        modelle = []
        for modell in sorted(baum[marke], key=str.lower):
            jahre = []
            # Neueste Erstzulassung zuerst; "unbekannt" ans Ende
            for jz in sorted(baum[marke][modell],
                             key=lambda x: (x == "unbekannt", -int(x) if x.isdigit() else 0)):
                kraftstoffe = []
                for kraft in sorted(baum[marke][modell][jz], key=str.lower):
                    autos = sorted(baum[marke][modell][jz][kraft], key=auto_sort)
                    kraftstoffe.append(KraftstoffGruppe(
                        name=kraft, anzahl=len(autos),
                        autos=[_eintrag(a) for a in autos]))
                jahre.append(JahrGruppe(
                    jahr=jz, anzahl=sum(k.anzahl for k in kraftstoffe),
                    kraftstoffe=kraftstoffe))
            modelle.append(ModellGruppe(
                name=modell, anzahl=sum(j.anzahl for j in jahre), jahre=jahre))
        marken.append(MarkenGruppe(
            name=marke, anzahl=sum(m.anzahl for m in modelle), modelle=modelle))
    return AutoDatenBaum(marken=marken, total=total,
                         truncated=total > len(docs), sort=sort)
