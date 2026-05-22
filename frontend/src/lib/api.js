import axios from "axios";

const BACKEND = process.env.REACT_APP_BACKEND_URL;
export const API_BASE = `${BACKEND}/api`;

export const api = axios.create({ baseURL: API_BASE });

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
