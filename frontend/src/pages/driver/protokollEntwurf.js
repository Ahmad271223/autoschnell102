/*
 * Entwurf des Abhol-Protokolls in der Fahrer-App (Protokoll.jsx) —
 * reine Hilfsfunktionen, damit sie ohne Oberfläche testbar sind.
 *
 * Rollenprüfung 22.09.2026:
 *  RP-060/RP-159  "15.000" wurde als Preisvorschlag zu 15 € (Number() mit
 *                 Komma-Ersatz). Jetzt deutsch gelesen über preisAusText.
 *  RP-061/RP-160  Ein Revisionskonflikt beim Speichern lud den Serverstand
 *                 und überschrieb die zuletzt getippten Antworten. Jetzt wird
 *                 der Serverstand geholt und die LOKALEN Änderungen werden
 *                 darübergelegt (entwurfZusammenfuehren).
 *  RP-067/RP-166  Abschnitt 5 startete als false ("Nein") und wurde mit !!
 *                 geladen — die Pflichtfrage galt so immer als beantwortet.
 *                 Jetzt null = noch nicht beantwortet.
 */
import { preisAusText } from "@/lib/preis";

export const LEERER_ENTWURF = Object.freeze({
  documents: {}, features: {}, condition: {}, keys_count: "", keys_expected: "",
  notes: "", place: "", damages_confirmed: null, new_damages: [],
  vehicle_check: {}, preis_vorschlag: "", sondervereinbarung: "",
});

/** Zahl vom Server als deutsch lesbarer Text fürs Eingabefeld ("15000,5"). */
function preisFeldText(wert) {
  if (wert === null || wert === undefined || wert === "" || !Number(wert)) return "";
  return String(wert).replace(".", ",");
}

/** Serverprotokoll -> Formularzustand (dieselben Felder wie LEERER_ENTWURF). */
export function entwurfAusServer(p) {
  const x = p || {};
  return {
    documents: x.documents || {},
    features: x.features || {},
    condition: x.condition || {},
    keys_count: x.keys_count || "",
    keys_expected: x.keys_expected || "",
    notes: x.notes || "",
    place: x.place || "",
    // RP-067: nicht beantwortet bleibt null (vorher !! -> false = "Nein").
    damages_confirmed: typeof x.damages_confirmed === "boolean" ? x.damages_confirmed : null,
    new_damages: x.new_damages || [],
    vehicle_check: x.vehicle_check || {},
    preis_vorschlag: preisFeldText(x.preis_vorschlag),
    sondervereinbarung: x.sondervereinbarung || "",
  };
}

/**
 * Preisvorschlag des Fahrers lesen.
 *   ""        -> { lesbar: true, wert: 0 }      (0 = kein Vorschlag, wie bisher)
 *   "15.000"  -> { lesbar: true, wert: 15000 }
 *   "12,50"   -> { lesbar: true, wert: 12.5 }
 *   "1.2.3"   -> { lesbar: false, wert: null }  (Aufrufer meldet es)
 */
export function preisVorschlagLesen(text) {
  const t = String(text ?? "").trim();
  if (!t) return { lesbar: true, wert: 0 };
  const wert = preisAusText(t);
  if (wert === null) return { lesbar: false, wert: null };
  return { lesbar: true, wert };
}

/**
 * Nutzlast für PUT /driver/appointments/{id}/protocol.
 * Wunsch Ahmad 14.09.2026: Preisvorschlag als Zahl (0 = kein Vorschlag).
 * RP-060: ein UNLESBARER Preis wird gar nicht geschickt (der Server behält
 *         seinen Stand) — die Seite zeigt den Fehler und sperrt das Abschicken.
 * RP-067: Abschnitt 5 nur, wenn beantwortet (null schickt nichts).
 * RP-059: der Verkäufername geht mit; ältere Server ignorieren das Feld.
 * RP-058/157/173 (Welle 2): getrimmt — und NUR, wenn der Aufrufer ihn
 *         übergibt (getippt bzw. schon im Entwurf). Ein nur aus dem Termin
 *         vorbelegter Name wird nicht gespeichert: sonst fröre der Entwurf den
 *         alten Namen ein, und eine spätere Korrektur des Händlers am Termin
 *         käme im Protokoll nie an (der Abschluss nimmt den Entwurfswert zuerst).
 */
export function nutzlast(s, sellerName) {
  const { preis_vorschlag: preisText, ...rest } = s || {};
  const out = { ...rest };
  const preis = preisVorschlagLesen(preisText);
  if (preis.lesbar) out.preis_vorschlag = preis.wert;
  if (typeof out.damages_confirmed !== "boolean") delete out.damages_confirmed;
  if (typeof sellerName === "string") out.seller_name = sellerName.trim();
  return out;
}

// Rollenprüfung 22.09.2026 (Review): Nutzlast als Text mit sortierten Schlüsseln —
// ein zusammengeführter Stand hat denselben Inhalt, aber evtl. eine andere
// Reihenfolge der Felder; "gesichert?" (Protokoll.jsx) hängt nur am Inhalt.
export function nutzlastText(nutz) {
  return JSON.stringify(nutz, (_k, v) => (v && typeof v === "object" && !Array.isArray(v)
    ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, v[k]]))
    : v));
}

const istObjekt = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const gleich = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/**
 * RP-061/RP-160: Drei-Wege-Zusammenführung.
 *   server  aktueller Serverstand (Formularform)
 *   lokal   was gerade auf dem Handy steht
 *   basis   der Serverstand, auf dem die lokalen Änderungen beruhen
 * Was lokal anders ist als in `basis`, hat der Fahrer hier geändert — das
 * gewinnt. Alles andere kommt vom Server (z. B. aus einem zweiten Tab).
 * Abschnitte mit Unterfeldern (Dokumente, Zustand, Fahrzeugdaten …) werden je
 * Zeile zusammengeführt, Listen (neue Schäden) als Ganzes.
 */
export function entwurfZusammenfuehren(server, lokal, basis) {
  const out = { ...(server || {}) };
  const b = basis || {};
  for (const k of Object.keys(lokal || {})) {
    const l = lokal[k];
    if (istObjekt(l) && istObjekt(out[k])) {
      const zeilen = { ...out[k] };
      const bk = istObjekt(b[k]) ? b[k] : {};
      for (const z of new Set([...Object.keys(l), ...Object.keys(bk)])) {
        if (gleich(l[z], bk[z])) continue;
        if (l[z] === undefined) delete zeilen[z];
        else zeilen[z] = l[z];
      }
      out[k] = zeilen;
    } else if (!gleich(l, b[k])) {
      out[k] = l;
    }
  }
  return out;
}

/**
 * Rollenprüfung 22.09.2026 (RP-062/RP-161): 409, weil die Fahrt nicht (mehr)
 * angenommen ist — der Händler hat Datum, Adresse, Fahrzeug oder Verkäufer
 * geändert, die Zusage ist zurückgesetzt. Speichern hilft dann nicht, der
 * Fahrer muss die geänderte Fahrt auf der Startseite erneut annehmen.
 */
export function istAnnahmeFehlt(status, text) {
  return status === 409 && /zuerst die Fahrt annehmen/i.test(String(text || ""));
}

/** 409 wegen der Revision (anderer Tab/anderes Gerät oder eigener Wettlauf)? */
export function istRevisionsKonflikt(status, text) {
  return status === 409 && /anderen Tab|anderen Gerät|Revision/.test(String(text || ""));
}
