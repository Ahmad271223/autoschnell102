# AutoSchnell

Plattform für Autohändler: Inserate von Kleinanzeigen, mobile.de und
AutoScout24 vergleichen, Kaufverträge erstellen und versenden, Abholung
über die Fahrer-App, Beweisdokumente auf Knopfdruck. Betrieb auf zwei
Hetzner-Servern hinter einem Load Balancer (MongoDB als Replica Set,
Dateien in Cloudflare R2).

| Teil | Wo |
| --- | --- |
| Backend (FastAPI, Python 3.12) | `backend/` — Start im Container: `python migrationen.py && uvicorn server:app` |
| Oberfläche (React 19, Vite 6) | `frontend/` — Befehle in [frontend/README.md](frontend/README.md) |
| Betrieb, Updates, Backups, Restore | [DEPLOYMENT.md](DEPLOYMENT.md) (Rollout Server für Server: `deploy/rollout.sh`) |
| Vor dem Live-Gang | [GO-LIVE-CHECKLISTE.md](GO-LIVE-CHECKLISTE.md), [docs/STAGING-CHECKLISTE.md](docs/STAGING-CHECKLISTE.md) |
| Kleinanzeigen-Abruf (wer ruft mit welcher IP ab) | [docs/kleinanzeigen-abruf.md](docs/kleinanzeigen-abruf.md) |
| Übersprungene Tests der Backend-Suite | [docs/tests/UEBERSPRUNGENE_TESTS.md](docs/tests/UEBERSPRUNGENE_TESTS.md) |
| Lasttests | `docs/lasttests/`, Skripte in `backend/scripts/lasttest_*.py`, auf prod2: `deploy/lasttest-auf-prod2.sh` |

Lokal starten: `docker compose up -d --build` mit einer `.env` nach
`.env.example` (Pflichtwerte prüft `backend/production_check.py` beim Start).
Backend-Tests: im Ordner `backend` `python -m pytest tests/ -q`
(Voraussetzungen und CI-gleicher Lauf in `docs/tests/UEBERSPRUNGENE_TESTS.md`).

`memory/PRD.md` ist die historische Produktbeschreibung von Februar 2026 und
beschreibt nicht mehr den heutigen Stand (Stripe, Playwright-Snapshots und
die Selbstregistrierung sind entfernt).
