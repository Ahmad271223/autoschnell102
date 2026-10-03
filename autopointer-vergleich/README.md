# AutoSchnell AutoPointer-Vergleich

Kleines Windows-Hintergrundprogramm: Der Sucher klickt in **AutoPointer** ein Inserat an – ca. eine
halbe Sekunde später öffnen sich automatisch die passenden Vergleichssuchen auf **mobile.de** und/oder
**AutoScout24** als neue Browser-Tabs. Keine Eingabe, kein zusätzlicher Knopf.

> Stand 03.10.2026: erst einmal **nur für Kunde 10002** (Chef + Sucher). Download in der App unter
> „Programme“ – nur für freigegebene Firmen sichtbar (`AUTOPOINTER_VERGLEICH_KUNDEN`, siehe DEPLOYMENT.md,
> Abschnitt „Programme zum Herunterladen“). Allen anderen zeigt die App nichts davon.

## Bedienung

* `AutoSchnell-Vergleich.exe` starten → grünes Lupen-Symbol unten rechts im Infobereich.
  * grün = aktiv, grau = Automatik aus, orange = AutoPointer nicht gefunden
* **Doppelklick** auf das Symbol oder **Strg+Alt+P**: Automatik an/aus (z. B. nur durchscrollen).
* **Rechtsklick** auf das Symbol:
  * *Aktuelles Fahrzeug jetzt vergleichen* – sofort, auch bei Pause oder demselben Auto
  * *Letzten Vergleich erneut öffnen*
  * *Einstellungen …* – Portale, Kilometer-/EZ-Bereich, Leistung, Kraftstoff, Getriebe, Browser, Autostart
  * *Protokoll anzeigen …* – was erkannt und welche Links gebaut wurden
* Ein Auto, das beim Programmstart schon angezeigt wird, öffnet nichts – erst das nächste angeklickte.
* Dasselbe Fahrzeug öffnet nie zweimal hintereinander (Kennung `Marke Modell | EZ | km | kW`).
* Kann ein Auto nicht sicher gelesen werden (Marke/Modell, EZ oder km fehlen), öffnet sich **nichts**;
  unten rechts erscheint „Fahrzeug konnte nicht eindeutig erkannt werden“.
* Wird das Modell im Katalog eines Portals nicht gefunden, öffnet dieses Portal nicht (sonst gäbe es
  eine Suche „nur Bentley“). Abschaltbar in den Einstellungen.

Standard-Filter (Vorgabe Ahmad 03.10.2026, wie `older_exact 1` / `plus 20000` / `min_ps 5` im Backend): Modell exakt,
EZ ab Vorjahr und neuer, Kilometer bis Kilometerstand + 20.000, Leistung ab 5 PS weniger (nach oben offen),
gleicher Kraftstoff, gleiches Getriebe, keine Unfallwagen, nur Deutschland, Preis aufsteigend.

## Wie es funktioniert

AutoPointer (vitdev, `aprun.exe`) ist eine Delphi-Anwendung mit DevExpress-Tabellen. Geprüft am
03.10.2026: **Windows UI Automation und MSAA liefern den Zelltext nicht** (die Tabellen werden
gezeichnet). Deshalb:

1. Fenster-Handles finden: Überschrift „Technische Daten“ → `TcxGrid` → `TcxGridSite`, dazu die
   Kopf-Tabelle (Quelle, Titel, Preis). Unabhängig von Auflösung, Fenstergröße und Position.
2. Alle 250 ms eine billige Prüfsumme (BitBlt) – ändert sich etwas, wird gewartet, bis die Ansicht
   400 ms stillsteht.
3. `PrintWindow` lässt die Tabelle sich selbst in ein Bild zeichnen (auch weggescrollte Zeilen bis zur
   Inserat-ID), die **Windows-Texterkennung** (offline, de-DE) liest es mit 3-fachem Zoom; fehlende
   Felder aus einem zweiten Durchlauf.
4. Hat sich die Anzeige während des Lesens geändert, wird verworfen (keine Mischdaten bei A → B → C).
5. Links nach denselben Regeln wie `backend/mobile_service.py` / `autoscout_service.py` /
   `fahrzeug_codes.py`; die Marken-/Modellkataloge werden direkt aus `backend/` eingebunden.

AutoPointer wird nie verändert – das Programm liest nur, was ohnehin angezeigt wird.

Voraussetzung: Windows 10/11 mit deutscher Texterkennung (bei deutschem Windows vorhanden; sonst
*Einstellungen → Zeit und Sprache → Sprache → Deutsch → Optische Zeichenerkennung*).

## Entwickeln

```
dotnet test tests\AutoPointerVergleich.Tests.csproj
powershell -ExecutionPolicy Bypass -File build.ps1     # → dist\AutoSchnell-Vergleich.exe (eine Datei)
```

Fehlersuche ohne Tray: `AutoSchnell-Vergleich.exe --einmal [--bilder <Ordner>] [--oeffnen]` liest das
gerade angezeigte Auto einmal und gibt Werte + Links aus. `--probelauf` startet das Tray-Programm,
öffnet aber keinen Browser.

Protokoll: `%LOCALAPPDATA%\AutoSchnell\AutoPointer-Vergleich\protokoll\` (14 Tage),
Einstellungen: `%APPDATA%\AutoSchnell\AutoPointer-Vergleich\einstellungen.json`.
