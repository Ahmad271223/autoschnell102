# AutoSchnell Backend — FastAPI + Playwright (Snapshots)
FROM python:3.12-slim

# Playwright legt seine Browser hier ab (siehe snapshot_service.py).
ENV PLAYWRIGHT_BROWSERS_PATH=/pw-browsers \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System-Abhaengigkeiten fuer Chromium (Snapshots) + curl fuer Health-Check.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

# Chromium (headless-shell) fuer Playwright installieren — inkl. OS-Libs.
RUN playwright install --with-deps chromium

COPY . .

# Nicht als root laufen (Sicherheit). /app + Browser-Ordner beschreibbar.
RUN useradd -m app && mkdir -p /pw-browsers /app/uploads \
    && chown -R app:app /app /pw-browsers
USER app

EXPOSE 8001

# Health-Check: der Container gilt als "unhealthy", wenn /api/health nicht 200 liefert.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8001/api/health || exit 1

# Produktion: mehrere Worker-Prozesse (Anzahl via WEB_CONCURRENCY, Default 8).
# ProactorEventLoop-Wahl passiert in server.py nur auf Windows; unter Linux
# ist der Default korrekt fuer Playwright-Subprozesse.
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port 8001 --workers ${WEB_CONCURRENCY:-8} --proxy-headers --forwarded-allow-ips='*'"]
