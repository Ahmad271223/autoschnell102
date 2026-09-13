#!/bin/sh
# Drain-Marker NUR entfernen, wenn Backend und Oberflaeche wirklich antworten.
#
# Gegenstueck zu deploy/rollout.sh: bricht das Rollout ab, bleibt der Server
# absichtlich im Drain (Health 503, kein Besucher vom Load Balancer). Erst
# wenn die Ursache behoben ist (erneutes Rollout oder Rollback, siehe
# DEPLOYMENT.md), gibt dieses Skript den Server wieder frei — und prueft
# vorher genau das, was der Load Balancer spaeter sieht:
#   * /api/ready im Backend (Datenbank, Migration, Produktionsvoraussetzungen)
#   * Startseite ueber den Proxy (Oberflaechen-Container vorhanden)
#
# Aufruf:  cd /opt/autoschnell && sh deploy/freigeben.sh
#          sh deploy/freigeben.sh --erzwingen   (ohne Pruefung — nur bewusst)
set -e
VERZ=${VERZ:-/opt/autoschnell}
cd "$VERZ" || { echo "FEHLER: $VERZ fehlt"; exit 2; }
if ! grep -q '^COMPOSE_FILE=' .env 2>/dev/null; then
    export COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.replica.yml
fi
PUBLIC_HOST=$(grep '^PUBLIC_HOST=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' )
[ -n "$PUBLIC_HOST" ] || { echo "FEHLER: PUBLIC_HOST fehlt in .env"; exit 2; }

# Runde 21 (Pruefbefund D): Nach einer gescheiterten Abschlusspruefung
# (rollout.sh, Code 3) ist der Server absichtlich NICHT im Drain. Wird dieses
# Skript dann aus Gewohnheit aufgerufen, sagt es das, statt still eine
# zweite "Freigabe" zu melden.
if [ -e deploy/drain/aktiv ]; then
    WAR_IM_DRAIN=1
else
    WAR_IM_DRAIN=0
    echo "Hinweis: kein Drain-Marker (deploy/drain/aktiv) — dieser Server war auf dem Host nicht im Drain."
fi
# Runde 21 (Gegenpruefung D): "Freigabe verweigert" klingt nach "geschuetzt im
# Drain". Ohne Marker steht der Server aber in der Rotation und bekommt
# Besucher — das muss die Fehlermeldung dann ausdruecklich sagen.
nicht_im_drain_hinweis() {
    [ "$WAR_IM_DRAIN" = 1 ] && return 0
    echo "   ACHTUNG: dieser Server ist NICHT im Drain — er steht in der Rotation und bekommt Besucher."
    echo "   Ist er defekt, von Hand herausnehmen: touch deploy/drain/aktiv (spaeter: sh deploy/freigeben.sh)"
}

if [ "$1" != "--erzwingen" ]; then
    if ! docker compose exec -T backend curl -fsS http://localhost:8001/api/ready >/dev/null 2>&1; then
        echo "FEHLER: Backend meldet sich nicht bereit (/api/ready) — Freigabe verweigert."
        echo "   Details: docker compose exec -T backend curl -s http://localhost:8001/api/ready"
        echo "            docker compose logs --tail 80 backend"
        nicht_im_drain_hinweis
        exit 1
    fi
    if ! docker compose exec -T proxy sh -c "wget -q -O /dev/null --header='Host: $PUBLIC_HOST' http://127.0.0.1/" >/dev/null 2>&1; then
        echo "FEHLER: Oberflaeche antwortet nicht ueber den Proxy — Freigabe verweigert."
        echo "   Details: docker compose ps; docker compose logs --tail 40 web proxy"
        echo "   Oft hilft: docker compose restart proxy (loest die Container-Adressen neu auf)"
        nicht_im_drain_hinweis
        exit 1
    fi
    echo "Backend bereit, Oberflaeche antwortet."
else
    echo "ACHTUNG: Freigabe OHNE Pruefung (--erzwingen)."
fi
# Runde 21: ein Marker, der sich nicht entfernen laesst (Rechte), endet laut
# statt nur mit der rohen rm-Fehlerzeile.
rm -f deploy/drain/aktiv || true
if [ -e deploy/drain/aktiv ]; then
    echo "FEHLER: Drain-Marker deploy/drain/aktiv laesst sich nicht entfernen (Rechte? ls -l deploy/drain/aktiv)"
    echo "   — dieser Server BLEIBT im Drain."
    exit 1
fi
docker compose exec -T proxy sh -c 'rm -f /tmp/drain' >/dev/null 2>&1 || true
if [ "$WAR_IM_DRAIN" = 1 ]; then
    echo "Drain aufgehoben — der Load Balancer nimmt $(hostname) in ca. 45 s wieder auf."
else
    echo "Nichts freizugeben auf dem Host — ein etwaiger Marker im Proxy-Container (/tmp/drain) wurde entfernt."
fi
