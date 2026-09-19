# -*- coding: utf-8 -*-
"""Gemeinsame Bewertung einer Datensicherung (Runde 21, Pruefbefund Backup).

Genutzt von scripts/backup_mongo.py (Rotation, Exit-Code), scripts/
restore_mongo.py (Ablehnung beim Einspielen) und backup_service.py
(Backup-Stand fuer /ready, Admin/Betrieb und die Nachholung). Absichtlich
OHNE Abhaengigkeiten (kein deps, kein pymongo), damit die Skripte das
Modul auch auf einem Rechner ohne Backend-Umgebung laden koennen.

Ein Backup ist GUT genau dann, wenn
  - eine manifest.json existiert,
  - "unvollstaendig" leer ist (Datei-Speicher, Offsite-Kopie) und
  - "inkonsistent" leer ist (alle Collections zeigen einen gemeinsamen
    Zeitpunkt oder die Sicherung ist ausdruecklich als Einzelserver-Lauf
    ohne Stichtagsgarantie zugelassen).

Konsistenz-Stufen im Manifest ("konsistenz"):
  "snapshot"                              stimmig (Replica Set, eine Snapshot-Sitzung)
  "stimmig (Schreibpause)"                stimmig (--wartung, Schreibzugriffe pausiert)
  "best-effort (standalone)"              Einzelserver ohne Replica Set; zugelassen
                                          fuer Entwicklung/Compose, NICHT stichtagsgenau
  "best-effort (snapshot fehlgeschlagen)" INKONSISTENT: Snapshot scheiterte, die
                                          Collections wurden nacheinander gelesen
Ab Manifest-Version 4 steht der Grund zusaetzlich im Feld "inkonsistent"
(leerer Text = kein Mangel). Alt-Manifeste ohne dieses Feld werden ueber
den konsistenz-Text bewertet.
"""
import os

KONSISTENZ_SNAPSHOT = "snapshot"
KONSISTENZ_SCHREIBPAUSE = "stimmig (Schreibpause)"
KONSISTENZ_STANDALONE = "best-effort (standalone)"
KONSISTENZ_RUECKFALL = "best-effort (snapshot fehlgeschlagen)"
STICHTAGSGENAU = (KONSISTENZ_SNAPSHOT, KONSISTENZ_SCHREIBPAUSE)

# Ab dieser Manifest-Version sind die Index-Metadaten je Collection Pflicht
# (Runde 21, Befund B) und das Feld "inkonsistent" ist gesetzt.
MANIFEST_VERSION_PFLICHT_INDEXE = 4


def _wahr(wert) -> bool:
    return str(wert or "").strip().lower() in ("1", "true", "yes", "ja", "on")


def snapshot_pflicht(mongo_url: str = None) -> bool:
    """Muss die Sicherung als Snapshot laufen?

    Ja, wenn BACKUP_SNAPSHOT_PFLICHT gesetzt ist oder die Verbindungsadresse
    ein Replica Set nennt (replicaSet=...). Dann darf ein Collection-fuer-
    Collection-Lauf nie als gutes Backup gelten, auch wenn die Erkennung
    des Replica Sets (hello/isMaster) gerade scheitert."""
    if _wahr(os.environ.get("BACKUP_SNAPSHOT_PFLICHT")):
        return True
    url = mongo_url if mongo_url is not None else os.environ.get("MONGO_URL", "")
    return "replicaset=" in str(url or "").lower()


def inkonsistenz(manifest) -> str:
    """Grund, warum das Backup NICHT in sich stimmig ist ("" = kein Mangel).

    Kein Manifest ist hier kein Konsistenzmangel (das bewertet ist_gut)."""
    if not isinstance(manifest, dict):
        return ""
    grund = manifest.get("inkonsistent")
    if grund:
        return str(grund)
    konsistenz = str(manifest.get("konsistenz") or "")
    if konsistenz.startswith("best-effort (snapshot fehlgeschlagen"):
        # Alt-Manifest (vor Version 4) oder Feld fehlt: der Text allein
        # belegt den Rueckfall.
        return ("Snapshot fehlgeschlagen; Collections nacheinander gelesen, "
                "Zeitstaende koennen abweichen")
    return ""


def unvollstaendig(manifest) -> list:
    if not isinstance(manifest, dict):
        return []
    return [str(x) for x in (manifest.get("unvollstaendig") or [])]


def mangel(manifest):
    """(unvollstaendig: list, inkonsistent: str) eines Manifests."""
    return unvollstaendig(manifest), inkonsistenz(manifest)


def ist_gut(manifest) -> bool:
    """True nur fuer ein Backup mit Manifest, ohne fehlende Teile und ohne
    Konsistenzmangel. Nur solche zaehlen als letzter guter Stand."""
    if not isinstance(manifest, dict) or not manifest:
        return False
    fehlend, grund = mangel(manifest)
    return not fehlend and not grund


def ist_stichtagsgenau(manifest) -> bool:
    return isinstance(manifest, dict) and not inkonsistenz(manifest) and \
        str(manifest.get("konsistenz") or "") in STICHTAGSGENAU


def metadaten_mangel(meta) -> str:
    """Pruefung einer geladenen <collection>.metadata.json ("" = in Ordnung).

    Pflicht: ein Objekt mit einer Liste "indexes"; jeder Eintrag hat einen
    Namen und einen nicht leeren Schluessel."""
    if not isinstance(meta, dict):
        return "kein JSON-Objekt"
    indexes = meta.get("indexes")
    if not isinstance(indexes, list):
        return "Feld 'indexes' fehlt oder ist keine Liste"
    for idx in indexes:
        if not isinstance(idx, dict) or not idx.get("name") \
                or not isinstance(idx.get("key"), dict) or not idx.get("key"):
            return f"unvollstaendiger Index-Eintrag: {str(idx)[:120]}"
    return ""
