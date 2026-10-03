# -*- coding: utf-8 -*-
"""Werkzeuge zum Herunterladen — Programme, die nur bestimmte Kunden bekommen.

03.10.2026 (Wunsch Ahmad): Der AutoPointer-Vergleich (Windows-Programm, Quelle
in autopointer-vergleich/) ist erst einmal NUR fuer Kunde 10002 freigeschaltet.
"Alle anderen bekommen das nicht, die sollen das gar nicht sehen": Fuer andere
Firmen gibt es weder einen Menuepunkt noch einen Download — die Route antwortet
404, als gaebe es sie nicht.

Freigabe je Werkzeug ueber eine Umgebungsvariable mit Kundennummern (Firma =
dealers.kunden_nr; Chef UND alle Sucher der Firma). Standard ohne Variable:
nur 10002. Mehrere Kunden: AUTOPOINTER_VERGLEICH_KUNDEN=10002,10017

Die Programmdatei liegt im Datei-Speicher (S3/R2 bzw. lokal) unter
werkzeuge/<id>/<dateiname>, Version/Groesse/Pruefsumme in der Sammlung
`werkzeuge`. Hochladen: scripts/werkzeug_hochladen.py (im Backend-Container;
die Datei ist groesser als das Upload-Limit von nginx).

Dieses Modul importiert weder FastAPI noch server/routes — die Tests und das
Skript nutzen es direkt.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Iterable, Optional

AUTOPOINTER = "autopointer-vergleich"

WERKZEUGE = {
    AUTOPOINTER: {
        # Name und Texte kommen NUR ueber /api/werkzeuge (nur fuer freigegebene
        # Firmen) — die Oberflaeche selbst enthaelt keinen Hinweis darauf.
        "name": "AutoPointer-Vergleich",
        "beschreibung": ("Windows-Programm für AutoPointer: Du klickst in AutoPointer ein Inserat an – "
                         "eine halbe Sekunde später öffnen sich automatisch die passenden Vergleiche "
                         "auf mobile.de und AutoScout24 (gleiches Modell, Baujahr, Kilometer, Leistung, "
                         "Kraftstoff, Getriebe). Keine Eingabe nötig."),
        "schritte": [
            "Programm herunterladen und starten (Windows 10/11). Beim ersten Start meldet Windows evtl. "
            "„Der Computer wurde durch Windows geschützt“ – dann „Weitere Informationen“ → „Trotzdem ausführen“.",
            "Unten rechts erscheint ein grünes Lupen-Symbol. AutoPointer öffnen und ein Inserat anklicken – "
            "die Vergleiche öffnen sich als neue Browser-Tabs.",
            "Doppelklick auf das Symbol oder Strg+Alt+P schaltet die Automatik aus und wieder an. "
            "Rechtsklick: Einstellungen (Portale, Kilometer-Spanne, Baujahr, Browser, mit Windows starten).",
        ],
        "dateiname": "AutoSchnell-Vergleich.exe",
        "schluessel": "werkzeuge/autopointer-vergleich/AutoSchnell-Vergleich.exe",
        "kunden_env": "AUTOPOINTER_VERGLEICH_KUNDEN",
        "kunden_standard": "10002",
    },
}

#: Obergrenze fuer eine Programmdatei (die EXE ist ~55 MB).
MAX_MB = 200
_MIN_BYTES = 1024


def kunden_text(kunden_nr) -> str:
    """Kundennummer als Text ohne fuehrende Nullen/Leerzeichen ("10002")."""
    if kunden_nr is None or isinstance(kunden_nr, bool):
        return ""
    try:
        return str(int(str(kunden_nr).strip()))
    except ValueError:
        return str(kunden_nr).strip()


def freigegebene_kunden(werkzeug_id: str) -> frozenset:
    """Kundennummern, die das Werkzeug sehen. Eine gesetzte, aber LEERE
    Variable schaltet es fuer alle ab."""
    w = WERKZEUGE.get(werkzeug_id)
    if not w:
        return frozenset()
    roh = os.environ.get(w["kunden_env"])
    if roh is None:
        roh = w["kunden_standard"]
    teile = (kunden_text(t) for t in roh.replace(";", ",").split(","))
    return frozenset(t for t in teile if t)


def ist_freigegeben(werkzeug_id: str, kunden_nr) -> bool:
    k = kunden_text(kunden_nr)
    return bool(k) and k in freigegebene_kunden(werkzeug_id)


def freigegebene_werkzeuge(kunden_nr) -> list:
    return [wid for wid in WERKZEUGE if ist_freigegeben(wid, kunden_nr)]


def exe_pruefen(daten: bytes) -> None:
    """Nur echte Windows-Programme (MZ-Kopf), nicht leer, nicht riesig."""
    if not daten or len(daten) < _MIN_BYTES or daten[:2] != b"MZ":
        raise ValueError("Keine Windows-Programmdatei (.exe)")
    if len(daten) > MAX_MB * 1024 * 1024:
        raise ValueError(f"Datei zu groß (max. {MAX_MB} MB)")


def eintrag(werkzeug_id: str, daten: bytes, version: str, jetzt: Optional[datetime] = None) -> dict:
    w = WERKZEUGE[werkzeug_id]
    return {
        "id": werkzeug_id,
        "schluessel": w["schluessel"],
        "dateiname": w["dateiname"],
        "version": (version or "").strip()[:40] or (jetzt or datetime.now(timezone.utc)).strftime("%Y-%m-%d"),
        "groesse": len(daten),
        "sha256": hashlib.sha256(daten).hexdigest(),
        "hochgeladen_am": (jetzt or datetime.now(timezone.utc)).isoformat(),
    }


def hochladen(db_sync, werkzeug_id: str, daten: bytes, version: str = "", storage=None) -> dict:
    """Datei pruefen, in den Speicher legen, Eintrag schreiben (synchron:
    pymongo-Datenbank, fuer Skript und Tests)."""
    if werkzeug_id not in WERKZEUGE:
        raise ValueError(f"Unbekanntes Werkzeug: {werkzeug_id}")
    exe_pruefen(daten)
    if storage is None:
        from storage_service import storage as storage_standard
        storage = storage_standard
    meta = eintrag(werkzeug_id, daten, version)
    storage.save(meta["schluessel"], daten, max_mb=MAX_MB)
    db_sync.werkzeuge.replace_one({"id": werkzeug_id}, meta, upsert=True)
    return meta


def oeffentlich(meta: Optional[dict], werkzeug_id: str) -> dict:
    """Was die Oberflaeche ueber ein freigegebenes Werkzeug erfaehrt."""
    w = WERKZEUGE[werkzeug_id]
    meta = meta or {}
    return {
        "id": werkzeug_id,
        "name": w["name"],
        "beschreibung": w.get("beschreibung", ""),
        "schritte": list(w.get("schritte", [])),
        "dateiname": w["dateiname"],
        "vorhanden": bool(meta.get("groesse")),
        "version": meta.get("version"),
        "groesse": meta.get("groesse"),
        "hochgeladen_am": meta.get("hochgeladen_am"),
    }


def alle_ids() -> Iterable[str]:
    return WERKZEUGE.keys()
