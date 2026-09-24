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
