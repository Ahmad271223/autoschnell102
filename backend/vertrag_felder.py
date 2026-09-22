# -*- coding: utf-8 -*-
"""Welche Felder aus dem Vertrags-Dialog ueberschreiben Fahrzeug und Kaeufer?

Gegenpruefung 12.09.2026: Die Zuordnung lag in routes/contracts.py. Seit das
Abholprotokoll (protokoll_vergleich, pickup_pdf_service) sie nutzt, zog jedes
PDF — auch das leere Papier-PDF — den ganzen Routen- und Datenbank-Stack nach
und brauchte MONGO_URL. Hier ohne FastAPI und ohne Datenbank; routes.contracts
importiert von hier (die bisherigen Namen dort bleiben gueltig).
"""
from typing import Optional

# Zuordnung fuer das Einsetzen (_apply_contract_overrides) UND das
# Einfrieren (routes.contracts.kaeufer_einfrieren) der Kaeuferdaten.
KAEUFER_FELDER = {
    "dealer_company": "company_name",
    "dealer_contact": "contact_person",
    "dealer_phone": "phone",
    "dealer_whatsapp": "whatsapp_number",
    "dealer_email": "email",
    "dealer_address": "address",
    "dealer_zip": "zip_code",
    "dealer_city": "city",
}


def _apply_contract_overrides(*, contract: dict, vehicle: dict, dealer: dict) -> tuple[dict, dict]:
    """Mergt die im Vertrags-Dialog editierten Fahrzeug- & Händler-Werte
    in die `vehicle`/`dealer`-Dicts hinein, die der PDF-Builder dann nutzt.
    So bleibt der bestehende PDF-Code unverändert.

    Werte werden NUR überschrieben, wenn der Händler im Dialog tatsächlich
    etwas eingetragen hat (nicht None und nicht leer-string) — Ausnahme seit
    22.09.2026 (RP-404): bei den Fahrzeugfeldern (Menge `leerbar`) heisst
    ein leerer Text "im Vertrag bewusst leer"; nur None/fehlend faellt auf
    das Inserat zurueck. Kaeuferfelder bleiben bei "leer = Profilwert"."""
    v = dict(vehicle or {})
    d = dict(dealer or {})

    def take(src_key: str) -> Optional[str]:
        val = contract.get(src_key)
        if val is None:
            return None
        s = str(val).strip()
        return s or None

    # --- Fahrzeug-Mappings (Override → Vehicle-Dict) ---
    veh_map = {
        "vehicle_make": ("make_label", "make"),
        "vehicle_model": ("model_description", "model_label", "model"),
        "vehicle_category": ("category_label", "category"),
        "vehicle_first_registration": ("first_registration", "ezl"),
        "vehicle_mileage": ("mileage", "km"),
        "vehicle_fuel": ("fuel_label", "fuel_type", "fuel"),
        "vehicle_gearbox": ("gearbox_label", "transmission", "gearbox"),
        "vehicle_power_kw": ("power_kw",),
        "vehicle_power_ps": ("power_ps",),
        "vehicle_displacement": ("displacement", "cubic_capacity"),
        "vehicle_color": ("exterior_color", "color"),
        "vehicle_doors": ("door_count", "doors"),
        "vehicle_seats": ("seat_count", "seats"),
        "vehicle_vin": ("vin", "fin"),
        "vehicle_license_plate": ("license_plate", "kennzeichen"),
        "vehicle_damage_note": ("damage_note",),
    }
    # Pruefbericht 20.09.2026 (P-06): Die FIN schickt der Dialog IMMER mit
    # (vorbelegt aus dem Inserat). Ein bewusst geleertes Feld heisst "keine
    # FIN" — vorher machte take() daraus None, und die Inserats-FIN stand
    # wieder im Vertrag.
    # Rollenpruefung 22.09.2026 (RP-404): dasselbe gilt fuer ALLE Fahrzeug-
    # felder, die der Dialog vorbelegt und immer mitschickt (Kilometerstand,
    # Erstzulassung, Farbe, …): "" = im Vertrag bewusst leer, None bzw. ein
    # fehlendes Feld (Altvertrag, anderer Client) = Wert aus dem Inserat.
    # Vorher kam z. B. ein geleerter Kilometerstand still aus dem Inserat
    # zurueck. Das Kennzeichen steht seit 15.09. nicht mehr im Vertrag.
    leerbar = set(veh_map) - {"vehicle_license_plate"}
    for src, targets in veh_map.items():
        val = take(src)
        if val is None:
            if src in leerbar and isinstance(contract.get(src), str) \
                    and not contract[src].strip():
                for t in targets:
                    v[t] = ""
            continue
        for t in targets:
            v[t] = val

    # --- Kaeufer-Mappings (Override → Dealer-Dict) ---
    for src, ziel in KAEUFER_FELDER.items():
        val = take(src)
        if val is None:
            continue
        d[ziel] = val

    return v, d
