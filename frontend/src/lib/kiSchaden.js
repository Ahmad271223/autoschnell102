// KI-Schadennachlass (Wunsch Ahmad 25./26.09.2026): reine Hilfsfunktionen ohne
// React — die festen Fragen je Schadensart (3–4 je Schaden, "unbekannt" ist
// eine gültige Antwort; erst wenn alles beantwortet ist, darf die KI ran —
// sie stellt keine Rückfragen mehr) und die Aufbereitung der Antwort mit
// vier Geldwerten (Mindestens / Fair / Sehr gut / Verhandlungsstart).

/** Feste Fragen je Schadensart der Skizze (type_key aus DamageSelector). */
export const SCHWERE_FRAGEN = {
  delle: [
    { key: "groesse", label: "Größe", options: ["bis 2 cm", "2–5 cm", "5–10 cm", "über 10 cm", "unbekannt"] },
    { key: "lack", label: "Lack beschädigt?", options: ["nein", "ja", "unbekannt"] },
    { key: "lage", label: "Lage", options: ["Fläche", "Kante/Sicke", "unbekannt"] },
  ],
  kratzer: [
    { key: "laenge", label: "Länge", options: ["bis 5 cm", "5–15 cm", "15–30 cm", "über 30 cm", "unbekannt"] },
    { key: "tiefe", label: "Tiefe", options: ["oberflächlich", "bis Grundierung", "bis Blech", "unbekannt"] },
    { key: "anzahl", label: "Anzahl", options: ["einzeln", "mehrere", "unbekannt"] },
  ],
  rost: [
    { key: "umfang", label: "Umfang", options: ["oberflächlich", "Blasen", "durchgerostet", "unbekannt"] },
    { key: "groesse", label: "Größe", options: ["bis 5 cm", "5–15 cm", "über 15 cm", "unbekannt"] },
    { key: "stelle", label: "Stelle", options: ["Fläche", "Kante/Falz", "tragendes Teil", "unbekannt"] },
  ],
  hagelschaden: [
    { key: "umfang", label: "Umfang", options: ["wenige (unter 10)", "viele (10–30)", "sehr viele (über 30)", "ganzes Fahrzeug"] },
    { key: "dellengroesse", label: "Dellengröße", options: ["klein (bis 1 cm)", "mittel (1–3 cm)", "groß", "unbekannt"] },
    { key: "lack", label: "Lack beschädigt?", options: ["nein", "ja", "unbekannt"] },
  ],
  steinschlag: [
    { key: "wo", label: "Wo?", options: ["Lack", "Windschutzscheibe", "andere Scheibe"] },
    { key: "umfang", label: "Umfang", options: ["einzeln", "mehrere", "Riss/flächig"] },
    { key: "tiefe", label: "Tiefe", options: ["nur Deckschicht", "bis Grundierung/Blech", "unbekannt"] },
  ],
  beleuchtung: [
    { key: "welches", label: "Welches Licht?", options: ["Scheinwerfer", "Rückleuchte", "Blinker/Nebel", "andere"] },
    { key: "funktion", label: "Funktion", options: ["eingeschränkt", "komplett ausgefallen", "Gehäuse beschädigt", "unbekannt"] },
    { key: "technik", label: "Technik", options: ["Halogen", "Xenon", "LED", "unbekannt"] },
  ],
  unfall_repariert: [
    { key: "nachweis", label: "Reparatur belegt?", options: ["Rechnung vorhanden", "kein Beleg", "unbekannt"] },
    { key: "umfang", label: "Umfang", options: ["Blech", "Blech + Rahmen", "unbekannt"] },
    { key: "qualitaet", label: "Ausführung", options: ["fachgerecht", "sichtbare Mängel", "unbekannt"] },
  ],
  unfall_nicht_repariert: [
    { key: "umfang", label: "Umfang", options: ["Blech", "Blech + Rahmen", "unbekannt"] },
    { key: "fahrbereit", label: "Fahrbereit?", options: ["ja", "nein", "unbekannt"] },
    { key: "airbag", label: "Airbag", options: ["nicht ausgelöst", "ausgelöst", "unbekannt"] },
  ],
  // Technischer Mangel (Wunsch Ahmad 25.09.2026 abends): kein Punkt auf der
  // Skizze, sondern Bereich (beim Anlegen) + Stand + Fahrbereitschaft +
  // Warnleuchte + kurze Beschreibung. Ohne bestätigte Diagnose liefert die KI
  // "Diagnose erforderlich" mit Diagnosekosten und drei Szenarien.
  technik: [
    { key: "status", label: "Stand", options: ["nur Symptom bemerkt", "Werkstatt hat Diagnose bestätigt", "unbekannt"] },
    { key: "fahrbereit", label: "Fahrbereit?", options: ["ja", "eingeschränkt", "nein", "unbekannt"] },
    { key: "warnleuchte", label: "Warnleuchte", options: ["keine", "leuchtet", "unbekannt"] },
    // nur bei bestätigter Diagnose (Review 25.09.2026 abends): Umfang und Kostenvoranschlag
    { key: "umfang", label: "Umfang lt. Werkstatt", nurWenn: { status: "Werkstatt hat Diagnose bestätigt" },
      options: ["Kleinteil/Einstellung", "Bauteil tauschen", "Instandsetzung/Überholung", "Austauschaggregat", "unbekannt"] },
    { key: "kva", label: "Kostenvoranschlag", nurWenn: { status: "Werkstatt hat Diagnose bestätigt" },
      options: ["liegt vor", "keiner", "unbekannt"], betragBei: "liegt vor", betragKey: "kva_eur" },
  ],
};

/** Fragen, die zum aktuellen Stand des Schadens gehören (Folgefragen nur, wenn ihre Bedingung erfüllt ist). */
export function fragenFuer(damage) {
  const sd = damage?.severity_data || {};
  return schwereFragen(damage?.type_key).filter((f) => !f.nurWenn
    || Object.entries(f.nurWenn).every(([k, v]) => sd[k] === v));
}

/** Technischer Mangel: eigene Schadensart ohne Skizzenpunkt. */
export const TECHNIK_TYP = { key: "technik", abbr: "TM", label: "Technischer Mangel", color: "#f97316" };
export const TECHNIK_BEREICHE = [
  "Motor", "Getriebe/Kupplung", "Fahrwerk/Bremsen/Lenkung", "Elektrik/Elektronik", "Klima/Heizung",
  "Fensterheber/Verriegelung/Sitze", "Auspuff/Abgas", "Batterie/Start", "Innenraum",
];
export function istTechnik(d) {
  return (d?.type_key || "") === TECHNIK_TYP.key;
}
export function technikSchaden(bereich, id) {
  return {
    id: id || `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    view: "technik", type_key: TECHNIK_TYP.key, type_label: TECHNIK_TYP.label, abbr: TECHNIK_TYP.abbr,
    color: TECHNIK_TYP.color, zone: bereich, note: "", severity_data: { bereich },
  };
}

export function schwereFragen(typeKey) {
  return SCHWERE_FRAGEN[typeKey] || [];
}

/** Kurztext der beantworteten Merkmale, z. B. "2–5 cm · nein · Fläche". */
export function schwereText(damage) {
  const sd = damage?.severity_data || {};
  const teile = fragenFuer(damage).map((f) => {
    const w = sd[f.key];
    if (w && f.betragBei && w === f.betragBei && sd[f.betragKey]) return `${w} (${sd[f.betragKey]} €)`;
    return w;
  }).filter(Boolean);
  return teile.join(" · ");
}

/** Noch nicht beantwortete Fragen eines Schadens ("unbekannt" gilt als Antwort). */
export function schwereOffen(damage) {
  const sd = damage?.severity_data || {};
  const offen = fragenFuer(damage).filter((f) => !sd[f.key]).map((f) => f.label);
  for (const f of fragenFuer(damage)) {
    if (f.betragBei && sd[f.key] === f.betragBei && !String(sd[f.betragKey] || "").trim()) offen.push("Betrag");
  }
  return offen;
}

/** Betrag zu einer Betragsfrage setzen (nur Ziffern). */
export function mitBetrag(damage, key, wert) {
  const sd = { ...(damage.severity_data || {}) };
  const ziffern = String(wert || "").replace(/[^0-9]/g, "").slice(0, 6);
  if (ziffern) sd[key] = ziffern; else delete sd[key];
  return { ...damage, severity_data: sd };
}

/** Sind alle Schäden vollständig beschrieben? (Bedingung für "Schäden bewerten") */
export function alleVollstaendig(damages) {
  return (damages || []).every((d) => schwereOffen(d).length === 0);
}

export function mitAntwort(damage, key, wert) {
  const sd = { ...(damage.severity_data || {}) };
  if (sd[key] === wert) delete sd[key]; else sd[key] = wert;
  return { ...damage, severity_data: sd };
}

// ---------------------------------------------------------------- Anzeige
export const PRIO_TEXT = { rot: "Stark preisrelevant", orange: "Preisrelevant", gelb: "Geringer Einfluss" };
export const PRIO_FARBE = { rot: "var(--st-rot)", orange: "var(--st-amber)", gelb: "var(--text-secondary)" };
export const DATENLAGE_TEXT = { hoch: "Datenlage hoch", mittel: "Datenlage mittel", niedrig: "Datenlage niedrig" };
export const DATENLAGE_FARBE = { hoch: "var(--st-gruen)", mittel: "var(--st-amber)", niedrig: "var(--st-rot)" };
export const ART_TEXT = {
  repair_estimate: "", diagnosis_required: "Diagnose erforderlich", expert_check_required: "Fachprüfung erforderlich",
};
/** Szenarien einer Diagnose-Position als kurzer Text. */
export function szenarienText(it) {
  if (!it || it.assessment_kind !== "diagnosis_required") return "";
  const teile = [];
  if (it.diagnosis_cost_eur > 0) teile.push(`Diagnose ca. ${eur(it.diagnosis_cost_eur)}`);
  if (it.scenario_high_eur > 0) {
    teile.push(`Szenarien: günstig ${eur(it.scenario_low_eur)} · mittel ${eur(it.scenario_mid_eur)} · aufwendig ${eur(it.scenario_high_eur)}`);
  }
  return teile.join(" · ");
}
export const RISIKO_TEXT = {
  normal: "",
  high: "Hohes Preisrisiko – Zahlen mit Vorsicht, ggf. Werkstattprüfung vor dem Kauf.",
  reconsider_purchase: "Die Mängel stellen den Kauf wirtschaftlich in Frage – bitte neu bewerten statt normal nachverhandeln.",
};

export function eur(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  return `${Math.round(Number(n)).toLocaleString("de-DE")} €`;
}

/** Die vier Geldwerte einer Position/Gesamt als kurzer Text. */
export function vierText(x) {
  if (!x) return "";
  return `mind. ${eur(x.minimum_justified_eur)} · sehr gut ${eur(x.best_realistic_eur)} · Start ${eur(x.negotiation_start_eur)}`;
}

/** Positionen nach Prioritaet gruppiert (rot, orange, gelb). */
export function nachPrioritaet(items) {
  const g = { rot: [], orange: [], gelb: [] };
  for (const it of items || []) (g[it.priority] || g.gelb).push(it);
  return g;
}

/** Text fuer den Zustand der Karte. */
export function kiStatusText(status) {
  switch (status) {
    case "laeuft": return "KI bewertet die Abweichungen …";
    case "veraltet": return "Bewertung veraltet – wird neu berechnet …";
    case "keine": return "Keine preisrelevanten Abweichungen festgestellt.";
    case "aus": return "KI-Bewertung ist nicht eingeschaltet.";
    case "freischaltung": return "KI-Bewertung für dieses Konto nicht freigeschaltet – der Betreiber schaltet sie wie das Abo frei.";
    case "zeitlimit": return "KI-Einschätzung momentan nicht verfügbar (Zeitlimit).";
    case "ueberlastet": return "KI-Dienst überlastet – bitte später neu berechnen.";
    case "schluessel": return "KI-Schlüssel fehlt oder ist ungültig (Einstellung auf dem Server).";
    case "abgelehnt": return "KI hat diese Anfrage nicht bewertet.";
    case "fehler": return "KI-Einschätzung momentan nicht verfügbar.";
    case "limit": return "Stundenlimit für KI-Bewertungen erreicht – bitte später erneut.";
    case "budget": return "Monatsbudget für KI-Bewertungen aufgebraucht – ab dem 1. wieder verfügbar.";
    case "netz": return "Keine Verbindung zum Server – bitte erneut versuchen.";
    default: return "";
  }
}

/** Sind wir noch am Warten (Karte fragt weiter nach)? */
export function kiWartet(status) {
  return status === "laeuft" || status === "veraltet";
}

/** Argumente als Text zum Kopieren. */
export function argumenteText(args) {
  return (args || []).map((a, i) => `${i + 1}. ${a}`).join("\n");
}

// ---------------------------------------------------------------- Vertrag (Stufe 3)
/** Anzeigename der Vertragsfelder, die das Inserat vorbelegen kann. */
export const FELD_LABEL = {
  schluessel_anzahl: "Schlüssel", hu_valid: "HU/AU vorhanden", hu_until: "HU gültig bis",
  service_book: "Scheckheftgepflegt", accident_free: "Unfallfrei", drivable: "Fahrtauglich",
  eu_import: "EU-Import", tires: "Bereifung",
};

const WERT_TEXT = { ja: "Ja, lückenlos", nein: "Nein", teilweise: "Teilweise" };

export function vorschlagWertText(feld, wert) {
  if (feld === "service_book") return WERT_TEXT[String(wert).toLowerCase()] || String(wert);
  return String(wert);
}

/**
 * Vorschläge aus dem Inserat (GET /contracts/vorschlaege/{id}) in das Formular
 * übernehmen — NUR in leere Felder, nie in Felder, die der Nutzer schon
 * angefasst hat (gesperrt). Liefert das neue Formular und die Liste dessen,
 * was übernommen wurde (mit Fundstelle), damit der Dialog es zeigt.
 */
export function vorschlaegeAnwenden(form, vorschlaege, gesperrt = {}) {
  const felder = vorschlaege?.felder || {};
  const neu = { ...form };
  const uebernommen = [];
  for (const [feld, v] of Object.entries(felder)) {
    if (!v || v.value === undefined || v.value === null || v.value === "") continue;
    if (!(feld in form) || gesperrt[feld]) continue;
    if (String(form[feld] ?? "").trim() !== "") continue;
    if (feld === "hu_until") {
      const hu = String(neu.hu_valid || "");
      if (hu !== "Ja") continue;
    }
    neu[feld] = String(v.value);
    uebernommen.push({ feld, label: FELD_LABEL[feld] || feld, wert: vorschlagWertText(feld, v.value),
                       fund: v.source_text || "" });
  }
  if (uebernommen.some((u) => u.feld === "schluessel_anzahl")) neu.empfang_schluessel = true;
  return { form: neu, uebernommen, hinweise: vorschlaege?.hinweise || [] };
}

/** Eine Zeile je Schaden für "Sind das alle Schäden?" */
export function schadenZeile(d) {
  const teile = [d?.type_label || d?.type_key || "Schaden", d?.zone].filter(Boolean);
  const s = schwereText(d);
  const note = istTechnik(d) && d?.note ? ` – „${String(d.note).trim()}“` : "";
  return teile.join(" – ") + (s ? ` – ${s}` : "") + note;
}

/** Fingerabdruck der Schäden (Art, Bauteil, Zusatzangaben) — ändert er sich
 *  nach einer Bewertung, ist die Karte veraltet. */
export function schaedenStand(damages) {
  return JSON.stringify((damages || []).map((d) => [d.id, d.type_key, d.zone, d.severity_data || {}, d.note || ""]));
}
