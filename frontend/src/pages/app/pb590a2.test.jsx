/*
 * Prüfbericht 20.09.2026 — Reparaturwelle A2 (Seiten und Komponenten außerhalb
 * von Verträgen/Terminen). Reine Hilfsfunktionen und Quelltext-Prüfungen:
 *  U-48   PhotoGallery: Index nie hinter dem Listenende
 *  M-16   PhotoGallery: Wisch unterdrückt den Klick auf den Hintergrund
 *  U-68   fotosBis rechnet in vollen 24-h-Schritten wie der Server
 *  K-25   Abhol-Check: Schlüssel/Tankstand zählen als ungespeichert
 *  K-05   Abhol-Check: Längen wie der Server
 *  U-30   Anfragen: unbekannter Status heißt nicht "Abgelehnt"
 *  U-117  PortalSheet: Fokusfalle
 *  U-164  Freigaben: "mindestens N" bei gekürzter Liste
 *  U-171  Freigaben: Preis 0 wird abgefangen
 *  M-17   ManuelleSuche: Pfeiltasten im Auswahlfeld
 *  U-82   Vergleich: "war schon angelegt" statt "PDF erstellt"
 *  V-33/U-169/U-165/V-34  X-Truncated wird gelesen
 *  U-106/U-108/U-118/U-125/U-113/M-18/U-44/U-47/U-12/U-14/U-11/U-119/U-120/
 *  U-136/U-139/M-14/U-124/U-163  Quelltext-Verdrahtung
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  errMsg: (e, f) => e?.message || f,
  openAuthedFile: vi.fn(),
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", role: "dealer" } }) }));
vi.mock("@/context/DriverContext", () => ({ driverApi: { get: vi.fn() }, openDriverPdf: vi.fn() }));
vi.mock("@/lib/clientFetch", () => ({ extensionReady: vi.fn(), fetchViaExtension: vi.fn() }));
vi.mock("@/lib/popup", () => ({ fensterDanebenSetzen: vi.fn(), zweitenBildschirmAnfragen: vi.fn() }));

const { galerieIndex, WISCH_KLICK_SPERRE_MS } = await import("@/components/PhotoGallery");
const { fotosBis } = await import("@/components/AbholberichtDialog");
const { abholCheckUngespeichert, ABWEICHUNG_TEXT_MAX, BEMERKUNG_MAX, SCHLUESSEL_STANDARD, TANK_STANDARD } =
  await import("@/components/AbholCheckDialog");
const { fokusFalle } = await import("@/components/PortalSheet");
const { statusMeta } = await import("./Anfragen");
const { wartendUeberschrift, preisFehler } = await import("./Freigaben");
const { naechsteMarkierung } = await import("./ManuelleSuche");
const { vertragErstelltMeldung, VERGLEICH_LAEUFT_HINWEIS } = await import("./Vergleich");
const { listeGekuerzt } = await import("@/pages/driver/DriverDashboard");

const quelle = async (pfad) => (await import(/* @vite-ignore */ `${pfad}?raw`)).default;

describe("U-48 / M-16: PhotoGallery", () => {
  it("galerieIndex bleibt innerhalb der Liste", () => {
    expect(galerieIndex(0, 3)).toBe(0);
    expect(galerieIndex(2, 3)).toBe(2);
    expect(galerieIndex(7, 3)).toBe(2);   // Liste geschrumpft -> letztes Foto
    expect(galerieIndex(-1, 3)).toBe(0);
    expect(galerieIndex(4, 0)).toBe(0);   // leere Liste
    expect(galerieIndex(undefined, 2)).toBe(0);
  });
  it("Wisch-Sperre ist kurz, aber länger als ein Browser-Klick nach dem Wisch", () => {
    expect(WISCH_KLICK_SPERRE_MS).toBeGreaterThanOrEqual(300);
    expect(WISCH_KLICK_SPERRE_MS).toBeLessThanOrEqual(1000);
  });
  it("Quelltext: Lightbox-Knöpfe benannt, Klick nach Wisch wird ignoriert (U-113)", async () => {
    const q = await quelle("@/components/PhotoGallery.jsx");
    expect(q).toContain('aria-label="Schließen"');
    expect(q).toContain('aria-label="Vorheriges Foto"');
    expect(q).toContain('aria-label="Nächstes Foto"');
    expect(q).toContain("onClick={hintergrundKlick}");
    expect(q).toContain("src={photos[aktuell]}");
  });
});

describe("U-68: fotosBis in vollen 24-Stunden-Schritten", () => {
  it("addiert N × 24 h auf den Zeitstempel (kein setDate in Ortszeit)", () => {
    const d = fotosBis("2026-03-28T23:30:00Z", 3);
    expect(d.getTime()).toBe(Date.parse("2026-03-28T23:30:00Z") + 3 * 86400000);
  });
  it("ohne Datum oder Frist nichts", () => {
    expect(fotosBis(null, 90)).toBeNull();
    expect(fotosBis("2026-01-01T00:00:00Z", 0)).toBeNull();
    expect(fotosBis("kein datum", 90)).toBeNull();
    expect(fotosBis("2026-01-01T00:00:00Z", "abc")).toBeNull();
  });
  it("Anzeige sagt 'ab dem'", async () => {
    expect(await quelle("@/components/AbholberichtDialog.jsx")).toContain("Die Fotos werden ab dem {fmtDatum(bis)}");
    expect(await quelle("./FahrzeugAkte.jsx")).toContain("Fahrerfotos werden ab dem {");
  });
});

describe("K-25 / K-05: Abhol-Check", () => {
  const leer = { mileage: "", keys: SCHLUESSEL_STANDARD, fuel: TANK_STANDARD, notes: "", deviations: [] };
  it("Standardwerte gelten als unberührt", () => {
    expect(abholCheckUngespeichert(leer)).toBe(false);
    expect(abholCheckUngespeichert({ ...leer, notes: "   " })).toBe(false);
  });
  it("Schlüsselanzahl und Tankstand zählen mit", () => {
    expect(abholCheckUngespeichert({ ...leer, keys: "1" })).toBe(true);
    expect(abholCheckUngespeichert({ ...leer, keys: "" })).toBe(true);
    expect(abholCheckUngespeichert({ ...leer, fuel: "voll" })).toBe(true);
  });
  it("wie bisher: km, Notiz, Abweichungen", () => {
    expect(abholCheckUngespeichert({ ...leer, mileage: "85120" })).toBe(true);
    expect(abholCheckUngespeichert({ ...leer, notes: "x" })).toBe(true);
    expect(abholCheckUngespeichert({ ...leer, deviations: [{ id: "a" }] })).toBe(true);
  });
  it("Längen wie routes/drivers.py PickupReportIn (200 / 5000)", async () => {
    expect(ABWEICHUNG_TEXT_MAX).toBe(200);
    expect(BEMERKUNG_MAX).toBe(5000);
    const q = await quelle("@/components/AbholCheckDialog.jsx");
    expect(q.match(/maxLength=\{ABWEICHUNG_TEXT_MAX\}/g)).toHaveLength(3);
    expect(q).toContain("maxLength={BEMERKUNG_MAX}");
    expect(q).toContain("useUngespeichert(ungespeichert);");
  });
});

describe("U-30: Anfragen-Status", () => {
  it("bekannte Status unverändert, unbekannte neutral mit Rohwert", () => {
    expect(statusMeta("offen").label).toBe("Offen");
    expect(statusMeta("abgelehnt").label).toBe("Abgelehnt");
    expect(statusMeta("neuer_status").label).toBe("neuer_status");
    expect(statusMeta("").label).toBe("Unbekannt");
    expect(statusMeta(undefined).label).toBe("Unbekannt");
  });
});

describe("U-117 / M-14 / U-124: PortalSheet", () => {
  const a = { n: "a" }, b = { n: "b" }, c = { n: "c" };
  it("Tab am Ende springt zum Anfang, Shift+Tab am Anfang ans Ende", () => {
    expect(fokusFalle(false, [a, b, c], c)).toBe(a);
    expect(fokusFalle(true, [a, b, c], a)).toBe(c);
  });
  it("mittendrin macht der Browser weiter; Fokus außerhalb kommt zum ersten", () => {
    expect(fokusFalle(false, [a, b, c], b)).toBeNull();
    expect(fokusFalle(true, [a, b, c], b)).toBeNull();
    expect(fokusFalle(false, [a, b, c], { n: "fremd" })).toBe(a);
    expect(fokusFalle(false, [], a)).toBeNull();
  });
  it("Quelltext: Escape, Breite ohne Überhang, Abzeichen benannt", async () => {
    const q = await quelle("@/components/PortalSheet.jsx");
    expect(q).toContain('e.key === "Escape"');
    expect(q).toContain("w-[calc(100%-2rem)] max-w-sm");
    expect(q).not.toContain("max-w-sm mx-4");
    expect(q.match(/<PortalBadge kind="(autoscout|mobile)" size="sm" dekorativ=\{false\} \/>/g)).toHaveLength(2);
    const badge = await quelle("@/components/PortalBadge.jsx");
    expect(badge).toContain('role: "img", "aria-label": spec.alt');
  });
});

describe("U-164 / U-171 / U-163: Freigaben", () => {
  it("Überschrift nennt 'mindestens' nur bei Kürzung", () => {
    expect(wartendUeberschrift(3, false)).toBe("Warten auf Freigabe (3)");
    expect(wartendUeberschrift(500, true)).toBe("Warten auf Freigabe (mindestens 500 — älteste nicht angezeigt)");
  });
  it("Preis 0 oder negativ wird vor dem Senden abgefangen", () => {
    expect(preisFehler(null)).toMatch(/gültigen Preis/);
    expect(preisFehler(0)).toMatch(/größer als 0/);
    expect(preisFehler(-5)).toMatch(/größer als 0/);
    expect(preisFehler(15000)).toBeNull();
  });
  it("Quelltext: Laufnummer und X-Truncated", async () => {
    const q = await quelle("./Freigaben.jsx");
    expect(q).toContain("const n = ++lauf.current;");
    expect(q.match(/if \(n !== lauf\.current\) return;/g)).toHaveLength(2);
    expect(q).toContain('r.headers?.["x-truncated"]');
  });
});

describe("M-17: Tastatur im Auswahlfeld", () => {
  it("Pfeile bewegen die Markierung innerhalb der Liste", () => {
    expect(naechsteMarkierung("ArrowDown", 0, 3)).toBe(1);
    expect(naechsteMarkierung("ArrowDown", 2, 3)).toBe(2);
    expect(naechsteMarkierung("ArrowUp", 0, 3)).toBe(0);
    expect(naechsteMarkierung("ArrowUp", 2, 3)).toBe(1);
    expect(naechsteMarkierung("End", 0, 3)).toBe(2);
    expect(naechsteMarkierung("Home", 2, 3)).toBe(0);
    expect(naechsteMarkierung("ArrowDown", 9, 3)).toBe(2);   // Liste geschrumpft
    expect(naechsteMarkierung("ArrowDown", 0, 0)).toBe(0);
    expect(naechsteMarkierung("x", 1, 3)).toBe(1);
  });
  it("Quelltext: Escape/Enter im Auswahlfeld, Fokusring statt outline-none (M-18 Chips 44 px)", async () => {
    const q = await quelle("./ManuelleSuche.jsx");
    expect(q).not.toMatch(/className="[^"]*outline-none/);
    expect(q).toContain("focus-visible:ring-2 focus-visible:ring-[var(--accent-red)]");
    expect(q).toContain('if (e.key === "Escape") { e.preventDefault(); onClose(); return; }');
    expect(q).toContain('if (e.key === "Enter")');
    expect(q).toContain("min-h-[44px] rounded-lg text-[12.5px]");
  });
});

describe("U-82 / U-15 / U-11 / U-12 / U-14 / U-85: Vergleich", () => {
  it("bereits_vorhanden meldet keinen neuen Vertrag", () => {
    expect(vertragErstelltMeldung({ bereits_vorhanden: true, appointment_id: "t1" })).toMatch(/schon angelegt/);
    expect(vertragErstelltMeldung({ appointment_id: "t1" })).toMatch(/Termin automatisch/);
    expect(vertragErstelltMeldung({})).toBe("PDF erstellt");
  });
  it("Quelltext-Verdrahtung", async () => {
    const q = await quelle("./Vergleich.jsx");
    expect(VERGLEICH_LAEUFT_HINWEIS).toMatch(/abbrechen/);
    expect(q).toContain('toast.info(VERGLEICH_LAEUFT_HINWEIS, { id: "vergleich-laeuft" })');
    expect(q).toContain("moeglich={result.beweis_moeglich !== false}");
    expect(q).toContain("url: urlRef.current, result, counter, contract");
    expect(q).toContain("}, [result, counter, contract, kontoId]);");
    expect(q).toContain("key={`${idx}-${src}`}");
    expect(q).toContain("key={`${i}-${f}`}");
    expect(q).toContain("setContract((c) => (c?.id === id ? { ...c, ...data } : c))");
    const karte = await quelle("@/components/BeweisCard.jsx");
    expect(karte).toContain('data-testid="beweis-nicht-moeglich"');
    expect(karte).toContain("Browser-Erweiterung geladen – kein Beweisdokument möglich");
    expect(karte).toContain("n === 0 && (start || vehicleId) ? TAKT_MS : 0");   // U-120
    expect(karte.match(/onClick=\{drucken\} disabled=\{druckt\}/g)).toHaveLength(2);   // U-119
  });
});

describe("V-33 / U-169 / U-165 / V-34: X-Truncated", () => {
  it("listeGekuerzt liest die Kopfzeile", () => {
    expect(listeGekuerzt({ headers: { "x-truncated": "1" } })).toBe(true);
    expect(listeGekuerzt({ headers: { "x-truncated": "0" } })).toBe(false);
    expect(listeGekuerzt({ headers: {} })).toBe(false);
    expect(listeGekuerzt(undefined)).toBe(false);
  });
  it("Fahrer- und Team-Seite lesen die Kopfzeile und zeigen den Hinweis", async () => {
    const fahrer = await quelle("./Fahrer.jsx");
    expect(fahrer).toContain('r.headers?.["x-truncated"]');
    expect(fahrer).toContain('data-testid="drivers-gekuerzt"');
    const team = await quelle("./Team.jsx");
    expect(team).toContain('s.headers?.["x-truncated"]');
    expect(team).toContain('data-testid="team-gekuerzt"');
    const app = await quelle("@/pages/driver/DriverDashboard.jsx");
    expect(app).toContain('data-testid="driver-gekuerzt"');
    expect(app.match(/setGekuerzt\(listeGekuerzt\(r\)\)/g)).toHaveLength(2);
  });
});

describe("Inserat / Bestand / Akte / Einstellungen (Quelltext)", () => {
  it("U-106/U-108/U-118/U-125/U-113/M-18 in Inserat.jsx", async () => {
    const q = await quelle("./Inserat.jsx");
    expect(q).not.toContain("einkaufFotos.slice(0, 12)");
    expect(q).toContain("einkaufFotos.map((u, i) =>");
    // U-108: publish sperrt wie setStatus bis zum neuen Stand
    const publish = q.slice(q.indexOf("const publish = async"), q.indexOf("const FOTOS_JE_PAKET"));
    expect(publish).toContain("if (busy || statusLaeuft.current) return;");
    expect(publish).toContain("statusLaeuft.current = true;");
    expect(publish).toContain("statusLaeuft.current = false;");
    expect(q).toContain('data-testid="inserat-anfragen-fehler"');
    expect(q).toContain("setFehler(e?.response?.status !== 403)");
    expect(q.match(/String\(c\.id \|\| ""\)\.slice\(0, 8\) \|\| "ohne Nummer"/g)).toHaveLength(1);
    expect(q).toContain("alt={`Foto ${i + 1} aus dem alten Bestand`}");
    expect(q).toContain("alt={`Foto ${i + 1}`}");
    expect(q).toContain('data-testid="inserat-zurueck-entwurf" className="basis-full sm:basis-auto sm:ml-auto');
    expect(q.match(/min-h-\[44px\]/g).length).toBeGreaterThanOrEqual(10);
  });
  it("U-44: Bestand — Banner rollenabhängig, Sucher-Knopf 'Aus meiner Liste entfernen'", async () => {
    const q = await quelle("./Bestand.jsx");
    expect(q).toContain('{chef ? "deine Entscheidung" : "die Entscheidung des Chefs"}');
    expect(q).toContain("data-testid={`bestand-sucher-entfernen-${v.id}`}");
    expect(q).toContain("api.post(`/vehicles/${vehicleId}/entfernen`)");
  });
  it("U-47/U-125: FahrzeugAkte — Anfragenummer, Vertrag ohne id", async () => {
    const q = await quelle("./FahrzeugAkte.jsx");
    expect(q).toContain("const nr = ++anfrageNr.current;");
    expect(q.match(/if \(nr !== anfrageNr\.current\) return;/g)).toHaveLength(2);
    expect(q).toContain('String(c.id || "").slice(0, 8) || "ohne Nummer"');
  });
  it("U-136/U-139: Einstellungen — Sucher-Text, Abo-Reiter mit 'Erneut laden'", async () => {
    const q = await quelle("./Einstellungen.jsx");
    expect(q).toContain("der Marktplatz zeigt die Firmendaten des Chefs.");
    expect(q).toContain('data-testid="abo-erneut-laden"');
    expect(q).toContain('data-testid="abo-ladefehler"');
    expect(q).not.toContain("if (!data) return null;");
  });
});
