/*
 * Prüfbericht 20.09.2026, Reparaturwelle A8 (Super-Admin-Oberfläche).
 *  AD-17  Passwort-Dialog des Zwischenhändlers: PasswortFeld + passwortProblem (mind. 10)
 *  AD-21  Fehler-/Audit-Seite: Ladefehler sichtbar mit "Erneut laden", kein leerer Erfolg
 *  AD-19  Nutzerliste: Suche ab 3 Zeichen an den Server (q), "Weitere laden" (page+1)
 *  AD-24  Löschdialog: Umfang je Rolle, Löschvorschau beim Chef schon im Dialog
 *  AD-25  Löschdialog nennt Rolle, Kontonummer, #Kundennummer, E-Mail, Anlagedatum, Konto-ID
 *  AD-27  Altanfrage ohne wanted_plan: kein stilles "monthly", Monat/Jahr ausdrücklich
 *  AD-28  Passwort ändern: neues Token dieses Tabs übernehmen (Einzel-Sitzung bleibt)
 *  AD-29  MFA-Karte: Ladefehler = "Status unbekannt", kein "Einrichten"
 *  R1-42/AD-34  Fahrerliste: busy je Fahrer beim Sperren/Passwort
 *  AD-33  Fahrerliste: Suche ab 3 Zeichen an den Server, "Weitere laden"
 *  DO-22  Betrieb: Kachel "Zahlungen ohne Zugang" weg
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

// Ein steuerbares Netz: je URL-Präfix eine Antwort oder ein Fehler.
const netz = { antworten: {}, fehler: {}, aufrufe: [] };
function antwort(url, config) {
  netz.aufrufe.push({ url, params: config?.params });
  const pfad = url.split("?")[0];
  const treffer = Object.keys(netz.fehler).find((p) => pfad.startsWith(p));
  if (treffer) {
    const e = new Error("weg"); e.response = { status: 502 }; throw e;
  }
  const schluessel = Object.keys(netz.antworten).find((p) => pfad.startsWith(p));
  const a = schluessel ? netz.antworten[schluessel] : { data: [], headers: {} };
  return typeof a === "function" ? a(url, config) : a;
}
async function postStandard(url, body) {
  netz.aufrufe.push({ url, body, methode: "post" });
  return netz.postAntwort || { data: { ok: true } };
}
vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(async (url, config) => antwort(url, config)),
    post: vi.fn(postStandard),
    put: vi.fn(async (url, body) => { netz.aufrufe.push({ url, body, methode: "put" }); return { data: { ok: true } }; }),
    delete: vi.fn(async (url) => { netz.aufrufe.push({ url, methode: "delete" }); return { data: { ok: true } }; }),
  },
  errMsg: (e, f) => f || "Fehler",
}));

const { toast } = await import("sonner");
const { tokenSetzen } = await import("@/lib/sitzung");
const { api } = await import("@/lib/api");
const { default: AdminFreischaltungen, anfragePlan } = await import("./Freischaltungen");
const { default: AdminUsers, rolleText, loeschUmfang, vorschauText, serverSuche } = await import("./Users");
const { default: AdminErrors } = await import("./Errors");
const { default: AdminAuditLog } = await import("./AuditLog");
const { default: AdminSettings } = await import("./Settings");
const { default: AdminBetrieb } = await import("./Betrieb");
const { default: AdminFahrer, serverSuche: fahrerSuche } = await import("./Fahrer");

let root;
let host;
beforeEach(() => {
  netz.antworten = {}; netz.fehler = {}; netz.aufrufe = []; netz.postAntwort = null;
  vi.mocked(toast.success).mockClear(); vi.mocked(toast.error).mockClear();
  vi.mocked(tokenSetzen).mockClear();
  vi.mocked(api.get).mockClear(); vi.mocked(api.delete).mockClear();
  vi.mocked(api.post).mockReset(); vi.mocked(api.post).mockImplementation(postStandard);
});
afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
});

const tick = async (n = 3) => {
  for (let i = 0; i < n; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};
const warten = async (ms) => { await act(async () => { await new Promise((r) => setTimeout(r, ms)); }); };

async function rendern(Komponente) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(h(MemoryRouter, null, h(Komponente))); });
  await tick();
  return host;
}

function tippen(input, wert) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  setter.call(input, wert);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}
const klick = async (el) => { await act(async () => { el.click(); }); await tick(); };
const getAufrufe = (pfad) => netz.aufrufe.filter((a) => !a.methode && a.url.startsWith(pfad));

// ------------------------------------------------------------------ AD-27
describe("AD-27: Altanfrage ohne wanted_plan", () => {
  it("anfragePlan(undefined) ist nicht freischaltbar", () => {
    expect(anfragePlan(undefined)).toEqual({ text: expect.stringMatching(/Wunsch unbekannt/), freischaltbar: false });
    expect(anfragePlan("")).toEqual(expect.objectContaining({ freischaltbar: false }));
    expect(anfragePlan("monthly").freischaltbar).toBe(true);
  });

  it("Zeile: kein 'Ja, freischalten', dafür Monat/Jahr — die Wahl geht mit", async () => {
    netz.antworten["/admin/plan-requests"] = { data: [{
      id: "alt1", type: "sucher_abo", status: "offen", subject_user_id: "s1",
      sucher_name: "Alt", company_name: "F", created_at: "2026-09-22T10:00:00+00:00",
    }], headers: {} };
    const el = await rendern(AdminFreischaltungen);
    expect(el.querySelector('[data-testid="abo-ja-alt1"]')).toBeNull();
    expect(el.querySelector('[data-testid="anfrage-plan-alt1"]').textContent).toMatch(/Wunsch unbekannt/);
    await klick(el.querySelector('[data-testid="abo-ja-jahr-alt1"]'));
    const buchung = netz.aufrufe.find((a) => a.url === "/admin/sucher/s1/abo");
    expect(buchung.body).toEqual({ plan: "yearly", anfrage_id: "alt1" });
  });
});

// ------------------------------------------------------------------ AD-17
describe("AD-17: Passwort-Dialog des Zwischenhändlers", () => {
  it("prüft wie der Server (mind. 10 Zeichen) und nutzt das PasswortFeld", async () => {
    netz.antworten["/admin/buyers"] = { data: [{ id: "k1", company_name: "K1", access: { active: true } }], headers: {} };
    const el = await rendern(AdminFreischaltungen);
    await klick(el.querySelector('[data-testid="buyer-pw-btn-k1"]'));
    const feld = el.querySelector('[data-testid="buyer-pw-input"]');
    expect(feld.type).toBe("password");
    expect(feld.placeholder).toMatch(/mind\. 10 Zeichen/);
    expect(el.querySelector('[data-testid="buyer-pw-input-vorschlag"]')).toBeTruthy();
    await act(async () => { tippen(feld, "kurz1234"); });
    await klick(el.querySelector('[data-testid="buyer-pw-submit"]'));
    expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/mindestens 10 Zeichen/));
    expect(netz.aufrufe.find((a) => a.url === "/admin/users/k1/password")).toBeUndefined();
    await act(async () => { tippen(feld, "Lang-Genug-2026"); });
    await klick(el.querySelector('[data-testid="buyer-pw-submit"]'));
    expect(netz.aufrufe.find((a) => a.url === "/admin/users/k1/password").body).toEqual({ new_password: "Lang-Genug-2026" });
  });
});

// ------------------------------------------------------------------ AD-21
describe("AD-21: Ladefehler auf Fehler- und Audit-Seite", () => {
  it("Fehler-Seite: 'Erneut laden' statt 'Keine offenen Fehler'", async () => {
    netz.fehler["/admin/errors"] = true;
    const el = await rendern(AdminErrors);
    expect(el.querySelector('[data-testid="errors-ladefehler"]')).toBeTruthy();
    expect(el.textContent).not.toMatch(/Keine offenen Fehler/);
    expect(toast.error).toHaveBeenCalled();
    delete netz.fehler["/admin/errors"];
    await klick(el.querySelector('[data-testid="errors-ladefehler"] button'));
    expect(el.querySelector('[data-testid="errors-ladefehler"]')).toBeNull();
    expect(el.textContent).toMatch(/Keine offenen Fehler/);
  });

  it("Audit-Seite: 'Erneut laden' statt 'Keine Einträge'", async () => {
    netz.fehler["/admin/audit"] = true;
    const el = await rendern(AdminAuditLog);
    expect(el.querySelector('[data-testid="audit-ladefehler"]')).toBeTruthy();
    expect(el.textContent).not.toMatch(/Keine Einträge/);
    delete netz.fehler["/admin/audit"];
    await klick(el.querySelector('[data-testid="audit-ladefehler"] button'));
    expect(el.querySelector('[data-testid="audit-ladefehler"]')).toBeNull();
    expect(el.textContent).toMatch(/Keine Einträge/);
  });
});

// ------------------------------------------------------------------ AD-19
const nutzer = (id, extra = {}) => ({
  id, role: "sucher", active: true, kontonummer: `10999-${id}`, created_at: "2026-09-01T10:00:00+00:00", ...extra,
});

describe("AD-19: Nutzerliste sucht ab 3 Zeichen auf dem Server", () => {
  it("serverSuche", () => {
    expect(serverSuche("ab")).toBe("");
    expect(serverSuche("  abc ")).toBe("abc");
    expect(serverSuche(null)).toBe("");
  });

  it("q erst ab 3 Zeichen, entprellt; 'Weitere laden' hängt Seite 2 an", async () => {
    netz.antworten["/admin/users"] = (url, config) => {
      const p = config?.params || {};
      if (p.q) return { data: [nutzer("t1", { first_name: "Treffer" })], headers: {} };
      if (p.page === 2) return { data: [nutzer("b")], headers: {} };
      return { data: [nutzer("a")], headers: { "x-truncated": "1" } };
    };
    const el = await rendern(AdminUsers);
    expect(getAufrufe("/admin/users").at(-1).params).toEqual({ page: 1 });
    expect(el.querySelector('[data-testid="admin-users-gekuerzt"]')).toBeTruthy();
    // Weitere laden: Seite 2 kommt UNTER die schon geladenen
    await klick(el.querySelector('[data-testid="admin-users-mehr"]'));
    expect(getAufrufe("/admin/users").at(-1).params).toEqual({ page: 2 });
    expect(el.querySelector('[data-testid="user-row-a"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="user-row-b"]')).toBeTruthy();
    // zwei Zeichen: nur die Oberfläche filtert, kein Serveraufruf
    const vorher = getAufrufe("/admin/users").length;
    const suche = el.querySelector('[data-testid="admin-users-search"]');
    await act(async () => { tippen(suche, "10"); });
    await warten(350);
    expect(getAufrufe("/admin/users").length).toBe(vorher);
    // drei Zeichen: Server-Suche mit q, Seite 1
    await act(async () => { tippen(suche, "Tre"); });
    expect(getAufrufe("/admin/users").length).toBe(vorher);      // entprellt
    await warten(350);
    expect(getAufrufe("/admin/users").at(-1).params).toEqual({ page: 1, q: "Tre" });
    expect(el.querySelector('[data-testid="user-row-t1"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="user-row-a"]')).toBeNull();
  });
});

// ------------------------------------------------------------------ AD-24 / AD-25
describe("AD-24/AD-25: Löschdialog", () => {
  it("Umfang und Rolle je Konto", () => {
    expect(loeschUmfang({ role: "dealer", ist_chef: true })).toMatch(/KOMPLETTE Firma/);
    expect(loeschUmfang({ role: "sucher" })).toMatch(/nur dieses Konto.*an den Chef/);
    expect(loeschUmfang({ role: "dealer", ist_chef: false })).toMatch(/nur dieses Konto/);
    expect(loeschUmfang({ role: "b2b_buyer" })).toMatch(/Zwischenhändler-Konto/);
    expect(loeschUmfang({ role: "admin" })).toBe("nur dieses Konto.");
    expect(rolleText({ role: "dealer", ist_chef: true })).toMatch(/Hauptaccount/);
    expect(rolleText({ role: "dealer", ist_chef: false })).toMatch(/arbeitet als Sucher/);
    expect(rolleText({ role: "sucher" })).toBe("Sucher");
    expect(rolleText({ role: "b2b_buyer" })).toBe("Zwischenhändler");
    expect(vorschauText({ wuerde_loeschen: { vehicles: 3, contracts: 0, users: 2 } })).toBe("3 × vehicles, 2 × users");
    expect(vorschauText({ wuerde_loeschen: {} })).toBe("keine weiteren Daten");
  });

  it("Chef: Vorschau beim Öffnen, Daten im Dialog, Löschen direkt mit firma_loeschen=true", async () => {
    netz.antworten["/admin/users"] = { data: [nutzer("c1", {
      role: "dealer", ist_chef: true, dealer_id: "d1", company_name: "Firma X", kunden_nr: 10999,
      kontonummer: "10999", email: "chef@x.de",
    })], headers: {} };
    netz.antworten["/admin/dealers/d1/loeschvorschau"] = { data: { wuerde_loeschen: { vehicles: 4, users: 2 } } };
    const el = await rendern(AdminUsers);
    await klick(el.querySelector('[data-testid="user-delete-btn-c1"]'));
    expect(getAufrufe("/admin/dealers/d1/loeschvorschau").length).toBe(1);
    expect(el.querySelector('[data-testid="admin-delete-user-umfang"]').textContent).toMatch(/KOMPLETTE Firma/);
    expect(el.querySelector('[data-testid="admin-delete-user-vorschau"]').textContent).toMatch(/4 × vehicles, 2 × users/);
    const daten = el.querySelector('[data-testid="admin-delete-user-daten"]').textContent;
    expect(daten).toMatch(/Hauptaccount/);
    expect(daten).toMatch(/10999/);
    expect(daten).toMatch(/#10999/);
    expect(daten).toMatch(/chef@x\.de/);
    expect(daten).toMatch(/Erstellt/);
    expect(daten).toMatch(/c1/);
    expect(el.textContent).not.toMatch(/inklusive Händler-Profil/);
    await klick(el.querySelector('[data-testid="admin-delete-user-confirm"]'));
    expect(netz.aufrufe.find((a) => a.methode === "delete").url).toBe("/admin/users/c1?firma_loeschen=true");
  });

  it("Sucher: 'nur dieses Konto', keine Vorschau, Löschen ohne Firmen-Schalter", async () => {
    netz.antworten["/admin/users"] = { data: [nutzer("s1", { dealer_id: "d1", first_name: "Erika" })], headers: {} };
    const el = await rendern(AdminUsers);
    await klick(el.querySelector('[data-testid="user-delete-btn-s1"]'));
    expect(getAufrufe("/admin/dealers/d1/loeschvorschau").length).toBe(0);
    expect(el.querySelector('[data-testid="admin-delete-user-umfang"]').textContent).toMatch(/nur dieses Konto/);
    expect(el.querySelector('[data-testid="admin-delete-user-vorschau"]')).toBeNull();
    await klick(el.querySelector('[data-testid="admin-delete-user-confirm"]'));
    expect(netz.aufrufe.find((a) => a.methode === "delete").url).toBe("/admin/users/s1");
  });
});

// ------------------------------------------------------------------ AD-28 / AD-29
describe("AD-28/AD-29: Einstellungen", () => {
  it("Passwort ändern übernimmt das neue Token dieses Tabs", async () => {
    netz.antworten["/admin/me/mfa"] = { data: { aktiv: true, wiederherstellungscodes_uebrig: 8 } };
    netz.postAntwort = { data: { ok: true, token: "a.b.c" } };
    const el = await rendern(AdminSettings);
    const felder = el.querySelectorAll('input[type="password"]');
    await act(async () => {
      tippen(felder[0], "Altes-Passwort-1"); tippen(felder[1], "Neues-Passwort-2"); tippen(felder[2], "Neues-Passwort-2");
    });
    await act(async () => {
      el.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await tick();
    expect(netz.aufrufe.find((a) => a.url === "/admin/me/password").body)
      .toEqual({ current_password: "Altes-Passwort-1", new_password: "Neues-Passwort-2" });
    expect(tokenSetzen).toHaveBeenCalledWith("ah_token", "a.b.c", { nurSitzung: true });
    expect(toast.success).toHaveBeenCalledWith(expect.stringMatching(/andere Geräte/));
  });

  it("MFA-Ladefehler: 'Status unbekannt' mit Neu laden, kein 'Einrichten'", async () => {
    netz.fehler["/admin/me/mfa"] = true;
    const el = await rendern(AdminSettings);
    expect(el.querySelector('[data-testid="mfa-status-unbekannt"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="mfa-ladefehler"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="mfa-einrichten"]')).toBeNull();
    expect(el.textContent).not.toMatch(/nicht aktiv/);
    delete netz.fehler["/admin/me/mfa"];
    netz.antworten["/admin/me/mfa"] = { data: { aktiv: false } };
    await klick(el.querySelector('[data-testid="mfa-ladefehler"] button'));
    expect(el.querySelector('[data-testid="mfa-ladefehler"]')).toBeNull();
    expect(el.querySelector('[data-testid="mfa-einrichten"]')).toBeTruthy();
  });
});

// ------------------------------------------------------------------ R1-42 / AD-34 / AD-33
describe("Fahrerliste", () => {
  const fahrer = (id, extra = {}) => ({ id, display_name: `Fahrer ${id}`, kontonummer: `FD-${id}`, active: true, ...extra });

  it("R1-42: Sperren nur einmal, Knopf gesperrt bis zum Neuladen", async () => {
    let freigabe;
    const bremse = new Promise((r) => { freigabe = r; });
    netz.antworten["/admin/drivers"] = { data: [fahrer("f1")], headers: {} };
    vi.mocked(api.post).mockImplementation(async (url, body) => {
      netz.aufrufe.push({ url, body, methode: "post" }); await bremse; return { data: { ok: true } };
    });
    const el = await rendern(AdminFahrer);
    const knopf = el.querySelector('[data-testid="fahrer-toggle-active-btn-f1"]');
    await act(async () => { knopf.click(); knopf.click(); });
    await tick();
    expect(netz.aufrufe.filter((a) => a.url === "/admin/drivers/f1/active").length).toBe(1);
    expect(knopf.disabled).toBe(true);
    expect(el.querySelector('[data-testid="fahrer-pw-btn-f1"]').disabled).toBe(true);
    freigabe();
    await tick();
    expect(el.querySelector('[data-testid="fahrer-toggle-active-btn-f1"]').disabled).toBe(false);
  });

  it("AD-34: 'Setzen' im Passwort-Dialog nur einmal", async () => {
    let freigabe;
    const bremse = new Promise((r) => { freigabe = r; });
    netz.antworten["/admin/drivers"] = { data: [fahrer("f2")], headers: {} };
    vi.mocked(api.post).mockImplementation(async (url, body) => {
      netz.aufrufe.push({ url, body, methode: "post" }); await bremse; return { data: { ok: true } };
    });
    const el = await rendern(AdminFahrer);
    await klick(el.querySelector('[data-testid="fahrer-pw-btn-f2"]'));
    await act(async () => { tippen(el.querySelector('[data-testid="fahrer-pw-input"]'), "Neues-Passwort-9"); });
    const setzen = el.querySelector('[data-testid="fahrer-pw-submit"]');
    await act(async () => { setzen.click(); setzen.click(); });
    await tick();
    expect(netz.aufrufe.filter((a) => a.url === "/admin/drivers/f2/password").length).toBe(1);
    expect(setzen.disabled).toBe(true);
    freigabe();
    await tick();
  });

  it("AD-33: Suche ab 3 Zeichen an den Server, 'Weitere laden' mit seite+1", async () => {
    expect(fahrerSuche("ab")).toBe("");
    expect(fahrerSuche("abc")).toBe("abc");
    netz.antworten["/admin/drivers"] = (url, config) => {
      const p = config?.params || {};
      if (p.q) return { data: [fahrer("t", { display_name: "Treffer" })], headers: {} };
      if (p.seite === 2) return { data: [fahrer("b")], headers: {} };
      return { data: [fahrer("a")], headers: { "x-truncated": "1" } };
    };
    const el = await rendern(AdminFahrer);
    expect(getAufrufe("/admin/drivers").at(-1).params).toEqual({ seite: 1 });
    await klick(el.querySelector('[data-testid="fahrer-mehr"]'));
    expect(getAufrufe("/admin/drivers").at(-1).params).toEqual({ seite: 2 });
    expect(el.querySelector('[data-testid="fahrer-row-a"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="fahrer-row-b"]')).toBeTruthy();
    await act(async () => { tippen(el.querySelector('[data-testid="fahrer-suche"]'), "Tre"); });
    await warten(350);
    expect(getAufrufe("/admin/drivers").at(-1).params).toEqual({ seite: 1, q: "Tre" });
    expect(el.querySelector('[data-testid="fahrer-row-t"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="fahrer-row-a"]')).toBeNull();
  });
});

// ------------------------------------------------------------------ DO-22
describe("DO-22: Betrieb ohne 'Zahlungen ohne Zugang'", () => {
  it("Kachel und Hinweis sind weg", async () => {
    netz.antworten["/admin/betrieb"] = { data: {
      alarme: [], alarm_uebersicht: { gesamt: 0, je_typ: [] }, datei_loeschungen_offen: 0,
      abo_vorgaenge_haengend: 0, backup: {}, wartungsmodus: false, alarm_empfaenger: "x@y.de",
    } };
    const el = await rendern(AdminBetrieb);
    expect(el.textContent).not.toMatch(/Zahlungen ohne Zugang/);
    expect(el.textContent).not.toMatch(/Bezahlt-ohne-Zugang/);
    expect(el.textContent).toMatch(/Wartungsmodus/);
  });
});
