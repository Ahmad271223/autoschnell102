# AutoSchnell — Server-Einrichtung (Schritt für Schritt)

Diese Anleitung bringt AutoSchnell auf einen eigenen Linux-Server. Alles
läuft in Docker-Containern; du brauchst keine tiefen Server-Kenntnisse.

## Was du brauchst
- Einen Server (Empfehlung Hetzner, Standort Deutschland). Für 500
  gleichzeitige Vergleiche: **CCX53** (32 Kerne, 128 GB). Zum Starten
  reicht **CPX41** (8 Kerne) — später per Klick vergrößern.
- Deine Domain (z. B. autoschnell.de), DNS auf die Server-IP zeigend.
- Docker + Docker Compose auf dem Server (`apt install docker.io docker-compose-plugin`).

## 1. Projekt auf den Server laden
```bash
git clone <dein-repo> autoschnell && cd autoschnell
```

## 2. Konfiguration setzen
```bash
cp .env.example .env
nano .env          # JWT_SECRET, ADMIN_PASSWORD, SMTP, Domain … eintragen
```
- `JWT_SECRET` erzeugen: `openssl rand -hex 32`
- `WEB_CONCURRENCY` = Anzahl CPU-Kerne des Servers.

## 3. HTTPS-Zertifikat holen (einmalig)
```bash
mkdir -p deploy/certs
# Mit certbot (Let's Encrypt), Domain muss auf den Server zeigen:
docker run --rm -p 80:80 -v $PWD/deploy/certs:/etc/letsencrypt \
  certbot/certbot certonly --standalone -d autoschnell.de -d www.autoschnell.de
# Die erzeugten fullchain.pem / privkey.pem nach deploy/certs kopieren
# (Pfad je nach certbot-Ausgabe).
```

## 4. Starten
```bash
docker compose up -d --build
```
Fertig — die Plattform läuft unter `https://autoschnell.de`.

## 5. Prüfen
```bash
curl https://autoschnell.de/api/health      # {"status":"healthy","db":"up"}
docker compose ps                           # alle Container "healthy"
docker compose logs -f backend              # Live-Log
```

## Updates einspielen
```bash
git pull && docker compose up -d --build    # bei Fehler: git checkout <alt> && ...
```

## Backups
Das Backend sichert **täglich um 03:00** MongoDB + alle Dateien nach
`/backups` (im Volume `backups_data`, 14 Tage Aufbewahrung). Ein Backup
meldet `BACKUP OK` (Exit 0) nur, wenn Datenbank, alle Datei-Speicher
(uploads, local_storage, ggf. S3) **und** — falls konfiguriert — die
Offsite-Kopie gesichert wurden. Sonst `BACKUP UNVOLLSTAENDIG` (Exit 2) mit
Begründung in `manifest.json` → `unvollstaendig` und Betriebsalarm
`backup_unvollstaendig`; Exit 1 (Datenbank nicht gesichert) →
`backup_fehlgeschlagen`. **Nur vollständige Backups zählen** für die
Nachhol-Logik beim Start und für die Readiness-Auskunft
(`backup_service.letztes_backup_info()`).

**Konsistenz:** Läuft Mongo als Replica Set (`--replSet rs0`), liest das
Backup alle Collections in **einer Snapshot-Session** — ein gemeinsamer
Zeitpunkt für die ganze Datenbank (`manifest.konsistenz: "snapshot"`). Die
Standalone-Mongo aus `docker-compose.yml` kann das nicht; dort werden die
Collections nacheinander gelesen (`"best-effort (standalone)"`) — Änderungen
während des Laufs können zwischen zwei Collections liegen. 03:00 ist
deshalb bewusst die verkehrsarme Zeit; wer Punkt-in-Zeit-Konsistenz braucht,
betreibt Mongo als Replica Set.

**Offsite-Kopie (für den Live-Betrieb Pflicht):** Mit `BACKUP_S3_BUCKET`
lädt das Backup nach dem lokalen Abschluss `autoschnell-<zeit>.tar.gz`
(serverseitig AES256-verschlüsselt, SHA-256 als Objekt-Metadatum) hoch und
vermerkt das im Manifest unter `offsite` (`bucket`, `key`, `uploaded_at`,
`bytes`, `sha256`). Schlägt der Upload fehl, ist das Backup UNVOLLSTAENDIG.

| Variable | Bedeutung |
|---|---|
| `BACKUP_S3_BUCKET` | Ziel-Bucket. **Eigener Bucket**, nicht der Datei-Speicher `S3_BUCKET` (Zugangsdaten/Endpoint: `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION`). |
| `BACKUP_S3_PREFIX` | Schlüssel-Präfix, Standard `autoschnell-backups/` |
| `BACKUP_S3_OBJECT_LOCK_DAYS` | `> 0`: Objekt wird mit `ObjectLockMode=COMPLIANCE` für N Tage unlöschbar (Schutz vor Ransomware/Admin-Fehler). Der Bucket muss **beim Anlegen mit Object Lock (Versionierung) erstellt** worden sein, sonst schlägt der Upload fehl. |
| `BACKUP_S3_KEEP` | Offsite-Aufbewahrung in Archiven, Standard 30 (Rotation best effort; gesperrte Objekte bleiben bis zum Ablauf). |

Ohne S3-Offsite das Volume regelmäßig auf einen ANDEREN Ort kopieren
(z. B. Hetzner Storage Box), damit ein Server-Ausfall nicht auch die Backups
mitnimmt:
```bash
# Beispiel: naechtlich per cron auf eine Storage Box spiegeln
docker run --rm -v autoschnell_backups_data:/b -v /mnt/storagebox:/dest \
  alpine sh -c "cp -ru /b/. /dest/"
```

**RPO/RTO:** RPO ≤ 24 h (ein Lauf pro Nacht; wer weniger Verlust
akzeptiert, ruft `scripts/backup_mongo.py` zusätzlich per cron auf — jeder
Lauf ist eigenständig und rotiert mit). RTO = Vorprüfung + Laden + Umschalten
des Restores; bei ~1 GB Daten etwa 10–20 min, währenddessen antwortet die API
mit 503 (Wartungsmodus). Nach dem Restore Backend einmal neu starten.

## Restore
```bash
docker compose exec backend python -X utf8 scripts/restore_mongo.py /backups/autoschnell-<zeit> --dry-run
docker compose exec backend python -X utf8 scripts/restore_mongo.py /backups/autoschnell-<zeit> --yes
```
Grundsatz: Nach dem Restore ist die Datenbank **entweder vollständig alt
oder vollständig auf Backup-Stand**, nie gemischt.
1. Vorprüfung: Prüfsummen aller Dateien, jede `.bson.gz` vollständig gelesen,
   Dokumentzahlen gegen das Manifest. Unvollständige Backups werden
   abgelehnt; Backups mit `s3/`-Objekten brauchen S3-Konfiguration.
2. Laden in `autoschnell__restore_<zeit>` inkl. Indexe.
3. Prüfung **vor** dem Umschalten: Dokumentzahlen und Indexe der temporären
   Datenbank, Datei-Speicher in Staging-Ordner (`uploads.restore-<zeit>`,
   `local_storage.restore-<zeit>`) kopiert und dort erneut per Prüfsumme geprüft.
4. Wartungsmodus setzen (s. u.), ggf. S3-Objekte hochladen.
5. Umschalten: Ordner per Rename (`uploads` → `uploads.vorher-<zeit>`,
   Staging → `uploads`), dann je Collection `renameCollection` (bisheriger
   Stand → `autoschnell__vorher_<zeit>`). Jeder Fehler dreht **alle** bereits
   umgeschalteten Collections und Ordner zurück (`ROLLBACK OK`).
6. Kontrolle: Dokumentzahlen/Indexe der Live-Datenbank erneut gegen das
   Manifest — nur dann `RESTORE OK`; sonst Rollback und Exit 1.

| Flag | Wirkung |
|---|---|
| `--dry-run` | nur prüfen, nichts verändern (meldet auch unvollständige Backups als Fehler) |
| `--yes` | ohne Rückfrage |
| `--db <name>` | Zieldatenbank (Standard `DB_NAME`) |
| `--allow-no-manifest` | alte Backups ohne `manifest.json` (keine Prüfsummen) |
| `--notfall-unvollstaendig-akzeptieren` | ein als UNVOLLSTAENDIG markiertes Backup **trotzdem** einspielen — nur im Notfall; die fehlenden Teile werden laut aufgelistet und fehlen danach |
| `--ohne-s3` | S3-Objekte im Backup bewusst nicht zurückspielen (sonst Abbruch, wenn S3 hier nicht konfiguriert ist) |
| `--nur-datenbank` | Datei-Speicher (uploads, local_storage, S3) unangetastet lassen — für die Restore-Probe in eine Testdatenbank |

**Wartungsmodus:** Vor dem Umschalten schreibt der Restore in der
Zieldatenbank `system_flags` → `{_id: "wartungsmodus", aktiv: true, grund:
"Restore", seit: <iso>}`; die API-Middleware antwortet solange mit **503**.
Nach Erfolg oder Rollback wird `aktiv: false` gesetzt. Nur wenn ein Rollback
selbst scheitert (Zustand gemischt), bleibt er absichtlich aktiv — die
Ausgabe nennt dann den Befehl; manuell aufheben:
```bash
mongosh --eval "db.getSiblingDB('autoschnell').system_flags.updateOne({_id:'wartungsmodus'},{\$set:{aktiv:false}})"
```
`system_flags` selbst wird nie aus dem Backup zurückgespielt.

Nach dem Restore bleiben `autoschnell__vorher_<zeit>` sowie
`uploads.vorher-<zeit>` / `local_storage.vorher-<zeit>` als Rückfalllinie —
nach der Kontrolle löschen. Offsite-Archiv zurückholen: `tar.gz` aus dem
Bucket laden, SHA-256 mit `manifest.offsite.sha256` vergleichen, entpacken
und den Ordner wie oben an `restore_mongo.py` übergeben (Prüfsummen greifen
dort genauso).

### Restore-Probe (monatlich, Ergebnis im Betriebsprotokoll festhalten)
- [ ] `docker compose exec backend python -X utf8 scripts/wiederherstellung_testen.py`
      → `ERGEBNIS: Wiederherstellung bewiesen`, Exit 0. Exit 2 = Datenbank ok,
      aber Backup unvollständig (Ursache aus der Ausgabe beheben); Exit 1 = Abweichung.
- [ ] Jüngstes `manifest.json` prüfen: `unvollstaendig: []`, `offsite` vorhanden,
      `konsistenz` wie erwartet.
- [ ] Ein Offsite-Archiv herunterladen, SHA-256 vergleichen, entpacken,
      `restore_mongo.py <ordner> --dry-run` → `DRY-RUN OK`.
- [ ] Admin → Betrieb / Readiness: keine offenen Alarme `backup_*`, letztes
      Backup < 26 h alt.
- [ ] Einmal jährlich: echter Restore auf Staging inkl. Datei-Speicher und
      gemessene Dauer (RTO) notieren.

## Bestehendes Mongo-Volume auf Authentifizierung umstellen
Läuft bereits eine Mongo **ohne** `--auth` mit Daten im Volume, legt
`MONGO_INITDB_ROOT_*` beim Neustart **keinen** Benutzer mehr an (das
passiert nur bei leerem Datenverzeichnis). Reihenfolge:
```bash
docker compose exec mongo mongosh --eval   "db.getSiblingDB('admin').createUser({user:'<MONGO_USER>',pwd:'<MONGO_PASSWORD>',roles:['root']})"
# .env: MONGO_USER/MONGO_PASSWORD setzen, MONGO_URL mit user:pass@mongo/...?authSource=admin
docker compose up -d --force-recreate mongo backend
docker compose exec backend python -c "from deps import db; import asyncio; print(asyncio.run(db.command('ping')))"
```
Vorher ein Backup ziehen. Erst wenn der Ping mit Zugangsdaten klappt, ist
die Umstellung abgeschlossen. Dieser Ablauf wurde **nicht** in einer
Testumgebung nachgestellt — bitte zuerst auf Staging durchspielen.

## Beim Start geprüft (production_check.py)
Mit `APP_ENV=production` bricht der Start ab bei: Dev-Secret/Demo-Passwort,
`localhost` in FRONTEND_URL/CORS, Mongo ohne Auth, aktivem Mock, nicht
beschreibbaren Backup-/Upload-/Snapshot-Verzeichnissen, fehlendem SMTP,
Aufbewahrungsfristen ≤ 0, halb konfiguriertem S3 sowie bei doppelten Werten
in Feldern mit Eindeutigkeits-Index (`scripts/dubletten_pruefen.py`). Die
Prüfung läuft **vor** Indexanlage und Admin-Seeding.

## Auto-Daten & 90-Tage-Löschung
- Kaufverträge (Verkäufer-Personendaten, PDF, Versionen, Versandstatus)
  werden nach `VERTRAG_AUFBEWAHRUNG_TAGE` (Standard 90) vom stündlichen
  Aufräumjob **vollständig gelöscht**; Terminverweise auf den Vertrag werden
  gekappt.
- Bei jeder Vertragserstellung entsteht zusätzlich ein **anonymer
  Auto-Datensatz** in `admin_vehicle_data` (nur Marke, Modell, EZ, km,
  Kraftstoff, PS, kW, Kaufpreis in Cent, Kaufdatum als Tag, Schäden). Er hat
  keine Verbindung zu Vertrag, Händler oder Personen und bleibt dauerhaft; nur
  der Super-Admin sieht ihn (`/api/admin/vehicle-data` als Liste,
  `/api/admin/vehicle-data/gruppiert` als Baum Marke → Modell → EZ-Jahr →
  Kraftstoff, Menü „Auto-Daten").
- Die Mongo aus `docker-compose.yml` läuft **ohne Replica Set**, daher gibt
  es keine Multi-Dokument-Transaktionen. Der Schreibvorgang ist stattdessen
  idempotent abgesichert (Datensatz → Vertrag → Rollback bei Fehler) und ein
  Reparaturlauf trägt fehlende Datensätze nach. Wer echte Transaktionen will,
  startet Mongo mit `--replSet rs0` und führt einmalig `rs.initiate()` aus.

## Skalieren (mehr Last)
- **Mehr CPU:** Hetzner-Konsole → Server → „Rescale" (2 Min), dann in
  `.env` `WEB_CONCURRENCY` erhöhen und `docker compose up -d`.
- **Abруf-Sperren vermeiden** (viele neue Vergleiche): `PROXY_ENABLED=true`
  + `PROXY_URL=...` setzen. Langfristig ist das client-seitige Abrufen
  (Browser-Erweiterung der Nutzer) geplant — verteilt die Abrufe auf
  hunderte IPs statt einer Server-IP.

## Sicherheits-Checkliste vor dem Live-Gang
- [ ] `JWT_SECRET` auf langen Zufallswert gesetzt
- [ ] `ADMIN_PASSWORD` stark und geändert (nicht der Entwicklungswert)
- [ ] `.env` ist **nicht** im Git (steht in .gitignore)
- [ ] HTTPS-Zertifikat aktiv, HTTP leitet auf HTTPS um
- [ ] Backups werden auf einen zweiten Ort gespiegelt
- [ ] `curl /api/health` liefert „healthy"


## Go-Live-Audit 09/2026 — was sich im Betrieb geändert hat

### Zugangsdaten rotieren (PFLICHT vor dem Live-Gang)
Frühere Commits enthielten Admin-/Super-Admin-Zugangsdaten. Die Historie ist
öffentlich erreichbar; Rotation ist zwingend, unabhängig von einer späteren
Historien-Bereinigung. Reihenfolge:

```bash
# 1. Neue Werte erzeugen (jeweils >= 32 Zeichen Zufall bzw. starke Passwörter)
openssl rand -base64 48        # JWT_SECRET
# 2. In .env eintragen: JWT_SECRET, ADMIN_PASSWORD, SUPER_ADMIN_PASSWORD,
#    MONGO_PASSWORD (+ Mongo-Benutzer ändern: mongosh db.changeUserPassword),
#    SMTP_PASS, STRIPE_*, APIFY_TOKEN, S3_SECRET_KEY, BROWSERLESS_TOKEN
# 3. Stack neu starten (neue Werte greifen; alte JWTs sind durch den neuen
#    JWT_SECRET ungültig)
docker compose up -d --build
# 4. Alle Sitzungen widerrufen (auch Fahrer/Käufer) + Reset-Links löschen
docker compose exec backend python scripts/sitzungen_widerrufen.py --yes
# 5. Nachweis: Datum, wer, welche Werte — im Betriebsprotokoll festhalten
```
CI scannt seit dem Audit die GESAMTE Git-Historie mit gitleaks
(`.gitleaks.toml`, Baseline `.gitleaks-baseline.json` = die vier bekannten
Alt-Funde). Jeder NEUE Fund blockiert den Build.

### Vor den Web-Workern läuft genau eine Migration
`python migrationen.py` (Dockerfile-CMD) legt Indizes und Seeds an und führt
die nummerierten Datenmigrationen (`schema_migrations`) mit Mongo-Sperre
aus; die Worker prüfen beim Start nur noch die Zielversion. In Produktion
bricht ein Migrations-/Indexfehler den Start ab (fail-closed). Stand:
`GET /api/ready` (Feld `schema_version`).

### Liveness und Readiness
- `/api/health` — nur Datenbank-Ping (Container-Healthcheck).
- `/api/ready` — 503 bei Datenbank, Migrationsstand, Speicherplatz
  (`MIN_FREI_MB`) oder nicht schreibbarem Datei-Speicher; Warnungen bei
  Backup älter als 26 h, offenen Betriebsalarmen, hängenden Link-Jobs, S3.
  Für externe Überwachung `/api/ready` verwenden.
- Admin → **Betrieb** (nur Super-Admin): offene Alarme (bezahlt ohne Zugang,
  nicht löschbare Dateien, Vertrag ohne Datensatz, Backup unvollständig),
  Löschwarteschlange, hängende Freischaltungs-Vorgänge, letztes Backup,
  Reparaturlauf per Klick (läuft sonst alle 10 Minuten automatisch).

### Wartungsmodus
`system_flags {_id:"wartungsmodus", aktiv:true}` lässt die API mit 503
antworten (außer /health, /ready). Der Restore setzt und löscht das Flag
selbst; manuell per mongosh:
`db.system_flags.updateOne({_id:"wartungsmodus"},{$set:{aktiv:false}})`.

### Proxy: Host-Allowlist und Sicherheits-Header
`PUBLIC_HOST` (in .env, Pflicht) ist die einzige bediente Domain; andere
Hosts erhalten 444, HTTP leitet fest auf `https://PUBLIC_HOST` um. Der
Proxy setzt HSTS, `X-Frame-Options`, `nosniff`, Referrer-Policy,
Permissions-Policy und `Content-Security-Policy: frame-ancestors 'none'`
für ALLE Antworten (auch die React-Oberfläche). Prüfen nach dem Start:
`curl -sI https://PUBLIC_HOST/ | grep -i -E "strict|frame|content-type-options"`.

### Ressourcen
Standard jetzt 4 Worker × 1 Chromium-Snapshot (vorher 8 × 3 = bis zu 24
Browser ≈ 9,6 GB). `docker-compose.yml` setzt Speicher-/CPU-Limits
(`BACKEND_MEM_LIMIT`, `MONGO_MEM_LIMIT`, …) und begrenzt den Mongo-Pool
(`maxPoolSize=20` in MONGO_URL). Faustregel: Backend-RAM ≈ 400 MB × Worker
+ 400 MB × (Worker × SNAPSHOT_CONCURRENCY) + 500 MB.

### Vertragslöschung (90 Tage) ist standardmäßig NUR Vorschau
`VERTRAG_LOESCHUNG_AKTIV=false`: der stündliche Lauf schreibt eine
Löschvorschau (`system_reports`, typ `vertrag_loeschvorschau`) und löscht
nichts. Vor dem Scharfschalten: `python scripts/vertraege_bestand_pruefen.py`
(muss Exit 0 liefern), externes Backup, dann `VERTRAG_LOESCHUNG_AKTIV=true`.
Gelöscht wird nur, wenn der dauerhafte Auto-Datensatz nachweislich
existiert; sonst Alarm `vertrag_ohne_auto_daten`.

### Dateien
Fahrzeugfotos werden nur noch über kurzlebige signierte Links ausgeliefert
(`DATEI_SIGNATUR_PFLICHT=true`, `DATEI_LINK_TTL_SEKUNDEN`). Firmenlogos
bleiben öffentlich; Protokolle/Unterschriften/Schadenfotos nur über
authentifizierte Endpunkte. Fehlgeschlagene Löschungen landen in
`storage_delete_retry` (Betrieb-Seite), nach 20 Versuchen Alarm.

### Optional: Ein-Knoten-Replica-Set (Transaktionen, konsistente Backups)
```bash
openssl rand -base64 756 > deploy/mongo-keyfile && chmod 400 deploy/mongo-keyfile
# docker-compose.yml: command ["mongod","--auth","--replSet","rs0","--keyFile","/etc/mongo-keyfile"]
#   + Volume ./deploy/mongo-keyfile:/etc/mongo-keyfile:ro
docker compose exec mongo mongosh -u "$MONGO_USER" -p "$MONGO_PASSWORD" --eval "rs.initiate()"
```
Das Backup nutzt dann automatisch Snapshot-Sessions (`konsistenz: snapshot`).

### Staging-Abnahme vor dem Live-Gang (Checkliste)
1. Denselben Stack (`docker compose up -d --build`) auf einem Staging-Server
   mit Kopie der Bestandsdaten starten (Mongo 8 + Auth, PUBLIC_HOST der
   Staging-Domain, echte Zertifikate).
2. `GET /api/ready` = 200, Admin → Betrieb ohne Alarme.
3. Update-Probe: neues Image bauen, `docker compose up -d`, Migration im Log,
   Rollback auf das vorherige Image.
4. Backup + `wiederherstellung_testen.py` + echter Restore auf Staging
   (Wartungsmodus sichtbar, Rollback-Test mit absichtlichem Fehler).
5. Stripe im Testmodus: Checkout, Webhook (Dashboard: `/api/webhook/stripe`),
   Wiederholungs-Webhook, Betrieb-Seite ohne "Zahlung ohne Zugang".
6. Rollen-/Mandantentests und Lasttest (Vergleiche + Snapshots + PDFs gleichzeitig).

## Zwei-Faktor-Anmeldung für Admins (TOTP)

- Jeder Admin/Super-Admin richtet sie selbst ein: **Einstellungen → Zwei-Faktor-Anmeldung → Einrichten**, Geheimnis bzw. `otpauth://`-Link in eine Authenticator-App (Google Authenticator, Aegis, 1Password …) übernehmen, Code eingeben → **8 Wiederherstellungscodes** erscheinen genau einmal — sicher ablegen.
- Danach fragt die Anmeldung nach dem Passwort zusätzlich den 6-stelligen Code (5 Minuten Zeit, 5 Fehlversuche → 15 Minuten Sperre). Ein Wiederherstellungscode gilt je einmal.
- App verloren: ein anderer Super-Admin setzt unter **Nutzer → 2FA zurücksetzen** die Zwei-Faktor-Anmeldung zurück (Sitzung wird beendet).
- `/api/ready` und der Bereich **Betrieb** zeigen, welche Super-Admin-Konten noch ohne Zwei-Faktor sind — vor dem Go-Live alle einrichten.
- Sucher/Fahrer/Zwischenhändler sind nicht betroffen (nur Admin-Rollen).

## Prüfskripte vor dem Go-Live (im Backend-Container bzw. mit Backend-Abhängigkeiten)

```bash
python scripts/betriebsprobe.py app.deine-domain.de --dkim-selector resend   # DNS, TLS, Header, Health, SPF/DMARC/DKIM, Ports
python scripts/offsite_pruefen.py --laden                                     # Offsite-Backup: Bucket, Object Lock, jüngstes Backup laden + prüfen
python scripts/lasttest.py --users 100 --duration 120                         # nur gegen Staging mit MOCK_PROVIDER_FETCH=true
```

## Stimmige Datensicherung ohne Replica Set

MongoDB läuft in der Standard-Zusammenstellung ohne Replica Set. Dann liest die Sicherung eine Collection nach der anderen: laufende Buchungen oder Terminänderungen können dazwischenliegen, die Dateien passen also nicht auf die Sekunde zusammen. Zwei Wege:

1. **Replica Set einrichten** (empfohlen): `mongod --replSet rs0` plus einmalig `rs.initiate()`. Die Sicherung nutzt dann automatisch einen Snapshot; das Manifest meldet `"konsistenz": "snapshot"`.
2. **Schreibpause**: den nächtlichen Lauf mit `--wartung` starten. Für die Dauer der Sicherung antwortet die API auf schreibende Aufrufe mit 503 (Wartungsmodus), danach wird er automatisch wieder abgeschaltet.

```bash
python scripts/backup_mongo.py --wartung
```
