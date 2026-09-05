// Runde 5: Bootstrap-Skripte aus index.html ausgelagert, damit die CSP
// script-src auf 'self' bleiben kann (kein 'unsafe-inline').
window.addEventListener("error",function(e){if(e.error instanceof DOMException&&e.error.name==="DataCloneError"&&e.message&&e.message.includes("PerformanceServerTiming")){e.stopImmediatePropagation();e.preventDefault()}},true);

if ("serviceWorker" in navigator) {
            window.addEventListener("load", () => {
              navigator.serviceWorker.register("/service-worker.js").catch(() => {});
            });
          }
