/*
 * Wunsch Ahmad 24.11.2026 — Fahrzeugliste (Bestand) und neue Fahrzeugakte.
 *  Liste: Zeitraum-Parameter, Summe über alle Treffer, Häkchen, "Seite
 *         auswählen", "Alle Treffer auswählen", Sammeländerung nur nach
 *         Bestätigung, Ergebnis mit übersprungenen Fahrzeugen.
 *  Akte:  Kopfleiste, Übersicht-Kacheln, Reiter im URL-Hash.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(), message: vi.fn() } }));
const rolle = { aktuell: "dealer" };
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "chef1", role: rolle.aktuell } }) }));
vi.mock("@/components/BeweisCard", () => ({ default: () => null }));
vi.mock("@/components/AbholFoto", () => ({ default: () => null }));
vi.mock("@/components/AbholberichtDialog", () => ({ default: () => null, fotosBis: () => null }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const { api } = await import("@/lib/api");
const { featuresSetzen } = await import("@/lib/features");
const L = await import("@/lib/fahrzeugListe");
const { reiterAusHash } = await import("./akte/AkteReiter");
const { default: Bestand } = await import("./Bestand");
const { default: FahrzeugAkte } = await import("./FahrzeugAkte");

let wurzel = null;
let behaelter = null;
async function rendern(el) {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(el); });
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
  return behaelter;
}
afterEach(() => {
  if (wurzel) act(() => wurzel.unmount());
  behaelter?.remove();
  wurzel = null;
  behaelter = null;
  rolle.aktuell = "dealer";
  vi.restoreAllMocks();
  featuresSetzen(null);
});

const q = (el, id) => el.querySelector(`[data-testid="${id}"]`);
async function klick(knoten) {
  await act(async () => { knoten.click(); });
  await act(async () => { await Promise.resolve(); });
}
async function waehle(select, wert) {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set;
  await act(async () => {
    setter.call(select, wert);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

describe("lib/fahrzeugListe", () => {
  it("Parameter: Zeitraum nur mit Datum beim eigenen Zeitraum, Seiten", () => {
    expect(L.listenParameter({ filter: "bestand", zeitraum: "letzte_woche", seite: 2, jeSeite: 48 }))
      .toBe("lifecycle=bestand&zeitraum=letzte_woche&seite=2&je_seite=48");
    expect(L.listenParameter({ zeitraum: "eigener" })).toBe("");
    expect(L.listenParameter({ zeitraum: "eigener", von: "2026-11-01", bis: "2026-11-30" }))
      .toBe("zeitraum=eigener&von=2026-11-01&bis=2026-11-30");
    expect(L.listenParameter({ zeitraum: "heute", von: "2026-11-01" })).toBe("zeitraum=heute");
    expect(L.zeitraumFehler("eigener", "2026-11-30", "2026-11-01")).toContain("vor");
    expect(L.zeitraumFehler("heute", "", "")).toBeNull();
  });
  it("Auswahl: umschalten, Seite an/ab, ganz gewählt", () => {
    let a = L.auswahlUmschalten(new Set(), "v1");
    expect([...a]).toEqual(["v1"]);
    a = L.seiteAuswaehlen(a, ["v1", "v2"], true);
    expect(L.seiteGanzGewaehlt(a, ["v1", "v2"])).toBe(true);
    a = L.seiteAuswaehlen(a, ["v2"], false);
    expect([...a]).toEqual(["v1"]);
    expect(L.seiteGanzGewaehlt(new Set(), [])).toBe(false);
    expect(L.sammelFrage(3, "archiviert")).toBe("3 Fahrzeuge auf „Archiviert“ setzen?");
    expect(L.sammelErgebnisText({ geaendert: [{}], uebersprungen: [{}, {}] }))
      .toBe("1 Fahrzeug geändert, 2 übersprungen");
  });
});

function fz(id, lc = "bestand") {
  return { id, lifecycle: lc, source: "plattform", gekauft_am: "2026-11-02T09:00:00+00:00",
    data: { make_label: "VW", model_label: `Golf ${id}` } };
}

async function zeigeBestand({ items, gesamt = items.length, zusammenfassung } = {}) {
  featuresSetzen({ marktplatz: false });
  const get = vi.spyOn(api, "get").mockImplementation(async (url) => {
    if (String(url).startsWith("/bestand/ids")) return { data: { ids: ["v1", "v2", "v3"], gesamt: 3, gekuerzt: false } };
    if (String(url).startsWith("/bestand")) {
      return { data: { items, counts: {}, gesamt, gekuerzt: false, je_seite: 48, seite: 1, zusammenfassung } };
    }
    return { data: [] };
  });
  const el = await rendern(createElement(MemoryRouter, null, createElement(Bestand)));
  return { el, get };
}

describe("Bestand: Summe, Zeitraum, Auswahl, Sammeländerung", () => {
  it("zeigt Anzahl und Einkauf gesamt aus der Server-Zusammenfassung", async () => {
    const { el } = await zeigeBestand({ items: [fz("v1")], gesamt: 7,
      zusammenfassung: { anzahl: 7, einkauf_gesamt: 123456.5, ohne_preis: 2 } });
    expect(q(el, "bestand-anzahl").textContent).toBe("7");
    expect(q(el, "bestand-einkauf-gesamt").textContent).toContain("123.456,5");
    expect(q(el, "bestand-ohne-preis").textContent).toContain("2 ohne Preis");
    expect(q(el, "bestand-gekauft-v1").textContent).toContain("02.11.2026");
  });

  it("ohne Betrag vom Server: kein Einkauf gesamt", async () => {
    const { el } = await zeigeBestand({ items: [fz("v1")], zusammenfassung: { anzahl: 1 } });
    expect(q(el, "bestand-anzahl").textContent).toBe("1");
    expect(q(el, "bestand-einkauf-gesamt")).toBeNull();
  });

  it("Zeitraum-Wahl geht als Parameter an den Server", async () => {
    const { el, get } = await zeigeBestand({ items: [fz("v1")], zusammenfassung: { anzahl: 1 } });
    await waehle(q(el, "bestand-zeitraum-wahl"), "letzter_monat");
    await act(async () => { await Promise.resolve(); });
    expect(get.mock.calls.map((c) => c[0]).some((u) => u.includes("zeitraum=letzter_monat"))).toBe(true);
  });

  it("Häkchen, Seite auswählen, Alle Treffer — dann erst nach Bestätigung ändern", async () => {
    const { el } = await zeigeBestand({ items: [fz("v1"), fz("v2", "abgeholt")], gesamt: 3,
      zusammenfassung: { anzahl: 3 } });
    const post = vi.spyOn(api, "post").mockResolvedValue({ data: {
      geaendert: [{ id: "v1", von: "bestand", nach: "archiviert" }],
      uebersprungen: [{ id: "v2", grund: "Von „Abgeholt“ nach „Archiviert“ ist nicht erlaubt" },
        { id: "v3", grund: "Fahrzeug nicht gefunden (andere Firma oder gelöscht)" }] } });

    await klick(q(el, "bestand-waehlen-v1"));
    expect(q(el, "bestand-auswahl-anzahl").textContent).toBe("1 ausgewählt");
    await klick(q(el, "bestand-seite-auswaehlen"));
    expect(q(el, "bestand-auswahl-anzahl").textContent).toBe("2 ausgewählt");
    await klick(q(el, "bestand-alle-treffer"));
    expect(q(el, "bestand-auswahl-anzahl").textContent).toBe("3 ausgewählt");

    expect(q(el, "bestand-sammel-start").disabled).toBe(true);
    await waehle(q(el, "bestand-ziel"), "archiviert");
    await klick(q(el, "bestand-sammel-start"));
    expect(q(el, "bestand-sammel-dialog").textContent).toContain("3 Fahrzeuge auf „Archiviert“ setzen?");
    expect(q(el, "bestand-sammel-hinweis").textContent).toContain("nicht rückgängig");
    // Abbrechen: nichts passiert
    await klick(q(el, "bestand-sammel-abbrechen"));
    expect(q(el, "bestand-sammel-dialog")).toBeNull();
    expect(post).not.toHaveBeenCalled();

    await klick(q(el, "bestand-sammel-start"));
    await klick(q(el, "bestand-sammel-ja"));
    expect(post).toHaveBeenCalledWith("/bestand/status", { ids: ["v1", "v2", "v3"], ziel: "archiviert" });
    expect(q(el, "bestand-sammel-ergebnis").textContent).toContain("1 Fahrzeug geändert, 2 übersprungen");
    expect(q(el, "bestand-sammel-skip-v2").textContent).toContain("Golf v2");
    expect(q(el, "bestand-sammel-skip-v2").textContent).toContain("nicht erlaubt");
    await klick(q(el, "bestand-sammel-fertig"));
    expect(q(el, "bestand-auswahl-anzahl")).toBeNull();
  });

  it("Sucher: keine Häkchen und keine Sammeländerung", async () => {
    rolle.aktuell = "sucher";
    const { el } = await zeigeBestand({ items: [fz("v1")], zusammenfassung: { anzahl: 1, einkauf_gesamt: 10 } });
    expect(q(el, "bestand-waehlen-v1")).toBeNull();
    expect(q(el, "bestand-auswahl-leiste")).toBeNull();
  });
});

// ------------------------------------------------------------------ Akte
function akte() {
  return {
    vehicle: { id: "v1", dealer_id: "d1", lifecycle: "bestand", source: "plattform", known_defects: [],
      purchase_price: 15900, data: { make_label: "BMW", model_label: "320d", mileage: 100000,
        images: ["https://bild/1.jpg"], first_registration: "03/2019" }, bestand: {} },
    protokoll_befund: null, einkaufspreis: {}, kaufvorgaenge: [], kaufvorgaenge_gesamt: 0,
    owner: { id: "chef1", name: "Chef", hauptaccount: true }, mitbearbeiter: [],
    retention_days_left: 20, contracts: [{ id: "c1", contract_no: "K-7", version: 2, created_at: "2026-11-01T10:00:00Z",
      send_status: [{ channel: "email", sent_at: "2026-11-01T11:00:00Z" }], kunde_unterschrieben_am: "2026-11-02T08:00:00Z" }],
    contracts_gesamt: 1, appointments: [{ id: "t1", pickup_date: "2026-11-05", pickup_time: "10:00", status: "offen",
      driver_name: "Fahrer Fritz" }], appointments_gesamt: 1, pickup_report: null, pickup_reports: [],
    pickup_reports_gesamt: 0, comparisons: [], comparisons_gesamt: 0, listings: [], protocols: [],
    protocols_gesamt: 0, history: [{ id: "h1", action: "fahrzeug.archiviert.hand", created_at: "2026-11-03T10:00:00Z" }],
    history_gekuerzt: false, fahrerfoto_tage: 30,
  };
}

let ortMerker = null;
function Ort() {
  ortMerker = useLocation();
  return null;
}

async function zeigeAkte(hash = "") {
  featuresSetzen({ marktplatz: false });
  vi.spyOn(api, "get").mockImplementation(async (url) => {
    if (String(url).startsWith("/vehicles/v1/akte")) return { data: akte() };
    return { data: {} };
  });
  return rendern(createElement(MemoryRouter, { initialEntries: [`/app/akte/v1${hash}`] },
    createElement(Routes, null,
      createElement(Route, { path: "/app/akte/:id", element: createElement("div", null,
        createElement(FahrzeugAkte), createElement(Ort)) }))));
}

describe("Fahrzeugakte: Kopfleiste, Übersicht, Reiter", () => {
  it("Kopfleiste mit Foto, Preis, Status; Kacheln mit Vertrag, Bestätigung und Fahrer", async () => {
    const el = await zeigeAkte();
    expect(q(el, "akte-foto").getAttribute("src")).toBe("https://bild/1.jpg");
    expect(q(el, "akte-kopf-preis").textContent).toContain("15.900");
    expect(q(el, "akte-status").textContent).toContain("Im Bestand");
    expect(q(el, "akte-frist-verlaengern")).not.toBeNull();
    expect(q(el, "akte-kopf-vertrag")).not.toBeNull();
    for (const k of ["fahrzeug", "kauf", "inserat", "abholung", "vertrag"]) {
      expect(q(el, `akte-kachel-${k}`)).not.toBeNull();
    }
    expect(q(el, "akte-kachel-fahrer").textContent).toContain("Fahrer Fritz");
    expect(q(el, "akte-kachel-versand").textContent).toContain("per E-Mail");
    expect(q(el, "akte-kachel-bestaetigt").textContent).toContain("unterschrieben");
    expect(q(el, "akte-mehr").textContent).toContain("Verträge bearbeiten / senden");
  });

  it("Reiter: Übersicht vorne, Wechsel schreibt den Hash, Abschnitte nur im eigenen Reiter", async () => {
    const el = await zeigeAkte();
    const historie = [...el.querySelectorAll('[data-reiter="notizen"]')]
      .find((s) => s.textContent.includes("Historie"));
    expect(historie.hidden).toBe(true);
    expect(q(el, "akte-uebersicht").parentElement.hidden).toBe(false);
    await klick(q(el, "akte-reiter-notizen"));
    expect(ortMerker.hash).toBe("#notizen");
    expect(historie.hidden).toBe(false);
    expect(historie.textContent).toContain("Fahrzeug von Hand archiviert");
    expect(q(el, "akte-uebersicht").parentElement.hidden).toBe(true);
    expect(q(el, "akte-reiter-notizen").getAttribute("aria-selected")).toBe("true");
  });

  it("Hash beim Öffnen wählt den Reiter; Kachel öffnet ihren Reiter", async () => {
    const el = await zeigeAkte("#abholung");
    expect(q(el, "akte-termine").hidden).toBe(false);
    await klick(q(el, "akte-reiter-uebersicht"));
    expect(ortMerker.hash).toBe("");
    await klick(q(el, "akte-kachel-vertrag"));
    expect(ortMerker.hash).toBe("#vertrag");
    expect(reiterAusHash("#unsinn")).toBe("uebersicht");
  });
});
