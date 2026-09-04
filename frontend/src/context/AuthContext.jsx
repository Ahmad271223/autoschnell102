import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";

const AuthCtx = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [dealer, setDealer] = useState(null);
  const [subscription, setSubscription] = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const token = localStorage.getItem("ah_token");
    if (!token) {
      setUser(null);
      setDealer(null);
      setSubscription(null);
      setLoading(false);
      return null;
    }
    try {
      const { data } = await api.get("/auth/me");
      setUser(data.user);
      setDealer(data.dealer);
      setSubscription(data.subscription);
      setLoading(false);
      return data;
    } catch {
      localStorage.removeItem("ah_token");
      setUser(null);
      setDealer(null);
      setSubscription(null);
      setLoading(false);
      return null;
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = async (email, password) => {
    const { data } = await api.post("/auth/login", { email, password });
    if (data?.mfa_erforderlich) {
      // Zwei-Faktor (Admin/Super-Admin): noch kein Sitzungs-Token — die
      // Login-Seite fragt jetzt den Code aus der Authenticator-App ab.
      return { mfa_erforderlich: true, mfa_token: data.mfa_token };
    }
    localStorage.setItem("ah_token", data.token);
    await refresh();
    return data.user;
  };
  const loginMfa = async (mfaToken, code) => {
    const { data } = await api.post("/auth/login/mfa", { mfa_token: mfaToken, code });
    localStorage.setItem("ah_token", data.token);
    await refresh();
    return data.user;
  };

  const register = async (payload) => {
    const { data } = await api.post("/auth/register", payload);
    localStorage.setItem("ah_token", data.token);
    await refresh();
    return data.user;
  };

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } catch (_) {}
    localStorage.removeItem("ah_token");
    sessionStorage.removeItem("ah_vergleich_state");
    setUser(null);
    setDealer(null);
    setSubscription(null);
  };

  return (
    <AuthCtx.Provider value={{ user, dealer, subscription, loading, login, loginMfa, register, logout, refresh, setDealer }}>
      {children}
    </AuthCtx.Provider>
  );
};

export const useAuth = () => useContext(AuthCtx);
