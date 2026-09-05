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
# Aufruf auf prod2, im Ordner /opt/autoschnell:
#   sh deploy/lasttest-auf-prod2.sh --kurz     # 45-Sekunden-Probelauf (~10 min)
#   sh deploy/lasttest-auf-prod2.sh            # komplette Messung (~90 min)
set -u

VERZ="${VERZEICHNIS:-/opt/autoschnell}"
NETZ=lasttest
KURZ=""
[ "${1:-}" = "--kurz" ] && KURZ="--kurz"

cd "$VERZ" || { echo "FEHLER: $VERZ fehlt"; exit 2; }
mkdir -p docs/lasttests/matrix docs/lasttests/stoss

aufraeumen() {
    docker rm -f last-backend last-mongo >/dev/null 2>&1
    docker network rm "$NETZ" >/dev/null 2>&1
}
trap aufraeumen EXIT INT TERM
aufraeumen

echo "== 1/4 Backend-Image bauen"
docker compose build -q backend || { echo "FEHLER: Build"; exit 1; }
IMAGE=$(docker compose images backend --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | head -1)
[ -n "$IMAGE" ] || IMAGE=autoschnell-backend:latest

echo "== 2/4 Wegwerf-Stack starten ($IMAGE)"
docker network create "$NETZ" >/dev/null
docker run -d --name last-mongo --network "$NETZ" \
    --memory 3g mongo:8.2 mongod --bind_ip_all --wiredTigerCacheSizeGB 1 >/dev/null
docker run -d --name last-backend --network "$NETZ" --memory 4g --shm-size 512m \
    -e MONGO_URL=mongodb://last-mongo:27017 -e DB_NAME=autoschnell_last \
    -e APP_ENV=development -e MOCK_PROVIDER_FETCH=true -e RATE_LIMIT_ENABLED=false \
    -e SELF_SIGNUP=true -e AUTO_DATEN_SCHAEDEN_FREITEXT=true \
    -e JWT_SECRET=lasttest-nur-wegwerf-nicht-produktiv \
    -e ADMIN_EMAIL=last-admin@ci.invalid -e ADMIN_PASSWORD=last-only-admin-pw-1 \
    -e SUPER_ADMIN_USERNAME=last-superadmin -e SUPER_ADMIN_PASSWORD=last-only-superadmin-pw-1 \
    -e WEB_CONCURRENCY="${WEB_CONCURRENCY:-4}" -e SNAPSHOT_CONCURRENCY=1 \
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
docker run --rm --user 0 --network "$NETZ" \
    -v "$VERZ/backend/scripts:/app/scripts:ro" \
    -v "$VERZ/docs/lasttests:/docs/lasttests" \
    -e TEST_BASE_URL=http://last-backend:8001 \
    -e MONGO_URL=mongodb://last-mongo:27017 -e DB_NAME=autoschnell_last \
    -e ADMIN_EMAIL=last-admin@ci.invalid -e ADMIN_PASSWORD=last-only-admin-pw-1 \
    -e SUPER_ADMIN_USERNAME=last-superadmin -e SUPER_ADMIN_PASSWORD=last-only-superadmin-pw-1 \
    -e WEB_CONCURRENCY="${WEB_CONCURRENCY:-4}" \
    "$IMAGE" sh -c "pip install -q psutil >/dev/null 2>&1; \
        python -X utf8 scripts/lasttest_matrix.py --alle --szenario T1,T2,T3,T8,T9 $KURZ && \
        python -X utf8 scripts/lasttest_stoss.py"
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
