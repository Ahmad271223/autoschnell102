import { createContext, useContext, useEffect, useState } from "react";
import { TOKEN_FAHRER, tokenLesen, tokenLoeschen, tokenSetzen } from "@/lib/sitzung";
import axios from "axios";
import { API_BASE, openAuthedFile } from "@/lib/api";
import { verbindungsGrund } from "@/components/VerbindungsFehler";

/**
 * Fahrer-Auth (eigenständige Accounts, separat vom Händler-Auth).
 * LocalStorage-Key: ah_driver_token.
 */
const DriverCtx = createContext(null);

export const driverApi = axios.create({ baseURL: API_BASE, timeout: 60000 });
driverApi.interceptors.request.use((c) => {
  const t = tokenLesen(TOKEN_FAHRER);
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
  // Runde 22 (11.09.2026): /driver/me hat nicht geantwortet (Funkloch,
  // Timeout, 502/520 im Rollout) oder mit 5xx/429 — Token bleibt,
  // DriverLayout zeigt "Keine Verbindung" statt der Login-Seite.
  // null = alles gut, sonst { status, detail } (status null = keine Antwort).
  const [fehler, setFehler] = useState(null);

  useEffect(() => {
    const t = tokenLesen(TOKEN_FAHRER);
    if (!t) { setReady(true); return; }
    driverApi.get("/driver/me")
      .then((r) => { setDriver(r.data); setFehler(null); })
      .catch((e) => {
        // Runde 22 (11.09.2026): Vorher wurde bei JEDEM Fehler abgemeldet —
        // ein Funkloch unterwegs hiess neu anmelden (und wegen Single-Session
        // die andere Sitzung verlieren). Wie BuyerContext: nur abmelden, wenn
        // der Server die Sitzung ablehnt (401 beendet/abgelaufen, 403 kein
        // Fahrer-Token). driverApi hat keinen 401-Interceptor, darum hier.
        const status = e?.response?.status;
        if (status === 401 || status === 403) tokenLoeschen(TOKEN_FAHRER);
        else setFehler(verbindungsGrund(e));
      })
      .finally(() => setReady(true));
  }, []);

  const login = async (email, password) => {
    const { data } = await driverApi.post("/driver/login", { email, password });
    tokenSetzen(TOKEN_FAHRER, data.token);
    // volle /me-Payload holen (inkl. dealers)
    const me = await driverApi.get("/driver/me");
    setDriver(me.data);
    setFehler(null);
    return me.data;
  };

  const register = async (email, password, display_name) => {
    const { data } = await driverApi.post("/driver/register", {
      email, password, display_name,
    });
    tokenSetzen(TOKEN_FAHRER, data.token);
    const me = await driverApi.get("/driver/me");
    setDriver(me.data);
    setFehler(null);
    return me.data;
  };

  const refresh = async () => {
    const me = await driverApi.get("/driver/me");
    setDriver(me.data);
    setFehler(null);
    return me.data;
  };

  const logout = () => {
    // Serverseitig widerrufen (Runde 5): vorher blieb ein kopierter Token
    // nach dem Abmelden bis zum Ablauf gueltig.
    driverApi.post("/driver/logout").catch(() => {});
    tokenLoeschen(TOKEN_FAHRER);
    setDriver(null);
    setFehler(null);
  };

  return (
    <DriverCtx.Provider value={{ driver, ready, fehler, login, register, logout, refresh }}>
      {children}
    </DriverCtx.Provider>
  );
}

export const useDriver = () => useContext(DriverCtx);
