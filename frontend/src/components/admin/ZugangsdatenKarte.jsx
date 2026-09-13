import { useState } from "react";
import { toast } from "sonner";
import { Check, Copy, KeyRound } from "lucide-react";

/**
 * Kontonummer (13.09.2026): Zugangsdaten direkt nach der Anlage durch den
 * Betreiber. Die Kontonummer ist die Anmeldekennung (Chef '10023', Sucher
 * '10023-2', Kaeufer/Fahrer eigene Nummer); das Passwort hat der Betreiber
 * selbst vergeben und es ist danach nicht mehr abrufbar. Beim Fahrer kommt
 * der FD-Code dazu — den gibt der Fahrer seiner Firma zum Verknuepfen.
 *
 * Fest dunkel wie die uebrigen Admin-Dialoge.
 */
const ANMELDESEITE = {
  app: "/login",
  kaeufer: "/markt/login",
  fahrer: "/fahrer/login",
};

export default function ZugangsdatenKarte({ titel = "Konto angelegt", name, kontonummer,
                                            driverCode, bereich = "app", hinweis, onClose }) {
  const [kopiert, setKopiert] = useState(false);

  const kopieren = async () => {
    try {
      await navigator.clipboard.writeText(String(kontonummer || ""));
      setKopiert(true);
      toast.success("Kontonummer kopiert");
      setTimeout(() => setKopiert(false), 2000);
    } catch {
      window.prompt("Kontonummer kopieren:", String(kontonummer || ""));
    }
  };

  return (
    <div data-testid="zugangsdaten-karte">
      <div className="flex items-center gap-2 text-emerald-300 text-[13px] font-semibold">
        <Check size={16} /> {titel}
      </div>
      {name && <div className="mt-1 text-[14px] text-white font-medium">{name}</div>}

      <div className="mt-4 rounded-xl p-4"
           style={{ background: "#18181b", border: "1px solid rgba(255,255,255,0.12)" }}>
        <div className="text-[11px] uppercase tracking-wide text-zinc-500">Kontonummer</div>
        <div className="mt-1 flex items-center justify-between gap-3">
          <span className="font-mono font-black text-[32px] leading-none tracking-wide text-white tabular-nums"
                data-testid="zugangsdaten-kontonummer">
            {kontonummer || "—"}
          </span>
          <button type="button" onClick={kopieren} data-testid="zugangsdaten-kopieren"
                  className="inline-flex items-center gap-1.5 h-9 px-3 rounded-lg text-[12.5px] text-white"
                  style={{ background: "rgba(255,255,255,0.08)", border: "1px solid rgba(255,255,255,0.12)" }}>
            {kopiert ? <Check size={14} /> : <Copy size={14} />} Kopieren
          </button>
        </div>
        {driverCode && (
          <div className="mt-3 text-[12.5px] text-zinc-400">
            Fahrer-ID für die Firma:{" "}
            <span className="font-mono text-white" data-testid="zugangsdaten-fahrer-code">{driverCode}</span>
          </div>
        )}
        <div className="mt-3 text-[12px] text-zinc-500">
          Anmeldung unter <span className="font-mono text-zinc-300">{ANMELDESEITE[bereich] || "/login"}</span>
        </div>
      </div>

      <div className="mt-3 flex items-start gap-2 text-[12px] text-zinc-400">
        <KeyRound size={13} className="mt-0.5 shrink-0" />
        <span>
          Passwort hast du vergeben – es ist nicht erneut abrufbar. Kontonummer und
          Passwort jetzt an den Kontakt weitergeben.
          {hinweis ? ` ${hinweis}` : ""}
        </span>
      </div>

      {onClose && (
        <button type="button" onClick={onClose} data-testid="zugangsdaten-fertig"
                className="mt-4 w-full h-10 rounded-xl text-[14px] font-medium text-white"
                style={{ background: "var(--accent-red)" }}>
          Fertig
        </button>
      )}
    </div>
  );
}
