// Kontonummer (13.09.2026): Der Super-Admin legt Firma, Sucher, Zwischenhaendler
// und Fahrer ueber die Admin-Dialoge an, sieht jeweils die Kontonummer in der
// Zugangsdaten-Karte — und alle vier melden sich in ihrer Maske damit an.
//
// Kernfall (Gegenpruefung 14.09.2026): Sucher und Fahrer werden OHNE E-Mail
// angelegt (der Dialog sendet email:"") — Liste, Profil und Anmeldung laufen
// dann nur ueber die Kontonummer. Chef und Kaeufer behalten eine Kontakt-E-Mail
// nach dem E2E-Muster (e2e-<rolle>-<hex>@e2etest-mail.de), damit sweepLeftovers
// Reste eines abgebrochenen Laufs findet; der Sucher geht mit der Firma, der
// Fahrer wird im afterAll ueber seinen Namen gefunden.
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

test.describe("Super-Admin: Kontenanlage mit Kontonummer", () => {
  const s = h.suffix();
  const mails = {
    chef: `e2e-chef-${s}@e2etest-mail.de`,
    kaeufer: `e2e-kaeufer-${s}@e2etest-mail.de`,
  };
  const firmenName = `E2E Kontenanlage ${s}`;
  const fahrerName = `Fahrer ${s}`;

  test.afterAll(async () => {
    const users = await h.superGet("/admin/users");
    const drivers = await h.superGet("/admin/drivers");
    const chef = users.find((u) => u.email === mails.chef);
    const kaeufer = users.find((u) => u.email === mails.kaeufer);
    const fahrer = drivers.find((d) => d.display_name === fahrerName);
    await h.cleanup({
      firmen: chef ? [{ userId: chef.id, email: chef.email }] : [],
      buyers: kaeufer ? [{ id: kaeufer.id, email: kaeufer.email }] : [],
      drivers: fahrer ? [{ id: fahrer.id, email: fahrer.kontonummer }] : [],
    });
  });

  /** Zugangsdaten-Karte lesen und schliessen. */
  async function kontonummerAusKarte(page) {
    const nr = page.getByTestId("zugangsdaten-kontonummer");
    // Kontonummer (Chef/Sucher/Fahrer) oder Kaeufer-Code (14.09.2026, z. B. 6FE7K2M)
    await expect(nr).toHaveText(/^(\d+(-\d+)?|[A-HJ-NP-Z2-9]{6,9}|FD-[A-HJ-NP-Z2-9]{8})$/);
    const wert = (await nr.textContent()).trim();
    return wert;
  }

  test("anlegen ueber die Dialoge, danach Anmeldung in allen vier Masken", async ({ page, browser }) => {
    // Pruefbericht 20.09.2026 (AD-05/O2): "Fertig" fragt nach, ob das Passwort
    // notiert ist (sonst ist es verloren). Der Test hat es — er bestaetigt.
    const rueckfragen = [];
    page.on("dialog", (d) => { rueckfragen.push(d.message()); d.accept(); });
    await h.authPage(page, "app", await h.superAdmin());

    // 1. Firma (Chef-Konto)
    await page.goto("/admin/users");
    await page.getByTestId("admin-create-user-btn").click();
    await page.getByTestId("create-user-company").fill(firmenName);
    await page.getByTestId("create-user-email").fill(mails.chef);
    await page.getByTestId("create-user-password").fill(h.PASSWORD);
    await page.getByTestId("admin-create-user-submit").click();
    const chefNr = await kontonummerAusKarte(page);
    expect(chefNr).toMatch(/^\d+$/);
    await page.getByTestId("zugangsdaten-fertig").click();
    expect(rueckfragen.some((m) => m.includes("Passwort notiert"))).toBe(true);
    const chef = (await h.superGet("/admin/users")).find((u) => u.email === mails.chef);
    expect(chef?.kontonummer).toBe(chefNr);
    await expect(page.getByTestId(`user-kontonummer-${chef.id}`)).toHaveText(`Kontonummer ${chefNr}`);

    // 2. Sucher der Firma OHNE E-Mail — Nummer '<Chef-Nummer>-<Zusatz>'
    await page.goto(`/admin/users/${chef.id}`);
    await page.getByTestId("admin-add-sucher").click();
    await page.getByTestId("sucher-anlegen-vorname").fill("Erika");
    await page.getByTestId("sucher-anlegen-nachname").fill(`Sucher${s}`);
    await expect(page.getByTestId("sucher-anlegen-email")).toHaveValue("");
    await page.getByTestId("sucher-anlegen-passwort").fill(h.PASSWORD);
    await page.getByTestId("sucher-anlegen-submit").click();
    const sucherNr = await kontonummerAusKarte(page);
    expect(sucherNr).toMatch(new RegExp(`^${chefNr}-\\d+$`));
    await page.getByTestId("zugangsdaten-fertig").click();
    const sucher = (await h.superGet("/admin/users")).find((u) => u.kontonummer === sucherNr);
    expect(sucher?.role).toBe("sucher");
    expect(sucher.email || "").toBe("");
    // Firmenansicht: Zeile per Kontonummer, keine E-Mail dahinter
    await expect(page.getByTestId(`sucher-kontonummer-${sucher.id}`)).toHaveText(sucherNr);
    await expect(page.locator("tr", { has: page.getByTestId(`sucher-kontonummer-${sucher.id}`) }))
      .not.toContainText("@");
    // Nutzerliste: Suche per Kontonummer, Zusatz nur mit dem Namen
    await page.goto("/admin/users");
    await page.getByTestId("admin-users-search").fill(sucherNr);
    await expect(page.getByTestId(`user-kontonummer-${sucher.id}`)).toHaveText(`Kontonummer ${sucherNr}`);
    await expect(page.getByTestId(`user-row-${sucher.id}`)).toContainText(`Erika Sucher${s}`);
    await expect(page.getByTestId(`user-row-${sucher.id}`)).not.toContainText("@");
    // Profil: Kontonummer, Kontakt-E-Mail leer ('—')
    await page.goto(`/admin/users/${sucher.id}`);
    await expect(page.getByTestId("profil-kontonummer")).toHaveText(sucherNr);
    await expect(page.getByText("Kontakt-E-Mail", { exact: true }).locator("xpath=.."))
      .toHaveText(/Kontakt-E-Mail\s*—$/);

    // 3. Zwischenhaendler (ohne B2B-Haken abgelehnt, mit Haken angelegt)
    await page.goto("/admin/freischaltungen");
    await page.getByTestId("kaeufer-anlegen-btn").click();
    await page.getByTestId("kaeufer-anlegen-firma").fill(`E2E Zwischenhandel ${s}`);
    await page.getByTestId("kaeufer-anlegen-name").fill("Kai Kaeufer");
    await page.getByTestId("kaeufer-anlegen-email").fill(mails.kaeufer);
    await page.getByTestId("kaeufer-anlegen-passwort").fill(h.PASSWORD);
    await page.getByTestId("kaeufer-anlegen-submit").click();
    await expect(page.getByText("Bitte bestätigen, dass der B2B-Nachweis vorliegt")).toBeVisible();
    await page.getByTestId("kaeufer-anlegen-b2b").check();
    await page.getByTestId("kaeufer-anlegen-submit").click();
    const kaeuferNr = await kontonummerAusKarte(page);
    // Kaeufer-Code statt Nummer aus der Reihe (14.09.2026): Buchstaben + Ziffern
    expect(kaeuferNr).toMatch(/^(?=.*[A-Z])[A-HJ-NP-Z2-9]{6,9}$/);
    await page.getByTestId("zugangsdaten-fertig").click();
    const kaeufer = (await h.superGet("/admin/users")).find((u) => u.email === mails.kaeufer);
    await expect(page.getByTestId(`buyer-kontonummer-${kaeufer.id}`)).toHaveText(kaeuferNr);

    // 4. Fahrer OHNE E-Mail — Kontonummer und FD-Code
    await page.goto("/admin/fahrer");
    await page.getByTestId("fahrer-anlegen-btn").click();
    await page.getByTestId("fahrer-anlegen-name").fill(fahrerName);
    await expect(page.getByTestId("fahrer-anlegen-email")).toHaveValue("");
    await page.getByTestId("fahrer-anlegen-passwort").fill(h.PASSWORD);
    await page.getByTestId("fahrer-anlegen-submit").click();
    const fahrerNr = await kontonummerAusKarte(page);
    // Fahrer-ID = Kontonummer (14.09.2026)
    expect(fahrerNr).toMatch(/^FD-[A-HJ-NP-Z2-9]{8}$/);
    await expect(page.getByTestId("zugangsdaten-fahrer-code")).toHaveText(/^FD-/);
    await page.getByTestId("zugangsdaten-fertig").click();
    const fahrer = (await h.superGet("/admin/drivers")).find((d) => d.kontonummer === fahrerNr);
    expect(fahrer?.display_name).toBe(fahrerName);
    expect(fahrer.email || "").toBe("");
    // Fahrerliste: Suche per Kontonummer, Zeile ohne E-Mail
    await page.getByTestId("fahrer-suche").fill(fahrerNr);
    await expect(page.getByTestId(`fahrer-kontonummer-${fahrer.id}`)).toHaveText(fahrerNr);
    await expect(page.getByTestId(`fahrer-row-${fahrer.id}`)).not.toContainText("@");

    // Alle Nummern verschieden (gemeinsame Reihe fuer Firmen, Kaeufer, Fahrer)
    expect(new Set([chefNr, kaeuferNr, fahrerNr]).size).toBe(3);

    // Anmeldung jeweils in einem eigenen Browser-Kontext (eigener Speicher)
    const anmelden = async (bereich, kontonummer, ziel) => {
      const context = await browser.newContext();
      try {
        const p = await context.newPage();
        await h.formLogin(p, bereich, { kontonummer, password: h.PASSWORD });
        await expect(p).toHaveURL(ziel);
      } finally {
        await context.close();
      }
    };
    await anmelden("auth", chefNr, /\/app\/bestand/);
    await anmelden("auth", sucherNr, /\/(app|abo)(\/|$)/);    // ohne Abo: Abo-Hinweis
    await anmelden("buyer", kaeuferNr, /\/markt\/?$/);
    await anmelden("driver", fahrerNr, /\/fahrer\/?$/);
  });
});
