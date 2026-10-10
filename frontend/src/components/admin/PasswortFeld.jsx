import { useState } from "react";
import { Eye, EyeOff, Wand2 } from "lucide-react";
import { PASSWORT_MIN, passwortVorschlag } from "@/lib/passwort";

/**
 * Passwortfeld fuer die Kontenanlage und -pflege durch den Betreiber
 * (14.09.2026, Wunsch Ahmad: "Kontonummer und Passwort muessen nach dem
 * Anlegen immer klappen"):
 *   - Klartext ein-/ausblendbar — Tippfehler sieht man, BEVOR die Zugangsdaten
 *     an den Kontakt gehen
 *   - Vorschlag mit 20 Zeichen, der die Regeln sicher erfuellt
 * Regeln: backend/passwoerter.py (mind. 10 Zeichen, Ziffer oder
 * Sonderzeichen, hoechstens 72 Zeichen). `onChange` bekommt den Text.
 */
export default function PasswortFeld({ value, onChange, placeholder, testid, className, style,
                                       autoFocus = false, autoComplete = "new-password" }) {
  const [sichtbar, setSichtbar] = useState(false);
  const vorschlagen = () => { onChange(passwortVorschlag(20)); setSichtbar(true); };
  return (
    <div>
      <div className="flex gap-2">
        <input value={value} onChange={(e) => onChange(e.target.value)}
               type={sichtbar ? "text" : "password"} autoComplete={autoComplete}
               placeholder={placeholder || `Passwort (mind. ${PASSWORT_MIN} Zeichen, Ziffer oder Sonderzeichen) *`}
               data-testid={testid} className={className} style={style} autoFocus={autoFocus}
               spellCheck={false} autoCapitalize="none" />
        <button type="button" onClick={() => setSichtbar((s) => !s)}
                aria-label={sichtbar ? "Passwort verbergen" : "Passwort anzeigen"}
                title={sichtbar ? "Passwort verbergen" : "Passwort anzeigen"}
                data-testid={testid ? `${testid}-anzeigen` : undefined}
                className="shrink-0 w-10 rounded-lg inline-flex items-center justify-center text-zinc-300 hover:text-white"
                style={{ background: "var(--bg-input-solid)", border: "1px solid var(--wa-12)" }}>
          {sichtbar ? <EyeOff size={15} /> : <Eye size={15} />}
        </button>
      </div>
      <button type="button" onClick={vorschlagen}
              data-testid={testid ? `${testid}-vorschlag` : undefined}
              className="mt-1.5 inline-flex items-center gap-1.5 text-[12px] text-zinc-400 hover:text-white">
        <Wand2 size={12} /> Sicheres Passwort mit 20 Zeichen vorschlagen
      </button>
    </div>
  );
}
