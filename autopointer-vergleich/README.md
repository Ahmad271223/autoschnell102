# AutoSchnell AutoPointer-Vergleich

Kleines Windows-Hintergrundprogramm: Der Sucher klickt in **AutoPointer** ein Inserat an – ca. eine
halbe Sekunde später öffnen sich automatisch die passenden Vergleichssuchen auf **mobile.de** und/oder
**AutoScout24** als neue Browser-Tabs. Keine Eingabe, kein zusätzlicher Knopf.

> Stand 03.10.2026: erst einmal **nur für Kunde 10002** (Chef + Sucher). Download in der App unter
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

## Bedienung

* Symbol unten rechts im Infobereich: grün = aktiv, grau = Automatik aus, orange = AutoPointer nicht gefunden,
  rot = nicht verbunden bzw. gesperrt (Abo/Freigabe).
* **Doppelklick** auf das Symbol oder **Strg+Alt+P**: Automatik an/aus (z. B. nur durchscrollen).
* **Rechtsklick**: Verbindungsstatus, *Mit AutoSchnell verbinden …*, *Verbindung trennen*, *Automatik*,
  *Aktuelles Fahrzeug jetzt vergleichen* (auch bei Pause/selbem Auto), *Letzten Vergleich erneut öffnen*,
  *Einstellungen …* (Portale, Browser, Ablauf, mit Windows starten), *Protokoll anzeigen …*.
* Ein Auto, das beim Programmstart schon angezeigt wird, öffnet nichts – erst das nächste angeklickte.
* Dasselbe Fahrzeug öffnet nie zweimal hintereinander (Kennung `Marke Modell | EZ | km | kW`).
* Kann ein Auto nicht sicher gelesen werden (Marke/Modell, EZ oder km fehlen), öffnet sich **nichts**;
  unten rechts erscheint „Fahrzeug konnte nicht eindeutig erkannt werden“.
* Kennt ein Portal das Modell nicht, öffnet dieses Portal nicht (sonst gäbe es eine Suche „nur Bentley“).

## Wie es funktioniert

AutoPointer (vitdev, `aprun.exe`) ist eine Delphi-Anwendung mit DevExpress-Tabellen. Geprüft am
03.10.2026: **Windows UI Automation und MSAA liefern den Zelltext nicht** (die Tabellen werden
gezeichnet). Deshalb:

1. Fenster-Handles finden: Überschrift „Technische Daten“ → `TcxGrid` → `TcxGridSite`, dazu die
   Kopf-Tabelle (Quelle, Titel, Preis). Unabhängig von Auflösung, Fenstergröße und Position.
2. Nur solange AutoPointer im Vordergrund ist, alle 250 ms eine billige Prüfsumme (BitBlt) – ändert sich
   etwas, wird gewartet, bis die Ansicht 400 ms stillsteht.
3. `PrintWindow` lässt die Tabelle sich selbst in ein Bild zeichnen (auch weggescrollte Zeilen bis zur
   Inserat-ID), die **Windows-Texterkennung** (offline, de-DE) liest es mit 3-fachem Zoom; fehlende
   Felder aus einem zweiten Durchlauf. Bezeichnungen werden unscharf erkannt („Kibmeterstand“).
4. Hat sich die Anzeige während des Lesens geändert, wird verworfen (keine Mischdaten bei A → B → C).
5. Marke/Modell trennt das Programm mit den Katalogen aus `backend/` (Lesefehler wie „Bentavga“ werden
   korrigiert); die Links baut der Server (`backend/routes/werkzeuge.py`).

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

Protokoll: `%LOCALAPPDATA%\AutoSchnell\AutoPointer-Vergleich\protokoll\` (14 Tage),
Einstellungen: `%APPDATA%\AutoSchnell\AutoPointer-Vergleich\einstellungen.json` (Schlüssel DPAPI-verschlüsselt).
