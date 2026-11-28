/**
 * Farbwahl der App (Wunsch Ahmad 02.10.2026): zehn Farben plus Standard. Ein Klick zeigt die Farbe sofort,
 * „Speichern“ legt sie am Konto ab (gilt auf jedem Gerät); „Verwerfen“ geht auf die gespeicherte zurück.
 */
import { useEffect, useState } from "react";
import { Check, Palette, Save, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { errMsg } from "@/lib/api";
import { AKZENTFARBEN, akzentAnwenden, akzentEintrag, akzentMerken, akzentSchluessel, gespeicherterAkzent } from "@/lib/akzent";

export default function AkzentWahl({ aktuell, speichern, titel = "Farbe der App" }) {
  const gespeichert = akzentSchluessel(aktuell ?? gespeicherterAkzent());
  const [wahl, setWahl] = useState(gespeichert);
  const [busy, setBusy] = useState(false);
  // Konto-Wert nachgeladen (useAuth/useDriver): Auswahl folgt, solange nichts angeklickt wurde
  useEffect(() => { setWahl(gespeichert); }, [gespeichert]);
  const geaendert = wahl !== gespeichert;

  const waehlen = (key) => {
    setWahl(key);
    akzentAnwenden(key);
  };
  const verwerfen = () => {
    setWahl(gespeichert);
    akzentAnwenden(gespeichert);
  };
  const sichern = async () => {
    setBusy(true);
    try {
      await speichern?.(wahl);
      akzentMerken(wahl);
      toast.success(`Farbe gespeichert: ${akzentEintrag(wahl).label}`);
    } catch (e) {
      toast.error(errMsg(e, "Farbe konnte nicht gespeichert werden"));
    } finally {
      setBusy(false);
    }
  };
  const dunkel = typeof document !== "undefined" && document.documentElement.getAttribute("data-theme") !== "light";

  return (
    <div className="apple-surface-gloss p-4 lg:p-5" data-testid="akzent-wahl">
      <div className="font-display font-bold text-lg tracking-tight flex items-center gap-2"><Palette size={18} /> {titel}</div>
      <div className="text-[12.5px] text-zinc-500 mt-1">
        Alles, was heute rot hervorgehoben ist, und die blauen Hauptknöpfe bekommen die gewählte Farbe. Ein Klick zeigt sie
        sofort — gespeichert wird sie erst mit „Speichern“ und gilt dann auf jedem Gerät, auf dem du angemeldet bist.
      </div>
      <div className="mt-4 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
        {AKZENTFARBEN.map((f) => {
          const [haupt, zweit] = dunkel ? f.dunkel : f.hell;
          const aktiv = wahl === f.key;
          return (
            <button key={f.key} type="button" onClick={() => waehlen(f.key)} data-testid={`akzent-${f.key}`}
                    aria-pressed={aktiv}
                    className="flex items-center gap-3 rounded-xl px-3 py-2.5 text-left text-[13px] min-h-[48px]"
                    style={{ background: "var(--wa-03)",
                             border: `2px solid ${aktiv ? haupt : "var(--wa-06)"}` }}>
              <span className="w-7 h-7 rounded-full shrink-0 flex items-center justify-center"
                    style={{ background: f.key === "standard" ? `linear-gradient(135deg, ${haupt} 50%, ${zweit} 50%)` : haupt,
                             color: "#fff" }}>
                {aktiv && <Check size={14} />}
              </span>
              <span className="leading-tight">{f.label}</span>
            </button>
          );
        })}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button type="button" onClick={sichern} disabled={busy || !geaendert} className="apple-btn apple-btn-primary disabled:opacity-60"
                data-testid="akzent-speichern">
          <Save size={14} /> {busy ? "Speichere…" : "Speichern"}
        </button>
        {geaendert && (
          <button type="button" onClick={verwerfen} disabled={busy} className="apple-btn apple-btn-secondary" data-testid="akzent-verwerfen">
            <Undo2 size={14} /> Verwerfen
          </button>
        )}
        <span className="text-[12px] text-zinc-500" data-testid="akzent-stand">
          {geaendert ? `Vorschau: ${akzentEintrag(wahl).label} — noch nicht gespeichert` : `Gespeichert: ${akzentEintrag(gespeichert).label}`}
        </span>
      </div>
    </div>
  );
}
