// ESLint (seit dem Vite-Umstieg 09/2026 eigener Schritt; unter CRA lief es im
// Build mit): React-Hook-Regeln wie zuvor in craco.config.js. In CI mit
// --max-warnings=0 — Warnungen sind Fehler (Audit 09/2026, Punkt 50).
import hooks from "eslint-plugin-react-hooks";
import react from "eslint-plugin-react";
import globals from "globals";

export default [
  {
    files: ["src/**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { ...globals.browser, ...globals.node, ...globals.vitest },
    },
    plugins: { "react-hooks": hooks, react },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      // Pruefbericht 20.09.2026: Zwei Namen waren gar nicht definiert
      // (errMsg im Vertragsarchiv, gekuerzt in der Terminliste) — beides
      // Abstuerze erst zur Laufzeit. Diese Regeln fangen das beim Pruefen.
      "no-undef": "error",
      "react/jsx-no-undef": "error",
    },
  },
];
