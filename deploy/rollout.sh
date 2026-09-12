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
#   2. Code holen.
#   3. Bauen und starten.
#   4. Warten, bis Backend (/api/ready) und Oberflaeche (/) antworten.
#   5. Marker entfernen, dem LB Zeit geben, den Server wieder aufzunehmen.
#   6. Abschlusspruefung von aussen (scripts/betriebsprobe.py ueber Cloudflare
#      und Load Balancer, wie ein Besucher). NUR wenn sie fehlerfrei ist,
#      meldet das Skript "FERTIG".
#   Erst nach "FERTIG" dasselbe auf dem anderen Server. Der andere Server
#   traegt die Last waehrenddessen allein — deshalb IMMER nacheinander.
#
# Bricht Schritt 1-5 ab (Build, Bereitschaft, Oberflaeche), BLEIBT der
# Server im Drain: der Marker wird nicht automatisch entfernt, weil ein halb
# fertiger Server sonst wieder in die Rotation kaeme (/api/health kann 200
# liefern, waehrend /api/ready an Datenbank, Migration oder R2 scheitert).
# Freigabe erst nach Behebung/Rollback mit `sh deploy/freigeben.sh`.
#
# Scheitert die Abschlusspruefung (Schritt 6), ist der Server schon wieder
# in der Rotation und BLEIBT dort (Begruendung bei Schritt 6). Das Skript
# meldet dann KEIN "FERTIG", endet mit Code 3 und sagt, was zu pruefen ist:
# NICHT auf dem anderen Server weitermachen, bis die Ursache geklaert ist.
#
# Rueckgabecodes:
#   0        FERTIG (Abschlusspruefung fehlerfrei oder bewusst uebersprungen)
#   1        Abbruch in Schritt 1-5, Server BLEIBT im Drain
#   2        Konfiguration fehlt (Verzeichnis, PUBLIC_HOST) oder der Drain-Marker
#            laesst sich nicht setzen — nichts veraendert, Server in der Rotation
#   3        Server in der Rotation, Abschlusspruefung gescheitert oder nicht
#            durchgelaufen — nicht auf dem anderen Server fortfahren
#   130/143  von Hand unterbrochen (Strg+C / kill); die Meldung sagt, ob der
#            Server im Drain oder in der Rotation steht
#
# Aufruf (erst auf prod2, nach "FERTIG" auf prod1):
#   prod2:  cd /opt/autoschnell && ERSTER_SERVER=1 sh deploy/rollout.sh
#   prod1:  cd /opt/autoschnell && sh deploy/rollout.sh
# Umgebung: WARTE_LB (Sekunden je LB-Umschaltung, Standard 60),
#           SCHLAF (Wartetakt der Bereitschaftsschleifen, Standard 3),
#           VERZ (Checkout, Standard /opt/autoschnell),
#           ERSTER_SERVER=1 (erster von zwei Servern: die Probe wertet einen
#             404 fuer das Oberflaechen-Skript nur als Warnung, weil der andere
#             Server das neue Bundle noch nicht kennt — betriebsprobe.py
#             --zwischenstand; streng geprueft wird nach dem zweiten Server),
#           OHNE_AUSSENPROBE=1 (Abschlusspruefung bewusst ueberspringen, z.B.
#             bei einer Cloudflare-Stoerung; wird laut gemeldet, die Probe muss
#             dann vor dem anderen Server von Hand laufen).
#           Nur der Wert 1 schaltet; alles andere gilt als nicht gesetzt.
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
# Runde 21 (Gegenpruefung D): Laesst sich der Host-Marker gar nicht setzen
# (Rechte auf deploy/drain), meldete abbruch() frueher "BLEIBT im Drain" —
# falsch, der Server war nie im Drain. Jetzt: Rollout nicht starten, Code 2,
# Meldung "unveraendert in der Rotation". Existiert der Marker bereits (z.B.
# root-eigen nach einem frueheren Abbruch), IST der Server im Drain und das
# Rollout laeuft weiter; ein spaeteres Entfernen scheitert dann laut in undrain.
drain() {
    { mkdir -p deploy/drain && touch deploy/drain/aktiv; } || true
    if [ ! -e deploy/drain/aktiv ]; then
        trap - EXIT INT TERM
        echo "FEHLER: Drain-Marker deploy/drain/aktiv laesst sich nicht setzen (Rechte? ls -ld deploy/drain)."
        echo "   Rollout NICHT gestartet — dieser Server ist unveraendert in der Rotation, es wurde nichts geaendert."
        echo "   Rechte beheben (als der Benutzer arbeiten, dem der Checkout gehoert), dann erneut: sh deploy/rollout.sh"
        exit 2
    fi
    docker compose exec -T proxy sh -c 'touch /tmp/drain' >/dev/null 2>&1 || true
    echo "   Drain gesetzt — der Load Balancer nimmt diesen Server in ca. 45 s aus der Rotation"
}
# Runde 21 (Pruefbefund D, Zusatz): Laesst sich der Host-Marker nicht
# entfernen (z.B. root-eigene Datei), steht der Server weiter im Drain. Das
# muss laut enden: undrain scheitert, abbruch() meldet "BLEIBT im Drain".
# Frueher war FERTIG=1 da schon gesetzt, der EXIT-Trap schwieg, und das
# Skript endete nur mit der rohen rm-Fehlerzeile.
undrain() {
    rm -f deploy/drain/aktiv || true
    if [ -e deploy/drain/aktiv ]; then
        echo "FEHLER: Drain-Marker deploy/drain/aktiv laesst sich nicht entfernen (Rechte? ls -l deploy/drain/aktiv)"
        return 1
    fi
    docker compose exec -T proxy sh -c 'rm -f /tmp/drain' >/dev/null 2>&1 || true
}
DRAIN_AUFGEHOBEN=0
# Drain-Phase (Schritt 1-5). Runde 21: Signale bekommen ihren eigenen Code
# und der Trap wird vor der Meldung abgeraeumt — sonst loest das "exit" im
# INT/TERM-Trap den EXIT-Trap erneut aus (Meldung doppelt), und bei
# "kill -TERM" waehrend eines Befehls war der Code 0.
abbruch() {
    rc=${1:-$?}
    # Signal genau zwischen undrain und dem Trap-Wechsel: der Server ist
    # schon in der Rotation, also die Rotations-Meldung (stimmt dann).
    [ "$DRAIN_AUFGEHOBEN" = 1 ] && in_rotation_abbruch "$rc"
    trap - EXIT INT TERM
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
    case $rc in 130|143) exit "$rc" ;; esac
    exit 1
}
trap abbruch EXIT
trap 'abbruch 130' INT
trap 'abbruch 143' TERM

echo "== 1/6 Drain (Health -> 503), warte ${WARTE_LB}s"
drain
sleep "$WARTE_LB"

# Runde 26 (12.09.2026): Die nginx-Vorlage wird NUR beim Start des
# Proxy-Containers ausgewertet (envsubst im nginx-Einstiegsskript) und liegt
# als Bind-Mount im Container. Aendert sie sich durch den Pull, muss der
# Proxy neu erzeugt werden — 'up -d --build' allein taete das nicht, und die
# Aenderung (z.B. Besucher-IP an das Backend) waere still wirkungslos.
VORLAGE=$(grep '^PROXY_TEMPLATE=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
VORLAGE=deploy/${VORLAGE:-default.conf.template}
# Immer erfolgreich: fehlt die Datei (oder md5sum), ist der Stand leer —
# ein Fehlercode wuerde hier wegen 'set -e' das ganze Rollout abbrechen.
vorlagen_stand() {
    if [ -f "$VORLAGE" ]; then
        md5sum "$VORLAGE" 2>/dev/null | cut -d' ' -f1 || true
    fi
    return 0
}
VORLAGE_VORHER=$(vorlagen_stand)
# Runde 29 (12.09.2026, Vorfall Cloudflare-404): Der Vergleich VOR/NACH dem
# Pull greift nur, wenn rollout.sh selbst pullt. Wer vorher von Hand pullt
# (so steht es im Handbuch), bekommt hier nur noch Already up to date — die
# geaenderte Vorlage blieb dann still wirkungslos, weil der Proxy seine
# Konfiguration NUR beim Start auswertet. Genau deshalb fehlte am 12.09. die
# Regel, fehlende Bundle-Dateien nicht zwischenspeichern zu lassen, und
# Cloudflare hielt ein 404 fest (leere Seite fuer alle Besucher).
# Jetzt merkt sich der Server, mit welcher Vorlage der Proxy zuletzt erzeugt
# wurde — unabhaengig davon, wer gepullt hat.
VORLAGE_MARKE=deploy/.proxy-vorlage
if [ "$(vorlagen_stand)" != "$(cat "$VORLAGE_MARKE" 2>/dev/null || echo "")" ]; then
    PROXY_NEU=1
    echo "   Proxy laeuft nicht mit der aktuellen nginx-Vorlage — er wird neu erzeugt"
fi

echo "== 2/6 Code holen"
git pull --ff-only
if [ "$(vorlagen_stand)" != "$VORLAGE_VORHER" ]; then
    PROXY_NEU=1
    echo "   nginx-Vorlage geaendert ($VORLAGE) — der Proxy wird neu erzeugt"
fi

# Runde 29 (12.09.2026): Die Bundle-Dateien des VORHERIGEN Standes aufheben.
# Wer die App offen hat, laedt nach dem Rollout noch Dateien des alten
# Standes nach (Vite teilt den Code in viele Haeppchen auf) — ohne Kopie
# gibt es dafuer 404 und einen schwarzen Bildschirm bis zum Neuladen.
# Der Ordner haengt schreibgeschuetzt im Oberflaechen-Container.
mkdir -p deploy/assets-alt/static 2>/dev/null || true
WEB_ALT=$(docker compose ps -q web 2>/dev/null || true)
if [ -n "$WEB_ALT" ]; then
    # Gegenpruefung 12.09.2026: "docker cp" uebernimmt die Zeitstempel AUS
    # DEM IMAGE (Bauzeit). Ein "find -mtime +14" direkt danach haette die
    # gerade kopierten Dateien sofort wieder geloescht — der Rueckfall waere
    # genau dann leer gewesen, wenn er gebraucht wird. Deshalb: erst in einen
    # Zwischenordner kopieren, dort die Zeitstempel auf JETZT setzen, dann
    # hinueberlegen (cp -a behaelt die aufgefrischte Zeit).
    rm -rf deploy/assets-alt/.neu 2>/dev/null || true
    mkdir -p deploy/assets-alt/.neu 2>/dev/null || true
    if docker cp "$WEB_ALT:/usr/share/nginx/html/static/." deploy/assets-alt/.neu/ 2>/dev/null; then
        find deploy/assets-alt/.neu -type f -exec touch {} + 2>/dev/null || true
        cp -a deploy/assets-alt/.neu/. deploy/assets-alt/static/ 2>/dev/null || true
    fi
    rm -rf deploy/assets-alt/.neu 2>/dev/null || true
fi
# Nach 14 Tagen aufraeumen, damit der Ordner nicht unbegrenzt waechst. Zaehlt
# ab dem AUFHEBEN (siehe touch oben), nicht ab der Bauzeit des Images.
find deploy/assets-alt/static -type f -mtime +14 -delete 2>/dev/null || true

echo "== 3/6 Bauen und starten"
docker compose up -d --build
if [ "$PROXY_NEU" = 1 ]; then
    # Der Drain-Marker auf dem Host (deploy/drain/aktiv) ueberlebt das,
    # der Server bleibt also waehrenddessen aus der Rotation.
    docker compose up -d --force-recreate --no-deps proxy
    # Merken, mit welcher Vorlage der Proxy jetzt laeuft (Runde 29).
    # Nicht abbrechen, wenn sich die Marke nicht schreiben laesst (set -e):
    # der Proxy laeuft dann schon richtig, nur die Erkennung greift beim
    # naechsten Mal erneut — das ist harmlos, ein Abbruch im Drain nicht.
    vorlagen_stand > "$VORLAGE_MARKE" 2>/dev/null || true
fi

echo "== 4/6 Warten, bis Backend und Oberflaeche antworten"
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

# Abschlusspruefung vorbereiten. Der Befehl steht auch in den Meldungen,
# damit er von Hand genau so wiederholt werden kann.
# DKIM mitpruefen, wenn der Selector in der .env steht (z.B. DKIM_SELECTOR=resend)
DKIM=$(grep '^DKIM_SELECTOR=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
ZWISCHEN=
if [ "$ERSTER_SERVER" = 1 ]; then
    ZWISCHEN=1
fi
PROBE_BEFEHL="docker compose exec -T backend python scripts/betriebsprobe.py $PUBLIC_HOST${DKIM:+ --dkim-selector $DKIM}${ZWISCHEN:+ --zwischenstand}"

# Runde 21 (Pruefbefund D): Ab Schritt 5 ist der Server wieder in der
# Rotation. Endet das Skript von dort bis zum Ergebnis der Abschlusspruefung
# unerwartet (Strg+C, kill, Fehler), darf das weder still passieren noch wie
# ein FERTIG aussehen — und es darf nicht "im Drain" heissen.
in_rotation_abbruch() {
    rc=${1:-$?}
    trap - EXIT INT TERM
    echo ""
    echo "UNTERBROCHEN nach der Freigabe (Code $rc): dieser Server ist WIEDER IN DER ROTATION"
    echo "   (Drain aufgehoben), die Abschlusspruefung von aussen ist aber NICHT durchgelaufen."
    echo "   NICHT auf dem anderen Server fortfahren, bevor diese Probe von Hand fehlerfrei"
    echo "   war ('ERGEBNIS: ... 0 Fehler'):"
    echo "     $PROBE_BEFEHL"
    case $rc in 130|143) exit "$rc" ;; esac
    exit 3
}

echo "== 5/6 Drain aufheben, warte ${WARTE_LB}s bis der Load Balancer den Server wieder fuehrt"
# Reihenfolge (Runde 21): erst den Marker wirklich entfernen, dann den
# Drain-Trap abloesen. Scheitert undrain, laeuft abbruch() und die Meldung
# "BLEIBT im Drain" stimmt. Keine doppelte Freigabe: der Drain wird nur hier
# aufgehoben, danach nie wieder gesetzt.
undrain
DRAIN_AUFGEHOBEN=1
trap in_rotation_abbruch EXIT
trap 'in_rotation_abbruch 130' INT
trap 'in_rotation_abbruch 143' TERM
# Die Wartezeit gilt auch mit OHNE_AUSSENPROBE=1: erst wenn der LB diesen
# Server wieder fuehrt, darf der andere in den Drain — sonst haette der LB
# kein gesundes Ziel mehr.
sleep "$WARTE_LB"

echo "== 6/6 Abschlusspruefung von aussen (ueber Cloudflare und Load Balancer, wie ein Besucher)"
# Erkennt u.a. einen im Cloudflare-Cache festgehaltenen 502 fuer das
# Oberflaechen-Skript (Vorfall 07.09.2026: schwarzer Bildschirm trotz
# gesunder Server).
#
# Runde 21 (Pruefbefund D): Frueher wurde ein Fehler hier nur ausgegeben und
# danach trotzdem "FERTIG ... jetzt denselben Befehl auf dem anderen Server"
# gemeldet, mit Code 0. Jetzt gilt: Probe rot -> kein FERTIG, Code 3, klare
# Anweisung, NICHT auf dem anderen Server fortzufahren, plus Pruefschritte.
#
# Entscheidung: nach einer gescheiterten Probe bleibt dieser Server IN DER
# ROTATION, es gibt KEINEN automatischen erneuten Drain. Gruende:
#   - Die eigenen Pruefungen dieses Servers (Schritt 4: /api/ready und
#     Startseite ueber den Proxy) waren gerade gruen — genau die Kriterien
#     von deploy/freigeben.sh. Ein erneuter Drain waere mit denselben, schon
#     bestandenen Pruefungen wieder aufzuheben und schuetzt nicht.
#   - Die Probe geht ueber Cloudflare, den Load Balancer und BEIDE Server und
#     prueft auch DNS, TLS, Mail-DNS und Ports. Liegt die Ursache am anderen
#     Server oder im Cache, nimmt ein automatischer Drain den gesunden Server
#     heraus — im schlimmsten Fall hat der LB dann gar kein Ziel mehr.
#   - Ein wirklich defekter Server faellt ueber den LB-Health-Check heraus
#     (/api/health prueft per auth_request auch die Oberflaeche).
#   - Ein automatischer Drain waere wieder ein Drain, den man vergessen kann.
# Wer den Server trotzdem herausnehmen will, tut das bewusst von Hand
# (touch deploy/drain/aktiv, Freigabe spaeter mit sh deploy/freigeben.sh).
if [ "$OHNE_AUSSENPROBE" = 1 ]; then
    trap - EXIT INT TERM
    echo "ACHTUNG: Abschlusspruefung von aussen UEBERSPRUNGEN (OHNE_AUSSENPROBE=1, bewusst gesetzt)."
    echo "   Vor dem anderen Server von Hand ausfuehren und nur bei 'ERGEBNIS: ... 0 Fehler' weitermachen:"
    echo "     $PROBE_BEFEHL"
    echo "FERTIG auf $(hostname) (OHNE Abschlusspruefung) — erst die Probe von Hand, dann derselbe Befehl auf dem anderen Server."
    exit 0
fi
if [ -n "$ZWISCHEN" ]; then
    echo "   ERSTER_SERVER=1: ein 404 fuer das Oberflaechen-Skript zaehlt nur als Warnung (Bundle-Wechsel zwischen den Servern)"
fi
PROBE_RC=0
docker compose exec -T backend python scripts/betriebsprobe.py "$PUBLIC_HOST" ${DKIM:+--dkim-selector "$DKIM"} ${ZWISCHEN:+--zwischenstand} || PROBE_RC=$?
trap - EXIT INT TERM
if [ "$PROBE_RC" != 0 ]; then
    case $PROBE_RC in
        # Code 1 kommt auch von "docker compose exec" (Container gestoppt) und
        # von einem Absturz der Probe (Traceback) — dann fehlt die ERGEBNIS-Zeile.
        1) DEUTUNG="die Probe meldet Fehler, Liste oben unter 'ERGEBNIS' — steht dort keine ERGEBNIS-Zeile, lief die Probe gar nicht: Meldung oben lesen, docker compose ps" ;;
        2) DEUTUNG="Aufruf der Probe abgelehnt — unbekannte Option, Backend-Image zu alt?" ;;
        *) DEUTUNG="die Probe konnte nicht laufen — Backend-Container / docker compose exec pruefen" ;;
    esac
    echo ""
    echo "ABSCHLUSSPRUEFUNG GESCHEITERT auf $(hostname) (Probe-Code $PROBE_RC: $DEUTUNG)."
    echo "   Dieser Server ist WIEDER IN DER ROTATION (Drain aufgehoben; seine eigenen Pruefungen"
    echo "   /api/ready und Startseite waren gruen). Er wird absichtlich NICHT erneut gedrained:"
    echo "   die Probe geht ueber Cloudflare, Load Balancer und BEIDE Server — die Ursache kann"
    echo "   auch am anderen Server oder im Cache liegen."
    echo ""
    echo "   >>> NICHT auf dem anderen Server fortfahren, bis die Ursache geklaert ist! <<<"
    echo "   (War der andere Server schon ausgerollt, dort nichts erneut starten — nur die Ursache klaeren.)"
    echo ""
    echo "   Pruefen:"
    echo "   - Fehlerliste der Probe oben lesen ('ERGEBNIS: ... Fehler' und die Zeilen darunter)."
    echo "   - Probe von Hand wiederholen:"
    echo "       $PROBE_BEFEHL"
    echo "   - Meldet sie '... aus dem Cloudflare-Cache, der Server liefert 200':"
    echo "       Cloudflare -> Caching -> Configuration -> Purge Everything, dann Probe erneut."
    if [ -z "$ZWISCHEN" ]; then
        echo "   - Ist dies der ERSTE Server und der einzige Fehler ein 404 fuer /static/js/main.*.js"
        echo "     (der andere Server kennt das neue Oberflaechen-Bundle noch nicht): Probe mit"
        echo "     --zwischenstand wiederholen; ist sie dann fehlerfrei, darf der andere Server"
        echo "     ausgerollt werden (kuenftig auf dem ersten Server: ERSTER_SERVER=1 sh deploy/rollout.sh)."
    fi
    echo "   - Anderen Server pruefen: Hetzner-Konsole -> Load Balancer -> Ziele gesund? Dort:"
    echo "       docker compose ps; docker compose exec -T backend curl -s http://localhost:8001/api/ready"
    echo "   - Diesen Server pruefen: docker compose ps; docker compose logs --tail 80 backend web proxy"
    echo "   - Ist DIESER Server die Ursache: von Hand aus der Rotation nehmen (touch deploy/drain/aktiv),"
    echo "     beheben oder Rollback (DEPLOYMENT.md), danach sh deploy/freigeben.sh."
    echo "   Erst wenn die Probe fehlerfrei ist ('ERGEBNIS: ... 0 Fehler'): sh deploy/rollout.sh auf dem anderen Server"
    echo "   (falls dort noch nicht ausgerollt)."
    exit 3
fi
if [ -n "$ZWISCHEN" ]; then
    echo "FERTIG auf $(hostname) (erster Server, Zwischenstand geprueft) — jetzt 'sh deploy/rollout.sh' OHNE ERSTER_SERVER auf dem anderen Server; dort wird streng geprueft."
else
    echo "FERTIG auf $(hostname) — Abschlusspruefung fehlerfrei; jetzt denselben Befehl auf dem anderen Server (falls dort noch nicht ausgerollt)."
fi
