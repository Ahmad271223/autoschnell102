/*
 * Wunsch Ahmad 03.10.2026: "Weitere Sucher anfragen" sendet eine echte Anfrage an den Betreiber
 * (vorher mailto-Link) — und der Betreiber sieht sie unter Freischaltungen mit "Erledigt".
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock("sonner", () => ({ toast }));

const anfragen = [];
const aufrufe = [];
vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(async (url) => {
      if (url.startsWith("/admin/plan-requests")) return { data: anfragen, headers: {} };
      return { data: [], headers: {} };
    }),
    post: vi.fn(async (url, body) => {
      aufrufe.push({ url, body });
      return { data: { ok: true, hinweis: "Anfrage an den Betreiber gesendet — er meldet sich bei dir.",
                       anfrage: { id: "a1", sucher_anzahl: body?.anzahl, wanted_plan: body?.plan } } };
    }),
    put: vi.fn(async (url, body) => { aufrufe.push({ url, body }); return { data: { ok: true } }; }),
  },
  errMsg: (e, f) => f || "Fehler",
}));

const { default: WeitereSucherDialog } = await import("./WeitereSucherDialog");
const { default: AdminFreischaltungen } = await import("@/pages/admin_v2/Freischaltungen");

let root;
let host;
const el = (id) => host.querySelector(`[data-testid="${id}"]`);
const weiter = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

beforeEach(() => {
  anfragen.length = 0;
  aufrufe.length = 0;
  vi.clearAllMocks();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => { root.unmount(); });
  host.remove();
});

describe("Weitere Sucher anfragen (Chef)", () => {
  it("Anzahl, Abo und Nachricht gehen an den Betreiber", async () => {
    const onClose = vi.fn();
    const onGesendet = vi.fn();
    await act(async () => {
      root.render(h(WeitereSucherDialog, { offen: true, anfrage: null, onClose, onGesendet,
                                          preise: { monthly: { price: 150 }, yearly: { price: 1500 } } }));
    });
    await act(async () => { el("weitere-sucher-plus").click(); });
    await act(async () => { el("weitere-sucher-plus").click(); });
    expect(el("weitere-sucher-anzahl").value).toBe("3");
    expect(el("weitere-sucher-summe").textContent).toContain("450 €");
    await act(async () => { el("weitere-sucher-plan-yearly").click(); });
    expect(el("weitere-sucher-summe").textContent).toContain("4.500 €");
    const feld = el("weitere-sucher-nachricht");
    const setzen = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
    await act(async () => {
      setzen.call(feld, "Anna und Ben");
      feld.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => { el("weitere-sucher-senden").click(); });
    await weiter();
    expect(aufrufe).toEqual([{ url: "/dealer/sucher-zugaenge-anfrage",
                               body: { anzahl: 3, plan: "yearly", message: "Anna und Ben" } }]);
    expect(toast.success).toHaveBeenCalled();
    expect(onGesendet).toHaveBeenCalledWith(expect.objectContaining({ sucher_anzahl: 3 }));
    expect(onClose).toHaveBeenCalled();
  });

  it("offene Anfrage: Fenster startet mit ihren Werten", async () => {
    await act(async () => {
      root.render(h(WeitereSucherDialog, { offen: true, onClose: vi.fn(),
                                          anfrage: { id: "a1", sucher_anzahl: 4, wanted_plan: "yearly", message: "x" } }));
    });
    expect(el("weitere-sucher-anzahl").value).toBe("4");
    expect(el("weitere-sucher-plan-yearly").getAttribute("aria-pressed")).toBe("true");
    expect(el("weitere-sucher-senden").textContent).toContain("Anfrage aktualisieren");
  });
});

function Ort() {
  const l = useLocation();
  return h("div", { "data-testid": "ort" }, l.pathname);
}

describe("Freischaltungen (Betreiber)", () => {
  it("zeigt die Anfrage mit Nachricht, Telefon, 'Zur Firma' und 'Erledigt'", async () => {
    anfragen.push({ id: "w1", type: "weitere_sucher", status: "offen", dealer_id: "d1", company_name: "KFZ Müller GmbH",
                    wanted: "3 weitere Sucher-Zugänge · Jährlich (1.500 € je Sucher)", sucher_anzahl: 3,
                    message: "Anna und Ben", contact_email: "chef@x.de", contact_phone: "030 123",
                    requester_user_id: "chef-1", created_at: "2026-10-03T10:00:00+00:00" });
    await act(async () => {
      root.render(h(MemoryRouter, { initialEntries: ["/admin/freischaltungen"] },
        h(Routes, null,
          h(Route, { path: "/admin/freischaltungen", element: h(AdminFreischaltungen) }),
          h(Route, { path: "/admin/users/:id", element: h(Ort) }))));
    });
    await weiter();
    const zeile = el("anfrage-w1");
    expect(zeile.textContent).toContain("Weitere Sucher");
    expect(zeile.textContent).toContain("3 weitere Sucher-Zugänge");
    expect(zeile.textContent).toContain("Anna und Ben");
    expect(zeile.textContent).toContain("030 123");
    expect(zeile.textContent).not.toContain("Paket aktivieren");
    await act(async () => { el("anfrage-erledigt-w1").click(); });
    await weiter();
    expect(aufrufe).toContainEqual({ url: "/admin/plan-requests/w1", body: { status: "erledigt" } });
    await act(async () => { el("weitere-sucher-firma-w1").click(); });
    expect(el("ort").textContent).toBe("/admin/users/chef-1");
  });
});
