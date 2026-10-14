/*
 * Prüfung 09.10.2026 (Befund Ahmad "Kaufvertrag aus dem Programm hängt"): der Dialog bei ungespeicherter Arbeit
 * bleibt stehen, bis der Sucher entscheidet — hier öffnen, in neuem Fenster öffnen oder später.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("@/lib/useModal", () => ({ MODAL_ATTRIBUTE: { role: "dialog", "aria-modal": "true" }, useModal: () => ({ current: null }) }));

const { default: StartZielDialog, zielBeschreibung } = await import("./StartZielDialog");

const KA = "https://www.kleinanzeigen.de/s-anzeige/3530379782";
const ZIEL = `/app/vergleich?url=${encodeURIComponent(KA)}&start=0123456789abcdef0123456789abcdef`;

let wurzel;
let behaelter;
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);

beforeEach(() => {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
});

afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
});

describe("zielBeschreibung", () => {
  it("nennt Portal und Nummer", () => {
    expect(zielBeschreibung(ZIEL)).toBe("kleinanzeigen.de · 3530379782");
    expect(zielBeschreibung("/app/vergleich?url=" + encodeURIComponent("https://suchen.mobile.de/fahrzeuge/details.html?id=487000201")))
      .toBe("mobile.de · 487000201");
    expect(zielBeschreibung("/app/termine")).toBe("/app/termine");
    expect(zielBeschreibung("kaputt::")).toBe("");
  });
});

describe("StartZielDialog", () => {
  it("zu ohne Ziel; offen mit drei Entscheidungen", async () => {
    const onHier = vi.fn();
    const onNeuesFenster = vi.fn();
    const onSpaeter = vi.fn();
    await act(async () => {
      wurzel.render(createElement(StartZielDialog, { ziel: null, onHier, onNeuesFenster, onSpaeter }));
    });
    expect(el("start-ziel-dialog")).toBeNull();
    await act(async () => {
      wurzel.render(createElement(StartZielDialog, { ziel: ZIEL, onHier, onNeuesFenster, onSpaeter }));
    });
    const dialog = el("start-ziel-dialog");
    expect(dialog).not.toBeNull();
    expect(dialog.querySelector("[role=dialog]")).not.toBeNull();
    expect(dialog.textContent).toContain("kleinanzeigen.de · 3530379782");
    expect(dialog.textContent).toContain("ungespeichert");
    await act(async () => { el("start-ziel-hier").click(); });
    expect(onHier).toHaveBeenCalledTimes(1);
    await act(async () => { el("start-ziel-neu").click(); });
    expect(onNeuesFenster).toHaveBeenCalledTimes(1);
    await act(async () => { el("start-ziel-spaeter").click(); });
    await act(async () => { el("start-ziel-spaeter-x").click(); });
    expect(onSpaeter).toHaveBeenCalledTimes(2);
  });
});
