/**
 * Erkennt, ob ein Text ein INSERATS-Link ist (Kleinanzeigen, mobile.de,
 * AutoScout24) — bewusst nur Detailseiten, keine Suchergebnis-Seiten.
 *
 * Wird an zwei Stellen gebraucht (18.09.2026, Wunsch Ahmad "beim Reinklicken
 * soll der kopierte Link automatisch drinstehen"): beim Einfügen mit Strg+V
 * und beim Klick ins Feld, wenn der Browser die Zwischenablage hergibt.
 */
const MUSTER = [
  /kleinanzeigen\.de\/s-anzeige\//i,
  /mobile\.de\/(?:[^\s]*\bauto-inserat\/|fahrzeuge\/details\.html\?)/i,
  /autoscout24\.[a-z.]{2,6}\/(?:angebote|offers)\//i,
];

export function istInseratsLink(text) {
  const t = (text || "").trim();
  if (!t || t.length > 2048) return false;      // Deckel wie im Backend
  return MUSTER.some((m) => m.test(t));
}

/**
 * Liest die Zwischenablage, wenn der Browser es erlaubt. Liefert den Text
 * oder "" — wirft nie. `false` als zweiter Rückgabewert heißt: Der Browser
 * kann/darf nicht (dann gar nicht mehr fragen, sonst kommt bei jedem Klick
 * eine Abfrage).
 */
export async function zwischenablageLesen() {
  if (typeof navigator === "undefined" || !navigator.clipboard?.readText) {
    return { text: "", moeglich: false };
  }
  try {
    const text = (await navigator.clipboard.readText()) || "";
    return { text: text.trim(), moeglich: true };
  } catch {
    // Keine Erlaubnis, kein sicherer Kontext, Firefox: normal weiterarbeiten.
    return { text: "", moeglich: false };
  }
}
