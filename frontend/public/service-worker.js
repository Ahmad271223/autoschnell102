/*
 * Service Worker von AutoSchnell — v3 (09/2026, "App installieren").
 *
 * Zwei Aufgaben:
 *  1. Die Seite ist als App installierbar. Chrome und Edge zeigen ihren
 *     Installieren-Dialog (beforeinstallprompt) nur, wenn der Service Worker
 *     einen fetch-Handler hat — v2 hatte ihn entfernt, deshalb kam der
 *     Knopf dort nie.
 *  2. Faellt das Netz weg (Fahrer unterwegs, Funkloch), erscheint eine klare
 *     Meldung mit "Erneut versuchen" statt der Fehlerseite des Browsers.
 *
 * Bewusst OHNE Zwischenspeicher: Seiten, Skripte und Daten kommen immer
 * frisch vom Server. Nach einem Update sieht jeder sofort die neue Version
 * (das war der Grund fuer v2).
 *  - Nur Seitenaufrufe (Navigation, GET) behandelt der Handler. Alles andere
 *    (API, Bilder, Skripte) schicken Chrome/Edge ab Version 126 per Static
 *    Routing ganz am Service Worker vorbei ans Netz — sonst muesste die
 *    erste Anfrage nach einer Pause (der Browser beendet den Worker nach
 *    ~30 s Leerlauf) erst auf seinen Start warten. Aeltere Browser, Safari
 *    und Firefox kennen das nicht; dort reicht der Handler diese Anfragen
 *    ohne Umweg durch (return ohne respondWith).
 *  - Navigation Preload: der Browser startet den Seitenabruf schon, waehrend
 *    der Service Worker hochfaehrt — kein Zeitverlust beim Oeffnen.
 *  - Die Offline-Seite kommt NUR, wenn die Anfrage gar nicht durchgeht.
 *    Antwortet der Server mit einem Fehler (502, 503), sieht man diesen.
 *
 * Notbremse: Muss der Service Worker weg, diese Datei durch eine Fassung
 * ersetzen, die sich selbst abmeldet (self.registration.unregister()).
 * Sie wird ohne Zwischenspeicher ausgeliefert (nginx: no-cache, Anmeldung
 * mit updateViaCache "none") und greift beim naechsten Seitenaufruf.
 * Siehe DEPLOYMENT.md, "Installierbare App".
 */

// Die Offline-Seite prueft alle 10 s selbst, ob der Server wieder antwortet:
// "online" feuert nur, wenn das Geraet sein Netz wiederfindet — nicht im
// WLAN ohne Internet, hinter einem Hotel-Portal oder bei DNS-Ausfall.
const OFFLINE_SEITE = `<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0A0A0A">
<title>AutoSchnell – keine Verbindung</title>
<style>
html,body{margin:0;height:100%;background:#0a0a0a;color:#f4f4f5;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{min-height:100%;box-sizing:border-box;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px;text-align:center}
svg{width:64px;height:64px;margin-bottom:20px}
h1{font-size:22px;margin:0 0 8px}
p{color:#a1a1aa;max-width:340px;line-height:1.5;margin:0 0 24px}
a{background:#ff3b30;color:#fff;text-decoration:none;font-weight:600;padding:12px 22px;border-radius:6px}
</style></head>
<body><main>
<svg viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="5" fill="#ff3b30"/><g transform="translate(3.36 3.36) scale(0.72)" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><circle cx="12" cy="12" r="4"/></g></svg>
<h1>Keine Internetverbindung</h1>
<p>AutoSchnell ist gerade nicht erreichbar. Prüfe WLAN oder mobile Daten – sobald die Verbindung wieder steht, lädt die Seite von selbst neu.</p>
<a href="">Erneut versuchen</a>
</main>
<script>
addEventListener("online", function () { location.reload(); });
setInterval(function () {
  if (document.visibilityState !== "visible") return;
  fetch(location.href, { method: "HEAD", cache: "no-store" }).then(function () { location.reload(); }, function () {});
}, 10000);
</script>
</body></html>`;

// Static Routing (Chrome/Edge): alles ausser Seitenaufrufen direkt ans Netz.
// Die Bedingung "not" kennt nicht jede Version — dann die gleichwertige
// "or"-Fassung; kennt der Browser beides nicht, bleibt es beim Handler.
async function routenSetzen(event) {
  const regeln = [
    { condition: { not: { requestMode: "navigate" } }, source: "network" },
    {
      condition: { or: [{ requestMode: "cors" }, { requestMode: "no-cors" }, { requestMode: "same-origin" }] },
      source: "network",
    },
  ];
  for (const regel of regeln) {
    try {
      await event.addRoutes(regel);
      return;
    } catch { /* diese Fassung kennt der Browser nicht */ }
  }
}

self.addEventListener("install", (event) => {
  self.skipWaiting();
  if (typeof event.addRoutes === "function") event.waitUntil(routenSetzen(event));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    // Reste frueherer Fassungen (v1 hatte einen Zwischenspeicher) entfernen.
    const namen = await caches.keys();
    await Promise.all(namen.map((name) => caches.delete(name)));
    if (self.registration.navigationPreload) {
      try { await self.registration.navigationPreload.enable(); } catch { /* aelterer Browser */ }
    }
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const anfrage = event.request;
  if (anfrage.mode !== "navigate" || anfrage.method !== "GET") return;
  event.respondWith((async () => {
    try {
      const vorab = await event.preloadResponse;
      if (vorab) return vorab;
      return await fetch(anfrage);
    } catch {
      return new Response(OFFLINE_SEITE, {
        status: 503,
        headers: {
          "Content-Type": "text/html; charset=utf-8",
          "Cache-Control": "no-store",
          "Content-Security-Policy":
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'",
        },
      });
    }
  })());
});
