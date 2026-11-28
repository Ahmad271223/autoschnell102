# -*- coding: utf-8 -*-
"""Dauerlauf (Wunsch Ahmad 27.11.2026): die Trefferliste.

Das Windows-Programm liest AutoPointer unbeaufsichtigt mit, sortiert an der Listenzeile vor und schickt jedes ganz
gelesene Auto hierher. Der Server prueft die Beschreibung auf Schaeden (``schaden_pruefung``), ordnet den Preis ein —
nur aus den schon vorhandenen Marktdaten, KEIN neuer Abruf, kein Tageslimit — und legt das Auto in die Trefferliste des
Kontos. Die App zeigt die Liste laufend an; „Vertrag“ fuehrt wie gewohnt ueber die Inserat-Adresse.

Hier steht nur, was sich ohne Datenbank und ohne Route pruefen laesst (Urteil, Preislage, Dokument, Antwort).
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

SAMMLUNG = "dauerlauf_treffer"

#: So lange bleibt ein Treffer liegen (danach loescht die Datenbank ihn samt Beschreibung selbst).
TREFFER_STUNDEN = max(1, int(os.environ.get("DAUERLAUF_TREFFER_STUNDEN", "48") or 48))

#: Reihenfolge = Rang in der Liste (gut zuerst).
URTEILE = ("gut", "leicht", "pruefen", "schwer")

#: Unter so vielen Vergleichsautos sagt die Preislage nichts.
PREIS_MINDESTENS = 5

_FAHRZEUG_FELDER = ("marke", "modell", "marke_modell_text", "titel", "ez_monat", "ez_jahr", "kilometer", "kw", "ps",
                    "kraftstoff", "getriebe", "tueren", "preis", "zustand", "kategorie", "quelle", "inserat_id",
                    "inserat_url")


def ablauf(ab: Optional[datetime] = None) -> datetime:
    """Loeschzeitpunkt eines Treffers (TTL-Index dauerlauf_treffer_ablauf)."""
    return (ab or datetime.now(timezone.utc)) + timedelta(hours=TREFFER_STUNDEN)


def _kurz(wert: Any, laenge: int) -> str:
    return re.sub(r"[ \t]+", " ", str(wert or "")).strip()[:laenge]


#: AutoPointer zeigt bei manchen Inseraten (v. a. AutoScout24) die HTML-Auszeichnung OHNE spitze Klammern an:
#: „…Laderaum/li/ulbrbAusstattung:/bbrulliAbgedunkelte Scheiben/liliAmbiente…“ (Bild Ahmad 28.11.2026). Ein Lauf aus
#: Tag-Namen zaehlt nur, wenn er einen Schraegstrich enthaelt und danach ein neues Wort (gross/Ziffer), Leerraum oder
#: das Textende kommt — so bleibt „km/bar“ oder „und/oder“ unberuehrt.
_HTML_REST = re.compile(r"(?:/?(?:li|ul|ol|br|b|p|strong|em))*/(?:li|ul|ol|br|b|p|strong|em)(?:/?(?:li|ul|ol|br|b|p|strong|em))*"
                        r"(?=[A-ZÄÖÜ0-9\s(\[„\"▯□]|$)")


def html_reste(text: Any) -> str:
    """Macht aus den Tag-Resten wieder Zeilen (Listenpunkte als „• “); sonst bleibt der Text, wie er ist."""
    def ersatz(m: "re.Match[str]") -> str:
        return "\n• " if m.group(0).endswith("li") else "\n"
    s = _HTML_REST.sub(ersatz, str(text or ""))
    s = re.sub(r"[ \t]*\n[ \t]*", "\n", s)
    s = re.sub(r"(?:\n•\s*)+(?=\n|$)", "", s)          # leere Listenpunkte
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def kennung(f: Dict[str, Any]) -> str:
    """Woran dasselbe Inserat wiedererkannt wird (das Programm kann ein Auto zweimal lesen): Portal + Inserat-Nummer;
    ohne Nummer die gelesenen Eckdaten."""
    quelle = _kurz(f.get("quelle"), 40).lower()
    nummer = _kurz(f.get("inserat_id"), 60) or _kurz(f.get("hash_id"), 60)
    if nummer:
        return f"{quelle}:{nummer}"
    roh = "|".join(str(f.get(k) or "") for k in ("marke_modell_text", "titel", "ez_monat", "ez_jahr", "kilometer", "preis"))
    return f"{quelle}:~{hashlib.sha256(roh.encode('utf-8')).hexdigest()[:24]}"


def urteil(schaden: Optional[Dict[str, Any]]) -> str:
    """Aus der Schadenspruefung wird EIN Wort fuer die Liste:

    * ``schwer``  — schwerer Schaden genannt (zaehlt auch aus einem nicht exakt gelesenen Text: lieber einmal zu viel);
    * ``gut``     — kein Schaden genannt UND der Text ist Buchstabe fuer Buchstabe abgeglichen;
    * ``leicht``  — nur leichte Schaeden genannt, Text exakt;
    * ``pruefen`` — alles andere: unklare Angaben, keine Beschreibung, oder der Text ist nicht exakt gelesen
      (dann koennte ein Schadenswort verlesen sein — „es darf keine Lesefehler geben“).
    """
    s = schaden or {}
    stufe = s.get("stufe")
    if stufe == "schwer":
        return "schwer"
    if not s.get("text_exakt"):
        return "pruefen"
    if stufe == "kein":
        return "gut"
    if stufe == "leicht":
        return "leicht"
    return "pruefen"


def _zahl(wert: Any) -> Optional[float]:
    try:
        z = float(wert)
    except (TypeError, ValueError):
        return None
    return z if z > 0 else None


def preislage(preis: Any, karte: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Wo liegt der Preis gegenueber den gespeicherten Marktdaten (``markt.abfrage.karte``)? None = keine Aussage
    (kein Preis, kein Segment, zu wenige Vergleichsautos).

    ``lage``: ``guenstig`` (hoechstens unteres Viertel), ``mittel`` (bis zur Mitte), ``teuer`` (darueber).
    Die Marktdaten sind Angebotspreise von mobile.de fuer dasselbe Modell in aehnlichem Alter/Kilometerstand — ein
    Anhalt, kein Gutachten; die Datenlage geht mit.
    """
    p = _zahl(preis)
    k = karte or {}
    mitte, anzahl = _zahl(k.get("median_sample_price")), int(k.get("sample_size") or 0)
    if p is None or mitte is None or anzahl < PREIS_MINDESTENS:
        return None
    viertel = _zahl(k.get("p25_price"))
    lage = "guenstig" if viertel is not None and p <= viertel else "mittel" if p <= mitte else "teuer"
    return {"lage": lage, "mitte": round(mitte), "unteres_viertel": round(viertel) if viertel is not None else None,
            "guenstigstes": round(_zahl(k.get("min_price")) or 0) or None, "anzahl": anzahl,
            "unter_mitte_eur": round(mitte - p), "segment": _kurz(k.get("label"), 120),
            "datenlage": _kurz(k.get("datenlage"), 40), "datenstand": k.get("datenstand")}


def telefon(wert: Any) -> str:
    """Nur was eine Telefonnummer sein kann (Ziffern, +, Leerzeichen, Klammern, / und -), sonst ""."""
    s = _kurz(wert, 40)
    return s if re.fullmatch(r"\+?[\d ()/\-]{6,40}", s) else ""


def email(wert: Any) -> str:
    s = _kurz(wert, 120)
    return s if re.fullmatch(r"[^@\s<>\"']+@[^@\s<>\"']+\.[A-Za-z]{2,}", s) else ""


def listenzeile(liste: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Die Zeile, wie sie in der Liste des Programms stand — fuer die Anzeige in derselben Form. Telefon und E-Mail nur,
    wenn sie wie eine Nummer bzw. Adresse aussehen (sie werden zu einem Anruf-/Mail-Knopf)."""
    liste = liste or {}
    km = liste.get("entfernung_km")
    return {
        "zeit": _kurz(liste.get("zeit"), 30), "privat": liste.get("privat"), "portal": _kurz(liste.get("portal"), 40),
        "art": _kurz(liste.get("art"), 200), "daten": _kurz(liste.get("daten"), 200),
        "ausstattung": _kurz(liste.get("ausstattung"), 200),
        "anbieter": _kurz(liste.get("anbieter"), 80),
        "telefone": [t for t in (telefon(x) for x in (liste.get("telefone") or [])[:3]) if t],
        "entfernung_km": int(km) if isinstance(km, int) and not isinstance(km, bool) and 0 <= km <= 5000 else None,
        "ort": _kurz(liste.get("ort"), 80), "email": email(liste.get("email")),
    }


def merkmal_liste(roh: Any) -> list:
    """Zeilen der Tabelle „Technische Daten“ als [{name, wert}] — hoechstens 60, ohne Leeres."""
    raus = []
    for paar in (roh or [])[:60]:
        if isinstance(paar, dict):
            name, wert = paar.get("name"), paar.get("wert")
        elif isinstance(paar, (list, tuple)) and len(paar) == 2:
            name, wert = paar
        else:
            continue
        name, wert = _kurz(name, 60).rstrip(":"), _kurz(wert, 160)
        if name and wert:
            raus.append({"name": name, "wert": wert})
    return raus


def ausstattung_liste(roh: Any) -> list:
    raus = []
    for x in (roh or [])[:120]:
        s = _kurz(x, 80)
        if s and s not in raus:
            raus.append(s)
    return raus


def merkmal(doc: Dict[str, Any], *namen: str) -> str:
    gesucht = {n.lower() for n in namen}
    for m in doc.get("merkmale") or []:
        if str(m.get("name") or "").lower() in gesucht:
            return str(m.get("wert") or "").strip()
    return ""


#: Modellangaben, mit denen ein Vertrag nichts anfangen kann („Marke, Modell: Andere“, „VW Weitere VW“)
_KEIN_MODELL = re.compile(r"^(andere[rs]?|weitere(\s.*)?|sonstige[rs]?)?$", re.IGNORECASE)


def fehlt(doc: Dict[str, Any]) -> list:
    """Welche Angaben das Inserat nicht hergab und der Sucher im Vertrag nachtragen muss (Wunsch Ahmad 27.11.2026:
    „dem Sucher sagen, dass es fehlt“). Reihenfolge wie im Vertragsformular."""
    f = doc.get("fahrzeug") or {}
    raus = []
    if _KEIN_MODELL.match(str(f.get("marke") or "").strip()):
        raus.append("Marke")
    if _KEIN_MODELL.match(str(f.get("modell") or "").strip()):
        raus.append("Modell")
    if not f.get("ez_jahr"):
        raus.append("Erstzulassung")
    if not (f.get("kraftstoff") or merkmal(doc, "Kraftstoff")):
        raus.append("Kraftstoff")
    if not (f.get("getriebe") or merkmal(doc, "Getriebeart", "Getriebe")):
        raus.append("Getriebe")
    return raus


def _ganzzahl(text: Any) -> Optional[int]:
    m = re.search(r"\d+", str(text or "").replace(".", ""))
    return int(m.group()) if m else None


def fahrzeug_fuer_vertrag(doc: Dict[str, Any], grund: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Aus einem Treffer das Fahrzeug, mit dem der Kaufvertrag vorausgefuellt wird (Feldnamen wie bei einem
    ausgelesenen Inserat: make_label, model_label, first_registration, mileage, fuel_label, features, description,
    seller_* …). ``grund`` = was ``werkzeuge.fahrzeug_zu_vehicle`` schon ergibt. Nichts wird erfunden: was fehlt,
    bleibt leer und steht in :func:`fehlt`."""
    f, liste = doc.get("fahrzeug") or {}, doc.get("liste") or {}
    v: Dict[str, Any] = dict(grund or {})

    def setze(feld: str, wert: Any) -> None:
        if wert not in (None, "", []):
            v[feld] = wert

    marke, modell = str(f.get("marke") or "").strip(), str(f.get("modell") or "").strip()
    # „Andere“ / „Weitere VW“ sind keine Angaben fuer einen Vertrag: dann bleibt das Feld leer (steht in fehlt)
    if _KEIN_MODELL.match(marke):
        v.pop("make_label", None)
    else:
        setze("make_label", marke)
    if not _KEIN_MODELL.match(modell):
        setze("model_label", modell)
    elif _KEIN_MODELL.match(str(v.get("model_label") or "")):
        v.pop("model_label", None)
    setze("title", f.get("titel"))
    setze("model_description", f.get("titel"))
    if f.get("ez_jahr"):
        monat, jahr = f.get("ez_monat"), int(f["ez_jahr"])
        setze("first_registration", f"{int(monat):02d}/{jahr}" if monat else str(jahr))
    setze("mileage", f.get("kilometer"))
    setze("power_kw", f.get("kw"))
    setze("power_ps", f.get("ps"))
    kraftstoff = f.get("kraftstoff") or merkmal(doc, "Kraftstoff")
    setze("fuel_label", kraftstoff)
    setze("fuel", kraftstoff)
    getriebe = f.get("getriebe") or merkmal(doc, "Getriebeart", "Getriebe")
    setze("gearbox_label", getriebe)
    setze("gearbox", getriebe)
    setze("doors", f.get("tueren") or merkmal(doc, "Türen"))
    setze("category_label", f.get("kategorie") or merkmal(doc, "Kategorie"))
    setze("color", merkmal(doc, "Farbe") or merkmal(doc, "Herstellerfarbe"))
    setze("seats", _ganzzahl(merkmal(doc, "Sitze")))
    setze("previous_owners", _ganzzahl(merkmal(doc, "Vorbesitzer", "Fahrzeughalter")))
    setze("displacement", merkmal(doc, "Hubraum"))
    hu = re.search(r"\d{1,2}/\d{4}", merkmal(doc, "HU"))
    setze("hu", hu.group() if hu else "")
    setze("features", list(doc.get("ausstattung") or []))
    setze("description", doc.get("beschreibung"))
    setze("list_price", f.get("preis"))
    v.setdefault("currency", "EUR")
    setze("mobile_ad_id", f.get("inserat_id"))
    setze("url", f.get("inserat_url"))
    setze("seller_name", liste.get("anbieter"))
    setze("seller_phone", (liste.get("telefone") or [""])[0])
    setze("seller_email", liste.get("email"))
    ort = re.match(r"^(?:[A-Z]{1,3}-)?(\d{4,5})\s+(.+)$", str(liste.get("ort") or "").strip())
    if ort:
        setze("seller_zip", ort.group(1))
        setze("seller_city", ort.group(2).strip())
    v.setdefault("images", [])
    return v


def dokument(user: Dict[str, Any], verbindung: Dict[str, Any], f: Dict[str, Any], liste: Optional[Dict[str, Any]],
             schaden: Dict[str, Any], lage: Optional[Dict[str, Any]], beschreibung: str, jetzt_iso: str,
             jetzt: Optional[datetime] = None, merkmale: Any = None, ausstattung: Any = None) -> Dict[str, Any]:
    """Der Eintrag in der Trefferliste. Verkaeuferdaten (Name, Telefon) liest das Programm nicht mit — sie stehen im
    Inserat, zu dem ``fahrzeug.inserat_url`` fuehrt."""
    liste = liste or {}
    return {
        "id": str(uuid.uuid4()), "kennung": kennung(f), "dealer_id": user.get("dealer_id"), "user_id": user["id"],
        "verbindung_id": verbindung.get("id"), "pc_name": verbindung.get("pc_name") or "",
        "erstellt_am": jetzt_iso, "ablauf": ablauf(jetzt),
        "fahrzeug": {k: f.get(k) for k in _FAHRZEUG_FELDER if f.get(k) not in (None, "")},
        "liste": listenzeile(liste),
        "schaden": schaden, "urteil": urteil(schaden), "preislage": lage,
        "beschreibung": html_reste(str(beschreibung or "")[:6000]),
        "merkmale": merkmal_liste(merkmale), "ausstattung": ausstattung_liste(ausstattung),
        "erledigt": False,
    }


def antwort(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Was die App je Treffer bekommt (ohne interne Felder)."""
    raus = {k: doc.get(k) for k in ("id", "erstellt_am", "fahrzeug", "liste", "schaden", "urteil", "preislage",
                                    "beschreibung", "pc_name", "erledigt")}
    raus["merkmale"], raus["ausstattung"] = doc.get("merkmale") or [], doc.get("ausstattung") or []
    raus["fehlt"] = fehlt(doc)
    raus["beschreibung"] = html_reste(raus.get("beschreibung"))     # auch bei schon gespeicherten Treffern
    return raus


def rang(doc: Dict[str, Any]) -> int:
    u = doc.get("urteil")
    return URTEILE.index(u) if u in URTEILE else len(URTEILE)
