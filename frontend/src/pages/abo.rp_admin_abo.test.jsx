/*
 * Rollenpruefung 22.09.2026 (RP-256): /abo fragt beim Oeffnen und danach alle
 * 30 s nach, ob der Betreiber inzwischen freigeschaltet hat — vorher blieb der
 * Sucher bis zum Neuladen auf der Seite stehen.
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const zustand = { subscription: null, refresh: vi.fn(async () => null) };
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ user: { role: "sucher", kontonummer: "10023-1" },
                    subscription: zustand.subscription, logout: vi.fn(),
                    refresh: zustand.refresh }),
}));

const { default: Subscription, ABO_NACHFRAGE_MS } = await import("./Subscription");

let root;
let host;
afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  vi.useRealTimers();
  zustand.refresh.mockClear();
  zustand.subscription = null;
});

async function rendern() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(h(MemoryRouter, null, h(Subscription))); });
}

describe("RP-256: Abo-Stand auf /abo", () => {
  it("fragt beim Oeffnen und dann alle 30 s nach", async () => {
    vi.useFakeTimers();
    await rendern();
    expect(zustand.refresh).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(ABO_NACHFRAGE_MS); });
    expect(zustand.refresh).toHaveBeenCalledTimes(2);
    await act(async () => { vi.advanceTimersByTime(ABO_NACHFRAGE_MS); });
    expect(zustand.refresh).toHaveBeenCalledTimes(3);
  });

  it("mit aktivem Abo wird nicht nachgefragt", async () => {
    vi.useFakeTimers();
    zustand.subscription = { active: true };
    await rendern();
    await act(async () => { vi.advanceTimersByTime(ABO_NACHFRAGE_MS * 3); });
    expect(zustand.refresh).not.toHaveBeenCalled();
  });
});
