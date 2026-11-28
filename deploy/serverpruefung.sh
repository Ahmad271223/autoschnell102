#!/bin/sh
# Serverpruefung — Wunsch Ahmad 24.11.2026: "pruefe den Server, ist alles gut, gibt es Probleme; schau alle nicht
# erkannten Inserate an". NUR LESEN: aendert keine Daten, keine Einstellungen, startet keinen Container neu.
#
# Normalerweise schickt deploy/serverpruefung.ps1 (auf dem PC) dieses Skript zusammen mit den Python-Skripten nach
# /tmp/autoschnell-pruefung und startet es dort. Das Ergebnis kommt als tar.gz auf der STANDARDAUSGABE zurueck —
# alles andere (Fortschritt, Fehler) geht nach stderr.
#
# Von Hand auf dem Server (Stand aus Git):
#   cd /opt/autoschnell && sh deploy/serverpruefung.sh voll 30 > /tmp/pruefung.tgz
#
# Art "voll":   Zustand dieses Servers + Systemcheck + Lesebilder-Export (mit Programm-Vergleichen ohne Erfolg und
#               Leseseiten) + Leseseiten-Struktur. Die Datenbank ist gemeinsam — einmal reicht.
# Art "server": nur der Zustand dieses Servers (fuer den zweiten Server).
set -u
ART="${1:-voll}"
TAGE="${2:-30}"
case "$TAGE" in *[!0-9]*|"") TAGE=30 ;; esac
HIER="$(cd "$(dirname "$0")" && pwd)"
ORDNER="${AUTOSCHNELL_ORDNER:-/opt/autoschnell}"
if [ -f "$HIER/systemcheck.py" ]; then SKRIPTE="$HIER"; else SKRIPTE="$ORDNER/backend/scripts"; fi
NAME="$(hostname 2>/dev/null || echo server)"
ARBEIT="$(mktemp -d /tmp/autoschnell-ergebnis.XXXXXX)"
AUS="$ARBEIT/$NAME"
mkdir -p "$AUS"
exec 3>&1 1>&2          # ab hier: nur das Archiv geht auf die echte Standardausgabe (Kanal 3)

schritt() { echo ">> $NAME: $*"; }

cd "$ORDNER" || { echo "Ordner $ORDNER fehlt"; exit 1; }
# wie rollout.sh/freigeben.sh: ohne COMPOSE_FILE in der .env die Replica-Set-Ergaenzung mitnehmen
if ! grep -q '^COMPOSE_FILE=' .env 2>/dev/null; then
    export COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.replica.yml
fi

schritt "Zustand des Servers"
{
    echo "== Server $NAME — $(date '+%d.%m.%Y %H:%M %Z')"
    uptime
    echo; echo "== Plattenplatz"; df -h / /var/lib/docker 2>/dev/null
    echo; echo "== Arbeitsspeicher"; free -m
    echo; echo "== Neustart faellig?"; [ -f /var/run/reboot-required ] && cat /var/run/reboot-required || echo "nein"
    echo; echo "== Stand im Ordner (git)"; git log --oneline -3 2>&1; git status --short 2>&1 | grep -v '^??' | head -20
    echo; echo "== Container"; docker compose ps --format 'table {{.Name}}\t{{.Status}}' 2>&1 || docker compose ps 2>&1
    echo; echo "== Docker-Speicher"; docker system df 2>&1
    echo; echo "== Bereitschaft (/api/ready)"
    docker compose exec -T backend curl -s --max-time 20 http://localhost:8001/api/ready 2>&1; echo
    echo; echo "== Replica-Set"; docker compose exec -T backend python scripts/replikat_pruefen.py 2>&1
    echo; echo "== Sicherungen (/backups, nur der Server mit der Tagessperre hat neue)"
    docker compose exec -T backend sh -c 'ls -1 /backups 2>/dev/null | tail -n 4; du -sh /backups 2>/dev/null' 2>&1
    for dienst in backend web proxy; do
        echo; echo "== Protokoll $dienst (letzte 72 h)"
        docker compose logs --since 72h --no-color "$dienst" > "$ARBEIT/log.txt" 2>&1
        # Python-Protokoll: ERROR/CRITICAL/Traceback; nginx: [error]/[crit]/[alert]/[emerg]
        if [ "$dienst" = backend ]; then muster='ERROR|CRITICAL|Traceback'; else muster='\[(error|crit|alert|emerg)\]'; fi
        echo "Zeilen: $(wc -l < "$ARBEIT/log.txt")   Fehler: $(grep -c -E "$muster" "$ARBEIT/log.txt")   Antworten 5xx: $(grep -c -E '" 5[0-9][0-9] ' "$ARBEIT/log.txt")"
        echo "-- haeufigste Fehler (ohne Uhrzeit zusammengezaehlt):"
        grep -E "$muster" "$ARBEIT/log.txt" \
            | sed -E 's/^[^|]*\| ?//; s/^[0-9]{4}[-\/][0-9]{2}[-\/][0-9]{2}[ T][0-9:.,]+( [+-][0-9]{4})? *//' \
            | cut -c1-240 | sort | uniq -c | sort -rn | head -n 30
        echo "-- die letzten 10 (mit Uhrzeit):"
        grep -E "$muster" "$ARBEIT/log.txt" | cut -c1-300 | tail -n 10
        echo "-- Adressen mit 5xx-Antwort (ohne ?-Teil):"
        grep -E '" 5[0-9][0-9] ' "$ARBEIT/log.txt" | grep -o -E '"[A-Z]+ [^ ?"]+' | sort | uniq -c | sort -rn | head -n 15
    done
    rm -f "$ARBEIT/log.txt"
} > "$AUS/server.txt" 2>&1

if [ "$ART" = "voll" ]; then
    for s in systemcheck.py lesebilder_export.py leseseite_analysieren.py; do
        if [ -f "$SKRIPTE/$s" ]; then
            docker compose cp "$SKRIPTE/$s" "backend:/app/scripts/$s" || echo "!! $s nicht in den Container kopiert"
        fi
    done
    schritt "Systemcheck (letzte $TAGE Tage)"
    docker compose exec -T backend python scripts/systemcheck.py --tage "$TAGE" > "$AUS/systemcheck.txt" 2>&1
    schritt "nicht erkannte Anzeigen exportieren"
    docker compose exec -T backend rm -rf /tmp/lesebilder-pruefung
    docker compose exec -T backend python scripts/lesebilder_export.py --ziel /tmp/lesebilder-pruefung \
        --tage "$TAGE" > "$AUS/export.txt" 2>&1
    docker compose cp backend:/tmp/lesebilder-pruefung "$AUS/lesebilder" || echo "!! Export nicht aus dem Container geholt"
    docker compose exec -T backend rm -rf /tmp/lesebilder-pruefung
    schritt "Leseseiten-Struktur"
    docker compose exec -T backend python scripts/leseseite_analysieren.py --anzahl 10 > "$AUS/leseseiten_struktur.txt" 2>&1
    # 24.11.2026: Lesungen ohne Fotos — wo die Bildadressen stehen und ob ein Stopp-Wort sie abschneidet
    schritt "Leseseiten ohne Fotos"
    docker compose exec -T backend python scripts/leseseite_analysieren.py --grund "ohne Fotos" --anzahl 10 \
        > "$AUS/leseseiten_fotos.txt" 2>&1
fi

schritt "packe Ergebnis ($(du -sh "$AUS" | cut -f1))"
tar czf - -C "$ARBEIT" "$NAME" >&3
rc=$?
rm -rf "$ARBEIT"
# den hochgeladenen Ordner (deploy/serverpruefung.ps1) wieder wegraeumen — nie den Projektordner
case "$HIER" in /tmp/autoschnell-pruefung*) rm -rf "$HIER" ;; esac
exit $rc
