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
  // Rollenprüfung 22.09.2026 (RP-205/RP-356): nur "/angebote/" — der
  // Abruf-Dienst liest keine Auslandsseiten (/offers/ usw.); solche Links
  // starteten automatisch und endeten nach drei Versuchen als "Technischer
  // Fehler". Der Server lehnt sie jetzt mit einer klaren Meldung ab.
  /autoscout24\.[a-z.]{2,6}\/angebote\//i,
];

export function istInseratsLink(text) {
  const t = (text || "").trim();
  if (!t || t.length > 2048) return false;      // Deckel wie im Backend
  return MUSTER.some((m) => m.test(t));
}

// Satzzeichen am Ende gehoeren nicht zur Adresse ("… schau mal: https://…!").
const URL_IM_TEXT = /https?:\/\/[^\s<>"'“”„«»]+/gi;
const ENDE_WEG = /[.,;:!?)\]}>'"“”„«»]+$/;

/**
 * Rollenprüfung 22.09.2026 (RP-409): "Teilen" aus der Kleinanzeigen- bzw.
 * mobile.de-App liefert Text MIT Link ("Schau mal: https://…"). Vorher wurde
 * der ganze Text übernommen und gestartet — der Server lehnte ihn immer mit
 * 400 ab. Liefert die erste Inserats-Adresse im Text (ohne Satzzeichen am
 * Ende) oder "" (kein Inserats-Link enthalten).
 */
export function inseratsLinkAusText(text) {
  const t = (text || "").trim();
  if (!t || t.length > 8192) return "";
  if (/^https?:\/\/\S+$/i.test(t)) return istInseratsLink(t) ? t : "";
  for (const treffer of t.match(URL_IM_TEXT) || []) {
    const kandidat = treffer.replace(ENDE_WEG, "");
    if (istInseratsLink(kandidat)) return kandidat;
  }
  return "";
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
