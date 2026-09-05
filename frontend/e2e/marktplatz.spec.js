// B2B-Marktplatz: Interesse -> Gegenangebot des Haendlers -> Annahme des Kaeufers.
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

test.describe("Marktplatz-Verhandlung", () => {
  let firma, listing, buyer;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    listing = await h.publishListing(firma);
    buyer = await h.createBuyer({ access: true });
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma], buyers: [buyer] });
  });

  test("Interesse senden, Gegenangebot, Annahme -> akzeptiert & reserviert", async ({ page, browser }) => {
    // Kaeufer: Inserat oeffnen und Interesse mit Angebot senden
    await h.authPage(page, "buyer", buyer.token);
    await page.goto("/markt");
    await expect(page.getByTestId("markt-page")).toBeVisible();
    const card = page.getByTestId(`markt-${listing.listingId}`);
    await expect(card).toBeVisible();
    await expect(card).toContainText("18.500 €");           // B2B-Preis fuer Zwischenhaendler
    await card.click();
    await page.getByTestId(`interesse-btn-${listing.listingId}`).click();
    await page.getByTestId("interesse-betrag").fill("17000");
    await page.getByTestId("interesse-nachricht").fill("E2E: Interesse am Fahrzeug");
    await page.getByTestId("interesse-senden").click();
    await expect(page.getByTestId("interesse-gesendet")).toBeVisible();

    const interessen = await h.get(`/dealer/interessen?listing_id=${listing.listingId}`, { token: firma.token });
    expect(interessen).toHaveLength(1);
    const iid = interessen[0].id;
    expect(interessen[0].offer).toBe(17000);

    // Haendler: Gegenangebot
    const dealer = await h.newAuthedPage(browser, "app", firma.token);
    try {
      await dealer.page.goto("/app/anfragen");
      const anfrage = dealer.page.getByTestId(`anfrage-${iid}`);
      await expect(anfrage).toBeVisible();
      await expect(anfrage).toContainText("17.000 €");
      await expect(anfrage).toContainText(buyer.companyName);
      await anfrage.getByTestId(`anfrage-gegenangebot-${iid}`).click();
      await anfrage.getByTestId(`gegenangebot-betrag-${iid}`).fill("18000");
      await anfrage.getByTestId(`gegenangebot-senden-${iid}`).click();
      await expect(anfrage).toContainText("Dein Gegenangebot: 18.000 €");
    } finally {
      await dealer.context.close();
    }

    // Kaeufer: Gegenangebot in "Meine Anfragen" annehmen
    await page.goto("/markt");
    await page.getByTestId("meine-anfragen-btn").click();
    const meine = page.getByTestId(`meine-anfrage-${iid}`);
    await expect(meine).toBeVisible();
    await expect(meine).toContainText("18.000 €");
    await meine.getByTestId(`gegenangebot-annehmen-${iid}`).click();
    await expect(meine).toContainText("akzeptiert");
    await expect(meine.getByTestId(`gegenangebot-annehmen-${iid}`)).toHaveCount(0);

    // Gegenprobe: Anfrage akzeptiert, Inserat reserviert
    const nachher = await h.get(`/dealer/interessen?listing_id=${listing.listingId}`, { token: firma.token });
    expect(nachher[0].status).toBe("akzeptiert");
    const inserat = await h.get(`/resale/${listing.listingId}`, { token: firma.token });
    expect(inserat.status).toBe("reserviert");
  });
});
