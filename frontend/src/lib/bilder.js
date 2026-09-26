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


/**
 * Bildformate, die der Server annimmt (backend/storage_service.py
 * validate_image_bytes: JPEG, PNG, WebP, GIF).
 */
const HOCHLADBAR = /^data:image\/(jpeg|jpg|pjpeg|png|webp|gif)[;,]/i;

/** Nimmt der Server diese data:-URL als Bild an? */
export function istHochladbaresBild(dataUrl) {
  return typeof dataUrl === "string" && HOCHLADBAR.test(dataUrl);
}

/**
 * Rollenprüfung 22.09.2026 (RP-533): Fehler für Fotos, die der Browser nicht
 * umwandeln kann (typisch HEIC vom iPhone am PC/Mac). Vorher ging die
 * Originaldatei trotzdem raus; der Server lehnte dann das GANZE Fotopaket
 * ab, und die folgenden Pakete wurden gar nicht mehr gesendet.
 */
export class BildFormatFehler extends Error {
  constructor(dateiname) {
    super(`${dateiname ? `„${dateiname}“: ` : ""}Dieses Bildformat (z. B. HEIC vom iPhone) `
      + "kann hier nicht verarbeitet werden — bitte als JPG speichern oder am iPhone unter "
      + "Einstellungen → Kamera → Formate „Maximale Kompatibilität“ wählen.");
    this.name = "BildFormatFehler";
  }
}

/**
 * Foto im Browser verkleinern, bevor es hochgeladen wird (Runde 21).
 * Handyfotos haben oft 4000 px und 5-8 MB; mit schwachem Netz dauert der
 * Upload beim Fahrer lange. Das Umzeichnen auf eine Leinwand entfernt
 * ausserdem alle Metadaten (Aufnahmezeit, Geraet, GPS-Position).
 * Liefert eine data:-URL (JPEG). Klappt es nicht, kommt die Originaldatei —
 * aber nur, wenn der Server ihr Format annimmt; sonst wirft die Funktion
 * einen BildFormatFehler (RP-533) mit einer Meldung fuer den Nutzer.
 */
export async function verkleinereBildDatei(file, { maxKante = 2000, qualitaet = 0.85 } = {}) {
  const original = async () => {
    const url = await new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(r.result);
      r.onerror = rej;
      r.readAsDataURL(file);
    });
    if (!istHochladbaresBild(url)) throw new BildFormatFehler(file?.name);
    return url;
  };
  try {
    if (typeof document === "undefined" || !file) return await original();
    let quelle = null;
    let breite = 0;
    let hoehe = 0;
    if (typeof createImageBitmap === "function") {
      quelle = await createImageBitmap(file, { imageOrientation: "from-image" });
      breite = quelle.width;
      hoehe = quelle.height;
    } else {
      const objUrl = URL.createObjectURL(file);
      try {
        quelle = await new Promise((res, rej) => {
          const i = new Image();
          i.onload = () => res(i);
          i.onerror = rej;
          i.src = objUrl;
        });
      } finally {
        setTimeout(() => URL.revokeObjectURL(objUrl), 0);
      }
      breite = quelle.naturalWidth;
      hoehe = quelle.naturalHeight;
    }
    if (!breite || !hoehe) return await original();
    const faktor = Math.min(1, maxKante / Math.max(breite, hoehe));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(breite * faktor));
    canvas.height = Math.max(1, Math.round(hoehe * faktor));
    const ctx = canvas.getContext("2d");
    if (!ctx) return await original();
    ctx.drawImage(quelle, 0, 0, canvas.width, canvas.height);
    if (typeof quelle.close === "function") quelle.close();
    const url = canvas.toDataURL("image/jpeg", qualitaet);
    return url && url.startsWith("data:image/jpeg") ? url : await original();
  } catch {
    return await original();
  }
}
