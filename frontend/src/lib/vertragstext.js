/*
 * Runde 26 (12.09.2026, Wunsch Ahmad): Die Einstellungen hatten zwei fast
 * gleich klingende Felder — "Allgemeine Geschäftsbedingungen (AGB)" und
 * "Allgemeine Vertragsbedingungen". Ab jetzt gibt es EIN Feld.
 *
 * Beim Öffnen der Einstellungen wird ein noch vorhandener AGB-Text unten an
 * die Vertragsbedingungen gehängt; beim Speichern wird das alte Feld geleert.
 * Bestehende Verträge bleiben unverändert — sie tragen ihren eigenen Stand.
 */

/** Beide Texte zu einem zusammenführen (AGB hinten, wie im Startertext). */
export function zusammenfuehren(agb, bedingungen) {
  const a = String(agb ?? "").trim();
  const b = String(bedingungen ?? "").trim();
  if (!a) return b;
  if (!b) return a;
  if (b.includes(a)) return b;          // schon zusammengeführt: nichts doppeln
  return `${b}\n\n${a}`;
}

/** true, wenn beim Öffnen etwas zusammengeführt wurde (Hinweis anzeigen). */
export function wurdeZusammengefuehrt(agb, bedingungen) {
  const a = String(agb ?? "").trim();
  if (!a) return false;
  return !String(bedingungen ?? "").includes(a);
}
