// KI-Schadennachlass (Wunsch Ahmad 25.09.2026): reine Hilfsfunktionen ohne
// React — die Zusatzfragen je Schadensart (wenige Sekunden fuer den Fahrer,
// grosser Gewinn fuer die Schaetzung) und die Aufbereitung der KI-Antwort.

/** Zusatzfragen je Schadensart der Skizze (type_key aus DamageSelector). */
export const SCHWERE_FRAGEN = {
  delle: [
    { key: "groesse", label: "Größe", options: ["bis 2 cm", "2–5 cm", "5–10 cm", "über 10 cm"] },
    { key: "lack", label: "Lack beschädigt?", options: ["nein", "ja", "unbekannt"] },
  ],
  kratzer: [
    { key: "laenge", label: "Länge", options: ["bis 5 cm", "5–15 cm", "15–30 cm", "über 30 cm"] },
    { key: "tiefe", label: "Tiefe", options: ["oberflächlich", "tief", "unbekannt"] },
  ],
  steinschlag: [
    { key: "wo", label: "Wo?", options: ["Lack", "Windschutzscheibe", "andere Scheibe"] },
    { key: "umfang", label: "Umfang", options: ["einzeln", "mehrere", "Riss"] },
  ],
  rost: [
    { key: "umfang", label: "Umfang", options: ["oberflächlich", "Blasen", "durchgerostet"] },
    { key: "groesse", label: "Größe", options: ["bis 5 cm", "5–15 cm", "über 15 cm"] },
  ],
  hagelschaden: [
    { key: "umfang", label: "Umfang", options: ["wenige Dellen", "viele Dellen", "ganzes Fahrzeug"] },
  ],
  beleuchtung: [
    { key: "funktion", label: "Funktion", options: ["eingeschränkt", "komplett ausgefallen", "Gehäuse beschädigt"] },
  ],
  unfall_repariert: [
    { key: "nachweis", label: "Reparatur belegt?", options: ["Rechnung vorhanden", "kein Beleg", "unbekannt"] },
  ],
  unfall_nicht_repariert: [
    { key: "umfang", label: "Umfang", options: ["Blech", "Blech + Rahmen", "unbekannt"] },
  ],
};

export function schwereFragen(typeKey) {
  return SCHWERE_FRAGEN[typeKey] || [];
}

/** Kurztext der beantworteten Merkmale, z. B. "2–5 cm · Lack nein". */
export function schwereText(damage) {
  const sd = damage?.severity_data || {};
  const fragen = schwereFragen(damage?.type_key);
  const teile = fragen.map((f) => sd[f.key]).filter(Boolean);
  return teile.join(" · ");
}

/** Fehlen dem Schaden noch Antworten? (fuer den Hinweis vor dem Abschicken) */
export function schwereOffen(damage) {
  const sd = damage?.severity_data || {};
  return schwereFragen(damage?.type_key).filter((f) => !sd[f.key]).map((f) => f.label);
}

export function mitAntwort(damage, key, wert) {
  const sd = { ...(damage.severity_data || {}) };
  if (sd[key] === wert) delete sd[key]; else sd[key] = wert;
  return { ...damage, severity_data: sd };
}

// ---------------------------------------------------------------- Anzeige
export const PRIO_TEXT = { rot: "Stark preisrelevant", orange: "Preisrelevant", gelb: "Geringer Einfluss" };
export const PRIO_FARBE = { rot: "var(--st-rot)", orange: "var(--st-amber)", gelb: "var(--text-secondary)" };

export function eur(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  return `${Math.round(Number(n)).toLocaleString("de-DE")} €`;
}

export function prozent(p) {
  return `${Math.round(Number(p || 0) * 100)} %`;
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
    case "zeitlimit": return "KI-Einschätzung momentan nicht verfügbar (Zeitlimit).";
    case "ueberlastet": return "KI-Dienst überlastet – bitte später neu berechnen.";
    case "schluessel": return "KI-Schlüssel fehlt oder ist ungültig (Einstellung auf dem Server).";
    case "abgelehnt": return "KI hat diese Anfrage nicht bewertet.";
    case "fehler": return "KI-Einschätzung momentan nicht verfügbar.";
    case "limit": return "Stundenlimit für KI-Bewertungen erreicht – bitte später erneut.";
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
  return teile.join(" – ") + (s ? ` – ${s}` : "");
}

/** Fingerabdruck der Schäden (Art, Bauteil, Zusatzangaben) — ändert er sich
 *  nach einer Bewertung, ist die Karte veraltet. */
export function schaedenStand(damages) {
  return JSON.stringify((damages || []).map((d) => [d.id, d.type_key, d.zone, d.severity_data || {}]));
}

/** Schlüssel für eine KI-Rückfrage im severity_data des Schadens. */
export function frageSchluessel(question) {
  const s = String(question || "").toLowerCase().replace(/[^a-z0-9äöüß]+/g, "_").replace(/^_+|_+$/g, "");
  return ("frage_" + s).slice(0, 40);
}

/** Antwort auf eine KI-Rückfrage am passenden Schaden ablegen. */
export function mitRueckfrageAntwort(damages, frage, antwort) {
  const key = frageSchluessel(frage?.question);
  return (damages || []).map((d) => (String(d.id) === String(frage?.source_id)
    ? { ...d, severity_data: { ...(d.severity_data || {}), [key]: antwort } } : d));
}
