/*
 * Rollenprüfung 22.09.2026, Welle 2 — Team "vertrag" (Oberfläche).
 *  RP-440/RP-444  Pseudonym/Ansprechpartner als Hinweis unter "Name / Firma"
 *  RP-045/RP-144  Folge-Mail: neuer Schlüssel nur bei geändertem Inhalt nach
 *                 einem Fehlschlag; Fassungsliste meldet die Kürzung
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
const toastMock = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }));
vi.mock("@/lib/api", () => ({ api, errMsg: (e) => e?.message || "Fehler" }));
vi.mock("sonner", () => ({ toast: toastMock }));
vi.mock("@/components/KopierKnopf", () => ({ default: () => null }));

const { verkaeuferNameHinweise } = await import("./ContractDialog");
const {
  default: FolgeMailDialog, schluesselFuerVersuch, versandInhalt,
} = await import("./FolgeMailDialog");

describe("RP-440/RP-444: Verkäufername im Kaufvertrag", () => {
  it("Pseudonym nur als Hinweis, solange das Feld leer ist", () => {
    const v = { seller_name: null, seller_alias: "vnightx" };
    const [h] = verkaeuferNameHinweise(v, "");
    expect(h).toMatch(/Kleinanzeigen-Name: vnightx/);
    expect(h).toMatch(/Pseudonym, bitte den echten Namen/);
    // echter Name eingetragen: kein Hinweis mehr
    expect(verkaeuferNameHinweise(v, "Max Mustermann")).toEqual([]);
    // das Pseudonym selbst eingetippt: Hinweis bleibt
    expect(verkaeuferNameHinweise(v, "VNightX")).toHaveLength(1);
  });
  it("Händler: kein Pseudonym-Hinweis, Ansprechpartner als Info", () => {
    expect(verkaeuferNameHinweise({ seller_name: "Davidoff GmbH", seller_alias: null }, "")).toEqual([]);
    expect(verkaeuferNameHinweise(
      { seller_name: "Autohaus Nord GmbH", seller_ansprechpartner: "Herr Meier" }, "Autohaus Nord GmbH"))
      .toEqual(["Ansprechpartner laut Inserat: Herr Meier"]);
    expect(verkaeuferNameHinweise({}, "")).toEqual([]);
    expect(verkaeuferNameHinweise(null)).toEqual([]);
  });
});

describe("RP-045/RP-144: Schlüssel für den nächsten Versuch", () => {
  const neu = () => "fm-neu";
  it("erster Versuch und unveränderte Wiederholung behalten den Schlüssel", () => {
    const inhalt = versandInhalt("k@x.de", "Betreff", "Text");
    expect(schluesselFuerVersuch("fm-alt", undefined, inhalt, neu)).toBe("fm-alt");
    expect(schluesselFuerVersuch("fm-alt", inhalt, inhalt, neu)).toBe("fm-alt");
    // Leerraum am Empfänger zählt nicht als Änderung
    expect(schluesselFuerVersuch("fm-alt", inhalt, versandInhalt(" k@x.de ", "Betreff", "Text"), neu))
      .toBe("fm-alt");
  });
  it("geänderter Text/Betreff/Empfänger nach einem Fehlschlag = neuer Schlüssel", () => {
    const vorher = versandInhalt("k@x.de", "Betreff", "Text");
    expect(schluesselFuerVersuch("fm-alt", vorher, versandInhalt("k@x.de", "Betreff", "Text 2"), neu)).toBe("fm-neu");
    expect(schluesselFuerVersuch("fm-alt", vorher, versandInhalt("k@x.de", "B2", "Text"), neu)).toBe("fm-neu");
    expect(schluesselFuerVersuch("fm-alt", vorher, versandInhalt("j@x.de", "Betreff", "Text"), neu)).toBe("fm-neu");
  });
});

describe("RP-045/RP-144: Folge-Mail-Dialog nach einem 502", () => {
  let wurzel;
  let behaelter;
  const el = (id) => behaelter.querySelector(`[data-testid="${id}"]`);
  async function warten() {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
  async function textSetzen(wert) {
    const feld = el("folgemail-text");
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
    await act(async () => {
      setter.call(feld, wert);
      feld.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }
  async function senden() {
    await act(async () => { el("folgemail-senden").click(); });
    await warten();
  }

  beforeEach(() => {
    vi.clearAllMocks();
    behaelter = document.createElement("div");
    document.body.appendChild(behaelter);
    api.get.mockResolvedValue({ data: { empfaenger: "kunde@x.de", betreff: "Kauf", text: "Hallo Max" } });
  });
  afterEach(async () => {
    if (wurzel) await act(async () => { wurzel.unmount(); });
    behaelter.remove();
  });

  it("gleicher Text: derselbe Schlüssel; geänderter Text: neuer Schlüssel", async () => {
    wurzel = createRoot(behaelter);
    await act(async () => {
      wurzel.render(createElement(FolgeMailDialog, { open: true, contract: { id: "c1" }, onClose: vi.fn() }));
    });
    await warten();
    api.post.mockRejectedValue(Object.assign(new Error("E-Mail-Versand fehlgeschlagen"),
                                             { response: { status: 502 } }));
    await senden();
    await senden();
    const k1 = api.post.mock.calls[0][1].idempotency_key;
    expect(api.post.mock.calls[1][1].idempotency_key).toBe(k1);
    await textSetzen("Hallo Max, geändert");
    api.post.mockResolvedValue({ data: { status: "ok", zustellung: "versendet" } });
    await senden();
    const k3 = api.post.mock.calls[2][1].idempotency_key;
    expect(k3).not.toBe(k1);
    expect(api.post.mock.calls[2][1].message).toBe("Hallo Max, geändert");
    expect(toastMock.success).toHaveBeenCalledWith("Mail verschickt.");
  });
});
