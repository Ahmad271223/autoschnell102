# -*- coding: utf-8 -*-
"""Suchauftraege (Auftrag v3, 26.09.2026): der Super-Admin legt Marktanalysen
selbst an — Marke, Modell, Variante, Filter, EZ-Jahre, km-Bereiche, Zeilen,
Frequenz. Hier: Pruefung/Normalisierung eines Entwurfs, deterministische
Kostenprognose (keine KI), Testlauf mit wenigen Treffern, Anlegen/Aendern/
Duplizieren/Archivieren. Archivieren loescht NIE Historie."""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from typing import Any, Dict, List, Optional

from markt import apify, katalog, konfig, normalisieren, segmente, url
from markt.konfig import JOBS, MODELLE

STATUS = ("active", "paused", "archived")
KRAFTSTOFFE = ("", "PETROL", "DIESEL", "HYBRID", "HYBRID_DIESEL", "ELECTRICITY", "LPG", "CNG")
GETRIEBE = ("", "MANUAL_GEAR", "AUTOMATIC_GEAR", "SEMIAUTOMATIC_GEAR")
# Reparaturwelle 6 Nr. 84: intern nur DEALER/PRIVATE (mobile.de-URL uebersetzt PRIVATE -> FSBO in url.py);
# alte Eingaben/Dokumente mit FSBO werden beim Pruefen und beim Synchronisieren normalisiert
VERKAEUFER = ("", "DEALER", "PRIVATE")
# Review 26.09.2026 abends P7: Karosserie als mobile.de-Code (c=), "" = alle
KAROSSERIE = ("",) + normalisieren.KAROSSERIE_CODES
ROWS_MAX = 100
# P4: materielle Merkmale eines Suchauftrags — aendert sich eines, mischen sich alte und neue
# Historie; deshalb bekommt der Auftrag eine neue Fassung (version) mit eigenen Segment-IDs.
# Reparaturwelle 6 Nr. 124: auch die Zeilenzahl (rows) ist materiell — ein Median der 10 guenstigsten
# ist ein anderer Wert als der der 20 guenstigsten. NICHT materiell: crawls_per_day, ez_years,
# km_buckets, label, status, notiz, priority.
FILTER_FELDER = ("make_id", "model_id", "fuel", "gearbox", "body", "power_kw_min", "power_kw_max",
                 "country", "zip", "radius_km", "seller_type")
DEFINITION_FELDER = FILTER_FELDER + ("rows",)
# Fassung des Fingerabdrucks (Migration im Sync-Pfad, segmente._hashes_heben): 2 = rows im Hash, Land 'DE'
# als Vorgabe, Verkaeuferart normalisiert
HASH_FASSUNG = 2


def _fingerabdruck(m: Dict[str, Any], felder: tuple) -> str:
    werte = {}
    for k in felder:
        w = m.get(k)
        if k == "country":
            werte[k] = (str(w).strip().upper()[:2] if w not in (None, "") else "DE") or "DE"
        elif k == "rows" and w in (None, ""):
            werte[k] = int(konfig.rows_je_segment())         # ohne Angabe gilt die Vorbelegung (wie in synchronisieren)
        elif w in (None, ""):
            werte[k] = None
        elif k in ("power_kw_min", "power_kw_max", "radius_km", "rows"):
            try:
                werte[k] = int(w)
            except (TypeError, ValueError):
                werte[k] = str(w)
        elif k == "seller_type":
            # Nr. 84: FSBO und PRIVATE sind dieselbe Verkaeuferart — derselbe Fingerabdruck
            s = str(w).strip().upper()
            werte[k] = normalisieren.VERKAEUFER_CODES.get(s, s)
        else:
            werte[k] = str(w).strip().upper() if k in ("fuel", "gearbox") else str(w).strip()
    return hashlib.sha256(json.dumps(werte, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()[:24]


def definition_hash(m: Dict[str, Any]) -> str:
    """Stabiler Fingerabdruck der Fassung: materielle Merkmale + Zeilenzahl (leer und None gleich)."""
    return _fingerabdruck(m, DEFINITION_FELDER)


def filter_hash(m: Dict[str, Any]) -> str:
    """Fingerabdruck der FILTER (ohne Zeilenzahl) — dafuer gilt ein bestandener Testlauf (Nr. 65/124):
    der Testlauf prueft, ob mobile.de die Filter respektiert; die Zeilenzahl aendert daran nichts."""
    return _fingerabdruck(m, FILTER_FELDER)


class Ungueltig(ValueError):
    pass


class Konflikt(Ungueltig):
    """Nr. 130: der Auftrag wurde inzwischen von jemand anderem geaendert (CAS) -> 409."""


def _int(w: Any, name: str, unten: int, oben: int) -> int:
    try:
        z = int(str(w).replace(".", "").replace(" ", ""))
    except (TypeError, ValueError):
        raise Ungueltig(f"{name}: keine Zahl")
    if z < unten or z > oben:
        raise Ungueltig(f"{name}: {z} liegt außerhalb {unten}–{oben}")
    return z


def slug(*teile: str) -> str:
    s = "-".join(str(t or "") for t in teile).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or uuid.uuid4().hex[:8]


def km_bereiche_pruefen(roh: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """min < max, keine negativen Werte, keine Ueberschneidungen, sortiert."""
    if not roh:
        raise Ungueltig("mindestens ein km-Bereich")
    if len(roh) > 12:
        raise Ungueltig("höchstens 12 km-Bereiche")
    sauber = []
    for b in roh:
        mn = _int(b.get("min_km", b.get("min")), "km von", 0, 2000000)
        mx = _int(b.get("max_km", b.get("max")), "km bis", 1, 2000000)
        if mn >= mx:
            raise Ungueltig(f"km-Bereich {mn}–{mx}: von muss kleiner als bis sein")
        sauber.append({"min_km": mn, "max_km": mx})
    sauber.sort(key=lambda b: b["min_km"])
    for a, b in zip(sauber, sauber[1:]):
        if b["min_km"] <= a["max_km"]:
            raise Ungueltig(f"km-Bereiche überschneiden sich: {a['min_km']}–{a['max_km']} und {b['min_km']}–{b['max_km']}")
    return sauber


def ez_jahre_pruefen(roh: Any, von: Any = None, bis: Any = None) -> List[int]:
    """Liste einzelner Jahre (Multi-Select) oder von–bis; jedes Jahr wird ein eigenes Segment."""
    jahre: List[int] = []
    if von or bis:
        v = _int(von or bis, "EZ von", 1980, 2100)
        b = _int(bis or von, "EZ bis", 1980, 2100)
        if b < v:
            raise Ungueltig("EZ bis liegt vor EZ von")
        jahre = list(range(v, b + 1))
    else:
        for j in roh or []:
            jahre.append(_int(j, "EZ-Jahr", 1980, 2100))
    jahre = sorted(set(jahre))
    if not jahre:
        raise Ungueltig("mindestens ein EZ-Jahr")
    if len(jahre) > 15:
        raise Ungueltig("höchstens 15 EZ-Jahre")
    return jahre


def entwurf_pruefen(e: Dict[str, Any], *, bestehend: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Formular -> gueltiges market_models-Dokument (ohne id/Zeitstempel).
    Marke/Modell werden ueber den mobile.de-Katalog aufgeloest (Name oder ID)."""
    marke = str(e.get("make") or e.get("marke") or "").strip()
    modell = str(e.get("model") or e.get("modell") or "").strip()
    variante = str(e.get("variant") or e.get("variante") or "").strip()[:80]
    if not marke or not modell:
        raise Ungueltig("Marke und Modell sind Pflicht")
    # Reparaturwelle 5 Nr. 66: die Variante ist Pflicht (sie ist der Anzeigename der Marktanalyse)
    if not variante:
        raise Ungueltig("Variante / Motorisierung ist Pflicht (z. B. 320d)")
    ids = katalog.modell_ids(marke, modell)
    if not ids:
        raise Ungueltig(f"Modell „{modell}“ der Marke „{marke}“ nicht im mobile.de-Katalog — bitte aus der Liste wählen")
    fuel = str(e.get("fuel") or "").upper().strip()
    if fuel not in KRAFTSTOFFE:
        raise Ungueltig(f"Kraftstoff {fuel} unbekannt")
    gearbox = str(e.get("gearbox") or "").upper().strip()
    if gearbox not in GETRIEBE:
        raise Ungueltig(f"Getriebe {gearbox} unbekannt")
    seller = str(e.get("seller_type") or "").upper().strip()
    seller = normalisieren.VERKAEUFER_CODES.get(seller, seller)          # Nr. 84: FSBO/PRIVAT -> PRIVATE, HAENDLER -> DEALER
    if seller not in VERKAEUFER:
        raise Ungueltig("Verkäuferart unbekannt")
    # P7: Karosserie als Code; Beschriftungen aus dem alten Freitextfeld ("Kombi", "SUV") werden zugeordnet
    body_roh = str(e.get("body") or "").strip()
    body = body_roh if body_roh in KAROSSERIE else (normalisieren.karosserie_code(body_roh) or "?")
    if body not in KAROSSERIE:
        raise Ungueltig(f"Karosserie „{body_roh}“ unbekannt — Limousine, Kombi, SUV, Cabrio, Coupé, Kleinwagen oder Van")
    kw_von = _int(e["power_kw_min"], "kW von", 1, 2000) if e.get("power_kw_min") not in (None, "") else None
    kw_bis = _int(e["power_kw_max"], "kW bis", 1, 2000) if e.get("power_kw_max") not in (None, "") else None
    if kw_von and kw_bis and kw_bis < kw_von:
        raise Ungueltig("kW bis liegt unter kW von")
    # Nr. 5: die Freitext-Variante filtert bei mobile.de NICHTS (Probelaeufe 26.09.2026) — sie ist nur
    # Beschriftung. Ohne Kraftstoff, Getriebe, kW-Bereich oder Karosserie waere "320d" ein Auftrag
    # ueber ALLE 3er. Deshalb mindestens eine einschraenkende Angabe.
    if not (fuel or gearbox or kw_von or kw_bis or body):
        raise Ungueltig("Variante braucht Kraftstoff/Getriebe/kW/Karosserie als Filter — die Variante allein filtert bei mobile.de nicht")
    jahre = ez_jahre_pruefen(e.get("ez_years"), e.get("ez_from"), e.get("ez_to"))
    km = km_bereiche_pruefen(e.get("km_buckets") or [])
    rows = _int(e.get("rows") or konfig.rows_je_segment(), "Zeilen je Segment", 1, ROWS_MAX)
    crawls = _int(e.get("crawls_per_day") or 1, "Abrufe je Tag", 1, 4)
    status = str(e.get("status") or (bestehend or {}).get("status") or "paused")
    if status not in STATUS:
        raise Ungueltig("Status unbekannt")
    country = str(e.get("country") or "DE").strip().upper()[:2] or "DE"
    zip_code = str(e.get("zip") or "").strip()[:10]
    radius = _int(e.get("radius_km"), "Radius", 1, 2000) if e.get("radius_km") not in (None, "") else None
    # Reparaturwelle 6 Nr. 142: PLZ und Radius nur zusammen (eines allein wurde still ignoriert); DE: 5 Ziffern
    if bool(zip_code) != bool(radius):
        raise Ungueltig("PLZ und Radius gehören zusammen — beides angeben oder beides leer lassen")
    if zip_code and country == "DE" and not re.fullmatch(r"\d{5}", zip_code):
        raise Ungueltig(f"PLZ „{zip_code}“ ungültig — in Deutschland 5 Ziffern")
    label = str(e.get("label") or "").strip() or f"{ids['make_name']} {variante or ids['model_name']}".strip()
    return {"make": ids["make_name"], "model": ids["model_name"], "variant": variante, "label": label[:120],
            "make_id": ids["make_id"], "model_id": ids["model_id"], "fuel": fuel or None, "gearbox": gearbox or None,
            "body": body or None, "seller_type": seller or None,
            "country": country, "zip": zip_code or None,
            "radius_km": radius, "power_kw_min": kw_von, "power_kw_max": kw_bis,
            "ez_years": jahre, "km_buckets": km, "rows": rows, "crawls_per_day": crawls,
            "status": status, "enabled": status == "active", "priority": _int(e.get("priority") or 5, "Priorität", 1, 9),
            "notiz": str(e.get("notiz") or "").strip()[:500]}


# ---------------------------------------------------------------- Prognose
def prognose_modell(m: Dict[str, Any]) -> Dict[str, Any]:
    """Reparaturwelle 5 Nr. 17: abgerufen werden rows + Puffer je Segment (konfig.zeilen_mit_puffer),
    gespeichert hoechstens rows — rows_tag/rows_monat sind die gespeicherten Zeilen, die Kosten
    rechnen mit den abgerufenen (rows_abruf_tag)."""
    ez = segmente.ez_buckets_fuer_modell_liste(m)
    km = m.get("km_buckets") or konfig.KM_BUCKETS_STANDARD
    rows = int(m.get("rows") or konfig.rows_je_segment())
    abruf = konfig.zeilen_mit_puffer(rows)
    k = int(m.get("crawls_per_day") or 1)
    seg = len(ez) * len(km)
    rows_tag = seg * rows * k
    rows_abruf_tag = seg * abruf * k
    laeufe_tag = math.ceil(seg * k / max(1, konfig.buendel_groesse())) if seg else 0
    kosten_tag = konfig.kosten_buendel_usd(konfig.actor(), laeufe_tag, rows_abruf_tag)
    # Review 26.09.2026 Nr. 53: Obergrenze, wenn ALLES ueber den Ersatz-Scraper liefe
    # (der laeuft je URL einzeln: ein Start je Segment-Abruf, teurere Zeilen)
    ersatz = konfig.actor_ersatz()
    kosten_tag_ersatz = konfig.kosten_buendel_usd(ersatz, seg * k, rows_abruf_tag) if ersatz and seg else 0.0
    return {"segmente": seg, "ez_jahre": len(ez), "km_bereiche": len(km), "rows": rows, "rows_abruf": abruf, "crawls_per_day": k,
            "rows_tag": rows_tag, "rows_monat": round(rows_tag * 30.4), "rows_abruf_tag": rows_abruf_tag, "laeufe_tag": laeufe_tag,
            "kosten_tag_usd": round(kosten_tag, 2), "kosten_monat_usd": round(kosten_tag * 30.4, 2),
            "kosten_tag_ersatz_usd": round(kosten_tag_ersatz, 2), "kosten_monat_ersatz_usd": round(kosten_tag_ersatz * 30.4, 2),
            "ersatz_actor": ersatz or None}


async def prognose(db, entwurf: Optional[Dict[str, Any]] = None, *, ohne_id: Optional[str] = None) -> Dict[str, Any]:
    """Alle aktiven Marktanalysen (+ optional ein ungespeicherter Entwurf, ggf.
    statt des Modells `ohne_id`) -> Segmente, Zeilen/Tag, Zeilen/Monat, Kosten,
    Vergleich mit dem Monatsbudget. Deterministisch."""
    from markt import budget, jobs
    aktive = await db[MODELLE].find({"status": "active"}, {"_id": 0}).to_list(5000)
    if ohne_id:
        aktive = [m for m in aktive if m.get("id") != ohne_id]
    teile = [prognose_modell(m) for m in aktive]
    e = prognose_modell(entwurf) if entwurf and entwurf.get("status", "active") == "active" else None
    alle = teile + ([e] if e else [])
    summe = {k: sum(t[k] for t in alle) for k in ("segmente", "rows_tag", "rows_monat", "rows_abruf_tag", "laeufe_tag", "kosten_tag_usd",
                                                   "kosten_monat_usd", "kosten_tag_ersatz_usd", "kosten_monat_ersatz_usd")}
    # Reparaturwelle 6 Nr. 133: Buendel enthalten nur Segmente gleicher Zeilenzahl — Actor-Starts je Tag sind
    # die Summe ueber die Gruppen ceil(Laeufe der Gruppe / Buendelgroesse), nicht je Auftrag einzeln gerundet
    gruppen: Dict[int, int] = {}
    for t in alle:
        gruppen[int(t["rows"])] = gruppen.get(int(t["rows"]), 0) + int(t["segmente"]) * int(t["crawls_per_day"])
    starts = jobs.starts_je_gruppe(gruppen, konfig.buendel_groesse())
    summe["laeufe_tag"] = starts
    summe["kosten_tag_usd"] = konfig.kosten_buendel_usd(konfig.actor(), starts, int(summe["rows_abruf_tag"]))
    summe["kosten_monat_usd"] = round(summe["kosten_tag_usd"] * 30.4, 4)
    # Welle 5 Nr. 38: die Entfernungspruefung (max. je Tag x (Start + 1 Zeile)) gehoert zu den Tageskosten
    entfernung_tag = konfig.entfernung_kosten_je_tag_usd()
    summe["kosten_tag_usd"] = round(summe["kosten_tag_usd"] + entfernung_tag, 4)
    summe["kosten_monat_usd"] = round(summe["kosten_monat_usd"] + entfernung_tag * 30.4, 4)
    summe["kosten_tag_ersatz_usd"] = round(summe["kosten_tag_ersatz_usd"] + entfernung_tag, 4)
    summe["kosten_monat_ersatz_usd"] = round(summe["kosten_monat_ersatz_usd"] + entfernung_tag * 30.4, 4)
    b = await budget.dokument(db)
    budget_usd = float(b.get("budget_usd") or 0)
    frei = float(b.get("frei_usd") or 0)
    # Nr. 141: die Warnung vergleicht die RESTKOSTEN des laufenden Monats (Tageskosten x Resttage) mit dem
    # freien Budget — vorher nur die 30,4-Tage-Prognose mit dem vollen Monatsbudget (Verbrauch ignoriert)
    rest_tage = konfig.rest_tage_im_monat()
    restkosten = round(summe["kosten_tag_usd"] * rest_tage, 4)
    return {"aktive_modelle": len(aktive) + (1 if e else 0), **{k: round(v, 2) for k, v in summe.items()},
            "entwurf": e, "budget_usd": budget_usd, "verbraucht_usd": round(float(b.get("used_usd") or 0), 2),
            "verbleibend_usd": round(frei, 2), "rest_tage": rest_tage, "restkosten_usd": round(restkosten, 2),
            "starts_je_tag": starts, "zeilen_gruppen": len(gruppen),
            "entfernung_tag_usd": round(entfernung_tag, 2), "entfernung_monat_usd": round(entfernung_tag * 30.4, 2),
            "ueberschritten": budget_usd > 0 and restkosten > frei,
            "ueberschritten_monat": budget_usd > 0 and summe["kosten_monat_usd"] > budget_usd,
            # Nr. 53: Ersatz-Scraper wuerde das Budget sprengen (nur Hinweis, kein Sperrgrund)
            "ersatz_ueberschritten": budget_usd > 0 and summe["kosten_monat_ersatz_usd"] > budget_usd,
            "ersatz_actor": konfig.actor_ersatz() or None,
            "preise": {"start_usd": konfig.preise_je_actor(konfig.actor())[0], "row_usd": konfig.preise_je_actor(konfig.actor())[1],
                       "buendel": konfig.buendel_groesse(), "actor": konfig.actor(),
                       "puffer_faktor": konfig.PUFFER_FAKTOR, "puffer_max": konfig.PUFFER_MAX}}


# ---------------------------------------------------------------- Testlauf
# Welle 5 Nr. 19: 40 Segmente je Testlauf — die 30 Standard-Segmente (5 EZ x 6 km) passen in EINEN Lauf
TESTLAUF_SEGMENTE_MAX = 40
TESTLAUF_JE_SEGMENT = 2


def _kontext(it: Dict[str, Any]) -> Optional[str]:
    ctx = it.get("inputContext")
    if isinstance(ctx, str):
        return ctx
    if isinstance(ctx, dict):
        return ctx.get("url") or ctx.get("startUrl")
    return None


def _zeile(l: Dict[str, Any], seg: Dict[str, Any], grund: str = "") -> Dict[str, Any]:
    jahr = re.search(r"(\d{4})", l.get("first_registration") or "")
    return {"title": l.get("title"), "make": l.get("make"), "model": l.get("model"), "variant": l.get("variant"),
            "first_registration": l.get("first_registration"), "mileage_km": l.get("mileage_km"),
            "price_gross": l.get("price_gross"), "power_kw": l.get("power_kw"), "fuel": l.get("fuel"),
            "gearbox": l.get("gearbox"), "url": l.get("url"),
            "ez_ok": bool(jahr) and int(jahr.group(1)) == seg["year_from"],
            "km_ok": l.get("mileage_km") is not None and seg["min_km"] <= int(l["mileage_km"]) <= seg["max_km"],
            "gueltig": not grund, "grund": grund or None}


def testlauf_bestanden(erg: Dict[str, Any]) -> bool:
    """Nr. 65: bestanden = mindestens ein gueltiger Treffer und keine Filterfehler (keine Zeile vom
    Zeilenfilter verworfen). Welle 6 Nr. 79: und in keinem Segment eine ungueltige Sortierung
    (Positionsnummern mit Luecken — der Worker wuerde den Lauf als 'data_invalid' verwerfen)."""
    return (int(erg.get("gueltig_gesamt") or 0) >= 1 and int(erg.get("verworfen_gesamt") or 0) == 0
            and int(erg.get("sortierung_ungueltig") or 0) == 0)


async def testlauf(entwurf: Dict[str, Any], n: int = 5, *, db=None) -> Dict[str, Any]:
    """Review 26.09.2026 Nr. 39: EIN Buendel-Lauf ueber ALLE Segmente des Entwurfs
    (hoechstens TESTLAUF_SEGMENTE_MAX) mit je 2 Treffern — zeigt je Segment, ob der Filter
    stimmt und ob es leer ist, bevor einen Monat lang falsche Daten laufen. `n` begrenzt
    nur die angezeigten Zeilen des ersten Segments.
    Welle 5 Nr. 18: mit `db` wird der Lauf gegen das Marktbudget reserviert und abgerechnet.
    Nr. 20: derselbe Zeilenfilter wie im Worker (passt_zum_segment) — je Segment
    geliefert/gueltig/verworfen (mit Grund). Nr. 65: testlauf_ok_at/testlauf_ok_hash, wenn
    bestanden — das Formular schickt sie beim Aktivieren mit."""
    from markt import budget
    m = entwurf_pruefen(entwurf)
    alle: List[Dict[str, Any]] = []
    for jahr in m["ez_years"]:
        for b in m["km_buckets"]:
            alle.append({"id": f"test:{jahr}:{b['min_km']}-{b['max_km']}", "min_km": b["min_km"], "max_km": b["max_km"],
                         "year_from": jahr, "year_to": jahr, "label": f"EZ {jahr} · {segmente.km_text(b)}"})
    segs = alle[:TESTLAUF_SEGMENTE_MAX]
    urls = [url.such_url(s, m) for s in segs]
    n_anzeige = max(1, min(int(n), 10))
    max_items = TESTLAUF_JE_SEGMENT * len(urls) if len(urls) > 1 else n_anzeige
    res = None
    if db is not None:
        res = await budget.reservieren(db, konfig.kosten_je_lauf_usd(konfig.actor(), max_items))
        if res is None:
            raise Ungueltig("Monatsbudget des Market-Crawlers aufgebraucht — kein Testlauf")
    try:
        if len(urls) > 1:
            r = await apify.lauf(urls, max_items, max_items_per_query=TESTLAUF_JE_SEGMENT)
        else:
            r = await apify.lauf(urls, n_anzeige)
    except apify.ApifyFehler as e:
        if res is not None:
            gelaufen = e.art in ("zeit", "ausfall", "poll")
            await budget.abrechnen(db, res, (e.usd if gelaufen else 0.0), 0, gelaufen=gelaufen, runs=1 if (gelaufen and e.run_id) else 0)
        raise
    except Exception:
        if res is not None:
            await budget.abrechnen(db, res, None, 0)
        raise
    if res is not None:
        await budget.abrechnen(db, res, r.get("usd"), len(r.get("items") or []), runs=int(r.get("laeufe") or 1))
    # Zuordnung Zeile -> Segment wie im Worker: ein Segment = alles, mehrere NUR ueber inputContext
    je_url: Dict[str, List[dict]] = {u: [] for u in urls}
    nicht_zuordenbar = 0
    if len(urls) == 1:
        je_url[urls[0]] = list(r["items"])
    else:
        for it in r["items"]:
            key = _kontext(it)
            if key in je_url:
                je_url[key].append(it)
            else:
                nicht_zuordenbar += 1          # Nr. 81: zaehlen und zeigen, nie raten
    ergebnis_segmente = []
    erste_zeilen: List[Dict[str, Any]] = []
    erste_ls: List[Dict[str, Any]] = []
    gueltig_gesamt = verworfen_gesamt = geliefert_gesamt = 0
    sortierung_ungueltig = 0
    for i, (s, u) in enumerate(zip(segs, urls)):
        roh = je_url[u]
        # Nr. 79: Sortierung und Top-N-Nachweis JE SEGMENT (vorher nur fuer das erste), wie im Worker auf den Rohzeilen
        nachweis, nachweis_grund = normalisieren.top_n_nachweis(roh)
        ls = normalisieren.listings_aus_items(roh)
        zeilen, gueltige, gruende = [], [], []
        for l in ls:
            ok, grund = normalisieren.passt_zum_segment(l, s, m)
            zeilen.append(_zeile(l, s, "" if ok else grund))
            if ok:
                gueltige.append(l)
            else:
                gruende.append(grund)
        sortiert = normalisieren.preise_aufsteigend(gueltige)
        if ls and (nachweis == normalisieren.SORTIERUNG_UNGUELTIG or not sortiert):
            sortierung_ungueltig += 1
        geliefert_gesamt += len(ls)
        gueltig_gesamt += len(gueltige)
        verworfen_gesamt += len(gruende)
        ergebnis_segmente.append({"label": s["label"], "anzahl": len(zeilen), "geliefert": len(ls), "gueltig": len(gueltige),
                                  "verworfen": len(gruende), "gruende": gruende[:3],
                                  "ez_ok": all(z["ez_ok"] for z in zeilen) if zeilen else None,
                                  "km_ok": all(z["km_ok"] for z in zeilen) if zeilen else None,
                                  "sortiert": sortiert if ls else None, "nachweis": nachweis if ls else None,
                                  "nachweis_grund": (nachweis_grund or None) if ls else None})
        if i == 0:
            erste_ls, erste_zeilen = ls[:n_anzeige], zeilen[:n_anzeige]
    erg = {"url": urls[0], "segment": segs[0]["label"], "anzahl": len(erste_zeilen),
           "sortiert": normalisieren.preise_aufsteigend(erste_ls),
           "alle_ez_ok": all(z["ez_ok"] for z in erste_zeilen) if erste_zeilen else None,
           "alle_km_ok": all(z["km_ok"] for z in erste_zeilen) if erste_zeilen else None,
           "usd": r.get("usd"), "dauer_ms": r.get("dauer_ms"), "actor": r.get("actor"), "zeilen": erste_zeilen,
           "segmente": ergebnis_segmente, "leer": sum(1 for s in ergebnis_segmente if s["anzahl"] == 0),
           "segmente_geprueft": len(segs), "segmente_gesamt": len(alle), "segmente_max": TESTLAUF_SEGMENTE_MAX,
           "geliefert_gesamt": geliefert_gesamt, "gueltig_gesamt": gueltig_gesamt, "verworfen_gesamt": verworfen_gesamt,
           "nicht_zuordenbar": nicht_zuordenbar, "sortierung_ungueltig": sortierung_ungueltig,
           "definition_hash": definition_hash(m), "filter_hash": filter_hash(m)}
    erg["bestanden"] = testlauf_bestanden(erg)
    erg["testlauf_ok_at"] = konfig.jetzt_iso() if erg["bestanden"] else None
    # Nr. 124: der Nachweis gilt fuer die FILTER (ohne Zeilenzahl)
    erg["testlauf_ok_hash"] = erg["filter_hash"] if erg["bestanden"] else None
    return erg


def _testlauf_pruefen(m: Dict[str, Any], e: Dict[str, Any], alt: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Nr. 65: fuer den aktuellen definition_hash muss ein bestandener Testlauf vorliegen — aus dem
    Formular (testlauf_ok_at + testlauf_ok_hash) oder schon am Auftrag gespeichert. Startlisten-Seed
    (seed_version) ist ausgenommen. Liefert die zu speichernden Felder."""
    h = filter_hash(m)          # Nr. 124: die Zeilenzahl braucht keinen neuen Testlauf
    quellen = [e, alt or {}]
    for q in quellen:
        if q.get("testlauf_ok_at") and str(q.get("testlauf_ok_hash") or "") == h:
            return {"testlauf_ok_at": str(q["testlauf_ok_at"]), "testlauf_ok_hash": h}
    if (alt or {}).get("seed_version"):          # nur der gespeicherte Seed-Vermerk, nie aus dem Formular
        return {}
    raise Ungueltig("erst Testlauf — Aktivieren geht nur nach einem bestandenen Testlauf fuer diese Konfiguration (mindestens ein Treffer, keine Filterfehler)")


async def _semantisches_duplikat(db, m: Dict[str, Any], *, ohne_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Nr. 64: gleicher definition_hash UND gleiche EZ-Jahre/km-Bereiche bei einem nicht archivierten
    Auftrag -> der neue waere nur eine zweite Rechnung fuer dieselben Segmente."""
    h = definition_hash(m)
    filt: Dict[str, Any] = {"status": {"$ne": "archived"}}
    if ohne_id:
        filt["id"] = {"$ne": ohne_id}
    async for d in db[MODELLE].find(filt, {"_id": 0, "id": 1, "label": 1, "ez_years": 1, "km_buckets": 1, "definition_hash": 1,
                                           "make_id": 1, "model_id": 1, "fuel": 1, "gearbox": 1, "body": 1, "power_kw_min": 1,
                                           "power_kw_max": 1, "country": 1, "zip": 1, "radius_km": 1, "seller_type": 1, "rows": 1,
                                           "hash_fassung": 1}):
        # Nr. 124: nur ein Fingerabdruck der aktuellen Fassung ist vergleichbar — sonst neu rechnen
        alt_h = d.get("definition_hash") if int(d.get("hash_fassung") or 1) >= HASH_FASSUNG else None
        if (alt_h or definition_hash(d)) != h:
            continue
        if sorted(int(j) for j in (d.get("ez_years") or [])) != sorted(m["ez_years"]):
            continue
        km_alt = [(int(b.get("min_km")), int(b.get("max_km"))) for b in (d.get("km_buckets") or [])]
        if sorted(km_alt) != sorted((b["min_km"], b["max_km"]) for b in m["km_buckets"]):
            continue
        return d
    return None


# ---------------------------------------------------------------- Anlegen / Aendern / Duplizieren / Archivieren
async def anlegen(db, entwurf: Dict[str, Any]) -> Dict[str, Any]:
    m = entwurf_pruefen(entwurf)
    # Welle 5 Nr. 64: semantisch identischer Auftrag (Definition + EZ + km) darf nicht doppelt laufen
    zwilling = await _semantisches_duplikat(db, m)
    if zwilling:
        raise Ungueltig(f"Ein gleicher Suchauftrag besteht schon: „{zwilling.get('label') or zwilling['id']}“ ({zwilling['id']}) — "
                        f"bitte den bestehenden aktivieren oder aendern")
    # Nr. 65: aktiv nur nach bestandenem Testlauf (Formular schickt testlauf_ok_at/_hash mit)
    testlauf_felder = _testlauf_pruefen(m, entwurf) if m["status"] == "active" else {
        k: entwurf[k] for k in ("testlauf_ok_at", "testlauf_ok_hash") if entwurf.get(k)}
    basis = slug(m["make"], m["variant"] or m["model"])
    mid = basis
    i = 2
    while await db[MODELLE].find_one({"id": mid}, {"_id": 1}):
        mid = f"{basis}-{i}"
        i += 1
    doc = {**m, **testlauf_felder, "version": 1, "definition_hash": definition_hash(m), "filter_hash": filter_hash(m),
           "hash_fassung": HASH_FASSUNG, "created_at": konfig.jetzt_iso(), "updated_at": konfig.jetzt_iso()}
    # Nr. 131: zwei gleichzeitige Anlagen desselben Namens — der Unique-Index (markt_modell_id) meldet das
    # Rennen, dann wird -2/-3/... versucht statt still zu scheitern oder doppelt anzulegen
    from pymongo.errors import DuplicateKeyError
    for _ in range(50):
        try:
            await db[MODELLE].insert_one({**doc, "id": mid})
            break
        except DuplicateKeyError:
            mid = f"{basis}-{i}"
            i += 1
    else:
        raise Ungueltig("Keine freie Kennung fuer den Suchauftrag — bitte einen anderen Anzeigenamen/Variante waehlen")
    doc["id"] = mid
    await segmente.synchronisieren(db)
    doc.pop("_id", None)
    return doc


async def aendern(db, model_id: str, entwurf: Dict[str, Any]) -> Dict[str, Any]:
    """P4: aendern sich materielle Merkmale (definition_hash), steigt die Fassung — die
    Segmente der neuen Fassung bekommen eigene IDs, die alten werden von synchronisieren
    deaktiviert (nichts geloescht). rows/crawls_per_day/ez_years/km_buckets/label/status/
    notiz/priority aendern die Fassung NICHT.
    Welle 5 Nr. 65: eine materielle Aenderung eines AKTIVEN Auftrags braucht einen bestandenen
    Testlauf fuer die neue Definition (sonst 'erst Testlauf'); Nr. 64: kein Zwilling."""
    alt = await db[MODELLE].find_one({"id": model_id}, {"_id": 0})
    if not alt:
        raise Ungueltig("Modell nicht gefunden")
    m = entwurf_pruefen({**alt, **entwurf}, bestehend=alt)
    # Nr. 124: ein Fingerabdruck einer aelteren Hash-Fassung (ohne Zeilenzahl) ist nicht vergleichbar -> neu rechnen
    alt_hash = (alt.get("definition_hash") if int(alt.get("hash_fassung") or 1) >= HASH_FASSUNG else None) or definition_hash(alt)
    neu_hash = definition_hash(m)
    version = segmente.modell_version(alt)
    testlauf_felder: Dict[str, Any] = {k: entwurf[k] for k in ("testlauf_ok_at", "testlauf_ok_hash") if entwurf.get(k)}
    if neu_hash != alt_hash:
        version += 1
        zwilling = await _semantisches_duplikat(db, m, ohne_id=model_id)
        if zwilling:
            raise Ungueltig(f"Ein gleicher Suchauftrag besteht schon: „{zwilling.get('label') or zwilling['id']}“ ({zwilling['id']})")
        if m["status"] == "active":
            testlauf_felder = _testlauf_pruefen(m, entwurf, alt)
    elif m["status"] == "active" and alt.get("status") != "active":
        testlauf_felder = _testlauf_pruefen(m, entwurf, alt)
    # Nr. 130: Compare-and-set auf den gelesenen Stand (updated_at + version) — zwei Admins, die denselben
    # Auftrag gleichzeitig bearbeiten, ueberschreiben sich nicht mehr still; der zweite bekommt 409
    r = await db[MODELLE].update_one({"id": model_id, "updated_at": alt.get("updated_at"), "version": alt.get("version")},
                                     {"$set": {**m, **testlauf_felder, "version": version, "definition_hash": neu_hash,
                                               "filter_hash": filter_hash(m), "hash_fassung": HASH_FASSUNG,
                                               "updated_at": konfig.jetzt_iso()}})
    if r.matched_count == 0:
        raise Konflikt("Der Suchauftrag wurde inzwischen von jemand anderem geändert — bitte neu laden und erneut speichern")
    await segmente.synchronisieren(db)
    return await db[MODELLE].find_one({"id": model_id}, {"_id": 0})


async def konfig_anwenden(db, *, km_buckets: List[Dict[str, Any]], ez_years: List[int], rows: int) -> Dict[str, Any]:
    """Oberflaeche (Welle 5): 'Auf alle aktiven Auftraege anwenden' — die zentralen Vorbelegungen
    (km-Bereiche, EZ-Jahre, Zeilen) auf jeden aktiven Auftrag ueber aendern() uebertragen (nicht
    materiell: Fassung bleibt, alte Segmente werden deaktiviert, Historie bleibt)."""
    geaendert, fehler = [], []
    async for m in db[MODELLE].find({"status": "active"}, {"_id": 0, "id": 1}):
        try:
            await aendern(db, m["id"], {"km_buckets": [dict(b) for b in km_buckets], "ez_years": list(ez_years), "rows": int(rows)})
            geaendert.append(m["id"])
        except Ungueltig as ex:
            fehler.append({"id": m["id"], "fehler": str(ex)})
    return {"geaendert": len(geaendert), "ids": geaendert, "fehler": fehler}


async def status_setzen(db, model_id: str, status: str) -> Dict[str, Any]:
    if status not in STATUS:
        raise Ungueltig("Status unbekannt")
    alt = await db[MODELLE].find_one({"id": model_id}, {"_id": 0})
    if not alt:
        raise Ungueltig("Modell nicht gefunden")
    if status == "active" and not alt.get("model_id"):
        raise Ungueltig("Modell hat keine mobile.de-ID — kann nicht beobachtet werden")
    if status == "active":
        # Nr. 65: Aktivieren nur mit bestandenem Testlauf fuer die aktuelle Definition (Seed ausgenommen)
        _testlauf_pruefen({**alt, "status": "active"}, {}, alt)
    await db[MODELLE].update_one({"id": model_id}, {"$set": {"status": status, "enabled": status == "active",
                                                            "updated_at": konfig.jetzt_iso(),
                                                            **({"archived_at": konfig.jetzt_iso()} if status == "archived" else {})}})
    await segmente.synchronisieren(db)
    if status != "active":
        # wartende Jobs des Modells abbrechen — nichts loeschen, Historie bleibt
        grund = f"Suchauftrag {'pausiert' if status == 'paused' else 'archiviert'}"
        await db[JOBS].update_many({"model_id": model_id, "status": "queued"},
                                   {"$set": {"status": "cancelled", "error": grund, "finished_at": konfig.jetzt_iso()}})
        # Review 26.09.2026 Nr. 48/51: laufende Jobs bekommen cancel_requested — der Worker
        # prueft das Flag nach dem Actor-Lauf und speichert dann keine Zeilen mehr
        await db[JOBS].update_many({"model_id": model_id, "status": "running"},
                                   {"$set": {"cancel_requested": True, "cancel_grund": grund,
                                             "cancel_requested_at": konfig.jetzt_iso()}})
    return await db[MODELLE].find_one({"id": model_id}, {"_id": 0})


async def duplizieren(db, model_id: str, aenderungen: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    alt = await db[MODELLE].find_one({"id": model_id}, {"_id": 0})
    if not alt:
        raise Ungueltig("Modell nicht gefunden")
    entwurf = {k: v for k, v in alt.items() if k not in ("id", "created_at", "updated_at", "archived_at", "grund",
                                                          "version", "definition_hash", "filter_hash", "hash_fassung",
                                                          "testlauf_ok_at", "testlauf_ok_hash", "seed_version")}
    entwurf.update(aenderungen or {})
    entwurf["status"] = "paused"
    if not (aenderungen or {}).get("label"):
        entwurf["label"] = ""
    return await anlegen(db, entwurf)


async def monatsverbrauch_je_modell(db) -> Dict[str, float]:
    m = konfig.monat()
    raus: Dict[str, float] = {}
    # P1 + Welle 5 Nr. 55: JEDER Job mit actual_cost hat Geld gekostet — auch cancelled/failed/data_invalid
    async for row in db[JOBS].aggregate([{"$match": {"actual_cost": {"$ne": None}, "tag": {"$regex": f"^{m}"}}},
                                         {"$group": {"_id": "$model_id", "usd": {"$sum": {"$ifNull": ["$actual_cost", 0]}},
                                                     "rows": {"$sum": {"$ifNull": ["$actual_rows", 0]}}}}]):
        raus[row["_id"]] = round(float(row["usd"] or 0), 4)
    return raus
