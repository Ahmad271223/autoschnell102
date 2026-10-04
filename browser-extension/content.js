// Brücke zwischen der AutoSchnell-Web-App und dem Helfer.
// Die App spricht die Erweiterung NICHT direkt an (kennt ihre ID nicht),
// sondern über window.postMessage — dieses Content-Script leitet weiter.

// 1. Der App signalisieren, dass der Helfer installiert ist.
window.postMessage({ __autoschnell: true, type: "EXT_READY" }, "*");

// 2. Auf Abruf-Wünsche der App hören.
window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  const d = event.data;
  if (!d || d.__autoschnell !== true) return;

  if (d.type === "PING") {
    window.postMessage({ __autoschnell: true, type: "EXT_READY" }, "*");
    return;
  }

  if (d.type === "FETCH" && d.reqId) {
    chrome.runtime.sendMessage(
      { type: "AUTOSCHNELL_FETCH", url: d.url },
      (resp) => {
        window.postMessage({
          __autoschnell: true,
          type: "FETCH_RESULT",
          reqId: d.reqId,
          ok: !!(resp && resp.ok),
          html: resp && resp.html,
          error: (resp && resp.error) || (chrome.runtime.lastError && chrome.runtime.lastError.message),
        }, "*");
      }
    );
  }
});

// 3. Browser-Helfer (04.10.2026, Wunsch Ahmad "immer die App öffnen"):
//    a) Läuft diese Seite als installierte App (eigenes Fenster), merkt sich der Helfer das — dann startet er bei
//       "Kaufvertrag" die App statt einer Webseite.
//    b) Ist die App offen, schickt der Helfer das Ziel hierher; die App wechselt ohne Neuladen dorthin.
function alsApp() {
  return ["standalone", "window-controls-overlay", "minimal-ui"]
    .some((m) => window.matchMedia && window.matchMedia(`(display-mode: ${m})`).matches);
}
try {
  if (alsApp()) chrome.runtime.sendMessage({ type: "AUTOSCHNELL_APP" }, () => void chrome.runtime.lastError);
} catch (e) { /* Erweiterung neu geladen */ }

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || sender.id !== chrome.runtime.id || msg.type !== "AUTOSCHNELL_OEFFNEN") return false;
  const ziel = typeof msg.ziel === "string" ? msg.ziel : "";
  if (!/^\/app\/[a-z]/.test(ziel)) {
    sendResponse({ ok: false });
    return false;
  }
  window.postMessage({ __autoschnell: true, type: "OEFFNEN", ziel }, window.location.origin);
  sendResponse({ ok: true, app: alsApp() });
  return false;
});
