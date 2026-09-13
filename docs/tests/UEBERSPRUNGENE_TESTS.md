# Uebersprungene Tests (Backend-Selbsttest-Suite)

Stand: 10.09.2026 (Runde 21, Pruefbefund C). Bezug: CI-Job `backend`, Schritt
**Selbsttest-Suite** in `.github/workflows/ci.yml`:

```
python -m pytest -v -rs tests/ --ignore=tests/backend_test.py
```

`-rs` schreibt jeden Skip mit Grund ins CI-Log. Diese Datei erklaert jeden
Grund: gewollt oder nicht, und was den Test aktivieren wuerde.

## Zahlen

| Kontext | uebersprungen |
|---|---|
| CI **vor** Runde 21 (ohne `RUNDE14_HTTP`) | ca. 71 |
| CI **nach** Runde 21 (mit `RUNDE14_HTTP=1`), erwartet | 12 |
| lokal mit `RUNDE14_HTTP=1`, ohne `BETREIBER_PROD_URL` | 13 |

Gegenpruefung 10.09.2026: CI-gleicher Lauf auf frischer DB mit
`RUNDE14_HTTP=1` ergab 1074 passed, 13 skipped. Das sind genau die 12 Skips
unten plus `test_betreiber` (lokal ohne `BETREIBER_PROD_URL`).

Die Differenz von 59 sind die HTTP-Teile der Runde-14-Dateien (siehe unten).
Die Rechnung vor Runde 21: 60 Skips in den Runde-14-Dateien + 13 uebrige
- 1 (`test_http_57`, erst mit Schalter erreichbar) - 1 (`BETREIBER_PROD_URL`,
in der CI gesetzt) = 71.

## Runde-14-HTTP-Tests: `RUNDE14_HTTP=1`

Dateien: `backend/tests/test_befunde_runde14_{admin,appointments,bestand,contracts,drivers,infra,marktplatz,resale,team_dealer}.py`.

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
  - `SELF_SIGNUP=true` und `MOCK_PROVIDER_FETCH=true`.
  - Den Super-Admin legen die Tests selbst in Mongo an. Die
    `ADMIN_*`/`SUPER_ADMIN_*`-Seeds brauchen sie nicht.
- `test_befunde_runde14_infra.py::test_r21_*` prueft, dass der Schalter in
  der CI gesetzt bleibt und kein Skip mehr den alten Grundtext traegt.

## Skips, die in der CI weiter auftreten (erwartet 12)

| Test | Anzahl | Grund (Grundtext) | gewollt? | aktivieren durch |
|---|---|---|---|---|
| `test_kontingent.py` (ganze Datei, `pytestmark`) | 7 | "Verkaufen ist derzeit kostenlos und unbegrenzt (VERKAUF_KOSTENLOS=true)". Default in `routes/team.py`. | **ja**: das Verkaufs-Kontingent ist fachlich abgeschaltet | `VERKAUF_KOSTENLOS=false` fuer Backend **und** pytest |
| `test_befunde_runde14_team_dealer.py::test_http_57_verkaufspaket_anfrage_idempotent` | 1 | "VERKAUF_KOSTENLOS aktiv — Pfad nicht erreichbar". `upgrade-request` antwortet mit 400 "kostenlos". | **ja**, wie oben | `VERKAUF_KOSTENLOS=false` am Backend |
| `test_driver_system.py::TestDriverFlow::test_driver_sees_assigned_appointments` | 1 | "Backend ohne MOCK_PROVIDER_FETCH ...". Der Text ist **irrefuehrend**: tatsaechlich antwortet `/mobile/compare` mit 402 "Kein aktives Abo". Die Freischaltung per `PUT /admin/users/{id}` mit `plan_type` legt kein Abo mehr an und wird nicht geprueft. | **nein**: der Test ist veraltet | Abo ueber den heutigen Weg aktivieren (z.B. `/admin/sucher/{uid}/abo` wie in `test_paket_sept.py`); bei Status != 200 hart scheitern statt ueberspringen |
| `test_runde5.py::test_05_seed_reaktiviert_gesperrten_admin_nicht` | 1 | "Bootstrap-Admin nicht vorhanden". Seit Runde 12 legt `seed_admin` keinen normalen Admin mehr an (nur noch ein Super-Admin). | **nein**: der Test ist obsolet | Test auf `seed_super_admin` umschreiben oder entfernen |
| `test_admin_dashboard.py::...::test_pdf_streaming` | 1 | "no contracts in DB to test PDF streaming". Die CI-DB ist frisch, und die Datei laeuft alphabetisch vor allen Tests, die Vertraege anlegen. | **nein** | Vertrag im Test selbst anlegen |
| `test_paket_sept.py::test_01_snapshots_nur_kleinanzeigen` | 1 | "mobile.de in dieser Umgebung nicht verfuegbar". `mobile_service.mobile_quelle_verfuegbar()` braucht `MOBILE_USER`+`MOBILE_PASS`, `APIFY_TOKEN` oder `MOBILE_SANDBOX_MODE`. `MOCK_PROVIDER_FETCH` zaehlt dafuer nicht. | **teilweise**: die CI hat bewusst keinen mobile.de-Zugang | vermutlich `MOBILE_SANDBOX_MODE=true` am Backend. **Ungetestet**: der Modus hat eigene Nebenwirkungen in `mobile_service.py` |

## Skips nur lokal

| Test | Grund | gewollt? | aktivieren durch |
|---|---|---|---|
| `test_betreiber.py::test_11_self_signup_aus` | "BETREIBER_PROD_URL nicht gesetzt (eigener Backend-Prozess mit SELF_SIGNUP=false)" | **ja** | zweites Backend mit `SELF_SIGNUP=false` und `BETREIBER_PROD_URL` darauf. Die CI macht das auf Port 8003. |

## Bedingte Skips, die in der CI nicht ausgeloest werden

Diese Skips schuetzen lokale Laeufe mit unvollstaendiger Umgebung. In der CI
ist die jeweilige Voraussetzung erfuellt; im Lauf mit Schalter sind sie nicht
aufgetreten.

| Grundtext | Dateien (Auswahl) | tritt auf, wenn ... |
|---|---|---|
| "Backend ohne MOCK_PROVIDER_FETCH" (mit Varianten) | `test_auto_daten`, `test_befunde_runde8/10/19`, `test_befunde_runde10_nachpruefung*`, `test_befunde_runde14_contracts`, `test_digitaler_vertrag`, `test_e2e_flow`, `test_foto_parallel`, `test_link_jobs`, `test_rollen_negativ`, `test_send_idempotenz`, `test_tenant_isolation`, `test_whatsapp_teilen` | das Backend ohne `MOCK_PROVIDER_FETCH=true` laeuft |
| "Backend nicht erreichbar" | `test_befunde_runde19`, `test_digitaler_vertrag`, `test_sitzung_meldung`, `test_ustid`, `test_whatsapp_teilen` | kein Backend auf `TEST_BASE_URL` laeuft |
| "Registrierung nicht moeglich (...)" / "SELF_SIGNUP aus?" / "SELF_SIGNUP=false — Chef darf keine Sucher anlegen" | `test_befunde_runde19`, `test_digitaler_vertrag`, `test_sitzung_meldung`, `test_whatsapp_teilen`, `test_befunde_runde14_team_dealer` | das Backend mit `SELF_SIGNUP=false` laeuft |
| "Mongo nicht erreichbar" / "kein Mongo" | `test_befunde_runde14_appointments`, `..._contracts`, `..._team_dealer` | keine MongoDB unter `MONGO_URL` erreichbar ist |
| "PIL fehlt" | `test_befunde_runde14_team_dealer` | Pillow nicht installiert ist |
| "kein sh vorhanden" (14 Tests) | `test_rollout.py` | keine POSIX-Shell auf dem Rechner ist |
| "keine Mail-Einstellungen in backend/.env hinterlegt" | `test_vertrag_mail.py` | eine vorhandene `backend/.env` weder `RESEND_API_KEY` noch `SMTP_HOST` hat. Fehlt die Datei ganz, legt der Test eine Wegwerf-`.env` an, deshalb kein Skip in der CI. |
| "Fahrer-Route nicht erreichbar (Backend-Stand?)" | `test_befunde_runde10_nachpruefung.py` | das Backend einen alten Stand hat |
| "keine pruefe_*-Funktion ..." / "pruefe-Funktion beendet den Prozess nicht ..." | `test_befunde_runde10_nachpruefung2.py` | `production_check` umgebaut ist |
| "Fahrzeug-IDs unterscheiden sich je Haendler — Fall tritt nicht auf" | `test_rollen_negativ.py` | der Datenfall nicht vorliegt (fachlich gewollt) |
| "contract creation unavailable: ..." | `test_driver_system.py` | nur erreichbar, wenn der Skip darueber (402) behoben ist |
| "ADMIN_EMAIL nicht gesetzt" | `test_runde5.py` (`test_05_...`) | `ADMIN_EMAIL` fehlt; die CI setzt es im Job-env, dort greift stattdessen "Bootstrap-Admin nicht vorhanden" (siehe oben) |

## Ausgeschlossen statt uebersprungen

`tests/backend_test.py` wird per `--ignore` nicht gesammelt. Das ist eine
Altsuite mit echten mobile.de-URLs und Stripe-Checkout
(`pytest.skip("Stripe checkout unavailable ...")`). Sie ist in der CI gewollt
aus: keine echten Anfragen an Anbieter.

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
  MOCK_PROVIDER_FETCH=true AUTO_DATEN_SCHAEDEN_FREITEXT=true SELF_SIGNUP=true \
  python -m pytest tests/ -q -rs --ignore=tests/backend_test.py
```

Danach Backend beenden und die Wegwerf-DB entfernen.

Unter Windows `127.0.0.1` statt `localhost` angeben (wie die CI): mit
`localhost` dauerte der Gesamtlauf rund 60 Minuten, mit `127.0.0.1` rund
7 Minuten (vermutlich versucht Windows bei jeder Verbindung zuerst IPv6).
