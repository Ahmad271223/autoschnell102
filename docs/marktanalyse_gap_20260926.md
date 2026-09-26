# GAP-Analyse Marktanalyse gegen Master-Auftrag (26.09.2026, HEAD 35d7699)

Zielspezifikation: Ahmads Master-Auftrag vom 26.09.2026 abends (Fahrzeug-Masterliste 170 Modelle,
EXACT_KM + ALL_KM je EZ, Datenqualität ≠ Markttiefe, sample_completeness UNKNOWN, Reporting 5/15/Monat,
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
| A5b | ALL_KM-Referenz je EZ | FEHLT | segment_id/bucket_fuer_km int(None); km_bereiche_pruefen; kein segment_scope |
| A5c | km-Profile je Modell | FEHLT | Seed nimmt KM_BUCKETS_STANDARD für alle |
| A6 | Reporting 5/15/Monat persistent | FEHLT | nur rollierender segment_verlauf; keine Sammlung, keine Blöcke, keine Confidence |
| A7 | Hot-Deal-Finder | FEHLT | chancen_ableiten = Tagesvergleich, je Tag neues Dokument, keine Klassen/Events |
| A7p | private Hot Deals | TEILWEISE | market_private_deals (Top-3 je Segment, Tagesmedian) |
| A8 | Health/Score/Proposals/Modi | FEHLT | nur leer_in_folge, last_empty_at, Stale-Alarm |
| A9 | Masterliste + Migration + Aktivierung nach Testlauf | FEHLT/TEILWEISE | Seed 72, Seeds aktivieren ohne Testlauf, testlauf_bestanden prüft nicht alle Segmente |
| A10 | Tagesbasis | TEILWEISE | fehlen: invalid_runs, verschwundene, Preiserhöhungen, Top-3/5-Wechsel, privat/Händler, Kosten/Tag |
| A11 | Modellaggregation gewichtet | TEILWEISE | Trends gewichtet; median_sample_mittel ungewichtet |
| A12 | Dashboard/Tabs | TEILWEISE | Hot Deals, Berichte, Optimierung fehlen; Modellseite ohne Tabs |
| A13 | neutrale Begriffe | TEILWEISE | *_top20_*-Aliase, „Top-N“-Labels |
| A14 | Datenschutz | VOLLSTÄNDIG | PRIVAT_FAHRZEUGFELDER, gerundete Koordinaten |
| A15 | idempotent/lock-sicher | VOLLSTÄNDIG (Bestand) | Claims, Leases, Segment-Sperre, Merker |
| A16 | Module/Sammlungen | FEHLT | reporting/health/deals/optimierung existieren nicht |

## Kosten Masterliste (170 Modelle, Ø 3,5 EZ × 7 Segmente, 5 Zeilen + Puffer 7, 1×/Tag)

| | 3 EZ | 3,5 EZ | 4 EZ |
|---|---|---|---|
| Segmente | 3.570 | 4.165 | 4.760 |
| $/Monat | 586 | 684 | 781 |
| davon ALL_KM | 84 | 98 | 112 |

Ohne Puffer (exakt 5 Zeilen) ≈ 506 $ bei 3,5 EZ. DB-Wachstum ≈ 20 MB/Tag. Reporting nur Lesen.

## Phasen

A Masterliste/Katalog + Migration 18 (needs_review, DEPRECATED = archived) → B EXACT_KM/ALL_KM (segment_scope, Migration 19)
→ C Tagesbasis + data_quality/market_depth/sample_completeness (Migration 20, Actor-Key-Protokoll)
→ D Hot Deals (markt/deals.py, market_hot_deals) → E Reporting (markt/reporting.py, market_model_reports)
→ F Health + Proposals OBSERVE (markt/health.py, optimierung.py) → G SAFE_AUTO.

## Masterliste: nicht eindeutig im mobile.de-Katalog (needs_review)

Golf Variant / A4 Avant / A6 Avant / Octavia Combi / Focus Turnier / Corolla Touring Sports → Grundmodell + body=EstateCar;
Enyaq iV → „Enyaq“; Aygo → „Aygo (X)“; Grandland → „Grandland (X)“; Ceed → Katalog „cee'd / Ceed“ (ID 26);
BMW 1er/2er nur als 116/118/120/218; Mercedes „… d“ → Modell ohne „d“ + Kraftstoff DIESEL; RAV4 → „RAV 4“.
