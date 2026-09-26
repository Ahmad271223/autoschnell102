/**
 * KI-Einschätzung auf der Freigabeseite — KiBewertungKarte.
 * Review 26.09.2026 (Nr. 26): "Neu berechnen" antwortet sofort mit "laeuft"
 * (der Lauf rechnet im Hintergrund); die Karte muss dann alle 3 s nachladen,
 * bis ok/fehler kommt, und danach aufhören.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api", () => ({ api, errMsg: (e, s) => e?.message || s }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const { default: KiBewertungKarte } = await import("./KiBewertungKarte");

const LAEUFT = { status: "laeuft", grund: "", protocol_id: "p1", ergebnis: null };
const FERTIG = {
  status: "ok", kaufpreis: 8100, protocol_id: "p1", input_hash: "h1",
  ergebnis: {
    items: [{ source_id: "d1", category: "damage", title: "Delle Kotflügel vorne rechts", priority: "orange",
              repair_method: "Ausbeulen ohne Lackieren", repair_estimate_eur: 150, minimum_justified_eur: 80,
              fair_discount_eur: 130, best_realistic_eur: 160, negotiation_start_eur: 220,
              manual_review_required: false, reason: "Kleine Delle ohne Lackschaden." }],
    combined: { sum_fair_eur: 130, overlap_adjustment_eur: 0, minimum_justified_eur: 80, fair_discount_eur: 130,
                best_realistic_eur: 160, negotiation_start_eur: 220, deal_risk: "normal",
                manual_review_required: false, recommended_purchase_price_eur: 7970,
                diagnosis_items: 0, expert_items: 0, uncertain_eur: 0 },
    datenlage: "hoch", arguments: ["Die Delle steht nicht im Vertrag."],
  },
};
const FEHLER = { status: "fehler", grund: "KI hat Position Delle nicht bewertet — bitte erneut starten", ergebnis: null };

let wurzel;
let behaelter;
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);

async function starten(props) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  const eintrag = { protocol_id: "p1", preis_vertrag: 8100, preis_vorschlag_fahrer: 7300,
                    ki_bewertung: { status: "ok", input_hash: "h1" } };
  await act(async () => { wurzel.render(createElement(KiBewertungKarte, { eintrag, onPreis: vi.fn(), ...props })); });
  await act(async () => { for (let i = 0; i < 5; i += 1) await Promise.resolve(); });
}
async function klick(t) {
  const k = el(t);
  if (!k) throw new Error(`nicht gefunden: ${t}`);
  await act(async () => { k.click(); });
  await act(async () => { for (let i = 0; i < 5; i += 1) await Promise.resolve(); });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  api.get.mockResolvedValue({ data: FERTIG });
  api.post.mockResolvedValue({ data: LAEUFT });
});
afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null; behaelter?.remove(); vi.clearAllMocks(); vi.useRealTimers();
});

describe("KiBewertungKarte", () => {
  it("lädt nach 'Neu berechnen' alle 3 s nach, bis das Ergebnis da ist, und hört dann auf", async () => {
    await starten({});
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get.mock.calls[0][0]).toBe("/protocols/p1/ki-bewertung");
    expect(el("ki-karte-p1").dataset.status).toBe("ok");
    // Neu berechnen: sofort "laeuft" vom Server, kein Warten in der Anfrage
    // (jede Antwort ein eigenes Objekt, wie vom Netz)
    api.get.mockResolvedValueOnce({ data: { ...LAEUFT } });
    await klick("ki-neu-p1");
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][0]).toBe("/protocols/p1/ki-bewertung/neu");
    expect(el("ki-karte-p1").dataset.status).toBe("laeuft");
    expect(el("ki-karte-p1").textContent).toContain("KI bewertet");
    // nach 3 s: erste Nachfrage (noch laeuft), nach weiteren 3 s: fertig
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(el("ki-karte-p1").dataset.status).toBe("laeuft");
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(api.get).toHaveBeenCalledTimes(3);
    expect(el("ki-karte-p1").dataset.status).toBe("ok");
    expect(el("ki-karte-p1").textContent).toContain("Delle Kotflügel vorne rechts");
    // fertig: keine weitere Nachfrage
    await act(async () => { await vi.advanceTimersByTimeAsync(9100); });
    expect(api.get).toHaveBeenCalledTimes(3);
  });

  it("zeigt bei 'fehler' den Grund des Servers und bietet Neu berechnen an", async () => {
    api.get.mockResolvedValueOnce({ data: LAEUFT });
    await starten({});
    expect(el("ki-karte-p1").dataset.status).toBe("laeuft");
    api.get.mockResolvedValueOnce({ data: FEHLER });
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(el("ki-karte-p1").dataset.status).toBe("fehler");
    expect(el("ki-karte-p1").textContent).toContain("nicht bewertet");
    expect(el("ki-neu-p1")).toBeTruthy();
    // kein Polling mehr im Fehlerzustand
    await act(async () => { await vi.advanceTimersByTimeAsync(6100); });
    expect(api.get).toHaveBeenCalledTimes(2);
  });
});
