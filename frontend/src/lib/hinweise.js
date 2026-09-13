/*
 * Runde 24 (11.09.2026): Server-Hinweise (data.hinweise) als Warn-Toasts —
 * jeder Text steht hoechstens einmal da.
 *
 * Anlass: Ahmad sah dieselbe gelbe Warnung ("AutoScout24 hat keine passende
 * Kategorie fuer 'Kombi' …") zweimal untereinander. Ursache: die Toasts
 * hatten keine id. Jeder weitere Lauf innerhalb der 8 s Anzeigedauer
 * stapelte denselben Text erneut — Einfuegen startet den Vergleich sofort,
 * ein danach noch geklicktes "Auslesen" (bei gecachtem Inserat ist der
 * erste Lauf dann schon fertig), der Profilwechsel oder das naechste Auto
 * derselben Kategorie liefern denselben Hinweis noch einmal.
 *
 * Feste id je Text: sonner aktualisiert einen stehenden Toast mit derselben
 * id, statt einen zweiten anzuzeigen. Hinweise des vorigen Laufs, die nicht
 * mehr gelten, schliessen wir erst mit dem neuen Ergebnis — nicht schon
 * beim Start: sonner schliesst per requestAnimationFrame und behaelt die
 * Loesch-Markierung, ein gleich danach mit derselben id gezeigter Hinweis
 * (gecachtes Ergebnis nach wenigen Millisekunden) verschwaende sonst mit.
 */

export const HINWEIS_DAUER_MS = 8000;

export const hinweisToastId = (text) => `hinweis:${text}`;

// Nicht-leere Hinweistexte ohne Dubletten, in der Reihenfolge des Servers.
export function eindeutigeHinweise(hinweise) {
  const gesehen = new Set();
  const texte = [];
  for (const h of Array.isArray(hinweise) ? hinweise : []) {
    const text = typeof h === "string" ? h.trim() : "";
    if (!text || gesehen.has(text)) continue;
    gesehen.add(text);
    texte.push(text);
  }
  return texte;
}

/**
 * Zeigt die Hinweise als Warnungen (eine je Text) und schliesst Hinweise
 * des vorigen Laufs, die jetzt nicht mehr dabei sind. Rueckgabe: die ids
 * der gezeigten Hinweise — beim naechsten Aufruf als vorherigeIds uebergeben.
 * toast wird injiziert (sonner), damit die Funktion ohne DOM testbar ist.
 */
export function hinweiseZeigen(toast, hinweise, vorherigeIds = []) {
  const texte = eindeutigeHinweise(hinweise);
  const ids = texte.map(hinweisToastId);
  for (const alt of vorherigeIds || []) {
    if (!ids.includes(alt)) toast.dismiss(alt);
  }
  texte.forEach((text, i) => {
    toast.warning(text, { id: ids[i], duration: HINWEIS_DAUER_MS });
  });
  return ids;
}
