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
  // Handy-Ansicht (24.09.2026): jeder Link 44 px hoch tippbar (Polster
  // innerhalb des Links, der Text bleibt klein).
  const link = "inline-flex items-center min-h-[44px] px-1 hover:underline underline-offset-2";
  return (
    <nav aria-label="Rechtliches" data-testid="rechts-links"
         className={`flex flex-wrap items-center justify-center gap-x-2 text-[11px] ${className}`}>
      <Link to="/impressum" className={link} style={stil}>Impressum</Link>
      <span aria-hidden="true" style={stil}>·</span>
      <Link to="/datenschutz" className={link} style={stil}>Datenschutz</Link>
      <span aria-hidden="true" style={stil}>·</span>
      <Link to="/agb" className={link} style={stil}>AGB</Link>
    </nav>
  );
}
