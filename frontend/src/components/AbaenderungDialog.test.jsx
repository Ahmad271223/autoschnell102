/*
 * Abänderungsvertrag (Wunsch Ahmad 24.11.2026): Erstellen verschickt nichts, Senden nur per Knopf,
 * „schon verschickt“ (409) nur nach Rückfrage mit erneut=true. Menüeintrag im Vertragsarchiv,
 * Nachlackierung in der Freigabe.
 */
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const post = vi.fn();
const openAuthedFile = vi.fn();
vi.mock("@/lib/api", () => ({
  api: { get: (...a) => get(...a), post: (...a) => post(...a) },
  errMsg: (e, f) => e?.response?.data?.detail?.msg || e?.message || f,
  openAuthedFile: (...a) => openAuthedFile(...a),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { default: AbaenderungDialog, abaenderungNoetig, sendenMitRueckfrage, STAND } = await import("./AbaenderungDialog");
const { default: VertragAktionen } = await import("./VertragAktionen");
const { default: NachlackierungHinweis } = await import("./NachlackierungHinweis");
const { linkSchluessel } = await import("@/pages/firma/AbaenderungUnterschreiben");

let wurzel;
let behaelter;
const el = (id) => document.querySelector(`[data-testid="${id}"]`);
const klick = async (id) => { await act(async () => { el(id).click(); }); };
const tippen = async (id, wert) => {
  const feld = el(id);
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  await act(async () => { setter.call(feld, wert); feld.dispatchEvent(new Event("input", { bubbles: true })); });
};

const STAND_LEER = { unterschrieben: true, vorschlag: [], vorschlag_zeilen: [], liste: [] };
const VORSCHLAG = {
  unterschrieben: true, quelle: "online", basis_version: 1, version: 2,
  vorschlag: [{ feld: "purchase_price" }], vorschlag_zeilen: ["Kaufpreis: 12.500 € → 11.900 €"], liste: [],
};
const EINTRAG = {
  id: "ab1", status: "offen", erstellt_am: "2026-11-24T10:00:00+00:00",
  zeilen: ["Kaufpreis: 12.500 € → 11.900 €"], versand: [],
};

beforeEach(() => {
  get.mockReset(); post.mockReset(); openAuthedFile.mockReset();
  behaelter = document.createElement("div");
  document.body.appendChild(behaelter);
  wurzel = createRoot(behaelter);
});
afterEach(async () => {
  await act(async () => { wurzel.unmount(); });
  behaelter.remove();
});

async function zeigen(props = {}) {
  await act(async () => {
    wurzel.render(createElement(AbaenderungDialog, {
      open: true, contract: { id: "c1", seller_email: "erika@example.org" }, onClose: vi.fn(), ...props }));
  });
}

describe("sendenMitRueckfrage", () => {
  it("fragt bei 409 bereits_versendet und sendet nur nach Ja mit erneut=true", async () => {
    const fehler = { response: { status: 409, data: { detail: { code: "bereits_versendet", msg: "Schon verschickt." } } } };
    const senden = vi.fn(async (erneut) => { if (!erneut) throw fehler; return { data: { ok: true } }; });
    expect(await sendenMitRueckfrage(senden, () => false)).toBeNull();
    expect(senden).toHaveBeenCalledTimes(1);
    senden.mockClear();
    const frage = vi.fn(() => true);
    expect(await sendenMitRueckfrage(senden, frage)).toEqual({ data: { ok: true } });
    expect(frage).toHaveBeenCalledWith("Schon verschickt.");
    expect(senden.mock.calls.map((c) => c[0])).toEqual([false, true]);
  });

  it("andere Fehler gehen ohne Rückfrage durch", async () => {
    const frage = vi.fn();
    const senden = vi.fn(async () => { throw { response: { status: 409, data: { detail: { code: "laeuft" } } } }; });
    await expect(sendenMitRueckfrage(senden, frage)).rejects.toBeTruthy();
    expect(frage).not.toHaveBeenCalled();
  });
});

describe("abaenderungNoetig", () => {
  it("nur bei unterschriebenem Vertrag mit Änderungen", async () => {
    get.mockResolvedValueOnce({ data: VORSCHLAG });
    expect(await abaenderungNoetig("c1")).toBe(VORSCHLAG);
    get.mockResolvedValueOnce({ data: STAND_LEER });
    expect(await abaenderungNoetig("c1")).toBeNull();
    get.mockResolvedValueOnce({ data: { ...VORSCHLAG, unterschrieben: false } });
    expect(await abaenderungNoetig("c1")).toBeNull();
    get.mockRejectedValueOnce(new Error("netz"));
    expect(await abaenderungNoetig("c1")).toBeNull();
  });
});

describe("AbaenderungDialog", () => {
  it("Vorschlag zeigen, mit Wahl erstellen — Erstellen verschickt nichts", async () => {
    get.mockResolvedValueOnce({ data: VORSCHLAG }).mockResolvedValueOnce({ data: { ...STAND_LEER, liste: [EINTRAG] } });
    post.mockResolvedValueOnce({ data: { id: "ab1", status: "offen" } });
    await zeigen();
    expect(el("abaenderung-vorschlag-zeilen").textContent).toContain("Kaufpreis: 12.500 € → 11.900 €");
    expect(el("abaenderung-wahl-online").checked).toBe(true);
    await klick("abaenderung-wahl-intern");
    await klick("abaenderung-erstellen");
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith("/contracts/c1/abaenderungen", { art: "intern" });
    expect(el("abaenderung-stand-ab1").textContent).toBe("offen");
    expect(el("abaenderung-senden-form-online")).toBeNull();
  });

  it("Online: Link senden mit Adresse und Häkchen, PDF ansehen", async () => {
    get.mockResolvedValue({ data: { ...STAND_LEER, liste: [EINTRAG] } });
    post.mockResolvedValue({ data: { zustellung: "versendet" } });
    await zeigen();
    await klick("abaenderung-pdf-ab1");
    expect(openAuthedFile).toHaveBeenCalledWith("/contracts/c1/abaenderungen/ab1/pdf");
    await klick("abaenderung-link-ab1");
    expect(el("abaenderung-empfaenger").value).toBe("erika@example.org");
    await klick("abaenderung-danach-senden");
    await klick("abaenderung-senden-los");
    expect(post).toHaveBeenCalledWith("/contracts/c1/abaenderungen/ab1/senden",
      { art: "online", recipient: "erika@example.org", erneut: false, nach_unterschrift_senden: true });
  });

  it("Per E-Mail senden: 409 → Rückfrage → erneut", async () => {
    get.mockResolvedValue({ data: { ...STAND_LEER, liste: [{ ...EINTRAG, status: "unterschrieben" }] } });
    post.mockRejectedValueOnce({ response: { status: 409, data: { detail: { code: "bereits_versendet", msg: "Schon verschickt." } } } })
      .mockResolvedValueOnce({ data: { zustellung: "versendet" } });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    await zeigen();
    expect(el("abaenderung-link-ab1")).toBeNull();   // unterschrieben: kein neuer Link
    await klick("abaenderung-mail-ab1");
    await tippen("abaenderung-empfaenger", "neu@example.org");
    await klick("abaenderung-senden-los");
    expect(confirm).toHaveBeenCalled();
    expect(post.mock.calls.map((c) => c[1].erneut)).toEqual([false, true]);
    expect(post.mock.calls[1][1]).toMatchObject({ art: "pdf", recipient: "neu@example.org" });
    confirm.mockRestore();
  });

  it("nicht unterschrieben: Hinweis statt Vorschlag", async () => {
    get.mockResolvedValue({ data: { unterschrieben: false, vorschlag: [], vorschlag_zeilen: [], liste: [] } });
    await zeigen();
    expect(el("abaenderung-nicht-unterschrieben")).not.toBeNull();
    expect(el("abaenderung-erstellen")).toBeNull();
  });

  it("Stand-Texte", () => {
    expect(Object.keys(STAND)).toEqual(["offen", "intern", "gesendet", "unterschrieben", "ersetzt"]);
  });
});

describe("Einbindung", () => {
  it("Menüeintrag Abänderungsverträge nur mit Aktion", async () => {
    const a = { termin: vi.fn(), ansehen: vi.fn(), digital: vi.fn(), senden: vi.fn(), portal: vi.fn(),
      folgeMail: vi.fn(), aendern: vi.fn(), korrektur: vi.fn(), loeschen: vi.fn() };
    await act(async () => { wurzel.render(createElement(VertragAktionen, { it: { id: "c1" }, a })); });
    await klick("pdf-mehr-c1");
    expect(el("abaenderungen-c1")).toBeNull();
    await klick("pdf-mehr-c1");
    a.abaenderungen = vi.fn();
    await act(async () => { wurzel.render(createElement(VertragAktionen, { it: { id: "c1" }, a: { ...a } })); });
    await klick("pdf-mehr-c1");
    await klick("abaenderungen-c1");
    expect(a.abaenderungen).toHaveBeenCalledTimes(1);
  });

  it("Nachlackierung in der Freigabe", async () => {
    await act(async () => { wurzel.render(createElement(NachlackierungHinweis, { befund: null, testid: "n" })); });
    expect(el("n")).toBeNull();
    await act(async () => {
      wurzel.render(createElement(NachlackierungHinweis, {
        befund: { text: "Ja, Tür vorne links (210 µm)", vertrag: "Ja", abweichend: true, schwelle_um: 160 }, testid: "n" }));
    });
    expect(el("n").textContent).toContain("Nachlackierung: Ja, Tür vorne links (210 µm)");
    expect(el("n").textContent).toContain("Abänderungsvertrag");
  });

  it("Link-Schlüssel nur aus dem #-Teil, nur erlaubte Zeichen", () => {
    expect(linkSchluessel("#abcDEF0123456789_-xyzABC")).toBe("abcDEF0123456789_-xyzABC");
    expect(linkSchluessel("#kurz")).toBe("");
    expect(linkSchluessel("#<script>alert(1)</script>aaaaaaaaaaaaaa")).toBe("");
    expect(linkSchluessel("")).toBe("");
  });
});
