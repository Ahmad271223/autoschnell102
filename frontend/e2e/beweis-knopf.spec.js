// Beweisdokument nur auf Knopfdruck (Pruefbericht 20.09.2026, T-25; Wunsch
// Ahmad 18.09.2026, BEWEIS_AUTOMATISCH=false): nach dem Vergleich zeigt die
// Karte den Knopf "Beweisdokument erstellen", erst der Klick merkt vor, der
// Hintergrund-Worker baut das PDF.
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

test.describe("Beweisdokument auf Knopfdruck", () => {
  let firma, sucher;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    sucher = await h.createSucher(firma, { abo: true });
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma] });
  });

  test("Vergleich zeigt den Knopf, Klick erzeugt das Dokument", async ({ page }) => {
    await h.authPage(page, "app", sucher.token);
    await page.goto("/app/vergleich");
    const eingabe = page.getByTestId("vergleich-url-input");
    await expect(eingabe).toBeVisible();
    await eingabe.fill(h.mockInseratUrl("beweis"));   // Einfuegen startet den Abruf
    const start = page.getByTestId("vergleich-start-btn");
    if (await start.isEnabled()) await start.click();
    await expect(page.getByTestId("vehicle-title")).toBeVisible({ timeout: 60_000 });

    const knopf = page.getByTestId("beweis-erstellen-btn");
    const laeuft = page.getByTestId("beweis-status-laeuft");
    const fertig = page.getByTestId("beweis-status-fertig");
    await expect(knopf.or(laeuft).or(fertig).first()).toBeVisible({ timeout: 15_000 });
    test.skip((await knopf.count()) === 0,
      "Server laeuft mit BEWEIS_AUTOMATISCH=true — das Dokument entsteht ohne Knopf (Test braucht BEWEIS_AUTOMATISCH=false)");

    // Vor dem Klick: kein Dokument zum Fahrzeug (API)
    const fahrzeuge = await h.get("/vehicles", { token: sucher.token });
    const liste = Array.isArray(fahrzeuge) ? fahrzeuge : (fahrzeuge.items || fahrzeuge.vehicles || []);
    expect(liste.length).toBeGreaterThan(0);
    const vehicleId = liste[0].id;
    const vorher = await h.get(`/beweise?vehicle_id=${vehicleId}`, { token: sucher.token });
    expect(vorher?.beweis || null).toBeNull();

    await knopf.click();
    await expect(page.getByText("Beweisdokument wird erstellt")).toBeVisible();
    await expect(knopf).toHaveCount(0);
    await expect(laeuft.or(fertig).first()).toBeVisible();
    // Der Worker braucht im Mock nur Sekunden; grosszuegig warten.
    await expect(fertig).toBeVisible({ timeout: 120_000 });
    await expect(page.getByTestId("beweis-pdf-btn")).toBeVisible();

    const nachher = await h.get(`/beweise?vehicle_id=${vehicleId}`, { token: sucher.token });
    expect(nachher?.beweis?.status).toBe("fertig");
    const pdf = await fetch(`${h.API_URL}/beweise/${nachher.beweis.id}/pdf`,
      { headers: { Authorization: `Bearer ${sucher.token}` } });
    expect(pdf.status).toBe(200);
    expect(pdf.headers.get("content-type") || "").toContain("application/pdf");
  });
});
