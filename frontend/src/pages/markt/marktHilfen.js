/**
 * Hilfen für den Marktplatz der Zwischenhändler (Rollenprüfung 22.09.2026).
 *
 * Reine Funktionen und kleine Speicher-Helfer, damit Marktplatz.jsx,
 * BuyerLogin.jsx und Anfrage.jsx dieselben Regeln benutzen und sie ohne
 * Browser getestet werden können (marktHilfen.rp_markt_kaeufer.test.jsx).
 */
import { kmAusText, preisAusText, preisText } from "@/lib/preis";
import { lesen, lokalerSpeicher, schreiben, sitzungsSpeicher } from "@/lib/speicher";

/* ------------------------------------------------------------------ Zahlen */

/**
 * RP-501/RP-512: PS deutsch lesen — nur ganze Zahlen, "150 PS" erlaubt.
 * "" → null (nichts eingetragen), Unsinn → NaN (der Aufrufer meldet es).
 */
export function psAusText(eingabe) {
  if (eingabe === null || eingabe === undefined) return null;
  let t = String(eingabe).trim();
  if (!t) return null;
  t = t.replace(/ps$/i, "").replace(/[\s\u00a0\u202f']/g, "");
  if (!t) return null;
  if (!/^\d{1,3}(\.\d{3})+$|^\d{1,5}$/.test(t)) return NaN;
  const zahl = Number(t.replace(/\./g, ""));
  return Number.isFinite(zahl) ? zahl : NaN;
}

const FILTER_NAMEN = {
  km_min: "Kilometer von", km_max: "Kilometer bis",
  ps_min: "PS von", ps_max: "PS bis",
  price_min: "Preis von", price_max: "Preis bis",
};

/**
 * RP-501/RP-512: Filterfelder (Text, deutsch) → URL-Parameter (Zahlen).
 * Vorher gingen <input type="number">-Werte roh an den Server: im deutschen
 * Browser wurde "150.000" zu 150 km, "12.500" € zu 12,5 €, und "95.5"
 * brachte eine englische Fehlermeldung.
 * Rückgabe: { params: {name: wert}, fehler: "…" | null }.
 */
export function filterParameter(filters) {
  const params = {};
  for (const [name, roh] of Object.entries(filters || {})) {
    const text = String(roh ?? "").trim();
    if (!text) continue;
    let wert = text;
    if (name === "km_min" || name === "km_max") {
      wert = kmAusText(text);
      if (wert === null) continue;
      if (Number.isNaN(wert)) {
        return { params: {}, fehler: `${FILTER_NAMEN[name]}: bitte eine ganze Zahl eingeben (z. B. 150.000)` };
      }
    } else if (name === "ps_min" || name === "ps_max") {
      wert = psAusText(text);
      if (wert === null) continue;
      if (Number.isNaN(wert)) {
        return { params: {}, fehler: `${FILTER_NAMEN[name]}: bitte eine ganze Zahl eingeben (z. B. 150)` };
      }
    } else if (name === "price_min" || name === "price_max") {
      wert = preisAusText(text);
      if (wert === null) {
        return { params: {}, fehler: `${FILTER_NAMEN[name]}: bitte einen Betrag wie 15.000 eingeben` };
      }
    }
    params[name] = String(wert);
  }
  return { params, fehler: null };
}

/**
 * RP-501: Angebotsbetrag (deutsch) prüfen. Leer ist erlaubt, wenn `optional`.
 * Rückgabe: { betrag: Zahl | null, fehler: "…" | null }.
 */
export function betragPruefen(text, { optional = false } = {}) {
  const roh = String(text ?? "").trim();
  if (!roh) {
    return optional ? { betrag: null, fehler: null }
      : { betrag: null, fehler: "Bitte einen Betrag eingeben" };
  }
  const betrag = preisAusText(roh);
  if (betrag === null) {
    return { betrag: null, fehler: "Bitte einen Betrag wie 12.500 eingeben (Punkt = Tausender, Komma = Cent)" };
  }
  if (!(betrag > 0)) return { betrag: null, fehler: "Der Betrag muss größer als 0 sein" };
  return { betrag, fehler: null };
}

/**
 * RP-456 (Welle 2, wie Anfragen.jsx beim Händler): schon beim Tippen zeigen,
 * wie der Betrag verstanden wird ("= 12.500,00 €"). Leeres Feld → null.
 * Rückgabe: { fehler: bool, text: "…" } oder null.
 */
export function betragHinweis(text) {
  if (!String(text ?? "").trim()) return null;
  const { betrag, fehler } = betragPruefen(text);
  return fehler ? { fehler: true, text: fehler } : { fehler: false, text: `= ${preisText(betrag)}` };
}

/** RP-501: Rückfrage vor dem Senden — der Käufer sieht, was ankommt. */
export function angebotsFrage(betrag, art = "Angebot") {
  return `Dein ${art}: ${preisText(betrag)}\n\nSo an den Händler senden?`;
}

/* --------------------------------------------------------------- Anzeige */

/** RP-507: Kilometerstand nur, wenn wirklich eine Zahl da ist ("" ≠ 0 km). */
export function kmAnzeige(mileage) {
  if (mileage === null || mileage === undefined) return null;
  if (typeof mileage === "string" && !mileage.trim()) return null;
  const zahl = Number(mileage);
  if (!Number.isFinite(zahl)) return null;
  return `${zahl.toLocaleString("de-DE")} km`;
}

/**
 * RP-504: "Unfallfrei" nur aus der Angabe des Händlers (accident_free aus dem
 * Inserat-Editor). Eine Portal-Angabe wird nie zur Zusicherung; meldete das
 * Einkaufsinserat einen Unfallschaden, erscheint nur ein Hinweis.
 * Rückgabe: [Bezeichnung, Wert] oder null.
 */
export function unfallZeile(daten) {
  const d = daten || {};
  const wert = d.accident_free;
  if (wert === true || wert === "Ja") return ["Unfallfrei", "Ja"];
  if (wert === false || wert === "Nein") return ["Unfallfrei", "Nein"];
  if (d.unfallschaden_laut_einkauf) return ["Unfallschaden", "laut Einkaufsinserat"];
  return null;
}

/**
 * RP-505: Der Käufer sieht den Titel, den der Händler pflegt (vorher nur
 * Marke/Modell plus den Anzeigentitel des Privatverkäufers).
 */
export function fahrzeugTitel(v) {
  const d = (v && v.data) || {};
  const markeModell = [d.make_label, d.model_label].filter(Boolean).join(" ").trim();
  const titel = String((v && v.title) || "").trim();
  if (titel) return { titel, unterzeile: markeModell && markeModell !== titel ? markeModell : "" };
  return { titel: markeModell || "Fahrzeug", unterzeile: "" };
}

/** Lesbare Status-Namen für "Meine Anfragen" (vorher Rohwerte wie gegenangebot_kaeufer). */
export const STATUS_TEXT = {
  offen: "Offen",
  gegenangebot: "Gegenangebot",
  gegenangebot_kaeufer: "Dein Gegenangebot",
  akzeptiert: "Angenommen",
  abgelehnt: "Beendet",
};

const GRUND_TEXT = {
  kaeufer_zurueckgezogen: "Von dir zurückgezogen",
  netzwerk_entfernt: "Beendet — der Händler hat dich aus seinem Netzwerk entfernt",
  inserat_verkauft: "Beendet — das Fahrzeug wurde verkauft",
  inserat_geloescht: "Beendet — das Inserat wurde gelöscht",
  inserat_zurueckgezogen: "Beendet — das Inserat wurde zurückgezogen",
  inserat_entwurf: "Beendet — das Inserat ist nicht mehr veröffentlicht",
  reservierung_aufgehoben: "Beendet — der Händler hat die Reservierung aufgehoben",
  kaeufer_gesperrt: "Beendet — dein Zugang wurde gesperrt",
  // Welle 2 (22.09.2026): Gründe aus resale (Hand-Reservierung, RP-093),
  // dem Aufräumlauf (Laufzeit, Inserat/Fahrzeug weg) und der Index-Bereinigung.
  inserat_reserviert: "Beendet — der Händler hat das Fahrzeug anderweitig reserviert",
  inserat_abgelaufen: "Beendet — die Laufzeit des Inserats ist abgelaufen",
  inserat_weg: "Beendet — das Inserat ist nicht mehr verfügbar",
  fahrzeug_geloescht: "Beendet — das Fahrzeug ist nicht mehr im Angebot",
  doppelte_anfrage: "Beendet — doppelte Anfrage (die andere läuft weiter)",
  // Welle 3 (22.09.2026): letzter Grund, den der Server schreibt
  // (admin.kaeufer_reservierungen_freigeben beim Löschen des Käuferkontos) —
  // vorher stand dort nur "Beendet". Die Händlerseite kennt ihn schon.
  kaeufer_geloescht: "Beendet — dein Käuferkonto wurde gelöscht",
};

/** Warum eine Anfrage beendet ist (beendet_grund) — oder null. */
export function beendetText(it) {
  if (!it || it.status !== "abgelehnt") return null;
  return GRUND_TEXT[it.beendet_grund] || (it.beendet_grund ? "Beendet" : "Abgelehnt");
}

const AKTION_TEXT = {
  interesse: "Anfrage", gegenangebot: "Gegenangebot", annehmen: "angenommen",
  akzeptieren: "angenommen", ablehnen: "abgelehnt", zurueckgezogen: "zurückgezogen",
};

/** Eine Verlaufszeile aus Sicht des Käufers ("Du · Gegenangebot · 12.500 €"). */
export function verlaufZeile(h) {
  const wer = h.von === "kaeufer" ? "Du" : h.von === "system" ? "System" : "Händler";
  const was = AKTION_TEXT[h.aktion] || GRUND_TEXT[h.aktion] || h.aktion;
  let zeile = `${wer} · ${was}`;
  if (h.angebot !== null && h.angebot !== undefined) zeile += ` · ${preisText(h.angebot)}`;
  if (h.nachricht) zeile += ` · „${h.nachricht}“`;
  return zeile;
}

/* ---------------------------------------------- Inseratslaufzeit (RP-519) */

/**
 * Rollenprüfung 22.09.2026 (RP-519): Inserate laufen nach 21 Tagen ab, und
 * mit ihnen enden laufende Anfragen — der Käufer sah die Frist nirgends. Der
 * Server liefert `laeuft_ab_am` (nur für veröffentlichte Inserate) im
 * Marktplatz und je Anfrage. Rückgabe: { bis: "12.10.2026", abgelaufen,
 * bald (≤ 3 Tage) } oder null ohne bzw. mit kaputtem Datum.
 */
export const INSERAT_BALD_TAGE = 3;

export function inseratAblauf(iso, jetzt = Date.now()) {
  if (!iso || typeof iso !== "string") return null;
  const ende = Date.parse(iso);
  if (!Number.isFinite(ende)) return null;
  const bis = new Date(ende).toLocaleDateString("de-DE", {
    day: "2-digit", month: "2-digit", year: "numeric",
  });
  return {
    bis,
    abgelaufen: ende <= jetzt,
    bald: ende - jetzt <= INSERAT_BALD_TAGE * 24 * 3600 * 1000,
  };
}

/** Hinweis unter einer LAUFENDEN Anfrage — "" wenn nichts anzuzeigen ist
 *  (beendet, ruht wegen fremder Reservierung, Inserat nicht veröffentlicht). */
export function anfrageAblaufText(it, jetzt = Date.now()) {
  if (!it || !["offen", "gegenangebot", "gegenangebot_kaeufer"].includes(it.status)) return "";
  if (it.anderweitig_reserviert) return "";
  if (it.inserat_status && it.inserat_status !== "veroeffentlicht") return "";
  const a = inseratAblauf(it.laeuft_ab_am, jetzt);
  if (!a) return "";
  if (a.abgelaufen) {
    return "Das Inserat ist abgelaufen und wird in Kürze entfernt — deine Anfrage endet dann.";
  }
  return `Das Inserat läuft am ${a.bis} ab — danach endet deine Anfrage automatisch.`;
}

/* ------------------------------------------------------- Zugang (RP-509) */

/** Ab so vielen Tagen vor dem Ablauf lässt der Server eine Verlängerung zu
 *  (routes/marketplace.VERLAENGERN_AB_TAGEN). */
export const VERLAENGERN_AB_TAGEN = 7;

/**
 * Rollenprüfung 22.09.2026 (RP-509): Bis wann gilt der bezahlte Zugang, und
 * darf der Käufer schon verlängern? Vorher zeigte der Marktplatz kein
 * Ablaufdatum, und "Zugang anfragen" antwortete bis zum letzten Tag "bereits
 * aktiv". Kostenlos-Modus, gesperrt oder kein Datum → null.
 * Rückgabe: { bis: "12.10.2026", bald: true|false } oder null.
 */
export function zugangAblauf(access, jetzt = Date.now()) {
  if (!access || access.kostenlos || access.gesperrt || !access.active || !access.expires_at) return null;
  const ende = Date.parse(access.expires_at);
  if (!Number.isFinite(ende)) return null;
  const bis = new Date(ende).toLocaleDateString("de-DE", {
    day: "2-digit", month: "2-digit", year: "numeric",
  });
  return { bis, bald: ende - jetzt <= VERLAENGERN_AB_TAGEN * 24 * 3600 * 1000 };
}

/* --------------------------------------------- Öffentliche Ansicht (RP-098) */

/**
 * Rollenprüfung 22.09.2026 (RP-098 Nr. 3): Besucher ohne Anmeldung sehen die
 * öffentlichen Fahrzeuge. Die Markenliste (/manual/makes) gibt es nur
 * angemeldet — für sie werden Marken und Modelle aus den geladenen
 * Fahrzeugen gebildet (Form wie die Katalogliste: {id, name, models}).
 */
export function markenAusInseraten(items) {
  const marken = new Map();
  for (const v of Array.isArray(items) ? items : []) {
    const marke = String(v?.data?.make_label || "").trim();
    if (!marke) continue;
    if (!marken.has(marke)) marken.set(marke, new Set());
    const modell = String(v?.data?.model_label || "").trim();
    if (modell) marken.get(marke).add(modell);
  }
  const sortiert = (a, b) => a.localeCompare(b, "de");
  return [...marken.keys()].sort(sortiert).map((name) => ({
    id: name,
    name,
    models: [...marken.get(name)].sort(sortiert).map((m) => ({ id: m, name: m })),
  }));
}

/**
 * RP-098 Nr. 9: weitere Seite an die Liste hängen, ohne Doppelte (zwischen
 * zwei Abrufen kann ein neues Fahrzeug die Seiten verschieben).
 */
export function listeAnhaengen(alt, neu) {
  const bisher = Array.isArray(alt) ? alt : [];
  const ids = new Set(bisher.map((v) => v.id));
  return [...bisher, ...(Array.isArray(neu) ? neu : []).filter((v) => !ids.has(v.id))];
}

/* ------------------------------------------------------------- Speicher */

const EINLADUNG = "ah_kaeufer_einladung";
const EINLADUNG_TAGE = 30;                 // längste Gültigkeit eines Einladungslinks

/**
 * RP-511: Einladung eines Partners ohne Konto merken. Er fragt über
 * /anfrage einen Zugang an; bei der ersten Anmeldung auf diesem Gerät wird
 * die Einladung eingelöst (vorher ging sie still verloren).
 */
export function einladungMerken(token, jetzt = Date.now()) {
  const t = String(token || "").trim();
  if (!t || t.length > 200) return false;
  return schreiben(lokalerSpeicher(), EINLADUNG,
    JSON.stringify({ token: t, bis: jetzt + EINLADUNG_TAGE * 24 * 3600 * 1000 }));
}

/** Gemerkte, noch nicht abgelaufene Einladung oder "". */
export function gemerkteEinladung(jetzt = Date.now()) {
  try {
    const roh = lesen(lokalerSpeicher(), EINLADUNG, "");
    if (!roh) return "";
    const e = JSON.parse(roh);
    if (!e || typeof e.token !== "string" || !(Number(e.bis) > jetzt)) {
      einladungVergessen();
      return "";
    }
    return e.token;
  } catch {
    einladungVergessen();
    return "";
  }
}

export function einladungVergessen() {
  try { lokalerSpeicher()?.removeItem(EINLADUNG); } catch { /* egal */ }
}

/** RP-531: vom 401-Abfänger gemerkter Grund der Abmeldung (nur dieser Tab). */
export const ABMELDEGRUND_KAEUFER = "ah_kaeufer_abmeldegrund";

export function abmeldegrundLesenUndLoeschen() {
  const speicher = sitzungsSpeicher();
  const g = lesen(speicher, ABMELDEGRUND_KAEUFER, "") || "";
  try { speicher?.removeItem(ABMELDEGRUND_KAEUFER); } catch { /* egal */ }
  return g;
}

const ENTWURF = "ah_kaeufer_entwurf";

/**
 * RP-531: Anfrage/Gegenangebot VOR dem Senden sichern. Endet die Sitzung
 * mitten drin (401 → volle Weiterleitung zur Anmeldung), gingen Betrag und
 * Nachricht verloren. Der Entwurf liegt nur in diesem Tab (sessionStorage).
 *   art "anfrage":       { listing_id, betrag, nachricht }
 *   art "gegenangebot":  { interest_id, betrag }
 */
export function entwurfMerken(art, daten) {
  return schreiben(sitzungsSpeicher(), ENTWURF, JSON.stringify({ art, ...daten, zeit: Date.now() }));
}

export function entwurfLesen(art) {
  try {
    const e = JSON.parse(lesen(sitzungsSpeicher(), ENTWURF, "") || "null");
    if (!e || e.art !== art) return null;
    // Nach einem halben Tag ist der Entwurf nicht mehr gemeint.
    if (!(Number(e.zeit) > Date.now() - 12 * 3600 * 1000)) return null;
    return e;
  } catch {
    return null;
  }
}

export function entwurfLoeschen() {
  try { sitzungsSpeicher()?.removeItem(ENTWURF); } catch { /* egal */ }
}

const GESEHEN = "ah_kaeufer_anfragen_gesehen";

/** RP-520: Stand der zuletzt in "Meine Anfragen" GEZEIGTEN Liste (ISO,
 *  Serverzeit — siehe anfragenStand) oder "". */
export function anfragenGesehen() {
  return lesen(lokalerSpeicher(), GESEHEN, "") || "";
}

export function anfragenGesehenMerken(iso) {
  // Rollenprüfung 22.09.2026 (Review): kein Vorgabewert "jetzt" mehr — die
  // Geräteuhr ist nicht die Serverzeit von updated_at (siehe anfragenStand).
  if (typeof iso !== "string" || !Number.isFinite(Date.parse(iso))) return false;
  return schreiben(lokalerSpeicher(), GESEHEN, iso);
}

/**
 * Rollenprüfung 22.09.2026 (Review zu RP-520): Bis wohin hat der Käufer die
 * Anfragen WIRKLICH gesehen? Die jüngste Änderungszeit (updated_at, vom
 * Server) der geladenen und angezeigten Liste. Vorher galt beim Öffnen und
 * beim Schließen "jetzt" (Uhr des Geräts) als gesehen: eine Annahme oder
 * Ablehnung, die kam, während das Fenster offen stand, zählte als gesehen,
 * ohne je gezeigt worden zu sein — kein Zähler, keine "Neu"-Marke. Eine
 * vorgehende Geräteuhr versteckte Änderungen genauso.
 * Rückgabe: der updated_at-Wert (unverändert, wie vom Server) oder "".
 */
export function anfragenStand(items) {
  let stand = "";
  let standMs = -Infinity;
  for (const it of Array.isArray(items) ? items : []) {
    const iso = typeof it?.updated_at === "string" ? it.updated_at : "";
    const ms = Date.parse(iso);
    if (!Number.isFinite(ms)) continue;
    // gleiche Millisekunde: der Server schreibt Mikrosekunden — dann der größere Text
    if (ms > standMs || (ms === standMs && iso > stand)) { stand = iso; standMs = ms; }
  }
  return stand;
}

/**
 * RP-520: Zahl am Knopf "Meine Anfragen". Der Server zählt getrennt:
 * am_zug = Gegenangebote des Händlers, neu = andere Änderungen seit dem
 * letzten Öffnen (ohne Gegenangebote) — die Summe zählt nichts doppelt.
 */
export function neuigkeitenZahl(z) {
  if (!z) return 0;
  return (Number(z.am_zug) || 0) + (Number(z.neu) || 0);
}

/** RP-520: ist diese Anfrage seit dem letzten Öffnen vom Händler/System geändert? */
export function istNeu(it, gesehenIso) {
  if (!it || !gesehenIso) return false;
  const verlauf = it.history || [];
  const letzte = verlauf[verlauf.length - 1];
  if (!letzte || letzte.von === "kaeufer") return false;
  const geaendert = Date.parse(it.updated_at);
  const gesehen = Date.parse(gesehenIso);
  return Number.isFinite(geaendert) && Number.isFinite(gesehen) && geaendert > gesehen;
}
