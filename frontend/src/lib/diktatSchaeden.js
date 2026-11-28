/**
 * Schäden aus dem Gesagten (Wunsch Ahmad 24.11.2026, ohne KI): "Kratzer Stoßstange hinten, Delle Fahrertür"
 * -> Vorschläge für die Schadensskizze (Kratzer · Stoßstange hinten, Delle · Tür vorne links). Feste Regeln:
 * Schadensart per Stichwort, Bauteil + vorne/hinten/links/rechts gegen die Punkte der Skizze (DamageSelector DOTS).
 * Der Fahrer bestätigt jeden Vorschlag — nichts landet ungefragt in Protokoll oder Vertrag.
 */

// Bauteile, wie sie nach der Vereinheitlichung (norm) als Wort vorkommen
const BAUTEILE = [
  "stoßstange", "kotflügel", "tür", "motorhaube", "dach", "heckklappe", "windschutzscheibe", "heckscheibe",
  "seitenscheibe", "scheinwerfer", "nebelscheinwerfer", "rücklicht", "außenspiegel", "rad", "schweller",
  "a-säule", "b-säule", "c-säule", "kennzeichen", "kühlergrill", "lufteinlass", "auspuff", "emblem",
];
const LAGEN = ["vorne", "hinten", "links", "rechts"];
const GEGENTEIL = { vorne: "hinten", hinten: "vorne", links: "rechts", rechts: "links" };

const ERSETZEN = [
  [/beifahrert[üu]r/g, " tür vorne rechts "],
  [/fahrert[üu]r/g, " tür vorne links "],
  [/beifahrerseite/g, " rechts "],
  [/fahrerseite/g, " links "],
  [/kennzeichenhalterung/g, " kennzeichen vorne "],
  [/nebel(schein)?werfer|nebelleuchte/g, " nebelscheinwerfer "],
  [/(?<!nebel)(haupt|front)?scheinwerfer/g, " scheinwerfer "],
  [/r[üu]ckleuchte|heckleuchte|r[üu]cklicht/g, " rücklicht "],
  [/kofferraum(deckel|klappe)?|heckdeckel/g, " heckklappe "],
  [/frontscheibe|windschutzscheibe/g, " windschutzscheibe "],
  [/(au[ßs]en|seiten)?spiegel/g, " außenspiegel "],
  [/vorder(rad|reifen|felge)/g, " vorne rad "],
  [/hinter(rad|reifen|felge)/g, " hinten rad "],
  [/felgen?|reifen|r[äa]der|\brad\b/g, " rad "],
  [/frontsch[üu]rze|frontsto[ßs]stange|frontsto[ßs]f[äa]nger/g, " stoßstange vorne "],
  [/hecksch[üu]rze|hecksto[ßs]stange|hecksto[ßs]f[äa]nger/g, " stoßstange hinten "],
  [/sto[ßs]stange|sto[ßs]f[äa]nger/g, " stoßstange "],
  [/seitenwand|radlauf|kotfl[üu]gel/g, " kotflügel "],
  [/motorhaube|\bhaube\b/g, " motorhaube "],
  [/k[üu]hlergrill|\bgrill\b/g, " kühlergrill "],
  [/marken-?emblem/g, " emblem "],
  [/\b([abc])[\s-]?s[äa]ule/g, " $1-säule "],
  [/t[üu]ren?\b/g, " tür "],
  [/\b(linke[nrsm]?|links)\b/g, " links "],
  [/\b(rechte[nrsm]?|rechts)\b/g, " rechts "],
  [/\b(vordere[nrsm]?|vorn|vorne|front)\b/g, " vorne "],
  [/\b(hintere[nrsm]?|hinten|heck)\b/g, " hinten "],
];

export function norm(text) {
  let t = ` ${String(text || "").toLowerCase()} `;
  for (const [re, ersatz] of ERSETZEN) t = t.replace(re, ersatz);
  return t.split(/[^\p{L}\p{N}-]+/u).filter(Boolean);
}

function schadensart(teil, bauteil) {
  const t = teil.toLowerCase();
  if (/unfall/.test(t)) return /nicht\s+repariert|unrepariert|offen/.test(t) ? "unfall_nicht_repariert" : "unfall_repariert";
  if (/hagel/.test(t)) return "hagelschaden";
  if (/steinschl/.test(t) || (/riss|gerissen/.test(t) && /scheibe/.test(bauteil || ""))) return "steinschlag";
  if (/rost|korros/.test(t)) return "rost";
  if (/dell|beule|eingedr[üu]ckt|d[öo]tsch/.test(t)) return "delle";
  if (/kratz|schramme|lackschaden/.test(t)) return "kratzer";
  if (/defekt|kaputt|geht nicht|funktioniert nicht|ausgefallen/.test(t)
      && ["scheinwerfer", "nebelscheinwerfer", "rücklicht"].includes(bauteil)) return "beleuchtung";
  return null;
}

const ANSICHTEN = ["front", "rear", "left", "right", "top"];

/** Bester Punkt der Skizze für Bauteil + Lagen; null wenn keiner passt. */
export function punktFinden(dots, bauteil, lagen) {
  let best = null;
  let gleich = false;
  for (const view of ANSICHTEN) {
    for (const dot of dots[view] || []) {
      const w = norm(dot.name);
      if (!w.includes(bauteil)) continue;
      const dl = new Set(w.filter((x) => LAGEN.includes(x)));
      if ([...lagen].some((l) => dl.has(GEGENTEIL[l]))) continue;          // Widerspruch: links vs. rechts
      let punkte = 0;
      for (const l of lagen) if (dl.has(l)) punkte += 2;
      for (const l of dl) if (!lagen.has(l)) punkte -= 1;                   // ungesagt: vorne/hinten offen
      const lage = [...dl].sort().join(" ");
      // gleich gut, aber eine andere Stelle (nicht nur dasselbe Bauteil in einer anderen Ansicht)
      if (!best || punkte > best.punkte) { best = { view, dot, punkte, lage }; gleich = false; }
      else if (punkte === best.punkte && best.lage !== lage) gleich = true;
    }
  }
  return best ? { view: best.view, dot: best.dot, unsicher: gleich } : null;
}

/**
 * Text -> { vorschlaege: [{ view, dot, type_key, unsicher, text }], offen: [Textteile ohne Zuordnung] }.
 * Teile werden an Komma/Punkt/"und" getrennt; ohne eigene Schadensart gilt die zuletzt genannte.
 */
export function schaedenAusSprache(text, dots) {
  const teile = String(text || "").split(/[,;.\n]|\bund\b|\bsowie\b|\bplus\b/i).map((x) => x.trim()).filter(Boolean);
  const vorschlaege = [];
  const offen = [];
  let letzteArt = null;
  for (const teil of teile) {
    const woerter = norm(teil);
    const bauteil = woerter.find((w) => BAUTEILE.includes(w)) || null;
    const art = schadensart(teil, bauteil) || (bauteil ? letzteArt : null);
    if (art) letzteArt = art;
    if (!art || !bauteil) {
      if (woerter.length) offen.push(teil);
      continue;
    }
    const lagen = new Set(woerter.filter((w) => LAGEN.includes(w)));
    const treffer = punktFinden(dots, bauteil, lagen);
    if (!treffer) { offen.push(teil); continue; }
    if (vorschlaege.some((v) => v.type_key === art && v.dot.name === treffer.dot.name)) continue;
    vorschlaege.push({ view: treffer.view, dot: treffer.dot, type_key: art, unsicher: treffer.unsicher, text: teil });
  }
  return { vorschlaege, offen };
}
