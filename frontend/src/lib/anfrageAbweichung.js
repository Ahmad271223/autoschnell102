import { errMsg } from "@/lib/api";

/**
 * Nachpruefung 15.09.2026 (Konten, Freigabe-Bindung): weichen E-Mail oder Firma
 * der Eingabe von der Zugangs-Anfrage ab, antwortet das Backend 409 mit dem
 * Hinweis "daten_geaendert". Nach ausdruecklicher Rueckfrage wird bewusst mit
 * daten_geaendert=true angelegt — sonst bleibt die Anlage abgelehnt.
 * `senden(extra)` fuehrt den Aufruf mit zusaetzlichen Feldern aus.
 */
export async function mitAbweichung(senden) {
  try {
    return await senden({});
  } catch (e) {
    const text = errMsg(e);
    if (e?.response?.status === 409 && /daten_geaendert/.test(text)) {
      const frage = `${text.replace(/\.? Bewusst abweichen.*$/, ".")}\n\n`
        + "Trotzdem mit den eingegebenen Daten anlegen? Die Anfrage gilt dann als erledigt.";
      if (window.confirm(frage)) return await senden({ daten_geaendert: true });
    }
    throw e;
  }
}
