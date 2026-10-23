import { fassungMithoeren } from "@/lib/fassung";
import { createContext, useContext, useEffect, useState } from "react";
import { TOKEN_KAEUFER, tokenLesen, tokenLoeschen, tokenSetzen } from "@/lib/sitzung";
import axios from "axios";
import {
  API_BASE, gehoertZumAktuellenToken, istLangeAktion, LANGE_AKTION_MS, neuesTokenUebernehmen,
} from "@/lib/api";
import { schreiben, sitzungsSpeicher } from "@/lib/speicher";
import { ABMELDEGRUND_KAEUFER } from "@/pages/markt/marktHilfen";
import { geraetIdLesen, geraetIdMerken } from "@/context/AuthContext";

/**
 * Zwischenhändler-Auth (Rolle b2b_buyer, eigene Accounts — separat vom
 * Händler- und Fahrer-Login). LocalStorage-Key: ah_buyer_token.
 */
const BuyerCtx = createContext(null);

export const buyerApi = axios.create({ baseURL: API_BASE, timeout: 60000 });
// Runde 31: eigene Verbindung — sonst bliebe dieser Bereich blind fuer neue Fassungen.
fassungMithoeren(buyerApi);
buyerApi.interceptors.request.use((c) => {
  const t = tokenLesen(TOKEN_KAEUFER);
  if (t) c.headers.Authorization = `Bearer ${t}`;
  // Wie in der Haendler- und Fahrer-App: Datei-Abrufe duerfen laenger dauern.
  if (istLangeAktion(c)) c.timeout = LANGE_AKTION_MS;
  return c;
});
// Session beendet (anderes Gerät / abgemeldet) -> sauber zum Login statt
// endloser Fehl-Requests mit totem Token.
buyerApi.interceptors.response.use(
  (r) => {
    // Rollenprüfung 22.09.2026 (RP-546): gleitende Sitzung. Läuft das Token
    // bald ab, schickt der Server ein frisches derselben Sitzung mit
    // (X-Neues-Token) — vorher endete jede Käufer-Sitzung hart nach 7 Tagen,
    // mitten in einer Verhandlung. Dieselbe Regel wie Händler- und Fahrer-App
    // (lib/api.neuesTokenUebernehmen -> sitzung.tokenErneuern): nur, wenn die
    // Anfrage mit dem AKTUELLEN Token lief, nur etwas Token-Förmiges, nie in
    // einem abgemeldeten Tab, und die "letzte Anmeldung" eines anderen Tabs
    // bleibt stehen. Vorher lief hier tokenSetzen — das hob die Abmeldung
    // des Tabs auf und schrieb localStorage auch über ein fremdes Konto.
    neuesTokenUebernehmen(r, TOKEN_KAEUFER);
    return r;
  },
  (err) => {
    // Nachpruefung 20.09.2026 (Nr. 41): dasselbe Grundproblem wie in
    // lib/api.js — eine verspaetete 401 aus einer FRUEHEREN Anmeldung
    // loeschte den gerade frisch gespeicherten Token. Deshalb zaehlt die
    // 401 nur, wenn sie zu genau diesem Token gehoert.
    if (err?.response?.status === 401
        && tokenLesen(TOKEN_KAEUFER)
        && gehoertZumAktuellenToken(err?.config, tokenLesen(TOKEN_KAEUFER))
        && !String(err?.config?.url || "").includes("/buyer/login")) {
      tokenLoeschen(TOKEN_KAEUFER);
      // Rollenprüfung 22.09.2026 (RP-531): den Grund merken (z. B. "neu
      // angemeldet am … von …") — die Käufer-Anmeldung zeigt ihn an, wie die
      // Händler- und die Fahrer-App. Vorher stand dort kommentarlos das Formular.
      const detail = err?.response?.data?.detail;
      schreiben(sitzungsSpeicher(), ABMELDEGRUND_KAEUFER,
                typeof detail === "string" && detail ? detail : "");
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
    const t = tokenLesen(TOKEN_KAEUFER);
    if (!t) { setReady(true); return; }
    buyerApi.get("/buyer/me")
      .then((r) => setBuyer(r.data))
      .catch((e) => {
        // Nur bei 401 (Session tot) ausloggen — bei Netz-/Serverfehlern
        // Token behalten, sonst wirft ein kurzer Backend-Aussetzer alle raus.
        if (e?.response?.status === 401) tokenLoeschen(TOKEN_KAEUFER);
      })
      .finally(() => setReady(true));
  }, []);

  // Kontonummer (13.09.2026): Anmeldung mit der Kontonummer. Kaeuferkonten
  // legt der Betreiber nach einer Anfrage an; Einladungen loest BuyerLogin
  // nach der Anmeldung ein.
  const login = async (kennung, password) => {
    // Rollenprüfung 22.09.2026 (RP-557): bekanntes Gerät mitsenden (derselbe
    // Schlüssel wie bei der Firmen-Anmeldung, AuthContext) — ein Angreifer
    // kann das Konto dann nicht mehr für dieses Gerät aussperren.
    const { data } = await buyerApi.post("/buyer/login",
      { kontonummer: kennung, password, geraet_id: geraetIdLesen() });
    geraetIdMerken(data);
    tokenSetzen(TOKEN_KAEUFER, data.token);
    // Login war erfolgreich — ein Fehler beim Nachladen des Profils darf
    // NICHT als "Anmeldung fehlgeschlagen" erscheinen.
    try { return await refresh(); }
    catch { setBuyer(data.user || null); return data.user; }
  };

  const logout = () => {
    // Server-Session mit beenden (Single-Session: Token wird ungültig).
    // Rollenprüfung 22.09.2026 (RP-500): Der Request-Interceptor läuft in
    // axios 1.x asynchron — er las den Token erst NACH tokenLoeschen, der
    // Logout ging ohne Authorization raus (401), und die Sitzung blieb auf
    // dem Server gültig. Jetzt den Token vorher lesen und selbst mitschicken.
    const t = tokenLesen(TOKEN_KAEUFER);
    if (t) {
      buyerApi.post("/auth/logout", null, { headers: { Authorization: `Bearer ${t}` } })
        .catch(() => {});
    }
    tokenLoeschen(TOKEN_KAEUFER);
    setBuyer(null);
  };

  return (
    <BuyerCtx.Provider value={{ buyer, ready, login, logout, refresh }}>
      {children}
    </BuyerCtx.Provider>
  );
}

export const useBuyer = () => useContext(BuyerCtx);
