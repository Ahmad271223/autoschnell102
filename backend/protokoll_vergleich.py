# -*- coding: utf-8 -*-
"""Vorher/Nachher im Abholprotokoll (Wunsch Ahmad, 12.09.2026).

"Besseres vorher/nachher": Der Chef soll im Freigabe-Kasten auf einen Blick
sehen, was laut VERTRAG vereinbart war und was der Fahrer VOR ORT gefunden hat.

Die Analyse vom 12.09.2026 fand dabei drei Fehler, die hier zusammen behoben
werden — sonst waere jeder Vorher-Wert falsch gewesen:
  * "Laut Vertrag" war in Wahrheit der INSERATSWERT. Korrigiert der Haendler
    im Vertrags-Dialog z.B. die Erstzulassung, sah der Fahrer vor Ort weiter
    den alten Wert. Jetzt gelten die Ueberschreibungen aus dem Vertrag
    (vertrag_felder._apply_contract_overrides) — ueberall gleich: App,
    Freigabe-Kasten und Protokoll-PDF.
  * Ja/Nein-Zeilen (Gewerbliche Nutzung, Unfallfrei) galten bei jeder Antwort
    ausser "Ja" als Abweichung — auch "Gewerbliche Nutzung: Nein". Jetzt wird
    mit der Angabe im Vertrag verglichen.
  * Ein Korrekturwert blieb stehen, wenn der Fahrer zurueck auf "stimmt"
    stellte, und landete trotzdem im unterschriebenen PDF.

Gegenpruefung 12.09.2026 (danach):
  * HU nur aus dem Vertrag — der Kaufvertrag druckt kein HU-Datum aus dem
    Inserat, das Protokoll nannte es trotzdem "laut Vertrag".
  * zahl() haengte alle Ziffern aneinander ("ca. 72.000, Tacho 2019" wurde
    720002019 km). Jetzt nur eindeutig lesbare Zahlen.
  * Ein halb getipptes "06/20" vor Ort wurde still als 06/2020 gelesen.
  * "Unfallfrei: Nein" ist keine Abweichung, wenn der Vertrag schon "Nein" sagt.
  * Infinity/NaN aus einem Protokoll liess die ganze Freigaben-Liste scheitern.

Reines Modul ohne FastAPI und ohne Datenbank — routes/protocols.py und
pickup_pdf_service.py nutzen es beide.
"""
import math
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from vertrag_felder import _apply_contract_overrides

# Abschnitt 1 im PDF: 12 Zeilen mit "stimmt / weicht ab" (bzw. Ja/Nein/
# unbekannt). Der Fahrer kreuzt jede Zeile am Handy an und kann bei
# Abweichung den korrekten Wert eintippen. (routes.protocols.VEHICLE_CHECK_FIELDS)
FELDER: List[Tuple[str, str, List[str]]] = [
    ("make", "Marke", ["stimmt", "weicht ab"]),
    ("model", "Modell", ["stimmt", "weicht ab"]),
    ("first_registration", "Erstzulassung", ["stimmt", "weicht ab"]),
    ("vin", "FIN (Fahrgestell-Nr.)", ["stimmt", "weicht ab"]),
    ("power", "Leistung", ["stimmt", "weicht ab"]),
    ("previous_owners", "Halter laut Schein", ["stimmt", "weicht ab"]),
    ("color", "Farbe", ["stimmt", "weicht ab"]),
    ("fuel", "Kraftstoff", ["stimmt", "weicht ab"]),
    ("hu", "HU", ["stimmt", "weicht ab"]),
    ("mileage_contract", "KM-Stand laut Vertrag", ["stimmt", "weicht ab"]),
    ("commercial", "Gewerbliche Nutzung", ["Ja", "Nein", "unbekannt"]),
    ("accident_free", "Unfallfrei laut Angabe", ["Ja", "Nein", "unbekannt"]),
]

# Wie werden die Werte einer Zeile gelesen und angezeigt?
ARTEN: Dict[str, str] = {
    "make": "text", "model": "text", "first_registration": "monat_jahr",
    "vin": "fin", "power": "leistung", "previous_owners": "anzahl",
    "color": "text", "fuel": "text", "hu": "hu", "mileage_contract": "km",
    "commercial": "ja_nein", "accident_free": "ja_nein",
}

# Ab welcher Differenz ist der Kilometerstand bei Abholung eine Abweichung?
# Die Fahrt zum Termin und kleine Tippfehler im Inserat sollen nicht jedes
# Protokoll rot machen.
KM_TOLERANZ = 1000


# ------------------------------------------------------------ Lesen
def _jetzt_jahr_zwei() -> int:
    return datetime.now().year % 100


def monat_jahr_text(wert: Any, art: str = "ez", kurzes_jahr: bool = True) -> str:
    """'1.2020', '01/2020', '2020-01', '01.03.2020', '032020', '06/26' ->
    'MM/JJJJ'. Nicht Lesbares (nur ein Jahr, 'Neu') bleibt unveraendert.

    kurzes_jahr=False: '06/20' bleibt '06/20' — fuer Eingaben VOR ORT, die
    halb getippt sein koennen (Altwerte aus Vertrag/Inserat duerfen kurz sein)."""
    if wert in (None, ""):
        return ""
    s = str(wert).strip()
    t = s.lower()
    m = j = None
    r = re.fullmatch(r"(\d{4})\s*[-/.]\s*(\d{1,2})", t)
    if r:
        j, m = int(r.group(1)), int(r.group(2))
    else:
        r = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", t)
        if r:
            m, j = int(r.group(2)), int(r.group(3))
        else:
            r = re.fullmatch(r"(\d{1,2})\s*[/.\-]\s*(\d{4})", t)
            if r:
                m, j = int(r.group(1)), int(r.group(2))
            else:
                r = re.fullmatch(r"(\d{1,2})\s*[/.\-]\s*(\d{2})", t) if kurzes_jahr else None
                if r:
                    m, jj = int(r.group(1)), int(r.group(2))
                    j = 2000 + jj if (art == "hu" or jj <= _jetzt_jahr_zwei()) else 1900 + jj
                else:
                    r = re.fullmatch(r"(\d{2})(\d{4})", t)
                    if r:
                        m, j = int(r.group(1)), int(r.group(2))
    if m is None or not (1 <= m <= 12) or not j or not (1900 <= j <= 2100):
        return s
    return f"{m:02d}/{j}"


def zahl(wert: Any) -> Optional[int]:
    """'86.000 km' / 86000.0 / '86000' / '110 kW' -> Zahl; sonst None.

    Nur eindeutig Lesbares: 'ca. 72.000, Tacho 2019' oder '2 (Brief: 3)' sind
    KEINE Zahl (vorher wurden alle Ziffern aneinandergehaengt)."""
    if wert is None or isinstance(wert, bool):
        return None
    if isinstance(wert, float):
        return int(wert) if math.isfinite(wert) else None
    if isinstance(wert, int):
        return wert
    s = str(wert).strip().lower()
    s = re.sub(r"\s*(km|kw|ps)\.?$", "", s).strip()
    if re.fullmatch(r"\d+", s):
        return int(s)
    if re.fullmatch(r"\d{1,3}(?:[.\s']\d{3})+", s):
        return int(re.sub(r"\D", "", s))
    r = re.fullmatch(r"(\d+)[.,]\d{1,2}", s)
    if r:
        return int(r.group(1))
    return None


def km_text(n: Optional[int]) -> str:
    return f"{n:,}".replace(",", ".") + " km" if n is not None else ""


def ja_nein(wert: Any) -> Optional[str]:
    s = str(wert or "").strip().lower()
    if s in ("ja", "yes", "true", "1"):
        return "Ja"
    if s in ("nein", "no", "false", "0"):
        return "Nein"
    return None


def json_sicher(wert: Any) -> Any:
    """Infinity/NaN -> None, verschachtelt. Ein einziges kaputtes Protokoll
    liess sonst die ganze Antwort beim JSON-Rendern scheitern."""
    if isinstance(wert, float) and not math.isfinite(wert):
        return None
    if isinstance(wert, dict):
        return {k: json_sicher(v) for k, v in wert.items()}
    if isinstance(wert, list):
        return [json_sicher(v) for v in wert]
    return wert


def _text(wert: Any) -> str:
    return str(wert).strip() if wert not in (None, "") else ""


# ------------------------------------------------------------ Vertragswerte
def vertragswerte(vehicle: Optional[dict], contract: Optional[dict]) -> Dict[str, Dict[str, Any]]:
    """Soll-Werte je Zeile: {schluessel: {wert, text, quelle}}.

    quelle "vertrag" = im Vertrags-Dialog eingetragen, "inserat" = aus den
    Fahrzeugdaten, None = unbekannt."""
    c = dict(contract or {})
    roh = dict(vehicle or {})
    v, _ = _apply_contract_overrides(contract=c, vehicle=roh, dealer={})

    def quelle(*vertrag_felder: str, inserat: Any = None) -> Optional[str]:
        if any(_text(c.get(f)) for f in vertrag_felder):
            return "vertrag"
        return "inserat" if inserat not in (None, "", [], {}) else None

    werte: Dict[str, Dict[str, Any]] = {}

    marke = _text(v.get("make_label") or v.get("make") or v.get("brand"))
    werte["make"] = {"wert": marke, "text": marke, "quelle": quelle("vehicle_make", inserat=marke)}

    modell = _text(v.get("model_label") or v.get("model") or v.get("model_description"))
    werte["model"] = {"wert": modell, "text": modell, "quelle": quelle("vehicle_model", inserat=modell)}

    ez = monat_jahr_text(v.get("first_registration") or v.get("ezl") or v.get("ez"), "ez")
    werte["first_registration"] = {"wert": ez, "text": ez,
                                   "quelle": quelle("vehicle_first_registration", inserat=ez)}

    fin = re.sub(r"\s+", "", _text(v.get("vin") or v.get("fin"))).upper()
    werte["vin"] = {"wert": fin, "text": fin, "quelle": quelle("vehicle_vin", inserat=fin)}

    kw = zahl(v.get("power_kw") or v.get("kw"))
    ps = zahl(v.get("power_ps") or v.get("ps"))
    if ps is None and kw:
        ps = round(kw * 1.35962)
    leistung = f"{kw if kw is not None else '—'} kW / {ps if ps is not None else '—'} PS" if (kw or ps) else ""
    werte["power"] = {"wert": kw, "text": leistung,
                      "quelle": quelle("vehicle_power_kw", "vehicle_power_ps", inserat=leistung)}

    halter = zahl(c.get("previous_owners"))
    halter_quelle = "vertrag" if halter is not None else None
    if halter is None:
        halter = zahl(roh.get("previous_owners"))
        halter_quelle = "inserat" if halter is not None else None
    werte["previous_owners"] = {"wert": halter, "text": str(halter) if halter is not None else "",
                                "quelle": halter_quelle}

    farbe = _text(v.get("exterior_color") or v.get("color"))
    werte["color"] = {"wert": farbe, "text": farbe, "quelle": quelle("vehicle_color", inserat=farbe)}

    kraftstoff = _text(v.get("fuel_label") or v.get("fuel_type") or v.get("fuel"))
    werte["fuel"] = {"wert": kraftstoff, "text": kraftstoff,
                     "quelle": quelle("vehicle_fuel", inserat=kraftstoff)}

    # Gegenpruefung 12.09.2026: Gibt es einen Vertrag, gilt NUR seine HU-Angabe.
    # Der Kaufvertrag druckt kein HU-Datum aus dem Inserat — das Protokoll
    # durfte es deshalb nicht "laut Vertrag" nennen. Ohne Vertrag (manuelles
    # Fahrzeug, Termin ohne Vertrag) bleibt das Inserat die einzige Angabe.
    hu_gueltig = ja_nein(c.get("hu_valid"))
    hu_inserat = "" if c else roh.get("hu")
    hu_bis = monat_jahr_text(c.get("hu_until") or hu_inserat, "hu")
    if hu_gueltig == "Nein":
        hu_text = "keine HU"
    elif hu_bis:
        hu_text = hu_bis
    elif hu_gueltig == "Ja":
        hu_text = "gültig"
    else:
        hu_text = ""
    werte["hu"] = {"wert": hu_text, "text": hu_text,
                   "quelle": quelle("hu_valid", "hu_until", inserat=hu_inserat)}

    km = zahl(v.get("mileage") or v.get("km"))
    werte["mileage_contract"] = {"wert": km, "text": km_text(km),
                                 "quelle": quelle("vehicle_mileage", inserat=km)}

    for schluessel, feld in (("commercial", "commercial_since_ez"), ("accident_free", "accident_free")):
        jn = ja_nein(c.get(feld))
        werte[schluessel] = {"wert": jn, "text": jn or "", "quelle": "vertrag" if jn else None}
    return werte


def werte_als_text(werte: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """Fuer die Fahrer-App (vehicle_check_values) und das PDF."""
    return {k: (w or {}).get("text") or "" for k, w in werte.items()}


# ------------------------------------------------------------ Vergleich
def _eintrag(vehicle_check: Optional[dict], schluessel: str) -> Tuple[str, str]:
    e = (vehicle_check or {}).get(schluessel)
    if isinstance(e, dict):
        return _text(e.get("status")), _text(e.get("value"))
    return _text(e), ""


def _vor_ort_lesen(art: str, schluessel: str, roh: str) -> Tuple[str, Any]:
    if not roh:
        return "", None
    if art in ("monat_jahr", "hu"):
        t = monat_jahr_text(roh, "hu" if art == "hu" else "ez", kurzes_jahr=False)
        return t, t
    if art == "km":
        n = zahl(roh)
        return (km_text(n), n) if n is not None else (roh, None)
    if art == "anzahl":
        n = zahl(roh)
        return (str(n), n) if n is not None else (roh, None)
    if art == "fin":
        t = re.sub(r"\s+", "", roh).upper()
        return t, t
    return roh, roh


def vergleich(felder: Iterable[Tuple[str, str, List[str]]], vehicle_check: Optional[dict],
              condition: Optional[dict], werte: Dict[str, Dict[str, Any]]) -> List[dict]:
    """Eine Zeile je Pruefpunkt: Vertrag -> vor Ort, mit Hinweis.

    Regeln:
      * "stimmt": vor Ort = Vertrag. Ein alter Korrekturwert zaehlt NICHT.
      * "weicht ab": vor Ort = Eingabe des Fahrers, einheitlich formatiert;
        halb getippte Daten und unlesbare Zahlen bekommen einen Hinweis.
      * Ja/Nein: abweichend nur, wenn der Vertrag etwas anderes sagt;
        "unbekannt" und fehlende Vertragsangabe sind nur ein Hinweis.
        "Unfallfrei: Nein" wird hervorgehoben — ausser der Vertrag sagt
        schon "Nein" (Unfall bekannt).
      * Kilometer: ohne eigene Korrektur zeigt die Zeile den Stand bei
        Abholung; abweichend ab KM_TOLERANZ Differenz."""
    km_abholung = zahl((condition or {}).get("mileage"))
    zeilen: List[dict] = []
    for schluessel, label, _optionen in felder:
        art = ARTEN.get(schluessel, "text")
        soll = werte.get(schluessel) or {}
        status, roh = _eintrag(vehicle_check, schluessel)
        zeile = {
            "schluessel": schluessel, "label": label, "art": art, "status": status,
            "vertrag_text": soll.get("text") or "", "vertrag_quelle": soll.get("quelle"),
            "vor_ort_text": "", "wert_roh": roh, "abweichend": False, "hinweis": "",
        }
        if art == "ja_nein":
            zeile["vor_ort_text"] = status
            vertrag = soll.get("wert")
            if status in ("Ja", "Nein") and vertrag in ("Ja", "Nein") and status != vertrag:
                zeile["abweichend"] = True
            elif status == "unbekannt":
                zeile["hinweis"] = "Fahrer: unbekannt"
            elif status in ("Ja", "Nein") and vertrag is None:
                zeile["hinweis"] = "nicht im Vertrag"
            if schluessel == "accident_free" and status == "Nein":
                if vertrag == "Nein":
                    zeile["hinweis"] = "Unfall laut Vertrag bekannt"
                else:
                    zeile["abweichend"] = True
        elif status == "weicht ab":
            text, zahlwert = _vor_ort_lesen(art, schluessel, roh)
            zeile["vor_ort_text"] = text
            zeile["abweichend"] = True
            if not text:
                zeile["hinweis"] = "kein Wert eingetragen"
            elif (art in ("monat_jahr", "hu") and re.search(r"\d", text)
                    and not re.fullmatch(r"\d{2}/\d{4}", text)):
                zeile["hinweis"] = "unvollständig"
            elif art in ("km", "anzahl") and zahlwert is None:
                zeile["hinweis"] = "keine lesbare Zahl"
            elif art == "km" and zahlwert is not None and soll.get("wert") is not None:
                diff = zahlwert - soll["wert"]
                zeile["hinweis"] = ("+" if diff >= 0 else "−") + km_text(abs(diff))
        elif status == "stimmt":
            zeile["vor_ort_text"] = zeile["vertrag_text"]
        if art == "km" and status != "weicht ab" and km_abholung is not None:
            zeile["vor_ort_text"] = km_text(km_abholung)
            if soll.get("wert") is not None:
                diff = km_abholung - soll["wert"]
                if diff:
                    zeile["hinweis"] = ("+" if diff >= 0 else "−") + km_text(abs(diff)) + " bei Abholung"
                if abs(diff) > KM_TOLERANZ:
                    zeile["abweichend"] = True
        if (status and art != "ja_nein" and not zeile["vertrag_text"]
                and not zeile["hinweis"]):
            zeile["hinweis"] = "nicht im Vertrag"
        zeilen.append(zeile)
    return zeilen


def abweichungen(zeilen: List[dict]) -> List[dict]:
    """Nur die Zeilen, ueber die sich verhandeln laesst — mit den bisherigen
    Schluesseln (feld/status/wert, Runde 30) plus Vertrag/vor Ort."""
    raus = []
    for z in zeilen:
        if not (z["abweichend"] or z["hinweis"] == "Fahrer: unbekannt"):
            continue
        raus.append({
            "feld": z["label"], "status": z["status"], "wert": z["wert_roh"] or z["vor_ort_text"],
            "schluessel": z["schluessel"], "art": z["art"],
            "vertrag_text": z["vertrag_text"], "vor_ort_text": z["vor_ort_text"],
            "abweichend": z["abweichend"], "hinweis": z["hinweis"],
        })
    return raus
