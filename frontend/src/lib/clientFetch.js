// Brücke zur AutoSchnell-Browser-Erweiterung (Abruf-Helfer).
// Wenn der Server bei einem NEUEN Kleinanzeigen-Link "needs_client_fetch"
// meldet, holt die Erweiterung die Seite über die Leitung des Nutzers.
// Ohne Erweiterung liefern diese Helfer ein sauberes "nicht verfügbar".

let _extReady = false;

// Die Erweiterung meldet sich beim Laden ("EXT_READY").
if (typeof window !== "undefined") {
  window.addEventListener("message", (e) => {
    if (e.source === window && e.data && e.data.__autoschnell && e.data.type === "EXT_READY") {
      _extReady = true;
    }
  });
  // Beim Start einmal anpingen (falls die Erweiterung schon da war).
  try { window.postMessage({ __autoschnell: true, type: "PING" }, "*"); } catch (_) {}
}

/** Ist der Abruf-Helfer installiert? (kurz warten, da async gemeldet) */
export function extensionReady(waitMs = 400) {
  return new Promise((resolve) => {
    if (_extReady) return resolve(true);
    try { window.postMessage({ __autoschnell: true, type: "PING" }, "*"); } catch (_) {}
    const t = setTimeout(() => resolve(_extReady), waitMs);
    const onMsg = (e) => {
      if (e.source === window && e.data && e.data.__autoschnell && e.data.type === "EXT_READY") {
        clearTimeout(t); window.removeEventListener("message", onMsg); resolve(true);
      }
    };
    window.addEventListener("message", onMsg);
  });
}

/** Lässt die Erweiterung die URL holen; liefert das HTML (oder wirft). */
export function fetchViaExtension(url, timeoutMs = 25000) {
  return new Promise((resolve, reject) => {
    const reqId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const onMsg = (e) => {
      const d = e.data;
      if (e.source !== window || !d || d.__autoschnell !== true) return;
      if (d.type === "FETCH_RESULT" && d.reqId === reqId) {
        clearTimeout(timer);
        window.removeEventListener("message", onMsg);
        if (d.ok && d.html) resolve(d.html);
        else reject(new Error(d.error || "Abruf fehlgeschlagen"));
      }
    };
    const timer = setTimeout(() => {
      window.removeEventListener("message", onMsg);
      reject(new Error("Zeitüberschreitung beim Abruf über die Erweiterung"));
    }, timeoutMs);
    window.addEventListener("message", onMsg);
    window.postMessage({ __autoschnell: true, type: "FETCH", url, reqId }, "*");
  });
}
