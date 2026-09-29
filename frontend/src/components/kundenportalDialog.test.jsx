/**
 * Kundenportal-Dialog (29.09.2026): Stand laden, Code erzeugen und groß anzeigen, Nachricht
 * kopieren, zurückziehen, unterschriebenen Vertrag öffnen; ohne Firmenseite ein Hinweis mit Link.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const post = vi.fn();
const del = vi.fn();
const openAuthedFile = vi.fn();
const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), message: vi.fn() };
vi.mock("sonner", () => ({ toast }));
vi.mock("@/lib/api", () => ({
  api: { get: (...a) => get(...a), post: (...a) => post(...a), put: vi.fn(), delete: (...a) => del(...a) },
  errMsg: (e, f) => e?.response?.data?.detail || e?.message || f,
  openAuthedFile: (...a) => openAuthedFile(...a),
}));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { default: KundenportalDialog } = await import("./KundenportalDialog");

const VERTRAG = { id: "c1", contract_no: "KV-1", make: "VW", model: "Golf", version: 1, seller_name: "Erika Mustermann" };
let wurzel = null;
let behaelter = null;
async function rendern(props) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => {
    wurzel.render(createElement(MemoryRouter, null, createElement(KundenportalDialog, { open: true, contract: VERTRAG, onClose: () => {}, ...props })));
  });
  return behaelter;
}
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);
async function klick(t) { await act(async () => { el(t).click(); }); }
afterEach(() => {
  if (wurzel) act(() => wurzel.unmount());
  behaelter?.remove();
  wurzel = null;
  behaelter = null;
  vi.clearAllMocks();
});

describe("KundenportalDialog", () => {
  it("erzeugt einen Code, zeigt ihn groß mit Adresse und Nachricht, zieht ihn zurück", async () => {
    let stand = { status: "keiner", code: null, url: "https://kfz-mueller.auto-schnellkauf.de", slug: "kfz-mueller", pdf_signiert: false, code_tage: 7 };
    get.mockImplementation(async () => ({ data: stand }));
    post.mockImplementation(async () => {
      stand = { ...stand, status: "offen", code: "K7M3XP", laeuft_ab: "2026-10-06T10:00:00+00:00", version: 1 };
      return { data: stand };
    });
    del.mockImplementation(async () => { stand = { ...stand, status: "zurueckgezogen", code: null }; return { data: stand }; });
    Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => undefined) } });
    window.confirm = () => true;
    await rendern();
    expect(el("kundenportal-status").textContent).toContain("Noch kein Code");
    await klick("kundenportal-erzeugen");
    expect(post).toHaveBeenCalledWith("/contracts/c1/portal");
    expect(el("kundenportal-code").textContent).toBe("K7M3XP");
    expect(el("kundenportal-url").getAttribute("href")).toBe("https://kfz-mueller.auto-schnellkauf.de");
    await klick("kundenportal-nachricht-kopieren");
    const text = navigator.clipboard.writeText.mock.calls[0][0];
    expect(text).toContain("K7M3XP");
    expect(text).toContain("https://kfz-mueller.auto-schnellkauf.de");
    expect(text).toContain("Erika Mustermann");
    expect(toast.success).toHaveBeenCalledWith("Nachricht kopiert");
    await klick("kundenportal-zurueckziehen");
    expect(del).toHaveBeenCalledWith("/contracts/c1/portal");
    expect(el("kundenportal-status").textContent).toContain("zurückgezogen");
    expect(el("kundenportal-code")).toBeNull();
  });

  it("unterschrieben: Datum, Name und Knopf zum unterschriebenen PDF", async () => {
    get.mockResolvedValue({ data: { status: "unterschrieben", unterschrieben_am: "2026-09-29T10:00:00+00:00", name: "Erika Mustermann", pdf_signiert: true, url: "https://x" } });
    await rendern();
    expect(el("kundenportal-unterschrieben").textContent).toContain("Erika Mustermann");
    expect(el("kundenportal-erzeugen")).toBeNull();
    await klick("kundenportal-pdf");
    expect(openAuthedFile).toHaveBeenCalledWith("/contracts/c1/portal/pdf");
  });

  it("ohne Firmenseite: Hinweis mit Link zu den Einstellungen", async () => {
    get.mockResolvedValue({ data: { status: "keiner", url: "" } });
    post.mockRejectedValue({ response: { status: 409, data: { detail: "Erst die Firmenseite einrichten (Einstellungen → Firmenseite & Kundenportal): Adresse festlegen und einschalten." } } });
    await rendern();
    await klick("kundenportal-erzeugen");
    expect(el("kundenportal-fehler").textContent).toContain("Firmenseite einrichten");
    expect(el("kundenportal-fehler").querySelector("a").getAttribute("href")).toBe("/app/einstellungen");
  });
});
