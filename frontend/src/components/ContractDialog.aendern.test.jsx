/**
 * Wunsch Ahmad 01.10.2026: Chef und Sucher ändern einen bestehenden Kaufvertrag jederzeit nachträglich —
 * derselbe Dialog wie beim Anlegen, vorausgefüllt, alles änderbar; „Neue Fassung erstellen“ ruft
 * POST /contracts/{id}/neue-fassung. Vertragsnummer und Abholtermin bleiben (gesperrt).
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const post = vi.fn();
const get = vi.fn();
const refresh = vi.fn();
const DEALER = {
  company_name: "Käufer GmbH", address: "Weg 1", zip_code: "10115", city: "Berlin",
  vertrags_kundennummer: "482913", default_terms: "", digital_vertragstext: "",
  digital_vertragstext_standard: "Standard", sondervereinbarungen_effektiv: "Nr. {kundennummer}",
};

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("@/lib/api", () => ({
  api: { get: (...a) => get(...a), post: (...a) => post(...a), put: vi.fn(), delete: vi.fn() },
  errMsg: (e, f) => e?.response?.data?.detail || e?.message || f,
}));
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ dealer: DEALER, user: { id: "u1", role: "dealer" }, refresh }),
}));
vi.mock("@/components/MarktdatenKarte", () => ({ useMarktHinweis: () => null }));
vi.mock("./KiSchadenKarte", () => ({ default: () => null }));
vi.mock("./DamageSelector", () => ({ default: () => null, damagesToText: (l) => (l || []).map((d) => `${d.type_label} – ${d.zone}`).join("; ") }));
vi.mock("@/lib/pdf", () => ({ openContractPdf: vi.fn() }));
vi.mock("@/lib/dateiOeffnen", () => ({ blobOeffnen: vi.fn() }));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { default: ContractDialog, anfangsFormular, formularAusVertrag } = await import("./ContractDialog");

const VERTRAG = {
  id: "c1", version: 1, contract_no: "KV-1", make: "BMW", model: "320d", vehicle_id: "v1",
  pickup_date: "2099-09-10", pickup_time: "10:00", purchase_price: 12500,
  contract_data: {
    seller_name: "Vera Verkauf", seller_city: "Berlin", purchase_price: 12500, payment_method: "Bar",
    vehicle_make: "BMW", vehicle_model: "320d", dealer_company: "Käufer GmbH", dealer_address: "Weg 1",
    dealer_zip: "10115", dealer_city: "Berlin", show_vat: false, vertrags_kundennummer: "777",
    additional_terms: "Eigene Vereinbarung", damages_text: "",
    damages: [{ type_key: "delle", type_label: "Delle", zone: "Tür", view: "links" }, "kaputter Eintrag"],
  },
};

let wurzel = null;
let behaelter = null;
const feld = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
function rendern(el) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  act(() => { wurzel.render(el); });
  return behaelter;
}
async function absenden() {
  const form = behaelter.querySelector("form");
  await act(async () => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await Promise.resolve();
    await Promise.resolve();
  });
}
afterEach(() => {
  if (wurzel) act(() => wurzel.unmount());
  behaelter?.remove();
  wurzel = null;
  behaelter = null;
});
beforeEach(() => {
  post.mockReset();
  get.mockReset();
  refresh.mockReset();
  get.mockResolvedValue({ data: null });
  try { window.localStorage.clear(); } catch { /* egal */ }
});

describe("formularAusVertrag", () => {
  it("füllt das Formular aus contract_data, Nummer/Preis/Termin aus dem Vertrag, Rest aus der Vorlage", () => {
    const basis = anfangsFormular({}, DEALER, "2026-10-01");
    const f = formularAusVertrag(basis, VERTRAG);
    expect(f.seller_name).toBe("Vera Verkauf");
    expect(f.purchase_price).toBe("12500");
    expect(f.contract_no).toBe("KV-1");
    expect(f.kundennummer).toBe("777");
    expect(f.pickup_date).toBe("2099-09-10");
    expect(f.pickup_time).toBe("10:00");
    expect(f.additional_terms).toBe("Eigene Vereinbarung");
    expect(f.damages).toEqual([{ type_key: "delle", type_label: "Delle", zone: "Tür", view: "links" }]);
    expect(f.damages_text).toBe("Delle – Tür");
    expect(f.show_vat).toBe(false);
    expect(f.digital_vertragstext).toBe("Standard");      // nicht im Vertrag -> Vorlage
    expect(f.seller_phone).toBe("");
  });
});

describe("ContractDialog im Ändern-Modus", () => {
  it("vorausgefüllt, Nummer und Termin gesperrt, Absenden erzeugt die neue Fassung", async () => {
    const onCreated = vi.fn();
    post.mockResolvedValue({ data: { id: "c1", version: 2, geaendert: true } });
    rendern(createElement(ContractDialog, {
      open: true, onClose: () => {}, vehicleId: "v1", onCreated, vertrag: VERTRAG,
      vehicle: { make_label: "BMW", model_label: "320d" },
    }));
    await act(async () => { await Promise.resolve(); });
    expect(feld("contract-dialog-overline").textContent).toBe("Kaufvertrag ändern · Fassung 1 → 2");
    expect(feld("contract-vertragsnummer").value).toBe("KV-1");
    expect(feld("contract-vertragsnummer").disabled).toBe(true);
    expect(feld("contract-pickup-date").disabled).toBe(true);
    expect(feld("contract-pickup-date").value).toBe("2099-09-10");
    expect(feld("contract-seller-name").value).toBe("Vera Verkauf");
    expect(feld("contract-price").value).toBe("12500");
    expect(feld("submit-contract").textContent).toContain("Neue Fassung erstellen");
    await absenden();
    expect(post).toHaveBeenCalledTimes(1);
    const [url, body] = post.mock.calls[0];
    expect(url).toBe("/contracts/c1/neue-fassung");
    expect(body.vehicle_id).toBe("v1");
    expect(body.seller_name).toBe("Vera Verkauf");
    expect(body.purchase_price).toBe(12500);
    expect(body.additional_terms).toBe("Eigene Vereinbarung");
    expect(body.damages).toHaveLength(1);
    expect(onCreated).toHaveBeenCalledWith({ id: "c1", version: 2, geaendert: true });
    // kein Entwurf im Browser (Ändern-Modus führt keinen)
    expect(Object.keys(window.localStorage).some((k) => k.includes("entwurf"))).toBe(false);
  });

  it("ohne vertrag: normaler Anlege-Modus wie bisher", async () => {
    rendern(createElement(ContractDialog, {
      open: true, onClose: () => {}, vehicleId: "v1", onCreated: () => {},
      vehicle: { seller_name: "Vera Verkauf", make_label: "BMW", model_label: "320d" },
    }));
    await act(async () => { await Promise.resolve(); });
    expect(feld("contract-dialog-overline").textContent).toBe("Kaufvertrag");
    expect(feld("contract-vertragsnummer").disabled).toBe(false);
    expect(feld("submit-contract").textContent).toContain("PDF erstellen");
  });
});
