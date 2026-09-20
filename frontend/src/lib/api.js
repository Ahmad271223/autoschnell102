import { fassungMithoeren } from "@/lib/fassung";
import { vergleichLeeren } from "@/lib/vergleichSpeicher";
import axios from "axios";
import { TOKEN_APP, tokenLesen, tokenLoeschen } from "@/lib/sitzung";

const BACKEND = process.env.REACT_APP_BACKEND_URL;
export const API_BASE = `${BACKEND}/api`;

// 60 s Timeout: haengt der Server, bekommt der Nutzer eine Fehlermeldung
// statt eines endlosen Spinners (Vergleich + PDF sind die langsamsten Wege).
export const api = axios.create({ baseURL: API_BASE, timeout: 60000 });
// Runde 31: jede Antwort traegt X-AH-Fassung — so erfaehrt die Oberflaeche
// von einer neuen Fassung, bevor sie gegen eine fehlende Datei laeuft.
fassungMithoeren(api);

// Runde 29 (12.09.2026, Pruefbefund): 60 s passen fuer die schnellen Wege,
// sind aber zu knapp fuer die langsamen. nginx laesst Anfragen bis 300 s
// laufen — der Browser brach nach 60 s ab, der Nutzer sah einen Fehler,
// obwohl der Server weiterarbeitete (und der Vertrag am Ende doch entstand).
// Betroffen sind das Erzeugen des Kaufvertrags und alle Datei-Abrufe (PDF).
//
// Hinweis (Gegenpruefung): In Produktion liegt Cloudflare davor und kappt
// eine Anfrage nach rund 100 s mit HTTP 524. Dieses Limit wirkt also als
// Netz fuer den Browser, nicht als Verlaengerung darueber hinaus — es
// verhindert vor allem, dass der Browser VOR dem Server aufgibt.
export const LANGE_AKTION_MS = 180000;

/** Braucht diese Anfrage das lange Zeitlimit? (rein, damit testbar) */
export function istLangeAktion(config = {}) {
  if (config.responseType === "blob") return true;
  const pfad = String(config.url || "");
  const methode = String(config.method || "get").toLowerCase();
  if (methode !== "post") return false;
  // Lange Wege auf der Serverseite: Vertrag erzeugen (PDF + Fotos) und der
  // Protokoll-Abschluss (Abholprotokoll-PDF + zwei Unterschrift-Bilder).
  // Abnahme 12.09.2026: Der Abschluss fehlte hier — ausgerechnet der
  // langsamste Weg der Fahrer-App lief weiter ins 60-Sekunden-Limit.
  return /^\/contracts(\/|$|\?)/.test(pfad)
    || /\/protocol\/(finalize|submit|correction)(\?|$)/.test(pfad)
    || /\/report(\?|$)/.test(pfad);
}

api.interceptors.request.use((config) => {
  const token = tokenLesen(TOKEN_APP);
  if (token) config.headers.Authorization = `Bearer ${token}`;
  if (istLangeAktion(config)) config.timeout = LANGE_AKTION_MS;
  return config;
});

/**
 * Gehoert diese 401 noch zum Token, der GERADE gespeichert ist?
 *
 * Nachpruefung 20.09.2026 (Nr. 39/40): Der Abfaenger loeschte bei jeder 401
 * einfach den aktuell gespeicherten Token — ohne zu pruefen, ob die
 * gescheiterte Anfrage ueberhaupt mit DIESEM Token losgeschickt wurde.
 * Der Ablauf, der dabei schiefging:
 *   1. Eine Anfrage mit Token A ist unterwegs.
 *   2. Im selben Tab meldet sich jemand als Konto B an -> Token B liegt jetzt.
 *   3. Die alte Anfrage antwortet verspaetet mit 401 (Token A ist ja weg).
 *   4. Der Abfaenger loeschte Token B und warf Konto B zurueck zum Login.
 * Jetzt zaehlt eine 401 nur noch fuer den Token, mit dem sie gesendet wurde.
 * (Rein exportiert, damit es sich pruefen laesst.)
 */
export function gehoertZumAktuellenToken(config, aktuell) {
  const gesendet = String(config?.headers?.Authorization || "");
  if (!gesendet) return true;      // ohne Token gesendet: nichts zu schuetzen
  return gesendet === `Bearer ${aktuell || ""}`;
}

/** Höchstens so viele automatische Wiederholungen je Anfrage. */
export const WIEDERHOLEN_MAX = 2;

/**
 * Darf diese gescheiterte Anfrage automatisch wiederholt werden?
 *
 * Prüfbericht 20.09.2026 (P1): Legen zwei Sucher fast gleichzeitig einen
 * Vertrag zum SELBEN Fahrzeug an, wartet der zweite serverseitig 6 s und
 * bekam dann einen sichtbaren Fehler — er musste von Hand noch einmal
 * speichern. Der Server weiß an dieser Stelle aber, dass NICHTS
 * geschrieben wurde, und sagt es mit `X-Wiederholen: 1`.
 *
 * Wichtig: NUR bei dieser Kopfzeile. Ein beliebiger 503 darf niemals
 * wiederholt werden — beim Anlegen eines Vertrags entstünde sonst ein
 * zweiter. (Rein exportiert, damit es sich prüfen lässt.)
 */
export function darfWiederholen(err) {
  const r = err?.response;
  if (!r || r.status !== 503) return false;
  const kopf = r.headers?.["x-wiederholen"] ?? r.headers?.["X-Wiederholen"];
  if (String(kopf || "") !== "1") return false;
  return (err.config?.__versuche || 0) < WIEDERHOLEN_MAX;
}

/** Wartezeit bis zum nächsten Versuch (Sekunden aus Retry-After, sonst 3 s). */
export function wiederholenNachMs(err) {
  const roh = err?.response?.headers?.["retry-after"];
  const sek = Number.parseInt(String(roh ?? ""), 10);
  return (Number.isFinite(sek) && sek > 0 ? Math.min(sek, 30) : 3) * 1000;
}

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (darfWiederholen(err)) {
      const config = err.config;
      config.__versuche = (config.__versuche || 0) + 1;
      return new Promise((res) => setTimeout(res, wiederholenNachMs(err)))
        .then(() => api(config));
    }
    if (err?.response?.status === 401) {
      const url = err?.config?.url || "";
      if (!gehoertZumAktuellenToken(err?.config, tokenLesen(TOKEN_APP))) {
        // Veraltete Antwort einer frueheren Anmeldung — die neue Sitzung
        // bleibt bestehen (Nr. 39/40).
        return Promise.reject(err);
      }
      if (!url.includes("/auth/login")) {
        tokenLoeschen(TOKEN_APP);
        const pfad = window.location.pathname;
        const imBereich = pfad.startsWith("/app") || pfad.startsWith("/admin");
        // /start (Einstieg der installierten App, 09/2026) leitet selbst
        // weiter — den Grund trotzdem merken, damit die Anmeldung ihn zeigt.
        if (imBereich || pfad === "/start") {
          // Runde 19: den ECHTEN Grund mitnehmen (neue Anmeldung wann/wo,
          // Abmeldung, Sperre, abgelaufen) — vorher stand fuer jede 401
          // "auf einem anderen Geraet verwendet". Nicht in die URL (Verlauf,
          // Logs), sondern nur fuer diesen Tab.
          const detail = err?.response?.data?.detail;
          try {
            window.sessionStorage.setItem("ah_abmeldegrund",
              typeof detail === "string" && detail ? detail : "");
            // Runde 27 (Pruefbefund P0): Auch der zuletzt angezeigte
            // Vergleich muss weg — sonst sieht der naechste Nutzer an
            // diesem Browser Fahrzeug, Verkaeuferdaten und Vertrag des
            // vorherigen Kontos.
            vergleichLeeren(window.sessionStorage);
          } catch { /* Storage gesperrt — dann nur die allgemeine Meldung */ }
        }
        if (imBereich) window.location.href = "/login?reason=session";
      }
    }
    return Promise.reject(err);
  }
);

// Geschuetzte Datei (PDF/PNG) in neuem Tab oeffnen — Abruf per
// Authorization-Header statt ?auth=<token> in der URL (der Token landete
// sonst in Browser-Verlauf, Proxy- und Server-Logs).
export async function openAuthedFile(path, mime = "application/pdf", client = api) {
  // Datei erst laden, dann per unsichtbarem <a target="_blank">-Klick
  // oeffnen — dasselbe Muster wie beim Kaufvertrag-PDF (openContractPdf),
  // das zuverlaessig funktioniert. Der fruehere Weg (leeren Tab synchron
  // oeffnen, opener kappen, Blob-URL nachtragen) bleibt in neueren
  // Chrome-Versionen dauerhaft weiss: nach `opener = null` liegt der Tab
  // in einer eigenen Storage-Partition und darf die Blob-URL des
  // Ursprungs-Tabs nicht mehr laden.
  const res = await client.get(path, { responseType: "blob" });
  const url = URL.createObjectURL(new Blob([res.data], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 1000);
  // Spaet freigeben — der eingebaute PDF-Betrachter laedt die Adresse
  // beim Drucken/Neuladen erneut.
  setTimeout(() => URL.revokeObjectURL(url), 10 * 60 * 1000);
}

/**
 * Normalize a FastAPI / axios error into a human-readable string.
 *
 * FastAPI 422 returns `{ detail: [{type, loc, msg, input, url}, ...] }` —
 * which can't be rendered as a React child. This helper flattens any
 * shape (string, array of pydantic errors, single object, …) into one
 * line so it's safe to pass to `toast.error(...)` or JSX.
 */
export const errMsg = (err, fallback = "Ein Fehler ist aufgetreten") => {
  const d = err?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    const parts = d.map((it) => (typeof it === "string" ? it
      : (it?.msg || JSON.stringify(it)).replace(/^Value error, /, "")));
    return parts.filter(Boolean).join(" · ") || fallback;
  }
  if (d && typeof d === "object") return d.msg || JSON.stringify(d);
  return err?.message || fallback;
};
