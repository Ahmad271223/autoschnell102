/** Farbwahl (Wunsch Ahmad 02.10.2026): elf Kacheln, Klick = Vorschau, Speichern = Konto + Browser. */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock("sonner", () => ({ toast }));
vi.mock("@/lib/api", () => ({ errMsg: (e, f) => e?.message || f }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { default: AkzentWahl } = await import("./AkzentWahl");
const { AKZENT_KEY } = await import("@/lib/akzent");

let wurzel = null;
let behaelter = null;
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
async function rendern(props) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(AkzentWahl, props)); });
}
async function klick(id) { await act(async () => { el(id).click(); await Promise.resolve(); }); }

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("style");
  document.documentElement.setAttribute("data-theme", "dark");
  vi.clearAllMocks();
});
afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null; behaelter?.remove();
});

describe("AkzentWahl", () => {
  it("zeigt elf Kacheln, Klick ist Vorschau, Speichern legt die Farbe am Konto und im Browser ab", async () => {
    const speichern = vi.fn(async () => {});
    await rendern({ aktuell: "standard", speichern });
    expect(behaelter.querySelectorAll('[data-testid^="akzent-"][aria-pressed]').length).toBe(11);
    expect(el("akzent-standard").getAttribute("aria-pressed")).toBe("true");
    expect(el("akzent-speichern").disabled).toBe(true);
    await klick("akzent-lila");
    expect(document.documentElement.style.getPropertyValue("--accent-red")).toBe("#a855f7");
    expect(el("akzent-stand").textContent).toContain("Vorschau: Lila");
    expect(el("akzent-speichern").disabled).toBe(false);
    expect(speichern).not.toHaveBeenCalled();
    await klick("akzent-speichern");
    expect(speichern).toHaveBeenCalledWith("lila");
    expect(window.localStorage.getItem(AKZENT_KEY)).toBe("lila");
    expect(toast.success).toHaveBeenCalled();
  });

  it("Verwerfen geht auf die gespeicherte Farbe zurück; Serverfehler wird gemeldet", async () => {
    await rendern({ aktuell: "blau", speichern: vi.fn(async () => { throw new Error("Server weg"); }) });
    expect(el("akzent-blau").getAttribute("aria-pressed")).toBe("true");
    await klick("akzent-gold");
    expect(document.documentElement.style.getPropertyValue("--accent-red")).toBe("#eab308");
    await klick("akzent-verwerfen");
    expect(document.documentElement.style.getPropertyValue("--accent-red")).toBe("#0a84ff");
    expect(el("akzent-verwerfen")).toBeNull();
    await klick("akzent-orange");
    await klick("akzent-speichern");
    expect(toast.error).toHaveBeenCalled();
    expect(window.localStorage.getItem(AKZENT_KEY)).toBeNull();
  });
});
