/*
 * Sicherung des Abhol-Protokolls im Tab (sessionStorage) — Fahrer-App.
 *
 * Rollenprüfung 22.09.2026 (RP-546): Unterschriften liegen bis zum Abschluss
 * NUR im Speicher der Seite (der Autosave schickt sie bewusst nicht mit), und
 * getippte Antworten erst nach dem nächsten Autosave beim Server. Endete die
 * Sitzung mitten vor Ort (Ablauf, Anmeldung auf einem anderen Handy), leitete
 * der 401-Abfänger zur Anmeldung um — beides war verloren, der Verkäufer
 * musste neu unterschreiben. Jetzt wird der offene Stand je Fahrt im Tab
 * gesichert und nach der Neuanmeldung wiederhergestellt:
 *   - Formular: nur solange das Protokoll noch Entwurf ist (dann über die
 *     Drei-Wege-Zusammenführung aus protokollEntwurf.js);
 *   - Unterschriften: nur, wenn Freigabe-Stand, Preis und Vermerk noch genau
 *     die sind, unter denen unterschrieben wurde (sonst gelten sie nicht).
 * Auch RP-062/RP-161 nutzt das: nach "Fahrt erneut annehmen" auf der
 * Startseite ist die Eingabe beim Zurückkommen wieder da.
 */
import { lesen, schreiben, sitzungsSpeicher } from "@/lib/speicher";

const schluessel = (id) => `ah_protokoll_sicherung_${id}`;
// Älteres als das ist kein "gerade eben unterbrochen" mehr.
export const SICHERUNG_HOECHSTENS_MS = 12 * 60 * 60 * 1000;

/** Kennung des Standes, unter dem unterschrieben wird (wie in Protokoll.jsx). */
export function freigabeKennung(protokoll) {
  const p = protokoll || {};
  return [p.freigabe_stand || "", p.neuer_preis ?? "", p.preis_notiz || ""].join("|");
}

export function sicherungLesen(id, jetzt = Date.now()) {
  try {
    const roh = lesen(sitzungsSpeicher(), schluessel(id), null);
    if (!roh) return null;
    const s = JSON.parse(roh);
    if (!s || s.v !== 1 || typeof s.zeit !== "number" || jetzt - s.zeit > SICHERUNG_HOECHSTENS_MS) {
      sicherungLoeschen(id);
      return null;
    }
    return s;
  } catch {
    return null;
  }
}

/** Stand sichern; passt er nicht in den Speicher, wenigstens ohne Unterschriften. */
export function sicherungSchreiben(id, stand, jetzt = Date.now()) {
  const speicher = sitzungsSpeicher();
  const s = { v: 1, zeit: jetzt, ...stand };
  if (schreiben(speicher, schluessel(id), JSON.stringify(s))) return true;
  return schreiben(speicher, schluessel(id),
                   JSON.stringify({ ...s, sigDriver: null, sigSeller: null }));
}

export function sicherungLoeschen(id) {
  try { sitzungsSpeicher()?.removeItem(schluessel(id)); } catch { /* egal */ }
}
