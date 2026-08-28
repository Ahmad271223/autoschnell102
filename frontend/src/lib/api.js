import axios from "axios";

const BACKEND = process.env.REACT_APP_BACKEND_URL;
export const API_BASE = `${BACKEND}/api`;

// 60 s Timeout: haengt der Server, bekommt der Nutzer eine Fehlermeldung
// statt eines endlosen Spinners (Vergleich + PDF sind die langsamsten Wege).
export const api = axios.create({ baseURL: API_BASE, timeout: 60000 });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("ah_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) {
      const url = err?.config?.url || "";
      if (!url.includes("/auth/login") && !url.includes("/auth/register")) {
        localStorage.removeItem("ah_token");
        if (window.location.pathname.startsWith("/app") || window.location.pathname.startsWith("/admin")) {
          window.location.href = "/login?reason=session";
        }
      }
    }
    return Promise.reject(err);
  }
);

// Geschuetzte Datei (PDF/PNG) in neuem Tab oeffnen — Abruf per
// Authorization-Header statt ?auth=<token> in der URL (der Token landete
// sonst in Browser-Verlauf, Proxy- und Server-Logs).
export async function openAuthedFile(path, mime = "application/pdf", client = api) {
  // Das Fenster MUSS synchron im Klick geoeffnet werden — nach einem await
  // blockiert es der Browser (Safari immer, Chrome nach ein paar Sekunden).
  // Deshalb erst den leeren Tab oeffnen, dann die Datei laden und die
  // Adresse nachtragen.
  // KEIN "noopener": damit liefert window.open laut Standard null zurueck,
  // und genau den Griff brauchen wir hier. Stattdessen die Rueckwaerts-
  // Referenz gleich selbst kappen.
  const tab = window.open("", "_blank");
  if (tab) {
    try { tab.opener = null; } catch { /* egal */ }
  }
  try {
    const res = await client.get(path, { responseType: "blob" });
    const url = URL.createObjectURL(new Blob([res.data], { type: mime }));
    if (tab && !tab.closed) {
      tab.location.href = url;
    } else {
      // Popup blockiert: NICHT die aktuelle Seite ersetzen (der Nutzer
      // wuerde die App verlieren) — stattdessen als Download anbieten
      // und, falls auch das der Browser verweigert, klar melden.
      const a = document.createElement("a");
      a.href = url;
      a.download = (path.split("/").pop() || "datei") +
                   (mime === "application/pdf" ? ".pdf" : "");
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
    // Spaet freigeben — der eingebaute PDF-Betrachter laedt die Adresse
    // beim Drucken/Neuladen erneut.
    setTimeout(() => URL.revokeObjectURL(url), 10 * 60 * 1000);
  } catch (e) {
    if (tab && !tab.closed) tab.close();
    throw e;
  }
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
    const parts = d.map((it) => (typeof it === "string" ? it : it?.msg || JSON.stringify(it)));
    return parts.filter(Boolean).join(" · ") || fallback;
  }
  if (d && typeof d === "object") return d.msg || JSON.stringify(d);
  return err?.message || fallback;
};
