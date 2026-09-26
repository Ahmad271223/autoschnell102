/*
 * Prüfbericht 20.09.2026 (K-21): "Keine Verbindung" versucht es von selbst
 * erneut (5/10/20 s, "online"), statt nur ein Neuladen anzubieten.
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import VerbindungsFehler, { WIEDERHOLUNG_S } from "./VerbindungsFehler";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let host;
let echt;

beforeEach(() => {
  vi.useFakeTimers();
  echt = window.location;
  delete window.location;
  window.location = { reload: vi.fn() };
});
afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  window.location = echt;
  vi.useRealTimers();
});

async function rendern(props) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(h(VerbindungsFehler, props)); });
  return host;
}

const weiter = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

describe("K-21: automatische Wiederholung", () => {
  it("mit onRetry: nach 5, 10 und 20 s je ein Versuch, mit Countdown", async () => {
    const onRetry = vi.fn(async () => null);
    const el = await rendern({ grund: { status: null, detail: "" }, onRetry });
    expect(WIEDERHOLUNG_S).toEqual([5, 10, 20]);
    expect(el.querySelector('[data-testid="verbindungsfehler-countdown"]').textContent).toMatch(/5 s/);
    await weiter(2000);
    expect(el.querySelector('[data-testid="verbindungsfehler-countdown"]').textContent).toMatch(/3 s/);
    expect(onRetry).not.toHaveBeenCalled();
    await weiter(3000);
    expect(onRetry).toHaveBeenCalledTimes(1);
    await weiter(10000);
    expect(onRetry).toHaveBeenCalledTimes(2);
    await weiter(20000);
    expect(onRetry).toHaveBeenCalledTimes(3);
    // danach bleibt es bei 20 s
    await weiter(20000);
    expect(onRetry).toHaveBeenCalledTimes(4);
    expect(window.location.reload).not.toHaveBeenCalled();
  });

  it("Klick und 'online' rufen onRetry auf, ohne Neuladen", async () => {
    const onRetry = vi.fn(async () => null);
    const el = await rendern({ grund: null, onRetry });
    await act(async () => { el.querySelector('[data-testid="verbindungsfehler-erneut"]').click(); });
    expect(onRetry).toHaveBeenCalledTimes(1);
    await act(async () => { window.dispatchEvent(new Event("online")); });
    expect(onRetry).toHaveBeenCalledTimes(2);
    expect(window.location.reload).not.toHaveBeenCalled();
  });

  it("ohne onRetry: kein Takt (kein Neulade-Kreislauf), aber 'online' und Klick laden neu", async () => {
    const el = await rendern({ grund: { status: 502, detail: "" } });
    expect(el.querySelector('[data-testid="verbindungsfehler-countdown"]')).toBeNull();
    await weiter(60000);
    expect(window.location.reload).not.toHaveBeenCalled();
    await act(async () => { window.dispatchEvent(new Event("online")); });
    expect(window.location.reload).toHaveBeenCalledTimes(1);
    await act(async () => { el.querySelector('[data-testid="verbindungsfehler-erneut"]').click(); });
    expect(window.location.reload).toHaveBeenCalledTimes(2);
  });

  it("nie zwei Versuche gleichzeitig", async () => {
    let fertig;
    const onRetry = vi.fn(() => new Promise((r) => { fertig = r; }));
    const el = await rendern({ grund: null, onRetry });
    await act(async () => { el.querySelector('[data-testid="verbindungsfehler-erneut"]').click(); });
    await act(async () => { window.dispatchEvent(new Event("online")); });
    expect(onRetry).toHaveBeenCalledTimes(1);
    await act(async () => { fertig(); });
    await act(async () => { window.dispatchEvent(new Event("online")); });
    expect(onRetry).toHaveBeenCalledTimes(2);
  });
});
