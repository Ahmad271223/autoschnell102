/*
 * Wunsch Ahmad 24.11.2026: Auswertungen für Kopfleiste, Übersicht-Kacheln
 * und die schmale Zeile der Fahrzeugakte — nur Anzeige, keine Logik, die
 * der Server nicht schon entschieden hat.
 */

// Geschützte Leerzeichen: "20.000 €" bricht nie vor dem €-Zeichen um.
export const eur = (n) => (n == null || !Number.isFinite(Number(n))
  ? null : `${Number(n).toLocaleString("de-DE")} €`);

export function datumZeit(s) {
  if (!s) return null;
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString("de-DE", { timeZone: "Europe/Berlin", day: "2-digit", month: "2-digit",
    year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function datum(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(s ?? "").trim());
  if (m) return `${m[3]}.${m[2]}.${m[1]}`;
  const d = new Date(s);
  if (!s || Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("de-DE", { timeZone: "Europe/Berlin", day: "2-digit", month: "2-digit", year: "numeric" });
}

/** Erstes Foto des Fahrzeugs (URL) oder null. */
export function erstesFoto(daten) {
  const d = daten || {};
  for (const feld of ["image_urls", "images", "photos", "pictures"]) {
    const liste = d[feld];
    if (Array.isArray(liste) && liste.length) {
      const f = liste[0];
      if (typeof f === "string" && f) return f;
      if (f && typeof f === "object" && (f.url || f.src)) return f.url || f.src;
    }
  }
  return null;
}

/**
 * Welcher Einkaufspreis gilt (gleiche Reihenfolge wie „Beschaffung & Kauf“):
 * realisiert am Fahrzeug, sonst Vorschlag des Servers (Vertrag), sonst keiner.
 */
export function einkaufAus(akte) {
  const v = akte?.vehicle || {};
  if (v.purchase_price != null) return { preis: v.purchase_price, hinweis: "realisiert" };
  const e = akte?.einkaufspreis || {};
  if (e.preis != null) return { preis: e.preis, hinweis: e.quelle === "vertrag" ? "laut Kaufvertrag" : "" };
  if (e.quelle === "mehrdeutig") return { preis: null, hinweis: "mehrere Verträge, Preis steht nach der Abholung fest" };
  return { preis: null, hinweis: "" };
}

/** Neuester Vertrag mit Versand- und Bestätigungsstand. */
export function vertragStand(akte) {
  const c = (akte?.contracts || [])[0];
  if (!c) return null;
  const versand = (Array.isArray(c.send_status) ? c.send_status : [])
    .filter((s) => s && s.sent_at)
    .sort((a, b) => String(b.sent_at).localeCompare(String(a.sent_at)))[0] || null;
  const unterschrieben = c.kunde_unterschrieben_am || c.portal?.unterschrieben_am || null;
  return {
    id: c.id,
    nummer: c.contract_no || String(c.id || "").slice(0, 8) || "ohne Nummer",
    fassung: Number(c.version) > 1 ? Number(c.version) : null,
    erstellt: c.created_at,
    versandAm: versand?.sent_at || null,
    versandWeg: versand?.channel || null,
    unterschriebenAm: unterschrieben,
    portalStatus: c.portal?.status || null,
  };
}

/** Termin für die Kachel: offener zuerst (frühester), sonst der neueste. */
export function terminStand(akte) {
  const termine = akte?.appointments || [];
  if (!termine.length) return null;
  const zu = ["abgeholt", "nicht abgeholt", "storniert", "erledigt"];
  const offen = termine.filter((t) => !zu.includes(String(t.status || "").toLowerCase()))
    .sort((a, b) => String(a.pickup_date || "").localeCompare(String(b.pickup_date || "")));
  return offen[0] || termine[0];
}

export const VERSANDWEG = { email: "per E-Mail", whatsapp: "per WhatsApp", link: "per Link" };
