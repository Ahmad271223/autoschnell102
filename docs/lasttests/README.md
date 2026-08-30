# Lasttest-Gesamtbericht (Stand 30.08.2026)

Alle Läufe: **lokal auf einem Windows-11-PC** (Backend 4 Worker-Prozesse,
MongoDB und Lastgenerator auf derselben Maschine), Anbieter/E-Mail/WhatsApp
**gemockt** (`MOCK_PROVIDER_FETCH=true` — Cache-, Job-, Sperr- und
Begrenzungslogik laufen vollständig echt, nur der externe Abruf liefert
synthetische Daten mit 0,4 s Verzögerung). Rohdaten mit Zeitstempel und
Konfiguration je Lauf: [matrix/](matrix/) und [stoss/](stoss/).

**Formulierungsregel dieses Berichts:** Es gab in keinem einzigen Lauf
unerwartete 500er, Abstürze oder hängende Anfragen; sämtliche 503 waren
kontrollierte Rückstauantworten mit `Retry-After`. („Keine 5xx" wäre
falsch — 503 ist technisch 5xx und wird hier bewusst getrennt gezählt.)

## 1. Fehlerklassen (gilt für alle Tabellen)

| Klasse | Bedeutung |
| --- | --- |
| technisch unerwartet | echte Fehler (unerwartete 5xx, falsche 4xx) |
| fachlich erwartet | korrekte Ablehnung (40-Fotos-Limit, Doppelabschluss-Schutz 400/409) |
| Sicherheitstest | absichtlich eingeschleuste Schaddateien, korrekt abgelehnt |
| Race-/UX-Befund | unerwartete 404 in echter Nutzeraktion (z. B. Foto parallel gelöscht) — ausgewiesen, nicht schöngerechnet |
| Verbindungsabbruch (Client) | 599: Antwort ging verloren; Server-Seite je Lauf gegengeprüft |
| Messartefakt | Skript-/Zählbasis-Effekte, im jeweiligen Bericht benannt |

**599-Forensik:** Jeder Abbruch ist im Lauf-JSON mit Zeitpunkt, Endpunkt,
nächstem System-Tick (CPU/RAM/Disk/Netz), Backend-Fehlerlog-Fenster und —
wo möglich — dem Server-Abschluss-Nachweis erfasst (`reset_forensik`,
`server_abschluss_nachweis`). Ergebnis über alle Läufe: Abbrüche traten
vereinzelt unter Spitzen-CPU auf; der Server hatte die betroffenen
Vorgänge in **allen** nachprüfbaren Fällen genau einmal abgeschlossen
(z. B. finale T2-Läufe: DB-Delta = Client-Zähler, Differenz 0).

## 2. Matrix — 9 Szenarien, je 80 % Hauptfunktion

100 Nutzer (T9: 140), 30 s Warmup unbewertet, 300 s Messung, Denkpause
0,2–1,2 s, 10 Firmen (Chef + 2 Sucher + 1 Fahrer), 3 Wiederholungen.
T1–T3 wurden nach Skript-Verbesserungen **mit der finalen Version komplett
wiederholt**; die 9 Altläufe stehen in [matrix/](matrix/) als
Diagnose-/Schutztests (dort wurden u. a. 40-Fotos-Limit und
Doppelabschluss-Schutz unter Konkurrenz bewiesen).

| Szenario | 3 gültige Läufe | Anfragen je Lauf | techn. Fehlerrate | fachl. Ablehnungen | Hauptfunktion p95 (ms) | p99 (ms) | max. Rückstau | Drain | Integrität | RAM fällt zurück | bestanden |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T1 Links | 3/3 (+3 Diagnose) | 7075/6549/6576 | 0,01/0/0 % | 0/0/0 | 36/34/35 | 89/64/55 | 93 | 0 s | 0 | ja | **JA** |
| T2 PDF | 3/3 (+3 Diagnose) | 28785/29013/28791 | 0/0/0 % | 8/4/2 | 563/550/548 | 703/704/711 | 9 | 0 s | 0 | ja | **JA** |
| T3 Fotos (nach Fix) | 3/3 (+6 Diagnose) | 47297/48544/47390 | 0/0/0 % | 4/1/1¹ | 185/142/192 | 342/298/346 | 7 | 0 s | 0 | ja | **JA** |
| T4 Protokolle | 3/3 | 11713/4229/12477 | 0,15/0,4/0 % | 7/1/9 | 1313/1274/1340 | 1545/1475/1604 | 8 | 0 s | 0 | ja | **JA** |
| T5 E-Mail | 3/3 | 26230/28473/40753 | 0,08/0,05/0 % | 3/3/6 | 99/108/117 | 154/166/175 | 5 | 0 s | 0 (28 o. Antwort²) | ja | **JA** |
| T6 WhatsApp | 3/3 | 41042/40819/22930 | 0/0/0,07 % | 3/4/3 | 113/121/105 | 174/206/282 | 3 | 0 s | 0 (10 o. Antwort²) | ja | **JA** |
| T7 Marktplatz | 3/3 | 39646/39762/36542 | 0/0/0 % | 5/3/4 | 120/109/361 | 176/167/500 | 10 | 0 s | 0 | ja | **JA** |
| T8 Gesamtbetrieb | 3/3 | 5198/7909/8215 | 0,19/0/0 % | 0/0/0 | 80/93/67³ | 340/253/235 | 90 | 0 s | 0 | ja | **JA** |
| T9 Lastspitze 140 | 3/3 | 5761/7148/5578 | 0/0/0 % | 21/63/23 | 275/285/324 | 328/370/414 | 73 | 0 s | 0 | ja | **JA** |

¹ T3 wurde nach dem Foto-Fix mit der reparierten App erneut 3× gefahren
(bewertete Läufe). Je Lauf ~1.350 Schaddateien, **alle abgelehnt**;
Datei↔DB-Audit **0/0**. Die 6 Diagnose-Läufe davor belegen zusätzlich
die 40-Fotos-Limit-Durchsetzung unter Sättigung (~4.000 kontrollierte
Ablehnungen je Lauf) und den Fehlerzustand vor dem Fix.
² Versand „ohne Antwort": bei Verbindungsabbruch ging nur die
Client-Antwort verloren; der Server registrierte genau einen Auftrag
(Rohzahl ≤ Abbrüche desselben Laufs). Kein doppelter Versandauftrag.
Harter Beweis zusätzlich: `tests/test_send_idempotenz.py` (Retry und 10
parallele Wiederholungen mit gleichem Idempotency-Key → genau 1 Eintrag).
³ T8-Hauptmesswert = bekannte Links; Jobannahme p95 35–51 ms, Wartezeit
neuer Links p95 24–99 s = gewollter Rückstau der Anbieter-Drossel (3
gleichzeitige Abrufe), mit korrektem Status sichtbar und stets voll
abgebaut.

**Speicher-/Datei-Beweise (finale Läufe):**
- PDF: je T2-Lauf ~13.400 Verträge (~78 MB) erzeugt; Stichproben (15/Lauf):
  echter `%PDF`-Header, lesbar (pypdf), Verkäufer + Kaufpreis im Text,
  richtige Firma, alle Dateien eindeutig (SHA-256), **0 Befunde**;
  Versionierung unangetastet (Beweis-PDFs werden nie überschrieben —
  eigene Versions-Sammlung). Nach jedem Lauf: 0 DB-Reste, 0 Datei-Reste,
  Plattenplatz wieder frei.
- Fotos: je Lauf Datei↔DB-Abgleich. Der Abgleich fand einen **echten
  Fehler**: parallele Uploads/Löschungen aufs selbe Inserat verloren durch
  Lesen-Ändern-Schreiben Referenzen (bis ~1.000 Datei-Waisen je
  T3-Volllauf, in 3 Läufen reproduziert). **Behoben** (atomares
  `$push`/`$pull` + atomares Limit) und doppelt bewiesen:
  `tests/test_foto_parallel.py` (3/3 grün) **und** drei komplette
  T3-Volllast-Neuläufe gegen die reparierte App mit Audit **0/0** bei
  15.000+ Uploads je Lauf.
- Volle Platte: **noch nicht geprüft** (auf dem Arbeits-PC nicht ohne
  Risiko simulierbar). Verhalten bei Storage-Fehlern ist abgefangen
  (StorageError → 400), Nachweis unter echter Voll-Platte steht aus.

## 3. Sekundenstoß-Test (Barrier, keine Denkpausen)

Alle virtuellen Nutzer werden über ein gemeinsames Startsignal exakt
gleichzeitig freigegeben; je Anfrage sind Freigabe- und Sendezeitpunkt
protokolliert. **Spannweite über alle 24 Stöße: 6–92 ms** — der Stoß lag
jeweils weit innerhalb derselben Sekunde. Gesamtbilanz: **8.100 von 8.100
Anfragen mit HTTP 200 angenommen**, 0 verlorene Requests, 0 doppelte
Anbieter-Abrufe, 0 doppelte Snapshots, 0 hängende Jobs, Drain immer 0 s,
normale Seiten blieben während jeder Spitze erreichbar (max. 344 ms).

| Szenario | n | Spannweite | Annahme p95 | sofort | über Queue | verloren | Doppel-Abrufe |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S1 gleicher neuer Link | 100/300/500 | 8/41/29 ms | 427/936/1546 ms | 0/32/8 | Rest | 0 | 0 |
| S2 verschiedene neue | 100/300/500 | 26/13/32 ms | 476/833/1769 ms | 0 | alle | 0 | 0 |
| S3 bekannte Links | 100/300/500 | 6/17/40 ms | 399/912/1493 ms | **alle** | 0 | 0 | 0 |
| S4 50/50 | 100/300/500 | 6/12/27 ms | 442/849/1483 ms | exakt 50 % | exakt 50 % | 0 | 0 |
| S5 eine Firma, ein Link | 100/300/500 | 8/28/61 ms | 460/958/1512 ms | — | — | 0 | 0 |
| S6 zehn Firmen, ein Link | 100/300/500 | 9/24/60 ms | 451/950/1412 ms | — | — | 0 | 0 |
| S7 Doppelklick (2× je Nutzer) | 100/300/500 | 14/34/92 ms | 618/1791/2840 ms | — | alle | 0 | 0 |
| S8 + 20 PDF + 20 Foto | 100/300/500 | 8/16/31 ms | 963/1736/2068 ms | — | alle | 0 | 0 |

S7-Zusatzbeweis: jedes Doppelklick-Paar erhielt **dieselbe Job-ID**
(0 Abweichler), kein zweiter Job, kein zweiter Abruf. Vereinzelte
Zweitjobs in der Historie (S1/S5/S6, je 1) entstanden im Übergabefenster
„Job fertig ↔ Cache sichtbar" und führten zu **keinem** zweiten
Anbieterabruf (fetch_count blieb 1). Kontingent: der Linkfluss bucht kein
Kontingent (nur Inserats-Publish); dessen Doppelklick-Schutz ist separat
bewiesen (atomarer Zeitraum-Marker, `test_e2e_flow`/Review-Runde 2).

**Einordnung der Annahme-Latenz** (4 Worker, geteilte Maschine):
p95 < 500 ms hält bis ~100 exakt gleichzeitige Einreichungen; bei 300
~0,9–1 s, bei 500 ~1,5 s (S7 mit 1.000 Requests: 2,8 s). Es gehen dabei
nie Anfragen verloren — es dauert nur länger.

## 4. Freigabe-Aussage (nur lokal Windows + Mock)

- **System konnte alle Anfragen annehmen:** bis 500 gleichzeitige
  Einreichungen (bzw. 1.000 Requests im Doppelklick-Stoß) — verlustfrei.
- **Sofort verfügbar:** bekannte Links in jeder getesteten Stoßgröße.
- **Nach Warteschlange fertig:** alle neuen Links, stets vollständig
  abgearbeitet (Drain 0 s nach Messende).
- **Maximal sicher angenommene Sekundenlast:** 500 gleichzeitige
  Link-Einreichungen (Annahme-Ziel p95 < 500 ms wird dabei
  überschritten; verlustfrei bleibt es trotzdem).
- **Sekundenlast mit p95 < 500 ms:** ~100 gleichzeitige Einreichungen.
- **Maximal dauerhaft verarbeitbare Linkrate (neue Inserate):** durch die
  Anbieter-Drossel bestimmt: `MAX_CONCURRENT_KLEINANZEIGEN ÷
  Abrufdauer`. Gemessen (Mock 0,4 s, Limit 3): **~6 neue Inserate/s
  nachhaltig** (T1: ~1.800 Jobs je 300-s-Lauf). Bei realen Abrufzeiten
  von 1–3 s entsprechend **1–3 neue Inserate/s** (3.600–10.800/h) —
  bewusst anbieterfreundlich, per Umgebungsvariable skalierbar.
- **Empfehlung für diese Hardware-Klasse:** Normalbetrieb bis ~100–150
  gleichzeitig aktive Nutzer mit sehr guten Antwortzeiten; Spitzen bis
  500 gleichzeitige Einreichungen werden sicher gepuffert.

## 5. Status-Kennzeichnung

| Bereich | Status |
| --- | --- |
| Matrix T1–T9, Stoß S1–S8, Regression 56 Tests | ✅ lokal auf Windows bestanden |
| Anbieter-Abrufe, E-Mail, WhatsApp, Stripe-Versandweg | ⚠️ nur mit Mock getestet (E-Mail/WhatsApp sind auch in der App serverseitig Mock/wa.me — kein Queue-/Zustellsystem vorhanden) |
| Linux-Staging (T1/T2/T3/T8/T9 + Stoß) | ⬜ noch nicht geprüft — Anleitung unten |
| Echte Testdienste (SMTP-Sandbox, Stripe-Testmodus, mobile.de-API) | ⬜ noch nicht geprüft |
| Volle Platte, echter Anbieter-Ratelimit | ⬜ noch nicht geprüft |
| T3 nach Foto-Fix (3 finale Läufe mit reparierter App) | ✅ bestanden (Audit 0/0) |

**Hypothese, nicht bewiesen:** Der Diagnose-Lauf T1 #2 (626 Anfragen)
litt unter *wahrscheinlicher Fremdlast auf dem Einzel-PC; die Ursache ist
nicht abschließend bewiesen* (kein Prozess-/I/O-Mitschnitt vorhanden).

## 6. Notwendige Maßnahmen (aus den Tests abgeleitet)

| # | Maßnahme | Status |
| --- | --- | --- |
| 1 | Foto-Listen atomar (`$push`/`$pull`, atomares Limit) | ✅ umgesetzt + Test |
| 2 | Versand-Idempotency-Key (Backend atomar + Frontend-UUID) | ✅ umgesetzt + Test |
| 3 | Waisen-Sweep im Cleanup (Dateien ohne DB-Referenz aus Abbruch-Restposten) | ⬜ offen (klein; Befund: ≤26 Objekte je Lauf VOR Fix 1, nach Fix 1 nur noch Abbruch-Reste) |
| 4 | T3-Wiederholung nach Fix 1 (3 finale Läufe) | ✅ bestanden (Audit 0/0, 0 % techn. Fehler) |
| 5 | Linux-Staging-Durchlauf | ⬜ offen |

## 7. Wiederholung auf Linux-Staging (Anleitung)

Backend dort mit 4 Workern und Mock starten, dann:

```bash
MOCK_PROVIDER_FETCH=true python -X utf8 scripts/lasttest_matrix.py --alle --szenario T1,T2,T3,T8,T9
```

```bash
MOCK_PROVIDER_FETCH=true python -X utf8 scripts/lasttest_stoss.py
```

Abnahme wie hier: technische Fehlerrate < 1 %, 0 Doppel-Abrufe/-Versand,
0 Hänger, Drain vollständig, RAM fällt zurück, Stoß-Spannweite < 1 s,
Annahme p95 < 500 ms bei n=100. Beide Skripte verweigern den Start ohne
bestätigten Mock — es entsteht kein echter Anbieter-Verkehr.
