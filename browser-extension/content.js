// Brücke zwischen der AutoSchnell-Web-App und dem Helfer.
// Die App spricht die Erweiterung NICHT direkt an (kennt ihre ID nicht),
// sondern über window.postMessage — dieses Content-Script leitet weiter.

// 2.6.0 (Pruefung 05.10.2026, Nr. 20): Nach einem Update/Neuladen der Erweiterung laeuft dieses Skript in offenen
// Tabs weiter, erreicht den Helfer aber nicht mehr — dann nicht mehr "bereit" melden und sofort mit Fehler antworten
// (vorher wartete die App 25 s).
function helferDa() {
  try { return !!(chrome.runtime && chrome.runtime.id); } catch (e) { return false; }
}

// 1. Der App signalisieren, dass der Helfer installiert ist.
if (helferDa()) window.postMessage({ __autoschnell: true, type: "EXT_READY" }, "*");

// 2. Auf Abruf-Wünsche der App hören.
window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  const d = event.data;
  if (!d || d.__autoschnell !== true) return;

  if (d.type === "PING") {
    if (helferDa()) window.postMessage({ __autoschnell: true, type: "EXT_READY" }, "*");
    return;
  }

  if (d.type === "FETCH" && d.reqId) {
    const antworten = (resp, fehler) => window.postMessage({
      __autoschnell: true,
      type: "FETCH_RESULT",
      reqId: d.reqId,
      ok: !!(resp && resp.ok),
      html: resp && resp.html,
      error: (resp && resp.error) || fehler,
    }, "*");
    if (!helferDa()) {
      antworten(null, "Der AutoSchnell Helfer wurde aktualisiert – bitte diese Seite neu laden.");
      return;
    }
    try {
      chrome.runtime.sendMessage({ type: "AUTOSCHNELL_FETCH", url: d.url },
        (resp) => antworten(resp, chrome.runtime.lastError && chrome.runtime.lastError.message));
    } catch (e) {
      antworten(null, "Der AutoSchnell Helfer wurde aktualisiert – bitte diese Seite neu laden.");
    }
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

  // P1 08.10.2026: Nicht schon den Empfang des postMessage als Erfolg
  // melden. AutoSchnell kann wegen ungespeicherter Arbeit erst eine
  // Rueckfrage zeigen. Der Helfer bekommt ok erst nach echter Navigation/
  // Fahrzeuguebernahme aus programmStart.js.
  const reqId = (crypto.randomUUID ? crypto.randomUUID()
    : (Date.now().toString(36) + Math.random().toString(36).slice(2)));
  let fertig = false;
  const aufraeumen = () => window.removeEventListener("message", bestaetigt);
  const bestaetigt = (event) => {
    const d = event.data;
    if (event.source !== window || !d || d.__autoschnell !== true
        || d.type !== "OEFFNEN_BESTAETIGT" || d.reqId !== reqId || fertig) return;
    fertig = true;
    clearTimeout(uhr);
    aufraeumen();
    sendResponse({ ok: true, app: alsApp() });
  };
  window.addEventListener("message", bestaetigt);
  const uhr = setTimeout(() => {
    if (fertig) return;
    fertig = true;
    aufraeumen();
    sendResponse({ ok: false, timeout: true, app: alsApp() });
  }, 90000);
  window.postMessage({ __autoschnell: true, type: "OEFFNEN", ziel, reqId }, window.location.origin);
  return true;
});
