/*
 * Kleine Prüfungen der Fahrer-App rund um eine Fahrt (Rollenprüfung 22.09.2026).
 */
import { driverApi } from "@/context/DriverContext";
import { kmAusText } from "@/lib/preis";

/**
 * Abholprotokoll einer Fahrt kurz nachsehen:
 *   { final: true|false|null, kmStand: Zahl|null }
 * final null = Server nicht erreichbar. kmStand = Kilometerstand aus
 * Abschnitt 4 des UNTERSCHRIEBENEN Protokolls (sonst null).
 */
export async function protokollNachsehen(apptId) {
  try {
    const r = await driverApi.get(`/driver/appointments/${apptId}/protocol`);
    const proto = r.data?.protocol || r.data?.doc || r.data;
    const final = Boolean(proto && (proto.status === "final" || proto.protocol?.status === "final"));
    // Rollenprüfung 22.09.2026 (RP-068/RP-167): km stand schon im Protokoll —
    // der Abhol-Check fragte ihn ein zweites Mal ohne Vorbelegung ab.
    const km = final ? kmAusText(proto?.condition?.mileage ?? proto?.protocol?.condition?.mileage) : null;
    return { final, kmStand: typeof km === "number" && Number.isFinite(km) ? km : null };
  } catch (e) {
    return { final: e?.response ? false : null, kmStand: null };
  }
}

/**
 * RP-064/RP-163: Ist das Abholprotokoll dieser Fahrt schon unterschrieben
 * (final)? true/false — oder null, wenn der Server nicht erreichbar war.
 * Die Startseite fragt das VOR dem Öffnen des Abhol-Checks; vorher merkte
 * erst das Absenden, dass das Protokoll fehlt, und km, Abweichungen und
 * Fotos waren verloren.
 */
export async function protokollIstFinal(apptId) {
  return (await protokollNachsehen(apptId)).final;
}

/**
 * RP-537: Gründe für "nicht abgeholt". Der Grund geht als Notiz an den Termin
 * (der Händler sieht ihn dort und im Verlauf).
 */
export const NICHT_ABGEHOLT_GRUENDE = [
  { key: "nicht_erschienen", label: "Verkäufer nicht erschienen / nicht erreichbar" },
  { key: "abgesagt", label: "Verkäufer hat abgesagt" },
  { key: "fahrzeug_weicht_ab", label: "Fahrzeug weicht stark ab" },
  { key: "sonstiges", label: "Sonstiges" },
];

/**
 * Notiztext aus Grund + Freitext. null, wenn die Angabe nicht reicht
 * (kein Grund, oder "Sonstiges" ohne mindestens 3 Zeichen Erklärung).
 */
export function nichtAbgeholtNotiz(grundKey, freitext) {
  const grund = NICHT_ABGEHOLT_GRUENDE.find((g) => g.key === grundKey);
  const text = String(freitext || "").trim();
  if (!grund) return null;
  if (grundKey === "sonstiges" && text.length < 3) return null;
  const notiz = `Nicht abgeholt: ${grund.label}${text ? ` — ${text}` : ""}`;
  return notiz.slice(0, 1900);                   // Server: notes max. 2000 Zeichen
}
