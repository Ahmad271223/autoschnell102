// Kundenportal (Wunsch Ahmad 29.09.2026): Betreiber richtet die Firmenseite ein (Weg A), Sucher gibt einen
// Kaufvertrag frei (6-stelliger Code), der Kunde oeffnet ihn auf der Firmenseite, sieht die PDF-Vorschau,
// unterschreibt mit der Maus und sendet ab; Sucher und Chef bekommen die Meldung, die Vertragsliste zeigt
// "digital unterschrieben", der Code ist danach tot.
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

test.describe("Kundenportal: Firmenseite, Code, digitale Unterschrift", () => {
  let firma, sucher, slug;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    sucher = await h.createSucher(firma, { abo: true });
    slug = `e2e-${firma.s}`.toLowerCase().replace(/[^a-z0-9-]/g, "-").slice(0, 40).replace(/-+$/, "");
    const st = await h.superPut(`/admin/dealers/${firma.dealerId}/webseite`, {
      slug, aktiv: true, ueber_uns: "Wir kaufen Ihr Auto — fair und schnell.",
      titel: "Ihr Partner für den Autoankauf in Testhausen", untertitel: "Schnell, sicher & fair",
      instagram: "https://www.instagram.com/autoschnell",
    });
    expect(st.webseite.slug).toBe(slug);
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma] });
  });

  test("Freigabe -> Firmenseite -> Vorschau -> Unterschrift -> Meldung", async ({ page }) => {
    const vgl = await h.compareMock(sucher, "portal");
    test.skip(!vgl, "Mock-Vergleich nicht verfuegbar (MOCK_PROVIDER_FETCH)");
    const vertrag = await h.post("/contracts", {
      vehicle_id: vgl.vehicleId, seller_name: "Erika Mustermann", seller_address: "Musterweg 2",
      seller_zip: "10115", seller_city: "Berlin", seller_phone: "+49 170 1234567", seller_email: "erika@example.org",
      purchase_price: 12500, pickup_date: h.isoDate(3), pickup_time: "10:00",
    }, { token: sucher.token });
    expect(vertrag.id).toBeTruthy();

    // Sucher gibt frei: Code aus dem sicheren Alphabet, gebunden an Fassung 1
    const frei = await h.post(`/contracts/${vertrag.id}/portal`, {}, { token: sucher.token });
    expect(frei.status).toBe("offen");
    expect(frei.code).toMatch(/^[A-HJ-NP-Z2-9]{6}$/);
    expect(frei.url).toContain(slug);

    // Kunde: Firmenseite nach Vorlage (Kopf, Ueberschrift, Karte, Fusszeile)
    await page.goto(`/firma/${slug}`);
    await expect(page.getByTestId("firmenseite-name")).toContainText(firma.companyName);
    await expect(page.getByTestId("firmenseite-ueberschrift")).toContainText("Autoankauf in Testhausen");
    await expect(page.getByTestId("firmenseite-unterzeile")).toContainText("Schnell, sicher & fair");
    await expect(page.getByTestId("firmenseite-ueber-uns")).toContainText("Wir kaufen Ihr Auto");
    await expect(page.getByTestId("firmenseite-social")).toBeVisible();
    await expect(page.getByTestId("firmenseite-datenschutz")).toHaveAttribute("href", /\/datenschutz$/);
    await expect(page.getByTestId("portal-oeffnen")).toBeDisabled();

    // falscher Code -> Klartext, richtiger Code (klein getippt) -> Vertrag
    await page.getByTestId("portal-code").fill("abcdef");
    await expect(page.getByTestId("portal-code")).toHaveValue("ABCDEF");
    await page.getByTestId("portal-oeffnen").click();
    await expect(page.getByTestId("portal-fehler")).toContainText("Code ungültig");
    await page.getByTestId("portal-code").fill(frei.code.toLowerCase());
    await page.getByTestId("portal-oeffnen").click();
    await expect(page.getByTestId("portal-vertrag")).toContainText(vertrag.contract_no);
    await expect(page.getByTestId("portal-vertrag")).toContainText("Erika Mustermann");
    await expect(page.getByTestId("portal-vertrag")).toContainText("12.500");
    // PDF-Vorschau direkt auf der Seite (pdf.js)
    await expect(page.locator('[data-testid="portal-seiten"] canvas').first()).toBeVisible({ timeout: 30_000 });

    // ohne Unterschrift/Zustimmung: Hinweise, kein Absenden
    await page.getByTestId("portal-absenden").click();
    await expect(page.getByTestId("portal-fehler")).toContainText("unterschreiben");
    const pad = page.locator('[data-testid="portal-unterschrift"] canvas');
    const box = await pad.boundingBox();
    expect(box.width).toBeGreaterThan(200);
    await page.mouse.move(box.x + 20, box.y + box.height / 2);
    await page.mouse.down();
    for (let i = 1; i <= 40; i += 1) {
      await page.mouse.move(box.x + 20 + (i * (box.width - 40)) / 40, box.y + box.height / 2 + Math.sin(i / 3) * 30);
    }
    await page.mouse.up();
    await page.getByTestId("portal-absenden").click();
    await expect(page.getByTestId("portal-fehler")).toContainText("gelesen");
    await page.getByTestId("portal-einverstanden").check();
    await page.getByTestId("portal-absenden").click();
    await expect(page.getByTestId("portal-fertig")).toContainText("Vielen Dank");
    await expect(page.getByTestId("portal-unterschrift")).toHaveCount(0);

    // Backend: unterschrieben, PDF mit Unterschrift, Code tot, Meldung fuer Sucher UND Chef
    const stand = await h.get(`/contracts/${vertrag.id}/portal`, { token: sucher.token });
    expect(stand.status).toBe("unterschrieben");
    expect(stand.name).toBe("Erika Mustermann");
    expect(stand.pdf_signiert).toBe(true);
    const nochmal = await h.api("POST", "/public/portal/oeffnen", { body: { code: frei.code, slug }, ok: false });
    expect(nochmal.status).toBe(404);
    const zSucher = await h.get("/meldungen/anzahl", { token: sucher.token });
    const zChef = await h.get("/meldungen/anzahl", { token: firma.token });
    expect(zSucher.ungelesen).toBe(1);
    expect(zChef.ungelesen).toBe(1);

    // Sucher in der App: Meldung, Badge in der Vertragsliste, unterschriebenes PDF abrufbar
    await h.authPage(page, "app", sucher.token);
    await page.goto("/app/meldungen");
    await expect(page.getByTestId(`meldung-${zSucher.ids[0]}`)).toContainText("Erika Mustermann");
    await page.goto("/app/vertraege");
    await expect(page.getByTestId(`portal-badge-${vertrag.id}`)).toContainText("digital unterschrieben");
    const pdf = await h.api("GET", `/contracts/${vertrag.id}/portal/pdf`, { token: sucher.token, ok: false });
    expect(pdf.status).toBe(200);
  });

  test("Firmenseite aus: Kunde bekommt eine klare Meldung", async ({ page }) => {
    await h.superPut(`/admin/dealers/${firma.dealerId}/webseite`, { aktiv: false });
    await page.goto(`/firma/${slug}`);
    await expect(page.getByTestId("firmenseite-fehler")).toContainText("keine Firmenseite");
    await h.superPut(`/admin/dealers/${firma.dealerId}/webseite`, { aktiv: true });
  });
});
