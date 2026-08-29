# Lasttest-Ergebnisse (29.08.2026, nach dem Job-Umbau)

Durchgeführt mit `backend/scripts/lasttest.py` gegen ein Backend mit
4 Worker-Prozessen und **aktivem Anbieter-Mock** (`MOCK_PROVIDER_FETCH=true`) —
es entsteht **kein echter Verkehr bei Kleinanzeigen/mobile.de**. Cache-,
Job-, Reservierungs- und Begrenzungslogik laufen dabei vollständig echt;
nur der Abruf selbst liefert synthetische Daten mit 0,4 s Verzögerung.
Das Skript verweigert den Start, wenn der Mock nicht aktiv ist.

Simuliertes Verhalten je Nutzer wie im echten Frontend: neue Links laufen
über `/listings/check` als **Hintergrundjob** (Status wird gepollt), danach
`/mobile/compare`; dazu bekannte Links, Bestandsliste, Abo-Status und
0,2–1,2 s Denkpausen.

## Ergebnis

| Kennzahl | 300 Nutzer | 500 Nutzer |
| --- | --- | --- |
| Anfragen gesamt (180 s) | 41.005 | 46.813 |
| Fehlerrate gesamt | 0,07 % | 0,07 % |
| Fehlerarten | ausschließlich kontrollierte 503-Rückstauantworten | dito |
| **Vergleich neuer Link** p50 / p95 / p99 | 0,8 s / 1,5 s / 1,8 s | 1,6 s / 2,2 s / 2,4 s |
| Vergleich bekannter Link p50 / p95 | 0,8 s / 1,7 s | 1,6 s / 2,4 s |
| **Jobwartezeit** (neues Inserat) p50 / p95 / p99 | 10,6 s / 22,5 s / 33,2 s | 20,8 s / 41,4 s / 46,3 s |
| **Externe Anbieter-Abrufe** | **199** bei 200 Inseraten | **299** bei 300 Inseraten |
| **Inserate mehrfach extern geholt** | **0** (max. 1 Abruf/Inserat) | **0** (max. 1 Abruf/Inserat) |
| MongoDB-Verbindungen | 143 | 192 |
| CPU (Median / Spitze) | 79 % / 99 % | 78 % / 89 % |
| RAM (Spitze) | 56 % | 54 % |

## Was das zeigt

**Keine unerwarteten Fehler.** Keine unerwarteten 500er, keine Abstürze,
keine hängenden Anfragen. Die einzigen Nicht-200-Antworten (29 bzw. 31
Stück, ~0,2 % der bekannten-Link-Vergleiche) sind **kontrollierte
503-Rückstauantworten mit `Retry-After`** — das Frontend wiederholt sie
automatisch und zeigt dem Nutzer nur die Wartemeldung. Hinweis zur
Einordnung: 503 ist technisch ein 5xx-Status; gemeint und gemessen ist
hier der Unterschied zwischen *kontrolliertem Rückstau* (503 mit
Retry-After, automatisch wiederholt) und *unerwarteten Fehlern* (500,
Absturz, Timeout) — von letzteren gab es **null**.

**Beweisbar anbieterfreundlich.** Der neue Abrufzähler je Inserat
(`fetch_count`) belegt: **kein einziges Inserat wurde mehrfach extern
geholt** (`inserate_mehrfach_extern_geholt: 0`,
`max_abrufe_pro_inserat: 1`) — bei ~7.600 Vergleichsanfragen auf neue
Links pro Lauf. Die zentrale Begrenzung (Standard: 3 gleichzeitige
Kleinanzeigen-Abrufe) gilt unabhängig von der Nutzerzahl; Snapshots
laufen durch dieselben Slots.

**Der Job-Umbau wirkt.** Die HTTP-Antworten bleiben schnell (Vergleich
p99 unter 2,5 s), weil niemand mehr minutenlang auf den Anbieter wartet —
das Warten steckt sichtbar und kontrolliert in der **Jobwartezeit**
(p95: 22 s bei 300, 41 s bei 500 Nutzern). Das ist die bewusste Folge des
Anbieter-Limits von 3: 200–300 neue Inserate ÷ 3 gleichzeitige Abrufe.
Stellschraube ist `MAX_CONCURRENT_KLEINANZEIGEN` bzw. der passende
API-Tarif des Anbieters.

## Testumgebung — Einschränkung

Alles lief auf **einem** Windows-Rechner: Backend (4 Worker), MongoDB und
der Lastgenerator gleichzeitig (CPU-Spitze 99 % enthält den Generator).
Für die Freigabe-Messung muss der Lauf auf der Staging-Zielumgebung
wiederholt werden:

```bash
MOCK_PROVIDER_FETCH=true python -X utf8 scripts/lasttest.py --users 500 --duration 300
```

Abnahmekriterien: Fehlerrate < 1 % (nur 503-Rückstau),
`inserate_mehrfach_extern_geholt == 0`, Vergleich p95 < 3 s,
Jobwartezeit p95 < 60 s, keine unerwarteten 500er im Backend-Log.
