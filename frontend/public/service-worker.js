/* Minimal Service Worker — nur zur PWA-Installation nötig.
 * Kein aggressives Caching, damit App-Updates sofort greifen. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => { /* network-first by default */ });
