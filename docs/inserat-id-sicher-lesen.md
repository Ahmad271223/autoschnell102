# Inserat-ID (mobile.de) aus AutoPointer sicher lesen – Analyse (13.10.2026, Wunsch Ahmad)

## Wo wir stehen

Das Programm „AutoSchnell Vergleich“ liest die Detailansicht von AutoPointer mit der **Windows-Texterkennung**
(Bildschirm-Abbild → `DetailLeser`). Die Zeile „Inserat-ID“ wird im **zweiten Blick** (`ZweiterBlick`) aus demselben
Abbild noch einmal gelesen (Zoom 5 in Farbe, Zoom 4 in Graustufen mit Kontrast), Alternativen gehen mit zum Server.
Der Server (`werkzeuge.inserat_nummer`) repariert nur **eindeutige** Verwechslungen (O→0, I/l/i/|→1, S→5, Z→2, höchstens
3 Zeichen) und baut daraus den Link (`inserat_url`, 6–20 Ziffern). Seit 09.10. (M1) gilt: eine reparierte oder aus der
zweiten Lesung stammende Nummer ist „unsicher“ — passt das Inserat dazu nicht zum Bildschirm, gibt es keinen Link.

Schwachstellen, die übrig bleiben:

1. **Mehrdeutige Zeichen** werden gar nicht repariert (B = 8 oder 3, G = 6 oder 9, Z/T = 7, D/Q = 0, A = 4): die Nummer
   bleibt mit Buchstaben → kein Link.
2. **Eine falsch gelesene Ziffer, die trotzdem eine Ziffer ist** (3 statt 8, 1 statt 7) fällt nicht auf: die Nummer ist
   „rein numerisch“, gilt als sicher, und der Link zeigt ein **fremdes Auto**. Das ist das eigentliche Risiko (Kaufvertrag
   mit falschem Fahrzeug). Die Prüfung gegen den Bildschirm (M1) läuft nur bei reparierten Nummern.
3. **Abgeschnittene Spalte** (schmale Detailansicht): die ID ist unvollständig → kein Link.
4. Die Windows-Texterkennung kennt **keine Ziffern-Vorgabe** (Whitelist) und liefert **keine Zuverlässigkeitswerte**,
   man kann also nicht sagen, „wie sicher“ eine Lesung war.

## Was wir tun können – von sicher nach weniger sicher

### A. Die ID gar nicht erst lesen, sondern abfragen (Fehlerquote 0)

1. **UI Automation (UIA) statt Bildschirm lesen.** Fast alle Windows-Programme (WinForms, WPF, DevExpress-Tabellen,
   auch Electron/Chromium) stellen ihre Texte über die Barrierefreiheits-Schnittstelle bereit. Dann liest das Programm
   die Zelle „Inserat-ID“ als **Text**, nicht als Bild — exakt, auch die Hash-ID von AutoScout, auch bei schmaler
   Spalte. Prüfen in 30 Minuten: Microsoft **Accessibility Insights** oder **Inspect.exe** auf die AutoPointer-
   Detailansicht richten; stehen dort die Zellen mit Namen/Wert, ist der Weg frei (`System.Windows.Automation` im C#-
   Programm, 1–2 Tage). Das ist die mit Abstand beste Lösung, deshalb zuerst prüfen.
2. **„Seite öffnen“ nutzen.** AutoPointer kann das Inserat im Browser öffnen; in der Adresse steht die ID
   (`…details.html?id=…` bzw. `/auto-inserat/…/<id>.html`), und die **Browser-Erweiterung** liest Seite und Adresse
   bereits (`listing_identity.extract_mobile_id`). Für den Kaufvertrag (wo es auf die richtige ID ankommt) könnte das
   Programm diesen Weg nehmen statt der Texterkennung — der Vertragsweg mit Erweiterung („inserat-gelesen“) ist dafür
   schon da. Vergleichssuchen brauchen die ID nicht, nur Marke/Modell/Preis.
3. **Zwischenablage.** Erlaubt AutoPointer „Kopieren“ auf der ID-Zelle (Rechtsklick oder Strg+C), kann das Programm
   das auslösen und die Zwischenablage lesen. Einmal am Gerät ausprobieren.
4. **AutoPointers eigene Daten.** Liegt die Inseratsliste lokal (SQLite/JSON unter %AppData%/%ProgramData%), ließe
   sie sich direkt lesen — vorher mit dem AutoPointer-Anbieter klären (Nutzungsbedingungen).

### B. Wenn es beim Lesen vom Bildschirm bleibt: die Lesung absichern

1. **Plausibilität der Nummer.** mobile.de-IDs haben 9 (ältere) oder 14 Stellen (aktuell, z. B. 43976177142304),
   Kleinanzeigen 10 Stellen. `inserat_url` erlaubt 6–20 Ziffern — enger fassen (mobile genau 9 oder 14,
   Kleinanzeigen 9–11); alles andere ist „unsicher“ (eine 13- oder 15-stellige Lesung ist sicher falsch gelesen).
2. **Mehrfachlesung mit Mehrheit je Ziffer.** Drei Aufbereitungen (Farbe Zoom 5, Graustufen Zoom 4, zusätzlich
   schwarz/weiß Zoom 6) und je Stelle die Mehrheit; erst wenn zwei Lesungen **Zeichen für Zeichen** gleich sind, gilt
   die Nummer als sicher — so wie heute schon die Hash-ID („zweimal gleich gelesen“).
3. **Kandidaten statt Reparatur.** Aus mehrdeutigen Zeichen mehrere Kandidaten bilden (B→8/3, G→6/9, Z→2/7, D/Q→0,
   T→7, A→4). Der Server prüft die Kandidaten: steht einer im Speicher (`listings_cache`) oder liest die Erweiterung
   das Inserat, wird Marke/Modell/Preis/km mit dem Bildschirm verglichen — nur der **einzig passende** Kandidat wird
   zum Link; keiner oder mehrere passend → unsicher, kein Link.
4. **Immer prüfen, nicht nur bei reparierten Nummern.** Der Bildschirm-Abgleich (Marke, Modell, Preis, km, EZ) auch
   für direkt gelesene Nummern; in der Leiste eine Ampel „ID bestätigt“ / „ID unsicher“; „Vertrag“ nur bei bestätigt,
   sonst der bekannte Hinweis „Adresse aus dem Browser einfügen“. Das schließt Punkt 2 der Schwachstellen.
5. **Aus Korrekturen lernen.** Fügt der Sucher nach „unsicher“ die richtige Adresse ein, speichert der Server das Paar
   (gelesener Text → richtige ID). Daraus ergibt sich, welche Zeichen AutoPointers Schrift wirklich verwechselt, und
   der Systemcheck zeigt die Fehlerquote je Woche.
6. **Schrift groß genug.** Unter etwa 18 Pixel Zeilenhöhe liest jede Texterkennung schlecht. Das Programm kennt die
   Zeilenhöhe aus dem ersten Durchgang — bei zu kleiner Schrift einmalig den Hinweis „AutoPointer-Zoom erhöhen“.
7. **Eigene Ziffern-Erkennung für die ID-Zeile.** AutoPointer benutzt immer dieselbe Schrift. Zehn Ziffern-Vorlagen
   aus genau dieser Schrift (Vorlagenvergleich, ohne fremde Bibliothek) erkennen die Nummer deutlich zuverlässiger
   als die allgemeine Texterkennung und liefern einen Vergleichswert je Ziffer — damit gibt es endlich ein Maß für
   „sicher“. Alternativ Tesseract nur für diese Zeile mit Ziffern-Whitelist (ca. 15 MB mehr im Programm).

### C. Vorgehen

1. Messen, bevor umgebaut wird: 200 gespeicherte Lesebilder (`Lesebild`, Betreiber-Liste) mit der richtigen ID
   versehen und die Fehlerquote des heutigen Weges festhalten.
2. **Zuerst A1 (UIA) prüfen.** Geht es, erledigt sich der Rest; die Texterkennung bleibt nur als Rückfall.
3. Sonst B4 + B1 sofort (kleine Änderungen am Server, schließen das Risiko „fremdes Auto“), danach B2/B3 im Programm,
   B7 als größerer Schritt.

Betroffene Stellen: `autopointer-vergleich/src/DetailLeser.cs`, `ZweiterBlick.cs`, `backend/werkzeuge.py`
(`inserat_nummer`, `inserat_url`), `backend/routes/werkzeuge.py` (M1-Prüfung).
