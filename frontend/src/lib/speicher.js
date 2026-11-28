/**
 * Sicherer Zugriff auf den Browser-Speicher (Pruefbericht 20.09.2026, B1/B4).
 *
 * Safari/iOS mit blockierten Website-Daten und strenge Firmenrichtlinien
 * ("alle Cookies blockieren") lassen schon den ZUGRIFF auf
 * `window.localStorage` bzw. `window.sessionStorage` einen SecurityError
 * werfen — nicht erst getItem. Zwei Stellen taten das ungeschuetzt:
 *
 *   ThemeToggle  auf Modulebene, BEVOR React startet -> komplett weisse
 *                Seite, keine Fehlergrenze kann greifen
 *   Vergleich    in den useState-Initialisierern, also mitten im Rendern ->
 *                die Kernfunktion des Suchers war dauerhaft unbenutzbar
 *
 * Diese beiden Funktionen liefern den Speicher oder null — sie werfen NIE.
 * Wer sie benutzt, muss mit null umgehen koennen; die vorhandenen
 * Hilfsfunktionen (vergleichSpeicher.js u.a.) tun das bereits.
 */

function holen(art) {
  try {
    if (typeof window === "undefined") return null;
    const speicher = window[art];          // schon DIESER Zugriff kann werfen
    if (!speicher) return null;
    return speicher;
  } catch {
    return null;
  }
}

/** localStorage oder null. */
export function lokalerSpeicher() {
  return holen("localStorage");
}

/** sessionStorage oder null. */
export function sitzungsSpeicher() {
  return holen("sessionStorage");
}

/** Einen Wert lesen — nie werfend, im Zweifel `ersatz`. */
export function lesen(speicher, schluessel, ersatz = null) {
  try {
    const wert = speicher?.getItem(schluessel);
    return wert === null || wert === undefined ? ersatz : wert;
  } catch {
    return ersatz;
  }
}

/** Einen Wert schreiben — nie werfend. true, wenn es geklappt hat. */
export function schreiben(speicher, schluessel, wert) {
  try {
    if (!speicher) return false;
    speicher.setItem(schluessel, wert);
    return true;
  } catch {
    return false;
  }
}
