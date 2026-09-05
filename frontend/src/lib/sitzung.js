/*
 * Wo liegt der Anmelde-Token im Browser?
 *
 * Befund 05.09.2026: Alle Tabs teilten sich EINEN Schluessel in
 * localStorage. Tab 1 als Super-Admin, Tab 2 als Firma angemeldet — und
 * schon schickte Tab 1 beim naechsten Aufruf den Firmen-Token, waehrend
 * die Oberflaeche noch "Super-Admin" zeigte. Konten vermischten sich,
 * scheinbar zufaellig, weil es erst beim naechsten API-Aufruf auffiel.
 *
 * Loesung: Jeder Tab hat seinen eigenen Token (sessionStorage gehoert
 * dem einzelnen Tab). localStorage merkt sich nur die LETZTE Anmeldung,
 * damit ein neu geoeffneter Tab dort weitermacht, wo man zuletzt war —
 * das ist der Komfort, den localStorage frueher gab, ohne die Vermischung.
 *
 *   Tab A (Admin) offen, Tab B meldet Firma an
 *     -> Tab A behaelt Admin, Tab B ist Firma, ein neuer Tab C ist Firma.
 *   Tab B meldet sich ab
 *     -> nur Tab B verliert seinen Token; die "letzte Anmeldung" wird nur
 *        geloescht, wenn sie zu genau diesem Konto gehoerte.
 *
 * Alle Zugriffe auf die drei Token-Schluessel laufen ueber diese Datei.
 * Storage kann fehlen oder gesperrt sein (privater Modus, Vorschau) —
 * deshalb ist jeder Zugriff abgesichert.
 */

export const TOKEN_APP = "ah_token";
export const TOKEN_KAEUFER = "ah_buyer_token";
export const TOKEN_FAHRER = "ah_driver_token";

function sicher(fn, sonst = null) {
  try {
    return fn();
  } catch {
    return sonst;
  }
}

/** Token dieses Tabs; fehlt er, wird die letzte Anmeldung uebernommen. */
export function tokenLesen(key = TOKEN_APP) {
  const eigener = sicher(() => window.sessionStorage.getItem(key));
  if (eigener) return eigener;
  const letzter = sicher(() => window.localStorage.getItem(key));
  if (letzter) {
    sicher(() => window.sessionStorage.setItem(key, letzter));
    return letzter;
  }
  return null;
}

/** Nach einer Anmeldung: dieser Tab UND "letzte Anmeldung". */
export function tokenSetzen(key, wert) {
  sicher(() => window.sessionStorage.setItem(key, wert));
  sicher(() => window.localStorage.setItem(key, wert));
}

/** Nach Abmeldung oder abgelaufener Sitzung: nur dieser Tab — und die
 *  "letzte Anmeldung" nur, wenn sie zu diesem Token gehoerte. */
export function tokenLoeschen(key = TOKEN_APP) {
  const eigener = sicher(() => window.sessionStorage.getItem(key));
  sicher(() => window.sessionStorage.removeItem(key));
  const letzter = sicher(() => window.localStorage.getItem(key));
  if (letzter && (!eigener || letzter === eigener)) {
    sicher(() => window.localStorage.removeItem(key));
  }
}
