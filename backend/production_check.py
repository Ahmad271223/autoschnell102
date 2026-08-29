# -*- coding: utf-8 -*-
"""Produktions-Validierung beim Start (Priorität 5).

Mit APP_ENV=production (setzt docker-compose) verweigert das Backend den
Start, wenn erkennbar Entwicklungswerte konfiguriert sind — lieber ein
klarer Abbruch mit Anleitung als ein oeffentlich erreichbares System mit
'Admin123!' und Dev-Secret. Ausserhalb von production wird nur gewarnt.
"""
import os
import sys

# Bekannte Platzhalter/Dev-Werte, die NIE in Produktion laufen duerfen.
_VERBOTENE_SECRETS = {
    "", "dev-secret", "changeme", "secret", "test",
    "BITTE-ERSETZEN-langer-zufallswert",
}
_VERBOTENE_PASSWOERTER = {
    "", "Admin123!", "admin", "passwort", "password",
    "BITTE-STARKES-PASSWORT",
}


def pruefe_produktion(log) -> None:
    ist_prod = os.environ.get("APP_ENV", "").strip().lower() == "production"
    fehler = []

    jwt = os.environ.get("JWT_SECRET", "").strip()
    if jwt in _VERBOTENE_SECRETS or len(jwt) < 32:
        fehler.append(
            "JWT_SECRET fehlt, ist ein Platzhalter oder zu kurz (<32 "
            "Zeichen). Erzeugen mit: openssl rand -hex 32")

    admin_pw = os.environ.get("ADMIN_PASSWORD", "").strip()
    if admin_pw in _VERBOTENE_PASSWOERTER or len(admin_pw) < 12:
        fehler.append(
            "ADMIN_PASSWORD fehlt, ist ein bekannter Demo-Wert oder zu "
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
