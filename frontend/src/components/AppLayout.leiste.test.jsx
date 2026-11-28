/*
 * Wunsch Ahmad 03.10.2026: Seitenleiste übersichtlicher — Bereiche nach Zweck gruppiert, am PC mit
 * Namen (einklappbar), am Handy ein Menü mit Namen; Fahrer mit eigenem Symbol.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const auth = vi.hoisted(() => ({ wert: null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), message: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => auth.wert }));
vi.mock("@/lib/features", () => ({ useFeatures: () => ({ marktplatz: true }) }));
vi.mock("@/lib/programme", () => ({ useProgramme: () => [] }));
vi.mock("@/lib/freigaben", () => ({
  useFreigabeZaehler: () => ({ geladen: true, wartet: 2, ids: ["p1", "p2"] }),
  neueWartende: () => [], neuHinzugekommen: () => 0, titelMitZahl: (t) => t,
}));
vi.mock("@/lib/anfragenZaehler", () => ({ useAnfragenZaehler: () => 0 }));
vi.mock("@/lib/abgelehntZaehler", () => ({ useAbgelehntZaehler: () => 0 }));
vi.mock("@/lib/meldungen", () => ({ useMeldungenZaehler: () => ({ geladen: false }), neueMeldungen: () => [] }));
vi.mock("@/components/InstallPWAButton", () => ({ default: () => null }));
vi.mock("@/components/ThemeToggle", () => ({ default: () => null }));
vi.mock("@/components/RechtsLinks", () => ({ default: () => null }));

const { default: AppLayout, gruppiert, LEISTE_KEY, NAV } = await import("./AppLayout");

let wurzel;
let behaelter;
const el = (id) => document.querySelector(`[data-testid="${id}"]`);
const alle = (id) => document.querySelectorAll(`[data-testid="${id}"]`);

async function zeigen(rolle = "dealer") {
  auth.wert = { user: { id: "u1", role: rolle }, dealer: { company_name: "KFZ Müller GmbH" },
                subscription: { active: true }, logout: vi.fn() };
  await act(async () => {
    wurzel.render(createElement(MemoryRouter, { initialEntries: ["/app/vertraege"] },
      createElement(AppLayout, null, createElement("div", null, "Inhalt"))));
  });
}

beforeEach(() => {
  window.localStorage.clear();
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
});

afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
});

describe("Seitenleiste", () => {
  it("gruppiert die Bereiche nach Zweck, mit Namen und Firma", async () => {
    await zeigen();
    const gruppen = [...document.querySelectorAll('[data-testid="app-sidebar"] [role="group"]')]
      .map((g) => g.getAttribute("aria-label"));
    expect(gruppen).toEqual(["Einkauf", "Abwicklung", "Verkauf", "Team", "Allgemein"]);
    expect(el("nav-vertraege").textContent).toContain("Verträge / PDFs");
    expect(el("nav-vertraege").getAttribute("aria-current")).toBe("page");
    expect(el("leiste-firma").textContent).toBe("KFZ Müller GmbH");
    // Zähler genau einmal (keine doppelte Kennung), Zahl im Namen für Screenreader
    expect(alle("nav-freigaben-zaehler")).toHaveLength(1);
    expect(el("nav-freigaben").getAttribute("aria-label")).toBe("Freigaben — 2 warten");
    // Fahrer und Mitarbeiter haben verschiedene Symbole
    const fahrer = NAV.find((n) => n.to === "/app/fahrer");
    const team = NAV.find((n) => n.to === "/app/team");
    expect(fahrer.icon).not.toBe(team.icon);
  });

  it("Sucher: Gruppen ohne eigene Bereiche fallen weg", async () => {
    await zeigen("sucher");
    const gruppen = [...document.querySelectorAll('[data-testid="app-sidebar"] [role="group"]')]
      .map((g) => g.getAttribute("aria-label"));
    expect(gruppen).toEqual(["Einkauf", "Abwicklung", "Team", "Allgemein"]);
    expect(el("nav-freigaben")).toBeNull();
    expect(el("nav-bestand")).toBeNull();
  });

  it("am PC ein- und ausklappbar, im Browser gemerkt", async () => {
    await zeigen();
    expect(el("app-sidebar").dataset.breit).toBe("ja");
    await act(async () => { el("leiste-umschalten").click(); });
    expect(el("app-sidebar").dataset.breit).toBe("nein");
    expect(window.localStorage.getItem(LEISTE_KEY)).toBe("1");
    await act(async () => { wurzel.unmount(); });
    wurzel = createRoot(behaelter);
    await zeigen();
    expect(el("app-sidebar").dataset.breit).toBe("nein");
    await act(async () => { el("leiste-umschalten").click(); });
    expect(window.localStorage.getItem(LEISTE_KEY)).toBe("0");
  });

  it("Handy: Menü mit Namen öffnet und schließt", async () => {
    await zeigen();
    expect(el("leiste-menue")).toBeNull();
    await act(async () => { el("leiste-menue-oeffnen").click(); });
    expect(el("leiste-menue")).not.toBeNull();
    expect(el("menue-vergleich").textContent).toContain("Vergleich");
    expect(el("menue-fahrer").textContent).toContain("Fahrer");
    await act(async () => { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); });
    expect(el("leiste-menue")).toBeNull();
    await act(async () => { el("leiste-menue-oeffnen").click(); });
    await act(async () => { el("leiste-menue-schliessen").click(); });
    expect(el("leiste-menue")).toBeNull();
  });

  it("gruppiert(): Einträge ohne Gruppe (Admin) ohne Überschrift", () => {
    const g = gruppiert([{ to: "/admin", label: "Admin" }]);
    expect(g).toHaveLength(1);
    expect(g[0].titel).toBe("");
  });
});
