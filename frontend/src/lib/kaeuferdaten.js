/*
 * Runde 24 (11.09.2026): Kaeuferdaten im Kaufvertrag sind Pflicht.
 *
 * Wunsch Ahmad: Hat der Sucher in den Einstellungen keine Kaeuferdaten
 * gespeichert, muss er sie im Vertragsformular eintragen — sie erscheinen
 * als Kaeufer im Kaufvertrag und als Auftraggeber im Abholprotokoll.
 * Die Vorschau bleibt ohne diese Pflicht moeglich.
 */

// Reihenfolge wie im Formular (Abschnitt "Käufer (Händler — du)").
export const KAEUFER_PFLICHTFELDER = [
  { key: "dealer_company", label: "Firma" },
  { key: "dealer_address", label: "Adresse" },
  { key: "dealer_zip", label: "PLZ" },
  { key: "dealer_city", label: "Ort" },
];

// Welche Pflichtangaben fehlen? Leer oder nur Leerzeichen zaehlt als fehlend
// (das required-Attribut des Browsers laesst " " durch).
export function fehlendeKaeuferfelder(form) {
  const f = form || {};
  return KAEUFER_PFLICHTFELDER.filter(({ key }) => !String(f[key] ?? "").trim());
}

const leer = (wert) => !String(wert ?? "").trim();

// Kaeuferfelder des Vertragsformulars aus dem Profil (useAuth().dealer =
// effective_dealer, bei Suchern mit ihren eigenen Einstellungen).
export function kaeuferAusProfil(dealer) {
  const d = dealer || {};
  return {
    dealer_company: d.company_name || "",
    dealer_contact: d.contact_person || "",
    dealer_phone: d.phone || "",
    dealer_whatsapp: d.whatsapp_number || d.phone || "",
    dealer_email: d.email || "",
    dealer_address: d.address || "",
    dealer_zip: d.zip_code || "",
    dealer_city: d.city || "",
  };
}

// Runde 24 (11.09.2026, Gegenpruefung): Das Profil im Tab kann veraltet sein
// (Einstellungen in einem anderen Tab gespeichert, Chef ergaenzt die
// Firmenadresse). Nach dem Nachladen fuellt der Dialog NUR noch leere
// Kaeuferfelder aus dem frischen Profil — was der Sucher schon getippt hat,
// bleibt. "Ort (Kaeufer)" der Empfangsbestaetigung folgt dem Kaeufer-Ort wie
// in ContractDialog.set(), solange er nicht von Hand abweicht.
// Gibt dasselbe Objekt zurueck, wenn nichts zu fuellen ist.
export function kaeuferLueckenFuellen(form, dealer) {
  const profil = kaeuferAusProfil(dealer);
  const next = { ...form };
  let geaendert = false;
  for (const [key, wert] of Object.entries(profil)) {
    if (wert && leer(form[key])) {
      next[key] = wert;
      geaendert = true;
    }
  }
  if (!geaendert) return form;
  if (next.dealer_city !== form.dealer_city && form.empfang_ort_kaeufer === form.dealer_city) {
    next.empfang_ort_kaeufer = next.dealer_city;
  }
  return next;
}
