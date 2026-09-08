#!/bin/sh
# Rollout OHNE 502 hinter dem Hetzner Load Balancer — EIN Server je Aufruf.
#
# Warum: "docker compose up -d --build" baut auch den Oberflaechen-Container
# neu. Der Load Balancer prueft nur /api/health (Backend) — das antwortete
# waehrend des Neubaus weiter mit 200, also schickte der LB Besucher auf
# diesen Server, und die bekamen fuer rund 45 Sekunden 502 fuer alle
# statischen Dateien (Vorfall 07.09.2026, 15:04 UTC).
#
# Ablauf:
#   1. Drain-Marker setzen: /api/health antwortet 503, der LB nimmt den
#      Server nach seinen Wiederholungen (3 x 15 s) aus der Rotation.
#   2. Code holen, bauen, starten.
#   3. Warten, bis Backend (/api/ready) und Oberflaeche (/) antworten.
#   4. Marker entfernen, dem LB Zeit geben, den Server wieder aufzunehmen.
#   Danach dasselbe auf dem anderen Server. Der zweite Server traegt die
#   Last waehrenddessen allein — deshalb IMMER nacheinander.
#
# Bricht ein Schritt ab (Build, Bereitschaft, Oberflaeche), BLEIBT der
# Server im Drain: der Marker wird nicht automatisch entfernt, weil ein halb
# fertiger Server sonst wieder in die Rotation kaeme (/api/health kann 200
# liefern, waehrend /api/ready an Datenbank, Migration oder R2 scheitert).
# Freigabe erst nach Behebung/Rollback mit `sh deploy/freigeben.sh`.
#
# Aufruf (auf prod2, dann auf prod1):
#   cd /opt/autoschnell && sh deploy/rollout.sh
# Umgebung: WARTE_LB (Sekunden je LB-Umschaltung, Standard 60),
#           SCHLAF (Wartetakt der Bereitschaftsschleifen, Standard 3),
#           VERZ (Checkout, Standard /opt/autoschnell).
set -e
VERZ=${VERZ:-/opt/autoschnell}
WARTE_LB=${WARTE_LB:-60}
SCHLAF=${SCHLAF:-3}
cd "$VERZ" || { echo "FEHLER: $VERZ fehlt"; exit 2; }

# Replikat-Ergaenzung IMMER mitnehmen (Vorfall 07.09.2026 vormittags), es sei
# denn, die .env setzt COMPOSE_FILE bereits.
if ! grep -q '^COMPOSE_FILE=' .env 2>/dev/null; then
    export COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.replica.yml
fi
PUBLIC_HOST=$(grep '^PUBLIC_HOST=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' )
[ -n "$PUBLIC_HOST" ] || { echo "FEHLER: PUBLIC_HOST fehlt in .env"; exit 2; }

# Marker auf dem Host (deploy/drain/aktiv, per Volume im Proxy sichtbar —
# ueberlebt einen Neustart des Proxy-Containers waehrend "up -d --build")
# UND im Container (/tmp/drain, Uebergang fuer Proxys ohne das Volume).
drain() {
    mkdir -p deploy/drain && touch deploy/drain/aktiv
    docker compose exec -T proxy sh -c 'touch /tmp/drain' >/dev/null 2>&1 || true
    echo "   Drain gesetzt — der Load Balancer nimmt diesen Server in ca. 45 s aus der Rotation"
}
undrain() {
    rm -f deploy/drain/aktiv
    docker compose exec -T proxy sh -c 'rm -f /tmp/drain' >/dev/null 2>&1 || true
}
FERTIG=0
abbruch() {
    rc=$?
    [ "$FERTIG" = 1 ] && return 0
    echo ""
    echo "ABBRUCH (Code $rc): dieser Server BLEIBT im Drain — /api/health antwortet 503,"
    echo "   der Load Balancer schickt keine Besucher hierher, der andere Server traegt"
    echo "   die Last allein. Der Marker wird absichtlich NICHT entfernt: ein halb"
    echo "   fertiger Server darf nicht zurueck in die Rotation."
    echo "   Naechste Schritte:"
    echo "     1. Ursache pruefen:  docker compose ps"
    echo "                          docker compose logs --tail 80 backend web proxy"
    echo "     2. Entweder beheben und 'sh deploy/rollout.sh' erneut ausfuehren,"
    echo "        oder zurueck auf den vorherigen Stand (DEPLOYMENT.md, 'Rollback')."
    echo "     3. Erst danach freigeben: 'sh deploy/freigeben.sh' (prueft Backend und"
    echo "        Oberflaeche und entfernt dann den Drain-Marker)."
    exit "$rc"
}
trap abbruch EXIT INT TERM

echo "== 1/5 Drain (Health -> 503), warte ${WARTE_LB}s"
drain
sleep "$WARTE_LB"

echo "== 2/5 Code holen"
git pull --ff-only

echo "== 3/5 Bauen und starten"
docker compose up -d --build

echo "== 4/5 Warten, bis Backend und Oberflaeche antworten"
i=0
until docker compose exec -T backend curl -fsS http://localhost:8001/api/ready >/dev/null 2>&1; do
    i=$((i+1))
    [ $i -gt 60 ] && { echo "FEHLER: Backend nicht bereit"; docker compose exec -T backend curl -s http://localhost:8001/api/ready; docker compose logs --tail 40 backend; exit 1; }
    sleep "$SCHLAF"
done
i=0
# Von innen (127.0.0.1 darf laut LB-Vorlage direkt zugreifen): Startseite ueber
# den Proxy holen — das prueft auch den neu gebauten Oberflaechen-Container.
until docker compose exec -T proxy sh -c "wget -q -O /dev/null --header='Host: $PUBLIC_HOST' http://127.0.0.1/" >/dev/null 2>&1; do
    i=$((i+1))
    if [ $i -eq 10 ]; then
        # Proxy mit alter Vorlage (ohne Docker-DNS-Resolver) kennt nach dem
        # Neubau des web-Containers noch dessen alte Adresse -> 502. Ein
        # Neustart loest neu auf; der Drain-Marker auf dem Host bleibt.
        echo "   Oberflaeche antwortet nicht ueber den Proxy — Proxy wird neu gestartet"
        docker compose restart proxy >/dev/null 2>&1 || true
    fi
    [ $i -gt 40 ] && { echo "FEHLER: Oberflaeche antwortet nicht"; docker compose logs --tail 20 web proxy; exit 1; }
    sleep "$SCHLAF"
done
echo "   Backend bereit, Oberflaeche antwortet."

echo "== 5/6 Drain aufheben, warte ${WARTE_LB}s bis der Load Balancer den Server wieder fuehrt"
FERTIG=1
undrain
trap - EXIT INT TERM
sleep "$WARTE_LB"

echo "== 6/6 Probe von aussen (ueber Cloudflare und Load Balancer, wie ein Besucher)"
# Erkennt u.a. einen im Cloudflare-Cache festgehaltenen 502 fuer das
# Oberflaechen-Skript (Vorfall 07.09.2026: schwarzer Bildschirm trotz
# gesunder Server). Ein Fehler hier bricht das Rollout nicht ab — der
# andere Server laeuft ja — wird aber laut gemeldet.
# DKIM mitpruefen, wenn der Selector in der .env steht (z.B. DKIM_SELECTOR=resend)
DKIM=$(grep '^DKIM_SELECTOR=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
if ! docker compose exec -T backend python scripts/betriebsprobe.py "$PUBLIC_HOST" ${DKIM:+--dkim-selector "$DKIM"}; then
    echo "ACHTUNG: Probe von aussen meldet Fehler — siehe oben (Cloudflare-Cache leeren? anderer Server?)"
fi
echo "FERTIG auf $(hostname) — jetzt denselben Befehl auf dem anderen Server."
