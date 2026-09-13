#!/bin/sh
# Zertifikat erneuern (Let's Encrypt), wenn nginx selbst die Verschluesselung
# macht — also im Betrieb OHNE Load Balancer.
#
# Warum ein Skript und keine lange Befehlskette im cron: certbot braucht
# Port 80, den der Proxy belegt. Der Proxy wird deshalb kurz gestoppt — und
# in JEDEM Fall wieder gestartet, auch wenn die Erneuerung scheitert. Eine
# Kette mit "&&" wuerde bei einem Fehler mittendrin abbrechen und die Seite
# dauerhaft offline lassen.
#
# Einrichten (einmalig, als root):
#   chmod +x /opt/autoschnell/deploy/zertifikat-erneuern.sh
#   echo '0 4 * * 1 root DOMAIN=app.auto-schnellkauf.de /opt/autoschnell/deploy/zertifikat-erneuern.sh >> /var/log/autoschnell-zertifikat.log 2>&1' \
#     > /etc/cron.d/autoschnell-zertifikat
#
# Von Hand testen (aendert nichts, sagt nur, was passieren wuerde):
#   DOMAIN=app.auto-schnellkauf.de PROBE=1 /opt/autoschnell/deploy/zertifikat-erneuern.sh
set -u

VERZEICHNIS="${VERZEICHNIS:-/opt/autoschnell}"
DOMAIN="${DOMAIN:-}"
PROBE="${PROBE:-0}"

if [ -z "$DOMAIN" ]; then
    # Aus der .env lesen, wenn nicht ausdruecklich angegeben.
    DOMAIN=$(grep -E '^PUBLIC_HOST=' "$VERZEICHNIS/.env" 2>/dev/null | cut -d= -f2 | tr -d '\r')
fi
if [ -z "$DOMAIN" ]; then
    echo "$(date '+%F %T') FEHLER: keine Domain (DOMAIN=... setzen oder PUBLIC_HOST in der .env)"
    exit 2
fi

cd "$VERZEICHNIS" || { echo "$(date '+%F %T') FEHLER: $VERZEICHNIS nicht gefunden"; exit 2; }
echo "$(date '+%F %T') Erneuerung fuer $DOMAIN startet"

TROCKEN=""
[ "$PROBE" = "1" ] && TROCKEN="--dry-run"

docker compose stop proxy

docker run --rm -p 80:80 \
    -v "$VERZEICHNIS/deploy/certs:/etc/letsencrypt" \
    certbot/certbot renew --quiet $TROCKEN
ERGEBNIS=$?

# Die frischen Dateien dorthin kopieren, wo nginx sie erwartet. Auch dann,
# wenn certbot "nichts zu tun" meldet — schadet nicht und heilt einen
# vergessenen Kopiervorgang.
LIVE="$VERZEICHNIS/deploy/certs/live/$DOMAIN"
if [ "$PROBE" != "1" ] && [ -f "$LIVE/fullchain.pem" ]; then
    cp "$LIVE/fullchain.pem" "$VERZEICHNIS/deploy/certs/fullchain.pem"
    cp "$LIVE/privkey.pem"  "$VERZEICHNIS/deploy/certs/privkey.pem"
    echo "$(date '+%F %T') Zertifikatsdateien aktualisiert"
fi

# Proxy IMMER wieder hochfahren.
docker compose start proxy

if [ $ERGEBNIS -eq 0 ]; then
    echo "$(date '+%F %T') fertig (Erneuerung ok)"
else
    echo "$(date '+%F %T') ACHTUNG: certbot meldete Fehler $ERGEBNIS — Proxy laeuft wieder,"
    echo "    aber das Zertifikat wurde NICHT erneuert. Bitte nachsehen."
fi
exit $ERGEBNIS
