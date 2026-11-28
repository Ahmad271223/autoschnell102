/**
 * Regeln für das Sprachdiktat (Wunsch Ahmad 24.11.2026) — alles ohne KI, fest und nachvollziehbar:
 *   nachbearbeiten()  Sprachbefehle ("neue Zeile", "Komma", "Punkt" …) + Fachwörter (AHK, TÜV, HU …)
 *   zahlAusSprache()  "hundertzwanzigtausend Kilometer" / "120.000 km" -> 120000
 * Das Windows-Programm hat dieselben Regeln in Diktat.cs (DiktatRegeln) — bei Änderungen beide anpassen.
 */

// ---------------------------------------------------------------- Sprachbefehle

// Reihenfolge wichtig: "Doppelpunkt" vor "Punkt". Umgebende Satzzeichen, die Whisper selbst setzt
// ("Kratzer, Komma, Delle."), werden mit verschluckt.
const BEFEHLE = [
  [/(neue|nächste|naechste)\s+zeile|neuer\s+absatz|absatz/, "\n"],
  [/doppelpunkt/, ":"],
  [/fragezeichen/, "?"],
  [/ausrufezeichen/, "!"],
  [/bindestrich/, "-"],
  [/komma/, ","],
  [/punkt/, "."],
];

export function sprachbefehle(text) {
  let t = String(text || "");
  for (const [muster, zeichen] of BEFEHLE) {
    const re = new RegExp(`[\\s,.;:]*(?<![\\p{L}\\p{N}-])(?:${muster.source})(?![\\p{L}\\p{N}])[\\s,.;:!?]*`, "giu");
    t = t.replace(re, zeichen === "\n" ? "\n" : zeichen === "-" ? "-" : `${zeichen} `);
  }
  return aufraeumen(t);
}

function aufraeumen(t) {
  return t
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n[ \t]+/g, "\n")
    .replace(/[ \t]+([,.:;!?])/g, "$1")
    .replace(/([,.:;!?])(?=[,.:;!?])/g, "")
    .replace(/[ \t]{2,}/g, " ")
    // Großschreibung nach Satzende und am Zeilenanfang
    .replace(/(^|[.!?]\s+|\n)(\p{Ll})/gu, (_m, vor, b) => vor + b.toUpperCase())
    .trim();
}

// ---------------------------------------------------------------- Fachwörter

const FACHWOERTER = [
  [/\bA\.?\s?H\.?\s?K\b\.?/g, "AHK"],
  [/\bT\.?\s?Ü\.?\s?V\b\.?/g, "TÜV"],
  [/\bTüv\b/gi, "TÜV"],
  [/\bH(?:\.\s?U\.|\s?U(?![\p{L}\p{N}]))/gu, "HU"],
  [/\bA(?:\.\s?U\.|\s?U(?![\p{L}\p{N}]))/gu, "AU"],
  [/\bA\.?\s?B\.?\s?S\b/g, "ABS"],
  [/\bE\.?\s?S\.?\s?P\b/g, "ESP"],
  [/\bP\.?\s?S\b(?=[\s,.!?]|$)/g, "PS"],
  // Whisper hört "Scheckheft" oft als "Checkheft" (Messung 24.11.2026)
  [/\bs?checkheft\s*gepflegt\b/gi, "scheckheftgepflegt"],
  [/\bcheckheft\b/gi, "Scheckheft"],
  [/\bzahn\s+riemen\b/gi, "Zahnriemen"],
  [/\bsteuer\s+kette\b/gi, "Steuerkette"],
  [/\bstein\s+schlag\b/gi, "Steinschlag"],
  [/\bstoss\s*stange\b/gi, "Stoßstange"],
  [/\bkotflugel\b/gi, "Kotflügel"],
];

export function fachwoerter(text) {
  let t = String(text || "");
  for (const [re, ersatz] of FACHWOERTER) t = t.replace(re, ersatz);
  return t;
}

/** Was jedes Diktat durchläuft, bevor es ins Feld kommt. */
export function nachbearbeiten(text) {
  return sprachbefehle(fachwoerter(text));
}

// ---------------------------------------------------------------- Zahlen

const EINER = {
  null: 0, ein: 1, eins: 1, eine: 1, einen: 1, zwei: 2, zwo: 2, drei: 3, vier: 4, fünf: 5, fuenf: 5, sechs: 6,
  sieben: 7, acht: 8, neun: 9, zehn: 10, elf: 11, zwölf: 12, zwoelf: 12, dreizehn: 13, vierzehn: 14,
  fünfzehn: 15, fuenfzehn: 15, sechzehn: 16, siebzehn: 17, achtzehn: 18, neunzehn: 19,
};
const ZEHNER = {
  zwanzig: 20, dreißig: 30, dreissig: 30, vierzig: 40, fünfzig: 50, fuenfzig: 50, sechzig: 60, siebzig: 70,
  achtzig: 80, neunzig: 90,
};

/** Zahl unter 1000 aus einem zusammengeschriebenen Zahlwort ("hundertzwanzig", "dreiundachtzig"), sonst null. */
function unterTausend(w) {
  if (!w) return 0;
  let rest = w;
  let wert = 0;
  const h = /^(.*?)hundert(.*)$/.exec(rest);
  if (h) {
    const vor = h[1] ? EINER[h[1]] : 1;
    if (vor == null || vor > 9) return null;
    wert += vor * 100;
    rest = h[2];
    if (rest.startsWith("und")) rest = rest.slice(3);
  }
  if (!rest) return wert;
  if (rest in EINER) return wert + EINER[rest];
  if (rest in ZEHNER) return wert + ZEHNER[rest];
  const u = /^(.+?)und(.+)$/.exec(rest);
  if (u && u[1] in EINER && EINER[u[1]] < 10 && u[2] in ZEHNER) return wert + EINER[u[1]] + ZEHNER[u[2]];
  return null;
}

/** "hundertzwanzigtausendfünfhundert" -> 120500; null, wenn es kein reines Zahlwort ist. */
export function zahlwort(text) {
  const w = String(text || "").toLowerCase().replace(/[\s-]+/g, "");
  if (!w) return null;
  let wert = 0;
  let rest = w;
  const mio = /^(.*?)million(en)?(.*)$/.exec(rest);
  if (mio) {
    const m = mio[1] ? unterTausend(mio[1]) : 1;
    if (m == null) return null;
    wert += m * 1_000_000;
    rest = mio[3];
  }
  const tsd = /^(.*?)tausend(.*)$/.exec(rest);
  if (tsd) {
    const t = tsd[1] ? unterTausend(tsd[1]) : 1;
    if (t == null) return null;
    wert += t * 1000;
    rest = tsd[2];
    if (rest.startsWith("und")) rest = rest.slice(3);
  }
  const u = unterTausend(rest);
  if (u == null) return null;
  return wert + u;
}

/**
 * Zahl aus dem Gesagten für km/Preis. Whisper schreibt meist schon Ziffern ("120.000 Kilometer",
 * "15.000 Euro", "15 000"), manchmal Wörter ("hundertzwanzigtausend"). Ergebnis: ganze Zahl oder null.
 */
export function zahlAusSprache(text) {
  let t = String(text || "").toLowerCase()
    .replace(/kilometer|km|euro|€|eur\b|ungefähr|ca\.?|circa|etwa|genau|stand|kilometerstand|preis|ist|sind|bei|um/g, " ")
    .replace(/[.!?]+$/g, "")
    .trim();
  if (!t) return null;
  // "120 tausend", "15,5 tausend"
  const tsd = /^(\d+(?:[.,]\d+)?)\s*(tausend|tsd\.?|k)$/.exec(t);
  if (tsd) return Math.round(Number(tsd[1].replace(",", ".")) * 1000);
  // Ziffern mit Tausenderpunkten/Leerzeichen
  const ziffern = /^\d{1,3}(?:[.\s ]\d{3})+$|^\d+$/.exec(t);
  if (ziffern) return Number(t.replace(/[.\s ]/g, ""));
  const wort = zahlwort(t);
  return wort;
}

/** 120000 -> "120.000" (wie die Felder es anzeigen). */
export function zahlText(n) {
  return Number(n).toLocaleString("de-DE", { maximumFractionDigits: 0 });
}
