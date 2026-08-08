"""Datei-Storage-Abstraktion für Fahrzeugfotos & Abholberichte.

Architektur-Entscheidung (05.08.2026): Fotos werden NICHT als Base64 in
MongoDB gespeichert. Stattdessen gibt es ein S3-kompatibles Interface mit
austauschbaren Backends:

  * LocalDiskStorage  — Default. Legt Dateien unter backend/uploads/ ab,
                        ausgeliefert über GET /api/files/{key}. Null Setup.
  * S3Storage         — Aktiviert über env S3_ENDPOINT/S3_BUCKET/S3_ACCESS_KEY/
                        S3_SECRET_KEY. Funktioniert mit MinIO, AWS S3,
                        Cloudflare R2 und jedem S3-kompatiblen Anbieter.

Alle Aufrufer nutzen ausschließlich `storage` (Singleton) — der Wechsel des
Backends erfordert keine Code-Änderung an den Routen.

Keys sind relative Pfade wie "resale/<dealer_id>/<uuid>.jpg".
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Optional

_UPLOAD_ROOT = Path(__file__).resolve().parent / "uploads"

# Erlaubte Datei-Endungen (Fotos + kurze Videos vom Abhol-Check).
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".webm", ".pdf"}
MAX_FILE_MB = 25

_KEY_RE = re.compile(r"^[a-z0-9_\-]+(/[A-Za-z0-9_\-.]+)+$")


class StorageError(ValueError):
    pass


def _validate_key(key: str) -> str:
    """Verhindert Path-Traversal: Keys sind streng whitelisted."""
    if not _KEY_RE.match(key) or ".." in key:
        raise StorageError(f"Ungültiger Storage-Key: {key!r}")
    return key


def make_key(category: str, dealer_id: str, filename: str) -> str:
    """Erzeugt einen kollisionsfreien Key: <category>/<dealer>/<uuid><ext>."""
    ext = os.path.splitext(filename or "")[1].lower() or ".jpg"
    if ext not in ALLOWED_EXTENSIONS:
        raise StorageError(f"Dateityp {ext} nicht erlaubt")
    safe_cat = re.sub(r"[^a-z0-9_\-]", "", category.lower()) or "misc"
    safe_dealer = re.sub(r"[^A-Za-z0-9_\-]", "", dealer_id) or "unknown"
    return f"{safe_cat}/{safe_dealer}/{uuid.uuid4().hex}{ext}"


class LocalDiskStorage:
    """Dateien auf der lokalen Platte unter backend/uploads/."""

    name = "local"

    def __init__(self, root: Path = _UPLOAD_ROOT):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, key: str, data: bytes) -> str:
        _validate_key(key)
        if len(data) > MAX_FILE_MB * 1024 * 1024:
            raise StorageError(f"Datei zu groß (max. {MAX_FILE_MB} MB)")
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def load(self, key: str) -> bytes:
        _validate_key(key)
        path = self.root / key
        if not path.is_file():
            raise StorageError("Datei nicht gefunden")
        return path.read_bytes()

    def delete(self, key: str) -> bool:
        _validate_key(key)
        path = self.root / key
        if path.is_file():
            path.unlink()
            return True
        return False

    def exists(self, key: str) -> bool:
        try:
            _validate_key(key)
        except StorageError:
            return False
        return (self.root / key).is_file()


class S3Storage:
    """S3-kompatibles Backend (MinIO / AWS S3 / Cloudflare R2 / …).

    Aktivierung über env:
      S3_ENDPOINT=https://…   S3_BUCKET=autoschnell
      S3_ACCESS_KEY=…         S3_SECRET_KEY=…
      S3_REGION=auto          (optional)
    """

    name = "s3"

    def __init__(self):
        import boto3  # lazy — nur nötig, wenn S3 konfiguriert ist
        self.bucket = os.environ["S3_BUCKET"]
        self.client = boto3.client(
            "s3",
            endpoint_url=os.environ["S3_ENDPOINT"],
            aws_access_key_id=os.environ["S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["S3_SECRET_KEY"],
            region_name=os.environ.get("S3_REGION", "auto"),
        )

    def save(self, key: str, data: bytes) -> str:
        _validate_key(key)
        if len(data) > MAX_FILE_MB * 1024 * 1024:
            raise StorageError(f"Datei zu groß (max. {MAX_FILE_MB} MB)")
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return key

    def load(self, key: str) -> bytes:
        _validate_key(key)
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def delete(self, key: str) -> bool:
        _validate_key(key)
        self.client.delete_object(Bucket=self.bucket, Key=key)
        return True

    def exists(self, key: str) -> bool:
        try:
            _validate_key(key)
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


def _build_storage():
    if os.environ.get("S3_ENDPOINT") and os.environ.get("S3_BUCKET"):
        try:
            return S3Storage()
        except Exception as exc:  # boto3 fehlt / Config kaputt → lokal weiter
            import logging
            logging.getLogger("autohandel").warning(
                "S3-Storage nicht verfügbar (%s) — nutze lokalen Storage.", exc)
    return LocalDiskStorage()


storage = _build_storage()


def guess_media_type(key: str) -> str:
    ext = os.path.splitext(key)[1].lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".mp4": "video/mp4", ".webm": "video/webm",
        ".pdf": "application/pdf",
    }.get(ext, "application/octet-stream")
