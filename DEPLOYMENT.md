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
`/backups` (im Volume `backups_data`, 14 Tage Aufbewahrung). **Wichtig:**
dieses Volume regelmäßig auf einen ANDEREN Ort kopieren (z. B. Hetzner
Storage Box), damit ein Server-Ausfall nicht auch die Backups mitnimmt:
```bash
# Beispiel: naechtlich per cron auf eine Storage Box spiegeln
docker run --rm -v autoschnell_backups_data:/b -v /mnt/storagebox:/dest \
  alpine sh -c "cp -ru /b/. /dest/"
```
Wiederherstellen: `backend/scripts/restore_mongo.py <backup-ordner>` — prüft
zuerst alle Prüfsummen (manifest.json) und liest jede Datei vollständig,
lädt dann in eine temporäre Datenbank und schaltet je Collection atomar um;
der bisherige Stand bleibt als `autoschnell__vorher_<zeit>` erhalten.
`--dry-run` prüft nur. Ein Backup meldet `BACKUP OK` nur, wenn Datenbank
**und** alle Datei-Speicher (uploads, local_storage, ggf. S3) gesichert
wurden — sonst `BACKUP UNVOLLSTAENDIG` (Exit-Code 2) mit Begründung im Log.

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
