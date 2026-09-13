/*
 * Zustand des Abhol-Protokolls in der Fahrer-App (Protokoll.jsx).
 *
 * Go-Live 13.09.2026 (N1/P2): "wird_abgeschlossen" fehlte. Brach ein langsamer
 * Abschluss ab (Cloudflare 524) und tippte der Fahrer erneut, lud die App neu
 * und zeigte ein wieder BEARBEITBARES Formular ohne Nachladen — jedes Tippen
 * speicherte automatisch und machte nach Ablauf des Claims aus der Freigabe
 * wieder einen Entwurf. Jetzt: gesperrt, still nachladen, Hinweis zeigen.
 */
export function protokollZustand(status) {
  const s = status || "entwurf";
  const isFinal = s === "final";
  const wartetAufFreigabe = s === "zur_freigabe";
  const freigegeben = s === "freigegeben";
  const wirdAbgeschlossen = s === "wird_abgeschlossen";
  return {
    isFinal,
    wartetAufFreigabe,
    freigegeben,
    wirdAbgeschlossen,
    // Keine Eingaben, kein Autospeichern.
    gesperrt: isFinal || wartetAufFreigabe || freigegeben || wirdAbgeschlossen,
    // Selbst nachsehen, ob sich der Stand beim Chef bzw. im Abschluss aendert.
    nachladen: wartetAufFreigabe || freigegeben || wirdAbgeschlossen,
    // Die Unterschriften bleiben waehrend des Abschlusses stehen (nur gesperrt),
    // damit sie nach einem gescheiterten Abschluss nicht still verschwinden.
    unterschriften: freigegeben || wirdAbgeschlossen,
  };
}
