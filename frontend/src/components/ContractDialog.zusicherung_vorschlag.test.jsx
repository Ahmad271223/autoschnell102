/**
 * Entscheidung 28.09.2026 (inserat4): Freitext ist zu vielfältig, um
 * Zusicherungen (HU, Scheckheft, Unfallfrei, Fahrtauglich, EU-Import) sicher
 * automatisch einzutragen. Der Vertragsdialog setzt sie NIE selbst — weder
 * beim Öffnen noch beim Wiederherstellen eines Entwurfs. Er zeigt je Feld den
 * Vorschlag aus dem Inserat mit Fundstelle und einem Knopf „Übernehmen“ (dazu
 * „Alle Vorschläge übernehmen“); erst der Klick setzt den Wert. Die Rückfrage
 * vor "PDF erstellen" entfällt.
 *
 * inserat5 (Prüfung Runde 4): AUS DEM INSERAT WIRD NICHTS MEHR UNGEFRAGT IN
 * DEN VERTRAG GESCHRIEBEN — auch Bereifung und Schlüsselanzahl sind nur
 * Vorschläge. „Alle Vorschläge übernehmen“ lässt selbst anders gewählte Felder
 * stehen; beim Fahrzeugwechsel verschwinden die alten Vorschläge sofort.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
const toastMock = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), message: vi.fn(),
}));
const auth = vi.hoisted(() => ({
  dealer: { company_name: "Autohaus Test", address: "Weg 1", zip_code: "12345", city: "Berlin" },
  refresh: undefined,
  user: { id: "U1" },
}));

vi.mock("@/lib/api", () => ({
  api, errMsg: (e, f) => e?.response?.data?.detail || f || "Fehler",
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/components/MarktdatenKarte", () => ({ useMarktHinweis: () => null }));
vi.mock("@/lib/pdf", () => ({ openContractPdf: vi.fn() }));
vi.mock("@/lib/dateiOeffnen", () => ({ blobOeffnen: vi.fn() }));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("./KiSchadenKarte", () => ({ default: () => null }));
vi.mock("./DamageSelector", () => ({ default: () => null, damagesToText: () => "" }));

const { default: ContractDialog, entwurfSchluessel, entwurfSpeichern } = await import("./ContractDialog.jsx");

const FAHRZEUG = { id: "V1", make_label: "BMW", mileage: "100000" };
const SCHLUESSEL = entwurfSchluessel("U1", "V1");
const VORSCHLAEGE = {
  felder: {
    hu_valid: { value: "Ja", source: "listing_description", source_text: "TÜV 07/2028" },
    hu_until: { value: "07/2028", source: "listing_description", source_text: "TÜV 07/2028" },
    service_book: { value: "ja", source: "listing_description", source_text: "lückenlos scheckheftgepflegt" },
    accident_free: { value: "Ja", source: "listing_description", source_text: "Unfallfrei, Nichtraucher" },
    drivable: { value: "Ja", source: "listing_description", source_text: "fahrbereit" },
    eu_import: { value: "Ja", source: "listing_description", source_text: "EU-Import aus Italien" },
    schluessel_anzahl: { value: "2", source: "listing_description", source_text: "2 Schlüssel" },
    tires: { value: "8-fach", source: "listing_description", source_text: "Winterreifen dabei" },
  },
  hinweise: ["Inserat zur Fahrbereitschaft nicht eindeutig („fahrbereit laut Vorbesitzer“) — bitte selbst prüfen."],
};
// testid des Auswahlfelds je Zusicherung
const FELD_TESTID = {
  hu_valid: "contract-hu-valid", service_book: "contract-service-book", accident_free: "contract-accident-free",
  drivable: "contract-drivable", eu_import: "contract-eu-import",
};

let wurzel;
let behaelter;
let vorschlaege = VORSCHLAEGE;

async function warten() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);

async function oeffnen(vehicleId = "V1", vehicle = FAHRZEUG) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(createElement(ContractDialog, { open: true, onClose: () => {}, vehicle, vehicleId }));
  });
  await warten();
}

async function schliessen() {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
  wurzel = null;
}

async function eingeben(testid, wert) {
  const feld = el(testid);
  const proto = feld.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(proto, "value").set.call(feld, wert);
    feld.dispatchEvent(new Event(feld.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}

async function klicken(testid) {
  const knopf = el(testid);
  expect(knopf, testid).toBeTruthy();
  await act(async () => { knopf.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
}

async function pdfErstellen() {
  await eingeben("contract-price", "15000");
  await eingeben("contract-payment", "Bar");
  const formular = el("contract-dialog").querySelector("form");
  await act(async () => {
    formular.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  await warten();
}

const zusicherungswerte = () => Object.fromEntries(
  Object.entries(FELD_TESTID).map(([feld, id]) => [feld, el(id).value]));
const LEER = { hu_valid: "", service_book: "", accident_free: "", drivable: "", eu_import: "" };

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  vorschlaege = VORSCHLAEGE;
  api.get.mockImplementation(async (url) => (String(url).startsWith("/contracts/vorschlaege/")
    ? { data: vorschlaege } : { data: {} }));
  api.post.mockResolvedValue({ data: { id: "C1" } });
  // Eine Rückfrage würde "PDF erstellen" hier abbrechen — es darf keine kommen.
  window.confirm = vi.fn(() => false);
});

afterEach(async () => {
  if (wurzel) await schliessen();
});

describe("Zusicherungen aus dem Inserat nur als Vorschlag", () => {
  it("beim Öffnen ist KEIN Feld aus dem Inserat gesetzt — auch Schlüssel/Bereifung nicht (inserat5)", async () => {
    await oeffnen();
    expect(zusicherungswerte()).toEqual(LEER);
    expect(el("contract-hu-until").value).toBe("");
    expect(el("contract-schluessel-anzahl").value).toBe("");
    expect(el("contract-tires").value).toBe("");
    const kasten = el("contract-inserat-vorschlaege");
    expect(kasten.textContent).not.toContain("Aus dem Inserat übernommen");
    // Bereifung und Schlüssel als Vorschlag mit Fundstelle und Knopf
    expect(el("contract-vorschlag-tires").textContent).toContain("Bereifung: 8-fach (Sommer + Winter)");
    expect(el("contract-vorschlag-tires").textContent).toContain("Inseratstext: „Winterreifen dabei“");
    expect(el("contract-vorschlag-schluessel_anzahl").textContent).toContain("Schlüssel: 2");
    expect(el("contract-vorschlag-uebernehmen-tires").textContent).toBe("Übernehmen");
    expect(el("contract-vorschlag-uebernehmen-schluessel_anzahl").textContent).toBe("Übernehmen");
    // je Zusicherung ein Vorschlag mit Fundstelle, Quelle und Knopf
    for (const feld of Object.keys(FELD_TESTID)) {
      expect(el(`contract-vorschlag-${feld}`), feld).toBeTruthy();
      expect(el(`contract-vorschlag-uebernehmen-${feld}`).textContent).toBe("Übernehmen");
    }
    expect(el("contract-vorschlag-hu_valid").textContent).toContain("HU/AU vorhanden: Ja, gültig bis 07/2028");
    expect(el("contract-vorschlag-accident_free").textContent).toContain("Inseratstext: „Unfallfrei, Nichtraucher“");
    expect(el("contract-vorschlag-service_book").textContent).toContain("Ja, lückenlos");
    expect(el("contract-vorschlaege-alle").textContent).toBe("Alle Vorschläge übernehmen");
  });

  it("Bereifung/Schlüssel stehen erst nach „Übernehmen“ im Formular", async () => {
    await oeffnen();
    await klicken("contract-vorschlag-uebernehmen-tires");
    expect(el("contract-tires").value).toBe("8-fach");
    expect(el("contract-schluessel-anzahl").value).toBe("");
    expect(el("contract-vorschlag-tires")).toBeNull();
    await klicken("contract-vorschlag-uebernehmen-schluessel_anzahl");
    expect(el("contract-schluessel-anzahl").value).toBe("2");
    expect(zusicherungswerte()).toEqual(LEER);
  });

  it("ein unklarer Vorschlag zeigt nur den Hinweis, ohne Knopf", async () => {
    vorschlaege = { felder: {}, hinweise: VORSCHLAEGE.hinweise };
    await oeffnen();
    const hinweis = el("contract-inserat-hinweis");
    expect(hinweis.textContent).toContain("Fahrbereitschaft nicht eindeutig");
    expect(hinweis.querySelector("button")).toBeNull();
    expect(el("contract-inserat-vorschlaege").querySelectorAll("button")).toHaveLength(0);
    expect(el("contract-drivable").value).toBe("");
  });

  it("„Übernehmen“ setzt genau dieses Feld und nimmt den Vorschlag aus der Liste", async () => {
    await oeffnen();
    await klicken("contract-vorschlag-uebernehmen-accident_free");
    expect(zusicherungswerte()).toEqual({ ...LEER, accident_free: "Ja" });
    expect(el("contract-vorschlag-accident_free")).toBeNull();
    expect(el("contract-vorschlag-drivable")).toBeTruthy();
    // HU: Ja + Datum in einem Schritt
    await klicken("contract-vorschlag-uebernehmen-hu_valid");
    expect(el("contract-hu-valid").value).toBe("Ja");
    expect(el("contract-hu-until").value).toBe("07/2028");
    expect(zusicherungswerte()).toEqual({ ...LEER, accident_free: "Ja", hu_valid: "Ja" });
  });

  it("„Alle Vorschläge übernehmen“ setzt alle, danach ist die Liste leer", async () => {
    await oeffnen();
    await klicken("contract-vorschlaege-alle");
    expect(zusicherungswerte()).toEqual({ hu_valid: "Ja", service_book: "ja", accident_free: "Ja",
                                          drivable: "Ja", eu_import: "Ja" });
    expect(el("contract-hu-until").value).toBe("07/2028");
    expect(el("contract-tires").value).toBe("8-fach");
    expect(el("contract-schluessel-anzahl").value).toBe("2");
    expect(el("contract-zusicherung-vorschlaege")).toBeNull();
    expect(el("contract-vorschlaege-alle")).toBeNull();
  });

  it("ein Vorschlag, der dem Feldwert entspricht, wird nicht erneut angeboten", async () => {
    await oeffnen();
    await eingeben("contract-accident-free", "Ja");
    expect(el("contract-vorschlag-accident_free")).toBeNull();
    // ein anderer Wert von Hand: der Vorschlag bleibt sichtbar (Knopf überschreibt bewusst)
    await eingeben("contract-drivable", "Nein");
    expect(el("contract-vorschlag-drivable")).toBeTruthy();
  });

  // inserat5 (Prüfung Runde 4, Nr. 2): „Alle“ überschrieb „Unfallfrei: Nein“ mit „Ja“
  it("„Alle übernehmen“ lässt von Hand abweichend Gewähltes stehen — Einzelknopf überschreibt bewusst", async () => {
    await oeffnen();
    await eingeben("contract-accident-free", "Nein");
    await eingeben("contract-drivable", "Nein");
    await eingeben("contract-schluessel-anzahl", "3");
    // Kennzeichnung an der Zeile, Knopf bleibt
    expect(el("contract-vorschlag-abweichung-accident_free").textContent)
      .toContain("Weicht von deiner Wahl ab (du: Nein)");
    expect(el("contract-vorschlag-abweichung-schluessel_anzahl").textContent).toContain("(du: 3)");
    expect(el("contract-vorschlag-abweichung-hu_valid")).toBeNull();
    expect(el("contract-vorschlag-uebernehmen-accident_free")).toBeTruthy();
    await klicken("contract-vorschlaege-alle");
    expect(el("contract-accident-free").value).toBe("Nein");
    expect(el("contract-drivable").value).toBe("Nein");
    expect(el("contract-schluessel-anzahl").value).toBe("3");
    // der Rest ist übernommen
    expect(el("contract-hu-valid").value).toBe("Ja");
    expect(el("contract-eu-import").value).toBe("Ja");
    expect(el("contract-tires").value).toBe("8-fach");
    // die abweichenden Zeilen bleiben stehen, „Alle“ gibt es nicht mehr (nichts mehr für „Alle“)
    expect(el("contract-vorschlag-accident_free")).toBeTruthy();
    expect(el("contract-vorschlag-drivable")).toBeTruthy();
    expect(el("contract-vorschlaege-alle")).toBeNull();
    // bewusster Einzelklick überschreibt
    await klicken("contract-vorschlag-uebernehmen-accident_free");
    expect(el("contract-accident-free").value).toBe("Ja");
    expect(el("contract-vorschlag-accident_free")).toBeNull();
  });

  // inserat5 (Nr. 4): Fahrzeugwechsel bei offenem Dialog — scheitert die neue
  // Anfrage, standen die Vorschläge des alten Inserats samt „Übernehmen“ da.
  it("Fahrzeugwechsel: alte Vorschläge verschwinden sofort, auch wenn die neue Anfrage scheitert", async () => {
    await oeffnen();
    expect(el("contract-vorschlag-accident_free")).toBeTruthy();
    let antworten;
    api.get.mockImplementation((url) => (String(url) === "/contracts/vorschlaege/V2"
      ? new Promise((_, reject) => { antworten = () => reject(new Error("Netz")); })
      : Promise.resolve({ data: {} })));
    await act(async () => {
      wurzel.render(createElement(ContractDialog, { open: true, onClose: () => {},
                                                     vehicle: { id: "V2", make_label: "VW" }, vehicleId: "V2" }));
    });
    // schon VOR der Antwort weg
    expect(el("contract-inserat-vorschlaege")).toBeNull();
    expect(el("contract-vorschlag-accident_free")).toBeNull();
    await act(async () => { antworten(); });
    await warten();
    expect(el("contract-inserat-vorschlaege")).toBeNull();
    expect(api.get).toHaveBeenCalledWith("/contracts/vorschlaege/V2");
  });

  it("PDF-Payload enthält nur bewusst gesetzte Zusicherungen — ohne Rückfrage", async () => {
    await oeffnen();
    await klicken("contract-vorschlag-uebernehmen-drivable");
    await pdfErstellen();
    expect(window.confirm).not.toHaveBeenCalled();
    expect(api.post).toHaveBeenCalledTimes(1);
    const [url, payload] = api.post.mock.calls[0];
    expect(url).toBe("/contracts");
    expect(payload.drivable).toBe("Ja");
    for (const feld of ["hu_valid", "hu_until", "service_book", "accident_free", "eu_import"]) {
      expect(payload[feld], feld).toBe("");
    }
    // inserat5: auch Schlüssel/Bereifung nicht ohne Klick
    expect(payload.schluessel_anzahl).toBe("");
    expect(payload.tires).toBe("");
  });

  it("ohne Klick geht keine Zusicherung aus dem Inserat in den Vertrag", async () => {
    await oeffnen();
    await pdfErstellen();
    expect(window.confirm).not.toHaveBeenCalled();
    const payload = api.post.mock.calls[0][1];
    for (const feld of ["hu_valid", "hu_until", "service_book", "accident_free", "drivable", "eu_import",
                        "tires", "schluessel_anzahl"]) {
      expect(payload[feld], feld).toBe("");
    }
  });

  it("Entwurf wiederherstellen setzt nichts ungefragt (alter Entwurf mit Vorbelegung)", async () => {
    // Entwurf aus der Zeit der automatischen Vorbelegung: Unfallfrei/HU vom
    // Inserat eingetragen (nicht angefasst), Fahrtauglich bewusst gewählt.
    entwurfSpeichern(SCHLUESSEL, {
      form: { accident_free: "Ja", hu_valid: "Ja", hu_until: "07/2028", drivable: "Nein", schluessel_anzahl: "2",
              tires: "8-fach", purchase_price: "15000", payment_method: "Bar" },
      beruehrt: { drivable: true, purchase_price: true, payment_method: true },
      uebernommen: [{ feld: "accident_free", label: "Unfallfrei", wert: "Ja", roh: "Ja", fund: "unfallfrei" },
                    { feld: "schluessel_anzahl", label: "Schlüssel", wert: "2", roh: "2", fund: "2 Schlüssel" },
                    { feld: "tires", label: "Bereifung", wert: "8-fach", roh: "8-fach", fund: "Winterreifen dabei" }],
    });
    await oeffnen();
    expect(zusicherungswerte()).toEqual({ ...LEER, drivable: "Nein" });
    expect(el("contract-hu-until").value).toBe("");
    // inserat5: auch die früher ohne Klick eingetragene Bereifung/Schlüsselanzahl ist wieder nur Vorschlag
    expect(el("contract-schluessel-anzahl").value).toBe("");
    expect(el("contract-tires").value).toBe("");
    expect(el("contract-vorschlag-accident_free")).toBeTruthy();
    expect(el("contract-vorschlag-hu_valid")).toBeTruthy();
    expect(el("contract-vorschlag-schluessel_anzahl")).toBeTruthy();
    expect(el("contract-vorschlag-tires")).toBeTruthy();
    expect(el("contract-inserat-vorschlaege").textContent).not.toContain("Aus dem Inserat übernommen");
  });

  // inserat5 (Nr. 5): ein von Hand getipptes „HU gültig bis“ überlebt das Wiederherstellen
  it("Entwurf: getipptes HU-Datum bleibt, nur die unberührte HU-Wahl wird geleert", async () => {
    entwurfSpeichern(SCHLUESSEL, {
      form: { hu_valid: "Ja", hu_until: "05/2027" },
      beruehrt: { hu_until: true },
    });
    await oeffnen();
    expect(el("contract-hu-valid").value).toBe("");
    expect(el("contract-hu-until").value).toBe("05/2027");
    // der HU-Vorschlag weicht vom getippten Datum ab: gekennzeichnet, nicht in „Alle“
    expect(el("contract-vorschlag-abweichung-hu_valid").textContent).toContain("du: gültig bis 05/2027");
    await klicken("contract-vorschlaege-alle");
    expect(el("contract-hu-until").value).toBe("05/2027");
    expect(el("contract-hu-valid").value).toBe("");
  });

  it("übernommene Zusicherung bleibt nach Entwurf und Neuöffnen stehen (bewusst gewählt)", async () => {
    await oeffnen();
    await klicken("contract-vorschlag-uebernehmen-accident_free");
    await act(async () => { window.dispatchEvent(new Event("pagehide")); });
    const gespeichert = JSON.parse(window.sessionStorage.getItem(SCHLUESSEL));
    expect(gespeichert.form.accident_free).toBe("Ja");
    expect(gespeichert.beruehrt.accident_free).toBe(true);
    await schliessen();
    await oeffnen();
    expect(zusicherungswerte()).toEqual({ ...LEER, accident_free: "Ja" });
    expect(el("contract-vorschlag-accident_free")).toBeNull();
    expect(el("contract-vorschlag-drivable")).toBeTruthy();
  });

  it("schmaler Dialog: Vorschlagszeile bricht um, Knöpfe haben Token-Farben", async () => {
    await oeffnen();
    const zeile = el("contract-vorschlag-accident_free");
    expect(zeile.className).toContain("flex-col");
    expect(zeile.className).toContain("sm:flex-row");
    expect(zeile.querySelector(".min-w-0")).toBeTruthy();
    expect(zeile.getAttribute("style")).toContain("var(--border-default)");
    expect(el("contract-vorschlag-uebernehmen-accident_free").className).toContain("apple-btn-secondary");
  });
});
