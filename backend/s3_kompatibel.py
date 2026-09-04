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


def client_konfiguration(endpoint: Optional[str] = None):
    """botocore-Config passend zum Ziel (oder None, wenn nichts noetig ist)."""
    endpoint = endpoint if endpoint is not None else os.environ.get("S3_ENDPOINT", "")
    wahl = (os.environ.get("S3_PRUEFSUMMEN") or "auto").strip().lower()
    if wahl == "immer":
        return None
    if wahl != "nur_noetig" and not _eigenwillig(endpoint):
        return None
    try:
        from botocore.config import Config
    except ImportError:                                   # pragma: no cover
        return None
    try:
        return Config(signature_version="s3v4",
                      request_checksum_calculation="when_required",
                      response_checksum_validation="when_required")
    except (TypeError, ValueError):
        # Aeltere botocore-Fassungen kennen die Schalter nicht — dort gab es
        # das Problem auch noch nicht.
        try:
            from botocore.config import Config as _C
            return _C(signature_version="s3v4")
        except Exception:                                 # pragma: no cover
            return None


def s3_client(*, endpoint: str = None, bucket_unbenutzt: str = None,
              access_key: str = None, secret_key: str = None,
              region: str = None):
    """boto3-Client mit den passenden Eigenheiten des jeweiligen Anbieters."""
    import boto3
    endpoint = endpoint if endpoint is not None else os.environ.get("S3_ENDPOINT", "")
    kwargs = {
        "aws_access_key_id": access_key or os.environ.get("S3_ACCESS_KEY"),
        "aws_secret_access_key": secret_key or os.environ.get("S3_SECRET_KEY"),
        "region_name": region or os.environ.get("S3_REGION") or "auto",
    }
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    cfg = client_konfiguration(endpoint)
    if cfg is not None:
        kwargs["config"] = cfg
    return boto3.client("s3", **kwargs)
