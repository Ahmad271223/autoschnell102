# -*- coding: utf-8 -*-
"""Feste Nummern fuer Abweichungen im Abholprotokoll (Wunsch Ahmad 24.11.2026).

"Nr. 3 · Kratzer Tuer hinten links" — Buero und Fahrer reden am Telefon ueber
dieselbe Stelle. Jede Abweichung bekommt deshalb eine Nummer, die sich NICHT
verschiebt, wenn spaeter eine dazukommt oder eine wegfaellt:

  * Eine Abweichung hat einen festen Schluessel aus Art + Kennung
    ("fz:mileage_contract", "sch:<Schaden-ID>", "aus:<Ausstattung>",
    "dok:<Unterlage>", "zus:<Zustandsfeld>").
  * Die vergebenen Nummern stehen am Protokoll (abweichung_nummern, Liste
    [{"k": Schluessel, "nr": Nummer}]). Eine weggefallene Abweichung behaelt
    ihren Eintrag — die Nummer wird nie neu vergeben; kommt sie zurueck, hat
    sie wieder dieselbe Nummer.
  * Neue Abweichungen bekommen die naechste freie Nummer (hoechste + 1), in
    fester Reihenfolge: Fahrzeugdaten (Reihenfolge wie im Protokoll), neue
    Schaeden, Ausstattung, Unterlagen, Zustand.

Die Rechnung ist rein und deterministisch: Wer dasselbe Protokoll liest (Chef
in den Freigaben, Sucher im Terminplaner, Fahrer in der App, das PDF), bekommt
dieselben Nummern — auch bevor sie gespeichert sind. `sichern` haelt sie danach
fest (Compare-and-Set auf die gespeicherte Liste), damit sie stabil bleiben.

Kein FastAPI, keine feste Datenbank: routes/protocols.py, routes/abweichungen.py
und pickup_pdf_service.py nutzen es alle.
"""
from typing import Any, Dict, Iterable, List, Optional, Tuple

import protokoll_vergleich as PV

#: Gruppen in fester Reihenfolge (Ueberschriften in App und PDF).
GRUPPEN: List[Tuple[str, str]] = [
    ("fahrzeugdaten", "Fahrzeugdaten"),
    ("schaeden", "Neue Schäden"),
    ("ausstattung_fehlt", "Ausstattung fehlt"),
    ("ausstattung", "Ausstattung defekt oder anders"),
    ("unterlagen", "Unterlagen fehlen"),
    ("zustand", "Technischer Zustand"),
]
GRUPPEN_TITEL = dict(GRUPPEN)

#: Obergrenze der gespeicherten Nummern je Protokoll (Ausreisser-Schutz).
NUMMERN_MAX = 2000
#: Laenge eines Schluessels (Ausstattungsnamen sind bis 80 Zeichen, PV kuerzt).
SCHLUESSEL_MAX = 200


def _t(wert: Any) -> str:
    return str(wert).strip() if wert not in (None, "") else ""


def schaden_titel(d: dict) -> str:
    """"Kratzer · Tür hinten links" (wie routes.protocols.schaden_bezeichnung)."""
    art = _t(d.get("type_label") or d.get("label") or d.get("type_key") or d.get("type")) or "Schaden"
    ort = _t(d.get("zone") or d.get("part_label") or d.get("part"))
    return f"{art} · {ort}" if ort else art


def _schaden_schluessel(d: dict, gesehen: Dict[str, int]) -> str:
    sid = _t(d.get("id"))
    if sid:
        basis = "sch:" + sid
    else:
        basis = "sch:" + "|".join((_t(d.get("type_key") or d.get("type_label") or d.get("type")),
                                   _t(d.get("view")), _t(d.get("zone"))))
    n = gesehen.get(basis, 0)
    gesehen[basis] = n + 1
    return (basis if n == 0 else f"{basis}#{n + 1}")[:SCHLUESSEL_MAX]


def _fz_text(z: dict) -> str:
    vertrag = z.get("vertrag_text") or ""
    vor_ort = z.get("vor_ort_text") or ""
    # ohne Pfeil: das unterschriebene PDF zeigt "→" nur neben "weicht ab" (Runde 33, test_23)
    text = f"laut Vertrag: {vertrag or 'nicht im Vertrag'}, vor Ort: {vor_ort or '—'}"
    hinweis = z.get("hinweis") or ""
    return f"{text} ({hinweis})" if hinweis and hinweis != "nicht im Vertrag" else text


def eintraege_sammeln(zeilen: Optional[List[dict]], vor_ort: Optional[dict],
                      neue_schaeden: Optional[Iterable[Any]]) -> List[dict]:
    """Alle echten Abweichungen in fester Reihenfolge, noch ohne Nummer.

    Gezaehlt wird, worueber sich verhandeln laesst: abweichende Fahrzeugdaten
    (wie PV.abweichungen), neue Schaeden, fehlende/defekte/andere Ausstattung,
    fehlende Unterlagen und Maengel am Zustand. "keine Angabe" und reine
    Hinweise (z. B. "Sauberkeit: mittel") sind keine Abweichung."""
    raus: List[dict] = []
    for a in PV.abweichungen(list(zeilen or [])):
        raus.append({"schluessel": "fz:" + a["schluessel"], "gruppe": "fahrzeugdaten",
                     "titel": a["feld"], "text": _fz_text(a)})
    gesehen: Dict[str, int] = {}
    for d in neue_schaeden or []:
        if not isinstance(d, dict):
            continue
        text = ""
        if d.get("view") == "technik" and _t(d.get("note")):
            text = _t(d.get("note"))[:200]
        raus.append({"schluessel": _schaden_schluessel(d, gesehen), "gruppe": "schaeden",
                     "titel": schaden_titel(d), "text": text or "neu vor Ort festgestellt"})
    v = vor_ort or {}
    for x in v.get("ausstattung") or []:
        art = x.get("art")
        if art in ("offen", "hinweis"):
            continue
        raus.append({"schluessel": ("aus:" + _t(x.get("name")))[:SCHLUESSEL_MAX],
                     "gruppe": "ausstattung_fehlt" if art == "fehlt" else "ausstattung",
                     "titel": _t(x.get("name")), "text": _t(x.get("befund"))})
    for x in v.get("dokumente") or []:
        if x.get("art") in ("offen", "hinweis"):
            continue
        raus.append({"schluessel": ("dok:" + _t(x.get("name")))[:SCHLUESSEL_MAX], "gruppe": "unterlagen",
                     "titel": _t(x.get("name")), "text": _t(x.get("befund")) or "fehlt"})
    for x in v.get("zustand") or []:
        if x.get("art") in ("offen", "hinweis"):
            continue
        raus.append({"schluessel": ("zus:" + _t(x.get("schluessel") or x.get("name")))[:SCHLUESSEL_MAX],
                     "gruppe": "zustand", "titel": _t(x.get("name")), "text": _t(x.get("befund"))})
    # doppelte Schluessel (z. B. zwei gleich benannte Ausstattungen) nur einmal
    einmal: Dict[str, dict] = {}
    for e in raus:
        einmal.setdefault(e["schluessel"], e)
    return list(einmal.values())


def gespeicherte_nummern(roh: Any) -> Dict[str, int]:
    """abweichung_nummern vom Protokoll lesen — kaputte Eintraege fallen still raus."""
    raus: Dict[str, int] = {}
    if not isinstance(roh, list):
        return raus
    for e in roh:
        if not isinstance(e, dict):
            continue
        k, nr = e.get("k"), e.get("nr")
        if isinstance(k, str) and k and isinstance(nr, int) and not isinstance(nr, bool) and nr > 0 \
                and k not in raus and nr not in raus.values():
            raus[k] = nr
    return raus


def nummern_vergeben(gespeichert: Dict[str, int], schluessel: Iterable[str]) -> Tuple[Dict[str, int], bool]:
    """Bekannte Schluessel behalten ihre Nummer; neue bekommen in der
    uebergebenen Reihenfolge hoechste + 1. Weggefallene bleiben im Ergebnis
    (reserviert). Rueckgabe: (alle Nummern, neu vergeben?)."""
    alle = dict(gespeichert)
    naechste = max(alle.values(), default=0) + 1
    neu = False
    for k in schluessel:
        if k in alle:
            continue
        if len(alle) >= NUMMERN_MAX:
            break
        alle[k] = naechste
        naechste += 1
        neu = True
    return alle, neu


def als_liste(nummern: Dict[str, int]) -> List[Dict[str, Any]]:
    return [{"k": k, "nr": nr} for k, nr in sorted(nummern.items(), key=lambda x: x[1])]


def nummerieren(doc: Optional[dict], eintraege: List[dict]) -> Dict[str, Any]:
    """Nummern an die Eintraege haengen und in Gruppen ordnen.

    Liefert {"anzahl", "eintraege" (nach Nummer), "gruppen" [{"key", "titel",
    "eintraege"}], "speichern" (neue Liste fuer abweichung_nummern oder None)}."""
    gespeichert = gespeicherte_nummern((doc or {}).get("abweichung_nummern"))
    nummern, neu = nummern_vergeben(gespeichert, [e["schluessel"] for e in eintraege])
    liste = []
    for e in eintraege:
        nr = nummern.get(e["schluessel"])
        if nr is None:            # NUMMERN_MAX erreicht — ohne Nummer zeigen
            continue
        liste.append({**e, "nr": nr, "gruppe_titel": GRUPPEN_TITEL.get(e["gruppe"], "")})
    liste.sort(key=lambda e: e["nr"])
    gruppen = []
    for key, titel in GRUPPEN:
        teil = [e for e in liste if e["gruppe"] == key]
        if teil:
            gruppen.append({"key": key, "titel": titel, "eintraege": teil})
    return {"anzahl": len(liste), "eintraege": liste, "gruppen": gruppen,
            "speichern": als_liste(nummern) if neu else None}


def ausstattung_vorlage(vehicle: Optional[dict]) -> Any:
    feats = (vehicle or {}).get("features")
    return feats[:PV.AUSSTATTUNG_MAX] if isinstance(feats, list) else feats


def berechnen(doc: Optional[dict], vehicle: Optional[dict], contract: Optional[dict], *,
              felder, condition_fields, document_items) -> Dict[str, Any]:
    """Alles in einem: Vergleich Vertrag/vor Ort, Befunde vor Ort, Nummern."""
    doc = PV.json_sicher(doc or {})
    werte = PV.vertragswerte(vehicle or {}, contract or {})
    zeilen = PV.vergleich(felder, doc.get("vehicle_check") or {}, doc.get("condition") or {}, werte)
    vor_ort = PV.vor_ort_befunde(doc, condition_fields=condition_fields, document_items=document_items,
                                 ausstattung=ausstattung_vorlage(vehicle))
    return nummerieren(doc, eintraege_sammeln(zeilen, vor_ort, doc.get("new_damages")))


def fuer_antwort(ergebnis: Dict[str, Any]) -> Dict[str, Any]:
    """Ohne den internen Speicher-Vorschlag (geht nicht an App/Browser)."""
    return {k: v for k, v in (ergebnis or {}).items() if k != "speichern"}


async def sichern(dbx, doc: Optional[dict], ergebnis: Dict[str, Any]) -> None:
    """Neue Nummern und die Anzahl festhalten — best effort.

    Compare-and-Set auf die gelesene Liste: hat inzwischen jemand anders
    gespeichert, gilt dessen Stand (der naechste Abruf rechnet darauf weiter).
    Die Anzahl steht zusaetzlich am Termin (protokoll_abweichungen) — fuer den
    Klick auf die Zahl in der Terminliste, ohne jedes Protokoll neu zu rechnen."""
    if not doc or not doc.get("id"):
        return
    neu = ergebnis.get("speichern")
    anzahl = int(ergebnis.get("anzahl") or 0)
    if neu is not None:
        alt = doc.get("abweichung_nummern")
        filt: Dict[str, Any] = {"id": doc["id"]}
        filt["abweichung_nummern"] = alt if isinstance(alt, list) else {"$exists": False}
        await dbx.pickup_protocols.update_one(filt, {"$set": {"abweichung_nummern": neu,
                                                                "abweichung_anzahl": anzahl}})
    elif doc.get("abweichung_anzahl") != anzahl:
        await dbx.pickup_protocols.update_one({"id": doc["id"]}, {"$set": {"abweichung_anzahl": anzahl}})
    else:
        return
    if doc.get("appointment_id"):
        await dbx.appointments.update_one({"id": doc["appointment_id"], "dealer_id": doc.get("dealer_id")},
                                          {"$set": {"protokoll_abweichungen": anzahl}})
