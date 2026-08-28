import { createContext, useContext, useEffect, useState } from "react";
import axios from "axios";
import { API_BASE, openAuthedFile } from "@/lib/api";

/**
 * Fahrer-Auth (eigenständige Accounts, separat vom Händler-Auth).
 * LocalStorage-Key: ah_driver_token.
 */
const DriverCtx = createContext(null);

export const driverApi = axios.create({ baseURL: API_BASE, timeout: 60000 });
driverApi.interceptors.request.use((c) => {
  const t = localStorage.getItem("ah_driver_token");
  if (t) c.headers.Authorization = `Bearer ${t}`;
  return c;
});

// PDF in neuem Tab oeffnen — Abruf per Authorization-Header statt
// ?auth=<token> in der URL (der Token landete sonst in Browser-Verlauf
// und Server-Logs). Nutzt denselben Oeffner wie die Haendler-App, damit
// Popup-Verhalten und Freigabe-Zeiten nur an EINER Stelle gepflegt werden.
export const openDriverPdf = (path) =>
  openAuthedFile(path, "application/pdf", driverApi);

export function DriverAuthProvider({ children }) {
  const [driver, setDriver] = useState(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const t = localStorage.getItem("ah_driver_token");
    if (!t) { setReady(true); return; }
    driverApi.get("/driver/me")
      .then((r) => setDriver(r.data))
      .catch(() => localStorage.removeItem("ah_driver_token"))
      .finally(() => setReady(true));
  }, []);

  const login = async (email, password) => {
    const { data } = await driverApi.post("/driver/login", { email, password });
    localStorage.setItem("ah_driver_token", data.token);
    // volle /me-Payload holen (inkl. dealers)
    const me = await driverApi.get("/driver/me");
    setDriver(me.data);
    return me.data;
  };

  const register = async (email, password, display_name) => {
    const { data } = await driverApi.post("/driver/register", {
      email, password, display_name,
    });
    localStorage.setItem("ah_driver_token", data.token);
    const me = await driverApi.get("/driver/me");
    setDriver(me.data);
    return me.data;
  };

  const refresh = async () => {
    const me = await driverApi.get("/driver/me");
    setDriver(me.data);
    return me.data;
  };

  const logout = () => {
    localStorage.removeItem("ah_driver_token");
    setDriver(null);
  };

  return (
    <DriverCtx.Provider value={{ driver, ready, login, register, logout, refresh }}>
      {children}
    </DriverCtx.Provider>
  );
}

export const useDriver = () => useContext(DriverCtx);
