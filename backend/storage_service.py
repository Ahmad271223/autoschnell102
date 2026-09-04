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


MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_UPLOAD_BYTES",
                                      str(8 * 1024 * 1024)))  # 8 MB

# Magic Bytes der erlaubten Bildformate — verhindert, dass ausführbare
# oder beliebige Dateien als "Foto" im Speicher landen.
_IMAGE_MAGIC = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"RIFF", "webp"),      # + 'WEBP' an Offset 8, unten geprueft
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)


def validate_image_bytes(raw: bytes, wo: str = "Foto") -> None:
    """Wirft StorageError, wenn die Datei zu gross ist oder kein
    bekanntes Bildformat traegt (Sicherheits-/Groessenpruefung fuer
    alle Foto-Uploads)."""
    if not raw:
        raise StorageError(f"{wo}: leere Datei")
    if len(raw) > MAX_IMAGE_BYTES:
        raise StorageError(
            f"{wo}: Datei zu gross ({len(raw) // (1024*1024)} MB, "
            f"erlaubt {MAX_IMAGE_BYTES // (1024*1024)} MB)")
    for magic, art in _IMAGE_MAGIC:
        if raw.startswith(magic):
            if art == "webp" and raw[8:12] != b"WEBP":
                continue
            return
    raise StorageError(f"{wo}: kein gueltiges Bildformat "
                       "(erlaubt: JPEG, PNG, WebP, GIF)")


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

    def delete_prefix(self, prefix: str) -> int:
        """Alle Dateien unter einem Praefix loeschen (DSGVO-Firmenloeschung,
        z.B. 'protocol/<dealer_id>/'). Liefert die Anzahl geloeschter
        Dateien. Der Praefix wird wie ein Key validiert (kein '..')."""
        _validate_key(prefix.rstrip("/") + "/x.jpg")   # Traversal-Schutz
        ordner = self.root / prefix.rstrip("/")
        if not ordner.is_dir():
            return 0
        n = sum(1 for p in ordner.rglob("*") if p.is_file())
        import shutil
        shutil.rmtree(ordner, ignore_errors=True)
        return n


class S3Storage:
    """S3-kompatibles Backend (MinIO / AWS S3 / Cloudflare R2 / …).

    Aktivierung über env:
      S3_ENDPOINT=https://…   S3_BUCKET=autoschnell
      S3_ACCESS_KEY=…         S3_SECRET_KEY=…
      S3_REGION=auto          (optional)
    """

    name = "s3"

    def __init__(self):
        # Gemeinsamer Zugang: setzt bei Cloudflare R2 & Co. die noetigen
        # Eigenheiten (Pruefsummen, Verschluesselungs-Kopfzeile).
        from s3_kompatibel import s3_client
        self.bucket = os.environ["S3_BUCKET"]
        self.client = s3_client(endpoint=os.environ["S3_ENDPOINT"])

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

    def delete_prefix(self, prefix: str) -> int:
        """Alle Objekte unter einem Praefix loeschen (paginiert)."""
        _validate_key(prefix.rstrip("/") + "/x.jpg")
        n = 0
        token = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            seite = self.client.list_objects_v2(**kwargs)
            keys = [{"Key": o["Key"]} for o in seite.get("Contents", [])]
            if keys:
                self.client.delete_objects(Bucket=self.bucket,
                                           Delete={"Objects": keys})
                n += len(keys)
            if not seite.get("IsTruncated"):
                return n
            token = seite.get("NextContinuationToken")

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
        except Exception as exc:  # boto3 fehlt / Config kaputt
            import logging
            log = logging.getLogger("autohandel")
            if os.environ.get("APP_ENV", "").strip().lower() == "production":
                # In Produktion NICHT still auf die lokale Platte ausweichen:
                # Fotos und PDFs laegen dann auf EINEM Server, der zweite
                # saehe sie nie, und die Sicherung wuerde sie nicht erfassen.
                # Der Aufbau des Clients scheitert nur bei echten
                # Konfigurationsfehlern (fehlendes boto3, kaputte Angaben) —
                # eine voruebergehende Stoerung von S3 fuehrt NICHT hierher.
                log.error("Datei-Speicher: S3/R2 ist konfiguriert, laesst sich "
                          "aber nicht aufbauen (%s). Start abgebrochen — sonst "
                          "landen Fotos unbemerkt auf der lokalen Platte.", exc)
                raise RuntimeError(
                    f"S3/R2 konfiguriert, aber nicht nutzbar: {exc}. "
                    "Entweder die S3_*-Werte korrigieren oder sie leeren, "
                    "wenn bewusst lokal gespeichert werden soll.") from exc
            log.warning("S3-Storage nicht verfügbar (%s) — nutze lokalen Storage.", exc)
    return LocalDiskStorage()


storage = _build_storage()


# ---------- Async-Wrapper (Review 09/2026) ----------
# Beide Backends blockieren (Platte bzw. boto3-HTTP). Async-Routen muessen
# diese Wrapper nutzen, sonst steht der ganze Event-Loop des Workers,
# waehrend EINE Datei geschrieben/gelesen/geloescht wird.
import asyncio as _asyncio


async def save_async(key: str, data: bytes) -> str:
    return await _asyncio.to_thread(storage.save, key, data)


async def load_async(key: str) -> bytes:
    return await _asyncio.to_thread(storage.load, key)


async def delete_async(key: str) -> bool:
    return await _asyncio.to_thread(storage.delete, key)


async def delete_prefix_async(prefix: str) -> int:
    return await _asyncio.to_thread(storage.delete_prefix, prefix)


# ---------- Loeschen oder vormerken (Go-Live-Audit 09/2026) ----------
# Bisher galt eine fehlgeschlagene Datei-Loeschung stillschweigend als
# Erfolg: der Aufrufer entfernte den Key aus seinem Dokument, die Datei
# blieb fuer immer liegen (Fotos, Snapshots, Protokoll-PDFs, Unterschriften).
# Jetzt gibt es EINEN zentralen Weg: klappt die Loeschung, True; sonst wird
# sie in `storage_delete_retry` vorgemerkt und der Aufraeumjob
# (cleanup_service.storage_loeschungen_nachholen) holt sie nach.
#
# REGEL fuer Aufrufer: Den Key/das Feld im eigenen Dokument NUR entfernen,
# wenn der Helfer True liefert. Bei False bleibt der Key stehen und das
# Dokument bekommt `<feld>_loeschung_offen: True` (o.ae.), damit nichts
# verloren geht. `ref` beschreibt, wo der Key referenziert ist, damit die
# Nachholung das Dokument nach Erfolg selbst bereinigen kann:
#   {"collection": <Name>, "id": <Dokument-id>, "unset_fields": [...]}
# optional zusaetzlich:
#   "pull_key_from": <Array-Feld>        -> $pull des Keys aus dieser Liste
#   "array": {"pfad": <Array-Feld>, "schluessel": <Elementfeld>}
#                                        -> unset_fields/set_fields gelten
#                                           fuer das Element mit Feld == Key
#   "set_fields": {<Feld>: <Wert|"$now">} -> nach Erfolg setzen
#   "loeschen_wenn_leer": [<Felder>]     -> Dokument loeschen, sobald keines
#                                           dieser Felder mehr belegt ist
async def loeschen_oder_vormerken(db, *, key: Optional[str] = None,
                                  prefix: Optional[str] = None, grund: str,
                                  dealer_id: str = "", ref: Optional[dict] = None,
                                  art: str = "storage") -> bool:
    """Datei (key), Ordner (prefix) oder Snapshot-Objekt (art="snapshot",
    key) loeschen. True = weg. False = NICHT geloescht, aber in
    `storage_delete_retry` vorgemerkt (wird nie stillschweigend verloren).
    Wirft selbst nie."""
    import logging
    import uuid as _uuid
    from datetime import datetime, timezone
    log = logging.getLogger("autohandel.storage")
    if art == "snapshot":
        art_eintrag = "snapshot"
    elif prefix:
        art_eintrag = "prefix"
    else:
        art_eintrag = "key"
    fehler: Optional[str] = None
    try:
        if art_eintrag == "snapshot":
            if not key:
                raise StorageError("Snapshot-Loeschung ohne Key")
            from snapshot_service import delete_object_async
            if not await delete_object_async(key):
                raise RuntimeError("Snapshot-Storage meldet Fehlschlag")
        elif art_eintrag == "prefix":
            await delete_prefix_async(prefix)
        else:
            if not key:
                raise StorageError("Datei-Loeschung ohne Key")
            # False = Datei gab es nicht mehr -> Ziel erreicht (idempotent).
            await delete_async(key)
        return True
    except Exception as exc:  # noqa: BLE001
        fehler = str(exc)[:300]
    jetzt = datetime.now(timezone.utc).isoformat()
    log.warning("Datei-Loeschung fehlgeschlagen (%s %s, %s) — vorgemerkt: %s",
                art_eintrag, key or prefix, grund, fehler)
    try:
        # Upsert je (art, key/prefix): derselbe Key wird nicht mehrfach
        # vorgemerkt, wenn der Aufraeumjob ihn stuendlich erneut anfasst.
        await db.storage_delete_retry.update_one(
            {"art": art_eintrag, "key": key, "prefix": prefix},
            {"$setOnInsert": {"id": str(_uuid.uuid4()), "art": art_eintrag,
                              "key": key, "prefix": prefix, "versuche": 0,
                              "created_at": jetzt},
             "$set": {"grund": grund, "dealer_id": dealer_id or "",
                      "ref": ref, "letzter_fehler": fehler, "updated_at": jetzt}},
            upsert=True)
    except Exception:  # noqa: BLE001
        log.exception("storage_delete_retry konnte nicht geschrieben werden (%s)",
                      key or prefix)
    return False


def guess_media_type(key: str) -> str:
    ext = os.path.splitext(key)[1].lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".mp4": "video/mp4", ".webm": "video/webm",
        ".pdf": "application/pdf",
    }.get(ext, "application/octet-stream")
