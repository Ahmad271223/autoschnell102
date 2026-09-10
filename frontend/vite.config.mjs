// Vite (ersetzt CRA/craco, 09/2026).
//
// Bewusst gleich geblieben, damit Docker, nginx-Cache-Regeln, der E2E-Server
// (e2e/serve.js) und die Aussenprobe beim Rollout (betriebsprobe.py) nichts
// merken:
//   * Ausgabe nach build/, Dateien unter static/js, static/css, static/media
//     und das Hauptskript heisst weiter static/js/main.<hash>.js
//   * REACT_APP_BACKEND_URL (Build-Argument/.env) wie bisher
//   * CSP-Platzhalter %REACT_APP_CSP_SCRIPT% / %REACT_APP_CSP_CONNECT% in
//     index.html: in Produktion leer (script-src streng 'self'), im
//     Entwicklungsserver eval/inline (React-Refresh) und localhost:8001
//   * /api wird im Entwicklungsserver an das Backend (Port 8001) weitergereicht
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

const WURZEL = path.dirname(fileURLToPath(import.meta.url));

function cspPlatzhalter(werte) {
  return {
    name: "autoschnell-csp-platzhalter",
    transformIndexHtml: {
      order: "pre",
      handler(html) {
        let aus = html;
        for (const [name, wert] of Object.entries(werte)) {
          aus = aus.replaceAll(`%${name}%`, wert);
        }
        return aus;
      },
    },
  };
}

function dateiname(info) {
  const name = (info.names && info.names[0]) || info.name || "";
  return name.endsWith(".css")
    ? "static/css/[name].[hash][extname]"
    : "static/media/[name].[hash][extname]";
}

export default defineConfig(({ command, mode }) => {
  // loadEnv liest .env-Dateien; bereits gesetzte Umgebungsvariablen
  // (Docker-Build-Argument, CI) haben Vorrang.
  const env = loadEnv(mode, WURZEL, "REACT_APP_");
  const dev = command === "serve";
  const werte = {
    REACT_APP_CSP_CONNECT: env.REACT_APP_CSP_CONNECT ?? (dev ? "http://localhost:8001" : ""),
    REACT_APP_CSP_SCRIPT: env.REACT_APP_CSP_SCRIPT ?? (dev ? "'unsafe-eval' 'unsafe-inline'" : ""),
  };

  return {
    plugins: [react(), cspPlatzhalter(werte)],
    resolve: {
      alias: { "@": path.resolve(WURZEL, "src") },
    },
    define: {
      // Der Code liest die Backend-Adresse wie unter CRA; leer = gleiche
      // Herkunft (nginx reicht /api an das Backend weiter).
      "process.env.REACT_APP_BACKEND_URL": JSON.stringify(env.REACT_APP_BACKEND_URL ?? ""),
    },
    server: {
      host: true,
      port: Number(process.env.PORT) || 3000,
      strictPort: true,
      // Zugriff ueber LAN-IP oder Tunnel (ngrok) wie bisher erlaubt.
      allowedHosts: true,
      proxy: {
        "/api": {
          target: process.env.BACKEND_PROXY_TARGET || "http://127.0.0.1:8001",
          changeOrigin: true,
          // Keep-Alive: sonst schneidet uvicorn (Windows) grosse PDF-Antworten ab.
          agent: new http.Agent({ keepAlive: true }),
        },
      },
    },
    preview: {
      port: Number(process.env.PORT) || 3000,
    },
    build: {
      outDir: "build",
      emptyOutDir: true,
      assetsDir: "static",
      sourcemap: false,
      // Grosse Seiten laden erst bei Bedarf nach (App.jsx, React.lazy).
      chunkSizeWarningLimit: 700,
      rollupOptions: {
        output: {
          entryFileNames: "static/js/main.[hash].js",
          chunkFileNames: "static/js/[name].[hash].chunk.js",
          assetFileNames: dateiname,
          // Kleinstteile (einzelne Symbole) wieder zusammenfuehren: weniger
          // Anfragen, ohne den ersten Aufruf groesser zu machen.
          experimentalMinChunkSize: 10_000,
          // Bibliotheken in eigene, lange gecachte Dateien: nach einem
          // Update laedt der Browser nur den eigenen Code neu.
          manualChunks(id) {
            if (!id.includes("node_modules")) return undefined;
            if (/[\\/]node_modules[\\/](react|react-dom|scheduler|react-router|react-router-dom)[\\/]/.test(id)) {
              return "react";
            }
            // Uebrige Bibliotheken verteilt Vite auf die Seiten, die sie
            // wirklich nutzen — nichts davon wird unnoetig sofort geladen.
            return undefined;
          },
        },
      },
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/setupTests.js"],
      include: ["src/**/*.test.{js,jsx}"],
    },
  };
});
