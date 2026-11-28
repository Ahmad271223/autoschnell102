# Schriften — Herkunft und Lizenzen

Alle Schriften dieser Anwendung werden seit 09/2026 lokal aus diesem Ordner
ausgeliefert (`/fonts/…`, eingebunden über `src/index.css`). Es werden keine
Schriften mehr von Google Fonts oder Fontshare nachgeladen — dadurch geht die
IP-Adresse der Besucher nicht mehr an Drittanbieter (Pruefbericht 09/2026).

Heruntergeladen am 2026-09-03 (woff2, Subsets `latin` + `latin-ext`; die
Google-Subsets Cyrillic/Vietnamese wurden bewusst nicht uebernommen).

## IBM Plex Sans / IBM Plex Mono

* Copyright (c) 2017 IBM Corp. with Reserved Font Name "Plex"
* Lizenz: SIL Open Font License, Version 1.1 (OFL-1.1) — Volltext in
  `OFL-IBM-Plex.txt` (aus https://github.com/IBM/plex/blob/master/LICENSE.txt)
* Quelle: Google Fonts (fonts.gstatic.com, IBM Plex Sans v20 / IBM Plex Mono v20),
  CSS abgerufen mit
  `https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap`
* Die OFL erlaubt das Selbst-Hosten und Buendeln mit der Anwendung; der
  Lizenztext muss mitgeliefert werden (siehe `OFL-IBM-Plex.txt`).

Dateien:

| Datei | Schnitt | Subset |
| --- | --- | --- |
| ibm-plex-sans-300-latin.woff2 / -latin-ext.woff2 | Light 300 | latin / latin-ext |
| ibm-plex-sans-400-latin.woff2 / -latin-ext.woff2 | Regular 400 | latin / latin-ext |
| ibm-plex-sans-500-latin.woff2 / -latin-ext.woff2 | Medium 500 | latin / latin-ext |
| ibm-plex-sans-600-latin.woff2 / -latin-ext.woff2 | SemiBold 600 | latin / latin-ext |
| ibm-plex-sans-700-latin.woff2 / -latin-ext.woff2 | Bold 700 | latin / latin-ext |
| ibm-plex-mono-400-latin.woff2 / -latin-ext.woff2 | Regular 400 | latin / latin-ext |
| ibm-plex-mono-500-latin.woff2 / -latin-ext.woff2 | Medium 500 | latin / latin-ext |
| ibm-plex-mono-600-latin.woff2 / -latin-ext.woff2 | SemiBold 600 | latin / latin-ext |

## Cabinet Grotesk

* Copyright: Indian Type Foundry (ITF), veroeffentlicht ueber Fontshare
* Lizenz: ITF Free Font License (FFL) — https://www.fontshare.com/licenses/itf-ffl
  Erlaubt die kostenlose Nutzung in privaten und kommerziellen Projekten
  einschliesslich Web-Einbettung/Self-Hosting auf der eigenen Website.
  NICHT erlaubt: Weitergabe oder Verkauf der Font-Dateien als eigenstaendiges
  Produkt sowie Veraenderung/Umbenennung der Schrift.
* Quelle: cdn.fontshare.com, CSS abgerufen mit
  `https://api.fontshare.com/v2/css?f[]=cabinet-grotesk@400,500,700,800,900&display=swap`

Dateien:

| Datei | Schnitt |
| --- | --- |
| cabinet-grotesk-400.woff2 | Regular 400 |
| cabinet-grotesk-500.woff2 | Medium 500 |
| cabinet-grotesk-700.woff2 | Bold 700 |
| cabinet-grotesk-800.woff2 | Extrabold 800 |
| cabinet-grotesk-900.woff2 | Black 900 |

## Aktualisieren

Neue Versionen wieder mit `curl` und einem modernen User-Agent (Chrome) laden,
damit Google woff2-URLs liefert; danach die `@font-face`-Regeln in
`src/index.css` (Dateinamen, `unicode-range`) und diese Datei anpassen.
