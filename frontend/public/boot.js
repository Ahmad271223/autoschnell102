// Runde 5: Bootstrap-Skripte aus index.html ausgelagert, damit die CSP
// script-src auf 'self' bleiben kann (kein 'unsafe-inline').
window.addEventListener("error",function(e){if(e.error instanceof DOMException&&e.error.name==="DataCloneError"&&e.message&&e.message.includes("PerformanceServerTiming")){e.stopImmediatePropagation();e.preventDefault()}},true);

// App installieren (09/2026): Chrome und Edge melden "installierbar" genau
// EINMAL je Seitenaufruf (beforeinstallprompt) — oft bevor die Oberflaeche
// geladen ist. Dieses Skript laeuft vor allem anderen und merkt sich das
// Ereignis; src/lib/installation.js holt es hier ab.
window.addEventListener("beforeinstallprompt", function (e) {
  e.preventDefault();
  window.__ahInstallation = e;
});
window.addEventListener("appinstalled", function () {
  window.__ahInstallation = null;
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", function () {
    // updateViaCache "none": beim Pruefen auf eine neue Fassung fragt der
    // Browser immer den Server, nie seinen eigenen Zwischenspeicher.
    navigator.serviceWorker.register("/service-worker.js", { updateViaCache: "none" }).catch(function () {});
  });
}
