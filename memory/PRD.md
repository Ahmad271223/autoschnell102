# PRD — Autohandel SaaS (mobile.de Filter-Generator)

## Original Problem Statement
Eine SaaS-Web-App für deutsche Autohändler. Händler gibt mobile.de-URL ein → App holt Fahrzeugdaten → öffnet mobile.de mit den entsprechenden Filtern.

## Personas
- Autohändler (Admin) — generiert Filter-Links und Kaufverträge.

## Core Features
- Mobile.de URL einlesen (`mobile_service.py`, aktuell via Sandbox-XML)
- Vergleichs-URL im kompakten `ms=`-Format generieren
- Kaufvertrag-PDF (reportlab) inkl. AGBs/Vereinbarungen
- Apple-Style UI: Termine, Einstellungen, Vergleich
- Auth (admin@autohandel.app / Admin123!)

## Implemented (Changelog)
- 2026-02: Apple-Style UI (Termine, Einstellungen, Vergleich, AppLayout)
- 2026-02: AGBs/Besondere Vereinbarungen speicherbar + im PDF
- 2026-02: URL-Builder kompaktes Format (`ms=...`), PS-Filter (`pw=`) korrigiert
- 2026-02-26: "Filter öffnen"-Button im Header neben "Vergleich"-Button (Vergleich.jsx)
- 2026-02-27: Kaufvertrag — 2-Spalten Verkäufer/Käufer-Layout, Felder Bereifung, HU/AU, unfallfrei, EU-Import, fahrtauglich, gewerblich, Vorhalter (PDF + ContractDialog), `/api/contracts/preview` Live-Vorschau
- 2026-02-27: Light-/Dark-Mode-Toggle (`ThemeToggle.jsx`), CSS-Token-System in `index.css`, alle Apple-Style-Pages migriert
- 2026-02-27: **Dynamische Make/Model-Auflösung** in `mobile_service.py` — alle 178 Marken & 2721 Modelle aus `mobile_makes_models.json` (User-Upload `allemodellefinal.txt`); Hardcoded `MAKE_ID_MAP` / `VERIFIED_MAKE_IDS` / `MODEL_ID_MAP` ersatzlos entfernt. Korrigierte IDs: Citroën 5500→5900, Kia 13600→13200, Mini 17700→17500. Lookup-Strategie: normalize (lowercase, ohne Diakritika) → exakt → Prefix-Shrink → Token-Fallback. Tests: `tests/test_mobile_service_makes.py`.
- 2026-02-27: **Land-Filter "Nur DE"** im Vergleich. URL-Builder hängt `cn=DE` (default) bzw. mehrere `cn=` Parameter an. Frontend `CountryPicker.jsx` mit 3 Modi (Nur DE / Alle / Auswählen) und Multi-Select-Grid mit allen 60 Ländern.
- 2026-02-27: **Kleinanzeigen-URL-Parser** (`kleinanzeigen_service.py`). `/api/mobile/compare` erkennt automatisch kleinanzeigen.de vs. mobile.de URLs. Parser zieht Marke, Modell, KM, Erstzulassung, PS/kW, Kraftstoff, Getriebe, HU, Farbe, Vorhalter, Beschreibung, Ausstattung & bis zu 60 Inserat-Fotos. Gemappt auf 178-Marken-Catalogue → erzeugt mobile.de-Vergleichs-URL. Fotos-Galerie in Vergleich.jsx eingebaut. Tests: `tests/test_kleinanzeigen_service.py` (3 Tests, 32 gesamt grün).
- 2026-02-27: **KA-Parser Bugfixes**: (a) Modell-Lookup mit Klammer-Varianten korrigiert — JSON enthält Namen wie `"Aygo (X)"`, `_load_makes_models` indexiert jetzt zusätzlich die parens-stripped Form. KA-Parser cleant Modellname vor Lookup. → `Toyota Aygo` löst jetzt korrekt zu `ms=24100;5;;;` auf. (b) Beschreibung wird im Vergleich-UI angezeigt (neuer Block unter Ausstattung). (c) Equipment-Filter entfernt Müll-Items wie "Der Preis ist Verhandlungsbasis.", "Privatanbieter", "41352 Korschenbroich", "Deutschland".
- 2026-02-27: **Globaler `errMsg`-Helper** (`/app/frontend/src/lib/api.js`) — alle 11 `toast.error(...)`-Aufrufe normalisiert; FastAPI-422-Pydantic-Errors werden nicht mehr als Object-Array gerendert (kein React-Crash mehr).
- 2026-02-27: **Auto-Snapshot Inserat-Seite** (Beweis-Archiv) — Playwright headless rendert KA/mobile.de Inserate beim Vergleich asynchron in PNG + PDF, persistiert in **Emergent Object Storage**. Neuer Service `snapshot_service.py`, neue Collection `listing_snapshots`, Endpoints `GET /api/snapshots`, `GET /api/snapshots/{id}`, `GET /api/snapshots/{id}/{pdf|png}` (Auth via Header oder `?auth=` Query-Param für `<iframe>`/`<img>`-Direktnutzung). Frontend `SnapshotCard.jsx` mit Live-Polling (4s), Status-Badges (läuft / fertig / Fehler) und Download-Buttons mit Bytes-Info. Erfolgt automatisch im Hintergrund bei jedem `/api/mobile/compare`. End-to-End verifiziert (snapshot ready in 5s, PDF 62 KB / PNG 102 KB).
- 2026-02-07: **Apple-Style Admin-Dashboard** (`/admin/*`). Super-Admin Login per Username **CashCarHannover2025** / Passwort **MaW34543WaM** (`/auth/login` akzeptiert E-Mail oder Username). Neue React-Struktur unter `/app/frontend/src/pages/admin_v2/` (AdminLayout mit Sidebar + Light-Theme, Overview, Users, UserDetail, Comparisons, UrlStats, Settings) — als verschachtelte Routen in `App.js` registriert. Backend-Endpoints (alle Admin-only):
  - `GET /admin/users` (mit Subscription, Company, Active-Status)
  - `POST /admin/users/{id}/active` — **Soft-Block** (Login → 403 "Account ist deaktiviert"); Schutz gegen Selbst- und Super-Admin-Sperre
  - `POST /admin/users/{id}/password` — Admin Reset (min. 8 Zeichen, invalidiert Session)
  - `POST /admin/me/password` — eigenes Admin-Passwort (alt + neu, min. 8)
  - `GET /admin/users/{id}/contracts` — read-only Verträge eines Nutzers
  - `GET /admin/contracts/{id}/pdf` — Streaming-PDF für Admin-Einsicht
  - `GET /admin/comparisons` — Aggregat „welches Auto, von welcher Plattform, wie oft, von wem" (mobile/kleinanzeigen/autoscout)
  - `GET /admin/url-stats` — Live URL-Counter, 4 Zeitfenster (Heute / 24 h / 7 Tage / Gesamt)
  - `GET /admin/stats` — KPIs (users / active_subs / contracts / appointments / comparisons_today)
  Frontend pollt URL-Statistik & Übersicht alle **60 s**. 19/19 Backend-Tests grün, Frontend E2E grün.
- 2026-02-08: **Admin-Dashboard auf Dark-Theme** umgestellt (passend zum Rest der App). Komplette Migration aller 6 admin_v2-Seiten + `_ui.jsx` (Card/Button/Badge/EmptyState) auf #0a0a0a-Hintergrund, weiße Schrift, rote Akzente, weiße Hairlines (rgba 8%). Außerdem: Super-Admin-Schutz auch auf Legacy-Endpoints — `PUT /admin/users/{id}` blockt Demote (`role!=admin`) & Soft-Block des Super-Admins; `DELETE /admin/users/{id}` blockt Löschen; beide blocken auch Self-Lockout/Self-Delete.
- 2026-02-08: **Backend-Refactoring (Phase 1)** — `server.py` von 2541 auf **2004 Zeilen** reduziert (-21 %). Neu:
  - `deps.py` (103 Zeilen): zentrale Shared-Code-Module für DB-Client, `bearer`, `current_user`, `current_admin`, `get_subscription_status`, `require_active_sub`, `now_iso`, `clean_doc`, `log_activity`. Alle anderen Module importieren von hier.
  - `routes/auth.py` (132 Zeilen): `/api/auth/register|login|logout|me` + `RegisterIn/LoginIn/TokenOut`-Models.
  - `routes/admin.py` (404 Zeilen): alle `/api/admin/*`-Endpoints + Admin-Models. Beinhaltet `cleanup/run`, Users CRUD + `active`/`password`-Toggles, Contracts (read-only Admin-Views), Stats, Comparisons, URL-Stats, Self-Password-Change.
  Mounting im `server.py` via `api.include_router(...)`. 19/19 admin pytest + 8/8 service-Tests grün, alle End-to-End curl-Smoke-Tests bestätigt (login/me/stats/users/url-stats/comparisons/block/unblock/password-reset/register).
- 2026-02-08: **Backend-Refactoring (Phase 2)** — `server.py` weiter von 2004 auf **237 Zeilen** geschrumpft (jetzt nur noch Bootstrap, Indexes, Seeding, Router-Mount). Sechs neue Router-Module:
  - `routes/dealer.py` (103 Z) — `/api/dealer/settings` (GET/PUT), `/api/dealer/active-profile`
  - `routes/contracts.py` (392 Z) — preview, create, list, get, pdf, send, delete + Contract-Helper `_apply_contract_overrides`
  - `routes/appointments.py` (247 Z) — CRUD + `/api/appointments/{id}/pickup-order.pdf` (Abholauftrag)
  - `routes/drivers.py` (517 Z) — Dealer→Fahrer-Linking + komplette Fahrer-App (`/api/driver/*`); enthält eigenen `current_driver`-Auth-Helper inkl. `?auth=`-Query-Token-Support
  - `routes/listings.py` (334 Z) — `/api/mobile/compare`, `/api/mobile/live-counter`, `/api/snapshots/*`, `/api/vehicles/*`, `/api/listings/{extract,resolve}`
  - `routes/payments.py` (167 Z) — Stripe Checkout + Status-Polling + Webhook
  Stripe-Webhook bleibt direkt an `app` registriert (außerhalb des `/api`-Prefix). Alle 41 pytest-Tests grün; End-to-End-Smoke (11 Endpoints) alle 200; Frontend-Login + Admin-Dashboard rendern unverändert. Server.py enthält jetzt nur noch: Imports, FastAPI/APIRouter-Setup, `ensure_indexes()`, `seed_admin()`, `seed_super_admin()`, `on_start`/`on_stop`-Hooks, Router-Mount, CORS — ein klarer Bootstrap-Layer.
- 2026-02-08: **Abo-Verwaltung im Händler-Profil** (Einstellungen → neuer Tab "Abo"). Händler sieht jetzt seinen aktuellen Plan, Status (Aktiv / Gekündigt / Abgelaufen / Lifetime), Ablaufdatum, verbleibende Tage. Aktionen:
  - **Verlängern / Reaktivieren**: Monats- oder Jahresabo (führt zum Stripe-Checkout via `/api/payments/checkout`)
  - **Kündigen**: Soft-Cancel → Abo bleibt aktiv bis `expires_at`, danach automatisch deaktiviert. Bestätigungs-Dialog vor Aktion. Lifetime-Accounts sind nicht kündbar.
  Backend: zwei neue Endpoints `GET /api/dealer/subscription` (info), `POST /api/dealer/subscription/cancel`. `get_subscription_status()` in `deps.py` behandelt jetzt `cancelled`-Status korrekt (bleibt aktiv bis Ablaufdatum). 41/41 pytest grün, End-to-End mit echtem Test-Dealer verifiziert (register → grant monthly → view → cancel → reactivate-ready).
- 2026-02-08: **Abholprotokoll-Redesign (Behörden-/Übergabe-Look)** in `pickup_pdf_service.py`. Komplettes Layout-Refresh entsprechend Kunden-Vorlage:
  - **Blauer Header-Bar** (volle Seitenbreite) auf jeder Seite — Titel "ABHOLPROTOKOLL · ÜBERGABE" links, Datum rechts, weiße Schrift
  - **Auftragsnummer + Abholung-am** als prominenter Kopfblock (Auftragsnummer fett in Corporate-Blau #1E5BB8)
  - **Zwei Info-Karten "AUFTRAGGEBER" / "ABHOLORT / VERKÄUFER"** mit blauen Header-Bars, weißem Body + dezentem grauem Border, E-Mail-Adressen in Blau
  - **Sektion „FAHRZEUGDATEN — VOR ORT PRÜFEN"** als blaue H2; luftige Reihen (9 pt Padding) ohne Zebra-Streifen, dezente graue Trennlinien — Spalten: Label (blau, Helvetica) | Wert (fett schwarz) | ○ stimmt | ○ weicht ab
  - Drei-Wert-Reihen (Gewerbliche Nutzung, Unfallfrei): Ja / Nein / unbekannt
  - Alle nachfolgenden Sektions-Headlines konsistent in Großbuchstaben + Corporate-Blau (DOKUMENTE & ZUBEHÖR, AUSSTATTUNG, TECHNISCHER ZUSTAND, VORBESTEHENDE SCHÄDEN, VOR-ORT-AUFNAHME, BEMERKUNGEN, ÜBERGABE-BESTÄTIGUNG)
  41/41 Tests grün, PDF-Build End-to-End verifiziert (6 Seiten, 2 MB).
- 2026-02-08: **AutoScout24-Filter-Generation** zusätzlich zur mobile.de-Suche. Neues Modul `autoscout_service.py` mit 288 Marken + Modellen aus `autoscout_makes.json`. Funktionen:
  - `build_search_url(vehicle, rules)` — erzeugt eine Autoscout24-Suchurl im offiziellen Format (`/lst/{slug}?atype=C&cat=ma{makeId}mo{modelId}&fregfrom&kmto&powerfrom&powerto&damaged_listing=exclude&sort=price&ustate=N,U`)
  - Reuses dieselbe `comparison_rules`-Struktur wie mobile.de → Händler pflegt EIN Regelwerk für beide Portale
  - Fuzzy-Make-Match (case-/diakritik-insensitiv) inkl. Alias-Map (VW → Volkswagen, Mercedes → Mercedes-Benz, …)
  - Modell-Match: erst exakt, dann startswith, dann eindeutiger Substring
  - Wert-Mappings: Kraftstoff (B/D/E/H/L/C/2/3) und Getriebe (A/M/S)
  - `/api/mobile/compare`-Endpoint liefert jetzt `search_url` (mobile.de) **und** `autoscout_url` parallel
  - Frontend (`Vergleich.jsx`): zwei separate „mobile.de"- und „AutoScout24"-Buttons in der Top-Bar sowie zwei Filter-Karten in den Ergebnis-Spalten. Klick öffnet jeweiligen Portal-Popup mit fertigem Filter.
  Verifiziert: BMW 325 → `cat=ma13mo1644` mit Erstzulassung ab 2008, max 179.879 km, 157-164 kW, `fuel=B`, `gear=M` ✓

## Backlog / Roadmap
- 🟠 P1: **Bilder beim Kaufvertrag** (manueller Upload + Speicherung via Object Storage, Anzeige in Vertragsliste & Kalender) — Storage ist jetzt eingerichtet, kann wiederverwendet werden
- P1: "Anbieter"-Filter (Händler/Privat) auf mobile.de — `sellerType=DEALER` greift evtl. nicht mehr; Recherche erforderlich
- P1: Echte mobile.de API-Credentials (`MOBILE_API_USER`/`PASS`) → Live-URLs statt Sandbox-Mock
- P2: Beweis-Archiv in Vertragsliste & Kalender-Termin sichtbar machen (aktuell nur direkt nach Vergleich)
- P2: Snapshot-Cleanup-Job (alte Snapshots > 90 Tage soft-deleten)
- P2: Auto-Snapshot auch für autoscout24, sobald API/Scraping verfügbar
- P2: System-Auto-Mode für Theme (`prefers-color-scheme`)
- P2: Wochenansicht im Terminplaner
- P2: Drag & Drop für Termine
- P2: Refactoring `mobile_service.py` → `services/mobile/{parser,url_builder,client}.py`

## Tech Stack
- Frontend: React + Tailwind (Apple UI in `index.css`)
- Backend: FastAPI + Motor (MongoDB)
- PDF: ReportLab
- Mocked: mobile.de via `sandbox_data.xml` (keine API-Credentials)

## Key Files
- `/app/backend/server.py`, `/app/backend/mobile_service.py`, `/app/backend/pdf_service.py`
- `/app/frontend/src/pages/app/{Vergleich,Termine,Einstellungen}.jsx`
- `/app/frontend/src/index.css`

## Test Credentials
Siehe `/app/memory/test_credentials.md`

## Changelog – 2026-04-30
- **Listing Identity & Cache** (`/app/backend/listing_identity.py`)
  - `detect_source`, `extract_kleinanzeigen_id`, `extract_mobile_id` (id=-Param + .html), `extract_autoscout_id` (UUID)
  - `get_listing_identity(url) -> {source, item_id, cache_key}` mit klaren Fehlern
  - `get_or_fetch_listing(db, url, fetcher, ttl_hours=24)` – TTL-Cache via `listings_cache` Collection
    (unique index auf `cache_key` + (`source`,`item_id`); zählt `use_count`, `last_used_at`)
  - SQLAlchemy-Referenzmodell als Doku im Modul (Projekt nutzt produktiv MongoDB)
  - Endpoints: `POST /api/listings/extract` (nur Identität), `POST /api/listings/resolve` (Cache + Fetch)
- **Playwright-Fix**: Symlink `/pw-browsers/chromium_headless_shell-1217 -> chromium_headless_shell-1208`
  → behebt „Executable doesn't exist at /pw-browsers/chromium_headless_shell-1217/...“ im Beweis-Archiv.

## Changelog – 2026-05-01 — Fahrer-App (eigenständiges System)
- **Modell-Wechsel**: alte `drivers` Collection (Händler-generierte Fahrer mit 9-stelligem Code)
  wurde ersetzt durch:
  - `driver_accounts` – echte Fahrer-Accounts mit E-Mail/Passwort/display_name/driver_code (`FD-XXXXXXXX`)
  - `dealer_drivers` – Link-Collection dealer↔driver_account (Fahrer kann bei mehreren Händlern aktiv sein)
- **Fahrer-Endpoints (neu)**:
  - `POST /api/driver/register` – direkt eingeloggt (kein Bestätigungs-Schritt)
  - `POST /api/driver/login` (E-Mail + Passwort statt Code)
  - `GET /api/driver/me` (inkl. dealers[])
  - `PUT /api/driver/me` (nur display_name, propagiert zu allen Dealer-Links)
  - `GET /api/driver/appointments` – alle Fahrten über alle Händler
  - `GET /api/driver/appointments/{id}/pickup-order.pdf`
  - `GET /api/driver/contracts/{id}/pdf`
  - `GET /api/driver/snapshots/{id}/{pdf|png}`
- **Dealer-Endpoints (neu)**:
  - `POST /api/drivers/add` – Fahrer per `driver_code` hinzufügen
  - `GET /api/drivers` – liefert `{id, name, driver_code, email, active}` aus dealer_drivers + driver_accounts
  - `DELETE /api/drivers/{driver_account_id}` – Link entfernen + driver_id aus offenen Terminen lösen
  - `GET /api/drivers/{driver_id}/conflicts?date=YYYY-MM-DD` – Warnung bei Mehrfachzuweisung
- **Frontend**: `/fahrer/login`, `/fahrer/register`, `/fahrer` (Kalender-Dashboard gruppiert nach Tag),
  `/fahrer/einstellungen` (Code anzeigen/kopieren, Name ändern, Händlerliste).
  Dealer-Seite `/app/fahrer` komplett umgebaut auf "Fahrer per ID hinzufügen".
  Termin-Dialog zeigt gelbe Warnung wenn Fahrer am selben Tag schon eine Fahrt hat (Soft-Warning, kein Block).
- **PWA**: `manifest.json`, Service Worker (`/service-worker.js`), Meta-Tags in `index.html`,
  `<InstallPWAButton />` auf `/fahrer/login` und in Header des Fahrer-Dashboards.
- **Tests**: `/app/backend/tests/test_driver_system.py` (14 Tests, alle grün).
