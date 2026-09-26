/*
 * Monat/Jahr fuer Erstzulassung und HU/TUEV (Wunsch Ahmad, 12.09.2026).
 *
 * "Der Fahrer soll den / nicht selbst tippen muessen: nur Ziffern, nach zwei
 * Ziffern (09 oder 12) automatisch /, dann die vier Ziffern des Jahres."
 *
 * Gespeichert wird immer "MM/JJJJ". Die Regeln liegen hier als reine
 * Funktionen (testbar ohne React); components/MonatJahrEingabe.jsx nutzt sie.
 *
 * Tippen:
 *   - nur Ziffern zaehlen; "1/" oder "1." wird zu "01/"
 *   - erste Ziffer 2-9 steht sofort fuer den Monat ("9" -> "09/")
 *   - erste Ziffer 0 oder 1 wartet auf die zweite; 00 und 13-19 gibt es nicht
 *   - nach dem Monat kommt "/" von selbst, danach hoechstens vier Jahresziffern
 *   - Monat in einem fertigen Wert markieren und ueberschreiben behaelt das
 *     Jahr ("12/2020": "12" markieren, "1" tippen -> "1/2020", wartet auf die
 *     zweite Ziffer). Ein ungueltiger Monat laesst den bisherigen Wert stehen.
 *   - Loeschen haengt nie etwas an; wer nur den "/" loescht, loescht die
 *     Monatsziffer davor mit (sonst kaeme der "/" sofort zurueck)
 * Einfuegen / Altwerte: "1.2020", "01/2020", "2020-01", "01.03.2020",
 *   "032020", "06/26", "März 2019" werden gelesen; nur ein Jahr ("2018"),
 *   "Neu" oder Unsinn nicht (null) — solche Werte bleiben unangetastet.
 */

const MONATSNAMEN = {
  jan: 1, januar: 1, feb: 2, februar: 2, mrz: 3, mar: 3, "mär": 3, "märz": 3, maerz: 3,
  apr: 4, april: 4, mai: 5, jun: 6, juni: 6, jul: 7, juli: 7, aug: 8, august: 8,
  sep: 9, sept: 9, september: 9, okt: 10, oktober: 10, nov: 11, november: 11,
  dez: 12, dezember: 12,
};

function zweistellig(n) {
  return String(n).padStart(2, "0");
}

/**
 * Feldinhalt nach einer Eingabe -> formatierter Wert.
 * @param {string} roh      Inhalt des Feldes nach der Eingabe
 * @param {string} vorher   bisheriger Wert
 * @param {{loeschen?: boolean}} art
 * @returns {{wert: string, hinweis: string}}
 */
export function monatJahrTippen(roh, vorher = "", { loeschen = false } = {}) {
  const text = String(roh ?? "");
  const vor = String(vorher ?? "");
  // Monat und Jahr durch den "/" getrennt, z. B. nach dem Ueberschreiben des Monats.
  const getrennt = /^(\d{0,2})\/(\d*)$/.exec(text);

  if (loeschen) {
    // Nur den automatischen "/" am Ende geloescht ("12/" -> "12").
    if (/^\d{2}\/$/.test(vor) && text === vor.slice(0, 2)) {
      return { wert: vor.slice(0, 1), hinweis: "" };
    }
    // Den "/" mitten im Wert geloescht ("05/2020" -> "052020"): Monatsziffer davor mit.
    if (!text.includes("/") && vor.includes("/")
        && text.replace(/\D/g, "") === vor.replace(/\D/g, "")) {
      const [m, j] = vor.split("/");
      const wert = `${m.slice(0, -1)}/${j}`;
      return { wert: wert === "/" ? "" : wert, hinweis: "" };
    }
    if (getrennt) {
      const [, m, j] = getrennt;
      return { wert: j ? `${m}/${j.slice(0, 4)}` : m, hinweis: "" };
    }
    const d = text.replace(/\D/g, "").slice(0, 6);
    return { wert: d.length > 2 ? `${d.slice(0, 2)}/${d.slice(2)}` : d, hinweis: "" };
  }

  if (getrennt && getrennt[2] !== "") {
    const [, m, j] = getrennt;
    const jahr = j.slice(0, 4);
    if (m === "") return { wert: `/${jahr}`, hinweis: "" };
    if (m.length === 1) {
      return { wert: m >= "2" ? `0${m}/${jahr}` : `${m}/${jahr}`, hinweis: "" };
    }
    const mm = Number(m);
    if (mm < 1 || mm > 12) return { wert: vor, hinweis: "Monat 01–12" };
    return { wert: `${m}/${jahr}`, hinweis: "" };
  }

  let ziffern = text.replace(/\D/g, "");
  if (/^\d[/.\-\s]/.test(text.trim())) ziffern = `0${ziffern}`;
  if (!ziffern) return { wert: "", hinweis: "" };

  let monat;
  let rest;
  const z1 = ziffern[0];
  if (z1 >= "2") {
    monat = `0${z1}`;
    rest = ziffern.slice(1);
  } else {
    if (ziffern.length === 1) return { wert: z1, hinweis: "" };
    const mm = Number(ziffern.slice(0, 2));
    if (mm < 1 || mm > 12) return { wert: vor || z1, hinweis: "Monat 01–12" };
    monat = ziffern.slice(0, 2);
    rest = ziffern.slice(2);
  }
  return { wert: `${monat}/${rest.slice(0, 4)}`, hinweis: "" };
}

/**
 * Wie viele Ziffern stehen nach dem Umformatieren vor dem Cursor?
 * Unendlich = ans Ende (am Ende getippt: "9" -> "09/"). Ziffern, die die Regel
 * dazugibt (fuehrende 0) oder wegnimmt (Monatsziffer beim Loeschen des "/",
 * abgelehnte Eingabe), verschieben den Cursor mit — sonst landete nach
 * "05/|2020" + Ruecktaste die naechste Ziffer im Jahr (Gegenpruefung 12.09.2026).
 * @param {string} roh      Feldinhalt nach der Eingabe
 * @param {number} cursor   Cursor im rohen Feldinhalt
 * @param {string} wert     formatierter Wert (monatJahrTippen)
 */
export function ziffernVorCursor(roh, cursor, wert) {
  const text = String(roh ?? "");
  if (cursor == null || cursor >= text.length) return Number.POSITIVE_INFINITY;
  const zusatz = String(wert ?? "").replace(/\D/g, "").length - text.replace(/\D/g, "").length;
  return Math.max(0, text.slice(0, cursor).replace(/\D/g, "").length + zusatz);
}

function zweistelligesJahr(jj, art, heute) {
  // Eine HU liegt immer in diesem Jahrhundert; eine EZ mit "95" ist 1995.
  if (art === "hu") return 2000 + jj;
  return jj <= heute.getFullYear() % 100 ? 2000 + jj : 1900 + jj;
}

/**
 * Beliebige Schreibweise -> "MM/JJJJ" oder null.
 * @param {string} eingabe
 * @param {{art?: "ez"|"hu", heute?: Date}} optionen
 */
export function monatJahrAusText(eingabe, { art = "ez", heute = new Date() } = {}) {
  const t = String(eingabe ?? "").trim().toLowerCase();
  if (!t) return null;
  let m;
  let j;
  let r;
  if ((r = /^(\d{4})\s*[-/.]\s*(\d{1,2})$/.exec(t))) {
    j = Number(r[1]); m = Number(r[2]);
  } else if ((r = /^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$/.exec(t))) {
    m = Number(r[2]); j = Number(r[3]);
  } else if ((r = /^(\d{1,2})\s*[/.\-]\s*(\d{4})$/.exec(t))) {
    m = Number(r[1]); j = Number(r[2]);
  } else if ((r = /^(\d{1,2})\s*[/.\-]\s*(\d{2})$/.exec(t))) {
    m = Number(r[1]); j = zweistelligesJahr(Number(r[2]), art, heute);
  } else if ((r = /^(\d{2})(\d{4})$/.exec(t))) {
    m = Number(r[1]); j = Number(r[2]);
  } else if ((r = /^([a-zäöü]+)\.?\s+(\d{4})$/.exec(t))) {
    m = MONATSNAMEN[r[1]]; j = Number(r[2]);
  } else {
    return null;
  }
  if (!m || m < 1 || m > 12 || !j || j < 1900 || j > 2100) return null;
  return `${zweistellig(m)}/${j}`;
}

/**
 * Pruefen beim Verlassen des Feldes.
 * @returns {{ok: boolean, fehler: string, hinweis: string}}
 */
export function monatJahrPruefen(wert, { art = "ez", heute = new Date() } = {}) {
  const w = String(wert ?? "").trim();
  const gut = (hinweis = "") => ({ ok: true, fehler: "", hinweis });
  const schlecht = (fehler) => ({ ok: false, fehler, hinweis: "" });
  if (!w) return gut();
  const r = /^(\d{2})\/(\d{4})$/.exec(w);
  if (!r) return schlecht("Bitte MM/JJJJ vollständig eingeben");
  const m = Number(r[1]);
  const j = Number(r[2]);
  if (m < 1 || m > 12) return schlecht("Monat 01–12");
  const jetzt = heute.getFullYear() * 12 + heute.getMonth();
  const eingabe = j * 12 + (m - 1);
  if (art === "hu") {
    if (j < heute.getFullYear() - 10) return schlecht("Jahr unplausibel");
    if (eingabe > jetzt + 36) return schlecht("HU gilt höchstens 3 Jahre im Voraus");
    return eingabe < jetzt ? gut("HU abgelaufen") : gut();
  }
  if (j < 1900) return schlecht("Jahr ab 1900");
  if (eingabe > jetzt) return schlecht("Erstzulassung liegt in der Zukunft");
  return gut();
}

/**
 * Sperre beim Speichern/Abschicken (Gegenpruefung 12.09.2026): Ein halb
 * getippter Wert ("06/20", "1/2020", "12") wurde sonst gespeichert und
 * spaeter als 06/2020 gelesen oder unvollstaendig gedruckt.
 *
 * Bewusst nur fuer GETIPPTE Werte (Ziffern und "/"): Altwerte aus einem
 * Inserat wie "2018" oder "Neu" sperren nicht — die kann niemand aendern,
 * der den Vertrag nur erstellen will.
 * @returns {string} Fehlertext oder ""
 */
export function monatJahrFehler(wert) {
  const w = String(wert ?? "").trim();
  if (!w || !/^[\d/]+$/.test(w)) return "";
  const r = /^(\d{2})\/(\d{4})$/.exec(w);
  if (r) {
    const m = Number(r[1]);
    return m < 1 || m > 12 ? "Monat 01–12" : "";
  }
  if (w.includes("/") || /^\d{1,2}$/.test(w)) return "Bitte MM/JJJJ vollständig eingeben";
  return "";
}

/** Zur Anzeige und zum Vergleich: lesbare Werte vereinheitlichen, sonst unveraendert. */
export function monatJahrText(wert, optionen = {}) {
  return monatJahrAusText(wert, optionen) || String(wert ?? "").trim();
}
