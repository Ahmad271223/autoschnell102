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
