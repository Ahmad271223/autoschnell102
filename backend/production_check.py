# -*- coding: utf-8 -*-
"""Produktions-Validierung beim Start (Priorität 5).

Mit APP_ENV=production (setzt docker-compose) verweigert das Backend den
Start, wenn erkennbar Entwicklungswerte konfiguriert sind — lieber ein
klarer Abbruch mit Anleitung als ein oeffentlich erreichbares System mit
Demo-Passwort und Dev-Secret. Ausserhalb von production wird nur gewarnt.

Runde 5: laeuft jetzt VOR Indexanlage und Admin-Seeding (server.on_start),
damit eine fehlerhafte Produktionseinstellung die Datenbank nicht mehr
veraendert, bevor der Start abbricht. Zusaetzlich geprueft: Schreibrechte
fuer Backup-/Upload-/Snapshot-Verzeichnisse, positive Aufbewahrungsfristen,
SMTP (Passwort-Reset/Vertragsversand), Stripe/Apify/S3-Konsistenz.
"""
import os
import sys
import uuid
from pathlib import Path

# Bekannte Platzhalter/Dev-Werte, die NIE in Produktion laufen duerfen.
_VERBOTENE_SECRETS = {
    "", "dev-secret", "changeme", "secret", "test",
    "BITTE-ERSETZEN-langer-zufallswert",
}
_VERBOTENE_PASSWOERTER = {
    "", "Admin123!", "admin", "passwort", "password",
    "BITTE-STARKES-PASSWORT", "ci-only-admin-pw-1", "ci-only-superadmin-pw-1",
}


def _schreibbar(pfad: Path) -> bool:
    try:
        pfad.mkdir(parents=True, exist_ok=True)
        probe = pfad / f".schreibtest-{uuid.uuid4().hex[:8]}"
        probe.write_bytes(b"ok")
        probe.unlink()
        return True
    except OSError:
        return False


def _int_env(name: str, default: str) -> int:
    try:
        return int(os.environ.get(name, default) or default)
    except ValueError:
        return -1


def pruefe_produktion(log) -> None:
    ist_prod = os.environ.get("APP_ENV", "").strip().lower() == "production"
    fehler = []
    warnungen = []

    jwt = os.environ.get("JWT_SECRET", "").strip()
    if jwt in _VERBOTENE_SECRETS or len(jwt) < 32:
        fehler.append(
            "JWT_SECRET fehlt, ist ein Platzhalter oder zu kurz (<32 "
            "Zeichen). Erzeugen mit: openssl rand -hex 32")

    admin_pw = os.environ.get("ADMIN_PASSWORD", "").strip()
    if admin_pw in _VERBOTENE_PASSWOERTER or len(admin_pw) < 12:
        fehler.append(
            "ADMIN_PASSWORD fehlt, ist ein bekannter Demo-/CI-Wert oder zu "
            "kurz (<12 Zeichen).")
    super_pw = os.environ.get("SUPER_ADMIN_PASSWORD", "").strip()
    if super_pw and (super_pw in _VERBOTENE_PASSWOERTER or len(super_pw) < 12):
        fehler.append("SUPER_ADMIN_PASSWORD ist gesetzt, aber unsicher.")

    frontend = os.environ.get("FRONTEND_URL", "").strip()
    if not frontend.startswith("https://") or "localhost" in frontend:
        fehler.append(
            "FRONTEND_URL muss in Produktion eine https-Adresse der echten "
            "Domain sein (Passwort-Reset-Links werden daraus gebaut).")

    cors = os.environ.get("CORS_ORIGINS", "").strip()
    if not cors or "localhost" in cors or cors == "*":
        fehler.append(
            "CORS_ORIGINS muss in Produktion die echte(n) Domain(s) "
            "enthalten — kein localhost, kein '*'.")

    mongo = os.environ.get("MONGO_URL", "").strip()
    if "@" not in mongo:
        fehler.append(
            "MONGO_URL enthaelt keine Zugangsdaten (user:pass@...). In "
            "Produktion MUSS MongoDB mit Authentifizierung laufen und darf "
            "nur privat (Docker-Netz/localhost, KEIN oeffentlicher Port) "
            "erreichbar sein — siehe docs/STAGING-CHECKLISTE.md.")

    if os.environ.get("MOCK_PROVIDER_FETCH", "").strip().lower() in (
            "1", "true", "yes"):
        fehler.append(
            "MOCK_PROVIDER_FETCH ist aktiv — der Lasttest-Mock liefert "
            "erfundene Fahrzeugdaten und hat in Produktion nichts verloren.")

    # --- Runde 5: Betriebsvoraussetzungen ---
    backend = Path(__file__).resolve().parent
    verzeichnisse = {
        "Backups (BACKUP_DIR)": Path(os.environ.get("BACKUP_DIR", "") or
                                     (backend / "backups")),
        "Uploads (Fotos/Unterschriften/Protokolle)": backend / "uploads",
        "Snapshots (local_storage)": backend / "local_storage",
    }
    for name, pfad in verzeichnisse.items():
        if not _schreibbar(pfad):
            (fehler if ist_prod else warnungen).append(
                f"{name}: Verzeichnis {pfad} ist nicht beschreibbar — Backups/"
                "Uploads/Snapshots wuerden im Betrieb scheitern (Volume-"
                "Rechte fuer Benutzer 'app' pruefen).")

    for var, default in (("VERTRAG_AUFBEWAHRUNG_TAGE", "90"),
                         ("SNAPSHOT_RETENTION_DAYS", "60")):
        if _int_env(var, default) <= 0:
            fehler.append(f"{var} muss eine positive Zahl (Tage) sein — "
                          "0 oder negativ wuerde SOFORT loeschen.")

    smtp_fehlt = [v for v in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_FROM")
                  if not os.environ.get(v, "").strip()]
    if smtp_fehlt:
        (fehler if ist_prod else warnungen).append(
            "SMTP unvollstaendig (" + ", ".join(smtp_fehlt) + ") — Passwort-"
            "Reset-Mails und Vertragsversand per E-Mail koennen nicht gesendet "
            "werden.")

    if not os.environ.get("STRIPE_API_KEY", "").strip() or \
            not os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip():
        warnungen.append("Stripe ist nicht (vollstaendig) konfiguriert — Abo-"
                         "Zahlungen laufen dann nur ueber manuelle Freischaltung.")
    if not os.environ.get("APIFY_TOKEN", "").strip():
        warnungen.append("APIFY_TOKEN fehlt — mobile.de/AutoScout24-Abrufe sind "
                         "nicht moeglich.")
    s3 = {v: os.environ.get(v, "").strip()
          for v in ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY")}
    if any(s3.values()) and not all(s3.values()):
        fehler.append("S3 ist nur teilweise konfiguriert (" +
                      ", ".join(k for k, v in s3.items() if not v) +
                      " fehlt) — Storage wuerde still auf lokale Platte fallen.")

    # Audit 09/2026 (Punkt 43): fail-closed fuer angebotene Pflichtfunktionen
    stripe_key = os.environ.get("STRIPE_API_KEY", "").strip()
    stripe_whsec = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if bool(stripe_key) != bool(stripe_whsec):
        (fehler if ist_prod else warnungen).append(
            "Stripe nur halb konfiguriert (STRIPE_API_KEY und STRIPE_WEBHOOK_SECRET "
            "beide setzen oder beide leer lassen — sonst ist Online-Zahlung aus).")
    if os.environ.get("SELF_SIGNUP", "").strip().lower() in ("1", "true", "yes") and ist_prod:
        warnungen.append("SELF_SIGNUP=true: offene Selbstregistrierung ist in Produktion aktiv.")
    if os.environ.get("AUTO_DATEN_SCHAEDEN_FREITEXT", "").strip().lower() in ("1", "true", "yes"):
        warnungen.append("AUTO_DATEN_SCHAEDEN_FREITEXT=true: Freitext-Schaeden koennen "
                         "Personendaten enthalten (Standard: false).")
    if os.environ.get("VERTRAG_LOESCHUNG_AKTIV", "").strip().lower() in ("1", "true", "yes"):
        warnungen.append("VERTRAG_LOESCHUNG_AKTIV=true: automatische Vertragsloeschung ist scharf.")
    try:
        wc = int(os.environ.get("WEB_CONCURRENCY", "4") or 4)
        sc = int(os.environ.get("SNAPSHOT_CONCURRENCY", "1") or 1)
        if wc * sc > 8:
            warnungen.append(f"WEB_CONCURRENCY x SNAPSHOT_CONCURRENCY = {wc * sc} Chromium-"
                             "Prozesse (~400 MB je) — Serverspeicher pruefen.")
    except ValueError:
        (fehler if ist_prod else warnungen).append("WEB_CONCURRENCY/SNAPSHOT_CONCURRENCY muessen Zahlen sein")
    if ist_prod and "maxPoolSize" not in os.environ.get("MONGO_URL", ""):
        warnungen.append("MONGO_URL ohne maxPoolSize — je Worker bis zu 100 Verbindungen (Empfehlung: maxPoolSize=20).")
    for w in warnungen:
        log.warning("Produktions-Check (Hinweis): %s", w)
    if not fehler:
        if ist_prod:
            log.info("Produktions-Check: alle Pflichtwerte gesetzt.")
        return

    for f in fehler:
        (log.error if ist_prod else log.warning)(
            "Produktions-Check: %s", f)
    if ist_prod:
        log.error("Start ABGEBROCHEN: %d Konfigurationsfehler (siehe oben). "
                  ".env pruefen — Vorlage: .env.example", len(fehler))
        sys.exit(78)  # EX_CONFIG
