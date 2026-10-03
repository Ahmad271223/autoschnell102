#!/bin/sh
# Lasttest auf echter Server-Hardware (prod2), ohne die Produktion zu beruehren.
#
# Warum: Die Lasttests vom August liefen auf einem Windows-PC. Der
# Pruefbericht nannte "Linux-Staging: noch nicht geprueft". prod2 ist
# baugleich mit prod1 — Zahlen von dort gelten fuer die Produktion.
#
# Was das Skript tut:
#   1. baut das Backend-Image aus dem aktuellen Stand
#   2. startet einen WEGWERF-Stack: eigene MongoDB (ohne Replikat, ohne
#      Passwort, nur im eigenen Docker-Netz), eigenes Backend mit
#      Anbieter-Attrappe (MOCK_PROVIDER_FETCH=true) — es entsteht KEIN
#      echter Anbieter-Verkehr und KEINE Kosten
#   3. laesst die Lasttest-Skripte laufen (Matrix T1,T2,T3,T8,T9 + Stoss)
#   4. raeumt alles wieder weg; die Ergebnisse bleiben unter
#      docs/lasttests/ auf dem Server
#
# Die Produktions-Datenbank (Replikat-Mitglied auf prod2) wird nicht
# angefasst: der Wegwerf-Stack hat ein eigenes Netz und eine eigene
# MongoDB.
#
# Konten (Kontonummer, 13.09.2026): Eine Selbstregistrierung gibt es nicht
# mehr. Die Lasttests legen in der Wegwerf-MongoDB einen eigenen Super-Admin
# an, erzeugen darueber Firmen, Sucher, Fahrer und Kaeufer und melden sie per
# Kontonummer an (backend/scripts/lasttest_konten.py). SUPER_ADMIN_* bleibt
# fuer den Seed beim Containerstart (migrationen.py).
#
# Bewusste Messeinschraenkungen (Nachpruefung Runde 10): RATE_LIMIT_ENABLED=false
# (alle virtuellen Nutzer kommen von EINER Adresse — sonst wuerde die
# Anmeldesperre gemessen statt der Kapazitaet), APP_ENV=development (die
# Produktionspruefung wertet den Anbieter-Mock als Startfehler; leistungs-
# relevanten Code schaltet APP_ENV nicht um), MongoDB ohne Replikat (die
# Zahlen sind geringfuegig optimistisch).
#
# Aufruf auf prod2, im Ordner /opt/autoschnell:
#   sh deploy/lasttest-auf-prod2.sh --kurz     # 45-Sekunden-Probelauf (~10 min)
#   sh deploy/lasttest-auf-prod2.sh            # komplette Messung (~90 min)
#   sh deploy/lasttest-auf-prod2.sh --nur-stoss # nur der Stosstest (~15 min)
#   SZENARIEN=T3 sh deploy/lasttest-auf-prod2.sh --nur-matrix  # nur ein Szenario
# Umgebung: WARTE_LB (Sekunden nach dem Drain-Marker, Standard 60; nur hinter
#           dem Load Balancer), WEB_CONCURRENCY (Worker des Wegwerf-Backends).
# Vorbedingungen (Pruefbericht 20.09.2026, DP-05, siehe unten): 8 GB freier
# Arbeitsspeicher, Mongo dieses Servers ist SECONDARY, hinter dem Load
# Balancer wird der Server fuer die Dauer der Messung aus der Rotation genommen.
set -u

VERZ="${VERZEICHNIS:-/opt/autoschnell}"
NETZ=lasttest
KURZ=""
NUR_STOSS=""
NUR_MATRIX=""
for arg in "$@"; do
    [ "$arg" = "--kurz" ] && KURZ="--kurz"
    [ "$arg" = "--nur-stoss" ] && NUR_STOSS=1
    [ "$arg" = "--nur-matrix" ] && NUR_MATRIX=1
done

cd "$VERZ" || { echo "FEHLER: $VERZ fehlt"; exit 2; }
mkdir -p docs/lasttests/matrix docs/lasttests/stoss

# Pruefbericht 20.09.2026 (DP-05): Der Wegwerf-Stack laeuft NEBEN der
# Produktion auf derselben Maschine. Drei Vorbedingungen, damit die Messung
# die Besucher nicht trifft und die Zahlen stimmen:
#   1. Hinter dem Load Balancer: diesen Server erst aus der Rotation nehmen
#      (Drain-Marker wie deploy/rollout.sh) und WARTE_LB abwarten. Am Ende
#      wird der Marker wieder entfernt — nur, wenn DIESES Skript ihn gesetzt
#      hat. Der andere Server traegt die Besucher waehrenddessen allein.
#   2. Mongo dieses Servers muss SECONDARY sein (rs.hello()), sonst bremst
#      der Lasttest den PRIMARY der Produktion. Vorher auf dem PRIMARY
#      rs.stepDown() bzw. die Prioritaeten pruefen (DEPLOYMENT.md, Replikat).
#   3. Mindestens 8 GB freier Arbeitsspeicher (mongo 3 GB + Backend 4 GB
#      Limit) und je 1,5 CPU-Kerne fuer die Wegwerf-Container — auf einem
#      CCX23 (4 Kerne) bleibt der Produktion damit ein Kern.
WARTE_LB="${WARTE_LB:-60}"
DRAIN_VON_UNS=0

FREI_MB=$(free -m 2>/dev/null | awk '/^Mem:/ {print $7}')
if [ -n "$FREI_MB" ] && [ "$FREI_MB" -lt 8192 ]; then
    echo "FEHLER: nur ${FREI_MB} MB Arbeitsspeicher verfuegbar — der Wegwerf-Stack braucht 8 GB (mongo 3 GB + Backend 4 GB + Puffer)."
    exit 2
fi

if grep -qi 'replicaSet=' .env 2>/dev/null; then
    # 2 = SECONDARY, 1 = PRIMARY, 0 = weder noch (rs.hello braucht keine Anmeldung)
    ROLLE=$(docker compose exec -T mongo mongosh --quiet --eval 'rs.hello().secondary ? 2 : (rs.hello().isWritablePrimary ? 1 : 0)' 2>/dev/null | tr -d '\r\n ')
    if [ "$ROLLE" != "2" ]; then
        case "$ROLLE" in 1) ROLLE=PRIMARY ;; 0) ROLLE="weder PRIMARY noch SECONDARY" ;; *) ROLLE=unbekannt ;; esac
        echo "FEHLER: Mongo auf diesem Server ist nicht SECONDARY (ist: $ROLLE)."
        echo "   Der Lasttest wuerde sonst den PRIMARY der Produktion bremsen. Auf dem PRIMARY"
        echo "   rs.stepDown() ausfuehren (mongosh, DEPLOYMENT.md 'Replikat'), dann hier erneut starten."
        exit 2
    fi
    echo "   Mongo dieses Servers ist SECONDARY."
fi

drain_aufheben() {
    [ "$DRAIN_VON_UNS" = 1 ] || return 0
    rm -f deploy/drain/aktiv
    docker compose exec -T proxy sh -c 'rm -f /tmp/drain' >/dev/null 2>&1 || true
    DRAIN_VON_UNS=0
    echo "   Drain aufgehoben — der Load Balancer nimmt diesen Server in ca. 45 s wieder auf."
}
aufraeumen() {
    docker rm -f last-backend last-mongo >/dev/null 2>&1
    docker volume rm last-uploads >/dev/null 2>&1
    docker network rm "$NETZ" >/dev/null 2>&1
    drain_aufheben
}
trap aufraeumen EXIT INT TERM
aufraeumen

if grep -q '^PROXY_TEMPLATE=hinter-loadbalancer' .env 2>/dev/null; then
    if [ -e deploy/drain/aktiv ]; then
        echo "   Server ist bereits im Drain (deploy/drain/aktiv) — bleibt so und wird am Ende NICHT freigegeben."
    else
        { mkdir -p deploy/drain && touch deploy/drain/aktiv; } || { echo "FEHLER: Drain-Marker deploy/drain/aktiv laesst sich nicht setzen (Rechte? ls -ld deploy/drain)"; exit 2; }
        docker compose exec -T proxy sh -c 'touch /tmp/drain' >/dev/null 2>&1 || true
        DRAIN_VON_UNS=1
        echo "   Drain gesetzt — der Load Balancer nimmt diesen Server in ca. 45 s aus der Rotation, warte ${WARTE_LB}s"
        sleep "$WARTE_LB"
    fi
fi

echo "== 1/4 Backend-Image bauen"
docker compose build -q backend || { echo "FEHLER: Build"; exit 1; }
IMAGE=$(docker compose images backend --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | head -1)
[ -n "$IMAGE" ] || IMAGE=autoschnell-backend:latest

echo "== 2/4 Wegwerf-Stack starten ($IMAGE)"
docker network inspect "$NETZ" >/dev/null 2>&1 || docker network create "$NETZ" >/dev/null
docker run -d --name last-mongo --network "$NETZ" \
    --memory 3g --cpus 1.5 mongo:8.2 mongod --bind_ip_all --wiredTigerCacheSizeGB 1 >/dev/null
docker run -d --name last-backend --network "$NETZ" --memory 4g --cpus 1.5 --shm-size 512m \
    -v last-uploads:/app/uploads \
    -e MONGO_URL=mongodb://last-mongo:27017 -e DB_NAME=autoschnell_last \
    -e APP_ENV=development -e MOCK_PROVIDER_FETCH=true -e RATE_LIMIT_ENABLED=false \
    -e AUTO_DATEN_SCHAEDEN_FREITEXT=true \
    -e JWT_SECRET=lasttest-nur-wegwerf-nicht-produktiv \
    -e SUPER_ADMIN_USERNAME=last-superadmin -e SUPER_ADMIN_PASSWORD=last-only-superadmin-pw-1 \
    -e WEB_CONCURRENCY="${WEB_CONCURRENCY:-4}" \
    -e TZ=Europe/Berlin \
    "$IMAGE" >/dev/null

i=0
until docker run --rm --network "$NETZ" curlimages/curl:8.10.1 -fsS http://last-backend:8001/api/health >/dev/null 2>&1; do
    i=$((i+1)); [ $i -gt 45 ] && { echo "FEHLER: Backend startet nicht"; docker logs --tail 40 last-backend; exit 1; }
    sleep 2
done
echo "   Backend bereit."

echo "== 3/4 Lasttest laeuft (Ergebnisse: docs/lasttests/)"
# Die Lasttest-Programme sind absichtlich NICHT im Produktions-Image
# (.dockerignore: scripts/lasttest*) — sie kommen vom Server in den Container.
# --user 0: das Image laeuft als Nutzer "app", der Ergebnisordner gehoert root.
# --volumes-from uebernimmt das benannte Volume last-uploads (ohne eigenes
# Volume haette der Backend-Container nichts zu teilen — Nachpruefung Runde 10).
# --volumes-from: der Foto-Abgleich (Dateien <-> Datenbank) muss dieselben
# Dateien sehen, die das Backend geschrieben hat — sonst zaehlt er jedes
# Foto als "in der Datenbank, aber nicht auf der Platte".
docker run --rm --user 0 --network "$NETZ" --volumes-from last-backend \
    -v "$VERZ/backend/scripts:/app/scripts:ro" \
    -v "$VERZ/docs/lasttests:/docs/lasttests" \
    -e TEST_BASE_URL=http://last-backend:8001 \
    -e MONGO_URL=mongodb://last-mongo:27017 -e DB_NAME=autoschnell_last \
    -e SUPER_ADMIN_USERNAME=last-superadmin -e SUPER_ADMIN_PASSWORD=last-only-superadmin-pw-1 \
    -e WEB_CONCURRENCY="${WEB_CONCURRENCY:-4}" \
    "$IMAGE" sh -c "pip install -q psutil >/dev/null 2>&1; \
        if [ -z '$NUR_STOSS' ]; then python -X utf8 scripts/lasttest_matrix.py --alle --szenario ${SZENARIEN:-T1,T2,T3,T8,T9} $KURZ; fi && \
        if [ -z '$NUR_MATRIX' ]; then python -X utf8 scripts/lasttest_stoss.py; fi"
ERG=$?
# Auswertung ist Beiwerk: darf fehlen, aendert das Ergebnis nicht.
[ $ERG -eq 0 ] && docker run --rm --user 0 -v "$VERZ/backend/scripts:/app/scripts:ro" \
    -v "$VERZ/docs/lasttests:/docs/lasttests" "$IMAGE" \
    python -X utf8 scripts/matrix_auswertung.py 2>/dev/null || true

echo "== 4/4 Aufraeumen"
aufraeumen
trap - EXIT
echo
if [ $ERG -eq 0 ]; then
    echo "FERTIG. Berichte liegen unter $VERZ/docs/lasttests/ (matrix/ und stoss/)."
    echo "Zum Uebertragen: tar czf /root/lasttest-prod2.tgz -C $VERZ/docs lasttests"
else
    echo "Lasttest endete mit Fehler $ERG — Ausgabe oben pruefen."
fi
exit $ERG
