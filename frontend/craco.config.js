// Runde 5 (CSP script-src 'self'): kein Inline-Runtime-Chunk im Build und die
// erlaubten API-Origins fuer connect-src — hier statt in .env-Dateien, weil die
// per .gitignore ausgeschlossen sind und CI/Docker sie sonst nie saehen.
process.env.INLINE_RUNTIME_CHUNK = process.env.INLINE_RUNTIME_CHUNK || "false";
if (process.env.REACT_APP_CSP_CONNECT === undefined) {
  process.env.REACT_APP_CSP_CONNECT =
    process.env.NODE_ENV === "production" ? "" : "http://localhost:8001";
}

// craco.config.js
const path = require("path");
require("dotenv").config();

// Check if we're in development/preview mode (not production build)
// Craco sets NODE_ENV=development for start, NODE_ENV=production for build
const isDevServer = process.env.NODE_ENV !== "production";

// Environment variable overrides
const config = {
  enableHealthCheck: process.env.ENABLE_HEALTH_CHECK === "true",
};

// Conditionally load health check modules only if enabled
let WebpackHealthPlugin;
let setupHealthEndpoints;
let healthPluginInstance;

if (config.enableHealthCheck) {
  WebpackHealthPlugin = require("./plugins/health-check/webpack-health-plugin");
  setupHealthEndpoints = require("./plugins/health-check/health-endpoints");
  healthPluginInstance = new WebpackHealthPlugin();
}

let webpackConfig = {
  eslint: {
    configure: {
      extends: ["plugin:react-hooks/recommended"],
      rules: {
        "react-hooks/rules-of-hooks": "error",
        "react-hooks/exhaustive-deps": "warn",
      },
    },
  },
  webpack: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
    configure: (webpackConfig) => {

      // Add ignored patterns to reduce watched directories
        webpackConfig.watchOptions = {
          ...webpackConfig.watchOptions,
          ignored: [
            '**/node_modules/**',
            '**/.git/**',
            '**/build/**',
            '**/dist/**',
            '**/coverage/**',
            '**/public/**',
        ],
      };

      // Add health check plugin to webpack if enabled
      if (config.enableHealthCheck && healthPluginInstance) {
        webpackConfig.plugins.push(healthPluginInstance);
      }
      return webpackConfig;
    },
  },
};

webpackConfig.devServer = (devServerConfig) => {
  // Add health check endpoints if enabled
  if (config.enableHealthCheck && setupHealthEndpoints && healthPluginInstance) {
    const originalSetupMiddlewares = devServerConfig.setupMiddlewares;

    devServerConfig.setupMiddlewares = (middlewares, devServer) => {
      // Call original setup if exists
      if (originalSetupMiddlewares) {
        middlewares = originalSetupMiddlewares(middlewares, devServer);
      }

      // Setup health endpoints
      setupHealthEndpoints(devServer, healthPluginInstance);

      return middlewares;
    };
  }

  // --- Zugriff von anderen Rechnern (LAN / Tunnel wie ngrok) ---
  // 1. /api wird an das Backend (Port 8001) weitergereicht. Frontend und
  //    API teilen sich dadurch EINE Adresse -> kein CORS, nur EIN Tunnel.
  // 2. allowedHosts: der Dev-Server wuerde fremde Hostnamen sonst mit
  //    "Invalid Host header" abweisen (z.B. den ngrok-Hostnamen).
  // 3. webSocketURL "auto": Live-Reload findet den richtigen Host selbst,
  //    egal ob localhost, LAN-IP oder https-Tunnel.
  devServerConfig.allowedHosts = "all";
  // compress: false — die Kompressions-Middleware des Dev-Servers
  // verstuemmelt grosse Binaer-Antworten, die durch den /api-Proxy laufen
  // (Snapshot-PDF/PNG kam mit ~10 KB weniger an als angekuendigt ->
  // Chrome bricht mit ERR_CONTENT_LENGTH_MISMATCH ab, "PDF/Foto" im
  // Beweis-Archiv blieb leer). Auf localhost bringt Kompression ohnehin
  // nichts; im Produktivbetrieb gibt es diesen Proxy nicht.
  devServerConfig.compress = false;
  devServerConfig.client = {
    ...(devServerConfig.client || {}),
    webSocketURL: "auto://0.0.0.0:0/ws",
  };
  // fixRequestBody: andere Dev-Server-Middleware liest den POST-Body vorher
  // aus; ohne diesen Fix wuerde jeder weitergereichte POST/PUT haengen.
  let fixRequestBody;
  try {
    ({ fixRequestBody } = require("http-proxy-middleware"));
  } catch (err) {
    fixRequestBody = null;
  }
  devServerConfig.proxy = {
    ...(devServerConfig.proxy || {}),
    "/api": {
      target: process.env.BACKEND_PROXY_TARGET || "http://127.0.0.1:8001",
      changeOrigin: true,
      // Keep-Alive zum Backend: ohne Agent schickt der Proxy
      // "Connection: close" — uvicorn (Windows) schliesst den Socket dann
      // sofort nach dem letzten Write und noch gepufferte Bytes gehen
      // verloren. Grosse Snapshot-PDF/PNG-Antworten kamen dadurch
      // abgeschnitten an (Chrome: ERR_CONTENT_LENGTH_MISMATCH, die
      // Beweis-Knoepfe "PDF"/"Foto" blieben leer). Mit Keep-Alive wird
      // die Verbindung nie mitten im Puffer geschlossen.
      agent: new (require("http").Agent)({ keepAlive: true }),
      ...(fixRequestBody ? { onProxyReq: fixRequestBody } : {}),
    },
  };

  return devServerConfig;
};

// Wrap with visual edits (automatically adds babel plugin, dev server, and overlay in dev mode)
if (isDevServer) {
  try {
    const { withVisualEdits } = require("@emergentbase/visual-edits/craco");
    webpackConfig = withVisualEdits(webpackConfig);
  } catch (err) {
    if (err.code === 'MODULE_NOT_FOUND' && err.message.includes('@emergentbase/visual-edits/craco')) {
      console.warn(
        "[visual-edits] @emergentbase/visual-edits not installed — visual editing disabled."
      );
    } else {
      throw err;
    }
  }
}

module.exports = webpackConfig;
