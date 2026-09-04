# -*- coding: utf-8 -*-
"""Produktions-.env erzeugen — mit sicheren Zufallswerten statt Platzhaltern.

Der Produktions-Check des Backends verweigert den Start bei schwachen oder
vergessenen Werten. Dieses Skript legt eine vollstaendige `.env` an: alles,
was zufaellig sein muss, wird zufaellig erzeugt; alles, was du selbst
entscheiden musst, steht klar markiert mit `BITTE-AUSFUELLEN` darin.

Aufruf (auf dem Server, im Projektverzeichnis):
  python backend/scripts/env_erzeugen.py --domain app.auto-schnellkauf.de \\
      --admin-mail chef@auto-schnellkauf.de > .env

Vorhandene Werte uebernehmen (nichts neu wuerfeln):
  python backend/scripts/env_erzeugen.py --domain … --vorlage .env > .env.neu

Die Ausgabe enthaelt Passwoerter — nicht in Chats, Tickets oder Screenshots
weitergeben und die Datei nur mit `chmod 600` ablegen.
"""
import argparse
import re
import secrets
import string
import sys
from pathlib import Path

ZEICHEN = string.ascii_letters + string.digits + "!%*+-_"
# Fuer Werte, die in eine Verbindungs-URL wandern (MONGO_URL): nur Zeichen,
# die dort KEINE Sonderbedeutung haben. Ein "%" oder "@" im Passwort macht
# die Verbindungszeichenfolge sonst ungueltig.
ZEICHEN_URL = string.ascii_letters + string.digits + "-_.~"


def passwort(laenge: int = 24) -> str:
    """Zufallspasswort fuer Anmeldungen (nicht fuer URLs)."""
    return "".join(secrets.choice(ZEICHEN) for _ in range(laenge))


def passwort_url(laenge: int = 32) -> str:
    """Zufallspasswort, das gefahrlos in einer Verbindungs-URL stehen darf."""
    return "".join(secrets.choice(ZEICHEN_URL) for _ in range(laenge))


def geheimnis(bytes_: int = 32) -> str:
    return secrets.token_hex(bytes_)


def vorlage_lesen(pfad: str) -> dict:
    werte = {}
    if not pfad:
        return werte
    p = Path(pfad)
    if not p.is_file():
        print(f"# Hinweis: Vorlage {pfad} nicht gefunden — alles neu erzeugt",
              file=sys.stderr)
        return werte
    for zeile in p.read_text(encoding="utf-8").splitlines():
        if "=" in zeile and not zeile.strip().startswith("#"):
            k, _, v = zeile.partition("=")
            if v.strip():
                werte.setdefault(k.strip(), v.strip())
    return werte


def main() -> int:
    # Immer UTF-8 ausgeben: unter Windows schreibt Python sonst in der
    # Systemkodierung, und die erzeugte .env waere auf dem Linux-Server kaputt.
    for strom in (sys.stdout, sys.stderr):
        try:
            strom.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Produktions-.env erzeugen")
    ap.add_argument("--domain", required=True,
                    help="oeffentliche Adresse, z.B. app.auto-schnellkauf.de")
    ap.add_argument("--admin-mail", default="",
                    help="E-Mail des ersten Admin-Kontos")
    ap.add_argument("--mongo-host", default="mongo",
                    help="Mongo-Adresse: 'mongo' (Docker) oder die private IP, "
                         "z.B. 10.0.0.2")
    ap.add_argument("--vorlage", default="",
                    help="bestehende .env, deren Werte uebernommen werden")
    ap.add_argument("--mail-von", default="Auto Schnellkauf <vertrag@auto-schnellkauf.de>")
    ap.add_argument("--mail-name", default="Auto Schnellkauf")
    args = ap.parse_args()

    domain = args.domain.strip().lower().lstrip("https://").lstrip("http://").rstrip("/")
    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
        print(f"FEHLER: '{args.domain}' sieht nicht wie eine Domain aus", file=sys.stderr)
        return 1
    alt = vorlage_lesen(args.vorlage)

    def wert(name: str, neu):
        return alt.get(name) or (neu() if callable(neu) else neu)

    mongo_user = wert("MONGO_USER", "autoschnell_app")
    mongo_pw = wert("MONGO_PASSWORD", passwort_url)
    mongo_url = alt.get("MONGO_URL") or (
        f"mongodb://{mongo_user}:{mongo_pw}@{args.mongo_host}:27017/"
        f"?authSource=admin&maxPoolSize=20")

    zeilen = [
        "# AutoSchnell — Produktionskonfiguration",
        f"# erzeugt fuer {domain}. Datei mit 'chmod 600 .env' schuetzen.",
        "",
        "# ---- Betrieb ----",
        "APP_ENV=production",
        "SELF_SIGNUP=false",
        "WEB_CONCURRENCY=4",
        "SNAPSHOT_CONCURRENCY=1",
        "ENABLE_DOCS=false",
        "MOCK_PROVIDER_FETCH=false",
        "MOBILE_SANDBOX_MODE=false",
        "RATE_LIMIT_ENABLED=true",
        "TRUST_PROXY=true",
        "# Eigene Vermittler (Load Balancer, Proxy). Pflicht, sobald mehr als",
        "# ein Vermittler davorsteht - sonst sehen alle Besucher gleich aus.",
        f"TRUSTED_PROXIES={alt.get('TRUSTED_PROXIES') or '10.0.0.0/16,127.0.0.1'}",
        "# Betriebsart des Webservers. Hinter einem Load Balancer terminiert",
        "# dieser die Verschluesselung; nginx laeuft dann nur auf Port 80.",
        f"PROXY_TEMPLATE={alt.get('PROXY_TEMPLATE') or 'hinter-loadbalancer.conf.template'}",
        f"PRIVATES_NETZ={alt.get('PRIVATES_NETZ') or '10.0.0.0/16'}",
        "",
        "# ---- Adressen ----",
        f"PUBLIC_HOST={domain}",
        f"FRONTEND_URL=https://{domain}",
        f"CORS_ORIGINS=https://{domain}",
        "",
        "# ---- Datenbank ----",
        f"MONGO_USER={mongo_user}",
        f"MONGO_PASSWORD={mongo_pw}",
        f"MONGO_URL={mongo_url}",
        f"DB_NAME={wert('DB_NAME', 'autoschnell')}",
        "MONGO_CACHE_GB=4",
        "MONGO_MEM_LIMIT=6g",
        "",
        "# ---- Anmeldung ----",
        f"JWT_SECRET={wert('JWT_SECRET', geheimnis)}",
        f"ADMIN_EMAIL={alt.get('ADMIN_EMAIL') or args.admin_mail or 'BITTE-AUSFUELLEN@' + domain}",
        f"ADMIN_PASSWORD={wert('ADMIN_PASSWORD', passwort)}",
        f"SUPER_ADMIN_USERNAME={wert('SUPER_ADMIN_USERNAME', lambda: 'chef-' + secrets.token_hex(3))}",
        f"SUPER_ADMIN_PASSWORD={wert('SUPER_ADMIN_PASSWORD', passwort)}",
        "",
        "# ---- E-Mail (Resend) ----",
        f"RESEND_API_KEY={alt.get('RESEND_API_KEY') or 'BITTE-AUSFUELLEN-re_...'}",
        f"MAIL_FROM={alt.get('MAIL_FROM') or args.mail_von}",
        f"MAIL_ABSENDER_NAME={alt.get('MAIL_ABSENDER_NAME') or args.mail_name}",
        "",
        "# ---- Datei-Speicher ----",
        "# Bei MEHREREN App-Servern PFLICHT: sonst liegen Fotos und PDFs nur",
        "# auf dem Server, der sie angenommen hat. Hetzner Object Storage ist",
        "# S3-kompatibel (Endpunkt z.B. https://nbg1.your-objectstorage.com).",
        f"S3_ENDPOINT={alt.get('S3_ENDPOINT', '')}",
        f"S3_BUCKET={alt.get('S3_BUCKET', '')}",
        f"S3_ACCESS_KEY={alt.get('S3_ACCESS_KEY', '')}",
        f"S3_SECRET_KEY={alt.get('S3_SECRET_KEY', '')}",
        "S3_REGION=auto",
        "",
        "# ---- Sicherung ----",
        "BACKUP_DIR=/backups",
        "BACKUP_HOUR=3",
        f"BACKUP_S3_BUCKET={alt.get('BACKUP_S3_BUCKET', '')}",
        "BACKUP_S3_PREFIX=autoschnell-backups/",
        "BACKUP_S3_OBJECT_LOCK_DAYS=",
        "BACKUP_S3_KEEP=30",
        "",
        "# ---- Anbieter-Abrufe ----",
        f"APIFY_TOKEN={alt.get('APIFY_TOKEN') or 'BITTE-AUSFUELLEN-oder-leer-lassen'}",
        "# 0 = kein Tageslimit fuer mobile.de/AutoScout (Entscheidung 09/2026).",
        "# Ab ANBIETER_TAGESWARNUNG Abrufen gibt es EINEN Hinweis im Bereich",
        "# Betrieb — eine Warnung, kein Riegel.",
        "ANBIETER_TAGESLIMIT_JE_FIRMA=0",
        "ANBIETER_TAGESLIMIT_GESAMT=0",
        "ANBIETER_TAGESWARNUNG=500",
        "ABRUF_RUECKFALL_TAGESLIMIT=25",
        "",
        "# ---- Zahlungen (leer lassen, solange nicht genutzt) ----",
        f"STRIPE_API_KEY={alt.get('STRIPE_API_KEY', '')}",
        f"STRIPE_WEBHOOK_SECRET={alt.get('STRIPE_WEBHOOK_SECRET', '')}",
        "",
        "# ---- Fachliche Schalter ----",
        "VERKAUF_KOSTENLOS=true",
        "# Erst auf true stellen, wenn die 90-Tage-Loeschung wirklich loeschen soll",
        "VERTRAG_LOESCHUNG_AKTIV=false",
        "AUTO_DATEN_SCHAEDEN_FREITEXT=false",
        "",
    ]
    print("\n".join(zeilen))
    offen = [z.split("=")[0] for z in zeilen if "BITTE-AUSFUELLEN" in z]
    leer = [z.split("=")[0] for z in zeilen
            if z.endswith("=") and z.split("=")[0] in
            ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY")]
    if offen or leer:
        print("\n# NOCH ZU ERGAENZEN: " + ", ".join(offen + leer), file=sys.stderr)
    print("# Passwoerter wurden zufaellig erzeugt — sicher ablegen "
          "(Passwortmanager).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
