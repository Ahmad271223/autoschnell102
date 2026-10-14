/*
 * Bestandsformular der Fahrzeugakte (Standort, Notizen, Kosten).
 *
 * Rollenprüfung 22.09.2026:
 *   RP-449/RP-565/RP-146: Das Kostenfeld war <input type="number"> mit
 *     parseFloat. "1.200" wurde 1,20 €, "12,50" je nach Browser 1.250 € oder
 *     0 — und das floss in die Marge. Betraege stehen jetzt als TEXT im
 *     Formular und werden erst beim Speichern deutsch gelesen (preisAusText).
 *     Unlesbares blockiert das Speichern mit Hinweis.
 *   RP-462: "Nur speichern", "Änderungen übernehmen" und jedes Neuladen bauten
 *     das Formular aus dem Serverstand neu — ungespeicherte Kosten waren weg.
 *     Jetzt bleiben Felder, die seit dem letzten Serverstand geändert wurden,
 *     stehen (gleiche Regel wie mitOffenenAenderungen in den Einstellungen).
 *   RP-461: Der Serverstand (bestand.stand) geht beim Speichern mit — ein
 *     veralteter Tab überschreibt nichts mehr still.
 */
import { preisAusText } from "@/lib/preis";

const gleich = (x, y) => JSON.stringify(x ?? null) === JSON.stringify(y ?? null);

/** Betrag für das Textfeld: 1200.5 -> "1.200,5", leer/unsinnig -> "". */
export function betragAlsText(n) {
  if (n === null || n === undefined || n === "") return "";
  const zahl = Number(n);
  if (!Number.isFinite(zahl)) return "";
  return zahl.toLocaleString("de-DE", { maximumFractionDigits: 2, useGrouping: true });
}

/** Formular aus dem gelieferten bestand-Objekt (Beträge als Text). */
export function bestandFormAus(bestand) {
  const b = bestand || {};
  return {
    location: b.location || "",
    notes: b.notes || "",
    costs: (Array.isArray(b.costs) ? b.costs : []).map((c) => ({
      label: String(c?.label ?? ""),
      amount: betragAlsText(c?.amount),
    })),
    // "" = noch nie gespeichert; wird beim Speichern mitgeschickt.
    stand: typeof b.stand === "string" ? b.stand : "",
  };
}

/**
 * Nach einem Neuladen: Felder, die der Nutzer seit dem letzten Serverstand
 * geändert hat, bleiben stehen; alles andere kommt frisch vom Server. Der
 * Stand kommt IMMER vom Server — sonst meldete das Speichern danach 409.
 */
export function bestandMitOffenenAenderungen(alt, alterStand, neu) {
  if (!alt || !alterStand) return neu;
  const out = { ...neu };
  for (const k of ["location", "notes", "costs"]) {
    if (!gleich(alt[k], alterStand[k])) out[k] = alt[k];
  }
  return out;
}

/** Hat das Formular ungespeicherte Änderungen gegenüber dem Serverstand? */
export function bestandGeaendert(form, stand) {
  if (!form || !stand) return false;
  return ["location", "notes", "costs"].some((k) => !gleich(form[k], stand[k]));
}

/**
 * Kosten fürs Speichern lesen. Liefert { costs, fehler }:
 *   - ganz leere Zeilen fallen weg,
 *   - Betrag ohne Bezeichnung -> "Kosten" (wie der Server),
 *   - Bezeichnung ohne Betrag -> 0 €,
 *   - unlesbarer Betrag ("12,5,0", "-3", "abc") -> fehler (nicht speichern).
 */
export function kostenLesen(costs) {
  const out = [];
  for (const c of Array.isArray(costs) ? costs : []) {
    const label = String(c?.label ?? "").trim();
    const roh = String(c?.amount ?? "").trim();
    if (!label && !roh) continue;
    let amount = 0;
    if (roh) {
      const zahl = preisAusText(roh);
      if (zahl === null) {
        return {
          costs: null,
          fehler: `Kosten „${label || "ohne Bezeichnung"}“: „${roh}“ ist kein gültiger Betrag `
            + "(z. B. 1.200 oder 249,90).",
        };
      }
      amount = zahl;
    }
    out.push({ label: label || "Kosten", amount });
  }
  return { costs: out, fehler: null };
}

/**
 * Rollenprüfung 22.09.2026 (Review): Meldung nach "Frist erneuern". Der
 * Server setzt die Bestandsfrist auf 50 Tage AB HEUTE (nicht alte Frist
 * + 50 Tage) — die Meldung sagt das so und nennt, wenn geliefert, das neue
 * Enddatum.
 */
export function fristErneuertText(expiresAt) {
  const d = expiresAt ? new Date(expiresAt) : null;
  const bis = d && !Number.isNaN(d.getTime())
    ? ` (bis ${d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" })})`
    : "";
  return `Frist erneuert — das Fahrzeug bleibt ab heute 50 Tage im Bestand${bis}`;
}
