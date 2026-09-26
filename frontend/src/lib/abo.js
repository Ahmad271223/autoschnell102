/*
 * Abo-Anzeige (Rollenprüfung 22.09.2026, RP-010/RP-109/RP-260).
 *
 * Die Probe-Abos, die der Betreiber vergibt (backend/routes/admin.py,
 * deps.py: probe3/probe5), standen als Rohschlüssel "probe3" auf der
 * Abo-Karte; die Team-Seite zeigte jedes aktive Nicht-Jahresabo als
 * "monatlich". Eine Tabelle für alle Stellen.
 */
export const PLAN_LABEL = {
  monthly:  "Monatsabo",
  yearly:   "Jahresabo",
  lifetime: "Lifetime",
  trial:    "Test-Phase",
  probe3:   "Probe (3 Tage)",
  probe5:   "Probe (5 Tage)",
};

/** Anzeigename eines Plans; Unbekanntes lesbar statt Rohschlüssel. */
export function planText(plan) {
  if (!plan) return "—";
  if (PLAN_LABEL[plan]) return PLAN_LABEL[plan];
  const probe = /^probe(\d+)$/.exec(String(plan));
  if (probe) return `Probe (${probe[1]} Tage)`;
  return String(plan);
}

/**
 * Rollenprüfung 22.09.2026 (RP-006/RP-105): Weicht der frisch geladene
 * Abo-Stand vom Anmelde-Kontext ab? Dann muss der Kontext nachgeladen werden
 * — sonst zeigte die Karte "Aktiv", während die Routensperre (ProtectedRoute)
 * mit dem alten "kein Abo" weiter auf /abo umleitete, bis zum Neuladen.
 */
export function aboKontextVeraltet(kontextAbo, geladen) {
  if (!geladen) return false;
  return Boolean(kontextAbo?.active) !== Boolean(geladen.active);
}
