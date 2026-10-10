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

/**
 * Meldet AutoSchnell, dass die App den Start uebernommen hat (Fehler egal — dann oeffnet das Programm den Browser).
 * Pruefung 09.10.2026 (Vertragsweg): mit Zustand — "offen" (geht auf), "nachgefragt" (etwas ungespeichert, Dialog
 * wartet), "anmeldung" (Anmeldeseite), "abo" (kein Sucher-Abo). Der Server nimmt die Meldung auch OHNE Anmeldung an;
 * das Programm (ab 1.5.15) sagt dem Sucher dann, was in der App zu tun ist, statt zusaetzlich den Browser zu oeffnen.
 */
export const START_ZUSTAENDE = ["offen", "nachgefragt", "anmeldung", "abo"];

export function startMelden(client, start, zustand) {
  if (!client || !start || !START_KENNUNG.test(start)) return Promise.resolve(false);
  const anfrage = START_ZUSTAENDE.includes(zustand)
    ? client.post(`/werkzeuge/app-start/${start}`, { zustand })
    : client.post(`/werkzeuge/app-start/${start}`);
  return anfrage.then(() => true, () => false);
}

// Pruefung 09.10.2026 (Befund Ahmad "Vertrag aus dem Programm haengt"): ein Ziel, das wegen ungespeicherter Arbeit
// erst nachgefragt wird, darf nie verloren gehen — es bleibt 15 Minuten gemerkt (sessionStorage), die App zeigt
// solange einen Hinweis mit Knopf.
export const ZIEL_MERKER = "ah_startziel";
const ZIEL_MERKER_MS = 15 * 60 * 1000;

function speicherVon(speicher) {
  if (speicher) return speicher;
  try { return typeof window !== "undefined" ? window.sessionStorage : null; } catch { return null; }
}

export function zielMerken(ziel, speicher) {
  try { speicherVon(speicher)?.setItem(ZIEL_MERKER, JSON.stringify({ ziel, seit: Date.now() })); } catch { /* egal */ }
}

export function zielVergessen(speicher) {
  try { speicherVon(speicher)?.removeItem(ZIEL_MERKER); } catch { /* egal */ }
}

/** Das gemerkte Ziel (nur Pfade in der App, nicht aelter als 15 Minuten), sonst null. */
export function zielGemerkt(speicher, jetzt = Date.now()) {
  try {
    const roh = speicherVon(speicher)?.getItem(ZIEL_MERKER);
    if (!roh) return null;
    const d = JSON.parse(roh);
    const ziel = typeof d?.ziel === "string" ? d.ziel : "";
    if (!/^\/app\/[a-z]/.test(ziel) || ziel.startsWith("//") || !(jetzt - Number(d.seit || 0) < ZIEL_MERKER_MS)) {
      zielVergessen(speicher);
      return null;
    }
    return ziel;
  } catch {
    return null;
  }
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

/** Pfad ohne Schrägstrich am Ende, klein — React Router matcht so, der Vergleich hier auch (Prüfung 09.10.2026). */
function pfadNormal(pfad) {
  return String(pfad || "").replace(/\/+$/, "").toLowerCase() || "/";
}

/**
 * Ein Ziel in der laufenden App öffnen: Vergleichsseite offen -> Ereignis (sie übernimmt das Auto selbst),
 * sonst navigieren; mit ungespeicherter Arbeit erst nachfragen.
 * Rückgabe: "uebernommen" (sofort ausgeführt) oder "nachgefragt" (wartet auf den Nutzer).
 * Prüfung 09.10.2026 (Befund Ahmad "Vertrag hängt"): das Ereignis trägt einen Rückkanal (detail.uebernommen) —
 * hört keine Vergleichsseite zu (Route zeigt gerade „Lade…“, Abo-Prüfung, Fehlergrenze), wird navigiert statt
 * das Ziel still zu verlieren. Vor einer Nachfrage wird das Ziel gemerkt (zielMerken), damit es nie verloren geht.
 */
export function zielUebernehmen(ziel, { fenster, navigieren, beschaeftigt = () => false, nachfragen = (f) => f() }) {
  const ausfuehren = () => {
    zielVergessen();
    const u = new URL(ziel, fenster.location.origin);
    const link = pfadNormal(u.pathname) === "/app/vergleich" ? u.searchParams.get("url") : null;
    if (link && pfadNormal(fenster.location.pathname) === "/app/vergleich") {
      // Die Vergleichsseite ist offen und bleibt eingehängt — sie übernimmt das neue Auto selbst.
      // Browser-Helfer (04.10.2026): "&vertrag=1" -> Kaufvertrag gleich öffnen (sonst wie bisher nur der Link).
      const detail = { link, vertrag: u.searchParams.get("vertrag") === "1",
                       lesungFehlt: u.searchParams.get("lesung") === "fehlt", uebernommen: false };
      fenster.dispatchEvent(new CustomEvent(INSERAT_EREIGNIS, { detail }));
      if (detail.uebernommen) return;
    }
    navigieren(ziel);
  };
  if (beschaeftigt()) {
    zielMerken(ziel);
    nachfragen(ausfuehren, ziel);
    return "nachgefragt";
  }
  ausfuehren();
  return "uebernommen";
}

/**
 * Browser-Helfer (04.10.2026): Die Erweiterung holt ein offenes App-Fenster nach vorne und schickt das Ziel über
 * ihr Seiten-Skript (content.js, window.postMessage) — kein Neuladen, ungespeicherte Arbeit bleibt.
 * Nur Pfade innerhalb der App (/app/…), nur aus diesem Fenster.
 * Prüfung 09.10.2026: die App antwortet mit OEFFNEN_ERGEBNIS (stand: uebernommen | nachgefragt | anmeldung | abo) —
 * die Box der Erweiterung (ab 2.7.4) sagt dem Sucher dann, was in der App zu tun ist.
 * @param zustand () => "anmeldung" | "abo" | null — was dem Öffnen in der App gerade im Weg steht
 */
export function erweiterungZieleVerfolgen(navigieren, {
  fenster = typeof window !== "undefined" ? window : null,
  beschaeftigt = () => false,
  nachfragen = (ausfuehren) => ausfuehren(),
  zustand = () => null,
} = {}) {
  if (!fenster?.addEventListener) return () => {};
  const empfangen = (e) => {
    const d = e?.data;
    if (e?.source !== fenster || !d || d.__autoschnell !== true || d.type !== "OEFFNEN") return;
    const ziel = typeof d.ziel === "string" ? d.ziel : "";
    if (!/^\/app\/[a-z]/.test(ziel) || ziel.startsWith("//")) return;
    const stand = zielUebernehmen(ziel, { fenster, navigieren, beschaeftigt, nachfragen });
    try {
      fenster.postMessage?.({ __autoschnell: true, type: "OEFFNEN_ERGEBNIS", ziel, stand: zustand() || stand },
                            fenster.location.origin);
    } catch { /* älteres Seiten-Skript: egal */ }
  };
  fenster.addEventListener("message", empfangen);
  return () => fenster.removeEventListener("message", empfangen);
}

/**
 * Meldet ein späterer App-Start ein Ziel, dorthin wechseln. Liefert false, wenn der Browser das nicht kann.
 * @param navigieren   (ziel) => void — Navigation der App (useNavigate)
 * @param beschaeftigt () => bool — true, solange etwas ungespeichert ist
 * @param nachfragen   (ausfuehren, ziel) => void — statt sofort zu wechseln (unübersehbarer Dialog)
 * @param melden       (start, zustand) => void — Nr. 12: das Fenster hat den Start angenommen; zustand "offen" oder
 *                     "nachgefragt" (Prüfung 09.10.2026: das Programm sagt dem Sucher, was in der App zu tun ist)
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
    if (start) melden(start, beschaeftigt() ? "nachgefragt" : "offen");
    zielUebernehmen(ziel, { fenster, navigieren, beschaeftigt, nachfragen });
  });
  return true;
}
