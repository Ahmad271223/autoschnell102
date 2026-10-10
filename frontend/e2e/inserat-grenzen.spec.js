// Inserat-Grenzen im Browser (Pruefbericht 20.09.2026, T-25; Regeln vom
// 20.09.2026, Ahmad): hoechstens 10 eigene Fotos und 500 Zeichen Beschreibung
// je Inserat — die Oberflaeche schneidet ab und sagt es, der Server lehnt ab.
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

test.describe("Inserat: 10 Fotos, 500 Zeichen", () => {
  let firma, listingId;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    const v = await h.post("/vehicles/manual", {
      make_label: "Audi", model_label: "A4", model_description: `E2E Grenzen ${firma.s}`,
      first_registration: "05/2020", mileage: 60000, fuel_label: "Diesel",
      gearbox_label: "Automatik", color: "Grau", purchase_price: 15000,
    }, { token: firma.token });
    const draft = await h.post(`/resale/draft/${v.id}`, {}, { token: firma.token });
    listingId = draft.id;
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma] });
  });

  test("Beschreibung endet bei 500 Zeichen, Server lehnt 501 ab", async ({ page }) => {
    await h.authPage(page, "app", firma.token);
    await page.goto(`/app/inserat/${listingId}`);
    await expect(page.getByTestId("inserat-page")).toBeVisible();
    const feld = page.locator('textarea[maxlength="500"]');
    await expect(feld).toBeVisible();
    await feld.fill("A".repeat(490));
    await feld.press("End");
    await page.keyboard.type("B".repeat(20));         // ueber die Grenze tippen
    await expect(feld).toHaveValue(/^A{490}B{10}$/);
    await expect(page.getByTestId("beschreibung-zaehler")).toHaveText("500 / 500");

    // Server: 501 Zeichen -> 422 (Pydantic-Grenze INSERAT_BESCHREIBUNG_MAX)
    const r = await h.api("PUT", `/resale/${listingId}`,
      { token: firma.token, body: { description: "C".repeat(501) }, ok: false });
    expect(r.status).toBe(422);
    const ok = await h.api("PUT", `/resale/${listingId}`,
      { token: firma.token, body: { description: "C".repeat(500) }, ok: false });
    expect(ok.status).toBe(200);
  });

  test("Fotos: 11 ausgewaehlt -> 10 uebernommen, danach 'schon 10 Fotos'", async ({ page }) => {
    await h.authPage(page, "app", firma.token);
    await page.goto(`/app/inserat/${listingId}`);
    await expect(page.getByTestId("inserat-page")).toBeVisible();
    await expect(page.getByTestId("fotos-regel")).toContainText("0 / 10 eigene Fotos");

    // Echtes (winziges) JPEG aus dem Browser — der Upload verkleinert ueber
    // createImageBitmap, ein reiner Magic-Bytes-Rumpf wuerde dort scheitern.
    const dataUrl = await page.evaluate(() => {
      const c = document.createElement("canvas");
      c.width = 48; c.height = 32;
      const x = c.getContext("2d");
      x.fillStyle = "#b91c1c"; x.fillRect(0, 0, 48, 32);
      x.fillStyle = "#fde68a"; x.fillRect(8, 8, 32, 16);
      return c.toDataURL("image/jpeg", 0.9);
    });
    const buffer = Buffer.from(dataUrl.split(",")[1], "base64");
    const datei = (i) => ({ name: `e2e-foto-${i}.jpg`, mimeType: "image/jpeg", buffer });

    await page.locator('input[type="file"]').setInputFiles(Array.from({ length: 11 }, (_, i) => datei(i)));
    await expect(page.getByText("Es werden 10 von 11 Fotos übernommen (maximal 10 je Inserat).")).toBeVisible();
    await expect(page.getByTestId("fotos-regel")).toContainText("10 / 10 eigene Fotos", { timeout: 60_000 });
    const inserat = await h.get(`/resale/${listingId}`, { token: firma.token });
    expect((inserat.photos?.uploaded_keys || []).length).toBe(10);

    // Voll: die Oberflaeche laedt gar nicht erst hoch
    await page.locator('input[type="file"]').setInputFiles([datei(99)]);
    await expect(page.getByText("Dieses Inserat hat schon 10 Fotos — bitte zuerst eines entfernen.")).toBeVisible();
    // ... und der Server lehnt ein elftes Foto ab (400, "Maximal 10 Fotos")
    const r = await h.api("POST", `/resale/${listingId}/photos`,
      { token: firma.token, body: { photos_b64: [h.TEST_FOTO] }, ok: false });
    expect(r.status).toBe(400);
    expect(String(r.data?.detail || "")).toContain("Maximal 10 Fotos");
    const danach = await h.get(`/resale/${listingId}`, { token: firma.token });
    expect((danach.photos?.uploaded_keys || []).length).toBe(10);
  });
});
