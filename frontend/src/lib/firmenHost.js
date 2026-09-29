/*
 * Firmenseite (Kundenportal, 29.09.2026): Jede Firma hat ihre eigene Adresse — eine Unterdomain
 * (kfz-mueller.auto-schnellkauf.de) oder eine eigene Kundendomain per DNS (kfz-mueller.de).
 * Läuft die App unter so einer Adresse, zeigt sie NUR die Firmenseite. Ob eine Adresse eine
 * Firmenseite ist, sagt das Backend (/api/public/firma?host=…); hier steht nur die Vorprüfung,
 * damit die Hauptadresse (app.…, localhost, IP-Adressen) nie eine Anfrage dafür macht.
 */

/** Kann diese Adresse eine Firmenseite sein? Hauptadresse, localhost und IP-Adressen: nein. */
export function istFirmenHostKandidat(hostname) {
  const h = String(hostname || "").trim().toLowerCase().replace(/\.$/, "");
  if (!h || !h.includes(".")) return false;                          // "localhost", leer
  if (/^app\./.test(h)) return false;                                // Hauptadresse (www.kfz-mueller.de bleibt Kandidat)
  if (h.endsWith(".localhost") || h === "127.0.0.1") return false;
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(h) || h.startsWith("[")) return false;   // IPv4 / IPv6
  return true;
}
