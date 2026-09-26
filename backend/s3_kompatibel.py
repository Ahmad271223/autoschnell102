# -*- coding: utf-8 -*-
"""Einheitlicher S3-Zugang — auch fuer Cloudflare R2, MinIO und AWS.

Hintergrund: "S3-kompatibel" heisst nicht "in jedem Detail gleich". Zwei
Stolperstellen kosten sonst Stunden:

1. Pruefsummen-Kopfzeilen. Seit botocore 1.36 schickt boto3 bei jedem
   Hochladen zusaetzliche Pruefsummen (`x-amz-checksum-crc32`). Mehrere
   S3-kompatible Speicher — darunter Cloudflare R2 — lehnen Anfragen mit
   diesen Kopfzeilen ab oder verarbeiten sie falsch. Deshalb werden die
   Pruefsummen fuer solche Ziele auf "nur wenn noetig" gestellt.

2. Verschluesselung. `ServerSideEncryption: AES256` ist bei AWS ueblich,
   R2 lehnt die Kopfzeile ab (R2 verschluesselt ohnehin immer selbst).
   `sse_optionen()` liefert deshalb je nach Ziel die passenden Werte.

Steuerbar ueber die Umgebung:
    S3_SSE=auto | aes256 | aus      (Standard: auto)
    S3_PRUEFSUMMEN=auto | immer | nur_noetig   (Standard: auto)
"""
from __future__ import annotations

import os
from typing import Dict, Optional

# Endpunkte, die die AWS-Eigenheiten NICHT mitmachen.
_EIGENWILLIG = ("r2.cloudflarestorage.com", "storage.googleapis.com")


def ist_r2(endpoint: Optional[str]) -> bool:
    return "r2.cloudflarestorage.com" in (endpoint or "").lower()


def _eigenwillig(endpoint: Optional[str]) -> bool:
    e = (endpoint or "").lower()
    return any(m in e for m in _EIGENWILLIG)


def sse_optionen(endpoint: Optional[str] = None) -> Dict[str, str]:
    """Zusatzangaben fuer put_object — leer, wenn das Ziel keine
    Verschluesselungs-Kopfzeile akzeptiert."""
    wahl = (os.environ.get("S3_SSE") or "auto").strip().lower()
    if wahl in ("aus", "off", "none", "false"):
        return {}
    if wahl in ("aes256", "an", "on", "true"):
        return {"ServerSideEncryption": "AES256"}
    # auto
    endpoint = endpoint if endpoint is not None else os.environ.get("S3_ENDPOINT", "")
    return {} if _eigenwillig(endpoint) else {"ServerSideEncryption": "AES256"}


def _zahl(name: str, standard: float, unten: float) -> float:
    try:
        return max(unten, float((os.environ.get(name) or "").strip() or standard))
    except ValueError:
        return standard


def zeitlimits(sicherung: bool = False) -> Dict[str, object]:
    """Rollenprüfung 22.09.2026 (RP-550): Zeitlimits und Wiederholungen fuer
    JEDEN S3-Zugriff. Vorher bekam der Client keine — botocore wartet dann
    60 s auf die Verbindung, 60 s aufs Lesen und versucht es mehrmals. Hing
    R2 (Pakete verschluckt), standen Datei-Aufrufe minutenlang und blockierten
    den gemeinsamen Thread-Pool: Anmeldung (Passwortpruefung), PDF-Erzeugung
    und Vertragsdruck standen fuer alle.
      S3_VERBINDUNG_TIMEOUT_S  Standard 5
      S3_LESE_TIMEOUT_S        Standard 30
      S3_VERSUCHE              Standard 2 (Modus "standard")

    Rollenprüfung 22.09.2026 (Review): Die knappen Grenzen sind fuer den
    ANFRAGEWEG gedacht. Die naechtliche Sicherung (Offsite-Archiv mehrere GB,
    mehrteilig; Datei-Sicherung Objekt fuer Objekt) lief mit denselben Werten
    — wenige Wiederholungen je Teil und 30 s Lesezeit; ein kurzer
    5xx/SlowDown-Schub brach den ganzen Upload ab. Mit sicherung=True gelten
    eigene, grosszuegige Werte (nie knapper als die botocore-Vorgaben):
      BACKUP_S3_VERBINDUNG_TIMEOUT_S  Standard 10
      BACKUP_S3_LESE_TIMEOUT_S        Standard 120
      BACKUP_S3_VERSUCHE              Standard 5 (Modus "standard")
    Achtung Zaehlweise: botocore liest `max_attempts` in der Config als
    WIEDERHOLUNGEN nach dem ersten Versuch (5 -> 6 Versuche insgesamt, 2 -> 3).
    Die alte Vorgabe ohne Config waren 5 Versuche insgesamt."""
    if sicherung:
        return {"connect_timeout": _zahl("BACKUP_S3_VERBINDUNG_TIMEOUT_S", 10, 1),
                "read_timeout": _zahl("BACKUP_S3_LESE_TIMEOUT_S", 120, 1),
                "retries": {"max_attempts": int(_zahl("BACKUP_S3_VERSUCHE", 5, 1)),
                            "mode": "standard"}}
    return {"connect_timeout": _zahl("S3_VERBINDUNG_TIMEOUT_S", 5, 1),
            "read_timeout": _zahl("S3_LESE_TIMEOUT_S", 30, 1),
            "retries": {"max_attempts": int(_zahl("S3_VERSUCHE", 2, 1)),
                        "mode": "standard"}}


def client_konfiguration(endpoint: Optional[str] = None, *, sicherung: bool = False):
    """botocore-Config passend zum Ziel.

    Rollenprüfung 22.09.2026 (RP-550): liefert jetzt IMMER eine Config (mit
    Zeitlimits, siehe zeitlimits(); sicherung=True fuer die Sicherungs-
    skripte); die Pruefsummen-Schalter kommen nur fuer
    eigenwillige Ziele (R2 & Co.) bzw. S3_PRUEFSUMMEN=nur_noetig dazu. Nur
    ohne botocore (Tests ohne boto3) gibt es None."""
    endpoint = endpoint if endpoint is not None else os.environ.get("S3_ENDPOINT", "")
    wahl = (os.environ.get("S3_PRUEFSUMMEN") or "auto").strip().lower()
    pruefsummen_zahm = wahl != "immer" and (wahl == "nur_noetig" or _eigenwillig(endpoint))
    try:
        from botocore.config import Config
    except ImportError:                                   # pragma: no cover
        return None
    grenzen = zeitlimits(sicherung)
    if not pruefsummen_zahm:
        try:
            return Config(**grenzen)
        except (TypeError, ValueError):                   # pragma: no cover
            return None
    try:
        return Config(signature_version="s3v4",
                      request_checksum_calculation="when_required",
                      response_checksum_validation="when_required", **grenzen)
    except (TypeError, ValueError):
        # Aeltere botocore-Fassungen kennen die Schalter nicht — dort gab es
        # das Problem auch noch nicht.
        try:
            from botocore.config import Config as _C
            return _C(signature_version="s3v4", **grenzen)
        except Exception:                                 # pragma: no cover
            return None


def s3_client(*, endpoint: str = None, bucket_unbenutzt: str = None,
              access_key: str = None, secret_key: str = None,
              region: str = None, sicherung: bool = False):
    """boto3-Client mit den passenden Eigenheiten des jeweiligen Anbieters.
    sicherung=True: Zeitlimits der Sicherungsskripte statt der knappen des
    Anfragewegs (Rollenprüfung 22.09.2026, Review; siehe zeitlimits())."""
    import boto3
    # Nachpruefung 20.09.2026, Nr. 46: Schluessel und Geheimnis fielen
    # EINZELN auf die S3_*-Werte zurueck. War nur einer der beiden gesetzt
    # (z. B. BACKUP_S3_ACCESS_KEY ohne BACKUP_S3_SECRET_KEY), entstand ein
    # GEMISCHTES Paar: Schluessel der Sicherung, Geheimnis des Datei-
    # Speichers. Jeder Zugriff scheiterte dann mit einem Signaturfehler, und
    # niemand sah, warum. Die beiden gehoeren zusammen — entweder beide oder
    # keiner.
    if bool(access_key) != bool(secret_key):
        fehlt = "Geheimnis" if access_key else "Schluessel"
        raise ValueError(
            f"S3-Zugangsdaten unvollstaendig: das {fehlt} fehlt. Schluessel und "
            f"Geheimnis muessen zusammen angegeben werden — sonst entstuende "
            f"ein gemischtes Paar aus zwei verschiedenen Zugaengen.")
    endpoint = endpoint if endpoint is not None else os.environ.get("S3_ENDPOINT", "")
    kwargs = {
        "aws_access_key_id": access_key or os.environ.get("S3_ACCESS_KEY"),
        "aws_secret_access_key": secret_key or os.environ.get("S3_SECRET_KEY"),
        "region_name": region or os.environ.get("S3_REGION") or "auto",
    }
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    cfg = client_konfiguration(endpoint, sicherung=sicherung)
    if cfg is not None:
        kwargs["config"] = cfg
    return boto3.client("s3", **kwargs)
