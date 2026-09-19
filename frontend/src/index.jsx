import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import App from "@/App";
import { applyStoredTheme } from "@/components/ThemeToggle";
import { installErrorReporter } from "@/lib/errorReporter";

applyStoredTheme();
installErrorReporter();

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
