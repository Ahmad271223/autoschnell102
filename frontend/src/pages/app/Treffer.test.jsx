/**
 * Trefferliste des Dauerlaufs (Wunsch Ahmad 27.11.2026): zeigt, was der Server liefert — Urteil, Preislage,
 * Fundstellen —, fragt laufend nach, „Vertrag“ führt über die Inserat-Adresse in den Vergleich. Ohne
 * Freischaltung (404) geht es still zur Startseite; im App-Code steht kein Programmname.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const post = vi.fn();
const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), message: vi.fn() };
vi.mock("sonner", () => ({ toast }));
vi.mock("@/lib/api", () => ({
  api: { get: (...a) => get(...a), post: (...a) => post(...a) },
  errMsg: (e, f) => e?.response?.data?.detail || e?.message || f,
}));
vi.mock("@/components/ContractDialog", () => ({
  default: ({ vehicleId, vehicle }) => createElement("div", { "data-testid": "vertrag-dialog" }, `${vehicleId}|${vehicle?.make_label}|${vehicle?.fuel_label || "-"}`),
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", role: "sucher" } }) }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { default: Treffer, TAKT_MS, preisSatz, pruefGrund, telLink, mailLink, eckdaten, seit, fehltText } = await import("./Treffer");

const GUT = {
  id: "t-gut", erstellt_am: "2026-11-27T16:58:19+00:00", urteil: "gut", erledigt: false,
  fahrzeug: { marke: "Audi", modell: "Q5", titel: "Audi Q5 3xSline", ez_jahr: 2017, ez_monat: 10, kilometer: 115000, ps: 190,
              preis: 26999, quelle: "Kleinanzeigen", inserat_url: "https://www.kleinanzeigen.de/s-anzeige/3584036047" },
  liste: { zeit: "27.11.2026 17:58:17", privat: true, portal: "Kleinanzeigen", art: "PKW, Gebraucht, 4/5 Türen",
           daten: "EZ: 10/2017, 115.000 Km, Diesel, 140 kW (190 PS), Automatik, Sc...", anbieter: "M.M",
           telefone: ["+49 (0)163 2364007"], entfernung_km: 41, ort: "D-31135 Hildesheim", email: "verkauf@beispiel.de" },
  schaden: { stufe: "kein", gruende: [], text_exakt: true },
  preislage: { lage: "guenstig", mitte: 29500, unter_mitte_eur: 2501, anzahl: 18 }, beschreibung: "Sehr gepflegt.\nScheckheft.",
  merkmale: [{ name: "Kraftstoff", wert: "Diesel" }, { name: "Getriebeart", wert: "Automatik" }, { name: "Inserat-ID", wert: "3584036047" }],
  ausstattung: ["ABS", "Navigationssystem", "Sitzheizung"], fehlt: [],
};
const LEICHT = {
  id: "t-leicht", erstellt_am: "2026-11-27T16:58:03+00:00", urteil: "leicht", erledigt: false,
  fahrzeug: { marke: "Volkswagen", modell: "Golf Plus", ez_jahr: 2005, kilometer: 170000, preis: 2200, quelle: "Kleinanzeigen", inserat_url: null },
  liste: { privat: true }, schaden: { stufe: "leicht", gruende: ["…altersbedingte Gebrauchsspuren vorhanden…"], text_exakt: true },
  preislage: null, beschreibung: "", fehlt: ["Kraftstoff", "Getriebe"],
};

let wurzel = null;
let behaelter = null;
async function rendern() {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(createElement(MemoryRouter, { initialEntries: ["/app/treffer"] },
      createElement(Routes, null,
        createElement(Route, { path: "/app/treffer", element: createElement(Treffer) }),
        createElement(Route, { path: "/app/vergleich", element: createElement("div", { "data-testid": "startseite" }) }))));
    await Promise.resolve();
  });
  await act(async () => { await Promise.resolve(); });
}
const q = (id) => behaelter.querySelector(`[data-testid="${id}"]`);

beforeEach(() => {
  vi.useFakeTimers();
  get.mockReset();
  post.mockReset();
});
afterEach(async () => {
  await act(async () => { wurzel?.unmount(); });
  behaelter?.remove();
  vi.useRealTimers();
});

describe("Trefferliste", () => {
  it("zeigt Urteil, Preislage und Fundstellen und führt zum Vertrag", async () => {
    get.mockResolvedValue({ data: { treffer: [GUT, LEICHT], gesamt: 5, stunden: 48 } });
    await rendern();
    expect(get).toHaveBeenCalledWith("/dauerlauf/treffer", { params: { alle: false } });
    expect(q("treffer-urteil-t-gut").textContent).toBe("Kein Schaden genannt");
    expect(q("treffer-urteil-t-leicht").textContent).toBe("Leichter Schaden");
    expect(q("treffer-preis-t-gut").textContent).toContain("2.501 € unter der Mitte von 18 Vergleichsautos");
    expect(q("treffer-gruende-t-leicht").textContent).toContain("Gebrauchsspuren");
    expect(q("treffer-vertrag-t-gut").tagName).toBe("BUTTON");
    expect(q("treffer-vertrag-t-leicht")).not.toBeNull();           // auch ohne Inserat-Adresse: der Vertrag braucht sie nicht mehr
    expect(q("treffer-fehlt-t-leicht").textContent).toBe("Im Inserat fehlt: Kraftstoff und Getriebe");
    expect(q("treffer-fehlt-t-gut")).toBeNull();
    // die Zeile, wie sie im Programm stand, dazu Anrufen und Mail — sonst keine Knöpfe
    expect(q("treffer-daten-t-gut").textContent).toBe("Audi Q5 · EZ 10/2017 · 115.000 km · 190 PS");
    expect(q("treffer-t-gut").textContent).toContain("D-31135 Hildesheim");
    expect(q("treffer-anrufen-t-gut").getAttribute("href")).toBe("tel:+491632364007");
    expect(q("treffer-mail-t-gut").getAttribute("href")).toBe("mailto:verkauf@beispiel.de");
    expect(q("treffer-anrufen-t-leicht")).toBeNull();
    expect(q("treffer-mail-t-leicht")).toBeNull();
    // je Zeile: Titel (öffnet die Details), Kaufvertrag, dazu Anrufen/Mail nur wenn vorhanden
    expect(q("treffer-t-gut").querySelectorAll("a, button").length).toBe(4);
    expect(q("treffer-t-leicht").querySelectorAll("a, button").length).toBe(2);
  });

  it("„Kaufvertrag“ öffnet den Vertrag sofort und sagt, was fehlt", async () => {
    get.mockResolvedValue({ data: { treffer: [GUT, LEICHT], gesamt: 2, stunden: 48 } });
    post.mockResolvedValue({ data: { vehicle_id: "v_123", vehicle: { make_label: "Volkswagen" }, fehlt: ["Kraftstoff", "Getriebe"] } });
    await rendern();
    expect(q("vertrag-dialog")).toBeNull();
    await act(async () => { q("treffer-vertrag-t-leicht").click(); });
    expect(post).toHaveBeenCalledWith("/dauerlauf/treffer/t-leicht/vertrag");
    expect(q("vertrag-dialog").textContent).toBe("v_123|Volkswagen|-");
    expect(toast.warning).toHaveBeenCalledWith("Im Inserat fehlt: Kraftstoff und Getriebe. Bitte im Vertrag nachtragen.", expect.anything());
    expect(q("treffer-detail")).toBeNull();                          // der Knopf öffnet nicht nebenbei die Details
  });

  it("Klick auf die Zeile zeigt rechts alle Daten, Ausstattung und Beschreibung", async () => {
    get.mockResolvedValue({ data: { treffer: [GUT, LEICHT], gesamt: 2, stunden: 48 } });
    await rendern();
    expect(q("treffer-detail")).toBeNull();
    await act(async () => { q("treffer-details-t-gut").click(); });
    expect(q("treffer-detail-merkmale").textContent).toContain("KraftstoffDiesel");
    expect(q("treffer-detail-merkmale").textContent).toContain("Inserat-ID3584036047");
    expect(q("treffer-detail-ausstattung").textContent).toBe("ABSNavigationssystemSitzheizung");
    expect(q("treffer-detail-text").textContent).toContain("Scheckheft");
    expect(q("treffer-detail-fehlt")).toBeNull();
    await act(async () => { q("treffer-details-t-leicht").click(); });
    expect(q("treffer-detail-fehlt").textContent).toContain("Kraftstoff und Getriebe");
    await act(async () => { q("treffer-detail-zu").click(); });
    expect(q("treffer-detail")).toBeNull();
  });

  it("fragt laufend nach und markiert, was dazukommt", async () => {
    get.mockResolvedValueOnce({ data: { treffer: [LEICHT], gesamt: 1, stunden: 48 } })
       .mockResolvedValue({ data: { treffer: [GUT, LEICHT], gesamt: 2, stunden: 48 } });
    await rendern();
    expect(q("treffer-t-gut")).toBeNull();
    expect(q("treffer-t-leicht").textContent).not.toContain("Neu");
    await act(async () => { await vi.advanceTimersByTimeAsync(TAKT_MS + 10); });
    expect(get).toHaveBeenCalledTimes(2);
    expect(q("treffer-t-gut").textContent).toContain("Neu");
  });

  it("„auch aussortierte“", async () => {
    get.mockResolvedValue({ data: { treffer: [GUT], gesamt: 1, stunden: 48 } });
    await rendern();
    await act(async () => { q("treffer-alle").click(); });
    expect(get).toHaveBeenLastCalledWith("/dauerlauf/treffer", { params: { alle: true } });
  });

  it("leere Liste und fehlende Freischaltung", async () => {
    get.mockResolvedValue({ data: { treffer: [], gesamt: 0, stunden: 48 } });
    await rendern();
    expect(q("treffer-leer").textContent).toContain("Noch keine Treffer");
    await act(async () => { wurzel.unmount(); });
    behaelter.remove();
    get.mockRejectedValue({ response: { status: 404 } });
    await rendern();
    expect(q("startseite")).not.toBeNull();
  });

  it("Sätze zu Preis und Prüfgrund", () => {
    expect(preisSatz(null)).toBeNull();
    expect(preisSatz({ lage: "teuer", mitte: 9000, unter_mitte_eur: -2000, anzahl: 12 })).toContain("2.000 € über der Mitte von 12");
    expect(pruefGrund({ urteil: "gut" })).toBeNull();
    expect(pruefGrund({ urteil: "pruefen", schaden: { stufe: "ohne_text" } })).toContain("keine Beschreibung");
    expect(pruefGrund({ urteil: "pruefen", schaden: { stufe: "kein", text_exakt: false } })).toContain("nicht sicher gelesen");
    expect(pruefGrund({ urteil: "pruefen", schaden: { stufe: "unklar", text_exakt: true } })).toContain("unklar");
  });

  it("Telefon und Mail nur als echte Nummer bzw. Adresse", () => {
    expect(telLink("+49 (0)163 2364007")).toBe("tel:+491632364007");
    expect(telLink("0151/123-456")).toBe("tel:0151123456");
    expect(telLink("javascript:alert(1)")).toBeNull();
    expect(telLink("12")).toBeNull();
    expect(telLink(null)).toBeNull();
    expect(mailLink("verkauf@beispiel.de")).toBe("mailto:verkauf@beispiel.de");
    expect(mailLink("a b@x.de")).toBeNull();
    expect(mailLink("x@y.de?subject=<script>")).toBeNull();
    expect(mailLink("")).toBeNull();
  });

  it("Eckdaten und Alter in Kurzform", () => {
    expect(eckdaten({ ez_jahr: 2009, ez_monat: 9, kilometer: 204000, kraftstoff: "Benzin", ps: 150, getriebe: "Schaltgetriebe" }))
      .toBe("EZ 09/2009 · 204.000 km · Benzin · 150 PS · Schaltgetriebe");
    expect(eckdaten({})).toBe("");
    const jetzt = Date.parse("2026-11-27T17:00:00Z");
    expect(seit("2026-11-27T16:59:48Z", jetzt)).toBe("vor 12 s");
    expect(seit("2026-11-27T16:56:00Z", jetzt)).toBe("vor 4 min");
    expect(seit("unsinn", jetzt)).toBe("");
    expect(fehltText(["Modell", "Kraftstoff", "Getriebe"])).toBe("Modell, Kraftstoff und Getriebe");
    expect(fehltText(["Getriebe"])).toBe("Getriebe");
    expect(fehltText([])).toBe("");
  });

  it("kein Programmname im App-Code", () => {
    const quelle = readFileSync(resolve(process.cwd(), "src/pages/app/Treffer.jsx"), "utf8");
    expect(quelle.toLowerCase()).not.toContain("autopointer");
  });
});
