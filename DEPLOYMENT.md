# AutoSchnell — Server-Einrichtung (Schritt für Schritt)

Diese Anleitung bringt AutoSchnell auf einen eigenen Linux-Server. Alles
läuft in Docker-Containern; du brauchst keine tiefen Server-Kenntnisse.

## Was du brauchst
- Einen Server (Empfehlung Hetzner, Standort Deutschland). Für 500
  gleichzeitige Vergleiche: **CCX53** (32 Kerne, 128 GB). Zum Starten
  reicht **CPX41** (8 Kerne) — später per Klick vergrößern.
- Deine Domain (z. B. autoschnell.de), DNS auf die Server-IP zeigend.
- Docker + Docker Compose auf dem Server — **aus der offiziellen Docker-Quelle**
  (`docker-ce` + `docker-compose-plugin`, genau so in Schritt 4 unten). Das
  Ubuntu-Paket `docker.io` hat kein `docker-compose-plugin`, der Befehl scheitert.

## 1. Projekt auf den Server laden
```bash
# Der Live-Stand liegt im Zweig feature/plattform-ausbau-2026-08 (nicht main).
git clone -b feature/plattform-ausbau-2026-08 <dein-repo> autoschnell && cd autoschnell
```

## 2. Konfiguration setzen
```bash
cp .env.example .env
nano .env          # JWT_SECRET, SUPER_ADMIN_USERNAME, SUPER_ADMIN_PASSWORD, SMTP, Domain … eintragen
```
- `JWT_SECRET` erzeugen: `openssl rand -hex 32`
- `WEB_CONCURRENCY` = Zahl der Web-Worker, **Standard 4** (CCX23: 4 Kerne). Mehr nur nach
  der Faustregel `(BACKEND_MEM_LIMIT − 500 MB) / 400 MB` (bei 4 GB also höchstens 8) — und
  dann `RESEND_PROZESSE` und `APIFY_MAX_PARALLEL` **mitziehen** (Abschnitte „Lasttest
  ‚30 gleichzeitige Verträge und Mails‘“ — Takt `RESEND_RATE ÷ RESEND_PROZESSE` — und
  „Apify-Grenze“), sonst gehen Mails und Abrufe verloren. Prüfbericht 20.09.2026, DO-17:
  „= Anzahl CPU-Kerne“ hätte auf einem CCX53 32 Worker ≈ 13 GB ergeben, weit über dem Limit.

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

**Hinter dem Load Balancer (prod1 + prod2, seit 09/2026): immer EIN Server
nach dem anderen mit dem Rollout-Skript.** Es setzt zuerst einen Drain-Marker
(`/api/health` antwortet 503, der Load Balancer nimmt den Server aus der
Rotation), baut dann neu und meldet den Server erst zurueck, wenn Backend
UND Oberflaeche antworten. Ohne dieses Vorgehen bekamen Besucher waehrend
des Neubaus des Oberflaechen-Containers rund 45 Sekunden lang 502 (Vorfall
07.09.2026, 15:04 UTC): der Load Balancer prueft nur das Backend, das die
ganze Zeit gesund war.

```bash
# 1) prod2 (erster Server — die Abschlussprobe wertet das neue Bundle hier nur als Zwischenstand):
cd /opt/autoschnell && ERSTER_SERVER=1 sh deploy/rollout.sh
# 2) erst nach "FERTIG" auf prod2, dann prod1:
cd /opt/autoschnell && sh deploy/rollout.sh
```

Ohne `ERSTER_SERVER=1` endet die Abschlussprobe auf prod2 mit Code 3 (das
neue Oberflaechen-Skript fehlt auf prod1 noch — 404).

**Migration als eigener Schritt (Pruefbericht 20.09.2026, AL-05; seit 22.09.2026):**
Schritt 3 baut zuerst die Images, faehrt dann die Migration sichtbar im
Vordergrund (`docker compose run -T --rm --no-deps backend python migrationen.py`
— Einmal-Container mit dem neuen Image gegen die laufende Datenbank; das alte
Backend bedient derweil weiter) und wechselt erst danach die Container. Eine
lange Datenmigration oder ein Indexaufbau laeuft also, BEVOR ein Container
ausgetauscht wird; der neue Container findet beim Start alles erledigt vor und
ist in Sekunden bereit (sein Healthcheck hat trotzdem 120 s Anlaufzeit).
Scheitert die Migration, endet das Rollout mit Code 1 im Drain, OHNE die
Container zu wechseln — das alte Backend laeuft weiter; die Datenbank ist dann
so weit migriert, wie der Lauf kam (jede Migration ist idempotent: Ursache
beheben, dann erneut `sh deploy/rollout.sh`). Schritt 4 wartet weiter hoechstens
rund drei Minuten auf `/api/ready` — haelt aber ein lebender Prozess die
Migrationssperre (`job_locks` "migration", z. B. weil der andere Server gerade
migriert), wartet es weiter, bis zu `WARTE_MIGRATION` Sekunden (Standard
14400 = 4 h, wie `migrationen.py` selbst). Sperrzustand von Hand:
`docker compose exec -T backend python migrationen.py --sperre-gehalten`
(Code 0 = gehalten, 1 = frei, 2 = Datenbank nicht erreichbar). Auf dem zweiten
Server ist die Migration dann schon durch, der Schritt dauert dort Sekunden.
Fuer den Bediener aendert sich nichts: derselbe Aufruf, dieselbe Reihenfolge
(prod2 mit `ERSTER_SERVER=1`, dann prod1). (Stand 22.09.2026: alle Migrationen
sind durch.)

Dauer je Server rund drei Minuten (zweimal 60 s Wartezeit fuer den Load
Balancer) plus die Dauer einer etwaigen Migration. Waehrenddessen traegt der
andere Server die Last allein.

Zwei Sicherungen stecken dahinter: Der Drain-Marker liegt auf dem Host
(`deploy/drain/aktiv`, per Volume im Proxy sichtbar) und ueberlebt damit
auch den Neustart des Proxy-Containers, den `up -d --build` ausloesen
kann. Und `/api/health` meldet nur dann "gesund", wenn Backend UND
Oberflaeche antworten (Unteranfrage an den web-Container) — ein Server
mit gerade neu gebauter Oberflaeche faellt so auch ohne Drain aus der
Rotation. Beides gilt nur fuer die LB-Vorlage; nach dem ersten `git pull`
mit dieser Aenderung einmal `docker compose up -d --force-recreate --no-deps proxy`,
damit Volume und Vorlage geladen sind (ein bis zwei Sekunden Unterbrechung).
**Wenn das Rollout abbricht** (Build, Bereitschaft oder Oberflaeche
scheitern), bleibt der Server absichtlich im Drain: `/api/health` antwortet
weiter 503, der Load Balancer schickt keine Besucher hin, der andere Server
traegt die Last allein. Der Marker wird NICHT automatisch entfernt — ein halb
fertiger Server (Backend antwortet, aber `/api/ready` scheitert an Datenbank,
Migration oder R2) darf nicht zurueck in die Rotation. Vorgehen:

1. Ursache ansehen: `docker compose ps` und
   `docker compose logs --tail 80 backend web proxy`.
2. Entweder beheben und `sh deploy/rollout.sh` erneut ausfuehren, oder
   **Rollback** auf den vorherigen Stand:
   ```bash
   cd /opt/autoschnell && git log --oneline -3      # vorherigen Commit ablesen
   # Runde 31: erst die Bundle-Dateien des laufenden Standes aufheben — wer ihn
   # schon geladen hat, braucht sie weiter:
   docker cp "$(docker compose ps -q web):/usr/share/nginx/html/static/." deploy/assets-alt/static/
   git reset --hard <vorheriger Commit>              # nur versionierte Dateien; .env, Keyfile, Zertifikate bleiben
   export APP_FASSUNG=$(git log -1 --format=%ct-%h) # Fassungs-Stempel, sonst kein Versionshinweis
   docker compose up -d --build
   ```
3. Erst danach freigeben: `sh deploy/freigeben.sh`. Das Skript prueft
   `/api/ready` und die Startseite ueber den Proxy und entfernt nur bei
   Erfolg den Drain-Marker (`--erzwingen` ueberspringt die Pruefung —
   nur bewusst einsetzen).

Nach einem Rollback per `git reset --hard` holt das naechste
`sh deploy/rollout.sh` mit `git pull --ff-only` wieder den neuesten Stand.

**Zwischen den beiden Servern** (nach prod2, vor prod1) laufen zwei Staende
gleichzeitig. Startseite und nachgeladene Seitenteile koennen ueber den Load
Balancer von verschiedenen Servern kommen. Vorfall 12.09.2026: prod2 lief ab
12:20:35 UTC neu, prod1 erst ab 12:33:58 UTC — 13 Minuten lang bekam jeder,
der einen Teil vom falschen Server holte, 404. Die Oberflaeche lieferte diese
404 mit `public, max-age=31536000, immutable` aus; Chromium hielt sie fest und
fragte den Server nie wieder. Fahrer-App und Super-Admin kamen danach bei
JEDER Anmeldung nicht weiter, obwohl beide Server laengst sauber waren
(Abhilfe auf den betroffenen Geraeten: Websitedaten von
`app.auto-schnellkauf.de` loeschen; einmal Cloudflare → Purge Everything).

Seit Runde 31:
- Fehlt dem Proxy eine Datei unter `/static/`, fragt er erst beide Server im
  privaten Netz (`hinter-loadbalancer.conf.template`, Port 8081 — nur auf
  `PRIVATE_IP` veroeffentlicht, nur fuer `PROD1_IP`/`PROD2_IP` freigegeben,
  liefert nur `/static/`, fragt selbst nie weiter). Der neue Server kennt das
  neue Bundle, beide ueber `deploy/assets-alt` die vorherigen. Erst wenn keiner
  sie hat: 404 mit `no-store`.
- Auch die Oberflaeche selbst liefert fehlende Dateien nur noch mit
  `no-store` (`frontend/Dockerfile`, `@fehlt`).
- Scheitert trotzdem ein Seitenteil, erneuert die Oberflaeche den
  Browser-Zwischenspeicher fuer genau diese Datei (`fetch(..., {cache: "reload"})`)
  und laedt einmal neu — nie, solange ungespeicherte Eingaben offen sind.
- Die Abschlusspruefung des Rollouts (`scripts/betriebsprobe.py`) holt auch
  ALLE nachladbaren Seitenteile, je dreimal am Cloudflare-Cache vorbei —
  vorher prueften sie nur die Startdateien und meldeten am 12.09. "0 Fehler".

**Beim ERSTEN Rollout mit dieser Aenderung** kennt der noch alte Server Port
8081 nicht. Wer auch dieses eine Fenster schliessen will, erzeugt auf dem
zweiten Server VOR dem Rollout des ersten nur den Proxy neu (eine Sekunde
Unterbrechung):
```bash
cd /opt/autoschnell && git pull --ff-only
COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.replica.yml docker compose up -d --force-recreate --no-deps proxy
```
Pruefen, von einem Server zum anderen (Antwort `200`; jede andere Adresse bekommt `403`):
`docker compose exec -T proxy wget -S -O /dev/null http://<PRIVATE_IP des anderen>:8081/static/js/<Datei aus der index.html>`

**Versionshinweis in der Oberflaeche (Runde 31):** `deploy/rollout.sh` setzt
`APP_FASSUNG=<Commit-Zeit>-<Kurz-SHA>` — aus dem Commit, damit beide Server
denselben Wert haben. Das Backend schickt ihn in jeder API-Antwort als
`X-AH-Fassung`, die Oberflaeche kennt ihren eigenen aus dem Bau. Ist der
Server-Stempel neuer, erscheint „Neue Version verfügbar“; beim naechsten
Seitenwechsel und direkt nach der Anmeldung laedt die Oberflaeche still die
neue Fassung — nie, solange Unterschriften, ein offener Kaufvertrag, ein
Abhol-Check oder Wiederherstellungscodes ungespeichert sind (dann fragt der
Browser vor dem Verlassen nach). Wer von Hand baut, setzt den Wert selbst
(`export APP_FASSUNG=$(git log -1 --format=%ct-%h)`); ohne ihn gibt es keinen
Hinweis, sonst aendert sich nichts.

**Einzelserver ohne Load Balancer** (Entwicklung, Staging):
```bash
git pull && docker compose up -d --build    # bei Fehler: git checkout <alt> && ...
```

**Replikat-Betrieb (seit 09/2026, beide Server):** `docker compose` ohne die
Ergaenzungsdatei baut den Mongo-Container ohne Mitgliedsnamen und das
Backend ohne die Namen mongo-prod1/mongo-prod2 — Folge: "Temporary failure
in name resolution", Mitglied faellt aus dem Replikat. Deshalb in der `.env`
einmalig setzen, dann gilt es fuer jeden Aufruf automatisch:

```
COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.replica.yml
```

Ohne diese Zeile immer ausdruecklich
`docker compose -f docker-compose.yml -f deploy/docker-compose.replica.yml up -d --build`.

## Go-Live: Anmeldung mit Kontonummer und Live-Reset (Runbook)

Einmaliger Ablauf beim Live-Gang der Anmeldung mit Kontonummer (Entscheidung
13.09.2026): Die Testdaten werden gelöscht, nur der Super-Admin bleibt; danach
legt er die echten Konten an. Alle `docker compose`-Befehle laufen mit BEIDEN
Compose-Dateien (Vorfall 07.09.2026): `COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.replica.yml`
steht in der `.env` — sonst jeden Aufruf ausdrücklich mit
`-f docker-compose.yml -f deploy/docker-compose.replica.yml`.

1. **Voraussetzungen:** Der Hotfix, der die Selbstregistrierung von Käufern
   und Fahrern in Produktion schließt, ist längst live. Alle Schritte der
   Umstellung sind gemergt, die CI ist grün, der Wortlaut von AGB und
   Datenschutzerklärung ist freigegeben. Das Wartungsfenster ist angekündigt,
   mit dem Hinweis „nach der Umstellung die Seite einmal neu laden“.
2. **Backup** (prod1, Backend läuft noch):
   ```bash
   docker compose exec backend python scripts/backup_mongo.py
   docker compose exec backend python scripts/offsite_pruefen.py --laden
   docker compose exec backend ls /backups          # Ordner autoschnell-<zeit> notieren
   git -C /opt/autoschnell rev-parse HEAD           # auf BEIDEN Servern: Commit <alt> notieren
   ```
   Dieses Backup ist nach dem Reset der EINZIGE Weg zurück. Es liegt im
   Volume `backups_data` von **prod1** — ein Restore läuft deshalb dort
   (Punkt 12).
3. **Nur das Backend stoppen, auf BEIDEN Servern:**
   ```bash
   cd /opt/autoschnell && docker compose stop backend
   ```
   mongo, web und proxy laufen weiter. Die Gesundheitsprüfung des Load
   Balancers schlägt fehl, Besucher sehen die Wartung. `unless-stopped` startet
   einen gestoppten Container auch nach einem Neustart des Docker-Dienstes
   nicht wieder. Beweis-, Aufräum- und Link-Jobs laufen im Backend-Prozess —
   danach schreibt niemand mehr in die Datenbank.
4. **prod2: neuen Stand holen und bauen, noch NICHT starten:**
   ```bash
   cd /opt/autoschnell && git pull --ff-only
   docker compose build backend web
   ```
   Nötig, weil `scripts/live_reset.py` erst im neuen Image liegt;
   `deploy/rollout.sh` baut das Image sonst erst mitten im eigenen Ablauf.
5. **prod2: Probelauf** (ändert nichts):
   ```bash
   docker compose run --rm --no-deps backend python scripts/live_reset.py --db autoschnell
   ```
   Ahmad prüft die Tabelle je Sammlung („wird gelöscht / bleibt“), die
   Dateizahlen je Präfix und die Warnung zu echten Zahlungen. Eine Sammlung
   „UNBEKANNT“ blockiert das Ausführen — erst klären.
6. **prod2: Ausführen:**
   ```bash
   docker compose run --rm --no-deps backend python scripts/live_reset.py --db autoschnell \
       --ausfuehren --bestaetige autoschnell --nummern-ab 10001
   ```
   Das Skript fragt zusätzlich `LOESCHEN` ab. `--nummern-ab` nur, wenn die
   Nummern ab einem festen Wert beginnen sollen (hebt den Zähler nur an).
   Bricht der Lauf ab, setzt ein erneuter Aufruf fort. **Exit 5** heißt:
   Datenbank erledigt, aber einzelne Dateien nicht gelöscht. Die Ziele stehen
   auf der Konsole und in `system_flags.live_reset.datei_fehler`; ein erneuter
   Aufruf versucht genau diese Ziele noch einmal (der vorige Lauf bleibt unter
   `vorige_laeufe`, sein `system.live_reset`-Eintrag bleibt).
7. **prod2: Rollout:**
   ```bash
   ERSTER_SERVER=1 sh deploy/rollout.sh
   ```
   Beim Start entfernt `ensure_indexes` die Eindeutigkeit der E-Mail und legt
   `kontonummer_eindeutig` an. Ab „FERTIG“ trägt prod2 allein.
8. **prod1 bleibt gestoppt bis zu seinem eigenen Rollout.** Er darf NICHT mit
   dem alten Stand starten (kein `docker compose start backend`): der alte Code
   legt `email_1` wieder eindeutig an, scheitert an den Konten ohne E-Mail und
   bricht als Leader mit Exit 78 ab. Stattdessen direkt:
   ```bash
   cd /opt/autoschnell && sh deploy/rollout.sh
   ```
   Die Warnung „kein laufender Oberflaechen-Container“ kommt dabei nicht, weil
   web die ganze Zeit lief.
9. **Cloudflare → Purge Everything.**
10. **Super-Admin anmelden** (Benutzername, Passwort, Zwei-Faktor) und die
    echten Konten anlegen: Firma mit Chef, Sucher, Zwischenhändler, Fahrer.
    Kontonummer und Passwort vergibt der Betreiber und teilt sie den Kunden
    auf sicherem Weg mit. Hinweis an alle mit installierter App: **Seite einmal
    neu laden** — eine noch zwischengespeicherte alte Anmeldemaske (E-Mail-Feld)
    kann keine Nummer senden.
11. **Erst jetzt** `ADMIN_EMAIL`, `ADMIN_PASSWORD` und `SELF_SIGNUP` aus der
    `.env` BEIDER Server entfernen (kein Neustart nötig). Die Server-.env erst
    nach beiden Rollouts bereinigen: solange ein alter Stand noch hätte
    starten können, verlangte dessen Produktionsprüfung `ADMIN_PASSWORD`.
12. **Zurück auf den alten Stand** (`<alt>` = der in Punkt 2 notierte Commit,
    `<zeit>` = der dort notierte Backup-Ordner). NICHT `sh deploy/rollout.sh`:
    es holt mit `git pull --ff-only` wieder den neuen Stand. Stattdessen von
    Hand wie unter „Rollback“:
    - **bis einschließlich Punkt 5** (noch nichts gelöscht): prod2 steht seit
      Punkt 4 schon auf dem neuen Commit, das neue Image ist gebaut. Das
      nächste `docker compose up -d` oder Rollout spielte den neuen Code über
      die alten Daten ein — die Konten dort haben keine Kontonummer, niemand
      könnte sich anmelden, und `ensure_indexes` entfernte die Eindeutigkeit
      der E-Mail. Deshalb erst prod2 zurücksetzen und die alten Images bauen:
      ```bash
      # prod2
      cd /opt/autoschnell && git reset --hard <alt>
      export APP_FASSUNG=$(git log -1 --format=%ct-%h)
      docker compose build backend web
      # danach auf BEIDEN Servern (prod1 ist unverändert)
      cd /opt/autoschnell && docker compose start backend
      ```
    - **ab Punkt 6** (Daten gelöscht): nur per Restore des Backups aus Punkt 2.
      Reihenfolge: Backend überall stoppen → Restore auf prod1 → alter Commit
      und alte Images auf beiden Servern → Start. Konten ohne E-Mail lassen den
      alten Code nicht starten, deshalb nie vor dem Restore starten. Das
      Backend ist gestoppt, `docker compose exec` geht nicht — der Restore
      läuft als Einmal-Container auf **prod1** (dort liegt das Backup):
      ```bash
      # BEIDE Server (prod2 läuft nach Punkt 7 schon mit dem neuen Code)
      cd /opt/autoschnell && docker compose stop backend
      # prod1: Restore
      docker compose run --rm --no-deps backend python -X utf8 scripts/restore_mongo.py /backups/autoschnell-<zeit> --dry-run
      docker compose run --rm --no-deps backend python -X utf8 scripts/restore_mongo.py /backups/autoschnell-<zeit> --yes
      # erst nach "RESTORE OK": BEIDE Server, zuerst prod1, dann prod2
      git reset --hard <alt>
      export APP_FASSUNG=$(git log -1 --format=%ct-%h)
      docker compose up -d --build
      sh deploy/freigeben.sh
      ```
      Danach Cloudflare → Purge Everything. `freigeben.sh` entfernt einen
      Drain-Marker aus einem abgebrochenen Rollout erst nach erfolgreicher
      Prüfung.
13. **Sicherungen:** Die lokalen Backups (14 Tage) und die Offsite-Kopien
    enthalten die Testdaten bis zur Rotation. Wer sie früher loswerden will,
    löscht sie von Hand.

## Backups
Das Backend sichert **täglich um 03:00** MongoDB nach `/backups` (im Volume
`backups_data`, 14 Tage Aufbewahrung) und den Datei-Speicher **Speicher-zu-
Speicher in den Sicherungs-Bucket** (seit 19.09.2026, s. u.). Ein Backup
meldet `BACKUP OK` (Exit 0) nur, wenn Datenbank, alle Datei-Speicher
(uploads, local_storage, ggf. S3) **und** — falls konfiguriert — die
Offsite-Kopie gesichert wurden.

**Zwei Server (Prüfbericht 20.09.2026, DP-10):** Die Nachtsicherung läuft auf
**einem** der beiden Server — dem, der die Tagessperre `backup-<tag>` in
`job_locks` gewinnt (prod1 ODER prod2); der Dump liegt dann nur auf dessen
Platte in `backups_data`. Welcher das war, steht in `/api/ready` →
`backup.server` (im Container: `docker compose exec -T backend curl -s
http://localhost:8001/api/ready`, Quelle „Sicherung lief auf …“) und auf der
Betriebsseite. Die Offsite-Kopie im Sicherungs-Bucket ist serverunabhängig —
für einen Restore auf dem anderen Server von dort holen.
`BACKUP_AKTIV=false` (Standard `true`, Prüfbericht AL-20) nimmt einen Server
aus der Nachtsicherung heraus — dann läuft sie nur noch auf dem anderen.

**Datei-Speicher seit 19.09.2026 (Entscheidung Ahmad): kein Spiegel mehr auf
der Platte.** Vorher lud jeder nächtliche Lauf den *ganzen* S3-Bucket auf die
Serverplatte (14 Stände + gepacktes Archiv = 15 × Bucket-Größe). Bei den
erwarteten ~20 GB Dateien (36 Nutzer × 150 Inserate am Tag) wären das 300 GB
auf einer 160-GB-Platte. Jetzt (`BACKUP_DATEIEN=bucket`, Standard sobald
`BACKUP_S3_BUCKET` gesetzt ist):

- Jede Datei wird **einmal** in den Sicherungs-Bucket kopiert (Präfix
  `dateien/`), danach nur noch neue oder geänderte — die Daten fließen durch
  den Arbeitsspeicher, nie auf die Platte. Gelesen wird mit den `S3_*`-,
  geschrieben mit den `BACKUP_S3_*`-Zugangsdaten (ein reiner Schreib-Schlüssel
  für die Sicherung reicht).
- Im Datei-Speicher gelöschte Dateien bleiben im Sicherungs-Bucket
  **30 Tage als Papierkorb** (Metadatum `geloescht-am`), dann verschwinden sie
  auch dort (`BACKUP_DATEIEN_AUFBEWAHRUNG_TAGE`).
- Das Manifest trägt `dateien_kopie` (Bucket, Präfix, kopiert/unverändert/
  Papierkorb/entfernt). Scheitert eine Datei, ist das Backup UNVOLLSTAENDIG.
- Zurückholen: `python -X utf8 scripts/dateien_zurueckkopieren.py --dry-run`
  zählt, `--yes` kopiert fehlende Dateien zurück in den Datei-Speicher
  (`--praefix protocol/` nur einen Ordner, `--auch-geloeschte` auch aus dem
  Papierkorb). Der DB-Restore (unten) bleibt unverändert.
- **Zusätzlich empfohlen:** im Cloudflare-Dashboard beim Datei-Bucket
  (`S3_BUCKET`) die **Versionierung** einschalten — dann lässt sich auch ohne
  Sicherung jede versehentlich gelöschte Datei sofort zurückholen.
- **Plattenbedarf lokal:** ≈ 15 × Größe eines DB-Dumps (14 Stände + Arbeitsordner;
  Messwert: `docker compose exec backend sh -c 'du -sh /backups/autoschnell-*'`).
  Im Spiegel-Modus kommen 15 × Datei-Speicher dazu — ohne `BACKUP_S3_BUCKET`
  deshalb `BACKUP_DATEIEN=aus` setzen oder einen Sicherungs-Bucket einrichten,
  sonst läuft die Platte voll (siehe „Speicher voll“).
- `BACKUP_DATEIEN=spiegel` schaltet den alten Weg wieder ein (nur für kleine
  Installationen), `aus` sichert keine Dateien. Sonst `BACKUP UNVOLLSTAENDIG` (Exit 2) mit
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
| `BACKUP_S3_KEEP` | Offsite-Aufbewahrung in Archiven, Standard 14 (Rotation best effort; gesperrte Objekte bleiben bis zum Ablauf). |
| `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY`, `BACKUP_S3_REGION` | Eigene Zugangsdaten NUR für den Sicherungs-Bucket (Phase 3, 15.09.2026). **Berechtigung "Object Read & Write", eingeschränkt auf genau diesen Bucket** — rein schreibend genügt nicht: der Upload prüft die Größe (`head_object`), die Rotation listet und löscht alte Archive, die Dateikopie führt einen Papierkorb (Korrektur 20.09.2026). Leer = die `S3_*`-Zugangsdaten (dann meldet die Bereitschaftsprüfung einen Hinweis). |

Ohne S3-Offsite das Volume regelmäßig auf einen ANDEREN Ort kopieren
(z. B. Hetzner Storage Box), damit ein Server-Ausfall nicht auch die Backups
mitnimmt:
```bash
# Beispiel: naechtlich per cron auf eine Storage Box spiegeln. Pruefbericht
# 20.09.2026 (DO-14): nur die juengsten 14 Staende bleiben — die Datenschutz-
# erklaerung sagt "ausser Haus die letzten 14 Sicherungen" (Runde 21 loeschte
# nach 30 Tagen, davor loeschte "cp -ru" nie).
docker run --rm -v autoschnell_backups_data:/b -v /mnt/storagebox:/dest \
  alpine sh -c "cp -ru /b/. /dest/ && cd /dest && ls -1d autoschnell-* | sort -r | tail -n +15 | xargs -r rm -rf"
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

> **Korrektur 20.09.2026 (Nr. 75):** Dieser Satz stimmte für den obigen
> Befehl bisher nicht. Collections, die es nur live gibt — weil sie nach dem
> Backup entstanden sind — blieben unverändert stehen, neben dem alten Stand
> aus dem Backup. Weggeräumt hat sie nur das nirgends dokumentierte
> `--exakt`. Das ist jetzt das **Standardverhalten**: solche Collections
> wandern in die Vorher-Datenbank `<db>__vorher_<zeit>` (gelöscht wird
> nichts, sie bleibt 30 Tage). Wer den gemischten Stand wirklich braucht,
> hängt `--zusaetzliche-behalten` an und bekommt eine laute Warnung.
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

> **Seit 22.09.2026 (Rollenprüfung RP-544/545/245):**
> - **TTL-Indexe** (Ablaufzeiten, z. B. `vehicle_cache`, `rate_limits`,
>   `betriebsalarme`) werden in Schritt 2 zunächst **ohne** Ablaufzeit angelegt
>   und erst nach der Kontrolle in Schritt 6 scharf geschaltet (`collMod`).
>   Vorher löschte die Datenbank in der temporären Kopie sofort alles, was seit
>   der Sicherung abgelaufen war — die Dokumentzahlen stimmten dann nicht mehr
>   mit dem Manifest überein, und der Restore brach ab (je älter das Backup,
>   desto sicherer). Nach dem Scharfschalten räumt die Datenbank abgelaufene
>   Einträge wie im laufenden Betrieb selbst weg.
> - Die Collections werden **Stück für Stück** (je 1000 Dokumente) aus der
>   Datei geladen statt komplett im Arbeitsspeicher gehalten — ein großer
>   Restore im Backend-Container (4 GB) wird nicht mehr per OOM beendet.
> - Nach dem Setzen des Wartungsmodus wartet der Restore, bis ein laufender
>   Aufräumlauf angehalten hat und alle Backend-Prozesse null offene
>   Schreibzugriffe melden (mindestens `RESTORE_AUSLAUF_MIN_S`, Standard 6 s,
>   höchstens `RESTORE_AUSLAUF_MAX_S`, Standard 180 s; danach fährt er mit
>   Warnung fort). Der Aufräumlauf selbst hält jetzt vor jedem Schritt an,
>   sobald eine Schreibpause oder ein Restore läuft.
> - Die Sicherung überschreibt einen Restore-Merker nicht mehr und startet
>   während eines Restores gar nicht erst (Exit 1, neuer Versuch in einer
>   Stunde).

| Flag | Wirkung |
|---|---|
| `--dry-run` | nur prüfen, nichts verändern (meldet auch unvollständige Backups als Fehler) |
| `--yes` | ohne Rückfrage |
| `--db <name>` | Zieldatenbank (Standard `DB_NAME`) |
| `--allow-no-manifest` | alte Backups ohne `manifest.json` (keine Prüfsummen) |
| `--notfall-unvollstaendig-akzeptieren` | ein als UNVOLLSTAENDIG markiertes Backup **trotzdem** einspielen — nur im Notfall; die fehlenden Teile werden laut aufgelistet und fehlen danach |
| `--ohne-s3` | S3-Objekte im Backup bewusst nicht zurückspielen (sonst Abbruch, wenn S3 hier nicht konfiguriert ist) |
| `--nur-datenbank` | Datei-Speicher (uploads, local_storage, S3) unangetastet lassen — für die Restore-Probe in eine Testdatenbank |
| `--exakt` | Collections, die es live gibt, im Backup aber nicht, wandern in die Vorher-Datenbank — der Live-Stand entspricht danach exakt dem Backup (Phase 3, 15.09.2026). **Seit 20.09.2026 (Nr. 75) das Standardverhalten**; die Option ist nur noch aus Gewohnheit erlaubt, Gegenteil: `--zusaetzliche-behalten`. Die Schema-Version wird in jedem Fall aus dem Backup übernommen, fehlende Migrationen laufen beim nächsten Start. |
| `--zusaetzliche-behalten` | Collections, die es nur live gibt, **stehen lassen** — der Stand ist dann gemischt (alte Daten aus dem Backup neben neueren Collections); laute Warnung, nur mit gutem Grund |
| `--notfall-inkonsistent-akzeptieren` | ein als INKONSISTENT markiertes Backup (Snapshot gescheitert, Collections nacheinander gelesen) trotzdem einspielen — nur im Notfall |
| `--alt-backup-ohne-indexdaten` | Alt-Backup ohne (lesbare) `metadata.json` einspielen; für diese Collections werden dann **keine** Indexe angelegt oder geprüft (Standard: Abbruch) — der nächste Start legt sie über `migrationen.py` neu an |
| `--vorher-aufbewahrung TAGE` | nach erfolgreichem Restore ältere Sicherungskopien (`<db>__vorher_<zeit>`, `uploads.vorher-<zeit>`) löschen; Standard 30 Tage, `0` = nie; der jüngste Stand bleibt immer (Abschnitt „Alte Sicherungskopien nach einem Restore“) |

**Wartungsmodus:** Vor dem Umschalten schreibt der Restore in der
Zieldatenbank `system_flags` → `{_id: "wartungsmodus", aktiv: true, grund:
"Restore", seit: <iso>}`; die API-Middleware antwortet solange mit **503**.
Nach Erfolg oder Rollback wird `aktiv: false` gesetzt. Nur wenn ein Rollback
selbst scheitert (Zustand gemischt), bleibt er absichtlich aktiv — die
Ausgabe nennt dann den Befehl; manuell aufheben (zeigt ohne `--ja` nur den Stand):
```bash
docker compose exec backend python scripts/wartung_aufheben.py --ja
```
`system_flags` selbst wird nie aus dem Backup zurückgespielt.

Nach dem Restore bleiben `autoschnell__vorher_<zeit>` sowie
`uploads.vorher-<zeit>` / `local_storage.vorher-<zeit>` als Rückfalllinie
(im Container sind beide Ordner eingehängte Volumes — dort liegt der
bisherige Stand als Unterordner `.vorher-<zeit>` **im** Volume, weil sich ein
Einhängepunkt nicht umbenennen lässt; `--dry-run` nennt den Weg) —
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
beschreibbaren Backup-/Upload-Verzeichnissen, fehlendem SMTP,
Aufbewahrungsfristen ≤ 0, halb konfiguriertem S3 sowie bei doppelten Werten
in Feldern mit Eindeutigkeits-Index (`scripts/dubletten_pruefen.py`). Die
Prüfung läuft **vor** Indexanlage und Admin-Seeding.

Seit Runde 15 gilt zusätzlich: **höchstens ein offener Abholtermin je
Fahrzeug und Firma** (Teil-Unique-Index `termin_offen_je_fahrzeug`). Gibt es
im Bestand noch mehrere offene Termine zum selben Fahrzeug, startet das
Backend trotzdem, legt den Index aber nicht an und schreibt eine Warnung ins
Log (`ensure_indexes: appointments: mehrere OFFENE Termine je Fahrzeug`).
Dann `python -X utf8 scripts/dubletten_pruefen.py` im Backend-Container
ausführen, die genannten Termine im Terminplaner abschließen oder löschen
und das Backend einmal neu starten. Bis dahin greift nur die Vorabprüfung
der Routen (409 „bereits ein offener Abholtermin"), nicht der Index.

Seit Runde 16 (Beschluss 08.09.2026) sehen **Sucher nur noch ihren eigenen
Arbeitsbereich**: Fahrzeuge (`vehicles.owner_user_id`), Termine,
Beweisdokumente, Abholberichte und Protokolle; der Händler-Hauptaccount sieht die
ganze Firma. (Fahrzeuge in der Fahrzeugakte umhängen konnte der Chef bis zum
21.09.2026; seitdem entfallen — Entscheidung Ahmad R1-01: niemandem wird ein
Fahrzeug, Vertrag oder Termin weggenommen.) Die Migration m4
(läuft beim ersten Start automatisch, Protokoll in `schema_migrations`)
ordnet den Altbestand zu: ältester Vertrag → ältester Vergleich →
Aktivität → ältester Termin → Chef. Fahrzeuge, die sich keinem Konto der
Firma zuordnen lassen (`stats.offen`), sieht nur der Chef; ein Sucher
übernimmt sie beim erneuten Vergleich des Links. Ein Sucher, der ein Inserat vergleicht, das ein Kollege bereits
führt, wird **Mitbearbeiter** (Wunsch 09.09.2026): das Fahrzeug erscheint
auch in seinem Bereich und er darf einen eigenen Kaufvertrag anlegen.
Hauptbearbeiter bleibt, wer zuerst verglichen hat.

**Kaufvorgänge (Umbau 09.09.2026):** Das Fahrzeug ist nur noch das
gemeinsame Inserat der Firma. Jeder Vertrag hat einen eigenen
Kaufvorgang (Sammlung `kaufvorgaenge`: Sucher, Fahrzeug, Vertrag,
Kaufpreis, Status, Termin). Damit können mehrere Sucher dasselbe Auto
unabhängig kaufen: jeder mit eigenem Vertrag, eigenem Termin (ein offener
Termin je Vertrag, Index `termin_offen_je_vertrag`) und eigenem Preis.
Der Fahrzeugstatus ist nur eine Zusammenfassung aller Vorgänge; „nicht
abgeholt" am Fahrzeug erst, wenn kein Vorgang mehr offen ist. Der
realisierte Einkaufspreis am Fahrzeug wird beim Abholen aus dem
erfolgreichen Vorgang übernommen. Sucher sehen Termine, Berichte,
Protokolle und Abweichungsfotos nur zu eigenen Vorgängen; der Hauptaccount
sieht alle. Die Migrationen m5 (Kaufvorgänge für Altverträge) und m6
(Besitzer nur aktive Konten, nächster gültiger Kandidat) laufen beim
ersten Start automatisch; Protokoll in `schema_migrations`.

Seit Runde 17 (08.09.2026) außerdem:
- **`VERTRAG_LOESCHUNG_AKTIV` muss in der Produktions-.env stehen** — `true`
  (Löschung nach `VERTRAG_AUFBEWAHRUNG_TAGE`, Standard 60 Tage, scharf) oder `false` (Trockenlauf). Fehlt die Variable
  ganz, bricht der Start ab (bewusste Entscheidung statt Vergessen).
- Die Betrieb-Seite im Admin zeigt `termin_index_aktiv` und
  `fahrzeug_index_aktiv`. Fehlt einer der beiden Unique-Indizes wegen
  Altdubletten (Alarm `termin_index_fehlt` bzw. `unique_index_fehlt`),
  zuerst `python -X utf8 scripts/dubletten_pruefen.py` im Backend-Container,
  Daten bereinigen, dann im Admin „Nachholen“ drücken (legt die Indizes
  ohne Neustart an und schließt den Alarm).
- Fahrzeug-IDs neuer mobile.de-/AutoScout-Vergleiche heißen
  `v_mobile_<ID>` bzw. `v_autoscout24_<ID>`; bestehende `v_<ID>` bleiben
  gültig (Rückfall beim Vergleich). Kleinanzeigen bleibt `v_<ID>`.
- Bricht ein Rollout ab, bleibt der Server im Drain (siehe oben,
  `deploy/freigeben.sh`).

## Auto-Daten & Vertragslöschung (60 Tage)
- Kaufverträge (Verkäufer-Personendaten, PDF, Versionen, Versandstatus)
  werden nach `VERTRAG_AUFBEWAHRUNG_TAGE` (Standard 60) vom stündlichen
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
  `.env` `WEB_CONCURRENCY` erhöhen (Grenze: `(BACKEND_MEM_LIMIT − 500 MB) / 400 MB`,
  Abschnitt 2) **und** `RESEND_PROZESSE` sowie `APIFY_MAX_PARALLEL` mitziehen, dann
  `docker compose up -d`.
- **Abруf-Sperren vermeiden** (viele neue Vergleiche): `PROXY_ENABLED=true`
  + `PROXY_URL=...` setzen. Langfristig ist das client-seitige Abrufen
  (Browser-Erweiterung der Nutzer) geplant — verteilt die Abrufe auf
  hunderte IPs statt einer Server-IP.

## Zweiter Server (prod2): Replikat, Snapshots in R2, Load Balancer

Stand 09/2026: zwei gleich starke Server (CCX23). Der Umbau geschieht in
drei Schritten, jeder fuer sich nuetzlich, jeder fuer sich rueckbaubar.
Reihenfolge einhalten — Schritt 2 setzt Schritt 1 voraus, Schritt 3
setzt beide voraus.

Was danach gilt: Jede Aenderung in der Datenbank liegt sofort auf beiden
Servern. Faellt prod1 aus, gehen keine Daten verloren. Ob das Umschalten
automatisch passiert, haengt vom Schiedsrichter ab (siehe 2c).

### 1. Alte Beweis-Snapshots in den Objektspeicher (R2)

Seit 10.09.2026 entstehen keine Snapshots mehr (siehe „Beweisdokument je
Inserat“ unten). Der folgende Schritt betrifft nur noch Aufnahmen von
davor, bis sie verfallen sind.

Fotos liegen laengst in R2. Die Snapshot-Dateien (JPG + PDF je Inserat)
lagen bis jetzt nur auf der Platte von prod1 — ein zweiter Server saehe
sie nicht. Seit diesem Stand schreibt das Backend neue Snapshots
automatisch nach R2, sobald `S3_ENDPOINT` und `S3_BUCKET` gesetzt sind
(sind sie). Die alten Dateien einmal hinterhertragen:

```bash
cd /opt/autoschnell && git pull && docker compose up -d --build
docker compose exec -T backend python scripts/snapshots_nach_r2.py
```

Das ist ein Probelauf und zeigt nur, was passieren wuerde. Dann:

```bash
docker compose exec -T backend python scripts/snapshots_nach_r2.py --wirklich
```

Beliebig oft wiederholbar; was schon in R2 liegt, wird uebersprungen. Die
lokalen Kopien bleiben liegen, bis du sie ausdruecklich mit `--loeschen`
entfernst (nur nach bestaetigtem Upload). Kontrolle: `/api/ready` zeigt
weiterhin `s3: up`, und ein alter Snapshot laesst sich in der Oberflaeche
oeffnen.

### 2. MongoDB als Replikat ueber beide Server

Voraussetzungen: beide Server im selben privaten Hetzner-Netz
(10.0.0.0/16), die Hetzner-Firewall blockt 27017 aus dem Internet (wie
bisher), und **derselbe** `deploy/mongo-keyfile` liegt auf beiden Servern
(von prod1 kopieren, Rechte `chmod 400`, `chown 999:999`).

**2a. prod2 vorbereiten.** Projekt wie in Abschnitt 1 auf prod2 laden, die
`.env` von prod1 uebernehmen — mit diesen Unterschieden auf jedem Server:

```
# prod1                          # prod2
PRIVATE_IP=10.0.0.2              PRIVATE_IP=10.0.0.3
MONGO_NAME=mongo-prod1           MONGO_NAME=mongo-prod2
PROD1_IP=10.0.0.2                PROD1_IP=10.0.0.2
PROD2_IP=10.0.0.3                PROD2_IP=10.0.0.3
```

(Die privaten Adressen stehen in der Hetzner-Konsole unter Netzwerke.)
Auf **beiden** Servern die MONGO_URL auf beide Mitglieder umstellen:

```
MONGO_URL=mongodb://autoschnell_app:PASSWORT@mongo-prod1:27017,mongo-prod2:27017/?authSource=admin&replicaSet=rs0&maxPoolSize=20
```

Ab jetzt wird auf beiden Servern IMMER mit der Ergaenzungsdatei
gestartet:

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.replica.yml up -d
```

**2b. Mitglied umbenennen und zweites Mitglied aufnehmen** (auf prod1; das
Umbenennen dauert Sekunden, in denen nicht geschrieben werden kann):

```bash
docker compose exec mongo mongosh -u "$MONGO_USER" -p "$MONGO_PASSWORD" --eval '
  cfg = rs.conf();
  cfg.members[0].host = "mongo-prod1:27017";
  rs.reconfig(cfg, {force: true});
  rs.add({host: "mongo-prod2:27017", priority: 0.5});
  rs.status().members.map(m => m.name + " " + m.stateStr)'
```

`priority: 0.5` heisst: prod1 bleibt bevorzugt der schreibende Server,
solange er lebt. Danach auf prod2 den Stack starten (Befehl aus 2a) — der
Mongo-Container dort ist leer und holt sich den kompletten Stand von
prod1 (bei deiner Datenmenge Sekunden).

Kontrolle, auf beiden Servern:

```bash
docker compose exec -T backend python scripts/replikat_pruefen.py
```

Erwartet: `mongo-prod1 PRIMARY`, `mongo-prod2 SECONDARY`, Rueckstand 0 s.

**Was `{w: 1}` bedeutet (bewusste Entscheidung):** Ein Schreibvorgang
gilt als erledigt, sobald der PRIMARY ihn hat — noch bevor prod2 ihn
kopiert hat (Rueckstand normalerweise unter einer Sekunde). Faellt prod1
in genau diesem Augenblick aus (endgueltig ODER nur kurz, wenn prod2
inzwischen PRIMARY wurde), koennen die letzten Sekunden Schreibarbeit
fehlen (ein gerade angelegter Vertrag muesste noch einmal angelegt
werden); MongoDB legt sie beim Wiederanschluss von prod1 unter
`/data/db/rollback/` als BSON ab, von Hand zurueckspielbar. Die Alternative `majority` wuerde dafuer bei JEDEM
Ausfall von prod2 alle Schreibvorgaenge anhalten. Fuer einen Zwei-Server-
Betrieb ist `{w: 1}` die uebliche Wahl. Ehrlich dazu: die Sicherung laeuft
EINMAL naechtlich (BACKUP_HOUR, Standard 03:00) — sie faengt den Totalverlust
BEIDER Server auf, nicht die letzten Sekunden vor einem Ausfall von prod1;
die deckt die Replikation auf prod2. Was dazwischen fehlen koennte, laesst
sich ueber die Belege (Resend-Kennung, Stripe-Ereignisse) nachvollziehen.

**2c. Schiedsrichter — die ehrliche Einschraenkung.** Zwei Mitglieder
koennen bei Ausfall eines Servers keine Mehrheit bilden: der uebrige
Server stellt das Schreiben ein, bis jemand eingreift (die Daten sind
sicher, die Seite ist bis dahin nur lesend). Fuer automatisches
Umschalten braucht es einen dritten Waehler, der NICHT auf prod1 oder
prod2 liegt: ein Schiedsrichter (Arbiter) auf einem kleinen dritten
Server (CX23, rund 4 Euro), ohne Daten, ohne Last.

Gemacht am 05.09.2026 (auto-spider-arbiter, 10.0.0.5). Zwei Dinge, die
man wissen muss: der Schiedsrichter muss die anderen Mitglieder unter
ihren NAMEN erreichen (`--add-host`), und MongoDB verlangt vor dem
Aufnehmen eine ausdrueckliche Schreibbestaetigungs-Regel. `{w: 1}` ist
fuer diesen Aufbau die richtige: bestaetigt der schreibende Server, gilt
es — mit `majority` muessten BEIDE Datenserver bestaetigen, und ein
Ausfall von prod2 liesse alle Schreibvorgaenge haengen.

```bash
# auf dem dritten Server, nach Kopie des Keyfiles nach /opt/mongo/:
chmod 400 /opt/mongo/mongo-keyfile && chown 999:999 /opt/mongo/mongo-keyfile
docker run -d --name mongo-arbiter --restart unless-stopped   -p 10.0.0.5:27017:27017   --add-host mongo-prod1:10.0.0.2 --add-host mongo-prod2:10.0.0.3   -v /opt/mongo/mongo-keyfile:/etc/mongo-keyfile:ro -v mongo_arbiter:/data/db   mongo:8.2 mongod --replSet rs0 --keyFile /etc/mongo-keyfile --bind_ip_all
# auf prod1:
docker compose exec mongo mongosh -u "$MONGO_USER" -p "$MONGO_PASSWORD" --eval '
  db.adminCommand({setDefaultRWConcern: 1, defaultWriteConcern: {w: 1}});
  rs.addArb("10.0.0.5:27017")'
docker compose exec -T backend python scripts/replikat_pruefen.py   # 3 Waehler
```

Ohne Schiedsrichter — manuelles Umschalten, wenn prod1 tot ist (auf
prod2):

```bash
docker compose exec mongo mongosh -u "$MONGO_USER" -p "$MONGO_PASSWORD" --eval '
  cfg = rs.conf(); cfg.members = cfg.members.filter(m => m.host.startsWith("mongo-prod2"));
  rs.reconfig(cfg, {force: true})'
```

**prod2 laenger weg (Rollenpruefung 22.09.2026, RP-552).** Mit
Schiedsrichter bleibt prod1 PRIMARY und schreibt weiter (`{w: 1}`). Aber der
**Mehrheits-Commitpunkt** bleibt stehen, solange prod2 fehlt: nur prod1 hat
die neuen Daten, der Schiedsrichter traegt keine. Zwei Folgen:

1. Der PRIMARY muss alles seit dem Ausfall im Cache halten — er wird mit
   der Zeit langsam (WiredTiger-Cachedruck).
2. Die naechtliche Sicherung liest per Snapshot genau diesen stehenden
   Punkt. Frueher kam deshalb jede Nacht **still der Stand vom
   Ausfallzeitpunkt** heraus. Seit 22.09.2026 misst `backup_mongo.py` den
   Rueckstand (`replSetGetStatus`); ist er groesser als
   `BACKUP_MEHRHEIT_RUECKSTAND_MAX_S` (Standard 600 s), liest die Sicherung
   die Collections nacheinander vom PRIMARY (aktuell, aber nicht
   stichtagsgenau), endet mit Code 3 und legt den Betriebsalarm
   `backup_inkonsistent` an. Einspielen ginge nur mit
   `--notfall-inkonsistent-akzeptieren`.

Ist absehbar, dass prod2 laenger als ein paar Stunden fehlt, das
ausgefallene Mitglied voruebergehend **ohne Stimme** fuehren — dann gilt
wieder prod1 + Schiedsrichter als Mehrheit, der Commitpunkt laeuft mit,
Cache und Sicherung sind wieder normal (auf prod1):

```bash
docker compose exec mongo mongosh -u "$MONGO_USER" -p "$MONGO_PASSWORD" --eval '
  cfg = rs.conf();
  m = cfg.members.find(x => x.host.startsWith("mongo-prod2"));
  m.votes = 0; m.priority = 0;
  rs.reconfig(cfg)'
docker compose exec -T backend python scripts/replikat_pruefen.py
```

**Ruecknahme**, sobald prod2 wieder laeuft und aufgeholt hat (Rueckstand
0 s in `replikat_pruefen.py` — vorher NICHT, sonst haengt der Schritt):

```bash
docker compose exec mongo mongosh -u "$MONGO_USER" -p "$MONGO_PASSWORD" --eval '
  cfg = rs.conf();
  i = cfg.members.findIndex(x => x.host.startsWith("mongo-prod2"));
  cfg.members[i].votes = 1; cfg.members[i].priority = 0.5;
  rs.reconfigForPSASet(i, cfg)'
docker compose exec -T backend python scripts/replikat_pruefen.py
```

Wichtig (Rollenpruefung 22.09.2026, Review): **nicht** `rs.reconfig(cfg)`.
Ab MongoDB 5.0 (wir laufen mit 8.2) lehnt `rs.reconfig` genau diesen
Uebergang ab — alte Konfiguration mit nur einem schreibenden Mitglied mit
Stimme, neue PSA mit waehlbarem Secondary ("Rejecting reconfig where the new
config has a PSA topology and the secondary is electable ..."). prod2 bliebe
dann ohne Stimme und ohne Failover. `rs.reconfigForPSASet(<Index im
members-Feld>, cfg)` macht es in zwei sicheren Schritten (erst Stimme mit
priority 0, nach dem Mehrheits-Commit priority 0.5). Von Hand geht
dasselbe so: erst `votes = 1; priority = 0` mit `rs.reconfig(cfg)`, warten,
bis `rs.status()` prod2 als SECONDARY zeigt, dann `priority = 0.5` mit
einem zweiten `rs.reconfig(cfg)`. Am Ende muss `rs.conf()` bei prod2
`votes: 1, priority: 0.5` zeigen.

Danach eine Sicherung von Hand ziehen
(`docker compose exec backend python scripts/backup_mongo.py`) und den
Alarm `backup_inkonsistent` auf der Betrieb-Seite abhaken.

**Was serveruebergreifend schon stimmt:** Sicherung, Aufraeumer und
Sperren laufen ueber Sperren in der Datenbank (`job_locks`) — sie laufen
auch mit zwei Backends genau einmal. Anfragesperren (Login-Versuche)
liegen in `rate_limits`. Fotos und Snapshots liegen in R2.

**Rueckbau:** `rs.remove("mongo-prod2:27017")` auf prod1, Ergaenzungsdatei
weglassen, MONGO_URL wieder auf `mongo:27017` — fertig.

### 3. Beide Server hinter dem Hetzner Load Balancer

Erst sinnvoll, wenn Schritt 2 laeuft — sonst schreibt prod2 in eine
Datenbank, die prod1 nicht sieht.

**Zertifikat: von Cloudflare, nicht von Hetzner.** Hetzners verwaltetes
Zertifikat braeuchte die Domain in einer Hetzner-DNS-Zone; die Konsole
nimmt nur Hauptdomains, und die Hauptdomain soll bei Cloudflare bleiben
(E-Mail-Eintraege!). Stattdessen: ein Cloudflare-Origin-Zertifikat
(15 Jahre gueltig, keine Erneuerung) am Load Balancer, und `app.` laeuft
ueber den Cloudflare-Proxy. Weg: Besucher -> Cloudflare (TLS) -> Load
Balancer (TLS mit Origin-Zertifikat) -> nginx (HTTP, privates Netz).
Die LB-Vorlage kennt die Cloudflare-Netze, damit nginx die echte
Besucheradresse sieht.

**3a. Cloudflare: Origin-Zertifikat erzeugen.** Zone auto-schnellkauf.de
-> SSL/TLS -> Origin Server -> "Create Certificate": RSA 2048, Hostnames
`app.auto-schnellkauf.de` (Vorschlag `*.auto-schnellkauf.de` und
`auto-schnellkauf.de` kann bleiben), Gueltigkeit 15 Jahre. Zertifikat
UND privaten Schluessel sofort kopieren — der Schluessel wird nur einmal
angezeigt.

**3b. Hetzner: Zertifikat hochladen.** Konsole -> Sicherheit ->
Zertifikate -> "Zertifikat hochladen": Name `cloudflare-origin-app`,
Zertifikat und Schluessel einfuegen.

**3c. Load Balancer anlegen.** Konsole -> Load Balancer: Standort
Nuernberg, Typ LB11, privates Netz auswaehlen. Ziele: prod1 und prod2,
jeweils "ueber privates Netz". Dienst 1: HTTPS, Port 443 -> Zielport 80,
Zertifikat `cloudflare-origin-app`. Dienst 2: HTTP 80 -> 80 mit
"Umleitung auf HTTPS". Gesundheitspruefung: HTTP, Port 80, Pfad
`/api/health`, Intervall 15 s. Die Ziele zeigen jetzt noch "unhealthy"
— prod1 antwortet auf 80 mit einer Umleitung, prod2 hat noch keinen
Web-Stack. Richtig so.

**Cloudflare SSL-Modus pruefen:** SSL/TLS -> Overview auf "Full (strict)"
stellen — das Origin-Zertifikat ist von Cloudflare selbst ausgestellt,
"strict" prueft es also sauber. "Full" liefe auch, prueft das Zertifikat
aber nicht (ein Angreifer im Weg zum LB koennte sich mit irgendeinem
Zertifikat ausgeben). Bei "Flexible" spraeche Cloudflare unverschluesselt
mit dem LB, der auf HTTPS umleitet — Endlosschleife.

**Vorbereitung ohne Ausfall:** Den A-Eintrag `app` bei Cloudflare
mindestens eine Stunde VOR dem Umschalten auf TTL "2 min" setzen — NICHT
"Auto": Auto bedeutet bei Cloudflare 300 s, also fuenf Minuten (bei
eingeschaltetem Proxy erzwingt Cloudflare ohnehin Auto). Und:
prod1 waehrend des Umschaltens NICHT abschalten — der alte Weg (direkt
auf prod1, Port 443) bleibt offen, bis der neue Weg nachweislich laeuft
(Schritt 3f kommt zuletzt).

**3d. prod2: Web-Stack im LB-Modus starten** (nach dem Lasttest).
In der `.env` auf prod2:

```
PROXY_TEMPLATE=hinter-loadbalancer.conf.template
PRIVATES_NETZ=10.0.0.4/32
TRUSTED_PROXIES=127.0.0.1,172.16.0.0/12,10.0.0.4/32
```

`PRIVATES_NETZ` ist die Adresse, von der nginx Anfragen ueberhaupt
annimmt: nur der Load Balancer (10.0.0.4), nicht das ganze private Netz
— sonst koennte jeder weitere Server im selben Netz (auch ein fremder,
wenn das Netz einmal geteilt wird) an Cloudflare vorbei direkt auf die
Seite. `TRUSTED_PROXIES` nennt die eigenen Vermittler fuer die
Besucheradresse (X-Forwarded-For). Ohne weiteren Schalter zaehlen daneben
IMMER die privaten Netze (10/8, 172.16/12, 192.168/16, Loopback) als
Vermittler — der direkte Nachbar des Backends ist ohnehin stets der eigene
nginx-Container. Wer wirklich nur die Liste gelten lassen will:
`TRUSTED_PROXIES_NUR_LISTE=true` in die `.env` (docker-compose.yml reicht
den Schalter durch; dann MUSS das Docker-Netz des nginx-Containers, z.B.
`172.16.0.0/12`, in der Liste stehen). Cloudflare-Kopfzeilen
(CF-Connecting-IP) reicht nginx seit Runde 10 nicht mehr durch — die
Besucheradresse kommt aus real_ip.

```bash
cd /opt/autoschnell && docker compose -f docker-compose.yml -f deploy/docker-compose.replica.yml up -d --build
```

Im Load Balancer wird prod2 nach spaetestens 30 s "healthy".

**3e. Umschalten** (die eine Minute): prod1 auf den LB-Modus umstellen —
dieselben drei Zeilen in die `.env` von prod1, dann nur den Proxy neu
starten:

```bash
cd /opt/autoschnell && docker compose -f docker-compose.yml -f deploy/docker-compose.replica.yml up -d --force-recreate proxy
```

Sofort danach bei Cloudflare den A-Eintrag `app` von 2.28.66.8 auf die
oeffentliche IP des Load Balancers aendern und den Proxy EINSCHALTEN
(orange Wolke). Cloudflare-Aenderungen greifen in Sekunden; wer bis zum
Ablauf der TTL (hoechstens 2 Minuten, siehe Vorbereitung) noch prod1
direkt anspricht, bekommt einen Fehler — laenger dauert es nicht. Beide
Ziele im LB sind jetzt "healthy".

**3f. Firewall zuziehen.** In der Hetzner-Firewall der beiden Server die
Regeln fuer 80 und 443 aus dem Internet entfernen. Der Load Balancer
spricht ueber das private Netz, das die Cloud-Firewall nicht filtert.
Direkt am Server kommt ab jetzt niemand mehr vorbei.

**3g. Aufraeumen und pruefen.** Der cron `/etc/cron.d/autoschnell-zertifikat`
auf prod1 ist ueberfluessig (das Zertifikat liegt jetzt am LB) und wird
geloescht. Kontrolle von aussen und innen:

```bash
python scripts/betriebsprobe.py app.auto-schnellkauf.de --mail-domain auto-schnellkauf.de --dkim-selector resend
docker compose exec -T backend python scripts/replikat_pruefen.py
```

**Rueckbau (vollstaendig, in dieser Reihenfolge):**
1. Hetzner-Firewall von prod1: Regeln fuer 80 und 443 aus dem Internet
   WIEDER anlegen (Schritt 3f hat sie entfernt — ohne das kommt niemand an).
2. prod1 `.env`: `PROXY_TEMPLATE=default.conf.template`, `PRIVATES_NETZ`
   und `TRUSTED_PROXIES` wie vor dem LB; Proxy neu starten.
3. Pruefen, dass das Let's-Encrypt-Zertifikat auf prod1 noch gueltig ist
   (`openssl x509 -enddate -noout -in deploy/certs/fullchain.pem`); den in
   3g geloeschten cron `/etc/cron.d/autoschnell-zertifikat` wieder anlegen
   (siehe oben "Zertifikat erneuern"), sonst laeuft es nach 90 Tagen ab.
4. Cloudflare: A-Eintrag `app` auf 2.28.66.8, Proxy AUS (grau).
5. `python scripts/betriebsprobe.py app.auto-schnellkauf.de` von aussen.

## Sicherheits-Checkliste vor dem Live-Gang
- [ ] `JWT_SECRET` auf langen Zufallswert gesetzt
- [ ] `SUPER_ADMIN_PASSWORD` stark und geändert (nicht der Entwicklungswert); `SUPER_ADMIN_USERNAME` sieht nicht wie eine Kontonummer aus
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
# 2. In .env eintragen: JWT_SECRET, SUPER_ADMIN_PASSWORD,
#    MONGO_PASSWORD (+ Mongo-Benutzer ändern: mongosh db.changeUserPassword),
#    SMTP_PASS, APIFY_TOKEN, S3_SECRET_KEY
# 3. Stack neu starten (neue Werte greifen; alte JWTs sind durch den neuen
#    JWT_SECRET ungültig)
docker compose up -d --build
# 4. Alle Sitzungen widerrufen (auch Fahrer/Käufer)
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
`GET /api/ready` (Feld `schema_version`). Seit 22.09.2026 (Prüfbericht
20.09.2026, AL-05) fährt `deploy/rollout.sh` dieselbe Migration schon **vor**
dem Containerwechsel als eigenen Schritt (Abschnitt „Updates einspielen“);
der Container-Start wiederholt sie nur noch als Absicherung (idempotent,
Sekunden). Sperrzustand: `python migrationen.py --sperre-gehalten`.

### Liveness und Readiness
- `/api/health` — nur Datenbank-Ping (Container-Healthcheck).
- `/api/ready` — 503 bei Datenbank, Migrationsstand, Speicherplatz
  (`MIN_FREI_MB`) oder nicht schreibbarem Datei-Speicher; Warnungen bei
  Backup älter als 26 h, offenen Betriebsalarmen, hängenden Link-Jobs, S3.
  Für externe Überwachung `/api/ready` verwenden.
- Admin → **Betrieb** (nur Super-Admin): offene Alarme (nicht löschbare
  Dateien, Vertrag ohne Datensatz, Backup unvollständig; *historisch:* „bezahlt
  ohne Zugang“ — entfällt seit 14.09.2026, Stripe entfernt, Prüfbericht DO-22),
  Löschwarteschlange, hängende Freischaltungs-Vorgänge, letztes Backup,
  Reparaturlauf per Klick (läuft sonst alle 10 Minuten automatisch).

### Wartungsmodus
`system_flags {_id:"wartungsmodus", aktiv:true}` lässt die API mit 503
antworten (außer /health, /ready). Der Restore setzt und löscht das Flag
selbst; manuell: `docker compose exec backend python scripts/wartung_aufheben.py --ja`
(ohne `--ja` zeigt es nur, wer den Merker seit wann hält).

### Proxy: Host-Allowlist und Sicherheits-Header
`PUBLIC_HOST` (in .env, Pflicht) ist die einzige bediente Domain; andere
Hosts erhalten 444, HTTP leitet fest auf `https://PUBLIC_HOST` um. Der
Proxy setzt HSTS, `X-Frame-Options`, `nosniff`, Referrer-Policy,
Permissions-Policy und `Content-Security-Policy: frame-ancestors 'none'`
für ALLE Antworten (auch die React-Oberfläche). Die Kopfzeilen stehen seit
22.09.2026 einmal in `deploy/sicherheitskopf.inc` (per docker-compose in den
Proxy eingehängt) und werden von beiden Vorlagen im server-Block **und** in
jeder Location mit eigenem `add_header` eingebunden — nginx vererbt
`add_header` sonst nicht dorthin, unter `/static/` fehlten sie (Prüfbericht
20.09.2026, DP-14); `payment=()` seit Stripe entfernt ist (K-20). Ändert sich
die Datei, erzeugt `deploy/rollout.sh` den Proxy neu. Prüfen nach dem Start:
`curl -sI https://PUBLIC_HOST/ | grep -i -E "strict|frame|content-type-options"`
und dasselbe für eine Datei unter `/static/`.

### Speicher voll
Anzeichen: `/api/ready` meldet zu wenig freien Speicher (unter `MIN_FREI_MB`),
die Sicherung endet mit Exit 1, Uploads scheitern.

1. Nachsehen: `df -h /` und `docker system df`; die Sicherungen mit
   `docker compose exec backend sh -c 'du -sh /backups/* | sort -h | tail -20'`.
2. Gefahrlos löschen darf man:
   - ältere Sicherungsordner `/backups/autoschnell-*` — **den jüngsten mit
     `BACKUP OK` immer behalten**;
   - Reste abgebrochener Läufe: `/backups/.tmp-autoschnell-*` und `/backups/.tmp-*.tar.gz`;
   - nach einem geprüften Restore die Rückfalllinie: Datenbank `<db>__vorher_<zeit>`
     und die Ordner `uploads.vorher-<zeit>` / `local_storage.vorher-<zeit>`;
   - alte Docker-Images: `docker image prune -a` (laufende bleiben).
     Seit 22.09.2026 (RP-559) raeumt `deploy/rollout.sh` nach jedem
     erfolgreichen Rollout selbst auf: verwaiste Images und Bau-Reste aelter
     als 7 Tage (`docker image prune -f`, `docker builder prune -f --filter
     until=168h`). Vorher kamen bei jedem Rollout rund 2 GB im Monat dazu.
3. **Wichtig:** Die Rotation (14 Stände) läuft erst **nach einem erfolgreichen
   Lauf**. Scheitert die Sicherung an voller Platte, bleiben alle alten Stände
   liegen, bis von Hand Platz geschaffen ist. Danach die Sicherung nachholen:
   `docker compose exec backend python scripts/backup_mongo.py`.

### Ressourcen
Standard 4 Worker, seit 10.09.2026 ohne Browser (Beweisdokumente
entstehen mit ReportLab; je Dokument mit 20 Fotos etwa 1–2 s Rechenzeit und
1–2,5 MB in R2). Seit 18.09.2026 entsteht ein Beweisdokument nur noch auf
Knopfdruck (`BEWEIS_AUTOMATISCH`) und wird 30 Tage aufbewahrt — Dauerstand
rund 6 GB (Rechnung im Abschnitt Beweisdokument). Entscheidung Ahmad
14.09.2026: keine Frist über 60 Tage, Backups offsite 14 Archive. `docker-compose.yml`
setzt Speicher-/CPU-Limits (`BACKEND_MEM_LIMIT`, `MONGO_MEM_LIMIT`, …) und
begrenzt den Mongo-Pool (`maxPoolSize=20` in MONGO_URL). Faustregel:
Backend-RAM ≈ 400 MB × Worker + 500 MB.

### Beweisdokument je Inserat (ersetzt die Snapshots, 10.09.2026)
Wird ein Inserats-Link zum ersten Mal verwendet — egal von welcher Firma —,
entsteht genau EIN PDF, das alle Firmen teilen, die das Inserat verwenden:
Portal-Kennzeichnung links oben, Anzeigen-ID, Inserats-Adresse, alle
ausgelesenen Daten geordnet, die Inseratsfotos (höchstens
`BEWEIS_FOTOS_MAX`, alle Adressen im Anhang). Erzeugt wird es im
Hintergrund (`beweis_service.py`, Collection `inserat_beweise`, Dateien
unter `beweise/<portal>/` in R2) — der Vergleich wartet nie darauf.

- Portal-Logos: ohne Datei ein Schriftzug in Markenfarbe. Echte Logos nur
  mit Nutzungsrecht als `backend/assets/logos/<mobile|autoscout24|kleinanzeigen>.png`
  ablegen und neu ausrollen (`backend/assets/logos/LIESMICH.txt`).
- Private Anbieter: nur PLZ/Ort, kein Name/Telefon (`BEWEIS_PRIVATDATEN=1`
  ändert das — vorher Datenschutzerklärung anpassen).
- Aufbewahrung: `BEWEIS_AUFBEWAHRUNG_TAGE` (30 Tage, Entscheidung Ahmad
  18.09.2026 — vorher 60 im Code und 90 in der erzeugten `.env`) ab Erstellung, länger,
  solange bei einer Firma zu dem Inserat ein Kaufvertrag, Abholtermin,
  Verkaufsinserat oder Bestandsfahrzeug besteht (bloß verglichene
  Fahrzeuge halten es nicht). Danach wird die
  Datei gelöscht; die Zeile bleibt als Grabstein, damit derselbe Link
  kein zweites „erstes“ Dokument bekommt.
- Alte Snapshots (vor dem 10.09.2026) bleiben lesbar, bis sie nach der
  bisherigen Regel verfallen (60 Tage, mit Kaufvertrag länger).
- Kontrolle (auf dem Server in `/opt/autoschnell`):
  ```bash
  docker compose exec -T mongo mongosh --quiet -u "$(grep ^MONGO_USER .env | cut -d= -f2)" -p "$(grep ^MONGO_PASSWORD .env | cut -d= -f2)" --authenticationDatabase admin autoschnell --eval 'db.inserat_beweise.aggregate([{$group:{_id:"$status",n:{$sum:1}}}]).toArray()'
  ```
  `offen` sollte nach wenigen Sekunden zu `fertig` werden. Endgültig
  `fehlgeschlagen` löst den Betriebsalarm `beweis_fehlgeschlagen` aus; ein
  neuer Versuch startet **nicht** von selbst (seit 18.09.2026 entsteht das
  Dokument nur auf Knopfdruck): in der Beweis-Karte „Noch einmal versuchen“
  drücken bzw. `POST /api/beweise/anfordern` (Abschnitt „Beweisdokument nur
  noch auf Knopfdruck“).
- **Hängender Job** (Prüfbericht 20.09.2026, DO-23): Ein Link-Job, dessen
  Worker gestorben ist, bleibt bis `processing_until`
  (`LINK_JOB_PROCESSING_TTL`, Standard 240 s) in `processing`, wird dann von
  selbst neu eingereiht und nach `LINK_JOB_MAX_ATTEMPTS` (3) als `failed`
  beendet; `/api/ready` warnt bei Jobs, die länger als 15 min warten.
  Ansehen bzw. wegräumen (mongosh wie oben):
  `db.link_jobs.find({status:{$in:["queued","processing"]}}, {id:1,status:1,updated_at:1,processing_until:1})`,
  `db.link_jobs.deleteOne({id:"<id>"})`. Sperren in `job_locks` (`migration`,
  `cleanup-cycle`, `backup-<tag>`, `betriebsmeldung`) laufen von selbst ab
  (`expires_at`); ansehen: `db.job_locks.find({}, {name:1,owner:1,expires_at:1})`,
  im Notfall `db.job_locks.deleteOne({name:"<name>"})` — nur, wenn der
  Besitzer-Prozess sicher nicht mehr läuft. Die Migrationssperre fragt
  `docker compose exec -T backend python migrationen.py --sperre-gehalten`
  ab (Code 0 = gehalten, 1 = frei).

### Installierbare App (09/2026)
AutoSchnell lässt sich als App installieren — Symbol auf Taskleiste,
Startmenü, Dock oder Startbildschirm. Der Knopf „Als App installieren“ steht
auf den Anmeldeseiten (Firma, Fahrer, Marktplatz), in der Seitenleiste der
Firmen-App und in der Kopfzeile der Fahrer-App; er erscheint nur, wenn der
Browser es kann und die App dort noch nicht installiert ist.

- Das Symbol öffnet `/start`: noch angemeldet → direkt zur eigenen
  Startseite (Sucher: Vergleich, Chef: Bestand, Fahrer, Marktplatz), sonst
  zur zuletzt benutzten Anmeldung; ist nichts bekannt (neues Gerät), eine
  Auswahl Firma / Fahrer / Marktplatz (`src/lib/appstart.js`).
- iPhone/iPad und Safari am Mac: Die installierte App hat einen eigenen
  Speicher, getrennt vom Browser (von Apple so gewollt). Man meldet sich in
  der App einmal an; wegen „eine Sitzung je Konto“ endet dabei die Anmeldung
  im Browser. Der Anleitungs-Dialog sagt das.
- `public/manifest.json` trägt `"id": "/driver-login"` — das ist die Kennung,
  unter der Fahrer das frühere „Fahrer-Portal“ installiert haben (ohne `id`
  gilt die alte `start_url`). Den neuen Einstieg bekommen diese
  Installationen automatisch, Name und Symbol am PC erst, wenn der Nutzer
  das App-Update bestätigt; iPhone/iPad-Symbole ändern sich nie. Deshalb
  leitet `/driver-login` dauerhaft auf `/start` weiter. Die `id` NIE ändern:
  sonst gilt jede Installation als fremde App und bekommt keine Änderungen mehr.
- `public/service-worker.js` speichert NICHTS zwischen. Er behandelt nur
  Seitenaufrufe (Chrome/Edge verlangen einen fetch-Handler für den
  Installieren-Dialog) und zeigt ohne Netz eine Seite „Keine
  Internetverbindung“, die von selbst neu lädt, sobald der Server wieder
  antwortet. Alle übrigen Anfragen (API, Bilder, Skripte) schicken
  Chrome/Edge ab Version 126 per Static Routing ganz am Worker vorbei;
  ältere Browser reicht der Handler ohne Umweg durch.
- `service-worker.js`, `boot.js` und `manifest.json` liefert nginx mit
  `Cache-Control: no-cache` aus (`frontend/Dockerfile`), damit Cloudflare
  keine alte Fassung festhält; `e2e/stack.spec.js` prüft das.
- **Cloudflare „Browser Cache TTL“ auf „Respect Existing Headers“ stellen
  (einmalig, Cloudflare → Caching → Configuration → Browser Cache TTL).**
  Mit dem Cloudflare-Standard (4 Stunden) ersetzt Cloudflare bei `.js`- und
  `.png`-Dateien unser `no-cache` durch `max-age=14400` — gemessen am
  11.09.2026 auch bei Abrufen, die Cloudflares Zwischenspeicher umgehen
  (`cf-cache-status: MISS`); `manifest.json` (von Cloudflare nicht
  zwischengespeichert) kam korrekt mit `no-cache`. Folge ohne Umstellung:
  Browser nutzen ein altes `boot.js` bis zu 4 Stunden (Installieren-Knopf
  fehlt dann so lange). Der Service Worker selbst ist nicht betroffen —
  `boot.js` registriert ihn mit `updateViaCache: "none"`.
- Danach einmal Cloudflare → Caching → Configuration → Custom Purge → URL:
  `https://<PUBLIC_HOST>/service-worker.js`, `/boot.js`, `/manifest.json`,
  `/icon-192.png`, `/icon-512.png` (ersatzweise „Purge Everything“).
  Prüfen: `curl -sI "https://<PUBLIC_HOST>/boot.js?x=$RANDOM"` zeigt
  `cache-control: no-cache`. Zeigt es weiter `max-age=14400`, greift die
  Browser-Cache-TTL-Einstellung noch. Was der Server selbst sendet (ohne
  Cloudflare), zeigt auf dem Server:
  `docker compose exec -T proxy wget -S -O /dev/null --header="Host: <PUBLIC_HOST>" http://127.0.0.1/boot.js`
- Notbremse, falls der Service Worker je Ärger macht: den Inhalt von
  `public/service-worker.js` ersetzen durch
  ```js
  self.addEventListener("install", () => self.skipWaiting());
  self.addEventListener("activate", (e) => e.waitUntil(self.registration.unregister()));
  ```
  und ausrollen — beim nächsten Seitenaufruf meldet er sich überall ab.

### Grenzen gelten je Konto, nicht je Firma oder Büro (Runde 26)
Alle Sucher einer Firma sind eigenständige Konten und dürfen sich nie
gegenseitig ausbremsen:

- **Anmeldung:** 10 Versuche je Minute und **Konto** (IP + Kontonummer bzw.
  Benutzername des Super-Admins). Nach einer erfolgreichen Anmeldung wird
  dieser Zähler geleert. Zusätzlich ein weiter gefasstes Limit je IP gegen
  Rateversuche (`LOGIN_IP_LIMIT`, Standard 120/min) — 30 Sucher hinter einer
  Büro-IP passen hinein.
- **Sperre je Kontonummer (13.09.2026):** Fortlaufende Nummern sind erratbar;
  über viele IPs ließe sich ein Passwort gegen alle Nummern probieren. Deshalb
  zählt ein dritter Zähler die Fehlversuche je Kontonummer OHNE IP: nach
  `LOGIN_KONTO_LIMIT` (Standard 30) Fehlversuchen in `LOGIN_KONTO_FENSTER`
  (Standard 900 s) antwortet die Anmeldung 429 — gleicher Text für vorhandene
  und unbekannte Nummern. Ausgenommen sind IPs, von denen sich das Konto schon
  erfolgreich angemeldet hat (bis zu 5, nur als HMAC am Konto). Beim Erreichen
  der Grenze meldet der Betrieb den Alarm `login_konto_angegriffen`. Eine
  erfolgreiche Anmeldung leert diesen Zähler bewusst NICHT, er läuft mit dem
  Fenster ab. Vorher aufheben: **Passwort setzen** durch den Betreiber (Chef,
  Sucher, Zwischenhändler, Fahrer) oder
  `docker compose exec backend python scripts/anmeldesperre_aufheben.py <kontonummer> --ausfuehren`
  (auch mit dem Benutzernamen des Super-Admins; ohne `--ausfuehren` nur
  Probelauf). `LOGIN_KONTO_LIMIT=0` sperrt nie und meldet nur noch den Alarm.
- **Konto prüfen (14.09.2026):** Bekommt jemand „Kontonummer oder Passwort
  falsch“, zeigt der Super-Admin unter **Nutzer** bzw. **Fahrer → Konto prüfen**
  (`GET /admin/konten/pruefen?kennung=…`) zu einer Kontonummer oder einem
  Käufer-Code: Kontoart und Anmeldeseite (Firma/Sucher `/login`, Zwischenhändler
  `/markt/login`, Fahrer `/fahrer/login`), aktiv, Passwort gesetzt, Fehlversuche
  und Anmeldesperre. Häufigster Fall: eine Fahrernummer in der Firmen-Anmeldung
  oder eine Firmennummer in der Fahrer-App — die Masken selbst nennen bewusst
  keine Kontoart. „Passwort setzen“ meldet seitdem nur dann eine aufgehobene
  Sperre, wenn wirklich eine bestand.
- **Kleinanzeigen-Rückfall:** `ABRUF_RUECKFALL_TAGESLIMIT` (25) gilt je
  **Sucher** und Tag, nicht mehr je Firma.
- **Vorschaubilder:** `BILD_PROXY_LIMIT` (3000/min je IP; 20.09.2026 von 1500 erhoeht — 30 Sucher x 40 Bilder sind schon 1200 fuer EINEN Vergleich). Der Bild-Link ist
  signiert und trägt kein Token, deshalb bleibt es ein IP-Limit — der Wert
  ist aber auf ein Büro mit vielen Suchern ausgelegt (ein Vergleich lädt
  bis zu 40 Bilder).
- **Kleinanzeigen über die API:** Ist `KLEINANZEIGEN_API_KEY` gesetzt,
  werden Inserate zuerst über die API von kleinanzeigen-agent.de geholt
  (gemessen 12.09.2026: 0,4 s je Inserat, 8 gleichzeitige Anfragen in
  0,56 s, Limit 600 Anfragen/Minute). Sie liefert zusätzlich den
  Verkäufernamen und meldet beendete Anzeigen zuverlässig. Der eigene
  Abruf der Webseite bleibt die **Notlösung** und springt bei jedem
  API-Problem automatisch ein — ohne Schlüssel läuft alles wie bisher.
  Deshalb gilt für den API-Weg eine eigene, höhere Obergrenze
  (`MAX_CONCURRENT_KLEINANZEIGEN_API`, seit 16.09.2026 Vorgabe 20) als für den Selbst-Abruf
  (`MAX_CONCURRENT_KLEINANZEIGEN`, 2). Wird der Schlüssel abgelehnt,
  steht das als Fehler im Protokoll — sonst liefe still der langsame Weg.
  **Bekannte Einschränkung:** In Großstädten außerhalb von Berlin/Hamburg
  nennt die API den Stadtteil statt der Stadt ("30179 Nord" statt
  "30179 Hannover"). Das Feld ist im Vertragsdialog editierbar.
- **Link-Warteschlange:** Jeder Sucher darf höchstens
  `LINK_JOB_MAX_OFFEN_JE_KONTO` (20) offene Link-Abrufe haben, die Firma
  `LINK_JOB_MAX_OFFEN_JE_FIRMA` (100). Darüber kommt 429 mit klarer
  Meldung. Der Worker arbeitet die Konten **reihum** ab statt streng nach
  Alter — ein Sucher mit 500 Links blockiert die anderen nicht mehr.
  Feineinstellung: `LINK_JOB_KANDIDATEN` (200 betrachtete Konten je
  Auswahl) und `LINK_JOB_HERZSCHLAG_MAX` (900 s Höchstlaufzeit eines
  Abrufs, bevor die Selbstheilung ihn zurückstellt).
- **Termine:** Ein Sucher kann einen Abholtermin nur an ein Fahrzeug
  hängen, das ihm gehört oder zu dem er einen eigenen Kaufvertrag hat.
  Der Chef darf weiterhin alles.
- **Besucher-Adresse:** nginx setzt für `/api/` jetzt ausdrücklich
  `X-Real-IP` und `X-Forwarded-For`. Vorher reichte es eine vom Besucher
  selbst gesetzte Kopfzeile durch — die IP-Sperren waren beeinflussbar.
  **Die Vorlage wird nur beim Start des Proxy-Containers ausgewertet.**
  `deploy/rollout.sh` erkennt eine geänderte Vorlage seit Runde 26 selbst
  und erzeugt den Proxy neu; von Hand:
  `docker compose up -d --force-recreate --no-deps proxy`.

### Vertragslöschung (60 Tage) ist SCHARF (Entscheidung Ahmad, 14.09.2026)
Compose und `.env.example` stehen auf `VERTRAG_LOESCHUNG_AKTIV=true`: der
stündliche Lauf löscht Kaufverträge samt Verkäuferdaten, PDFs und
Unterschriften 60 Tage nach Erstellung endgültig — ohne Restore sind sie weg.
Kaufverträge sind Buchungsbelege: der Händler muss sie vorher in sein eigenes
Archiv übernehmen (steht so auch in der Datenschutzerklärung).
Nur-Vorschau (Trockenlauf) per `sh deploy/env_setzen.sh VERTRAG_LOESCHUNG_AKTIV=false`:
dann schreibt der Lauf eine Löschvorschau (`system_reports`, typ
`vertrag_loeschvorschau`) und löscht nichts. Prüfen mit
`python scripts/vertraege_bestand_pruefen.py` (muss Exit 0 liefern).
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
# Volume in docker-compose.yml: ./deploy/mongo-keyfile:/etc/mongo-keyfile:ro
# Und den Schalter in die .env — OHNE ihn startet mongod ohne Replica Set
# und rs.initiate() scheitert mit "not running with --replSet"
# (Nachpruefung 20.09.2026, Nr. 28 — er stand nur als Kommentar in der
# Compose-Datei, nicht in .env.example und nicht im Generator):
sh deploy/env_setzen.sh 'MONGO_EXTRA_ARGS=--replSet rs0 --keyFile /etc/mongo-keyfile'
docker compose up -d mongo
docker compose exec mongo mongosh -u "$MONGO_USER" -p "$MONGO_PASSWORD" --eval "rs.initiate()"
# Gegenprobe (muss "Replica Set 'rs0'" und einen PRIMARY zeigen):
docker compose exec backend python scripts/replikat_pruefen.py
```
Das Backup nutzt dann automatisch Snapshot-Sessions (`konsistenz: snapshot`) —
ohne jede Unterbrechung des Betriebs. `deploy/rollout.sh` prueft das Replikat
ab jetzt selbst, sobald `replicaSet=` in der `.env` steht (Nr. 29).

### Staging-Abnahme vor dem Live-Gang (Checkliste)
1. Denselben Stack (`docker compose up -d --build`) auf einem Staging-Server
   mit Kopie der Bestandsdaten starten (Mongo 8 + Auth, PUBLIC_HOST der
   Staging-Domain, echte Zertifikate).
2. `GET /api/ready` = 200, Admin → Betrieb ohne Alarme.
3. Update-Probe: neues Image bauen, `docker compose up -d`, Migration im Log,
   Rollback auf das vorherige Image.
4. Backup + `wiederherstellung_testen.py` + echter Restore auf Staging
   (Wartungsmodus sichtbar, Rollback-Test mit absichtlichem Fehler).
5. Zahlung: seit 14.09.2026 KEIN Zahlungsdienst mehr (Stripe entfernt) — Rechnung, Zahlungseingang, dann Freischaltung im Admin-Bereich. Entfaellt: Checkout, Webhook,
   Wiederholungs-Webhook, Betrieb-Seite ohne "Zahlung ohne Zugang".
6. Rollen-/Mandantentests und Lasttest (Vergleiche + Beweisdokumente + PDFs gleichzeitig).

## Zwei-Faktor-Anmeldung für Admins (TOTP)

- Jeder Admin/Super-Admin richtet sie selbst ein: **Einstellungen → Zwei-Faktor-Anmeldung → Einrichten**, Geheimnis bzw. `otpauth://`-Link in eine Authenticator-App (Google Authenticator, Aegis, 1Password …) übernehmen, Code eingeben → **8 Wiederherstellungscodes** erscheinen genau einmal — sicher ablegen.
- Danach fragt die Anmeldung nach dem Passwort zusätzlich den 6-stelligen Code (5 Minuten Zeit, 5 Fehlversuche → 15 Minuten Sperre). Ein Wiederherstellungscode gilt je einmal.
- **Neue Notfall-Codes** (Codes verbraucht, verlegt oder nie sicher abgelegt): **Einstellungen → Zwei-Faktor-Anmeldung → Neue Notfall-Codes erzeugen**, den aktuellen 6-stelligen Code aus der App eingeben (nicht den Code vom Anmelden, kein Notfall-Code). Es erscheinen 8 neue Codes — **Als Datei speichern**, zusätzlich außer Haus ablegen (USB-Stick, Passwort-Manager, Ausdruck), dann **Codes sicher abgelegt**. Die bisherigen Codes gelten sofort nicht mehr; der Eintrag in der App und die Sitzung bleiben. Kein Abschalten nötig — so entsteht kein Zeitfenster ohne zweiten Faktor. Ein vertippter Code meldet nicht mehr ab; 5 falsche Codes → 15 Minuten Sperre.
- App verloren: mit einem **Wiederherstellungscode** anmelden und neu einrichten. Es gibt bewusst nur EINEN Super-Admin — ohne Code bleibt nur der Notweg auf dem Server (unten, `mfa_pruefen.py --abschalten --ja`).
- **Ausgesperrt („Code ungültig“, obwohl er vorher passte):** erst mit einem **Wiederherstellungscode** im Code-Feld anmelden. Sonst auf dem Server prüfen, ob die Uhr der App oder ein fremdes Geheimnis schuld ist:
  `docker compose exec backend python scripts/mfa_pruefen.py --konto <SUPER_ADMIN_USERNAME> --code 123456`
  (nur lesend). Notfall ohne Wiederherstellungscode — Zwei-Faktor abschalten, dann **innerhalb von 30 Minuten** mit Benutzername + Passwort anmelden und neu einrichten (danach verlangt die Anmeldung in Produktion wieder den zweiten Faktor, Runde 14):
  `docker compose exec backend python scripts/mfa_pruefen.py --konto <SUPER_ADMIN_USERNAME> --abschalten --ja`
  Die 30 Minuten gelten auch, wenn das Konto gar keine Zwei-Faktor-Daten mehr hat (z. B. in den Einstellungen abgeschaltet und den Tab vor „Einrichten“ verloren) — `MFA_PFLICHT` muss dafür nicht geändert werden.
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
2. **Schreibpause**: `BACKUP_WARTUNG=true` in der Server-`.env` setzen
   (`sh deploy/env_setzen.sh BACKUP_WARTUNG=true`; Einzelheiten im Abschnitt
   „Wartungsmodus und Datei-Sicherung“). Der nächtliche Lauf schaltet dann für die
   Dauer der Sicherung den Wartungsmodus ein — die API antwortet solange auf **alle**
   Aufrufe mit 503, auch lesende — und danach automatisch wieder ab (mit Ablaufzeit,
   falls der Lauf abstürzt). Von Hand: `python scripts/backup_mongo.py --wartung`.
   **Grenze (Prüfbericht 20.09.2026, SK-10):** Angehalten werden nur die HTTP-Wege;
   Link-, Beweis-, Aufräum- und Abo-Worker schreiben weiter
   (`backend/backup_service.py`), die Sicherung ist also auch so nicht
   stichtagsgenau. Deshalb bleibt die Empfehlung: Replica Set (Ahmads Server
   laufen so) und `BACKUP_WARTUNG` leer.

```bash
python scripts/backup_mongo.py --wartung
```

## E-Mail-Versand über Resend

Alle Mails (Kaufverträge und die Belegkopie an den Sucher) gehen über **eine eigene Absenderadresse**, nicht über die Adresse des Händlers. Nur so bleiben die Mails zustellbar, weil nur die eigene Domain bei Resend verifiziert ist.

1. Domain in Resend anlegen und die drei DNS-Einträge (SPF, DKIM, DMARC) setzen, bis der Status „verified" ist.
2. In der `.env`:

```
RESEND_API_KEY=re_xxxxxxxxxxxx
MAIL_FROM=AutoSchnell <vertrag@deine-domain.de>
MAIL_ABSENDER_NAME=AutoSchnell
```

So sieht der Kunde die Mail: Absender **„Autohaus Muster über AutoSchnell"**, Adresse `vertrag@deine-domain.de`. Antwortet er, geht die Antwort an den **Sucher**, der den Vertrag verschickt hat (Reply-To). Der Sucher bekommt außerdem automatisch eine **Kopie mit dem PDF** als Beleg.

Ist `RESEND_API_KEY` nicht gesetzt, wird auf SMTP zurückgefallen (`SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`); Resend lässt sich auch als SMTP-Anbieter eintragen. Ohne beides meldet der Vertragsversand einen klaren Fehler statt still zu scheitern.

## WhatsApp-Versand: Teilen am Handy, Download-Link am PC

WhatsApp erlaubt keinem Programm, von der privaten Nummer eines Suchers
automatisch zu senden. Deshalb gibt es zwei Wege, beide ohne Meta-Konto
und ohne Kosten:

- **Handy:** „Per WhatsApp teilen" übergibt die digitale Vertragsfassung
  (ohne Unterschriftsfelder) über das Teilen-Menü an WhatsApp — von der
  eigenen Nummer des Suchers, PDF hängt an. Nur den Chat wählt er selbst.
  Im Archiv steht der Versand als `versand_vorbereitet` mit
  `methode: teilen`.
- **PC:** Der Chat öffnet sich wie bisher; die Nachricht enthält einen
  Download-Link `https://<FRONTEND_URL>/api/public/vertrag/<Token>` auf die
  digitale Fassung. Der Link braucht keine Anmeldung, ist standardmäßig
  14 Tage gültig (`VERTRAG_LINK_TAGE`), je IP gedrosselt (60/min) und mit
  der Löschung des Vertrags automatisch tot. Er ist an die Vertragsfassung
  gebunden, die verschickt wurde (`freigabe.version`): wird der Vertrag
  danach neu erzeugt (verschobener Abholtermin), liefert derselbe Link
  weiter die archivierte Fassung, und der nächste Versand erzeugt einen
  neuen Link. Jeder Abruf wird gezählt (`freigabe.abrufe`) und als
  `vertrag.link.abgerufen` protokolliert — das ist ein **anonymer
  Linkabruf**, kein Nachweis, dass der Verkäufer das Dokument geöffnet hat:
  wer den Link hat, kann ihn öffnen.

Voraussetzung: `FRONTEND_URL` in der `.env` muss die öffentliche
https-Adresse sein (die Produktionsprüfung verlangt das ohnehin). Der Index
`vertrag_freigabe_token` auf `generated_pdfs` wird beim Start angelegt.

Was das System belegen kann und was nicht: belegt sind Erstellung, Inhalt
und Versand des Vertrags (PDF-Fassungen mit Versionsarchiv, Versandprotokoll,
Mail-Beleg). NICHT belegt ist die Zustimmung des Verkäufers — es gibt keinen
Verkäufer-Login, keinen Bestätigungslink und keine Signatur. Das ist so
gewollt (Entscheidung Ahmad 09.09.2026): Der Verkäufer stimmt außerhalb des
Systems zu, per Antwort auf die E-Mail direkt an den Sucher. Der Text der
digitalen Ausfertigung ist eine Vertragsbedingung des Händlers, keine vom
System nachgewiesene Tatsache.

Seit 10.09.2026 stehen die Klauseln (Standard: vier Sätze, oder der Text aus
Einstellungen → „Allgemeine Vertragsbedingungen") in JEDER Fassung als eigener
Abschnitt „Allgemeine Vertragsbedingungen". Die digitale Fassung (E-Mail/
WhatsApp) hat keine Unterschriftsfelder; unter „Unterschriften" steht nur:
„Dieser Vertrag ist ohne Unterschrift gültig."

Texte aus den Einstellungen gelten nur für NEUE Verträge. Ein bereits
erstellter Vertrag behält den Text vom Zeitpunkt seiner Erstellung — auch
bei Terminverschiebung (Neuerzeugung) und beim späteren Nacherzeugen der
digitalen Fassung. Verträge von vor der Funktion bekommen nie nachträglich
Vertragsbedingungen, ihre digitale Fassung ist als „nachträglich erzeugt"
gekennzeichnet.

## Inseratsfotos über den eigenen Bild-Proxy

Seit 10.09.2026 lädt der Browser Portal-Fotos (Kleinanzeigen, mobile.de,
AutoScout24) nicht mehr direkt vom fremden CDN, sondern als kleines JPEG
(max. 640 px) über `GET /api/bild?u=…&exp=…&sig=…`. Der Server holt das
Bild einmal, verkleinert es und hält es im Speicher (`BILD_PROXY_CACHE`,
Standard 400 Bilder). Kein offener Proxy: nur https-Adressen der bekannten
Portal-Hosts (Allowliste, erweiterbar über `BILD_PROXY_HOSTS`, kommagetrennt),
jede Adresse trägt eine Signatur mit Ablauf, je IP `BILD_PROXY_LIMIT` (Standard 3000)
Bilder/Minute.
Vorschaubilder stehen in den Antworten als `images_thumbs`
(Vergleich), `einkauf_thumbs` (Inserat), `vehicle_image_urls_thumbs`
(Vertragsliste) und in den öffentlichen Marktplatz-Fotos. Große Ansichten
und Links zeigen weiter das Originalfoto.

Schadensskizzen für Abholauftrag und Protokoll liegen jetzt unter
`backend/assets/damage/` (vorher nur im Frontend-Ordner, den das
Backend-Image nicht enthält — in Produktion fehlten die Skizzen deshalb).

## Fahrerfotos (Abweichungsfotos aus dem Abhol-Check)

Seit 10.09.2026 (Runde 21):

- **Frist:** 60 Tage nach dem Hochladen des Berichts (`FAHRERFOTO_TAGE`,
  Standard 60, also so lange wie der Kaufvertrag). Unabhängig vom
  Terminstatus: gelöschte, stornierte und wiedergeöffnete Termine sind
  damit abgedeckt. Gelöscht wird nur das Bild; der Berichtstext bleibt.
- **Wer sieht sie:** der Chef alle, ein Sucher nur zu Terminen in seinem
  Bereich, der Fahrer nur seine eigenen und nur, solange der Termin ihm
  zugeteilt ist. Keine öffentliche Adresse (Präfix `pickup/` ist privat).
- **Wo:** Terminplaner → Knopf „Abholbericht“ am Termin, Fahrzeugakte →
  Abschnitt „Abholung“ (Vorschaubilder), Bestand → „Fahrzeugakte ·
  Abholbericht“. Im Verkaufsinserat: „Fotos vom Fahrer übernehmen“ legt
  eine eigene Kopie unter `resale/` an, die im Inserat bleibt.
- **Metadaten:** Die Fahrer-App verkleinert Fotos vor dem Hochladen
  (max. 2000 px); der Server speichert Bilder mit EXIF/GPS immer neu,
  ohne diese Daten.

## Fahrzeuge verkaufen ist kostenlos

`VERKAUF_KOSTENLOS=true` (Standard) bedeutet: Jede Firma kann unbegrenzt viele Fahrzeuge veröffentlichen, ohne Paket und ohne Monatskontingent. Die Paketverwaltung bleibt im Code erhalten; mit `VERKAUF_KOSTENLOS=false` gelten wieder Pakete und Kontingente wie zuvor.

## Alte Sicherungskopien nach einem Restore

Jeder Restore legt den bisherigen Stand vollständig zur Seite: die Datenbank als `<db>__vorher_<zeitpunkt>`, die Datei-Ordner als `<ordner>.vorher-<zeitpunkt>`. Das ist das Sicherheitsnetz, falls die Wiederherstellung doch nicht passt — es sind aber vollständige Kopien mit Kundendaten, Verträgen und Fotos.

`restore_mongo.py` räumt sie deshalb nach einem erfolgreichen Lauf selbst auf: Kopien älter als 30 Tage werden gelöscht, die jüngste bleibt immer erhalten. Anpassen mit `--vorher-aufbewahrung TAGE`, `0` schaltet das Aufräumen ab.

```bash
python scripts/restore_mongo.py /backups/2026-09-04 --yes                       # 30 Tage (Standard)
python scripts/restore_mongo.py /backups/2026-09-04 --yes --vorher-aufbewahrung 7
```

## Inbetriebnahme bei Hetzner (Load Balancer, privates Netz)

Empfohlener Weg: **erst ein Server, dann der zweite.** Der Load Balancer bleibt davor, der zweite Server kommt dazu, sobald der Objektspeicher steht. Grund: Hochgeladene Fotos und PDFs liegen sonst auf der Platte des Servers, der sie angenommen hat, und der zweite Server sieht sie nicht.

### Aufstellung Stufe 1

- **Server 1** (2.28.66.8) bedient die Domain direkt. nginx stellt das Zertifikat
  selbst aus (Let's Encrypt) und erneuert es automatisch.
- **Datenbank** läuft im selben Paket, ohne Port nach außen.
- **Fotos und PDFs** liegen in Cloudflare R2.
- **Cloudflare** macht nur die Namensauflösung (graue Wolke).
- Der **Load Balancer** bleibt vorerst ungenutzt. Er kommt mit dem zweiten
  Server dazu (Stufe 2).

Warum nicht gleich über den Load Balancer? Dessen verwaltetes Zertifikat wird
über einen DNS-Eintrag geprüft und setzt voraus, dass die Domain bei Hetzner
DNS liegt. Solange die Domain bei Cloudflare liegt, bräuchte es dafür eine
zusätzliche Delegation. Für den Start ist der direkte Weg schneller und hat
weniger Teile, die schiefgehen können.

### Schritt 1 — Namensauflösung

Cloudflare: Eintrag Typ **A**, Name **app**, Ziel **2.28.66.8** (Server 1),
Proxy **aus** (graue Wolke). Die graue Wolke ist nötig, weil der Server das
Zertifikat selbst holt und dafür direkt erreichbar sein muss.

### Schritt 2 — Firewall

In der Hetzner Console unter **Firewalls → auto-spider-production-firewall →
Rules** müssen eingehend genau diese drei Regeln stehen:

| Protokoll | Port | Quelle | Zweck |
|---|---|---|---|
| TCP | 22 | deine eigene IP (oder `0.0.0.0/0`, wenn wechselnd) | Wartung |
| TCP | 80 | `0.0.0.0/0` und `::/0` | Zertifikat und Umleitung |
| TCP | 443 | `0.0.0.0/0` und `::/0` | die Anwendung |

Port 27017 bleibt zu. Ausgehend kann alles offen bleiben.

### Schritt 3 — Zertifikat holen

Erst wenn die Namensauflösung greift (`ping app.auto-schnellkauf.de` zeigt
2.28.66.8), auf dem Server:

```bash
cd /opt/autoschnell
docker run --rm -p 80:80 -v "$PWD/deploy/certs:/etc/letsencrypt" \
  certbot/certbot certonly --standalone --agree-tos --no-eff-email \
  -m DEINE-MAIL@auto-schnellkauf.de -d app.auto-schnellkauf.de
cp deploy/certs/live/app.auto-schnellkauf.de/fullchain.pem deploy/certs/fullchain.pem
cp deploy/certs/live/app.auto-schnellkauf.de/privkey.pem  deploy/certs/privkey.pem
```

Erneuerung einmal einrichten (Zertifikate laufen nach 90 Tagen ab). Das mitgelieferte Skript stoppt den Proxy nur kurz und startet ihn **in jedem Fall** wieder — auch wenn die Erneuerung scheitert:

```bash
chmod +x /opt/autoschnell/deploy/zertifikat-erneuern.sh
echo '0 4 * * 1 root DOMAIN=app.auto-schnellkauf.de /opt/autoschnell/deploy/zertifikat-erneuern.sh >> /var/log/autoschnell-zertifikat.log 2>&1' > /etc/cron.d/autoschnell-zertifikat
```

Einmal gefahrlos ausprobieren (ändert nichts):

```bash
DOMAIN=app.auto-schnellkauf.de PROBE=1 /opt/autoschnell/deploy/zertifikat-erneuern.sh
```

Bitte **keine** lange Befehlskette mit `&&` in den cron schreiben: Schlägt die Erneuerung mittendrin fehl, bleibt der Proxy gestoppt und die Seite ist dauerhaft offline.

### Schritt 4 — Server 1 vorbereiten

```bash
ssh root@2.28.66.8

# Docker aus der offiziellen Quelle. Das Ubuntu-Paket "docker.io" bringt KEIN
# "docker compose" mit, und "docker-compose-plugin" gibt es in Ubuntus eigenen
# Quellen nicht — die Installation braechte sonst ab.
apt update && apt install -y ca-certificates curl gnupg git openssl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  > /etc/apt/sources.list.d/docker.list
apt update && apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
docker compose version        # muss eine Version anzeigen

git clone https://github.com/Ahmad271223/autoschnell102.git /opt/autoschnell
cd /opt/autoschnell && git checkout feature/plattform-ausbau-2026-08
```

Die Prüfskripte laufen im Container mit, dort sind alle Bibliotheken vorhanden. Auf dem Server selbst muss dafür nichts installiert werden:

```bash
docker compose run --rm backend python scripts/verbindung_pruefen.py
docker compose run --rm backend python scripts/betriebsprobe.py app.auto-schnellkauf.de
```

### Schritt 5 — Konfiguration und Schlüsseldatei

`.env` anlegen (Inhalt bekommst du fertig) und schützen, dann die Schlüsseldatei für das Replica Set:

```bash
nano .env            # Inhalt einfügen, speichern
chmod 600 .env
openssl rand -base64 756 > deploy/mongo-keyfile
chmod 400 deploy/mongo-keyfile
chown 999:999 deploy/mongo-keyfile
```

Die Schlüsseldatei muss **vor** dem ersten Start existieren und dem Benutzer 999 gehören, sonst startet die Datenbank nicht („permissions are too open").

### Schritt 6 — Starten und Replica Set einschalten

Die Reihenfolge ist wichtig: **zuerst nur die Datenbank**, dann das Replica Set, dann der Rest. Startet alles gleichzeitig, sucht die Anwendung ein Replica Set, das es noch nicht gibt, und läuft in eine Neustartschleife.

```bash
docker compose up -d mongo
sleep 25
```

Einmalig das Replica Set einrichten (sorgt für in sich stimmige Sicherungen):

```bash
docker compose exec -T mongo mongosh --quiet   -u "$(grep ^MONGO_USER .env | cut -d= -f2)"   -p "$(grep ^MONGO_PASSWORD .env | cut -d= -f2)"   --authenticationDatabase admin   --eval 'rs.initiate({_id:"rs0",members:[{_id:0,host:"mongo:27017"}]})'
```

Erst jetzt der Rest:

```bash
docker compose up -d --build
docker compose logs -f backend      # mit Strg+C beenden, sobald "Uvicorn running" steht
```

Der Name `mongo` ist Absicht. Eine Server-IP funktioniert an dieser Stelle nicht, weil der Container sie nicht als eigene Adresse erkennt.

### Schritt 7 — Prüfen

```bash
docker compose exec backend python scripts/verbindung_pruefen.py
curl -sk -H "Host: app.auto-schnellkauf.de" https://localhost/api/health
docker compose run --rm backend python scripts/betriebsprobe.py app.auto-schnellkauf.de --dkim-selector resend
```

Der Host-Kopf ist nötig, weil der Webserver nur die eingetragene Domain bedient; `-k` überspringt die Zertifikatsprüfung, weil `localhost` nicht im Zertifikat steht.

Danach zeigt `https://app.auto-schnellkauf.de` die Anmeldung. Erste Anmeldung mit `SUPER_ADMIN_USERNAME` und `SUPER_ADMIN_PASSWORD` aus der `.env`, danach **sofort** die Zwei-Faktor-Anmeldung einrichten (Einstellungen → Zwei-Faktor).

**Wichtig (Rollenprüfung 22.09.2026, RP-543):** In Produktion verlangt die Anmeldung für den Betreiber den zweiten Faktor — den kann man aber erst *nach* der Anmeldung einrichten. Deshalb bekommt das beim allerersten Start **neu angelegte** Betreiberkonto eine Gnadenfrist von **60 Minuten** (`SEED_MFA_FRIST_MIN`), in der die Anmeldung mit Benutzername + Passwort reicht. Ist die Frist verstrichen, bevor der zweite Faktor eingerichtet war, einmal:

```bash
docker compose exec backend python scripts/mfa_pruefen.py --konto <SUPER_ADMIN_USERNAME> --abschalten --ja
```

Das setzt 30 Minuten Gnadenfrist — dann anmelden und die Zwei-Faktor-Anmeldung einrichten. Passwort vergessen: siehe „Notfall: Betreiber-Passwort vergessen“ weiter unten.

Konten gibt es nur über den Super-Admin (Kontonummer, 13.09.2026): Er legt Firma mit Chef, Sucher, Zwischenhändler und Fahrer an. **Kontonummer und Passwort vergibt der Betreiber** und teilt sie den Kunden mit — Chef z. B. `10023`, Sucher `10023-2`, Fahrer seit 14.09.2026 ihre **Fahrer-ID** wie `FD-7K2M9QX4` (zugleich der Code, mit dem die Firma den Fahrer verknüpft; ältere Fahrerkonten mit reiner Nummer gelten weiter), Zwischenhändler seit 14.09.2026 einen **Käufer-Code** wie `6FE7K2M` (7 Zeichen, Buchstaben und Ziffern ohne I/O/0/1, Groß-/Kleinschreibung und Trenner egal; ältere numerische Käufernummern gelten weiter). Passwörter: mindestens 10 Zeichen mit Ziffer oder Sonderzeichen, bis 72 Zeichen — die Admin-Formulare bieten „Sicheres Passwort mit 20 Zeichen vorschlagen“ und zeigen das Passwort nach dem Anlegen einmalig neben der Kontonummer. Eine Selbstregistrierung gibt es nicht; ein vergessenes Passwort setzt der Betreiber neu (Admin → Passwort setzen). Konten aus der Zeit vor dem 14.09.2026, die als Zwischenhändler oder Fahrer noch eine reine Nummer tragen, listet und löscht `docker compose exec backend python scripts/alte_kontonummern_loeschen.py` (ohne `--ausfuehren` nur Probelauf; Firmen und Sucher werden nie angefasst).

### Stufe 2 — zweiter Server und Load Balancer (später)

1. **Zertifikat auf den Load Balancer verlagern.** Damit Hetzner ein
   verwaltetes Zertifikat ausstellen kann, in Cloudflare drei NS-Einträge für
   `_acme-challenge.app` auf die Hetzner-Nameserver setzen und in der Hetzner
   DNS Console die passende Zone anlegen. Danach im Load Balancer den Dienst
   **HTTPS 443 → HTTP 80** mit verwaltetem Zertifikat anlegen, dazu
   **HTTP 80 → HTTP 80**, Gesundheitsprüfung HTTP Port 80 Pfad `/api/health`.
2. In der `.env` umstellen auf `PROXY_TEMPLATE=hinter-loadbalancer.conf.template`
   und `TRUSTED_PROXIES=127.0.0.1,172.16.0.0/12,10.0.0.0/16`, dann `docker compose up -d`.
3. DNS-Eintrag `app` von der Server-Adresse auf die des Load Balancers ändern.
4. Firewall umstellen: Port 80 und 443 nur noch aus `10.0.0.0/16`.
5. **Datenbank für Server 2 erreichbar machen:** Mongo mit `network_mode: host`
   an `10.0.0.2` binden, Firewall 27017 nur aus `10.0.0.0/16`, das
   Replica-Set-Mitglied auf `10.0.0.2:27017` umstellen.
6. Auf Server 2 dieselbe `.env` ablegen, `MONGO_URL` auf `10.0.0.2` zeigen
   lassen, dann `docker compose up -d --build backend web proxy`.
7. Server 2 im Load Balancer als zweites Ziel eintragen.

### Wenn es klemmt

| Symptom | Ursache | Abhilfe |
|---|---|---|
| Datenbank startet nicht, „permissions are too open" | Schlüsseldatei falsch | `chmod 400` und `chown 999:999 deploy/mongo-keyfile` |
| Datenbank startet nicht, Meldung mit `Linux kernel versions 6.19 and newer` | MongoDB 8.0 laeuft nicht auf neuen Kernen; Ubuntu 26.04 bringt Kernel 7.0 mit | Ist bereits auf `mongo:8.2` umgestellt. Kontrolle: `grep 'image: mongo' docker-compose.yml` |
| Load Balancer bleibt „Unhealthy" | Prüfpfad falsch oder Backend startet nicht | HTTP, Port 80, Pfad `/api/health`. Die Prüfung antwortet auch, wenn der Load Balancer mit der Server-IP statt der Domain anfragt; sie kommt aber vom Backend, „healthy" heißt also wirklich lauffähig. |
| Endlose Weiterleitung im Browser | falsche Betriebsart | `PROXY_TEMPLATE=hinter-loadbalancer.conf.template` |
| Alle Nutzer gleichzeitig ausgesperrt | Besucheradresse kommt nicht an | `TRUSTED_PROXIES=127.0.0.1,172.16.0.0/12,10.0.0.4/32` und `PRIVATES_NETZ=10.0.0.4/32` (LB-Adresse) |
| Nach `git pull` wirken Änderungen nicht | Abbilder wurden nicht neu gebaut | Immer `docker compose up -d --build` — `up -d` allein startet nur die ALTEN Abbilder neu |
| Backend startet nicht | Produktionsprüfung meckert | die Meldung im Log nennt genau den fehlenden Wert |
| „rs.initiate" meldet „maps to this node" | Server-IP statt `mongo` verwendet | mit `host:"mongo:27017"` wiederholen |

## Datei-Speicher mit Cloudflare R2

R2 ist S3-kompatibel, weicht aber in zwei Punkten von AWS ab. Beides ist im Code berücksichtigt und wird an der Adresse automatisch erkannt:

- **Prüfsummen:** Neuere boto3-Fassungen schicken bei jedem Hochladen zusätzliche Prüfsummen mit, die R2 ablehnt. Für R2-Adressen werden sie auf „nur wenn nötig" gestellt.
- **Verschlüsselung:** `ServerSideEncryption: AES256` weist R2 zurück, weil es ohnehin selbst verschlüsselt. Die Kopfzeile entfällt für R2.

Nötig sind in der `.env`:

```
S3_ENDPOINT=https://<konto-id>.r2.cloudflarestorage.com
S3_BUCKET=autoschnell-dateien
S3_ACCESS_KEY=<R2 Access Key ID>
S3_SECRET_KEY=<R2 Secret Access Key>
S3_REGION=auto
```

Die Zugangsdaten entstehen in Cloudflare unter **R2 → Manage API Tokens → Create API Token**, Berechtigung **Object Read & Write**, begrenzt auf den einen Bucket. Die Konto-Kennung steht in der R2-Übersicht.

Für die Sicherungen einen **zweiten** Bucket anlegen und `BACKUP_S3_BUCKET` setzen. Getrennte Buckets, damit ein Fehler in der Anwendung die Sicherungen nicht mitreißt.

Wenn ein anderer Anbieter zickt, lassen sich beide Eigenheiten von Hand steuern: `S3_SSE=auto|aes256|aus` und `S3_PRUEFSUMMEN=auto|immer|nur_noetig`.

## Betrieb seit Phase 3 (15.09.2026)

- `/api/health` antwortet **503** (Load Balancer nimmt die Instanz aus der Rotation), sobald
  Migrationen ausstehen, ein kritischer eindeutiger Index fehlt (`vehicles(dealer_id,id)`,
  `kaufvorgaenge(contract_id)`) oder beim Start ein eindeutiger Index nicht angelegt werden
  konnte. Ein S3-Ausfall bleibt eine Warnung in `/api/ready`.
- In Produktion **startet das Backend nicht**, wenn ein eindeutiger Index wegen Dubletten
  nicht angelegt werden kann (Log: „Start ABGEBROCHEN: eindeutige Indizes fehlen“).
  Bereinigen mit `python scripts/dubletten_pruefen.py`, danach erneut starten.
- Vermittler-Netze: in der Kopfzeilen-Kette zählen die `TRUSTED_PROXIES` **plus** die privaten
  Netze (10.x, 172.16.x, 192.168.x) als eigene Vermittler; der Compose-Standard enthält
  `10.0.0.0/8` für den Hetzner-Load-Balancer. Mit `TRUSTED_PROXIES_NUR_LISTE=true` muss ein
  10.x-Netz in der Liste stehen, sonst bricht die Produktionsprüfung ab.
- Hintergrund-Sperren tragen ein Besitzer-Token und werden per Heartbeat verlängert; ein
  fehlgeschlagenes Backup gibt seine Tagessperre frei und wird nach einer Stunde erneut
  versucht (bis dreimal).
- Gelöschte Firmen bekommen einen Grabstein (`firmen_geloescht`); der Aufräumjob entfernt
  30 Tage lang Reste aus allen Firmen-Sammlungen.

### Anmeldung und Konten seit Runde 14 (15.09.2026)

- **Super-Admin ohne Zwei-Faktor kommt in Produktion nicht mehr herein** (`MFA_PFLICHT`, Standard
  `true` — dieselbe Regel wie `/api/ready`). Vor dem Rollout prüfen: Zwei-Faktor ist eingerichtet
  (Bereich **Betrieb** bzw. `/api/ready` zeigt „Super-Admin-Konto ohne Zwei-Faktor“). Nach dem
  Notfall-Abschalten mit `scripts/mfa_pruefen.py --abschalten --ja` gilt eine **Gnadenfrist von
  30 Minuten**, um sich mit Passwort anzumelden und den zweiten Faktor neu einzurichten.
- Eine Sitzung entsteht nur, wenn das Konto beim Schreiben noch genau so dasteht wie geprüft
  (Passwort, Zwei-Faktor, aktiv, keine Löschung); Logout beendet nur die eigene Sitzung;
  Entsperren verwirft eine während der Sperre entstandene Sitzung.
- Passwörter: mindestens ein Buchstabe **und** eine Ziffer oder ein Sonderzeichen (rein
  numerische Passwörter sind ungültig), keine eigenen Kontodaten (Kontonummer, Name, E-Mail,
  Firma, Fahrer-ID). Gesetzt werden sie nur über `POST /admin/users/{id}/password`,
  `POST /admin/drivers/{id}/password` (fremde Konten) und `/admin/me/password` (eigenes Konto,
  mit aktuellem Passwort); `PUT /admin/users/{id}` nimmt kein Passwort mehr an.
  Bleibt die Anmeldesperre nach „Passwort setzen“ unklar, zeigt die Antwort einen Hinweis und
  der Alarm `konto_sperre_nicht_aufgehoben` bleibt offen (`scripts/anmeldesperre_aufheben.py`).
- Zugangs-Anfragen: je E-Mail und Kontoart höchstens eine offene Anfrage. Beim Anlegen aus einer
  Anfrage müssen E-Mail und Firma zur Anfrage passen, sonst 409 — bewusst abweichen mit
  `daten_geaendert=true` (die Oberfläche fragt nach). Neue Teil-Unique-Indizes
  (`zugangsanfrage_eindeutig`, `uniq_offene_zugang_anfrage`) werden „weich“ angelegt: Dubletten
  in Altdaten brechen den Start nicht ab, sondern erzeugen den Alarm `unique_index_fehlt_weich`.
- Migration 8 (`konten_aktiv_feld`) schreibt das Feld `active` in jedes Konto (users ohne Feld →
  gesperrt, Fahrer ohne Feld → aktiv — die bisherige Bedeutung, nur ausdrücklich).
- Fahrer-App: vor der Annahme einer Fahrt nur PLZ und Ort, keine Verkäuferdaten; Sucher sehen in
  der Fahrerliste keine Fahrer-ID und keine E-Mail. Erstbericht-Reservierungen verfallen nach
  10 Minuten (Prozessabsturz); Aufräumjob holt Fahrer-Pseudonyme in Berichten nach und löst
  Termine, deren Vertrag gelöscht wurde.

### Zwei-Faktor, Betreiberkonto und Anmeldesperren seit Runde 15 (15.09.2026)

- **Zwei-Faktor wird nicht aus einer laufenden Sitzung ersetzt.** Gerätewechsel: unter
  Einstellungen mit dem aktuellen Code **abschalten**, dann neu einrichten. Eine begonnene
  Einrichtung verfällt nach einer Stunde. Seit der Rollenprüfung 22.09.2026 (RP-556) gilt nach
  dem Abschalten eine **Gnadenfrist von 30 Minuten** (`mfa.pflicht_ausgesetzt_bis`); die
  Einstellungen öffnen die Einrichtung sofort und zeigen die Frist an. **Abschalten und neu
  Einrichten in einem Zug erledigen:** Wer sich nach Ablauf der Frist ohne neuen zweiten Faktor
  abmeldet, kommt in Produktion nur noch über den Notweg auf dem Server herein
  (`docker compose exec backend python scripts/mfa_pruefen.py --konto <SUPER_ADMIN_USERNAME> --abschalten --ja`,
  setzt erneut 30 Minuten). Die Aktivierung beendet die bisherige Sitzung ohne
  zweiten Faktor (andere Geräte müssen sich neu anmelden, jetzt mit Code); der Tab, der aktiviert
  hat, läuft mit neuem Token weiter.
- **`DATEN_SCHLUESSEL`** (optional, `openssl rand -hex 32`): eigener Schlüssel für die Ablage der
  Zwei-Faktor-Geheimnisse und der bekannten Anmelde-IPs. Ohne ihn gilt wie bisher `JWT_SECRET`;
  nach dem Setzen bleiben vorhandene Geheimnisse lesbar (beide Schlüssel werden probiert), und
  eine spätere `JWT_SECRET`-Rotation macht MFA-Daten nicht mehr unlesbar. Auf **beiden** Servern
  gleich setzen.
- **Betreiberkonto (Seed):** `SUPER_ADMIN_PASSWORD` muss die allgemeine Passwortregel erfüllen
  (Produktionsprüfung und Seed). Ein vorhandenes Konto mit demselben Benutzernamen, das kein
  Super-Admin ist, wird **nicht** hochgestuft (Alarm `super_admin_seed_konflikt`, Produktion
  startet nicht). Ein geänderter `SUPER_ADMIN_USERNAME` legt **kein zweites** Betreiberkonto an
  (Alarm `super_admin_doppelt`, Produktion startet nicht) — das bestehende Konto umbenennen oder
  die `.env` zurücksetzen. `/api/ready` meldet mehr als ein aktives Super-Admin-Konto als Fehler.
  Ein von der Datenbank abweichendes `SUPER_ADMIN_PASSWORD` in der `.env` ändert das Passwort
  **nicht** (Alarm `super_admin_passwort_env_abweichend`): Passwort nur über Einstellungen ändern.
- **Anmeldesperren:** die Kontosperre (30 Fehlversuche je 15 Minuten) zählt gleitend über das
  aktuelle und das vorige Fenster; die Anmelde-Limiter sind fail-closed — ist der gemeinsame
  Zähler in MongoDB nicht erreichbar, gilt „gesperrt“ statt eines Zählers je Prozess.
- Das Betreiber-Token liegt nur noch im Tab (sessionStorage), nicht in localStorage: nach einem
  Browser-Neustart meldet sich der Betreiber neu an (mit zweitem Faktor).
- Zwischenhändler, der zurück zum Sucher wird, bekommt eine Sucher-Nummer seiner Firma
  (`<Kundennummer>-<Zusatz>`); der alte Käufer-Code steht in `kontonummer_vorher`.
- 422-Antworten spiegeln Passwörter und Codes nicht mehr zurück (`***`).

### Go-Live-Schalter: Marktplatz und Inserieren (15.09.2026)

> **STAND 20.09.2026: Der Marktplatz ist WIEDER AUF.** Der Compose-Standard steht auf
> `true`. Der Schalter bleibt vollständig erhalten — `MARKTPLATZ_AKTIV=false` in der
> Server-`.env` plus Rollout schließt alles wieder, ohne Code anzufassen.
> **Achtung:** Eine ausdrückliche Zeile in der Server-`.env` gewinnt über den
> Compose-Standard. Stand dort beim Abschalten am 15.09. ein `MARKTPLATZ_AKTIV=false`,
> muss es zum Öffnen entfernt oder auf `true` gesetzt werden:
> `sh deploy/env_setzen.sh MARKTPLATZ_AKTIV=true`
>
> Der Abschnitt unten beschreibt den Schalter, wie er am 15.09.2026 gebaut wurde.

- **Ursprünglich: Standard aus.** `MARKTPLATZ_AKTIV` schaltet den
  B2B-Marktplatz (Zwischenhändler-Anmeldung, Marktplatz-Seiten, Kaufanfragen, Einladungen,
  Verkaufspaket) und das Inserieren („Jetzt inserieren“, „Weiterverkaufen“, Inserats-Editor) ab.
  Die Routen antworten mit **503 „Demnächst verfügbar“**, die Oberfläche blendet Menüpunkte,
  Buttons und Reiter aus und zeigt auf den Seiten „Demnächst verfügbar“. Der Code bleibt
  vollständig erhalten.
- Freischalten: `MARKTPLATZ_AKTIV=true` in die Server-`.env`, Rollout. `GET /api/features`
  zeigt den Stand (`{"marktplatz": false}`).
- Tests und CI laufen mit `MARKTPLATZ_AKTIV=true`, damit die Marktplatz-Tests weiter greifen.

### Regeln fürs Inserieren, wenn der Marktplatz wieder anspringt (20.09.2026)

Entscheidung Ahmad vom 20.09.2026. Alle vier Regeln sind über die Umgebung verstellbar,
die Standardwerte sind die vereinbarten:

| Regel | Einstellung | Standard |
|---|---|---|
| Beschreibung höchstens 500 Zeichen | `INSERAT_BESCHREIBUNG_MAX` | `500` (100–30000) |
| Höchstens 10 Fotos je Inserat | `INSERAT_FOTOS_MAX` | `10` (1–40) |
| Inserat läuft nach 3 Wochen ab | `INSERAT_LAUFZEIT_TAGE` | `21` (1–365) |
| Fotos müssen neu sein | fest verdrahtet | — |

**Fahrzeugdaten** werden aus dem Einkauf übernommen (Marke, Modell, Erstzulassung, Kilometer,
Leistung, Getriebe, Kraftstoff …) und dürfen im Inserats-Editor **geändert** werden — der Einkauf
selbst bleibt unangetastet.

**Fotos werden NICHT übernommen.** Ein neues Inserat startet mit
`photos: {"mode": "neu", "einkauf_urls": [], "uploaded_keys": []}`. Bilder aus dem Portal-Inserat
(mobile.de, AutoScout24, Kleinanzeigen) gehören dem jeweiligen Verkäufer, nicht uns. Wer
veröffentlichen will, muss eigene Fotos hochladen: `POST /resale/{id}/publish` weist ein Inserat
ohne eigenes Bild mit **400** ab.

**Nach 3 Wochen verschwindet nur die Anzeige.** `cleanup_service.abgelaufene_inserate_entfernen()`
läuft im `marktplatz_rotieren`-Durchgang, nimmt ausschließlich Inserate im Status
`veroeffentlicht`, deren `published_at` älter als `INSERAT_LAUFZEIT_TAGE` ist, löscht sie samt
hochgeladener Fotos aus dem Speicher und schreibt den Grund `inserat_laufzeit_abgelaufen` ins
Protokoll. **Nicht angefasst werden:** Fahrzeug, Kaufvertrag, Abholprotokoll, Beweisdokument und
alles andere aus dem Einkauf. Wer das Auto gekauft hat, behält seine Unterlagen unverändert.

**Speicher:** 10 Fotos à rund 400 KB sind etwa 4 MB je Inserat. Bei 3 Wochen Laufzeit stehen
selbst bei 2.000 Inseraten im Monat nie mehr als rund 5,6 GB gleichzeitig im R2-Bucket — im
Rahmen der heutigen Server. Vor der Regel lief das unbegrenzt mit.

**Großer Rollentest:** `backend/scripts/rollentest_gross.py` fährt alle fünf Rollen (Chef,
zweites Chef-Konto, Sucher, Zwischenhändler, Fahrer) einmal komplett durch und prüft bei jedem
Schritt beides — was die Rolle darf *und* was sie nicht darf. 62 Prüfungen, Stand 20.09.2026 alle
grün. Aufruf bei laufendem Backend:

```
DB_NAME=... MARKTPLATZ_AKTIV=true TEST_BASE_URL=http://127.0.0.1:8002   python -X utf8 scripts/rollentest_gross.py
```

Testkonten werden am Ende wieder entfernt (`--behalten` lässt sie stehen). Exit 0 = alles wie
erwartet.

### Cloudflare schneidet lange Anfragen vor nginx ab (20.09.2026)

nginx laesst in beiden Produktionsvorlagen `proxy_read_timeout 300s` zu, und Teile der
Anwendung rechnen mit bis zu 180 s. **Cloudflare bricht aber frueher ab** — der
dokumentierte Standard liegt bei rund 100 Sekunden (Fehler **524**). Was laenger dauert,
sieht der Nutzer als Cloudflare-Fehlerseite, waehrend nginx und das Backend noch arbeiten.

**Was daraus folgt:** Kein Weg, auf den ein Nutzer wartet, darf synchron laenger als etwa
90 Sekunden laufen. Lange Arbeit gehoert in einen Auftrag mit Statusabfrage — so laeuft
das Einlesen neuer Links bereits (`link_jobs`, das Frontend fragt nach). Beim Bauen neuer
Funktionen ist das die Grenze, an der man sich orientiert, nicht die 300 s aus nginx.

**Betroffen sind heute:** Vertrags-PDF mit vielen Fotos und der Protokoll-Abschluss. Beide
liegen normal weit darunter; der gemeinsame Apify-Topf (siehe unten) hat den langsamsten
gemessenen Fall von 143 s auf 83 s gedrueckt und damit zusaetzlich Luft geschaffen.

### Gemeinsamer Apify-Topf und echtes Auslaufen der Schreibpause (20.09.2026, Nachpruefung)

**Gemeinsamer Topf statt zwei fester Grenzen.** mobile.de und AutoScout24 laufen beide
ueber Apify. Zwei feste Obergrenzen hatten zwei Nachteile zugleich: zusammen durften sie
mehr, als der Plan erlaubt (429), und keine konnte die Plaetze der anderen nutzen, wenn
die gerade ruhte. Gemessen mit `backend/scripts/lasttest_apify_grenze.py --eine-quelle`
(100 mobile.de-Links, je 20 s Apify-Zeit):

| Einstellung | Hoechststand | letzter Sucher |
|---|---|---|
| 16 + 16 fest | 16 | **143 s** — das Frontend gibt nach 120 s auf |
| gemeinsamer Topf 32 | 32 | **83 s** |

Jeder Apify-Abruf belegt jetzt einen Platz im Topf `apify` **und** einen seiner Quelle.
Zusammen nie mehr als `APIFY_MAX_PARALLEL`, einzeln bis zum vollen Plan. Standard:
`MAX_CONCURRENT_MOBILE=32`, `MAX_CONCURRENT_AUTOSCOUT=32`, `APIFY_MAX_PARALLEL=32`. Der
Start **warnt**, wenn eine Quelle weniger darf als der Plan — dann liegen Plaetze brach.
`LINK_JOB_MAX_OFFEN_JE_FIRMA` steht jetzt auf 200 (vorher exakt die Zielgroesse 100).

**Echtes Auslaufen statt blindem Schlafen.** Die Schreibpause (`BACKUP_WARTUNG=true`,
nur ohne Replica Set noetig) wartete nach dem Einschalten stur `BACKUP_WARTUNG_WARTEN_S`
Sekunden und begann dann den Dump — ohne zu wissen, ob noch jemand schreibt. Jetzt meldet
**jeder Backend-Prozess** waehrend einer Pause einmal je Sekunde seine offenen
Schreibzugriffe nach `wartung_schreiber`; die Sicherung wartet, bis alle null melden
(hoechstens `BACKUP_AUSLAUFEN_MAX_S`, Standard 120 s).

**Wichtig:** Die Zusage „stichtagsgenau" faellt jetzt **nur** bei bestaetigtem Auslaufen.
Meldet niemand (alte Fassung ohne Melder) oder laeuft die Frist ab, wird trotzdem
gesichert — aber im Log und im Manifest steht, dass die Sicherung nicht stichtagsgenau
ist. Die Melde-Sammlung ist vom Dump ausgenommen und wird im Normalbetrieb gar nicht
beschrieben.

### Apify-Grenze: mobile.de und AutoScout24 teilen sich einen Plan (20.09.2026)

Frage Ahmads: Kleinanzeigen läuft über einen eigenen bezahlten Dienst
(kleinanzeigen-agent.de, eigenes Limit) — **mobile.de und AutoScout24 aber beide über
Apify**. Jede Quelle hatte ihre eigene Obergrenze, eine gemeinsame gab es nicht.

Gemessen mit `backend/scripts/lasttest_apify_grenze.py` (Apify nachgestellt, echter
Job-Weg, echte Zähler in der Datenbank, Verbund aus 8 Prozessen — **es geht keine echte
Anfrage raus**):

| Einstellung | Summe | Ergebnis |
|---|---|---|
| mobile 20 + autoscout 20 | 40 | **8 von 40 Suchern sahen einen Fehler** (24 Apify-Ablehnungen) |
| mobile 16 + autoscout 16 | 32 | 0 Fehler |
| 20 + 20, aber mit Abstand | 40 | 0 Fehler (8 Abweisungen, nach 5 s durch) |

Ursache: der Starter-Plan erlaubt 32 gleichzeitige Actor-Läufe. Alles darüber wird mit
429 abgewiesen — und der Auftrag ging bisher **ohne Wartezeit** zurück in die Schlange,
sodass alle drei Versuche in gut einer Sekunde verbraucht waren und alle drei in dieselbe
Überlastung liefen.

Zwei Absicherungen:

- **Standardwerte** `MAX_CONCURRENT_MOBILE=16`, `MAX_CONCURRENT_AUTOSCOUT=16`,
  `APIFY_MAX_PARALLEL=32`. Der Start **warnt**, wenn die Summe über dem Plan liegt —
  so fällt eine falsche `.env` auf, statt still Fehler zu erzeugen.
- **`LINK_JOB_TEMPOLIMIT_WARTEN=5`** — nur ein Tempolimit (429) bekommt diesen Abstand,
  bevor der Auftrag erneut anläuft. Alle anderen Fehler laufen wie bisher sofort wieder an.

**Größerer Apify-Plan?** Dann `APIFY_MAX_PARALLEL` **und** beide Quellwerte anheben.
Festgehalten in `tests/test_apify_grenze_20260920.py`.

### Lasttest „30 gleichzeitige Verträge und Mails“ (20.09.2026)

Zwei Einwände aus dem Prüfbericht, beide nachgemessen statt geschätzt.

**`backend/scripts/lasttest_vertraege_gleiches_auto.py`** — 30 Sucher legen an derselben
Schranke gleichzeitig einen Vertrag an, einmal zum **selben** Auto, einmal zu 30
verschiedenen. Das Skript spielt danach die Wiederholung der Oberfläche nach (nur bei 503
mit `X-Wiederholen`, höchstens zweimal).

| | dasselbe Auto | 30 verschiedene |
|---|---|---|
| Vertrag bekommen | 30 von 30 | 30 von 30 |
| davon im ersten Anlauf | 25 | 30 |
| Fehler für den Nutzer | **0** | **0** |
| langsamster Fall | 12,8 s | 2,3 s |

Eine garantierte Warteschlange gibt es weiterhin nicht — fünf von dreißig liefen in den 503.
Die Wiederholung fängt sie alle ab; niemand sieht einen Fehler.

**`backend/scripts/lasttest_mailversand.py`** — Resend wird nachgestellt (echtes Tempolimit,
429 mit Retry-After), die echte Sendefunktion läuft unverändert. **Es geht keine echte Mail
raus.** Gemessen wurde der ganze Verbund: 8 Prozesse × `RESEND_PARALLEL` gegen ein Konto.

| Sendungen | vorher | seit dem Takt |
|---|---|---|
| 30 (= 60 Mail-Aufrufe) | 0–7 Fehlschläge (schwankend) | **0** |
| 46 (= 92) | **29–32 Fehlschläge** | **0** |
| 60 (= 120) | — | **0** |

Der Einwand war berechtigt: `RESEND_PARALLEL` deckelt **gleichzeitige** Anfragen, nicht
Anfragen je Sekunde, und alle Wartenden kamen im Gleichschritt zurück. Zwei Änderungen:

- **Takt** `RESEND_RATE` ÷ `RESEND_PROZESSE` (Standard 10 ÷ 8) — jeder Worker lässt nur
  seinen Anteil am Konto-Limit durch, ohne gemeinsame Ablage (die Teilung ist für alle
  gleich). **Mehr Server oder Worker → `RESEND_PROZESSE` mit anheben.**
- **Volle Streuung** bei der Wiederholung statt festem Backoff mit 0–0,5 s obendrauf.

Ablehnungen durch Resend fielen damit von 345 auf 6, und die langsamste Mail wurde
schneller (10,3 s statt 13,6 s). Festgehalten in `tests/test_mailtakt_20260920.py`.

### Lasttest „180 Sucher, 30 neue Links gleichzeitig“ (16.09.2026)

`backend/scripts/lasttest_links_gleichzeitig.py [links.txt]` legt 180 Wegwerf-Sucher in sechs Firmen
an, lässt 30 verschiedene neue Links je von einem Erst-Abrufer und fünf Mitwartenden derselben Firma
einfügen (einmal exakt gleichzeitig, einmal je 10 ms versetzt) und prüft: Wartezeit je Konto, Fehler,
genau ein Anbieter-Abruf je Link, 1 Besitzer + 5 Mitbearbeiter je Fahrzeug. Ergebnis lokal
(ein Prozess, Attrappen-Abruf 0,4 s):

| Lauf | Fehler | Job fertig (Median / max) | bis Vergleich fertig (Median / max) |
|---|---|---|---|
| gleichzeitig, 4 Job-Arbeiter | 0 von 180 | 5,1 s / 9,2 s | 7,1 s / 9,4 s |
| 10 ms versetzt, 4 Job-Arbeiter | 0 von 180 | 3,9 s / 6,9 s | 5,4 s / 7,2 s |
| gleichzeitig, 10 Job-Arbeiter | 0 von 180 | 3,9 s / 7,0 s | 6,9 s / 8,5 s |
| ein Konto allein, bekannter Link | – | – | 0,08 s |
| 150 Konten laden denselben bekannten Link gleichzeitig neu | 0 von 150 | – (kein Abruf) | 3,6 s / 4,5 s |

Nach Quelle (alle gleichzeitig, Attrappe): Kleinanzeigen Median 6,0 s, mobile.de 6,9 s, AutoScout
7,2 s — der Unterschied kommt allein von den Anbieter-Slots (AutoScout und Kleinanzeigen 3, mobile.de 10);
mit echten Anbietern zählt vor allem die Abrufdauer (Kleinanzeigen-API 1–3 s, mobile.de/AutoScout über
Apify 10–20 s). Beim Neuladen eines bekannten Links gibt es keinen neuen Anbieter-Abruf; die 3,6 s
entstehen, weil ein einzelner Testprozess 150 Vergleiche hintereinander abarbeitet (≈ 25 ms je
Vergleich, Datenbank-Rundläufe). Mit 4 Prozessen je Server auf zwei Servern entspricht das etwa
0,5 s.

**180 verschiedene neue Links gleichzeitig** (`backend/scripts/lasttest_neue_links.py`, je 60
Kleinanzeigen / mobile.de / AutoScout, Anbieterzeit nachgestellt mit `MOCK_PROVIDER_DELAY_MS_<QUELLE>`:
Kleinanzeigen 1,5 s, mobile.de und AutoScout 8 s): Der Dauer-Worker holte wartende Jobs einzeln (zwei
Aggregationen je Job, ~10 je Sekunde) — das Einsammeln allein dauerte 15 s. Seit 16.09.2026 holt er
freie Plätze paketweise (`_claim_many`), Standard `LINK_JOB_CONCURRENCY=32` je Prozess und vier
Sofort-Anstöße. Ergebnis auf EINEM Prozess mit 200 Jobs/Slots (entspricht 8 Prozessen × 32):

| Quelle | Attrappe | Abruf fertig (Median / max) | bis Vergleich (Median / max) |
|---|---|---|---|
| Kleinanzeigen (60) | 1,5 s | 7,0 s / 8,9 s | 7,8 s / 9,5 s |
| mobile.de (60) | 8 s | 13,0 s / 15,4 s | 13,7 s / 15,7 s |
| AutoScout (60) | 8 s | 13,1 s / 15,3 s | 13,8 s / 15,7 s |

Der Rest über der Anbieterzeit (≈ 5 s) ist der Anlauf eines einzelnen Prozesses: 180 Anfragen
annehmen (≈ 36 je Sekunde: Anmeldung, Cache-Prüfung, Einreihen) und die Jobs beanspruchen. Mit acht
Prozessen in Produktion schrumpft das auf etwa eine Sekunde. **Zielwerte von Ahmad** (bekannte Links
< 1 s; 180 neue Links: Kleinanzeigen < 2 s, mobile.de/AutoScout < 10 s, spätestens 15 s) sind damit
erreichbar, wenn (1) die Anbieter-Slots die gleichzeitigen Abrufe zulassen (`MAX_CONCURRENT_MOBILE`,
`MAX_CONCURRENT_AUTOSCOUT`, `MAX_CONCURRENT_KLEINANZEIGEN_API` ≥ Zahl der gleichzeitig neuen Links je
Quelle, also für den Fall oben 60) und (2) Apify mobile.de/AutoScout in ≤ 8 s liefert und der Apify-Plan
so viele gleichzeitige Actor-Läufe erlaubt. Kleinanzeigen < 2 s ist knapp: API-Antwort 1–2 s plus
≈ 1 s eigener Anteil.

**Noch schneller?** Der eigene Anteil je Vergleich ist bereits klein (Datenbank-Rundläufe, keine
Rechenarbeit); seit 16.09.2026 schreibt der Vergleich den Cache-Schlüssel des Inserats mit demselben
Write ans Fahrzeug wie die Übernahme (vorher ein eigener `update_one`). Der größte Hebel liegt in
der Server-Konfiguration: `docker-compose.yml` reichte bis einschließlich Stand `0a84eb9` als
Vorgabe nur 2 Slots für mobile.de und AutoScout, 8 für die Kleinanzeigen-API und 2 Jobs je Prozess
durch, sobald die `.env` die Werte nicht setzt — und ältere Kopien der Vorlage trugen ebenfalls je 2.
Damit warten 60 gleichzeitig neue mobile.de-Links bis zu 5 Minuten, und die Oberfläche meldet nach
2 Minuten „dauert ungewöhnlich lange, dein Link ist vorgemerkt“. Seit dem Folgestand lauten die
Vorgaben in Compose, Code und `.env.example` einheitlich 20/20/20 Slots und 32 Jobs je Prozess.
**Auf beiden Servern prüfen**, was die `.env` trägt:

```bash
grep -E "^(MAX_CONCURRENT|LINK_JOB_CONCURRENCY|WEB_CONCURRENCY)" /opt/autoschnell/.env
```

Empfehlung: Summe aus `MAX_CONCURRENT_MOBILE` und `MAX_CONCURRENT_AUTOSCOUT` höchstens so groß wie
die gleichzeitigen Actor-Läufe des Apify-Plans (Starter 32 → 16/16, Scale 128 → 60/60; Stand
16.09.2026: Starter), dazu `MAX_CONCURRENT_KLEINANZEIGEN_API=20`, `LINK_JOB_CONCURRENCY=32`; setzen
mit `deploy/env_setzen.sh` (Abschnitt „Tageslimit je Konto“), danach `docker compose up -d`.
Voraussetzung: der
Apify-Plan erlaubt so viele gleichzeitige Actor-Läufe (Apify-Konsole → Settings → Limits); sonst
stellt Apify die Läufe in seine eigene Warteschlange, die Abrufe dauern länger, und nach 180 s
bricht ein Abruf ab und der Job versucht es erneut. Die Anbieterzeit selbst (Kleinanzeigen-API
1–2 s, Apify 8–20 s) lässt sich von uns nicht verkürzen — bekannte Links sind deshalb der schnelle
Weg (kein Abruf, < 1 s). `WEB_CONCURRENCY` bleibt bei der Kernzahl (CCX23: 4); die Annahme der
Anfragen ist nicht der Engpass.

Jeder Link wurde genau einmal abgerufen, Mitwartende hängen sich an den laufenden Abruf, keine
429/503. Die Wartezeit bestimmen zwei Größen: `LINK_JOB_CONCURRENCY` (Job-Arbeiter je
API-Prozess; Produktion: `WEB_CONCURRENCY` Prozesse je Server) und die Anbieter-Slots
`MAX_CONCURRENT_MOBILE` / `MAX_CONCURRENT_AUTOSCOUT` / `MAX_CONCURRENT_KLEINANZEIGEN` (global über
alle Prozesse, in der Datenbank). Faustregel: der letzte einer Welle wartet etwa
(neue Links je Quelle ÷ Slots) × Abrufdauer. Mit nur 2 Slots (alte Vorgabe) und 15 s je Apify-Abruf
sind das bei 10 gleichzeitig neuen mobile.de-Links rund 75 s, bei 60 Links rund 7,5 Minuten; mit
20 Slots bleibt es bei 15 s bzw. 45 s. Ein Job, der 30 s lang keinen Slot bekommt, geht ohne
Fehlversuch zurück in die Schlange — er scheitert nicht, er wartet; die Oberfläche fragt bis zu
2 Minuten nach und bittet danach um einen neuen Versuch (der Link bleibt vorgemerkt). Die Kapazität
der eigenen Server lässt sich mit `deploy/lasttest-auf-prod2.sh` messen (Anbieter-Attrappe, ohne
Kosten); die echte Anbieterzeit misst nur ein Abruf mit echten Links.

### Runde 19 (16.09.2026): Sucher-Termine, Übergabe, Abruf-Lease, Auto-Daten

- **Termine:** Termin-Antworten an Sucher tragen keine Konto-Kennungen mehr (`created_by`,
  `uebergeben_von`, `kaufvorgang_id`); das Termin-Detail liefert das Fahrzeug mit derselben
  Projektion wie die Liste (keine Bestandsnotizen, Kosten, Inseratskopie) und maskiert Konto-IDs.
  Löschen prüft auch `updated_at` (409 bei paralleler Änderung).
- **„Aus meiner Liste entfernen“** übergibt den ganzen Vorgang an den Chef (Kaufvorgänge,
  Verträge, Termine), wie der Besitzerwechsel. Der Besitzerwechsel schreibt den Merker
  `vehicles.uebergabe_offen`; eine Wiederholung oder der Stundenlauf (`uebergaben_nachholen`)
  bringt eine abgebrochene Übergabe zu Ende. Die Protokollsperre gilt je Firma und wird nach dem
  Write nochmals geprüft.
- **Nachtrag 21.09.2026 (Entscheidung Ahmad, R1-01):** Den Besitzerwechsel durch den Chef
  (Feld „Bearbeiter“ in der Fahrzeugakte, `PUT /vehicles/{id}/besitzer`) gibt es nicht mehr —
  „man soll nie an dem sein abgeschlossenen Vertrag oder sonstwas wegnehmen“. Die Route
  antwortet immer 410 und ändert nichts; die Akte zeigt den Bearbeiter nur noch als Text.
  Fahrzeug, Verträge, Kaufvorgänge und Termine bleiben bei dem, der sie angelegt hat. Den
  ganzen Vorgang an den Chef übergeben nur noch „Aus meiner Liste entfernen“ (der Sucher selbst)
  und das Löschen eines Sucher-Kontos durch den Betreiber. Neue Merker
  `uebergabe_offen` entstehen nicht mehr; `uebergaben_nachholen` bringt nur alte Merker zu Ende.
- **Fahrzeugpool:** Übergabe an einen Mitbearbeiter trimmt anschließend dessen Pool; während ein
  Vertrag oder Termin entsteht, ist das Fahrzeug fünf Minuten geschützt (`geschuetzt_bis`).
  Altbestand ohne Besitzer: der Verlierer des Wettrennens wird Mitbearbeiter.
- **Abrufe:** die Cache-Lease trägt ein Besitzer-Token (`fetching_claim`); Herzschlag, Freigabe und
  Ergebnis wirken nur unter der eigenen Lease. Link-Jobs schließen nur den eigenen Claim ab; der
  Hintergrund-Abruf läuft durch dieselbe Konto-Bremse wie der Vergleich. Nach drei Job-Rennen wird
  der Cache geprüft statt „completed“ geraten (sonst 503, Client reiht neu ein). Ein technisch
  gescheiterter mobile.de/AutoScout-Abruf wird im Tagesbudget zurückgebucht. Der Vergleich ist je
  Konto auf `VERGLEICH_JE_KONTO_MINUTE` (Standard 120) gedeckelt — gegen Skripte, nicht gegen Sucher.
  Browser-Einreichungen gewinnen atomar (first-wins), die manuelle Suche verbraucht ihr Kontingent
  erst nach der Eingabeprüfung.
- **Verträge:** ein Sucher löscht keinen Vertrag mehr, zu dem ein unterschriebenes Abholprotokoll
  existiert (nur der Chef). Bei der Vertragsanlage wird ein bestehender Auto-Datensatz erst NACH
  dem gespeicherten Vertrag nachgeführt; eine kurze Sperre je Firma+Fahrzeug (`sperren`) verhindert
  zwei Datensätze bei zwei gleichzeitigen ersten Verträgen; der Datensatz wird nur über die
  quellenspezifische Fahrzeug-ID gefunden. Nach einer Neuerzeugung steht der Merker
  `auto_daten_nachfuehrung_offen`, bis die Auto-Daten nachgeführt sind (Aufräumjob holt nach).
- **Auto-Daten:** Löschen markiert zuerst die Verträge, dann fällt der Datensatz; ein Vertrag mit
  Verweis ins Leere wird bei der Fristlöschung an Ort und Stelle repariert (Datensatz aus der
  Vertragsfassung), sonst Alarm. „Schäden entfernen“ steht im Audit.
- **Fahrzeugakte:** Sucher bekommen keine Bestandskosten/-notizen und eine reduzierte Historie
  (Aktion, Bezug, Zeit); Vergleiche, Protokolle und Berichte nennen die Gesamtzahl; Protokolle sind
  nach Abschluss sortiert. Die Oberfläche blendet Chef-Funktionen (Bestandsdaten, Abweichungen
  übernehmen, manuelles Anlegen, Verkaufsentscheidungen) für Sucher aus.

### Getriebe und Kraftstoff in den Vergleichs-Links (17.09.2026)

Meldung Ahmad: „ab und zu klappt Getriebe 1:1 nicht“. Ursache: `tr=`/`ft=` im mobile.de-Link
bekamen den gespeicherten Rohwert. Nur Kleinanzeigen speicherte mobile.de-Codes; mobile.de-Inserate
über Apify („Automatic“ → `AUTOMATIC`) und AutoScout24-Inserate („Schaltgetriebe“, „Benzin“) ergaben
Werte, die mobile.de nicht kennt — gefiltert wurde still ohne Getriebe bzw. Kraftstoff. Halbautomatik
lief in beiden Links als Automatik.

- **Eine Zuordnung:** `backend/fahrzeug_codes.py` (`getriebe_code`, `kraftstoff_code`,
  `autoscout_getriebe`, `autoscout_kraftstoff`, `filter_hinweise`). Neue Quellen oder Werte dort
  ergänzen, nie wieder Rohwerte in einen Link schreiben.
- **Beim Link-Bauen:** `mobile_service.build_search_url` und `autoscout_service.build_search_url`
  leiten den Code aus Code ODER Beschriftung ab. Das gilt auch für Fahrzeuge und Zwischenspeicher mit
  alten Rohwerten — kein Neuabruf nötig.
- **Beim Speichern:** die Apify-Parser (mobile.de, AutoScout24) und der Kleinanzeigen-Parser speichern
  die mobile.de-Codes (`SEMIAUTOMATIC_GEAR` für Halbautomatik, `HYBRID_DIESEL` für Diesel-Hybrid).
- **Hinweis statt stillem Link:** fehlt Getriebe oder Kraftstoff im Inserat oder ist der Wert
  unbekannt, meldet der Vergleich das (`regeln_nicht_abgebildet`).
- **Tests:** `tests/test_getriebe_kraftstoff_filter.py` gegen die echten Actor-Datensätze in
  `tests/fixtures`.

### Nachprüfung Nr. 46–143 (16.09.2026): Verträge, Protokolle, Abrufe, Abos

98 Reviewer-Punkte gegen `8d16a95` geprüft; 70 echte in vier Commits mit je eigener Vollprüfung
behoben (A `d051d02`, B `3fdc655`, C `729a7c1`, D `8ab1d50`). Neue Regeln, die nicht wieder gebrochen werden dürfen:

- **Verträge/Auto-Daten (A):** `preis_vor_abholung` wird nur beim ERSTEN Preiswechsel gesetzt
  (Einkaufspreis = Preis des ersten Vertrags). `freigabe_alt` hält bis zu 50 alte Freigabe-Links,
  abgelaufene werden vorher entfernt; auch alte Token zählen `abrufe`. `auto_daten.aktualisieren`
  überschreibt bekannte Werte nie mit `None`; „ohne Auto-Daten“ heißt `OHNE_AUTO_DATEN` (fehlt,
  `null`, `""`, falscher Typ); Ersatzdaten aus dem Fahrzeug tragen `ersatzquelle_fahrzeug`.
  `vertrag_sperre` liefert `{schluessel, claim}`, wird nur mit eigenem Claim freigegeben (immer im
  `finally`) und ist fail-closed (`SperreBelegt` -> 503 mit Retry-After 3). Nach dem Insert wird der
  Lebenszyklus nachkontrolliert (sonst Vertrag zurück, 409) und das CAS-Ergebnis geprüft (sonst
  `zurueckrollen`). Die Sucher-Löschsperre findet Protokolle auch über `contract_id`. Der Grabstein
  der Fristlöschung wird per CAS gesetzt (`loeschung.status != laeuft`), sonst Wiederaufnahme.
- **Protokolle/Termine/Fahrer (B):** zweiter Erstspeicher (DuplicateKey) ohne Revision -> 409
  „App neu laden“; kein Entwurf/keine Korrektur zu einem Vertrag mit Grabstein
  (`_vertrag_nicht_in_loeschung`). `preis_quelle = "chef"` bei Chef-Preis (fällt beim Zurücksetzen
  weg). Wiederöffnen eines Termins nimmt die Freigabe in derselben Transaktion zurück
  (`freigabe_beim_schliessen_zuruecknehmen(..., session)`); `korrektur_verwerfen` prüft die
  Reaktivierung der Vorversion (Transaktion bricht ab bzw. Verwerfen wird zurückgenommen, Alarm
  `protokoll_korrektur_ohne_vorversion`). `final_price` am Termin ruft `vor_ort_nachtragen`. Jeder
  Statuswechsel ohne Client-Stand läuft gegen `existing.updated_at`; Termin-Stand wird erst nach dem
  gelungenen Protokoll-CAS angefasst (Alarm `termin_stand_nicht_angefasst`). „Termin erstellt“ nur,
  wenn der Vertrag noch `erstellt`/`neu erstellt` ist. `termin_status_uebernehmen` wiederholt einen
  verlorenen CAS bis 3x, sonst `nacharbeit_offen` + Alarm `kaufvorgang_status_konflikt`. Löschen liest
  die betroffenen Vorgänge in der Transaktion; Audit `termin.geloescht` erst nach dem erfolgreichen
  Löschen (Alarm `audit_fehlt`). Nach dem Termin-Insert: Vertrag ohne Grabstein und Fahrzeug nicht
  gelöscht, sonst Termin zurück + 409. `POST /appointments` und Abholberichte sind für Sucher
  maskiert (`termin_fuer_sucher`, `bericht_fuer_sucher`). Ablehnen einer Fahrt prüft `stand`.
  Protokollliste je Fahrzeug: eigener Termin genügt (ohne Fahrzeug-Bereich-Vorfilter).
- **Vergleich/Abrufe/Link-Jobs/Beweise (C):** `/mobile/compare` prüft erst die Adresse, dann das
  Tempolimit, und schreibt den `vehicle_comparisons`-Eintrag erst NACH `_fahrzeug_uebernehmen`.
  `get_or_fetch_listing` stempelt `fetched_at/expires_at/last_used_at` mit dem Abruf-Ende; bei
  verlorener Lease (`matched_count == 0`) wird KEIN Beweis vorgemerkt. Alte Snapshots: existiert das
  Fahrzeug in der Firma, ist es der Anker (Bereich oder eigener Vertrag), der Ersteller zählt nur
  ohne Fahrzeug; Routen laufen durch `_snapshot_nutzer` (Firmensperre), Download mit
  `Cache-Control: no-store`, Sucher sehen keine Kollegen-`user_id`. Live-Zähler ohne `?quelle`
  zählt `mobile:<id>`. `_gehalten` läuft ohne 500er-Deckel (Cursor), Weiterverkaufsinserate mit
  Status `geloescht` halten nicht. Beweis-Worker: `bearbeitung_claim` je Beanspruchung
  (`_eigene_bearbeitung`), `_aufraeumen` nur gegen das gelesene `bearbeitung_bis`; in Produktion ohne
  Unique-Index keine Vormerkung (`_produktion()`, Alarm `unique_index_fehlt`). Link-Jobs:
  `_requeue_stale` nur gegen gelesenes `processing_until` + `claim_id`; Import-Fehlerpfad mit
  `eigener_claim`; nach außen nur `fehlertext(exc)` (`FEHLER_TECHNISCH`), roher Text in
  `error_intern`; ein geteilter Job bucht das Tageslimit beim ersten wartenden Konto mit Kontingent.
  `fetch_listing`: `ListingGone` wird NICHT zurückgebucht (Anbieter kontaktiert), technische Fehler
  weiter. `release_slot` stempelt `freigegeben_am`; `_heal_stale` räumt Freigaben nach
  `FREIGABE_NACHLAUF_SEKUNDEN` (60). Produktionsprüfung warnt, wenn `ANBIETER_TAGESLIMIT_JE_KONTO`
  fehlt oder 0 ist.
- **Abos/Mail/Aufräumen/Pool/Konten (D):** `massgebliches_abo` bindet das persönliche Abo an die
  aktuelle `dealer_id` (Altbestand ohne Feld gilt); `/dealer/sucher` ignoriert `status = ersetzt`;
  `plan` in `/dealer/abo-anfrage-selbst` muss ein String sein (400); `days_remaining` nutzt
  `_ablauf_parsen` (naiv = UTC). Nacharbeit (Verträge, Termine, Kaufvorgänge) rotiert über
  `nacharbeit_versuch_am` (nie versuchte zuerst). `email_service`: 5xx nach allen Wiederholungen ->
  `ResendUnklar`, KEIN SMTP-Rückfall (429/4xx wie bisher). `berichte_nach_frist_loeschen` lässt
  Berichte offener Termine stehen und rechnet die Frist ab `updated_at` des geschlossenen Termins.
  `konten_ohne_firma_sperren` sperrt alle Konten ohne laufende Löschung. `fahrzeugpool_trimmen`
  trägt `geschuetzt_bis` im CAS von Löschen und Übergabe.
- **Bewusst so / nicht echt:** 52, 60, 63/64, 70, 71, 76–78, 80/81, 86, 89, 91, 96, 99, 101, 102,
  107, 128, 136, 140, 142, 143 (Begründung je Punkt im Commit-Text). Schon behoben: 97, 98, 121.
  Offen: Nr. 68 (Entscheidung Ahmad: Sucher schließt Termin ohne Fahrer), Nr. 59 (später:
  Stand-Prüfung für Einstellungen).
- **Test-Fallstricke:** Quelltext-Tests für den Audit-Eintrag beim Termin-Löschen erwarten jetzt
  „nach dem Löschen“ (runde29/runde30); `POST /appointments` liefert Suchern kein `created_by` /
  `kaufvorgang_id` (aus der DB lesen); Snapshot-Test in runde17 erwartet 404 für den Ersteller nach
  der Übergabe; Resend-Test 503 erwartet `ResendUnklar`; Abholbericht-Test schließt den Termin vor
  der Fristlöschung.

### Auto-Daten, Preis-Nachführung und helle Ansicht (15.09.2026, Wunsch Ahmad)

- **Auto-Daten löschen:** der Super-Admin entfernt einen Datensatz endgültig
  (`DELETE /api/admin/vehicle-data/{id}`, Knopf „Löschen“ je Zeile mit Rückfrage). Verträge,
  die den Datensatz tragen, bekommen den Vermerk `auto_daten_entfernt_am`: die 90-Tage-Löschung
  verlangt dann keinen Datensatz mehr, die Reparatur legt keinen neuen an. Audit
  `admin.auto_daten.geloescht`.
- **Ein Auto, ein Datensatz:** ein neuer Kaufvertrag zu demselben Fahrzeug (gleiche Firma,
  gleiches Fahrzeug bzw. gleiche Anzeigen-ID) führt den vorhandenen Datensatz nach (Preis,
  Kaufdatum, Zusicherungen) statt ein zweites Auto anzulegen. Der Einkaufspreis des Vertrags
  (für den man zum Auto gefahren ist) bleibt stehen: eine Nachverhandlung bei der Abholung füllt
  die eigenen Spalten „Preis vor Ort“ (`preis_vor_ort_cents`) und „Mängel vor Ort“
  (`maengel_vor_ort`, die vom Fahrer im Abholprotokoll festgehaltenen neuen Schäden, gefiltert wie
  die Vertragsschäden). Beide werden beim Abschluss des Protokolls und in der Selbstheilung
  nachgetragen; „Schäden entfernen“ leert auch die Mängel vor Ort.
- **Helle Ansicht komplett:** Sucher-App, Betreiber-Bereich (jetzt mit Design-Schalter in der
  Seitenleiste) und Fahrer-App folgen dem Schalter. Dunkle Utility-Klassen werden in
  `index.css` zentral umgelenkt; bewusst dunkle Bereiche (Abhol-Check des Fahrers) tragen
  `.bleibt-dunkel`. Die Startseite bleibt als Marketing-Seite dunkel.
- Vertragsformular des Suchers: Schrift eine Stufe kräftiger (`.vertrag-formular`).

### Tageslimit je Konto und Werte in der .env setzen (16.09.2026)

Entscheidung Ahmad (16.09.2026): **höchstens 400 neue Anbieter-Abrufe je Konto und Tag**, über
alle Quellen (mobile.de, AutoScout, Kleinanzeigen): `ANBIETER_TAGESLIMIT_JE_KONTO` (Compose-Vorgabe
400; 0 = aus). Gezählt wird nur ein echter Abruf: bekannte Links aus dem Speicher (14 Tage, spätestens nach 21 Tagen gelöscht),
Mitwarten an einem laufenden Abruf und technisch gescheiterte Abrufe (Rückbuchung) kosten nichts.
Der 401. Abruf bekommt 429 mit klarer Meldung („Tageslimit für neue Links erreicht … morgen
erneut“), ein Link-Job scheitert sofort ohne weitere Versuche. Firmen- und Gesamtlimit bleiben aus
(0), die Tageswarnung `ANBIETER_TAGESWARNUNG` bleibt ein Hinweis (Compose-Vorgabe seit 21.09.2026
**5000** statt 500 — bei 1.000–3.240 erwarteten Apify-Abrufen am Tag kam sonst jeden Tag ein
Alarm samt Betriebsmail; steht in der Server-`.env` noch `ANBIETER_TAGESWARNUNG=500`, gilt der
alte Wert: `sh deploy/env_setzen.sh ANBIETER_TAGESWARNUNG=5000`). Tageswechsel um 0 Uhr UTC
(Zähler `provider_budget`, Schlüssel `<Tag>:konto:<user_id>`). Erwartete Menge (Ahmad, 16.09.):
30 Sucher × 150 Vergleiche × 30 Tage, davon 10 % bekannt, 70 % mobile.de, 10 % AutoScout, 20 %
Kleinanzeigen — rund 4.500 Vergleiche am Tag; Apify-Plan „Starter“ (32 gleichzeitige Läufe) reicht,
das eigene Apify-Kostenlimit muss über den erwarteten ~90 $/Monat liegen.

Werte ohne Editor setzen (ersetzt vorhandene Zeilen an Ort und Stelle, hängt fehlende an, legt
`.env.bak-<Datum>` an):

```bash
cd /opt/autoschnell && sh deploy/env_setzen.sh MAX_CONCURRENT_MOBILE=16 MAX_CONCURRENT_AUTOSCOUT=16 MAX_CONCURRENT_KLEINANZEIGEN_API=20 LINK_JOB_CONCURRENCY=32 ANBIETER_TAGESLIMIT_JE_KONTO=400
```

**Rechte der `.env` (21.09.2026):** `env_setzen.sh` schreibt die Datei über eine Zwischendatei neu.
Bis zum 21.09.2026 war die neue `.env` danach für **jeden Benutzer** auf dem Server lesbar (644)
— sie enthält aber alle Geheimnisse (Schritt 5 verlangt `chmod 600`). Jetzt entstehen `.env` und
`.env.bak-<Datum>` nur für den Besitzer lesbar, und jeder Aufruf setzt die `.env` wieder auf
`600` — auch eine, die ein älterer Aufruf offen hinterlassen hat. Prüfen: `ls -l .env .env.bak-*`
→ `-rw-------`. **Server mit Stand vor dem 21.09.2026: einmal `chmod 600 .env .env.bak-*`** — dort
liegt bis zum `git pull` noch das alte `env_setzen.sh` ohne diese Rechte, der erste Aufruf nach
dieser Anleitung lässt `.env` und Sicherung also noch offen. Ab dem 21.09.2026 setzt auch
`sh deploy/rollout.sh` nach dem `git pull` jedes Mal `chmod 600 .env .env.bak-*`.

Danach neu starten — **Server für Server, nie beide gleichzeitig** (erst prod2, dann prod1):

- **Neuer Stand aus Git:** `sh deploy/rollout.sh` (wie oben, prod2 mit `ERSTER_SERVER=1`).
- **Nur Werte geändert, gleicher Stand:** nicht einfach `docker compose up -d` — das startet das
  Backend neu, während der Load Balancer noch Besucher schickt. Erst aus der Rotation nehmen:

  ```bash
  cd /opt/autoschnell
  grep -q '^COMPOSE_FILE=' .env || export COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.replica.yml
  touch deploy/drain/aktiv     # Load Balancer nimmt den Server heraus
  sleep 60                     # bis er es gemerkt hat
  docker compose up -d         # Container mit den neuen Werten neu erzeugen
  sh deploy/freigeben.sh       # prueft /api/ready und die Startseite, erst dann zurueck
  ```

  Die `grep`-Zeile setzt `COMPOSE_FILE` nur, wenn die `.env` es nicht schon vorgibt — ohne den
  Eintrag startet der Stack sonst ohne die Replica-Set-Einstellungen (Vorfall 07.09.2026, das
  Mitglied fällt aus `rs0`; `rollout.sh`/`freigeben.sh` setzen das selbst).
  Erwartet: „Drain aufgehoben — der Load Balancer nimmt … in ca. 45 s wieder auf.“ Meldet
  `freigeben.sh` „Backend meldet sich nicht bereit“, ist das Backend meist nur noch beim Start:
  30 s warten und `sh deploy/freigeben.sh` erneut aufrufen — bis dahin bleibt der Server sicher
  im Drain, der andere trägt die Last. Danach 60 s warten, dann derselbe Ablauf auf dem anderen
  Server.

Prüfen mit `docker compose exec backend env | grep -E "MAX_CONCURRENT|LINK_JOB|TAGESLIMIT"`.

### Versand, Kundenfassung und Abrufe seit Runde 16 (15.09.2026)

- **Kundenfassung des Kaufvertrags** (E-Mail/WhatsApp): ohne Abschnitt „Unterschriften“ und ohne
  Empfangsbestätigung (Schlüssel erhalten, Kaufpreis bestätigt) — beides nur in der Druckfassung
  für Fahrer, Sucher und Chef. Kein Kennzeichen, keine Uhrzeit im Vertrag. Scheckheftgepflegt als
  Auswahl (ja, lückenlos / nein / teilweise bis MM/JJJJ).
- **Leere Punkte fehlen im Vertrag** (16.09.2026, Wunsch Ahmad): Was der Sucher nicht ausfüllt
  (z. B. Ansprechpartner, E-Mail, Bereifung), steht nicht als Zeile mit Strich im Vertrag, sondern
  entfällt ganz — in den Kästen Verkäufer/Käufer, bei den Fahrzeugdaten und bei den
  Zusicherungen; ein leerer Zusicherungs-Block entfällt samt Überschrift (`pdf_service._ohne_leere`).
  Seit dem 16.09.2026 (abends) steht der **Ansprechpartner gar nicht mehr im Kaufvertrag**, auch
  ausgefüllt nicht (Wunsch Ahmad); das Feld bleibt für Versand und Marktplatz.
- **Firmenlogo nur durch den Chef** (16.09.2026): `POST /dealer/logo` antwortet Suchern mit 403, die
  Einstellungen zeigen Suchern das Logo nur an; `logo_url` ist kein persönliches Sucher-Feld mehr,
  alte persönliche Logo-Overrides sind wirkungslos.
- **Vergleichsregeln speichern** (16.09.2026): das Formular bekommt immer vollständige Regelpakete
  beider Profile (`deps.regelpakete_vervollstaendigen`), ein geleertes Zahlenfeld speichert den
  Standard statt 0, der Editor bleibt nach dem Speichern auf dem bearbeiteten Profil, und ein
  Sucher-Override entsteht nur bei echter Abweichung (Vergleich vollständiger Pakete).
- **Versand:** `VERSAND_JE_KONTO_10MIN` (Standard 300) deckelt die Vertragsversände je Konto.
  Der Versand-Schlüssel ist an Fassung, Kanal, Empfänger, Betreff und Text gebunden (409 bei
  Abweichung); bei E-Mail können zwei Tabs denselben Vertrag nicht gleichzeitig an denselben
  Empfänger schicken (bei WhatsApp entsteht nur der Link, beide bekommen denselben); ein
  gescheiterter Versand gilt nie als „bereits gesendet“. WhatsApp-Nummern brauchen
  7–15 Ziffern. Der Versandvermerk trägt die Fassung; nach einer Neuerzeugung steht der Vertrag
  wieder auf „neu erstellt“. Ein neuer WhatsApp-Link lässt einen noch laufenden alten Link gültig.
- **Anbieter-Abrufe:** direkte Abrufe (Cache-Miss im Vergleich und beim Auflösen) sind je Konto
  gebremst — `ABRUF_JE_KONTO_MINUTE` (Standard 60) und `ABRUF_GLEICHZEITIG_JE_KONTO` (Standard 8);
  Tageslimits bleiben aus (Vorgabe: keine Tageslimits für Sucher). Ein gebuchter Kleinanzeigen-
  Rückfall wird bei Anbieterfehler oder voller Warteschlange zurückgebucht.
- Beweis-Routen prüfen den Firmenstatus (Löschung, Sperre) wie alle Firmenrouten; die Firmensperre
  für Fahrer folgt jetzt ebenfalls dem eingetragenen Hauptaccount.
- Vergleichsregel Leistung: neuer Modus „−X PS und aufwärts (nach oben offen)“. Vergleich: Schalter
  „Filter daneben öffnen (zweiter Bildschirm)“ — braucht die Bildschirm-Berechtigung des Browsers
  (Chrome/Edge), sonst öffnet das Fenster neben der App, wenn Platz ist.


### Helle Ansicht nachgeschärft (18.09.2026, Wunsch Ahmad „mach die helle Variante auch perfekt“)

Geprüft wurde die ganze Oberfläche im hellen Design — jede Seite, jeder Dialog, jedes Dropdown —
mit einer im Browser gemessenen Kontrastprüfung (Schrift gegen die tatsächlich darunter liegende
Fläche). Gefunden und behoben:

- **Statusfarben sind jetzt Token** (`--st-blau/-himmel/-cyan/-gruen/-gelb/-amber/-rot/-grau/-lila`)
  und **Schrift auf getönten Flächen** eigene Token (`--tx-blau/-cyan/-gruen/-amber/-rot/-lila`).
  Im dunklen Design stehen dort die bisherigen Werte, im hellen kräftigere. Vorher standen feste
  Hex-Farben in den Seiten (Termine, Bestand, Freigaben, Inserat, Anfragen, Einstellungen,
  Fahrer-App, Beweiskarte, Fahrzeugpool-Status): Grün, Gelb, Amber und Rot kamen auf Weiß nur auf
  2–3,5:1 und waren praktisch unlesbar.
- **Statusschild** (`StatusSchild.jsx`) bekommt die Farbe als Variable `--st`; Rahmen und Fläche
  mischt die Klasse `.status-schild` per `color-mix`. Vorher wurde „66“/„1a“ an den Hex-Wert
  gehängt — mit Token geht das nicht mehr.
- **Marktplatz folgt dem Schalter:** `data-theme="dark"`, `#0a0a0a`, `#141416`, `#fff` und
  `rgba(255,255,255,x)` sind raus, die Kopfzeile nutzt `.glass-nav`. Käufer-Login und Startseite
  bleiben bewusst dunkel (Marketing/Anmeldung).
- **Meldungen und Dialoge:** `<Toaster>` (sonner) folgt dem Schalter über den neuen Haken
  `useTheme()`; die shadcn-Token (`--background`, `--popover`, …) haben helle Gegenstücke — der
  Dialog „Als App installieren“ war sonst ein schwarzer Kasten auf heller Seite. Der Schalter
  setzt zusätzlich `<meta name="theme-color">`, damit die Browserleiste auf dem Handy mitzieht.
- **Graue Schrift im hellen Design dunkler:** `--text-secondary` #55555a, `--text-muted` #646469,
  `--text-dim` #5a5a5f (Apples #6e6e73/#86868b lagen bei 3,3:1).
- **Weitere Umlenkungen in `index.css`:** `bg/border-white/[0.02…0.25]`, `hover:`- und `focus:`-
  Varianten, `divide-white/10`, `text-white/50|60|70`, solides `border-white` sowie die hellen
  bunten Tailwind-Stufen (`text-emerald-400`, `text-red-400`, `text-amber-300`, `text-sky-400`, …)
  und die 15-%-Tönung der Betreiber-Schildchen.

Regel für neue Oberflächen: **keine festen Farben in JSX** — Flächen und Schrift über die Token,
Statusfarben über `--st-*`, Schrift auf getönten Hinweisflächen über `--tx-*`; bewusst dunkle
Bereiche tragen `.bleibt-dunkel`. Wächter: `backend/tests/test_helle_ansicht_20260918.py`.

Offen (bewusst nicht geändert): das Signalrot `#ff3b30` der Marke bleibt in beiden Designs —
weiße Schrift darauf kommt auf 3,55:1 (Apple macht es genauso). Im **dunklen** Design ist
`text-zinc-600` an manchen Stellen zu dunkel (2,4:1, u. a. Fahrzeugpool und Team) — das ist eine
Altlast der dunklen Ansicht und wartet auf Ahmads Entscheidung.

### Beweisdokument nur noch auf Knopfdruck (18.09.2026, Wunsch Ahmad)

Bis hierher entstand zu **jedem** abgerufenen Inserat automatisch ein Beweis-PDF. Gemessen sind das
rund 0,85 MB je Inserat (echte Inserate haben im Median 20 Fotos, Deckel `BEWEIS_FOTOS_MAX=20`) —
bei 30 Suchern × 150 Vergleichen am Tag also ~3,5 GB täglich, von denen fast nichts gebraucht wird.

Neu:

- **Automatisch entsteht nichts mehr.** Weder der Abruf (`listing_identity.get_or_fetch_listing`)
  noch der Vergleich (`routes/listings.compare`) merken etwas vor. Der Vergleich zeigt ein bereits
  vorhandenes Dokument weiterhin an.
- **`POST /api/beweise/anfordern`** (`{vehicle_id}` **oder** `{cache_key}`) legt es an — idempotent:
  gibt es zum Inserat schon eines (auch ein laufendes), kommt genau dieses zurück, nie ein zweites
  „erstes" Dokument. Erlaubt ist es für Fahrzeuge im eigenen Bereich; ohne Fahrzeug nur für den,
  der das Inserat selbst verglichen hat. Audit: `beweis.angefordert`.
- **Oberfläche:** Die Beweis-Karte (Fahrzeugakte, Termine, Vertragsarchiv, Vergleich) zeigt jetzt
  den Knopf „Beweisdokument erstellen", wenn es noch keines gibt, und „Noch einmal versuchen",
  wenn die Erzeugung gescheitert ist. Nach dem Vertragsversand (WhatsApp **oder** E-Mail) fragt der
  Versand-Dialog einmal: „Beweisdokument erstellen lassen?" → *Ja, erstellen* / *Nein, danke*.
- **Datenstand:** Angefordert wird mit dem Stand aus dem Inseratsspeicher (`listings_cache`,
  14 Tage, spätestens nach 21 Tagen gelöscht) — ersatzweise mit den beim Vergleich am Fahrzeug gespeicherten Daten. Die Fotos holt der
  Worker beim Erzeugen vom Portal; ist das Inserat dann schon offline, entsteht das Dokument ohne
  Fotos (die Fotoadressen stehen weiter im Anhang). Wer das Dokument sicher mit Fotos will, fordert
  es am selben Tag an.
- **Zurückschalten ohne Code-Änderung:** `BEWEIS_AUTOMATISCH=true` in der Server-`.env` stellt das
  alte Verhalten wieder her (jedes abgerufene Inserat bekommt wieder automatisch eines).

Erwartete Größe danach: statt ~3,5 GB/Tag nur noch für die Inserate, die wirklich zum Vertrag
führen — bei 5 % Vertragsquote ~0,2 GB/Tag, Dauerstand rund 6 GB (30 Tage Aufbewahrung)
statt 215 GB.
Wächter: `backend/tests/test_beweis_auf_knopfdruck_20260918.py`.

### Drei Wünsche vom 18.09.2026 (Ansichten, Vertragsbedingungen, Navi)

**1. Werbeseiten bleiben in beiden Designs dunkel.** Startseite und die linke Hälfte der Anmeldung
liegen auf dunklen Fotos; im hellen Design zog vorher nur die Schrift ins Helle um und stand
dunkelgrau auf dunklem Grund. Beide tragen jetzt `class="bleibt-dunkel"` **und** `data-theme="dark"`.
Dafür gilt seit heute: die dunklen Token stehen unter `:root, [data-theme="dark"]` (ein einzelner
Bereich kann also dunkel bleiben), `[data-theme="dark"]` setzt zusätzlich `color: var(--text-primary)`
(sonst erbt der Bereich die dunkle Schrift des hellen Seitenkörpers), und die Utility-Umlenkungen
lassen mit `:not(.bleibt-dunkel):not(.bleibt-dunkel *)` den markierten Bereich **und** seinen Inhalt aus.

**2. Vertragsbedingungen/AGB und Besondere Vereinbarungen stehen im Vertragsdialog.** Seit Runde 26
liegt der AGB-Text in EINEM Feld der Einstellungen (`digital_vertragstext`); der Dialog zeigte aber
weiter nur das seitdem leere `default_terms` — der Text landete still im PDF, war aber nirgends zu
sehen. Jetzt:
- Der Dialog füllt „Vertragsbedingungen & AGB" aus den Einstellungen vor (leer gespeichert =
  Standardtext, den `/auth/me` als `digital_vertragstext_standard` mitliefert) und trägt ihn nach,
  falls die Einstellungen erst nach dem Öffnen geladen sind (unberührte, leere Felder).
- `ContractIn.digital_vertragstext` (max. 20.000 Zeichen) ist ein **Übersteuern für genau diesen
  Vertrag**; leer = weiterhin der Text aus den Einstellungen. Gilt für `/contracts` und
  `/contracts/preview` gleichermaßen.
- Ein noch vorhandener alter `default_terms`-Text erscheint als zusätzliches Feld („Zusätzlicher
  AGB-Abschnitt"); ist er leer, bleibt das Feld weg.
- „Besondere Vereinbarungen" wird wie bisher aus `default_special_agreements` vorbelegt.

**3. Navi wird mitverglichen.** Steht im Inserat ein Navigationssystem (Ausstattungsliste, Titel oder
Beschreibung), hängt der mobile.de-Link `fe=NAVIGATION_SYSTEM` an — der Parameter stammt aus einem von
Ahmad geprüften Suchlink (das früher benutzte `f=…` filterte nicht und wurde in Runde 24 entfernt).
Erkennung zentral in `backend/fahrzeug_codes.py::hat_navigation`: „ohne Navi", „kein Navigationssystem"
und bloße „Navi-Vorbereitung" zählen **nicht**. AutoScout24 bleibt unverändert — dafür fehlt ein
geprüfter Link mit gesetztem Ausstattungsfilter.

Wächter: `test_helle_ansicht_20260918.py` (Regel 6), `test_vertragsbedingungen_20260918.py`,
`test_getriebe_kraftstoff_filter.py` (Navi-Block).

### Navi-Regel und „Abbrechen" beim Vergleich (18.09.2026, Wunsch Ahmad)

**Navi als Regel.** In den Vergleichsregeln (beide Profile) steht jetzt „Navigationssystem":
`wenn_vorhanden` (Standard) hängt `fe=NAVIGATION_SYSTEM` an, sobald das Inserat ein Navi nennt;
`ignore` filtert nie danach. Gespeicherte Altpakete ohne den Schlüssel verhalten sich wie
`wenn_vorhanden`. Erkennung: `fahrzeug_codes.hat_navigation` (Ausstattung, Titel, Beschreibung;
„ohne Navi", „kein Navigationssystem", „Navi-Vorbereitung" zählen nicht).

**Abbrechen.** Dauert ein Abruf zu lange, steht neben „Lade…" ein **„✕ Abbrechen"**. Der Klick
- bricht die laufende Anfrage im Browser ab (AbortController, durchgereicht bis in die
  Job-Abfrage in `lib/linkCheck.js`; `istAbbruch()` unterscheidet Abbruch von Fehler — es
  erscheint keine Fehlermeldung, sondern „Abgebrochen — du kannst sofort einen neuen Link
  einfügen."),
- meldet dem Server über `POST /api/listings/check/{job_id}/abbrechen`, dass hier niemand mehr
  wartet (`link_jobs.warten_beenden`).

Serverseitig gilt: Ein Job gehört **allen**, die auf dasselbe Inserat warten. Steigt der letzte
Wartende aus und hat noch kein Worker angefangen (`queued`), wird der Job gelöscht — der
Anbieter-Abruf findet gar nicht erst statt (spart Apify-Lauf und Tageskontingent). Läuft er schon
(`processing`), läuft er zu Ende; sein Ergebnis landet im Zwischenspeicher, der nächste Versuch mit
demselben Link ist dann sofort da. **Einen bereits gestarteten Apify-Lauf können wir nicht abbrechen**
(der Sync-Endpunkt liefert keine Run-ID) — dafür müsste der Abruf auf die asynchrone Apify-API
umgestellt werden.

Zusätzlich: Bricht der Browser mitten im Abruf ab, gibt `listing_identity.get_or_fetch_listing` die
Inserats-Sperre jetzt frei (`except BaseException` + `asyncio.shield`) — vorher blieb derselbe Link
bis zu 90 Sekunden mit „wird gerade abgerufen" blockiert, weil `CancelledError` keine `Exception` ist.

Wächter: `backend/tests/test_abbrechen_und_navi_20260918.py`, `src/lib/linkCheck.test.js`.

### Link einfügen per Klick, Filter als Tab (18.09.2026, Wunsch Ahmad)

**Klick ins Link-Feld fügt den kopierten Link ein.** Kein Rechtsklick → Einfügen, kein Strg+V:
Ein Klick ins leere Feld liest die Zwischenablage (`navigator.clipboard.readText`) und trägt den Text
ein, **wenn** er ein echter Inserats-Link ist (`lib/inseratsLink.js`: Kleinanzeigen-Anzeige,
mobile.de-Detailseite, AutoScout24-Angebot — keine Suchseiten). Bewusst **ohne** automatischen Start:
ein alter Link in der Zwischenablage soll keinen Abruf auslösen, nur weil man ins Feld klickt
(Strg+V startet weiterhin sofort, das bleibt wie es war).

- Chrome/Edge fragen beim ersten Mal „Text aus der Zwischenablage zulassen?" — erlaubt der Nutzer
  das nicht (oder der Browser kann es nicht, z. B. Firefox), passiert einfach nichts; normales
  Einfügen funktioniert unverändert. Es wird dann auch nicht bei jedem Klick erneut gefragt.
- Derselbe Text wird nicht zweimal eingefügt (wer das Feld leert, bekommt ihn nicht sofort zurück).

**Filter öffnen sich als Tab.** Ein einzelnes Portal ging bisher als eigenes **Fenster** auf
(`openInPopup` mit Größenangaben). Jetzt öffnet auch der Einzelfall einen **benannten Tab**
(`window.open(url, name)` ohne Features) — der nächste Vergleich benutzt denselben Tab wieder, statt
Tabs zu stapeln. Ist kein Browserfenster offen, macht der Browser von sich aus eines auf. Nur mit
„Filter daneben öffnen (zweiter Bildschirm)" bleibt es beim platzierbaren Fenster — genau dafür ist
der Schalter da.

Wächter: `src/lib/inseratsLink.test.js`, `src/lib/popup.test.js` („benannter Tab statt eigenem Fenster").

### Automatisch auslesen nach dem Einfügen + Team-Seite ist Chefsache (18.09.2026)

**Auslesen startet jetzt nach jedem Einfügen.** Rückmeldung Ahmad: „automatisch auslesen nach Link
einfügen klappt irgendwie nicht." Der Start hing bisher am `paste`-Ereignis des Browsers — das kommt
nicht auf jedem Weg an (Rechtsklick-Menü in manchen Browsern, Ziehen und Ablegen, Einfügen aus der
Zwischenablage per Klick, Ersetzen eines markierten Texts). Jetzt zählt das Ergebnis im Feld:
Springt der Inhalt **in einem Rutsch** um mindestens 12 Zeichen auf einen gültigen Inserats-Link
(`lib/inseratsLink.js`), läuft der Vergleich los. Zeichenweises Tippen löst weiterhin nichts aus,
ein laufender Abruf wird nicht doppelt gestartet.

**Die Seite „Mitarbeiter / Sucher" schickt Sucher zurück.** Befund aus dem Rollentest: Im Menü sieht
ein Sucher die Seite nicht, über ein Lesezeichen oder eine alte Adresse kam er trotzdem hin — dann
liefen drei Chef-Abrufe (`/dealer/sucher`, `/dealer/sucher-plans`, `/dealer/sale-plan`) in 403 und es
standen rote Fehlermeldungen auf dem Bildschirm. Der Server hat nie Daten herausgegeben
(`deps.current_chef`); es war eine Anzeige-Sache. Jetzt lädt die Seite für Nicht-Chefs gar nicht
erst und leitet still auf die eigene Startseite (`/app/vergleich`). „Freigaben" löst dasselbe schon
länger mit einem Hinweistext — beide Wege bleiben wie sie sind.

Wächter: `backend/tests/test_rollen_sichtbarkeit_20260918.py`, `src/lib/inseratsLink.test.js`.

### Kaputte Unterschrift bringt den Abschluss nicht mehr zum Absturz (19.09.2026)

Gefunden im kompletten Rollentest (Sucher, Chef mit Sucher-Funktion, Fahrer-App): Beim Abschluss des
Abholprotokolls wurde die Unterschrift nur auf ihr **Dateiformat** geprueft (erste Bytes). Eine
abgeschnittene Datei — so kommt sie an, wenn die Uebertragung abbricht — kam damit durch und fiel
erst beim Bauen des PDF auf: **500 "Interner Serverfehler"**, und zwar genau in dem Moment, in dem
der Fahrer beim Verkaeufer unterschreiben laesst. Der Vorgang selbst blieb sauber (Rollback gibt
Claim und Dateien frei), aber der Fahrer stand vor einer Fehlernummer statt vor einer Ansage.

Jetzt wird jedes Bild, das in ein PDF wandert, einmal wirklich gelesen
(`storage_service.bild_lesbar_pruefen`):

- **Unterschrift**: laesst sie sich nicht lesen, kommt sofort *"Unterschrift (fahrer) konnte nicht
  gelesen werden — bitte noch einmal unterschreiben"* (400) statt eines Absturzes. Sie darf nicht
  still fehlen — ohne Unterschrift ist das Protokoll wertlos.
- **PDFs**: Abholprotokoll und Beweisdokument lesen jedes eingebettete Bild einmal wirklich und
  lassen bei einem unlesbaren Bild einfach Platz, statt den ganzen Vorgang abstuerzen zu lassen.
  Fotos duerfen beim Hochladen weiterhin nie scheitern (bewusste Regel in `bild_verkleinern`) —
  jetzt faellt hoechstens das Bild aus, nie der Abschluss.

In der Fahrer-App werden die Unterschriften bei genau dieser Meldung geleert — mit demselben
kaputten Bild waere jeder weitere Versuch gescheitert. Ein gescheiterter Abschluss aus anderem
Grund (409 Preis/Stand) behaelt die Unterschriften wie bisher.

Nebenwirkung in den Tests: Sechs Dateien benutzten als Unterschrift eine **erfundene** PNG-Datei
(nur der Magic-Header plus Nullbytes) — die gab es so nie auf einem Handy. Sie bekommen jetzt
eine echte 1x1-PNG.

Wächter: `backend/tests/test_unterschrift_lesbar_20260919.py`.

### Nachpruefung Nr. 144-165 (19.09.2026): Berechtigungen gelten jetzt bis zuletzt

22 Befunde, ein gemeinsames Muster: Eine Berechtigung wurde **einmal** geprueft — beim Einreihen,
beim Anmelden, beim Anzeigen — und danach nie wieder, obwohl zwischen Pruefung und Wirkung Minuten
(Warteschlange) oder Monate (Dokument-Adressen) liegen koennen.

**A. Zugang (144, 145, 164).** `require_active_sub` haengt jetzt an `current_firma` statt an
`current_user`. Damit gelten Firmendokument und Loeschsperre automatisch fuer Vergleich,
Linkpruefung, resolve, ingest, Live-Zaehler, manuelle Suche und die Vertragswege. Vorher konnte ein
Sucher **waehrend der laufenden Firmenloeschung** weiter vergleichen und Links einreihen (die
Loeschkaskade raeumte also gegen laufende Neuanlagen an), und ein Konto mit geloeschter Firma
arbeitete mit einer `dealer_id` weiter, zu der es keine Firma mehr gab.

**B. Warteschlange (146-152, 165).** Der Worker ruft Minuten spaeter extern ab. Vor jedem Abruf
prueft er jetzt je wartendem Konto (`link_jobs.wartender_darf_abrufen`): Konto aktiv, Abo aktiv,
Firma vorhanden und nicht in Loeschung, und die Firma muss die sein, unter der der Auftrag
eingereiht wurde. Ist niemand mehr berechtigt, endet der Auftrag ohne Abruf.

- Gesperrtes Konto, abgelaufenes Abo, geloeschte Firma stoppen den Abruf (146-148).
- Wechselt ein Wartender zwischendurch die Firma, wird der Abruf **nicht** der neuen Firma
  angelastet — sie hat ihn nie eingereiht (149).
- "Abbrechen" raeumt jetzt vollstaendig auf: Der Aussteiger ist nicht mehr `requested_by_user`
  (sonst wurde ausgerechnet er wieder zuerst fuer den Abruf verwendet und mit seinem Tageskontingent
  belastet) und seine Firma faellt aus `dealer_ids`, wenn von dort niemand mehr wartet (150, 151).
- Die Bremse "60/min bzw. 8 gleichzeitig" gilt je Konto: Sie fuehrt jetzt zum naechsten Wartenden,
  statt den gemeinsamen Auftrag komplett zurueckzustellen (152).
- Die Firmenloeschung raeumt die Warteschlange selbst (`link_jobs.firma_austragen`) — der Sucher
  kann waehrend der Loeschung nicht mehr abbrechen, also darf auch nichts mehr laufen (165).

**C. Zwischenspeicher und Beweisdokument (153-155).**

- Verliert eine Anfrage das Single-Flight-Rennen, gibt sie ihre verworfenen Daten **nicht mehr
  zurueck**, sondern wartet kurz auf den Gewinner-Stand (sonst stand im gemeinsamen Speicher ein
  anderer Stand als im Fahrzeug dieses Suchers) (153).
- `/beweise/anfordern` friert nur noch den **rohen Inseratsstand** ein. Der frühere Rueckfall auf
  `fahrzeug.data` konnte haendlerlokale Korrekturen zum gemeinsamen Beweis aller Firmen machen
  (155). Wurde das Inserat nach dem Vergleich neu abgerufen, sagt die Antwort das jetzt
  ausdruecklich (`hinweis`) — das Fahrzeug merkt sich dafuer `inserat_stand_am` (154).

**D. Fahrer-Unterlagen (156-163).** Eine Regel fuer alle Wege
(`routes/drivers.unterlagen_zugriff_oder_404`): storniert = nie, abgeschlossen = nur solange die
Fahrt auch in der App steht (14 Tage nach "abgeholt", sonst 30). Das gilt jetzt fuer Abholauftrag,
Kaufvertrag, Beweisdokument, Snapshot und Abholbericht — vorher kannte nur die Liste diese Fristen,
und der Abholauftrag mit Verkaeufername, Anschrift und Telefon war sogar bei stornierten Fahrten
weiter abrufbar (156, 157). Snapshot und Bericht verlangen jetzt eine **angenommene** Fahrt (158,
159), "angenommen" ist praezise gefasst (unbekannte Alt-Werte kommen nicht mehr durch, 160), der
Deckel von 50 Fahrzeugen im Beweiszugriff ist weg (er sperrte echte Termine aus, 161), datumslose
Fahrten werden sortiert gekappt (162).

**Nr. 163 bewusst anders geloest.** Der Befund las im Fotocleanup "hoechstens 500 je Lauf" und
vermisste die Obergrenze im Code. Dieser Deckel wurde am 14.09.2026 (Runde 10, 3.4) aber
ABSICHTLICH entfernt: Mit ihm blieb bei grossem Rueckstand jeden Lauf ein Rest liegen, und Fotos
waeren ueber ihre Frist hinaus gespeichert geblieben. Der Lauf arbeitet deshalb weiter
vollstaendig, aber in Stapeln (`batch_size`) — der irrefuehrende Satz im Code ist jetzt weg.

Wächter: `backend/tests/test_befunde_144_165_20260919.py`.

### Live-faehig (19.09.2026, Entscheidung Ahmad): Sicherung ohne Platten-Spiegel + sichtbare Maengel

**Sicherung:** Der Datei-Speicher wird nicht mehr in jedes lokale Backup gespiegelt, sondern
Speicher-zu-Speicher in den Sicherungs-Bucket kopiert (`BACKUP_DATEIEN=bucket`, Standard sobald
`BACKUP_S3_BUCKET` gesetzt ist) — Details und Rueckweg im Abschnitt "Backups". Nach dem Rollout
faellt die naechtliche Kopie auf die Platte damit automatisch weg; die 14 lokalen Staende
enthalten nur noch die Datenbank. Zusaetzlich im Cloudflare-Dashboard beim Datei-Bucket die
Versionierung einschalten (manuell, Ahmad).

**Sichtbare Maengel aus der Video-Pruefung vom 17.09.2026:**

1. Marke: "AUTOHANDEL." hiess auf Startseite, Login, Anfrage, Abo-Seite, Fahrer-Login,
   Passwort-Seite und Rechtstexten jetzt **AutoSchnell.**, Fusszeile "© AutoSchnell".
2. Kontaktadresse `support@autohandel.app` (Fusszeile, Team-Seite) — **bleibt offen**, bis Ahmad
   ein echtes Postfach nennt (nicht erfunden).
3. Versand-Vorlagen: alle acht beworbenen Platzhalter werden gefuellt (`{kunde_name}`,
   `{fahrzeug}`, `{marke}`, `{modell}`, `{abholdatum}` als TT.MM.JJJJ mit Uhrzeit,
   `{händler_name}`, `{telefon}`, `{email}`) — vorher ging alles ausser dem Haendlernamen woertlich
   an den Verkaeufer.
4. Fahrer-App: Nach dem unterschriebenen Protokoll (Fahrt = abgeholt) gibt es den Knopf
   "Abhol-Check nachtragen (km, Schluessel, Tank, Fotos)" — 24 h lang, solange noch kein Bericht
   da ist (dieselbe Frist wie `driver_submit_report`). Die Terminliste liefert dafuer
   `bericht_vorhanden` und `status_changed_at`.
5. Abo-Seite: sagt jetzt, dass Kaufvertraege (samt Versand) das Sucher-Abo brauchen — so
   verlangt es der Server (`require_active_sub`); Terminplaner, Freigaben, Bestand und Inserate
   bleiben kostenlos.

Wächter: `backend/tests/test_live_faehig_20260919.py`.

### Inserats-Zwischenspeicher 14 Tage, Vertragsinhaber behaelt seinen Stand (19.09.2026, Wunsch Ahmad)

Der gemeinsame Zwischenspeicher (`listings_cache`) traegt die Inseratsdaten samt **Name, Telefon
und Anschrift des Verkaeufers** — bei Kleinanzeigen meist Privatpersonen. Er stand auf 90 Tagen,
damit ein spaeter angefordertes Beweisdokument noch etwas zu dokumentieren hat. Jetzt:

- **14 Tage** (`LISTING_CACHE_TTL_HOURS=336`, vorher 2160/1440 — im Router, im Cleanup, in
  docker-compose.yml und .env.example stand die Zahl **verschieden**), geloescht spaetestens
  **21 Tage** nach dem Abruf (`INSERATSCACHE_MAX_TAGE`, 14 + 7 Tage Karenz).
- **Wer einen Kaufvertrag macht, behaelt den Stand bei sich:** `POST /contracts` friert den ROHEN
  Inseratsstand aus dem Zwischenspeicher am Vertrag ein (`generated_pdfs.inserat_stand`:
  cache_key, data, fetched_at, source, item_id, url). Bewusst nicht `vehicles.data` — die darf der
  Haendler bearbeiten, ein Beweis zeigt nur das Inserat.
- `POST /beweise/anfordern` greift darauf zurueck, wenn der Zwischenspeicher abgelaufen ist —
  **nur aus dem eigenen Bereich** (`_vertrag_bereich`: Chef = Firma, Sucher = eigene Vertraege),
  juengster Vertrag zuerst. Wer keinen Vertrag hat, bekommt wie bisher "bitte noch einmal
  vergleichen".
- Datenschutzerklaerung (Abschnitt 5) nachgezogen: "max. 21 Tage; zu einem Kaufvertrag gehoerende
  Inseratsdaten so lange wie der Vertrag".
- Nebenwirkung, gewollt: Nach 14 Tagen kostet derselbe Link wieder einen Anbieter-Abruf (vorher
  bis zu 90 Tage gratis aus dem Speicher). Bei 5 % Wiederholungsrate faellt das kaum ins Gewicht;
  wer es anders will, setzt `LISTING_CACHE_TTL_HOURS` hoch — dann aber die Datenschutzerklaerung
  und `INSERATSCACHE_MAX_TAGE` mitziehen.

Wächter: `backend/tests/test_inseratsspeicher_14_tage_20260919.py`.

### Nach der Abholung: neuer Kaufvertrag mit den Daten von vor Ort (19.09.2026, Wunsch Ahmad)

Der Ablauf selbst stand schon und bleibt: Fahrer schickt das Protokoll ab → der Chef sieht es (mit
Zähler im Menü) → Fahrer (`preis_vorschlag`) oder Chef trägt den neuen Preis ein → der Chef gibt
frei → **erst dann** darf vor Ort unterschrieben werden (Abschluss prüft Preis **und** Freigabe-
Stand). Neu ist, was danach passiert:

- **Alle Ausstattungen im Online-Protokoll.** Der Deckel lag bei 20 Zeilen (`[:20]`) — gut
  ausgestattete Wagen haben 30–60, der Rest fiel weg. Jetzt `AUSSTATTUNG_MAX = 80` im
  Online-Protokoll und in beiden PDF-Wegen; die Antwort des Fahrers darf ebenso viele Zeilen
  tragen (`FELD_MAX`, vorher 60 je Abschnitt). Live geprüft: 45 Ausstattungen → 45 Zeilen.
- **Korrigierte Daten wandern in den neuen Vertrag.** `protokoll_vergleich.vertrags_korrekturen()`
  macht aus den Abweichungen Vertragsfelder (Marke, Modell, EZ, FIN, Farbe, Kraftstoff, HU, KM,
  Halter, gewerblich, unfallfrei). Halb getippte Daten, unlesbare Zahlen und leere Eingaben ändern
  **nichts** (im Freigabe-Kasten sieht der Chef sie trotzdem); „Leistung" bleibt außen vor, weil kW
  und PS zwei Felder sind, die der Fahrer nicht getrennt einträgt.
- **Neu aufgenommene Schäden** (`new_damages`) kommen zu den im Vertrag dokumentierten dazu — die
  alten bleiben stehen, sie waren ja bekannt.
- **Alte Fassung = Nachweis, neue = gültig.** Wie bisher wird die bisherige Fassung nach
  `generated_pdf_versions` archiviert und die Versionsnummer erhöht; die Liste zeigt die gültige
  Fassung mit `v2`-Marke und darunter „Frühere Fassungen".
- **Die App fragt nach dem Versand.** Der Vertrag trägt `nach_abholung_versand_offen` und
  `nach_abholung_aenderungen` (Preis alt/neu, geänderte Felder, Zahl neuer Schäden). Das
  Vertragsarchiv zeigt daraufhin „Nach der Abholung neu erstellt — … Die vorherige Fassung bleibt
  als Nachweis erhalten." mit dem Knopf **„Neuen Vertrag senden"** (derselbe Dialog wie beim ersten
  Versand, WhatsApp oder E-Mail). Nach dem Senden verschwindet der Hinweis.
- **Ohne Änderung passiert nichts:** kein neuer Vertrag, kein Archiv-Eintrag, keine Rückfrage.
- Der Merker wird nur bei `grund="abholung_abgeschlossen"` gesetzt — eine bloße Terminverschiebung
  erzeugt weiterhin still eine neue Fassung, ohne nach dem Versand zu fragen.

Wächter: `backend/tests/test_abholung_neuer_vertrag_20260919.py`; Live-Probe (17 Prüfungen) in
`scratchpad/abholung_probe.py`.

### Härtung 19.09.2026: Anmelde-Geheimnis und Sicherungs-Zugangsdaten

Zwei Punkte aus der Sicherheits-Durchsicht, die der Code bisher nicht selbst geprüft hat:

- **`JWT_SECRET` muss mindestens 32 Zeichen haben.** Es unterschreibt jede Anmeldung — wer es
  errät, baut sich Sitzungen für jedes Konto. Geprüft wurde bisher nur, DASS es gesetzt ist.
  Jetzt: in Produktion (`APP_ENV=production`) startet das Backend mit einem kürzeren Wert **nicht
  mehr**, lokal gibt es eine Warnung. Erzeugen mit `openssl rand -hex 32` (64 Zeichen), auf allen
  Servern derselbe Wert — **nach dem Wechsel müssen sich alle neu anmelden** (bestehende Sitzungen
  werden ungültig), deshalb in einer ruhigen Minute wechseln.
- **Eigene Zugangsdaten für die Sicherung.** Nutzt die Sicherung dieselben S3-Schlüssel wie der
  Datei-Speicher, kommt ein gestohlener Schlüssel an die Daten *und* an ihre Sicherungen.
  `/api/ready` meldet jetzt eine Warnung, wenn `BACKUP_S3_ACCESS_KEY` fehlt, und führt
  `backup_eigene_zugangsdaten` als Feld. Nötig ist ein eigener Schlüssel mit "Object Read & Write"
  **nur auf dem Sicherungs-Bucket** — er kommt damit nicht an die Dateien der App.

Zur Einordnung, weil beides in der Sicherheitsliste stand: Die **Zwei-Faktor-Pflicht für den
Super-Admin ist bereits erzwungen** — nicht nur empfohlen. `routes/auth.mfa_pflicht_aktiv()` weist
die Anmeldung in Produktion ohne zweiten Faktor ab, und `/api/ready` meldet ein Konto ohne MFA als
Fehler. Nur `MFA_PFLICHT=false` (Testumgebungen) schaltet das ab.

**Nachtrag 20.09.2026 (zwei berechtigte Einwände):**

- `BACKUP_SNAPSHOT_PFLICHT` fehlte **weiterhin** in `docker-compose.yml` — die Variable wird nicht
  in `backup_mongo.py` gelesen, sondern im importierten `backup_bewertung.py`, und entging damit
  auch dem neuen Test. Der dokumentierte Schalter war im Docker-Betrieb wirkungslos. Jetzt
  durchgereicht und in `.env.example` beschrieben. (Praktische Auswirkung war gering: bei einer
  `MONGO_URL` mit `replicaSet=…` erkennt `snapshot_pflicht()` die Pflicht ohnehin selbst.)
- Der Wächter-Test war zu schwach: Er las nur `backup_mongo.py` und fragte lediglich, ob der Name
  **irgendwo** in der Compose-Datei vorkommt — ein Kommentar hätte gereicht. Jetzt liest er die
  ganze Sicherungs-Kette (`backup_bewertung.py`, `backup_service.py`, `backup_mongo.py`,
  `restore_mongo.py`, `offsite_pruefen.py`) und wertet den `environment:`-Block des
  **backend**-Dienstes aus dem YAML-Baum aus. Gegenprobe gemacht: Zeile entfernt, Kommentar
  stehen gelassen → Test wird rot und nennt die Variable.

Wächter: `backend/tests/test_haertung_20260919.py`.

### Go-Live-Durchsicht 20.09.2026 — 20 Punkte, geprueft und eingeordnet

Ein Pruefer hat den Stand `70ead7c` durchgesehen und 20 Live-Blocker gemeldet. Jeder Punkt wurde
im Code gegengeprueft. Ergebnis: **9 echte Code-/Konfigurationsfehler (behoben)**, **3 bewusste
Entscheidungen Ahmads (kein Fehler)**, **8 Betreiber-Aufgaben** (Rotation, Staging, Lasttest,
Rechtstexte — ausserhalb des Codes).

**Behoben in diesem Stand:**

| # | Befund | Korrektur |
|---|---|---|
| 2 | `DATEN_SCHLUESSEL` erreichte den Container nie | durchgereicht — MFA-Geheimnisse und bekannte Anmelde-IPs hingen sonst weiter an `JWT_SECRET` |
| 20 | `MIN_FREI_MB` erreichte den Container nie | durchgereicht (mit Vorgabewert 500) |
| — | **9 weitere derselben Art**, bei der Suche ueber ALLE Namen gefunden | `ABRUF_JE_KONTO_MINUTE`, `ABRUF_GLEICHZEITIG_JE_KONTO`, `VERGLEICH_JE_KONTO_MINUTE`, `VERSAND_JE_KONTO_10MIN`, `LINK_JOB_SOFORT_MAX`, `LINK_JOB_MAX_ATTEMPTS`, `IMAGE_QUALITY`, `MAX_IMAGE_UPLOAD_BYTES`, `MFA_AUSSTELLER` — alle mit den Code-Standardwerten als Vorgabe (ein leerer Wert haette `int()` zum Absturz gebracht) |
| 4 | Kaeufer-Login schrieb die Sitzung ohne Bedingung | jetzt `sitzungs_bedingung()` wie bei Firma und Fahrer: ein Passwortwechsel waehrend der Anmeldung laesst keine Sitzung mehr entstehen (401) |
| 6 | Vertragsfrist 60 vs. 90 Tage | ueberall **60** (`production_check.py` rechnete mit 90, DEPLOYMENT.md nannte 90) |
| 7 | Sicherung lief immer ohne Schreibpause | **am selben Tag korrigiert** — die Automatik "ohne Replica Set immer `--wartung`" war falsch und ist zurueckgenommen. Begruendung und neuer Stand: Abschnitt *Wartungsmodus (Nr. 64-72)* weiter unten |
| 8 | Nicht stichtagsgenaue Sicherung galt still als gut | `/api/ready` warnt jetzt ausdruecklich und nennt den Weg (Replica Set oder `BACKUP_WARTUNG=true`). Die Bewertung selbst bleibt: ein Einzelserver kann es nicht besser |
| 9 | Sperre nicht pruefbar = "anderer Worker sichert" | `job_lock.acquire(..., fehler_melden=True)`: eine Datenbank-Stoerung ist jetzt ein Fehlschlag → Wiederholung in einer Stunde |
| 10 | Doku verlangte einen "nur schreibenden" Schluessel | falsch — Upload (`head_object`), Rotation (list/delete) und der Papierkorb lesen dort. Ueberall auf **"Object Read & Write", auf den Sicherungs-Bucket eingeschraenkt** korrigiert |

**Bewusste Entscheidungen — kein Fehler:**

- **#5 `VERTRAG_LOESCHUNG_AKTIV=true`:** Ahmads Entscheidung vom 14.09.2026 ("scharf"), in
  `.env.example` mit Begruendung dokumentiert. Der Trockenlauf bleibt per `false` verfuegbar.
- **#12 `w:1` beim Replica Set:** bewusst gewaehlt (2 Datenserver + Arbiter). Das Fenster ist in
  der Replica-Doku beschrieben; `w:majority` waere die Alternative, kostet aber Schreib-Tempo —
  Ahmads Abwaegung.
- **#13 `MARKTPLATZ_AKTIV=false`:** Go-Live-Schalter vom 15.09.2026. Der Marktplatz ist
  absichtlich aus ("Demnaechst verfuegbar"), bis Ahmad ihn freigibt.

**Eingeordnet, aber nicht als Blocker:**

- **#11 Load Balancer prueft `/api/health`, nicht `/api/ready`:** absichtlich — der Rollout
  braucht einen Server, der waehrend des Neubaus aus der Rotation geht, aber nicht bei jeder
  Readiness-Warnung (z. B. offene Betriebsalarme) rausfaellt. `rollout.sh` setzt den Drain-Marker
  selbst; `/api/ready` meldet inhaltliche Probleme an den Betreiber.
- **#14 Kleinanzeigen-Engpass:** Bei Ahmad ist `KLEINANZEIGEN_API_KEY` gesetzt
  (`MAX_CONCURRENT_KLEINANZEIGEN_API=20`); die strenge Bremse von 2 gilt nur fuer den
  Selbst-Abruf ohne API-Schluessel.
- **#1, #15-#19:** Secret-Rotation, echter Provider-Probelauf, Staging-Abnahme, Lasttest,
  Offsite-Pruefung und die Rechtstexte sind Betreiber-Aufgaben aus der eigenen Checkliste —
  sie stehen dort weiter offen und koennen nicht im Code erledigt werden.

Wächter: `backend/tests/test_pruefung_20260920.py` (12 Tests) und die erweiterten Tests in
`test_haertung_20260919.py` — darunter `test_09`, das ab jetzt **jede** in `.env.example`
versprochene und vom Code gelesene Einstellung im `environment:`-Block verlangt.

---

### Wartungsmodus und Datei-Sicherung (Nachpruefung 20.09.2026, Nr. 64-72)

Am Vormittag des 20.09.2026 hatte ich die Schreibpause der naechtlichen Sicherung
**automatisch** eingeschaltet, sobald MongoDB ohne Replica Set laeuft. Die naechste
Durchsicht hat das zerlegt, und sie hatte in jedem Punkt recht. Die Automatik ist
**zurueckgenommen**; der Wartungsmodus ist stattdessen abgesichert worden.

| # | Befund | Korrektur |
|---|---|---|
| 64 | Der Schreibstopp hielt nur HTTP an; Link-, Beweis- und Aufraeum-Worker schrieben weiter — die Sicherung nannte sich trotzdem stichtagsgenau | alle drei Worker fragen jetzt `wartung.aktiv_async()` und pausieren ihren Zyklus |
| 65 | Nach dem Einschalten wurde stur 6 s gewartet | `BACKUP_WARTUNG_WARTEN_S` (Standard **30 s**, Minimum 6); `/api/ready` zeigt unter `schreiber_offen`, wie viele schreibende Anfragen noch laufen |
| 66 | Ohne Besitzer, Ablaufzeit und Waechter konnte ein abgestuerzter Lauf die Plattform **dauerhaft** sperren | der Merker hat `besitzer` + `gilt_bis` (Standard 15 min, waehrend des Laufs jede Minute verlaengert). Abgelaufen = unwirksam; der Serverstart raeumt ihn weg. Aufheben darf nur der Besitzer |
| 67 | `/api/ready` blieb gruen, der Lastverteiler schickte weiter Kunden hin | Restore (`umfang: alles`) ist jetzt ein **Fehler** → der Server faellt aus der Rotation. Eine reine Schreibpause bleibt eine Warnung, weil Lesen weiterlaeuft |
| 68 | Die "Schreibpause" war ein kompletter Ausfall — auch GET, Downloads, Marktplatz | `umfang: "schreiben"` laesst GET/HEAD/OPTIONS durch; nur veraendernde Anfragen bekommen 503. Der Restore setzt weiterhin `umfang: "alles"` |
| 69 | Datenbank und Dateien stammten aus verschiedenen Zeitpunkten (die Pause endete direkt nach dem DB-Dump) | die Pause bleibt bis **nach** der Dateisicherung an |
| 70 | Die Datei-Sicherung hielt nicht fest, welche Dateien zu einem Backup gehoerten | jedes Backup legt `dateien-liste.json.gz` an (Schluessel, Groesse, ETag) und bekommt dafuer eine SHA-256-Pruefsumme im Manifest. `dateien_zurueckkopieren.py --liste <Backup-Ordner>` prueft dagegen und meldet Fehlendes, Abweichendes und spaeter Dazugekommenes (Exit 2) |
| 71 | Papierkorb-Fehler wurden nur geloggt, das Backup meldete trotzdem OK | sie zaehlen jetzt als `fehler` → **BACKUP UNVOLLSTAENDIG** (Exit 2) |
| 72 | Compose/Generator/Doku nannten verschiedene Standardwerte | `BACKUP_S3_KEEP` ueberall **14**, `LINK_JOB_SOFORT_MAX` ueberall **4**, `MAX_IMAGE_UPLOAD_BYTES` ueberall **8 MB** (das sind **8 MB je Foto**; das Limit fuer die ganze Anfrage sind die 25 MB aus `deploy/nginx.conf`) |

**Gegengeprueft und NICHT bestaetigt — Nr. 51 ("nginx blockiert Uploads bei 1 MB"):**
Die Durchsicht sah nur `default.conf.template` und `hinter-loadbalancer.conf.template` —
dort steht tatsaechlich kein `client_max_body_size`. Die Vorgabe steht aber im
`http`-Block von `deploy/nginx.conf` (`client_max_body_size 25m;`), und **genau diese
Datei** wird in den Proxy-Container gehaengt (`./deploy/nginx.conf:/etc/nginx/nginx.conf:ro`).
nginx vererbt `http`-Vorgaben an jeden Server- und Location-Block, die wirksame Grenze ist
also 25 MB, nicht 1 MB. `test_72b_nginx_begrenzt_die_anfragegroesse` haelt beides fest.

**Empfehlung zum Betrieb:** `BACKUP_WARTUNG` bleibt leer. Ahmads Server laufen als
Replica Set (`rs0`) — dort liest die Sicherung alle Collections in EINER Snapshot-Sitzung,
ohne jede Unterbrechung. Die Schreibpause ist nur fuer Einzelserver-Installationen da, und
auch dort eine bewusste Entscheidung.

Waechter: `backend/tests/test_wartung_sicherung_20260920.py` (26 Tests).

---

### Nachpruefung 20.09.2026, Nr. 21-74 — vollstaendige Abarbeitung

Der Bericht hatte 54 Punkte (Nr. 21-74). Stand nach diesem Durchgang:

**Inbetriebnahme (Nr. 24-29, 45/46)** — `fix(inbetriebnahme)`

| # | Befund | Korrektur |
|---|---|---|
| 24/25 | `env_erzeugen.py` schreibt `BITTE-AUSFUELLEN-...`, die Produktionspruefung fragte nur "ist ein Wert da?" | jede Einstellung mit dem Vorlagentext faellt jetzt durch — nicht nur `RESEND_API_KEY`/`APIFY_TOKEN`, sondern **alle** |
| 26 | `.lstrip("https://")` entfernte Einzelzeichen: `shop.example.de` → `op.example.de` | echtes Praefix-Entfernen (`_domain_saeubern`) |
| 27 | Generator setzte still die Load-Balancer-Vorlage (verwirft Direktzugriffe mit 444) | Einzelserver ist Standard, `--hinter-loadbalancer` ist die bewusste Angabe |
| 28 | `MONGO_EXTRA_ARGS` stand nur als Compose-Kommentar, das Runbook verlangte aber `rs.initiate()` | in `.env.example` und im Runbook, mit Gegenprobe `replikat_pruefen.py` |
| 29 | Rollout prueft `/api/ready` — das beweist Erreichbarkeit, nicht Replikation | `deploy/rollout.sh` prueft das Replica Set, sobald `replicaSet=` in der `.env` steht; kaputtes Replikat stoppt den Rollout |
| 45/46 | Halb gesetzte `BACKUP_S3_*`-Zugangsdaten mischten Schluessel und Geheimnis aus zwei Zugaengen | `s3_client()` lehnt das ab, der Startcheck meldet es als Fehler |

**Zahlungen und Zugaenge (Nr. 47-50, 61-63, 73)** — `fix(zahlungen)`

| # | Befund | Korrektur |
|---|---|---|
| 47/48 | Zwischenhaendler-Freischaltung ohne Sperre und ohne Abgleich: zwei Klicks = zwei Zahlungen, eine Verlaengerung | eine Sperre je Konto, Zugang zuerst (mit Abgleich auf den gelesenen Stand), Zahlung idempotent ueber `vorgang_id` |
| 49 | Freischalten und Sperren ohne gemeinsame Sperre | beide nehmen jetzt dieselbe |
| 50 | Herzschlag verlor die Sperre und beendete nur sich selbst | `_Wache.pruefen()` bricht den Vorgang mit 409 ab; eine DB-Stoerung zaehlt **nicht** als Verlust |
| 61 | Firmenzahlung liess sich einem Sucher einer anderen Firma zuordnen | wird gegen die Firma geprueft |
| 62/63 | Deaktivierte Konten konnten bezahlt freigeschaltet werden und blieben gesperrt | 400 mit klarem Text; Sperren bleibt erlaubt |
| 73 | Laufzeitaenderung las EIN Abo, aenderte per `update_many` aber jedes aktive | Sperre + `update_one` auf die gelesene Id, mit Abgleich auf den gelesenen Ablauf |

**Betrieb und Sicherheit (Nr. 54-60, 74)** — `fix(betrieb)`

| # | Befund | Korrektur |
|---|---|---|
| 54 | `/api/ready` gab anonym Schema-Version, freien Speicher, Alarme, Jobs, Super-Admin-Zahlen preis | Einzelheiten nur noch, wenn der **direkte Nachbar die Schleife ist** (so lesen `rollout.sh` und `freigeben.sh` die Begruendung), mit Super-Admin-Token, oder mit der Kennung `X-Ready-Token`, die `scripts/betriebsprobe.py` und der Server beide aus `JWT_SECRET` ableiten (keine zusaetzliche Einstellung). Alle anderen bekommen nur `ready` true/false — der Zustandscode 200/503 bleibt fuer jeden gleich, der Lastverteiler merkt nichts. Bewusst NICHT `rate_limiter.client_ip`: die liest `X-Forwarded-For` und faellt bei einer Kette aus lauter eigenen Vermittlern auf den ersten Eintrag zurueck, den ein Besucher selbst setzen kann |
| 55 | teurer Endpunkt ohne Bremse | `READY_CACHE_S` (5 s) |
| 56 | feste Probedatei `.readiness` → zwei Aufrufe loeschten sie sich gegenseitig | eigener Name je Aufruf (derselbe Fehler steckte auch in `LocalDiskStorage`) |
| 57 | Vorschau-Pfad des Bildproxys entpackte ohne Pixelpruefung | dieselbe Grenze wie im PDF-Pfad, **vor** dem Entpacken |
| 58 | `BILD_PROXY_LIMIT` 1500 zu knapp (30 Sucher x 40 Bilder = 1200) | **3000** |
| 59 | SMTP: jede Ausnahme gab den Idempotenz-Eintrag frei — auch nach Annahme durch den Server | freigegeben wird nur noch bei nachweislicher Nicht-Zustellung; sonst "unklar" und **keine** automatische Wiederholung |
| 60 | S3-Bereitschaft pruefte nur `head_bucket` | zusaetzlich alle 15 min eine echte Schreibprobe |
| 74 | Kleinanzeigen-Abruf mit `follow_redirects=True`: die Adresse wurde erst **nach** dem Abruf geprueft | Weiterleitungen von Hand, jede Stufe **vor** dem Abruf geprueft — wie es der Bild-Proxy laengst macht |

**Oberflaeche (Nr. 39-42, 52/53)** — `fix(oberflaeche)`

| # | Befund | Korrektur |
|---|---|---|
| 39/40/41 | Der 401-Abfaenger loeschte den Token, ohne zu pruefen, ob die Anfrage mit **diesem** Token lief: eine verspaetete 401 warf ein frisch angemeldetes Konto hinaus | `gehoertZumAktuellenToken()` in `lib/api.js` und `BuyerContext` |
| 42 | genau dieser Fall war nirgends geprueft | vier Tests in `api.test.js` |
| 52 | `Inserat.jsx` las bis zu 20 Fotos roh und schickte sie in EINER Anfrage | Verkleinern im Browser (2000 px, entfernt auch Aufnahmeort und Geraet) und Pakete zu vier |
| 53 | serverseitig nur Grenzen je Bild, nicht fuer die Anfrage | hoechstens 8 Bilder und 24 MB je Anfrage |

**Transaktionen, Sperren, Lebenszyklus (Nr. 30-38, 43/44)** — `fix(transaktionen+sperren)`

| # | Befund | Korrektur |
|---|---|---|
| 30-33 | `transaktion()` lief nach JEDEM `PyMongoError` noch einmal ohne Transaktion — auch wenn die Uebergabe geklappt haben konnte (dann sah der Nutzer 409/404, obwohl gespeichert war) | `UnknownTransactionCommitResult` → **nicht** wiederholen, 503 mit klarer Ansage; `TransientTransactionError` → einmal komplett wiederholen (mit Transaktion); sonst wie bisher |
| 34/35 | Sperrverlust wurde nur protokolliert, der Lauf machte weiter — bei Migrationen also zwei Laeufe ueber dieselben Daten | Wache mit `pruefen()`; der Verlierer einer Migrationssperre wird zum Wartenden statt abzubrechen |
| 37/38 | "erledigt" ergab je nach Weg einen anderen Fahrzeugzustand | eine Tabelle fuer Buero und Fahrer-App (`lifecycle.TERMINSTATUS_FAHRZEUGZUSTAND`) |
| 43/44 | alte Stripe-Indizes konnten ein Deployment stoppen, obwohl es keinen Stripe-Weg mehr gibt | Warnung + Betriebsalarm statt Startverbot; ein Test prueft gegen, dass wirklich niemand mehr `session_id` schreibt |

**Gegengeprueft und NICHT bestaetigt:**

- **Nr. 51** ("nginx blockiert Uploads bei 1 MB"): `client_max_body_size 25m` steht im
  `http`-Block von `deploy/nginx.conf`, und **genau diese Datei** wird in den Container
  gehaengt. nginx vererbt das an jeden Server-Block — die wirksame Grenze ist 25 MB.
  Die Durchsicht hatte nur die Server-Block-Vorlagen angesehen.
- **Nr. 36** (Beweis-Index "nicht fail-closed"): in Produktion wird seit Befund 123
  (16.09.2026) **ohne** den Unique-Index gar kein Beweisdokument mehr vorgemerkt. Die
  zitierte Zeile "doppelte Dokumente moeglich" gilt nur ausserhalb von Produktion.

Beide Gegenproben stehen als Test fest (`test_72b`, `test_36`), damit die Lage nicht
unbemerkt kippt.

---

### Nachpruefung 20.09.2026, Nr. 75-81 — Restore, Abholung, Compose

| # | Befund | Korrektur |
|---|---|---|
| 75 | Der **dokumentierte** Restore-Befehl liess Collections stehen, die es nur live gibt (weil sie nach dem Backup entstanden sind) — genau der Mischstand, den Doku und Dateikopf mit "nie gemischt" ausschliessen. Weggeraeumt hat sie nur das nirgends dokumentierte `--exakt` | `--exakt` ist jetzt das **Standardverhalten**: solche Collections wandern nach `<db>__vorher_<zeit>` (geloescht wird nichts). Wer den Mischstand braucht, haengt `--zusaetzliche-behalten` an und bekommt eine laute Warnung statt eines beilaeufigen "Hinweis" |
| 76 | Scheiterte dabei das Verschieben einer Collection, wurde das nur gedruckt — die Liste der Extras wurde danach pauschal geleert, und der Lauf meldete Exit 0 mit `RESTORE OK`, obwohl der verlangte Stand nicht erreicht war | jeder Fehlschlag zaehlt, wird einzeln genannt und beendet den Lauf mit **Exit 1** (`RESTORE UNVOLLSTAENDIG`). Nur wirklich Verschobenes faellt aus der Liste |
| 77 | S3-Objekte wurden **vor** dem Umschalten direkt im Live-Eimer ueberschrieben. Scheiterte danach das Umschalten, drehte der Rollback Datenbank und lokale Ordner zurueck — die S3-Objekte nicht. Die weiterlaufende alte Datenbank zeigte dann auf zurueckgespielte Dateien | vom bisherigen Stand jedes ueberschriebenen Objekts wird vorher eine Kopie unter `restore-vorher/<zeit>/` im selben Eimer angelegt (Server zu Server, ohne Herunterladen). Der Rollback holt sie zurueck und loescht, was der Restore neu angelegt hat; die Kopien verschwinden erst nach einem **gelungenen** Restore |
| 78 | Das Protokoll wurde auf `final` gesetzt und der Nacharbeits-Merker entfernt, **bevor** der Vertrag neu erzeugt wurde. Starb der Prozess dazwischen, heilte die Selbstheilung Termin, Preis und Lebenszyklus — die Vertragsneuerzeugung kannte sie gar nicht. Ergebnis: finales Protokoll, abgeholtes Fahrzeug, dauerhaft alte Vertragsfassung | der Merker faellt erst nach der Vertragsneuerzeugung, und der Selbstheilungspfad erzeugt den Vertrag mit (samt Vor-Ort-Korrekturen und neuen Schaeden) |
| 79 | Scheiterte die neue Vertrags-PDF, fing `regenerate_contract_for_pickup()` die Ausnahme selbst ab und lieferte `False`. Der Aufrufer legte den Betriebsalarm aber nur bei einer **durchgereichten** Ausnahme an — ein `False` rutschte still durch, ohne Alarm und ohne Nachholversuch | `False` loest jetzt denselben Alarm aus. "Es gab nichts zu aendern" bleibt davon unberuehrt (der Fall steigt vor dem `try` aus) |
| 80 | Der Nachholjob lud nur Preis und Sondervereinbarung. Bei einer Preisaenderung entstand dadurch eine neue, inhaltlich **unvollstaendige** Fassung und der Alarm wurde geschlossen; bestand die Aenderung nur aus Fahrzeugkorrekturen oder neuen Schaeden, konnte er gar keine Fassung erzeugen und der Alarm blieb ewig offen | der Nachholer laedt das ganze Protokoll und berechnet die Korrekturen genauso wie der Normalpfad |
| 81 | `BEWEIS_AUTOMATISCH`, `BILD_PROXY_HOSTS`, `VERTRAG_LINK_TAGE`, `MOBILE_API_BASE` erreichten den Container nicht — der dokumentierte Beweis-Rollback-Schalter und eine kuerzere Vertragslink-Frist blieben in der Server-`.env` wirkungslos | durchgereicht. Die systematische Suche fand **20** solche Namen, nicht 4; alle sind jetzt drin (ausser `SSL_VERIFY`, das im Container bewusst nicht einstellbar sein soll) |

**Und der Waechter hat diesmal selbst versagt.** `test_09` verglich die Schnittmenge
aus `.env.example` und Code gegen den `environment:`-Block. Die vier Namen aus Nr. 81
stehen aber gar nicht in `.env.example` — nur in DEPLOYMENT.md und im Code. Der Test
konnte sie also grundsaetzlich nicht sehen. Er sucht jetzt **vom Code aus**: was
`os.environ.get(...)` liest, muss durchgereicht sein oder mit Begruendung in
`NICHT_IM_CONTAINER` stehen. Ein zweiter Test (`test_09b`) prueft, dass diese
Ausnahmeliste nicht zum Freibrief wird.

**Nebenbefund aus Ahmads Betriebsprobe:** Die drei `DeprecationWarning`-Zeilen mitten
im Bericht sind weg (`datetime.utcnow()`, `ssl.PROTOCOL_TLSv1/-1_1`). Dabei fiel auf,
dass der TLS-1.0/1.1-Test ungenau war: konnte schon der **eigene Client** das alte
Protokoll nicht mehr anbieten, meldete die Probe "Server lehnt ab" — geprueft war in
Wahrheit nichts. Aufbau des Kontexts und Handschlag sind jetzt getrennt; im ersten
Fall steht "nicht pruefbar" statt eines falschen OK.

Waechter: `backend/tests/test_restore_abholung_20260920.py` (20 Tests).

---

### Betriebsmeldungen per E-Mail (20.09.2026)

Bis heute landete jeder Fehler **nur in der Datenbank**: ein 500er in `error_logs`,
ein schwerer Vorgang zusaetzlich als Betriebsalarm. Sichtbar war beides auf der
Betriebs-Seite und in `/api/ready` — aber **niemand erfuhr davon**. Ging Samstagnacht
etwas kaputt, wusste es bis zum naechsten Hinsehen keiner.

Jetzt gehen zwei Meldungen an `BETRIEB_MELDUNG_AN`:

| | Wann | Inhalt |
|---|---|---|
| **Sofortmeldung** | sobald ein **neuer** Betriebsalarm entsteht | Typ, betroffener Datensatz, Einzelheiten, seit wann, wie oft |
| **Anfragen** | sobald eine neue Freischaltungs-Anfrage eingeht | Art (neue Firma, Zwischenhaendler, Sucher-Abo, Marktplatz), Firma, Kunden-/Kontonummer, Wunsch, **E-Mail und Telefon**, Nachricht |
| **Tagesbericht** | taeglich um `BETRIEB_TAGESBERICHT_STUNDE` (8 Uhr) | offene Alarme, Fehler der letzten 24 h mit den haeufigsten Wegen, **offene Anfragen**, Zustand der Sicherung, haengende Abrufe |

Die Anfragen-Mail ist bewusst von den Alarmen getrennt: eine Anfrage ist **kein
Fehler, sondern Geschaeft** — jemand will zahlen. Sie bringt die Kontaktdaten gleich
mit, damit sich zurueckrufen laesst, ohne sich erst anzumelden. Im Tagesbericht
drehen offene Anfragen den Betreff **nicht** auf "Auffaelligkeiten"; sie stehen dort
nur, damit eine uebersehene wieder auftaucht.

**Der Tagesbericht kommt auch, wenn alles in Ordnung ist.** Das ist Absicht: eine
Plattform, die schweigt, ist von einer toten nicht zu unterscheiden. Bleibt die Mail
aus, stimmt etwas nicht.

**Einstellungen** (alle in `docker-compose.yml` durchgereicht):

```
BETRIEB_MELDUNG_AN=ahmadfkh006@gmail.com   # LEER = alles aus
BETRIEB_MELDUNG_SOFORT_MIN=10              # Sammelfrist in Minuten
BETRIEB_TAGESBERICHT_STUNDE=8              # -1 schaltet nur den Bericht ab
```

Setzen auf beiden Servern:
```bash
sh deploy/env_setzen.sh BETRIEB_MELDUNG_AN=ahmadfkh006@gmail.com
```

Genau **eine** Adresse, ohne Leerzeichen und ohne Anführungszeichen. `env_setzen.sh` lässt die
`.env` dabei nur für root lesbar (`chmod 600`) — aber erst in der Fassung ab dem 21.09.2026.
**Server mit Stand vor dem 21.09.2026: danach einmal `chmod 600 .env .env.bak-*`** — bis zum
`git pull` liegt dort noch das alte Skript, und das hinterlässt `.env` und Sicherung für jeden
Benutzer lesbar. Ab dem nächsten `sh deploy/rollout.sh` erledigt das der Rollout nach dem Pull
selbst. Die Adresse wirkt erst nach einem **Neustart des Backends**:
der Meldedienst startet nur, wenn sie beim Start schon da ist. Also zusammen mit dem nächsten
`sh deploy/rollout.sh` (prod2 mit `ERSTER_SERVER=1`, dann prod1) — oder, wenn sich nur der Wert
ändert, Server für Server über den Drain (Einzelheiten unter „Tageslimit je Konto und Werte in
der .env setzen“):

```bash
cd /opt/autoschnell
grep -q '^COMPOSE_FILE=' .env || export COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.replica.yml
touch deploy/drain/aktiv
sleep 60
docker compose up -d
sh deploy/freigeben.sh     # "Drain aufgehoben ..."; meldet es "nicht bereit": 30 s warten, erneut
```

Die `grep`-Zeile nicht weglassen: fehlt `COMPOSE_FILE` in der `.env`, erzeugt
`docker compose up -d` mongo und backend ohne die Replica-Set-Ergänzung neu — das Mitglied fällt
aus `rs0`, `freigeben.sh` wartet vergeblich auf `/api/ready` (Vorfall 07.09.2026).
Erst danach, nach weiteren 60 s, der andere Server. Nie beide gleichzeitig.

**Prüfen per Knopf (21.09.2026):** Admin → Betrieb zeigt „Meldungen gehen an: <adresse>“ —
ist `BETRIEB_MELDUNG_AN` leer, steht dort weiter der gelbe Hinweis „Alarme werden an niemanden
per E-Mail gemeldet …“. Daneben schickt **„Testmail senden“** sofort eine kurze Probe-Mail über
denselben Weg wie Alarme und Tagesbericht (`POST /api/admin/betrieb/testmail`, nur Super-Admin,
höchstens eine je Minute und Prozess). Man muss also nicht mehr auf den Tagesbericht um 8 Uhr
warten. Antworten:

| Meldung | Bedeutung |
|---|---|
| „Testmail an … verschickt“ | Anbieter hat angenommen — Posteingang und Spam-Ordner ansehen |
| 400 „Keine Adresse eingetragen (BETRIEB_MELDUNG_AN)“ | Wert fehlt auf diesem Server (oder Backend nicht neu gestartet) |
| 400 „… keine gültige E-Mail-Adresse …“ | mehrere Adressen, Leerzeichen oder Anführungszeichen im Wert |
| 503 „E-Mail-Versand ist nicht eingerichtet …“ | `RESEND_API_KEY` bzw. `SMTP_*` fehlt — dann kommen auch keine Alarme |
| rot „Testmail an … nicht zugestellt: …“ | der Anbieter hat abgelehnt; der Grund steht dabei (z. B. Absender-Domain bei Resend nicht verifiziert) |
| rot „Der Mail-Anbieter hat nicht innerhalb von 45 s geantwortet …“ | Ausgang unklar — die Mail kann noch ankommen; Posteingang ansehen, Server-Protokoll prüfen |
| 429 „Höchstens eine Testmail pro Minute …“ | kurz warten |

Die beiden roten Meldungen kommen seit der Prüfung vom 21.09.2026 als Antwort **200** mit
`{"ok": false, "grund": "…"}` — nicht mehr als 502. Cloudflare ersetzt 502/504 vom Server durch
eine eigene Fehlerseite; im Browser stand dann nur „Request failed with status code 502“ und der
Grund fehlte. Der Versand ist auf 45 s begrenzt (vorher bis zu ~3 Minuten bei einem nicht
erreichbaren Resend — länger, als Browser und Cloudflare warten).

Im Testbetrieb (`MOCK_PROVIDER_FETCH=true`) geht keine Mail hinaus; die Seite meldet dann
„Testmodus“. Der Load Balancer verteilt die Klicks auf beide Server — ein paarmal
„Aktualisieren“ klicken und prüfen, dass beide dieselbe Adresse zeigen.

**Warum es nicht achtmal kommt:** zwei Server mit je vier Prozessen. Jede Runde laeuft
unter einer Job-Sperre, der Tagesbericht unter einer Tagessperre (20 h) wie die
Sicherung. **Scheitert der Tagesbericht** (21.09.2026), wird seine Tagessperre auf
45 Minuten verkuerzt: hoechstens vier Versuche am Tag, jeder mit eigenem
Idempotency-Key — vorher kam der naechste Versuch erst am folgenden Morgen. Nach einem
erfolgreichen Versand bleibt es bei genau einem Bericht je Tag.
Jeder gemeldete Alarm bekommt `gemeldet_am` — scheitert der Versand,
bleibt die Markierung aus und die naechste Runde versucht es erneut. Ein bereits
gemeldeter Alarm meldet sich **nicht** noch einmal, auch wenn er oefter auftritt
(`betrieb.alarm` zaehlt dann nur `anzahl` hoch).

**Stolperstein beim Bauen, der fast durchgerutscht waere:** `/api/ready` wertet einen
beendeten Hintergrundjob als **Fehler** (503). Der erste Entwurf beendete den Dienst,
wenn keine Adresse gesetzt ist — der Server waere aus dem Lastverteiler geflogen, nur
weil niemand Meldungen haben will. Jetzt zweifach abgesichert: `server.py` startet den
Dienst ohne Adresse gar nicht erst, und die Schleife beendet sich auch dann nicht.

Waechter: `backend/tests/test_betriebsmeldung_20260920.py` (30 Tests),
`backend/tests/test_betrieb_testmail_20260921.py` (Testmail, Wiederholung des
Tagesberichts, Rechte der `.env`).

---

### Probe-Abo und Blaettern in der Vertragsliste (20.09.2026, Wunsch Ahmad)

**Probe-Abo.** Neben "150 €/M" und "1.500 €/J" gibt es auf der Firmenseite zwei
weitere Knoepfe: **Probe 3 T** und **Probe 5 T**. Kostenlos, laeuft nach 3 bzw. 5
Tagen ab — danach sperrt die Abo-Pruefung die Sucher-Funktion automatisch, genau wie
bei jedem abgelaufenen Abo. Das Konto selbst (Anmeldung, Bestand, Vertraege) bleibt
erhalten; gesperrt wird nur die kostenpflichtige Sucher-Funktion. Das ist dieselbe
Regel wie bei "Abo aufheben" und seit 09/2026 so gewollt.

Drei Vorkehrungen, ohne die es schiefgegangen waere:

| | |
|---|---|
| **Ein bezahltes Abo wird nie ersetzt** | `_abo_vorgang_ausfuehren` ersetzt ALLE bisherigen Abos durch das neue. Eine Probe auf ein laufendes Jahr haette ein bezahltes Jahr durch drei Tage ersetzt. Die Route lehnt das jetzt mit 400 ab und nennt Plan und Ablauf des laufenden Abos |
| **Die Probe beginnt JETZT** | nicht am Ende einer Restlaufzeit — sonst ergaeben zwei Proben sechs Tage |
| **Der Sucher kann sie nicht selbst anfragen** | sonst holte sich jeder alle drei Tage neue drei Tage. `ANFRAGBARE_PLANS` enthaelt sie nicht, und `/dealer/sucher-plans` zeigt sie der Firma gar nicht |

Kein Betrag und kein eigenes Ablaufdatum: beides lehnt die Route ab, die Laufzeit
entscheidet der Plan. In den Zahlungen erscheint die Probe mit **0,00 €** und
Zahlungsart **`probe`** — getrennt von "Kulanz", damit die Abrechnung sauber bleibt.

Wichtig fuer die Wirkung: `probe3`/`probe5` stehen auch in `deps.ABO_PLAENE_ERLAUBT`.
Ohne diesen Eintrag waere das Abo zwar angelegt, gaebe aber **keinen Zugang** (der
Plan gaelte als "ungueltig").

**Vertragsliste.** Die Firmenseite laedt jetzt **20 Vertraege** und darunter einen
Knopf "Weitere 20 anzeigen"; die schon geladenen bleiben stehen, die naechsten kommen
darunter dazu. Der Zaehler oben zeigt die Gesamtzahl, daneben steht, wie viele davon
geladen sind. Die Obergrenze bleibt bei **2.000** (`ADMIN_VERTRAEGE_MAX`) — am Ende
verschwindet der Knopf und ein Hinweis nennt die wahre Zahl, statt sie still zu
ziehen. Die Schnittstelle: `GET /api/admin/users/{id}/contracts?seite=&limit=`
(Standard 20) liefert zusaetzlich `gesamt`, `weitere` und `seite`.

Waechter: `backend/tests/test_probeabo_vertragsliste_20260920.py` (15 Tests).

---

### Pruefbericht 20.09.2026 (P0/P1) — drei bestaetigt, drei schon erledigt

| # | Befund | Stand |
|---|---|---|
| **P0** | Zweites/aelteres dealer-Konto bekam Chef-Rechte | **bestaetigt und behoben** |
| **P1** | Gleichzeitige Vertragserstellung scheitert sichtbar mit 503 | **bestaetigt und behoben** |
| **P1** | Fahrzeugstatus nach Vertragsloeschung dauerhaft falsch | **bestaetigt und behoben** |
| P1 | Compose reicht nicht alle Limits durch | schon erledigt (Nr. 21-23 + Nr. 81) |
| P1 | Backup ohne harten Snapshot | trifft diese Installation nicht (Replica Set) |
| P1 | Backup-Lock-Fehler galt als "anderer Worker" | schon erledigt (Nr. 9) |

**P0 — der kritischste.** `current_chef` prueft seit dem 15.09.2026 richtig gegen
`dealers.user_id`. Sechs andere Stellen fragten aber nur `role == "dealer"` und
behandelten damit **jedes** dealer-Konto der Firma als Chef:

| Weg | Was ein zweites Chef-Konto konnte |
|---|---|
| `PUT /dealer/settings` | firmenweite Vorgaben ueberschreiben |
| `POST /dealer/logo` | Firmenlogo aller austauschen |
| `POST /dealer/subscription/cancel` | **das Abo der Firma kuendigen** |
| `PUT /dealer/active-profile` | Firmenprofil umschalten |
| Terminliste | Fahrer-ID und E-Mail sehen |
| Fahrerliste | vollstaendige Fahrerdaten sehen |

Die Sucher- und Abo-**Verwaltung** war nie betroffen: `current_haendler` ist
`current_chef`. Neu ist `deps.ist_haupt_chef(user)` — dieselbe Regel wie
`current_chef`, nur als Ja/Nein statt als 403, fail-closed und an allen sechs
Stellen verwendet.

Wie entsteht ueberhaupt ein zweites dealer-Konto? Im Normalbetrieb **gar nicht**:
die Anlage legt genau eines an, und der Chefwechsel stuft unter einer Sperre alle
anderen zu Suchern herab. Es bleiben zwei echte Wege — ein Chefwechsel, der zwischen
den Schritten abbricht, und Altbestand von vor dem 15.09.2026. Genau dafuer ist die
Pruefung da.

**P1 Vertragssperre.** `auto_daten.vertrag_sperre()` wartet 6 s und wirft dann
`SperreBelegt` -> 503. An dieser Stelle steht fest, dass **nichts** geschrieben wurde
(die Sperre kam nie zustande) — ein zweiter Versuch ist also gefahrlos. Der Server
sagt das jetzt mit `X-Wiederholen: 1`, und die Oberflaeche wiederholt bis zu zweimal
nach `Retry-After`. **Nur bei dieser Kopfzeile**: ein beliebiger 503 wird nie
wiederholt, sonst entstuende beim Vertragsanlegen ein zweiter Vertrag.

**P1 Fahrzeugstatus.** `fahrzeug_status_aggregieren()` wirft ausdruecklich **nie** —
sie liefert bei einem CAS- oder Datenbank-Konflikt ein stilles `None`. Der Loeschpfad
achtete aber nur auf eine Ausnahme. Ergebnis konnte sein: Vertrag weg, Kaufvorgang
storniert, Fahrzeug weiter auf "gekauft". Jetzt wird der Rueckgabewert geprueft und
ein Betriebsalarm nennt genau die betroffenen Fahrzeuge — so, wie es die
Vertrags-Nacharbeit 250 Zeilen weiter oben seit Phase 2 macht.

Waechter: `backend/tests/test_pruefbericht_p0p1_20260920.py` (14) und
sechs weitere in `frontend/src/lib/api.test.js`.

### Notfall: Betreiber-Passwort vergessen (Rollenprüfung 22.09.2026, RP-551)

Bisher gab es dafür **keinen** Weg außer einem Eingriff in die Datenbank: der
Admin-Reset ist für den Super-Admin gesperrt, `/admin/me/password` verlangt das
alte Passwort, und ein neues `SUPER_ADMIN_PASSWORD` in der `.env` wird beim
Start bewusst nicht übernommen. Jetzt auf einem der Server:

```bash
docker compose exec backend python scripts/betreiber_passwort_setzen.py --konto <SUPER_ADMIN_USERNAME> --ja
```

Das Skript fragt das neue Passwort zweimal verdeckt ab (es steht nie in der
Befehlszeile), prüft dieselbe Passwortregel wie überall, beendet die laufende
Sitzung, hebt die Anmeldesperre des Kontos auf und schreibt einen Eintrag ins
Aktivitätsprotokoll (`auth.passwort.gesetzt.betreiber_konsole`). Die
Zwei-Faktor-Anmeldung bleibt unverändert; ist auch das Handy weg, zusätzlich
`scripts/mfa_pruefen.py --konto <name> --abschalten --ja`. Ohne `--ja` ändert
das Skript nichts. Stimmt das neue Passwort nicht mit `SUPER_ADMIN_PASSWORD`
in der `.env` überein, meldet der nächste Start das als Alarm — die `.env` dann
bei Gelegenheit angleichen.

### Rollenprüfung 22.09.2026 — Betrieb (Aufräumlauf, Sicherung, Speicher)

| Nr. | Was war | Was jetzt gilt |
|---|---|---|
| RP-243/394 | Ein Fehler in EINEM Schritt des stündlichen Aufräumlaufs brach alle folgenden ab (Fristlöschung, Nacharbeit, Storage-Nachholung …) — nur im Log | jeder Schritt hat einen eigenen Fehlerfang und Betriebsalarm `aufraeumschritt_fehlgeschlagen` (Bezug = Schritt); der Stand des letzten Laufs steht in `system_reports` (`typ: aufraeumlauf`), `/api/ready` warnt nach `AUFRAEUMLAUF_WARN_H` (3 h) ohne vollständigen Lauf |
| RP-245/396 | eine Schreibpause (Sicherung/Restore) hielt einen laufenden Aufräumlauf nicht an | der Lauf prüft vor jedem Schritt die Schreibpause und hält an; er zählt als Schreiber (die Sicherung wartet auf ihn), und der Restore wartet auf ihn |
| RP-246/397 | `wartung.setzen()` überschrieb einen fremden Merker (Sicherung über Restore) | nur noch, wenn kein fremder gültiger Merker steht; die Sicherung startet während eines Restores nicht |
| RP-073/172 | der Nachholer „Vertrag nach Abholung“ erzeugte den Vertrag mit der ABGELÖSTEN Protokollversion neu | er nimmt die aktuelle finale Version des Termins |
| RP-084/183 | ein nach 21 Tagen abgelaufenes Inserat verschwand ohne Status-Prüfung, das Fahrzeug blieb „veröffentlicht“ und ließ sich nie wieder inserieren | Ende per Compare-and-Set, Kaufanfragen mit Grund `inserat_abgelaufen` beendet, Fahrzeug zurück in den Bestand (frische 50-Tage-Frist); ein Nachholer setzt alte hängengebliebene Fahrzeuge zurück |
| RP-244/395 | ein Vertrag, dessen Abholung (ohne Protokoll) gerade erst abgeschlossen wurde, wurde bei über 60 Tage altem Vertrag sofort gelöscht | die Frist läuft ab dem Abschluss der Abholung (Termin „abgeholt/erledigt“ bzw. Kaufvorgang „abgeholt“) — die 60 Tage selbst sind unverändert |
| RP-248/399 | Personendaten OFFENER Termine ohne Vertrag wurden nach Abhol-/Anlagedatum gelöscht | nur geschlossene Termine, Stichtag ist der Abschluss |
| RP-513 | die akzeptierte Anfrage einer noch laufenden Reservierung wurde nach 60 Tagen gelöscht | bleibt, solange das Inserat reserviert ist |
| RP-544/545 | der Restore scheiterte an TTL-Indexen und hielt die ganze Datenbank im Speicher | siehe „Restore“ oben |
| RP-547 | eine fehlende Datei im Objektspeicher gab 500 (und je Aufruf einen Traceback in `error_logs`) | 404; Backend-Fehler werden wie Browser-Fehler zusammengefasst und durch `ERROR_LOG_MAX` begrenzt |
| RP-550 | hängender R2 blockierte Anmeldung, PDF und `/api/ready` | Zeitlimits (`S3_VERBINDUNG_TIMEOUT_S` 5, `S3_LESE_TIMEOUT_S` 30, `S3_VERSUCHE` 2), eigener Thread-Pool für Dateizugriffe (`SPEICHER_THREADS` 16), `/api/ready` wartet höchstens 5 s auf den Speicher. Die Sicherungsskripte (Offsite-Upload, Datei-Sicherung, `offsite_pruefen.py`) laufen bewusst mit eigenen, großzügigeren Werten (`BACKUP_S3_VERBINDUNG_TIMEOUT_S` 10, `BACKUP_S3_LESE_TIMEOUT_S` 120, `BACKUP_S3_VERSUCHE` 5) |
| RP-552 | fällt prod2 länger aus, froren die Sicherungen still auf den Ausfallzeitpunkt ein | siehe „prod2 länger weg“ |
| RP-559 | alte Docker-Images wurden nie entfernt | `rollout.sh` räumt nach Erfolg auf |
| RP-548/560 | `env_erzeugen.py` erzeugte eine `.env`, an der die Startprüfung scheiterte, und warf mit `--vorlage` Werte weg | klare Platzhalter (`APIFY_TOKEN`, `BACKUP_S3_BUCKET`), `DATEN_SCHLUESSEL` wird erzeugt, jeder Wert der Vorlage gewinnt, unbekannte Schlüssel stehen unter „Übernommen aus der Vorlage“ |
| RP-249/400 | Betriebsmeldungen: Sammelfrist durch sofortiges Freigeben der Sperre ausgehebelt, Zeiten in Serverzeit (ohne tzdata: UTC) | Sperre läuft mit der Sammelfrist ab; alle Zeiten und die Stunde des Tagesberichts in deutscher Zeit, gekennzeichnet |
| RP-233/384 | `/api/ready` gab Betriebsdaten auch an ein MFA-Zwischen-Token oder ein abgemeldetes Token | nur noch mit gültiger Sitzung |
| RP-543 | eine Neuinstallation ließ den ersten Betreiber nicht herein | 60 Minuten Gnadenfrist ohne Zwei-Faktor für das frisch angelegte Konto |

**Zweite Welle (Übergaben anderer Teams an Betrieb, 22.09.2026):**

| Nr. | Was war | Was jetzt gilt |
|---|---|---|
| RP-083/182 | zwei parallele „Inserat anlegen“ ergaben zwei aktive Inserate für dasselbe Fahrzeug | Teil-Unique-Index `resale_listings.ein_aktives_je_fahrzeug` (Firma + Fahrzeug, Status entwurf … zurückgezogen). Weich: Altdubletten werden **nicht** gelöscht, sondern als Alarm `inserat_dubletten_je_fahrzeug` gemeldet (überzählige Inserate im Editor löschen, beim nächsten Start greift der Index) |
| RP-046/145/152 | ein zweites aktives Firmen-Abo verdrängte still das ältere | Teil-Unique-Index `subscriptions.ein_aktives_firmen_abo_je_firma` (`art: firma`); Migration 11 kennzeichnet Altbestand, Dubletten → Alarm `mehrfache_aktive_firmen_abos`. Mehr als ein Chefkonto je Firma meldet der Aufräumlauf als Alarm `mehrere_chefkonten` (kein Index: er würde den Chefwechsel blockieren) |
| RP-066/165 | ein wiederholter Abholbericht (Netzabbruch) konnte doppelt gespeichert werden | Teil-Unique-Index `pickup_reports.bericht_idempotenz` (Termin + `client_bericht_id`, weich) |
| RP-223/374 | gleich geschlossen angelegte Termine wurden nie aufgeräumt | Migration 12 setzt bei solchen Altterminen `abgeschlossen_seit`/`status_changed_at` auf den Anlagezeitpunkt (Merker `abschluss_zeit_nachgetragen`) |
| RP-532 | eigene Fotos von Altinseraten (Fotomodus „einkauf“) waren unsichtbar | Migration 13 stellt diese Inserate auf „beide“ |
| RP-517 | nach der Freigabe einer Käufer-Reservierung durch den Betreiber löschte der Aufräumlauf ein über 21 Tage altes Inserat binnen einer Stunde | die Laufzeit zählt ab dem späteren von erster Veröffentlichung und `wieder_veroeffentlicht_am` |
| RP-265/015, RP-080/179 | der Aufräumlauf setzte bei Terminen ohne Vertrag den Fahrzeugstatus auch über laufende Käufe von Kollegen hinweg; gesperrte Fahrer blieben Fahrten zugeteilt | nur ohne Kaufvorgänge am Fahrzeug (sonst Zusammenfassung), dieselbe Status-Tabelle wie Büro und Fahrer-App; Fahrten gesperrter oder gelöschter Fahrerkonten verlieren die Zuweisung |
| RP-098 Nr. 8 | Inseratsfotos im Editor waren nach einer Stunde Fehlbilder | `DATEI_LINK_TTL_INSERAT_SEKUNDEN` (Standard 24 h) für `resale/` |
| RP-549/553 | ein vertippter Apify-Actor-Name ließ jeden neuen Link scheitern | der Produktions-Check warnt bei Namen ohne `~`/`/`; `APIFY_MEMORY_MB`, `APIFY_MOBILE_BUILD`, `APIFY_AUTOSCOUT_BUILD` in `.env.example` |
| RP-546, RP-135 | verlängerte Sitzung und deutsche Längenfehler | CORS gibt `X-Neues-Token` frei; 422-Meldungen zu Längengrenzen kommen deutsch (z. B. „Beschreibung: höchstens 500 Zeichen“) |
| RP-249/400 | Zeitzonen ohne tzdata | `tzdata` steht jetzt in `backend/requirements.txt` (Image) |

Nach dem Rollout in **/admin/betrieb** nach `inserat_dubletten_je_fahrzeug`, `mehrfache_aktive_firmen_abos` und `mehrere_chefkonten` sehen — alle drei ändern nichts selbst, sie zeigen nur Altbestand, der von Hand bereinigt werden muss.
