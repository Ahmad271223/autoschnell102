# AutoSchnell im Apple App Store — Anleitung (Stand 06.10.2026)

Die iPhone-App ist eine Hülle (Capacitor) um `https://app.auto-schnellkauf.de/start`:
Vollbild ohne Browserleiste, eigenes Symbol, dunkler Startbildschirm. **Die Inhalte kommen
immer live vom Server** — eine neue Store-Version braucht es nur, wenn sich Name, Symbol,
Startseite oder die Berechtigungstexte ändern. Fahrer, Sucher und Chef melden sich wie gewohnt an.

Weil Apple zum Bauen einen Mac mit Xcode verlangt und wir keinen haben, baut **GitHub** die App
auf einem gemieteten Mac (`.github/workflows/ios.yml`) und lädt sie direkt zu Apple hoch.
Du brauchst dafür keinen Mac — nur ein Apple-Entwicklerkonto und vier Werte in GitHub.

## Was in diesem Ordner liegt

| Datei / Ordner | Zweck |
|---|---|
| `capacitor.config.json` | Paketname `de.autoschnellkauf.app`, Startseite, Farben, erlaubte Adressen |
| `ios/App/` | Das Xcode-Projekt (Symbol, Startbild, Berechtigungstexte in `App/Info.plist`) |
| `www/index.html` | Seite „Keine Verbindung" (wird nur ohne Internet gezeigt) |
| `ExportOptions.plist` | Export-Einstellungen für App Store Connect (Team-ID setzt der Workflow ein) |
| `package.json` | Capacitor 7 (`npm ci`, `npx cap sync ios`) |

Paketname (Bundle-ID): `de.autoschnellkauf.app` · Version 1.0.0 · Build-Nummer = Laufnummer des Workflows · nur iPhone (kein iPad-Eintrag nötig) · iOS 14 oder neuer.

## Einmalig: Apple-Konto und Schlüssel (ca. 30 Minuten + Wartezeit)

1. **Apple Developer Program** beitreten: https://developer.apple.com/programs/enroll/
   — 99 $ pro Jahr. Als Einzelperson geht es mit Apple-ID + Ausweis (Freischaltung meist 1–2 Tage);
   als Firma braucht Apple eine D-U-N-S-Nummer (kostenlos, dauert 1–2 Wochen). Der Name des Kontos
   erscheint im Store als „Anbieter".
2. **Team-ID** notieren: https://developer.apple.com/account → *Membership details* → „Team ID" (10 Zeichen).
3. **Bundle-ID registrieren**: https://developer.apple.com/account/resources/identifiers →
   „+" → *App IDs* → *App* → Description „AutoSchnell", Bundle ID **Explicit** `de.autoschnellkauf.app`,
   keine Capabilities anhaken → *Register*.
4. **App in App Store Connect anlegen**: https://appstoreconnect.apple.com → *Meine Apps* → „+" →
   *Neue App*: Plattform iOS, Name „AutoSchnell", Primärsprache Deutsch, Bundle-ID
   `de.autoschnellkauf.app` auswählen, SKU `autoschnell-ios`, Zugriff „Vollzugriff".
5. **API-Schlüssel erstellen**: App Store Connect → *Benutzer und Zugriff* → Reiter *Integrationen* →
   *App Store Connect API* → *Teamschlüssel* → „+": Name „GitHub Build", Zugriff **Admin**
   (nur „App-Manager" reicht nicht, weil Xcode damit das Signaturzertifikat in der Cloud anlegt).
   - **Issuer ID** (oben auf der Seite) und **Key ID** (in der Zeile des Schlüssels) kopieren.
   - **„API-Schlüssel herunterladen"** → Datei `AuthKey_XXXXXXXXXX.p8`. **Nur einmal möglich** — sicher
     ablegen (z. B. neben `upload-key.jks` im Ordner `autoschnell-playstore`).
6. **Vier Secrets in GitHub eintragen**: https://github.com/Ahmad271223/autoschnell102/settings/secrets/actions →
   *New repository secret*, je ein Eintrag:

   | Name | Wert |
   |---|---|
   | `APPLE_TEAM_ID` | Team-ID aus Schritt 2 |
   | `APPSTORE_KEY_ID` | Key ID aus Schritt 5 |
   | `APPSTORE_ISSUER_ID` | Issuer ID aus Schritt 5 |
   | `APPSTORE_PRIVATE_KEY` | der **komplette Text** der .p8-Datei (mit `-----BEGIN PRIVATE KEY-----` und `-----END PRIVATE KEY-----`; mit Editor öffnen, alles kopieren) |

   Die Werte stehen danach nirgends im Code und werden nach jedem Bau vom Mac gelöscht.

## Bauen und hochladen (jedes Mal, ca. 15–25 Minuten)

1. https://github.com/Ahmad271223/autoschnell102/actions → links **„iOS-App bauen"** → rechts
   **„Run workflow"** → Branch wählen (der Stand mit dem Ordner `ios-app`), Versionsnummer leer lassen
   (= 1.0.0) oder z. B. `1.0.1` eintragen, Haken „hochladen" lassen → **Run workflow**.
2. Grüner Haken = die App liegt bei Apple. In App Store Connect → *AutoSchnell* → Reiter **TestFlight**
   erscheint der Build nach 10–30 Minuten „Verarbeitung" (Apple schickt eine Mail).
3. Beim ersten Build fragt TestFlight nach den **Exportbestimmungen** — ist schon beantwortet
   (`ITSAppUsesNonExemptEncryption = false` in der Info.plist: nur HTTPS, keine eigene Verschlüsselung).

**Kosten:** GitHub rechnet Mac-Minuten 10-fach. Das private Repo hat 2.000 Freiminuten im Monat,
das sind rund 200 Mac-Minuten — also etwa 8 Builds im Monat kostenlos, danach ca. 0,08 $ je Minute.
Normalerweise braucht es im Jahr nur wenige Builds.

**Falls der Bau mit „No signing certificate" oder „Cloud signing permission" abbricht:** Apple
lässt die Cloud-Signatur für dieses Konto nicht zu. Dann einmalig ein Zertifikat per Hand (ohne Mac,
mit Git Bash auf deinem PC — sag mir Bescheid, ich mache das mit dir):

```bash
openssl req -new -newkey rsa:2048 -nodes -keyout ios-dist.key -out ios-dist.csr -subj "/CN=AutoSchnell Distribution/O=AutoSchnell/C=DE"
```
`ios-dist.csr` unter https://developer.apple.com/account/resources/certificates → „+" → **Apple Distribution**
hochladen, `distribution.cer` herunterladen, dann:
```bash
openssl x509 -in distribution.cer -inform DER -out ios-dist.pem
openssl pkcs12 -export -inkey ios-dist.key -in ios-dist.pem -out ios-dist.p12
base64 -w0 ios-dist.p12 > ios-dist.p12.b64
```
Inhalt von `ios-dist.p12.b64` als Secret `IOS_P12_BASE64`, das beim Export gewählte Passwort als
`IOS_P12_PASSWORD` — der Workflow nutzt das Zertifikat dann automatisch. `ios-dist.key` nie weitergeben.

## TestFlight (Fahrer vorab testen lassen)

App Store Connect → *TestFlight* → *Interne Tests* → Gruppe anlegen → Tester per Apple-ID-Mail einladen
(bis 100, sofort, ohne Apple-Prüfung). Die Fahrer installieren die App „TestFlight" aus dem App Store
und bekommen AutoSchnell darüber. Für *Externe Tests* (bis 10.000) prüft Apple den Build einmal kurz.

## Store-Eintrag (App Store Connect → AutoSchnell → iOS-App → 1.0.0)

- **Screenshots**: mindestens 1, besser 3–5 Bilder im Format **6,9 Zoll (1320 × 2868 Pixel)** —
  das sind Screenshots von einem iPhone 16 Pro Max / 15 Pro Max; kleinere iPhones übernehmen sie
  automatisch. Motive: Anmeldung, Fahrten/Termine, Abholprotokoll, Vertrag. (Ohne großes iPhone:
  Screenshots eines beliebigen iPhones machen und auf 1320 × 2868 skalieren — ich kann das umrechnen.)
- **Werbetext / Beschreibung** (Vorschlag): „AutoSchnell ist die App für Autohändler: Fahrzeuge
  vergleichen, Kaufverträge erstellen, Abholtermine planen und Abholprotokolle mit Fotos und
  Unterschrift direkt vor Ort aufnehmen. Für Chefs, Sucher und Fahrer — Anmeldung mit der Kontonummer
  Ihres Betriebs." · **Schlüsselwörter**: `Autohandel,Ankauf,Kaufvertrag,Abholung,Fahrzeug,Händler`
- **Support-URL**: `https://app.auto-schnellkauf.de/impressum` · **Datenschutz-URL**: `https://app.auto-schnellkauf.de/datenschutz`
- **Kategorie**: Business · **Preis**: kostenlos · **Verfügbarkeit**: Deutschland (oder alle Länder)
- **Alterseinstufung**: Fragebogen, überall „Nein" → 4+ (Apple fragt zusätzlich nach uneingeschränktem Webzugriff: **Nein**, die App zeigt nur unsere Seite)
- **App-Datenschutz** („Datenschutz-Label"): Daten werden erhoben → Name, Telefonnummer, Fotos/Videos,
  Nutzer-ID (Kontonummer), grobe Standortangabe (Abholadresse) — jeweils „Mit dem Nutzer verknüpft",
  Zweck „App-Funktionalität", **nicht** für Tracking. Das deckt sich mit der Datenschutzerklärung.
- **Anmeldedaten für die Prüfung** (*App-Review-Informationen* → „Anmeldung erforderlich"): ein
  Fahrer-Testkonto UND ein Firmen-Testkonto (Kontonummer + Passwort) mit sichtbaren Testdaten
  (Termin, Fahrzeug) — sonst lehnt Apple ab. Kontakt-Telefon und E-Mail angeben.
- **Hinweise für die Prüfung** (Vorschlag, auf Englisch):
  „AutoSchnell is a B2B tool for licensed car dealerships in Germany. Accounts are created by the
  dealership owner only; there is no public sign-up. Please use the demo accounts above: the driver
  account shows the pickup workflow (appointments, pickup protocol with photos taken via camera and
  on-screen signature), the company account shows vehicle comparison and contract creation.
  Content is served from our own backend; the app uses the camera for pickup-protocol photos."
- **Build auswählen** (Abschnitt *Build* → „+" → den hochgeladenen Build) → **Zur Prüfung einreichen**.
  Dauer meist 1–3 Tage.

**Risiko, das du kennen solltest:** Apple lehnt Apps gelegentlich nach Richtlinie 4.2 („Minimum
Functionality") ab, wenn sie „nur eine Webseite" sind. Dagegen helfen die Prüfnotiz oben, echte
Testkonten mit Daten und die Kamera-/Unterschriften-Funktionen. Kommt trotzdem eine Ablehnung,
antworten wir im *Resolution Center* (Erklärung: Fachanwendung für geschlossene Nutzergruppe) oder
rüsten native Funktionen nach (z. B. Push-Mitteilungen für neue Fahrten, Kamera-Plugin) — dann melde dich.

## Neue Version (später)

Nur nötig bei neuem Namen, Symbol, anderer Startseite oder geänderten Berechtigungstexten:
Änderung im Ordner `ios-app` committen und pushen, dann Workflow mit höherer Versionsnummer starten
(z. B. `1.0.1`), in App Store Connect neue Version anlegen, Build zuordnen, einreichen.
Die Build-Nummer vergibt der Workflow selbst (Laufnummer), sie muss nie von Hand erhöht werden.

## Android

Unverändert: fertiges Paket `AutoSchnell-1.0.0.aab` und Anleitung in `C:\Users\ahmad\autoschnell-playstore\`
(Play Console, 25 $ einmalig). Google verlangt bei neuen **privaten** Entwicklerkonten vor der
Veröffentlichung einen geschlossenen Test mit mindestens 12 Testern über 14 Tage; ein Firmenkonto
(mit D-U-N-S) hat diese Pflicht nicht.
