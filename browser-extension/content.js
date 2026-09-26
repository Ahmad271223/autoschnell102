// Brücke zwischen der AutoSchnell-Web-App und dem Abruf-Helfer.
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
