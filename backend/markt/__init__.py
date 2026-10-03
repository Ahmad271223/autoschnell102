# -*- coding: utf-8 -*-
"""Market Intelligence (Auftrag Ahmad 25.09.2026): eigene historische
mobile.de-Marktbeobachtung — vollstaendig getrennt vom schnellen Hauptweg
(Vergleich, Vertrag, PDF, Versand, Fahrer, Freigaben).

Aufbau (jede Datei eine Aufgabe):
  konfig        Schalter und Grenzen (alles per Umgebung, nichts hart)
  katalog       Startliste der beobachteten Modelle (mobile.de-Modell-IDs)
  segmente      Modell x km-Bereich = Segment; Sync in market_segments
  url           mobile.de-Such-URL je Segment (Preis aufsteigend)
  apify         Scraper-Lauf: starten, abwarten, Daten holen, echte Kosten
  normalisieren Scraper-Zeile -> unser Listing (ohne Telefon/Name)
  speicher      Listings, Tages-Snapshots, Preisaenderungen, Zustaende,
                Tagesaggregate, Segmentstatistik, Chancen (deterministisch)
  budget        Monatsbudget des Crawlers, atomar reserviert
  jobs          persistente Warteschlange (Lease, Retry, Dedupe), Tagesplan
  entfernung    "nicht im Sample" ist KEIN Verkauf — gezielte Nachpruefung
  abfrage       Lesewege fuer Vergleich, Chancen und Admin-Marktanalyse

Der Hauptweg liest nur fertige Daten aus diesen Sammlungen und wartet nie
auf einen Crawl."""
