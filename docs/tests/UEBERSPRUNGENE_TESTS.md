# Uebersprungene Tests (Backend-Selbsttest-Suite)

Stand: 22.09.2026 (Pruefbericht 20.09.2026, T-11; zuvor 10.09.2026, Runde 21,
Pruefbefund C). Bezug: CI-Job `backend`, Schritt
**Selbsttest-Suite** in `.github/workflows/ci.yml`:

```
python -m pytest -v -rs tests/
```

`-rs` schreibt jeden Skip mit Grund ins CI-Log. Diese Datei erklaert jeden
Grund: gewollt oder nicht, und was den Test aktivieren wuerde.

## Zahlen

| Kontext | uebersprungen |
|---|---|
| CI **vor** Runde 21 (ohne `RUNDE14_HTTP`) | ca. 71 |
| CI **nach** Runde 21 (mit `RUNDE14_HTTP=1`), erwartet | 12 |
| CI und lokal nach Kontonummer Schritt 5 (13.09.2026), erwartet | 11 |
| CI seit Runde 31 (12.09.2026), erwartet — dazu `test_18_das_muster_passt_auf_den_echten_bau` (kein Frontend-Bau im Backend-Job) | 12 |
| Testdateien `tests/test_*.py` am 22.09.2026 | 222 (10.09.2026: 171) |
| Skip-Stellen im Quelltext am 22.09.2026 (`pytest.skip`, `skipif`, `pytestmark`) | 155 — davon 69 `HTTP_GRUND` (Runde 14), 15 weitere `RUNDE14_HTTP`, 16 "kein sh vorhanden" |

Gegenpruefung 10.09.2026: CI-gleicher Lauf auf frischer DB mit
`RUNDE14_HTTP=1` ergab 1074 passed, 13 skipped. Das sind genau die 12 Skips
unten plus `test_betreiber` (lokal ohne `BETREIBER_PROD_URL`).

Nachfuehrung 22.09.2026 (T-11): statisch aus den Testdateien erhoben (Grep
nach `pytest.skip`, `skipif`, `pytestmark`), ohne neuen `-rs`-Lauf. Die seit
dem 10.09.2026 hinzugekommenen Skip-Gruende stehen in den Tabellen unten; an
den CI-Voraussetzungen hat sich nichts geaendert.

Die Differenz von 59 sind die HTTP-Teile der Runde-14-Dateien (siehe unten).
Die Rechnung vor Runde 21: 60 Skips in den Runde-14-Dateien + 13 uebrige
- 1 (`test_http_57`, erst mit Schalter erreichbar) - 1 (`BETREIBER_PROD_URL`,
in der CI gesetzt) = 71.

## Runde-14-HTTP-Tests: `RUNDE14_HTTP=1`

Dateien: `backend/tests/test_befunde_runde14_{admin,appointments,bestand,contracts,drivers,infra,marktplatz,resale,team_dealer}.py`.
Denselben Schalter nutzen inzwischen auch `test_golive_20260914_*.py` (9 Dateien,
`skipif`), `test_golive_20260915_marktplatz_schalter.py`,
`test_kontenanlage_admin.py` und `test_kontonummer_http.py` (`pytestmark`, ganze
Datei) — gleicher Grundtext, gleiche Voraussetzungen.

Jede Datei setzt `HTTP = os.environ.get("RUNDE14_HTTP") == "1"`. Die HTTP-Tests
bzw. ihre HTTP-Fixtures rufen ohne Schalter `pytest.skip(HTTP_GRUND)` auf. Bis
Runde 20 lautete der Grund nur "HTTP nach Neustart". Jetzt heisst er:
"RUNDE14_HTTP=1 nicht gesetzt — HTTP-Test braucht ein laufendes Backend auf
TEST_BASE_URL (CI: Schritt Selbsttest-Suite)".

- **Gewollt:** ja, aber nur ohne laufendes Backend (z.B. schneller lokaler Lauf).
- **Aktiviert durch:** `RUNDE14_HTTP=1` und ein Backend auf `TEST_BASE_URL`.
  Die CI setzt den Schalter seit Runde 21 im Schritt *Selbsttest-Suite*,
  gegen das Backend auf Port 8001.
- **Weitere Voraussetzungen**, die die CI schon erfuellt:
  - `MONGO_URL`/`DB_NAME` zeigen auf dieselbe DB wie das Backend.
  - `MOCK_PROVIDER_FETCH=true`. Konten legen die Tests ueber den Super-Admin
    an (`backend/tests/konten.py`), eine Selbstregistrierung gibt es nicht mehr.
  - Den Super-Admin legen die Tests selbst in Mongo an. Die
    `ADMIN_*`/`SUPER_ADMIN_*`-Seeds brauchen sie nicht.
- `test_befunde_runde14_infra.py::test_r21_*` prueft, dass der Schalter in
  der CI gesetzt bleibt und kein Skip mehr den alten Grundtext traegt.

## Skips, die in der CI weiter auftreten (erwartet 12)

| Test | Anzahl | Grund (Grundtext) | gewollt? | aktivieren durch |
|---|---|---|---|---|
| `test_befunde_runde31_fassung.py::test_18_das_muster_passt_auf_den_echten_bau` | 1 | "kein Frontend-Bau vorhanden". Liest `frontend/build/static/js/main.*.js`; der Backend-Job der CI baut die Oberflaeche nicht (das tut der Job `frontend`). | **ja**: Gegenprobe am echten Bau, lokal nach `yarn build` | Oberflaeche vor der Suite bauen |
| `test_kontingent.py` (ganze Datei, `pytestmark`) | 7 | "Verkaufen ist derzeit kostenlos und unbegrenzt (VERKAUF_KOSTENLOS=true)". Default in `routes/team.py`. | **ja**: das Verkaufs-Kontingent ist fachlich abgeschaltet | `VERKAUF_KOSTENLOS=false` fuer Backend **und** pytest |
| `test_befunde_runde14_team_dealer.py::test_http_57_verkaufspaket_anfrage_idempotent` | 1 | "VERKAUF_KOSTENLOS aktiv — Pfad nicht erreichbar". `upgrade-request` antwortet mit 400 "kostenlos". | **ja**, wie oben | `VERKAUF_KOSTENLOS=false` am Backend |
| `test_driver_system.py::TestDriverFlow::test_driver_sees_assigned_appointments` | 1 | "Backend ohne MOCK_PROVIDER_FETCH ...". Der Text ist **irrefuehrend**: tatsaechlich antwortet `/mobile/compare` mit 402 "Kein aktives Abo". Die Freischaltung per `PUT /admin/users/{id}` mit `plan_type` legt kein Abo mehr an und wird nicht geprueft. | **nein**: der Test ist veraltet | Abo ueber den heutigen Weg aktivieren (z.B. `/admin/sucher/{uid}/abo` wie in `test_paket_sept.py`); bei Status != 200 hart scheitern statt ueberspringen |
| `test_admin_dashboard.py::...::test_pdf_streaming` | 1 | "no contracts in DB to test PDF streaming". Die CI-DB ist frisch, und die Datei laeuft alphabetisch vor allen Tests, die Vertraege anlegen. | **nein** | Vertrag im Test selbst anlegen |
| `test_paket_sept.py::test_01_snapshots_nur_kleinanzeigen` | 1 | "mobile.de in dieser Umgebung nicht verfuegbar". `mobile_service.mobile_quelle_verfuegbar()` braucht `MOBILE_USER`+`MOBILE_PASS`, `APIFY_TOKEN` oder `MOBILE_SANDBOX_MODE`. `MOCK_PROVIDER_FETCH` zaehlt dafuer nicht. | **teilweise**: die CI hat bewusst keinen mobile.de-Zugang | vermutlich `MOBILE_SANDBOX_MODE=true` am Backend. **Ungetestet**: der Modus hat eigene Nebenwirkungen in `mobile_service.py` |

## Skips nur lokal

Keine. `test_betreiber.py::test_11_alte_anlagewege_geschlossen` prueft seit
Kontonummer Schritt 5 (13.09.2026) die geschlossenen Anlagewege (410/403) am
normalen Backend — ein zweites Backend im Betreiber-Modus ist nicht mehr noetig.

## Bedingte Skips, die in der CI nicht ausgeloest werden

Diese Skips schuetzen lokale Laeufe mit unvollstaendiger Umgebung. In der CI
ist die jeweilige Voraussetzung erfuellt; im Lauf mit Schalter sind sie nicht
aufgetreten.

| Grundtext | Dateien (Auswahl) | tritt auf, wenn ... |
|---|---|---|
| "Backend ohne MOCK_PROVIDER_FETCH" (mit Varianten) | `test_auto_daten`, `test_befunde_runde8/10/19`, `test_befunde_runde10_nachpruefung*`, `test_befunde_runde14_contracts`, `test_digitaler_vertrag`, `test_e2e_flow`, `test_foto_parallel`, `test_link_jobs`, `test_rollen_negativ`, `test_send_idempotenz`, `test_tenant_isolation`, `test_whatsapp_teilen` | das Backend ohne `MOCK_PROVIDER_FETCH=true` laeuft |
| "Backend nicht erreichbar" | `test_befunde_runde19`, `test_digitaler_vertrag`, `test_sitzung_meldung`, `test_ustid`, `test_whatsapp_teilen` | kein Backend auf `TEST_BASE_URL` laeuft |
| "Registrierung nicht moeglich (...)" | `test_befunde_runde19`, `test_digitaler_vertrag`, `test_sitzung_meldung`, `test_whatsapp_teilen` | die Firmenanlage ueber `POST /admin/users` scheitert (z.B. Wegwerf-Super-Admin nicht anmeldbar) |
| "Mongo nicht erreichbar" / "kein Mongo" | `test_befunde_runde14_appointments`, `..._contracts`, `..._team_dealer` | keine MongoDB unter `MONGO_URL` erreichbar ist |
| "PIL fehlt" | `test_befunde_runde14_team_dealer` | Pillow nicht installiert ist |
| "kein sh vorhanden" (16 Tests: 14 in `test_rollout`, je 1 in `test_betrieb_testmail_20260921` und `test_rp_betrieb_20260922`; dazu `test_deploy_pb590_20260922` seit 22.09.2026) | `test_rollout.py` u. a. | keine POSIX-Shell auf dem Rechner ist |
| "Dateirechte nur unter Linux/macOS pruefbar" (2) | `test_betrieb_testmail_20260921` | der Lauf unter Windows ist (`chmod 600` der `.env` im Rollout) |
| "keine TrueType-Schrift auf diesem Rechner" (2) | `test_dokumente_20260920` | keine Unicode-TrueType-Schrift gefunden wird (CI-Runner: vorhanden) |
| "RATE_LIMIT_ENABLED=false" | `test_golive_20260915_runde15_mfa_seed` | die Umgebung des Testprozesses `RATE_LIMIT_ENABLED=false` setzt (CI: nicht gesetzt) |
| "Konto-Limiter abgeschaltet" | `test_rp_markt_kaeufer_welle2_20260922` | `LOGIN_KONTO_LIMIT` auf 0 steht |
| "Umgebung setzt den Wert — der Standard ist hier nicht sichtbar" | `test_pruefbericht_n1_n10_20260920` | `MAX_CONCURRENT_KLEINANZEIGEN` in der Umgebung steht |
| "Vorlage der Produktionsumgebung nicht importierbar" | `test_befunde_20260916_block_c` | `test_befunde_runde13_g2` sich nicht importieren laesst |
| "Mongo nicht erreichbar: ..." | `test_deploy_pb590_20260922` (Sperrabfrage `migrationen.py --sperre-gehalten`) | keine MongoDB unter `MONGO_URL` erreichbar ist |
| "keine Mail-Einstellungen in backend/.env hinterlegt" | `test_vertrag_mail.py` | eine vorhandene `backend/.env` weder `RESEND_API_KEY` noch `SMTP_HOST` hat. Fehlt die Datei ganz, legt der Test eine Wegwerf-`.env` an, deshalb kein Skip in der CI. |
| "Fahrer-Route nicht erreichbar (Backend-Stand?)" | `test_befunde_runde10_nachpruefung.py` | das Backend einen alten Stand hat |
| "keine pruefe_*-Funktion ..." / "pruefe-Funktion beendet den Prozess nicht ..." | `test_befunde_runde10_nachpruefung2.py` | `production_check` umgebaut ist |
| "Fahrzeug-IDs unterscheiden sich je Haendler — Fall tritt nicht auf" | `test_rollen_negativ.py` | der Datenfall nicht vorliegt (fachlich gewollt) |
| "contract creation unavailable: ..." | `test_driver_system.py` | nur erreichbar, wenn der Skip darueber (402) behoben ist |

## Ausgeschlossen statt uebersprungen

Nichts mehr. Die Altsuite `tests/backend_test.py` (echte mobile.de-URLs,
Stripe-Checkout, Anmeldung per `ADMIN_EMAIL`) ist mit Kontonummer Schritt 5
(13.09.2026) geloescht, der `--ignore`-Schalter entfaellt.

## Lokal wie die CI ausfuehren

Im Ordner `backend`:

1. Eigenes Backend auf einem freien Port starten, mit **eigener Wegwerf-DB**.
   Die Runde-14-Dateien schreiben ohne `DB_NAME` in die Entwicklungs-DB
   `autoschnell`.
   - `RESEND_API_KEY`, `APIFY_TOKEN`, `MAIL_FROM`, `STRIPE_API_KEY` und
     `CLIENT_FETCH_KLEINANZEIGEN` **leer setzen**. Sonst laedt `load_dotenv`
     sie aus `backend/.env` nach.
2. Tests mit demselben `DB_NAME` gegen dieses Backend laufen lassen:

```
RUNDE14_HTTP=1 TEST_BASE_URL=http://127.0.0.1:<PORT> DB_NAME=<wegwerf-db> \
  MOCK_PROVIDER_FETCH=true AUTO_DATEN_SCHAEDEN_FREITEXT=true \
  python -m pytest tests/ -q -rs
```

Danach Backend beenden und die Wegwerf-DB entfernen.

Unter Windows `127.0.0.1` statt `localhost` angeben (wie die CI): mit
`localhost` dauerte der Gesamtlauf rund 60 Minuten, mit `127.0.0.1` rund
7 Minuten (vermutlich versucht Windows bei jeder Verbindung zuerst IPv6).
