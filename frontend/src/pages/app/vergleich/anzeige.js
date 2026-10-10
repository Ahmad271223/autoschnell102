// Kleine reine Helfer der Vergleichsseite (08.10.2026 aus Vergleich.jsx herausgezogen — die Seite war zu groß;
// Vergleich.jsx exportiert sie weiter, damit bestehende Importe gelten).

// Pruefbericht 20.09.2026 (A-03/V5): Stand der Inseratsdaten zeigen — aus dem
// gemeinsamen Zwischenspeicher koennen Preis und km bis zu 14 Tage alt sein.
// Aelter als 24 Stunden wird hervorgehoben.
export function datenStand(result, jetzt = Date.now()) {
  const roh = result?.abgerufen_am;
  if (!roh) return null;
  const t = new Date(roh).getTime();
  if (!Number.isFinite(t)) return null;
  const stunden = (jetzt - t) / 3600000;
  const datum = new Date(t).toLocaleString("de-DE", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  if (stunden < 1) return { text: "Daten eben abgerufen", alt: false };
  return { text: `Daten vom ${datum}${stunden >= 24 ? " — Preis/km ggf. veraltet" : ""}`, alt: stunden >= 24 };
}

// Rollenprüfung 22.09.2026 (RP-207/RP-358): Der LIVE-Zähler zählt je
// Inserats-Schlüssel (cache_key = "quelle:id"). Gefragt wurde mit ad_id —
// bei AutoScout24 ist das die Anzeigen-Nummer des Anbieters (uniqueRef), die
// von der ID in der Adresse abweichen kann; der Zähler stand dann immer auf 0.
// Jetzt zählt der Schlüssel, unter dem der Vergleich gespeichert wurde.
export function liveZaehlerPfad(r) {
  const key = r?.cache_key || "";
  const i = key.indexOf(":");
  if (i > 0 && i < key.length - 1) {
    return `/mobile/live-counter/${encodeURIComponent(key.slice(i + 1))}`
      + `?quelle=${encodeURIComponent(key.slice(0, i))}`;
  }
  if (!r?.ad_id) return null;
  return `/mobile/live-counter/${encodeURIComponent(r.ad_id)}?quelle=${encodeURIComponent(r.source || "")}`;
}

// Runde 22 (11.09.2026): Eintraege fuer filterOeffnen aus den Ergebnisdaten
// und den Portal-Toggles — ein Ort fuer "Filter öffnen", die Einzel-Knoepfe
// und das automatische Oeffnen nach dem Auslesen.
export function filterEintraege(data, { mobile = true, autoscout = true } = {}) {
  return [
    mobile    && data?.search_url    && { url: data.search_url,    name: "mobileFilterWindow",    label: "mobile.de" },
    autoscout && data?.autoscout_url && { url: data.autoscout_url, name: "autoscoutFilterWindow", label: "AutoScout24" },
  ].filter(Boolean);
}

/** Prüfbericht 20.09. U-82: der Server meldet bereits_vorhanden, wenn der
 *  Vertrag schon angelegt war (Doppelklick, zweiter Tab) — dann nicht
 *  "PDF erstellt" behaupten. */
export function vertragErstelltMeldung(c) {
  if (c?.bereits_vorhanden) return "Dieser Vertrag war schon angelegt — es wurde kein neuer erstellt";
  if (c?.appointment_id) return "PDF erstellt – Termin automatisch im Terminplaner angelegt";
  return "PDF erstellt";
}

/** U-15: Hinweis, wenn während eines laufenden Vergleichs ein neuer Link kommt. */
export const VERGLEICH_LAEUFT_HINWEIS =
  "Es läuft noch ein Vergleich – mit dem X abbrechen, dann den neuen Link einfügen.";
