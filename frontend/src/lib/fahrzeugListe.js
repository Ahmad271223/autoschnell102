/*
 * Wunsch Ahmad 24.11.2026 — Fahrzeugliste (Bestand): Zeitraum-Filter,
 * Summe über alle Treffer, Mehrfachauswahl und Sammeländerung.
 * Reine Hilfen ohne React, damit sie einzeln testbar sind.
 */

/** Filter "Gekauft im Zeitraum" (backend/routes/bestand.py ZEITRAEUME). */
export const ZEITRAEUME = [
  { key: "", label: "Alle Zeiträume" },
  { key: "heute", label: "Heute" },
  { key: "diese_woche", label: "Diese Woche" },
  { key: "letzte_woche", label: "Letzte Woche" },
  { key: "dieser_monat", label: "Dieser Monat" },
  { key: "letzter_monat", label: "Letzter Monat" },
  { key: "eigener", label: "Eigener Zeitraum …" },
];

/** Ziele der Sammeländerung (backend SAMMEL_ZIELE) mit kurzem Hinweis. */
export const SAMMEL_ZIELE = [
  { key: "bestand", label: "Im Bestand",
    hinweis: "Abgeholte Fahrzeuge kommen in den Bestand, im Bestand wird die Frist auf 50 Tage ab heute erneuert." },
  { key: "archiviert", label: "Archiviert",
    hinweis: "Fotos und Inserats-Entwürfe werden gelöscht. Das lässt sich nicht rückgängig machen. Vertrag und Verlauf bleiben." },
  { key: "storniert", label: "Storniert",
    hinweis: "Nur ohne laufenden Kaufvertrag und ohne offenen Abholtermin." },
  { key: "verglichen", label: "Wieder aktiv (verglichen)",
    hinweis: "Holt stornierte Fahrzeuge zurück." },
];

export const JE_SEITE = 48;

export function zielText(key) {
  return SAMMEL_ZIELE.find((z) => z.key === key)?.label || key;
}

/**
 * Abfrage-Parameter für /bestand und /bestand/ids. Eigener Zeitraum nur mit
 * mindestens einem Datum — sonst ohne Zeitraum (der Server sagte sonst 422).
 */
export function listenParameter({ filter = "", quelle = "", zeitraum = "", von = "", bis = "",
  seite = null, jeSeite = null } = {}) {
  const p = new URLSearchParams();
  if (filter) p.set("lifecycle", filter);
  if (quelle) p.set("source", quelle);
  if (zeitraum && (zeitraum !== "eigener" || von || bis)) {
    p.set("zeitraum", zeitraum);
    if (zeitraum === "eigener") {
      if (von) p.set("von", von);
      if (bis) p.set("bis", bis);
    }
  }
  if (seite != null) p.set("seite", String(seite));
  if (jeSeite != null) p.set("je_seite", String(jeSeite));
  return p.toString();
}

/** Eigener Zeitraum: Fehlertext oder null. */
export function zeitraumFehler(zeitraum, von, bis) {
  if (zeitraum !== "eigener") return null;
  if (!von && !bis) return "Bitte „von“ oder „bis“ wählen.";
  if (von && bis && bis < von) return "„Bis“ liegt vor „von“.";
  return null;
}

/** Ein Fahrzeug an- oder abwählen (neues Set). */
export function auswahlUmschalten(auswahl, id) {
  const neu = new Set(auswahl);
  if (neu.has(id)) neu.delete(id); else neu.add(id);
  return neu;
}

/** Alle IDs der Seite an- (an=true) oder abwählen. */
export function seiteAuswaehlen(auswahl, ids, an) {
  const neu = new Set(auswahl);
  for (const id of ids) {
    if (an) neu.add(id); else neu.delete(id);
  }
  return neu;
}

export function seiteGanzGewaehlt(auswahl, ids) {
  return ids.length > 0 && ids.every((id) => auswahl.has(id));
}

/** "12.345 €" mit geschütztem Leerzeichen; null bei fehlendem Betrag. */
export function euro(n) {
  if (n == null || !Number.isFinite(Number(n))) return null;
  return `${Number(n).toLocaleString("de-DE", { maximumFractionDigits: 2 })} €`;
}

/** Kaufdatum als TT.MM.JJJJ in deutscher Zeit. */
export function kaufDatum(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("de-DE", { timeZone: "Europe/Berlin", day: "2-digit", month: "2-digit", year: "numeric" });
}

/** Rückfrage vor der Sammeländerung. */
export function sammelFrage(anzahl, ziel) {
  const was = anzahl === 1 ? "1 Fahrzeug" : `${anzahl} Fahrzeuge`;
  return `${was} auf „${zielText(ziel)}“ setzen?`;
}

/** Kurzfassung des Ergebnisses für die Meldung. */
export function sammelErgebnisText(r) {
  const g = (r?.geaendert || []).length;
  const u = (r?.uebersprungen || []).length;
  const teil1 = g === 1 ? "1 Fahrzeug geändert" : `${g} Fahrzeuge geändert`;
  return u ? `${teil1}, ${u} übersprungen` : teil1;
}
