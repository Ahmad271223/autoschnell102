// Globaler Frontend-Fehler-Reporter: unbehandelte JS-Fehler und Promise-
// Rejections werden an das Backend gemeldet und erscheinen im Admin-Bereich
// unter "Fehler". Bewusst mit fetch statt axios, damit ein Fehler im
// axios-Interceptor keine Endlosschleife auslösen kann.

const MAX_REPORTS_PER_SESSION = 10;
let reported = 0;
const seen = new Set();

function report(message, stack) {
  try {
    if (reported >= MAX_REPORTS_PER_SESSION) return;
    const key = String(message).slice(0, 200);
    if (seen.has(key)) return; // gleiche Meldung nicht mehrfach senden
    seen.add(key);
    reported += 1;

    const backend = process.env.REACT_APP_BACKEND_URL || "";
    fetch(`${backend}/api/client-errors`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: String(message).slice(0, 1000),
        stack: String(stack || "").slice(0, 8000),
        // Nur Origin + Pfad: Query/Fragment koennen Reset-Token oder
        // Stripe-Session-IDs enthalten (Pruefbericht Runde 5).
        url: (window.location.origin + window.location.pathname).slice(0, 500),
        user_email: "",
      }),
    }).catch(() => {});
  } catch {}
}

export function installErrorReporter() {
  window.addEventListener("error", (e) => {
    report(e?.message || "Unbekannter Fehler", e?.error?.stack);
  });
  window.addEventListener("unhandledrejection", (e) => {
    const r = e?.reason;
    // Abgefangene API-Fehler (axios) haben response — die behandelt die App
    // selbst per Toast; nur echte unbehandelte Fehler melden.
    if (r?.response) return;
    report(r?.message || String(r || "Unhandled rejection"), r?.stack);
  });
}
