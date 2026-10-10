/**
 * Akzentfarbe der App (Wunsch Ahmad 02.10.2026): Alles, was heute Rot ist (var(--accent-red), Markierungen,
 * Preise, aktive Symbole) und die blauen Hauptknöpfe folgen EINER wählbaren Farbe. "standard" = Rot/Blau wie
 * bisher. Die Wahl liegt im Browser (sofort wirksam) und am Konto (Server, gilt auf jedem Gerät).
 *
 * Je Farbe ein Paar für das dunkle und das helle Design: [Hauptton, dunklerer Ton] — der Hauptton ist der
 * Akzent, das Paar bildet den Verlauf der Hauptknöpfe. Alle Haupttöne tragen weiße Schrift mindestens so gut
 * wie das bisherige Rot (#ff3b30, ≈ 3,1:1).
 */
import { lokalerSpeicher, lesen, schreiben } from "@/lib/speicher";

export const AKZENT_KEY = "ah_akzent";
export const AKZENT_STANDARD = "standard";

export const AKZENTFARBEN = [
  { key: "standard", label: "Standard (Rot und Blau wie bisher)", dunkel: ["#ff3b30", "#0a84ff"], hell: ["#ff3b30", "#0071e3"] },
  { key: "rot",     label: "Rot",                 dunkel: ["#ff3b30", "#d93025"], hell: ["#ff3b30", "#c92a21"] },
  { key: "lila",    label: "Lila (freundlich)",   dunkel: ["#a855f7", "#7e3af2"], hell: ["#7c3aed", "#5b21b6"] },
  { key: "gruen",   label: "Grün",                dunkel: ["#22c55e", "#15803d"], hell: ["#15803d", "#166534"] },
  { key: "blau",    label: "Blau",                dunkel: ["#0a84ff", "#0062cc"], hell: ["#0071e3", "#0051a8"] },
  { key: "schwarz", label: "Schwarz (im dunklen Design Grau)", dunkel: ["#6e6e73", "#48484a"], hell: ["#1d1d1f", "#000000"] },
  { key: "orange",  label: "Orange",              dunkel: ["#f97316", "#c2410c"], hell: ["#c2410c", "#9a3412"] },
  { key: "petrol",  label: "Petrol",              dunkel: ["#14b8a6", "#0f766e"], hell: ["#0f766e", "#115e59"] },
  { key: "pink",    label: "Pink",                dunkel: ["#ec4899", "#be185d"], hell: ["#be185d", "#9d174d"] },
  { key: "gold",    label: "Gold",                dunkel: ["#eab308", "#a16207"], hell: ["#a16207", "#854d0e"] },
  { key: "indigo",  label: "Indigo",              dunkel: ["#6366f1", "#4338ca"], hell: ["#4f46e5", "#3730a3"] },
];

const SCHLUESSEL = new Set(AKZENTFARBEN.map((f) => f.key));

/** Gültiger Schlüssel oder "standard". */
export function akzentSchluessel(wert) {
  const k = String(wert || "").trim().toLowerCase();
  return SCHLUESSEL.has(k) ? k : AKZENT_STANDARD;
}

export function akzentEintrag(wert) {
  const k = akzentSchluessel(wert);
  return AKZENTFARBEN.find((f) => f.key === k) || AKZENTFARBEN[0];
}

function hexZuRgb(hex) {
  const h = String(hex || "").replace("#", "");
  const v = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(v, 16);
  if (Number.isNaN(n) || v.length !== 6) return [0, 0, 0];
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function aufhellen(hex, anteil = 0.08) {
  const [r, g, b] = hexZuRgb(hex);
  const f = (x) => Math.min(255, Math.round(x + (255 - x) * anteil));
  return `#${[f(r), f(g), f(b)].map((x) => x.toString(16).padStart(2, "0")).join("")}`;
}

const VARIABLEN = ["--accent-red", "--accent-red-hover", "--border-focus", "--akzent-rgb",
                   "--knopf-primaer", "--knopf-primaer-dunkel", "--knopf-primaer-hover", "--knopf-primaer-hover-dunkel",
                   "--knopf-primaer-schatten"];

/** Die Werte, die für einen Schlüssel und ein Design gesetzt werden (leer = Standard, nichts überschreiben). */
export function akzentWerte(wert, theme = "dark") {
  const e = akzentEintrag(wert);
  if (e.key === AKZENT_STANDARD) return {};
  const [haupt, dunkel] = theme === "light" ? e.hell : e.dunkel;
  const rgb = hexZuRgb(haupt).join(", ");
  return {
    "--accent-red": haupt,
    "--accent-red-hover": dunkel,
    "--border-focus": `rgba(${rgb}, 0.5)`,
    "--akzent-rgb": rgb,
    "--knopf-primaer": haupt,
    "--knopf-primaer-dunkel": dunkel,
    "--knopf-primaer-hover": aufhellen(haupt),
    "--knopf-primaer-hover-dunkel": aufhellen(dunkel),
    "--knopf-primaer-schatten": `rgba(${rgb}, 0.55)`,
  };
}

function aktuellesTheme() {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

/** Setzt die Farbe am Dokument (sofort sichtbar). Ohne Dokument (Tests ohne DOM) passiert nichts. */
export function akzentAnwenden(wert, theme = aktuellesTheme()) {
  if (typeof document === "undefined") return akzentSchluessel(wert);
  const wurzel = document.documentElement;
  const werte = akzentWerte(wert, theme);
  for (const name of VARIABLEN) {
    if (werte[name]) wurzel.style.setProperty(name, werte[name]);
    else wurzel.style.removeProperty(name);
  }
  wurzel.setAttribute("data-akzent", akzentSchluessel(wert));
  return akzentSchluessel(wert);
}

export function gespeicherterAkzent() {
  return akzentSchluessel(lesen(lokalerSpeicher(), AKZENT_KEY));
}

export function akzentMerken(wert) {
  const k = akzentSchluessel(wert);
  schreiben(lokalerSpeicher(), AKZENT_KEY, k);
  return k;
}

/** Beim Start (vor React) und bei jedem Designwechsel: gemerkte Farbe im passenden Design anwenden. */
export function applyStoredAkzent(theme = aktuellesTheme()) {
  return akzentAnwenden(gespeicherterAkzent(), theme);
}

/** Vom Server gelieferte Farbe (Konto) übernehmen — sie gilt vor der lokalen Wahl. */
export function akzentVomKonto(wert) {
  if (wert === undefined || wert === null || wert === "") return gespeicherterAkzent();
  const k = akzentMerken(wert);
  akzentAnwenden(k);
  return k;
}
