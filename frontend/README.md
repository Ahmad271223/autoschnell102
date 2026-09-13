# AutoSchnell — Oberflaeche

React 19 mit Vite 6 (seit 09/2026, vorher Create React App).

| Befehl | Wirkung |
| --- | --- |
| `yarn start` / `yarn dev` | Entwicklungsserver auf Port 3000 (`PORT` in `.env`), `/api` geht an das Backend auf Port 8001 (`BACKEND_PROXY_TARGET`) |
| `yarn build` | Produktions-Build nach `build/` (Hauptskript `static/js/main.<hash>.js`) |
| `yarn test` | Unit-Tests mit Vitest (einmaliger Lauf) |
| `yarn lint` | ESLint mit den React-Hook-Regeln, Warnungen sind Fehler |
| `yarn preview` | fertigen Build lokal ansehen (nur localhost, ohne /api) |
| `yarn e2e` | Browser-Tests mit Playwright gegen `build/` (siehe `playwright.config.js`) |

Konfiguration: `vite.config.mjs` (CSP-Platzhalter, Proxy, Ausgabe), `.env.example`.
Seiten werden bei Bedarf nachgeladen (`src/App.jsx`); angemeldete Nutzer bekommen
die Arbeitsseiten im Leerlauf vorgeladen.

Installierbare App: `public/manifest.json` (Einstieg `/start`), `public/service-worker.js`
(ohne Zwischenspeicher, nur Offline-Seite), Knopf `src/components/InstallPWAButton.jsx`,
Wege je Browser in `src/lib/installation.js`. Die App-Symbole erzeugt
`scripts/app_symbole.py` (Python mit Pillow: `python scripts/app_symbole.py public`)
aus dem Lucide-Symbol „bolt“ (Logo der Startseite); Details und Notbremse in
`DEPLOYMENT.md`, „Installierbare App“.
