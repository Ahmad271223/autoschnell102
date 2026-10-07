# AutoSchnell Helfer (Browser-Erweiterung für Chrome und Edge)

Seit Version 2.0.0 (04.10.2026) macht die Erweiterung zwei Dinge:

1. **Abruf-Helfer (wie bisher):** lädt für die AutoSchnell-App Kleinanzeigen-Fahrzeugseiten über die
   Internetverbindung des Nutzers (`AUTOSCHNELL_FETCH`, `content.js`), damit der Server nicht gesperrt
   wird. Aktiv nur mit `CLIENT_FETCH_KLEINANZEIGEN=true` (siehe `docs/kleinanzeigen-abruf.md`).
2. **Browser-Helfer (neu, Wunsch Ahmad 04.10.2026):** auf mobile.de, AutoScout24 und Kleinanzeigen.
   Freigabe auf dem Server per `BROWSER_HELFER_KUNDEN` (Standard `10001,10002`).

## So läuft der Browser-Helfer
1. Einmalig verbinden: Symbol anklicken, 6-stelligen Code aus AutoSchnell (Programme → Browser-Helfer)
   eintippen. Ein Konto = ein Browser (wie beim AutoPointer-Programm ein PC); Abo-Pflicht.
2. Inserat öffnen → `portal.js` packt die Seite (gzip) → `POST /api/werkzeuge/browser-helfer/inserat`.
   Der Server liest sie aus (`backend/browser_helfer.py`), baut die Vergleichslinks mit den Firmenregeln,
   merkt Inserat + Verkäuferdaten 24 h (`werkzeug_inserate`, seit 04.10. abends für **alle** Konten — wer
   den Link direkt in AutoSchnell einfügt, braucht dann keinen Apify-Abruf) und schreibt das
   Protokoll (`werkzeug_vergleiche`, Chef-Übersicht, „Deine letzten Autos“).
3. Die Vergleiche gehen als Hintergrund-Tabs auf (abschaltbar im Symbol-Fenster). Inserate, die aus einer
   dieser Vergleichsseiten geöffnet werden, öffnen **keine** neuen Vergleiche (nur Knopf).
4. Jede selbst geöffnete Vergleichsseite geht genau einmal an `POST …/marktlage` → Platz des Inserats
   unter den Vergleichsangeboten + Ampel (grün ≤ 25 %, gelb ≤ 50 %, rot darüber). Keine KI.
5. Box im Inserat: Fahrzeug, Ampel je Portal, Preisbewertung von mobile.de/AutoScout24, Hinweise
   (Unfall, HU, Vorbesitzer, Schadenswörter, VB), seit 2.1.0 auch **aussortierte Angebote** (Unfall,
   defekt, Export/Händlerpreis, Neuwagen, Lockangebote) und das günstigste saubere Angebot **umgerechnet**
   auf km und Erstzulassung des eigenen Autos. Seit 2.3.0 gelten Beschädigte und Navi **wie in den
   AutoSchnell-Einstellungen** (Wunsch Ahmad 04.10. abends: „immer an AutoSchnell-Regeln halten“; 2.1.0/2.2.0
   haben Beschädigte immer ausgeschlossen). Für die Ampel sortiert `/marktlage` Unfallwagen usw. weiter aus.
   Unplausible Erstzulassung/Kilometer stehen als Hinweis in der Box (`melden`, wie im Windows-Programm).
   Seit 2.2.0: Start bei `document_end` statt nach allen Bildern; `background.js` holt die Vergleichsseiten
   zusätzlich selbst (`direktAuswerten`, mit den Cookies des Nutzers) — wer zuerst fertig ist, liefert die
   Ampel, die zweite Lieferung wird verworfen (`marktlageMerken`).
   Seit 2.4.0: `gemeinsam.js` läuft bei `document_start` (portal.js weiter bei `document_end`) und meldet ein
   Inserat sofort (`frueh`): der Hintergrund wacht auf und wärmt die Verbindung zu AutoSchnell vor. portal.js
   fragt erst ohne Seite — kennt der Helfer das Inserat (30 min), steht die Box ohne Einpacken und Hochladen.
   Seit 2.5.0: Box erkennt eine aktualisierte/neu geladene Erweiterung (`AutoSchnell.helferDa`) und bietet
   „Seite neu laden“; kennt der Helfer das Inserat nicht mehr, lesen „Kaufvertrag“/„Vergleich öffnen“ es nach
   (`nachlesen`, `ohneOeffnen: true`) und wiederholen sich einmal.
   Seit 2.6.0 (Live-Prüfung): von selbst öffnen nur im sichtbaren Tab (`ansicht`, `vergleiche_auto` beim
   Hinwechseln), nicht nach Neuladen/Zurück, höchstens 8 je Minute, Vergleichs-Tabs je Inserat-Tab werden
   wiederverwendet; Ampel mit Zeitgrenze/Fehlertext (`marktlage_fehler`); Direktabruf nur AutoScout24;
   Box hängt sich wieder ein, erkennt eine Doppel-Installation (Seiten-Ereignis `autoschnell-helfer-da`) und
   vorgeladene Seiten; Kleinanzeigen nur Kategorie 216.
   Seit 2.6.1: „Kaufvertrag“ startet die App immer zuerst per Link-Typ (auch wenn der Helfer sie nie gesehen hat);
   ohne App-Fenster fragt die Box („Webseite öffnen“) statt selbst die Webseite zu öffnen.
   Seit 2.6.2 (Paket 1, Prüfung 05./06.10.): Vergleichs-Tab nur wiederverwenden, wenn dort noch eine Vergleichsseite
   steht (`chrome.tabs.get` + `erlaubterLink`, sonst neuer Tab); Doppelstart-Sperre (`zuletztGestartet`, Knopf
   gesperrt, solange er läuft); `/marktlage` antwortet 409 für eine andere Suche (Tab lud noch das vorige Auto) —
   dann und nach vorübergehenden Fehlern (kein Netz, 429, 5xx) wird „erledigt“ zurückgenommen, der Tab versucht es
   nach 6 s einmal neu.
   Seit 2.6.3 (Paket 2+3): Doppel-Installation nur als Hinweis; Nachrichten nur von Portalseiten (`PORTAL`); `app_pfad`
   geprüft; Box-Zustand über den Hintergrund (`storage.local.setAccessLevel`); Sperre (402/403/offline) kurz gemerkt;
   Abruf-Helfer mit Zeitgrenze und höchstens 3 gleichzeitig; Fenster sofort aus dem Speicher; `bauen.ps1` ohne
   localhost, `hochladen.ps1` nur committet.
   Seit 2.7.0 (Wunsch Ahmad 06.10.): Portalwahl im Fenster am Symbol — mobile.de, AutoScout24 oder beide (wie im
   Windows-Programm); vorher gingen immer beide auf.
   Seit 2.7.1 (07.10., E2E Programm + Erweiterung): auch die kurze Kleinanzeigen-Adresse `/s-anzeige/<Nr>` gilt als
   Inserat (`inseratKennung`) — so öffnet das Windows-Programm ab 1.5.7 das Inserat als Tab, damit die Erweiterung es
   liest (Kaufvertrag ohne Apify); vorher brauchte die Adresse die Kategorie `-216-`.
   **Zusammen mit dem Windows-Programm (seit 2.3.0, dasselbe AutoSchnell-Konto):**
   - Hat das Programm das Auto in den letzten 30 Minuten verglichen (`programm_verglichen` in der Antwort von
     `…/inserat`), öffnet der Helfer **keine** Vergleiche von selbst — nur die Ampel per Direktabruf.
   - Die Vergleichsseiten, die das Programm geöffnet hat, erkennt der Helfer (`POST …/programm-suche`: erst nur
     die Adresse, `browser_helfer.gleiche_suche` verträgt das Umschreiben der AutoScout24-Adresse) und zeigt dort
     eine Box mit dem Auto und der Ampel. Autos, die man von dort aus öffnet, öffnen keine neuen Vergleiche.
   - Knopf **Vergleich öffnen** immer in der Box und im Fenster am AutoSchnell-Symbol (auch bei zugemachter
     Box) — öffnet die Vergleiche trotzdem.
6. Knopf **Kaufvertrag** → `/app/vergleich?url=…&vertrag=1`: das Vertragsfenster geht gleich auf, alles
   aus der Seite eingetragen. `/listings/check` und `/mobile/compare` nehmen die Browserdaten (jedes Konto):
   kein Apify-Abruf, kein Tageslimit. Beweisdokument gibt es für Browserdaten nicht (nur nach Server-Abruf).
   **Immer die installierte App** (Wunsch Ahmad): offenes App-Fenster → nach vorne, Ziel über `content.js`
   (kein Neuladen); App installiert, aber zu → Start über `web+autoschnell:` (protocol_handlers im
   App-Manifest; "installiert" merkt sich der Helfer, wenn AutoSchnell einmal als App lief); sonst Webseite.

Alles Wissen über den Seitenaufbau der Portale liegt auf dem Server — ändert ein Portal seine Seite,
reicht ein Server-Update. Die Erweiterung schickt nur die Seite.

## Bauen und bereitstellen
```
powershell -File browser-extension\bauen.ps1
```
erzeugt `browser-extension\dist\AutoSchnell-Helfer.zip`. Hochladen wie das Programm
(DEPLOYMENT.md „Programme zum Herunterladen“) mit `--werkzeug browser-helfer`.

## Installation beim Nutzer (bis zum Chrome Web Store)
1. ZIP aus AutoSchnell herunterladen und entpacken.
2. `chrome://extensions` bzw. `edge://extensions` → „Entwicklermodus“ → „Entpackte Erweiterung laden“
   → entpackten Ordner wählen.
3. Symbol anheften, Code eintippen.

Zum Testen gegen ein lokales Backend: im Symbol-Fenster unter „Server (nur zum Testen)“
`http://127.0.0.1:8001` eintragen (der Browser fragt einmal nach dem Zugriffsrecht).
