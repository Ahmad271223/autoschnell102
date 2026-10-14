#!/bin/sh
# Werte in der .env setzen, ohne Editor — je Aufruf beliebig viele NAME=WERT.
# Eine vorhandene Zeile NAME=... wird an Ort und Stelle ersetzt (weitere
# Dubletten entfernt), eine fehlende ans Ende angehaengt. Vorher entsteht
# eine Sicherung .env.bak-<Datum>.
#
# Aufruf (im Ordner /opt/autoschnell):
#   sh deploy/env_setzen.sh MAX_CONCURRENT_MOBILE=16 MAX_CONCURRENT_AUTOSCOUT=16
#
# Die Werte gelten erst nach einem Neustart der Container — Server fuer
# Server, nie beide gleichzeitig:
#   sh deploy/rollout.sh        (neuer Stand aus Git, ein Server je Aufruf)
#   nur Werte geaendert, gleicher Stand — erst aus dem Load Balancer nehmen:
#     grep -q '^COMPOSE_FILE=' .env || export COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.replica.yml
#     touch deploy/drain/aktiv && sleep 60 && docker compose up -d
#     sh deploy/freigeben.sh    (meldet es "nicht bereit": 30 s warten, erneut)
#   Die erste Zeile (Pruefung 21.09.2026): ohne COMPOSE_FILE erzeugt
#   "docker compose up -d" mongo und backend OHNE die Replica-Set-Ergaenzung
#   neu — das Mitglied faellt aus rs0 (Vorfall 07.09.2026). rollout.sh und
#   freigeben.sh setzen das selbst, ein Aufruf von Hand nicht.
#
# Rechte (21.09.2026): die .env enthaelt alle Geheimnisse und bleibt nur fuer
# ihren Besitzer lesbar (chmod 600, siehe DEPLOYMENT.md) — ebenso die
# Sicherung .env.bak-<Datum>. Vorher entstand die neue .env ueber eine
# Zwischendatei mit den Standardrechten (644) und war danach fuer jeden
# Benutzer auf dem Server lesbar. Server mit Stand vor dem 21.09.2026 haben bis
# zum naechsten git pull noch das alte Skript: danach einmal
# chmod 600 .env .env.bak-*   (rollout.sh tut das nach dem Pull selbst).
#
# Grenzen: Werte ohne Backslash und ohne Zeilenumbruch; Variablennamen aus
# Grossbuchstaben, Ziffern und Unterstrich.
set -eu
DATEI="${ENV_DATEI:-.env}"
if [ ! -f "$DATEI" ]; then
  echo "FEHLER: $DATEI nicht gefunden — im Ordner /opt/autoschnell aufrufen." >&2
  exit 2
fi
if [ "$#" -eq 0 ]; then
  echo "Aufruf: sh deploy/env_setzen.sh NAME=WERT [NAME=WERT ...]" >&2
  exit 2
fi
for paar in "$@"; do
  case "$paar" in
    *=*) ;;
    *) echo "FEHLER: '$paar' ist kein NAME=WERT" >&2; exit 2 ;;
  esac
  name="${paar%%=*}"
  case "$name" in
    [A-Z_]*) ;;
    *) echo "FEHLER: '$name' ist kein gueltiger Variablenname (GROSSBUCHSTABEN, Ziffern, _)" >&2; exit 2 ;;
  esac
  case "$name" in
    *[!A-Z0-9_]*) echo "FEHLER: '$name' ist kein gueltiger Variablenname (GROSSBUCHSTABEN, Ziffern, _)" >&2; exit 2 ;;
  esac
  wert="${paar#*=}"
  case "$wert" in
    *\\*) echo "FEHLER: Wert fuer $name enthaelt einen Backslash" >&2; exit 2 ;;
  esac
done
# Pruefbericht 21.09.2026 (Betrieb): Sicherung und Zwischendatei entstehen
# nur fuer den Besitzer lesbar (600) — mv uebernimmt die Rechte der
# Zwischendatei, die .env war danach sonst 644.
umask 077
cp "$DATEI" "$DATEI.bak-$(date +%Y%m%d-%H%M%S)"
tmp="$DATEI.neu.$$"
for paar in "$@"; do
  name="${paar%%=*}"
  wert="${paar#*=}"
  awk -v n="$name" -v w="$wert" '
    BEGIN { fertig = 0 }
    index($0, n "=") == 1 { if (!fertig) { print n "=" w; fertig = 1 }; next }
    { print }
    END { if (!fertig) print n "=" w }
  ' "$DATEI" > "$tmp"
  mv "$tmp" "$DATEI"
done
# Repariert auch eine .env, die ein frueherer Aufruf (vor dem 21.09.2026)
# fuer alle lesbar hinterlassen hat.
chmod 600 "$DATEI" 2>/dev/null \
  || echo "WARNUNG: Rechte von $DATEI nicht auf 600 gesetzt — von Hand: chmod 600 $DATEI" >&2
echo "Gesetzt in $DATEI:"
for paar in "$@"; do
  name="${paar%%=*}"
  grep "^${name}=" "$DATEI"
done
