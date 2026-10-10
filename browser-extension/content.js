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

// 4. 2.7.2 (Wunsch Ahmad 08.10.2026, Vorgangsnummer): das Windows-Programm oeffnet nur /app/vorgang/<id>. Der Helfer
//    uebernimmt den Vorgang sofort (document_start, bevor die App laedt) und oeffnet Vergleiche + Inserat selbst.
//    Ohne Helfer (oder nicht verbunden) zeigt die App die Seite als Rueckfall, und das Programm oeffnet nach 5 s selbst.
const VORGANG_SEITE = /^\/app\/vorgang\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/?$/;
const vorgangTreffer = VORGANG_SEITE.exec(location.pathname);
if (vorgangTreffer && window.top === window && helferDa()) {
  try {
    chrome.runtime.sendMessage({ type: "AUTOSCHNELL_VORGANG", id: vorgangTreffer[1], alsApp: alsApp() }, (antwort) => {
      void chrome.runtime.lastError;
      // bleibt die Seite stehen (App-Fenster), sagt die App "im Browser geoeffnet"
      window.postMessage({ __autoschnell: true, type: "VORGANG_ERGEBNIS", id: vorgangTreffer[1],
                           ok: !!(antwort && antwort.ok) }, "*");
    });
  } catch (e) { /* Erweiterung neu geladen: das Programm oeffnet nach 5 s selbst */ }
}

// 5. 2.7.4 (Pruefung 09.10.2026, Vertragsweg, E1): nach OEFFNEN sagt die App zurueck, was sie mit dem Ziel tut —
//    "uebernommen" (Vertrag geht auf), "nachgefragt" (etwas ungespeichert, sie fragt den Sucher), "anmeldung" (Anmeldeseite,
//    danach geht es dort weiter), "abo" (kein Sucher-Abo). Darauf wartet der Helfer bis 1,5 s und reicht den Stand in die
//    Box durch; aeltere App-Fenster melden nichts -> Antwort wie bisher ohne "stand".
const ERGEBNIS_WARTE_MS = 1500;
const ERGEBNIS_STAENDE = new Set(["uebernommen", "nachgefragt", "anmeldung", "abo"]);

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || sender.id !== chrome.runtime.id || msg.type !== "AUTOSCHNELL_OEFFNEN") return false;
  const ziel = typeof msg.ziel === "string" ? msg.ziel : "";
  if (!/^\/app\/[a-z]/.test(ziel)) {
    sendResponse({ ok: false });
    return false;
  }
  let beantwortet = false;
  let uhr = 0;
  const antworten = (stand) => {
    if (beantwortet) return;
    beantwortet = true;
    clearTimeout(uhr);
    window.removeEventListener("message", horcher);
    sendResponse(stand ? { ok: true, app: alsApp(), stand } : { ok: true, app: alsApp() });
  };
  const horcher = (event) => {
    if (event.source !== window) return;
    const d = event.data;
    if (!d || d.__autoschnell !== true || d.type !== "OEFFNEN_ERGEBNIS" || d.ziel !== ziel) return;
    antworten(ERGEBNIS_STAENDE.has(d.stand) ? d.stand : "");
  };
  window.addEventListener("message", horcher);
  uhr = setTimeout(() => antworten(""), ERGEBNIS_WARTE_MS);
  window.postMessage({ __autoschnell: true, type: "OEFFNEN", ziel }, window.location.origin);
  return true;        // sendResponse kommt, sobald die App geantwortet hat (oder nach 1,5 s)
});
