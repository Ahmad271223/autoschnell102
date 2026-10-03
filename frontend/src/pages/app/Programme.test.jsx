/**
 * Programme zum Herunterladen (03.10.2026, Wunsch Ahmad): erst einmal nur Kunde 10002 —
 * "alle anderen sollen das gar nicht sehen". Die Seite zeigt nur, was der Server für
 * DIESE Firma liefert; ohne Freischaltung geht es still zur Startseite, und im App-Code
 * steht kein Programmname.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const post = vi.fn();
const del = vi.fn();
const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), message: vi.fn() };
vi.mock("sonner", () => ({ toast }));
vi.mock("@/lib/api", () => ({
  api: { get: (...a) => get(...a), post: (...a) => post(...a), put: vi.fn(), delete: (...a) => del(...a) },
  errMsg: (e, f) => e?.response?.data?.detail || e?.message || f,
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "u-10002-1", role: "sucher" } }) }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { default: Programme } = await import("./Programme");
const { programmeVergessen, groesseText } = await import("@/lib/programme");

const PROGRAMM = {
  id: "werkzeug-x", name: "Testprogramm", beschreibung: "Macht etwas Nützliches.",
  schritte: ["Herunterladen", "Starten"], dateiname: "Test.exe",
  vorhanden: true, version: "1.0.0", groesse: 57671680,
};

let wurzel = null;
let behaelter = null;
async function rendern() {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(createElement(MemoryRouter, { initialEntries: ["/app/programme"] },
      createElement(Routes, null,
        createElement(Route, { path: "/app/programme", element: createElement(Programme) }),
        createElement(Route, { path: "/app/vergleich", element: createElement("div", { "data-testid": "startseite" }) }))));
    await Promise.resolve();
    await Promise.resolve();
  });
  return behaelter;
}
const feld = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
const klick = async (el) => {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await Promise.resolve();
    await Promise.resolve();
  });
};

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  del.mockReset();
  toast.success.mockReset();
  toast.error.mockReset();
  programmeVergessen();
});
afterEach(() => {
  if (wurzel) act(() => wurzel.unmount());
  behaelter?.remove();
  wurzel = null;
  behaelter = null;
});

describe("Programme", () => {
  it("ohne Freischaltung: keine Seite, still zur Startseite", async () => {
    get.mockResolvedValue({ data: { werkzeuge: [] } });
    await rendern();
    expect(get).toHaveBeenCalledWith("/werkzeuge");
    expect(feld("programme-seite")).toBeNull();
    expect(feld("startseite")).not.toBeNull();
  });

  it("freigeschaltet: Name, Beschreibung, Schritte und Download vom Server", async () => {
    get.mockResolvedValue({ data: { werkzeuge: [PROGRAMM] } });
    await rendern();
    const seite = feld("programme-seite");
    expect(seite.textContent).toContain("Testprogramm");
    expect(seite.textContent).toContain("Macht etwas Nützliches.");
    expect(seite.querySelectorAll("ol li")).toHaveLength(2);
    expect(feld("programm-info-werkzeug-x").textContent).toBe("Version 1.0.0 · 55,0 MB");

    get.mockReset();
    get.mockResolvedValue({ data: new Blob(["MZ"]) });
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    const klicks = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await act(async () => {
      feld("programm-download-werkzeug-x").dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(get).toHaveBeenCalledWith("/werkzeuge/werkzeug-x/download", { responseType: "blob" });
    expect(klicks).toHaveBeenCalledTimes(1);
    klicks.mockRestore();
  });

  it("noch nicht hochgeladen: Knopf gesperrt, Hinweis", async () => {
    get.mockResolvedValue({ data: { werkzeuge: [{ ...PROGRAMM, vorhanden: false, version: null, groesse: null }] } });
    await rendern();
    expect(feld("programm-download-werkzeug-x").disabled).toBe(true);
    expect(feld("programm-info-werkzeug-x").textContent).toContain("Wird gerade bereitgestellt");
  });

  it("Fehler beim Laden der Liste: nichts anzeigen", async () => {
    get.mockRejectedValue(new Error("Netz weg"));
    await rendern();
    expect(feld("programme-seite")).toBeNull();
  });

  it("Code zum Verbinden: 6 Ziffern, 10 Minuten", async () => {
    get.mockResolvedValue({ data: { werkzeuge: [{ ...PROGRAMM, verbindung: null }] } });
    post.mockResolvedValue({ data: { code: "482913", gueltig_bis: "2026-10-03T13:40:00+00:00", minuten: 10 } });
    await rendern();
    expect(feld("programm-pc-werkzeug-x").textContent).toContain("Noch kein PC verbunden");
    await klick(feld("programm-code-werkzeug-x"));
    expect(post).toHaveBeenCalledWith("/werkzeuge/werkzeug-x/code");
    const anzeige = feld("programm-code-anzeige-werkzeug-x").textContent;
    expect(anzeige).toContain("482 913");
    expect(anzeige).toContain("10 Minuten");
  });

  it("ohne Abo: kein Code, klare Meldung", async () => {
    get.mockResolvedValue({ data: { werkzeuge: [PROGRAMM] } });
    post.mockRejectedValue({ response: { status: 402, data: { detail: "Kein aktives Abo" } } });
    await rendern();
    await klick(feld("programm-code-werkzeug-x"));
    expect(feld("programm-code-anzeige-werkzeug-x")).toBeNull();
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("Kein aktives Abo"));
  });

  it("verbundener PC wird angezeigt und lässt sich trennen", async () => {
    get.mockResolvedValue({ data: { werkzeuge: [{ ...PROGRAMM, verbindung: {
      pc_name: "BUERO-PC", verbunden_am: "2026-10-03T10:00:00+00:00", zuletzt_am: "2026-10-03T11:00:00+00:00" } }] } });
    del.mockResolvedValue({ data: { ok: true, getrennt: true } });
    await rendern();
    expect(feld("programm-pc-werkzeug-x").textContent).toContain("BUERO-PC");
    await klick(feld("programm-trennen-werkzeug-x"));
    expect(del).toHaveBeenCalledWith("/werkzeuge/werkzeug-x/verbindung");
    expect(feld("programm-pc-werkzeug-x").textContent).toContain("Noch kein PC verbunden");
  });

  it("Chef sieht, wer aus der Firma wann welches Auto verglichen hat", async () => {
    get.mockImplementation((url) => Promise.resolve(url === "/werkzeuge"
      ? { data: { werkzeuge: [{ ...PROGRAMM, chef: true }] } }
      : url.endsWith("/meine") ? { data: { vergleiche: [] } }
      : { data: { gesamt: 1,
          verbindungen: [{ id: "v1", user_id: "s1", name: "Max Sucher", konto: "10050-1", pc_name: "PC-1",
                           verbunden_am: "2026-10-03T10:00:00+00:00", zuletzt_am: "2026-10-03T11:00:00+00:00" }],
          vergleiche: [{ id: "c1", user_id: "s1", name: "Max Sucher", konto: "10050-1", pc_name: "PC-1",
                         erstellt_am: "2026-10-03T11:00:00+00:00",
                         fahrzeug: { marke: "VW", modell: "Polo", ez_monat: 10, ez_jahr: 2005, kilometer: 128000, ps: 75,
                                     preis: 2599, quelle: "Kleinanzeigen", inserat_id: "3529833344",
                                     inserat_url: "https://www.kleinanzeigen.de/s-anzeige/3529833344" },
                         links: [{ portal: "mobile.de", url: "https://suchen.mobile.de/x" }] }] } }));
    del.mockResolvedValue({ data: { ok: true } });
    await rendern();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(get).toHaveBeenCalledWith("/werkzeuge/werkzeug-x/firma", { params: { limit: 200 } });
    const tabelle = feld("pv-vergleiche").textContent;
    expect(tabelle).toContain("Max Sucher");
    expect(tabelle).toContain("VW Polo · EZ 10/2005 · 128.000 km · 75 PS · 2.599 €");
    expect(tabelle).toContain("3529833344");
    expect(feld("pv-inserat-c1").getAttribute("href")).toBe("https://www.kleinanzeigen.de/s-anzeige/3529833344");
    expect(feld("pv-pcs").textContent).toContain("PC „PC-1“");
    await klick(feld("pv-trennen-s1"));
    expect(del).toHaveBeenCalledWith("/werkzeuge/werkzeug-x/verbindungen/s1");
  });

  it("Sucher sieht keine Firmenübersicht", async () => {
    get.mockResolvedValue({ data: { werkzeuge: [{ ...PROGRAMM, chef: false }] } });
    await rendern();
    expect(feld("programm-firma")).toBeNull();
    expect(get.mock.calls.map((c) => c[0])).not.toContain("/werkzeuge/werkzeug-x/firma");
  });

  it("Deine letzten Autos: mit Inserat-Adresse direkt zum Kaufvertrag, ohne Adresse ein Hinweis", async () => {
    get.mockImplementation((url) => Promise.resolve(url === "/werkzeuge"
      ? { data: { werkzeuge: [PROGRAMM] } }
      : { data: { vergleiche: [
          { id: "m1", erstellt_am: "2026-10-03T13:00:00+00:00",
            fahrzeug: { marke: "Opel", modell: "Mokka X", ez_jahr: 2018, kilometer: 14500,
                        inserat_url: "https://www.autoscout24.de/angebote/ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d" } },
          { id: "m2", erstellt_am: "2026-10-03T12:00:00+00:00",
            fahrzeug: { marke: "Audi", modell: "A5", ez_jahr: 2026, kilometer: 29219, inserat_url: null } },
        ] } }));
    await rendern();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(get).toHaveBeenCalledWith("/werkzeuge/werkzeug-x/meine", { params: { limit: 15 } });
    const link = feld("meine-vertrag-m1");
    expect(link.getAttribute("href")).toBe(
      `/app/vergleich?url=${encodeURIComponent("https://www.autoscout24.de/angebote/ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d")}`);
    expect(link.textContent).toContain("Für Kaufvertrag öffnen");
    expect(feld("meine-vertrag-m2")).toBeNull();
    expect(feld("meine-ohne-link-m2").textContent).toContain("selbst kopieren");
  });

  it("Größe lesbar", () => {
    expect(groesseText(57671680)).toBe("55,0 MB");
    expect(groesseText(2048)).toBe("2 KB");
    expect(groesseText(null)).toBe("");
  });

  it("im App-Code steht kein Programmname (andere Kunden sollen nichts sehen)", async () => {
    const quellen = await Promise.all([
      import("./Programme.jsx?raw"), import("@/lib/programme.js?raw"),
      import("@/components/AppLayout.jsx?raw"), import("@/App.jsx?raw"),
      import("@/components/ProgrammVergleiche.jsx?raw"), import("@/pages/admin_v2/ProgrammVergleiche.jsx?raw"),
      import("@/pages/admin_v2/AdminLayout.jsx?raw"), import("./Vergleich.jsx?raw"),
    ]);
    for (const { default: text } of quellen) {
      expect(text).not.toMatch(/autopointer/i);
      expect(text).not.toContain("10002");
      expect(text).not.toContain("10001");
    }
  });
});
