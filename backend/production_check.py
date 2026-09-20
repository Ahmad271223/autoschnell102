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


# Fehlerkennungen, die eindeutig auf falsche Zugangsdaten oder einen
# falschen Eimernamen hindeuten — dagegen hilft kein Abwarten, das muss
# der Betreiber korrigieren. Alles andere (Netz, Zeitueberschreitung)
# kann voruebergehend sein und ist deshalb nur eine Warnung.
_S3_DAUERFEHLER = {
    "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch",
    "NoSuchBucket", "AllAccessDisabled", "InvalidBucketName",
    "AuthorizationHeaderMalformed", "403", "401", "404",
}


def _s3_wirklich_pruefen(bucket: str):
    """Schreibt eine winzige Probedatei, liest sie zurueck und loescht sie.

    Liefert (art, meldung) mit art aus "ok", "warnung", "fehler".
    Aendert nichts an echten Daten: der Schluessel liegt unter
    systempruefung/ und wird sofort wieder entfernt.
    """
    # Der Schluessel muss den eigenen Regeln aus storage_service genuegen,
    # damit ihn im Notfall auch delete_prefix wieder wegraeumen kann.
    schluessel = "systempruefung/start-probe.txt"
    try:
        from s3_kompatibel import s3_client, sse_optionen
        endpoint = os.environ["S3_ENDPOINT"].strip()
        client = s3_client(endpoint=endpoint)
        inhalt = b"autoschnell-startpruefung"
        client.put_object(Bucket=bucket, Key=schluessel, Body=inhalt,
                          **sse_optionen(endpoint))
        zurueck = client.get_object(Bucket=bucket, Key=schluessel)["Body"].read()
        client.delete_object(Bucket=bucket, Key=schluessel)
        if zurueck != inhalt:
            return ("fehler",
                    f"Datei-Speicher '{bucket}': zurueckgelesener Inhalt weicht ab "
                    "— der Eimer verhaelt sich nicht wie erwartet.")
        return ("ok", "")
    except Exception as exc:                        # noqa: BLE001
        text = f"{type(exc).__name__}: {exc}"
        code = ""
        antwort = getattr(exc, "response", None)
        if isinstance(antwort, dict):
            code = str((antwort.get("Error") or {}).get("Code") or "")
            if not code:
                code = str((antwort.get("ResponseMetadata") or {})
                           .get("HTTPStatusCode") or "")
        dauerhaft = code in _S3_DAUERFEHLER or any(
            w in text for w in ("AccessDenied", "InvalidAccessKeyId",
                                "SignatureDoesNotMatch", "NoSuchBucket"))
        if dauerhaft:
            return ("fehler",
                    f"Datei-Speicher '{bucket}' nicht benutzbar ({code or 'Fehler'}): "
                    f"{text}. Zugangsdaten oder Eimername pruefen — Fotos, "
                    "Protokolle und Sicherungen wuerden sonst nicht abgelegt.")
        return ("warnung",
                f"Datei-Speicher '{bucket}' antwortete beim Start nicht ({text}). "
                "Sieht nach einer voruebergehenden Stoerung aus; wenn das bleibt, "
                "Zugangsdaten und Netz pruefen.")


def pruefe_produktion(log) -> None:
    ist_prod = os.environ.get("APP_ENV", "").strip().lower() == "production"
    fehler = []
    warnungen = []

    jwt = os.environ.get("JWT_SECRET", "").strip()
    if jwt in _VERBOTENE_SECRETS or len(jwt) < 32:
        fehler.append(
            "JWT_SECRET fehlt, ist ein Platzhalter oder zu kurz (<32 "
            "Zeichen). Erzeugen mit: openssl rand -hex 32")

    # Kontonummer (13.09.2026), Schritt 5: den Bootstrap-Admin per E-Mail samt
    # Passwort-Pflicht gibt es nicht mehr (nur der Super-Admin wird geseedet).
    super_pw = os.environ.get("SUPER_ADMIN_PASSWORD", "").strip()
    if super_pw and (super_pw in _VERBOTENE_PASSWOERTER or len(super_pw) < 12):
        fehler.append("SUPER_ADMIN_PASSWORD ist gesetzt, aber unsicher.")
    elif super_pw:
        # Runde 15: dieselbe Regel wie fuer jedes Konto (Buchstabe + Ziffer/
        # Sonderzeichen, keine Allerweltswoerter, kein Benutzername darin).
        try:
            from passwoerter import pruefe_passwort
            pruefe_passwort(super_pw)
        except ValueError as exc:
            fehler.append(f"SUPER_ADMIN_PASSWORD ist gesetzt, aber unsicher: {exc}")
    # Ein Benutzername im Kontonummer-Muster (z.B. '10023') wuerde vom
    # Nummern-Zweig der Anmeldung verdeckt; seed_super_admin legt ihn dann
    # gar nicht an — der Betreiber waere ausgesperrt.
    super_name = os.environ.get("SUPER_ADMIN_USERNAME", "").strip()
    if super_name:
        from kontonummer import kennung_normalisieren
        if kennung_normalisieren(super_name):
            fehler.append(
                f"SUPER_ADMIN_USERNAME '{super_name}' sieht wie eine Kontonummer, ein "
                "Kaeufer-Code oder eine Fahrer-ID aus — bitte einen laengeren Benutzernamen "
                "mit Kleinbuchstaben und Bindestrich waehlen (sonst ist die "
                "Betreiber-Anmeldung nicht moeglich).")
    # Pruefung 14.09.2026 (F5): ohne Betreiberkonto startet Produktion nicht.
    if ist_prod and (not super_name or not super_pw):
        fehler.append("SUPER_ADMIN_USERNAME und SUPER_ADMIN_PASSWORD muessen in Produktion "
                      "gesetzt sein — sonst gibt es kein Betreiberkonto.")
    # Pruefung 14.09.2026 (A18/B30): Zahlen-Variablen mit Tippfehler
    from konfig import FEHLERHAFT, zahl_pruefen
    for name, roh in sorted(FEHLERHAFT.items()):
        (fehler if ist_prod else warnungen).append(
            f"{name}='{roh}' ist keine ganze Zahl — Standardwert aktiv, bitte korrigieren.")
    for name in ("LOGIN_KONTO_LIMIT", "LOGIN_KONTO_FENSTER", "LOGIN_IP_LIMIT", "BACKUP_S3_KEEP",
                 "WEB_CONCURRENCY", "MIN_FREI_MB", "BEWEIS_FOTOS_MAX", "BEWEIS_AUFBEWAHRUNG_TAGE",
                 "BEWEIS_PARALLEL", "FAHRERFOTO_TAGE", "BERICHT_AUFBEWAHRUNG_TAGE",
                 "ANBIETER_TAGESLIMIT_JE_FIRMA", "ANBIETER_TAGESLIMIT_GESAMT",
                 "ANBIETER_TAGESLIMIT_JE_KONTO",
                 "ANBIETER_TAGESWARNUNG", "ABRUF_RUECKFALL_TAGESLIMIT", "BILD_PROXY_LIMIT"):
        if name not in FEHLERHAFT:
            roh = zahl_pruefen(name)
            if roh is not None:
                (fehler if ist_prod else warnungen).append(
                    f"{name}='{roh}' ist keine ganze Zahl — bitte korrigieren.")

    # Befund 131 (16.09.2026): Entscheidung Ahmad 16.09.2026 — 400 neue Abrufe
    # je Konto und Tag. Im Code heisst 0/fehlend "aus"; docker-compose setzt
    # 400 vor. Wer ausserhalb des Compose startet oder 0 eintraegt, sieht das
    # hier als Warnung (kein Abbruch: Warnen statt Bremsen, Regel Ahmad).
    konto_limit = os.environ.get("ANBIETER_TAGESLIMIT_JE_KONTO", "").strip()
    if ist_prod and "ANBIETER_TAGESLIMIT_JE_KONTO" not in FEHLERHAFT:
        try:
            konto_limit_zahl = int(konto_limit) if konto_limit else 0
        except ValueError:
            konto_limit_zahl = 0
        if konto_limit_zahl <= 0:
            warnungen.append(
                "ANBIETER_TAGESLIMIT_JE_KONTO fehlt oder ist 0 — kein Tageslimit je "
                "Konto (Entscheidung 16.09.2026: 400). docker-compose setzt 400 vor; "
                "in der .env pruefen.")

    frontend = os.environ.get("FRONTEND_URL", "").strip()
    if not frontend.startswith("https://") or "localhost" in frontend:
        fehler.append(
            "FRONTEND_URL muss in Produktion eine https-Adresse der echten "
            "Domain sein (Links der Plattform, z.B. in Vertragsmails und "
            "Einladungen, werden daraus gebaut).")

    # Pruefung 14.09.2026 (Liste 4, Nr. 74): hinter Proxy/Load Balancer muss
    # TRUST_PROXY gesetzt sein — sonst zaehlt der Limiter alle Nutzer unter der
    # Proxy-Adresse (und Sperren treffen alle auf einmal).
    if ist_prod and os.environ.get("TRUST_PROXY", "").strip().lower() not in ("1", "true", "yes"):
        fehler.append("TRUST_PROXY=true fehlt — hinter nginx/Load Balancer sieht das Backend "
                      "sonst nur die Proxy-Adresse (Rate-Limiter, Anmeldesperren, Audit).")
    # Phase 3 (3.6, E8): Der Hetzner-Load-Balancer steht mit seiner privaten
    # 10.x-Adresse in der Kette. Mit TRUSTED_PROXIES_NUR_LISTE=true zaehlen
    # NUR die gelisteten Netze — dann muss das 10.x-Netz drinstehen.
    nur_liste = os.environ.get("TRUSTED_PROXIES_NUR_LISTE", "").strip().lower() in ("1", "true", "yes")
    if ist_prod and nur_liste:
        import ipaddress
        netze = []
        for roh in os.environ.get("TRUSTED_PROXIES", "").split(","):
            try:
                netze.append(ipaddress.ip_network(roh.strip(), strict=False))
            except ValueError:
                continue
        privat = ipaddress.ip_network("10.0.0.0/8")
        if not any(n.version == 4 and n.overlaps(privat) for n in netze):
            fehler.append("TRUSTED_PROXIES_NUR_LISTE=true, aber TRUSTED_PROXIES enthaelt kein "
                          "10.x-Netz — der Load Balancer (privates Hetzner-Netz) wuerde als "
                          "Besucher gezaehlt und alle Nutzer teilten sich einen Zaehler.")
    if ist_prod and os.environ.get("BACKUP_S3_BUCKET", "").strip() \
            and not os.environ.get("BACKUP_S3_ACCESS_KEY", "").strip():
        warnungen.append("BACKUP_S3_ACCESS_KEY/SECRET_KEY nicht gesetzt — die Offsite-Kopie nutzt "
                         "die Zugangsdaten des Datei-Speichers (Empfehlung: eigener, nur "
                         "schreibender Schluessel fuer den Sicherungs-Bucket).")

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
    if os.environ.get("RATE_LIMIT_ENABLED", "true").strip().lower() == "false":
        # Nachpruefung Runde 10: der Lasttest schaltet die Sperren ab —
        # in Produktion waere das ein offenes Scheunentor fuer Passwortraten.
        fehler.append(
            "RATE_LIMIT_ENABLED=false: Anmelde-/Registrierungssperren sind aus — "
            "nur fuer Tests/Lasttest, in Produktion nicht erlaubt.")

    # Runde 13: Befund B4 — DATEI_SIGNATUR_PFLICHT=false schaltete die
    # Signaturpruefung fuer resale/-Fotos in server.serve_file komplett ab
    # (vorher: reiner Uebergangsschalter, nirgends geprueft — der zufaellige
    # Storage-Key genuegte dann ohne Login; jetzt: in Produktion ist der
    # Schalter ein Startfehler, ausserhalb eine Warnung). Alle Erzeuger
    # signieren laengst (resale.py, marketplace.py), der Uebergang ist vorbei.
    if os.environ.get("DATEI_SIGNATUR_PFLICHT", "true").strip().lower() in ("0", "false", "no"):
        (fehler if ist_prod else warnungen).append(
            "DATEI_SIGNATUR_PFLICHT=false: Fahrzeugfotos unter resale/ waeren "
            "ohne Login und ohne Signatur fuer jeden abrufbar, der den "
            "Schluessel kennt. In Produktion nicht erlaubt — Schalter "
            "entfernen oder auf true setzen.")

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

    # Nachpruefung 20.09.2026: stand hier auf 90, waehrend cleanup_service,
    # .env.example und Compose mit 60 arbeiten — bei Loeschfristen fuer
    # Personendaten darf es nur EINE Zahl geben.
    for var, default in (("VERTRAG_AUFBEWAHRUNG_TAGE", "60"),
                         ("SNAPSHOT_RETENTION_DAYS", "60"),
                         ("BEWEIS_AUFBEWAHRUNG_TAGE", "30")):
        if _int_env(var, default) <= 0:
            fehler.append(f"{var} muss eine positive Zahl (Tage) sein — "
                          "0 oder negativ wuerde SOFORT loeschen.")

    # Versandweg: Resend (bevorzugt) ODER vollstaendiges SMTP.
    resend_da = bool(os.environ.get("RESEND_API_KEY", "").strip()
                     and (os.environ.get("MAIL_FROM", "").strip()
                          or os.environ.get("SMTP_FROM", "").strip()))
    smtp_fehlt = [v for v in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_FROM")
                  if not os.environ.get(v, "").strip()]
    if resend_da and "@" not in (os.environ.get("MAIL_FROM", "")
                                 or os.environ.get("SMTP_FROM", "")):
        fehler.append("MAIL_FROM enthaelt keine Absenderadresse — erwartet "
                      "z.B. 'AutoSchnell <vertrag@deine-domain.de>'.")
    if smtp_fehlt and not resend_da:
        (fehler if ist_prod else warnungen).append(
            "Kein E-Mail-Versandweg eingerichtet: entweder RESEND_API_KEY + "
            "MAIL_FROM setzen oder SMTP vervollstaendigen (" +
            ", ".join(smtp_fehlt) + ") — sonst koennen Passwort-Reset-Mails "
            "und der Vertragsversand nicht gesendet werden.")

    _mock = os.environ.get("MOCK_PROVIDER_FETCH", "").strip().lower() in ("1", "true", "yes")
    if not os.environ.get("APIFY_TOKEN", "").strip():
        # Entscheidung Ahmad 14.09.2026: die Fahrzeugsuche ist Kernfunktion —
        # ohne Anbieter-Zugang startet Produktion nicht (Ausnahme: Mock im Test).
        (fehler if (ist_prod and not _mock) else warnungen).append(
            "APIFY_TOKEN fehlt — mobile.de/AutoScout24-Abrufe sind nicht moeglich.")
    _lokal = os.environ.get("STORAGE_LOKAL_ERLAUBT", "").strip().lower() in ("1", "true", "yes")
    if ist_prod and not _lokal and not os.environ.get("BACKUP_S3_BUCKET", "").strip():
        # Entscheidung Ahmad 14.09.2026: Offsite-Backup ist Pflicht im Zwei-Server-Betrieb.
        fehler.append("BACKUP_S3_BUCKET fehlt — ohne Offsite-Backup startet Produktion nicht "
                      "(Einzelserver mit STORAGE_LOKAL_ERLAUBT=true ausgenommen).")
    if ist_prod and os.environ.get("BEWEIS_PRIVATDATEN", "").strip().lower() in ("1", "true", "yes"):
        # Pruefung 14.09.2026 (A21): Privatverkaeuferdaten gehoeren nicht in
        # firmenuebergreifend genutzte Beweisdokumente.
        fehler.append("BEWEIS_PRIVATDATEN=true ist in Produktion nicht erlaubt.")
    s3 = {v: os.environ.get(v, "").strip()
          for v in ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY")}
    if any(s3.values()) and not all(s3.values()):
        fehler.append("S3 ist nur teilweise konfiguriert (" +
                      ", ".join(k for k, v in s3.items() if not v) +
                      " fehlt) — Storage wuerde still auf lokale Platte fallen.")
    elif not any(s3.values()) and ist_prod             and os.environ.get("STORAGE_LOKAL_ERLAUBT", "").strip().lower() not in ("1", "true", "yes"):
        # Pruefung 14.09.2026 (Liste 3, Nr. 31): Zwei-Server-Betrieb ohne
        # gemeinsamen S3/R2-Speicher — Dateien von Server A fehlen auf Server B.
        # Ein Einzelserver mit lokaler Platte setzt STORAGE_LOKAL_ERLAUBT=true.
        fehler.append("Kein S3/R2-Speicher konfiguriert (S3_ENDPOINT, S3_BUCKET, "
                      "S3_ACCESS_KEY, S3_SECRET_KEY) — in Produktion mit zwei Servern "
                      "waeren Fotos, Protokolle und Vertraege nur auf einem Server.")
    elif all(s3.values()):
        # Pruefbericht 09/2026: bisher wurde nur geprueft, ob die vier
        # Angaben DA sind — nicht, ob sie funktionieren. Ein vertippter
        # Eimername oder ein Schluessel ohne Schreibrecht waere erst
        # aufgefallen, wenn der erste Nutzer ein Foto hochlaedt. Deshalb
        # einmal beim Start wirklich schreiben, lesen und wieder loeschen.
        art, meldung = _s3_wirklich_pruefen(s3["S3_BUCKET"])
        if art == "fehler":
            (fehler if ist_prod else warnungen).append(meldung)
        elif art == "warnung":
            warnungen.append(meldung)

    # Audit 09/2026 (Punkt 43): fail-closed fuer angebotene Pflichtfunktionen
    # 14.09.2026: Stripe ist entfernt — gesetzte Reste in der .env sind nur ein Hinweis.
    stripe_key = os.environ.get("STRIPE_API_KEY", "").strip()
    stripe_whsec = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if stripe_key or stripe_whsec:
        warnungen.append("STRIPE_* ist gesetzt, wird aber nicht mehr verwendet (Stripe entfernt "
                         "am 14.09.2026) — aus der .env entfernen.")
    if os.environ.get("AUTO_DATEN_SCHAEDEN_FREITEXT", "").strip().lower() in ("1", "true", "yes"):
        warnungen.append("AUTO_DATEN_SCHAEDEN_FREITEXT=true: Freitext-Schaeden koennen "
                         "Personendaten enthalten (Standard: false).")
    if os.environ.get("VERTRAG_LOESCHUNG_AKTIV", "").strip().lower() in ("1", "true", "yes", "ja"):
        warnungen.append("VERTRAG_LOESCHUNG_AKTIV=true: automatische Vertragsloeschung ist scharf.")
    elif ist_prod and not os.environ.get("VERTRAG_LOESCHUNG_AKTIV", "").strip():
        # Runde 17: FEHLT die Variable ganz, ist das kein bewusster
        # Trockenlauf, sondern vergessen — dann startet Produktion nicht.
        # Ein ausdrueckliches false bleibt erlaubt (Warnung unten).
        fehler.append("VERTRAG_LOESCHUNG_AKTIV fehlt in der .env: bewusst 'true' "
                      "(90-Tage-Loeschung scharf) oder 'false' (Trockenlauf) setzen.")
    elif ist_prod:
        # Nachpruefung Runde 14 (Nr. 97): der Trockenlauf ist die dokumentierte
        # Go-Live-Voreinstellung — wird das Scharfschalten aber vergessen,
        # laeuft die versprochene 90-Tage-Loeschung (und die Termin-Frist
        # ohne Vertrag) in Produktion nie. Deshalb laut sagen, nicht abbrechen.
        warnungen.append("VERTRAG_LOESCHUNG_AKTIV fehlt/false: 90-Tage-Loeschung von "
                         "Vertraegen laeuft nur als Vorschau (Trockenlauf) — nach "
                         "Bestandspruefung und Backup auf true setzen (DEPLOYMENT.md).")
    # Seit 10.09.2026 kein Browser mehr (Beweisdokument statt Snapshot).
    try:
        int(os.environ.get("WEB_CONCURRENCY", "4") or 4)
        bp = int(os.environ.get("BEWEIS_PARALLEL", "1") or 1)
        if bp > 4:
            warnungen.append(f"BEWEIS_PARALLEL={bp}: so viele Beweisdokumente je Worker "
                             "gleichzeitig (je 1-2 s Rechenzeit, bis zu 20 Fotos laden) — "
                             "Standard 1 genuegt in der Regel.")
    except ValueError:
        (fehler if ist_prod else warnungen).append("WEB_CONCURRENCY/BEWEIS_PARALLEL muessen Zahlen sein")
    veraltet = [v for v in ("SNAPSHOT_CONCURRENCY", "SNAPSHOT_WORKER_TIMEOUT",
                            "BROWSERLESS_URL", "BROWSERLESS_TOKEN")
                if os.environ.get(v, "").strip()]
    if veraltet:
        warnungen.append(", ".join(veraltet) + " sind seit 10.09.2026 wirkungslos (keine "
                         "Snapshots mehr) — koennen aus der .env entfernt werden.")
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


def produktionspruefung_beim_start(log) -> None:
    """Aufruf aus server.on_start — fail-closed in Produktion.

    Go-Live 13.09.2026 (B4): vorher fing on_start jede andere Exception als
    SystemExit ab, loggte nur eine Warnung und startete WEITER. Geschuetzt
    war Produktion dann nur, weil der Dockerfile-CMD vorher
    `python migrationen.py` (ohne try/except) ausfuehrt. Jetzt bricht der
    Start in Produktion auch ohne diesen Vorlauf ab, wenn die Pruefung selbst
    nicht durchlaeuft; ausserhalb von Produktion bleibt es beim Log.
    (Name bewusst nicht "pruefe_*": test_befunde_runde10 sucht per dir().)
    """
    try:
        pruefe_produktion(log)
    except SystemExit:
        raise
    except Exception as exc:                        # noqa: BLE001
        if os.environ.get("APP_ENV", "").strip().lower() == "production":
            log.error("Produktions-Check konnte nicht laufen: %s — Start "
                      "ABGEBROCHEN (Go-Live 13.09.2026, B4)", exc)
            raise SystemExit(78)  # EX_CONFIG
        log.warning("production check failed to run: %s", exc)
