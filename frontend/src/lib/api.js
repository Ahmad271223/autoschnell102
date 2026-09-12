import { vergleichLeeren } from "@/lib/vergleichSpeicher";
import axios from "axios";
import { TOKEN_APP, tokenLesen, tokenLoeschen } from "@/lib/sitzung";

const BACKEND = process.env.REACT_APP_BACKEND_URL;
export const API_BASE = `${BACKEND}/api`;

// 60 s Timeout: haengt der Server, bekommt der Nutzer eine Fehlermeldung
// statt eines endlosen Spinners (Vergleich + PDF sind die langsamsten Wege).
export const api = axios.create({ baseURL: API_BASE, timeout: 60000 });

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

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) {
      const url = err?.config?.url || "";
      if (!url.includes("/auth/login") && !url.includes("/auth/register")) {
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
