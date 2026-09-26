import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Check, ClipboardCopy, Copy, KeyRound } from "lucide-react";
import { ungespeichertMelden } from "@/lib/ungespeichert";

/**
 * Kontonummer (13.09.2026): Zugangsdaten direkt nach der Anlage durch den
 * Betreiber. Die Kontonummer ist die Anmeldekennung (Chef '10023', Sucher
 * '10023-2', Fahrer eigene Nummer, Zwischenhaendler seit 14.09.2026 ein
 * Kaeufer-Code wie '6FE7K2M'); das Passwort hat der Betreiber selbst vergeben.
 * 14.09.2026 (Wunsch Ahmad, "Kontonummer und Passwort muessen immer klappen"):
 * das eben vergebene Passwort wird hier noch einmal im Klartext gezeigt —
 * zum Abgleich und Kopieren — und ist nach dem Schliessen nicht mehr abrufbar.
 * Beim Fahrer kommt der FD-Code dazu — den gibt der Fahrer seiner Firma zum
 * Verknuepfen.
 *
 * Fest dunkel wie die uebrigen Admin-Dialoge.
 */
const ANMELDESEITE = {
  app: "/login",
  kaeufer: "/markt/login",
  fahrer: "/fahrer/login",
};

export default function ZugangsdatenKarte({ titel = "Konto angelegt", name, kontonummer,
                                            driverCode, bereich = "app", hinweis, passwort, onClose }) {
  const [kopiert, setKopiert] = useState("");
  // Pruefbericht 20.09.2026 (AD-05/O2): Kontonummer und Passwort lebten nur in
  // dieser Karte. F5 oder ein Seitenwechsel verwarfen sie ohne Rueckfrage —
  // danach existierte das Konto, aber niemand kannte das Passwort. Solange ein
  // Passwort angezeigt wird, fragt der Browser jetzt vor dem Verlassen nach,
  // und "Fertig" fragt nach, wenn noch nichts kopiert wurde.
  const [gesichert, setGesichert] = useState(false);
  useEffect(() => {
    if (!passwort || gesichert) return undefined;
    return ungespeichertMelden();
  }, [passwort, gesichert]);

  const anmeldeAdresse = `${window.location.origin}${ANMELDESEITE[bereich] || "/login"}`;
  const kopieren = async (was, wert, { alles = false } = {}) => {
    try {
      await navigator.clipboard.writeText(String(wert || ""));
      setKopiert(was);
      if (alles || !passwort || was === "Passwort") setGesichert(true);
      toast.success(`${was} kopiert`);
      setTimeout(() => setKopiert(""), 2000);
    } catch {
      window.prompt(`${was} kopieren:`, String(wert || ""));
    }
  };
  const allesText = [
    name ? `Konto: ${name}` : null,
    `Kontonummer: ${kontonummer || "—"}`,
    passwort ? `Passwort: ${passwort}` : null,
    driverCode && driverCode !== kontonummer ? `Fahrer-ID: ${driverCode}` : null,
    `Anmeldung: ${anmeldeAdresse}`,
  ].filter(Boolean).join("\n");
  const fertig = () => {
    if (passwort && !gesichert && !window.confirm(
      "Passwort notiert? Nach dem Schließen ist es nicht mehr abrufbar — "
      + "dann hilft nur „Passwort setzen“.")) return;
    setGesichert(true);
    onClose?.();
  };

  return (
    <div data-testid="zugangsdaten-karte">
      <div className="flex items-center gap-2 text-emerald-300 text-[13px] font-semibold">
        <Check size={16} /> {titel}
      </div>
      {name && <div className="mt-1 text-[14px] text-white font-medium">{name}</div>}

      <div className="mt-4 rounded-xl p-4"
           style={{ background: "var(--bg-input-solid)", border: "1px solid var(--wa-12)" }}>
        <div className="text-[11px] uppercase tracking-wide text-zinc-500">Kontonummer</div>
        <div className="mt-1 flex items-center justify-between gap-3">
          <span className="font-mono font-black text-[32px] leading-none tracking-wide text-white tabular-nums"
                data-testid="zugangsdaten-kontonummer">
            {kontonummer || "—"}
          </span>
          <button type="button" onClick={() => kopieren("Kontonummer", kontonummer)} data-testid="zugangsdaten-kopieren"
                  className="inline-flex items-center gap-1.5 h-9 px-3 rounded-lg text-[12.5px] text-white"
                  style={{ background: "var(--wa-08)", border: "1px solid var(--wa-12)" }}>
            {kopiert === "Kontonummer" ? <Check size={14} /> : <Copy size={14} />} Kopieren
          </button>
        </div>
        {passwort && (
          <div className="mt-4">
            <div className="text-[11px] uppercase tracking-wide text-zinc-500">Passwort (nur jetzt sichtbar)</div>
            <div className="mt-1 flex items-center justify-between gap-3">
              <span className="font-mono text-[17px] text-white break-all" data-testid="zugangsdaten-passwort">
                {passwort}
              </span>
              <button type="button" onClick={() => kopieren("Passwort", passwort)} data-testid="zugangsdaten-passwort-kopieren"
                      className="inline-flex items-center gap-1.5 h-9 px-3 rounded-lg text-[12.5px] text-white shrink-0"
                      style={{ background: "var(--wa-08)", border: "1px solid var(--wa-12)" }}>
                {kopiert === "Passwort" ? <Check size={14} /> : <Copy size={14} />} Kopieren
              </button>
            </div>
          </div>
        )}
        {driverCode && (
          <div className="mt-3 text-[12.5px] text-zinc-400">
            {driverCode === kontonummer
              ? "Die Fahrer-ID ist zugleich die Kontonummer — damit verknüpft die Firma den Fahrer: "
              : "Fahrer-ID für die Firma: "}
            <span className="font-mono text-white" data-testid="zugangsdaten-fahrer-code">{driverCode}</span>
          </div>
        )}
        <div className="mt-3 text-[12px] text-zinc-500">
          Anmeldung unter <span className="font-mono text-zinc-300">{ANMELDESEITE[bereich] || "/login"}</span>
        </div>
      </div>

      <button type="button" onClick={() => kopieren("Zugangsdaten", allesText, { alles: true })}
              data-testid="zugangsdaten-alles-kopieren"
              className="mt-3 w-full inline-flex items-center justify-center gap-2 h-10 rounded-xl text-[13px] font-medium text-white"
              style={{ background: "var(--wa-08)", border: "1px solid var(--wa-12)" }}>
        {kopiert === "Zugangsdaten" ? <Check size={15} /> : <ClipboardCopy size={15} />}
        Alle Zugangsdaten kopieren (Kontonummer, Passwort, Anmeldeseite)
      </button>

      <div className="mt-3 flex items-start gap-2 text-[12px] text-zinc-400">
        <KeyRound size={13} className="mt-0.5 shrink-0" />
        <span>
          Passwort hast du vergeben – nach dem Schließen ist es nicht mehr abrufbar. Kontonummer und
          Passwort jetzt an den Kontakt weitergeben; bei Problemen „Passwort setzen“.
          {hinweis ? ` ${hinweis}` : ""}
        </span>
      </div>

      {onClose && (
        <button type="button" onClick={fertig} data-testid="zugangsdaten-fertig"
                className="mt-4 w-full h-10 rounded-xl text-[14px] font-medium text-white"
                style={{ background: "var(--accent-red)" }}>
          Fertig
        </button>
      )}
    </div>
  );
}
