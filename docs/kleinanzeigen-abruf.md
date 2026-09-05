# Kleinanzeigen-Abruf: Wer ruft wann mit welcher IP ab? (Stand 29.08.2026)

Reine Dokumentation des Ist-Zustands — keine Änderung. Alle Zeilenangaben
beziehen sich auf den aktuellen Branch-Stand.

## Klare Antwort vorab

**„Teilweise Nutzer-PC."** Es hängt vom Schalter `CLIENT_FETCH_KLEINANZEIGEN`
ab (Standard: **aus**):

| Konstellation | Wer ruft die Kleinanzeigen-Seite ab | Welche IP sieht Kleinanzeigen |
| --- | --- | --- |
| Schalter AUS (Standard, heutiger Betrieb) | **Backend-Server** (Datenabruf + Beweis-Snapshot) | Server-IP |
| Schalter AN, Inseratsdaten | **Browser des Nutzers** über die Erweiterung | Nutzer-IP |
| Schalter AN, **Beweis-Snapshot** | **Backend-Server** (Playwright) | Server-IP |
| Schalter AN, `/listings/resolve` bei unbekanntem Link | **Backend-Server** | Server-IP |
| Bekanntes Inserat (Cache-Treffer), egal welcher Schalter | **niemand** — nur Datenbank | keine |

Ein reiner „immer Nutzer-PC"-Betrieb existiert derzeit **nicht**, selbst mit
aktivem Schalter, wegen der zwei server-seitigen Restpfade (Snapshot,
resolve).

## Der vollständige Ablauf (mit Codestellen)

### 1. Nutzer fügt Link ein

Frontend [Vergleich.jsx](../frontend/src/pages/app/Vergleich.jsx) →
`POST /api/listings/check` ([routes/listings.py:400 ff.](../backend/routes/listings.py))
bzw. anschließend `POST /api/mobile/compare`
([routes/listings.py:67 ff.](../backend/routes/listings.py)).

### 2. Cache-Prüfung

`peek_cached_listing` ([listing_identity.py](../backend/listing_identity.py))
prüft **zuerst** den globalen Cache (`listings_cache`, ein Eintrag je
Inserat) und — wenn eine `dealer_id` übergeben wird — zusätzlich die
**händler-eigene Quarantäne** (`listings_cache_client`).

### 3. Inserat bekannt → nur vorhandene Daten

**Ja, ausschließlich.** Bei frischem Treffer werden die gespeicherten Daten
zurückgegeben (`use_count`/`last_used_at` werden aktualisiert); es findet
**kein** externer Abruf statt. Der Regressionstest
[test_link_jobs.py](../backend/tests/test_link_jobs.py) beweist per
`fetch_count == 1`, dass auch 15 parallele Anfragen keinen zweiten Abruf
auslösen.

### 4. Inserat unbekannt — wer ruft ab?

**Schalter AUS (Standard):** `/listings/check` legt einen Hintergrundjob an
([link_jobs.py](../backend/link_jobs.py)); der Job ruft
`get_or_fetch_listing` → `fetch_listing`
([provider_fetch.py](../backend/provider_fetch.py)) →
`fetch_kleinanzeigen_vehicle` → `_fetch_html`
([kleinanzeigen_service.py:480/555](../backend/kleinanzeigen_service.py)).
Das ist ein **HTTP-Abruf durch den Backend-Server** — Kleinanzeigen sieht
die **Server-IP**. Gedrosselt auf `MAX_CONCURRENT_KLEINANZEIGEN` (Standard
3) gleichzeitige Abrufe über alle Prozesse
([provider_limiter.py](../backend/provider_limiter.py)).

**Schalter AN:** `/listings/check` und `/mobile/compare` antworten bei
unbekanntem Link mit `needs_client_fetch`
([routes/listings.py:118–125 und 434–436](../backend/routes/listings.py)).
Das Frontend lädt die Seite über die **Browser-Erweiterung**
([frontend/src/lib/clientFetch.js](../frontend/src/lib/clientFetch.js),
[browser-extension/](../browser-extension/)) — also mit der **IP des
eingeloggten Nutzers** — und schickt das HTML an `POST /api/listings/ingest`.

### 5. Automatischer Backend-Fallback, wenn die Erweiterung fehlt?

**Für die Inseratsdaten: NEIN.** Ohne Erweiterung zeigt das Frontend die
Meldung „Abruf-Helfer benötigt" und bricht ab
([Vergleich.jsx, Zweig `needs_client_fetch`](../frontend/src/pages/app/Vergleich.jsx));
der Server holt die Seite dann **nicht** ersatzweise selbst.

**ABER es gibt zwei server-seitige Restpfade, die auch bei aktivem
Schalter greifen:**

1. **Beweis-Snapshot**: Nach erfolgreichem Vergleich erzeugt der Server
   für die Beweissicherung einen Playwright-Screenshot der ECHTEN
   Inseratsseite ([routes/listings.py:203/226 ff.](../backend/routes/listings.py)
   → [snapshot_service.py](../backend/snapshot_service.py)) — ein
   **Server-Seitenaufruf** mit Server-IP (gedrosselt über dieselben
   Anbieter-Slots, aber eben server-seitig).
2. **`POST /listings/resolve`**: prüft zwar Quarantäne und Cache, ruft bei
   Miss aber direkt `get_or_fetch_listing` auf — **ohne**
   `needs_client_fetch`-Weiche ([routes/listings.py:559 ff.](../backend/routes/listings.py)).
   Das Haupt-Frontend nutzt diesen Endpunkt nicht, er ist aber per API
   erreichbar.

### 6. Was bewirken die Umgebungsvariablen?

| Variable | Wirkung |
| --- | --- |
| `CLIENT_FETCH_KLEINANZEIGEN` (Standard aus) | AN = neue Kleinanzeigen-Links holt der Nutzer-Browser via Erweiterung; Server-Datenabruf für diese Links abgeschaltet (außer Restpfade oben) |
| `MAX_CONCURRENT_KLEINANZEIGEN` (3) | globale Obergrenze gleichzeitiger Server-Abrufe (Daten **und** Snapshots), über alle Worker/Server |
| `LISTING_CACHE_TTL_HOURS` (8760) | wie lange ein Server-Abruf im globalen Cache gilt |
| `CLIENT_INGEST_TTL_HOURS` (24) / `CLIENT_CONFIRMED_TTL_HOURS` (168) | Gültigkeit von Client-Einreichungen in Quarantäne / nach Freigabe |
| `MOCK_PROVIDER_FETCH` (aus; nur Staging) | ersetzt JEDEN externen Abruf durch synthetische Daten; Produktions-Check verweigert damit den Start |
| `PROXY_ENABLED`/`PROXY_URL` | optionaler Proxy für Server-Abrufe (dann sieht Kleinanzeigen die Proxy-IP) |

### 7. Ein Abruf bei vielen gleichzeitigen Nutzern?

**Ja, bewiesen.** Lease + Single-Flight in `get_or_fetch_listing`, EIN
aktiver Job je Inserat (Unique-Index in [link_jobs.py](../backend/link_jobs.py)),
und der Zähler `fetch_count`. Tests:
[test_listing_cache.py](../backend/tests/test_listing_cache.py) (90
parallele Aufrufer → 1 Abruf/Link) und
[test_link_jobs.py](../backend/tests/test_link_jobs.py) (15 parallele
Checks → 1 Job, `fetch_count == 1`); Lasttest 300/500 Nutzer:
`inserate_mehrfach_extern_geholt: 0`.

### 8. Quarantäne vor globalem Cache?

**Ja.** `POST /listings/ingest` validiert das HTML (≥3 Strukturmarker der
echten Detailseite + Anzeigen-Nummer aus der URL muss im HTML stehen +
Pflichtfelder Titel/Preis/Marke) und schreibt dann **ausschließlich** in
die händler-eigene Quarantäne (`store_client_listing`,
[listing_identity.py](../backend/listing_identity.py)). Global freigegeben
wird erst, wenn ein **zweiter, unabhängiger Händler** dieselben Kerndaten
einreicht (Preis ±1 %, Titel, km ±1 %, Erstzulassung, Marke) — und dann
werden die Daten der **älteren** Einreichung veröffentlicht.

### 9. Kann ein manipulierter Client gefälschte Daten einreichen?

**In die eigene Quarantäne: ja** — ein manipulierter Client kann sich
selbst gefälschte Daten vorsetzen (Schaden: nur der eigene Händler,
nachvollziehbar über `ingested_by_user/_dealer`, 24 h TTL).
**Global: nur mit Kollusion zweier Händler-Konten**, die identische
Kerndaten einreichen — Einzeltäter erreichen andere Händler nicht.
Restrisiko dokumentiert; die Beweis-Snapshots entstehen unabhängig davon
server-seitig von der echten Seite.

### 10. Welche IP sieht Kleinanzeigen?

Siehe Tabelle oben. Zusammengefasst: Standardbetrieb = **Server-IP** für
alles; Client-Modus = **Nutzer-IP** für Inseratsdaten, **Server-IP** für
Beweis-Snapshots und den resolve-Restpfad; Cache-Treffer = keine.

## Zulässigkeit / Anbieterbedingungen — Einschätzung, kein Rechtsrat

Automatisierter Abruf („Crawling/Scraping") ist laut Kleinanzeigen-
Nutzungsbedingungen ohne Zustimmung **untersagt** — das betrifft den
server-seitigen Standardmodus genauso wie den verteilten Abruf über die
Erweiterung: auch der ist ein automatisierter Abruf im Auftrag der
Plattform, nur mit anderer IP. Die Verteilung auf Nutzer-IPs ist eine
**technische** Entlastung, keine **rechtliche**. Konsequenz für den
Livegang (steht so auch in der [STAGING-CHECKLISTE](STAGING-CHECKLISTE.md)):

1. **Vor dem öffentlichen Start eine ausdrückliche Vereinbarung bzw. einen
   offiziellen API-Zugang anstreben** (analog zur mobile.de Search-API);
   bis dahin die Abrufe minimal halten (Limit 3, 1 Abruf je Inserat,
   1-Jahres-Cache — genau das belegen die Tests).
2. Die Drosselung und der Nachweis „kein Inserat doppelt" sind Argumente
   FÜR eine solche Vereinbarung, ersetzen sie aber nicht.
3. Lasttests laufen ausschließlich gegen den Mock — nie gegen den
   Anbieter (vom Skript erzwungen).
