/*
 * Installierte App (Wunsch Ahmad 03.10.2026): Das Windows-Programm öffnet den Kaufvertrag in der installierten
 * AutoSchnell-App statt in einem Browser-Tab. Ist die App schon offen, holt Windows dieses Fenster nach vorne
 * (manifest.json: launch_handler "focus-existing") und meldet das Ziel über window.launchQueue — hier wird
 * daraus eine Navigation bzw. (Vergleich schon offen) ein Ereignis, das die Vergleichsseite übernimmt.
 * Ist gerade etwas ungespeichert (z. B. ein halb ausgefüllter Kaufvertrag), wird nur gefragt.
 */

export const INSERAT_EREIGNIS = "autoschnell:inserat";

// Pruefbericht 03.10.2026 (Nr. 12): Das Programm haengt "&start=<32 Hex-Zeichen>" an und fragt danach, ob die App das
// Auto uebernommen hat — ohne Rueckmeldung binnen 10 Sekunden oeffnet es den Kaufvertrag im Browser.
const START_KENNUNG = /^[0-9a-f]{32}$/;

/** Kennung des Programm-Starts aus einem Ziel oder einer Adresse, sonst null. */
export function startKennung(ziel, origin = "https://app.auto-schnellkauf.de") {
  try {
    const s = new URL(ziel, origin).searchParams.get("start");
    return s && START_KENNUNG.test(s) ? s : null;
  } catch {
    return null;
  }
}

/** Meldet AutoSchnell, dass die App den Start uebernommen hat (Fehler egal — dann oeffnet das Programm den Browser). */
export function startMelden(client, start) {
  if (!client || !start || !START_KENNUNG.test(start)) return Promise.resolve(false);
  return client.post(`/werkzeuge/app-start/${start}`).then(() => true, () => false);
}

/** Browser-Helfer: erst nach wirklich geladenem Ziel bestaetigen. */
export function helferOeffnenBestaetigen(reqId, fenster = typeof window !== "undefined" ? window : null) {
  if (!reqId || typeof reqId !== "string" || !fenster?.postMessage) return false;
  fenster.postMessage({
    __autoschnell: true, type: "OEFFNEN_BESTAETIGT", reqId,
  }, fenster.location.origin);
  return true;
}

// Adresse, mit der dieses Fenster gestartet wurde (beim Laden des Moduls, bevor eine Seite sie umschreibt).
const START_ADRESSE = typeof window !== "undefined" ? window.location.href : "";

/**
 * Browser-Helfer (04.10.2026, Wunsch Ahmad "immer die App öffnen"): Ist die App installiert, aber zu, startet die
 * Erweiterung sie über den Link-Typ web+autoschnell: (manifest.json protocol_handlers) — der Browser öffnet dann
 * /app/vergleich?protokoll=<web+autoschnell:vertrag?url=…>. Daraus wird die gewohnte Suche ?url=…&vertrag=1.
 * Andere Suchen bleiben, wie sie sind.
 */
export function protokollSuche(search) {
  const s = new URLSearchParams(search || "");
  const roh = s.get("protokoll");
  if (!roh) return s;
  const m = /^web\+autoschnell:(?:\/\/)?([a-z]+)\??(.*)$/i.exec(roh.trim());
  const neu = new URLSearchParams();
  if (!m) return neu;
  const innen = new URLSearchParams(m[2]);
  if (innen.get("url")) neu.set("url", innen.get("url"));
  if (m[1].toLowerCase() === "vertrag") neu.set("vertrag", "1");
  const start = innen.get("start") || "";
  if (START_KENNUNG.test(start)) neu.set("start", start);
  return neu;
}

/** Wohin soll ein App-Start führen? null = nur nach vorne holen (App-Symbol) oder nichts zu tun. */
export function zielAusAppStart(targetURL, { origin, startAdresse = START_ADRESSE } = {}) {
  if (!targetURL) return null;
  let u;
  try { u = new URL(targetURL); } catch { return null; }
  if (u.origin !== origin) return null;
  if (targetURL === startAdresse) return null;            // frisch geöffnetes Fenster steht schon dort
  if (u.pathname === "/" || u.pathname === "/start") return null;   // App-Symbol: nur nach vorne holen
  if (u.searchParams.has("protokoll")) {
    const s = protokollSuche(u.search).toString();
    return s ? `/app/vergleich?${s}` : null;
  }
  return u.pathname + u.search + u.hash;
}

/**
 * Ein Ziel in der laufenden App öffnen: Vergleichsseite offen -> Ereignis (sie übernimmt das Auto selbst),
 * sonst navigieren; mit ungespeicherter Arbeit erst nachfragen.
 */
export function zielUebernehmen(ziel, {
  fenster, navigieren, beschaeftigt = () => false, nachfragen = (f) => f(), uebernommen = () => {},
}) {
  const ausfuehren = () => {
    const u = new URL(ziel, fenster.location.origin);
    const link = u.pathname === "/app/vergleich" ? u.searchParams.get("url") : null;
    if (link && fenster.location.pathname === "/app/vergleich") {
      // Die Vergleichsseite ist offen und bleibt eingehängt — sie übernimmt das neue Auto selbst.
      // Start-Kennung mitreichen: AutoPointer wird ERST von Vergleich.jsx
      // bestätigt, nachdem genau dieses Fahrzeug erfolgreich geladen wurde.
      const vertrag = u.searchParams.get("vertrag") === "1";
      const start = startKennung(ziel, fenster.location.origin);
      const helferReq = u.searchParams.get("helfer_req") || null;
      let detail = link;
      if (vertrag || start || helferReq) {
        detail = { link, vertrag };
        if (start) detail.start = start;
        if (helferReq) detail.helferReq = helferReq;
      }
      fenster.dispatchEvent(new CustomEvent(INSERAT_EREIGNIS, { detail }));
      uebernommen();
      return;
    }
    navigieren(ziel);
    uebernommen();
  };
  if (beschaeftigt()) nachfragen(ausfuehren);
  else ausfuehren();
}

/**
 * Browser-Helfer (04.10.2026): Die Erweiterung holt ein offenes App-Fenster nach vorne und schickt das Ziel über
 * ihr Seiten-Skript (content.js, window.postMessage) — kein Neuladen, ungespeicherte Arbeit bleibt.
 * Nur Pfade innerhalb der App (/app/…), nur aus diesem Fenster.
 */
export function erweiterungZieleVerfolgen(navigieren, {
  fenster = typeof window !== "undefined" ? window : null,
  beschaeftigt = () => false,
  nachfragen = (ausfuehren) => ausfuehren(),
} = {}) {
  if (!fenster?.addEventListener) return () => {};
  const empfangen = (e) => {
    const d = e?.data;
    if (e?.source !== fenster || !d || d.__autoschnell !== true || d.type !== "OEFFNEN") return;
    const ziel = typeof d.ziel === "string" ? d.ziel : "";
    if (!/^\/app\/[a-z]/.test(ziel) || ziel.startsWith("//")) return;
    const reqId = typeof d.reqId === "string" ? d.reqId : "";
    let zielMitAck = ziel;
    let vergleichZiel = false;
    try {
      const u = new URL(ziel, fenster.location.origin);
      vergleichZiel = u.pathname === "/app/vergleich" && Boolean(u.searchParams.get("url"));
      if (vergleichZiel && reqId) {
        u.searchParams.set("helfer_req", reqId);
        zielMitAck = u.pathname + "?" + u.searchParams.toString() + u.hash;
      }
    } catch { /* Ziel wurde oben bereits validiert */ }

    zielUebernehmen(zielMitAck, {
      fenster, navigieren, beschaeftigt, nachfragen,
      // Bei einem Vergleich ist Navigation allein KEIN Erfolg. Vergleich.jsx
      // bestaetigt erst nach erfolgreichem Laden genau dieses Fahrzeugs.
      uebernommen: () => {
        if (!reqId || vergleichZiel) return;
        helferOeffnenBestaetigen(reqId, fenster);
      },
    });
  };
  fenster.addEventListener("message", empfangen);
  return () => fenster.removeEventListener("message", empfangen);
}

/**
 * Meldet ein späterer App-Start ein Ziel, dorthin wechseln. Liefert false, wenn der Browser das nicht kann.
 * @param navigieren   (ziel) => void — Navigation der App (useNavigate)
 * @param beschaeftigt () => bool — true, solange etwas ungespeichert ist
 * @param nachfragen   (ausfuehren) => void — statt sofort zu wechseln (z. B. Hinweis mit Knopf)
 * @param melden       (start) => void — Nr. 12: das Fenster hat den Start angenommen (auch wenn erst nachgefragt wird)
 */
export function startZieleVerfolgen(navigieren, {
  fenster = typeof window !== "undefined" ? window : null,
  startAdresse = START_ADRESSE,
  beschaeftigt = () => false,
  nachfragen = (ausfuehren) => ausfuehren(),
  melden = () => {},
} = {}) {
  if (!fenster?.launchQueue?.setConsumer) return false;
  fenster.launchQueue.setConsumer((params) => {
    const ziel = zielAusAppStart(params?.targetURL, { origin: fenster.location.origin, startAdresse });
    if (!ziel) return;
    // AutoPointer-Starts werden NICHT schon beim Navigieren bestaetigt.
    // Die Start-Kennung steckt im Ziel/Event; Vergleich.jsx meldet sie erst
    // nach erfolgreichem Laden des konkreten Fahrzeugs. So bleibt der
    // 10-Sekunden-Browser-Fallback aktiv, wenn die App nur aufgeht, der
    // Vergleich aber scheitert.
    zielUebernehmen(ziel, {
      fenster, navigieren, beschaeftigt, nachfragen,
      uebernommen: () => {},
    });
  });
  return true;
}
