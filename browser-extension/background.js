// AutoSchnell Abruf-Helfer — Service Worker.
// Holt eine Kleinanzeigen-Seite über die Internetverbindung DES NUTZERS
// (nicht über den AutoSchnell-Server) und gibt das HTML zurück.
// Die Erweiterung hat host_permissions NUR für kleinanzeigen.de — sie kann
// keine anderen Seiten lesen.

const ALLOWED = /^https:\/\/(www\.)?kleinanzeigen\.de\/s-anzeige\//i;

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== "AUTOSCHNELL_FETCH") return;

  const url = String(msg.url || "");
  if (!ALLOWED.test(url)) {
    sendResponse({ ok: false, error: "Nur Kleinanzeigen-Fahrzeuglinks erlaubt." });
    return true;
  }

  fetch(url, {
    credentials: "omit",
    headers: { "Accept": "text/html,application/xhtml+xml" },
  })
    .then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.text();
    })
    .then((html) => sendResponse({ ok: true, html }))
    .catch((e) => sendResponse({ ok: false, error: String(e && e.message || e) }));

  return true; // async sendResponse
});
