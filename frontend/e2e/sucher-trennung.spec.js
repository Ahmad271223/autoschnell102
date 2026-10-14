// Zwei Sucher derselben Firma sehen NICHTS voneinander (Pruefbericht
// 20.09.2026, T-14). Regel (Runde 16/29/30, Ahmad): Fahrzeugpool, Vertraege
// und Termine eines Suchers gehoeren nur ihm; der Chef sieht die Firma. Und
// die JSON-Antworten fuer einen Sucher tragen keine Konto-Kennungen der
// Kollegen (owner_user_id, mitbearbeiter_ids) — beim Chef stehen sie drin.
//
// Bisher gab es keinen Browsertest mit zwei Suchern EINER Firma (der einzige
// zweite Sucher der Suite gehoerte zu einer anderen Firma).
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

const KONTO_FELDER = ["owner_user_id", "mitbearbeiter_ids", "uebernommen_von",
  "besitzer_migriert_von", "besitzer_vorher", "entfernt_von_sucher"];

/** Alle Schluessel einer JSON-Antwort, beliebig tief. */
function schluessel(obj, acc = new Set()) {
  if (Array.isArray(obj)) obj.forEach((v) => schluessel(v, acc));
  else if (obj && typeof obj === "object") {
    Object.keys(obj).forEach((k) => { acc.add(k); schluessel(obj[k], acc); });
  }
  return acc;
}
const kontoFelderIn = (obj) => KONTO_FELDER.filter((f) => schluessel(obj).has(f));

test.describe("Zwei Sucher einer Firma: strikte Trennung", () => {
  let firma, a, b;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    a = await h.createSucher(firma, { abo: true });
    b = await h.createSucher(firma, { abo: true });
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma] });
  });

  test("Sucher B sieht Fahrzeug, Vertrag und Termin von Sucher A nicht — und kein JSON nennt Kollegen", async ({ page }) => {
    // --- Sucher A legt an: Vergleich (Mock) -> Fahrzeug, Termin, Kaufvertrag
    const vergleich = await h.compareMock(a, "trennung");
    test.skip(!vergleich, "Backend laeuft nicht im Anbieter-Mock (MOCK_PROVIDER_FETCH) — kein Vergleich ohne echten Abruf");
    const vehicleId = vergleich.vehicleId;
    expect(vehicleId).toBeTruthy();
    const termin = await h.post("/appointments", {
      title: `E2E Trennung ${a.s}`, status: "offen", pickup_date: h.isoDate(1), pickup_time: "10:00",
      vehicle_id: vehicleId, seller_name: "Verkaeufer Trennung",
    }, { token: a.token });
    const vertrag = await h.post("/contracts", {
      vehicle_id: vehicleId, seller_name: "Verkäufer Trennung GmbH", seller_address: "Str 2",
      seller_zip: "10115", seller_city: "Berlin", seller_phone: "+490", seller_email: "v@e.de",
      purchase_price: 19999, pickup_date: h.isoDate(2), pickup_time: "10:00",
    }, { token: a.token });
    expect(termin.id && vertrag.id).toBeTruthy();

    // --- JSON fuer Sucher A: eigene Daten da, aber KEINE Konto-Kennungen
    const fahrzeugeA = await h.get("/vehicles", { token: a.token });
    const listeA = Array.isArray(fahrzeugeA) ? fahrzeugeA : (fahrzeugeA.items || fahrzeugeA.vehicles || []);
    expect(listeA.some((v) => v.id === vehicleId)).toBe(true);
    expect(kontoFelderIn(fahrzeugeA)).toEqual([]);
    const detailA = await h.get(`/vehicles/${vehicleId}`, { token: a.token });
    expect(detailA.id).toBe(vehicleId);
    expect(kontoFelderIn(detailA)).toEqual([]);
    const termineA = await h.get("/appointments", { token: a.token });
    expect((Array.isArray(termineA) ? termineA : termineA.items || []).some((t) => t.id === termin.id)).toBe(true);
    expect(kontoFelderIn(termineA)).toEqual([]);
    const terminA = await h.get(`/appointments/${termin.id}`, { token: a.token });
    expect(terminA.vehicle?.id).toBe(vehicleId);
    expect(kontoFelderIn(terminA)).toEqual([]);

    // --- Beim Chef stehen die Kennungen drin (er verwaltet die Zuordnung)
    const fahrzeugeChef = await h.get("/vehicles", { token: firma.token });
    const listeChef = Array.isArray(fahrzeugeChef) ? fahrzeugeChef : (fahrzeugeChef.items || fahrzeugeChef.vehicles || []);
    expect(listeChef.find((v) => v.id === vehicleId)?.owner_user_id).toBe(a.userId);
    const terminChef = await h.get(`/appointments/${termin.id}`, { token: firma.token });
    expect(terminChef.vehicle?.owner_user_id).toBe(a.userId);

    // --- Sucher B per API: leer bzw. 404
    const fahrzeugeB = await h.get("/vehicles", { token: b.token });
    const listeB = Array.isArray(fahrzeugeB) ? fahrzeugeB : (fahrzeugeB.items || fahrzeugeB.vehicles || []);
    expect(listeB.some((v) => v.id === vehicleId)).toBe(false);
    for (const pfad of [`/vehicles/${vehicleId}`, `/vehicles/${vehicleId}/akte`,
                        `/appointments/${termin.id}`, `/contracts/${vertrag.id}`]) {
      const r = await h.api("GET", pfad, { token: b.token, ok: false });
      expect(r.status, `${pfad} fuer Sucher B`).toBe(404);
    }
    const vertraegeB = await h.get("/contracts", { token: b.token });
    expect((Array.isArray(vertraegeB) ? vertraegeB : vertraegeB.items || []).some((c) => c.id === vertrag.id)).toBe(false);

    // --- Sucher B im Browser: Fahrzeugpool leer, kein Vertrag, kein Termin
    await h.authPage(page, "app", b.token);
    await page.goto("/app/fahrzeuge");
    await expect(page.getByTestId("vehicles-page")).toBeVisible();
    await expect(page.getByTestId("pool-leer")).toBeVisible();
    await expect(page.getByTestId(`pool-${vehicleId}`)).toHaveCount(0);

    await page.goto("/app/vertraege");
    await expect(page.getByTestId("pdfs-page")).toBeVisible();
    await expect(page.getByTestId(`pdf-card-${vertrag.id}`)).toHaveCount(0);
    await expect(page.getByText("Verkäufer Trennung GmbH")).toHaveCount(0);

    await page.goto("/app/termine");
    await expect(page.getByTestId("termine-page")).toBeVisible();
    await page.getByTestId("view-list").click();
    await expect(page.getByTestId(`appt-row-${termin.id}`)).toHaveCount(0);
    await expect(page.getByText(`E2E Trennung ${a.s}`)).toHaveCount(0);
  });
});
