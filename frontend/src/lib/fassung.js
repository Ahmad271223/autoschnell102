/*
 * Gibt es eine neuere Fassung der Oberflaeche? (Runde 31, 12.09.2026)
 *
 * Vorfall: Fahrer-App und Super-Admin standen auf "Die Seite konnte nicht
 * geladen werden". Die App wusste nicht, dass es eine neue Fassung gab —
 * sie erfuhr es erst, als sie gegen die Wand lief.
 *
 * Jede API-Antwort traegt jetzt die Kopfzeile X-AH-Fassung
 * ("<Commit-Zeit>-<Kurz-SHA>", gesetzt von deploy/rollout.sh). Die
 * Oberflaeche kennt ihren eigenen Stempel aus dem Bau. Ist der gesehene
 * ECHT neuer, wird das gemeldet — ohne eigene Abfrage, ohne Takt.
 *
 * Warum "neuer" und nicht "anders": Waehrend eines Rollouts antworten
 * abwechselnd der alte und der neue Server. Ein Vergleich auf Ungleichheit
 * wuerde bei jedem zweiten Aufruf hin- und herspringen.
 *
 * Niemals wird hier von sich aus neu geladen, waehrend jemand arbeitet.
 * Neu geladen wird nur an Stellen, an denen nichts verloren gehen kann
 * (Seitenwechsel, direkt nach der Anmeldung) oder auf Klick.
 */
import { hatUngespeichert } from "@/lib/ungespeichert";

const EIGENE = String(import.meta.env.APP_FASSUNG || "");
const MERKER = "ah_fassung_neu_geladen";

/** "1757680000-3af35e8" -> 1757680000; alles andere -> null */
export function fassungsZeit(stempel) {
  const m = /^(\d{9,12})-[0-9a-f]{4,40}$/i.exec(String(stempel || "").trim());
  return m ? Number(m[1]) : null;
}

/** Ist `gesehen` ECHT neuer als `eigene`? Ohne eigenen Stempel: nie. */
export function istNeuer(gesehen, eigene) {
  const a = fassungsZeit(eigene);
  const b = fassungsZeit(gesehen);
  return a !== null && b !== null && b > a;
}

let zustand = null; // null | { grund: "fassung" | "nachladen", fassung }
const hoerer = new Set();

function melden(neu) {
  zustand = neu;
  for (const fn of hoerer) {
    try { fn(zustand); } catch { /* ein Hoerer darf die anderen nicht stoppen */ }
  }
}

export function veralteteFassung() {
  return zustand;
}

export function fassungAbonnieren(fn) {
  hoerer.add(fn);
  return () => { hoerer.delete(fn); };
}

/** Kopfzeile einer Antwort auswerten — Erfolg wie Fehler (auch 409 traegt sie). */
export function fassungPruefen(antwort, eigene = EIGENE) {
  const kopf = antwort?.headers;
  if (!kopf) return;
  const wert = typeof kopf.get === "function" ? kopf.get("x-ah-fassung") : kopf["x-ah-fassung"];
  if (!wert || !istNeuer(wert, eigene)) return;
  // Nur vorwaerts: eine aeltere Antwort (anderer Server im Rollout) aendert
  // eine schon erkannte neuere Fassung nicht.
  if (zustand?.grund === "fassung" && !istNeuer(wert, zustand.fassung)) return;
  melden({ grund: "fassung", fassung: String(wert) });
}

/** Ein Seitenteil liess sich nicht nachladen — die laufende Fassung ist alt. */
export function nachladenGescheitert() {
  if (zustand) return;
  melden({ grund: "nachladen", fassung: "" });
}

/** An einer axios-Verbindung mithoeren (api, driverApi, buyerApi). */
export function fassungMithoeren(instanz) {
  instanz.interceptors.response.use(
    (r) => { fassungPruefen(r); return r; },
    (e) => { fassungPruefen(e?.response); return Promise.reject(e); },
  );
}

/**
 * Die neue Fassung laden — nur, wenn dabei nichts verloren geht, und je
 * Fassung hoechstens einmal (landet das Neuladen im Rollout noch auf dem
 * alten Server, soll es sich nicht wiederholen). true = die Seite laedt neu.
 */
export function neueFassungLaden(ziel) {
  if (zustand?.grund !== "fassung" || hatUngespeichert()) return false;
  try {
    if (window.sessionStorage.getItem(MERKER) === zustand.fassung) return false;
    window.sessionStorage.setItem(MERKER, zustand.fassung);
  } catch {
    return false;
  }
  window.location.assign(ziel);
  return true;
}

/** Nur fuer Tests. */
export function _zuruecksetzen() {
  zustand = null;
  hoerer.clear();
}
