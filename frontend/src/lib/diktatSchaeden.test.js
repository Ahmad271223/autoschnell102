import { describe, expect, it } from "vitest";
import { DOTS } from "@/components/DamageSelector";
import { schaedenAusSprache } from "./diktatSchaeden";

const kurz = (text) => schaedenAusSprache(text, DOTS).vorschlaege.map((v) => `${v.type_key}|${v.dot.name}`);

describe("Schäden aus dem Gesagten", () => {
  it("findet Art und Bauteil", () => {
    expect(kurz("Kratzer an der Stoßstange hinten, Delle in der Fahrertür")).toEqual([
      "kratzer|Stoßstange hinten", "delle|Tür vorne links"]);
    expect(kurz("Beule Beifahrertür")).toEqual(["delle|Tür vorne rechts"]);
    expect(kurz("Steinschlag in der Frontscheibe")).toEqual(["steinschlag|Windschutzscheibe"]);
    expect(kurz("Rost am Kotflügel hinten rechts")).toEqual(["rost|Kotflügel hinten rechts"]);
    expect(kurz("Hagelschaden auf dem Dach")).toEqual(["hagelschaden|Dach"]);
    expect(kurz("linker Scheinwerfer defekt")).toEqual(["beleuchtung|Linker Hauptscheinwerfer"]);
    expect(kurz("Nebelscheinwerfer rechts kaputt")).toEqual(["beleuchtung|Rechter Nebelscheinwerfer"]);
    expect(kurz("Kratzer am rechten Außenspiegel")).toEqual(["kratzer|Rechter Außenspiegel"]);
    expect(kurz("Kofferraum verkratzt")).toEqual(["kratzer|Heckklappe"]);
  });
  it("übernimmt die Schadensart für weitere Bauteile", () => {
    expect(kurz("Kratzer an der Motorhaube und am Kotflügel vorne links")).toEqual([
      "kratzer|Motorhaube", "kratzer|Kotflügel vorne links"]);
  });
  it("Unfall repariert / nicht repariert", () => {
    expect(kurz("Unfallschaden Tür hinten links repariert")).toEqual(["unfall_repariert|Tür hinten links"]);
    expect(kurz("Unfall Heckklappe nicht repariert")).toEqual(["unfall_nicht_repariert|Heckklappe"]);
  });
  it("links/rechts widerspricht sich nie", () => {
    const v = schaedenAusSprache("Delle Tür hinten rechts", DOTS).vorschlaege;
    expect(v[0].dot.name).toBe("Tür hinten rechts");
    expect(v[0].unsicher).toBe(false);
  });
  it("markiert Ungenaues als unsicher", () => {
    const v = schaedenAusSprache("Kratzer an der Tür", DOTS).vorschlaege;
    expect(v).toHaveLength(1);
    expect(v[0].unsicher).toBe(true);
  });
  it("lässt Unklares offen statt zu raten", () => {
    const r = schaedenAusSprache("Kratzer hinten links. Riecht nach Rauch", DOTS);
    expect(r.vorschlaege).toEqual([]);
    expect(r.offen).toEqual(["Kratzer hinten links", "Riecht nach Rauch"]);
  });
  it("doppelt Gesagtes nur einmal", () => {
    expect(kurz("Kratzer Motorhaube, nochmal Kratzer Motorhaube")).toEqual(["kratzer|Motorhaube"]);
  });
});
