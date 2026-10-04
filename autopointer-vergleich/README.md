# AutoSchnell AutoPointer-Vergleich

Kleines Windows-Hintergrundprogramm: Der Sucher klickt in **AutoPointer** ein Inserat an – ca. eine
halbe Sekunde später öffnen sich automatisch die passenden Vergleichssuchen auf **mobile.de** und/oder
**AutoScout24** als neue Browser-Tabs. Keine Eingabe, kein zusätzlicher Knopf.

> Stand 03.10.2026: erst einmal **nur für die Kunden 10001 und 10002** (Chef + Sucher). Download in der App unter
> „Programme“ – nur für freigegebene Firmen sichtbar (`AUTOPOINTER_VERGLEICH_KUNDEN`, siehe DEPLOYMENT.md,
> Abschnitt „Programme zum Herunterladen“). Allen anderen zeigt die App nichts davon.

## Lizenz: nur verbunden, nur mit Abo

1. In AutoSchnell anmelden → „AutoPointer-Vergleich“ → **Code zum Verbinden anzeigen** (6 Ziffern, 10 Minuten,
   nur mit aktivem Abo).
2. Programm starten → Fenster „Mit AutoSchnell verbinden“ → Code eintippen.
3. **Ein Konto = ein PC.** Wird dasselbe Konto auf einem zweiten PC verbunden, fragt der erste wieder nach einem
   Code. Die Browser-Anmeldung bleibt davon unberührt.
4. Jeder Vergleich geht über den AutoSchnell-Server: Abo prüfen, Links mit den **Vergleichsregeln der Firma**
   bauen (AutoSchnell → Einstellungen → Vergleich, wie der Vergleich in der App), protokollieren. Ohne Abo
   (402) oder ohne Verbindung öffnet das Programm nichts und sagt warum (rotes Symbol).
5. Wer wann welches Auto verglichen hat: Super-Admin unter Admin → „Programm-Vergleiche“, der Chef in der App
   beim Programm (nur seine Firma). Beide können PCs trennen.

## Kaufvertrag ohne Link-Einfügen

Beim Anklicken liest das Programm auch die **Inserat-ID** (mobile.de, Kleinanzeigen) bzw. die **Hash-ID**
(AutoScout24, nur vollständig und zweimal gleich gelesen). Der Server baut daraus den Inserat-Link und liest das
Inserat **im Hintergrund aus** (Daten + Fotos, wie das Einfügen in der App, zählt fürs Tageslimit). Der Abruf
wartet dafür 15 Sekunden: klickt der Sucher vorher das nächste Auto an, fällt der alte weg und der neue nimmt
seinen Platz ein (kein Stau, kein unnötiger Abruf); öffnet er das Auto in der App, startet er sofort. Für den Vertrag:
Rechtsklick → **„Kaufvertrag: Auto in AutoSchnell öffnen“** (bzw. „Vertrag“ in der Leiste) oder in der App „Deine
letzten Autos“ → der Vergleich steht sofort da. Seit 1.3.3 öffnet das in der **installierten AutoSchnell-App**
(Edge/Chrome, erkannt an ihrer Verknüpfung): ist sie offen, übernimmt dieses Fenster das Auto (manifest
`launch_handler` „focus-existing“ + `lib/programmStart.js`; mit ungespeicherter Arbeit nur ein Hinweis mit Knopf),
sonst startet sie; nur ohne installierte App öffnet der Browser. Zeigt AutoPointer die Hash-ID nur abgeschnitten (schmale Detailansicht), gibt es keinen Link –
dann die Inserat-Adresse selbst kopieren (AutoPointer: „Seite öffnen“) und in AutoSchnell einfügen.

## Bedienung

* **Fenster mit Knöpfen** (seit 1.2.0, Wunsch Ahmad 03.10.): große Anzeige AKTIV / GESTOPPT / NICHT VERBUNDEN,
  Knöpfe *Aktivieren*, *Stoppen*, *Aktuelles Auto jetzt vergleichen*, *Letzten Vergleich nochmal öffnen*,
  *Kaufvertrag: Auto in AutoSchnell öffnen*, *Mit AutoSchnell verbinden / Verbindung trennen*, *Einstellungen*,
  *Protokoll*, *Beenden*. Das X verkleinert nur in die Taskleiste; aus ist das Programm nur mit *Beenden*.
  Ein zweiter Start (Doppelklick auf die EXE) holt das Fenster nach vorne. Beim Start mit Windows startet es
  verkleinert.
* **Kleine Leiste** (seit 1.3.0, Wunsch Ahmad 03.10.): Status, *Stopp/Start*, *Vergleichen*, *Vertrag*, ☰ (großes
  Fenster) — **immer im Vordergrund**, auch wenn der Browser Tabs öffnet, fest **unten links** (Standard, verdeckt die
  Detailansicht von AutoPointer nicht) oder **unten rechts**, auf dem Bildschirm von AutoPointer. Sie nimmt
  AutoPointer nie den Fokus weg. Rechtsklick auf die Leiste: Ecke wählen, ausblenden, beenden. Mit Leiste startet
  das Programm nur mit der Leiste; das X am großen Fenster blendet es aus. Liegt die Leiste über der Tabelle, liest
  das Programm per PrintWindow statt vom Bildschirm (sonst läse es die Leiste mit).
* Symbol unten rechts im Infobereich: grün = aktiv, grau = Automatik aus, orange = AutoPointer nicht gefunden,
  rot = nicht verbunden bzw. gesperrt (Abo/Freigabe). **Doppelklick** öffnet das Fenster, **Strg+Alt+P**:
  Automatik an/aus.
* **Rechtsklick**: *Fenster öffnen*, Verbindungsstatus, *Mit AutoSchnell verbinden …*, *Verbindung trennen*, *Automatik*,
  *Aktuelles Fahrzeug jetzt vergleichen* (auch bei Pause/selbem Auto), *Letzten Vergleich erneut öffnen*,
  *Einstellungen …* (Portale, Browser, Ablauf, mit Windows starten), *Protokoll anzeigen …*.
* Ein Auto, das beim Programmstart schon angezeigt wird, öffnet nichts – erst das nächste angeklickte.
* **Neuwagen** (Zustand „Neu“) haben in AutoPointer weder Erstzulassung noch Kilometerstand: dann gilt dieses Jahr
  und 0 km (Befund 03.10.: BYD Dolphin, mobile.de).
* Dasselbe Fahrzeug öffnet nie zweimal hintereinander (Kennung aus dem gelesenen Text `Marke Modell | EZ | km | kW`,
  Lesefehler i/l/1 und o/0 zählen nicht als neues Auto). **Seit 1.5.0** entscheidet die Inserat-ID (AutoScout: Hash-ID),
  wenn beide Lesungen eine haben: zwei verschiedene Inserate mit gleichen Daten sind zwei Autos; fehlt die ID in einer
  Lesung, gilt weiter die Kennung aus den Daten (ein Lesefehler macht aus einem Auto nicht zwei).
* Bei jedem neuen Auto werden „Letzten Vergleich erneut öffnen“ und das gemerkte Inserat sofort geleert — es öffnen
  nie die Links eines früheren Autos.
* **Seit 1.4.0 erkennt der Server Marke und Modell** (Wunsch Ahmad 03.10.: „das Programm enthält kaum noch Wissen“):
  das Programm schickt nur, was AutoPointer zeigt (`roh: true`), `backend/werkzeug_erkennung.py` erkennt es — 1:1 die
  frühere Programm-Logik (am 03.10. über 16.636 Fälle ohne Abweichung abgeglichen). Verbesserungen brauchen damit nur
  ein Server-Update. Alle Regeln unten gelten weiter, laufen aber auf dem Server.
* Kann ein Auto nicht sicher gelesen werden (Marke/Modell, EZ oder km fehlen), öffnet sich **nichts**;
  unten rechts erscheint „Fahrzeug konnte nicht eindeutig erkannt werden“.
* Kennt ein Portal das Modell nicht, öffnet dieses Portal nicht (sonst gäbe es eine Suche „nur Bentley“).
* Steht in AutoPointer nur ein Platzhalter („VW Weitere VW“, „Andere“, „Sonstige“) oder eine Kleinanzeigen-
  Kategorie („VW VW-Busse“), kommt das Modell aus der Überschrift — die Wörter dürfen verstreut stehen („T5 Bulli
  multivan“ → T5 Multivan, „VW Beetle Cabrio 1.2 TSI“ → Beetle; nur Treffer im Modell-Katalog, nie Sammelnamen wie
  „T5 andere“). Fehlt sogar die Marke („Andere“), kommen Marke und Modell aus der Überschrift („Ford Mondeo
  Turnier …“ → Ford Mondeo).
* **Modell nur in der Beschreibung** (seit 1.5.1, Befund 04.10.: Mercedes „Andere“, Überschrift „Mercedes-Benz Weitere
  Mercedes Be…“, Beschreibung „meinen Mercedes C 300 e“): das Programm liest den sichtbaren Anfang der Beschreibung
  mit (vom Bildschirm, parallel zur Tabelle, kein Zeitverlust); der Server nimmt das Modell daraus nur, wenn Feld und
  Überschrift keins hergeben, und strenger als bei der Überschrift (Wörter direkt hintereinander, keine Kurznamen wie
  „G“) — mit Hinweis „Modell aus der Beschreibung übernommen … bitte kurz prüfen“. Die Beschreibung wird nicht
  gespeichert.
* Typische Lesefehler der Texterkennung bei Modellen mit Ziffern: i/l/1 und O/0 werden verwechselt („Hyundai ilO“ →
  i10; nur wenn genau ein Katalogmodell passt). In der Überschrift dürfen zwei Wörter zusammengehören („XC 60“ →
  XC60).
* Das Programm filtert **nie nach Navigationssystem** (Wunsch Ahmad 03.10.) – in der App bleibt die
  Einstellung „Navi mitvergleichen“ wie sie ist.

## Wie es funktioniert

AutoPointer (vitdev, `aprun.exe`) ist eine Delphi-Anwendung mit DevExpress-Tabellen. Geprüft am
03.10.2026: **Windows UI Automation und MSAA liefern den Zelltext nicht** (die Tabellen werden
gezeichnet). Deshalb:

1. Fenster-Handles finden: Überschrift „Technische Daten“ → `TcxGrid` → `TcxGridSite`, dazu die
   Kopf-Tabelle (Quelle, Titel, Preis). Unabhängig von Auflösung, Fenstergröße und Position.
2. Nur solange AutoPointer im Vordergrund ist, alle 250 ms eine billige Prüfsumme (BitBlt) – ändert sich
   etwas, wird gewartet, bis die Ansicht 400 ms stillsteht.
3. Es wird nur **kopiert, was ohnehin auf dem Bildschirm steht** (BitBlt – AutoPointer bekommt davon nichts mit).
   Fehlen Zeilen (weggescrollt, schmale Ansicht), sagt das Programm „Detailbereich größer ziehen“. `PrintWindow`
   (AutoPointer zeichnet die Tabelle selbst, auch weggescrollte Zeilen) nur noch, wenn es in den Einstellungen
   ausdrücklich erlaubt ist — seit 1.3.2 Standard AUS: am 03.10.2026 meldete AutoPointer zweimal genau dabei
   dieselbe „Zugriffsverletzung“ (aprun.exe, Offset 16B050B). Die **Windows-Texterkennung** (offline,
   de-DE) liest es mit 3-fachem Zoom; fehlende Felder aus einem zweiten Durchlauf. Bezeichnungen werden
   unscharf erkannt („Kibmeterstand“). Das Protokoll sagt je Auto, welcher Weg benutzt wurde.
   (Anlass 03.10.2026: AutoPointer meldete eine „Zugriffsverletzung“ in aprun.exe. AutoPointer stürzt
   nachweislich auch ohne uns ab – Windows-Ereignis vom 24.09. –, trotzdem fassen wir es so wenig wie möglich an.)
4. Hat sich die Anzeige während des Lesens geändert, wird verworfen (keine Mischdaten bei A → B → C).
5. Marke/Modell erkennt seit 1.4.0 der Server (`backend/werkzeug_erkennung.py`, Kataloge aus `backend/`,
   Lesefehler wie „Bentavga“ werden korrigiert); die Links baut er ebenfalls (`backend/routes/werkzeuge.py`).

AutoPointer wird nie verändert – das Programm liest nur, was ohnehin angezeigt wird.

Voraussetzung: Windows 10/11 mit deutscher Texterkennung (bei deutschem Windows vorhanden; sonst
*Einstellungen → Zeit und Sprache → Sprache → Deutsch → Optische Zeichenerkennung*).

## Entwickeln

```
dotnet test tests\AutoPointerVergleich.Tests.csproj
powershell -ExecutionPolicy Bypass -File build.ps1     # → dist\AutoSchnell-Vergleich.exe (eine Datei)
```

Fehlersuche: `AutoSchnell-Vergleich.exe --einmal` liest das gerade angezeigte Auto einmal und gibt Werte
(und, falls verbunden, die Server-Links im Probelauf) aus; `--verbinden <code>` verbindet ohne Fenster;
`--server <url>` nimmt einen Testserver; `--probelauf` startet das Tray-Programm, öffnet aber keinen Browser.
`AUTOSCHNELL_VERGLEICH_DATEN=<ordner>` legt Einstellungen/Schlüssel woanders ab (Tests).
`--systemcheck` (auch Knopf „Systemcheck“ im Fenster und im Menü) prüft Windows, Texterkennung, Server, Verbindung/Abo,
Version, AutoPointer, eine Probe-Lesung samt Probe-Vergleich (öffnet nichts), Browser und App. Die Tests laufen seit
1.5.0 auch in der GitHub-CI (Job „AutoPointer-Vergleich (Windows-Tests)“).

Protokoll: `%LOCALAPPDATA%\AutoSchnell\AutoPointer-Vergleich\protokoll\` (14 Tage) — seit 1.3.5 **verschlüsselt**
(Windows-DPAPI, nur derselbe Windows-Benutzer; lesbar im Protokollfenster mit Tagesauswahl und „Kopieren“ oder mit
`--protokoll [JJJJ-MM-TT]`; alte Klartext-Dateien werden beim Start verschlüsselt und gelöscht; Erkennungsbilder
ebenfalls, `--entschluesseln <datei.dat>`),
Einstellungen: `%APPDATA%\AutoSchnell\AutoPointer-Vergleich\einstellungen.json` (Schlüssel DPAPI-verschlüsselt).
