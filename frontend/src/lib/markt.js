// Market Intelligence (Auftrag Ahmad 25./26.09.2026): reine Hilfsfunktionen.
// Wortwahl bewusst: "N günstigste Vergleichsangebote", "Median der N günstigsten",
// "untere Marktpreisspanne" — nie "Marktpreis" oder "Marktmedian". Reparaturwelle 6
// Nr. 137/138: die Zahl N kommt immer aus den Daten (sample_size / sample_limit),
// nie fest "20" — jeder Suchauftrag hat seine eigene Zeilenzahl.

export function eur(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  return `${Math.round(Number(n)).toLocaleString("de-DE")} €`;
}

export function pct(n, plus = true) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  const v = Number(n);
  return `${plus && v > 0 ? "+" : ""}${v.toLocaleString("de-DE", { maximumFractionDigits: 1 })} %`;
}

/** "−850 € (−4,0 %)" bzw. "—" ohne Daten. */
export function trendText(eurWert, pctWert) {
  if (eurWert === null || eurWert === undefined) return "—";
  const v = Number(eurWert);
  const z = `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(Math.round(v)).toLocaleString("de-DE")} €`;
  return pctWert === null || pctWert === undefined ? z : `${z} (${pct(pctWert)})`;
}

/** Review 26.09.2026 Nr. 55 — Bestandstrend: "gleiche Autos: −210 € (−1,1 %) · 14 Autos";
 *  ohne gemeinsame Autos an beiden Vergleichstagen "gleiche Autos: —". */
export function bestandText(eurWert, pctWert, anzahl) {
  if (eurWert === null || eurWert === undefined || !anzahl) return "gleiche Autos: —";
  return `gleiche Autos: ${trendText(eurWert, pctWert)} · ${anzahl} ${anzahl === 1 ? "Auto" : "Autos"}`;
}

export function trendFarbe(v) {
  if (v === null || v === undefined) return "var(--text-dim)";
  return Number(v) < 0 ? "var(--st-gruen)" : Number(v) > 0 ? "var(--st-rot)" : "var(--text-secondary)";
}

export const DATENLAGE = {
  niedrig: { text: "Datenlage niedrig (unter 7 Tage oder Lücken)", farbe: "var(--st-rot)" },
  mittel: { text: "Datenlage mittel", farbe: "var(--st-amber)" },
  gut: { text: "Datenlage gut", farbe: "var(--st-gruen)" },
  unsicher: { text: "Datenlage unsicher (Sortierung)", farbe: "var(--st-amber)" },
  // Reparaturwelle 6 Nr. 143: der Markt hat mehr Angebote, als der letzte Abruf lieferte
  unvollstaendig: { text: "Datenlage unvollständig (weniger geliefert als bestellt)", farbe: "var(--st-amber)" },
  keine: { text: "noch keine Daten", farbe: "var(--text-dim)" },
};

/** Nr. 137: "Median der 10 günstigsten" — N aus den Daten (Stichprobe), nie fest. */
export function medianLabel(n) {
  return n ? `Median der ${n} günstigsten` : "Median der günstigsten";
}

/** Nr. 137/146: Median aus dem neuen Feld, alte Feldnamen nur als Rückfall. */
export function medianSample(d) {
  const v = d?.median_sample_price ?? d?.median_top20_price;
  return v === null || v === undefined ? null : Number(v);
}

/** Nr. 136: eine Chance, deren Preis sich seit dem Erkennen geändert hat, ist nur noch historisch. */
export const CHANCE_HISTORISCH = "historisch — Preis geändert";

export const CHANCE_TYP = {
  neu_guenstig: "Neu & günstig",
  neues_minimum: "Neues günstigstes Angebot",
  stark_reduziert: "Stark reduziert",
  neu_top5: "Neu in den Top 5",
};

export function chanceTypText(typ) {
  if (!typ) return "";
  if (typ.startsWith("neu_top")) return `Neu in den Top ${typ.replace("neu_top", "")}`;
  return CHANCE_TYP[typ] || typ;
}

export const ZUSTAND = {
  seen: "im Sample gesehen",
  not_seen_in_sample: "nicht mehr unter den günstigsten (kein Verkauf!)",
  verification_pending: "wird nachgeprüft (zweite Prüfung am Folgetag)",
  confirmed_removed: "Inserat nicht mehr online",
};

export function zustandText(z) {
  return ZUSTAND[z] || "";
}

export function datumKurz(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 10);
  return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
}

export function datumZeit(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/** Seit Erstbeobachtung: "21.900 € → 20.400 € · −1.500 € · 3 Reduzierungen". */
export function seitErstbeobachtung(l) {
  if (!l || l.first_price == null || l.current_price == null) return "";
  const diff = Number(l.current_price) - Number(l.first_price);
  const teile = [`${eur(l.first_price)} → ${eur(l.current_price)}`];
  if (diff !== 0) teile.push(trendText(diff, l.first_price ? (diff / Number(l.first_price)) * 100 : null));
  if (l.price_reductions) teile.push(`${l.price_reductions} ${l.price_reductions === 1 ? "Reduzierung" : "Reduzierungen"}`);
  return teile.join(" · ");
}

export const BEREICHE = [["7d", "7 Tage"], ["30d", "30 Tage"], ["90d", "90 Tage"], ["6m", "6 Monate"], ["1y", "1 Jahr"], ["alle", "Gesamt"]];
