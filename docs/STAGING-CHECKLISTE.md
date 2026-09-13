# Staging-/Livegang-Checkliste

Reihenfolge einhalten. Punkte mit **[Server]** gehen nur auf der echten
Staging-/Produktionsmaschine — sie sind hier bewusst NICHT abgehakt,
Befehl und Abnahmekriterium stehen jeweils dabei.

## 1. Konfiguration (.env)

- [ ] **[Server]** `.env` aus `.env.example` erstellen und füllen:
  `JWT_SECRET` (openssl rand -hex 32), starkes `ADMIN_PASSWORD`,
  `FRONTEND_URL=https://…`, `CORS_ORIGINS=https://…`
- [x] Das Backend **verweigert den Start** mit Entwicklungswerten:
  `APP_ENV=production` ist im docker-compose gesetzt, `production_check.py`
  prüft JWT_SECRET, Admin-Passwörter, FRONTEND_URL, CORS, Mongo-Auth und
  einen versehentlich aktiven Anbieter-Mock (Exit 78 mit klarer Meldung —
  automatisiert getestet).

## 2. MongoDB — Authentifizierung + private Erreichbarkeit

- [ ] **[Server]** Admin-Nutzer anlegen und Auth erzwingen:
  ```bash
  docker compose exec mongo mongosh --eval '
    use admin;
    db.createUser({user:"autoschnell", pwd:"<STARKES-PASSWORT>",
                   roles:[{role:"readWrite", db:"autoschnell"}]})'
  ```
  danach im Compose beim mongo-Dienst `command: ["--auth"]` aktivieren und
  in `.env` setzen:
  `MONGO_URL=mongodb://autoschnell:<PASSWORT>@mongo:27017/autoschnell?authSource=admin`
- [ ] **[Server]** KEIN öffentlicher Mongo-Port: im docker-compose darf der
  mongo-Dienst **kein** `ports:`-Mapping haben (nur das interne
  Docker-Netz). Abnahme: `nmap <server-ip> -p 27017` von außen → closed.
- [x] Der Produktions-Check bricht ab, wenn `MONGO_URL` keine Zugangsdaten
  enthält.

## 3. Domain + HTTPS

- [ ] **[Server]** DNS: `autoschnell.de` + `www` auf die Server-IP.
- [ ] **[Server]** TLS-Zertifikat (z.B. certbot/Traefik/Caddy) vor
  `deploy/nginx.conf`; HTTP→HTTPS-Umleitung. Abnahme:
  `curl -I http://autoschnell.de` → 301 auf https;
  SSL-Labs-Note mindestens A.

## 4. E-Mail (Passwort vergessen)

- [ ] **[Server]** Resend-/SMTP-Zugang in `.env` (`SMTP_*`), Absender-Domain
  verifizieren (SPF/DKIM). Abnahme: „Passwort vergessen" auf Staging
  auslösen → Mail kommt an, Link zeigt auf `FRONTEND_URL`.

## 5. Browser-Erweiterung (Client-Abruf)

- [ ] Erweiterung aus `browser-extension/` paketieren und an die Sucher
  verteilen (oder Chrome-Web-Store-Eintrag).
- [ ] Erst DANACH `CLIENT_FETCH_KLEINANZEIGEN=true` setzen — vorher holt
  der Server neue Kleinanzeigen-Links selbst (Details:
  docs/kleinanzeigen-abruf.md).

## 6. Anbieterzugänge

- [ ] mobile.de Search-API: Vertrag (Professional-Tarif) abschließen,
  `MOBILE_API_USER/PASS` in `.env`. Ohne Zugang bleiben mobile.de-Links
  gesperrt (klare 400-Meldung — gewollt).
- [ ] Kleinanzeigen: schriftlich klären, ob der geplante Abrufweg
  (Server-Abruf gedrosselt auf 3 gleichzeitige bzw. Client-Abruf über die
  Erweiterung) den Nutzungsbedingungen entspricht — siehe
  docs/kleinanzeigen-abruf.md. **Keine Lasttests gegen echte Anbieter**
  (der Lasttest verweigert den Start ohne Mock — automatisiert geprüft).

## 7. Stripe (kann nach dem Start folgen; manuelle Freischaltung reicht)

- [ ] Live-Keys (`STRIPE_API_KEY`) + Webhook-Endpunkt
  `https://…/api/webhook/stripe` im Stripe-Dashboard anlegen und das
  `whsec_…` als `STRIPE_WEBHOOK_SECRET` setzen (Signatur wird geprüft,
  Fehler → 400, Stripe wiederholt).
- [ ] Testkauf im Stripe-Testmodus: Abo wird nach `checkout.session.completed`
  aktiv; fremde Session-ID liefert 403 (automatisiert getestet).

## 8. Backup + Wiederherstellung

- [x] Nächtliches Backup läuft im Backend (03:00, 14 Tage Rotation,
  Ein-Worker-Sperre) — im Container nach `/backups` (Volume).
- [x] **Wiederherstellung automatisiert bewiesen**:
  `python -X utf8 scripts/wiederherstellung_testen.py`
  (Backup → Restore in separate Testdatenbank → Zähl- und Lesevergleich →
  Aufräumen; letzter Lauf: 30/30 Collections identisch).
- [ ] **[Server]** Denselben Test dort wöchentlich per Cron laufen lassen;
  Abnahme: Exit 0 und „Wiederherstellung bewiesen" im Log. Backups
  zusätzlich **außer Haus** kopieren (z.B. Object Storage).

## 9. Monitoring

- [x] `GET /api/admin/monitoring` (Admin): Ampel + unerwartete Fehler der
  letzten Stunde (error_logs), Job-Rückstau inkl. Alter des ältesten
  wartenden Jobs, aktive Anbieter-Slots, Abrufe heute.
- [ ] **[Server]** Externe Überwachung: alle 1–5 min `/api/health`
  (Erwartung: 200 + `"db":"up"`) und `/api/admin/monitoring`
  (Alarm bei `"ampel":"rot"`); dazu Host-Metriken (CPU/RAM/Disk).

## 10. Lasttest auf der Zielumgebung

- [ ] **[Server]** `MOCK_PROVIDER_FETCH=true python -X utf8
  scripts/lasttest.py --users 500 --duration 300` — Abnahmekriterien in
  docs/lasttests/README.md (Fehlerrate < 1 % nur 503-Rückstau,
  0 Mehrfach-Abrufe, Vergleich p95 < 3 s, Jobwartezeit p95 < 60 s).
  Danach `MOCK_PROVIDER_FETCH` wieder ENTFERNEN (der Produktions-Check
  verweigert sonst den Start).
