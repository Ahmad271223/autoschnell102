// Abholprotokoll auf dem Handy: Freigabe durch den Chef vor den
// Unterschriften — und die Knopfleiste, die dabei sichtbar und BEDIENBAR
// bleiben muss.
//
// Hintergrund (Befund Ahmad 12.09.2026): "unten die button beim fertigen
// abholprotokoll sind nicht sichtbar". Ursache war kein fehlendes Rendern,
// sondern die Fahrer-Tableiste (z-40), unter der die Aktionsleiste ohne
// z-index lag. Genau das prueft dieser Test — mit echtem Klick, denn ein
// verdecktes Element ist fuer Playwright "sichtbar", aber nicht klickbar.
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

const HANDY = { width: 375, height: 812 };   // iPhone-Groesse

test.describe("Abholprotokoll: Freigabe und Knopfleiste", () => {
  let firma, driver, appt, vehicleId;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    driver = await h.createDriver();
    await h.post("/drivers/add", { driver_code: driver.driverCode }, { token: firma.token });
    const v = await h.post("/vehicles/manual", {
      make_label: "Chevrolet", model_label: "Camaro", first_registration: "01/2021",
      mileage: 75000, color: "Weiß", fuel_label: "Benzin",
    }, { token: firma.token });
    vehicleId = v.id || v.vehicle_id;
    // Ohne Kaufvertrag (der braeuchte ein Abo) — fuer diesen Test zaehlt
    // der Protokoll-Ablauf, nicht die Vertragsanlage.
    appt = await h.createAppointment(firma, {
      title: `E2E Abholung ${firma.s}`, pickup_date: h.isoDate(0),
      pickup_time: "10:00", driver_id: driver.id, status: "offen",
      vehicle_id: vehicleId, seller_name: "Max Verkaeufer",
      pickup_address: "Musterstrasse 1, 30159 Hannover",
    });
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma], drivers: [driver] });
  });

  test("Knopfleiste liegt ueber der Tableiste und ist klickbar", async ({ page, browser }) => {
    await page.setViewportSize(HANDY);
    await page.goto("/fahrer/login");
    await page.getByTestId("driver-login-email").fill(driver.email);
    await page.getByTestId("driver-login-password").fill(driver.password);
    await page.getByTestId("driver-login-submit").click();
    await expect(page).toHaveURL(/\/fahrer\/?$/);

    // Fahrt annehmen, dann das Protokoll oeffnen
    const card = page.getByTestId(`appt-${appt.id}`);
    await card.locator("button").first().click();
    await card.getByTestId(`zuteilung-annehmen-${appt.id}`).click();
    await page.goto(`/fahrer/protokoll/${appt.id}`);
    await expect(page.getByTestId("protokoll-page")).toBeVisible();

    // --- Der eigentliche Befund: Leiste sichtbar UND nicht verdeckt ---
    const leiste = page.getByTestId("protokoll-aktionen");
    await expect(leiste).toBeVisible();
    const tabs = page.getByTestId("fahrer-tableiste");
    const a = await leiste.boundingBox();
    const t = await tabs.boundingBox();
    expect(a, "Aktionsleiste hat keine Ausdehnung").not.toBeNull();
    expect(t, "Tableiste hat keine Ausdehnung").not.toBeNull();
    // Unterkante der Aktionsleiste darf die Oberkante der Tabs nicht
    // ueberlappen — sonst liegt eine auf der anderen.
    expect(a.y + a.height,
      `Aktionsleiste (${a.y}..${a.y + a.height}) ueberlappt die Tableiste (ab ${t.y})`)
      .toBeLessThanOrEqual(t.y + 1);
    // Und sie muss komplett im Bild liegen.
    expect(a.y + a.height).toBeLessThanOrEqual(HANDY.height);

    // --- Klicken beweist, dass nichts darueber liegt ---
    // Unvollstaendig -> der Server lehnt ab; entscheidend ist, dass der
    // Klick ueberhaupt ankommt (verdeckte Knoepfe wirft Playwright ab).
    page.once("dialog", (d) => d.accept());
    await page.getByTestId("protokoll-zur-freigabe").click({ timeout: 5000 });
  });

  test("Fahrer schickt ab, Chef gibt mit neuem Preis frei, dann Unterschriften", async ({ page, browser }) => {
    await page.setViewportSize(HANDY);
    // Einmal-Sitzung: der Formular-Login im ersten Test hat das alte Token
    // entwertet — hier ein frisches holen.
    const tok = (await h.post("/driver/login",
      { email: driver.email, password: driver.password })).token;
    await h.authPage(page, "driver", tok);

    // Protokoll vollstaendig ueber die API fuellen (die Tipparbeit selbst
    // deckt die Unit-Ebene ab; hier geht es um den Ablauf).
    const tpl = await h.get(`/driver/appointments/${appt.id}/protocol`, { token: tok });
    const felder = tpl.template.vehicle_check_fields.map((f) => f.key);
    await h.put(`/driver/appointments/${appt.id}/protocol`, {
      vehicle_check: Object.fromEntries(felder.map((k) => [k, { status: "stimmt" }])),
      keys_count: "2", condition: { mileage: "75200" }, damages_confirmed: true,
      place: "Hannover", notes: "E2E",
    }, { token: tok });

    await page.goto(`/fahrer/protokoll/${appt.id}`);
    page.once("dialog", (d) => d.accept());
    await page.getByTestId("protokoll-zur-freigabe").click();
    await expect(page.getByTestId("protokoll-wartet")).toBeVisible();
    // Solange es beim Chef liegt, gibt es keine Unterschriftsfelder.
    await expect(page.getByText("Mit dem Finger unterschreiben")).toHaveCount(0);

    // --- Chef: eigene Seite "Freigaben" (Runde 33, Wunsch Ahmad) ---
    const chef = await h.newAuthedPage(browser, "app", firma.token);
    try {
      // Der Zaehler im Menue zeigt auf JEDER Seite, dass ein Fahrer wartet.
      await chef.page.goto("/app/termine");
      await expect(chef.page.getByTestId("nav-freigaben-zaehler")).toHaveText("1");
      await chef.page.getByTestId("termine-freigaben-hinweis").click();
      await expect(chef.page).toHaveURL(/\/app\/freigaben$/);
      const proto = await h.get("/protocols/zur-freigabe", { token: firma.token });
      const pid = proto[0].protocol_id;
      const karte = chef.page.getByTestId(`freigabe-${pid}`);
      await expect(karte).toBeVisible();
      await expect(karte).toContainText("Chevrolet Camaro");
      // Vorher/Nachher: Kilometerstand bei Abholung einheitlich formatiert.
      await expect(karte).toContainText("75.200 km");
      // Deutscher Tausenderpunkt: "17.250" muss 17250 EUR ergeben, nicht 17.
      await chef.page.getByTestId(`freigabe-preis-${pid}`).fill("17.250");
      await chef.page.getByTestId(`freigabe-ok-${pid}`).click();
      await expect(chef.page.getByTestId(`freigabe-aktueller-preis-${pid}`)).toContainText("17.250,00");
    } finally {
      await chef.context.close();
    }

    // --- Fahrer: jetzt erst unterschreiben ---
    await page.reload();
    await expect(page.getByTestId("protokoll-freigegeben")).toBeVisible();
    await expect(page.getByTestId("protokoll-neuer-preis")).toContainText("17.250,00");
    await expect(page.getByTestId("protokoll-abschliessen")).toBeVisible();
    const leiste = await page.getByTestId("protokoll-aktionen").boundingBox();
    const tabs = await page.getByTestId("fahrer-tableiste").boundingBox();
    expect(leiste.y + leiste.height).toBeLessThanOrEqual(tabs.y + 1);
  });
});
