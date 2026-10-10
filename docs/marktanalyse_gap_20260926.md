# GAP-Analyse Marktanalyse gegen Master-Auftrag (26.09.2026, HEAD 35d7699)

Zielspezifikation: Ahmads Master-Auftrag vom 26.09.2026 abends (Fahrzeug-Masterliste 170 Modelle,
6 km-Segmente je EZ (ALL_KM am 26.09. abends von Ahmad gestrichen), Datenqualität ≠ Markttiefe, sample_completeness UNKNOWN, Reporting 5/15/Monat,
Hot Deals, Segment Health, Optimierung in Stufen). Nur Lesung, Zeilenangaben Stand 35d7699.

## Status je Anforderung

| Nr. | Anforderung | Status | Beleg / Lücke |
|---|---|---|---|
| A1a | rows-Änderung = neue Fassung | VOLLSTÄNDIG | auftraege.DEFINITION_FELDER enthält rows; aendern() version+1; segment_id trägt vN |
| A1b | kein Testlauf, wenn nur rows | VOLLSTÄNDIG | filter_hash ohne rows; _testlauf_pruefen vergleicht filter_hash |
| A1c | Filteränderung inkl. EZ/km braucht Testlauf | TEILWEISE | ez_years/km_buckets sind nicht in FILTER_FELDER → keine Testlauf-Pflicht |
| A1d | Berichte mischen nie Fassungen | TEILWEISE | Tagesstats ohne version/definition_hash; chancen() ohne Fassungsfilter |
| A2 | data_quality ≠ market_depth | FEHLT | speicher.datenlage() = ein Wert; 2 von 5 → „niedrig“ (rot) |
| A3 | Karte: Region/Karosserie nur mit Daten | VOLLSTÄNDIG | abfrage.modelle_fuer_fahrzeug |
| A4 | sample_completeness UNKNOWN, keine geratenen Felder | FEHLT (Verstoß) | normalisieren.markt_gesamt rät Feldnamen; kein Key-Protokoll |
| A5a | EZ einzeln, sechs km | VOLLSTÄNDIG | konfig, synchronisieren |
| A5b | ALL_KM-Referenz je EZ | GESTRICHEN (Ahmad 26.09. abends) | keine Referenzsegmente, keine ALL_KM-Crawls |
| A5c | km-Profile je Modell | FEHLT | Seed nimmt KM_BUCKETS_STANDARD für alle |
| A6 | Reporting 5/15/Monat persistent | ERLEDIGT (Phase E) | markt/berichte.py, market_model_reports (eingefroren, Unique je Modell/Typ/Periode), Blöcke, Confidence, Übersicht aller Modelle |
| A7 | Hot-Deal-Finder | ERLEDIGT (Phase D, 347fde0) | markt/deals.py: Referenz 30 Tage, Basis ≥ 7 Tage/5 Inserate, Klassen DEAL/STRONG/EXTREME, Zustand + Ereignis-Historie getrennt |
| A7p | private Hot Deals | TEILWEISE | Hot Deals mit Kennzeichen privat (Filter, Zähler); market_private_deals bleibt; echter Privat-Crawl (PRIVATE_TOP3) offen |
| A8 | Health/Score/Proposals/Modi | ERLEDIGT (Phasen F/G) | markt/health.py (HOT/HEALTHY/NORMAL/THIN/EMPTY/UNSTABLE/STALE + UNKNOWN, Activity Score, konfigurierbare Frequenz), markt/optimierung.py (MERGE/SPLIT/REDUCE/PAUSE/HOT; OBSERVE Standard, SAFE_AUTO mit Protokoll und Ruecknahme, Tagesplan liest die Wirkung nur in SAFE_AUTO; FULL_AUTO gesperrt) |
| A9 | Masterliste + Migration + Aktivierung nach Testlauf | FEHLT/TEILWEISE | Seed 72, Seeds aktivieren ohne Testlauf, testlauf_bestanden prüft nicht alle Segmente |
| A10 | Tagesbasis | TEILWEISE | fehlen: invalid_runs, verschwundene, Preiserhöhungen, Top-3/5-Wechsel, privat/Händler, Kosten/Tag |
| A11 | Modellaggregation gewichtet | TEILWEISE | Trends gewichtet; median_sample_mittel ungewichtet |
| A12 | Dashboard/Tabs | TEILWEISE | Hot Deals, Berichte (Phase D/E) und Segment-Optimierung (Phase F) da; Modellseite ohne Tabs (Links auf Berichte/Optimierung, Health-Badge) |
| A13 | neutrale Begriffe | TEILWEISE | *_top20_*-Aliase, „Top-N“-Labels |
| A14 | Datenschutz | VOLLSTÄNDIG | PRIVAT_FAHRZEUGFELDER, gerundete Koordinaten |
| A15 | idempotent/lock-sicher | VOLLSTÄNDIG (Bestand) | Claims, Leases, Segment-Sperre, Merker |
| A16 | Module/Sammlungen | VOLLSTÄNDIG | deals.py, berichte.py, auswertung.py (Worker), health.py, optimierung.py; market_segment_health (+ _history nur Wechsel), market_model_health, market_optimization_proposals |

## Kosten Masterliste (170 Modelle, 4.068 Segmente, 5 Zeilen, 1×/Tag) — NUR Crawls

Nachgerechnet mit der eingebauten Masterliste (fast alle Zeilen haben 4 EZ-Jahre; Aygo X 3):

| Abgerufene Zeilen je Segment | $/Tag | $/Monat |
|---|---|---|
| 7 (5 + Puffer 2, heutige Einstellung) | 21,97 | 668 |
| 6 (Puffer 1) | 19,12 | 581 |
| 5 (ohne Puffer) | 16,27 | 495 |

Dazu Entfernungsprüfung ≈ 9 $/Monat. Sammel-Testlauf einmalig ≈ 6 $. **Berichte (5/15/Monat), Diagramme, Trends,
Hot Deals = 0 Crawls** (nur DB/Rechenarbeit). Bei 500 $ Budget plant der Tagesplan automatisch ≈ 74 % der Segmente je Tag.

## Phasen

A Masterliste/Katalog + Migration 18 (needs_review, DEPRECATED = archived) — ERLEDIGT 2285a5c → B (ALL_KM gestrichen; Kostenfelder + Architekturtest) — ERLEDIGT 7ebe935
→ C Tagesbasis + data_quality/market_depth/sample_completeness (Migration 19, Actor-Key-Protokoll) — ERLEDIGT 7ebe935
→ D Hot Deals (markt/deals.py, market_hot_deals + market_hot_deal_events) — ERLEDIGT 347fde0
→ E Berichte 5/15/Monat (markt/berichte.py, market_model_reports, Worker markt_auswertung) — ERLEDIGT
→ F Health + Proposals OBSERVE (markt/health.py, optimierung.py, Admin → Segment-Optimierung) — ERLEDIGT → G SAFE_AUTO (market_optimization_changes, jobs.tagesplan) — ERLEDIGT.

## Masterliste: nicht eindeutig im mobile.de-Katalog (needs_review)

Golf Variant / A4 Avant / A6 Avant / Octavia Combi / Focus Turnier / Corolla Touring Sports → Grundmodell + body=EstateCar;
Enyaq iV → „Enyaq“; Aygo → „Aygo (X)“; Grandland → „Grandland (X)“; Ceed → Katalog „cee'd / Ceed“ (ID 26);
BMW 1er/2er nur als 116/118/120/218; Mercedes „… d“ → Modell ohne „d“ + Kraftstoff DIESEL; RAV4 → „RAV 4“.
