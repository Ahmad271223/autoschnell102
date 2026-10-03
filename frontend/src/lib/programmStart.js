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

// Adresse, mit der dieses Fenster gestartet wurde (beim Laden des Moduls, bevor eine Seite sie umschreibt).
const START_ADRESSE = typeof window !== "undefined" ? window.location.href : "";

/** Wohin soll ein App-Start führen? null = nur nach vorne holen (App-Symbol) oder nichts zu tun. */
export function zielAusAppStart(targetURL, { origin, startAdresse = START_ADRESSE } = {}) {
  if (!targetURL) return null;
  let u;
  try { u = new URL(targetURL); } catch { return null; }
  if (u.origin !== origin) return null;
  if (targetURL === startAdresse) return null;            // frisch geöffnetes Fenster steht schon dort
  if (u.pathname === "/" || u.pathname === "/start") return null;   // App-Symbol: nur nach vorne holen
  return u.pathname + u.search + u.hash;
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
    const start = startKennung(ziel, fenster.location.origin);
    if (start) melden(start);
    const ausfuehren = () => {
      const u = new URL(ziel, fenster.location.origin);
      const link = u.pathname === "/app/vergleich" ? u.searchParams.get("url") : null;
      if (link && fenster.location.pathname === "/app/vergleich") {
        // Die Vergleichsseite ist offen und bleibt eingehängt — sie übernimmt das neue Auto selbst.
        fenster.dispatchEvent(new CustomEvent(INSERAT_EREIGNIS, { detail: link }));
        return;
      }
      navigieren(ziel);
    };
    if (beschaeftigt()) nachfragen(ausfuehren);
    else ausfuehren();
  });
  return true;
}
