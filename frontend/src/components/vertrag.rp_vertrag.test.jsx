/*
 * Rollenprüfung 22.09.2026 — Team "vertrag" (Oberfläche).
 *  RP-402  Kaufpreis "15.000" = 15.000 € (nicht 15 €), km "150 Tkm"
 *  RP-412  Entwurf des Kaufvertrags im sessionStorage
 *  RP-490  frische Firmendaten überschreiben nur unberührte Käuferfelder
 *  RP-215  Bahn-Mail ohne eingetragene Verbindung wird erkannt
 *  RP-406/RP-417  Abholtermin nur anlegen, wenn keiner offen ist
 *  RP-413  iPhone-App (standalone) erkennen
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

const {
  kaufpreisPruefen, kmFuerVertrag, entwurfSchluessel, entwurfLesen, entwurfSpeichern, entwurfLoeschen,
} = await import("./ContractDialog");
const { kaeuferAktualisieren } = await import("@/lib/kaeuferdaten");
const { bahnTextUnveraendert } = await import("./FolgeMailDialog");
const { abholterminAnlegen, TERMIN_MELDUNG } = await import("./SendDialog");
const { brauchtAbholtermin, ARCHIV_SEITE } = await import("@/pages/app/PDFArchiv");
const { iosApp } = await import("@/lib/dateiOeffnen");

describe("RP-402: Kaufpreis in deutscher Schreibweise", () => {
  it("15.000 ist fünfzehntausend, nicht 15", () => {
    expect(kaufpreisPruefen("15.000")).toEqual({ betrag: 15000, fehler: "" });
    expect(kaufpreisPruefen("15.000,50").betrag).toBe(15000.5);
    expect(kaufpreisPruefen("8900").betrag).toBe(8900);
    expect(kaufpreisPruefen("8.900 €").betrag).toBe(8900);
  });
  it("Leeres, Unlesbares und 0 blockieren", () => {
    expect(kaufpreisPruefen("").fehler).toMatch(/Kaufpreis eingeben/);
    expect(kaufpreisPruefen("abc").fehler).toMatch(/nicht lesbar/);
    expect(kaufpreisPruefen("1,2,3").betrag).toBeNull();
    expect(kaufpreisPruefen("0").fehler).toMatch(/größer als 0/);
  });
});

describe("RP-402: Kilometerstand", () => {
  it("liest deutsche Schreibweise und Tkm", () => {
    expect(kmFuerVertrag("85.120")).toBe("85120");
    expect(kmFuerVertrag("150 Tkm")).toBe("150000");
    expect(kmFuerVertrag("85.120 km")).toBe("85120");
    expect(kmFuerVertrag(145880)).toBe("145880");
  });
  it("leer bleibt leer, Unsinn ist null (blockiert)", () => {
    expect(kmFuerVertrag("")).toBe("");
    expect(kmFuerVertrag(null)).toBe("");
    expect(kmFuerVertrag("viel")).toBeNull();
  });
});

describe("RP-412: Entwurf im sessionStorage", () => {
  beforeEach(() => window.sessionStorage.clear());
  it("speichern, lesen, löschen — je Konto und Fahrzeug", () => {
    const key = entwurfSchluessel("u1", "v1");
    expect(key).not.toBe(entwurfSchluessel("u2", "v1"));
    expect(entwurfLesen(key)).toBeNull();
    entwurfSpeichern(key, { form: { seller_name: "Max" }, beruehrt: { seller_name: true }, idempotenz: "k1" });
    const e = entwurfLesen(key);
    expect(e.form.seller_name).toBe("Max");
    expect(e.beruehrt.seller_name).toBe(true);
    expect(e.idempotenz).toBe("k1");
    entwurfLoeschen(key);
    expect(entwurfLesen(key)).toBeNull();
  });
  it("zu alte oder kaputte Entwürfe werden ignoriert", () => {
    const key = entwurfSchluessel("u1", "v2");
    entwurfSpeichern(key, { form: { seller_name: "Alt" } }, Date.now() - 25 * 60 * 60 * 1000);
    expect(entwurfLesen(key)).toBeNull();
    window.sessionStorage.setItem(key, "{kaputt");
    expect(entwurfLesen(key)).toBeNull();
  });
});

describe("RP-490: Käuferdaten nach dem Nachladen", () => {
  const form = { dealer_company: "Alt GmbH", dealer_address: "Alte Str. 1", dealer_city: "Alt",
                 empfang_ort_kaeufer: "Alt", dealer_phone: "0511 1" };
  const profil = { company_name: "Neu GmbH", address: "Neue Str. 9", city: "Neu", phone: "0511 1" };
  it("unberührte Felder bekommen den frischen Stand, auch wenn sie gefüllt waren", () => {
    const neu = kaeuferAktualisieren(form, profil, {});
    expect(neu.dealer_company).toBe("Neu GmbH");
    expect(neu.dealer_address).toBe("Neue Str. 9");
    expect(neu.empfang_ort_kaeufer).toBe("Neu");
  });
  it("Getipptes bleibt", () => {
    const neu = kaeuferAktualisieren(form, profil, { dealer_company: true });
    expect(neu.dealer_company).toBe("Alt GmbH");
    expect(neu.dealer_address).toBe("Neue Str. 9");
  });
  it("nichts Neues: dasselbe Objekt", () => {
    const gleich = { dealer_company: "Neu GmbH" };
    expect(kaeuferAktualisieren(gleich, { company_name: "Neu GmbH" }, {})).toBe(gleich);
  });
});

describe("RP-215: Bahnverbindung", () => {
  it("unveränderte Vorlage wird erkannt (Leerraum egal)", () => {
    expect(bahnTextUnveraendert("bahn", "Hallo  Welt\n", "Hallo Welt")).toBe(true);
    expect(bahnTextUnveraendert("bahn", "Hallo Welt ICE 571", "Hallo Welt")).toBe(false);
    expect(bahnTextUnveraendert("nach_kauf", "Hallo Welt", "Hallo Welt")).toBe(false);
    expect(bahnTextUnveraendert("bahn", "Text", "")).toBe(false);
  });
});

describe("RP-406/RP-417: Abholtermin zum Vertrag", () => {
  const vertrag = { id: "c1", vehicle_id: "v1", seller_name: "S", pickup_date: "",
                    contract_data: { seller_address: "Weg 1", seller_zip: "30159", seller_city: "H" } };
  it("storniert / abgeholt / offener Termin: nichts anlegen", async () => {
    const post = vi.fn();
    expect(await abholterminAnlegen({ ...vertrag, kaufvorgang_status: "storniert" }, { post }))
      .toEqual({ angelegt: false, grund: "storniert" });
    expect(await abholterminAnlegen({ ...vertrag, kaufvorgang_status: "abgeholt" }, { post }))
      .toEqual({ angelegt: false, grund: "abgeholt" });
    expect(await abholterminAnlegen({ ...vertrag, termin_offen: true }, { post }))
      .toEqual({ angelegt: false, grund: "offen" });
    // ohne Angabe der Liste: appointment_id = frisch angelegter, offener Termin
    expect(await abholterminAnlegen({ ...vertrag, appointment_id: "t1" }, { post }))
      .toEqual({ angelegt: false, grund: "offen" });
    expect(post).not.toHaveBeenCalled();
    expect(TERMIN_MELDUNG.storniert).toMatch(/storniert/);
  });
  it("nach 'nicht abgeholt' (appointment_id zeigt auf den geschlossenen Termin): neu anlegen", async () => {
    const post = vi.fn().mockResolvedValue({ data: { id: "t2", hinweis: "Doppelbuchung" } });
    const erg = await abholterminAnlegen(
      { ...vertrag, appointment_id: "t1", termin_offen: false, kaufvorgang_status: "nicht_abgeholt" },
      { post });
    expect(erg).toEqual({ angelegt: true, hinweis: "Doppelbuchung" });
    expect(post).toHaveBeenCalledWith("/appointments", expect.objectContaining({
      contract_id: "c1", vehicle_id: "v1", pickup_address: "Weg 1 30159 H", status: "offen" }));
  });
  it("409 'offener Termin' vom Server ist kein Fehler", async () => {
    const post = vi.fn().mockRejectedValue({ response: { status: 409,
      data: { detail: "Zu diesem Vertrag gibt es bereits einen offenen Abholtermin." } } });
    expect(await abholterminAnlegen({ ...vertrag, termin_offen: false }, { post }))
      .toEqual({ angelegt: false, grund: "offen" });
  });
  it("Archiv zeigt den Knopf nur ohne offenen Termin", () => {
    expect(brauchtAbholtermin({ termin_offen: false, kaufvorgang_status: "vertrag_erstellt" })).toBe(true);
    expect(brauchtAbholtermin({ termin_offen: false, kaufvorgang_status: "nicht_abgeholt" })).toBe(true);
    expect(brauchtAbholtermin({ termin_offen: true })).toBe(false);
    expect(brauchtAbholtermin({ termin_offen: false, kaufvorgang_status: "storniert" })).toBe(false);
    expect(brauchtAbholtermin({ termin_offen: false, kaufvorgang_status: "abgeholt" })).toBe(false);
    expect(brauchtAbholtermin({})).toBe(false);          // alter Server ohne Angabe
    expect(ARCHIV_SEITE).toBe(50);
  });
});

describe("RP-413: installierte App auf dem iPhone", () => {
  it("nur iOS UND standalone", () => {
    const iphone = { userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)" };
    expect(iosApp({ nav: iphone, alsApp: () => true })).toBe(true);
    expect(iosApp({ nav: iphone, alsApp: () => false })).toBe(false);
    expect(iosApp({ nav: { userAgent: "Mozilla/5.0 (Windows NT 10.0) Chrome/120" },
                    alsApp: () => true })).toBe(false);
  });
});
