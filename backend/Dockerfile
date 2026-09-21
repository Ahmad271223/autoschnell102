# AutoSchnell Backend — FastAPI (ohne Browser: Beweisdokumente entstehen mit ReportLab)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl fuer den Health-Check; fonts-liberation fuer die PDFs (Beweisdokument:
# Umlaute, Euro-Zeichen, fremde Buchstaben in Inseratstexten).
# Audit 09/2026: Betriebssystem-Pakete auf den letzten Stand (Image-Scan
# in CI blockiert bei bekannten, behebbaren Schwachstellen).
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    curl ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Nicht als root laufen (Sicherheit). /app beschreibbar.
# Runde 5: Backup- und Upload-Verzeichnisse (local_storage: lokale Kopien alter
# Snapshots bis zu deren Verfall) existieren im Image und
# gehoeren 'app' — benannte Docker-Volumes uebernehmen diese Rechte beim
# ersten Start (sonst waren /backups und /app/local_storage nicht beschreibbar).
RUN useradd -m app && mkdir -p /app/uploads /app/local_storage /backups \
    && chown -R app:app /app /backups
USER app

EXPOSE 8001

# Health-Check: der Container gilt als "unhealthy", wenn /api/health nicht 200 liefert.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8001/api/health || exit 1

# Produktion: mehrere Worker-Prozesse (Anzahl via WEB_CONCURRENCY, Default 8).
# ProactorEventLoop-Wahl passiert in server.py nur auf Windows; unter Linux
# ist der Default-Event-Loop korrekt.
# Audit 09/2026 (Punkt 18/19): ZUERST genau eine Migration (Indizes, Seeds,
# versionierte Datenmigrationen — mit Sperre), DANN die Worker. Default 4
# Worker (statt 8) — WEB_CONCURRENCY an die Servergroesse anpassen.
# Pruefbericht 20.09.2026 (SV-01/S1): Mit --forwarded-allow-ips='*' nahm
# uvicorn den LINKESTEN Eintrag aus X-Forwarded-For als Besucheradresse —
# also den, den der Aufrufer selbst mitschickt (nginx haengt die echte Adresse
# nur hinten an). Jede Anmelde-, Konto- und Fahrersperre war damit per frei
# gewaehlter Kopfzeile auszuhebeln. Jetzt gelten nur die eigenen Netze (nginx
# im Docker-Netz, LB im privaten Netz) als Vermittler; uvicorn nimmt dann den
# rechtesten FREMDEN Eintrag = die Adresse, die nginx eingetragen hat.
# Pruefbericht 20.09.2026 (DP-06): "exec" — sonst bleibt sh Prozess 1 und
# reicht das SIGTERM von "docker compose up/stop" NIE an uvicorn weiter; nach
# 10 s kam SIGKILL mitten in laufende Anfragen und Hintergrundarbeiten.
# uvicorn beendet sich jetzt geordnet (hoechstens 50 s, stop_grace_period 60 s).
CMD ["sh", "-c", "python migrationen.py && exec uvicorn server:app --host 0.0.0.0 --port 8001 --workers ${WEB_CONCURRENCY:-4} --proxy-headers --forwarded-allow-ips=\"${FORWARDED_ALLOW_IPS:-127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16}\" --timeout-graceful-shutdown 50"]
