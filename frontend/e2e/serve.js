#!/usr/bin/env node
/*
 * Statischer Server fuer den Produktions-Build (SPA-Fallback auf index.html)
 * plus /api-Proxy zum Backend — nur Node-Bordmittel, keine Abhaengigkeit.
 *
 * Warum kein "npx serve": Seite und API muessen unter DERSELBEN Herkunft
 * laufen, genau wie in Produktion (nginx reicht /api an das Backend weiter).
 * Sonst braeuchte das Backend eine CORS-Freigabe fuer den Test-Port und die
 * CSP (connect-src 'self') im index.html eine Ausnahme. Der Build wird dafuer
 * mit leerem REACT_APP_BACKEND_URL erzeugt (api.js -> "/api").
 *
 *   E2E_BASE_URL  Adresse dieses Servers (Port), Default http://localhost:3100
 *   E2E_API_URL   Backend-API, Default http://localhost:8002/api
 */
const http = require("http");
const fs = require("fs");
const path = require("path");

const BUILD = path.resolve(__dirname, "..", "build");
const base = new URL(process.env.E2E_BASE_URL || "http://localhost:3100");
const PORT = Number(base.port || 3100);
const apiUrl = new URL(process.env.E2E_API_URL || "http://localhost:8002/api");
const API_PREFIX = apiUrl.pathname.replace(/\/+$/, "") || "/api";
const TARGET = {
  // "localhost" kann unter Windows/Node auf ::1 zeigen, uvicorn lauscht auf IPv4.
  host: apiUrl.hostname === "localhost" ? "127.0.0.1" : apiUrl.hostname,
  port: Number(apiUrl.port || (apiUrl.protocol === "https:" ? 443 : 80)),
};

if (!fs.existsSync(path.join(BUILD, "index.html"))) {
  console.error(`[e2e-serve] Kein Produktions-Build unter ${BUILD} — zuerst "yarn build" ausfuehren.`);
  process.exit(1);
}

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".webmanifest": "application/manifest+json",
  ".map": "application/json",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".txt": "text/plain; charset=utf-8",
};

function sendFile(res, file) {
  const ext = path.extname(file).toLowerCase();
  res.writeHead(200, {
    "Content-Type": MIME[ext] || "application/octet-stream",
    "Cache-Control": "no-store",
  });
  const stream = fs.createReadStream(file);
  stream.on("error", () => { res.statusCode = 500; res.end(); });
  stream.pipe(res);
}

function serveStatic(req, res) {
  let pathname = "/";
  try { pathname = decodeURIComponent(new URL(req.url, "http://localhost").pathname); } catch { /* ignore */ }
  const file = path.normalize(path.join(BUILD, pathname));
  if (!file.startsWith(BUILD)) { res.writeHead(403); res.end(); return; }
  fs.stat(file, (err, st) => {
    // Unbekannte Pfade (Client-Routing) -> index.html
    sendFile(res, !err && st.isFile() ? file : path.join(BUILD, "index.html"));
  });
}

function proxy(req, res) {
  const headers = { ...req.headers, host: `${TARGET.host}:${TARGET.port}` };
  const upstream = http.request(
    { host: TARGET.host, port: TARGET.port, method: req.method, path: req.url, headers },
    (r) => { res.writeHead(r.statusCode || 502, r.headers); r.pipe(res); },
  );
  upstream.on("error", (e) => {
    res.writeHead(502, { "Content-Type": "text/plain; charset=utf-8" });
    res.end(`Backend nicht erreichbar (${TARGET.host}:${TARGET.port}): ${e.message}`);
  });
  req.pipe(upstream);
}

const isApi = (url) => url === API_PREFIX || url.startsWith(`${API_PREFIX}/`) || url.startsWith(`${API_PREFIX}?`);

http.createServer((req, res) => (isApi(req.url || "/") ? proxy(req, res) : serveStatic(req, res)))
  .listen(PORT, () => {
    console.log(`[e2e-serve] http://localhost:${PORT}  Build: ${BUILD}  ${API_PREFIX} -> http://${TARGET.host}:${TARGET.port}${API_PREFIX}`);
  });
