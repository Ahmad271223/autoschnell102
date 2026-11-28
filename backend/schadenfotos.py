# -*- coding: utf-8 -*-
"""Schadenfotos im Abhol-Protokoll (Wunsch Ahmad 06.10.2026).

Der Fahrer markiert auf der Skizze einen Schaden oder eine Lackdicke-Messung und kann dazu Fotos
hochladen; der Chef sieht sie bei der Freigabe. Regeln (Entscheidungen Ahmad 06.10.2026):

  * hoechstens SCHADENFOTO_MAX (25) Fotos je Protokoll — gezaehlt werden die Fotos zu Schaeden/Messungen,
    die es im Protokoll noch gibt (ein entfernter Schaden gibt seine Plaetze frei);
  * sichtbar SCHADENFOTO_SICHT_TAGE (7) Tage ab dem Hochladen — danach liefert der Server sie nicht mehr
    aus, die Dateien BLEIBEN aber gespeichert ("behalten, nur ausblenden");
  * geloescht werden sie mit dem Protokoll: verworfener Entwurf, geloeschter Termin, Loeschung des
    Kaufvertrags (PII-Kaskade) — nie, solange eine andere Protokoll-Version (Korrektur) dieselbe Datei
    noch nennt;
  * nicht im Protokoll-PDF (sonst lebten sie dort unbegrenzt weiter) und nie im Kaufvertrag.

Ablage: pickup_protocols.schaden_fotos = [{id, key, schaden_id, erstellt_am, von}], Dateien unter dem
privaten Praefix protocol/<firma>/ (nie ueber /api/files, siehe dateien.PRIVATE_PREFIXE).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, List, Optional

from konfig import zahl_env

log = logging.getLogger("autohandel.schadenfotos")

SCHADENFOTO_MAX = 25
SCHADENFOTO_SICHT_TAGE = zahl_env("SCHADENFOTO_SICHT_TAGE", 7, unten=1, oben=365)
# Obergrenze der Eintraege insgesamt (auch Fotos zu inzwischen entfernten Schaeden, die erst beim
# Abschicken aufgeraeumt werden) — gegen unbegrenztes Wachsen des Protokolls.
SCHADENFOTO_EINTRAEGE_MAX = 2 * SCHADENFOTO_MAX
# base64 eines Fotos (die App verkleinert vorher auf 2000 px, JPEG — ein Foto ist dann ~0,5-1,5 MB)
SCHADENFOTO_B64_MAX = 8_000_000


def _zeit(wert: Any) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(str(wert or ""))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def sichtbar_bis(eintrag: dict) -> Optional[str]:
    t = _zeit(eintrag.get("erstellt_am"))
    return (t + timedelta(days=SCHADENFOTO_SICHT_TAGE)).isoformat() if t else None


def sichtbar(eintrag: dict, jetzt: Optional[datetime] = None) -> bool:
    """Innerhalb der Sichtfrist (ab dem Hochladen)? Ohne lesbaren Zeitpunkt: nein."""
    t = _zeit(eintrag.get("erstellt_am"))
    if t is None:
        return False
    return (jetzt or datetime.now(timezone.utc)) < t + timedelta(days=SCHADENFOTO_SICHT_TAGE)


def markierungs_ids(doc: dict) -> set:
    """ids aller Schaeden und Lackdicke-Messungen, die das Protokoll gerade traegt."""
    ids = set()
    for liste in ("new_damages", "lackmessungen"):
        for d in doc.get(liste) or []:
            if isinstance(d, dict) and str(d.get("id") or "").strip():
                ids.add(str(d["id"]))
    return ids


def aktive_fotos(doc: dict) -> List[dict]:
    """Fotos zu Schaeden/Messungen, die es im Protokoll noch gibt (zaehlen fuers Limit)."""
    ids = markierungs_ids(doc)
    return [e for e in doc.get("schaden_fotos") or []
            if isinstance(e, dict) and str(e.get("schaden_id") or "") in ids]


def fuer_anzeige(doc: dict, jetzt: Optional[datetime] = None) -> List[dict]:
    """Was App und Chef sehen: aktive, noch sichtbare Fotos — ohne Speicher-Key."""
    return [{"id": e.get("id"), "schaden_id": e.get("schaden_id"), "erstellt_am": e.get("erstellt_am"),
             "sichtbar_bis": sichtbar_bis(e)}
            for e in aktive_fotos(doc) if sichtbar(e, jetzt)]


def ohne_keys(doc: Optional[dict]) -> Optional[dict]:
    """Protokoll fuer die Fahrer-App: schaden_fotos nur in der Anzeige-Form (keine Speicher-Keys)."""
    if not isinstance(doc, dict) or "schaden_fotos" not in doc:
        return doc
    return {**doc, "schaden_fotos": fuer_anzeige(doc)}


async def dateien_loeschen(db, docs: Iterable[dict], grund: str) -> int:
    """Nach dem Loeschen von Protokoll-Dokumenten: deren Schadenfotos loeschen — ausser eine andere
    Protokoll-Version nennt dieselbe Datei noch (Korrektur uebernimmt die Fotos der Vorversion).
    Scheitert das Loeschen, merkt loeschen_oder_vormerken es zur Nachholung vor. Wirft nie."""
    from storage_service import loeschen_oder_vormerken
    n = 0
    for doc in docs or []:
        for e in (doc or {}).get("schaden_fotos") or []:
            key = (e or {}).get("key") if isinstance(e, dict) else None
            if not key:
                continue
            try:
                if await db.pickup_protocols.count_documents({"schaden_fotos.key": key}, limit=1):
                    continue
                if await loeschen_oder_vormerken(db, key=key, grund=grund,
                                                 dealer_id=str(doc.get("dealer_id") or "")):
                    n += 1
            except Exception:  # noqa: BLE001
                log.exception("Schadenfoto %s nicht geloescht (%s)", key, grund)
    return n


def markierung_text(m: dict) -> str:
    """Kurztext einer Markierung fuer die Bildunterschrift ("Kratzer · Motorhaube", "Lackdicke 180 µm · Dach")."""
    zone = str(m.get("zone") or "").strip()
    if "wert_um" in m or m.get("art") == "lackdicke":
        wert = m.get("wert_um")
        kopf = f"Lackdicke {wert} µm" if wert is not None else "Lackdicke"
    else:
        kopf = str(m.get("type_label") or m.get("type_key") or "Schaden").strip()
    return " · ".join(x for x in (kopf, zone) if x)


def markierungen(doc: dict) -> dict:
    """id -> Kurztext aller Schaeden und Lackdicke-Messungen (fuer die Bildunterschriften)."""
    raus = {}
    for d in doc.get("new_damages") or []:
        if isinstance(d, dict) and d.get("id"):
            raus[str(d["id"])] = markierung_text(d)
    for m in doc.get("lackmessungen") or []:
        if isinstance(m, dict) and m.get("id"):
            raus[str(m["id"])] = markierung_text({**m, "art": "lackdicke"})
    return raus


#: Projektion fuer die Loeschpfade (das Noetige jedes Protokolls fuer dateien_loeschen)
PROJEKTION = {"_id": 0, "id": 1, "dealer_id": 1, "schaden_fotos": 1}
