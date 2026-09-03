import { createContext, useContext, useEffect, useState } from "react";
import axios from "axios";
import { API_BASE } from "@/lib/api";

/**
 * Zwischenhändler-Auth (Rolle b2b_buyer, eigene Accounts — separat vom
 * Händler- und Fahrer-Login). LocalStorage-Key: ah_buyer_token.
 */
const BuyerCtx = createContext(null);

export const buyerApi = axios.create({ baseURL: API_BASE, timeout: 60000 });
buyerApi.interceptors.request.use((c) => {
  const t = localStorage.getItem("ah_buyer_token");
  if (t) c.headers.Authorization = `Bearer ${t}`;
  return c;
});
// Session beendet (anderes Gerät / abgemeldet) -> sauber zum Login statt
// endloser Fehl-Requests mit totem Token.
buyerApi.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401
        && localStorage.getItem("ah_buyer_token")
        && !String(err?.config?.url || "").includes("/buyer/login")) {
      localStorage.removeItem("ah_buyer_token");
      if (window.location.pathname.startsWith("/markt")
          && !window.location.pathname.startsWith("/markt/login")) {
        window.location.href = "/markt/login?reason=session";
      }
    }
    return Promise.reject(err);
  },
);

export function BuyerAuthProvider({ children }) {
  const [buyer, setBuyer] = useState(null);
  const [ready, setReady] = useState(false);

  const refresh = async () => {
    const { data } = await buyerApi.get("/buyer/me");
    setBuyer(data);
    return data;
  };

  useEffect(() => {
    const t = localStorage.getItem("ah_buyer_token");
    if (!t) { setReady(true); return; }
    buyerApi.get("/buyer/me")
      .then((r) => setBuyer(r.data))
      .catch((e) => {
        // Nur bei 401 (Session tot) ausloggen — bei Netz-/Serverfehlern
        // Token behalten, sonst wirft ein kurzer Backend-Aussetzer alle raus.
        if (e?.response?.status === 401) localStorage.removeItem("ah_buyer_token");
      })
      .finally(() => setReady(true));
  }, []);

  const login = async (email, password) => {
    const { data } = await buyerApi.post("/buyer/login", { email, password });
    localStorage.setItem("ah_buyer_token", data.token);
    // Login war erfolgreich — ein Fehler beim Nachladen des Profils darf
    // NICHT als "Anmeldung fehlgeschlagen" erscheinen.
    try { return await refresh(); }
    catch { setBuyer(data.user || null); return data.user; }
  };

  const register = async (payload) => {
    const { data } = await buyerApi.post("/buyer/register", payload);
    localStorage.setItem("ah_buyer_token", data.token);
    let u = data.user || null;
    try { u = await refresh(); }
    catch { setBuyer(data.user || null); }
    // network_joined kommt ehrlich vom Server: false bei ungueltiger,
    // abgelaufener oder aufgebrauchter Einladung.
    return { ...(u || {}), network_joined: !!data.network_joined };
  };

  const logout = () => {
    // Server-Session mit beenden (Single-Session: Token wird ungültig).
    buyerApi.post("/auth/logout").catch(() => {});
    localStorage.removeItem("ah_buyer_token");
    setBuyer(null);
  };

  return (
    <BuyerCtx.Provider value={{ buyer, ready, login, register, logout, refresh }}>
      {children}
    </BuyerCtx.Provider>
  );
}

export const useBuyer = () => useContext(BuyerCtx);
