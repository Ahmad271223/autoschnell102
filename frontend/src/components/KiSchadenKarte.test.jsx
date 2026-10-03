/**
 * KI-Schadennachlass im Vertragsdialog (Umbau 26.09.2026) — KiSchadenKarte:
 *  - ohne Schäden nichts; mit Schäden erst "Sind das alle Schäden?" (kein Aufruf)
 *  - "Ja, Schäden bewerten" ist gesperrt, bis jeder Schaden vollständig ist
 *  - Start = EIN POST; sofort Vorschau (vorläufig), dann Abfrage bis ok
 *  - Karte zeigt vier Geldwerte, Datenlage, Kaufrisiko, Quellen; "als Preis
 *    übernehmen" reicht nur den Zielpreis nach oben
 *  - Limit/Budget/Netzfehler als Statuszeile, nie ein Absturz
 */
import { act, createElement, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), info: vi.fn() }));
vi.mock("@/lib/api", () => ({ api, errMsg: (e, s) => e?.message || s }));
vi.mock("sonner", () => ({ toast: toastMock }));

const { default: KiSchadenKarte } = await import("./KiSchadenKarte");

const DELLE = { id: "d1", type_key: "delle", type_label: "Delle", zone: "Kotflügel vorne rechts",
                severity_data: { groesse: "2–5 cm", lack: "nein", lage: "Fläche" } };
const UNVOLL = { id: "d2", type_key: "kratzer", type_label: "Kratzer", zone: "Tür", severity_data: { laenge: "bis 5 cm" } };
const VORSCHAU = { minimum_justified_eur: 100, fair_discount_eur: 150, best_realistic_eur: 220, negotiation_start_eur: 250,
                   vorlaeufig: true, recommended_purchase_price_eur: 8750, datenlage: "mittel" };
const START = { id: "bew1", status: "laeuft", kaufpreis: 8900, basis: "inseratspreis", vorschau: VORSCHAU, ergebnis: null };
const FERTIG = {
  id: "bew1", status: "ok", kaufpreis: 8900, basis: "inseratspreis", vorschau: VORSCHAU,
  ergebnis: {
    items: [{ source_id: "d1", category: "damage", title: "Delle Kotflügel vorne rechts", priority: "orange",
              repair_method: "Smart-Repair", repair_estimate_eur: 180, minimum_justified_eur: 200,
              fair_discount_eur: 250, best_realistic_eur: 300, negotiation_start_eur: 340,
              manual_review_required: false, reason: "Kleine Delle." }],
    combined: { sum_fair_eur: 250, overlap_adjustment_eur: 0, minimum_justified_eur: 200, fair_discount_eur: 250,
                best_realistic_eur: 300, negotiation_start_eur: 340, deal_risk: "high", manual_review_required: false,
                recommended_purchase_price_eur: 8650 },
    datenlage: "hoch", market: { comparable_count: 12, median_price_eur: 9100, agreed_vs_median_percent: -2.2 },
    quellen: [{ url: "https://www.adac.de/smart-repair", titel: "ADAC Smart Repair" }],
    arguments: ["Die Delle ist im Inserat nicht genannt."],
  },
};

let wurzel;
let behaelter;
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);

function Huelle({ start, ...rest }) {
  const [damages] = useState(start);
  return createElement(KiSchadenKarte, { vehicleId: "v1", damages, ...rest });
}

async function starten(props) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(Huelle, props)); });
}
// Nur Mikrotasks abwarten (kein setTimeout) — die Tests laufen teils mit
// nachgebildeten Zeitgebern, damit die Nachfrage-Schleife pruefbar ist.
async function klick(t) {
  const k = el(t);
  if (!k) throw new Error(`nicht gefunden: ${t}`);
  await act(async () => { k.click(); });
  await act(async () => { for (let i = 0; i < 5; i += 1) await Promise.resolve(); });
}

beforeEach(() => {
  api.post.mockResolvedValue({ data: START });
  api.get.mockResolvedValue({ data: FERTIG });
});
afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null; behaelter?.remove(); vi.clearAllMocks(); vi.useRealTimers();
});

describe("KiSchadenKarte", () => {
  it("sperrt den Knopf, bis jeder Schaden vollstaendig ist", async () => {
    await starten({ start: [] });
    expect(behaelter.innerHTML).toBe("");
    await act(async () => { wurzel.unmount(); });
    wurzel = null; behaelter?.remove();
    await starten({ start: [DELLE, UNVOLL] });
    expect(el("ki-vertrag-frage").dataset.vollstaendig).toBe("0");
    expect(el("ki-vertrag-bewerten").disabled).toBe(true);
    expect(el("ki-vertrag-unvollstaendig")).toBeTruthy();
    expect(el("ki-vertrag-schaden-d2").textContent).toContain("bitte angeben: Tiefe, Anzahl");
    expect(api.post).not.toHaveBeenCalled();
  });

  it("startet einmal, zeigt sofort die Vorschau und dann das Ergebnis mit vier Werten", async () => {
    const onPreis = vi.fn();
    const onBewertungId = vi.fn();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    await starten({ start: [DELLE], onPreis, onBewertungId });
    expect(el("ki-vertrag-frage").dataset.vollstaendig).toBe("1");
    expect(el("ki-vertrag-frage").textContent).toContain("Delle – Kotflügel vorne rechts – 2–5 cm · nein · Fläche");
    await klick("ki-vertrag-bewerten");
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][0]).toBe("/contracts/ki-schadennachlass");
    expect(api.post.mock.calls[0][1]).toMatchObject({ vehicle_id: "v1", damages: [DELLE] });
    // laeuft: Vorschau sichtbar
    expect(el("ki-vertrag-status").dataset.status).toBe("laeuft");
    expect(el("ki-vertrag-vorschau").textContent).toContain("Vorläufige Einschätzung");
    expect(el("ki-vertrag-vorschau-gesamt").textContent).toContain("150 €");
    await klick("ki-vertrag-vorschau-preis");
    expect(onPreis).toHaveBeenCalledWith(8750);
    // nach 3 s fragt die Karte nach -> fertig
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(api.get).toHaveBeenCalledWith("/contracts/ki-schadennachlass/bew1");
    expect(onBewertungId).toHaveBeenCalledWith("bew1");
    const karte = el("ki-vertrag-karte");
    expect(karte.textContent).toContain("Delle Kotflügel vorne rechts");
    expect(el("ki-vertrag-gesamt").textContent).toContain("Mindestens sinnvoll");
    expect(el("ki-vertrag-gesamt").textContent).toContain("Verhandlung starten");
    expect(el("ki-vertrag-gesamt").textContent).toContain("Zielpreis 8.650 €");
    expect(el("ki-vertrag-datenlage").textContent).toContain("Datenlage hoch");
    expect(el("ki-vertrag-risiko").textContent).toContain("Hohes Preisrisiko");
    expect(el("ki-vertrag-markt").textContent).toContain("12 ähnliche Fahrzeuge");
    expect(el("ki-vertrag-quellen").querySelector("a").getAttribute("href")).toBe("https://www.adac.de/smart-repair");
    expect(karte.textContent).not.toContain("Sicherheit");
    await klick("ki-vertrag-preis");
    expect(onPreis).toHaveBeenLastCalledWith(8650);
  });

  it("Limit, Budget und Netzfehler als Statuszeile", async () => {
    api.post.mockResolvedValueOnce({ data: { id: "b2", status: "budget", grund: "Monatsbudget aufgebraucht", vorschau: VORSCHAU, kaufpreis: 8000, basis: "kaufpreis" } });
    await starten({ start: [DELLE], preisBetrag: 8000 });
    await klick("ki-vertrag-bewerten");
    expect(api.post.mock.calls[0][1].purchase_price).toBe(8000);
    expect(el("ki-vertrag-status").textContent).toContain("Monatsbudget");
    expect(el("ki-vertrag-neu")).toBeNull();
    expect(el("ki-vertrag-vorschau")).toBeTruthy();
    await act(async () => { wurzel.unmount(); });
    wurzel = null; behaelter?.remove();
    api.post.mockRejectedValueOnce(new Error("Netz weg"));
    await starten({ start: [DELLE] });
    await klick("ki-vertrag-bewerten");
    expect(el("ki-vertrag-status").textContent).toContain("Keine Verbindung");
    expect(el("ki-vertrag-status").textContent).toContain("Netz weg");
    expect(el("ki-vertrag-neu")).toBeTruthy();
  });
});
