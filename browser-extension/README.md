# AutoSchnell Abruf-Helfer (Browser-Erweiterung)

Diese kleine Erweiterung lädt Kleinanzeigen-Fahrzeugseiten über die
**Internetverbindung des jeweiligen Nutzers** — statt über den AutoSchnell-
Server. Dadurch verteilen sich die Abrufe auf hunderte verschiedene
Anschlüsse, und Kleinanzeigen blockt den Server nicht, egal wie viele
Vergleiche laufen.

**Nur Kleinanzeigen.** mobile.de und AutoScout laufen weiter über den
Server (offizielle API, keine Sperr-Gefahr).

## Wie es funktioniert (in einfach)
1. Sucher klickt „Vergleichen".
2. Der Server schaut in den Speicher: Link schon da? → sofort fertig.
3. Wenn neu: Der Server bittet den Browser des Nutzers, die Seite zu holen.
4. Diese Erweiterung lädt die Seite über die Leitung des Nutzers.
5. Das Ergebnis geht an den Server, wird für immer gespeichert — alle
   weiteren Nutzer bekommen es dann sofort aus dem Speicher.

Die Erweiterung darf **ausschließlich** kleinanzeigen.de lesen (siehe
`host_permissions`) und reagiert nur auf die AutoSchnell-Seite.

## Installation (bis zur Veröffentlichung im Chrome Web Store)
1. Chrome öffnen → `chrome://extensions`
2. Oben rechts „Entwicklermodus" einschalten.
3. „Entpackte Erweiterung laden" → diesen Ordner (`browser-extension`) wählen.
4. Fertig — beim nächsten Vergleich nutzt AutoSchnell automatisch den Helfer.

Für den flächendeckenden Einsatz später: als `.crx` signieren und im
Chrome Web Store / Firefox Add-ons veröffentlichen, dann installiert jeder
Sucher sie mit einem Klick.

## Aktivierung serverseitig
Der Server nutzt den Helfer nur, wenn `CLIENT_FETCH_KLEINANZEIGEN=true`
gesetzt ist (siehe `.env.example`). Ist er aus, holt der Server wie bisher
selbst — die Plattform funktioniert also mit und ohne Erweiterung.
