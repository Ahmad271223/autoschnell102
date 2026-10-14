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

Browser-Tests lokal (wie in `ci.yml`): Backend im Anbieter-Mock-Modus auf Port 8002
starten, dann

```
APP_FASSUNG=1700000000-aaaaaaa REACT_APP_BACKEND_URL= yarn build && yarn e2e
```

`APP_FASSUNG` ist bewusst ein **alter** Fassungs-Stempel — `e2e/fassung.spec.js` prüft
den Versionshinweis und überspringt ohne ihn (Prüfbericht 20.09.2026, T-17).

Rauchtest gegen den echten Compose-Stack (nginx mit HTTPS, Backend im Container,
MongoDB mit Passwort; nur `e2e/stack.spec.js`, kein eigener Test-Server):

```
E2E_STACK=1 E2E_BASE_URL=https://localhost yarn e2e
```

Die Super-Admin-Zugangsdaten für den Anmeldetest kommen wie in der übrigen Suite aus
`E2E_SUPER_ADMIN_USERNAME`/`E2E_SUPER_ADMIN_PASSWORD`, sonst `SUPER_ADMIN_*` aus der
Umgebung oder — nur lokal — aus `backend/.env` (`e2e/helpers.js`, `h.SUPER_ADMIN`).

Konfiguration: `vite.config.mjs` (CSP-Platzhalter, Proxy, Ausgabe), `.env.example`.
Seiten werden bei Bedarf nachgeladen (`src/App.jsx`); angemeldete Nutzer bekommen
die Arbeitsseiten im Leerlauf vorgeladen.

Installierbare App: `public/manifest.json` (Einstieg `/start`), `public/service-worker.js`
(ohne Zwischenspeicher, nur Offline-Seite), Knopf `src/components/InstallPWAButton.jsx`,
Wege je Browser in `src/lib/installation.js`. Die App-Symbole erzeugt
`scripts/app_symbole.py` (Python mit Pillow: `python scripts/app_symbole.py public`)
aus dem Lucide-Symbol „bolt“ (Logo der Startseite); Details und Notbremse in
`DEPLOYMENT.md`, „Installierbare App“.
