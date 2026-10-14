/**
 * WhatsApp-Versand vom Handy (09.09.2026): Das Vertrags-PDF wird ueber das
 * Teilen-Menue des Geraets (Web Share API) an WhatsApp uebergeben — von der
 * eigenen Nummer des Suchers, mit angehaengter Datei. Nur den Chat waehlt
 * der Nutzer selbst; WhatsApp erlaubt keine Vorauswahl der Nummer.
 *
 * Am PC (kein Datei-Teilen) bleibt der wa.me-Weg: der Server haengt einen
 * zeitlich begrenzten Download-Link an die Nachricht.
 */

/** Kann dieses Geraet die Datei ueber das Teilen-Menue weitergeben? */
export function kannDateiTeilen(datei, nav = typeof navigator !== "undefined" ? navigator : undefined) {
  try {
    if (!nav || typeof nav.share !== "function" || typeof nav.canShare !== "function") return false;
    if (!datei) return false;
    return nav.canShare({ files: [datei] }) === true;
  } catch {
    return false;
  }
}

/**
 * Teilt die Datei. Ergebnis:
 *   "geteilt"        — an die gewaehlte App uebergeben
 *   "abgebrochen"    — der Nutzer hat das Teilen-Menue geschlossen
 *   "erneut"         — der Browser verlangt eine frische Nutzeraktion
 *                      (NotAllowedError: Tipp zu alt, schon ein Teilen offen)
 *                      -> noch einmal tippen, NICHT der Link-Weg
 *   "nicht_moeglich" — Geraet/Browser kann es nicht (-> Link-Weg)
 *
 * Pruefbericht 20.09.2026 (K-14): NotAllowedError lief bisher als
 * "nicht_moeglich" — der Versand-Dialog oeffnete dann ohne Rueckfrage den
 * Chat mit oeffentlichem Download-Link, obwohl das Geraet teilen KANN.
 */
export async function dateiTeilen({ datei, text, titel }, nav = typeof navigator !== "undefined" ? navigator : undefined) {
  if (!kannDateiTeilen(datei, nav)) return "nicht_moeglich";
  try {
    await nav.share({ files: [datei], text: text || "", title: titel || "" });
    return "geteilt";
  } catch (e) {
    if (e && e.name === "AbortError") return "abgebrochen";
    if (e && e.name === "NotAllowedError") return "erneut";
    return "nicht_moeglich";
  }
}

/** Blob -> File mit sauberem Namen (WhatsApp zeigt den Dateinamen an). */
export function pdfDatei(blob, name = "Kaufvertrag.pdf") {
  const sauber = String(name || "Kaufvertrag.pdf").replace(/[^\w.\-äöüÄÖÜß ]+/g, "_");
  const endung = sauber.toLowerCase().endsWith(".pdf") ? sauber : `${sauber}.pdf`;
  return new File([blob], endung, { type: "application/pdf" });
}
