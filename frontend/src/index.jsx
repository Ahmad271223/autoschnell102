import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import App from "@/App";
import { applyStoredTheme } from "@/components/ThemeToggle";
import { FehlerGrenze } from "@/components/NachladeFehler";
import { installErrorReporter } from "@/lib/errorReporter";

// Pruefbericht 20.09.2026 (K-07): erst der Fehlermelder, dann das Design —
// wirft der Speicherzugriff doch einmal, wird es wenigstens gemeldet.
installErrorReporter();
applyStoredTheme();

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    {/* Pruefbericht 20.09.2026 (U-150): zweite, routerlose Fehlergrenze —
        Kontexte, Router, Toaster und FassungsHinweis liegen ausserhalb der
        Grenze in App.jsx; ein Fehler dort ergab eine weisse Seite. */}
    <FehlerGrenze>
      <App />
    </FehlerGrenze>
  </React.StrictMode>,
);
