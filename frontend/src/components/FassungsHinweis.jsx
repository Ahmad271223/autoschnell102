import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { RefreshCw, X } from "lucide-react";
import { fassungAbonnieren, fassungSchonGeladen, neueFassungLaden, veralteteFassung } from "@/lib/fassung";

/**
 * Runde 31 (12.09.2026): Hinweis "Neue Version verfuegbar".
 *
 * Zwei Wege zur neuen Fassung, beide ohne Datenverlust:
 *  - Klick auf "Aktualisieren" (liegt noch etwas Ungespeichertes vor,
 *    fragt der Browser vorher nach — lib/ungespeichert.js).
 *  - Beim naechsten Seitenwechsel still: Die alte Seite wird dabei ohnehin
 *    verlassen, statt des Wechsels im Speicher laedt der Browser die
 *    Zielseite frisch. Geht nur einmal je Fassung, und nie, solange etwas
 *    Ungespeichertes gemeldet ist.
 *
 * Pruefbericht 20.09.2026 (K-09): Der Klick lief an neueFassungLaden vorbei
 * (nur reload, ohne Merker). Landete das Neuladen im Rollout auf dem alten
 * Server, kam das Band mit der naechsten Antwort unveraendert zurueck. Jetzt
 * setzt auch der Klick den Merker; traegt er schon diese Fassung, steht
 * "wird gerade verteilt" — der Knopf laedt dann schlicht neu (Klick = Geste
 * des Nutzers, Rueckfrage bei Ungespeichertem stellt der Browser).
 */
export default function FassungsHinweis() {
  const [stand, setStand] = useState(veralteteFassung);
  const [spaeter, setSpaeter] = useState("");
  const { pathname, search, hash } = useLocation();
  const vorher = useRef(pathname);

  useEffect(() => fassungAbonnieren(setStand), []);

  useEffect(() => {
    if (vorher.current === pathname) return;
    vorher.current = pathname;
    neueFassungLaden(pathname + search + hash);
  }, [pathname, search, hash]);

  if (!stand) return null;
  const kennung = stand.fassung || stand.grund;
  if (spaeter === kennung) return null;
  const verteilt = fassungSchonGeladen();
  const aktualisieren = () => {
    if (!neueFassungLaden(pathname + search + hash)) window.location.reload();
  };

  return (
    <div role="status" data-testid="fassungs-hinweis"
         className="fixed left-1/2 -translate-x-1/2 z-[70] flex items-center gap-2.5 pl-3.5 pr-1.5 py-1.5 rounded-full text-[13px] shadow-lg"
         style={{
           top: "calc(env(safe-area-inset-top, 0px) + 4.25rem)",
           maxWidth: "calc(100vw - 1.5rem)",
           background: "var(--bg-elevated)",
           border: "1px solid var(--wa-12)",
           color: "var(--text-strong)",
         }}>
      <RefreshCw size={14} className="shrink-0" style={{ color: "var(--accent-red)" }} />
      {/* Kein nowrap: auf Handybreite muss der laengere "verteilt"-Text
          umbrechen, sonst rutscht "Spaeter" aus dem Bild (E2E fassung.spec). */}
      <span className="min-w-0 leading-tight" data-testid="fassungs-hinweis-text">
        {verteilt ? "Neue Version wird gerade verteilt" : "Neue Version verfügbar"}
      </span>
      <button type="button" data-testid="fassungs-hinweis-laden"
              onClick={aktualisieren}
              className="rounded-full px-3 py-1 text-[12.5px] font-semibold text-white whitespace-nowrap"
              style={{ background: "var(--accent-red)" }}>
        Aktualisieren
      </button>
      <button type="button" aria-label="Später" title="Später"
              onClick={() => setSpaeter(kennung)}
              className="p-1.5 rounded-full text-zinc-400 hover:text-white">
        <X size={14} />
      </button>
    </div>
  );
}
