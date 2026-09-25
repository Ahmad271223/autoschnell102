/**
 * KI-Schadennachlass im Vertragsdialog (Stufe 3, 26.09.2026) — KiSchadenKarte:
 *  - ohne Schäden nichts; mit Schäden erst "Sind das alle Schäden?" (kein Aufruf)
 *  - "Ja, Schäden bewerten" = EIN POST /contracts/ki-schadennachlass, Karte danach
 *  - "als Kaufpreis übernehmen" reicht nur den Zielpreis nach oben
 *  - Antwort auf eine Rückfrage landet am Schaden, die Karte gilt als veraltet
 *  - Fehler/Limit als Statuszeile, nie ein Absturz
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
                severity_data: { groesse: "2–5 cm", lack: "nein" } };
const ERGEBNIS = {
  id: "bew1", status: "ok", kaufpreis: 8900, basis: "inseratspreis",
  ergebnis: {
    items: [{ source_id: "d1", category: "damage", title: "Delle Kotflügel vorne rechts", priority: "orange",
              repair_method: "Smart-Repair", repair_estimate_eur: 180, recommended_discount_eur: 250,
              discount_min_eur: 220, discount_max_eur: 290, confidence: 0.88, manual_review_required: false,
              reason: "Kleine Delle." }],
    combined: { sum_of_items_eur: 250, overlap_adjustment_eur: 0, recommended_discount_eur: 250,
                discount_min_eur: 220, discount_max_eur: 290, negotiation_start_eur: 300, confidence: 0.85,
                manual_review_required: false, recommended_purchase_price_eur: 8650 },
    needs_information: [{ source_id: "d1", question: "Ist der Lack beschädigt?", options: ["Ja", "Nein"] }],
    arguments: ["Die Delle ist im Inserat nicht genannt."],
    quellen: [{ url: "https://www.adac.de/smart-repair", titel: "ADAC Smart Repair" }],
  },
};

let wurzel;
let behaelter;
let zustand;
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);

function Huelle({ start, ...rest }) {
  const [damages, setDamages] = useState(start);
  zustand = { damages };
  return createElement(KiSchadenKarte, { vehicleId: "v1", damages, onDamagesChange: setDamages, ...rest });
}

async function starten(props) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(Huelle, props)); });
}
async function klick(t) {
  const k = el(t);
  if (!k) throw new Error(`nicht gefunden: ${t}`);
  await act(async () => { k.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

beforeEach(() => { api.post.mockResolvedValue({ data: ERGEBNIS }); });
afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null; behaelter?.remove(); vi.clearAllMocks();
});

describe("KiSchadenKarte", () => {
  it("fragt erst, ruft dann genau einmal und zeigt die Karte", async () => {
    const onPreis = vi.fn();
    const onBewertungId = vi.fn();
    await starten({ start: [], onPreis, onBewertungId });
    expect(behaelter.innerHTML).toBe("");
    await act(async () => { wurzel.unmount(); });
    wurzel = null; behaelter?.remove();
    await starten({ start: [DELLE], onPreis, onBewertungId });
    expect(el("ki-vertrag-frage")).toBeTruthy();
    expect(el("ki-vertrag-frage").textContent).toContain("1 Schaden erfasst");
    expect(el("ki-vertrag-frage").textContent).toContain("Delle – Kotflügel vorne rechts – 2–5 cm · nein");
    expect(api.post).not.toHaveBeenCalled();
    await klick("ki-vertrag-bewerten");
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][0]).toBe("/contracts/ki-schadennachlass");
    expect(api.post.mock.calls[0][1]).toMatchObject({ vehicle_id: "v1", damages: [DELLE] });
    expect(onBewertungId).toHaveBeenCalledWith("bew1");
    const karte = el("ki-vertrag-karte");
    expect(karte.textContent).toContain("Delle Kotflügel vorne rechts");
    expect(karte.textContent).toContain("250 €");
    expect(el("ki-vertrag-gesamt").textContent).toContain("Zielpreis 8.650 €");
    await klick("ki-vertrag-preis");
    expect(onPreis).toHaveBeenCalledWith(8650);
    expect(el("ki-vertrag-argumente").textContent).toContain("nicht genannt");
    // Stufe 5: Quellen der Marktrecherche als Links
    expect(el("ki-vertrag-quellen").textContent).toContain("ADAC Smart Repair");
    expect(el("ki-vertrag-quellen").querySelector("a").getAttribute("href")).toBe("https://www.adac.de/smart-repair");
  });

  it("Antwort auf die Rückfrage landet am Schaden und die Karte gilt als veraltet", async () => {
    await starten({ start: [DELLE] });
    await klick("ki-vertrag-bewerten");
    expect(el("ki-vertrag-antwort-d1-Nein")).toBeTruthy();
    await klick("ki-vertrag-antwort-d1-Nein");
    expect(zustand.damages[0].severity_data["frage_ist_der_lack_beschädigt"]).toBe("Nein");
    expect(el("ki-vertrag-karte")).toBeNull();
    expect(el("ki-vertrag-frage").dataset.veraltet).toBe("1");
    expect(el("ki-vertrag-bewerten").textContent).toContain("neu bewerten");
    await klick("ki-vertrag-bewerten");
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[1][1].damages[0].severity_data["frage_ist_der_lack_beschädigt"]).toBe("Nein");
    expect(el("ki-vertrag-karte")).toBeTruthy();
  });

  it("Limit und Fehler als Statuszeile mit 'Erneut versuchen'", async () => {
    api.post.mockResolvedValueOnce({ data: { status: "limit", grund: "Höchstens 40 je Stunde" } });
    await starten({ start: [DELLE], preisBetrag: 8000 });
    await klick("ki-vertrag-bewerten");
    expect(api.post.mock.calls[0][1].purchase_price).toBe(8000);
    expect(el("ki-vertrag-status").textContent).toContain("Stundenlimit");
    api.post.mockRejectedValueOnce(new Error("Netz weg"));
    await klick("ki-vertrag-neu");
    expect(el("ki-vertrag-status").textContent).toContain("Keine Verbindung");
    expect(el("ki-vertrag-status").textContent).toContain("Netz weg");
    expect(el("ki-vertrag-neu")).toBeTruthy();
  });
});
