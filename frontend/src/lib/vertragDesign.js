/**
 * Vertragsdesign (Wunsch Ahmad 03.10.2026): Farbe und Layout des Kaufvertrags-PDFs.
 * Dieselben Farbnamen wie die Farbe der App, aber druckfreundliche Töne für weißes Papier —
 * muss mit backend/pdf_service.VERTRAG_FARBEN übereinstimmen (test_vertrag_design_20261003 prüft das).
 * "rot" gibt es im Backend auch, hier fehlt es: es ist dieselbe Farbe wie "standard".
 */
export const VERTRAG_FARBEN = [
  { key: "standard", label: "Standard (Rot)", hex: "#FF3B30" },
  { key: "lila",     label: "Lila",           hex: "#7C3AED" },
  { key: "gruen",    label: "Grün",           hex: "#15803D" },
  { key: "blau",     label: "Blau",           hex: "#0071E3" },
  { key: "schwarz",  label: "Schwarz",        hex: "#1D1D1F" },
  { key: "orange",   label: "Orange",         hex: "#E8731F" },
  { key: "petrol",   label: "Petrol",         hex: "#0F766E" },
  { key: "pink",     label: "Pink",           hex: "#BE185D" },
  { key: "gold",     label: "Gold",           hex: "#A16207" },
  { key: "indigo",   label: "Indigo",         hex: "#4F46E5" },
];

export const VERTRAG_LAYOUTS = [
  { key: "modern", label: "Modern", text: "Wie bisher: Balken-Überschriften, Kästen für die Parteien, dunkler Preisblock." },
  { key: "formular", label: "Formular", text: "Überschrift mittig, Felder auf Linien wie ein ausgefülltes Formular, heller Preisstreifen." },
];

/** Gültiger Farbname ("rot" zählt als Standard) — sonst "standard". */
export function vertragFarbe(wert) {
  const k = String(wert || "").trim().toLowerCase();
  if (k === "rot") return "standard";
  return VERTRAG_FARBEN.some((f) => f.key === k) ? k : "standard";
}

export function vertragFarbeHex(wert) {
  const k = vertragFarbe(wert);
  return (VERTRAG_FARBEN.find((f) => f.key === k) || VERTRAG_FARBEN[0]).hex;
}

export function vertragLayout(wert) {
  const k = String(wert || "").trim().toLowerCase();
  return VERTRAG_LAYOUTS.some((l) => l.key === k) ? k : "modern";
}
