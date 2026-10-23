/**
 * Entscheidungen Ahmad 26.09.2026 — Freigabe-Seite:
 *  (3) Dialog „Rückfrage an den Fahrer“: Frage (Pflicht, max. 300), Bezug
 *      (allgemein / neuer Schaden / KI-Position), Antwortart Optionen (Chips,
 *      Vorschlag Ja/Nein/Unklar) oder Freitext; POST-Körper an
 *      /protocols/{id}/freigabe mit FreigabeIn.rueckfrage_frage; danach
 *      „Rückfrage gestellt am … — wartet auf Fahrer“ mit Bezug und Verlauf.
 *  (2) Hinweis „Schlüsselanzahl im Vertrag nicht hinterlegt — bitte nachtragen“.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }));
vi.mock("@/lib/api", () => ({ api, errMsg: (e, s) => e?.message || s }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", role: "dealer" } }) }));
vi.mock("@/lib/freigaben", () => ({ freigabeZaehlerAktualisieren: vi.fn() }));
vi.mock("@/lib/ungespeichert", () => ({ useUngespeichert: () => {} }));
// Die KI-Karte meldet ihre Positionen dem Rahmen (onErgebnis) — hier als Attrappe.
vi.mock("@/components/KiBewertungKarte", async () => {
  const React = await import("react");
  function KiAttrappe({ onErgebnis }) {
    React.useEffect(() => {
      onErgebnis?.({ items: [{ source_id: "dev:mileage", title: "Kilometer weichen ab" }] });
    }, [onErgebnis]);
    return React.createElement("div", { "data-testid": "ki-attrappe" });
  }
  return { default: KiAttrappe };
});

const { default: Freigaben, rueckfrageBezuege } = await import("./Freigaben");
const { rueckfrageKoerper, OPTIONEN_VORSCHLAG } = await import("@/components/RueckfrageDialog");

const EINTRAG = {
  protocol_id: "p1", appointment_id: "t1", vehicle_id: "v1", status: "zur_freigabe", stand: "s1",
  fahrzeug: "BMW 320d", abholung: "27.09.2026 10:00", abholort: "Teststr. 1", verkaeufer: "Vera", fahrer: "Fritz",
  abgeschickt_am: "2026-09-26T10:00:00Z", kilometerstand: "", vergleich: [], abweichungen: [],
  neue_schaeden: [{ id: "s1", type_label: "Delle", zone: "Tür vorne links" }],
  schluessel: "1", schluessel_vereinbart: "", schluessel_vereinbart_fehlt: true,
  preis_vertrag: 10000, rueckfrage_antworten: [], rueckfrage_verlauf: [],
};
const OFFEN = {
  protocol_id: "p1", appointment_id: "t1", status: "entwurf", stand: "s2", fahrzeug: "BMW 320d",
  abholung: "27.09.2026 10:00", abholort: "Teststr. 1", fahrer: "Fritz", rueckfrage: "",
  rueckfrage_am: "2026-09-26T12:30:00Z", rueckfrage_von_name: "Chef",
  rueckfrage_frage: { frage_id: "f1", source_id: "s1", source_label: "Schaden: Delle · Tür vorne links",
                      question: "Geht die Delle bis aufs Blech?", options: ["Ja", "Nein", "Teilweise"], freitext: false },
  rueckfrage_antworten: [], rueckfrage_verlauf: [{ frage: { frage_id: "f0", question: "Lack?", source_label: "" },
                                                   antworten: [{ answer: "nein" }] }],
  neue_schaeden: [{ id: "s1", bezeichnung: "Delle · Tür vorne links" }],
};

let liste;
let offen;
let wurzel;
let behaelter;
const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
async function warten() {
  for (let i = 0; i < 3; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
async function starten() {
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
  await act(async () => { wurzel.render(createElement(Freigaben)); });
  await warten();
}
async function klick(id) {
  const k = el(id);
  if (!k) throw new Error(`nicht gefunden: ${id}`);
  await act(async () => { k.click(); });
  await warten();
}
async function tippen(id, wert, proto = window.HTMLInputElement.prototype, ereignis = "input") {
  const feld = el(id);
  if (!feld) throw new Error(`nicht gefunden: ${id}`);
  const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
  await act(async () => {
    setter.call(feld, wert);
    feld.dispatchEvent(new Event(ereignis, { bubbles: true }));
  });
}
const frageTippen = (t) => tippen("rueckfrage-frage", t, window.HTMLTextAreaElement.prototype);
const bezugWaehlen = (v) => tippen("rueckfrage-bezug", v, window.HTMLSelectElement.prototype, "change");

beforeEach(() => {
  liste = [EINTRAG];
  offen = [];
  api.get.mockImplementation((url) => (url.includes("rueckfragen-offen")
    ? Promise.resolve({ data: offen })
    : Promise.resolve({ data: liste, headers: {} })));
  api.post.mockResolvedValue({ data: { ok: true, status: "entwurf" } });
  try { window.localStorage.clear(); } catch { /* egal */ }
});
afterEach(async () => {
  if (wurzel) await act(async () => { wurzel.unmount(); });
  wurzel = null; behaelter?.remove(); vi.clearAllMocks();
});

describe("rueckfrageKoerper / rueckfrageBezuege", () => {
  it("baut den Körper für FreigabeIn.rueckfrage_frage und meldet Fehler", () => {
    expect(rueckfrageKoerper({ frage: " ", bezug: "", antwortart: "optionen", optionen: ["Ja", "Nein"] }).fehler).toMatch(/Frage/);
    expect(rueckfrageKoerper({ frage: "x".repeat(301), antwortart: "freitext" }).fehler).toMatch(/300/);
    expect(rueckfrageKoerper({ frage: "Lack?", antwortart: "optionen", optionen: ["Ja", " Ja ", ""] }).fehler).toMatch(/mindestens zwei/);
    expect(rueckfrageKoerper({ frage: "Lack?", antwortart: "optionen", optionen: ["1", "2", "3", "4", "5", "6", "7"] }).fehler).toMatch(/Höchstens 6/);
    expect(rueckfrageKoerper({ frage: " Lack? ", bezug: "s1", antwortart: "optionen", optionen: ["Ja", "Nein", "Ja"] }).koerper)
      .toEqual({ source_id: "s1", question: "Lack?", options: ["Ja", "Nein"], freitext: false });
    // Freitext: Optionen werden nicht mitgeschickt
    expect(rueckfrageKoerper({ frage: "Was genau?", antwortart: "freitext", optionen: ["Ja"] }).koerper)
      .toEqual({ source_id: "", question: "Was genau?", options: [], freitext: true });
    expect(OPTIONEN_VORSCHLAG).toEqual(["Ja", "Nein", "Unklar"]);
  });
  it("Bezüge: neue Schäden und KI-Positionen, ohne Doppel", () => {
    expect(rueckfrageBezuege(EINTRAG.neue_schaeden, { items: [{ source_id: "dev:mileage", title: "Kilometer weichen ab" },
                                                                { source_id: "s1", title: "Delle" }] }))
      .toEqual([{ id: "s1", label: "Schaden: Delle · Tür vorne links" },
                { id: "dev:mileage", label: "KI-Position: Kilometer weichen ab" }]);
    expect(rueckfrageBezuege([], null)).toEqual([]);
  });
});

describe("Freigaben: Rückfrage an den Fahrer (Entscheidung 3)", () => {
  it("Optionen-Pfad: Frage, Bezug Schaden, Chips bearbeiten, POST-Körper, danach 'wartet auf Fahrer'", async () => {
    await starten();
    expect(el("freigabe-p1")).toBeTruthy();
    expect(el("rueckfrage-dialog")).toBeNull();
    await klick("freigabe-rueckfrage-p1");
    expect(el("rueckfrage-dialog")).toBeTruthy();
    // Bezug-Auswahl: allgemein, der neue Schaden, die KI-Position
    const optionen = [...el("rueckfrage-bezug").querySelectorAll("option")].map((o) => [o.value, o.textContent]);
    expect(optionen).toEqual([["", "Allgemein (kein bestimmter Punkt)"],
                              ["s1", "Schaden: Delle · Tür vorne links"],
                              ["dev:mileage", "KI-Position: Kilometer weichen ab"]]);
    // Vorschlag Ja/Nein/Unklar als Chips
    expect(el("rueckfrage-chip-Ja") && el("rueckfrage-chip-Nein") && el("rueckfrage-chip-Unklar")).toBeTruthy();
    await frageTippen("Geht die Delle bis aufs Blech?");
    await bezugWaehlen("s1");
    await klick("rueckfrage-chip-entfernen-Unklar");
    expect(el("rueckfrage-chip-Unklar")).toBeNull();
    await tippen("rueckfrage-option-neu", "Teilweise");
    await klick("rueckfrage-option-hinzu");
    expect(el("rueckfrage-chip-Teilweise")).toBeTruthy();
    // Senden: bestehende Route, strukturierter Körper (Nr. 125: >= 2 Optionen)
    offen = [OFFEN];
    liste = [];
    await klick("rueckfrage-senden");
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][0]).toBe("/protocols/p1/freigabe");
    expect(api.post.mock.calls[0][1]).toEqual({
      zurueck: true, stand: "s1",
      rueckfrage_frage: { source_id: "s1", question: "Geht die Delle bis aufs Blech?",
                          options: ["Ja", "Nein", "Teilweise"], freitext: false },
    });
    expect(toastMock.success).toHaveBeenCalled();
    // Dialog zu, Protokoll liegt jetzt beim Fahrer
    expect(el("rueckfrage-dialog")).toBeNull();
    expect(el("freigabe-p1")).toBeNull();
    expect(el("freigaben-rueckfragen-titel").textContent).toBe("Rückfrage beim Fahrer (1)");
    expect(el("rueckfrage-offen-status-p1").textContent).toMatch(/Rückfrage gestellt am .*— wartet auf Fahrer/);
    const frage = el("rueckfrage-offen-frage-p1").textContent;
    expect(frage).toContain("Geht die Delle bis aufs Blech?");
    expect(frage).toContain("Bezug: Schaden: Delle · Tür vorne links");
    expect(frage).toContain("Antwortmöglichkeiten: Ja · Nein · Teilweise");
    expect(el("rueckfrage-offen-antwort-p1")).toBeNull();
    // Verlauf früherer Runden aufklappbar
    expect(el("rueckfrage-offen-verlauf-p1").textContent).toContain("Frühere Rückfragen (1)");
    expect(el("rueckfrage-offen-verlauf-p1").textContent).toContain("nein");
    expect(el("freigaben-leer")).toBeNull();
  });

  it("Freitext-Pfad mit KI-Position als Bezug; gespeicherte Antwort des Fahrers sichtbar", async () => {
    await starten();
    await klick("freigabe-rueckfrage-p1");
    await klick("rueckfrage-art-freitext");
    expect(el("rueckfrage-optionen")).toBeNull();
    expect(el("rueckfrage-freitext-hinweis")).toBeTruthy();
    await frageTippen("Was genau steht auf dem Tacho?");
    await bezugWaehlen("dev:mileage");
    offen = [{ ...OFFEN, rueckfrage: "Bitte genau ablesen",
               rueckfrage_frage: { frage_id: "f2", source_id: "dev:mileage", source_label: "KI-Position: Kilometer weichen ab",
                                   question: "Was genau steht auf dem Tacho?", options: [], freitext: true },
               rueckfrage_antworten: [{ frage_id: "f2", answer: "123.456 km" }] }];
    liste = [];
    await klick("rueckfrage-senden");
    expect(api.post.mock.calls[0][1]).toEqual({
      zurueck: true, stand: "s1",
      rueckfrage_frage: { source_id: "dev:mileage", question: "Was genau steht auf dem Tacho?", options: [], freitext: true },
    });
    const frage = el("rueckfrage-offen-frage-p1").textContent;
    expect(frage).toContain("Bezug: KI-Position: Kilometer weichen ab");
    expect(frage).toContain("Antwort als Freitext");
    expect(el("rueckfrage-offen-antwort-p1").textContent).toContain("123.456 km");
    expect(el("rueckfrage-offen-p1").textContent).toContain("Hinweis an den Fahrer: Bitte genau ablesen");
  });

  it("prüft die Eingaben vor dem Senden und bleibt bei einem Serverfehler offen", async () => {
    await starten();
    await klick("freigabe-rueckfrage-p1");
    await klick("rueckfrage-senden");
    expect(toastMock.error).toHaveBeenCalledWith(expect.stringContaining("Frage"));
    expect(api.post).not.toHaveBeenCalled();
    await frageTippen("Lack?");
    await klick("rueckfrage-chip-entfernen-Unklar");
    await klick("rueckfrage-chip-entfernen-Nein");
    await klick("rueckfrage-senden");
    expect(toastMock.error).toHaveBeenLastCalledWith(expect.stringContaining("mindestens zwei"));
    expect(api.post).not.toHaveBeenCalled();
    // Server lehnt ab (z. B. Nr. 126: Bezug gibt es nicht) -> Dialog bleibt offen
    await tippen("rueckfrage-option-neu", "Nein");
    await klick("rueckfrage-option-hinzu");
    api.post.mockRejectedValueOnce(new Error("Die Rückfrage verweist auf eine Position, die es nicht gibt."));
    await klick("rueckfrage-senden");
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(toastMock.error).toHaveBeenLastCalledWith(expect.stringContaining("Position"));
    expect(el("rueckfrage-dialog")).toBeTruthy();
    await klick("rueckfrage-abbrechen");
    expect(el("rueckfrage-dialog")).toBeNull();
  });
});

describe("Freigaben: Schlüsselanzahl fehlt im Vertrag (Entscheidung 2)", () => {
  it("zeigt den Hinweis zum Nachtragen — und nicht, wenn der Wert da ist", async () => {
    await starten();
    expect(el("freigabe-schluessel-hinweis-p1").textContent).toBe("Schlüsselanzahl im Vertrag nicht hinterlegt — bitte nachtragen.");
    await act(async () => { wurzel.unmount(); });
    wurzel = null; behaelter.remove();
    liste = [{ ...EINTRAG, schluessel_vereinbart: "2", schluessel_vereinbart_fehlt: false }];
    await starten();
    expect(el("freigabe-schluessel-hinweis-p1")).toBeNull();
    expect(el("freigabe-p1").textContent).toContain("1 (vereinbart 2)");
  });
});
