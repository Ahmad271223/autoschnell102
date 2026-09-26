/*
 * Prüfbericht 20.09.2026 (K-09): "Aktualisieren" geht ueber neueFassungLaden
 * (setzt den Merker); traegt der Merker schon diese Fassung, sagt das Band
 * "wird gerade verteilt" und der Knopf laedt schlicht neu.
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import FassungsHinweis from "./FassungsHinweis";
import { _zuruecksetzen, fassungPruefen } from "@/lib/fassung";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const ALT = "1757600000-aaaaaaa";
const NEU = "1757680000-bbbbbbb";
const antwort = (wert) => ({ headers: { "x-ah-fassung": wert } });

let root;
let host;
let echt;

beforeEach(() => {
  _zuruecksetzen();
  window.sessionStorage.clear();
  echt = window.location;
  delete window.location;
  window.location = { assign: vi.fn(), reload: vi.fn() };
});
afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  window.location = echt;
});

async function rendern(pfad = "/app/termine?tag=heute") {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(h(MemoryRouter, { initialEntries: [pfad] }, h(FassungsHinweis)));
  });
  return host;
}

describe("K-09: Aktualisieren setzt den Merker", () => {
  it("erster Klick: Zielseite (mit Query) frisch laden, Merker gesetzt", async () => {
    fassungPruefen(antwort(NEU), ALT);
    const el = await rendern();
    expect(el.querySelector('[data-testid="fassungs-hinweis-text"]').textContent).toBe("Neue Version verfügbar");
    await act(async () => { el.querySelector('[data-testid="fassungs-hinweis-laden"]').click(); });
    expect(window.location.assign).toHaveBeenCalledWith("/app/termine?tag=heute");
    expect(window.location.reload).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem("ah_fassung_neu_geladen")).toBe(NEU);
  });

  it("Merker traegt schon diese Fassung: 'wird gerade verteilt', Knopf laedt neu", async () => {
    // Das Neuladen landete im Rollout auf dem alten Server — dieselbe Fassung
    // wird erneut gemeldet.
    window.sessionStorage.setItem("ah_fassung_neu_geladen", NEU);
    fassungPruefen(antwort(NEU), ALT);
    const el = await rendern();
    expect(el.querySelector('[data-testid="fassungs-hinweis-text"]').textContent).toMatch(/wird gerade verteilt/);
    await act(async () => { el.querySelector('[data-testid="fassungs-hinweis-laden"]').click(); });
    expect(window.location.assign).not.toHaveBeenCalled();
    expect(window.location.reload).toHaveBeenCalledTimes(1);
  });

  it("ohne gemeldete Fassung kein Band", async () => {
    const el = await rendern();
    expect(el.querySelector('[data-testid="fassungs-hinweis"]')).toBeNull();
  });
});
