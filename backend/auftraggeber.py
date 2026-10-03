"""Auftraggeber im Abholprotokoll (Runde 24, 11.09.2026).

Befund Ahmad: Im Abholprotokoll stand unter "AUFTRAGGEBER" nur "—" und im
Kopf "Autohändler" — die drei Aufrufer (Terminkalender, Fahrer-App,
Protokoll-Abschluss) uebergaben nur das nackte Firmen-Dokument (db.dealers).
Richtig ist, wer im Kaufvertrag als Kaeufer steht:

  1. Basis = effective_dealer des Vertrags-Erstellers (generated_pdfs.user_id)
     — also MIT seinen Sucher-Einstellungen (Beschluss "Sucher-Overrides
     bleiben"), sonst des Termin-Erstellers (appointments.created_by), sonst
     das Firmen-Dokument.
  2. Darauf die im Vertrag eingetragenen Kaeuferfelder (dealer_company,
     dealer_contact, …) mit DERSELBEN Zuordnung wie im Kaufvertrag
     (routes.contracts._apply_contract_overrides) — Kaufvertrag und
     Abholprotokoll zeigen damit denselben Auftraggeber.

Nur Konten DERSELBEN Firma zaehlen: ein fremdes created_by (z. B. ein
Admin-Konto) faellt auf die Firmendaten zurueck, nie auf eine andere Firma.

DB-Zugriff bewusst ueber deps.db zur Aufrufzeit: effective_dealer liest
ebenfalls deps.db, und die Tests biegen genau dieses Modul-db um (ein
eigenes "from deps import db" haenge sonst am Loop des ersten Tests).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import deps


async def _ersteller_basis(user_id: Optional[str],
                           dealer_id: str) -> Optional[Dict[str, Any]]:
    """effective_dealer eines Kontos DIESER Firma — None, wenn es das Konto
    (in dieser Firma) nicht gibt."""
    if not user_id:
        return None
    u = await deps.db.users.find_one(
        {"id": user_id, "dealer_id": dealer_id},
        {"_id": 0, "id": 1, "role": 1, "dealer_id": 1, "settings_override": 1})
    if not u:
        return None
    return await deps.effective_dealer(u)


async def kaeufer_basis(*, dealer_id: str, user_ids) -> Dict[str, Any]:
    """Basis des Kaeufers: effective_dealer des ERSTEN Kontos DIESER Firma aus
    user_ids (Vertrags-Ersteller, dann Termin-Ersteller), sonst das
    Firmen-Dokument.

    Gegenpruefung Runde 25 (12.09.2026): Kaufvertrag, Neuerzeugung nach
    verschobenem Termin und Abholprotokoll muessen DIESELBE Kette nutzen —
    sonst friert z. B. der Chef beim Verschieben seine Firmendaten im
    Vertrag eines Suchers ein.
    """
    for uid in user_ids or ():
        basis = await _ersteller_basis(uid, dealer_id)
        if basis is not None:
            return basis
    return await deps.db.dealers.find_one({"id": dealer_id}, {"_id": 0}) or {}


async def auftraggeber_fuer_termin(appt: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Haendler-Dokument des Auftraggebers eines Abholtermins (company_name,
    contact_person, address, zip_code, city, phone, email, …) — mit oder
    ohne Kaufvertrag. Liefert {} nur, wenn der Termin keiner Firma gehoert."""
    # Spaet importiert: routes.contracts zieht pdf_service & Co. nach und
    # darf beim Import der Termin-/Fahrer-Routen keinen Kreis bilden.
    from routes.contracts import _apply_contract_overrides

    appt = appt or {}
    dealer_id = appt.get("dealer_id")
    if not dealer_id:
        return {}
    vertrag: Dict[str, Any] = {}
    if appt.get("contract_id"):
        vertrag = await deps.db.generated_pdfs.find_one(
            {"id": appt["contract_id"], "dealer_id": dealer_id},
            {"_id": 0, "user_id": 1, "contract_data": 1}) or {}
    basis = await kaeufer_basis(
        dealer_id=dealer_id,
        user_ids=(vertrag.get("user_id"), appt.get("created_by")))
    daten = vertrag.get("contract_data")
    _, auftraggeber = _apply_contract_overrides(
        contract=daten if isinstance(daten, dict) else {},
        vehicle={}, dealer=basis)
    return auftraggeber
