/**
 * Vorschaubilder ueber den eigenen Bild-Proxy (10.09.2026).
 *
 * Der Server liefert zu Portal-Fotos signierte Adressen "/api/bild?u=…"
 * (klein, zwischengespeichert, kein Fremdhost im Browser). Diese Helfer
 * machen daraus eine ladbare Adresse und fallen sauber auf das Original
 * zurueck, wenn der Proxy nichts liefert.
 */
const BACKEND = process.env.REACT_APP_BACKEND_URL || "";

/** Proxy-Adresse absolut machen; fehlt sie, das Original. */
export function thumbSrc(thumb, original) {
  if (typeof thumb === "string" && thumb) {
    return thumb.startsWith("/") ? `${BACKEND}${thumb}` : thumb;
  }
  return original || "";
}

/** Beim Laden gescheitert -> einmal auf das Original umschalten. */
export function thumbFehler(e, original) {
  const el = e?.currentTarget;
  if (!el || !original || el.src === original) return;
  el.src = original;
}
