# Technischer Plan: B2B-Händler- & Wiederverkaufsmodul

Stand: 05.08.2026 · Basis: aktueller Code (FastAPI + MongoDB + React)

---

## 0. Leitplanken

- **Händleraccount kostenlos.** Einnahmen: (a) Sucher-Abos (bestehendes Abo-System, wird pro Sucher statt pro Händler gerechnet), (b) Verkaufspakete nach *neu veröffentlichten* Fahrzeugen pro Abrechnungsmonat.
- **Nur der Händler-Hauptaccount** entscheidet über Weiterverkauf, Sichtbarkeit, Pakete, Mitarbeiter.
- Kein Bruch am Bestehenden: Phase 1 läuft komplett ohne Änderung am heutigen Abo-Modell.
- Zahlungen: Stripe ist lokal nicht verfügbar → Kontingente/Pakete werden vollständig gebaut, die Bezahlung läuft zunächst wie beim Sucher-Abo manuell über den Admin (Paket im Admin-Bereich zuweisen). Stripe-Anbindung ist ein austauschbarer Baustein.

---

## 1. Ist-Analyse (was der Code heute kann)

| Bereich | Heute | Relevanz |
|---|---|---|
| `users` | Rollen: `admin`, `dealer`; 1 User = 1 Händler (dealer_id) | Wird um `sucher` (Phase 2) und `b2b_buyer` (Phase 3) erweitert |
| `vehicles` | Pro Händler; `data` = komplette Fahrzeugdaten aus Vergleich (Marke, Modell, km, Ausstattung, Bilder-URLs, …); `status` = Freitext („Vertrag erstellt", „Termin erstellt") | Basis der Fahrzeugakte; `status` wird durch echte Statusmaschine ersetzt |
| `generated_pdfs` | Kaufvertrag inkl. `contract_data` (Preis, Verkäufer, Schäden mit Koordinaten), `vehicle_image_urls` Snapshot | Quelle für „Beschaffung/Kauf" in der Akte + Schäden im Inserat |
| `appointments` | Status: `offen`, …, `abgeholt`, `nicht abgeholt`, `storniert`; `status_changed_at`; Fahrer-Zuordnung | Trigger „Abgeholt → Händler-Entscheidung" existiert schon |
| Fahrer-App | Fahrer markiert `abgeholt` / `nicht abgeholt` (`POST /driver/appointments/{id}/status`) | Andockpunkt für digitale Abweichungserfassung |
| `cleanup_service` | Löscht Fotos/Assets **7 Tage** nach `abgeholt`, 14 Tage nach `nicht abgeholt` | ⚠ Konflikt mit 50-Tage-Bestand → Regel wird angepasst (s. 4.4) |
| `subscriptions` | Pro dealer_id; Pläne monthly/yearly/lifetime/trial | Bleibt für Sucher-Abo; neue Collection für Verkaufspakete |
| `activity_logs` / `error_logs` | Audit + Fehler seit 08/2026 | „Händler sieht alles" speist sich hieraus |

---

## 2. Fahrzeug-Lebenszyklus (Statusmaschine)

Neues Feld `vehicles.lifecycle` (ersetzt den Freitext-`status` schrittweise; alter Wert bleibt als `legacy_status` erhalten):

```
gefunden → verglichen → besichtigung → verhandlung → vertrag_erstellt
        → gekauft → abholung_geplant → abgeholt
                                        ├─ bestand            (nur gespeichert, 50 Tage)
                                        ├─ verkaufsentwurf    → veroeffentlicht → reserviert → verkauft
                                        └─ geloescht
   (nicht_abgeholt / storniert als Seitenausgänge)
```

Übergänge werden serverseitig validiert (erlaubte Folge-Status je Status, Rolle geprüft). Jede Änderung → `activity_logs` (`fahrzeug.status.<neu>`).

**Automatische Trigger (bestehende Flows, keine Doppelpflege):**
- Vergleich angelegt → `verglichen`
- Vertrag erstellt → `vertrag_erstellt`, mit Kaufpreis → `gekauft`
- Termin erstellt → `abholung_geplant`
- Fahrer meldet `abgeholt` → `abgeholt` + Händler-Entscheidung fällig

---

## 3. Datenmodell (neue/erweiterte Collections)

### 3.1 `vehicles` (erweitert)
```
lifecycle: str                      # s.o.
lifecycle_changed_at: iso
bestand: {
  saved_at: iso                     # Start der 50-Tage-Frist
  expires_at: iso                   # saved_at + 50d (None wenn veroeffentlicht/verkauft)
  location: str                     # Standort/Platz
  notes: str                        # interne Notizen
  costs: [ {label, amount} ]        # Transport, Aufbereitung, Reparatur, TÜV, Gebühren …
}
purchase_price: float               # existiert schon (vom Vertrag)
```

### 3.2 `pickup_reports` (NEU — digitale Abweichungen)
Vom Fahrer/Sucher bei der Abholung erfasst (Fahrer-App, Schritt vor „abgeholt"-Meldung):
```
{ id, appointment_id, vehicle_id, dealer_id, driver_account_id,
  mileage_at_pickup: int,
  keys_count: int,
  fuel_level: "leer|1/4|1/2|3/4|voll",
  deviations: [ {
    id, field: "mileage|keys|tires|damage|warning_light|other",
    label: str,                     # "Kratzer hinten rechts"
    expected: str, actual: str,     # "84.000 km" / "85.120 km"
    photo_b64: str?,                # Foto (komprimiert), optional
    note: str
  } ],
  created_at }
```
→ Händler-Ansicht „⚠ N Abweichungen"; Diff-Seite Einkauf vs. Abholung; Übernahme einzelner Werte per Klick (schreibt in `vehicles.data` + Log).

### 3.3 `resale_listings` (NEU — Verkaufsinserate)
```
{ id, dealer_id, vehicle_id,
  status: "entwurf|veroeffentlicht|reserviert|verkauft|zurueckgezogen",
  title: str,                       # auto-generiert, editierbar
  description: str,                 # auto-generiert aus data + Abweichungen
  data: {...},                      # Kopie der (korrigierten) Fahrzeugdaten
  known_defects: [str],             # aus Schäden + Abweichungen
  photos: { mode: "einkauf|neu|beide", urls: [..], uploaded_b64: [..] },
  prices: { public: float, b2b: float?, network: float? },
  purchase_price: float, costs_total: float, margin: float,   # berechnet
  visibility: "public|private",     # erbt Default vom Händlerprofil
  published_at: iso?,               # zählt fürs Monatskontingent
  reserved_for: str?, sold_at: iso?, sold_price: float?,
  created_by, created_at, updated_at }
```

### 3.4 `sale_plans` + Händler-Paket (Phase 2)
```
dealers.sale_plan: { tier: "s5|s10|s20|s30|s40|enterprise",
                     quota: int, period_start: iso,   # rollierender Monat
                     custom_quota: int? }             # Enterprise
```
Preise als Konstante im Backend (`SALE_PLANS`): 5→10,00 € · 10→19,99 € · 20→28,99 € · 30→37,99 € · 40→45,00 € · Enterprise→Anfrage.
**Kontingent-Logik:** Verbrauch = `count(resale_listings, dealer_id, published_at ∈ aktueller Abrechnungszeitraum)`. Bestehende Online-Inserate zählen im Folgemonat NICHT erneut. Kontingent voll → 402 mit Upgrade-Hinweis (kein Einzelkauf).

### 3.5 Sucher-Unteraccounts (Phase 2)
`users` erweitert: `role: "sucher"`, `dealer_id` (des Händlers), `permissions: {…}` (Default laut Rechte-Matrix, s. 6), `created_by`. Sucher-Abo: `subscriptions.subject_user_id` (statt nur dealer_id) — Migration: bestehende Abos bleiben dem Händler-Account zugeordnet, der damit selbst als „erster Sucher" suchen darf (kein Bestandskunde verliert etwas).

### 3.6 Marktplatz (Phase 3)
```
dealer_profiles:  { dealer_id, public: bool, slug, description, verified,
                    member_since, followers: [user_id] }
dealer_invites:   { id, dealer_id, token, expires_at, max_uses, used_count,
                    used_by: [user_id], created_at }        # Default 1 Nutzung
users (neu):      role "b2b_buyer" (registriert sich frei oder per Einladung)
network_members:  { dealer_id, buyer_user_id, via_invite_id, created_at }
listing_interest: { id, listing_id, dealer_id, buyer_user_id,
                    offer: float, message,
                    status: "offen|akzeptiert|abgelehnt|gegenangebot",
                    counter_offer: float?, history: [...], created_at }
```

---

## 4. Backend-Endpoints

### Phase 1
| Endpoint | Zweck |
|---|---|
| `GET  /api/vehicles/{id}/akte` | Fahrzeugakte: aggregiert Inserat, Vergleiche, Vertrag, Termin, Abholbericht, Bestand, Verkauf (aus bestehenden Collections — keine Datendopplung) |
| `POST /api/driver/appointments/{id}/report` | Fahrer: Abholbericht + Abweichungen (+ Fotos) einreichen; danach erst `abgeholt` |
| `GET  /api/appointments/{id}/report` | Händler liest Bericht |
| `POST /api/vehicles/{id}/decision` | Nach Abholung: `bestand` / `verkaufsentwurf` / `loeschen` (nur Händler-Rolle; Sicherheitsabfrage im Frontend) |
| `POST /api/vehicles/{id}/apply-deviations` | Ausgewählte Abweichungen in Fahrzeugdaten übernehmen (Diff-Seite) |
| `PUT  /api/vehicles/{id}/bestand` | Standort, Notizen, Kosten pflegen |
| `POST /api/resale/draft/{vehicle_id}` | Inseratsentwurf generieren (Titel, Beschreibung, Ausstattung, Fotos, bekannte Mängel, Abweichungen eingearbeitet) |
| `GET/PUT /api/resale/{id}` | Entwurf lesen/bearbeiten (inkl. Foto-Modus, Preise, Kosten → Marge live) |
| `POST /api/resale/{id}/publish` | Veröffentlichen (Phase 1: nur intern sichtbar; Kontingent-Check greift ab Phase 2) |
| `POST /api/resale/{id}/status` | reserviert / verkauft / zurückziehen |

### Phase 2
| Endpoint | Zweck |
|---|---|
| `POST/GET/PUT/DELETE /api/dealer/sucher` | Sucher-CRUD (nur Händler), inkl. Rechte, Passwort-Reset |
| `GET /api/dealer/sucher/{id}/aktivitaet` | Suchvorgänge, Vergleiche, Käufe je Sucher (aus activity_logs/vehicles) |
| `GET /api/dealer/sale-plan` · `POST …/upgrade-request` | Kontingent-Stand (X/Y, Zeitraum) + Upgrade-Wunsch (landet beim Admin) |
| `Admin: PUT /api/admin/dealers/{id}/sale-plan` | Paket zuweisen/ändern (bis Stripe existiert) |
| Anpassung `require_active_sub` | Suche/Vergleich prüft Abo des Suchers; Händler-Kernfunktionen (Bestand, Verkauf) sind abo-frei |

### Phase 3
| Endpoint | Zweck |
|---|---|
| `GET /api/marktplatz/haendler` + Filter | Öffentliche Händlersuche |
| `GET /api/marktplatz/haendler/{slug}` | Profil + veröffentlichte Fahrzeuge (Preis je nach Betrachter: public/b2b/network) |
| `POST /api/dealer/invites` · `POST /api/invites/{token}/redeem` | Einladungslinks (Gültigkeit 24h/7d/30d, Nutzungen 1/5/10, Default 1) |
| `POST /api/listings/{id}/interesse` | Zwischenhändler: Interesse + Angebot |
| `POST /api/interesse/{id}/antwort` | Händler: akzeptieren / ablehnen / Gegenangebot |
| `GET /api/buyer/favoriten` etc. | Favoriten, Verlauf |

---

## 5. Frontend-Screens

### Phase 1 (Händler-App)
1. **Termine:** Nach „abgeholt" → Dialog „Was möchtest du mit dem Fahrzeug machen?" (Speichern / Speichern & weiterverkaufen / Löschen mit Sicherheitsabfrage). Badge „⚠ N Abweichungen".
2. **Fahrzeugakte** (`/app/fahrzeuge/:id`): Tabs Beschaffung · Bewertung · Kauf · Abholung · Bestand · Verkauf. Countdown „wird in N Tagen gelöscht" (ab Tag 40 gelb, ab Tag 47 rot).
3. **Diff-Seite:** Tabelle „Beim Einkauf | Bei Abholung" mit Checkboxen → „Änderungen übernehmen".
4. **Inserats-Editor:** vorausgefüllter Entwurf, Foto-Modus (Einkauf / neu / beide), Preisfelder mit **Margen-Rechner** (Einkauf + Kosten vs. Verkaufspreis), „Direkt veröffentlichen" / „Bearbeiten".
5. **Bestand** (`/app/bestand`): Kacheln mit Lifecycle-Filter (gekauft / zum Verkauf / reserviert / nur intern).

### Phase 1 (Fahrer-App)
6. **Abhol-Check:** vor „Abgeholt"-Button: km-Stand, Schlüsselzahl, Tankstand, Abweichungen (+ Foto) — speist `pickup_reports` und das (bereits neue) Abholprotokoll-PDF.

### Phase 2
7. **Mitarbeiter/Sucher-Verwaltung** + Sucher-Statistiken im Dashboard („Sucher 2: 11 Käufe").
8. **Dashboard-Kacheln:** Heute (Suchvorgänge, Besichtigungen, Käufe, Abholungen, Abweichungen) · Bestand · Weiterverkaufsplan „13/20 veröffentlicht · Zeitraum 01.–31.08. · Paket erhöhen".

### Phase 3
9. **Händler entdecken** (Suche + Filter), **Händlerprofil** (öffentlich/privat-Schalter, Einladungslinks-Verwaltung), **Interessenten-Postfach** (Angebot/Gegenangebot), **Zwischenhändler-Ansicht** (Marktplatz, Favoriten).

---

## 6. Rechte-Matrix (Kurzform)

| Aktion | Händler | Sucher | Fahrer | Zwischenhändler |
|---|:-:|:-:|:-:|:-:|
| Suchen/Vergleichen/Vertrag/Kauf markieren | ✔ | ✔ (mit Abo) | — | — |
| Abholung planen/dokumentieren | ✔ | ✔ | ✔ (nur zugewiesene) | — |
| Abweichungen erfassen | ✔ | ✔ | ✔ | — |
| Nach-Abholung-Entscheidung, Bestand, Löschen | ✔ | — | — | — |
| Inserat erstellen/veröffentlichen/Preise | ✔ | — | — | — |
| Profil/Pakete/Rechnungen/Sucher verwalten | ✔ | — | — | — |
| Marktplatz ansehen, Interesse senden | ✔ | — | — | ✔ |

---

## 7. Aufbewahrung & Cleanup (Konfliktlösung)

Heute löscht der Cleanup-Job Fotos 7 Tage nach „abgeholt". Neu:
- Solange **keine** Händler-Entscheidung getroffen wurde: bisherige Regel bleibt (7/14 Tage) — verhindert Datenmüll.
- Entscheidung **„Speichern"**: Frist = `saved_at + 50 Tage`; Warnhinweise im UI ab Tag 40/47; Cleanup löscht danach Fahrzeugdaten + Fotos (Akte-Einträge zu Vertrag/PDF bleiben, die haben eigene Regeln).
- Entscheidung **„Weiterverkaufen"** (Entwurf oder veröffentlicht): keine automatische Löschung, solange Inserat aktiv; nach „verkauft"/„zurückgezogen" beginnt die 50-Tage-Frist neu.
- Entscheidung **„Löschen"**: sofort (mit Sicherheitsabfrage), Audit-Log-Eintrag.

---

## 8. Reihenfolge & Aufwand (grob)

| Schritt | Inhalt | Aufwand |
|---|---|---|
| 1.1 | Statusmaschine + Trigger + Migration `legacy_status` | S |
| 1.2 | Abhol-Check Fahrer-App + `pickup_reports` + Protokoll-PDF-Anbindung | M |
| 1.3 | Nach-Abholung-Dialog + Bestand + 50-Tage-Logik + Cleanup-Umbau | M |
| 1.4 | Fahrzeugakte (Aggregat + UI) | M |
| 1.5 | Diff-Seite + Übernahme | S–M |
| 1.6 | Inseratsentwurf + Editor + Margen-Rechner + interner Publish | M–L |
| 2.1 | Sucher-Rolle + Rechte + Verwaltung + Abo-Umhängung | L |
| 2.2 | Verkaufspakete + Kontingent + Dashboard | M |
| 3.x | Marktplatz, Invites, Zwischenhändler, Interessenten | L |

(S = überschaubar, M = mittel, L = groß — Phase 1 gesamt ist in wenigen Arbeitssitzungen machbar, Phase 2/3 jeweils vergleichbar.)

---

## 9. Beschlossene Entscheidungen (05.08.2026)

1. **Abo-Umhängung:** Bestehende Händler behalten ihr Abo und gelten selbst als „erster Sucher". ✔
2. **Fotos:** KEINE Base64-Endarchitektur. Storage-Abstraktion ab Tag 1 (S3-kompatibles Interface); lokal Datei-Backend, per env auf MinIO/S3/R2 umschaltbar. ✔
3. **Abrechnungszeitraum:** Rollierend ab Buchungsdatum (z.B. 17.08.–16.09.). ✔
4. **Enterprise:** „Enterprise anfragen"-Button → Admin erhält Händler-ID, Verbrauch, Wunschvolumen, Kontaktdaten. ✔
5. **Publish/Kontingent:** Neuer Status `verkaufsbereit` zwischen Entwurf und Veröffentlichung. Solange kein Marktplatz existiert, gibt es kein „veröffentlicht" und KEIN Kontingentverbrauch. Kontingent zählt erst, wenn ein Fahrzeug tatsächlich für andere erreichbar ist — und pro Inserat nur EINMAL je Abrechnungszeitraum (Entwürfe nie; Zurückziehen + Reaktivieren im selben Zeitraum zählt nicht erneut → `counted_periods` am Inserat). ✔

**Weitere Beschlüsse:**
- **50-Tage-Regel:** Es wird NICHT das komplette Fahrzeug gelöscht — nur Fotos und temporäre Verkaufsdaten. Kaufvertrag, Historie, Einkaufspreis, Abholbericht und Audit-Logs bleiben dauerhaft erhalten (Fahrzeug erhält Status `archiviert`).
- **`resale_listings.data` als Kopie** bestätigt (spätere Akte-Änderungen dürfen veröffentlichte Inserate nicht verändern).
- **`pickup_reports` unveränderbar** nach endgültiger Bestätigung; Korrekturen nur als neue Version mit Verweis (`replaces_id`) + Audit-Eintrag.
- **Zwischenhändler** = eigene Rolle `b2b_buyer` (keine Händlerrolle). ✔
- **Zwei Bestände:** `source: "plattform"` (über Sucher/System gekauft) und `source: "manuell"` (Händler trägt vorhandene Fahrzeuge selbst ein — einmalige Dateneingabe, danach identische Funktionen: Bestand, B2B-Verkauf, Netzwerk, Marge, Inserate).
