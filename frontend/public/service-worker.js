/* Minimal Service Worker — nur zur PWA-Installation nötig.
 * v2: leerer fetch-Handler entfernt (verursachte "no-op" Warnung +
 * verhinderte sofortige Updates der index.html / CSP-Meta-Tags).
 * Alle gecachten Assets werden beim Aktivieren geleert. */
const CACHE_VERSION = "v2";

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

// Kein fetch-Handler → Browser nutzt direkt das Netzwerk (network-first).
// Dadurch greifen neue index.html / CSP-Änderungen sofort ohne Cache-Busting.
