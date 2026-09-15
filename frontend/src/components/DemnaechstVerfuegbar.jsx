import { Link } from "react-router-dom";
import { Clock } from "lucide-react";

/**
 * Platzhalter fuer abgeschaltete Bereiche (Go-Live-Schalter 15.09.2026):
 * Marktplatz und Inserieren kommen spaeter — die Seite sagt das klar, statt
 * eine halbe Oberflaeche oder einen Serverfehler zu zeigen.
 */
export default function DemnaechstVerfuegbar({ bereich = "Dieser Bereich", eingebettet = false, zurueck = "/" }) {
  const inhalt = (
    <div className="tactical-card p-8 text-center" data-testid="demnaechst-verfuegbar">
      <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full"
           style={{ background: "var(--wa-06)" }}>
        <Clock size={22} />
      </div>
      <h1 className="font-display font-black text-2xl">Demnächst verfügbar</h1>
      <p className="mt-3 text-sm text-zinc-400">
        {bereich} wird gerade fertiggestellt. Sobald es so weit ist, findest du es genau hier.
      </p>
      {!eingebettet && (
        <Link to={zurueck} className="inline-block mt-6 text-sm text-zinc-300 hover:text-white underline">
          Zurück
        </Link>
      )}
    </div>
  );
  if (eingebettet) return inhalt;
  return (
    <div className="min-h-screen flex items-center justify-center px-4" style={{ background: "var(--bg-page)" }}>
      <div className="w-full max-w-md">{inhalt}</div>
    </div>
  );
}
