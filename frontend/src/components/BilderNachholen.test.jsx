/*
 * Befund Ahmad 10.10.2026: „Bilder nachholen“ lief in den Cloudflare-Abbruch (englischer Text auf der Seite). Jetzt
 * antwortet der Server vor der Frist mit „läuft im Hintergrund“ (kein Fehler, Knopf bleibt), und ein Proxy-Abbruch
 * bekommt einen deutschen Hinweis.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const post = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ api: {}, errMsg: (e, f) => e?.response?.data?.detail || e?.message || f }));
vi.mock("@/lib/linkCheck", () => ({ postWithRetry503: (_api, pfad, body) => post(pfad, body) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const { default: BilderNachholen } = await import("./BilderNachholen");

let wurzel;
let behaelter;
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);

async function warten() {
  for (let i = 0; i < 4; i += 1) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
});

afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
});

describe("BilderNachholen", () => {
  it("Abruf läuft im Hintergrund: Hinweis, Knopf bleibt", async () => {
    post.mockResolvedValue({ data: { ok: true, laeuft: true, bilder: 0, hinweis: "Der Abruf läuft noch im Hintergrund — bitte in einer Minute noch einmal." } });
    const onBilder = vi.fn();
    await act(async () => { wurzel.render(createElement(BilderNachholen, { url: "https://suchen.mobile.de/x?id=1", onBilder })); });
    await act(async () => { el("bilder-nachholen-btn").click(); });
    await warten();
    expect(post).toHaveBeenCalledWith("/mobile/bilder-nachholen", { url: "https://suchen.mobile.de/x?id=1" });
    expect(el("bilder-nachholen-hinweis").textContent).toContain("läuft noch");
    expect(el("bilder-nachholen-btn")).not.toBeNull();
    expect(onBilder).not.toHaveBeenCalled();
  });

  it("Cloudflare-/Proxy-Abbruch: deutscher Hinweis statt englischem Text", async () => {
    post.mockRejectedValue({ response: { status: 524, data: "<html>The origin web server did not respond to Cloudflare</html>" } });
    await act(async () => { wurzel.render(createElement(BilderNachholen, { url: "https://suchen.mobile.de/x?id=1" })); });
    await act(async () => { el("bilder-nachholen-btn").click(); });
    await warten();
    expect(el("bilder-nachholen-hinweis").textContent).toContain("dauert gerade zu lange");
    expect(el("bilder-nachholen-hinweis").textContent).not.toContain("Cloudflare");
    expect(el("bilder-nachholen-btn")).not.toBeNull();
  });

  it("Fotos da: Ergebnis an die Seite; 409 sperrt den Knopf", async () => {
    post.mockResolvedValue({ data: { ok: true, bilder: 2, images: ["a", "b"], images_thumbs: [] } });
    const onBilder = vi.fn();
    await act(async () => { wurzel.render(createElement(BilderNachholen, { url: "https://suchen.mobile.de/x?id=1", onBilder })); });
    await act(async () => { el("bilder-nachholen-btn").click(); });
    await warten();
    expect(onBilder).toHaveBeenCalledWith(expect.objectContaining({ bilder: 2 }));
    post.mockRejectedValue({ response: { status: 409, data: { detail: "Schon 3-mal ohne Fotos nachgeholt" } } });
    await act(async () => { el("bilder-nachholen-btn").click(); });
    await warten();
    expect(el("bilder-nachholen-hinweis").textContent).toContain("3-mal");
    expect(el("bilder-nachholen-btn")).toBeNull();
  });
});
