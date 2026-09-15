/*
 * Runde 24 (11.09.2026): Kaeuferdaten im Vertragsformular sind Pflicht
 * (Firma, Adresse, PLZ, Ort) — Wunsch Ahmad.
 */
import {
  fehlendeKaeuferfelder,
  kaeuferAusProfil,
  kaeuferLueckenFuellen,
  KAEUFER_PFLICHTFELDER,
} from "./kaeuferdaten";

const VOLL = {
  dealer_company: "Autohaus Beispiel GmbH",
  dealer_address: "Musterstr. 1",
  dealer_zip: "90402",
  dealer_city: "Nürnberg",
};

const labels = (form) => fehlendeKaeuferfelder(form).map((f) => f.label);

describe("fehlendeKaeuferfelder", () => {
  test("vollstaendige Angaben: nichts fehlt", () => {
    expect(fehlendeKaeuferfelder(VOLL)).toEqual([]);
  });

  test("leere Einstellungen: alle vier fehlen, in Formularreihenfolge", () => {
    expect(labels({ dealer_company: "", dealer_address: "", dealer_zip: "", dealer_city: "" }))
      .toEqual(["Firma", "Adresse", "PLZ", "Ort"]);
    expect(labels({})).toEqual(["Firma", "Adresse", "PLZ", "Ort"]);
    expect(labels(null)).toEqual(["Firma", "Adresse", "PLZ", "Ort"]);
  });

  test("nur Leerzeichen zaehlt als fehlend", () => {
    expect(labels({ ...VOLL, dealer_city: "   ", dealer_zip: "\t" })).toEqual(["PLZ", "Ort"]);
  });

  test("Zahlen (z.B. PLZ) gelten als ausgefuellt", () => {
    expect(labels({ ...VOLL, dealer_zip: 90402 })).toEqual([]);
  });

  test("Kontaktfelder sind keine Pflicht", () => {
    // Ansprechpartner/Telefon/WhatsApp/E-Mail bleiben freiwillig.
    expect(KAEUFER_PFLICHTFELDER.map((f) => f.key))
      .toEqual(["dealer_company", "dealer_address", "dealer_zip", "dealer_city"]);
    expect(fehlendeKaeuferfelder({ ...VOLL, dealer_phone: "", dealer_email: "" })).toEqual([]);
  });
});

// Runde 24 (11.09.2026, Gegenpruefung): veraltetes Profil im Tab — nach dem
// Speichern der Einstellungen in einem anderen Tab laedt der Dialog frisch
// nach und fuellt nur leere Kaeuferfelder.
const PROFIL = {
  company_name: "Autohaus Beispiel GmbH",
  contact_person: "Sam Sucher",
  phone: "0911 1234",
  whatsapp_number: "",
  email: "sam@example.de",
  address: "Musterstr. 1",
  zip_code: "90402",
  city: "Nürnberg",
};

describe("kaeuferAusProfil", () => {
  test("ordnet Profilfelder den Formularfeldern zu, WhatsApp faellt auf Telefon zurueck", () => {
    expect(kaeuferAusProfil(PROFIL)).toEqual({
      dealer_company: "Autohaus Beispiel GmbH",
      dealer_contact: "Sam Sucher",
      dealer_phone: "0911 1234",
      dealer_whatsapp: "0911 1234",
      dealer_email: "sam@example.de",
      dealer_address: "Musterstr. 1",
      dealer_zip: "90402",
      dealer_city: "Nürnberg",
    });
  });

  test("ohne Profil: alles leer, Hinweis nennt alle Pflichtfelder", () => {
    expect(labels(kaeuferAusProfil(null))).toEqual(["Firma", "Adresse", "PLZ", "Ort"]);
    expect(labels(kaeuferAusProfil(PROFIL))).toEqual([]);
  });
});

describe("kaeuferLueckenFuellen", () => {
  const LEER = { ...kaeuferAusProfil(null), empfang_ort_kaeufer: "", purchase_price: "5000" };

  test("fuellt leere Felder aus dem frischen Profil, Pflicht danach erfuellt", () => {
    const next = kaeuferLueckenFuellen(LEER, PROFIL);
    expect(next.dealer_company).toBe("Autohaus Beispiel GmbH");
    expect(next.dealer_city).toBe("Nürnberg");
    expect(next.purchase_price).toBe("5000");
    expect(fehlendeKaeuferfelder(next)).toEqual([]);
  });

  test("schon Getipptes bleibt stehen", () => {
    const getippt = { ...LEER, dealer_company: "Eigene Firma", dealer_zip: "10115" };
    const next = kaeuferLueckenFuellen(getippt, PROFIL);
    expect(next.dealer_company).toBe("Eigene Firma");
    expect(next.dealer_zip).toBe("10115");
    expect(next.dealer_address).toBe("Musterstr. 1");
  });

  test("nur Leerzeichen gilt als leer und wird gefuellt", () => {
    expect(kaeuferLueckenFuellen({ ...LEER, dealer_city: "  " }, PROFIL).dealer_city).toBe("Nürnberg");
  });

  test("Empfangs-Ort (Kaeufer) folgt dem Kaeufer-Ort nur, solange er ihm folgte", () => {
    expect(kaeuferLueckenFuellen(LEER, PROFIL).empfang_ort_kaeufer).toBe("Nürnberg");
    const vonHand = { ...LEER, empfang_ort_kaeufer: "Fürth" };
    expect(kaeuferLueckenFuellen(vonHand, PROFIL).empfang_ort_kaeufer).toBe("Fürth");
    const ortGetippt = { ...LEER, dealer_city: "Erlangen", empfang_ort_kaeufer: "Erlangen" };
    expect(kaeuferLueckenFuellen(ortGetippt, PROFIL).empfang_ort_kaeufer).toBe("Erlangen");
  });

  test("nichts zu fuellen: dasselbe Objekt (kein unnoetiges Neurendern)", () => {
    const voll = { ...kaeuferAusProfil(PROFIL), empfang_ort_kaeufer: "Nürnberg" };
    expect(kaeuferLueckenFuellen(voll, PROFIL)).toBe(voll);
    expect(kaeuferLueckenFuellen(LEER, null)).toBe(LEER);
    expect(kaeuferLueckenFuellen(LEER, {})).toBe(LEER);
  });
});
