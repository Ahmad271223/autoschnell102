/*
 * Prüfbericht 20.09.2026 (K-11): "Lade…" bietet nach 10 s einen Ausweg.
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SeiteLaedt, { LANGE_MS } from "./SeiteLaedt";

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

describe("K-11: Ausweg aus 'Lade…'", () => {
  it("nach LANGE_MS: Hinweis und 'Neu laden'", async () => {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => { root.render(h(SeiteLaedt, { ganzeSeite: true })); });
    expect(host.querySelector('[data-testid="seite-laedt-lange"]')).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(LANGE_MS - 1); });
    expect(host.querySelector('[data-testid="seite-laedt-lange"]')).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(host.querySelector('[data-testid="seite-laedt-lange"]').textContent).toMatch(/ungewöhnlich lange/);
    await act(async () => { host.querySelector('[data-testid="seite-laedt-neu"]').click(); });
    expect(window.location.reload).toHaveBeenCalledTimes(1);
  });
});
