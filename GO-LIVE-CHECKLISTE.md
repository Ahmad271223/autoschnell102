# Go-Live-Checkliste — Stand nach dem Audit 09/2026

Legende: **✅ umgesetzt (im Code, mit Test)** · **🔧 vorbereitet, Betreiber muss handeln** · **⏳ offen (Infrastruktur/extern)**

## Blocker

| # | Befund | Stand | Was/Wo |
|---|--------|-------|--------|
| 1 | 81 Backend-Schwachstellen, pip-audit ignoriert | ✅ | Alle 14 Pakete angehoben (`backend/requirements.txt`), `python-jose`/`ecdsa` entfernt (PyJWT), pip-audit blockiert in CI, Ausnahmen nur in `backend/audit-ausnahmen.txt` (befristet), Trivy-Image-Scan im Docker-Job, `apt-get upgrade` im Image. pip-audit: 0 Funde. |
| 2 | Zugangsdaten in der Git-Historie | 🔧 | CI scannt die GANZE Historie (`.gitleaks.toml`, Baseline mit den 4 Alt-Funden). **Rotation ist deine Aufgabe**: DEPLOYMENT.md → „Zugangsdaten rotieren“, danach `scripts/sitzungen_widerrufen.py --yes`. |
| 3 | Datenverlust bei >500 Alt-Verträgen | ✅ | Backfill läuft bis zur Erschöpfung; Löschung nur bei nachweislich vorhandenem Datensatz; `VERTRAG_LOESCHUNG_AKTIV=false` = Dry-Run mit Löschvorschau; `scripts/vertraege_bestand_pruefen.py` vorab. |
| 4 | Restore akzeptiert unvollständige Backups | ✅ | Harter Abbruch, Notfall nur mit `--notfall-unvollstaendig-akzeptieren`; S3 fehlt → Abbruch ohne `--ohne-s3`; Vor-/Nachvalidierung (Zählungen, Prüfsummen, Indizes). |
| 5 | Restore kann gemischten Stand erzeugen | ✅ | Wartungsmodus (503) während des Umschaltens, Staging-Verzeichnisse mit atomarem Tausch, Rollback aller umgeschalteten Collections bei Fehler; Replica-Set-Option dokumentiert. |
| 6 | Stripe-Kauf funktioniert nicht im Image | ✅ | Offizielles Stripe-SDK, Webhook-Signatur Pflicht, Feature-Flag (`/api/payments/config`), UI zeigt Stripe nur wenn aktiv; 9 Tests inkl. echter Signatur. **Echter Testkauf im Stripe-Testmodus auf Staging: 🔧** |
| 7 | Produktionsdeployment nie durchgespielt | 🔧 | CI startet jetzt den echten Compose-Stack (Mongo 8 + Auth, Migration, Proxy, Health/Ready). **Staging-Abnahme mit Bestandsdaten: DEPLOYMENT.md → Checkliste.** |

## Hoch

| # | Befund | Stand | Was/Wo |
|---|--------|-------|--------|
| 8 | Bezahlt ohne Zugang | ✅ | Zustandsautomat `paid → activating → active/activation_failed`, idempotent, Abgleich alle 10 min, Alarm `zahlung_ohne_zugang`. |
| 9 | Host-Header beeinflusst URLs | ✅ | Proxy nur `PUBLIC_HOST` (444 sonst), feste Redirect-Domain, keine URL aus `request.base_url`. |
| 10 | Clickjacking-Schutz Oberfläche | ✅ | Header im Proxy (`deploy/default.conf.template`) und im Web-Image für alle Antworten. |
| 11 | Datei-Löschfehler als Erfolg | ✅ | `loeschen_oder_vormerken` + Retry-Queue mit Referenzen, Schlüssel bleibt bis bestätigter Löschung, Alarm nach 20 Versuchen, sichtbar unter Admin → Betrieb. |
| 12 | Vertragslöschung nicht atomar | ✅ | Tombstone + idempotente Schritte + Reparaturlauf (`vertrag_endgueltig_loeschen`). |
| 13 | Entfernter Fahrer behält Zugriff | ✅ | Jeder Fahrer-Endpunkt prüft die aktive Verknüpfung; Historie in `driver_id_hist`. |
| 14 | Fahrer-Löschung anonymisiert nicht | ✅ | `fahrer_konto_anonymisieren` (Termine, Berichte, Protokolle, Audit-Log). Unterschriften bleiben in finalen Protokollen (Beweiskette) — Fristen: Anwalt 🔧. |
| 15 | Freitext-Schäden mit Personendaten | ✅ | Standard `false` (Compose + Code), Skizzen-Kategorien werden korrekt übernommen, `scripts/schaeden_freitext_bereinigen.py` für Altbestand. |
| 16 | Long-Polling DB-Last | ✅ | Backoff (max. 6 Lesungen), Limit je Konto. |
| 17 | Unbegrenzte Hintergrundaufgaben | ✅ | Sofort-Anstöße begrenzt/dedupliziert (`LINK_JOB_SOFORT_MAX`). |
| 18 | Startmigrationen in 8 Workern | ✅ | `migrationen.py` mit Sperre + Versionierung, vor den Workern; Fehler bricht in Produktion ab. |
| 19 | Ressourcenbedarf | ✅ | 4 Worker × 1 Snapshot, Limits in Compose, `maxPoolSize`; Lasttest-Skript `backend/scripts/lasttest.py` (gegen Staging mit `MOCK_PROVIDER_FETCH=true`) — **Lauf auf Staging 🔧 Betreiber**. |
| 20 | Datenschutz/AGB widersprechen Code | 🔧 | Texte angeglichen (90 Tage, Empfänger, Fristen, USt), Fonts lokal, B2B-Bestätigung. **Platzhalter `[…]` ausfüllen + juristische Prüfung.** |
| 21 | Backups nur lokal | 🔧 | Verschlüsselte Offsite-Kopie mit Object-Lock-Option (`BACKUP_S3_*`), Alter/Vollständigkeit in `/api/ready` und Betrieb; Prüfskript `python scripts/offsite_pruefen.py [--laden]` (Bucket erreichbar, Verschlüsselung, Object Lock, jüngstes Backup, Prüfsumme, Manifest). **Bucket anlegen + Skript monatlich.** |

## Mittel

| # | Befund | Stand |
|---|--------|-------|
| 22 | Unvollständiges Backup zählt als aktuell | ✅ nur vollständige Manifeste zählen |
| 23 | Abo-Sperre Race | ✅ Besitzer-Token, Compare-and-Swap |
| 24 | Abo/Zahlung nicht atomar | ✅ Vorgang mit ID, idempotente Schritte, Reparaturlauf |
| 25 | Zahlungshistorie verändert | ✅ `zugangs_aenderungen` statt Umschreiben |
| 26 | Zahlungsdaten schwach validiert | ✅ Decimal > 0, Datum, Plan-Enum, Kulanz mit Grund |
| 27 | Abo-Status Bestandskunden | ✅ zentrale Auflösung + Migration firmenweit → Chef |
| 28 | Preisangaben widersprüchlich | ✅ UI/AGB einheitlich (150/1.500 netto, 20 € inkl. USt); PR-Text 🔧 |
| 29 | Zugangs-Anfragen ohne Frist | ✅ `ANFRAGEN_AUFBEWAHRUNG_TAGE` |
| 30 | Fehlerberichte unbegrenzt | ✅ Dedup, Obergrenze, Frist auch für offene, Redaktion |
| 31 | Reset-Token vor Passwortänderung verbraucht | ✅ Claim-Zustand mit Rücksetzung |
| 32 | Passwortregeln uneinheitlich | ✅ `passwoerter.py` für alle Rollen (min. 10, max. 72 Byte, Blockliste); Zwei-Faktor (TOTP) für Admin/Super-Admin ✅ — Einstellungen → Zwei-Faktor; Betrieb und `/api/ready` warnen bei Super-Admins ohne 2FA |
| 33 | Job-Status nicht mandantengebunden | ✅ `dealer_ids` |
| 34 | Protokollabschluss verwaist Dateien | ✅ Rollback löscht/vormerkt |
| 35 | Selbstheilung unvollständig | ✅ idempotente Wiederholung |
| 36 | Korrekturversionen Race | ✅ atomarer Claim + Unique-Index |
| 37 | Fahrer-Konfliktsuche | ✅ echte Endzustände |
| 38 | Kundennummer >9999 | ✅ läuft 5-stellig weiter (kein Abbruch), Unique-Index |
| 39 | Aufbewahrung Cache/Marktplatz | ✅ Cache 90 Tage, Interessen 180 Tage, gelöschte Inserate 90 Tage; Datenverzeichnis in Datenschutz §5 |
| 40 | B2B ohne Verifikation | ✅ Pflicht-Bestätigung + USt-IdNr. mit Landesformat-Prüfung bei der Registrierung (`ustid.py`, 29 Länder; Handelsregister-Nr. weiter erlaubt) + Online-Prüfung beim EU-Dienst VIES durch den Admin (Freischaltungen → Prüfen, Ergebnis mit Firmenname/Adresse am Käufer gespeichert) |
| 41 | Verkäuferadresse in Übersicht | ✅ standardmäßig eingeklappt |
| 42 | Readiness nur Mongo | ✅ `/api/ready` |
| 43 | Konfiguration nicht fail-closed | ✅ SELF_SIGNUP prod-Default false, Stripe halb → Abbruch, Prod-Test läuft in CI |
| 44 | Logs mit sensiblen Inhalten | ✅ zentrale Redaktion, Fristen |
| 45 | Datei-URLs als Bearer-Links | ✅ signierte kurzlebige Links, Cache privat |

## Niedrig / Nachweise

| # | Befund | Stand |
|---|--------|-------|
| 46 | Frontend ungetestet | ✅ Playwright-E2E (Rollen, Logins, Freischaltung, Fahrer, Marktplatz, Mobile) im CI-Job `e2e` |
| 47 | Stripe-Erfolgsfall | ✅ Tests mit echter Signatur/Wiederholung; echter Testmodus-Kauf 🔧 |
| 48 | Providerintegration | ✅ Ausfallpfade simuliert (`tests/test_anbieter_fehlerfaelle.py`): Token ungültig, Guthaben leer, Anbieter-Limit, Zeitüberschreitung, Anbieter-5xx, kaputte Antwort, Tagesbudget → klare Nutzertexte (`anbieter_fehler.py`) und Betriebsalarm bei Token/Guthaben (max. 1/Std.); Probelauf mit echten Zugängen auf Staging ⏳ Betreiber |
| 49 | Frontend-Audit wirkungslos | ✅ Gate im richtigen Ordner, blockierend |
| 50 | Buildwarnungen unterdrückt | ✅ `CI=true`, Warnungen bereinigt |
| 51 | Peer-Konflikte | ✅ `react-day-picker` entfernt, Auflösungen gepinnt |
| 52 | Veraltete Mechanismen | ✅ FastAPI-Lifespan; GitHub Actions auf v7 (checkout, setup-python, setup-node, upload-artifact) |
| 53 | Betriebsproben | 🔧 Skript `python scripts/betriebsprobe.py app.<domain> [--dkim-selector …]` prüft DNS, TLS (Ablauf, TLS 1.0/1.1 abgelehnt), HTTP→HTTPS, Host-Allowlist, Security-Header, `/api/health` + `/api/ready`, SPF/DMARC/DKIM, offene Mongo-/Backend-Ports — **vor Go-Live vom Betreiber ausführen**; Uptime-Check/Alarmierung extern einrichten |

## Abo-Verwaltung (zweiter Bericht)

| Befund | Stand |
|--------|-------|
| Funktionen nicht Super-Admin-exklusiv | ✅ Backend (`current_super_admin`) + Oberfläche (nur lesen für Admins), Negativtests |
| Alte Datumswerte laufen nie ab | ✅ naive Werte = UTC, unlesbar = gesperrt, Migration ergänzt Zeitzonen |
| Lifetime nicht sperrbar | ✅ Status vor Plan, Aufheben sperrt auch Lifetime, Lifetime nicht mehr anlegbar |
| Ungültiger Plantyp | ✅ feste Auswahl none/monthly/yearly/trial |
| Freischaltung nicht atomar | ✅ idempotenter Vorgang + Reparaturlauf |
| Chef-Sperre widerruft Sucher-Tokens nicht | ✅ Firmensperre beendet alle Sitzungen |
| Sperre-Begriffe missverständlich | ✅ Bestätigungsdialoge erklären Abo aufheben / Konto sperren / Firma sperren |
| Bezahlt vs. kostenlos unklar | ✅ Texte: Abo = nur Suche & Vergleich |
| Oberfläche aktualisiert nicht | ✅ Polling + „Status aktualisieren“ |
| Abohistorie gelöscht | ✅ alte Zeilen bleiben als „ersetzt“ |
| Zahlungshistorie verändert | ✅ eigene Zugangsänderungen |
| Doppelklick-Race | ✅ Besitzer-Token |
| Zahlungsdaten schwach validiert | ✅ s. 26, Kulanz getrennt mit Pflichtgrund |
| Marktplatz ohne Zahlungshistorie | ✅ Zahlungsdatensatz auch manuell/Stripe |
| Passwortregeln Admin-Anlage | ✅ zentral; Zwei-Faktor Super-Admin ✅ (TOTP, 8 Wiederherstellungscodes, Sperre nach 5 Fehlversuchen, Reset durch Super-Admin) |
| Normaler Admin ungetestet | ✅ `tests/test_super_admin_only.py` |
| Firmenweites Abo anders angezeigt | ✅ zentrale Auflösung + Migration |
| Monat/Jahr ungenau | ✅ „30 Tage / 365 Tage“ |

## Was NUR du erledigen kannst (in dieser Reihenfolge)
1. Zugangsdaten rotieren (alle), Sitzungen widerrufen, im Betriebsprotokoll dokumentieren.
2. `.env` vollständig füllen: `PUBLIC_HOST`, Stripe-Test-/Live-Schlüssel + Webhook-Secret (Dashboard-Endpunkt `/api/webhook/stripe`), SMTP, Offsite-Bucket (`BACKUP_S3_*`).
3. Datenschutz/AGB: Platzhalter `[…]` ausfüllen, USt-Entscheidung bestätigen, juristische Prüfung.
4. Staging-Abnahme nach DEPLOYMENT.md (Update, Rollback, Backup/Restore, Stripe-Testkauf, Lasttest).
5. Erst danach: Merge + Deploy; `VERTRAG_LOESCHUNG_AKTIV` erst nach Backup + Bestandsprüfung scharf schalten.
