import { toast } from "sonner";

/**
 * Geladene Datei (PDF, Foto) in einem neuen Tab öffnen — auch dann, wenn das
 * Laden länger gedauert hat.
 *
 * Prüfbericht 20.09.2026 (B15/F14/H7/H20/M36): Die Datei wurde erst geladen
 * und DANACH per unsichtbarem Link-Klick geöffnet. Browser erlauben einen
 * neuen Tab aber nur kurz nach einem echten Klick (Chrome etwa 5 Sekunden,
 * Safari deutlich kürzer). Brauchte der Server länger — ein Kaufvertrag mit
 * Fotos am Handy —, verwarf der Browser das Öffnen still: kein Tab, keine
 * Meldung, und jeder weitere Tipp startete den nächsten langen Download.
 *
 * Jetzt: Ist der Klick noch „frisch", öffnet sich der Tab wie bisher. Sonst
 * erscheint ein Hinweis mit Knopf — dessen Klick ist die neue Nutzeraktion,
 * die der Browser verlangt. Außerdem wird die Blob-Adresse nicht mehr nach
 * einer Sekunde freigegeben (am Handy blieb der neue Tab dann weiß), sondern
 * nach zehn Minuten.
 */

/** Ohne navigator.userActivation (ältere Browser) gilt ein Klick so lange als frisch. */
const FRIST_OHNE_API_MS = 1000;
/** So lange bleibt die Blob-Adresse gültig (Drucken/Neuladen im Betrachter). */
const FREIGABE_NACH_MS = 10 * 60 * 1000;

/** Darf die Seite jetzt noch ohne neuen Klick einen Tab öffnen? */
export function klickNochFrisch(startMs, jetzt = Date.now(), nav = typeof navigator !== "undefined" ? navigator : null) {
  try {
    const ua = nav?.userActivation;
    if (ua && typeof ua.isActive === "boolean") return ua.isActive;
  } catch { /* alte Browser */ }
  return jetzt - startMs <= FRIST_OHNE_API_MS;
}

function linkKlicken(blobUrl, dateiname) {
  const a = document.createElement("a");
  a.href = blobUrl;
  if (dateiname) {
    a.download = dateiname;
  } else {
    a.target = "_blank";
    a.rel = "noopener noreferrer";
  }
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 1000);
}

/**
 * blob: die geladene Datei. startMs: Zeitpunkt des Klicks (vor dem Laden).
 * titel: für den Hinweis ("Der Kaufvertrag ist fertig."). dateiname: statt
 * Tab herunterladen. mime: Dateityp erzwingen (z. B. "application/pdf").
 */
export function blobOeffnen(blob, { startMs = Date.now(), titel = "Das Dokument", dateiname = null, mime = null } = {}) {
  const daten = mime && blob && blob.type !== mime ? new Blob([blob], { type: mime }) : blob;
  const blobUrl = URL.createObjectURL(daten);
  setTimeout(() => URL.revokeObjectURL(blobUrl), FREIGABE_NACH_MS);
  if (klickNochFrisch(startMs)) {
    linkKlicken(blobUrl, dateiname);
    return "geoeffnet";
  }
  toast.success(`${titel} ist fertig.`, {
    duration: 60000,
    action: { label: dateiname ? "Speichern" : "Öffnen", onClick: () => linkKlicken(blobUrl, dateiname) },
  });
  return "hinweis";
}

/** Blob-Adresse ohne Klick in einem neuen Tab (Ersatzweg beim Drucken). */
export function blobUrlImTab(blobUrl) {
  linkKlicken(blobUrl, null);
}
