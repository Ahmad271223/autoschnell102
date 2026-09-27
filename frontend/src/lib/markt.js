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
  // Master-Auftrag Phase C: Alias aus Datenqualität x Markttiefe — technischer Fehler rot, dünner Markt gelb,
  // leerer Markt grau (Marktlücke ist kein Fehler), veraltet (letzter gültiger Lauf > 48 h) gelb
  fehler: { text: "Datenlage: Fehler (letzter Lauf technisch unbrauchbar)", farbe: "var(--st-rot)" },
  duenn: { text: "Datenlage dünn (wenige Angebote im Segment)", farbe: "var(--st-amber)" },
  leer: { text: "Datenlage leer (kein Angebot — Marktlücke, kein Fehler)", farbe: "var(--text-dim)" },
  veraltet: { text: "Datenlage veraltet (letzter gültiger Lauf über 48 h)", farbe: "var(--st-amber)" },
  keine: { text: "noch keine Daten", farbe: "var(--text-dim)" },
};

/** Master-Auftrag Phase C: Datenqualität (Lauf technisch verlässlich?) getrennt von der Markttiefe (wie viele Angebote?). */
export const DATENQUALITAET = {
  GOOD: { text: "Datenqualität gut", kurz: "Qualität gut", zaehler: "gut", farbe: "var(--st-gruen)" },
  MEDIUM: { text: "Datenqualität mittel", kurz: "Qualität mittel", zaehler: "mittel", farbe: "var(--st-amber)" },
  POOR: { text: "Datenqualität schlecht", kurz: "Qualität schlecht", zaehler: "schlecht", farbe: "var(--st-rot)" },
  UNKNOWN: { text: "Datenqualität unbekannt (kein Lauf)", kurz: "Qualität ?", zaehler: "ohne Lauf", farbe: "var(--text-dim)" },
};
export const DATENQUALITAET_GRUND = {
  fremdfahrzeuge: "Fremdfahrzeuge verworfen", parser: "Parserfehler", nur_monoton: "Top-N nicht bewiesen", stale: "letzter Lauf über 48 h",
  ungueltig: "nur ungültige Läufe", kein_lauf: "kein Lauf",
};
export const MARKTTIEFE = {
  FULL: { text: "Markttiefe voll", kurz: "Tiefe voll", zaehler: "voll", farbe: "var(--st-gruen)" },
  NORMAL: { text: "Markttiefe normal", kurz: "Tiefe normal", zaehler: "normal", farbe: "var(--st-gruen)" },
  THIN: { text: "Markttiefe dünn (wenige Angebote)", kurz: "Tiefe dünn", zaehler: "dünn", farbe: "var(--st-amber)" },
  EMPTY: { text: "Markttiefe leer (kein Angebot — Marktlücke)", kurz: "Tiefe leer", zaehler: "leer", farbe: "var(--text-dim)" },
  UNKNOWN: { text: "Markttiefe unbekannt", kurz: "Tiefe ?", zaehler: "unbekannt", farbe: "var(--text-dim)" },
};
export const VOLLSTAENDIGKEIT = {
  COMPLETE: "Stichprobe vollständig", INCOMPLETE: "Stichprobe unvollständig (Markt größer als geliefert)",
  UNKNOWN: "Vollständigkeit unbekannt (Scraper liefert keine Gesamtzahl)",
};
export function qualitaetsText(d) {
  const q = DATENQUALITAET[d?.data_quality] || DATENQUALITAET.UNKNOWN;
  const g = d?.data_quality_grund ? DATENQUALITAET_GRUND[d.data_quality_grund] || d.data_quality_grund : "";
  return g && d?.data_quality !== "GOOD" ? `${q.text} — ${g}` : q.text;
}

/** Nr. 137: "Low-Market-Median (10 günstigste)" — N aus den Daten (Stichprobe), nie fest;
 *  Master-Auftrag Phase C: neutrale Bezeichnung statt "Top-N". */
export function medianLabel(n) {
  return n ? `Median der ${n} günstigsten` : "Median der günstigsten";
}
export function lowMarketLabel(n) {
  return n ? `Low-Market-Median (${n} günstigste)` : "Low-Market-Median (günstigste)";
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

/** Private Deals (Ahmad 26.09.2026 abends): Inserat-Link nur, wenn er sicher auf mobile.de (https) zeigt —
 *  alles andere (leer, http, fremde Domain, "mobile.de.example.com") bekommt keinen Knopf. */
export function mobileLink(url) {
  if (!url || typeof url !== "string") return null;
  return /^https:\/\/([a-z0-9-]+\.)*mobile\.de(\/|$)/i.test(url.trim()) ? url.trim() : null;
}

export const PRIVATE_SORTIERUNGEN = [
  ["abstand_pct", "größter Abstand zum Segment-Median"], ["neueste", "neueste in der Top-3"], ["preis", "Preis aufsteigend"],
  ["reduzierung", "stärkste Reduzierung"], ["standzeit", "längste Standzeit bei mobile.de"],
];

/** Master-Auftrag Phase D (27.09.2026): Hot-Deal-Klassen nach Vorteil gegenüber dem historischen
 *  Low-Market-Median desselben Segments (DEAL 5–8 %, STRONG 8–12 %, EXTREME ab 12 %). */
export const HOTDEAL_KLASSE = {
  EXTREME: { text: "Extrem", tone: "green" },
  STRONG: { text: "Stark", tone: "blue" },
  DEAL: { text: "Deal", tone: "yellow" },
};
export const HOTDEAL_EREIGNIS = {
  NEW_HOT_DEAL: "neu im Sample und gleich ein Hot Deal",
  BECAME_HOT_DEAL: "wurde zum Hot Deal",
  STILL_HOT: "weiterhin Hot Deal",
  PRICE_DROP_HOT_DEAL: "Preis gesenkt (weiter Hot Deal)",
  LEFT_HOT_ZONE: "nicht mehr in der Hot-Deal-Zone",
  REMOVED: "Inserat nicht mehr online",
};
export const HOTDEAL_STATUS = { ACTIVE: "aktiv", LEFT: "verlassen", REMOVED: "entfernt" };
export const HOTDEAL_GRUND = {
  ueber_schwelle: "Preis/Referenz — nicht mehr günstig genug",
  nicht_mehr_im_sample: "nicht mehr unter den günstigsten (kein Verkauf!)",
  inserat_entfernt: "Inserat nicht mehr online",
};
export const HOTDEAL_SORTIERUNGEN = [
  ["vorteil_pct", "größter Vorteil in %"], ["vorteil_eur", "größter Vorteil in €"], ["neueste", "neueste"], ["privat", "privat zuerst"],
  ["modell", "Modell"], ["ez", "EZ (neueste zuerst)"], ["km", "km aufsteigend"], ["liquiditaet", "Liquidität"], ["klasse", "Deal-Klasse"],
];
/** Liquidität (Abschnitt 56): aus Umschlag und Top-5-Wechsel, nicht nur aus der Anzahl. */
export const LIQUIDITAET = {
  HIGH: { text: "hoch", farbe: "var(--st-gruen)" },
  MEDIUM: { text: "mittel", farbe: "var(--st-amber)" },
  LOW: { text: "niedrig", farbe: "var(--st-rot)" },
  UNKNOWN: { text: "unbekannt", farbe: "var(--text-dim)" },
};
export function liquiditaetText(stufe) {
  return (LIQUIDITAET[stufe] || LIQUIDITAET.UNKNOWN).text;
}

/** Master-Auftrag Phase E (27.09.2026): Berichte 5 Tage / 15 Tage / Monat. Farblogik (Abschnitte 14/47):
 *  Preis fällt = grün, steigt = rot, stabil (±0,5 %) = neutral. */
export const BERICHT_TYP = { FIVE_DAY: "5 Tage", FIFTEEN_DAY: "15 Tage", MONTHLY: "Monat" };
export const RICHTUNG = {
  FALLING: { text: "fallend", farbe: "var(--st-gruen)", tone: "green" },
  RISING: { text: "steigend", farbe: "var(--st-rot)", tone: "red" },
  STABLE: { text: "stabil", farbe: "var(--text-secondary)", tone: "gray" },
  UNKNOWN: { text: "unbekannt", farbe: "var(--text-dim)", tone: "gray" },
};
export function richtungFarbe(r) {
  return (RICHTUNG[r] || RICHTUNG.UNKNOWN).farbe;
}
/** Richtung aus einer %-Änderung mit derselben Stabilitätszone wie das Backend (berichte.richtung): innerhalb
 *  ±zone stabil (neutral), darunter fallend (grün), darüber steigend (rot), ohne Wert unbekannt. Für Werte, die
 *  der Bericht ohne eigene Richtung speichert (Segmentdetail, Stichprobe inkl. Mix). */
export function richtungAusPct(p, zone = 0.5) {
  if (p === null || p === undefined || p === "" || Number.isNaN(Number(p))) return "UNKNOWN";
  const z = zone === null || zone === undefined || Number.isNaN(Number(zone)) ? 0.5 : Number(zone);
  if (Number(p) < -z) return "FALLING";
  if (Number(p) > z) return "RISING";
  return "STABLE";
}
export const CONFIDENCE = {
  HIGH: { text: "hoch", farbe: "var(--st-gruen)" },
  MEDIUM: { text: "mittel", farbe: "var(--st-amber)" },
  LOW: { text: "niedrig", farbe: "var(--st-rot)" },
};
/** Prüfung Runde 2 (#3): Rechenregel der Berichte (backend berichte.SCHEMA). Ein eingefrorener Bericht behält seine
 *  Nummer — Schema 1 (oder ohne Angabe) ist nach einer älteren Rechenregel entstanden; Kennzahlen wie Periodenwerte,
 *  Tagesbewegung, Kosten je Einheit und Segmentabdeckung sind dort anders definiert. */
export const BERICHT_SCHEMA = 2;
export const BERICHT_ALT_TEXT = "nach älterer Rechenregel erstellt";
export function berichtAltesSchema(b) {
  if (!b) return false;
  const s = Number(b.schema ?? 1);
  return Number.isNaN(s) || s < BERICHT_SCHEMA;
}
/** Diagrammpunkt eines Berichtstags. Schema 2: die Medianlinie ist der Korbwert (median_korb — an Tagen mit
 *  Teilabdeckung über die an beiden Vergleichstagen vorhandenen Segmente verkettet, kein Scheineinbruch); P25/P75
 *  eines teilabgedeckten oder heute noch laufenden Tages gelten nur für die vorhandenen Segmente → Lücke; das
 *  Tagesminimum ist ein echtes Angebot und bleibt. Ältere Berichte ohne median_korb: Teilabdeckung = ganze Lücke. */
export function berichtDiagrammPunkt(r) {
  if (!r) return r;
  if (r.median_korb !== undefined) {
    const teil = r.teilabdeckung || Number(r.ausstehende_segmente || 0) > 0;
    return { ...r, median: r.median_korb, ...(teil ? { p25: null, p75: null } : {}) };
  }
  return r.teilabdeckung ? { ...r, median: null, min: null, p25: null, p75: null } : r;
}
/** "01.09.–05.09.2026" */
export function periodeText(von, bis) {
  const t = (s) => (s ? `${s.slice(8, 10)}.${s.slice(5, 7)}.` : "");
  return von && bis ? `${t(von)}–${t(bis)}${bis.slice(0, 4)}` : "";
}
/** Abschnitt 37: Sortierungen der Übersicht aller Modelle. */
export const BERICHT_SORTIERUNGEN = [
  ["rueckgang", "größter Preisrückgang"], ["anstieg", "größter Anstieg"], ["deals", "meiste Deals"], ["privat", "meiste private Deals"],
  ["liquiditaet", "höchste Liquidität"], ["qualitaet", "schlechteste Qualität"], ["leer", "meisten EMPTY-Segmente"], ["kosten", "höchste Kosten"],
  ["modell", "Modell (A–Z)"],
];
const QUALITAET_SCHLECHT = { POOR: 3, UNKNOWN: 2, MEDIUM: 1, GOOD: 0 };
const LIQ_RANG = { HIGH: 3, MEDIUM: 2, LOW: 1, UNKNOWN: 0 };
export function berichtSortieren(zeilen, sort) {
  const zahl = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? null : Number(v));
  const schluessel = {
    rueckgang: (z) => zahl(z.delta_pct), anstieg: (z) => (zahl(z.delta_pct) === null ? null : -zahl(z.delta_pct)),
    deals: (z) => -(zahl(z.hot_deals) || 0), privat: (z) => -(zahl(z.private_hot_deals) || 0),
    liquiditaet: (z) => -(LIQ_RANG[z.liquiditaet] ?? 0), qualitaet: (z) => -(QUALITAET_SCHLECHT[z.data_quality] ?? 2),
    leer: (z) => -(zahl(z.empty_segmente) || 0), kosten: (z) => -(zahl(z.kosten_usd) || 0),
  }[sort];
  const liste = [...(zeilen || [])];
  if (!schluessel) return liste.sort((a, b) => String(a.label || "").localeCompare(String(b.label || ""), "de"));
  return liste.sort((a, b) => {
    const x = schluessel(a); const y = schluessel(b);
    if (x === null && y === null) return String(a.label || "").localeCompare(String(b.label || ""), "de");
    if (x === null) return 1;
    if (y === null) return -1;
    return x - y || String(a.label || "").localeCompare(String(b.label || ""), "de");
  });
}
