#!/bin/sh
# Werte in der .env setzen, ohne Editor — je Aufruf beliebig viele NAME=WERT.
# Eine vorhandene Zeile NAME=... wird an Ort und Stelle ersetzt (weitere
# Dubletten entfernt), eine fehlende ans Ende angehaengt. Vorher entsteht
# eine Sicherung .env.bak-<Datum>.
#
# Aufruf (im Ordner /opt/autoschnell):
#   sh deploy/env_setzen.sh MAX_CONCURRENT_MOBILE=16 MAX_CONCURRENT_AUTOSCOUT=16
#
# Die Werte gelten erst nach einem Neustart der Container:
#   sh deploy/rollout.sh        (neuer Stand aus Git, ein Server je Aufruf)
#   docker compose up -d        (nur Werte geaendert, gleicher Stand)
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
echo "Gesetzt in $DATEI:"
for paar in "$@"; do
  name="${paar%%=*}"
  grep "^${name}=" "$DATEI"
done
