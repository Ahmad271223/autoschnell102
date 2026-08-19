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
Wiederherstellen: `backend/scripts/restore_mongo.py <backup-ordner>`.

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
