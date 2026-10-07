/*
 * Vorgangsnummer (08.10.2026): /app/vorgang/<id> ist der Rückfall, wenn die Browser-Erweiterung in diesem Browser
 * fehlt — zeigt das Auto, sagt, dass das Programm selbst öffnet, und bietet nur sichere Portal-Links an.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api", () => ({ api, errMsg: (e, f) => e?.message || f }));
vi.mock("react-router-dom", () => ({
  useParams: () => ({ id: "0f8c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b" }),
  Link: ({ to, children, ...rest }) => createElement("a", { href: to, ...rest }, children),
}));

const { default: Vorgang, sichererLink } = await import("./Vorgang");

const ID = "0f8c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b";
let wurzel;
let behaelter;
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);

async function warten() {
  for (let i = 0; i < 3; i += 1) {
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

describe("Vorgangsseite (Rückfall ohne Erweiterung)", () => {
  it("zeigt Auto, Hinweis, sichere Links und den Kaufvertrag", async () => {
    api.get.mockResolvedValue({ data: {
      vorgang_id: ID, uebernommen: false, kennung: "mobile:42196329136896",
      inserat_url: "https://suchen.mobile.de/fahrzeuge/details.html?id=42196329136896",
      fahrzeug: { marke: "VW", modell: "Golf", ez_monat: 5, ez_jahr: 2019, kilometer: 60000, ps: 290, preis: 23850 },
      links: [
        { portal: "mobile.de", url: "https://suchen.mobile.de/fahrzeuge/search.html?ms=25200" },
        { portal: "AutoScout24", url: "https://www.autoscout24.de/lst/vw/golf" },
        { portal: "boese", url: "javascript:alert(1)" },
      ],
    } });
    await act(async () => { wurzel.render(createElement(Vorgang)); });
    await warten();
    expect(api.get).toHaveBeenCalledWith(`/werkzeuge/vorgang/${ID}`);
    expect(el("vorgang-fahrzeug").textContent).toContain("VW Golf");
    expect(el("vorgang-hinweis").textContent).toContain("öffnet die Vergleiche deshalb gleich selbst");
    expect(el("vorgang-link-mobile.de").getAttribute("href")).toContain("suchen.mobile.de");
    expect(el("vorgang-link-AutoScout24")).not.toBeNull();
    expect(el("vorgang-link-boese")).toBeNull();
    expect(el("vorgang-vertrag").getAttribute("href")).toBe(
      "/app/vergleich?url=" + encodeURIComponent("https://suchen.mobile.de/fahrzeuge/details.html?id=42196329136896")
      + "&vertrag=1");
  });

  it("meldet die Erweiterung 'übernommen', sagt die Seite das", async () => {
    api.get.mockResolvedValue({ data: { vorgang_id: ID, uebernommen: false, links: [], fahrzeug: {} } });
    await act(async () => { wurzel.render(createElement(Vorgang)); });
    await warten();
    expect(el("vorgang-ohne-inserat")).not.toBeNull();
    await act(async () => {
      // wie content.js der Erweiterung (window.postMessage im selben Fenster; jsdom setzt "source" nicht selbst)
      window.dispatchEvent(new MessageEvent("message", {
        data: { __autoschnell: true, type: "VORGANG_ERGEBNIS", id: ID, ok: true }, source: window,
      }));
      // fremde Nachricht (anderer Vorgang) aendert nichts
      window.dispatchEvent(new MessageEvent("message", {
        data: { __autoschnell: true, type: "VORGANG_ERGEBNIS", id: "anders", ok: false }, source: window,
      }));
    });
    expect(el("vorgang-hinweis").textContent).toContain("hat die Vergleiche und das Inserat geöffnet");
  });

  it("unbekannter Vorgang: klare Meldung", async () => {
    api.get.mockRejectedValue(Object.assign(new Error("x"), { response: { status: 404 } }));
    await act(async () => { wurzel.render(createElement(Vorgang)); });
    await warten();
    expect(el("vorgang-fehler").textContent).toContain("gibt es nicht");
  });

  it("nur https-Adressen der Portale", () => {
    expect(sichererLink("https://www.kleinanzeigen.de/s-anzeige/123")).toBe("https://www.kleinanzeigen.de/s-anzeige/123");
    expect(sichererLink("http://suchen.mobile.de/x")).toBeNull();
    expect(sichererLink("https://boese.example/x")).toBeNull();
    expect(sichererLink("javascript:alert(1)")).toBeNull();
  });
});
