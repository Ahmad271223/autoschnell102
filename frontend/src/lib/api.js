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
export async function openAuthedFile(path, mime = "application/pdf") {
  const res = await api.get(path, { responseType: "blob" });
  const url = URL.createObjectURL(new Blob([res.data], { type: mime }));
  window.open(url, "_blank", "noopener");
  setTimeout(() => URL.revokeObjectURL(url), 60000);
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
