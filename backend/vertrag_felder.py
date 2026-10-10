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
    # Entscheidung Ahmad 22.09.2026: eigene Kundennummer fuer Vertraege —
    # nicht die Anmeldenummer des Chefs (dealers.kunden_nr). Wird wie die
    # anderen Kaeuferdaten beim Vertrag eingefroren.
    "vertrags_kundennummer": "vertrags_kundennummer",
    # Wunsch Ahmad 24.09.2026: Empfangsbestätigung im Druck an/aus, Stand
    # beim Erstellen (None = an).
    "empfang_drucken": "empfang_drucken",
}

# Startpruefung 27.09.2026 (K3): Kaeuferfelder mit Wahrheitswert. Frueher
# machte das Einfrieren aus jedem Wert Text — aus False wurde das WORT
# "False", und der Druck hielt jeden Text fuer "an". Der Empfangsblock stand
# damit in jedem seit 24.09. angelegten Vertrag, auch bei Einstellung "aus".
# Seitdem bleibt ein bool ein bool; gespeicherte Texte versteht
# als_wahrheitswert (Vertraege vom 24.09. bis zu dieser Korrektur).
KAEUFER_WAHRHEITSFELDER = frozenset({"empfang_drucken"})

# Empfangsbestaetigung (Kaestchen im Abschnitt "Unterschriften"). Seit 24.09.
# fragt der Dialog sie nicht mehr ab; sie bleiben im Vertrag leer und werden
# bei der Uebergabe von Hand angekreuzt (Startpruefung 27.09.2026, K4).
EMPFANG_KAESTCHEN = ("empfang_zulassungsbescheinigung", "empfang_schluessel",
                     "empfang_kaufpreis")

_WAHR = frozenset({"true", "1", "ja", "an", "yes", "on", "x"})
_FALSCH = frozenset({"false", "0", "nein", "aus", "no", "off"})


def als_wahrheitswert(wert) -> Optional[bool]:
    """bool bleibt bool; gespeicherte Texte ('False', 'false', '0', 'nein',
    'aus' = aus; 'True', 'true', '1', 'ja', 'an' = an) werden verstanden.
    None, leerer oder unbekannter Text: None (= nicht festgelegt)."""
    if wert is None:
        return None
    if isinstance(wert, bool):
        return wert
    if isinstance(wert, (int, float)):
        return bool(wert)
    s = str(wert).strip().lower()
    if s in _WAHR:
        return True
    if s in _FALSCH:
        return False
    return None


def kaeufer_wert(feld: str, wert):
    """Wert eines Kaeuferfelds so, wie er im Vertrag eingefroren wird:
    Wahrheitsfelder als bool (None = nicht festgelegt), alle anderen als
    getrimmter Text ("" = nicht festgelegt)."""
    if feld in KAEUFER_WAHRHEITSFELDER:
        return als_wahrheitswert(wert)
    return str(wert).strip() if wert is not None else ""


def kaeufer_wert_fehlt(feld: str, wert) -> bool:
    """True, wenn kaeufer_wert nichts Festgelegtes liefert (None bzw. "").
    Ein eingefrorenes False ist festgelegt — es heisst "aus"."""
    w = kaeufer_wert(feld, wert)
    return w is None or w == ""


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
        # Startpruefung 27.09.2026 (K3): Wahrheitsfelder bleiben bool — take()
        # machte aus einem eingefrorenen False den Text "False".
        val = (als_wahrheitswert(contract.get(src)) if src in KAEUFER_WAHRHEITSFELDER
               else take(src))
        if val is None:
            continue
        d[ziel] = val

    return v, d
