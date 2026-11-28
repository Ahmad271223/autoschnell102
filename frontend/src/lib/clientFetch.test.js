/*
 * Prüfbericht 20.09.2026 (U-114): extensionReady() raeumt seinen
 * message-Hoerer auch beim Zeitablauf ab — vorher blieb je Vergleich mit
 * needs_client_fetch einer haengen.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { extensionReady } from "./clientFetch";

const hoerer = (spion) => spion.mock.calls.filter(([typ]) => typ === "message").length;

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe("U-114: extensionReady", () => {
  it("ohne Erweiterung: false nach der Wartezeit, Hoerer wieder abgehaengt", async () => {
    const an = vi.spyOn(window, "addEventListener");
    const ab = vi.spyOn(window, "removeEventListener");
    const p = extensionReady(400);
    expect(hoerer(an)).toBe(1);
    await vi.advanceTimersByTimeAsync(400);
    await expect(p).resolves.toBe(false);
    expect(hoerer(ab)).toBe(1);
    expect(ab.mock.calls[0][1]).toBe(an.mock.calls[0][1]);   // derselbe Hoerer
  });

  it("mit Erweiterung: true sofort, Hoerer abgehaengt, Zeitablauf ohne Wirkung", async () => {
    const ab = vi.spyOn(window, "removeEventListener");
    const p = extensionReady(400);
    // postMessage geht in jsdom asynchron — das Ereignis direkt zustellen.
    const e = new MessageEvent("message", { data: { __autoschnell: true, type: "EXT_READY" }, source: window });
    window.dispatchEvent(e);
    await expect(p).resolves.toBe(true);
    expect(hoerer(ab)).toBe(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(hoerer(ab)).toBe(1);
  });
});
