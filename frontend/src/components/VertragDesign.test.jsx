/*
 * Wunsch Ahmad 03.10.2026: Vertragsdesign in den Einstellungen — Layout (modern/formular),
 * Farbe und Vorschau mit Musterdaten (auch vor dem Speichern).
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ post: vi.fn() }));
const oeffnen = vi.hoisted(() => ({ blobOeffnen: vi.fn() }));
vi.mock("@/lib/api", () => ({ api, errMsg: (e, f) => e?.message || f }));
vi.mock("@/lib/dateiOeffnen", () => oeffnen);
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const { default: VertragDesign } = await import("./VertragDesign");
const { VERTRAG_FARBEN, vertragFarbe, vertragFarbeHex, vertragLayout } = await import("@/lib/vertragDesign");

let wurzel;
let behaelter;
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);

async function zeigen(props) {
  await act(async () => { wurzel.render(createElement(VertragDesign, props)); });
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

describe("Vertragsdesign", () => {
  it("normalisiert Farbe und Layout", () => {
    expect(vertragFarbe("rot")).toBe("standard");
    expect(vertragFarbe("Petrol")).toBe("petrol");
    expect(vertragFarbe("neon")).toBe("standard");
    expect(vertragFarbeHex("petrol")).toBe("#0F766E");
    expect(vertragLayout("FORMULAR")).toBe("formular");
    expect(vertragLayout("bunt")).toBe("modern");
    expect(VERTRAG_FARBEN).toHaveLength(10);
  });

  it("zeigt die Auswahl und meldet Änderungen", async () => {
    const onChange = vi.fn();
    await zeigen({ farbe: "petrol", layout: "formular", onChange });
    expect(el("vertrag-layout-formular").getAttribute("aria-pressed")).toBe("true");
    expect(el("vertrag-layout-modern").getAttribute("aria-pressed")).toBe("false");
    expect(el("vertrag-farbe-petrol").getAttribute("aria-checked")).toBe("true");
    await act(async () => { el("vertrag-layout-modern").click(); });
    expect(onChange).toHaveBeenLastCalledWith({ vertrag_layout: "modern" });
    await act(async () => { el("vertrag-farbe-lila").click(); });
    expect(onChange).toHaveBeenLastCalledWith({ vertrag_farbe: "lila" });
  });

  it("Vorschau mit der aktuellen (noch ungespeicherten) Auswahl", async () => {
    api.post.mockResolvedValue({ data: new Blob(["%PDF"], { type: "application/pdf" }) });
    await zeigen({ farbe: "gold", layout: "formular", onChange: vi.fn() });
    await act(async () => { el("vertrag-vorschau-druck").click(); });
    expect(api.post).toHaveBeenCalledWith("/dealer/vertrag-vorschau",
      { farbe: "gold", layout: "formular", variante: "druck" }, { responseType: "blob" });
    expect(oeffnen.blobOeffnen).toHaveBeenCalledTimes(1);
    await act(async () => { el("vertrag-vorschau-digital").click(); });
    expect(api.post).toHaveBeenLastCalledWith("/dealer/vertrag-vorschau",
      { farbe: "gold", layout: "formular", variante: "digital" }, { responseType: "blob" });
  });
});
