# -*- coding: utf-8 -*-
"""Kaufvorgaenge (Umbau 09.09.2026, Wunsch Ahmad).

Bisher war `vehicle_id` zugleich das INSERAT (gemeinsam je Firma, id
v_<Inserats-ID>) und der KAUFVORGANG eines einzelnen Suchers. Sobald zwei
Sucher derselben Firma dasselbe Auto vergleichen und beide einen Vertrag
anlegen duerfen, passt das nicht mehr: Kaufpreis, Termin und Status
ueberschrieben sich gegenseitig.

Jetzt gilt:
  * `vehicles`            = Inserat/Fahrzeug, gemeinsam (Daten, Fotos,
                            Kleinanzeigen-Snapshot, organisatorischer
                            Bearbeiter owner_user_id + mitbearbeiter_ids)
  * `kaufvorgaenge`       = EIN Vorgang je Vertrag: Sucher, Fahrzeug,
                            Vertrag, Kaufpreis, Status, Termin
  * `generated_pdfs`      = Vertrag (weiter je Ersteller getrennt)
  * `appointments`        = Termin zu genau einem Vertrag/Kaufvorgang

Der Fahrzeug-Lebenszyklus (lifecycle.py) ist nur noch eine ZUSAMMENFASSUNG
aller Kaufvorgaenge des Fahrzeugs (fahrzeug_status_aggregieren): abgeholt,
sobald ein Vorgang abgeholt ist; "nicht abgeholt" erst, wenn kein Vorgang
mehr offen ist. Der realisierte Kaufpreis (vehicles.purchase_price) wird
erst beim Abholen aus dem erfolgreichen Vorgang uebernommen.
"""
import logging
import uuid
from typing import Any, Dict, Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from deps import db, now_iso
from lifecycle import try_set_lifecycle

log = logging.getLogger("autohandel")

STATUS = ("vertrag_erstellt", "gesendet", "abholung_geplant",
          "abgeholt", "nicht_abgeholt", "storniert")
OFFEN = ("vertrag_erstellt", "gesendet", "abholung_geplant")
# Terminstatus -> Vorgangsstatus
_TERMIN_ZU_STATUS = {"abgeholt": "abgeholt", "nicht abgeholt": "nicht_abgeholt",
                     "storniert": "storniert", "erledigt": "abgeholt"}
_KETTE = ["verglichen", "vertrag_erstellt", "gekauft", "abholung_geplant", "abgeholt"]


def bereich(user) -> Dict[str, Any]:
    """Chef: alle Vorgaenge der Firma; Sucher: nur eigene."""
    b: Dict[str, Any] = {"dealer_id": user["dealer_id"]}
    if (user or {}).get("role") == "sucher":
        b["user_id"] = user["id"]
    return b


async def anlegen(*, dealer_id: str, user_id: str, vehicle_id: str, contract_id: str,
                  purchase_price=None, status: str = "vertrag_erstellt",
                  kaufvorgang_id: Optional[str] = None,
                  appointment_id: Optional[str] = None) -> dict:
    """Vorgang anlegen; je Vertrag genau einer (Unique-Index contract_id).
    Existiert er schon (Wiederholung), wird der vorhandene geliefert."""
    doc = {"id": kaufvorgang_id or str(uuid.uuid4()), "dealer_id": dealer_id,
           "user_id": user_id, "vehicle_id": vehicle_id, "contract_id": contract_id,
           "purchase_price": purchase_price, "status": status,
           "appointment_id": appointment_id,
           "created_at": now_iso(), "updated_at": now_iso()}
    try:
        await db.kaufvorgaenge.insert_one(dict(doc))
    except DuplicateKeyError:
        alt = await db.kaufvorgaenge.find_one({"contract_id": contract_id}, {"_id": 0})
        if alt:
            return alt
        raise
    doc.pop("_id", None)
    return doc


async def fuer_vertrag(contract: dict) -> Optional[dict]:
    if not contract:
        return None
    if contract.get("kaufvorgang_id"):
        kv = await db.kaufvorgaenge.find_one({"id": contract["kaufvorgang_id"]}, {"_id": 0})
        if kv:
            return kv
    if contract.get("id"):
        kv = await db.kaufvorgaenge.find_one({"contract_id": contract["id"]}, {"_id": 0})
        if kv:
            return kv
        # Selbstheilung: der Vertrag traegt eine kaufvorgang_id, der Vorgang
        # fehlt (Anlage nach dem Vertrags-Insert gescheitert) -> nachlegen.
        if contract.get("dealer_id") and contract.get("vehicle_id") and contract.get("user_id"):
            return await anlegen(
                dealer_id=contract["dealer_id"], user_id=contract["user_id"],
                vehicle_id=contract["vehicle_id"], contract_id=contract["id"],
                purchase_price=contract.get("purchase_price"),
                kaufvorgang_id=contract.get("kaufvorgang_id") or None,
                appointment_id=contract.get("appointment_id"))
    return None


async def fuer_termin(appt: dict) -> Optional[dict]:
    if not appt:
        return None
    if appt.get("kaufvorgang_id"):
        kv = await db.kaufvorgaenge.find_one({"id": appt["kaufvorgang_id"]}, {"_id": 0})
        if kv:
            return kv
    if appt.get("contract_id"):
        return await db.kaufvorgaenge.find_one({"contract_id": appt["contract_id"]}, {"_id": 0})
    return None


async def status_setzen(kaufvorgang_id: str, status: str, *, user: Optional[dict] = None,
                        appointment_id=..., extra: Optional[dict] = None) -> Optional[dict]:
    """Status des Vorgangs setzen und den Fahrzeugstatus neu zusammenfassen.
    `appointment_id` nur mitgeben, wenn er sich aendert (auch None)."""
    if status not in STATUS:
        raise ValueError(f"unbekannter Kaufvorgang-Status {status!r}")
    setzen: Dict[str, Any] = {"status": status, "updated_at": now_iso(), **(extra or {})}
    if appointment_id is not ...:
        setzen["appointment_id"] = appointment_id
    doc = await db.kaufvorgaenge.find_one_and_update(
        {"id": kaufvorgang_id}, {"$set": setzen},
        projection={"_id": 0}, return_document=ReturnDocument.AFTER)
    if doc:
        await fahrzeug_status_aggregieren(doc["vehicle_id"], doc["dealer_id"], user=user)
    return doc


async def termin_status_uebernehmen(appt: dict, termin_status: str, *,
                                    user: Optional[dict] = None) -> bool:
    """Terminstatus auf den Kaufvorgang des Termins abbilden (abgeholt,
    nicht abgeholt, storniert; alles andere = Abholung geplant). Liefert
    False, wenn der Termin keinen Kaufvorgang hat (manueller Termin ohne
    Vertrag) — dann darf der Aufrufer wie frueher direkt am Fahrzeug
    arbeiten."""
    kv = await fuer_termin(appt)
    if not kv:
        return False
    neu = _TERMIN_ZU_STATUS.get(termin_status, "abholung_geplant")
    if kv.get("status") != neu:
        await status_setzen(kv["id"], neu, user=user)
    return True


def _schritte(aktuell: str, ziel: str) -> list:
    """Welche Lifecycle-Schritte fuehren von `aktuell` zu `ziel`? Leer, wenn
    der Weg nicht in der Kaufkette liegt (Bestand, Verkauf, geloescht ...)."""
    if ziel in ("nicht_abgeholt", "storniert"):
        return [ziel] if aktuell in ("vertrag_erstellt", "gekauft", "abholung_geplant",
                                     "verglichen", "besichtigung", "verhandlung") else []
    if ziel not in _KETTE:
        return []
    zi = _KETTE.index(ziel)
    if aktuell == "nicht_abgeholt":
        start = _KETTE.index("abholung_geplant")
        return _KETTE[start:zi + 1] if zi >= start else []
    if aktuell == "storniert":
        return _KETTE[:zi + 1]
    if aktuell in ("gefunden", "besichtigung", "verhandlung"):
        return _KETTE[1:zi + 1]
    if aktuell in _KETTE:
        ai = _KETTE.index(aktuell)
        return _KETTE[ai + 1:zi + 1] if zi > ai else []
    return []


async def fahrzeug_status_aggregieren(vehicle_id: str, dealer_id: str, *,
                                      user: Optional[dict] = None) -> Optional[str]:
    """Fahrzeug-Lebenszyklus aus ALLEN Kaufvorgaengen des Fahrzeugs ableiten:
      abgeholt (irgendein Vorgang)  > abholung_geplant > gekauft (Vertrag
      erstellt/gesendet) > nur beendete Vorgaenge: nicht_abgeholt bzw. storniert.
    Beim Abholen wird der realisierte Kaufpreis ans Fahrzeug geschrieben
    (juengster abgeholter Vorgang). Nie eine Exception nach aussen."""
    try:
        faelle = await db.kaufvorgaenge.find(
            {"vehicle_id": vehicle_id, "dealer_id": dealer_id},
            {"_id": 0, "status": 1, "purchase_price": 1, "updated_at": 1}).to_list(500)
        if not faelle:
            return None
        stati = {f.get("status") for f in faelle}
        if "abgeholt" in stati:
            abg = max((f for f in faelle if f.get("status") == "abgeholt"),
                      key=lambda f: f.get("updated_at") or "")
            if abg.get("purchase_price") is not None:
                await db.vehicles.update_one(
                    {"id": vehicle_id, "dealer_id": dealer_id},
                    {"$set": {"purchase_price": abg["purchase_price"]}})
            ziel = "abgeholt"
        elif "abholung_geplant" in stati:
            ziel = "abholung_geplant"
        elif stati & {"vertrag_erstellt", "gesendet"}:
            ziel = "gekauft"
        else:
            ziel = "nicht_abgeholt" if "nicht_abgeholt" in stati else "storniert"
        v = await db.vehicles.find_one({"id": vehicle_id, "dealer_id": dealer_id},
                                       {"_id": 0, "lifecycle": 1})
        aktuell = (v or {}).get("lifecycle") or "verglichen"
        for schritt in _schritte(aktuell, ziel):
            await try_set_lifecycle(vehicle_id, dealer_id, schritt, user=user)
        return ziel
    except Exception:
        log.exception("Fahrzeugstatus fuer %s konnte nicht zusammengefasst werden", vehicle_id)
        return None


async def termin_loesen(appointment_id: str) -> None:
    """Termin geloescht/abgehaengt: Vorgaenge, die auf ihn zeigen, verlieren
    den Verweis; ein geplanter Vorgang faellt auf 'vertrag_erstellt' zurueck."""
    async for kv in db.kaufvorgaenge.find({"appointment_id": appointment_id},
                                          {"_id": 0, "id": 1, "status": 1}):
        neu = "vertrag_erstellt" if kv.get("status") == "abholung_geplant" else kv.get("status")
        await status_setzen(kv["id"], neu, appointment_id=None)
