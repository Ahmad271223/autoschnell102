import { Component } from "react";

// Fehlergrenze fuer nachgeladene Seiten (Vite, 09/2026): scheitert eine Seite
// endgueltig (z. B. waehrend eines Updates), erscheint ein klarer Hinweis
// mit "Neu laden" statt einer leeren Oberflaeche.
export default class NachladeFehler extends Component {
  constructor(props) {
    super(props);
    this.state = { fehler: null };
  }

  static getDerivedStateFromError(fehler) {
    return { fehler };
  }

  render() {
    if (!this.state.fehler) return this.props.children;
    return (
      <div className="min-h-[60vh] flex flex-col items-center justify-center gap-3 px-6 text-center"
           data-testid="nachlade-fehler">
        <div className="text-sm" style={{ color: "var(--text-muted)" }}>
          Die Seite konnte nicht geladen werden — vermutlich gibt es gerade eine neue Version.
        </div>
        <button type="button" className="apple-btn apple-btn-primary"
                onClick={() => window.location.reload()}>
          Neu laden
        </button>
      </div>
    );
  }
}
