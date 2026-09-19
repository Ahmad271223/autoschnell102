/**
 * Preis-Eingaben aus deutscher Schreibweise in eine Zahl umrechnen.
 *
 * Gegenprüfung 12.09.2026 (schwerer Befund): Die Freigabe rechnete nur das
 * Komma um. Aus "15.000" (völlig übliche deutsche Schreibweise für
 * fünfzehntausend) wurde dadurch **15 €** — freigegeben, im unterschriebenen
 * PDF und im Kaufvorgang. Genau die gefährliche Variante rutschte
 * stillschweigend durch, während "1.250,50" wenigstens als Fehler auffiel.
 *
 * Regeln (in dieser Reihenfolge):
 *   "17.250,50" → 17250.5   Punkt = Tausender, Komma = Dezimal
 *   "17250,50"  → 17250.5   nur Komma = Dezimal
 *   "15.000"    → 15000     Punkt in Dreierblöcken = Tausender
 *   "15.5"      → 15.5      einzelner Punkt ohne Dreierblock = Dezimal
 *   "15 000"    → 15000     Leerzeichen als Tausender
 *   ""/Unsinn   → null      (der Aufrufer meldet es dem Nutzer)
 */
const NUR_TAUSENDER = /^\d{1,3}(\.\d{3})+$/;

export function preisAusText(eingabe) {
  if (eingabe === null || eingabe === undefined) return null;
  let t = String(eingabe).trim();
  if (!t) return null;
  // Währungszeichen und Leerzeichen (auch geschützte) raus.
  t = t.replace(/[€\s  ']/g, "");
  if (!t) return null;
  const hatKomma = t.includes(",");
  const hatPunkt = t.includes(".");
  if (hatKomma && hatPunkt) {
    t = t.replace(/\./g, "").replace(",", ".");
  } else if (hatKomma) {
    t = t.replace(",", ".");
  } else if (hatPunkt && NUR_TAUSENDER.test(t)) {
    t = t.replace(/\./g, "");
  }
  // Danach darf nur noch eine reine Dezimalzahl übrig sein.
  if (!/^\d+(\.\d+)?$/.test(t)) return null;
  const zahl = Number(t);
  return Number.isFinite(zahl) ? zahl : null;
}

/** Betrag deutsch anzeigen ("17.250,00 €"); leer → "—". */
export function preisText(v) {
  if (v === null || v === undefined || v === "") return "—";
  const zahl = Number(v);
  if (!Number.isFinite(zahl)) return "—";
  return new Intl.NumberFormat("de-DE", {
    style: "currency", currency: "EUR",
  }).format(zahl);
}
