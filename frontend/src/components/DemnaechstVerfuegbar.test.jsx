import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import DemnaechstVerfuegbar from "./DemnaechstVerfuegbar";

let root;
let host;
afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
});

async function rendern(el) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(h(MemoryRouter, null, el)); });
  return host;
}

describe("DemnaechstVerfuegbar (Go-Live-Schalter)", () => {
  it("nennt den Bereich und bietet einen Rueckweg", async () => {
    const el = await rendern(h(DemnaechstVerfuegbar, { bereich: "Der B2B-Marktplatz", zurueck: "/app" }));
    expect(el.querySelector('[data-testid="demnaechst-verfuegbar"]')).toBeTruthy();
    expect(el.textContent).toMatch(/Demnächst verfügbar/);
    expect(el.textContent).toMatch(/Der B2B-Marktplatz wird gerade fertiggestellt/);
    expect(el.querySelector("a").getAttribute("href")).toBe("/app");
  });
  it("eingebettet ohne Rueckweg", async () => {
    const el = await rendern(h(DemnaechstVerfuegbar, { bereich: "Das Inserieren", eingebettet: true }));
    expect(el.querySelector("a")).toBeNull();
    expect(el.textContent).toMatch(/Das Inserieren wird gerade fertiggestellt/);
  });
});
