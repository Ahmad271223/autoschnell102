# Lasttest-Ergebnisse (28.08.2026)

Durchgeführt mit `backend/scripts/lasttest.py` gegen ein Backend mit
4 Worker-Prozessen und **aktivem Anbieter-Mock** (`MOCK_PROVIDER_FETCH=true`),
damit kein echter Verkehr bei Kleinanzeigen/mobile.de entsteht. Cache-,
Reservierungs- und Begrenzungslogik laufen dabei vollständig echt — nur der
Abruf selbst liefert synthetische Daten mit 0,4 s Verzögerung.

Simuliertes Verhalten je Nutzer: 25 % neue Links, 50 % bereits bekannte Links,
15 % Bestandsliste, 10 % Abo-Status, dazwischen 0,2–1,2 s Denkpause.

## Ergebnis

| Kennzahl | 300 Nutzer | 500 Nutzer |
| --- | --- | --- |
| Anfragen gesamt (180 s) | 39.751 | 35.217 |
| Fehlerrate gesamt | 0,09 % | 0,74 % |
| Fehlerarten | ausschließlich 503 (Rückstau) | ausschließlich 503 (Rückstau) |
| p95 Vergleich (neuer Link) | 1.407 ms | 2.488 ms |
| p99 Vergleich (neuer Link) | 9.851 ms | 20.443 ms |
| p95 Bestandsliste | 423 ms | 877 ms |
| p95 Abo-Status | 437 ms | 819 ms |
| **Externe Anbieter-Abrufe** | **191** bei 200 Inseraten | **299** bei 300 Inseraten |
| MongoDB-Verbindungen | 144 | 198 |
| CPU (Median / Spitze) | 72 % / 83 % | 71 % / 85 % |
| RAM (Spitze) | 62 % | 63 % |

## Was das zeigt

**Anbieterfreundlich.** 9.984 Vergleichsanfragen auf neue Links erzeugten
191 externe Abrufe — genau einen je Inserat. Die zentrale Begrenzung
(`MAX_CONCURRENT_KLEINANZEIGEN`, Standard 3) hält die Zahl gleichzeitiger
Abrufe unabhängig von der Nutzerzahl konstant; Snapshot-Aufnahmen belegen
dieselben Slots.

**Stabil.** Kein einziger 5xx-Fehler, kein Absturz, keine hängenden
Anfragen. Die gemeldeten Fehler sind ausnahmslos 503 mit `Retry-After` —
also bewusster Rückstau, wenn alle Abruf-Slots belegt sind.

**Grenze.** Der p99 von 20 s bei 500 Nutzern ist die Wartezeit auf einen
freien Abruf-Slot. Das ist gewollt (lieber warten als den Anbieter
überlasten), für die Bedienung aber spürbar. Zwei Stellschrauben:
`MAX_CONCURRENT_KLEINANZEIGEN` erhöhen, sobald die Anbieter-Vereinbarung das
zulässt — oder der Umbau auf Hintergrund-Jobs mit Job-Nummer, damit die
Anfrage sofort antwortet und das Ergebnis nachgereicht wird.

## Testumgebung — Einschränkung

Alles lief auf **einem** Windows-Rechner: Backend (4 Worker), MongoDB und
der Lastgenerator gleichzeitig. Die CPU-Werte enthalten also auch den
Generator, und die Latenzen sind eher pessimistisch. Auf getrennter
Staging-Hardware sind bessere Werte zu erwarten. Für eine Freigabe-Messung
sollte der Lauf auf der Zielumgebung wiederholt werden:

```bash
MOCK_PROVIDER_FETCH=true python -X utf8 scripts/lasttest.py --users 500 --duration 300
```

Das Skript bricht ab, wenn der Mock-Modus nicht aktiv ist.
