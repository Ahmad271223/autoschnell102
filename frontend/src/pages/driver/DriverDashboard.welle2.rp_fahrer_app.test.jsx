/**
 * Rollenprüfung 22.09.2026, Welle 2 — Startseite der Fahrer-App (DriverDashboard.jsx).
 *  RP-062/RP-161  Hinweis "Fahrt wurde geändert" nennt auch Fahrzeug und Verkäufer;
 *                 /fahrer?fahrt=<id> (aus dem Protokoll) klappt genau diese Fahrt auf.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }));

vi.mock("@/context/DriverContext", () => ({ driverApi: api, openDriverPdf: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("react-router-dom", async () => {
  const { createElement: h } = await import("react");
  return { Link: ({ children, to, ...rest }) => h("a", { href: String(to), ...rest }, children),
           useNavigate: () => vi.fn() };
});
vi.mock("@/components/PhotoGallery", () => ({ default: () => null }));
vi.mock("@/components/AbholCheckDialog", () => ({ default: () => null }));

const { default: DriverDashboard } = await import("./DriverDashboard");

let wurzel;
let behaelter;

async function warten() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);

function fahrt(id, extra = {}) {
  return { id, title: `Fahrt ${id}`, pickup_date: "2026-09-25", pickup_time: "10:00",
           status: "offen", zuteilung: "angenommen", updated_at: "u1",
           vehicle: { make: "VW", model: "Golf" }, dealer: { name: "Firma" }, ...extra };
}

async function starten(liste) {
  api.get.mockResolvedValue({ data: liste });
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(DriverDashboard)); });
  await warten();
}

beforeEach(() => {
  vi.clearAllMocks();
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
});

afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null;
  behaelter.remove();
  window.history.replaceState({}, "", "/");
});

describe("RP-062: geänderte Fahrt erneut annehmen", () => {
  it("Ziel-Fahrt aus ?fahrt= ist aufgeklappt, Hinweis nennt Verkäufer", async () => {
    window.history.replaceState({}, "", "/fahrer?fahrt=f2");
    await starten([fahrt("f1"),
                   fahrt("f2", { zuteilung: "offen", zuteilung_neu_wegen_aenderung: true })]);
    const block = el("zuteilung-f2");
    expect(block).not.toBeNull();
    expect(block.textContent).toContain("Verkäufer");
    expect(block.textContent).toContain("Fahrzeug");
    expect(el("zuteilung-annehmen-f2")).not.toBeNull();
    expect(el("mark-pickedup-f1")).toBeNull();          // andere Fahrten bleiben zu
  });

  it("ohne ?fahrt= ist nichts aufgeklappt", async () => {
    await starten([fahrt("f2", { zuteilung: "offen", zuteilung_neu_wegen_aenderung: true })]);
    expect(el("zuteilung-f2")).toBeNull();
  });
});
