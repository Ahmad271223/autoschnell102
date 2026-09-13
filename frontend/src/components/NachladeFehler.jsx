import { Component } from "react";
import { useLocation } from "react-router-dom";

// Fehlergrenze fuer nachgeladene Seiten (Vite, 09/2026): scheitert eine Seite
// endgueltig (z. B. waehrend eines Updates), erscheint ein klarer Hinweis
// mit "Neu laden" statt einer leeren Oberflaeche.
//
// Runde 31 (12.09.2026, Vorfall Super-Admin): Die Grenze setzte sich nie
// zurueck. Einmal ausgeloest, zeigte der ganze Bereich nur noch die Meldung —
// auch nach einem Klick auf "Uebersicht" oder "Nutzer", deren Dateien
// einwandfrei da waren. Jetzt beginnt jeder Seitenwechsel neu.
class Grenze extends Component {
  constructor(props) {
    super(props);
    this.state = { fehler: null };
  }

  static getDerivedStateFromError(fehler) {
    return { fehler };
  }

  componentDidUpdate(vorher) {
    if (this.state.fehler && vorher.pfad !== this.props.pfad) {
      this.setState({ fehler: null });
    }
  }

  render() {
    if (!this.state.fehler) return this.props.children;
    return (
      <div className="min-h-[60vh] flex flex-col items-center justify-center gap-3 px-6 text-center"
           data-testid="nachlade-fehler">
        <div className="text-sm" style={{ color: "var(--text-secondary)" }}>
          Diese Seite konnte gerade nicht geladen werden.
        </div>
        <div className="text-[13px] max-w-sm" style={{ color: "var(--text-muted)" }}>
          Das passiert kurz nach einem Update. „Neu laden“ holt die aktuelle
          Version — du bleibst auf dieser Seite.
        </div>
        <button type="button" className="apple-btn apple-btn-primary"
                onClick={() => window.location.reload()}>
          Neu laden
        </button>
      </div>
    );
  }
}

export default function NachladeFehler({ children }) {
  const { pathname } = useLocation();
  return <Grenze pfad={pathname}>{children}</Grenze>;
}
