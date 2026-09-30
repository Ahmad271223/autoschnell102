// Kompletter Lauf (Wunsch Ahmad 30.09.2026): EIN Fahrzeug von der Kontenanlage bis zum abgeschlossenen
// Abholprotokoll — alle Rollen hintereinander, jede mit echter Anmeldung ueber ihr Formular:
//
//   Betreiber  legt Firma (Chef), Sucher und Fahrer an, richtet die Firmenseite ein
//   Chef       verbindet den Fahrer (Fahrer-Code)
//   Sucher     vergleicht ein Inserat, laesst die bekannte Delle bewerten (Vorschau / KI-Schadennachlass),
//              erstellt den Kaufvertrag mit diesem Schaden (Termin entsteht von selbst),
//              erzeugt den Code fuers Kundenportal
//   Kunde      unterschreibt den Vertrag auf der Firmenseite
//   Chef       teilt den Fahrer im Terminplaner zu
//   Fahrer     nimmt an, fuellt das Protokoll (Kilometer weichen ab, neuer Kratzer, Preisvorschlag),
//              schickt es zur Freigabe
//   Chef       sieht Abweichung + Schaden (+ KI-Bewertung), gibt mit neuem Preis frei
//   Fahrer     laesst unterschreiben, schliesst ab -> Protokoll-PDF, Termin "abgeholt"
//   danach     Vertrag Fassung 2 mit neuem Preis; die Online-Unterschrift gilt nur fuer Fassung 1
//              (Hinweis in Vertragsliste, Dialog und Meldungen)
//
// KI: laeuft im CI ohne Schluessel ("aus"). Lokal mit E2E_KI=1 und einem Backend mit
// KI_BEWERTUNG_AKTIV=true + ANTHROPIC_API_KEY rechnen Schadennachlass (Vertrag) und Abholbewertung ECHT
// (zusammen rund 15 Cent je Lauf).
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

const HANDY = { width: 390, height: 844 };
const KI_ECHT = process.env.E2E_KI === "1";
const PREIS_VERTRAG = 12500;
const PREIS_NEU = 11900;
const KM_VOR_ORT = 96500;
// Bekannter Schaden laut Inserat/Verkaeufer — steht im Vertrag, der Fahrer bestaetigt ihn nur.
const DELLE = { view: "right", zone: "Kotflügel hinten rechts", x: 1100, y: 520, type_key: "delle", type_label: "Delle",
                severity_data: { groesse: "2–5 cm", lack: "nein", lage: "Fläche" } };

/** Mit der Maus in ein Unterschriftsfeld schreiben (Feld vorher in die Bildmitte holen). */
async function unterschreiben(page, canvas) {
  await canvas.evaluate((el) => el.scrollIntoView({ block: "center" }));
  const box = await canvas.boundingBox();
  expect(box, "Unterschriftsfeld hat keine Ausdehnung").not.toBeNull();
  expect(box.width).toBeGreaterThan(200);
  const mitte = box.y + box.height / 2;
  await page.mouse.move(box.x + 15, mitte);
  await page.mouse.down();
  for (let i = 1; i <= 30; i += 1) {
    await page.mouse.move(box.x + 15 + (i * (box.width - 30)) / 30, mitte + Math.sin(i / 3) * (box.height / 4));
  }
  await page.mouse.up();
}

/** Bild der Seite in den Testergebnis-Ordner (test-results/…) — Beleg fuer den Durchlauf. */
async function bild(page, testInfo, name) {
  await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: true });
}

/** Token, das die Formular-Anmeldung in den Browser gelegt hat (Single-Session: nur dieses gilt noch). */
async function tokenAusBrowser(page, key) {
  await expect.poll(() => page.evaluate((k) => window.localStorage.getItem(k), key)).toBeTruthy();
  return page.evaluate((k) => window.localStorage.getItem(k), key);
}

test.describe("Kompletter Lauf: Betreiber -> Sucher -> Kunde -> Chef -> Fahrer -> Abholprotokoll", () => {
  test.describe.configure({ mode: "serial" });

  let firma, sucher, driver, slug;
  let chefToken, sucherToken, fahrerToken;
  let vertrag, termin, code, protokollId;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    sucher = await h.createSucher(firma, { abo: true });
    driver = await h.createDriver();
    slug = `lauf-${firma.s}`;
    await h.superPut(`/admin/dealers/${firma.dealerId}/webseite`, {
      slug, aktiv: true, ueber_uns: "Wir kaufen Ihr Auto — fair und schnell.",
    });
    if (KI_ECHT) {
      // KI je Konto: Sucher (Schadennachlass im Vertrag), Chef (Abholbewertung der Firma) und Fahrer des Termins
      await h.superPost(`/admin/sucher/${sucher.userId}/ki`, { aktiv: true });
      await h.superPost(`/admin/sucher/${firma.userId}/ki`, { aktiv: true });
      await h.superPost(`/admin/drivers/${driver.id}/ki`, { aktiv: true });
    }
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma], drivers: [driver] });
  });

  test("1 Betreiber hat die Konten angelegt; Chef meldet sich an und verbindet den Fahrer", async ({ page }) => {
    const konten = await h.superGet("/admin/users");
    const chefKonto = konten.find((u) => u.id === firma.userId);
    const sucherKonto = konten.find((u) => u.id === sucher.userId);
    expect(chefKonto?.role).toBe("dealer");
    expect(sucherKonto?.role).toBe("sucher");
    expect(String(sucherKonto.kontonummer)).toBe(`${firma.kontonummer}-1`);        // Sucher = Chef-Nummer + Zusatz
    expect((await h.superGet("/admin/drivers")).some((d) => d.id === driver.id)).toBe(true);

    await h.formLogin(page, "auth", firma);
    await expect(page).toHaveURL(/\/app\//);
    chefToken = await tokenAusBrowser(page, "ah_token");

    await page.goto("/app/fahrer");
    await page.getByTestId("driver-code-input").fill(driver.driverCode);
    await page.getByTestId("add-driver-btn").click();
    await expect(page.getByTestId(`driver-row-${driver.id}`)).toContainText(driver.displayName);
  });

  test("2 Sucher: Vergleich, Schadennachlass, Kaufvertrag, Termin entsteht, Code fuers Kundenportal", async ({ page }) => {
    test.setTimeout(KI_ECHT ? 300_000 : 60_000);
    await h.formLogin(page, "auth", sucher);
    await expect(page).toHaveURL(/\/app\//);
    sucherToken = await tokenAusBrowser(page, "ah_token");

    const vgl = await h.compareMock({ token: sucherToken }, "lauf");
    test.skip(!vgl, "Mock-Vergleich nicht verfuegbar (MOCK_PROVIDER_FETCH)");

    // Schadennachlass fuer die bekannte Delle: die Vorschau rechnet ohne KI (keine Kosten) ...
    const anfrage = { vehicle_id: vgl.vehicleId, damages: [DELLE], purchase_price: PREIS_VERTRAG };
    const vorschau = await h.post("/contracts/ki-schadennachlass/vorschau", anfrage, { token: sucherToken });
    expect(vorschau.vorlaeufig).toBe(true);
    expect(vorschau.fair_discount_eur).toBeGreaterThan(0);
    expect(vorschau.recommended_purchase_price_eur).toBe(PREIS_VERTRAG - vorschau.fair_discount_eur);
    // ... die KI-Bewertung nur mit freigeschaltetem Konto (rein beratend, aendert nichts am Vertrag)
    let nachlass = await h.post("/contracts/ki-schadennachlass", anfrage, { token: sucherToken });
    if (KI_ECHT) {
      for (let i = 0; i < 100 && nachlass.status === "laeuft"; i += 1) {
        await page.waitForTimeout(2000);
        nachlass = await h.get(`/contracts/ki-schadennachlass/${nachlass.id}`, { token: sucherToken });
      }
      expect(nachlass.status).toBe("ok");
      expect(nachlass.ergebnis, "KI-Schadennachlass ohne Ergebnis").toBeTruthy();
      console.log(`[kompletter-lauf] KI Vertrag: ${JSON.stringify(nachlass.ergebnis.combined || nachlass.ergebnis).slice(0, 400)}`
        + ` — Kosten ${nachlass.kosten_ct} ct`);
    } else {
      expect(["aus", "freischaltung"]).toContain(nachlass.status);
      expect(nachlass.ergebnis).toBeNull();
    }

    vertrag = await h.post("/contracts", {
      vehicle_id: vgl.vehicleId, seller_name: "Erika Mustermann", seller_address: "Musterweg 2",
      seller_zip: "10115", seller_city: "Berlin", seller_phone: "+49 170 1234567", seller_email: "erika@example.org",
      purchase_price: PREIS_VERTRAG, pickup_date: h.isoDate(0), pickup_time: "10:00",
      damages: [DELLE], schluessel_anzahl: "2",
    }, { token: sucherToken });
    expect(vertrag.contract_no).toBeTruthy();
    expect(vertrag.contract_data.damages).toHaveLength(1);

    // Der Abholtermin entsteht mit dem Vertrag — der Sucher sieht ihn, noch ohne Fahrer
    const termine = await h.get("/appointments", { token: sucherToken });
    termin = termine.find((t) => t.contract_id === vertrag.id);
    expect(termin, "Termin zum Vertrag fehlt").toBeTruthy();
    expect(termin.status).toBe("offen");
    expect(termin.seller_name).toBe("Erika Mustermann");
    expect(termin.driver_id || null).toBeNull();

    // Vertragsliste: Kundenportal-Knopf -> Code erzeugen
    await page.goto("/app/vertraege");
    await page.getByTestId(`kundenportal-${vertrag.id}`).click();
    await page.getByTestId("kundenportal-erzeugen").click();
    await expect(page.getByTestId("kundenportal-code")).toHaveText(/^[A-HJ-NP-Z2-9]{6}$/);
    code = (await page.getByTestId("kundenportal-code").textContent()).trim();
    await expect(page.getByTestId("kundenportal-url")).toHaveAttribute("href", new RegExp(slug));
  });

  test("3 Kunde unterschreibt den Vertrag auf der Firmenseite", async ({ page }) => {
    test.skip(!code, "kein Code aus Schritt 2");
    await page.goto(`/firma/${slug}`);
    await expect(page.getByTestId("firmenseite-name")).toContainText(firma.companyName);
    await page.getByTestId("portal-code").fill(code.toLowerCase());
    await page.getByTestId("portal-oeffnen").click();
    await expect(page.getByTestId("portal-vertrag")).toContainText(vertrag.contract_no);
    await expect(page.getByTestId("portal-vertrag")).toContainText("12.500");
    await expect(page.locator('[data-testid="portal-seiten"] canvas').first()).toBeVisible({ timeout: 30_000 });
    await unterschreiben(page, page.locator('[data-testid="portal-unterschrift"] canvas'));
    await page.getByTestId("portal-einverstanden").check();
    await page.getByTestId("portal-absenden").click();
    await expect(page.getByTestId("portal-fertig")).toContainText("Vielen Dank");

    const stand = await h.get(`/contracts/${vertrag.id}/portal`, { token: sucherToken });
    expect(stand.status).toBe("unterschrieben");
    expect(stand.unterschrieben_version).toBe(1);
    // Meldung fuer Sucher UND Chef
    expect((await h.get("/meldungen/anzahl", { token: sucherToken })).ungelesen).toBe(1);
    expect((await h.get("/meldungen/anzahl", { token: chefToken })).ungelesen).toBe(1);
  });

  test("4 Chef teilt den Fahrer zu, Fahrer meldet sich an und nimmt die Fahrt an", async ({ page, browser }) => {
    test.skip(!termin, "kein Termin aus Schritt 2");
    // Chef: Terminplaner, Liste, Termin oeffnen, Fahrer waehlen, speichern
    await h.authPage(page, "app", chefToken);
    await page.goto("/app/termine");
    await page.getByTestId("view-list").click();
    await page.getByTestId(`appt-open-${termin.id}`).click();
    await expect(page.getByTestId("edit-appt-dialog")).toBeVisible();
    await page.getByTestId("edit-driver").selectOption(driver.id);
    await page.getByTestId("save-appt-btn").click();
    await expect(page.getByTestId("edit-appt-dialog")).toHaveCount(0);
    await expect(page.getByTestId(`fahrer-${termin.id}`)).toContainText(driver.displayName);
    await expect(page.getByTestId(`fahrer-${termin.id}`)).toContainText("wartet auf Annahme");

    // Fahrer: Anmeldung auf dem Handy, Fahrt annehmen
    const fahrer = await browser.newContext({ viewport: HANDY, locale: "de-DE", serviceWorkers: "block" });
    try {
      const fp = await fahrer.newPage();
      await h.formLogin(fp, "driver", driver);
      await expect(fp).toHaveURL(/\/fahrer\/?$/);
      fahrerToken = await tokenAusBrowser(fp, "ah_driver_token");
      const karte = fp.getByTestId(`appt-${termin.id}`);
      await expect(karte).toBeVisible();
      await karte.locator("button").first().click();
      await expect(karte.getByText("Neue Fahrt zugeteilt")).toBeVisible();
      await karte.getByTestId(`zuteilung-annehmen-${termin.id}`).click();
      await expect(karte.getByTestId(`mark-pickedup-${termin.id}`)).toBeVisible();
    } finally {
      await fahrer.close();
    }
    await page.reload();
    await page.getByTestId("view-list").click();
    await expect(page.getByTestId(`fahrer-${termin.id}`)).toContainText("angenommen");
  });

  test("5 Fahrer fuellt das Abholprotokoll (Abweichung, neuer Schaden, Preisvorschlag) und schickt es zur Freigabe", async ({ page }) => {
    test.skip(!fahrerToken, "Fahrer nicht angemeldet (Schritt 4)");
    await page.setViewportSize(HANDY);
    await h.authPage(page, "driver", fahrerToken);

    const tpl = await h.get(`/driver/appointments/${termin.id}/protocol`, { token: fahrerToken });
    expect(tpl.preis_vertrag).toBe(PREIS_VERTRAG);
    expect(tpl.damages).toHaveLength(1);                            // die Delle aus dem Vertrag
    expect(String(tpl.template.keys_expected)).toBe("2");           // Schluessel laut Vertrag
    const zeilen = tpl.template.vehicle_check_fields;
    const fahrzeug = Object.fromEntries(zeilen.map((f) => [f.key, { status: f.options[0] }]));
    fahrzeug.mileage_contract = { status: "weicht ab", value: String(KM_VOR_ORT) };   // Vertrag: 90.000 km
    fahrzeug.commercial = { status: "Nein" };
    const zustand = Object.fromEntries(tpl.template.condition_fields.map((f) =>
      [f.key, f.key === "mileage" ? String(KM_VOR_ORT) : (f.options ? f.options[0] : "5/5/4/4")]));
    zustand.fuel_level = "1/2";
    // Der neue Schaden geht bewusst OHNE id an den Server (die App vergibt sonst eine): der Server muss
    // selbst eine vergeben — ohne id verwarf die KI-Abholbewertung die Position (Befund 30.09.2026).
    const entwurf = await h.put(`/driver/appointments/${termin.id}/protocol`, {
      revision: tpl.protocol?.revision,
      vehicle_check: fahrzeug,
      documents: Object.fromEntries(tpl.template.documents.map((d) => [d, true])),
      features: Object.fromEntries((tpl.template.features || []).map((d) => [d, true])),
      keys_count: "2", keys_expected: "2", condition: zustand, damages_confirmed: true,
      new_damages: [{ view: "left", zone: "Tür vorne links", x: 520, y: 480, type_key: "kratzer", type_label: "Kratzer",
                      severity_data: { laenge: "15–30 cm", tiefe: "bis Grundierung", anzahl: "einzeln" } }],
      preis_vorschlag: PREIS_NEU,
      place: "Berlin", seller_name: "Erika Mustermann", seller_id_document: "L01X00T47",
      notes: "Kompletter Lauf: Kilometerstand höher als im Vertrag, neuer Kratzer an der Fahrertür.",
    }, { token: fahrerToken });
    expect(entwurf).toBeTruthy();
    const gespeichert = await h.get(`/driver/appointments/${termin.id}/protocol`, { token: fahrerToken });
    expect(gespeichert.protocol.new_damages).toHaveLength(1);
    expect(gespeichert.protocol.new_damages[0].id).toMatch(/^n-[0-9a-f]{12}$/);

    await page.goto(`/fahrer/protokoll/${termin.id}`);
    await expect(page.getByTestId("protokoll-page")).toBeVisible();
    await expect(page.getByTestId("vc-mileage_contract-wert")).toHaveValue(/96\.?500/);
    page.once("dialog", (d) => d.accept());
    await page.getByTestId("protokoll-zur-freigabe").click();
    await expect(page.getByTestId("protokoll-wartet")).toBeVisible();
    await expect(page.getByText("Mit dem Finger unterschreiben")).toHaveCount(0);

    // Chef sieht den Zaehler; die KI-Karte des Fahrers zeigt den Stand (rechnet oder ist aus)
    expect((await h.get("/protocols/zur-freigabe/anzahl", { token: chefToken })).wartet).toBe(1);
    const ki = await h.get(`/driver/appointments/${termin.id}/ki-bewertung`, { token: fahrerToken });
    expect(["ok", "laeuft", "keine", "aus", "freischaltung"]).toContain(ki.status);
  });

  test("6 Chef prueft Abweichung, Schaden und KI-Bewertung und gibt mit neuem Preis frei", async ({ page }, testInfo) => {
    test.skip(!fahrerToken, "Schritt 5 fehlt");
    test.setTimeout(KI_ECHT ? 300_000 : 60_000);
    await h.authPage(page, "app", chefToken);
    await page.goto("/app/termine");
    await expect(page.getByTestId("nav-freigaben-zaehler")).toHaveText("1");
    await page.getByTestId("termine-freigaben-hinweis").click();
    await expect(page).toHaveURL(/\/app\/freigaben$/);

    const offen = await h.get("/protocols/zur-freigabe", { token: chefToken });
    expect(offen).toHaveLength(1);
    protokollId = offen[0].protocol_id;
    const karte = page.getByTestId(`freigabe-${protokollId}`);
    await expect(karte).toContainText("VW Golf");
    await expect(karte).toContainText("96.500 km");                 // vor Ort
    await expect(karte).toContainText("90.000 km");                 // laut Vertrag
    await expect(karte).toContainText("Kratzer");
    await expect(page.getByTestId(`freigabe-vorschlag-${protokollId}`)).toContainText("11.900");

    // KI-Abholbewertung: rein beratend. Im CI aus; lokal mit E2E_KI=1 wird echt gerechnet.
    const kiKarte = page.getByTestId(`ki-karte-${protokollId}`);
    if (KI_ECHT) {
      await expect(kiKarte).toHaveAttribute("data-status", "ok", { timeout: 240_000 });
      const ki = await h.get(`/protocols/${protokollId}/ki-bewertung`, { token: chefToken });
      expect(ki.status).toBe("ok");
      expect(ki.ergebnis, "KI-Ergebnis fehlt").toBeTruthy();
      // beide Abweichungen bewertet (Kilometer + neuer Kratzer), je vier Geldwerte in aufsteigender Folge
      const { items, combined } = ki.ergebnis;
      expect(items.length).toBeGreaterThanOrEqual(2);
      expect(items.map((i) => i.category)).toEqual(expect.arrayContaining(["mileage", "damage"]));
      for (const teil of [...items, combined]) {
        expect(teil.minimum_justified_eur).toBeLessThanOrEqual(teil.fair_discount_eur);
        expect(teil.fair_discount_eur).toBeLessThanOrEqual(teil.best_realistic_eur);
        expect(teil.best_realistic_eur).toBeLessThanOrEqual(teil.negotiation_start_eur);
      }
      expect(combined.fair_discount_eur).toBeGreaterThan(0);
      expect(combined.fair_discount_eur).toBeLessThan(PREIS_VERTRAG * 0.9);        // Deckel 90 % vom Kaufpreis
      expect(combined.recommended_purchase_price_eur).toBe(PREIS_VERTRAG - combined.fair_discount_eur);
      await expect(kiKarte).toContainText("€");
      console.log(`[kompletter-lauf] KI: fair ${combined.fair_discount_eur} EUR Nachlass, empfohlener Preis `
        + `${combined.recommended_purchase_price_eur} EUR, Datenlage ${ki.ergebnis.datenlage}, `
        + `${items.map((i) => `${i.title}: ${i.fair_discount_eur} EUR`).join(" | ")}`);
      // die bekannte Delle aus dem Vertrag ist KEIN neuer Schaden — sie darf nicht noch einmal abgezogen werden
      expect(items.filter((i) => i.category === "damage")).toHaveLength(1);
      if (ki.kosten_ct != null) console.log(`[kompletter-lauf] KI Abholung: Kosten ${ki.kosten_ct} ct`);
      const fahrerSicht = await h.get(`/driver/appointments/${termin.id}/ki-bewertung`, { token: fahrerToken });
      expect(fahrerSicht.status).toBe("ok");
      // beratend: der Preis im Vertrag ist durch die KI NICHT veraendert worden
      const c = (await h.get("/contracts", { token: sucherToken })).find((x) => x.id === vertrag.id);
      expect(c.purchase_price).toBe(PREIS_VERTRAG);
    } else {
      const ki = await h.get(`/protocols/${protokollId}/ki-bewertung`, { token: chefToken });
      expect(["aus", "freischaltung", "keine"]).toContain(ki.status);
    }

    await page.getByTestId(`freigabe-preis-${protokollId}`).fill("11.900");
    await bild(page, testInfo, "chef-freigabe");
    await page.getByTestId(`freigabe-ok-${protokollId}`).click();
    await expect(page.getByTestId(`freigabe-aktueller-preis-${protokollId}`)).toContainText("11.900,00");
  });

  test("7 Fahrer laesst unterschreiben und schliesst ab: PDF, Termin abgeholt, Vertrag Fassung 2", async ({ page }, testInfo) => {
    test.skip(!protokollId, "Schritt 6 fehlt");
    await page.setViewportSize(HANDY);
    await h.authPage(page, "driver", fahrerToken);
    await page.goto(`/fahrer/protokoll/${termin.id}`);
    await expect(page.getByTestId("protokoll-freigegeben")).toBeVisible();
    await expect(page.getByTestId("protokoll-neuer-preis")).toContainText("11.900,00");

    // ohne Unterschriften kein Abschluss
    await page.getByTestId("protokoll-abschliessen").click();
    await expect(page.locator("[data-sonner-toast]").filter({ hasText: "beide Unterschriften" })).toBeVisible();

    const felder = page.locator("canvas");
    await expect(felder).toHaveCount(2);                            // Verkaeufer + Fahrer
    await unterschreiben(page, felder.nth(0));
    await unterschreiben(page, felder.nth(1));
    await bild(page, testInfo, "fahrer-unterschriften");
    page.once("dialog", (d) => d.accept());
    await page.getByTestId("protokoll-abschliessen").click();
    await expect(page.getByTestId("protokoll-pdf-unten")).toBeVisible({ timeout: 30_000 });

    // Protokoll final, PDF fuer Fahrer und Chef
    const nachher = await h.get(`/driver/appointments/${termin.id}/protocol`, { token: fahrerToken });
    expect(nachher.protocol.status).toBe("final");
    expect((await h.api("GET", `/driver/appointments/${termin.id}/protocol.pdf`, { token: fahrerToken, ok: false })).status).toBe(200);
    expect((await h.api("GET", `/protocols/${protokollId}.pdf`, { token: chefToken, ok: false })).status).toBe(200);
    // Termin abgeholt
    const t = await h.get(`/appointments/${termin.id}`, { token: chefToken });
    expect(t.status).toBe("abgeholt");
    // Vertrag: neue Fassung mit dem freigegebenen Preis, Ausweisnummer aus dem Protokoll
    const c = (await h.get("/contracts", { token: sucherToken })).find((x) => x.id === vertrag.id);
    expect(c.version).toBe(2);
    expect(c.purchase_price).toBe(PREIS_NEU);
    expect(c.contract_data.id_document).toBe("L01X00T47");
    expect(c.nach_abholung_aenderungen.preis).toBe(PREIS_NEU);
    expect(c.nach_abholung_aenderungen.preis_vorher).toBe(PREIS_VERTRAG);
    expect(c.nach_abholung_aenderungen.neue_schaeden).toBe(1);
    expect((await h.api("GET", `/contracts/${vertrag.id}/pdf`, { token: sucherToken, ok: false })).status).toBe(200);
    // Fassung 1 liegt im Archiv — und traegt die Online-Unterschrift des Kunden
    const fassungen = await h.get(`/contracts/${vertrag.id}/versions`, { token: sucherToken });
    expect(fassungen.map((f) => f.version)).toEqual([1]);
    expect(fassungen[0].kunde_unterschrieben_am).toBeTruthy();
    expect(fassungen[0].portal).toBeUndefined();
  });

  test("8 Sucher sieht: neue Fassung, Unterschrift gilt nur fuer Fassung 1, Meldungen", async ({ page }, testInfo) => {
    test.skip(!protokollId, "Schritt 7 fehlt");
    const stand = await h.get(`/contracts/${vertrag.id}/portal`, { token: sucherToken });
    expect(stand.status).toBe("unterschrieben_alt");
    expect(stand.unterschrieben_version).toBe(1);
    expect(stand.aktuelle_version).toBe(2);
    expect((await h.api("GET", `/contracts/${vertrag.id}/portal/pdf?fassung=1`, { token: sucherToken, ok: false })).status).toBe(200);
    expect((await h.api("GET", `/contracts/${vertrag.id}/portal/pdf?fassung=2`, { token: sucherToken, ok: false })).status).toBe(404);
    for (const token of [sucherToken, chefToken]) {
      const liste = await h.get("/meldungen", { token });
      expect(liste.map((m) => m.typ)).toEqual(expect.arrayContaining(["vertrag_unterschrieben", "vertrag_unterschrift_veraltet"]));
    }

    await h.authPage(page, "app", sucherToken);
    await page.goto("/app/vertraege");
    await expect(page.getByTestId(`portal-badge-${vertrag.id}`)).toContainText("Unterschrift nur für Fassung 1");
    await page.getByTestId(`kundenportal-${vertrag.id}`).click();
    await expect(page.getByTestId("kundenportal-status")).toContainText("nach der Unterschrift des Kunden geändert");
    await expect(page.getByTestId("kundenportal-unterschrieben-alt")).toContainText("Fassung 1 — aktuell ist Fassung 2");
    await expect(page.getByTestId("kundenportal-erzeugen")).toBeVisible();          // neuer Code fuer Fassung 2 moeglich
    await bild(page, testInfo, "sucher-unterschrift-alte-fassung");
    await page.goto("/app/meldungen");
    await expect(page.getByText("gilt nur für Fassung 1")).toBeVisible();
  });
});
