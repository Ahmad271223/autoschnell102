/*
 * Zustand des Abhol-Protokolls in der Fahrer-App (Protokoll.jsx).
 *
 * Go-Live 13.09.2026 (N1/P2): "wird_abgeschlossen" fehlte. Brach ein langsamer
 * Abschluss ab (Cloudflare 524) und tippte der Fahrer erneut, lud die App neu
 * und zeigte ein wieder BEARBEITBARES Formular ohne Nachladen — jedes Tippen
 * speicherte automatisch und machte nach Ablauf des Claims aus der Freigabe
 * wieder einen Entwurf. Jetzt: gesperrt, still nachladen, Hinweis zeigen.
 *
 * Rollenprüfung 22.09.2026 (RP-071/RP-170): fail-closed. Vorher galt jeder
 * UNBEKANNTE Status (ein neuer Zustand auf dem Server, den diese App-Fassung
 * noch nicht kennt) als bearbeitbar — mit Autospeichern auf ein Protokoll, das
 * gar kein Entwurf mehr ist. Jetzt ist nur "entwurf" (bzw. noch kein
 * Protokoll) bearbeitbar; alles andere ist gesperrt und wird still
 * nachgeladen, bis ein bekannter Stand kommt.
 */
const BEKANNT = new Set(["entwurf", "zur_freigabe", "freigegeben", "wird_abgeschlossen", "final"]);

export function protokollZustand(status) {
  const s = status || "entwurf";
  const isFinal = s === "final";
  const wartetAufFreigabe = s === "zur_freigabe";
  const freigegeben = s === "freigegeben";
  const wirdAbgeschlossen = s === "wird_abgeschlossen";
  const unbekannt = !BEKANNT.has(s);
  return {
    isFinal,
    wartetAufFreigabe,
    freigegeben,
    wirdAbgeschlossen,
    // RP-071: unbekannter Status -> nicht bearbeitbar (Hinweis auf der Seite).
    unbekannt,
    // Keine Eingaben, kein Autospeichern — alles außer dem Entwurf.
    gesperrt: s !== "entwurf",
    // Selbst nachsehen, ob sich der Stand beim Chef bzw. im Abschluss aendert
    // (und ob ein unbekannter Stand inzwischen ein bekannter ist).
    nachladen: wartetAufFreigabe || freigegeben || wirdAbgeschlossen || unbekannt,
    // Die Unterschriften bleiben waehrend des Abschlusses stehen (nur gesperrt),
    // damit sie nach einem gescheiterten Abschluss nicht still verschwinden.
    unterschriften: freigegeben || wirdAbgeschlossen,
  };
}
