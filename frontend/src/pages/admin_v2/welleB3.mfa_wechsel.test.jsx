/*
 * Rollenprüfung 22.09.2026, Welle B3 (RP-556): Zwei-Faktor "Gerät wechseln"
 * ohne Abschalten in den Einstellungen des Super-Admins.
 *  - Knopf "Gerät wechseln": Code der bisherigen App -> POST /admin/me/mfa/wechsel
 *    -> neuer Schlüssel im Kasten, Knopf "Neues Gerät bestätigen"
 *  - Code des neuen Geräts -> POST /admin/me/mfa/aktivieren -> neue Notfall-Codes,
 *    KEIN neues Token (Sitzung bleibt), Hinweis "Gerät gewechselt"
 *  - Abbruch im Prompt sendet nichts; "Abschalten" bleibt, die Rückfrage nennt den neuen Weg
 *  - laufender Wechsel nach Neuladen sichtbar (wechsel_offen)
 */
import { act, createElement as h } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));
vi.mock("@/lib/sitzung", () => ({ tokenSetzen: vi.fn(), TOKEN_APP: "ah_token" }));
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ user: { id: "sa", role: "admin", is_super_admin: true, username: "chef" } }),
}));

const netz = { status: null, posts: [], antworten: {} };
vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(async () => ({ data: netz.status })),
    post: vi.fn(async (url, body) => {
      netz.posts.push({ url, body });
      const a = netz.antworten[url];
      if (a instanceof Error) throw a;
      return a || { data: { ok: true } };
    }),
  },
  errMsg: (e, f) => e?.message || f || "Fehler",
}));

const { toast } = await import("sonner");
const { tokenSetzen } = await import("@/lib/sitzung");
const { default: AdminSettings } = await import("./Settings");

let root;
let host;
beforeEach(() => {
  netz.status = { aktiv: true, wiederherstellungscodes_uebrig: 8, pflicht: true, aktiviert_am: "2026-09-01T10:00:00+00:00" };
  netz.posts = []; netz.antworten = {};
  vi.mocked(toast.success).mockClear(); vi.mocked(toast.error).mockClear();
  vi.mocked(tokenSetzen).mockClear();
  window.prompt = vi.fn(() => null);
  window.confirm = vi.fn(() => false);
});
afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
});

const tick = async (n = 3) => {
  for (let i = 0; i < n; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};
async function rendern() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(h(MemoryRouter, null, h(AdminSettings))); });
  await tick();
  return host;
}
function tippen(input, wert) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  setter.call(input, wert);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}
const klick = async (el) => { await act(async () => { el.click(); }); await tick(); };
const q = (el, id) => el.querySelector(`[data-testid="${id}"]`);

describe("RP-556: Gerät wechseln ohne Abschalten", () => {
  it("Code der alten App -> neuer Schlüssel -> Code des neuen Geräts -> neue Notfall-Codes, Sitzung bleibt", async () => {
    window.prompt = vi.fn(() => " 111222 ");
    netz.antworten["/admin/me/mfa/wechsel"] = { data: {
      secret: "NEUERSCHLUESSEL", otpauth_uri: "otpauth://totp/AutoSchnell:chef?secret=NEUERSCHLUESSEL",
      wechsel: true, gueltig_bis: new Date(Date.now() + 15 * 60000).toISOString(),
    } };
    netz.antworten["/admin/me/mfa/aktivieren"] = { data: {
      ok: true, aktiv: true, geraet_gewechselt: true, wiederherstellungscodes: ["aaaaaaaa-11111111", "bbbbbbbb-22222222"],
    } };
    const el = await rendern();
    expect(q(el, "mfa-geraet-wechseln")).toBeTruthy();
    expect(q(el, "mfa-deaktivieren")).toBeTruthy();
    expect(q(el, "mfa-einrichten")).toBeNull();
    await klick(q(el, "mfa-geraet-wechseln"));
    expect(window.prompt).toHaveBeenCalledWith(expect.stringMatching(/BISHERIGEN App/));
    expect(netz.posts[0]).toEqual({ url: "/admin/me/mfa/wechsel", body: { code: "111222" } });
    // neuer Schlüssel sichtbar, klare Beschriftung für das neue Gerät
    expect(q(el, "mfa-wechsel-box")).toBeTruthy();
    expect(q(el, "mfa-secret").textContent).toBe("NEUERSCHLUESSEL");
    expect(q(el, "mfa-wechsel-box").textContent).toMatch(/bisherige Schlüssel gilt weiter/);
    expect(q(el, "mfa-wechsel-box").textContent).toMatch(/NEUEN Gerät/);
    expect(q(el, "mfa-aktivieren").textContent).toBe("Neues Gerät bestätigen");
    expect(q(el, "mfa-geraet-wechseln")).toBeNull();
    // Code vom neuen Gerät bestätigen
    await act(async () => { tippen(q(el, "mfa-aktivieren-code"), "333444"); });
    await klick(q(el, "mfa-aktivieren"));
    expect(netz.posts[1]).toEqual({ url: "/admin/me/mfa/aktivieren", body: { code: "333444" } });
    expect(q(el, "mfa-wiederherstellung")).toBeTruthy();
    expect(q(el, "mfa-wiederherstellung").textContent).toMatch(/aaaaaaaa-11111111/);
    expect(q(el, "mfa-wechsel-box")).toBeNull();
    expect(tokenSetzen).not.toHaveBeenCalled();
    expect(toast.success).toHaveBeenCalledWith(expect.stringMatching(/Gerät gewechselt/), expect.anything());
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("Abbruch im Prompt sendet nichts; Fehler vom Server wird gezeigt", async () => {
    const el = await rendern();
    await klick(q(el, "mfa-geraet-wechseln"));
    expect(netz.posts).toEqual([]);
    expect(q(el, "mfa-wechsel-box")).toBeNull();
    window.prompt = vi.fn(() => "000000");
    netz.antworten["/admin/me/mfa/wechsel"] = new Error("Code ungültig");
    await klick(q(el, "mfa-geraet-wechseln"));
    expect(netz.posts).toHaveLength(1);
    expect(toast.error).toHaveBeenCalledWith("Code ungültig");
    expect(q(el, "mfa-wechsel-box")).toBeNull();
    expect(q(el, "mfa-geraet-wechseln")).toBeTruthy();
  });

  it("Abschalten bleibt — die Rückfrage nennt 'Gerät wechseln' als besseren Weg", async () => {
    const el = await rendern();
    await klick(q(el, "mfa-deaktivieren"));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringMatching(/Gerät wechseln/));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringMatching(/30 Minuten/));
    expect(netz.posts).toEqual([]);
  });

  it("laufender Wechsel ist nach dem Neuladen sichtbar", async () => {
    netz.status = { ...netz.status, wechsel_offen: true, wechsel_bis: new Date(Date.now() + 5 * 60000).toISOString() };
    const el = await rendern();
    expect(q(el, "mfa-wechsel-offen")).toBeTruthy();
    expect(q(el, "mfa-wechsel-offen").textContent).toMatch(/Gerätewechsel ist begonnen/);
    expect(q(el, "mfa-geraet-wechseln")).toBeTruthy();
  });
});
