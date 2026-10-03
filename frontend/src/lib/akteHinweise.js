/*
 * Rollenprüfung 22.09.2026, Welle B2 — reine Helfer für Bestand.jsx und
 * FahrzeugAkte.jsx (beide Seiten nutzen dieselben Texte).
 *
 *  RP-454  "Fahrzeug löschen" bei offenen Abholterminen: der Server antwortet
 *          409 mit { msg, code: "termine_offen", termine: [...] }. Der Chef
 *          bekommt daraus die Rückfrage "Offene Termine stornieren und
 *          Fahrzeug löschen?" mit Datum, Uhrzeit und Fahrer je Termin; erst
 *          nach dem Ja geht die Entscheidung mit termine_stornieren=true raus.
 *  RP-474  Deutlicher Hinweis in der Akte, was aus dem unterschriebenen
 *          Abholprotokoll noch nicht in den Fahrzeugdaten steht.
 */

/** Kennung im 409-Detail (backend/routes/bestand.py TERMINE_OFFEN_CODE). */
export const TERMINE_OFFEN_CODE = "termine_offen";

/** Liefert das 409-Detail mit der Terminliste — oder null bei jedem anderen Fehler. */
export function termineOffenDetail(err) {
  if (err?.response?.status !== 409) return null;
  const d = err?.response?.data?.detail;
  if (!d || typeof d !== "object" || Array.isArray(d)) return null;
  if (d.code !== TERMINE_OFFEN_CODE || !Array.isArray(d.termine)) return null;
  return d;
}

/** "30.09.2026 14:00 — Fahrer: Max M." (ohne Uhrzeit/Fahrer entsprechend kürzer). */
export function terminZeile(t) {
  const datum = datumDeutsch(t?.pickup_date);
  const zeit = t?.pickup_time ? ` ${String(t.pickup_time).slice(0, 5)}` : "";
  const fahrer = t?.driver_name ? ` — Fahrer: ${t.driver_name}` : " — ohne Fahrer";
  return `${datum}${zeit}${fahrer}`;
}

function datumDeutsch(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ""));
  return m ? `${m[3]}.${m[2]}.${m[1]}` : "ohne Datum";
}

/** Text für window.confirm — nennt jeden Termin, damit der Chef weiß, was er storniert. */
export function termineStornoFrage(detail) {
  const termine = Array.isArray(detail?.termine) ? detail.termine : [];
  const n = termine.length;
  const kopf = n === 1
    ? "Zu diesem Fahrzeug gibt es noch einen offenen Abholtermin:"
    : `Zu diesem Fahrzeug gibt es noch ${n} offene Abholtermine:`;
  const zeilen = termine.map((t) => `• ${terminZeile(t)}`).join("\n");
  return `${kopf}\n${zeilen}\n\n`
    + (n === 1 ? "Offenen Termin stornieren und Fahrzeug löschen?"
               : "Offene Termine stornieren und Fahrzeug löschen?")
    + "\nDer Fahrer sieht die Fahrt danach nicht mehr; Vertrag und Historie bleiben erhalten.";
}

/** RP-474: "Aus dem Abholprotokoll: 85.120 km, 2 neue Schäden — ins Fahrzeug übernehmen". */
export function protokollBefundHinweis({ km = null, schaeden = 0 } = {}) {
  const teile = [];
  const zahl = typeof km === "number" ? km : Number(km);
  if (km != null && Number.isFinite(zahl) && zahl > 0) teile.push(`${zahl.toLocaleString("de-DE")} km`);
  if (schaeden === 1) teile.push("1 neuer Schaden");
  else if (schaeden > 1) teile.push(`${schaeden} neue Schäden`);
  if (!teile.length) return "Aus dem Abholprotokoll übernehmen";
  return `Aus dem Abholprotokoll: ${teile.join(", ")} — ins Fahrzeug übernehmen`;
}
