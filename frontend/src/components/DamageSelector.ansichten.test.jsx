/**
 * Wunsch Ahmad 14.10.2026: alle fuenf Skizzen-Ansichten sollen am grossen Bildschirm auf einmal ins Bild passen —
 * drei Spalten ab lg (3 + 2) und je Skizze hoechstens SKIZZE_HOEHE_VH der Fensterhoehe (Breite folgt dem
 * Seitenverhaeltnis ueber eine CSS-Variable; Handy/Tablet bleiben bei einer bzw. zwei Spalten).
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() } }));

const { default: DamageSelector, SKIZZE_HOEHE_VH } = await import("./DamageSelector");

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

describe("Skizzen-Raster (Wunsch Ahmad 14.10.2026)", () => {
  it("drei Spalten ab lg, fuenf Ansichten, jede Skizze mit Hoehenbegrenzung", async () => {
    await act(async () => { wurzel.render(createElement(DamageSelector, { damages: [], onChange: () => {} })); });
    const raster = el("damage-grid");
    expect(raster.className).toContain("grid-cols-1");
    expect(raster.className).toContain("md:grid-cols-2");
    expect(raster.className).toContain("lg:grid-cols-3");
    const bilder = ["front", "rear", "left", "right", "top"].map((v) => el(`damage-bild-${v}`));
    expect(bilder.every(Boolean)).toBe(true);
    for (const b of bilder) {
      expect(b.className).toContain("lg:max-w-[var(--skizze-max)]");
      expect(b.style.getPropertyValue("--skizze-max")).toContain(`${SKIZZE_HOEHE_VH}vh`);
      expect(b.style.aspectRatio).not.toBe("");
    }
    expect(SKIZZE_HOEHE_VH).toBeGreaterThan(20);
    expect(SKIZZE_HOEHE_VH).toBeLessThanOrEqual(40);
  });
});
