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
 * Runde 11 (06.09.2026): Ein Tab, der sich abgemeldet hat oder dessen
 * Sitzung beendet wurde (401), uebernimmt die "letzte Anmeldung" NICHT
 * mehr. Vorher: Tab A meldet den Admin ab, in localStorage liegt noch der
 * Firmen-Token aus Tab B — beim naechsten Laden war Tab A ploetzlich das
 * Firmenkonto. Der Tab merkt sich "abgemeldet", bis in ihm wieder jemand
 * anmeldet. Ein NEUER Tab macht weiterhin bei der letzten Anmeldung weiter.
 *
 * Alle Zugriffe auf die Token-Schluessel laufen ueber diese Datei.
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

/** Marker "dieser Tab hat sich abgemeldet" — je Token-Schluessel. */
function abgemeldetKey(key) {
  return `${key}_abgemeldet`;
}

/** Token dieses Tabs; fehlt er, wird die letzte Anmeldung uebernommen —
 *  ausser dieser Tab hat sich abgemeldet oder wurde abgemeldet. */
export function tokenLesen(key = TOKEN_APP) {
  const eigener = sicher(() => window.sessionStorage.getItem(key));
  if (eigener) return eigener;
  if (sicher(() => window.sessionStorage.getItem(abgemeldetKey(key)))) return null;
  const letzter = sicher(() => window.localStorage.getItem(key));
  if (letzter) {
    sicher(() => window.sessionStorage.setItem(key, letzter));
    return letzter;
  }
  return null;
}

/** Welche Anmeldung zuletzt benutzt wurde (TOKEN_APP, TOKEN_FAHRER oder
 *  TOKEN_KAEUFER) — nur die Art, kein Token. Der Einstieg der installierten
 *  App (/start, lib/appstart.js) oeffnet danach die passende Anmeldeseite. */
export const LETZTE_ANMELDUNG = "ah_letzte_anmeldung";

export function letzteAnmeldung() {
  return sicher(() => window.localStorage.getItem(LETZTE_ANMELDUNG));
}

/** Anmeldeseite von Fahrer/Marktplatz aufgerufen, aber hier noch nie
 *  angemeldet: die Art trotzdem vormerken — wer von dort aus die App
 *  installiert, landet beim ersten Start auf der richtigen Anmeldung.
 *  Eine echte fruehere Anmeldung wird nie ueberschrieben. */
export function anmeldeartVormerken(key) {
  if (letzteAnmeldung()) return;
  sicher(() => window.localStorage.setItem(LETZTE_ANMELDUNG, key));
}

/** Nach einer Anmeldung: dieser Tab UND "letzte Anmeldung". */
export function tokenSetzen(key, wert) {
  sicher(() => window.sessionStorage.removeItem(abgemeldetKey(key)));
  sicher(() => window.sessionStorage.setItem(key, wert));
  sicher(() => window.localStorage.setItem(key, wert));
  sicher(() => window.localStorage.setItem(LETZTE_ANMELDUNG, key));
}

/** Nach Abmeldung oder abgelaufener Sitzung: nur dieser Tab — und die
 *  "letzte Anmeldung" nur, wenn sie zu diesem Token gehoerte. Der Tab
 *  uebernimmt danach keine fremde letzte Anmeldung mehr. */
export function tokenLoeschen(key = TOKEN_APP) {
  const eigener = sicher(() => window.sessionStorage.getItem(key));
  sicher(() => window.sessionStorage.removeItem(key));
  sicher(() => window.sessionStorage.setItem(abgemeldetKey(key), "1"));
  const letzter = sicher(() => window.localStorage.getItem(key));
  if (letzter && (!eigener || letzter === eigener)) {
    sicher(() => window.localStorage.removeItem(key));
  }
}
