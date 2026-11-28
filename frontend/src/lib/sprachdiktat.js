/**
 * Sprachdiktat — nur in der iPhone-App (Wunsch Ahmad 24.11.2026).
 *
 * Die App-Hülle (ios-app/, Capacitor) bringt das native Plugin "SprachDiktat"
 * mit (WhisperKit, Erkennung auf dem iPhone, kein Ton zum Server). Im Browser,
 * in der Android-TWA und in alten App-Ständen ohne Plugin gibt es kein
 * window.Capacitor bzw. kein Plugin — dann zeigt DiktatKnopf gar nichts an.
 */

const PLUGIN = "SprachDiktat";
const PRUEF_ZEIT_MS = 3000;

function capacitor() {
  if (typeof window === "undefined") return null;
  const c = window.Capacitor;
  if (!c || typeof c.nativePromise !== "function") return null;
  if (typeof c.getPlatform === "function" && c.getPlatform() !== "ios") return null;
  return c;
}

export function diktatAufruf(methode, optionen = {}) {
  const c = capacitor();
  if (!c) return Promise.reject(new Error("Diktat gibt es nur in der iPhone-App."));
  return c.nativePromise(PLUGIN, methode, optionen);
}

let verfuegbar = null;

/** true, wenn die App-Hülle das Plugin kennt. Ein App-Stand ohne Plugin
 *  antwortet auf unbekannte Plugins nicht immer — daher mit Zeitgrenze. */
export function diktatVerfuegbar() {
  if (!capacitor()) return Promise.resolve(false);
  if (!verfuegbar) {
    verfuegbar = Promise.race([
      diktatAufruf("status").then(() => true, () => false),
      new Promise((r) => setTimeout(() => r(false), PRUEF_ZEIT_MS)),
    ]);
  }
  return verfuegbar;
}

/** Für Tests: Merker zurücksetzen. */
export function _diktatMerkerLeeren() {
  verfuegbar = null;
}

/**
 * Erkannten Text an den vorhandenen anhängen (neuer Satz in neuer Zeile,
 * sonst mit Leerzeichen) und auf max Zeichen kürzen.
 */
export function textAnhaengen(alt, neu, max) {
  const a = String(alt || "");
  const n = String(neu || "").trim();
  if (!n) return a;
  let ergebnis;
  if (!a.trim()) ergebnis = n;
  else if (/[.!?]\s*$/.test(a)) ergebnis = `${a.replace(/\s+$/, "")}\n${n}`;
  else ergebnis = `${a.replace(/\s+$/, "")} ${n}`;
  return max ? ergebnis.slice(0, max) : ergebnis;
}
