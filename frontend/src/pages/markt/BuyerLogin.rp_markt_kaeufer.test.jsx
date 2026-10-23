/*
 * Rollenprüfung 22.09.2026 — Käufer-Anmeldung (Team markt_kaeufer).
 *
 * RP-531  nach einer beendeten Sitzung steht der Grund auf der Anmeldeseite
 * RP-511  "Zugang anfragen" nimmt die Einladung mit, und sie wird gemerkt;
 *         nach der Anmeldung wird eine gemerkte Einladung eingelöst
 * RP-563  Impressum/Datenschutz/AGB verlinkt
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { BuyerAuthProvider, buyerApi } = await import("@/context/BuyerContext");
const { default: BuyerLogin } = await import("./BuyerLogin");
const { ABMELDEGRUND_KAEUFER, einladungMerken, gemerkteEinladung } = await import("./marktHilfen");

let root;
let host;

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  vi.restoreAllMocks();
});

async function rendern(pfad) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(h(MemoryRouter, { initialEntries: [pfad] }, h(BuyerAuthProvider, null, h(BuyerLogin))));
  });
  return host;
}

describe("BuyerLogin (Rollenprüfung 22.09.2026)", () => {
  it("RP-531: zeigt den gemerkten Abmeldegrund einmal an", async () => {
    window.sessionStorage.setItem(ABMELDEGRUND_KAEUFER, "Sitzung beendet: dein Konto wurde erneut angemeldet am 22.09.2026 10:00 Uhr.");
    const el = await rendern("/markt/login?reason=session");
    expect(el.querySelector('[data-testid="buyer-abmeldegrund"]').textContent)
      .toMatch(/erneut angemeldet am 22\.09\.2026/);
    expect(window.sessionStorage.getItem(ABMELDEGRUND_KAEUFER)).toBeNull();
  });

  it("RP-511/RP-563: Einladung wird gemerkt und an 'Zugang anfragen' angehängt; Rechtslinks da", async () => {
    const el = await rendern("/markt/login?invite=tokXYZ");
    const link = el.querySelector('[data-testid="buyer-link-anfrage"]');
    expect(link.getAttribute("href")).toBe("/anfrage?art=kaeufer&invite=tokXYZ");
    expect(gemerkteEinladung()).toBe("tokXYZ");
    expect(el.querySelector('[data-testid="rechts-links"]')).toBeTruthy();
  });

  it("RP-511: nach der Anmeldung wird eine gemerkte Einladung eingelöst und vergessen", async () => {
    einladungMerken("gemerkt123");
    vi.spyOn(buyerApi, "get").mockResolvedValue({ data: { id: "k1", role: "b2b_buyer" } });
    const post = vi.spyOn(buyerApi, "post").mockImplementation(async (url) => {
      if (url === "/buyer/login") return { data: { token: "t", user: { id: "k1", role: "b2b_buyer" } } };
      return { data: { ok: true, dealer: "Autohaus Test" } };
    });
    const el = await rendern("/markt/login");
    const setze = (testid, wert) => {
      const feld = el.querySelector(`[data-testid="${testid}"]`);
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(feld, wert);
      feld.dispatchEvent(new Event("input", { bubbles: true }));
    };
    await act(async () => { setze("buyer-login-kontonummer", "6FE7K2M"); setze("buyer-login-password", "geheim"); });
    await act(async () => {
      el.querySelector('[data-testid="buyer-login-submit"]').click();
    });
    for (let i = 0; i < 5; i += 1) await act(async () => { await Promise.resolve(); });
    expect(post).toHaveBeenCalledWith("/invites/gemerkt123/redeem");
    expect(gemerkteEinladung()).toBe("");
  });
});
