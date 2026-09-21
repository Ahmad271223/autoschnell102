import { Link } from "react-router-dom";

/**
 * Impressum, Datenschutz und AGB — von JEDER Seite aus erreichbar.
 *
 * Prüfbericht 20.09.2026 (RE-01/R4): Im angemeldeten Bereich, auf der
 * Anmeldeseite und in der Fahrer-App gab es keinen einzigen Link dorthin.
 * § 5 DDG verlangt „leicht erkennbar, unmittelbar erreichbar und ständig
 * verfügbar", Art. 13 DSGVO gilt im eingeloggten Bereich genauso.
 */
export default function RechtsLinks({ className = "" }) {
  const stil = { color: "var(--text-muted)" };
  return (
    <nav aria-label="Rechtliches" data-testid="rechts-links"
         className={`flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-[11px] ${className}`}>
      <Link to="/impressum" className="hover:underline underline-offset-2" style={stil}>Impressum</Link>
      <span aria-hidden="true" style={stil}>·</span>
      <Link to="/datenschutz" className="hover:underline underline-offset-2" style={stil}>Datenschutz</Link>
      <span aria-hidden="true" style={stil}>·</span>
      <Link to="/agb" className="hover:underline underline-offset-2" style={stil}>AGB</Link>
    </nav>
  );
}
