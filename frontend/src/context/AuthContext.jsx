import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { TOKEN_APP, tokenLesen, tokenLoeschen, tokenSetzen } from "@/lib/sitzung";

const AuthCtx = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [dealer, setDealer] = useState(null);
  const [subscription, setSubscription] = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const token = tokenLesen(TOKEN_APP);
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
      tokenLoeschen(TOKEN_APP);
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
    tokenSetzen(TOKEN_APP, data.token);
    await refresh();
    return data.user;
  };
  const loginMfa = async (mfaToken, code) => {
    const { data } = await api.post("/auth/login/mfa", { mfa_token: mfaToken, code });
    tokenSetzen(TOKEN_APP, data.token);
    await refresh();
    return data.user;
  };

  const register = async (payload) => {
    const { data } = await api.post("/auth/register", payload);
    tokenSetzen(TOKEN_APP, data.token);
    await refresh();
    return data.user;
  };

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } catch (_) {}
    tokenLoeschen(TOKEN_APP);
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
