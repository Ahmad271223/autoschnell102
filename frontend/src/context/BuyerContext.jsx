import { createContext, useContext, useEffect, useState } from "react";
import axios from "axios";
import { API_BASE } from "@/lib/api";

/**
 * Zwischenhändler-Auth (Rolle b2b_buyer, eigene Accounts — separat vom
 * Händler- und Fahrer-Login). LocalStorage-Key: ah_buyer_token.
 */
const BuyerCtx = createContext(null);

export const buyerApi = axios.create({ baseURL: API_BASE });
buyerApi.interceptors.request.use((c) => {
  const t = localStorage.getItem("ah_buyer_token");
  if (t) c.headers.Authorization = `Bearer ${t}`;
  return c;
});

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
      .catch(() => localStorage.removeItem("ah_buyer_token"))
      .finally(() => setReady(true));
  }, []);

  const login = async (email, password) => {
    const { data } = await buyerApi.post("/buyer/login", { email, password });
    localStorage.setItem("ah_buyer_token", data.token);
    return refresh();
  };

  const register = async (payload) => {
    const { data } = await buyerApi.post("/buyer/register", payload);
    localStorage.setItem("ah_buyer_token", data.token);
    return refresh();
  };

  const logout = () => {
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
