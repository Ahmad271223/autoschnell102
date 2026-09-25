/**
 * KI-Auswertung in der Fahrer-App (25.09.2026, abends) — KiFahrerKarte:
 *  - zuerst nur der Knopf, kein Aufruf
 *  - Tipp auf den Knopf: EIN GET; "laeuft" -> Statuszeile, Nachfrage alle 3 s
 *  - Ergebnis: Vertrag / Dein Vorschlag / KI-Zielpreis, vier Geldwerte,
 *    Datenlage; KEIN "Preis übernehmen" (nur lesend), keine Kosten
 *  - "keine" zeigt den Grund des Servers; Netzfehler als Text, kein Absturz
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const driverApi = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/context/DriverContext", () => ({ driverApi }));
vi.mock("@/lib/api", () => ({ errMsg: (e, s) => e?.message || s }));

const { default: KiFahrerKarte } = await import("./KiFahrerKarte");

const LAEUFT = { status: "laeuft", grund: "", ergebnis: null };
const FERTIG = {
  status: "ok", kaufpreis: 8100, preis_vorschlag: 7300,
  ergebnis: {
    items: [{ source_id: "d1", category: "damage", title: "Delle Kotflügel vorne rechts", priority: "orange",
              repair_method: "Ausbeulen ohne Lackieren", repair_estimate_eur: 150, minimum_justified_eur: 80,
              fair_discount_eur: 130, best_realistic_eur: 160, negotiation_start_eur: 220,
              manual_review_required: false, reason: "Kleine Delle ohne Lackschaden." },
            { source_id: "t1", category: "technical", title: "Automatik ruckelt (Symptom)", priority: "rot",
              repair_method: "Diagnose", repair_estimate_eur: 0, minimum_justified_eur: 120,
              fair_discount_eur: 300, best_realistic_eur: 1200, negotiation_start_eur: 3500,
              manual_review_required: false, assessment_kind: "diagnosis_required", diagnosis_cost_eur: 120,
              scenario_low_eur: 300, scenario_mid_eur: 1200, scenario_high_eur: 3500,
              reason: "Nur Symptom, Szenarien statt Befund." },
            { source_id: "u1", category: "accident_history", title: "Unfallfreiheit weicht ab", priority: "rot",
              repair_method: "", repair_estimate_eur: 0, minimum_justified_eur: 0, fair_discount_eur: 0,
              best_realistic_eur: 0, negotiation_start_eur: 0, manual_review_required: true,
              assessment_kind: "expert_check_required", reason: "Sachverständiger." }],
    combined: { sum_fair_eur: 430, overlap_adjustment_eur: 0, minimum_justified_eur: 200, fair_discount_eur: 430,
                best_realistic_eur: 1360, negotiation_start_eur: 3720, deal_risk: "high",
                manual_review_required: true, recommended_purchase_price_eur: 7670,
                diagnosis_items: 1, expert_items: 1, uncertain_eur: 3200 },
    datenlage: "mittel", arguments: ["Die Delle steht nicht im Vertrag."],
  },
};

let wurzel;
let behaelter;
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);

async function starten(props) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(KiFahrerKarte, { apptId: "t1", preisVertrag: 8100, ...props })); });
}
async function klick(t) {
  const k = el(t);
  if (!k) throw new Error(`nicht gefunden: ${t}`);
  await act(async () => { k.click(); });
  await act(async () => { for (let i = 0; i < 5; i += 1) await Promise.resolve(); });
}

beforeEach(() => { driverApi.get.mockResolvedValue({ data: FERTIG }); });
afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null; behaelter?.remove(); vi.clearAllMocks(); vi.useRealTimers();
});

describe("KiFahrerKarte", () => {
  it("ruft erst nach dem Tipp ab und zeigt dann das Ergebnis nur lesend", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    driverApi.get.mockResolvedValueOnce({ data: LAEUFT });
    await starten({});
    expect(el("protokoll-ki-oeffnen")).toBeTruthy();
    expect(el("protokoll-ki-karte")).toBeNull();
    expect(driverApi.get).not.toHaveBeenCalled();
    await klick("protokoll-ki-oeffnen");
    expect(driverApi.get).toHaveBeenCalledTimes(1);
    expect(driverApi.get.mock.calls[0][0]).toBe("/driver/appointments/t1/ki-bewertung");
    expect(el("protokoll-ki-karte").dataset.status).toBe("laeuft");
    expect(el("protokoll-ki-status").textContent).toContain("KI bewertet");
    // nach 3 s fragt die Karte nach -> fertig
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(driverApi.get).toHaveBeenCalledTimes(2);
    const karte = el("protokoll-ki-karte");
    expect(karte.dataset.status).toBe("ok");
    expect(karte.textContent).toContain("Dein Vorschlag");
    expect(karte.textContent).toContain("7.300 €");
    expect(karte.textContent).toContain("Delle Kotflügel vorne rechts");
    expect(el("protokoll-ki-gesamt").textContent).toContain("Fairer Nachlass");
    expect(el("protokoll-ki-gesamt").textContent).toContain("Verhandlung starten");
    expect(el("protokoll-ki-datenlage").textContent).toContain("Datenlage mittel");
    expect(el("protokoll-ki-preis")).toBeNull();            // nur lesend
    // Drei Ergebnisarten (Schadenkatalog): Diagnose mit Szenarien, Fachpruefung ohne Zahl
    expect(el("protokoll-ki-art-t1").textContent).toContain("Diagnose erforderlich");
    expect(el("protokoll-ki-szenarien-t1").textContent).toContain("Diagnose ca. 120 €");
    expect(el("protokoll-ki-szenarien-t1").textContent).toContain("günstig 300 € · mittel 1.200 € · aufwendig 3.500 €");
    expect(el("protokoll-ki-art-u1").textContent).toContain("Fachprüfung erforderlich");
    expect(el("protokoll-ki-diagnose").textContent).toContain("Eine Position nur als Szenario");
    expect(el("protokoll-ki-diagnose").textContent).toContain("unsicherer Anteil bis 3.200 €");
    expect(karte.textContent).not.toContain("Sicherheit");
    expect(karte.textContent).not.toContain("ct");
    // fertig: keine weitere Nachfrage
    await act(async () => { await vi.advanceTimersByTimeAsync(6100); });
    expect(driverApi.get).toHaveBeenCalledTimes(2);
  });

  it("zeigt bei 'keine' den Grund des Servers und bei Netzfehler den Fehlertext", async () => {
    driverApi.get.mockResolvedValueOnce({ data: { status: "keine", grund: "Erst nach dem Abschicken an den Händler", ergebnis: null } });
    await starten({});
    await klick("protokoll-ki-oeffnen");
    expect(el("protokoll-ki-status").textContent).toContain("Erst nach dem Abschicken");
    driverApi.get.mockRejectedValueOnce(new Error("Netz weg"));
    await klick("protokoll-ki-aktualisieren");
    expect(el("protokoll-ki-status").textContent).toContain("Netz weg");
  });
});
