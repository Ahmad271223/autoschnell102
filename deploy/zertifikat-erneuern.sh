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
#
# Rueckgabecodes: 0 ok, 2 Konfiguration fehlt, 3 hinter dem Load Balancer
# (nichts zu tun, Zertifikat liegt am LB), 124 certbot-Zeitlimit (5 min),
# sonst der Code von certbot. Der Proxy laeuft in JEDEM Fall wieder.
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
# Pruefbericht 20.09.2026 (DP-12): Hinter dem Load Balancer liegt das
# Zertifikat am LB (DEPLOYMENT.md, Schritt 3g: der Cron wird dort geloescht),
# nginx hoert nur auf Port 80. Der Proxy-Stopp unten haette den Server fuer
# den Load Balancer ohne Drain-Marker unerreichbar gemacht — ganz umsonst.
if grep -q '^PROXY_TEMPLATE=hinter-loadbalancer' "$VERZEICHNIS/.env" 2>/dev/null; then
    echo "$(date '+%F %T') hinter dem Load Balancer nicht noetig (PROXY_TEMPLATE=hinter-loadbalancer): nichts getan — Cron /etc/cron.d/autoschnell-zertifikat loeschen"
    exit 3
fi
echo "$(date '+%F %T') Erneuerung fuer $DOMAIN startet"

TROCKEN=""
[ "$PROBE" = "1" ] && TROCKEN="--dry-run"

docker compose stop proxy
# Pruefbericht 20.09.2026 (SK-15): Zwischen stop und start darf NICHTS den
# Proxy aus lassen — ein "unless-stopped"-Container, der per stop angehalten
# wurde, kommt auch nach einem Reboot nicht von selbst wieder. Deshalb: Trap
# fuer Abbruch und Signale (raeumt den certbot-Container weg, damit Port 80
# frei ist, und startet den Proxy), und certbot bekommt ein Zeitlimit von
# 5 Minuten (Code 124).
CERTBOT_NAME=autoschnell-certbot
proxy_wieder_starten() {
    docker rm -f "$CERTBOT_NAME" >/dev/null 2>&1
    docker compose start proxy
}
trap 'proxy_wieder_starten' EXIT
trap 'trap - EXIT; proxy_wieder_starten; exit 130' INT TERM HUP

timeout 300 docker run --rm --name "$CERTBOT_NAME" -p 80:80 \
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

# Proxy IMMER wieder hochfahren — hier der normale Weg; der Trap deckt nur
# Abbruch und Signale ab und wird davor abgeraeumt (sonst zweimal "start").
trap - EXIT INT TERM HUP
proxy_wieder_starten

if [ $ERGEBNIS -eq 0 ]; then
    echo "$(date '+%F %T') fertig (Erneuerung ok)"
elif [ $ERGEBNIS -eq 124 ]; then
    echo "$(date '+%F %T') ACHTUNG: certbot wurde nach 5 Minuten abgebrochen (Zeitlimit) — Proxy laeuft wieder,"
    echo "    aber das Zertifikat wurde NICHT erneuert. Bitte nachsehen (Port 80 frei? Let's Encrypt erreichbar?)."
else
    echo "$(date '+%F %T') ACHTUNG: certbot meldete Fehler $ERGEBNIS — Proxy laeuft wieder,"
    echo "    aber das Zertifikat wurde NICHT erneuert. Bitte nachsehen."
fi
exit $ERGEBNIS
