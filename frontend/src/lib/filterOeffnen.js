/**
 * Runde 22 (11.09.2026): Filter der Portale oeffnen — mit Hinweis, wenn der
 * Browser etwas blockiert.
 *
 * Chrome/Edge lassen je Klick nur EIN neues Fenster zu; beim automatischen
 * Oeffnen nach dem Auslesen ist die Klick-Erlaubnis oft schon abgelaufen.
 * Statt still zu scheitern, erscheint EIN Hinweis: einmalig Pop-ups
 * erlauben (dann oeffnet sich kuenftig alles von selbst) oder per Knopf
 * das naechste Portal oeffnen — der Knopfdruck ist eine neue Geste.
 *
 * Eintrag = { url, name, label } (label z.B. "mobile.de" / "AutoScout24").
 * Rueckgabe: Liste der blockierten Eintraege ([] = alle offen).
 */
import { toast } from "sonner";
// Relativ statt "@/lib/popup": Jest kennt den "@"-Alias nicht (popup.test.js).
import { openMultiple } from "./popup";

export const FILTER_TOAST_ID = "filter-blockiert";

export const POPUP_ERLAUBEN_HINWEIS =
  "Einmalig Pop-ups erlauben, dann öffnen sich alle Filter von selbst: " +
  "Rechts in der Adresszeile auf das Symbol für blockierte Pop-ups klicken → " +
  "„Immer zulassen“ wählen (Chrome: „Pop-ups und Weiterleitungen … immer zulassen“, " +
  "Edge: „Pop-ups und Umleitungen … immer zulassen“) → Fertig.";

export function filterOeffnen(eintraege, { automatisch = false } = {}) {
  const blockiert = openMultiple(eintraege || []);
  if (blockiert.length === 0) {
    // Runde 22 (11.09.2026, Gegenpruefung): Ein stehender Hinweis gehoert zu
    // einem frueheren Aufruf — sein Knopf wuerde den benannten Tab (der jetzt
    // das neue Fahrzeug zeigt) auf den Filter des alten Fahrzeugs lenken.
    toast.dismiss(FILTER_TOAST_ID);
    return blockiert;
  }

  const labels = blockiert.map((e) => e.label || "Filter");
  const titel = automatisch
    ? "Automatisches Öffnen vom Browser blockiert"
    : `${labels.join(" und ")} vom Browser blockiert`;
  // Feste id: jeder weitere Aufruf ersetzt den Hinweis, statt zu stapeln.
  toast.warning(titel, {
    id: FILTER_TOAST_ID,
    duration: 20000,
    description: POPUP_ERLAUBEN_HINWEIS,
    action: {
      label: `${labels[0]} öffnen`,
      onClick: (event) => {
        const rest = filterOeffnen(blockiert);
        // sonner schliesst den Hinweis nach dem Klick — bleibt noch etwas
        // blockiert, soll der eben aktualisierte Hinweis stehen bleiben.
        if (rest.length > 0) event?.preventDefault?.();
      },
    },
  });
  return blockiert;
}
