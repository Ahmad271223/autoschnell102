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
const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), message: vi.fn() };
vi.mock("sonner", () => ({ toast }));
vi.mock("@/lib/api", () => ({
  api: { get: (...a) => get(...a), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
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

beforeEach(() => {
  get.mockReset();
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

  it("Größe lesbar", () => {
    expect(groesseText(57671680)).toBe("55,0 MB");
    expect(groesseText(2048)).toBe("2 KB");
    expect(groesseText(null)).toBe("");
  });

  it("im App-Code steht kein Programmname (andere Kunden sollen nichts sehen)", async () => {
    const quellen = await Promise.all([
      import("./Programme.jsx?raw"), import("@/lib/programme.js?raw"),
      import("@/components/AppLayout.jsx?raw"), import("@/App.jsx?raw"),
    ]);
    for (const { default: text } of quellen) {
      expect(text).not.toMatch(/autopointer/i);
      expect(text).not.toContain("10002");
    }
  });
});
