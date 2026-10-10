/**
 * Text in die Zwischenablage legen (Wunsch Ahmad 21.09.2026: Vorlagen, die
 * die App nicht verschickt — der Nutzer kopiert sie und fügt sie in seine
 * eigene E-Mail oder WhatsApp ein).
 *
 * Erst die Zwischenablage-Schnittstelle; wo die fehlt oder gesperrt ist
 * (kein sicherer Kontext, ältere Browser), der alte Weg über ein
 * unsichtbares Textfeld. Liefert true, wenn der Text drin ist.
 */
export async function textKopieren(text) {
  const wert = String(text ?? "");
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(wert);
      return true;
    }
  } catch {
    // weiter mit dem alten Weg
  }
  if (typeof document === "undefined") return false;
  const feld = document.createElement("textarea");
  feld.value = wert;
  feld.setAttribute("readonly", "");
  feld.style.position = "fixed";
  feld.style.top = "0";
  feld.style.opacity = "0";
  document.body.appendChild(feld);
  try {
    feld.select();
    return document.execCommand("copy") === true;
  } catch {
    return false;
  } finally {
    document.body.removeChild(feld);
  }
}
