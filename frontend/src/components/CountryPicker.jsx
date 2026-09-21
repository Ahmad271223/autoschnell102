// Country picker for the mobile.de "Land"-filter (Vergleichs-Einstellungen).
// Three modes: only Germany (default), all countries, or custom selection.
// The custom mode shows a chip grid where each country can be toggled.
import { useState } from "react";
import { Check, Globe, X } from "lucide-react";

// Country list mirrors mobile.de's <select id="location-filter-country">
// (excluding the empty default + the duplicate "Deutschland" pin entry).
export const MOBILE_COUNTRIES = [
  { code: "DE", name: "Deutschland" },
  { code: "AT", name: "Österreich" },
  { code: "CH", name: "Schweiz" },
  { code: "BE", name: "Belgien" },
  { code: "DK", name: "Dänemark" },
  { code: "FR", name: "Frankreich" },
  { code: "IT", name: "Italien" },
  { code: "LU", name: "Luxemburg" },
  { code: "NL", name: "Niederlande" },
  { code: "PL", name: "Polen" },
  { code: "CZ", name: "Tschechische Republik" },
  { code: "ES", name: "Spanien" },
  { code: "PT", name: "Portugal" },
  { code: "SE", name: "Schweden" },
  { code: "NO", name: "Norwegen" },
  { code: "FI", name: "Finnland" },
  { code: "GB", name: "Großbritannien" },
  { code: "IE", name: "Irland" },
  { code: "HU", name: "Ungarn" },
  { code: "SK", name: "Slowakische Republik" },
  { code: "SI", name: "Slowenien" },
  { code: "HR", name: "Kroatien" },
  { code: "BG", name: "Bulgarien" },
  { code: "RO", name: "Rumänien" },
  { code: "GR", name: "Griechenland" },
  { code: "EE", name: "Estland" },
  { code: "LV", name: "Lettland" },
  { code: "LT", name: "Litauen" },
  { code: "MT", name: "Malta" },
  { code: "CY", name: "Zypern" },
  { code: "AD", name: "Andorra" },
  { code: "AL", name: "Albanien" },
  { code: "BA", name: "Bosnien und Herzegowina" },
  { code: "BR", name: "Brasilien" },
  { code: "CA", name: "Kanada" },
  { code: "EG", name: "Ägypten" },
  { code: "ET", name: "Äthiopien" },
  { code: "FO", name: "Faröer" },
  { code: "IL", name: "Israel" },
  { code: "IS", name: "Island" },
  { code: "JO", name: "Jordanien" },
  { code: "JP", name: "Japan" },
  { code: "KR", name: "Südkorea" },
  { code: "KW", name: "Kuwait" },
  { code: "LB", name: "Libanon" },
  { code: "LI", name: "Liechtenstein" },
  { code: "MA", name: "Marokko" },
  { code: "MC", name: "Monaco" },
  { code: "MD", name: "Moldawien" },
  { code: "ME", name: "Montenegro" },
  { code: "MK", name: "Mazedonien" },
  { code: "MX", name: "Mexiko" },
  { code: "NG", name: "Nigeria" },
  { code: "NZ", name: "Neuseeland" },
  { code: "OM", name: "Oman" },
  { code: "RS", name: "Serbien" },
  { code: "RU", name: "Russland" },
  { code: "SA", name: "Saudi-Arabien" },
  { code: "SM", name: "San Marino" },
  { code: "TN", name: "Tunesien" },
  { code: "TR", name: "Türkei" },
  { code: "TW", name: "Taiwan" },
  { code: "UA", name: "Ukraine" },
  { code: "US", name: "USA" },
  { code: "AE", name: "Vereinigte Arabische Emirate" },
  { code: "BY", name: "Weißrussland" },
  { code: "ZA", name: "Südafrika" },
];

const labelFor = (code) => MOBILE_COUNTRIES.find((c) => c.code === code)?.name || code;

// Pruefbericht 20.09.2026 (U-26/M19): so viele Laender nimmt der Server an
// (backend/regeln.py MAX_LAENDER) — vorher waren 67 waehlbar, und ab dem
// 41. scheiterte das ganze Speichern.
export const MAX_LAENDER = 40;

export default function CountryPicker({ value, onChange }) {
  // U-28/N10: "any" (beliebig) ist dasselbe wie "alle" — vorher stand dann
  // "Nur Deutschland" in der Auswahl.
  const mode = value?.mode === "any" ? "all" : (value?.mode || "exact");
  const codes = value?.codes || ["DE"];
  const [showAll, setShowAll] = useState(false);
  const [hinweis, setHinweis] = useState("");

  const setMode = (m) => {
    if (m === "all") {
      onChange({ mode: "all", codes: [] });
    } else if (m === "de") {
      onChange({ mode: "exact", codes: ["DE"] });
    } else {
      onChange({ mode: "exact", codes: codes.length ? codes : ["DE"] });
      setShowAll(true);
    }
  };

  const toggleCode = (code) => {
    setHinweis("");
    if (codes.includes(code)) {
      // U-132/M18: Das letzte Land liess sich abwaehlen; der Server lehnte
      // dann den GESAMTEN Speichervorgang ab (auch Vorlagen und Vertragstexte).
      if (codes.length === 1) {
        setHinweis("Mindestens ein Land muss ausgewählt bleiben — sonst „Alle Länder“ wählen.");
        return;
      }
      onChange({ mode: "exact", codes: codes.filter((c) => c !== code) });
      return;
    }
    if (codes.length >= MAX_LAENDER) {
      setHinweis(`Höchstens ${MAX_LAENDER} Länder — für mehr bitte „Alle Länder“ wählen.`);
      return;
    }
    onChange({ mode: "exact", codes: [...codes, code] });
  };

  const isCustom = mode === "exact" && (codes.length !== 1 || codes[0] !== "DE" || showAll);
  const currentMode = mode === "all" ? "all" : isCustom ? "custom" : "de";

  return (
    <div className="space-y-2 w-full">
      <select
        data-testid="rule-country-mode"
        value={currentMode}
        onChange={(e) => setMode(e.target.value)}
        className="apple-input !w-auto !min-w-[200px] cursor-pointer"
      >
        <option value="de">Nur Deutschland</option>
        <option value="all">Alle Länder</option>
        <option value="custom">Ausgewählte Länder…</option>
      </select>

      {currentMode === "custom" && (
        <div className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-3 space-y-2">
          <div className="text-[11px]" style={{ color: "var(--text-muted)" }} data-testid="rule-country-zaehler">
            {codes.length} von höchstens {MAX_LAENDER} Ländern ausgewählt
          </div>
          {hinweis && (
            <div className="text-[12px]" role="alert" style={{ color: "var(--tx-amber)" }}
                 data-testid="rule-country-hinweis">{hinweis}</div>
          )}
          {codes.length > 0 && (
            <div className="flex flex-wrap gap-1.5" data-testid="rule-country-selected">
              {codes.map((code) => (
                <button
                  key={code}
                  type="button"
                  onClick={() => toggleCode(code)}
                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-[var(--accent)] text-white text-xs font-medium hover:opacity-90 transition"
                  data-testid={`country-chip-${code}`}
                >
                  {labelFor(code)} <X size={12} />
                </button>
              ))}
            </div>
          )}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-1 max-h-64 overflow-y-auto pr-1">
            {MOBILE_COUNTRIES.map(({ code, name }) => {
              const sel = codes.includes(code);
              return (
                <button
                  key={code}
                  type="button"
                  onClick={() => toggleCode(code)}
                  className={`flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-xs text-left transition ${
                    sel
                      ? "bg-[var(--accent)]/15 text-[var(--accent)] font-medium"
                      : "hover:bg-[var(--wa-06)] text-[var(--text-primary)]"
                  }`}
                  data-testid={`country-option-${code}`}
                >
                  {sel ? <Check size={12} /> : <Globe size={12} className="opacity-30" />}
                  <span className="truncate">{name}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
