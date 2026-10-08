# -*- coding: utf-8 -*-
"""Werkzeuge zum Herunterladen — Programme, die nur bestimmte Kunden bekommen.

03.10.2026 (Wunsch Ahmad): Der AutoPointer-Vergleich (Windows-Programm, Quelle
in autopointer-vergleich/) ist erst einmal NUR fuer Kunde 10002 freigeschaltet, seit dem Abend
auch fuer Kunde 10001, seit 06.10.2026 auch fuer Kunde 10007 (Wunsch Ahmad).
"Alle anderen bekommen das nicht, die sollen das gar nicht sehen": Fuer andere
Firmen gibt es weder einen Menuepunkt noch einen Download — die Route antwortet
404, als gaebe es sie nicht.

Freigabe je Werkzeug ueber eine Umgebungsvariable mit Kundennummern (Firma =
dealers.kunden_nr; Chef UND alle Sucher der Firma). Standard ohne Variable:
10001, 10002 und 10007. Mehrere Kunden: AUTOPOINTER_VERGLEICH_KUNDEN=10001,10002,10007,10017

Die Programmdatei liegt im Datei-Speicher (S3/R2 bzw. lokal) unter
werkzeuge/<id>/<dateiname>, Version/Groesse/Pruefsumme in der Sammlung
`werkzeuge`. Hochladen: scripts/werkzeug_hochladen.py (im Backend-Container;
die Datei ist groesser als das Upload-Limit von nginx).

Lizenz (Wunsch Ahmad 03.10.2026 nachmittags): Das Programm arbeitet nur
verbunden. Der Sucher holt sich in der App einen 6-stelligen Code (10 Minuten,
einmal), das Programm tauscht ihn gegen einen Programm-Schluessel. Pro Konto
EIN PC: eine neue Verbindung ersetzt die alte (der alte PC bekommt 401). Die
Browser-Anmeldung bleibt davon unberuehrt — der Schluessel ist keine Sitzung
und kann nur Vergleiche fuer dieses Werkzeug anfragen. Jeder Vergleich geht
ueber den Server: Abo pruefen, Links mit den Vergleichsregeln der Firma bauen
(wie der Vergleich in der App), protokollieren (Admin + Chef sehen, wer welches
Auto verglichen hat).

Dieses Modul importiert weder FastAPI noch server/routes — die Tests und das
Skript nutzen es direkt.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

AUTOPOINTER = "autopointer-vergleich"
#: 04.10.2026 (Wunsch Ahmad): eigene Browser-Erweiterung fuer Chrome/Edge (browser-extension/,
#: Auswertung in backend/browser_helfer.py) — gleiche Lizenz wie das Programm (Code, ein Browser je Konto).
BROWSER_HELFER = "browser-helfer"

WERKZEUGE = {
    AUTOPOINTER: {
        # Name und Texte kommen NUR ueber /api/werkzeuge (nur fuer freigegebene
        # Firmen) — die Oberflaeche selbst enthaelt keinen Hinweis darauf.
        # Wunsch Ahmad 06.10.2026: Anzeigenamen "AutoSchnell Vergleich" / "AutoSchnell Analyse und
        # Vertragsabwicklung" (Dateinamen und Kennungen bleiben)
        "name": "AutoSchnell Vergleich",
        "art": "windows",
        "geraet": "PC",
        "beschreibung": ("Windows-Programm für AutoPointer: Du klickst in AutoPointer ein Inserat an – "
                         "eine halbe Sekunde später öffnen sich automatisch die passenden Vergleiche "
                         "auf mobile.de und AutoScout24 (gleiches Modell, Baujahr, Kilometer, Leistung, "
                         "Kraftstoff, Getriebe). Keine Eingabe nötig."),
        "schritte": [
            "Programm herunterladen und starten (Windows 10/11). Beim ersten Start meldet Windows evtl. "
            "„Der Computer wurde durch Windows geschützt“ – dann „Weitere Informationen“ → „Trotzdem ausführen“.",
            "Das Programm fragt nach einem Code: hier auf „Programm verbinden“ klicken und den 6-stelligen Code "
            "eintippen. Jedes Konto kann auf EINEM PC verbunden sein; ein neuer PC ersetzt den alten.",
            "Unten rechts erscheint ein grünes Lupen-Symbol. AutoPointer öffnen und ein Inserat anklicken – "
            "die Vergleiche öffnen sich als neue Browser-Tabs, mit euren Vergleichsregeln aus AutoSchnell "
            "(Einstellungen → Vergleich). Ohne aktives Abo öffnet das Programm nichts.",
            "Kaufvertrag: Beim Anklicken liest AutoSchnell das Inserat schon im Hintergrund aus (Daten + Fotos). "
            "Rechtsklick auf das Symbol → „Kaufvertrag: Auto in AutoSchnell öffnen“ oder hier unten bei „Deine "
            "letzten Autos“ – kein Link-Einfügen nötig. Nur wenn AutoPointer bei AutoScout die Hash-ID nicht "
            "vollständig zeigt: Inserat-Adresse selbst kopieren und unter „Vergleich“ einfügen.",
            "Doppelklick auf das Symbol oder Strg+Alt+P schaltet die Automatik aus und wieder an. "
            "Rechtsklick: Einstellungen (Portale, Browser, mit Windows starten).",
        ],
        "dateiname": "AutoSchnell-Vergleich.exe",
        "schluessel": "werkzeuge/autopointer-vergleich/AutoSchnell-Vergleich.exe",
        "kunden_env": "AUTOPOINTER_VERGLEICH_KUNDEN",
        "kunden_standard": "10001,10002,10007",
    },
    BROWSER_HELFER: {
        "name": "AutoSchnell Analyse und Vertragsabwicklung",
        "art": "browser",
        "geraet": "Browser",
        "beschreibung": ("Erweiterung für Chrome und Edge: Du öffnest ein Inserat auf mobile.de, AutoScout24 oder "
                         "Kleinanzeigen – sofort gehen die Vergleiche mit euren Regeln auf, im Inserat zeigt eine "
                         "Ampel, wo der Preis unter den Vergleichsangeboten liegt, und der Kaufvertrag geht mit "
                         "einem Klick – ohne Link-Einfügen und ohne Warten."),
        "schritte": [
            "ZIP herunterladen und entpacken (Rechtsklick auf die Datei → „Alle extrahieren“).",
            "Chrome: chrome://extensions öffnen, Edge: edge://extensions – „Entwicklermodus“ einschalten → "
            "„Entpackte Erweiterung laden“ (Edge: „Entpackt laden“) → den entpackten Ordner wählen.",
            "Auf das AutoSchnell-Symbol in der Browserleiste klicken (evtl. erst über das Puzzle-Symbol anheften) "
            "und den 6-stelligen Code von hier eintippen. Jedes Konto kann in EINEM Browser verbunden sein; ein "
            "neuer Browser ersetzt den alten.",
            "Inserat öffnen – Vergleiche, Ampel und der Knopf „Kaufvertrag“ erscheinen von selbst. Im Symbol-Menü "
            "lässt sich das automatische Öffnen der Vergleiche abschalten. Ohne aktives Abo passiert nichts.",
        ],
        "dateiname": "AutoSchnell-Helfer.zip",
        "schluessel": "werkzeuge/browser-helfer/AutoSchnell-Helfer.zip",
        "kunden_env": "BROWSER_HELFER_KUNDEN",
        "kunden_standard": "10001,10002,10007",
    },
}

#: Obergrenze fuer eine Programmdatei (die EXE ist ~55 MB).
MAX_MB = 200
_MIN_BYTES = 1024
#: Erweiterungs-ZIP: deutlich kleiner als ein Programm
MAX_ZIP_MB = 20


def art(werkzeug_id: str) -> str:
    return (WERKZEUGE.get(werkzeug_id) or {}).get("art", "windows")


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


def zip_pruefen(daten: bytes) -> None:
    """Nur ein ZIP mit manifest.json im obersten Ordner (Chrome laedt den entpackten Ordner)."""
    import io
    import zipfile
    if not daten or len(daten) < _MIN_BYTES or daten[:4] != b"PK\x03\x04":
        raise ValueError("Keine ZIP-Datei")
    if len(daten) > MAX_ZIP_MB * 1024 * 1024:
        raise ValueError(f"Datei zu groß (max. {MAX_ZIP_MB} MB)")
    try:
        namen = zipfile.ZipFile(io.BytesIO(daten)).namelist()
    except zipfile.BadZipFile:
        raise ValueError("ZIP-Datei beschädigt")
    if "manifest.json" not in namen:
        raise ValueError("Im ZIP fehlt manifest.json (Erweiterung im obersten Ordner packen)")


def datei_pruefen(werkzeug_id: str, daten: bytes) -> None:
    zip_pruefen(daten) if art(werkzeug_id) == "browser" else exe_pruefen(daten)


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
    datei_pruefen(werkzeug_id, daten)
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
        # 04.10.2026: Programm (Windows) oder Erweiterung (Browser) — Texte der Seite "Programme"
        "art": w.get("art", "windows"),
        "geraet": w.get("geraet", "PC"),
        "vorhanden": bool(meta.get("groesse")),
        "version": meta.get("version"),
        "groesse": meta.get("groesse"),
        "hochgeladen_am": meta.get("hochgeladen_am"),
    }


def alle_ids() -> Iterable[str]:
    return WERKZEUGE.keys()


# ---------------------------------------------------------------------------
# Lizenz: Code -> Programm-Schluessel, ein PC je Konto
# ---------------------------------------------------------------------------
CODE_LAENGE = 6
CODE_MINUTEN = 10
#: Sammlungen (alle mit dealer_id — gehen in die Firmenloeschung, routes/admin.py)
SAMMLUNG_CODES = "werkzeug_codes"
SAMMLUNG_VERBINDUNGEN = "werkzeug_verbindungen"
SAMMLUNG_VERGLEICHE = "werkzeug_vergleiche"
#: Entscheidung Ahmad 06.10.2026: Vergleiche (Programm + Browser-Helfer) 60 Tage aufbewahren — wie Logs und
#: Vertraege; "Deine letzten Autos" zeigt 30, die Chef-Uebersicht die letzten Wochen. Danach loescht Mongo (TTL).
VERGLEICHE_TAGE = int(os.environ.get("WERKZEUG_VERGLEICHE_TAGE", "60") or 60)


def vergleich_ablauf(ab: Optional[datetime] = None) -> datetime:
    """Loeschzeitpunkt eines Vergleichs (TTL-Index werkzeug_vergleiche_ablauf)."""
    return (ab or datetime.now(timezone.utc)) + timedelta(days=max(1, VERGLEICHE_TAGE))


# ---------------------------------------------------------------- Mindestversionen (08.10.2026)
# Durchsicht vor dem Rollout 08.10.2026: der Server schaltet neue Wege nur ein, wenn Programm UND Erweiterung beim
# Kunden sie kennen — sonst arbeiten aeltere Versionen wie bisher weiter (kein Vorab-Abruf ohne Inserat-Tab, keine
# Vorgangsseite, die niemand uebernimmt).
#: Programm oeffnet das Inserat als Tab (inserat_im_browser), Erweiterung liest auch /s-anzeige/<Nr>
INSERAT_TAB_PROGRAMM, INSERAT_TAB_HELFER = "1.5.7", "2.7.1"
#: Programm oeffnet nur /app/vorgang/<id>, Erweiterung uebernimmt den Vorgang (ueber_helfer)
VORGANG_PROGRAMM, VORGANG_HELFER = "1.5.8", "2.7.2"


def version_mindestens(version: Optional[str], mindest: str) -> bool:
    """"1.5.10" >= "1.5.8"; fehlende oder unlesbare Version zaehlt als zu alt."""
    def teile(v):
        return tuple(int(x) for x in str(v).split("."))
    try:
        return bool(version) and teile(version) >= teile(mindest)
    except ValueError:
        return False


# ---------------------------------------------------------------- Betreiber-Liste: Top-Modelle (08.10.2026)
def _modell_schluessel(marke, modell) -> str:
    """"VW up!" und "Vw Up" zaehlen zusammen: klein, ohne Satzzeichen, Leerraum einfach."""
    text = f"{marke or ''} {modell or ''}".lower()
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-zäöüß]+", " ", text)).strip()


def top_modelle(fahrzeuge: list, anzahl: int = 20) -> list:
    """Die meistverglichenen Modelle: [{"modell": "VW Golf", "anzahl": 42}] — Anzeige-Name = haeufigste Schreibweise."""
    zaehler: dict = {}
    for f in fahrzeuge:
        f = f or {}
        schluessel = _modell_schluessel(f.get("marke"), f.get("modell"))
        if not schluessel:
            continue
        name = " ".join(x for x in (str(f.get("marke") or "").strip(), str(f.get("modell") or "").strip()) if x)
        eintrag = zaehler.setdefault(schluessel, {"anzahl": 0, "namen": {}})
        eintrag["anzahl"] += 1
        eintrag["namen"][name] = eintrag["namen"].get(name, 0) + 1
    liste = [{"modell": max(e["namen"].items(), key=lambda x: (x[1], x[0]))[0], "anzahl": e["anzahl"]}
             for e in zaehler.values()]
    liste.sort(key=lambda x: (-x["anzahl"], x["modell"].lower()))
    return liste[:anzahl]


# ---------------------------------------------------------------- Portalwahl (08.10.2026)
# Wunsch Ahmad 08.10.2026 (externe Pruefung): die Wahl "mobile.de / AutoScout24 / beide" gab es dreimal — in der App
# (Browser-Speicher), im Windows-Programm und in der Erweiterung. Jetzt EINMAL je Konto (users.vergleich_portale,
# PUT /auth/vergleich-portale, in der App auf der Vergleichsseite). Der Server filtert die Links fuer Programm und
# Erweiterung selbst; beide zeigen die Wahl nur noch an (status.portale).
PORTAL_SCHLUESSEL = {"mobile.de": "mobile", "AutoScout24": "autoscout"}


def portale_von(user: Optional[dict]) -> dict:
    """{"mobile": bool, "autoscout": bool} — ohne Eintrag beide; beide aus gibt es nicht (dann wieder beide)."""
    p = (user or {}).get("vergleich_portale")
    p = p if isinstance(p, dict) else {}
    mobile, autoscout = p.get("mobile") is not False, p.get("autoscout") is not False
    if not (mobile or autoscout):
        mobile = autoscout = True
    return {"mobile": mobile, "autoscout": autoscout}


def nach_portalen(links: list, hinweise: list, portale: dict) -> tuple:
    """Nur die Links der gewaehlten Portale — und keine Hinweise "kein <Portal>-Vergleich" fuer abgewaehlte."""
    aus = [name for name, k in PORTAL_SCHLUESSEL.items() if portale.get(k) is False]
    links = [l for l in links if portale.get(PORTAL_SCHLUESSEL.get(l.get("portal"), ""), True) is not False]
    hinweise = [h for h in hinweise if not any(f"kein {name}-Vergleich" in str(h) for name in aus)]
    return links, hinweise
#: Pruefbericht 03.10.2026 (Nr. 12): die App meldet, dass sie ein Auto aus dem Programm uebernommen hat
SAMMLUNG_APP_STARTS = "werkzeug_app_starts"
#: Pruefbericht 03.10.2026 (Nr. 16): warum ein Programm-Schluessel nicht mehr gilt (anderer PC, Chef, Betreiber)
SAMMLUNG_GETRENNT = "werkzeug_getrennt"
TOKEN_KOPF = "X-Werkzeug-Schluessel"


def code_erzeugen() -> str:
    return f"{secrets.randbelow(10 ** CODE_LAENGE):0{CODE_LAENGE}d}"


def code_normalisieren(roh) -> str:
    """Nur Ziffern ("123 456" / "123-456" -> "123456")."""
    return re.sub(r"\D", "", str(roh or ""))[:20]


def streuwert(wert: str) -> str:
    """SHA-256 — Codes und Schluessel liegen nie im Klartext in der Datenbank."""
    return hashlib.sha256(str(wert).encode("utf-8")).hexdigest()


def schluessel_erzeugen() -> str:
    return secrets.token_urlsafe(32)


#: So lange bekommt derselbe PC fuer denselben Code denselben Schluessel noch einmal
#: (Anfrage doppelt angekommen: Netz, Doppelklick — gesehen beim Test am 03.10.2026).
WIEDERHOLUNG_SEKUNDEN = 120


def schluessel_ableiten(geheimnis: str, code_id: str, pc_kennung: str) -> str:
    """Schluessel fuer genau diese Einloesung (Code + PC): eine doppelt angekommene
    Anfrage ergibt denselben Schluessel, ohne dass er irgendwo im Klartext liegt."""
    import base64
    import hmac
    roh = hmac.new(str(geheimnis).encode("utf-8"), f"werkzeug|{code_id}|{pc_kennung}".encode("utf-8"),
                   hashlib.sha256).digest()
    return base64.urlsafe_b64encode(roh).decode("ascii").rstrip("=")


def _text(wert, laenge: int) -> str:
    return re.sub(r"\s+", " ", str(wert or "")).strip()[:laenge]


def fahrzeug_zu_vehicle(f: dict) -> dict:
    """Vom Programm gelesene Werte -> Fahrzeug-Dict der Link-Bauer
    (mobile_service/autoscout_service.build_search_url)."""
    jahr, monat = f.get("ez_jahr"), f.get("ez_monat")
    ez = f"{int(monat):02d}/{int(jahr)}" if jahr and monat else (str(int(jahr)) if jahr else "")
    titel = _text(f.get("titel"), 200)
    v = {
        "make_label": _text(f.get("marke"), 60),
        "model_label": _text(f.get("modell"), 80),
        "model_description": titel,
        "title": titel,
        "first_registration": ez,
        "mileage": int(f["kilometer"]) if f.get("kilometer") is not None else None,
        "power_kw": int(f["kw"]) if f.get("kw") else None,
        "power_ps": int(f["ps"]) if f.get("ps") else None,
        "fuel": _text(f.get("kraftstoff"), 40),
        "fuel_label": _text(f.get("kraftstoff"), 40),
        "gearbox": _text(f.get("getriebe"), 40),
        "gearbox_label": _text(f.get("getriebe"), 40),
        "doors": _text(f.get("tueren"), 10) or None,
    }
    v = {k: w for k, w in v.items() if w not in (None, "")}
    # Wunsch Ahmad 03.10.2026: Kleinanzeigen fuehrt viele Autos als "Weitere VW" — dann das Modell aus dem
    # Titel ("VW Beetle Cabrio 1.2 TSI" -> Beetle), wie beim Einfuegen eines Links in der App
    # (mobile_service._enhance_generic_model, nur der Titel zaehlt, Pruefbericht B-10). Uebernommen wird
    # nur ein Treffer im Modell-Katalog — sonst bleibt es beim Hinweis statt einer Suche nur nach der Marke.
    import mobile_service as ms
    if titel and ms._is_generic_model_label(v.get("model_label")):
        probe = ms._enhance_generic_model({k: v[k] for k in ("make_label", "model_label", "model_description")
                                           if k in v})
        if probe.get("model"):
            v["model_label"] = probe["model_label"]
    return v


def _km_text(km: int) -> str:
    return f"{km:,}".replace(",", ".")


def plausibel(f: dict, heute: Optional[date] = None) -> list:
    """Befund Ahmad 04.10.2026 (echte Vergleichsliste: "Kia Rio · EZ 04/2026 · 165.000 km", "Opel Crossland ·
    EZ 10/2026 · 75.000 km", "Audi 80 · 1.960.817 km"): Lesefehler oder falsche Angaben im Inserat -> dem Sucher
    SAGEN. Die Filter bleiben trotzdem genau so, wie sie in den Einstellungen (Firma/Sucher) stehen — Wunsch Ahmad
    04.10.: "EZ immer -1 und km immer +20.000 oder je nachdem, was im Konto eingestellt ist", nie still weglassen.

      * Kilometer ueber 1.000.000
      * Erstzulassung in der Zukunft
      * mehr als 20.000 km + 10.000 km je Monat Alter

    Rueckgabe: Hinweise fuer den Sucher (leer = alles plausibel)."""
    heute = heute or date.today()
    hinweise = []
    jahr, monat, km = f.get("ez_jahr"), f.get("ez_monat"), f.get("kilometer")
    if km is not None and km > 1_000_000:
        hinweise.append(f"Kilometerstand {_km_text(km)} km wirkt unplausibel (Lesefehler?) – bitte prüfen.")
        km = None
    if jahr:
        ez_text = f"{int(monat):02d}/{int(jahr)}" if monat else str(int(jahr))
        alter = (heute.year - int(jahr)) * 12 + (heute.month - int(monat or 1))
        if alter < 0:
            hinweise.append(f"Erstzulassung {ez_text} liegt in der Zukunft – bitte prüfen.")
        elif km is not None and km > 20_000 + 10_000 * alter:
            hinweise.append(f"Erstzulassung {ez_text} passt nicht zu {_km_text(km)} km – bitte prüfen.")
    return hinweise


def vergleichs_links(vehicle: dict, regeln: dict) -> tuple:
    """(links, hinweise) mit denselben Link-Bauern wie der Vergleich in der App.

    Strenger als die App (Vorgabe Ahmad: "keine Suche nur nach Bentley"): ein
    Portal bekommt nur dann einen Link, wenn sein Katalog Marke UND Modell
    kennt — sonst ein Hinweis statt einer Suche ueber die ganze Marke.

    Wunsch Ahmad 04.10.2026: Programm und Browser-Helfer halten sich IMMER an die AutoSchnell-Einstellungen
    (Firma/Sucher) — auch beim Navi (03.10. war es im Programm abgeschaltet) und bei Beschaedigten (der Helfer
    hat sie seit 04.10. vormittags immer ausgeschlossen). Wer kein Navi-Filter will, stellt "Navi" auf egal."""
    import autoscout_service as asv
    import mobile_service as ms
    links, hinweise = [], []
    marke = vehicle.get("make_label", "")
    modell = vehicle.get("model_label", "")
    m_marke, m_modell = ms.modell_aufgeloest(vehicle)
    if m_marke and m_modell:
        links.append({"portal": "mobile.de", "url": ms.build_search_url(vehicle, regeln)})
    elif not m_marke:
        hinweise.append(f"mobile.de kennt die Marke „{marke}“ nicht – kein mobile.de-Vergleich.")
    else:
        hinweise.append(f"mobile.de kennt das Modell „{modell}“ nicht – kein mobile.de-Vergleich "
                        f"(sonst würde nur nach „{marke}“ gesucht).")
    as_marke = asv._find_make(marke) if marke else None
    as_modell = asv._find_model(as_marke, modell) if (as_marke and modell) else None
    if as_marke and as_modell:
        links.append({"portal": "AutoScout24", "url": asv.build_search_url(vehicle, regeln)})
    elif not as_marke:
        hinweise.append(f"AutoScout24 kennt die Marke „{marke}“ nicht – kein AutoScout24-Vergleich.")
    else:
        hinweise.append(f"AutoScout24 kennt das Modell „{modell}“ nicht – kein AutoScout24-Vergleich "
                        f"(sonst würde nur nach „{marke}“ gesucht).")
    for h in asv.regeln_nicht_abgebildet(vehicle, regeln):
        if h not in hinweise:
            hinweise.append(h)
    return links, hinweise


# ---------------------------------------------------------------------------
# Inserat-Link (Wunsch Ahmad 03.10.2026): aus der Inserat-ID (mobile.de, Kleinanzeigen)
# bzw. der Hash-ID (AutoScout24) — am 03.10. live mit echten Inseraten geprueft.
# ---------------------------------------------------------------------------
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def vorab_warten_s() -> float:
    """So lange wartet ein Vorab-Abruf, bevor er startet (Standard 15 s). Klickt der Sucher in der Zeit das
    naechste Auto an, faellt der alte Abruf weg (kein Apify-Lauf, kein Tageskontingent); oeffnet er das Auto
    in der App, startet er sofort."""
    try:
        return min(300.0, max(0.0, float(os.environ.get("AUTOPOINTER_VORAB_WARTEN_S") or 15)))
    except ValueError:
        return 15.0


def inserat_im_browser_an() -> bool:
    """Wunsch Ahmad 07.10.2026: hat das Konto den Browser-Helfer, liest DER das Inserat im Browser (kostenlos, ohne
    Apify, ohne Tageslimit) — das Programm oeffnet das Inserat dafuer als Tab mit. Standard an; aus = wie frueher
    immer der Vorab-Abruf ueber Apify."""
    return (os.environ.get("AUTOPOINTER_INSERAT_IM_BROWSER") or "true").strip().lower() not in ("0", "false", "nein", "aus")


def vorab_abruf_an() -> bool:
    """Inserat beim Klick im Programm im Hintergrund auslesen (Standard an). Jeder echte Abruf zaehlt
    wie ein eingefuegter Link fuer das Tageslimit des Kontos; Speicher-Treffer sind kostenlos."""
    return (os.environ.get("AUTOPOINTER_VORAB_ABRUF") or "true").strip().lower() not in ("0", "false", "nein", "aus")


def inserat_url(quelle, inserat_id, hash_id=None) -> Optional[str]:
    """Adresse des Original-Inserats oder None (lieber kein Link als ein falscher).

    mobile.de:      suchen.mobile.de/fahrzeuge/details.html?id=<Inserat-ID>  (9 bis 14 Stellen gesehen)
    Kleinanzeigen:  www.kleinanzeigen.de/s-anzeige/<Inserat-ID>
    AutoScout24:    www.autoscout24.de/angebote/<Hash-ID>  (die Inserat-ID aus AutoPointer
                    kennt AutoScout nicht — "Seite nicht gefunden")"""
    q = re.sub(r"[^a-z0-9]", "", str(quelle or "").lower())
    nummer = str(inserat_id or "").strip()
    if "mobile" in q and re.fullmatch(r"\d{6,20}", nummer):
        return f"https://suchen.mobile.de/fahrzeuge/details.html?id={nummer}"
    if "kleinanzeigen" in q and re.fullmatch(r"\d{6,20}", nummer):
        return f"https://www.kleinanzeigen.de/s-anzeige/{nummer}"
    if "autoscout" in q:
        h = str(hash_id or "").strip().lower()
        if _UUID.match(h):
            return f"https://www.autoscout24.de/angebote/{h}"
    return None
