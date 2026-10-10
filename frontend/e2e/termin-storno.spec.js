// Chef setzt eine abgeholte Abholung nachtraeglich auf "storniert" — nur nach
// Rueckfrage (Pruefbericht 20.09.2026, V-12/T-25; Entscheidung Ahmad
// 21.09.2026). Ohne Bestaetigung bleibt der alte Status; mit Bestaetigung
// schickt die Oberflaeche ausgang_bestaetigt, der Server haelt die Aenderung
// mit Name und Uhrzeit fest (ausgang_geaendert).
const { test, expect } = require("@playwright/test");
const h = require("./helpers");

test.describe("Terminplaner: abgeholt -> storniert nur mit Rueckfrage", () => {
  let firma, appt;

  test.beforeAll(async () => {
    firma = await h.createFirma();
    const v = await h.post("/vehicles/manual", {
      make_label: "Skoda", model_label: "Octavia", first_registration: "02/2019",
      mileage: 98000, color: "Blau", fuel_label: "Diesel",
    }, { token: firma.token });
    appt = await h.createAppointment(firma, {
      title: `E2E Storno ${firma.s}`, pickup_date: h.isoDate(-1), pickup_time: "09:00",
      status: "abgeholt", vehicle_id: v.id || v.vehicle_id, seller_name: "Max Verkaeufer",
    });
    expect(appt.status).toBe("abgeholt");
  });

  test.afterAll(async () => {
    await h.cleanup({ firmen: [firma] });
  });

  test("Abbrechen laesst 'abgeholt' stehen, Bestaetigen storniert mit Verlauf", async ({ page }) => {
    const rueckfragen = [];
    let antwort = false;                                   // erste Rueckfrage: Abbrechen
    page.on("dialog", (d) => { rueckfragen.push(d.message()); antwort ? d.accept() : d.dismiss(); });
    await h.authPage(page, "app", firma.token);
    await page.goto("/app/termine");
    await page.getByTestId("view-list").click();
    await page.getByTestId(`appt-row-${appt.id}`).click();
    await expect(page.getByTestId("edit-appt-dialog")).toBeVisible();

    // 1) storniert waehlen, speichern, Rueckfrage ABBRECHEN -> nichts geaendert
    await page.getByTestId("status-storniert").click();
    await page.getByTestId("save-appt-btn").click();
    await expect.poll(() => rueckfragen.length).toBe(1);
    expect(rueckfragen[0]).toContain("Diese Abholung ist bereits abgeschlossen.");
    expect(rueckfragen[0]).toContain("mit deinem Namen und der Uhrzeit im Verlauf festgehalten");
    await expect(page.getByTestId("edit-appt-dialog")).toBeVisible();    // Dialog bleibt offen
    const unveraendert = await h.get(`/appointments/${appt.id}`, { token: firma.token });
    expect(unveraendert.status).toBe("abgeholt");
    expect(unveraendert.ausgang_geaendert).toBeFalsy();

    // 2) noch einmal, diesmal BESTAETIGEN -> storniert, Verlauf festgehalten
    antwort = true;
    await page.getByTestId("status-storniert").click();
    await page.getByTestId("save-appt-btn").click();
    await expect.poll(() => rueckfragen.length).toBe(2);
    await expect(page.getByTestId("edit-appt-dialog")).toHaveCount(0);
    await expect.poll(async () => (await h.get(`/appointments/${appt.id}`, { token: firma.token })).status)
      .toBe("storniert");
    const nachher = await h.get(`/appointments/${appt.id}`, { token: firma.token });
    // Merker der Chef-Uebersteuerung (routes/appointments.py: am, von, von_status,
    // nach_status, protokoll_id) — im selben Write wie der Status.
    expect(nachher.ausgang_geaendert).toBeTruthy();
    expect(nachher.ausgang_geaendert.von_status).toBe("abgeholt");
    expect(nachher.ausgang_geaendert.nach_status).toBe("storniert");
    expect(nachher.ausgang_geaendert.am).toBeTruthy();
    expect(nachher.ausgang_geaendert.protokoll_id ?? null).toBeNull();   // hier ohne Protokoll
    // Der Hinweis im Dialog nennt die Aenderung
    await page.getByTestId(`appt-row-${appt.id}`).click();
    await expect(page.getByTestId("ausgang-geaendert-hinweis")).toBeVisible();

    // 3) Ohne Bestaetigung lehnt der SERVER ab (409) — nicht nur die Oberflaeche
    const r = await h.api("PUT", `/appointments/${appt.id}`,
      { token: firma.token, body: { status: "abgeholt" }, ok: false });
    // Rueckweg zu "abgeholt" nach einer Ausgangs-Aenderung: ebenfalls belegpflichtig
    expect([200, 409]).toContain(r.status);
    if (r.status === 200) {
      const zurueck = await h.api("PUT", `/appointments/${appt.id}`,
        { token: firma.token, body: { status: "storniert" }, ok: false });
      expect(zurueck.status).toBe(409);
      expect(String(zurueck.data?.detail || "")).toContain("Rückfrage");
    } else {
      expect(String(r.data?.detail || "")).toMatch(/Rückfrage|bestätig/i);
    }
  });
});
