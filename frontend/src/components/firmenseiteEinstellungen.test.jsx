/**
 * Firmenseite & Kundenportal — Einstellungs-Baustein (29.09.2026): im Admin (Weg A, adminDealerId)
 * gegen /admin/dealers/<id>/…, beim Chef gegen /dealer/…; Speichern, Vorschlag, „Domain prüfen“
 * mit Schritten und nächstem Schritt; Sucher sieht nur.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const put = vi.fn();
const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), message: vi.fn() };
vi.mock("sonner", () => ({ toast }));
vi.mock("@/lib/api", () => ({
  api: { get: (...a) => get(...a), put: (...a) => put(...a), post: vi.fn(), delete: vi.fn() },
  errMsg: (e, f) => e?.response?.data?.detail || e?.message || f,
  openAuthedFile: vi.fn(),
}));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { default: FirmenseiteEinstellungen } = await import("./FirmenseiteEinstellungen");

const STAND = (extra = {}) => ({
  webseite: { slug: "", aktiv: false, ueber_uns: "", bilder: [], domains: [] },
  url: "", url_pfad: "", firmen_domain: "auto-schnellkauf.de", unterdomain_moeglich: true,
  unterschrift_vorhanden: false, ist_chef: true, firma: "KFZ Müller GmbH", slug_vorschlag: "kfz-mueller-gmbh",
  proxy_hosts: ["*.auto-schnellkauf.de"], bilder_max: 6, domains_max: 5, code_tage: 7, ...extra,
});

let wurzel = null;
let behaelter = null;
async function rendern(props) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(FirmenseiteEinstellungen, props)); });
  return behaelter;
}
const el = (t) => behaelter.querySelector(`[data-testid="${t}"]`);
async function klick(t) { await act(async () => { el(t).click(); }); }
async function tippen(t, wert) {
  const feld = el(t);
  const proto = feld.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(feld, wert);
  await act(async () => { feld.dispatchEvent(new Event("input", { bubbles: true })); });
}
afterEach(() => {
  if (wurzel) act(() => wurzel.unmount());
  behaelter?.remove();
  wurzel = null;
  behaelter = null;
  vi.clearAllMocks();
});

describe("FirmenseiteEinstellungen", () => {
  it("Admin (Weg A): lädt über /admin/dealers/<id>, Vorschlag übernehmen, speichern", async () => {
    let stand = STAND();
    get.mockImplementation(async (url) => {
      if (url === "/admin/dealers/d1/webseite") return { data: stand };
      throw new Error("unerwartet " + url);
    });
    put.mockImplementation(async (url, body) => {
      expect(url).toBe("/admin/dealers/d1/webseite");
      expect(body).toEqual({ slug: "kfz-mueller-gmbh", aktiv: true, ueber_uns: "Wir kaufen Ihr Auto.", domains: ["kfz-mueller.de"],
                             titel: "Ihr Partner in Hannover", untertitel: "", facebook: "", instagram: "https://www.instagram.com/kfz" });
      stand = STAND({ webseite: { slug: "kfz-mueller-gmbh", aktiv: true, ueber_uns: body.ueber_uns, bilder: [], domains: ["kfz-mueller.de"] },
                      url: "https://kfz-mueller-gmbh.auto-schnellkauf.de" });
      return { data: stand };
    });
    await rendern({ adminDealerId: "d1" });
    expect(behaelter.textContent).toContain("Firmenseite & Kundenportal — KFZ Müller GmbH");
    await klick("firmenseite-vorschlag");
    expect(el("firmenseite-slug").value).toBe("kfz-mueller-gmbh");
    await klick("firmenseite-aktiv");
    await tippen("firmenseite-ueber-uns", "Wir kaufen Ihr Auto.");
    await tippen("firmenseite-domains", "kfz-mueller.de\n");
    await tippen("firmenseite-titel", "Ihr Partner in Hannover");
    await tippen("firmenseite-instagram", "https://www.instagram.com/kfz");
    await klick("firmenseite-speichern");
    expect(toast.success).toHaveBeenCalledWith("Firmenseite gespeichert");
    expect(el("firmenseite-url").textContent).toContain("https://kfz-mueller-gmbh.auto-schnellkauf.de");
    expect(el("domain-kfz-mueller.de")).toBeTruthy();
  });

  it("Domain prüfen zeigt die Schritte und den nächsten Schritt", async () => {
    get.mockImplementation(async (url, cfg) => {
      if (url === "/admin/dealers/d1/webseite") return { data: STAND({ webseite: { slug: "kfz", aktiv: true, ueber_uns: "", bilder: [], domains: ["kfz-mueller.de"] }, url: "https://kfz.auto-schnellkauf.de" }) };
      if (url === "/admin/dealers/d1/webseite/domain-pruefung") {
        expect(cfg.params).toEqual({ domain: "kfz-mueller.de" });
        return { data: { domain: "kfz-mueller.de", ok: false, url: "https://kfz-mueller.de",
                         schritte: [{ schritt: "dns", ok: true, text: "Domain zeigt auf 104.21.6.253" }, { schritt: "proxy", ok: false, text: "Proxy kennt die Domain noch nicht" }],
                         naechster_schritt: "Proxy: auf beiden Servern sh deploy/env_setzen.sh 'FIRMEN_HOSTS=…' und den Proxy neu erzeugen." } };
      }
      throw new Error("unerwartet " + url);
    });
    await rendern({ adminDealerId: "d1" });
    await klick("domain-pruefen-kfz-mueller.de");
    const erg = el("domain-ergebnis-kfz-mueller.de").textContent;
    expect(erg).toContain("Domain zeigt auf 104.21.6.253");
    expect(erg).toContain("Proxy kennt die Domain noch nicht");
    expect(erg).toContain("Nächster Schritt: Proxy: auf beiden Servern");
  });

  it("Sucher: alles nur lesen, kein Speichern-Knopf", async () => {
    get.mockResolvedValue({ data: STAND({ ist_chef: false, webseite: { slug: "kfz", aktiv: true, ueber_uns: "x", bilder: [], domains: [] } }) });
    await rendern({});
    expect(get).toHaveBeenCalledWith("/dealer/webseite");
    expect(el("firmenseite-slug").disabled).toBe(true);
    expect(el("firmenseite-speichern")).toBeNull();
    expect(el("firmenseite-nur-chef").textContent).toContain("pflegt der Chef");
  });
});
